// Cinematic scenes check (Phase 13) in real Chrome against a throwaway server with the local stand-ins only
// (AI_FAKE_PROVIDER=1: stand-in image provider; FAKE_TTS=1: a local speech-like tone). No real provider, no network.
//
//   1. Presenter + visual: presenter right, visual left, previewed and played (the plan, the measured boxes,
//      entrances, labels, a camera that moves as one picture)
//   2. Formula: typeset by MathJax (not rewritten), the camera focuses on it, the presenter secondary, labels at
//      their [SYNC] reveal
//   3. Visual only: the presenter hidden, the visual large, the camera focusing where the safety rules allow
//   4. Transitions: the lesson's fade between scenes; a cut has no transition at all
//   5. Export: previewed, exported, the output exists, and a recorded frame has the preview's composition
//   Also: scenes A-F (the visual quality gate) at 1280x720 and 1920x1080 with nothing overlapping; Classic
//   unchanged; the scene inspector in Visual Review (keep, change, stale after the presenter moved); reduced motion;
//   an AI background generated only when asked; Aadhi's covered studio clip paused; page frame rate.
//
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/cinematic_browser_check.mjs
// Silent: no audio output, browser speech stubbed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.CINEMATIC_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-cinematic-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.CINEMATIC_CHECK_PORT || 9960 + (process.pid % 15));
const BASE = `http://127.0.0.1:${PORT}`;
if (!PASSWORD) {
    console.error('Set AADHI_PASSWORD to the default admin password (a fresh database creates that admin).');
    process.exit(2);
}

