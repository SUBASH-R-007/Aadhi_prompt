# B2 engineering plan (draft for review) — Visual system and the R1 templates

Source of truth for WHAT: docs/DYNAMIC_VIDEO_DESIGN.md §4 (visual system), §2.5 (zones), §2.6/§4.11 (captions), §3.3 (Aadhi
compositions), §6.7 (schema), §6.8 (deterministic stages), §7.3-§7.7 (stage contract, determinism), §10.3 B2 (scope,
contracts, tests, acceptance). This plan decides HOW: exact contracts, module boundaries, order, ownership, tests.

Ground truth from the B1 code read (all verified): the stage is `web/js/dynamic/stage.js` (Slot per scene: background
build/update, template build/layout/update/texts/focusRect, presenter, media via ctx.media, Painter-hashed writes);
`board_generic.js` is the only template (1,100 lines, card stack, quiz, media split, title card); `presentation.py` is the
B1 stand-in resolver (hard-coded 4 families, board_generic only, mesh_drift/colour_block, cut/crossfade). Preflight remedies
use `ResolvedLook.variant = "safe"` and `ResolvedLook.text_fit[slot] = {size, scrim}`. Zones exist in three copies
(core.js ZONES, preflight.py bands, a test). Colour maths exists in three copies (motion/color.js, presentation.py,
compose/dynamic/colour.py). Pillow reads the vendored WOFF2 on the dev machine (no raqm).

## 0. Principles for B2

1. One source of truth per catalogue, in Python; the stage reads a GENERATED, committed JS module, checked in CI
   (`python -m aadhi.compose.dynamic.catalogue --check`, like `aadhi.schemas.generate --check`). A JS module rather than the
   design's `web/dynamic/themes.json`: no JSON import attributes needed, CSP-safe, type-checked by tsc, scanned by the source
   scan; recorded as an as-built deviation. Name: `web/js/dynamic/catalogue.gen.js` (header "generated — do not edit").
2. Python decides WHEN (events.py, kinetic.py) and WHAT (direct.py, resolver); JS decides HOW (templates, cues, backgrounds,
   transitions). Every resolved choice is stored in the timeline (old timelines keep their look).
3. Determinism gates every visual change: the source scan, the order-independence test (1xN/3xN/reverse), and the new
   gallery fixtures run through it. Classic output never changes (invariance goldens).
4. No LLM anywhere in B2. Pure functions of (screenplay, presentation seeds, format, history input).

## 1. Contracts frozen by B2 (exact)

### 1.1 `ResolvedLook` (aadhi/schemas/timeline.py) — additive, all excluded at defaults
```
template: str                       # catalogue id (always a released template; never an unknown id)
variant: str | None                 # the template's own variant (direct.py); never "safe" any more
tokens: dict[str, str]              # structure tokens: struct.accent, bg, bg-alt, bg-tint, surface (hex)
background: {preset, params, seed, frozen_at}   # params: {tone, ...preset params}; frozen_at also set by template holds
transition_in: {kind, seconds, dir?, anchor?}   # kind in cut|crossfade|push|colour_wipe|morph; dir for push; anchor for morph
hold_reason: str | None
presenter: {clip_key, clip, crop:[x,y,w,h], rect:[x,y,w,h], t_exit, src_fps, url?} | None   # url filled at serve time
text_fit: dict[slot, {size, lines}]  # textfit.py (Python measurement) — owned by the resolver
fixes: dict = {}                    # NEW: preflight-owned remedies {safe: bool, slots: {slot: {size?, scrim?}}}
icons: dict[str, {name, paths:[...], filled}] = {}   # NEW: item id -> resolved icon geometry (icons.py)
entities: dict[str, int] = {}       # NEW: per scene, normalised symbol/entity -> slot (from Presentation.entity_colors)
continues_from: str | None = None   # NEW: scene id whose final state part B continues
```
Migration: preflight writes `fixes` instead of `variant="safe"`/`text_fit[slot]` (B1 remedies were never persisted, only
applied to the served copy for one render, so no stored data changes). `slotTuning` in templates reads `fixes`.

### 1.2 `TimelinePresentation` — additive
`content` (unchanged), `fonts` {display, body, mono, indic?}, `personality`, `caption_style`, `cue_term_style`,
`motion_scale` NEW (personality duration scale, 0.85/1.15), `family` NEW (id, for analytics/gallery), `mode` NEW
("dark"|"light"), `engine_version` = 2 (stage contract unchanged at v3; engine_version bumps because templates changed).

