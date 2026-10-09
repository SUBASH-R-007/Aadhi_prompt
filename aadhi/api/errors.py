"""Error envelope: every error response is ``{"detail": ..., "code": "...", **extra}``.

* :class:`ApiException` is what routers raise.
* HTTPExceptions raised by other areas (e.g. ``aadhi.auth.deps``) are mapped too; a ``code``
  attribute (core's ``AppHTTPException``), an ``extra`` dict attribute or a dict ``detail`` with a
  ``code`` key are honoured, otherwise the code is derived from the status.
* Domain exceptions (``JobInProgress``, ``BudgetExceeded``, ``UploadRejected``, ``InvalidToken``)
  are translated to their documented status codes.
* Anything unexpected becomes a generic 500 without internals (logged with redaction).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import get_settings

logger = logging.getLogger(__name__)

STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    410: "gone",
    411: "length_required",
    413: "too_large",
    415: "unsupported_type",
    416: "range_not_satisfiable",
    422: "validation",
    429: "rate_limited",
    500: "internal",
    503: "unavailable",
}

GENERIC_500 = "Internal server error. The problem has been logged."


def code_for_status(status: int) -> str:
    """Default machine code for an HTTP status."""
    return STATUS_CODES.get(status, "error")


class ApiException(StarletteHTTPException):
    """HTTP error rendered as the API envelope.

    ``ApiException(409, "revision_conflict", "Screenplay changed", current_revision=5)`` renders as
    ``{"detail": "Screenplay changed", "code": "revision_conflict", "current_revision": 5}``.
    ``headers`` (keyword) are sent with the response (e.g. ``Retry-After``).
    """

    def __init__(
        self, status: int, code: str, detail: Any, *, headers: Mapping[str, str] | None = None, **extra: Any
    ) -> None:
        super().__init__(status_code=status, detail=detail, headers=dict(headers) if headers else None)
        self.code = code
        self.extra = extra


def not_found(what: str = "Resource") -> ApiException:
    """404 with the standard envelope (never reveals whether the object exists)."""
    return ApiException(404, "not_found", f"{what} not found")


def validation_error(message: str, loc: list[Any] | None = None, *, type_: str = "value_error") -> ApiException:
    """422 shaped like a pydantic error list so clients render it uniformly."""
    return ApiException(422, "validation", [{"loc": loc or ["body"], "msg": message, "type": type_}])


def seconds_until_utc_midnight(now: dt.datetime | None = None) -> int:
    """Retry-After for daily budgets."""
    now = now or dt.datetime.now(dt.timezone.utc)
    tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def envelope(
    status: int, code: str, detail: Any, *, headers: Mapping[str, str] | None = None, **extra: Any
) -> JSONResponse:
    """Build the JSON error response."""
    body: dict[str, Any] = {"detail": detail, "code": code}
    for key, value in extra.items():
        if key not in body:
            body[key] = value
    return JSONResponse(jsonable_encoder(body), status_code=status, headers=dict(headers) if headers else None)


def _redact(text: str) -> str:
    try:
        return get_settings().redact(text)
    except Exception:  # noqa: BLE001  # pragma: no cover - never fail while rendering an error
        return text


def clean_validation_errors(errors: Any) -> list[dict[str, Any]]:
    """Pydantic errors without echoed input (could be a whole screenplay) or non-JSON ctx."""
    out: list[dict[str, Any]] = []
    for err in errors or []:
        if not isinstance(err, Mapping):
            out.append({"loc": ["body"], "msg": str(err), "type": "value_error"})
            continue
        item: dict[str, Any] = {
            "loc": list(err.get("loc", ())),
            "msg": str(err.get("msg", "")),
            "type": str(err.get("type", "value_error")),
        }
        ctx = err.get("ctx")
        if isinstance(ctx, Mapping):
            item["ctx"] = {
                str(k): (v if isinstance(v, (int, float, str, bool)) or v is None else str(v)) for k, v in ctx.items()
            }
        out.append(item)
    return out


def http_exception_parts(exc: StarletteHTTPException) -> tuple[str, Any, dict[str, Any]]:
    """(code, detail, extra) for any HTTPException flavour."""
    detail: Any = exc.detail
    extra: dict[str, Any] = {}
    code = getattr(exc, "code", None)
    raw_extra = getattr(exc, "extra", None)
    if isinstance(raw_extra, Mapping):
        extra.update(raw_extra)
    if isinstance(detail, Mapping) and ("code" in detail or "message" in detail or "detail" in detail):
        d = dict(detail)
        code = code or d.pop("code", None)
        message = d.pop("detail", None)
        if message is None:
            message = d.pop("message", None)
        detail = message
        extra.update(d)
    if not isinstance(code, str) or not code:
        code = code_for_status(exc.status_code)
    if detail is None:
        detail = code
    return code, detail, extra


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code, detail, extra = http_exception_parts(exc)
    return envelope(exc.status_code, code, detail, headers=exc.headers, **extra)


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return envelope(422, "validation", clean_validation_errors(exc.errors()))


async def _job_in_progress_handler(request: Request, exc: Exception) -> JSONResponse:
    job_id = getattr(exc, "job_id", None)
    return envelope(409, "job_in_progress", "Another job is already running for this version.", job_id=job_id)


async def _budget_handler(request: Request, exc: Exception) -> JSONResponse:
    retry = seconds_until_utc_midnight()
    message = _redact(str(exc)) or "Budget exhausted."
    return envelope(429, "budget", message, headers={"Retry-After": str(retry)})


async def _upload_rejected_handler(request: Request, exc: Exception) -> JSONResponse:
    status = int(getattr(exc, "status_code", 415) or 415)
    code = str(getattr(exc, "code", "") or code_for_status(status))
    message = str(getattr(exc, "message", "") or exc) or "Upload rejected."
    return envelope(status, code, message)


async def _invalid_token_handler(request: Request, exc: Exception) -> JSONResponse:
    return envelope(401, "unauthenticated", "Invalid or expired token.")


async def _integrity_handler(request: Request, exc: Exception) -> JSONResponse:
    """A unique/foreign-key violation from a concurrent change (details only in the log)."""
    logger.warning(
        "integrity error on %s %s: %s", request.method, request.url.path, type(getattr(exc, "orig", exc)).__name__
    )
    return envelope(409, "conflict", "The request conflicts with a concurrent change; please retry.")


async def _invalid_job_state_handler(request: Request, exc: Exception) -> JSONResponse:
    return envelope(409, "conflict", _redact(str(exc)) or "The job is not in a state that allows this.")


async def _job_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return envelope(404, "not_found", "Job not found")


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort (ServerErrorMiddleware): generic message, details only in the log."""
    return envelope(500, "internal", GENERIC_500)


