"""The OpenTofu provider registry protocol, served by the API workers.

https://opentofu.org/docs/internals/provider-registry-protocol/. One registry per Pulp
host: discovery at the host root names it, and each request's namespace selects the
``TofuNamespace`` whose distribution answers. Answers are built per request from that
distribution's repository version; archive and checksum URLs point at the content app.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast, override

from django.conf import settings
from django.db.models import QuerySet
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from yarl import URL

from pulpcore.plugin.models import ContentRedirectContentGuard
from pulpcore.plugin.util import get_domain

from .authentication import registry_authenticators
from .hashes import zh_hash
from .models import (
    ModuleVersion,
    ProviderPackage,
    ProviderVersion,
    TofuDistribution,
    TofuNamespace,
    TofuToken,
)
from .protocol import Platform, normalize_hostname

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser, AnonymousUser

PROVIDERS_SERVICE = "providers.v1"
MODULES_SERVICE = "modules.v1"
CHECKSUMS_SEGMENT = "_checksums"


def registry_hostname() -> str | None:
    """``TOFU_REGISTRY_HOSTNAME`` as OpenTofu compares hostnames, or None if unset."""
    hostname = settings.TOFU_REGISTRY_HOSTNAME
    return normalize_hostname(hostname) if isinstance(hostname, str) and hostname else None


def service_url(service: Literal["providers", "modules"]) -> URL:
    """A v1 service's path, with the domain segment when domains are enabled."""
    segments = str(settings.TOFU_REGISTRY_PATH_PREFIX).strip("/").split("/")
    if settings.DOMAIN_ENABLED:
        segments.append(str(settings.TOFU_REGISTRY_DOMAIN))
    return URL("/").joinpath(*segments, "v1", service, "")


def _errors(status: int, message: str) -> Response:
    return Response({"errors": [message]}, status=status)


