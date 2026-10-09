"""Image / video / GIF providers (fakes produce real media; network adapters are mocked)."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

import httpx
import pytest
import respx
from google.genai import types as gtypes
from PIL import Image

from aadhi.providers.base import ContentBlocked, ImageInput, ProviderError, ProviderNotConfigured, RateLimited, Usage
from aadhi.providers.gif.fake import FakeGif
from aadhi.providers.gif.giphy import GIPHY_SEARCH_URL, GiphyGif, pick_result
from aadhi.providers.image.fake import FakeImage
from aadhi.providers.image.gemini import GeminiImage
from aadhi.providers.image.pollinations import POLLINATIONS_BASE_URL, PollinationsImage
from aadhi.providers.image.verify import inspect_image_sync
from aadhi.providers.video.veo import VeoVideo

from .conftest import GEMINI_KEYS, no_sleep
from .fakes import gemini_blob_response, gemini_error, gemini_factory, gemini_prompt_blocked, gemini_text_response

BROKEN_PNG = b"\x89PNG\r\n\x1a\n truncated"
MP4_STUB = b"\x00\x00\x00\x18ftypmp42fake"


def png_bytes(width: int = 64, height: int = 36, color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


# --- verification ---------------------------------------------------------------------------------


def test_inspect_image() -> None:
    info = inspect_image_sync(png_bytes(80, 40))
    assert (info.mime, info.width, info.height) == ("image/png", 80, 40)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")
    assert inspect_image_sync(buf.getvalue()).mime == "image/jpeg"
    for bad in (b"", b"<html>nope</html>", png_bytes()[:30]):
        with pytest.raises(ProviderError):
            inspect_image_sync(bad)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="BMP")
    with pytest.raises(ProviderError, match="unsupported image format"):
        inspect_image_sync(buf.getvalue())


# --- fake image ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_image_is_deterministic_png() -> None:
    usages: list[Usage] = []
    a = await FakeImage().generate("Ohm's law circuit", on_usage=usages.append)
    b = await FakeImage().generate("Ohm's law circuit")
    c = await FakeImage().generate("Photosynthesis")
    assert a.data == b.data and a.data != c.data
    assert (a.mime, a.width, a.height) == ("image/png", 1280, 720)
    info = inspect_image_sync(a.data)
    assert (info.width, info.height) == (1280, 720)
    with Image.open(io.BytesIO(a.data)) as img:
        colors = img.convert("RGB").getcolors(maxcolors=1_000_000)
    assert colors is not None and len(colors) > 50  # a real gradient, not a flat fill
    portrait = await FakeImage().generate("x", aspect="9:16")
    assert (portrait.width, portrait.height) == (720, 1280)
    assert usages[0] == Usage(provider="fake", model="fake-image", operation="image", units=1)


# --- pollinations ------------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_pollinations_request_and_verification(app_env) -> None:
    route = respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(
        return_value=httpx.Response(200, content=png_bytes(1280, 720), headers={"content-type": "image/png"})
    )
    provider = PollinationsImage(app_env, sleep=no_sleep)
    usages: list[Usage] = []
    res = await provider.generate("A resistor & battery / circuit?", on_usage=usages.append)
    request = route.calls.last.request
    raw_path = request.url.raw_path.decode()
    assert raw_path.startswith("/prompt/A%20resistor%20%26%20battery%20%2F%20circuit%3F")
    assert "no%20text" in raw_path  # house style suffix appended
    params = request.url.params
    assert params["width"] == "1280" and params["height"] == "720" and params["nologo"] == "true"
    seed = params["seed"]
    await provider.generate("A resistor & battery / circuit?")
    assert route.calls.last.request.url.params["seed"] == seed  # deterministic per prompt
    assert (res.mime, res.width, res.height) == ("image/png", 1280, 720)
    assert usages[0].provider == "pollinations" and usages[0].units == 1


@pytest.mark.asyncio
@respx.mock
async def test_pollinations_retries_and_errors(app_env) -> None:
    good = httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})
    route = respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(
        side_effect=[httpx.Response(502), httpx.ReadTimeout("slow"),
                     httpx.Response(200, text="<html>busy</html>", headers={"content-type": "text/html"}), good]
    )
    res = await PollinationsImage(app_env, sleep=no_sleep, max_attempts=4).generate("x")
    assert res.width == 64 and route.call_count == 4

    respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimited):
        await PollinationsImage(app_env, sleep=no_sleep, max_attempts=2).generate("x")
    respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(return_value=httpx.Response(400, text="bad"))
    with pytest.raises(ProviderError) as info:
        await PollinationsImage(app_env, sleep=no_sleep).generate("x")
    assert info.value.status == 400 and "image.pollinations.ai" in str(info.value)
    respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(
        return_value=httpx.Response(200, content=b"garbage", headers={"content-type": "image/png"}))
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="not a valid image"):
        await PollinationsImage(app_env, sleep=no_sleep).generate("x", on_usage=usages.append)
    assert len(usages) == 1


@pytest.mark.asyncio
@respx.mock
async def test_pollinations_error_detail_is_not_cut_before_redaction(make_settings) -> None:
    key = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE12345"
    s = make_settings(gemini_api_key=key)
    body = "x" * 190 + f" key {key} rejected"  # the key straddles the old 200-character pre-cut
    respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(return_value=httpx.Response(400, text=body))
    with pytest.raises(ProviderError) as info:
        await PollinationsImage(s, sleep=no_sleep).generate("x")
    assert key[:8] not in str(info.value) and "[REDACTED]" in str(info.value)


# --- gemini image ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_image_generate_content(make_settings) -> None:
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], image_model="gemini-2.5-flash-image")
    factory, clients = gemini_factory({GEMINI_KEYS[0]: [gemini_blob_response(png_bytes(128, 72), "image/png")]})
    usages: list[Usage] = []
    res = await GeminiImage(s, client_factory=factory, sleep=no_sleep).generate("a diode", on_usage=usages.append)
    assert (res.mime, res.width, res.height) == ("image/png", 128, 72)
    call = clients[GEMINI_KEYS[0]].models.calls[0]
    assert call["config"].response_modalities == ["TEXT", "IMAGE"]
    assert call["config"].image_config.aspect_ratio == "16:9"
    assert call["contents"].startswith("a diode")
    assert usages[0].operation == "image" and usages[0].units == 1 and usages[0].provider == "gemini"

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_text_response("I cannot draw that")]})
    usages.clear()
    with pytest.raises(ProviderError, match="no image"):
        await GeminiImage(s, client_factory=factory, sleep=no_sleep).generate("x", on_usage=usages.append)
    assert len(usages) == 1 and usages[0].units == 0 and usages[0].input_tokens == 100  # tokens are still billed


@pytest.mark.asyncio
async def test_gemini_image_reports_usage_before_checks(make_settings) -> None:
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], image_model="gemini-2.5-flash-image")
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_blob_response(BROKEN_PNG, "image/png")]})
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="not a valid image"):
        await GeminiImage(s, client_factory=factory, sleep=no_sleep).generate("x", on_usage=usages.append)
    assert [(u.operation, u.units) for u in usages] == [("image", 1)]  # billed even though unusable

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_prompt_blocked()]})
    usages.clear()
    with pytest.raises(ContentBlocked):
        await GeminiImage(s, client_factory=factory, sleep=no_sleep).generate("x", on_usage=usages.append)
    assert len(usages) == 1

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_text_response("", finish="RECITATION")]})
    with pytest.raises(ContentBlocked, match="recitation"):  # media outputs cannot be re-asked
        await GeminiImage(s, client_factory=factory, sleep=no_sleep).generate("x")


@pytest.mark.asyncio
async def test_imagen_path_and_filtering(make_settings) -> None:
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], image_model="imagen-4.0-generate-001")
    ok = gtypes.GenerateImagesResponse(generated_images=[gtypes.GeneratedImage(
        image=gtypes.Image(image_bytes=png_bytes(160, 90), mime_type="image/png"))])
    filtered = gtypes.GenerateImagesResponse(generated_images=[gtypes.GeneratedImage(rai_filtered_reason="unsafe")])
    broken = gtypes.GenerateImagesResponse(generated_images=[gtypes.GeneratedImage(
        image=gtypes.Image(image_bytes=BROKEN_PNG, mime_type="image/png"))])
    factory, clients = gemini_factory({GEMINI_KEYS[0]: [ok, filtered, broken]})
    provider = GeminiImage(s, client_factory=factory, sleep=no_sleep)
    usages: list[Usage] = []
    res = await provider.generate("x", aspect="21:9", on_usage=usages.append)
    assert res.width == 160 and len(usages) == 1
    assert clients[GEMINI_KEYS[0]].models.calls[0]["config"].aspect_ratio == "16:9"  # unsupported -> 16:9
    with pytest.raises(ContentBlocked):
        await provider.generate("y", on_usage=usages.append)
    assert len(usages) == 1  # filtered images are not billed
    with pytest.raises(ProviderError, match="not a valid image"):
        await provider.generate("z", on_usage=usages.append)
    assert len(usages) == 2  # the returned (billed) image is reported before verification fails


# --- video -------------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_fake_video_probes_correctly(app_env) -> None:
    from aadhi.providers.media import probe_media
    from aadhi.providers.video.fake import FakeVideo

    usages: list[Usage] = []
    res = await FakeVideo(app_env).generate("a waterfall", on_usage=usages.append)
    assert res.mime == "video/mp4" and (res.width, res.height) == (1280, 720)
    assert res.duration == pytest.approx(4.0, abs=0.1)
    assert res.data[4:8] == b"ftyp"
    info = await probe_media(res.data, settings=app_env)
    assert info.has_video and not info.has_audio and info.duration == pytest.approx(4.0, abs=0.1)
    assert usages[0].operation == "video" and usages[0].seconds == pytest.approx(res.duration)
    portrait = await FakeVideo(app_env).generate("x", aspect="9:16")
    assert (portrait.width, portrait.height) == (720, 1280)


def _op(done: bool, video: gtypes.Video | None = None, **extra) -> SimpleNamespace:
    response = None
    if video is not None or extra:
        response = SimpleNamespace(generated_videos=[SimpleNamespace(video=video)] if video else [], **extra)
    return SimpleNamespace(name="operations/1", done=done, error=None, response=response, result=None)


@pytest.mark.asyncio
async def test_veo_polls_with_creating_key_and_downloads(gemini_settings, monkeypatch) -> None:
    from aadhi.providers.media import MediaInfo
    from aadhi.providers.video import veo as veo_mod

    async def fake_probe(data, settings=None):
        return MediaInfo(duration=8.0, width=1280, height=720, has_video=True)

    monkeypatch.setattr(veo_mod, "probe_media", fake_probe)
    limited = gemini_error(429, "busy", "RESOURCE_EXHAUSTED")
    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    factory, clients = gemini_factory(
        {GEMINI_KEYS[0]: [limited], GEMINI_KEYS[1]: [_op(False)]},
        ops={GEMINI_KEYS[1]: [gemini_error(503, "flaky", "UNAVAILABLE"), _op(False), _op(True, video)]},
    )
    provider = VeoVideo(gemini_settings, client_factory=factory, sleep=no_sleep, poll_interval_s=0)
    client2 = factory(GEMINI_KEYS[1])
    client2.aio.files.download_bytes = b"\x00\x00\x00\x18ftypmp42fake"
    usages: list[Usage] = []
    ref = ImageInput(data=png_bytes(), mime="image/png")
    res = await provider.generate("ocean waves at dawn", reference_image=ref, timeout_s=60, on_usage=usages.append)
    assert res.data.startswith(b"\x00\x00\x00\x18ftyp") and res.duration == 8.0 and res.width == 1280
    assert clients[GEMINI_KEYS[1]].aio.operations.calls == 3  # retried the flaky poll on the same key
    call = clients[GEMINI_KEYS[1]].models.calls[0]
    assert call["source"].prompt == "ocean waves at dawn" and call["source"].image.image_bytes == ref.data
    assert call["config"].aspect_ratio == "16:9" and call["config"].number_of_videos == 1
    assert usages == [Usage(provider="veo", model="veo-2.0-generate-001", operation="video", seconds=8.0, units=1,
                            meta={"status": "generated", "estimated": True})]


@pytest.mark.asyncio
async def test_veo_reference_images_on_31_and_failures(make_settings, monkeypatch) -> None:
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], veo_model="veo-3.1-generate-preview")
    factory, clients = gemini_factory({GEMINI_KEYS[0]: [_op(True, None, rai_media_filtered_count=1,
                                                             rai_media_filtered_reasons=["people"])]})
    provider = VeoVideo(s, client_factory=factory, sleep=no_sleep, poll_interval_s=0)
    with pytest.raises(ContentBlocked, match="people"):
        await provider.generate("x", aspect="9:16", reference_image=ImageInput(data=png_bytes()))
    call = clients[GEMINI_KEYS[0]].models.calls[0]
    assert call["source"].image is None and call["config"].reference_images[0].reference_type.value == "ASSET"
    assert call["config"].aspect_ratio == "9:16"

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [_op(False)]}, ops={GEMINI_KEYS[0]: [_op(False)] * 50})
    clock = iter(range(0, 10_000, 100))
    from aadhi.providers.video import veo as veo_mod

    monkeypatch.setattr(veo_mod, "time", SimpleNamespace(monotonic=lambda: next(clock)))  # module-local clock
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="timed out"):
        await VeoVideo(s, client_factory=factory, sleep=no_sleep, poll_interval_s=0).generate(
            "x", timeout_s=30, on_usage=usages.append)
    # the accepted operation may still be billed: an estimate is reported before giving up
    assert usages == [Usage(provider="veo", model="veo-3.1-generate-preview", operation="video", seconds=8.0,
                            units=1, meta={"status": "abandoned", "estimated": True})]

    failed = SimpleNamespace(name="op", done=True, error={"message": "internal error"}, response=None, result=None)
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [failed]})
    usages.clear()
    with pytest.raises(ProviderError, match="internal error"):
        await VeoVideo(s, client_factory=factory, sleep=no_sleep).generate("x", on_usage=usages.append)
    assert usages == []  # failed operations are not billed


@pytest.mark.asyncio
async def test_veo_reports_usage_before_download_and_probe(gemini_settings, monkeypatch) -> None:
    from aadhi.providers.video import veo as veo_mod

    async def broken_probe(data, settings=None):
        raise ProviderError("ffprobe is not installed")

    monkeypatch.setattr(veo_mod, "probe_media", broken_probe)
    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [_op(True, video)]})
    factory(GEMINI_KEYS[0]).aio.files.download_bytes = MP4_STUB
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="ffprobe"):
        await VeoVideo(gemini_settings, client_factory=factory, sleep=no_sleep).generate("x", on_usage=usages.append)
    assert [(u.seconds, u.meta["status"]) for u in usages] == [(8.0, "generated")]

    # download failure after the video exists: still billed, still reported
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [_op(True, video)]})
    usages.clear()
    with pytest.raises(ProviderError, match="no data"):
        await VeoVideo(gemini_settings, client_factory=factory, sleep=no_sleep).generate("x", on_usage=usages.append)
    assert len(usages) == 1 and usages[0].meta["status"] == "generated"


@pytest.mark.asyncio
async def test_veo_polling_failure_reports_abandoned_usage(gemini_settings) -> None:
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [_op(False)]},
                                ops={GEMINI_KEYS[0]: [gemini_error(400, "bad operation name", "INVALID_ARGUMENT")]})
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="video polling failed"):
        await VeoVideo(gemini_settings, client_factory=factory, sleep=no_sleep, poll_interval_s=0).generate(
            "x", on_usage=usages.append)
    assert [u.meta["status"] for u in usages] == ["abandoned"]


@pytest.mark.asyncio
async def test_veo_cancellation_reports_abandoned_usage(gemini_settings) -> None:
    import asyncio

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [_op(False)]}, ops={GEMINI_KEYS[0]: [asyncio.CancelledError()]})
    usages: list[Usage] = []
    with pytest.raises(asyncio.CancelledError):
        await VeoVideo(gemini_settings, client_factory=factory, sleep=no_sleep, poll_interval_s=0).generate(
            "x", on_usage=usages.append)
    assert [u.meta["status"] for u in usages] == ["abandoned"]

    def failing_sink(_usage: Usage) -> None:
        raise RuntimeError("database is down")

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [_op(False)]}, ops={GEMINI_KEYS[0]: [asyncio.CancelledError()]})
    with pytest.raises(asyncio.CancelledError):  # the cancellation is never swallowed
        await VeoVideo(gemini_settings, client_factory=factory, sleep=no_sleep, poll_interval_s=0).generate(
            "x", on_usage=failing_sink)


def test_veo_usage_uses_requested_duration(gemini_settings) -> None:
    provider = VeoVideo(gemini_settings, client_factory=gemini_factory({})[0], sleep=no_sleep)
    usage = provider._usage("veo-x", gtypes.GenerateVideosConfig(duration_seconds=5), status="generated")
    assert usage.seconds == 5.0 and usage.units == 1
    assert provider._usage("veo-x", gtypes.GenerateVideosConfig(), status="generated").seconds == 8.0


# --- gifs ------------------------------------------------------------------------------------------

GIPHY_KEY = "giphy-secret-key-123456"


def giphy_item(url: str = "https://media2.giphy.com/media/abc/giphy.gif", w: str = "480", h: str = "270") -> dict:
    return {"id": "abc", "title": "Ohm GIF", "url": "https://giphy.com/gifs/abc",
            "images": {"downsized": {"url": url, "width": w, "height": h}}}


@pytest.mark.asyncio
@respx.mock
async def test_giphy_search(make_settings) -> None:
    s = make_settings(giphy_api_key=GIPHY_KEY)
    route = respx.get(GIPHY_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": [
        giphy_item(url="http://evil.example/x.gif"), giphy_item()]}))
    usages: list[Usage] = []
    res = await GiphyGif(s, sleep=no_sleep).search("electric current & flow", rating="nc-17", on_usage=usages.append)
    params = route.calls.last.request.url.params
    assert params["api_key"] == GIPHY_KEY and params["q"] == "electric current & flow" and params["rating"] == "g"
    assert res.url == "https://media2.giphy.com/media/abc/giphy.gif" and (res.width, res.height) == (480, 270)
    assert res.attribution == "Powered by GIPHY" and res.link_url == "https://giphy.com/gifs/abc"
    assert usages[0].provider == "giphy"


@pytest.mark.asyncio
@respx.mock
async def test_giphy_errors(make_settings) -> None:
    s = make_settings(giphy_api_key=GIPHY_KEY)
    respx.get(GIPHY_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ProviderError) as info:
        await GiphyGif(s, sleep=no_sleep).search("nothing")
    assert info.value.status == 404
    respx.get(GIPHY_SEARCH_URL).mock(return_value=httpx.Response(403, text=f"bad api_key={GIPHY_KEY}"))
    with pytest.raises(ProviderError) as info:
        await GiphyGif(s, sleep=no_sleep).search("x")
    assert GIPHY_KEY not in str(info.value) and info.value.status == 403
    with pytest.raises(ProviderError, match="empty query"):
        await GiphyGif(s, sleep=no_sleep).search("   ")
    # a long server-requested pause is not waited out for an optional GIF
    route = respx.get(GIPHY_SEARCH_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "60"}))
    before = route.call_count  # respx reuses the route object across .mock() calls
    with pytest.raises(RateLimited):
        await GiphyGif(s, sleep=no_sleep).search("x")
    assert route.call_count - before == 1
    with pytest.raises(ProviderNotConfigured):
        GiphyGif(make_settings())


def test_pick_result_filters_hosts_and_sizes() -> None:
    assert pick_result([giphy_item(url="https://giphy.com.evil.net/x.gif")]) is None
    assert pick_result([giphy_item(w="0")]) is None
    assert pick_result(["junk", giphy_item()]).width == 480


@pytest.mark.asyncio
async def test_fake_gif_data_url() -> None:
    res = await FakeGif().search("current flow")
    assert res.url.startswith("data:image/gif;base64,")
    data = base64.b64decode(res.url.split(",", 1)[1])
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "GIF" and img.size == (res.width, res.height) and img.n_frames > 1
    assert (await FakeGif().search("current flow")).url == res.url
