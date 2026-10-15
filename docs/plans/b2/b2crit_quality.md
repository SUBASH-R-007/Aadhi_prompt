# LENS quality

## [critical] cues.js / cue.term colours (signalling, contrast)
PROBLEM: The plan draws cue.term as a marker sweep or underline "behind text" with "mark colour from roles". It never says the term's ink must change to dark ink in dark families, and it never picks a different underline colour for light families. As written, every key term in a dark family is white on yellow (#F2D935), about 1.4:1. In a light family a yellow underline on cream is about 1.25:1, below the 3:1 graphics minimum. This affects the main learning cue in every video.
EVIDENCE: Design §4.7 table, line 896: mark = "#F2D935 marker under dark ink" (dark) / "the same yellow at 35% under ink" (light), "ink on mark ≥ 12:1". §4.8 line 968: "4 px mark/ink underline". R-53 line 264: graphics ≥ 3:1. Plan §1.4 line 70. Dark-family ink is L ≥ 0.96, which is near white. Relative luminance of #F2D935 is about 0.69, so white on it is about 1.4:1.
CHANGE: Plan §1.4 should specify the following. Dark families: the term's glyphs switch to the dark on-block ink (core.js ON_BLOCK dark) in step with the sweep. Use a hard-stop gradient on the glyph fill (background-clip:text) that moves with the marker, so no frame shows light-on-yellow or dark-on-dark. Light families: mark at 35% alpha under ink. Underline colour: mark in dark families, ink in light families. Multi-line terms use box-decoration-break: clone. Put the per-mode cue colours into catalogue.gen.js (marker_ink, marker_alpha, underline). Add validator asserts: ink on mark ≥ 12:1, underline ≥ 3:1 against every bg and surface of its mode. Add a cues.js unit test that samples the sweep at 0/25/50/75/100% and checks the contrast of each glyph fill against what is behind it.

## [major] Text slots: Python textfit vs JS layout (overflow, legibility)
PROBLEM: TemplateSpec has no slot geometry. textfit.py picks size and line breaks "per slot", but the boxes those slots fill are defined only in JS (board_generic keeps its own LAYOUT/MIN_* constants and a 4 px fitting loop). Two fitters working from separately maintained geometry will disagree. The likely result: lints pass in Python while the stage shrinks or overflows, or the JS ignores text_fit. The plan also does not say which side wins.
EVIDENCE: Plan §1.5 lines 78-82 (no slots). Plan B2b lines 122-123. Design §4.6 lines 875-877 (Python measures at direct.py time; the page lays out and reports overflow). The B1 report puts board_generic LAYOUT at :99-128 and its title fitter at :612-645.
CHANGE: Add `slots: {slot: {fmt: {variant: {w, max_lines, size:(min,max), role, face, weight, line_height}}}}` to TemplateSpec and emit it in catalogue.gen.js. JS templates size their text boxes from it, with no literal text-box widths. Precedence: JS lays out at text_fit.size/lines and shrinks only on measured overflow, reporting that it did. Gallery test: the JS size is within one step of Python's, and the lines are equal for Latin script (at most +1 line for Indic).

## [major] kinetic.py caption measurement width
PROBLEM: The plan measures 9:16 chunks "to 660 px". The clean_pill text box is only 612 px wide (24 px padding on each side). bold_outline adds a 6 px outside stroke and 0.16em word-spacing. Chunks that "fit" in Python will wrap to 3 lines in the stage (overflow-wrap: anywhere). captions.js reports only vertical overflow, so preflight cannot see a 3-line wrap that still fits the band height.
EVIDENCE: web/css/dynamic.css:805-806 (padding 2px 24px), :820-822 (text-stroke 12px, word-spacing 0.16em). Plan B2c line 133. captions.js textEntry overflow = offsetHeight − band height.
CHANGE: Put caption-style metrics in the catalogue: {padding_x, outline_px, word_spacing_em, size, weight, line_height per script}. kinetic.py measures to band − 2·padding − 2·outline and includes word-spacing. captions.js reports more than 2 rendered lines, or any sideways clipping, as overflow. Add a slow test comparing chunk widths with Chromium for both styles and for Latin, Tamil and Devanagari.

