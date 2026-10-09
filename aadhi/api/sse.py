"""Server-sent events response for ``/api/jobs/{id}/stream``.

Stream admission (per-user / global caps) and slot release live in ``aadhi.jobs.events.stream_job``
(it raises ``TooManyStreams`` *before* any byte is sent, so the API can still answer 429). This
module only wraps the chunk iterator so that it is always closed — on normal end, client
disconnect or error — which stops the DB polling and releases the slot immediately.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anyio
from starlette.responses import StreamingResponse

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


async def closing(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """Yield from ``stream`` and always ``aclose`` it (shielded: runs even on client disconnect)."""
    try:
        async for chunk in stream:
            yield chunk
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            with anyio.CancelScope(shield=True):
                await aclose()


def event_stream_response(stream: AsyncIterator[str]) -> StreamingResponse:
    """``text/event-stream`` response for an iterator of pre-formatted SSE chunks."""
    return StreamingResponse(closing(stream), media_type="text/event-stream", headers=dict(SSE_HEADERS))
