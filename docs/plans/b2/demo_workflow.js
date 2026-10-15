export const meta = {
  name: 'b2-demo',
  description: 'Demo push: direct.py + events wire the built templates into real videos; 6 more R1 templates in parallel; integrate, render demo MP4s, one review, one fix',
  phases: [
    { title: 'Build', detail: 'direct.py/events + six templates in parallel (own files only)' },
    { title: 'Integrate', detail: 'wiring, registry, gates, demo MP4s' },
    { title: 'Review', detail: 'one reviewer' },
    { title: 'Fix', detail: 'one fixer + gates' },
  ],
}

const ROOT = 'C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt'
const PY = `${ROOT}/.venv/Scripts/python.exe`
const PLAN = `${ROOT}/docs/plans/b2`
const BASE = args.base

const ENV = `Repository ${ROOT} (Windows 11, branch overhaul/v2, base ${BASE}). Use ${PY} (pytest-xdist not installed; --basetemp=C:/t/<label>). The full fast suite (${PY} -m pytest -m "not slow and not network" -q --basetemp=...) takes ~15 min: run it in the background and poll; never block one tool call longer than ~9 min. JS: npm test; npx tsc -p tsconfig.json. Lint: .venv/Scripts/ruff.exe check aadhi tests scripts evals docker. Generated-file checks: scripts/gen_env_example.py, -m aadhi.schemas.generate, evals/make_fixtures.py, scripts/gen_chromium_seccomp.py, -m aadhi.compose.dynamic.catalogue, -m aadhi.compose.dynamic.faces, -m aadhi.compose.dynamic.icons, scripts/gen_third_party_notices.py, node scripts/gen_colour_vectors.mjs, scripts/lock_requirements.py (each with --check). Never read .env or secrets; never call paid APIs. No many-minute measurement in CI. Do not commit or change git state.`

const CONTEXT = `DEADLINE MODE: the owner has 2 days to a WORKING DEMO of the Dynamic video mode (docs/DYNAMIC_VIDEO_DESIGN.md): real generated lectures must render with the new templates instead of board_generic everywhere. Quality still matters (it is shown to people), but scope is cut: build what makes the demo work, record the rest as open items. Read ${PLAN}/HANDOFF.md, ${PLAN}/b2_plan_v2.md (§1.4 template contract, §1.5 cues, §1.6 TemplateSpec, §1.7 targets, §2 rules, §A steps 4-6), ${PLAN}/b2c_review.md and ${PLAN}/b2c_design_notes.md (B2c: _shared.js, cues.js, state.js and the pilot templates formula_focus, media_split, list_build — read them as the pattern to follow), design §4.2-§4.4 (classes, the template texts, the fit/signal table), §4.7-§4.9. Existing code: web/js/dynamic/ (stage.js TEMPLATES, templates/*.js, _shared.js, cues.js, state.js, core.js), aadhi/compose/presentation.py (resolve_looks picks board_generic today: presentation.TEMPLATES), aadhi/compose/dynamic/templates.py (TemplateSpec, fit, implemented, slots, consumes), aadhi/schemas/timeline.py (VisualEvent), aadhi/compose/timeline.py (build_timeline). Invariants: classic output unchanged (tests/invariance); determinism (every frame a pure function of t; 1x/3x/reverse gate); templates follow the contract and the source scan (no emphasis drawing, no .timeline, catalogue geometry only, TeX via ctx.renderTex).`

const RESULT = {
  type: 'object',
  properties: {
    summary: { type: 'string' }, files: { type: 'array', items: { type: 'string' } },
    contracts: { type: 'string' }, requests: { type: 'string', description: 'exact changes needed in shared files (for the integrator)' },
    tests: { type: 'string' }, open_items: { type: 'string' },
  },
  required: ['summary', 'files', 'contracts', 'requests', 'tests', 'open_items'],
}

const PARALLEL_RULE = `Several agents work in this same repository AT THE SAME TIME. Edit ONLY the files your scope names (new files of your own are fine). Do NOT edit shared files (stage.js, templates.py, catalogue.gen.js, presentation.py, timeline.py, schemas, _shared.js, cues.js, state.js, core.js, dynamic.css, sourceScan tests, existing fixtures) — put every change you need there into 'requests' with exact code. Run only your own tests and npx tsc; the integrator runs the suites.`

