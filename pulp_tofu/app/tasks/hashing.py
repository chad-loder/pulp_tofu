"""Tasks that derive hashes from stored provider archives."""

from pulpcore.plugin.models import ContentArtifact

from pulp_tofu.app.hashes import h1_hash
from pulp_tofu.app.models import ProviderPackage


def record_h1(package_pk: str) -> None:
    """Compute and store a package's ``h1:`` hash from its stored archive.

    Does nothing when the hash is already known or the archive is not stored.
    """
    package = ProviderPackage.objects.get(pk=package_pk)
    if package.h1:
        return
    content_artifact = (
        ContentArtifact.objects.select_related("artifact")
        .filter(content=package, artifact__isnull=False)
        .first()
    )
    if content_artifact is None:
        return
    with content_artifact.artifact.file.open("rb") as archive:
        h1 = h1_hash(archive)
    ProviderPackage.objects.filter(pk=package.pk, h1="").update(h1=h1)
