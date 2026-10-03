// Aadhi Studio check (Phase 20) in real Chrome against a throwaway server with the local stand-ins only (AI_FAKE_PROVIDER=1:
// stand-in media and text models, the stand-in lesson writer; FAKE_TTS=1: a speech-like tone as long as the text). No real
// provider is ever called.
//
// The whole workflow is done the way a user does it, through the Studio (clicks, keys, file choosers); the page's state and
// the server are only READ for the checks. Two lessons:
//   Lesson 1 (tests/fixtures/studio/photosynthesis.txt, the full path)
//     Create      1 open the Studio   2 create a lesson "From a document"   3 the source file chosen
//     Understand  4 the Document Assistant analyses it   5 its extracted structure (sections)   6 traceability (scenes carry
//                 scene.source origin "source" with refs; the Content stage says "From your document: N scenes")
//     Plan        7 the durable lesson run writes it (real progress steps, POST /api/studio/lessons, GET /api/studio/runs/{id}
//                 until completed, the lesson opens with ?project_id=)   8 its scenes (definition, process, formula, code,
//                 comparison, quiz, summary, presenter-led)
//     Generate    9 prepare visuals (the existing lesson batch; images made by the stand-in; counts move; nothing made twice)
//                 10 the presenter (the mounted Phase 12 panel: the Aadhi Teacher, a position; the plan follows)
//                 11 the required media ready
//     Compose     12 a cinematic scene renders (the scene style switched in the mounted Phase 17 panel)   13 the
//                 synchronization plan (plan.sync)   14 a Phase 17 style applied (no generation request)
//     Edit        15 the editor opens paused from the Studio   16 reorder   17 timing (a minimum duration)   18 a visual from
//                 the Asset Library   19 a presenter setting   20 camera / transition   21 undo   22 redo   23 saved
//     Quality     24 run from the Studio   25 findings shown   26 targeted invalidation (one scene edited: the Studio says
//                 "changed since the check", only that scene stale in the editor)
//     Review      27 Visual Review from the Studio   28 approve a visual (kept)
//     Preview     29 the whole lesson from the Studio's Preview (edited order, hidden scene skipped)   30 seek between scenes
//     Export      31 export from the Studio   32 completion   33 the file (ffprobe: video, duration, audio) and its outputs
//                 (subtitles, chapters in the edited order)   34 export history: "This lesson", "Matches the current lesson"
//     Reopen      35 reload ?project_id   36 edits remain   37 approvals remain   38 the quality state and the lesson's stage
//   Lesson 2 (tests/fixtures/studio/newtons_second_law.txt, a shorter path): paste → written → style → preview → export.
//   Plus: override protection (a visual chosen in Visual Review stays after the lesson batch finishes for another scene; a style
//   chosen while the batch runs stays), partial failure (the server restarted with the stand-in failing every AI image: a
//   lesson with one library visual and one that cannot be made shows Retry · Choose existing · Continue without), the home
//   list with both lessons and their stage chips, no provider names in the Studio's own UI, the editor or the export panel
//   (Visual Review's "AI provider" line is a Phase 8 requirement and the presenter panel's collapsed advanced settings list the
//   providers by design: both left out), no page errors, nothing generated except by "Prepare visuals" (and "Retry"), all silent.
//
// Artifacts (STUDIO_CHECK_OUT): screenshots of every Studio stage, the editor, Visual Review, preview frames of representative
// scenes and the matching export frames, export-chapters*.txt, export-subtitles*.vtt, studio-check.json.
//
// Needs Chrome, ffmpeg / ffprobe, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/studio_browser_check.mjs
// Silent: --mute-audio and --disable-audio-output, browser speech stubbed; the export's tab capture keeps the tab unmuted
// (a muted tab records silence) but asks for suppressLocalAudioPlayback, with no audio output device.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const FIXTURES = path.join(REPO, 'tests', 'fixtures', 'studio');
const OUT = process.env.STUDIO_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-studio-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.STUDIO_CHECK_PORT || 9930 + (process.pid % 8));
const BASE = `http://127.0.0.1:${PORT}`;
const STARTED = Date.now();
const BUDGET_MS = Number(process.env.STUDIO_CHECK_BUDGET_MS || 3000000); // stop starting new work before an outer timeout
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
const timeLeft = () => BUDGET_MS - (Date.now() - STARTED);
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
const JWT_SECRET = 'studio-check-' + Math.random().toString(36).slice(2);
let server = null;
let serverLogs = 0;
// extra: more environment (the partial-failure part restarts the server with the stand-in failing every AI image)
function startServer(extra = {}) {
    const log = fs.openSync(path.join(OUT, `server${serverLogs++ ? '-' + serverLogs : ''}.log`), 'w');
    server = spawn(PYTHON, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
        cwd: REPO, stdio: ['ignore', log, log],
        env: { ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `asb-${process.pid}`), JWT_SECRET,
            AI_FAKE_PROVIDER: '1', FAKE_TTS: '1', AI_GENERATION_ENABLED: '1', AI_FAKE_STATE_DIR: path.join(data, 'jobs'),
            // the stand-in lesson writer takes a moment per answer, so the progress shows its real stages
            FAKE_LLM_SECONDS: '3', AI_MEDIA_LOG: '1', GEMINI_API_KEY: '', OPENAI_API_KEY: '', AI_FAKE_FAIL: '', ...extra }
    });
}
function killServer() {
    if (!server || server.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
    else server.kill('SIGKILL');
}
process.on('exit', killServer);
// a response awaited by a step that failed meanwhile must not end the whole check
process.on('unhandledRejection', e => console.log('      (a pending wait ended after its step: ' + String((e && e.message) || e).split(/\r?\n/)[0] + ')'));
async function waitForServer() {
    for (let i = 0; i < 160; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + OUT);
        try { if ((await fetch(BASE + '/studio.js')).ok) return; } catch (e) { /* not up yet */ }
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
async function until(fn, ms = 60000, every = 400) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
        try { const v = await fn(); if (v) return v; } catch (e) { /* not yet */ }
        await sleep(every);
    }
    return null;
}
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
// a scene being measured never advances to the next one (the export and the preview never set it: they advance as always)
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
function ffprobe(file) {
    const r = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height,duration', '-of', 'json', file], { encoding: 'utf8' });
    if (r.status !== 0) return null;
    const out = JSON.parse(r.stdout || '{}');
    const streams = out.streams || [];
    let duration = parseFloat((out.format || {}).duration);
    if (!Number.isFinite(duration)) { // a recording without a duration in its header: decoded to the end
        const d = spawnSync('ffmpeg', ['-v', 'info', '-i', file, '-map', '0:v:0', '-f', 'null', '-'], { encoding: 'utf8' });
        const times = [...String(d.stderr || '').matchAll(/time=(\d+):(\d+):([\d.]+)/g)];
        const last = times[times.length - 1];
        duration = last ? +last[1] * 3600 + +last[2] * 60 + parseFloat(last[3]) : NaN;
    }
    return { duration, video: streams.find(s => s.codec_type === 'video') || null, audio: streams.find(s => s.codec_type === 'audio') || null };
}
// how loud the recording's sound is (dB, mean volume): -91 is digital silence
function meanVolume(file) {
    const r = spawnSync('ffmpeg', ['-v', 'info', '-i', file, '-map', '0:a:0?', '-af', 'volumedetect', '-f', 'null', '-'], { encoding: 'utf8' });
    const m = /mean_volume:\s*(-?[\d.]+) dB/.exec(String(r.stderr || ''));
    return m ? parseFloat(m[1]) : null;
}
const grid = (file, w = 32, h = 18) => [...ffmpeg('-i', file, '-vf', `scale=${w}:${h}:flags=area`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')];
const difference = (a, b) => a.reduce((s, v, i) => s + Math.abs(v - (b[i] || 0)), 0) / a.length;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const norm = v => String(v === undefined || v === null ? '' : v).trim().toLowerCase().replace(/\s+/g, ' ');

// Media / AI generation endpoints: only "Prepare visuals" (the lesson batch) may make anything. Planning (/api/cinematic/plan,
// /api/visuals/plan), reviews, quality, saving, reading files and the lesson writer are not media generation
const GENERATION = /\/generate-ai|\/api\/ai-media\/lessons\/[^/]+\/generate|\/api\/cinematic\/background|\/regenerate-manim|\/get-image|\/get-gif|\/api\/presenters\/(?:[^?]*\/)?(?:render|generate)(?:[/?]|$)|\/api\/visuals\/[^?]*generate/;
const isGeneration = (method, url) => GENERATION.test(url) || (method !== 'GET' && /\/api\/ai[-/]/.test(url) && !/\/resolve$|\/cancel$/.test(url));
const isBatch = url => /\/api\/ai-media\/lessons\/[^/]+\/generate/.test(url);
// Words that name an AI provider or model (none may reach the editor, Visual Review or the export panel outside debug mode);
// the stand-ins' own names and labels included
const PROVIDER_WORDS = /\b(gemini|openai|chatgpt|gpt-?\d|anthropic|claude|pollinations|replicate|runway(ml)?|kling|luma|pika|eleven ?labs|stability|dall-?e|imagen|veo|sora|midjourney|ltx|fake(-alt|-presenter)?|stand-in)\b/i;

// ---- page-side probes (serialized into the page; they only READ) -------------------------------------------------------------
// The Studio as drawn
function studioUI() {
    const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
    const root = document.querySelector('.studio-root');
    const q = sel => (root ? root.querySelector(sel) : null);
    return {
        open: !!root, view: root ? root.getAttribute('data-view') : null, heading: t(q('#studio-heading')), status: t(q('.studio-status')),
        error: t(q('.studio-error[role="alert"]')),
        stages: root ? [...root.querySelectorAll('button.studio-stage')].map(b => ({ key: b.dataset.stage, status: b.dataset.status,
            text: t(b.querySelector('.studio-stage-status')), summary: t(b.querySelector('.studio-stage-summary')), current: b.classList.contains('is-current') })) : [],
        detail: { title: t(q('.studio-detail-title')), status: t(q('.studio-detail-status')), summary: t(q('.studio-detail-summary')) },
        counts: root ? Object.fromEntries([...root.querySelectorAll('.studio-count')].map(c => [c.dataset.count, t(c)])) : {},
        attention: root ? [...root.querySelectorAll('.studio-attention-item')].map(li => ({ item: li.dataset.item, scene: li.dataset.sceneIndex, text: t(li),
            buttons: [...li.querySelectorAll('button')].map(b => ({ action: b.dataset.action, disabled: b.disabled })) })) : [],
        quality: t(q('.studio-quality')), qualityStatus: q('.studio-quality') ? q('.studio-quality').getAttribute('data-status') : null,
        qualityStale: t(q('.studio-quality-stale')), styleNote: t(q('.studio-style-note')), origin: t(q('.studio-origin')),
        exportLatest: t(q('.studio-export-latest')), exportMatch: q('.studio-export-match') ? { text: t(q('.studio-export-match')), matches: q('.studio-export-match').getAttribute('data-matches') } : null,
        chip: t(q('.studio-lesson-title-row .studio-chip')), chipStage: q('.studio-lesson-title-row .studio-chip') ? q('.studio-lesson-title-row .studio-chip').getAttribute('data-lesson-stage') : null,
        lessons: root ? [...root.querySelectorAll('.studio-lesson-item[data-project-id]')].map(li => ({ id: li.dataset.projectId, title: t(li.querySelector('.studio-lesson-title')),
            meta: t(li.querySelector('.studio-lesson-meta')), chip: t(li.querySelector('.studio-chip')), stage: (li.querySelector('.studio-chip') || {}).dataset ? li.querySelector('.studio-chip').dataset.lessonStage : null })) : [],
        runs: root ? root.querySelectorAll('.studio-run-item').length : 0,
        steps: root ? [...root.querySelectorAll('.studio-step')].map(s => ({ step: s.dataset.step, status: s.dataset.status, text: t(s) })) : [],
        settings: root ? [...root.querySelectorAll('section.studio-settings')].map(s => ({ kind: s.dataset.settings,
            mounted: !!s.querySelector('.studio-settings-mount #presenter-settings, .studio-settings-mount #cinematic-settings') })) : [],
        text: root ? t(root).slice(0, 4000) : '',
        ownText: root ? (() => { const c = root.cloneNode(true); c.querySelectorAll('details.presenter-advanced:not([open])').forEach(d => d.remove()); return t(c).slice(0, 6000); })() : ''
    };
}
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
        playing: !!ttsState.isPlaying, ids: slides.map(x => x && x.scene_id), titles: slides.map(x => x && x.title), types: slides.map(x => x && x.type),
        edits: slides.map(x => (x && x.edit ? JSON.parse(JSON.stringify(x.edit)) : null)),
        comps: slides.map(x => (comp(x) ? { status: comp(x).status, overrides: comp(x).overrides || {} } : null)),
        plans: slides.map(x => {
            const p = (x && x.cinematic_plan) || {};
            return { template: p.template || null, camera: p.camera ? p.camera.movement : null, transition: p.transition ? p.transition.in : null,
                presenter: p.presenter ? p.presenter.presenter_id : null, side: p.presenter ? p.presenter.side : null, shown: p.presenter ? !!p.presenter.shown : null,
                sync: p.sync ? { events: (p.sync.events || []).length, source: p.sync.timing_source || null } : null };
        }),
        sideAsset: slides.map(x => (x && x.visual_plan && x.visual_plan.side && x.visual_plan.side.asset_id) || null),
        sideReview: slides.map(x => (x && x.visual_review && x.visual_review.side ? { status: x.visual_review.side.status, asset: x.visual_review.side.asset_id || null } : null)),
        total: tl ? tl.total : null, play: tl ? tl.scenes.map(t => t.play_seconds) : [],
        dirty: m ? m.dirty : null, save: a ? a.state : null, canUndo: m ? m.canUndo() : false, canRedo: m ? m.canRedo() : false
    };
}
// The editor's chrome as drawn
function edUI() {
    const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
    const q = sel => document.querySelector(sel);
    return {
        root: !!q('.editor-root'),
        list: [...document.querySelectorAll('.editor-scene-list .editor-scene')].map(b => ({ id: b.getAttribute('data-scene-id'), index: +b.getAttribute('data-index'),
            title: t(b.querySelector('.editor-scene-title')), meta: t(b.querySelector('.editor-scene-meta')), hidden: b.classList.contains('is-hidden'),
            marks: [...b.querySelectorAll('.editor-mark')].map(m => ({ text: t(m), mark: m.getAttribute('data-mark'), approval: m.getAttribute('data-approval'),
                stale: m.getAttribute('data-stale'), chip: m.classList.contains('editor-quality-chip') })) })),
        save: q('.editor-save') ? q('.editor-save').getAttribute('data-state') : null, saveText: t(q('.editor-save')),
        time: t(q('.editor-tl-time')), play: t(q('.editor-timeline [data-action="play"]')),
        quality: t(q('.editor-top [data-action="quality"]')),
        undo: q('.editor-top [data-action="undo"]') ? q('.editor-top [data-action="undo"]').disabled : null,
        redo: q('.editor-top [data-action="redo"]') ? q('.editor-top [data-action="redo"]').disabled : null,
        text: t(q('.editor-root')) || ''
    };
}
// What the stage shows
function stageFacts() {
    const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
    const img = document.querySelector('.dynamic-side-zone .side-panel-view.active img');
    return { current: currentSlide, id: slides[currentSlide] && slides[currentSlide].scene_id, renderId: window.currentSlideRenderId,
        title: t(document.getElementById('slide-title-container')), cineTitle: t(document.querySelector('.cine-title-text')),
        board: t(document.getElementById('slide-content-container')).slice(0, 160), img: img ? img.src : null,
        cineStyle: document.body.getAttribute('data-cine-style'), cine: !!document.querySelector('[data-cine-style], .cine-title-text') };
}
// Board content cut off on the stage: a formula, code block or table wider than a box that clips it (what the video would show)
function clippedOnStage() {
    const out = [];
    const visible = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
    const name = el => el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(/\s+/)[0] : '');
    document.querySelectorAll('mjx-container, mjx-math, .formula-block, pre, table').forEach(el => {
        if (!visible(el) || el.closest('.editor-root, .studio-root, .export-overlay, .review-overlay')) return;
        const r = el.getBoundingClientRect();
        const own = getComputedStyle(el);
        if (/hidden|clip|auto|scroll/.test(own.overflowX) && el.scrollWidth > el.clientWidth + 2) { // its own content cut off
            out.push({ what: name(el), text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60), width: el.scrollWidth, box: el.clientWidth });
            return;
        }
        for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
            const cs = getComputedStyle(a);
            if (cs.overflowX === 'visible' && cs.overflow === 'visible') continue;
            const b = a.getBoundingClientRect();
            if (r.right > b.right + 2 || r.left < b.left - 2) {
                out.push({ what: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(/\s+/)[0] : ''),
                    text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60), width: Math.round(r.width), box: Math.round(b.width) });
                break;
            }
        }
    });
    return out.slice(0, 6);
}
// A recorder of what plays: every scene drawn (by renderSlide's render id), every 40 ms
function startRecorder() {
    if (window.__recTimer) clearInterval(window.__recTimer);
    window.__rec = { renders: [] };
    let last = window.currentSlideRenderId;
    window.__recTimer = setInterval(() => {
        if (window.currentSlideRenderId !== last) {
            last = window.currentSlideRenderId;
            window.__rec.renders.push({ t: Date.now(), i: currentSlide, id: slides[currentSlide] && slides[currentSlide].scene_id, playing: !!ttsState.isPlaying });
        }
    }, 40);
}
function stopRecorder() {
    if (window.__recTimer) clearInterval(window.__recTimer);
    window.__recTimer = null;
    return window.__rec || { renders: [] };
}

