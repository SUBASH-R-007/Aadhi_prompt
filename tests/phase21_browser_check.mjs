// Phase 21 (UI/UX refinement) check in real Chrome against a throwaway server with the local stand-ins only (AI_FAKE_PROVIDER=1:
// stand-in media and text models and the stand-in lesson writer; FAKE_TTS=1: a speech-like tone as long as the text), plus a
// second throwaway server without any lesson writer for the "no writer" note. No real provider is ever called.
//
// What a user sees and does, done the way a user does it (clicks, keys, file choosers); the page's state and the server are only
// READ for the checks:
//   Home        the empty state (one "Create a lesson"), lesson rows with plain stage chips (icon + words), Continue
//   Create      a document through the Studio → Document Assistant → name → written; pasted notes; a lesson file (.json);
//               errors: a server without a lesson writer shows the note and turns writing off; empty notes are refused; writing
//               is cancelled with the in-panel question
//   Studio      the stage rail (each stage opens directly, its state as icon + words), the "Next step" label and button, the
//               lesson continued after a reload (the start card's "Open in Studio") and from Home ("Continue")
//   Review/Edit the editor opened from the Studio and "Back to the Studio"; a change saved; Visual Review with the origin line and
//               no provider names; the quality headline and a finding with "Show scene"
//   Style       all four styles: the stage changes, the product UI's computed colours do not
//   Preview     play, seek, captions: a two-line caption at 1280×720 stays inside its band and clear of the presenter's name card
//               and the board; a one-line caption keeps bottom 60 px; at 1920×1080 nothing moves
//   Export      the progress steps, completion, "Your videos" with the new video, chapters without a duplicate "Introduction"
//   Navigation  the top bar (Home · Create · Library · Videos · Settings) reachable from the start screen and from a lesson on
//               the stage; Home from a playing lesson stops it and returns; the start card of ?project_id= (Open in Studio, Home)
//   Settings    five groups; the AI writer's service and model only under Advanced; debug-only tools hidden
//   Responsive  1440×900, 1280×720, 1024×768, 820×1180, 600×900: no horizontal scroll on any screen; the editor's transport
//               row, timeline tracks and inspector never overlap at 1024 and 820; the player bar's essential controls on screen
//   A11y        a keyboard-only path Home → Create → paste → write → stage → editor → back; focus into each overlay and back
//               to its opener; Tab kept inside dialogs; every icon-only button named; reduced motion: no product-UI animation
//               running; status regions present
//   Library     Aadhi's shared assets first, narration hidden by default, no asset ids outside the debug view
// Plus: no page errors, console errors or failed requests; no provider name in the product UI outside the debug view; silent.
//
// Artifacts (PHASE21_CHECK_OUT): a screenshot of every screen (at every viewport for the responsive part), phase21-check.json.
// Needs Chrome, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/phase21_browser_check.mjs
// Silent: --mute-audio and --disable-audio-output, browser speech stubbed; the export's tab capture keeps the tab unmuted
// (a muted tab records silence) but asks for suppressLocalAudioPlayback, with no audio output device.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const FIXTURES = path.join(REPO, 'tests', 'fixtures', 'studio');
const OUT = process.env.PHASE21_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-phase21-check');
const PASSWORD = process.env.AADHI_PASSWORD;
let PORT = Number(process.env.PHASE21_CHECK_PORT || 9935 + (process.pid % 8));
if (PORT === 9942) PORT = 9934; // never the development server's port
const PORT_B = PORT - 20; // the second server (no lesson writer)
const BASE = `http://127.0.0.1:${PORT}`;
const BASE_B = `http://127.0.0.1:${PORT_B}`;
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
fs.mkdirSync(OUT, { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const PYTHON = path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python');
const servers = [];
// One throwaway server: its own database, files and jobs under OUT/<name>-data
function startServer(name, port, extra) {
    const data = path.join(OUT, `${name}-data`);
    for (const dir of ['assets', 'exports', 'static', 'jobs']) fs.mkdirSync(path.join(data, dir), { recursive: true });
    const log = fs.openSync(path.join(OUT, `${name}.log`), 'w');
    const proc = spawn(PYTHON, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(port)], {
        cwd: REPO, stdio: ['ignore', log, log],
        env: { ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `p21-${name}-${process.pid}`), JWT_SECRET: `phase21-${name}-` + Math.random().toString(36).slice(2),
            AI_FAKE_STATE_DIR: path.join(data, 'jobs'), FAKE_TTS: '1', AI_MEDIA_LOG: '1', GEMINI_API_KEY: '', OPENAI_API_KEY: '', AI_FAKE_FAIL: '', ...extra }
    });
    servers.push(proc);
    return { proc, data };
}
function killServer(proc) {
    if (!proc || proc.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(proc.pid), '/T', '/F']);
    else proc.kill('SIGKILL');
}
process.on('exit', () => servers.forEach(killServer));
process.on('unhandledRejection', e => console.log('      (a pending wait ended after its step: ' + String((e && e.message) || e).split(/\r?\n/)[0] + ')'));
async function waitForServer(base, proc) {
    for (let i = 0; i < 160; i++) {
        if (proc.exitCode !== null) throw new Error('a test server stopped; see ' + OUT);
        try { if ((await fetch(base + '/studio.js')).ok) return; } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('a test server did not start');
}
async function login(base) {
    return (await (await fetch(base + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
}
let token = null;
async function api(method, route, body, base = BASE, tok = token) {
    const res = await fetch(base + route, { method, headers: { Authorization: 'Bearer ' + tok, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined });
    return { status: res.status, data: await res.json().catch(() => null) };
}
async function apiText(route) {
    const res = await fetch(BASE + route, { headers: { Authorization: 'Bearer ' + token } });
    return { status: res.status, text: await res.text().catch(() => '') };
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
// renderSlide() assigns window.checkAndAdvanceSlide for every scene: while window.__holdScene is set the page sees a no-op, so a
// scene being measured never advances (the export and the preview never set it)
function holdProbe() {
    let real = () => {};
    Object.defineProperty(window, 'checkAndAdvanceSlide', { configurable: true,
        get() { return window.__holdScene ? () => {} : real; }, set(v) { real = v; } });
}
const norm = v => String(v === undefined || v === null ? '' : v).replace(/\s+/g, ' ').trim();
// Words that name an AI provider or model (none may reach the product UI outside the debug view); the stand-ins' own names too
const PROVIDER_WORDS = /\b(gemini|openai|chatgpt|gpt-?\d|anthropic|claude|pollinations|replicate|runway(ml)?|kling|luma|pika|eleven ?labs|stability|dall-?e|imagen|veo|sora|midjourney|ltx|fake(-alt|-presenter)?|stand-in)\b/i;
const HEX_ID = /\b[0-9a-f]{32}\b/;
const STATE_ICON = /^[✓●→⚠○✎]\s+\S+/; // a status: an icon, then words
const VIEWPORTS = [{ key: '1440', width: 1440, height: 900 }, { key: '1280', width: 1280, height: 720 }, { key: '1024', width: 1024, height: 768 },
    { key: '820', width: 820, height: 1180 }, { key: '600', width: 600, height: 900 }];
const VIEW = { width: 1280, height: 720 };

// ---- page-side probes (serialized into the page; they only READ) -------------------------------------------------------------
function shellFacts() {
    const vis = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
    const root = document.querySelector('.studio-root');
    return {
        start: !document.getElementById('upload-screen').classList.contains('hidden'),
        card: getComputedStyle(document.getElementById('start-overlay')).display !== 'none',
        active: document.body.classList.contains('presentation-active'),
        board: !document.getElementById('presentation-board').classList.contains('hidden'),
        playing: typeof ttsState !== 'undefined' && !!ttsState.isPlaying,
        studio: root ? root.getAttribute('data-view') : null,
        settings: !document.getElementById('settings-overlay').hidden,
        library: !!document.querySelector('.asset-overlay.open'), videos: !!document.querySelector('.export-overlay.open'),
        editor: !!document.querySelector('.editor-root'), review: !!document.querySelector('.review-overlay.open'),
        barInert: document.getElementById('app-bar').hasAttribute('inert'), barShown: vis(document.getElementById('nav-home')),
        project: typeof currentProjectId !== 'undefined' ? currentProjectId : null, url: location.search,
        current: typeof currentSlide !== 'undefined' ? currentSlide : null
    };
}
function studioUI() {
    const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
    const root = document.querySelector('.studio-root');
    const q = sel => (root ? root.querySelector(sel) : null);
    const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
    return {
        open: !!root, view: root ? root.getAttribute('data-view') : null, heading: t(q('#studio-heading')), busy: root ? root.getAttribute('aria-busy') : null,
        error: t(q('.studio-error[role="alert"]')), writerNote: t(q('.studio-writer-note')), status: t(q('.studio-status')),
        stages: root ? [...root.querySelectorAll('button.studio-stage')].map(b => ({ key: b.dataset.stage, status: b.dataset.status, state: b.dataset.state,
            text: t(b.querySelector('.studio-stage-status')), name: t(b.querySelector('.studio-stage-name')), next: !!b.querySelector('.studio-stage-next'),
            nextText: t(b.querySelector('.studio-stage-next')), current: b.classList.contains('is-current'), aria: b.getAttribute('aria-current'), label: b.getAttribute('aria-label') })) : [],
        detail: { stage: q('.studio-detail') ? q('.studio-detail').getAttribute('data-stage') : null, title: t(q('.studio-detail-title')), status: t(q('.studio-detail-status')),
            next: t(q('.studio-detail-next')), nextBtn: t(q('[data-action="next-stage"]')) },
        lessons: root ? [...root.querySelectorAll('.studio-lesson-item[data-project-id]')].map(li => ({ id: li.dataset.projectId, title: t(li.querySelector('.studio-lesson-title')),
            meta: t(li.querySelector('.studio-lesson-meta')), chip: t(li.querySelector('.studio-chip')), stage: li.querySelector('.studio-chip') ? li.querySelector('.studio-chip').dataset.lessonStage : null,
            button: t(li.querySelector('button')), buttonLabel: li.querySelector('button') ? li.querySelector('button').getAttribute('aria-label') : null })) : [],
        createButtons: root ? [...root.querySelectorAll('[data-action="create"]')].filter(vis).length : 0,
        choices: root ? [...root.querySelectorAll('.studio-choice')].map(b => b.dataset.action) : [],
        steps: root ? [...root.querySelectorAll('.studio-step')].map(s => ({ step: s.dataset.step, status: s.dataset.status, text: t(s.querySelector('.studio-step-state')) })) : [],
        chip: t(q('.studio-lesson-title-row .studio-chip')),
        startOff: q('[data-action="start-lesson"]') ? { disabled: q('[data-action="start-lesson"]').disabled, off: q('[data-action="start-lesson"]').getAttribute('data-off') } : null,
        text: root ? t(root).slice(0, 3000) : '',
        ownText: root ? (() => { const c = root.cloneNode(true); c.querySelectorAll('details.presenter-advanced:not([open])').forEach(d => d.remove()); return t(c).slice(0, 6000); })() : ''
    };
}
function focusInfo() {
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return { el: 'body', none: true };
    const cs = getComputedStyle(el);
    return { el: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.dataset && el.dataset.action ? `[${el.dataset.action}]` : '') + (el.dataset && el.dataset.stage ? `[stage=${el.dataset.stage}]` : ''),
        id: el.id || null, action: (el.dataset && el.dataset.action) || null, stage: (el.dataset && el.dataset.stage) || null, tag: el.tagName.toLowerCase(),
        text: (el.getAttribute('aria-label') || el.textContent || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 40),
        ring: (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (!!cs.boxShadow && cs.boxShadow !== 'none'),
        inStudio: !!el.closest('.studio-root'), inEditor: !!el.closest('.editor-root'), inSettings: !!el.closest('#settings-dialog'),
        inLibrary: !!el.closest('.asset-overlay.open'), inVideos: !!el.closest('.export-overlay.open'), inReview: !!el.closest('.review-overlay.open'),
        inMenu: !!el.closest('#player-more-menu'), inDoc: !!el.closest('.doc-overlay.open') };
}
function layoutFacts() {
    const de = document.documentElement;
    const vw = innerWidth;
    const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
    const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
    const offenders = [];
    if (de.scrollWidth > vw + 1 || document.body.scrollWidth > vw + 1) {
        document.querySelectorAll('body *').forEach(el => {
            if (offenders.length >= 6 || !vis(el)) return;
            const r = el.getBoundingClientRect();
            if (r.right > vw + 1 && !offenders.some(o => o.node.contains(el))) offenders.push({ node: el, el: name(el), right: Math.round(r.right) });
        });
    }
    return { vw, scrollW: Math.max(de.scrollWidth, document.body.scrollWidth), hScroll: de.scrollWidth > vw + 1 || document.body.scrollWidth > vw + 1,
        offenders: offenders.map(o => `${o.el}→${o.right}`) };
}
// visible buttons with no words and no accessible name (an icon-only control a screen reader cannot name)
function unnamedIconButtons() {
    const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
    const out = [];
    document.querySelectorAll('button, [role="button"], [role="menuitem"], a[href]').forEach(el => {
        if (!vis(el) || el.closest('[inert]')) return;
        const text = (el.textContent || '').replace(/\s+/g, '');
        const words = /[\p{L}\p{N}]{2,}/u.test(text);
        const label = (el.getAttribute('aria-label') || '').trim() || (el.getAttribute('aria-labelledby') ? (document.getElementById(el.getAttribute('aria-labelledby').split(/\s+/)[0]) || {}).textContent : '');
        if (!words && !(label && label.trim())) out.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}.${String(el.className || '').split(/\s+/)[0]} "${text.slice(0, 6)}"`);
    });
    return out;
}
function rectsOf(selectors) {
    const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 1 && r.height > 1 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
    const out = {};
    for (const [key, sel] of Object.entries(selectors)) {
        out[key] = [...document.querySelectorAll(sel)].filter(vis).map(el => { const r = el.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; });
    }
    out.vw = innerWidth;
    out.vh = innerHeight;
    return out;
}
// The product UI's computed colours (never the lesson style's) and the stage's look
function uiColours() {
    const pick = (sel, props) => { const el = document.querySelector(sel); if (!el) return null; const cs = getComputedStyle(el); return props.map(p => cs[p]).join(' | '); };
    return {
        bar: pick('.app-bar', ['backgroundColor', 'color', 'borderBottomColor']), nav: pick('#nav-home', ['color', 'backgroundColor']),
        player: pick('#voice-control-bar', ['backgroundColor', 'color']), play: pick('#tts-play-btn', ['color', 'backgroundColor', 'borderTopColor']),
        studioPanel: pick('.studio-root .studio-panel', ['backgroundColor', 'color']), studioPrimary: pick('.studio-root .studio-btn-primary', ['backgroundColor', 'color']),
        studioStage: pick('.studio-root button.studio-stage.is-current', ['backgroundColor', 'color', 'borderTopColor']),
        studioTitle: pick('.studio-root .studio-detail-title', ['color']), studioBtn: pick('.studio-root .studio-btn:not(.studio-btn-primary)', ['backgroundColor', 'color']),
        styleOption: pick('.studio-root .style-option[data-selected="true"]', ['borderTopColor']),
        settings: pick('#settings-dialog', ['backgroundColor', 'color'])
    };
}
function stageLook() {
    const root = getComputedStyle(document.documentElement);
    const v = name => root.getPropertyValue(name).trim();
    const title = document.querySelector('.cine-title-text');
    return { style: document.body.getAttribute('data-cine-style'), tone: document.body.getAttribute('data-cine-tone'), cinematic: document.body.hasAttribute('data-cinematic'),
        tokens: ['--st-bg-1', '--st-bg-2', '--st-title-bar', '--st-text', '--st-accent'].map(v).join(' | '),
        titleColour: title ? getComputedStyle(title).color : null };
}
// The caption, its band and what it must stay clear of
function captionFacts() {
    const track = document.getElementById('subtitle-track');
    const vis = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.05; };
    const box = el => { const r = el.getBoundingClientRect(); return { l: Math.round(r.left), t: +r.top.toFixed(1), r: Math.round(r.right), b: +r.bottom.toFixed(1), h: +r.height.toFixed(1) }; };
    const range = document.createRange();
    range.selectNodeContents(track);
    const tops = [...new Set([...range.getClientRects()].filter(r => r.width > 1).map(r => Math.round(r.top / 4)))];
    const cs = getComputedStyle(track);
    const name = document.querySelector('#presenter-layer .cine-presenter-name');
    const board = document.getElementById('presentation-board');
    const vh = document.documentElement.clientHeight;
    return { vw: innerWidth, vh, text: track.textContent.replace(/\s+/g, ' ').trim(), active: track.classList.contains('active'), shown: vis(track),
        box: box(track), lines: tops.length, bottom: cs.bottom, fontSize: cs.fontSize,
        drop: track.style.getPropertyValue('--cine-caption-drop'), fit: track.style.getPropertyValue('--cine-caption-fit'),
        bandTop: +(vh * (1 - AadhiCinematic.SUBTITLES.h)).toFixed(1), cinematic: document.body.hasAttribute('data-cinematic'), style: document.body.getAttribute('data-cine-style'),
        caption: document.body.getAttribute('data-cine-caption'),
        nameCard: vis(name) ? { ...box(name), text: name.textContent } : null, board: vis(board) ? box(board) : null,
        boardBody: vis(board && board.querySelector('.board-body')) ? box(board.querySelector('.board-body')) : null };
}
// Product-UI animations running now (the recorded stage left out: it has its own reduced-motion rules)
function runningUiAnimations(stage = false) {
    const STAGE = '#presentation-board, .cinematic-scene-container, .cinematic-zoom-active, .dynamic-side-zone, #mascot-bg, #static-bg, #subtitle-track, #intro-sequence-container, #presenter-layer, #cine-labels, .cine-bg, [class^="cine-"], #quiz-cp-wrap';
    if (stage) return document.getAnimations().filter(a => a.playState === 'running' && a.effect && a.effect.target && a.effect.target.closest(STAGE))
        .map(a => `${a.animationName || a.transitionProperty || a.constructor.name}@${a.effect.target.id || String(a.effect.target.className || a.effect.target.tagName).split(/\s+/)[0]}`);
    return document.getAnimations().filter(a => a.playState === 'running').map(a => {
        const t = a.effect && a.effect.target;
        return { t, name: a.animationName || a.transitionProperty || a.constructor.name, iterations: a.effect && a.effect.getTiming ? a.effect.getTiming().iterations : null };
    }).filter(x => x.t && !x.t.closest(STAGE)).map(x => `${x.name}@${x.t.id || String(x.t.className || x.t.tagName).split(/\s+/)[0]}${x.iterations === Infinity ? ' (infinite)' : ''}`);
}

const problems = [];
const expectedFailures = []; // { phase, re }: a failure a step causes on purpose (a stand-in answer), never counted as a problem
const isExpected = (ph, text) => expectedFailures.some(x => x.phase === ph && x.re.test(text));
const silent = { pages: 0, stubbed: 0, flags: [] };
const dialogs = [];
const texts = [];            // the product UI's own words in the normal view, scanned for provider names at the end
const record = { screens: [] };
let phase = 'setup';
let browser = null;
let exportBrowser = null;
let serverA = null;
let serverB = null;

try {
    serverA = startServer('server', PORT, { AI_FAKE_PROVIDER: '1', AI_GENERATION_ENABLED: '1', FAKE_LLM_SECONDS: '2' });
    await waitForServer(BASE, serverA.proc);
    token = await login(BASE);
    // a picture of the user's own (the lesson file's diagram scene matches it: Visual Review then says where it came from)
    const picture = path.join(OUT, 'lever.png');
    const ff = spawnSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0xf4f1e8:s=640x360,drawbox=x=80:y=230:w=480:h=14:color=0x6b4f2a@1:t=fill,'
        + 'drawbox=x=300:y=244:w=40:h=60:color=0x333333@1:t=fill,drawbox=x=110:y=170:w=80:h=60:color=0xc0392b@1:t=fill', '-frames:v', '1', picture]);
    if (ff.status !== 0) throw new Error('ffmpeg could not make the library picture: ' + String(ff.stderr));
    {
        const form = new FormData();
        form.append('file', new Blob([fs.readFileSync(picture)], { type: 'image/png' }), 'lever-on-a-fulcrum.png');
        const up = await fetch(BASE + '/api/assets', { method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: form });
        const asset = (await up.json()).asset;
        await api('PATCH', `/api/assets/${asset.id}`, { description: 'A lever balanced on a fulcrum lifting a heavy box', keywords: ['lever', 'fulcrum', 'load', 'box', 'effort'] });
        record.libraryPicture = asset.id;
    }
    const { chromium } = await loadPlaywright();
    const MAIN_ARGS = ['--mute-audio', '--disable-audio-output', '--autoplay-policy=no-user-gesture-required'];
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: MAIN_ARGS });
    silent.flags.push('main: ' + MAIN_ARGS.join(' '));

    async function newPage(b, { viewport = VIEW, reducedMotion = 'no-preference', tok = token, base = BASE } = {}) {
        const context = await b.newContext({ viewport, reducedMotion });
        await context.addInitScript(t => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('jwt_token', t); sessionStorage.setItem('seeded', '1'); } }, tok);
        await context.addInitScript(silentPage);
        await context.addInitScript(holdProbe);
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error (${phase}): ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text()) && !isExpected(phase, m.text())) problems.push(`console (${phase}): ${m.text().slice(0, 300)}`); });
        page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico') && !isExpected(phase, r.url())) problems.push(`HTTP ${r.status()} (${phase}): ${r.request().method()} ${r.url().replace(base, '')}`); });
        page.on('requestfailed', r => {
            const err = (r.failure() || {}).errorText || '';
            if (/ERR_ABORTED/.test(err)) return;
            problems.push(`request failed (${err}, ${phase}): ${r.url()}`);
        });
        page.on('dialog', d => { dialogs.push({ phase, type: d.type(), message: d.message().slice(0, 200) }); d.dismiss().catch(() => {}); });
        silent.pages += 1;
        return { context, page };
    }
    async function confirmSilent(p) {
        if (await p.evaluate(() => !window.speechSynthesis || /onstart/.test(String(window.speechSynthesis.speak)))) silent.stubbed += 1;
    }
    const shot = (p, name) => { record.screens.push(name); return p.screenshot({ path: path.join(OUT, name) }).catch(() => {}); };
    const sui = p => p.evaluate(studioUI);
    const facts = p => p.evaluate(shellFacts);
    const focus = p => p.evaluate(focusInfo);
    const waitView = (p, view, ms = 15000) => p.waitForSelector(`.studio-root[data-view="${view}"]`, { timeout: ms });
    const studioIdle = p => p.waitForFunction(() => { const r = document.querySelector('.studio-root'); return r && r.getAttribute('aria-busy') !== 'true'
        && !/Loading/.test((r.querySelector('#studio-heading') || {}).textContent || ''); }, null, { timeout: 30000 }).catch(() => {});
    async function stage(p, key) {
        // (narrow screens show the stages as one select, "Stage 3 of 7")
        if (await p.isVisible(`.studio-root button.studio-stage[data-stage="${key}"]`)) await p.click(`.studio-root button.studio-stage[data-stage="${key}"]`);
        else await p.selectOption('.studio-root .studio-stage-select', key);
        await p.waitForFunction(k => { const d = document.querySelector('.studio-root .studio-detail'); return d && d.getAttribute('data-stage') === k; }, key, { timeout: 10000 });
        await studioIdle(p);
        await sleep(250);
    }
    // the Studio's lesson view once its stages are drawn
    const lessonView = (p, ms = 30000) => p.waitForFunction(() => { const r = document.querySelector('.studio-root[data-view="lesson"]'); return r && r.querySelectorAll('button.studio-stage').length === 7; }, null, { timeout: ms });
    // a lesson on the stage, paused on its first scene after the intro was skipped (as the arrow key does)
    async function startPlaying(p, pid, { pause = true } = {}) {
        await p.goto(`${BASE}/?project_id=${pid}`);
        await p.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await confirmSilent(p);
        await p.click('#start-lecture-btn');
        await p.waitForFunction(() => isIntroRunning || document.body.classList.contains('presentation-active'), null, { timeout: 15000 }).catch(() => {});
        await p.evaluate(() => skipIntroSequence());
        await p.waitForFunction(() => document.body.classList.contains('presentation-active') && !document.getElementById('presentation-board').classList.contains('hidden')
            && getComputedStyle(document.getElementById('intro-sequence-container')).display === 'none', null, { timeout: 30000 });
        await p.waitForFunction(() => slides.length && slides.every(s => s.cinematic_plan), null, { timeout: 30000 }).catch(() => {});
        await sleep(1200);
        if (pause) await p.evaluate(() => { window.__holdScene = true; if (ttsState.isPlaying) document.getElementById('tts-play-btn').click(); });
        await sleep(400);
    }
    // the player bar shown (it is shown on hover while a lesson is on the stage)
    async function showPlayerBar(p) {
        const vp = p.viewportSize();
        await p.mouse.move(vp.width / 2, vp.height - 30);
        await sleep(500);
    }
    // Tab (or Shift+Tab) until the focus is on what want(focusInfo) accepts; the presses it took, or -1
    async function tabTo(p, want, { max = 40, shift = false } = {}) {
        for (let i = 1; i <= max; i++) {
            await p.keyboard.press(shift ? 'Shift+Tab' : 'Tab');
            if (want(await focus(p))) return i;
        }
        return -1;
    }
    // Tab n times: every focus stop inside(focusInfo)?
    async function tabStaysIn(p, inside, n = 30) {
        const outside = [];
        let prev = (await focus(p)).el;
        for (let i = 0; i < n; i++) {
            const key = i % 7 === 6 ? 'Shift+Tab' : 'Tab';
            await p.keyboard.press(key);
            const f = await focus(p);
            if (!inside(f)) outside.push(`${key} from ${prev} → ${f.el}`);
            prev = f.el;
        }
        return outside;
    }
    const studioTexts = (where, u) => texts.push({ where, text: u.ownText || u.text || '' });
    async function writeDone(p, ms = 150000) {
        await p.waitForSelector('.studio-root[data-view="lesson"]', { timeout: ms });
        await lessonView(p);
        await studioIdle(p);
    }

    const { context: mainContext, page } = await newPage(browser);
    await page.goto(BASE + '/');
    await page.waitForSelector('#start-create-btn', { state: 'visible', timeout: 30000 });
    await confirmSilent(page);
    await sleep(600);

    // ==== Home (empty), the top bar, the Studio's focus, Create ====================================================================
    await section('home: empty state', async () => {
        phase = 'home-empty';
        await shot(page, 'home-01-start-screen.png');
        const nav = await page.evaluate(() => [...document.querySelectorAll('#app-bar .app-nav-btn')].map(b => ({ id: b.id, label: b.getAttribute('aria-label'), title: b.title,
            current: b.getAttribute('aria-current') })));
        check('navigation: the top bar has Home · Create · Library · Videos · Settings, named, Home marked as the current place on the start screen',
            JSON.stringify(nav.map(n => n.label)) === JSON.stringify(['Home', 'Create', 'Library', 'Videos', 'Settings'])
            && JSON.stringify(nav.map(n => n.id)) === JSON.stringify(['nav-home', 'nav-create', 'open-assets-btn', 'open-videos-btn', 'nav-settings'])
            && nav.every(n => n.title) && nav[0].current === 'page', JSON.stringify(nav.map(n => `${n.id}:${n.label}${n.current ? '*' : ''}`)));
        const start = await page.evaluate(() => ({ create: document.getElementById('start-create-btn').textContent.trim(), open: document.getElementById('open-studio-btn').textContent.trim(),
            demo: document.getElementById('demo-btn').textContent.trim(), generate: document.getElementById('generate-btn').hidden, title: document.title }));
        check('home: the start screen offers "Create a lesson", "Open your lessons" and "See an example lesson"; the one-step generate button is hidden',
            start.create === 'Create a lesson' && start.open === 'Open your lessons' && start.demo === 'See an example lesson' && start.generate, JSON.stringify(start));
        await page.click('#open-studio-btn');
        await waitView(page, 'home');
        await until(async () => !/Loading your lessons/.test((await sui(page)).text), 15000);
        await sleep(300);
        const u = await sui(page);
        const f = await focus(page);
        const s = await facts(page);
        await shot(page, 'home-02-your-lessons-empty.png');
        studioTexts('studio home (empty)', u);
        check('home: "Open your lessons" shows Your lessons with the empty state: what to do next and exactly one "Create a lesson"',
            u.view === 'home' && u.heading === 'Your lessons' && /You haven.t created a lesson yet/.test(u.text) && u.createButtons === 1,
            `heading "${u.heading}", ${u.createButtons} Create button(s); "${u.text.slice(0, 160)}"`);
        check('a11y: the focus moves into the Studio when it opens, and the page behind it is inert', f.inStudio && s.barInert, `focus ${f.el}; top bar inert ${s.barInert}`);
        const trapped = await tabStaysIn(page, x => x.inStudio, 25);
        check('a11y: Tab and Shift+Tab stay inside the Studio', trapped.length === 0, trapped.slice(0, 4).join(', ') || '25 stops inside');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
        await sleep(300);
        const back = await focus(page);
        const s2 = await facts(page);
        check('a11y: Esc closes the Studio; the focus returns to "Open your lessons" and the page is usable again', back.id === 'open-studio-btn' && !s2.barInert,
            `focus ${back.el}; top bar inert ${s2.barInert}`);
        await page.click('#nav-create');
        await waitView(page, 'create');
        await sleep(300);
        const c = await sui(page);
        await shot(page, 'create-01-how-to-start.png');
        studioTexts('studio create', c);
        check('create: the top bar\'s Create opens the Studio\'s Create view: a document, pasted notes or a lesson file',
            c.heading === 'How would you like to start?' && JSON.stringify(c.choices) === JSON.stringify(['from-document', 'from-text', 'open-file']) && !c.writerNote,
            `heading "${c.heading}"; choices ${c.choices.join(', ')}`);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
    });

    // ==== Settings ====================================================================================================================
    await section('settings', async () => {
        phase = 'settings';
        await page.click('#nav-settings');
        await page.waitForSelector('#settings-overlay:not([hidden])', { timeout: 10000 });
        await sleep(300);
        const f = await focus(page);
        const s = await page.evaluate(() => {
            const t = el => el.textContent.replace(/\s+/g, ' ').trim();
            const sections = [...document.querySelectorAll('#settings-body .settings-section')];
            const outside = sections.filter(x => x.dataset.section !== 'advanced');
            return {
                nav: [...document.querySelectorAll('.settings-nav-btn')].map(t), sections: sections.map(x => `${x.dataset.section}:${t(x.querySelector('h3'))}`),
                providerIn: (document.getElementById('provider-select').closest('.settings-section') || {}).dataset.section,
                modelIn: (document.getElementById('model-select').closest('.settings-section') || {}).dataset.section,
                modelsOutside: outside.map(x => { const c = x.cloneNode(true); c.querySelectorAll('details.presenter-advanced, #ai-providers-panel').forEach(d => d.remove()); return t(c); })
                    .filter(text => /\b(gemini|gpt|claude)[- ]?\d|\bmodel\b/i.test(text)).map(text => text.slice(0, 80)),
                debugOnlyShown: [...document.querySelectorAll('#settings-dialog [data-debug-only]')].filter(b => !b.hidden).map(b => b.id),
                moved: ['presenter-settings', 'cinematic-settings', 'ai-visuals-select', 'ai-providers-panel', 'tts-engine-select', 'voice-select', 'gemini-voice-select',
                    'provider-select', 'model-select', 'hardware-select', 'start-server-btn', 'download-master-prompt-btn', 'download-json-btn', 'export-html-btn', 'logout-btn', 'admin-btn']
                    .filter(id => !(document.getElementById(id) && document.getElementById(id).closest('#settings-dialog'))),
                account: t(document.getElementById('settings-account')), logout: !document.getElementById('logout-btn').hidden
            };
        });
        await shot(page, 'settings-01-general.png');
        check('settings: five groups (General · Lesson defaults · Visuals & AI · Voice · Advanced), each a titled section',
            JSON.stringify(s.nav) === JSON.stringify(['General', 'Lesson defaults', 'Visuals & AI', 'Voice', 'Advanced'])
            && JSON.stringify(s.sections) === JSON.stringify(['general:General', 'lesson:Lesson defaults', 'visuals:Visuals & AI', 'voice:Voice', 'advanced:Advanced']),
            `${s.nav.join(' · ')}; ${s.sections.join(', ')}`);
        check('settings: the start screen\'s controls are all here; the AI writer\'s service and model only under Advanced, no model named elsewhere, debug-only tools hidden',
            s.moved.length === 0 && s.providerIn === 'advanced' && s.modelIn === 'advanced' && s.modelsOutside.length === 0 && s.debugOnlyShown.length === 0,
            `missing ${JSON.stringify(s.moved)}; service in ${s.providerIn}, model in ${s.modelIn}; outside Advanced: ${JSON.stringify(s.modelsOutside)}; debug-only shown ${JSON.stringify(s.debugOnlyShown)}`);
        check('settings: General says who is signed in and offers Sign out', /admin/.test(s.account) && s.logout, `"${s.account}"; sign out ${s.logout}`);
        check('a11y: the focus moves into Settings when it opens', f.inSettings, f.el);
        for (const key of ['lesson', 'visuals', 'voice', 'advanced']) {
            await page.click(`.settings-nav-btn[data-section="${key}"]`);
            await sleep(350);
            await shot(page, `settings-0${['lesson', 'visuals', 'voice', 'advanced'].indexOf(key) + 2}-${key}.png`);
        }
        const current = await page.evaluate(() => (document.querySelector('.settings-nav-btn[aria-current="true"]') || {}).textContent);
        check('settings: a group\'s button scrolls to it and is marked as the current one', current === 'Advanced', `current "${current}"`);
        const trapped = await tabStaysIn(page, x => x.inSettings, 30);
        check('a11y: Tab and Shift+Tab stay inside Settings', trapped.length === 0, trapped.slice(0, 4).join(', ') || '30 stops inside');
        await page.keyboard.press('Escape');
        await page.waitForSelector('#settings-overlay', { state: 'hidden', timeout: 5000 });
        const back = await focus(page);
        check('a11y: Esc closes Settings and the focus returns to the Settings button', back.id === 'nav-settings', back.el);
        // the debug view: the lesson-file tools appear (still off without a lesson)
        const dbg = await newPage(browser);
        await dbg.page.goto(BASE + '/?visualDebug=1');
        await dbg.page.waitForSelector('#nav-settings', { state: 'visible', timeout: 30000 });
        await dbg.page.click('#nav-settings');
        await dbg.page.waitForSelector('#settings-overlay:not([hidden])');
        const dbgTools = await dbg.page.evaluate(() => [...document.querySelectorAll('#settings-dialog [data-debug-only]')].map(b => `${b.id}:${b.hidden ? 'hidden' : 'shown'}:${b.disabled ? 'off' : 'on'}`));
        await dbg.context.close();
        check('settings: with ?visualDebug the raw lesson file and scene list tools are shown (off until a lesson is open)',
            dbgTools.length === 2 && dbgTools.every(x => /:shown:off$/.test(x)), dbgTools.join(', '));
    });

    // ==== Errors: a server without a lesson writer ===================================================================================
    await section('create: no lesson writer on the server', async () => {
        phase = 'no-writer';
        serverB = startServer('server-b', PORT_B, { AI_FAKE_PROVIDER: '', AI_GENERATION_ENABLED: '0' });
        await waitForServer(BASE_B, serverB.proc);
        const tokB = await login(BASE_B);
        const b = await newPage(browser, { tok: tokB, base: BASE_B });
        await b.page.goto(BASE_B + '/');
        await b.page.waitForSelector('#nav-create', { state: 'visible', timeout: 30000 });
        await confirmSilent(b.page);
        await b.page.click('#nav-create');
        await waitView(b.page, 'create');
        await b.page.waitForSelector('.studio-root .studio-writer-note', { timeout: 15000 });
        const c = await sui(b.page);
        await shot(b.page, 'create-errors-01-no-writer.png');
        await b.page.click('.studio-root [data-action="from-text"]');
        await waitView(b.page, 'paste');
        await b.page.fill('.studio-root [data-field="text"]', '# Levers\n\nA lever turns about a fulcrum.');
        await sleep(300);
        const p = await sui(b.page);
        await shot(b.page, 'create-errors-02-no-writer-paste.png');
        check('create (error): on a server without a lesson writer the Create view says so in plain words, and "Write the lesson" is turned off',
            /can't write lessons with AI yet — ask your administrator/.test(c.writerNote || '') && /can't write lessons with AI yet/.test(p.writerNote || '') && p.startOff && p.startOff.disabled && p.startOff.off === 'true'
            && !PROVIDER_WORDS.test(c.writerNote || ''),
            `note "${c.writerNote}"; write button ${JSON.stringify(p.startOff)}`);
        texts.push({ where: 'no-writer create view', text: c.ownText });
        await b.context.close();
        killServer(serverB.proc);
    });

    // ==== Create from a document: the Document Assistant, the name, the written lesson; the Studio's stages ===========================
    let pidDoc = null;
    let docTitle = '';
    await section('create: a document through the Studio', async () => {
        phase = 'create-document';
        await page.click('#nav-create');
        await waitView(page, 'create');
        const chooser = page.waitForEvent('filechooser', { timeout: 15000 });
        await page.click('.studio-root [data-action="from-document"]');
        const fc = await chooser;
        await fc.setFiles(path.join(FIXTURES, 'photosynthesis.txt'));
        await page.waitForSelector('.doc-overlay.open .doc-card', { timeout: 60000 });
        await page.waitForFunction(() => !/AI analysis running/.test((document.querySelector('.doc-overlay.open .doc-ai') || {}).textContent || ''), null, { timeout: 90000 });
        await sleep(500);
        const docFocus = await focus(page);
        await shot(page, 'create-02-document-assistant.png');
        await page.click('.doc-overlay.open .doc-use');
        await waitView(page, 'name', 20000);
        await sleep(300);
        const named = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.studio-root [data-field]')].map(i => [i.dataset.field, i.value])));
        const n = await sui(page);
        await shot(page, 'create-03-name.png');
        await page.fill('.studio-root [data-field="subject_name"]', 'Biology');
        const progressSeen = [];
        await page.evaluate(() => {
            window.__steps = [];
            const sample = () => {
                const r = document.querySelector('.studio-root');
                if (!r || r.getAttribute('data-view') !== 'progress') return;
                const k = [...r.querySelectorAll('.studio-step')].map(x => `${x.dataset.step}:${x.dataset.status}:${(x.querySelector('.studio-step-state') || {}).textContent || ''}`).join('|');
                if (window.__steps[window.__steps.length - 1] !== k) window.__steps.push(k);
            };
            window.__stepTimer = setInterval(sample, 50);
        });
        await page.click('.studio-root [data-action="start-lesson"]');
        await waitView(page, 'progress', 15000);
        await sleep(700);
        const pr = await sui(page);
        await shot(page, 'create-04-writing.png');
        await writeDone(page);
        progressSeen.push(...await page.evaluate(() => { clearInterval(window.__stepTimer); return window.__steps; }));
        const u = await sui(page);
        const s = await facts(page);
        pidDoc = s.project;
        docTitle = u.heading;
        await shot(page, 'create-05-written-stages.png');
        studioTexts('studio name view', n);
        studioTexts('studio progress view', pr);
        const stepStates = [...new Set(progressSeen.flatMap(k => k.split('|').map(x => x.split(':').slice(2).join(':'))))].filter(Boolean);
        check('create: "Upload a document" asks for the file and opens the Document Assistant (the focus in it); "Write the lesson from this" leads to "Name your lesson" with the title filled in',
            docFocus.inDoc && n.heading === 'Name your lesson' && /How Plants Make Food/i.test(named.session_title || ''), `focus ${docFocus.el}; prefilled ${JSON.stringify(named)}`);
        check('create: the lesson is written with its progress shown as steps (each state an icon and words), then the Studio opens on its stages',
            pr.heading === 'Writing your lesson' && pr.steps.length >= 3 && stepStates.length >= 2 && stepStates.every(x => STATE_ICON.test(x) || /^[✓●○⚠]/.test(x))
            && u.view === 'lesson' && u.stages.length === 7 && Number.isInteger(pidDoc) && s.url === `?project_id=${pidDoc}`,
            `steps ${pr.steps.map(x => x.step).join(', ')}; states seen ${JSON.stringify(stepStates)}; lesson ${pidDoc} "${docTitle}"`);
    });

    await section('studio: stages', async () => {
        phase = 'studio-stages';
        const u0 = await sui(page);
        const keys = u0.stages.map(x => x.key);
        const nexts = u0.stages.filter(x => x.next);
        check('studio: seven stages, each with its state as an icon and words (never colour alone)',
            JSON.stringify(keys) === JSON.stringify(['content', 'lesson', 'visuals', 'style', 'review', 'preview', 'export']) && u0.stages.every(x => STATE_ICON.test(x.text || '')),
            u0.stages.map(x => `${x.key}: ${x.text}`).join(' · '));
        check('studio: exactly one stage carries the "Next step" label', nexts.length === 1 && nexts[0].nextText === 'Next step', nexts.map(x => x.key).join(', ') || 'none');
        // direct navigation: the last stage first, then every stage in turn (screenshots of each)
        const seen = [];
        for (const key of ['export', 'content', 'lesson', 'visuals', 'style', 'review', 'preview', 'export']) {
            await stage(page, key);
            const u = await sui(page);
            const cur = u.stages.find(x => x.current) || {};
            seen.push({ key, detail: u.detail.stage, title: u.detail.title, aria: cur.aria, status: u.detail.status, next: u.detail.next, nextBtn: u.detail.nextBtn });
            studioTexts(`studio stage ${key}`, u);
            if (!seen.slice(0, -1).some(x => x.key === key)) await shot(page, `studio-stage-${keys.indexOf(key) + 1}-${key}.png`);
        }
        const names = { content: 'Content', lesson: 'Lesson', visuals: 'Visuals & presenter', style: 'Style', review: 'Review & edit', preview: 'Preview', export: 'Export' };
        check('studio: any stage opens directly (even the last one first): its title, its state in words and the rail marks it as the current step',
            seen.every(x => x.detail === x.key && x.title === `${keys.indexOf(x.key) + 1} · ${names[x.key]}` && x.aria === 'step' && STATE_ICON.test(x.status || '')),
            seen.map(x => `${x.key}→${x.detail} "${x.title}" ${x.aria}`).join(' · '));
        const nextKey = nexts[0] ? nexts[0].key : null;
        if (nextKey === 'export') await stage(page, 'content'); // (the button is offered on every other stage)
        const onOther = seen.filter(x => x.key !== nextKey);
        const onNext = seen.find(x => x.key === nextKey) || {};
        const nextAt = keys.indexOf(nextKey);
        const offered = x => (keys.indexOf(x.key) < nextAt ? `Next step: ${names[nextKey]} →` : `Still to do: ${nextAt + 1} · ${names[nextKey]} →`);
        check('studio: the suggested stage says "Suggested next step"; a stage before it offers "Next step: … →", a stage after it "Still to do: N · … →" (never "next" pointing back)',
            nextKey && onNext.next === 'Suggested next step' && !onNext.nextBtn && onOther.every(x => x.nextBtn === offered(x)),
            `next ${nextKey}; on it "${onNext.next}"; elsewhere ${onOther.map(x => `${x.key}: ${x.nextBtn}`).join(' / ')}`);
        await page.click('.studio-root [data-action="next-stage"]');
        await sleep(400);
        const after = await sui(page);
        const f = await focus(page);
        check('studio: "Next step" opens the suggested stage and moves the focus to its title', after.detail.stage === nextKey && f.id === 'studio-detail-title',
            `stage ${after.detail.stage}; focus ${f.el}`);
    });

    await section('studio: continue after a reload, and from Home', async () => {
        phase = 'continue';
        await page.reload();
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await sleep(400);
        const card = await page.evaluate(() => ({ title: document.getElementById('start-overlay-title').textContent.trim(),
            buttons: [...document.querySelectorAll('#start-overlay button')].filter(b => b.getBoundingClientRect().width > 0).map(b => `${b.id}:${b.textContent.trim()}`) }));
        await shot(page, 'continue-01-start-card.png');
        check('navigation: the start card of ?project_id= offers Open in Studio, Play the lesson and Back to Home',
            card.buttons.includes('start-studio-btn:Open in Studio') && card.buttons.includes('start-lecture-btn:▶ Play the lesson') && card.buttons.includes('start-home-btn:Back to Home'),
            `"${card.title}": ${card.buttons.join(', ')}`);
        await page.click('#start-studio-btn');
        await lessonView(page);
        await studioIdle(page);
        const u = await sui(page);
        check('studio: after a reload "Open in Studio" continues the same lesson on its stages', u.heading === docTitle && u.stages.length === 7, `"${u.heading}"`);
        await page.click('.studio-root [data-action="home"]');
        await waitView(page, 'home');
        await until(async () => (await sui(page)).lessons.length > 0, 15000);
        const h = await sui(page);
        const row = h.lessons.find(l => Number(l.id) === pidDoc) || {};
        await shot(page, 'home-03-your-lessons.png');
        studioTexts('studio home (lessons)', h);
        check('home: Your lessons lists the lesson with a plain stage chip (icon + words) and Continue',
            row.title === docTitle && STATE_ICON.test(row.chip || '') && !/_/.test(row.chip || '') && /^(Continue|Open)$/.test(row.button || '') && /“/.test(row.buttonLabel || '')
            && h.createButtons === 1, JSON.stringify(row));
        await page.click(`.studio-root [data-action="open-lesson"][data-project-id="${pidDoc}"]`);
        await lessonView(page);
        await studioIdle(page);
        const again = await sui(page);
        check('home: Continue opens the lesson\'s stages', again.heading === docTitle && (await facts(page)).project === pidDoc, `"${again.heading}"`);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
    });

    // ==== Accessibility: the keyboard-only path (Home → Create → paste → write → stage → editor → back) ==============================
    let pidPaste = null;
    await section('a11y: keyboard-only path', async () => {
        phase = 'keyboard';
        const k = await newPage(browser);
        const p = k.page;
        await p.goto(BASE + '/');
        await p.waitForSelector('#start-create-btn', { state: 'visible', timeout: 30000 });
        await confirmSilent(p);
        await sleep(800);
        const steps = [];
        const log = (what, ok, f) => steps.push({ what, ok: !!ok, f: f ? f.el : null, ring: f ? f.ring : null });
        let n = await tabTo(p, f => f.id === 'nav-create', { max: 15 });
        let f = await focus(p);
        log('Tab to Create in the top bar', n > 0, f);
        await p.keyboard.press('Enter');
        await waitView(p, 'create');
        await sleep(300);
        f = await focus(p);
        log('Enter: the Create view, focus in the Studio', f.inStudio, f);
        n = await tabTo(p, x => x.action === 'from-text', { max: 12 });
        f = await focus(p);
        log('Tab to "Paste your notes"', n > 0, f);
        await p.keyboard.press('Enter');
        await waitView(p, 'paste');
        await sleep(300);
        n = await tabTo(p, x => x.tag === 'textarea', { max: 6 });
        f = await focus(p);
        log('Tab to the notes box', n > 0, f);
        await p.keyboard.insertText(fs.readFileSync(path.join(FIXTURES, 'newtons_second_law.txt'), 'utf8'));
        n = await tabTo(p, x => x.action === 'start-lesson', { max: 12 });
        f = await focus(p);
        log('Tab to "Write the lesson"', n > 0, f);
        await p.keyboard.press('Enter');
        await waitView(p, 'progress', 15000);
        f = await focus(p);
        log('Enter: writing (the focus stays in the Studio)', f.inStudio, f);
        await writeDone(p);
        await sleep(600);
        pidPaste = (await facts(p)).project;
        n = await tabTo(p, x => x.stage === 'lesson', { max: 25 });
        f = await focus(p);
        log('Tab to the Lesson stage', n > 0, f);
        await p.keyboard.press('Enter');
        await p.waitForFunction(() => (document.querySelector('.studio-root .studio-detail') || {}).getAttribute && document.querySelector('.studio-root .studio-detail').getAttribute('data-stage') === 'lesson', null, { timeout: 10000 });
        n = await tabTo(p, x => x.action === 'open-editor', { max: 25 });
        f = await focus(p);
        log('Tab to "Open the editor"', n > 0, f);
        await p.keyboard.press('Enter');
        await p.waitForSelector('.editor-root .editor-scene', { timeout: 30000 });
        await sleep(1500);
        f = await focus(p);
        log('Enter: the editor, focus in it', f.inEditor, f);
        await shot(p, 'a11y-keyboard-editor.png');
        n = await tabTo(p, x => x.inEditor && x.action === 'close', { max: 30, shift: true }); // (back to the toolbar: the timeline has many stops)
        f = await focus(p);
        const closeText = n > 0 ? f.text : '';
        log(`Tab to "${closeText}"`, n > 0 && /Back to the Studio/.test(closeText), f);
        await p.keyboard.press('Enter');
        await lessonView(p).catch(() => {});
        await sleep(600);
        f = await focus(p);
        log('Enter: back in the Studio (focus in it)', f.inStudio && !!(await sui(p)).stages.length, f);
        await shot(p, 'a11y-keyboard-back-in-studio.png');
        record.keyboard = steps;
        check('a11y: keyboard only: Home → Create → paste → write → Lesson stage → editor → "Back to the Studio", every step reached with Tab/Enter and a visible focus ring',
            steps.every(x => x.ok) && steps.filter(x => x.f && x.f !== 'body').every(x => x.ring), steps.map(x => `${x.ok ? '✓' : '✗'} ${x.what} [${x.f}${x.ring ? '' : ', no ring'}]`).join(' · '));
        await k.context.close();
    });

    // ==== Errors: empty notes refused; writing cancelled with the in-panel question ====================================================
    await section('create: errors and cancel', async () => {
        phase = 'cancel';
        await page.goto(BASE + '/');
        await page.waitForSelector('#nav-create', { state: 'visible', timeout: 30000 });
        await page.click('#nav-create');
        await waitView(page, 'create');
        await page.click('.studio-root [data-action="from-text"]');
        await waitView(page, 'paste');
        await page.fill('.studio-root [data-field="session_title"]', 'Friction');
        await page.click('.studio-root [data-action="start-lesson"]');
        await page.waitForSelector('.studio-root .studio-error[role="alert"]', { timeout: 5000 });
        const empty = await sui(page);
        await shot(page, 'create-errors-03-empty-notes.png');
        check('create (error): empty notes are refused in plain words (an alert), nothing is written', /Paste the lesson content first/.test(empty.error || '') && empty.view === 'paste',
            `"${empty.error}"`);
        await page.fill('.studio-root [data-field="text"]', '# Friction\n\nFriction is a force that resists sliding. Rough surfaces have more friction than smooth ones.\n\n## Examples\n\n- Brakes\n- Walking');
        await page.click('.studio-root [data-action="start-lesson"]');
        await waitView(page, 'progress', 15000);
        await sleep(300);
        await page.click('.studio-root [data-action="cancel-run"]');
        await page.waitForSelector('.studio-root .studio-confirm', { timeout: 5000 });
        const ask = await page.evaluate(() => ({ text: document.querySelector('.studio-root .studio-confirm-text').textContent, buttons: [...document.querySelectorAll('.studio-root .studio-confirm button')].map(b => b.textContent.trim()),
            role: document.querySelector('.studio-root .studio-confirm').getAttribute('role'), focus: document.activeElement && document.activeElement.dataset.action }));
        await shot(page, 'create-errors-04-stop-question.png');
        await page.click('.studio-root [data-action="confirm-cancel-run"]');
        await page.waitForSelector('.studio-root .studio-run-failed[data-status="cancelled"]', { timeout: 60000 });
        const done = await sui(page);
        await shot(page, 'create-errors-05-stopped.png');
        studioTexts('studio cancelled run', done);
        check('create: "Stop writing" asks in the panel (Stop writing / Keep writing, the focus on Keep writing); confirmed, the run stops and says nothing was saved and the document or notes are safe',
            JSON.stringify(ask.buttons) === JSON.stringify(['Stop writing', 'Keep writing']) && ask.focus === 'keep-writing' && ask.role === 'group'
            && done.heading === 'Writing stopped' && /Nothing was saved; your document or notes are safe/.test(done.text) && /Try again/.test(done.text) && /Back to your lessons/.test(done.text),
            `asked "${ask.text}" ${JSON.stringify(ask.buttons)} focus ${ask.focus}; then "${done.heading}"`);
        await page.click('.studio-root [data-action="back-home"]');
        await waitView(page, 'home');
    });

    // ==== Errors: the one-step generation (Settings → Advanced → classic) failing: plain words, the page behind it inert ===========
    await section('create: classic generation fails in plain words', async () => {
        phase = 'classic';
        expectedFailures.push({ phase, re: /generate-script|lesson could not|Traceback|stand-in failure/i });
        const c = await newPage(browser);
        const p = c.page;
        // the server's answer stands in for a provider failure (a traceback and a key name: neither may reach the user)
        await p.route('**/generate-script', r => r.fulfill({ status: 500, contentType: 'application/json',
            body: JSON.stringify({ detail: 'Traceback (most recent call last):\n  File "server.py", line 812\nKeyError: GEMINI_API_KEY (stand-in failure)' }) }));
        await p.goto(BASE + '/');
        await p.waitForSelector('#nav-settings', { state: 'visible', timeout: 30000 });
        await confirmSilent(p);
        await p.click('#nav-settings');
        await p.click('.settings-nav-btn[data-section="advanced"]');
        await p.check('#classic-generation');
        await p.click('#settings-close');
        await p.setInputFiles('#file-input', { name: 'levers.txt', mimeType: 'text/plain', buffer: Buffer.from('# Levers\n\nA lever turns about a fulcrum. The effort and the load sit on either side.') });
        await p.waitForSelector('#generate-btn:not([hidden])', { timeout: 10000 });
        const label = norm(await p.textContent('#generate-btn'));
        await p.click('#generate-btn');
        await p.waitForSelector('#loading-overlay .error-message', { timeout: 30000 });
        await sleep(700);
        const shown = await p.evaluate(() => ({ text: document.querySelector('#loading-overlay .error-message').innerText.replace(/\s+/g, ' ').trim(),
            role: document.querySelector('#loading-overlay .error-message').getAttribute('role'), barInert: document.getElementById('app-bar').hasAttribute('inert'),
            startInert: !!document.getElementById('upload-screen').closest('[inert]') && !document.getElementById('loading-overlay').closest('[inert]'),
            focus: document.activeElement && document.activeElement.textContent.trim(), buttons: [...document.querySelectorAll('#loading-overlay .error-actions button')].map(b => b.textContent.trim()) }));
        await shot(p, 'create-errors-06-classic-failed.png');
        await p.click('#loading-overlay .retry-btn');
        await p.waitForFunction(() => !document.getElementById('loading-overlay').classList.contains('active'), null, { timeout: 5000 }).catch(() => {});
        await sleep(600);
        const after = await p.evaluate(() => ({ barInert: document.getElementById('app-bar').hasAttribute('inert'), generate: !document.getElementById('generate-btn').hidden }));
        check('create (error): a failed one-step generation says what happened, that the file is safe and what to do (Try again / Dismiss), never the server\'s raw text; the page behind it is inert until it is dismissed',
            /^Open the lesson file|Write|Generate|Make/i.test(label) && /The lesson could not be made/.test(shown.text) && /Your file is still chosen/.test(shown.text) && shown.role === 'alert'
            && !/Traceback|KeyError|GEMINI|server\.py|line \d/.test(shown.text) && JSON.stringify(shown.buttons) === JSON.stringify(['Try again', 'Dismiss']) && shown.focus === 'Dismiss'
            && shown.barInert && !after.barInert && after.generate,
            `button "${label}"; "${shown.text.slice(0, 200)}"; role ${shown.role}; focus "${shown.focus}"; top bar inert while shown ${shown.barInert}, after Dismiss ${after.barInert}`);
        await c.context.close();
    });

    // ==== Create from a lesson file (.json) ===========================================================================================
    // The lesson the rest of the check uses: its first scene is titled "Introduction" (the chapters), one narration is a two-line
    // caption and one a one-line caption, an abbreviation never spelled out and Java code (quality findings)
    const LESSON = { subject_name: 'Physics', unit_name: 'Simple machines', session_number: 'Session 2', session_title: 'Levers and torque',
        concept_map: [{ id: 'c1', title: 'Levers', depends_on: [] }, { id: 'c2', title: 'Torque', depends_on: ['c1'] }],
        scenes: [
            { type: 'content', concept_id: 'c1', title: 'Introduction', html: '<p>A lever lets a small push lift a heavy load.</p>',
              narration: 'Welcome. Today we see how a lever lets a small push lift a heavy load.' },
            { type: 'content', concept_id: 'c1', title: 'What is a lever?', html: "<div class='definition'><span class='keyword'>Lever</span>: a rigid bar that turns about a fixed point called the fulcrum.</div>",
              narration: 'A lever is a rigid bar that turns about a fixed point, so a small push far away can lift a heavy load nearby.' },
            { type: 'content', concept_id: 'c2', title: 'Torque in code', html: "<pre><code class='language-java'>double torque = force * distance;\nSystem.out.println(torque);</code></pre>",
              narration: 'Here is torque in Java.' },
            { type: 'content', concept_id: 'c2', title: 'The MA of a lever', html: '<p>The MA of a lever is the load divided by the effort.</p>',
              visual: { concept: 'lever fulcrum load', description: 'A lever balanced on a fulcrum lifting a heavy box', type: 'diagram', keywords: ['lever', 'fulcrum', 'load', 'box'] },
              narration: 'The MA tells us how much the lever multiplies our push.' },
            { type: 'key-takeaway', title: 'Key takeaways', html: "<ul class='takeaway-list'><li>A lever turns about a fulcrum</li><li>Torque is force times distance</li></ul>",
              narration: 'Let us recap what we learned today.' }
        ] };
    const AT = { intro: 0, twoLine: 1, oneLine: 2, ma: 3, summary: 4 };
    const lessonFile = path.join(OUT, 'levers-and-torque.json');
    fs.writeFileSync(lessonFile, JSON.stringify(LESSON, null, 2));
    let pidJson = null;
    await section('create: a lesson file', async () => {
        phase = 'create-file';
        await page.click('.studio-root [data-action="create"]');
        await waitView(page, 'create');
        const chooser = page.waitForEvent('filechooser', { timeout: 15000 });
        await page.click('.studio-root [data-action="open-file"]');
        const fc = await chooser;
        await fc.setFiles(lessonFile);
        await lessonView(page);
        await studioIdle(page);
        const u = await sui(page);
        pidJson = (await facts(page)).project;
        await shot(page, 'create-06-lesson-file.png');
        const saved = (await api('GET', `/api/projects/${pidJson}`)).data || {};
        check('create: "Open a lesson file" takes a .json lesson: it is kept as a lesson of its own and the Studio opens on its stages',
            Number.isInteger(pidJson) && u.view === 'lesson' && /Levers and torque|Physics/.test(u.heading || '') && (saved.scenes || []).length === LESSON.scenes.length,
            `lesson ${pidJson} "${u.heading}", ${(saved.scenes || []).length} scenes`);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
    });

    // ==== The presenter for the caption check (Settings → Lesson defaults): the drawn teacher on every scene ===========================
    await section('settings: lesson defaults', async () => {
        phase = 'presenter';
        await page.click('#nav-settings');
        await page.click('.settings-nav-btn[data-section="lesson"]');
        await page.waitForSelector('#presenter-select', { state: 'visible', timeout: 15000 });
        await page.selectOption('#presenter-select', 'aadhi-teacher');
        await page.selectOption('#presenter-mode', 'always');
        await page.waitForSelector('#cinematic-mode', { state: 'visible', timeout: 15000 });
        if (await page.$eval('#cinematic-mode', s => s.value) !== 'cinematic') await page.selectOption('#cinematic-mode', 'cinematic');
        await sleep(400);
        const saved = await page.evaluate(() => ({ presenter: JSON.parse(localStorage.getItem('aadhi.presenter') || '{}'), cinematic: JSON.parse(localStorage.getItem('aadhi.cinematic') || '{}') }));
        await shot(page, 'settings-06-lesson-defaults-chosen.png');
        check('settings: Lesson defaults sets the presenter and the layout for new and open lessons (kept in this browser)',
            saved.presenter.presenter_id === 'aadhi-teacher' && saved.presenter.mode === 'always' && saved.cinematic.mode === 'cinematic', JSON.stringify(saved));
        await page.click('#settings-close');
    });

    // ==== Review & edit (from the Studio): the editor and back, a saved change, Visual Review, quality ================================
    await section('review & edit', async () => {
        phase = 'review';
        await page.goto(`${BASE}/?project_id=${pidJson}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-studio-btn');
        await lessonView(page);
        await studioIdle(page);
        await stage(page, 'review');
        await page.click('.studio-root [data-action="open-editor"]');
        await page.waitForSelector('.editor-root .editor-scene', { timeout: 30000 });
        await sleep(1500);
        const back = await page.evaluate(() => { const b = document.querySelector('.editor-root [data-action="close"]'); return b ? { text: b.textContent.replace(/\s+/g, ' ').trim(), label: b.getAttribute('aria-label') } : null; });
        // a change, saved: the scene's title in the inspector
        await page.click(`.editor-root .editor-scene-list .editor-scene >> nth=${AT.summary}`);
        await sleep(400);
        const input = page.locator('#editor-field-title');
        await input.click();
        await input.fill('What we learned');
        await page.keyboard.press('Escape'); // leaves the field (one "Edit title" step; a text field keeps its edit open while focused)
        await page.waitForFunction(() => { const s = lessonEditorSession; return s && s.autosave.state === 'saved' && !s.model.dirty && !s.autosave.running; }, null, { timeout: 30000 }).catch(() => {});
        const saveUi = await page.evaluate(() => ({ state: (document.querySelector('.editor-save') || {}).getAttribute ? document.querySelector('.editor-save').getAttribute('data-state') : null,
            text: (document.querySelector('.editor-save') || {}).textContent }));
        const editorText = await page.evaluate(() => document.querySelector('.editor-root').textContent.replace(/\s+/g, ' '));
        texts.push({ where: 'editor', text: editorText });
        await shot(page, 'review-01-editor.png');
        const stored = ((await api('GET', `/api/projects/${pidJson}`)).data || {}).scenes || [];
        check('review & edit: a change in the editor is saved ("Saved" on the toolbar; the server has it)',
            saveUi.state === 'saved' && stored[AT.summary] && stored[AT.summary].title === 'What we learned', `${JSON.stringify(saveUi)}; stored "${stored[AT.summary] && stored[AT.summary].title}"`);
        await page.click('.editor-root [data-action="close"]');
        await lessonView(page);
        await sleep(400);
        const u = await sui(page);
        check('review & edit: the editor opened from the Studio offers "Back to the Studio", which returns to the Studio\'s stages',
            back && /Back to the Studio/.test(back.text) && u.view === 'lesson' && !(await facts(page)).editor, `button ${JSON.stringify(back)}; then ${u.view}`);
        // Visual Review from the Studio
        await stage(page, 'review');
        await page.click('.studio-root [data-action="open-review"]');
        await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
        await sleep(800);
        const rf = await focus(page);
        const items = await page.evaluate(() => [...document.querySelectorAll('.review-overlay.open .review-item')].map((b, i) => ({ i, slot: b.dataset.slot, scene: b.dataset.scene })));
        let origin = null;
        for (const it of items) {
            await page.click(`.review-overlay.open .review-item >> nth=${it.i}`);
            await sleep(250);
            origin = await page.evaluate(() => { const o = document.querySelector('.review-overlay.open .review-origin'); return o ? o.textContent.trim() : null; });
            if (origin) break;
        }
        const reviewFacts = await page.evaluate(() => ({ providerLines: document.querySelectorAll('.review-overlay.open .review-provider').length,
            text: document.querySelector('.review-overlay.open').textContent.replace(/\s+/g, ' ') }));
        await shot(page, 'review-02-visual-review.png');
        texts.push({ where: 'visual review', text: reviewFacts.text });
        check('review & edit: Visual Review (from the Studio, the focus in it) says where a visual came from in plain words, with no provider line or name',
            rf.inReview && !!origin && reviewFacts.providerLines === 0 && !PROVIDER_WORDS.test(reviewFacts.text), `focus ${rf.el}; origin "${origin}"; provider lines ${reviewFacts.providerLines}`);
        // quality: the headline and a finding with "Show scene"
        await page.click('.review-overlay.open .quality-run');
        await page.waitForFunction(() => { const b = document.querySelector('.review-overlay.open .quality-headline'); return b && b.getAttribute('data-status') !== 'none'; }, null, { timeout: 60000 });
        await sleep(500);
        if (await page.$('.review-overlay.open .quality-toggle[aria-expanded="false"]')) await page.click('.review-overlay.open .quality-toggle');
        await sleep(300);
        const q = await page.evaluate(() => ({ headline: (document.querySelector('.review-overlay.open .quality-headline') || {}).textContent,
            status: document.querySelector('.review-overlay.open .quality-headline').getAttribute('data-status'),
            issues: [...document.querySelectorAll('.review-overlay.open .quality-issues .quality-issue')].map(li => ({ sev: (li.querySelector('.quality-severity') || {}).textContent.trim(),
                message: (li.querySelector('.quality-message') || {}).textContent.trim(), show: li.querySelector('.quality-show') ? li.querySelector('.quality-show').dataset.scene : null })) }));
        await page.locator('.review-overlay.open .quality-issues').scrollIntoViewIfNeeded().catch(() => {});
        await shot(page, 'review-03-quality.png');
        const withShow = q.issues.find(x => x.show !== null);
        let shown = null;
        if (withShow) {
            await page.click(`.review-overlay.open .quality-show[data-scene="${withShow.show}"] >> nth=0`);
            await sleep(600);
            shown = await page.evaluate(() => ({ position: (document.querySelector('.review-overlay.open .review-position') || {}).textContent,
                selected: (document.querySelector('.review-overlay.open .review-item.selected') || {}).dataset }));
            await shot(page, 'review-04-show-scene.png');
        }
        check('review & edit: "Check quality" gives a headline and findings, each with its severity as an icon and words; "Show scene" shows that scene',
            !!q.headline && q.issues.length > 0 && q.issues.every(x => /^\S+\s+\w+/.test(x.sev || '')) && withShow && shown && Number(shown.selected && shown.selected.scene) === Number(withShow.show)
            && new RegExp(`^Scene ${Number(withShow.show) + 1} of`).test(shown.position || ''),
            `"${q.headline}" (${q.status}); ${q.issues.slice(0, 3).map(x => `${x.sev}: ${x.message.slice(0, 50)}`).join(' | ')}; shown ${JSON.stringify(shown)}`);
        // (every focus move after the close, with the code that made it: the evidence when the focus lands elsewhere)
        await page.evaluate(() => {
            window.__focusLog = [];
            const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.dataset && el.dataset.action ? `[${el.dataset.action}]` : '');
            window.__focusLogger = e => window.__focusLog.push(`${name(e.target)} by ${(new Error().stack || '').split(/\n/).slice(2, 5).map(l => l.trim().replace(/^at /, '').replace(/https?:\/\/[^/]+\//, '')).join(' < ')}`);
            document.addEventListener('focusin', window.__focusLogger, true);
        });
        await page.click('.review-overlay.open .review-panel .export-close');
        await page.waitForFunction(() => !document.querySelector('.review-overlay.open'), null, { timeout: 10000 });
        await sleep(1800); // (the Studio refreshes when Visual Review has closed: up to 0.5 s, then its lesson state)
        const after = await focus(page);
        const moves = await page.evaluate(() => { document.removeEventListener('focusin', window.__focusLogger, true); return window.__focusLog; });
        check('a11y: Visual Review closed: the focus returns to the button that opened it ("Open Visual Review" in the Studio)', after.inStudio && after.action === 'open-review',
            `${after.el}; focus moves: ${moves.join(' | ') || 'none'}`);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
    });

    // ==== Style: the four styles change the stage, never the product UI's colours ======================================================
    const FAMILIES = ['cinematic_education', 'academic', 'children_education', 'corporate_training'];
    await section('style', async () => {
        phase = 'style';
        await startPlaying(page, pidJson);
        await page.evaluate(n => { currentSlide = n; renderSlide(n); }, AT.twoLine);
        await sleep(800);
        const seen = [];
        const paused = [];
        for (const family of FAMILIES) {
            await showPlayerBar(page);
            // the lesson paused by the user (the play button) before the Studio opens
            if (await page.evaluate(() => ttsState.isPlaying)) { await page.click('#tts-play-btn'); await sleep(500); }
            const before = await page.evaluate(() => ttsState.isPlaying);
            await page.click('#studio-btn');
            await lessonView(page);
            await studioIdle(page);
            await stage(page, 'style');
            await page.waitForSelector('.studio-root .studio-settings[data-settings="style"] #cinematic-settings .style-option', { timeout: 15000 });
            await page.click(`.studio-root #cinematic-settings .style-option[data-style="${family}"]`);
            await page.waitForFunction(f => { const o = document.querySelector(`.studio-root #cinematic-settings .style-option[data-style="${f}"]`); return o && o.getAttribute('aria-checked') === 'true'; }, family, { timeout: 15000 });
            await studioIdle(page);
            await sleep(900);
            const during = await page.evaluate(() => ttsState.isPlaying);
            const inStudio = await page.evaluate(uiColours);
            await shot(page, `style-${family}-studio.png`);
            await page.click('.studio-root [data-action="close"]');
            await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
            await page.waitForFunction(f => document.body.getAttribute('data-cine-style') === f, family, { timeout: 20000 }).catch(() => {});
            await sleep(1200);
            paused.push({ family, before, during, after: await page.evaluate(() => ttsState.isPlaying) });
            await showPlayerBar(page);
            const onStage = await page.evaluate(uiColours);
            const look = await page.evaluate(stageLook);
            await shot(page, `style-${family}-stage.png`);
            seen.push({ family, ui: { ...onStage, studioPanel: inStudio.studioPanel, studioPrimary: inStudio.studioPrimary, studioStage: inStudio.studioStage, studioTitle: inStudio.studioTitle, studioBtn: inStudio.studioBtn },
                look });
        }
        record.styles = seen;
        record.stylePaused = paused;
        check('style: choosing a style while the lesson is paused keeps it paused (nothing starts playing behind the Studio or after it closes)',
            paused.every(x => !x.before && !x.during && !x.after), paused.map(x => `${x.family}: paused ${!x.before} → while in the Studio playing ${x.during} → after closing playing ${x.after}`).join(' · '));
        const uiKeys = Object.keys(seen[0].ui).filter(k => seen[0].ui[k] !== null);
        const changed = uiKeys.filter(k => new Set(seen.map(s => s.ui[k])).size > 1);
        const looks = new Set(seen.map(s => s.look.tokens + s.look.titleColour));
        check('style: each of the four styles is applied to the stage (its style, tokens and title colour differ)',
            seen.every(s => s.look.style === s.family && s.look.cinematic) && looks.size === FAMILIES.length,
            seen.map(s => `${s.family}: ${s.look.style}/${s.look.tone} title ${s.look.titleColour}`).join(' · '));
        check('style: the product UI\'s computed colours are the same in all four styles (top bar, player bar, the Studio\'s panel, buttons, stages)',
            uiKeys.length >= 8 && changed.length === 0, changed.length ? changed.map(k => `${k}: ${[...new Set(seen.map(s => s.ui[k]))].join(' ≠ ')}`).join(' | ') : `${uiKeys.length} colours compared: ${uiKeys.join(', ')}`);
    });

    // ==== Preview: play, seek, captions ===============================================================================================
    await section('preview: play and seek', async () => {
        phase = 'preview';
        await page.evaluate(() => { window.__holdScene = true; }); // (it plays, but stays on a scene while it is measured)
        await showPlayerBar(page);
        await page.click('#studio-btn');
        await lessonView(page);
        await studioIdle(page);
        await stage(page, 'preview');
        await page.click('.studio-root [data-action="preview"]');
        await page.waitForFunction(() => !document.querySelector('.studio-root') && ttsState.isPlaying && document.body.classList.contains('presentation-active'), null, { timeout: 20000 });
        await sleep(1500);
        const playing = await facts(page);
        await shot(page, 'preview-01-playing.png');
        check('preview: "Preview the lesson" closes the Studio and plays the lesson from its first scene', playing.playing && playing.active && playing.current === AT.intro,
            `playing ${playing.playing}, scene ${playing.current}`);
        await showPlayerBar(page);
        await page.click(`#slide-scrubber-container .scrubber-dot[data-scene="${AT.ma}"]`);
        await sleep(900);
        const sought = await page.evaluate(() => ({ current: currentSlide, title: (document.querySelector('.cine-title-text') || document.getElementById('slide-title-container')).textContent.trim() }));
        await page.keyboard.press('ArrowLeft');
        await sleep(900);
        const back = await page.evaluate(() => currentSlide);
        await shot(page, 'preview-02-seek.png');
        check('preview: seeking with the scene scrubber jumps to that scene; the arrow key steps back one',
            sought.current === AT.ma && /MA of a lever/.test(sought.title) && back === AT.ma - 1, `scrubber → scene ${sought.current} "${sought.title}"; ← → scene ${back}`);
    });

    // A scene held on the stage while its narration plays: the caption measured once it shows the scene's narration
    async function captionOf(index, viewport) {
        if (viewport) { await page.setViewportSize(viewport); await sleep(700); }
        await page.evaluate(n => { window.__holdScene = true; ttsState.isPlaying = true; updateTTSButtons(); currentSlide = n; renderSlide(n); }, index);
        const want = norm(LESSON.scenes[index].narration).slice(0, 30);
        await page.waitForFunction(w => { const t = document.getElementById('subtitle-track'); return t.classList.contains('active') && t.textContent.replace(/\s+/g, ' ').trim().startsWith(w); }, want, { timeout: 30000 }).catch(() => {});
        await sleep(700); // the caption's transition, then the fit (a ResizeObserver) has run
        // the pointer off the page, then idle: the player bar steps aside 3 s after the last activity (and with it the caption's
        // lift above the bar), so the caption is measured where the video has it
        // (the scene is started by the check, not from the bar: no control of the bar keeps the keyboard focus)
        await page.evaluate(() => { if (document.activeElement && document.activeElement.closest('#voice-control-bar')) document.activeElement.blur(); });
        await page.mouse.move(VIEW.width / 2, -40).catch(() => {});
        await sleep(3800);
        return page.evaluate(captionFacts);
    }
    const overlaps = (a, b) => !!a && !!b && Math.min(a.r, b.r) - Math.max(a.l, b.l) > 1 && Math.min(a.b, b.b) - Math.max(a.t, b.t) > 1;
    await section('preview: captions fit their band', async () => {
        phase = 'captions';
        const two = await captionOf(AT.twoLine);
        await shot(page, 'preview-03-caption-two-lines-1280.png');
        const one = await captionOf(AT.oneLine);
        await shot(page, 'preview-04-caption-one-line-1280.png');
        const big = await captionOf(AT.twoLine, { width: 1920, height: 1080 });
        await shot(page, 'preview-05-caption-1920.png');
        const bigOne = await captionOf(AT.oneLine);
        await page.setViewportSize(VIEW);
        await sleep(600);
        record.captions = { two, one, big, bigOne };
        const clear = c => c.box.t >= c.bandTop - 4.5 && c.box.b <= c.vh + 0.5 && !overlaps(c.box, c.nameCard) && (!c.board || c.board.b <= c.box.t + 1);
        check('preview: at 1280×720 a two-line caption stays inside its band, clear of the presenter\'s name card and the board',
            two.cinematic && two.style === 'corporate_training' && two.lines === 2 && !!two.nameCard && clear(two),
            `${two.lines} lines "${two.text.slice(0, 50)}…"; caption ${two.box.t}–${two.box.b} (band from ${two.bandTop}), bottom ${two.bottom}, drop "${two.drop}", fit "${two.fit}"; `
            + `name card ${two.nameCard ? `${two.nameCard.t}–${two.nameCard.b} "${two.nameCard.text}"` : 'not shown'}; board ${two.board ? `${two.board.t}–${two.board.b}` : '-'}`);
        check('preview: at 1280×720 a one-line caption keeps its place (bottom 60 px, nothing set) and is clear too',
            one.lines === 1 && one.bottom === '60px' && !one.drop && !one.fit && clear(one), `${one.lines} line "${one.text}"; bottom ${one.bottom}, drop "${one.drop}", fit "${one.fit}"; caption ${one.box.t}–${one.box.b}`);
        check('preview: at 1920×1080 nothing moves: the captions keep bottom 60 px and their size, inside the band',
            big.bottom === '60px' && !big.drop && !big.fit && bigOne.bottom === '60px' && !bigOne.drop && !bigOne.fit && big.fontSize === two.fontSize && clear(big) && clear(bigOne),
            `two-line scene: ${big.lines} line(s), bottom ${big.bottom}, font ${big.fontSize} (720p ${two.fontSize}), caption ${big.box.t}–${big.box.b} (band from ${big.bandTop}); one-line: bottom ${bigOne.bottom}`);
        await page.evaluate(() => { ttsState.isPlaying = false; updateTTSButtons(); try { speakNarration(null); } catch (e) { /* nothing playing */ } });
    });

    // ==== Preview: a paused lesson's caption stays above the player bar (the preview only; the recording is unchanged) ===============
    // The caption as drawn: its box, its transform's vertical offset, the lift the page keeps for it, the fit variables
    const liftFacts = () => page.evaluate(() => {
        const track = document.getElementById('subtitle-track');
        const bar = document.getElementById('voice-control-bar');
        const t = track.getBoundingClientRect();
        const b = bar.getBoundingClientRect();
        const m = getComputedStyle(track).transform.match(/matrix\(([^)]+)\)/);
        const ty = m ? +m[1].split(',')[5] : null;
        return { trackTop: +t.top.toFixed(1), trackBottom: +t.bottom.toFixed(1), barTop: +b.top.toFixed(1), barH: Math.round(b.height), ty,
            active: track.classList.contains('active'), text: track.textContent.replace(/\s+/g, ' ').trim().slice(0, 40),
            playing: document.body.classList.contains('lesson-playing'), recording: document.body.hasAttribute('data-recording'),
            lift: document.documentElement.style.getPropertyValue('--ui-caption-lift'), drop: track.style.getPropertyValue('--cine-caption-drop'),
            fit: track.style.getPropertyValue('--cine-caption-fit'), bottom: getComputedStyle(track).bottom,
            nameCard: (() => { const n = document.querySelector('#presenter-layer .cine-presenter-name'); if (!n) return null; const r = n.getBoundingClientRect();
                return r.width > 0 && getComputedStyle(n).display !== 'none' ? { l: r.left, t: +r.top.toFixed(1), r: r.right, b: +r.bottom.toFixed(1),
                    visible: getComputedStyle(n).visibility !== 'hidden' } : null; })(),
            // the caption's words (each line's box), not its 80 % wide band
            lines: (() => { const rg = document.createRange(); rg.selectNodeContents(track); return [...rg.getClientRects()].filter(r => r.width > 1)
                .map(r => ({ l: Math.round(r.left), t: +r.top.toFixed(1), r: Math.round(r.right), b: +r.bottom.toFixed(1) })); })() };
    });
    await section('preview: a paused caption stays above the player bar', async () => {
        phase = 'caption-lift';
        const seen = [];
        const bars = [];
        for (const vp of [{ key: '1280', width: 1280, height: 720 }, { key: '820', width: 820, height: 1180 }]) {
            await captionOf(AT.twoLine, { width: vp.width, height: vp.height }); // (playing, the pointer idle for 3.8 s)
            const playing = await liftFacts();
            // the user's way: pause, then Play again with the mouse on the bar's button; the pointer then leaves and rests
            await showPlayerBar(page);
            await page.click('#tts-play-btn');
            await sleep(400);
            await page.click('#tts-play-btn');
            await page.mouse.move(vp.width / 2, -40).catch(() => {});
            await sleep(3800);
            const idleBar = await page.evaluate(() => { const cs = getComputedStyle(document.getElementById('voice-control-bar'));
                return { opacity: cs.opacity, transform: cs.transform, active: document.body.classList.contains('player-active'),
                    playing: document.body.classList.contains('lesson-playing'), focus: document.activeElement ? document.activeElement.id || document.activeElement.tagName : null }; });
            const idleCaption = await liftFacts();
            await shot(page, `preview-06-caption-playing-idle-${vp.key}.png`);
            // a pointer move while it plays: the bar comes back and the caption is lifted above it
            await page.mouse.move(vp.width / 2, vp.height / 2);
            await page.mouse.move(vp.width / 2 + 20, vp.height / 2 + 10);
            await sleep(600);
            const moved = await liftFacts();
            const movedBar = await page.evaluate(() => { const cs = getComputedStyle(document.getElementById('voice-control-bar'));
                return { opacity: cs.opacity, transform: cs.transform, active: document.body.classList.contains('player-active') }; });
            await shot(page, `preview-06-caption-playing-pointer-${vp.key}.png`);
            // the keyboard way: Tab onto ▶ (Shift+Tab from ■), Space plays; with the focus there the bar stays, however long the pointer rests
            await showPlayerBar(page);
            await page.click('#tts-play-btn'); // (paused with the mouse first)
            await sleep(400);
            await page.focus('#tts-stop-btn');
            await page.keyboard.press('Shift+Tab');
            const kbFocus = await focus(page);
            await page.keyboard.press('Space');
            await page.mouse.move(vp.width / 2, -40).catch(() => {});
            await sleep(3800);
            const kbBar = await page.evaluate(() => { const cs = getComputedStyle(document.getElementById('voice-control-bar'));
                return { opacity: cs.opacity, playing: document.body.classList.contains('lesson-playing'), focus: document.activeElement ? document.activeElement.id : null,
                    focusVisible: !!document.querySelector('#voice-control-bar :focus-visible') }; });
            await page.evaluate(() => { if (document.activeElement) document.activeElement.blur(); });
            bars.push({ vp: vp.key, idleBar, idleCaption, movedBar, moved, kbFocus: kbFocus.id, kbBar });
            // paused as the user does it (the play button), the pointer then off the page
            await showPlayerBar(page);
            await page.click('#tts-play-btn');
            await page.mouse.move(vp.width / 2, -40).catch(() => {});
            await sleep(900);
            const paused = await liftFacts();
            await shot(page, `preview-06-caption-paused-${vp.key}.png`);
            // a recording (body[data-recording]) never gets the lift, even while paused
            await page.evaluate(() => document.body.setAttribute('data-recording', ''));
            await sleep(500);
            const recording = await liftFacts();
            await page.evaluate(() => document.body.removeAttribute('data-recording'));
            await sleep(500);
            seen.push({ vp: vp.key, playing, paused, recording });
        }
        await page.setViewportSize(VIEW);
        await sleep(600);
        record.captionLift = seen;
        record.playerBar = bars;
        check('preview: while the lesson plays (started with the mouse on Play) the player bar steps aside after 3 s without activity (hidden, moved down, the caption back in its place) and a pointer move brings it back, the caption lifted above it',
            bars.length === 2 && bars.every(b => b.idleBar.playing && +b.idleBar.opacity === 0 && b.idleCaption.ty === -5 && !!b.idleCaption.nameCard && b.idleCaption.nameCard.visible && /matrix\(1, 0, 0, 1, 0, [1-9]/.test(b.idleBar.transform) && !b.idleBar.active
                && +b.movedBar.opacity === 1 && b.movedBar.active && b.moved.ty < -5 && b.moved.trackBottom <= b.moved.barTop + 1),
            bars.map(b => `${b.vp}: after Play (mouse) and 3.8 s idle: opacity ${b.idleBar.opacity} ${b.idleBar.transform}, player-active ${b.idleBar.active}, focus on ${b.idleBar.focus}, caption translateY ${b.idleCaption.ty}, name card ${b.idleCaption.nameCard ? (b.idleCaption.nameCard.visible ? 'shown' : 'hidden') : 'none'}; after a move opacity ${b.movedBar.opacity}, caption translateY ${b.moved.ty}, bottom ${b.moved.trackBottom} vs bar ${b.moved.barTop}`).join(' · '));
        check('a11y: with the keyboard focus on the player bar (Tab onto ▶, Space to play) the bar stays shown while the lesson plays',
            bars.length === 2 && bars.every(b => b.kbFocus === 'tts-play-btn' && b.kbBar.playing && +b.kbBar.opacity === 1 && b.kbBar.focus === 'tts-play-btn' && b.kbBar.focusVisible),
            bars.map(b => `${b.vp}: Shift+Tab → ${b.kbFocus}; after Space and 3.8 s: playing ${b.kbBar.playing}, opacity ${b.kbBar.opacity}, focus ${b.kbBar.focus} (focus-visible ${b.kbBar.focusVisible})`).join(' · '));
        const fmt = f => `caption ${f.trackTop}–${f.trackBottom}, bar from ${f.barTop} (${f.barH}px), translateY ${f.ty}, lift "${f.lift}", drop "${f.drop}", fit "${f.fit}"`;
        check('preview: with the lesson paused the caption sits above the player bar, at 1280×720 and at 820×1180 (the bar on two rows)',
            seen.length === 2 && seen.every(s => s.paused.active && !s.paused.playing && s.paused.text && s.paused.trackBottom <= s.paused.barTop + 1),
            seen.map(s => `${s.vp}: ${fmt(s.paused)}`).join(' · '));
        check('preview: while the lesson plays, and in a recording, the caption keeps its own place (the plain translateY(-5px))',
            seen.every(s => s.playing.playing && s.playing.ty === -5 && s.recording.recording && s.recording.ty === -5),
            seen.map(s => `${s.vp}: playing translateY ${s.playing.ty}, recording translateY ${s.recording.ty}`).join(' · '));
        check('preview: the lift changes no caption fit (--cine-caption-drop / --cine-caption-fit the same playing, paused and recording)',
            seen.every(s => s.paused.drop === s.playing.drop && s.paused.fit === s.playing.fit && s.recording.drop === s.playing.drop && s.recording.fit === s.playing.fit
                && s.paused.bottom === s.playing.bottom),
            seen.map(s => `${s.vp}: drop "${s.playing.drop}"/"${s.paused.drop}"/"${s.recording.drop}", fit "${s.playing.fit}"/"${s.paused.fit}"/"${s.recording.fit}", bottom ${s.playing.bottom}/${s.paused.bottom}`).join(' · '));
        // the lifted caption's words must not cover the presenter's name card either (the reason the caption fit exists)
        const covers = f => !!f.nameCard && f.nameCard.visible && f.lines.some(l => overlaps(l, f.nameCard));
        check('preview: the lifted caption of a paused lesson never covers the presenter\'s name card (the card steps aside or the words stay clear), nor does the playing caption',
            seen.every(s => !covers(s.paused) && !covers(s.playing)),
            seen.map(s => `${s.vp}: name card ${s.paused.nameCard ? `${s.paused.nameCard.t}–${s.paused.nameCard.b} x ${Math.round(s.paused.nameCard.l)}–${Math.round(s.paused.nameCard.r)}${s.paused.nameCard.visible ? '' : ' (hidden while lifted)'}` : 'not shown'}, playing ${s.playing.nameCard && s.playing.nameCard.visible ? 'shown' : 'NOT shown'}; `
                + `paused caption lines ${s.paused.lines.map(l => `${l.t}–${l.b} x ${l.l}–${l.r}`).join(', ')}${covers(s.paused) ? ' COVER IT' : ''}; playing ${covers(s.playing) ? 'COVERS IT' : 'clear'}`).join(' · '));
        await page.evaluate(() => { ttsState.isPlaying = false; updateTTSButtons(); try { speakNarration(null); } catch (e) { /* nothing playing */ } });
    });

    // ==== Navigation from a lesson on the stage ======================================================================================
    await section('navigation from a lesson', async () => {
        phase = 'navigation';
        const reached = [];
        const open = [
            ['nav-create', '.studio-root[data-view="create"]', 'inStudio'], ['open-assets-btn', '.asset-overlay.open', 'inLibrary'],
            ['open-videos-btn', '.export-overlay.open', 'inVideos'], ['nav-settings', '#settings-overlay:not([hidden])', 'inSettings']];
        for (const [id, sel, inside] of open) {
            await page.mouse.move(VIEW.width / 2, 3); // the bar comes back at the top edge
            await sleep(500);
            await page.click(`#${id}`);
            await page.waitForSelector(sel, { timeout: 15000 });
            await sleep(700);
            const f = await focus(page);
            await shot(page, `navigation-from-lesson-${id}.png`);
            if (id === 'open-videos-btn') texts.push({ where: 'export panel (lesson open)', text: await page.evaluate(() => document.querySelector('.export-overlay.open').textContent.replace(/\s+/g, ' ')) });
            await page.keyboard.press('Escape');
            await page.waitForFunction(s => !document.querySelector(s), sel, { timeout: 10000 }).catch(() => {});
            await sleep(500);
            const back = await focus(page);
            reached.push({ id, opened: true, focusIn: f[inside], back: back.id });
        }
        record.navigation = reached;
        check('navigation: Create, Library, Videos and Settings open from the top bar while a lesson is on the stage; the focus goes in and comes back to the button',
            reached.length === 4 && reached.every(r => r.focusIn && r.back === r.id), reached.map(r => `${r.id}: in ${r.focusIn}, back to ${r.back}`).join(' · '));
        // Home while the lesson plays: it stops and the Home view shows
        await page.evaluate(() => { window.__holdScene = false; });
        await showPlayerBar(page);
        await page.click('#tts-play-btn');
        await page.waitForFunction(() => ttsState.isPlaying, null, { timeout: 10000 });
        await sleep(1200);
        const hidden = await page.evaluate(() => getComputedStyle(document.getElementById('app-bar')).transform);
        await page.mouse.move(VIEW.width / 2, 3);
        await sleep(600);
        await page.click('#nav-home');
        await waitView(page, 'home');
        await sleep(1500);
        const s = await facts(page);
        const audio = await page.evaluate(() => !!(window.currentAudio && !window.currentAudio.paused));
        await shot(page, 'navigation-home-from-playing.png');
        check('navigation: while the lesson plays the top bar steps aside; at the top edge it comes back, and Home stops the lesson and shows Your lessons',
            hidden !== 'none' && !s.playing && !audio && !s.active && s.start && s.studio === 'home' && !/project_id/.test(s.url),
            `bar while playing ${hidden}; after Home: playing ${s.playing}, audio ${audio}, stage ${s.active}, start screen ${s.start}, Studio ${s.studio}, url "${s.url}"`);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
        // the start card's "Back to Home"
        await page.goto(`${BASE}/?project_id=${pidJson}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-home-btn');
        await waitView(page, 'home');
        await sleep(600);
        const h = await facts(page);
        check('navigation: the start card\'s "Back to Home" closes the lesson and shows Your lessons', !h.card && h.start && h.studio === 'home' && !/project_id/.test(h.url), JSON.stringify(h));
        await page.keyboard.press('Escape');
    });

    // ==== Export: progress steps, completion, Your videos, chapters ==================================================================
    let exportJob = null;
    await section('export', async () => {
        phase = 'export';
        const EXPORT_ARGS = ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'];
        exportBrowser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'], args: EXPORT_ARGS });
        silent.flags.push('export: ' + EXPORT_ARGS.join(' ') + ' + suppressLocalAudioPlayback');
        const settings = await page.evaluate(() => Object.fromEntries(['aadhi.presenter', 'aadhi.cinematic', 'aadhi_ai_visuals'].map(k => [k, localStorage.getItem(k)]).filter(([, v]) => v !== null)));
        const ex = await newPage(exportBrowser);
        await ex.context.addInitScript(s => { if (!sessionStorage.getItem('copied')) { Object.entries(s).forEach(([k, v]) => localStorage.setItem(k, v)); sessionStorage.setItem('copied', '1'); } }, settings);
        const p = ex.page;
        await p.goto(`${BASE}/?project_id=${pidJson}`);
        await p.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await confirmSilent(p);
        await p.click('#start-studio-btn');
        await lessonView(p);
        await studioIdle(p);
        await stage(p, 'export');
        await shot(p, 'export-01-stage.png');
        await p.click('.studio-root [data-action="export-video"]');
        await p.waitForSelector('.export-overlay.open', { timeout: 15000 });
        await sleep(800);
        const title = await p.evaluate(() => (document.getElementById('export-panel-title') || {}).textContent);
        await shot(p, 'export-02-panel.png');
        await p.evaluate(() => {
            window.__exportSteps = {};
            const log = () => document.querySelectorAll('.export-overlay.open .export-step').forEach(li => {
                const label = (li.querySelector('.export-step-label') || {}).textContent || '?';
                const list = window.__exportSteps[label] || (window.__exportSteps[label] = []);
                const state = `${li.getAttribute('data-state')}:${(li.querySelector('.export-step-state') || {}).textContent || ''}`;
                if (list[list.length - 1] !== state) list.push(state);
            });
            new MutationObserver(log).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-state'] });
        });
        await p.click('.export-overlay.open .export-start-btn');
        const limit = 420000;
        for (;;) {
            const start = p.locator('.export-actions button', { hasText: 'Start Recording' });
            const anyway = p.locator('.export-actions button', { hasText: 'Record anyway' });
            await Promise.race([start.waitFor({ timeout: limit }), anyway.waitFor({ timeout: limit })]);
            if (await anyway.isVisible()) { note('export asked first: ' + norm(await p.textContent('.export-message').catch(() => '')).slice(0, 200)); await anyway.click(); continue; }
            await sleep(300);
            await shot(p, 'export-03-progress.png');
            await start.click();
            break;
        }
        const end = Date.now() + limit;
        let outcome = null;
        let midShot = false;
        while (!outcome && Date.now() < end) {
            await sleep(1000);
            const s = await p.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden,
                error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
            if (s.ready) outcome = 'ready';
            else if (s.error) outcome = 'error: ' + s.error;
        }
        if (!midShot) midShot = true;
        await sleep(2500); // the history refreshes after the upload
        const job = await p.evaluate(() => exportFlow.job);
        exportJob = job && job.id;
        const steps = await p.evaluate(() => window.__exportSteps);
        const ready = await p.evaluate(() => ({ title: (document.querySelector('.export-ready-title') || {}).textContent, meta: (document.querySelector('.export-ready-meta') || {}).textContent,
            history: [...document.querySelectorAll('.export-history-item')].map(li => ({ status: li.getAttribute('data-status'), title: (li.querySelector('.export-history-title') || {}).textContent })),
            panelTitle: (document.getElementById('export-panel-title') || {}).textContent, text: document.querySelector('.export-overlay.open').textContent.replace(/\s+/g, ' ') }));
        await shot(p, 'export-04-done.png');
        await p.locator('.export-history').scrollIntoViewIfNeeded().catch(() => {});
        await shot(p, 'export-05-your-videos.png');
        texts.push({ where: 'export panel (done)', text: ready.text });
        record.export = { outcome, steps, ready: { ...ready, text: undefined } };
        const labels = Object.keys(steps || {});
        check('export: the progress is shown as steps, each going from not started to in progress to done (in words for screen readers)',
            labels.length >= 3 && labels.every(l => /^done:/.test(steps[l][steps[l].length - 1])) && labels.every(l => steps[l].some(x => /^active:in progress$/.test(x)) || steps[l].length >= 2),
            labels.map(l => `${l}: ${steps[l].map(x => x.split(':')[0]).join('→')}`).join(' · '));
        check('export: the export completes with the video ready (said with an icon and words, with its facts)', outcome === 'ready' && /^✓ Export complete$/.test(norm(ready.title))
            && /WebM video · 720p · \d+:\d\d/.test(norm(ready.meta)), `${outcome}; "${ready.title}" ${norm(ready.meta)}`);
        check('export: the panel is "Your videos" and lists the new video', title === 'Your videos' && ready.panelTitle === 'Your videos'
            && ready.history.some(h => /Levers and torque|Physics/.test(h.title || '') && /ready|done|completed/i.test(h.status || '')),
            `title "${title}"; ${ready.history.map(h => `${h.status}: ${h.title}`).join(' | ')}`);
        let chapters = { status: null, text: '' };
        if (exportJob && outcome === 'ready') {
            for (let k = 0; k < 30 && chapters.status !== 200; k++) { chapters = await apiText(`/api/exports/${exportJob}/outputs/chapters`); if (chapters.status !== 200) await sleep(1000); }
        }
        fs.writeFileSync(path.join(OUT, 'export-chapters.txt'), chapters.text || '');
        const lines = (chapters.text || '').split(/\r?\n/).filter(Boolean);
        const intro = lines.filter(l => /^\d+:\d+(:\d+)? Introduction$/.test(l));
        check('export: chapters: the opening is "Opening" (a scene is titled "Introduction"), so "Introduction" appears once',
            /^00:00 Opening$/.test(lines[0] || '') && intro.length === 1 && lines.length === LESSON.scenes.length + 1, lines.join(' | '));
        await ex.context.close();
    });

    // ==== Library: the shared Aadhi assets first, narration hidden by default, no asset ids ===========================================
    await section('library', async () => {
        phase = 'library';
        await page.goto(BASE + '/');
        await page.waitForSelector('#open-assets-btn', { state: 'visible', timeout: 30000 });
        await page.click('#open-assets-btn');
        await page.waitForSelector('.asset-overlay.open .asset-item', { timeout: 30000 });
        await sleep(800);
        const f = await focus(page);
        const lib = await page.evaluate(() => {
            const o = document.querySelector('.asset-overlay.open');
            return { groups: [...o.querySelectorAll('.asset-group-head')].map(g => `${g.dataset.group}:${g.textContent.trim()}`),
                type: o.querySelector('.asset-filter[aria-label="Type"]').value, typeLabel: o.querySelector('.asset-filter[aria-label="Type"]').selectedOptions[0].textContent,
                badges: [...o.querySelectorAll('.asset-item .asset-badge')].map(b => b.textContent.trim()), first: (o.querySelector('.asset-item') || {}).dataset,
                text: o.innerText };
        });
        await shot(page, 'library-01.png');
        // the first of your own files, opened
        const own = await page.$('.asset-overlay.open .asset-item[data-scope="user"], .asset-overlay.open .asset-item:not([data-scope="system"])');
        if (own) { await own.click(); await sleep(600); }
        const detail = await page.evaluate(() => { const d = document.querySelector('.asset-overlay.open .asset-detail'); return { text: d ? d.innerText : '', copy: [...document.querySelectorAll('.asset-overlay.open button')].some(b => /Copy asset ID/.test(b.textContent)) }; });
        await shot(page, 'library-02-detail.png');
        await page.selectOption('.asset-overlay.open .asset-filter[aria-label="Type"]', '');
        await page.waitForFunction(() => !document.querySelector('.asset-overlay.open[aria-busy="true"]'), null, { timeout: 15000 }).catch(() => {});
        await sleep(1500);
        const all = await page.evaluate(() => [...document.querySelectorAll('.asset-overlay.open .asset-item .asset-badge')].map(b => b.textContent.trim()));
        await shot(page, 'library-03-all-files.png');
        texts.push({ where: 'library', text: lib.text });
        check('library: Aadhi\'s shared assets are the first group, then your files',
            /^system:Shared Aadhi assets \(\d+\)$/.test(lib.groups[0] || '') && /^mine:Your files/.test(lib.groups[1] || '') && lib.first && lib.first.scope === 'system', lib.groups.join(' · '));
        check('library: narration is hidden by default ("All except narration"); "All files" shows the lesson\'s narration clips',
            lib.type === 'no-narration' && lib.typeLabel === 'All except narration' && !lib.badges.includes('Narration') && all.includes('Narration'),
            `filter "${lib.typeLabel}": ${[...new Set(lib.badges)].join(', ')}; all files: ${[...new Set(all)].join(', ')}`);
        check('library: no asset ids outside the debug view (list and detail; no "Copy asset ID")', !HEX_ID.test(lib.text) && !HEX_ID.test(detail.text) && !detail.copy && !!own,
            `detail "${norm(detail.text).slice(0, 120)}"; copy ${detail.copy}`);
        check('a11y: the Library takes the focus when it opens', f.inLibrary, f.el);
        const trapped = await tabStaysIn(page, x => x.inLibrary, 25);
        check('a11y: Tab and Shift+Tab stay inside the Library', trapped.length === 0, trapped.slice(0, 4).join(', ') || '25 stops inside');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.asset-overlay.open'), null, { timeout: 10000 });
        const back = await focus(page);
        check('a11y: Esc closes the Library and the focus returns to the Library button', back.id === 'open-assets-btn', back.el);
        // the debug view shows the ids (for developers)
        const dbg = await newPage(browser);
        await dbg.page.goto(BASE + '/?visualDebug=1');
        await dbg.page.waitForSelector('#open-assets-btn', { state: 'visible', timeout: 30000 });
        await dbg.page.click('#open-assets-btn');
        await dbg.page.waitForSelector('.asset-overlay.open .asset-item', { timeout: 30000 });
        await dbg.page.click('.asset-overlay.open .asset-item:not([data-scope="system"]) >> nth=0');
        await sleep(600);
        const dbgCopy = await dbg.page.evaluate(() => [...document.querySelectorAll('.asset-overlay.open button')].some(b => /Copy asset ID/.test(b.textContent)));
        await dbg.context.close();
        check('library: with ?visualDebug the asset id can be copied (the technical detail is only there)', dbgCopy, `Copy asset ID ${dbgCopy}`);
    });

    // ==== Responsive: every screen at every viewport ===================================================================================
    await section('responsive', async () => {
        phase = 'responsive';
        const r = { hScroll: [], unnamed: new Set(), editor: [], bar: [], blocked: [], errors: [] };
        const rp = await newPage(browser, { viewport: VIEWPORTS[0] });
        const p = rp.page;
        // a real click; when it cannot be made (the control hidden or covered at this size) that is recorded, and the control is
        // pressed through the page so the remaining screens of this viewport are still seen
        let vpNow = null;
        const press = async (sel, wait = 5000) => {
            try { await p.click(sel, { timeout: wait }); } catch (e) {
                r.blocked.push(`${vpNow}: ${sel}`);
                await p.evaluate(s => { const el = document.querySelector(s); if (el) el.click(); }, sel);
            }
        };
        const measure = async (vp, screen) => {
            await shot(p, `responsive-${vp.key}-${screen}.png`);
            const l = await p.evaluate(layoutFacts);
            if (l.hScroll) r.hScroll.push(`${vp.key} ${screen}: ${l.scrollW}px (${l.offenders.join(', ')})`);
            (await p.evaluate(unnamedIconButtons)).forEach(x => r.unnamed.add(`${screen}: ${x}`));
        };
        for (const vp of VIEWPORTS) {
          vpNow = vp.key;
          try {
            await p.setViewportSize({ width: vp.width, height: vp.height });
            await p.goto(BASE + '/');
            await p.waitForSelector('#start-create-btn', { state: 'visible', timeout: 30000 });
            await confirmSilent(p);
            await sleep(700);
            await measure(vp, '01-start');
            await press('#nav-settings');
            await p.waitForSelector('#settings-overlay:not([hidden])');
            await sleep(400);
            await measure(vp, '02-settings');
            await p.keyboard.press('Escape');
            await press('#open-studio-btn');
            await waitView(p, 'home');
            await until(async () => (await sui(p)).lessons.length > 0, 15000);
            await sleep(300);
            await measure(vp, '03-studio-home');
            await press('.studio-root [data-action="create"]');
            await waitView(p, 'create');
            await sleep(300);
            await measure(vp, '04-studio-create');
            await p.keyboard.press('Escape');
            await p.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
            await startPlaying(p, pidJson);
            await p.evaluate(n => { currentSlide = n; renderSlide(n); }, AT.twoLine);
            await sleep(800);
            await showPlayerBar(p);
            const bar = await p.evaluate(() => ['tts-play-btn', 'tts-stop-btn', 'review-btn', 'editor-btn', 'studio-btn', 'auto-export-btn', 'tts-mute-btn', 'player-more-btn'].map(id => {
                const b = document.getElementById(id); const rc = b.getBoundingClientRect(); const cs = getComputedStyle(b);
                return { id, on: rc.width > 0 && rc.height > 0 && rc.left >= -1 && rc.right <= innerWidth + 1 && rc.top >= -1 && rc.bottom <= innerHeight + 1 && cs.visibility !== 'hidden' && cs.display !== 'none' };
            }));
            const off = bar.filter(b => !b.on).map(b => b.id);
            r.bar.push({ vp: vp.key, off });
            await measure(vp, '05-lesson-player-bar');
            await press('#player-more-btn');
            await sleep(300);
            await measure(vp, '06-player-more-menu');
            await p.keyboard.press('Escape');
            await press('#studio-btn');
            await lessonView(p);
            await studioIdle(p);
            await measure(vp, '07-studio-stages');
            await stage(p, 'style');
            await sleep(500);
            await measure(vp, '08-studio-style');
            await press('.studio-root [data-action="close"]');
            await p.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
            await showPlayerBar(p);
            await press('#editor-btn');
            await p.waitForSelector('.editor-root .editor-scene', { timeout: 30000 });
            await sleep(1500);
            await measure(vp, '09-editor');
            const boxes = await p.evaluate(rectsOf, { transport: '.editor-root .editor-tl-bar', tracks: '.editor-root .editor-tl-row', inspector: '.editor-root .editor-inspector',
                top: '.editor-root .editor-top', scenes: '.editor-root .editor-scenes', buttons: '.editor-root .editor-tl-bar button, .editor-root .editor-top button' });
            const clash = [];
            const ov = (a, b) => Math.min(a.r, b.r) - Math.max(a.l, b.l) > 2 && Math.min(a.b, b.b) - Math.max(a.t, b.t) > 2;
            boxes.transport.forEach(t => { boxes.tracks.forEach((k, i) => { if (ov(t, k)) clash.push(`transport × track ${i}`); }); boxes.inspector.forEach(ins => { if (ov(t, ins)) clash.push('transport × inspector'); }); });
            boxes.tracks.forEach((k, i) => boxes.inspector.forEach(ins => { if (ov(k, ins)) clash.push(`track ${i} × inspector`); }));
            boxes.top.forEach(t => boxes.inspector.concat(boxes.transport).forEach(o => { if (ov(t, o)) clash.push('toolbar × panel'); }));
            const offButtons = boxes.buttons.filter(b => b.r > boxes.vw + 1 || b.l < -1).length;
            r.editor.push({ vp: vp.key, transport: boxes.transport.length, tracks: boxes.tracks.length, inspector: boxes.inspector.length, clash, offButtons });
            if (vp.key === '1024' || vp.key === '820') {
                // the inspector closed: every track back, still nothing overlapping
                const toggle = await p.$('.editor-root [data-action="toggle-inspector"]');
                if (toggle) {
                    await toggle.click();
                    await sleep(600);
                    await measure(vp, '10-editor-inspector-toggled');
                    const b2 = await p.evaluate(rectsOf, { transport: '.editor-root .editor-tl-bar', tracks: '.editor-root .editor-tl-row', inspector: '.editor-root .editor-inspector' });
                    const c2 = [];
                    b2.transport.forEach(t => b2.tracks.forEach((k, i) => { if (ov(t, k)) c2.push(`transport × track ${i}`); }));
                    b2.tracks.forEach((k, i) => b2.inspector.forEach(ins => { if (ov(k, ins)) c2.push(`track ${i} × inspector`); }));
                    b2.transport.forEach(t => b2.inspector.forEach(ins => { if (ov(t, ins)) c2.push('transport × inspector'); }));
                    r.editor.push({ vp: vp.key + ' (inspector toggled)', transport: b2.transport.length, tracks: b2.tracks.length, inspector: b2.inspector.length, clash: c2, offButtons: 0 });
                    await toggle.click().catch(() => {});
                    await sleep(400);
                }
            }
            await press('.editor-root [data-action="close"]');
            await p.waitForFunction(() => !document.querySelector('.editor-root'), null, { timeout: 10000 });
            await p.mouse.move(vp.width / 2, 3);
            await sleep(400);
            await press('#open-videos-btn');
            await p.waitForSelector('.export-overlay.open', { timeout: 15000 });
            await sleep(600);
            await measure(vp, '11-videos');
            await p.keyboard.press('Escape');
            await sleep(400);
            await p.mouse.move(vp.width / 2, 3);
            await sleep(400);
            await press('#open-assets-btn');
            await p.waitForSelector('.asset-overlay.open .asset-item', { timeout: 30000 });
            await sleep(600);
            await measure(vp, '12-library');
            await p.keyboard.press('Escape');
            await sleep(300);
          } catch (e) {
            r.errors.push(`${vp.key}: ${(e.message || String(e)).split(/\r?\n/)[0]}`);
            await shot(p, `responsive-${vp.key}-error.png`);
          }
        }
        await rp.context.close();
        record.responsive = { ...r, unnamed: [...r.unnamed] };
        check('responsive: no horizontal scroll on any screen at 1440×900, 1280×720, 1024×768, 820×1180 and 600×900', r.hScroll.length === 0, r.hScroll.join(' | ') || '5 viewports × 12 screens');
        const tablet = r.editor.filter(e => /^(1024|820)/.test(e.vp));
        check('responsive: in the editor at 1024×768 and 820×1180 the transport row, the timeline tracks and the inspector never overlap',
            tablet.length >= 2 && tablet.every(e => e.transport === 1 && e.tracks >= 1 && e.clash.length === 0),
            tablet.map(e => `${e.vp}: transport ${e.transport}, tracks ${e.tracks}, inspector ${e.inspector}${e.clash.length ? ' CLASH ' + e.clash.join(', ') : ''}`).join(' · '));
        check('responsive: the editor\'s controls stay on screen at every viewport', r.editor.every(e => e.offButtons === 0), r.editor.map(e => `${e.vp}: ${e.offButtons} off`).join(' · '));
        check('responsive: the player bar\'s essential controls (play, stop, review, edit, Studio, export, mute, more) are on screen at every viewport',
            r.bar.every(b => b.off.length === 0), r.bar.map(b => `${b.vp}: ${b.off.length ? 'OFF ' + b.off.join(',') : 'all on screen'}`).join(' · '));
        check('a11y: every icon-only button on these screens has an accessible name', r.unnamed.size === 0, [...r.unnamed].slice(0, 8).join(' | ') || 'none unnamed');
        check('responsive: every screen was reached with real clicks at every viewport (nothing hidden, covered or cut off)', r.blocked.length === 0 && r.errors.length === 0,
            [...r.blocked, ...r.errors].join(' | ') || `${VIEWPORTS.length} viewports`);
    });

    // ==== Accessibility: the More menu, status regions, reduced motion =================================================================
    await section('a11y: menu, status regions, reduced motion', async () => {
        phase = 'a11y';
        await startPlaying(page, pidJson);
        await showPlayerBar(page);
        await page.focus('#player-more-btn');
        await page.keyboard.press('Enter');
        await sleep(300);
        const inMenu = await focus(page);
        const items = await page.evaluate(() => [...document.querySelectorAll('#player-more-menu [role="menuitem"]')].filter(i => !i.hidden).map(i => i.id || i.textContent.trim()));
        await page.keyboard.press('ArrowDown');
        const second = await focus(page);
        await page.keyboard.press('Escape');
        await sleep(200);
        const back = await focus(page);
        check('a11y: the player bar\'s More menu (practice sheet, reload the scene, record the screen) opens with the keyboard on its first item, arrows move, Esc returns to its button',
            inMenu.inMenu && second.inMenu && second.id !== inMenu.id && back.id === 'player-more-btn' && ['download-companion-btn', 'reload-slide-btn', 'record-btn'].every(id => items.includes(id)),
            `opened on ${inMenu.el}, ↓ ${second.el}, Esc → ${back.el}; items ${items.join(', ')}`);
        const regions = await page.evaluate(() => ({ notice: document.getElementById('app-notice').getAttribute('aria-live'), loading: document.getElementById('loading-overlay').getAttribute('role'),
            providers: document.getElementById('ai-providers-body').getAttribute('aria-live') }));
        await page.click('#studio-btn');
        await lessonView(page);
        const studioStatus = await page.evaluate(() => { const s = document.querySelector('.studio-root .studio-status'); return s ? `${s.getAttribute('role')}/${s.getAttribute('aria-live')}` : null; });
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.studio-root'), null, { timeout: 10000 });
        await showPlayerBar(page);
        await page.click('#editor-btn');
        await page.waitForSelector('.editor-root .editor-scene', { timeout: 30000 });
        await sleep(1000);
        const editorStatus = await page.evaluate(() => { const s = document.querySelector('.editor-root .editor-save'); return s ? `${s.getAttribute('role')}/${s.getAttribute('aria-live')}` : null; });
        const editorFocus = await focus(page);
        const editorTrap = await tabStaysIn(page, x => x.inEditor, 30);
        await page.click('.editor-root [data-action="close"]');
        await page.waitForFunction(() => !document.querySelector('.editor-root'), null, { timeout: 10000 });
        await sleep(500);
        const editorBack = await focus(page);
        check('a11y: the editor (from the player bar) takes the focus, and closed, gives it back to the button that opened it',
            editorFocus.inEditor && editorBack.id === 'editor-btn', `in ${editorFocus.el}; back to ${editorBack.el}`);
        await page.mouse.move(VIEW.width / 2, 3);
        await sleep(400);
        await page.click('#open-videos-btn');
        await page.waitForSelector('.export-overlay.open', { timeout: 15000 });
        const exportStatus = await page.evaluate(() => [...document.querySelectorAll('.export-overlay.open [role="status"], .export-overlay.open [aria-live]')].length);
        const videosTrap = await tabStaysIn(page, x => x.inVideos, 20);
        await page.keyboard.press('Escape');
        check('a11y: status regions are present (notices, loading, the Studio\'s status, the editor\'s save state, the export panel, the AI services list)',
            regions.notice === 'polite' && regions.loading === 'status' && regions.providers === 'polite' && studioStatus === 'status/polite' && editorStatus === 'status/polite' && exportStatus > 0,
            `${JSON.stringify(regions)}; Studio ${studioStatus}; editor ${editorStatus}; export panel ${exportStatus} region(s)`);
        check('a11y: Tab and Shift+Tab stay inside the editor and the export panel', editorTrap.length === 0 && videosTrap.length === 0,
            `editor: ${editorTrap.slice(0, 3).join(', ') || 'inside'}; export panel: ${videosTrap.slice(0, 3).join(', ') || 'inside'}`);
        // reduced motion
        const rm = await newPage(browser, { reducedMotion: 'reduce' });
        const p = rm.page;
        const running = {};
        await p.goto(BASE + '/');
        await p.waitForSelector('#start-create-btn', { state: 'visible', timeout: 30000 });
        await confirmSilent(p);
        await sleep(2500);
        running.start = await p.evaluate(runningUiAnimations);
        await p.click('#open-studio-btn');
        await waitView(p, 'home');
        await sleep(1500);
        running.studio = await p.evaluate(runningUiAnimations);
        await p.keyboard.press('Escape');
        await p.click('#nav-settings');
        await sleep(1200);
        running.settings = await p.evaluate(runningUiAnimations);
        await p.keyboard.press('Escape');
        await p.click('#open-assets-btn');
        await sleep(1500);
        running.library = await p.evaluate(runningUiAnimations);
        await p.keyboard.press('Escape');
        await startPlaying(p, pidJson);
        await showPlayerBar(p);
        await sleep(1200);
        running.lesson = await p.evaluate(runningUiAnimations);
        record.reducedMotionStage = [...new Set(await p.evaluate(runningUiAnimations, true))];
        note('the stage under reduced motion (its own rules; it is what the video records): ' + (record.reducedMotionStage.join(', ') || 'nothing running'));
        await shot(p, 'a11y-reduced-motion-lesson.png');
        await rm.context.close();
        record.reducedMotion = running;
        const any = Object.entries(running).filter(([, v]) => v.length);
        check('a11y: with reduced motion no product-UI animation is running (start screen, Studio, Settings, Library, a lesson with its player bar)',
            any.length === 0, any.map(([k, v]) => `${k}: ${v.slice(0, 5).join(', ')}`).join(' | ') || 'none running');
    });

    // ==== Everywhere ==================================================================================================================
    phase = 'end';
    const leaks = texts.filter(t => PROVIDER_WORDS.test(t.text)).map(t => `${t.where}: "${(t.text.match(PROVIDER_WORDS) || [])[0]}"`);
    check('no AI provider or model named in the product UI outside the debug view (Studio, editor, Visual Review, export panel, Library)', leaks.length === 0,
        leaks.join(' | ') || `${texts.length} screens read`);
    check('no page errors, console errors or failed requests', problems.length === 0, problems.slice(0, 8).join(' | '));
    check('no browser dialog (alert / confirm / prompt) was shown', dialogs.length === 0, JSON.stringify(dialogs.slice(0, 4)));
    check('silent: every page muted (no audio output device, speech stubbed)', silent.stubbed >= 5 && silent.flags.every(f => /--disable-audio-output/.test(f)),
        `${silent.stubbed} page loads confirmed stubbed; ${silent.flags.join(' ; ')}`);
} catch (err) {
    check('the check ran to the end', false, (err.stack || String(err)).split('\n').slice(0, 4).join(' | '));
} finally {
    if (browser) await browser.close().catch(() => {});
    if (exportBrowser) await exportBrowser.close().catch(() => {});
    servers.forEach(killServer);
}

fs.writeFileSync(path.join(OUT, 'phase21-check.json'), JSON.stringify({ results, record, problems, dialogs }, null, 2));
const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed in ${Math.round((Date.now() - STARTED) / 1000)}s. Screenshots and logs: ${OUT}`);
process.exit(failed ? 1 : 0);
