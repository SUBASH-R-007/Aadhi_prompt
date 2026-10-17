"""``scripts/b3_spike.py``, the owner-run B3-0 spike (B3 plan §A B3-0 task 6, owner decision O0), offline only.

The real run is the owner's (paid, his keys); nothing here sends a request. Checked: the dry run makes no request at
all (sockets and HTTP refused) and needs no key; keys are never written or printed (only "present"/"absent"); the
run refuses without the key its chosen items need (and only then) or past ``--max-usd``, before any request; facts the
run does not measure are labelled as such; and a whole run against a FAKE server
(an ``httpx`` mock transport; the Realtime websocket probe stubbed) completes every item, writes samples and a report,
and redacts a key the server echoes back.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import math
import re
import socket
import struct
import sys
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from aadhi.config import ROOT_DIR

OPENAI_KEY = "sk-test-NEVER-WRITE-THIS-0123456789abcdef"
GEMINI_KEY = "AIza-test-NEVER-WRITE-THIS-0123456789"


def load_spike() -> Any:
    spec = importlib.util.spec_from_file_location("b3_spike_under_test", ROOT_DIR / "scripts" / "b3_spike.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up by name
    spec.loader.exec_module(module)  # guarded by __main__: nothing runs
    return module


@pytest.fixture()
def spike():
    return load_spike()


@pytest.fixture()
def no_network(monkeypatch):
    """Any socket connection or HTTP send fails the test."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the spike tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)


def files_text(folder: Path) -> str:
    return "\n".join(p.read_bytes().decode("latin-1") for p in folder.rglob("*") if p.is_file())


def test_the_dry_run_makes_no_request_and_needs_no_key(spike, no_network, tmp_path, capsys) -> None:
    out = tmp_path / "spike"
    assert spike.main(["--dry-run", "--out", str(out)], env={}) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["dry_run"] is True and report["requests"] == 0 and report["spent_estimate_usd"] == 0
    assert report["keys"] == {"OPENAI_API_KEY": "absent", "GEMINI_API_KEY": "absent"}
    assert {p["item"] for p in report["plan"]} == set(spike.ITEMS)  # every item a..j is planned
    assert 0 < report["estimate_usd"] <= 1.0  # the plan's "about $1" cap
    assert not (out / "samples").exists()
    st = report["self_test"]
    assert st["sine_997hz_0dbfs_lufs"] == pytest.approx(-3.01, abs=0.1)  # BS.1770's reference reading
    assert st["tone_pair"]["speech_span_s"] == pytest.approx(1.3, abs=0.02)
    assert st["tone_pair"]["pause_s"] == pytest.approx(0.3, abs=0.02) and st["tone_f0_hz"] == pytest.approx(200, abs=2)
    assert "no request" in capsys.readouterr().out


def test_keys_are_never_written_or_printed(spike, no_network, tmp_path, capsys) -> None:
    out = tmp_path / "spike"
    assert spike.main(["--dry-run", "--out", str(out)], env={"OPENAI_API_KEY": OPENAI_KEY,
                                                             "GEMINI_API_KEY": GEMINI_KEY}) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["keys"] == {"OPENAI_API_KEY": "present", "GEMINI_API_KEY": "present"}
    printed = capsys.readouterr()
    for secret in (OPENAI_KEY, GEMINI_KEY, OPENAI_KEY[:12], GEMINI_KEY[:12]):
        assert secret not in files_text(out) and secret not in printed.out + printed.err


def test_the_default_output_folder_is_outside_the_repository(spike) -> None:
    assert spike.DEFAULT_OUT == "C:/t/b3_spike"
    assert not Path(spike.DEFAULT_OUT).resolve().is_relative_to(ROOT_DIR.resolve())


def test_no_key_or_over_the_cap_runs_nothing(spike, no_network, tmp_path) -> None:
    assert spike.main(["--out", str(tmp_path / "a")], env={}) == 2
    assert spike.main(["--out", str(tmp_path / "b"), "--max-usd", "0.01"], env={"OPENAI_API_KEY": OPENAI_KEY}) == 3
    assert not (tmp_path / "a" / "samples").exists() and not (tmp_path / "b" / "samples").exists()


def test_redaction(spike) -> None:
    secrets = spike.Secrets({"OPENAI_API_KEY": OPENAI_KEY, "GEMINI_API_KEY": GEMINI_KEY})
    text = f"Incorrect API key provided: {OPENAI_KEY}; header x-goog-api-key={GEMINI_KEY}; prefix {OPENAI_KEY[:14]}"
    clean = secrets.redact(text)
    assert OPENAI_KEY not in clean and GEMINI_KEY not in clean and OPENAI_KEY[:12] not in clean
    assert clean.count("<redacted>") >= 3


