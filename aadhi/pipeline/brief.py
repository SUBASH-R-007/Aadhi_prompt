"""Concept brief: what the lecture must teach, distilled from the scoped source before planning.

One structured call (the lecture engine's fast model; an admin's ``llm_model_plan`` override wins) with
``GenBrief``. The model sees only the scoped source chunks (``ingest.chunks``: metadata,
administrative/production data and visual directions already removed by ``source_scope``), the
*number* of visual suggestions set aside, and the request (audience, depth, narration language).
Excluded texts such as the SME's name are never sent, not even as "things to avoid".

Every chunk must be accounted for: cited by a concept (its ``source_refs`` or a key fact's), by a
source question, or listed in ``skipped_chunks`` with a reason (administrative, production,
scaffolding, duplicate, off_topic). When a brief exists, the planner and the scene writers never see
the skipped chunks (``plan.build_plan_prompt``, ``scene_context``), so the lecture explains the
subject matter only.

Semantic problems (unknown chunk refs or prerequisite keys, prerequisite cycles, an order that
breaks dependencies, no concepts, chunks neither cited nor skipped, a skipped chunk that reads like
teaching prose) go back to the model inside the ``generate_json`` re-ask loop; later passes only
insist on a non-empty brief and ``brief_from_gen`` fixes the rest deterministically (unknown refs
dropped, cycles broken, a dependency-respecting teaching order, skips of cited chunks ignored, skips
of chunks that hold a definition, law, formula or worked example ignored, a ``duplicate`` skip kept
only when the chunk repeats a cited one, and no skips at all when they would set aside more than half
of the source's text).

The result is cached as a private ``intermediate`` asset keyed by the exact prompt (which is built
from the extract and the options subset it uses), the prompt version and the model, so planning the
same source again (plan review, a new version) costs nothing.

``build_brief_prompt`` is pure (used by the prompt preview tool); ``fake_brief_responder`` gives
offline runs a deterministic brief.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from ..schemas.screenplay import slugify
from ..storage.assets import Produced, compute_key
from . import dbops, integrations
from .base import BriefConcept, BriefFact, ConceptBrief, ExcludedItem, GenerationOptions, IngestResult, SkippedChunk
from .gen_models import GenBrief
from .ingest import readable_chars
from .prompting import extract_json, join_sections, json_section, language_label, load_prompt, section, system_prompt
from .source_scope import looks_like_person, parse_title_line

log = logging.getLogger(__name__)

BRIEF_PROMPTS = ("brief",)
BRIEF_CONTEXT_CHARS = 200_000
BRIEF_CACHE_VERSION = "2"  # 2: skipped_chunks + source question refs
MIN_BRIEF_CHARS = 200  # readable characters below which (and without the original file) no brief is attempted
MAX_CONCEPTS = 40
MAX_PROBLEMS = 20
MAX_SKIPPED_SHARE = 0.5  # skips that would set aside more of the source's text than this are all ignored

SME_SCRIPT_NOTE = (
    "The source is a video script written by a subject-matter expert. Its clip and segment scaffolding, title "
    "cards, bridges between clips, timings, administrative details and its narrator and board labels were removed "
    "before you see it, so a point may appear twice: once as narration and once as a board line."
)
ATTACHED_NOTE = ("The original document is attached. Read it directly for equations, figures and scanned pages, "
                 "ignore its administrative and production details, and cite the chunk ids below.")


class BriefUnavailable(Exception):
    """No brief can be built for this source (too little readable text); planning continues without one."""


def brief_model(options: GenerationOptions, settings: Any) -> str:
    """The lecture engine's fast model, unless an admin override for the planning stage is set."""
    return integrations.llm_model(settings, "fast", options, override=options.llm_model_plan)


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


