"""Session and scoped JWTs: typ confusion both ways, expiry, leeway, algorithms, key separation."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest

from aadhi.auth.tokens import (
    InvalidToken,
    _scoped_key,
    create_scoped_token,
    create_session_token,
    decode_scoped_token,
    decode_session_token,
)


def _user(uid: int = 7, tv: int = 3):
    return SimpleNamespace(id=uid, token_version=tv)


def _secret(settings) -> str:
    return settings.resolved_jwt_secret()


def test_session_roundtrip(app_env):
    tok = create_session_token(_user(), app_env)
    claims = decode_session_token(tok, app_env)
    assert claims["sub"] == "7" and claims["tv"] == 3 and claims["typ"] == "session"
    assert claims["exp"] - claims["iat"] == app_env.jwt_ttl_hours * 3600
    assert len(claims["jti"]) >= 16
    assert jwt.get_unverified_header(tok)["alg"] == "HS256"
    assert create_session_token(_user(), app_env) != tok  # unique jti


def test_scoped_token_is_never_a_session(app_env):
    scoped = create_scoped_token("render", {"vid": 5}, 60, app_env)
    with pytest.raises(InvalidToken):
        decode_session_token(scoped, app_env)
    # Even re-signed with the session key, typ=scoped is refused.
    forged = jwt.encode(
        {"sub": "1", "tv": 0, "typ": "scoped", "iat": int(time.time()), "exp": int(time.time()) + 60, "jti": "x"},
        _secret(app_env),
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_session_token(forged, app_env)


def test_session_token_is_never_scoped(app_env):
    session = create_session_token(_user(), app_env)
    with pytest.raises(InvalidToken):
        decode_scoped_token(session, "render", app_env)
    # A session-typed payload signed with the scoped key is refused too (typ check).
    now = int(time.time())
    forged = jwt.encode(
        {"typ": "session", "scope": "render", "iat": now, "exp": now + 60, "jti": "x"},
        _scoped_key("render", app_env),
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_scoped_token(forged, "render", app_env)


def test_scoped_roundtrip_and_scope_separation(app_env):
    tok = create_scoped_token("render", {"vid": 9}, 120, app_env)
    claims = decode_scoped_token(tok, "render", app_env)
    assert claims["vid"] == 9 and claims["scope"] == "render" and claims["typ"] == "scoped"
    with pytest.raises(InvalidToken):
        decode_scoped_token(tok, "export", app_env)
    assert _scoped_key("render", app_env) != _scoped_key("export", app_env)
    assert _scoped_key("render", app_env) != _secret(app_env).encode()
    # Scoped token signed with the raw JWT secret (not the derived key) is rejected.
    now = int(time.time())
    raw = jwt.encode(
        {"typ": "scoped", "scope": "render", "iat": now, "exp": now + 60, "jti": "j"}, _secret(app_env), "HS256"
    )
    with pytest.raises(InvalidToken):
        decode_scoped_token(raw, "render", app_env)


def test_scoped_defaults_to_global_settings(app_env):
    tok = create_scoped_token("render", {"vid": 1}, 30)
    assert decode_scoped_token(tok, "render")["vid"] == 1


def test_expiry_and_leeway(app_env):
    key = _secret(app_env)
    now = int(time.time())
    base = {"sub": "1", "tv": 0, "typ": "session", "iat": now - 100, "jti": "j"}
    within_leeway = jwt.encode({**base, "exp": now - 10}, key, algorithm="HS256")
    assert decode_session_token(within_leeway, app_env)["sub"] == "1"
    expired = jwt.encode({**base, "exp": now - 120}, key, algorithm="HS256")
    with pytest.raises(InvalidToken, match="expired"):
        decode_session_token(expired, app_env)
    future_iat = jwt.encode({**base, "iat": now + 600, "exp": now + 1200}, key, algorithm="HS256")
    with pytest.raises(InvalidToken):
        decode_session_token(future_iat, app_env)


def test_scoped_expiry(app_env):
    now = int(time.time())
    tok = jwt.encode(
        {"typ": "scoped", "scope": "render", "iat": now - 600, "exp": now - 300, "jti": "j"},
        _scoped_key("render", app_env),
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_scoped_token(tok, "render", app_env)


@pytest.mark.parametrize("missing", ["sub", "tv", "typ", "iat", "exp", "jti"])
def test_required_session_claims(app_env, missing):
    now = int(time.time())
    payload = {"sub": "1", "tv": 0, "typ": "session", "iat": now, "exp": now + 60, "jti": "j"}
    payload.pop(missing)
    with pytest.raises(InvalidToken):
        decode_session_token(jwt.encode(payload, _secret(app_env), algorithm="HS256"), app_env)


@pytest.mark.parametrize("sub,tv", [("abc", 0), ("1", "0"), ("1", True), ("-1", 0), (1, 0)])
def test_session_claim_types(app_env, sub, tv):
    now = int(time.time())
    payload = {"sub": sub, "tv": tv, "typ": "session", "iat": now, "exp": now + 60, "jti": "j"}
    try:
        token = jwt.encode(payload, _secret(app_env), algorithm="HS256")
    except Exception:  # noqa: BLE001 - PyJWT may refuse a non-string sub at encode time
        return
    with pytest.raises(InvalidToken):
        decode_session_token(token, app_env)


@pytest.mark.filterwarnings("ignore:The HMAC key")
def test_algorithm_pinned(app_env):
    now = int(time.time())
    payload = {"sub": "1", "tv": 0, "typ": "session", "iat": now, "exp": now + 60, "jti": "j"}
    hs512 = jwt.encode(payload, _secret(app_env), algorithm="HS512")
    with pytest.raises(InvalidToken):
        decode_session_token(hs512, app_env)
    unsigned = jwt.encode(payload, None, algorithm="none")
    with pytest.raises(InvalidToken):
        decode_session_token(unsigned, app_env)


def test_wrong_secret_and_tampering(app_env):
    tok = create_session_token(_user(), app_env)
    other = app_env.model_copy(update={"jwt_secret": type(app_env.jwt_secret)("another-secret-" + "y" * 40)})
    with pytest.raises(InvalidToken):
        decode_session_token(tok, other)
    head, body, sig = tok.split(".")
    tampered = ".".join([head, body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1], sig])
    with pytest.raises(InvalidToken):
        decode_session_token(tampered, app_env)
    for junk in ("", "abc", "a.b.c", "x" * 5000, None):
        with pytest.raises(InvalidToken):
            decode_session_token(junk, app_env)  # type: ignore[arg-type]


def test_scoped_argument_validation(app_env):
    with pytest.raises(ValueError):
        create_scoped_token("Render!", {}, 60, app_env)
    with pytest.raises(ValueError):
        create_scoped_token("render", {"typ": "session"}, 60, app_env)
    with pytest.raises(ValueError):
        create_scoped_token("render", {"exp": 1}, 60, app_env)
    with pytest.raises(ValueError):
        create_scoped_token("render", {}, 0, app_env)
    with pytest.raises(ValueError):
        create_scoped_token("render", {}, 8 * 24 * 3600, app_env)
    with pytest.raises(InvalidToken):
        decode_scoped_token(create_scoped_token("render", {}, 60, app_env), "BAD SCOPE", app_env)


def test_dev_secret_is_persisted(app_env, tmp_path):
    from pydantic import SecretStr

    s = app_env.model_copy(update={"jwt_secret": SecretStr(""), "data_dir": tmp_path / "d"})
    tok = create_session_token(_user(), s)
    assert decode_session_token(tok, s)["sub"] == "7"
    assert (tmp_path / "d" / ".jwt_secret").exists()
    prod = s.model_copy(update={"app_env": "production"})
    with pytest.raises(RuntimeError):
        create_session_token(_user(), prod)
