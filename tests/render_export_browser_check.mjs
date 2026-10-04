// Rendered export check (Phase 22 Q1) in real Chrome against a throwaway server with the local stand-ins only
// (AI_FAKE_PROVIDER=1; FAKE_TTS=1: a tone as long as the text). No real provider is ever called.
//
// The server render (POST /api/exports/render: render_worker.mjs steps the live stage frame by frame, ffmpeg encodes and mixes)
// is checked on what it produces, with tests/helpers/video_qa.mjs:
//   refuse     a lesson with an AI video scene that has no clip: missing_visuals 'refuse' ends FAILED, naming the scene
//   main       the same lesson with 'omit': progress in plain words through preparing -> capturing -> mixing -> finishing;
//              the MP4: 1920x1080, exactly 30 fps CFR, frame count = round(duration x 30) +- 1, H.264 yuv420p + AAC; no black
//              edges; each [SYNC] reveal on the expected frame +- 1 (from the render timeline); Aadhi's crossfades and scenes
//              without frozen frames beyond the 24 -> 30 fps repeat (one repeat in 5 frames); no notice card (the timeline's
//              notes name the omitted scene, and its frames show no gold button while the preview of it does); audible sound
//              starting with the narration's logged starts, the quiz's sound effects; VTT + chapters matching the timeline;
//              preview / export parity <= 5 of 255 (the Phase 20 method: a scene played to its end in the preview against the
//              export frame 0.6 s before the next scene, the preview's player controls left out)
//   cinematic  a short Cinematic lesson (page_settings 'aadhi.cinematic'): the same file checks, its sync reveals, parity
//   determinism  a three-scene lesson rendered twice: the same timeline, the same video frame for frame (Aadhi's clip frames,
//              the particles), and Aadhi carrying on across the range seams
//   pages      the main lesson rendered again on one page (RENDER_RANGES=1) and on three pages (forced through the planner's
//              rates file): frame for frame the same video as the planner's render (encoder noise only, at the seams too), the
//              same clip frame at every seam; the render speed of each (frames, wall time, render minutes per video minute) and
//              the time a 100-minute lesson would take (the worker's own planner with the measured rates)
//   restart    the throwaway server killed mid-capture (the process only, as a crash: the worker, its browser and encoder must
//              stop with it), restarted on the same data: the render resumes and finishes (the same video as the uninterrupted
//              one) or fails cleanly in plain words
//   tab        the tab-capture export ("Record the screen") still works: a short smoke test in the export_e2e way
//
// Needs Chrome, ffmpeg / ffprobe, playwright-core and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/render_export_browser_check.mjs
// Env: RENDER_CHECK_OUT (artifacts), RENDER_CHECK_PORT (default 9740-9759), RENDER_CHECK_PARTS (default
// refuse,main,cinematic,determinism,pages,restart,tab), RENDER_CHECK_RENDER_MS (one render's time limit, default 45 min).
// Silent: --mute-audio and --disable-audio-output, browser speech stubbed; the tab-capture smoke keeps the tab unmuted (a muted
// tab records silence) but asks for suppressLocalAudioPlayback, with no audio output device.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as qa from './helpers/video_qa.mjs';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.RENDER_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-render-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.RENDER_CHECK_PORT || 9740 + (process.pid % 20));
const BASE = `http://127.0.0.1:${PORT}`;
const PARTS = new Set((process.env.RENDER_CHECK_PARTS || 'refuse,main,cinematic,determinism,pages,restart,tab').split(',').map(s => s.trim()).filter(Boolean));
const RENDER_MS = Number(process.env.RENDER_CHECK_RENDER_MS || 45 * 60000);
const FPS = 30;
const STARTED = Date.now();
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
for (const dir of ['assets', 'exports', 'static', 'jobs', 'render']) fs.mkdirSync(path.join(data, dir), { recursive: true });
const EXPORTS_DIR = path.join(data, 'exports');
const RENDER_DIR = path.join(data, 'render');
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const PYTHON = path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python');
const JWT_SECRET = 'render-check-' + Math.random().toString(36).slice(2); // the same across restarts: tokens stay valid
let server = null;
let serverLogs = 0;
function startServer(extra = {}) {
    const log = fs.openSync(path.join(OUT, `server${serverLogs++ ? '-' + serverLogs : ''}.log`), 'w');
    server = spawn(PYTHON, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
        cwd: REPO, stdio: ['ignore', log, log],
        env: { ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR, STATIC_DIR: path.join(data, 'static'), RENDER_DIR,
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `arc-${process.pid}`), JWT_SECRET,
            AI_FAKE_PROVIDER: '1', FAKE_TTS: '1', AI_GENERATION_ENABLED: '1', AI_FAKE_STATE_DIR: path.join(data, 'jobs'),
            GEMINI_API_KEY: '', OPENAI_API_KEY: '', AI_FAKE_FAIL: '', ...extra }
    });
}
const leftovers = new Map(); // processes of a killed server that must not outlive it (killed at exit whatever happens)
function killServer() {
    if (!server || server.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
    else server.kill('SIGKILL');
}
// A crash: only the process running the server dies (on Windows the venv's python.exe is a launcher whose child interpreter runs
// uvicorn; that child is the server). What it started must stop with it.
function crashServer(table) {
    if (!server || server.exitCode !== null) return null;
    const interpreter = table.find(p => p.ppid === server.pid && /python/i.test(p.name));
    const pid = interpreter ? interpreter.pid : server.pid;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(pid), '/F']);
    else try { process.kill(pid, 'SIGKILL'); } catch (e) { /* gone */ }
    return pid;
}
function killLeftovers() {
    for (const pid of leftovers.keys()) {
        if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(pid), '/T', '/F']);
        else try { process.kill(pid, 'SIGKILL'); } catch (e) { /* gone */ }
    }
}
process.on('exit', () => { killServer(); killLeftovers(); });
process.on('unhandledRejection', e => console.log('      (a pending wait ended after its step: ' + String((e && e.message) || e).split(/\r?\n/)[0] + ')'));
async function waitForServer() {
    for (let i = 0; i < 200; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + OUT);
        try { if ((await fetch(BASE + '/studio.js')).ok) return; } catch (e) { /* not up yet */ }
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
async function apiText(route) {
    const res = await fetch(BASE + route, { headers: { Authorization: 'Bearer ' + token } });
    return { status: res.status, text: await res.text().catch(() => '') };
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
// While window.__holdScene is set the page's checkAndAdvanceSlide is a no-op: a scene played for a preview frame stays on
function holdProbe() {
    let real = () => {};
    Object.defineProperty(window, 'checkAndAdvanceSlide', { configurable: true,
        get() { return window.__holdScene ? () => {} : real; }, set(v) { real = v; } });
}
// Words a progress or error message for the user must not contain (plain words: no code, paths or tool names)
const TECH = /\b(traceback|exception|errno|stack ?trace|playwright|chromium|cdp|ffmpeg|ffprobe|subprocess|stderr|stdout|exit code|status code|econn\w*|enoent|epipe|sqlalchemy|keyerror|typeerror|valueerror)\b|[A-Za-z]:\\|\.py\b|\.mjs\b|\/api\//i;
const CODE_WORDS = /\b(None|null|NaN|undefined|True|False)\b/;
const PROVIDER_WORDS = /\b(gemini|openai|chatgpt|gpt-?\d|anthropic|claude|pollinations|replicate|runway(ml)?|kling|luma|pika|eleven ?labs|stability|dall-?e|imagen|veo|sora|midjourney|ltx|fake(-alt|-presenter)?|stand-in)\b/i;
function plainWords(text) {
    const s = String(text || '');
    const m = TECH.exec(s) || CODE_WORDS.exec(s) || PROVIDER_WORDS.exec(s);
    return { ok: !!s.trim() && !m, bad: m ? m[0] : (s.trim() ? null : '(empty)') };
}
const norm = v => String(v === undefined || v === null ? '' : v).trim().toLowerCase().replace(/\s+/g, ' ');
const r3 = v => (Number.isFinite(v) ? +v.toFixed(3) : v);

// ---- processes (the crash test: what the server started must stop with it) --------------------------------------------------
function processTable() {
    if (process.platform === 'win32') {
        const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command',
            'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress'], { encoding: 'utf8', maxBuffer: 1 << 26 });
        try { return JSON.parse(r.stdout).map(x => ({ pid: x.ProcessId, ppid: x.ParentProcessId, name: x.Name })); } catch (e) { return []; }
    }
    const r = spawnSync('ps', ['-eo', 'pid=,ppid=,comm='], { encoding: 'utf8' });
    return String(r.stdout).split('\n').map(l => l.trim().split(/\s+/)).filter(x => x.length >= 3).map(([pid, ppid, name]) => ({ pid: +pid, ppid: +ppid, name }));
}
function descendants(pid) {
    const table = processTable();
    const out = [];
    const walk = p => table.filter(x => x.ppid === p && x.pid !== p).forEach(c => { out.push(c); walk(c.pid); });
    walk(pid);
    return out;
}
function stillRunning(list) {
    const now = new Map(processTable().map(x => [x.pid, x.name]));
    return list.filter(p => now.get(p.pid) === p.name);
}

