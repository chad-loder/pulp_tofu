"""Single-flight: concurrent callers for the same key share one execution.

When many clients miss the cache for the same provider at once, only the first
request goes upstream; the rest await its result. The work runs as its own task, so a
caller that disconnects does not cancel it for the others. Keys are per event loop
because a task cannot be awaited from another loop.
"""

import asyncio
from collections.abc import Awaitable, Callable, Hashable


class SingleFlight[T]:
    """Deduplicates concurrent awaits of the same keyed work within one process."""

    def __init__(self) -> None:
        self._inflight: dict[tuple[int, Hashable], asyncio.Task[T]] = {}

    async def run(self, key: Hashable, work: Callable[[], Awaitable[T]]) -> T:
        """Await ``work()``, or the identical call already in flight under ``key``."""
        slot = (id(asyncio.get_running_loop()), key)
        task = self._inflight.get(slot)
        if task is None:
            task = asyncio.ensure_future(work())
            self._inflight[slot] = task
            task.add_done_callback(lambda done: self._release(slot, done))
        return await asyncio.shield(task)

    def _release(self, slot: tuple[int, Hashable], done: "asyncio.Task[T]") -> None:
        if self._inflight.get(slot) is done:
            del self._inflight[slot]
        if not done.cancelled():
            done.exception()  # Retrieved, so a failure nobody awaited is not logged as lost.
