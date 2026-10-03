// Secure Manim rendering check (Phase 10) with real server processes, the real sandbox and real Chrome:
// a throwaway server (its own database, static folder and sandbox folder).
//
//   1. a lesson's simulation scene plays in the page: its Manim code is rendered in the sandbox (standard
//      profile), the video plays, and it is a normal library asset recorded with how it was rendered
//   2. Visual Review shows that render for the scene (the visual router finds it without rendering again)
//   3. the lesson is exported: the export reuses the render (no second render) and the video is completed
//   4. a scene whose code asks for something forbidden shows a friendly, escaped error (no render starts)
//   5. the server dies while the sandbox renders (a crash inside it, then an outside kill of the server
//      process alone): no sandboxed process outlives it (the Job Object takes them down), and after a
//      restart the render is done again from the start and registered once
//
// Needs Chrome, ffmpeg, playwright-core (npm install --no-save playwright-core, or PLAYWRIGHT_CORE_DIR), the
// repo's virtualenv (.venv) and a secure runtime (Windows AppContainer, or Docker):
//   AADHI_PASSWORD=... node tests/manim_sandbox_browser_check.mjs
// Silent: no audio output, browser speech stubbed, narration answered with silence.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.MANIM_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-manim-sandbox-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.MANIM_CHECK_PORT || 9980 + (process.pid % 15));
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
    try {
        return await import('playwright-core');
    } catch (e) {
        if (process.env.PLAYWRIGHT_CORE_DIR) return import(pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE_DIR, 'index.mjs')).href);
        throw new Error('playwright-core not found: run `npm install --no-save playwright-core` (or set PLAYWRIGHT_CORE_DIR)');
    }
}

fs.rmSync(OUT, { recursive: true, force: true });
const data = path.join(OUT, 'server-data');
const SANDBOX = process.env.MANIM_CHECK_SANDBOX || path.join(os.tmpdir(), `amc-${process.pid}`);  // short: Windows path limit
fs.rmSync(SANDBOX, { recursive: true, force: true });
for (const dir of ['assets', 'exports', 'static']) fs.mkdirSync(path.join(data, dir), { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const JWT = 'manim-check-' + Math.random().toString(36).slice(2);
let server = null;
let serverRuns = 0;

function startServer(extraEnv = {}) {
    serverRuns++;
    const log = fs.openSync(path.join(OUT, `server-${serverRuns}.log`), 'w');
    server = spawn(path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python'),
        ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
            cwd: REPO, stdio: ['ignore', log, log],
            env: {
                ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
                DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
                ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
                MANIM_SANDBOX_DIR: SANDBOX, JWT_SECRET: JWT, AI_FAKE_PROVIDER: '1', AI_GENERATION_ENABLED: '0',
                AI_RECOVERY_ENABLED: '1', AI_RECOVERY_INTERVAL: '1', AI_JOB_LEASE_SECONDS: '4', AI_JOB_HEARTBEAT_SECONDS: '1',
                MANIM_AUTO_HEAL_ATTEMPTS: '0', AI_MEDIA_LOG: '1', ...extraEnv
            }
        });
    server.log = path.join(OUT, `server-${serverRuns}.log`);
    return server;
}
function killServerOnly() {  // the server process alone (not its tree): what happens to the sandbox is the Job Object's doing
    if (!server || server.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/F']);
    else server.kill('SIGKILL');
}
process.on('exit', () => { if (server && server.exitCode === null) spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']); });
const exited = proc => new Promise(resolve => { if (proc.exitCode !== null) resolve(proc.exitCode); else proc.on('exit', code => resolve(code)); });

async function waitForServer() {
    for (let i = 0; i < 120; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + OUT);
        try {
            if ((await fetch(BASE + '/visuals.js')).ok) return;
        } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('the test server did not start');
}

let token = null;
async function api(method, route, body) {
    const res = await fetch(BASE + route, {
        method, headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined
    });
    return { status: res.status, headers: res.headers, data: await res.json().catch(() => null) };
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 60000, every = 300) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
        try {
            const v = await fn();
            if (v) return v;
        } catch (e) { /* server restarting */ }
        await sleep(every);
    }
    return null;
}
const run = id => api('GET', `/api/ai-media/runs/${id}`).then(r => r.data);
const logText = () => fs.readdirSync(OUT).filter(f => /^server-\d+\.log$/.test(f)).map(f => fs.readFileSync(path.join(OUT, f), 'utf8')).join('\n');
const renders = () => (logText().match(/"event": "render_start"/g) || []).length;
const staticFiles = prefix => fs.readdirSync(path.join(data, 'static')).filter(f => f.startsWith(prefix));
function sandboxProcesses() {  // sandboxed programs still alive (their command line names this check's sandbox folder)
    const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command',
        `@(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*${SANDBOX.replace(/'/g, "''")}*' }).Count`],
        { encoding: 'utf8' });
    return Number((r.stdout || '').trim());
}
const jobFolders = () => fs.existsSync(path.join(SANDBOX, 'jobs')) ? fs.readdirSync(path.join(SANDBOX, 'jobs')) : [];

