# B2 engineering plan v2 — decisions after the three-lens critique

v1 is `b2_plan.md`; critiques are `b2crit_{design,engineering,quality}.md`. This v2 records every decision. Where the design
is changed, the change goes into the DEVIATION REGISTER (§D) and into DYNAMIC_VIDEO_DESIGN.md's B2 as-built notes.

## A. Sequencing (each step: implement -> adversarial review -> fix -> full gates -> commit)

0. **B2-0 spike (measurement only, no product code; results decide policy §P).** Real page, 1xN / 3xN / reverse hashes
   (tests/determinism harness, --disable-partial-raster now standard) on one fixture per class:
   (a) scaled text container 0.90-1.10 (camera push / punch-zoom); (b) scaled inline SVG icon (pop 1.08);
   (c) rotateX 90->0 card (tile flip); (d) scaled <img> of a /__frames JPEG, only ever downscaling from >= final size
   (presenter settle 0.96->1, media 0.96->1; Ken Burns was already measured order-independent by the B1 pass);
   (e) scaled background tile (paper_grain +-1%, spotlight +-3%); (f) canvas drawImage crop window (fallback for a/d);
   (g) MathJax `\color{#hex}` survives `ui/safe` and is addressable as `g[fill]` in the SVG; (h) Chart.js built with
   animation:false, responsive:false, events:[], devicePixelRatio:1, update('none') per frame: order-independent;
   (i) a text layer with `background-clip:text` gradient sweep (cue.term dark-family glyph switch).
   Output: a table class -> allowed | whole-pixel only | replaced-by; written into §P and the design's §4.8 as-built.
1. **B2a contracts & foundations** (no visible change for B1 timelines: proven by a same-machine differential frame diff of
   the QA fixture and showcase before/after). Python generator committed first, JS consumers second.
2. **B2b assets**: fonts (+ face table), icons, textfit.
3. **B2c vertical slice**: `_shared.js`, `state.js`, cues.js (stage-driven), the minimal events needed, and three pilot
   templates through EVERY gate: formula_focus (MathJax, var tints, hold freeze), media_split (clip, mediaBox, look_here),
   list_build (per-item reveal, cue.term, 9:16 compact stack). Reference frames produced for owner review.
4. **B2d time engines complete**: events.py, kinetic.py (+ Dynamic caption split), preflight sampling/type-floor/caption
   path, reading_windows/static_gaps.
5. **B2e direct.py + resolver** (acts/segments, palette_slot, accents, entity map, transitions per boundary, looks refs,
   Aadhi timing), picking only templates whose JS exists (catalogue `implemented` flag checked by parity test).
6. **B2f stage completion + remaining 19 templates**: backgrounds (dot_field, paper_grain, soft_waves, spotlight),
   transitions (push, colour_wipe, morph overlay), personalities; templates in parallel groups after the pilot, with the
   wipe/morph landing alongside chapter_bumper/aadhi_intro/worked_steps; presenter crop + cache; Chart.js chart_build.
7. **B2g gallery, kitchen-sink lesson + reel MP4s, laptop benchmark re-run on the §7.10 templates, docs, deviation register.**

## P. Transform / determinism policy (B2-0 MEASURED 2026-10-11, Windows, chrome-headless-shell, --disable-partial-raster)
Spike (scratchpad/spike): 90 frames per case, 1x90 vs 3x30 on fresh pages vs reverse, CDP JPEG q92 SHA-256:
(a) scaled text 0.90-1.10: 0 mismatches; (b) SVG icon pop 1.08: 0; (c) rotateX 90->0: 0; (d) <img> downscaled 0.96-1
from a larger source: 0; (e) scaled grain tile +-1%: 0; (f) canvas drawImage crop: 0; (g) MathJax \color under ui/safe:
0, fills addressable (#0FE3FE/#C4A4FE/#FEACE3 found as fill attributes); (h) Chart.js animation:false, update('none'): 0;
(i) background-clip:text marker sweep: 0.
POLICY: the design's scale/3D motion is ALLOWED as specified (camera push 2-6% = scale, punch-zoom 1.10, settles,
media 0.96->1, Ken Burns, rotateX flips, pop on icons, background breathing) — B1's "never scale" rule was a symptom of
partial raster, now off. Conditions: (1) pictures only ever DOWNSCALE from a source >= their largest displayed size
(extraction/derivatives sized accordingly); (2) translates on whole pixels; (3) every template fixture still runs the
1x/3x/reverse gate (Windows now, container when available). No deviation needed for motion; the Linux confirmation is open.
Scan additions: templates may not write `will-change`, `filter`, `backdrop-filter`, `outline`, `text-decoration` through
ANY path (incl. ctx.paint.style), nor read `.timeline`.

