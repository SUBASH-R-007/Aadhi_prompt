// Presenter + Visual Synchronization check (Phase 16) in real Chrome against a throwaway server with the local stand-ins
// only (AI_FAKE_PROVIDER=1; FAKE_TTS=1: a speech-like tone as long as the text). No real provider, no network.
//
//   1. open a lesson   2. start playback (the server audio, in preview too)   3. the presenter speaks
//   4. the visual is highlighted when the narration points at it   5. labels appear when their meaning is spoken
//   6. the camera moves at the key moment   7. text appears at the right moment (the code's output, a timeline's dates)
//   8. the presenter changes what it does (points, then explains)   9. the scene ends cleanly (everything fired, the
//   result held)   10. a different speech rate keeps the same moments   11. regenerating the narration recompiles the
//   timing (nothing generated)   12. a removed visual leaves no orphan events   13. Visual Review shows the
//   synchronization   14. the export plays the same plan (the same moments, a frame that matches)   15. a refresh keeps
//   the plans. Every fired event is logged with the narration position it fired at: its error against the planned
//   position is measured in seconds of narration.
//
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/sync_browser_check.mjs
// Silent: no audio output, browser speech stubbed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.SYNC_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-sync-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.SYNC_CHECK_PORT || 9960 + (process.pid % 8));
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
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `asc-${process.pid}`), JWT_SECRET: 'sync-check-' + Math.random().toString(36).slice(2),
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

