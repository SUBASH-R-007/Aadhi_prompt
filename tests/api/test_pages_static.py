"""Pages (CSP per page), static mounts (/web, /branding), health probes, render-timeline endpoint."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from aadhi.models import Project, Render
from tests.api.factories import add_project


@pytest.fixture()
def site(app_env, tmp_path):
    """An app whose web_dir/branding_dir are temporary fixture trees."""
    from aadhi.main import create_app

    web = tmp_path / "web"
    (web / "sandbox").mkdir(parents=True)
    (web / "js").mkdir()
    (web / "vendor" / "lib").mkdir(parents=True)
    (web / "vendor" / "lib@1.2.3").mkdir()
    (web / "js" / ".hidden").mkdir()
    (web / "node_modules" / "pkg").mkdir(parents=True)
    (web / "tests").mkdir()
    (web / "index.html").write_text("<!doctype html><title>Studio</title>", encoding="utf-8")
    (web / "watch.html").write_text("<!doctype html><title>Watch</title>", encoding="utf-8")
    (web / "render.html").write_text("<!doctype html><title>Render</title>", encoding="utf-8")
    (web / "sandbox" / "p5.html").write_text("<!doctype html><title>p5</title>", encoding="utf-8")
    (web / "js" / "app.js").write_text("export const x = 1;\n", encoding="utf-8")
    (web / "vendor" / "lib" / "lib.js").write_text("/* vendored */\n", encoding="utf-8")
    (web / "vendor" / "lib@1.2.3" / "lib.js").write_text("/* vendored, versioned */\n", encoding="utf-8")
    (web / "vendor" / "lib" / "lib.3f2a9b1c.js").write_text("/* content-hashed */\n", encoding="utf-8")
    (web / "tests" / "secret.test.js").write_text("// test\n", encoding="utf-8")
    (web / "js" / ".env.js").write_text("// dot-file\n", encoding="utf-8")
    (web / "js" / ".hidden" / "x.js").write_text("// dot-dir\n", encoding="utf-8")
    (web / "node_modules" / "pkg" / "index.js").write_text("// dependency\n", encoding="utf-8")
    branding = tmp_path / "branding"
    branding.mkdir()
    (branding / "bgm.mp3").write_bytes(b"ID3" + b"\x00" * 32)
    (branding / "notes.txt").write_text("internal", encoding="utf-8")
    (branding / ".cache").mkdir()
    (branding / ".cache" / "x.png").write_bytes(b"PNG")
    settings = app_env.model_copy(update={"web_dir": web, "branding_dir": branding})
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        yield client


def test_pages_have_their_csp(site):
    from aadhi.security.headers import build_csp

    for path in ("/", "/watch/" + "t" * 32, "/preview/12", "/render-frame"):
        r = site.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-cache"
        assert "script-src 'self';" in r.headers["content-security-policy"]
    sandbox = site.get("/sandbox/p5")
    assert sandbox.status_code == 200
    assert sandbox.headers["content-security-policy"] == build_csp(site.app.state.settings, kind="sandbox")
    assert "'unsafe-eval'" in sandbox.headers["content-security-policy"]
    assert "connect-src 'none'" in sandbox.headers["content-security-policy"]
    assert site.get("/watch/abc").headers["referrer-policy"] == "no-referrer"
    assert site.get("/preview/not-a-number").status_code == 404


def test_web_static_cache_policy_and_types(site):
    app_js = site.get("/web/js/app.js")
    assert app_js.status_code == 200
    assert app_js.headers["content-type"].startswith("text/javascript")
    assert app_js.headers["cache-control"] == "no-cache"
    assert app_js.headers["x-content-type-options"] == "nosniff"
    etag = app_js.headers["etag"]
    assert site.get("/web/js/app.js", headers={"If-None-Match": etag}).status_code == 304
    assert site.get("/web/tests/secret.test.js").status_code == 404
    assert site.get("/web/../aadhi/main.py").status_code == 404
    assert site.get("/web/js/missing.js").status_code == 404


IMMUTABLE = "public, max-age=31536000, immutable"


def test_vendor_files_are_immutable_only_at_content_addressed_urls(site):
    # Unversioned path: an upgrade via `npm run vendor` must reach returning browsers -> revalidate.
    plain = site.get("/web/vendor/lib/lib.js")
    assert plain.status_code == 200 and plain.headers["cache-control"] == "no-cache"
    assert site.get("/web/vendor/lib/lib.js", headers={"If-None-Match": plain.headers["etag"]}).status_code == 304
    versioned_dir = site.get("/web/vendor/lib@1.2.3/lib.js")
    assert versioned_dir.status_code == 200 and versioned_dir.headers["cache-control"] == IMMUTABLE
    hashed = site.get("/web/vendor/lib/lib.3f2a9b1c.js")
    assert hashed.status_code == 200 and hashed.headers["cache-control"] == IMMUTABLE
    pinned_query = site.get("/web/vendor/lib/lib.js?v=3f2a9b1c")
    assert pinned_query.status_code == 200 and pinned_query.headers["cache-control"] == IMMUTABLE
    assert site.get("/web/vendor/lib/lib.js?v=").headers["cache-control"] == "no-cache"
    # App code never becomes immutable, even with a version-looking query.
    assert site.get("/web/js/app.js?v=1").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path",
    [
        "/web/tests/secret.test.js",
        "/web/Tests/secret.test.js",  # NTFS/APFS are case-insensitive
        "/web/TESTS/secret.test.js",
        "/web/node_modules/pkg/index.js",
        "/web/NODE_MODULES/pkg/index.js",
        "/web/Node_Modules/pkg/index.js",
        "/web/js/.env.js",  # nested dot-file
        "/web/js/.hidden/x.js",  # file in a nested dot-directory
        "/web/js/.HIDDEN/x.js",
    ],
)
def test_web_deny_list_is_case_insensitive_and_covers_nested_dot_files(site, path):
    assert site.get(path).status_code == 404


@pytest.mark.parametrize(
    ("rel", "query", "versioned"),
    [
        ("p5/p5.min.js", "", False),
        ("chartjs/chart.umd.min.js", "", False),
        ("fonts/inter-latin-300-normal.woff2", "", False),
        ("mathjax/input/tex/extensions/all-packages.js", "", False),
        ("p5@1.9.0/p5.min.js", "", True),
        ("mathjax-3.2.2/tex-chtml.js", "", True),
        ("v4.4.1/chart.js", "", True),
        ("chart/chart.3f2a9b1c.js", "", True),
        ("p5/p5.min.js", "v=abc123", True),
        ("p5/p5.min.js", "x=1", False),
    ],
)
def test_is_versioned_path(rel, query, versioned):
    from aadhi.api.static import is_versioned_path

    assert is_versioned_path(rel, query) is versioned


def test_real_vendor_tree_has_no_false_immutable_paths():
    """Every file `npm run vendor` produces today sits at an unversioned path (so: revalidated)."""
    from aadhi.api.static import WebStaticFiles
    from aadhi.config import ROOT_DIR

    vendor = ROOT_DIR / "web" / "vendor"
    if not vendor.is_dir():
        pytest.skip("web/vendor not generated")
    static = WebStaticFiles(directory=ROOT_DIR / "web", check_dir=False)
    wrongly_immutable = [
        p.relative_to(vendor).as_posix()
        for p in vendor.rglob("*")
        if p.is_file() and static.cache_control("vendor/" + p.relative_to(vendor).as_posix()) == IMMUTABLE
    ]
    assert wrongly_immutable == []


def test_branding_serves_only_media(site):
    bgm = site.get("/branding/bgm.mp3")
    assert bgm.status_code == 200
    assert bgm.headers["content-type"] == "audio/mpeg"
    assert "max-age" in bgm.headers["cache-control"]
    assert site.get("/branding/notes.txt").status_code == 404
    assert site.get("/branding/missing.mp4").status_code == 404
    assert site.get("/branding/BGM.MP3").status_code in (200, 404)  # filesystem-dependent, never an error
    assert site.get("/branding/.cache/x.png").status_code == 404


def test_missing_page_is_404(app_env, tmp_path):
    from aadhi.main import create_app

    settings = app_env.model_copy(update={"web_dir": tmp_path / "nothing"})
    with TestClient(create_app(settings)) as c:
        assert c.get("/").status_code == 404
        assert c.get("/web/js/app.js").status_code == 404


def test_health_probes(api):
    c = api.client(browser=False)
    assert c.get("/healthz").json() == {"ok": True}
    ready = c.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"ok": True, "db": True, "storage": True}


def test_readyz_reports_failures(api, monkeypatch):
    from aadhi.api.routers import health

    def boom(db):
        raise RuntimeError("db down")

    monkeypatch.setattr(health, "ping", boom)
    r = api.client(browser=False).get("/readyz")
    assert r.status_code == 503
    assert r.json()["db"] is False and r.json()["code"] == "unavailable"


def _render_token(claims: dict, scope: str = "render") -> str:
    from aadhi.auth.tokens import create_scoped_token

    return create_scoped_token(scope, claims, 600)


def test_render_timeline_with_scoped_token(api):
    alice = api.user("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
        render = Render(version_id=version.id, status="running")
        db.add(render)
        db.commit()
        db.refresh(render)
    c = api.client(browser=False)
    token = _render_token({"vid": version.id, "rid": render.id})
    r = c.get("/api/render/timeline", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["version_id"] == version.id
    assert (
        c.get(f"/api/render/timeline?vid={version.id}", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    )
    mismatch = c.get(f"/api/render/timeline?vid={version.id + 1}", headers={"Authorization": f"Bearer {token}"})
    assert mismatch.status_code == 403
    wrong_scope = _render_token({"vid": version.id}, scope="export")
    assert c.get("/api/render/timeline", headers={"Authorization": f"Bearer {wrong_scope}"}).status_code == 401
    assert c.get("/api/render/timeline", headers={"Authorization": "Bearer garbage"}).status_code == 401
    # A session cookie is no substitute for the scoped token.
    session = api.login("alice")
    assert session.get("/api/render/timeline").status_code == 401
    with api.db() as db:
        db.get(Project, project.id).deleted_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    assert c.get("/api/render/timeline", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_render_timeline_rid_must_belong_to_vid(api):
    alice = api.user("alice")
    with api.db() as db:
        _, v1 = add_project(db, alice)
        _, v2 = add_project(db, alice, title="Other")
        render = Render(version_id=v2.id, status="running")
        db.add(render)
        db.commit()
        db.refresh(render)
    token = _render_token({"vid": v1.id, "rid": render.id})
    r = api.client(browser=False).get("/api/render/timeline", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_render_timeline_rebuilds_for_include_intro_claim(api):
    from aadhi.models import ProjectVersion
    from aadhi.schemas.manifest import AssetManifest

    alice = api.user("alice")
    with api.db() as db:
        _, version = add_project(db, alice)
        db.get(ProjectVersion, version.id).set_manifest(AssetManifest())
        db.commit()
    c = api.client(browser=False)
    stored = c.get(
        "/api/render/timeline", headers={"Authorization": f"Bearer {_render_token({'vid': version.id})}"}
    ).json()
    assert not stored.get("intro")
    without = c.get(
        "/api/render/timeline",
        headers={"Authorization": f"Bearer {_render_token({'vid': version.id, 'include_intro': False})}"},
    )
    assert without.status_code == 200 and not without.json().get("intro")
    assert without.json()["scenes"][0]["start"] == 0
    rebuilt = c.get(
        "/api/render/timeline",
        headers={"Authorization": f"Bearer {_render_token({'vid': version.id, 'include_intro': True})}"},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    body = rebuilt.json()
    assert body["intro"] and body["intro"]["duration"] > 0
    assert body["scenes"][0]["start"] == body["intro"]["duration"]
    assert body["version_id"] == version.id
