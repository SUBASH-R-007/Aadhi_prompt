"""Repository tooling owned by the devops area: .env.example generator, requirements lock, pyproject,
Docker/compose/CI files, the Chromium seccomp profile, package scripts and ignore rules. File checks
plus `docker compose config` when the docker CLI exists (no daemon, no network, never reads .env)."""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

from aadhi.config import ALL_JOB_KINDS, ROOT_DIR, Settings

SCRIPTS = ROOT_DIR / "scripts"
DOCKER_APP = ROOT_DIR / "docker" / "app"

# v1 files that must never reach an image (the repo-cleanup list of the v2 overhaul).
V1_DEBRIS = (
    "old_index.html", "index2.html", "index.html", "app.js", "app_test.js", "main_script.js", "speak.js",
    "check_inline.js", "extractor.py", "fix.py", "fix_all.py", "fix_tag.py", "fix_bottom_tag.py", "patch_index.py",
    "patch_index_final.py", "patch_index_safe.py", "rewrite_sync.py", "safe_escape.py", "update.py",
    "upgrade_history.py", "run_tts.py", "test_api.py", "test_gemini.py", "test_ltx_download.py", "test_openai.py",
    "test_scene.py", "Digital_System_Design_1781773168.html", "database.py", "models.py", "vercel.json",
    "handoff.md", "Aadhi_EduEngine_Full_Context.md", "video_template/video_template.mp4",
    "video_template/video_template_hi.mp4", "video_template/video_template_old.mp4",
    "video_template/video_template_2.mp4", "sample_template.docx",
)


def _load_script(name: str, path: Path | None = None):
    path = path or SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"aadhi_script_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen_env():
    return _load_script("gen_env_example")


@pytest.fixture(scope="module")
def lock():
    return _load_script("lock_requirements")


@pytest.fixture(scope="module")
def seccomp():
    return _load_script("gen_chromium_seccomp")


@pytest.fixture(scope="module")
def chromium_check():
    return _load_script("check_chromium", DOCKER_APP / "check_chromium.py")


def _yaml(path: str) -> dict:
    return yaml.safe_load((ROOT_DIR / path).read_text(encoding="utf-8"))


def _text(path: str) -> str:
    """File text with CRLF normalised (Windows clones use core.autocrlf=true)."""
    return (ROOT_DIR / path).read_bytes().decode("utf-8").replace("\r\n", "\n")


# --- .env.example ----------------------------------------------------------------------------------


def test_env_example_lists_every_setting_with_secrets_empty(gen_env):
    text = gen_env.render()
    for name, field in Settings.model_fields.items():
        env = name.upper()
        if gen_env.is_secret(field.annotation):
            assert re.search(rf"^{env}=$", text, re.M), env
        else:
            assert re.search(rf"^# {env}=", text, re.M), env
    assert str(ROOT_DIR) not in text and "\\" not in text.replace('\\"', "")  # no machine paths
    assert "# DATA_DIR=./data" in text
    assert "[options: gemini | openai | anthropic | fake]" in text
    assert "# --- Runtime" in text and "# --- Manim" in text
    assert f"# WORKER_KINDS={','.join(ALL_JOB_KINDS)}" in text


def test_env_example_is_committed_and_current(gen_env):
    assert (ROOT_DIR / ".env.example").read_text(encoding="utf-8") == gen_env.render()
    assert gen_env.main(["--check"]) == 0


def test_env_example_check_detects_drift(gen_env, tmp_path, capsys):
    out = tmp_path / ".env.example"
    assert gen_env.main(["--output", str(out), "--check"]) == 1
    assert gen_env.main(["--output", str(out)]) == 0
    assert gen_env.main(["--output", str(out), "--check"]) == 0
    assert b"\r\n" not in out.read_bytes()
    capsys.readouterr()


def test_env_example_documents_every_compose_variable(gen_env):
    text = gen_env.render()
    compose_vars = set(re.findall(r"\$\{([A-Z0-9_]+)", _text("docker-compose.yml")))
    documented = set(re.findall(r"^(?:# )?([A-Z0-9_]+)=", text, re.M))
    assert compose_vars and compose_vars - documented == set()
    assert re.search(r"^POSTGRES_PASSWORD=$", text, re.M)  # required by compose, listed empty like secrets


