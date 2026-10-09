"""Job-scoped checkpoints of finished LLM stages, so a retried job does not pay for them twice.

A ``generate_lecture`` (or ``translate``) attempt that stops after its plan or scenes were paid for — a
deploy releasing the job, an OOM kill, a transient provider error, a lost lease — is run again by the
worker with the same job id and payload. The orchestrator stores the output of every finished LLM stage
as a private ``intermediate`` asset keyed by ``(job id, stage, digest of every input that shapes it)``;
a later attempt of the same job (``ctx.attempt > 1``) with the same inputs loads it instead of asking the
model again, so the lecture continues from the outputs that were already paid for.

* Job-scoped: another job (e.g. "generate again") never reuses them, so a new generation stays new.
* The digest covers the options, the AI engine, the models, the prompt versions, the source extract,
  the teacher's metadata and the outputs of the stages before: when anything changed between attempts
  (an admin switched models, the teacher renamed the session) the stage runs again.
* Never referenced by a project: the cleanup job removes them as orphans after 30 days.
* Best effort: an unreadable or unwritable checkpoint only means that the stage runs again.
* ``LLM_STAGE_CHECKPOINTS=false`` switches them off; contexts without a job id never use them.
* Scenes are also checkpointed one by one (:class:`SceneCheckpoints`, the hook of
  ``script.write_scenes_detailed``): an attempt that stopped while writing the scenes keeps the scenes it
  finished, and the retry only writes the others. A scene's key covers the scene-writing stage's inputs
  (which include the whole plan), the scene id and whether the original file went to the writers.
  Fallback scenes (writing failed) are never checkpointed: the retry asks the model again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from ..storage.assets import Produced, canonical_json, compute_key

log = logging.getLogger(__name__)

CHECKPOINT_KIND = "intermediate"
CHECKPOINT_VERSION = "1"  # bump when a stage's stored shape changes: older checkpoints then never match


def stage_digest(*parts: Any) -> str:
    """Digest of a stage's inputs (canonical JSON: key order and JSON round trips do not matter)."""
    return hashlib.sha256(canonical_json(list(parts)).encode("utf-8")).hexdigest()


class StageCheckpoints:
    """Load / save stage outputs for one job (see module docstring)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        job_id = getattr(ctx, "job_id", None)
        valid_id = isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0
        self.job_id: int | None = job_id if valid_id else None
        self.enabled = self.job_id is not None and bool(getattr(ctx.settings, "llm_stage_checkpoints", True))
        attempt = getattr(ctx, "attempt", 1)
        self.resumable = self.enabled and isinstance(attempt, int) and attempt > 1
        self.reused: list[str] = []

    def key(self, stage: str, inputs: str) -> str:
        return compute_key("checkpoint", {"job_id": self.job_id, "stage": stage, "inputs": inputs}, CHECKPOINT_VERSION)

    async def load(self, stage: str, inputs: str, *, label: str | None = None) -> dict[str, Any] | None:
        """The stored output of ``stage`` for exactly these ``inputs`` (only on a retry), else None.

        A hit is noted once in ``reused`` as ``label`` (default: the stage name)."""
        if not self.resumable:
            return None
        key = self.key(stage, inputs)
        try:
            asset = await asyncio.to_thread(self.ctx.assets.get, key, verify_blob=True)
            if asset is None:
                return None
            raw = await asyncio.to_thread(self.ctx.storage.get_bytes, asset.storage_key)
            doc = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - a checkpoint is an optimisation: run the stage instead
            log.info("checkpoint %s of job %s is unreadable (%s); running the stage", stage, self.job_id,
                     type(exc).__name__)
            return None
        data = doc.get("data") if isinstance(doc, dict) and doc.get("stage") == stage else None
        if not isinstance(data, dict):
            return None
        if (label or stage) not in self.reused:
            self.reused.append(label or stage)
        return data

    async def save(self, stage: str, inputs: str, data: dict[str, Any]) -> None:
        """Store ``data`` (JSON-safe) as the output of ``stage`` for ``inputs``. Never raises."""
        if not self.enabled:
            return
        key = self.key(stage, inputs)
        try:
            body = json.dumps({"stage": stage, "job_id": self.job_id, "data": data}, ensure_ascii=False,
                              allow_nan=False).encode("utf-8")
            produced = Produced(data=body, mime="application/json", meta={"checkpoint": stage, "job_id": self.job_id})
            await asyncio.to_thread(self.ctx.assets.put, key, CHECKPOINT_KIND, produced,
                                    created_by=getattr(self.ctx, "user_id", None))
        except Exception as exc:  # noqa: BLE001 - losing a checkpoint only costs a re-run on retry
            log.warning("could not save the %s checkpoint of job %s: %s", stage, self.job_id, type(exc).__name__)


class SceneCheckpoints:
    """Per-scene checkpoints of one job's scene-writing stage (the ``checkpoints`` hook of
    ``script.write_scenes_detailed``). ``stage_inputs``: the digest of that stage's inputs."""

    STAGE = "scene"
    LABEL = "scenes"  # how a reused scene shows in ``StageCheckpoints.reused`` (once, however many scenes)

    def __init__(self, stages: StageCheckpoints, stage_inputs: str) -> None:
        self.stages = stages
        self.stage_inputs = stage_inputs
        self.reused: list[str] = []  # ids of the scenes taken from the previous attempt

    def _inputs(self, scene_id: str, attached: bool) -> str:
        return stage_digest(self.STAGE, self.stage_inputs, scene_id, bool(attached))

    async def load(self, scene_id: str, attached: bool) -> dict[str, Any] | None:
        """The scene written for ``scene_id`` by an earlier attempt of this job (same inputs), else None."""
        data = await self.stages.load(self.STAGE, self._inputs(scene_id, attached), label=self.LABEL)
        if data is not None:
            self.reused.append(scene_id)
        return data

    async def save(self, scene_id: str, attached: bool, data: dict[str, Any]) -> None:
        """Store a finished scene (never raises)."""
        await self.stages.save(self.STAGE, self._inputs(scene_id, attached), data)
