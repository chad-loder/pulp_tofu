"""The OpenTofu provider registry protocol, served by the API workers.

https://opentofu.org/docs/internals/provider-registry-protocol/. One registry per Pulp
host: discovery at the host root names it, and each request's namespace selects the
``TofuNamespace`` whose distribution answers. Answers are built per request from that
distribution's repository version; archive and checksum URLs point at the content app.
"""

from typing import TYPE_CHECKING, Any, cast, override

from django.conf import settings
from django.utils.module_loading import import_string
from rest_framework.authentication import BaseAuthentication
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView
from yarl import URL

from pulpcore.plugin.util import get_domain

from .hashes import zh_hash
from .models import ProviderPackage, ProviderVersion, TofuNamespace
from .protocol import Platform

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser, AnonymousUser

PROVIDERS_SERVICE = "providers.v1"
CHECKSUMS_SEGMENT = "_checksums"


def providers_url() -> URL:
    """The providers.v1 API's path, with the domain segment when domains are enabled."""
    segments = str(settings.TOFU_REGISTRY_PATH_PREFIX).strip("/").split("/")
    if settings.DOMAIN_ENABLED:
        segments.append(str(settings.TOFU_REGISTRY_DOMAIN))
    return URL("/").joinpath(*segments, "v1", "providers", "")


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
    permission_classes: list[Any] = []

    @override
    def get_authenticators(self) -> list[BaseAuthentication]:
        extra = [import_string(path) for path in settings.TOFU_AUTHENTICATION_CLASSES]
        defaults = cast(
            "list[type[BaseAuthentication]]", api_settings.DEFAULT_AUTHENTICATION_CLASSES
        )
        return [cls() for cls in (*extra, *defaults)]

    def _namespace(self, request: Request, name: str) -> TofuNamespace | Response:
        """The namespace the caller may read, or the error response to send instead."""
        if not settings.TOFU_REGISTRY_ENABLED:
            return _errors(404, "Not Found")
        namespace = (
            TofuNamespace.objects.select_related("distribution")
            .filter(name=name, pulp_domain=get_domain())
            .first()
        )
        if namespace is None or namespace.distribution is None:
            return _errors(404, "Not Found")
        user = cast("AbstractUser | AnonymousUser", request.user)
        if namespace.private and not user.has_perm("tofu.view_tofunamespace", namespace):
            if user.is_authenticated:
                return _errors(403, "Forbidden")
            return _errors(401, "Unauthorized")
        return namespace

    @staticmethod
    def _versions(namespace: TofuNamespace, provider_type: str) -> "Any":
        distribution = namespace.distribution
        assert distribution is not None  # _namespace refuses a namespace without one.
        _, repository_version, _ = distribution.get_repository_publication_and_version()
        if repository_version is None:
            return ProviderVersion.objects.none()
        hostname = namespace.content_hostname or settings.TOFU_REGISTRY_HOSTNAME
        return ProviderVersion.objects.filter(
            pk__in=repository_version.content,
            hostname=hostname,
            namespace=namespace.name,
            provider_type=provider_type,
        )


class DiscoveryView(_RegistryView):
    """``GET /.well-known/terraform.json``."""

    def get(self, request: Request) -> Response:
        if not settings.TOFU_REGISTRY_ENABLED:
            return _errors(404, "Not Found")
        return Response({PROVIDERS_SERVICE: str(providers_url())})


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
            "download_url": str(base / record["filename"]),
            "shasums_url": str(base.joinpath(CHECKSUMS_SEGMENT, "SHA256SUMS")),
            "shasums_signature_url": str(base.joinpath(CHECKSUMS_SEGMENT, "SHA256SUMS.sig")),
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


def _content_base(namespace: TofuNamespace, provider_version: ProviderVersion) -> URL:
    """The content-app URL of a provider version's directory in the namespace's mirror."""
    distribution = namespace.distribution
    assert distribution is not None
    return distribution.mirror_url().joinpath(
        provider_version.hostname,
        provider_version.namespace,
        provider_version.provider_type,
        provider_version.version,
        "",
    )
