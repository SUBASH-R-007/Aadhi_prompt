"""Backend tests for the AI provider adapters and the provider registry (ai_providers.py, Phase 8).

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
No provider is ever reached: HTTP responses and the Google SDK are replaced by local fakes, and any
socket connection fails the test. Each adapter is checked for request normalization, response
normalization, success, failure, malformed answers, timeouts, rate limits, unsupported requests,
refused credentials, job polling, output retrieval and metadata.
"""
import asyncio
import io
import json
import os
import socket
import tempfile
import time
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from backend_env import FFMPEG, TMP  # noqa: F401  first: throwaway database

import ai_cache  # noqa: E402
import ai_providers as P  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 600
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("151.101.1.1", 443))]


_real_connect = socket.socket.connect


def _guarded_connect(sock, address):
    host = address[0] if isinstance(address, tuple) else address
    if host in ("127.0.0.1", "::1"):  # the event loop's own socket pair on Windows
        return _real_connect(sock, address)
    raise AssertionError(f"a real network connection was attempted ({host})")


def no_network():
    return mock.patch.object(socket.socket, "connect", _guarded_connect)


def run(coro):
    return asyncio.run(coro)


def request(media_type="image", prompt="A coral reef", **extra):
    return P.MediaRequest(media_type=media_type, prompt=prompt, **extra)


class FakeResponse(io.BytesIO):
    def __init__(self, data, ctype="image/jpeg", length=True):
        super().__init__(data)
        self.headers = {"Content-Type": ctype}
        if length:
            self.headers["Content-Length"] = str(len(data))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://image.pollinations.ai/prompt/x", code, "status", headers or {}, None)


class ApiError(Exception):
    """Shaped like google.genai.errors.APIError: an HTTP code and a message."""

    def __init__(self, code, message="api error"):
        super().__init__(message)
        self.code = code
        self.message = message


class ErrorsAndSanitizingTest(unittest.TestCase):
    def test_http_statuses_map_to_categories(self):
        cases = {429: P.Failure.RATE_LIMITED, 401: P.Failure.AUTH, 402: P.Failure.AUTH, 403: P.Failure.AUTH, 404: P.Failure.UNSUPPORTED,
                 400: P.Failure.REJECTED, 422: P.Failure.REJECTED, 408: P.Failure.TIMEOUT, 504: P.Failure.TIMEOUT,
                 500: P.Failure.UNAVAILABLE, 503: P.Failure.UNAVAILABLE}
        for status, category in cases.items():
            self.assertEqual(P.error_for_status(status).category, category, status)
        self.assertTrue(P.error_for_status(503).retryable)
        self.assertFalse(P.error_for_status(401).retryable)
        self.assertFalse(P.error_for_status(400).fallback_ok)   # a refused prompt is not sent elsewhere
        self.assertTrue(P.error_for_status(401).fallback_ok)    # refused credentials: another provider may be tried

    def test_exceptions_are_classified(self):
        self.assertEqual(P.error_from_exception(socket.timeout("slow")).category, P.Failure.TIMEOUT)
        self.assertEqual(P.error_from_exception(ConnectionResetError("reset")).category, P.Failure.UNAVAILABLE)
        limited = P.error_from_exception(http_error(429, {"Retry-After": "7"}))
        self.assertEqual((limited.category, limited.retry_after), (P.Failure.RATE_LIMITED, 7.0))
        self.assertEqual(P.error_from_exception(ApiError(429)).category, P.Failure.RATE_LIMITED)
        bug = P.error_from_exception(KeyError("x"))
        self.assertEqual((bug.category, bug.retryable, bug.fallback_ok), (P.Failure.UNAVAILABLE, False, True))

    def test_retry_after_accepts_seconds_and_dates(self):
        self.assertEqual(P.parse_retry_after("12"), 12.0)
        self.assertIsNone(P.parse_retry_after("soon"))
        import datetime
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        self.assertAlmostEqual(P.parse_retry_after("Thu, 01 Jan 2026 00:00:30 GMT", now=now), 30.0)

    def test_secrets_never_survive_sanitizing(self):
        env = {"GEMINI_API_KEY": "configured-secret-value-123", "JWT_SECRET": "another-secret-456"}
        text = ("failed https://generativelanguage.googleapis.com/v1/models?key=AIzaSyA1234567890abcdefghijklmnopqrstu&x=1 "
                "Authorization: Bearer abc.def.ghi-1234567 token=tok_998877 configured-secret-value-123 another-secret-456 "
                "https://storage.googleapis.com/f.mp4?X-Goog-Signature=deadbeef1234")
        clean = P.sanitize(text, limit=1000, env=env)
        for secret in ("AIzaSyA1234567890", "abc.def.ghi-1234567", "tok_998877", "configured-secret-value-123",
                       "another-secret-456", "deadbeef1234"):
            self.assertNotIn(secret, clean)
        self.assertIn("[redacted]", clean)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTOTALLYSECRET000000000000"}):
            self.assertNotIn("TOTALLYSECRET", P.ProviderError(P.Failure.AUTH, "bad key AIzaSyTOTALLYSECRET000000000000").message)


