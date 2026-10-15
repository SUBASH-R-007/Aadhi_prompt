"""Compose test configuration: the ``container`` marker.

``container`` marks the ffmpeg feature checks (``test_ffmpeg_features.py``) and the loudness measurements that
the Docker ``test`` image must pass on Debian bookworm's ffmpeg 5.1 (DYNAMIC_VIDEO_DESIGN §7.9; run there with
``pytest -m container``). They are also ``slow``, so elsewhere they run against the local ffmpeg with the slow
suite. ``pyproject.toml`` declares the marker; this hook keeps it known when pytest runs with another
configuration file.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "container: ffmpeg feature checks the Docker test image runs on ffmpeg 5.1 (pytest -m container)",
    )