// The representative lesson: definition, formula, process, comparison, code, a diagram-heavy explanation, a timeline,
// a summary, an introduction and a quiz; the leaf concept comes back (continuity)
const LESSON = { subject_name: 'Sync check', session_title: 'How plants and programs work',
    concept_map: [{ id: 'c1', title: 'Photosynthesis', depends_on: [] }, { id: 'c2', title: 'The leaf', depends_on: ['c1'] },
        { id: 'c3', title: "Newton's second law", depends_on: [] }, { id: 'c4', title: 'Loops', depends_on: [] },
        { id: 'c5', title: 'History of computing', depends_on: [] }],
    scenes: [
    { type: 'content', concept_id: 'c1', title: 'Photosynthesis', subtitle: 'How plants make their own food', html: '<p>Plants turn sunlight, water and air into sugar.</p>',
      narration: 'Welcome! Today we find out how plants make their own food.' },
    { type: 'content', concept_id: 'c1', title: 'What is photosynthesis?',
      html: "<div class='definition'><span class='keyword'>Photosynthesis</span> is the process by which green plants use sunlight to make glucose from carbon dioxide and water.</div>",
      visual: { concept: 'leaf cross section', description: 'Layers of a leaf where photosynthesis happens', type: 'diagram', keywords: ['leaf', 'cross', 'section', 'chloroplast'] },
      narration: 'Here is the definition. [SYNC] Photosynthesis is the process by which green plants make glucose.' },
    { type: 'content', concept_id: 'c1', title: 'How photosynthesis happens',
      html: '<ol><li>Chlorophyll absorbs sunlight</li><li>Water is split into hydrogen and oxygen</li><li>Carbon dioxide is taken in</li><li>Glucose is made</li></ol>',
      narration: 'It happens in four steps. First, [SYNC] chlorophyll absorbs sunlight. Then [SYNC] water is split. Next, [SYNC] carbon dioxide is taken in. Finally [SYNC] glucose is made.' },
    { type: 'content', concept_id: 'c2', title: 'Inside the leaf', html: "<ul><li><span class='keyword'>Palisade cells</span></li><li><span class='keyword'>Stoma</span></li></ul>",
      visual: { concept: 'leaf cross section', description: 'Leaf cross section with palisade cells and stoma', type: 'diagram', keywords: ['leaf', 'cross', 'section', 'palisade'] },
      narration: 'Look at this diagram of a leaf. [SYNC] The palisade cells catch the light. [SYNC] The stoma lets air in.' },
    { type: 'content', concept_id: 'c2', title: 'Inside the leaf (Contd.)', html: "<ul><li><span class='keyword'>Chloroplasts</span> hold the chlorophyll</li><li>The <span class='keyword'>spongy layer</span> lets gases move</li></ul>",
      visual: { concept: 'leaf cross section', description: 'Leaf cross section, the inner layers', type: 'diagram', keywords: ['leaf', 'cross', 'section', 'chloroplast'] },
      narration: 'Look again at the leaf. [SYNC] Chloroplasts hold the chlorophyll. [SYNC] The spongy layer lets gases move.' },
    { type: 'content', concept_id: 'c3', title: "Newton's second law", html: "<div class='formula-block'>\\[F = ma\\]</div><p>Force = Mass × Acceleration</p>",
      narration: 'Here is the law. [SYNC] F equals m times a. [SYNC] Force is mass times acceleration.' },
    { type: 'content', concept_id: 'c4', title: 'Python for loop', html: "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre><p>It prints the numbers 0 to 4, one per line.</p>",
      narration: 'Here is a loop. It prints the numbers zero to four.' },
    { type: 'content', title: 'Plants vs Animals', html: '<table><thead><tr><th>Plants</th><th>Animals</th></tr></thead><tbody><tr><td>Make their own food</td><td>Eat other living things</td></tr><tr><td>Release oxygen</td><td>Release carbon dioxide</td></tr><tr><td>Cannot move around</td><td>Move to find food</td></tr></tbody></table>',
      narration: 'Let us compare plants and animals side by side.' },
    { type: 'content', concept_id: 'c5', title: 'History of computing',
      html: '<ul><li>1837: Babbage designs the Analytical Engine</li><li>1946: ENIAC is switched on</li><li>1971: the first microprocessor</li><li>2007: the smartphone era begins</li></ul>',
      narration: 'Let us travel through time. In 1837, [SYNC] Babbage designs an engine. In 1946, [SYNC] ENIAC is switched on. In 1971, [SYNC] the microprocessor arrives. And in 2007, [SYNC] the smartphone era begins.' },
    { type: 'quiz_checkpoint', title: 'Quick check', question: 'What does a plant make in photosynthesis?', options: ['Glucose', 'Salt', 'Iron'], correct_index: 0,
      explanation: 'Plants make glucose from light, water and carbon dioxide.', countdown_seconds: 2, narration: 'Quick question for you.', reveal_narration: 'Glucose!' },
    { type: 'key-takeaway', title: 'Key takeaways', html: "<ul class='takeaway-list'><li>Plants make glucose from sunlight</li><li>Chlorophyll captures the light</li><li>Loops repeat code</li><li>F = ma links force and motion</li></ul>",
      narration: 'Let us recap. [SYNC] Plants make glucose. [SYNC] Chlorophyll captures light. [SYNC] Loops repeat code. [SYNC] Force is mass times acceleration.' }
] };
const LABELS = ['1-intro', '2-definition', '3-process', '4-diagram', '5-diagram-contd', '6-formula', '7-code', '8-comparison', '9-timeline', '10-quiz', '11-summary'];
const AT = Object.fromEntries(LABELS.map((l, i) => [l, i]));