def test_render_value_and_comment_parsing(gen_env):
    assert gen_env.render_value(None) == ""
    assert (gen_env.render_value(True), gen_env.render_value(False)) == ("true", "false")
    assert gen_env.render_value(["a", "b"]) == "a,b"
    assert gen_env.render_value("#1A0B2E") == '"#1A0B2E"'
    assert gen_env.render_value(ROOT_DIR / "video_template") == "./video_template"
    src = (
        "class Settings:\n"
        "    # --- Group A ---\n"
        "    a: int = 1  # first value\n"
        "    b: str = (\n"
        "        'x'\n"
        "    )  # multi-line\n"
        "    # --- Group B -----\n"
        "    c: int = 3\n"
    )
    docs = gen_env.parse_comments(src)
    assert (docs["a"].section, docs["a"].comment) == ("Group A", "first value")
    assert docs["b"].comment == "multi-line"
    assert (docs["c"].section, docs["c"].comment) == ("Group B", "")


# --- requirements ----------------------------------------------------------------------------------


def _req_lines(path: str) -> list[str]:
    lines = (ROOT_DIR / path).read_text(encoding="utf-8").splitlines()
    out = [ln.split(" #", 1)[0].strip() for ln in lines]
    return [ln for ln in out if ln and not ln.startswith("#")]


def _lock_pins() -> tuple[list[str], list[str]]:
    """(plain pins, marker pins) of requirements.lock."""
    pins = _req_lines("requirements.lock")
    return [p for p in pins if ";" not in p], [p for p in pins if ";" in p]


def test_lock_is_exact_pins_without_the_project(lock):
    plain, marked = _lock_pins()
    assert plain == sorted(plain, key=lambda p: lock.normalise(p.split("==")[0]))
    for line in plain:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==\S+", line), line
        assert lock.normalise(line.split("==")[0]) not in {"aadhi-eduengine", "aadhi"}
    for line in marked:
        req = Requirement(line)
        assert str(req.specifier).startswith("==") and req.marker is not None, line


def test_lock_satisfies_requirement_ranges(lock):
    plain, marked = _lock_pins()
    pins = {lock.normalise(p.split("==")[0]): p.split("==")[1] for p in plain}
    for path in ("requirements.txt", "requirements-dev.txt"):
        for line in _req_lines(path):
            req = Requirement(line)
            name = lock.normalise(req.name)
            assert name in pins, f"{req.name} from {path} is not pinned in requirements.lock"
            assert req.specifier.contains(pins[name], prereleases=True), f"{req.name}=={pins[name]} vs {line}"
    for needed in ("fastapi", "uvicorn", "sqlalchemy", "alembic", "psycopg", "manim", "playwright", "boto3"):
        assert needed in pins
    assert any(line.startswith("uvloop==") for line in marked)


def test_requirement_floors_match_the_verified_versions():
    reqs = {Requirement(line).name.lower(): Requirement(line) for line in _req_lines("requirements.txt")}
    assert not reqs["openai"].specifier.contains("2.9.0") and reqs["openai"].specifier.contains("3.22.1")
    assert not reqs["google-genai"].specifier.contains("1.99.0") and reqs["google-genai"].specifier.contains("2.26.0")
    assert not reqs["sqlalchemy"].specifier.contains("2.0.40") and reqs["sqlalchemy"].specifier.contains("2.1.1")
    assert str(reqs["manim"].specifier) == "==0.21.*"
    sandbox = _text("docker/manim-sandbox/Dockerfile")
    assert "ARG MANIM_VERSION=v0.21." in sandbox  # sandbox image and lock agree (cache keys include it)


def test_lock_lines_filtering(lock):
    raw = ["Foo_Bar==1.0", "-e git+https://x#egg=y", "aadhi-eduengine==2.0.0", "pkg @ file:///tmp/pkg",
           "# comment", "zeta==2", "alpha[extra]==3"]
    assert lock.lock_lines(raw) == ["alpha==3", "Foo_Bar==1.0", "zeta==2"]  # pip freeze never prints extras
    assert lock.render(raw).startswith("# Exact pins")


def test_lock_render_adds_platform_pins_once(lock):
    text = lock.render(["uvicorn==0.54.0"])
    assert 'uvloop==0.23.0 ; sys_platform != "win32"' in text and "CONSTRAINTS" in text
    linux_locked = lock.render(["uvicorn==0.54.0", "uvloop==0.23.0"])  # lock regenerated on Linux
    assert linux_locked.count("uvloop==") == 1 and lock.PLATFORM_HEADER not in linux_locked


