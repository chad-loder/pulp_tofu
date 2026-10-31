"""REST API viewsets for pulp_tofu."""

import base64
from gettext import gettext as _

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request

from pulpcore.plugin import viewsets as core
from pulpcore.plugin.actions import ModifyRepositoryActionMixin
from pulpcore.plugin.models import Artifact
from pulpcore.plugin.serializers import AsyncOperationResponseSerializer
from pulpcore.plugin.tasking import dispatch
from pulpcore.plugin.util import get_domain

from . import models, serializers, tasks
from .publishing import ReleaseFiles


def _object_policy(model: str, *, create: list[str], update: list[str]) -> list[dict[str, object]]:
    """The standard owner/viewer statements for a model in the ``tofu`` app."""
    perm = f"tofu.{{}}_{model}"
    view = f"has_model_or_domain_or_obj_perms:{perm.format('view')}"
    return [
        {"action": ["list", "my_permissions"], "principal": "authenticated", "effect": "allow"},
        {
            "action": ["create"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": [f"has_model_or_domain_perms:{perm.format('add')}", *create],
        },
        {
            "action": ["retrieve"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": view,
        },
        {
            "action": ["update", "partial_update", "set_label", "unset_label"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": [
                f"has_model_or_domain_or_obj_perms:{perm.format('change')}",
                view,
                *update,
            ],
        },
        {
            "action": ["destroy"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": [f"has_model_or_domain_or_obj_perms:{perm.format('delete')}", view],
        },
        {
            "action": ["list_roles", "add_role", "remove_role"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": f"has_model_or_domain_or_obj_perms:{perm.format('manage_roles')}",
        },
    ]


def _roles(model: str, *extra: str) -> dict[str, list[str]]:
    owner = [f"tofu.{verb}_{model}" for verb in ("view", "change", "delete", *extra)]
    return {
        f"tofu.{model}_creator": [f"tofu.add_{model}"],
        f"tofu.{model}_owner": [*owner, f"tofu.manage_roles_{model}"],
        f"tofu.{model}_viewer": [f"tofu.view_{model}"],
    }


def _creation_hooks(model: str) -> list[dict[str, object]]:
    return [
        {
            "function": "add_roles_for_object_creator",
            "parameters": {"roles": f"tofu.{model}_owner"},
        }
    ]


_REPOSITORY_VIEW = "has_repository_model_or_domain_or_obj_perms:tofu.view_tofurepository"
_REMOTE_PARAM_VIEW = "has_remote_param_model_or_domain_or_obj_perms:tofu.view_tofuremote"


class ProviderVersionFilter(core.ContentFilter):
    """Filters for provider versions."""

    class Meta:
        model = models.ProviderVersion
        fields = ["hostname", "namespace", "provider_type", "version"]


class ProviderVersionViewSet(core.ReadOnlyContentViewSet):
    """Provider versions Pulp has verified and stored."""

    endpoint_name = "provider_versions"  # type: ignore[assignment]
    queryset = models.ProviderVersion.objects.all()
    serializer_class = serializers.ProviderVersionSerializer
    filterset_class = ProviderVersionFilter


class ProviderPackageFilter(core.ContentFilter):
    """Filters for provider packages."""

    class Meta:
        model = models.ProviderPackage
        fields = ["hostname", "namespace", "provider_type", "version", "os", "arch", "sha256"]


class ProviderPackageViewSet(core.ReadOnlyContentViewSet):
    """Provider archives, one per platform of a provider version."""

    endpoint_name = "provider_packages"  # type: ignore[assignment]
    queryset = models.ProviderPackage.objects.all()
    serializer_class = serializers.ProviderPackageSerializer
    filterset_class = ProviderPackageFilter


class TofuRemoteViewSet(core.RemoteViewSet, core.RolesMixin):
    """Upstream OpenTofu provider registries."""

    endpoint_name = "tofu"  # type: ignore[assignment]
    queryset = models.TofuRemote.objects.all()
    serializer_class = serializers.TofuRemoteSerializer
    queryset_filtering_required_permission = "tofu.view_tofuremote"

    DEFAULT_ACCESS_POLICY = {
        "statements": _object_policy("tofuremote", create=[], update=[]),
        "creation_hooks": _creation_hooks("tofuremote"),
        "queryset_scoping": {"function": "scope_queryset"},
    }
    LOCKED_ROLES = _roles("tofuremote")


class TofuRepositoryViewSet(core.RepositoryViewSet, ModifyRepositoryActionMixin, core.RolesMixin):
    """Repositories of provider versions and archives."""

    endpoint_name = "tofu"  # type: ignore[assignment]
    queryset = models.TofuRepository.objects.all()
    serializer_class = serializers.TofuRepositorySerializer
    queryset_filtering_required_permission = "tofu.view_tofurepository"

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            *_object_policy(
                "tofurepository", create=[_REMOTE_PARAM_VIEW], update=[_REMOTE_PARAM_VIEW]
            ),
            {
                "action": ["modify"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:tofu.modify_tofurepository",
                    "has_model_or_domain_or_obj_perms:tofu.view_tofurepository",
                ],
            },
            {
                "action": ["sync"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:tofu.sync_tofurepository",
                    "has_model_or_domain_or_obj_perms:tofu.view_tofurepository",
                    _REMOTE_PARAM_VIEW,
                ],
            },
        ],
        "creation_hooks": _creation_hooks("tofurepository"),
        "queryset_scoping": {"function": "scope_queryset"},
    }
    LOCKED_ROLES = _roles("tofurepository", "modify", "repair", "sync")

    @extend_schema(
        description="Fetch the selected providers from a remote into a new repository version.",
        summary="Sync from a remote",
        responses={202: AsyncOperationResponseSerializer},
    )
    @action(detail=True, methods=["post"], serializer_class=serializers.TofuSyncSerializer)
    def sync(self, request: Request, pk: str) -> core.OperationPostponedResponse:
        repository = self.get_object()
        serializer = serializers.TofuSyncSerializer(
            data=request.data, context={"request": request, "repository_pk": pk}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        remote = data.get("remote", repository.remote)
        kwargs: tasks.SyncKwargs = {
            "remote_pk": str(remote.pk),
            "repository_pk": str(repository.pk),
            "providers": [
                {
                    "namespace": str(p["namespace"]),
                    "type": str(p["type"]),
                    "versions": [str(v) for v in p["versions"]],
                    "latest": int(p["latest"]),
                }
                for p in data["providers"]
            ],
            "platforms": [str(p) for p in data["platforms"]],
            "mirror": bool(data["mirror"]),
        }
        result = dispatch(
            tasks.synchronize,
            exclusive_resources=[repository],
            shared_resources=[remote],
            kwargs=kwargs,
        )
        return core.OperationPostponedResponse(result, request)


class TofuRepositoryVersionViewSet(core.RepositoryVersionViewSet):
    """Versions of a tofu repository."""

    parent_viewset = TofuRepositoryViewSet

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list", "retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": _REPOSITORY_VIEW,
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_repository_model_or_domain_or_obj_perms:tofu.delete_tofurepository",
                    _REPOSITORY_VIEW,
                ],
            },
            {
                "action": ["repair"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_repository_model_or_domain_or_obj_perms:tofu.repair_tofurepository",
                    _REPOSITORY_VIEW,
                ],
            },
        ],
    }


