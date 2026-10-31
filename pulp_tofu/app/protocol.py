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


# A provider namespace or type as OpenTofu's registry-address (ParseProviderPart)
# accepts one, kept to ASCII: letters, digits and single dashes, no dash at either end,
# at most one DNS label long.
_PROVIDER_PART = re.compile(r"^(?=.{1,63}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
# OpenTofu refuses provider types with these prefixes.
_REDUNDANT_TYPE_PREFIXES = ("terraform-", "opentofu-")


def is_provider_part(value: str) -> bool:
    """Whether ``value`` is a provider namespace OpenTofu accepts, already lowercase."""
    return _PROVIDER_PART.match(value) is not None


def is_provider_type(value: str) -> bool:
    """Whether ``value`` is a provider type OpenTofu accepts, already lowercase."""
    return is_provider_part(value) and not value.startswith(_REDUNDANT_TYPE_PREFIXES)


# Module names and target systems as OpenTofu's registry-address accepts them, matched
# case-sensitively: names may also use underscores, systems only letters and digits.
_MODULE_NAME = re.compile(r"^[0-9A-Za-z](?:[0-9A-Za-z_-]{0,62}[0-9A-Za-z])?$")
_MODULE_SYSTEM = re.compile(r"^[0-9a-z]{1,64}$")


def is_module_name(value: str) -> bool:
    """Whether ``value`` is a module name OpenTofu accepts."""
    return _MODULE_NAME.match(value) is not None


def is_module_system(value: str) -> bool:
    """Whether ``value`` is a module target system OpenTofu accepts."""
    return _MODULE_SYSTEM.match(value) is not None


def normalize_hostname(value: str) -> str:
    """A registry hostname as OpenTofu's svchost compares hostnames.

    Raises:
        ValueError: ``value`` is not a host with an optional port.
    """
    return registry_host(URL.build(scheme="https", authority=value.strip()))


def registry_host(url: URL) -> str:
    """The hostname OpenTofu writes in addresses for a registry at ``url``.

    yarl lowercases the host, gives it in Unicode form and drops a default port; an
    IPv6 address gets back the brackets yarl's ``host`` leaves off.

    Raises:
        ValueError: ``url`` has no host.
    """
    host = url.host
    if not host:
        raise ValueError(f"{url} has no host")
    if ":" in host:
        host = f"[{host}]"
    return host if url.explicit_port is None else f"{host}:{url.explicit_port}"


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