def test_wav_parsing_accepts_a_streamed_header(spike) -> None:
    x = [0.5 * math.sin(2 * math.pi * 220 * i / 24000) for i in range(2400)]
    wav = spike.to_wav(__import__("numpy").array(x))
    streamed = wav[:40] + struct.pack("<I", 0xFFFFFFFF) + wav[44:]  # the data size an HTTP stream cannot know
    for data in (wav, streamed):
        y, rate = spike.parse_wav(data)
        assert rate == 24000 and len(y) == 2400 and abs(float(y[60]) - x[60]) < 1e-3


# --- a whole run against a fake server ----------------------------------------------------------------------------


def speech_wav(text: str, speed: float = 1.0) -> bytes:
    """A deterministic 'voice': a 0.22 s tone per word, 0.06 s gaps, 0.35 s after each sentence, 0.2 s lead/tail."""
    import numpy as np

    rate = 24000
    parts: list[Any] = []

    def silence(sec: float) -> None:
        parts.append(np.zeros(int(sec * rate / speed)))

    def tone(sec: float) -> None:
        parts.append(0.18 * np.sin(2 * math.pi * 180 * np.arange(int(sec * rate / speed)) / rate))

    silence(0.2)
    for word in text.split():
        tone(0.22)
        silence(0.35 if word.endswith((".", "?", "!")) else 0.06)
    silence(0.2)
    pcm = (np.concatenate(parts) * 32767).round().astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class FakeServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content or b"{}")
        self.calls.append((url, body))
        if url.endswith("/v1/audio/speech"):
            if request.headers.get("authorization") != f"Bearer {OPENAI_KEY}":
                return httpx.Response(401, text="no key")
            if body["voice"] == "ballad":  # a refused voice; the error echoes the key, which must be redacted
                return httpx.Response(400, text=f"voice not supported for key {OPENAI_KEY}")
            return httpx.Response(200, content=speech_wav(body["input"], body.get("speed") or 1.0))
        if url.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini-tts"}, {"id": "gpt-realtime-2.1-mini"},
                                                      {"id": "gpt-5"}]})
        if ":generateContent" in url:
            wav = speech_wav(body["contents"][0]["parts"][0]["text"].split(": ", 1)[1])
            pcm = wav[44:]
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]})
        if "/v1beta/models" in url:
            return httpx.Response(200, json={"models": [{"name": "models/gemini-3.8-flash-tts"}]})
        return httpx.Response(404, text="unknown")