function silentWav(seconds = 1.2) {
    const rate = 8000, samples = Math.round(rate * seconds);
    const buf = Buffer.alloc(44 + samples * 2);
    buf.write('RIFF', 0); buf.writeUInt32LE(36 + samples * 2, 4); buf.write('WAVE', 8); buf.write('fmt ', 12);
    buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24);
    buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36);
    buf.writeUInt32LE(samples * 2, 40);
    return buf;
}
function silentPage() {
    const devices = navigator.mediaDevices;  // the export's tab capture, without playing the sound locally
    if (devices && devices.getDisplayMedia) {
        const capture = devices.getDisplayMedia.bind(devices);
        devices.getDisplayMedia = (constraints = {}) => capture({
            ...constraints,
            audio: constraints.audio ? { ...(typeof constraints.audio === 'object' ? constraints.audio : {}), suppressLocalAudioPlayback: true } : false
        });
    }
    if (window.speechSynthesis) {
        window.speechSynthesis.speak = u => setTimeout(() => { if (u.onstart) u.onstart(new Event('start')); setTimeout(() => u.onend && u.onend(new Event('end')), 300); }, 0);
        window.speechSynthesis.cancel = () => {};
    }
}
const tag = Date.now().toString(36);
const manim = (words, extra = '') => `from manim import *\n\nclass CheckScene(Scene):\n    def construct(self):\n` +
    `        title = Text("${words}", font_size=40).to_edge(UP)\n        box = Square(color=BLUE)\n` +
    `        self.play(Write(title))\n        self.play(Create(box))\n        self.play(Transform(box, Circle(color=YELLOW)))\n${extra}`;
const slow = words => manim(words, '        for i in range(30):\n            self.play(Rotate(box, 0.2), run_time=0.5)\n');