class SafeDownloadTest(unittest.TestCase):
    HOSTS = frozenset({"image.pollinations.ai"})

    def test_only_https_on_allowed_public_hosts(self):
        resolve_public = lambda *a, **k: PUBLIC  # noqa: E731
        self.assertTrue(P.check_url("https://image.pollinations.ai/prompt/x", self.HOSTS, resolve=resolve_public))
        for url in ("http://image.pollinations.ai/prompt/x", "https://evil.example/x", "file:///etc/passwd"):
            with self.assertRaises(P.UnsafeUrl, msg=url):
                P.check_url(url, self.HOSTS, resolve=resolve_public)
        for address in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.2", "::1"):
            private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.assertRaises(P.UnsafeUrl, msg=address):
                P.check_url("https://image.pollinations.ai/x", self.HOSTS, resolve=lambda *a, **k: private)

    def test_redirects_are_checked_too(self):
        handler = P._AllowedRedirects(self.HOSTS)
        with mock.patch("socket.getaddrinfo", return_value=PUBLIC):
            with self.assertRaises(P.UnsafeUrl):
                handler.redirect_request(None, None, 302, "Found", {}, "https://metadata.internal/latest")

    def test_downloads_are_size_capped_and_typed(self):
        dest = os.path.join(TMP, "dl.bin")
        with no_network(), mock.patch("socket.getaddrinfo", return_value=PUBLIC):
            with mock.patch("urllib.request.build_opener", return_value=FakeOpener(FakeResponse(JPEG * 3, length=False))):
                with self.assertRaises(P.ProviderError) as big:
                    P.safe_download("https://image.pollinations.ai/x", dest, allowed_hosts=self.HOSTS, max_bytes=1000, timeout=5)
            self.assertEqual(big.exception.category, P.Failure.INVALID_OUTPUT)
            with mock.patch("urllib.request.build_opener", return_value=FakeOpener(FakeResponse(b"<html>", "text/html"))):
                with self.assertRaises(P.ProviderError) as html:
                    P.safe_download("https://image.pollinations.ai/x", dest, allowed_hosts=self.HOSTS, max_bytes=10 ** 6,
                                    timeout=5, expect_prefix="image/")
            self.assertEqual(html.exception.category, P.Failure.INVALID_OUTPUT)
            with self.assertRaises(P.ProviderError) as unsafe:
                P.safe_download("https://evil.example/x", dest, allowed_hosts=self.HOSTS, max_bytes=10, timeout=5)
            self.assertEqual((unsafe.exception.category, unsafe.exception.retryable), (P.Failure.INVALID_OUTPUT, False))


