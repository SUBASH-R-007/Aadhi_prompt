"""Fake aadhi.pipeline.plan_state: plan stored in generation_meta['plan']."""

from __future__ import annotations

from aadhi.pipeline.base import LecturePlan


def load_plan(version):
    raw = (version.generation_meta or {}).get("plan")
    return None if raw is None else LecturePlan.model_validate(raw)


def save_plan(version, plan) -> None:
    version.generation_meta = {**dict(version.generation_meta or {}), "plan": plan.model_dump(mode="json")}
