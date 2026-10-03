"""Security tests of the Manim sandbox (Phase 10): what untrusted code can and cannot do, verified by running it.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest tests.test_manim_sandbox -v

Most probes bypass every Python-level defence on purpose: they run as the sandboxed program itself (no AST
check, no audit hook), so a "denied" here is the operating system's isolation (AppContainer + Job Object)
refusing, not a filter. A second group runs real Manim scenes through the normal runner (audit hook on)
and checks the categories the page shows. The probes are harmless — they only *try* (read a canary file,
connect to a local canary listener, start `cmd /c echo`, allocate memory) inside a throwaway workspace;
nothing on this machine is changed and no outside service is contacted beyond a connection attempt that
the sandbox must refuse. Without a secure runtime (no AppContainer support, no Docker) they are skipped.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from backend_env import REPO, TMP  # first: throwaway database and sandbox folder

import manim_security as security  # noqa: E402
from manim_sandbox import ManimSandbox, SandboxUnavailable, Workspace  # noqa: E402

SANDBOX = ManimSandbox()
try:
    RUNTIME = SANDBOX.runtime()
except SandboxUnavailable as e:
    RUNTIME, WHY = None, str(e)
else:
    WHY = ""

PROBE_HEAD = r'''
import json, os, socket, sys, time
job = json.load(open(sys.argv[1]))
WS = job["workspace"]
R = {}
def check(name, fn):
    try:
        out = fn()
        R[name] = "allowed" + ("" if out is None else ": " + str(out)[:120])
    except BaseException as e:
        R[name] = "denied: %s %s" % (type(e).__name__, getattr(e, "winerror", "") or "")
def connect(host, port, timeout=2.0):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return "connected"
    finally:
        s.close()
'''
PROBE_TAIL = '''
open(os.path.join(WS, "result.json"), "w").write(json.dumps(R))
'''


def lim(**overrides):
    base = security.limits("preview")
    base.update(overrides)
    return base


def pid_alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True).stdout
    return f'"{pid}"' in out


@unittest.skipUnless(RUNTIME is not None, f"no secure Manim runtime here: {WHY}")
class OsIsolationTest(unittest.TestCase):
    """The sandboxed program itself, with no Python-level defences: only the OS isolation stands in the way."""

    @classmethod
    def setUpClass(cls):
        cls.canary = tempfile.mkdtemp(prefix="aadhi-canary-", dir=TMP)
        with open(os.path.join(cls.canary, "secret.txt"), "w") as f:
            f.write("canary-secret-7f3a")

    def probe(self, body, limits=None, prepare=None, started=None):
        job_id = uuid.uuid4().hex
        ws = Workspace(SANDBOX.root, job_id).create("test", job_id)
        try:
            with open(ws.file("runner.py"), "w", encoding="utf-8") as f:
                f.write(PROBE_HEAD + body + PROBE_TAIL)
            with open(ws.file("job.json"), "w", encoding="utf-8") as f:
                json.dump({"workspace": ws.path}, f)
            if prepare:
                prepare(ws)
            info = {}
            stop, metrics = RUNTIME.run(ws, {}, limits or lim(timeout=30), on_start=lambda i: info.update(i))
            result = {}
            if os.path.isfile(ws.file("result.json")):
                with open(ws.file("result.json"), encoding="utf-8") as f:
                    result = json.load(f)
            if started:
                started(info)
            return stop, metrics, result, info
        finally:
            ws.remove()

    def assertDenied(self, result, *names):
        for name in names:
            self.assertIn(name, result, result)
            self.assertTrue(result[name].startswith("denied"), f"{name}: {result[name]}")

    # ---- files ----------------------------------------------------------------------------------------------

    def test_application_files_are_unreadable(self):
        env_file, db_file, source = (os.path.join(REPO, n) for n in (".env", "projects.db", "server.py"))
        stop, _m, r, _i = self.probe(f'''
check("env", lambda: open({env_file!r}).read(1))
check("db", lambda: open({db_file!r}, "rb").read(1))
check("source", lambda: open({source!r}).read(1))
check("repo_listing", lambda: os.listdir({REPO!r}))
check("canary", lambda: open({os.path.join(self.canary, "secret.txt")!r}).read())
check("home", lambda: os.listdir({os.path.dirname(REPO)!r}))
check("user_profile", lambda: os.listdir({os.path.expanduser("~")!r}))
check("own_home", lambda: os.listdir(os.path.expanduser("~")))  # the sandbox's own empty home (ws/home)
''')
        self.assertIsNone(stop)
        self.assertDenied(r, "source", "repo_listing", "canary", "home", "user_profile")
        self.assertEqual(r["own_home"], "allowed: []")
        for name in ("env", "db"):  # a missing file cannot leak either, but where it exists it must be refused
            if os.path.exists(os.path.join(REPO, ".env" if name == "env" else "projects.db")):
                self.assertDenied(r, name)

    def test_writes_outside_the_workspace_are_refused(self):
        target = os.path.join(self.canary, "written.txt")
        site = os.path.join(sys.prefix, "Lib", "site-packages", "aadhi_sandbox_test.txt")
        stop, _m, r, _i = self.probe(f'''
check("canary_dir", lambda: open({target!r}, "w").write("x"))
check("runtime", lambda: open({site!r}, "w").write("x"))
check("repo", lambda: open({os.path.join(REPO, "aadhi_sandbox_test.txt")!r}, "w").write("x"))
check("workspace", lambda: open(os.path.join(WS, "output", "ok.txt"), "w").write("fine"))
''')
        self.assertDenied(r, "canary_dir", "runtime", "repo")
        self.assertEqual(r["workspace"], "allowed: 4")
        self.assertFalse(os.path.exists(target) or os.path.exists(site) or os.path.exists(os.path.join(REPO, "aadhi_sandbox_test.txt")))

    def test_path_traversal_and_absolute_paths(self):
        stop, _m, r, _i = self.probe(f'''
check("traversal", lambda: open(os.path.join(WS, "..", "..", "..", "..", "secret.txt")).read())
check("traversal_repo", lambda: open(os.path.join(WS, *[".."] * 12, {os.path.relpath(os.path.join(REPO, "server.py"), os.path.splitdrive(REPO)[0] + os.sep)!r})).read(1))
check("other_job", lambda: os.listdir(os.path.join(WS, "..")))
check("absolute", lambda: open({os.path.join(self.canary, "secret.txt")!r}).read())
''')
        self.assertDenied(r, "traversal_repo", "other_job", "absolute")
        self.assertTrue(r["traversal"].startswith("denied"), r)

    def test_links_do_not_lead_out(self):
        """A junction planted in the workspace (the sandbox cannot make one itself) still reaches nothing: the
        target's own permissions apply. The sandbox's own attempt to create links is refused."""
        import _winapi

        def plant(ws):
            _winapi.CreateJunction(self.canary, ws.file("source", "link"))
        stop, _m, r, _i = self.probe(f'''
check("through_junction", lambda: open(os.path.join(WS, "source", "link", "secret.txt")).read())
check("make_symlink", lambda: os.symlink({self.canary!r}, os.path.join(WS, "temp", "s")))
''', prepare=plant)
        self.assertDenied(r, "through_junction", "make_symlink")

    def test_output_link_is_not_accepted(self):
        import _winapi
        ws = Workspace(SANDBOX.root, uuid.uuid4().hex).create("test", "x")
        try:
            os.rmdir(ws.file("output"))
            _winapi.CreateJunction(self.canary, ws.file("output"))
            shutil.copyfile(os.path.join(self.canary, "secret.txt"), os.path.join(self.canary, "scene.mp4"))
            self.assertIsNone(ws.safe_output())  # the output is never read through a link
        finally:
            ws.remove()
            if os.path.exists(os.path.join(self.canary, "scene.mp4")):
                os.remove(os.path.join(self.canary, "scene.mp4"))
        self.assertTrue(os.path.exists(os.path.join(self.canary, "secret.txt")))  # removing the workspace kept the target

    # ---- network ----------------------------------------------------------------------------------------------

    def test_network_is_refused(self):
        hits = []
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        server.settimeout(0.2)
        port = server.getsockname()[1]
        done = threading.Event()

        def accept():
            while not done.is_set():
                try:
                    conn, _ = server.accept()
                    hits.append(1)
                    conn.close()
                except OSError:
                    pass
        threading.Thread(target=accept, daemon=True).start()
        try:
            socket.create_connection(("127.0.0.1", port), timeout=2).close()  # control: the listener works
            time.sleep(0.3)
            control = len(hits)
            stop, _m, r, _i = self.probe(f'''
check("http", lambda: connect("1.1.1.1", 80))
check("https", lambda: connect("1.1.1.1", 443))
check("localhost_canary", lambda: connect("127.0.0.1", {port}))
check("localhost_app", lambda: connect("127.0.0.1", 9942, 1))
check("ipv6_loopback", lambda: connect("::1", {port}, 1))
check("private_10", lambda: connect("10.0.0.1", 80, 1))
check("private_192", lambda: connect("192.168.1.1", 80, 1))
check("metadata", lambda: connect("169.254.169.254", 80, 1))
check("udp", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("1.1.1.1", 53)) and connect("1.1.1.1", 53))
check("listen", lambda: socket.create_server(("0.0.0.0", 0)).getsockname())
''', limits=lim(timeout=60))
        finally:
            done.set()
            server.close()
        self.assertEqual(control, 1)
        self.assertEqual(len(hits), control, "the sandbox reached the local listener")
        self.assertDenied(r, "http", "https", "localhost_canary", "localhost_app", "ipv6_loopback", "private_10", "private_192",
                          "metadata", "udp")
        # The public internet is refused by policy (WSAEACCES), not merely unreachable
        self.assertIn("10013", r["http"])
        self.assertIn("10013", r["https"])
        # Listening is possible only on the AppContainer's own isolated loopback: nothing outside can connect to it
        self.__class__.listen_result = r["listen"]

    # ---- processes ---------------------------------------------------------------------------------------------

    def test_processes_cannot_be_started(self):
        stop, metrics, r, _i = self.probe('''
import subprocess
mark = os.path.join(WS, "temp", "child-ran.txt")
check("subprocess", lambda: subprocess.run(["cmd", "/c", "echo ran>" + mark], capture_output=True).returncode)
check("python_child", lambda: subprocess.Popen([sys.executable, "-c", "open(%r, 'w').write('ran')" % mark]).wait())
check("os_system", lambda: os.system("echo ran>" + mark) or "exit-code-0")
check("startfile", lambda: os.startfile("notepad.exe"))
R["child_ran"] = os.path.exists(mark)
''')
        self.assertDenied(r, "subprocess", "python_child", "startfile")
        self.assertNotEqual(r["os_system"], "allowed: exit-code-0")
        self.assertFalse(r["child_ran"])  # no child ever did anything
        self.assertEqual(metrics["processes_left"], 0)

    def test_the_whole_process_tree_is_killed(self):
        """With a child allowed (processes=3 for this test only), a runaway parent and its child both die at the
        timeout: the Job Object kills the tree, nothing is left behind."""
        pids = {}

        def seen(_info):
            pass
        stop, metrics, r, info = self.probe('''
import subprocess
child = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time; time.sleep(120)"])
open(os.path.join(WS, "temp", "child.pid"), "w").write(str(child.pid))
R["child"] = child.pid
open(os.path.join(WS, "result.json"), "w").write(json.dumps(R))
while True:
    pass
''', limits=lim(timeout=4, processes=3), prepare=lambda ws: pids.update(ws=ws))
        self.assertEqual(stop, "timeout")
        self.assertEqual(metrics["processes_left"], 0)
        self.assertGreaterEqual(metrics["processes_started"], 2)
        self.assertIn("child", r)
        time.sleep(0.5)
        self.assertFalse(pid_alive(r["child"]), "the child survived")
        self.assertFalse(pid_alive(info["pid"]), "the sandboxed program survived")

    # ---- resources -------------------------------------------------------------------------------------------------

    def test_memory_is_limited(self):
        stop, metrics, r, _i = self.probe('''
def grab():
    blocks = []
    for _ in range(40):
        blocks.append(bytearray(50 * 1024 * 1024))
    return len(blocks)
check("memory", grab)
''', limits=lim(timeout=30, memory_mb=256))
        self.assertDenied(r, "memory")
        self.assertIn("MemoryError", r["memory"])
        self.assertLess(metrics["peak_memory_mb"], 300)

    def test_infinite_loop_is_stopped_at_the_timeout(self):
        started = time.time()
        stop, metrics, _r, info = self.probe("while True:\n    pass\n", limits=lim(timeout=3))
        self.assertEqual(stop, "timeout")
        self.assertLess(time.time() - started, 8)
        self.assertEqual(metrics["processes_left"], 0)
        self.assertFalse(pid_alive(info["pid"]))

    def test_cpu_rate_is_capped(self):
        stop, metrics, _r, _i = self.probe('''
end = time.time() + 4
n = 0
while time.time() < end:
    n += 1
R["n"] = n
''', limits=lim(timeout=30, cpu_percent=4))
        self.assertIsNone(stop)
        # 4 % of a 12-thread machine is about half a core: a busy loop gets well under the 4 s it ran
        self.assertLess(metrics["cpu_seconds"], metrics["seconds"] * 0.8, metrics)

    def test_output_size_is_limited(self):
        stop, _m, _r, _i = self.probe('''
with open(os.path.join(WS, "output", "big.bin"), "wb") as f:
    for _ in range(30):
        f.write(b"\\0" * 1048576)
time.sleep(5)
''', limits=lim(timeout=30, workspace_mb=10, output_mb=5))
        self.assertEqual(stop, "output_limit")

    # ---- identity and environment -------------------------------------------------------------------------------

    def test_no_secrets_in_the_environment(self):
        os.environ["AADHI_TEST_SECRET"] = "must-not-leak-1234"
        try:
            _s, _m, r, _i = self.probe('R["env"] = sorted(os.environ)\nR["values"] = "must-not-leak-1234" in repr(dict(os.environ))\n')
        finally:
            del os.environ["AADHI_TEST_SECRET"]
        names = set(r["env"])
        self.assertFalse(r["values"])
        self.assertNotIn("AADHI_TEST_SECRET", names)
        for name in names:
            self.assertFalse(any(word in name.upper() for word in ("SECRET", "KEY", "TOKEN", "PASSWORD", "JWT", "DATABASE")), name)
        self.assertLessEqual(names, {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "HOME", "LOCALAPPDATA",
                                     "APPDATA", "HOMEDRIVE", "HOMEPATH", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE",
                                     "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                                     "MPLBACKEND", "PYTHONHASHSEED"})

    def test_runs_in_an_appcontainer_with_low_integrity(self):
        _s, _m, r, _i = self.probe('''
try:
    import ctypes
except BaseException as e:
    R["error"] = repr(e)
import ctypes
from ctypes import wintypes as wt
k32, adv = ctypes.WinDLL("kernel32"), ctypes.WinDLL("advapi32")
k32.GetCurrentProcess.restype = wt.HANDLE
adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
token = wt.HANDLE()
adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token))
flag, size = wt.DWORD(), wt.DWORD()
adv.GetTokenInformation(token, 29, ctypes.byref(flag), 4, ctypes.byref(size))  # TokenIsAppContainer
R["appcontainer"] = flag.value
buf = ctypes.create_string_buffer(64)
adv.GetTokenInformation(token, 25, buf, 64, ctypes.byref(size))  # TokenIntegrityLevel
sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
text = wt.LPWSTR()
adv.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(text))
R["integrity"] = text.value
''')
        self.assertNotIn("error", r, r)
        self.assertEqual(r["appcontainer"], 1)
        self.assertEqual(r["integrity"], "S-1-16-4096")  # low mandatory level

    def test_registry(self):
        """The OS boundary refuses every registry write and the autostart keys. Windows lets any AppContainer
        *read* some keys (installed software, the user's environment variables): a documented residual risk
        that the Python layers close for generated code (see RunnerTest.test_registry_is_refused_by_the_hook)."""
        _s, _m, r, _i = self.probe('''
import winreg
check("autostart", lambda: winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Run"))
check("write_user", lambda: winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\\AadhiSandboxTest"))
check("write_machine", lambda: winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\AadhiSandboxTest"))
''')
        self.assertDenied(r, "autostart", "write_user", "write_machine")

    def test_health_check(self):
        health = SANDBOX.run_health_check()
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["checked"], {"app_files": "denied", "network": "denied", "manim": security.MANIM_VERSION})


# ---- the normal runner: real Manim scenes, audit hook on ---------------------------------------------------------

def scene(body, name="Probe", base="Scene", imports=""):
    return f"from manim import *\n{imports}\n\nclass {name}({base}):\n    def construct(self):\n" + \
        "".join("        " + line + "\n" for line in body.strip().splitlines())


LEGIT = {
    "text": scene('t = Text("Photosynthesis", font_size=48)\nself.play(Write(t))\nself.play(t.animate.to_edge(UP))'),
    "formula": scene('f = Text("E = m c²", font_size=60)\nself.play(FadeIn(f))\nself.play(f.animate.scale(1.3))'),
    "graph": scene('ax = Axes(x_range=[0, 5], y_range=[0, 10], x_length=6, y_length=4)\n'
                   'g = ax.plot(lambda x: x ** 2 / 2.5, color=YELLOW)\nself.play(Create(ax), Create(g))'),
    "shapes": scene('s = Square(color=BLUE)\nc = Circle(color=RED)\nself.play(Create(s))\nself.play(Transform(s, c))'),
    "many_objects": scene('dots = VGroup(*[Dot(point=[x * 0.5 - 3, y * 0.5 - 2, 0]) for x in range(13) for y in range(9)])\n'
                          'self.play(FadeIn(dots, lag_ratio=0.01))'),
    "camera": scene('sq = Square()\nself.add(sq)\nself.play(self.camera.frame.animate.move_to(sq).scale(0.5))',
                    base="MovingCameraScene"),
    "diagram": scene('a = RoundedRectangle(corner_radius=0.2, width=2.5, height=1).shift(LEFT * 3)\n'
                     'b = a.copy().shift(RIGHT * 6)\nla = Text("Input", font_size=28).move_to(a)\n'
                     'lb = Text("Output", font_size=28).move_to(b)\narrow = Arrow(a.get_right(), b.get_left())\n'
                     'self.play(Create(a), Create(b), Write(la), Write(lb))\nself.play(GrowArrow(arrow))',
                     imports="import numpy as np"),
}


@unittest.skipUnless(RUNTIME is not None, f"no secure Manim runtime here: {WHY}")
class RunnerTest(unittest.TestCase):
    """Real renders through the normal path: static checks, runner (audit hook), sandbox, output checks."""

    def render(self, code, limits=None, check=True, audit_hook=True):
        limits = limits or lim()
        code = security.fix_code(code)
        scenes = security.check_source(code, limits) if check else ["Probe"]
        job_id = uuid.uuid4().hex
        ws, job = SANDBOX.prepare(job_id, "test", job_id, code, scenes[0], limits, audit_hook=audit_hook)
        try:
            result = SANDBOX.execute(ws, job, limits)
            info = None
            if result.ok:
                from media import inspect_media
                info = inspect_media(result.output)
                info["fps"] = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                              "stream=r_frame_rate", "-of", "csv=p=0", result.output],
                                             capture_output=True, text=True).stdout.strip()
            return result, info
        finally:
            ws.remove()

    def test_legitimate_scenes_render(self):
        for name, code in LEGIT.items():
            with self.subTest(name):
                result, info = self.render(code)
                self.assertTrue(result.ok, f"{name}: {result.category} {result.log[-800:]}")
                self.assertEqual((info["width"], info["height"], info["fps"]), (854, 480, "15/1"))
                self.assertEqual(result.metrics["processes_left"], 0)

    def test_hook_refuses_files_network_processes(self):
        env_file = os.path.join(REPO, ".env")
        cases = {
            "filesystem_access_denied": scene(f'open({env_file!r}).read()'),
            "network_denied": scene('import socket\nsocket.create_connection(("1.1.1.1", 80), timeout=2)'),
            "process_denied": scene('import os\nos.system("echo hi")'),
            "unsafe_code": scene('import ctypes\nctypes.CDLL("kernel32")'),
        }
        for category, code in cases.items():
            with self.subTest(category):
                result, _ = self.render(code, check=False)  # past the static checks: the runtime must refuse
                self.assertFalse(result.ok)
                self.assertEqual(result.category, category, result.log[-600:])

    def test_registry_is_refused_by_the_hook(self):
        code = scene('import winreg\nwinreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment"), "Path")')
        with self.assertRaises(security.UnsafeCode):  # refused before it runs...
            security.check_source(code, lim())
        result, _ = self.render(code, check=False)  # ...and, past the static check, while it runs
        self.assertEqual(result.category, "unsafe_code", result.log[-600:])

    def test_os_categories_without_the_hook(self):
        """Without the audit hook the OS refuses, and the refusal is still classified for the page."""
        cases = {
            "network_denied": scene('import socket\nsocket.create_connection(("1.1.1.1", 80), timeout=2)'),
            "process_denied": scene('import subprocess\nsubprocess.run(["cmd", "/c", "echo"])'),
            "filesystem_access_denied": scene(f'open({os.path.join(REPO, "server.py")!r}).read()'),
        }
        for category, code in cases.items():
            with self.subTest(category):
                result, _ = self.render(code, check=False, audit_hook=False)
                self.assertEqual(result.category, category, result.log[-600:])

    def test_memory_limit(self):
        result, _ = self.render(scene('import numpy as np\nx = np.ones((20000, 20000))\nself.add(Text(str(x.sum())))'),
                                limits=lim(memory_mb=512))
        self.assertEqual(result.category, "memory_limit", result.log[-600:])
        self.assertLess(result.metrics["peak_memory_mb"], 560)

    def test_frame_limit(self):
        result, _ = self.render(scene('self.add(Square())\nself.wait(30)'), limits=lim(max_duration=2, max_frames=30))
        self.assertEqual(result.category, "frame_limit", result.log[-600:])

    def test_resolution_and_fps_cannot_be_raised(self):
        code = ("from manim import *\nconfig.pixel_width = 3840\nconfig.pixel_height = 2160\nconfig.frame_rate = 120\n\n"
                "class Probe(Scene):\n    def construct(self):\n        self.play(Create(Circle()))\n")
        result, info = self.render(code)
        self.assertTrue(result.ok, result.log[-600:])
        self.assertEqual((info["width"], info["height"], info["fps"]), (854, 480, "15/1"))  # the profile won
        for body, category in (('config.pixel_width = 3840\nself.play(Create(Circle()))', "resolution_limit"),
                               ('config.frame_rate = 120\nself.play(Create(Circle()))', "fps_limit")):
            result, _info = self.render(scene(body))  # changed while rendering: stopped before a frame is written
            self.assertEqual(result.category, category, result.log[-600:])

    def test_timeout_kills_a_scene(self):
        started = time.time()
        result, _ = self.render(scene('while True:\n    pass'), limits=lim(timeout=6), check=False)
        self.assertEqual(result.category, "timeout")
        self.assertLess(time.time() - started, 15)
        self.assertEqual(result.metrics["processes_left"], 0)


class StaticCheckTest(unittest.TestCase):
    """The static checks: an early, friendly refusal (defence in depth — the sandbox is the boundary)."""

    def refused(self, code, category="unsafe_code", limits=None):
        with self.assertRaises(security.UnsafeCode) as caught:
            security.check_source(code, limits or lim())
        self.assertEqual(caught.exception.category, category, caught.exception.detail)

    def test_escape_attempts_are_refused(self):
        for code in [
            "import os\nfrom manim import *\nclass A(Scene):\n    def construct(self): pass",
            "import subprocess\nfrom manim import *\nclass A(Scene):\n    def construct(self): pass",
            "from manim import *\nclass A(Scene):\n    def construct(self): eval('1')",
            "from manim import *\nclass A(Scene):\n    def construct(self): exec('x=1')",
            "from manim import *\nclass A(Scene):\n    def construct(self): __import__('os')",
            "from manim import *\nimport importlib\nclass A(Scene):\n    def construct(self): pass",
            "from manim import *\nimport ctypes\nclass A(Scene):\n    def construct(self): pass",
            "from manim import *\nclass A(Scene):\n    def construct(self): ().__class__.__bases__[0].__subclasses__()",
            "from manim import *\nclass A(Scene):\n    def construct(self): getattr(self, '__globals__')",
            "from manim import *\nclass A(Scene):\n    def construct(self): open('x.txt', 'w')",
            "from manim import *\nimport numpy as np\nclass A(Scene):\n    def construct(self): np.load('x.npy')",
            "from manim import *\nclass A(Scene):\n    def construct(self): globals()['os']",
            "from manim import *\nfrom os import system\nclass A(Scene):\n    def construct(self): pass",
            "from manim import *\nimport socket\nclass A(Scene):\n    def construct(self): pass",
        ]:
            with self.subTest(code.splitlines()[-1] if "import" not in code.splitlines()[0] else code.splitlines()[0]):
                self.refused(code)

    def test_limits(self):
        many = "from manim import *\n" + "".join(f"class S{i}(Scene):\n    def construct(self): pass\n" for i in range(5))
        self.refused(many, "scene_limit")
        self.refused("from manim import *\nclass A(Scene):\n    def construct(self): pass\n" + "# x\n" * 60000, "source_limit")
        self.refused("from manim import *\nclass A(Scene):\n    def construct(self)\n", "invalid_code")
        self.refused("from manim import *\nx = 1\n", "invalid_code")

    def test_legitimate_code_passes(self):
        for name, code in LEGIT.items():
            with self.subTest(name):
                self.assertEqual(security.check_source(security.fix_code(code), lim()), ["Probe"])

    def test_profiles_are_capped(self):
        env = {"MANIM_MAX_RESOLUTION": "7680x4320", "MANIM_MAX_FPS": "240", "MANIM_TIMEOUT": "99999", "MANIM_MEMORY_LIMIT_MB": "64000"}
        capped = security.limits("high_quality", env)
        self.assertLessEqual((capped["width"], capped["height"], capped["fps"]), (1920, 1080, 60))
        self.assertLessEqual(capped["timeout"], security.HARD_MAX["timeout"])
        self.assertLessEqual(capped["memory_mb"], security.HARD_MAX["memory_mb"])
        with self.assertRaises(ValueError):
            security.limits("ultra")

    def test_fingerprint_follows_code_version_and_profile(self):
        code = security.fix_code(LEGIT["shapes"])
        a, _ = security.fingerprint(code, security.limits("preview"), "Probe")
        self.assertEqual(a, security.fingerprint(code.replace("\n", "\r\n").replace("\r\n", "\n"), security.limits("preview"), "Probe")[0])
        self.assertNotEqual(a, security.fingerprint(code, security.limits("standard"), "Probe")[0])
        self.assertNotEqual(a, security.fingerprint(code + "\n# changed", security.limits("preview"), "Probe")[0])


class UnavailableTest(unittest.TestCase):
    def test_no_unsafe_fallback(self):
        for env in ({"MANIM_SANDBOX_ENABLED": "0"}, {"MANIM_SANDBOX_RUNTIME": "subprocess"}, {"MANIM_SANDBOX_RUNTIME": "none"}):
            with self.subTest(env):
                sandbox = ManimSandbox({**env, "MANIM_SANDBOX_DIR": os.path.join(TMP, "unavailable")})
                with self.assertRaises(SandboxUnavailable):
                    sandbox.runtime()
                status = sandbox.status()
                self.assertFalse(status["available"])
                self.assertEqual(status["message"], "Secure Manim execution is unavailable in this deployment.")

    def test_docker_command_is_locked_down(self):
        from manim_sandbox import DockerRuntime
        ws = Workspace(os.path.join(TMP, "docker-cmd"), uuid.uuid4().hex).create("t", "t")
        try:
            cmd = DockerRuntime(os.path.join(TMP, "docker-cmd"), {}).command(ws, lim(), "aadhi-manim-test")
        finally:
            ws.remove()
        text = " ".join(cmd)
        for flag in ("--network none", "--read-only", "--cap-drop ALL", "no-new-privileges", "--pids-limit", "--memory",
                     "--cpus", "--user 10001"):
            self.assertIn(flag, text)
        self.assertEqual(sum(1 for part in cmd if part == "-v"), 1)  # only the job workspace is mounted
        self.assertNotIn("--privileged", text)
        self.assertNotIn("-e", cmd)  # no environment passed in


if __name__ == "__main__":
    unittest.main()
