"""Verification of provider ``SHA256SUMS`` documents and their detached OpenPGP signatures."""

import re
from collections.abc import Sequence
from typing import NewType

from pysequoia import Cert, Sig, verify

from .hashes import Sha256Hex, parse_sha256

# Lowercase hex fingerprint of an OpenPGP certificate's primary key.
Fingerprint = NewType("Fingerprint", str)

_SUMS_LINE = re.compile(r"^([0-9a-f]{64}) [ *]?(\S+)$")
# Archive names become stored paths and archive URLs, so only plain names are kept.
ARCHIVE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class SignatureError(Exception):
    """A ``SHA256SUMS`` document is malformed, unsigned or not signed by an offered key."""


def parse_sha256sums(document: bytes) -> dict[str, Sha256Hex]:
    """Parse a ``sha256sum``-style document into ``{filename: sha256}``.

    Lines naming anything but a plain archive filename are skipped.

    Raises:
        SignatureError: A line is malformed or a filename repeats with a different digest.
    """
    try:
        text = document.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SignatureError("SHA256SUMS is not ASCII") from exc
    sums: dict[str, Sha256Hex] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _SUMS_LINE.match(line)
        digest = parse_sha256(match.group(1)) if match else None
        if match is None or digest is None:
            raise SignatureError(f"SHA256SUMS line {number} is malformed")
        filename = match.group(2)
        if not ARCHIVE_NAME.match(filename):
            continue
        if sums.setdefault(filename, digest) != digest:
            raise SignatureError(f"SHA256SUMS lists {filename} twice with different digests")
    return sums


def verify_sha256sums(
    document: bytes,
    signature: bytes,
    armored_keys: Sequence[str],
    *,
    trusted: frozenset[Fingerprint] = frozenset(),
) -> Fingerprint:
    """Verify a detached signature over ``document`` against any of ``armored_keys``.

    A non-empty ``trusted`` admits only certificates with those fingerprints, whatever
    keys the registry offers.

    Returns:
        The fingerprint of the certificate that verified the signature.

    Raises:
        SignatureError: No usable key was offered, or no key verifies.
    """
    certs: list[Cert] = []
    for armored in armored_keys:
        try:
            certs.extend(Cert.split_bytes(armored.encode()))
        except Exception as exc:  # pysequoia raises bare exceptions for parse failures.
            raise SignatureError("a signing key does not parse as OpenPGP") from exc
    if trusted:
        certs = [cert for cert in certs if Fingerprint(cert.fingerprint) in trusted]
        if not certs:
            raise SignatureError("none of the offered signing keys is a trusted key")
    if not certs:
        raise SignatureError("no signing keys offered")
    try:
        sig = Sig.from_bytes(signature)
    except Exception as exc:  # pysequoia raises bare exceptions for parse failures.
        raise SignatureError("the signature does not parse as OpenPGP") from exc

    for cert in certs:
        if _verifies(document, sig, cert):
            return Fingerprint(cert.fingerprint)
    raise SignatureError("the signature does not verify against the offered keys")


def parse_fingerprint(value: str) -> Fingerprint | None:
    """``value`` as a 40- or 64-digit hex fingerprint, lowercased, or None."""
    compact = value.replace(" ", "").lower()
    if len(compact) in (40, 64) and all(c in "0123456789abcdef" for c in compact):
        return Fingerprint(compact)
    return None


def _verifies(document: bytes, sig: Sig, cert: Cert) -> bool:
    def store(_key_ids: list[str]) -> list[Cert]:
        return [cert]

    try:
        verify(bytes=document, store=store, signature=sig)
    except Exception:  # pysequoia raises bare exceptions for verification failures.
        return False
    return True
