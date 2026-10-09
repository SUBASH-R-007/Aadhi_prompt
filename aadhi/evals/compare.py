"""Diff two eval runs (``python -m aadhi.evals compare A B``).

Run-level means are recomputed over the fixtures that are ``ok`` in *both* runs, so a fixture that
newly fails cannot drop out of the candidate's mean and make it look better. Fixture status changes
(ok in A, failed/skipped/missing in B) and a rising failure count are regressions of their own.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .metrics import HEADLINE_METRICS, METRIC_DIRECTIONS, flatten_metrics
from .report import aggregate_metrics, md_cell

_EPS = 1e-9
Verdict = Literal["better", "worse", "changed", "same", "added", "removed"]
StatusVerdict = Literal["regressed", "fixed", "changed", "added", "removed"]
_LABELS = {m.key: m.label for m in HEADLINE_METRICS}
# Higher is better; a fixture missing from the candidate run ranks below every status.
_STATUS_RANK = {"ok": 2, "skipped": 1, "failed": 0}
MISSING = "missing"


@dataclass(frozen=True)
class MetricDiff:
    key: str
    label: str
    a: float | None
    b: float | None
    delta: float | None
    verdict: Verdict
    headline: bool


@dataclass(frozen=True)
class FixtureChange:
    """A fixture whose status differs between the runs (``missing`` = not in that run)."""

    name: str
    a: str
    b: str
    verdict: StatusVerdict
    b_failed_stage: str | None = None


@dataclass
class Comparison:
    """Everything ``compare`` reports: metric diffs plus fixture-level changes."""

    diffs: list[MetricDiff]
    changes: list[FixtureChange] = field(default_factory=list)
    common: list[str] = field(default_factory=list)  # fixtures ok in both runs (basis of the means)
    summary_a: dict[str, int] = field(default_factory=dict)
    summary_b: dict[str, int] = field(default_factory=dict)
    fixture: str | None = None

    @property
    def metric_regressions(self) -> list[MetricDiff]:
        return regressions(self.diffs)

    @property
    def status_regressions(self) -> list[FixtureChange]:
        return [c for c in self.changes if c.verdict == "regressed"]

    @property
    def failed_increase(self) -> int:
        """How many more fixtures failed in B than in A (0 for a single-fixture comparison)."""
        if self.fixture:
            return 0
        return max(0, self.summary_b.get("failed", 0) - self.summary_a.get("failed", 0))

    def regressed(self) -> bool:
        """True if any headline metric got worse, any fixture's status got worse, or more failed."""
        return bool(self.metric_regressions or self.status_regressions or self.failed_increase)


def _verdict(key: str, a: float | None, b: float | None) -> tuple[float | None, Verdict]:
    if a is None and b is None:
        return None, "same"
    if a is None:
        return None, "added"
    if b is None:
        return None, "removed"
    delta = round(b - a, 6)
    if abs(delta) <= _EPS:
        return 0.0, "same"
    direction = METRIC_DIRECTIONS.get(key)
    if direction is None:
        return delta, "changed"
    improved = delta < 0 if direction == "lower" else delta > 0
    return delta, "better" if improved else "worse"


def diff_metrics(a: Mapping[str, float], b: Mapping[str, float], *, include_all: bool = False) -> list[MetricDiff]:
    """Compare two flat metric maps. Headline metrics first (in their canonical order), then the
    rest alphabetically; unchanged non-headline metrics are dropped unless ``include_all``."""
    headline_keys = [m.key for m in HEADLINE_METRICS]
    other = sorted((set(a) | set(b)) - set(headline_keys))
    out: list[MetricDiff] = []
    for key in [*headline_keys, *other]:
        if key not in a and key not in b:
            continue
        av, bv = a.get(key), b.get(key)
        delta, verdict = _verdict(key, av, bv)
        headline = key in _LABELS
        if not headline and verdict == "same" and not include_all:
            continue
        out.append(MetricDiff(key, _LABELS.get(key, key), av, bv, delta, verdict, headline))
    return out


