"""Sandbox runners: command lines, environment allow-lists, process supervision (timeouts, caps, kills)."""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import threading
import time
from pathlib import Path

import psutil
import pytest

from aadhi.manim import sandbox
from aadhi.manim.aadhi_scene import assemble_script, build_config
from aadhi.manim.base import ManimError
from aadhi.manim.sandbox import (
    OUTPUT_CAP,
    OWNER_MARKER,
    DockerRunner,
    SandboxResult,
    SubprocessRunner,
    dir_size_mb,
    dvisvgm_flags,
    failed_result,
    find_output,
    get_runner,
    kill_tree,
    latex_errors,
    log_tail,
    manim_args,
    run_in_thread,
    run_process,
    scrub_paths,
    sweep_orphans,
    tex_flags,
    valid_scene_name,
    write_owner_marker,
)

PY = sys.executable
SECRETS = {
    "OPENAI_API_KEY": "sk-should-never-leak-1",
    "GEMINI_API_KEY": "gm-should-never-leak-2",
    "AWS_SECRET_ACCESS_KEY": "aws-should-never-leak-3",
    "JWT_SECRET": "jwt-should-never-leak-4",
    "DATABASE_URL": "postgresql://u:pw-should-never-leak@db/x",
}


def pairs(cmd: list[str]) -> set[tuple[str, str]]:
    return set(zip(cmd, cmd[1:]))


# --- command lines --------------------------------------------------------------------------------


def test_manim_args_match_the_manim_cli() -> None:
    args = manim_args("scene.py", "Demo", media_dir="m", quality="l")
    assert args == ["render", "-ql", "--format", "mp4", "--disable_caching", "--media_dir", "m", "-o", "out",
                    "--progress_bar", "none", "-v", "WARNING", "--silent", "scene.py", "Demo"]
    with pytest.raises(ValueError, match="quality"):
        manim_args("s.py", "Demo", media_dir="m", quality="k")
    for bad in ("1Demo", "De mo", "Demo;rm", "", "-Demo", "A" * 65, "\u0b93\u0bae\u0bcd", "Ohm\u00e9", "a.b"):
        assert not valid_scene_name(bad)
        with pytest.raises(ValueError, match="scene class"):
            manim_args("s.py", bad, media_dir="m", quality="m")
    for good in ("Ohms_Law", "Scene2", "_Private", "A" * 64, "x"):
        assert valid_scene_name(good)
        assert manim_args("s.py", good, media_dir="m", quality="m")[-1] == good


def test_scene_name_rules_of_guard_and_sandbox_agree() -> None:
    from aadhi.manim.guard import SCENE_NAME

    for name in ("Ohms_Law", "Scene2", "A" * 64, "x", "_Private", "1Demo", "\u0b93\u0bae\u0bcd", "A" * 65,
                 "Ohm\u00e9"):
        if SCENE_NAME.fullmatch(name):
            assert valid_scene_name(name), name  # everything the guard accepts reaches the CLI


async def test_runners_turn_invalid_arguments_into_failed_results(tmp_path: Path) -> None:
    for runner in (SubprocessRunner(python=str(tmp_path / "never-started.exe")),
                   DockerRunner("img", docker="aadhi-never-started-docker")):
        result = await runner.run("x", "Ohm\u00e9", workdir=tmp_path / runner.name, quality="l", timeout=5)
        assert not result.ok and result.returncode is None and result.video_path is None
        assert "invalid scene class name" in result.error_report and "[AADHI_ERROR]" in result.log
    assert failed_result("boom").error_report == "boom"


def test_subprocess_command_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DOCKER_HOST", "tcp://example:2375")
    runner = SubprocessRunner()
    cmd = runner.build_command(tmp_path / "scene.py", "Demo", tmp_path / "m", "h")
    assert cmd[:4] == [PY, "-m", "manim", "render"] and "-qh" in cmd and cmd[-2:] == [str(tmp_path / "scene.py"), "Demo"]
    env = runner.build_env(tmp_path)
    leaked = [k for k, v in env.items() if any(s in v for s in SECRETS.values())]
    assert not leaked and "OPENAI_API_KEY" not in env and "DOCKER_HOST" not in env
    assert env["TEMP"] == env["TMP"] == str(tmp_path / "tmp") and (tmp_path / "tmp").is_dir()
    assert env["PYTHONIOENCODING"] == "utf-8" and env["NO_COLOR"] == "1"
    assert env["AADHI_TEX_FLAGS"] == tex_flags(sandbox.tex_bin_dir())
    assert env["AADHI_DVISVGM_FLAGS"] == dvisvgm_flags(sandbox.tex_bin_dir())
    assert "PATH" in env
    allowed = set(sandbox.ENV_ALLOWLIST) | {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "NO_COLOR",
                                            "TERM", "COLUMNS", "AADHI_TEX_FLAGS", "AADHI_DVISVGM_FLAGS",
                                            "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"}
    assert set(env) <= allowed
    assert env["OPENBLAS_NUM_THREADS"] == env["OMP_NUM_THREADS"] == env["MKL_NUM_THREADS"] == "1"


