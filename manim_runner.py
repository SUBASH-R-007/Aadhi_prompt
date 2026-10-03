"""Runs INSIDE the Manim sandbox (Phase 10): the trusted bootstrap that renders one scene of untrusted code.

    python -I -S -B runner.py <workspace>/job.json

It is copied into the job's workspace (the sandbox cannot read the application's files). It forces the
profile's resolution and frame rate, counts frames against the limit, and installs an audit hook before the
generated code runs, so starting processes, opening network connections and loading native libraries are
refused inside the interpreter as well. That hook is one more layer: the sandbox itself (OS isolation and
resource limits) is what actually stops these. Results go to <workspace>/result.json and the video to
<workspace>/output/scene.mp4 — nowhere else.
"""
import json
import os
import sys
import time
import traceback

JOB = json.load(open(sys.argv[1], encoding="utf-8"))
WORKSPACE = JOB["workspace"]
RESULT = os.path.join(WORKSPACE, "result.json")
STARTED = time.time()


def finish(ok, **fields):
    data = {"ok": ok, "seconds": round(time.time() - STARTED, 2), **fields}
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(data, f)
    sys.stdout.flush()
    os._exit(0 if ok else 2)


class Blocked(RuntimeError):
    def __init__(self, category, event):
        super().__init__(f"blocked by the sandbox: {event}")
        self.category = category


BLOCKED_EVENTS = {
    "subprocess.Popen": "process_denied", "os.system": "process_denied", "os.exec": "process_denied",
    "os.spawn": "process_denied", "os.posix_spawn": "process_denied", "os.startfile": "process_denied",
    "os.fork": "process_denied", "os.forkpty": "process_denied", "os.kill": "process_denied",
    "socket.connect": "network_denied", "socket.bind": "network_denied", "socket.getaddrinfo": "network_denied",
    "socket.gethostbyname": "network_denied", "socket.sendto": "network_denied",
    "ctypes.dlopen": "unsafe_code", "ctypes.dlsym": "unsafe_code", "ctypes.call_function": "unsafe_code",
    "winreg.OpenKey": "unsafe_code", "winreg.ConnectRegistry": "unsafe_code", "webbrowser.open": "process_denied",
}


READ_ROOTS = []   # the workspace and the Python runtime (set in main); the generated code may read nothing else
WRITE_ROOTS = []  # the workspace only
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _inside(path, roots):
    try:
        full = os.path.normcase(os.path.abspath(path))
    except (TypeError, ValueError):
        return False
    return any(full == root or full.startswith(root + os.sep) for root in roots)


def audit(event, args):
    category = BLOCKED_EVENTS.get(event)
    if category:
        raise Blocked(category, event)
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0])
        mode, flags = (args[1] or "") if len(args) > 1 else "", (args[2] or 0) if len(args) > 2 else 0
        writing = any(c in str(mode) for c in "wax+") or bool(flags & WRITE_FLAGS)
        if path.lower() in ("nul", os.devnull.lower()):
            return
        if not _inside(path, WRITE_ROOTS if writing else READ_ROOTS):
            raise Blocked("filesystem_access_denied", f"open {'for writing ' if writing else ''}outside the sandbox")
    elif event in ("os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes)):
        if not _inside(os.fsdecode(args[0]), READ_ROOTS):
            raise Blocked("filesystem_access_denied", "list a folder outside the sandbox")


NETWORK_ERRORS = (10013, 10050, 10051, 10060, 10061, 10065, 11001, 11002, 11003, 11004)  # WSA: access denied, unreachable, ...


def category_of(exc):
    if isinstance(exc, Blocked):
        return exc.category
    if isinstance(exc, MemoryError) or type(exc).__name__ == "_ArrayMemoryError":
        return "memory_limit"
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) == 1816:  # the Job Object's process quota
            return "process_denied"
        if getattr(exc, "winerror", None) in NETWORK_ERRORS or isinstance(exc, (TimeoutError, ConnectionError)) \
                or type(exc).__name__ in ("gaierror", "herror", "timeout"):
            return "network_denied"
    if isinstance(exc, PermissionError):
        return "filesystem_access_denied"
    if isinstance(exc, FrameLimit):
        return "frame_limit"
    if isinstance(exc, SettingLimit):
        return exc.category
    return "render_failed"


class FrameLimit(RuntimeError):
    pass


