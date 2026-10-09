"""Fake aadhi.pipeline.companion."""

from __future__ import annotations

import html


def render_markdown(screenplay) -> str:
    lines = [f"# {screenplay.session_title or 'Companion sheet'}", ""]
    for scene in screenplay.scenes:
        lines.append(f"## {scene.title or scene.id}")
    return "\n".join(lines) + "\n"


def render_html(screenplay) -> str:
    title = html.escape(screenplay.session_title or "Companion sheet")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body><h1>{title}</h1></body></html>"
