"""Structured single-line logging with secret redaction."""

from __future__ import annotations

import io
import json
import logging

import pytest
from pydantic import SecretStr

from aadhi.logging_setup import RedactingFilter, SingleLineFormatter, configure_logging

SECRET = "sk-live-0123456789abcdef"


def _record(msg: str, *args, exc_info=None, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("aadhi.test", logging.WARNING, __file__, 1, msg, args, exc_info)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_redacting_filter_message_args_and_patterns():
    f = RedactingFilter([SECRET, "short"])
    rec = _record("calling %s with key=%s and Bearer abcdefghijklmnop", "https://api", SECRET)
    assert f.filter(rec) is True
    text = rec.getMessage()
    assert SECRET not in text and "key=[REDACTED]" in text and "Bearer [REDACTED]" in text
    assert "short" in f.redact("short")  # values < 6 chars are not treated as secrets


def test_redacting_filter_exceptions():
    f = RedactingFilter([SECRET])
    try:
        raise RuntimeError(f"provider said {SECRET}")
    except RuntimeError:
        import sys

        rec = _record("boom", exc_info=sys.exc_info())
    f.filter(rec)
    assert SECRET not in rec.exc_text and "[REDACTED]" in rec.exc_text
    line = SingleLineFormatter(json_lines=False).format(rec)
    assert "\n" not in line and SECRET not in line and "RuntimeError" in line


def test_bad_format_args_do_not_crash():
    rec = _record("value %d", "not-a-number")
    RedactingFilter([]).filter(rec)
    assert rec.getMessage() == "value %d"


def test_formatter_modes():
    rec = _record('multi\nline "quoted"', job_id=12)
    kv = SingleLineFormatter(json_lines=False).format(rec)
    assert "level=WARNING" in kv and 'msg="multi\\nline \\"quoted\\""' in kv and 'job_id="12"' in kv
    js = json.loads(SingleLineFormatter(json_lines=True).format(rec))
    assert js["msg"] == 'multi\nline "quoted"' and js["job_id"] == 12 and js["logger"] == "aadhi.test"


@pytest.fixture()
def restore_root():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        if h not in handlers:
            root.removeHandler(h)
    root.setLevel(level)


def test_configure_logging_idempotent_and_redacts(app_env, restore_root):
    settings = app_env.model_copy(update={"openai_api_key": SecretStr(SECRET), "log_level": "info"})
    h1 = configure_logging(settings)
    h2 = configure_logging(settings)
    root = logging.getLogger()
    assert h1 not in root.handlers and h2 in root.handlers
    assert root.level == logging.INFO
    stream = io.StringIO()
    h2.setStream(stream)
    logging.getLogger("aadhi.provider").warning("request failed for %s", SECRET)
    out = stream.getvalue()
    assert SECRET not in out and "[REDACTED]" in out and out.count("\n") == 1
    assert logging.getLogger("uvicorn.access").propagate is True


def test_production_uses_json(app_env, restore_root):
    handler = configure_logging(app_env.model_copy(update={"app_env": "production"}))
    stream = io.StringIO()
    handler.setStream(stream)
    logging.getLogger("aadhi.x").error("hello")
    assert json.loads(stream.getvalue())["msg"] == "hello"


class _Opaque:
    def __repr__(self) -> str:
        return f"<Opaque token={SECRET}>"


class _Broken:
    def __repr__(self) -> str:
        raise RuntimeError("no repr")


def test_extra_fields_are_redacted():
    """Secrets passed through ``extra=`` never reach the output, in either line format."""
    f = RedactingFilter([SECRET])
    rec = _record(
        "provider call failed",
        error=f"401 invalid key {SECRET}",
        url="https://api.example?key=abc123def456&x=1",
        body={"detail": f"bad {SECRET}"},
        obj=_Opaque(),
        api_key="not-configured-but-named-like-a-key",
        render_token="eyJhbGciOiJIUzI1NiJ9.payload.sig",
        asset_key="figure-abc123",
        clean={"n": 1},
        job_id=7,
        broken=_Broken(),
    )
    f.filter(rec)
    for fmt in (SingleLineFormatter(json_lines=True), SingleLineFormatter(json_lines=False)):
        line = fmt.format(rec)
        assert SECRET not in line and "abc123def456" not in line, line
        assert "not-configured-but-named-like-a-key" not in line and "eyJhbGciOiJIUzI1NiJ9" not in line
        assert "figure-abc123" in line  # ordinary identifiers stay readable
    payload = json.loads(SingleLineFormatter(json_lines=True).format(rec))
    assert payload["error"] == "401 invalid key [REDACTED]"
    assert payload["url"] == "https://api.example?key=[REDACTED]&x=1"
    assert payload["api_key"] == payload["render_token"] == "[REDACTED]"
    assert "[REDACTED]" in payload["body"] and "[REDACTED]" in payload["obj"]
    assert payload["job_id"] == 7 and payload["broken"] == "<unprintable _Broken>"
    assert rec.clean == {"n": 1}  # clean non-scalar extras are left untouched for other handlers


def test_configured_handler_redacts_extras(monkeypatch):
    from aadhi.config import Settings

    settings = Settings(app_env="development", gemini_api_key=SecretStr(SECRET))
    handler = configure_logging(settings)
    stream = io.StringIO()
    monkeypatch.setattr(handler, "stream", stream)
    try:
        logging.getLogger("aadhi.extra").warning("upstream", extra={"error": f"401 {SECRET}"})
    finally:
        logging.getLogger().removeHandler(handler)
    out = stream.getvalue()
    assert "upstream" in out and SECRET not in out and "[REDACTED]" in out
