"""Opt-in network smoke tests (free services only): ``AADHI_NETWORK_TESTS=1``.

Never uses API keys: the Edge read-aloud service is free and keyless.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("AADHI_NETWORK_TESTS") != "1",
                                reason="network tests are opt-in (AADHI_NETWORK_TESTS=1)")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_edge_tts_real_word_boundaries(app_env) -> None:
    from aadhi.providers.tts.edge import EdgeTTS

    res = await EdgeTTS(app_env).synthesize("Ohm's law relates voltage, current and resistance.",
                                            voice="en-IN-NeerjaNeural", language="en-IN")
    assert res.audio and res.mime == "audio/mpeg"
    assert 1.5 < res.duration < 10
    assert len(res.words) >= 6 and res.words[0].text.lower().startswith("ohm")
    assert all(0 <= w.start <= w.end <= res.duration for w in res.words)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_edge_tts_tamil(app_env) -> None:
    from aadhi.providers.tts.edge import EdgeTTS

    res = await EdgeTTS(app_env).synthesize("மின்னோட்டம் என்பது மின்னூட்டங்களின் ஓட்டம்.",
                                            voice="ta-IN-PallaviNeural", language="ta-IN")
    assert res.duration > 1 and res.words