const problems = [];
const silent = { pages: 0, stubbed: new Set(), flags: [] };
const requests = [];          // every request the pages made: { phase, method, url, body }
const answers = [];           // the answers that matter: { phase, method, url, status }
const dialogs = [];           // window.alert / confirm / prompt shown by a page (none is expected)
const record = { lesson1: {}, lesson2: {}, partial: {} }; // what the checks saw (written to studio-check.json)
let phase = 'setup';
let browser = null;
let exportBrowser = null;
const VIEW = { width: 1280, height: 720 };

try {
    // ---- library pictures (none of them matches lesson 1's diagram, so "Prepare visuals" has something to make; the cart
    // matches lesson 2's diagram, so the Visual Router picks it there) --------------------------------------------------------
    ffmpeg('-f', 'lavfi', '-i', 'color=c=0xd62728:s=640x360,drawbox=x=120:y=150:w=300:h=120:color=0x222222@1:t=fill,drawbox=x=420:y=200:w=90:h=90:color=0x111111@1:t=fill', '-frames:v', '1', path.join(OUT, 'tractor.png'));
    ffmpeg('-f', 'lavfi', '-i', 'color=c=0x2a7fd4:s=640x360,drawbox=x=0:y=240:w=640:h=120:color=0x3c9a3c@1:t=fill,drawbox=x=280:y=60:w=80:h=80:color=0xffd700@1:t=fill', '-frames:v', '1', path.join(OUT, 'sunflower.png'));
    ffmpeg('-f', 'lavfi', '-i', 'color=c=0xeeeeee:s=640x360,drawbox=x=160:y=120:w=260:h=140:color=0x888888@1:t=3,drawbox=x=440:y=170:w=140:h=20:color=0xcc0000@1:t=fill', '-frames:v', '1', path.join(OUT, 'cart.png'));
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const tractorAsset = await upload(path.join(OUT, 'tractor.png'), 'A red tractor parked on a farm track', ['tractor', 'farm', 'vehicle', 'red']);
    const sunflowerAsset = await upload(path.join(OUT, 'sunflower.png'), 'A sunflower against a blue summer sky over a meadow', ['sunflower', 'meadow', 'summer', 'sky']);
    const cartAsset = await upload(path.join(OUT, 'cart.png'), 'A shopping cart on a flat floor with an arrow showing the push to the right', ['shopping', 'cart', 'flat', 'floor', 'arrow', 'push']);
    record.assets = { tractorAsset, sunflowerAsset, cartAsset };
    const { chromium } = await loadPlaywright();
    const MAIN_ARGS = ['--mute-audio', '--disable-audio-output', '--autoplay-policy=no-user-gesture-required'];
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: MAIN_ARGS });
    silent.flags.push('main: ' + MAIN_ARGS.join(' '));

    async function newPage(b, viewport = VIEW) {
        const context = await b.newContext({ viewport });
        await context.addInitScript(t => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('jwt_token', t); sessionStorage.setItem('seeded', '1'); } }, token);
        await context.addInitScript(silentPage);
        await context.addInitScript(holdProbe);
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error (${phase}): ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console (${phase}): ${m.text().slice(0, 300)}`); });
        page.on('response', r => {
            const url = r.url();
            const method = r.request().method();
            if (/\/api\/studio\/|\/api\/editor\/|\/api\/cinematic\/(review|style)|\/api\/visuals\/review|\/api\/quality\/lesson|\/save-history|\/api\/ai-media\/|\/api\/exports/.test(url)) {
                answers.push({ phase, method, url: url.replace(BASE, ''), status: r.status() });
            }
            const expected = (record.expected409 || []).some(x => x.phase === phase && r.status() === x.status && x.re.test(url));
            if (r.status() >= 400 && !expected && !url.endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()} (${phase}): ${method} ${url.replace(BASE, '')}`);
        });
        page.on('requestfailed', r => {
            const err = (r.failure() || {}).errorText || '';
            if (/ERR_ABORTED/.test(err) || (phase === 'restart' && /ERR_CONNECTION_REFUSED/.test(err))) return;
            problems.push(`request failed (${err}, ${phase}): ${r.url()}`);
        });
        page.on('request', r => requests.push({ phase, method: r.method(), url: r.url().replace(BASE, ''), body: r.method() === 'GET' ? null : (r.postData() || '').slice(0, 2000) }));
        page.on('dialog', d => { dialogs.push({ phase, type: d.type(), message: d.message().slice(0, 200) }); d.dismiss().catch(() => {}); });
        silent.pages += 1;
        return { context, page };
    }
    async function confirmSilent(page) {
        if (await page.evaluate(() => !window.speechSynthesis || /onstart/.test(String(window.speechSynthesis.speak)))) silent.stubbed.add(page);
    }
    const shot = (page, name) => page.screenshot({ path: path.join(OUT, name) }).catch(() => {});
    const sui = page => page.evaluate(studioUI);
    const state = page => page.evaluate(edState);
    const ui = page => page.evaluate(edUI);
    const lessonState = async pid => (await api('GET', `/api/studio/lessons/${pid}`)).data;
    const project = async pid => (await api('GET', `/api/projects/${pid}`)).data || {};

    // ==== the flow (added in parts below) =========================================================================================
    const { page } = await newPage(browser);
    await page.goto(BASE + '/');
    await page.waitForSelector('#open-studio-btn', { state: 'visible', timeout: 30000 });
    await confirmSilent(page);

    // The kinds of scene the checks look at, found in a lesson's scenes by what they show
    const KINDS = ['definition', 'process', 'formula', 'code', 'comparison', 'quiz', 'summary', 'presenter', 'diagram'];
    function kindsIn(scenes) {
        const html = s => String((s && s.html) || '');
        const tpl = s => (s && s.composition && s.composition.template) || '';
        const find = test => scenes.findIndex(s => s && test(s));
        return {
            definition: find(s => /class=['"]definition['"]/.test(html(s)) && !/^introduction$/i.test(s.title || '')),
            process: find(s => /process-list|<ol[\s>]/.test(html(s))),
            formula: find(s => /formula-block|\$\$|\\\[/.test(html(s))),
            code: find(s => /<pre[\s>][\s\S]*<code/.test(html(s))),
            comparison: find(s => /<table[\s>]/.test(html(s))),
            quiz: find(s => s.type === 'quiz_checkpoint'),
            summary: find(s => s.type === 'key-takeaway'),
            // the presenter-led explanation: "Why it matters" when the lesson has it (an introduction can be presenter-led too)
            presenter: [s => tpl(s) === 'presenter_explanation' && /why it matters/i.test(s.title || ''),
                s => tpl(s) === 'presenter_explanation' && !/^introduction$/i.test(s.title || ''),
                s => tpl(s) === 'presenter_explanation'].map(find).find(i => i >= 0) ?? -1,
            diagram: find(s => s.side_panel && s.side_panel.type === 'image')
        };
    }
    const waitView = (p, view, ms = 15000) => p.waitForSelector(`.studio-root[data-view="${view}"]`, { timeout: ms });
    // the Studio idle (no action in flight) and its lesson state loaded
    const studioIdle = p => p.waitForFunction(() => { const r = document.querySelector('.studio-root'); return r && r.getAttribute('aria-busy') !== 'true'
        && !/Loading/.test((r.querySelector('#studio-heading') || {}).textContent || ''); }, null, { timeout: 30000 }).catch(() => {});
    async function stage(p, key) {
        await p.click(`.studio-root button.studio-stage[data-stage="${key}"]`);
        await p.waitForFunction(k => { const d = document.querySelector('.studio-root .studio-detail'); return d && d.getAttribute('data-stage') === k; }, key, { timeout: 10000 });
        await studioIdle(p);
        await sleep(300);
        studioTexts.push({ phase, stage: key, text: (await sui(p)).ownText });
    }
    const studioTexts = [];   // what each Studio stage said (scanned for provider names at the end)
    const stageOf = (u, key) => u.stages.find(s => s.key === key) || {};

    // ==== LESSON 1: Create · Understand · Plan ======================================================================================
    const L1 = { file: path.join(FIXTURES, 'photosynthesis.txt'),
        names: { subject_name: 'Biology', unit_name: 'Plants', session_number: 'Session 1', session_title: 'How plants make food' } };
    let pid1 = null;
    let scenes1 = [];
    let kinds1 = {};
    await section('1. open the Studio', async () => {
        phase = 'create';
        await page.click('#open-studio-btn');
        await waitView(page, 'home');
        await until(async () => !/Loading your lessons/.test((await sui(page)).text), 15000);
        const u = await sui(page);
        await shot(page, 'l1-01-studio-home.png');
        // (Phase 21: Home is "Your lessons" with one "Create a lesson" action; the ways to start are on the Create view)
        check('1. "Open your lessons" on the start screen opens the Studio on Home: Your lessons (none yet: what to do next) and Create a lesson',
            u.open && u.view === 'home' && u.heading === 'Your lessons' && /Create a lesson/.test(u.text) && /You haven.t created a lesson yet/.test(u.text),
            `view ${u.view}, heading "${u.heading}"; "${u.text.slice(0, 220)}"; l1-01-studio-home.png`);
    });
    let analysis1 = null;
    await section('2-5. a lesson from a document: the file, the Document Assistant, its structure', async () => {
        await page.click('.studio-root [data-action="create"]'); // Create a lesson → How would you like to start?
        await waitView(page, 'create');
        const chooser = page.waitForEvent('filechooser', { timeout: 15000 });
        await page.click('.studio-root [data-action="from-document"]');
        const fc = await chooser;
        const busy = await page.evaluate(() => document.querySelector('.studio-root').getAttribute('aria-busy'));
        check('2. "Choose a document" (From a document) asks for the file (the start screen\'s file chooser) while the Studio waits',
            !!fc && busy === 'true', `file chooser ${fc ? 'shown' : 'NOT shown'}, accepts "${await page.getAttribute('#file-input', 'accept').catch(() => '')}"; Studio busy ${busy}`);
        await fc.setFiles(L1.file);
        await page.waitForSelector('.doc-overlay.open', { timeout: 15000 });
        await page.waitForSelector('.doc-overlay.open .doc-card', { timeout: 60000 });
        await page.waitForFunction(() => !/AI analysis running/.test((document.querySelector('.doc-overlay.open .doc-ai') || {}).textContent || ''), null, { timeout: 90000 });
        await sleep(500);
        const view = await page.evaluate(() => JSON.parse(JSON.stringify(window.documentAssistant.session.view)));
        analysis1 = { document_id: view.document_id || (view.document && view.document.document_id), analysis_id: view.analysis_id };
        const overview = await page.textContent('.doc-overlay.open .doc-overview').catch(() => '');
        check('3. the chosen file is the source: the Document Assistant names photosynthesis.txt (a text file) and stores it',
            /photosynthesis\.txt/.test(overview || '') && /^[0-9a-f]{32}$/.test(analysis1.document_id || '') && /^[0-9a-f]{32}$/.test(analysis1.analysis_id || ''),
            `overview "${(overview || '').replace(/\s+/g, ' ').slice(0, 200)}"; document ${analysis1.document_id}, analysis ${analysis1.analysis_id}`);
        await shot(page, 'l1-02-document-assistant.png');
        const a = view.analysis || {};
        const ready = a.readiness || {};
        check('4. the Document Assistant (Phase 11) analyses it: its title, sections, readiness (structural analysis on this server)',
            a.document && a.document.section_count >= 8 && /How Plants Make Food/i.test(JSON.stringify(a.aadhi_ready && a.aadhi_ready.title)) && typeof ready.verdict === 'string',
            `${a.document && a.document.section_count} sections; title ${JSON.stringify(a.aadhi_ready && a.aadhi_ready.title && a.aadhi_ready.title.text)}; verdict ${ready.verdict}, ready ${ready.ready_for_generation}; AI "${await page.textContent('.doc-overlay.open .doc-ai').catch(() => '')}"`);
        const structure = await page.evaluate(() => ({
            items: [...document.querySelectorAll('.doc-overlay.open .doc-structure-item input[aria-label="Section title"]')].map(i => i.value),
            formulas: [...document.querySelectorAll('.doc-overlay.open .doc-formula')].map(e => e.textContent),
            code: [...document.querySelectorAll('.doc-overlay.open .doc-code')].map(e => e.textContent)
        }));
        await page.locator('.doc-overlay.open .doc-structure').scrollIntoViewIfNeeded().catch(() => {});
        await shot(page, 'l1-03-document-structure.png');
        record.lesson1.structure = structure;
        // the document's closing "Summary" is kept as the lesson's summary (Phase 11's summary role), not as a section
        const wanted = ['Introduction', 'What is photosynthesis?', 'Inside the leaf', 'The four steps', 'The equation', 'Counting the light hours in code', 'Plants and animals compared', 'Why it matters', 'Quick check'];
        const summary = ((a.aadhi_ready || {}).summary || []).map(x => x.text || '').join(' ');
        if (!structure.formulas.length) note('the Document Assistant lists no formula for the "$$ ... $$" equation line (Phase 11 formula detection); the lesson writer keeps it (see check 8)');
        check('5. the extracted structure is shown: every section of the document in order (editable, each with its subtopics), the code, the closing summary kept as the lesson summary',
            same(structure.items, wanted) && structure.code.some(c => /for h in hours/.test(c)) && /Plants use sunlight/.test(summary),
            `sections ${structure.items.map(x => `"${x}"`).join(', ')}; summary "${summary.slice(0, 80)}"; formulas ${JSON.stringify(structure.formulas.slice(0, 2))}; code blocks ${structure.code.length}; l1-03-document-structure.png`);
    });
    let runId1 = null;
    await section('7. the lesson written by the durable run', async () => {
        phase = 'write';
        await page.click('.doc-overlay.open .doc-use');
        await waitView(page, 'name', 20000);
        const named = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.studio-root [data-field]')].map(i => [i.dataset.field, i.value])));
        await shot(page, 'l1-04-name.png');
        record.lesson1.prefilled = named;
        for (const [k, v] of Object.entries(L1.names)) await page.fill(`.studio-root [data-field="${k}"]`, v);
        // every change of the progress view, recorded as the Studio draws it (a mutation observer: a stage may last less than
        // one poll, and the "all done" view only until the lesson has opened)
        await page.evaluate(() => {
            window.__stepLog = [];
            let last = '';
            const sample = () => {
                const r = document.querySelector('.studio-root');
                if (!r) return;
                const view = r.getAttribute('data-view');
                const key = [...r.querySelectorAll('.studio-step')].map(x => `${x.dataset.step}:${x.dataset.status}`).join(' ');
                const k = `${view}|${key}`;
                if (k !== last) { last = k; window.__stepLog.push({ t: Date.now(), view, key, heading: (r.querySelector('#studio-heading') || {}).textContent || '' }); }
            };
            window.__stepObserver = new MutationObserver(sample);
            window.__stepObserver.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-view', 'data-status'] });
            window.__stepTimer = setInterval(sample, 20);
        });
        const posted = page.waitForResponse(r => /\/api\/studio\/lessons$/.test(r.url()) && r.request().method() === 'POST', { timeout: 30000 });
        await page.click('.studio-root [data-action="start-lesson"]');
        const res = await posted;
        let body = {};
        try { body = JSON.parse(res.request().postData() || '{}'); } catch (e) { /* unreadable */ }
        const answer = await res.json().catch(() => ({}));
        runId1 = answer.run_id;
        await waitView(page, 'progress', 15000).catch(() => {});
        let progressShot = false;
        const end = Date.now() + 90000;
        while (Date.now() < end) {
            const u = await sui(page);
            if (u.view === 'progress' && !progressShot && u.steps.some(s => s.status === 'active')) { await shot(page, 'l1-05-progress.png'); progressShot = true; }
            if (u.view === 'lesson') break;
            await sleep(200);
        }
        const seen = (await page.evaluate(() => { clearInterval(window.__stepTimer); if (window.__stepObserver) window.__stepObserver.disconnect(); return window.__stepLog; })).filter(x => x.view === 'progress' && x.key);
        await studioIdle(page);
        await until(async () => (await sui(page)).stages.length === 7, 20000);
        const polls = answers.filter(a => a.phase === 'write' && /\/api\/studio\/runs\//.test(a.url) && a.method === 'GET');
        const run = (await api('GET', `/api/studio/runs/${runId1}`)).data || {};
        const facts = await page.evaluate(() => ({ url: location.search, pid: currentProjectId, n: slides.length, playing: !!ttsState.isPlaying,
            board: !document.getElementById('presentation-board').classList.contains('hidden') }));
        pid1 = facts.pid;
        record.lesson1.run = { request: { ...body, system_prompt: `${(body.system_prompt || '').length} characters` }, answer, run, progress: seen, polls: polls.length };
        const u = await sui(page);
        await shot(page, 'l1-06-lesson-stages.png');
        const sawWriting = seen.some(s => /write:active/.test(s.key));
        const allDone = seen.length && /source:done write:done check:done save:done/.test(seen[seen.length - 1].key);
        check('7. "Write the lesson": POST /api/studio/lessons with the prepared source (no pasted text) and the page\'s own prompt → a run; the progress shows its real stages (source ✓, writing ●, then all ✓) from GET /api/studio/runs/{id}; the run completes and the lesson opens (?project_id=, not playing)',
            res.status() === 200 && body.source && body.source.analysis_id === analysis1.analysis_id && body.text === undefined && (body.system_prompt || '').length > 1000
            && /^[0-9a-f]{32}$/.test(runId1 || '') && sawWriting && allDone && polls.length >= 2 && polls.every(p => p.status === 200)
            && run.status === 'completed' && Number.isInteger(run.project_id) && run.project_id === pid1 && facts.url === `?project_id=${pid1}` && facts.n > 5 && !facts.playing
            && u.view === 'lesson' && u.stages.length === 7,
            `POST ${res.status()} ${JSON.stringify({ source: body.source, names: body.names, provider: body.provider, prompt: (body.system_prompt || '').length })} → ${JSON.stringify(answer)}; `
            + `progress ${seen.map(s => `[${s.key}] "${s.heading}"`).join(' → ')}; ${polls.length} run polls; run ${JSON.stringify(run)}; page ${JSON.stringify(facts)}; prefilled names ${JSON.stringify(named)}; l1-05-progress.png, l1-06-lesson-stages.png`);
    });
    await section('6. traceability', async () => {
        if (!pid1) throw new Error('no lesson');
        const pr = await project(pid1);
        scenes1 = pr.scenes || [];
        kinds1 = kindsIn(scenes1);
        const st = await lessonState(pid1);
        const origins = scenes1.map(s => (s.source ? `${s.source.origin}:${(s.source.refs || []).length}:${s.source.coverage}` : 'none'));
        const fromSource = scenes1.filter(s => s.source && s.source.origin === 'source');
        await stage(page, 'content');
        const u = await sui(page);
        await shot(page, 'l1-07-stage-content.png');
        record.lesson1.trace = { origins, origin: st.origin, source: st.source, stage: stageOf(u, 'content'), sourceDocument: pr.source_document };
        check('6. traceability: the lesson keeps its source (document + analysis); scenes from the document carry scene.source {origin "source", refs, coverage}; the Content stage says "From your document: N scenes"',
            pr.source_document && pr.source_document.analysis_id === analysis1.analysis_id && st.source && st.source.file_name === 'photosynthesis.txt'
            && fromSource.length >= scenes1.length - 3 && fromSource.every(s => Array.isArray(s.source.refs) && s.source.refs.length > 0 && s.source.coverage > 0)
            && st.origin.source === fromSource.length && new RegExp(`From your document: ${fromSource.length} scenes`).test(stageOf(u, 'content').summary || '')
            && new RegExp(`From your document: ${fromSource.length} scenes`).test(u.origin || ''),
            `${fromSource.length} of ${scenes1.length} scenes from the source (${origins.join(' ')}); state origin ${JSON.stringify(st.origin)}, source ${JSON.stringify(st.source)}; content stage "${stageOf(u, 'content').summary}"; l1-07-stage-content.png`);
        // the structure confirmed (the Content stage's checkpoint)
        await page.click('.studio-root [data-action="confirm-structure"]');
        await studioIdle(page);
        await until(async () => stageOf(await sui(page), 'content').status === 'done', 10000);
        const after = await sui(page);
        const cp = ((await lessonState(pid1)).checkpoints || {}).structure;
        check('6b. "Confirm structure" keeps the checkpoint with the lesson (the Content stage turns ✓ Done)', stageOf(after, 'content').status === 'done' && cp && cp.at,
            `stage ${JSON.stringify(stageOf(after, 'content'))}; checkpoint ${JSON.stringify(cp)}`);
    });
    await section('8. the lesson\'s scenes', async () => {
        const page_ = await page.evaluate(() => slides.map(s => ({ type: s.type, title: s.title, id: s.scene_id })));
        const missing = KINDS.filter(k => kinds1[k] === undefined || kinds1[k] < 0);
        await stage(page, 'lesson');
        const u = await sui(page);
        await shot(page, 'l1-08-stage-lesson.png');
        record.lesson1.kinds = Object.fromEntries(KINDS.map(k => [k, kinds1[k] >= 0 ? `${kinds1[k] + 1}. ${scenes1[kinds1[k]].title}` : null]));
        check('8. the written lesson has the scenes the source asks for: a definition, process steps, a formula, code, a comparison table, a quiz, a summary, a presenter-led explanation and the diagram (all with scene ids, the page holds the same scenes)',
            missing.length === 0 && same(page_.map(s => s.id), scenes1.map(s => s.scene_id)) && scenes1.every(s => /^s-[0-9a-f]{12}$/.test(s.scene_id || ''))
            && new RegExp(`${scenes1.length} scenes`).test(stageOf(u, 'lesson').summary || ''),
            `${scenes1.length} scenes: ${scenes1.map((s, i) => `${i + 1}. ${s.type} "${s.title}"`).join(' | ')}; kinds ${JSON.stringify(record.lesson1.kinds)}; missing ${missing.join(', ') || 'none'}; lesson stage "${stageOf(u, 'lesson').summary}"`);
    });

    // ==== LESSON 1: Generate · Compose (and override protection) =================================================================
    // Visual Review (from the Studio's Review stage): the visual of one scene changed to a library picture
    async function reviewPick(p, sceneIndex, assetId, shotName) {
        await p.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
        const sel = `.review-overlay.open .review-item[data-scene="${sceneIndex}"]`;
        await p.click(sel);
        await p.waitForFunction(s => document.querySelector(s).classList.contains('selected'), sel, { timeout: 10000 });
        await p.click('.review-detail [data-action="change"]');
        await p.click('.review-detail .review-change [data-action="pick"]');
        await p.waitForSelector('.asset-overlay.open', { timeout: 15000 });
        await p.waitForSelector(`.asset-overlay.open .asset-item[data-id="${assetId}"]`, { timeout: 15000 });
        await p.click(`.asset-overlay.open .asset-item[data-id="${assetId}"]`);
        await p.waitForSelector('.asset-overlay.open .asset-pick', { timeout: 15000 });
        await p.click('.asset-overlay.open .asset-pick');
        await p.waitForFunction(() => /Changed/.test((document.querySelector('.review-status') || {}).textContent || ''), null, { timeout: 30000 });
        await sleep(500);
        if (shotName) await shot(p, shotName);
    }
    async function closeReview(p) {
        await p.click('.review-panel .export-close');
        await p.waitForFunction(() => !document.querySelector('.review-overlay.open'), null, { timeout: 10000 });
        await sleep(1200); // the Studio refreshes when the panel above it closed
        await studioIdle(p);
    }
    const reviewText = p => p.evaluate(() => (document.querySelector('.review-overlay.open .review-panel') || {}).textContent || '');
    const providerShown = [];  // provider or model names seen in the editor, Visual Review or the export panel
    const reviewProviders = [];  // Visual Review's provenance line (Phase 8 requirement): recorded only
    const lookInReview = (where, text) => { const m = PROVIDER_WORDS.exec(text || ''); if (m) reviewProviders.push(`${where}: "${(text || '').slice(Math.max(0, m.index - 40), m.index + 40)}"`); };
    const lookForProviders = (where, text) => { const m = PROVIDER_WORDS.exec(text || ''); if (m) providerShown.push(`${where}: "${(text || '').slice(Math.max(0, m.index - 40), m.index + 40)}"`); };
    let override = null;
    await section('override setup: a visual changed in Visual Review from the Studio before the visuals are prepared', async () => {
        phase = 'review-early';
        await stage(page, 'review');
        await shot(page, 'l1-09-stage-review-before.png');
        await page.click('.studio-root [data-action="open-review"]');
        await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
        const listed = await page.$$eval('.review-overlay.open .review-item', els => els.map(e => ({ scene: +e.dataset.scene, slot: e.dataset.slot })));
        const target = listed.some(x => x.scene === kinds1.process) ? kinds1.process : (listed.find(x => x.scene !== kinds1.diagram) || {}).scene;
        lookInReview('Visual Review (opened)', await reviewText(page));
        await reviewPick(page, target, tractorAsset, 'l1-10-review-change-before-batch.png');
        lookInReview('Visual Review (changed)', await reviewText(page));
        await closeReview(page);
        const sc = ((await project(pid1)).scenes || [])[target] || {};
        override = { scene: target, title: scenes1[target] && scenes1[target].title, review: sc.visual_review && sc.visual_review.side, plan: sc.visual_plan && sc.visual_plan.side };
        record.lesson1.override = { listed, ...override };
        note(`Visual Review listed ${listed.length} visuals: ${listed.map(x => `${x.scene + 1}/${x.slot}`).join(' ')}; scene ${target + 1} "${override.title}" changed to the tractor picture`);
    });
    let batch = null;
    let batchRun = null;
    const runAsset = r => (r && ((r.result && r.result.asset_id) || r.asset_id)) || null;
    await section('9-11. prepare the visuals; the presenter; the media ready (and a style chosen while the batch runs)', async () => {
        // 10: the presenter panel (Phase 12) moved into the Visuals stage: the Aadhi Teacher on the left
        phase = 'presenter';
        await stage(page, 'visuals');
        const before = await sui(page);
        const st0 = await lessonState(pid1);
        await page.waitForSelector('.studio-root .studio-settings[data-settings="presenter"] #presenter-settings #presenter-select', { timeout: 15000 });
        await shot(page, 'l1-11-stage-visuals-before.png');
        await page.selectOption('.studio-root #presenter-select', 'aadhi-teacher');
        await sleep(600);
        await page.selectOption('.studio-root #presenter-position', 'left');
        await sleep(600);
        const presenter = await page.evaluate(() => ({ stored: JSON.parse(localStorage.getItem('aadhi.presenter') || 'null'), settings: { ...window.presenterSettings.settings },
            inStudio: !!document.querySelector('.studio-root .studio-settings[data-settings="presenter"] #presenter-settings') }));
        record.lesson1.presenter = presenter;
        check('10. the presenter settings (Phase 12) are mounted in the Visuals stage: the Aadhi Teacher chosen, on the left (kept in the page\'s presenter settings)',
            presenter.inStudio && presenter.settings.presenter_id === 'aadhi-teacher' && presenter.settings.position === 'left' && presenter.stored && presenter.stored.presenter_id === 'aadhi-teacher',
            `${JSON.stringify(presenter.settings)}; mounted in the Studio ${presenter.inStudio}`);
        // the scene style switched to cinematic in the Style stage's mounted panel (Phase 13 / 17); no style chosen yet
        phase = 'mode';
        await stage(page, 'style');
        await page.waitForSelector('.studio-root .studio-settings[data-settings="style"] #cinematic-settings #cinematic-mode', { timeout: 15000 });
        await page.selectOption('.studio-root #cinematic-mode', 'cinematic');
        await page.waitForSelector('.studio-root #cinematic-settings .style-option', { timeout: 15000 });
        await shot(page, 'l1-12-stage-style-cinematic.png');
        // 9: Prepare visuals (the existing lesson batch)
        phase = 'batch';
        await stage(page, 'visuals');
        const batchAnswer = page.waitForResponse(r => isBatch(r.url()) && r.request().method() === 'POST', { timeout: 60000 });
        await page.click('.studio-root [data-action="prepare-media"]');
        const br = await batchAnswer;
        batch = await br.json().catch(() => null);
        const batchAt = Date.now();
        // a style chosen while the batch runs (Style stage: Academic)
        phase = 'style-during-batch';
        await stage(page, 'style');
        const styleAnswer = page.waitForResponse(r => /\/api\/cinematic\/style/.test(r.url()) && r.request().method() === 'POST', { timeout: 20000 });
        await page.click('.studio-root #cinematic-settings .style-option[data-style="academic"]');
        const sr = await styleAnswer;
        const runAtStyle = batch && batch.runs && batch.runs[0] ? ((await api('GET', `/api/ai-media/runs/${batch.runs[0].run_id || batch.runs[0].id}`)).data || {}).status : null;
        phase = 'batch';
        await stage(page, 'visuals');
        const counts = [];
        const end = Date.now() + 120000;
        while (Date.now() < end) {
            const u = await sui(page);
            const key = JSON.stringify(u.counts);
            if (!counts.length || counts[counts.length - 1].key !== key) counts.push({ t: Date.now() - batchAt, key, summary: stageOf(u, 'visuals').summary });
            const ls = await lessonState(pid1);
            if (ls.media.generating === 0 && stageOf(u, 'visuals').status !== 'active' && /ready/.test(u.counts.ready || '') && !/● [1-9]/.test(u.counts.generating || '')) break;
            await sleep(700);
        }
        await sleep(1500);
        await page.evaluate(() => window.dispatchEvent(new Event('focus'))); // (the Studio also refreshes when the window gets the focus)
        await sleep(1500);
        const after = await sui(page);
        await shot(page, 'l1-13-stage-visuals-after.png');
        const st1 = await lessonState(pid1);
        const runs = ((await api('GET', `/api/ai-media/runs?project_id=${pid1}&limit=50`)).data || {}).runs || [];
        batchRun = runs[0] || null;
        record.lesson1.batchRun = batchRun;
        const pr = await project(pid1);
        const diagram = (pr.scenes || [])[kinds1.diagram] || {};
        const side = (diagram.visual_plan || {}).side || {};
        record.lesson1.batch = { answer: batch, counts, before: before.counts, beforeSummary: stageOf(before, 'visuals').summary, after: after.counts,
            afterSummary: stageOf(after, 'visuals').summary, media0: st0.media, media1: st1.media, runs: runs.map(r => ({ id: r.run_id || r.id, status: r.status, slot: r.slot, scene: r.scene_index })), side };
        check('9. "Prepare visuals" starts the existing lesson batch (POST /api/ai-media/lessons/{id}/generate: one picture, the diagram); the stand-in makes it; the counts move (0 ready → ready, nothing being made at the end) and the diagram scene keeps the new picture',
            br.status() === 200 && batch && Array.isArray(batch.runs) && batch.runs.length === 1 && runs.length === 1 && runs[0].status === 'completed'
            && st0.media.needed >= 1 && st1.media.needed === 0 && st1.media.generating === 0 && st1.media.ready >= 1 && side.asset_id
            && (!runAsset(runs[0]) || runAsset(runs[0]) === side.asset_id)
            && /✓ 0 ready/.test(before.counts.ready || '') && !/✓ 0 ready/.test(after.counts.ready || ''),
            `batch ${br.status()} ${batch ? batch.runs.length : '-'} run(s); runs now ${runs.map(r => `${r.slot}@${r.scene_index}:${r.status}`).join(', ')}; media ${JSON.stringify({ needed: st0.media.needed, ready: st0.media.ready })} → ${JSON.stringify({ needed: st1.media.needed, ready: st1.media.ready, generating: st1.media.generating })}; `
            + `Studio counts ${JSON.stringify(before.counts)} → ${counts.map(c => `${c.t}ms ${c.key}`).join(' → ')}; diagram plan ${JSON.stringify({ source: side.source, asset: side.asset_id, provider: side.provider })}; l1-11 / l1-13 screenshots`);
        // again: nothing is made twice
        phase = 'batch-again';
        const again = page.waitForResponse(r => isBatch(r.url()) && r.request().method() === 'POST', { timeout: 60000 });
        await page.click('.studio-root [data-action="prepare-media"]');
        const ar = await again;
        const againBody = await ar.json().catch(() => null);
        await studioIdle(page);
        const noteText = (await sui(page)).status;
        const runs2 = ((await api('GET', `/api/ai-media/runs?project_id=${pid1}&limit=50`)).data || {}).runs || [];
        check('9b. "Prepare visuals" again makes nothing (no run queued, the Studio says every visual is ready; still one generation for this lesson)',
            ar.status() === 200 && againBody && Array.isArray(againBody.runs) && againBody.runs.length === 0 && runs2.length === 1 && /ready/i.test(noteText || ''),
            `second batch ${ar.status()} ${JSON.stringify(againBody && { runs: againBody.runs.length, already: againBody.already_available })}; note "${noteText}"; generations for the lesson ${runs2.length}`);
        const v = stageOf(after, 'visuals');
        // (with a picture made and ready, "No pictures or clips to make" would not be the truth)
        check('11. the media the lesson needs is ready: the Visuals stage says ✓ Done ("All N pictures and clips ready · nothing left to make"), nothing needs attention, nothing missing or failing on the server',
            v.status === 'done' && (st1.media.ready > 0 ? /(All \d+ pictures and clips|1 picture or clip) ready · nothing left to make/.test(v.summary || '') : /No pictures/.test(v.summary || '')) && st1.media.attention === 0 && st1.media.failed === 0 && st1.media.needed === 0 && after.attention.length === 0,
            `stage ${JSON.stringify(v)}; media ${JSON.stringify({ needed: st1.media.needed, ready: st1.media.ready, generating: st1.media.generating, attention: st1.media.attention, failed: st1.media.failed })}`);
        // override protection: the visual chosen in Visual Review stays; the style chosen while the batch ran stays
        const ov = (pr.scenes || [])[override.scene] || {};
        const ovReview = (ov.visual_review || {}).side || {};
        const ovPlan = (ov.visual_plan || {}).side || {};
        record.lesson1.overrideAfter = { review: ovReview, plan: ovPlan, style: pr.cinematic_style, styleAnswer: sr.status(), runAtStyle };
        check('override: a visual chosen in Visual Review (scene "' + override.title + '": the library tractor) stays after the lesson batch finished for the diagram',
            ovReview.status === 'changed' && ovReview.asset_id === tractorAsset && (ovPlan.asset_id || tractorAsset) === tractorAsset,
            `review ${JSON.stringify({ status: ovReview.status, asset: ovReview.asset_id === tractorAsset ? 'tractor' : ovReview.asset_id })}; plan ${JSON.stringify({ asset: ovPlan.asset_id === tractorAsset ? 'tractor' : ovPlan.asset_id, source: ovPlan.source })}`);
        check('override: a style chosen in the Studio while the batch ran (Academic) stays with the lesson after the batch attached its picture',
            sr.status() === 200 && pr.cinematic_style && pr.cinematic_style.style === 'academic',
            `POST /api/cinematic/style ${sr.status()} (the batch's run was "${runAtStyle}" when the style was saved${runAtStyle === 'completed' ? ': the race window was not hit' : ''}); lesson style ${JSON.stringify(pr.cinematic_style)}`);
    });
    let styleRequestsFrom = 0;
    await section('14. a Phase 17 style applied from the Studio (no generation)', async () => {
        phase = 'style';
        styleRequestsFrom = requests.length;
        await stage(page, 'style');
        const before = stageOf(await sui(page), 'style');
        const posted = page.waitForResponse(r => /\/api\/cinematic\/style/.test(r.url()) && r.request().method() === 'POST', { timeout: 20000 });
        await page.click('.studio-root #cinematic-settings .style-option[data-style="corporate_training"]');
        const r = await posted;
        let sent = {};
        try { sent = JSON.parse(r.request().postData() || '{}'); } catch (e) { /* unreadable */ }
        // the Style stage follows the choice made in its own panel (no other action)
        const followed = await until(async () => /Corporate/i.test(stageOf(await sui(page), 'style').summary || ''), 6000, 300);
        const byItself = stageOf(await sui(page), 'style');
        await shot(page, 'l1-14-stage-style-corporate.png');
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await sleep(1500);
        const after = stageOf(await sui(page), 'style');
        const made = requests.slice(styleRequestsFrom).filter(x => isGeneration(x.method, x.url));
        const pr = await project(pid1);
        const look = await page.evaluate(() => ({ body: document.body.getAttribute('data-cine-style'), settings: { style: cinematicSettings.settings.style, mode: cinematicSettings.settings.mode } }));
        record.lesson1.style = { before, sent, byItself, after, made, saved: pr.cinematic_style, look };
        check('14. the style changed in the Style stage (Academic → Corporate Training): POST /api/cinematic/style keeps it with the lesson, NO generation request, the stage says "Style: Corporate Training" and "Changing the style never re-makes pictures or clips"',
            r.status() === 200 && sent.style === 'corporate_training' && made.length === 0 && pr.cinematic_style && pr.cinematic_style.style === 'corporate_training'
            && after.status === 'done' && /Corporate/i.test(after.summary || '') && /without making its pictures or clips again/.test((await sui(page)).styleNote || ''),
            `POST ${r.status()} ${JSON.stringify(sent)}; generation requests ${made.length}${made.length ? ': ' + made.map(x => x.url).join(', ') : ''}; saved ${JSON.stringify(pr.cinematic_style)}; stage before ${JSON.stringify(before)}, after ${JSON.stringify(after)}; l1-14-stage-style-corporate.png`);
        check('14b. the Style stage shows a style chosen in its own panel at once (without waiting for another refresh)', !!followed,
            `${followed ? 'updated' : 'NOT updated'} within 6 s: ${JSON.stringify(byItself)} (after a window focus: ${JSON.stringify(after)})`);
    });

    // ==== LESSON 1: Edit (the editor opened from the Studio) =======================================================================
    const sceneSel = id => `.editor-scene-list .editor-scene[data-scene-id="${id}"]`;
    const resumed = [];          // where the lesson started playing on its own while the editor had it paused
    // The lesson kept paused while editing: when it started playing on its own, the editor's ❚❚ Pause is pressed and recorded
    async function ensurePaused(p, context) {
        const deadline = Date.now() + 6000;
        let quietSince = null;
        let pressed = false;
        while (Date.now() < deadline) {
            const s = await p.evaluate(() => ({ playing: !!ttsState.isPlaying, open: !!document.querySelector('.editor-root'),
                label: (document.querySelector('.editor-timeline [data-action="play"]') || {}).textContent || '' }));
            if (!s.open) return;
            if (!s.playing) {
                if (quietSince === null) quietSince = Date.now();
                if (Date.now() - quietSince >= 1200) return;
                await sleep(150);
                continue;
            }
            quietSince = null;
            if (!pressed) resumed.push(context);
            pressed = true;
            if (/Pause/.test(s.label)) await p.click('.editor-timeline [data-action="play"]');
            await sleep(350);
        }
    }
    // the edits saved (the autosave idle: saved, nothing pending), the lesson paused
    async function settle(p, ms = 1800, context = null) {
        await sleep(ms);
        const ok = await p.waitForFunction(() => {
            const s = typeof lessonEditorSession !== 'undefined' ? lessonEditorSession : null;
            return !s || !s.workspace.isOpen || (s.autosave.state === 'saved' && !s.model.dirty && !s.autosave.running && !s.model.inTransaction);
        }, null, { timeout: 30000 }).then(() => true).catch(() => false);
        if (!ok) note(`(${phase}) the editor did not reach "Saved"`);
        await ensurePaused(p, context || `after an edit (${phase})`);
        return ok;
    }
    async function selectScene(p, id) {
        for (let attempt = 0; attempt < 4; attempt++) {
            await p.click(sceneSel(id));
            const ok = await p.waitForFunction(i => lessonEditorSession.workspace.selectedId === i, id, { timeout: 4000 }).then(() => true).catch(() => false);
            if (!ok) continue;
            await ensurePaused(p, `a list click (${phase})`);
            if (await p.evaluate(i => lessonEditorSession.workspace.selectedId === i, id)) break;
        }
        if (!(await p.evaluate(i => lessonEditorSession.workspace.selectedId === i, id))) throw new Error(`the scene ${id} could not be selected in the list`);
        await sleep(200);
    }
    async function openSection(p, key) {
        const open = await p.evaluate(k => !!document.querySelector(`.editor-inspector .editor-section[data-section="${k}"] .editor-section-body`), key);
        if (!open) await p.click(`.editor-inspector [data-action="section"][data-section="${key}"]`);
        await p.waitForSelector(`.editor-inspector .editor-section[data-section="${key}"] .editor-section-body`, { timeout: 5000 });
    }
    // A composition choice in the inspector: the composition review POST it makes, the scene's review record after it
    async function compositionChange(p, id, key, value, act) {
        const posted = p.waitForResponse(r => r.url().includes('/api/cinematic/review') && r.request().method() === 'POST', { timeout: 30000 });
        await act();
        const r = await posted;
        let sent = {};
        try { sent = JSON.parse(r.request().postData() || '{}'); } catch (e) { /* unreadable */ }
        await p.waitForFunction(([i, k, v]) => {
            const s = slides.find(x => x && x.scene_id === i);
            const c = s && s.visual_review && s.visual_review.composition;
            return !!(c && c.overrides && c.overrides[k] === v);
        }, [id, key, value], { timeout: 20000 }).catch(() => {});
        await settle(p, 1200);
        return { status: r.status(), sent: { action: sent.action, scene_index: sent.scene_index, overrides: sent.overrides } };
    }
    // The Asset Library in pick mode: the asset clicked, then "Use this visual"
    async function pickFromLibrary(p, assetId, shotName) {
        await p.waitForSelector('.asset-overlay.open', { timeout: 15000 });
        await p.waitForSelector(`.asset-overlay.open .asset-item[data-id="${assetId}"]`, { timeout: 15000 });
        await p.click(`.asset-overlay.open .asset-item[data-id="${assetId}"]`);
        await p.waitForSelector('.asset-overlay.open .asset-pick', { timeout: 15000 });
        if (shotName) await shot(p, shotName);
        await p.click('.asset-overlay.open .asset-pick');
        await p.waitForFunction(() => !document.querySelector('.asset-overlay.open'), null, { timeout: 10000 });
    }
    async function openEditorFromStudio(p) {
        await stage(p, 'lesson');
        await p.click('.studio-root [data-action="open-editor"]');
        await p.waitForFunction(() => typeof lessonEditorSession !== 'undefined' && lessonEditorSession && lessonEditorSession.workspace.isOpen, null, { timeout: 40000 });
        await p.waitForSelector('.editor-root .editor-scene', { timeout: 15000 });
    }
    async function closeEditorToStudio(p) {
        await p.click('.editor-top [data-action="close"]');
        await p.waitForFunction(() => !document.querySelector('.editor-root'), null, { timeout: 10000 });
        await waitView(p, 'lesson', 15000);
        await studioIdle(p);
        await sleep(800);
    }
    const ID = {};               // lesson 1's scene ids by kind
    const kindOf = id => Object.keys(ID).find(k => ID[k] === id) || id;
    let edits = null;
    await section('15 + 12, 13, 10b. the editor opens from the Studio, paused, on a cinematic stage with the plans', async () => {
        phase = 'edit';
        await openEditorFromStudio(page);
        const atOnce = await page.evaluate(() => ({ playing: !!ttsState.isPlaying, studio: !!document.querySelector('.studio-root') }));
        await settle(page, 2500, 'the editor just opened from the Studio');
        const st = await state(page);
        const u = await ui(page);
        KINDS.forEach(k => { if (kinds1[k] >= 0) ID[k] = st.ids[kinds1[k]]; });
        ID.intro = st.ids[1];
        ID.title = st.ids[0];
        ID.last = st.ids[st.ids.length - 1];  // the closing "Summary" content scene
        record.lesson1.ids = { ...ID };
        await shot(page, 'l1-15-editor-open.png');
        lookForProviders('editor (opened)', u.text);
        check('15. "Review & edit scenes" in the Studio\'s Lesson stage opens the editor (the Studio steps aside); the lesson is paused; every scene is listed',
            st.open && !atOnce.playing && !atOnce.studio && !st.playing && resumed.length === 0 && u.list.length === scenes1.length && /Play/.test(u.play || '') && same(u.list.map(x => x.id), st.ids),
            `editor open ${st.open}, playing at once ${atOnce.playing} / after ${st.playing}, Studio shown ${atOnce.studio}; ${u.list.length} scenes listed: ${u.list.map(x => x.title).join(' | ')}; play button "${u.play}"; l1-15-editor-open.png`);
        const stg = await page.evaluate(stageFacts);
        check('12. a cinematic scene renders: every scene has a composition plan (templates), the stage draws the composition in the chosen style (Corporate Training)',
            st.plans.every(x => x.template) && stg.cine && stg.cineStyle === 'corporate_training',
            `templates ${st.plans.map(x => x.template).join(',')}; stage scene ${stg.current + 1} "${stg.cineTitle || stg.title}", data-cine-style ${stg.cineStyle}`);
        const narrated = [ID.definition, ID.process, ID.formula, ID.code, ID.summary].map(id => st.ids.indexOf(id));
        check('13. the synchronization plan is present (plan.sync with timed events for the narrated content scenes)',
            narrated.every(i => st.plans[i].sync && st.plans[i].sync.events > 0),
            narrated.map(i => `${kindOf(st.ids[i])} ${JSON.stringify(st.plans[i].sync)}`).join('; '));
        const pres = st.plans.filter(x => x.presenter);
        const presScene = st.plans[st.ids.indexOf(ID.presenter)];
        record.lesson1.plans = st.plans;
        check('10b. the plan follows the presenter chosen in the Studio: the scenes are presented by the Aadhi Teacher, the presenter-led scene on the left',
            pres.length > 0 && pres.every(x => x.presenter === 'aadhi-teacher') && presScene.presenter === 'aadhi-teacher' && presScene.side === 'left',
            `presenters ${[...new Set(pres.map(x => x.presenter))].join(',')} on ${pres.length} scenes; presenter scene ${JSON.stringify(presScene)}`);
    });
    await section('16-23. edits in the editor', async () => {
        edits = {};
        // 16 reorder: the code scene one place earlier (Alt+←): before the equation
        const s0 = await state(page);
        await selectScene(page, ID.code);
        await page.keyboard.press('Alt+ArrowLeft');
        await page.waitForFunction(([c, f]) => { const ids = slides.map(s => s.scene_id); return ids.indexOf(c) === ids.indexOf(f) - 1; }, [ID.code, ID.formula], { timeout: 10000 }).catch(() => {});
        await settle(page);
        const s1 = await state(page);
        edits.order = s1.ids.slice();
        check('16. reorder: Alt+← moves the selected code scene before the equation (scene ids kept, saved)',
            s1.ids.indexOf(ID.code) === s1.ids.indexOf(ID.formula) - 1 && s1.ids.indexOf(ID.code) === s0.ids.indexOf(ID.formula) && same([...s1.ids].sort(), [...s0.ids].sort()) && s1.save === 'saved',
            `order ${s1.ids.map(kindOf).join(',')}; save ${s1.save}`);
        // 17 timing: a minimum duration on the code scene
        await selectScene(page, ID.code);
        const i0 = s1.ids.indexOf(ID.code);
        const play0 = s1.play[i0];
        const hold = Math.ceil(play0) + 4;
        edits.hold = hold;
        await page.locator('#editor-field-min-seconds').fill(String(hold));
        await page.locator('#editor-field-min-seconds').press('Enter');
        const applied = await page.waitForFunction(([id, h]) => ((slides.find(s => s.scene_id === id) || {}).edit || {}).min_seconds === h, [ID.code, hold], { timeout: 4000 }).then(() => true).catch(() => false);
        if (!applied) await page.click('.editor-scenes .editor-panel-head');
        await settle(page);
        const s2 = await state(page);
        check(`17. timing: a minimum duration of ${hold} s on the code scene lengthens it and the timeline`,
            s2.edits[s2.ids.indexOf(ID.code)] && s2.edits[s2.ids.indexOf(ID.code)].min_seconds === hold && s2.play[s2.ids.indexOf(ID.code)] === hold && s2.total > s1.total + 3,
            `play ${play0} s → ${s2.play[s2.ids.indexOf(ID.code)]} s; timeline ${s1.total} s → ${s2.total} s`);
        // a scene hidden (the introduction): the preview and the export skip it
        await selectScene(page, ID.intro);
        await page.click('.editor-inspector [data-action="toggle-hidden"]');
        await page.waitForFunction(id => ((slides.find(s => s.scene_id === id) || {}).edit || {}).hidden === true, ID.intro, { timeout: 10000 }).catch(() => {});
        await settle(page);
        // 18 a visual from the Asset Library on the closing Summary scene
        await selectScene(page, ID.last);
        await openSection(page, 'visual');
        lookForProviders('editor (visual section)', (await ui(page)).text);
        const vposted = page.waitForResponse(r => r.url().includes('/api/visuals/review') && r.request().method() === 'POST', { timeout: 30000 });
        await page.click('.editor-inspector [data-action="choose-visual"][data-slot="side"]');
        await pickFromLibrary(page, sunflowerAsset, 'l1-16-asset-picker.png');
        const vr = await vposted;
        let vsent = {};
        try { vsent = JSON.parse(vr.request().postData() || '{}'); } catch (e) { /* unreadable */ }
        await page.waitForFunction(([id, a]) => { const s = slides.find(x => x.scene_id === id); return s && s.visual_plan && s.visual_plan.side && s.visual_plan.side.asset_id === a; }, [ID.last, sunflowerAsset], { timeout: 20000 }).catch(() => {});
        await settle(page, 1500);
        const s3 = await state(page);
        check('18. a visual from the Asset Library: "Choose from library" on the closing Summary scene → the sunflower picture: POST /api/visuals/review (choose), the scene\'s visual changes',
            vr.status() === 200 && vsent.action === 'choose' && vsent.asset_id === sunflowerAsset && s3.sideAsset[s3.ids.indexOf(ID.last)] === sunflowerAsset,
            `POST ${vr.status()} ${JSON.stringify({ action: vsent.action, slot: vsent.slot, asset: vsent.asset_id === sunflowerAsset ? 'sunflower' : vsent.asset_id })}; scene visual ${s3.sideAsset[s3.ids.indexOf(ID.last)] === sunflowerAsset ? 'sunflower' : s3.sideAsset[s3.ids.indexOf(ID.last)]}`);
        // 19 a presenter setting: the presenter-led scene's presenter moved to the right
        await selectScene(page, ID.presenter);
        await openSection(page, 'presenter');
        const pr = await compositionChange(page, ID.presenter, 'presenter_position', 'right',
            () => page.click('.editor-inspector [data-action="override"][data-key="presenter_position"][data-value="right"]'));
        const s4 = await state(page);
        const pi = s4.ids.indexOf(ID.presenter);
        check('19. a presenter setting: the presenter-led scene\'s presenter moved to the right (inspector → Presenter): POST /api/cinematic/review, the plan follows',
            pr.status === 200 && same(pr.sent.overrides, { presenter_position: 'right' }) && s4.plans[pi].side === 'right' && s4.comps[pi] && s4.comps[pi].status === 'changed',
            `POST ${pr.status} ${JSON.stringify(pr.sent)}; plan side ${s4.plans[pi].side}; review ${JSON.stringify(s4.comps[pi])}`);
        // 20 camera (the code scene: still) and transition (into the equation)
        await selectScene(page, ID.code);
        await openSection(page, 'camera');
        const cr = await compositionChange(page, ID.code, 'camera', 'static', () => page.selectOption('#editor-field-camera', 'static'));
        await selectScene(page, ID.formula);
        await openSection(page, 'transition');
        const t0 = (await state(page)).plans[(await state(page)).ids.indexOf(ID.formula)].transition;
        const tvalue = ['zoom', 'slide', 'wipe', 'crossfade'].find(v => v !== t0);
        const tr = await compositionChange(page, ID.formula, 'transition', tvalue, () => page.selectOption('#editor-field-transition', tvalue));
        const s5 = await state(page);
        edits.transition = tvalue;
        check(`20. camera and transition: the code scene's camera "Still" and the transition into the equation "${tvalue}" (inspector): POST /api/cinematic/review twice, both plans follow`,
            cr.status === 200 && tr.status === 200 && s5.plans[s5.ids.indexOf(ID.code)].camera === 'static' && s5.plans[s5.ids.indexOf(ID.formula)].transition === tvalue,
            `camera POST ${cr.status} → ${s5.plans[s5.ids.indexOf(ID.code)].camera}; transition ${t0} → POST ${tr.status} → ${s5.plans[s5.ids.indexOf(ID.formula)].transition}`);
        // 21 undo (the ↶ button): the transition back; 22 redo (Ctrl+Y): the transition again
        const undoPost = page.waitForResponse(r => r.url().includes('/api/cinematic/review') && r.request().method() === 'POST', { timeout: 30000 });
        await page.click('.editor-top [data-action="undo"]');
        await undoPost.catch(() => {});
        await page.waitForFunction(([id, v]) => { const s = slides.find(x => x.scene_id === id); const c = s && s.visual_review && s.visual_review.composition;
            return !(c && c.overrides && c.overrides.transition) && s.cinematic_plan && s.cinematic_plan.transition && s.cinematic_plan.transition.in === v; }, [ID.formula, t0], { timeout: 20000 }).catch(() => {});
        await settle(page, 1200);
        const s6 = await state(page);
        check('21. undo (↶ Undo): the transition change is undone (no transition override left on the equation, its planned transition back)',
            !(s6.comps[s6.ids.indexOf(ID.formula)] && s6.comps[s6.ids.indexOf(ID.formula)].overrides.transition) && s6.plans[s6.ids.indexOf(ID.formula)].transition === t0,
            `review ${JSON.stringify(s6.comps[s6.ids.indexOf(ID.formula)])}; transition ${s6.plans[s6.ids.indexOf(ID.formula)].transition}`);
        await selectScene(page, ID.formula); // the keyboard focus on the scene list (shortcuts never fire inside a field)
        const redoPost = page.waitForResponse(r => r.url().includes('/api/cinematic/review') && r.request().method() === 'POST', { timeout: 30000 });
        await page.keyboard.press('Control+y');
        await redoPost.catch(() => {});
        await page.waitForFunction(([id, v]) => { const s = slides.find(x => x.scene_id === id); const c = s && s.visual_review && s.visual_review.composition;
            return !!(c && c.overrides && c.overrides.transition === v) && s.cinematic_plan && s.cinematic_plan.transition && s.cinematic_plan.transition.in === v; }, [ID.formula, tvalue], { timeout: 20000 }).catch(() => {});
        await settle(page, 1200);
        const s7 = await state(page);
        check('22. redo (Ctrl+Y): the transition change is back', s7.comps[s7.ids.indexOf(ID.formula)] && s7.comps[s7.ids.indexOf(ID.formula)].overrides.transition === tvalue && s7.plans[s7.ids.indexOf(ID.formula)].transition === tvalue,
            `review ${JSON.stringify(s7.comps[s7.ids.indexOf(ID.formula)])}; transition ${s7.plans[s7.ids.indexOf(ID.formula)].transition}`);
        // 23 saved in place
        await settle(page);
        const s8 = await state(page);
        const u8 = await ui(page);
        const puts = answers.filter(a => a.phase === 'edit' && a.method === 'PUT' && /\/api\/editor\//.test(a.url));
        const hist = requests.filter(x => x.phase === 'edit' && x.method === 'POST' && /\/save-history/.test(x.url));
        const server = await project(pid1);
        await shot(page, 'l1-17-editor-after-edits.png');
        lookForProviders('editor (after edits)', u8.text);
        edits.ids = s8.ids.slice();
        record.lesson1.timelineTotal = s8.total; // the edited lesson's planned length (the editor's timeline, hidden scenes left out)
        record.lesson1.edits = { ...edits, final: s8.ids.map(kindOf), puts: puts.map(a => a.status) };
        check('23. saved: the editor says "✓ Saved"; every edit went through PUT /api/editor in place (no /save-history); the server has the edited order, the hold, the hidden scene and the chosen visual',
            u8.save === 'saved' && /Saved/.test(u8.saveText || '') && !s8.dirty && puts.length >= 3 && puts.every(a => a.status === 200) && hist.length === 0
            && same((server.scenes || []).map(x => x.scene_id), s8.ids) && ((server.scenes || []).find(x => x.scene_id === ID.code) || {}).edit.min_seconds === hold
            && (((server.scenes || []).find(x => x.scene_id === ID.intro) || {}).edit || {}).hidden === true,
            `"${u8.saveText}"; ${puts.length} PUT /api/editor (${[...new Set(puts.map(a => a.status))].join('/')}); /save-history while editing ${hist.length}; server order ${(server.scenes || []).map(x => kindOf(x.scene_id)).join(',')}; l1-17-editor-after-edits.png`);
        check('the lesson stays paused while editing (no edit or selection started playback)', resumed.length === 0, resumed.join(' | ') || 'never');
        await closeEditorToStudio(page);
        // the Studio shows the lesson as it is now (its state is fetched again when it opens)
        const counted = await until(async () => { const x = stageOf(await sui(page), 'lesson').summary || ''; return /moved/.test(x) && /hidden/.test(x); }, 10000, 400);
        const su = await sui(page);
        await shot(page, 'l1-18-studio-after-editor.png');
        const ed = (await lessonState(pid1)).editor;
        record.lesson1.afterEditor = { stages: su.stages, editor: ed };
        check('the editor closes back to the Studio, which shows the lesson as edited: the Lesson stage counts the moved and the hidden scene',
            su.view === 'lesson' && !!counted && ed.moved >= 1 && ed.hidden === 1,
            `view ${su.view}; lesson stage ${JSON.stringify(stageOf(su, 'lesson'))} (within 10 s: ${counted ? 'yes' : 'NO'}); server editor counts ${JSON.stringify(ed)}; l1-18-studio-after-editor.png`);
    });

    // ==== LESSON 1: Quality · Review ===============================================================================================
    let report1 = null;
    await section('24-25. the quality check from the Studio', async () => {
        phase = 'quality';
        await stage(page, 'review');
        const before = await sui(page);
        const posted = page.waitForResponse(r => r.url().includes('/api/quality/lesson') && r.request().method() === 'POST', { timeout: 90000 });
        await page.click('.studio-root [data-action="run-quality"]');
        const r = await posted;
        report1 = await r.json().catch(() => null);
        await studioIdle(page);
        await sleep(1500);
        const u = await sui(page);
        await shot(page, 'l1-19-stage-review-quality.png');
        const st = await lessonState(pid1);
        const cp = (st.checkpoints || {}).quality || {};
        const counts = (report1 && report1.summary && report1.summary.counts) || {};
        const n = ['notice', 'warning', 'error', 'blocking'].reduce((a, k) => a + (Number.isInteger(counts[k]) && counts[k] > 0 ? counts[k] : 0), 0);
        const words = report1 && (report1.status === 'good' ? '✓ Quality looks good' : n ? `⚠ ${n} ${n === 1 ? 'thing' : 'things'} to review` : '⚠ Some things to review');
        const issues = (report1 && Array.isArray(report1.issues)) ? report1.issues : [];
        record.lesson1.quality = { http: r.status(), status: report1 && report1.status, counts, issues: issues.map(f => ({ severity: f.severity, rule: f.rule, scene: f.scene, message: f.message })),
            checkpoint: cp, state: st.quality, studio: u.quality, stage: stageOf(u, 'review') };
        issues.forEach(f => note(`  finding: ${f.severity} ${f.rule} scene ${f.scene === null || f.scene === undefined ? '-' : f.scene + 1}: ${f.message}`));
        check('24. "Check quality" in the Studio\'s Review stage runs the Phase 18 check (POST /api/quality/lesson 200) and keeps its result with the lesson (checkpoint "quality": status, counts, the lesson version)',
            r.status() === 200 && report1 && cp.status === report1.status && cp.lesson_fingerprint === st.fingerprint && st.quality && st.quality.stale === false
            && /Quality not checked yet/.test(stageOf(before, 'review').summary || '') && !/not checked/.test(stageOf(u, 'review').summary || ''),
            `POST ${r.status()} status ${report1 && report1.status} counts ${JSON.stringify(counts)}; checkpoint ${JSON.stringify(cp)}; state quality ${JSON.stringify(st.quality)}; review stage "${stageOf(before, 'review').summary}" → "${stageOf(u, 'review').summary}"`);
        check('25. the findings are shown: the Studio says what the check found (the report\'s count of things to review, or "looks good")',
            u.quality === words && u.qualityStatus === (report1 && report1.status),
            `Studio "${u.quality}" [${u.qualityStatus}], expected "${words}" from ${issues.length} findings; l1-19-stage-review-quality.png`);
    });
    await section('26. targeted invalidation after one scene is edited', async () => {
        phase = 'quality-edit';
        await page.click('.studio-root [data-action="open-editor"]');
        await page.waitForFunction(() => typeof lessonEditorSession !== 'undefined' && lessonEditorSession && lessonEditorSession.workspace.isOpen, null, { timeout: 40000 });
        await page.waitForSelector('.editor-root .editor-scene', { timeout: 15000 });
        await settle(page, 2000, 'the editor opened again from the Review stage');
        const u0 = await ui(page);
        await shot(page, 'l1-20-editor-quality-chips.png');
        await selectScene(page, ID.definition);
        const title0 = await page.inputValue('#editor-field-title');
        await page.locator('#editor-field-title').fill(`${title0} (checked)`);
        await page.keyboard.press('Escape');
        await settle(page, 1500);
        await ensurePaused(page, 'after the title edit');
        const u1 = await ui(page);
        await shot(page, 'l1-21-editor-one-scene-stale.png');
        const staleIds = u1.list.filter(x => x.marks.some(m => m.chip && m.stale === 'true')).map(x => x.id);
        const staleBefore = u0.list.filter(x => x.marks.some(m => m.chip && m.stale === 'true')).map(x => x.id);
        record.lesson1.invalidation = { before: u0.list.map(x => ({ k: kindOf(x.id), marks: x.marks.filter(m => m.chip) })), after: u1.list.map(x => ({ k: kindOf(x.id), marks: x.marks.filter(m => m.chip) })), top: [u0.quality, u1.quality] };
        await closeEditorToStudio(page);
        await stage(page, 'review');
        const su = await sui(page);
        await shot(page, 'l1-22-stage-review-stale.png');
        const st = await lessonState(pid1);
        check('26. targeted invalidation: one scene\'s title edited after the check → only that scene is marked "changed since the check" in the editor; the Studio says "Changed since the check" (quality.stale on the server)',
            staleBefore.length === 0 && same(staleIds, [ID.definition]) && st.quality && st.quality.stale === true && /Changed since the check/.test(su.qualityStale || '')
            && /Changed since the check/.test(stageOf(su, 'review').summary || ''),
            `stale chips before ${staleBefore.length}, after ${staleIds.map(kindOf).join(',') || 'none'}; editor top "${u0.quality}" → "${u1.quality}"; server quality ${JSON.stringify(st.quality)}; Studio "${su.qualityStale}" / stage "${stageOf(su, 'review').summary}"; l1-21 / l1-22 screenshots`);
    });
    await section('27-28. Visual Review from the Studio: a visual approved', async () => {
        phase = 'review';
        await stage(page, 'review');
        await page.click('.studio-root [data-action="open-review"]');
        await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
        await sleep(800);
        await shot(page, 'l1-23-visual-review.png');
        lookInReview('Visual Review', await reviewText(page));
        const idx = await page.evaluate(ids => ids.map(id => slides.findIndex(s => s.scene_id === id)), [ID.diagram, override ? scenes1[override.scene].scene_id : null, ID.last]);
        const items = await page.$$eval('.review-overlay.open .review-item', els => els.map(e => ({ scene: +e.dataset.scene, slot: e.dataset.slot, chip: (e.querySelector('.review-chip') || {}).textContent || '' })));
        check('27. "Visual Review" in the Studio\'s Review stage opens Visual Review over the lesson: its visuals listed, the earlier choices shown as changed',
            items.length >= 3 && /Changed/.test((items.find(x => x.scene === idx[1]) || {}).chip || '') && /Changed/.test((items.find(x => x.scene === idx[2]) || {}).chip || ''),
            `${items.length} items: ${items.map(x => `${x.scene + 1}/${x.slot} "${x.chip}"`).join(' | ')}; l1-23-visual-review.png`);
        const sel = `.review-overlay.open .review-item[data-scene="${idx[0]}"]`;
        await page.click(sel);
        await page.waitForFunction(s => document.querySelector(s).classList.contains('selected'), sel, { timeout: 10000 });
        lookInReview('Visual Review (the generated picture)', await reviewText(page));
        await shot(page, 'l1-24-visual-review-diagram.png');
        const kept = page.waitForResponse(r => r.url().includes('/api/visuals/review') && r.request().method() === 'POST', { timeout: 30000 });
        await page.click('.review-detail [data-action="keep"]');
        const kr = await kept;
        await page.waitForFunction(() => /Kept|Approved/.test((document.querySelector('.review-status') || {}).textContent || ''), null, { timeout: 30000 }).catch(() => {});
        const chip = await page.textContent(`${sel} .review-chip`).catch(() => '');
        const sideChips = await page.$$eval('.review-overlay.open .review-item[data-slot="side"], .review-overlay.open .review-item[data-slot="main"]',
            els => els.map(e => (e.querySelector('.review-chip') || {}).textContent || ''));
        await closeReview(page);
        const su = await sui(page);
        await shot(page, 'l1-25-stage-review-after.png');
        const sc = ((await project(pid1)).scenes || []).find(x => x.scene_id === ID.diagram) || {};
        record.lesson1.review = { items, chip, review: sc.visual_review && sc.visual_review.side, counts: su.text.match(/Scene visuals: [^·]+· [^·]+· [^·]+/) };
        check('28. "Keep" approves the diagram\'s generated picture (POST /api/visuals/review): the chip says Approved, the lesson keeps the approval, the Studio counts it',
            kr.status() === 200 && /Approved/.test(chip || '') && sc.visual_review && sc.visual_review.side && sc.visual_review.side.status === 'approved' && /[1-9]\d* approved/.test(su.text),
            `POST ${kr.status()}; chip "${chip}"; saved review ${JSON.stringify(sc.visual_review && sc.visual_review.side && { status: sc.visual_review.side.status })}; Studio "${(su.text.match(/Scene visuals:[^⚠✓○◌]*/) || [''])[0]}"`);
        // the Studio's Visual Review line agrees with Visual Review's own chips for the scene visuals
        const approvedChips = sideChips.filter(c => /Approved/.test(c)).length;
        const changedChips = sideChips.filter(c => /Changed/.test(c)).length;
        const pendingChips = sideChips.filter(c => /Needs review/.test(c)).length;
        const line = (su.text.match(/Scene visuals: (\d+) approved · (\d+) changed · (\d+) to check/) || []).slice(1).map(Number);
        check('28b. the Studio\'s "Scene visuals: N approved · N changed · N to check" agrees with Visual Review\'s chips for the scene visuals (the two library choices are "changed")',
            line.length === 3 && line[0] === approvedChips && line[1] === changedChips && line[2] === pendingChips,
            `Studio ${line.length ? `${line[0]} approved · ${line[1]} changed · ${line[2]} to check` : 'no line'}; Visual Review scene-visual chips: ${approvedChips} approved, ${changedChips} changed, ${pendingChips} needing review, ${sideChips.length - approvedChips - changedChips - pendingChips} other (${sideChips.join(' | ')})`);
    });

    // ==== LESSON 1: Preview (the Studio's Preview stage) =============================================================================
    let order1 = null;   // the played order of lesson 1 (scene ids, hidden ones left out)
    await section('29-30. the whole lesson previewed from the Studio; seeking between scenes', async () => {
        phase = 'preview';
        await stage(page, 'preview');
        await shot(page, 'l1-26-stage-preview.png');
        order1 = await page.evaluate(() => slides.filter(s => !(s.edit && s.edit.hidden)).map(s => s.scene_id));
        await page.evaluate(startRecorder);
        await page.click('.studio-root [data-action="preview"]');
        await page.waitForFunction(() => ttsState.isPlaying && !document.querySelector('.studio-root'), null, { timeout: 30000 }).catch(() => {});
        await sleep(2500);
        const first = await page.evaluate(() => (window.__rec.renders[0] || null));
        const startedAt = await page.evaluate(() => ({ current: currentSlide, playing: !!ttsState.isPlaying, intro: !!isIntroRunning }));
        // 30: seek with the player bar's scene dots: forward to the comparison, then back to the first scene
        await page.mouse.move(VIEW.width / 2, VIEW.height - 30);
        await sleep(600);
        const dots = await page.$$eval('#slide-scrubber-container .scrubber-dot', els => els.map(e => +e.dataset.scene));
        const seeks = [];
        for (const id of [ID.comparison, ID.title]) {
            const i = await page.evaluate(x => slides.findIndex(s => s.scene_id === x), id);
            await page.mouse.move(VIEW.width / 2, VIEW.height - 30);
            await sleep(400);
            await page.click(`#slide-scrubber-container .scrubber-dot[data-scene="${i}"]`, { force: true });
            const ok = await page.waitForFunction(x => currentSlide === x, i, { timeout: 8000 }).then(() => true).catch(() => false);
            await sleep(1200);
            const f = await page.evaluate(stageFacts);
            seeks.push({ to: kindOf(id), index: i, ok, current: f.current, title: f.cineTitle || f.title });
            if (id === ID.comparison) await shot(page, 'l1-27-preview-seek-comparison.png');
        }
        const fromAt = await page.evaluate(() => window.__rec.renders.length);
        // the lesson plays to its end
        const end = Date.now() + Math.min(420000, Math.max(60000, timeLeft() - 900000));
        let lastId = null;
        while (Date.now() < end) {
            await sleep(1000);
            const s = await page.evaluate(() => ({ id: slides[currentSlide] && slides[currentSlide].scene_id, playing: !!ttsState.isPlaying, done: window.slideSyncState && window.slideSyncState.audioFinished }));
            lastId = s.id;
            if (s.id === order1[order1.length - 1] && (s.done || !s.playing)) { await sleep(2500); break; }
        }
        await shot(page, 'l1-28-preview-end.png');
        const rec = await page.evaluate(stopRecorder);
        const played = rec.renders.slice(fromAt - 1 >= 0 ? fromAt - 1 : 0).map(r => r.id).filter((id, i, a) => id && a[i - 1] !== id);
        const fromTitle = played.slice(played.indexOf(ID.title));
        record.lesson1.preview = { first: first && kindOf(first.id), startedAt, dots: dots.length, seeks, played: fromTitle.map(kindOf), lastId: kindOf(lastId) };
        // the scene the preview started on: the first one drawn after the click, or (none drawn) the scene already on the stage
        const firstId = first ? first.id : await page.evaluate(i => slides[i] && slides[i].scene_id, startedAt.current);
        check('29. "Preview the lesson" in the Studio plays the whole lesson from its first scene: every scene in the edited order (code before the equation), the hidden introduction skipped',
            firstId === order1[0] && same(fromTitle, order1) && !played.includes(ID.intro),
            `the preview started on ${kindOf(firstId)} (scene ${startedAt.current + 1}${first ? '' : ', the scene left on the stage by the editor: no scene was drawn after the click'}, playing ${startedAt.playing}); after seeking back to the first scene it played: ${fromTitle.map(kindOf).join(' → ')}; expected ${order1.map(kindOf).join(' → ')}; l1-28-preview-end.png`);
        check('30. seeking between scenes with the player bar\'s scene dots (forward to the comparison, back to the first scene) shows that scene; the hidden scene has no dot',
            seeks.every(x => x.ok && x.current === x.index) && dots.length === order1.length && !dots.includes(await page.evaluate(x => slides.findIndex(s => s.scene_id === x), ID.intro)),
            `${dots.length} dots for ${order1.length} played scenes; ${seeks.map(x => `→ ${x.to} (scene ${x.index + 1}): ${x.ok ? 'shown' : 'NOT shown'} "${x.title}"`).join(' | ')}; l1-27-preview-seek-comparison.png`);
        // the Preview stage remembers this version was watched (the Studio from the player bar)
        if (await page.evaluate(() => !!ttsState.isPlaying)) await page.evaluate(() => document.getElementById('tts-play-btn').click());
        await page.mouse.move(VIEW.width / 2, VIEW.height - 30);
        await sleep(600);
        await page.click('#studio-btn');
        await waitView(page, 'lesson', 15000);
        await studioIdle(page);
        await sleep(800);
        const su = await sui(page);
        await shot(page, 'l1-29-studio-after-preview.png');
        check('the Studio reopened from the player bar (🧭) shows the lesson; its Preview stage says this version was previewed',
            su.view === 'lesson' && stageOf(su, 'preview').status === 'done' && /previewed this version/.test(stageOf(su, 'preview').summary || ''),
            `view ${su.view}; preview stage ${JSON.stringify(stageOf(su, 'preview'))}`);
    });

    // ==== Export (from the Studio, in the export browser) ============================================================================
    // The export browser records the tab with its sound, so it keeps the tab unmuted (--mute-audio left out: a muted tab records
    // silence) but has no audio output device and asks for suppressLocalAudioPlayback. It is the same user's browser: the page's
    // own presenter and scene-style settings (localStorage) are carried over from the main page.
    const EXPORT_ARGS = ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'];
    async function exportPage(settingsFrom) {
        if (!exportBrowser) {
            exportBrowser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'], args: EXPORT_ARGS });
            silent.flags.push('export: ' + EXPORT_ARGS.join(' ') + ' + suppressLocalAudioPlayback');
        }
        const settings = await settingsFrom.evaluate(() => Object.fromEntries(['aadhi.presenter', 'aadhi.cinematic', 'aadhi_ai_visuals']
            .map(k => [k, localStorage.getItem(k)]).filter(([, v]) => v !== null)));
        const ex = await newPage(exportBrowser);
        await ex.context.addInitScript(s => { if (!sessionStorage.getItem('copied')) { Object.entries(s).forEach(([k, v]) => localStorage.setItem(k, v)); sessionStorage.setItem('copied', '1'); } }, settings);
        return ex;
    }
    // "Play Video" on the start card; the intro skipped once it runs (as the arrow key does)
    async function startPlaying(p, projectId) {
        await p.goto(`${BASE}/?project_id=${projectId}`);
        await p.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await confirmSilent(p);
        await p.click('#start-lecture-btn');
        await p.waitForFunction(() => isIntroRunning || document.body.classList.contains('presentation-active'), null, { timeout: 15000 }).catch(() => {});
        await p.evaluate(() => skipIntroSequence());
        await p.waitForFunction(() => document.body.classList.contains('presentation-active')
            && getComputedStyle(document.getElementById('intro-sequence-container')).display === 'none', null, { timeout: 30000 });
        await p.waitForFunction(() => slides.length && slides.every(s => s.cinematic_plan), null, { timeout: 30000 }).catch(() => {});
        await sleep(1500);
    }
    // One scene played with its narration to the end, the next one not started (a preview frame of that scene)
    async function playScene(p, index) {
        await p.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; currentSlide = n; renderSlide(n); }, index);
        await sleep(1600);
        await p.waitForFunction(() => window.slideSyncState && window.slideSyncState.audioFinished, null, { timeout: 45000 }).catch(() => {});
        await sleep(900);
        return p.evaluate(stageFacts);
    }
    // The whole export of a lesson from the Studio's Export stage: preview frames first (the same browser), then the recording,
    // its file and outputs, the frames at the same scene moments, the export history and the Studio's Export stage
    async function exportLesson({ pid, tag, kinds, suffix, expectTitles, lessonLength }) {
        const out = { tag };
        const ex = await exportPage(page);
        const p = ex.page;
        await startPlaying(p, pid);
        const ids = await p.evaluate(() => slides.map(s => s.scene_id));
        const kindIndex = Object.fromEntries(Object.entries(kinds).filter(([, id]) => id).map(([k, id]) => [k, ids.indexOf(id)]).filter(([, i]) => i >= 0));
        out.previews = {};
        for (const [k, i] of Object.entries(kindIndex)) {
            const facts = await playScene(p, i);
            const file = path.join(OUT, `${tag}-preview-${k}.png`);
            await p.screenshot({ path: file });
            out.previews[k] = { file: path.basename(file), title: facts.cineTitle || facts.title, img: facts.img ? facts.img.replace(/token=[^&]+/, 'token=…').slice(-80) : null,
                clipped: await p.evaluate(clippedOnStage) };
        }
        const clipped = Object.entries(out.previews).filter(([, v]) => v.clipped.length);
        check(`(${tag}) nothing on the board is cut off in the previewed scenes (formulas, code and tables fit their boxes)`, clipped.length === 0,
            clipped.map(([k, v]) => `${k}: ${v.clipped.map(c => `${c.what} "${c.text}" ${c.width}px in a ${c.box}px box`).join('; ')} (${v.file})`).join(' | ') || 'all fit');
        await p.evaluate(() => { window.__holdScene = false; ttsState.isPlaying = false; try { speakNarration(null); } catch (e) { /* nothing playing */ } });
        await sleep(800);
        // the Studio from the player bar → Export stage → "Export video"
        await p.mouse.move(VIEW.width / 2, VIEW.height - 30);
        await sleep(600);
        await p.click('#studio-btn');
        await waitView(p, 'lesson', 20000);
        await studioIdle(p);
        await stage(p, 'export');
        out.stageBefore = stageOf(await sui(p), 'export');
        await shot(p, `${tag}-stage-export-before.png`);
        await p.click('.studio-root [data-action="export-video"]');
        await p.waitForSelector('.export-overlay.open', { timeout: 15000 });
        await sleep(800);
        lookForProviders(`export panel (${tag})`, await p.evaluate(() => (document.querySelector('.export-overlay.open') || {}).textContent || ''));
        await shot(p, `${tag}-export-panel.png`);
        await p.click('.export-overlay.open .export-start-btn');
        const limit = Math.min(600000, Math.max(120000, timeLeft() - 120000));
        out.prompts = [];
        for (;;) {
            const start = p.locator('.export-actions button', { hasText: 'Start Recording' });
            const anyway = p.locator('.export-actions button', { hasText: 'Record anyway' });
            await Promise.race([start.waitFor({ timeout: limit }), anyway.waitFor({ timeout: limit })]);
            if (await anyway.isVisible()) {
                out.prompts.push((await p.textContent('.export-message').catch(() => '') || '').replace(/\s+/g, ' ').slice(0, 300));
                await shot(p, `${tag}-export-prompt.png`);
                await anyway.click();
                continue;
            }
            await start.click();
            break;
        }
        const recordEnd = Date.now() + limit;
        let outcome = null;
        while (!outcome && Date.now() < recordEnd) {
            await sleep(1000);
            const s = await p.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden,
                error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
            if (s.ready) outcome = 'ready';
            else if (s.error) outcome = 'error: ' + s.error;
        }
        out.outcome = outcome;
        const job = await p.evaluate(() => exportFlow.job);
        out.job = job && job.id;
        out.sceneLog = await p.evaluate(() => (window.exportSceneLog || []).map(x => ({ title: x.title, t: x.t })));
        await sleep(2500); // the history refreshes after the upload
        lookForProviders(`export panel after the recording (${tag})`, await p.evaluate(() => (document.querySelector('.export-overlay.open') || {}).textContent || ''));
        out.history = await p.evaluate(() => ({ scope: (document.querySelector('.export-history-scope [aria-pressed="true"]') || {}).textContent || null,
            items: [...document.querySelectorAll('.export-history-item')].map(li => ({ status: li.getAttribute('data-status'), title: (li.querySelector('.export-history-title') || {}).textContent || '',
                match: li.querySelector('.export-history-match') ? { kind: li.querySelector('.export-history-match').getAttribute('data-match'), text: li.querySelector('.export-history-match').textContent } : null })) }));
        await p.locator('.export-history').scrollIntoViewIfNeeded().catch(() => {});
        await shot(p, `${tag}-export-history.png`);
        // the Studio's Export stage after the export
        await p.click('.export-overlay.open .export-close').catch(() => {});
        await sleep(800);
        if (!(await p.$('.studio-root'))) {
            await p.mouse.move(VIEW.width / 2, VIEW.height - 30);
            await sleep(600);
            await p.click('#studio-btn').catch(() => {});
        }
        await waitView(p, 'lesson', 20000).catch(() => {});
        await studioIdle(p);
        await stage(p, 'export').catch(() => {});
        const su = await sui(p);
        out.stageAfter = stageOf(su, 'export');
        out.studioExport = { latest: su.exportLatest, match: su.exportMatch, chip: su.chip, chipStage: su.chipStage };
        await shot(p, `${tag}-stage-export-after.png`);
        await ex.context.close();
        // the stored file and its outputs
        const exportDir = path.join(data, 'exports');
        const userDir = job && fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(dd => fs.existsSync(path.join(exportDir, dd, `${job.id}.webm`))) : null;
        const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
        out.file = stored ? path.relative(OUT, stored) : null;
        out.record = job ? (await api('GET', `/api/exports/${job.id}`)).data : null;
        let chapters = { status: null, text: '' };
        let vtt = { status: null, text: '' };
        if (job && outcome === 'ready') {
            for (let k = 0; k < 30 && chapters.status !== 200; k++) { chapters = await apiText(`/api/exports/${job.id}/outputs/chapters`); if (chapters.status !== 200) await sleep(1000); }
            for (let k = 0; k < 30 && vtt.status !== 200; k++) { vtt = await apiText(`/api/exports/${job.id}/outputs/vtt`); if (vtt.status !== 200) await sleep(1000); }
        }
        fs.writeFileSync(path.join(OUT, `export-chapters${suffix}.txt`), chapters.text || '');
        fs.writeFileSync(path.join(OUT, `export-subtitles${suffix}.vtt`), vtt.text || '');
        const chapterLines = (chapters.text || '').split(/\r?\n/).map(l => /^\s*([\d:.]+)\s+(.*)$/.exec(l)).filter(Boolean)
            .map(m => ({ t: m[1].split(':').reduce((a, v) => a * 60 + parseFloat(v), 0), title: m[2].trim() }));
        const lessonStart = out.sceneLog.length ? out.sceneLog[0].t : 0;
        // the chapters of the lesson's scenes (the recording opens with the intro, its own chapter before the first scene)
        out.chapters = { status: chapters.status, all: chapterLines.map(c => `${c.t}s ${c.title}`),
            titles: chapterLines.filter(c => c.t >= lessonStart - 1.5).map(c => c.title) };
        out.vtt = { status: vtt.status, cues: (vtt.text || '').split(/\r?\n\r?\n/).filter(b => /-->/.test(b)).length };
        out.probe = stored ? ffprobe(stored) : null;
        out.volume = stored ? meanVolume(stored) : null;
        // the export frames at the same scene moments (just before the next scene starts)
        out.frames = {};
        for (const [k] of Object.entries(kindIndex)) {
            const title = out.previews[k] && out.previews[k].title;
            const r = out.sceneLog.findIndex(x => norm(x.title) === norm(title) || norm(title).includes(norm(x.title)));
            const next = r >= 0 ? out.sceneLog[r + 1] : null;
            const at = next ? Math.max(0, next.t - 0.6) : (r >= 0 && out.probe ? Math.max(0, out.probe.duration - 1) : null);
            const file = path.join(OUT, `${tag}-export-${k}.png`);
            out.frames[k] = { file: path.basename(file), at, diff: null };
            if (!stored || at === null) continue;
            spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', at.toFixed(2), '-i', stored, '-frames:v', '1', file]);
            if (!fs.existsSync(file)) continue;
            const a = grid(path.join(OUT, out.previews[k].file));
            const b = grid(file);
            const lesson = 16 * 32 * 3; // the 32x18 grid without its two bottom rows (the player controls of the preview)
            out.frames[k].diff = +difference(a.slice(0, lesson), b.slice(0, lesson)).toFixed(1);
        }
        note(`${tag} frame differences (preview vs export, controls excluded): ${Object.entries(out.frames).map(([k, f]) => `${k} ${f.diff}`).join(', ')}`);
        const titles = out.sceneLog.map(x => x.title);
        const n = (expectTitles || []).length;
        check(`31-32 (${tag}). "Export video" in the Studio's Export stage opens the export panel; the recording runs to the end, is uploaded and is ready`,
            outcome === 'ready' && !!stored && out.record && out.record.status === 'COMPLETED',
            `${outcome}; ${out.file || 'no stored video'}; record ${out.record && JSON.stringify({ status: out.record.status, duration: out.record.duration_seconds, audio: out.record.has_audio, project: out.record.project_id })}; prompts ${out.prompts.map(x => `"${x.slice(0, 120)}"`).join(' | ') || 'none'}; ${tag}-export-panel.png`);
        const pr = out.probe || {};
        check(`33 (${tag}). the file: a video stream and an audible audio stream, as long as the lesson (≥ ${lessonLength ? lessonLength.toFixed(1) + ' s' : 'the last scene'}); outputs: subtitles and chapters in the played order`,
            pr.video && pr.audio && Number.isFinite(pr.duration) && out.volume !== null && out.volume > -70
            && (lessonLength ? pr.duration >= lessonLength : out.sceneLog.length && pr.duration > out.sceneLog[out.sceneLog.length - 1].t + 3)
            && same(titles.map(norm), expectTitles.map(norm)) && same(out.chapters.titles.map(norm), expectTitles.map(norm))
            && out.vtt.status === 200 && out.vtt.cues > n,
            `video ${pr.video && `${pr.video.codec_name} ${pr.video.width}x${pr.video.height}`}, audio ${pr.audio && pr.audio.codec_name} (mean ${out.volume} dB), ${Number.isFinite(pr.duration) ? pr.duration.toFixed(1) : pr.duration} s; `
            + `scene log ${titles.map(t => `"${t}"`).join(' → ')}; expected ${expectTitles.map(t => `"${t}"`).join(' → ')}; chapters ${out.chapters.status}: ${out.chapters.all.join(' | ')}; VTT ${out.vtt.status} ${out.vtt.cues} cues; export-chapters${suffix}.txt, export-subtitles${suffix}.vtt`);
        // the subtitles belong to the recording: none before the lesson's first scene (the intro), none left over from playback
        // before the export
        const cueStarts = (vtt.text || '').split(/\r?\n\r?\n/).filter(b => /-->/.test(b)).map(b => {
            const m = /(\d+):(\d\d):(\d\d(?:\.\d+)?)\s*-->/.exec(b) || /(\d\d):(\d\d(?:\.\d+)?)\s*-->/.exec(b);
            const start = m ? (m.length === 4 ? +m[1] * 3600 + +m[2] * 60 + parseFloat(m[3]) : +m[1] * 60 + parseFloat(m[2])) : null;
            return { start, text: b.split(/\r?\n/).filter(l => !/-->/.test(l) && !/^\d+$/.test(l.trim())).join(' ').trim() };
        });
        const firstScene = out.sceneLog.length ? out.sceneLog[0].t : null;
        const early = cueStarts.filter(c => firstScene !== null && c.start !== null && c.start < firstScene - 0.5);
        out.earlyCues = early;
        check(`33b (${tag}). the exported subtitles start with the lesson itself: no caption before its first scene (none left over from the playback before the export)`,
            firstScene !== null && early.length === 0,
            `first scene at ${firstScene === null ? '-' : firstScene.toFixed(1)} s; cues before it: ${early.map(c => `${c.start.toFixed(1)} s "${c.text.slice(0, 80)}"`).join(' | ') || 'none'}; first cue ${cueStarts[0] ? `${(cueStarts[0].start || 0).toFixed(1)} s "${cueStarts[0].text.slice(0, 60)}"` : '-'}; export-subtitles${suffix}.vtt`);
        const first = out.history.items[0] || {};
        check(`34 (${tag}). the export history shows the video under "This lesson" with "Matches the current lesson"; the Studio's Export stage says it matches`,
            /This lesson/.test(out.history.scope || '') && first.status === 'COMPLETED' && first.match && first.match.kind === 'current' && /Matches the current lesson/.test(first.match.text)
            && out.stageAfter.status === 'done' && out.studioExport.match && out.studioExport.match.matches === 'true',
            `scope "${out.history.scope}"; ${out.history.items.length} item(s): ${out.history.items.map(x => `${x.status} "${x.title}" ${x.match ? x.match.text : '(no match line)'}`).join(' | ')}; `
            + `Studio export stage ${JSON.stringify(out.stageBefore)} → ${JSON.stringify(out.stageAfter)}; ${JSON.stringify(out.studioExport)}; ${tag}-export-history.png, ${tag}-stage-export-after.png`);
        return out;
    }
    let export1 = null;
    await section('31-34. lesson 1 exported from the Studio', async () => {
        phase = 'export1';
        if (timeLeft() < 400000) throw new Error(`not enough time left for the export (${Math.round(timeLeft() / 1000)} s)`);
        const sv = await project(pid1);
        const played = (sv.scenes || []).filter(s => !(s.edit && s.edit.hidden));
        const kinds = { ...Object.fromEntries(KINDS.filter(k => k !== 'diagram').map(k => [k, ID[k]])), diagram: ID.diagram };
        export1 = await exportLesson({ pid: pid1, tag: 'l1', kinds, suffix: '', expectTitles: played.map(s => s.title), lessonLength: record.lesson1.timelineTotal || null });
        record.lesson1.export = export1;
    });

    // ==== LESSON 1: Reopen ===========================================================================================================
    // The Studio for the lesson open on the page: the start card has no Studio button, so the lesson is played and the player
    // bar's 🧭 Studio (which pauses it) is used, as a user would
    async function studioFromPlayerBar(p, projectId) {
        await startPlaying(p, projectId);
        await p.mouse.move(VIEW.width / 2, VIEW.height - 30);
        await sleep(600);
        await p.click('#studio-btn');
        await waitView(p, 'lesson', 20000);
        await studioIdle(p);
        await sleep(800);
    }
    await section('35-38. lesson 1 reopened', async () => {
        phase = 'reopen';
        const before = dialogs.length;
        await page.goto(`${BASE}/?project_id=${pid1}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.waitForFunction(n => slides.length === n, scenes1.length, { timeout: 20000 }).catch(() => {});
        await confirmSilent(page);
        const facts = await page.evaluate(() => ({ url: location.search, pid: currentProjectId, n: slides.length, title: document.querySelector('#start-overlay h1').textContent }));
        await shot(page, 'l1-30-reopened.png');
        check('35. the page reloaded with ?project_id opens lesson 1 (its start card, every scene, no question asked)',
            facts.url === `?project_id=${pid1}` && facts.pid === pid1 && facts.n === scenes1.length && dialogs.length === before,
            `${JSON.stringify(facts)}; dialogs ${dialogs.slice(before).map(d => d.message).join(' | ') || 'none'}`);
        const s = await state(page);
        const at = k => s.ids.indexOf(ID[k]);
        const items = {
            order: same(s.ids, edits.ids),
            hold: s.edits[at('code')] && s.edits[at('code')].min_seconds === edits.hold,
            hidden: s.edits[at('intro')] && s.edits[at('intro')].hidden === true,
            visual: s.sideAsset[at('last')] === sunflowerAsset,
            presenter: s.comps[at('presenter')] && s.comps[at('presenter')].overrides.presenter_position === 'right',
            camera: s.comps[at('code')] && s.comps[at('code')].overrides.camera === 'static',
            transition: s.comps[at('formula')] && s.comps[at('formula')].overrides.transition === edits.transition,
            title: /\(checked\)$/.test(s.titles[at('definition')] || ''),
            style: await page.evaluate(() => cinematicSettings.settings.style) === 'corporate_training'
        };
        check('36. the edits remain after the reload: the order, the hold, the hidden scene, the library visual, the presenter side, the camera, the transition, the edited title, the style',
            Object.values(items).every(Boolean), Object.entries(items).map(([k, v]) => `${k} ${v ? 'kept' : 'LOST'}`).join(', '));
        const ov = override ? s.sideReview[s.ids.indexOf(scenes1[override.scene].scene_id)] : null;
        const approvals = { diagram: s.sideReview[at('diagram')], override: ov, summary: s.sideReview[at('last')], diagramAsset: s.sideAsset[at('diagram')] };
        record.lesson1.reopened = { items, approvals };
        check('37. the approvals remain: the diagram\'s generated picture approved, the tractor (Visual Review) and the sunflower (editor) still the chosen visuals',
            !!(approvals.diagram && approvals.diagram.status === 'approved' && ov && ov.status === 'changed' && ov.asset === tractorAsset && approvals.summary && approvals.summary.asset === sunflowerAsset
            && approvals.diagramAsset && (!runAsset(batchRun) || approvals.diagramAsset === runAsset(batchRun))),
            JSON.stringify({ ...approvals, batchAsset: runAsset(batchRun) }));
        await studioFromPlayerBar(page, pid1);
        const st = await lessonState(pid1);
        await stage(page, 'review');
        const su = await sui(page);
        await shot(page, 'l1-31-reopened-studio-review.png');
        const cp = (st.checkpoints || {}).quality || {};
        const expectStale = cp.lesson_fingerprint !== st.fingerprint;
        const expectStage = st.media.generating ? 'generating' : (st.media.attention || st.media.failed) ? 'needs_attention'
            : st.exports.latest && st.exports.latest.matches_lesson ? 'completed' : st.quality && !st.quality.stale ? 'ready_to_export' : 'review';
        record.lesson1.reopenedStudio = { quality: st.quality, expectStale, stage: st.stage, expectStage, chip: su.chip, stages: su.stages };
        check('38. the Studio after the reload: the quality checkpoint is as it should be (changed since the check: the title edit and the approval came after it), and the lesson\'s stage follows from its data (the video matches it: ✓ Video ready)',
            st.quality && st.quality.stale === expectStale && expectStale === true && /Changed since the check/.test(su.qualityStale || '')
            && st.stage === expectStage && su.chipStage === expectStage && expectStage === 'completed',
            `quality ${JSON.stringify(st.quality)} (checkpoint fingerprint ${cp.lesson_fingerprint ? cp.lesson_fingerprint.slice(0, 8) : '-'} vs lesson ${st.fingerprint.slice(0, 8)}); Studio "${su.qualityStale}"; stage ${st.stage} (expected ${expectStage}), chip "${su.chip}"; `
            + `stages ${su.stages.map(x => `${x.key}:${x.status}`).join(' ')}; l1-31-reopened-studio-review.png`);
    });

    // ==== LESSON 2: paste → written → style → preview → export =====================================================================
    let pid2 = null;
    let scenes2 = [];
    let kinds2 = {};
    await section('lesson 2: written from pasted content', async () => {
        phase = 'l2-write';
        await page.click('.studio-root [data-action="home"]');
        await waitView(page, 'home');
        await page.click('.studio-root [data-action="create"]');
        await waitView(page, 'create');
        await page.click('.studio-root [data-action="from-text"]');
        await waitView(page, 'paste');
        const text = fs.readFileSync(path.join(FIXTURES, 'newtons_second_law.txt'), 'utf8');
        await page.fill('.studio-root [data-field="text"]', text);
        const auto = await page.evaluate(() => ({ subject: document.querySelector('.studio-root [data-field="subject_name"]').value, title: document.querySelector('.studio-root [data-field="session_title"]').value }));
        await page.fill('.studio-root [data-field="subject_name"]', 'Physics');
        await page.fill('.studio-root [data-field="unit_name"]', 'Forces');
        await page.fill('.studio-root [data-field="session_title"]', "Newton's second law");
        await shot(page, 'l2-01-paste.png');
        const posted = page.waitForResponse(r => /\/api\/studio\/lessons$/.test(r.url()) && r.request().method() === 'POST', { timeout: 30000 });
        await page.click('.studio-root [data-action="start-lesson"]');
        const res = await posted;
        let body = {};
        try { body = JSON.parse(res.request().postData() || '{}'); } catch (e) { /* unreadable */ }
        const answer = await res.json().catch(() => ({}));
        await waitView(page, 'progress', 15000).catch(() => {});
        await shot(page, 'l2-02-progress.png');
        await waitView(page, 'lesson', 90000);
        await studioIdle(page);
        await until(async () => (await sui(page)).stages.length === 7, 20000);
        const run = (await api('GET', `/api/studio/runs/${answer.run_id}`)).data || {};
        pid2 = await page.evaluate(() => currentProjectId);
        const pr = await project(pid2);
        scenes2 = pr.scenes || [];
        kinds2 = kindsIn(scenes2);
        await stage(page, 'content');
        const u = await sui(page);
        await shot(page, 'l2-03-lesson-stages.png');
        const fromText = scenes2.filter(s => s.source && s.source.origin === 'source').length;
        record.lesson2.write = { auto, answer, run, kinds: Object.fromEntries(Object.entries(kinds2).map(([k, i]) => [k, i >= 0 ? scenes2[i].title : null])), origin: u.origin };
        check('lesson 2: "Paste content" → the pasted text (the names follow its first line until changed) is written by the durable run (text, no source) and opens; the Content stage says "From your text: N scenes"',
            auto.title === "Newton's Second Law of Motion" && res.status() === 200 && typeof body.text === 'string' && body.text.includes('F = m \\times a') && !body.source
            && run.status === 'completed' && run.project_id === pid2 && pid2 !== pid1 && await page.evaluate(() => location.search) === `?project_id=${pid2}`
            && fromText >= scenes2.length - 3 && new RegExp(`From your text: ${fromText} scenes`).test(u.origin || '')
            && ['definition', 'process', 'formula', 'code', 'comparison', 'quiz', 'summary', 'presenter', 'diagram'].every(k => kinds2[k] >= 0),
            `names from the first line ${JSON.stringify(auto)}; POST ${res.status()} text ${(body.text || '').length} chars; run ${JSON.stringify(run)}; ${scenes2.length} scenes (${fromText} from the text); kinds ${JSON.stringify(record.lesson2.write.kinds)}; origin "${u.origin}"; l2-03-lesson-stages.png`);
    });
    await section('lesson 2: a style from the Studio', async () => {
        phase = 'l2-style';
        const from = requests.length;
        await stage(page, 'style');
        await page.waitForSelector('.studio-root #cinematic-settings .style-option', { timeout: 15000 });
        const posted = page.waitForResponse(r => /\/api\/cinematic\/style/.test(r.url()) && r.request().method() === 'POST', { timeout: 20000 });
        await page.click('.studio-root #cinematic-settings .style-option[data-style="cinematic_education"]');
        const r = await posted;
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await sleep(1500);
        const u = await sui(page);
        await shot(page, 'l2-04-stage-style.png');
        const pr = await project(pid2);
        const made = requests.slice(from).filter(x => isGeneration(x.method, x.url));
        check('lesson 2: the Cinematic Education style chosen in the Style stage is kept with the lesson (no generation)',
            r.status() === 200 && pr.cinematic_style && pr.cinematic_style.style === 'cinematic_education' && made.length === 0 && /Cinematic education/i.test(stageOf(u, 'style').summary || ''),
            `POST ${r.status()}; saved ${JSON.stringify(pr.cinematic_style)}; generation requests ${made.length}; stage ${JSON.stringify(stageOf(u, 'style'))}`);
    });
    await section('lesson 2: previewed from the Studio', async () => {
        phase = 'l2-preview';
        await stage(page, 'preview');
        await page.evaluate(startRecorder);
        await page.click('.studio-root [data-action="preview"]');
        const ids = scenes2.map(s => s.scene_id);
        const ok = await page.waitForFunction(id => (window.__rec.renders || []).some(r => r.id === id), ids[3], { timeout: 150000 }).then(() => true).catch(() => false);
        await shot(page, 'l2-05-preview.png');
        const rec = await page.evaluate(stopRecorder);
        const played = rec.renders.map(r => r.id).filter((id, i, a) => id && a[i - 1] !== id);
        const stg = await page.evaluate(stageFacts);
        if (await page.evaluate(() => !!ttsState.isPlaying)) await page.evaluate(() => document.getElementById('tts-play-btn').click());
        record.lesson2.preview = { played: played.map(id => ids.indexOf(id) + 1), style: stg.cineStyle };
        check('lesson 2: "Preview the lesson" plays it from the start in order (the first four scenes seen advancing on their own) in its style',
            ok && same(played.slice(0, 4), ids.slice(0, 4)) && stg.cineStyle === 'cinematic_education',
            `played scenes ${played.map(id => ids.indexOf(id) + 1).join(' → ')}; stage style ${stg.cineStyle}; l2-05-preview.png`);
    });
    let export2 = null;
    await section('lesson 2: exported from the Studio', async () => {
        phase = 'export2';
        if (timeLeft() < 300000) throw new Error(`not enough time left for the export (${Math.round(timeLeft() / 1000)} s)`);
        const kinds = Object.fromEntries(KINDS.map(k => [k, kinds2[k] >= 0 ? scenes2[kinds2[k]].scene_id : null]));
        export2 = await exportLesson({ pid: pid2, tag: 'l2', kinds, suffix: '-lesson2', expectTitles: scenes2.map(s => s.title), lessonLength: null });
        record.lesson2.export = export2;
        const diagram = export2.previews.diagram || {};
        const saved = ((await project(pid2)).scenes || [])[kinds2.diagram] || {};
        const plan = (saved.visual_plan || {}).side || {};
        const runs2 = ((await api('GET', `/api/ai-media/runs?project_id=${pid2}&limit=20`)).data || {}).runs || [];
        check('lesson 2: its diagram scene shows the matching library picture (the Visual Router chose the cart; nothing generated for this lesson)',
            String(diagram.img || '').includes(cartAsset) && runs2.length === 0,
            `stage picture ${diagram.img}; saved plan ${JSON.stringify({ source: plan.source, asset: plan.asset_id === cartAsset ? 'cart' : plan.asset_id })}; generations for lesson 2: ${runs2.length}; l2-preview-diagram.png / l2-export-diagram.png`);
    });

    // ==== both lessons on Home ========================================================================================================
    await section('Home lists both lessons with their stage chips', async () => {
        phase = 'home';
        await studioFromPlayerBar(page, pid2);
        await page.click('.studio-root [data-action="home"]');
        await waitView(page, 'home');
        await until(async () => (await sui(page)).lessons.length >= 2, 15000);
        const u = await sui(page);
        await shot(page, 'home-both-lessons.png');
        const list = (await api('GET', '/api/studio/lessons')).data || {};
        const byId = Object.fromEntries((list.lessons || []).map(l => [String(l.project_id), l]));
        record.home = { shown: u.lessons, server: list.lessons };
        const l1 = await lessonState(pid1);
        const l2 = await lessonState(pid2);
        check('a lesson only played again after its export (no edit) still matches its video: both lessons\' latest video "matches the current lesson"',
            l1.exports.latest && l1.exports.latest.matches_lesson === true && l2.exports.latest && l2.exports.latest.matches_lesson === true,
            `lesson 1 ${JSON.stringify(l1.exports.latest)} stage ${l1.stage}; lesson 2 ${JSON.stringify(l2.exports.latest)} stage ${l2.stage} (lesson 2 was played once in the main page after its export, nothing edited)`);
        check('Home ("Your lessons") lists both lessons (newest first) with a stage chip each that matches the lesson\'s stage on the server',
            u.lessons.length === 2 && u.lessons[0].id === String(pid2) && u.lessons[1].id === String(pid1)
            && u.lessons.every(l => byId[l.id] && l.stage === byId[l.id].stage && l.chip) && /Newton/.test(u.lessons[0].title || '') && /plants make food/i.test(u.lessons[1].title || ''),
            `${u.lessons.map(l => `${l.id} "${l.title}" [${l.chip}] (${l.meta})`).join(' | ')}; server ${(list.lessons || []).map(l => `${l.project_id}:${l.stage}`).join(', ')}; home-both-lessons.png`);
    });

    // ==== partial failure: the server restarted with the stand-in failing every AI picture =========================================
    // A third lesson (pasted) with two diagrams: one matches the library cart (ready without generating), the other cannot be
    // made. The Studio lists the one that failed with Retry · Choose existing · Continue without; the rest of the lesson is fine.
    await section('partial failure: one visual that cannot be made', async () => {
        phase = 'partial';
        if (timeLeft() < 150000) throw new Error(`not enough time left (${Math.round(timeLeft() / 1000)} s)`);
        phase = 'restart'; // (requests the open page makes while the server is down are refused: expected)
        killServer();
        await sleep(1500);
        startServer({ AI_FAKE_FAIL: 'fake:image:rejected,fake-alt:image:rejected' });
        await waitForServer();
        await page.goto(BASE + '/');
        phase = 'partial';
        await page.waitForSelector('#open-studio-btn', { state: 'visible', timeout: 30000 });
        await page.click('#open-studio-btn');
        await waitView(page, 'home');
        await page.click('.studio-root [data-action="create"]');
        await waitView(page, 'create');
        await page.click('.studio-root [data-action="from-text"]');
        await waitView(page, 'paste');
        await page.fill('.studio-root [data-field="text"]', ['# Carts and Volcanoes', '', '## Pushing a cart', '',
            'Diagram: a shopping cart on a flat floor with an arrow showing the push to the right.', '', 'A cart speeds up when it is pushed harder.', '',
            '## Inside a volcano', '', 'Diagram: a volcano cross section with a magma chamber and a vent.', '', 'Magma rises through the vent and erupts as lava.', '',
            '## A moving glacier', '', 'Diagram: a glacier flowing down a mountain valley.', '', 'The ice of a glacier creeps slowly downhill.', '',
            '## Summary', '', 'A push speeds a cart up, and rising magma makes a volcano erupt.'].join('\n'));
        await page.click('.studio-root [data-action="start-lesson"]');
        await waitView(page, 'lesson', 90000);
        await studioIdle(page);
        const pid3 = await page.evaluate(() => currentProjectId);
        const scenes3 = (await project(pid3)).scenes || [];
        const volcano = scenes3.findIndex(s => /inside a volcano/i.test(s.title || ''));
        const cart = scenes3.findIndex(s => /pushing a cart/i.test(s.title || ''));
        const glacier = scenes3.findIndex(s => /moving glacier/i.test(s.title || ''));
        phase = 'partial-batch';
        await stage(page, 'visuals');
        const before = await sui(page);
        const br = page.waitForResponse(r => isBatch(r.url()) && r.request().method() === 'POST', { timeout: 60000 });
        await page.click('.studio-root [data-action="prepare-media"]');
        const batch3 = await (await br).json().catch(() => null);
        const settled = await until(async () => { const st = await lessonState(pid3); return st.media.generating === 0 && (st.media.attention + st.media.failed) >= 2 ? st : null; }, 120000, 1000);
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await sleep(2000);
        await until(async () => (await sui(page)).attention.length >= 2, 15000);
        const u = await sui(page);
        await shot(page, 'partial-01-stage-visuals-attention.png');
        const st = await lessonState(pid3);
        const item = u.attention.find(x => Number(x.scene) === volcano) || { buttons: [] };
        const actions = item.buttons.map(b => b.action);
        const glacierItem = u.attention.find(x => Number(x.scene) === glacier) || { buttons: [] };
        record.partial = { pid3, batch: batch3, media: st.media, studio: { counts: u.counts, attention: u.attention, stage: stageOf(u, 'visuals'), chip: u.chip } };
        lookForProviders('Studio (a visual that failed)', u.ownText);
        check('partial failure: with the stand-in failing every AI picture, "Prepare visuals" queues only the two visuals the library cannot give; both fail; the Visuals stage says ⚠ Needs attention and lists those scenes, each with Retry · Choose existing · Continue without',
            batch3 && Array.isArray(batch3.runs) && batch3.runs.length === 2 && same(batch3.runs.map(r => r.scene_index).sort(), [volcano, glacier].sort()) && settled && stageOf(u, 'visuals').status === 'attention'
            && u.attention.length === 2 && [item, glacierItem].every(x => ['retry-item', 'choose-existing', 'dismiss-item'].every(a => x.buttons.some(b => b.action === a && !b.disabled)) && /Scene \d+ visual needs attention/.test(x.text || '')),
            `batch ${batch3 ? batch3.runs.length : '-'} run(s); media ${JSON.stringify({ ready: st.media.ready, needed: st.media.needed, attention: st.media.attention, failed: st.media.failed, items: st.media.items.map(i => `${i.scene_index}/${i.slot}:${i.status}`) })}; `
            + `stage ${JSON.stringify(stageOf(u, 'visuals'))}; items ${JSON.stringify(u.attention)}; lesson chip "${u.chip}"; partial-01-stage-visuals-attention.png`);
        const cartItem = st.media.items.find(i => i.scene_index === cart);
        check('partial failure: the visual the library gives (the cart: the batch found it already available) counts as ready in the Studio, not as one still to prepare',
            batch3 && batch3.already_available >= 1 && st.media.ready >= 1 && !cartItem,
            `batch already_available ${batch3 && batch3.already_available}; media ready ${st.media.ready}, needed ${st.media.needed}; the cart scene (${cart + 1}) listed as ${cartItem ? `"${cartItem.status}"` : 'nothing (ready)'}; Visuals stage "${stageOf(u, 'visuals').summary}"`);
        // Retry: the visual is tried again (it fails again here) — or the Studio explains why not
        phase = 'partial-retry';
        // a refused retry is judged by the check below (not counted again among the unexpected errors)
        record.expected409 = [{ phase: 'partial-retry', status: 409, re: /\/api\/ai-media\/runs\/[^/]+\/resolve/ }];
        const runsBefore = (((await api('GET', `/api/ai-media/runs?project_id=${pid3}&limit=50`)).data || {}).runs || []).length;
        const retried = page.waitForResponse(r => (/\/api\/ai-media\/runs\/[^/]+\/resolve/.test(r.url()) || isBatch(r.url()) || /\/generate-ai-/.test(r.url())) && r.request().method() === 'POST', { timeout: 15000 }).catch(() => null);
        const volcanoItem = (await sui(page)).attention.find(x => Number(x.scene) === volcano);
        await page.click(`.studio-root .studio-attention-item[data-item="${volcanoItem ? volcanoItem.item : 'media-0'}"] [data-action="retry-item"]`);
        const rr = await retried;
        await studioIdle(page);
        const again = await until(async () => { const r = (((await api('GET', `/api/ai-media/runs?project_id=${pid3}&limit=50`)).data || {}).runs || []); return r.length > runsBefore && r.every(x => !['queued', 'running', 'recovering'].includes(x.status)) ? r : null; }, 60000, 1000);
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await sleep(2000);
        const ur = await sui(page);
        await shot(page, 'partial-02-after-retry.png');
        record.partial.retry = { request: rr ? `${rr.request().method()} ${rr.url().replace(BASE, '')} ${rr.status()}` : null, error: ur.error, note: ur.status, attention: ur.attention.length,
            runs: again ? again.map(x => `${x.scene_index}:${x.status}`) : null };
        check('partial failure: "Retry" on the failed picture makes it again (a new generation for that scene, through the lesson batch); on this server it fails again and is listed again, without an error',
            rr && rr.status() === 200 && !ur.error && again && again.length > runsBefore && again.filter(x => x.scene_index === volcano).length === 2
            && ur.attention.some(x => Number(x.scene) === volcano),
            `${record.partial.retry.request || 'no request'}; generations ${runsBefore} → ${again ? again.length : '-'} (${record.partial.retry.runs ? record.partial.retry.runs.join(', ') : '-'}); Studio error ${ur.error ? `"${ur.error}"` : 'none'}; note "${ur.status}"; items ${ur.attention.length}; partial-02-after-retry.png`);
        // Continue without (the glacier): the visual is removed in Visual Review, the scene plays without it, nothing queued again
        phase = 'partial-dismiss';
        const gl = (await sui(page)).attention.find(x => Number(x.scene) === glacier);
        if (gl) {
            await page.click(`.studio-root .studio-attention-item[data-item="${gl.item}"] [data-action="dismiss-item"]`);
            await studioIdle(page);
            await sleep(1500);
        }
        const ud = await sui(page);
        await shot(page, 'partial-03-after-continue-without.png');
        const glScene = ((await project(pid3)).scenes || [])[glacier] || {};
        const glReview = (glScene.visual_review || {}).side || {};
        const stD = await lessonState(pid3);
        record.partial.dismiss = { review: glReview, attention: ud.attention.map(x => x.scene), error: ud.error, media: { attention: stD.media.attention, failed: stD.media.failed } };
        check('partial failure: "Continue without" on the other failed picture removes that visual in Visual Review (the scene plays without it) and takes it off the list, without an error',
            !!gl && !ud.error && glReview.status === 'removed' && !ud.attention.some(x => Number(x.scene) === glacier),
            `review ${JSON.stringify({ status: glReview.status })}; items left for scenes ${ud.attention.map(x => Number(x.scene) + 1).join(', ') || 'none'}; Studio error ${ud.error ? `"${ud.error}"` : 'none'}; partial-03-after-continue-without.png`);
        // Choose existing: Visual Review opens on that scene; a library picture chosen resolves it
        phase = 'partial-choose';
        const target = (await sui(page)).attention.find(x => Number(x.scene) === volcano);
        if (target) {
            await page.click(`.studio-root .studio-attention-item[data-item="${target.item}"] [data-action="choose-existing"]`);
            await page.waitForSelector('.review-overlay.open', { timeout: 20000 });
            await sleep(800);
            const selected = await page.evaluate(() => { const s = document.querySelector('.review-overlay.open .review-item.selected'); return s ? { scene: +s.dataset.scene, slot: s.dataset.slot } : null; });
            await shot(page, 'partial-04-choose-existing.png');
            await page.click('.review-detail [data-action="change"]');
            await page.click('.review-detail .review-change [data-action="pick"]');
            await page.waitForSelector(`.asset-overlay.open .asset-item[data-id="${tractorAsset}"]`, { timeout: 15000 });
            await page.click(`.asset-overlay.open .asset-item[data-id="${tractorAsset}"]`);
            await page.click('.asset-overlay.open .asset-pick');
            await page.waitForFunction(() => /Changed/.test((document.querySelector('.review-status') || {}).textContent || ''), null, { timeout: 30000 }).catch(() => {});
            await closeReview(page);
            await sleep(1000);
            const uc = await sui(page);
            await shot(page, 'partial-05-after-choose.png');
            const st2 = await lessonState(pid3);
            record.partial.choose = { selected, media: st2.media, stage: stageOf(uc, 'visuals'), attention: uc.attention };
            check('partial failure: "Choose existing" opens Visual Review on that scene; a library picture chosen there resolves it (the Visuals stage no longer needs attention)',
                selected && selected.scene === volcano && uc.attention.length === 0 && stageOf(uc, 'visuals').status !== 'attention' && st2.media.attention + st2.media.failed === 0,
                `Visual Review opened on ${selected ? `scene ${selected.scene + 1}/${selected.slot}` : 'nothing selected'} (the failed visual: scene ${volcano + 1}); Studio items left ${uc.attention.length}; media ${JSON.stringify({ ready: st2.media.ready, attention: st2.media.attention, failed: st2.media.failed })}; stage ${JSON.stringify(stageOf(uc, 'visuals'))}; partial-05-after-choose.png`);
        } else {
            check('partial failure: "Choose existing" opens Visual Review on that scene; a library picture chosen there resolves it', false, 'no item left to choose for');
        }
    });

    // ==== the whole check: nothing generated except by "Prepare visuals"; no provider names; no errors; silent ======================
    const generated = requests.filter(r => isGeneration(r.method, r.url));
    const allowed = generated.filter(r => r.method === 'POST' && ((isBatch(r.url) && ['batch', 'batch-again', 'partial-batch'].includes(r.phase)) || r.phase === 'partial-retry'));
    fs.writeFileSync(path.join(OUT, 'requests.json'), JSON.stringify(requests, null, 2));
    fs.writeFileSync(path.join(OUT, 'answers.json'), JSON.stringify(answers, null, 2));
    check('nothing is generated except by "Prepare visuals" (the lesson batch) and the Studio\'s "Retry": no other media generation request during the whole check',
        generated.length === allowed.length && allowed.length >= 2,
        `${allowed.length} lesson batch request(s); other generation requests: ${generated.filter(r => !allowed.includes(r)).slice(0, 6).map(r => `${r.phase} ${r.method} ${r.url}`).join(', ') || 'none'}; ${requests.length} requests in all`);
    studioTexts.forEach(t => lookForProviders(`Studio (${t.phase}, ${t.stage})`, t.text));
    record.providerShown = providerShown;
    record.reviewProviders = reviewProviders;
    if (reviewProviders.length) note(`Visual Review's provenance line names the provider (Phase 8 requirement, not checked here): ${reviewProviders[0]}`);
    check('no provider or model name in the Studio\'s own UI, the editor or the export panel (left out: the mounted presenter panel\'s collapsed "Advanced presenter settings", Phase 12\'s advanced provider list; Visual Review\'s "AI provider" line, a Phase 8 requirement)',
        providerShown.length === 0, providerShown.slice(0, 6).join(' | ') || 'none seen');
    check('no unexpected page errors, failed requests or dialogs', problems.length === 0 && dialogs.length === 0,
        [...problems.slice(0, 8), ...dialogs.slice(0, 3).map(d => `dialog (${d.phase}) ${d.type}: ${d.message}`)].join(' | '));
    check('every browser was silent (audio output disabled, speech stubbed on every page, capture without local playback)',
        silent.stubbed.size === silent.pages && silent.pages > 0 && silent.flags.every(f => f.includes('--disable-audio-output')) && silent.flags[0].includes('--mute-audio'),
        `${silent.stubbed.size}/${silent.pages} pages stubbed; ${silent.flags.join(' | ')}`);
} catch (e) {
    check('check ran to the end', false, e.stack || e.message);
} finally {
    if (browser) await browser.close().catch(() => {});
    if (exportBrowser) await exportBrowser.close().catch(() => {});
    killServer();
    try { fs.writeFileSync(path.join(OUT, 'studio-check.json'), JSON.stringify(record, null, 2)); } catch (e) { /* best effort */ }
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Artifacts: ${OUT}`);
process.exit(failed.length ? 1 : 0);
