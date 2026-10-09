"""Hostile-probe tests against the REAL Docker sandbox: the container boundary alone must refuse escape.

These run a deliberately malicious probe (the Python defences off) in the configured sandbox image
and assert the OS/container boundary denies file, network and process access. They need a working
Docker daemon and the sandbox image; otherwise they are skipped (there is no marker in pyproject, so
the integrator may add an optional ``docker`` CI job that un-skips them by setting the env vars).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from aadhi.manim.sandbox import DockerRunner, run_process

IMAGE = os.environ.get("MANIM_DOCKER_IMAGE", "aadhi-manim-sandbox:latest")


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=False)  # noqa: S607
        if info.returncode != 0:
            return False
        img = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=20, check=False)  # noqa: S607
        return img.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _docker_ready(), reason="needs a Docker daemon and the sandbox image"),
]

PROBE = r"""
import json, os, socket
out = {}
out["uid"] = os.getuid()
# network must be refused (--network none)
try:
    socket.socket().connect(("1.1.1.1", 80)); out["network"] = "open"
except Exception:
    out["network"] = "denied"
# the read-only root must refuse a write outside /work
try:
    open("/evil.txt", "w").write("x"); out["root_write"] = "allowed"
except Exception:
    out["root_write"] = "denied"
# /work is the only writable mount
try:
    open("/work/ok.txt", "w").write("x"); out["work_write"] = "allowed"
except Exception:
    out["work_write"] = "denied"
# no host environment leaked in
_HINTS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL", "JWT")
out["env_has_secrets"] = any(any(h in k.upper() for h in _HINTS) for k in os.environ)
print("[PROBE] " + json.dumps(out))
"""


def _run_probe() -> dict:
    runner = DockerRunner(IMAGE)
    # the render's mount layout (DockerRunner.render_sync): a world-writable leaf in a private directory, so the
    # container user (uid 1000) can use it whatever the CI user's uid is
    outer = Path(tempfile.mkdtemp(prefix="aadhi-dockertest-"))
    workdir = outer / "w"
    workdir.mkdir()
    (workdir / "probe.py").write_text(PROBE, encoding="utf-8")
    if os.name != "nt":
        os.chmod(workdir, 0o777)  # noqa: S103 - parent is a private 0700 temp dir
        os.chmod(workdir / "probe.py", 0o644)
    name = f"aadhi-manim-itest-{uuid.uuid4().hex[:12]}"
    cmd = runner.build_command(workdir, "Probe", "l", name, timeout=60)
    image_at = cmd.index(runner.image)
    cmd = [*cmd[: image_at + 1], "python", f"{runner.WORKDIR}/probe.py"]
    _rc, output, _killed, _elapsed = run_process(cmd, cwd=outer, env=runner.build_env(), timeout=90)
    line = next((ln for ln in output.splitlines() if ln.startswith("[PROBE] ")), None)
    assert line, f"no probe output:\n{output[-2000:]}"
    return json.loads(line[len("[PROBE] "):])


def test_container_denies_network_root_write_and_host_secrets() -> None:
    probe = _run_probe()
    assert probe["network"] == "denied"
    assert probe["root_write"] == "denied"
    assert probe["work_write"] == "allowed"
    assert probe["uid"] == 1000
    assert probe["env_has_secrets"] is False
