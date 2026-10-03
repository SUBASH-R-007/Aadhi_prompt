// Quality & Consistency Engine check (Phase 18) in real Chrome against a throwaway server with the local stand-ins only
// (AI_FAKE_PROVIDER=1: stand-in media and text models; FAKE_TTS=1: a speech-like tone as long as the text). No real provider.
//
//   1. a clean multi-scene lesson (drawn Aadhi Teacher, Cinematic): Visual Review's "Check quality" shows the lesson report and
//   the engine does not cry wolf (good, or notices only)   2. a crafted problem lesson: the findings grouped by scene with icon
//   and words, the scene chips, "Show scene", the scene's own block in view   3. "Fix automatically" re-plans only the scenes
//   the finding names, the finding is gone, the approvals stay, nothing is generated   4. "Apply suggestion" goes through the composition review and becomes the user's
//   choice   5. a style change makes the report stale; "Check again" gives a fresh one, approvals and media untouched
//   6. quality in the four Phase 17 styles (no style / colour / text warnings)   7. a presenter decision is intentional, a
//   presenter plan out of step is reported   8. the clean lesson's export finds no quality error; preview frame = export frame;
//   a lesson with findings lists them under the prompt's own quality heading (then cancelled)   9. reduced motion: a calm stage and no motion error   10. no errors, all silent.
//
// The vocabulary (severities, dimensions, rules version, the styles) comes from quality.py / styles.py, never copied here.
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/quality_browser_check.mjs
// Silent: --mute-audio and --disable-audio-output, browser speech stubbed; the export's tab capture keeps the tab unmuted
// (a muted tab records silence) but asks for suppressLocalAudioPlayback, with no audio output device.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.QUALITY_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-quality-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.QUALITY_CHECK_PORT || 9950 + (process.pid % 8));
const BASE = `http://127.0.0.1:${PORT}`;
const STARTED = Date.now();
const BUDGET_MS = Number(process.env.QUALITY_CHECK_BUDGET_MS || 1600000); // stop starting new work before an outer timeout
if (!PASSWORD) {
    console.error('Set AADHI_PASSWORD to the default admin password (a fresh database creates that admin).');
    process.exit(2);
}
const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: !!ok, detail });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`);
}
function note(text) { console.log('      ' + text); }
async function section(name, fn) {
    try { await fn(); } catch (e) { check(name, false, 'error: ' + (e.stack || e.message).split('\n').slice(0, 3).join(' | ')); }
}
async function loadPlaywright() {
    try { return await import('playwright-core'); } catch (e) {
        if (process.env.PLAYWRIGHT_CORE_DIR) return import(pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE_DIR, 'index.mjs')).href);
        throw new Error('playwright-core not found');
    }
}
fs.rmSync(OUT, { recursive: true, force: true });
const data = path.join(OUT, 'server-data');
for (const dir of ['assets', 'exports', 'static', 'jobs']) fs.mkdirSync(path.join(data, dir), { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const PYTHON = path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python');
let server = null;
function startServer() {
    const log = fs.openSync(path.join(OUT, 'server.log'), 'w');
    server = spawn(PYTHON, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
        cwd: REPO, stdio: ['ignore', log, log],
        env: { ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `aqc-${process.pid}`), JWT_SECRET: 'quality-check-' + Math.random().toString(36).slice(2),
            AI_FAKE_PROVIDER: '1', FAKE_TTS: '1', AI_GENERATION_ENABLED: '1', AI_FAKE_STATE_DIR: path.join(data, 'jobs'),
            GEMINI_API_KEY: '', OPENAI_API_KEY: '' }
    });
}
function killServer() {
    if (!server || server.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
    else server.kill('SIGKILL');
}
process.on('exit', killServer);
async function waitForServer() {
    for (let i = 0; i < 120; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + OUT);
        try { if ((await fetch(BASE + '/cinematic.js')).ok) return; } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('the test server did not start');
}
let token = null;
async function api(method, route, body, raw = false) {
    const res = await fetch(BASE + route, { method, headers: { Authorization: 'Bearer ' + token, ...(body && !raw ? { 'Content-Type': 'application/json' } : {}) },
        body: raw ? body : body ? JSON.stringify(body) : undefined });
    return { status: res.status, data: await res.json().catch(() => null) };
}
async function upload(file, description, keywords) {
    const form = new FormData();
    form.append('file', new Blob([fs.readFileSync(file)], { type: 'image/png' }), path.basename(file));
    const asset = (await api('POST', '/api/assets', form, true)).data.asset;
    await api('PATCH', `/api/assets/${asset.id}`, { description, keywords });
    return asset.id;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const firstLine = e => String((e && e.message) || e).split(/\r?\n/)[0];
function silentPage() {
    const devices = navigator.mediaDevices;
    if (devices && devices.getDisplayMedia) {
        const capture = devices.getDisplayMedia.bind(devices);
        devices.getDisplayMedia = (c = {}) => capture({ ...c, audio: c.audio ? { ...(typeof c.audio === 'object' ? c.audio : {}), suppressLocalAudioPlayback: true } : false });
    }
    if (window.speechSynthesis) {
        window.speechSynthesis.speak = u => setTimeout(() => { if (u.onstart) u.onstart(new Event('start')); setTimeout(() => u.onend && u.onend(new Event('end')), 900); }, 0);
        window.speechSynthesis.cancel = () => {};
    }
}
// renderSlide() assigns window.checkAndAdvanceSlide for every scene: while window.__holdScene is set the page sees a no-op, so
// a scene being measured never advances to the next one (the export, which never sets it, advances as always)
function holdProbe() {
    let real = () => {};
    Object.defineProperty(window, 'checkAndAdvanceSlide', { configurable: true,
        get() { return window.__holdScene ? () => {} : real; }, set(v) { real = v; } });
}
function ffmpeg(...args) {
    const r = spawnSync('ffmpeg', ['-v', 'error', '-y', ...args], { encoding: 'buffer' });
    if (r.status !== 0) throw new Error(String(r.stderr));
    return r.stdout;
}
const grid = (file, w = 32, h = 18) => [...ffmpeg('-i', file, '-vf', `scale=${w}:${h}:flags=area`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')];
const difference = (a, b) => a.reduce((s, v, i) => s + Math.abs(v - (b[i] || 0)), 0) / a.length;

// ---- what quality.py and styles.py say (the source of truth), read with the repo's Python ------------------------------------
const PY = `
import json, quality, styles
print(json.dumps({"severities": list(quality.SEVERITIES), "dimensions": list(quality.DIMENSIONS), "rules": quality.RULES,
                  "families_loaded": [n for n, _m in quality._families()], "family_modules": list(quality.FAMILY_MODULES),
                  "styles": list(styles.FAMILIES), "labels": {f: styles.resolve({"style": f})["label"] for f in styles.FAMILIES}}))
