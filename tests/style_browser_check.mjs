// Professional Video Styling System check (Phase 17) in real Chrome against a throwaway server with the local stand-ins only
// (AI_FAKE_PROVIDER=1: stand-in media and text models; FAKE_TTS=1: a speech-like tone as long as the text). No real provider.
//
//   1. the settings panel offers the four styles, each with its own swatch   2. a representative lesson rendered in every
//   style (the style's attribute and tokens on the page, a screenshot of every scene)   3. every text on screen reads
//   (WCAG contrast measured in the page)   4. switching styles re-renders without generating anything (same library visual)
//   5. preview and export match in a light and a dark style (and the exported frame carries the style)   6. overrides
//   (accent, text size, plain background) change the tokens and the board still fits   7. the style is kept with the lesson
//   and restored on open   8. an old lesson keeps today's look   9. Classic is unchanged   10. Visual Review shows the style
//   and a scene accent stays on that scene   11. the presenter name card follows the style   12. no errors, all silent.
//
// The expected values come from styles.py itself (the repo's Python), never copied here.
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/style_browser_check.mjs
// Silent: --mute-audio and --disable-audio-output, browser speech stubbed; the export's tab capture keeps the tab unmuted
// (a muted tab records silence) but asks for suppressLocalAudioPlayback, with no audio output device.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.STYLE_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-style-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.STYLE_CHECK_PORT || 9930 + (process.pid % 8));
const BASE = `http://127.0.0.1:${PORT}`;
const STARTED = Date.now();
const BUDGET_MS = Number(process.env.STYLE_CHECK_BUDGET_MS || 1600000); // stop starting new work before an outer timeout
if (!PASSWORD) {
    console.error('Set AADHI_PASSWORD to the default admin password (a fresh database creates that admin).');
    process.exit(2);
}
const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: !!ok, detail });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`);
}
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
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `ast-${process.pid}`), JWT_SECRET: 'style-check-' + Math.random().toString(36).slice(2),
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
// The recorded frames around a moment of a video (20 per second, each as a 16x9 grid): how much each one varies, and its
// mean colour (an empty single-colour frame, such as a white or black flash, varies by almost nothing)
function framesAround(video, t, before = 0.4, after = 1.4) {
    const start = Math.max(0, t - before);
    const raw = ffmpeg('-ss', start.toFixed(2), '-t', (before + after).toFixed(2), '-i', video, '-vf', 'fps=20,scale=16:9:flags=area', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-');
    const size = 16 * 9 * 3;
    const out = [];
    for (let o = 0; o + size <= raw.length; o += size) {
        const g = [...raw.subarray(o, o + size)];
        const mean = g.reduce((a, b) => a + b, 0) / g.length;
        const sd = Math.sqrt(g.reduce((a, v) => a + (v - mean) ** 2, 0) / g.length);
        const rgb = [0, 1, 2].map(c => g.filter((_, i) => i % 3 === c).reduce((a, b) => a + b, 0) / (g.length / 3));
        out.push({ t: +(start + out.length / 20).toFixed(2), spread: +sd.toFixed(1), mean: +mean.toFixed(1), colour: hexOf(rgb) });
    }
    return out;
}
// How much a screenshot varies (standard deviation of a 16x9 grid): an empty, single-colour frame is near 0
function spread(file) {
    const g = grid(file, 16, 9);
    const mean = g.reduce((a, b) => a + b, 0) / g.length;
    return +Math.sqrt(g.reduce((a, v) => a + (v - mean) ** 2, 0) / g.length).toFixed(1);
}
const norm = v => String(v === undefined || v === null ? '' : v).trim().toLowerCase().replace(/\s+/g, ' ');
const hexOf = c => '#' + c.slice(0, 3).map(x => Math.round(x).toString(16).padStart(2, '0')).join('');
function rgbOf(value) {
    const s = norm(value);
    let m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(s);
    if (m) { let h = m[1]; if (h.length === 3) h = h.split('').map(c => c + c).join(''); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)).concat(1); }
    m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(s);
    return m ? [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]] : null;
}
const overRgb = (t, b) => [0, 1, 2].map(i => t[i] * t[3] + b[i] * (1 - t[3])).concat(1);
const dist = (a, b) => Math.sqrt([0, 1, 2].reduce((s, i) => s + (a[i] - b[i]) ** 2, 0));

// ---- what styles.py says (the source of truth), read with the repo's Python ------------------------------------------------
const PY = `
import json, styles
ov = {"accent": "teal", "text_size": "large", "background": "plain"}
scene = {"visual_review": {"composition": {"status": "changed", "overrides": {"style_accent": "teal"}}}}
out = {"families": list(styles.FAMILIES), "default": styles.DEFAULT_FAMILY, "looks": {}}
for f in styles.FAMILIES:
    look = styles.resolve({"style": f})
    out["looks"][f] = {"label": look["label"], "tone": look["tone"], "prefs": look["prefs"], "css": styles.css_variables(look),
        "override_css": styles.css_variables(styles.resolve({"style": f, "style_overrides": ov})),
        "scene_teal_css": styles.css_variables(styles.resolve({"style": f}, scene))}
