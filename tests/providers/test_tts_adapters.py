"""Edge / ElevenLabs / OpenAI / Gemini TTS adapters with mocked transports."""

from __future__ import annotations

import base64
import struct
from typing import Any

import httpx
import pytest
import respx

from aadhi.providers.base import ContentBlocked, ProviderError, ProviderNotConfigured, RateLimited, Usage
from aadhi.providers.tts.alignment import words_from_characters
from aadhi.providers.tts.audio_utils import pcm_to_wav
from aadhi.providers.tts.edge import EdgeTTS
from aadhi.providers.tts.elevenlabs import ElevenLabsTTS
from aadhi.providers.tts.gemini import GeminiTTS
from aadhi.providers.tts.openai import OpenAITTS

from .conftest import GEMINI_KEYS, no_sleep
from .fakes import FakeOpenAIClient, gemini_blob_response, gemini_error, gemini_factory, gemini_text_response

ONE_SECOND_WAV = pcm_to_wav(struct.pack("<24000h", *([0] * 24000)), sample_rate=24000)


# --- alignment --------------------------------------------------------------------------------


def test_words_from_characters() -> None:
    text = "Hi, Ohm's law!  ok"
    starts = [i * 0.1 for i in range(len(text))]
    ends = [s + 0.1 for s in starts]
    words = words_from_characters(list(text), starts, ends)
    assert [w.text for w in words] == ["Hi", "Ohm's", "law", "ok"]
    assert words[0].start == pytest.approx(0.0) and words[0].end == pytest.approx(0.2)
    assert words[1].start == pytest.approx(0.4)
    assert words_from_characters(["a", " ", "-"], [0, 1, 2], [1, 2, 3])[0].text == "a"
    assert words_from_characters(list("abc"), [0.0], [0.1]) == words_from_characters(["a"], [0.0], [0.1])


# --- edge -------------------------------------------------------------------------------------------


class FakeCommunicate:
    instances: list[FakeCommunicate] = []

    def __init__(self, text: str, voice: str, *, chunks: list[dict[str, Any]], fail: list[BaseException],
                 **kwargs: Any) -> None:
        self.text, self.voice, self.kwargs = text, voice, kwargs
        self._chunks, self._fail = chunks, fail
        FakeCommunicate.instances.append(self)

    async def stream(self):
        if self._fail:
            raise self._fail.pop(0)
        for chunk in self._chunks:
            yield chunk


def edge_with(chunks: list[dict[str, Any]], settings, fail: list[BaseException] | None = None) -> EdgeTTS:
    FakeCommunicate.instances = []
    failures = list(fail or [])

    def factory(text: str, voice: str, **kwargs: Any) -> FakeCommunicate:
        return FakeCommunicate(text, voice, chunks=chunks, fail=failures, **kwargs)

    return EdgeTTS(settings, communicate_factory=factory, sleep=no_sleep)


