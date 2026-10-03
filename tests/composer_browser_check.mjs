// Intelligent Scene Composer check (Phase 14) in real Chrome against a throwaway server with the local stand-ins only
// (AI_FAKE_PROVIDER=1: stand-in media and text models; FAKE_TTS=1: a speech-like tone). No real provider, no network.
//
//   1. a simple explanation     2. presenter + diagram      3. formula      4. code      5. comparison      6. quiz
//   7. an explicit user override (and "Automatic" giving it back)
//   8. an invalid composition repaired automatically (a user's choice that would make code unreadable)
//   9. AI-assisted without a model: the rules compose (and with the stand-in model: a validated suggestion)
//  10. preview → export: the recorded frame has the preview's composition
//   Also: the mini-lesson (introduction, definition, explanation, diagram, formula, code, comparison, quiz, summary)
//   at 1280x720 and 1920x1080 with nothing overlapping and text never shrunk below 80 %; regenerating a composition
//   generates no media and withdraws its approval.
//
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/composer_browser_check.mjs
// Silent: no audio output, browser speech stubbed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.COMPOSER_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-composer-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.COMPOSER_CHECK_PORT || 9975 + (process.pid % 15));
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
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `akc-${process.pid}`), JWT_SECRET: 'composer-check-' + Math.random().toString(36).slice(2),
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
function ffmpeg(...args) {
    const r = spawnSync('ffmpeg', ['-v', 'error', '-y', ...args], { encoding: 'buffer' });
    if (r.status !== 0) throw new Error(String(r.stderr));
    return r.stdout;
}
const grid = (file, w = 32, h = 18) => [...ffmpeg('-i', file, '-vf', `scale=${w}:${h}:flags=area`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')];
const difference = (a, b) => a.reduce((s, v, i) => s + Math.abs(v - (b[i] || 0)), 0) / a.length;

const LESSON = { subject_name: 'Composer check', session_title: 'Photosynthesis', scenes: [
    { type: 'content', title: 'Photosynthesis', subtitle: 'How plants make their own food', html: '<p>Plants turn sunlight, water and air into sugar.</p>',
      narration: 'Welcome! Today we find out how plants make their own food.' },
    { type: 'content', title: 'What is photosynthesis?', html: "<div class='definition'>Photosynthesis is the process by which green plants use sunlight to make glucose from carbon dioxide and water.</div>",
      visual: { concept: 'leaf cross section', description: 'Layers of a leaf where photosynthesis happens', type: 'diagram', keywords: ['leaf', 'cross', 'section', 'chloroplast'] },
      narration: 'Here is the definition. [SYNC] Photosynthesis is the process by which green plants make glucose.' },
    { type: 'content', title: 'What plants need', html: '<ul><li>Light gives the energy</li><li>Water comes from the soil</li><li>Air brings carbon dioxide</li></ul>',
      narration: 'Plants need three things. [SYNC] Light. [SYNC] Water. [SYNC] Air.' },
    { type: 'content', title: 'Inside a plant', html: '<ul><li>Sunlight</li><li>Water</li><li>Carbon dioxide</li></ul>',
      visual: { concept: 'photosynthesis plant diagram', description: 'A plant with sunlight, water and air', type: 'diagram', keywords: ['photosynthesis', 'plant', 'diagram'] },
      narration: 'Look at this diagram. [SYNC] Sunlight falls on the leaves. [SYNC] Water rises from the roots. [SYNC] Carbon dioxide comes from the air.',
      composition: { labels: ['Sunlight → Chlorophyll → Glucose'] } },
    { type: 'content', title: "Newton's second law", html: "<div class='formula-block'>\\[F = ma\\]</div><p>Force = Mass × Acceleration</p>",
      narration: 'Here is the law. [SYNC] F equals m times a. [SYNC] Force is mass times acceleration.',
      composition: { labels: [{ text: 'F = Force' }, { text: 'm = Mass' }, { text: 'a = Acceleration', at: { sync: 1 } }] } },
    { type: 'content', title: 'Python for loop', html: "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre><p>It prints the numbers 0 to 4.</p>",
      narration: 'Here is a loop that counts to four.' },
    { type: 'content', title: 'Plants vs Animals', html: '<table><thead><tr><th>Plants</th><th>Animals</th></tr></thead><tbody><tr><td>Make their own food</td><td>Eat other living things</td></tr><tr><td>Release oxygen</td><td>Release carbon dioxide</td></tr><tr><td>Cannot move around</td><td>Move to find food</td></tr></tbody></table>',
      narration: 'Let us compare plants and animals.' },
    { type: 'quiz_checkpoint', title: 'Quick check', question: 'What does a plant make in photosynthesis?', options: ['Glucose', 'Salt', 'Iron'], correct_index: 0,
      explanation: 'Plants make glucose from light, water and carbon dioxide.', countdown_seconds: 2, narration: 'Quick question for you.', reveal_narration: 'Glucose!' },
    { type: 'key-takeaway', title: 'Key takeaways', html: "<ul class='takeaway-list'><li>Plants make glucose from sunlight</li><li>Chlorophyll captures the light</li><li>Oxygen is released into the air</li></ul>",
      narration: 'Let us recap. [SYNC] Plants make glucose. [SYNC] Chlorophyll captures light. [SYNC] Oxygen is released.' }
] };
const LABELS = ['1-intro', '2-definition', '3-explanation', '4-diagram', '5-formula', '6-code', '7-comparison', '8-quiz', '9-summary'];
const EXPECTED = ['presenter_intro', 'presenter_plus_visual', 'presenter_explanation', 'diagram_focus', 'formula_focus', 'code_focus', 'comparison', 'quiz', 'summary'];

const problems = [];
let browser;
try {
    spawnSync(PYTHON, [path.join(REPO, 'tests', 'fixtures', 'cinematic_diagrams.py'), OUT]);
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    await upload(path.join(OUT, 'photosynthesis_diagram.png'), 'Photosynthesis plant diagram with sunlight, water, carbon dioxide and oxygen', ['photosynthesis', 'plant', 'diagram', 'sunlight']);
    await upload(path.join(OUT, 'leaf_cross_section.png'), 'Leaf cross section showing palisade cells, spongy layer and stoma', ['leaf', 'cross', 'section', 'palisade', 'chloroplast']);
    const pid = (await api('POST', '/save-history', LESSON)).data.id;
    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'],
        args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });
    async function newPage(viewport, cine) {
        const context = await browser.newContext({ viewport });
        await context.addInitScript(([t, c]) => {
            if (!sessionStorage.getItem('seeded')) {
                localStorage.setItem('jwt_token', t);
                localStorage.setItem('aadhi.presenter', JSON.stringify({ presenter_id: 'aadhi-teacher', mode: 'auto', position: 'right', style: 'friendly', fallback: 'none' }));
                localStorage.setItem('aadhi.cinematic', JSON.stringify(c));
                sessionStorage.setItem('seeded', '1');
            }
        }, [token, cine]);
        await context.addInitScript(silentPage);
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error: ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console: ${m.text()}`); });
        page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
        return { context, page };
    }
    async function startLesson(page, projectId) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
        await sleep(1500); // the lesson's own start has shown its first scene
    }
    async function playScene(page, index) {
        await page.evaluate(n => { ttsState.isPlaying = true; currentSlide = n; renderSlide(n); window.checkAndAdvanceSlide = () => {}; }, index);
        await sleep(60);
        await page.evaluate(() => { window.checkAndAdvanceSlide = () => {}; });
    }
    const measure = (page, index) => page.evaluate(n => {
        const vis = el => { if (!el) return null; const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.05 || !r.width ? null : { x: r.x, y: r.y, w: r.width, h: r.height }; };
        const boxes = { title: vis(document.querySelector('#cine-title .cine-title-inner')),
            board: document.body.getAttribute('data-cine-board') === 'none' ? null : vis(document.getElementById('presentation-board')),
            visual: document.body.getAttribute('data-cine-visual') === 'none' ? null : vis(document.querySelector('.dynamic-side-zone .side-panel-view.active')),
            presenter: vis(document.querySelector('#presenter-layer svg, #presenter-layer video')), labels: vis(document.getElementById('cine-labels')) };
        const sub = document.getElementById('subtitle-track');
        const subs = sub && sub.textContent.trim() && sub.classList.contains('active') ? vis(sub) : null;
        const hit = (a, b) => !!(a && b && a.x < b.x + b.w - 3 && b.x < a.x + a.w - 3 && a.y < b.y + b.h - 3 && b.y < a.y + a.h - 3);
        const names = Object.keys(boxes).filter(k => boxes[k]);
        const overlaps = [];
        names.forEach((a, i) => names.slice(i + 1).forEach(b => { if (hit(boxes[a], boxes[b])) overlaps.push(`${a}+${b}`); }));
        names.forEach(a => { if (a !== 'presenter' && hit(boxes[a], subs)) overlaps.push(`${a}+subtitles`); });
        const zone = document.querySelector('.lecture-overlay-zone');
        const tf = getComputedStyle(zone).transform;
        const plan = slides[n].cinematic_plan;
        const th = [...document.querySelectorAll('#slide-content-container th')].map(e => Math.round(e.getBoundingClientRect().width));
        return { scene: currentSlide, template: plan.template, source: plan.composition && plan.composition.source, decision: plan.composition && plan.composition.decision,
            reasons: plan.composition && plan.composition.reasons, repairs: plan.composition && plan.composition.repairs, ai: plan.composition && plan.composition.ai,
            camera: plan.camera.movement, zoom: tf === 'none' ? 1 : +new DOMMatrix(tf).a.toFixed(4), boxes, overlaps, fit: cinematicStage.stats.lastFit,
            overflow: !!cinematicStage.stats.overflow, W: innerWidth, H: innerHeight, th, presenterStart: (plan.layers.find(l => l.id === 'presenter') || {}).start,
            boardStart: (plan.layers.find(l => l.id === 'board') || {}).start, mjx: !!document.querySelector('#slide-content-container mjx-container'),
            prism: document.querySelectorAll('#slide-content-container .token').length };
    }, index);
    const area = b => (b ? b.w * b.h : 0);

    // ---- the mini-lesson at 1280x720 ----------------------------------------------------------------------------------
    const cine = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'auto', typography: 'academic', composer: 'rules' };
    const { page } = await newPage({ width: 1280, height: 720 }, cine);
    await startLesson(page, pid);
    const seen = {};
    for (const [i, label] of LABELS.entries()) {
        await playScene(page, i);
        await sleep(5500);
        seen[label] = await measure(page, i);
        await page.screenshot({ path: path.join(OUT, `720-${label}.png`) });
    }
    const all = Object.values(seen);
    check('the mini-lesson is composed by purpose (introduction, definition, explanation, diagram, formula, code, comparison, quiz, summary)',
        LABELS.every((l, i) => seen[l].template === EXPECTED[i]) && all.every(s => s.source === 'rules'), LABELS.map(l => seen[l].template).join(','));
    check('nothing overlaps and no text shrinks below 80 % (720p)', all.every(s => s.overlaps.length === 0 && !s.overflow && s.fit >= 0.8),
        LABELS.map(l => `${l}:${seen[l].overlaps.join('+') || 'ok'}/${seen[l].fit}`).join(' '));
    check('every composition says why, in short factual reasons', all.every(s => s.reasons && s.reasons.length >= 1 && s.reasons.every(r => r.length < 120)),
        seen['4-diagram'].reasons.join(' | '));
    const e1 = seen['3-explanation'];
    check('1. a simple explanation: the text with the presenter beside it', e1.template === 'presenter_explanation' && e1.boxes.presenter && e1.boxes.board
        && e1.boxes.board.x + e1.boxes.board.w <= e1.boxes.presenter.x + 2, JSON.stringify(e1.decision));
    const d2 = seen['4-diagram'];
    check('2. presenter + diagram: the diagram gets the largest area, the presenter stays small and clear of it',
        d2.decision.visual_role === 'dominant' && d2.decision.presenter_role === 'small' && area(d2.boxes.visual) > area(d2.boxes.board)
        && area(d2.boxes.visual) > 3 * area(d2.boxes.presenter), `visual ${Math.round(area(d2.boxes.visual))} board ${Math.round(area(d2.boxes.board))} presenter ${Math.round(area(d2.boxes.presenter))}`);
    const f3 = seen['5-formula'];
    check('3. formula: typeset by MathJax, dominant, the presenter secondary, the camera leaning in gently', f3.mjx && f3.decision.presenter_role === 'small'
        && area(f3.boxes.board) > 1.5 * area(f3.boxes.presenter) && f3.zoom > 1.01 && f3.zoom <= 1.075, `zoom ${f3.zoom}`);
    const c4 = seen['6-code'];
    check('4. code: the code gets the stage, highlighted, with a still camera', c4.prism > 3 && c4.zoom === 1 && ['hidden', 'small'].includes(c4.decision.presenter_role)
        && c4.boxes.board.w > 0.6 * c4.W, `${c4.prism} tokens, presenter ${c4.decision.presenter_role}`);
    const v5 = seen['7-comparison'];
    check('5. comparison: both sides the same width, a still camera, the presenter out of the table', v5.template === 'comparison' && v5.th.length === 2
        && Math.abs(v5.th[0] - v5.th[1]) <= 4 && v5.zoom === 1, `columns ${v5.th.join('/')} px`);
    const q6 = seen['8-quiz'];
    check('6. quiz: the question leads, the presenter comes in after it', q6.template === 'quiz' && q6.presenterStart > q6.boardStart && q6.boxes.presenter,
        `question ${q6.boardStart}s, presenter ${q6.presenterStart}s`);

    // ---- 1920x1080: the same compositions --------------------------------------------------------------------------------
    const hd = await newPage({ width: 1920, height: 1080 }, cine);
    await startLesson(hd.page, pid);
    const hdSeen = {};
    for (const [i, label] of LABELS.entries()) {
        await playScene(hd.page, i);
        await sleep(5500);
        hdSeen[label] = await measure(hd.page, i);
        await hd.page.screenshot({ path: path.join(OUT, `1080-${label}.png`) });
    }
    check('1920x1080: the same compositions, nothing overlapping, text readable', LABELS.every(l => hdSeen[l].template === seen[l].template
        && hdSeen[l].overlaps.length === 0 && !hdSeen[l].overflow && hdSeen[l].fit >= 0.8), LABELS.map(l => `${l}:${hdSeen[l].overlaps.join('+') || 'ok'}`).join(' '));
    await hd.context.close();

    // ---- 7. an explicit user override, and "Automatic" giving it back -----------------------------------------------------
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    await page.goto(`${BASE}/?project_id=${pid}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.evaluate(() => planLessonVisuals());
    await page.click('#start-review-btn');
    await page.waitForSelector('.review-overlay.open .review-item[data-slot="composition"]', { timeout: 30000 });
    await page.click('.review-overlay.open .review-item[data-slot="composition"][data-scene="2"]');
    const automatic = await page.textContent('.review-overlay.open .review-composition-auto');
    await page.screenshot({ path: path.join(OUT, 'inspector.png') });
    await page.click('.review-overlay.open [data-action="change"]');
    await page.selectOption('.review-overlay.open #composition-presenter_position', 'left');
    await page.click('.review-overlay.open [data-action="apply"]');
    await page.waitForFunction(() => /composed your way/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    let saved = (await api('GET', `/api/projects/${pid}`)).data.scenes[2];
    const userChoice = { side: saved.cinematic_plan.presenter.side, source: saved.cinematic_plan.composition.source, label: await page.textContent('.review-overlay.open .review-composition-auto') };
    await page.click('.review-overlay.open [data-action="change"]');
    await page.selectOption('.review-overlay.open #composition-presenter_position', 'auto');
    await page.click('.review-overlay.open [data-action="apply"]');
    await page.waitForFunction(() => /composed|automatic/i.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    saved = (await api('GET', `/api/projects/${pid}`)).data.scenes[2];
    check('7. an explicit override is respected (presenter moved left, "Your choice"), and "Automatic" gives it back',
        /Automatic ✓/.test(automatic) && userChoice.side === 'left' && userChoice.source === 'user' && /Your choice/.test(userChoice.label)
        && saved.cinematic_plan.presenter.side === 'right' && !(saved.visual_review || {}).composition, `${userChoice.side}/${userChoice.source} → ${saved.cinematic_plan.presenter.side}`);

    // ---- regenerate: only the composition, never media; the approval is withdrawn -------------------------------------------
    await page.click('.review-overlay.open .review-item[data-slot="composition"][data-scene="3"]');
    await page.click('.review-overlay.open [data-action="keep"]');
    await page.waitForFunction(() => /composed as shown/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    const runsBefore = (await api('GET', '/api/ai-media/runs?limit=100')).data;
    await page.click('.review-overlay.open [data-action="regenerate"]');
    await page.waitForFunction(() => /decided again/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    const runsAfter = (await api('GET', '/api/ai-media/runs?limit=100')).data;
    const count = r => ((r && (r.runs || r)) || []).length;
    saved = (await api('GET', `/api/projects/${pid}`)).data.scenes[3];
    const chip = await page.getAttribute('.review-overlay.open .review-item[data-slot="composition"][data-scene="3"] .review-chip', 'data-status');
    check('regenerating a composition generates no media and withdraws its approval', count(runsAfter) === count(runsBefore) && chip === 'pending'
        && !(saved.visual_review || {}).composition && saved.cinematic_plan.template === 'diagram_focus', `runs ${count(runsBefore)}→${count(runsAfter)}, status ${chip}`);

    // ---- 8. an invalid composition repaired automatically -------------------------------------------------------------------
    // A long explanation (the Presenter Director shows the presenter here) with the user's choice of a large presenter
    const long = Array.from({ length: 9 }, (_, k) => `<p>Step ${k} of the process moves energy from one molecule to the next inside the chloroplast.</p>`).join('');
    const repairLesson = { subject_name: 'Repair', scenes: [
        { type: 'content', title: 'Opening', html: '<p>Start.</p>', narration: 'Start.' },
        { type: 'content', title: 'How energy moves', html: long, narration: 'Energy moves step by step.',
          visual_review: { composition: { status: 'changed', overrides: { presenter_size: 'dominant' } } } }] };
    const pid2 = (await api('POST', '/save-history', repairLesson)).data.id;
    await page.click('.review-panel .export-close');
    await startLesson(page, pid2);
    await playScene(page, 1);
    await sleep(2500);
    const r8 = await measure(page, 1);
    await page.screenshot({ path: path.join(OUT, 'repaired.png') });
    check('8. a choice that would make the text unreadable is repaired automatically (and says so); the text fits',
        r8.decision.presenter_role !== 'dominant' && (r8.repairs || []).some(t => /could not be kept/.test(t)) && !r8.overflow && r8.fit >= 0.8 && r8.overlaps.length === 0,
        `${r8.decision.presenter_role}; ${(r8.repairs || []).join(' | ')}`);

    // ---- 9. AI-assisted: without a model the rules compose; with the stand-in model a validated suggestion ------------------------
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    await page.goto(BASE + '/');
    await page.click('#nav-settings'); // (Phase 21: the start screen's settings are in Settings → Lesson defaults)
    await page.waitForSelector('#cinematic-composer', { timeout: 30000 });
    await page.selectOption('#cinematic-composer', 'ai');
    await page.waitForFunction(() => /not available on this server/.test(document.getElementById('cinematic-settings').textContent), null, { timeout: 15000 });
    const note = await page.textContent('#cinematic-settings');
    const ramp = { subject_name: 'AI', scenes: [
        { type: 'content', title: 'Opening', html: '<p>Start.</p>', narration: 'Start.' },
        { type: 'content', title: 'Forces on a ramp', html: "<div class='formula-block'>\\[F = mg\\sin\\theta\\]</div><p>Look at the ramp.</p>",
          visual: { concept: 'leaf cross section', type: 'diagram', keywords: ['leaf', 'cross', 'section'] }, narration: 'Look at the ramp. [SYNC] The force is m g sin theta.' }] };
    const pid3 = (await api('POST', '/save-history', ramp)).data.id;
    await startLesson(page, pid3);
    const withoutModel = await page.evaluate(() => slides[1].cinematic_plan.composition);
    await page.evaluate(() => { window.composerOptions = () => ({ provider: 'fake', model: 'fake-composer-1' }); });
    await page.evaluate(() => planLessonCinematic());
    await page.waitForFunction(() => slides[1].cinematic_plan.composition.ai && slides[1].cinematic_plan.composition.ai.provider === 'fake', null, { timeout: 30000 });
    const withModel = await page.evaluate(() => slides[1].cinematic_plan.composition);
    await playScene(page, 1);
    await sleep(2500);
    const ai9 = await measure(page, 1);
    check('9. AI-assisted without a text model: said honestly, and the rules compose every scene', /not available on this server/.test(note)
        && withoutModel.ai && withoutModel.ai.status === 'unavailable' && withoutModel.source === 'rules', `${withoutModel.ai && withoutModel.ai.status} → ${withoutModel.source}`);
    check('9. with the stand-in model: only the ambiguous scene is asked, the suggestion is validated and rendered safely',
        withModel.ai.status === 'ok' && withModel.source === 'ai' && ai9.overlaps.length === 0 && !ai9.overflow, `${withModel.ai.status}, ${withModel.decision.template}`);

    // ---- 10. preview → export: the same composition --------------------------------------------------------------------------
    await page.evaluate(() => { window.composerOptions = null; cinematicSettings.set('composer', 'rules'); });
    await startLesson(page, pid);
    await playScene(page, 3);
    await sleep(4000);
    await page.screenshot({ path: path.join(OUT, 'preview-diagram.png') });
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    await page.goto(`${BASE}/?project_id=${pid}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.evaluate(() => planLessonVisuals());
    await page.click('#start-videos-btn');
    await page.click('.export-start-btn');
    for (;;) {
        const start = page.locator('.export-actions button', { hasText: 'Start Recording' });
        const anyway = page.locator('.export-actions button', { hasText: 'Record anyway' });
        await Promise.race([start.waitFor({ timeout: 300000 }), anyway.waitFor({ timeout: 300000 })]);
        if (await anyway.isVisible()) { await anyway.click(); continue; }
        await start.click();
        break;
    }
    const recordEnd = Date.now() + 500000;
    let outcome = null;
    const templatesDuring = new Set();
    while (!outcome && Date.now() < recordEnd) {
        await sleep(700);
        const s = await page.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden, recording: exportFlow.recording, template: document.body.getAttribute('data-cinematic'),
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
        if (s.recording && s.template) templatesDuring.add(s.template);
        if (s.ready) outcome = 'ready';
        else if (s.error) outcome = 'error: ' + s.error;
    }
    const job = await page.evaluate(() => exportFlow.job);
    const exportDir = path.join(data, 'exports');
    const userDir = job && fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(d => fs.existsSync(path.join(exportDir, d, `${job.id}.webm`))) : null;
    const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
    const sceneLog = await page.evaluate(() => window.exportSceneLog || []);
    let diff = null;
    if (stored && sceneLog[3]) {
        spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', (sceneLog[3].t + 4).toFixed(2), '-i', stored, '-frames:v', '1', path.join(OUT, 'export-diagram.png')]);
        diff = +difference(grid(path.join(OUT, 'preview-diagram.png')), grid(path.join(OUT, 'export-diagram.png'))).toFixed(1);
    }
    check('10. preview → export: the recording used the same compositions and its frame matches the preview', outcome === 'ready' && stored
        && EXPECTED.every(t => templatesDuring.has(t)) && diff !== null && diff < 28, `${outcome}; ${templatesDuring.size} templates; frame difference ${diff}`);
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