def test_docker_command_has_every_isolation_flag(tmp_path: Path) -> None:
    runner = DockerRunner("aadhi-manim-sandbox:test", output_limit_mb=300)
    cmd = runner.build_command(tmp_path, "Demo", "m", "aadhi-manim-abc123")
    assert cmd[:3] == ["docker", "run", "--rm"]
    expected = {
        ("--name", "aadhi-manim-abc123"), ("--network", "none"), ("--memory", "2g"), ("--memory-swap", "2g"),
        ("--cpus", "2"), ("--pids-limit", "256"), ("--user", "1000:1000"), ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"), ("-v", f"{tmp_path.resolve()}:/work:rw"), ("-w", "/work"),
        ("--label", DockerRunner.LABEL),
    }
    assert expected <= pairs(cmd)
    assert "--read-only" in cmd and cmd[cmd.index("--tmpfs") + 1].startswith("/tmp:")
    # sweep label with a deadline, and a file-size ulimit (output cap * 2)
    labels = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--label"]
    assert DockerRunner.LABEL in labels
    deadline = next(lbl for lbl in labels if lbl.startswith("aadhi.manim.deadline="))
    assert int(deadline.split("=")[1]) >= int(time.time())
    assert cmd[cmd.index("--ulimit") + 1] == f"fsize={300 * 2 * 1024 * 1024}"
    image = cmd.index("aadhi-manim-sandbox:test")
    assert cmd[image + 1:image + 5] == ["python", "-m", "manim", "render"]  # no timeout wrapper without a timeout
    assert cmd[-2:] == ["/work/scene.py", "Demo"] and ("--media_dir", "/work/media") in pairs(cmd)
    assert "-qm" in cmd and "--disable_caching" in cmd
    env_flags = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
    assert all(not any(s in e for s in SECRETS.values()) for e in env_flags)
    assert all(k.split("=")[0] in {"HOME", "AADHI_TEX_FLAGS", "PYTHONDONTWRITEBYTECODE", "NO_COLOR"} for k in env_flags)
    for bad in ("", "a b", "-x", "a;b"):
        with pytest.raises(ValueError, match="container name"):
            runner.build_command(tmp_path, "Demo", "m", bad)
    custom = DockerRunner("img", docker="podman", memory="1g", cpus="1", pids_limit=64, user="2000:2000")
    cmd = custom.build_command(tmp_path, "Demo", "l", "c1")
    assert cmd[0] == "podman" and {("--memory", "1g"), ("--cpus", "1"), ("--pids-limit", "64"),
                                    ("--user", "2000:2000")} <= pairs(cmd)


def test_docker_command_wraps_in_an_in_container_timeout(tmp_path: Path) -> None:
    """A deadline wrapper so a sibling container dies even if the docker CLI handle is lost."""
    runner = DockerRunner("img")
    cmd = runner.build_command(tmp_path, "Demo", "l", "aadhi-manim-t1", timeout=120)
    image = cmd.index("img")
    assert cmd[image + 1:image + 5] == ["timeout", "-s", "KILL", str(120 + 30)]
    assert cmd[image + 5:image + 8] == ["python", "-m", "manim"]
    assert cmd[-2:] == ["/work/scene.py", "Demo"]
    labels = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--label"]
    deadline = int(next(lbl for lbl in labels if lbl.startswith("aadhi.manim.deadline=")).split("=")[1])
    assert deadline >= int(time.time()) + 120  # must outlive MANIM_TIMEOUT_SECONDS


def test_docker_cli_env_is_allow_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DOCKER_HOST", "tcp://example:2375")
    env = DockerRunner("img").build_env()
    assert env.get("DOCKER_HOST") == "tcp://example:2375" or env.get("DOCKER_HOST".lower())
    assert not any(s in v for v in env.values() for s in SECRETS.values())