class UnhandledErrorMiddleware:
    """Innermost safety net: turns unexpected exceptions into the 500 envelope.

    Sitting inside the security/CORS middlewares means even 500s carry security and CORS headers.
    If the response already started (streaming) the exception is re-raised.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def _send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            # The traceback goes through the logging redaction filter (aadhi.logging_setup).
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            response = envelope(500, "internal", GENERIC_500)
            await response(scope, receive, send)


def install_exception_handlers(app: FastAPI) -> None:
    """Register every envelope handler on ``app``."""
    from sqlalchemy.exc import IntegrityError

    from ..auth.tokens import InvalidToken
    from ..jobs import queue as job_queue
    from ..jobs.base import BudgetExceeded
    from ..security.uploads import UploadRejected

    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(job_queue.JobInProgress, _job_in_progress_handler)
    app.add_exception_handler(BudgetExceeded, _budget_handler)
    app.add_exception_handler(UploadRejected, _upload_rejected_handler)
    app.add_exception_handler(InvalidToken, _invalid_token_handler)
    app.add_exception_handler(IntegrityError, _integrity_handler)
    # Raised by aadhi.jobs.queue on races (e.g. a job finished between check and transition).
    for name, handler in (("InvalidJobState", _invalid_job_state_handler), ("JobNotFound", _job_not_found_handler)):
        exc_type = getattr(job_queue, name, None)
        if isinstance(exc_type, type) and issubclass(exc_type, Exception):
            app.add_exception_handler(exc_type, handler)
    app.add_exception_handler(Exception, _unhandled_handler)
