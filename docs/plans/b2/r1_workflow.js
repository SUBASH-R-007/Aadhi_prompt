export const meta = {
  name: 'b2-demo-scenes',
  description: 'The templates the demo lecture actually falls back on: full_bleed_media, animation_stage, side panels in worked_steps/key_terms, check, predict; integrate, re-render the demo, one review, one fix',
  phases: [
    { title: 'Build', detail: 'four templates plus side-panel support, in parallel (own files only)' },
    { title: 'Integrate', detail: 'shared changes, registry, direct/events, gates, demo MP4s' },
    { title: 'Review', detail: 'one reviewer' },
    { title: 'Fix', detail: 'one fixer + gates' },
  ],
}

const ROOT = 'C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt'
const PY = `${ROOT}/.venv/Scripts/python.exe`
const PLAN = `${ROOT}/docs/plans/b2`
const BASE = args.base

const ENV = `Repository ${ROOT} (Windows 11, branch overhaul/v2, base ${BASE}). Use ${PY} (pytest-xdist not installed; --basetemp=C:/t/<label>). The full fast suite (${PY} -m pytest -m "not slow and not network" -q --basetemp=...) takes ~14 min: run it in the background and poll; never block one tool call longer than ~9 min. JS: npm test; npx tsc -p tsconfig.json. Lint: .venv/Scripts/ruff.exe check aadhi tests scripts evals docker. Generated-file checks: scripts/gen_env_example.py, -m aadhi.schemas.generate, evals/make_fixtures.py, scripts/gen_chromium_seccomp.py, -m aadhi.compose.dynamic.catalogue, -m aadhi.compose.dynamic.faces, -m aadhi.compose.dynamic.icons, scripts/gen_third_party_notices.py, node scripts/gen_colour_vectors.mjs, scripts/lock_requirements.py (each with --check). Never read .env or secrets; never call paid APIs. No many-minute measurement in CI. Do not commit or change git state.`

const CONTEXT = `DEADLINE MODE: the owner needs a WORKING DEMO of the Dynamic video mode (docs/DYNAMIC_VIDEO_DESIGN.md). The demo phase has landed: direct.py chooses a template per scene among the implemented R1 templates, events.py generates the visual events, ten R1 templates draw, a newly generated lecture is written as a Dynamic video with a rotating look, and an administrator switches the mode in the Studio. BUT 12 of the showcase lecture's 16 scenes still draw board_generic, and final_videos/demo_b2/README.md says exactly why. THIS run closes the biggest part of that gap: the owner re-prioritised it (2026-10-16) to the templates those fallback scenes actually need, in this order - full_bleed_media (3 scenes), animation_stage (1), side-panel support in worked_steps/key_terms (3), check (1), predict. Read final_videos/demo_b2/README.md FIRST: it names the scene each of your templates must win. Quality still matters (it is shown to people), but scope is cut: build what makes the demo work, record the rest as open items.

READ FIRST: ${PLAN}/b2c_design_notes.md — the as-built pattern (the template module shape and its three invariants, what _shared.js gives you, what cues.js owns and you may therefore NOT draw, how a template is registered). Then ${PLAN}/HANDOFF.md, ${PLAN}/b2_plan_v2.md (§1.4 template contract, §1.5 cues, §1.6 TemplateSpec, §1.7 targets, §2 rules), design §4.3 (your template's entry), §4.2, §4.7-§4.9, and the nearest existing template as a model: a text/card template -> definition_card or list_build; a formula/dense one -> formula_focus; a media one -> media_split.

Existing code: web/js/dynamic/ (stage.js TEMPLATES, templates/*.js, templates/_shared.js, cues.js, state.js, core.js), aadhi/compose/dynamic/templates.py (TemplateSpec, fit, implemented, slots, consumes), aadhi/compose/dynamic/direct.py and events.py (the demo phase's: template choice and event generation), aadhi/compose/presentation.py (resolve_looks), aadhi/schemas/timeline.py.

Invariants: classic (Aadhi presenter) output never changes (tests/invariance goldens); every frame a pure function of t (the 1x/3x/reverse gate in real Chromium); the template contract and the source scan (no clock, no emphasis drawing, no will-change/filter, TeX only through ctx.renderTex, catalogue geometry only, no .timeline); never drop content (hand a scene you cannot lay out whole to board_generic and report overflow through texts() so preflight blocks).`

const RESULT = {
  type: 'object',
  properties: {
    summary: { type: 'string' }, files: { type: 'array', items: { type: 'string' } },
    contracts: { type: 'string' }, requests: { type: 'string', description: 'exact changes needed in shared files (for the integrator), as code' },
    tests: { type: 'string' }, open_items: { type: 'string' },
  },
  required: ['summary', 'files', 'contracts', 'requests', 'tests', 'open_items'],
}

