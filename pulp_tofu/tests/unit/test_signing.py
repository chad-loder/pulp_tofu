from pathlib import Path

import pytest
from pysequoia import SignatureMode, Tsk, sign

from pulp_tofu.app.signing import (
    Fingerprint,
    SignatureError,
    parse_sha256sums,
    verify_sha256sums,
)

# terraform-provider-random 3.7.2 as published through registry.opentofu.org.
RANDOM = Path(__file__).parent / "fixtures" / "random_3.7.2"
RANDOM_FINGERPRINT = "e3e6e43d84cb852eadb0051d0c0af313e5fd9f80"
RANDOM_LINUX_AMD64 = "9b0ac4c1d8e36a86b59ced94fa517ae9b015b1d044b3455465cc6f0eab70915d"


@pytest.fixture
def published() -> tuple[bytes, bytes, str]:
    return (
        (RANDOM / "SHA256SUMS").read_bytes(),
        (RANDOM / "SHA256SUMS.sig").read_bytes(),
        (RANDOM / "key.asc").read_text(),
    )


def test_verifies_published_provider(published: tuple[bytes, bytes, str]) -> None:
    sums, sig, key = published
    assert verify_sha256sums(sums, sig, [key]) == RANDOM_FINGERPRINT


def test_rejects_tampered_sums(published: tuple[bytes, bytes, str]) -> None:
    sums, sig, key = published
    with pytest.raises(SignatureError):
        verify_sha256sums(sums.replace(b"9b0a", b"0000"), sig, [key])


def test_names_the_key_that_signed(published: tuple[bytes, bytes, str]) -> None:
    sums, sig, key = published
    other = str(Tsk.generate("other@example.com").extract_certificate())
    assert verify_sha256sums(sums, sig, [other, key]) == RANDOM_FINGERPRINT


def test_rejects_wrong_key(published: tuple[bytes, bytes, str]) -> None:
    sums, sig, _ = published
    other = str(Tsk.generate("other@example.com").extract_certificate())
    with pytest.raises(SignatureError):
        verify_sha256sums(sums, sig, [other])


def test_rejects_no_keys(published: tuple[bytes, bytes, str]) -> None:
    sums, sig, _ = published
    with pytest.raises(SignatureError, match="no signing keys"):
        verify_sha256sums(sums, sig, [])


def test_verifies_binary_detached_signature() -> None:
    key = Tsk.generate("signer@example.com")
    cert = key.extract_certificate()
    document = b"%s  p.zip\n" % (b"a" * 64)
    signature = sign(key.signer(), document, mode=SignatureMode.DETACHED, armor=False)
    assert verify_sha256sums(document, signature, [str(cert)]) == cert.fingerprint


def test_parse_published_sums(published: tuple[bytes, bytes, str]) -> None:
    sums = parse_sha256sums(published[0])
    assert sums["terraform-provider-random_3.7.2_linux_amd64.zip"] == RANDOM_LINUX_AMD64
    assert len(sums) == 10


def test_parse_skips_names_that_are_not_plain_archive_names() -> None:
    sums = parse_sha256sums(b"%s  ../p.zip\n%s  ok.zip\n" % (b"a" * 64, b"b" * 64))
    assert list(sums) == ["ok.zip"]


def test_trusted_fingerprints_exclude_other_offered_keys(
    published: tuple[bytes, bytes, str],
) -> None:
    sums, sig, key = published
    other = Fingerprint(Tsk.generate("other@example.com").extract_certificate().fingerprint)
    with pytest.raises(SignatureError, match="trusted"):
        verify_sha256sums(sums, sig, [key], trusted=frozenset({other}))
    trusted = frozenset({Fingerprint(RANDOM_FINGERPRINT)})
    assert verify_sha256sums(sums, sig, [key], trusted=trusted) == RANDOM_FINGERPRINT


def test_parse_rejects_malformed_line() -> None:
    with pytest.raises(SignatureError, match="line 1"):
        parse_sha256sums(b"nothex  p.zip\n")


def test_parse_rejects_conflicting_duplicate() -> None:
    with pytest.raises(SignatureError, match="twice"):
        parse_sha256sums(b"%s  p.zip\n%s  p.zip\n" % (b"a" * 64, b"b" * 64))