## [major] Measured (pixel) contrast and the Aadhi studio wall
PROBLEM: The analytic contrast check cannot see text over pictures or translucent layers: colour.py returns None for a translucent bg. Captions declare an opaque #0B0D14 although the pill is 78% alpha. The design's post-render pixel-contrast warning is not built. Several R1 templates put text over imagery: the full_bleed_media lower third (55% scrim), the hook_question mystery image, the aadhi_intro fullframe/split title on the studio wall, the animation_stage lower thirds and the interactive_poster chip. The plan also leaves out the required measurement of the studio wall and the `studio` token, so the first frame of every lesson is unvalidated.
EVIDENCE: Design §3.3 line 519 ("B2 measures the wall's luminance over all 192 frames in the title region"). §4.7 line 940 (validator "including the measured Aadhi studio wall"). §7.12 line 2006. colour.py:260-269. captions.js textEntry bg = CAPTION_PILL vs dynamic.css:807 rgba(11,13,20,0.78). The plan never mentions "studio" (grep).
CHANGE: Add three things to B2a/B2f. (a) A one-off wall measurement script over the 192 frames of aadhi_left_clean.mp4 per intro layout title region, producing the `studio` surface token that the validator checks. (b) A gallery pixel-contrast test: capture each sampled frame normally and again with a gallery-only class that makes text transparent. Take the 95th-percentile-worst background luminance under each critical box and compare it with the declared ink against R-53. Below 4.5 fails, and captions below 7:1 warn. (c) Templates and captions declare the composited bg (scrim or pill alpha over the worst bg), not the opaque scrim colour.

## [major] Preflight type floor (phone legibility)
PROBLEM: below_type_floor flags only label/chip below the label floor and 9:16 weight under 600. Body, title, subtitle or formula text below 36 px (16:9) or 40 px (9:16) is never flagged unless it also overflows. With 22 new templates, a 30 px body passes preflight. R-29's floors are what keep text legible at phone scale: 36 px × 0.44 ≈ 16 CSS px.
EVIDENCE: aadhi/compose/dynamic/preflight.py:222-230 (below_type_floor) and :86 (MIN_TEXT_PX, used only for shrink floors). R-29 line 219.
CHANGE: In B2a, extend the type_floor check to every critical box with size < the floor (floors read from zones/catalogue). Formula boxes report their effective glyph size after fit-to-width. The gallery and kitchen-sink tests require zero type_floor findings rather than allowing warn.

## [major] chart_build text drawn in canvas
PROBLEM: Chart.js draws ticks and labels in the canvas at 12 px by default. textBoxes is built only from DOM text entries, so chart labels escape the size, contrast and safe-zone checks, and direct labels (R-27) cannot be verified. The result is unreadable chart labels on phones.
EVIDENCE: Design §4.3 chart_build line 721 (direct-labelled, no legends). R-27 line 217. R-29 labels ≥ 40/44. stage.js textBoxes add(e) uses rectOf(e.el) from template texts().
CHANGE: In B2f group (3): Chart.js draws only marks and gridlines (ticks, legend and title off). Every axis title, tick label, direct label and count-up value is a DOM element positioned with chart.scales.*.getPixelForValue, at or above the label floor, and reported in texts(). Test: each direct label lies within 96 px (5% of 1920) of its series end or bar top, and no legend exists.

## [major] State styling vs cue.look_here (signalling clarity) and the source scan
PROBLEM: The design requires state outlines and strikes in templates: the list_build current item has a 3 px ink outline, the worked_steps answer an ok outline, and myth_fact a strike-through. The plan keeps the scan's ban on outline and text-decoration in templates, so these states either cannot be drawn or get drawn through `border` loopholes. In light families focus = ink, so a 3 px ink state outline looks almost the same as the 4 px look_here outline, and learners cannot tell "current" from "look here". That weakens R-26's two-cue system.
EVIDENCE: §4.3 list_build line 676, worked_steps line 716, myth_fact line 734. R-52 line 263 ("weight, ink and outline"). §4.7 line 897 (focus = ink in light). Plan §1.3 line 64. sourceScan.test.js:260, :303, :714.
CHANGE: Add a shared `state.js` helper. It is the only non-cue stroke allowed in templates and is allow-listed in the scan: current = weight 700 + 2 px ink2 card border; done = ok check icon + ok border; struck = an SVG line in warn. Never `outline` or `text-decoration`. Require the look_here outline to sit outset by at least 8 px and always come with the 70% dim of the other content. Gallery checklist item: in a light family at phone scale, the current-item state and look_here are distinguishable.