const DIRECT = `YOUR SCOPE: aadhi/compose/dynamic/direct.py (new) and aadhi/compose/dynamic/events.py (new) + their tests (tests/compose/dynamic/test_direct.py, test_events.py). direct.py: for each scene of a Dynamic screenplay choose the template deterministically: candidates = TemplateSpecs with implemented=True and release R1 whose formats include the lecture's format; score with spec.fit(scene, fmt, last=..., chapter_end=...); forced templates on their forced scene types; highest fit wins (ties: a fixed documented order; board_generic only as the fallback); variant choice per the template's rules (media_split media side flips only at chapter boundaries). events.py: generate the VisualEvents each chosen template consumes (its spec.consumes) from the screenplay/timing data the timeline already has: 'reveal' per item on its [SYNC]/beat time (R-17: 120 ms before the anchor word), 'var' events for formula symbols named in the narration, 'cue_term' on the first two narrated mentions of a [[term]] key term (R-35/R-26 limits: <= 1 new cue per 2 s, <= 2 concurrent), 'look_here' for focus sync cues and to satisfy R-16 holds, 'draw' for formula boxes; read the B2c templates and cues.js to see exactly which kinds/targets each reads. Provide one entry point the integrator calls from presentation.resolve_looks / build_timeline: e.g. direct.plan(screenplay, scenes, fmt) -> per scene {template, variant, events}. Pure, deterministic, fast (the 40-scene preview budget of tests/compose/test_dynamic_isolation.py, <= 300 ms, must still hold), imported lazily inside the Dynamic gate. Templates still unimplemented must never be chosen (they will become eligible automatically when their implemented flag flips). ${PARALLEL_RULE}`

const TEMPLATES = [
  ['definition_card', 'design §4.3 definition_card (R1, definition class).'],
  ['chapter_bumper', 'design §4.3 chapter_bumper (R1): on the colour_block background with the stored block tokens (block.ink/ink2), chapter number decorative outline numeral (deviation 14), title; the colour-wipe transition into it is B2f — use the existing crossfade/cut.'],
  ['hook_question', 'design §4.3 hook_question (R1, hook class): question/true_false/stakes/challenge variants (mystery deferred to B4, deviation 16), 9:16 hook band y 340-760 (frame0_hook rule).'],
  ['key_terms', 'design §4.3 key_terms (R1): per-term reveal, cue.term styles via cues.js (itemEl keeps .rt-k spans).'],
  ['worked_steps', 'design §4.3 worked_steps (R1, dense: background frozen R-55, no SFX): step-by-step derivation with TeX via ctx.renderTex, current/done states via state.js, look_here on <item>:step:<n>.'],
  ['recap_retrieval', 'design §4.3 recap_retrieval (R1): retrieval prompts revealed then answered, both formats.'],
]

phase('Build')
const tasks = [
  () => agent(`${CONTEXT}\n\n${ENV}\n\n${DIRECT}`, { label: 'build:direct', phase: 'Build', schema: RESULT }).then((r) => (r ? { id: 'direct', ...r } : null)),
  ...TEMPLATES.map(([id, brief]) => () => agent(`${CONTEXT}\n\n${ENV}\n\nYOUR SCOPE: the ${id} template ONLY: web/js/dynamic/templates/${id}.js implementing the full template contract (plan §1.4) like the B2c pilots (formula_focus/media_split/list_build) using templates/_shared.js helpers; ${brief} Both formats, dark and light families. Its own jsdom tests web/tests/dynamic/templates/${id}.test.js and fixtures (follow how the B2c templates make theirs: tests/compose/dynamic/template_fixtures.py / make_*_fixtures.py — write a NEW generator file for yours, do not edit theirs). In 'requests' give: the templates.py TemplateSpec changes (implemented=True, slots mirroring your JS constants, consumes), the stage.js import + TEMPLATES entry, and anything else shared. ${PARALLEL_RULE}`,
    { label: `build:${id}`, phase: 'Build', schema: RESULT }).then((r) => (r ? { id, ...r } : null))),
]
const built = (await parallel(tasks)).filter(Boolean)
log(`Built: ${built.map((b) => b.id).join(', ')}`)

