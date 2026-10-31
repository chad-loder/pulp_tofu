"""The OpenTofu provider network mirror protocol, as served by Pulp.

Response documents follow
https://opentofu.org/docs/internals/provider-network-mirror-protocol/.
"""

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import NotRequired, TypedDict, override

JSON_CONTENT_TYPE = "application/json"

# Provider protocol major versions this OpenTofu generation installs.
SUPPORTED_PROTOCOL_MAJORS = frozenset({"5", "6"})


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

    @override
    def __str__(self) -> str:
        return f"{self.os}_{self.arch}"


def protocols_supported(protocols: tuple[str, ...]) -> bool:
    """Return whether any listed provider protocol version is one OpenTofu installs.

    An empty list means the registry did not say, which OpenTofu accepts.
    """
    if not protocols:
        return True
    return any(p.partition(".")[0] in SUPPORTED_PROTOCOL_MAJORS for p in protocols)


def address_allowed(address: ProviderAddress, includes: list[str], excludes: list[str]) -> bool:
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
    """Render ``index.json``."""
    return _dump({"versions": {version: {} for version in versions}})


def version_document(archives: dict[str, ArchiveEntry]) -> bytes:
    """Render ``:version.json`` from entries keyed ``os_arch``."""
    return _dump({"archives": archives})


def archive_url(version: str, filename: str) -> str:
    """The archive URL relative to ``:version.json``, which sits beside the version directory."""
    return f"{version}/{filename}"


def _dump(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