EDGE_CHUNKS = [
    {"type": "WordBoundary", "offset": 1_000_000, "duration": 3_000_000, "text": "Hello"},
    {"type": "audio", "data": ONE_SECOND_WAV[: len(ONE_SECOND_WAV) // 2]},
    {"type": "WordBoundary", "offset": 5_000_000, "duration": 9_000_000, "text": "class,"},
    {"type": "WordBoundary", "offset": 9_500_000, "duration": 100, "text": "."},
    {"type": "audio", "data": ONE_SECOND_WAV[len(ONE_SECOND_WAV) // 2 :]},
]


@pytest.mark.asyncio
async def test_edge_collects_audio_and_word_boundaries(app_env) -> None:
    tts = edge_with(EDGE_CHUNKS, app_env)
    usages: list[Usage] = []
    res = await tts.synthesize("Hello class.", voice="", language="ta-IN", rate="+10%", on_usage=usages.append)
    comm = FakeCommunicate.instances[0]
    assert comm.voice == "ta-IN-PallaviNeural"
    assert comm.kwargs["boundary"] == "WordBoundary" and comm.kwargs["rate"] == "+10%"
    assert res.audio == ONE_SECOND_WAV and res.duration == pytest.approx(1.0)
    assert [(w.text, round(w.start, 3), round(w.end, 3)) for w in res.words] == [
        ("Hello", 0.1, 0.4), ("class", 0.5, 1.0)  # 100 ns units -> s; end clamped to the measured duration
    ]
    assert res.mime == "audio/mpeg" and res.provider == "edge"
    assert usages == [Usage(provider="edge", model="ta-IN-PallaviNeural", operation="tts", characters=12)]


@pytest.mark.asyncio
async def test_edge_retries_transient_errors(app_env) -> None:
    from edge_tts.exceptions import NoAudioReceived, WebSocketError

    tts = edge_with(EDGE_CHUNKS, app_env, fail=[WebSocketError("reset"), NoAudioReceived("none")])
    res = await tts.synthesize("Hello class.", voice="en-IN-NeerjaNeural", language="en-IN")
    assert len(FakeCommunicate.instances) == 3 and res.words


@pytest.mark.asyncio
async def test_edge_failures(app_env) -> None:
    from edge_tts.exceptions import WebSocketError

    tts = edge_with(EDGE_CHUNKS, app_env, fail=[WebSocketError("down")] * 3)
    with pytest.raises(ProviderError, match="speech synthesis failed"):
        await tts.synthesize("Hello", voice="en-IN-NeerjaNeural", language="en-IN")
    with pytest.raises(ProviderError, match="no audio"):
        await edge_with([{"type": "WordBoundary", "offset": 0, "duration": 1, "text": "x"}], app_env).synthesize(
            "x", voice="en-IN-NeerjaNeural", language="en-IN")
    for bad_voice in ("en-IN-Neerja'/><x", "x"):
        with pytest.raises(ProviderError, match="invalid voice"):
            await tts.synthesize("Hello", voice=bad_voice, language="en-IN")
    with pytest.raises(ProviderError, match="invalid rate"):
        await tts.synthesize("Hello", voice="en-IN-NeerjaNeural", language="en-IN", rate="fast")
    with pytest.raises(ProviderError, match="empty"):
        await tts.synthesize(" ", voice="en-IN-NeerjaNeural", language="en-IN")


def test_edge_metadata(app_env) -> None:
    tts = EdgeTTS(app_env)
    assert tts.supports_word_timings and tts.default_voice("hi-IN") == "hi-IN-SwaraNeural"
    assert {v["language"] for v in tts.voices("kn-IN")} == {"kn-IN"}


@pytest.mark.asyncio
async def test_edge_tts_is_loaded_off_the_event_loop(app_env, monkeypatch) -> None:
    import threading

    from aadhi.providers import _lazy
    from aadhi.providers.tts import edge as edge_mod

    monkeypatch.setattr(_lazy, "_READY", {})
    with pytest.raises(RuntimeError, match="not been loaded"):
        edge_mod._default_factory("x", "en-IN-NeerjaNeural")  # never imports on the calling thread
    loop_thread = threading.get_ident()
    import_threads: list[int] = []
    real_import = _lazy.importlib.import_module

    def recording_import(name: str):
        import_threads.append(threading.get_ident())
        return real_import(name)

    monkeypatch.setattr(_lazy.importlib, "import_module", recording_import)
    module = await edge_mod.load_edge_tts()
    assert module.__name__ == "edge_tts" and import_threads and loop_thread not in import_threads
    assert _lazy.imported("edge_tts") is module
    from edge_tts.exceptions import WebSocketError

    assert edge_mod.classify_edge_error(WebSocketError("reset")).retry
    comm = edge_mod._default_factory("Hello", "en-IN-NeerjaNeural")  # constructs only, no network
    assert type(comm).__name__ == "Communicate"


def test_provider_modules_do_not_import_sdks_at_import_time() -> None:
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import aadhi.providers.tts.edge, aadhi.providers.llm.gemini, aadhi.providers.tts.gemini\n"
        "import aadhi.providers.image.gemini, aadhi.providers.video.veo, aadhi.providers.gemini_common\n"
        "print(sorted(m for m in ('edge_tts', 'google.genai', 'openai', 'aiohttp') if m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True)  # noqa: S603
    assert out.stdout.strip() == "[]"


# --- elevenlabs ----------------------------------------------------------------------------------

EL_KEY = "el-secret-key-1234567890"
VOICE = "21m00Tcm4TlvDq8ikWAM"
EL_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}/with-timestamps"


def el_payload(text: str = "Hi all") -> dict[str, Any]:
    starts = [i * 0.1 for i in range(len(text))]
    return {
        "audio_base64": base64.b64encode(ONE_SECOND_WAV).decode(),
        "alignment": {
            "characters": list(text),
            "character_start_times_seconds": starts,
            "character_end_times_seconds": [s + 0.1 for s in starts],
        },
    }


@pytest.fixture()
def el_settings(make_settings):
    return make_settings(elevenlabs_api_key=EL_KEY, tts_provider="elevenlabs")


@pytest.mark.asyncio
@respx.mock
async def test_elevenlabs_success(el_settings) -> None:
    route = respx.post(EL_URL).mock(return_value=httpx.Response(200, json=el_payload()))
    tts = ElevenLabsTTS(el_settings, sleep=no_sleep)
    usages: list[Usage] = []
    res = await tts.synthesize("Hi all", voice=VOICE, language="en-IN", rate="+10%", on_usage=usages.append)
    request = route.calls.last.request
    assert request.headers["xi-api-key"] == EL_KEY
    assert request.url.params["output_format"] == "mp3_44100_128"
    body = __import__("json").loads(request.content)
    assert body["model_id"] == "eleven_multilingual_v2" and body["voice_settings"]["speed"] == 1.1
    assert [w.text for w in res.words] == ["Hi", "all"]
    assert res.words[1].start == pytest.approx(0.3) and res.duration == pytest.approx(1.0)
    assert usages[0].provider == "elevenlabs" and usages[0].characters == 6


@pytest.mark.asyncio
@respx.mock
async def test_elevenlabs_quotes_voice_path(el_settings) -> None:
    tts = ElevenLabsTTS(el_settings, sleep=no_sleep)
    with pytest.raises(ProviderError, match="invalid voice"):
        await tts.synthesize("Hi", voice="../../v1/user", language="en-IN")
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
async def test_elevenlabs_retries_then_rate_limited(el_settings) -> None:
    route = respx.post(EL_URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=el_payload())])
    tts = ElevenLabsTTS(el_settings, sleep=no_sleep)
    await tts.synthesize("Hi all", voice=VOICE, language="en-IN")
    assert route.call_count == 2

    respx.post(EL_URL).mock(return_value=httpx.Response(429, json={"detail": {"message": "too many"}}))
    with pytest.raises(RateLimited):
        await ElevenLabsTTS(el_settings, sleep=no_sleep, max_attempts=2).synthesize("Hi", voice=VOICE, language="en-IN")


@pytest.mark.asyncio
@respx.mock
async def test_elevenlabs_error_redacted(el_settings) -> None:
    respx.post(EL_URL).mock(return_value=httpx.Response(401, json={"detail": f"invalid key {EL_KEY}"}))
    with pytest.raises(ProviderError) as info:
        await ElevenLabsTTS(el_settings, sleep=no_sleep).synthesize("Hi", voice=VOICE, language="en-IN")
    assert info.value.status == 401 and EL_KEY not in str(info.value) and "api.elevenlabs.io" in str(info.value)

    respx.post(EL_URL).mock(return_value=httpx.Response(200, json={"audio_base64": "!!!"}))
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="no audio"):
        await ElevenLabsTTS(el_settings, sleep=no_sleep).synthesize("Hi", voice=VOICE, language="en-IN",
                                                                    on_usage=usages.append)
    assert [u.characters for u in usages] == [2]  # the successful request is billed


