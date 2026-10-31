"""The task that verifies a published provider release and adds it to a repository."""

import base64
from typing import TypedDict, Unpack

from django.conf import settings
from django.db import transaction

from pulpcore.plugin.models import Artifact, ContentArtifact
from pulpcore.plugin.util import get_domain

from pulp_tofu.app.hashes import h1_hash
from pulp_tofu.app.models import (
    ArchiveRecord,
    ProviderPackage,
    ProviderVersion,
    TofuNamespace,
    TofuRepository,
)
from pulp_tofu.app.protocol import Platform, PlatformKey
from pulp_tofu.app.publishing import ReleaseError, manifest_protocols
from pulp_tofu.app.signing import parse_sha256sums, verify_sha256sums


class ReleaseConflictError(ReleaseError):
    """The version is already published with different checksums."""


class PublishKwargs(TypedDict):
    """The JSON task arguments ``publish_release`` takes; bytes travel as base64."""

    namespace_pk: str
    provider_type: str
    version: str
    shasums: str
    signature: str
    manifest: str | None
    archives: dict[str, str]


def publish_release(**args: Unpack[PublishKwargs]) -> None:
    """Verify a release against the namespace's keyring and add it to the repository.

    ``archives`` maps each platform key to the pk of the archive's artifact, created
    when the upload was received.

    Raises:
        ReleaseError: The signature does not verify, the archives and the checksums
            disagree, or the manifest is malformed.
        ReleaseConflictError: The version exists with different checksums.
    """
    namespace = TofuNamespace.objects.select_related("distribution", "keyring").get(
        pk=args["namespace_pk"]
    )
    distribution = namespace.distribution
    if distribution is None or distribution.repository is None:
        raise ReleaseError(f"namespace {namespace.name} has no repository to publish to")
    repository = distribution.repository.cast()
    if not isinstance(repository, TofuRepository):
        raise ReleaseError(f"namespace {namespace.name} is not served by a tofu repository")

    shasums = base64.b64decode(args["shasums"])
    signature = base64.b64decode(args["signature"])
    manifest = base64.b64decode(args["manifest"]) if args["manifest"] else None
    signer = verify_sha256sums(shasums, signature, namespace.armored_keys())
    protocols = manifest_protocols(manifest)
    sums = parse_sha256sums(shasums)

    provider_type, version = args["provider_type"], args["version"]
    prefix = f"terraform-provider-{provider_type}_{version}_"
    listed = {name for name in sums if name.startswith(prefix) and name.endswith(".zip")}
    artifacts = {
        PlatformKey(key): Artifact.objects.get(pk=pk) for key, pk in args["archives"].items()
    }
    names = {key: f"{prefix}{key}.zip" for key in artifacts}
    if set(names.values()) != listed:
        raise ReleaseError("the uploaded archives are not the archives SHA256SUMS lists")
    for key, artifact in artifacts.items():
        if artifact.sha256 != sums[names[key]]:
            raise ReleaseError(f"{names[key]} does not match its SHA256SUMS digest")

    hostname = str(settings.TOFU_REGISTRY_HOSTNAME)
    address = {
        "hostname": hostname,
        "namespace": namespace.name,
        "provider_type": provider_type,
        "version": version,
    }
    records: dict[PlatformKey, ArchiveRecord] = {
        key: {"filename": names[key], "sha256": sums[names[key]], "size": artifact.size}
        for key, artifact in artifacts.items()
    }
    with transaction.atomic():
        provider_version, created = ProviderVersion.objects.select_for_update().get_or_create(
            **address,
            _pulp_domain=get_domain(),
            defaults={
                "protocols": list(protocols),
                "shasums": shasums,
                "shasums_signature": signature,
                "signing_key_fingerprint": signer.fingerprint,
                "signing_key": signer.public_key,
                "archives": records,
            },
        )
        if not created and bytes(provider_version.shasums) != shasums:
            raise ReleaseConflictError(
                f"{namespace.name}/{provider_type} {version} is already published"
            )
        packages = [_package(address, key, records[key], artifacts[key]) for key in artifacts]

    with repository.new_version() as new_version:
        new_version.add_content(ProviderVersion.objects.filter(pk=provider_version.pk))
        new_version.add_content(ProviderPackage.objects.filter(pk__in=[p.pk for p in packages]))


def _package(
    address: dict[str, str], key: PlatformKey, record: ArchiveRecord, artifact: Artifact
) -> ProviderPackage:
    platform = Platform.from_key(key)
    assert platform is not None  # The upload serializer parsed every key.
    package, _ = ProviderPackage.objects.get_or_create(
        **address,
        os=platform.os,
        arch=platform.arch,
        sha256=record["sha256"],
        _pulp_domain=get_domain(),
        defaults={"filename": record["filename"], "size": record["size"]},
    )
    ContentArtifact.objects.get_or_create(
        content=package, relative_path=package.relative_path, defaults={"artifact": artifact}
    )
    if not package.h1:
        with artifact.file.open("rb") as archive:
            package.h1 = h1_hash(archive)
        package.save(update_fields=["h1"])
    return package