`;
function expectedFromPython() {
    const r = spawnSync(PYTHON, ['-c', PY], { cwd: REPO, encoding: 'utf8' });
    if (r.status !== 0) throw new Error('quality.py could not be read: ' + r.stderr);
    return JSON.parse(r.stdout);
}

// ---- the lessons --------------------------------------------------------------------------------------------------------------
// The clean lesson: introduction, definition, explanation, process, a diagram (a library asset), a formula with its symbols
// spelled out, code with its output, a comparison, a presenter-led explanation, a quiz and the key points
const CLEAN = { subject_name: 'Quality check', session_title: 'How plants and programs work',
    cinematic_style: { style: 'academic', style_version: 1, style_overrides: {} },
    concept_map: [{ id: 'c1', title: 'Photosynthesis', depends_on: [] }, { id: 'c2', title: 'The leaf', depends_on: ['c1'] },
        { id: 'c3', title: "Newton's second law", depends_on: [] }, { id: 'c4', title: 'Loops', depends_on: [] }],
    scenes: [
    { type: 'content', concept_id: 'c1', title: 'Photosynthesis', subtitle: 'How plants make their own food', html: '<p>Plants turn sunlight, water and air into sugar.</p>',
      narration: 'Welcome! Today we find out how plants make food.' },
    { type: 'content', concept_id: 'c1', title: 'What is photosynthesis?',
      html: "<div class='definition'><span class='keyword'>Photosynthesis</span> is the process by which green plants use sunlight to make glucose from carbon dioxide and water.</div>",
      narration: 'Here is the definition. [SYNC] Plants use sunlight to make glucose.' },
    { type: 'content', concept_id: 'c1', title: 'Why leaves are green',
      html: "<p>Leaves hold <span class='keyword'>chlorophyll</span>, a pigment that absorbs red and blue light and reflects green light.</p>",
      narration: 'Why are leaves green? [SYNC] Chlorophyll absorbs red and blue light, and reflects the green.' },
    { type: 'content', concept_id: 'c1', title: 'How photosynthesis happens',
      html: '<ol><li>Chlorophyll absorbs sunlight</li><li>Water is split into hydrogen and oxygen</li><li>Carbon dioxide is taken in</li><li>Glucose is made</li></ol>',
      narration: 'Four steps. [SYNC] Light is absorbed. [SYNC] Water is split. [SYNC] Carbon dioxide enters. [SYNC] Glucose is made.' },
    { type: 'content', concept_id: 'c2', title: 'Inside the leaf', html: "<ul><li><span class='keyword'>Palisade cells</span> catch the light</li><li>The <span class='keyword'>stoma</span> lets air in</li></ul>",
      visual: { concept: 'leaf cross section', description: 'Leaf cross section with palisade cells and stoma', type: 'diagram', keywords: ['leaf', 'cross', 'section', 'palisade'] },
      narration: 'Look at this diagram of a leaf. [SYNC] Palisade cells catch light. [SYNC] The stoma lets air in.' },
    { type: 'content', concept_id: 'c3', title: "Newton's second law", html: "<div class='formula-block'>\\[F = ma\\]</div><ul><li>F = force</li><li>m = mass</li><li>a = acceleration</li></ul>",
      narration: 'Here is the law. [SYNC] F equals m times a. [SYNC] F is the force, m the mass and a the acceleration.' },
    { type: 'content', concept_id: 'c4', title: 'Python for loop', html: "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre><p>It prints the numbers 0 to 4, one per line.</p>",
      narration: 'Here is a loop. It prints the numbers zero to four.' },
    { type: 'content', title: 'Plants vs Animals', html: '<table><thead><tr><th>Plants</th><th>Animals</th></tr></thead><tbody><tr><td>Make their own food</td><td>Eat other living things</td></tr><tr><td>Release oxygen</td><td>Release carbon dioxide</td></tr></tbody></table>',
      narration: 'Let us compare plants and animals side by side. Plants make their own food, while animals eat other living things. Plants release oxygen, and animals release carbon dioxide.' },
    { type: 'content', concept_id: 'c1', title: 'Why plants matter to us', html: '<p>Almost every breath you take holds oxygen that a plant released.</p>',
      narration: 'Think about this. Almost every breath you take holds oxygen that a plant released.' },
    { type: 'quiz_checkpoint', title: 'Quick check', question: 'What does a plant make in photosynthesis?', options: ['Glucose', 'Salt', 'Iron'], correct_index: 0,
      explanation: 'Plants make glucose from light, water and carbon dioxide.', countdown_seconds: 2, narration: 'Quick question for you.', reveal_narration: 'Glucose!' },
    { type: 'key-takeaway', title: 'Key takeaways', html: "<ul class='takeaway-list'><li>Plants make glucose from sunlight</li><li>Chlorophyll captures the light</li><li>Loops repeat code</li><li>F = ma links force and motion</li></ul>",
      narration: 'Let us recap. [SYNC] Plants make glucose. [SYNC] Chlorophyll captures light. [SYNC] Loops repeat code. [SYNC] Force is mass times acceleration.' }
] };
const KINDS = ['intro', 'definition', 'explanation', 'process', 'diagram', 'formula', 'code', 'comparison', 'presenter-led', 'quiz', 'summary'];
const AT = Object.fromEntries(KINDS.map((k, i) => [k, i]));

// The problem lesson: an abbreviation never spelled out (ATP), a term capitalised two ways in its keywords ("palisade cells" /
// "Palisade Cells"), Java code (the player colours only Python and JavaScript), a scene overloaded (the board, a library
// picture, the presenter and six labels); in the page: the style switched without re-planning and one scene's plan_hash
// tampered (a stale plan)
const PROBLEM = { subject_name: 'Quality problems', session_title: 'Energy in leaves',
    cinematic_style: { style: 'academic', style_version: 1, style_overrides: {} },
    concept_map: [{ id: 'c1', title: 'Energy in cells', depends_on: [] }, { id: 'c2', title: 'The leaf', depends_on: [] }, { id: 'c3', title: 'Loops', depends_on: [] }],
    scenes: [
    { type: 'content', concept_id: 'c1', title: 'Energy in cells', subtitle: 'How cells keep their energy', html: '<p>Cells store the energy they need in ATP.</p>',
      narration: 'Welcome! Cells keep their energy in ATP, and today we see how.' },
    { type: 'content', concept_id: 'c2', title: 'Palisade cells', html: "<div class='definition'><span class='keyword'>palisade cells</span> are the leaf cells that catch most of the light.</div>",
      narration: 'Here is the definition. [SYNC] Palisade cells catch most of the light.' },
    { type: 'content', concept_id: 'c2', title: 'Inside the leaf',
      html: "<ul><li><span class='keyword'>Palisade Cells</span> catch the light</li><li>The <span class='keyword'>stoma</span> lets air in</li><li>ATP carries the energy</li></ul>",
      visual: { concept: 'leaf cross section', description: 'Leaf cross section with palisade cells and stoma', type: 'diagram', keywords: ['leaf', 'cross', 'section', 'palisade'] },
      composition: { labels: [{ text: 'Palisade layer' }, { text: 'Spongy layer' }, { text: 'Stoma' }, { text: 'Guard cell' }, { text: 'Xylem' }, { text: 'Phloem' }] },
      narration: 'Look at this diagram of a leaf. [SYNC] Palisade cells catch light. [SYNC] The stoma lets air in. [SYNC] The spongy layer lets gases move, and the xylem and phloem carry water and sugar.' },
    { type: 'content', concept_id: 'c3', title: 'A Java loop', html: "<pre><code class='language-java'>for (int i = 0; i &lt; 3; i++) {\n    System.out.println(i);\n}</code></pre><p>It prints 0, 1 and 2.</p>",
      narration: 'Here is a loop in Java. It prints zero, one and two.' },
    { type: 'content', concept_id: 'c1', title: 'Why it matters', html: '<p>Every living cell needs a steady supply of energy.</p>',
      narration: 'Think about this. Every living cell needs a steady supply of energy.' },
    { type: 'key-takeaway', title: 'Key takeaways', html: "<ul class='takeaway-list'><li>Cells store energy in ATP</li><li>Palisade cells catch the light</li><li>Loops repeat code</li></ul>",
      narration: 'Let us recap. [SYNC] Cells store energy in ATP. [SYNC] Palisade cells catch the light. [SYNC] Loops repeat code.' }
] };
const P = { intro: 0, definition: 1, overloaded: 2, code: 3, stale: 4, summary: 5 };
// what the problem lesson must show, each by the rules that can report it
const PROBLEM_EXPECT = [
    { key: 'abbr', what: 'abbreviation never spelled out', rules: ['education.abbreviation_undefined'] },
    { key: 'casing', what: 'inconsistent keyword casing', rules: ['education.term_casing'] },
    { key: 'code', what: 'code in a language the player does not colour', rules: ['education.code_unhighlighted'], scene: P.code },
    { key: 'style', what: 'style switched without re-planning', rules: ['style.stale_look'] },
    { key: 'stale', what: 'a stale stored plan (tampered camera / plan_hash)', rules: ['core.stale_plan'], scene: P.stale },
    { key: 'busy', what: 'scene overloaded', rules: ['timing.density_overloaded', 'style.text_overflow', 'style.dense_board', 'style.tiny_text', 'education.asset_labels'], scene: P.overloaded }
];

// Media / AI generation endpoints (the quality check must never call one): planning, reviews, quality, keeping the style and
// reading files are not generation
const GENERATION = /\/generate-ai|\/api\/ai-media\/lessons\/[^/]+\/generate|\/api\/cinematic\/background|\/regenerate-manim|\/get-image|\/get-gif|\/api\/presenters\/(?:[^?]*\/)?(?:render|generate)(?:[/?]|$)|\/api\/visuals\/[^?]*generate/;
const isGeneration = (method, url) => GENERATION.test(url) || (method !== 'GET' && /\/api\/ai[-/]/.test(url));

// ---- page-side probes (serialized into the page) ---------------------------------------------------------------------------
// The quality panel as shown: headline, scenes checked, stale note, state, areas, findings grouped by scene, list chips
function qualityPanel() {
    const box = document.querySelector('.review-overlay.open .quality-panel');
    if (!box) return null;
    const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
    const head = box.querySelector('.quality-headline');
    const state = box.querySelector('.quality-state');
    return {
        hidden: box.hasAttribute('hidden'), status: box.getAttribute('data-status'), busy: box.getAttribute('aria-busy'),
        headline: t(head), headStatus: head ? head.getAttribute('data-status') : null, scenes: t(box.querySelector('.quality-scenes')),
        run: t(box.querySelector('.quality-run')), stale: !!box.querySelector('.quality-stale'), staleText: t(box.querySelector('.quality-stale')),
        state: t(state), stateKind: state ? state.getAttribute('data-kind') : null,
        areas: [...box.querySelectorAll('.quality-dimension')].map(li => ({ key: li.getAttribute('data-dimension'), status: li.getAttribute('data-status'), hidden: li.hasAttribute('hidden'), text: t(li) })),
        fine: t(box.querySelector('.quality-dimensions-fine')),
        groups: [...box.querySelectorAll('.quality-group')].map(g => ({ scene: g.getAttribute('data-scene'), title: t(g.querySelector('.quality-group-title')),
            issues: [...g.querySelectorAll('.quality-issue')].map(li => {
                const fix = li.querySelector('.quality-fix');
                return { id: li.getAttribute('data-issue'), severity: li.getAttribute('data-severity'), sev: t(li.querySelector('.quality-severity')),
                    mark: t(li.querySelector('.quality-severity .quality-mark')), message: t(li.querySelector('.quality-message')), show: !!li.querySelector('.quality-show'),
                    fix: fix ? { action: fix.getAttribute('data-action'), text: t(fix), disabled: fix.disabled, html: fix.outerHTML.slice(0, 300) } : null };
            }) })),
        limitations: [...box.querySelectorAll('.quality-limitation')].map(t),
        chips: [...document.querySelectorAll('.review-overlay.open .review-item .quality-chip')].map(c => ({ scene: c.closest('.review-item').getAttribute('data-scene'),
            slot: c.closest('.review-item').getAttribute('data-slot'), severity: c.getAttribute('data-severity'), text: t(c) })),
        filter: !!document.querySelector('.review-overlay.open .review-filter[data-filter="quality"]:not([hidden])'),
        sessionStale: typeof reviewSession !== 'undefined' ? reviewSession.qualityIsStale() : null,
        engineering: /\b(?:plan_hash|fingerprint|token|evidence|provider|gemini|openai)\b|--st-/i.test(t(box) || '')
    };
}
// The review list's visible statuses (composition and presenter items) and every scene's stored review record
function reviewState() {
    return {
        chips: [...document.querySelectorAll('.review-overlay.open .review-item')].map(b => `${b.getAttribute('data-scene')}:${b.getAttribute('data-slot')}:${(b.querySelector('.review-chip') || {}).getAttribute ? b.querySelector('.review-chip').getAttribute('data-status') : '-'}`),
        reviews: slides.map(s => JSON.stringify(s.visual_review || null)),
        statuses: slides.map(s => ((s.visual_review || {}).composition || {}).status || null),
        presenter: slides.map(s => ((s.visual_review || {}).presenter || {}).status || null)
    };
}
// What the stage did in the scene on screen (camera moves, transforms, running camera animations)
function stageMotion() {
    const cams = [...document.querySelectorAll('.cine-camera')];
    const anims = document.getAnimations ? document.getAnimations().filter(a => {
        const target = a.effect && a.effect.target;
        return target && target.classList && target.classList.contains('cine-camera') && a.playState === 'running';
    }) : [];
    const plan = slides[currentSlide] && slides[currentSlide].cinematic_plan;
    return { scene: currentSlide, reduced: !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches),
        calm: cinematicStage.calm ? cinematicStage.calm() : null, cameraMoves: cinematicStage.stats.cameraMoves,
        planned: plan && plan.camera ? plan.camera.movement : null, transforms: cams.map(e => e.style.transform).filter(v => v && v !== 'none'),
        running: anims.length, transition: document.body.getAttribute('data-cine-transition') };
}

const problems = [];
const silent = { pages: 0, stubbed: new Set(), flags: [] };
const requests = [];         // every request the pages made: { phase, method, url }
let phase = 'setup';
let browser = null;
let exportBrowser = null;
const reports = {};          // every report the checks read, by name (written to reports.json)
try {
    const EXPECT = expectedFromPython();
    const FAMILIES = EXPECT.styles;
    fs.writeFileSync(path.join(OUT, 'expected.json'), JSON.stringify(EXPECT, null, 2));
    note(`quality.py ${EXPECT.rules}; families loaded ${EXPECT.families_loaded.join(', ')} (of ${EXPECT.family_modules.join(', ')}); styles ${FAMILIES.join(', ')}`);
    spawnSync(PYTHON, [path.join(REPO, 'tests', 'fixtures', 'cinematic_diagrams.py'), OUT]);
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const leafAsset = await upload(path.join(OUT, 'leaf_cross_section.png'), 'Leaf cross section showing palisade cells, spongy layer and stoma', ['leaf', 'cross', 'section', 'palisade', 'chloroplast']);
    await upload(path.join(OUT, 'photosynthesis_diagram.png'), 'Photosynthesis plant diagram with sunlight, water, carbon dioxide and oxygen', ['photosynthesis', 'plant', 'diagram', 'sunlight']);
    const cleanPid = (await api('POST', '/save-history', CLEAN)).data.id;
    const problemPid = (await api('POST', '/save-history', PROBLEM)).data.id;
    const { chromium } = await loadPlaywright();
    const MAIN_ARGS = ['--mute-audio', '--disable-audio-output', '--autoplay-policy=no-user-gesture-required'];
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: MAIN_ARGS });
    silent.flags.push('main: ' + MAIN_ARGS.join(' '));

    const baseCine = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'auto', typography: 'academic', composer: 'rules', director: 'rules',
        style: 'academic', style_version: 1, style_overrides: {} };
    const TEACHER = { presenter_id: 'aadhi-teacher', mode: 'auto', position: 'right', style: 'friendly', fallback: 'none' };
    async function newPage(b, viewport, cine, { reducedMotion = null } = {}) {
        const context = await b.newContext({ viewport });
        await context.addInitScript(([t, c, pr]) => {
            if (!sessionStorage.getItem('seeded')) {
                localStorage.setItem('jwt_token', t);
                localStorage.setItem('aadhi.presenter', JSON.stringify(pr));
                localStorage.setItem('aadhi.cinematic', JSON.stringify(c));
                sessionStorage.setItem('seeded', '1');
            }
        }, [token, cine, TEACHER]);
        await context.addInitScript(silentPage);
        await context.addInitScript(holdProbe);
        const page = await context.newPage();
        if (reducedMotion) await page.emulateMedia({ reducedMotion });
        page.on('pageerror', e => problems.push(`page error: ${e.message}`));
        // expected: the export's own log line when check 8c cancels its prompt on purpose
        const expected = text => /Failed to load resource|Logo video play error|WebSocket connection/.test(text)
            || (phase === 'export-prompt' && /The export was cancelled before recording/.test(text));
        page.on('console', m => { if (m.type() === 'error' && !expected(m.text())) problems.push(`console: ${m.text()}`); });
        page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
        page.on('requestfailed', r => { const err = (r.failure() || {}).errorText || ''; if (!/ERR_ABORTED/.test(err)) problems.push(`request failed (${err}): ${r.url()}`); });
        page.on('request', r => requests.push({ phase, method: r.method(), url: r.url() }));
        silent.pages += 1;
        return { context, page };
    }
    async function confirmSilent(page) {
        if (await page.evaluate(() => !window.speechSynthesis || /onstart/.test(String(window.speechSynthesis.speak)))) silent.stubbed.add(page);
    }
    async function openLesson(page, projectId) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await confirmSilent(page);
    }
    async function startLesson(page, projectId) {
        await openLesson(page, projectId);
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => slides.every(s => s.cinematic_plan || s.type === 'quiz_checkpoint'), null, { timeout: 30000 });
        await sleep(1500);
    }
    async function stopNarration(page) {
        await page.evaluate(() => { ttsState.isPlaying = false; try { speakNarration(null); } catch (e) { /* nothing playing */ } });
    }
    async function playScene(page, index) {
        await page.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; currentSlide = n; renderSlide(n); }, index);
        await sleep(1600);
        const mid = await page.evaluate(stageMotion);
        await page.waitForFunction(() => window.slideSyncState && window.slideSyncState.audioFinished, null, { timeout: 40000 }).catch(() => {});
        await sleep(700);
        return { mid, end: await page.evaluate(stageMotion) };
    }
    // Visual Review opened as the user does (the start screen's button: the page plans the lesson, never generates)
    async function openReview(page) {
        await page.click('#start-review-btn');
        await page.waitForSelector('.review-overlay.open .review-item[data-slot="composition"]', { timeout: 60000 });
        await page.waitForSelector('.review-overlay.open .quality-panel:not([hidden])', { timeout: 15000 });
        await sleep(500);
    }
    async function closeReview(page) {
        await page.click('.review-overlay.open .review-panel .export-close');
        await page.waitForFunction(() => !document.querySelector('.review-overlay.open'), null, { timeout: 10000 });
    }
    // The panel's "Check quality" / "Check again": the report the page received and the panel as drawn
    async function runQuality(page, name) {
        const answer = page.waitForResponse(r => r.url().includes('/api/quality/lesson') && r.request().method() === 'POST', { timeout: 60000 });
        await page.click('.review-overlay.open .quality-run');
        const response = await answer;
        const report = await response.json().catch(() => null);
        await page.waitForFunction(() => !reviewSession.qualityBusy && !document.querySelector('.review-overlay.open .quality-panel[aria-busy]'), null, { timeout: 30000 });
        await sleep(250);
        const panel = await page.evaluate(qualityPanel);
        reports[name] = { http: response.status(), report, panel };
        return { http: response.status(), report, panel };
    }
    // A style picked in the settings panel (its button on the start screen; dispatched to the button itself, as the review may
    // cover it). Off the lesson (start screen) the page keeps the style with the lesson but does not re-plan.
    async function pickStyle(page, family) {
        if (await page.evaluate(f => cinematicSettings.settings.style === f, family)) return 200; // already the lesson's style (kept)
        const kept = page.waitForResponse(r => r.url().includes('/api/cinematic/style') && r.request().method() === 'POST'
            && (r.request().postData() || '').includes(`"${family}"`), { timeout: 30000 }).catch(() => null);
        await page.locator(`#cinematic-settings .style-option[data-style="${family}"]`).dispatchEvent('click');
        await page.waitForFunction(f => cinematicSettings.settings.style === f, family, { timeout: 15000 });
        const response = await kept;
        await sleep(400);
        return response ? response.status() : null;
    }
    // A composition approved through the review ("Keep")
    async function approveComposition(page, scene) {
        await page.click(`.review-overlay.open .review-item[data-slot="composition"][data-scene="${scene}"]`);
        await page.click('.review-overlay.open .review-actions [data-action="keep"]');
        await page.waitForFunction(n => ((slides[n].visual_review || {}).composition || {}).status === 'approved', scene, { timeout: 20000 });
        await sleep(300);
    }
    const shot = (page, name) => page.screenshot({ path: path.join(OUT, name) });
    async function panelShot(page, name) {
        const box = page.locator('.review-overlay.open .quality-panel');
        await box.evaluate(el => { el.dataset.keepMax = el.style.maxHeight; el.style.maxHeight = 'none'; }).catch(() => {});
        await box.screenshot({ path: path.join(OUT, name) }).catch(() => shot(page, name));
        await box.evaluate(el => { el.style.maxHeight = el.dataset.keepMax || ''; delete el.dataset.keepMax; }).catch(() => {});
    }
    const issuesOf = report => (report && Array.isArray(report.issues) ? report.issues : []);
    const describe = f => `${f.severity} ${f.rule}${f.evidence && Array.isArray(f.evidence.also) ? ` (+ ${f.evidence.also.join(', ')})` : ''} scene ${f.scene === null ? '-' : f.scene + 1}${f.repair && f.repair.kind !== 'none' ? ` [${f.repair.kind}: ${f.repair.label}]` : ''}: ${f.message}`;
    const counts = report => (report && report.summary ? report.summary.counts : {});
    const countLine = report => { const c = counts(report); return EXPECT.severities.map(s => `${s} ${c[s] || 0}`).join(', '); };
    const serious = (report, from = 'warning') => issuesOf(report).filter(f => EXPECT.severities.indexOf(f.severity) >= EXPECT.severities.indexOf(from));
    const generationIn = list => list.filter(r => isGeneration(r.method, r.url));

    const { page } = await newPage(browser, { width: 1280, height: 720 }, baseCine);
    page.on('dialog', d => d.accept().catch(() => {}));
    let cleanFirst = null;

    // ---- 1. the clean lesson ---------------------------------------------------------------------------------------------------
    await section('1. the clean lesson: the lesson report shows and the engine does not cry wolf', async () => {
        phase = 'clean';
        await openLesson(page, cleanPid);
        await openReview(page);
        const before = await page.evaluate(qualityPanel);
        // two compositions approved first (checks 5 and 6 read them back)
        await approveComposition(page, AT.definition);
        await approveComposition(page, AT.process);
        const r = await runQuality(page, 'clean');
        cleanFirst = r;
        await panelShot(page, 'panel-clean.png');
        await shot(page, 'review-clean.png');
        const plans = await page.evaluate(() => slides.map(s => { const p = s.cinematic_plan || {}; return { template: p.template, presenter: p.presenter ? `${p.presenter.presenter_id}${p.presenter.shown ? '' : ' (hidden)'}` : null, style: ((p.style || {}).look || {}).family || null }; }));
        const c = counts(r.report);
        const bad = serious(r.report);
        const notices = issuesOf(r.report).filter(f => f.severity === 'notice');
        const infos = issuesOf(r.report).filter(f => f.severity === 'info');
        note(`clean lesson plans: ${plans.map((p, i) => `${i + 1}:${p.template}/${p.presenter}/${p.style}`).join(' ')}`);
        note(`clean report: status ${r.report && r.report.status}, ${countLine(r.report)}; limitations: ${(r.report && r.report.limitations || []).join(' | ') || 'none'}`);
        issuesOf(r.report).forEach(f => note('  finding: ' + describe(f)));
        const shownOk = r.http === 200 && !r.panel.hidden && r.panel.headline && r.panel.headStatus === r.report.status && r.panel.scenes === `${CLEAN.scenes.length} scenes checked`;
        check('1. the clean lesson planned in Cinematic (drawn Aadhi Teacher): "Check quality" shows the lesson report (headline, "11 scenes checked")',
            shownOk && before && before.run === 'Check quality' && before.headline === 'Quality not checked yet' && r.panel.run === 'Check again' && plans.some(p => /aadhi-teacher/.test(p.presenter || '')),
            `before: "${before && before.headline}" button "${before && before.run}"; after: HTTP ${r.http}, headline "${r.panel.headline}" [${r.panel.headStatus}], "${r.panel.scenes}", button "${r.panel.run}"; ${r.panel.fine || ''}`);
        check('1b. the clean lesson is good or has notices only (no warning, error or blocking finding: no false alarm)',
            r.report && ['good', 'review'].includes(r.report.status) && bad.length === 0 && (c.warning || 0) + (c.error || 0) + (c.blocking || 0) === 0,
            `status ${r.report && r.report.status}; ${countLine(r.report)}${bad.length ? '; FALSE ALARMS: ' + bad.map(describe).join(' || ') : ''}${notices.length ? '; notices: ' + notices.map(describe).join(' || ') : ''}${infos.length ? `; ${infos.length} info` : ''}`);
    });

    // ---- 5. stale state after a style change -----------------------------------------------------------------------------------
    await section('5. a style change makes the report stale; "Check again" gives a fresh one', async () => {
        phase = 'stale';
        if (!cleanFirst) throw new Error('no report from check 1');
        const fresh = await page.evaluate(qualityPanel);
        const before = await page.evaluate(reviewState);
        const startAt = requests.length;
        await closeReview(page);
        const kept = await pickStyle(page, 'corporate_training');
        await openReview(page); // the page plans the lesson in the new style (as on every opening)
        const staleNow = await page.evaluate(qualityPanel);
        await panelShot(page, 'panel-stale.png');
        const disabledFixes = staleNow.groups.flatMap(g => g.issues).filter(f => f.fix);
        reports['stale-panel'] = { panel: staleNow };
        const r = await runQuality(page, 'after-style-change');
        const after = await page.evaluate(reviewState);
        const generated = generationIn(requests.slice(startAt));
        const statusesSame = JSON.stringify(before.statuses) === JSON.stringify(after.statuses);
        const chipsSame = JSON.stringify(before.chips.filter(x => /composition/.test(x))) === JSON.stringify(after.chips.filter(x => /composition/.test(x)));
        note(`after the style change: ${countLine(r.report)}; ${serious(r.report, 'notice').map(describe).join(' || ') || 'nothing to look at'}`);
        check('5. switching the style in the settings panel: the panel says "The lesson changed since this check"; "Check again" gives a fresh report (new fingerprint, no stale note)',
            !fresh.stale && staleNow.stale && /The lesson changed since this check/.test(staleNow.staleText) && disabledFixes.every(f => f.fix.disabled)
            && r.http === 200 && !r.panel.stale && r.report.fingerprint !== cleanFirst.report.fingerprint && kept === 200,
            `before: stale ${fresh.stale}; style kept HTTP ${kept}; reopened: stale note "${staleNow.staleText}", ${disabledFixes.length} fix buttons ${disabledFixes.every(f => f.fix.disabled) ? 'disabled' : 'ENABLED: ' + disabledFixes.filter(f => !f.fix.disabled).map(f => f.fix.html).join(' ')} (session stale ${staleNow.sessionStale}); `
            + `Check again: HTTP ${r.http}, stale ${r.panel.stale}, fingerprint ${cleanFirst.report.fingerprint} -> ${r.report.fingerprint}, headline "${r.panel.headline}"`);
        check('5b. the style change and the new check regenerated nothing and kept the approvals (composition statuses unchanged)',
            generated.length === 0 && statusesSame && chipsSame && before.statuses[AT.definition] === 'approved' && before.statuses[AT.process] === 'approved',
            `${generated.length} generation requests${generated.length ? ': ' + generated.slice(0, 3).map(x => x.url).join(', ') : ''}; statuses ${before.statuses.map(s => s || '-').join(',')} -> ${after.statuses.map(s => s || '-').join(',')}; `
            + `list composition chips ${chipsSame ? 'unchanged' : 'CHANGED ' + before.chips.filter(x => /composition/.test(x)).join(' ') + ' -> ' + after.chips.filter(x => /composition/.test(x)).join(' ')}`);
    });

    // ---- 6. quality in the four Phase 17 styles --------------------------------------------------------------------------------
    await section('6. quality in all four styles', async () => {
        phase = 'styles';
        const per = {};
        for (const family of FAMILIES) {
            await closeReview(page);
            const kept = await pickStyle(page, family);
            await openReview(page);
            const planned = await page.evaluate(f => slides.filter(s => s.cinematic_plan).every(s => ((s.cinematic_plan.style || {}).look || {}).family === f), family);
            const r = await runQuality(page, `style-${family}`);
            await panelShot(page, `panel-${family}.png`);
            const look = issuesOf(r.report).filter(f => ['style', 'colour', 'typography'].includes(f.dimension));
            const bad = look.filter(f => ['warning', 'error', 'blocking'].includes(f.severity));
            per[family] = { kept, planned, status: r.report && r.report.status, counts: counts(r.report), look: look.length, bad, all: serious(r.report, 'notice'), stale: r.panel.stale };
            note(`${family}: status ${per[family].status}, ${countLine(r.report)}; style/colour/text findings ${look.length} (${bad.length} warning or worse)`);
            serious(r.report, 'notice').forEach(f => note('  finding: ' + describe(f)));
        }
        fs.writeFileSync(path.join(OUT, 'styles.json'), JSON.stringify(per, null, 2));
        check('6. re-planned in each of the four styles, the report has no style / colour / text warning or error in any style',
            FAMILIES.every(f => per[f].planned && per[f].kept === 200 && per[f].bad.length === 0),
            FAMILIES.map(f => `${f} (${EXPECT.labels[f]}): planned ${per[f].planned}, ${per[f].status}, style/colour/text ${per[f].look} (${per[f].bad.length} bad${per[f].bad.length ? ': ' + per[f].bad.map(describe).join(' || ') : ''}), `
                + `all: ${EXPECT.severities.map(s => `${s[0]}${per[f].counts[s] || 0}`).join('/')}`).join(' | '));
        check('6b. no warning or error of any kind on the clean lesson in any style (screenshots panel-<style>.png)',
            FAMILIES.every(f => per[f].all.every(x => !['warning', 'error', 'blocking'].includes(x.severity))),
            FAMILIES.map(f => `${f}: ${per[f].all.filter(x => !['notice'].includes(x.severity)).map(describe).join(' || ') || 'none'}`).join(' | '));
    });

    // ---- 7. presenter consistency ----------------------------------------------------------------------------------------------
    await section('7. presenter consistency: a Visual Review decision is intentional, a plan out of step is reported', async () => {
        phase = 'presenter';
        const shown = await page.evaluate(() => slides.map((s, i) => ({ i, shown: !!(s.cinematic_plan && s.cinematic_plan.presenter && s.cinematic_plan.presenter.shown),
            side: s.cinematic_plan && s.cinematic_plan.presenter ? s.cinematic_plan.presenter.side : null, item: !!document.querySelector(`.review-overlay.open .review-item[data-slot="presenter"][data-scene="${i}"]`) })));
        const candidates = shown.filter(s => s.shown && s.item && s.i !== AT.intro && s.i !== AT.quiz);
        if (candidates.length < 2) throw new Error('fewer than two scenes show the presenter: ' + JSON.stringify(shown));
        const moved = (candidates.find(s => s.i === AT.explanation) || candidates[0]).i;
        const crafted = candidates.find(s => s.i !== moved && s.i !== moved + 1 && s.i !== moved - 1) || candidates.find(s => s.i !== moved);
        const side = shown[moved].side === 'left' ? 'right' : 'left';
        // the decision through Visual Review: the presenter of one scene moved to the other side
        await page.click(`.review-overlay.open .review-item[data-slot="presenter"][data-scene="${moved}"]`);
        await page.click('.review-overlay.open .review-actions [data-action="change"]');
        await page.click(`.review-overlay.open .review-presenter-moves [data-action="move-${side}"]`);
        await page.waitForFunction(([n, s]) => { const r = (slides[n].visual_review || {}).presenter || {}; return ['changed', 'approved'].includes(r.status) && slides[n].presenter_plan && slides[n].presenter_plan.position === s; },
            [moved, side], { timeout: 20000 });
        await sleep(400);
        const rightAway = await runQuality(page, 'presenter-decision');
        const forMoved = issuesOf(rightAway.report).filter(f => f.scene === moved);
        note(`right after the decision (scene ${moved + 1} moved ${side}, composition not planned again yet): ${forMoved.map(describe).join(' || ') || 'nothing'}`);
        // the page plans the lesson again (as on every opening of the review), then a presenter plan is put out of step by hand
        await closeReview(page);
        await openReview(page);
        const sideNow = await page.evaluate(n => (slides[n].cinematic_plan.presenter || {}).side, moved);
        const other = await page.evaluate(n => { const p = slides[n].presenter_plan; const was = p.presenter_id; p.presenter_id = was === 'aadhi' ? 'aadhi-teacher' : 'aadhi'; p.type = was === 'aadhi' ? 'illustrated' : 'mascot'; return { was, now: p.presenter_id }; }, crafted.i);
        const r = await runQuality(page, 'presenter-crafted');
        await panelShot(page, 'panel-presenter.png');
        const mine = issuesOf(r.report).filter(f => f.scene === moved);
        // a presenter finding may be folded into another one of the same scene (one cause, one finding: evidence.also)
        const alsoOf = f => ((f.evidence || {}).also || []);
        const aboutPresenter = f => f.dimension === 'presenter' || /^presenter\./.test(f.rule) || alsoOf(f).some(x => /^presenter\./.test(x));
        const presenterMine = mine.filter(aboutPresenter);
        const accidental = presenterMine.filter(f => f.severity !== 'info' || /unexpected_switch|side_flip|plan_mismatch/.test([f.rule, ...alsoOf(f)].join(' ')));
        const theirs = issuesOf(r.report).filter(f => f.scene === crafted.i && aboutPresenter(f) && f.severity !== 'info');
        // put the crafted plan back for what follows
        await page.evaluate(([n, was]) => { const p = slides[n].presenter_plan; p.presenter_id = was; p.type = was === 'aadhi' ? 'mascot' : 'illustrated'; }, [crafted.i, other.was]);
        check('7. the presenter moved for one scene through Visual Review (a decision) is not reported as an accidental switch: info at most',
            accidental.length === 0 && sideNow === side,
            `scene ${moved + 1} moved ${side} (composition now ${sideNow}); its presenter findings: ${presenterMine.map(describe).join(' || ') || 'none'}; all its findings: ${mine.map(describe).join(' || ') || 'none'}`);
        check('7b. a presenter plan out of step with the scene\'s composition (no decision) is reported',
            theirs.length > 0 && theirs.some(f => ['warning', 'error', 'blocking'].includes(f.severity)),
            `scene ${crafted.i + 1}: presenter_plan ${other.was} -> ${other.now} by hand; reported: ${theirs.map(describe).join(' || ') || 'NOTHING'}`);
    });

    // ---- 2. the problem lesson -------------------------------------------------------------------------------------------------
    let problemReport = null;
    let approvedBefore = null;
    await section('2. the problem lesson: the findings by scene, the chips, "Show scene", the scene block', async () => {
        phase = 'problem';
        await openLesson(page, problemPid);
        await openReview(page);
        await approveComposition(page, P.definition);
        await approveComposition(page, P.summary);
        approvedBefore = await page.evaluate(reviewState);
        // the style switched without re-planning (the review is open on the start screen: the page keeps the style, the plans
        // stay as they were), and one scene's plan_hash tampered (a stale plan)
        const kept = await pickStyle(page, 'children_education');
        const tampered = await page.evaluate(n => {
            const p = slides[n].cinematic_plan;
            const was = { hash: p.plan_hash, camera: p.camera ? p.camera.movement : null };
            p.plan_hash = 'tampered' + String(p.plan_hash || '').slice(8);
            p.camera = was.camera && was.camera !== 'static' ? { ...p.camera, movement: 'static' } : { ...(p.camera || {}), movement: 'slow_zoom_in' };
            return { was, now: { hash: p.plan_hash, camera: p.camera.movement } };
        }, P.stale);
        const r = await runQuality(page, 'problem');
        problemReport = r.report;
        // observations (not checks): one style switch reported per scene too, and the panel's lists as styled on screen
        const stalePlans = issuesOf(r.report).filter(f => f.rule === 'core.stale_plan');
        note(`one style switch + one tampered plan: style.stale_look ${issuesOf(r.report).filter(f => f.rule === 'style.stale_look').length}, core.stale_plan ${stalePlans.length} (scenes ${stalePlans.map(f => f.scene + 1).join(',')}; evidence ${[...new Set(stalePlans.map(f => JSON.stringify(f.evidence.found)))].join(' ')}); headline "${r.panel.headline}"`);
        const styling = await page.evaluate(() => {
            const cs = sel => { const e = document.querySelector('.review-overlay.open ' + sel); return e ? getComputedStyle(e) : null; };
            const ul = cs('.quality-issue-list'), dl = cs('.quality-dimension-list'), btn = cs('.quality-panel button.quality-areas');
            return { issueList: ul && `${ul.listStyleType} padding-left ${ul.paddingLeft}`, dimensionList: dl && `${dl.listStyleType} padding-left ${dl.paddingLeft}`,
                areasButton: btn && `display ${btn.display}, background ${btn.backgroundColor}, border ${btn.borderTopStyle} ${btn.borderTopColor}` };
        });
        note(`panel styling: findings list ${styling.issueList}; areas list ${styling.dimensionList}; "Show them" button ${styling.areasButton}`);
        await panelShot(page, 'panel-problem.png');
        await shot(page, 'review-problem.png');
        issuesOf(r.report).forEach(f => note('  finding: ' + describe(f)));
        const found = PROBLEM_EXPECT.map(e => ({ ...e, hits: issuesOf(r.report).filter(f => e.rules.includes(f.rule) && f.severity !== 'info' && (e.scene === undefined || f.scene === e.scene)) }));
        const hit = key => (found.find(e => e.key === key) || { hits: [] }).hits[0];
        const shownIds = new Set(r.panel.groups.flatMap(g => g.issues.map(i => i.id)));
        const allShown = found.every(e => e.hits.some(f => shownIds.has(f.id)));
        // grouped by scene (each group titled with its scene; its findings are that scene's), each with an icon and words
        const byId = Object.fromEntries(issuesOf(r.report).map(f => [f.id, f]));
        const groupsOk = r.panel.groups.length >= 3 && r.panel.groups.every(g => g.issues.every(i => {
            const f = byId[i.id];
            return f && (g.scene === 'lesson' ? f.scene === null : String(f.scene) === g.scene);
        })) && r.panel.groups.filter(g => g.scene !== 'lesson').every(g => /^Scene \d+/.test(g.title));
        const words = { notice: 'Worth a look', warning: 'Please check', error: 'Needs fixing', blocking: 'Blocks the export', info: 'Noted' };
        const iconsOk = r.panel.groups.flatMap(g => g.issues).every(i => i.mark && /[⚠✕ℹ]/.test(i.mark) && i.sev.includes(words[i.severity]));
        const sceneOf = found.flatMap(e => e.hits).map(f => f.scene).filter(s => s !== null);
        const chipScenes = new Set(r.panel.chips.map(c => Number(c.scene)));
        const chipsOk = [...new Set(sceneOf)].every(s => chipScenes.has(s)) && r.panel.chips.every(c => /^[⚠✕] \d+$/.test(c.text));
        check('2. the problem lesson\'s report lists each deliberate problem, grouped by scene, each with an icon and words',
            kept === 200 && found.every(e => e.hits.length) && allShown && groupsOk && iconsOk && !r.panel.engineering,
            `${found.map(e => `${e.what}: ${e.hits.length ? e.hits.map(f => `${f.rule} (${f.severity}, scene ${f.scene === null ? '-' : f.scene + 1})`).join(', ') : 'MISSING'}`).join(' | ')}; `
            + `groups ${r.panel.groups.map(g => `${g.title} [${g.issues.length}]`).join(', ')} (${groupsOk ? 'each its scene\'s findings' : 'GROUPING WRONG'}); icon+words ${iconsOk}; engineering words in the panel ${r.panel.engineering}; tampered scene ${P.stale + 1}: plan_hash ${String(tampered.was.hash).slice(0, 10)}… -> ${tampered.now.hash.slice(0, 10)}…, camera ${tampered.was.camera} -> ${tampered.now.camera}`);
        check('2b. the scenes with findings carry a quality chip in the review list, and the "Quality" filter is offered',
            chipsOk && r.panel.filter,
            `chips ${r.panel.chips.map(c => `scene ${Number(c.scene) + 1}/${c.slot} ${c.text} [${c.severity}]`).join(', ')}; scenes with findings ${[...new Set(sceneOf)].map(s => s + 1).join(',')}; filter ${r.panel.filter}`);
        // "Show scene" on the code finding: its scene is selected and its inspector carries the scene's quality block
        const code = hit('code');
        await page.click(`.review-overlay.open .quality-group[data-scene="${code.scene}"] .quality-show`);
        await page.waitForFunction(n => { const s = document.querySelector('.review-overlay.open .review-item.selected'); return s && s.getAttribute('data-scene') === String(n); }, code.scene, { timeout: 10000 });
        await sleep(300);
        const block = await page.evaluate(() => {
            const b = document.querySelector('.review-overlay.open .review-detail div.review-quality');
            const sel = document.querySelector('.review-overlay.open .review-item.selected');
            let visible = null;
            if (b) {
                let box = b.parentElement;
                while (box && box !== document.body && !/(auto|scroll)/.test(getComputedStyle(box).overflowY)) box = box.parentElement;
                const r = b.getBoundingClientRect();
                const v = box && box !== document.body ? box.getBoundingClientRect() : { top: 0, bottom: innerHeight };
                visible = { top: Math.round(r.top), bottom: Math.round(r.bottom), viewTop: Math.round(v.top), viewBottom: Math.round(v.bottom),
                    scroller: box && box !== document.body ? box.className : 'page', scrollTop: box ? Math.round(box.scrollTop) : null,
                    inView: r.bottom > v.top + 4 && r.top < v.bottom - 4 };
            }
            return { visible, selected: sel ? `${sel.getAttribute('data-scene')}:${sel.getAttribute('data-slot')}` : null, block: !!b, severity: b ? b.getAttribute('data-severity') : null,
                lead: b ? (b.querySelector('.review-quality-auto') || {}).textContent : null, items: b ? [...b.querySelectorAll('.quality-issue')].map(li => li.textContent.replace(/\s+/g, ' ').trim()) : [] };
        });
        await shot(page, `problem-scene-${code.scene + 1}-shown.png`); // as the user sees it right after "Show scene"
        const codeBlock = page.locator('.review-overlay.open .review-detail div.review-quality');
        await codeBlock.scrollIntoViewIfNeeded().catch(() => {});
        await codeBlock.screenshot({ path: path.join(OUT, `problem-scene-${code.scene + 1}-block.png`) }).catch(() => {});
        // the overloaded scene's block too
        const busy = hit('busy');
        let busyBlock = null;
        if (busy && busy.scene !== null) {
            await page.click(`.review-overlay.open .quality-group[data-scene="${busy.scene}"] .quality-show`);
            await sleep(400);
            busyBlock = await page.evaluate(() => { const b = document.querySelector('.review-overlay.open .review-detail div.review-quality'); return b ? b.textContent.replace(/\s+/g, ' ').trim().slice(0, 200) : null; });
            const busyLocator = page.locator('.review-overlay.open .review-detail div.review-quality');
            await busyLocator.scrollIntoViewIfNeeded().catch(() => {});
            await busyLocator.screenshot({ path: path.join(OUT, `problem-scene-${busy.scene + 1}-block.png`) }).catch(() => {});
        }
        check('2c. "Show scene" selects the scene in the review and its inspector shows the scene\'s quality block (div.review-quality)',
            block.selected === `${code.scene}:composition` && block.block && block.items.some(t => /Java/.test(t)) && /Quality: \d+ things? to look at in this scene/.test(block.lead || ''),
            `selected ${block.selected}; block ${block.block} [${block.severity}] "${(block.lead || '').trim()}": ${block.items.map(t => t.slice(0, 90)).join(' || ')}; overloaded scene block: ${busyBlock ? '"' + busyBlock.slice(0, 120) + '"' : '-'}`);
        const v = block.visible;
        check('2d. right after "Show scene" the scene\'s quality block is in view in the inspector (not scrolled away)',
            v && v.inView,
            v ? `block at ${v.top}-${v.bottom}px, the inspector (${v.scroller}) shows ${v.viewTop}-${v.viewBottom}px with scrollTop ${v.scrollTop}; problem-scene-${code.scene + 1}-shown.png` : 'no block');
    });

    // ---- 3. safe repair --------------------------------------------------------------------------------------------------------
    await section('3. "Fix automatically" re-plans; after "Check again" the finding is gone, approvals unchanged', async () => {
        phase = 'repair';
        if (!problemReport) throw new Error('no problem report from check 2');
        // the stale stored plan of one scene (tampered in check 2): its automatic repair plans that scene again, and only it
        const target = issuesOf(problemReport).find(f => f.rule === 'core.stale_plan' && f.scene === P.stale && f.repair.kind === 'auto')
            || issuesOf(problemReport).find(f => f.repair.kind === 'auto' && f.severity !== 'info');
        if (!target) throw new Error('the problem report offers no automatic repair: ' + issuesOf(problemReport).map(describe).join(' || '));
        const targets = target.repair.action.scenes;
        const hashes = () => page.evaluate(() => slides.map(s => (s.cinematic_plan || {}).plan_hash || null));
        const hashBefore = await hashes();
        const before = await page.evaluate(reviewState);
        const startAt = requests.length;
        const plan = page.waitForResponse(r => r.url().includes('/api/cinematic/plan') && r.request().method() === 'POST', { timeout: 30000 }).catch(() => null);
        await page.click(`.review-overlay.open .quality-panel .quality-issue[data-issue="${target.id}"] [data-action="quality-repair"]`);
        const planned = await plan;
        await page.waitForFunction(() => !reviewSession.qualityBusy, null, { timeout: 30000 });
        await sleep(400);
        const afterFix = await page.evaluate(qualityPanel);
        const hashAfter = await hashes();
        await panelShot(page, 'panel-problem-fixed.png');
        const r = await runQuality(page, 'problem-after-repair');
        await panelShot(page, 'panel-problem-after-repair.png');
        const after = await page.evaluate(reviewState);
        const stillThere = issuesOf(r.report).filter(f => f.id === target.id);
        const staleThere = issuesOf(r.report).filter(f => f.rule === 'core.stale_plan' && targets.includes(f.scene));
        const changed = hashBefore.map((h, i) => (h !== hashAfter[i] ? i : null)).filter(i => i !== null);
        const onlyTargets = changed.every(i => targets.includes(i)) && targets.every(i => changed.includes(i));
        const generated = generationIn(requests.slice(startAt));
        const reviewsSame = JSON.stringify(before.reviews) === JSON.stringify(after.reviews);
        // what is left of the lesson-wide style switch (not part of this repair) and how it is offered
        const lookLeft = issuesOf(r.report).filter(f => /^style\./.test(f.rule) && f.scene === null);
        const lookButtons = r.panel.groups.flatMap(g => g.issues).filter(i => lookLeft.some(f => f.id === i.id)).map(i => (i.fix ? `${i.fix.action} "${i.fix.text}"` : 'no button'));
        note(`left after the repair (not part of it): ${lookLeft.map(f => `${describe(f)} -> ${f.repair.kind}/${f.repair.action.type} "${f.repair.label}"`).join(' || ') || 'no lesson-wide style finding'}; panel: ${lookButtons.join(', ') || '-'}`);
        check('3. "Fix automatically" on the stale plan re-plans only the scenes it names; after "Check again" the finding is gone and the report fingerprint changed',
            planned && planned.status() === 200 && /Fixed/.test(afterFix.state || '') && afterFix.stale && stillThere.length === 0 && staleThere.length === 0
            && onlyTargets && r.report.fingerprint !== problemReport.fingerprint,
            `fixed ${describe(target)}${target.evidence.also ? ` (folded: ${target.evidence.also.join(', ')})` : ''}; POST /api/cinematic/plan ${planned ? planned.status() : 'NOT SENT'}; `
            + `plans changed in scenes ${changed.map(i => i + 1).join(',') || 'none'} (repair names ${targets.map(i => i + 1).join(',')}); panel "${afterFix.state}", stale note ${afterFix.stale}; `
            + `after Check again: ${stillThere.length ? 'STILL THERE' : 'gone'}, stale plan in those scenes ${staleThere.length}; fingerprint ${problemReport.fingerprint} -> ${r.report.fingerprint}; now ${countLine(r.report)}`);
        check('3b. the repair kept every scene\'s review record and the approvals (slides[i].visual_review unchanged), and generated nothing',
            reviewsSame && after.statuses[P.definition] === 'approved' && after.statuses[P.summary] === 'approved' && generated.length === 0
            && JSON.stringify(approvedBefore.reviews) === JSON.stringify(after.reviews),
            `visual_review ${reviewsSame ? 'unchanged in every scene' : 'CHANGED: ' + before.reviews.map((x, i) => (x !== after.reviews[i] ? `scene ${i + 1} ${x} -> ${after.reviews[i]}` : null)).filter(Boolean).join('; ')}; `
            + `approved scenes ${P.definition + 1}: ${after.statuses[P.definition]}, ${P.summary + 1}: ${after.statuses[P.summary]}; ${generated.length} generation requests`);
        problemReport = r.report;
    });

    // ---- 4. a suggestion through the composition review ----------------------------------------------------------------------
    await section('4. "Apply suggestion" goes through the composition review and becomes the user\'s change', async () => {
        phase = 'suggest';
        if (!problemReport) throw new Error('no problem report');
        const target = issuesOf(problemReport).find(f => f.repair.kind === 'suggest' && f.repair.action.type === 'composition' && f.repair_status === 'available');
        if (!target) throw new Error('the report offers no composition suggestion: ' + issuesOf(problemReport).map(describe).join(' || '));
        const scene = target.repair.action.scene;
        const startAt = requests.length;
        const reviewed = page.waitForResponse(r => r.url().includes('/api/cinematic/review') && r.request().method() === 'POST', { timeout: 30000 });
        const button = page.locator(`.review-overlay.open .quality-panel .quality-issue[data-issue="${target.id}"] [data-action="quality-apply"]`);
        const label = (await button.textContent()).trim();
        await button.click();
        const response = await reviewed;
        const sent = JSON.parse(response.request().postData() || '{}');
        await page.waitForFunction(() => !reviewSession.qualityBusy, null, { timeout: 30000 });
        await sleep(500);
        const state = await page.evaluate(qualityPanel);
        const review = await page.evaluate(n => (slides[n].visual_review || {}).composition || null, scene);
        const saved = (((await api('GET', `/api/projects/${problemPid}`)).data || {}).scenes || [])[scene] || {};
        const savedReview = (saved.visual_review || {}).composition || null;
        await panelShot(page, 'panel-problem-applied.png');
        const generated = generationIn(requests.slice(startAt));
        const want = target.repair.action.overrides;
        const has = o => o && Object.entries(want).every(([k, v]) => o[k] === v);
        check('4. "Apply suggestion" on a composition suggestion: POST /api/cinematic/review (action change), the scene\'s review becomes the user\'s change',
            response.status() === 200 && sent.action === 'change' && sent.scene_index === scene && has(sent.overrides) && review && review.status === 'changed' && has(review.overrides)
            && /Applied/.test(state.state || '') && generated.length === 0,
            `${describe(target)}; button "${label}"; POST /api/cinematic/review ${response.status()} ${JSON.stringify({ action: sent.action, scene_index: sent.scene_index, overrides: sent.overrides })}; `
            + `scene ${scene + 1} review ${review ? `${review.status} ${JSON.stringify(review.overrides)}` : 'none'}; saved ${savedReview ? savedReview.status : 'none'}; panel "${state.state}"; ${generated.length} generation requests`);
    });

    // ---- 9. reduced motion ---------------------------------------------------------------------------------------------------
    await section('9. reduced motion: the stage plays calm and the report has no motion error', async () => {
        phase = 'reduced';
        const calm = await newPage(browser, { width: 1280, height: 720 }, { ...baseCine, motion: 'subtle' }, { reducedMotion: 'reduce' });
        const p = calm.page;
        p.on('dialog', d => d.accept().catch(() => {}));
        await startLesson(p, cleanPid);
        const moving = await p.evaluate(() => slides.map((s, i) => ({ i, movement: s.cinematic_plan && s.cinematic_plan.camera ? s.cinematic_plan.camera.movement : null }))
            .filter(x => x.movement && x.movement !== 'static').map(x => x.i));
        const scenes = (moving.length ? moving : [AT.definition, AT.formula, AT.diagram]).slice(0, 4);
        const start = await p.evaluate(() => cinematicStage.stats.cameraMoves);
        const played = [];
        for (const i of scenes) played.push(await playScene(p, i));
        await shot(p, 'reduced-motion-scene.png');
        const moves = (await p.evaluate(() => cinematicStage.stats.cameraMoves)) - start;
        const report = await p.evaluate(() => reviewSession.composition.qualityRun());
        reports['reduced-motion'] = { report };
        const motionBad = issuesOf(report).filter(f => ['motion', 'camera'].includes(f.dimension) && ['error', 'blocking'].includes(f.severity));
        const motionAny = issuesOf(report).filter(f => ['motion', 'camera'].includes(f.dimension) && f.severity !== 'info');
        await stopNarration(p);
        await calm.context.close();
        check('9. prefers-reduced-motion: the stage plays calm (no camera move, no camera transform or animation running) and the quality report has no motion error',
            played.every(x => x.mid.reduced && x.mid.calm === true && x.mid.transforms.length === 0 && x.mid.running === 0 && x.end.transforms.length === 0) && moves === 0 && motionBad.length === 0,
            `scenes ${scenes.map(i => i + 1).join(',')} (planned camera ${played.map(x => x.mid.planned).join(',')}); reduced ${played.every(x => x.mid.reduced)}, calm ${played.map(x => x.mid.calm).join(',')}; camera moves ${moves}; `
            + `transforms ${played.map(x => x.mid.transforms.length + x.end.transforms.length).join(',')}; running ${played.map(x => x.mid.running).join(',')}; report ${countLine(report)}; motion/camera findings ${motionAny.map(describe).join(' || ') || 'none'}`);
    });

    await browser.close();
    browser = null;

    // ---- 8. preview and export ------------------------------------------------------------------------------------------------
    const QUALITY_HEADING = 'The quality check found things to review';
    const PREPARE_HEADING = 'Some parts of the lesson could not be prepared';
    function promptParts(prompt) {
        const parts = String(prompt || '').split(/\n\s*\n/);
        const items = head => { const part = parts.find(x => x.trim().startsWith(head)); return part ? part.split('\n').slice(1).map(x => x.replace(/^\s*•\s*/, '').trim()).filter(Boolean) : null; };
        return { quality: items(QUALITY_HEADING), prepare: items(PREPARE_HEADING) };
    }
    // The export's first step on a lesson with findings: its prompt, then "Cancel" (nothing is recorded)
    async function exportPrompt(b, projectId, file) {
        const ex = await newPage(b, { width: 1280, height: 720 }, baseCine);
        const p = ex.page;
        const answers = [];
        p.on('response', async r => { if (r.url().includes('/api/quality/lesson') && r.request().method() === 'POST') answers.push(await r.json().catch(() => null)); });
        await openLesson(p, projectId);
        await p.evaluate(() => planLessonVisuals());
        await p.click('#start-videos-btn');
        await p.click('.export-start-btn');
        const start = p.locator('.export-actions button', { hasText: 'Start Recording' });
        const anyway = p.locator('.export-actions button', { hasText: 'Record anyway' });
        await Promise.race([start.waitFor({ timeout: 120000 }), anyway.waitFor({ timeout: 120000 })]);
        const asked = await anyway.isVisible();
        const prompt = asked ? await p.textContent('.export-message') : null;
        await p.screenshot({ path: path.join(OUT, file) });
        await p.locator('.export-actions button', { hasText: 'Cancel' }).click();
        await sleep(800);
        await ex.context.close();
        return { asked, prompt, report: answers[answers.length - 1] || null };
    }
    const EXPORT_ARGS = ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'];
    exportBrowser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'], args: EXPORT_ARGS });
    silent.flags.push('export: ' + EXPORT_ARGS.join(' ') + ' + suppressLocalAudioPlayback');
    // the problem lesson's export prompt: its findings under the quality heading, apart from what could not be prepared
    await section('8c. the export prompt of a lesson with findings', async () => {
        phase = 'export-prompt';
        const { asked, prompt, report } = await exportPrompt(exportBrowser, problemPid, 'export-prompt-problem.png');
        reports['export-prompt-problem'] = { report, prompt };
        const parts = promptParts(prompt);
        const listed = issuesOf(report).filter(f => ['warning', 'error', 'blocking'].includes(f.severity) && !['media.missing', 'style.background_fallback'].includes(f.rule));
        const inPrepare = (parts.prepare || []).filter(x => issuesOf(report).some(f => x.includes(f.message.slice(0, 40))));
        const shown = listed.filter(f => (parts.quality || []).some(x => x.includes(f.message.slice(0, 40))));
        check('8c. on a lesson with findings the export asks first and lists them under their own heading ("The quality check found things to review"), not among the parts that could not be prepared',
            asked && parts.quality && parts.quality.length > 0 && shown.length === Math.min(listed.length, 12) && inPrepare.length === 0 && parts.quality.some(x => /Java/.test(x)),
            `asked ${asked}; quality section ${parts.quality ? parts.quality.length + ' line(s): ' + parts.quality.map(x => x.slice(0, 80)).join(' || ') : 'MISSING'}; `
            + `could-not-be-prepared ${parts.prepare ? parts.prepare.length + ' line(s)' : 'none'}${inPrepare.length ? ' WITH QUALITY FINDINGS: ' + inPrepare.join(' || ') : ''}; `
            + `report ${report ? countLine(report) : 'none'} (${listed.length} warning or worse, ${shown.length} shown); export-prompt-problem.png`);
    });

    await section('8. the export of the clean lesson: no quality error before recording; preview frame = export frame', async () => {
        phase = 'export';
        const left = BUDGET_MS - (Date.now() - STARTED);
        if (left < 150000) throw new Error(`not enough time left for the export (${Math.round(left / 1000)} s)`);
        const exportPid = (await api('POST', '/save-history', CLEAN)).data.id;
        const ex = await newPage(exportBrowser, { width: 1280, height: 720 }, baseCine);
        const p = ex.page;
        const qualityAnswers = [];
        p.on('response', async r => {
            if (r.url().includes('/api/quality/lesson') && r.request().method() === 'POST') qualityAnswers.push(await r.json().catch(() => null));
        });
        await startLesson(p, exportPid);
        await playScene(p, AT.code);
        const previewFile = path.join(OUT, 'preview-code-end.png');
        await p.screenshot({ path: previewFile });
        await stopNarration(p);
        await openLesson(p, exportPid);
        await p.evaluate(() => planLessonVisuals());
        await p.click('#start-videos-btn');
        await p.click('.export-start-btn');
        const limit = Math.min(360000, left - 60000);
        let prompt = null;
        for (;;) {
            const start = p.locator('.export-actions button', { hasText: 'Start Recording' });
            const anyway = p.locator('.export-actions button', { hasText: 'Record anyway' });
            await Promise.race([start.waitFor({ timeout: limit }), anyway.waitFor({ timeout: limit })]);
            if (await anyway.isVisible()) {
                prompt = await p.textContent('.export-message');
                await p.screenshot({ path: path.join(OUT, 'export-prompt-clean.png') });
                await anyway.click();
                continue;
            }
            await start.click();
            break;
        }
        const recordEnd = Date.now() + limit;
        let outcome = null;
        while (!outcome && Date.now() < recordEnd) {
            await sleep(700);
            const s = await p.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden,
                error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
            if (s.ready) outcome = 'ready';
            else if (s.error) outcome = 'error: ' + s.error;
        }
        const job = await p.evaluate(() => exportFlow.job);
        const exportMoves = await p.evaluate(() => cinematicStage.stats.cameraMoves);
        const exportDir = path.join(data, 'exports');
        const userDir = job && fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(dd => fs.existsSync(path.join(exportDir, dd, `${job.id}.webm`))) : null;
        const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
        const sceneLog = await p.evaluate(() => window.exportSceneLog || []);
        const next = sceneLog[AT.code + 1];
        let diff = null, fullDiff = null;
        const exportFile = path.join(OUT, 'export-code-end.png');
        if (stored && next) {
            spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', Math.max(0, next.t - 0.6).toFixed(2), '-i', stored, '-frames:v', '1', exportFile]);
            if (fs.existsSync(exportFile)) {
                const a = grid(previewFile);
                const b = grid(exportFile);
                const lesson = 16 * 32 * 3; // the 32x18 grid without its two bottom rows (the preview's player controls)
                diff = +difference(a.slice(0, lesson), b.slice(0, lesson)).toFixed(1);
                fullDiff = +difference(a, b).toFixed(1);
            }
        }
        const parts = promptParts(prompt);
        const qualityLines = parts.quality || [];
        const prepared = qualityAnswers[qualityAnswers.length - 1];
        reports['export-prepare'] = { report: prepared, prompt };
        const errors = issuesOf(prepared).filter(f => ['error', 'blocking'].includes(f.severity));
        const warned = issuesOf(prepared).filter(f => f.severity === 'warning');
        await ex.context.close();
        check('8. before recording the clean lesson, the pre-export prompt holds no quality error (the export\'s own quality check finds no error / blocking)',
            prepared && errors.length === 0 && !qualityLines.some(x => /Needs attention before sharing/.test(x)) && outcome === 'ready',
            `${qualityAnswers.length} quality check(s) during the export, last: ${prepared ? `${prepared.status}, ${countLine(prepared)}` : 'NONE'}${errors.length ? '; ERRORS: ' + errors.map(describe).join(' || ') : ''}`
            + `${warned.length ? '; warnings: ' + warned.map(describe).join(' || ') : ''}; pre-export prompt: ${prompt ? `quality section ${qualityLines.length} line(s)${qualityLines.length ? ': ' + qualityLines.join(' || ') : ''}, `
                + `could-not-be-prepared ${(parts.prepare || []).length} line(s)${(parts.prepare || []).length ? ': ' + parts.prepare.join(' || ') : ''}` : 'none (nothing to warn about)'}; export ${outcome}`);
        check('8b. a preview frame matches the export frame (end of the code scene, player controls excluded: mean difference < 12)',
            outcome === 'ready' && diff !== null && diff < 12,
            `frame difference ${diff} (whole frame with the player controls ${fullDiff}); ${path.basename(previewFile)} vs ${path.basename(exportFile)}; camera moves while recording ${exportMoves} (the export is never calm)`);
    });

    // ---- 10. no errors; silent; nothing generated -----------------------------------------------------------------------------
    const generated = generationIn(requests);
    const quality = requests.filter(r => /\/api\/quality\/lesson/.test(r.url));
    fs.writeFileSync(path.join(OUT, 'reports.json'), JSON.stringify(reports, null, 2));
    fs.writeFileSync(path.join(OUT, 'requests.json'), JSON.stringify(requests.map(r => ({ ...r, url: r.url.replace(BASE, '') })), null, 2));
    check('10. no generation request during the whole check (planning, reviews and quality checks only)',
        generated.length === 0, `${generated.length} generation requests${generated.length ? ': ' + generated.slice(0, 4).map(r => `${r.phase} ${r.method} ${r.url.replace(BASE, '')}`).join(', ') : ''}; ${quality.length} quality checks, ${requests.length} requests in all`);
    check('10b. no unexpected page errors or failed requests', problems.length === 0, problems.slice(0, 6).join(' | '));
    check('10c. every browser was silent (audio output disabled, speech stubbed on every page, capture without local playback)',
        silent.stubbed.size === silent.pages && silent.pages > 0 && silent.flags.length === 2 && silent.flags.every(f => f.includes('--disable-audio-output')) && silent.flags[0].includes('--mute-audio'),
        `${silent.stubbed.size}/${silent.pages} pages stubbed; ${silent.flags.join(' | ')}`);
} catch (e) {
    check('check ran to the end', false, e.stack || e.message);
} finally {
    if (browser) await browser.close().catch(() => {});
    if (exportBrowser) await exportBrowser.close().catch(() => {});
    killServer();
    try { fs.writeFileSync(path.join(OUT, 'reports.json'), JSON.stringify(reports, null, 2)); } catch (e) { /* best effort */ }
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Artifacts: ${OUT}`);
process.exit(failed.length ? 1 : 0);