def _chunk_payload(ingest: IngestResult, budget: int = BRIEF_CONTEXT_CHARS) -> list[dict[str, Any]]:
    total = sum(len(c.text) for c in ingest.chunks) or 1
    scale = min(1.0, budget / total)
    out = []
    for c in ingest.chunks:
        text = c.text if scale >= 1.0 else c.text[: max(300, int(len(c.text) * scale))]
        out.append({"id": c.id, "heading": " > ".join(c.heading_path), "text": text})
    return out


def brief_request(ingest: IngestResult, options: GenerationOptions) -> dict[str, Any]:
    return {
        "audience": options.audience,
        "depth": options.depth,
        "narration_language": language_label(options.language),
        "source_format": ingest.source_format,
        "visual_suggestions_set_aside": len(ingest.visual_notes),
    }


def build_brief_prompt(ingest: IngestResult, options: GenerationOptions, *, attached: bool = False) -> tuple[str, str]:
    """(system, user) prompts for the concept brief. Pure: no I/O, no settings."""
    about: list[str] = []
    if ingest.source_format == "sme_script":
        about.append(SME_SCRIPT_NOTE)
    if ingest.visual_notes:
        about.append(f"The author's {len(ingest.visual_notes)} animation and visual suggestions were set aside for the "
                     "animators; they are not part of the brief.")
    about.extend(ingest.warnings)
    if attached:
        about.append(ATTACHED_NOTE)
    user = join_sections(
        json_section("Request", brief_request(ingest, options)),
        section("About the source", "\n".join(about)),
        json_section("Source chunks", _chunk_payload(ingest)),
        section("Task", f"Write the teaching brief for this source. Account for all {len(ingest.chunks)} chunk ids: cite "
                        "each one from a concept, a key fact or a source question, or list it in skipped_chunks with a "
                        "reason. Respond with JSON that matches the response schema."),
    )
    return system_prompt(*BRIEF_PROMPTS), user


# ---------------------------------------------------------------------------
# validation + normalisation
# ---------------------------------------------------------------------------


def concept_key(raw: str, fallback: str = "concept") -> str:
    """ASCII snake_case key (<= 64 chars); "Ohm's Law" -> "ohms_law"."""
    key = slugify(re.sub(r"['’`]", "", raw or ""), fallback).replace("-", "_")
    key = re.sub(r"_+", "_", key).strip("_") or fallback
    return key[:64]


def _cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """One prerequisite cycle (as a key path) or None."""
    state: dict[str, int] = {}
    for start in graph:
        if state.get(start):
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, i = stack.pop()
            if i == 0:
                state[node] = 1
                path.append(node)
            deps = graph.get(node, [])
            if i < len(deps):
                stack.append((node, i + 1))
                nxt = deps[i]
                if state.get(nxt) == 1:
                    return path[path.index(nxt):] + [nxt]
                if not state.get(nxt) and nxt in graph:
                    stack.append((nxt, 0))
            else:
                state[node] = 2
                path.pop()
    return None


_FORMULA = re.compile(r"[\w)\]'’]\s*(?:=|≈|≤|≥|≠)\s*[\w(\\√−-]|\\(?:frac|sqrt|overline|sum|int)\b")
_EXPRESSION = re.compile(r"\w\s*[+×·*]\s*\w+\s*[+×·*]\s*\w")  # "R1 + R2 + R3", "A · B · C"
_DEFINITION = re.compile(r"\b(?:is|are)\s+(?:defined\s+as|called|known\s+as|the\s+(?:rate|ratio|amount|measure|product"
                         r"|quantity|property|process|opposition|study))\b|\b(?:refers?\s+to|states?\s+that|means\s+that)\b",
                         re.I)
_LAW = re.compile(r"\b(?:law|theorem|principle|rule|postulate|axiom|lemma|identity|corollary|criterion)\b"
                  r"(?:\s+of\s+[\w'’ -]{1,40}?)?\s*(?::|\bsays\b|\bstates\b|\bholds\b|\bgives\b)", re.I)