class PollinationsTest(unittest.TestCase):
    def setUp(self):
        self.provider = P.PollinationsProvider(env={})
        self.dest = tempfile.mktemp(suffix=".jpg", dir=TMP)

    def generate(self, outcome, req=None):
        opener = FakeOpener(outcome)
        req = req or request()
        with no_network(), mock.patch("socket.getaddrinfo", return_value=PUBLIC), \
                mock.patch("urllib.request.build_opener", return_value=opener):
            result = run(self.provider.generate(req, self.provider.settings_for(req), self.dest))
        return result, opener

    def test_request_and_response_normalization(self):
        result, opener = self.generate(FakeResponse(JPEG))
        url = opener.requests[0][0].full_url
        self.assertTrue(url.startswith("https://image.pollinations.ai/prompt/A%20coral%20reef?"))
        self.assertIn("width=800", url)
        self.assertIn("height=1200", url)
        self.assertIn("nologo=true", url)
        self.assertEqual(opener.requests[0][1], self.provider.timeout)
        self.assertEqual((result.status, result.provider, result.media_type, result.model), (P.COMPLETED, "pollinations", "image", None))
        self.assertIsInstance(result.metadata["seed"], int)  # provenance, not identity
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), JPEG)
        self.assertIsNotNone(result.duration_ms)

    def test_failures_are_normalized(self):
        cases = [(http_error(429, {"Retry-After": "3"}), P.Failure.RATE_LIMITED), (http_error(503), P.Failure.UNAVAILABLE),
                 (http_error(401), P.Failure.AUTH), (http_error(402), P.Failure.AUTH), (http_error(400), P.Failure.REJECTED),
                 (socket.timeout("timed out"), P.Failure.TIMEOUT), (urllib.error.URLError("no route"), P.Failure.UNAVAILABLE),
                 (FakeResponse(b"{}", "application/json"), P.Failure.INVALID_OUTPUT)]
        for outcome, category in cases:
            with self.assertRaises(P.ProviderError, msg=category) as caught:
                self.generate(outcome)
            self.assertEqual((caught.exception.category, caught.exception.provider), (category, "pollinations"))
        with self.assertRaises(P.ProviderError) as limited:
            self.generate(http_error(429, {"Retry-After": "3"}))
        self.assertEqual(limited.exception.retry_after, 3.0)

    def test_shapes_and_unsupported_requests(self):
        wide = request(aspect_ratio="16:9")
        self.provider.check(wide)
        params = self.provider.settings_for(wide)["parameters"]
        self.assertAlmostEqual(params["width"] / params["height"], 16 / 9, delta=0.02)
        self.assertEqual(self.provider.settings_for(request(aspect_ratio="2:3")), self.provider.settings_for(request()))  # usual shape: same identity
        for bad in (request("video"), request(aspect_ratio="7:1"), request(duration_seconds=4), request(prompt="x" * 1600)):
            with self.assertRaises(P.ProviderError) as caught:
                self.provider.check(bad)
            self.assertEqual(caught.exception.category, P.Failure.UNSUPPORTED)


def fake_genai(client):
    types = SimpleNamespace(
        HttpOptions=lambda **kw: ("http", kw),
        GenerateContentConfig=lambda **kw: ("content-config", kw),
        ImageConfig=lambda **kw: ("image-config", kw),
        GenerateVideosConfig=lambda **kw: ("videos-config", kw))
    genai = SimpleNamespace(Client=lambda **kw: client.bind(kw))
    return mock.patch.object(P, "_genai", return_value=(genai, types))


class FakeClient:
    def __init__(self):
        self.calls = []
        self.kwargs = None

    def bind(self, kwargs):
        self.kwargs = kwargs
        return self


def image_response(parts=None, finish=None, block=None):
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts or []), finish_reason=SimpleNamespace(name=finish) if finish else None)
    return SimpleNamespace(candidates=[candidate], prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
                           model_version="gemini-2.5-flash-image-001")