def _fixtures(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(r.get("name")): r for r in report.get("fixtures", [])}


def fixture_status(report: Mapping[str, Any], name: str) -> str:
    """Status of fixture ``name`` in ``report`` (``missing`` when it was not part of the run)."""
    entry = _fixtures(report).get(name)
    return str(entry.get("status", MISSING)) if entry is not None else MISSING


def fixture_metrics(report: Mapping[str, Any], name: str) -> dict[str, float]:
    """Flat metrics of one fixture in a report (empty when absent or not ok)."""
    entry = _fixtures(report).get(name)
    if entry is None or entry.get("status") != "ok":
        return {}
    return flatten_metrics(entry.get("metrics") or {})


def common_ok_fixtures(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """Names of fixtures with status ``ok`` in both reports (sorted)."""
    fa, fb = _fixtures(a), _fixtures(b)
    return sorted(n for n in fa.keys() & fb.keys() if fa[n].get("status") == "ok" and fb[n].get("status") == "ok")


def fixture_changes(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[FixtureChange]:
    """Fixtures whose status differs between baseline ``a`` and candidate ``b`` (sorted by name)."""
    fa, fb = _fixtures(a), _fixtures(b)
    out: list[FixtureChange] = []
    for name in sorted(fa.keys() | fb.keys()):
        sa = str(fa[name].get("status")) if name in fa else MISSING
        sb = str(fb[name].get("status")) if name in fb else MISSING
        if sa == sb:
            continue
        stage = fb[name].get("failed_stage") if name in fb else None
        if sa == MISSING:
            verdict: StatusVerdict = "added"
        elif sb == MISSING:
            verdict = "removed" if sa != "ok" else "regressed"
        else:
            ra, rb = _STATUS_RANK.get(sa, -1), _STATUS_RANK.get(sb, -1)
            verdict = "regressed" if rb < ra else ("fixed" if sb == "ok" else "changed")
        out.append(FixtureChange(name, sa, sb, verdict, stage))
    return out


def _summary(report: Mapping[str, Any]) -> dict[str, int]:
    s = report.get("summary") or {}
    if s:
        return {k: int(s.get(k, 0)) for k in ("fixtures", "ok", "failed", "skipped")}
    statuses = [str(r.get("status")) for r in report.get("fixtures", [])]
    return {"fixtures": len(statuses), **{k: statuses.count(k) for k in ("ok", "failed", "skipped")}}


def compare_runs(
    a: Mapping[str, Any], b: Mapping[str, Any], *, fixture: str | None = None, include_all: bool = False
) -> Comparison:
    """Full comparison of baseline ``a`` and candidate ``b`` (run means or one fixture)."""
    changes = fixture_changes(a, b)
    if fixture:
        diffs = diff_metrics(fixture_metrics(a, fixture), fixture_metrics(b, fixture), include_all=include_all)
        changes = [c for c in changes if c.name == fixture]
        common = [fixture] if fixture in common_ok_fixtures(a, b) else []
    else:
        common = common_ok_fixtures(a, b)
        keep = set(common)
        agg_a = aggregate_metrics(r for r in a.get("fixtures", []) if r.get("name") in keep)
        agg_b = aggregate_metrics(r for r in b.get("fixtures", []) if r.get("name") in keep)
        diffs = diff_metrics(agg_a, agg_b, include_all=include_all)
    return Comparison(diffs, changes, common, _summary(a), _summary(b), fixture)


def compare_reports(
    a: Mapping[str, Any], b: Mapping[str, Any], *, fixture: str | None = None, include_all: bool = False
) -> list[MetricDiff]:
    """Metric diffs only: means over fixtures ok in both runs (default) or one fixture's metrics."""
    return compare_runs(a, b, fixture=fixture, include_all=include_all).diffs


def regressions(diffs: Sequence[MetricDiff]) -> list[MetricDiff]:
    """Headline metrics that got worse."""
    return [d for d in diffs if d.headline and d.verdict == "worse"]


def _num(v: float | None) -> str:
    if v is None:
        return "-"
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _delta(v: float | None) -> str:
    if v is None:
        return ""
    return ("+" if v > 0 else "") + _num(v)


def format_diff(
    diffs: Sequence[MetricDiff],
    *,
    a_name: str = "A",
    b_name: str = "B",
    fmt: Literal["text", "md", "json"] = "text",
) -> str:
    """Render a diff table as aligned text, a markdown table or JSON."""
    if fmt == "json":
        return json.dumps([asdict(d) for d in diffs], indent=2)
    rows = [[d.label if d.headline else d.key, _num(d.a), _num(d.b), _delta(d.delta), d.verdict] for d in diffs]
    headers = ["metric", a_name, b_name, "delta", "verdict"]
    if fmt == "md":
        lines = ["| " + " | ".join(md_cell(h) for h in headers) + " |", "|---|---:|---:|---:|---|"]
        lines += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in rows]
        return "\n".join(lines) + "\n"
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    widths = [min(w, 60) for w in widths]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            (str(c)[:60].ljust(widths[i]) if i in (0, 4) else str(c).rjust(widths[i])) for i, c in enumerate(cells)
        ).rstrip()

    out = [line(headers), line(["-" * w for w in widths])]
    out += [line(r) for r in rows]
    return "\n".join(out) + "\n"


