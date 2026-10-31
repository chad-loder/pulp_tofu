"""Module packages from an upstream registry's go-getter locations.

An upstream module registry answers ``download`` with a go-getter address, most often
``git::https://host/owner/repo?ref=<commit>``, sometimes an HTTPS archive URL. Pulp
fetches the source at that address and packs it as the ``.tar.gz`` it serves.

Git sources are checked out with GitPython, as pulp_ansible's git remote does, over
HTTPS only. Any git host works: GitHub, GitLab, Forgejo, Gitea. The remote's CA and
proxy settings apply to git through the environment, never through argv. Repositories
that need credentials are not supported.
"""

import gzip
import io
import os
import tarfile
import tempfile
import zipfile
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from git import GitCommandError, Repo
from yarl import URL

from .protocol import display_url
from .publishing import ReleaseError, check_module_archive

# The archive formats go-getter unpacks, by a URL's name or its ``archive`` parameter.
type ArchiveFormat = Literal["tar.gz", "zip"]
_ARCHIVE_SUFFIXES: tuple[tuple[str, ArchiveFormat], ...] = (
    (".tar.gz", "tar.gz"),
    (".tgz", "tar.gz"),
    (".zip", "zip"),
)
_ARCHIVE_FORMATS: dict[str, ArchiveFormat] = {"tar.gz": "tar.gz", "tgz": "tar.gz", "zip": "zip"}
# go-getter's shorthand hosts that need no forced getter.
_SHORTHAND_GIT_HOSTS = ("github.com/", "gitlab.com/")
MAX_ARCHIVE_BYTES = 64 << 20


