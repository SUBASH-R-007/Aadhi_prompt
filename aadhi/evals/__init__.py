"""Eval harness for lecture generation quality (``python -m aadhi.evals``; see docs/EVALS.md).

* ``runner``    runs ingest -> plan -> script -> lint -> critic [-> repair] -> companion [-> assets]
                over fixtures in an isolated workspace (own SQLite DB + storage, fake providers by
                default) with ``context.EvalJobContext``.
* ``metrics``   deterministic quality metrics of a ``Screenplay`` (pure functions).
* ``report``    ``report.json`` + ``report.md``; ``compare`` diffs two runs; ``judge`` adds an
                optional LLM rubric score (real providers only).
"""

from __future__ import annotations

from .metrics import HEADLINE_METRICS, MetricInputs, compute_metrics, flatten_metrics

__all__ = ["HEADLINE_METRICS", "MetricInputs", "compute_metrics", "flatten_metrics"]
