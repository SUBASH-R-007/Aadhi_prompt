"""The media library service (``aadhi.library``): text rules, matching, items, use in lectures, statistics."""

from __future__ import annotations

import datetime as dt
import time

import pytest
from sqlalchemy import event, insert, select

from aadhi import library
from aadhi.api.errors import ApiException
from aadhi.models import Asset, AssetRef, LibraryItem, Project, UsageEvent, User
from aadhi.schemas.screenplay import Screenplay

# --- helpers -----------------------------------------------------------------------------------------


def add_user(db, name: str, role: str = "editor") -> User:
    user = User(username=name, password_hash="x", role=role)
    db.add(user)
    db.commit()
    return user


def add_project(db, owner: User, title: str = "Cells") -> Project:
    project = Project(owner_id=owner.id, title=title)
    db.add(project)
    db.commit()
    return project


def add_asset(db, key: str, *, kind: str = "upload", mime: str = "image/png", **kw) -> Asset:
    asset = Asset(key=key, kind=kind, storage_key=f"assets/{kind}/{key}/x.{mime.split('/')[1]}", mime=mime,
                  size_bytes=10, **kw)
    db.add(asset)
    db.commit()
    return asset


def item(db, user: User, key: str, *, title: str = "", kind: str = "image", mime: str = "image/png",
         asset_kind: str = "upload", **kw) -> LibraryItem:
    if db.execute(select(Asset.id).where(Asset.key == key)).scalar_one_or_none() is None:
        add_asset(db, key, kind=asset_kind, mime=mime)
    out = library.add_item(db, user_id=user.id, asset_key=key, kind=kind, source=kw.pop("source", "upload"),
                           title=title, **kw)
    db.commit()
    return out


def screenplay(*scenes: dict) -> Screenplay:
    base = []
    for i, extra in enumerate(scenes, start=1):
        scene = {"id": f"s{i}", "type": "content", "title": f"Scene {i}",
                 "board": [{"id": f"s{i}-i1", "kind": "bullet", "text": "Point"}],
                 "beats": [{"id": f"s{i}-b1", "narration": "Some narration here.", "board_item_id": f"s{i}-i1"}]}
        scene.update(extra)
        if scene["type"] != "content":  # only board scenes have a board
            scene.pop("board")
            scene["beats"] = [{"id": f"s{i}-b1", "narration": "Some narration here."}]
        base.append(scene)
    return Screenplay.model_validate({"session_title": "Cells", "scenes": base})


def image_panel(prompt: str, title: str | None = None, override: str | None = None) -> dict:
    panel = {"kind": "image", "image_prompt": prompt, "rationale": "helps"}
    if title:
        panel["title"] = title
    if override:
        panel["override_asset_key"] = override
    return {"side_panel": panel}


# --- text rules ----------------------------------------------------------------------------------------


def test_tidy_removes_controls_and_bidi_overrides_and_collapses_space():
    assert library.tidy("  Plant\tcell\n‮diagram\x00  ") == "Plant cell diagram"
    assert library.tidy(None) == ""
    assert library.tidy("ஒளிச்சேர்க்கை") == "ஒளிச்சேர்க்கை"  # Tamil (combining marks kept)


def test_strict_cleaners_refuse_and_lenient_cleaners_shorten():
    with pytest.raises(ValueError, match="at most 120"):
        library.clean_title("x" * 121)
    assert len(library.clean_title("word " * 40, strict=False)) <= library.TITLE_MAX
    with pytest.raises(ValueError, match="at most 1000"):
        library.clean_description("y" * 1001)
    assert library.clean_description("y" * 1001, strict=False) == "y" * 1000
    assert library.clean_description("") == ""


def test_keywords_split_tidy_and_drop_repeats_ignoring_case():
    assert library.clean_keywords(["Cell, nucleus", " cell ", "", "Cell wall;Mitochondria"]) == [
        "Cell", "nucleus", "Cell wall", "Mitochondria"]
    assert library.clean_keywords("a,b\nc") == ["a", "b", "c"]
    assert library.clean_keywords(None) == []
    with pytest.raises(ValueError, match="At most 20"):
        library.clean_keywords([f"k{i}" for i in range(21)])
    with pytest.raises(ValueError, match="at most 40"):
        library.clean_keywords(["z" * 41])
    with pytest.raises(ValueError, match="text"):
        library.clean_keywords([3])
    assert len(library.clean_keywords([f"k{i}" for i in range(30)], strict=False)) == 20
    assert library.clean_keywords(["z" * 41], strict=False) == ["z" * 40]