phase('Integrate')
const integ = await agent(`${CONTEXT}\n\n${ENV}\n\nReports of the parallel builders:\n${JSON.stringify(built, null, 1)}\n\nYOUR SCOPE (Integrate): (1) apply every builder's 'requests' to the shared files, resolving conflicts; register each new template (stage.js TEMPLATES, templates.py implemented/slots/consumes; regenerate the catalogue; registry parity tests). (2) Wire direct.py + events.py into the Dynamic path: presentation.resolve_looks takes template, variant and events from direct.plan; bump ENGINE_VERSION (presentation.py, web/js/dynamic/engine.js and every pin of it) so stored Dynamic timelines rebuild at serve time with the new looks; keep classic output unchanged and the 40-scene preview budget. (3) Make every new template pass the gates: source scan, jsdom contract tests, the 1x/3x/reverse order-independence gate in real Chromium for a fixture per template in both formats (follow tests/determinism/test_template_pages.py), preflight on each fixture with zero blocks. (4) DEMO: build the showcase lecture tests/core/fixtures/showcase_ohms_law.json as a Dynamic lecture (one dark and one light R1 family; 16:9 and 9:16) through build_timeline, and RENDER real MP4s through the real Dynamic render engine (find how tests/determinism/test_render_acceptance.py or evals/render_parity.py renders a Dynamic MP4; use fake/synthetic audio as the tests do — never paid APIs); write them to ${ROOT}/final_videos/demo_b2/ (git-ignored) with a short README.md there listing which template each scene used; report the paths. Also regenerate the reference frames contact sheet (python -m tests.compose.dynamic.make_reference_frames, extended to the new templates) under docs/plans/b2/b2c_frames/ or a new docs/plans/b2/demo_frames/. (5) Run ALL gates and report counts: ruff, every generated-file check, npm test, npx tsc, the full fast suite (background + poll), the slow Chromium tests (tests/determinism/test_template_pages.py, test_template_fixtures.py, test_template_clips.py, test_board_real_page.py, test_stage_layout.py, test_live_css.py, test_stage_fonts.py, tests/compose/dynamic/test_preflight_stage.py and the new ones). Fix what fails.`,
  { label: 'integrate', phase: 'Integrate', schema: RESULT })

phase('Review')
const review = await agent(`${CONTEXT}\n\n${ENV}\n\nYou are an INDEPENDENT REVIEWER (READ-ONLY: never edit files or change git state; scratch work under C:/t/ only). The demo work is uncommitted in ${ROOT} vs ${BASE}. Reports:\n${JSON.stringify({ built, integ }, null, 1)}\n\nFind real defects that would hurt the demo or the codebase: wrong template choice or broken events in real lectures, visual defects (open the demo MP4s' frames: extract stills with ffmpeg at scene midpoints and look at them; and the reference frames), contrast/overflow/clipping, determinism, classic output change, a failing gate (run the fast suite, npm test, tsc, ruff, the slow template tests). Report each with severity (blocker/major/minor), file:line, evidence, suggested fix. Do not report matters of taste or documented deferrals.`,
  { label: 'review', phase: 'Review', schema: { type: 'object', properties: {
    findings: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
      problem: { type: 'string' }, evidence: { type: 'string' }, suggested_fix: { type: 'string' } },
      required: ['id', 'severity', 'file', 'problem', 'evidence', 'suggested_fix'] } },
    verified_ok: { type: 'string' } }, required: ['findings', 'verified_ok'] } })

phase('Fix')
const fixable = review ? review.findings.filter((f) => f.severity !== 'minor' || review.findings.length <= 12) : []
const fix = fixable.length ? await agent(`${CONTEXT}\n\n${ENV}\n\nFix these review findings at the root (tests that fail without the fix), re-render the demo MP4s if visuals change, then run ALL gates (as the integrator did) and report counts. Findings:\n${JSON.stringify(fixable, null, 1)}`,
  { label: 'fix', phase: 'Fix', schema: { type: 'object', properties: {
    outcomes: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, outcome: { type: 'string' }, what: { type: 'string' } }, required: ['id', 'outcome', 'what'] } },
    gates: { type: 'string' }, demo: { type: 'string' }, open_items: { type: 'string' } }, required: ['outcomes', 'gates', 'demo', 'open_items'] } }) : null

return { built, integ, review, fix }
