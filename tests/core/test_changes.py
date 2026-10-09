"""aadhi.changes: the generated snapshot, comparing it with the current screenplay, and reverting a scene."""

from __future__ import annotations

import copy
import json

import pytest

from aadhi import changes
from aadhi.schemas.screenplay import Screenplay
from tests.api.factories import screenplay_dict


def sp(data: dict) -> Screenplay:
    return Screenplay.model_validate(data)


def lecture(n: int = 4) -> dict:
    return screenplay_dict(n_content=n, quiz=False)


def statuses(result: dict) -> list[tuple[str, str]]:
    return [(r["scene_id"], r["status"]) for r in result["scenes"]]


def test_store_and_load_the_snapshot(asset_store):
    changes.clear_cache()
    document = sp(lecture()).model_dump(mode="json")
    key = changes.store_snapshot(asset_store, document, created_by=None)
    assert key.startswith("snapshot-") and changes.store_snapshot(asset_store, document) == key  # content addressed
    asset = asset_store.get(key)
    assert asset.kind == "snapshot" and asset.storage_key.startswith("private/snapshot/")
    assert asset.mime == "application/json" and asset.meta == {"scenes": 4}
    loaded = changes.load_generated(asset_store, {changes.SNAPSHOT_META_KEY: key})
    assert loaded is not None and loaded.model_dump(mode="json") == document
    assert changes.load_generated(asset_store, {}) is None
    assert changes.load_generated(asset_store, {changes.SNAPSHOT_META_KEY: "extract-abc"}) is None  # not a snapshot
    assert changes.load_generated(asset_store, {changes.SNAPSHOT_META_KEY: "snapshot-missing"}) is None


def test_unreadable_snapshot_is_unavailable(asset_store):
    from aadhi.storage.assets import Produced

    changes.clear_cache()
    asset_store.put("snapshot-broken", "snapshot", Produced(data=b"{not json", mime="application/json"))
    assert changes.load_generated(asset_store, {changes.SNAPSHOT_META_KEY: "snapshot-broken"}) is None


def test_unchanged_lecture():
    gen = sp(lecture())
    result = changes.changes(gen, gen, {})
    assert result["available"] and statuses(result) == [(f"s{i}", "unchanged") for i in range(1, 5)]
    assert result["scenes"][2] == {"scene_id": "s3", "status": "unchanged", "generated_index": 2, "current_index": 2,
                                   "fields_changed": [], "history": 0}


def test_edited_added_moved_and_removed_scenes():
    gen_data = lecture(5)
    gen = sp(gen_data)
    cur = copy.deepcopy(gen_data)
    cur["scenes"][0]["title"] = "A better title"  # s1 edited
    cur["scenes"][0]["beats"][1]["narration"] = "Now think it over."
    s4 = cur["scenes"].pop(3)  # s4 moved before s2
    cur["scenes"].insert(1, s4)
    del cur["scenes"][4]  # s5 removed (after the move: s1 s4 s2 s3 s5)
    new = copy.deepcopy(cur["scenes"][2])
    new.update(id="s9", board=[{"id": "s9-i1", "kind": "bullet", "text": "New"}],
               beats=[{"id": "s9-b1", "narration": "A new scene.", "board_item_id": "s9-i1"}])
    cur["scenes"].append(new)  # s9 added
    meta = {"scene_history": {"s2": [{"at": "x", "instructions": "", "revision": 2, "scene": {}}]}}
    result = changes.changes(gen, sp(cur), meta)
    assert statuses(result) == [("s1", "edited"), ("s4", "moved"), ("s2", "unchanged"), ("s3", "unchanged"),
                                ("s5", "removed"), ("s9", "added")]
    rows = {r["scene_id"]: r for r in result["scenes"]}
    assert rows["s1"]["fields_changed"] == ["title", "beats"]
    assert rows["s5"]["generated_index"] == 4 and rows["s5"]["current_index"] is None
    assert rows["s9"]["generated_index"] is None and rows["s9"]["current_index"] == 4
    assert rows["s2"]["history"] == 1 and rows["s4"]["current_index"] == 1


def test_removed_scenes_are_listed_where_they_were():
    gen = sp(lecture(4))
    cur = lecture(4)
    del cur["scenes"][0]
    del cur["scenes"][1]  # s1 and s3 removed
    assert statuses(changes.changes(gen, sp(cur), {})) == [
        ("s1", "removed"), ("s2", "unchanged"), ("s3", "removed"), ("s4", "unchanged")]


