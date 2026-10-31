import base64
import hashlib
import io
import zipfile

import pytest

from pulp_tofu.app.hashes import h1_hash, zh_hash


def _zip(entries: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buffer.seek(0)
    return buffer


def test_zh_hash_prefixes_hex_digest() -> None:
    assert zh_hash("ab" * 32) == "zh:" + "ab" * 32


def test_h1_hash_follows_dirhash_hash1() -> None:
    entries = {"terraform-provider-x_v1.0.0": b"binary", "LICENSE": b"text"}
    summary = "".join(
        f"{hashlib.sha256(entries[name]).hexdigest()}  {name}\n" for name in sorted(entries)
    )
    expected = "h1:" + base64.b64encode(hashlib.sha256(summary.encode()).digest()).decode()
    assert h1_hash(_zip(entries)) == expected


def test_h1_hash_ignores_entry_order() -> None:
    a = h1_hash(_zip({"a": b"1", "b": b"2"}))
    b = h1_hash(_zip({"b": b"2", "a": b"1"}))
    assert a == b


def test_h1_hash_rejects_newline_in_name() -> None:
    with pytest.raises(ValueError, match="newline"):
        h1_hash(_zip({"bad\nname": b""}))


def test_h1_hash_rejects_non_zip() -> None:
    with pytest.raises(zipfile.BadZipFile):
        h1_hash(io.BytesIO(b"not a zip"))
