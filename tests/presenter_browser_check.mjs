// AI Presenter check (Phase 12) in real Chrome against a throwaway server whose only presenter provider is the
// local stand-in (AI_FAKE_PROVIDER=1: a drawn teacher whose mouth follows the narration's loudness) and whose TTS
// is a local speech-like tone (FAKE_TTS=1). No real provider, no network.
//
//   1. Default settings: Aadhi (mascot) placed by the screenplay, no presenter layer (existing lessons unchanged)
//   2. Presenter chosen on the start screen (AI Teacher, automatic): no clip yet -> nothing shown (nothing faked);
//      Visual Review lists the presenter per scene; clips generated there (a second request is a cache hit, a New
//      Version makes another and keeps the first); approved
//   3. The lesson plays: scenes A-F (intro, diagram, explanation, mathematics, quiz, code) - the presenter stands
//      in the zone the board leaves free and never covers the board, the side visual or the subtitles; it is
//      small for mathematics and hidden for code; its clip plays in time with the narration
//   4. Exported: the recording shows the presenter in presenter scenes and not in the code scene
//   5. Aadhi Teacher (illustrated): drawn, mouth driven by the narration audio's envelope, expressions / gestures
//      from the plan; screenshots A-F
//   6. The server killed while a presenter clip is generated: recovery resumes the same provider job (no second
//      job) and the clip lands in the saved lesson
//
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/presenter_browser_check.mjs
// Silent: no audio output, browser speech stubbed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.PRESENTER_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-presenter-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.PRESENTER_CHECK_PORT || 9900 + (process.pid % 15));
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
const jobs = path.join(OUT, 'provider-jobs');
for (const dir of ['assets', 'exports', 'static']) fs.mkdirSync(path.join(data, dir), { recursive: true });
fs.mkdirSync(jobs, { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const PYTHON = path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python');
const JWT = 'presenter-check-' + Math.random().toString(36).slice(2);
let server = null;
let serverRuns = 0;

function startServer(extraEnv = {}) {
    serverRuns++;
    const log = fs.openSync(path.join(OUT, `server-${serverRuns}.log`), 'w');
    server = spawn(PYTHON, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
        cwd: REPO, stdio: ['ignore', log, log],
        env: { ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `apc-${process.pid}`), JWT_SECRET: JWT, AI_FAKE_PROVIDER: '1', FAKE_TTS: '1',
            AI_GENERATION_ENABLED: '1', AI_FAKE_STATE_DIR: jobs, AI_RECOVERY_ENABLED: '1', AI_RECOVERY_INTERVAL: '1',
            AI_JOB_LEASE_SECONDS: '4', AI_JOB_HEARTBEAT_SECONDS: '1', FAKE_PRESENTER_POLL_SECONDS: '0.3', AI_MEDIA_LOG: '1',
            GEMINI_API_KEY: '', OPENAI_API_KEY: '', ...extraEnv }
    });
}
function killServer() {
    if (!server || server.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
    else server.kill('SIGKILL');
}
process.on('exit', killServer);
const exited = proc => new Promise(resolve => { if (proc.exitCode !== null) resolve(proc.exitCode); else proc.on('exit', code => resolve(code)); });
async function waitForServer() {
    for (let i = 0; i < 120; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + OUT);
        try { if ((await fetch(BASE + '/presenter.js')).ok) return; } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('the test server did not start');
}
let token = null;
async function api(method, route, body) {
    const res = await fetch(BASE + route, { method, headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined });
    return { status: res.status, data: await res.json().catch(() => null) };
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 60000, every = 300) {
    const end = Date.now() + ms;
    while (Date.now() < end) { try { const v = await fn(); if (v) return v; } catch (e) { /* not yet */ } await sleep(every); }
    return null;
}
const jobFiles = () => fs.readdirSync(jobs).filter(f => /^fake-presenter-[0-9a-f]{10}\.json$/.test(f));
function silentPage() {
    const devices = navigator.mediaDevices;
    if (devices && devices.getDisplayMedia) {
        const capture = devices.getDisplayMedia.bind(devices);
        devices.getDisplayMedia = (c = {}) => capture({ ...c, audio: c.audio ? { ...(typeof c.audio === 'object' ? c.audio : {}), suppressLocalAudioPlayback: true } : false });
    }
    if (window.speechSynthesis) {
        window.speechSynthesis.speak = u => setTimeout(() => { if (u.onstart) u.onstart(new Event('start')); setTimeout(() => u.onend && u.onend(new Event('end')), 300); }, 0);
        window.speechSynthesis.cancel = () => {};
    }
}

// Scenes A-F (the quality gate's six situations)
const lesson = tag => ({ subject_name: `Presenter check ${tag}`, session_title: 'Photosynthesis', scenes: [
    { type: 'title', title: 'PHOTOSYNTHESIS', aadhi_position: 'center', html: '<h2>How plants make food</h2>', narration: 'Welcome! Today we find out how plants make their own food.' },
    { type: 'content', title: 'THE LEAF', aadhi_position: 'left', html: '<p>Leaves capture light.</p>', narration: 'Look at this chart of light and sugar in a leaf.',
      side_panel: { type: 'chart', chart_type: 'bar', data: { labels: ['Light', 'Sugar'], datasets: [{ label: 'Leaf', data: [8, 5] }] } } },
    { type: 'content', title: 'THREE STAGES', aadhi_position: 'left', html: '<ul><li>Light is absorbed</li><li>Water is split</li><li>Sugar is made</li></ul>', narration: 'Photosynthesis happens in three stages.' },
    { type: 'content', title: 'THE EQUATION', aadhi_position: 'left', html: '<p>\\(6CO_2 + 6H_2O \\rightarrow C_6H_{12}O_6 + 6O_2\\)</p>', narration: 'Here is the overall equation.' },
    { type: 'quiz_checkpoint', title: 'QUICK CHECK', aadhi_position: 'right', question: 'What do plants make?', options: ['Sugar', 'Salt'], answer: 0, narration: 'Quick question for you.' },
    { type: 'content', title: 'CODE', aadhi_position: 'left', html: '<pre>for stage in stages:\n    print(stage)</pre>', narration: 'A short loop lists the stages.' }
] });

const problems = [];
let browser;
try {
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'],
        args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    await context.addInitScript(t => localStorage.setItem('jwt_token', t), token);
    await context.addInitScript(silentPage);
    const page = await context.newPage();
    page.on('pageerror', e => problems.push(`page error: ${e.message}`));
    page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console: ${m.text()}`); });
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });

    const pid = (await api('POST', '/save-history', lesson('ai'))).data.id;
    async function openLesson(projectId) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    }
    async function playScene(index) {
        await page.evaluate(n => { ttsState.isPlaying = true; currentSlide = n; renderSlide(n); }, index);
        await sleep(1200);
    }
    // Where things are on screen: the presenter never over the board, the side visual or the subtitles
    const geometry = () => page.evaluate(() => {
        const rect = el => { if (!el) return null; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
            return r.width && r.height && cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0.05 ? { x: r.x, y: r.y, w: r.width, h: r.height } : null; };
        const layer = document.getElementById('presenter-layer');
        const shown = layer && ['idle', 'speaking', 'listening'].includes(layer.getAttribute('data-state'));
        const content = layer && (layer.querySelector('video, svg, img'));
        const box = shown && content ? rect(content) : null;
        const board = rect(document.querySelector('.board-body')) || rect(document.getElementById('slide-content-container'));
        const side = rect(document.querySelector('.dynamic-side-zone'));
        const sub = document.getElementById('subtitle-track');
        const subs = sub && sub.textContent.trim() ? rect(sub) : null;
        const hit = (a, b) => !!(a && b && a.x < b.x + b.w - 2 && b.x < a.x + a.w - 2 && a.y < b.y + b.h - 2 && b.y < a.y + a.h - 2);
        return { shown: !!box, state: layer && layer.getAttribute('data-state'), layout: layer && layer.getAttribute('data-layout'), box, board, side, subs,
            overlaps: [hit(box, board) && 'board', hit(box, side) && 'side visual', hit(box, subs) && 'subtitles'].filter(Boolean) };
    });

    // ---- 1. Default settings: the legacy mascot path -----------------------------------------------------------------
    await openLesson(pid);
    await page.click('#start-lecture-btn');
    await page.evaluate(() => skipIntroSequence());
    await page.waitForFunction(() => !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 30000 });
    await playScene(1);
    const legacy = await page.evaluate(() => ({ plans: slides.filter(s => s.presenter_plan).length, layer: (document.getElementById('presenter-layer') || {}).getAttribute
        ? document.getElementById('presenter-layer').getAttribute('data-state') : 'none', placement: mascot.placement || null }));
    check('1. default settings: Aadhi placed by the screenplay, no presenter planned or drawn (existing lessons unchanged)',
        legacy.plans === 0 && ['hidden', 'none', null].includes(legacy.layer), JSON.stringify(legacy));

    // ---- 2. AI Teacher chosen on the start screen; clips generated in Visual Review --------------------------------------
    await page.goto(BASE + '/');
    await page.click('#nav-settings'); // (Phase 21: the start screen's settings are in Settings → Lesson defaults)
    await page.waitForSelector('#presenter-select', { timeout: 30000 });
    const options = await page.$$eval('#presenter-select option', os => os.map(o => ({ value: o.value, text: o.textContent, disabled: o.disabled })));
    await page.selectOption('#presenter-select', 'ai-teacher');
    await page.selectOption('#presenter-mode', 'auto');
    const preview = await page.textContent('.presenter-preview');
    check('2. the start screen offers the presenters with what they can do here (AI Teacher via the stand-in provider)',
        options.map(o => o.value).join(',') === 'aadhi,aadhi-teacher,ai-teacher' && !options.find(o => o.value === 'ai-teacher').disabled
        && /lip-synced to the narration/.test(preview) && /may be billed/.test(preview), preview.replace(/\s+/g, ' ').slice(0, 160));
    await openLesson(pid);
    await page.click('#start-lecture-btn');
    await page.evaluate(() => skipIntroSequence());
    await page.waitForFunction(() => slides.every(s => s.presenter_plan), null, { timeout: 30000 });
    await playScene(1);
    const noClip = await geometry();
    check('2. an AI presenter without a clip shows nothing (no substitute, nothing faked)', !noClip.shown && noClip.state === 'missing', noClip.state);
    const plans = await page.evaluate(() => slides.map(s => ({ role: s.presenter_plan.role, enabled: s.presenter_plan.enabled,
        layout: s.presenter_plan.placement === 'pip' ? `pip_${s.presenter_plan.layout}` : s.presenter_plan.layout })));
    check('2. the Presenter Director plans by what each scene teaches', plans.map(p => `${p.role}:${p.enabled ? p.layout : 'hidden'}`).join(' ')
        === 'intro:right visual:right explanation:right math:pip_right quiz:right code:hidden', plans.map(p => `${p.role}:${p.enabled ? p.layout : 'hidden'}`).join(' '));
    await page.evaluate(() => { ttsState.isPlaying = false; });
    // Phase 21: which provider made a clip is a technical detail, shown only in the debug view: the clips are made with
    // ?visualDebug (the provider is checked there), then the review is opened again as a teacher sees it (plain words)
    const openReview = async (debug) => {
        await page.goto(`${BASE}/?project_id=${pid}${debug ? '&visualDebug=1' : ''}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.evaluate(() => planLessonVisuals());
        await page.click('#start-review-btn');
        await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
    };
    await openReview(true);
    const presenterItems = await page.$$eval('.review-overlay.open .review-item[data-slot="presenter"]', els => els.length);
    check('2. Visual Review lists the presenter of every scene (opening it generates nothing)', presenterItems === 6 && jobFiles().length === 0, `${presenterItems} presenter items`);
    const generated = [];
    for (const scene of [0, 1, 2, 3, 4]) {
        await page.click(`.review-overlay.open .review-item[data-slot="presenter"][data-scene="${scene}"]`);
        await page.click('.review-overlay.open [data-action="change"]');
        await page.click('.review-overlay.open [data-action="generate"]');
        await page.waitForFunction(() => /generated by|Reused/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 60000 });
        generated.push(await page.textContent('.review-overlay.open .review-status'));
    }
    check('2. presenter clips generated explicitly in Visual Review (one provider job per scene; the provider in the debug view)',
        generated.every(t => /generated by fake-presenter/.test(t)) && jobFiles().length === 5, `${jobFiles().length} jobs; ${generated[0]}`);
    await page.click('.review-overlay.open .review-item[data-slot="presenter"][data-scene="1"]');
    await page.waitForSelector('.review-overlay.open .review-preview video', { timeout: 10000 });
    const debugFacts = await page.textContent('.review-overlay.open .review-facts');
    check('2. the review shows the clip and says honestly how it speaks (debug view: and which provider made it)',
        /Lip-synced to the narration by fake-presenter/.test(debugFacts) && /point/.test(debugFacts), debugFacts.replace(/\s+/g, ' ').slice(0, 200));
    // the same review as a teacher sees it: plain words, no provider anywhere in the scene's detail
    await openReview(false);
    await page.click('.review-overlay.open .review-item[data-slot="presenter"][data-scene="1"]');
    await page.waitForSelector('.review-overlay.open .review-preview video', { timeout: 10000 });
    const facts = await page.textContent('.review-overlay.open .review-facts');
    const plainDetail = await page.textContent('.review-overlay.open .review-detail');
    check('2. normal view: "Lip-synced to the narration", and no provider name in the review', /Lip-synced to the narration/.test(facts) && /point/.test(facts)
        && !/fake-presenter/.test(plainDetail), facts.replace(/\s+/g, ' ').slice(0, 200));
    await page.click('.review-overlay.open [data-action="change"]');
    await page.click('.review-overlay.open [data-action="generate"]'); // "Generate new presenter version"
    await page.waitForFunction(() => /made with AI for this scene|Reused/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 60000 });
    const plainStatus = await page.textContent('.review-overlay.open .review-status');
    check('2. normal view: a new clip is announced in plain words (no provider)', plainStatus.trim() === 'Presenter clip made with AI for this scene.'
        && !/fake-presenter/.test(await page.textContent('.review-overlay.open .review-detail')), plainStatus);
    const versions = (await api('GET', '/api/assets?source=ai-presenter')).data;
    check('2. a New Version makes another clip and keeps the earlier one', jobFiles().length === 6 && versions && (versions.assets || versions.items || []).length === 6,
        `${jobFiles().length} jobs, ${(versions.assets || versions.items || []).length} presenter assets`);
    for (const scene of [0, 1, 2, 3, 4]) {
        await page.click(`.review-overlay.open .review-item[data-slot="presenter"][data-scene="${scene}"]`);
        const keep = page.locator('.review-overlay.open [data-action="keep"]');
        if (await keep.isEnabled()) { await keep.click(); await sleep(400); }
    }
    const statuses = await page.$$eval('.review-overlay.open .review-item[data-slot="presenter"] .review-chip', els => els.map(e => e.getAttribute('data-status')));
    await page.screenshot({ path: path.join(OUT, '2-review.png') });
    await page.click('.review-panel .export-close');
    check('2. presenters reviewed (approved or changed)', statuses.slice(0, 5).every(s => s === 'approved' || s === 'changed'), statuses.join(','));

    // ---- 3. Playback: scenes A-F -----------------------------------------------------------------------------------------
    await page.goto(`${BASE}/?project_id=${pid}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.click('#start-lecture-btn');
    await page.evaluate(() => skipIntroSequence());
    await page.waitForFunction(() => slides.every(s => s.presenter_plan), null, { timeout: 30000 });
    const seen = [];
    for (const [i, label] of ['A-intro', 'B-diagram', 'C-concept', 'D-math', 'E-quiz', 'F-code'].entries()) {
        await playScene(i);
        await page.waitForFunction(() => { const v = document.querySelector('#presenter-layer video'); return !v || v.readyState >= 2; }, null, { timeout: 15000 }).catch(() => {});
        await sleep(900);
        const g = await geometry();
        const playing = await page.evaluate(() => { const v = document.querySelector('#presenter-layer video'); return v ? { t: v.currentTime, paused: v.paused, w: v.videoWidth } : null; });
        await page.screenshot({ path: path.join(OUT, `3-ai-${label}.png`) });
        seen.push({ label, ...g, playing });
    }
    const byLabel = Object.fromEntries(seen.map(s => [s.label, s]));
    check('3. the presenter appears where the Director placed it and never covers the board, side visual or subtitles',
        seen.every(s => s.overlaps.length === 0) && ['A-intro', 'B-diagram', 'C-concept', 'D-math', 'E-quiz'].every(l => byLabel[l].shown),
        seen.map(s => `${s.label}:${s.shown ? s.layout : 'hidden'}${s.overlaps.length ? '!' + s.overlaps.join('+') : ''}`).join(' '));
    check('3. small for mathematics, hidden for code', byLabel['D-math'].shown && byLabel['D-math'].box.w < byLabel['C-concept'].box.w && !byLabel['F-code'].shown,
        `math ${Math.round(byLabel['D-math'].box && byLabel['D-math'].box.w)}px vs ${Math.round(byLabel['C-concept'].box && byLabel['C-concept'].box.w)}px`);
    check('3. the presenter clip plays with the narration', seen.filter(s => s.playing).some(s => s.playing.t > 0.2 && s.playing.w === 480),
        seen.filter(s => s.playing).map(s => s.playing.t.toFixed(1)).join(','));

    // ---- 4. Export -------------------------------------------------------------------------------------------------------
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
    const presenterDuring = [];
    const recordEnd = Date.now() + 300000;
    let outcome = null;
    while (!outcome && Date.now() < recordEnd) {
        await sleep(700);
        const s = await page.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden, recording: exportFlow.recording,
            slide: Number((document.getElementById('presenter-layer') || { getAttribute: () => -1 }).getAttribute('data-scene')),
            presenter: (document.getElementById('presenter-layer') || {}).getAttribute ? document.getElementById('presenter-layer').getAttribute('data-state') : null,
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
        if (s.recording) presenterDuring.push(`${s.slide}:${s.presenter}`);
        if (s.ready) outcome = 'ready';
        else if (s.error) outcome = 'error: ' + s.error;
    }
    const job = await page.evaluate(() => exportFlow.job);
    const record = job && (await api('GET', `/api/exports/${job.id}`)).data;
    const presentScenes = new Set(presenterDuring.filter(x => /speaking|idle|listening/.test(x)).map(x => Number(x.split(':')[0])));
    check('4. exported with the presenter in the presenter scenes and not in the code scene', outcome === 'ready' && record && record.status === 'COMPLETED'
        && [0, 1, 2, 3, 4].every(i => presentScenes.has(i)) && !presentScenes.has(5) && exportWarnings.length === 0,
        `${outcome}; presenter seen in scenes ${[...presentScenes].sort().join(',')}; warnings ${exportWarnings.length}`);
    // The recorded video itself: the presenter's blazer (the stand-in's purple) in a presenter scene's box
    const stored = record && path.join(data, 'exports', String(fs.readdirSync(path.join(data, 'exports')).find(d => fs.existsSync(path.join(data, 'exports', d, `${job.id}.webm`)))), `${job.id}.webm`);
    const sceneLog = await page.evaluate(() => window.exportSceneLog || []);
    let pixel = null;
    if (stored && fs.existsSync(stored) && sceneLog[2]) {
        const at = (sceneLog[2].t + 2.5).toFixed(2);
        const frame = spawnSync('ffmpeg', ['-v', 'error', '-ss', at, '-i', stored, '-frames:v', '1', '-vf', 'crop=iw*0.20:ih*0.30:iw*0.75:ih*0.55,scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], { encoding: 'buffer' });
        pixel = [...frame.stdout.slice(0, 3)];
        spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', at, '-i', stored, '-frames:v', '1', path.join(OUT, '4-recorded-frame.png')]);
    }
    check('4. the recorded video shows the presenter (its purple blazer in the presenter zone)', pixel && pixel[2] > pixel[1] + 15 && pixel[0] > pixel[1],
        pixel && `rgb(${pixel.join(',')})`);

    // ---- 5. Aadhi Teacher (illustrated) ------------------------------------------------------------------------------------
    await page.goto(BASE + '/');
    await page.click('#nav-settings'); // (Phase 21: the start screen's settings are in Settings → Lesson defaults)
    await page.waitForSelector('#presenter-select', { timeout: 30000 });
    await page.selectOption('#presenter-select', 'aadhi-teacher');
    await page.selectOption('#presenter-mode', 'auto');
    await openLesson(pid);
    await page.click('#start-lecture-btn');
    await page.evaluate(() => skipIntroSequence());
    await page.waitForFunction(() => slides.every(s => s.presenter_plan && s.presenter_plan.presenter_id === 'aadhi-teacher'), null, { timeout: 30000 });
    const teacher = [];
    for (const [i, label] of ['A-intro', 'B-diagram', 'C-concept', 'D-math', 'E-quiz', 'F-code'].entries()) {
        await page.evaluate(() => { presenterStage.stats.envelopeFrames = 0; });
        await playScene(i);
        await sleep(1500);
        const g = await geometry();
        const figure = await page.evaluate(() => { const svg = document.querySelector('#presenter-layer svg');
            return svg ? { expression: svg.getAttribute('data-expression'), gesture: svg.getAttribute('data-gesture'), envelope: presenterStage.stats.envelopeFrames } : null; });
        await page.screenshot({ path: path.join(OUT, `5-teacher-${label}.png`) });
        teacher.push({ label, ...g, figure });
    }
    const tb = Object.fromEntries(teacher.map(s => [s.label, s]));
    check('5. Aadhi Teacher is drawn where planned, never over the content, hidden for code', teacher.every(s => s.overlaps.length === 0)
        && ['A-intro', 'B-diagram', 'C-concept', 'D-math', 'E-quiz'].every(l => tb[l].shown) && !tb['F-code'].shown,
        teacher.map(s => `${s.label}:${s.shown ? s.layout : 'hidden'}${s.overlaps.length ? '!' + s.overlaps.join('+') : ''}`).join(' '));
    check('5. expressions and gestures follow the plan (welcome for the intro, point at the diagram, counting three stages)',
        tb['A-intro'].figure.gesture === 'welcome' && tb['B-diagram'].figure.gesture === 'point' && tb['C-concept'].figure.gesture === 'counting',
        teacher.filter(s => s.figure).map(s => `${s.label}:${s.figure.expression}/${s.figure.gesture}`).join(' '));
    check("5. the teacher's mouth follows the narration audio (speech envelope, not a loop)", teacher.some(s => s.figure && s.figure.envelope > 5),
        teacher.filter(s => s.figure).map(s => s.figure.envelope).join(','));

    // ---- 6. Recovery of a presenter clip --------------------------------------------------------------------------------
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    killServer();
    await exited(server);
    startServer({ FAKE_PRESENTER_JOB_SECONDS: '12' });
    await waitForServer();
    const recoveryLesson = lesson('recovery');
    recoveryLesson.scenes[2].narration = `Recovery check ${Date.now()}: the three stages of photosynthesis.`; // not generated before (no cache hit)
    const pid2 = (await api('POST', '/save-history', recoveryLesson)).data.id;
    const before = jobFiles().length;
    const started = (await api('POST', '/api/presenters/generate', { scene: recoveryLesson.scenes[2], scene_index: 2, project_id: pid2,
        settings: { presenter_id: 'ai-teacher', mode: 'auto' }, wait: false })).data;
    const running = await until(async () => { const r = (await api('GET', `/api/ai-media/runs/${started.run_id}`)).data; return r && r.provider_job_id ? r : null; }, 30000);
    killServer();
    await exited(server);
    startServer({ FAKE_PRESENTER_JOB_SECONDS: '12' });
    await waitForServer();
    const done = await until(async () => { const r = (await api('GET', `/api/ai-media/runs/${started.run_id}`)).data; return r && r.status === 'completed' ? r : null; }, 120000, 1000);
    const saved = (await api('GET', `/api/projects/${pid2}`)).data;
    const media = saved && saved.scenes[2].presenter_plan && saved.scenes[2].presenter_plan.media;
    check('6. a presenter clip interrupted by a server kill is recovered from the same provider job (no second job)',
        running && done && done.provider_job_id === running.provider_job_id && jobFiles().length === before + 1 && done.recovery_count >= 1,
        done && `${done.explanation}; ${jobFiles().length - before} job(s)`);
    check('6. the recovered clip lands in the saved lesson', media && media.asset_id === done.result.asset_id && media.lip_sync === true, JSON.stringify(media && { asset: media.asset_id, lip: media.lip_sync }));
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