async def _broken_probe(data, settings=None):
    raise ProviderError("ffprobe is not installed")


@pytest.mark.asyncio
@respx.mock
async def test_elevenlabs_reports_usage_before_probe(el_settings, monkeypatch) -> None:
    from aadhi.providers.tts import elevenlabs as el_mod

    monkeypatch.setattr(el_mod, "probe_duration", _broken_probe)
    respx.post(EL_URL).mock(return_value=httpx.Response(200, json=el_payload()))
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="ffprobe"):
        await ElevenLabsTTS(el_settings, sleep=no_sleep).synthesize("Hi all", voice=VOICE, language="en-IN",
                                                                    on_usage=usages.append)
    assert [(u.provider, u.characters) for u in usages] == [("elevenlabs", 6)]


@pytest.mark.asyncio
@respx.mock
async def test_elevenlabs_honours_long_retry_after(el_settings) -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    respx.post(EL_URL).mock(side_effect=[httpx.Response(429, headers={"retry-after": "45"}),
                                         httpx.Response(200, json=el_payload())])
    await ElevenLabsTTS(el_settings, sleep=sleep).synthesize("Hi all", voice=VOICE, language="en-IN")
    assert sleeps == [45.0]  # not cut to max_delay (30 s): an earlier retry would just be limited again