## [major] look_here dimming
PROBLEM: The plan dims "others" with a box-shadow hole, which is a spotlight veil over the background. The design asks for the other elements to be dimmed to 70%. A veil turns light-family cream grey and changes the luminance behind nearby text without any contrast check. Separately, any dimmed text (alpha < 0.99) becomes non-critical and escapes every check.
EVIDENCE: §4.8 line 969 ("other elements dimmed to 70%"). Plan §1.4 line 71. stage.js textBoxes: critical = e.critical && alpha >= 0.99.
CHANGE: Dim with opacity 0.7 on the template's content groups other than the target (the template exposes dim groups, e.g. data-dyn-content). Background, structure chips and captions stay untouched. Validator: ink at 70% ≥ 4.5:1 and ink2 at 70% ≥ 3:1 on every bg and surface of the mode. textBoxes keeps dimmed (cue-state) text critical for contrast; only entrance alpha exempts it.

## [major] Scale-based motion vs determinism
PROBLEM: Several R1 moves the design specifies are scales: the push (2-6%), the punch-zoom 1.00→1.10, the Aadhi 9:16 card settle 0.96→1, the media_split media 0.96→1, Ken Burns 1.00→1.06 and the recap tile flip. B1 found that scaled pictures make frames depend on render order, and Ken Burns still scales without a test. The plan quietly turns push into a translate, which no longer reads as a push-in, and gives no rule for the others.
EVIDENCE: §4.8 line 969. §3.3 line 535. §4.3 lines 657, 681, 683. B1 deviation at design lines 2505-2506 ("rising (never scaling) pictures"). web/js/dynamic/media.js:399-401 + web/js/player/kenburns.js:42 (scale). Plan §1.4 lines 72-73.
CHANGE: Add a B2a spike gate. Run the 1×N/3×N/reverse determinism test on (a) a scaled text container, (b) a scaled <img> from a pre-extracted frame at or above the final size (only ever scaling down), and (c) a canvas drawImage crop window. Adopt what passes for Ken Burns, push and settles, and record the choice. If text cannot scale deterministically, the push falls back to the outline form; a translate is not an acceptable push. Add Ken Burns and push cases to the tests/determinism fixture.

## [major] Acceptance gallery and owner checklist
PROBLEM: The gallery matrix (template × format × 4 families × longest/Tamil/Hindi) misses the dimensions that cause visual failures: personality (cue style, caption style, entrance), display face width (Poppins vs Outfit/Lexend), safe variant, minimal content, mixed-script text. It has no motion playback, phone scale, CVD view or zone overlay (the overlay is B2 scope). It has no full-sequence view either, so "would a student want to keep watching?", transitions, accent rotation and pacing cannot be judged. About 700 tiles with no automated flags will not get careful review.
EVIDENCE: Plan B2g lines 180-183. Design acceptance line 2544. B2 scope line 2534 ("zones.py with the safe-zone overlay"). §4.8 personalities table lines 977-981.
CHANGE: Pin the gallery spec. Matrix: each family with its default personality (Indigo/Soft, Teal/Crisp, Paper/Crisp, Mint/Soft), with display faces assigned so Poppins appears in one dark and one light family; plus the safe variant. Text cases: typical, lint-limit longest with wide glyphs, one very long word, minimal (2 items / 1 line), Tamil, Hindi, mixed Tamil+English term+$TeX$. Views: a consistency sheet (all templates, one family, settled frame); per-template 4-family playable strips (1×/0.25×, frame step, jump to each event); a stress sheet. Toggles: phone scale (0.44× for 9:16 and for 16:9 landscape, 0.2× glance), zone overlay with YT/Meta/TikTok UI rectangles, textBox outlines, greyscale and Machado CVD. Per-tile automated badges: minimum measured contrast, smallest critical px, overflow, visible items/words (R-25), R-64, maximum static gap. Add a hand-written R1 kitchen-sink lesson and reel using every R1 template in a realistic order. Render both to MP4 through the real engine for the owner to watch on a phone, checking frames decoded from the MP4 (chroma fringing). Record the checklist per tile as pass/fail with notes.

