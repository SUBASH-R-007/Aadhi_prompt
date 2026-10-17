"""A default ``Screenplay.presentation`` for a newly generated lecture (DYNAMIC_VIDEO_DESIGN §6.7, §10.3 B4).

``Screenplay.presentation`` is what selects the Dynamic video mode: ``compose.timeline.build_timeline`` builds a
Dynamic timeline only for a screenplay that has one, and only while ``DYNAMIC_ENABLED`` is on
(``compose.presentation.dynamic_enabled``). Until this module, nothing in the pipeline wrote one: a presentation could
reach a lecture only through an edit or an import (schema v3, write-gated by ``DYNAMIC_FEATURES``), so a freshly
generated lecture was always classic, whatever the flag said.

**This is the deadline slice, not B4.** Batch B4 ("First release: Dynamic lesson and reel end to end") owns the real
thing: ``dynamic_plan.md`` / ``dynamic_scene.md``, the Dynamic ``GenerationOptions``, and the rest of the rotation
inputs (check order, greeting, intro layout, card position). What is here is the smallest honest default that makes
**generate -> Dynamic MP4** work end to end:

* every value is a released R1 one, so the resolver never has to fall back (``themes.FAMILIES``, the catalogue's
  ``captions``, ``templates.py``'s ``hook_question`` variants);
* ``look_seed`` is derived from the lecture's own identity with SHA-256 (never ``hash()``, which is salted per
  process), so the same lecture always gets the same look and the director breaks its ties the same way;
* the look ROTATES on that seed (owner decision, 2026-10-15), so two lectures do not look alike: the theme family,
  the motion personality and the hook kind are drawn from :data:`POOLS`, each from its own hash stream, and the
  caption and cue styles follow the personality the design pairs them with (:func:`rotated`);
* the narrator is the voice the teacher already chose for the lecture (``GenerationOptions.tts_voice``);
* music is ``off`` and no second voice is cast, because casting and the music plan are B3's, not this slice's.

**What does not rotate yet, and why.** ``display_font`` stays ``outfit``: ``faces.resolver_fonts`` maps a display id
the B1 stage never drew to ``outfit``, so storing ``poppins`` would change the presentation and nothing on screen.
Rotating the display face is the resolver's (B2e, design §4.6), and it also needs the live player to preload
``stageFontRequests`` for a non-B1 face (§7.3). ``check_order`` (the seeded permutation of check kinds, §4.3) waits
for the ``check`` template, and ``greeting_id`` / ``intro_layout`` / ``reel_card_x`` for ``aadhi_intro``.

Nothing here runs for a classic build: the pipeline imports this module only when the flag is on.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ...schemas.screenplay import AssetStyle, MusicChoice, Presentation, VoiceCast

__all__ = ["DEFAULTS", "POOLS", "default_presentation", "look_seed_for", "narrator_for", "pick", "rotated"]

# The released R1 values that do NOT rotate (see the module docstring for why). Checked against the catalogue by
# ``tests/compose/dynamic/test_authoring.py``, so a rename fails a test instead of silently making every generated
# lecture fall back.
DEFAULTS: dict[str, str] = {
    "display_font": "outfit",  # faces.py; what the resolver stores for any display id the B1 stage did not draw
}

#: What rotates on the lecture's look seed (design §4.4 "bounded variety"; owner decision, 2026-10-15). Every value is
#: a released R1 one, pinned against the catalogue by the tests, so a rotation can never pick something that falls
#: back. B4 owns the rest of the rotation inputs.
POOLS: dict[str, tuple[str, ...]] = {
    # The four R1 families, two dark and two light, so two lectures differ at a glance (§4.7).
    "theme_family": ("midnight_indigo", "deep_teal", "paper_ink", "mint_studio"),
    # The two R1 personalities: duration scale 0.85 / 1.15 and different text entrances (§4.8).
    "personality": ("crisp", "soft"),
    # hook_question's variants without 'mystery', which is deferred to B4 (deviation 16) and would fall back.
    "hook_kind": ("question", "true_false", "stakes", "challenge"),
}
#: The look seed is an int field; keep it inside a comfortable 2^31 so every store and JSON reader is happy.
_SEED_MOD = 2_000_000_000


def look_seed_for(*parts: str) -> int:
    """A stable look seed for a lecture, from its identity.

    SHA-256 of the parts, never :func:`hash` (salted per process, so the same lecture would get a different look in
    every worker, and the director's §4.4 tie-break would not be reproducible).
    """
    digest = hashlib.sha256("\x1f".join(p.strip() for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % _SEED_MOD


def pick(pool: str, seed: int, epoch: int = 0) -> str:
    """The rotated value of ``pool`` for a lecture, deterministically.

    Each pool draws from its OWN SHA-256 stream, keyed by the pool's name, so two pools never move together (a seed
    that lands on the second family must not also land on the second personality). ``epoch`` is
    ``Presentation.look_epoch``, which Fresh look / New colours bump, so those re-roll while a plain rebuild of the
    same lecture never does (design §6.8).
    """
    options = POOLS[pool]
    digest = hashlib.sha256(f"{pool}{seed}{epoch}".encode()).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


def rotated(seed: int, epoch: int = 0, fmt: str = "landscape") -> dict[str, str]:
    """Every look value a lecture's seed decides: the three rotated pools plus the two the personality pairs.

    ``caption_style`` and ``cue_term_style`` are NOT rolled separately: the design's personality table already pairs
    them with the personality (``themes.Personality``), and rolling them apart would store a pairing the design does
    not have - Crisp with a marker cue, or a 9:16 Crisp lecture with ``clean_pill`` instead of ``bold_outline``.
    """
    from .themes import PERSONALITIES  # lazy: a classic build must not pull the theme tables

    personality = pick("personality", seed, epoch)
    row = PERSONALITIES[personality]
    return {
        "theme_family": pick("theme_family", seed, epoch),
        "personality": personality,
        "hook_kind": pick("hook_kind", seed, epoch),
        "caption_style": row.caption_style[fmt],
        "cue_term_style": row.cue_term_style,
    }


def narrator_for(voice: str | None, provider: str | None, language: str | None) -> str:
    """The narrator voice to cast: the teacher's own choice, else the provider's default for the language.

    ``VoiceCast.narrator`` may not be empty, and a default presentation must never be the reason a generation fails,
    so an unknown provider falls back to Edge's default for the language rather than raising.
    """
    chosen = (voice or "").strip()
    if chosen:
        return chosen
    from ...providers.tts.voices import default_voice  # lazy: only a Dynamic generation needs the voice table

    for prov in ((provider or "").strip().lower(), "edge"):
        try:
            return default_voice(prov, language or "en")
        except KeyError:
            continue
    return "en-IN-NeerjaNeural"  # Edge's en-IN default; unreachable while 'edge' stays a provider


def narration_choice(voice: str | None, requested: str | None, settings: Any) -> tuple[str | None, str]:
    """The narrator's ``(voice, provider)`` for :func:`narrator_for`, picked as the asset build picks the lecture's
    (``aadhi.pipeline.assets._Builder._tts_provider``).

    The provider is the one that will narrate (``providers.factory.narrating_tts_provider``): the lecture's choice,
    else the server's ``TTS_PROVIDER`` (OpenAI TTS by default), else the free Edge voice when neither has an API key
    for this job (``settings`` are the job's, with the owner's personal keys). The lecture's ``voice`` is kept only
    when it is a voice of that provider (``voices.voice_fits``, as ``aadhi.pipeline.assets.voice_config`` keeps it): a
    voice saved for another provider (an Edge voice saved on "Server default" before OpenAI became the default or got
    its key, an OpenAI voice while OpenAI has no key) gives None, so the narrating provider's default for the
    language is cast.
    """
    from ...providers.factory import narrating_tts_provider  # lazy, like narrator_for's voice table
    from ...providers.tts.voices import voice_fits

    provider = narrating_tts_provider(requested, settings)
    chosen = (voice or "").strip()
    keep = bool(chosen) and voice_fits(provider, chosen)
    return (chosen if keep else None), provider


def default_presentation(
    *,
    subject_name: str = "",
    unit_name: str = "",
    session_number: str = "",
    session_title: str = "",
    narrator_voice: str | None = None,
    tts_provider: str | None = None,
    language: str | None = None,
    fmt: str = "landscape",
    **overrides: Any,
) -> Presentation:
    """The default presentation of a newly generated lecture, or ``overrides`` on top of it.

    ``fmt`` is the video format (``landscape`` for a lesson; a reel is ``portrait``, which B4's options choose).
    ``narrator_voice`` is ``GenerationOptions.tts_voice``; without one the provider's default for ``language`` is
    cast (:func:`narrator_for`), because ``VoiceCast.narrator`` may not be empty. B3's casting owns the real cast.
    """
    seed = look_seed_for(subject_name, unit_name, session_number, session_title)
    epoch = int(overrides.get("look_epoch", 0) or 0)
    values: dict[str, Any] = {
        "style": "dynamic",
        "format": fmt,
        "look_seed": seed,
        "display_font": DEFAULTS["display_font"],  # pinned until the resolver rotates faces (module docstring)
        # B3 casts the real voices (and the Aadhi and student voices); this keeps the teacher's own narrator choice.
        "voices": VoiceCast(narrator=narrator_for(narrator_voice, tts_provider, language)),
        # No music until B3's music plan: 'off' is a first-class source, not a placeholder.
        "music": MusicChoice(source="off"),
    }
    values.update(rotated(seed, epoch, fmt))
    values.update(overrides)
    # The asset style follows the family and personality the lecture ENDS UP with, so an override of either (the demo
    # renders a named family) is not left with the rolled one beside it.
    if "asset_style" not in overrides:
        values["asset_style"] = AssetStyle(family=values["theme_family"], personality=values["personality"])
    return Presentation.model_validate(values)
