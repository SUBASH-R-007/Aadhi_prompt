"""Differential pixel test of the classic mode: render the fixture with the merge base's code and with HEAD's, in
the same container (or on the same machine), and compare (docs/DYNAMIC_VIDEO_DESIGN.md section 3.6).

Rendered pixels depend on the Chromium build and the OS, and MP4 bytes on the x264 build, so frames are never
compared with stored hashes from another machine; both renders here share the tools, the synthetic media
(``OUT/media``) and the settings, and only the code differs.

    # CI (inside the Docker test image): the merge base checked out at /base (with its own `npm ci`)
    python tests/invariance/differential.py --base-root /base --head-root /src --out /out
    # locally: any git revision (exported read-only with `git archive`; web/vendor copied from HEAD)
    python tests/invariance/differential.py --base-ref origin/main --out OUT

A batch that changes the classic output on purpose (approved in review) passes ``--declared-change``: the report
is written and shown, and the exit code is 0.

Exit codes: 0 equal within the tolerance (or a declared change), 1 different or a render failed, 2 tools missing.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import classic_fixture as fx  # noqa: E402
import pixeldiff  # noqa: E402


def export_ref(head_root: Path, ref: str, dest: Path) -> Path:
    """``git archive`` of ``ref`` extracted into ``dest`` (read-only for the repository)."""
    data = subprocess.run(["git", "-C", str(head_root), "archive", "--format=tar", ref], capture_output=True,
                          check=True, timeout=600, stdin=subprocess.DEVNULL).stdout
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:  # pragma: no cover - Python without the extraction filters
            root = dest.resolve()
            for member in tar.getmembers():
                target = (dest / member.name).resolve()
                if root not in target.parents and target != root or member.issym() or member.islnk():
                    raise RuntimeError(f"unsafe archive member {member.name!r}")
            tar.extractall(dest)  # noqa: S202 - members checked above
    # Fresh modification times: git archive stamps the commit time, and temp-file cleaners (Windows Storage Sense)
    # delete "old" files under the temp folder in the middle of a run.
    for path in dest.rglob("*"):
        os.utime(path)
    return dest


def rev_parse(head_root: Path, ref: str) -> str | None:
    out = subprocess.run(["git", "-C", str(head_root), "rev-parse", ref], capture_output=True, text=True,
                         timeout=60, stdin=subprocess.DEVNULL)
    return out.stdout.strip() or None if out.returncode == 0 else None


def ensure_vendor(root: Path, head_root: Path) -> str:
    """``web/vendor`` of ``root``: its own (``npm ci``), else a copy of HEAD's (reported)."""
    if (root / "web" / "vendor" / "manifest.json").is_file():
        return "own"
    src = head_root / "web" / "vendor"
    if not (src / "manifest.json").is_file():
        raise SystemExit(f"{src} is missing: run `npm ci` first")
    shutil.copytree(src, root / "web" / "vendor", dirs_exist_ok=True)
    return "copied from HEAD"


def resolve_ffmpeg(head_root: Path) -> str:
    """ffmpeg as the app resolves it (FFMPEG_PATH through ``compose.capabilities.resolve_tool``)."""
    if str(head_root) not in sys.path:
        sys.path.insert(0, str(head_root))
    try:
        from aadhi.compose.capabilities import resolve_tool
        from aadhi.config import Settings

        return resolve_tool(Settings(_env_file=None).ffmpeg_path) or "ffmpeg"
    except Exception:  # noqa: BLE001 - fall back to PATH
        return shutil.which("ffmpeg") or "ffmpeg"


RENDER_OUTPUTS = ("report.json", "capture.json", "render.mp4")  # what render_classic.py writes per side


def clear_side(side_out: Path) -> None:
    """Remove an earlier run's outputs of one side, so a render that dies without writing them (killed, or stopped
    with a message) is never compared through the previous run's files."""
    for name in RENDER_OUTPUTS:
        (side_out / name).unlink(missing_ok=True)
    shutil.rmtree(side_out / "states", ignore_errors=True)


def render_side(side: str, root: Path, args: argparse.Namespace) -> int:
    clear_side(args.out / side)
    cmd = [sys.executable, str(HERE / "render_classic.py"), "--code-root", str(root), "--out", str(args.out / side),
           "--media", str(args.out / "media"), "--fixture", args.fixture, "--preset", args.preset]
    if args.max_scenes:
        cmd += ["--max-scenes", str(args.max_scenes)]
    if args.keep_states:
        cmd.append("--keep-states")
    if args.burn_captions:
        cmd.append("--burn-captions")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    print(f"[invariance] rendering {side} ({root})", flush=True)
    t0 = time.monotonic()
    code = subprocess.run(cmd, cwd=root, env=env, timeout=args.timeout, stdin=subprocess.DEVNULL).returncode
    print(f"[invariance] {side}: exit {code} after {time.monotonic() - t0:.0f} s", flush=True)
    return code


