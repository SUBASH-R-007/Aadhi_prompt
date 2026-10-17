"""``GET /api/meta``'s classic voice and language lists, pinned (B3 plan §A B3-0 task 3, §3.1 item 11, §D 28).

B3's voice personas live in ``aadhi/providers/tts/personas.py`` and are never added to ``voices.CATALOGUE`` or
``LANGUAGES``: the Studio's classic voice picker and language list must not change by accident. The golden is
``tests/invariance/goldens/_api/meta_voices.json``: every language, and per TTS provider its id, label, word-timing
flag and voice list (the offline ``fake`` provider, listed only in tests, is left out; ``configured`` depends on the
keys, not on the catalogue). Regenerate only for an approved catalogue change, with ``AADHI_UPDATE_GOLDENS=1``.
"""

from __future__ import annotations

import difflib
from typing import Any

import pytest

from tests.invariance import goldens as g

GOLDEN = ("_api", "meta_voices")


def voice_lists(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "languages": body["languages"],
        "tts_providers": [{k: p[k] for k in ("id", "label", "word_timings", "voices")}
                          for p in body["tts"]["providers"] if p["id"] != "fake"],
    }


def test_the_classic_voice_and_language_lists_are_unchanged(api) -> None:
    _, client = api.editor("alice")
    r = client.get("/api/meta")
    assert r.status_code == 200, r.text
    doc = voice_lists(r.json())
    assert {p["id"] for p in doc["tts_providers"]} >= {"edge", "openai", "gemini"}
    path = g.golden_path(*GOLDEN)
    text = g.dumps(doc)
    if g.updating():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.is_file(), f"missing golden {path}: generate it with {g.UPDATE_ENV}=1"
    expected = path.read_text(encoding="utf-8")
    if text != expected:
        diff = "".join(difflib.unified_diff(expected.splitlines(True), text.splitlines(True),
                                            fromfile="goldens/_api/meta_voices.json", tofile="now", n=2))
        pytest.fail(f"GET /api/meta's classic voice or language list changed (B3 plan §3.1 item 11):\n{diff[:8000]}")
