"""Bearer tokens for the registry: OpenTofu's ``TF_TOKEN_<host>``, sent on API requests.

A token reads ``tofu_<prefix>_<secret>``. The prefix finds the row and the secret's
SHA-256 digest is compared in constant time. Requests carrying any other credential are
left to the next authenticator, so pulpcore's own authentication, including Workload
Identity where pulpcore configures it, works beside this class.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import override

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.module_loading import import_string
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request
from rest_framework.settings import api_settings

from .models import TofuToken

TOKEN_PREFIX = "tofu_"
# last_used_at is written at most this often per token.
LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class IssuedToken:
    """A new token: the string shown once, and the public prefix and digest to store."""

    token: str
    prefix: str
    digest: str

    @override
    def __repr__(self) -> str:
        return f"IssuedToken(prefix={self.prefix!r}, token='[redacted]')"


def issue_token() -> IssuedToken:
    """A fresh token string with its stored prefix and digest."""
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return IssuedToken(
        token=f"{TOKEN_PREFIX}{prefix}_{secret}", prefix=prefix, digest=_digest(secret)
    )


class TofuTokenAuthentication(BaseAuthentication):
    """Authenticate ``Authorization: Bearer tofu_...`` as the token's user.

    ``request.auth`` is the ``TofuToken``, which the registry views use to narrow the
    user's permissions to the token's namespaces and scope.
    """

    @override
    def authenticate(self, request: Request) -> tuple[AbstractBaseUser, TofuToken] | None:
        # As DRF's own TokenAuthentication reads the header.
        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            return None
        try:
            credential = parts[1].decode()
        except UnicodeDecodeError:
            return None
        if not credential.startswith(TOKEN_PREFIX):
            return None
        prefix, _, secret = credential.removeprefix(TOKEN_PREFIX).partition("_")
        token = TofuToken.objects.select_related("user").filter(prefix=prefix).first()
        if token is None or not hmac.compare_digest(token.digest, _digest(secret)):
            raise AuthenticationFailed("Invalid token.")
        now = timezone.now()
        if token.expires_at is not None and token.expires_at <= now:
            raise AuthenticationFailed("Token expired.")
        if not token.user.is_active:
            raise AuthenticationFailed("User inactive.")
        if token.last_used_at is None or now - token.last_used_at > LAST_USED_RESOLUTION:
            TofuToken.objects.filter(pk=token.pk).update(last_used_at=now)
        return token.user, token

    @override
    def authenticate_header(self, request: Request) -> str:
        # A WWW-Authenticate value makes DRF answer 401, not 403, to a missing token.
        return "Bearer"


def registry_authentication_classes() -> list[type[BaseAuthentication]]:
    """``TOFU_AUTHENTICATION_CLASSES``, then pulpcore's ``DEFAULT_AUTHENTICATION_CLASSES``."""
    extra: list[object] = [import_string(path) for path in settings.TOFU_AUTHENTICATION_CLASSES]
    defaults: list[object] = list(api_settings.DEFAULT_AUTHENTICATION_CLASSES)
    return [_authentication_class(c) for c in (*extra, *defaults)]


def _authentication_class(value: object) -> type[BaseAuthentication]:
    if not (isinstance(value, type) and issubclass(value, BaseAuthentication)):
        raise ImproperlyConfigured(f"{value!r} is not a DRF authentication class")
    return value


def registry_authenticators() -> list[BaseAuthentication]:
    """Instances of ``registry_authentication_classes``."""
    return [cls() for cls in registry_authentication_classes()]


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