## [major] Visual regression goldens
PROBLEM: "Gallery golden frames" as hashes break on any intended change and invite blind regeneration. They also show reviewers nothing about what changed.
EVIDENCE: Plan B2g line 182. Design §7.4(c) line 1895. The existing tolerant comparer is tests/invariance/pixeldiff.py.
CHANGE: Use the owner-approved reference frames as the goldens, at semantic instants: each beat settled, mid-cue, mid-transition, final frame. Store them as 1/4-scale PNGs per platform and compare with pixeldiff tolerances rather than SHA. On failure, write an HTML before/after/diff sheet. Commit a subset (one dark and one light family × typical and longest text); the full matrix is a generated artifact. Determinism stays SHA-based.

## [major] Learning-pacing gates (R-64, R-15, R-16, R-25)
PROBLEM: Template choreography decides reading load and static stretches, yet B2 tests cover only R-17/R-26/R-49/R-32/R-33. R-16's "a cue moves or a ring runs" during holds is not generated by events.py. Some R1 template designs can break R-25 (≤ 3 items, ≤ 24 words): key_terms with 4 cards × (3+8 words) = 44 words, the full 16:9 list_build list, and the recap tiles. The plan does not say how each template complies.
EVIDENCE: R-64 line 221. R-15 line 200. R-16 line 201. R-25 line 215. key_terms lines 641-643. list_build line 676. Plan B2c tests lines 139-140 (no R-64/R-15/R-16/R-25; grep finds none in the plan).
CHANGE: B2c adds pure `reading_windows()` and `static_gaps()` (the B4 lints reuse them). events.py generates the R-16 hold cues (a look_here walk over formula/code/diagram parts, or the ring). Tests on the gallery fixtures and the kitchen-sink lesson: R-64 passes, max static ≤ 8 s (16:9) / 6 s (9:16), every hold has a moving cue or ring. State an R-25 policy per template, e.g. key_terms collapses earlier cards to term chips after their beat, and list_build counts ink2 rows and switches to compact past 24 words.

## [major] Indic and mixed-script typography
PROBLEM: 16:9 captions use line-height 1.08 so that two lines at 48 px fit the 108 px band, and display text is 1.0-1.05. Devanagari matras and Tamil vowel signs will collide or clip at those values. Overflow is measured on boxes, not glyph ink, so nothing catches it. Mixed-script lines (a Tamil sentence with an English term) are not tested either, and they are the normal case for this audience.
EVIDENCE: web/css/dynamic.css:796 (line-height 1.08). Design §4.6 line 871. captions.js textEntry (offsetHeight based). Plan B2g only says "Tamil/Hindi".
CHANGE: Add per-script line-height tokens to the catalogue (Indic: display ≥ 1.25, body ≥ 1.45, captions ≥ 1.35). 16:9 Indic captions become 1 line per chunk when 2 lines cannot fit the band. Add a gallery test that measures glyph ink with canvas measureText (actualBoundingBoxAscent/Descent) for each rendered line against the line pitch and the container, failing on collision or clipping. Add a mixed-script fixture.

## [major] Craft consistency across parallel template groups
PROBLEM: Four groups build 22 templates in parallel with only `_shared.js` helpers. There are no layout or type tokens: no grid/regions, spacing scale, radii, borders, shadows, icon sizes or type scale. Each group will invent its own, and the set will look inconsistent and amateur.
EVIDENCE: Plan B2f lines 166-169. Catalogue §1.6 lines 88-90 (no layout/type tokens). Design §2.5 lines 381-382 (12 columns, 24 px gutters, named regions).
CHANGE: B2a emits `layout` tokens into catalogue.gen.js: grid and regions per format from zones.py, an 8 px spacing scale, radii (card 24, media 24, chip pill), 4-6 px structure borders, shadow levels, icon sizes, and a type scale from the R-29 ranges. The scan bans literal border-radius and box-shadow in template JS. B2f opens with a pilot (definition_card, list_build, media_split) and an owner reference-frame review of static settled comps for all 23 templates in one dark and one light family. Only after that do the groups run in parallel.

## [major] B1 leftovers the design hands to B2
PROBLEM: The plan does not list the B1 defects that the design assigns to B2 templates, so they may survive as visible defects.
EVIDENCE: Design lines 2519-2526: the 9:16 split picture is small when cards are tall; a 9:16 terminal card collapses before its last line; a card revealed on the same beat as a taller card is pushed out before it is shown ("B2's media_split and code_terminal").
CHANGE: List these under B2f groups (2) and (3), with the showcase scenes as regression fixtures. 9:16 media_split media takes ≥ 50% of the stage area. The code_walkthrough terminal never collapses before its last output line. No card exits before MIN_DWELL visible.

