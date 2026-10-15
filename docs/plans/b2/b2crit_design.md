# LENS design

## [critical] Motion / determinism / cue.look_here (plan §1.4, B2e, B2f)
PROBLEM: The plan quietly redefines the camera push and the punch-zoom as translates. It also has no policy for the many scale-based animations the R1 design asks for, even though B1 measured that scaled pictures make frames order-dependent. These animations are: Ken Burns, the 9:16 Aadhi card settle, the media_split entrance, recap tiles flipping on rotateX, the myth card shrinking to 70%, the pop with overshoot, and paper_grain's scale breathing. Templates built this way will fail the blocking 1x300 / 3x100 / reverse determinism gate late in B2f, or they will be changed without any record.
EVIDENCE: Plan lines 71-73 say 'push (camera translate 2-6%...)' and 'Punch-zoom = push at 10%'. The design says 'a camera push of 2-6%' and 'punch-zoom (1.00 -> 1.10 in 250 ms)' at §4.8 l.969. Other scale animations in the design: §3.3 l.535 'card settles from 0.96 to 1.0'; §4.3 l.681 'media scales 0.96 -> 1'; l.683 'Ken Burns 1.00 -> 1.06'; l.657 'rotateX 90° -> 0'; l.734 'shrinks to 70%'; §4.5 l.851 paper_grain '±1% scale breathing'; §4.8 l.960 pop 1.08. The B1 as-built says 'whole-pixel positions and rising (never scaling) pictures, all needed for order-independent frames' (design l.2505-2506; web/css/dynamic.css:7-9). Ken Burns is already scaled and untested (web/js/dynamic/media.js:348-352, player/kenburns.js:41; the determinism fixture has no ken_burns, web/tests/dynamic/_fixture.js:81).
CHANGE: Add a 'Scale policy' to plan §0. Every scale animation in §3.3/§4.3/§4.5/§4.8 must be either (a) proven safe by a determinism-fixture case (1x300, 3x100, reverse) before any template depends on it, or (b) replaced (push/punch becomes a translate plus a clip of the content layer; settle and shrink become rise and opacity; tile flip becomes a rise; pop is limited to SVG icons), with each replacement listed as an as-built deviation in §4.8/§4.3. Add a Ken Burns still and a push cue to tests/determinism fixtures in B2a, so the policy is settled before B2e/B2f.

## [major] Contracts frozen (plan §1 vs design §10.3 B2)
PROBLEM: The plan's frozen list does not match the design's. Three items are missing: the semantic-class table, the role API, and SceneLook.lock semantics. The TemplateSpec 'events' signature is left open. The plan adds TemplateSpec fields without recording them. The look_here target grammar (the 'part' vocabulary that Python writes and JS parses) is not frozen.
EVIDENCE: Design l.2540: 'Contracts frozen: TemplateSpec fields; the semantic-class table; the role and cue APIs; SceneLook.lock semantics; the themes.json schema'. Plan §1.5 l.82: 'events: Callable[...]', while design §4.2 l.589 has 'events(scene, timed) -> list[VisualEvent]'. Plan l.81 adds dense/push_ok. The closed role list is at design §4.2 l.593 and core.js:230-233. Lock semantics are at §4.4 l.835. VisualEvent.target is 'board item id, item:part, panel' (schemas/timeline.py:232), but parts such as code lines, chart bars, formula symbols and the table 'column:<n>' (§4.3 l.694) are not defined anywhere.
CHANGE: Add §1.8-§1.11 to the plan. §1.8: the 19-class table of §4.2, frozen, with a Python/JS test that every TemplateSpec.semantic_class is in it. §1.9: the role API (closed role list, color()/inkOn() signatures, and how a studio/paper surface gets its ink), exported in the catalogue. §1.10: lock semantics auto/settled/pinned exactly as §4.4 l.835. §1.11: the target grammar (item, item:line:<n>, item:var:<sym>, panel:bar:<series>:<i>, panel:row:<n>, panel:column:<n>, panel), shared by events.py and focusRect. Give TemplateSpec.events the exact signature events(scene, timed, fmt) -> list[VisualEvent], and record dense/push_ok as additions in §4.2.

