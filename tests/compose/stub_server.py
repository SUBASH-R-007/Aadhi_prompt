"""Tiny threaded HTTP servers for the stub render page (ephemeral ports, test-only).

* :func:`serve_stub` serves the stub render page, its config (``mode`` + extra keys, read by
  ``stub.js``), the timeline API and a same-origin ``/redirect?to=...`` (302) endpoint.
* :func:`serve_other` is a second origin that records every request it receives (to prove the
  render page cannot reach it) and answers ``/fig.png`` with an orange PNG.
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image

STUB_DIR = Path(__file__).parent / "stub_render"
FIGURE_RGB = (255, 128, 0)


@dataclass
class StubState:
    token: str
    timeline: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    requests: list[tuple[str, str | None]] = field(default_factory=list)  # (path, authorization)


@dataclass
class OtherState:
    requests: list[str] = field(default_factory=list)  # request lines


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence
        return

    def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def _handler(state: StubState) -> type[BaseHTTPRequestHandler]:
    class Handler(_Base):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            auth = self.headers.get("Authorization")
            state.requests.append((self.path, auth))
            parts = urlsplit(self.path)
            if parts.path == "/render-frame":
                self._send(200, (STUB_DIR / "render.html").read_bytes(), "text/html; charset=utf-8")
            elif parts.path == "/stub.js":
                self._send(200, (STUB_DIR / "stub.js").read_bytes(), "text/javascript; charset=utf-8")
            elif parts.path == "/stub-config.js":
                body = f"window.STUB = {json.dumps(state.config)};\n".encode()
                self._send(200, body, "text/javascript; charset=utf-8")
            elif parts.path == "/redirect":
                target = parse_qs(parts.query).get("to", [""])[0]
                self._send(302, b"", "text/plain", {"Location": target})
            elif parts.path == "/api/render/timeline":
                if auth != f"Bearer {state.token}":
                    self._send(401, b'{"code":"unauthenticated"}', "application/json")
                else:
                    self._send(200, json.dumps(state.timeline).encode("utf-8"), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), FIGURE_RGB).save(buf, format="PNG")
    return buf.getvalue()


def _other_handler(state: OtherState) -> type[BaseHTTPRequestHandler]:
    png = _png_bytes()

    class Handler(_Base):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            state.requests.append(f"GET {self.path}")
            if urlsplit(self.path).path == "/fig.png":
                self._send(200, png, "image/png", {"Access-Control-Allow-Origin": "*"})
            else:
                self._send(200, b"ok", "text/plain")

    return Handler


@contextmanager
def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def serve_stub(token: str, timeline: dict[str, Any], *, mode: str = "ok",
               config: dict[str, Any] | None = None) -> Iterator[tuple[str, StubState]]:
    """Serve the stub render page on 127.0.0.1:<ephemeral>; yields (base_url, state)."""
    state = StubState(token=token, timeline=timeline, config={"mode": mode, **(config or {})})
    with _serve(_handler(state)) as base:
        yield base, state


@contextmanager
def serve_other() -> Iterator[tuple[str, OtherState]]:
    """A second origin that records requests; yields (base_url, state)."""
    state = OtherState()
    with _serve(_other_handler(state)) as base:
        yield base, state
