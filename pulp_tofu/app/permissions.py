"""Permission checks outside access policies, at the levels pulpcore's conditions use."""

from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Model

from pulpcore.plugin.util import get_domain

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser, AnonymousUser


def has_any_level_perm(
    user: "AbstractUser | AnonymousUser | None", permission: str, obj: Model
) -> bool:
    """Whether ``user`` holds ``permission`` globally, on the current domain, or on ``obj``.

    The same levels as pulpcore's ``has_model_or_domain_or_obj_perms``. pulpcore's
    backend answers ``has_perm(permission, obj)`` from object roles only, so a check
    on ``obj`` alone refuses users whose role is global or on the domain.
    """
    if user is None:
        return False
    if user.has_perm(permission):
        return True
    if settings.DOMAIN_ENABLED and user.has_perm(permission, get_domain()):
        return True
    return user.has_perm(permission, obj)
