# ruff: noqa: E402
"""Aadhi sandbox hardening: appended after the runtime, before the scene code.

Like ``scene_runtime.py`` this file is NEVER imported by the application; ``aadhi.manim.aadhi_scene``
appends it as text when ``assemble_script`` is given a hardening config (the ``_AADHI_HARDEN``
header line). It runs inside the render process before any scene code and is defence in depth on
top of the AST guard and the OS/container boundary. It never changes what is drawn, so it is not
part of the render cache key (``render.source_revision`` hashes ``scene_runtime.py`` only).

* frame budget and profile lock (``frame_limits``): ``SceneFileWriter.write_frame`` counts frames
  against ``ceil((total_duration * 1.5 + 10) * fps)`` and refuses frames once the resolution or
  frame rate differs from the profile the render started with (or exceeds the hard maximum);
  refusals print ``[AADHI_LIMIT] frames`` / ``[AADHI_LIMIT] profile`` and raise ``RuntimeError``;
* stats: ``[AADHI_STATS] frames=N`` is printed when the process exits;
* audit hook (``audit_hook``, ``sys.addaudithook``; it cannot be removed once added): an allow-list
  for operations. File writes are allowed only under the work dir, the Manim media dir and the temp
  dir; file reads and directory listings also under the Python installation and ``sys.path``.
  Process creation (``subprocess.Popen``, and ``os.posix_spawn``, which subprocess itself uses on Python
  3.13+) is allowed only for the TeX programs, by absolute path (bare names are resolved once, before
  scene code runs). Sockets, ctypes, the registry and the web browser are refused.
  Every refusal prints ``[AADHI_BLOCKED] <event>`` and raises ``PermissionError``.

Limits: native code that opens files itself (fonts in Pango, PyAV, cairo) is not audited. The
container runner (``MANIM_SANDBOX=docker``) remains the real isolation boundary.
"""

import atexit as _aadhi_h_atexit
import math as _aadhi_h_math
import os as _aadhi_h_os
import shutil as _aadhi_h_shutil
import subprocess as _aadhi_h_subprocess
import sys as _aadhi_h_sys
import tempfile as _aadhi_h_tempfile
import threading as _aadhi_h_threading

_AADHI_H = dict(globals().get("_AADHI_HARDEN") or {})
_AADHI_H_STATE = {"frames": 0, "blocked": 0}
# Hard maximum for any render profile: the largest output is the 1080x1350 panel / 1920x1080 frame.
_AADHI_H_MAX_SIDE = 1920
_AADHI_H_MAX_PIXELS = 1920 * 1350
_AADHI_H_MAX_FPS = 60
_AADHI_H_BUDGET_FACTOR = 1.5
_AADHI_H_BUDGET_EXTRA_SECONDS = 10.0


def _aadhi_h_say(kind, message):
    try:
        _aadhi_h_sys.stderr.write(f"\n[AADHI_{kind}] {message}\n")
        _aadhi_h_sys.stderr.flush()
    except Exception:  # pragma: no cover - reporting must never fail the render
        pass


def _aadhi_h_print_stats():
    _aadhi_h_say("STATS", f"frames={int(_AADHI_H_STATE['frames'])}")


# --- frame budget and profile lock ---------------------------------------------------------------


def _aadhi_h_profile_problem(width, height, fps):
    if width <= 0 or height <= 0 or fps <= 0:
        return "the render profile is invalid"
    if max(width, height) > _AADHI_H_MAX_SIDE or width * height > _AADHI_H_MAX_PIXELS:
        return f"the resolution {width}x{height} is above the allowed maximum"
    if fps > _AADHI_H_MAX_FPS:
        return f"the frame rate {fps:g} fps is above the allowed maximum of {_AADHI_H_MAX_FPS} fps"
    return ""


def _aadhi_h_frame_budget(total_duration, fps):
    seconds = max(float(total_duration), 0.0) * _AADHI_H_BUDGET_FACTOR + _AADHI_H_BUDGET_EXTRA_SECONDS
    return int(_aadhi_h_math.ceil(seconds * float(fps)))


def _aadhi_h_install_limits(spec):
    import manim.scene.scene_file_writer as _sfw
    from manim import config as _config

    locked = (int(_config.pixel_width), int(_config.pixel_height), float(_config.frame_rate))
    problem = _aadhi_h_profile_problem(*locked)
    if problem:
        _aadhi_h_say("LIMIT", "profile")
        raise RuntimeError(problem)
    max_frames = int(spec.get("max_frames") or _aadhi_h_frame_budget(spec.get("total_duration", 0.0), locked[2]))
    total = float(spec.get("total_duration", 0.0))
    original = _sfw.SceneFileWriter.write_frame

    def write_frame(self, frame_or_renderer, num_frames=1, *args, **kwargs):
        _AADHI_H_STATE["frames"] += max(int(num_frames), 0)
        if _AADHI_H_STATE["frames"] > max_frames:
            _aadhi_h_say("LIMIT", "frames")
            raise RuntimeError(
                f"the animation runs far past the scene length of {total:g} s (more than {max_frames} frames); "
                "keep waits short and end with self.finish()"
            )
        current = (int(_config.pixel_width), int(_config.pixel_height), float(_config.frame_rate))
        shape = getattr(frame_or_renderer, "shape", None)
        if current != locked or (shape is not None and tuple(shape[:2]) != (locked[1], locked[0])):
            _aadhi_h_say("LIMIT", "profile")
            raise RuntimeError("the scene changed the resolution or frame rate while rendering; this is not allowed")
        return original(self, frame_or_renderer, num_frames, *args, **kwargs)

    write_frame._aadhi_original = original
    _sfw.SceneFileWriter.write_frame = write_frame
    return max_frames


