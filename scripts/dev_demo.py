"""Offline demo server: the full pipeline with deterministic fake providers and no API keys.

    .venv/Scripts/python.exe scripts/dev_demo.py            # http://127.0.0.1:8010

* Forces LLM/IMAGE/VIDEO providers to ``fake`` and blanks every API key via environment variables
  (which take precedence over ``.env``), so nothing billable can be called even if ``.env`` holds
  real keys. API keys saved in the Studio are switched off too (``STORED_API_KEYS_ENABLED=false``):
  the demo never uses a server or personal key, and the Studio offers no key forms. TTS defaults to the free Microsoft Edge voices; set ``DEMO_TTS=fake`` for fully
  offline audio.
* Uses its own data directory (``data-demo/``) so it never touches your real database.
* The first admin password is generated once and written to ``data-demo/initial_admin_password.txt``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FORCED = {
    "APP_ENV": "development",
    "DATA_DIR": str(ROOT / "data-demo"),
    "DATABASE_URL": "",
    "LLM_PROVIDER": "fake",
    "IMAGE_PROVIDER": "fake",
    "VIDEO_PROVIDER": "fake",
    "TTS_PROVIDER": os.getenv("DEMO_TTS", "edge"),
    "GEMINI_API_KEY": "",
    "GEMINI_API_KEYS": "",
    "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "ELEVENLABS_API_KEY": "",
    "GIPHY_API_KEY": "",
    "STORED_API_KEYS_ENABLED": "false",
    "S3_ACCESS_KEY_ID": "",
    "S3_SECRET_ACCESS_KEY": "",
    "STORAGE_BACKEND": "local",
    "STORAGE_LOCAL_DIR": "",
    "WORKER_MODE": "inline",
    "MANIM_SANDBOX": os.getenv("DEMO_MANIM_SANDBOX", "subprocess"),
    "MANIM_VISUAL_QA": "false",
    "ADMIN_PASSWORD": "",
}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8010"))
    os.environ.update(FORCED)
    os.environ["BASE_URL"] = f"http://127.0.0.1:{port}"
    import uvicorn

    uvicorn.run("aadhi.main:app", host="127.0.0.1", port=port, log_config=None)
