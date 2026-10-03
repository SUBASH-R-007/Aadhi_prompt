// AI Visual Director check (Phase 15) in real Chrome against a throwaway server with the local stand-ins only
// (AI_FAKE_PROVIDER=1: stand-in media and text models; FAKE_TTS=1: a speech-like tone). No real provider, no network.
//
//   1. open a lesson   2. the scenes load   3. each scene has a visual direction   4. the direction shows in the
//   composition (step flow, timeline, symbol labels, code beside its output, comparison together, key points)
//   5. Regenerate direction works (no media)   6. an existing library visual is reused (never generated)
//   7. a user's direction choice is respected (and "Automatic" gives it back)   8. AI failure falls back safely
//   9. Visual Review still works   10. the preview plays   11. the export uses the same direction
//   12. a refresh keeps the directions (saved with the lesson, unchanged)
//   Also: the representative scenes at 1280x720 and 1920x1080 with nothing overlapping and text never below 80 %, and
//   cross-scene continuity (the same concept keeps its representation).
//
// Needs Chrome, ffmpeg, playwright-core (PLAYWRIGHT_CORE_DIR) and the repo's virtualenv:
//   AADHI_PASSWORD=... node tests/direction_browser_check.mjs
// Silent: no audio output, browser speech stubbed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.DIRECTION_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-direction-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.DIRECTION_CHECK_PORT || 9990 + (process.pid % 8));
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
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `adc-${process.pid}`), JWT_SECRET: 'direction-check-' + Math.random().toString(36).slice(2),
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
const LESSON = { subject_name: 'Direction check', session_title: 'How plants and programs work',
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

const problems = [];
let browser;
try {
    spawnSync(PYTHON, [path.join(REPO, 'tests', 'fixtures', 'cinematic_diagrams.py'), OUT]);
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const leafAsset = await upload(path.join(OUT, 'leaf_cross_section.png'), 'Leaf cross section showing palisade cells, spongy layer and stoma', ['leaf', 'cross', 'section', 'palisade', 'chloroplast']);
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
        await sleep(1500);
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
        const plan = slides[n].cinematic_plan;
        const dir = slides[n].visual_direction || null;
        const items = [...document.querySelectorAll('#slide-content-container li')];
        const content = document.getElementById('slide-content-container');
        const outCard = content && content.querySelector(':scope > pre + p');
        return { template: plan.template, rep: document.body.getAttribute('data-cine-rep'), board: document.body.getAttribute('data-cine-board'),
            reveal: plan.reveal || null, direction: plan.direction || null, stored: dir && { strategy: dir.strategy, kind: dir.primary_visual.kind,
                goal: dir.learning_goal, source: dir.source, fingerprint: dir.fingerprint, annotations: (dir.annotations || []).map(a => a.text),
                continuity: dir.continuity, route: dir.route },
            labels: [...document.querySelectorAll('#cine-labels .cine-label')].map(c => c.textContent),
            visibleItems: items.filter(li => Number(getComputedStyle(li).opacity) > 0.5).length, items: items.length,
            dates: document.querySelectorAll('#slide-content-container .cine-date').length,
            outputCard: outCard ? { css: getComputedStyle(outCard, '::before').content, x: outCard.getBoundingClientRect().x } : null,
            pre: content && content.querySelector('pre') ? content.querySelector('pre').getBoundingClientRect().x : null,
            camera: plan.camera.movement, decision: plan.composition && plan.composition.decision, boxes, overlaps,
            fit: cinematicStage.stats.lastFit, overflow: !!cinematicStage.stats.overflow, W: innerWidth, H: innerHeight,
            visualSrc: (document.querySelector('.dynamic-side-zone .side-panel-view.active img') || {}).src || null,
            plan_visual: (slides[n].visual_plan || {}).side || null };
    }, index);

    // ---- 1-4, 10: the representative lesson at 1280x720 ---------------------------------------------------------------------
    const cine = { mode: 'cinematic', transitions: 'fade', motion: 'subtle', background: 'auto', typography: 'academic', composer: 'rules', director: 'rules' };
    const { page } = await newPage({ width: 1280, height: 720 }, cine);
    await startLesson(page, pid);
    check('1-2. the lesson opens and every scene loads with a composition', await page.evaluate(n => slides.length === n && slides.every(s => s.cinematic_plan), LABELS.length));
    const directed = await page.evaluate(() => slides.map(s => s.visual_direction && s.visual_direction.strategy));
    check('3. every scene has a visual direction (stored on the scene)', directed.every(Boolean), directed.join(','));
    const seen = {};
    for (const [i, label] of LABELS.entries()) {
        await playScene(page, i);
        await sleep(label === '10-quiz' ? 2500 : 6000);
        seen[label] = await measure(page, i);
        await page.screenshot({ path: path.join(OUT, `720-${label}.png`) });
    }
    fs.writeFileSync(path.join(OUT, 'seen-720.json'), JSON.stringify(seen, null, 2));
    const s = seen;
    check('4. process: a step flow, built step by step, the presenter as a small guide', s['3-process'].rep === 'steps' && s['3-process'].stored.strategy === 'step_by_step'
        && s['3-process'].reveal === 'progressive', `${s['3-process'].stored.strategy}/${s['3-process'].rep}/${s['3-process'].decision.presenter_role}`);
    check('4. timeline: the events on a timeline with date badges', s['9-timeline'].rep === 'timeline' && s['9-timeline'].dates >= 3, `${s['9-timeline'].rep}, ${s['9-timeline'].dates} dates`);
    check('4. formula: the symbols labelled from the lesson\'s own words', s['6-formula'].template === 'formula_focus'
        && ['F = Force', 'm = Mass', 'a = Acceleration'].every(t => s['6-formula'].labels.includes(t)), s['6-formula'].labels.join(' · '));
    check('4. code: the code beside its output', s['7-code'].rep === 'code_output' && s['7-code'].outputCard && /Output/.test(s['7-code'].outputCard.css)
        && s['7-code'].camera === 'static', `${s['7-code'].rep}; output card ${!!s['7-code'].outputCard}`);
    check('4. comparison: both sides shown together, a still camera', s['8-comparison'].template === 'comparison' && s['8-comparison'].reveal === 'together'
        && s['8-comparison'].visibleItems === s['8-comparison'].items && s['8-comparison'].camera === 'static', `${s['8-comparison'].reveal}`);
    check('4. summary: the key points as cards', s['11-summary'].rep === 'key_points', s['11-summary'].rep);
    check('4. diagram: diagram-first, the presenter small and pointing', ['conceptual_diagram', 'mechanism'].includes(s['4-diagram'].stored.strategy)
        && s['4-diagram'].decision.visual_role === 'dominant', `${s['4-diagram'].stored.strategy}/${s['4-diagram'].decision.presenter_role}`);
    check('directions say why and what the learner should get, in plain words', LABELS.every(l => s[l].direction && s[l].direction.reasons.length
        && s[l].stored.goal && !/Inside the leaf|Quick check/.test(s[l].stored.goal)), LABELS.map(l => s[l].stored.goal).slice(0, 4).join(' | '));
    const contd = s['5-diagram-contd'].stored;
    check('continuity: the leaf concept keeps its representation where it returns', contd.continuity.from_scene === AT['4-diagram'] && contd.continuity.kept
        && contd.kind === s['4-diagram'].stored.kind, JSON.stringify(contd.continuity));
    check('nothing overlaps and no text shrinks below 80 % (720p)', Object.values(s).every(m => m.overlaps.length === 0 && !m.overflow && m.fit >= 0.8),
        LABELS.map(l => `${l}:${s[l].overlaps.join('+') || 'ok'}/${s[l].fit}`).join(' '));

    // ---- 1920x1080 ------------------------------------------------------------------------------------------------------------
    const hd = await newPage({ width: 1920, height: 1080 }, cine);
    await startLesson(hd.page, pid);
    const hdSeen = {};
    for (const [i, label] of LABELS.entries()) {
        await playScene(hd.page, i);
        await sleep(label === '10-quiz' ? 2500 : 6000);
        hdSeen[label] = await measure(hd.page, i);
        await hd.page.screenshot({ path: path.join(OUT, `1080-${label}.png`) });
    }
    check('1920x1080: the same directions and compositions, nothing overlapping, text readable', LABELS.every(l => hdSeen[l].template === s[l].template
        && hdSeen[l].rep === s[l].rep && hdSeen[l].overlaps.length === 0 && !hdSeen[l].overflow && hdSeen[l].fit >= 0.8),
        LABELS.map(l => `${l}:${hdSeen[l].overlaps.join('+') || 'ok'}`).join(' '));
    await hd.context.close();

    // ---- 6. an existing library visual is reused (never generated) -----------------------------------------------------------
    const runs = (await api('GET', '/api/ai-media/runs?limit=100')).data;
    const count = r => ((r && (r.runs || r)) || []).length;
    check('6. the leaf diagram is the library asset (reused, not generated); no generation ran', s['4-diagram'].plan_visual && s['4-diagram'].plan_visual.asset_id === leafAsset
        && s['4-diagram'].stored.route && s['4-diagram'].stored.route.prefer_existing && count(runs) === 0,
        `${s['4-diagram'].plan_visual && s['4-diagram'].plan_visual.source} ${s['4-diagram'].plan_visual && s['4-diagram'].plan_visual.selection}; runs ${count(runs)}`);

    // ---- 9. Visual Review still works; the direction shows in the scene inspector -------------------------------------------
    const fingerprintsBefore = await page.evaluate(() => slides.map(s => s.visual_direction && s.visual_direction.fingerprint));
    await page.evaluate(() => { ttsState.isPlaying = false; speakNarration(null); });
    await page.goto(`${BASE}/?project_id=${pid}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.evaluate(() => planLessonVisuals());
    // ---- 12. a refresh keeps the directions (saved with the lesson, unchanged) ----------------------------------------------
    const fingerprintsAfter = await page.evaluate(() => slides.map(s => s.visual_direction && s.visual_direction.fingerprint));
    const savedScenes = (await api('GET', `/api/projects/${pid}`)).data.scenes;
    check('12. a refresh keeps every scene\'s direction (the same, and saved with the lesson)', fingerprintsAfter.every((f, i) => f && f === fingerprintsBefore[i])
        && savedScenes.every((sc, i) => sc.visual_direction && sc.visual_direction.fingerprint === fingerprintsBefore[i]),
        `${fingerprintsAfter.filter((f, i) => f === fingerprintsBefore[i]).length}/${fingerprintsBefore.length} unchanged`);
    await page.click('#start-review-btn');
    await page.waitForSelector('.review-overlay.open .review-item[data-slot="composition"]', { timeout: 30000 });
    const reviewItems = await page.$$eval('.review-overlay.open .review-item', els => els.map(e => e.getAttribute('data-slot')));
    await page.click(`.review-overlay.open .review-item[data-slot="composition"][data-scene="${AT['3-process']}"]`);
    const block = await page.textContent('.review-overlay.open .review-direction');
    await page.screenshot({ path: path.join(OUT, 'inspector-direction.png') });
    await page.click('.review-overlay.open [data-action="keep"]');
    await page.waitForFunction(() => /composed as shown/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    const kept = (await api('GET', `/api/projects/${pid}`)).data.scenes[AT['3-process']];
    check('9. Visual Review still works: visuals, presenters and compositions are listed, the direction is shown, Keep approves',
        reviewItems.includes('composition') && reviewItems.includes('side') && /Visual direction: Step-by-step/.test(block) && /follow the 4 steps/.test(block)
        && (kept.visual_review || {}).composition && kept.visual_review.composition.status === 'approved', block.replace(/\s+/g, ' ').slice(0, 160));

    // ---- 5. Regenerate direction: decided again, no media; the composition follows -----------------------------------------
    const runsBefore = count((await api('GET', '/api/ai-media/runs?limit=100')).data);
    await page.click('.review-overlay.open [data-action="direction-regenerate"]');
    await page.waitForFunction(() => /visual direction decided again/i.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    const runsAfter = count((await api('GET', '/api/ai-media/runs?limit=100')).data);
    const regen = await page.evaluate(n => ({ fp: slides[n].visual_direction.fingerprint, plan: slides[n].cinematic_plan.direction.fingerprint }), AT['3-process']);
    check('5. Regenerate direction: decided again with no media generated, and the composition follows it', runsAfter === runsBefore && regen.fp === regen.plan,
        `runs ${runsBefore}→${runsAfter}; ${regen.fp}`);

    // ---- 7. the user's direction choice is respected, and "Automatic" gives it back -----------------------------------------
    await page.click('.review-overlay.open [data-action="direction-change"]');
    await page.selectOption('.review-overlay.open #direction-presenter_role', 'hidden');
    await page.click('.review-overlay.open [data-action="direction-apply"]');
    await page.waitForFunction(() => /follows your visual direction/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    let saved = (await api('GET', `/api/projects/${pid}`)).data.scenes[AT['3-process']];
    const chosen = { role: saved.visual_direction.presenter.role, source: saved.visual_direction.source, shown: saved.cinematic_plan.presenter.shown,
        review: (saved.visual_review || {}).direction, compositionStale: saved.cinematic_plan.review_status };
    await page.click('.review-overlay.open [data-action="direction-change"]');
    await page.selectOption('.review-overlay.open #direction-presenter_role', 'auto');
    await page.click('.review-overlay.open [data-action="direction-apply"]');
    await page.waitForFunction(() => /automatic visual direction|follows your visual direction/.test(document.querySelector('.review-overlay.open .review-status').textContent), null, { timeout: 15000 });
    saved = (await api('GET', `/api/projects/${pid}`)).data.scenes[AT['3-process']];
    check('7. a direction choice is respected (no presenter, "Your choice"), the approved composition re-opens, and "Automatic" gives it back',
        chosen.role === 'hidden' && chosen.source === 'user' && chosen.shown === false && chosen.review && chosen.review.overrides.presenter_role === 'hidden'
        && chosen.compositionStale === 'pending' && !(saved.visual_review || {}).direction && saved.visual_direction.source !== 'user' && saved.cinematic_plan.presenter.shown,
        `${chosen.role}/${chosen.source}/${chosen.compositionStale} → ${saved.visual_direction.presenter.role}`);
    await page.click('.review-panel .export-close');

    // ---- 8. AI-assisted direction without a model: said honestly, the rules decide; with the stand-in model: validated ----------
    await page.goto(BASE + '/');
    await page.click('#nav-settings'); // (Phase 21: the start screen's settings are in Settings → Lesson defaults)
    await page.waitForSelector('#cinematic-director', { timeout: 30000 });
    await page.selectOption('#cinematic-director', 'ai');
    await page.waitForFunction(() => /not available/i.test((document.querySelector('.cinematic-director-note') || {}).textContent || ''), null, { timeout: 15000 });
    const directorNote = await page.textContent('.cinematic-director-note');
    await startLesson(page, pid);
    const offline = await page.evaluate(() => slides.map(s => s.visual_direction && { source: s.visual_direction.source, ai: s.visual_direction.ai && s.visual_direction.ai.status }));
    await playScene(page, AT['4-diagram']);
    await sleep(2500);
    const offlineScene = await measure(page, AT['4-diagram']);
    await page.evaluate(() => { window.composerOptions = () => ({ provider: 'fake', model: 'fake-director-1' }); });
    await page.evaluate(() => planLessonVisuals());
    const withModel = await page.evaluate(() => slides.map(s => s.visual_direction && s.visual_direction.ai && s.visual_direction.ai.status).filter(Boolean));
    check('8. AI-assisted direction without a model: said honestly; the rules decide every scene and the lesson plays',
        /not available/i.test(directorNote) && offline.every(d => d && d.source !== 'ai') && offline.some(d => d.ai === 'unavailable') && offlineScene.overlaps.length === 0,
        `${offline.filter(d => d.ai).map(d => d.ai).join(',')}`);
    check('8. with the stand-in model: only undecided scenes are asked and every answer is validated', withModel.length > 0 && withModel.every(st => ['ok', 'repaired'].includes(st)),
        withModel.join(','));

    // ---- 10-11. preview → export: the recording uses the same directions -------------------------------------------------------
    await page.evaluate(() => { window.composerOptions = null; cinematicSettings.set('director', 'rules'); });
    await startLesson(page, pid);
    await playScene(page, AT['3-process']);
    await sleep(6000);
    await page.screenshot({ path: path.join(OUT, 'preview-process.png') });
    const previewRep = await page.evaluate(() => document.body.getAttribute('data-cine-rep'));
    check('10. the preview plays the directed scene (the step flow drawn, the narration revealing it)', previewRep === 'steps', previewRep);
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
    const repsDuring = new Set();
    while (!outcome && Date.now() < recordEnd) {
        await sleep(700);
        const st = await page.evaluate(() => ({ ready: !document.querySelector('.export-ready').hidden, recording: exportFlow.recording, rep: document.body.getAttribute('data-cine-rep'),
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null }));
        if (st.recording && st.rep && st.rep !== 'none') repsDuring.add(st.rep);
        if (st.ready) outcome = 'ready';
        else if (st.error) outcome = 'error: ' + st.error;
    }
    const job = await page.evaluate(() => exportFlow.job);
    const exportDir = path.join(data, 'exports');
    const userDir = job && fs.existsSync(exportDir) ? fs.readdirSync(exportDir).find(d => fs.existsSync(path.join(exportDir, d, `${job.id}.webm`))) : null;
    const stored = userDir ? path.join(exportDir, userDir, `${job.id}.webm`) : null;
    const sceneLog = await page.evaluate(() => window.exportSceneLog || []);
    let diff = null;
    const proc = sceneLog[AT['3-process']];
    if (stored && proc) {
        spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', (proc.t + 6).toFixed(2), '-i', stored, '-frames:v', '1', path.join(OUT, 'export-process.png')]);
        diff = +difference(grid(path.join(OUT, 'preview-process.png')), grid(path.join(OUT, 'export-process.png'))).toFixed(1);
    }
    check('11. the export uses the same directions (step flow, timeline, code with output, key points) and its frame matches the preview',
        outcome === 'ready' && stored && ['steps', 'timeline', 'code_output', 'key_points'].every(r => repsDuring.has(r)) && diff !== null && diff < 28,
        `${outcome}; ${[...repsDuring].join(',')}; frame difference ${diff}`);

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
