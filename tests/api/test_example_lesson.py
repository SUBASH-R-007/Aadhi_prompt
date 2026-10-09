"""The Studio's bundled example lecture (``web/examples/ohms-law.json``, "Try an example" on New lecture).

It must stay a valid v2 screenplay that the import endpoint accepts without warnings, lint cleanly (no
errors or warnings), need no paid media (no generated pictures, AI video, animations or uploaded assets) and
be served to the signed-in Studio as a static file.
"""

from __future__ import annotations

import json

from aadhi.config import ROOT_DIR
from aadhi.pipeline.validate import lint
from aadhi.schemas.screenplay import AIVideoScene, BoardScene, InteractiveScene, QuizScene, Screenplay, SimulationScene

EXAMPLE = ROOT_DIR / "web" / "examples" / "ohms-law.json"
TITLE = "Example: Ohm's law"


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_is_a_small_clean_lecture_without_paid_media():
    sp = Screenplay.model_validate(_example())
    assert 4 <= len(sp.scenes) <= 6
    assert any(isinstance(s, QuizScene) for s in sp.scenes)
    assert any(isinstance(s, BoardScene) and s.type == "example" for s in sp.scenes)
    for scene in sp.scenes:
        assert not isinstance(scene, (AIVideoScene, SimulationScene, InteractiveScene)), scene.id
        panel = scene.side_panel
        assert panel is None or panel.kind not in {"image", "gif", "manim"}, scene.id
        assert panel is None or panel.override_asset_key is None, scene.id
    assert sp.figures == []
    problems = [i for i in lint(sp) if i.severity in ("error", "warning")]
    assert problems == [], [(i.code, i.message) for i in problems]


def test_example_imports_without_warnings_and_is_served_to_the_studio(api):
    _, c = api.editor("alice")
    served = c.get("/web/examples/ohms-law.json")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/json")
    assert served.json() == _example()
    r = c.post(
        "/api/projects/import",
        files={"file": ("ohms-law.json", served.content, "application/json")},
        data={"title": TITLE},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["warnings"] == []
    assert body["project"]["title"] == TITLE
    assert body["job"]["kind"] == "build_assets"
    counts = body["version"]["issue_counts"]
    assert counts["error"] == 0 and counts["warning"] == 0, counts
