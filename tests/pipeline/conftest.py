"""Fixtures for pipeline tests."""

from __future__ import annotations

import pytest

from tests.pipeline.fakes import install_fake_templates, install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN


@pytest.fixture()
def providers(monkeypatch):
    """All provider seams patched with offline fakes (LLM uses fake_content responders)."""
    return install_providers(monkeypatch)


@pytest.fixture()
def templates(monkeypatch):
    """A fake 'equation_steps' Manim template registry."""
    install_fake_templates(monkeypatch)


@pytest.fixture()
def sample_ingest():
    from aadhi.pipeline.base import IngestResult
    from aadhi.pipeline.chunking import chunk_markdown
    from aadhi.schemas.screenplay import SourceFigure

    return IngestResult(
        markdown=SAMPLE_MARKDOWN,
        chunks=chunk_markdown(SAMPLE_MARKDOWN),
        pages=3,
        figures=[SourceFigure(id="fig-p1-1", caption="Figure 1: A simple circuit with a cell and a resistor", page=1)],
        source_mime="text/markdown",
        detected_language="en-IN",
    )


@pytest.fixture()
def options():
    from aadhi.pipeline.base import GenerationOptions

    return GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)


@pytest.fixture()
def fast_audio(monkeypatch):
    """Audio assembly without ffmpeg (real logic, in-process WAV decode, dummy MP3 bytes)."""
    from tests.pipeline.fakes import install_fast_audio

    install_fast_audio(monkeypatch)
