"""An HTTP downloader whose retries continue the byte stream instead of restarting it.

pulpcore's ``HttpDownloader`` retries a failed transfer from byte 0. When the content
app is already streaming that transfer to a client, the restart appends the archive's
first bytes to what the client has received, and the client sees a complete-length
body with the wrong contents. ``ResumingHttpDownloader`` sends a validated Range
request from the last byte handed to ``handle_data``, so the client stream, the stored
file and the running digests all continue from the same offset.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, override

from aiohttp import ClientResponse, ClientTimeout
from pyhaul import ContentRangeError, is_weak_validator, parse_etag
from pyhaul.content_range import parse_content_range
from pyhaul.transport import TransportHeaders
from pyhaul.transport.aiohttp_adapter import headers_from_aiohttp_response

from pulpcore.plugin.download import DownloadResult, HttpDownloader
from pulpcore.plugin.exceptions import TimeoutException

from .retry import is_retryable, retry_delay

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024

# Seconds without a byte before a transfer counts as stalled, when the remote sets no
# sock_read_timeout. A stall is retried, and the retry resumes.
STALL_TIMEOUT = 60.0


class ResumeRefusedError(Exception):
    """A retry cannot continue the stream: the upstream changed or refused the range."""


class ResumingHttpDownloader(HttpDownloader):
    """``HttpDownloader`` that resumes retries with ``Range`` and ``If-Range``.

    ``total_timeout`` is the remote's own setting. When it is unset the transfer has no
    overall deadline, only the connect timeout and a read-stall timeout, so a slow large
    archive keeps going while a stalled one fails and resumes.

    Retries are bounded by attempts that make no progress: an attempt that delivered
    bytes resets the count, so a long transfer over a flaky link keeps resuming while a
    dead upstream gives up after ``max_retries`` fruitless tries.
    """

    def __init__(self, *args: Any, total_timeout: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._total_timeout = total_timeout
        self._delivered = 0
        self._validator: str | None = None
        self._length: int | None = None

    @override
    async def run(self, extra_data: Mapping[str, Any] | None = None) -> DownloadResult:
        never_retry = tuple((extra_data or {}).get("disable_retry_list", ()))
        fruitless = 0
        async with self.semaphore:
            while True:
                before = self._delivered
                try:
                    return await self._run(extra_data=extra_data)
                except TimeoutError as exc:
                    error: BaseException = TimeoutException(self.url)
                    error.__cause__ = exc
                except Exception as exc:  # noqa: BLE001 -- classified just below
                    error = exc
                if isinstance(error, never_retry) or not is_retryable(error):
                    raise error
                fruitless = 0 if self._delivered > before else fruitless + 1
                delay = retry_delay(fruitless, error)
                if fruitless > self.max_retries or delay is None:
                    raise error
                log.info(
                    "Retrying %s at byte %d in %.1fs: %s", self.url, self._delivered, delay, error
                )
                await asyncio.sleep(delay)

    @override
    async def _run(self, extra_data: Mapping[str, Any] | None = None) -> DownloadResult:
        if self.download_throttler:
            await self.download_throttler.acquire()
        request_kwargs: dict[str, Any] = {
            "proxy": self.proxy,
            "proxy_auth": self.proxy_auth,
            "auth": self.auth,
        }
        if extra_data and extra_data.get("request_kwargs"):
            request_kwargs.update(extra_data["request_kwargs"])
        # Identity encoding keeps Range offsets and the signed SHA-256 on the same bytes.
        headers = {**(request_kwargs.pop("headers", None) or {}), "Accept-Encoding": "identity"}
        session_timeout = self.session.timeout
        request_kwargs["timeout"] = ClientTimeout(
            total=self._total_timeout,
            connect=session_timeout.connect,
            sock_connect=session_timeout.sock_connect,
            sock_read=session_timeout.sock_read or STALL_TIMEOUT,
        )
        resuming = self._delivered > 0
        if resuming:
            if self._validator is None:
                raise ResumeRefusedError(f"{self.url} offered no strong validator to resume with")
            headers = {
                **headers,
                "Range": f"bytes={self._delivered}-",
                "If-Range": self._validator,
            }
        async with self.session.get(self.url, headers=headers, **request_kwargs) as response:
            self.raise_for_status(response)
            if resuming:
                self._check_resumed(response)
            else:
                self._remember(headers_from_aiohttp_response(response))
                if self.headers_ready_callback:
                    await self.headers_ready_callback(response.headers)
            while chunk := await response.content.read(_CHUNK):
                await self.handle_data(chunk)
                self._delivered += len(chunk)
            await self.finalize()
            result = DownloadResult(
                path=self.path,
                artifact_attributes=self.artifact_attributes,
                url=self.url,
                headers=response.headers,
            )
            await response.release()
        if self._close_session_on_finalize:
            await self.session.close()
        return result

    def _remember(self, headers: TransportHeaders) -> None:
        """Keep the validator and length that a resumed request must match."""
        if headers.get("Content-Encoding"):
            return
        length = headers.get("Content-Length")
        self._length = int(length) if length and length.isdigit() else None
        etag = headers.get("ETag")
        if etag:
            parsed = parse_etag(etag)
            if parsed and not is_weak_validator(parsed):
                self._validator = etag
                return
        self._validator = headers.get("Last-Modified")

    def _check_resumed(self, response: ClientResponse) -> None:
        """Accept only a 206 that continues exactly where the stream stopped."""
        if response.status != 206:
            raise ResumeRefusedError(f"{self.url} answered {response.status} to a resumed range")
        try:
            content_range = parse_content_range(response.headers.get("Content-Range", ""))
        except ContentRangeError as exc:
            raise ResumeRefusedError(f"{self.url} sent an unusable Content-Range") from exc
        if content_range.start != self._delivered:
            raise ResumeRefusedError(f"{self.url} resumed at {content_range.start}")
        if self._length is not None and content_range.instance_length != self._length:
            raise ResumeRefusedError(f"{self.url} changed length during the download")
