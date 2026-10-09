"""Command line: ``python -m aadhi.evals {run,compare,judge,list} ...`` (see docs/EVALS.md)."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..config import ROOT_DIR

DEFAULT_FIXTURES = ROOT_DIR / "evals" / "fixtures"
DEFAULT_RESULTS = ROOT_DIR / "evals" / "results"


def _json_arg(value: str) -> dict[str, Any]:
    """``--options '{"target_minutes": 8}'`` or ``--options @path/to/options.json``."""
    try:
        text = Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON options: {exc}") from exc
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("options must be a JSON object")
    return data


def _positive_float(value: str) -> float:
    try:
        f = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc
    if f <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return f


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for all sub-commands."""
    parser = argparse.ArgumentParser(prog="python -m aadhi.evals", description="Aadhi EduEngine eval harness")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="generate lectures for every fixture and measure them")
    run.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES, help="fixture directory")
    run.add_argument("--provider", choices=("fake", "configured"), default="fake",
                     help="fake = offline deterministic providers; configured = providers from env/.env (costs money)")
    run.add_argument("--out", type=Path, default=None, help="output directory (default evals/results/<timestamp>)")
    run.add_argument("--assets", action="store_true",
                     help="also run the asset stage (TTS, images; fake TTS in fake mode)")
    run.add_argument("--repair", action="store_true", help="run the repair stage after lint+critic and re-lint")
    run.add_argument("--render-manim", action="store_true", help="render Manim during --assets (slow; else disabled)")
    run.add_argument("--only", action="append", default=[], metavar="NAME", help="run only this fixture (repeatable)")
    run.add_argument("--options", type=_json_arg, default={}, help="GenerationOptions overrides (JSON or @file)")
    run.add_argument("--budget-usd", type=_positive_float, default=None,
                     help="max spend per fixture (default MAX_COST_PER_LECTURE_USD)")
    run.add_argument("--timeout", type=_positive_float, default=None, help="max seconds per fixture")
    run.add_argument("--keep-workspace", action="store_true", help="keep the eval database/storage under --out")
    run.add_argument("-q", "--quiet", action="store_true", help="no progress output")

    cmp = sub.add_parser("compare", help="diff the metrics of two runs")
    cmp.add_argument("a", type=Path, help="baseline run directory or report.json")
    cmp.add_argument("b", type=Path, help="candidate run directory or report.json")
    cmp.add_argument("--fixture", default=None, help="compare one fixture instead of the run means")
    cmp.add_argument("--all", action="store_true", help="include unchanged non-headline metrics")
    cmp.add_argument("--format", choices=("text", "md", "json"), default="text")
    cmp.add_argument("--fail-on-regression", action="store_true",
                     help="exit 1 if a headline metric got worse, a fixture's status got worse or more fixtures failed")

    judge = sub.add_parser("judge", help="score a run's lectures with an LLM rubric (real provider only)")
    judge.add_argument("run_dir", type=Path, help="run directory produced by `run`")
    judge.add_argument("--engine", choices=("gemini", "openai", "anthropic"), default=None,
                       help="AI engine of the judge (default LLM_PROVIDER); its API key must be configured")
    judge.add_argument("--model", default=None, help="model id (default: the engine's critic model)")
    judge.add_argument("--budget-usd", type=_positive_float, default=None, help="max spend for the whole judge run")
    judge.add_argument("--only", action="append", default=[], metavar="NAME")
    judge.add_argument("--allow-fake", action="store_true", help=argparse.SUPPRESS)

    lst = sub.add_parser("list", help="list fixtures and their resolved options")
    lst.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    return parser


