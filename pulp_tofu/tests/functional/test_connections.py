import time

import pytest

from .conftest import Mirror

pytestmark = pytest.mark.parallel

# A silent drop costs one read timeout; the drop case shortens it on the remote.
DROP_READ_TIMEOUT = 3.0
DROP_BUDGET_SECONDS = DROP_READ_TIMEOUT + 5.0
CLOSE_BUDGET_SECONDS = 3.0


@pytest.mark.parametrize(
    ("mode", "budget"),
    [("fin", CLOSE_BUDGET_SECONDS), ("rst", CLOSE_BUDGET_SECONDS), ("drop", DROP_BUDGET_SECONDS)],
)
def test_pooled_connections_reaped_while_idle_are_replaced(
    mirror: Mirror, mode: str, budget: float
) -> None:
    if mode == "drop":
        mirror.update_remote(sock_read_timeout=DROP_READ_TIMEOUT)
    state = mirror.registry.state
    state.idle_reap = mode  # type: ignore[assignment]
    state.idle_reap_after = 1.0
    assert mirror.get("index.json").status == 200
    opened = state.connections

    time.sleep(state.idle_reap_after + 1.5)
    started = time.monotonic()
    response = mirror.get("1.0.0.json")
    elapsed = time.monotonic() - started

    assert response.status == 200, response.body
    assert elapsed < budget, f"{mode}: {elapsed:.1f}s"
    assert state.connections > opened


def test_kept_alive_connections_are_reused(mirror: Mirror) -> None:
    state = mirror.registry.state
    assert mirror.get("index.json").status == 200
    opened, requested = state.connections, len(state.requests)
    assert mirror.get("1.0.0.json").status == 200
    assert mirror.get("1.1.0.json").status == 200
    # Each content-app worker keeps its own pool, so only the ratio is portable: without
    # reuse every upstream request would open a connection.
    assert state.connections - opened < len(state.requests) - requested