// ---- the lessons ----------------------------------------------------------------------------------------------------------
// Classic: Aadhi left / right / left / right / left / centre / left (a crossfade at every scene), Classic [SYNC] cascade reveals,
// a formula, code, an AI video scene with a prompt but no clip (the default server makes AI videos by hand: a missing visual),
// a quiz with its countdown ticks and reveal sound, a wrap-up
const LESSON = {
    subject_name: 'Render check', unit_name: 'Unit 1', session_number: 'Lesson 01', session_title: 'Forces and Motion',
    concept_map: [{ id: 'force', title: 'Force', depends_on: [] }, { id: 'law', title: "Newton's second law", depends_on: ['force'] }],
    scenes: [
        { type: 'title', concept_id: 'force', title: 'FORCES AND MOTION', subtitle: 'Lesson 01', aadhi_position: 'left',
            narration: 'Welcome. Today we find out what makes things move.' },
        { type: 'content', concept_id: 'force', title: 'WHAT IS A FORCE?', aadhi_position: 'right',
            html: '<p>A force is a push or a pull.</p><p>Forces change how things move.</p><p>We measure force in newtons.</p>',
            narration: 'Let us start with a simple idea that everyone can feel every day. [SYNC] A force is a push or a pull. [SYNC] Forces change how things move. [SYNC] We measure force in newtons.' },
        { type: 'content', concept_id: 'law', title: "NEWTON'S SECOND LAW", aadhi_position: 'left',
            html: "<div class='formula-block'>\\[F = m \\times a\\]</div><p>Force equals mass times acceleration.</p>",
            narration: 'Here is the law that links them. [SYNC] F equals m times a. [SYNC] Force equals mass times acceleration.' },
        { type: 'content', concept_id: 'law', title: 'A FORCE IN CODE', aadhi_position: 'right',
            html: "<pre><code class='language-python'>mass = 2.0          # kilograms\nacceleration = 3.5  # metres per second squared\nforce = mass * acceleration\nprint(force)        # 7.0 newtons</code></pre><p>The program prints 7.0 newtons.</p>",
            narration: 'This short program multiplies the mass by the acceleration. [SYNC] It prints seven newtons.' },
        { type: 'ai_video', concept_id: 'force', title: 'FORCES IN THE REAL WORLD', aadhi_position: 'left',
            prompt: 'A car speeding up on a motorway, arrows showing the forces', narration: 'Think of a car speeding up on a motorway.' },
        { type: 'quiz_checkpoint', concept_id: 'law', title: 'QUICK CHECK', aadhi_position: 'center', question: 'What is the unit of force?',
            options: ['Newton', 'Joule', 'Watt'], correct_index: 0, countdown_seconds: 3, explanation: 'Force is measured in newtons.',
            narration: 'Quick question for you.', reveal_narration: 'Newtons!' },
        { type: 'content', concept_id: 'force', title: 'WRAP UP', aadhi_position: 'left',
            html: '<ul><li>A force is a push or a pull</li><li>F = m × a</li></ul>',
            narration: 'Let us recap. [SYNC] A force is a push or a pull. [SYNC] Force is mass times acceleration.' }
    ]
};
const MISSING = 4; // the AI video scene without a clip
const QUIZ = 5;
const PARITY_SCENES = [0, 1, 2, 3, 6];
const SYNC_SCENES = [1, 2, 3, 6];
// Cinematic (the render request carries the page's settings): a definition, a process with three reveals, a formula
const CINE_SETTINGS = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'studio', typography: 'academic', composer: 'rules', director: 'rules' };
const CINE_LESSON = {
    subject_name: 'Render check', session_title: 'Energy (cinematic)',
    concept_map: [{ id: 'energy', title: 'Energy', depends_on: [] }],
    scenes: [
        { type: 'content', concept_id: 'energy', title: 'WHAT IS ENERGY?', aadhi_position: 'right',
            html: "<div class='definition'><span class='keyword'>Energy</span> is the ability to do work.</div>",
            narration: 'Here is the definition we need today. [SYNC] Energy is the ability to do work.' },
        { type: 'content', concept_id: 'energy', title: 'HOW ENERGY CHANGES', aadhi_position: 'left',
            html: '<ol><li>A ball is lifted</li><li>It gains stored energy</li><li>It falls and speeds up</li></ol>',
            narration: 'Watch the energy change step by step. First, [SYNC] a ball is lifted. Then [SYNC] it gains stored energy. Finally [SYNC] it falls and speeds up.' },
        { type: 'content', concept_id: 'energy', title: 'STORED ENERGY', aadhi_position: 'right',
            html: "<div class='formula-block'>\\[E = m g h\\]</div><p>Mass times gravity times height.</p>",
            narration: 'Stored energy has a simple formula. [SYNC] E equals m g h.' }
    ]
};
// Determinism: rendered twice, the two videos must be the same frame for frame (seeded random, the mascot's clip frames, the
// particles); three scenes so that the default three ranges meet at two seams
const DET_LESSON = {
    subject_name: 'Render check', session_title: 'Determinism',
    scenes: [
        { type: 'title', title: 'THE SAME TWICE', subtitle: 'Determinism', aadhi_position: 'left', narration: 'Two renders of this lesson must match exactly.' },
        { type: 'content', title: 'ONE IDEA', aadhi_position: 'right', html: '<p>First point.</p><p>Second point.</p>',
            narration: 'Here is the first idea of this short check. [SYNC] First point. [SYNC] Second point.' },
        { type: 'content', title: 'ANOTHER IDEA', aadhi_position: 'left', html: '<ul><li>Alpha</li><li>Beta</li></ul>',
            narration: 'And now the second idea. [SYNC] Alpha comes first, [SYNC] then beta.' }
    ]
};
// The tab-capture smoke: two short scenes, nothing missing
const SMOKE_LESSON = {
    subject_name: 'Render check', session_title: 'Screen recording smoke',
    scenes: [
        { type: 'title', title: 'A SHORT RECORDING', subtitle: 'Smoke test', aadhi_position: 'left', narration: 'This is a short recording.' },
        { type: 'content', title: 'ONE IDEA', aadhi_position: 'right', html: '<p>One short idea.</p>', narration: 'Here is [SYNC] one short idea.' }
    ]
};
// Aadhi's head and upper body at 1920x1080 per placement (the 1280x720 clips drawn 1.5x, measured on the clips: left x 9-32 %,
// right x 70-94 %, centre x 40-63 % of the width; the head from 4 %)
const AADHI = { left: { x: 180, y: 40, w: 420, h: 520 }, right: { x: 1350, y: 40, w: 440, h: 520 }, center: { x: 870, y: 130, w: 300, h: 430 } };
// his whole body (left out of the parity measure: the clip runs at its own phase in the preview and the export) and the captions
const AADHI_BODY = { left: { x: 120, y: 0, w: 600, h: 1080 }, right: { x: 1290, y: 0, w: 560, h: 1080 }, center: { x: 720, y: 0, w: 520, h: 1080 } };
const CAPTION_BAND = { x: 0, y: 900, w: 1920, h: 180 };
// The recording look from page load (a recording has it from its first frame; some layout, like the side panel's height, is set
// when a scene is drawn): the player bar hidden, body[data-recording] (+ data-render: the glass blur stays on, as in render mode)
function recordingLookOnLoad() {
    const apply = () => {
        const bar = document.getElementById('voice-control-bar');
        if (bar) bar.classList.add('hidden-force');
        if (document.body) { document.body.setAttribute('data-recording', ''); document.body.setAttribute('data-render', ''); }
        // a playing lesson (the shell's top bar steps aside: body.lesson-playing), as in a recording from its first frame
        if (document.body && document.body.classList.contains('presentation-active')) document.body.classList.add('lesson-playing');
        if (window.mascot && typeof window.mascot.setRecording === 'function') window.mascot.setRecording(true);
    };
    document.addEventListener('DOMContentLoaded', apply);
    window.addEventListener('load', () => { apply(); setTimeout(apply, 500); });
}
// the page's recording look in a preview (export.js setRecordingUi + the render page's data-render, which keeps the glass blur)
async function recordingLook(page, on) {
    await page.evaluate(v => {
        document.getElementById('voice-control-bar').classList.toggle('hidden-force', v);
        document.body.toggleAttribute('data-recording', v);
        document.body.toggleAttribute('data-render', v);
        if (window.mascot && typeof mascot.setRecording === 'function') mascot.setRecording(v);
    }, on);
}
const VIEW = { width: 1920, height: 1080 };

// ---- server render helpers ------------------------------------------------------------------------------------------------
// Follows a render export until it ends (status COMPLETED / FAILED / CANCELLED): every phase, message and frame count seen
async function followRender(id, { timeoutMs = RENDER_MS, every = 750, stopWhen = null } = {}) {
    const seen = { phases: [], phaseAt: {}, messages: [], frames: [], unreachable: 0, record: null, polls: 0, startedAt: Date.now() };
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) {
        let rec = null;
        try { rec = (await api('GET', `/api/exports/${id}`)).data; } catch (e) { seen.unreachable += 1; }
        if (rec && rec.id) {
            seen.polls += 1;
            seen.record = rec;
            const r = rec.render || {};
            if (r.phase && seen.phases[seen.phases.length - 1] !== r.phase) seen.phases.push(r.phase);
            if (r.phase && !(r.phase in seen.phaseAt)) seen.phaseAt[r.phase] = Date.now();
            if (r.message && seen.messages[seen.messages.length - 1] !== r.message) seen.messages.push(r.message);
            if (Number.isFinite(r.frames_done)) seen.frames.push({ phase: r.phase, done: r.frames_done, total: r.frames_total, at: Date.now() });
            if (stopWhen && stopWhen(rec)) return { ...seen, stopped: true };
            if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(rec.status)) return seen;
        }
        await sleep(every);
    }
    return { ...seen, timedOut: true };
}
function jobDir(id) { return path.join(RENDER_DIR, 'jobs', `job-${id}`); }
function storedFile(id, ext) {
    if (!fs.existsSync(EXPORTS_DIR)) return null;
    for (const d of fs.readdirSync(EXPORTS_DIR)) {
        const f = path.join(EXPORTS_DIR, d, `${id}${ext}`);
        if (fs.existsSync(f)) return f;
    }
    return null;
}
function readJson(file) { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return null; } }
function rangeFiles(id) {
    const dir = path.join(jobDir(id), 'ranges');
    if (!fs.existsSync(dir)) return [];
    return fs.readdirSync(dir).map(n => { const st = fs.statSync(path.join(dir, n)); return { name: n, size: st.size, mtime: st.mtimeMs }; });
}
// The render timeline's scenes as spans on the video clock, matched to the lesson by title: [{ index, title, t, end }]
function sceneSpans(tl, lesson, duration) {
    const scenes = (tl && tl.scenes) || [];
    return scenes.map((s, i) => ({ index: lesson.scenes.findIndex(x => norm(x.title) === norm(s.title)), title: s.title, type: s.type, t: s.t,
        end: i + 1 < scenes.length ? scenes[i + 1].t : duration }));
}
const spanOf = (spans, index) => spans.find(s => s.index === index) || null;
// The scene a timeline entry belongs to: its own scene field (a lesson index or a title) or the span containing its time
function entryScene(entry, spans, lesson) {
    if (Number.isInteger(entry.scene) && entry.scene >= 0 && entry.scene < lesson.scenes.length) return entry.scene;
    if (typeof entry.scene === 'string') { const i = lesson.scenes.findIndex(x => norm(x.title) === norm(entry.scene)); if (i >= 0) return i; }
    const s = spans.find(x => entry.t >= x.t && entry.t < x.end);
    return s ? s.index : -1;
}

// ---- preview helpers (normal preview at 1920x1080, the Phase 20 way) --------------------------------------------------------
async function startPlaying(p, projectId, cinematic) {
    await p.goto(`${BASE}/?project_id=${projectId}`);
    await p.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await p.click('#start-lecture-btn');
    await p.waitForFunction(() => isIntroRunning || document.body.classList.contains('presentation-active'), null, { timeout: 15000 }).catch(() => {});
    await p.evaluate(() => skipIntroSequence());
    await p.waitForFunction(() => document.body.classList.contains('presentation-active')
        && getComputedStyle(document.getElementById('intro-sequence-container')).display === 'none', null, { timeout: 30000 });
    if (cinematic) await p.waitForFunction(() => slides.length && slides.every(s => s.cinematic_plan), null, { timeout: 30000 }).catch(() => {});
    await sleep(1500);
}
// One scene played with its narration to the end, the next one not started (a quiz: its question and countdown, 4.5 s in)
async function playScene(p, index, quiz = false) {
    await p.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; currentSlide = n; renderSlide(n); }, index);
    if (quiz) return sleep(4500);
    await sleep(1600);
    await p.waitForFunction(() => window.slideSyncState && window.slideSyncState.audioFinished, null, { timeout: 60000 }).catch(() => {});
    await sleep(900);
}
// What the board shows once a scene has played: the revealed items (Classic cascade order), the visual box, the cue bubble
function boardFacts() {
    const rect = el => { if (!el) return null; const r = el.getBoundingClientRect(); return r.width && r.height ? { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) } : null; };
    const container = document.getElementById('slide-content-container');
    const selectors = 'h2, h3, p, li, .math-block, .definition, .formula-block, .info-callout, .warning-callout, .tip-callout, pre';
    const items = container ? [...container.querySelectorAll(selectors)].filter(item => {
        let parent = item.parentElement;
        while (parent && parent !== container) { if (parent.matches(selectors)) return false; parent = parent.parentElement; }
        return true;
    }) : [];
    const cue = document.querySelector('.mascot-cue');
    return { items: items.map(el => ({ rect: rect(el), text: el.textContent.trim().slice(0, 60) })), board: rect(container),
        visual: rect(document.getElementById('jxgbox')), notice: !!document.querySelector('#jxgbox .visual-notice'),
        cue: cue ? { state: cue.getAttribute('data-cue') || '', x: getComputedStyle(cue).getPropertyValue('--mascot-cue-x'), y: getComputedStyle(cue).getPropertyValue('--mascot-cue-y'), rect: rect(cue.querySelector('.mascot-cue-bubble')) } : null };
}
// The element a timeline sync entry names, when the preview can find it: a Classic cascade reveal's {text} (the smallest visible
// element on the stage whose text starts with it: a board item, the title's subtitle), or a string id / data attribute / selector
function resolveTargets(targets) {
    const rect = el => { if (!el) return null; const r = el.getBoundingClientRect(); return r.width && r.height ? { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) } : null; };
    const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
    return targets.map(t => {
        if (t === null || t === undefined || t === '') return null;
        if (typeof t === 'object') {
            const want = norm(t.text).slice(0, 40);
            if (want.length < 3) return null;
            let best = null;
            for (const el of document.querySelectorAll('h1, h2, h3, h4, p, li, pre, div, span')) {
                const r = rect(el);
                if (!r || el.closest('#voice-control-bar, #subtitle-track, .export-overlay')) continue;
                const have = norm(el.textContent);
                if (have.slice(0, want.length) !== want || have.length > want.length * 3 + 20) continue;
                if (!best || r.w * r.h < best.w * best.h) best = r;
            }
            return best;
        }
        const s = String(t);
        const tries = [() => document.getElementById(s), () => document.querySelector(`[data-cine-id="${CSS.escape(s)}"]`), () => document.querySelector(`[data-element-id="${CSS.escape(s)}"]`),
            () => document.querySelector(`[data-sync-target="${CSS.escape(s)}"]`), () => document.querySelector(`[data-id="${CSS.escape(s)}"]`), () => document.querySelector(s)];
        for (const f of tries) { try { const el = f(); if (el && rect(el)) return rect(el); } catch (e) { /* not a selector */ } }
        return null;
    });
}
const clampRect = (r, pad = 6) => r && ({ x: Math.max(0, r.x - pad), y: Math.max(0, r.y - pad), w: Math.min(VIEW.width - Math.max(0, r.x - pad), r.w + 2 * pad), h: Math.min(VIEW.height - Math.max(0, r.y - pad), r.h + 2 * pad) });