def _counts(s: Mapping[str, int]) -> str:
    return f"{s.get('ok', 0)} ok / {s.get('failed', 0)} failed / {s.get('skipped', 0)} skipped"


def _change_text(c: FixtureChange) -> str:
    stage = f" at {c.b_failed_stage}" if c.b_failed_stage and c.b != "ok" else ""
    return f"{c.name}: {c.a} -> {c.b}{stage} ({c.verdict})"


def format_comparison(
    cmp: Comparison, *, a_name: str = "A", b_name: str = "B", fmt: Literal["text", "md", "json"] = "text"
) -> str:
    """Header (fixture sets and status changes) followed by the metric diff table."""
    if fmt == "json":
        return json.dumps(
            {
                "fixture": cmp.fixture,
                "common_ok_fixtures": cmp.common,
                "summary": {a_name: cmp.summary_a, b_name: cmp.summary_b},
                "fixture_changes": [asdict(c) for c in cmp.changes],
                "metrics": [asdict(d) for d in cmp.diffs],
                "regressed": cmp.regressed(),
            },
            indent=2,
        )
    if cmp.fixture:
        scope = f"fixture {cmp.fixture}" + ("" if cmp.common else " (not ok in both runs: metrics of a non-ok run are empty)")
    else:
        scope = f"means over {len(cmp.common)} fixture(s) ok in both runs"
    header = [
        f"{a_name}: {_counts(cmp.summary_a)}; {b_name}: {_counts(cmp.summary_b)}; {scope}",
        *(f"  {_change_text(c)}" for c in cmp.changes),
    ]
    if fmt == "md":
        text = "\n".join(f"- {md_cell(h.strip())}" for h in header) + "\n\n"
    else:
        text = "\n".join(header) + "\n\n"
    return text + format_diff(cmp.diffs, a_name=a_name, b_name=b_name, fmt=fmt)


def regression_summary(cmp: Comparison) -> list[str]:
    """One line per reason the comparison counts as a regression (empty when it does not)."""
    lines: list[str] = []
    if cmp.metric_regressions:
        lines.append(f"{len(cmp.metric_regressions)} headline metric(s) regressed: "
                     + ", ".join(d.label for d in cmp.metric_regressions))
    if cmp.status_regressions:
        lines.append(f"{len(cmp.status_regressions)} fixture(s) regressed: "
                     + ", ".join(_change_text(c) for c in cmp.status_regressions))
    if cmp.failed_increase:
        lines.append(f"failed fixtures increased by {cmp.failed_increase}")
    return lines