def test_titles_from_file_names_and_prompts():
    assert library.title_from_filename("plant_cell-v2.png", "image") == "plant cell v2"
    assert library.title_from_filename("../../etc/Ohm's law.MP4", "video") == "Ohm's law"
    assert library.title_from_filename("", "image") == "Untitled picture"
    assert library.title_from_filename(None, "video") == "Untitled clip"
    assert library.title_from_prompt("A plant cell with organelles. Soft light, no text.", "image") == (
        "A plant cell with organelles")
    assert library.title_from_prompt("", "video") == "Generated clip"
    assert library.library_kind("image/webp") == "image" and library.library_kind("video/mp4") == "video"
    assert library.library_kind("audio/mpeg") is None


# --- words, stems, scores ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [("cells", "cell"), ("batteries", "battery"), ("boxes", "box"), ("processes", "process"),
     ("processing", "process"), ("running", "run"), ("charged", "charg"), ("charges", "charg"),
     ("charge", "charg"), ("nucleus", "nucleus"), ("analysis", "analysis"), ("wave", "wave"), ("waves", "wave"),
     ("ohm", "ohm"), ("प्रकाश", "प्रकाश")],
)
def test_stem(word, expected):
    assert library.stem(word) == expected


def test_terms_drop_stop_generic_negated_and_numeric_words_in_any_script():
    assert library.terms("A labelled diagram of a plant cell showing the nucleus, no text") == [
        "plant", "cell", "nucleus"]
    assert library.terms("PlantCell IMG_2034 without people") == ["plant", "cell"]
    assert library.terms("प्रकाश संश्लेषण की प्रक्रिया") == ["प्रकाश", "संश्लेषण", "प्रक्रिया"]
    assert library.words("Ohm's LAW") == ["ohm", "s", "law"]


def test_score_prefers_specific_items_and_is_deterministic():
    request = library.terms("A labelled diagram of a plant cell showing the nucleus, mitochondria and cell wall")
    plant = library.profile("Plant cell", "", ["plant cell", "nucleus", "mitochondria", "cell wall"], None)
    animal = library.profile("Animal cell", "", ["animal cell", "nucleus", "cell membrane"], None)
    solar = library.profile("Solar cell", "Photovoltaic panel on a roof", ["photovoltaic"], None)
    circuit = library.profile("Ohm's law circuit", "", ["resistor", "voltage"], None)
    assert library.score(request, plant) == 1.0
    assert library.SUGGESTION_THRESHOLD <= library.score(request, animal) < library.AUTO_USE_THRESHOLD
    assert library.score(request, solar) < library.SUGGESTION_THRESHOLD
    assert library.score(request, circuit) == 0.0
    assert library.score([], plant) == 0.0
    assert [library.score(request, plant) for _ in range(3)] == [1.0, 1.0, 1.0]


def test_description_and_prompt_words_count_less_than_title_and_keywords():
    request = library.terms("mitochondria")
    by_keyword = library.profile("Organelle", "", ["mitochondria"], None)
    by_description = library.profile("Organelle", "Shows the mitochondria", [], None)
    by_prompt = library.profile("Organelle", "", [], "mitochondria close-up")
    assert library.score(request, by_keyword) > library.score(request, by_description)
    assert library.score(request, by_description) == library.score(request, by_prompt) > 0


def test_a_whole_multi_word_keyword_earns_the_phrase_bonus():
    request = library.terms("electric field lines around a point charge")
    phrase = library.profile("Untitled picture", "", ["field lines", "charge"], None)
    split = library.profile("Untitled picture", "", ["lines", "field", "charge"], None)
    assert library.score(request, phrase) > library.score(request, split)


def test_search_text_holds_words_and_stems_with_spaces_around():
    text = library.search_text("Plant Cells", "Mitochondria", ["Cell wall"], "batteries")
    assert text.startswith(" ") and text.endswith(" ")
    for word in ("plant", "cells", "cell", "mitochondria", "wall", "batteries", "battery"):
        assert f" {word} " in text
    assert library.query_terms("the cells of a plant") == ["cell", "plant"]
    assert library.query_terms("the") == ["the"]