_EXAMPLE = re.compile(r"\b(?:examples?|e\.g\.|for\s+instance|consider|suppose|worked\s+problem|illustration)\b", re.I)
# "Current: the rate of flow of charge", "Series resistors add: the total ...", "- Node: a point where ..."
_COLON_DEF = re.compile(r"(?:^|[.;]\s+|^\s*[-*•]\s*)([A-Z][\w'’()/-]*(?:[ \t]+[\w'’()/-]+){0,5}):[ \t]+(?:the|an?)\s+\w+"
                        r"(?:\s+\S+){2,}", re.M)
# "Voltage is the electric potential difference between two points", "A node is a point where ..."
_IS_DEF = re.compile(r"(?:^|(?<=[.!?;])\s+)([A-Z][\w'’()-]*(?:\s+[\w'’()-]+){0,4})\s+(?:is|are)\s+(?:the|an?)\s+\w+"
                     r"(?:\s+\S+){3,}")
_NOT_A_TERM = re.compile(  # subjects of packaging sentences: "This video is the first ...", "Coming up next: the ..."
    r"^(?:this|that|it|there|here|today|now|next|coming|up\s+next|my|our|your|i|we|you|he|she|they)\b"
    r"|\b(?:video|clip|session|segment|lecture|series|module|course|unit|recording|duration|sme|faculty|semester|"
    r"regulation|reviewer|prepared|recorded|camera|editor|studio|note|next|bridge|transition)\b", re.I)
_PROSE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _defined_term(pattern: re.Pattern[str], text: str) -> bool:
    return any(not _NOT_A_TERM.search(m.group(1)) for m in pattern.finditer(text))


def teaching_signal(text: str, *, prose: bool = True) -> str:
    """Why ``text`` reads like teaching content ("" when it does not): a formula, a definition ("X is the ...",
    "X: the ..."), a law or rule statement, an example with numbers, or (``prose``) several explanatory sentences.

    On the first pass it asks the model to reconsider a skip. Without ``prose`` (the strong signals only) it also
    decides: ``skipped_from_gen`` never sets aside such a chunk. An admin line with "=" is then kept, which fails
    open: source scoping and the lint leak checks still apply to it."""
    raw = text or ""
    t = " ".join(raw.split())
    if _FORMULA.search(t) or _EXPRESSION.search(t):
        return "a formula"
    if _DEFINITION.search(t):
        return "a definition"
    if _LAW.search(t):
        return "a law or rule"
    if _EXAMPLE.search(t) and re.search(r"\d", t):
        return "a worked example"
    if _defined_term(_COLON_DEF, raw) or _defined_term(_IS_DEF, t):
        return "a definition"
    if prose and sum(1 for x in _PROSE_SPLIT.split(t) if len(x.split()) >= 6) >= 3:
        return "several explanatory sentences"
    return ""


def gen_cited(gen: GenBrief) -> set[str]:
    """Chunk ids a generated brief cites (concepts, their key facts, source questions)."""
    out = {r.strip() for c in gen.concepts for r in [*c.source_refs, *(x for f in c.key_facts for x in f.source_refs)]}
    out.update(r.strip() for q in gen.source_questions for r in q.source_refs)
    return out


def accounting_problems(gen: GenBrief, chunks: Mapping[str, str]) -> list[str]:
    """Chunks neither cited nor skipped, unknown or contradictory skips, and skipped chunks that read like teaching
    content (``chunks``: id -> text in source order; empty texts skip that last check)."""
    cited = gen_cited(gen)
    skipped = [x.chunk_id.strip() for x in gen.skipped_chunks]
    out: list[str] = []
    missing = [cid for cid in chunks if cid not in cited and cid not in skipped]
    if missing:
        out.append(f"chunks {missing[:15]} are neither cited nor skipped: cite each one from the concept, key fact or "
                   "source question it supports, or list it in skipped_chunks with a reason if it holds no teaching "
                   "content.")
    unknown = sorted({cid for cid in skipped if cid not in chunks})
    if unknown:
        out.append(f"skipped_chunks lists unknown chunk ids {unknown[:5]}; use ids from the Source chunks section.")
    both = [cid for cid in dict.fromkeys(skipped) if cid in cited and cid in chunks]
    if both:
        out.append(f"chunks {both[:8]} are both cited and skipped; a cited chunk is teaching content, so remove it "
                   "from skipped_chunks.")
    flagged = 0
    for x in gen.skipped_chunks:
        cid = x.chunk_id.strip()
        why = teaching_signal(chunks.get(cid, "")) if cid not in cited else ""
        if why and flagged < 5:
            flagged += 1
            out.append(f"chunk {cid} is skipped as {x.reason} but it contains {why}: cite it from the concept it "
                       "teaches unless it really holds no teaching content.")
    return out