## [minor] chapter_bumper exit vs R-54; red accents
PROBLEM: The design says the bumper has a "hard cut out", but R-54 requires full-frame colour changes to cross-fade over at least 300 ms, and B1 already forces a crossfade for colour_block. Accent windows 0-70 (Deep Teal, Paper & Ink) can produce saturated-red full-frame blocks.
EVIDENCE: §4.3 line 651. R-54 line 265. Family table lines 920 and 925.
CHANGE: In direct.py's §4.9 rules, set the bumper exit to a crossfade of at least 300 ms and record it as the resolution of the conflict. The validator lowers chroma (or skips the hue) for full-frame accent blocks in the saturated-red range. The flash test measures per-frame luminance and red ratio across the bumper's wipe in and cut out.

## [minor] Catalogue and validator completeness
PROBLEM: The catalogue has no forced-scrim table for (preset, family) pairs that cannot hold contrast. The validator does not check the §7.5 chroma rule: every light-mode entity, warn and ok colour has L < 0.6, so text in those colours must be ≥ 32 px. It also has no dimmed-state checks.
EVIDENCE: §4.5 line 859. §7.5 line 1917. §4.7 line 891-895. Plan §1.6.
CHANGE: Add `scrims:{preset:{family:bool}}` to the catalogue, applied by the stage without waiting for preflight. Add validator asserts for the chroma rule, using the effective glyph size of TeX, and for the 70% dim states.

## [minor] code_walkthrough floors and colours
PROBLEM: Several code-template numbers conflict with R-29. 16:9 code at 34 px is below the 36 px floor. 9:16 "≤ 32 chars" at the 40 px floor needs 768 px in JetBrains Mono (0.6 em advance), but the text column is 696 px (660 px below y 840). Syntax colours are unspecified, and in light families the content tokens fail on the required dark editor surface.
EVIDENCE: §4.3 line 719. R-29 line 219. §2.5 lines 404-405.
CHANGE: Settle this in TemplateSpec slots. 16:9: 36 px with at most 17 lines (or a declared code floor of 34 px). 9:16: ≤ 28 chars at 40 px, with lint portrait.code_too_wide aligned. The editor uses the dark content set in every family. Syntax uses ink/ink2 plus weight; a variable that is a mapped entity keeps its entity colour (consistent with the formula); no other hues.

## [minor] Headline line breaks
PROBLEM: Headline and title line-break policy is unspecified, so orphans and breaks after articles will look unprofessional.
EVIDENCE: Plan §1.1 text_fit {size, lines}. Design §4.6 lines 871-877. The caption break rules exist (§2.6 line 440).
CHANGE: textfit uses balanced breaks, no single-word last line when the text has ≥ 4 words, and the caption rules (never article+noun, number+unit). Test on the gallery fixtures.

## [minor] Aadhi bookend timing and frame 0
PROBLEM: The plan does not cover the Aadhi scene length rule, the 9:16 frame-0 hook check, the Aadhi-time preflight check or the logo bug. Frame 0 is the reel cover.
EVIDENCE: §3.3 lines 506, 519, 532. §7.12 lines 1990-1991. preflight.py:44-46 (deferred).
CHANGE: build_timeline sets the Aadhi scene length = t_exit + exit, clamped to [5,8] s. Add preflight `frame0_hook` (9:16 hook box fully inside y 340-760 at alpha 1 on frame 0) and `aadhi_time`, both from zones.py. The logo bug comes from the last frame of logo_animation and is cached as a branding derivative.

## [minor] thumbnail, reduced motion, choreography grammar, holds
PROBLEM: Four gaps. The thumbnail has no render path or small-size legibility check. cues.js has no reduced-motion mapping. The choreography grammar (one camera move at a time, nothing enters during it, exits 30% shorter, no macro change in the 2 s before a reveal) is untested. The template minimum holds (formula ≥ 3 s, hook ≥ 0.8 s) have no stated owner.
EVIDENCE: §2.8 line 470. §4.3 line 662. §4.8 lines 973, 986. R-18 line 203. R-30 line 220. §4.3 line 636.
CHANGE: Thumbnail: render at 1920×1080 and downscale with lanczos to 1280×720, and the gallery shows it at 360×202 and 168×94 (≤ 5 words, measured contrast ≥ 4.5:1). cues.js maps push/punch to outline under reduced motion (unit test). Add the grammar constraints and tests to events.py. build_timeline extends the silent tail to the template minimum, or a test fails.