# --- items -----------------------------------------------------------------------------------------------


def test_add_item_is_idempotent_and_never_overwrites_the_teachers_words(db_session):
    alice = add_user(db_session, "alice")
    first = item(db_session, alice, "upload-a", title="My cell")
    library.update_item(db_session, first, description="Teacher words", keywords=["cell"])
    db_session.commit()
    again = library.add_item(db_session, user_id=alice.id, asset_key="upload-a", kind="image", source="upload",
                             title="other title", description="other", keywords=["other"], used=True)
    db_session.commit()
    assert again.id == first.id
    assert (again.title, again.description, again.keywords) == ("My cell", "Teacher words", ["cell"])
    assert again.last_used_at is not None
    assert db_session.execute(select(LibraryItem)).scalars().all() == [again]


def test_add_item_fills_only_empty_fields(db_session):
    alice = add_user(db_session, "alice")
    add_asset(db_session, "image-g", kind="image")
    first = library.add_item(db_session, user_id=alice.id, asset_key="image-g", kind="image", source="generated")
    db_session.commit()
    assert first.title == "" and first.prompt is None
    filled = library.add_item(db_session, user_id=alice.id, asset_key="image-g", kind="image", source="generated",
                              title="Plant cell", prompt="a plant cell", provider="fake", model="m1")
    db_session.commit()
    assert (filled.title, filled.prompt, filled.provider, filled.model) == ("Plant cell", "a plant cell", "fake", "m1")
    assert " plant " in filled.search_text


@pytest.mark.parametrize(
    ("asset_kind", "mime", "kind"),
    [("tts", "audio/mpeg", "image"), ("source", "application/pdf", "image"), ("upload", "image/png", "video"),
     ("screenshot", "image/png", "image")],
)
def test_add_item_refuses_anything_but_stored_pictures_and_clips(db_session, asset_kind, mime, kind):
    alice = add_user(db_session, "alice")
    add_asset(db_session, "k-1", kind=asset_kind, mime=mime)
    with pytest.raises(ValueError):
        library.add_item(db_session, user_id=alice.id, asset_key="k-1", kind=kind, source="upload")
    with pytest.raises(ValueError):
        library.add_item(db_session, user_id=alice.id, asset_key="missing", kind="image", source="upload")
    with pytest.raises(ValueError):
        library.add_item(db_session, user_id=alice.id, asset_key="k-1", kind="audio", source="upload")


def test_two_users_with_the_same_bytes_keep_separate_items_and_words(db_session):
    alice, bob = add_user(db_session, "alice"), add_user(db_session, "bob")
    a = item(db_session, alice, "upload-same", title="Alice's heart")
    b = item(db_session, bob, "upload-same", title="Bob's pump")
    library.update_item(db_session, a, keywords=["private words"])
    db_session.commit()
    assert a.id != b.id
    assert library.get_item(db_session, bob.id, b.id).keywords == []
    with pytest.raises(ApiException) as exc:
        library.get_item(db_session, bob.id, a.id)
    assert exc.value.status_code == 404
    with pytest.raises(ApiException):
        library.get_item(db_session, alice.id, 2**63)
    asset = db_session.execute(select(Asset).where(Asset.key == "upload-same")).scalar_one()
    assert asset.meta == {}  # nothing per user ever lands on the shared asset row