def brief_problems(gen: GenBrief, chunks: set[str] | Mapping[str, str]) -> tuple[list[str], list[str]]:
    """(hard, soft) problems of a generated brief. ``chunks``: the source chunks as id -> text in source order (a
    set of ids works too and skips the teaching-prose check for skipped chunks)."""
    texts: Mapping[str, str] = chunks if isinstance(chunks, Mapping) else dict.fromkeys(sorted(chunks), "")
    chunk_ids = set(texts)
    hard: list[str] = []
    soft: list[str] = []
    if not gen.concepts:
        hard.append("`concepts` is empty: list the concepts the source teaches.")
        return hard, soft
    keys = [concept_key(c.key or c.name) for c in gen.concepts]
    seen: set[str] = set()
    for k in keys:
        if k in seen:
            soft.append(f"concept key '{k}' is used twice; keys must be unique.")
        seen.add(k)
    known = set(keys)
    graph: dict[str, list[str]] = {}
    for c, k in zip(gen.concepts, keys, strict=True):
        refs = list(c.source_refs) + [r for f in c.key_facts for r in f.source_refs]
        bad = sorted({r for r in refs if chunk_ids and r not in chunk_ids})
        if bad:
            soft.append(f"concept '{k}' cites unknown chunk ids {bad[:5]}; use ids from the Source chunks section.")
        if chunk_ids and not c.source_refs and not any(f.source_refs for f in c.key_facts):
            soft.append(f"concept '{k}' has no source_refs; cite the chunks that teach it.")
        deps = [concept_key(p) for p in c.prerequisites if p.strip()]
        unknown = sorted({d for d in deps if d not in known})
        if unknown:
            soft.append(f"concept '{k}' lists unknown prerequisites {unknown[:5]}; use keys of concepts in this brief.")
        if k in deps:
            soft.append(f"concept '{k}' lists itself as a prerequisite.")
        if not c.key_facts and not c.must_explain:
            soft.append(f"concept '{k}' has neither key_facts nor must_explain.")
        graph[k] = [d for d in deps if d in known and d != k]
    cycle = _cycle(graph)
    if cycle:
        soft.append("prerequisites form a cycle: " + " -> ".join(cycle) + "; a concept cannot depend on itself.")
    order = [concept_key(k) for k in gen.teaching_order if k.strip()]
    missing = [k for k in keys if k not in order]
    extra = sorted({k for k in order if k not in known})
    if missing:
        soft.append(f"teaching_order is missing {missing[:8]}; list every concept key once.")
    if extra:
        soft.append(f"teaching_order has unknown keys {extra[:8]}.")
    pos = {k: i for i, k in enumerate(order)}
    for k, deps in graph.items():
        late = [d for d in deps if d in pos and k in pos and pos[d] > pos[k]]
        if late:
            soft.append(f"teaching_order puts '{k}' before its prerequisites {late[:4]}.")
    bad_q = sorted({r.strip() for q in gen.source_questions for r in q.source_refs if chunk_ids and r.strip() not in chunk_ids})
    if bad_q:
        soft.append(f"source_questions cite unknown chunk ids {bad_q[:5]}; use ids from the Source chunks section.")
    if chunk_ids:  # every chunk cited or skipped (first, so the cap never drops it)
        soft = accounting_problems(gen, texts) + soft
    return hard, soft[:MAX_PROBLEMS]