class SettingLimit(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def main():
    if os.name == "nt":
        # The sandbox cannot read the folders above its workspace; resolving a path's final name needs them.
        # Paths inside the workspace are plain, so the absolute path is the real one.
        import ntpath
        real = ntpath.realpath

        def safe_realpath(path, *a, **k):
            try:
                return real(path, *a, **k)
            except OSError:
                return ntpath.abspath(path)
        ntpath.realpath = os.path.realpath = safe_realpath
    for path in reversed(JOB.get("python_path") or []):
        sys.path.insert(0, path)
    lim = JOB["limits"]
    from manim import config
    from manim.scene.scene_file_writer import SceneFileWriter

    def apply_profile():
        config.media_dir = os.path.join(WORKSPACE, "media")
        # Short folders (Windows paths are limited to 260 characters; Manim's own layout nests five levels deep)
        config.video_dir = os.path.join(WORKSPACE, "media", "v")
        config.partial_movie_dir = os.path.join(WORKSPACE, "media", "p")
        config.pixel_width, config.pixel_height, config.frame_rate = lim["width"], lim["height"], lim["fps"]
        config.background_color = lim["background"]
        config.disable_caching = True
        config.write_to_movie = True
        config.save_last_frame = False
        config.progress_bar = "none"
        config.verbosity = "WARNING"
        config.output_file = "scene"
    apply_profile()
    frames = {"n": 0}
    write_frame = SceneFileWriter.write_frame

    def counted(self, frame_or_renderer, num_frames=1, *a, **k):
        # Every frame written is counted (a still wait writes one image many times), and the profile must still
        # hold: code that changes the resolution or frame rate while rendering is stopped before it is written
        frames["n"] += max(1, int(num_frames or 1))
        if frames["n"] > lim["max_frames"]:
            raise FrameLimit(f"more than {lim['max_frames']} frames ({lim['max_duration']} s at {lim['fps']} fps)")
        shape = getattr(frame_or_renderer, "shape", None)
        if (config.pixel_width, config.pixel_height) != (lim["width"], lim["height"]) or                 (shape is not None and (shape[0] > lim["height"] or shape[1] > lim["width"])):
            raise SettingLimit("resolution_limit", "the scene changed the resolution while rendering")
        if config.frame_rate != lim["fps"]:
            raise SettingLimit("fps_limit", "the scene changed the frame rate while rendering")
        return write_frame(self, frame_or_renderer, num_frames, *a, **k)
    SceneFileWriter.write_frame = counted

    import importlib.util
    READ_ROOTS.extend(os.path.normcase(os.path.abspath(p)) for p in
                      [WORKSPACE, sys.prefix, sys.base_prefix, sys.exec_prefix, *(JOB.get("python_path") or [])])
    WRITE_ROOTS.append(os.path.normcase(os.path.abspath(WORKSPACE)))
    if JOB.get("audit_hook", True):  # the host turns it off only in its own security tests, to prove the OS isolation
        sys.addaudithook(audit)  # from here on the generated code runs; nothing can remove this hook
    spec = importlib.util.spec_from_file_location("aadhi_scene", os.path.join(WORKSPACE, "source", "scene.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    apply_profile()  # whatever the module set at import time, the profile wins
    cls = getattr(module, JOB["scene"], None)
    if cls is None:
        raise SettingLimit("render_failed", f"the scene {JOB['scene']} was not found")
    scene = cls()
    camera = getattr(getattr(scene, "renderer", None), "camera", None)
    if camera is not None and (camera.pixel_width > lim["width"] or camera.pixel_height > lim["height"]):
        raise SettingLimit("resolution_limit", f"the scene asked for {camera.pixel_width}x{camera.pixel_height}")
    if config.frame_rate > lim["fps"]:
        raise SettingLimit("fps_limit", f"the scene asked for {config.frame_rate} fps")
    scene.render()
    movie = scene.renderer.file_writer.movie_file_path
    if not movie or not os.path.isfile(movie):
        raise SettingLimit("invalid_output", "the render produced no video")
    target = os.path.join(WORKSPACE, "output", "scene.mp4")
    os.replace(movie, target)
    finish(True, frames=frames["n"], scene=JOB["scene"])


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001 - every failure is reported to the host, classified
        tail = traceback.format_exc()[-1500:].replace(WORKSPACE, "<workspace>")
        for path in JOB.get("python_path") or []:
            tail = tail.replace(path, "<python>")
        finish(False, category=category_of(exc), message=f"{type(exc).__name__}: {str(exc)[:300]}".replace(WORKSPACE, "<workspace>"),
               trace=tail)