class GeminiImageTest(unittest.TestCase):
    ENV = {"GEMINI_API_KEY": "test-key-not-real", "GEMINI_IMAGE_ENABLED": "1"}

    def generate(self, answer, env=None):
        client = FakeClient()

        def generate_content(**kw):
            client.calls.append(kw)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        client.models = SimpleNamespace(generate_content=generate_content)
        provider = P.GeminiImageProvider(env=env or self.ENV)
        dest = tempfile.mktemp(suffix=".png", dir=TMP)
        req = request()
        with no_network(), fake_genai(client):
            result = run(provider.generate(req, provider.settings_for(req), dest))
        return result, client, dest

    def test_configuration_is_opt_in_and_needs_a_key(self):
        self.assertEqual(P.GeminiImageProvider(env={"GEMINI_API_KEY": "k"}).configuration()[0], "disabled")
        self.assertEqual(P.GeminiImageProvider(env={"GEMINI_IMAGE_ENABLED": "1"}).configuration()[0], "not_configured")
        self.assertEqual(P.GeminiImageProvider(env=self.ENV).configuration()[0], "available")
        self.assertTrue(P.GeminiImageProvider(env=self.ENV).capabilities().paid)

    def test_success_request_and_metadata(self):
        part = SimpleNamespace(inline_data=SimpleNamespace(data=JPEG, mime_type="image/png"))
        result, client, dest = self.generate(image_response([part]))
        call = client.calls[0]
        self.assertEqual(call["model"], "gemini-2.5-flash-image")
        self.assertEqual(call["contents"], "A coral reef")
        config = call["config"][1]
        self.assertEqual(config["response_modalities"], ["IMAGE"])
        self.assertEqual(config["image_config"], ("image-config", {"aspect_ratio": "2:3"}))
        self.assertEqual(client.kwargs["api_key"], "test-key-not-real")
        self.assertEqual((result.status, result.model, result.metadata["model_version"]),
                         (P.COMPLETED, "gemini-2.5-flash-image", "gemini-2.5-flash-image-001"))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), JPEG)

    def test_refusals_errors_and_malformed_answers(self):
        cases = [(image_response(finish="IMAGE_SAFETY"), P.Failure.REJECTED), (image_response(block="SAFETY"), P.Failure.REJECTED),
                 (image_response(finish="STOP"), P.Failure.INVALID_OUTPUT), (ApiError(429), P.Failure.RATE_LIMITED),
                 (ApiError(401), P.Failure.AUTH), (ApiError(404, "model not found"), P.Failure.UNSUPPORTED),
                 (ApiError(503), P.Failure.UNAVAILABLE), (socket.timeout("slow"), P.Failure.TIMEOUT)]
        for answer, category in cases:
            with self.assertRaises(P.ProviderError, msg=category) as caught:
                self.generate(answer)
            self.assertEqual(caught.exception.category, category)

    def test_model_can_be_configured(self):
        provider = P.GeminiImageProvider(env={**self.ENV, "GEMINI_IMAGE_MODEL": "gemini-3.1-flash-image"})
        self.assertEqual(provider.settings_for(request())["model"], "gemini-3.1-flash-image")
        with self.assertRaises(P.ProviderError):
            provider.check(request(model="imagen-4.0-generate"))