def test_without_a_snapshot_only_history_is_known():
    cur = sp(lecture(2))
    meta = {"scene_history": {"s1": [{"scene": {}}, {"scene": {}}]}}
    result = changes.changes(None, cur, meta)
    assert result == {"available": False, "scenes": [
        {"scene_id": "s1", "status": "unchanged", "generated_index": None, "current_index": 0, "fields_changed": [],
         "history": 2},
        {"scene_id": "s2", "status": "unchanged", "generated_index": None, "current_index": 1, "fields_changed": [],
         "history": 0}]}


def test_fields_added_to_the_schema_later_are_not_edits():
    data = lecture(2)
    gen = sp(data)
    cur = sp(json.loads(json.dumps(data)))
    assert statuses(changes.changes(gen, cur, {})) == [("s1", "unchanged"), ("s2", "unchanged")]


def test_revert_an_edited_scene_and_restore_a_removed_one():
    gen_data = lecture(4)
    gen = sp(gen_data)
    cur = copy.deepcopy(gen_data)
    cur["scenes"][1]["title"] = "Teacher title"
    del cur["scenes"][2]  # s3 removed
    current = sp(cur)
    back = changes.reverted(current, gen, changes.target_scene(gen, {}, "s2", "generated"))
    assert back.scene_by_id("s2").title == "Scene 2" and [s.id for s in back.scenes] == ["s1", "s2", "s4"]
    restored = changes.reverted(current, gen, changes.target_scene(gen, {}, "s3", "generated"))
    assert [s.id for s in restored.scenes] == ["s1", "s2", "s3", "s4"]  # after its earlier generated neighbour
    first = changes.reverted(current, gen, changes.target_scene(gen, {}, "s3", "generated"), position=0)
    assert [s.id for s in first.scenes] == ["s3", "s1", "s2", "s4"]
    last = changes.reverted(current, gen, changes.target_scene(gen, {}, "s3", "generated"), position=99)
    assert [s.id for s in last.scenes] == ["s1", "s2", "s4", "s3"]


def test_revert_to_a_history_entry():
    gen = sp(lecture(2))
    older = copy.deepcopy(gen.scene_by_id("s1").model_dump(mode="json"))
    older["title"] = "Before regeneration"
    meta = {"scene_history": {"s1": [{"at": "2026-01-01T00:00:00+00:00", "instructions": "simpler", "revision": 2,
                                      "scene": older}]}}
    scene = changes.target_scene(gen, meta, "s1", "history", 0)
    assert scene["title"] == "Before regeneration"
    assert changes.reverted(gen, gen, scene).scene_by_id("s1").title == "Before regeneration"
    view = changes.history_view(meta, "s1")
    assert view[0]["instructions"] == "simpler" and view[0]["revision"] == 2 and view[0]["scene"]["title"]
    with pytest.raises(changes.RevertError) as missing:
        changes.target_scene(gen, meta, "s1", "history", 1)
    assert missing.value.code == "nothing_to_revert"
    other = dict(older, id="s2")
    with pytest.raises(changes.RevertError) as wrong:
        changes.target_scene(gen, {"scene_history": {"s1": [{"scene": other}]}}, "s1", "history")
    assert wrong.value.code == "revert_invalid"
    with pytest.raises(changes.RevertError) as broken:
        changes.target_scene(gen, {"scene_history": {"s1": [{"scene": {"id": "s1"}}]}}, "s1", "history")
    assert broken.value.code == "revert_invalid"


def test_nothing_to_revert_to():
    gen = sp(lecture(2))
    with pytest.raises(changes.RevertError) as no_snapshot:
        changes.target_scene(None, {}, "s1", "generated")
    assert no_snapshot.value.code == "nothing_to_revert"
    with pytest.raises(changes.RevertError):
        changes.target_scene(gen, {}, "s9", "generated")  # added in the editor: never generated


def test_restored_scene_drops_links_to_parts_that_are_gone():
    data = lecture(3)
    data["concept_map"] = [{"id": "ohm", "title": "Ohm"}, {"id": "power", "title": "Power"}]
    data["chapters"] = [{"id": "ch1", "title": "One", "scene_ids": ["s1", "s2", "s3"]}]
    for s in data["scenes"]:
        s["concept_id"] = "power" if s["id"] == "s2" else "ohm"
        s["chapter_id"] = "ch1"
    gen = sp(data)
    cur = copy.deepcopy(data)
    del cur["scenes"][1]
    cur["chapters"][0]["scene_ids"] = ["s1", "s3"]
    cur["concept_map"] = [{"id": "ohm", "title": "Ohm"}]  # the teacher removed the concept of s2
    back = changes.reverted(sp(cur), gen, changes.target_scene(gen, {}, "s2", "generated"))
    restored = back.scene_by_id("s2")
    assert restored.concept_id is None and restored.chapter_id == "ch1"
    assert back.chapters[0].scene_ids == ["s1", "s2", "s3"]  # listed in its chapter again, in order


