"""Administrative data in real SME document shapes, end to end with the offline responders.

For every document in ``fixtures.leak_sources`` (serial-number header tables in DOCX and PDF, key variants,
upper-case TXT headers, Markdown front matter, an AV-table script, admin data inside title lines, per-clip
scaffolding, a recording note naming the author) the full lecture is generated through
``orchestrator.generate_lecture``: no header value reaches the user prompt of any model call (brief, plan,
scene writers, quiz, critic, practice), the screenplay or a job event, and the version is ready with no
``content.admin_leak`` issue.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aadhi.pipeline import orchestrator
from aadhi.pipeline.base import GenerationOptions
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.fixtures import leak_sources as L

OPTIONS = GenerationOptions(target_minutes=5, quiz_every_n_concepts=2)
NAMES = {".docx": "source.docx", ".pdf": "source.pdf", ".txt": "source.txt", ".md": "source.md"}


def generate(job_ctx: Any, monkeypatch: Any, data: bytes, mime: str, filename: str) -> dict[str, Any]:
    providers = install_providers(monkeypatch)
    seeded = seed_project(job_ctx.assets.storage, data, mime, filename, options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id, job_ctx.version_id = seeded.project_id, seeded.version_id
    job_ctx.kind = "generate_lecture"
    job_ctx.payload = {"source_document_id": seeded.source_id, "options": OPTIONS.model_dump(mode="json"),
                       "base_revision": 1}
    asyncio.run(orchestrator.generate_lecture(job_ctx))
    v = get_version(seeded.version_id)
    assert v.status == "ready", v.generation_meta.get("error")
    return {"version": v, "screenplay": Screenplay.model_validate(v.screenplay), "llm": providers.llm,
            "events": job_ctx.events}


@pytest.mark.parametrize("name", list(L.ALL))
def test_header_values_never_reach_a_prompt_or_the_lecture(name, job_ctx, monkeypatch, fast_audio):
    data, mime, secrets = L.ALL[name]()
    if name == "note.docx":
        secrets = ("Kavitha", "Ramanujam", "record this segment")
    filename = NAMES["." + name.rsplit(".", 1)[-1]]
    out = generate(job_ctx, monkeypatch, data, mime, filename)
    calls = out["llm"].calls
    assert {"GenBrief", "GenPlan"} <= {c["schema"] for c in calls}
    for call in calls:  # the system prompts name packaging phrases as examples; the user prompt carries the source
        text = call["prompt"].lower()
        for secret in secrets:
            assert secret.lower() not in text, (secret, call["schema"])
    lecture = json.dumps(out["version"].screenplay, ensure_ascii=False).lower()
    events = json.dumps(out["events"], ensure_ascii=False).lower()
    for secret in secrets:
        assert secret.lower() not in lecture, (secret, "screenplay")
        assert secret.lower() not in events, (secret, "job events")
    issues = out["version"].issues
    assert not [i for i in issues if i["code"] == "content.admin_leak"], issues
    sp = out["screenplay"]
    assert [s.type for s in sp.scenes].count("title") == 1  # one opening, however many clips the source had