const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: !!ok, detail });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`);
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
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `acc-${process.pid}`), JWT_SECRET: 'cinematic-check-' + Math.random().toString(36).slice(2),
            AI_FAKE_PROVIDER: '1', FAKE_TTS: '1', AI_GENERATION_ENABLED: '1', AI_FAKE_STATE_DIR: path.join(data, 'jobs'), AI_MEDIA_LOG: '1',
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
async function upload(file, type, description, keywords) {
    const form = new FormData();
    form.append('file', new Blob([fs.readFileSync(file)], { type }), path.basename(file));
    const asset = (await api('POST', '/api/assets', form, true)).data.asset;
    if (description) await api('PATCH', `/api/assets/${asset.id}`, { description, keywords });
    return asset.id;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
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
    // The transitions used between scenes (test 4)
    window.__transitions = [];
    const start = document.startViewTransition && document.startViewTransition.bind(document);
    if (start) document.startViewTransition = cb => { window.__transitions.push(document.documentElement.getAttribute('data-cine-transition')); return start(cb); };
}
function ffmpeg(...args) {
    const r = spawnSync('ffmpeg', ['-v', 'error', '-y', ...args], { encoding: 'buffer' });
    if (r.status !== 0) throw new Error(String(r.stderr));
    return r.stdout;
}
// A frame as a small grid of colours, for comparing compositions (not pixels)
function grid(file, w = 32, h = 18) {
    return [...ffmpeg('-i', file, '-vf', `scale=${w}:${h}:flags=area`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')];
}
function difference(a, b) {
    let sum = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i++) sum += Math.abs(a[i] - b[i]);
    return sum / Math.min(a.length, b.length);
}
function region(g, x, y, w, h, gw = 32, gh = 18) { // mean colour of a normalized region of a grid
    const px = [];
    for (let j = Math.floor(y * gh); j < Math.ceil((y + h) * gh); j++) for (let i = Math.floor(x * gw); i < Math.ceil((x + w) * gw); i++) px.push(g.slice((j * gw + i) * 3, (j * gw + i) * 3 + 3));
    return [0, 1, 2].map(c => Math.round(px.reduce((s, p) => s + p[c], 0) / px.length));
}

const lesson = (diagram, leaf, clip) => ({ subject_name: 'Cinematic check', session_title: 'Photosynthesis', scenes: [
    { type: 'content', title: 'Photosynthesis', subtitle: 'How plants make their own food', html: '<p>Plants turn sunlight, water and air into sugar.</p>',
      narration: 'Welcome! Today we find out how plants make their own food.' },
    { type: 'content', title: 'Inside a plant', html: '<ul><li>Sunlight is absorbed by the leaves</li><li>Water rises from the roots</li><li>Glucose is made</li></ul>',
      visual: { concept: 'photosynthesis plant diagram', description: 'A plant with sunlight, water and air', type: 'image', keywords: ['photosynthesis', 'plant', 'diagram'] },
      narration: 'Look at this diagram. [SYNC] Sunlight is absorbed by the leaves. [SYNC] Water rises from the roots. [SYNC] Glucose is made.',
      // Phase 14's composer would choose the diagram focus for this short-text scene; the presenter + visual layout this
      // check exercises is asked for explicitly (the composer's own choices are checked by composer_browser_check.mjs)
      composition: { template: 'presenter_plus_visual', labels: ['Sunlight → Chlorophyll → Glucose'] } },
    { type: 'content', title: 'The leaf up close', html: '<p>Chlorophyll sits in the palisade cells.</p><p>Gases pass through the stoma.</p>',
      visual: { concept: 'leaf cross section', description: 'Layers of a leaf', type: 'image', keywords: ['leaf', 'cross', 'section', 'palisade'] },
      narration: 'Now we zoom into the leaf. [SYNC] Chlorophyll sits in the palisade cells. [SYNC] Gases pass through the stoma.',
      composition: { template: 'diagram_focus', labels: ['Palisade cells', 'Stoma'] } },
    { type: 'content', title: "Newton's second law", html: "<div class='formula-block'>\\[F = ma\\]</div><p>Force = Mass × Acceleration</p>",
      narration: 'Here is the law. [SYNC] F equals m times a. [PAUSE:1] Force is mass times acceleration.',
      composition: { labels: [{ text: 'F = Force' }, { text: 'm = Mass' }, { text: 'a = Acceleration', at: { sync: 1 } }], emphasis: [{ target: 'formula', at: { sync: 1 } }] } },
    { type: 'content', title: 'Python for loop', html: "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre><p>It prints the numbers 0 to 4.</p>",
      narration: 'Here is a loop that counts to four.' },
    { type: 'quiz_checkpoint', title: 'Quick check', question: 'What does a plant make in photosynthesis?', options: ['Glucose', 'Salt', 'Iron'], correct_index: 0,
      explanation: 'Plants make glucose from light, water and carbon dioxide.', countdown_seconds: 2, narration: 'Quick question for you.', reveal_narration: 'Glucose!' },
    { type: 'ai_video', title: 'A forest breathing', prompt: 'a forest in sunlight', video_asset_id: clip, ai_audio_source: 'narration',
      narration: 'Watch the forest. [SYNC] Every leaf is working.', composition: { camera: 'focus', focus: 'board' } }
].map(s => s) });
const LABELS = ['A-intro', 'B-visual', 'C-diagram', 'D-formula', 'E-code', 'F-quiz', 'G-visual-only'];

const problems = [];
let browser;
try {
    spawnSync(PYTHON, [path.join(REPO, 'tests', 'fixtures', 'cinematic_diagrams.py'), OUT]);
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=6', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', path.join(OUT, 'forest_clip.mp4'));
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const diagram = await upload(path.join(OUT, 'photosynthesis_diagram.png'), 'image/png', 'Photosynthesis plant diagram with sunlight, water, carbon dioxide and oxygen', ['photosynthesis', 'plant', 'diagram', 'sunlight']);
    const leaf = await upload(path.join(OUT, 'leaf_cross_section.png'), 'image/png', 'Leaf cross section showing palisade cells, spongy layer and stoma', ['leaf', 'cross', 'section', 'palisade', 'stoma']);
    const clip = await upload(path.join(OUT, 'forest_clip.mp4'), 'video/mp4');
    const pid = (await api('POST', '/save-history', lesson(diagram, leaf, clip))).data.id;

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'],
        args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });
    const settingsFor = (cine, presenter = { presenter_id: 'aadhi-teacher', mode: 'auto', position: 'right', style: 'friendly', fallback: 'none' }) => ([token, presenter, cine]);
    async function newPage(viewport, cine, presenter) {
        const context = await browser.newContext({ viewport });
        await context.addInitScript(([t, p, c]) => {
            if (!sessionStorage.getItem('seeded')) { // the settings a user would have chosen on the start screen (kept afterwards)
                localStorage.setItem('jwt_token', t);
                localStorage.setItem('aadhi.presenter', JSON.stringify(p));
                localStorage.setItem('aadhi.cinematic', JSON.stringify(c));
                sessionStorage.setItem('seeded', '1');
            }
        }, settingsFor(cine, presenter));
        await context.addInitScript(silentPage);
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error: ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console: ${m.text()}`); });
        page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico') && !r.url().includes('/api/cinematic/background')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
        return { context, page };
    }
    async function startLesson(page, projectId) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 30000 });
    }
    // Plays one scene without moving on to the next (the check looks at it)
    async function playScene(page, index) {
        await page.evaluate(n => { ttsState.isPlaying = true; currentSlide = n; renderSlide(n); window.checkAndAdvanceSlide = () => {}; }, index);
        await sleep(60);
        await page.evaluate(() => { window.checkAndAdvanceSlide = () => {}; });
    }
    const geometry = page => page.evaluate(() => {
        const vis = el => { if (!el) return null; const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.05 || !r.width ? null : { x: r.x, y: r.y, w: r.width, h: r.height }; };
        const zoneHidden = document.body.getAttribute('data-cine-visual') === 'none';
        const boxes = {
            title: vis(document.querySelector('#cine-title .cine-title-inner')),
            board: document.body.getAttribute('data-cine-board') === 'none' ? null : vis(document.getElementById('presentation-board')),
            visual: zoneHidden ? null : vis(document.querySelector('.dynamic-side-zone .side-panel-view.active')),
            presenter: vis(document.querySelector('#presenter-layer svg, #presenter-layer video')),
            labels: vis(document.getElementById('cine-labels'))
        };
        const sub = document.getElementById('subtitle-track');
        const subtitles = sub && sub.textContent.trim() && sub.classList.contains('active') ? vis(sub) : null;
        const hit = (a, b) => !!(a && b && a.x < b.x + b.w - 3 && b.x < a.x + a.w - 3 && a.y < b.y + b.h - 3 && b.y < a.y + a.h - 3);
        const names = Object.keys(boxes).filter(k => boxes[k]);
        const overlaps = [];
        names.forEach((a, i) => names.slice(i + 1).forEach(b => { if (hit(boxes[a], boxes[b])) overlaps.push(`${a}+${b}`); }));
        names.forEach(a => { if (a !== 'presenter' && hit(boxes[a], subtitles)) overlaps.push(`${a}+subtitles`); });
        const zone = document.querySelector('.lecture-overlay-zone');
        const m = new DOMMatrix(getComputedStyle(zone).transform === 'none' ? undefined : getComputedStyle(zone).transform);
        const plan = slides[currentSlide].cinematic_plan;
        return { template: plan && plan.template, camera: plan && plan.camera.movement, planScale: plan && plan.camera.scale, zoom: +m.a.toFixed(4),
            boxes, subtitles, overlaps, W: innerWidth, H: innerHeight, fit: cinematicStage.stats.lastFit, overflow: !!cinematicStage.stats.overflow,
            mascot: mascot.getStatus().audio || null, mascotPaused: !mascot.wantPlaying, bg: document.body.getAttribute('data-cine-bg') };
    });

    // ---- the lesson in cinematic mode at 1280x720: scenes A-G ---------------------------------------------------------
    const cine = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'auto', typography: 'academic' };
    const { page } = await newPage({ width: 1280, height: 720 }, cine);
    await startLesson(page, pid);
    await page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
    const seen = {};
    for (const [i, label] of LABELS.entries()) {
        await playScene(page, i);
        await sleep(700);
        const early = await geometry(page);
        await sleep(4800);
        const late = await geometry(page);
        const extra = await page.evaluate(() => ({
            labelsShown: [...document.querySelectorAll('.cine-label')].map(c => c.getAttribute('data-shown')),
            mjx: !!document.querySelector('#slide-content-container mjx-container'), tex: (document.querySelector('#slide-content-container .formula-block') || {}).textContent || '',
            prism: document.querySelectorAll('#slide-content-container .token').length,
            video: (() => { const v = document.querySelector('#presentation-board video'); return v ? { t: v.currentTime, w: v.videoWidth, paused: v.paused } : null; })(),
            entrances: cinematicStage.stats.scenes, anchored: cinematicStage.stats.anchored }));
        await page.screenshot({ path: path.join(OUT, `720-${label}.png`) });
        seen[label] = { early, late, extra };
    }
    const all = Object.values(seen);
    check('scenes A-G are composed (the templates the screenplay and the scene roles call for)',
        LABELS.map(l => seen[l].late.template).join(',') === 'presenter_intro,presenter_plus_visual,diagram_focus,formula_focus,code_focus,quiz,visual_focus',
        LABELS.map(l => seen[l].late.template).join(','));
    check('nothing overlaps: title, board, visual, presenter, labels and subtitles each keep their place (720p)',
        all.every(s => s.late.overlaps.length === 0 && s.early.overlaps.length === 0), LABELS.map(l => `${l}:${seen[l].late.overlaps.join('+') || 'ok'}`).join(' '));
    check('text fits its board (no overflow, never shrunk below 80 %)', all.every(s => !s.late.overflow && s.late.fit >= 0.8), LABELS.map(l => seen[l].late.fit).join(','));

    // Test 1: presenter + visual
    const b = seen['B-visual'].late.boxes;
    check('1. presenter + visual: the visual on the left, the text beside it, the presenter on the right', b.visual && b.board && b.presenter
        && b.visual.x + b.visual.w <= b.board.x + 2 && b.board.x + b.board.w <= b.presenter.x + 2 && b.title.y + b.title.h <= b.visual.y,
        `visual ${Math.round(b.visual && b.visual.x)} board ${Math.round(b.board && b.board.x)} presenter ${Math.round(b.presenter && b.presenter.x)}`);
    check('1. played: the label appears, the camera moves slowly (the board and the visual as one picture)',
        seen['B-visual'].extra.labelsShown[0] === 'true' && seen['B-visual'].late.zoom > seen['B-visual'].early.zoom + 0.005 && seen['B-visual'].late.zoom <= 1.07,
        `zoom ${seen['B-visual'].early.zoom} → ${seen['B-visual'].late.zoom}`);
    // Test 2: formula
    const d = seen['D-formula'];
    check('2. formula: typeset by MathJax from the lesson\'s own TeX (not rewritten), dominant on the board', d.extra.mjx && /F\s*=\s*m\s*a/.test(d.extra.tex.replace(/\s+/g, ' '))
        && d.late.boxes.board.w > d.late.boxes.presenter.w * 2, d.extra.tex.replace(/\s+/g, ' ').slice(0, 40));
    check('2. the camera focuses on the formula; the presenter stays secondary; the labels come at their [SYNC] reveal',
        d.late.camera === 'focus' && d.late.zoom > 1.02 && d.extra.labelsShown.every(v => v === 'true') && d.late.boxes.presenter,
        `zoom ${d.late.zoom}, labels ${d.extra.labelsShown.join(',')}, anchored ${d.extra.anchored}`);
    // Test 3: visual only
    const g = seen['G-visual-only'];
    check('3. visual only: no presenter, the clip large and playing, the camera where the safety rules allow',
        !g.late.boxes.presenter && g.extra.video && g.extra.video.w === 1280 && g.late.boxes.board.w >= 0.6 * g.late.W
        && (g.late.camera === 'static' ? g.late.zoom === 1 : g.late.zoom > 1.005), `${g.late.camera} zoom ${g.late.zoom}, video ${JSON.stringify(g.extra.video)}`);
    check('code stays whole and highlighted (Prism), with the presenter out of the way', seen['E-code'].extra.prism > 3 && !seen['E-code'].late.boxes.presenter
        && seen['E-code'].late.camera === 'static', `${seen['E-code'].extra.prism} tokens`);
    check('the intro: a large title with the presenter beside it; the quiz: question, answers and presenter',
        seen['A-intro'].late.boxes.title && seen['A-intro'].late.boxes.title.h > 60 && seen['A-intro'].late.boxes.presenter && seen['F-quiz'].late.boxes.presenter,
        `title ${Math.round(seen['A-intro'].late.boxes.title && seen['A-intro'].late.boxes.title.h)}px`);
    check("Aadhi's studio clip is paused while a gradient covers it (no hidden video decoding)", all.every(s => s.late.mascotPaused), all.map(s => s.late.mascotPaused).join(','));
    const fps = await page.evaluate(() => new Promise(resolve => { let n = 0; const t0 = performance.now();
        const tick = () => { n++; if (performance.now() - t0 < 2000) requestAnimationFrame(tick); else resolve(Math.round(n / ((performance.now() - t0) / 1000))); };
        renderSlide(1); requestAnimationFrame(tick); }));
    check('page frame rate during a cinematic scene with a camera move', fps >= 30, `${fps} fps`);

    // ---- 1920x1080: the same compositions, scaled -------------------------------------------------------------------------
    const hd = await newPage({ width: 1920, height: 1080 }, cine);
    await startLesson(hd.page, pid);
    await hd.page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
    const hdSeen = {};
    for (const [i, label] of LABELS.slice(0, 6).entries()) {
        await playScene(hd.page, i);
        await sleep(5500);
        hdSeen[label] = await geometry(hd.page);
        await hd.page.screenshot({ path: path.join(OUT, `1080-${label}.png`) });
    }
    const proportional = LABELS.slice(0, 6).every(l => {
        const a = seen[l].late.boxes.presenter; const c = hdSeen[l].boxes.presenter;
        return (!a && !c) || (a && c && Math.abs(a.x / 1280 - c.x / 1920) < 0.03 && Math.abs(a.w / 1280 - c.w / 1920) < 0.03);
    });
    check('1920x1080: nothing overlaps and the layout keeps its proportions', Object.values(hdSeen).every(s => s.overlaps.length === 0) && proportional,
        LABELS.slice(0, 6).map(l => `${l}:${hdSeen[l].overlaps.join('+') || 'ok'}`).join(' '));
    await hd.context.close();

    // Test 4: transitions
    await page.evaluate(() => { window.__transitions = []; });
    await playScene(page, 0);
    await sleep(300);
    await playScene(page, 1);
    await sleep(900);
    const fades = await page.evaluate(() => window.__transitions.slice());
    await page.evaluate(() => { cinematicSettings.set('transitions', 'cut'); });
    await page.waitForFunction(() => slides.every(s => s.cinematic_plan && s.cinematic_plan.transition.in === 'cut'), null, { timeout: 15000 });
    await page.evaluate(() => { window.__transitions = []; });
    await playScene(page, 2);
    await sleep(900);
    const cuts = await page.evaluate(() => window.__transitions.slice());
    check('4. scene A → fade → scene B; with "cut" chosen there is no transition at all', fades.length >= 2 && fades.every(t => t === 'fade') && cuts.length === 0,
        `fade: ${fades.join(',')}; cut: ${cuts.length} transitions`);
    await page.evaluate(() => { cinematicSettings.set('transitions', 'fade'); });
    await page.waitForFunction(() => slides.every(s => s.cinematic_plan && s.cinematic_plan.transition.in === 'fade'), null, { timeout: 15000 });

    // Classic is untouched
    await page.evaluate(() => { cinematicSettings.set('mode', 'classic'); });
    await playScene(page, 1);
    await sleep(900);
    const classic = await page.evaluate(() => ({ cine: document.body.getAttribute('data-cinematic'), title: getComputedStyle(document.getElementById('slide-title-container')).display,
        bg: (document.getElementById('cine-background') || {}).getAttribute ? getComputedStyle(document.getElementById('cine-background')).opacity : null, mascot: !mascot.wantPlaying }));
    check('Classic: the lesson renders exactly as before (no composition, its own title, Aadhi playing)', classic.cine === null && classic.title !== 'none' && classic.bg !== '1' && !classic.mascot,
        JSON.stringify(classic));
    await page.evaluate(() => { cinematicSettings.set('mode', 'cinematic'); });
    await page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 15000 });

    // ---- Visual Review: the scene inspector --------------------------------------------------------------------------
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    await page.goto(`${BASE}/?project_id=${pid}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.evaluate(() => planLessonVisuals());
    await page.click('#start-review-btn');
    await page.waitForSelector('.review-overlay.open .review-item[data-slot="composition"]', { timeout: 30000 });
    const items = await page.$$eval('.review-overlay.open .review-item[data-slot="composition"]', els => els.length);
    await page.click('.review-overlay.open .review-item[data-slot="composition"][data-scene="1"]');
    const frameLayers = await page.$$eval('.review-overlay.open .review-composition-box', els => els.map(e => e.getAttribute('data-layer')));
    const inspector = await page.textContent('.review-overlay.open .review-facts');
    await page.screenshot({ path: path.join(OUT, 'review-inspector.png') });
    check('the scene inspector: a composition item per scene, drawn to scale with its facts (opening it generates nothing)',
        items === 7 && frameLayers.join(',') === 'visual,board,title,labels,presenter,subtitles' && /Presenter \+ visual/.test(inspector) && /Duration/.test(inspector),
        `${items} items; ${frameLayers.join(',')}`);
    await page.click('.review-overlay.open [data-action="keep"]');
    await page.waitForFunction(() => /composed as shown/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    await page.click('.review-overlay.open .review-item[data-slot="composition"][data-scene="2"]');
    await page.click('.review-overlay.open [data-action="change"]');
    await page.selectOption('.review-overlay.open #composition-camera', 'static');
    await page.click('.review-overlay.open [data-action="apply"]');
    await page.waitForFunction(() => /composed your way/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    let saved = (await api('GET', `/api/projects/${pid}`)).data;
    check('keep and change are saved with the lesson (the camera of scene C made still)',
        saved.scenes[1].visual_review.composition.status === 'approved' && saved.scenes[2].visual_review.composition.overrides.camera === 'static'
        && saved.scenes[2].cinematic_plan.camera.movement === 'static', JSON.stringify(saved.scenes[2].visual_review.composition.overrides));
    // The presenter of scene B moved (Phase 12 review): the approved composition no longer applies
    await page.click('.review-overlay.open .review-item[data-slot="presenter"][data-scene="1"]');
    await page.click('.review-overlay.open [data-action="change"]');
    await page.click('.review-overlay.open [data-action="move-left"]');
    await page.waitForFunction(() => /Moved/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    await page.click('.review-panel .export-close');
    await page.evaluate(() => planLessonVisuals());
    const stale = await page.evaluate(() => ({ status: slides[1].cinematic_plan.review_status, stale: !!slides[1].cinematic_plan.review_stale,
        side: slides[1].cinematic_plan.presenter.side }));
    check('an approved composition goes stale when its presenter moves (never approved by accident)', stale.status === 'pending' && stale.stale && stale.side === 'left',
        JSON.stringify(stale));

    // ---- reduced motion (the viewer's preference, respected in the preview) --------------------------------------------
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.click('#start-lecture-btn');
    await page.evaluate(() => skipIntroSequence());
    await page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
    await sleep(1500); // the lesson's own start has shown its first scene
    await playScene(page, 3);
    await sleep(3500);
    const calm = await geometry(page);
    const calmPlan = await page.evaluate(() => ({ scene: currentSlide, camera: slides[3].cinematic_plan.camera.movement }));
    check('reduced motion: the camera stays still in the preview (the plan still has its move, for the export)',
        calmPlan.scene === 3 && calmPlan.camera === 'focus' && calm.zoom === 1, `scene ${calmPlan.scene}, plan ${calmPlan.camera}, zoom ${calm.zoom}`);
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    // ---- AI background: generated only when asked, through the AI media layer ------------------------------------------------
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    // (Phase 21: which provider made it is a technical detail, shown only in the debug view: made first with ?visualDebug, where
    // the provider is checked, then made again as a teacher sees it, in plain words)
    await page.goto(BASE + '/?visualDebug=1');
    await page.click('#nav-settings'); // (Phase 21: the start screen's settings are in Settings → Lesson defaults)
    await page.waitForSelector('#cinematic-mode', { timeout: 30000 });
    const before = (await api('GET', '/api/ai-media/runs?limit=50')).data;
    await page.selectOption('#cinematic-background', 'ai');
    await sleep(500);
    const runsAfterChoosing = (await api('GET', '/api/ai-media/runs?limit=50')).data;
    await page.click('[data-action="generate-background"]');
    await page.waitForFunction(() => /Background generated|Reused/.test((document.querySelector('.cinematic-status') || {}).textContent || ''), null, { timeout: 60000 });
    const bgStatus = await page.textContent('.cinematic-status');
    const bgAsset = await page.evaluate(() => cinematicSettings.settings.background_asset_id);
    const count = r => ((r && (r.runs || r)) || []).length;
    check('an AI background is generated only when asked (choosing it makes nothing), by the stand-in provider (debug view)', count(runsAfterChoosing) === count(before)
        && /^Background generated and added to the lesson \(by fake/.test(bgStatus) && /^[0-9a-f]{32}$/.test(bgAsset || ''), bgStatus);
    await page.goto(BASE + '/');
    await page.click('#nav-settings'); // (Phase 21: the start screen's settings are in Settings → Lesson defaults)
    await page.waitForSelector('[data-action="generate-background"]', { timeout: 30000 });
    await page.click('[data-action="generate-background"]'); // "Generate a new background": asked again, a new one is made
    await page.waitForFunction(() => /Background generated|Reused/.test((document.querySelector('.cinematic-status') || {}).textContent || ''), null, { timeout: 60000 });
    const plainBgStatus = (await page.textContent('.cinematic-status')).trim();
    check('normal view: the new background is announced in plain words (no provider)', plainBgStatus === 'Background generated and added to the lesson.'
        && !/fake/.test(await page.textContent('#cinematic-settings')) && /^[0-9a-f]{32}$/.test(await page.evaluate(() => cinematicSettings.settings.background_asset_id) || ''), plainBgStatus);
    await startLesson(page, pid);
    await page.waitForFunction(() => slides.every(s => s.cinematic_plan && s.cinematic_plan.background.type === 'image'), null, { timeout: 30000 });
    await playScene(page, 1);
    await sleep(4000);
    const withBg = await page.evaluate(() => { const img = document.querySelector('#cine-background img'); return { type: document.getElementById('cine-background').getAttribute('data-type'), loaded: !!(img && img.complete && img.naturalWidth) }; });
    await page.screenshot({ path: path.join(OUT, 'preview-B.png') });
    check('the AI background is behind every scene, under a scrim (the text stays readable)', withBg.type === 'image' && withBg.loaded, JSON.stringify(withBg));

    // ---- Test 5: export ---------------------------------------------------------------------------------------------------
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    await page.goto(`${BASE}/?project_id=${pid}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.evaluate(() => planLessonVisuals());
    await page.click('#start-videos-btn');
    await page.click('.export-start-btn');
    const exportWarnings = [];
    for (;;) {
        const start = page.locator('.export-actions button', { hasText: 'Start Recording' });
        const anyway = page.locator('.export-actions button', { hasText: 'Record anyway' });
        await Promise.race([start.waitFor({ timeout: 300000 }), anyway.waitFor({ timeout: 300000 })]);
        if (await anyway.isVisible()) { exportWarnings.push(await page.textContent('.export-message')); await anyway.click(); continue; }
        await start.click();
        break;
    }
    const templatesDuring = new Set();
    const recordEnd = Date.now() + 400000;
    let outcome = null;
    while (!outcome && Date.now() < recordEnd) {
        await sleep(700);
        const s = await page.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden, recording: exportFlow.recording,
            template: document.body.getAttribute('data-cinematic'),
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
        if (s.recording && s.template) templatesDuring.add(s.template);
        if (s.ready) outcome = 'ready';
        else if (s.error) outcome = 'error: ' + s.error;
    }
    const job = await page.evaluate(() => exportFlow.job);
    const record = job && (await api('GET', `/api/exports/${job.id}`)).data;
    const exportDir = path.join(data, 'exports');
    const userDir = fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(dname => job && fs.existsSync(path.join(exportDir, dname, `${job.id}.webm`))) : null;
    const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
    check('5. exported: the recording ran through the cinematic scenes and the video exists', outcome === 'ready' && record && record.status === 'COMPLETED'
        && stored && fs.statSync(stored).size > 100000 && templatesDuring.has('presenter_plus_visual') && templatesDuring.has('formula_focus'),
        `${outcome}; ${[...templatesDuring].join(',')}; warnings ${exportWarnings.length}`);
    const sceneLog = await page.evaluate(() => window.exportSceneLog || []);
    let similarity = null;
    if (stored && sceneLog[1]) {
        const frame = path.join(OUT, 'export-B.png');
        spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', (sceneLog[1].t + 4).toFixed(2), '-i', stored, '-frames:v', '1', frame]);
        const a = grid(path.join(OUT, 'preview-B.png'));
        const e = grid(frame);
        const visualA = region(a, 0.06, 0.25, 0.26, 0.40); const visualE = region(e, 0.06, 0.25, 0.26, 0.40);
        similarity = { diff: +difference(a, e).toFixed(1), visualPreview: visualA, visualExport: visualE };
    }
    check('5. the recorded frame has the preview\'s composition (the same layout, the diagram in the same place)', similarity && similarity.diff < 28
        && Math.abs(similarity.visualPreview[0] - similarity.visualExport[0]) < 45, JSON.stringify(similarity));
    check('no unexpected page errors or failed requests', problems.length === 0, problems.slice(0, 4).join(' | '));
} catch (e) {
    check('check ran to the end', false, e.stack || e.message);
} finally {
    if (browser) await browser.close();
    killServer();
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Artifacts: ${OUT}`);
process.exit(failed.length ? 1 : 0);