def chaptered() -> dict:
    data = lecture(4)
    data["chapters"] = [{"id": "ch1", "title": "One", "scene_ids": ["s1", "s2"]},
                        {"id": "ch2", "title": "Two", "scene_ids": ["s3", "s4"]}]
    for s in data["scenes"]:
        s["chapter_id"] = "ch1" if s["id"] in ("s1", "s2") else "ch2"
    return data


def move_to_ch2(data: dict, scene_id: str) -> dict:
    """What the editor's "Change chapter" does: the scene's chapter_id and both chapters' lists."""
    cur = copy.deepcopy(data)
    next(s for s in cur["scenes"] if s["id"] == scene_id)["chapter_id"] = "ch2"
    cur["chapters"][0]["scene_ids"].remove(scene_id)
    cur["chapters"][1]["scene_ids"].insert(0, scene_id)
    return cur


def test_reverting_a_scene_moved_to_another_chapter_lists_it_in_its_chapter_again():
    data = chaptered()
    gen = sp(data)
    current = sp(move_to_ch2(data, "s2"))
    assert statuses(changes.changes(gen, current, {}))[1] == ("s2", "edited")
    back = changes.reverted(current, gen, changes.target_scene(gen, {}, "s2", "generated"))
    assert back.model_dump(mode="json") == gen.model_dump(mode="json")
    assert [(c.id, c.scene_ids) for c in back.chapters] == [("ch1", ["s1", "s2"]), ("ch2", ["s3", "s4"])]
    assert all(status == "unchanged" for _, status in statuses(changes.changes(gen, back, {})))


def test_reverting_to_a_history_entry_after_a_chapter_change_lists_it_once():
    data = chaptered()
    gen = sp(data)
    older = copy.deepcopy(gen.scene_by_id("s2").model_dump(mode="json"))
    older["title"] = "Before regeneration"
    meta = {"scene_history": {"s2": [{"at": "2026-01-01T00:00:00+00:00", "instructions": "", "revision": 2,
                                      "scene": older}]}}
    back = changes.reverted(sp(move_to_ch2(data, "s2")), gen, changes.target_scene(gen, meta, "s2", "history", 0))
    assert back.scene_by_id("s2").chapter_id == "ch1" and back.scene_by_id("s2").title == "Before regeneration"
    assert [(c.id, c.scene_ids) for c in back.chapters] == [("ch1", ["s1", "s2"]), ("ch2", ["s3", "s4"])]
    same_chapter = copy.deepcopy(data)  # an edit inside the same chapter leaves the chapters' lists as they are
    same_chapter["scenes"][1]["title"] = "Teacher title"
    kept = changes.reverted(sp(same_chapter), gen, changes.target_scene(gen, {}, "s2", "generated"))
    assert [c.scene_ids for c in kept.chapters] == [["s1", "s2"], ["s3", "s4"]]


def test_a_full_lecture_cannot_take_a_restored_scene():
    data = screenplay_dict(n_content=200, quiz=False)
    gen = sp(data)
    cur = copy.deepcopy(data)
    del cur["scenes"][5]
    cur["scenes"].append({"id": "extra", "type": "content", "title": "x", "board": [],
                          "beats": [{"id": "extra-b1", "narration": "Extra."}]})
    with pytest.raises(changes.RevertError) as exc:
        changes.reverted(sp(cur), gen, changes.target_scene(gen, {}, "s6", "generated"))
    assert exc.value.code == "revert_invalid"


def test_scene_detail():
    gen_data = lecture(2)
    gen = sp(gen_data)
    cur = copy.deepcopy(gen_data)
    cur["scenes"][0]["title"] = "Edited"
    detail = changes.scene_detail(gen, sp(cur), {}, "s1")
    assert detail["status"] == "edited" and detail["fields_changed"] == ["title"]
    assert detail["generated"]["title"] == "Scene 1" and detail["current"]["title"] == "Edited"
    assert detail["history"] == [] and detail["available"]
    assert changes.scene_detail(gen, sp(cur), {}, "nope") is None


def test_longest_stable_order_is_linear_and_correct():
    assert changes._stable([0, 1, 2]) == {0, 1, 2}
    assert changes._stable([2, 0, 1]) == {1, 2}
    assert changes._stable([]) == set()
    big = list(range(5000))
    big[10], big[4000] = big[4000], big[10]
    assert len(changes._stable(big)) == 4998