def test_elevenlabs_requires_key(make_settings) -> None:
    with pytest.raises(ProviderNotConfigured):
        ElevenLabsTTS(make_settings())


# --- openai -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_tts(make_settings) -> None:
    s = make_settings(openai_api_key="sk-test-123456789")
    client = FakeOpenAIClient(audio=ONE_SECOND_WAV)
    tts = OpenAITTS(s, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    res = await tts.synthesize("Hello", voice="", language="hi-IN", rate="-20%", on_usage=usages.append)
    call = client.audio.speech.calls[0]
    assert call == {"model": "gpt-4o-mini-tts", "voice": "nova", "input": "Hello", "response_format": "mp3",
                    "speed": 0.8}
    assert res.words == [] and res.duration == pytest.approx(1.0) and not tts.supports_word_timings
    assert usages[0].provider == "openai" and usages[0].characters == 5 and usages[0].model == "gpt-4o-mini-tts"
    with pytest.raises(ProviderError, match="longer than"):
        await tts.synthesize("x" * 5000, voice="nova", language="en-IN")
    with pytest.raises(ProviderError, match="invalid voice"):
        await tts.synthesize("x", voice="Nova Voice!", language="en-IN")


@pytest.mark.asyncio
async def test_openai_tts_legacy_models_keep_the_same_request(make_settings) -> None:
    """tts-1 / tts-1-hd (until their 2027-01-06 shutdown) get exactly the request gpt-4o-mini-tts gets."""
    for model in ("tts-1-hd", "tts-1"):
        s = make_settings(openai_api_key="sk-test-123456789", openai_tts_model=model)
        client = FakeOpenAIClient(audio=ONE_SECOND_WAV)
        usages: list[Usage] = []
        await OpenAITTS(s, client_factory=lambda c=client: c, sleep=no_sleep).synthesize(
            "Hello", voice="onyx", language="en-IN", on_usage=usages.append)
        assert client.audio.speech.calls == [{"model": model, "voice": "onyx", "input": "Hello",
                                              "response_format": "mp3"}]
        assert usages[0].model == model


@pytest.mark.asyncio
async def test_openai_tts_reports_usage_before_probe(make_settings, monkeypatch) -> None:
    from aadhi.providers.tts import openai as openai_tts_mod

    monkeypatch.setattr(openai_tts_mod, "probe_duration", _broken_probe)
    s = make_settings(openai_api_key="sk-test-123456789")
    tts = OpenAITTS(s, client_factory=lambda: FakeOpenAIClient(audio=b"not audio"), sleep=no_sleep)
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="ffprobe"):
        await tts.synthesize("Hello", voice="nova", language="en-IN", on_usage=usages.append)
    assert [(u.provider, u.characters) for u in usages] == [("openai", 5)]