## [major] Cross-scene persistent elements (§4.3 chapter_bumper, predict; R-62)
PROBLEM: The plan's anchor() covers only an A->B morph. Several R1 elements persist across scenes and are not planned: the bumper's 'from last chapter' chip, which stays through the next scene; the 'Your prediction?' chip, which lives in the next explaining scene (whatever its template) until the answering beat; and the bumper's mini-roadmap, which needs every chapter's title. Templates reading other scenes' data also escape the chunk key's slices.
EVIDENCE: Design l.650: the anchor 'morphs into a <= 160 px from last chapter chip that stays at the top left through the next scene'. l.741: 'In the explaining scene: a small Your prediction? chip ... stays at the top until the answering beat'. l.649: mini-roadmap of all chapters. Plan §1.3 l.58 has only 'anchor(ctx) -> {el, rect}|null'. aadhi/compose/motion/chunks.py:198-212 slices only the chunk's scenes, the outgoing scene, the kicker chapters and the globals.
CHANGE: Add ResolvedLook.carry: [{kind:'anchor'|'prediction', text|tex, icon?, until_t, resolve_t?, answer?}] and ResolvedLook.roadmap: [{title, state:'past'|'current'|'next'}], filled by the resolver from Beat.answers/HookRef and the chapters. Draw them in a stage-level overlay layer (cues.js-like, not per template), so every template inherits them. Add a test that a template reads no timeline data outside its own scene, the outgoing scene and presentation (extend the STAGE_TIMELINE_FIELDS scan, tests/compose/motion/test_chunks.py:197-213).

## [major] 9:16 structure: acts, segments, energy (R-18, R-52, §4.5, §2.1)
PROBLEM: B2d defines background and accent changes only for 16:9. 9:16 needs the background preset and the accent to change per act (Learning) or per segment (Social). No act or segment detection is planned. The B1 per-chapter behaviour in both formats is not named as a bug to fix. 'Energy Lively' has no stored input, so a re-run of direct.py (build_timeline, New layouts) cannot honour it.
EVIDENCE: Plan l.147: 'one background preset per video (+ per chapter for builds_on=false or energy Lively)'. Design R-18 l.203: 'Accent and background at act boundaries | ... at segment boundaries'. §4.5 l.844: '9:16 Learning: the preset changes at act boundaries; Social energy: at segment boundaries'. §2.1 l.308. Act definition at §1.4 l.156. B1 uses chapters in both formats (presentation.py:176-192, :232). Energy exists only in GenerationOptions.look (design l.1419, l.1445); Presentation (§6.7 l.1531-1559) has no energy field.
CHANGE: Add to B2d: (1) acts(screenplay) and segments(screenplay) functions (Hook = intro + promise; Build 1 runs up to the mid-reel check; Build 2; Check = recap + outro), with tests on a reel fixture. (2) The boundary unit is chosen by format and Presentation.reel_profile: chapter for 16:9, act for 9:16 Learning, segment for 9:16 Social. This unit drives the accent, the background preset and the music layers. (3) Make energy an explicit direct.py parameter (default 'balanced'), and record the schema gap (an energy field is needed on Presentation, or B4 must store it) as an open item for B4.

## [major] R-54 vs bumper/intro wipes; W2 iris in R1 paths (§3.3, §4.3, §4.9)
PROBLEM: The design contradicts itself around full-frame colour changes, and the plan does not decide between the readings. (1) chapter_bumper does a 'hard cut out' of a full-bleed colour block, but R-54 requires a ≥ 300 ms cross-fade and B1 forces cross-fades around colour_block. (2) The brand wipe is described both as the intro's own exit (scene length = t_exit + exit) and as a transition 'out of aadhi_intro', which is painted inside the next scene. (3) The 9:16 intro exits 'while the next scene's iris opens', the Soft family is 'crossfade / iris', and hooks use iris, yet iris is W2.
EVIDENCE: Design l.651 'hard cut out'; R-54 l.265 'full-frame colour changes cross-fade ≥ 300 ms'; presentation.py:221-222; web read divergence 9 (the stage itself does not enforce R-54). §3.3 l.506 and l.529 vs §4.9 l.997. l.535 'the next scene's iris opens'; §4.8 l.980 Soft 'crossfade / iris'; §4.9 l.999 Iris W2. Plan B2e l.160-161 lists only push, colour_wipe and morph.
CHANGE: Record three decisions in the plan and the design's as-built. (a) The colour wipe is B's transition_in: it sweeps the accent block across and reveals B, and scene length = t_exit (the §3.3 formula is amended). (b) Out of a chapter_bumper, use a crossfade of ≥ 300 ms, or a reverse wipe that reveals the next scene, never a hard cut. (c) In R1, the 9:16 intro exit is a slide-down over a crossfade, and Soft uses cut/crossfade only. Extend the R-54 test to assert that no frame pair across any boundary has a full-frame luminance step without a ≥ 300 ms ramp, for each R1 transition and family.