def test_linux_closure_finds_platform_only_dependencies(lock):
    _needed, missing = lock.linux_closure(["uvicorn[standard]"])
    assert "uvloop" in missing  # Linux-only extra dependency that pip freeze on Windows never sees
    win = {**lock.LINUX_ENV, "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"}
    needed, missing_win = lock.linux_closure(["uvicorn[standard]"], env=win)
    assert "uvloop" not in missing_win and {"click", "h11", "httptools", "watchfiles"} <= needed
    assert "colorama" in lock.linux_closure(["tqdm"], env=win)[0]  # Windows-only marker ...
    assert "colorama" not in lock.linux_closure(["tqdm"])[0]  # ... skipped for Linux
    assert lock.linux_closure(["uvicorn"])[1] == {}  # extras are only followed when requested


def test_lock_covers_the_linux_dependency_closure(lock):
    assert lock.platform_gaps() == []
    lock_text = _text("requirements.lock")
    for name, pin in lock.PLATFORM_PINS.items():
        assert f"{name}=={pin.version} ; {pin.marker}" in lock_text


# --- pyproject -------------------------------------------------------------------------------------


def test_pyproject_metadata_and_tool_config():
    data = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert (project["name"], project["version"], project["requires-python"]) == ("aadhi-eduengine", "2.0.0", ">=3.11")
    assert data["tool"]["setuptools"]["dynamic"]["dependencies"]["file"] == ["requirements.txt"]
    assert any(d.startswith("boto3") for d in project["optional-dependencies"]["s3"])
    pytest_cfg = data["tool"]["pytest"]["ini_options"]
    assert pytest_cfg["testpaths"] == ["tests"] and pytest_cfg["asyncio_mode"] == "auto"
    assert any(m.startswith("slow:") for m in pytest_cfg["markers"])
    assert any(m.startswith("network:") for m in pytest_cfg["markers"])
    ruff = data["tool"]["ruff"]
    assert ruff["line-length"] == 120 and ruff["target-version"] == "py311"
    assert set(ruff["lint"]["select"]) >= {"E", "F", "W", "I", "B", "UP", "ASYNC", "S"}
    assert "S324" in ruff["lint"]["per-file-ignores"]["tests/**"]  # sha1/md5 as fixture content hashes


@pytest.mark.skipif(shutil.which("ruff") is None and importlib.util.find_spec("ruff") is None, reason="ruff missing")
def test_owned_paths_are_ruff_clean():
    paths = [str(ROOT_DIR / p) for p in ("aadhi/evals", "tests/evals", "scripts", "evals", "docker")]
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", *paths], capture_output=True, text=True,
                          cwd=ROOT_DIR, timeout=120, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- docker: image ------------------------------------------------------------------------------------


