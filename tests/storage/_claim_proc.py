"""Child-process entry point for the cross-process claim test (importable under ``spawn``).

The child inherits the test's environment (fake providers, blank API keys, the test's DATA_DIR and
DATABASE_URL), builds its OWN ``AssetStore`` on the shared SQLite database and storage directory,
and asks for the same paid asset as its sibling process.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any


def produce_video(key: str, calls_file: str, result_file: str, barrier: Any, hold_seconds: float) -> None:
    from aadhi.db import get_sessionmaker
    from aadhi.storage import get_storage
    from aadhi.storage.assets import AssetStore, ClaimConfig, Produced

    store = AssetStore(get_storage(), get_sessionmaker(),
                       claims=ClaimConfig(ttl_seconds=30, poll_seconds=0.05, max_wait_seconds=60))

    async def producer() -> Produced:
        with open(calls_file, "a", encoding="utf-8") as f:  # one line per (paid) provider call
            f.write(f"{os.getpid()}\n")
        await asyncio.sleep(hold_seconds)
        return Produced(data=b"\x00\x00\x00\x18ftypmp42 cross-process", mime="video/mp4", duration_s=8.0)

    barrier.wait(timeout=60)  # both processes ask at (nearly) the same time
    started = time.monotonic()
    asset, created = asyncio.run(store.get_or_create(key, "video", producer))
    Path(result_file).write_text(json.dumps({
        "pid": os.getpid(), "created": created, "key": asset.key, "storage_key": asset.storage_key,
        "seconds": round(time.monotonic() - started, 3),
    }), encoding="utf-8")