class _RegistryView(APIView):
    """Base for registry endpoints: JSON only, authentication from the plugin's setting.

    Authorization is per namespace inside each view, so DRF's permission classes and
    pulpcore's access policies do not apply here. The views stay out of Pulp's OpenAPI
    schema, as pulp_container's registry views do: OpenTofu speaks this protocol, not
    the generated bindings.
    """

    schema = None
    renderer_classes = [JSONRenderer]
    permission_classes: list[type[BasePermission]] = []

    @override
    def get_authenticators(self) -> list[BaseAuthentication]:
        return registry_authenticators()

    def _namespace(self, request: Request, name: str) -> "_Served | Response":
        """The namespace the caller may read, or the error response to send instead."""
        if not settings.TOFU_REGISTRY_ENABLED:
            return _errors(404, "Not Found")
        namespace = (
            TofuNamespace.objects.select_related("distribution__content_guard")
            .filter(name=name, pulp_domain=get_domain())
            .first()
        )
        if namespace is None or namespace.distribution is None:
            return _errors(404, "Not Found")
        if namespace.private:
            user = cast("AbstractUser | AnonymousUser", request.user)
            token = request.auth if isinstance(request.auth, TofuToken) else None
            if not user.is_authenticated:
                return _errors(401, "Unauthorized")
            if not user.has_perm("tofu.view_tofunamespace", namespace) or (
                token is not None and not token.allows(namespace, "read")
            ):
                return _errors(403, "Forbidden")
        return _Served(namespace=namespace, distribution=namespace.distribution)

    @staticmethod
    def _versions(served: "_Served", provider_type: str) -> "QuerySet[ProviderVersion]":
        distribution = served.distribution
        _, repository_version, _ = distribution.get_repository_publication_and_version()
        if repository_version is None:
            return ProviderVersion.objects.none()
        hostname = served.namespace.content_hostname or registry_hostname()
        return ProviderVersion.objects.filter(
            pk__in=repository_version.content,
            hostname=hostname,
            namespace=served.namespace.name,
            provider_type=provider_type,
        )

    @staticmethod
    def _modules(served: "_Served", name: str, system: str) -> "QuerySet[ModuleVersion]":
        _, repository_version, _ = served.distribution.get_repository_publication_and_version()
        if repository_version is None:
            return ModuleVersion.objects.none()
        return ModuleVersion.objects.filter(
            pk__in=repository_version.content,
            hostname=served.namespace.content_hostname or registry_hostname(),
            namespace=served.namespace.name,
            name=name,
            system=system,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Served:
    """A namespace the caller may read, and the distribution that serves it."""

    namespace: TofuNamespace
    distribution: TofuDistribution


class DiscoveryView(_RegistryView):
    """``GET /.well-known/terraform.json``."""

    def get(self, request: Request) -> Response:
        if not settings.TOFU_REGISTRY_ENABLED:
            return _errors(404, "Not Found")
        return Response(
            {
                PROVIDERS_SERVICE: str(service_url("providers")),
                MODULES_SERVICE: str(service_url("modules")),
            }
        )


class ModuleVersionsView(_RegistryView):
    """``GET …/v1/modules/:namespace/:name/:system/versions``."""

    def get(self, request: Request, namespace: str, name: str, system: str) -> Response:
        found = self._namespace(request, namespace)
        if isinstance(found, Response):
            return found
        versions = [
            {"version": v}
            for v in self._modules(found, name, system).values_list("version", flat=True)
        ]
        if not versions:
            return _errors(404, "Not Found")
        return Response({"modules": [{"versions": versions}]})


class ModuleDownloadView(_RegistryView):
    """``GET …/v1/modules/:namespace/:name/:system/:version/download``.

    The location is both the JSON ``location`` that current OpenTofu reads and the
    ``X-Terraform-Get`` header that older clients read. It is an HTTPS URL ending in
    ``.tar.gz``, which go-getter unpacks by its extension.
    """

    def get(
        self, request: Request, namespace: str, name: str, system: str, version: str
    ) -> Response:
        found = self._namespace(request, namespace)
        if isinstance(found, Response):
            return found
        module = self._modules(found, name, system).filter(version=version).first()
        if module is None:
            return _errors(404, "Not Found")
        location = _content_url(
            found, found.distribution.mirror_url().joinpath(*module.relative_path.split("/"))
        )
        return Response({"location": location}, headers={"X-Terraform-Get": location})


class ProviderVersionsView(_RegistryView):
    """``GET …/v1/providers/:namespace/:type/versions``."""

    def get(self, request: Request, namespace: str, provider_type: str) -> Response:
        found = self._namespace(request, namespace)
        if isinstance(found, Response):
            return found
        versions = [
            {
                "version": pv.version,
                "protocols": list(pv.protocol_versions),
                "platforms": [
                    {"os": p.os, "arch": p.arch}
                    for key in pv.archive_records()
                    if (p := Platform.from_key(key)) is not None
                ],
            }
            for pv in self._versions(found, provider_type)
        ]
        if not versions:
            return _errors(404, "Not Found")
        return Response({"versions": versions})


class ProviderDownloadView(_RegistryView):
    """``GET …/v1/providers/:namespace/:type/:version/download/:os/:arch``."""

    def get(
        self, request: Request, namespace: str, provider_type: str, version: str, os: str, arch: str
    ) -> Response:
        found = self._namespace(request, namespace)
        if isinstance(found, Response):
            return found
        provider_version = self._versions(found, provider_type).filter(version=version).first()
        if provider_version is None or not provider_version.signing_key:
            # A registry other than registry.opentofu.org must list a signing key.
            return _errors(404, "Not Found")
        records = provider_version.archive_records()
        key = Platform(os=os, arch=arch).key
        record = records.get(key)
        if record is None:
            return _errors(404, "Not Found")
        base = _content_base(found, provider_version)
        h1s = dict(
            ProviderPackage.objects.filter(
                hostname=provider_version.hostname,
                namespace=provider_version.namespace,
                provider_type=provider_version.provider_type,
                version=provider_version.version,
                _pulp_domain=get_domain(),
            )
            .exclude(h1="")
            .values_list("sha256", "h1")
        )
        document: dict[str, object] = {
            "protocols": list(provider_version.protocol_versions),
            "os": os,
            "arch": arch,
            "filename": record["filename"],
            "download_url": _content_url(found, base / record["filename"]),
            "shasums_url": _content_url(found, base.joinpath(CHECKSUMS_SEGMENT, "SHA256SUMS")),
            "shasums_signature_url": _content_url(
                found, base.joinpath(CHECKSUMS_SEGMENT, "SHA256SUMS.sig")
            ),
            "shasum": record["sha256"],
            "signing_keys": {
                "gpg_public_keys": [
                    {
                        "key_id": provider_version.signing_key_fingerprint[-16:].upper(),
                        "ascii_armor": provider_version.signing_key,
                    }
                ]
            },
        }
        # OpenTofu requires every platform with a size when the map is present.
        if all(r["size"] for r in records.values()):
            document["packages"] = {
                platform_key: {
                    "hashes": [zh_hash(r["sha256"]), *filter(None, [h1s.get(r["sha256"])])],
                    "package_size": r["size"],
                }
                for platform_key, r in records.items()
            }
        return Response(document)


def _content_url(served: _Served, url: URL) -> str:
    """``url``, preauthenticated when a redirect guard protects the distribution.

    OpenTofu sends no credentials for archives, checksums or signatures, so a guarded
    distribution is readable only through URLs its ``ContentRedirectContentGuard``
    signed; they expire after the guard's ``EXPIRETIME``. Other guards refuse OpenTofu.
    """
    guard = served.distribution.content_guard
    redirect = guard.cast() if guard is not None else None
    if isinstance(redirect, ContentRedirectContentGuard):
        return str(redirect.preauthenticate_url(str(url)))
    return str(url)


def _content_base(served: _Served, provider_version: ProviderVersion) -> URL:
    """The content-app URL of a provider version's directory in the namespace's mirror."""
    return served.distribution.mirror_url().joinpath(
        provider_version.hostname,
        provider_version.namespace,
        provider_version.provider_type,
        provider_version.version,
        "",
    )