const problems = [];
let expectErrors = false;
let browser;
try {
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const status = (await api('GET', '/api/manim/sandbox')).data;
    check('the server renders in a secure sandbox (health check passed)', status.available && status.health && status.health.ok,
        `${status.runtime}; ${JSON.stringify(status.health && status.health.checked)}`);
    if (!status.available) throw new Error('no secure Manim runtime on this machine: ' + status.message);

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'],
        args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await context.addInitScript(t => localStorage.setItem('jwt_token', t), token);
    await context.addInitScript(silentPage);
    await context.route('**/generate-audio', r => r.fulfill({ json: { status: 'success', audio_url: `/__silence.wav?n=${Math.random()}` } }));
    await context.route('**/__silence.wav*', r => r.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav() }));
    const page = await context.newPage();
    page.on('pageerror', e => problems.push(`page error: ${e.message}`));
    page.on('console', m => {
        const text = m.text();
        if (m.type() === 'error' && !expectErrors && !/Failed to load resource/.test(text) && !text.startsWith('Logo video play error AbortError')
            && !(m.location().url || '').endsWith('/favicon.ico')) problems.push(`console: ${text}`);
    });
    page.on('response', r => { if (r.status() >= 400 && !expectErrors && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });

    async function openScene(projectId, index, debug = false) {
        await page.goto(`${BASE}/?project_id=${projectId}${debug ? '&visualDebug=1' : ''}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => window.lessonVisualPlans.length > 0 && ttsState.isPlaying
            && !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 30000 });
        await page.evaluate(n => { ttsState.isPlaying = false; currentSlide = n; renderSlide(n); }, index);
    }

    // ---- 1. The page plays a simulation scene: rendered in the sandbox -----------------------------------------------
    const code1 = manim(`Forces ${tag}`);
    const p1 = (await api('POST', '/save-history', { subject_name: `Manim check ${tag}`, session_title: 'Sandbox',
        scenes: [{ type: 'simulation', title: 'FORCES', aadhi_position: 'hidden', manim_code: code1, narration: 'Forces.' }] })).data.id;
    const rendered = page.waitForResponse(r => r.url().endsWith('/render') && r.request().method() === 'POST', { timeout: 120000 });
    await openScene(p1, 0);
    const answer = await (await rendered).json();
    await page.waitForFunction(() => { const v = document.getElementById('active-manim-video'); return v && v.readyState >= 2; }, null, { timeout: 30000 }).catch(() => {});
    const playing = await page.evaluate(() => { const v = document.getElementById('active-manim-video'); return v ? { ready: v.readyState, w: v.videoWidth, h: v.videoHeight, src: v.currentSrc } : null; });
    await page.screenshot({ path: path.join(OUT, '1-playing.png') });
    check('the scene\'s Manim code was rendered in the sandbox and plays in the page',
        answer.status === 'success' && !answer.cached && playing && playing.ready >= 2 && playing.w === 1280 && playing.h === 720, JSON.stringify(playing));
    const asset = (await api('GET', `/api/assets/${answer.asset_id}`)).data;
    const gen = (asset && asset.details && asset.details.generation) || {};
    check('the render is a normal library asset that records how it was made', asset && asset.status === 'ready' && asset.source === 'manim'
        && asset.width === 1280 && asset.height === 720 && gen.profile === 'standard' && gen.runtime === status.runtime && gen.run_id === answer.run_id
        && gen.peak_memory_mb > 0, `${gen.runtime}, ${gen.render_seconds}s, peak ${gen.peak_memory_mb} MB`);
    const r1 = await run(answer.run_id);
    check('the render is a durable run (kind manim) tied to the lesson scene', r1 && r1.kind === 'manim' && r1.status === 'completed'
        && r1.project_id === p1 && r1.scene_index === 0 && r1.slot === 'main' && r1.prompt === null, r1 && `${r1.status}, ${r1.history.length} attempt`);
    check('the sandbox workspace is gone after the render', jobFolders().length === 0 && sandboxProcesses() === 0, jobFolders().join(','));

    // ---- 2. Visual Review shows the render ---------------------------------------------------------------------------
    await page.goto(`${BASE}/?project_id=${p1}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.click('#start-review-btn');
    await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
    const reviewPlan = await page.evaluate(() => (slides[0].visual_plan || {}).main || null);
    check('Visual Review shows the rendered animation for the scene (found, not rendered again)', reviewPlan && reviewPlan.source === 'MANIM'
        && reviewPlan.asset_id === answer.asset_id && renders() === 1, reviewPlan && `${reviewPlan.source} ${reviewPlan.asset_id}`);
    await page.click('.review-panel .export-close');

    // ---- 3. Export the lesson: the render is reused --------------------------------------------------------------------
    await page.goto(`${BASE}/?project_id=${p1}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.click('#start-videos-btn');
    await page.click('.export-start-btn');
    for (;;) {
        const start = page.locator('.export-actions button', { hasText: 'Start Recording' });
        const anyway = page.locator('.export-actions button', { hasText: 'Record anyway' });
        await Promise.race([start.waitFor({ timeout: 300000 }), anyway.waitFor({ timeout: 300000 })]);
        if (await anyway.isVisible()) { problems.push('export warning: ' + await page.textContent('.export-message')); await anyway.click(); continue; }
        await start.click();
        break;
    }
    const outcome = await until(() => page.evaluate(() => {
        const message = document.querySelector('.export-message');
        if (!document.querySelector('.export-ready').hidden) return 'ready';
        return message && message.getAttribute('data-kind') === 'error' ? 'error: ' + message.textContent : null;
    }), 300000, 1000);
    const job = await page.evaluate(() => exportFlow.job);
    const record = job && (await api('GET', `/api/exports/${job.id}`)).data;
    await page.screenshot({ path: path.join(OUT, '3-exported.png') });
    check('the lesson with the sandboxed animation was exported', outcome === 'ready' && record && record.status === 'COMPLETED',
        record && `${record.status}, ${record.duration_seconds}s, ${record.width}x${record.height}`);
    check('the export reused the render (no second render)', renders() === 1, `${renders()} render(s)`);

    // ---- 4. Forbidden code: a friendly, escaped error ------------------------------------------------------------------
    expectErrors = true;
    const evil = 'import os\nfrom manim import *\n\nclass Evil(Scene):\n    def construct(self):\n        os.system("calc")  # <b>x</b>\n';
    const p4 = (await api('POST', '/save-history', { subject_name: `Manim check evil ${tag}`, session_title: 'Forbidden',
        scenes: [{ type: 'simulation', title: 'EVIL', aadhi_position: 'hidden', manim_code: evil, narration: 'No.' }] })).data.id;
    const refused = page.waitForResponse(r => r.url().endsWith('/render'), { timeout: 60000 });
    await openScene(p4, 0);
    const refusal = await refused;
    await page.waitForFunction(() => /could not be rendered/.test((document.getElementById('jxgbox') || {}).textContent || ''), null, { timeout: 20000 }).catch(() => {});
    const shown = await page.evaluate(() => { const b = document.getElementById('jxgbox'); return { text: b.textContent, bold: b.querySelectorAll('b').length }; });
    await page.screenshot({ path: path.join(OUT, '4-refused.png') });
    check('forbidden code is refused with a friendly message and category (nothing rendered)', refusal.status() === 422
        && refusal.headers()['x-error-category'] === 'unsafe_code' && /not allowed in the Manim sandbox/.test(shown.text) && renders() === 1,
        shown.text.slice(0, 120));
    // (Phase 21: the stage shows one plain card; the scene's code only in the debug view, where it is text, never markup)
    check('the card shows plain words, never the scene\'s code (outside the debug view)', !shown.text.includes('os.system') && shown.bold === 0,
        shown.text.slice(0, 160));
    await openScene(p4, 0, true);
    await page.waitForFunction(() => /os\.system/.test((document.getElementById('jxgbox') || {}).textContent || ''), null, { timeout: 20000 }).catch(() => {});
    const debugShown = await page.evaluate(() => { const b = document.getElementById('jxgbox'); return { text: b.textContent, bold: b.querySelectorAll('b').length }; });
    check('the error and the code are shown as text, never as markup (debug view)', debugShown.bold === 2 && debugShown.text.includes('<b>x</b>'),
        `${debugShown.bold} bold elements`);
    expectErrors = false;

    // ---- 5a. A crash inside the server while the sandbox renders --------------------------------------------------------
    await page.close();
    killServerOnly();
    await exited(server);
    startServer({ MANIM_TEST_CRASH_AT: 'manim_during_render' });
    await waitForServer();
    const code5 = slow(`Crash ${tag}`);
    const queued = (await api('POST', '/render', { code: code5, wait: false })).data;
    const died = await Promise.race([exited(server), sleep(60000).then(() => 'alive')]);
    await sleep(1500);
    const left = sandboxProcesses();
    check('a crash while rendering: the server died and took the sandboxed process with it', died === 97 && left === 0,
        `exit ${died}; ${left} sandboxed process(es) left`);
    startServer();
    await waitForServer();
    const redone = await until(async () => { const r = await run(queued.run_id); return r && r.status === 'completed' ? r : null; }, 180000, 1000);
    const prefix = redone && redone.result && redone.result.video_url.split('/').pop().split('_').slice(0, 2).join('_');
    check('after the restart the render was done again from the start and registered once', redone && redone.recovery_count >= 1
        && /rendered again from the start/.test(redone.explanation || '') && prefix && staticFiles(prefix).length === 1,
        redone && `${redone.explanation}; ${prefix && staticFiles(prefix).length} file(s)`);
    check('no workspace was left behind', jobFolders().length === 0, jobFolders().join(','));

    // ---- 5b. The server process killed from outside while the sandbox renders ----------------------------------------------
    const code6 = slow(`Kill ${tag}`);
    const queued6 = (await api('POST', '/render', { code: code6, wait: false })).data;
    const running6 = await until(() => sandboxProcesses() > 0 ? true : null, 60000, 200);
    await sleep(1500);
    killServerOnly();
    await exited(server);
    await sleep(1500);
    const left6 = sandboxProcesses();
    check('the server killed from outside mid-render: no sandboxed process outlived it', running6 && left6 === 0, `${left6} left`);
    startServer();
    await waitForServer();
    const redone6 = await until(async () => { const r = await run(queued6.run_id); return r && r.status === 'completed' ? r : null; }, 180000, 1000);
    const prefix6 = redone6 && redone6.result && redone6.result.video_url.split('/').pop().split('_').slice(0, 2).join('_');
    check('recovered after the kill: rendered again, one asset', redone6 && redone6.recovery_count >= 1 && prefix6 && staticFiles(prefix6).length === 1,
        redone6 && redone6.explanation);
    check('after everything: no sandboxed process and no workspace left', sandboxProcesses() === 0 && jobFolders().length === 0);
    check('no unexpected page errors or failed requests', problems.length === 0, problems.slice(0, 4).join(' | '));
} catch (e) {
    check('check ran to the end', false, e.stack || e.message);
} finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
    fs.rmSync(SANDBOX, { recursive: true, force: true });
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Artifacts: ${OUT}`);
process.exit(failed.length ? 1 : 0);