// Aadhi across the range seams: each range is a separate page that pre-rolls the scene before it; the clip on screen at a seam
// must carry on from the frame the previous range ended on (step 0 or 1), or Aadhi jumps to another pose
function seamSteps(mp4, full, spans, lesson) {
    return ((full && full.ranges) || []).filter(r => r.offset > 0).map(r => {
        const n = Math.round(r.offset * FPS);
        const before = spans.find(s => s.t < r.offset - 0.001 && s.end >= r.offset - 0.001);
        const pl = before && lesson.scenes[before.index] && lesson.scenes[before.index].aadhi_position;
        if (!AADHI[pl]) return { at: r.offset, frame: n, placement: pl || null, judged: false };
        const q = qa.clipSequence(mp4, path.join(REPO, 'video_template', `aadhi_${pl}.mp4`), r.offset - 0.2, r.offset + 0.05, AADHI[pl], { minMargin: 0 });
        const x = q.seq.find(v => v.n === n - 1), y = q.seq.find(v => v.n === n);
        if (!x || !y) return { at: r.offset, frame: n, placement: pl, judged: false };
        const step = (y.idx - x.idx + q.clipFrames) % q.clipFrames;
        return { at: r.offset, frame: n, placement: pl, from: x.idx, to: y.idx, step, judged: x.margin >= 0.4 && y.margin >= 0.4, margins: [x.margin, y.margin] };
    });
}
const seamText = seams => seams.map(s => `seam ${s.at}s (frame ${s.frame}, ${s.placement}): ${s.from === undefined ? 'not measurable' : `clip frame ${s.from} → ${s.to} (step ${s.step})${s.judged ? '' : ', not confident'}`}`).join('; ');

// How fast a render went: frames, wall time (request to completion), time per phase, pages (ranges)
function speedOf(followed, cfr, full, startedAt) {
    const at = followed.phaseAt || {};
    const order = ['preparing', 'capturing', 'mixing', 'finishing', 'done'].filter(ph => ph in at);
    const phaseSeconds = {};
    order.forEach((ph, i) => { if (i + 1 < order.length) phaseSeconds[ph] = +((at[order[i + 1]] - at[ph]) / 1000).toFixed(1); });
    const wall = ((at.done || Date.now()) - startedAt) / 1000;
    const minutes = cfr.duration / 60;
    return { pages: ((full && full.ranges) || []).length || null, frames: cfr.frames, videoSeconds: +cfr.duration.toFixed(2), wallSeconds: +wall.toFixed(1), phaseSeconds,
        captureFps: phaseSeconds.capturing ? +(cfr.frames / phaseSeconds.capturing).toFixed(2) : null, overallFps: +(cfr.frames / wall).toFixed(2),
        renderMinutesPerVideoMinute: +(wall / 60 / minutes).toFixed(2), plan: full && full.ranges ? full.ranges.map(r => ({ from: r.fromScene, to: r.toScene, frames: r.frames })) : null };
}
const speedText = sp => `${sp.pages} page(s): ${sp.frames} frames (${sp.videoSeconds} s) in ${sp.wallSeconds} s wall ${JSON.stringify(sp.phaseSeconds)}, ${sp.overallFps} fps overall`
    + `${sp.captureFps ? `, ${sp.captureFps} fps while capturing` : ''}, ${sp.renderMinutesPerVideoMinute} render min per video min`;