# Mean-difference limit for the burned-in caption render (see parse_args); an explicit --max-mean-abs wins.
BURN_MAX_MEAN_ABS = 0.5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    base = p.add_mutually_exclusive_group(required=True)
    base.add_argument("--base-root", type=Path, help="checkout of the merge base")
    base.add_argument("--base-ref", help="git revision of the merge base (exported with git archive)")
    p.add_argument("--head-root", type=Path, default=REPO_ROOT, help="checkout under test (default: this one)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--fixture", default=fx.RENDER_FIXTURE, choices=sorted(fx.FIXTURES))
    p.add_argument("--max-scenes", type=int, help="only the first N scenes (quick local checks; CI renders all)")
    p.add_argument("--preset", default="ultrafast")
    p.add_argument("--keep-states", action="store_true", help="keep both renders' state screenshots")
    p.add_argument("--burn-captions", action="store_true",
                   help="render both sides with burned-in captions (the classic render dialog's option; needs libass)")
    p.add_argument("--declared-change", action="store_true",
                   help="an approved classic change: report the difference, exit 0")
    p.add_argument("--timeout", type=float, default=3600, help="seconds per render")
    pixeldiff.add_tolerance_args(p)
    args = p.parse_args(argv)
    if args.burn_captions and not args.exact and args.max_mean_abs == pixeldiff.Tolerance().max_mean_abs:
        # The burned-in path re-encodes every frame with libass on top, so encoder decisions spread a faint
        # wobble over whole frames: two renders of the SAME code differ by a mean of up to ~0.13 (B0 audit,
        # x264 re-encode noise), far above the 0.05 measured on the plain path. The strict per-pixel limit
        # still applies, and a real caption change (text, size, position) moves thousands of pixels past it.
        args.max_mean_abs = BURN_MAX_MEAN_ABS
    args.out = args.out.resolve()
    args.head_root = args.head_root.resolve()
    if args.base_root is not None:
        args.base_root = args.base_root.resolve()
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "summary.md"):  # a stale verdict never survives a run that stops early
        (args.out / name).unlink(missing_ok=True)
    shutil.rmtree(args.out / "frames", ignore_errors=True)
    base_root = args.base_root or export_ref(args.head_root, args.base_ref, args.out / "base-src")
    vendor = {"base": ensure_vendor(base_root, args.head_root), "head": ensure_vendor(args.head_root, args.head_root)}
    ffmpeg = resolve_ffmpeg(args.head_root)
    report: dict[str, Any]
    codes = {side: render_side(side, root, args) for side, root in (("base", base_root), ("head", args.head_root))}
    if 2 in codes.values():
        report = {"ok": False, "skipped": "ffmpeg or Playwright Chromium is missing", "renders": codes}
        (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(report))
        return 2
    failed = {side: code for side, code in codes.items() if code != 0}
    if failed:  # never compare what an earlier run left; a failed render is never a declared change
        report = {"ok": False, "renders": codes,
                  "problems": [f"the {side} render exited with {code}" for side, code in failed.items()]}
        text = pixeldiff.markdown(report)
        (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        (args.out / "summary.md").write_text(text, encoding="utf-8")
        sys.stdout.write(text)
        return 1
    report = pixeldiff.compare_dirs(args.out / "base", args.out / "head", tol=pixeldiff.tolerance_from(args),
                                    ffmpeg=ffmpeg, out=args.out)
    for side, root in (("base", base_root), ("head", args.head_root)):
        r = json.loads((args.out / side / "report.json").read_text(encoding="utf-8"))
        commit = r.get("commit") or (rev_parse(args.head_root, args.base_ref) if side == "base" and args.base_ref
                                     else None)
        report[side] = {"root": str(root), "commit": commit, "vendor": vendor[side],
                        "seconds": r.get("seconds"), "exit": codes[side]}
    report["declared_change"] = bool(args.declared_change)
    text = pixeldiff.markdown(report)
    (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    (args.out / "summary.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if report["ok"] or args.declared_change else 1


if __name__ == "__main__":
    raise SystemExit(main())
