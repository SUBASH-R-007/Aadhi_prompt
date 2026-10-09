"""Every /api route requires authentication except an explicit public allow-list."""

from __future__ import annotations

import re

from fastapi.routing import iter_route_contexts

PUBLIC = {
    ("POST", "/api/auth/login"),
    # Always 204: clears a stale cookie; only a valid session's token_version is bumped.
    ("POST", "/api/auth/logout"),
    ("GET", "/api/public/watch/{token}"),
    ("POST", "/api/analytics/events"),
    ("GET", "/api/render/timeline"),
}
SAMPLE_PARAMS = {"token": "x" * 32, "scene_id": "s1", "storage_key": "assets/x/y.png"}


def api_routes(app) -> list[tuple[str, str]]:
    out = []
    for ctx in iter_route_contexts(app.routes):
        path = ctx.path or ""
        if not path.startswith("/api/") or path.startswith(("/api/docs", "/api/openapi")):
            continue
        for method in sorted(ctx.methods or ()):
            if method != "HEAD":
                out.append((method, path))
    return sorted(set(out))


def concrete(path: str) -> str:
    return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: SAMPLE_PARAMS.get(m.group(1), "1"), path)


def test_route_table_covers_the_documented_api(api):
    routes = api_routes(api.app)
    paths = {p for _, p in routes}
    expected = {
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/auth/change-password",
        "/api/meta",
        "/api/projects",
        "/api/projects/import",
        "/api/projects/{project_id}",
        "/api/projects/{project_id}/regenerate",
        "/api/versions/{vid}",
        "/api/versions/{vid}/timeline",
        "/api/versions/{vid}/timeline/preview",
        "/api/versions/{vid}/screenplay",
        "/api/versions/{vid}/lint",
        "/api/versions/{vid}/build",
        "/api/versions/{vid}/scenes/{scene_id}/regenerate",
        "/api/versions/{vid}/plan",
        "/api/versions/{vid}/approve-plan",
        "/api/versions/{vid}/duplicate",
        "/api/versions/{vid}/translate",
        "/api/versions/{vid}/render",
        "/api/versions/{vid}/renders",
        "/api/renders/{rid}/download",
        "/api/versions/{vid}/companion.md",
        "/api/versions/{vid}/companion.html",
        "/api/versions/{vid}/export.json",
        "/api/versions/{vid}/chapters.txt",
        "/api/jobs",
        "/api/jobs/{job_id}",
        "/api/jobs/{job_id}/events",
        "/api/jobs/{job_id}/stream",
        "/api/jobs/{job_id}/cancel",
        "/api/jobs/{job_id}/retry",
        "/api/uploads",
        "/api/projects/{project_id}/shares",
        "/api/shares/{token}",
        "/api/public/watch/{token}",
        "/api/analytics/events",
        "/api/projects/{project_id}/analytics",
        "/api/usage/me",
        "/api/usage/projects/{project_id}",
        "/api/admin/usage",
        "/api/admin/users",
        "/api/admin/users/{user_id}",
        "/api/keys",
        "/api/keys/{provider}",
        "/api/keys/{provider}/test",
        "/api/admin/keys",
        "/api/admin/keys/{provider}",
        "/api/admin/keys/{provider}/test",
        "/api/render/timeline",
    }
    assert expected <= paths, sorted(expected - paths)
    assert len(routes) >= 50


def test_every_api_route_requires_auth_except_allow_list(api):
    client = api.client(browser=False)
    seen_public = set()
    for method, path in api_routes(api.app):
        r = client.request(method, concrete(path))
        if (method, path) in PUBLIC:
            seen_public.add((method, path))
            assert r.status_code != 403, (method, path, r.text)
            continue
        assert r.status_code == 401, (method, path, r.status_code, r.text)
        assert r.json()["code"] == "unauthenticated"
    assert seen_public == PUBLIC


def test_public_routes_behave_without_credentials(api):
    c = api.client(browser=False)
    assert c.get("/api/public/watch/" + "x" * 32).status_code == 404
    assert c.get("/api/render/timeline").status_code == 401
    anon = c.post(
        "/api/analytics/events", json={"viewer_id": "v" * 20, "version_id": 1, "events": [{"event": "pause", "t": 1}]}
    )
    assert anon.status_code == 401


def test_admin_routes_forbid_editors(api):
    _, c = api.editor("alice")
    for method, path in [
        ("GET", "/api/admin/users"),
        ("POST", "/api/admin/users"),
        ("PATCH", "/api/admin/users/1"),
        ("GET", "/api/admin/usage"),
        ("GET", "/api/admin/keys"),
        ("PUT", "/api/admin/keys/anthropic"),
        ("DELETE", "/api/admin/keys/anthropic"),
        ("POST", "/api/admin/keys/anthropic/test"),
    ]:
        r = c.request(method, path, json={} if method != "GET" else None)
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["code"] == "forbidden"


def test_unknown_routes_use_the_envelope(api):
    r = api.client().get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
