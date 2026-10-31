"""The files of a provider release as GoReleaser lays them out, and what they declare.

A release of provider ``widget`` version ``1.2.0`` is::

    terraform-provider-widget_1.2.0_linux_amd64.zip      one per platform
    terraform-provider-widget_1.2.0_SHA256SUMS
    terraform-provider-widget_1.2.0_SHA256SUMS.sig       binary detached signature
    terraform-provider-widget_1.2.0_manifest.json        optional

https://opentofu.org/docs/internals/provider-registry-protocol/ and GoReleaser's
provider template both use these names.
"""

import json
import re
from dataclasses import dataclass

from .protocol import Platform, is_provider_type, protocols_supported
from .versions import is_release_version

# Provider types and versions become path segments; parse_release holds both to
# OpenTofu's rules after matching.
_RELEASE_FILE = re.compile(
    r"^terraform-provider-(?P<type>[a-z0-9][a-z0-9-]*)"
    r"_(?P<version>[^_/]+)"
    r"_(?P<part>[a-z0-9]+_[a-z0-9]+\.zip|SHA256SUMS|SHA256SUMS\.sig|manifest\.json)$"
)
DEFAULT_PROTOCOLS = ("5.0",)
# The checksums, signature and manifest are read into memory; archives are not.
MAX_SMALL_FILE_BYTES = 64 * 1024


class ReleaseError(ValueError):
    """The uploaded files are not one complete provider release."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseFiles:
    """The filenames of one release, sorted by role."""

    provider_type: str
    version: str
    shasums: str
    signature: str
    manifest: str | None
    archives: dict[Platform, str]


def parse_release(filenames: list[str]) -> ReleaseFiles:
    """Sort ``filenames`` into one release's roles.

    Raises:
        ReleaseError: A name is not a release file, the names disagree on type or
            version, a role is missing or repeated, or there are no archives.
    """
    if len(set(filenames)) != len(filenames):
        raise ReleaseError("a file is uploaded twice")
    releases: set[tuple[str, str]] = set()
    singles: dict[str, str] = {}
    archives: dict[Platform, str] = {}
    for name in filenames:
        match = _RELEASE_FILE.match(name)
        if match is None:
            raise ReleaseError(f"{name} is not a provider release file")
        releases.add((match["type"], match["version"]))
        part = match["part"]
        if part.endswith(".zip"):
            platform = Platform.from_key(part.removesuffix(".zip"))
            if platform is None:
                raise ReleaseError(f"{name} does not name an os_arch platform")
            archives[platform] = name
        else:
            singles[part] = name
    if len(releases) != 1:
        raise ReleaseError("the files name more than one provider type or version")
    [(provider_type, version)] = releases
    if not is_provider_type(provider_type):
        raise ReleaseError(f"OpenTofu does not accept the provider type {provider_type!r}")
    if not is_release_version(version):
        raise ReleaseError(f"{version!r} is not a SemVer 2.0 version without build metadata")
    if "SHA256SUMS" not in singles or "SHA256SUMS.sig" not in singles:
        raise ReleaseError("a release needs its SHA256SUMS and SHA256SUMS.sig")
    if not archives:
        raise ReleaseError("a release needs at least one platform archive")
    return ReleaseFiles(
        provider_type=provider_type,
        version=version,
        shasums=singles["SHA256SUMS"],
        signature=singles["SHA256SUMS.sig"],
        manifest=singles.get("manifest.json"),
        archives=archives,
    )


def manifest_protocols(document: bytes | None) -> tuple[str, ...]:
    """The plugin protocol versions a ``manifest.json`` declares; 5.0 without one.

    Raises:
        ReleaseError: The manifest is malformed or declares no protocol OpenTofu installs.
    """
    if document is None:
        return DEFAULT_PROTOCOLS
    try:
        parsed: object = json.loads(document)
    except ValueError as exc:
        raise ReleaseError("manifest.json is not JSON") from exc
    metadata = parsed.get("metadata") if isinstance(parsed, dict) else None
    declared = metadata.get("protocol_versions") if isinstance(metadata, dict) else None
    if declared is None:
        return DEFAULT_PROTOCOLS
    if not isinstance(declared, list) or not all(isinstance(p, str) for p in declared):
        raise ReleaseError("manifest.json protocol_versions is not a list of strings")
    protocols = tuple(declared)
    if not protocols or not protocols_supported(protocols):
        raise ReleaseError(f"manifest.json declares no supported protocol: {protocols}")
    return protocols