out["legacy"] = styles.css_variables(styles.resolve({"typography": "academic"}))
print(json.dumps(out))
`;
function expectedFromStylesPy() {
    const r = spawnSync(PYTHON, ['-c', PY], { cwd: REPO, encoding: 'utf8' });
    if (r.status !== 0) throw new Error('styles.py could not be read: ' + r.stderr);
    return JSON.parse(r.stdout);
}

// The representative lesson: an introduction, a definition, process steps, a diagram (a library asset), a formula with symbol
// labels, code with its output, a comparison table, a presenter-led explanation and the key points
const LESSON = { subject_name: 'Style check', session_title: 'How plants and programs work',
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
const AT = Object.fromEntries(KINDS.map((k, i) => [k, i]));
// A short lesson for the exports: the frame compared is the end of the code scene (a still camera, everything revealed)
const SHORT = { subject_name: 'Style export check', session_title: 'A loop',
    scenes: [LESSON.scenes[AT.definition], LESSON.scenes[AT.code], LESSON.scenes[AT.summary]] };
const SHORT_CODE = 1;

// Media / AI generation endpoints (a style switch must never call one): planning (/api/cinematic/plan, /api/visuals/plan),
// keeping the style (/api/cinematic/style) and reading files are not generation
const GENERATION = /\/generate-ai|\/api\/ai-media\/lessons\/[^/]+\/generate|\/api\/cinematic\/background|\/regenerate-manim|\/get-image|\/get-gif|\/api\/presenters\/(?:[^?]*\/)?(?:render|generate)(?:[/?]|$)|\/api\/visuals\/[^?]*generate/;
const isGeneration = (method, url) => GENERATION.test(url) || (method !== 'GET' && /\/api\/ai[-/]/.test(url));

// ---- page-side probes (serialized into the page) ---------------------------------------------------------------------------
// WCAG contrast of every text on screen against what is really behind it: the ancestors' backgrounds (colours and gradient
// stops, alpha composited, every gradient stop tried: the worst counts) down to the first opaque one, else the scene's
// background (the plan's --st-bg-2; today's #2a1352 for a plan without a look)
function readabilityProbe(only) {
    const parse = v => {
        const s = String(v || '').trim().toLowerCase();
        if (!s) return null;
        if (s === 'transparent') return [0, 0, 0, 0];
        let m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(s);
        if (m) { let h = m[1]; if (h.length === 3) h = h.split('').map(c => c + c).join(''); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), 1]; }
        m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+)(%?))?\s*\)$/.exec(s);
        if (!m) return null;
        const a = m[4] === undefined ? 1 : parseFloat(m[4]) / (m[5] ? 100 : 1);
        return [+m[1], +m[2], +m[3], Math.max(0, Math.min(1, a))];
    };
    const over = (t, b) => [0, 1, 2].map(i => t[i] * t[3] + b[i] * (1 - t[3])).concat(1);
    const lum = c => { const ch = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }; return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]); };
    const ratio = (f, b) => { const a = lum(f); const c = lum(b); return (Math.max(a, c) + 0.05) / (Math.min(a, c) + 0.05); };
    const hex = c => '#' + c.slice(0, 3).map(x => Math.round(x).toString(16).padStart(2, '0')).join('');
    let base = parse(getComputedStyle(document.documentElement).getPropertyValue('--st-bg-2')) || [42, 19, 82, 1];
    if (base[3] < 1) base = over(base, [0, 0, 0, 1]);
    const stopsOf = img => (String(img).match(/rgba?\([^)]*\)/g) || []).map(parse).filter(Boolean);
    function behind(el) {
        const layers = [];
        for (let n = el; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
            const cs = getComputedStyle(n);
            if (cs.backgroundImage && cs.backgroundImage !== 'none') {
                if (/url\(/.test(cs.backgroundImage)) return null; // a picture behind the text: not measurable this way
                const stops = stopsOf(cs.backgroundImage);
                if (stops.some(c => c[3] > 0)) layers.push(stops);
                if (stops.length && stops.every(c => c[3] >= 0.999)) break;
            }
            const bc = parse(cs.backgroundColor);
            if (bc && bc[3] > 0) { layers.push([bc]); if (bc[3] >= 0.999) break; }
        }
        let cands = [base];
        for (let i = layers.length - 1; i >= 0; i--) {
            let next = [];
            cands.forEach(c => layers[i].forEach(s => next.push(over(s, c))));
            if (next.length > 24) { const step = Math.ceil(next.length / 24); next = next.filter((_, k) => k % step === 0); }
            cands = next;
        }
        return cands;
    }
    const direct = el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const shown = el => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const groups = [
        ['body', '#slide-content-container :is(p, li, td, th, .definition, .keyword)', 4.5],
        ['heading', '#slide-content-container :is(h1, h2, h3, h4, h5, h6)', 3],
        ['title', '.cine-title-text', 3],
        ['title-subtitle', '.cine-title-subtitle', 4.5],
        ['label', '.cine-label', 4.5],
        ['subtitles', '#subtitle-track.active, #subtitle-track.active *', 4.5],
        ['presenter-name', '.cine-presenter-name', 4.5]
    ].filter(g => !only || only.includes(g[0]));
    const items = [];
    groups.forEach(([group, sel, min]) => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.closest('pre') || !direct(el) || !shown(el)) return;
            const cs = getComputedStyle(el);
            let fg = parse(cs.webkitTextFillColor) || parse(cs.color);
            const text = el.textContent.replace(/\s+/g, ' ').trim().slice(0, 40);
            if (!fg || fg[3] === 0) { items.push({ group, text, skipped: 'transparent or unreadable text colour ' + cs.color }); return; }
            const bgs = behind(el);
            if (!bgs) { items.push({ group, text, skipped: 'a picture behind the text' }); return; }
            let worst = null;
            bgs.forEach(b => { const f = fg[3] < 1 ? over(fg, b) : fg; const r = ratio(f, b); if (!worst || r < worst.r) worst = { r, b }; });
            items.push({ group, text, min, ratio: Math.round(worst.r * 100) / 100, fg: cs.color, bg: hex(worst.b), ok: worst.r >= min });
        });
    });
    return items;
}

// What the scene shows: the style attributes, the --st-* variables on <html>, the presenter and its name card, the visual
function sceneFacts(n) {
    const root = document.documentElement;
    const vars = {};
    for (let i = 0; i < root.style.length; i++) { const n = root.style[i]; if (n.startsWith('--st-')) vars[n] = root.style.getPropertyValue(n).trim(); }
    const plan = slides[n] && slides[n].cinematic_plan;
    const look = plan && plan.style && plan.style.look;
    const vw = innerWidth, vh = innerHeight;
    const pl = plan && (plan.layers || []).find(l => l.id === 'presenter');
    const box = pl && pl.box ? { x: pl.box.x * vw, y: pl.box.y * vh, w: pl.box.w * vw, h: pl.box.h * vh } : null;
    const isShown = el => { const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
        return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 && Number(cs.opacity) > 0.05; };
    const rectOf = el => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const holder = document.getElementById('presenter-layer');
    const cards = [...document.querySelectorAll('.cine-presenter-name')];
    const card = cards.find(c => holder && holder.contains(c)) || cards[0] || null;
    let name = null;
    if (card) {
        name = { state: card.getAttribute('data-state'), text: card.textContent, inLayer: !!(holder && holder.contains(card)), shown: isShown(card), rect: rectOf(card),
            layer: holder ? rectOf(holder) : null, strays: cards.filter(c => c !== card && isShown(c)).length };
    }
    const board = document.getElementById('presentation-board');
    const boardShown = document.body.getAttribute('data-cine-board') !== 'none' && board && board.getBoundingClientRect().width > 0;
    const img = document.querySelector('.dynamic-side-zone .side-panel-view.active img');
    const attrs = {};
    [...document.body.attributes].forEach(a => { if (a.name.startsWith('data-cine')) attrs[a.name] = a.value; });
    const side = (slides[n].visual_plan || {}).side || {};
    return { scene: n, current: currentSlide, template: plan && plan.template, family: look ? look.family : null, lookId: look ? look.id : null,
        overrides: look ? look.overrides : null, sceneOverrides: look ? look.scene_overrides : null, attrs, style: document.body.getAttribute('data-cine-style'),
        vars, presenterShown: !!(plan && plan.presenter && plan.presenter.shown), presenterId: plan && plan.presenter ? plan.presenter.presenter_id : null,
        presenterZ: (document.getElementById('presenter-layer') && getComputedStyle(document.getElementById('presenter-layer')).zIndex) || null,
        box, name, visualSrc: img ? img.src : null, visualAsset: side.asset_id || null,
        board: boardShown ? { scroll: board.scrollHeight, client: board.clientHeight } : null,
        fit: cinematicStage.stats.lastFit, overflow: !!cinematicStage.stats.overflow };
}

const problems = [];
const silent = { pages: 0, stubbed: new Set(), flags: [] };
let browser = null;
let exportBrowser = null;
let phase = null;            // 'switch' while a style switch is in flight
const switchRequests = [];   // every request made while switching
try {
    const EXPECT = expectedFromStylesPy();
    const FAMILIES = EXPECT.families;
    const LOOK = EXPECT.looks;
    const LABEL_STYLES = FAMILIES.filter(f => LOOK[f].prefs.presenter_label === true);
    fs.writeFileSync(path.join(OUT, 'expected-styles.json'), JSON.stringify(EXPECT, null, 2));
    spawnSync(PYTHON, [path.join(REPO, 'tests', 'fixtures', 'cinematic_diagrams.py'), OUT]);
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const leafAsset = await upload(path.join(OUT, 'leaf_cross_section.png'), 'Leaf cross section showing palisade cells, spongy layer and stoma', ['leaf', 'cross', 'section', 'palisade', 'chloroplast']);
    await upload(path.join(OUT, 'photosynthesis_diagram.png'), 'Photosynthesis plant diagram with sunlight, water, carbon dioxide and oxygen', ['photosynthesis', 'plant', 'diagram', 'sunlight']);
    const pid = (await api('POST', '/save-history', LESSON)).data.id;
    const { chromium } = await loadPlaywright();
    const MAIN_ARGS = ['--mute-audio', '--disable-audio-output', '--autoplay-policy=no-user-gesture-required'];
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: MAIN_ARGS });
    silent.flags.push('main: ' + MAIN_ARGS.join(' '));

    const baseCine = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'auto', typography: 'academic', composer: 'rules', director: 'rules' };
    const TEACHER = { presenter_id: 'aadhi-teacher', mode: 'auto', position: 'right', style: 'friendly', fallback: 'none' };
    async function newPage(b, viewport, cine, presenter = TEACHER) {
        const context = await b.newContext({ viewport });
        await context.addInitScript(([t, c, pr]) => {
            if (!sessionStorage.getItem('seeded')) {
                localStorage.setItem('jwt_token', t);
                localStorage.setItem('aadhi.presenter', JSON.stringify(pr));
                localStorage.setItem('aadhi.cinematic', JSON.stringify(c));
                sessionStorage.setItem('seeded', '1');
            }
        }, [token, cine, presenter]);
        await context.addInitScript(silentPage);
        await context.addInitScript(holdProbe);
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error: ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console: ${m.text()}`); });
        page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
        page.on('requestfailed', r => { const err = (r.failure() || {}).errorText || ''; if (!/ERR_ABORTED/.test(err)) problems.push(`request failed (${err}): ${r.url()}`); });
        page.on('request', r => { if (phase === 'switch') switchRequests.push({ method: r.method(), url: r.url() }); });
        silent.pages += 1;
        return { context, page };
    }
    async function confirmSilent(page) {
        if (await page.evaluate(() => !window.speechSynthesis || /onstart/.test(String(window.speechSynthesis.speak)))) silent.stubbed.add(page);
    }
    async function openLesson(page, projectId) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    }
    async function startLesson(page, projectId, cinematic = true) {
        await openLesson(page, projectId);
        await confirmSilent(page);
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        if (cinematic) await page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
        await sleep(1500);
    }
    async function stopNarration(page) {
        await page.evaluate(() => { ttsState.isPlaying = false; try { speakNarration(null); } catch (e) { /* nothing playing */ } });
    }
    // Plays one scene with its narration to the end (the next scene is not started); the subtitles are measured while shown
    async function playScene(page, index, { read = true } = {}) {
        await page.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; currentSlide = n; renderSlide(n); }, index);
        await sleep(1600);
        const mid = read ? await page.evaluate(readabilityProbe, ['subtitles']) : [];
        await page.waitForFunction(() => window.slideSyncState && window.slideSyncState.audioFinished, null, { timeout: 40000 }).catch(() => {});
        await sleep(700);
        const facts = await page.evaluate(sceneFacts, index);
        facts.read = read ? mid.concat(await page.evaluate(readabilityProbe, null)) : [];
        return facts;
    }
    // A style picked in the settings panel (its button; the panel lives on the start screen, hidden while the lesson plays,
    // so the click is dispatched to the button itself); the page re-plans and redraws the scene
    async function pickStyle(page, family) {
        await stopNarration(page);
        phase = 'switch';
        const kept = page.waitForResponse(r => r.url().includes('/api/cinematic/style') && r.request().method() === 'POST'
            && (r.request().postData() || '').includes(`"${family}"`), { timeout: 30000 }).catch(() => null);
        await page.locator(`#cinematic-settings .style-option[data-style="${family}"]`).dispatchEvent('click');
        await page.waitForFunction(f => slides.every(s => !s.cinematic_plan || ((s.cinematic_plan.style || {}).look || {}).family === f)
            && document.body.getAttribute('data-cine-style') === f, family, { timeout: 30000 });
        const response = await kept;
        await sleep(900);
        phase = null;
        return response ? response.status() : null;
    }
    async function setOverride(page, key, value) {
        await page.locator(`#cinematic-settings select[data-override="${key}"]`).evaluate((el, v) => {
            el.value = v; el.dispatchEvent(new Event('change', { bubbles: true }));
        }, value);
        await sleep(150);
    }
    const shot = (page, name) => page.screenshot({ path: path.join(OUT, name) });

    const { page } = await newPage(browser, { width: 1280, height: 720 }, baseCine);

    // ---- 1. the settings panel offers the four styles, each with its own swatch ------------------------------------------------
    await section('1. the settings panel lists the four styles with a swatch each, the swatches differ', async () => {
        await page.goto(BASE + '/');
        await confirmSilent(page);
        await page.click('#nav-settings'); // (Phase 21: the start screen's settings are in Settings → Lesson defaults)
        await page.waitForSelector('#cinematic-settings .style-option', { timeout: 30000 });
        const options = await page.$$eval('#cinematic-settings .style-option', els => els.map(el => {
            const sw = el.querySelector('.style-swatch');
            const cs = sw ? getComputedStyle(sw) : null;
            return { id: el.getAttribute('data-style'), selected: el.getAttribute('data-selected'), name: (el.querySelector('.style-option-name') || {}).textContent || '',
                swatch: !!sw, bg: cs ? `${cs.backgroundColor} ${cs.backgroundImage}` : null, swVar: sw ? sw.style.getPropertyValue('--st-bg-1').trim() : null };
        }));
        await shot(page, 'settings-panel.png');
        await page.locator('#cinematic-settings .style-section').screenshot({ path: path.join(OUT, 'settings-styles-none.png') }).catch(() => {});
        // what the panel says: none chosen (the original look) / one chosen (overrides open, nothing else changed)
        const panelState = () => page.evaluate(() => ({
            selected: [...document.querySelectorAll('#cinematic-settings .style-option[data-selected="true"]')].map(e => e.getAttribute('data-style')),
            legacyNote: !!document.querySelector('#cinematic-settings .style-note-legacy'),
            disabled: [...document.querySelectorAll('#cinematic-settings select[data-override]')].filter(s => s.disabled).length,
            selects: document.querySelectorAll('#cinematic-settings select[data-override]').length,
            suggest: !!document.querySelector('#cinematic-settings .style-suggest .style-suggest-apply'),
            transitions: cinematicSettings.settings.transitions, motion: cinematicSettings.settings.motion }));
        const none = await panelState();
        await page.click('#cinematic-settings .style-option[data-style="academic"]');
        const academic = await panelState();
        // a style whose suggestion differs from the lesson's transition: suggested, applied only when asked
        await page.click('#cinematic-settings .style-option[data-style="children_education"]');
        const children = await panelState();
        await page.locator('#cinematic-settings .style-section').screenshot({ path: path.join(OUT, 'settings-styles-children.png') }).catch(() => {});
        await page.click('#cinematic-settings .style-suggest-apply');
        const applied = await panelState();
        await page.selectOption('#cinematic-transition', 'fade'); // back to the lesson's own transition
        await page.click('#cinematic-settings .style-option[data-style="academic"]');
        const ids = options.map(o => o.id);
        const distinct = new Set(options.map(o => o.bg)).size;
        check('1. the settings panel lists the four styles with a swatch each, the swatches differ',
            FAMILIES.every(f => ids.includes(f)) && options.length === 4 && options.every(o => o.swatch && o.name.trim() === LOOK[o.id].label)
            && options.every(o => norm(o.swVar) === norm(LOOK[o.id].css['--st-bg-1'])) && distinct === 4 && academic.selected.length === 1 && academic.selected[0] === 'academic',
            `${options.map(o => `${o.id}="${o.name}" swatch ${o.bg && o.bg.slice(0, 60)}`).join(' | ')}; ${distinct} distinct; clicked academic -> selected ${academic.selected.join(',')}`);
        const want = LOOK.children_education.prefs.transition;
        check('1b. no style chosen: nothing selected, the "original look" note, overrides disabled; picking a style changes only the style, its transition is suggested and applied on request',
            none.selected.length === 0 && none.legacyNote && none.selects >= 5 && none.disabled === none.selects
            && !academic.legacyNote && academic.disabled === 0 && academic.transitions === none.transitions && academic.motion === none.motion
            && children.transitions === none.transitions && children.suggest && applied.transitions === want,
            `none: selected [${none.selected}] note ${none.legacyNote} disabled ${none.disabled}/${none.selects}; academic: note ${academic.legacyNote} disabled ${academic.disabled}, transitions ${none.transitions}->${academic.transitions}, motion ${none.motion}->${academic.motion}; `
            + `children: transitions ${children.transitions}, suggestion ${children.suggest}; applied -> ${applied.transitions} (styles.py ${want})`);
    });

    // ---- 2-4, 11. the representative lesson in every style -----------------------------------------------------------------------
    const seen = {};
    const pickStatus = {};
    await section('2. the representative lesson renders in every style', async () => {
        await startLesson(page, pid);
        const legacy = await page.evaluate(sceneFacts, 0);
        const failures = {};
        for (const family of FAMILIES) {
            seen[family] = [];
            try {
                pickStatus[family] = await pickStyle(page, family);
            } catch (e) {
                phase = null;
                failures[family] = 'the page did not re-plan in this style: ' + firstLine(e);
                await shot(page, `${family}-switch-failed.png`);
            }
            for (const [i, kind] of KINDS.entries()) {
                try {
                    const facts = await playScene(page, i);
                    await shot(page, `${family}-${i + 1}-${kind}.png`);
                    facts.spread = spread(path.join(OUT, `${family}-${i + 1}-${kind}.png`));
                    seen[family].push(facts);
                } catch (e) {
                    failures[family] = (failures[family] ? failures[family] + '; ' : '') + `scene ${i + 1}: ${firstLine(e)}`;
                    seen[family].push({ scene: i, vars: {}, attrs: {}, read: [], error: e.message });
                }
            }
        }
        if (Object.keys(failures).length) console.log('      problems while rendering: ' + JSON.stringify(failures));
        fs.writeFileSync(path.join(OUT, 'seen-styles.json'), JSON.stringify({ legacy, seen, pickStatus, failures, switchRequests }, null, 2));
        const lines = [];
        const blankFrames = [];
        const ok = FAMILIES.map(family => {
            const expected = LOOK[family].css;
            const list = seen[family];
            const mismatched = new Set();
            const unsafe = new Set();
            list.forEach(f => {
                Object.keys(expected).forEach(k => { if (norm(f.vars[k]) !== norm(expected[k])) mismatched.add(k); });
                Object.entries(f.vars).forEach(([k, v]) => { if (!/^--st-[a-z0-9-]{1,40}$/.test(k) || /url|expression/i.test(v)) unsafe.add(k); });
            });
            const good = list.length === KINDS.length && list.every(f => f.style === family && f.family === family && f.template
                && f.attrs['data-cine-tone'] === LOOK[family].tone && norm(f.vars['--st-bg-1']) === norm(expected['--st-bg-1']) && f.current === f.scene);
            const blank = list.map((f, i) => (f.spread !== undefined && f.spread < 6 ? `${family}-${i + 1}-${KINDS[i]}.png (spread ${f.spread})` : null)).filter(Boolean);
            blankFrames.push(...blank);
            lines.push(`${family}: ${list.filter(f => f.style === family).length}/${KINDS.length} scenes with data-cine-style, --st-bg-1 ${list.map(f => f.vars['--st-bg-1'])[0]} (styles.py ${expected['--st-bg-1']}), `
                + `${mismatched.size} token mismatches${mismatched.size ? ' [' + [...mismatched].slice(0, 4).map(k => `${k}=${list[0].vars[k]} vs ${expected[k]}`).join('; ') + ']' : ''}, kept ${pickStatus[family]}`
                + `${blank.length ? `, EMPTY FRAMES ${blank.join(', ')}` : ''}`);
            return good && mismatched.size === 0 && unsafe.size === 0 && pickStatus[family] === 200 && blank.length === 0;
        }).every(Boolean);
        check('2. the representative lesson renders in every style: body[data-cine-style], the tone and every --st-* token are styles.py\'s, a non-empty screenshot per scene',
            ok, `${lines.join(' | ')}; templates ${seen[FAMILIES[0]].map(f => f.template).join(',')}`);

        // ---- 3. readability ---------------------------------------------------------------------------------------------------
        const perStyle = FAMILIES.map(family => {
            const items = seen[family].flatMap((f, i) => f.read.map(r => ({ ...r, scene: `${i + 1}-${KINDS[i]}` })));
            const measured = items.filter(r => r.ratio !== undefined);
            const bad = measured.filter(r => !r.ok);
            const minOf = groups => { const g = measured.filter(r => groups.includes(r.group)); return g.length ? g.reduce((a, b) => (b.ratio < a.ratio ? b : a)) : null; };
            const body = minOf(['body', 'title-subtitle', 'label', 'subtitles', 'presenter-name']);
            const large = minOf(['heading', 'title']);
            const subs = measured.filter(r => r.group === 'subtitles').length;
            const skipped = items.filter(r => r.skipped);
            return { family, measured: measured.length, bad, body, large, subs, skipped };
        });
        fs.writeFileSync(path.join(OUT, 'readability.json'), JSON.stringify(perStyle, null, 2));
        const desc = r => (r ? `${r.ratio} (${r.group} "${r.text}" ${r.fg} on ${r.bg}, scene ${r.scene})` : '-');
        check('3. every text reads in every style: body text >= 4.5:1, titles and headings >= 3:1 (measured on screen)',
            perStyle.every(p => p.measured > 0 && p.bad.length === 0),
            perStyle.map(p => `${p.family}: ${p.measured} texts (${p.subs} subtitle), body min ${desc(p.body)}, titles min ${desc(p.large)}`
                + `${p.bad.length ? `, ${p.bad.length} below: ${p.bad.slice(0, 3).map(desc).join('; ')}` : ''}${p.skipped.length ? `, ${p.skipped.length} not measurable` : ''}`).join(' | '));

        // ---- 4. switching styles generates nothing; the library visual stays ---------------------------------------------------
        const generated = switchRequests.filter(r => isGeneration(r.method, r.url));
        const kinds = [...new Set(switchRequests.map(r => `${r.method} ${new URL(r.url).pathname.replace(/\/[0-9a-f]{32}(?=\/|$)/g, '/{id}')}`))];
        // the narration's audio and speech timeline (cached on the server by text and voice) are not media generation: listed apart
        const narration = switchRequests.filter(r => /\/generate-audio|\/api\/presenters\/speech/.test(r.url)).length;
        const srcs = FAMILIES.map(f => seen[f][AT.diagram].visualSrc);
        const assets = FAMILIES.map(f => seen[f][AT.diagram].visualAsset);
        const srcPath = s => (s ? new URL(s).pathname : null);
        check('4. switching styles four times re-renders without generating media (0 generation requests) and the diagram stays the same library asset',
            generated.length === 0 && srcs.every(s => s && srcPath(s) === srcPath(srcs[0]) && srcPath(s).includes(leafAsset)) && assets.every(a => a === leafAsset),
            `${generated.length} generation requests${generated.length ? ': ' + generated.slice(0, 3).map(r => r.url).join(', ') : ''}; requests while switching: ${kinds.join(', ')} (${narration} of them the narration's cached audio / speech timeline); `
            + `img ${srcPath(srcs[0]) || '-'} in ${srcs.filter(s => s && srcPath(s) === srcPath(srcs[0])).length}/4 styles, plan asset ${assets.every(a => a === leafAsset) ? 'the library leaf in all 4' : assets.join(',')}`);

        // ---- 11. the presenter name card follows the style ---------------------------------------------------------------------
        const nameLines = [];
        const namesOk = FAMILIES.map(family => {
            const withPresenter = seen[family].filter(f => f.presenterShown && f.box);
            const want = LABEL_STYLES.includes(family);
            // inside the presenter layer (which carries the presenter's box and the camera), at its bottom centre
            const placed = n => {
                const r = n.rect, l = n.layer;
                if (!l || !n.inLayer) return false;
                const centred = Math.abs((r.x + r.w / 2) - (l.x + l.w / 2)) <= 6;
                const within = r.x >= l.x - 2 && r.x + r.w <= l.x + l.w + 2 && r.y >= l.y - 2 && r.y + r.h <= l.y + l.h + 2;
                const low = r.y + r.h >= l.y + l.h * 0.8;
                return centred && within && low;
            };
            const good = withPresenter.filter(f => {
                const n = f.name;
                if (!want) return !n || (!n.shown && !n.strays);
                return n && n.shown && !n.strays && n.text.trim() === 'Aadhi Teacher' && placed(n);
            });
            const sample = withPresenter.find(f => f.name && f.name.shown) || withPresenter.find(f => f.name);
            nameLines.push(`${family} (${want ? 'label' : 'no label'}): ${good.length}/${withPresenter.length} presenter scenes right`
                + (sample && sample.name ? ` e.g. "${sample.name.text}" ${sample.name.inLayer ? 'in' : 'OUTSIDE'} #presenter-layer at ${JSON.stringify(sample.name.rect)}, layer ${JSON.stringify(sample.name.layer)}` : ''));
            return withPresenter.length > 0 && good.length === withPresenter.length;
        }).every(Boolean);
        check(`11. the presenter name card shows "Aadhi Teacher" at the bottom centre of the presenter layer in ${LABEL_STYLES.join(' and ')}, and nowhere in the other styles`,
            namesOk, nameLines.join(' | '));
    });

    // ---- 6. overrides through the panel's selects -------------------------------------------------------------------------------
    await section('6. overrides (accent teal, large text, plain background) change the tokens; the board still fits', async () => {
        const family = await page.evaluate(() => document.body.getAttribute('data-cine-style'));
        const want = LOOK[family].override_css;
        await stopNarration(page);
        phase = 'switch';
        await setOverride(page, 'accent', 'teal');
        await setOverride(page, 'text_size', 'large');
        await setOverride(page, 'background', 'plain');
        const applied = await page.waitForFunction(w => {
            const st = n => document.documentElement.style.getPropertyValue(n).trim();
            const o = ((slides[currentSlide].cinematic_plan || {}).style || {}).look;
            return o && o.overrides && o.overrides.accent === 'teal' && o.overrides.text_size === 'large' && o.overrides.background === 'plain'
                && st('--st-accent').toLowerCase() === w.accent.toLowerCase() && st('--st-text-scale') === w.scale;
        }, { accent: want['--st-accent'], scale: want['--st-text-scale'] }, { timeout: 30000 }).then(() => true).catch(() => false);
        await sleep(900);
        phase = null;
        const facts = [];
        for (const [i, kind] of KINDS.entries()) {
            const f = await playScene(page, i, { read: false });
            if (['definition', 'process', 'comparison', 'summary'].includes(kind)) await shot(page, `override-${family}-${i + 1}-${kind}.png`);
            facts.push(f);
        }
        const st = facts.map(f => ({ accent: f.vars['--st-accent'], scale: f.vars['--st-text-scale'], glow1: f.vars['--st-bg-glow-1'], glow2: f.vars['--st-bg-glow-2'], board: f.board, overflow: f.overflow, fit: f.fit }));
        const fits = facts.every(f => !f.board || f.board.scroll <= f.board.client + 2);
        const tokensOk = st.every(s => norm(s.accent) === norm(want['--st-accent']) && s.scale === want['--st-text-scale'] && norm(s.glow1) === 'transparent' && norm(s.glow2) === 'transparent');
        const allTokens = facts.every(f => Object.keys(want).every(k => norm(f.vars[k]) === norm(want[k])));
        check('6. overrides through the panel (accent teal, text size large, background plain) change --st-accent and --st-text-scale, remove the glows, and every board still fits',
            applied && tokensOk && allTokens && fits,
            `${family}: --st-accent ${st[0].accent} (styles.py ${want['--st-accent']}), --st-text-scale ${st[0].scale} (${want['--st-text-scale']}), glows ${st[0].glow1}/${st[0].glow2}; `
            + `boards ${facts.map((f, i) => (f.board ? `${KINDS[i]}:${f.board.scroll}/${f.board.client}` : `${KINDS[i]}:-`)).join(' ')}; fit ${facts.map(f => f.fit).join(',')}`);
        // back to the style's own values
        await stopNarration(page);
        for (const key of ['accent', 'text_size', 'background']) await setOverride(page, key, 'default');
        await page.waitForFunction(() => { const o = ((slides[currentSlide].cinematic_plan || {}).style || {}).look; return o && o.overrides && !Object.keys(o.overrides).length; }, null, { timeout: 30000 }).catch(() => {});
        await sleep(900);
    });

    // ---- 7. the style is kept with the lesson and restored when it is opened ---------------------------------------------------
    let savedPid = null;
    await section('7. the style is kept with the lesson (in place) and restored on open', async () => {
        const status = await pickStyle(page, 'children_education');
        const inPlace = (await api('GET', `/api/projects/${pid}`)).data || {};
        check('7. picking a style keeps it with the open lesson (POST /api/cinematic/style, the same lesson)',
            status === 200 && inPlace.cinematic_style && inPlace.cinematic_style.style === 'children_education' && inPlace.cinematic_style.style_version === 1,
            `POST ${status}; GET /api/projects/${pid} cinematic_style ${JSON.stringify(inPlace.cinematic_style || null)}`);
        // a save from the page (/save-history, as the export does for a lesson not saved yet) carries the style too
        savedPid = await page.evaluate(async () => { currentProjectId = null; return ensureExportProject(); });
        const saved = (await api('GET', `/api/projects/${savedPid}`)).data || {};
        // the browser's own setting says another style: the lesson's must win when it is opened
        await page.evaluate(() => { const s = JSON.parse(localStorage.getItem('aadhi.cinematic') || '{}'); s.style = 'academic'; s.style_overrides = {}; localStorage.setItem('aadhi.cinematic', JSON.stringify(s)); });
        await stopNarration(page);
        await openLesson(page, savedPid);
        const restored = await page.evaluate(() => ({ setting: cinematicSettings.settings.style,
            selected: [...document.querySelectorAll('#cinematic-settings .style-option[data-selected="true"]')].map(e => e.getAttribute('data-style')) }));
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
        await sleep(1500);
        const facts = await playScene(page, AT.definition, { read: false });
        await shot(page, 'restored-children_education-2-definition.png');
        check('7b. a page save (/save-history) stores cinematic_style; reloading the project restores it (settings, selected option, body[data-cine-style])',
            saved.cinematic_style && saved.cinematic_style.style === 'children_education' && restored.setting === 'children_education'
            && restored.selected.length === 1 && restored.selected[0] === 'children_education' && facts.style === 'children_education',
            `saved #${savedPid} ${JSON.stringify(saved.cinematic_style || null)}; after reload: setting ${restored.setting}, selected ${restored.selected.join(',')}, body ${facts.style}`);
    });

    // ---- 10. Visual Review shows the style; a scene accent stays on its scene -------------------------------------------------
    await section('10. Visual Review shows the Style block and a scene accent override stays on that scene', async () => {
        if (!savedPid) throw new Error('no saved lesson from check 7');
        await stopNarration(page);
        await openLesson(page, savedPid);
        await page.evaluate(() => planLessonVisuals());
        await page.click('#start-review-btn');
        await page.waitForSelector('.review-overlay.open .review-item[data-slot="composition"]', { timeout: 30000 });
        await page.click(`.review-overlay.open .review-item[data-slot="composition"][data-scene="${AT.definition}"]`);
        await page.waitForSelector('.review-overlay.open .review-style', { timeout: 15000 });
        const block = (await page.textContent('.review-overlay.open .review-style')).replace(/\s+/g, ' ').trim();
        await shot(page, 'review-style.png');
        // the scene's composition approved first: a style-only change must keep that approval
        const reviewOf = async () => ((((await api('GET', `/api/projects/${savedPid}`)).data || {}).scenes || [])[AT.definition] || {}).visual_review || {};
        await page.click('.review-overlay.open .review-actions [data-action="keep"]');
        let approvedBefore = null;
        for (let i = 0; i < 30 && approvedBefore !== 'approved'; i++) { await sleep(500); approvedBefore = ((await reviewOf()).composition || {}).status || null; }
        await page.click('.review-overlay.open .review-actions [data-action="change"]');
        await page.selectOption('.review-overlay.open #composition-style_accent', 'teal');
        await page.click('.review-overlay.open .review-composition-change [data-action="apply"]');
        await page.waitForFunction(() => /this scene's style|Changed/i.test((document.querySelector('.review-overlay.open .review-status') || {}).textContent || ''), null, { timeout: 20000 });
        await sleep(600);
        const after = (await page.textContent('.review-overlay.open .review-style').catch(() => '')).replace(/\s+/g, ' ').trim();
        const status = (await page.textContent('.review-overlay.open .review-status')).trim();
        await shot(page, 'review-style-teal.png');
        const scenes = ((await api('GET', `/api/projects/${savedPid}`)).data || {}).scenes || [];
        const review = ((scenes[AT.definition] || {}).visual_review || {}).composition || {};
        const lookAt = i => ((scenes[i] && scenes[i].cinematic_plan && scenes[i].cinematic_plan.style) || {}).look || null;
        const mine = lookAt(AT.definition);
        const others = scenes.map((_, i) => i).filter(i => i !== AT.definition && lookAt(i));
        const family = 'children_education';
        const teal = LOOK[family].scene_teal_css['--st-accent'];
        const lessonAccent = LOOK[family].css['--st-accent'];
        const savedOk = mine && JSON.stringify(mine.scene_overrides) === JSON.stringify({ accent: 'teal' }) && norm(mine.css['--st-accent']) === norm(teal)
            && others.length >= 5 && others.every(i => JSON.stringify(lookAt(i).scene_overrides || {}) === '{}' && norm(lookAt(i).css['--st-accent']) === norm(lessonAccent));
        await page.click('.review-panel .export-close').catch(() => {});
        // on screen: only that scene takes the teal accent
        await startLesson(page, savedPid);
        const onDef = await playScene(page, AT.definition, { read: false });
        const onNext = await playScene(page, AT.process, { read: false });
        await shot(page, 'scene-accent-teal-check.png');
        check('10. Visual Review shows the Style block with the style\'s label; a scene accent (teal) from the Change form becomes look.scene_overrides for that scene only, and the scene stays approved',
            block.includes(LOOK[family].label) && savedOk && /teal/i.test(after) && norm(onDef.vars['--st-accent']) === norm(teal) && norm(onNext.vars['--st-accent']) === norm(lessonAccent)
            && approvedBefore === 'approved' && review.status === 'approved' && (review.overrides || {}).style_accent === 'teal',
            `block "${block.slice(0, 120)}"; after "${after.slice(0, 140)}"; status "${status.slice(0, 80)}"; review ${approvedBefore} -> ${review.status} ${JSON.stringify(review.overrides || {})}; saved scene ${AT.definition} ${JSON.stringify(mine && mine.scene_overrides)} `
            + `--st-accent ${mine && mine.css['--st-accent']} (styles.py ${teal}); other scenes ${others.filter(i => JSON.stringify(lookAt(i).scene_overrides || {}) === '{}').length}/${others.length} without, `
            + `accent ${others.length ? lookAt(others[0]).css['--st-accent'] : '-'} (${lessonAccent}); on screen ${onDef.vars['--st-accent']} then ${onNext.vars['--st-accent']}`);
    });

    // ---- 8. an old lesson keeps today's look -------------------------------------------------------------------------------------
    const oldPid = (await api('POST', '/save-history', LESSON)).data.id;
    await section('8. an old lesson (no cinematic_style, no style setting) renders in today\'s look', async () => {
        // a browser that kept an override from elsewhere but no style: a lesson without a style ignores lesson overrides
        const old = await newPage(browser, { width: 1280, height: 720 }, { ...baseCine, style_overrides: { accent: 'teal' } });
        await startLesson(old.page, oldPid);
        const f = await playScene(old.page, AT.definition, { read: false });
        const look = await old.page.evaluate(() => {
            const board = document.getElementById('presentation-board');
            const bcs = getComputedStyle(board);
            const title = document.querySelector('.cine-title-text');
            const plan = slides[1].cinematic_plan;
            return { setting: cinematicSettings.settings.style, bg: bcs.backgroundImage, border: bcs.borderTopColor, title: title ? getComputedStyle(title).color : null,
                overrides: plan && plan.style && plan.style.look ? plan.style.look.overrides : null,
                selected: document.querySelectorAll('#cinematic-settings .style-option[data-selected="true"]').length,
                legacyNote: !!document.querySelector('#cinematic-settings .style-note-legacy'),
                disabled: [...document.querySelectorAll('#cinematic-settings select[data-override]')].every(s => s.disabled) };
        });
        // the server too: overrides without a style change nothing
        const planned = (await api('POST', '/api/cinematic/plan', { scenes: [LESSON.scenes[AT.definition]],
            settings: { ...baseCine, style: null, style_overrides: { accent: 'teal' } } })).data || {};
        const serverLook = (((planned.plans || [])[0] || {}).style || {}).look || {};
        await shot(old.page, 'legacy-2-definition.png');
        const intro = await playScene(old.page, AT.intro, { read: false });
        await shot(old.page, 'legacy-1-intro.png');
        const legacyVarsOk = Object.keys(f.vars).length === 0 || Object.keys(EXPECT.legacy).every(k => norm(f.vars[k]) === norm(EXPECT.legacy[k]));
        check('8. an old lesson (saved without cinematic_style, no style in the settings) renders exactly in today\'s look',
            (f.style === null || f.style === 'cinematic_education') && look.setting === null
            && look.bg === 'linear-gradient(160deg, rgba(40, 19, 82, 0.92), rgba(19, 10, 42, 0.92))' && look.border === 'rgba(255, 215, 0, 0.22)'
            && look.title === 'rgb(255, 255, 255)' && legacyVarsOk && intro.style === f.style
            && JSON.stringify(look.overrides || {}) === '{}' && norm(f.vars['--st-accent'] || '#FFD700') === norm(EXPECT.legacy['--st-accent'])
            && look.selected === 0 && look.legacyNote && look.disabled
            && JSON.stringify(serverLook.overrides || {}) === '{}' && norm((serverLook.css || {})['--st-accent']) === norm(EXPECT.legacy['--st-accent']),
            `body[data-cine-style]=${f.style}; setting ${look.setting}; board ${look.bg}; border ${look.border}; title ${look.title}; ${Object.keys(f.vars).length} --st-* (legacy tokens ${legacyVarsOk ? 'equal today\'s' : 'differ'}); `
            + `stale teal override: page ${JSON.stringify(look.overrides)} accent ${f.vars['--st-accent']}, server ${JSON.stringify(serverLook.overrides)} accent ${(serverLook.css || {})['--st-accent']}; `
            + `panel: ${look.selected} selected, original-look note ${look.legacyNote}, overrides disabled ${look.disabled}`);
        await old.context.close();
    });

    // ---- 9. Classic is unchanged ---------------------------------------------------------------------------------------------------
    await section('9. Classic mode is unchanged', async () => {
        const classic = await newPage(browser, { width: 1280, height: 720 }, { ...baseCine, mode: 'classic' });
        await startLesson(classic.page, oldPid, false);
        await classic.page.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = false; currentSlide = n; renderSlide(n); }, AT.definition);
        await sleep(2500);
        const c = await classic.page.evaluate(() => {
            const root = document.documentElement;
            const inline = [];
            for (let i = 0; i < root.style.length; i++) if (root.style[i].startsWith('--st-')) inline.push(root.style[i]);
            const cs = getComputedStyle(root);
            const attrs = [...document.body.attributes].map(a => a.name).filter(n => n.startsWith('data-cine'));
            return { styleAttr: document.body.getAttribute('data-cine-style'), cinematic: document.body.getAttribute('data-cinematic'), inline,
                computed: ['--st-bg-1', '--st-surface-1', '--st-text', '--st-accent'].map(n => cs.getPropertyValue(n).trim()).filter(Boolean), attrs,
                plans: slides.filter(s => s.cinematic_plan).length };
        });
        await shot(classic.page, 'classic-2-definition.png');
        check('9. Classic mode is unchanged: no data-cine-style (nor any style attribute), no --st-* property on <html>',
            c.styleAttr === null && c.cinematic === null && c.inline.length === 0 && c.computed.length === 0 && c.attrs.length === 0,
            `data-cine-style ${c.styleAttr}; data-cine* ${c.attrs.join(',') || 'none'}; inline --st-* ${c.inline.length}; computed ${c.computed.join(',') || 'none'}; plans ${c.plans}`);
        await classic.context.close();
    });

    // ---- 11b. never a name card for Aadhi (the mascot), even in a style that shows one -----------------------------------------
    await section('11b. no presenter name card for Aadhi (the mascot)', async () => {
        if (!savedPid) throw new Error('no Children\'s Education lesson from check 7');
        const mascot = await newPage(browser, { width: 1280, height: 720 }, baseCine, { ...TEACHER, presenter_id: 'aadhi' });
        await startLesson(mascot.page, savedPid);
        const facts = [];
        for (const i of [AT.intro, AT.definition, AT.explanation]) facts.push(await playScene(mascot.page, i, { read: false }));
        await shot(mascot.page, 'mascot-children_education-8-explanation.png');
        const shownMascot = facts.filter(f => f.presenterShown);
        const types = await mascot.page.evaluate(ids => ids.map(i => (slides[i].cinematic_plan.presenter || {}).type), [AT.intro, AT.definition, AT.explanation]);
        check('11b. Aadhi (the mascot) never gets a name card, even in Children\'s Education (presenter_label)',
            facts.every(f => f.style === 'children_education') && shownMascot.length > 0 && types.includes('mascot') && facts.every(f => !f.name || (!f.name.shown && !f.name.strays)),
            `styles ${facts.map(f => f.style).join(',')}; presenter types ${types.join(',')}; ${shownMascot.length} scenes with Aadhi shown; cards ${facts.map(f => (f.name ? (f.name.shown ? 'SHOWN "' + f.name.text + '"' : 'hidden') : 'none')).join(',')}`);
        await mascot.context.close();
    });

    await browser.close();
    browser = null;

    // ---- 5. preview and export match in a light and a dark style ------------------------------------------------------------------
    const EXPORT_ARGS = ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'];
    exportBrowser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'], args: EXPORT_ARGS });
    silent.flags.push('export: ' + EXPORT_ARGS.join(' ') + ' + suppressLocalAudioPlayback');
    const exported = {};
    // as when the user took the style's suggestions (.style-suggest-apply): its preferred transition and camera in the settings
    const prefilled = family => ({ ...baseCine, style: family, style_version: 1, transitions: LOOK[family].prefs.transition,
        motion: LOOK[family].prefs.camera === 'still' ? 'none' : 'subtle' });
    const transitionOf = family => LOOK[family].prefs.transition;
    // a light and a dark style (the frame comparison), and Children's Education for its soft fade in the recording
    for (const family of ['academic', 'corporate_training', 'children_education']) {
        await section(`5. preview and export match (${family})`, async () => {
            const left = BUDGET_MS - (Date.now() - STARTED);
            if (left < 120000) throw new Error(`not enough time left for the export (${Math.round(left / 1000)} s)`);
            const shortPid = (await api('POST', '/save-history', { ...SHORT, cinematic_style: { style: family, style_version: 1, style_overrides: {} } })).data.id;
            const ex = await newPage(exportBrowser, { width: 1280, height: 720 }, prefilled(family));
            const p = ex.page;
            await startLesson(p, shortPid);
            const preview = await playScene(p, SHORT_CODE, { read: false });
            const previewFile = path.join(OUT, `preview-${family}-code-end.png`);
            await p.screenshot({ path: previewFile });
            await stopNarration(p);
            await openLesson(p, shortPid);
            await p.evaluate(() => planLessonVisuals());
            await p.click('#start-videos-btn');
            await p.click('.export-start-btn');
            const limit = Math.min(300000, left - 60000);
            for (;;) {
                const start = p.locator('.export-actions button', { hasText: 'Start Recording' });
                const anyway = p.locator('.export-actions button', { hasText: 'Record anyway' });
                await Promise.race([start.waitFor({ timeout: limit }), anyway.waitFor({ timeout: limit })]);
                if (await anyway.isVisible()) { await anyway.click(); continue; }
                await start.click();
                break;
            }
            const recordEnd = Date.now() + limit;
            let outcome = null;
            const stylesDuring = new Set();
            while (!outcome && Date.now() < recordEnd) {
                await sleep(700);
                const s = await p.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden, recording: exportFlow.recording,
                    style: document.body.getAttribute('data-cine-style'),
                    error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
                if (s.recording && s.style) stylesDuring.add(s.style);
                if (s.ready) outcome = 'ready';
                else if (s.error) outcome = 'error: ' + s.error;
            }
            const job = await p.evaluate(() => exportFlow.job);
            const exportDir = path.join(data, 'exports');
            const userDir = job && fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(dd => fs.existsSync(path.join(exportDir, dd, `${job.id}.webm`))) : null;
            const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
            const sceneLog = await p.evaluate(() => window.exportSceneLog || []);
            const next = sceneLog[SHORT_CODE + 1];
            let diff = null, fullDiff = null, dominant = null, nearest = null, nearDefault = null;
            const exportFile = path.join(OUT, `export-${family}-code-end.png`);
            if (stored && next) {
                spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', Math.max(0, next.t - 0.6).toFixed(2), '-i', stored, '-frames:v', '1', exportFile]);
                if (fs.existsSync(exportFile)) {
                    const a = grid(previewFile);
                    const b = grid(exportFile);
                    // the lesson frame without the player chrome (the preview's control bar and progress line, hidden while recording):
                    // the 32x18 grid without its two bottom rows
                    const lesson = 16 * 32 * 3;
                    diff = +difference(a.slice(0, lesson), b.slice(0, lesson)).toFixed(1);
                    fullDiff = +difference(a, b).toFixed(1);
                    // the frame's dominant colour (the most common 16-level colour bin of the 32x18 grid)
                    const bins = new Map();
                    for (let i = 0; i < b.length; i += 3) {
                        const key = [b[i], b[i + 1], b[i + 2]].map(v => v >> 4).join(',');
                        const e = bins.get(key) || { n: 0, sum: [0, 0, 0] };
                        e.n += 1; e.sum = e.sum.map((v, k) => v + b[i + k]);
                        bins.set(key, e);
                    }
                    const top = [...bins.values()].sort((x, y) => y.n - x.n)[0];
                    dominant = top.sum.map(v => v / top.n);
                    const palette = fam => {
                        const css = LOOK[fam].css;
                        const bg2 = rgbOf(css['--st-bg-2']);
                        return ['--st-bg-1', '--st-bg-2', '--st-bg-3', '--st-bg-solid', '--st-backdrop', '--st-scrim', '--st-surface-1', '--st-surface-2']
                            .map(k => { const c = rgbOf(css[k]); return c ? { k, c: c[3] < 1 ? overRgb(c, bg2) : c } : null; }).filter(Boolean);
                    };
                    const near = fam => palette(fam).map(x => ({ k: x.k, d: dist(dominant, x.c) })).sort((x, y) => x.d - y.d)[0];
                    nearest = near(family);
                    nearDefault = near(EXPECT.default);
                }
            }
            exported[family] = { outcome, diff, dominant: dominant && hexOf(dominant), nearest, nearDefault, stylesDuring: [...stylesDuring], preview: preview.style };
            check(`5. preview and export match in ${family} (${LOOK[family].tone}): the same frame of the code scene (difference < 12) and the exported frame in the style's colours`,
                outcome === 'ready' && preview.style === family && diff !== null && diff < 12 && nearest && nearest.d <= 48
                && (family === EXPECT.default || nearest.d < nearDefault.d) && [...stylesDuring].every(s => s === family) && stylesDuring.size === 1,
                `${outcome}; preview body ${preview.style}; recording body ${[...stylesDuring].join(',') || '-'}; frame difference ${diff} (whole frame with the player controls ${fullDiff}); dominant ${dominant ? hexOf(dominant) : '-'} `
                + `nearest ${nearest ? `${nearest.k} ${LOOK[family].css[nearest.k]} (distance ${nearest.d.toFixed(0)})` : '-'}, today's look ${nearDefault ? nearDefault.d.toFixed(0) : '-'}; ${path.basename(exportFile)}`);
            // the recorded video at every change between lesson scenes: no flash, i.e. no single-colour frame brighter than both
            // scenes (the "fade" transition dips through the page's black on purpose: darker single-colour frames are that dip)
            const changes = [];
            if (stored) {
                for (let k = 1; k < sceneLog.length; k++) {
                    const frames = framesAround(stored, sceneLog[k].t);
                    if (frames.length < 10) { changes.push({ scene: k, t: sceneLog[k].t, frames: frames.length, flashes: [], dip: null }); continue; }
                    const median = list => list.map(f => f.mean).sort((a, b) => a - b)[Math.floor(list.length / 2)];
                    const brighter = Math.max(median(frames.slice(0, 3)), median(frames.slice(-3)));
                    const flashes = frames.filter(f => f.spread < 6 && f.mean > brighter + 8);
                    const dip = frames.reduce((a, f) => (!a || f.mean < a.mean ? f : a), null);
                    const worst = flashes.length ? flashes.reduce((a, f) => (f.mean > a.mean ? f : a)) : dip;
                    const evidence = path.join(OUT, `export-${family}-change-${k}-${flashes.length ? 'flash' : 'darkest'}.png`);
                    spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', worst.t.toFixed(2), '-i', stored, '-frames:v', '1', evidence]);
                    changes.push({ scene: k, t: sceneLog[k].t, frames: frames.length, scenesMean: Math.round(brighter), flashes: flashes.map(f => `${f.t}s ${f.colour}`),
                        dip: { t: dip.t, colour: dip.colour, spread: dip.spread }, evidence: path.basename(evidence) });
                }
            }
            exported[family].changes = changes;
            check(`5b. the ${family} export (${transitionOf(family)}) has no white or grey flash at a scene change (single-colour frames only as the dip through black)`,
                stored && changes.length >= 2 && changes.every(c => c.frames >= 10 && c.flashes.length === 0),
                changes.map(c => `scene ${c.scene} at ${c.t.toFixed(1)}s: ${c.frames} frames, ${c.flashes.length ? `FLASH ${c.flashes.slice(0, 4).join(' ')} (scenes ~${c.scenesMean})` : `darkest ${c.dip ? `${c.dip.colour} at ${c.dip.t}s` : '-'}`} (${c.evidence || '-'})`).join(' | ') || 'no recording');
            await ex.context.close();
        });
    }
    fs.writeFileSync(path.join(OUT, 'exports.json'), JSON.stringify(exported, null, 2));

    // ---- 12. no errors; silent -----------------------------------------------------------------------------------------------------
    check('12. no unexpected page errors or failed requests', problems.length === 0, problems.slice(0, 5).join(' | '));
    check('12. every browser was silent (audio output disabled, speech stubbed on every page, capture without local playback)',
        silent.stubbed.size === silent.pages && silent.pages > 0 && silent.flags.length === 2 && silent.flags.every(f => f.includes('--disable-audio-output')) && silent.flags[0].includes('--mute-audio'),
        `${silent.stubbed.size}/${silent.pages} pages stubbed; ${silent.flags.join(' | ')}`);
} catch (e) {
    check('check ran to the end', false, e.stack || e.message);
} finally {
    if (browser) await browser.close().catch(() => {});
    if (exportBrowser) await exportBrowser.close().catch(() => {});
    killServer();
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Artifacts: ${OUT}`);
process.exit(failed.length ? 1 : 0);
