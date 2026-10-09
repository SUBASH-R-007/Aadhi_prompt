"""Aadhi EduEngine v2 entry point (thin shim; the app lives in ``aadhi.main``).

``python server.py`` keeps working for local development and ``.claude/launch.json``.
``log_config=None``: the app already installed ``aadhi.logging_setup`` (single-line, redacting
handler on the root logger); uvicorn must not replace it with its own unredacted handlers.
"""

from __future__ import annotations

import os

import uvicorn

from aadhi.main import app  # noqa: F401  (re-exported for `uvicorn server:app`)

if __name__ == "__main__":
    uvicorn.run(
        "aadhi.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_config=None,
    )
