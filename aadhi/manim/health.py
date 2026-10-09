"""Sandbox self-check: does the configured Manim sandbox actually deny secrets, files and network?

``sandbox_health(settings)`` runs a tiny probe through the configured runner and reports **booleans
and short strings only** - never host paths, env values or secrets. It is meant for an operator CLI
or an admin-only route (both outside this package); nothing here is exposed to ordinary users.

The probe prints one ``[AADHI_PROBE] {json}`` line. It:

* reads the installed Manim version (compared with :func:`aadhi.manim.render.manim_version`);
* checks that no obvious secret-shaped names are present in its environment;
* tries a TCP connection to a public address (``denied`` is the healthy result);
* tries to read a host file outside its work dir (a canary written next to the work dir: ``denied``
  is the healthy result; only the boolean is reported, never the path);
* reports its uid (1000 in the container, -1 on Windows).

For the subprocess runner the probe is wrapped in the hardening audit hook exactly as renders are
(``MANIM_AUDIT_HOOK``: with the hook off the probe runs without it and the check says so), so the
health check exercises that hook; the result still says ``isolated: false`` because a local
subprocess is only process-isolated, not a real boundary. The Docker runner runs the probe in the
locked-down container, with the same mount layout as a render.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import Settings
from ..scratch import make_temp_dir, scratch_root
from .aadhi_scene import hardening_source
from .render import manim_version
from .sandbox import ENV_ALLOWLIST, DockerRunner, SubprocessRunner, run_process

_PROBE_LINE = re.compile(r"\[AADHI_PROBE\] (\{.*\})")

PROBE_SCRIPT = r"""
import json, os, socket
out = {}
try:
    import importlib.metadata as _m
    out["manim_version"] = _m.version("manim")
except Exception:
    out["manim_version"] = ""
_HINTS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL", "JWT", "AWS")
out["env_has_secrets"] = any(any(h in k.upper() for h in _HINTS) for k in os.environ)
try:
    _s = socket.socket(); _s.settimeout(2); _s.connect(("1.1.1.1", 80)); _s.close()
    out["network"] = "open"
except Exception:
    out["network"] = "denied"
try:
    with open(globals().get("_CANARY", ""), "rb") as _f:
        _f.read(1)
    out["host_file"] = "readable"
except Exception:
    out["host_file"] = "denied"
