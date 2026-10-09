"""Fake aadhi.compose.chapters."""

from __future__ import annotations


def youtube_chapters(timeline) -> str:
    lines = []
    for scene in timeline.scenes:
        total = int(scene.start)
        lines.append(f"{total // 60:02d}:{total % 60:02d} {scene.title or scene.scene_id}")
    return "\n".join(lines)
