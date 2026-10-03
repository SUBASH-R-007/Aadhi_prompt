"""Signed links and request authentication for media routes (exports and library assets).

<video>, <audio> and <img> elements cannot send an Authorization header, so the page asks for a
short-lived link that is valid for one file only. Such a link is never a login: server.py's
get_current_user rejects any token that carries a scope.
"""
import datetime

import jwt
from fastapi import HTTPException

import models


def make_link_token(secret, algorithm, username, scope, resource_id, ttl):
    return jwt.encode({"sub": username, "scope": scope, "rid": resource_id, "exp": datetime.datetime.utcnow() + ttl},
                      secret, algorithm=algorithm)


def authenticate_media_request(request, token, *, scope, resource_id, db, secret, algorithm, noun="file"):
    """The user behind a normal login token (Authorization header) or a signed link (?token=)
    issued for exactly this resource."""
    header = request.headers.get("authorization", "")
    raw = header[7:] if header.lower().startswith("bearer ") else None
    scoped = raw is None and token is not None
    raw = raw or token
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(raw, secret, algorithms=[algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail=f"This link has expired. Open the {noun} again to get a new one.")
    if scoped:
        resource = payload.get("rid", payload.get("eid"))  # Phase 2 export links named it "eid"
        if payload.get("scope") != scope or resource != resource_id:
            raise HTTPException(status_code=403, detail=f"This link is not valid for this {noun}.")
    elif payload.get("scope"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(models.User).filter(models.User.username == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
