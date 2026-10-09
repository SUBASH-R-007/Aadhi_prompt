"""Fake aadhi.pipeline.assets.scene_hash."""

from __future__ import annotations

import hashlib
import json


def scene_hash(scene) -> str:
    data = scene.model_dump(mode="json")
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
