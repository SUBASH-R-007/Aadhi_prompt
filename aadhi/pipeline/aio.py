"""Async helpers shared by pipeline stages."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

T = TypeVar("T")


async def gather_all(aws: Iterable[Awaitable[T]]) -> list[T]:
    """``asyncio.gather`` that cancels the remaining tasks when one fails, then re-raises."""
    tasks = [asyncio.ensure_future(a) for a in aws]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
