"""File storage for server-managed files (video exports, library assets).

A LocalStorage is one root directory whose files are addressed by server-generated keys such as
"1/<export-id>.webm" or "blobs/ab/<sha256>.png". It is the only place that turns keys into
filesystem paths, and it refuses keys that would escape its root. Callers deal in keys, so the
files can later move to persistent or object storage by replacing this class; today everything
is on the local disk (no object-storage provider is configured in this project).
"""
import hashlib
import os
import shutil


class StorageError(ValueError):
    """A key that is malformed or points outside the storage root."""


class LocalStorage:
    def __init__(self, root):
        self.root = os.path.abspath(root)

    def path(self, key):
        if not key or not isinstance(key, str) or os.path.isabs(key) or "\\" in key or ":" in key:
            raise StorageError(f"invalid storage key: {key!r}")
        parts = key.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise StorageError(f"invalid storage key: {key!r}")
        full = os.path.abspath(os.path.join(self.root, *parts))
        if os.path.commonpath([full, self.root]) != self.root:
            raise StorageError(f"storage key escapes its root: {key!r}")
        return full

    def exists(self, key):
        return os.path.isfile(self.path(key))

    def size(self, key):
        return os.path.getsize(self.path(key))

    def mtime(self, key):
        return os.path.getmtime(self.path(key))

    def put_file(self, key, source, move=False):
        """Stores a local file under `key`; atomic for moves within the same disk."""
        target = self.path(key)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if move:
            os.replace(source, target)
        else:
            partial = target + ".partial"
            shutil.copyfile(source, partial)
            os.replace(partial, target)
        return target

    def delete(self, key):
        path = self.path(key)
        if os.path.isfile(path):
            os.remove(path)

    def list(self, prefix=""):
        """Keys of the files directly inside the folder `prefix` (e.g. "tmp")."""
        folder = self.path(prefix) if prefix else self.root
        if not os.path.isdir(folder):
            return []
        return [f"{prefix}/{name}" if prefix else name for name in sorted(os.listdir(folder))
                if os.path.isfile(os.path.join(folder, name))]


def sha256_file(path, chunk_size=1024 * 1024):
    """Content hash, streamed: large videos are never loaded into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()