def make_validator(chunks: set[str] | Mapping[str, str]):
    """``generate_json`` hook: every problem on the first pass, only hard ones afterwards."""
    calls = {"n": 0}

    def validate(gen: GenBrief) -> list[str]:
        calls["n"] += 1
        hard, soft = brief_problems(gen, chunks)
        return hard + soft if calls["n"] == 1 else hard

    return validate


def teaching_order(keys: Sequence[str], prerequisites: dict[str, list[str]],
                   preferred: Sequence[str] = ()) -> tuple[list[str], dict[str, list[str]]]:
    """A dependency-respecting order (closest to ``preferred``, then ``keys`` order) and the
    prerequisites that were kept (edges closing a cycle are dropped)."""
    rank: dict[str, int] = {}
    for k in preferred:
        if k in prerequisites:
            rank.setdefault(k, len(rank))
    for k in keys:
        rank.setdefault(k, len(rank))
    remaining = set(keys)
    kept = {k: [d for d in dict.fromkeys(prerequisites.get(k, [])) if d in remaining and d != k] for k in keys}
    placed: list[str] = []
    while remaining:
        ready = [k for k in remaining if not any(d in remaining for d in kept[k])]
        if ready:
            k = min(ready, key=lambda x: rank[x])
        else:  # a cycle: place the earliest concept and drop its unmet prerequisites
            k = min(remaining, key=lambda x: rank[x])
            kept[k] = [d for d in kept[k] if d not in remaining]
        placed.append(k)
        remaining.discard(k)
    return placed, kept


_NAME_KEY = re.compile(r"name|\bby\b|sme|faculty|author|expert|presenter|speaker|teacher|instructor|reviewer|editor|narrator|hod")
_HONORIFIC = re.compile(r"^(?:dr|prof|mr|mrs|ms|miss|er|shri|smt|thiru|tmt)\.?\s+", re.I)


def person_values(excluded: Sequence[ExcludedItem]) -> list[str]:
    """Names removed by the rules (SME / author / reviewer fields), used to scrub the brief.

    Only values that read as a person's name count ("Dr. A. Kumar", "Ravi Kumar"); a value such as
    "the Constituent Assembly" or "heating ammonium chloride" is never a name, so the brief keeps it.
    """
    out: list[str] = []
    for e in excluded:
        if e.category != "person" or ":" not in e.text:
            continue
        key, value = e.text.split(":", 1)
        if not _NAME_KEY.search(key.lower()):
            continue
        for part in re.split(r"[,;/&]|\band\b", value):
            if not looks_like_person(part):
                continue
            name = _HONORIFIC.sub("", part.strip()).strip(" .")
            if len(name) >= 4 and any(ch.isalpha() for ch in name):
                out.append(name)
    return out


def _scrubber(names: Sequence[str]):
    patterns = [re.compile(re.escape(n), re.I) for n in sorted(set(names), key=len, reverse=True)]

    def scrub(text: str) -> str:
        for p in patterns:
            text = p.sub("the author", text)
        return text

    return scrub


def _clean(text: str, limit: int, scrub: Any) -> str:
    return scrub(re.sub(r"\s+", " ", text or "").strip())[:limit]


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").casefold()).split())


def _repeats(text: str, others: Sequence[str]) -> bool:
    """``text`` is (nearly) the same as, or contained in, one of ``others`` (all normalised)."""
    if not text:
        return True
    for o in others:
        if text in o:
            return True
        m = difflib.SequenceMatcher(None, text, o, autojunk=False)
        if m.real_quick_ratio() >= 0.9 and m.quick_ratio() >= 0.9 and m.ratio() >= 0.9:
            return True
    return False


