"""REST API viewsets for pulp_tofu."""

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request

from pulpcore.plugin import viewsets as core
from pulpcore.plugin.actions import ModifyRepositoryActionMixin
from pulpcore.plugin.serializers import AsyncOperationResponseSerializer
from pulpcore.plugin.tasking import dispatch

from . import models, serializers, tasks


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
