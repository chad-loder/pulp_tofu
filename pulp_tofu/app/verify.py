"""Fetch a provider version's checksums from upstream and verify them before use.

Shared by the content app's pull-through handler and the sync task.
"""

import asyncio
import logging

from pulpcore.plugin.util import get_domain

from .hashes import Sha256Hex
from .models import ArchiveRecord, ProviderVersion, TofuRemote
from .protocol import Platform, PlatformKey, ProviderAddress
from .registry import RegistryClient, UpstreamError, UpstreamVersion
from .signing import SignatureError, Signer, parse_sha256sums, verify_sha256sums

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
    signer: Signer | None
    trusted = remote.trusted_fingerprints
    if info.signing_keys or trusted:
        signer = verify_sha256sums(shasums, signature, info.signing_keys, trusted=trusted)
    elif address.hostname == KEYLESS_REGISTRY and not remote.require_signatures:
        signer = None
    else:
        raise SignatureError(f"{address} {upstream.version} offers no signing keys")
    sums = parse_sha256sums(shasums)
    if sums.get(info.filename) != info.shasum:
        raise UpstreamError("SHA256SUMS does not list the download document's shasum")
    by_digest: dict[Sha256Hex, list[str]] = {}
    for filename, digest in sums.items():
        by_digest.setdefault(digest, []).append(filename)

    archives: dict[PlatformKey, ArchiveRecord] = {}
    unreported: list[Platform] = []
    for platform in platforms:
        key = platform.key
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
            archives[platform.key] = {
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
        # The columns store no key for a provider accepted unsigned.
        signing_key_fingerprint=signer.fingerprint if signer else "",
        signing_key=signer.public_key if signer else "",
        archives=archives,
        _pulp_domain=get_domain(),
    )


def platform_of(key: PlatformKey) -> Platform:
    """The platform for an ``os_arch`` key that ``parse_archive_records`` produced."""
    platform = Platform.from_key(key)
    if platform is None:
        raise ValueError(f"not a platform key: {key!r}")
    return platform
