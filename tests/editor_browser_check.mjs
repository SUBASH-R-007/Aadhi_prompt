// Advanced Video Editor check (Phase 19) in real Chrome against a throwaway server with the local stand-ins only
// (AI_FAKE_PROVIDER=1: stand-in media and text models; FAKE_TTS=1: a speech-like tone as long as the text). No real provider.
//
// Everything is done the way a user does it (clicks, keys, drags in the page); the page's state is only READ for the checks.
//   1. a saved cinematic lesson (9 scenes: introduction, definition, process, diagram with a library asset, formula, code,
//   comparison, presenter-led explanation with the drawn Aadhi Teacher, key points) opens and plays   2. the 🎬 button opens the
//   editor   3. the scene list shows every scene   4. a list click selects a scene: the inspector shows it, the stage renders
//   it   5. play / pause   6. seek on the timeline ruler (snaps to a scene start)   7. reorder: a timeline drag and Alt+Arrow
//   (scene ids kept)   8. a minimum duration lengthens the timeline   9. camera and 10. transition through the inspector (the
//   composition review: the scene's review becomes "changed")   11. the visual from the Asset Library (the visual review
//   route)   12. the presenter side   13. a scene accent   14. undo (Ctrl+Z and the button)   15. redo (Ctrl+Y and the button)
//   16. saved in place (PUT /api/editor, no /save-history, no new history entry)   17. reload   18. the edits remain
//   19. the quality check from the editor   20. the quality state and the scene chips   21. preview: the hold, the hidden scene
//   skipped, the captions off   22. export   23. the export follows the edits (order, hidden scene absent, chapters,
//   captions, the edited scene's frame = the preview's).   Plus: duplicate (new id, no approvals copied), delete with a
//   confirmation (library assets untouched), insert a blank scene / a library picture, a stale save (409: reload, replay,
//   saved, nothing lost), shortcuts off while typing, narration typed and reverted, a split at a pause (and its undo), mute,
//   labels, background density (and its undo), the small-screen editor, no errors, nothing generated, all silent.
//
// The expected style values come from styles.py itself (the repo's Python), never copied here.
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/editor_browser_check.mjs
// Silent: --mute-audio and --disable-audio-output, browser speech stubbed; the export's tab capture keeps the tab unmuted
// (a muted tab records silence) but asks for suppressLocalAudioPlayback, with no audio output device.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.EDITOR_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-editor-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.EDITOR_CHECK_PORT || 9970 + (process.pid % 8));
const BASE = `http://127.0.0.1:${PORT}`;
const STARTED = Date.now();
const BUDGET_MS = Number(process.env.EDITOR_CHECK_BUDGET_MS || 1600000); // stop starting new work before an outer timeout
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
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `aec-${process.pid}`), JWT_SECRET: 'editor-check-' + Math.random().toString(36).slice(2),
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
async function apiText(route) {
    const res = await fetch(BASE + route, { headers: { Authorization: 'Bearer ' + token } });
    return { status: res.status, text: await res.text().catch(() => '') };
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
// a scene being measured never advances to the next one (the export and the editor preview never set it: they advance as always)
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
const norm = v => String(v === undefined || v === null ? '' : v).trim().toLowerCase().replace(/\s+/g, ' ');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const ID_RE = /^s-[0-9a-f]{12}$/;
// editor_ui.js clock(): whole seconds, m:ss
const clock = s => { const v = Math.max(0, Math.round(s)); return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`; };
const parseClock = s => { const m = /^(\d+):(\d\d)$/.exec(String(s || '').trim()); return m ? +m[1] * 60 + +m[2] : null; };
function parseTimeLabel(text) {
    const [cur, total] = String(text || '').split('/').map(x => parseClock(x));
    return { cur, total };
}

// ---- what styles.py says (the source of truth), read with the repo's Python ------------------------------------------------
const PY = `
import json, styles
teal = {"visual_review": {"composition": {"status": "changed", "overrides": {"style_accent": "teal"}}}}
print(json.dumps({"teal": styles.css_variables(styles.resolve({"style": "academic"}, teal))["--st-accent"],
                  "accent": styles.css_variables(styles.resolve({"style": "academic"}))["--st-accent"]}))
`;
function expectedFromStylesPy() {
    const r = spawnSync(PYTHON, ['-c', PY], { cwd: REPO, encoding: 'utf8' });
    if (r.status !== 0) throw new Error('styles.py could not be read: ' + r.stderr);
    return JSON.parse(r.stdout);
}

// The lesson: an introduction, a definition, process steps, a diagram (a library asset), a formula, code with its output, a
// comparison table, a presenter-led explanation and the key points (the Academic style; the drawn Aadhi Teacher presents)
const LESSON = { subject_name: 'Editor check', session_title: 'How plants and programs work',
    cinematic_style: { style: 'academic', style_version: 1, style_overrides: {} },
    concept_map: [{ id: 'c1', title: 'Photosynthesis', depends_on: [] }, { id: 'c2', title: 'The leaf', depends_on: ['c1'] },
        { id: 'c3', title: "Newton's second law", depends_on: [] }, { id: 'c4', title: 'Loops', depends_on: [] }],
    scenes: [
    { type: 'content', concept_id: 'c1', title: 'Photosynthesis', subtitle: 'How plants make their own food', html: '<p>Plants turn sunlight, water and air into sugar.</p>',
      narration: 'Welcome! Today we find out how plants make food.' },
    { type: 'content', concept_id: 'c1', title: 'What is photosynthesis?',
      html: "<div class='definition'><span class='keyword'>Photosynthesis</span> is the process by which green plants use sunlight to make glucose from carbon dioxide and water.</div>",
      narration: 'Here is the definition. [SYNC] Plants use sunlight to make glucose.' },
    { type: 'content', concept_id: 'c1', title: 'How photosynthesis happens',
      html: '<ol><li>Chlorophyll absorbs sunlight</li><li>Water is split into hydrogen and oxygen</li><li>Carbon dioxide is taken in</li><li>Glucose is made</li></ol>',
      narration: 'Four steps. [SYNC] Light is absorbed. [SYNC] Water is split. [SYNC] Carbon dioxide enters. [SYNC] Glucose is made.' },
    { type: 'content', concept_id: 'c2', title: 'Inside the leaf', html: "<ul><li><span class='keyword'>Palisade cells</span> catch the light</li><li>The <span class='keyword'>stoma</span> lets air in</li></ul>",
      visual: { concept: 'leaf cross section', description: 'Leaf cross section with palisade cells and stoma', type: 'diagram', keywords: ['leaf', 'cross', 'section', 'palisade'] },
      narration: 'Look at this diagram of a leaf. [SYNC] Palisade cells catch light. [SYNC] The stoma lets air in.' },
    { type: 'content', concept_id: 'c3', title: "Newton's second law", html: "<div class='formula-block'>\\[F = ma\\]</div><p>Force = Mass × Acceleration</p>",
      narration: 'Here is the law. [SYNC] F equals m times a. [SYNC] Force is mass times acceleration.' },
    { type: 'content', concept_id: 'c4', title: 'Python for loop', html: "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre><p>It prints the numbers 0 to 4, one per line.</p>",
      narration: 'Here is a loop. It prints the numbers zero to four.' },
    { type: 'content', title: 'Plants vs Animals', html: '<table><thead><tr><th>Plants</th><th>Animals</th></tr></thead><tbody><tr><td>Make their own food</td><td>Eat other living things</td></tr><tr><td>Release oxygen</td><td>Release carbon dioxide</td></tr></tbody></table>',
      narration: 'Let us compare plants and animals side by side.' },
    { type: 'content', concept_id: 'c1', title: 'Why plants matter to us', html: '<p>Almost every breath you take holds oxygen that a plant released.</p>',
      narration: 'Think about this. Almost every breath you take holds oxygen that a plant released.' },
    { type: 'key-takeaway', title: 'Key takeaways', html: "<ul class='takeaway-list'><li>Plants make glucose from sunlight</li><li>Chlorophyll captures the light</li><li>Loops repeat code</li><li>F = ma links force and motion</li></ul>",
      narration: 'Let us recap. [SYNC] Plants make glucose. [SYNC] Chlorophyll captures light. [SYNC] Loops repeat code. [SYNC] Force is mass times acceleration.' }
] };
const KINDS = ['intro', 'definition', 'process', 'diagram', 'formula', 'code', 'comparison', 'explanation', 'summary'];
const TITLE = Object.fromEntries(KINDS.map((k, i) => [k, LESSON.scenes[i].title]));
// The order the edits below give the lesson (formula dragged after code, the explanation moved one place earlier); the
// comparison is hidden
const FINAL = ['intro', 'definition', 'process', 'diagram', 'code', 'formula', 'explanation', 'comparison', 'summary'];
const PLAYED = FINAL.filter(k => k !== 'comparison');
const HOLD = 14;                      // the code scene's minimum duration (seconds)
const OTHER_SUBTITLE = 'Changed in another tab';

// Media / AI generation endpoints (editing must never call one): planning (/api/cinematic/plan, /api/visuals/plan), reviews,
// quality, saving and reading files are not generation
const GENERATION = /\/generate-ai|\/api\/ai-media\/lessons\/[^/]+\/generate|\/api\/cinematic\/background|\/regenerate-manim|\/get-image|\/get-gif|\/api\/presenters\/(?:[^?]*\/)?(?:render|generate)(?:[/?]|$)|\/api\/visuals\/[^?]*generate/;
const isGeneration = (method, url) => GENERATION.test(url) || (method !== 'GET' && /\/api\/ai[-/]/.test(url));

// ---- page-side probes (serialized into the page; they only READ) -------------------------------------------------------------
// The lesson and the editor model as the page holds them
function edState() {
    const s = typeof lessonEditorSession !== 'undefined' ? lessonEditorSession : null;
    const w = s && s.workspace;
    const m = s && s.model;
    const a = s && s.autosave;
    const comp = sc => (sc && sc.visual_review && sc.visual_review.composition) || null;
    const tl = m ? m.timeline() : null;
    return {
        open: !!(w && w.isOpen), selected: w ? w.selectedId : null, current: currentSlide, renderId: window.currentSlideRenderId,
        playing: !!ttsState.isPlaying, ids: slides.map(x => x && x.scene_id), titles: slides.map(x => x && x.title),
        edits: slides.map(x => (x && x.edit ? JSON.parse(JSON.stringify(x.edit)) : null)),
        comps: slides.map(x => (comp(x) ? { status: comp(x).status, overrides: comp(x).overrides || {} } : null)),
        reviewSlots: slides.map(x => (x && x.visual_review ? Object.keys(x.visual_review) : [])),
        planReview: slides.map(x => (x && x.cinematic_plan ? x.cinematic_plan.review_status || null : null)),
        plans: slides.map(x => {
            const p = (x && x.cinematic_plan) || {};
            return { camera: p.camera ? p.camera.movement : null, transition: p.transition ? p.transition.in : null,
                side: p.presenter ? p.presenter.side : null, shown: p.presenter ? !!p.presenter.shown : null,
                accent: p.style && p.style.look && p.style.look.css ? p.style.look.css['--st-accent'] : null };
        }),
        sideAsset: slides.map(x => (x && x.visual_plan && x.visual_plan.side && x.visual_plan.side.asset_id) || null),
        html: slides.map(x => (x && typeof x.html === 'string' ? x.html.slice(0, 1200) : null)),
        subtitles: slides.map(x => (x && x.subtitle) || null),
        total: tl ? tl.total : null, play: tl ? tl.scenes.map(t => t.play_seconds) : [], starts: tl ? tl.scenes.map(t => t.start) : [],
        dirty: m ? m.dirty : null, save: a ? a.state : null, revision: m ? m.revision : null,
        canUndo: m ? m.canUndo() : false, canRedo: m ? m.canRedo() : false,
        editor: window.lessonEditor ? JSON.parse(JSON.stringify(window.lessonEditor)) : null
    };
}
// The editor's chrome as drawn
function edUI() {
    const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
    const q = sel => document.querySelector(sel);
    const rect = el => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
    const bar = document.getElementById('voice-control-bar');
    const undo = q('.editor-top [data-action="undo"]');
    const redo = q('.editor-top [data-action="redo"]');
    const quality = q('.editor-top [data-action="quality"]');
    const ph = q('.editor-playhead');
    return {
        root: !!q('.editor-root'), active: document.body.classList.contains('editor-active'),
        barDisplay: bar ? getComputedStyle(bar).display : null,
        list: [...document.querySelectorAll('.editor-scene-list .editor-scene')].map(b => ({ id: b.getAttribute('data-scene-id'), index: +b.getAttribute('data-index'),
            title: t(b.querySelector('.editor-scene-title')), meta: t(b.querySelector('.editor-scene-meta')), current: b.getAttribute('aria-current'),
            hidden: b.classList.contains('is-hidden'),
            marks: [...b.querySelectorAll('.editor-mark')].map(m => ({ text: t(m), approval: m.getAttribute('data-approval'), mark: m.getAttribute('data-mark'),
                severity: m.getAttribute('data-severity'), stale: m.getAttribute('data-stale'), chip: m.classList.contains('editor-quality-chip') })) })),
        count: t(q('.editor-scene-count')),
        inspectorHead: t(q('.editor-inspector .editor-panel-head span')),
        factTitle: t(q('.editor-inspector .editor-fact-title')),
        save: q('.editor-save') ? q('.editor-save').getAttribute('data-state') : null, saveText: t(q('.editor-save')),
        time: t(q('.editor-tl-time')), play: t(q('.editor-timeline [data-action="play"]')),
        blocks: [...document.querySelectorAll('.editor-tl-row[data-track="scene"] button.editor-tl-block')].map(b => ({ id: b.getAttribute('data-scene-id'), ...rect(b),
            hidden: b.classList.contains('is-hidden'), text: t(b) })),
        captionLane: [...document.querySelectorAll('.editor-tl-row[data-track="caption"] .editor-tl-block')].map(b => ({ id: b.getAttribute('data-scene-id'), state: b.getAttribute('data-state') })),
        playheadX: ph ? ph.getBoundingClientRect().x : null,
        quality: t(quality), qualityStatus: quality ? quality.getAttribute('data-status') : null,
        message: t(q('.editor-top-message:not([hidden])')),
        confirm: q('.editor-confirm:not([hidden])') ? t(q('.editor-confirm')) : null,
        help: !!q('.editor-help:not([hidden])'),
        undo: undo ? { disabled: undo.disabled, label: undo.getAttribute('aria-label') } : null,
        redo: redo ? { disabled: redo.disabled, label: redo.getAttribute('aria-label') } : null,
        sceneQuality: (() => { const n = q('.editor-section[data-section="quality"] [data-quality]'); return n ? n.getAttribute('data-quality') : null; })(),
        findings: [...document.querySelectorAll('.editor-section[data-section="quality"] .editor-finding')].map(li => ({ severity: li.getAttribute('data-severity'), text: t(li) }))
    };
}
// What the stage shows
function stageFacts() {
    const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
    const img = document.querySelector('.dynamic-side-zone .side-panel-view.active img');
    return { current: currentSlide, id: slides[currentSlide] && slides[currentSlide].scene_id, renderId: window.currentSlideRenderId,
        title: t(document.getElementById('slide-title-container')), cineTitle: t(document.querySelector('.cine-title-text')),
        board: t(document.getElementById('slide-content-container')).slice(0, 160), img: img ? img.src : null,
        accent: document.documentElement.style.getPropertyValue('--st-accent').trim() };
}
// A recorder of what plays: every scene drawn (by renderSlide's render id) and the caption line, every 40 ms
function startRecorder() {
    if (window.__recTimer) clearInterval(window.__recTimer);
    window.__rec = { renders: [], caps: [] };
    let last = window.currentSlideRenderId;
    let shown = slides[currentSlide] && slides[currentSlide].scene_id;
    window.__recTimer = setInterval(() => {
        const now = Date.now();
        if (window.currentSlideRenderId !== last) {
            last = window.currentSlideRenderId;
            shown = slides[currentSlide] && slides[currentSlide].scene_id;
            window.__rec.renders.push({ t: now, i: currentSlide, id: shown, playing: !!ttsState.isPlaying });
        }
        const track = document.getElementById('subtitle-track');
        if (track) {
            const cs = getComputedStyle(track);
            const text = (track.textContent || '').replace(/\s+/g, ' ').trim();
            window.__rec.caps.push({ t: now, id: shown, on: track.classList.contains('active') && cs.visibility !== 'hidden' && cs.display !== 'none' && !!text,
                off: track.dataset.captions === 'off', text: text.slice(0, 50) });
        }
    }, 40);
}
function stopRecorder() {
    if (window.__recTimer) clearInterval(window.__recTimer);
    window.__recTimer = null;
    return window.__rec || { renders: [], caps: [] };
}

const problems = [];
const silent = { pages: 0, stubbed: new Set(), flags: [] };
const requests = [];          // every request the pages made: { phase, method, url }
const answers = [];           // the answers that matter: { phase, method, url, status }
const dialogs = [];           // window.alert / confirm / prompt shown by a page (none is expected)
const record = {};            // what the checks saw (written to editor-check.json)
const resumed = [];           // where the lesson started playing on its own while the editor had it paused
const assetScheme = [];       // requests for a raw "asset:ID" picture (a library reference the page did not resolve)
const newSelection = [];      // whether a new scene (duplicate, picture, blank) stayed selected after the edit
const ignoredClicks = [];     // list clicks on a scene that did nothing (clicked again, as a user would)
let phase = 'setup';
let browser = null;
let exportBrowser = null;
const VIEW = { width: 1280, height: 720 };
try {
    const EXPECT = expectedFromStylesPy();
    spawnSync(PYTHON, [path.join(REPO, 'tests', 'fixtures', 'cinematic_diagrams.py'), OUT]);
    ffmpeg('-f', 'lavfi', '-i', 'color=c=0x2e8b57:s=640x360,drawbox=x=170:y=80:w=300:h=200:color=0xf4d03f@1:t=fill', '-frames:v', '1', path.join(OUT, 'chloroplast.png'));
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const leafAsset = await upload(path.join(OUT, 'leaf_cross_section.png'), 'Leaf cross section showing palisade cells, spongy layer and stoma', ['leaf', 'cross', 'section', 'palisade', 'chloroplast']);
    const photoAsset = await upload(path.join(OUT, 'photosynthesis_diagram.png'), 'Photosynthesis plant diagram with sunlight, water, carbon dioxide and oxygen', ['photosynthesis', 'plant', 'diagram', 'sunlight']);
    const chloroAsset = await upload(path.join(OUT, 'chloroplast.png'), 'A chloroplast, the green part of a plant cell', ['chloroplast', 'cell']);
    const pid = (await api('POST', '/save-history', LESSON)).data.id;
    const { chromium } = await loadPlaywright();
    const MAIN_ARGS = ['--mute-audio', '--disable-audio-output', '--autoplay-policy=no-user-gesture-required'];
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: MAIN_ARGS });
    silent.flags.push('main: ' + MAIN_ARGS.join(' '));

    const baseCine = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'auto', typography: 'academic', composer: 'rules', director: 'rules',
        style: 'academic', style_version: 1, style_overrides: {} };
    const TEACHER = { presenter_id: 'aadhi-teacher', mode: 'auto', position: 'right', style: 'friendly', fallback: 'none' };
    async function newPage(b, viewport, cine) {
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
        page.on('pageerror', e => problems.push(`page error (${phase}): ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console (${phase}): ${m.text()}`); });
        page.on('response', r => {
            const url = r.url();
            const method = r.request().method();
            if (/\/api\/editor\/|\/api\/cinematic\/review|\/api\/visuals\/review|\/api\/quality\/lesson|\/save-history/.test(url)) answers.push({ phase, method, url: url.replace(BASE, ''), status: r.status() });
            // expected: the stale save refused once (409), then reloaded, replayed and saved
            const expected = phase === 'stale' && r.status() === 409 && method === 'PUT' && /\/api\/editor\//.test(url);
            if (r.status() >= 400 && !expected && !url.endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()} (${phase}): ${method} ${url.replace(BASE, '')}`);
        });
        page.on('requestfailed', r => {
            const err = (r.failure() || {}).errorText || '';
            if (/^asset:/.test(r.url())) { assetScheme.push({ phase, url: r.url(), err }); return; } // reported by the picture-scene check
            if (!/ERR_ABORTED/.test(err)) problems.push(`request failed (${err}, ${phase}): ${r.url()}`);
        });
        page.on('request', r => requests.push({ phase, method: r.method(), url: r.url().replace(BASE, '') }));
        page.on('dialog', d => { dialogs.push({ phase, type: d.type(), message: d.message().slice(0, 200) }); d.dismiss().catch(() => {}); });
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
    // "Play Video" on the start card; the intro (started 0.5 s after the click) skipped once it runs, as the arrow key does
    async function startLesson(page, projectId) {
        await openLesson(page, projectId);
        await page.click('#start-lecture-btn');
        await page.waitForFunction(() => isIntroRunning || document.body.classList.contains('presentation-active'), null, { timeout: 15000 }).catch(() => {});
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => document.body.classList.contains('presentation-active')
            && getComputedStyle(document.getElementById('intro-sequence-container')).display === 'none', null, { timeout: 30000 });
        await page.waitForFunction(() => slides.length && slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
        await sleep(1500);
    }
    async function stopNarration(page) {
        await page.evaluate(() => { ttsState.isPlaying = false; try { speakNarration(null); } catch (e) { /* nothing playing */ } });
    }
    // One scene played with its narration to the end, the next one not started (the export browser's preview frames)
    async function playScene(page, index) {
        await page.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; currentSlide = n; renderSlide(n); }, index);
        await sleep(1600);
        await page.waitForFunction(() => window.slideSyncState && window.slideSyncState.audioFinished, null, { timeout: 40000 }).catch(() => {});
        await sleep(700);
        return page.evaluate(stageFacts);
    }
    // The player bar shows while the pointer is over the page; its play / pause and 🎬 buttons are clicked there
    async function playerBar(page, selector) {
        await page.mouse.move(VIEW.width / 2, VIEW.height - 30);
        await sleep(600);
        await page.click(selector);
    }
    const shot = (page, name) => page.screenshot({ path: path.join(OUT, name) });
    const sceneSel = id => `.editor-scene-list .editor-scene[data-scene-id="${id}"]`;
    const state = page => page.evaluate(edState);
    const ui = page => page.evaluate(edUI);
    // The lesson kept paused while editing: when the page started playing on its own (a scene drawn on the stage starts its
    // narration), the editor's ❚❚ Pause is pressed, as a user would, and where it happened is recorded (a finding of its own)
    async function ensurePaused(page, context) {
        const deadline = Date.now() + 7000;
        let quietSince = null;
        let pressed = false;
        while (Date.now() < deadline) {
            const s = await page.evaluate(() => ({ playing: !!ttsState.isPlaying, open: !!document.querySelector('.editor-root'),
                label: (document.querySelector('.editor-timeline [data-action="play"]') || {}).textContent || '' }));
            if (!s.open) return;
            if (!s.playing) {
                if (quietSince === null) quietSince = Date.now();
                if (Date.now() - quietSince >= 1500) return;
                await sleep(150);
                continue;
            }
            quietSince = null;
            if (!pressed) resumed.push(context);
            pressed = true;
            if (/Pause/.test(s.label)) await page.click('.editor-timeline [data-action="play"]');
            await sleep(350);
        }
    }
    // the edits were saved (the autosave idle: saved, nothing pending, no typing session open), the lesson paused
    async function settle(page, ms = 2200, context = null) {
        await sleep(ms);
        const ok = await page.waitForFunction(() => {
            const s = typeof lessonEditorSession !== 'undefined' ? lessonEditorSession : null;
            return !s || !s.workspace.isOpen || (s.autosave.state === 'saved' && !s.model.dirty && !s.autosave.running && !s.model.inTransaction);
        }, null, { timeout: 30000 }).then(() => true).catch(() => false);
        if (!ok) note(`(${phase}) the editor did not reach "Saved": ${JSON.stringify(await page.evaluate(() => { const s = lessonEditorSession; return s ? { state: s.autosave.state, info: s.autosave.info, dirty: s.model.dirty } : null; }).catch(() => null))}`);
        await ensurePaused(page, context || `after an edit (${phase})`);
        await sleep(200);
        return ok;
    }
    // a scene clicked in the list (selected, shown on the stage), the lesson kept paused; clicked again if playback moved the
    // selection on meanwhile
    async function selectScene(page, id, label = null) {
        const name = label || Object.keys(ID).find(k => ID[k] === id) || 'a new scene';
        let ignored = 0;
        for (let attempt = 0; attempt < 4; attempt++) {
            await page.click(sceneSel(id));
            const ok = await page.waitForFunction(i => lessonEditorSession.workspace.selectedId === i, id, { timeout: 4000 }).then(() => true).catch(() => false);
            if (!ok) { ignored += 1; continue; }
            await ensurePaused(page, `a list click on ${name}`);
            if (await page.evaluate(i => lessonEditorSession.workspace.selectedId === i, id)) break;
        }
        if (ignored) ignoredClicks.push(`${name} (${phase}): ${ignored} click(s) without effect`);
        if (!(await page.evaluate(i => lessonEditorSession.workspace.selectedId === i, id))) throw new Error(`the scene ${name} could not be selected in the list`);
        await sleep(200);
    }
    async function openSection(page, key) {
        const open = await page.evaluate(k => !!document.querySelector(`.editor-inspector .editor-section[data-section="${k}"] .editor-section-body`), key);
        if (!open) await page.click(`.editor-inspector [data-action="section"][data-section="${key}"]`);
        await page.waitForSelector(`.editor-inspector .editor-section[data-section="${key}"] .editor-section-body`, { timeout: 5000 });
    }
    // A composition choice in the inspector: the composition review POST it makes, the scene's review record after it
    async function compositionChange(page, id, key, value, act) {
        const posted = page.waitForResponse(r => r.url().includes('/api/cinematic/review') && r.request().method() === 'POST', { timeout: 30000 });
        await act();
        const r = await posted;
        let sent = {};
        try { sent = JSON.parse(r.request().postData() || '{}'); } catch (e) { /* unreadable */ }
        await page.waitForFunction(([i, k, v]) => {
            const s = slides.find(x => x && x.scene_id === i);
            const c = s && s.visual_review && s.visual_review.composition;
            return !!(c && c.overrides && c.overrides[k] === v);
        }, [id, key, value], { timeout: 20000 }).catch(() => {});
        await settle(page, 1200);
        return { status: r.status(), sent: { action: sent.action, scene_index: sent.scene_index, overrides: sent.overrides } };
    }
    // The Asset Library in pick mode: the asset clicked, then "Use this visual"
    async function pickFromLibrary(page, assetId, shotName) {
        await page.waitForSelector('.asset-overlay.open', { timeout: 15000 });
        await page.waitForSelector(`.asset-overlay.open .asset-item[data-id="${assetId}"]`, { timeout: 15000 });
        await page.click(`.asset-overlay.open .asset-item[data-id="${assetId}"]`);
        await page.waitForSelector('.asset-overlay.open .asset-pick', { timeout: 15000 });
        if (shotName) await shot(page, shotName);
        await page.click('.asset-overlay.open .asset-pick');
        await page.waitForFunction(() => !document.querySelector('.asset-overlay.open'), null, { timeout: 10000 });
    }
    const assetRow = async id => { const r = await api('GET', `/api/assets/${id}`); return { http: r.status, status: r.data && r.data.status, references: r.data ? r.data.references : null }; };
    const at = (st, kind) => st.ids.indexOf(ID[kind]);
    const kindsOf = st => st.ids.map(id => Object.keys(ID).find(k => ID[k] === id) || id);

    const ID = {};
    let historyBefore = null;
    const { page } = await newPage(browser, VIEW, baseCine);

    // ---- 1. the saved cinematic lesson opens --------------------------------------------------------------------------------
    await section('1. a saved cinematic lesson opens and plays', async () => {
        phase = 'open';
        historyBefore = ((await api('GET', '/get-history')).data.history || []).length;
        await startLesson(page, pid);
        const facts = await page.evaluate(() => ({ n: slides.length, plans: slides.filter(s => s.cinematic_plan).length, playing: !!ttsState.isPlaying,
            templates: slides.map(s => s.cinematic_plan && s.cinematic_plan.template),
            presenters: slides.map(s => { const p = s.cinematic_plan && s.cinematic_plan.presenter; return p ? `${p.presenter_id}${p.shown ? '' : ' (hidden)'}` : null; }),
            diagram: ((slides[3].visual_plan || {}).side || {}).asset_id || null, style: document.body.getAttribute('data-cine-style') }));
        // paused from the player bar (the editor opens on a still stage)
        await playerBar(page, '#tts-play-btn');
        await page.waitForFunction(() => !ttsState.isPlaying, null, { timeout: 5000 });
        await shot(page, 'lesson-open.png');
        record.open = facts;
        check('1. the saved 9-scene cinematic lesson (Academic, drawn Aadhi Teacher, the diagram from the library) opens and plays; paused from the player bar',
            facts.n === 9 && facts.plans === 9 && facts.playing && facts.diagram === leafAsset && facts.presenters.some(p => /^aadhi-teacher$/.test(p || '')) && facts.style === 'academic',
            `${facts.n} scenes, ${facts.plans} plans, templates ${facts.templates.join(',')}; presenters ${facts.presenters.join(',')}; diagram asset ${facts.diagram === leafAsset ? 'the library leaf' : facts.diagram}; style ${facts.style}; was playing ${facts.playing}`);
    });

    // ---- 2-3. the editor opens; every scene listed ------------------------------------------------------------------------------
    await section('2. the 🎬 button opens the editor', async () => {
        phase = 'editing';
        await playerBar(page, '#editor-btn');
        await page.waitForFunction(() => typeof lessonEditorSession !== 'undefined' && lessonEditorSession && lessonEditorSession.workspace.isOpen, null, { timeout: 30000 });
        await page.waitForSelector('.editor-root .editor-scene', { timeout: 10000 });
        await settle(page);
        const st = await state(page);
        const u = await ui(page);
        KINDS.forEach((k, i) => { ID[k] = st.ids[i]; });
        record.initialIds = st.ids;
        await shot(page, 'editor-open.png');
        const unique = new Set(st.ids).size === st.ids.length;
        check('2. the 🎬 button opens the editor (body.editor-active, the player bar hidden, the lesson saved in place with a scene id per scene and its generated order)',
            u.root && u.active && u.barDisplay === 'none' && st.ids.every(id => ID_RE.test(id || '')) && unique && st.save === 'saved' && u.save === 'saved'
            && st.editor && same(st.editor.generated_order, st.ids) && st.editor.version === 1,
            `root ${u.root}, editor-active ${u.active}, player bar display ${u.barDisplay}; ids ${st.ids.join(' ')} (${unique ? 'unique' : 'DUPLICATED'}); save ${st.save} / "${u.saveText}"; `
            + `payload.editor ${JSON.stringify(st.editor)}; title "${await page.textContent('.editor-title').catch(() => '')}"; editor-open.png`);
        check('3. the scene list shows every scene in order (titles, the count)',
            u.list.length === 9 && same(u.list.map(x => x.title), KINDS.map(k => TITLE[k])) && same(u.list.map(x => x.id), st.ids) && u.count === '9'
            && u.blocks.length === 9,
            `${u.list.length} items: ${u.list.map(x => `${x.index + 1}. ${x.title} [${x.meta}]`).join(' | ')}; count "${u.count}"; ${u.blocks.length} timeline blocks`);
    });

    // ---- 4. a scene selected in the list ----------------------------------------------------------------------------------------
    await section('4. a list click selects the scene: inspector and stage', async () => {
        const before = await page.evaluate(stageFacts);
        await page.click(sceneSel(ID.diagram));
        await page.waitForFunction(i => lessonEditorSession.workspace.selectedId === i && slides[currentSlide].scene_id === i, ID.diagram, { timeout: 10000 });
        await sleep(2000);
        const u = await ui(page);
        const stage = await page.evaluate(stageFacts);
        record.selectWhilePaused = await page.evaluate(() => ({ playing: !!ttsState.isPlaying, button: (document.querySelector('.editor-timeline [data-action="play"]') || {}).textContent }));
        await shot(page, 'editor-selected-diagram.png');
        if (record.selectWhilePaused.playing) note(`a list click on a scene of the paused lesson started playback (editor button "${record.selectWhilePaused.button}"): editor-selected-diagram.png`);
        await ensurePaused(page, 'a list click on the diagram (check 4)');
        const item = u.list.find(x => x.id === ID.diagram);
        const shows = [stage.title, stage.cineTitle, stage.board].some(t => (t || '').includes(TITLE.diagram) || (t || '').includes('Palisade'));
        check('4. a click on scene 4 in the list: the inspector shows "Scene 4 of 9 — Inside the leaf" and the stage renders that scene (its board and its library diagram)',
            u.inspectorHead === 'Scene 4 of 9' && u.factTitle === TITLE.diagram && item && item.current === 'true' && stage.current === 3 && stage.renderId > before.renderId
            && shows && stage.img && stage.img.includes(leafAsset),
            `inspector "${u.inspectorHead}" / "${u.factTitle}"; list item current ${item && item.current}; stage scene ${stage.current + 1} (render ${before.renderId} -> ${stage.renderId}), `
            + `title "${stage.title || stage.cineTitle}", board "${stage.board.slice(0, 60)}", picture ${stage.img ? (stage.img.includes(leafAsset) ? 'the library leaf' : stage.img.slice(-60)) : 'none'}; editor-selected-diagram.png`);
    });

    // ---- 5. play / pause -----------------------------------------------------------------------------------------------------
    await section('5. play / pause from the editor', async () => {
        await ensurePaused(page, 'before play / pause (check 5)');
        const u0 = await ui(page);
        await page.click('.editor-timeline [data-action="play"]');
        await page.waitForFunction(() => ttsState.isPlaying, null, { timeout: 5000 });
        await sleep(2600);
        const mid = await ui(page);
        const midState = await page.evaluate(() => ({ playing: !!ttsState.isPlaying, audio: !!(window.currentAudio && !window.currentAudio.paused), current: currentSlide }));
        await page.click('.editor-timeline [data-action="play"]');
        await page.waitForFunction(() => !ttsState.isPlaying, null, { timeout: 5000 });
        await sleep(400);
        const u1 = await ui(page);
        const t0 = parseTimeLabel(u0.time).cur;
        const tMid = parseTimeLabel(mid.time).cur;
        check('5. ▶ Play plays the lesson from the selected scene (the button turns to ❚❚ Pause, the playhead time runs); pressing it again pauses',
            /Play/.test(u0.play) && /Pause/.test(mid.play) && midState.playing && /Play/.test(u1.play) && tMid !== null && t0 !== null && tMid > t0,
            `before "${u0.play}" ${u0.time}; playing "${mid.play}" ${mid.time} (narration audio playing ${midState.audio}, scene ${midState.current + 1}); after "${u1.play}" ${u1.time}`);
    });

    // ---- 6. seek on the timeline ruler -------------------------------------------------------------------------------------------
    await section('6. seek on the timeline ruler (snapping to a scene start)', async () => {
        const st = await state(page);
        const u = await ui(page);
        const ruler = await page.locator('.editor-tl-row[data-track="ruler"] .editor-tl-lane').boundingBox();
        const blk = u.blocks.find(b => b.id === ID.process);
        const seeks = [];
        for (const [frac, want] of [[0.3, 'process'], [0.8, 'diagram']]) {
            const x = blk.x + blk.w * frac;
            await page.mouse.click(x, ruler.y + ruler.height / 2);
            await page.waitForFunction(i => lessonEditorSession.workspace.selectedId === i, ID[want], { timeout: 5000 }).catch(() => {});
            // read at once: the snapped time and playhead (before any playback could move them on)
            const s = await state(page);
            const v = await ui(page);
            await ensurePaused(page, `a seek on the ruler to ${want} (check 6)`);
            const startAt = s.starts[at(s, want)];
            const wantBlock = v.blocks.find(b => b.id === ID[want]);
            seeks.push({ frac, want, selected: kindsOf(s)[s.ids.indexOf(s.selected)], current: s.current, wantIndex: at(s, want), time: v.time,
                expected: clock(startAt), playheadX: v.playheadX, blockX: wantBlock ? wantBlock.x : null });
        }
        record.seeks = seeks;
        await shot(page, 'editor-seek.png');
        check('6. a press on the time ruler 30% into scene 3 seeks to scene 3\'s start; 80% into it snaps to scene 4\'s start (selection, stage and playhead follow)',
            seeks.every(x => x.selected === x.want && x.current === x.wantIndex && parseTimeLabel(x.time).cur === parseClock(x.expected)
                && x.playheadX !== null && x.blockX !== null && Math.abs(x.playheadX - x.blockX) <= 4) && st.ids.length === 9,
            seeks.map(x => `${Math.round(x.frac * 100)}% into "${TITLE.process}" -> selected ${x.selected}, stage scene ${x.current + 1}, time ${x.time} (scene start ${x.expected}), playhead x ${x.playheadX && x.playheadX.toFixed(0)} vs block x ${x.blockX && x.blockX.toFixed(0)}`).join(' | '));
    });

    // ---- 7. reorder: a timeline drag and Alt+Arrow; 14/15 undo / redo of the move -------------------------------------------------
    const undoRedo = { undo: [], redo: [] };
    await section('7. reorder by dragging a timeline block and by Alt+←', async () => {
        await ensurePaused(page, 'before the drag (check 7)');
        const before = await state(page);
        const titleOf = Object.fromEntries(before.ids.map((id, i) => [id, before.titles[i]]));
        const u = await ui(page);
        const from = u.blocks.find(b => b.id === ID.formula);
        const to = u.blocks.find(b => b.id === ID.code);
        const y = from.y + from.h / 2;
        const x0 = from.x + from.w / 2;
        const x1 = to.x + to.w / 2 + 12;
        await page.mouse.move(x0, y);
        await page.mouse.down();
        for (let k = 1; k <= 14; k++) { await page.mouse.move(x0 + (x1 - x0) * k / 14, y); await sleep(25); }
        const during = await page.evaluate(() => ({ drop: !!document.querySelector('.editor-tl-drop:not([hidden])'), held: lessonEditorSession.autosave.held }));
        await page.mouse.up();
        await page.waitForFunction(([f, c]) => { const ids = slides.map(s => s.scene_id); return ids.indexOf(f) === ids.indexOf(c) + 1; }, [ID.formula, ID.code], { timeout: 10000 }).catch(() => {});
        await settle(page);
        const afterDrag = await state(page);
        const uDrag = await ui(page);
        await shot(page, 'editor-after-drag.png');
        // the first click on a scene after the drag (a user picks the next scene to change)
        const suppress = await page.evaluate(() => lessonEditorSession.workspace.suppressClick);
        await page.click(sceneSel(ID.explanation));
        const firstClick = await page.waitForFunction(i => lessonEditorSession.workspace.selectedId === i, ID.explanation, { timeout: 4000 }).then(() => true).catch(() => false);
        record.afterDragClick = { selected: firstClick, suppressBefore: suppress };
        check('7b. the first click on a scene after a timeline drag selects it (no click is swallowed)',
            firstClick, `first click on "${TITLE.explanation}" after the drag: ${firstClick ? 'selected' : 'IGNORED'} (workspace.suppressClick before the click: ${suppress}); editor-after-drag.png`);
        // Alt+← on the explanation (selected in the list)
        await selectScene(page, ID.explanation);
        await page.keyboard.press('Alt+ArrowLeft');
        await page.waitForFunction(i => slides.findIndex(s => s.scene_id === i) === 6, ID.explanation, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const afterAlt = await state(page);
        const uAlt = await ui(page);
        const want1 = ['intro', 'definition', 'process', 'diagram', 'code', 'formula', 'comparison', 'explanation', 'summary'];
        const sameIds = s => s.ids.length === 9 && new Set(s.ids).size === 9 && s.ids.every(id => before.ids.includes(id) && titleOf[id] === s.titles[s.ids.indexOf(id)]);
        check('7. dragging the formula block past the code block moves it after the code (one move, saved after the release); Alt+← moves the selected explanation one place earlier; the scene ids are kept',
            same(kindsOf(afterDrag), want1) && same(kindsOf(afterAlt), FINAL) && sameIds(afterDrag) && sameIds(afterAlt) && during.drop && during.held > 0
            && same(uAlt.list.map(x => x.id), afterAlt.ids) && afterAlt.save === 'saved'
            && [ID.formula, ID.code].some(id => uDrag.list.find(x => x.id === id).marks.some(m => m.mark === 'moved')),
            `after the drag ${kindsOf(afterDrag).join(',')} (drop marker ${during.drop}, autosave held ${during.held}); after Alt+← ${kindsOf(afterAlt).join(',')}; ids kept ${sameIds(afterAlt)}; `
            + `"Moved" marks after the drag: ${uDrag.list.filter(x => x.marks.some(m => m.mark === 'moved')).map(x => x.title).join(', ') || 'none'} (of two swapped neighbours either one is "the moved one"); save ${afterAlt.save}; editor-after-drag.png`);
        // undo with the toolbar button, redo with Ctrl+Y
        const label = (await ui(page)).undo;
        await page.click('.editor-top [data-action="undo"]');
        await page.waitForFunction(i => slides.findIndex(s => s.scene_id === i) === 7, ID.explanation, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const undone = await state(page);
        undoRedo.undo.push({ how: 'the ↶ Undo button', label: label && label.label, ok: same(kindsOf(undone), want1), order: kindsOf(undone).join(',') });
        await selectScene(page, ID.intro); // the keyboard focus on the scene list (not in a field)
        await page.keyboard.press('Control+y');
        await page.waitForFunction(i => slides.findIndex(s => s.scene_id === i) === 6, ID.explanation, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const redone = await state(page);
        undoRedo.redo.push({ how: 'Ctrl+Y', ok: same(kindsOf(redone), FINAL), order: kindsOf(redone).join(',') });
    });

    // ---- 8. a minimum duration; 14/15 undo (Ctrl+Z) and redo (the button) ---------------------------------------------------------
    await section('8. a minimum duration lengthens the timeline', async () => {
        await selectScene(page, ID.code);
        const s0 = await state(page);
        const u0 = await ui(page);
        const i = at(s0, 'code');
        const play0 = s0.play[i];
        const w0 = u0.blocks.find(b => b.id === ID.code).w;
        const input = page.locator('#editor-field-min-seconds');
        await input.fill(String(HOLD));
        await input.press('Enter');
        const applied = await page.waitForFunction(id => ((slides.find(s => s.scene_id === id) || {}).edit || {}).min_seconds === 14, ID.code, { timeout: 4000 }).then(() => true).catch(() => false);
        if (!applied) { await page.click('.editor-scenes .editor-panel-head'); note('the minimum was applied when the field lost focus (Enter did not commit it)'); }
        await settle(page);
        const s1 = await state(page);
        const u1 = await ui(page);
        const w1 = u1.blocks.find(b => b.id === ID.code).w;
        await shot(page, 'editor-duration.png');
        const delta = s1.total - s0.total;
        const want = HOLD - play0;
        const head = await page.textContent('.editor-inspector [data-field-box="min_seconds"]').catch(() => '');
        check(`8. a minimum duration of ${HOLD} s on the code scene (inspector → Timing): the scene and the timeline get ${want.toFixed(2)} s longer, the block wider, the field marked edited`,
            s1.edits[i] && s1.edits[i].min_seconds === HOLD && Math.abs(delta - want) < 0.06 && s1.play[i] === HOLD && w1 > w0 * 1.5
            && parseTimeLabel(u1.time).total > parseTimeLabel(u0.time).total && /Edited/.test(head) && s1.save === 'saved',
            `play ${play0} s -> ${s1.play[i]} s; timeline ${s0.total} s -> ${s1.total} s (+${delta.toFixed(2)}, expected +${want.toFixed(2)}); label ${u0.time} -> ${u1.time}; block ${w0.toFixed(0)} -> ${w1.toFixed(0)} px; `
            + `field "${(head || '').replace(/\s+/g, ' ').slice(0, 90)}"; edit ${JSON.stringify(s1.edits[i])}; editor-duration.png`);
        // Ctrl+Z (the focus back on the scene list first: shortcuts never fire inside a field)
        await selectScene(page, ID.code);
        await page.keyboard.press('Control+z');
        await page.waitForFunction(id => !((slides.find(s => s.scene_id === id) || {}).edit || {}).min_seconds, ID.code, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s2 = await state(page);
        undoRedo.undo.push({ how: 'Ctrl+Z', ok: !(s2.edits[i] && s2.edits[i].min_seconds) && Math.abs(s2.total - s0.total) < 0.01, order: `code min ${s2.edits[i] ? s2.edits[i].min_seconds : '-'}, total ${s2.total} s` });
        const label = (await ui(page)).redo;
        await page.click('.editor-top [data-action="redo"]');
        await page.waitForFunction(id => ((slides.find(s => s.scene_id === id) || {}).edit || {}).min_seconds === 14, ID.code, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s3 = await state(page);
        undoRedo.redo.push({ how: 'the ↷ Redo button', label: label && label.label, ok: s3.edits[i] && s3.edits[i].min_seconds === HOLD && Math.abs(s3.total - s1.total) < 0.01,
            order: `code min ${s3.edits[i] ? s3.edits[i].min_seconds : '-'}, total ${s3.total} s` });
    });
    check('14. undo: Ctrl+Z undoes the duration, the ↶ Undo button undoes the move (each one step, saved)',
        undoRedo.undo.length === 2 && undoRedo.undo.every(x => x.ok), undoRedo.undo.map(x => `${x.how}${x.label ? ` ("${x.label}")` : ''}: ${x.ok ? 'undone' : 'NOT UNDONE'} (${x.order})`).join(' | '));
    check('15. redo: Ctrl+Y redoes the move, the ↷ Redo button redoes the duration',
        undoRedo.redo.length === 2 && undoRedo.redo.every(x => x.ok), undoRedo.redo.map(x => `${x.how}${x.label ? ` ("${x.label}")` : ''}: ${x.ok ? 'redone' : 'NOT REDONE'} (${x.order})`).join(' | '));

    // ---- 9, 10, 12, 13. composition choices through the inspector ----------------------------------------------------------------
    const changes = {};
    await section('9. camera through the inspector', async () => {
        await selectScene(page, ID.code);
        await openSection(page, 'camera');
        const before = (await state(page)).plans[at(await state(page), 'code')].camera;
        const r = await compositionChange(page, ID.code, 'camera', 'static', () => page.selectOption('#editor-field-camera', 'static'));
        const s = await state(page);
        const i = at(s, 'code');
        changes.camera = { before, ...r, comp: s.comps[i], plan: s.plans[i].camera };
        const head = await page.textContent('.editor-inspector [data-field-box="camera"]').catch(() => '');
        check('9. the camera of the code scene set to "Still" in the inspector: POST /api/cinematic/review (change, {camera: static}), the scene\'s review "changed", its plan still',
            r.status === 200 && r.sent.action === 'change' && r.sent.scene_index === i && same(r.sent.overrides, { camera: 'static' }) && s.comps[i] && s.comps[i].status === 'changed'
            && s.comps[i].overrides.camera === 'static' && s.plans[i].camera === 'static' && /Edited/.test(head),
            `plan camera ${before} -> ${s.plans[i].camera}; POST ${r.status} ${JSON.stringify(r.sent)}; review ${JSON.stringify(s.comps[i])}; field "${(head || '').replace(/\s+/g, ' ').slice(0, 60)}"`);
    });
    await section('10. transition through the inspector', async () => {
        await selectScene(page, ID.explanation);
        await openSection(page, 'transition');
        const s0 = await state(page);
        const before = s0.plans[at(s0, 'explanation')].transition;
        const value = ['slide', 'crossfade', 'zoom', 'wipe'].find(v => v !== before);
        const r = await compositionChange(page, ID.explanation, 'transition', value, () => page.selectOption('#editor-field-transition', value));
        const s = await state(page);
        const i = at(s, 'explanation');
        changes.transition = { before, value, ...r, comp: s.comps[i], plan: s.plans[i].transition };
        const u = await ui(page);
        check(`10. the transition into the explanation set to "${value}" in the inspector: POST /api/cinematic/review (change), the review "changed", the plan's transition and the timeline follow`,
            r.status === 200 && r.sent.action === 'change' && r.sent.scene_index === i && same(r.sent.overrides, { transition: value }) && s.comps[i] && s.comps[i].status === 'changed'
            && s.comps[i].overrides.transition === value && s.plans[i].transition === value
            && (await page.evaluate(id => (document.querySelector(`.editor-tl-transition[data-scene-id="${id}"]`) || {}).getAttribute ? document.querySelector(`.editor-tl-transition[data-scene-id="${id}"]`).getAttribute('data-transition') : null, ID.explanation)) === value,
            `plan transition ${before} -> ${s.plans[i].transition}; POST ${r.status} ${JSON.stringify(r.sent)}; review ${JSON.stringify(s.comps[i])}; save ${u.save}`);
    });
    await section('12. presenter side through the inspector', async () => {
        await openSection(page, 'presenter');
        const s0 = await state(page);
        const i0 = at(s0, 'explanation');
        const before = s0.plans[i0].side;
        const value = before === 'left' ? 'right' : 'left';
        const r = await compositionChange(page, ID.explanation, 'presenter_position', value,
            () => page.click(`.editor-inspector [data-action="override"][data-key="presenter_position"][data-value="${value}"]`));
        const s = await state(page);
        const i = at(s, 'explanation');
        changes.presenter = { before, value, ...r, comp: s.comps[i], plan: s.plans[i].side };
        const pressed = await page.evaluate(v => { const b = document.querySelector(`.editor-inspector [data-key="presenter_position"][data-value="${v}"]`); return b ? b.getAttribute('aria-pressed') : null; }, value);
        await sleep(1200);
        await shot(page, 'editor-presenter-moved.png');
        check(`12. the presenter of the explanation moved ${value} (inspector → Presenter): POST /api/cinematic/review, the review "changed" keeps the transition too, the plan's presenter on the ${value}`,
            r.status === 200 && same(r.sent.overrides, { presenter_position: value }) && s.comps[i] && s.comps[i].status === 'changed' && s.comps[i].overrides.presenter_position === value
            && s.comps[i].overrides.transition === changes.transition.value && s.plans[i].side === value && pressed === 'true',
            `presenter ${before} -> ${s.plans[i].side} (shown ${s.plans[i].shown}); POST ${r.status} ${JSON.stringify(r.sent)}; review ${JSON.stringify(s.comps[i])}; button pressed ${pressed}; editor-presenter-moved.png`);
    });
    await section('13. scene accent through the inspector', async () => {
        await selectScene(page, ID.code);
        await openSection(page, 'style');
        const r = await compositionChange(page, ID.code, 'style_accent', 'teal', () => page.selectOption('#editor-field-style_accent', 'teal'));
        await sleep(1500);
        const s = await state(page);
        const i = at(s, 'code');
        const stage = await page.evaluate(stageFacts);
        // the inspector sections of the edited scene (camera and style open), then the stage of another scene
        await page.locator('.editor-inspector .editor-section[data-section="camera"]').scrollIntoViewIfNeeded().catch(() => {});
        await page.locator('.editor-inspector').screenshot({ path: path.join(OUT, 'inspector-sections.png') }).catch(() => {});
        await page.locator('.editor-inspector .editor-section[data-section="style"]').scrollIntoViewIfNeeded().catch(() => {});
        await page.locator('.editor-inspector').screenshot({ path: path.join(OUT, 'inspector-style.png') }).catch(() => {});
        await shot(page, 'editor-code-edited.png');
        await selectScene(page, ID.definition);
        await sleep(1500);
        const other = await page.evaluate(stageFacts);
        changes.accent = { ...r, comp: s.comps[i], plan: s.plans[i].accent, stage: stage.accent, other: other.accent };
        check('13. the code scene\'s accent set to teal (inspector → Style): POST /api/cinematic/review {style_accent: teal}, kept beside the camera; the stage shows styles.py\'s teal on that scene only',
            r.status === 200 && same(r.sent.overrides, { style_accent: 'teal' }) && s.comps[i] && s.comps[i].overrides.style_accent === 'teal' && s.comps[i].overrides.camera === 'static'
            && norm(s.plans[i].accent) === norm(EXPECT.teal) && stage.id === ID.code && norm(stage.accent) === norm(EXPECT.teal) && norm(other.accent) === norm(EXPECT.accent),
            `POST ${r.status} ${JSON.stringify(r.sent)}; review ${JSON.stringify(s.comps[i])}; plan accent ${s.plans[i].accent} (styles.py teal ${EXPECT.teal}); on stage ${stage.accent} (scene ${stage.current + 1}); `
            + `the definition scene ${other.accent} (Academic ${EXPECT.accent}); inspector-sections.png, inspector-style.png`);
    });

    // ---- 11. the visual from the Asset Library ------------------------------------------------------------------------------------
    await section('11. the visual chosen from the Asset Library', async () => {
        await selectScene(page, ID.diagram);
        await openSection(page, 'visual');
        const s0 = await state(page);
        const posted = page.waitForResponse(r => r.url().includes('/api/visuals/review') && r.request().method() === 'POST', { timeout: 30000 });
        await page.click('.editor-inspector [data-action="choose-visual"][data-slot="side"]');
        await pickFromLibrary(page, photoAsset, 'asset-picker.png');
        const r = await posted;
        let sent = {};
        try { sent = JSON.parse(r.request().postData() || '{}'); } catch (e) { /* unreadable */ }
        await page.waitForFunction(([id, a]) => { const s = slides.find(x => x.scene_id === id); return s && s.visual_plan && s.visual_plan.side && s.visual_plan.side.asset_id === a; }, [ID.diagram, photoAsset], { timeout: 20000 }).catch(() => {});
        await settle(page, 1500);
        await page.waitForFunction(a => { const img = document.querySelector('.dynamic-side-zone .side-panel-view.active img'); return img && img.src.includes(a); }, photoAsset, { timeout: 10000 }).catch(() => {});
        const s = await state(page);
        const i = at(s, 'diagram');
        const stage = await page.evaluate(stageFacts);
        const review = await page.evaluate(id => { const sc = slides.find(x => x.scene_id === id); return sc && sc.visual_review ? JSON.parse(JSON.stringify(sc.visual_review.side || null)) : null; }, ID.diagram);
        await shot(page, 'editor-visual-changed.png');
        changes.visual = { status: r.status(), action: sent.action, asset: sent.asset_id, review };
        check('11. "Choose from library" on the diagram scene → the Asset Library picker → the photosynthesis diagram: POST /api/visuals/review (choose), the scene\'s visual and the stage picture change',
            r.status() === 200 && sent.action === 'choose' && sent.asset_id === photoAsset && sent.slot === 'side' && s0.sideAsset[i] === leafAsset && s.sideAsset[i] === photoAsset
            && review && review.status && stage.img && stage.img.includes(photoAsset),
            `POST /api/visuals/review ${r.status()} ${JSON.stringify({ action: sent.action, slot: sent.slot, asset_id: sent.asset_id === photoAsset ? 'the photosynthesis diagram' : sent.asset_id })}; `
            + `scene visual ${s0.sideAsset[i] === leafAsset ? 'leaf' : s0.sideAsset[i]} -> ${s.sideAsset[i] === photoAsset ? 'photosynthesis diagram' : s.sideAsset[i]}; review ${JSON.stringify(review && { status: review.status })}; `
            + `stage picture ${stage.img ? (stage.img.includes(photoAsset) ? 'the photosynthesis diagram' : stage.img.slice(-50)) : 'none'}; asset-picker.png, editor-visual-changed.png`);
    });

    // ---- captions off on the formula scene ---------------------------------------------------------------------------------------
    await section('captions off on a scene', async () => {
        await selectScene(page, ID.formula);
        await openSection(page, 'captions');
        await page.click('.editor-inspector [data-action="scene-captions"]');
        await page.waitForFunction(id => ((slides.find(s => s.scene_id === id) || {}).edit || {}).captions === 'off', ID.formula, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s = await state(page);
        const u = await ui(page);
        const lane = u.captionLane.find(b => b.id === ID.formula);
        check('captions: "Hide captions in this scene" on the formula scene sets edit.captions "off" (the timeline\'s caption lane shows "Captions off" there only)',
            s.edits[at(s, 'formula')] && s.edits[at(s, 'formula')].captions === 'off' && lane && lane.state === 'off' && u.captionLane.filter(b => b.state === 'off').length === 1,
            `edit ${JSON.stringify(s.edits[at(s, 'formula')])}; caption lane ${u.captionLane.map(b => `${kindsOf(s)[s.ids.indexOf(b.id)]}:${b.state}`).join(' ')}`);
    });

    // ---- a stale save: another writer changed the lesson meanwhile (409 -> reload, replay, saved) ---------------------------------
    await section('stale save: 409, reload, replay, saved', async () => {
        await settle(page);
        phase = 'stale';
        const from = answers.length;
        const srv = (await api('GET', `/api/editor/${pid}`)).data;
        const scenes = srv.scenes;
        const summary = scenes.find(s => s.scene_id === ID.summary);
        summary.subtitle = OTHER_SUBTITLE;
        const other = await api('PUT', `/api/editor/${pid}`, { expected_revision: srv.revision, scenes, editor: srv.editor });
        // in the editor (its copy is now stale): the comparison scene hidden
        await selectScene(page, ID.comparison);
        await page.click('.editor-inspector [data-action="toggle-hidden"]');
        await page.waitForFunction(id => ((slides.find(s => s.scene_id === id) || {}).edit || {}).hidden === true, ID.comparison, { timeout: 10000 }).catch(() => {});
        await page.waitForFunction(([id, sub]) => { const s = lessonEditorSession; const sc = slides.find(x => x.scene_id === id);
            return s.autosave.state === 'saved' && !s.model.dirty && sc && sc.subtitle === sub; }, [ID.summary, OTHER_SUBTITLE], { timeout: 30000 }).catch(() => {});
        await settle(page);
        const s = await state(page);
        const u = await ui(page);
        const seen = answers.slice(from).filter(a => /\/api\/editor\//.test(a.url));
        const server = (await api('GET', `/api/projects/${pid}`)).data || {};
        const sv = server.scenes || [];
        const svComparison = sv.find(x => x.scene_id === ID.comparison) || {};
        const svSummary = sv.find(x => x.scene_id === ID.summary) || {};
        const svCode = sv.find(x => x.scene_id === ID.code) || {};
        const puts = seen.filter(a => a.method === 'PUT').map(a => a.status);
        const replay = await page.evaluate(() => JSON.parse(JSON.stringify(lessonEditorSession.autosave.lastReplay || null)));
        await shot(page, 'editor-after-stale-save.png');
        record.stale = { other: other.status, seen, replay, saveText: u.saveText };
        check('stale save: another writer changes the lesson (PUT /api/editor from "another tab"); the editor\'s next save is refused (409), it reloads, replays the hide by scene id and saves: both changes kept, "Saved"',
            other.status === 200 && puts.includes(409) && puts[puts.length - 1] === 200 && puts.indexOf(409) < puts.lastIndexOf(200)
            && seen.some(a => a.method === 'GET' && a.status === 200 && seen.indexOf(a) > seen.findIndex(x => x.status === 409))
            && s.save === 'saved' && u.save === 'saved' && s.subtitles[at(s, 'summary')] === OTHER_SUBTITLE && s.edits[at(s, 'comparison')] && s.edits[at(s, 'comparison')].hidden === true
            && svComparison.edit && svComparison.edit.hidden === true && svSummary.subtitle === OTHER_SUBTITLE && svCode.edit && svCode.edit.min_seconds === HOLD
            && same(sv.map(x => x.scene_id), s.ids) && replay && replay.applied >= 1 && replay.conflicts.length === 0,
            `other writer PUT ${other.status}; editor requests ${seen.map(a => `${a.method} ${a.status}`).join(', ')}; replay ${JSON.stringify(replay)}; state "${u.saveText}"; `
            + `page: summary subtitle "${s.subtitles[at(s, 'summary')]}", comparison hidden ${s.edits[at(s, 'comparison')] && s.edits[at(s, 'comparison')].hidden}; `
            + `server: summary subtitle "${svSummary.subtitle}", comparison hidden ${svComparison.edit && svComparison.edit.hidden}, code min ${svCode.edit && svCode.edit.min_seconds}, order ${same(sv.map(x => x.scene_id), s.ids) ? 'the editor\'s' : 'DIFFERENT'}`);
        phase = 'editing';
        const v = await ui(page);
        const item = v.list.find(x => x.id === ID.comparison);
        const blk = v.blocks.find(b => b.id === ID.comparison);
        check('hide: the hidden comparison scene stays in the list, dimmed and marked "Hidden"; its timeline block collapses; the timeline loses its seconds',
            item && item.hidden && item.marks.some(m => m.mark === 'hidden') && blk && blk.hidden && blk.w < 20 && s.play.length === 9,
            `list item hidden ${item && item.hidden} marks ${item && item.marks.map(m => m.text).join(' ')}; block ${blk && blk.w.toFixed(0)} px hidden ${blk && blk.hidden}; timeline ${s.total} s`);
    });

    // ---- duplicate (new id, no approvals copied), then delete it with a confirmation ---------------------------------------------
    await section('duplicate and delete a scene', async () => {
        await selectScene(page, ID.diagram);
        const s0 = await state(page);
        await page.click('.editor-inspector [data-action="duplicate"]');
        await page.waitForFunction(() => slides.length === 10, null, { timeout: 10000 });
        const right = await page.evaluate(() => lessonEditorSession.workspace.selectedId);
        await settle(page, 2200, 'after "Duplicate" (the stage redrawn)');
        const s1 = await state(page);
        const u1 = await ui(page);
        const src = at(s1, 'diagram');
        const copyId = s1.ids[src + 1];
        const copyItem = u1.list.find(x => x.id === copyId) || { marks: [] };
        const srcItem = u1.list.find(x => x.id === ID.diagram) || { marks: [] };
        const server1 = ((await api('GET', `/api/projects/${pid}`)).data || {}).scenes || [];
        newSelection.push({ what: 'the duplicate', atOnce: right === copyId, after: s1.selected === copyId, now: kindsOf(s1)[s1.ids.indexOf(s1.selected)] || s1.selected,
            inspector: u1.inspectorHead });
        check('duplicate: "⧉ Duplicate" puts a copy right after the scene with a NEW scene id (origin duplicated, from the original), none of its review decisions, saved',
            ID_RE.test(copyId || '') && !s0.ids.includes(copyId) && s1.edits[src + 1] && s1.edits[src + 1].origin === 'duplicated' && s1.edits[src + 1].from === ID.diagram
            && s1.reviewSlots[src].length > 0 && s1.reviewSlots[src + 1].length === 0 && s1.titles[src + 1] === TITLE.diagram
            && copyItem.marks.some(m => m.mark === 'new') && !copyItem.marks.some(m => m.approval === 'approved' || m.approval === 'changed')
            && server1.some(x => x.scene_id === copyId) && s1.save === 'saved',
            `copy ${copyId} at ${src + 2} (edit ${JSON.stringify(s1.edits[src + 1])}); reviews: original [${s1.reviewSlots[src].join(',')}], copy [${s1.reviewSlots[src + 1].join(',')}]; `
            + `list marks: original ${srcItem.marks.map(m => m.text).join(' ')}, copy ${copyItem.marks.map(m => m.text).join(' ')}; selected right after ${right === copyId ? 'the copy' : right}, after the redraw ${s1.selected === copyId ? 'the copy' : s1.selected}; saved on the server ${server1.some(x => x.scene_id === copyId)}`);
        // delete the copy (chosen in the list first): the in-editor confirmation (Cancel keeps it; Delete removes it)
        await selectScene(page, copyId, 'the duplicate');
        await page.click('.editor-inspector [data-action="delete"]');
        await page.waitForSelector('.editor-confirm:not([hidden]) [data-action="confirm-ok"]', { timeout: 5000 });
        const question = (await ui(page)).confirm;
        await shot(page, 'editor-confirm-delete.png');
        await page.click('.editor-confirm [data-action="confirm-cancel"]');
        await sleep(500);
        const kept = (await state(page)).ids.includes(copyId);
        await page.click('.editor-inspector [data-action="delete"]');
        await page.click('.editor-confirm:not([hidden]) [data-action="confirm-ok"]');
        await page.waitForFunction(id => !slides.some(s => s.scene_id === id), copyId, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s2 = await state(page);
        const rows = { leaf: await assetRow(leafAsset), photo: await assetRow(photoAsset) };
        const server2 = ((await api('GET', `/api/projects/${pid}`)).data || {}).scenes || [];
        check('delete: "🗑 Delete" asks first (in the editor; Cancel keeps the scene), then removes it from the lesson and the saved lesson; the library assets stay',
            /Delete this scene\?/.test(question || '') && kept && !s2.ids.includes(copyId) && same(kindsOf(s2), FINAL) && !server2.some(x => x.scene_id === copyId)
            && Object.values(rows).every(r => r.http === 200 && r.status === 'ready'),
            `question "${(question || '').slice(0, 120)}"; after Cancel still there ${kept}; after Delete ${kindsOf(s2).join(',')}; server ${server2.length} scenes; assets ${Object.entries(rows).map(([k, r]) => `${k} HTTP ${r.http} ${r.status} (${r.references} uses)`).join(', ')}; editor-confirm-delete.png`);
    });

    // ---- insert a library picture (then delete it with the Delete key: its asset stays), insert a blank scene (then Ctrl+Z) ------
    await section('insert a picture scene and a blank scene', async () => {
        await selectScene(page, ID.definition);
        await page.click('.editor-inspector [data-action="insert-picture"]');
        await pickFromLibrary(page, chloroAsset, 'asset-picker-insert.png');
        await page.waitForFunction(() => slides.length === 10, null, { timeout: 10000 }).catch(() => {});
        const right = await page.evaluate(() => lessonEditorSession.workspace.selectedId);
        await settle(page, 2200, 'after inserting a picture scene (the stage redrawn)');
        const s1 = await state(page);
        const i = at(s1, 'definition') + 1;
        const picId = s1.ids[i];
        const used = await assetRow(chloroAsset);
        const server1 = ((await api('GET', `/api/projects/${pid}`)).data || {}).scenes || [];
        newSelection.push({ what: 'the inserted picture scene', atOnce: right === picId, after: s1.selected === picId, now: kindsOf(s1)[s1.ids.indexOf(s1.selected)] || s1.selected });
        await shot(page, 'editor-inserted-picture.png');
        check('insert a picture: "🖼 Picture from library" → the picker → a new scene after the definition referring to that library picture (new id, origin inserted), saved',
            ID_RE.test(picId || '') && !Object.values(ID).includes(picId) && (s1.html[i] || '').includes(chloroAsset) && /<img[\s>]/.test(s1.html[i] || '') && s1.edits[i] && s1.edits[i].origin === 'inserted'
            && server1.some(x => x.scene_id === picId) && used.references >= 1,
            `scene ${i + 1} ${picId} "${s1.titles[i]}" html ${(s1.html[i] || '').replace(/token=[^"&]+/, 'token=…').slice(0, 160)}; edit ${JSON.stringify(s1.edits[i])}; chloroplast asset used ${used.references} time(s)`);
        // the new scene chosen in the list: the stage shows it (its library picture resolved to a real address and loaded)
        const before = assetScheme.length;
        await selectScene(page, picId, 'the inserted picture scene');
        await sleep(1500);
        const pic = await page.evaluate(() => {
            const img = document.querySelector('#slide-content-container img');
            return img ? { src: img.getAttribute('src'), loaded: img.complete && img.naturalWidth > 0, w: img.naturalWidth } : null;
        });
        await shot(page, 'editor-inserted-picture-on-stage.png');
        record.insertedPicture = { pic, raw: assetScheme.slice(before) };
        check('the inserted picture scene shows its library picture on the stage (the asset reference resolved, the image loaded)',
            pic && pic.loaded && !/^asset:/.test(pic.src || '') && assetScheme.length === before,
            `stage <img src="${pic ? String(pic.src).slice(0, 90) : '-'}"> loaded ${pic && pic.loaded} (${pic ? pic.w : 0} px); raw "asset:" requests the browser could not load: ${assetScheme.slice(before).map(a => `${a.url} (${a.err})`).join(', ') || 'none'}; editor-inserted-picture-on-stage.png`);
        // the Delete key (the focus on the scene list), confirmed
        await page.keyboard.press('Delete');
        await page.waitForSelector('.editor-confirm:not([hidden]) [data-action="confirm-ok"]', { timeout: 5000 });
        await page.click('.editor-confirm:not([hidden]) [data-action="confirm-ok"]');
        await page.waitForFunction(id => !slides.some(s => s.scene_id === id), picId, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s2 = await state(page);
        const row = await assetRow(chloroAsset);
        const file = fs.existsSync(path.join(data, 'assets')) ? fs.readdirSync(path.join(data, 'assets'), { recursive: true }).length : 0;
        check('delete with the Delete key (confirmed): the picture scene leaves the lesson, its library picture stays in the library (asset row ready)',
            !s2.ids.includes(picId) && same(kindsOf(s2), FINAL) && row.http === 200 && row.status === 'ready',
            `after Delete ${kindsOf(s2).join(',')}; chloroplast asset HTTP ${row.http} ${row.status}, ${row.references} use(s) now; ${file} files in the asset store`);
        // a blank scene after the summary, then Ctrl+Z
        await selectScene(page, ID.summary);
        await page.click('.editor-inspector [data-action="insert-blank"]');
        await page.waitForFunction(() => slides.length === 10, null, { timeout: 10000 }).catch(() => {});
        const rightBlank = await page.evaluate(() => lessonEditorSession.workspace.selectedId);
        await settle(page, 2200, 'after inserting a blank scene (the stage redrawn)');
        const s3 = await state(page);
        const j = at(s3, 'summary') + 1;
        const blankId = s3.ids[j];
        const u3 = await ui(page);
        newSelection.push({ what: 'the blank scene', atOnce: rightBlank === blankId, after: s3.selected === blankId, now: kindsOf(s3)[s3.ids.indexOf(s3.selected)] || s3.selected });
        await shot(page, 'editor-inserted-blank.png');
        await selectScene(page, blankId, 'the blank scene');
        await page.keyboard.press('Control+z');
        await page.waitForFunction(id => !slides.some(s => s.scene_id === id), blankId, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s4 = await state(page);
        check('insert a blank scene: "＋ Blank scene" adds "New scene" after the summary (new id, origin inserted, in the list and the timeline, saved); Ctrl+Z removes it again',
            ID_RE.test(blankId || '') && !Object.values(ID).includes(blankId) && s3.titles[j] === 'New scene' && s3.edits[j] && s3.edits[j].origin === 'inserted'
            && u3.list.some(x => x.id === blankId) && u3.blocks.some(b => b.id === blankId) && !s4.ids.includes(blankId) && same(kindsOf(s4), FINAL) && s4.save === 'saved',
            `new scene ${j + 1} ${blankId} "${s3.titles[j]}" edit ${JSON.stringify(s3.edits[j])}; after Ctrl+Z ${kindsOf(s4).join(',')}, save ${s4.save}`);
    });

    // ---- shortcuts never fire while typing in a field ---------------------------------------------------------------------------
    await section('shortcuts while typing', async () => {
        await selectScene(page, ID.definition);
        const s0 = await state(page);
        const input = page.locator('#editor-field-title');
        await input.click();
        await page.keyboard.press('End');
        await page.keyboard.type(' draft');
        for (const key of ['ArrowLeft', 'ArrowRight', 'Space', 'Delete', '?', 'b', 'Alt+ArrowLeft', 'Alt+ArrowRight']) {
            await page.keyboard.press(key);
            await sleep(120);
        }
        await sleep(500);
        const s1 = await state(page);
        const u1 = await ui(page);
        const value = await input.inputValue();
        const typed = await page.evaluate(id => (slides.find(s => s.scene_id === id) || {}).title, ID.definition);
        // Escape leaves the field (one "Edit title" step); Revert gives the generated title back
        await page.keyboard.press('Escape');
        await sleep(900);
        await ensurePaused(page, 'after a title edit (the stage redrawn)');
        if (!(await page.evaluate(i => lessonEditorSession.workspace.selectedId === i, ID.definition))) await selectScene(page, ID.definition);
        const marked = await page.textContent('.editor-inspector [data-field-box="title"]').catch(() => '');
        await page.click('.editor-inspector [data-action="revert"][data-field="title"]');
        await page.waitForFunction(([id, t]) => (slides.find(s => s.scene_id === id) || {}).title === t, [ID.definition, TITLE.definition], { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s2 = await state(page);
        const iDef = at(s2, 'definition');
        check('shortcuts do not fire while typing in an inspector field (←, →, Space, Delete, ?, B, Alt+←/→ typed into the title: no seek, no move, no delete question, no help, no playback); Escape leaves the field, Revert restores the generated title',
            s1.selected === s0.selected && s1.current === s0.current && s1.renderId === s0.renderId && same(s1.ids, s0.ids) && !s1.playing && !u1.confirm && !u1.help
            && value.startsWith(TITLE.definition) && /draft/.test(value) && typed === value && /Edited/.test(marked || '')
            && s2.titles[iDef] === TITLE.definition && !(s2.edits[iDef] && s2.edits[iDef].original && s2.edits[iDef].original.title !== undefined) && s2.save === 'saved',
            `field "${value}" (the scene's title live: "${typed}"); selection ${s1.selected === s0.selected ? 'kept' : 'CHANGED'}, stage scene ${s0.current + 1} -> ${s1.current + 1}, render ${s0.renderId} -> ${s1.renderId}, order ${same(s1.ids, s0.ids) ? 'kept' : 'CHANGED'}, `
            + `playing ${s1.playing}, confirmation ${u1.confirm ? '"' + u1.confirm.slice(0, 40) + '"' : 'none'}, help ${u1.help}; after Escape "${(marked || '').replace(/\s+/g, ' ').slice(0, 60)}"; after Revert "${s2.titles[iDef]}" edit ${JSON.stringify(s2.edits[iDef])}`);
    });

    // ---- narration, split, mute, labels and background density through the inspector (each given back afterwards: the
    // checks below expect the lesson as edited above) -----------------------------------------------------------------------
    await section('narration, split, mute, labels and background density', async () => {
        const NARRATION = LESSON.scenes[KINDS.indexOf('definition')].narration;
        const PAUSED = NARRATION.replace('. [SYNC]', '. [PAUSE] [SYNC]');
        const lane = id => page.evaluate(i => { const b = document.querySelector(`.editor-tl-row[data-track="narration"] .editor-tl-block[data-scene-id="${i}"]`); return b ? b.getAttribute('data-state') : null; }, id);
        const scene = id => page.evaluate(i => JSON.parse(JSON.stringify(slides.find(s => s && s.scene_id === i) || null)), id);
        const saved = async id => ((await api('GET', `/api/editor/${pid}`)).data.scenes || []).find(s => s.scene_id === id) || null;
        const plainEdit = e => !e || Object.keys(e).length === 0;
        const r = {};
        await selectScene(page, ID.definition);
        const s0 = await state(page);
        const comp0 = s0.comps[at(s0, 'definition')];
        // the narration typed in the inspector: the scene says it, its generated text kept for Revert, saved
        await openSection(page, 'narration');
        await page.locator('#editor-field-narration').fill(PAUSED);
        await page.keyboard.press('Escape');
        await settle(page);
        let sc = await scene(ID.definition);
        let sv = await saved(ID.definition);
        r.narration = { text: sc.narration, original: sc.edit && sc.edit.original ? sc.edit.original.narration : undefined, saved: sv && sv.narration };
        check('narration typed in the inspector: the scene\'s narration changes, its generated text is kept for Revert ("Edited"), saved in place',
            sc.narration === PAUSED && r.narration.original === NARRATION && r.narration.saved === PAUSED, JSON.stringify(r.narration));
        // split at the pause it now has: two scenes sharing the board, the second with a new id; undo gives the one scene back
        if (!(await page.evaluate(i => lessonEditorSession.workspace.selectedId === i, ID.definition))) await selectScene(page, ID.definition);
        await page.waitForSelector('.editor-inspector [data-action="split"]', { timeout: 10000 });
        await page.click('.editor-inspector [data-action="split"]');
        await page.waitForFunction(n => slides.length === n + 1, s0.ids.length, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s1 = await state(page);
        const iDef = at(s1, 'definition');
        const newId = s1.ids[iDef + 1];
        const second = await scene(newId);
        sc = await scene(ID.definition);
        const savedIds = ((await api('GET', `/api/editor/${pid}`)).data.scenes || []).map(s => s.scene_id);
        r.split = { n: s1.ids.length, first: sc.narration, second: second && second.narration, origin: second && second.edit, sameBoard: !!second && second.html === sc.html,
            selected: s1.selected === newId, saved: same(savedIds, s1.ids) };
        check('split at the narration\'s pause (inspector → Scene → ✂ Split here): two scenes, the second right after with a NEW id and the rest of the narration, the same board, selected, saved',
            s1.ids.length === s0.ids.length + 1 && newId && !s0.ids.includes(newId) && /definition\.$/.test(sc.narration.trim()) && second && /Plants use sunlight/.test(second.narration)
            && second.edit && second.edit.origin === 'split' && r.split.sameBoard && r.split.selected && r.split.saved, JSON.stringify(r.split));
        await shot(page, 'editor-split.png');
        await page.click('.editor-top [data-action="undo"]');
        await page.waitForFunction(n => slides.length === n, s0.ids.length, { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s2 = await state(page);
        sc = await scene(ID.definition);
        check('undo of the split: one scene again, its id and narration as before the split, saved', same(s2.ids, s0.ids) && sc.narration === PAUSED
            && same(((await api('GET', `/api/editor/${pid}`)).data.scenes || []).map(s => s.scene_id), s0.ids), `${s2.ids.length} scenes; narration "${sc.narration}"`);
        // Revert: the generated narration back
        await selectScene(page, ID.definition);
        await openSection(page, 'narration');
        await page.click('.editor-inspector [data-action="revert"][data-field="narration"]');
        await page.waitForFunction(([i, n]) => (slides.find(s => s.scene_id === i) || {}).narration === n, [ID.definition, NARRATION], { timeout: 10000 }).catch(() => {});
        await settle(page);
        sc = await scene(ID.definition);
        check('Revert gives the generated narration back (no edit left on it), saved', sc.narration === NARRATION && !(sc.edit && sc.edit.original && sc.edit.original.narration !== undefined)
            && (await saved(ID.definition)).narration === NARRATION, `narration "${sc.narration}"; edit ${JSON.stringify(sc.edit || null)}`);
        // mute: the scene plays without its narration (the timeline says so); unmute gives it back
        await openSection(page, 'narration');
        await page.click('.editor-inspector [data-action="toggle-mute"]');
        await settle(page);
        sc = await scene(ID.definition);
        sv = await saved(ID.definition);
        r.mute = { edit: sc.edit || null, lane: await lane(ID.definition), saved: sv && sv.edit, button: await page.textContent('.editor-inspector [data-action="toggle-mute"]').catch(() => null) };
        await page.click('.editor-inspector [data-action="toggle-mute"]');
        await settle(page);
        sc = await scene(ID.definition);
        r.unmute = { edit: sc.edit || null, lane: await lane(ID.definition) };
        check('mute narration (inspector → Narration): edit.narration_muted, the narration lane shows it muted, saved; pressed again it speaks again',
            r.mute.edit && r.mute.edit.narration_muted === true && r.mute.lane === 'muted' && r.mute.saved && r.mute.saved.narration_muted === true && /muted/i.test(r.mute.button || '')
            && plainEdit(r.unmute.edit) && r.unmute.lane !== 'muted', JSON.stringify({ mute: r.mute, unmute: r.unmute }));
        // labels (inspector → Text): the scene's labels, then Revert
        const labels0 = (await scene(ID.definition)).composition ? (await scene(ID.definition)).composition.labels || null : null;
        await openSection(page, 'text');
        await page.locator('#editor-field-labels').fill('Sunlight\nGlucose');
        await page.keyboard.press('Escape');
        await settle(page);
        sc = await scene(ID.definition);
        sv = await saved(ID.definition);
        const names = l => (Array.isArray(l) ? l.map(x => (typeof x === 'string' ? x : x && x.text)) : null);
        r.labels = { now: names(sc.composition && sc.composition.labels), saved: names(sv && sv.composition && sv.composition.labels) };
        if (!(await page.evaluate(i => lessonEditorSession.workspace.selectedId === i, ID.definition))) await selectScene(page, ID.definition);
        await openSection(page, 'text');
        await page.click('.editor-inspector [data-action="revert"][data-field="labels"]');
        await settle(page);
        sc = await scene(ID.definition);
        r.labels.after = sc.composition ? sc.composition.labels || null : null;
        check('labels typed in the inspector (Text → Labels) become the scene\'s labels, saved; Revert gives the generated ones back',
            same(r.labels.now, ['Sunlight', 'Glucose']) && same(r.labels.saved, ['Sunlight', 'Glucose']) && same(r.labels.after, labels0), JSON.stringify(r.labels));
        // background density (inspector → Background): through the composition review; undo sends it back
        await openSection(page, 'background');
        const d = await compositionChange(page, ID.definition, 'style_background', 'rich', () => page.selectOption('#editor-field-style_background', 'rich'));
        const s3 = await state(page);
        r.density = { status: d.status, sent: d.sent, comp: s3.comps[at(s3, 'definition')] };
        const undone = page.waitForResponse(x => x.url().includes('/api/cinematic/review') && x.request().method() === 'POST', { timeout: 30000 });
        await page.click('.editor-top [data-action="undo"]');
        await undone;
        await page.waitForFunction(i => { const s = slides.find(x => x && x.scene_id === i); const c = s && s.visual_review && s.visual_review.composition; return !(c && c.overrides && c.overrides.style_background); },
            ID.definition, { timeout: 20000 }).catch(() => {});
        await settle(page, 1500);
        const s4 = await state(page);
        r.density.after = s4.comps[at(s4, 'definition')];
        check('background density "rich" (inspector → Background): POST /api/cinematic/review {style_background: rich}, the review records the user\'s change; undo sends it back (no override left)',
            d.status === 200 && same(d.sent.overrides, { style_background: 'rich' }) && r.density.comp && r.density.comp.overrides.style_background === 'rich'
            && !(r.density.after && r.density.after.overrides && r.density.after.overrides.style_background) && same(r.density.after, comp0), JSON.stringify(r.density));
        // the lesson as it was before this section (the checks below expect the edits made above)
        const s5 = await state(page);
        const i5 = at(s5, 'definition');
        check('after these edits were given back the lesson is as before: the same scenes and ids, the definition scene\'s narration, no editor data on it, its review unchanged, saved',
            same(s5.ids, s0.ids) && plainEdit(s5.edits[i5]) && same(s5.comps[i5], comp0) && (await scene(ID.definition)).narration === NARRATION && s5.save === 'saved' && !s5.dirty,
            `ids ${same(s5.ids, s0.ids) ? 'same' : 'CHANGED'}; edit ${JSON.stringify(s5.edits[i5])}; review ${JSON.stringify(s5.comps[i5])}; save ${s5.save}`);
        record.textEdits = r;
    });

    // ---- 16. saved in place, no history entry -----------------------------------------------------------------------------------
    let finalIds = null;
    await section('16. saved in place without a history entry', async () => {
        await settle(page);
        const s = await state(page);
        const u = await ui(page);
        finalIds = s.ids;
        const editing = ['editing', 'stale'];
        const puts = answers.filter(a => editing.includes(a.phase) && a.method === 'PUT' && /\/api\/editor\//.test(a.url));
        const histPosts = requests.filter(r => editing.includes(r.phase) && r.method === 'POST' && /\/save-history/.test(r.url));
        const historyAfter = ((await api('GET', '/get-history')).data.history || []).length;
        await page.locator('.editor-timeline').screenshot({ path: path.join(OUT, 'timeline-after-edits.png') }).catch(() => {});
        await shot(page, 'editor-after-edits.png');
        record.afterEdits = { order: kindsOf(s), edits: s.edits, comps: s.comps, puts: puts.length, total: s.total };
        check('16. the save state reaches "✓ Saved": every edit went through PUT /api/editor in place; no /save-history POST while editing, the lesson history has no new entry',
            u.save === 'saved' && /Saved/.test(u.saveText) && s.save === 'saved' && !s.dirty && puts.length >= 8 && puts.every(a => a.status === 200 || a.status === 409)
            && histPosts.length === 0 && historyAfter === historyBefore,
            `"${u.saveText}"; ${puts.length} PUT /api/editor (${[...new Set(puts.map(a => a.status))].join('/')}); POST /save-history while editing: ${histPosts.length}; history entries ${historyBefore} -> ${historyAfter}; `
            + `timeline ${u.time}; timeline-after-edits.png, editor-after-edits.png`);
    });

    // ---- the small-screen editor ---------------------------------------------------------------------------------------------------
    await section('small screen', async () => {
        await page.setViewportSize({ width: 600, height: 800 });
        await sleep(1000);
        const small = await page.evaluate(() => {
            const cs = sel => { const e = document.querySelector(sel); return e ? getComputedStyle(e) : null; };
            const vis = sel => { const e = document.querySelector(sel); if (!e) return false; const r = e.getBoundingClientRect(); const c = getComputedStyle(e); return r.width > 0 && r.height > 0 && c.display !== 'none' && c.visibility !== 'hidden'; };
            const items = [...document.querySelectorAll('.editor-scene-list .editor-scene')];
            return { scroll: (cs('.editor-tl-scroll') || {}).display, note: vis('.editor-tl-note'), hole: (cs('.editor-stage-hole') || {}).display, list: vis('.editor-scene-list'),
                items: items.length, itemWidth: items[0] ? Math.round(items[0].getBoundingClientRect().width) : 0, play: vis('.editor-timeline [data-action="play"]'),
                top: vis('.editor-top'), inspector: vis('.editor-inspector'), overflow: document.documentElement.scrollWidth > innerWidth + 1,
                columns: getComputedStyle(document.querySelector('.editor-root')).gridTemplateColumns };
        });
        await shot(page, 'editor-small-screen.png');
        await page.setViewportSize(VIEW);
        await sleep(800);
        record.small = small;
        check('small screen (600 px wide): the simplified editor: one column, the scene list and the play controls, no timeline tracks (a note instead), no stage hole, no sideways scroll',
            small.scroll === 'none' && small.note && small.hole === 'none' && small.list && small.items === 9 && small.play && small.top && !small.overflow && !/\s/.test(small.columns.trim()),
            `${JSON.stringify(small)}; editor-small-screen.png`);
    });

    // ---- what the editing showed about playback and the selection -----------------------------------------------------------------
    record.resumed = resumed.slice();
    record.newSelection = newSelection.slice();
    check('the lesson stays paused while editing: choosing a scene, seeking or an edit never starts playback (the editor shows the scene paused)',
        resumed.length === 0 && record.selectWhilePaused && !record.selectWhilePaused.playing,
        `playback started on its own ${resumed.length} time(s): ${Object.entries(resumed.reduce((m, c) => { m[c] = (m[c] || 0) + 1; return m; }, {})).map(([c, n]) => `${c}${n > 1 ? ` ×${n}` : ''}`).join(' | ') || 'never'}; editor-selected-diagram.png shows "${record.selectWhilePaused && record.selectWhilePaused.button}" right after a list click on the paused lesson`);
    check('a new scene (duplicate, library picture, blank) stays selected after the edit (the inspector shows the new scene, so the next action applies to it)',
        newSelection.length === 3 && newSelection.every(x => x.atOnce && x.after),
        newSelection.map(x => `${x.what}: selected right after ${x.atOnce}, after the stage redrew ${x.after ? 'still selected' : `selection moved to ${x.now}`}${x.inspector ? ` (inspector "${x.inspector}")` : ''}`).join(' | '));
    check('every other click on a scene in the list selected it the first time', ignoredClicks.length === 0, ignoredClicks.join(' | ') || 'all first clicks selected');

    // ---- 17-18. reload; the edits remain -----------------------------------------------------------------------------------------
    await section('17. reload with ?project_id', async () => {
        phase = 'reload';
        const before = dialogs.length;
        await startLesson(page, pid);
        await playerBar(page, '#tts-play-btn');
        await page.waitForFunction(() => !ttsState.isPlaying, null, { timeout: 5000 }).catch(() => {});
        const facts = await page.evaluate(() => ({ url: location.search, n: slides.length, plans: slides.filter(s => s.cinematic_plan).length }));
        check('17. the page reloaded with ?project_id opens the edited lesson (no draft of unsaved edits offered: everything was saved)',
            facts.url === `?project_id=${pid}` && facts.n === 9 && facts.plans === 9 && dialogs.length === before,
            `${facts.url}: ${facts.n} scenes, ${facts.plans} plans; dialogs ${dialogs.slice(before).map(d => `${d.type} "${d.message}"`).join(' | ') || 'none'}`);
        const s = await state(page);
        const server = (await api('GET', `/api/projects/${pid}`)).data || {};
        const sv = server.scenes || [];
        const svOf = kind => sv.find(x => x.scene_id === ID[kind]) || {};
        const comp = sc => (sc.visual_review && sc.visual_review.composition) || {};
        const i = kind => at(s, kind);
        const items = {
            order: same(s.ids, finalIds) && same(kindsOf(s), FINAL) && same(sv.map(x => x.scene_id), finalIds),
            hold: s.edits[i('code')] && s.edits[i('code')].min_seconds === HOLD && svOf('code').edit && svOf('code').edit.min_seconds === HOLD,
            camera: s.comps[i('code')] && s.comps[i('code')].overrides.camera === 'static' && s.plans[i('code')].camera === 'static' && (comp(svOf('code')).overrides || {}).camera === 'static',
            accent: s.comps[i('code')] && s.comps[i('code')].overrides.style_accent === 'teal' && norm(s.plans[i('code')].accent) === norm(EXPECT.teal),
            transition: s.comps[i('explanation')] && s.comps[i('explanation')].overrides.transition === changes.transition.value && s.plans[i('explanation')].transition === changes.transition.value,
            presenter: s.comps[i('explanation')] && s.comps[i('explanation')].overrides.presenter_position === changes.presenter.value && s.plans[i('explanation')].side === changes.presenter.value,
            visual: s.sideAsset[i('diagram')] === photoAsset,
            hidden: s.edits[i('comparison')] && s.edits[i('comparison')].hidden === true,
            captions: s.edits[i('formula')] && s.edits[i('formula')].captions === 'off',
            otherWriter: s.subtitles[i('summary')] === OTHER_SUBTITLE,
            generatedOrder: s.editor && same(s.editor.generated_order, record.initialIds),
            title: s.titles[i('definition')] === TITLE.definition
        };
        record.reloaded = { order: kindsOf(s), edits: s.edits, comps: s.comps, plans: s.plans, editor: s.editor };
        check('18. after the reload the edits remain: the order, the 14 s hold, camera Still + teal accent (code), transition + presenter side (explanation), the chosen visual, the hidden scene, captions off, the other writer\'s change, the generated order',
            Object.values(items).every(Boolean),
            `${Object.entries(items).map(([k, v]) => `${k} ${v ? 'kept' : 'LOST'}`).join(', ')}; order ${kindsOf(s).join(',')}; code ${JSON.stringify({ edit: s.edits[i('code')], review: s.comps[i('code')], camera: s.plans[i('code')].camera })}; `
            + `explanation ${JSON.stringify({ review: s.comps[i('explanation')], transition: s.plans[i('explanation')].transition, side: s.plans[i('explanation')].side })}`);
    });

    // ---- 19-20. the quality check from the editor --------------------------------------------------------------------------------
    await section('19. the quality check from the editor', async () => {
        phase = 'quality';
        await playerBar(page, '#editor-btn');
        await page.waitForFunction(() => typeof lessonEditorSession !== 'undefined' && lessonEditorSession && lessonEditorSession.workspace.isOpen, null, { timeout: 30000 });
        await page.waitForSelector('.editor-root .editor-scene', { timeout: 10000 });
        await settle(page, 1500);
        const before = await ui(page);
        await selectScene(page, ID.code);
        await openSection(page, 'quality');
        const notChecked = (await ui(page)).sceneQuality;
        const answer = page.waitForResponse(r => r.url().includes('/api/quality/lesson') && r.request().method() === 'POST', { timeout: 60000 });
        await page.click('.editor-inspector [data-action="quality-run"]');
        const response = await answer;
        const report = await response.json().catch(() => null);
        await page.waitForFunction(() => !lessonEditorSession.workspace.qualityBusy, null, { timeout: 30000 }).catch(() => {});
        await sleep(600);
        const u = await ui(page);
        const s = await state(page);
        await shot(page, 'editor-quality.png');
        const issues = (report && Array.isArray(report.issues)) ? report.issues : [];
        record.quality = { http: response.status(), status: report && report.status, counts: report && report.summary && report.summary.counts,
            issues: issues.map(f => ({ severity: f.severity, rule: f.rule, scene: f.scene, message: f.message })) };
        issues.forEach(f => note(`  finding: ${f.severity} ${f.rule} scene ${f.scene === null ? '-' : f.scene + 1}: ${f.message}`));
        check('19. "Check quality" in the editor\'s inspector (Quality section) checks the edited lesson (POST /api/quality/lesson 200); the top bar\'s quality summary is no longer "not checked"',
            response.status() === 200 && report && before.qualityStatus === 'none' && notChecked === 'none' && u.qualityStatus && !['none', 'stale'].includes(u.qualityStatus),
            `before: top "${before.quality}" [${before.qualityStatus}], scene "${notChecked}"; POST ${response.status()} status ${report && report.status}, counts ${JSON.stringify(report && report.summary && report.summary.counts)}; after: top "${u.quality}" [${u.qualityStatus}]; editor-quality.png`);
        // 20: each scene's chip in the list follows the report's findings for that scene (the edited ones in particular)
        const rows = s.ids.map((id, index) => {
            const mine = issues.filter(f => f.scene === index && f.severity !== 'info');
            const item = u.list.find(x => x.id === id) || { marks: [] };
            const chip = item.marks.find(m => m.chip);
            const count = chip ? Number((/(\d+)/.exec(chip.text || '') || [])[1]) : 0;
            return { kind: kindsOf(s)[index], findings: mine.length, chip: chip ? chip.text : null, stale: chip ? chip.stale : null, ok: chip ? count === mine.length && !chip.stale : mine.length === 0 };
        });
        const codeFindings = issues.filter(f => f.scene === at(s, 'code'));
        const codeOk = codeFindings.length ? u.findings.length === codeFindings.length : u.sceneQuality === 'good';
        const edited = ['code', 'formula', 'explanation', 'diagram', 'comparison'];
        check('20. the quality state shows: the top bar\'s summary, a chip on each scene with findings (count = the report\'s, the edited scenes included), the code scene\'s own result in its inspector',
            rows.every(r => r.ok) && codeOk && edited.every(k => rows.find(r => r.kind === k)),
            `${rows.map(r => `${r.kind}: ${r.findings} finding(s), chip ${r.chip ? '"' + r.chip + '"' : 'none'}${r.ok ? '' : ' MISMATCH'}`).join(' | ')}; code inspector ${u.sceneQuality || `${u.findings.length} finding(s)`} (report ${codeFindings.length})`);
    });

    // ---- 21. preview: the hold, the hidden scene skipped, captions off ------------------------------------------------------------
    await section('21. preview of the edited lesson', async () => {
        phase = 'preview';
        await page.evaluate(startRecorder);
        await selectScene(page, ID.diagram);
        await sleep(800);
        await page.click('.editor-timeline [data-action="play"]');
        const playAt = Date.now();
        const shots = { hold: false, formula: false };
        let done = false;
        while (!done && Date.now() - playAt < 90000) {
            await sleep(250);
            const rec = await page.evaluate(() => (window.__rec ? window.__rec.renders.map(r => ({ t: r.t, id: r.id })) : []));
            const now = await page.evaluate(() => Date.now());
            const byId = id => rec.find(r => r.id === id);
            const c = byId(ID.code);
            const f = byId(ID.formula);
            if (c && !f && !shots.hold && now - c.t > 9000) { await shot(page, 'preview-code-hold.png'); shots.hold = true; }
            if (f && !shots.formula && now - f.t > 2500) { await shot(page, 'preview-formula-captions-off.png'); shots.formula = true; }
            done = !!byId(ID.summary);
        }
        await sleep(1500);
        if (await page.evaluate(() => !!ttsState.isPlaying)) await page.click('.editor-timeline [data-action="play"]');
        await page.waitForFunction(() => !ttsState.isPlaying, null, { timeout: 5000 }).catch(() => {});
        const rec = await page.evaluate(stopRecorder);
        const seq = rec.renders.map(r => r.id);
        const tOf = id => (rec.renders.find(r => r.id === id) || {}).t;
        const holdSeconds = tOf(ID.formula) && tOf(ID.code) ? (tOf(ID.formula) - tOf(ID.code)) / 1000 : null;
        const capsOf = id => rec.caps.filter(c => c.id === id);
        const formulaCaps = capsOf(ID.formula);
        const shownElsewhere = [ID.diagram, ID.code, ID.explanation, ID.summary].filter(id => capsOf(id).some(c => c.on));
        record.preview = { sequence: seq.map(id => Object.keys(ID).find(k => ID[k] === id) || id), holdSeconds, formulaCaps: formulaCaps.length, formulaShown: formulaCaps.filter(c => c.on).length,
            shownElsewhere: shownElsewhere.map(id => Object.keys(ID).find(k => ID[k] === id)) };
        const after = seq.slice(seq.indexOf(ID.code));
        check(`21. preview (▶ Play in the editor from the diagram): the code scene holds its ${HOLD} s minimum, the hidden comparison is skipped (explanation → key takeaways), the formula plays with no caption line`,
            holdSeconds !== null && holdSeconds >= HOLD - 0.5 && holdSeconds < HOLD + 8 && same(after.slice(0, 4), [ID.code, ID.formula, ID.explanation, ID.summary]) && !seq.includes(ID.comparison)
            && formulaCaps.length > 20 && formulaCaps.every(c => !c.on) && formulaCaps.some(c => c.off),
            `played ${record.preview.sequence.join(' → ')}; code scene on screen ${holdSeconds === null ? '-' : holdSeconds.toFixed(2)} s (minimum ${HOLD}); formula caption samples ${formulaCaps.length}, shown ${record.preview.formulaShown}, marked off ${formulaCaps.filter(c => c.off).length}; `
            + `captions shown in ${record.preview.shownElsewhere.join(', ') || 'no other scene'}; preview-code-hold.png, preview-formula-captions-off.png`);
        if (!shownElsewhere.length) note('no caption line was shown in any scene of this preview (the captions-off check then only shows the formula had none)');
        // close the editor: the player bar comes back
        await page.click('.editor-top [data-action="close"]');
        await page.waitForFunction(() => !document.querySelector('.editor-root'), null, { timeout: 10000 }).catch(() => {});
        const closed = await page.evaluate(() => ({ root: !!document.querySelector('.editor-root'), active: document.body.classList.contains('editor-active'),
            bar: getComputedStyle(document.getElementById('voice-control-bar')).display }));
        check('the editor closes ("‹ Back to lesson"): its chrome is gone and the player bar is back',
            !closed.root && !closed.active && closed.bar !== 'none', JSON.stringify(closed));
    });

    await browser.close();
    browser = null;

    // ---- 22-23. export the edited lesson ------------------------------------------------------------------------------------------
    const EXPORT_ARGS = ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'];
    exportBrowser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'], args: EXPORT_ARGS });
    silent.flags.push('export: ' + EXPORT_ARGS.join(' ') + ' + suppressLocalAudioPlayback');
    await section('22. export the edited lesson', async () => {
        phase = 'export';
        const left = BUDGET_MS - (Date.now() - STARTED);
        if (left < 150000) throw new Error(`not enough time left for the export (${Math.round(left / 1000)} s)`);
        const ex = await newPage(exportBrowser, VIEW, baseCine);
        const p = ex.page;
        await startLesson(p, pid);
        const idx = await p.evaluate(ids => ids.map(id => slides.findIndex(s => s.scene_id === id)), [ID.code, ID.diagram, ID.explanation]);
        const previews = {};
        for (const [k, i] of [['code', idx[0]], ['diagram', idx[1]], ['explanation', idx[2]]]) {
            const facts = await playScene(p, i);
            const file = path.join(OUT, `preview-edited-${k}-end.png`);
            await p.screenshot({ path: file });
            previews[k] = { file, facts };
        }
        await stopNarration(p);
        await openLesson(p, pid);
        await p.evaluate(() => planLessonVisuals());
        await p.click('#start-videos-btn');
        await p.click('.export-start-btn');
        const limit = Math.min(420000, left - 60000);
        let prompt = null;
        for (;;) {
            const start = p.locator('.export-actions button', { hasText: 'Start Recording' });
            const anyway = p.locator('.export-actions button', { hasText: 'Record anyway' });
            await Promise.race([start.waitFor({ timeout: limit }), anyway.waitFor({ timeout: limit })]);
            if (await anyway.isVisible()) {
                prompt = await p.textContent('.export-message');
                await p.screenshot({ path: path.join(OUT, 'export-prompt.png') });
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
        const exportDir = path.join(data, 'exports');
        const userDir = job && fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(dd => fs.existsSync(path.join(exportDir, dd, `${job.id}.webm`))) : null;
        const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
        const sceneLog = await p.evaluate(() => window.exportSceneLog || []);
        await ex.context.close();
        let chapters = { status: null, text: '' };
        let vtt = { status: null, text: '' };
        if (job && outcome === 'ready') {
            for (let k = 0; k < 20 && chapters.status !== 200; k++) { chapters = await apiText(`/api/exports/${job.id}/outputs/chapters`); if (chapters.status !== 200) await sleep(1000); }
            for (let k = 0; k < 20 && vtt.status !== 200; k++) { vtt = await apiText(`/api/exports/${job.id}/outputs/vtt`); if (vtt.status !== 200) await sleep(1000); }
        }
        fs.writeFileSync(path.join(OUT, 'export-chapters.txt'), chapters.text || '');
        fs.writeFileSync(path.join(OUT, 'export-subtitles.vtt'), vtt.text || '');
        record.export = { outcome, prompt, job: job && job.id, sceneLog };
        check('22. the edited lesson exports (the export flow on the saved lesson: recorded, uploaded, ready)',
            outcome === 'ready' && !!stored, `${outcome}; ${stored ? path.relative(OUT, stored) : 'no stored video'}; pre-export prompt ${prompt ? '"' + prompt.replace(/\s+/g, ' ').slice(0, 160) + '"' : 'none'}`);

        // 23: order, hidden scene, chapters, captions, the edited scene's frame
        const titles = sceneLog.map(x => x.title);
        const want = PLAYED.map(k => TITLE[k]);
        const chapterTitles = (chapters.text || '').split('\n').map(l => l.replace(/^\s*[\d:]+\s+/, '').trim()).filter(Boolean);
        const knownChapters = chapterTitles.filter(t => Object.values(TITLE).includes(t));
        const rank = kind => titles.indexOf(TITLE[kind]);
        const codeSeconds = rank('code') >= 0 && sceneLog[rank('code') + 1] ? sceneLog[rank('code') + 1].t - sceneLog[rank('code')].t : null;
        check('23. the export follows the edits: the recorded scene order and the chapters are the edited order, the hidden comparison is absent, the code scene held its minimum',
            same(titles, want) && same(knownChapters, want) && !titles.includes(TITLE.comparison) && !chapterTitles.includes(TITLE.comparison) && codeSeconds !== null && codeSeconds >= HOLD - 0.5,
            `scene log ${titles.map(t => `"${t}"`).join(' → ')}; chapters ${chapters.status} ${chapterTitles.map(t => `"${t}"`).join(' → ')}; code scene ${codeSeconds === null ? '-' : codeSeconds.toFixed(2)} s in the recording; export-chapters.txt`);
        const cues = (vtt.text || '').split(/\r?\n\r?\n/).filter(b => /-->/.test(b)).map(b => b.split(/\r?\n/).filter(l => !/-->/.test(l) && !/^\d+$/.test(l.trim())).join(' ').trim());
        const formulaCue = cues.filter(c => /F equals m times a|Here is the law/i.test(c));
        const otherCue = cues.filter(c => /Here is a loop|Look at this diagram|Think about this|Let us recap/i.test(c));
        check('23b. the exported subtitles leave the formula scene out (its captions are off) and keep the other scenes\' lines',
            vtt.status === 200 && formulaCue.length === 0 && otherCue.length > 0,
            `VTT ${vtt.status}: ${cues.length} cues; formula lines ${formulaCue.length}${formulaCue.length ? ': ' + formulaCue.slice(0, 2).join(' | ') : ''}; other scenes' lines ${otherCue.length} (e.g. "${(otherCue[0] || '').slice(0, 60)}"); export-subtitles.vtt`);
        // the frame at the end of each edited scene (just before the next one starts) against the preview's
        const frames = {};
        for (const k of ['code', 'diagram', 'explanation']) {
            const r = rank(k);
            const next = sceneLog[r + 1];
            const file = path.join(OUT, `export-edited-${k}-end.png`);
            frames[k] = { diff: null, fullDiff: null, file: path.basename(file) };
            if (!stored || r < 0 || !next) continue;
            spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', Math.max(0, next.t - 0.6).toFixed(2), '-i', stored, '-frames:v', '1', file]);
            if (!fs.existsSync(file)) continue;
            const a = grid(previews[k].file);
            const b = grid(file);
            const lesson = 16 * 32 * 3; // the 32x18 grid without its two bottom rows (the preview's player controls)
            frames[k].diff = +difference(a.slice(0, lesson), b.slice(0, lesson)).toFixed(1);
            frames[k].fullDiff = +difference(a, b).toFixed(1);
        }
        record.export.frames = frames;
        note(`frame differences (preview vs export, controls excluded): ${Object.entries(frames).map(([k, f]) => `${k} ${f.diff} (${f.file})`).join(', ')}`);
        check('23c. the exported frame of the edited code scene (moved, 14 s hold, camera Still, teal accent) matches the preview (mean difference < 12, player controls excluded)',
            frames.code.diff !== null && frames.code.diff < 12 && norm(previews.code.facts.accent) === norm(EXPECT.teal),
            `code ${frames.code.diff} (whole frame ${frames.code.fullDiff}); preview accent ${previews.code.facts.accent}; also diagram ${frames.diagram.diff}, explanation ${frames.explanation.diff} (camera moves allowed there: not asserted); `
            + `preview-edited-code-end.png vs export-edited-code-end.png`);
    });

    // ---- no errors; nothing generated; silent -----------------------------------------------------------------------------------------
    const generated = requests.filter(r => isGeneration(r.method, r.url));
    fs.writeFileSync(path.join(OUT, 'requests.json'), JSON.stringify(requests, null, 2));
    fs.writeFileSync(path.join(OUT, 'answers.json'), JSON.stringify(answers, null, 2));
    check('no generation request during the whole check (planning, reviews, saving, quality and the export only)',
        generated.length === 0, `${generated.length} generation requests${generated.length ? ': ' + generated.slice(0, 4).map(r => `${r.phase} ${r.method} ${r.url}`).join(', ') : ''}; ${requests.length} requests in all`);
    check('no unexpected page errors, failed requests or dialogs', problems.length === 0 && dialogs.length === 0,
        [...problems.slice(0, 6), ...dialogs.slice(0, 3).map(d => `dialog (${d.phase}) ${d.type}: ${d.message}`)].join(' | '));
    check('every browser was silent (audio output disabled, speech stubbed on every page, capture without local playback)',
        silent.stubbed.size === silent.pages && silent.pages > 0 && silent.flags.length === 2 && silent.flags.every(f => f.includes('--disable-audio-output')) && silent.flags[0].includes('--mute-audio'),
        `${silent.stubbed.size}/${silent.pages} pages stubbed; ${silent.flags.join(' | ')}`);
} catch (e) {
    check('check ran to the end', false, e.stack || e.message);
} finally {
    if (browser) await browser.close().catch(() => {});
    if (exportBrowser) await exportBrowser.close().catch(() => {});
    killServer();
    try { fs.writeFileSync(path.join(OUT, 'editor-check.json'), JSON.stringify(record, null, 2)); } catch (e) { /* best effort */ }
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Artifacts: ${OUT}`);
process.exit(failed.length ? 1 : 0);