### 1.3 Template contract (JS) — `web/js/dynamic/templates/types.js`
```
export default {
  id, build(ctx), layout(ctx), update(ctx, t, state), texts(ctx),
  focusRect(ctx, target) -> rect|null,     // cue.look_here geometry (item id, "item:part", "panel")
  itemEl(ctx, itemId) -> Element|null,     // cue.term finds [[term]] spans (.rt-k) inside it
  anchor(ctx) -> {el, rect}|null,          // shared-element morph (optional)
  mediaBox(scene, zones, mode) -> {w,h}|null  // static: largest media box (mediaPlan extracts at this size)
}
ctx += { cues, motion, catalogue, icons }   // motion = personality tokens (durations scaled, entrance kind)
```
Stage-driven cues (decision): the STAGE applies `cue_term` / `look_here` events from `scene.events` generically through
`cues.js` using the template's `itemEl`/`focusRect`; templates never draw emphasis themselves (source scan keeps banning
mark/focus/outline/underline in templates). `ctx.cues` exposes the same API for the rare template-driven cue.

### 1.4 cues.js API
`createCues({root, roles, presentation, paint})` -> `{update(t, events, hooks), term(...), lookHere(...)}`.
- `cue.term`: one style per video (`presentation.cue_term_style`: marker | underline), drawn on the term span itself via
  background-image gradients (marker sweep 300 ms behind text; underline 4 px drawn left to right), mark colour from roles.
- `cue.look_here`: outline (4 px `focus` rounded rect drawn in 250 ms in a cue layer above content; others dimmed to 70%
  with a box-shadow hole) or push (camera translate 2-6% toward the target; only where the template spec allows; never on
  formula scenes, R-30). Punch-zoom = push at 10% in 250 ms, <= 1 per 20 s (enforced by events.py).
- Limits are Python's (events.py); cues.js draws what it is given, deterministically, as a pure function of (t, events).

### 1.5 `TemplateSpec` (aadhi/compose/dynamic/templates.py)
```
@dataclass(frozen=True) class TemplateSpec:
  id, semantic_class, representational: bool, release: "R1"|"W2"|"B6", formats: frozenset,
  variants: tuple[str,...], forced_for: frozenset[str], duration: {fmt: (min,max)}, holds: frozenset[str],
  dense: bool (freeze background, no SFX, music off in gaps_bookends), push_ok: bool (camera push allowed),
  fit: Callable[[Scene, fmt], float], events: Callable[...] (template-specific event extras; most use the generic builder)
```
All 41 templates listed (released flag gates selection); the JS registry must match ids/classes/variants/formats/release
(parity test through the generated catalogue).

### 1.6 Generated catalogue (`web/js/dynamic/catalogue.gen.js`)
`{version, zones, content:{dark, light}, families:{id:{mode, hue, accent, chroma, release, personality}}, personalities,
caption_styles, display_faces, motion_tokens, templates:{id:{class, variants, formats, release, dense}},
backgrounds:{id:{release, personalities}}, transitions:{id:{release, seconds:[min,max]}}}`.

### 1.7 Zones (aadhi/compose/dynamic/zones.py) — the single source
Everything in design §2.5 and R-59: per format critical boxes, caption band, header/title/body/textColumn regions, the 9:16
hook band (y 340-760), one-line headline (y 340-440) + stage (y 460-1040) and the tall variant (y 340-1040), text columns
above/below y 840, the platform zones (YT_SHORTS, META_REELS, TIKTOK, UNIVERSAL), end-screen space (16:9 right half for
the last 15 s), `contain_blur` clip box (y 360-968 in 9:16). Preflight, core.js (via catalogue) and tests read it.

## 2. Sub-batches (in order; each = implement -> adversarial review -> fix -> full gates -> commit)