## 1. Contracts (frozen by B2)

### 1.1 Catalogue (replaces "themes.json schema"; generated JS module `web/js/dynamic/catalogue.gen.js` + `--check`)
Canonical, sorted, byte-deterministic; `--check` in the Python CI job; shape test pins its schema/version.
Runtime use is GEOMETRY ONLY (core.js reads zones, grid/regions, slots, type floors, layout tokens, caption-style
metrics). Colours, durations and personality values reach the stage ONLY through the timeline (old timelines keep their
look). Families/personalities stay in the module for the gallery and parity tests.
Contents: version; zones (all §2.5 regions, both formats, platform zones, hook band, 9:16 headline/stage/tall, clip box
y 360-968, end-screen half); grid (16:9 12 col, 24 px gutters, named regions full | left-55+right-45 | centre-60 |
top-band+stage); type floors (R-29) and type scale; layout tokens (8 px spacing scale, radii card 24 / media 24 / chip
pill, structure border 4-6 px, shadow levels, icon sizes, dim alpha 0.7, scrim alpha >= 0.55); caption-style metrics
{padding_x, outline_px, word_spacing_em, size, weight, line_height per script}; per-mode cue colours {marker_ink,
marker_alpha, underline}; template registry {class, variants, formats, release, implemented, dense, push_ok, slots,
consumes, holds, duration}; backgrounds/transitions/personalities registries; the semantic-class table (§1.8); the role
list (§1.9); scrims table {preset: {family: bool}}; studio surface token.

### 1.2 ResolvedLook (additive; excluded at defaults)
template, variant (direct.py's; never "safe"), tokens (ALL derived structure colours computed in Python as hex: bg, bg-alt,
bg-tint, surface, struct.accent, block.base/highlight, block.ink/ink2, scrim, accent_prev for the R-52 fade),
background {preset, params, seed, frozen_at}, transition_in {kind, seconds, dir?}, hold_reason, presenter {clip (logical
id), crop [x,y,w,h], rect [x,y,w,h], t_exit, exit_kind, src_fps; url at serve}, text_fit {slot: {size, lines}} (Python,
conservative upper bound), fixes {safe: bool, slots: {slot: {size?, scrim?}}} (preflight-owned), icons {item_id: {name,
set, version, viewBox, stroke_width, filled, paths[d]}} (normalised to path d, capped, sanitised both sides),
refs {roadmap: [{title, state}], anchor: {kind, text|tex, icon?}, prediction: {text, resolve_t?}, next: {title},
accent_next} (cross-scene data denormalised by the resolver), accent_fade (s, 0.6-1.0 when the accent changes without a
wipe), continues_from.
Legacy: the stage still reads B1 `variant: "safe"` / `text_fit[slot].scrim` for engine_version 1 timelines.

### 1.3 TimelinePresentation (additive)
content (video's mode) + content_alt (other mode: code editor surface in light families), fonts {display, body, mono,
indic}, family, mode, personality, motion_scale, caption_style, cue_term_style, entity_colors (lesson-wide, R-30),
engine_version = 2 (= look-engine generation; stage contract stays aadhiStage v3). current_timeline rebuilds a Dynamic
timeline of the current revision whose engine_version < ENGINE_VERSION; the stage refuses engine_version above known with
timeline_invalid and feature-detects fields (never branches on version).

