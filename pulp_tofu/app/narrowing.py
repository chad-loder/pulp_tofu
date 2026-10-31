"""Type guards for values deserialized from JSON, task kwargs or DRF.

TypeGuard rather than TypeIs: TypeIs needs Python 3.13. The guards widen the
elements to ``object`` so callers check each one rather than trusting ``Any``.
"""

from typing import TypeGuard


def is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def is_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)
