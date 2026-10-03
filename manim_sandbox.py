"""Manim sandbox (Phase 10): where untrusted Manim code runs, isolated from the application.

The API server never runs generated code itself. A render is handed to one of these runtimes:

  windows-appcontainer   (Windows; implemented and tested on the development machine)
      * AppContainer: the OS denies the process every file it is not explicitly granted (the application's
        folder, .env, the database, other users' files, the user profile) and every network connection
        (internet, private addresses, localhost); it runs with a restricted, low-integrity token — never as
        the user or an administrator.
      * a unique capability per job: only that job can open its own workspace (jobs cannot see each other).
      * Job Object: hard memory limit, CPU-rate cap, CPU-time limit, maximum number of processes (default 1:
        the code cannot start any process), no desktop/clipboard access, and the whole process tree is
        killed on timeout, cancellation, or when the server itself dies (kill-on-close).
      * a deliberately built environment (no secrets), a private workspace watched for size, removed after.
  docker                 (Linux / any host with a Docker daemon; implemented, NOT validated here — no Docker
                          on the development machine)
      * --network none, --read-only root, tmpfs /tmp, non-root user, --cap-drop ALL,
        no-new-privileges, pids/memory/CPU limits, only the job workspace mounted; image sandbox/Dockerfile.manim.

If no runtime is available, Manim is unavailable. There is no unsafe fallback.
"""
import ctypes
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manim_runner.py")
MARKER = ".aadhi-sandbox.json"
RESULT_MAX_BYTES = 64 * 1024
LOG_TAIL_BYTES = 4000


from manim_security import MANIM_VERSION  # noqa: E402 - the version the health check expects inside the sandbox


class SandboxUnavailable(RuntimeError):
    """Secure Manim execution cannot be provided here (the reason is for administrators)."""


class SandboxResult:
    def __init__(self, ok, category=None, message="", output=None, metrics=None, log=""):
        self.ok = ok
        self.category = category
        self.message = message
        self.output = output          # the video inside the workspace, when ok
        self.metrics = metrics or {}  # seconds, cpu_seconds, peak_memory_mb, frames, output_bytes, processes
        self.log = log                # bounded, host paths removed


# User-facing words for each failure category (no paths, hosts or internals)
MESSAGES = {
    "unsafe_code": "This animation requested an operation that is not allowed in the Manim sandbox.",
    "filesystem_access_denied": "This animation tried to read or write files outside its sandbox, which is not allowed.",
    "network_access_denied": "This animation tried to use the network, which is not allowed in the Manim sandbox.",
    "network_denied": "This animation tried to use the network, which is not allowed in the Manim sandbox.",
    "process_denied": "This animation tried to start another program, which is not allowed in the Manim sandbox.",
    "process_limit": "This animation tried to start another program, which is not allowed in the Manim sandbox.",
    "memory_limit": "The animation exceeded the memory limit and was stopped.",
    "cpu_limit": "The animation used more processing time than allowed and was stopped.",
    "timeout": "The animation took longer than the time limit and was stopped.",
    "output_limit": "The animation produced more data than allowed and was stopped.",
    "frame_limit": "The animation is longer than the allowed duration and was stopped.",
    "resolution_limit": "The animation asked for a larger picture than allowed.",
    "fps_limit": "The animation asked for a higher frame rate than allowed.",
    "invalid_output": "The animation finished but did not produce a usable video.",
    "render_failed": "The animation code failed while rendering.",
    "cancelled": "The animation was cancelled.",
    "sandbox_unavailable": "Secure Manim execution is unavailable in this deployment.",
    "sandbox_error": "The Manim sandbox could not run this animation.",
}


def message_for(category):
    return MESSAGES.get(category, MESSAGES["sandbox_error"])


def _env_flag(env, name, default):
    value = env.get(name)
    return default if value is None or value.strip() == "" else value.strip().lower() not in ("0", "false", "no", "off")