## ANSWERS
These parts of the plan are sound and should stay:
- one Python source with a generated catalogue checked by `--check`;
- remedies (`fixes`) kept separate from `text_fit`;
- the static `mediaBox` hook, which fixes the B1 media-key risk;
- one colour-maths module with JS parity vectors, and one zones module;
- determinism gating every sub-batch;
- kinetic.py merging chunks across beats;
- crossfades always at least 300 ms.

Q1 (generated JS module instead of themes.json). Acceptable, recorded as an as-built deviation. For video quality, the module must hold every visual constant so nothing is retyped in JS:
- layout and type tokens and slot geometry;
- caption-style metrics (padding, outline, word-spacing, line-height per script);
- per-mode cue colours (marker ink, underline colour);
- dim and scrim alphas;
- the forced-scrim table;
- the `studio` token.

The source scan should also cover it.

Q2 (stage-driven cues vs template-driven ctx.cues). Stage-driven is the better fit for §4.2/R-26: there is one look per video, no template can drift, and the scan stays simple. It needs four things:
- focusRect returns {rect, shape: rect|ring|bar, radius}, with a shared outset of at least 8 px;
- templates expose dim groups (opacity 0.7) instead of the box-shadow hole;
- itemEl resolves a term only inside a revealed, fully visible item. Otherwise events.py drops the cue and a test fails;
- the cue layer sits above content and below captions, clamped to the critical zone.

Keep ctx.cues only for template-timed cases, such as a code-line bar that follows typing.

Q3 (direct.py inside build_timeline for missing/auto looks). Consistent with §4.4/§6.8 if it stays pure and only fills `auto` or missing looks, never `settled` or `pinned`. The resolved look must be stored in the built timeline the render job serves, so preview and MP4 cannot differ. One caveat to accept: until B4 writes settled looks at generation, an auto look can change between previews when a neighbour changes, because the R-20 penalties look at the previous template.

Q4 (kinetic_captions in 16:9 too). Yes. 16:9 needs them for four reasons:
- blanking: without it, hook_question and kinetic_statement make learners read the same words twice;
- R-35 key-term colour;
- the 1.0 s minimum: the classic MIN_CUE_SECONDS of 0.04 lets cues flash;
- one caption code path for both formats.

Timeline.captions stays complete for the sidecar files. Measure the 16:9 line width as the band minus the pill padding.