def test_a_whole_run_against_a_fake_server(spike, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(spike, "realtime_probe", lambda run, a: {"skipped": "stubbed in the offline test"})
    monkeypatch.setattr(spike.time, "sleep", lambda s: None)
    server = FakeServer()
    out = tmp_path / "spike"
    rc = spike.main(["--out", str(out)], env={"OPENAI_API_KEY": OPENAI_KEY, "GEMINI_API_KEY": GEMINI_KEY},
                    transport=httpx.MockTransport(server))
    assert rc == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    results = report["results"]
    assert set(results) == set(spike.ITEMS)
    assert not [k for k, v in results.items() if isinstance(v, dict) and ("error" in v or "stopped" in v)], results
    a = results["a"]
    assert a["voices"]["ballad"]["accepted"] is False and "ballad" not in a["accepted"] and len(a["accepted"]) == 12
    assert a["voices"]["nova"]["wpm"] > 100 and a["voices"]["nova"]["lufs"] is not None
    assert set(results["c"]["targets"]) == {"140", "155", "170"} and results["c"]["targets"]["155"]["spread_pm"] == 0
    assert set(results["d"]["rows"]) == set(spike.STYLES)
    e = results["e"]["voices"]["nova"]
    assert e["per_sentence"]["requests"] == 4 and e["per_beat"]["silence_boundaries"] is not None
    assert len(e["per_beat"]["silence_boundaries"]) == 4
    assert set(results["f"]["voices"]) == set(a["accepted"])
    assert results["g"]["lufs_spread"] == 0 and results["h"]["bytes_identical"] is True
    assert set(results["i"]["voices"]) == set(spike.GEMINI_VOICES)
    assert results["j"]["openai"]["models"] == ["gpt-4o-mini-tts", "gpt-realtime-2.1-mini"]
    speeds = results["b"]["voices"]["nova"]
    assert speeds["1.10"]["vs_1.00"]["span_ratio"] == pytest.approx(1 / 1.1, abs=0.03)
    # instructions only where an item sends them; the plain requests are exactly model/voice/input/format
    plain = [b for u, b in server.calls if u.endswith("/audio/speech") and "instructions" not in b and "speed" not in b]
    assert plain and all(set(b) == {"model", "voice", "input", "response_format"} for b in plain)
    assert all(b["model"] == "gpt-4o-mini-tts" and b["response_format"] == "wav"
               for u, b in server.calls if u.endswith("/audio/speech"))
    samples = list((out / "samples").glob("*.wav"))
    assert len(samples) > 60
    assert 0 < report["spent_estimate_usd"] <= 1.0
    printed = capsys.readouterr()
    for secret in (OPENAI_KEY, GEMINI_KEY, OPENAI_KEY[:12]):
        assert secret not in files_text(out) and secret not in printed.out + printed.err
    assert re.search(r"<redacted>", json.dumps(a["voices"]["ballad"]))


def test_a_key_is_required_only_by_the_items_that_need_it(spike, tmp_path, monkeypatch) -> None:
    """``--only i`` runs on the Gemini key alone (no OpenAI request); ``--only j`` runs its Gemini part and records the
    OpenAI part as skipped; an item whose key is missing, or a run that could make no request, refuses up front."""
    monkeypatch.setattr(spike.time, "sleep", lambda s: None)

    def no_realtime(run: Any, a: Any) -> Any:
        raise AssertionError("the OpenAI Realtime probe ran without OPENAI_API_KEY")

    monkeypatch.setattr(spike, "realtime_probe", no_realtime)
    gemini_only = {"GEMINI_API_KEY": GEMINI_KEY}
    server = FakeServer()
    assert spike.main(["--only", "i", "--out", str(tmp_path / "i")], env=gemini_only,
                      transport=httpx.MockTransport(server)) == 0
    report = json.loads((tmp_path / "i" / "report.json").read_text(encoding="utf-8"))
    assert set(report["results"]["i"]["voices"]) == set(spike.GEMINI_VOICES)
    assert {p["provider"] for p in report["plan"]} == {"gemini"}
    assert server.calls and not [u for u, _ in server.calls if "openai.com" in u]

    server = FakeServer()
    assert spike.main(["--only", "j", "--out", str(tmp_path / "j")], env=gemini_only,
                      transport=httpx.MockTransport(server)) == 0
    j = json.loads((tmp_path / "j" / "report.json").read_text(encoding="utf-8"))["results"]["j"]
    assert j["openai"] == {"skipped": "OPENAI_API_KEY is not set"}
    assert j["gemini"]["models"] == ["models/gemini-3.8-flash-tts"]
    assert not [u for u, _ in server.calls if "openai.com" in u]

    for only, env in (("a", gemini_only), ("i,a", gemini_only), ("i", {}), ("j", {}), ("i", {"OPENAI_API_KEY": OPENAI_KEY})):
        out = tmp_path / f"refused-{only.replace(',', '')}-{len(env)}"
        assert spike.main(["--only", only, "--out", str(out)], env=env, transport=httpx.MockTransport(FakeServer())) == 2
        assert not (out / "samples").exists()


def test_documented_gemini_facts_are_never_reported_as_measured(spike, tmp_path, monkeypatch) -> None:
    """Item j's Gemini facts that the run does not measure (voices offered, word timings) are labelled as taken from
    the documentation, under ``assumed_not_measured``, never beside the measured model list."""
    monkeypatch.setattr(spike.time, "sleep", lambda s: None)
    monkeypatch.setattr(spike, "realtime_probe", lambda run, a: {"skipped": "stubbed in the offline test"})
    out = tmp_path / "spike"
    assert spike.main(["--only", "j", "--out", str(out)], env={"OPENAI_API_KEY": OPENAI_KEY,
                                                                "GEMINI_API_KEY": GEMINI_KEY},
                      transport=httpx.MockTransport(FakeServer())) == 0
    gemini = json.loads((out / "report.json").read_text(encoding="utf-8"))["results"]["j"]["gemini"]
    assert set(gemini) == {"models", "models_request", "samples", "assumed_not_measured"}
    assert {"voices_offered", "word_timings"} <= set(gemini["assumed_not_measured"])
    assert "not measured" in gemini["assumed_not_measured"]["source"]