### B2a — Foundations (contracts; no visible change except refactors)
Owner A (Python): `aadhi/compose/dynamic/{colour_math.py (single OKLCH + CSS Color 4 gamut mapping port of color.js, +
contrast, + composite), themes.py (content palette, 4 R1 families + 8 W2 families as disabled data, structure tokens, accent
rule, personalities Crisp/Soft + W2 data, caption styles, display faces, validator incl. Machado CVD + hue separation +
contrast vs every background/surface/preset worst case), zones.py, templates.py (41 TemplateSpecs, R1 fit() functions),
catalogue.py (generator + --check)}`, schema additions §1.1/§1.2 (+ regenerate web/schemas, JSDoc types), preflight
migration to `fixes` + zones.py, presentation.py switched to themes.py (B1 outputs for the fixture must stay
pixel-equal or the change must be deliberate and noted), CI step for the catalogue check.
Owner B (JS): `catalogue.gen.js` consumers: core.js roles/zones from the catalogue (Python and JS colour maths equal on
golden vectors), board_generic reads `fixes`, templates/types.js contract §1.3, `cues.js` (API + both cue styles + outline +
dim + push) with stage-driven application from events, the `mediaBox` hook in stage.mediaPlan, personality motion tokens in
ctx.motion.
Tests: palette pinned (contrast >= R-53 on every background of its mode, hue separation >= 30°, CVD >= 0.05), JS/Python
colour parity vectors, catalogue --check, template registry parity, zones single-source (no literal zone numbers left in
preflight/tests), cues unit tests + source scan, determinism on the QA fixture unchanged.

### B2b — Assets: fonts, icons, text fit
Fonts vendored through scripts/vendor.mjs with LICENSE files (Poppins, Lexend, Atkinson Hyperlegible Next, Anek variable
per script, existing Inter/Outfit/JetBrains Mono/Noto), weights actually used, unicode subsets kept (fix the subset filter
that would drop new scripts), THIRD_PARTY_NOTICES generated (design §4.12, §7.13 promise it). Icons: lucide-static +
@tabler/icons vendored (only SVG path data; licences), `aadhi/compose/dynamic/icons.py` offline index (Lucide tags, Tabler,
deterministic match of `icon_hint`, no match -> no icon), resolver writes `ResolvedLook.icons`. `textfit.py`: Pillow
FreeType measurement with the vendored WOFF2 (4% slack, 8% Indic), size+line-break choice per slot, used by direct.py lints
and the resolver's `text_fit`; WOFF2 check + TTF fallback path documented for the container.
Tests: vendor manifest/notices, icon index determinism and sanitisation, textfit vs Chromium measurement on fixtures (slow).