### 1.4 Template contract (JS)
```
export default {
  id, build(ctx), layout(ctx), update(ctx, t, state), texts(ctx),
  focusRect(ctx, target) -> {rect, shape: 'rect'|'ring'|'bar', radius} | null   // whole px
  itemEl(ctx, itemId) -> Element | null        // cue.term finds .rt-k spans (word splitting must keep .rt-k)
  dimGroups(ctx) -> Element[]                  // content groups dimmed to 70% by look_here (not the target)
  anchor(ctx) -> {el, rect} | null             // morph overlay source/target
}
static: mediaBox(scene, zones, mode, slot, fixes) -> {w,h} | null ; tex(scene, look, fmt) -> [{tex, display}]
ctx: {scene, index, look, roles, zones, format, rng (decoration only; layout randomness is decided in Python), mode,
      reducedMotion, root, paint, pending, media(ref, {slot, start, alt}) (box from mediaBox), onBackground, rectOf,
      locale, renderTex, frameInfo, view, motion (from presentation: scale, entrance), outgoing? }   // NO ctx.timeline
```
Templates never transform ctx.root (the Slot owns a `.dyn-camera` wrapper) and never draw emphasis.

### 1.5 cues.js (stage-driven only; ctx.cues not shipped)
Per-Slot `.dyn-cues` layer above content, below captions, clamped to the critical zone; applied after template.update
for the incoming and the outgoing slot (outgoing at its final time). From scene.events only (R-26 budget is Python's).
- cue.term: personality style. MARKER: in dark families the glyphs switch to dark on-block ink exactly where the marker
  has swept (background-clip:text hard-stop gradient moving with the sweep) — never light-on-yellow or dark-on-dark;
  light families: mark at 35% alpha under ink. UNDERLINE: mark (dark) / ink (light), 4 px, drawn left to right.
  Multi-line: box-decoration-break: clone. Validator: ink on mark >= 12:1; underline >= 3:1 on every bg/surface.
- cue.look_here: outline (4 px focus, outset >= 8 px, shape rect|ring|bar, 250 ms draw) + dim of dimGroups to 0.7
  ramped over >= 300 ms; push only if B2-0 (a) passes and the template's push_ok, else outline form. Dimmed text stays
  critical for contrast checks (reported with its dimmed ink).
- Reduced motion (live only): push/punch -> outline form; Ken Burns frozen at start; parallax/drift frozen; stylised
  transitions -> crossfade. Render mode never applies it (test).
- state.js (allow-listed): current = weight 700 + 2 px ink2 border; done = ok icon + ok border; struck = SVG line in warn.
  Never `outline`/`text-decoration`; visually distinct from look_here (gallery checklist item).

### 1.6 TemplateSpec (Python, templates.py)
id, semantic_class, representational, release (R1|W2|B6), implemented, formats, variants, forced_for, duration{fmt},
holds, dense (background frozen R-55, no SFX R-49, music off with 1.5 s fades R-44 [B3]), push_ok, slots
{slot: {fmt: {variant: {w, max_lines, size:(min,max), role, face, weight, line_height, max_words|max_chars}}}},
consumes {kinds, targets}, fit(scene, fmt) -> float, events(scene, timed, fmt) -> list[VisualEvent] (extras only).
templates.py and events.py do not import each other (declarative specs).

### 1.7 Target grammar (events.py <-> focusRect/itemEl)
`<item>`, `<item>:line:<n>`, `<item>:var:<symbol>`, `<item>:step:<n>`, `<item>:row:<n>`, `<item>:column:<n>`,
`panel`, `panel:bar:<series>:<i>`, `panel:point:<series>:<i>`, `panel:row:<n>`, `panel:column:<n>`, `option:<i>`.

### 1.8 Semantic-class table (§4.2, 19 classes) frozen; every TemplateSpec class is in it (Python + JS test).
### 1.9 Role API: closed list (ink, ink2, surface, entity:0..2, warn, ok, mark, focus, struct.accent, struct.tint,
struct.border, struct.pip) + roles.on(mode) for content_alt + inkOn(bg) + block roles from look.tokens.
### 1.10 SceneLook.lock semantics exactly §4.4 l.835. palette_slot written by direct.py; resolver derives the accent from it.
### 1.11 Slots/type: the PAGE decides pixels; text_fit.size is a start/upper bound; Python biased conservative; test:
Python fits => page overflow 0 on every gallery slot; Python overflow => page overflow > 0 or within 4% / 8% slack.
### 1.12 mediaPlan 'aadhi' entries gain optional crop [x,y,w,h]; key a-<hash(clip|crop)>-WxH; extract_args crops before
scaling; additive stage-contract-v3 extension (INTERFACES). Logical clip id -> current file at serve/extract time.