OPENAI_TEST_KEY = "sk-test-123456789"
# OpenAI's answer to a speech request naming a model it does not serve (retired, renamed, misspelt) or one the API key
# has no access to (OpenAI's error format: {"error": {"message", "type", "param", "code"}})
OPENAI_MODEL_NOT_FOUND = {"error": {"message": "The model `tts-1` does not exist or you do not have access to it.",
                                    "type": "invalid_request_error", "param": None, "code": "model_not_found"}}


def openai_speech_client(answer: httpx.Response, requests: list[httpx.Request]) -> Any:
    """The real ``AsyncOpenAI`` SDK (its own status-error mapping) over a transport that gives every request
    ``answer``; ``requests`` records them."""
    import openai

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answer

    return openai.AsyncOpenAI(api_key=OPENAI_TEST_KEY, max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (404, OPENAI_MODEL_NOT_FOUND),
    (410, {"error": {"message": "The model `tts-1` has been shut down.", "type": "invalid_request_error",
                     "param": None, "code": None}}),
])
async def test_openai_tts_model_no_longer_served_is_model_unavailable(make_settings, status, body) -> None:
    """Every /v1/audio/speech model retires on 2027-01-06 (docs/OPERATIONS.md section 3.3): a request naming one is
    then answered 404 model_not_found. One request, no retry, nothing billed, and an error the asset build recognises
    (it narrates with Edge instead; design §10.3 H0) that is not a personal-key rejection."""
    from aadhi.credentials import personal_key_rejection
    from aadhi.providers.base import ModelUnavailable, ProviderUnavailable

    settings = make_settings(openai_api_key=OPENAI_TEST_KEY, openai_tts_model="tts-1")
    requests: list[httpx.Request] = []
    client = openai_speech_client(httpx.Response(status, json=body), requests)
    usages: list[Usage] = []
    with pytest.raises(ModelUnavailable) as exc:
        await OpenAITTS(settings, client_factory=lambda: client, sleep=no_sleep, max_attempts=4).synthesize(
            "Vanakkam", voice="nova", language="ta-IN", on_usage=usages.append)
    await client.close()
    err = exc.value
    assert isinstance(err, ProviderUnavailable) and isinstance(err, ProviderError)
    assert err.status == status and err.provider == "openai"
    assert "the speech model tts-1 is not available" in str(err) and "OPENAI_TTS_MODEL" in str(err)
    assert "does not exist or you do not have access" in str(err) or status == 410  # OpenAI's reason: the job log
    assert OPENAI_TEST_KEY not in str(err)
    assert [r.url.path for r in requests] == ["/v1/audio/speech"] and usages == []  # not retried, nothing billed
    assert personal_key_rejection(err, {"openai": "personal"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,rejected", [
    (400, {"error": {"message": "Invalid value for 'voice'.", "type": "invalid_request_error", "param": "voice",
                     "code": "invalid_value"}}, False),
    (401, {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error", "param": None,
                     "code": "invalid_api_key"}}, True),
    (403, {"error": {"message": "You are not allowed to sample from this model.", "type": "invalid_request_error",
                     "param": None, "code": None}}, True),
])
async def test_openai_tts_other_client_errors_stay_plain_provider_errors(make_settings, status, body,
                                                                          rejected) -> None:
    """Only 404 / 410 name a model the API does not serve: a bad request stays a plain narration failure, and a
    refused key keeps the personal-key rejection path (the job fails; it never falls back to Edge)."""
    from aadhi.credentials import personal_key_rejection
    from aadhi.providers.base import ProviderUnavailable

    requests: list[httpx.Request] = []
    client = openai_speech_client(httpx.Response(status, json=body), requests)
    with pytest.raises(ProviderError) as exc:
        await OpenAITTS(make_settings(openai_api_key=OPENAI_TEST_KEY), client_factory=lambda: client,
                        sleep=no_sleep).synthesize("x", voice="nova", language="en-IN")
    await client.close()
    assert exc.value.status == status and not isinstance(exc.value, ProviderUnavailable) and len(requests) == 1
    assert (personal_key_rejection(exc.value, {"openai": "personal"}) is not None) is rejected


