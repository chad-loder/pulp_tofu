"""The OpenTofu provider network mirror protocol, as served by Pulp.

Response documents follow
https://opentofu.org/docs/internals/provider-network-mirror-protocol/.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import NewType, NotRequired, Self, TypedDict, override

from yarl import URL

JSON_CONTENT_TYPE = "application/json"

SUPPORTED_PROTOCOL_MAJORS = frozenset({"5", "6"})

# "os_arch", the key the network mirror protocol and registries use for a platform.
PlatformKey = NewType("PlatformKey", str)
# Go's GOOS and GOARCH values: lowercase letters and digits, never an underscore.
_PLATFORM_KEY = re.compile(r"^([a-z0-9]+)_([a-z0-9]+)$")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderAddress:
    """A provider source address: origin registry hostname, namespace and type."""

    hostname: str
    namespace: str
    type: str

    @override
    def __str__(self) -> str:
        return f"{self.hostname}/{self.namespace}/{self.type}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Platform:
    """An operating system and CPU architecture pair."""

    os: str
    arch: str

    @property
    def key(self) -> PlatformKey:
        """The platform's ``os_arch`` key."""
        return PlatformKey(f"{self.os}_{self.arch}")

    @classmethod
    def from_key(cls, key: str) -> Self | None:
        """The platform an ``os_arch`` key names, or None if it is not one."""
        match = _PLATFORM_KEY.match(key)
        return cls(os=match.group(1), arch=match.group(2)) if match else None

    @override
    def __str__(self) -> str:
        return self.key


def parse_platform_key(key: str) -> Platform | None:
    """The platform an ``os_arch`` key names, or None if it is not one."""
    return Platform.from_key(key)


def protocols_supported(protocols: Sequence[str]) -> bool:
    """Return whether any listed provider protocol version is one OpenTofu installs.

    An empty list means the registry did not say, which OpenTofu accepts.
    """
    if not protocols:
        return True
    return any(p.partition(".")[0] in SUPPORTED_PROTOCOL_MAJORS for p in protocols)


def latest_releases(versions: Sequence[str], count: int) -> list[str]:
    """The ``count`` highest ``MAJOR.MINOR.PATCH`` versions, newest first.

    Pre-releases and build metadata are left out, as OpenTofu leaves them out when it
    resolves an unconstrained provider.
    """
    releases = [v for v in versions if v.count(".") == 2 and v.replace(".", "").isdigit()]
    releases.sort(key=_release_order, reverse=True)
    return releases[:count]


def _release_order(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def address_allowed(
    address: ProviderAddress, includes: Sequence[str], excludes: Sequence[str]
) -> bool:
    """Apply case-sensitive ``hostname/namespace/type`` glob filters.

    An empty ``includes`` admits every provider; ``excludes`` wins over ``includes``.
    """
    name = str(address)
    if includes and not any(fnmatchcase(name, pattern) for pattern in includes):
        return False
    return not any(fnmatchcase(name, pattern) for pattern in excludes)


class ArchiveEntry(TypedDict):
    """One platform entry of a version document."""

    url: str
    hashes: NotRequired[list[str]]


def index_document(versions: list[str]) -> bytes:
    return _dump({"versions": {version: {} for version in versions}})


def version_document(archives: dict[str, ArchiveEntry]) -> bytes:
    """Render ``:version.json`` from entries keyed ``os_arch``."""
    return _dump({"archives": archives})


def archive_url(version: str, filename: str) -> str:
    """The archive URL relative to ``:version.json``, which sits beside the version directory."""
    return str(URL.build(path=version).joinpath(filename))


def _dump(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