// ---- the checks on one rendered file (main and cinematic) -------------------------------------------------------------------
async function checkRenderedFile(tag, id, lesson, followed, preview) {
    const out = { tag, id };
    const rec = followed.record || {};
    const mp4 = storedFile(id, '.mp4');
    const full = readJson(path.join(jobDir(id), 'timeline.full.json'));
    const stored = readJson(storedFile(id, '.timeline.json') || '');
    out.files = { mp4: mp4 && path.relative(OUT, mp4), full: !!full, stored: !!stored };
    check(`(${tag}) the render finished: COMPLETED, an MP4 registered (format mp4, 1920x1080, with sound), the full render timeline kept`,
        rec.status === 'COMPLETED' && !!mp4 && rec.format === 'mp4' && rec.width === 1920 && rec.height === 1080 && rec.has_audio && !!full,
        `${rec.status}; ${out.files.mp4 || 'no MP4'}; record ${JSON.stringify({ format: rec.format, w: rec.width, h: rec.height, audio: rec.has_audio, duration: rec.duration_seconds, error: rec.error_message })}; full timeline ${!!full}`);
    if (!mp4) return out;
    // -- format, CFR, frame count
    const p = qa.probe(mp4);
    const cfr = qa.checkCfr(mp4, FPS, { probe: p });
    out.probe = { ...p, file: undefined };
    out.cfr = cfr;
    check(`(${tag}) 1920x1080, H.264 yuv420p + AAC`, p.width === 1920 && p.height === 1080 && p.codec === 'h264' && p.pixFmt === 'yuv420p' && p.audio && p.audio.codec === 'aac',
        `${p.codec} ${p.profile} ${p.width}x${p.height} ${p.pixFmt}; audio ${p.audio && `${p.audio.codec} ${p.audio.sampleRate} Hz ${p.audio.channels} ch`}; ${(p.size / 1048576).toFixed(1)} MB`);
    check(`(${tag}) exactly 30 fps CFR: every frame interval 1/30 s (+-1 ms), r_frame_rate = avg_frame_rate = 30/1, frames = round(duration x 30) +- 1`, cfr.ok,
        `${cfr.frames} frames, expected ${cfr.expectedFrames} for ${cfr.duration} s (container ${cfr.formatDuration} s); r ${cfr.rFrameRate}, avg ${cfr.avgFrameRate}; intervals ${JSON.stringify(cfr.intervalMs)} ms, ${cfr.offIntervals} off${cfr.firstOff.length ? ' ' + JSON.stringify(cfr.firstOff) : ''}`);
    const r = rec.render || {};
    const tlFrames = full && Number.isFinite(full.frames) ? full.frames : null;
    check(`(${tag}) the frame count agrees with the run's own count (render.frames_total, the timeline's frames)`,
        (r.frames_total === null || r.frames_total === undefined || Math.abs(r.frames_total - cfr.frames) <= 1) && (tlFrames === null || Math.abs(tlFrames - cfr.frames) <= 1),
        `file ${cfr.frames}; render.frames_total ${r.frames_total}; timeline frames ${tlFrames}, duration ${full && full.duration}`);
    const spans = sceneSpans(full, lesson, cfr.duration);
    out.spans = spans;
    const played = lesson.scenes.map((s, i) => i);
    check(`(${tag}) the render timeline has every scene, in order, on the video clock`, spans.length === played.length && spans.every((s, i) => s.index === i && s.t >= 0 && s.t < cfr.duration),
        spans.map(s => `${s.index}:"${s.title}"@${r3(s.t)}`).join(' → '));
    // -- the timeline matches the picture: each scene appears at its logged time (the tab capture's scene log ran ahead of the
    //    picture by 0.9 s median, 1.5 s at most: p22 BEFORE), so chapters and subtitles land on the right frames
    // (the scene's arrival: the first frame-to-frame change well above the motion before it, Aadhi and the particles included)
    const lags = spans.map(s => {
        const { times, diffs } = qa.frameDiffs(mp4, Math.max(0, s.t - 0.4), Math.min(cfr.duration, s.t + 2));
        const before = diffs.filter((d, i) => times[i + 1] < s.t - 0.05).sort((a, b) => a - b);
        const usual = before.length ? before[before.length >> 1] : 0.5;
        const i = diffs.findIndex(d => d > Math.max(3, usual * 4));
        const change = i >= 0 ? times[i + 1] : null;
        return { scene: s.index, t: r3(s.t), change: change === null ? null : r3(change), lag: change === null ? null : r3(change - s.t) };
    });
    out.lags = lags;
    check(`(${tag}) every scene appears on screen at its timeline time (the picture changes within 0.4 s after it, not before)`,
        lags.length > 0 && lags.every(l => l.lag !== null && l.lag >= -1 / FPS - 0.001 && l.lag <= 0.4),
        lags.map(l => `${l.scene}@${l.t}s ${l.lag === null ? 'no change' : (l.lag >= 0 ? '+' : '') + Math.round(l.lag * 1000) + ' ms'}`).join(', '));
    // -- black edges: three moments of every scene, and the intro
    const times = [1, 6, 11].filter(t => !spans.length || t < spans[0].t);
    spans.forEach(s => { const len = s.end - s.t; times.push(s.t + Math.min(0.6, len / 4), s.t + len / 2, s.end - Math.min(0.4, len / 4)); });
    const edges = qa.blackEdges(mp4, times.filter(t => t >= 0 && t < cfr.duration - 0.02));
    const flagged = edges.filter(e => e.flagged && e.flagged.length);
    out.edges = edges;
    const widest = edges.reduce((m, e) => Math.max(m, ...(e.bars ? [e.bars.left, e.bars.right, e.bars.top, e.bars.bottom] : [0])), 0);
    check(`(${tag}) no black edges (letterbox / pillarbox) in ${edges.length} sampled frames, aadhi_left's baked-in pillars masked`, flagged.length === 0,
        flagged.length ? flagged.slice(0, 6).map(e => `${e.t}s ${e.flagged.join('+')} (bars ${JSON.stringify(e.bars)}, centre ${e.centre})`).join('; ') : `widest uniformly dark edge ${widest} px`);
    for (const e of flagged.slice(0, 3)) qa.frameAt(mp4, e.t, path.join(OUT, `${tag}-edge-${e.t.toFixed(2)}s.png`));
    // -- sound: audible overall; the narration starts where the timeline logged them
    const audio = (full && full.audio) || [];
    check(`(${tag}) audible sound (mean volume above -40 dB)`, p.meanVolume !== null && p.meanVolume > -40, `mean ${p.meanVolume} dB, peak ${p.maxVolume} dB; ${audio.length} audio events (${[...new Set(audio.map(a => a.kind))].join(', ')})`);
    const narr = audio.filter(a => a.kind === 'narration' && Number.isFinite(a.t) && a.t < cfr.duration - 0.3);
    const onsets = narr.map(a => ({ t: r3(a.t), onset: qa.audioOnset(mp4, a.t, { before: 0.3, after: 0.6, db: -45, quiet: 0.08 }) }));
    const measured = onsets.filter(o => o.onset);
    const late = measured.filter(o => Math.abs(o.onset.lead) > 0.08);
    out.onsets = onsets;
    check(`(${tag}) the narration is heard from its logged starts (+-80 ms, every start after a quiet moment)`, narr.length > 0 && measured.length >= Math.min(3, narr.length) && late.length === 0,
        `${narr.length} narration starts, ${measured.length} measurable: ${measured.map(o => `${o.t}s ${o.onset.lead >= 0 ? '+' : ''}${Math.round(o.onset.lead * 1000)} ms`).join(', ')}${late.length ? '; off: ' + late.map(o => o.t).join(', ') : ''}`);
    // -- VTT + chapters from the render timeline (the stored, cleaned timeline is what they are made from)
    let vtt = { status: null, text: '' };
    let chapters = { status: null, text: '' };
    for (let k = 0; k < 30 && vtt.status !== 200; k++) { vtt = await apiText(`/api/exports/${id}/outputs/vtt`); if (vtt.status !== 200) await sleep(1000); }
    for (let k = 0; k < 30 && chapters.status !== 200; k++) { chapters = await apiText(`/api/exports/${id}/outputs/chapters`); if (chapters.status !== 200) await sleep(1000); }
    fs.writeFileSync(path.join(OUT, `${tag}-subtitles.vtt`), vtt.text || '');
    fs.writeFileSync(path.join(OUT, `${tag}-chapters.txt`), chapters.text || '');
    const cues = qa.parseVtt(vtt.text);
    const chs = qa.parseChapters(chapters.text);
    const tlCues = ((stored && stored.cues) || []).filter(c => c.text && Number.isFinite(c.start));
    // the full timeline's cues that keep words once cleaned (tags, [SYNC] / [PAUSE] removed) must all be in the stored timeline
    const words = s => String(s || '').replace(/<[^>]*>/g, ' ').replace(/\[(SYNC|PAUSE)(:[\d.]+)?\]/gi, ' ').replace(/\s+/g, ' ').trim();
    const fullCues = ((full && full.cues) || []).filter(c => words(c.text) && Number.isFinite(c.start));
    const lost = fullCues.filter(c => !tlCues.some(s => Math.abs(s.start - c.start) <= 0.02));
    const unmatched = tlCues.filter(c => !cues.some(v => Math.abs(v.start - c.start) <= 0.1));
    const sceneChapters = chs.filter(c => spans.length && c.t >= Math.floor(spans[0].t) - 1);
    const chapterTimesOk = spans.every(s => sceneChapters.some(c => norm(c.title) === norm(s.title) && Math.abs(c.t - s.t) <= 1.01));
    check(`(${tag}) VTT subtitles and chapters present and matching the render timeline (every cue start, every scene title and time)`,
        vtt.status === 200 && chapters.status === 200 && tlCues.length > 0 && unmatched.length === 0 && cues.length >= tlCues.length
        && cues.every(c => c.end <= cfr.duration + 0.05) && sceneChapters.map(c => norm(c.title)).join('|') === spans.map(s => norm(s.title)).join('|') && chapterTimesOk
        && lost.length === 0,
        `VTT ${vtt.status}: ${cues.length} cues for ${tlCues.length} timeline cues (full timeline ${fullCues.length}${lost.length ? ', ' + lost.length + ' not in the stored one' : ''})${unmatched.length ? ', unmatched ' + unmatched.slice(0, 3).map(c => `${r3(c.start)}s "${c.text.slice(0, 30)}"`).join('; ') : ''}; chapters ${chapters.status}: ${chs.map(c => `${c.t}s ${c.title}`).join(' | ')}`);
    // -- [SYNC] reveals on the expected frame +- 1
    const syncList = (full && Array.isArray(full.sync)) ? full.sync : null;
    out.sync = [];
    if (syncList) {
        const byScene = {};
        syncList.filter(e => Number.isFinite(e.t)).forEach(e => { const i = entryScene(e, spans, lesson); (byScene[i] = byScene[i] || []).push(e); });
        for (const [k, list] of Object.entries(byScene)) {
            const index = +k;
            const span = spanOf(spans, index);
            if (!span || !preview || !preview.page) continue;
            list.sort((a, b) => a.t - b.t);
            await playScene(preview.page, index);
            const facts = await preview.page.evaluate(boardFacts);
            // a Classic cascade reveal names its item ({segment, item, text}); a cinematic moment names its element
            const targetRects = await preview.page.evaluate(resolveTargets, list.map(e => (e.target === undefined ? null : e.target)));
            list.forEach((e, n) => {
                // measured: what makes something appear (reveals, labels); emphasis, highlights and camera moves are listed only
                if (!/reveal|label/i.test(String(e.kind || ''))) return;
                // left out: a reveal in the scene's first 1.5 s (its entrance still moves the board) or less than 0.4 s after another
                // moment that already changes the picture
                const before = list.slice(0, n).filter(x => e.t - x.t > 0.02 && e.t - x.t < 0.4);
                if (e.t - span.t < 1.5 || before.length) return;
                // left out: an item due in the last 0.5 s of its narration segment. The page shows what is left 0.2 s after the
                // segment's last half second begins (revealSegmentElements, no timeline entry), so it may appear before its entry
                const seg = audio.filter(a => a.kind === 'narration' && a.t <= e.t + 0.001 && (a.end === undefined || a.end >= e.t - 0.05)).pop();
                // (also an item the page shows as left over at the segment's end, logged with leftover: true: no [SYNC] moment)
                if (e.leftover || (seg && Number.isFinite(e.due) && Number.isFinite(seg.clipDuration) && e.due >= seg.clipDuration - 0.5)) {
                    out.syncEnd = (out.syncEnd || []).concat([{ scene: index, t: r3(e.t), due: e.due, segment: seg ? seg.clipDuration : null, leftover: !!e.leftover }]);
                    return;
                }
                const item = e.target && typeof e.target === 'object' && Number.isInteger(e.target.item) ? facts.items[e.target.item] : null;
                const how = targetRects[n] ? 'target' : item ? 'item' : 'board';
                const region = clampRect(targetRects[n] || (item && item.rect) || facts.board);
                if (!region) return;
                // the page applies frame ceil(t x 30) when the event fires, and the reveal already shows on that frame
                const expected = Math.ceil(e.t * FPS - 0.02);
                const found = qa.syncFrame(mp4, Math.max(span.t + 0.2, e.t - 0.4), Math.min(span.end, e.t + 0.6), region);
                out.sync.push({ scene: index, kind: e.kind, target: e.target, t: r3(e.t), logged: e.frame, expected, frame: found.frame, diff: found.diff, shift: found.shift,
                    region, how, error: found.frame === null ? null : found.frame - expected });
            });
        }
    } else if (preview && preview.page) {
        // no sync list in the timeline: the Classic moment from the narration (character position x the segment's duration / rate)
        for (const index of preview.syncScenes || []) {
            const span = spanOf(spans, index);
            if (!span) continue;
            const seg = audio.find(a => a.kind === 'narration' && a.t >= span.t - 0.05 && a.t < span.end);
            if (!seg) continue;
            const text = String(lesson.scenes[index].narration).split(/\[PAUSE(?::\d+(?:\.\d+)?)?\]/i)[0].trim();
            const parts = text.split('[SYNC]');
            const clean = text.replace(/\[SYNC\]/g, '');
            let duration = Number.isFinite(seg.duration) ? seg.duration : null;
            if (duration === null && seg.src) {
                const f = path.join(OUT, `narration-${index}${path.extname(new URL(seg.src, BASE).pathname) || '.mp3'}`);
                const res = await fetch(new URL(seg.src, BASE), { headers: { Authorization: 'Bearer ' + token } });
                if (res.ok) { fs.writeFileSync(f, Buffer.from(await res.arrayBuffer())); duration = qa.probe(f, { volume: false }).duration; }
            }
            if (!duration) continue;
            await playScene(preview.page, index);
            const facts = await preview.page.evaluate(boardFacts);
            let at = 0;
            parts.slice(0, -1).forEach((part, n) => {
                at += part.length;
                const t = seg.t + (at / clean.length) * duration / (seg.rate || 1);
                if (t - span.t < 1.0 || !facts.items[n]) return;
                const region = clampRect(facts.items[n].rect);
                const expected = Math.ceil(t * FPS - 0.02);
                const found = qa.syncFrame(mp4, Math.max(span.t + 0.2, t - 0.4), Math.min(span.end, t + 0.6), region);
                out.sync.push({ scene: index, kind: 'derived', t: r3(t), expected, frame: found.frame, diff: found.diff, region, error: found.frame === null ? null : found.frame - expected });
            });
        }
    }
    // the timeline's own frame numbers are on the video clock (frame = ceil(t x 30)), like its times
    if (syncList && syncList.some(e => Number.isInteger(e.frame))) {
        const offClock = syncList.filter(e => Number.isInteger(e.frame) && Number.isFinite(e.t) && Math.abs(e.frame - Math.ceil(e.t * FPS - 0.02)) > 1);
        check(`(${tag}) the render timeline's sync entries carry video frame numbers that match their times`, offClock.length === 0,
            offClock.length ? `${offClock.length} of ${syncList.length} off: ${offClock.slice(0, 4).map(e => `t ${e.t}s → frame ${e.frame} (video frame ${Math.ceil(e.t * FPS - 0.02)})`).join('; ')}` : `${syncList.length} entries`);
    }
    const syncOff = out.sync.filter(s => s.error === null || Math.abs(s.error) > 1);
    check(`(${tag}) each [SYNC] reveal shows on its expected frame +- 1 (${syncList ? 'the render timeline\'s sync list' : 'derived from the narration: the timeline has no sync list'})`,
        out.sync.length >= 2 && syncOff.length === 0,
        (out.sync.map(s => `scene ${s.scene} ${s.kind || ''} @${s.t}s: frame ${s.frame} vs ${s.expected} (region by ${s.how || 'narration'})`).join('; ') || 'no measurable reveal')
        + (out.syncEnd ? `; left out (shown as left over at the end of the narration, no [SYNC] moment): ${out.syncEnd.map(x => `scene ${x.scene} @${x.t}s`).join(', ')}` : ''));
    // -- preview / export parity (Phase 20): a scene played to its end against the frame 0.6 s before the next scene. Asserted on
    //    the preview in the recording look (its player bar hidden, as every recording hides it; the glass blur on as in render
    //    mode), with Aadhi's body and the caption band left out: his clip runs at its own phase in each, and the preview holds
    //    the whole narration as its caption after it ends. The Phase 20 number (the normal preview, its controls' rows left out)
    //    and the recording look's whole frame are reported too.
    out.parity = [];
    let recPage = null;
    let shotMs = 0;
    if (preview && preview.recPage && (preview.parityScenes || []).length) {
        try {
            recPage = await preview.recPage();
            const t = Date.now();
            await recPage.screenshot({ path: path.join(OUT, `${tag}-latency.png`) });
            shotMs = Date.now() - t; // a 1920x1080 screenshot takes a while: taken that much early, it shows the moment wanted
        } catch (e) { note(`(${tag}) the recording-look preview did not open: ${String(e.message).split(/\r?\n/)[0]}`); recPage = null; }
    }
    for (const index of (preview && preview.parityScenes) || []) {
        const shot = preview.shots[index];
        const span = spanOf(spans, index);
        if (!shot || !span) continue;
        const at = Math.max(span.t, span.end - 0.6);
        const frame = path.join(OUT, `${tag}-export-${index}.png`);
        qa.frameAt(mp4, at, frame);
        // the same moment of the scene in the recording-look preview (seconds since the scene began, as in the render timeline)
        let rec = null;
        if (recPage) {
            rec = path.join(OUT, `${tag}-preview-timed-${index}.png`);
            await recPage.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; currentSlide = n; renderSlide(n); if (typeof updateTTSButtons === 'function') updateTTSButtons(); document.body.classList.add('lesson-playing'); }, index);
            const t0 = Date.now();
            await recPage.mouse.move(5, 5);
            const wait = (at - span.t) * 1000 - (Date.now() - t0) - shotMs;
            if (wait > 0) await sleep(wait);
            await recPage.screenshot({ path: rec });
        }
        const body = AADHI_BODY[lesson.scenes[index].aadhi_position];
        const mask = [...(body ? [body] : []), CAPTION_BAND];
        out.parity.push({ scene: index, title: lesson.scenes[index].title, at: r3(at), sceneSeconds: r3(at - span.t),
            diff: rec ? qa.meanDiff(rec, null, frame, null, { mask }) : null,
            recWhole: rec ? qa.meanDiff(rec, null, frame, null) : null,
            phase20: qa.meanDiff(shot, null, frame, null, { rows: 16 }),
            preview: path.basename(rec || shot), export: path.basename(frame) });
    }
    if (recPage) await recPage.context().close().catch(() => {});
    check(`(${tag}) preview / export parity: mean difference <= 5 of 255 on a 32x18 grid in ${out.parity.length} scenes (the preview in the recording look from page load, at the export frame's moment of the scene; Aadhi's body and the caption band left out)`,
        out.parity.length > 0 && out.parity.every(x => x.diff !== null && x.diff <= 5),
        out.parity.map(x => `${x.scene} "${x.title}" ${x.diff} (whole frame ${x.recWhole}; Phase 20 way ${x.phase20})`).join(', '));
    // -- code / formula / small text as sharp as in the preview (JPEG quality 92 + H.264: the board's sharpness, export / preview)
    out.sharp = [];
    for (const index of (preview && preview.sharpScenes) || []) {
        const facts = preview.facts && preview.facts[index];
        const pair = out.parity.find(x => x.scene === index);
        if (!facts || !facts.board || !pair) continue;
        const region = clampRect(facts.board, 0);
        const timed = path.join(OUT, `${tag}-preview-timed-${index}.png`);
        const a = qa.sharpness(fs.existsSync(timed) ? timed : (preview.recShots && preview.recShots[index]) || preview.shots[index], null, region);
        const b = qa.sharpness(path.join(OUT, pair.export), null, region);
        out.sharp.push({ scene: index, title: lesson.scenes[index].title, preview: a, export: b, ratio: a ? +(b / a).toFixed(3) : null });
    }
    check(`(${tag}) code / formula boards as sharp as in the preview (sharpness export / preview >= 0.8; a 1 px blur gives about 0.57)`,
        out.sharp.length > 0 && out.sharp.every(s => s.ratio !== null && s.ratio >= 0.8), out.sharp.map(s => `${s.scene} "${s.title}" ${s.export} / ${s.preview} = ${s.ratio}`).join(', '));
    return { ...out, mp4, full, stored, p, cfr, spans };
}