class ModuleSourceError(ReleaseError):
    """A module location is unsupported, unreachable, or yields an unsafe package."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GitSource:
    """A git repository over HTTPS, at ``ref`` or its default branch."""

    url: str
    ref: str | None
    subdir: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ArchiveSource:
    """An archive to download over HTTPS."""

    url: str
    format: ArchiveFormat
    subdir: str


type ModuleSource = GitSource | ArchiveSource


def parse_location(location: str, base: URL) -> ModuleSource:
    """Parse a registry's module location the way go-getter would.

    Relative locations (``/``, ``./``, ``../``) resolve against ``base``, the download
    URL, as OpenTofu resolves them. A ``//`` after the host and path selects a
    subdirectory.

    Raises:
        ModuleSourceError: The location uses a getter or scheme other than git over
            HTTPS or an HTTPS archive.
    """
    raw = location.strip()
    if raw.startswith(("/", "./", "../")):
        raw = str(base.join(URL(raw)))
    # Locations may be presigned, so messages carry them without their query.
    shown = display_url(location)
    # A forced getter ("git::...") is go-getter syntax, not part of the URL.
    getter, separator, address = raw.partition("::")
    if not separator or "/" in getter:
        getter, address = "", raw
    if not getter and address.startswith(_SHORTHAND_GIT_HOSTS):
        getter, address = "git", f"https://{address}"
    if getter not in ("", "git"):
        raise ModuleSourceError(f"unsupported module getter {getter!r} in {shown!r}")
    # encoded=True keeps the path as written: yarl would otherwise fold go-getter's "//"
    # and any "..", which raw_parts then shows as empty and ".." segments.
    try:
        url = URL(address, encoded=True)
    except ValueError as exc:
        raise ModuleSourceError(f"module location {shown!r} is not a URL") from exc
    if url.scheme != "https" or not url.raw_host:
        raise ModuleSourceError(f"module location {shown!r} is not an HTTPS address")
    # Credentials in a URL would reach git's argv and every message naming the source.
    if url.raw_user or url.raw_password:
        raise ModuleSourceError(f"module location {shown!r} carries credentials")
    segments = url.raw_parts[1:]
    boundary = segments.index("") if "" in segments else len(segments)
    source_segments = segments[:boundary]
    subdir_segments = [s for s in segments[boundary + 1 :] if s]
    if {".", ".."} & {*source_segments, *subdir_segments}:
        raise ModuleSourceError(f"module location {shown!r} climbs its path")
    source_url = url.with_path("/", encoded=True).joinpath(*source_segments, encoded=True)
    subdir = "/".join(subdir_segments)
    query = url.query
    if getter == "git":
        if "sshkey" in query:
            raise ModuleSourceError("module locations with an SSH key are not supported")
        clean = source_url.with_query(None).with_fragment(None)
        return GitSource(url=str(clean), ref=query.get("ref"), subdir=subdir)
    named = query.get("archive")
    archive_format: ArchiveFormat | None = (
        _ARCHIVE_FORMATS.get(named)
        if named is not None
        else next(
            (fmt for suffix, fmt in _ARCHIVE_SUFFIXES if source_url.raw_name.endswith(suffix)),
            None,
        )
    )
    if archive_format is None:
        raise ModuleSourceError(f"module location {shown!r} is neither git nor an archive")
    clean = source_url.with_query(query).without_query_params("archive").with_fragment(None)
    return ArchiveSource(url=str(clean), format=archive_format, subdir=subdir)


def checkout(source: GitSource, workdir: Path, environment: Mapping[str, str]) -> Path:
    """Check out a git source at its ref into ``workdir``; return the module directory.

    As prek does: fetch only the ref at depth 1, and when the server refuses that, fetch
    every branch and tag and check the ref out, unless the refusal was for want of
    credentials, which a full fetch would only meet again more slowly. Submodules are
    updated, as go-getter does, when the checkout has any. Line endings are left as
    committed, as mise clones, so a worker's git configuration cannot change the
    package's digest.

    Raises:
        ModuleSourceError: Git failed, or the repository needs credentials.
    """
    env = {
        **environment,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
        "LC_ALL": "C",
    }
    try:
        repo = Repo.init(workdir)
        with repo.config_writer() as config:
            config.set_value("core", "autocrlf", "false")
            config.set_value("advice", "detachedHead", "false")
        with repo.git.custom_environment(**env):
            try:
                repo.git.fetch("--depth", "1", source.url, source.ref or "HEAD")
                repo.git.checkout("FETCH_HEAD")
            except GitCommandError as exc:
                if source.ref is None or _needs_credentials(exc):
                    raise
                repo.git.fetch(source.url, "+refs/heads/*:refs/remotes/origin/*", "--tags")
                repo.git.checkout(source.ref)
            if (workdir / ".gitmodules").exists():
                repo.git.submodule("update", "--init", "--recursive", "--depth", "1")
    except GitCommandError as exc:
        if _needs_credentials(exc):
            raise ModuleSourceError(f"{source.url} needs credentials Pulp does not hold") from exc
        raise ModuleSourceError(f"git could not fetch {source.url} at {source.ref}") from exc
    return _module_dir(workdir, source.subdir)


# The messages git prints when a fetch needs credentials, as prek detects them.
_CREDENTIAL_ERRORS = (
    "terminal prompts disabled",
    "could not read username",
    "could not read password",
    "authentication failed",
    "http basic: access denied",
    "missing or invalid credentials",
    "could not authenticate to server",
)


def _needs_credentials(error: GitCommandError) -> bool:
    stderr = str(error.stderr).lower()
    return any(message in stderr for message in _CREDENTIAL_ERRORS)


def unpack(source: ArchiveSource, data: bytes, workdir: Path) -> Path:
    """Unpack an archive source into ``workdir``; return the module directory.

    Raises:
        ModuleSourceError: The archive is not a tar.gz or zip, or holds unsafe paths.
    """
    kind = source.format
    try:
        if kind == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for name in archive.namelist():
                    if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                        raise ModuleSourceError(f"{name} leaves the module archive")
                archive.extractall(workdir)
        else:
            check_module_archive(io.BytesIO(data))
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                archive.extractall(workdir, filter="data")
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise ModuleSourceError(f"{display_url(source.url)} is not a {kind} archive") from exc
    return _module_dir(workdir, source.subdir)


def pack(module_dir: Path, target: Path) -> None:
    """Write ``module_dir`` to ``target`` as a deterministic ``.tar.gz``.

    Entries are sorted and carry no time, owner or mode beyond the executable bit, and
    the gzip header has no name or time, so the same source always packs to the same
    digest. ``.git`` directories are left out. A symbolic link to a file is stored as
    that file, and only if it stays inside ``module_dir``.

    Raises:
        ModuleSourceError: A link points outside the module or at a directory, or the
            package is unsafe.
    """
    root = module_dir.resolve()
    with (
        target.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for path in _walk(root):
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ModuleSourceError(f"{path.relative_to(root)} links outside the module")
            if path.is_symlink() and resolved.is_dir():
                raise ModuleSourceError(f"{path.relative_to(root)} links to a directory")
            info = tarfile.TarInfo(path.relative_to(root).as_posix())
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            if resolved.is_dir():
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            else:
                info.size = resolved.stat().st_size
                info.mode = 0o755 if os.access(resolved, os.X_OK) else 0o644
                with resolved.open("rb") as content:
                    tar.addfile(info, content)
    with target.open("rb") as written:
        check_module_archive(written)


def git_environment(ca_cert: str | None, proxy_url: str | None, directory: Path) -> dict[str, str]:
    """Environment variables that give git the remote's CA bundle and proxy.

    The CA bundle is written into ``directory``, which must outlast the checkouts.
    """
    env: dict[str, str] = {}
    if ca_cert:
        bundle = directory / "remote-ca.pem"
        bundle.write_text(ca_cert)
        env["GIT_SSL_CAINFO"] = str(bundle.resolve())
    if proxy_url:
        env["HTTPS_PROXY"] = proxy_url
    return env


@contextmanager
def workspace() -> Generator[Path]:
    """A scratch directory removed afterwards."""
    with tempfile.TemporaryDirectory(prefix="pulp-tofu-module-") as directory:
        yield Path(directory)


def _walk(root: Path) -> Iterator[Path]:
    for directory, subdirs, files in os.walk(root, followlinks=False):
        subdirs[:] = sorted(d for d in subdirs if d != ".git")
        base = Path(directory)
        for name in sorted([*files, *subdirs]):
            yield base / name


def _module_dir(workdir: Path, subdir: str) -> Path:
    module_dir = (workdir / subdir).resolve() if subdir else workdir.resolve()
    if not module_dir.is_relative_to(workdir.resolve()) or not module_dir.is_dir():
        raise ModuleSourceError(f"the source has no directory {subdir!r}")
    return module_dir
