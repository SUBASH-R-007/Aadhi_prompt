"""Pure helpers: time, filenames, ETags, options policy, SSE parsing helpers."""

from __future__ import annotations

import datetime as dt

import pytest

from aadhi.api.timelines import etag_matches, signed_urls, timeline_etag
from aadhi.api.util import as_utc, content_disposition, download_name, iso, page_params
from aadhi.models import ProjectVersion


def test_iso_and_as_utc():
    naive = dt.datetime(2026, 10, 1, 8, 30)
    assert iso(naive) == "2026-10-01T08:30:00Z"
    ist = dt.datetime(2026, 10, 1, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    assert iso(ist) == "2026-10-01T08:30:00Z"
    assert as_utc(None) is None and iso(None) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Ohm's Law", "Ohm's Law.mp4"),
        ('Bad "quotes" / slashes \\ and\nnewlines', "Bad quotes slashes and newlines.mp4"),
        ("", "lecture.mp4"),
        ("...", "lecture.mp4"),
        ("x" * 300, "x" * 120 + ".mp4"),
    ],
)
def test_download_name(title, expected):
    assert download_name(title, "mp4") == expected


def test_content_disposition_ascii_and_unicode():
    assert content_disposition("Ohm's Law.mp4") == 'attachment; filename="Ohm\'s Law.mp4"'
    header = content_disposition("ஓம் விதி.mp4")
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''%E0%AE%93" in header
    assert '"' not in header.split("filename*=")[1]


def test_page_params_bounds():
    assert page_params(50, 0) == (50, 0)
    for bad in ((0, 0), (201, 0), (10, -1)):
        with pytest.raises(Exception) as exc:
            page_params(*bad)
        assert getattr(exc.value, "status_code", None) == 422


def test_etag_matching_rules():
    tag = '"r1-b1-abcdefabcdef"'
    assert etag_matches(tag, tag)
    assert etag_matches(f'"other", {tag}', tag)
    assert etag_matches(f"W/{tag}", tag)
    assert etag_matches("*", tag)
    assert not etag_matches(None, tag)
    assert not etag_matches('"r1-b1-000000000000"', tag)


T0 = dt.datetime(2026, 10, 1, 8, 30, 0, 123456, tzinfo=dt.timezone.utc)


def _version(**kw) -> ProjectVersion:
    values = {"id": 7, "revision": 3, "built_revision": 2, "has_timeline": True, "updated_at": T0, **kw}
    return ProjectVersion(**values)


def test_timeline_etag_tracks_cheap_columns(app_env):
    a = timeline_etag(_version(), app_env)
    assert a.startswith('"r3-b2-') and a.endswith('"') and len(a.strip('"').split("-")[-1]) == 12
    assert timeline_etag(_version(), app_env) == a  # deterministic
    # SQLite hands back naive datetimes: the same instant gives the same tag.
    assert timeline_etag(_version(updated_at=T0.replace(tzinfo=None)), app_env) == a
    # Every write to the version row bumps updated_at (onupdate) -> a new tag, even at equal revisions.
    assert timeline_etag(_version(updated_at=T0 + dt.timedelta(microseconds=1)), app_env) != a
    assert timeline_etag(_version(id=8), app_env) != a  # duplicated versions never share a tag
    assert timeline_etag(_version(has_timeline=False), app_env) != a
    assert timeline_etag(_version(revision=4), app_env) != a
    assert timeline_etag(_version(built_revision=3), app_env) != a
    cdn = app_env.model_copy(update={"cdn_base_url": "https://cdn.example.com"})
    assert timeline_etag(_version(), cdn) != a  # resolved URLs change with the URL settings


def test_timeline_etag_never_touches_the_timeline_document(app_env):
    """The tag is computed per poll (304s included): it must not load or hash the large document."""
    from types import SimpleNamespace

    class NoDocument(SimpleNamespace):
        @property
        def timeline(self):  # pragma: no cover - failing is the point
            raise AssertionError("timeline_etag loaded the timeline document")

    v = NoDocument(id=7, revision=3, built_revision=2, has_timeline=True, updated_at=T0)
    assert timeline_etag(v, app_env) == timeline_etag(_version(), app_env)  # type: ignore[arg-type]


def test_timeline_etag_presign_window(app_env):
    v = _version()
    s3 = app_env.model_copy(update={"storage_backend": "s3", "cdn_base_url": "", "s3_presign_ttl_seconds": 600})
    assert signed_urls(s3) and not signed_urls(app_env)
    early = timeline_etag(v, s3, now=0)
    assert early.endswith('-t0"')
    assert timeline_etag(v, s3, now=299) == early
    assert timeline_etag(v, s3, now=301) != early
    cdn = s3.model_copy(update={"cdn_base_url": "https://cdn.example.com"})
    assert not signed_urls(cdn)
    assert "-t" not in timeline_etag(v, cdn, now=301).split("-", 2)[2]


def test_options_policy(app_env):
    from aadhi.api.generation import parse_options, sanitize_options
    from aadhi.models import User

    settings = app_env.model_copy(update={"llm_model_allowlist": ["m-ok"]})
    opts = parse_options('{"llm_model_plan": "m-ok", "llm_model_script": "m-bad"}')
    admin = sanitize_options(opts, User(role="admin"), settings)
    assert (admin.llm_model_plan, admin.llm_model_script) == ("m-ok", None)
    editor = sanitize_options(opts, User(role="editor"), settings)
    assert (editor.llm_model_plan, editor.llm_model_script) == (None, None)
    assert parse_options(None, defaults={"target_minutes": 30}).target_minutes == 30
    assert parse_options("", defaults={"target_minutes": 30}).target_minutes == 30
    with pytest.raises(Exception) as exc:
        parse_options("[1, 2]")
    assert exc.value.status_code == 422


def test_timeline_etag_changes_with_the_serve_time_clip_substitutions(app_env, monkeypatch):
    """Cached copies naming a retired mascot clip must be refreshed once the substitution changes."""
    from aadhi.api import timelines

    before = timeline_etag(_version(), app_env)
    monkeypatch.setattr(timelines, "RETIRED_MASCOT_CLIPS", {**timelines.RETIRED_MASCOT_CLIPS, "a.mp4": "b.mp4"})
    assert timeline_etag(_version(), app_env) != before


def test_timeline_etag_changes_with_the_retired_branding_files(app_env, monkeypatch):
    """Cached copies naming the old intro logo are refreshed when RETIRED_BRANDING_FILES changes."""
    from aadhi.api import timelines

    before = timeline_etag(_version(), app_env)
    monkeypatch.setattr(timelines, "RETIRED_BRANDING_FILES", {**timelines.RETIRED_BRANDING_FILES, "a.png": "b.png"})
    assert timeline_etag(_version(), app_env) != before


def test_timeline_etag_changes_with_mascot_cues(app_env):
    """MASCOT_CUES is applied when a timeline is served: flipping it must refresh cached copies."""
    on = timeline_etag(_version(), app_env.model_copy(update={"mascot_cues": True}))
    off = timeline_etag(_version(), app_env.model_copy(update={"mascot_cues": False}))
    assert on != off
    assert on.split("-")[:2] == off.split("-")[:2]  # same revision part, only the hash differs
