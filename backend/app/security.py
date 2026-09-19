from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet


class SecretBox:
    def __init__(self, key_path: Path):
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            temp = key_path.with_suffix(".tmp")
            temp.write_bytes(key)
            os.chmod(temp, 0o600)
            temp.replace(key_path)
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")