out["uid"] = getattr(os, "getuid", lambda: -1)()
print("[AADHI_PROBE] " + json.dumps(out))
"""


@dataclass
class SandboxHealth:
    """Operator-facing sandbox status (no host paths or secrets)."""

    available: bool
    sandbox: str
    isolated: bool
    isolation: str
    manim_version: str
    manim_version_ok: bool
    latex: bool
    network_denied: bool | None = None  # None when the probe could not run
    env_clean: bool | None = None
    files_denied: bool | None = None  # a host file outside the work dir could not be read
    probe_ran: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(output: str) -> dict | None:
    matches = _PROBE_LINE.findall(output or "")
    if not matches:
        return None
    try:
        return json.loads(matches[-1])
    except ValueError:
        return None


def _canary() -> Path:
    """A host file outside every work dir (a sibling temp dir; never inside the hook's read roots)."""
    folder = Path(tempfile.mkdtemp(prefix="aadhi-canary-"))
    path = folder / "c.txt"
    path.write_text("canary", encoding="utf-8")
    return path


def _run_subprocess_probe(settings: Settings) -> dict | None:
    runner = SubprocessRunner(scratch_dir=scratch_root(settings))
    workdir = make_temp_dir(settings, "aadhi-probe-")  # inside the scratch root, as render work dirs
    canary: Path | None = None
    try:
        canary = _canary()
        # Prepend the audit hook as renders get it (MANIM_AUDIT_HOOK; no manim import, no frame limits).
        hook = bool(settings.manim_audit_hook)
        header = f"_AADHI_HARDEN = {{'audit_hook': {hook!r}, 'frame_limits': False, 'programs': []}}\n"
        script = workdir / "probe.py"
        script.write_text(header + f"_CANARY = {str(canary)!r}\n" + hardening_source() + "\n" + PROBE_SCRIPT,
                          encoding="utf-8")
        env = runner.build_env(workdir)
        _rc, output, _killed, _elapsed = run_process(
            [runner.python, str(script)], cwd=workdir, env=env, timeout=30,
        )
        return _parse(output)
    except OSError:  # pragma: no cover - best effort
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if canary is not None:
            shutil.rmtree(canary.parent, ignore_errors=True)


def _run_docker_probe(settings: Settings) -> dict | None:
    runner = DockerRunner(settings.manim_docker_image, memory=f"{settings.manim_memory_limit_mb}m",
                          cpus=f"{settings.manim_docker_cpus:g}", scratch_dir=scratch_root(settings))
    if shutil.which(runner.docker) is None:
        return None
    # The render's mount layout (DockerRunner.render_sync): the container user (uid 1000) can read the probe
    # whatever the host user's uid is, and the mount sits in a private 0700 directory inside the scratch root
    # (SCRATCH_DIR: the host-shared path the Docker sandbox mounts from).
    outer = make_temp_dir(settings, "aadhi-probe-")
    canary: Path | None = None
    try:
        canary = _canary()
        mount = outer / "w"
        mount.mkdir()
        (mount / "probe.py").write_text(f"_CANARY = {str(canary)!r}\n" + PROBE_SCRIPT, encoding="utf-8")
        if os.name != "nt":
            os.chmod(mount, 0o777)  # noqa: S103 - parent is a private 0700 temp dir
            os.chmod(mount / "probe.py", 0o644)
        name = f"aadhi-manim-probe-{uuid.uuid4().hex[:12]}"
        cmd = runner.build_command(mount, "Probe", "l", name, timeout=30)
        # Replace the trailing manim args with the probe; keep every isolation flag before the image.
        image_at = cmd.index(runner.image)
        cmd = [*cmd[: image_at + 1], "python", f"{runner.WORKDIR}/probe.py"]
        _rc, output, _killed, _elapsed = run_process(cmd, cwd=outer, env=runner.build_env(), timeout=45)
        return _parse(output)
    except (OSError, ValueError):  # pragma: no cover - best effort
        return None
    finally:
        shutil.rmtree(outer, ignore_errors=True)
        if canary is not None:
            shutil.rmtree(canary.parent, ignore_errors=True)


def sandbox_health(settings: Settings) -> SandboxHealth:
    """Probe the configured sandbox. Safe to expose to operators (booleans and short strings only)."""
    version = manim_version()
    if settings.manim_sandbox == "disabled":
        return SandboxHealth(available=False, sandbox="disabled", isolated=False,
                             isolation="Manim rendering is disabled", manim_version=version,
                             manim_version_ok=False, latex=False, notes=["set MANIM_SANDBOX to enable rendering"])
    if settings.manim_sandbox == "docker":
        isolated, isolation = True, ("docker: network none, read-only root, caps dropped, "
                                     "non-root user, memory/pids limited, only the work dir mounted")
        probe = _run_docker_probe(settings)
        latex = True
    else:
        guard = "env allow-list + audit hook" if settings.manim_audit_hook else "env allow-list only; audit hook disabled"
        isolated, isolation = False, f"subprocess: process isolation only ({guard}; no OS boundary)"
        probe = _run_subprocess_probe(settings)
        latex = SubprocessRunner().has_latex()
    health = SandboxHealth(
        available=True, sandbox=settings.manim_sandbox, isolated=isolated, isolation=isolation,
        manim_version=version, manim_version_ok=bool(version) and version != "unknown", latex=latex,
    )
    if settings.manim_sandbox == "subprocess" and not settings.manim_audit_hook:
        health.notes.append("the runtime audit hook is disabled (MANIM_AUDIT_HOOK=false)")
    if probe is not None:
        health.probe_ran = True
        health.network_denied = probe.get("network") == "denied"
        health.env_clean = probe.get("env_has_secrets") is False
        health.files_denied = probe.get("host_file") == "denied"
        if not health.files_denied:
            health.notes.append("a host file outside the work dir was readable from the sandbox")
        health.manim_version_ok = bool(probe.get("manim_version")) and probe.get("manim_version") == version
        if not health.network_denied:
            health.notes.append("network was reachable from the sandbox")
        if not health.env_clean:
            health.notes.append("secret-shaped environment variables were visible to the sandbox")
    else:
        health.notes.append("the isolation probe could not be run")
    return health


def _env_allowlist_is_secret_free() -> bool:  # pragma: no cover - sanity helper for the CLI
    return not any(any(h in name for h in ("API_KEY", "SECRET", "TOKEN", "PASSWORD")) for name in ENV_ALLOWLIST)
