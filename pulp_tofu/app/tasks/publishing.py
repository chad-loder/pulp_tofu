"""The tasks that verify published provider and module releases and add them to a
repository."""

import base64
from pathlib import Path
from typing import TypedDict, Unpack

from django.db import transaction

from pulpcore.plugin.models import Artifact, ContentArtifact, Domain
from pulpcore.plugin.util import get_domain

from pulp_tofu.app.hashes import h1_hash
from pulp_tofu.app.models import (
    ArchiveRecord,
    ModuleVersion,
    ProviderPackage,
    ProviderVersion,
    TofuNamespace,
    TofuRepository,
)
from pulp_tofu.app.protocol import Platform, PlatformKey
from pulp_tofu.app.publishing import ReleaseError, check_module_archive, manifest_protocols
from pulp_tofu.app.signing import parse_sha256sums, verify_sha256sums


class ReleaseConflictError(ReleaseError):
    """The version is already published with different checksums."""


class PublishKwargs(TypedDict):
    """The JSON task arguments ``publish_release`` takes; bytes travel as base64."""

    namespace_pk: str
    hostname: str
    provider_type: str
    version: str
    shasums: str
    # None: the namespace's signing service signs ``shasums``.
    signature: str | None
    manifest: str | None
    archives: dict[str, str]


def publish_release(**args: Unpack[PublishKwargs]) -> None:
    """Verify a release and add it to the repository.

    A signed release must verify against the namespace's keyring. An unsigned one is
    signed by the namespace's signing service, and must verify against its key.
    ``archives`` maps each platform key to the pk of the archive's artifact, created
    when the upload was received.

    Raises:
        ReleaseError: The signature does not verify, the archives and the checksums
            disagree, or the manifest is malformed.
        ReleaseConflictError: The version exists with different checksums.
    """
    namespace, repository = _target(args["namespace_pk"])
    shasums = base64.b64decode(args["shasums"])
    manifest = base64.b64decode(args["manifest"]) if args["manifest"] else None
    if args["signature"] is not None:
        signature = base64.b64decode(args["signature"])
        keys = namespace.armored_keys()
    else:
        service = namespace.signing_service
        if service is None:
            raise ReleaseError(f"namespace {namespace.name} has no signing service")
        # The task's working directory, which the worker removes when the task ends.
        sums_file = Path.cwd() / "SHA256SUMS"
        sums_file.write_bytes(shasums)
        try:
            signature = service.sign_sums(sums_file, Path.cwd())
        except RuntimeError as exc:
            raise ReleaseError(f"the signing service failed: {exc}") from exc
        keys = [service.public_key]
    signer = verify_sha256sums(shasums, signature, keys)
    protocols = manifest_protocols(manifest)
    sums = parse_sha256sums(shasums)

    provider_type, version = args["provider_type"], args["version"]
    prefix = f"terraform-provider-{provider_type}_{version}_"
    listed = {name for name in sums if name.startswith(prefix) and name.endswith(".zip")}
    artifacts: dict[Platform, Artifact] = {}
    for key, pk in args["archives"].items():
        platform = Platform.from_key(key)
        if platform is None:
            raise ReleaseError(f"{key} is not an os_arch platform")
        artifacts[platform] = Artifact.objects.get(pk=pk)
    names = {platform: f"{prefix}{platform.key}.zip" for platform in artifacts}
    if set(names.values()) != listed:
        raise ReleaseError("the uploaded archives are not the archives SHA256SUMS lists")
    for platform, artifact in artifacts.items():
        if artifact.sha256 != sums[names[platform]]:
            raise ReleaseError(f"{names[platform]} does not match its SHA256SUMS digest")

    records: dict[PlatformKey, ArchiveRecord] = {
        platform.key: {
            "filename": names[platform],
            "sha256": sums[names[platform]],
            "size": artifact.size,
        }
        for platform, artifact in artifacts.items()
    }
    with transaction.atomic():
        provider_version, created = ProviderVersion.objects.select_for_update().get_or_create(
            hostname=args["hostname"],
            namespace=namespace.name,
            provider_type=provider_type,
            version=version,
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
        packages = [
            _package(provider_version, platform, records[platform.key], artifact)
            for platform, artifact in artifacts.items()
        ]

    with repository.new_version() as new_version:
        new_version.add_content(ProviderVersion.objects.filter(pk=provider_version.pk))
        new_version.add_content(ProviderPackage.objects.filter(pk__in=[p.pk for p in packages]))


def _package(
    provider_version: ProviderVersion, platform: Platform, record: ArchiveRecord, artifact: Artifact
) -> ProviderPackage:
    package, _ = ProviderPackage.objects.get_or_create(
        hostname=provider_version.hostname,
        namespace=provider_version.namespace,
        provider_type=provider_version.provider_type,
        version=provider_version.version,
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


class PublishModuleKwargs(TypedDict):
    """The JSON task arguments ``publish_module`` takes."""

    namespace_pk: str
    hostname: str
    name: str
    system: str
    version: str
    artifact_pk: str


def publish_module(**args: Unpack[PublishModuleKwargs]) -> None:
    """Check a module package and add it to the namespace's repository.

    Raises:
        ReleaseError: The package is not a safe ``.tar.gz`` of a module directory.
        ReleaseConflictError: The version exists with a different package.
    """
    namespace, repository = _target(args["namespace_pk"])
    artifact = Artifact.objects.get(pk=args["artifact_pk"])
    with artifact.file.open("rb") as archive:
        check_module_archive(archive)
    domain = get_domain()
    if domain is None:
        raise RuntimeError("publish_module runs without a domain")
    address: _ModuleKey = {
        "hostname": args["hostname"],
        "namespace": namespace.name,
        "name": args["name"],
        "system": args["system"],
        "version": args["version"],
        "_pulp_domain": domain,
    }
    with transaction.atomic():
        existing = ModuleVersion.objects.select_for_update().filter(**address).first()
        if existing is not None and existing.sha256 != artifact.sha256:
            raise ReleaseConflictError(
                f"{namespace.name}/{args['name']}/{args['system']} {args['version']} "
                "is already published"
            )
        module, _ = ModuleVersion.objects.get_or_create(
            **address, sha256=artifact.sha256, defaults={"size": artifact.size}
        )
        ContentArtifact.objects.get_or_create(
            content=module, relative_path=module.relative_path, defaults={"artifact": artifact}
        )
    with repository.new_version() as new_version:
        new_version.add_content(ModuleVersion.objects.filter(pk=module.pk))


class _ModuleKey(TypedDict):
    """The fields that identify a module version within a domain."""

    hostname: str
    namespace: str
    name: str
    system: str
    version: str
    _pulp_domain: Domain


def _target(namespace_pk: str) -> tuple[TofuNamespace, TofuRepository]:
    """The namespace and the repository of the distribution that serves it."""
    namespace = TofuNamespace.objects.select_related(
        "distribution", "keyring", "signing_service"
    ).get(pk=namespace_pk)
    distribution = namespace.distribution
    if distribution is None or distribution.repository is None:
        raise ReleaseError(f"namespace {namespace.name} has no repository to publish to")
    repository = distribution.repository.cast()
    if not isinstance(repository, TofuRepository):
        raise ReleaseError(f"namespace {namespace.name} is not served by a tofu repository")
    return namespace, repository