class VeoTest(unittest.TestCase):
    ENV = {"GEMINI_API_KEY": "test-key-not-real", "VEO_POLL_SECONDS": "0.01", "VEO_TIMEOUT": "0.3"}

    def client(self, done_after=1, response=None, error=None, submit_error=None, download=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 600):
        client = FakeClient()
        state = {"polls": 0, "cancelled": False}

        def generate_videos(**kw):
            client.calls.append(kw)
            if submit_error:
                raise submit_error
            return SimpleNamespace(name="operations/veo-123", done=False, error=None, response=None)

        def get(operation):
            state["polls"] += 1
            done = done_after is not None and state["polls"] >= done_after
            return SimpleNamespace(name=operation.name, done=done, error=error if done else None,
                                   response=(response if response is not None else
                                             SimpleNamespace(generated_videos=[SimpleNamespace(video=SimpleNamespace(uri="u", video_bytes=None))],
                                                             rai_media_filtered_count=0)) if done else None)
        client.models = SimpleNamespace(generate_videos=generate_videos)
        client.operations = SimpleNamespace(get=get)
        client.files = SimpleNamespace(download=lambda file: download)
        client.state = state
        return client

    def generate(self, client, req=None, env=None, mode="veo"):
        provider = P.VeoProvider(env=env or self.ENV, video_mode=lambda: mode)
        req = req or request("video", "A glacier calving")
        dest = tempfile.mktemp(suffix=".mp4", dir=TMP)
        progress = []
        with no_network(), fake_genai(client):
            result = run(provider.generate(req, provider.settings_for(req), dest, progress=lambda s, j: progress.append((s, j))))
        return result, progress, dest

    def test_configuration(self):
        self.assertEqual(P.VeoProvider(env={}, video_mode=lambda: "veo").configuration()[0], "not_configured")
        self.assertEqual(P.VeoProvider(env={"GEMINI_API_KEY": "k"}, video_mode=lambda: "ltx").configuration()[0], "disabled")
        self.assertEqual(P.VeoProvider(env={"GEMINI_API_KEY": "k", "VEO_ENABLED": "1"}, video_mode=lambda: "ltx").configuration()[0], "available")
        self.assertEqual(P.VeoProvider(env={"GEMINI_API_KEY": "k"}, video_mode=lambda: "veo").configuration()[0], "available")

    def test_job_is_submitted_polled_and_downloaded(self):
        client = self.client(done_after=3)
        result, progress, dest = self.generate(client)
        call = client.calls[0]
        self.assertEqual((call["model"], call["prompt"]), ("veo-2.0-generate-001", "A glacier calving"))
        self.assertEqual(call["config"][1], {"aspect_ratio": "16:9", "person_generation": "ALLOW_ADULT"})
        self.assertEqual(client.state["polls"], 3)
        self.assertEqual(progress, [(P.RUNNING, "operations/veo-123")])
        self.assertEqual((result.status, result.provider_job_id, result.model), (P.COMPLETED, "operations/veo-123", "veo-2.0-generate-001"))
        self.assertGreater(os.path.getsize(dest), 500)

    def test_duration_and_shape_requests(self):
        client = self.client()
        self.generate(client, request("video", "x", aspect_ratio="9:16", duration_seconds=6))
        self.assertEqual(client.calls[0]["config"][1], {"aspect_ratio": "9:16", "person_generation": "ALLOW_ADULT", "duration_seconds": 6})
        with self.assertRaises(P.ProviderError):
            P.VeoProvider(env=self.ENV, video_mode=lambda: "veo").check(request("video", "x", duration_seconds=12))

    def test_polling_is_bounded_and_cancels(self):
        client = self.client(done_after=None)
        provider = P.VeoProvider(env=self.ENV, video_mode=lambda: "veo")
        with mock.patch.object(P.VeoProvider, "cancel") as cancel:
            started = time.time()
            with self.assertRaises(P.ProviderError) as caught:
                self.generate(client)
        self.assertLess(time.time() - started, 3)
        self.assertEqual((caught.exception.category, caught.exception.retryable), (P.Failure.TIMEOUT, False))  # never resubmitted
        cancel.assert_called_once()
        self.assertIsNotNone(provider)

    def test_failed_filtered_and_malformed_jobs(self):
        cases = [(dict(error={"code": 3, "message": "bad prompt"}), P.Failure.REJECTED),
                 (dict(error={"code": 13, "message": "internal"}), P.Failure.UNAVAILABLE),
                 (dict(response=SimpleNamespace(generated_videos=[], rai_media_filtered_count=1)), P.Failure.REJECTED),
                 (dict(response=SimpleNamespace(generated_videos=None, rai_media_filtered_count=0)), P.Failure.INVALID_OUTPUT),
                 (dict(download=b""), P.Failure.INVALID_OUTPUT),
                 (dict(submit_error=ApiError(429, "quota")), P.Failure.RATE_LIMITED),
                 (dict(submit_error=ApiError(403, "key")), P.Failure.AUTH)]
        for kwargs, category in cases:
            with self.assertRaises(P.ProviderError, msg=kwargs) as caught:
                self.generate(self.client(**kwargs))
            self.assertEqual(caught.exception.category, category, kwargs)


class LtxAndManualTest(unittest.TestCase):
    def test_ltx_needs_a_loaded_pipeline(self):
        self.assertEqual(P.LtxProvider(env={}, pipeline=lambda: "MANUAL").configuration()[0], "not_configured")
        self.assertEqual(P.LtxProvider(env={}, pipeline=lambda: object()).configuration()[0], "available")
        with self.assertRaises(P.ProviderError):
            P.LtxProvider(env={}, pipeline=lambda: object()).check(request("video", "x", duration_seconds=5))  # fixed length

    def test_ltx_failures_are_not_retried(self):
        def broken(**kw):
            raise RuntimeError("CUDA out of memory")
        provider = P.LtxProvider(env={}, pipeline=lambda: broken)
        req = request("video", "x")
        with mock.patch.dict("sys.modules", {"torch": SimpleNamespace(Generator=lambda: SimpleNamespace(manual_seed=lambda s: None)),
                                             "diffusers": SimpleNamespace(), "diffusers.utils": SimpleNamespace(export_to_video=None)}):
            with self.assertRaises(P.ProviderError) as caught:
                provider.produce(req, provider.settings_for(req), os.path.join(TMP, "ltx.mp4"))
        self.assertEqual((caught.exception.category, caught.exception.retryable, caught.exception.fallback_ok),
                         (P.Failure.UNAVAILABLE, False, True))

    def test_manual_cannot_regenerate(self):
        with self.assertRaises(P.ProviderError):
            P.ManualProvider(env={}).check(request("video", "x", force_regenerate=True))


