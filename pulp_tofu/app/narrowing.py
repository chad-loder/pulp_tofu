"""Type guards for values deserialized from JSON, task kwargs or DRF.

TypeGuard rather than TypeIs: TypeIs needs Python 3.13. The guards type the
elements as ``object``, so the checker makes callers narrow each one.
"""

from typing import TypeGuard


def is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def is_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)
