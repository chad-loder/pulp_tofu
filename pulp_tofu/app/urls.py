"""Root-level URLs: the registry's discovery document and its API.

pulpcore mounts every plugin's ``urls.py`` at the URL root, outside ``API_ROOT``, as it
does for pulp_container's ``/v2/`` and pulp_python's ``/pypi/``.
"""

from django.conf import settings
from django.urls import path

from .registry_api import DiscoveryView, ProviderDownloadView, ProviderVersionsView

_PREFIX = str(settings.TOFU_REGISTRY_PATH_PREFIX).strip("/")
_DOMAIN = "<slug:pulp_domain>/" if settings.DOMAIN_ENABLED else ""
_PROVIDERS = f"{_PREFIX}/{_DOMAIN}v1/providers/<str:namespace>/<str:provider_type>/"

urlpatterns = [
    path(".well-known/terraform.json", DiscoveryView.as_view()),
    path(f"{_PROVIDERS}versions", ProviderVersionsView.as_view()),
    path(
        f"{_PROVIDERS}<str:version>/download/<str:os>/<str:arch>",
        ProviderDownloadView.as_view(),
    ),
]