def skipped_from_gen(gen: GenBrief, ingest: IngestResult) -> list[SkippedChunk]:
    """The brief's skipped chunks, in source order: known ids only, never one the brief also cites, the first reason
    wins. A skip is ignored (and logged) when the chunk holds a definition, law, formula or worked example
    (``teaching_signal`` without the prose check), and a ``duplicate`` skip only stands when the chunk repeats a
    cited chunk (or one already set aside). None at all when they would set aside more than ``MAX_SKIPPED_SHARE`` of
    the source's text (a misread source)."""
    texts = {c.id: c.text for c in ingest.chunks}
    order = {cid: i for i, cid in enumerate(texts)}
    cited = gen_cited(gen)
    reasons: dict[str, Any] = {}
    for x in gen.skipped_chunks:
        cid = x.chunk_id.strip()
        if cid in texts and cid not in cited:
            reasons.setdefault(cid, x.reason)
    plain = {cid: _plain(t) for cid, t in texts.items()}
    kept_aside: list[str] = []
    out: list[SkippedChunk] = []
    ignored: list[str] = []
    for cid in sorted(reasons, key=order.__getitem__):
        reason = reasons[cid]
        if reason == "duplicate":
            others = [plain[c] for c in texts if c != cid and (c in cited or c in kept_aside)]
            keep = _repeats(plain[cid], others)
        else:
            keep = not teaching_signal(texts[cid], prose=False)
        if keep:
            out.append(SkippedChunk(chunk_id=cid, reason=reason))
            kept_aside.append(cid)
        else:
            ignored.append(cid)
    if ignored:
        log.info("the concept brief skipped %d chunk(s) that hold teaching content %s; they stay in the lecture",
                 len(ignored), ignored[:10])
    total = sum(len(t) for t in texts.values())
    if total and sum(len(texts[s.chunk_id]) for s in out) > MAX_SKIPPED_SHARE * total:
        log.warning("the concept brief set aside %d of %d chunks (most of the source); its skips are ignored",
                    len(out), len(texts))
        return []
    return out[:500]