# --- audit hook ----------------------------------------------------------------------------------

_AADHI_H_DENY_PREFIXES = ("socket.", "ctypes.", "winreg.", "webbrowser.")
_AADHI_H_DENY_EVENTS = frozenset(
    {
        "os.system", "os.exec", "os.spawn", "os.fork", "os.forkpty", "os.kill", "os.killpg",
        "os.startfile", "os.startfile/2", "pty.spawn", "os.link", "os.symlink", "_winapi.OpenProcess",
        "_winapi.CreateJunction",
    }
)
# event -> indexes of path arguments that must lie under the write roots / the read roots.
_AADHI_H_WRITE_EVENTS = {
    "os.mkdir": (0,), "os.rmdir": (0,), "os.remove": (0,), "os.chmod": (0,), "os.chown": (0,), "os.chflags": (0,),
    "os.utime": (0,), "os.truncate": (0,), "os.setxattr": (0,), "os.removexattr": (0,), "os.rename": (0, 1),
    "shutil.rmtree": (0,), "shutil.move": (0, 1), "shutil.copytree": (1,), "shutil.copyfile": (1,),
    "shutil.copymode": (1,), "shutil.copystat": (1,), "shutil.chown": (0,), "shutil.unpack_archive": (1,),
    "shutil.make_archive": (0,), "tempfile.mkstemp": (0,), "tempfile.mkdtemp": (0,), "_winapi.CreateFile": (0,),
}
_AADHI_H_READ_EVENTS = {
    "os.listdir": (0,), "os.scandir": (0,), "os.chdir": (0,), "os.listxattr": (0,), "os.getxattr": (0,),
    "shutil.copytree": (0,), "shutil.copyfile": (0,), "shutil.unpack_archive": (0,),
}
_AADHI_H_WRITE_FLAGS = (
    getattr(_aadhi_h_os, "O_WRONLY", 0) | getattr(_aadhi_h_os, "O_RDWR", 0) | getattr(_aadhi_h_os, "O_APPEND", 0)
    | getattr(_aadhi_h_os, "O_CREAT", 0) | getattr(_aadhi_h_os, "O_TRUNC", 0)
)
_AADHI_H_DEVNULL = frozenset({_aadhi_h_os.devnull.lower(), "nul", "/dev/null", "\\\\.\\nul"})


def _aadhi_h_norm(path):
    return _aadhi_h_os.path.normcase(_aadhi_h_os.path.realpath(_aadhi_h_os.fsdecode(path)))


def _aadhi_h_roots(paths):
    roots = []
    for p in paths:
        try:
            if p and _aadhi_h_os.path.isabs(_aadhi_h_os.fsdecode(p)):
                root = _aadhi_h_norm(p)
                if root not in roots:
                    roots.append(root)
        except (TypeError, ValueError, OSError):
            continue
    return tuple(roots)


def _aadhi_h_make_policy(spec):
    """Roots and programs, computed once from the trusted state before any scene code runs."""
    cwd = _aadhi_h_os.getcwd()
    write = [cwd, _aadhi_h_tempfile.gettempdir()]
    if "manim" in _aadhi_h_sys.modules:
        from manim import config as _config

        write.append(_aadhi_h_os.path.abspath(str(_config.media_dir)))
        for key in ("text_dir", "tex_dir"):
            try:
                write.append(_aadhi_h_os.path.abspath(str(_config.get_dir(key))))
            except Exception:
                pass
    write += [_aadhi_h_os.path.abspath(p) for p in spec.get("write_roots", ())]
    read = list(write)
    read += [_aadhi_h_sys.prefix, _aadhi_h_sys.base_prefix, _aadhi_h_sys.exec_prefix,
             getattr(_aadhi_h_sys, "base_exec_prefix", "")]
    read += [p for p in _aadhi_h_sys.path if isinstance(p, str)]
    if _aadhi_h_os.name != "nt":
        read.append("/usr/share")
    read += [_aadhi_h_os.path.abspath(p) for p in spec.get("read_roots", ())]
    programs = {}
    for name in spec.get("programs", ()):
        found = _aadhi_h_shutil.which(str(name))
        if found:
            full = _aadhi_h_os.path.abspath(found)
            programs[_aadhi_h_os.path.normcase(full)] = full
    return {"write": _aadhi_h_roots(write), "read": _aadhi_h_roots(read), "programs": programs,
            "names": {str(n).lower() for n in spec.get("programs", ())}}


