"""Django settings for type checking and unit tests outside a Pulp deployment.

pulpcore's settings refuse to load without a database encryption key file. This
module points them at a throwaway key under ``.dev/`` before importing them.
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet

_STATE = Path(__file__).resolve().parent.parent / ".dev"
_KEY = _STATE / "database_fields.symmetric.key"
for directory in ("tmp", "media", "work"):
    (_STATE / directory).mkdir(parents=True, exist_ok=True)
if not _KEY.exists():
    _KEY.write_bytes(Fernet.generate_key())
    _KEY.chmod(0o600)

os.environ.setdefault("PULP_DB_ENCRYPTION_KEY", str(_KEY))
os.environ.setdefault("PULP_FILE_UPLOAD_TEMP_DIR", str(_STATE / "tmp"))
os.environ.setdefault("PULP_MEDIA_ROOT", str(_STATE / "media"))
os.environ.setdefault("PULP_WORKING_DIRECTORY", str(_STATE / "work"))
os.environ.setdefault("PULP_SECRET_KEY", "unit-tests-only")
os.environ.setdefault("PULP_CONTENT_ORIGIN", "https://pulp.example")

from pulpcore.app.settings import *  # noqa: E402, F403, TID251  # pyright: ignore[reportWildcardImportFromLibrary]
