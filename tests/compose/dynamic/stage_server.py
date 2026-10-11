"""A loopback server for the stub Dynamic render page (test-only; ephemeral port).

Serves ``/render-dynamic`` and the stub's ``/web/js/dynamic/render.js`` and ``stage.js`` (``stub_stage/``), its
config (``/stub-config.js`` -> ``window.STUB``), the timeline at ``GET /api/render/timeline`` (Bearer token
checked) and records every request path. Anything else is 404.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

STUB_DIR = Path(__file__).parent / "stub_stage"
FILES = {
    "/render-dynamic": ("render-dynamic.html", "text/html; charset=utf-8"),
    "/web/js/dynamic/render.js": ("render.js", "text/javascript; charset=utf-8"),
    "/web/js/dynamic/stage.js": ("stage.js", "text/javascript; charset=utf-8"),
}


@dataclass
class StageServerState:
    token: str
    timeline: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    timeline_requests: int = 0


def _handler(state: StageServerState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = urlsplit(self.path).path
            state.requests.append(path)
            if path in FILES:
                name, ctype = FILES[path]
                self._send(200, (STUB_DIR / name).read_bytes(), ctype)
            elif path == "/stub-config.js":
                self._send(200, f"window.STUB = {json.dumps(state.config)};\n".encode(), "text/javascript")
            elif path == "/api/render/timeline":
                state.timeline_requests += 1
                if self.headers.get("Authorization") != f"Bearer {state.token}":
                    self._send(401, b'{"code":"unauthenticated"}', "application/json")
                else:
                    self._send(200, json.dumps(state.timeline).encode("utf-8"), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


@contextmanager
def serve_stage(token: str, timeline: dict[str, Any], *,
                config: dict[str, Any] | None = None) -> Iterator[tuple[str, StageServerState]]:
    """Serve the stub stage on 127.0.0.1:<ephemeral>; yields ``(base_url, state)``."""
    state = StageServerState(token=token, timeline=timeline, config=dict(config or {}))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