def _docker_regex(pattern: str) -> re.Pattern[str]:
    """moby/patternmatcher semantics: `*` and `?` stay inside one path segment, `**` crosses them."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
            continue
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[":
            end = pattern.index("]", i)
            out += pattern[i:end + 1]
            i = end
        else:
            out += re.escape(c)
        i += 1
    return re.compile(out + r"\Z")


def _dockerignored(path: str, patterns: list[str]) -> bool:
    """True if `path` (or a parent directory) is excluded from the build context."""
    parts = path.strip("/").split("/")
    prefixes = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    ignored = False
    for raw in patterns:
        negate = raw.startswith("!")
        rx = _docker_regex(raw.lstrip("!").strip("/"))
        if any(rx.match(p) for p in prefixes):
            ignored = not negate
    return ignored


def _dockerignore_patterns() -> list[str]:
    lines = [ln.strip() for ln in _text(".dockerignore").splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def _dockerfile_instructions() -> list[str]:
    """Dockerfile instructions with line continuations joined (comments dropped)."""
    out: list[str] = []
    buf = ""
    for line in _text("Dockerfile").splitlines():
        if line.lstrip().startswith("#") and not buf:
            continue
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        full = (buf + line).strip()
        buf = ""
        if full:
            out.append(full)
    return out


def _copy_sources(instructions: list[str]) -> list[tuple[str, list[str]]]:
    """(stage, sources) of every COPY from the build context (not --from=...)."""
    stage, out = "", []
    for ins in instructions:
        if ins.startswith("FROM "):
            stage = ins.split()[-1]
        elif ins.startswith("COPY ") and "--from=" not in ins:
            args = [a for a in ins.split()[1:] if not a.startswith("--")]
            out.append((stage, args[:-1]))
    return out


def test_dockerfile_contract():
    text = _text("Dockerfile")
    assert re.search(r"^FROM node:\$\{NODE_VERSION\}-slim AS web", text, re.M)
    assert "ARG NODE_VERSION=22" in text and "ARG PYTHON_VERSION=3.11" in text
    assert "npm ci" in text and "playwright install --with-deps chromium" in text
    for pkg in ("ffmpeg", "texlive-latex-base", "texlive-latex-extra", "texlive-fonts-recommended",
                "texlive-science", "dvisvgm", "cm-super", "fonts-noto-core"):
        assert pkg in text
    assert re.search(r"^USER aadhi$", text, re.M)
    assert "HEALTHCHECK" in text and "/healthz" in text
    assert 'ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/app/entrypoint.sh"]' in text
    assert 'CMD ["uvicorn", "aadhi.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]' in text
    assert text.rstrip().splitlines()[-1] == "FROM runtime AS app"  # default build target
    assert "sed -i 's/\\r$//' docker/app/entrypoint.sh" in text  # CRLF checkouts still produce a runnable script


def test_dockerfile_installs_runtime_dependencies_only():
    installs = [i for i in _dockerfile_instructions() if i.startswith("RUN pip install")]
    assert installs == ["RUN pip install -c /tmp/requirements.lock -r /tmp/requirements.txt boto3"]
    assert "requirements-dev" not in _text("Dockerfile")


def test_dockerfile_copies_an_allow_list():
    copies = _copy_sources(_dockerfile_instructions())
    runtime = [src for stage, srcs in copies if stage == "runtime" for src in srcs]
    assert "." not in runtime and "./" not in runtime
    assert {"aadhi/", "alembic/", "alembic.ini", "server.py", "web/", "docker/app/"} <= set(runtime)
    assert all(not src.startswith(("tests", "docs", "evals", "scripts", ".env")) for src in runtime)
    patterns = _dockerignore_patterns()
    for _stage, srcs in copies:
        for src in srcs:
            matches = [p for p in ROOT_DIR.glob(src.rstrip("/"))] if any(c in src for c in "*?[") else [
                ROOT_DIR / src.rstrip("/")]
            assert matches and all(m.exists() for m in matches), f"COPY source {src} does not exist"
            for m in matches:
                rel = m.relative_to(ROOT_DIR).as_posix()
                assert not _dockerignored(rel, patterns), f"COPY source {rel} is excluded by .dockerignore"
    for debris in V1_DEBRIS:
        assert not any(re.fullmatch(_docker_regex(src.rstrip("/")).pattern, debris) for src in runtime), debris


def test_dockerfile_ships_every_branding_file():
    from aadhi.compose.base import BGM, LOGO_VIDEO, MASCOT_CLIPS, STATIC_BACKGROUND

    runtime = [src for stage, srcs in _copy_sources(_dockerfile_instructions()) if stage == "runtime" for src in srcs]
    branding = [s for s in runtime if s.startswith("video_template/")]
    for name in {*MASCOT_CLIPS.values(), BGM, LOGO_VIDEO, STATIC_BACKGROUND}:
        assert any(_docker_regex(src).match(f"video_template/{name}") for src in branding), name
        assert (ROOT_DIR / "video_template" / name).is_file(), name
    # Every mascot clip's poster frame (web/js/player/mascot.js posterUrl: <name>.mp4 -> posters/<name>.jpg).
    for clip in set(MASCOT_CLIPS.values()):
        poster = f"video_template/posters/{Path(clip).stem}.jpg"
        assert any(poster.startswith(src) if src.endswith("/") else _docker_regex(src).match(poster)
                   for src in branding), poster
        assert (ROOT_DIR / poster).is_file(), poster
    for old in ("video_template.mp4", "video_template_2.mp4", "video_template_hi.mp4", "video_template_old.mp4"):
        assert not any(_docker_regex(src).match(f"video_template/{old}") for src in branding), old


def test_dockerignore_excludes_secrets_local_state_and_v1_files():
    patterns = _dockerignore_patterns()
    assert {".env", ".venv", "node_modules", "data", ".git", "*.db", "evals/results", "media"} <= set(patterns)
    assert "!.env.example" in patterns
    for path in (*V1_DEBRIS, ".env", ".env.local", "data/aadhi.db", "projects.db", "tests/evals/test_cli.py",
                 "web/tests/player/schedule.test.js", "web/vendor/manifest.json", "node_modules/x/index.js",
                 "aadhi/__pycache__/x.pyc", "evals/fixtures/ohms_law.pdf", "docs/OPERATIONS.md", "temp_x.py",
                 "test_script_3.js", "media/x.mp4"):
        assert _dockerignored(path, patterns), path
    for path in ("web/index.html", "web/render.html", "aadhi/main.py", "aadhi/models.py", "alembic.ini",
                 "alembic/env.py", "server.py", "scripts/vendor.mjs", "package.json", "package-lock.json",
                 "requirements.txt", "requirements.lock", "docker/app/entrypoint.sh", "docker/app/check_chromium.py",
                 "video_template/aadhi_center.mp4", "video_template/bgm.mp3", ".env.example"):
        assert not _dockerignored(path, patterns), path


def test_dockerignore_matcher_semantics():
    assert _dockerignored("a/b/c.pyc", ["**/*.py[cod]"]) and not _dockerignored("a/b/c.py", ["**/*.py[cod]"])
    assert _dockerignored("index.html", ["index.html"]) and not _dockerignored("web/index.html", ["index.html"])
    assert _dockerignored("tests/x/y.py", ["tests"]) and not _dockerignored("x/tests", ["tests"])
    assert not _dockerignored(".env.example", [".env.*", "!.env.example"])


def test_entrypoint_script():
    raw = (DOCKER_APP / "entrypoint.sh").read_bytes()
    text = raw.decode("utf-8").replace("\r\n", "\n")  # autocrlf checkouts; the Dockerfile strips CR again
    assert text.startswith("#!/bin/sh\n")
    assert 'RUN_MIGRATIONS:-0}" = "1"' in text and "python -m aadhi.cli migrate" in text
    assert text.rstrip().endswith('exec "$@"')
    assert "install -d -o 1000 -g 1000" in text


def _shell() -> str | None:
    return shutil.which("sh") or shutil.which("bash")


@pytest.mark.skipif(_shell() is None, reason="no POSIX shell")
def test_entrypoint_shell_syntax():
    proc = subprocess.run([_shell(), "-n", str(DOCKER_APP / "entrypoint.sh")], capture_output=True,
                          text=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(_shell() is None, reason="no POSIX shell")
def test_entrypoint_refuses_an_unusable_tmpdir(tmp_path):
    script = tmp_path / "entrypoint.sh"
    script.write_bytes((DOCKER_APP / "entrypoint.sh").read_bytes().replace(b"\r\n", b"\n"))
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("RUN_MIGRATIONS", "TMPDIR")}
    env.update(DATA_DIR=(tmp_path / "d").as_posix(), STORAGE_LOCAL_DIR=(tmp_path / "s").as_posix())

    def run(tmpdir: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([_shell(), script.as_posix(), "echo", "started"], env={**env, "TMPDIR": tmpdir},
                              capture_output=True, text=True, timeout=60, check=False)

    bad = run((blocker / "sub").as_posix())  # parent is a file: cannot be created
    assert bad.returncode == 1 and "not a writable directory" in bad.stderr and "started" not in bad.stdout
    good = run((tmp_path / "manim-tmp").as_posix())
    assert good.returncode == 0 and good.stdout.strip() == "started"
    assert (tmp_path / "manim-tmp").is_dir()


# --- docker: render-worker Chromium sandbox -------------------------------------------------------------


def test_seccomp_profile_is_generated_and_current(seccomp, capsys):
    assert _text("docker/app/chromium-seccomp.json") == seccomp.render()
    assert seccomp.main(["--check"]) == 0
    capsys.readouterr()


def test_seccomp_check_detects_drift(seccomp, tmp_path, capsys):
    out = tmp_path / "p.json"
    assert seccomp.main(["--output", str(out), "--check"]) == 1
    assert seccomp.main(["--output", str(out)]) == 0
    assert seccomp.main(["--output", str(out), "--check"]) == 0
    out.write_bytes(out.read_bytes().replace(b"\n", b"\r\n"))  # CRLF checkout is still current
    assert seccomp.main(["--output", str(out), "--check"]) == 0
    capsys.readouterr()


def test_seccomp_profile_allows_user_namespaces_and_nothing_dangerous(seccomp):
    profile = json.loads(_text("docker/app/chromium-seccomp.json"))
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO" and profile["defaultErrnoRet"] == 1
    arches = {a["architecture"] for a in profile["archMap"]}
    assert arches == {"SCMP_ARCH_X86_64", "SCMP_ARCH_AARCH64"}

    def unconditional_allows() -> set[str]:
        return {n for r in profile["syscalls"] if r["action"] == "SCMP_ACT_ALLOW" and not r.get("args")
                and not r.get("includes") and not r.get("excludes") for n in r["names"]}

    allowed = unconditional_allows()
    assert {"clone", "setns", "unshare"} <= allowed  # Chromium's namespace sandbox
    assert {"read", "write", "openat", "execve", "futex", "mmap", "epoll_pwait", "statx", "rseq", "prctl",
            "seccomp", "memfd_create", "getrandom", "socketpair", "sendmsg", "recvmsg"} <= allowed
    for name in (*seccomp.ALWAYS_DENIED, "mount", "umount2", "bpf", "perf_event_open", "init_module",
                 "finit_module", "delete_module", "reboot", "ptrace", "kcmp", "settimeofday", "chroot",
                 "open_by_handle_at", "clone3", "socket", "personality"):
        assert name not in allowed, name
    every_name = {n for r in profile["syscalls"] for n in r["names"]}
    assert not set(seccomp.ALWAYS_DENIED) & every_name  # not even conditionally
    gated = {r["includes"]["caps"][0]: set(r["names"]) for r in profile["syscalls"] if "caps" in r.get("includes", {})}
    assert {"mount", "bpf", "pivot_root"} & gated["CAP_SYS_ADMIN"] == {"mount", "bpf"}
    assert gated["CAP_SYS_CHROOT"] == {"chroot"}  # default Docker caps include SYS_CHROOT (Chromium uses it)
    clone3 = next(r for r in profile["syscalls"] if r["names"] == ["clone3"] and r["action"] == "SCMP_ACT_ERRNO")
    assert clone3["errnoRet"] == 38  # ENOSYS -> glibc falls back to clone()
    vsock = next(r for r in profile["syscalls"] if r["names"] == ["socket"])
    assert vsock["args"] == [{"index": 0, "value": 40, "op": "SCMP_CMP_NE"}]
    assert len(seccomp.ALLOWED) == len(set(seccomp.ALLOWED))


def test_chromium_check_uses_the_render_worker_flags(chromium_check):
    from aadhi.compose.screenshot import CHROMIUM_ARGS

    assert chromium_check.chromium_args() == list(CHROMIUM_ARGS)


def test_chromium_check_reports_missing_playwright(chromium_check, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    ok, message = chromium_check.check()
    assert not ok and message.startswith("playwright is not installed")
    assert chromium_check.main() == 2
    assert "playwright is not installed" in capsys.readouterr().err


@pytest.mark.slow
def test_chromium_check_launches_sandboxed_chromium(chromium_check):
    ok, message = chromium_check.check(timeout_s=120)
    assert ok, message
    assert "with sandbox: ok" in message


# --- docker compose ---------------------------------------------------------------------------------------


def _default(value: str) -> str:
    """Innermost default of a `${A:-${B:-x}}` interpolation."""
    m = re.search(r":-([^${}]*)\}+$", value)
    assert m, value
    return m.group(1)


def test_compose_services_and_worker_kinds():
    compose = _yaml("docker-compose.yml")
    services = compose["services"]
    assert {"postgres", "api", "worker", "render-worker", "minio", "manim-sandbox"} <= set(services)
    assert services["postgres"]["image"] == "postgres:16" and "healthcheck" in services["postgres"]
    api = services["api"]
    assert api["environment"]["APP_ENV"] == "production" and api["environment"]["WORKER_MODE"] == "external"
    assert api["environment"]["RUN_MIGRATIONS"] == "1"
    assert api["environment"]["STORAGE_LOCAL_DIR"] == "/data/storage"
    assert "JWT_SECRET" in api["environment"] and "BASE_URL" in api["environment"]
    worker_kinds_expr = services["worker"]["environment"]["WORKER_KINDS"]
    assert worker_kinds_expr.startswith("${WORKER_KINDS:-")  # overridable for the docker-sandbox setup
    worker_kinds = _default(worker_kinds_expr).split(",")
    assert "render_video" not in worker_kinds and set(worker_kinds) <= set(ALL_JOB_KINDS)
    assert services["worker"]["command"] == ["python", "-m", "aadhi.worker"]
    assert services["render-worker"]["environment"]["WORKER_KINDS"] == "render_video"
    for name in ("worker", "render-worker"):
        assert "RUN_MIGRATIONS" not in services[name]["environment"]
        assert services[name]["healthcheck"] == {"disable": True}
    assert services["minio"]["profiles"] == ["s3"]
    assert services["manim-sandbox"]["build"]["dockerfile"] == "docker/manim-sandbox/Dockerfile"
    assert set(compose["volumes"]) >= {"pgdata", "appdata"}


def test_compose_admin_password_is_optional_after_bootstrap():
    text = _text("docker-compose.yml")
    assert "ADMIN_PASSWORD:?" not in text
    assert _yaml("docker-compose.yml")["services"]["api"]["environment"]["ADMIN_PASSWORD"] == "${ADMIN_PASSWORD:-}"


def test_compose_trusts_the_pinned_bridge_gateway():
    compose = _yaml("docker-compose.yml")
    ipam = compose["networks"]["default"]["ipam"]["config"][0]
    subnet = ipaddress.ip_network(_default(ipam["subnet"]))
    gateway = ipaddress.ip_address(_default(ipam["gateway"]))
    assert gateway in subnet
    api_env = compose["services"]["api"]["environment"]
    for value in (api_env["TRUSTED_PROXIES"], api_env["FORWARDED_ALLOW_IPS"]):
        assert "AADHI_GATEWAY" in value and ipaddress.ip_address(_default(value)) == gateway


def test_compose_render_worker_runs_chromium_with_the_seccomp_profile():
    services = _yaml("docker-compose.yml")["services"]
    opts = services["render-worker"]["security_opt"]
    assert opts == ["seccomp=./docker/app/chromium-seccomp.json"]
    assert (ROOT_DIR / opts[0].split("=", 1)[1]).is_file()
    assert "unconfined" not in json.dumps(services) and "SYS_ADMIN" not in json.dumps(services)
    manim = services["manim-worker"]
    host_tmp = "${MANIM_HOST_TMP:-/var/lib/aadhi/manim-tmp}"
    assert manim["environment"]["TMPDIR"] == host_tmp
    assert f"{host_tmp}:{host_tmp}" in manim["volumes"]  # same path on host and container


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=30,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


@pytest.mark.skipif(not _docker_compose_available(), reason="docker compose CLI not installed")
def test_docker_compose_config_without_admin_password(tmp_path):
    """`docker compose` (no daemon needed) must work once ADMIN_PASSWORD is removed from .env."""
    shutil.copy(ROOT_DIR / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / "docker" / "app").mkdir(parents=True)
    shutil.copy(DOCKER_APP / "chromium-seccomp.json", tmp_path / "docker" / "app" / "chromium-seccomp.json")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ADMIN_", "COMPOSE_", "AADHI_", "TRUSTED_", "FORWARDED_", "WORKER_"))}
    env.update(POSTGRES_PASSWORD="test-only", JWT_SECRET="test-only-jwt-secret-0123456789abcdef", BASE_URL="https://t.invalid")
    base = ["docker", "compose", "--project-directory", str(tmp_path), "-f", str(tmp_path / "docker-compose.yml")]
    for profiles in ([], ["--profile", "s3", "--profile", "sandbox", "--profile", "docker-sandbox"]):
        proc = subprocess.run([*base, *profiles, "config", "--format", "json"], capture_output=True, text=True,
                              cwd=tmp_path, env=env, timeout=120, check=False)
        assert proc.returncode == 0, proc.stderr
    config = json.loads(proc.stdout)
    api = config["services"]["api"]["environment"]
    assert api["ADMIN_PASSWORD"] == "" and api["TRUSTED_PROXIES"] == "172.30.0.1"
    assert api["FORWARDED_ALLOW_IPS"] == "172.30.0.1"
    assert config["services"]["render-worker"]["security_opt"] == ["seccomp=./docker/app/chromium-seccomp.json"]
    env["WORKER_KINDS"] = "import_legacy,cleanup"
    proc = subprocess.run([*base, "config", "--format", "json"], capture_output=True, text=True, cwd=tmp_path,
                          env=env, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    services = json.loads(proc.stdout)["services"]
    assert services["worker"]["environment"]["WORKER_KINDS"] == "import_legacy,cleanup"
    assert services["render-worker"]["environment"]["WORKER_KINDS"] == "render_video"


# --- CI --------------------------------------------------------------------------------------------------


def test_ci_workflows():
    ci = _yaml(".github/workflows/ci.yml")
    steps = "\n".join(str(s.get("run", "")) for job in ci["jobs"].values() for s in job["steps"])
    assert 'pytest -m "not slow and not network"' in steps
    assert "pip install -c requirements.lock -r requirements.txt -r requirements-dev.txt" in steps
    assert "ruff check aadhi tests scripts evals docker" in steps
    for check in ("gen_env_example.py --check", "gen_chromium_seccomp.py --check", "make_fixtures.py --check"):
        assert check in steps
    assert "npm ci" in steps and "npm run typecheck" in steps and "npm test" in steps
    uses = [s.get("uses", "") for job in ci["jobs"].values() for s in job["steps"]]
    assert any(u.startswith("actions/setup-python") for u in uses) and any(u.startswith("actions/setup-node") for u in uses)
    docker_steps = ci["jobs"]["docker"]["steps"]
    build = next(s for s in docker_steps if str(s.get("uses", "")).startswith("docker/build-push-action"))
    assert build["with"]["load"] is True
    docker_runs = "\n".join(str(s.get("run", "")) for s in docker_steps)
    assert "seccomp=docker/app/chromium-seccomp.json" in docker_runs and "check_chromium.py" in docker_runs
    configs = [s for s in docker_steps if "docker compose" in str(s.get("run", ""))]
    assert any("ADMIN_PASSWORD" not in (s.get("env") or {}) for s in configs)  # works after the first login
    slow = _yaml(".github/workflows/slow.yml")
    on = slow.get("on", slow.get(True))  # PyYAML parses the bare key `on` as True
    assert "workflow_dispatch" in on
    slow_steps = "\n".join(str(s.get("run", "")) for s in slow["jobs"]["slow"]["steps"])
    assert "pytest -m slow" in slow_steps and "ffmpeg" in slow_steps
    assert "-c requirements.lock -r requirements.txt -r requirements-dev.txt" in slow_steps


# --- node / launch / ignore rules ---------------------------------------------------------------------


def test_package_scripts_and_lockfile_in_sync():
    pkg = json.loads((ROOT_DIR / "package.json").read_text(encoding="utf-8"))
    for script in ("typecheck", "test", "vendor", "postinstall"):
        assert script in pkg["scripts"]
    assert pkg["scripts"]["postinstall"] == "node scripts/vendor.mjs"
    # Node 22's test runner treats a bare directory argument as a module: use a glob.
    assert "web/tests/" not in pkg["scripts"]["test"].split() and "*.test." in pkg["scripts"]["test"]
    lock = json.loads((ROOT_DIR / "package-lock.json").read_text(encoding="utf-8"))
    root = lock["packages"][""]
    assert root.get("dependencies", {}) == pkg.get("dependencies", {})
    assert root.get("devDependencies", {}) == pkg.get("devDependencies", {})
    assert (SCRIPTS / "vendor.mjs").is_file()


def test_launch_configurations():
    data = json.loads((ROOT_DIR / ".claude" / "launch.json").read_text(encoding="utf-8"))
    configs = {c["name"]: c for c in data["configurations"]}
    server = configs["aadhi-server"]
    assert server["runtimeExecutable"] == ".venv/Scripts/python.exe"
    assert server["runtimeArgs"] == ["server.py"] and server["port"] == 8000
    assert configs["aadhi-worker"]["runtimeArgs"] == ["-m", "aadhi.worker"]


def test_gitignore_rules():
    lines = [ln.strip() for ln in (ROOT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()]
    for required in ("data/", ".venv/", "node_modules/", "web/vendor/", ".ruff_cache/", ".pytest_cache/",
                     "evals/results/", "*.db", ".env", "media/", "static_videos/", "images/", "final_videos/"):
        assert required in lines, required
    assert "package-lock.json" not in lines
    assert "test*.js" not in lines and "/test*.js" in lines  # root-anchored scratch patterns only


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT_DIR / ".git").exists(), reason="not a git checkout")
@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("web/tests/player/schedule.test.js", False),
        ("web/tests/test_helpers.js", False),
        ("evals/fixtures/ohms_law.pdf", False),
        ("docker/app/chromium-seccomp.json", False),
        ("package-lock.json", False),
        (".env.example", False),
        ("evals/results/run/report.json", True),
        ("test_scratch.js", True),
        (".env", True),
        ("data/aadhi.db", True),
    ],
)
def test_git_check_ignore(path, ignored):
    proc = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT_DIR, capture_output=True,
                          timeout=30, check=False)
    assert (proc.returncode == 0) is ignored


def test_fixture_and_docs_exist():
    for rel in ("evals/make_fixtures.py", "evals/fixtures/legacy_ohms_law.json", "evals/fixtures/sample_template.docx",
                "docs/OPERATIONS.md", "docs/EVALS.md"):
        assert (ROOT_DIR / rel).is_file(), rel
    ops = _text("docs/OPERATIONS.md")
    assert "import-legacy --db projects.db" in ops and "initial_admin_password.txt" in ops
    assert "LLM_PROVIDER=fake" in ops
    for needed in ("chromium-seccomp.json", "check_chromium.py", "kernel.apparmor_restrict_unprivileged_userns",
                   "install -d -o 1000 -g 1000", "WORKER_KINDS=import_legacy,cleanup", "S3_ACCESS_KEY_ID=",
                   "S3_REGION=us-east-1", "AADHI_GATEWAY", "-c requirements.lock"):
        assert needed in ops, needed
