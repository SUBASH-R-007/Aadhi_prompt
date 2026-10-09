"""Share links, public watch, analytics ingest and the dashboard."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from aadhi.models import AnalyticsEvent, Project, ShareLink
from tests.api.factories import add_project

VIEWER = "viewer_ABCDEFGHIJ12"


def setup_project(api, **kw):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, **kw)
    return alice, c, project, version


def test_create_list_and_watch_share(api):
    _, c, project, version = setup_project(api)
    r = c.post(f"/api/projects/{project.id}/shares", json={"expires_in_days": 7})
    assert r.status_code == 201, r.text
    share = r.json()
    assert share["url"] == f"http://testserver/watch/{share['token']}"
    assert share["version_id"] is None and share["expires_at"] is not None
    assert len(share["token"]) >= 32
    public = api.client(browser=False)
    watch = public.get(f"/api/public/watch/{share['token']}")
    assert watch.status_code == 200
    body = watch.json()
    assert body["share_token"] == share["token"]
    assert body["project"] == {"title": "Ohm's Law", "subject_name": "", "session_title": ""}
    assert body["timeline"]["version_id"] == version.id
    assert "owner" not in body["project"]
    public.get(f"/api/public/watch/{share['token']}")
    listing = c.get(f"/api/projects/{project.id}/shares").json()["items"]
    assert listing[0]["token"] == share["token"] and listing[0]["view_count"] == 2


def test_share_pinned_to_a_version_of_the_project_only(api):
    alice, c, project, version = setup_project(api)
    with api.db() as db:
        _, other_version = add_project(db, alice, title="Other")
    assert c.post(f"/api/projects/{project.id}/shares", json={"version_id": other_version.id}).status_code == 422
    pinned = c.post(f"/api/projects/{project.id}/shares", json={"version_id": version.id}).json()
    assert pinned["version_id"] == version.id
    assert c.post(f"/api/projects/{project.id}/shares", json={"expires_in_days": 0}).status_code == 422


def test_revoked_expired_and_deleted_links_are_404(api):
    _, c, project, version = setup_project(api)
    public = api.client(browser=False)
    revoked = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    assert c.delete(f"/api/shares/{revoked}").status_code == 204
    assert public.get(f"/api/public/watch/{revoked}").status_code == 404
    expired = c.post(f"/api/projects/{project.id}/shares", json={"expires_in_days": 1}).json()["token"]
    with api.db() as db:
        row = db.execute(select(ShareLink).where(ShareLink.token == expired)).scalar_one()
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        db.commit()
    assert public.get(f"/api/public/watch/{expired}").status_code == 404
    live = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    with api.db() as db:
        db.get(Project, project.id).deleted_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    assert public.get(f"/api/public/watch/{live}").status_code == 404
    assert public.get("/api/public/watch/short").status_code == 404


def test_watch_404_when_nothing_is_built(api):
    _, c, project, version = setup_project(api, built=False, status="generating")
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    assert api.client(browser=False).get(f"/api/public/watch/{token}").status_code == 404


def ingest(client, body: dict):
    return client.post("/api/analytics/events", json=body)


def test_ingest_with_share_token_derives_project_and_version(api):
    alice, c, project, version = setup_project(api)
    with api.db() as db:
        _, other_version = add_project(db, alice, title="Other")
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    public = api.client(browser=False)
    r = ingest(
        public,
        {
            "share_token": token,
            "version_id": other_version.id,  # ignored: the token decides
            "viewer_id": VIEWER,
            "events": [
                {"event": "session_start", "t": 0},
                {"event": "scene_enter", "scene_id": "s1", "t": 0.5, "scene_t": 0},
                {"event": "quiz_answer", "scene_id": "q1", "t": 30, "data": {"choice": 1, "correct": False}},
                {"event": "seek", "t": 31, "data": {"from": 31, "to": 5}},
            ],
        },
    )
    assert r.status_code == 202, r.text
    with api.db() as db:
        rows = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id)).scalars().all()
        assert len(rows) == 4
        assert {(e.project_id, e.version_id) for e in rows} == {(project.id, version.id)}
        assert rows[0].share_link_id is not None and rows[0].user_id is None
        assert rows[2].data == {"choice": 1, "correct": False, "t": 30.0}
        assert rows[3].data == {"from": 31.0, "to": 5.0, "t": 31.0}


def test_ingest_validation(api):
    _, c, project, version = setup_project(api)
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    public = api.client(browser=False)
    base = {"share_token": token, "viewer_id": VIEWER}
    assert ingest(public, {**base, "viewer_id": "short"}).status_code == 422
    assert ingest(public, {**base, "viewer_id": "x" * 15 + "!"}).status_code == 422
    assert ingest(public, {**base, "events": []}).status_code == 422
    assert ingest(public, {**base, "events": [{"event": "pause", "t": 1}] * 101}).status_code == 422
    assert ingest(public, {**base, "events": [{"event": "explode", "t": 1}]}).status_code == 422
    assert ingest(public, {**base, "events": [{"event": "pause", "t": -1}]}).status_code == 422
    assert ingest(public, {**base, "events": [{"event": "pause", "t": 1, "data": {"x": 1}}]}).status_code == 422
    bad_quiz = {"event": "quiz_answer", "scene_id": "q1", "t": 1, "data": {"choice": "a", "correct": True}}
    assert ingest(public, {**base, "events": [bad_quiz]}).status_code == 422
    no_scene = {"event": "quiz_answer", "t": 1, "data": {"choice": 0, "correct": True}}
    assert ingest(public, {**base, "events": [no_scene]}).status_code == 422
    bad_seek = {"event": "seek", "t": 1, "data": {"from": "x", "to": 2}}
    assert ingest(public, {**base, "events": [bad_seek]}).status_code == 422
    assert ingest(public, {**base, "events": [{"event": "pause", "t": 1}], "extra": 1}).status_code == 422
    big = {**base, "events": [{"event": "pause", "t": 1, "scene_id": "s" * 60}] * 100}
    too_big = public.post(
        "/api/analytics/events",
        content=(str(big).replace("'", '"') + " " * 40000).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert too_big.status_code == 413
    bogus_token = {**base, "share_token": "y" * 32, "events": [{"event": "pause", "t": 1}]}
    assert ingest(public, bogus_token).status_code == 404


@pytest.fixture()
def client_ips(monkeypatch):
    """Requests choose their client IP with ``X-Test-IP`` (stands in for TRUSTED_PROXIES handling)."""
    from aadhi.api.routers import analytics

    monkeypatch.setattr(
        analytics, "client_ip", lambda request, settings: request.headers.get("x-test-ip", "203.0.113.1")
    )


def viewer(i: int) -> str:
    return f"viewer_{i:016d}"


def send(client, token: str, viewer_id: str, n: int, ip: str):
    body = {"share_token": token, "viewer_id": viewer_id, "events": [{"event": "pause", "t": 1}] * n}
    return client.post("/api/analytics/events", json=body, headers={"X-Test-IP": ip})


def share_token(api) -> str:
    _, c, project, version = setup_project(api)
    return c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]


def stored_events() -> int:
    from aadhi.db import get_sessionmaker

    with get_sessionmaker()() as db:
        return len(db.execute(select(AnalyticsEvent.id)).all())


def test_ingest_daily_cap_per_share(api, monkeypatch, client_ips):
    """The share-wide cap is the storage bound; reaching it takes several client IPs."""
    from aadhi.api.routers import analytics

    monkeypatch.setattr(analytics, "SHARE_DAILY_EVENT_CAP", 10)  # -> at most 4 events per IP
    token = share_token(api)
    public = api.client(browser=False)
    assert send(public, token, viewer(1), 4, "198.51.100.1").status_code == 202
    assert send(public, token, viewer(2), 4, "198.51.100.2").status_code == 202
    capped = send(public, token, viewer(3), 4, "198.51.100.3")
    assert capped.status_code == 429 and capped.json()["code"] == "rate_limited"
    assert "link" in capped.json()["detail"]
    assert 0 < int(capped.headers["Retry-After"]) <= 86400
    assert send(public, token, viewer(4), 2, "198.51.100.4").status_code == 202  # exactly at the cap
    assert stored_events() == 10


def test_rotating_viewer_ids_from_one_ip_cannot_lock_out_the_class(api, monkeypatch, client_ips):
    """Review probe: cap 500, API limit 2/min, one IP sends 100-event batches with fresh viewer ids."""
    from aadhi.api.routers import analytics

    monkeypatch.setattr(analytics, "SHARE_DAILY_EVENT_CAP", 500)
    api.settings.api_rate_limit_per_minute = 2
    token = share_token(api)
    public = api.client(browser=False)
    codes = [send(public, token, viewer(i), 100, "198.51.100.66") for i in range(6)]
    assert [r.status_code for r in codes] == [202, 202, 429, 429, 429, 429]
    assert all("network" in r.json()["detail"] for r in codes[2:])
    # A real student on another network is unaffected: the attacker got 40% of the link at most.
    student = send(public, token, "student_aaaaaaaaaaaa", 20, "203.0.113.7")
    assert student.status_code == 202, student.text
    assert stored_events() == 220


def test_rotating_viewer_ids_hits_the_per_ip_request_limit(api, client_ips):
    api.settings.api_rate_limit_per_minute = 2  # -> 8 requests per minute per IP and link
    token = share_token(api)
    public = api.client(browser=False)
    codes = [send(public, token, viewer(i), 1, "198.51.100.66").status_code for i in range(10)]
    assert codes == [202] * 8 + [429, 429]
    assert send(public, token, viewer(99), 1, "203.0.113.7").status_code == 202  # other IPs unaffected


def test_per_viewer_daily_cap_only_blocks_that_viewer(api, monkeypatch, client_ips):
    from aadhi.api.routers import analytics

    monkeypatch.setattr(analytics, "VIEWER_DAILY_EVENT_CAP", 150)
    token = share_token(api)
    public = api.client(browser=False)
    nat = "10.20.30.40"  # a whole class behind one campus NAT
    assert send(public, token, viewer(1), 100, nat).status_code == 202
    over = send(public, token, viewer(1), 100, nat)
    assert over.status_code == 429 and "viewer" in over.json()["detail"]
    assert send(public, token, viewer(1), 50, nat).status_code == 202  # what is left of its quota
    assert send(public, token, viewer(2), 100, nat).status_code == 202  # classmates are fine
    assert stored_events() == 250


def test_refused_batches_do_not_count_against_quotas(api, monkeypatch, client_ips):
    from aadhi.api.routers import analytics

    monkeypatch.setattr(analytics, "VIEWER_DAILY_EVENT_CAP", 100)
    token = share_token(api)
    public = api.client(browser=False)
    assert send(public, token, viewer(1), 60, "10.0.0.1").status_code == 202
    for _ in range(3):  # each would exceed the cap: refused whole, nothing charged
        assert send(public, token, viewer(1), 60, "10.0.0.1").status_code == 429
    assert send(public, token, viewer(1), 40, "10.0.0.1").status_code == 202
    assert stored_events() == 100


def test_session_ingest_has_per_viewer_quota_too(api, monkeypatch, client_ips):
    from aadhi.api.routers import analytics

    monkeypatch.setattr(analytics, "VIEWER_DAILY_EVENT_CAP", 3)
    _, c, project, version = setup_project(api)
    body = {"version_id": version.id, "viewer_id": VIEWER, "events": [{"event": "pause", "t": 3}] * 2}
    assert ingest(c, body).status_code == 202
    assert ingest(c, body).status_code == 429


def test_daily_caps_stay_below_the_share_cap(monkeypatch):
    from aadhi.api.routers import analytics

    assert analytics._daily_caps(True) == (20_000, 2_000)
    assert analytics._daily_caps(False) == (20_000, 2_000)
    monkeypatch.setattr(analytics, "SHARE_DAILY_EVENT_CAP", 1_000)
    assert analytics._daily_caps(True) == (400, 400)
    assert analytics._daily_caps(False) == (20_000, 2_000)  # session ingest has no share cap


def test_ingest_with_session_requires_access_to_the_version(api):
    alice, c, project, version = setup_project(api)
    ok = ingest(c, {"version_id": version.id, "viewer_id": VIEWER, "events": [{"event": "pause", "t": 3}]})
    assert ok.status_code == 202
    with api.db() as db:
        row = db.execute(select(AnalyticsEvent)).scalar_one()
        assert row.user_id == alice.id and row.share_link_id is None
    _, bob = api.editor("bob")
    assert (
        ingest(bob, {"version_id": version.id, "viewer_id": VIEWER, "events": [{"event": "pause", "t": 3}]}).status_code
        == 404
    )
    assert ingest(c, {"viewer_id": VIEWER, "events": [{"event": "pause", "t": 3}]}).status_code == 422


def _events(viewer: str, *items: tuple) -> list[dict]:
    out = []
    for item in items:
        event, scene, t, *rest = item
        ev = {"event": event, "t": t}
        if scene:
            ev["scene_id"] = scene
        if rest:
            ev["data"] = rest[0]
        out.append(ev)
    return out


def test_dashboard_numbers(api):
    _, c, project, version = setup_project(api)
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    public = api.client(browser=False)
    viewers = {
        # Watches everything, answers correctly (then changes the answer: ignored).
        "viewer_aaaaaaaaaaaa01": [
            ("session_start", None, 0),
            ("scene_enter", "s1", 0),
            ("scene_complete", "s1", 10),
            ("scene_enter", "s2", 10),
            ("scene_complete", "s2", 20),
            ("scene_enter", "q1", 20),
            ("quiz_answer", "q1", 25, {"choice": 0, "correct": True}),
            ("quiz_answer", "q1", 26, {"choice": 2, "correct": False}),
            ("scene_complete", "q1", 30),
            ("complete", None, 30),
        ],
        # Leaves during s2.
        "viewer_aaaaaaaaaaaa02": [
            ("session_start", None, 0),
            ("scene_enter", "s1", 0),
            ("scene_complete", "s1", 10),
            ("scene_enter", "s2", 10),
            ("seek", None, 12, {"from": 12, "to": 500}),
        ],
        # Skips ahead, answers wrong (client claims correct: recomputed from the screenplay).
        "viewer_aaaaaaaaaaaa03": [
            ("session_start", None, 0),
            ("scene_enter", "s1", 0),
            ("scene_complete", "s1", 10),
            ("scene_enter", "s2", 10),
            ("scene_complete", "s2", 20),
            ("scene_enter", "q1", 20),
            ("quiz_answer", "q1", 24, {"choice": 1, "correct": True}),
        ],
    }
    for viewer, items in viewers.items():
        assert (
            ingest(public, {"share_token": token, "viewer_id": viewer, "events": _events(viewer, *items)}).status_code
            == 202
        )
    r = c.get(f"/api/projects/{project.id}/analytics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"] == {
        "viewers": 3,
        "sessions": 3,
        "completion_rate": round(1 / 3, 4),
        "avg_watch_seconds": round((30 + 10 + 24) / 3, 2),
    }
    scenes = {s["scene_id"]: s for s in body["scenes"]}
    assert [s["scene_id"] for s in body["scenes"]] == ["s1", "s2", "q1"]
    assert scenes["s1"] == {
        "scene_id": "s1",
        "title": "Scene 1",
        "index": 0,
        "enters": 3,
        "completes": 3,
        "dropoff_rate": 0.0,
    }
    assert scenes["s2"]["enters"] == 3 and scenes["s2"]["completes"] == 2
    assert scenes["s2"]["dropoff_rate"] == round(1 / 3, 4)
    assert scenes["q1"]["dropoff_rate"] == 0.5
    quiz = body["quizzes"][0]
    assert quiz["scene_id"] == "q1" and quiz["correct_index"] == 0
    assert quiz["answers"] == 2 and quiz["accuracy"] == 0.5
    assert quiz["option_counts"] == [1, 1, 0]
    assert quiz["question"].startswith("What is V")
    flags = {(f["scene_id"], f["code"]) for f in body["flags"]}
    assert flags == {("s2", "high_dropoff"), ("q1", "high_dropoff")}
    assert all(f["reason"] for f in body["flags"])


def test_dashboard_version_must_belong_to_project(api):
    alice, c, project, version = setup_project(api)
    with api.db() as db:
        _, other_version = add_project(db, alice, title="Other")
    assert c.get(f"/api/projects/{project.id}/analytics", params={"version_id": other_version.id}).status_code == 422
    empty = c.get(f"/api/projects/{project.id}/analytics", params={"version_id": version.id}).json()
    assert empty["summary"] == {"viewers": 0, "sessions": 0, "completion_rate": 0.0, "avg_watch_seconds": 0.0}
    assert empty["quizzes"][0]["accuracy"] is None and empty["flags"] == []


def test_out_of_range_first_answer_does_not_use_up_the_viewers_answer(api):
    _, c, project, version = setup_project(api)
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    public = api.client(browser=False)
    vid = "viewer_bbbbbbbbbbbb01"
    answers = [
        ("quiz_answer", "q1", 20, {"choice": 9, "correct": True}),  # q1 has 3 options: invalid
        ("quiz_answer", "q1", 21, {"choice": 0, "correct": True}),  # the first valid answer counts
        ("quiz_answer", "q1", 22, {"choice": 2, "correct": False}),
    ]
    assert ingest(public, {"share_token": token, "viewer_id": vid, "events": _events(vid, *answers)}).status_code == 202
    quiz = c.get(f"/api/projects/{project.id}/analytics").json()["quizzes"][0]
    assert quiz["answers"] == 1 and quiz["accuracy"] == 1.0 and quiz["option_counts"] == [1, 0, 0]


def test_quiz_counts_aggregate_many_viewers(api):
    _, c, project, version = setup_project(api)
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    public = api.client(browser=False)
    firsts = [0, 0, 1, 2, 0, 1, 0]  # correct_index 0 -> 4 of 7 correct
    for i, choice in enumerate(firsts):
        vid = f"viewer_cccccccccc{i:02d}"
        items = [
            ("quiz_answer", "q1", 20, {"choice": choice, "correct": False}),
            ("quiz_answer", "q1", 25, {"choice": (choice + 1) % 3, "correct": False}),  # later answers ignored
        ]
        body = {"share_token": token, "viewer_id": vid, "events": _events(vid, *items)}
        assert ingest(public, body).status_code == 202
    quiz = c.get(f"/api/projects/{project.id}/analytics").json()["quizzes"][0]
    assert quiz["answers"] == 7 and quiz["option_counts"] == [4, 2, 1]
    assert quiz["accuracy"] == round(4 / 7, 4)


def test_quiz_numbers_without_a_readable_screenplay(api):
    """Unknown quiz (screenplay missing): any choice >= 0 counts and the client flag decides."""
    from aadhi.models import ProjectVersion

    _, c, project, version = setup_project(api)
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    public = api.client(browser=False)
    for vid, choice, correct in (("viewer_dddddddddd01", 1, True), ("viewer_dddddddddd02", 3, False)):
        items = [("quiz_answer", "qx", 5, {"choice": choice, "correct": correct})]
        body = {"share_token": token, "viewer_id": vid, "events": _events(vid, *items)}
        assert ingest(public, body).status_code == 202
    with api.db() as db:
        db.get(ProjectVersion, version.id).screenplay = None
        db.commit()
    body = c.get(f"/api/projects/{project.id}/analytics").json()
    quiz = next(q for q in body["quizzes"] if q["scene_id"] == "qx")
    assert quiz["correct_index"] is None and quiz["question"] == ""
    assert quiz["answers"] == 2 and quiz["accuracy"] == 0.5 and quiz["option_counts"] == [0, 1, 0, 1]


# every ``data`` shape the player sends (web/js/player/player.js ``_track``): one rejected event loses the batch
PLAYER_EVENTS = [
    {"event": "session_start", "t": 0},
    {"event": "scene_enter", "scene_id": "s1", "t": 0.5, "scene_t": 0},
    {"event": "pause", "t": 1.5, "data": {"reason": "blocked"}},  # the player's own pause after an autoplay block
    {"event": "resume", "t": 1.5},
    {"event": "pause", "t": 2},
    {"event": "seek", "t": 3, "data": {"from": 3, "to": 1}},
    {"event": "quiz_answer", "scene_id": "q1", "t": 4, "data": {"choice": 2, "correct": True}},
]


def test_every_player_event_shape_is_accepted_by_the_ingest_model():
    from aadhi.api.routers.analytics import IngestBody

    body = IngestBody.model_validate({"viewer_id": VIEWER, "version_id": 1, "events": PLAYER_EVENTS})
    assert body.events[2].data == {"reason": "blocked"} and body.events[4].data == {}


@pytest.mark.parametrize("data", [{"reason": "other"}, {"reason": "blocked", "x": 1}, {"reason": 1}])
def test_pause_accepts_only_the_blocked_tag(data):
    from pydantic import ValidationError

    from aadhi.api.routers.analytics import EventIn

    with pytest.raises(ValidationError):
        EventIn.model_validate({"event": "pause", "t": 1, "data": data})
    with pytest.raises(ValidationError):
        EventIn.model_validate({"event": "resume", "t": 1, "data": {"reason": "blocked"}})


def test_blocked_pause_batch_is_stored(api):
    _, c, project, version = setup_project(api)
    token = c.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    public = api.client(browser=False)
    r = ingest(public, {"share_token": token, "viewer_id": VIEWER, "events": PLAYER_EVENTS[:3]})
    assert r.status_code == 202, r.text
    with api.db() as db:
        rows = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id)).scalars().all()
        assert [e.event for e in rows] == ["session_start", "scene_enter", "pause"]
        assert rows[2].data == {"reason": "blocked", "t": 1.5}
    for bad in ({"reason": "other"}, {"reason": "blocked", "x": 1}):
        event = {"event": "pause", "t": 1, "data": bad}
        assert ingest(public, {"share_token": token, "viewer_id": VIEWER, "events": [event]}).status_code == 422