def _aadhi_h_inside(path, roots):
    try:
        full = _aadhi_h_norm(path)
    except (TypeError, ValueError, OSError):
        return False
    return any(full == root or full.startswith(root.rstrip(_aadhi_h_os.sep) + _aadhi_h_os.sep) for root in roots)


def _aadhi_h_path_ok(arg, roots):
    if arg is None or isinstance(arg, int):
        return True  # an already open file descriptor, or "no path"
    try:
        text = _aadhi_h_os.fsdecode(arg)
    except TypeError:
        return False
    if text.lower() in _AADHI_H_DEVNULL:
        return True
    return _aadhi_h_inside(text, roots)


def _aadhi_h_program_ok(policy, executable, args):
    allowed = policy["programs"]
    if executable is not None:
        try:
            if _aadhi_h_os.path.normcase(_aadhi_h_os.fsdecode(executable)) not in allowed:
                return False
        except TypeError:
            return False
    if isinstance(args, (str, bytes)):  # Windows: the audited value is the final command line
        line = _aadhi_h_os.fsdecode(args)
        for full in allowed.values():
            quoted = _aadhi_h_subprocess.list2cmdline([full])
            if line == quoted or line.startswith(quoted + " "):
                return True
        return False
    try:
        first = _aadhi_h_os.fsdecode(list(args)[0])
    except (TypeError, IndexError):
        return False
    return _aadhi_h_os.path.normcase(first) in allowed


def _aadhi_h_decide(policy, event, args):
    """Reason to refuse ``event`` (an empty string allows it)."""
    if event in _AADHI_H_DENY_EVENTS or event.startswith(_AADHI_H_DENY_PREFIXES):
        return event
    if event == "subprocess.Popen":
        executable, cmd = (args[0], args[1]) if len(args) >= 2 else (None, None)
        return "" if _aadhi_h_program_ok(policy, executable, cmd) else "subprocess.Popen"
    if event == "os.posix_spawn":  # also os.posix_spawnp; subprocess spawns this way on Python 3.13+ (glibc >= 2.34)
        path, argv = (args[0], args[1]) if len(args) >= 2 else (None, None)
        return "" if path is not None and _aadhi_h_program_ok(policy, path, argv) else event
    if event == "open":
        path = args[0] if args else None
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool(flags & _AADHI_H_WRITE_FLAGS)
        ok = _aadhi_h_path_ok(path, policy["write"] if writing else policy["read"])
        return "" if ok else ("open for writing" if writing else "open")
    for table, roots in ((_AADHI_H_WRITE_EVENTS, policy["write"]), (_AADHI_H_READ_EVENTS, policy["read"])):
        indexes = table.get(event)
        if indexes and not all(_aadhi_h_path_ok(args[i] if i < len(args) else None, roots) for i in indexes):
            return event
    return ""


class _AadhiTexPrograms:
    """``subprocess`` as seen by the runtime's TeX wrapper: bare TeX program names become absolute paths."""

    def __init__(self, real, policy):
        self._real = real
        self._policy = policy

    def __getattr__(self, name):
        return getattr(self._real, name)

    def _absolute(self, command):
        if isinstance(command, (list, tuple)) and command:
            first = str(command[0])
            base = first.lower()[:-4] if first.lower().endswith(".exe") else first.lower()
            if _aadhi_h_os.path.basename(first) == first and base in self._policy["names"]:
                for full in self._policy["programs"].values():
                    name = _aadhi_h_os.path.basename(full).lower()
                    if name in (base, base + ".exe"):
                        return [full, *command[1:]]
        return command

    def run(self, command, *args, **kwargs):
        return self._real.run(self._absolute(command), *args, **kwargs)


def _aadhi_h_install_audit(spec):
    policy = _aadhi_h_make_policy(spec)
    local = _aadhi_h_threading.local()

    def _aadhi_h_hook(event, args):
        if getattr(local, "busy", False):
            return
        local.busy = True
        try:
            reason = _aadhi_h_decide(policy, event, args)
        except Exception:
            reason = event
        finally:
            local.busy = False
        if reason:
            _AADHI_H_STATE["blocked"] += 1
            if _AADHI_H_STATE["blocked"] <= 20:
                _aadhi_h_say("BLOCKED", reason)
            raise PermissionError(f"blocked by the Aadhi sandbox: {reason}")

    # The runtime's TeX wrapper calls the module-level ``_aadhi_subprocess``; route it through the
    # absolute-path resolver so the hook can require exact program paths.
    real = globals().get("_aadhi_subprocess")
    if real is not None:
        globals()["_aadhi_subprocess"] = _AadhiTexPrograms(real, policy)
    _aadhi_h_sys.addaudithook(_aadhi_h_hook)
    return policy


if _AADHI_H.get("frame_limits"):
    _aadhi_h_install_limits(_AADHI_H)
    _aadhi_h_atexit.register(_aadhi_h_print_stats)
if _AADHI_H.get("audit_hook"):
    _aadhi_h_install_audit(_AADHI_H)