def test_list_items_filters_searches_pages_and_hides_lost_media(db_session):
    alice, bob = add_user(db_session, "alice"), add_user(db_session, "bob")
    item(db_session, alice, "upload-1", title="Plant cell", keywords=["mitochondria"])
    item(db_session, alice, "upload-2", title="Water cycle")
    item(db_session, alice, "video-3", title="Cell division", kind="video", mime="video/mp4", asset_kind="video",
         source="generated")
    item(db_session, bob, "upload-4", title="Plant cell")
    rows, total = library.list_items(db_session, alice.id)
    assert total == 3 and [i.asset_key for i, _ in rows] == ["video-3", "upload-2", "upload-1"]
    assert library.list_items(db_session, alice.id, kind="video")[1] == 1
    assert library.list_items(db_session, alice.id, source="generated")[1] == 1
    assert [i.title for i, _ in library.list_items(db_session, alice.id, q="cells")[0]] == ["Cell division", "Plant cell"]
    assert [i.title for i, _ in library.list_items(db_session, alice.id, q="plant mitochon")[0]] == ["Plant cell"]
    assert library.list_items(db_session, alice.id, q="100%_")[1] == 0  # LIKE wildcards are escaped
    page, total = library.list_items(db_session, alice.id, limit=1, offset=1)
    assert total == 3 and [i.asset_key for i, _ in page] == ["upload-2"]
    db_session.execute(Asset.__table__.delete().where(Asset.key == "upload-2"))
    db_session.commit()
    assert library.list_items(db_session, alice.id)[1] == 2


def test_used_in_counts_only_the_users_own_live_lectures(db_session):
    alice, bob = add_user(db_session, "alice"), add_user(db_session, "bob")
    mine, mine2, gone = add_project(db_session, alice), add_project(db_session, alice), add_project(db_session, alice)
    theirs = add_project(db_session, bob)
    gone.deleted_at = dt.datetime.now(dt.timezone.utc)
    add_asset(db_session, "upload-x")
    for p in (mine, mine2, gone, theirs):
        db_session.add(AssetRef(project_id=p.id, asset_key="upload-x"))
    db_session.commit()
    assert library.used_in(db_session, alice.id, ["upload-x", "upload-none"]) == {"upload-x": 2}
    assert library.used_in(db_session, bob.id, ["upload-x"]) == {"upload-x": 1}


def test_update_and_delete_item(db_session):
    alice = add_user(db_session, "alice")
    it = item(db_session, alice, "upload-1", title="Old")
    before = it.updated_at
    time.sleep(0.01)
    updated = library.update_item(db_session, it, title="New title", keywords="kidney, nephron")
    db_session.commit()
    assert (updated.title, updated.keywords) == ("New title", ["kidney", "nephron"])
    assert updated.updated_at > before
    assert library.list_items(db_session, alice.id, q="nephron")[1] == 1
    with pytest.raises(ValueError):
        library.update_item(db_session, updated, title="t" * 200)
    db_session.add(AssetRef(project_id=add_project(db_session, alice).id, asset_key="upload-1"))
    db_session.commit()
    library.delete_item(db_session, updated)
    db_session.commit()
    assert db_session.execute(select(LibraryItem)).first() is None
    assert db_session.execute(select(AssetRef.asset_key)).scalars().all() == ["upload-1"]
    assert db_session.execute(select(Asset.key)).scalars().all() == ["upload-1"]


def test_attach_records_the_ref_once_and_marks_use_without_an_edit(db_session):
    alice = add_user(db_session, "alice")
    project = add_project(db_session, alice)
    it = item(db_session, alice, "upload-1", title="Heart")
    edited = it.updated_at
    assert library.attach_to_project(db_session, it, project) == "upload-1"
    assert library.attach_to_project(db_session, it, project) == "upload-1"
    db_session.commit()
    refs = db_session.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project.id)).scalars().all()
    assert refs == ["upload-1"]
    fresh = db_session.execute(select(LibraryItem).execution_options(populate_existing=True)).scalar_one()
    assert fresh.last_used_at is not None and fresh.updated_at == edited
    db_session.execute(Asset.__table__.delete())
    db_session.commit()
    with pytest.raises(ApiException) as exc:
        library.attach_to_project(db_session, fresh, project)
    assert exc.value.status_code == 409 and exc.value.code == "asset_missing"