// ===============================================================================================================================
const record = { parts: [...PARTS] };
let browser = null;
let exportBrowser = null;
const problems = [];
try {
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const pidMain = (await api('POST', '/save-history', LESSON)).data.id;
    const pidCine = (await api('POST', '/save-history', CINE_LESSON)).data.id;
    const pidSmoke = (await api('POST', '/save-history', SMOKE_LESSON)).data.id;
    const pidDet = (await api('POST', '/save-history', DET_LESSON)).data.id;
    record.projects = { pidMain, pidCine, pidSmoke, pidDet };
    check('the fixture lessons are saved (Classic, Cinematic, the smoke lesson, the determinism lesson)', [pidMain, pidCine, pidSmoke, pidDet].every(Number.isInteger), JSON.stringify(record.projects));
    const { chromium } = await loadPlaywright();
    const MAIN_ARGS = ['--mute-audio', '--disable-audio-output', '--autoplay-policy=no-user-gesture-required'];
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: MAIN_ARGS });
    async function newPage(b, viewport, settings = {}, recording = false) {
        const context = await b.newContext({ viewport });
        if (recording) await context.addInitScript(recordingLookOnLoad);
        await context.addInitScript(([t, s]) => {
            if (!sessionStorage.getItem('seeded')) {
                localStorage.setItem('jwt_token', t);
                Object.entries(s).forEach(([k, v]) => localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v)));
                sessionStorage.setItem('seeded', '1');
            }
        }, [token, settings]);
        await context.addInitScript(silentPage);
        await context.addInitScript(holdProbe);
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error: ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console: ${m.text().slice(0, 300)}`); });
        page.on('dialog', d => { problems.push(`dialog: ${d.message().slice(0, 200)}`); d.dismiss().catch(() => {}); });
        return { context, page };
    }
    // ---- the Classic preview: frames of the parity scenes, the board items, the missing scene's card, the cue bubble --------
    const preview = { shots: {}, recShots: {}, facts: {}, parityScenes: PARITY_SCENES, syncScenes: SYNC_SCENES, sharpScenes: [2, 3], page: null,
        recPage: async () => { const { page } = await newPage(browser, VIEW, {}, true); await startPlaying(page, pidMain, false); return page; } };
    if (PARTS.has('main') || PARTS.has('restart') || PARTS.has('preview')) await section('the Classic preview at 1920x1080', async () => {
        const { page } = await newPage(browser, VIEW);
        preview.page = page;
        await startPlaying(page, pidMain, false);
        check('the preview is silent (browser speech stubbed, no audio output)', await page.evaluate(() => /onstart/.test(String(window.speechSynthesis && window.speechSynthesis.speak))));
        for (const index of [...PARITY_SCENES, MISSING, QUIZ]) {
            await playScene(page, index, index === QUIZ);
            const file = path.join(OUT, `preview-${index}.png`);
            await page.screenshot({ path: file });
            preview.shots[index] = file;
            preview.facts[index] = await page.evaluate(boardFacts);
            if (PARITY_SCENES.includes(index)) { // the same moment in the recording look (the layout a recording has)
                await recordingLook(page, true);
                await sleep(1200);
                const rec = path.join(OUT, `preview-rec-${index}.png`);
                await page.screenshot({ path: rec });
                preview.recShots[index] = rec;
                preview.facts[index] = await page.evaluate(boardFacts);
                await recordingLook(page, false);
            }
        }
        // the cue bubble in the preview (mascot.js places it by hand-measured clip fractions): the quiz again, shot the moment the
        // bubble shows (its first state, the question mark), with the scene time it appeared at
        await page.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; currentSlide = n; renderSlide(n); }, QUIZ);
        const cueAt = Date.now();
        const cue = await page.waitForFunction(() => { const c = document.querySelector('.mascot-cue'); const st = c && c.getAttribute('data-cue');
            if (!st) return null; const r = c.querySelector('.mascot-cue-bubble').getBoundingClientRect();
            return r.width ? { state: st, rect: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) } } : null; },
        null, { timeout: 10000, polling: 50 }).then(h => h.jsonValue()).catch(() => null);
        if (cue) {
            await sleep(400); // the bubble's own pop-in finished
            preview.cueShot = path.join(OUT, 'preview-cue.png');
            await page.screenshot({ path: preview.cueShot });
            preview.cue = { ...cue, sceneSeconds: +((Date.now() - cueAt) / 1000).toFixed(2) };
        }
        note(`preview cue bubble: ${JSON.stringify(preview.cue || null)}`);
        const miss = preview.facts[MISSING];
        const gold = miss.visual ? qa.colourRows(preview.shots[MISSING], null, qa.isNoticeGold, miss.visual) : { rows: 0, longest: 0 };
        preview.missingButton = gold;
        check('the preview itself is unchanged: the AI video scene without a clip shows its notice card (the control for the gold-button detector)',
            miss.notice && gold.rows >= 12, `notice ${miss.notice}; rows of the visual box with a run of 90+ gold px: ${gold.rows} (longest ${gold.longest} px); visual box ${JSON.stringify(miss.visual)}`);
        // for the frame inspection (p22 qa/inspect_frames.mjs --lesson --facts): the lesson and where the preview drew each board
        fs.writeFileSync(path.join(OUT, 'lesson-main.json'), JSON.stringify(LESSON, null, 1));
        fs.writeFileSync(path.join(OUT, 'preview-facts.json'), JSON.stringify(preview.facts, null, 1));
    });

    // ---- refuse --------------------------------------------------------------------------------------------------------------
    if (PARTS.has('refuse')) await section('refuse', async () => {
        const bad = await api('POST', '/api/exports/render', { project_id: pidMain, missing_visuals: 'maybe' });
        check('a render request with an unknown missing-visuals choice is refused', bad.status >= 400 && bad.status < 500, `HTTP ${bad.status}`);
        const res = await api('POST', '/api/exports/render', { project_id: pidMain, missing_visuals: 'refuse' });
        let rec = res.data || {};
        let followed = null;
        if (res.status < 300 && rec.id) { followed = await followRender(rec.id, { timeoutMs: 10 * 60000 }); rec = followed.record || rec; }
        const detail = res.status >= 400 ? JSON.stringify(res.data && res.data.detail) : rec.error_message;
        const r = rec.render || {};
        const title = LESSON.scenes[MISSING].title;
        const names = new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i').test(String(detail || '')) || /FORCES IN THE REAL WORLD/i.test(JSON.stringify(res.data || {}));
        const listed = Array.isArray(r.missing) && r.missing.some(m => m.index === MISSING || norm(m.title) === norm(title));
        record.refuse = { status: res.status, exportStatus: rec.status, error: detail, render: r, phases: followed && followed.phases };
        check(`missing_visuals 'refuse': the render is refused, naming scene ${MISSING + 1} "${title}" (FAILED, error code missing_visuals, the scene in render.missing; or HTTP 409)`,
            (res.status === 409 && names) || (rec.status === 'FAILED' && r.error_code === 'missing_visuals' && listed && names),
            `HTTP ${res.status}; status ${rec.status}; error_code ${r.error_code}; missing ${JSON.stringify(r.missing)}; "${String(detail || '').slice(0, 200)}"`);
        const words = plainWords(detail);
        check('the refusal is in plain words', words.ok, `"${String(detail || '').slice(0, 200)}"${words.bad ? ' — contains ' + words.bad : ''}`);
        if (followed) check('the refusal comes before any capture (no frames captured)', !followed.frames.some(f => f.done > 0) && !storedFile(rec.id, '.mp4'),
            `phases ${followed.phases.join(' → ')}; frames ${JSON.stringify(followed.frames.slice(-1))}`);
    });

    // ---- main render ---------------------------------------------------------------------------------------------------------
    let main = null;
    if (PARTS.has('main')) await section('main render', async () => {
        const t0 = Date.now();
        const res = await api('POST', '/api/exports/render', { project_id: pidMain, missing_visuals: 'omit' });
        check(`missing_visuals 'omit': the render starts (status RECORDING)`, res.status < 300 && res.data && res.data.status === 'RECORDING', `HTTP ${res.status}: ${JSON.stringify(res.data).slice(0, 300)}`);
        if (!(res.data && res.data.id)) return;
        const id = res.data.id;
        const followed = await followRender(id);
        const seconds = (Date.now() - t0) / 1000;
        const order = ['preparing', 'capturing', 'mixing', 'finishing', 'done'];
        const idx = followed.phases.map(ph => order.indexOf(ph));
        const inOrder = idx.every((v, i) => v >= 0 && (i === 0 || v > idx[i - 1]));
        const frames = followed.frames.filter(f => f.phase === 'capturing').map(f => f.done);
        const monotonic = frames.every((v, i) => i === 0 || v >= frames[i - 1]);
        const badWords = followed.messages.map(m => ({ m, w: plainWords(m) })).filter(x => !x.w.ok);
        record.main = { id, phases: followed.phases, messages: followed.messages, seconds: Math.round(seconds), polls: followed.polls, timedOut: !!followed.timedOut };
        check('progress goes preparing → capturing → mixing → finishing → done (the phases seen, in order, ending done), with frames counting up while capturing',
            inOrder && followed.phases.includes('capturing') && followed.phases[followed.phases.length - 1] === 'done' && frames.length > 1 && monotonic && frames[frames.length - 1] > frames[0],
            `phases ${followed.phases.join(' → ')}; frames ${frames.slice(0, 3).join(', ')} … ${frames.slice(-2).join(', ')} (${frames.length} readings)${followed.timedOut ? '; TIMED OUT' : ''}`);
        check('every progress message is plain words', followed.messages.length > 0 && badWords.length === 0,
            followed.messages.slice(0, 8).map(m => `"${m}"`).join(' | ') + (badWords.length ? ' — not plain: ' + badWords.map(x => `"${x.m}" (${x.w.bad})`).join('; ') : ''));
        main = await checkRenderedFile('main', id, LESSON, followed, preview);
        if (!main.mp4) return;
        const { mp4, full, spans, cfr } = main;
        const minutes = cfr.duration / 60;
        record.main.speed = { ...speedOf(followed, cfr, full, t0), hoursFor100MinutesLinear: +(seconds / minutes * 100 / 3600).toFixed(2) };
        record.main.renderSecondsPerVideoMinute = Math.round(seconds / minutes);
        note(`render speed (the planner's choice), ${speedText(record.main.speed)}`);
        // -- Aadhi: each crossfade moves on every one of its frames; in each scene his clip frame follows the time (the frame
        //    sets: index = round(t x 24); at 30 fps that is steps 1,1,1,1,0, so never three frames of one clip frame). The clip's
        //    own near-still stretches (its first frames barely differ) make "identical frames" ambiguous, so the clip frame each
        //    video frame shows is found by matching, and only confident matches are judged (qa.clipSequence)
        const place = i => LESSON.scenes[i].aadhi_position;
        const fades = [];
        for (let i = 1; i < spans.length; i++) {
            const a = spans[i - 1], b = spans[i];
            if (!AADHI[place(a.index)] || !AADHI[place(b.index)] || place(a.index) === place(b.index)) continue;
            const { times, diffs } = qa.frameDiffs(mp4, b.t - 0.4, Math.min(b.end, b.t + 1.2));
            // the fade's first frame: the first frame-to-frame change well above Aadhi's own motion before the scene
            const before = diffs.filter((d, k) => times[k + 1] < b.t - 0.05).sort((x, y) => x - y);
            const usual = before.length ? before[before.length >> 1] : 0.5;
            const start = diffs.findIndex((d, k) => times[k + 1] >= b.t - 0.1 && d > Math.max(3, usual * 4));
            const fade = start >= 0 ? diffs.slice(start, start + 15) : [];
            fades.push({ at: r3(b.t), from: place(a.index), to: place(b.index), starts: start >= 0 ? r3(times[start + 1] - b.t) : null,
                frames: fade.length, minDiff: fade.length ? r3(Math.min(...fade)) : null });
        }
        main.fades = fades;
        check('Aadhi\'s crossfades between placements never stop: the picture changes on every frame of the 0.5 s fade (whole frame > 0.3 of 255), starting with the scene',
            fades.length >= 3 && fades.every(f => f.starts !== null && f.starts <= 0.2 && f.frames === 15 && f.minDiff > 0.3),
            fades.map(f => `${f.at}s ${f.from}→${f.to}: starts +${f.starts === null ? '?' : Math.round(f.starts * 1000)} ms, smallest change ${f.minDiff}`).join('; '));
        const moving = spans.filter(s => AADHI[place(s.index)] && s.end - s.t > 2.5).map(s => {
            const q = qa.clipSequence(mp4, path.join(REPO, 'video_template', `aadhi_${place(s.index)}.mp4`), s.t + 0.7, s.end - 0.3, AADHI[place(s.index)]);
            return { scene: s.index, placement: place(s.index), frames: q.frames, confident: q.confident, steps: q.steps, hist: q.hist, zeroShare: q.zeroShare,
                doubleZero: q.doubleZero, odd: q.odd, worstMatch: q.worstMatch };
        });
        main.moving = moving;
        check('Aadhi\'s clip frame follows the time in every scene he is in (confident matches: steps of 0 or 1 clip frame only, about one repeat in 5, never two repeats in a row)',
            moving.length >= 4 && moving.every(m => m.steps >= 20 && Object.keys(m.hist).every(k => k === '0' || k === '1') && m.doubleZero.length === 0 && m.zeroShare >= 0.15 && m.zeroShare <= 0.25),
            moving.map(m => `scene ${m.scene} (${m.placement}): ${m.confident}/${m.frames} frames matched, steps ${JSON.stringify(m.hist)}, repeats ${m.zeroShare}${m.doubleZero.length ? ', double repeats at ' + m.doubleZero.slice(0, 4).join(',') : ''}${m.odd.length ? ', jumps ' + JSON.stringify(m.odd.slice(0, 4)) : ''}`).join('; '));
        // -- Aadhi across the range seams: each range is a separate page that pre-rolls the scene before it; the clip on screen at
        //    the seam must carry on from the frame the previous range ended on (step 0 or 1), or Aadhi jumps to another pose
        const seams = seamSteps(mp4, full, spans, LESSON);
        main.seams = seams;
        // (where the clip barely moves its frame cannot be told apart: such a seam is reported, and the pages part compares the
        // whole video with a one-page render frame for frame instead)
        if (seams.length) check('Aadhi carries on across the range seams: the clip frame on screen steps 0 or 1 from a range\'s last frame to the next range\'s first (no jump to another pose)',
            seams.every(s => !s.judged || s.step <= 1),
            seamText(seams) + (seams.some(s => s.judged) ? '' : ' (no seam measurable here: see the pages part)'));
        // -- no placeholder: the notes name the omitted scene; its frames show no gold button where the preview shows one
        const notes = (full && full.notes) || [];
        const noteFor = notes.filter(n => entryScene(n, spans, LESSON) === MISSING);
        const rnotes = (followed.record.render || {}).notes;
        check(`the render timeline's notes name the scene drawn without its visual (scene ${MISSING + 1})`, noteFor.length > 0,
            `${notes.length} notes: ${notes.slice(0, 4).map(n => `${r3(n.t)}s scene ${n.scene}: "${String(n.text).slice(0, 80)}"`).join(' | ')}; render.notes ${JSON.stringify(rnotes).slice(0, 200)}`);
        const ms = spanOf(spans, MISSING);
        const box = preview.facts[MISSING] && preview.facts[MISSING].visual;
        const region = box || { x: 480, y: 200, w: 960, h: 680 };
        // a filled gold button (rows with a run of 100+ gold pixels), not the scene's gold heading
        const golds = ms ? [ms.t + 1, (ms.t + ms.end) / 2, ms.end - 0.5].map(t => ({ t: r3(t), ...qa.colourRows(mp4, t, qa.isNoticeGold, region) })) : [];
        if (ms) qa.frameAt(mp4, (ms.t + ms.end) / 2, path.join(OUT, 'main-missing-scene.png'));
        const control = preview.missingButton || { rows: 0 };
        check('no notice card: the omitted scene\'s frames have no gold button where the preview draws one (main-missing-scene.png)',
            control.rows >= 12 && golds.length === 3 && golds.every(g => g.rows < 4),
            `button rows (a run of 90+ gold px): ${golds.map(g => `${g.t}s ${g.rows} (longest run ${g.longest} px)`).join(', ')}; the preview's card ${control.rows} rows (longest ${control.longest} px); region ${JSON.stringify(region)}`);
        const allGold = spans.map(s => ({ scene: s.index, ...qa.colourRows(mp4, (s.t + s.end) / 2, qa.isNoticeGold, { x: 0, y: 0, w: 1920, h: 1080 }) }));
        note(`gold button rows in each scene's middle frame (whole frame): ${allGold.map(g => `${g.scene}: ${g.rows}`).join(', ')}`);
        // -- the quiz's sound effects: logged as synthesised sounds and heard at their times
        const qs = spanOf(spans, QUIZ);
        const sfx = ((full && full.audio) || []).filter(a => a.kind === 'sfx' && qs && a.t >= qs.t - 0.05 && a.t < qs.end);
        const heard = sfx.map(a => ({ t: r3(a.t), synth: a.synth ? a.synth.type : null, ...qa.loudness(mp4, a.t, Math.max(0.12, (a.synth && a.synth.duration) || a.duration || 0.12)) }));
        check('the quiz\'s sound effects are in the mix (logged as synthesised sounds, heard at their times: peak above -35 dB)',
            sfx.length > 0 && sfx.every(a => a.synth || a.src) && heard.every(h => h.max > -35),
            `${sfx.length} effects: ${heard.map(h => `${h.t}s ${h.synth || 'file'} peak ${h.max} dB`).join(', ')}`);
        // -- the cue bubble (mascot.js's hand-measured position kept by the mask): frames for a visual check
        if (qs) {
            const cueTimes = [qs.t + 1.5, qs.t + 3, (qs.t + qs.end) / 2];
            cueTimes.forEach((t, k) => qa.frameAt(mp4, t, path.join(OUT, `main-quiz-cue-${k + 1}.png`)));
            // the preview's bubble beside the render's at the same moment of the quiz: same place, same look (cue-preview-vs-render.png)
            if (preview.cue && preview.cueShot) {
                const r = preview.cue.rect;
                const box = { x: Math.max(0, r.x - 80), y: Math.max(0, r.y - 60), w: Math.min(1920 - Math.max(0, r.x - 80), r.w + 160), h: Math.min(1080 - Math.max(0, r.y - 60), r.h + 120) };
                const frame = path.join(OUT, 'main-quiz-cue-same-moment.png');
                qa.frameAt(mp4, qs.t + preview.cue.sceneSeconds, frame);
                qa.ffmpeg('-i', preview.cueShot, '-i', frame, '-filter_complex', `[0]crop=${box.w}:${box.h}:${box.x}:${box.y},scale=${box.w * 2}:${box.h * 2}:flags=neighbor[a];[1]crop=${box.w}:${box.h}:${box.x}:${box.y},scale=${box.w * 2}:${box.h * 2}:flags=neighbor[b];[a][b]hstack`,
                    path.join(OUT, 'cue-preview-vs-render.png'));
                const bubble = clampRect(r, 2);
                main.cue = { preview: preview.cue, diff: qa.meanDiff(preview.cueShot, null, frame, null, { w: 8, h: 8, mask: [], frame: { w: 1920, h: 1080 } }),
                    bubbleDiff: qa.sharpness(frame, null, bubble), previewBubble: qa.sharpness(preview.cueShot, null, bubble) };
                note(`cue bubble: preview ${JSON.stringify(preview.cue)}; the render's frame at the same moment of the quiz: cue-preview-vs-render.png (left preview, right render); detail in the bubble's box: render ${main.cue.bubbleDiff} / preview ${main.cue.previewBubble}`);
            }
        }
        const rec = followed.record;
        check('the render is linked to its lesson (Phase 20: lesson fingerprint) and offered like any finished video (VTT and chapters ready, the MP4 copy not needed)',
            rec.lesson_fingerprint && rec.outputs && rec.outputs.vtt && rec.outputs.vtt.status === 'ready' && rec.outputs.chapters && rec.outputs.chapters.status === 'ready',
            `fingerprint ${rec.lesson_fingerprint ? rec.lesson_fingerprint.slice(0, 12) + '…' : null}; outputs ${JSON.stringify(rec.outputs)}`);
        record.main = { ...record.main, probe: main.probe, cfr: { ...main.cfr }, edges: main.edges.length, sync: main.sync, parity: main.parity, fades, moving, onsets: main.onsets };
    });

    // ---- cinematic -----------------------------------------------------------------------------------------------------------
    if (PARTS.has('cinematic')) await section('cinematic render', async () => {
        const cine = { shots: {}, recShots: {}, facts: {}, parityScenes: [0, 1, 2], syncScenes: [0, 1, 2], sharpScenes: [1, 2], page: null,
            recPage: async () => { const { page: p } = await newPage(browser, VIEW, { 'aadhi.cinematic': CINE_SETTINGS }, true); await startPlaying(p, pidCine, true); return p; } };
        const { page } = await newPage(browser, VIEW, { 'aadhi.cinematic': CINE_SETTINGS });
        cine.page = page;
        await startPlaying(page, pidCine, true);
        for (const index of cine.parityScenes) {
            await playScene(page, index);
            const file = path.join(OUT, `cine-preview-${index}.png`);
            await page.screenshot({ path: file });
            cine.shots[index] = file;
            await recordingLook(page, true);
            await sleep(1200);
            const rec = path.join(OUT, `cine-preview-rec-${index}.png`);
            await page.screenshot({ path: rec });
            cine.recShots[index] = rec;
            cine.facts[index] = await page.evaluate(boardFacts);
            await recordingLook(page, false);
        }
        const t0 = Date.now();
        const res = await api('POST', '/api/exports/render', { project_id: pidCine, missing_visuals: 'refuse', page_settings: { 'aadhi.cinematic': CINE_SETTINGS } });
        check('a Cinematic render starts with the page\'s settings (page_settings "aadhi.cinematic")', res.status < 300 && res.data && res.data.id, `HTTP ${res.status}: ${JSON.stringify(res.data).slice(0, 300)}`);
        if (!(res.data && res.data.id)) return;
        const followed = await followRender(res.data.id);
        const out = await checkRenderedFile('cinematic', res.data.id, CINE_LESSON, followed, cine);
        record.cinematic = { id: res.data.id, seconds: Math.round((Date.now() - t0) / 1000), phases: followed.phases, sync: out.sync, parity: out.parity, cfr: out.cfr };
        await page.context().close();
    });

    // ---- determinism ---------------------------------------------------------------------------------------------------------
    if (PARTS.has('determinism')) await section('determinism', async () => {
        const runs = [];
        for (const k of [1, 2]) {
            const res = await api('POST', '/api/exports/render', { project_id: pidDet, missing_visuals: 'refuse' });
            if (!(res.data && res.data.id)) { check(`determinism: render ${k} starts`, false, `HTTP ${res.status}: ${JSON.stringify(res.data).slice(0, 200)}`); return; }
            const followed = await followRender(res.data.id);
            runs.push({ id: res.data.id, rec: followed.record || {}, mp4: storedFile(res.data.id, '.mp4'), full: readJson(path.join(jobDir(res.data.id), 'timeline.full.json')) });
        }
        const [a, b] = runs;
        check('determinism: the same lesson rendered twice, both finished', runs.every(r => r.rec.status === 'COMPLETED' && r.mp4 && r.full),
            runs.map(r => `${r.id.slice(0, 8)} ${r.rec.status}${r.rec.error_message ? ' "' + r.rec.error_message + '"' : ''}`).join('; '));
        if (!runs.every(r => r.mp4 && r.full)) return;
        // the timelines: scenes, cues, sounds and sync moments at the same times
        const shape = tl => JSON.stringify({ frames: tl.frames, scenes: tl.scenes, cues: tl.cues,
            audio: (tl.audio || []).map(x => [x.t, x.kind, x.offset, x.rate, x.clipDuration, String(x.src || '').replace(/^https?:\/\/[^/]+/, '')]),
            sync: (tl.sync || []).map(x => [x.t, x.kind, x.frame]), notes: tl.notes });
        check('determinism: the two render timelines are identical (frames, scenes, captions, sounds, sync moments)', shape(a.full) === shape(b.full),
            `frames ${a.full.frames} / ${b.full.frames}; scenes ${a.full.scenes.map(x => x.t).join(',')} / ${b.full.scenes.map(x => x.t).join(',')}; sync ${(a.full.sync || []).map(x => x.t).join(',')} / ${(b.full.sync || []).map(x => x.t).join(',')}`);
        // the pictures, frame for frame: the whole frame, Aadhi's clip frames, and a band only the particles cross
        const whole = qa.compareVideos(a.mp4, b.mp4);
        const spansA = sceneSpans(a.full, DET_LESSON, a.full.duration);
        const aadhiDiffs = spansA.map(s => {
            const pl = DET_LESSON.scenes[s.index] && DET_LESSON.scenes[s.index].aadhi_position;
            if (!AADHI[pl]) return null;
            const clip = path.join(REPO, 'video_template', `aadhi_${pl}.mp4`);
            const q1 = qa.clipSequence(a.mp4, clip, s.t + 0.7, s.end - 0.3, AADHI[pl]);
            const q2 = qa.clipSequence(b.mp4, clip, s.t + 0.7, s.end - 0.3, AADHI[pl]);
            const second = new Map(q2.seq.filter(x => x.margin >= 0.4).map(x => [x.n, x.idx]));
            const both = q1.seq.filter(x => x.margin >= 0.4 && second.has(x.n));
            const same = both.filter(x => x.idx === second.get(x.n)).length;
            const offsets = [...new Set(both.map(x => (second.get(x.n) - x.idx + q1.clipFrames) % q1.clipFrames))].slice(0, 4);
            return { scene: s.index, placement: pl, compared: both.length, same, offsets };
        }).filter(Boolean);
        const particles = qa.compareVideos(a.mp4, b.mp4, { crop: { x: 700, y: 0, w: 600, h: 140 }, size: { w: 120, h: 28 }, threshold: 0.3 });
        record.determinism = { ids: runs.map(r => r.id), whole, aadhi: aadhiDiffs, particles: { over: particles.over, worst: particles.worst, overRuns: particles.overRuns },
            seams: runs.map(r => seamSteps(r.mp4, r.full, sceneSpans(r.full, DET_LESSON, r.full.duration), DET_LESSON)) };
        check('determinism: the two videos are the same frame for frame (every frame within 0.5 of 255 at 192x108)', whole.compared > 0 && whole.over === 0,
            `${whole.compared} frames compared; ${whole.over} differ${whole.firstOver ? `, the first at frame ${whole.firstOver.n} (${(whole.firstOver.n / FPS).toFixed(2)} s; scenes start at ${spansA.map(x => x.t).join(', ')} s)` : ''}; worst frame ${whole.worst.n} (${whole.worst.diff}); runs ${JSON.stringify(whole.overRuns.slice(0, 5))}`);
        check('determinism: Aadhi shows the same clip frame at the same video frame in both renders', aadhiDiffs.length > 0 && aadhiDiffs.every(x => x.compared > 10 && x.same === x.compared),
            aadhiDiffs.map(x => `scene ${x.scene} (${x.placement}): ${x.same}/${x.compared} the same${x.same < x.compared ? `, clip-frame offsets ${x.offsets.join(',')}` : ''}`).join('; '));
        check('determinism: the floating particles are in the same places in both renders (a band only they cross)', particles.over === 0,
            `${particles.over} of ${particles.compared} frames differ (worst ${particles.worst.diff} at frame ${particles.worst.n})${particles.overRuns.length ? '; runs ' + JSON.stringify(particles.overRuns.slice(0, 5)) : ''}`);
        note(`determinism seams: ${record.determinism.seams.map((x, i) => `render ${i + 1}: ${seamText(x) || 'one range'}`).join(' | ')}`);
    });

    // ---- pages: one page and three pages against the planner's render --------------------------------------------------------
    if (PARTS.has('pages') && main && main.mp4) await section('pages', async () => {
        const ratesFile = path.join(RENDER_DIR, 'rates.json');
        const savedRates = fs.existsSync(ratesFile) ? fs.readFileSync(ratesFile, 'utf8') : null;
        record.speed = { planner: record.main && record.main.speed };
        const variants = [
            { key: 'onePage', name: 'one page', env: { RENDER_RANGES: '1' } },
            // three pages: the planner told that pages barely slow each other and that pre-roll is free, so it uses all three
            { key: 'threePages', name: 'three pages (forced)', env: { RENDER_RANGES: '3' }, rates: { captureFps: 6.5, prerollFps: 100000, alpha: 0.01, overhead: 1 } }
        ];
        const outs = {};
        for (const v of variants) {
            killServer();
            await sleep(1500);
            if (v.rates) fs.writeFileSync(ratesFile, JSON.stringify(v.rates));
            else if (savedRates !== null) fs.writeFileSync(ratesFile, savedRates);
            startServer(v.env);
            await waitForServer();
            const t0 = Date.now();
            const res = await api('POST', '/api/exports/render', { project_id: pidMain, missing_visuals: 'omit' });
            if (!(res.data && res.data.id)) { check(`pages (${v.name}): the render starts`, false, `HTTP ${res.status}: ${JSON.stringify(res.data).slice(0, 200)}`); continue; }
            const followed = await followRender(res.data.id);
            const mp4 = storedFile(res.data.id, '.mp4');
            const full = readJson(path.join(jobDir(res.data.id), 'timeline.full.json'));
            if (!mp4 || !full || (followed.record || {}).status !== 'COMPLETED') {
                check(`pages (${v.name}): the render finished`, false, `${(followed.record || {}).status} ${(followed.record || {}).error_message || ''}`);
                continue;
            }
            const cfr = qa.checkCfr(mp4, FPS);
            const speed = speedOf(followed, cfr, full, t0);
            record.speed[v.key] = speed;
            const cmp = qa.compareVideos(main.mp4, mp4, { threshold: 1.0, keep: true });
            // every seam of either render: the frames around it, and the clip frame Aadhi shows on both sides
            const seamFrames = [...new Set([...(main.full.ranges || []), ...(full.ranges || [])].filter(r => r.offset > 0).map(r => Math.round(r.offset * FPS)))].sort((a, b) => a - b);
            const around = seamFrames.map(n => ({ n, diffs: cmp.diffs.filter(d => d.n >= n - 2 && d.n <= n + 2).map(d => d.diff) }));
            const spansMain = main.spans;
            const clipAtSeams = seamFrames.map(n => {
                const t = n / FPS;
                const sp = spansMain.find(x => x.t <= t - 0.05 && x.end >= t - 0.05) || spansMain.find(x => x.t <= t && x.end > t);
                const pl = sp && LESSON.scenes[sp.index].aadhi_position;
                if (!AADHI[pl]) return { n, placement: pl || null };
                const clip = path.join(REPO, 'video_template', `aadhi_${pl}.mp4`);
                const q1 = qa.clipSequence(main.mp4, clip, t - 0.1, t + 0.1, AADHI[pl], { minMargin: 0 });
                const q2 = qa.clipSequence(mp4, clip, t - 0.1, t + 0.1, AADHI[pl], { minMargin: 0 });
                return { n, placement: pl, main: q1.seq.map(x => x.idx), other: q2.seq.map(x => x.idx) };
            });
            outs[v.key] = { id: res.data.id, mp4, full, cfr, speed, cmp: { ...cmp, diffs: undefined }, around, clipAtSeams };
            note(`render speed, ${speedText(speed)}`);
            check(`pages (${v.name}, ${speed.pages} page(s)): the same video as the planner's ${main.full.ranges.length}-page render, frame for frame (encoder noise only: every frame within 1.0 of 255), the same frame count`,
                cfr.ok && cfr.frames === main.cfr.frames && cmp.over === 0,
                `${cfr.frames} frames (planner ${main.cfr.frames}); ${cmp.over} frames over 1.0${cmp.firstOver ? `, the first at ${cmp.firstOver.n}` : ''}; worst frame ${cmp.worst.n} (${cmp.worst.diff}), mean ${cmp.mean}; ranges ${JSON.stringify(speed.plan)}`);
            check(`pages (${v.name}): at every seam the frames match (within 1.0) and Aadhi shows the same clip frame in both renders (no pose jump)`,
                around.length > 0 && around.every(a => a.diffs.length && Math.max(...a.diffs) <= 1.0) && clipAtSeams.every(c => !c.main || c.main.join() === c.other.join()),
                around.map((a, i) => `frame ${a.n}: differences ${a.diffs.join('/')}; clip frames ${clipAtSeams[i].main ? `${clipAtSeams[i].main.join(',')} vs ${clipAtSeams[i].other.join(',')}` : 'no mascot'}`).join('; ') || 'no seam in either render');
        }
        // the time a 100-minute lesson takes: the worker's own planner with the rates these renders measured (one page's capture
        // speed; how much three pages slow each other, from the slowest page; whole-lesson pre-roll at 11 ms a frame, the
        // development machine's measure). (Not rates.json: the forced three-page run seeded it with made-up rates.)
        try {
            const { planPages } = await import(pathToFileURL(path.join(REPO, 'render_worker.mjs')).href);
            const one = outs.onePage, three = outs.threePages;
            if (!one || !three) throw new Error('both page-count renders are needed');
            const captureFps = one.cfr.frames / one.speed.phaseSeconds.capturing;
            const ranges3 = three.full.ranges || [];
            const slowest = ranges3[ranges3.length - 1];
            const prerollFrames = Math.round(slowest.offset * FPS);
            const perFrame3 = (three.speed.phaseSeconds.capturing - prerollFrames * 0.011) / slowest.frames;
            const alpha = Math.log(perFrame3 * captureFps) / Math.log(Math.max(2, ranges3.length));
            const other = (one.speed.phaseSeconds.preparing || 0) + (one.speed.phaseSeconds.mixing || 0) + (one.speed.phaseSeconds.finishing || 0);
            const rates = { captureFps: +captureFps.toFixed(3), prerollFps: 90.9, alpha: +alpha.toFixed(3), overhead: Math.round(other) };
            const scenes = Array.from({ length: 100 }, (_, i) => ({ index: i, estimate: 60, hidden: false }));
            const hours = Object.fromEntries([1, 2, 3].map(k => [k, +(planPages(scenes, k, rates).seams.predictedSeconds / 3600).toFixed(2)]));
            record.speed.hundredMinutes = { rates, hours };
            note(`a 100-minute lesson (the worker's planner with the measured rates ${JSON.stringify(rates)}): one page ≈ ${hours[1]} h, two pages ≈ ${hours[2]} h, three pages ≈ ${hours[3]} h`);
        } catch (e) { note(`the 100-minute estimate could not be made: ${String(e.message).split(/\r?\n/)[0]}`); }
        record.pages = Object.fromEntries(Object.entries(outs).map(([k, o]) => [k, { id: o.id, speed: o.speed, cmp: o.cmp, around: o.around, clipAtSeams: o.clipAtSeams }]));
        // back to the server as the other parts expect it
        killServer();
        await sleep(1500);
        if (savedRates !== null) fs.writeFileSync(ratesFile, savedRates); else fs.rmSync(ratesFile, { force: true });
        startServer();
        await waitForServer();
    });

    // ---- restart -------------------------------------------------------------------------------------------------------------
    if (PARTS.has('restart')) await section('restart recovery', async () => {
        const res = await api('POST', '/api/exports/render', { project_id: pidMain, missing_visuals: 'omit' });
        if (!(res.data && res.data.id)) { check('restart: the render starts', false, `HTTP ${res.status}`); return; }
        const id = res.data.id;
        const expected = main && main.cfr ? main.cfr.frames : null;
        // kill once a range segment is finished (so the kept segments can be seen), or once capture is well under way
        const firstRange = Date.now();
        const mid = await followRender(id, { timeoutMs: RENDER_MS, every: 500, stopWhen: rec => {
            const r = rec.render || {};
            if (r.phase !== 'capturing') return false;
            const done = rangeFiles(id).some(f => /^range-\d+\.json$/.test(f.name));
            const share = expected ? r.frames_done / expected : 0;
            return done || share >= 0.85 || (!expected && r.frames_done >= 1200) || (Date.now() - firstRange > 20 * 60000 && r.frames_done > 30);
        } });
        if (!mid.stopped) { check('restart: the capture got under way before the kill', false, `phases ${mid.phases.join(' → ')}; status ${mid.record && mid.record.status}`); return; }
        const atKill = (mid.record.render || {}).frames_done;
        const rangesBefore = rangeFiles(id);
        const tree = descendants(server.pid);
        const killed = crashServer(tree); // the Job Object must take the worker, its Chrome and ffmpeg with it
        await sleep(6000);
        const survivors = stillRunning(tree.filter(p => p.pid !== killed));
        killServer(); // the launcher, if it is still there
        survivors.forEach(p => leftovers.set(p.pid, p.name));
        check('the worker, its browser and its encoder stop when the server dies (Windows Job Object)', tree.length > 1 && survivors.length === 0,
            `${tree.length} processes under the server (${[...new Set(tree.map(p => p.name))].join(', ')}); the server process ${killed} killed alone; still running after 6 s: ${survivors.map(p => `${p.name} ${p.pid}`).join(', ') || 'none'}`);
        killLeftovers();
        startServer();
        await waitForServer();
        const rangesRestart = rangeFiles(id);
        const t0 = Date.now();
        const after = await followRender(id, { timeoutMs: Math.max(10 * 60000, RENDER_MS) });
        const rec = after.record || {};
        const r = rec.render || {};
        const kept = rangesBefore.filter(f => /^range-\d+\.mp4$/.test(f.name) && rangesRestart.some(g => g.name === f.name && g.size === f.size && g.mtime === f.mtime));
        record.restart = { id, framesAtKill: atKill, rangesBefore, rangesRestart, kept: kept.map(f => f.name), phasesAfter: after.phases, status: rec.status, error: rec.error_message, render: r, seconds: Math.round((Date.now() - t0) / 1000) };
        note(`restart: killed at ${atKill} frames; ranges finished before the kill: ${rangesBefore.filter(f => /^range-\d+\.json$/.test(f.name)).map(f => f.name).join(', ') || 'none'}; finished range videos still there after the restart (kept, not redone): ${kept.map(f => f.name).join(', ') || 'none'}`);
        if (rec.status === 'COMPLETED') {
            const mp4 = storedFile(id, '.mp4');
            const cfr = mp4 ? qa.checkCfr(mp4, FPS) : { ok: false };
            check('restart: the render resumed after the restart and finished (recovered), the file CFR 30 with the uninterrupted render\'s frame count',
                !!mp4 && cfr.ok && (r.recovered || 0) >= 1 && (expected === null || Math.abs(cfr.frames - expected) <= 1),
                `${after.phases.join(' → ')}; recovered ${r.recovered}; ${cfr.frames} frames (uninterrupted ${expected}); ${Math.round((Date.now() - t0) / 1000)} s after the restart`);
            if (mp4 && main && main.mp4) {
                const span = main.spans;
                const times = span.map(s => (s.t + s.end) / 2).concat(span.length ? [span[span.length - 1].end - 0.5] : []);
                const diffs = times.map(t => ({ t: r3(t), diff: qa.meanDiff(main.mp4, t, mp4, t) }));
                check('restart: the resumed video is the same as the uninterrupted one (mean difference <= 3 of 255 at every scene\'s middle)', diffs.every(d => d.diff <= 3),
                    diffs.map(d => `${d.t}s ${d.diff}`).join(', '));
            }
        } else {
            const words = plainWords(rec.error_message);
            check('restart: the render failed cleanly after the restart, in plain words (it did not resume)', rec.status === 'FAILED' && words.ok && !!r.error_code,
                `status ${rec.status}; error_code ${r.error_code}; "${String(rec.error_message || '').slice(0, 200)}"${words.bad ? ' — contains ' + words.bad : ''}; phases ${after.phases.join(' → ')}`);
        }
    });

    // ---- tab capture smoke ---------------------------------------------------------------------------------------------------
    if (PARTS.has('tab')) await section('tab-capture smoke', async () => {
        const EXPORT_ARGS = ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'];
        exportBrowser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'], args: EXPORT_ARGS });
        const { page: p } = await newPage(exportBrowser, { width: 1280, height: 720 });
        await p.goto(`${BASE}/?project_id=${pidSmoke}`);
        await p.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await p.click('#start-videos-btn');
        await p.waitForSelector('.export-overlay.open', { timeout: 15000 });
        await sleep(600);
        const screen = p.locator('.export-overlay.open button, .export-overlay.open label', { hasText: /record the screen/i }).first();
        const choices = await p.$$eval('.export-overlay.open button', bs => bs.map(b => b.textContent.trim()).filter(Boolean));
        if (await screen.isVisible().catch(() => false)) await screen.click();
        else await p.click('.export-overlay.open .export-start-btn');
        const prompts = [];
        for (let k = 0; k < 6; k++) {
            const start = p.locator('.export-actions button', { hasText: 'Start Recording' });
            const anyway = p.locator('.export-actions button', { hasText: /Record anyway|without (these|this) visual|Record without/i });
            await Promise.race([start.waitFor({ timeout: 180000 }), anyway.waitFor({ timeout: 180000 })]);
            if (await anyway.isVisible()) { prompts.push((await p.textContent('.export-message').catch(() => '') || '').slice(0, 200)); await anyway.click(); continue; }
            await start.click();
            break;
        }
        let outcome = null;
        const end = Date.now() + 8 * 60000;
        while (!outcome && Date.now() < end) {
            await sleep(1000);
            const s = await p.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden,
                error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
            if (s.ready) outcome = 'ready';
            else if (s.error) outcome = 'error: ' + s.error;
        }
        const job = await p.evaluate(() => exportFlow.job);
        const rec = job ? (await api('GET', `/api/exports/${job.id}`)).data : null;
        const webm = job ? storedFile(job.id, '.webm') : null;
        const pr = webm ? qa.probe(webm) : null;
        record.tab = { choices, prompts, outcome, job: job && job.id, record: rec && { status: rec.status, source: rec.source, format: rec.format, render: rec.render }, probe: pr && { codec: pr.codec, w: pr.width, h: pr.height, audio: pr.audio, mean: pr.meanVolume } };
        check('the tab-capture export ("Record the screen") still records, uploads and stores a WebM with sound',
            outcome === 'ready' && rec && rec.status === 'COMPLETED' && rec.source === 'lesson' && !rec.render && pr && ['vp9', 'vp8'].includes(pr.codec) && pr.audio && pr.audio.codec === 'opus' && pr.meanVolume > -70,
            `choices ${choices.map(c => `"${c}"`).join(', ')}; prompts ${prompts.length}; ${outcome}; record ${rec && JSON.stringify({ status: rec.status, source: rec.source, format: rec.format, render: rec.render })}; file ${pr && `${pr.codec} ${pr.width}x${pr.height} + ${pr.audio && pr.audio.codec} (mean ${pr.meanVolume} dB)`}`);
        await p.context().close();
    });

    check('no page errors in the preview pages', problems.length === 0, problems.slice(0, 6).join(' | '));
} catch (e) {
    check('the check ran to the end', false, 'error: ' + (e.stack || e.message).split('\n').slice(0, 4).join(' | '));
} finally {
    if (browser) await browser.close().catch(() => {});
    if (exportBrowser) await exportBrowser.close().catch(() => {});
    killServer();
    killLeftovers();
}
const failed = results.filter(r => !r.ok).length;
record.results = results;
record.seconds = Math.round((Date.now() - STARTED) / 1000);
fs.writeFileSync(path.join(OUT, 'render-check.json'), JSON.stringify(record, null, 1));
console.log(`\n${results.length - failed}/${results.length} checks passed in ${record.seconds} s; artifacts in ${OUT}`);
process.exit(failed ? 1 : 0);