const PARALLEL_RULE = `Several agents work in this same repository AT THE SAME TIME. Edit ONLY the files your scope names (new files of your own are fine). Do NOT edit shared files (stage.js, templates.py, direct.py, events.py, catalogue.gen.js, presentation.py, timeline.py, themes.py, schemas, cues.js, state.js, core.js, dynamic.css, sourceScan tests, existing fixtures). _shared.js, key_terms.js and worked_steps.js belong to the 'side_panels' agent ALONE this run: every other agent reads them and never writes them — put every change you need there into 'requests' with exact code, including the TemplateSpec (implemented=True, slots mirroring your JS constants, consumes, holds, dense, push_ok, variants), the stage.js import + TEMPLATES entry, and any direct.py/events.py change your template needs (the forced scene type it answers, the event kinds and target forms it consumes). Run only your own tests and npx tsc; the integrator runs the suites.`

const TEMPLATES = [
  ['full_bleed_media', `design §4.3 full_bleed_media (R1, media class; FORCED for an \`ai_video\` scene). THE HIGHEST-VALUE TEMPLATE OF THIS RUN: the showcase lecture has three ai_video scenes (s1, s6, s14) that fall back to board_generic today purely because this template is not built. A clip or picture filling the frame, with the scene's text over it: the scrim rules of design §7.12 and _shared.js scrimmedBg decide the ink, and \`texts()\` must report the colour the text really sits on (a scrim over a medium), never the page background. A clip is NEVER scaled and NEVER pushed (B2c measured it order-dependent: cues.js showsClip), so its entrance is a fade and your spec is NOT push_ok. Size the media box from the preflight fixes through \`mediaBox\` the way media_split does, not the way board_generic does.`],
  ['animation_stage', `design §4.3 animation_stage (R1, media; FORCED for a \`simulation\` scene; showcase s8). A Manim or generated animation on the stage with its caption/label furniture; it is a MEDIUM like any other (the stage's media path, \`ctx.media\` and your \`mediaBox\`), so draw the frame, the label and the states - never a player of your own, never a clock. Same clip rules as full_bleed_media: never scaled, never pushed, entrance a fade.`],
  ['side_panels', `NOT A NEW TEMPLATE: side-panel support in the templates that already exist, which is why showcase s5 (key_terms), s11 and s12 (worked_steps, a \`graph\` and a \`terminal\` panel) still fall back. YOUR SCOPE IS web/js/dynamic/templates/_shared.js, templates/key_terms.js and templates/worked_steps.js AND NOBODY ELSE'S - you are the ONLY agent allowed to edit _shared.js in this run, and the only one allowed in those two templates. Add the shared side-panel helper to _shared.js (geometry from the catalogue only; board_generic.js and media_split.js are the models for how a panel and its medium are drawn, including \`mediaBox\` for the panel slot and the \`panel:row\` / \`panel:column\` target parts in templates.py _PANEL_PARTS), then let key_terms and worked_steps draw a panel and widen their \`drawsScene\` so they stop refusing such a scene. CRITICAL (B2c review CG-2, and the demo-phase review's D5): a template's PYTHON fit must refuse exactly what its JS \`drawsScene\` refuses, or the director picks it and the page silently draws board_generic - so the matching \`_fit_key_terms\` / \`_fit_worked_steps\` change goes in 'requests' with the exact code, and your jsdom tests pin the two against each other. Panel kinds: templates.py MEDIA_PANELS ('figure', 'image', 'chart', 'graph', 'terminal', 'manim', 'model_3d'); a chart panel is Chart.js (chart_build is NOT in this run - draw the panel's chart the way board_generic already does, do not invent a second chart path).`],
  ['check', `design §4.3 check (R1, prompt; FORCED for quiz scenes; showcase s9). Kinds from QuizScene and board \`question\` items: mcq (at most 4 options, 16:9 only), free_recall, true_false, apply; a reel uses free_recall or true_false only. Think time and QuizScene.countdown_seconds per plan §1.4; the ring runs with no riser. Options appear with a 100 ms stagger; on reveal the correct tile turns \`ok\` with a check pop, the others dim to 40%, then the explanation line. templates.py CHECK_MAX_OPTIONS holds the option cap. Because it is FORCED for quiz scenes, flipping implemented=True changes the template of every quiz scene in every lecture: say so in 'requests' so the integrator expects the timeline diff.`],
  ['predict', `design §4.3 predict (R1, prompt; R-62: the prediction comes BEFORE the explanation). Data: a \`question\` item with blank=True whose answer beat lives in the NEXT explaining scene of the same chapter. Layout: the question card, "Pause and predict", and the think ring (plan §1.4). No answer in this scene. The "Your prediction?" chip that stays in the following explaining scene until the answering beat is part of this scope: put the chip in your OWN module and request the hook the explaining template needs through 'requests' - do not edit another template.`],
]

phase('Build')
const NEW_TEMPLATE = (id, brief) => `YOUR SCOPE: the ${id} template ONLY: web/js/dynamic/templates/${id}.js implementing the full template contract (plan §1.4) like the landed templates, using templates/_shared.js helpers (READ them; do NOT edit them - the side_panels agent owns _shared.js this run); ${brief} Both formats, dark and light families. Its own jsdom tests web/tests/dynamic/templates/${id}.test.js and its own NEW fixture generator under tests/compose/dynamic/ (follow how the landed templates make theirs; never edit another template's generator).`