def test_record_generated_follows_the_setting_and_never_raises(db_session, app_env):
    alice = add_user(db_session, "alice")
    project = add_project(db_session, alice)
    add_asset(db_session, "image-g1", kind="image")
    args = dict(user_id=alice.id, asset_key="image-g1", kind="image", project_id=project.id, scene_id="s3",
                prompt="Plant cell with labelled organelles. Soft light.", provider="fake", model="fake-image")
    app_env.library_auto_save_generated = False
    assert library.record_generated(db_session, settings=app_env, **args) is None
    app_env.library_auto_save_generated = True
    got = library.record_generated(db_session, settings=app_env, **args)
    db_session.commit()
    assert got is not None and got.source == "generated" and got.title == "Plant cell with labelled organelles"
    assert (got.origin_project_id, got.origin_scene_id, got.provider) == (project.id, "s3", "fake")
    assert got.last_used_at is not None
    assert library.record_generated(db_session, settings=app_env, **{**args, "asset_key": "image-missing"}) is None
    assert library.record_generated(db_session, settings=app_env, **{**args, "kind": "audio"}) is None
    assert library.record_generated(db_session, settings=app_env, **{**args, "user_id": None}) is None
    # The caller's transaction is still usable after a refused record.
    db_session.add(AssetRef(project_id=project.id, asset_key="image-g1"))
    db_session.commit()
    library.update_item(db_session, got, title="Teacher title")
    db_session.commit()
    again = library.record_generated(db_session, settings=app_env, **args)
    db_session.commit()
    assert again is not None and again.id == got.id and again.title == "Teacher title"


# --- matching --------------------------------------------------------------------------------------------


def test_match_is_per_user_and_per_kind(db_session):
    alice, bob = add_user(db_session, "alice"), add_user(db_session, "bob")
    item(db_session, alice, "upload-pc", title="Plant cell", keywords=["nucleus"])
    item(db_session, alice, "video-pc", title="Plant cell timelapse", kind="video", mime="video/mp4",
         asset_kind="video")
    item(db_session, bob, "upload-bob", title="Plant cell", keywords=["nucleus", "chloroplast"])
    found = library.match(db_session, alice.id, "plant cell nucleus", "image")
    assert [(i.asset_key, s) for i, s in found] == [("upload-pc", 1.0)]
    assert [i.asset_key for i, _ in library.match(db_session, alice.id, "plant cell", "video")] == ["video-pc"]
    assert {i.asset_key for i, _ in library.match(db_session, alice.id, "plant cell", None)} == {"upload-pc", "video-pc"}
    assert [i.asset_key for i, _ in library.match(db_session, bob.id, "plant cell nucleus", "image")] == ["upload-bob"]
    assert library.match(db_session, alice.id, "the and of", "image") == []
    assert library.match(db_session, alice.id, "plant cell", "image", threshold=1.01) == []


def test_match_ties_prefer_the_most_recently_used_then_newest(db_session):
    alice = add_user(db_session, "alice")
    older = item(db_session, alice, "upload-1", title="Heart")
    newer = item(db_session, alice, "upload-2", title="Heart")
    assert [i.id for i, _ in library.match(db_session, alice.id, "heart", "image")] == [newer.id, older.id]
    library.attach_to_project(db_session, older, add_project(db_session, alice))
    db_session.commit()
    assert [i.id for i, _ in library.match(db_session, alice.id, "heart", "image")] == [older.id, newer.id]


def test_matcher_scores_old_items_only_when_they_share_a_request_word(db_session, monkeypatch):
    monkeypatch.setattr(library, "MATCH_RECENT", 3)
    monkeypatch.setattr(library, "MATCH_OVERLAP", 2)
    alice = add_user(db_session, "alice")
    old_match = item(db_session, alice, "upload-old", title="Nephron of the kidney")
    item(db_session, alice, "upload-old2", title="Unrelated thing")
    for i in range(3):
        item(db_session, alice, f"upload-new{i}", title=f"Recent picture {i}")
    matcher = library.LibraryMatcher.load(db_session, alice.id, kinds=["image"], texts=["the nephron filters blood"])
    assert {it.asset_key for it in matcher.items} == {"upload-old", "upload-new0", "upload-new1", "upload-new2"}
    assert [i.id for i, _ in matcher.rank("the nephron filters blood", ["image"])] == [old_match.id]


