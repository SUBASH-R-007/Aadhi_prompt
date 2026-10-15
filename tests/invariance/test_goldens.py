"""Classic invariance, part 1: platform-independent goldens (docs/DYNAMIC_VIDEO_DESIGN.md section 3.6).

The classic (Aadhi presenter) mode's stored screenplay, scene hashes, timelines and MP4 command lines must stay
byte-identical while the Dynamic mode is built next to it. A failure prints a diff against ``goldens/``; regenerate
only for a classic change the batch declares and the review approves (README.md)."""

from __future__ import annotations

import difflib
import json

import pytest

from tests.helpers import FakeJobContext

from . import classic_fixture as fx
from . import goldens as g

FIXTURE_NAMES = sorted(fx.FIXTURES)
PURE_DOCUMENTS = tuple(d for d in g.DOCUMENTS if d != "segments")  # segments need the database


def check_or_update(fixture: str, doc: str, value: object) -> None:
    path = g.golden_path(fixture, doc)
    text = g.dumps(value)
    if g.updating():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.is_file(), f"missing golden {path}: generate it with {g.UPDATE_ENV}=1 (tests/invariance/README.md)"
    expected = path.read_text(encoding="utf-8")
    if text != expected:
        diff = "".join(difflib.unified_diff(expected.splitlines(True), text.splitlines(True),
                                            fromfile=f"goldens/{fixture}/{doc}.json", tofile="now", n=2))
        pytest.fail(f"the classic {doc} of {fixture} changed (docs/DYNAMIC_VIDEO_DESIGN.md 3.6):\n{diff[:8000]}")


@pytest.fixture(scope="module")
def documents() -> dict[str, dict[str, object]]:
    return {name: g.build_documents(name) for name in FIXTURE_NAMES}


@pytest.mark.parametrize("doc", PURE_DOCUMENTS)
@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_classic_golden(documents, fixture: str, doc: str) -> None:
    check_or_update(fixture, doc, documents[fixture][doc])


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_classic_segment_command_lines(fixture: str, app_env, asset_store, tmp_path, monkeypatch) -> None:
    """The render job's ffmpeg commands for the fixture (browser and ffmpeg replaced, see goldens.py)."""
    settings = g.settings_for_goldens(data_dir=tmp_path / "data", render_work_dir=str(tmp_path / "render-work"),
                                      render_output_qa=False)

    def ctx_factory(**kw: object) -> FakeJobContext:
        return FakeJobContext(assets=asset_store, **kw)  # type: ignore[arg-type]

    doc = g.segments_document(fixture, ctx_factory=ctx_factory, store=asset_store, settings=settings,
                              patch=monkeypatch.setattr)
    names = [c["name"] for c in doc["commands"]]
    assert names[0] == "intro" and names[-1] == "final" and len(names) >= 3
    flat = json.dumps(doc)
    assert ":\\\\" not in flat and "/tmp" not in flat and str(tmp_path) not in flat  # no machine paths left
    check_or_update(fixture, "segments", doc)


@pytest.mark.parametrize(("variant", "flags"), [("segments_burn", {"burn_captions": True}),
                                                ("segments_soft", {"soft_subtitles": True})])
@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_classic_caption_variants(fixture: str, variant: str, flags: dict[str, bool], app_env, asset_store, tmp_path,
                                  monkeypatch) -> None:
    """The classic render dialog's caption options: burned-in captions (libass filter, re-encode of the final mux)
    and the soft ``mov_text`` track. Separate variants, since a burned render drops the soft track."""
    settings = g.settings_for_goldens(data_dir=tmp_path / "data", render_work_dir=str(tmp_path / "render-work"),
                                      render_output_qa=False)

    def ctx_factory(**kw: object) -> FakeJobContext:
        return FakeJobContext(assets=asset_store, **kw)  # type: ignore[arg-type]

    doc = g.segments_document(fixture, ctx_factory=ctx_factory, store=asset_store, settings=settings,
                              patch=monkeypatch.setattr, **flags)
    flat = json.dumps(doc)
    assert ":\\\\" not in flat and "/tmp" not in flat and str(tmp_path) not in flat  # no machine paths left
    final = doc["commands"][-1]
    assert final["name"] == "final"
    if flags.get("burn_captions"):
        assert "captions.burn.srt" in doc["side_files"] and "[vout]" in final["args"]
        assert "libx264" in final["args"] and "mov_text" not in final["args"]
        assert any("FontSize=22," in line for lines in final["files"].values() for line in lines)
    else:
        assert "mov_text" in final["args"] and "[vout]" not in final["args"]
    check_or_update(fixture, variant, doc)


@pytest.mark.parametrize("fixture", FIXTURE_NAMES)
def test_stored_documents_round_trip_unchanged(documents, fixture: str) -> None:
    """Validating a stored classic document and dumping it again adds nothing: new fields stay inert."""
    from aadhi.schemas.screenplay import Screenplay
    from aadhi.schemas.timeline import Timeline

    sp = documents[fixture]["screenplay"]
    assert Screenplay.model_validate(sp).model_dump(mode="json") == sp
    for doc in ("timeline", "preview_timeline"):
        tl = documents[fixture][doc]
        assert Timeline.model_validate(tl).model_dump(mode="json") == tl


def test_classic_fixtures_have_no_presentation(documents) -> None:
    """The goldens are of the classic mode: no ``presentation`` in the screenplay or the timeline."""
    for name in FIXTURE_NAMES:
        assert "presentation" not in documents[name]["screenplay"]
        assert "presentation" not in documents[name]["timeline"]


def test_capture_documents_match_their_fixtures() -> None:
    """``capture.json`` (recorded from a real render page) belongs to the fixture's current timeline."""
    from aadhi.compose.timeline import build_timeline

    for name in FIXTURE_NAMES:
        capture = g.read_capture(name)
        sp = fx.load_screenplay(name)
        tl = build_timeline(sp, fx.synthetic_manifest(sp, fixture=name), settings=g.settings_for_goldens(),
                            version_id=1, revision=1)
        assert len(capture["scenes"]) == len(tl.scenes), name
        for index, states in capture["scenes"].items():
            scene = tl.scenes[int(index)]
            assert states and states[0]["t"] == 0.0
            assert all(0.0 <= s["t"] < scene.duration for s in states), (name, index)
            assert all(s["key"].startswith(f"s{index}-") for s in states), (name, index)
        assert len(capture["intro"]) == len(tl.intro.cards)


def test_settings_for_goldens_ignore_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_PRESET", "veryslow")
    monkeypatch.setenv("SYNC_WORD_ANCHORS", "false")
    s = g.settings_for_goldens()
    from aadhi.config import Settings

    assert s.render_preset == Settings.model_fields["render_preset"].default
    assert s.sync_word_anchors is Settings.model_fields["sync_word_anchors"].default


def test_synthetic_manifest_is_deterministic() -> None:
    for name in FIXTURE_NAMES:
        a = fx.synthetic_manifest(fx.load_screenplay(name), fixture=name).model_dump(mode="json")
        b = fx.synthetic_manifest(fx.load_screenplay(name), fixture=name).model_dump(mode="json")
        assert a == b
        assert set(a["scene_hashes"]) == {s.id for s in fx.visible_scenes(fx.load_screenplay(name))}


def test_default_generation_options_json() -> None:
    """``GenerationOptions()`` as old clients and ``regenerate`` send it: the classic default, field for field."""
    from aadhi.pipeline.base import GenerationOptions

    check_or_update("_api", "generation_options", GenerationOptions().model_dump(mode="json"))
