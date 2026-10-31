"""Verification of provider ``SHA256SUMS`` documents and their detached OpenPGP signatures."""

import re
from collections.abc import Sequence

from pysequoia import Cert, Sig, verify

_SUMS_LINE = re.compile(r"^([0-9a-f]{64}) [ *]?(\S+)$")


class SignatureError(Exception):
    """A ``SHA256SUMS`` document is malformed, unsigned or not signed by an offered key."""


def parse_sha256sums(document: bytes) -> dict[str, str]:
    """Parse a ``sha256sum``-style document into ``{filename: sha256_hex}``.

    Raises:
        SignatureError: A line is malformed or a filename repeats with a different digest.
    """
    try:
        text = document.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SignatureError("SHA256SUMS is not ASCII") from exc
    sums: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _SUMS_LINE.match(line)
        if match is None:
            raise SignatureError(f"SHA256SUMS line {number} is malformed")
        digest, filename = match.group(1), match.group(2)
        if sums.setdefault(filename, digest) != digest:
            raise SignatureError(f"SHA256SUMS lists {filename} twice with different digests")
    return sums


def verify_sha256sums(document: bytes, signature: bytes, armored_keys: Sequence[str]) -> str:
    """Verify a detached signature over ``document`` against any of ``armored_keys``.

    Returns:
        The lowercase hex fingerprint of the certificate that verified the signature.

    Raises:
        SignatureError: No key was offered, a key does not parse, or no key verifies.
    """
    certs: list[Cert] = []
    for armored in armored_keys:
        try:
            certs.extend(Cert.split_bytes(armored.encode()))
        except Exception as exc:  # pysequoia raises bare exceptions for parse failures.
            raise SignatureError("a signing key does not parse as OpenPGP") from exc
    if not certs:
        raise SignatureError("no signing keys offered")
    try:
        sig = Sig.from_bytes(signature)
    except Exception as exc:  # pysequoia raises bare exceptions for parse failures.
        raise SignatureError("the signature does not parse as OpenPGP") from exc

    for cert in certs:
        if _verifies(document, sig, cert):
            return cert.fingerprint
    raise SignatureError("the signature does not verify against the offered keys")


def _verifies(document: bytes, sig: Sig, cert: Cert) -> bool:
    try:
        verify(bytes=document, store=lambda _key_ids: [cert], signature=sig)
    except Exception:  # pysequoia raises bare exceptions for verification failures.
        return False
    return True
