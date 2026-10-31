"""Fetch a provider version's checksums from upstream and verify them before use.

Shared by the content app's pull-through handler and the sync task.
"""

import asyncio
import logging

from pulpcore.plugin.util import get_domain

from .models import ArchiveRecord, ProviderVersion, TofuRemote
from .protocol import Platform, ProviderAddress
from .registry import RegistryClient, UpstreamError, UpstreamVersion
from .signing import SignatureError, parse_sha256sums, verify_sha256sums

log = logging.getLogger(__name__)

# Providers from this registry may be unsigned; OpenTofu accepts them by default too.
KEYLESS_REGISTRY = "registry.opentofu.org"
PREFERRED_PLATFORM = Platform(os="linux", arch="amd64")


async def fetch_provider_version(
    remote: TofuRemote, address: ProviderAddress, upstream: UpstreamVersion
) -> ProviderVersion:
    """Fetch and verify one version's SHA256SUMS; return an unsaved ``ProviderVersion``.

    Raises:
        UpstreamError: The registry failed or contradicted itself.
        SignatureError: The checksums are unsigned where a signature is required, or the
            signature does not verify.
    """
    platforms = list(upstream.platforms)
    if not platforms:
        raise UpstreamError(f"{address} {upstream.version} lists no platforms")
    first = PREFERRED_PLATFORM if PREFERRED_PLATFORM in platforms else platforms[0]
    client = RegistryClient(remote)
    info = await client.download(address, upstream.version, first)
    shasums, signature = await asyncio.gather(
        client.fetch_bytes(info.shasums_url), client.fetch_bytes(info.shasums_signature_url)
    )
    if info.signing_keys:
        fingerprint = verify_sha256sums(shasums, signature, info.signing_keys)
    elif address.hostname == KEYLESS_REGISTRY and not remote.require_signatures:
        fingerprint = ""
    else:
        raise SignatureError(f"{address} {upstream.version} offers no signing keys")
    sums = parse_sha256sums(shasums)
    if sums.get(info.filename) != info.shasum:
        raise UpstreamError("SHA256SUMS does not list the download document's shasum")
    by_digest: dict[str, list[str]] = {}
    for filename, digest in sums.items():
        by_digest.setdefault(digest, []).append(filename)

    archives: dict[str, ArchiveRecord] = {}
    unreported: list[Platform] = []
    for platform in platforms:
        key = str(platform)
        reported = info.packages.get(key)
        if platform == info.platform:
            size = reported.size if reported else None
            archives[key] = {"filename": info.filename, "sha256": info.shasum, "size": size}
        elif reported is not None and reported.zh is not None:
            names = by_digest.get(reported.zh, [])
            if len(names) == 1:
                archives[key] = {"filename": names[0], "sha256": reported.zh, "size": reported.size}
            else:
                log.warning("%s %s: %s is not in SHA256SUMS", address, upstream.version, key)
        else:
            unreported.append(platform)
    # Registries without a packages map need one download document per platform.
    others = await asyncio.gather(
        *(client.download(address, upstream.version, p) for p in unreported)
    )
    for platform, other in zip(unreported, others, strict=True):
        if sums.get(other.filename) == other.shasum:
            archives[str(platform)] = {
                "filename": other.filename,
                "sha256": other.shasum,
                "size": None,
            }
        else:
            log.warning("%s %s: %s is not in SHA256SUMS", address, upstream.version, platform)

    return ProviderVersion(
        hostname=address.hostname,
        namespace=address.namespace,
        provider_type=address.type,
        version=upstream.version,
        protocols=list(upstream.protocols),
        shasums=shasums,
        shasums_signature=signature,
        signing_key_fingerprint=fingerprint,
        archives=archives,
        _pulp_domain=get_domain(),
    )


def platform_of(key: str) -> Platform:
    """The platform for an ``os_arch`` archive key."""
    os_name, _, arch = key.partition("_")
    return Platform(os=os_name, arch=arch)
