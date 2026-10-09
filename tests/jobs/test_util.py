"""Small helpers: time normalisation, JSON coercion, redaction, error formatting."""

from __future__ import annotations

import datetime as dt

from aadhi.db import session_scope
from aadhi.jobs.queue import has_claimable
from aadhi.jobs.util import (
    as_utc,
    clean_text,
    finite_or_none,
    is_connection_error,
    is_transient_db_error,
    iso,
    jsonable,
    redact_obj,
    short_error,
    traceback_tail,
    truncate,
)

from ._helpers import add_job, update_job


def test_time_helpers():
    naive = dt.datetime(2026, 1, 2, 3, 4, 5)  # noqa: DTZ001 - SQLite hands back naive UTC values
    assert as_utc(naive).tzinfo is dt.timezone.utc
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    assert as_utc(dt.datetime(2026, 1, 2, 8, 34, 5, tzinfo=ist)) == as_utc(naive)
    assert iso(naive) == "2026-01-02T03:04:05+00:00"
    assert iso(None) is None and as_utc(None) is None


def test_truncate_and_jsonable():
    assert truncate("abc", 3) == "abc"
    assert truncate("abcdef", 4) == "abc…"
    assert jsonable({"d": dt.date(2026, 1, 1), "t": (1, 2), "s": {2, 1}}) == {"d": "2026-01-01", "t": [1, 2], "s": [1, 2]}
    assert jsonable(None) is None


def test_jsonable_is_strict_json():
    """NaN/inf (json.dumps emits them, Postgres JSONB rejects them) and NUL characters never survive."""
    import enum
    import json

    class Color(str, enum.Enum):
        RED = "red"

    class Level(enum.IntEnum):
        HIGH = 3

    cyclic: dict = {"a": 1}
    cyclic["self"] = cyclic
    value = {
        "nan": float("nan"),
        "inf": [float("inf"), float("-inf"), 1.5],
        "nul\x00key": "a\x00b",
        1: "int key",
        None: True,
        (1, 2): "tuple key",
        "enum": Color.RED,
        "int_enum": Level.HIGH,
        "obj": object.__new__(type("Thing", (), {"__str__": lambda self: "thing\x00"})),
        "cyclic": cyclic,
    }
    out = jsonable(value)
    assert out["nan"] is None and out["inf"] == [None, None, 1.5]
    assert out["nulkey"] == "ab" and out["1"] == "int key" and out["null"] is True and out["(1, 2)"] == "tuple key"
    assert out["enum"] == "red" and out["int_enum"] == 3 and out["obj"] == "thing"
    assert out["cyclic"] == {"a": 1, "self": "<cycle>"}
    json.dumps(out, allow_nan=False)  # strictly valid JSON
    shared = [1]
    assert jsonable({"a": shared, "b": shared}) == {"a": [1], "b": [1]}  # repeated, not cyclic


def test_clean_text_and_finite():
    assert clean_text("a\x00b\x00") == "ab" and clean_text("plain") == "plain"
    assert finite_or_none("1.5") == 1.5 and finite_or_none(float("nan")) is None
    assert finite_or_none("x") is None and finite_or_none(None) is None and finite_or_none(10**400) is None


def test_traceback_redacted_before_it_is_cut(app_env, monkeypatch):
    """A secret straddling the cut must not leave a fragment behind (redact first, then cut)."""
    from pydantic import SecretStr

    from aadhi.config import get_settings

    key = "sk-test-" + "1a2B3c4D5e" * 3
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr(key))
    try:
        raise RuntimeError("calling provider with " + key + " " + "x" * 300)
    except RuntimeError as exc:
        for limit in range(300, 400, 3):  # move the cut across the whole key
            tail = traceback_tail(exc, limit=limit)
            assert len(tail) <= limit
            for n in range(6, len(key)):
                assert key[-n:] not in tail, (limit, n)


def test_db_error_classification():
    from sqlalchemy import exc as sa_exc

    op = sa_exc.OperationalError("UPDATE jobs", {}, Exception("database is locked"))
    data = sa_exc.DataError("INSERT", {}, Exception("invalid input syntax for type json"))
    integrity = sa_exc.IntegrityError("INSERT", {}, Exception("unique"))
    dropped = sa_exc.DBAPIError("SELECT 1", {}, Exception("server closed"), connection_invalidated=True)
    assert is_connection_error(op) and is_transient_db_error(op)
    assert is_connection_error(dropped) and is_transient_db_error(dropped)
    assert is_connection_error(ConnectionResetError()) and is_connection_error(sa_exc.TimeoutError())
    for err in (data, integrity, ValueError("NUL"), TypeError("x")):
        assert not is_connection_error(err) and not is_transient_db_error(err)
    assert is_transient_db_error(RuntimeError("unknown")) and not is_connection_error(RuntimeError("unknown"))


def test_redaction_helpers(app_env):
    data = {"url": "https://x/?key=K123456", "nested": [{"token": "token=T987654"}], "n": 3}
    out = redact_obj(data)
    assert "K123456" not in str(out) and "T987654" not in str(out) and out["n"] == 3
    try:
        raise RuntimeError("failed with password=hunter2222\nsecond line")
    except RuntimeError as exc:
        assert short_error(exc) == "RuntimeError: failed with password=[REDACTED]"
        tail = traceback_tail(exc, limit=200)
        assert "hunter2222" not in tail and len(tail) <= 200 and "RuntimeError" in tail
    assert short_error(ValueError()) == "ValueError"


def test_has_claimable(app_env):
    with session_scope() as db:
        assert not has_claimable(db, ["t_job"])
    jid = add_job()
    with session_scope() as db:
        assert has_claimable(db, ["t_job"]) and not has_claimable(db, []) and not has_claimable(db, ["other"])
    update_job(jid, run_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1))
    with session_scope() as db:
        assert not has_claimable(db, ["t_job"])