class RegistryTest(unittest.TestCase):
    def registry(self, env=None, mode="veo", fake=False):
        return P.ProviderRegistry(env=env if env is not None else {"GEMINI_API_KEY": "test-key-not-real"}, video_mode=lambda: mode,
                                  ltx_pipeline=lambda: object(), fake=fake)

    def test_preference_and_fallback_order(self):
        registry = self.registry(env={"GEMINI_API_KEY": "k", "VEO_ENABLED": "1"}, mode="ltx")
        self.assertEqual(registry.order("video"), ["ltx", "veo"])
        self.assertEqual(registry.order("image"), ["pollinations", "gemini-image"])
        chosen = registry.select(request("video", "x"))
        self.assertEqual([(c.name, c.usable) for c in chosen.candidates], [("ltx", True), ("veo", True)])
        self.assertEqual(self.registry(env={"AI_IMAGE_PROVIDER": "gemini-image"}).order("image")[0], "gemini-image")
        self.assertEqual(self.registry(env={"AI_VIDEO_PROVIDERS": "ltx"}, mode="veo").order("video"), ["veo", "ltx"])

    def test_the_manual_workflow_never_falls_back(self):
        registry = self.registry(env={"GEMINI_API_KEY": "k", "VEO_ENABLED": "1"}, mode="manual")
        self.assertEqual(registry.order("video"), ["manual"])

    def test_explicit_choices_are_not_silently_replaced(self):
        registry = self.registry(env={}, mode="veo")  # no key: veo is not configured
        explicit = registry.select(request("video", "x", provider="veo"))
        self.assertEqual([c.name for c in explicit.candidates], ["veo"])
        self.assertFalse(explicit.fallback)
        self.assertFalse(explicit.candidates[0].usable)
        allowed = registry.select(request("video", "x", provider="veo", allow_fallback=True))
        self.assertEqual([c.name for c in allowed.candidates], ["veo", "ltx"])
        unknown = registry.select(request("image", "x", provider="nope"))
        self.assertEqual((unknown.candidates[0].supported, unknown.candidates[0].reason), (False, "no such provider on this server"))

    def test_fallback_can_be_switched_off(self):
        registry = self.registry(env={"AI_PROVIDER_FALLBACK": "0", "GEMINI_IMAGE_ENABLED": "1", "GEMINI_API_KEY": "k"})
        self.assertEqual([c.name for c in registry.select(request()).candidates], ["pollinations"])

    def test_capabilities_decide_which_provider_can_serve(self):
        registry = self.registry(env={"GEMINI_API_KEY": "k", "VEO_ENABLED": "1"}, mode="ltx")
        chosen = registry.select(request("video", "x", duration_seconds=6))  # LTX makes fixed-length clips
        self.assertEqual([(c.name, c.supported, c.usable) for c in chosen.candidates], [("ltx", False, False), ("veo", True, True)])
        self.assertIn("fixed length", chosen.candidates[0].reason)

    def test_recent_failures_move_a_provider_back_but_never_exclude_it(self):
        registry = self.registry(env={"GEMINI_API_KEY": "k", "VEO_ENABLED": "1", "AI_PROVIDER_COOLDOWN": "60"}, mode="ltx")
        registry.note_failure("ltx", P.ProviderError(P.Failure.UNAVAILABLE, "down"))
        chosen = registry.select(request("video", "x"))
        self.assertEqual([c.name for c in chosen.usable], ["veo", "ltx"])  # ltx still usable, after veo
        self.assertEqual(registry.state("ltx")[0], "temporarily_unavailable")
        registry.note_failure("pollinations", P.ProviderError(P.Failure.RATE_LIMITED, "429", retry_after=30))
        only = registry.select(request())
        self.assertEqual([c.name for c in only.usable], ["pollinations"])  # the only image provider is still tried
        registry.note_success("ltx")
        self.assertEqual(registry.state("ltx")[0], "available")
        registry.note_failure("veo", P.ProviderError(P.Failure.REJECTED, "no"))
        self.assertEqual(registry.state("veo")[0], "available")  # a refused prompt says nothing about availability
        # A provider that now asks for payment (as Pollinations' free image endpoint did in September 2026)
        registry.note_failure("pollinations", P.error_for_status(402))
        state, reason = registry.state("pollinations")
        self.assertEqual(state, "temporarily_unavailable")
        self.assertIn("payment or credentials refused", reason)

    def test_concurrency_limit(self):
        registry = self.registry(env={"POLLINATIONS_MAX_CONCURRENT": "1"})

        async def scenario():
            await registry.slot("pollinations")
            with self.assertRaises(P.ProviderError) as busy:
                await registry.slot("pollinations", wait_seconds=0.2)
            registry.release("pollinations")
            await registry.slot("pollinations", wait_seconds=0.2)
            registry.release("pollinations")
            return busy.exception
        self.assertEqual(run(scenario()).category, P.Failure.UNAVAILABLE)

    def test_status_is_safe_and_calls_nothing(self):
        secret = "AIzaSyREALLOOKINGSECRET1234567890abc"
        registry = self.registry(env={"GEMINI_API_KEY": secret, "GEMINI_IMAGE_ENABLED": "1"})
        with no_network():
            status = registry.status()
        text = json.dumps(status)
        self.assertNotIn(secret, text)
        states = {p["name"]: p["state"] for p in status["providers"]}
        self.assertEqual(states, {"pollinations": "available", "gemini-image": "available", "veo": "available",
                                  "ltx": "available", "manual": "available"})
        self.assertEqual(status["preferred"], {"image": "pollinations", "video": "veo"})

    def test_test_mode_registers_only_the_stand_ins(self):
        registry = self.registry(fake=True, mode="fake")
        self.assertEqual(registry.names(), ["fake", "fake-alt", "fake-presenter"])  # Phase 12 added the presenter stand-in
        self.assertEqual(registry.order("video"), ["fake", "fake-alt"])
        self.assertEqual(registry.order("presenter"), ["fake-presenter"])

    def test_every_adapter_has_settings_for_what_it_makes(self):
        registry = self.registry(fake=False)
        for name in registry.names():
            for media_type in registry.get(name).capabilities().media_types:
                self.assertIn((media_type, name), ai_cache.PROVIDER_SETTINGS)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class FakeProviderTest(unittest.TestCase):
    def test_stand_in_behaves_like_a_job_provider_and_fails_on_demand(self):
        provider = P.FakeProvider("fake", "stand-in", env={"FAKE_POLL_SECONDS": "0.01", "FAKE_TIMEOUT": "0.3",
                                                            "AI_FAKE_FAIL": "fake:image:rate_limited"})
        video = request("video", "x")
        progress = []
        dest = tempfile.mktemp(suffix=".mp4", dir=TMP)
        result = run(provider.generate(video, provider.settings_for(video), dest, progress=lambda s, j: progress.append(j)))
        self.assertEqual((result.status, result.provider_job_id), (P.COMPLETED, progress[0]))
        with self.assertRaises(P.ProviderError) as limited:
            image = request()
            run(provider.generate(image, provider.settings_for(image), tempfile.mktemp(suffix=".jpg", dir=TMP)))
        self.assertEqual((limited.exception.category, limited.exception.retry_after), (P.Failure.RATE_LIMITED, 0.2))
        slow = P.FakeProvider("fake", "stand-in", env={"FAKE_POLL_SECONDS": "0.01", "FAKE_TIMEOUT": "0.2", "AI_FAKE_FAIL": "fake:video:slow"})
        with self.assertRaises(P.ProviderError) as timed_out:
            run(slow.generate(video, slow.settings_for(video), tempfile.mktemp(suffix=".mp4", dir=TMP)))
        self.assertEqual((timed_out.exception.category, getattr(slow, "cancelled", 0)), (P.Failure.TIMEOUT, 1))


if __name__ == "__main__":
    unittest.main()