def brief_from_gen(gen: GenBrief, ingest: IngestResult) -> ConceptBrief:
    """Canonical ``ConceptBrief``: unique snake_case keys, known chunk refs only, acyclic prerequisites,
    a dependency-respecting teaching order, clamped lengths, no author names, and the chunks it set aside
    (``skipped_from_gen``)."""
    chunk_ids = {c.id for c in ingest.chunks}
    scrub = _scrubber(person_values(ingest.excluded))
    used: set[str] = set()
    keymap: dict[str, str] = {}
    picked: list[tuple[str, Any]] = []
    for gc in gen.concepts:
        if len(picked) >= MAX_CONCEPTS:
            break
        if not (gc.name or gc.key).strip():
            continue
        base = concept_key(gc.key or gc.name)
        key, n = base, 2
        while key in used:
            key = f"{base[:60]}_{n}"
            n += 1
        used.add(key)
        keymap.setdefault(concept_key(gc.key or gc.name), key)
        keymap.setdefault(concept_key(gc.name), key)
        picked.append((key, gc))

    def refs(values: Sequence[str], limit: int) -> list[str]:
        return [r for r in dict.fromkeys(v.strip() for v in values) if r in chunk_ids][:limit]

    prereqs: dict[str, list[str]] = {}
    for key, gc in picked:
        deps = [keymap.get(concept_key(p)) for p in gc.prerequisites if p.strip()]
        prereqs[key] = [d for d in dict.fromkeys(deps) if d and d != key]
    keys = [k for k, _ in picked]
    preferred = [keymap[concept_key(k)] for k in gen.teaching_order if concept_key(k) in keymap]
    order, kept = teaching_order(keys, prereqs, preferred)

    concepts: list[BriefConcept] = []
    for key, gc in picked:
        facts = [
            BriefFact(text=_clean(f.text, 600, scrub), source_refs=refs(f.source_refs, 10))
            for f in gc.key_facts if f.text.strip()
        ][:20]
        own = refs(gc.source_refs, 20)
        if not own:
            own = refs([r for f in gc.key_facts for r in f.source_refs], 20)
        concepts.append(BriefConcept(
            key=key,
            name=_clean(gc.name, 160, scrub) or key,
            why_it_matters=_clean(gc.why_it_matters, 400, scrub),
            must_explain=[_clean(x, 400, scrub) for x in gc.must_explain if x.strip()][:12],
            key_facts=facts,
            examples=[_clean(x, 800, scrub) for x in gc.examples if x.strip()][:10],
            prerequisites=kept[key][:10],
            source_refs=own,
        ))
    excluded = [
        ExcludedItem(category=e.category, text=_clean(e.text, 300, scrub), reason=_clean(e.reason, 200, scrub), source="brief")
        for e in gen.excluded if e.text.strip()
    ][:100]
    return ConceptBrief(
        topic=_clean(gen.topic, 300, scrub),
        concepts=concepts,
        teaching_order=order[:MAX_CONCEPTS],
        source_questions=[_clean(q.text, 600, scrub) for q in gen.source_questions if q.text.strip()][:20],
        source_question_refs=refs([r for q in gen.source_questions for r in q.source_refs], 100),
        excluded=excluded,
        skipped_chunks=skipped_from_gen(gen, ingest),
        notes=_clean(gen.notes, 1000, scrub),
    )


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def brief_cache_key(system: str, user: str, model: str, files: Sequence[Any] = ()) -> str:
    """Content address of a brief: exact prompt + prompt version + model (+ attached files)."""
    digest = hashlib.sha256(f"{system}\x00{user}".encode()).hexdigest()
    return compute_key("brief", {
        "prompt": digest, "model": model, "prompt_version": load_prompt("brief").version, "cache": BRIEF_CACHE_VERSION,
        "files": [hashlib.sha256(f.data).hexdigest()[:24] for f in files],
    })


async def _cached(ctx: Any, key: str) -> ConceptBrief | None:
    asset = await asyncio.to_thread(ctx.assets.get, key, verify_blob=True)
    if asset is None:
        return None
    try:
        payload = await asyncio.to_thread(ctx.storage.get_bytes, asset.storage_key)
        return ConceptBrief.model_validate_json(payload)
    except (OSError, ValueError, KeyError):
        log.info("cached concept brief %s is unreadable; rebuilding", key)
        return None


async def _store(ctx: Any, key: str, brief: ConceptBrief, model: str) -> None:
    produced = Produced(data=brief.model_dump_json().encode("utf-8"), mime="application/json",
                        meta={"concepts": len(brief.concepts), "model": model})
    project_id = getattr(ctx, "project_id", None)

    def put() -> None:
        ctx.assets.put(key, "intermediate", produced, created_by=getattr(ctx, "user_id", None))
        if project_id:
            with ctx.session() as db:
                dbops.ensure_asset_refs(db, int(project_id), [key])

    try:
        await asyncio.to_thread(put)
    except Exception:  # noqa: BLE001 - a cache write must never cost the brief
        log.warning("could not cache the concept brief %s", key, exc_info=True)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


