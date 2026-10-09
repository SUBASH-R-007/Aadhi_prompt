"""The in-sandbox hardening block: assembly, the audit hook, the frame budget and the profile lock.

The fast tests check assembly and cache-key independence. The slow tests run deliberately hostile
scripts (bypassing the AST guard) through the real subprocess sandbox and prove each is refused.
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from aadhi.manim.aadhi_scene import (
    END_MARKER,
    assemble_script,
    build_config,
    build_hardening,
    hardening_source,
    runtime_source,
    scene_class_name,
    user_line_offset,
)
from aadhi.manim.sandbox import SubprocessRunner

SCENE = "from manim import *\n\nclass Demo(AadhiScene):\n    def construct(self):\n        self.play_step(0, Create(Circle()))\n"


# --- assembly -------------------------------------------------------------------------------------


def test_build_hardening_contents() -> None:
    hard = build_hardening(total_duration=6.0, audit_hook=True, has_latex=True)
    assert hard["frame_limits"] is True and hard["audit_hook"] is True
    assert hard["total_duration"] == 6.0 and hard["programs"] == ["latex", "dvisvgm"]
    assert build_hardening(total_duration=6.0, has_latex=False)["programs"] == []
    assert ast.literal_eval(repr(hard)) == hard  # stays a one-line literal in the header
    with pytest.raises(ValueError, match="finite"):
        build_hardening(total_duration=float("inf"))


def test_assemble_with_hardening_tracks_user_lines_and_parses() -> None:
    cfg = build_config(beat_times=[0.5], total_duration=3.0)
    hard = build_hardening(total_duration=3.0)
    script = assemble_script(SCENE, cfg, hard)
    assert hardening_source() in script and runtime_source() in script
    offset = user_line_offset(script)
    lines = script.splitlines()
    assert lines[offset] == "from manim import *" and lines[offset - 1] == END_MARKER
    header = ast.literal_eval(lines[1].split("=", 1)[1].strip())  # _AADHI_CFG stays one line
    assert header["user_line_offset"] == offset
    assert "_AADHI_HARDEN = {" in script
    ast.parse(script)  # the whole assembled script is valid Python


def test_hardening_is_not_in_the_runtime_source() -> None:
    """The hardening block must not change render cache keys (source_revision hashes the runtime only)."""
    assert "_aadhi_h_install_audit" not in runtime_source()
    assert "addaudithook" in hardening_source()
    plain = assemble_script(SCENE, build_config(beat_times=[0.5], total_duration=3.0))
    assert "_AADHI_HARDEN" not in plain  # omitted when no hardening is requested


# --- real sandbox (slow) --------------------------------------------------------------------------


def _hardened(body: str, *, total: float = 3.0, beats=(0.5,), latex: bool = False) -> str:
    cfg = build_config(beat_times=list(beats), total_duration=total, has_latex=latex, quality="l")
    hard = build_hardening(total_duration=total, audit_hook=True, has_latex=latex)
    return assemble_script(body, cfg, hard)


async def _run(body: str, *, total: float = 3.0, beats=(0.5,), latex: bool = False, timeout: float = 60):
    runner = SubprocessRunner(memory_limit_mb=2048, disk_limit_mb=1024, output_limit_mb=300)
    workdir = Path(tempfile.mkdtemp(prefix="aadhi-harden-test-"))
    return await runner.run(_hardened(body, total=total, beats=beats, latex=latex),
                            scene_class_name(body), workdir=workdir, quality="l", timeout=timeout)


NET = ("from manim import *\n\nclass N(AadhiScene):\n    def construct(self):\n"
       "        import socket\n        socket.socket().connect(('1.1.1.1', 80))\n")
FILE = ("from manim import *\n\nclass F(AadhiScene):\n    def construct(self):\n"
        "        open('escape.txt'.join(['../../', '']), 'w').write('x')\n")
PROC = ("from manim import *\n\nclass P(AadhiScene):\n    def construct(self):\n"
        "        import subprocess\n        subprocess.Popen(['whoami'])\n")
RUNAWAY = ("from manim import *\n\nclass R(AadhiScene):\n    def construct(self):\n"
           "        self.add(Dot())\n        self.wait(10000)\n")
PROFILE = ("from manim import *\n\nclass C(AadhiScene):\n    def construct(self):\n"
           "        config.frame_rate = 120\n        self.play(Create(Circle()))\n        self.wait(1)\n")


@pytest.mark.slow
@pytest.mark.parametrize(("body", "category"), [(NET, "blocked"), (FILE, "blocked"), (PROC, "blocked")])
async def test_audit_hook_refuses_network_files_and_processes(body: str, category: str) -> None:
    result = await _run(body)
    assert not result.ok and result.category == category
    assert "[AADHI_BLOCKED]" in result.log


@pytest.mark.slow
async def test_frame_budget_stops_an_overlong_wait() -> None:
    result = await _run(RUNAWAY, total=2.0, beats=(0.3,))
    assert not result.ok and result.category == "frame_limit"
    assert "past the scene length" in result.error_report


@pytest.mark.slow
async def test_profile_lock_stops_a_mid_render_fps_change() -> None:
    result = await _run(PROFILE, total=3.0, beats=(0.3,))
    assert not result.ok and result.category == "profile_limit"


@pytest.mark.slow
async def test_legit_scene_still_renders_with_the_hook_on() -> None:
    good = ("from manim import *\n\nclass G(AadhiScene):\n    def construct(self):\n"
            "        self.play_step(0, Create(Circle()))\n        self.play_step(1, FadeIn(Square()))\n")
    result = await _run(good, total=4.0, beats=(0.3, 2.0))
    assert result.ok and result.video_path is not None and result.frames > 0


# --- process creation through os.posix_spawn (subprocess on Python 3.13+, glibc >= 2.34) --------------------


def test_posix_spawn_is_allowed_only_for_the_tex_programs() -> None:
    """The audit event of os.posix_spawn / posix_spawnp is (path, argv, env): the same program rule as Popen."""
    import sys

    g: dict = {"_AADHI_HARDEN": {"audit_hook": False, "frame_limits": False, "programs": []}}
    exec(compile(hardening_source(), "hardening", "exec"), g)  # noqa: S102 - our own source, no hook installed
    policy = g["_aadhi_h_make_policy"]({"programs": [sys.executable]})
    decide = g["_aadhi_h_decide"]
    allowed = policy["programs"][next(iter(policy["programs"]))]
    assert decide(policy, "os.posix_spawn", (allowed, [allowed, "-c", "pass"], {})) == ""
    other = "/bin/sh" if sys.platform != "win32" else r"C:\Windows\System32\cmd.exe"
    assert decide(policy, "os.posix_spawn", (other, [other, "-c", "true"], {})) == "os.posix_spawn"
    assert decide(policy, "os.posix_spawn", ("python", ["python"], {})) == "os.posix_spawn"  # posix_spawnp, bare
    assert decide(policy, "os.posix_spawn", (allowed, [other], {})) == "os.posix_spawn"  # argv[0] must match too
    assert decide(policy, "os.posix_spawn", ()) == "os.posix_spawn"
    assert decide(policy, "os.system", ("ls",)) == "os.system"  # the other process events stay refused


@pytest.mark.skipif(__import__("sys").platform != "linux" or not hasattr(__import__("os"), "posix_spawn"),
                    reason="os.posix_spawn through subprocess is a Linux (glibc) path")
def test_allowed_program_runs_through_posix_spawn_under_the_hook(tmp_path: Path) -> None:
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(f"""
        import os, subprocess
        g = {{"_AADHI_HARDEN": {{"audit_hook": True, "frame_limits": False, "programs": ["true"]}},
              "_aadhi_subprocess": subprocess}}
        exec(compile({hardening_source()!r}, "hardening", "exec"), g)
        # close_fds=False takes the posix_spawn path on 3.11/3.12; it is the default path on 3.13+
        assert g["_aadhi_subprocess"].run(["true"], stdout=subprocess.DEVNULL, close_fds=False).returncode == 0
        try:
            os.posix_spawn("/bin/sh", ["/bin/sh", "-c", "true"], {{}})
        except PermissionError:
            print("refused")
        """)
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "refused" in out.stdout and "[AADHI_BLOCKED] os.posix_spawn" in out.stderr
