"""A tiny threaded HTTP server for the Dynamic stage stub (ephemeral port, test-only).

Serves ``/render-dynamic`` (the stub page), ``/stage-stub.js``, ``/stub-config.js`` (``window.STUB``) and the timeline
API (``/api/render/timeline``, Bearer token); everything else is a 404. Every request path is recorded, so a test
can prove the media frames (``/__frames/...``) never reach the server: the render worker answers them itself.

With ``web_dir`` it serves the real stage instead: ``/render-dynamic`` is ``<web_dir>/render-dynamic.html`` and
``/web/...`` the files under ``web_dir`` (used when the real stage exists).
"""

from __future__ import annotations

import json
import mimetypes
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

STUB_DIR = Path(__file__).parent / "stub_stage"


@dataclass
class StageState:
    token: str
    timeline: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    web_dir: Path | None = None
    requests: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, path: str) -> None:
        with self._lock:
            self.requests.append(path)


def _handler(state: StageState) -> type[BaseHTTPRequestHandler]:
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
            state.record(self.path)
            path = urlsplit(self.path).path
            if path == "/render-dynamic":
                page = (state.web_dir / "render-dynamic.html") if state.web_dir else STUB_DIR / "render-dynamic.html"
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            elif path == "/stage-stub.js" and state.web_dir is None:
                self._send(200, (STUB_DIR / "stage-stub.js").read_bytes(), "text/javascript; charset=utf-8")
            elif path == "/stub-config.js":
                self._send(200, f"window.STUB = {json.dumps(state.config)};\n".encode(), "text/javascript")
            elif path == "/api/render/timeline":
                if self.headers.get("Authorization") != f"Bearer {state.token}":
                    self._send(401, b'{"code":"unauthenticated"}', "application/json")
                else:
                    self._send(200, json.dumps(state.timeline).encode("utf-8"), "application/json")
            elif state.web_dir is not None and path.startswith("/web/"):
                root = state.web_dir.resolve()
                file = (root / unquote(path[len("/web/"):])).resolve()
                if root in file.parents and file.is_file():
                    ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                    if file.suffix in (".js", ".mjs"):
                        ctype = "text/javascript"
                    self._send(200, file.read_bytes(), ctype)
                else:
                    self._send(404, b"not found", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


class _Server(ThreadingHTTPServer):
    request_queue_size = 128  # several workers' pages load at once (the default backlog is 5)


@contextmanager
def serve_stage(token: str, timeline: dict[str, Any], *, config: dict[str, Any] | None = None,
                web_dir: Path | None = None) -> Iterator[tuple[str, StageState]]:
    """Serve the stage on 127.0.0.1:<ephemeral>; yields ``(base_url, state)``."""
    state = StageState(token=token, timeline=timeline, config=dict(config or {}), web_dir=web_dir)
    server = _Server(("127.0.0.1", 0), _handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
