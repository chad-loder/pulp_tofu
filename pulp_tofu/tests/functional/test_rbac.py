"""Access policies and roles, per action and per role, as each user sees them."""

import secrets
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from pulpcore.client.pulp_tofu.exceptions import ApiException

pytestmark = pytest.mark.parallel


def _attempt(user: Any, api: Any, action: str, *args: Any) -> tuple[int, Any]:  # noqa: ANN401
    """Call ``action`` as ``user``; its status and data."""
    with user:
        try:
            response = getattr(api, f"{action}_with_http_info")(*args)
        except ApiException as error:
            return error.status, None
    return response.status_code, response.data


def _remote_body() -> dict[str, str]:
    return {"name": str(uuid.uuid4()), "url": "https://registry.opentofu.org/"}


def _repository_body() -> dict[str, str]:
    return {"name": str(uuid.uuid4())}


def _distribution_body() -> dict[str, str]:
    name = str(uuid.uuid4())
    return {"name": name, "base_path": name}


@pytest.mark.parametrize(
    ("model", "api_name", "body", "asynchronous_create"),
    [
        ("tofuremote", "RemotesTofuApi", _remote_body, False),
        ("tofurepository", "RepositoriesTofuApi", _repository_body, False),
        ("tofudistribution", "DistributionsTofuApi", _distribution_body, True),
    ],
)
def test_viewer_creator_and_stranger(
    model: str,
    api_name: str,
    body: Callable[[], dict[str, str]],
    asynchronous_create: bool,
    gen_user: Callable[..., Any],
    tofu_bindings: Any,
    monitor_task: Callable[[str], Any],
) -> None:
    api = getattr(tofu_bindings, api_name)
    viewer = gen_user(model_roles=[f"tofu.{model}_viewer"])
    creator = gen_user(model_roles=[f"tofu.{model}_creator"])
    stranger = gen_user()

    assert _attempt(viewer, api, "create", body())[0] == 403
    assert _attempt(stranger, api, "create", body())[0] == 403
    status, created = _attempt(creator, api, "create", body())
    assert status == (202 if asynchronous_create else 201)
    href = (
        monitor_task(created.task).created_resources[0]
        if asynchronous_create
        else created.pulp_href
    )

    # The creator owns what it creates; a viewer sees everything; a stranger, nothing.
    assert _attempt(viewer, api, "read", href)[0] == 200
    assert _attempt(creator, api, "read", href)[0] == 200
    assert _attempt(stranger, api, "read", href)[0] == 404
    assert _attempt(stranger, api, "list")[1].count == 0

    rename = {"name": str(uuid.uuid4())}
    assert _attempt(viewer, api, "partial_update", href, rename)[0] == 403
    assert _attempt(stranger, api, "partial_update", href, rename)[0] == 404
    assert _attempt(creator, api, "partial_update", href, rename)[0] == 202

    assert _attempt(viewer, api, "delete", href)[0] == 403
    assert _attempt(stranger, api, "delete", href)[0] == 404
    status, deleted = _attempt(creator, api, "delete", href)
    assert status == 202
    monitor_task(deleted.task)


@pytest.fixture
def namespace(
    tofu_repository_factory: Callable[..., Any],
    tofu_distribution_factory: Callable[..., Any],
    tofu_namespace_factory: Callable[..., Any],
) -> Any:  # noqa: ANN401
    distribution = tofu_distribution_factory(
        repository=tofu_repository_factory(), allow_uploads=True
    )
    return tofu_namespace_factory(f"rbac{secrets.token_hex(4)}", distribution, private=True)


def test_namespace_roles(
    namespace: Any,
    gen_user: Callable[..., Any],
    tofu_bindings: Any,
) -> None:
    api = tofu_bindings.PulpTofuNamespacesApi
    consumer = gen_user(object_roles=[("tofu.tofunamespace_consumer", namespace.pulp_href)])
    collaborator = gen_user(object_roles=[("tofu.tofunamespace_collaborator", namespace.pulp_href)])
    creator = gen_user(model_roles=["tofu.tofunamespace_creator"])
    stranger = gen_user()

    assert _attempt(consumer, api, "read", namespace.pulp_href)[0] == 200
    assert _attempt(stranger, api, "read", namespace.pulp_href)[0] == 404
    assert (
        _attempt(consumer, api, "partial_update", namespace.pulp_href, {"private": False})[0] == 403
    )
    assert (
        _attempt(collaborator, api, "partial_update", namespace.pulp_href, {"private": False})[0]
        == 403
    )

    # Publishing is checked before the upload: a consumer is refused, a collaborator gets
    # as far as validating the empty request.
    empty: dict[str, str] = {"name": "vpc", "system": "aws", "version": "1.0.0"}
    with consumer, pytest.raises(ApiException) as refused:
        api.publish_module(namespace.pulp_href, **empty)
    assert refused.value.status == 403
    with collaborator, pytest.raises(ApiException) as invalid:
        api.publish_module(namespace.pulp_href, **empty)
    assert invalid.value.status == 400

    status, created = _attempt(creator, api, "create", {"name": f"own{secrets.token_hex(4)}"})
    assert status == 201
    assert _attempt(creator, api, "partial_update", created.pulp_href, {"private": True})[0] == 200
    assert _attempt(creator, api, "delete", created.pulp_href)[0] == 204


def test_token_edges(
    namespace: Any,
    gen_user: Callable[..., Any],
    tofu_bindings: Any,
) -> None:
    api = tofu_bindings.PulpTofuTokensApi
    body = {"name": "ci", "scope": "read", "namespaces": [namespace.pulp_href]}
    reader = gen_user(object_roles=[("tofu.tofunamespace_consumer", namespace.pulp_href)])
    stranger = gen_user()

    # A token cannot name a namespace its user cannot see.
    assert _attempt(stranger, api, "create", body)[0] == 400
    status, token = _attempt(reader, api, "create", body)
    assert status == 201
    assert token.token

    # Users see and delete only their own tokens, and the secret is never shown again.
    assert _attempt(stranger, api, "list")[1].count == 0
    assert _attempt(stranger, api, "read", token.pulp_href)[0] == 404
    assert _attempt(stranger, api, "delete", token.pulp_href)[0] == 404
    status, shown = _attempt(reader, api, "read", token.pulp_href)
    assert (status, shown.token) == (200, None)
    assert _attempt(reader, api, "delete", token.pulp_href)[0] == 204
