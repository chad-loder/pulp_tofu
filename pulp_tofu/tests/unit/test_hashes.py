import io
import zipfile
from pathlib import Path

import pytest

from pulp_tofu.app import hashes
from pulp_tofu.app.hashes import h1_hash

FIXTURES = Path(__file__).parent / "fixtures"

# From golang.org/x/mod v0.35.0: dirhash.HashZip("provider.zip", dirhash.Hash1).
PROVIDER_ZIP_H1 = "h1:0umXnCzEkKgIH1r2074qqDtGKP8jGPdfKSKTlxG3leE="


def test_h1_hash_matches_go_dirhash() -> None:
    with (FIXTURES / "provider.zip").open("rb") as archive:
        assert h1_hash(archive) == PROVIDER_ZIP_H1


def test_h1_hash_refuses_archives_that_expand_past_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hashes, "MAX_UNPACKED_BYTES", 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros", b"\0" * 4096)
    buffer.seek(0)
    with pytest.raises(ValueError, match="unpacked size"):
        h1_hash(buffer)


def test_h1_hash_rejects_newline_in_name() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("bad\nname", b"")
    buffer.seek(0)
    with pytest.raises(ValueError, match="newline"):
        h1_hash(buffer)