async def test_docker_runner_moves_the_video_out_of_a_private_mount(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run_process(cmd, *, cwd, env, timeout, cancel=None, memory_limit_mb=None, on_kill=None, **_kw):
        mount = Path(cmd[cmd.index("-v") + 1].rsplit(":/work", 1)[0])
        seen.update(mount=mount, script=(mount / "scene.py").read_text(encoding="utf-8"), on_kill=on_kill)
        if os.name != "nt":
            seen["mode"] = mount.stat().st_mode & 0o777
            seen["outer_mode"] = mount.parent.stat().st_mode & 0o777
        out = mount / "media" / "videos" / "scene" / "480p15" / "out.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"video")
        return 0, "rendered", "", 0.1

    monkeypatch.setattr(sandbox, "run_process", fake_run_process)
    result = await DockerRunner("img").run("print('scene')", "Demo", workdir=tmp_path / "w", quality="l", timeout=9)
    assert result.ok and result.video_path == tmp_path / "w" / "out.mp4" and result.video_path.read_bytes() == b"video"
    assert seen["script"] == "print('scene')" and len(seen["on_kill"]) == 1
    assert not seen["mount"].parent.exists(), "the private mount is removed"
    if os.name != "nt":
        assert seen["mode"] == 0o777 and seen["outer_mode"] == 0o700


async def test_missing_binaries_fail_cleanly(tmp_path: Path) -> None:
    docker = await DockerRunner("img", docker="aadhi-no-such-docker-cli").run(
        "x", "Demo", workdir=tmp_path / "d", quality="l", timeout=5)
    assert not docker.ok and docker.returncode is None and "not found" in docker.log and "--network" in docker.command
    local = await SubprocessRunner(python=str(tmp_path / "no-python.exe")).run(
        "x", "Demo", workdir=tmp_path / "s", quality="l", timeout=5)
    assert not local.ok and "not found" in local.log


def test_get_runner(app_env) -> None:
    assert isinstance(get_runner(app_env), SubprocessRunner)
    docker = get_runner(app_env.model_copy(update={"manim_sandbox": "docker", "manim_docker_image": "img:1"}))
    assert isinstance(docker, DockerRunner) and docker.image == "img:1" and docker.has_latex()
    with pytest.raises(ManimError, match="disabled"):
        get_runner(app_env.model_copy(update={"manim_sandbox": "disabled"}))


# --- results and logs -----------------------------------------------------------------------------


def test_error_report_variants() -> None:
    block = "noise\n[AADHI_ERROR]\n  line 3, in construct: x\nNameError: x\n[/AADHI_ERROR]\n! Undefined control sequence."
    assert SandboxResult(False, 1, None, block).error_report == (
        "line 3, in construct: x\nNameError: x\n! Undefined control sequence."
    )
    assert "timed out" in SandboxResult(False, None, None, "", killed_reason="timeout").error_report
    assert "memory" in SandboxResult(False, None, None, "", killed_reason="memory").error_report
    assert "without producing a video" in SandboxResult(False, 0, None, "fine").error_report
    long_log = "\n".join(f"row {i}" for i in range(100))
    assert SandboxResult(False, 1, None, long_log).error_report.splitlines()[0] == "row 60"


def test_log_helpers() -> None:
    assert log_tail("a\n\n b \nc\n", 2) == " b\nc"
    log = "x\n! LaTeX Error: File `foo.sty' not found.\n! Emergency stop.\n! Emergency stop.\nok"
    assert latex_errors(log) == "! LaTeX Error: File `foo.sty' not found.\n! Emergency stop."
    assert latex_errors("all good") == ""


def test_tex_flags(tmp_path: Path) -> None:
    assert tex_flags(None) == "" and dvisvgm_flags(None) == ""
    assert tex_flags(tmp_path) == "-no-shell-escape" and dvisvgm_flags(tmp_path) == ""
    (tmp_path / "initexmf.exe").write_bytes(b"")
    assert sandbox.is_miktex(tmp_path)
    assert tex_flags(tmp_path) == "-disable-installer -disable-write18"
    assert dvisvgm_flags(tmp_path) == "--miktex-disable-installer"


def test_scrub_paths_hides_host_layout(tmp_path: Path) -> None:
    import tempfile

    work = tmp_path / "aadhi-manim-x1"
    home = str(Path.home())
    log = "\n".join([
        f'File "{work / "f0" / "scene.py"}", line 9',
        "cwd " + str(work).replace("\\", "/"),
        f"media {Path(tempfile.gettempdir()) / 'aadhi-m-abc' / 'videos'}",
        f"lib {Path(sys.prefix) / 'Lib' / 'site-packages' / 'manim' / 'scene.py'}",
        f"home {home.upper()}",
        r"other C:\Users\alice\AppData\x.txt and /home/bob/.cache/x and /Users/carol/y",
    ])
    clean = scrub_paths(log, [(work, "<work>")])
    assert "<work>" in clean and "<tmp>" in clean and "<python>" in clean and "<home>" in clean
    for leak in (str(work), str(work).replace("\\", "/"), tempfile.gettempdir(), sys.prefix, home, "alice", "bob",
                 "carol", Path.home().name):
        assert leak.lower() not in clean.lower(), leak
    assert 'scene.py", line 9' in clean and "x.txt" in clean
    assert scrub_paths("") == "" and scrub_paths("plain text") == "plain text"
    assert scrub_paths("x", [(None, "<work>"), ("", "<work>")]) == "x"


def test_find_output_ignores_partial_files(tmp_path: Path) -> None:
    assert find_output(tmp_path / "missing") is None
    partial = tmp_path / "videos" / "scene" / "480p15" / "partial_movie_files" / "Demo" / "out.mp4"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")
    assert find_output(tmp_path) is None
    empty = tmp_path / "videos" / "scene" / "480p15" / "out.mp4"
    empty.write_bytes(b"")
    assert find_output(tmp_path) is None
    empty.write_bytes(b"movie")
    assert find_output(tmp_path) == empty


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_find_output_rejects_symlinks_and_escapes(tmp_path: Path) -> None:
    media = tmp_path / "media"
    good = media / "videos" / "scene" / "480p15" / "out.mp4"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"movie")
    assert find_output(media) == good
    # a symlinked out.mp4 pointing at a host file is never promoted
    outside = tmp_path / "host.mp4"
    outside.write_bytes(b"secret")
    link = media / "videos" / "scene" / "480p15" / "link_dir"
    link.mkdir()
    sym = link / "out.mp4"
    sym.symlink_to(outside)
    good.unlink()
    assert find_output(media) is None


