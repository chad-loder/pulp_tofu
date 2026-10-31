"""OpenTofu package hash schemes.

``zh:`` is the hex SHA-256 of the archive file. ``h1:`` is Go's
``golang.org/x/mod/sumdb/dirhash.Hash1`` over the archive's entries, which is what
OpenTofu records for unpacked providers
(``internal/getproviders/hash.go`` in OpenTofu).
"""

import base64
import hashlib
import zipfile
from string import hexdigits
from typing import IO, NewType

ZH_PREFIX = "zh:"
H1_PREFIX = "h1:"

# A lowercase hex SHA-256 digest; ``parse_sha256`` is the only way to make one from text.
Sha256Hex = NewType("Sha256Hex", str)
ZhHash = NewType("ZhHash", str)
H1Hash = NewType("H1Hash", str)

# Large providers unpack to under 1 GiB; four times that leaves room without inviting abuse.
MAX_UNPACKED_BYTES = 4 << 30
MAX_ENTRIES = 10_000

_CHUNK = 1 << 20
_HEX = frozenset(hexdigits.lower())


def parse_sha256(value: str) -> Sha256Hex | None:
    """``value`` as a SHA-256 hex digest, lowercased, or None if it is not one."""
    lowered = value.lower()
    if len(lowered) == 64 and set(lowered) <= _HEX:
        return Sha256Hex(lowered)
    return None


def zh_hash(sha256: Sha256Hex) -> ZhHash:
    """Return the ``zh:`` hash for an archive's SHA-256."""
    return ZhHash(ZH_PREFIX + sha256)


def h1_hash(archive: IO[bytes]) -> H1Hash:
    """Return the ``h1:`` hash of a provider zip archive opened for binary reading.

    The archive's checksum is signed, but its contents are still upstream data: a zip
    that expands past ``MAX_UNPACKED_BYTES`` or holds more than ``MAX_ENTRIES`` entries
    is refused. Bytes are counted as they are read, so a lying size header cannot pass.

    Raises:
        zipfile.BadZipFile: ``archive`` is not a zip file.
        ValueError: An entry name contains a newline, which dirhash rejects, or the
            archive exceeds the size or entry limits.
    """
    summary = hashlib.sha256()
    unpacked = 0
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError(f"archive holds {len(infos)} entries, over {MAX_ENTRIES}")
        if sum(info.file_size for info in infos) > MAX_UNPACKED_BYTES:
            raise ValueError("archive expands past the unpacked size limit")
        for name in sorted(info.filename for info in infos):
            if "\n" in name:
                raise ValueError(f"archive entry name contains a newline: {name!r}")
            entry = hashlib.sha256()
            with zf.open(name) as member:
                while chunk := member.read(_CHUNK):
                    unpacked += len(chunk)
                    if unpacked > MAX_UNPACKED_BYTES:
                        raise ValueError("archive expands past the unpacked size limit")
                    entry.update(chunk)
            summary.update(f"{entry.hexdigest()}  {name}\n".encode())
    return H1Hash(H1_PREFIX + base64.b64encode(summary.digest()).decode())
