"""Process-wide logging: structured single-line records with secret redaction.

``configure_logging(settings)`` installs ONE stderr handler on the root logger (idempotent):

* production: JSON lines ``{"ts", "level", "logger", "msg", "exc"?, ...extra}``;
* development/test: ``<ts> level=INFO logger=aadhi.x msg="..."`` key=value lines;
* ``RedactingFilter`` replaces every configured secret (``Settings.secret_values()``, plus API keys
  saved in the Studio as they are decrypted: ``aadhi.security.redaction``) and
  ``key=/token=/secret=/password=`` values in messages, tracebacks, stack info and ``extra=``
  fields; an extra whose *name* is credential-like (``api_key``, ``token``, ``password``, ...) is
  replaced entirely.

Uvicorn's loggers are routed through the same handler.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sys
from collections.abc import Iterable
from typing import Any

from .config import Settings
from .security.redaction import registered_secrets

__all__ = ["REDACTED", "RedactingFilter", "SingleLineFormatter", "configure_logging", "extra_fields"]

_KEY_VALUE = re.compile(r"(?i)\b(api[_-]?key|key|token|secret|password|passwd|authorization)=([^&\s\"']+)")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {"message", "asctime"}
_SENSITIVE_NAME = re.compile(  # extra names whose value is a credential (``asset_key`` etc. are not)
    r"(?i)^key$|(?:^|_)(?:api_?key|secret_?key|access_?key|private_?key|token|secret|password|passwd"
    r"|authorization|cookie|credentials?)(?:$|_)"
)
REDACTED = "[REDACTED]"
_MARK = "_aadhi_handler"


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The ``extra=`` attributes of a record (non-standard, non-private)."""
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and not k.startswith("_")}


class RedactingFilter(logging.Filter):
    """Removes secret values from log records (message, args, exception text, stack info, extras)."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        """Return ``text`` with secrets and key-like values replaced by ``[REDACTED]``."""
        if not text:
            return text
        for secret in (*registered_secrets(), *self.secrets):  # registered: decrypted keys saved in the Studio
            if secret in text:
                text = text.replace(secret, REDACTED)
        text = _KEY_VALUE.sub(r"\1=[REDACTED]", text)
        return _BEARER.sub(r"\1 [REDACTED]", text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError, KeyError):  # bad format args: log the raw template instead
            message = str(record.msg)
        record.msg = self.redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redact(record.stack_info)
        for key, value in extra_fields(record).items():
            redacted = self._redact_extra(key, value)
            if redacted is not value:
                setattr(record, key, redacted)
        return True

    def _redact_extra(self, key: str, value: Any) -> Any:
        """``value`` itself when it is clean, else its redacted text (credential-like names: fully)."""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if _SENSITIVE_NAME.search(key):
            return REDACTED
        if isinstance(value, str):
            clean = self.redact(value)
            return value if clean == value else clean
        try:
            texts = {repr(value), str(value)}
        except Exception:  # noqa: BLE001 - a broken __repr__ must not break logging
            return f"<unprintable {type(value).__name__}>"
        if all(self.redact(t) == t for t in texts):
            return value
        return self.redact(repr(value))


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace('"', '\\"')


class SingleLineFormatter(logging.Formatter):
    """One record per line (JSON or key=value); multi-line text is escaped."""

    def __init__(self, *, json_lines: bool) -> None:
        super().__init__()
        self.json_lines = json_lines

    def format(self, record: logging.LogRecord) -> str:
        ts = dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc).isoformat(timespec="milliseconds")
        message = record.getMessage()
        exc = record.exc_text or (self.formatException(record.exc_info) if record.exc_info else "")
        if record.stack_info:
            exc = f"{exc}\n{record.stack_info}".strip()
        extra = extra_fields(record)
        if self.json_lines:
            payload: dict[str, Any] = {"ts": ts, "level": record.levelname, "logger": record.name, "msg": message}
            if exc:
                payload["exc"] = exc
            for k, v in extra.items():
                payload[k] = v if isinstance(v, (str, int, float, bool)) or v is None else repr(v)
            return json.dumps(payload, ensure_ascii=False)
        parts = [ts, f"level={record.levelname}", f"logger={record.name}", f'msg="{_escape(message)}"']
        parts.extend(f'{k}="{_escape(str(v))}"' for k, v in extra.items())
        if exc:
            parts.append(f'exc="{_escape(exc)}"')
        return " ".join(parts)


def configure_logging(settings: Settings) -> logging.Handler:
    """Install the redacting single-line handler on the root logger (replaces a previous one)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _MARK, False):
            root.removeHandler(h)
            h.close()
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _MARK, True)
    handler.setFormatter(SingleLineFormatter(json_lines=settings.is_production))
    handler.addFilter(RedactingFilter(settings.secret_values()))
    root.addHandler(handler)
    level = logging.getLevelName(str(settings.log_level).upper())
    root.setLevel(level if isinstance(level, int) else logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    for noisy in ("botocore", "boto3", "s3transfer", "urllib3", "httpx", "httpcore", "PIL", "multipart"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
    return handler