def default_root(env=None):
    env = os.environ if env is None else env
    if env.get("MANIM_SANDBOX_DIR"):
        return os.path.abspath(env["MANIM_SANDBOX_DIR"])
    base = env.get("LOCALAPPDATA") if os.name == "nt" else None
    return os.path.join(base or os.path.join(os.path.expanduser("~"), ".cache"), "AadhiEduEngine", "manim-sandbox")


def _dir_size(path, limit):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
            if total > limit:
                return total
    return total


def _read_tail(path, size=LOG_TAIL_BYTES):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - size))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


# ---- workspaces -------------------------------------------------------------------------------------------

class Workspace:
    """One job's private folder: source/, output/, media/, temp/, home/, logs/ and nothing else."""

    def __init__(self, root, job_id):
        if not job_id or any(c not in "0123456789abcdef-" for c in job_id):
            raise ValueError("invalid job id")
        self.jobs = os.path.join(root, "jobs")
        self.path = os.path.join(self.jobs, f"job-{job_id}")
        self.job_id = job_id

    def create(self, run_id, attempt_id):
        self.remove()
        for sub in ("source", "output", "media", "temp", "home", "logs"):
            os.makedirs(os.path.join(self.path, sub), exist_ok=True)
        with open(os.path.join(self.path, MARKER), "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "attempt_id": attempt_id, "created": time.time()}, f)
        return self

    def file(self, *parts):
        """A path inside the workspace; refuses anything that would leave it (.., absolute, links)."""
        full = os.path.abspath(os.path.join(self.path, *parts))
        if os.path.commonpath([full, os.path.abspath(self.path)]) != os.path.abspath(self.path):
            raise ValueError("path escapes the sandbox workspace")
        return full

    def safe_output(self):
        """The video the runner wrote, if it is a real file exactly where expected (no link, no escape)."""
        target = self.file("output", "scene.mp4")
        if not os.path.lexists(target) or os.path.islink(target) or not os.path.isfile(target):
            return None
        real = os.path.realpath(target)
        if os.path.commonpath([real, os.path.realpath(self.path)]) != os.path.realpath(self.path):
            return None
        return target

    def remove(self):
        if os.path.isdir(self.path) and os.path.commonpath([os.path.abspath(self.path), os.path.abspath(self.jobs)]) == os.path.abspath(self.jobs):
            shutil.rmtree(self.path, onerror=lambda fn, p, exc: (os.chmod(p, 0o700), fn(p)) if os.path.exists(p) else None)


def sweep_orphans(root, is_active, older_than=600):
    """Removes job workspaces left behind (a crash, a killed server): only folders inside <root>/jobs that carry
    the sandbox marker, whose attempt is no longer active and that are older than `older_than` seconds."""
    jobs = os.path.join(root, "jobs")
    removed = 0
    if not os.path.isdir(jobs):
        return 0
    for name in os.listdir(jobs):
        path = os.path.join(jobs, name)
        marker = os.path.join(path, MARKER)
        if not name.startswith("job-") or os.path.islink(path) or not os.path.isfile(marker):
            continue
        try:
            with open(marker, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if time.time() - info.get("created", 0) < older_than or is_active(info.get("attempt_id")):
            continue
        Workspace(root, name[len("job-"):]).remove()
        removed += 1
    return removed


# ---- Windows: AppContainer + Job Object ------------------------------------------------------------------------

if os.name == "nt":
    import ctypes.wintypes as wt

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p), ("CapabilityCount", wt.DWORD),
                    ("Reserved", wt.DWORD)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                    ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                    ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD),
                    ("SchedulingClass", wt.DWORD)]

    class _EXT_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _BASIC_ACCOUNTING(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                    ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                    ("TotalPageFaultCount", wt.DWORD), ("TotalProcesses", wt.DWORD), ("ActiveProcesses", wt.DWORD),
                    ("TotalTerminatedProcesses", wt.DWORD)]

    class _CPU_RATE(ctypes.Structure):
        _fields_ = [("ControlFlags", wt.DWORD), ("CpuRate", wt.DWORD)]

    class _UI_RESTRICTIONS(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wt.DWORD)]