Q5 (icon geometry and entities in ResolvedLook). Icons in the look: yes. The timeline stays self-contained, and icon-set upgrades never change old videos. Icons must use content roles (ink/ink2) only. Entities: store the lesson-wide map once, in TimelinePresentation (R-30, §4.7 \"one lesson-wide mapping\"). Per-scene copies can drift. Add a test that each symbol maps to exactly one slot and that formula_focus, worked_steps, chart_build and code agree.

Q6 (engine_version 2). Yes, bump it: templates, cues and fixes change pixels, and the chunk key already includes engine_version. v1 timelines were never shipped (DYNAMIC_ENABLED is off by default), so they render through the same stage:
- board_generic and the B1 backgrounds are kept;
- absent fixes, cues and kinetic captions fall back to B1 behaviour;
- no migration is needed;
- pin the evals/render_parity v1 fixture pixel-equal, to guard the `_shared.js` extraction.

The player needs nothing special.

Q7 (what the plan misses or gets wrong).
- **The cue.term colours.** In dark families the term's ink must switch to dark ink as the marker sweeps over it. In light families the underline must be ink, because yellow on cream fails 3:1. This is critical.
- **Slot geometry.** TemplateSpec has no slots, so Python's textfit and the JS layout cannot agree on box sizes.
- **Caption width.** kinetic.py measures to 660 px, but the clean_pill text box is 612 px wide, and the outline style adds a stroke and word-spacing.
- **Pixel contrast.** Text over pictures, the studio wall and translucent pills needs a measured check. The design requires the studio-wall measurement in B2.
- **Type floor.** Preflight never flags body or title text below 36/40 px.
- **Chart labels.** Chart.js draws them in the canvas, where no check can see them.
- **State vs cue.** The current-item outline and look_here look the same in light families, and the scan bans the outlines the design requires.
- **look_here dim.** It is a box-shadow veil instead of 70% opacity.
- **Scale moves.** Push, Ken Burns and the card settles are scales, which conflict with B1's determinism rule. They need a spike gate.
- **The gallery and checklist** need a pinned spec.
- **Goldens** should be PNGs compared with tolerance, not hashes.
- **Pacing rules** R-64, R-15, R-16 and R-25 have no B2 tests or generation.
- **Indic line-heights** clip glyphs, and there is no mixed-script fixture.
- **Shared craft tokens** and a pilot are needed before the template groups run in parallel.
- **B1 leftovers** that the design assigns to B2 are not listed.
- **Minor items:**
  - the bumper's hard cut vs R-54;
  - the forced-scrim table and the chroma rule;
  - code-template floors;
  - headline line breaks;
  - Aadhi timing, frame 0 and the Aadhi-time check;
  - the thumbnail path;
  - the reduced-motion cue mapping;
  - choreography-grammar tests;
  - who applies template minimum holds.

## MISSING
Scope items from the design that the plan omits:
1. The Aadhi studio-wall luminance measurement over 192 frames, the `studio` surface token, and the validator check against them (§3.3 line 519, §4.7 line 940).
2. The safe-zone overlay (B2 scope, line 2534).
3. R-16 hold cues (a cue moves or a ring runs during every hold), generated by events.py.
4. The forced-scrim table for (preset, family) pairs (§4.5 line 859).
5. The §7.5 chroma rule in the validator.
6. Preflight checks for the 9:16 frame-0 hook band and Aadhi time, plus the Aadhi scene-length clamp to [5,8] s (§3.3, §7.12).
7. The reduced-motion mapping in cues.js (§4.8 line 986).
8. The logo bug derivative.
9. The thumbnail render path.
10. Code-template colours on the dark editor surface in light families.
11. B1 open items assigned to B2: the small 9:16 media_split picture, terminal collapse, and the same-beat taller card (lines 2519-2526).

Contract gaps:
1. TemplateSpec has no `slots` geometry (size range, width, max lines, face, weight, line height per script).
2. The catalogue has no layout or type tokens, no caption-style metrics, no per-mode cue colours, no dim or scrim alphas, and no scrims table.
3. focusRect needs a shape and radius.
4. Templates need to expose dim groups.
5. The precedence between text_fit and the JS fitter is undefined.
6. The entity map should be lesson-wide, in TimelinePresentation.
7. The `state.js` exception to the emphasis scan is undefined.

Tests:
1. cue.term contrast across the sweep in both modes.
2. type_floor for every critical role, with zero findings allowed on fixtures.
3. Pixel-measured contrast with a text-off differential capture.
4. Glyph-ink clipping and collision for Indic and mixed-script text.
5. kinetic.py widths against Chromium for both caption styles.
6. R-27 chart labels within 96 px of their mark, with no legend and DOM-drawn labels.
7. R-63: the fact card is dominant (area greater than the myth card, myth alpha ≤ 0.4) and is the final frame.
8. R-64 reading budget, R-15 maximum static gap, and R-25 density on the gallery and kitchen-sink fixtures.
9. A flash test on per-frame luminance and saturated red, for every transition and the bumper's wipe in and cut out.
10. A determinism spike for scale moves, and Ken Burns and push cases in tests/determinism.
11. Choreography grammar: one camera move at a time, nothing enters during it, exits 30% shorter, and no macro change in the 2 s before a reveal.
12. Live-CSS parity extended from 2 to all 4 R1 families (tests/determinism/test_live_css.py).
13. Goldens as tolerant PNG diffs at semantic instants, with an HTML diff report.

Acceptance:
1. A defined pass rule for \"every R1 template passes in both formats\": automated badges green, plus an owner tick per tile.
2. A gallery matrix covering personality, display face (with Poppins as the widest case), the safe variant, minimal content and mixed-script text.
3. Phone-scale (0.44× and 0.2×), CVD and greyscale, and zone-overlay views.
4. Owner review of frames decoded from the MP4, not only the page.
5. An R1 kitchen-sink lesson and reel rendered to MP4 and watched on a phone. This is what answers \"would a student want to keep watching?\" and covers transitions, accent rotation and pacing.
6. An owner review of static reference frames from a pilot of 3 templates before the parallel template groups start.