const tasks = TEMPLATES.map(([id, brief]) => () => agent(
  `${CONTEXT}

${ENV}

${id === 'side_panels' ? brief : NEW_TEMPLATE(id, brief)}

${PARALLEL_RULE}`,
  { label: `build:${id}`, phase: 'Build', schema: RESULT }).then((r) => (r ? { id, ...r } : null)))
const built = (await parallel(tasks)).filter(Boolean)
log(`Built: ${built.map((b) => b.id).join(', ')}`)

phase('Integrate')
const integ = await agent(`${CONTEXT}\n\n${ENV}\n\nReports of the parallel builders:\n${JSON.stringify(built, null, 1)}\n\nYOUR SCOPE (Integrate): (1) apply every builder's 'requests' to the shared files, resolving conflicts between them; register each template (stage.js TEMPLATES; templates.py implemented/slots/consumes/holds/dense/push_ok/variants; regenerate the catalogue; the registry parity tests). (2) Teach direct.py and events.py the new templates: 'check' is FORCED for quiz scenes and 'predict' pairs with the following explaining scene's chip, so template choice changes for existing scenes — keep the choice deterministic and documented, and keep the 40-scene preview budget of tests/compose/test_dynamic_isolation.py (<= 300 ms). Store the code-editor surface token if code_walkthrough asked for it. Bump ENGINE_VERSION (presentation.py, web/js/dynamic/engine.js and every pin of it) if stored looks change. (3) Make every new template pass the gates: the source scan, the jsdom contract tests, the 1x/3x/reverse order-independence gate in real Chromium for a fixture per template in both formats (tests/determinism/test_template_pages.py), and preflight on each fixture with zero blocks. (4) DEMO: re-render the showcase lecture MP4s into ${ROOT}/final_videos/demo_b2/ exactly as the demo phase did (one dark and one light R1 family, 16:9 and 9:16, synthetic audio, never paid APIs) and update that folder's README.md with the template each scene now uses; extend the reference-frame contact sheet to the new templates. Report the paths and which scenes changed template. (5) Run ALL gates and report counts: ruff, every generated-file check, npm test, npx tsc, the full fast suite (background + poll), the slow Chromium tests (tests/determinism/test_template_pages.py, test_template_fixtures.py, test_template_clips.py, test_board_real_page.py, test_stage_layout.py, test_live_css.py, test_stage_fonts.py, tests/compose/dynamic/test_preflight_stage.py, the invariance differential and the new ones). Fix what fails.`,
  { label: 'integrate', phase: 'Integrate', schema: RESULT })

phase('Review')
const review = await agent(`${CONTEXT}\n\n${ENV}\n\nYou are an INDEPENDENT REVIEWER (READ-ONLY: never edit files or change git state; scratch work under C:/t/ only). The work is uncommitted in ${ROOT} vs ${BASE}. Reports:\n${JSON.stringify({ built, integ }, null, 1)}\n\nFind real defects that would hurt the demo or the codebase: a wrong or non-deterministic template choice in real lectures, broken events, visual defects (extract stills from the demo MP4s with ffmpeg at scene midpoints and LOOK at them, and at the reference frames), contrast/overflow/clipping, a frame that depends on render order (Chart.js and the typing code are the risks), a classic-output change, a failing gate (run the fast suite, npm test, tsc, ruff, the slow template tests). Report each with severity (blocker/major/minor), file:line, evidence and a suggested fix. Do not report matters of taste or documented deferrals.`,
  { label: 'review', phase: 'Review', schema: { type: 'object', properties: {
    findings: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
      problem: { type: 'string' }, evidence: { type: 'string' }, suggested_fix: { type: 'string' } },
      required: ['id', 'severity', 'file', 'problem', 'evidence', 'suggested_fix'] } },
    verified_ok: { type: 'string' } }, required: ['findings', 'verified_ok'] } })

phase('Fix')
const fixable = review ? review.findings.filter((f) => f.severity !== 'minor' || review.findings.length <= 12) : []
const fix = fixable.length ? await agent(`${CONTEXT}\n\n${ENV}\n\nFix these review findings at the root (a test that fails without the fix), re-render the demo MP4s if the visuals change, then run ALL gates (as the integrator did) and report counts. Findings:\n${JSON.stringify(fixable, null, 1)}`,
  { label: 'fix', phase: 'Fix', schema: { type: 'object', properties: {
    outcomes: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, outcome: { type: 'string' }, what: { type: 'string' } }, required: ['id', 'outcome', 'what'] } },
    gates: { type: 'string' }, demo: { type: 'string' }, open_items: { type: 'string' } }, required: ['outcomes', 'gates', 'demo', 'open_items'] } }) : null

return { built, integ, review, fix }