### B2c — Time engines: events.py and kinetic.py
`aadhi/compose/dynamic/events.py`: reveal/fill (anchor word or beat start, -120 ms lead, R-17), cue_term (one per narrated
key term on its word), look_here (from sync cues focus/emphasis, rows, bars, lines), var/output/count/draw/countdown/answer,
anchored flag (target named within +-300 ms), R-26 limits (<= 2 concurrent, <= 1 new per 2 s, no repeat on an item within
4 s, punch <= 1 per 20 s), R-49 SFX (bumper whoosh, answer chime, countdown-end tick 150 ms before the reveal; none in dense
scenes; never within +-150 ms of a key term; personality/format accent budgets), `Timeline.sfx` cues (B3 mixes them; the
B1 engine keeps today's audio until B3). `kinetic.py`: phrase chunks for BOTH formats into `TimedScene.kinetic_captions`
(9:16 <= 32 chars, <= 2 lines measured to 660 px at the style size, 1.5-3.0 s, >= 1.0 s hard, merges across beats in a
scene; 16:9 <= 42 chars/line, <= 2 lines, >= 1.0 s), Netflix break rules (never split article+noun, number+unit,
auxiliary+verb; bottom-heavy), R-33 reading rate, blanking at >= 70% token overlap with on-screen text (aadhi_intro hook,
hook_question, kinetic_statement, quote_card), key terms (LexiconEntry + [[term]]) flagged on their first two mentions and
never while the same term carries a board cue; Timeline.captions/sidecars stay complete (blanked chunks still in SRT/VTT).
Wire both into build_timeline for Dynamic only; classic unchanged.
Tests: R-17 anchoring, R-26 limits, R-49 placement, chunker R-32/R-33/Indic (Tamil, Devanagari, Telugu)/blanking/
first-two-mentions, measured widths vs Chromium (slow), invariance.

### B2d — direct.py and the resolver
`aadhi/pipeline/direct.py`: class signal table (§4.4), released templates only, fit() > 0, scoring formula, forced
templates exempt, board_generic board-only, bounded post-pass for distinct >= min(5, classes), R-20/R-23/R-28, variants
(media side changes only at chapter boundaries), one background preset per video (+ per chapter for builds_on=false or
energy Lively), per-scene tone variant, transitions per boundary (§4.9 rules, R-23/R-24), accents per chapter (16:9) or act
(9:16), entity colour mapping (formula variables then compared entities, 3 slots), hold_reason (formula/code/diagram/think),
text fit via textfit, look locks (auto/settled/pinned), family/personality/display font/caption style per video with a
`history` input (B3's history.py supplies it later; empty now), look_seed rule (§4.4). Called: at generation (B4 wires it),
on PUT for auto/unfit looks (B5), and by build_timeline for scenes whose look is missing or `auto` (not stored; pure).
`aadhi/compose/presentation.py` becomes the resolver of §1.1 using themes/zones/textfit/icons/events/kinetic; R-54 crossfade
>= 300 ms always; chapter detection = build_chapters' rules (fixes the B1 mismatch).
Tests: the §4.4 property tests (determinism, post-pass termination, forced templates kept for consecutive quiz/clip/
ai_video/simulation, board_generic never on non-board, swaps never cross classes, editing one scene's text never changes
another scene's settled look), plus resolver goldens.

### B2e — Stage: backgrounds, transitions, personalities
Backgrounds dot_field, paper_grain (vendored grain PNG -> generated seeded tile like colour_block), soft_waves (SVG),
spotlight; all with hold freeze + 30% desaturation, colors() for the analytic contrast, low salience (R-55).
Transitions push (direction = progress), colour_wipe (accent block, only into chapter_bumper / out of aadhi_intro), morph
(shared element; translate + crossfade, no text scaling unless the determinism test proves it safe). Personalities Crisp/
Soft drive durations, text entrance, transition family, cue style, caption style.
Tests: unit + determinism (each background and transition in the order-independence test) + flash safety (R-54).

### B2f — The 22 new R1 templates (+ board_generic kept as the fallback)
Shared helpers first (`templates/_shared.js`: title block with word entrance, card, rich text, icon, ring, stack/compact
stack, fit loops, texts() helpers) extracted WITHOUT changing board_generic output (determinism + stage tests prove it).
Groups (parallel, disjoint files): (1) bookends: aadhi_intro (4 x 16:9 layouts + 9:16 card), aadhi_recap, hook_question,
key_terms, chapter_bumper (mini-roadmap, numeral, shared anchor chip), recap_retrieval, outro_next, thumbnail;
(2) explanation: definition_card, kinetic_statement, list_build, media_split, formula_focus, worked_steps;
(3) code/data/practice: code_walkthrough, chart_build (Chart.js animation off, data interpolated per frame), myth_fact,
predict, check; (4) forced: full_bleed_media (Ken Burns, contain_blur), animation_stage (Manim clip + step lower thirds),
interactive_poster. Presenter: Python resolver sets ResolvedLook.presenter (clip, crop, rect, t_exit derived from Aadhi's
last word + 0.3 s, src_fps 24), serve-time url, engine extracts presenter frames once per clip/crop/size and caches them
as branding derivatives (§7.7).
Each template: both formats, safe variant for preflight `fixes.safe`, texts() for every text box, focusRect/itemEl/anchor,
mediaBox, Tamil/Hindi text, longest allowed text, no emphasis code, no clock.

### B2g — Gallery, integration, docs
`web/dynamic/gallery.html` (dev-only; not served in production — gate the route / exclude from the image) rendering every
template x format x the 4 families with the longest allowed text and Tamil/Hindi, from fixtures generated by Python;
gallery golden frames (Windows now; Docker when available); owner checklist. Update render_parity fixture, INTERFACES,
API, ARCHITECTURE, OPERATIONS, PROMPTS (no prompt changes in B2), design doc as-built.

## 3. Open questions for the reviewers
Q1. Generated JS module instead of web/dynamic/themes.json — acceptable deviation?
Q2. Stage-driven cues (templates expose itemEl/focusRect) vs template-driven ctx.cues — which better satisfies §4.2/R-26?
Q3. build_timeline running direct.py for scenes with missing/auto looks — consistent with §4.4/§6.8 locks?
Q4. kinetic_captions in 16:9 too (needed for blanking R-31 and key terms R-35 in both formats) — right?
Q5. Storing icon geometry and per-scene entities in ResolvedLook (timeline self-contained) — right?
Q6. engine_version bump to 2 for B2 timelines — right, and what must the engine/player do with v1 timelines?
Q7. Anything in design §4/§10.3 B2 this plan misses or gets wrong?