# --- gemini -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_tts_pcm_to_wav(gemini_settings) -> None:
    pcm = struct.pack("<12000h", *([100] * 12000))
    factory, clients = gemini_factory({GEMINI_KEYS[0]: [gemini_blob_response(pcm, "audio/L16;codec=pcm;rate=24000")]})
    tts = GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep)
    usages: list[Usage] = []
    res = await tts.synthesize("Vanakkam", voice="", language="ta-IN", on_usage=usages.append)
    assert res.audio.startswith(b"RIFF") and res.mime == "audio/wav" and res.sample_rate == 24000
    assert res.duration == pytest.approx(0.5) and res.voice == "Kore" and res.words == []
    cfg = clients[GEMINI_KEYS[0]].models.calls[0]["config"]
    assert cfg.response_modalities == ["AUDIO"]
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert usages[0].operation == "tts" and usages[0].characters == 8 and usages[0].provider == "gemini"


@pytest.mark.asyncio
async def test_gemini_38_tts_wav_answer_is_kept(gemini_settings) -> None:
    """Gemini 3.8 TTS (the default) answers with a RIFF WAV: used as is, never wrapped a second time."""
    assert gemini_settings.gemini_tts_model == "gemini-3.8-flash-tts"
    wav = pcm_to_wav(struct.pack("<12000h", *([100] * 12000)), sample_rate=24000)
    factory, clients = gemini_factory({GEMINI_KEYS[0]: [gemini_blob_response(wav, "audio/wav")]})
    usages: list[Usage] = []
    res = await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep).synthesize(
        "Vanakkam", voice="Sulafat", language="ta-IN", on_usage=usages.append)
    assert res.audio == wav and res.mime == "audio/wav" and res.sample_rate == 24000
    assert res.duration == pytest.approx(0.5) and res.voice == "Sulafat"
    call = clients[GEMINI_KEYS[0]].models.calls[0]
    assert call["model"] == "gemini-3.8-flash-tts" and call["contents"] == "Vanakkam"
    cfg = call["config"]
    assert cfg.response_modalities == ["AUDIO"] and cfg.temperature is None
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Sulafat"
    assert usages[0].model == "gemini-3.8-flash-tts" and usages[0].characters == 8


@pytest.mark.asyncio
async def test_gemini_tts_errors(gemini_settings) -> None:
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_text_response("no audio here")]})
    with pytest.raises(ProviderError, match="no audio"):
        await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep).synthesize(
            "x", voice="Kore", language="en-IN")
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_text_response("", finish="SAFETY")]})
    with pytest.raises(ContentBlocked):
        await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep).synthesize(
            "x", voice="Kore", language="en-IN")
    limited = {k: [gemini_error(429, "quota", "RESOURCE_EXHAUSTED")] * 3 for k in GEMINI_KEYS}
    factory, _ = gemini_factory(limited)
    with pytest.raises(RateLimited):
        await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep, max_attempts=3).synthesize(
            "x", voice="Kore", language="en-IN")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(404, "NOT_FOUND"), (410, "GONE")])