# --- disk, output and metrics ---------------------------------------------------------------------


def test_dir_size_mb_counts_and_exits_early(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    (tmp_path / "sub" / "b.bin").write_bytes(b"x" * (3 * 1024 * 1024))
    assert dir_size_mb([tmp_path]) == pytest.approx(5.0, abs=0.05)
    assert dir_size_mb([tmp_path], limit_mb=1.0) > 1.0  # early exit still reports over the limit
    assert dir_size_mb([tmp_path / "missing"]) == 0.0


def test_disk_watchdog_stops_a_runaway_writer(tmp_path: Path) -> None:
    out = tmp_path / "work"
    out.mkdir()
    code = (
        "import time\n"
        f"f = open({str(out / 'big.bin')!r}, 'wb')\n"
        "chunk = b'x' * (1024 * 1024)\n"
        "for _ in range(80):\n"
        "    f.write(chunk); f.flush()\n"
        "time.sleep(30)\n"
    )
    rc, _out, killed, elapsed = run_process([PY, "-c", code], cwd=tmp_path, env=None, timeout=60,
                                            watch_dirs=[out], disk_limit_mb=20)
    assert killed == "output_limit" and elapsed < 30


def test_run_process_reports_peak_memory(tmp_path: Path) -> None:
    code = "import time; b = b'x' * (120 * 1024 * 1024); time.sleep(1.5); del b"
    metrics: dict = {}
    rc, _out, killed, _elapsed = run_process([PY, "-c", code], cwd=tmp_path, env=None, timeout=30, metrics=metrics)
    assert rc == 0 and killed == ""
    assert metrics["peak_memory_mb"] >= 100  # the 120 MB buffer was sampled


def test_subprocess_runner_caps_oversized_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_process(cmd, *, cwd, env, timeout, cancel=None, memory_limit_mb=None, on_kill=None,
                         watch_dirs=(), disk_limit_mb=None, max_processes=None, metrics=None):
        media = Path(cmd[cmd.index("--media_dir") + 1])
        out = media / "videos" / "scene" / "480p15" / "out.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"x" * (5 * 1024 * 1024))  # 5 MB
        if metrics is not None:
            metrics["peak_memory_mb"] = 12.0
        return 0, "[AADHI_STATS] frames=42\nrendered", "", 0.2

    monkeypatch.setattr(sandbox, "run_process", fake_run_process)
    runner = SubprocessRunner(output_limit_mb=1)  # 1 MB cap, output is 5 MB
    result = await_run(runner, tmp_path / "w")
    assert not result.ok and result.killed_reason == "output_limit" and result.category == "output_limit"
    assert "above the" in result.log

    runner_ok = SubprocessRunner(output_limit_mb=50)
    result = await_run(runner_ok, tmp_path / "w2")
    assert result.ok and result.video_path == tmp_path / "w2" / "out.mp4"
    assert result.frames == 42 and result.peak_memory_mb == 12.0


def await_run(runner, workdir: Path) -> SandboxResult:
    return asyncio.run(runner.run("print('x')", "Demo", workdir=workdir, quality="l", timeout=9))


# --- failure categories ---------------------------------------------------------------------------


def test_sandbox_result_category_and_reports() -> None:
    assert SandboxResult(True, 0, Path("x"), "ok").category == ""
    assert SandboxResult(False, None, None, "", killed_reason="timeout").category == "timeout"
    assert SandboxResult(False, None, None, "", killed_reason="memory").category == "memory_limit"
    assert SandboxResult(False, None, None, "", killed_reason="output_limit").category == "output_limit"
    blocked = SandboxResult(False, 1, None, "noise\n[AADHI_BLOCKED] open for writing\nPermissionError")
    assert blocked.category == "blocked" and "does not allow" in blocked.error_report
    frames = SandboxResult(False, 1, None, "[AADHI_LIMIT] frames\nRuntimeError: runs past")
    assert frames.category == "frame_limit"
    profile = SandboxResult(False, 1, None, "[AADHI_LIMIT] profile\nRuntimeError: changed resolution")
    assert profile.category == "profile_limit"
    tex = SandboxResult(False, 1, None, "! LaTeX Error: File `x.sty' not found.")
    assert tex.category == "latex"
    assert SandboxResult(False, 0, None, "fine").category == "invalid_output"
    assert SandboxResult(False, 1, None, "boom").category == "render_failed"


# --- orphan sweep ---------------------------------------------------------------------------------


def test_sweep_removes_temp_dirs_by_owner_and_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setattr(sandbox.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.delenv("MANIM_HOST_TMP", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)

    live = tmp_path / "aadhi-manim-live"
    live.mkdir()
    write_owner_marker(live)  # our host + our live pid: must be kept

    dead = tmp_path / "aadhi-m-dead"
    dead.mkdir()
    (dead / OWNER_MARKER).write_text(
        json.dumps({"host": sandbox._owner_identity()[0], "pid": _exited_pid(), "boot_id": "other-boot",
                    "created": int(time.time())}),
        encoding="utf-8",
    )  # same host, the owner process has exited: gone

    old = tmp_path / "aadhi-d-old"
    old.mkdir()
    ancient = time.time() - sandbox.ORPHAN_MAX_AGE_SECONDS - 100
    os.utime(old, (ancient, ancient))  # no marker, older than a day

    fresh = tmp_path / "aadhi-m-fresh"
    fresh.mkdir()  # no marker, young: kept

    unrelated = tmp_path / "something-else"
    unrelated.mkdir()

    # the sweep looks only inside the scratch root (SCRATCH_DIR), here the test's directory
    settings = type("S", (), {"manim_sandbox": "subprocess", "scratch_dir": str(tmp_path)})()
    result = sweep_orphans(settings, now=time.time())
    assert result["temp_dirs"] == 2
    assert live.exists() and fresh.exists() and unrelated.exists()
    assert not dead.exists() and not old.exists()


def _exited_pid() -> int:
    """The pid of a process that has already exited (printed by the child itself: the venv launcher on
    Windows would make ``Popen.pid`` the launcher's)."""
    import subprocess

    out = subprocess.run([PY, "-c", "import os; print(os.getpid())"], capture_output=True, text=True,  # noqa: S603
                         timeout=60, check=True)
    pid = int(out.stdout.strip())
    psutil.wait_procs([psutil.Process(pid)] if psutil.pid_exists(pid) else [], timeout=10)
    return pid


def _marker(path: Path, **fields) -> Path:
    import json

    path.mkdir()
    data = {"host": sandbox._owner_identity()[0], "pid": os.getpid(), "boot_id": "x", "created": int(time.time())}
    (path / OWNER_MARKER).write_text(json.dumps({**data, **fields}), encoding="utf-8")
    return path


def _sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now: float | None = None) -> int:
    monkeypatch.setattr(sandbox.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.delenv("MANIM_HOST_TMP", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    # the sweep looks only inside the scratch root (SCRATCH_DIR), here the test's directory
    settings = type("S", (), {"manim_sandbox": "subprocess", "scratch_dir": str(tmp_path)})()
    return sweep_orphans(settings, now=time.time() if now is None else now)["temp_dirs"]


def test_sweep_keeps_the_live_dirs_of_sibling_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BOOT_ID is random per process: another live worker / API process on this host keeps its work dirs."""
    import subprocess

    live = tmp_path / "aadhi-manim-sibling"
    live.mkdir()
    code = ("import sys, time; from pathlib import Path; from aadhi.manim.sandbox import write_owner_marker; "
            f"write_owner_marker(Path({str(live)!r})); print('ready', flush=True); time.sleep(120)")
    repo = Path(sandbox.__file__).resolve().parents[2]
    child = subprocess.Popen([PY, "-c", code], cwd=repo, stdout=subprocess.PIPE, text=True)  # noqa: S603
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        assert _sweep(tmp_path, monkeypatch) == 0 and live.exists()
    finally:
        kill_tree(child.pid)
        child.wait(10)
    assert _sweep(tmp_path, monkeypatch) == 1 and not live.exists()


def test_sweep_spots_a_recycled_pid_and_ages_out_old_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reused = _marker(tmp_path / "aadhi-m-reused", started=1.0)  # our live pid, but another process's start time
    mine = tmp_path / "aadhi-m-mine"
    mine.mkdir()
    write_owner_marker(mine)
    legacy = _marker(tmp_path / "aadhi-d-legacy")  # no start time: a live pid may be a sibling worker
    assert _sweep(tmp_path, monkeypatch) == 1
    assert not reused.exists() and mine.exists() and legacy.exists()
    assert _sweep(tmp_path, monkeypatch, now=time.time() + sandbox.ORPHAN_MAX_AGE_SECONDS + 100) == 1
    assert not legacy.exists() and mine.exists()


def test_sweep_docker_kills_only_expired_containers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    listing = f"aaa {int(now - 10)}\nbbb {int(now + 300)}\nccc notanumber\n"
    calls: list[list[str]] = []

    def fake_which(_name):
        return "docker"

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        if "ps" in cmd:
            return subprocess_result(listing)
        return subprocess_result("")

    monkeypatch.setattr(sandbox.shutil, "which", fake_which)
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    monkeypatch.setattr(sandbox.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.delenv("MANIM_HOST_TMP", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)

    settings = type("S", (), {"manim_sandbox": "docker"})()
    result = sweep_orphans(settings, now=now)
    assert result["containers"] == 1
    rm = next(c for c in calls if "rm" in c)
    assert "aaa" in rm and "bbb" not in rm and "ccc" not in rm  # only the expired container is killed


def subprocess_result(stdout: str):
    import subprocess as sp

    return sp.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


# --- Windows Job Object (kill-on-close) -----------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows-only")
def test_windows_job_kills_the_tree_on_close() -> None:
    import subprocess

    from aadhi.manim.winjob import JobObject

    job = JobObject(memory_limit_mb=512, max_processes=16)
    child = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"],  # noqa: S603
                             creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        job.assign(int(child._handle))
        stats = job.stats()
        assert stats.processes >= 1 and stats.peak_memory_mb > 0
        pid = child.pid
        job.close()  # KILL_ON_JOB_CLOSE must end the child even though we never killed it
        psutil.wait_procs([psutil.Process(pid)], timeout=10)
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    finally:
        child.kill()
        child.wait(10)


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows-only")
def test_render_tree_dies_with_a_hard_killed_worker(tmp_path: Path) -> None:
    """Only the kill-on-close job can do this: the worker is hard-killed, so no kill_tree / finally runs."""
    import subprocess

    pid_file = tmp_path / "render.pid"
    render_code = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
    worker_code = textwrap.dedent(f"""
        import sys
        from pathlib import Path
        from aadhi.manim.sandbox import run_process
        run_process([sys.executable, "-c", {render_code!r}], cwd=Path({str(tmp_path)!r}), env=None, timeout=120)
        """)
    repo = Path(sandbox.__file__).resolve().parents[2]
    worker = subprocess.Popen([PY, "-c", worker_code], cwd=repo)  # noqa: S603
    child = None
    try:
        deadline = time.monotonic() + 60
        while child is None:
            assert worker.poll() is None and time.monotonic() < deadline, "the worker never started the render"
            text = pid_file.read_text() if pid_file.exists() else ""
            child = psutil.Process(int(text)) if text.strip() else None
            time.sleep(0.1)
        worker.kill()  # TerminateProcess: the worker's own cleanup never runs
        worker.wait(10)
        _gone, alive = psutil.wait_procs([child], timeout=10)
        assert not alive, "the render outlived its killed worker"
    finally:
        worker.kill()
        worker.wait(10)
        if child is not None and child.is_running():
            kill_tree(child.pid)


class _FakeJob:
    """A job whose commit backstop always reports a hit (the clean / failed exit decides)."""

    created: list[float | None] = []

    def __init__(self, *, memory_limit_mb=None, max_processes=None) -> None:
        _FakeJob.created.append(memory_limit_mb)

    def assign(self, handle) -> None:
        pass

    def stats(self):
        from aadhi.manim.winjob import JobStats

        return JobStats(cpu_seconds=0.5, peak_memory_mb=1900.0, processes=1)

    def hit_memory_limit(self) -> bool:
        return True

    def close(self) -> None:
        pass


@pytest.mark.skipif(os.name != "nt", reason="the job is created on Windows only")
def test_job_memory_only_explains_a_failed_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aadhi.manim import winjob

    monkeypatch.setattr(winjob, "JobObject", _FakeJob)
    _FakeJob.created.clear()
    metrics: dict = {}
    rc, _out, killed, _ = run_process([PY, "-c", "print('done')"], cwd=tmp_path, env=None, timeout=60,
                                      memory_limit_mb=1024, metrics=metrics)
    assert rc == 0 and killed == ""  # a finished render is never reclassified as a memory failure
    assert _FakeJob.created == [1024 * 2 + 2048]  # the job gets the loose commit backstop, not the RSS ceiling
    assert metrics["peak_commit_mb"] == 1900.0 and metrics["peak_memory_mb"] < 1900.0  # RSS is reported
    rc, _out, killed, _ = run_process([PY, "-c", "import sys; sys.exit(1)"], cwd=tmp_path, env=None, timeout=60,
                                      memory_limit_mb=1024)
    assert rc == 1 and killed == "memory"  # a failed exit near the backstop is explained by it


def test_job_commit_backstop() -> None:
    assert sandbox._job_commit_backstop_mb(2048) == 6144 and sandbox._job_commit_backstop_mb(None) is None


# --- process supervision --------------------------------------------------------------------------


def test_output_is_capped_to_the_tail(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.write('x' * 500000); sys.stdout.flush(); print(); print('END')"
    rc, output, killed, _ = run_process([PY, "-c", code], cwd=tmp_path, env=None, timeout=60)
    assert rc == 0 and killed == ""
    assert output.startswith("[... ") and "bytes of earlier output dropped" in output
    assert output.rstrip().endswith("END") and len(output) <= OUTPUT_CAP + 100


def test_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    code = textwrap.dedent(
        f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        time.sleep(120)
        """
    )
    killed_hooks: list[str] = []
    started = time.monotonic()
    rc, _out, killed, elapsed = run_process([PY, "-c", code], cwd=tmp_path, env=None, timeout=3,
                                            on_kill=[lambda: killed_hooks.append("hook")])
    assert killed == "timeout" and killed_hooks == ["hook"]
    assert 3 <= elapsed < 15 and time.monotonic() - started < 20
    child_pid = int(pid_file.read_text())
    try:
        child = psutil.Process(child_pid)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass


def test_cancel_event_kills_the_process(tmp_path: Path) -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    try:
        rc, _out, killed, elapsed = run_process([PY, "-c", "import time; time.sleep(60)"], cwd=tmp_path, env=None,
                                                timeout=60, cancel=cancel)
    finally:
        timer.cancel()
    assert killed == "cancelled" and elapsed < 15


def test_memory_ceiling_kills_the_process(tmp_path: Path) -> None:
    code = "import time; b = b'x' * (400 * 1024 * 1024); time.sleep(60)"
    rc, _out, killed, elapsed = run_process([PY, "-c", code], cwd=tmp_path, env=None, timeout=60, memory_limit_mb=150)
    assert killed == "memory" and elapsed < 30


def test_kill_tree_tolerates_missing_processes() -> None:
    proc = psutil.Popen([PY, "-c", "pass"])
    proc.wait(30)
    kill_tree(proc.pid)  # already gone: no error


async def test_cancelling_a_render_sets_the_kill_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    done = threading.Event()

    def fake_run_process(cmd, *, cwd, env, timeout, cancel=None, memory_limit_mb=None, on_kill=None, **_kw):
        seen["media"] = Path(cmd[cmd.index("--media_dir") + 1])
        seen["cancelled"] = cancel.wait(10)
        time.sleep(0.5)  # killing the process tree takes a while
        done.set()
        return None, "", "cancelled", 0.5

    monkeypatch.setattr(sandbox, "run_process", fake_run_process)
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(SubprocessRunner().run("x", "Demo", workdir=tmp_path, quality="l", timeout=60), 0.3)
    # Cancellation propagates only after the worker has killed the process and cleaned up.
    assert done.is_set() and seen["cancelled"] is True and time.monotonic() - started >= 0.75
    assert not seen["media"].exists()


async def test_docker_cancel_waits_for_the_container_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    done = threading.Event()

    def fake_run_process(cmd, *, cwd, env, timeout, cancel=None, memory_limit_mb=None, on_kill=None, **_kw):
        cancel.wait(10)
        time.sleep(0.3)
        done.set()
        return None, "", "cancelled", 0.3

    monkeypatch.setattr(sandbox, "run_process", fake_run_process)
    task = asyncio.ensure_future(DockerRunner("img").run("x", "Demo", workdir=tmp_path, quality="l", timeout=60))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert done.is_set()


async def test_run_in_thread_survives_repeated_cancellation() -> None:
    cancel = threading.Event()
    finished = threading.Event()

    def work() -> SandboxResult:
        cancel.wait(10)
        time.sleep(0.4)
        finished.set()
        return SandboxResult(False, None, None, "")

    task = asyncio.ensure_future(run_in_thread(work, cancel))
    await asyncio.sleep(0.05)
    for _ in range(3):  # impatient callers cancel again while the process tree is being killed
        task.cancel()
        await asyncio.sleep(0.05)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel.is_set() and finished.is_set()


async def test_run_in_thread_returns_results_and_errors() -> None:
    ok = await run_in_thread(lambda: SandboxResult(True, 0, None, "fine"), threading.Event())
    assert ok.ok and ok.log == "fine"

    def boom() -> SandboxResult:
        raise RuntimeError("worker failed")

    with pytest.raises(RuntimeError, match="worker failed"):
        await run_in_thread(boom, threading.Event())


# --- real manim (slow) ----------------------------------------------------------------------------


def _script(body: str, beats: list[float], total: float) -> str:
    cfg = build_config(beat_times=beats, total_duration=total, quality="l")
    return assemble_script(textwrap.dedent(body), cfg)


@pytest.mark.slow
async def test_infinite_loop_scene_is_killed(tmp_path: Path) -> None:
    script = _script(
        """
        from manim import *

        class Spin(AadhiScene):
            def construct(self):
                while True:
                    pass
        """,
        [0.5],
        2.0,
    )
    before = {p.pid for p in psutil.process_iter()}
    result = await SubprocessRunner().run(script, "Spin", workdir=tmp_path, quality="l", timeout=8)
    assert not result.ok and result.timed_out and result.killed_reason == "timeout"
    assert 8 <= result.elapsed < 25 and "timed out" in result.error_report
    survivors = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        if proc.info["pid"] in before:
            continue
        try:
            if any(str(tmp_path) in part for part in proc.info["cmdline"] or []):
                survivors.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    assert survivors == []


@pytest.mark.slow
async def test_scene_errors_are_reported_with_user_line_numbers(tmp_path: Path) -> None:
    script = _script(
        """
        from manim import *

        class Broken(AadhiScene):
            def construct(self):
                self.wait_until_beat(0)
                self.play(Create(Circl()))
        """,
        [0.2],
        1.0,
    )
    result = await SubprocessRunner().run(script, "Broken", workdir=tmp_path, quality="l", timeout=120)
    assert not result.ok and result.returncode not in (0, None)
    report = result.error_report
    assert "line 7, in construct: self.play(Create(Circl()))" in report
    assert "NameError" in report and "Circl" in report


@pytest.mark.slow
async def test_runtime_namespace_is_enforced_inside_the_real_sandbox(tmp_path: Path) -> None:
    """Even code that never went through the guard cannot reach manim's file/process helpers."""
    script = _script(
        """
        from manim import *

        class Probe(AadhiScene):
            def construct(self):
                names = ("capture", "guarantee_empty_existence", "open_file", "SVGMobject", "ImageMobject",
                         "utils", "camera", "logger", "Typst")
                found = [n for n in names if n in globals()]
                try:
                    Code(code_file="scene.py")
                    code_file = "allowed"
                except ValueError:
                    code_file = "refused"
                raise RuntimeError(f"PROBE found={found} code_file={code_file} np={np.__name__}")
        """,
        [0.2],
        1.0,
    )
    result = await SubprocessRunner().run(script, "Probe", workdir=tmp_path, quality="l", timeout=120)
    assert not result.ok
    assert "PROBE found=[] code_file=refused np=numpy" in result.error_report, result.log[-2000:]


async def test_module_run_uses_the_configured_runner(app_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class Recorder:
        name = "recorder"

        async def run(self, script, scene, *, workdir, quality, timeout):
            seen.update(script=script, scene=scene, workdir=workdir, quality=quality, timeout=timeout)
            return SandboxResult(True, 0, None, "")

    monkeypatch.setattr(sandbox, "get_runner", lambda settings: Recorder())
    await sandbox.run("code", "Demo", settings=app_env, workdir=str(tmp_path))
    assert seen == {"script": "code", "scene": "Demo", "workdir": tmp_path, "quality": app_env.manim_quality,
                    "timeout": float(app_env.manim_timeout_seconds)}
    await sandbox.run("code", "Demo", settings=app_env, workdir=tmp_path, quality="l", timeout=5)
    assert (seen["quality"], seen["timeout"]) == ("l", 5.0)


# --- POSIX CPU backstop, runner limits and settings bounds ------------------------------------------------


def test_cpu_backstop_tolerates_a_resource_module_without_prlimit(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    fake = types.ModuleType("resource")
    fake.RLIMIT_CPU = 0  # macOS: resource, but no prlimit
    monkeypatch.setitem(sys.modules, "resource", fake)
    sandbox._posix_cpu_backstop(os.getpid(), 30)  # must not raise


def test_posix_cpu_backstop_is_a_multiple_of_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    seen: list = []
    fake = types.ModuleType("resource")
    fake.RLIMIT_CPU = 0
    fake.prlimit = lambda pid, what, limits: seen.append(limits)
    monkeypatch.setitem(sys.modules, "resource", fake)
    sandbox._posix_cpu_backstop(123, 180)
    ((soft, hard),) = seen
    assert soft >= 180 * 2 and hard > soft  # manim's threads use 1.5-1.9x CPU per wall second


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_CPU through prlimit is Linux-only")
def test_posix_cpu_backstop_is_set_on_the_render(tmp_path: Path) -> None:
    code = "import resource, time; time.sleep(0.5); print(resource.getrlimit(resource.RLIMIT_CPU))"
    rc, out, killed, _ = run_process([PY, "-c", code], cwd=tmp_path, env=None, timeout=30)
    assert rc == 0 and killed == "" and out.strip().endswith("(150, 165)")  # 30 s x 4 + 30, +15


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGXCPU"), reason="SIGXCPU is POSIX-only")
def test_sigxcpu_exit_is_reported_as_timeout(tmp_path: Path) -> None:
    code = "import os, signal; os.kill(os.getpid(), signal.SIGXCPU)"
    rc, out, killed, elapsed = run_process([PY, "-c", code], cwd=tmp_path, env=None, timeout=10)
    assert killed == "timeout"
    result = SandboxResult(False, rc, None, out, timed_out=True, killed_reason=killed, elapsed=elapsed)
    assert result.category == "timeout"


def test_get_runner_passes_the_limits(app_env) -> None:
    s = app_env.model_copy(update={"manim_memory_limit_mb": 1024, "manim_max_workspace_mb": 200,
                                   "manim_max_output_mb": 50, "manim_max_processes": 32, "manim_docker_cpus": 1.5})
    sub = get_runner(s)
    assert (sub.memory_limit_mb, sub.disk_limit_mb, sub.output_limit_mb, sub.max_processes) == (1024.0, 200.0, 50.0, 32)
    dock = get_runner(s.model_copy(update={"manim_sandbox": "docker"}))
    assert (dock.memory, dock.cpus, dock.disk_limit_mb, dock.output_limit_mb) == ("1024m", "1.5", 200.0, 50.0)


@pytest.mark.parametrize(("name", "value"), [
    ("manim_timeout_seconds", 9), ("manim_timeout_seconds", 901), ("manim_max_concurrent", -1),
    ("manim_max_concurrent", 17), ("manim_memory_limit_mb", 255), ("manim_memory_limit_mb", 8193),
    ("manim_docker_cpus", 0), ("manim_docker_cpus", 8.5), ("manim_max_processes", 7), ("manim_max_processes", 257),
    ("manim_max_workspace_mb", 63), ("manim_max_workspace_mb", 4097), ("manim_max_output_mb", 15),
    ("manim_max_output_mb", 2049),
])
def test_manim_settings_bounds(name: str, value: float) -> None:
    import pydantic

    from aadhi.config import Settings

    with pytest.raises(pydantic.ValidationError):
        Settings(_env_file=None, **{name: value})