## 2. Rules wired (owner module + test)
- R-54: colour_wipe = B's transition_in into chapter_bumper (block sweeps over A, bumper content on the block); OUT of a
  bumper: crossfade >= 300 ms (not the design's hard cut). aadhi_intro's brand wipe = its own exit choreography in
  [t_exit, t_exit+0.6] with refs.accent_next, and the next scene enters with a crossfade >= 300 ms from the block. 9:16
  intro exit: card slides down + crossfade (iris is W2). Soft = cut/crossfade in R1. Flash test: per-frame relative
  luminance change over > 25% of the area and saturated-red ratio on every transition x family. Validator lowers chroma
  for full-frame accent blocks in the saturated-red range.
- R-52 accent cross-fade 600-1000 ms (accent_fade) when no wipe carries the change; R-18 macro changes never within 2 s
  before a reveal (direct.py shifts the boundary-only change); 9:16 boundary unit = act (Learning) / segment (Social),
  16:9 = chapter; energy is a direct.py parameter (default balanced; Presentation has no field — B4 gap recorded).
- §4.8 grammar: one camera move at a time; nothing enters during it; exits 30% shorter (events.py + tests).
- R-16 holds: events.py generates a look_here walk or the ring for every hold; R-15/R-64/R-25 via pure
  reading_windows()/static_gaps()/visible_density() (B4 lints reuse them); tests on gallery + kitchen sink.
- R-49: SFX in word gaps >= the sound's length, <= 1 s, none when presentation.sfx is false; MP4 audio stays classic
  until B3 (per-second ticks) — recorded deviation.
- R-55 numeric tests per preset (period >= 15 s, <= 2% width/s, behind-text luminance ratio <= 1.5); R-56 check;
  §7.5 chroma rule; accent-vs-bumper-text with +-0.06 L nudge (content never nudged); forced-scrim table; 70% dim
  contrast; cue colour contrast.
- R-29 type floor for EVERY critical role in preflight (zero findings on fixtures); captions: min 52 (9:16) / 48 (16:9),
  overflow -> re-chunk then warn (never block), > 2 rendered lines or sideways clip = overflow; mark colour from the
  presentation; per-script line heights (Indic display >= 1.25, body >= 1.45, captions >= 1.35); 16:9 Indic captions
  1 line per chunk when 2 cannot fit; glyph-ink collision test.
- Captions: Dynamic 16:9 Timeline.captions built by a Dynamic phrase split (42x2, >= 1.0 s, Netflix rules, measured to
  band - 2 padding - 2 outline incl. word-spacing) and kinetic chunks 1:1 from it; 9:16 Timeline.captions derived from
  the kinetic chunks (portrait_caption_cues retired); classic Timeline.captions unchanged; test SRT == chunks.
- Preflight: event-based settle sampling (event.t + dur), kinetic chunk starts in both formats, every critical TextEntry
  sampled settled at least once (gallery test), frame0_hook (9:16) and aadhi_time checks within schema 1, remedy-
  conformance harness per template, legacy 'safe' read kept for v1.
- Aadhi: audio_offset 0.8 / 0.15 s; duration = clamp(t_exit + exit, 5, 8); title holds to 5.0 s; intro_layout,
  reel_card_x, aadhi_recap from Presentation; DYNAMIC_AADHI_INTRO=false -> hook_question; studio wall measured over the
  192 frames per intro layout title region -> studio token -> validator; logo bug = logo clip's last frame as a branding
  derivative; presenter frame cache under RENDER_WORK_DIR/derivatives/aadhi/<digest>/<crop>/<size>/ (tmp+replace under a
  lock; swept with workspaces).
- Chunk key: background clock at the chunk's first frame added to slices; CHUNK_CACHE_VERSION bump.
- Classic isolation: Dynamic modules imported lazily inside the Dynamic gate; test that a classic build imports none of
  aadhi.compose.dynamic.*, aadhi.pipeline.direct, PIL.ImageFont; preview build budget <= 300 ms for 40 scenes.
- Fonts: B2 faces under web/vendor/fonts/dynamic/ (classic _copy_fonts untouched); one face table generates fonts.css and a
  Python copy; textfit resolves faces per grapheme run with the stage's stack order, Layout.BASIC, variable axes set,
  fail closed; CI tests load every face and pin advance widths (ubuntu job + container); fontRequests from
  presentation.fonts; THIRD_PARTY_NOTICES generated.
- Chart.js: marks + gridlines only in canvas; every text is DOM, positioned by scale pixels, in texts(); built in layout();
  'chart' leaves UNDRAWN_PANEL_KINDS when chart_build lands.
- MathJax: template static tex() unioned into render.js preconversion; render-mode TeX cache unbounded; post-ready
  conversion counter asserted 0 on every gallery fixture; entity tints via \color placeholders if B2-0 (g) passes.

## G. Gallery and acceptance
Dev-only, served by a dev script with its own static server; page + fixtures under a dockerignored path the static mount
denies and bundle_digest skips. Matrix: each R1 family with its default personality (Indigo/Soft, Teal/Crisp,
Paper/Crisp, Mint/Soft), display faces assigned so Poppins appears in one dark and one light family, plus the safe
variant. Text cases: typical, lint-limit longest with wide glyphs, one very long word, minimal, Tamil, Hindi, mixed
Tamil+English term+$TeX$. Views: consistency sheet; playable per-template strips (1x / 0.25x, frame step, jump to event);
stress sheet. Toggles: phone scale (0.44x, 0.2x), zone overlay (zoneOverlay.js stage option: UNIVERSAL + YT/Meta/TikTok
generic UI rects + caption band), text-box outlines, greyscale and Machado CVD. Automated badges per tile: min measured
contrast (pixel, text-off differential capture), smallest critical px, overflow, R-25 density, R-64, max static gap.
Kitchen-sink R1 lesson and reel (every R1 template in a realistic order) rendered to MP4 through the real engine.
Goldens = tolerant PNGs at semantic instants (pixeldiff tolerances) with an HTML diff report; determinism stays SHA.
"Every R1 template passes in both formats" := zero preflight blocks and zero type_floor, order-independence, SSIM >= 0.98
live vs MP4, frame-time p50/p95 within budget, on all gallery fixtures — plus the owner's checklist tick.
Thumbnail: formats {landscape}; rendered at 1920x1080 in a one-scene timeline and lanczos-downscaled to 1280x720; export
path B4.

## D. Deviation register (to copy into the design's as-built)
1 catalogue.gen.js instead of web/dynamic/themes.json. 2 fixes channel instead of "the template's safe variant" via
variant. 3 stage-driven cues; ctx.cues not shipped. 4 ResolvedLook/TimelinePresentation additions (§1.2/§1.3).
5 TemplateSpec additions (implemented, dense, push_ok, slots, consumes). 6 16:9 Dynamic captions: Dynamic phrase split +
kinetic chunks. 7 build_timeline runs direct.py for missing/auto looks (pure, never stored). 8 paper_grain generated tile.
9 gallery goldens on Windows (Docker pending). 10 R-54: bumper exit crossfade not hard cut; intro wipe as own exit.
11 iris replaced in R1 paths. 12 transform policy outcomes (§P). 13 code_walkthrough 36 px in 16:9 (<= 17 lines) and
<= 28 chars in 9:16 (R-29 wins over §4.3's 34 px / 32 chars). 14 bumper numeral = decorative outline role outside R-29.
15 paper interlude variant deferred to W2. 16 hook_question 'mystery' variant deferred to B4 (needs the asset-time blur
and B4 hook rotation). 17 animation_stage Manim colour mapping/content key deferred to B4 (clip shown in a dark card).
18 live checkpoint interaction (predict/check pause) deferred to B4. 19 emoji as images and Anek axis rotation W2.
20 energy has no stored field (B4). 21 engine_version relabelled look-engine generation. 22 entity map lesson-wide.
23 presenter cache location. 24 remedies persisted in B4 (B2 keeps them MP4-only, as B1).

B2c additions (the vertical slice; review round 1, 2026-10-15). 25 cue.term styles the term's own span in place
(background layers under the glyphs plus a background-clip:text glyph layer), not a layer above (plan §1.5's "cue
layer" cannot put a marker under glyphs); cue timings are absolute (not scaled by the personality); a cue without
`dur` holds 2 s. 26 target grammar gains `<item>:term:<n>`; `itemEl` may return an array (a 9:16 misconception's two
cards). 27 `consumes` narrows kinds to forms (templates.py `Consumes.narrow` / `forms`, catalogue `consumes.forms`):
the gate is per kind, never the cross product (review CG-5). 28 a clip is never scaled: no camera push in a scene that
shows a clip (its look_here keeps the outline form), media_split's clip enters by a fade (measured order-dependent
under a push or scale; §P (d) measured stills only). 29 `TimelinePresentation.text_entrance` is written on every
engine-2 build (frames unchanged: board_generic never reads it; chunk-cache keys of rebuilt projects change, cache
misses only; d63ff94's model accepts the new JSON) (review NVC-1). 30 look_here around a picture, card or drawing
(`FocusRect.kind` 'shape') is clamped to the shapes area, not the critical text boxes (review CG-1). 31 pilot type
sizes below R-29's nominal ranges, above its floors (board_generic's precedent; OWNER DECISION, review V8): media_split
16:9 title 64-88 (headings 84-120), body 44-52 (48-56), 9:16 phrase 48-64 (body 56-64); list_build 16:9 rows 40-52;
inline TeX in media_split 9:16 phrases and list_build 9:16 rows is MathJax's one weight (400) at <= 64 px, while
formula_focus keeps its 9:16 formula >= 68 px for R-29's weight clause; preflight's type_floor cannot see TeX weight.
32 9:16 headlines may take 2 lines in all three pilots (prefer 1; board_generic's precedent) against §4.3 media_split's
"one-line headline" and R-25's 9:16 column; a 2-line headline runs past the zone's y 440 and costs the picture room
(OWNER DECISION, review V9). 33 formula_focus: §4.3's 9:16 "768 px" read as the card (text keeps R-59's 696 px
column); the box is a 4 px struct.border drawn on `draw`; braces at their symbol's glyphs (under, over a numerator,
none when boxed in), each leaving at the next symbol's cue or the first look_here, the last after at most 2 s; the
sentence is left out before the legend overflows; no legend row ever leaves (L-13); `consumes` gains look_here (R-16
walk), whose focused symbol keeps its legend row bright. 34 list_build: the current row's state is plan §1.5's 2 px
ink2 inset border (not §4.3's 3 px ink outline); look_here goes round the row's card, outside the state border; 16:9
is centred unless a picture/clip panel or a stored icon for every row fills the right 45%; 9:16 draws no panel (a
scene with a panel it does not draw scores 0 and falls back to board_generic). 35 media_split: the 9:16 key phrase is
the current text item, one at a time, sharing the headline's left edge; a pushing scene lays its text inside the push
area; every 16:9 item but a heading carries a marker dot; Indic text takes the Indic line heights.
36 preflight (fix, not a deviation): a finding seen first as a warning (critical text mid-entrance is reported as not
critical) no longer masks a checked sighting of the same slot (`Finding.merge`); it had let a 9:16 key phrase and a
9:16 list row that overflowed at their minimums pass. Today's timelines have no finding either way (12 timelines
checked). The 9:16 media_split phrase column (x 192-780, less push room) holds at most 2 lines at 48 px, so key
phrases are short (the Tamil and long fixtures were shortened to fit; preflight blocks a longer one).
