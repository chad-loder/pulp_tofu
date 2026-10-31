"""OpenTofu package hash schemes.

``zh:`` is the hex SHA-256 of the archive file. ``h1:`` is Go's
``golang.org/x/mod/sumdb/dirhash.Hash1`` over the archive's entries, which is what
OpenTofu records for unpacked providers
(``internal/getproviders/hash.go`` in OpenTofu).
"""

import base64
import hashlib
import zipfile
from typing import IO

ZH_PREFIX = "zh:"
H1_PREFIX = "h1:"

_CHUNK = 1 << 20


def zh_hash(sha256_hex: str) -> str:
    """Return the ``zh:`` hash for an archive's hex SHA-256."""
    return ZH_PREFIX + sha256_hex


def h1_hash(archive: IO[bytes]) -> str:
    """Return the ``h1:`` hash of a provider zip archive opened for binary reading.

    Raises:
        zipfile.BadZipFile: ``archive`` is not a zip file.
        ValueError: An entry name contains a newline, which dirhash rejects.
    """
    summary = hashlib.sha256()
    with zipfile.ZipFile(archive) as zf:
        for name in sorted(info.filename for info in zf.infolist()):
            if "\n" in name:
                raise ValueError(f"archive entry name contains a newline: {name!r}")
            entry = hashlib.sha256()
            with zf.open(name) as member:
                while chunk := member.read(_CHUNK):
                    entry.update(chunk)
            summary.update(f"{entry.hexdigest()}  {name}\n".encode())
    return H1_PREFIX + base64.b64encode(summary.digest()).decode()