class WindowsAppContainerRuntime:
    name = "windows-appcontainer"
    PROFILE = "AadhiEduEngine.ManimSandbox"

    def __init__(self, root, env=None):
        self.root = root
        self.env = os.environ if env is None else env
        self._sid = None
        self._sid_str = None
        self._lock = threading.Lock()

    # -- setup (once) --
    def _apis(self):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.userenv = ctypes.WinDLL("userenv")
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kbase = ctypes.WinDLL("kernelbase", use_last_error=True)
        self.k32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                                            ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        self.k32.CreateJobObjectW.restype = wt.HANDLE
        self.k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        for fn in ("SetInformationJobObject", "QueryInformationJobObject"):
            getattr(self.k32, fn).argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD] + ([ctypes.c_void_p] if fn.startswith("Query") else [])
        self.k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        self.k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
        self.k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        self.k32.CloseHandle.argtypes = [wt.HANDLE]
        self.k32.ResumeThread.argtypes = [wt.HANDLE]
        self.k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]

    def _profile(self):
        with self._lock:
            if self._sid:
                return self._sid, self._sid_str
            self._apis()
            sid = ctypes.c_void_p()
            name = self.env.get("MANIM_SANDBOX_PROFILE") or self.PROFILE
            hr = self.userenv.CreateAppContainerProfile(name, name, "Aadhi EduEngine Manim sandbox", None, 0, ctypes.byref(sid))
            if hr != 0 and self.userenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid)) != 0:
                raise SandboxUnavailable("the AppContainer profile could not be created")
            text = wt.LPWSTR()
            if not self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise SandboxUnavailable("the AppContainer identity could not be read")
            self._sid, self._sid_str = sid, text.value
            self._grant_runtime()
            return self._sid, self._sid_str

    def _icacls(self, path, sid, rights):
        result = subprocess.run(["icacls", path, "/grant", f"*{sid}:{rights}", "/Q"], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise SandboxUnavailable(f"could not grant the sandbox access to its runtime ({result.stderr.strip()[:200]})")

    def _grant_runtime(self):
        """The sandbox may read and execute the Python runtime and the installed packages — nothing else of
        this machine's user files. Done once (recorded in the sandbox folder)."""
        paths = sorted({os.path.abspath(sys.base_prefix), os.path.abspath(sys.prefix)})
        os.makedirs(self.root, exist_ok=True)
        stamp = os.path.join(self.root, f"runtime-acl-{hashlib.sha256((self._sid_str + '|'.join(paths)).encode()).hexdigest()[:16]}.done")
        if os.path.exists(stamp):
            return
        for path in paths:
            current = subprocess.run(["icacls", path], capture_output=True, text=True, timeout=60).stdout
            if any(self._sid_str in line and "(OI)(CI)" in line and "(RX)" in line for line in current.splitlines()):
                continue  # granted earlier (another sandbox folder): no need to walk the whole runtime again
            self._icacls(path, self._sid_str, "(OI)(CI)(RX)")
        with open(stamp, "w", encoding="utf-8") as f:
            json.dump({"sid": self._sid_str, "paths": paths, "at": time.time()}, f)

    def _capability(self, job_id):
        """A capability SID unique to this job: its workspace is granted to it only."""
        groups, group_count, sids, count = ctypes.c_void_p(), wt.DWORD(), ctypes.c_void_p(), wt.DWORD()
        fn = self.kbase.DeriveCapabilitySidsFromName
        fn.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.DWORD), ctypes.POINTER(ctypes.c_void_p),
                       ctypes.POINTER(wt.DWORD)]
        if not fn(f"aadhiManimJob{job_id.replace('-', '')}", ctypes.byref(groups), ctypes.byref(group_count), ctypes.byref(sids),
                  ctypes.byref(count)) or count.value < 1:
            raise SandboxUnavailable("a job capability could not be derived")
        sid = ctypes.cast(sids, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wt.LPWSTR()
        self.advapi.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(text))
        return ctypes.c_void_p(sid), text.value

    def check(self):
        if os.name != "nt":
            return False, "AppContainers exist only on Windows"
        try:
            self._profile()
        except (OSError, AttributeError, SandboxUnavailable) as e:
            return False, str(e)
        return True, ""

    def describe(self):
        return {"runtime": self.name, "isolation": ["AppContainer (files, network, low-integrity token)", "per-job capability",
                                                    "Job Object (memory, CPU rate, CPU time, process count, kill tree, no UI)",
                                                    "empty environment", "private workspace"]}

    # -- one run --
    def _environment(self, ws):
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        base = os.path.abspath(sys.base_prefix)
        values = {"SYSTEMROOT": system_root, "WINDIR": system_root, "PATH": f"{base};{system_root}\\System32",
                  "TEMP": ws.file("temp"), "TMP": ws.file("temp"), "USERPROFILE": ws.file("home"), "HOME": ws.file("home"),
                  "LOCALAPPDATA": ws.file("home"), "APPDATA": ws.file("home"), "HOMEDRIVE": ws.path[:2], "HOMEPATH": ws.file("home")[2:],
                  "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                  "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "MPLBACKEND": "Agg"}
        return "\0".join(f"{k}={v}" for k, v in values.items()) + "\0\0"

    def run(self, ws, job, lim, cancelled=lambda: False, on_start=None):
        sid, _sid_str = self._profile()
        cap_sid, cap_str = self._capability(ws.job_id)
        self._icacls(ws.path, cap_str, "(OI)(CI)(M)")  # only this job's capability may use its workspace
        python = os.path.join(os.path.abspath(sys.base_prefix), "python.exe")
        cmd = f'"{python}" -I -S -B "{ws.file("runner.py")}" "{ws.file("job.json")}"'
        k32 = self.k32
        size = ctypes.c_size_t()
        k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrs = ctypes.create_string_buffer(size.value)
        if not k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
            raise SandboxUnavailable("the sandbox process attributes could not be prepared")
        caps = (_SID_AND_ATTRIBUTES * 1)(_SID_AND_ATTRIBUTES(cap_sid, 0x4))  # SE_GROUP_ENABLED
        security = _SECURITY_CAPABILITIES(sid, ctypes.cast(caps, ctypes.c_void_p), 1, 0)
        if not k32.UpdateProcThreadAttribute(attrs, 0, ctypes.c_size_t(0x00020009), ctypes.byref(security), ctypes.sizeof(security), None, None):
            raise SandboxUnavailable("the sandbox isolation could not be applied")
        # Not a "less privileged" AppContainer: it cannot start without the registryRead capability, which is
        # exactly what grants the registry reads it would remove (see the security model, residual risks)
        job_handle = k32.CreateJobObjectW(None, None)
        if not job_handle:
            raise SandboxUnavailable("the sandbox resource limits could not be created")
        try:
            limit = _EXT_LIMIT()
            limit.BasicLimitInformation.LimitFlags = (0x8 | 0x200 | 0x100 | 0x2000 | 0x400 | 0x4)
            # ACTIVE_PROCESS | JOB_MEMORY | PROCESS_MEMORY | KILL_ON_JOB_CLOSE | DIE_ON_UNHANDLED_EXCEPTION | JOB_TIME
            limit.BasicLimitInformation.ActiveProcessLimit = lim["processes"]
            limit.BasicLimitInformation.PerJobUserTimeLimit = int(lim["timeout"] * 10_000_000)  # CPU time, 100 ns units
            limit.JobMemoryLimit = limit.ProcessMemoryLimit = lim["memory_mb"] * 1024 * 1024
            if not k32.SetInformationJobObject(job_handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
                raise SandboxUnavailable("the sandbox limits could not be set")
            rate = _CPU_RATE(0x1 | 0x4, lim["cpu_percent"] * 100)  # ENABLE | HARD_CAP, share of all processors
            k32.SetInformationJobObject(job_handle, 15, ctypes.byref(rate), ctypes.sizeof(rate))
            ui = _UI_RESTRICTIONS(0xFF)  # no desktop, clipboard, global atoms, system settings, handles of other windows
            k32.SetInformationJobObject(job_handle, 4, ctypes.byref(ui), ctypes.sizeof(ui))
            si = _STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(si)
            si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
            pi = _PROCESS_INFORMATION()
            env = ctypes.create_unicode_buffer(self._environment(ws))
            flags = 0x00080000 | 0x00000004 | 0x08000000 | 0x00000400  # EXTENDED_STARTUPINFO | SUSPENDED | NO_WINDOW | UNICODE_ENV
            if not k32.CreateProcessW(None, ctypes.create_unicode_buffer(cmd), None, None, False, flags, env, ws.path,
                                      ctypes.byref(si), ctypes.byref(pi)):
                raise SandboxUnavailable(f"the sandbox process could not be started (error {ctypes.get_last_error()})")
            try:
                if not k32.AssignProcessToJobObject(job_handle, pi.hProcess):
                    k32.TerminateProcess(pi.hProcess, 1)
                    raise SandboxUnavailable("the sandbox process could not be limited")
                k32.ResumeThread(pi.hThread)
                if on_start:
                    on_start({"runtime": self.name, "pid": pi.dwProcessId})
                return self._watch(job_handle, pi.hProcess, ws, lim, cancelled)
            finally:
                k32.CloseHandle(pi.hThread)
                k32.CloseHandle(pi.hProcess)
        finally:
            self._kill_all(job_handle)
            k32.CloseHandle(job_handle)  # kill-on-close: nothing of the job survives
            k32.DeleteProcThreadAttributeList(attrs)

    def _accounting(self, job_handle):
        acc, ext = _BASIC_ACCOUNTING(), _EXT_LIMIT()
        self.k32.QueryInformationJobObject(job_handle, 1, ctypes.byref(acc), ctypes.sizeof(acc), None)
        self.k32.QueryInformationJobObject(job_handle, 9, ctypes.byref(ext), ctypes.sizeof(ext), None)
        return {"cpu_seconds": round((acc.TotalUserTime + acc.TotalKernelTime) / 1e7, 2), "user_seconds": acc.TotalUserTime / 1e7,
                "active_processes": acc.ActiveProcesses, "processes_started": acc.TotalProcesses,
                "peak_memory_mb": round(ext.PeakJobMemoryUsed / 1048576, 1)}

    def _kill_all(self, job_handle):
        """Terminates every process of the job and confirms none is left."""
        acc = self._accounting(job_handle)
        if acc["active_processes"]:
            self.k32.TerminateJobObject(job_handle, 1)
            for _ in range(50):
                if not self._accounting(job_handle)["active_processes"]:
                    break
                time.sleep(0.1)
        return self._accounting(job_handle)["active_processes"]

    def _watch(self, job_handle, process, ws, lim, cancelled):
        started = time.time()
        stop = None
        last_size = 0.0
        while True:
            if self.k32.WaitForSingleObject(process, 250) == 0:
                break
            elapsed = time.time() - started
            if cancelled():
                stop = "cancelled"
            elif elapsed > lim["timeout"]:
                stop = "timeout"
            elif time.time() - last_size >= 1.0:
                last_size = time.time()
                if _dir_size(ws.path, lim["workspace_mb"] * 1048576) > lim["workspace_mb"] * 1048576:
                    stop = "output_limit"
            if stop:
                self.k32.TerminateJobObject(job_handle, 1)
                self.k32.WaitForSingleObject(process, 5000)
                break
        code = wt.DWORD()
        self.k32.GetExitCodeProcess(process, ctypes.byref(code))
        left = self._kill_all(job_handle)
        metrics = {**self._accounting(job_handle), "seconds": round(time.time() - started, 2), "exit_code": code.value,
                   "processes_left": left}
        if not stop and metrics["user_seconds"] >= lim["timeout"] * 0.98:
            stop = "cpu_limit"
        return stop, metrics


# ---- Docker (Linux production) -----------------------------------------------------------------------------

class DockerRuntime:
    """A disposable container per render. Implemented to the documented flags; not validated on the development
    machine (it has no Docker)."""
    name = "docker"

    def __init__(self, root, env=None):
        self.root = root
        self.env = os.environ if env is None else env
        self.image = self.env.get("MANIM_SANDBOX_IMAGE") or "aadhi-manim-sandbox:0.21.0"

    def check(self):
        exe = shutil.which("docker")
        if not exe:
            return False, "docker is not installed"
        try:
            if subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=15).returncode != 0:
                return False, "the docker daemon is not reachable"
            if subprocess.run([exe, "image", "inspect", self.image], capture_output=True, timeout=15).returncode != 0:
                return False, f"the sandbox image {self.image} is not built (see sandbox/Dockerfile.manim)"
        except (OSError, subprocess.TimeoutExpired):
            return False, "docker did not answer"
        return True, ""

    def describe(self):
        return {"runtime": self.name, "image": self.image,
                "isolation": ["--network none", "--read-only root", "tmpfs /tmp", "non-root user 10001", "--cap-drop ALL",
                              "no-new-privileges", "pids/memory/CPU limits", "only the job workspace mounted"]}

    def command(self, ws, lim, name):
        return ["docker", "run", "--rm", "--name", name, "--label", "aadhi.manim=1", "--network", "none", "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m", "--user", "10001:10001", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", str(max(8, lim["processes"] * 8)),
                "--memory", f"{lim['memory_mb']}m", "--memory-swap", f"{lim['memory_mb']}m",
                "--cpus", f"{max(0.5, lim['cpu_percent'] / 100 * (os.cpu_count() or 1)):.2f}",
                "--ulimit", f"fsize={lim['output_mb'] * 2 * 1048576}", "--env", "OPENBLAS_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=1",
                "-v", f"{ws.path}:/workspace:rw", "--workdir", "/workspace", self.image,
                "python", "-I", "-S", "-B", "/workspace/runner.py", "/workspace/job.json"]

    def run(self, ws, job, lim, cancelled=lambda: False, on_start=None):
        name = f"aadhi-manim-{ws.job_id[:24]}"
        proc = subprocess.Popen(self.command(ws, lim, name), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if on_start:
            on_start({"runtime": self.name, "container": name})
        started, stop = time.time(), None
        while proc.poll() is None:
            if cancelled():
                stop = "cancelled"
            elif time.time() - started > lim["timeout"]:
                stop = "timeout"
            elif _dir_size(ws.path, lim["workspace_mb"] * 1048576) > lim["workspace_mb"] * 1048576:
                stop = "output_limit"
            if stop:
                subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
                break
            time.sleep(0.5)
        proc.wait(timeout=60)
        oom = subprocess.run(["docker", "inspect", "--format", "{{.State.OOMKilled}}", name], capture_output=True, text=True).stdout.strip()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        if oom == "true":
            stop = "memory_limit"
        return stop, {"seconds": round(time.time() - started, 2), "exit_code": proc.returncode}

    def sweep(self):
        exe = shutil.which("docker")
        if exe:
            ids = subprocess.run([exe, "ps", "-aq", "--filter", "label=aadhi.manim=1"], capture_output=True, text=True).stdout.split()
            if ids:
                subprocess.run([exe, "rm", "-f", *ids], capture_output=True)


# ---- the sandbox ------------------------------------------------------------------------------------------------

class ManimSandbox:
    def __init__(self, env=None):
        self.env = os.environ if env is None else env
        self.root = default_root(self.env)
        self._runtime = None
        self._reason = None
        self._checked = 0
        self.health = None

    def runtime(self):
        """The secure runtime to use, or raises SandboxUnavailable. Chosen once (re-checked every 10 minutes when unavailable)."""
        if self._runtime is not None:
            return self._runtime
        if self._reason and time.time() - self._checked < 600:
            raise SandboxUnavailable(self._reason)
        self._checked = time.time()
        if not _env_flag(self.env, "MANIM_SANDBOX_ENABLED", True):
            self._reason = "Manim rendering is switched off (MANIM_SANDBOX_ENABLED=0)"
            raise SandboxUnavailable(self._reason)
        wanted = (self.env.get("MANIM_SANDBOX_RUNTIME") or "auto").strip().lower()
        options = {"windows-appcontainer": WindowsAppContainerRuntime, "docker": DockerRuntime}
        if wanted == "auto":
            names = (["windows-appcontainer"] if os.name == "nt" else []) + ["docker"]
        elif wanted in options:
            names = [wanted]
        else:
            self._reason = f"unknown MANIM_SANDBOX_RUNTIME {wanted!r} (there is no unsafe runtime)"
            raise SandboxUnavailable(self._reason)
        reasons = []
        for name in names:
            runtime = options[name](self.root, self.env)
            ok, why = runtime.check()
            if ok:
                self._runtime, self._reason = runtime, None
                return runtime
            reasons.append(f"{name}: {why}")
        self._reason = "no secure runtime is available (" + "; ".join(reasons) + ")"
        raise SandboxUnavailable(self._reason)

    def status(self):
        try:
            runtime = self.runtime()
            return {"available": True, **runtime.describe(), "health": self.health}
        except SandboxUnavailable as e:
            return {"available": False, "reason": str(e), "message": message_for("sandbox_unavailable")}

    def prepare(self, job_id, run_id, attempt_id, code, scene, lim, audit_hook=True):
        """Writes the job's workspace. audit_hook=False only in the security tests (to prove the OS isolation alone)."""
        ws = Workspace(self.root, job_id).create(run_id, attempt_id)
        with open(ws.file("source", "scene.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(code)
        shutil.copyfile(RUNNER, ws.file("runner.py"))
        python_path = [] if isinstance(self._runtime, DockerRuntime) else [os.path.join(os.path.abspath(sys.prefix), "Lib", "site-packages")] \
            if os.name == "nt" else [p for p in sys.path if p.endswith("site-packages")]
        workspace = "/workspace" if isinstance(self._runtime, DockerRuntime) else ws.path
        job = {"workspace": workspace, "scene": scene, "limits": lim, "python_path": python_path, "audit_hook": bool(audit_hook)}
        with open(ws.file("job.json"), "w", encoding="utf-8") as f:
            json.dump(job, f)
        return ws, job

    def execute(self, ws, job, lim, cancelled=lambda: False, on_start=None):
        """Runs the prepared job in the secure runtime; returns a SandboxResult (never raises for the code's failures)."""
        runtime = self.runtime()
        stop, metrics = runtime.run(ws, job, lim, cancelled, on_start)
        result = {}
        path = ws.file("result.json")
        if os.path.isfile(path) and os.path.getsize(path) <= RESULT_MAX_BYTES:
            try:
                with open(path, encoding="utf-8") as f:
                    result = json.load(f)
            except ValueError:
                result = {}
        metrics.update({k: result[k] for k in ("frames", "seconds") if k in result and k != "seconds"})
        log = (result.get("trace") or "")[-LOG_TAIL_BYTES:]
        if stop:
            return SandboxResult(False, stop, message_for(stop), metrics=metrics, log=log)
        if not result:
            if metrics.get("peak_memory_mb", 0) >= lim["memory_mb"] * 0.95:
                return SandboxResult(False, "memory_limit", message_for("memory_limit"), metrics=metrics)
            return SandboxResult(False, "sandbox_error", message_for("sandbox_error"), metrics=metrics)
        if not result.get("ok"):
            category = result.get("category") or "render_failed"
            if category == "render_failed" and metrics.get("peak_memory_mb", 0) >= lim["memory_mb"] * 0.95:
                category = "memory_limit"
            return SandboxResult(False, category, message_for(category), metrics=metrics, log=log or result.get("message", ""))
        output = ws.safe_output()
        if not output:
            return SandboxResult(False, "invalid_output", message_for("invalid_output"), metrics=metrics)
        metrics["output_bytes"] = os.path.getsize(output)
        if metrics["output_bytes"] > lim["output_mb"] * 1048576:
            return SandboxResult(False, "output_limit", message_for("output_limit"), metrics=metrics)
        return SandboxResult(True, output=output, metrics=metrics)

    def run_health_check(self):
        """A tiny isolated process (no render): it must start, write its workspace, and be refused the
        application's files and the network. Cached in self.health."""
        started = time.time()
        try:
            runtime = self.runtime()
        except SandboxUnavailable as e:
            self.health = {"ok": False, "reason": str(e)}
            return self.health
        probe = ("import json, socket\nr = {}\nsys.path[0:0] = job.get('python_path') or []\n"
                 "try:\n    from importlib.metadata import version\n    r['manim'] = version('manim')\nexcept Exception: r['manim'] = 'missing'\n"
                 f"try:\n    open({os.path.abspath('.env')!r}).read(1); r['app_files'] = 'readable'\nexcept Exception: r['app_files'] = 'denied'\n"
                 "try:\n    socket.create_connection(('1.1.1.1', 80), timeout=2); r['network'] = 'open'\nexcept Exception: r['network'] = 'denied'\n")
        job_id = uuid.uuid4().hex
        ws = Workspace(self.root, job_id).create("health", job_id)
        try:
            with open(ws.file("runner.py"), "w", encoding="utf-8") as f:
                f.write("import sys, json, os\njob = json.load(open(sys.argv[1]))\n" + probe +
                        "open(os.path.join(job['workspace'], 'result.json'), 'w').write(json.dumps(r))\n")
            with open(ws.file("job.json"), "w", encoding="utf-8") as f:
                json.dump({"workspace": "/workspace" if isinstance(runtime, DockerRuntime) else ws.path,
                           "python_path": [] if isinstance(runtime, DockerRuntime) else [os.path.join(os.path.abspath(sys.prefix), "Lib", "site-packages")]}, f)
            lim = {"timeout": 30, "memory_mb": 256, "cpu_percent": 25, "processes": 1, "workspace_mb": 10, "output_mb": 1}
            stop, _metrics = runtime.run(ws, {}, lim)
            with open(ws.file("result.json"), encoding="utf-8") as f:
                seen = json.load(f)
            ok = not stop and seen.get("app_files") == "denied" and seen.get("network") == "denied" and seen.get("manim") == MANIM_VERSION
            self.health = {"ok": ok, "checked": seen, "seconds": round(time.time() - started, 2),
                           "at": datetime.datetime.utcnow().isoformat() + "Z"}
        except (OSError, ValueError, SandboxUnavailable) as e:
            self.health = {"ok": False, "reason": str(e)}
        finally:
            ws.remove()
        return self.health

    def sweep(self, is_active, older_than=600):
        removed = sweep_orphans(self.root, is_active, older_than)
        if isinstance(self._runtime, DockerRuntime):
            self._runtime.sweep()
        return removed