async def build_brief(ctx: Any, ingest: IngestResult, options: GenerationOptions) -> ConceptBrief:
    """Extract the concepts to teach (cached). Raises ``BriefUnavailable`` or provider errors; the
    orchestrator plans without a brief when this fails."""
    settings = ctx.settings
    llm = integrations.get_llm(settings, integrations.llm_engine(options, settings))
    model = brief_model(options, settings)
    files: list[Any] = []
    if ingest.attach_original:
        from .plan import original_attachment  # lazy: the planner may import this module

        files = await original_attachment(ctx, ingest, llm, purpose="concept brief")
    if not files and readable_chars(ingest.markdown) < MIN_BRIEF_CHARS:
        raise BriefUnavailable("the source has too little readable text for a concept brief")
    system, user = build_brief_prompt(ingest, options, attached=bool(files))
    key = brief_cache_key(system, user, model, files)
    cached = await _cached(ctx, key)
    if cached is not None:
        log.info("concept brief cache hit (%s)", key)
        return cached
    async with integrations.limit("llm"):
        gen: GenBrief = await llm.generate_json(
            model=model,
            system=system,
            prompt=user,
            schema=GenBrief,
            files=files,
            temperature=0.2,
            on_usage=ctx.record_usage,
            validate=make_validator({c.id: c.text for c in ingest.chunks}),
            validation_retries=2,
        )
    brief = brief_from_gen(gen, ingest)
    if not brief.concepts:
        raise BriefUnavailable("the model found no concepts in the source")
    await _store(ctx, key, brief, model)
    return brief


# ---------------------------------------------------------------------------
# offline responder (LLM_PROVIDER=fake, tests)
# ---------------------------------------------------------------------------

_SKIP_HEADING = re.compile(r"\b(?:objectives?|outcomes?|summary|recap|quiz|questions?|review)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def fake_brief_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Deterministic ``GenBrief`` from the prompt's source chunks: one concept per section, in order. Every chunk
    is accounted for: a lone title-page line is skipped as administrative, question sections are cited by their
    questions and everything else by a concept."""
    chunks = extract_json(prompt, "Source chunks") or []
    groups: dict[str, list[dict[str, Any]]] = {}
    questions: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    extra: list[tuple[int, str]] = []  # (index of the group before it, chunk id): sections cited by a nearby concept
    for c in chunks:
        heading = (c.get("heading") or "").split(" > ")[-1].strip() or "Overview"
        text = str(c.get("text") or "")
        if parse_title_line(text) is not None:
            skipped.append({"chunk_id": c["id"], "reason": "administrative"})
            continue
        if _SKIP_HEADING.search(heading):
            asked = [ln.strip() for ln in text.splitlines() if ln.strip().endswith("?")][:5]
            questions += [{"text": q, "source_refs": [c["id"]]} for q in asked]
            if not asked:
                extra.append((len(groups) - 1, c["id"]))
            continue
        groups.setdefault(heading, []).append(c)
    concepts: list[dict[str, Any]] = []
    prev: str | None = None
    for heading, items in list(groups.items())[:12]:
        key = concept_key(heading, f"concept_{len(concepts) + 1}")
        text = " ".join(str(i.get("text") or "") for i in items)
        lines = [ln.strip(" -*") for ln in re.split(r"\n+", text) if ln.strip()]
        sentences = [s for s in _SENTENCE.split(" ".join(lines)) if len(s.split()) >= 4]
        facts = [ln for ln in lines if "=" in ln and len(ln) <= 120][:3]
        concepts.append({
            "key": key,
            "name": heading[:1].upper() + heading[1:].lower() if heading.isupper() else heading,
            "why_it_matters": sentences[0][:300] if sentences else "",
            "must_explain": [s[:200] for s in sentences[:3]],
            "key_facts": [{"text": f, "source_refs": [items[0]["id"]]} for f in facts],
            "examples": [s[:200] for s in sentences if re.search(r"\bexample|consider|imagine\b", s, re.I)][:2],
            "prerequisites": [prev] if prev else [],
            "source_refs": [i["id"] for i in items][:20],
        })
        prev = key
    if concepts:  # sections without a concept of their own: cited by the nearest concept
        for items in list(groups.values())[12:]:
            concepts[-1]["source_refs"] += [i["id"] for i in items]
        for before, cid in extra:
            concepts[min(max(before, 0), len(concepts) - 1)]["source_refs"].append(cid)
    return {
        "topic": concepts[0]["name"] if concepts else "",
        "concepts": concepts,
        "teaching_order": [c["key"] for c in concepts],
        "source_questions": questions[:20],
        "skipped_chunks": skipped,
        "excluded": [],
        "notes": "",
    }