## [major] §3.3 Aadhi timing and composition inputs (aadhi_intro / aadhi_recap)
PROBLEM: B2f plans only the presenter dict and t_exit. These §3.3 items are missing: the fixed speech start; scene length = t_exit + exit, clamped to [5.0, 8.0], with the title holding to 5.0 s; the 7.1 s too-long threshold; the recap defaults and its toggle; the DYNAMIC_AADHI_INTRO fallback; where intro_layout and reel_card_x come from; the studio surface token and the 192-frame wall luminance measurement the validator needs; the logo bug. The preflight checks 'Aadhi time' and 'Frame 0 (9:16)', which need B2's zones and templates, are not assigned to any batch.
EVIDENCE: Design §3.3 l.506 ('speech start is fixed (0.8 s in 16:9 ..., 0.15 s in 9:16) ... clamped to [5.0, 8.0] ... later than 7.1 s ... title holds until 5.0 s'); l.519 ('fixed studio surface token; B2 measures the wall's luminance over all 192 frames'); l.538/542 recap 6.0/5.0 s; §3.4 l.550; Presentation.intro_layout/reel_card_x/aadhi_recap at §6.7 l.1545-1555; validator 'including the measured Aadhi studio wall' at l.940; §7.12 l.1990-1991. Plan B2f l.173-175. Preflight does only 4 checks (preflight.py:101).
CHANGE: Add an 'Aadhi scenes' item to B2f. build_timeline sets audio_offset to 0.8/0.15, sets duration = clamp(t_exit + exit, 5, 8) from the Aadhi beat's word timings, and reads intro_layout, reel_card_x and aadhi_recap from Presentation. Fall back to hook_question when the presenter is disabled. A scripts/measure_studio_wall.py writes the studio token and the worst-case wall luminance into themes.py, and the validator checks the title stack against it. Add the preflight checks 'aadhi_time' and 'frame0_hook' (zones.py hook band) within schema 1, as type_floor was added. Test the scene lengths at speech ends of 3.5, 6.8 and 7.3 s.

## [major] Text slots: one source for textfit, templates, preflight, lints and the gallery (§4.6, R-29)
PROBLEM: textfit.py, every template, preflight's shrink minimum, the B4 lints and the gallery's 'longest allowed text' all need the same per-slot spec, and the plan defines none. The catalogue has no type tokens (line-height, tracking, size ranges), so Python measurement cannot match Chromium. The plan also leaves undecided whether the page applies text_fit.lines or wraps with CSS. Two design values conflict with R-29 and need a recorded resolution.
EVIDENCE: §4.6 l.871 gives line heights and tracking; l.875-877 say text is fitted per slot, preflight shrinks 'down to its minimum size'. Preflight falls back to MIN_TEXT_PX when the stage sends no min (preflight.py:85-86, :389). Plan §1.6 l.88-90 has no type/slots, and B2b l.122 has only 'size+line-break choice per slot'. Conflicts: code_walkthrough '34 px' (l.719) is below the R-29 floor 'never < 36' (l.219); the bumper numeral at 320 px (l.649) exceeds display 140-200.
CHANGE: Add §1.12 'Slot specs' as TemplateSpec.slots: {slot: {face_role, weight, size:[min,max] per fmt, max_lines, box per fmt/variant (from zones.py regions), line_height, tracking, max_words/max_chars}}. Generate it into the catalogue. textfit.py, the templates (through a _shared.js helper), preflight (min = slot.min) and the gallery fixtures read only this. State that the page uses text_fit.size as the start size and CSS wrapping, and that Python's lines are only for lints and the hook-band check. Resolve the code size (36 px with ≤ 17 lines, or a recorded exemption of the code role) and the numeral (an outline 'decorative' role outside R-29) as deviations.

## [major] Gallery and acceptance (§10.3 B2 scope and acceptance)
PROBLEM: The scope's 'zones.py with the safe-zone overlay' is missing from the plan. The gallery as planned (static rendering plus golden frames) cannot support the owner's checklist items 'smooth motion', 'no flashes', 'legible at phone size' and 'safe zones'. The design's automated test 'text fit against page overflow on the gallery fixtures' is replaced by a textfit-vs-Chromium comparison.
EVIDENCE: Design l.2534 'zones.py with the safe-zone overlay'; §9.6 l.2294 (critical box, caption band, generic UI silhouette); acceptance l.2544; test l.2542. Plan B2g l.180-182; B2b test l.124.
CHANGE: Add to B2a/B2g: (1) web/js/dynamic/zoneOverlay.js, a stage option overlay:'zones' that draws UNIVERSAL plus a platform choice and the caption band, used by the gallery now and the Studio in B4. (2) A gallery with play/scrub in live mode, a 'phone size' preview at 360x640, an overlay toggle, and Tamil/Hindi/longest-text variants per slot spec. (3) A slow test running preflight's textBoxes checks (zones, overflow, contrast, type floor) on every gallery fixture x format x family. It must pass with no 'block', and that is part of 'every R1 template passes in both formats'. (4) docs/B2_GALLERY_CHECKLIST.md, with each acceptance item mapped to the gallery control or automated check that supports it.

## [major] Validator and background contrast (§4.5, §4.7, R-55, R-56, §7.5)
PROBLEM: The validator plan leaves out several design requirements: (1) the analytic worst-case luminance per preset and palette, which needs a Python mirror of each JS preset's colours; (2) the 'force scrim' output; (3) structure accent against the text drawn on it, with a ±0.06 L nudge; (4) the chroma rule for small saturated text; (5) numeric tests for R-55 (period, speed, behind-text luminance ratio); (6) R-56 (≤ 3 hue families on screen), which chart_build plus an accent card border can exceed.
EVIDENCE: §4.5 l.859: 'Each preset declares its worst-case luminance per palette, computed analytically when the catalogue is built; the validator forces the scrim on'. JS colours: mesh_drift.js:96-100, colour_block.js:67-82. §4.7 l.942-943; §7.5 l.1917 ('L ≥ 0.6 or be ≥ 32 px'); R-55 l.266; R-56 l.267. Plan B2a l.103-104 and B2e l.159 ('low salience (R-55)', no test).
CHANGE: In themes.py, add preset_colours(preset, tokens), mirroring every R1 preset's colors() with a JS/Python golden-vector parity test. Add catalogue.scrim_required[preset][family][slot_role], read by templates. Add the accent-vs-bumper-text check with the ±0.06 L nudge (content never nudged). Add the chroma rule. Add R-55 tests sampling each preset over 30 s: period ≥ 15 s, displacement ≤ 2% of width per second, behind-text luminance max/min ≤ 1.5. Add an R-56 check of hue families in the text and graphic colours per gallery frame, or a rule that entity-coloured scenes use neutral card borders.

## [major] cues.js details (§4.8, R-26, R-54, source scan)
PROBLEM: Four problems. (1) look_here has only a rounded-rect outline; the design also needs ring and bar shapes (code_walkthrough must use a bar). (2) The 'box-shadow hole' dims the whole frame, including the background, instead of dimming the other elements to 70%, and a 250 ms full-frame dim conflicts with R-54's ≥ 300 ms. (3) The 'ctx.cues for the rare template-driven cue' path draws cues that are absent from scene.events, so events.py's limits and the signal.* lint cannot see them. (4) Stage-driven cue.term looks for .rt-k spans, which board_generic's word splitting destroys. Separately, the scan bans outlines in templates, but list_build and worked_steps need non-cue outline states.
EVIDENCE: §4.8 l.969 'rounded rect, ring or bar'; l.719 'a bar, no zoom'; R-54 l.265; plan l.64-65 and l.71-72. board_generic.js:464-473 and :487-502 build plain word spans (web read bug 1). sourceScan.test.js:260, 293, 303, 311 ban outline and text-decoration in templates; design l.676 'current item ... 3 px ink outline', l.716 'ok outline'.
CHANGE: lookHere takes shape rect|ring|bar (focusRect returns {rect, shape, radius}). Dim by setting opacity 0.7 on sibling content elements through the stage, never with a full-frame overlay, and ramp it over ≥ 300 ms. Remove the template-driven drawing path, so ctx.cues is read-only (active(t)). Require word-split helpers in _shared.js to keep .rt-k on term words. Add a stateRing() helper in _shared.js for current/past states that uses only ink or ok roles, scanned as allowed, and keep 'outline' banned elsewhere.

## [major] Asset-side work and the non-timeline template (§4.3)
PROBLEM: Several R1 template behaviours need pipeline or engine work that the plan neither schedules nor defers. (1) animation_stage needs a Manim colour mapping, a background from the asset family and a colour_is_content dark card: a new Manim config input and content key. (2) The hook_question 'mystery' image must be blurred when the asset is built. (3) A 9:16 contain_blur in the MP4 needs either CSS blur (about 22 ms per frame) or a blurred extraction, and the latter changes the frozen mediaPlan schema. (4) thumbnail is a single frame outside the timeline, 16:9 only, which contradicts the plan's 'Each template: both formats'. (5) The outro logo bug comes from the logo clip's last frame.
EVIDENCE: §4.3 l.727-728 ('a new Manim config input and content key'), l.634 ('blurred when the asset is built because CSS blur costs ≈ 22 ms per frame'), l.683 contain_blur, l.662 thumbnail, §2.8 l.470 (9:16 cover = frame 0), §3.2 l.499. Frozen mediaPlan entry: aadhi/compose/motion/media.py:63-116. Plan l.172-176.
CHANGE: Add 'B2f-assets'. (a) Either implement the Manim colour config and content key in pipeline/assets.py, or record a deferral to B4 and draw colour_is_content clips in a dark card now. (b) Add a blurred derivative for mystery images, built at asset time. (c) contain_blur adds an optional `filter: 'blur'` field to mediaPlan entries (additive, like asset_key) that the extractor applies with ffmpeg boxblur, recorded as a stage-contract extension. (d) TemplateSpec.formats for thumbnail = {landscape}; the gallery renders it in a synthetic one-scene 1920x1080 timeline, downscaled to 1280x720; the export path stays in B4. (e) Add a branding derivative for the logo's last frame.

## [major] Reduced motion (§4.8, §7.3)
PROBLEM: B2 introduces every feature covered by the reduced-motion mapping (camera push, punch-zoom, Ken Burns on full_bleed_media and interactive_poster, soft_waves parallax), but the plan has no mapping or test for it. Reduced motion is one of only two sanctioned live/MP4 differences.
EVIDENCE: §4.8 l.986: 'camera pushes and punch-zooms into the outline form of cue.look_here, and switches off parallax and Ken Burns'; §7.3 l.1876. The stage already has a reducedMotion option (stage.js StageOptions :307-315). Plan §1.4 and B2e are silent.
CHANGE: Add to cues.js and the backgrounds: when ctx.reducedMotion is set (live only), push and punch become the outline form, Ken Burns is frozen at its start frame, soft_waves parallax and every drift are frozen, and stylised transitions become a crossfade. Add jsdom tests asserting each mapping, and a render-mode test asserting it is never applied there.

## [major] Live/MP4 parity and engine_version (plan principle 2, §1.2, Q6)
PROBLEM: Principle 2 says resolved choices stored in the timeline keep old looks, but the MP4 always rebuilds the timeline from the screenplay. After B2 ships, a stored B1 (engine v1) timeline is still served to the live player while the MP4 for the same revision is rebuilt with B2 templates. The two then differ, against §7.4(b) and the B1 rule that the player and MP4 never show different engines.
EVIDENCE: aadhi/compose/video.py:434 (render rebuilds through build_timeline); aadhi/api/timelines.py:110-138 (current_timeline serves the stored timeline unless the DYNAMIC_ENABLED engine differs); design §6.7 l.1646 (engine_version labelled 'stage contract') and l.1693; plan l.22 and l.50.
CHANGE: Amend principle 2: looks are stable because direct.py stores settled SceneLooks in the screenplay; the timeline is derived. In B2a, extend current_timeline so it rebuilds when the stored presentation.engine_version differs from ENGINE_VERSION. The stage keeps rendering the v1 shapes (variant 'safe', text_fit.scrim) as a fallback and refuses engine_version above the known version with timeline_invalid. Record in §6.7 that engine_version is the look-engine generation, separate from aadhiStage.version (3).

## [major] Deviation register (§10.2 gate 6, as-built practice)
PROBLEM: The plan records only the JS catalogue as a deviation. It makes several other changes to design decisions without recording them.
EVIDENCE: The unrecorded changes: (1) fixes.safe replaces 'Force the template's safe variant' (§7.12 l.1987). (2) Stage-driven cues replace 'Cues only through ctx.cues' (§4.2 l.590, l.594). (3) ResolvedLook/TimelinePresentation gain fields beyond §6.7 l.1633-1656. (4) TemplateSpec gains dense/push_ok. (5) 16:9 kinetic chunks replace §4.11 l.1025. (6) build_timeline becomes a direct.py trigger (§6.8 l.1715). (7) paper_grain uses a generated tile instead of 'a vendored grain PNG' (§4.5 l.851). (8) Gallery goldens on Windows instead of 'in Docker' (l.2542). (9) Push becomes a translate (§4.8 l.969).
CHANGE: Add plan §4 'Deviations to record in DYNAMIC_VIDEO_DESIGN as-built'. It is a table: design line, decision, reason, test that pins it. It covers the nine items above plus every decision taken from findings above (R-54 wipes, iris, code size, scale policy). Make updating the design doc a B2g exit criterion.

## [major] Captions: 16:9 kinetic chunks vs sidecars (Q4, §2.6, §4.11)
PROBLEM: A separate 16:9 chunker would produce burned and in-page captions whose breaks differ from the .srt/.vtt and the mov_text track, which are built from Timeline.captions. That breaks the 'Both versions' guarantee and the rule that sidecars carry the same phrase captions. In 9:16 the plan does not say that the sidecars follow the kinetic chunks; Timeline.captions currently comes from portrait_caption_cues.
EVIDENCE: §4.11 l.1025 ('16:9 uses the existing phrase captions'), l.1040; §2.6 l.430; plan B2c l.133-138; presentation.py:267-320 still builds the 9:16 Timeline.captions.
CHANGE: In 16:9, kinetic.py derives chunks 1:1 from Timeline.captions cues (same text, breaks and times) and adds only word timings, key and blank. In 9:16, Timeline.captions is generated from the kinetic chunks, with blank chunks included, and portrait_caption_cues is retired. Add a test that for both formats the SRT cue list equals the kinetic chunk list (text, start, end).

## [major] Boundary timing rules (R-52, R-18, §4.8 choreography)
PROBLEM: Three timing rules have no owner in the plan: R-52's accent cross-fade of 600-1000 ms at boundaries; R-18's 'never in the 2 s before a reveal' for macro changes; §4.8's 'at most one camera move at a time; nothing else enters during a camera move'. The resolver stores tokens per scene, so without work the stage switches the accent instantly.
EVIDENCE: R-52 l.263 ('cross-faded 600–1000 ms'); R-18 l.203; §4.8 l.973. The R-26 list in plan B2c l.129-131 omits them; resolver tokens are per scene (presentation.py:232).
CHANGE: Add ResolvedLook.tokens_from (the previous unit's struct tokens) plus an accent_fade seconds value in [0.6, 1.0]. The stage interpolates struct.* in OKLCH over that window when no colour wipe carries the change. events.py rejects or shifts a camera cue that overlaps another camera move or a reveal. direct.py moves a boundary-only change that lands within 2 s of a reveal. Add tests for each.

## [minor] B1 carry-overs assigned to B2
PROBLEM: The plan does not list or test the B1 open items the design hands to B2, nor the B1 bugs that the R1 templates replace.
EVIDENCE: Design l.2517-2526: 9:16 split picture is small; a 9:16 terminal card collapses; a same-beat taller card pushes another out; 12 caption chunks under 1.0 s; preflight remedies reach only the MP4 ('until B2/B4 store them'). Web read: quiz rich-lite is lost (board_generic.js:487); texts() reports bg without the scrim (board_generic.js:978); live presenter playbackRate (stage.js:121-135); Outfit 600 is not vendored though dynamic.css asks for it.
CHANGE: Add 'B1 carry-overs' to B2f, with one regression test each. Decide explicitly whether persisting the remedies is done in B2 or deferred to B4, and record the choice.

## [minor] Sub-batch order (B2c before B2d)
PROBLEM: events.py needs the template specs, and kinetic blanking depends on the template id. R-35 needs the cue_term events, so events must run before kinetic. B2c is scheduled before direct.py exists and does not say where the template id comes from.
EVIDENCE: §6.8 l.1716 (events inputs include 'template specs'); §4.11 l.1027; R-35 l.231; plan l.126-140.
CHANGE: State the build_timeline order (direct -> events -> kinetic -> resolver). Make events.py and kinetic.py take the template id per scene as an input: SceneLook.template, or board_generic from the B1 resolver until B2d. Add an integration test of that order in B2d.

## [minor] R-49 SFX details
PROBLEM: 'In word gaps', '≤ 1 s each' and the 'Sound effects: off' option (presentation.sfx) are missing from the plan. Until B3, the B1 engine still mixes a tick for every countdown second, which violates R-49 in every B2 MP4, and this is not recorded.
EVIDENCE: R-49 l.255; §4.10 l.1015-1018 (removed 'the countdown riser'); aadhi/compose/motion/engine.py:493-494 ('a tick per countdown second').
CHANGE: In events.py, place SFX only in word gaps of at least the sound's length and emit no Timeline.sfx when presentation.sfx is false. Add tests for both. Record 'MP4 audio stays classic until B3 (per-second ticks)' as a known B2 deviation.

## [minor] TemplateSpec.dense misstates R-44
PROBLEM: 'music off in gaps_bookends' is wrong. Dense scenes have music off with 1.5 s fades in every mode; gaps_bookends is only the lesson default when dense runtime is 30% or more.
EVIDENCE: R-44 l.250; plan l.81.
CHANGE: Change the field doc to: 'dense: background frozen (R-55), no SFX (R-49), music off with 1.5 s fades (R-44, B3)'.

## [minor] SceneLook.palette_slot unused
PROBLEM: Accents are recomputed from the chapter or act index. Inserting or removing a chapter therefore recolours settled scenes, although only 'New colours' may restyle them.
EVIDENCE: §6.7 l.1570 palette_slot; §4.4 l.833 (output), l.835 (locks); accent_hue by chapter at presentation.py:169-173.
CHANGE: direct.py writes palette_slot (the accent step) into SceneLook for each unit's scenes, and the resolver derives the accent from palette_slot when it is present. Extend the 'editing one scene never changes another settled look' property test to cover inserting a chapter.

## [minor] definition_card paper interlude
PROBLEM: The light 'paper interlude' surface in dark families is neither planned nor deferred. It conflicts with R-52 (fixed content colours) if entity or warn/ok colours appear on the light card.
EVIDENCE: §4.3 l.668; R-52 l.263; plan B2f group 2.
CHANGE: Either implement it with light-mode content tokens and forbid entity colours on that variant (validator case: light surface in each dark family), or defer it to W2 as a recorded deviation.

## [minor] zones.py completeness
PROBLEM: The plan's zones.py omits the 16:9 12-column grid with 24 px gutters and the named regions that templates are specified against. The source of the end-screen flag is undefined.
EVIDENCE: §2.5 l.381-382 ('full | left-55 + right-45 | centre-60 | top-band + stage'), l.390; §4.3 l.635 'centre-60', l.676 'left 55%'; §2.6 l.427 (end-screen space comes from the YouTube destination).
CHANGE: zones.py exports GRID16 and REGIONS[fmt][name] in the catalogue. Derive end_screen = (destination == 'youtube' and fmt == 'landscape') in the resolver.

## [minor] Pillow WOFF2 in the container
PROBLEM: The design asks B2 to verify that Pillow in the Debian image reads WOFF2. The plan only says it will be 'documented'.
EVIDENCE: §4.6 l.878; scope l.2534; plan l.123.
CHANGE: Add a container-marked test (AADHI_CONTAINER_TESTS) that loads every vendored WOFF2 used by textfit through Pillow. If it fails, vendor.mjs stores TTF copies and textfit prefers them; test that path too.

## [minor] Icons and entities in ResolvedLook (Q5)
PROBLEM: Lucide and Tabler SVGs use circle, rect, line and polyline elements, so 'paths:[...]' cannot hold them. key_terms stroke-draws icons, which needs stroke geometry. Per-scene `entities` copies the lesson-wide map that R-30 requires to stay constant.
EVIDENCE: §4.3 l.644 ('the icon's strokes draw (400 ms)'); §4.7 l.902; R-30 l.220; plan l.40-41.
CHANGE: icons.py converts every element to path d deterministically and stores {name, set, viewBox, stroke_width, paths}, capped in size and sanitised in Python and in the stage's svg(). Move entities to TimelinePresentation.entity_colors (lesson-wide), which the chunk key already hashes through the global presentation.

## ANSWERS
Q1 (generated JS module instead of themes.json): Acceptable. The design freezes 'the themes.json schema' (l.2540), so the plan must make three things explicit. (a) Record the deviation in §4.7 and §10.3 B2. (b) Freeze the catalogue object's schema, versioned, as the replacement contract. (c) Generate it from canonical JSON (sorted keys), so --check compares content and not formatting. Python-side parity must run in the Python CI job, because the web job has no venv (tests-web note 6). web/js is already in BUNDLE_PATHS and inside the source-scan scope, which is good.

Q2 (stage-driven vs template-driven cues): Stage-driven satisfies R-26 better, because every cue comes from scene.events and events.py's limits and the signal.* lint see all of them. Make it the only path. Drop the 'rare template-driven cue' (plan l.65), since a cue drawn outside events bypasses R-26. ctx.cues becomes read-only (active cues at t). Freeze the target grammar. The stage owns the camera transform, and templates keep .rt-k on word-split terms. This is a change to §4.2 l.590 and l.594 and must be recorded.

Q3 (direct.py inside build_timeline for missing or auto looks): The design does not list it. §6.8 l.1715 names generation, save (auto or unfit), and New colours/New layouts. It is acceptable as the pure stand-in that B1 already uses in presentation.py, under these conditions:
- no history is read (§6.8 l.1723);
- per-video choices (family, personality, fonts, caption and cue styles, entity map) come from the stored Presentation and are never re-chosen;
- settled and pinned looks are honoured, and fit()=0 re-picks only among templates compatible with the fixed neighbours;
- the result is deterministic, so the live (stored) and MP4 (rebuilt at video.py:434) timelines agree;
- nothing is persisted (B5's PUT stores it);
- it is recorded as a deviation, and the settled-look property test runs through build_timeline.

Q4 (kinetic_captions in 16:9): Yes in substance. R-31 and R-35 apply in 16:9, and Timeline.captions cannot carry key or blank flags. But §4.11 l.1025 must be amended, and 16:9 chunks must equal the Timeline.captions cues 1:1, with only key, blank and word times added. Otherwise the burned, soft-track and SRT/VTT captions diverge (§2.6 l.430). In 9:16, Timeline.captions should be derived from the kinetic chunks.

Q5 (icon geometry and per-scene entities in ResolvedLook): Icons, yes, because it keeps the timeline self-contained and the chunk key correct. Store normalised path data (convert circle, rect, line and polyline), the viewBox and the stroke width, capped and sanitised on both sides, so that stroke drawing works. Entities, no per-scene copy: put one lesson-wide map in TimelinePresentation, matching Presentation.entity_colors (§6.7 l.1553), so R-30 constancy holds by construction.

Q6 (engine_version 2): Yes, bump it, since look semantics and the chunk key change. Re-label it in §6.7 l.1646 as the look-engine generation, which is not the stage contract (that stays at 3). The MP4 never sees a v1 timeline because render rebuilds (video.py:434). The live player can, because current_timeline serves stored timelines (api/timelines.py:110). So:
- current_timeline rebuilds on an engine_version mismatch, the same way it does for a DYNAMIC_ENABLED mismatch;
- the stage still renders the v1 shapes (variant 'safe', text_fit.scrim) as a fallback;
- the stage refuses engine_version above the known version with timeline_invalid (rollback safety).

Q7 (what the plan misses or gets wrong): See the findings. The largest gaps are:
- the scale and camera-push policy against the B1 determinism finding;
- the frozen-contract list (semantic-class table, role API, lock semantics, target grammar);
- cross-scene chips and the roadmap;
- 9:16 act and segment boundaries, and energy;
- R-54 around colour-block wipes and cuts, and iris (W2) appearing in R1 paths;
- the §3.3 Aadhi timing and studio-wall validation;
- one source for slot specs;
- the safe-zone overlay and a gallery that can support the acceptance checklist;
- the analytic preset contrast and the R-55/R-56 tests;
- the reduced-motion mapping;
- asset-side work (Manim colours, mystery blur, contain_blur, the thumbnail path);
- a deviation register.

Correct as planned:
- 23 R1 modules (22 new plus board_generic);
- 6 R1 backgrounds;
- module locations;
- §4.4 algorithm, post-pass and property tests;
- R-26 limits;
- R-32/R-33 chunker rules;
- content palette tests;
- the mediaBox hook (it fixes the B1 media-key risk);
- removing the zone and colour-maths copies;
- the Aadhi frame cache.

## MISSING
Scope items:
- zones.py 'with the safe-zone overlay' (l.2534, §9.6 l.2294).
- The 'Pillow WOFF2 check in the container' as a real container test, not documentation.
- The studio surface token and the 192-frame wall measurement (§3.3 l.519, §4.7 l.940).
- The role API (scope l.2531).
- The paper interlude variant (l.668).
- The animation_stage Manim colour mapping and content key (l.727-728).
- Blurred derivatives for the mystery hook and 9:16 contain_blur.
- The thumbnail render path, which is 16:9 only.
- The outro logo bug.
- Emoji as images (§4.6 l.880).
- Anek axis rotation for Indic lessons (l.867).
- The live p5 path of interactive_poster (l.764).

Contracts:
- The semantic-class table.
- The role API.
- SceneLook.lock semantics.
- The exact TemplateSpec.events signature.
- The look_here target 'part' grammar.
- The themes.json schema (renamed to the catalogue schema, recorded).

Rules not wired to any module or test:
- R-52 accent cross-fade 600–1000 ms.
- R-18 9:16 per-act and per-segment changes, and 'never in the 2 s before a reveal'.
- §4.8 'one camera move at a time / nothing enters during a camera move'.
- R-49 'in word gaps', ≤ 1 s, and sfx=false.
- R-55 numeric limits.
- R-56 colour budget.
- The §7.5 chroma rule.
- Structure accent vs bumper text with the ±0.06 L nudge.
- R-27 direct labels and no legends (chart_build).
- R-30 hold ≥ 3 s after the formula completes.
- R-63 fact card as the dominant final frame, and no MYTH stamp on true_false hooks.
- R-09 'Your turn' 4–6 s think ring.
- warn and ok always shown with an icon (§4.7 l.894-895).
- §3.3 timing: fixed speech start 0.8/0.15 s, scene length clamp(t_exit + exit, 5, 8), 7.1 s threshold, recap defaults and toggle, DYNAMIC_AADHI_INTRO fallback.

Preflight checks that need B2 parts and have no batch:
- 'Aadhi time'.
- 'Frame 0 (9:16)'.
- 'Captions R-32/R-33 re-chunk' (§7.12).

Tests:
- 'Text fit against page overflow on the gallery fixtures' as preflight checks over every gallery fixture.
- Gallery golden frames in Docker (the plan says Windows now; record the deviation).
- Determinism cases for every scale animation, Ken Burns, push, morph, and each new background and transition.
- Reduced-motion mapping tests.
- A Python/JS parity test for preset colours.
- SRT equals kinetic chunks.
- Act and segment detection.
- One regression test per B1 carry-over (9:16 split picture, terminal collapse, same-beat taller card, chunks under 1.0 s, quiz rich-lite, scrim bg in texts()).

Acceptance support:
- The gallery needs playback and scrub, a phone-size preview, the zone overlay and a written checklist, so the owner can judge 'smooth motion', 'no flashes', 'legible at phone size' and 'safe zones'.
- 'Every R1 template passes in both formats' needs a definition: the checklist plus zero preflight blocks per template, format and family.
- The thumbnail is 16:9 only, which must be reconciled with 'both formats'.

Process:
- A deviation register for the design's as-built section (fixes.safe, stage-driven cues, the schema additions, 16:9 kinetic chunks, build_timeline as a direct.py trigger, push as translate, the generated grain tile, Windows goldens, the R-54 wipe and cut decisions, iris replacements, code size vs R-29).
- An explicit decision on whether persisting the preflight remedies is B2 or B4 work.