async def test_gemini_tts_model_no_longer_served_is_model_unavailable(make_settings, status, code) -> None:
    """A retired or misspelt GEMINI_TTS_MODEL (design §10.3 H0): one request, no retry, nothing billed, and an error
    the asset build recognises (it narrates with Edge instead) that is not a personal-key rejection."""
    from aadhi.credentials import personal_key_rejection
    from aadhi.providers.base import ModelUnavailable, ProviderUnavailable

    settings = make_settings(gemini_api_key=GEMINI_KEYS[0], gemini_tts_model="gemini-2.5-flash-preview-tts")
    message = ("models/gemini-2.5-flash-preview-tts is not found for API version v1beta, or is not supported for "
               "generateContent. Call ListModels to see the list of available models and their supported methods.")
    factory, clients = gemini_factory({GEMINI_KEYS[0]: [gemini_error(status, message, code)] * 3})
    usages: list[Usage] = []
    with pytest.raises(ModelUnavailable) as exc:
        await GeminiTTS(settings, client_factory=factory, sleep=no_sleep, max_attempts=3).synthesize(
            "Vanakkam", voice="Kore", language="ta-IN", on_usage=usages.append)
    err = exc.value
    assert isinstance(err, ProviderUnavailable) and isinstance(err, ProviderError)
    assert err.status == status and err.provider == "gemini"
    assert "gemini-2.5-flash-preview-tts is not available" in str(err) and "GEMINI_TTS_MODEL" in str(err)
    assert GEMINI_KEYS[0] not in str(err)
    assert len(clients[GEMINI_KEYS[0]].models.calls) == 1 and usages == []  # not retried, nothing billed
    assert personal_key_rejection(err, {"gemini": "personal"}) is None


@pytest.mark.asyncio
async def test_gemini_tts_other_client_errors_stay_plain_provider_errors(gemini_settings) -> None:
    from aadhi.providers.base import ProviderUnavailable

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_error(400, "Request contains an invalid argument.",
                                                               "INVALID_ARGUMENT")]})
    with pytest.raises(ProviderError) as exc:
        await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep).synthesize(
            "x", voice="Kore", language="en-IN")
    assert exc.value.status == 400 and not isinstance(exc.value, ProviderUnavailable)


@pytest.mark.asyncio
async def test_gemini_tts_reports_usage_before_checks(gemini_settings, monkeypatch) -> None:
    from aadhi.providers.tts import gemini as gemini_tts_mod

    usages: list[Usage] = []
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_text_response("", finish="SAFETY", prompt_tokens=9)]})
    with pytest.raises(ContentBlocked):
        await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep).synthesize(
            "x", voice="Kore", language="en-IN", on_usage=usages.append)
    assert [u.input_tokens for u in usages] == [9]  # blocked answers are billed too

    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_text_response("", finish="RECITATION")]})
    with pytest.raises(ContentBlocked, match="recitation"):
        await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep).synthesize(
            "x", voice="Kore", language="en-IN")

    monkeypatch.setattr(gemini_tts_mod, "probe_duration", _broken_probe)
    pcm = struct.pack("<2400h", *([0] * 2400))
    factory, _ = gemini_factory({GEMINI_KEYS[0]: [gemini_blob_response(pcm, "audio/L16;rate=24000")]})
    usages.clear()
    with pytest.raises(ProviderError, match="ffprobe"):
        await GeminiTTS(gemini_settings, client_factory=factory, sleep=no_sleep).synthesize(
            "Vanakkam", voice="Kore", language="ta-IN", on_usage=usages.append)
    assert [(u.operation, u.characters) for u in usages] == [("tts", 8)]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_edge_with_real_mp3_measured_by_ffprobe(app_env, tmp_path) -> None:
    import subprocess

    out = tmp_path / "tone.mp3"
    subprocess.run(  # noqa: S603 - fixed argv
        [app_env.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=300:duration=1.5", "-ac", "1", "-ar", "24000", "-b:a", "48k", str(out)],
        check=True,
    )
    mp3 = out.read_bytes()
    tts = edge_with([{"type": "audio", "data": mp3},
                     {"type": "WordBoundary", "offset": 0, "duration": 30_000_000, "text": "tone"}], app_env)
    res = await tts.synthesize("tone", voice="en-IN-NeerjaNeural", language="en-IN")
    assert res.duration == pytest.approx(1.5, abs=0.1)
    assert res.words[0].end == pytest.approx(res.duration)  # clamped to the measured duration
