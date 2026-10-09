"""Local filesystem storage (dev / single box). Public keys are served by the API at ``/media``."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .base import validate_key


class LocalStorage:
    name = "local"

    def __init__(self, root: Path, url_prefix: str = "/media") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.url_prefix = url_prefix.rstrip("/")

    def _path(self, key: str) -> Path:
        validate_key(key)
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError(f"storage key escapes root: {key!r}")
        return p

    def _atomic_write(self, p: Path, writer) -> None:
        if p.exists():
            raise FileExistsError(f"refusing to overwrite storage object {p.name}")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                writer(f)
            os.replace(tmp, p)  # atomic: readers never see partial files
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._atomic_write(self._path(key), lambda f: f.write(data))

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        def copy(f):
            with open(path, "rb") as src:
                shutil.copyfileobj(src, f, length=1024 * 1024)

        self._atomic_write(self._path(key), copy)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def download_to(self, key: str, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), path)
        return path

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def public_url(self, key: str) -> str:
        validate_key(key)
        return f"{self.url_prefix}/{key}"

    def signed_url(self, key: str, ttl_seconds: int, *, download_name: str | None = None) -> str:
        return self.public_url(key)

    def local_path(self, key: str) -> Path | None:
        return self._path(key)