class TofuDistributionViewSet(core.DistributionViewSet, core.RolesMixin):  # type: ignore[misc]
    """OpenTofu provider network mirrors."""

    endpoint_name = "tofu"  # type: ignore[assignment]
    queryset = models.TofuDistribution.objects.all()
    serializer_class = serializers.TofuDistributionSerializer
    queryset_filtering_required_permission = "tofu.view_tofudistribution"

    _PARAMS = [
        "has_repo_or_repo_ver_param_model_or_domain_or_obj_perms:tofu.view_tofurepository",
        _REMOTE_PARAM_VIEW,
    ]
    DEFAULT_ACCESS_POLICY = {
        "statements": _object_policy("tofudistribution", create=_PARAMS, update=_PARAMS),
        "creation_hooks": _creation_hooks("tofudistribution"),
        "queryset_scoping": {"function": "scope_queryset"},
    }
    LOCKED_ROLES = _roles("tofudistribution")


class TofuNamespaceViewSet(
    core.NamedModelViewSet,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    core.RolesMixin,
):
    """Registry namespaces: who owns, publishes to and reads each one."""

    endpoint_name = "pulp_tofu/namespaces"  # type: ignore[assignment]
    queryset = models.TofuNamespace.objects.all()
    serializer_class = serializers.TofuNamespaceSerializer
    filterset_fields = ["name"]
    queryset_filtering_required_permission = "tofu.view_tofunamespace"

    DEFAULT_ACCESS_POLICY = {
        # The serializer checks that the caller may view the distribution it names.
        "statements": [
            *_object_policy("tofunamespace", create=[], update=[]),
            {
                "action": ["publish"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:tofu.publish_tofunamespace",
            },
        ],
        "creation_hooks": _creation_hooks("tofunamespace"),
        "queryset_scoping": {"function": "scope_queryset"},
    }
    # As pulp_container's namespaces: owners manage, collaborators publish, consumers read.
    LOCKED_ROLES = {
        "tofu.tofunamespace_creator": ["tofu.add_tofunamespace"],
        "tofu.tofunamespace_owner": [
            "tofu.view_tofunamespace",
            "tofu.change_tofunamespace",
            "tofu.delete_tofunamespace",
            "tofu.publish_tofunamespace",
            "tofu.manage_roles_tofunamespace",
        ],
        "tofu.tofunamespace_collaborator": [
            "tofu.view_tofunamespace",
            "tofu.publish_tofunamespace",
        ],
        "tofu.tofunamespace_consumer": ["tofu.view_tofunamespace"],
    }

    @extend_schema(
        description="Publish a provider release to the namespace.",
        request={"multipart/form-data": serializers.ProviderReleaseSerializer},
        responses={202: AsyncOperationResponseSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        serializer_class=serializers.ProviderReleaseSerializer,
        parser_classes=[MultiPartParser],
    )
    def publish(self, request: Request, pk: str) -> core.OperationPostponedResponse:
        """Store the release's archives, then verify and add it in a task."""
        namespace: models.TofuNamespace = self.get_object()
        repository = _upload_repository(namespace)
        serializer = serializers.ProviderReleaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        release: ReleaseFiles = serializer.validated_data["release"]
        small: dict[str, bytes] = serializer.validated_data["small"]
        uploads: dict[str, UploadedFile] = serializer.validated_data["archives"]

        existing = models.ProviderVersion.objects.filter(
            hostname=settings.TOFU_REGISTRY_HOSTNAME,
            namespace=namespace.name,
            provider_type=release.provider_type,
            version=release.version,
            _pulp_domain=get_domain(),
        ).first()
        if existing is not None and bytes(existing.shasums) != small[release.shasums]:
            raise ReleaseConflict(
                f"{namespace.name}/{release.provider_type} {release.version} is already published"
            )

        archives = {
            str(key): str(_stored_artifact(uploads[name]).pk)
            for key, name in release.archives.items()
        }
        encoded = {name: base64.b64encode(data).decode() for name, data in small.items()}
        kwargs: tasks.PublishKwargs = {
            "namespace_pk": str(namespace.pk),
            "provider_type": release.provider_type,
            "version": release.version,
            "shasums": encoded[release.shasums],
            "signature": encoded[release.signature],
            "manifest": encoded[release.manifest] if release.manifest else None,
            "archives": archives,
        }
        result = dispatch(
            tasks.publish_release,
            exclusive_resources=[repository],
            shared_resources=[namespace],
            kwargs=kwargs,
        )
        return core.OperationPostponedResponse(result, request)


class ReleaseConflict(APIException):
    """409: the version is already published with different checksums."""

    status_code = 409
    default_detail = "The provider version is already published."
    default_code = "conflict"


def _upload_repository(namespace: models.TofuNamespace) -> models.TofuRepository:
    """The repository a release published to ``namespace`` goes into.

    Raises:
        ValidationError: The namespace's distribution does not accept uploads.
    """
    if not settings.TOFU_REGISTRY_HOSTNAME:
        raise ValidationError(_("Set TOFU_REGISTRY_HOSTNAME to publish providers."))
    if namespace.content_hostname:
        raise ValidationError(
            _("The namespace serves content from {host}; publish to one that does not.").format(
                host=namespace.content_hostname
            )
        )
    distribution = namespace.distribution
    if distribution is None or not distribution.allow_uploads or distribution.remote is not None:
        raise ValidationError(_("The namespace's distribution does not accept uploads."))
    repository = distribution.repository.cast() if distribution.repository else None
    if not isinstance(repository, models.TofuRepository):
        raise ValidationError(_("The namespace's distribution has no tofu repository."))
    return repository


def _stored_artifact(upload: UploadedFile) -> Artifact:
    """The artifact holding ``upload``, reusing one with the same digest.

    Follows pulpcore's ``SingleArtifactContentUploadViewSet.init_content_data``.
    """
    artifact = Artifact.init_and_validate(upload)
    domain = get_domain()
    try:
        existing = Artifact.objects.get(sha256=artifact.sha256, pulp_domain=domain)
    except Artifact.DoesNotExist:
        existing = None
    if existing is not None and existing.pulp_domain.get_storage().exists(existing.file.name):
        existing.touch()
        return existing
    if existing is not None:
        existing.file = upload
        existing.save()
        return existing
    try:
        artifact.save()
    except IntegrityError:
        artifact = Artifact.objects.get(sha256=artifact.sha256, pulp_domain=domain)
        artifact.touch()
    return artifact