def test_visual_needs_and_suggestions(db_session):
    alice = add_user(db_session, "alice")
    item(db_session, alice, "upload-pc", title="Plant cell", keywords=["nucleus", "cell wall"])
    item(db_session, alice, "video-mi", title="Mitosis", kind="video", mime="video/mp4", asset_kind="video",
         keywords=["cell division"])
    item(db_session, alice, "upload-mi", title="Mitosis stages", keywords=["cell division"])
    sp = screenplay(
        image_panel("A plant cell with its nucleus and cell wall", title="Plant cell"),
        {"type": "ai_video", "video_prompt": "Time-lapse of mitosis, cell division under a microscope",
         "fallback_image_prompt": "Stages of mitosis"},
        image_panel("A plant cell", override="upload-pc"),
        {"side_panel": {"kind": "chart", "rationale": "x",
                        "chart": {"type": "bar", "labels": ["a"], "datasets": [{"label": "d", "data": [1]}]}}},
        {},
    )
    needs = library.visual_needs(sp)
    assert [(n.scene_id, n.kinds, n.overridden) for n in needs] == [
        ("s1", ("image",), False), ("s2", ("video", "image"), False), ("s3", ("image",), True)]
    found = {need.scene_id: ranked for need, ranked in library.suggestions(db_session, alice.id, sp)}
    assert set(found) == {"s1", "s2"}
    assert [i.asset_key for i, _ in found["s1"]] == ["upload-pc"]
    assert [i.asset_key for i, _ in found["s2"]][:2] in (["video-mi", "upload-mi"], ["upload-mi", "video-mi"])
    assert all(score >= library.SUGGESTION_THRESHOLD for ranked in found.values() for _, score in ranked)
    with_overridden = library.suggestions(db_session, alice.id, sp, include_overridden=True)
    assert [n.scene_id for n, _ in with_overridden] == ["s1", "s2", "s3"]
    assert library.suggestions(db_session, add_user(db_session, "bob").id, sp)[0][1] == []


