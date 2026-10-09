"""``AssetStore.get_or_create``: in-flight de-duplication, scoped by who pays for the work.

A producer running on one user's personal API key must never hand its outcome (e.g. a rejected key)
to another user's concurrent job for the same content key; callers in the same scope still share one
run, and the stored asset stays content-addressed (shared by everyone).
"""

from __future__ import annotations

import asyncio

from aadhi.providers.base import ProviderError
from aadhi.storage.assets import Produced


async def _until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def test_inflight_runs_are_shared_only_within_a_scope(asset_store):
    runs: list[str] = []

    async def scenario():
        gate = asyncio.Event()

        def producer(name: str, *, refuse: bool = False):
            async def produce() -> Produced:
                runs.append(name)
                await gate.wait()
                if refuse:
                    raise ProviderError("openai: request failed (HTTP 401)", status=401, provider="openai")
                return Produced(data=f"audio of {name}".encode(), mime="audio/mpeg")

            return produce

        alice = asyncio.ensure_future(
            asset_store.get_or_create("tts-shared", "tts", producer("alice", refuse=True), inflight_scope="user:1"))
        await _until(lambda: "alice" in runs)  # alice's run (on her refused key) is in flight
        alice_again = asyncio.ensure_future(
            asset_store.get_or_create("tts-shared", "tts", producer("alice-again"), inflight_scope="user:1"))
        bob = asyncio.ensure_future(asset_store.get_or_create("tts-shared", "tts", producer("bob")))
        await _until(lambda: "bob" in runs)  # bob (server key) did not wait on alice's run
        await asyncio.sleep(0.2)  # alice's second call reaches the in-flight map and joins her first run
        gate.set()
        return await asyncio.gather(alice, alice_again, bob, return_exceptions=True)

    first, second, other = asyncio.run(scenario())
    assert isinstance(first, ProviderError) and isinstance(second, ProviderError)  # same scope: one shared run
    asset, created = other
    assert created and asset.key == "tts-shared"
    assert runs == ["alice", "bob"]
    assert asset_store.get("tts-shared") is not None  # stored under the content key, for everyone
    assert not asset_store._inflight  # every slot was released
