"""pytest fixtures for pulp_tofu functional tests, registered under ``pytest11``.

They build on pulpcore's own fixtures (``bindings_cfg``, ``gen_object_with_cleanup``,
``monitor_task``) and need the generated ``pulpcore.client.pulp_tofu`` bindings.
"""

import uuid
from collections.abc import Callable, Generator
from typing import Any

import pytest

from pulpcore.tests.functional.utils import BindingsNamespace


@pytest.fixture(scope="session")
def tofu_bindings(_api_client_set: set[Any], bindings_cfg: Any) -> Generator[Any]:  # noqa: ANN401
    """Preconfigured pulp_tofu API clients, e.g. ``tofu_bindings.RemotesTofuApi.list()``."""
    from pulpcore.client import pulp_tofu as tofu_bindings_module

    api_client = tofu_bindings_module.ApiClient(bindings_cfg)
    _api_client_set.add(api_client)
    yield BindingsNamespace(tofu_bindings_module, api_client)
    _api_client_set.remove(api_client)


def _href(value: Any) -> str:  # noqa: ANN401
    return value if isinstance(value, str) else str(value.pulp_href)


@pytest.fixture
def tofu_remote_factory(tofu_bindings: Any, gen_object_with_cleanup: Any) -> Callable[..., Any]:
    """Create a tofu remote, deleted at test cleanup. Defaults to registry.opentofu.org."""

    def factory(**body: Any) -> Any:  # noqa: ANN401
        body.setdefault("name", str(uuid.uuid4()))
        body.setdefault("url", "https://registry.opentofu.org/")
        return gen_object_with_cleanup(tofu_bindings.RemotesTofuApi, body)

    return factory


@pytest.fixture
def tofu_repository_factory(tofu_bindings: Any, gen_object_with_cleanup: Any) -> Callable[..., Any]:
    """Create a tofu repository, deleted at test cleanup."""

    def factory(remote: Any = None, **body: Any) -> Any:  # noqa: ANN401
        body.setdefault("name", str(uuid.uuid4()))
        if remote is not None:
            body["remote"] = _href(remote)
        return gen_object_with_cleanup(tofu_bindings.RepositoriesTofuApi, body)

    return factory


@pytest.fixture
def tofu_distribution_factory(
    tofu_bindings: Any, gen_object_with_cleanup: Any
) -> Callable[..., Any]:
    """Create a tofu distribution, deleted at test cleanup."""

    def factory(remote: Any = None, repository: Any = None, **body: Any) -> Any:  # noqa: ANN401
        name = str(uuid.uuid4())
        body.setdefault("name", name)
        body.setdefault("base_path", name)
        if remote is not None:
            body["remote"] = _href(remote)
        if repository is not None:
            body["repository"] = _href(repository)
        return gen_object_with_cleanup(tofu_bindings.DistributionsTofuApi, body)

    return factory


@pytest.fixture
def tofu_namespace_factory(tofu_bindings: Any, gen_object_with_cleanup: Any) -> Callable[..., Any]:
    """Create a registry namespace, deleted at test cleanup."""

    def factory(name: str, distribution: Any, **body: Any) -> Any:  # noqa: ANN401
        body.update(name=name, distribution=_href(distribution))
        return gen_object_with_cleanup(tofu_bindings.PulpTofuNamespacesApi, body)

    return factory


@pytest.fixture
def tofu_sync(tofu_bindings: Any, monitor_task: Any) -> Callable[..., Any]:
    """Sync a repository and wait; returns the finished task."""

    def sync(repository: Any, remote: Any, providers: list[dict[str, Any]], **body: Any) -> Any:  # noqa: ANN401
        body.update(remote=_href(remote), providers=providers)
        response = tofu_bindings.RepositoriesTofuApi.sync(_href(repository), body)
        return monitor_task(response.task)

    return sync
