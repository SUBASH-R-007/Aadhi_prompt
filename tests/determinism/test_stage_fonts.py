"""The Dynamic stage's faces on the real render page (real Chromium, the vendored fonts; design §4.6, §7.3; plan B2
§1.3, §2 "Fonts"): what jsdom cannot show.

* **No look changes in B2b.** The B2 form of ``presentation.fonts`` that the B2b resolver stores
  (``faces.resolver_fonts``: B1's faces, with ``indic``) draws the same pixels as the B1 form it replaces, frame for
  frame, in both formats, with Latin, Tamil and Devanagari text and a display id the B1 stage drew as Outfit.
* **Every face before ready, none after.** The render page loads every face of every family the stacks hold before
  ``ready`` (B1's list for the B1 form, the roles' faces for the B2 form) and no face loads while frames are captured;
  a B2-form timeline naming the B2b faces (Lexend, Atkinson Hyperlegible Next, Anek) is drawn with them.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic.stage_page import open_stage

from .test_board_real_page import RICH, _timeline

pytestmark = pytest.mark.slow
TOKEN = "stage.fonts"
TAMIL = "மின்னோட்டம் எவ்வளவு?"
HINDI = "धारा कितनी है?"
FRAMES = (0, 45, 75, 100, 150, 200, 260, 330)  # the question's reveal, the options, the countdown, the answer

_FACES_JS = """() => [...document.fonts].map((f) => `${f.family} ${f.weight} ${f.status}`).sort()"""
_STYLE_JS = """() => ({
  title: getComputedStyle(document.querySelector('.dyn-title')).fontFamily,
  stage: getComputedStyle(document.querySelector('.dyn-stage')).fontFamily,
  code: getComputedStyle(document.querySelector('code.rt-code')).fontFamily,
})"""


def _with(served: dict[str, Any], *, fonts: dict[str, str], language: str = "en-IN",
          question: str = RICH) -> dict[str, Any]:
    out = copy.deepcopy(served)
    out["presentation"]["fonts"] = dict(fonts)
    out["language"] = out["board_language"] = language
    out["scenes"][0]["quiz"]["question"] = question
    return out


async def _frames(real_app, served: dict[str, Any]) -> list[str]:
    async with open_stage(real_app.settings, size=(served["width"], served["height"]), token=TOKEN,
                          base_url=real_app.base, served_timeline=served) as page:
        hashes = []
        for f in FRAMES:
            await page.seek(f)
            hashes.append(hashlib.sha256(await page.capture_png()).hexdigest())
        assert page.errors == [] and page.guard_trips == [], (page.errors, page.guard_trips)
    return hashes


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
@pytest.mark.parametrize(("display", "language", "question"), [
    ("outfit", "en-IN", RICH), ("poppins", "ta-IN", f"{TAMIL} {RICH}"), ("inter", "hi-IN", f"{HINDI} {RICH}")])
async def test_the_resolver_s_b2_form_draws_the_b1_pixels(real_app, fmt: str, display: str, language: str,
                                                          question: str) -> None:
    base = _timeline(fmt, RICH)
    b1 = _with(base, fonts={"display": display, "body": "inter", "mono": "jetbrains_mono"}, language=language,
               question=question)
    b2 = _with(base, fonts=fc.resolver_fonts(display), language=language, question=question)
    assert not fc.is_b2_fonts(b1["presentation"]["fonts"]) and fc.is_b2_fonts(b2["presentation"]["fonts"])
    old, new = await _frames(real_app, b1), await _frames(real_app, b2)
    assert len(set(old)) >= 4, "the frames show the scene moving (a blank capture would compare equal too)"
    assert new == old


async def _faces(real_app, served: dict[str, Any]) -> tuple[list[str], list[str], dict[str, str]]:
    async with open_stage(real_app.settings, size=(served["width"], served["height"]), token=TOKEN,
                          base_url=real_app.base, served_timeline=served) as page:
        at_ready = await page.page.evaluate(_FACES_JS)
        for f in FRAMES:
            await page.seek(f)
        after = await page.page.evaluate(_FACES_JS)
        style = await page.page.evaluate(_STYLE_JS)
        assert page.errors == [] and page.guard_trips == [], (page.errors, page.guard_trips)
    return at_ready, after, style


def _loaded(faces: list[str]) -> set[str]:
    return {f.rsplit(" ", 1)[0] for f in faces if f.endswith(" loaded")}


async def test_every_face_is_loaded_before_ready_and_none_after(real_app) -> None:
    # B1 form: B1's faces (render/fonts.js fontRequests), the lecture's Tamil included
    b1 = _with(_timeline("landscape", RICH), fonts={"display": "outfit", "body": "inter", "mono": "jetbrains_mono"},
               language="ta-IN", question=f"{TAMIL} {RICH}")
    at_ready, after, style = await _faces(real_app, b1)
    assert at_ready == after, "no face was loaded (or started loading) while frames were captured"
    assert not [f for f in after if f.endswith(" loading") or f.endswith(" error")]
    loaded = _loaded(after)
    for family, weights in (("Inter", (300, 400, 600, 700)), ("Outfit", (300, 400, 700, 900)),
                            ("JetBrains Mono", (400, 700)), ("Noto Sans Tamil", (400, 700))):
        assert all(f"{family} {w}" in loaded for w in weights), (family, sorted(loaded))
    assert not any(f.startswith(("Lexend", "Poppins", "Anek", "Atkinson")) for f in loaded), "B2b faces stay unloaded"
    assert style["title"].startswith('Outfit, Inter, "Noto Sans Tamil"'), style

    # B2 form naming the B2b faces: they are loaded before ready and drawn
    fonts = {"display": "lexend", "body": "atkinson_hyperlegible_next", "mono": "jetbrains_mono", "indic": "anek"}
    b2 = _with(_timeline("portrait", RICH), fonts=fonts, language="ta-IN", question=f"{TAMIL} {RICH}")
    at_ready, after, style = await _faces(real_app, b2)
    assert at_ready == after and not [f for f in after if f.endswith(" loading") or f.endswith(" error")]
    loaded = _loaded(after)
    for family, weights in (("Lexend", (400, 600, 700, 900)), ("Atkinson Hyperlegible Next", (400, 600, 700)),
                            ("JetBrains Mono", (400, 700))):
        assert all(f"{family} {w}" in loaded for w in weights), (family, sorted(loaded))
    assert "Anek Tamil 100 800" in loaded, sorted(loaded)
    assert not any(f.startswith(("Outfit", "Inter ", "Noto Sans", "Poppins")) for f in loaded), sorted(loaded)
    assert style["title"].startswith('Lexend, "Atkinson Hyperlegible Next", "Anek Tamil"'), style
    assert style["stage"].startswith('"Atkinson Hyperlegible Next", "Anek Tamil"'), style
    assert style["code"].startswith('"JetBrains Mono"'), style
