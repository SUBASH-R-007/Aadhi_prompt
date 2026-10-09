"""Render-mode screenshots: contract helpers, network policy, and real Chromium runs against the stub page."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from PIL import Image

from aadhi.compose.screenshot import (
    MAX_STATES_PER_SCENE,
    NetworkPolicy,
    ScreenshotError,
    capture_render_frames,
    media_origins_for,
    normalize_states,
    origin_of,
    page_error,
    render_url,
)
from aadhi.compose.timeline import build_timeline, resolve_urls
from aadhi.config import Settings
from aadhi.jobs.base import JobCancelled

from .factories import make_manifest, make_screenplay
from .stub_server import FIGURE_RGB, serve_other, serve_stub

# --- pure helpers ---------------------------------------------------------------------------------


def test_origin_and_url() -> None:
    assert origin_of("http://127.0.0.1:8123/render-frame#x") == "http://127.0.0.1:8123"
    assert origin_of("https://App.Example.com/x") == "https://app.example.com:443"
    assert origin_of("http://a.com") == origin_of("http://a.com:80/")
    assert origin_of("https://evil.com") != origin_of("http://evil.com")
    assert origin_of("ws://127.0.0.1:9/x") == "ws://127.0.0.1:9"
    assert origin_of("http://[::1]:8000/x") == "http://[::1]:8000"
    assert origin_of("http://bad:port/") == "invalid://"
    assert render_url("http://x:1/", "tok") == "http://x:1/render-frame#token=tok"


def test_normalize_states() -> None:
    raw = [{"t": 2.0, "key": "b"}, {"t": 1.0, "key": "a"}, {"t": 1.0005, "key": "a2"}, {"t": 3.0, "key": "a2"},
           {"t": float("nan"), "key": "x"}, "junk", {"t": 99, "key": "late"}, {"t": -1, "key": "neg"},
           {"t": True, "key": "bool"}]
    out = normalize_states(raw, duration=10.0)
    assert out[0] == (0.0, "neg")
    assert (1.0, "a2") in out and (2.0, "b") in out
    assert (3.0, "a2") in out  # same key again later (not consecutive) is kept
    assert out[-1][0] < 10.0 and out[-1][1] == "late"
    assert "bool" not in [k for _, k in out]
    assert normalize_states([], 5.0) == [(0.0, "__scene_start")]
    with pytest.raises(ScreenshotError) as ei:
        normalize_states({"t": 0}, 5.0)
    assert not ei.value.retryable


def test_too_many_states_fail_loudly() -> None:
    many = [{"t": i * 0.01, "key": f"k{i}"} for i in range(MAX_STATES_PER_SCENE + 10)]
    with pytest.raises(ScreenshotError) as ei:
        normalize_states(many, duration=1000.0)
    assert ei.value.code == "too_many_states" and not ei.value.retryable
    ok = normalize_states(many[:MAX_STATES_PER_SCENE], duration=1000.0)
    assert len(ok) == MAX_STATES_PER_SCENE  # nothing silently dropped below the limit


def test_network_policy() -> None:
    policy = NetworkPolicy.build("http://127.0.0.1:8123", ["https://cdn.example.com/assets/x.png?sig=1",
                                                             "https://bucket.s3.amazonaws.com", "", "/media/x"])
    assert policy.media_origins == frozenset({"https://cdn.example.com:443", "https://bucket.s3.amazonaws.com:443"})
    assert policy.allows("http://127.0.0.1:8123/api/render/timeline", "fetch")
    assert policy.allows("http://127.0.0.1:8123/x", "xhr", "POST")
    assert policy.allows("https://cdn.example.com/a.png", "image")
    assert policy.allows("https://bucket.s3.amazonaws.com/k.mp4?X-Amz-Signature=1", "media")
    assert policy.allows("https://cdn.example.com/f.woff2", "font")
    assert not policy.allows("https://cdn.example.com/a.png", "fetch")  # media origins: media types only
    assert not policy.allows("https://cdn.example.com/a.png", "image", "POST")
    assert not policy.allows("http://127.0.0.1:9999/x", "image")  # another port is another origin
    assert not policy.allows("https://evil.example/x.png", "image")
    assert policy.allows("data:image/png;base64,AAAA", "image") and policy.allows("blob:http://x/1", "image")
    bypass = policy.proxy_bypass().split(",")
    assert bypass[0] == "<-loopback>" and "http://127.0.0.1:8123" in bypass
    assert "https://cdn.example.com:443" in bypass and len(bypass) == 4


class _FakeStorage:
    def __init__(self, public: str, signed: str | Exception) -> None:
        self.public, self.signed = public, signed

    def public_url(self, key: str) -> str:
        return self.public + key

    def signed_url(self, key: str, ttl: int) -> str:
        if isinstance(self.signed, Exception):
            raise self.signed
        return self.signed + key + "?X-Amz-Signature=abc"


def test_media_origins_for_storage_backends() -> None:
    local = Settings(_env_file=None)
    assert media_origins_for(_FakeStorage("/media/", "/media/"), local) == []  # same-origin paths
    s3 = Settings(_env_file=None, storage_backend="s3", s3_bucket="b", s3_region="ap-south-1")
    presigned = _FakeStorage("/media/", "https://b.s3.ap-south-1.amazonaws.com/aadhi/")
    assert media_origins_for(presigned, s3) == ["https://b.s3.ap-south-1.amazonaws.com:443"]
    cdn = Settings(_env_file=None, storage_backend="s3", s3_bucket="b", cdn_base_url="https://cdn.rec.edu/x")
    both = media_origins_for(_FakeStorage("https://cdn.rec.edu/x/aadhi/", RuntimeError("no credentials")), cdn)
    assert both == ["https://cdn.rec.edu:443"]
    minio = Settings(_env_file=None, storage_backend="s3", s3_bucket="b", s3_endpoint_url="http://127.0.0.1:9000")
    assert media_origins_for(_FakeStorage("/media/", RuntimeError("x")), minio) == ["http://127.0.0.1:9000"]


def test_page_error_mapping() -> None:
    settings = Settings(_env_file=None)
    fatal = page_error({"code": "player_api", "message": "Player is missing render-mode methods"}, settings)
    assert fatal.code == "render_page_player_api" and not fatal.retryable and "player_api" in str(fatal)
    assert not page_error({"code": "fonts_missing", "message": "x"}, settings).retryable
    assert not page_error({"code": "timeline_http", "message": "failed: HTTP 401 (unauthenticated)"},
                          settings).retryable
    assert page_error({"code": "timeline_http", "message": "failed: HTTP 503"}, settings).retryable
    assert page_error({"code": "timeout", "message": "MathJax loading timed out"}, settings).retryable
    odd = page_error({"code": "<script>", "message": 5}, settings)
    assert odd.code == "render_page_script" and odd.retryable
    assert page_error("nonsense", settings).code == "render_page_boot_failed"


# --- real Chromium against the stub page (slow) ---------------------------------------------------


def _timeline(app_env):
    sp = make_screenplay()
    return build_timeline(sp, make_manifest(sp), settings=app_env)


@pytest.mark.slow
def test_capture_against_stub_page(app_env, asset_store, tmp_path: Path) -> None:
    tl = _timeline(app_env)
    served = resolve_urls(tl, asset_store).model_dump(mode="json")
    with serve_stub("tok-123", served) as (base, state):
        settings = app_env.model_copy(update={"base_url": base})
        progress: list[tuple[int, int]] = []
        result = asyncio.run(capture_render_frames(
            settings=settings, token="tok-123", timeline=tl, out_dir=tmp_path / "frames",
            intro_times=[c.start + 1 for c in tl.intro.cards], pages=2,
            on_scene_done=lambda d, n: progress.append((d, n))))
    # token was sent as a header, never in a request URL
    assert ("/api/render/timeline", "Bearer tok-123") in [(p, a) for p, a in state.requests]
    assert all("tok-123" not in p for p, _ in state.requests)
    assert result.blocked_requests and all("blocked.example.invalid" in b for b in result.blocked_requests)
    assert result.blocked_media == [] and result.websocket_attempts == []
    assert sorted(result.scenes) == list(range(len(tl.scenes)))
    assert progress[-1] == (len(tl.scenes), len(tl.scenes))
    board = result.scenes[[s.scene_id for s in tl.scenes].index("s-board")]
    assert board[0].t == 0.0 and len(board) >= 5
    assert len({s.png_path for s in board}) == len({s.key for s in board})
    with Image.open(board[0].png_path) as im:
        assert im.size == (1920, 1080) and im.mode == "RGBA"
        assert im.getpixel((5, 1075))[3] == 0  # transparent background
        assert im.getpixel((100, 60))[3] > 0  # title box painted
    panel_shots = [s for s in board if s.panel_media_rect]
    assert panel_shots and panel_shots[0].t >= tl.scenes[2].side_panel.show_at - 1e-6
    assert panel_shots[0].panel_media_rect == {"x": 1480.0, "y": 260.0, "width": 360.0, "height": 360.0}
    sim = result.scenes[[s.scene_id for s in tl.scenes].index("s-sim")]
    assert sim[0].media_rect == {"x": 576.0, "y": 160.0, "width": 1024.0, "height": 576.0}
    assert sim[0].media_fit == "contain"
    quiz = result.scenes[[s.scene_id for s in tl.scenes].index("s-quiz")]
    assert len(quiz) >= 5 + 2  # countdown seconds + question + reveal
    assert len(result.intro) == 2 and all(p.png_path.exists() for p in result.intro)


@pytest.mark.slow
@pytest.mark.parametrize("allowed", [True, False])
def test_page_images_from_media_origins(app_env, tmp_path: Path, allowed: bool) -> None:
    """Board figures drawn by the page itself load from the CDN/S3 origin only when it is allowed."""
    tl = _timeline(app_env)
    with serve_other() as (other, other_state), serve_stub(
            "t", tl.model_dump(mode="json"), config={"figure_url": f"{other}/fig.png?X-Amz-Signature=x"}) as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        result = asyncio.run(capture_render_frames(
            settings=settings, token="t", timeline=tl, out_dir=tmp_path, scene_indices=[0], pages=1,
            media_origins=[other] if allowed else []))
    with Image.open(result.scenes[0][0].png_path) as im:
        pixel = im.convert("RGBA").getpixel((200, 800))
    if allowed:
        assert pixel[:3] == FIGURE_RGB and pixel[3] == 255
        assert any(r.startswith("GET /fig.png") for r in other_state.requests)
        assert result.blocked_media == []
    else:
        assert pixel[3] == 0  # not loaded
        assert other_state.requests == []
        assert result.blocked_media == [origin_of(other)]


@pytest.mark.slow
def test_websockets_never_reach_other_origins(app_env, tmp_path: Path) -> None:
    tl = _timeline(app_env)
    with serve_other() as (other, other_state), serve_stub(
            "t", tl.model_dump(mode="json"), config={"ws_url": other.replace("http://", "ws://") + "/ws-exfil"}
    ) as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        result = asyncio.run(capture_render_frames(settings=settings, token="t", timeline=tl, out_dir=tmp_path,
                                                   scene_indices=[0], pages=1))
    assert other_state.requests == []
    assert result.websocket_attempts == [origin_of(other.replace("http://", "ws://"))]


@pytest.mark.slow
def test_off_origin_redirect_is_blocked_and_fails(app_env, tmp_path: Path) -> None:
    tl = _timeline(app_env)
    with serve_other() as (other, other_state), serve_stub(
            "t", tl.model_dump(mode="json"), config={"redirect_to": f"{other}/exfil?data=secret"}) as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        with pytest.raises(ScreenshotError) as ei:
            asyncio.run(capture_render_frames(settings=settings, token="t", timeline=tl, out_dir=tmp_path,
                                              scene_indices=[0], pages=1))
    assert other_state.requests == []  # the redirect hop never reached the other origin (dead proxy)
    assert ei.value.code == "render_network_policy" and not ei.value.retryable
    assert origin_of(other) in str(ei.value)


@pytest.mark.slow
def test_capture_fails_cleanly_when_page_never_ready(app_env, tmp_path: Path) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, None, settings=app_env)
    with serve_stub("right-token", tl.model_dump(mode="json")) as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        with pytest.raises(ScreenshotError, match="never became ready") as ei:
            asyncio.run(capture_render_frames(settings=settings, token="wrong-token", timeline=tl,
                                              out_dir=tmp_path, ready_timeout_s=3))
    assert ei.value.retryable


@pytest.mark.slow
def test_boot_error_is_reported_immediately(app_env, tmp_path: Path) -> None:
    tl = _timeline(app_env)
    with serve_stub("t", tl.model_dump(mode="json"), mode="boot-error") as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        t0 = time.monotonic()
        with pytest.raises(ScreenshotError) as ei:
            asyncio.run(capture_render_frames(settings=settings, token="t", timeline=tl, out_dir=tmp_path,
                                              ready_timeout_s=60))
    assert time.monotonic() - t0 < 30  # not the 60 s ready timeout
    assert ei.value.code == "render_page_player_api" and not ei.value.retryable
    assert "renderState" in str(ei.value)


@pytest.mark.slow
def test_hanging_page_call_times_out(app_env, tmp_path: Path) -> None:
    tl = _timeline(app_env)
    with serve_stub("t", tl.model_dump(mode="json"), mode="hang-show") as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        t0 = time.monotonic()
        with pytest.raises(ScreenshotError) as ei:
            asyncio.run(capture_render_frames(settings=settings, token="t", timeline=tl, out_dir=tmp_path,
                                              pages=2, call_timeout_s=2))
    assert time.monotonic() - t0 < 25
    assert ei.value.code == "render_page_timeout" and ei.value.retryable
    assert "aadhiRender.show" in str(ei.value)


@pytest.mark.slow
def test_cancel_interrupts_a_hanging_call(app_env, tmp_path: Path) -> None:
    tl = _timeline(app_env)
    t0 = time.monotonic()
    ready_at: list[float] = []

    def check() -> None:
        if not ready_at:
            ready_at.append(time.monotonic())
        if time.monotonic() - ready_at[0] > 1.5:
            raise JobCancelled()

    with serve_stub("t", tl.model_dump(mode="json"), mode="hang-show") as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        with pytest.raises(JobCancelled):
            asyncio.run(capture_render_frames(settings=settings, token="t", timeline=tl, out_dir=tmp_path,
                                              call_timeout_s=120, check_cancelled=check))
    assert time.monotonic() - t0 < 40  # far below the 120 s call timeout


@pytest.mark.slow
def test_capture_honours_cancellation(app_env, tmp_path: Path) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, None, settings=app_env)

    def cancel() -> None:
        raise JobCancelled()

    with serve_stub("t", tl.model_dump(mode="json")) as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        with pytest.raises(JobCancelled):
            asyncio.run(capture_render_frames(settings=settings, token="t", timeline=tl, out_dir=tmp_path,
                                              check_cancelled=cancel))


# --- internal render origin / notices / missing browser ------------------------------------------


def test_render_origin_prefers_the_internal_address() -> None:
    from aadhi.compose.screenshot import render_origin

    public = Settings(_env_file=None, base_url="https://lectures.example.com/")
    assert render_origin(public) == "https://lectures.example.com"
    internal = public.model_copy(update={"render_base_url": " http://api:8000/ "})
    assert render_origin(internal) == "http://api:8000"
    policy = NetworkPolicy.build(render_origin(internal), ["https://cdn.example.com"])
    assert policy.app_origin == "http://api:8000"
    assert policy.allows("http://api:8000/web/js/render/render.js", "script")
    assert policy.allows("http://api:8000/media/assets/x.png", "image")  # relative /media resolves internally
    assert not policy.allows("https://lectures.example.com/web/js/render/render.js", "script")  # public origin not
    assert policy.allows("https://cdn.example.com/a.png", "image")


def test_missing_browser_and_notice_parsing() -> None:
    from aadhi.compose.screenshot import _notices, missing_browser

    assert missing_browser("BrowserType.launch: Executable doesn't exist at /ms-playwright/chromium/chrome")
    assert missing_browser("Looks like Playwright was just installed. Please run: playwright install")
    assert not missing_browser("net::ERR_CONNECTION_REFUSED")
    assert _notices(["  The figure is\n not available yet. ", "", 3, "The figure is not available yet."]) == (
        "The figure is not available yet.",)
    assert _notices("nope") == () and _notices(None) == ()


def test_missing_chromium_is_a_fatal_render_unavailable_error(app_env, tmp_path: Path, monkeypatch) -> None:
    import playwright.async_api as pw_api

    class FakeChromium:
        async def launch(self, **kwargs):
            raise pw_api.Error("BrowserType.launch: Executable doesn't exist at /x/chrome\n Please run playwright install")

    class FakePlaywright:
        chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(pw_api, "async_playwright", lambda: FakePlaywright())
    tl = _timeline(app_env)
    with pytest.raises(ScreenshotError) as ei:
        asyncio.run(capture_render_frames(settings=app_env, token="t", timeline=tl, out_dir=tmp_path / "f"))
    assert ei.value.code == "render_unavailable" and not ei.value.retryable
    assert "playwright install chromium" in str(ei.value)


@pytest.mark.slow
def test_capture_uses_the_internal_render_origin(app_env, asset_store, tmp_path: Path) -> None:
    """BASE_URL is the public origin (unreachable here); RENDER_BASE_URL is where the page is served."""
    tl = _timeline(app_env)
    served = resolve_urls(tl, asset_store).model_dump(mode="json")
    with serve_stub("tok-int", served) as (base, state):
        settings = app_env.model_copy(update={"base_url": "https://public.invalid", "render_base_url": base})
        result = asyncio.run(capture_render_frames(settings=settings, token="tok-int", timeline=tl,
                                                   out_dir=tmp_path / "frames", scene_indices=[0], pages=1))
    assert 0 in result.scenes and ("/api/render/timeline", "Bearer tok-int") in state.requests
