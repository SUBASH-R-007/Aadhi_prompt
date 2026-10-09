"""The app's private scratch root for temporary work directories (``SCRATCH_DIR``).

Work dirs that host housekeeping may sweep later (Manim render dirs, see ``aadhi.manim.sandbox``) are
created inside this root, and the sweeps (``manim.sandbox.sweep_orphans``, ``compose.workspace.sweep_stale``)
look only inside it. They never list the system temp dir itself, so folders of other programs, of another
checkout or of the developer's own runs next to it are never touched, whatever their names.

Default: ``<system temp>/aadhi`` (``aadhi-<uid>`` on POSIX, where the temp dir is shared by every user).
On POSIX the default root is created ``0700`` and must be a real directory owned by this user; a symlink
or a directory of another user there is refused (``UnsafeScratchDir``) instead of used, because another
local user could otherwise swap the work dirs inside it. An explicit ``SCRATCH_DIR`` is the operator's
choice and is only created when missing (the Docker sandbox needs it under the host-shared ``TMPDIR``).
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any


class UnsafeScratchDir(RuntimeError):
    """The default scratch root exists but is not a private directory of this user."""


def _default_name() -> str:
    getuid = getattr(os, "getuid", None)
    return f"aadhi-{getuid()}" if getuid is not None else "aadhi"


def scratch_root(settings: Any = None) -> Path:
    """The scratch root (absolute; not created): ``settings.scratch_dir`` or the default under the temp dir."""
    raw = (getattr(settings, "scratch_dir", "") or "").strip() if settings is not None else ""
    if raw:
        return Path(os.path.abspath(Path(raw).expanduser()))
    return Path(os.path.abspath(Path(tempfile.gettempdir()) / _default_name()))


def _is_default(root: Path) -> bool:
    return root == Path(os.path.abspath(Path(tempfile.gettempdir()) / _default_name()))


def ensure_scratch_root(settings: Any = None) -> Path:
    """The scratch root, created when missing (see the module docstring for the POSIX checks)."""
    root = scratch_root(settings)
    if not _is_default(root):
        root.mkdir(parents=True, exist_ok=True)
        return root
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if os.name != "nt":
        st = os.lstat(root)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise UnsafeScratchDir(f"{root} is not a private directory of this user; set SCRATCH_DIR")
        if stat.S_IMODE(st.st_mode) & 0o077:
            os.chmod(root, 0o700)
    return root


def make_temp_dir(settings: Any, prefix: str) -> Path:
    """A new private directory (``tempfile.mkdtemp``, mode 0700) inside the scratch root."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=ensure_scratch_root(settings)))
