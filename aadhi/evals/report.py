"""Eval reports: ``report.json`` (machine-readable, diffable) and ``report.md`` (for humans)."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import ROOT_DIR, Settings
from .metrics import HEADLINE_METRICS, METRICS_VERSION, flatten_metrics, get_metric

if TYPE_CHECKING:  # pragma: no cover
    from .runner import FixtureResult, PipelineStages, RunConfig

REPORT_JSON = "report.json"
REPORT_MD = "report.md"


def describe_settings(settings: Settings, engine: str | None = None) -> dict[str, Any]:
    """Non-secret settings that influence results (never includes keys or URLs with credentials).

    ``llm_provider`` / ``llm_models`` describe ``engine`` (the run-wide ``--options`` llm_provider)
    when it names a known engine, else the server default engine; ``default_llm_provider`` is always
    ``LLM_PROVIDER``. Fixtures whose own options pick another engine record it as ``llm_engine``.
    """
    from ..providers.factory import LLM_ENGINES, llm_models

    chosen = engine if engine in (*LLM_ENGINES, "fake") else settings.llm_provider
    return {
        "app_env": settings.app_env,
        "llm_provider": chosen,
        "default_llm_provider": settings.llm_provider,
        "llm_models": llm_models(settings, chosen),
        "tts_provider": settings.tts_provider,
        "image_provider": settings.image_provider,
        "video_provider": settings.video_provider,
        "manim_sandbox": settings.manim_sandbox,
        "manim_quality": settings.manim_quality,
        "default_language": settings.default_language,
        "max_cost_per_lecture_usd": settings.max_cost_per_lecture_usd,
    }


def git_commit(cwd: Path = ROOT_DIR) -> str | None:
    """Short HEAD commit of the checkout, or ``None`` (not a git checkout / git missing)."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH is intended
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def aggregate_metrics(results: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Mean of every numeric metric over fixtures with status ``ok`` (keys sorted)."""
    sums: dict[str, float] = {}
    counts: Counter[str] = Counter()
    for r in results:
        if r.get("status") != "ok":
            continue
        for key, value in flatten_metrics(r.get("metrics") or {}).items():
            sums[key] = sums.get(key, 0.0) + value
            counts[key] += 1
    return {k: round(sums[k] / counts[k], 4) for k in sorted(sums)}


def _fixture_cost(r: Mapping[str, Any]) -> float:
    """Spend of one fixture entry (``cost_usd``; older reports only have the metric)."""
    value = r.get("cost_usd")
    if value is None:
        value = get_metric(r.get("metrics") or {}, "cost.total_usd")
    return float(value or 0.0)


def build_report(
    cfg: RunConfig,
    results: Sequence[FixtureResult],
    stages: PipelineStages,
    settings_info: Mapping[str, Any],
    *,
    started: float,
    finished: float,
) -> dict[str, Any]:
    """Assemble the report dict written to ``report.json``."""
    from .runner import HARNESS_VERSION, STAGE_FUNCTIONS

    fixtures = [r.to_dict() for r in results]
    statuses = Counter(r.status for r in results)
    # Per-fixture spend from the cost meter: includes fixtures that failed before metrics existed.
    total_cost = sum(float(r.cost_usd) for r in results)
    return {
        "harness_version": HARNESS_VERSION,
        "metrics_version": METRICS_VERSION,
        "name": cfg.name,
        "created_at": dt.datetime.fromtimestamp(started, dt.UTC).isoformat(timespec="seconds"),
        "duration_s": round(finished - started, 3),
        "git_commit": git_commit(),
        "config": cfg.describe(),
        "settings": dict(settings_info),
        "stages_available": {s: s in stages.functions for s in STAGE_FUNCTIONS},
        "stages_missing": dict(sorted(stages.missing.items())),
        "summary": {
            "fixtures": len(results),
            "ok": statuses.get("ok", 0),
            "failed": statuses.get("failed", 0),
            "skipped": statuses.get("skipped", 0),
            "total_cost_usd": round(total_cost, 6),
        },
        "aggregate": aggregate_metrics(fixtures),
        "fixtures": fixtures,
    }


def load_report(path: Path) -> dict[str, Any]:
    """Load ``report.json`` from a run directory or a direct file path."""
    path = Path(path)
    if path.is_dir():
        path = path / REPORT_JSON
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "fixtures" not in data:
        raise ValueError(f"{path} is not an eval report")
    return data


# --- markdown ----------------------------------------------------------------------------------


def md_cell(value: Any) -> str:
    """Escape a value for a markdown table cell."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def fmt_value(value: Any, unit: str = "") -> str:
    """Human formatting for metric values (``-`` for missing)."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if unit == "USD":
            return f"${value:.4f}"
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text}%" if unit == "%" else text
    if isinstance(value, int):
        return f"{value}%" if unit == "%" else str(value)
    return str(value)


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(md_cell(h) for h in headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(md_cell(c) for c in row) + " |" for row in rows)
    return lines


def _summary_row(r: Mapping[str, Any]) -> list[Any]:
    m = r.get("metrics") or {}
    errors, warnings = get_metric(m, "lint.errors"), get_metric(m, "lint.warnings")
    return [
        r["name"],
        r["status"] + (f" ({r['failed_stage']})" if r.get("failed_stage") else ""),
        fmt_value(get_metric(m, "structure.scenes")),
        fmt_value(get_metric(m, "pacing.est_minutes")),
        f"{fmt_value(errors)}/{fmt_value(warnings)}",
        fmt_value(get_metric(m, "objectives.taught_pct"), "%"),
        fmt_value(get_metric(m, "objectives.assessed_pct"), "%"),
        fmt_value(get_metric(m, "misconceptions.targeted_pct"), "%"),
        fmt_value(get_metric(m, "grounding.beats_with_refs_pct"), "%"),
        fmt_value(_fixture_cost(r), "USD"),
        f"{r.get('seconds', 0):.1f}s",
    ]


def _fixture_section(r: Mapping[str, Any]) -> list[str]:
    m = r.get("metrics") or {}
    lines = [f"### {md_cell(r['name'])} ({r['status']})", ""]
    lines.append(f"- Source: `{md_cell(r.get('source'))}` ({r.get('kind')})")
    if r.get("options"):
        lines.append(f"- Options: `{md_cell(json.dumps(r['options'], sort_keys=True))}`")
    stages = r.get("stages") or []
    if stages:
        parts = [
            f"{s['name']} {s['status']}" + (f" {s['seconds']:.1f}s" if s.get("seconds") else "") for s in stages
        ]
        lines.append("- Stages: " + " -> ".join(parts))
    if r.get("error"):
        lines.append(f"- Error: {md_cell(r['error'])}")
    for w in (r.get("warnings") or [])[:10]:
        lines.append(f"- Warning: {md_cell(w)}")
    brief = get_metric(m, "brief") or {}
    if brief:
        if brief.get("available"):
            reasons = ", ".join(f"{k} {v}" for k, v in (brief.get("skipped_by_reason") or {}).items())
            lines.append(
                f"- Concept brief: {brief.get('concepts', 0)} concepts; {brief.get('cited_chunks', 0)} of "
                f"{brief.get('chunks', 0)} chunks cited, {brief.get('skipped_chunks', 0)} set aside"
                + (f" ({reasons})" if reasons else "")
                + f", {brief.get('unaccounted_chunks', 0)} neither; coverage {fmt_value(brief.get('coverage'))}"
            )
        else:
            lines.append("- Concept brief: not built (planned directly from the source)")
    by_code = get_metric(m, "lint.by_code") or {}
    if by_code:
        top = sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        lines.append("- Top issue codes: " + ", ".join(f"`{c}` x{n}" for c, n in top))
    for key, label in (
        ("objectives.untaught", "Untaught objectives"),
        ("objectives.unassessed", "Unassessed objectives"),
        ("misconceptions.untargeted", "Untargeted misconceptions"),
        ("grounding.unknown_ref_ids", "Unknown source refs"),
    ):
        values = get_metric(m, key) or []
        if values:
            lines.append(f"- {label}: " + ", ".join(f"`{md_cell(v)}`" for v in values))
    types = get_metric(m, "structure.scene_types") or {}
    if types:
        lines.append("- Scene types: " + ", ".join(f"{k} {v}" for k, v in types.items()))
    panels = get_metric(m, "media.side_panels") or {}
    if panels:
        lines.append("- Side panels: " + ", ".join(f"{k} {v}" for k, v in panels.items()))
    lines.append("")
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    """Human-readable report (summary table, headline means, per-fixture details)."""
    s = report.get("summary", {})
    cfg = report.get("config", {})
    settings = report.get("settings", {})
    lines = [f"# Eval report: {md_cell(report.get('name'))}", ""]
    lines.append(
        f"- Created {report.get('created_at')} in {report.get('duration_s', 0):.1f}s"
        f" - commit `{report.get('git_commit') or 'unknown'}`"
        f" - harness v{report.get('harness_version')} / metrics v{report.get('metrics_version')}"
    )
    lines.append(
        f"- Provider mode `{cfg.get('provider')}`: LLM `{settings.get('llm_provider')}`"
        f" ({', '.join(f'{k}={v}' for k, v in (settings.get('llm_models') or {}).items())}),"
        f" TTS `{settings.get('tts_provider')}`, images `{settings.get('image_provider')}`"
    )
    engines = Counter(str(r.get("llm_engine")) for r in report.get("fixtures", []) if r.get("llm_engine"))
    if any(e != settings.get("llm_provider") for e in engines):
        lines.append("- LLM engines per fixture: " + ", ".join(f"`{e}` {n}" for e, n in sorted(engines.items())))
    flags = [name for name in ("assets", "repair", "render_manim") if cfg.get(name)]
    lines.append(f"- Fixtures `{cfg.get('fixtures_dir')}`; flags: {', '.join(flags) or 'none'}")
    lines.append(
        f"- Result: {s.get('ok', 0)} ok, {s.get('failed', 0)} failed, {s.get('skipped', 0)} skipped"
        f" of {s.get('fixtures', 0)}; total cost {fmt_value(s.get('total_cost_usd'), 'USD')}"
    )
    missing = report.get("stages_missing") or {}
    if missing:
        lines.append("- Unavailable stages: " + "; ".join(f"{k} ({md_cell(v)})" for k, v in missing.items()))
    lines += ["", "## Summary", ""]
    headers = ["Fixture", "Status", "Scenes", "Est. min", "Lint E/W", "Obj. taught", "Obj. assessed",
               "Misc. targeted", "Grounded beats", "Cost", "Time"]
    lines += _table(headers, (_summary_row(r) for r in report.get("fixtures", [])))
    agg = report.get("aggregate") or {}
    if agg:
        lines += ["", f"## Headline metrics (mean over {s.get('ok', 0)} ok fixtures)", ""]
        rows = [
            [m.label, fmt_value(agg.get(m.key), m.unit), m.better or "", f"`{m.key}`"]
            for m in HEADLINE_METRICS
            if m.key in agg
        ]
        lines += _table(["Metric", "Mean", "Better", "Key"], rows)
    lines += ["", "## Fixtures", ""]
    for r in report.get("fixtures", []):
        lines += _fixture_section(r)
    return "\n".join(lines).rstrip() + "\n"


def write_report(out_dir: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report.md`` atomically into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / REPORT_JSON, out_dir / REPORT_MD
    for path, text in (
        (json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n"),
        (md_path, render_markdown(report)),
    ):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return json_path, md_path