// Logs every synchronization event when it fires, with the narration position the stage was at (page side)
function syncProbe() {
    window.__syncLog = [];
    const seen = new WeakSet();
    const loop = () => {
        try {
            // eslint-disable-next-line no-undef
            const st = typeof cinematicStage !== 'undefined' ? cinematicStage : null;
            if (st && st.syncEvents) {
                st.syncEvents.forEach(r => {
                    if (!r.done || seen.has(r)) return;
                    seen.add(r);
                    const svg = document.querySelector('#presenter-layer svg');
                    window.__syncLog.push({ scene: typeof currentSlide !== 'undefined' ? currentSlide : -1, id: r.id, type: r.ev.type, target: r.ev.target,
                        planned: r.pos, tolerance: r.tolerance, pos: st.narrationPosition(), clock: st.narration && st.narration.mode,
                        exporting: typeof isAutoExporting !== 'undefined' && !!isAutoExporting,
                        rate: (st.narration && st.narration.audio && st.narration.audio.playbackRate) || 1,
                        gesture: svg ? svg.getAttribute('data-gesture') : null, t: performance.now() });
                });
            }
        } catch (e) { /* the page is still loading */ }
        requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
}
// Error of a fired event against its plan, in seconds of narration (null when it cannot be measured)
function errorOf(e) {
    if (!e.planned || !e.pos || e.clock !== 'audio') return null;
    if (e.pos.segment !== e.planned.segment) return e.pos.segment > e.planned.segment ? Infinity : -Infinity;
    return (e.pos.ratio - e.planned.ratio) * e.pos.duration;
}
// on time: never noticeably before its word (a frame's lead plus measuring slack), and no later than its tolerance
const onTime = e => { const err = errorOf(e); return err !== null && err >= -0.15 && err <= e.tolerance + 0.12; };
const fmt = e => { const err = errorOf(e); return err === null ? '-' : (Number.isFinite(err) ? err.toFixed(2) : String(err)); };

const problems = [];
let browser;
let audioRequests = 0;
try {
    spawnSync(PYTHON, [path.join(REPO, 'tests', 'fixtures', 'cinematic_diagrams.py'), OUT]);
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    await upload(path.join(OUT, 'leaf_cross_section.png'), 'Leaf cross section showing palisade cells, spongy layer and stoma', ['leaf', 'cross', 'section', 'palisade', 'chloroplast']);
    await upload(path.join(OUT, 'photosynthesis_diagram.png'), 'Photosynthesis plant diagram with sunlight, water, carbon dioxide and oxygen', ['photosynthesis', 'plant', 'diagram', 'sunlight']);
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
        await context.addInitScript(syncProbe);
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error: ${e.message}`));
        page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|Logo video play error|WebSocket connection/.test(m.text())) problems.push(`console: ${m.text()}`); });
        page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
        page.on('request', r => { if (r.url().includes('/generate-audio')) audioRequests += 1; });
        return { context, page };
    }
    async function startLesson(page, projectId) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => slides.every(s => s.cinematic_plan), null, { timeout: 30000 });
        await sleep(1200);
    }
    // Plays one scene with its narration to the end (the next scene is not started)
    // Plays one scene with its narration to the end; with `moments`, a screenshot the instant each new event fires (QA frames)
    async function playScene(page, index, moments = null) {
        await page.evaluate(n => { window.checkAndAdvanceSlide = () => {}; ttsState.isPlaying = true; currentSlide = n; window.__syncLog.length = 0; renderSlide(n); }, index);
        await sleep(300);
        if (moments) {
            const end = Date.now() + 40000;
            let shot = 0;
            for (;;) {
                const st = await page.evaluate(() => ({ n: window.__syncLog.length, last: window.__syncLog[window.__syncLog.length - 1],
                    done: !!(window.slideSyncState && window.slideSyncState.audioFinished) }));
                if (st.n > shot) {
                    shot = st.n;
                    await sleep(250); // the glow / chip / camera has started
                    await page.screenshot({ path: path.join(OUT, `moment-${moments}-${shot}-${st.last.type}.png`) });
                }
                if (st.done || Date.now() > end) break;
                await sleep(60);
            }
        }
        await page.waitForFunction(() => window.slideSyncState && window.slideSyncState.audioFinished, null, { timeout: 40000 }).catch(() => {});
        await sleep(400);
        return page.evaluate(() => ({ log: window.__syncLog.slice(), state: cinematicStage.state(), endHold: cinematicStage.endHold() }));
    }
    const cine = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'auto', typography: 'academic', composer: 'rules', director: 'rules' };
    const { page } = await newPage({ width: 1280, height: 720 }, cine);
    await startLesson(page, pid);

    // ---- 1-2. the lesson opens; every narrated content scene has a synchronization plan; preview uses the server audio ----------
    const plans = await page.evaluate(() => slides.map(s => s.cinematic_plan && s.cinematic_plan.sync ? { events: s.cinematic_plan.sync.events.length,
        fingerprint: s.cinematic_plan.sync.fingerprint, source: s.cinematic_plan.sync.timing_source } : null));
    check('1. the lesson opens and every narrated content scene has a synchronization plan (the quiz runs its own flow)',
        plans.every((p, i) => i === AT['10-quiz'] ? p === null : p && p.source === 'narration'), plans.map(p => (p ? p.events : '-')).join(','));
    const seen = {};
    for (const label of ['2-definition', '4-diagram', '6-formula', '7-code', '8-comparison', '9-timeline', '11-summary']) {
        seen[label] = await playScene(page, AT[label], label);
        await page.screenshot({ path: path.join(OUT, `720-${label}.png`) });
    }
    fs.writeFileSync(path.join(OUT, 'preview-log.json'), JSON.stringify(seen, null, 2));
    check('2. preview plays the server audio for synchronized scenes (the voice the export records)', audioRequests > 0, `${audioRequests} audio requests`);
    const all = Object.values(seen).flatMap(s => s.log);
    const measured = all.filter(e => errorOf(e) !== null);

    // ---- 3-9: what happened, and when ------------------------------------------------------------------------------------------
    const speaking = await page.evaluate(async () => {
        renderSlide(currentSlide);
        await new Promise(r => setTimeout(r, 900));
        const layer = document.getElementById('presenter-layer');
        return layer ? (layer.getAttribute('data-state') || (layer.querySelector('[data-state]') || layer).getAttribute('data-state')) : null;
    });
    check('3. the presenter speaks with the narration', /speak|talk/.test(String(speaking)), String(speaking));
    const focus = seen['4-diagram'].log.find(e => e.type === 'diagram_focus' || e.type === 'visual_highlight');
    check('4. the diagram is highlighted when the narration points at it (on time)', focus && onTime(focus), focus ? `error ${fmt(focus)} s` : 'not fired');
    const labelsFired = seen['6-formula'].log.filter(e => e.type === 'label_enter');
    check('5. the formula\'s symbol labels appear as each meaning is spoken (on time)', labelsFired.length === 3 && labelsFired.every(onTime),
        labelsFired.map(fmt).join(' / '));
    const cam = all.find(e => e.type === 'camera_focus');
    check('6. the camera leans in at the key teaching moment (on time)', cam && onTime(cam), cam ? `${LABELS[cam.scene]} error ${fmt(cam)} s` : 'no camera event');
    const out = seen['7-code'].log.find(e => e.type === 'text_reveal');
    const dates = seen['9-timeline'].log.filter(e => e.type === 'text_emphasis');
    check('7. text appears when it becomes relevant: the code\'s output when the narration says what it prints, each date as it is said',
        out && onTime(out) && dates.length >= 3 && dates.every(onTime), `output ${out ? fmt(out) : '-'}; dates ${dates.map(fmt).join(' / ')}`);
    const points = all.filter(e => e.type === 'presenter_point');
    const explains = all.filter(e => e.type === 'presenter_explain' || e.type === 'presenter_summarize');
    check('8. the presenter acts at the moments the attention moves (points, then explains or sums up)', points.length > 0 && explains.length > 0,
        `${points.length} points (${points.map(e => e.gesture).join(',')}), ${explains.length} explains (${explains.map(e => e.gesture).join(',')})`);
    const finished = Object.values(seen).every(s => !s.state.sync || s.state.sync.fired === s.state.sync.events);
    const pendingLeft = await page.evaluate(() => document.querySelectorAll('#slide-content-container .cine-pending').length);
    check('9. every scene ends cleanly: all its moments fired, no result left hidden, the result held a moment', finished && pendingLeft === 0 && seen['7-code'].endHold > 0,
        `hold ${seen['7-code'].endHold}s; pending ${pendingLeft}; ${Object.entries(seen).map(([k, s]) => `${k}:${s.state.sync ? s.state.sync.fired + '/' + s.state.sync.events : '-'}`).join(' ')}`);
    const errs = measured.map(errorOf).filter(Number.isFinite);
    const maxErr = errs.length ? Math.max(...errs.map(Math.abs)) : null;
    check('synchronization accuracy: every measured moment fired within its tolerance', measured.length >= 8 && measured.every(onTime),
        `${measured.length} measured; max error ${maxErr === null ? '-' : maxErr.toFixed(2)} s; mean ${errs.length ? (errs.reduce((a, b) => a + Math.abs(b), 0) / errs.length).toFixed(2) : '-'} s`);

    // ---- 10. another speech rate: the same moments ------------------------------------------------------------------------------
    await page.evaluate(() => { ttsState.rate = 1.3; const s = document.getElementById('tts-rate-slider'); if (s) s.value = '1.3'; });
    const fast = await playScene(page, AT['6-formula']);
    const fastLabels = fast.log.filter(e => e.type === 'label_enter');
    check('10. at speech rate 1.3 the same moments still fire on time (positions are on the narration, not on the clock)',
        fastLabels.length === 3 && fastLabels.every(onTime) && fastLabels.every(e => e.rate > 1.1), fastLabels.map(e => `${fmt(e)}@${e.rate}`).join(' / '));
    await page.evaluate(() => { ttsState.rate = 0.9; const s = document.getElementById('tts-rate-slider'); if (s) s.value = '0.9'; });

    // ---- 11. regenerating the narration recompiles the timing, generates nothing ------------------------------------------------
    const count = r => ((r && (r.runs || r)) || []).length;
    const runsBefore = count((await api('GET', '/api/ai-media/runs?limit=100')).data);
    const regen = await page.evaluate(async n => {
        const before = slides[n].cinematic_plan.sync;
        const template = slides[n].cinematic_plan.template;
        slides[n].narration = 'Look closely now. ' + slides[n].narration;
        await planLessonCinematic();
        const after = slides[n].cinematic_plan.sync;
        return { fpBefore: before.fingerprint, fpAfter: after.fingerprint, template, templateAfter: slides[n].cinematic_plan.template,
            firstBefore: before.events.find(e => e.type === 'label_enter').at, firstAfter: after.events.find(e => e.type === 'label_enter').at };
    }, AT['6-formula']);
    const runsAfter = count((await api('GET', '/api/ai-media/runs?limit=100')).data);
    check('11. new narration recompiles the synchronization (new positions) and keeps the composition; nothing is generated',
        regen.fpBefore !== regen.fpAfter && regen.template === regen.templateAfter && regen.firstAfter.ratio !== regen.firstBefore.ratio && runsBefore === runsAfter,
        `${regen.firstBefore.ratio} -> ${regen.firstAfter.ratio}; runs ${runsBefore}->${runsAfter}`);

    // ---- 12. a visual removed in Visual Review leaves no orphan events -----------------------------------------------------------
    await api('POST', '/api/visuals/review', { project_id: pid, scene_index: AT['4-diagram'], slot: 'side', action: 'remove' });
    const orphan = await page.evaluate(async n => {
        const saved = await (await fetch(`/api/projects/${new URLSearchParams(location.search).get('project_id')}`, { headers: { Authorization: 'Bearer ' + localStorage.getItem('jwt_token') } })).json();
        slides[n].visual_review = saved.scenes[n].visual_review;
        await planLessonVisuals();
        const s = slides[n].cinematic_plan.sync;
        return { visualEvents: s ? s.events.filter(e => e.target.layer === 'visual').length : -1, layers: slides[n].cinematic_plan.layers.map(l => l.id), events: s ? s.events.length : 0 };
    }, AT['4-diagram']);
    check('12. a removed visual takes its events with it (no orphan highlight or focus; the scene still plays)', orphan.visualEvents === 0 && !orphan.layers.includes('visual'),
        JSON.stringify(orphan));

    // ---- 13. Visual Review shows the synchronization -------------------------------------------------------------------------------
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    await page.goto(`${BASE}/?project_id=${pid}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.evaluate(() => planLessonVisuals());
    await page.click('#start-review-btn');
    await page.waitForSelector('.review-overlay.open .review-item[data-slot="composition"]', { timeout: 30000 });
    await page.click(`.review-overlay.open .review-item[data-slot="composition"][data-scene="${AT['6-formula']}"]`);
    const inspector = await page.evaluate(() => (document.querySelector('.review-overlay.open') || document.body).innerText);
    await page.screenshot({ path: path.join(OUT, 'inspector-sync.png') });
    check('13. Visual Review shows the scene\'s synchronization in plain words (and still works)', /Synchroni[sz]ation/i.test(inspector) && /label|camera|presenter/i.test(inspector),
        (inspector.match(/Synchroni[sz]ation[\s\S]{0,140}/i) || [''])[0].replace(/\s+/g, ' '));
    await page.click('.review-panel .export-close');

    // ---- 15. a refresh rebuilds the same plans ------------------------------------------------------------------------------------
    const refreshed = await page.evaluate(() => slides.map(s => (s.cinematic_plan && s.cinematic_plan.sync ? s.cinematic_plan.sync.fingerprint : null)));
    const before = plans.map(p => p && p.fingerprint);
    const unchanged = refreshed.filter((fp, i) => fp && fp === before[i]).length;
    check('15. a refresh rebuilds the same synchronization plans (only the two scenes changed above differ)', unchanged >= before.filter(Boolean).length - 2,
        `${unchanged}/${before.filter(Boolean).length} unchanged`);

    // ---- 14. the export plays the same plan: the same moments, fired on time, a frame that matches -----------------------------------
    await startLesson(page, pid);
    await playScene(page, AT['7-code']);
    const previewOut = await page.evaluate(() => window.__syncLog.slice());
    await page.screenshot({ path: path.join(OUT, 'preview-code-end.png') });
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
    const recordEnd = Date.now() + 600000;
    let outcome = null;
    while (!outcome && Date.now() < recordEnd) {
        await sleep(700);
        const s = await page.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden,
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
        if (s.ready) outcome = 'ready';
        else if (s.error) outcome = 'error: ' + s.error;
    }
    const exportLog = await page.evaluate(() => window.__syncLog.slice());
    fs.writeFileSync(path.join(OUT, 'export-log.json'), JSON.stringify(exportLog, null, 2));
    const exported = exportLog.filter(e => e.exporting);
    const exportMeasured = exported.filter(e => errorOf(e) !== null);
    const sameIds = previewOut.filter(e => e.scene === AT['7-code']).every(p => exported.some(x => x.scene === p.scene && x.id === p.id && JSON.stringify(x.planned) === JSON.stringify(p.planned)));
    const job = await page.evaluate(() => exportFlow.job);
    const exportDir = path.join(data, 'exports');
    const userDir = job && fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(dd => fs.existsSync(path.join(exportDir, dd, `${job.id}.webm`))) : null;
    const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
    const sceneLog = await page.evaluate(() => window.exportSceneLog || []);
    let diff = null;
    const next = sceneLog[AT['7-code'] + 1];
    if (stored && next) {
        spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', Math.max(0, next.t - 0.6).toFixed(2), '-i', stored, '-frames:v', '1', path.join(OUT, 'export-code-end.png')]);
        diff = +difference(grid(path.join(OUT, 'preview-code-end.png')), grid(path.join(OUT, 'export-code-end.png'))).toFixed(1);
    }
    check('14. the export plays the same synchronization plan: the same events at the same planned moments, fired on time, and its frame matches the preview',
        outcome === 'ready' && exported.length >= 10 && sameIds && exportMeasured.length >= 8 && exportMeasured.every(onTime) && diff !== null && diff < 30,
        `${outcome}; ${exported.length} fired in the export (${exportMeasured.filter(onTime).length}/${exportMeasured.length} on time); frame difference ${diff}`);
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