def test_matching_ten_thousand_items_is_bounded_and_fast(db_session):
    alice = add_user(db_session, "alice")
    topics = ["heart", "kidney", "nephron", "neuron", "plant cell", "mitosis", "photosynthesis", "circuit",
              "resistor", "magnet", "lens", "prism", "atom", "molecule", "volcano", "river", "glacier", "desert"]
    now = dt.datetime.now(dt.timezone.utc)
    assets, items = [], []
    for i in range(10_000):
        key = f"upload-{i:05d}"
        topic = topics[i % len(topics)]
        title, words = f"{topic} picture {i}", [topic, f"tag{i % 97}"]
        assets.append({"key": key, "kind": "upload", "storage_key": f"assets/upload/{key}/x.png", "mime": "image/png",
                       "size_bytes": 1, "meta": {}, "created_at": now})
        items.append({"user_id": alice.id, "asset_key": key, "kind": "image", "title": title, "description": "",
                      "keywords": words, "source": "upload",
                      "search_text": library.search_text(title, "", words, None),
                      "created_at": now - dt.timedelta(seconds=i), "updated_at": now - dt.timedelta(seconds=i)})
    db_session.execute(insert(Asset), assets)
    db_session.execute(insert(LibraryItem), items)
    db_session.commit()

    statements: list[str] = []
    engine = db_session.get_bind()

    def count(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", count)
    try:
        start = time.perf_counter()
        found = library.match(db_session, alice.id, "the structure of a nephron in the kidney", "image", limit=5)
        single = time.perf_counter() - start
        sp = screenplay(*[image_panel(f"A detailed {topics[i % len(topics)]} for scene {i}") for i in range(60)])
        start = time.perf_counter()
        suggested = library.suggestions(db_session, alice.id, sp)
        many = time.perf_counter() - start
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert found and all("nephron" in i.title or "kidney" in i.title for i, _ in found)
    assert len(suggested) == 60 and all(len(r) <= 3 for _, r in suggested)
    assert len(statements) <= 4  # two prefilter queries per match / per suggestion run
    assert single < 3.0 and many < 5.0, (single, many)


# --- statistics ------------------------------------------------------------------------------------------


def test_media_cache_stats_counts_reuse_across_lectures(db_session):
    alice, bob = add_user(db_session, "alice"), add_user(db_session, "bob")
    p1, p2, p3 = add_project(db_session, alice), add_project(db_session, alice), add_project(db_session, bob)
    add_asset(db_session, "image-1", kind="image")
    add_asset(db_session, "image-2", kind="image")
    add_asset(db_session, "video-1", kind="video", mime="video/mp4")
    t0 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    db_session.add_all([
        AssetRef(project_id=p1.id, asset_key="image-1", created_at=t0),
        AssetRef(project_id=p2.id, asset_key="image-1", created_at=t0 + dt.timedelta(minutes=5)),
        AssetRef(project_id=p3.id, asset_key="image-1", created_at=t0 + dt.timedelta(minutes=9)),
        AssetRef(project_id=p1.id, asset_key="image-2", created_at=t0),
        UsageEvent(user_id=alice.id, provider="fake", operation="image", units=1, cost_usd=0.04),
        UsageEvent(user_id=alice.id, provider="fake", operation="image", units=1, cost_usd=0.04),
    ])
    db_session.commit()
    item(db_session, alice, "image-1", title="x", asset_kind="image", source="generated")
    stats = library.media_cache_stats(db_session, days=7)
    assert stats["days"] == 7
    assert stats["kinds"]["image"] == {"generated": 2, "reused_from_other_lectures": 2, "provider_calls": 2,
                                       "cost_usd": 0.08, "stored": 2}
    assert stats["kinds"]["video"]["stored"] == 1 and stats["kinds"]["video"]["reused_from_other_lectures"] == 0
    assert stats["library"] == {"items": 1, "by_source": {"upload": 0, "generated": 1, "figure": 0}}
    assert library.media_cache_stats(db_session, days=0)["days"] == 1


# --- bounded work, automatic use, several sources (batch 3 review) ---------------------------------------


def _bulk(db, user: User, n: int, title) -> None:
    """``n`` picture items, item 0 the most recently edited."""
    now = dt.datetime.now(dt.timezone.utc)
    assets, items = [], []
    for i in range(n):
        key = f"upload-b{i:05d}"
        assets.append({"key": key, "kind": "upload", "storage_key": f"assets/upload/{key}/x.png", "mime": "image/png",
                       "size_bytes": 1, "meta": {}, "created_at": now})
        items.append({"user_id": user.id, "asset_key": key, "kind": "image", "title": title(i), "description": "",
                      "keywords": [], "source": "upload", "search_text": library.search_text(title(i), "", [], None),
                      "created_at": now - dt.timedelta(seconds=i), "updated_at": now - dt.timedelta(seconds=i)})
    db.execute(insert(Asset), assets)
    db.execute(insert(LibraryItem), items)
    db.commit()


def _count_scores(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = library._score

    def counted(req, prof):
        calls.append(1)
        return real(req, prof)

    monkeypatch.setattr(library, "_score", counted)
    return calls


def test_rank_scores_at_most_max_candidates_the_most_recent_first(db_session, monkeypatch):
    alice = add_user(db_session, "alice")
    _bulk(db_session, alice, 30, lambda i: f"heart picture {i}")
    matcher = library.LibraryMatcher.load(db_session, alice.id, kinds=["image"], texts=["heart"])
    calls = _count_scores(monkeypatch)
    ranked = matcher.rank("a heart", ["image"], limit=50, max_candidates=5)
    assert len(calls) == 5
    assert {i.asset_key for i, _ in ranked} == {f"upload-b{i:05d}" for i in range(5)}
    calls.clear()
    assert len(matcher.rank("a heart", ["image"], limit=50)) == 30 and len(calls) == 30  # unbounded by default


def test_one_suggestions_run_scores_at_most_max_pairs(db_session, monkeypatch):
    alice = add_user(db_session, "alice")
    _bulk(db_session, alice, 40, lambda i: f"heart picture {i}")
    monkeypatch.setattr(library, "MATCH_MAX_PAIRS", 20)
    calls = _count_scores(monkeypatch)
    sp = screenplay(*[image_panel(f"A heart, view {i}") for i in range(4)])
    found = library.suggestions(db_session, alice.id, sp)
    assert len(found) == 4 and all(len(ranked) == 3 for _, ranked in found)
    assert len(calls) <= 20


def test_suggestions_for_one_scene_equal_those_of_the_whole_run(db_session, monkeypatch):
    monkeypatch.setattr(library, "MATCH_RECENT", 6)
    monkeypatch.setattr(library, "MATCH_OVERLAP", 6)
    monkeypatch.setattr(library, "MATCH_MAX_PAIRS", 12)
    alice = add_user(db_session, "alice")
    _bulk(db_session, alice, 30, lambda i: f"{'heart' if i % 2 else 'kidney'} picture {i}")
    sp = screenplay(image_panel("A heart"), image_panel("A kidney"), image_panel("A kidney next to a heart"))
    full = {n.scene_id: [(i.id, s) for i, s in r] for n, r in library.suggestions(db_session, alice.id, sp)}
    assert all(full.values())
    for sid, expected in full.items():
        one = library.suggestions(db_session, alice.id, sp, scene_ids={sid})
        assert [n.scene_id for n, _ in one] == [sid]
        assert [(i.id, s) for i, s in one[0][1]] == expected


def test_the_media_a_scene_shows_is_not_suggested_for_it(db_session):
    alice = add_user(db_session, "alice")
    item(db_session, alice, "image-shown", title="Plant cell", asset_kind="image", source="generated")
    item(db_session, alice, "image-older", title="Plant cell diagram", asset_kind="image", source="generated")
    sp = screenplay(image_panel("A plant cell", title="Plant cell"), image_panel("A plant cell", title="Plant cell"))
    found = {n.scene_id: [i.asset_key for i, _ in r]
             for n, r in library.suggestions(db_session, alice.id, sp, shown={"s1": "image-shown"})}
    assert found["s1"] == ["image-older"]  # an earlier version of the scene's picture is still a suggestion
    assert "image-shown" in found["s2"]  # another scene may use it


def test_automatic_use_matches_the_image_prompt_never_the_title(db_session):
    alice = add_user(db_session, "alice")
    ohm = item(db_session, alice, "upload-ohm", title="Ohm's law")
    prompts = ["A digital multimeter measuring current in a series circuit",
               "A 12 V battery connected to a 4 ohm resistor",
               "A straight line graph of voltage against current"]
    for prompt in prompts:
        assert library.auto_match(db_session, alice.id, prompt) is None, prompt
    # suggestions still find it from the scene's words, titles included
    sp = screenplay(*[{"title": "Ohm's law", **image_panel(p, title="Ohm's law in practice")} for p in prompts])
    for _, ranked in library.suggestions(db_session, alice.id, sp):
        assert any(i.id == ohm.id and s >= library.SUGGESTION_THRESHOLD for i, s in ranked)
    board = item(db_session, alice, "upload-bb", title="Transistor on a breadboard")
    got = library.auto_match(db_session, alice.id, "a transistor on a breadboard")
    assert got is not None and got[0].id == board.id and got[1] >= library.AUTO_USE_THRESHOLD
    assert library.auto_match(db_session, alice.id, "a transistor on a breadboard", exclude={"upload-bb"}) is None
    assert library.auto_match(db_session, alice.id, "a transistor on a breadboard",
                              skip=lambda it: it.id == board.id) is None
    assert library.auto_match(db_session, alice.id, "the and of") is None
    matcher = library.LibraryMatcher.load(db_session, alice.id, kinds=["image"], texts=["breadboard"])
    assert library.auto_match(None, alice.id, "a transistor on a breadboard", matcher=matcher)[0].id == board.id


def test_list_items_of_several_sources(db_session):
    alice = add_user(db_session, "alice")
    item(db_session, alice, "upload-1", title="A")
    item(db_session, alice, "image-2", title="B", asset_kind="image", source="generated")
    item(db_session, alice, "figure-3", title="C", asset_kind="figure", source="figure")
    rows, total = library.list_items(db_session, alice.id, source=("upload", "figure"))
    assert total == 2 and {i.asset_key for i, _ in rows} == {"upload-1", "figure-3"}
    assert library.list_items(db_session, alice.id, source="generated")[1] == 1
    assert library.list_items(db_session, alice.id, source=())[1] == 3


def test_item_profiles_are_cached_by_their_words(db_session):
    alice = add_user(db_session, "alice")
    a = item(db_session, alice, "upload-a", title="Heart", keywords=["valve"])
    b = item(db_session, alice, "upload-b", title="Heart", keywords=["valve"])
    assert library.item_profile(a) is library.item_profile(b)
    renamed = library.update_item(db_session, a, title="Kidney")
    db_session.commit()
    assert library.item_profile(renamed).title == frozenset({"kidney"})