def _print(text: str = "", *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(text + "\n")
    stream.flush()


def _echo(fixture: str, event: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "progress":
        done = 100 * float(event.get("progress", 0))
        _print(f"[{fixture}] {event.get('stage')} {done:5.1f}% {event.get('message', '')}")
    elif kind == "log" and event.get("level") in ("warning", "error"):
        _print(f"[{fixture}] {event['level']}: {event.get('message', '')}")
    elif kind == "fixture":
        _print(f"[{fixture}] {event.get('message', '')}")


def cmd_run(args: argparse.Namespace) -> int:
    from .fixtures import FixtureError
    from .report import REPORT_MD
    from .runner import RunConfig, run_eval

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    out = args.out or DEFAULT_RESULTS / f"{stamp}-{args.provider}"
    cfg = RunConfig(
        fixtures_dir=args.fixtures,
        out_dir=out,
        provider=args.provider,
        assets=args.assets,
        repair=args.repair,
        only=tuple(args.only),
        option_overrides=args.options,
        budget_usd=args.budget_usd,
        timeout_s=args.timeout,
        keep_workspace=args.keep_workspace,
        render_manim=args.render_manim,
    )
    if args.provider == "configured":
        budget = f"${args.budget_usd:.2f}" if args.budget_usd else "MAX_COST_PER_LECTURE_USD"
        _print(f"provider=configured: real API calls will be made and billed (budget per fixture: {budget})", err=True)
    try:
        report = run_eval(cfg, echo=None if args.quiet else _echo)
    except FixtureError as exc:
        _print(f"error: {exc}", err=True)
        return 2
    s = report["summary"]
    _print(f"{s['ok']} ok, {s['failed']} failed, {s['skipped']} skipped; cost ${s['total_cost_usd']:.4f}")
    for name, reason in (report.get("stages_missing") or {}).items():
        _print(f"  stage {name} unavailable: {reason}")
    for r in report["fixtures"]:
        if r["status"] != "ok":
            _print(f"  {r['name']}: {r['status']} at {r.get('failed_stage') or '-'}: {r.get('error') or ''}")
    _print(f"report: {(out / REPORT_MD).as_posix()}")
    if s["failed"]:
        return 1
    if not s["ok"]:
        _print("nothing was evaluated (every fixture was skipped)", err=True)
        return 3
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from .compare import compare_runs, format_comparison, regression_summary
    from .report import load_report

    try:
        a, b = load_report(args.a), load_report(args.b)
    except (OSError, ValueError) as exc:
        _print(f"error: {exc}", err=True)
        return 2
    cmp = compare_runs(a, b, fixture=args.fixture, include_all=args.all)
    a_name, b_name = str(a.get("name", "A")), str(b.get("name", "B"))
    if a_name == b_name:
        a_name, b_name = f"A:{a_name}", f"B:{b_name}"
    sys.stdout.write(format_comparison(cmp, a_name=a_name, b_name=b_name, fmt=args.format))
    for line in regression_summary(cmp):  # markdown/JSON on stdout stay clean for pasting/parsing
        _print(line, err=args.format != "text")
    return 1 if (args.fail_on_regression and cmp.regressed()) else 0


def cmd_judge(args: argparse.Namespace) -> int:
    from ..config import get_settings
    from .judge import JUDGE_MD, judge_run

    settings = get_settings()
    engine = args.engine or settings.llm_provider
    if engine == "fake" and not args.allow_fake:
        _print("error: judge needs a real LLM provider (--engine gemini|openai|anthropic, or LLM_PROVIDER, "
               "and its API key)", err=True)
        return 2
    try:
        from ..providers.factory import get_llm, llm_configured
    except ModuleNotFoundError as exc:
        _print(f"error: providers are not available: {exc}", err=True)
        return 2
    if not llm_configured(settings, engine):
        _print(f"error: the {engine} engine is not configured (set its API key)", err=True)
        return 2
    if not (args.run_dir / "report.json").is_file():
        _print(f"error: {args.run_dir} has no report.json", err=True)
        return 2
    result = asyncio.run(
        judge_run(args.run_dir, get_llm(settings, engine=engine), settings, model=args.model,
                  budget_usd=args.budget_usd, only=args.only, engine=engine)
    )
    _print(f"overall mean {result['overall_mean']}; cost ${result['cost_usd']:.4f}")
    _print(f"report: {(args.run_dir / JUDGE_MD).as_posix()}")
    return 0 if all(r.get("status") == "ok" for r in result["fixtures"]) else 1


def cmd_list(args: argparse.Namespace) -> int:
    from .fixtures import FixtureError, discover_fixtures, resolve_options

    try:
        fixtures = discover_fixtures(args.fixtures)
        for fx in fixtures:
            opts = resolve_options(fx).model_dump(mode="json", exclude_defaults=True)
            _print(f"{fx.name:24} {fx.kind:10} {fx.path.name:28} {json.dumps(opts, sort_keys=True)}")
    except FixtureError as exc:
        _print(f"error: {exc}", err=True)
        return 2
    return 0


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached/odd streams
                pass


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    _configure_console()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    commands = {"run": cmd_run, "compare": cmd_compare, "judge": cmd_judge, "list": cmd_list}
    return commands[args.command](args)
