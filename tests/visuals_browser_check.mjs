// Browser check of the visual router (visuals.py + visuals.js) in real Chrome: a four-scene lesson
// whose visuals come from four different sources, previewed and then exported to a video file.
//   scene 1  an AI-video scene with no video, matched to a library clip by its description
//   scene 2  a chart side panel, drawn by the built-in Chart.js renderer
//   scene 3  an AI-video scene nothing matches: planned, not generated, until "Generate" is clicked
//            (the AI provider is mocked in the browser: no real AI call is ever made)
//   scene 4  a scene whose "visual" intent asks for Aadhi, matched to a shared Aadhi picture
// Then the lesson is exported and the downloaded file is checked for the same visuals.
//
// Needs the server running, Chrome, ffmpeg + ffprobe and playwright-core (npm install --no-save
// playwright-core, or PLAYWRIGHT_CORE_DIR):
//   AADHI_USER=admin AADHI_PASSWORD=... node tests/visuals_browser_check.mjs [http://127.0.0.1:9942]
// Silent: nothing reaches the speakers (see the launch options). Output goes to $VISUALS_CHECK_OUT.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const BASE = (process.argv[2] || 'http://127.0.0.1:9942').replace(/\/$/, '');
const OUT = process.env.VISUALS_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-visuals-check');
const USER = process.env.AADHI_USER || 'admin';
const PASSWORD = process.env.AADHI_PASSWORD;
if (!PASSWORD) {
    console.error('Set AADHI_PASSWORD (and AADHI_USER if not admin) to the login to test with.');
    process.exit(2);
}

const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: !!ok, detail });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`);
}

function run(cmd, args) {
    const r = spawnSync(cmd, args, { encoding: 'utf8', maxBuffer: 1 << 26 });
    if (r.status !== 0) throw new Error(`${cmd} failed: ${r.stderr}`);
    return r;
}

async function loadPlaywright() {
    try {
        return await import('playwright-core');
    } catch (e) {
        if (process.env.PLAYWRIGHT_CORE_DIR) return import(pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE_DIR, 'index.mjs')).href);
        throw new Error('playwright-core not found: run `npm install --no-save playwright-core` (or set PLAYWRIGHT_CORE_DIR)');
    }
}

async function api(method, route, token, body) {
    const res = await fetch(BASE + route, {
        method,
        headers: { ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined
    });
    return { status: res.status, data: await res.json().catch(() => null) };
}

// Average colour of the centre of a frame of the video, as [r, g, b]
function centreColour(file, seconds) {
    const r = spawnSync('ffmpeg', ['-v', 'error', '-ss', String(seconds), '-i', file, '-frames:v', '1',
        '-vf', 'crop=iw/5:ih/5:iw*2/5:ih*2/5,scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], { maxBuffer: 1 << 20 });
    return [...r.stdout.subarray(0, 3)];
}
const near = (c, target, tol = 60) => c.length === 3 && c.every((v, i) => Math.abs(v - target[i]) <= tol);

function frameAt(file, seconds, name) {
    const out = path.join(OUT, name);
    run('ffmpeg', ['-v', 'error', '-y', '-ss', String(seconds), '-i', file, '-frames:v', '1', '-vf', 'scale=640:-1', out]);
    return out;
}

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

// Two clips with unmistakable colours, so the recording shows which one played:
// the library clip is blue, the (mocked) AI video magenta; each has a moving box
const tag = Date.now().toString(36);
const BLUE = [32, 64, 224];
const MAGENTA = [224, 32, 160];
const libraryClip = path.join(OUT, `press-${tag}.mp4`);
const aiName = `visual_check_ai_${tag}.mp4`;
const aiClip = path.join(REPO, 'static_videos', aiName);
const colourClip = (hex, file) => run('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', `color=c=0x${hex}:size=1280x720:rate=25:duration=12`,
    '-vf', "drawbox=x='mod(t*200,1100)':y=300:w=120:h=120:c=white:t=fill", '-pix_fmt', 'yuv420p', '-c:v', 'libx264', file]);
colourClip('2040E0', libraryClip);
colourClip('E020A0', aiClip);

const login = await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: USER, password: PASSWORD }) })).json();
const token = login.access_token;

// The library clip, described (not named) as what the lesson asks for
const form = new FormData();
form.append('file', new Blob([fs.readFileSync(libraryClip)]), `IMG_${tag}.mp4`);
const clipId = (await (await fetch(BASE + '/api/assets', { method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: form })).json()).asset.id;
const described = await api('PATCH', `/api/assets/${clipId}`, token, { description: 'a hydraulic press slowly crushing a steel block', keywords: ['hydraulic press', 'compression'] });
check('library clip uploaded and described (its file name says nothing)', described.status === 200 && described.data.details.description.includes('hydraulic press'), `IMG_${tag}.mp4`);

const rocketPrompt = `a rocket lifting off from a desert launch pad at dawn ${tag}`;
const lesson = {
    subject_name: 'Visual Router Check', session_number: '1', session_title: 'Four visual sources',
    concept_map: [{ id: 'c1', label: 'Forces' }],
    scenes: [
        { type: 'ai_video', concept_id: 'c1', title: 'CRUSHING STEEL', aadhi_position: 'hidden', prompt: 'a hydraulic press slowly crushing a steel block',
          narration: 'Watch the press squeeze the block. The steel resists at first, then it slowly gives way under the load.' },
        { type: 'content', concept_id: 'c1', title: 'MEASURED FORCES', aadhi_position: 'popup_bottom_left', html: '<p>Forces grow with load.</p>',
          side_panel: { type: 'chart', chart_type: 'bar', title: 'Force by load', data: { labels: ['Light', 'Medium', 'Heavy'], datasets: [{ label: 'kN', data: [3, 7, 12], backgroundColor: 'rgba(176,38,255,0.5)' }] } },
          narration: 'The chart shows the forces we measured. A light load gives three kilonewtons, and a heavy load gives twelve.' },
        { type: 'ai_video', concept_id: 'c1', title: 'LIFT OFF', aadhi_position: 'hidden', prompt: rocketPrompt,
          narration: 'A rocket pushes hot gas down against the ground. The ground pushes back, and the rocket rises into the sky.' },
        { type: 'content', concept_id: 'c1', title: 'MEET AADHI', aadhi_position: 'popup_bottom_left', html: '<p>Your guide for this lesson.</p>',
          visual: { concept: 'Aadhi the mascot', type: 'image' }, narration: 'Say hello to Aadhi.' }
    ]
};

const { chromium } = await loadPlaywright();
// Silent: Chrome sends nothing to the speakers (--disable-audio-output), the captured tab's sound is
// not played locally (suppressLocalAudioPlayback) and browser speech is a silent stand-in. Chrome's
// own mute is not used because a muted tab records silence.
const browser = await chromium.launch({
    channel: 'chrome', headless: true,
    args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'],
    ignoreDefaultArgs: ['--mute-audio']
});
function silentPage() {
    const devices = navigator.mediaDevices;
    const capture = devices.getDisplayMedia.bind(devices);
    devices.getDisplayMedia = (constraints = {}) => capture({
        ...constraints,
        audio: constraints.audio ? { ...(typeof constraints.audio === 'object' ? constraints.audio : {}), suppressLocalAudioPlayback: true } : false
    });
    if (window.speechSynthesis) {
        window.speechSynthesis.speak = utterance => setTimeout(() => {
            if (utterance.onstart) utterance.onstart(new Event('start'));
            setTimeout(() => utterance.onend && utterance.onend(new Event('end')), 300);
        }, 0);
        window.speechSynthesis.cancel = () => {};
    }
}

const problems = [];
const aiCalls = { video: 0, image: 0 };
try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, acceptDownloads: true });
    await context.addInitScript(t => localStorage.setItem('jwt_token', t), token);
    await context.addInitScript(silentPage);
    // The AI providers never see a request: AI video generation answers with the local magenta clip,
    // AI images are refused (and counted: this lesson must not need any)
    await context.route('**/generate-ai-video', route => {
        aiCalls.video++;
        route.fulfill({ status: 200, json: { status: 'success', video_url: `/static/${aiName}` } });
    });
    for (const pattern of ['**/get-image?**', '**/generate-ai-image']) {
        await context.route(pattern, route => {
            aiCalls.image++;
            route.fulfill({ status: 418, json: { detail: 'AI images are not allowed in this check' } });
        });
    }
    const page = await context.newPage();
    page.on('pageerror', e => problems.push(`page error: ${e.message}`));
    page.on('console', m => {
        const text = m.text();
        if (m.type() !== 'error' || (m.location().url || '').endsWith('/favicon.ico') || text.startsWith('Logo video play error AbortError')) return;
        problems.push(`console: ${text}`);
    });
    page.on('requestfailed', r => {
        const reason = r.failure() && r.failure().errorText;
        if (reason !== 'net::ERR_ABORTED') problems.push(`request failed: ${r.url()} ${reason}`);
    });
    page.on('response', r => { if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`); });

    await page.goto(BASE + '/');
    await page.waitForFunction(() => typeof AadhiVisuals !== 'undefined' && typeof planLessonVisuals === 'function');
    // (Phase 21: the setting is in Settings → Visuals & AI, its choices in plain words)
    check('AI visuals setting (Settings → Visuals & AI) defaults to "images": AI pictures automatically, AI videos on request',
        await page.$eval('#ai-visuals-select', s => s.value === 'images' && !!s.closest('#settings-visuals')
            && /^Pictures automatically, videos only when you ask$/.test(s.selectedOptions[0].textContent.trim())));
    const projectId = await page.evaluate(async data => {
        const res = await fetch('/save-history', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        return (await res.json()).id;
    }, lesson);

    // ---- Preview -----------------------------------------------------------------------------
    await page.goto(`${BASE}/?project_id=${projectId}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 20000 });
    await page.click('#start-lecture-btn');
    await page.waitForFunction(() => document.body.classList.contains('presentation-active'), null, { timeout: 30000 });
    await page.evaluate(() => skipIntroSequence());
    // Planned and started (the board shows and playback turns on together); then the check steps
    // through the scenes itself instead of the narration
    await page.waitForFunction(() => window.lessonVisualPlans.length > 0 && ttsState.isPlaying
        && !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 30000 });
    await page.evaluate(() => { ttsState.isPlaying = false; });

    const plans = await page.evaluate(() => slides.map(s => s.visual_plan || {}));
    const systemImages = (await api('GET', '/api/assets?kind=image&scope=system&limit=200', token)).data.assets;
    const system = systemImages.map(a => a.id);
    const aadhiPicture = systemImages.find(a => plans[3].side && a.id === plans[3].side.asset_id);
    check('scene 1: matched to the described library clip, not generated',
        plans[0].main && plans[0].main.source === 'ASSET' && plans[0].main.selection === 'matched' && plans[0].main.asset_id === clipId,
        plans[0].main && `${plans[0].main.source} ${plans[0].main.selection}, relevance ${plans[0].main.score}`);
    check('scene 2: the chart is drawn by the built-in renderer',
        plans[1].side && plans[1].side.source === 'PROCEDURAL' && plans[1].side.renderer === 'chart');
    check('scene 3: nothing matches, so an AI video is planned but not generated',
        plans[2].main && plans[2].main.source === 'AI_VIDEO' && plans[2].main.requires_generation === true && !plans[2].main.url,
        plans[2].main && `provider ${plans[2].main.provider}`);
    check('scene 4: the visual intent is answered by a shared Aadhi picture',
        plans[3].side && plans[3].side.source === 'SYSTEM_ASSET' && system.includes(plans[3].side.asset_id) && plans[3].side.media === 'STATIC_IMAGE',
        plans[3].side && `${aadhiPicture ? aadhiPicture.file_name : plans[3].side.asset_id.slice(0, 8)}, relevance ${plans[3].side.score}`);
    check('scene 4: the picture shows Aadhi (not the background still without Aadhi)',
        aadhiPicture && aadhiPicture.details.role === 'poster' && aadhiPicture.details.placement !== 'hidden', aadhiPicture && JSON.stringify(aadhiPicture.details));
    check('planning made no AI request', aiCalls.video === 0 && aiCalls.image === 0);

    const show = index => page.evaluate(i => { currentSlide = i; renderSlide(i); }, index);
    // The src of the scene's playing video once it contains `expected` (or what it shows instead)
    const playing = async expected => {
        const read = () => page.evaluate(() => { const v = document.getElementById('active-ai-video'); return v && v.readyState >= 2 ? v.getAttribute('src') : null; });
        try {
            await page.waitForFunction(exp => { const v = document.getElementById('active-ai-video'); return v && v.readyState >= 2 && (v.getAttribute('src') || '').includes(exp); },
                expected, { timeout: 20000 });
        } catch (e) { /* reported by the check below */ }
        return (await read()) || '';
    };
    const videoState = () => page.evaluate(() => {
        const v = document.getElementById('active-ai-video');
        const board = document.getElementById('presentation-board');
        return JSON.stringify(v ? { src: (v.getAttribute('src') || '').slice(0, 50), readyState: v.readyState, error: v.error && v.error.code, currentSlide,
            board: !board.classList.contains('hidden'), intro: document.getElementById('intro-sequence-container').style.display }
            : { video: 'none', currentSlide, notice: (document.querySelector('#jxgbox') || {}).textContent });
    });
    const settle = () => page.waitForTimeout(1200); // lets the scene's fade-in finish before a screenshot
    const shown = {};

    // Scene 1 is already on screen from the start; rendering it a second time at once would make
    // Chrome wait on its own cache for the same clip URL, so it is only re-shown if playback moved on
    if (await page.evaluate(() => currentSlide !== 0)) await show(0);
    shown[0] = await playing(`/api/assets/${clipId}/content`);
    check('preview scene 1 plays the library clip', shown[0].includes(`/api/assets/${clipId}/content?token=`), shown[0] ? '' : await videoState());
    await settle();
    await page.screenshot({ path: path.join(OUT, '1-library-clip.png') });

    await show(1);
    await page.waitForFunction(() => document.getElementById('side-chart-panel').classList.contains('active') && currentSideChart, null, { timeout: 15000 });
    check('preview scene 2 shows the chart', await page.evaluate(() => currentSideChart.data.datasets[0].data.join(',') === '3,7,12'));
    await settle();
    await page.screenshot({ path: path.join(OUT, '2-chart.png') });

    await show(2);
    await page.waitForSelector('#jxgbox .visual-notice-action', { timeout: 15000 });
    const notice = await page.textContent('#jxgbox .visual-notice');
    check('preview scene 3 asks before generating (default setting)', aiCalls.video === 0 && /needs an AI video/.test(notice), notice.replace(/\s+/g, ' ').slice(0, 90));
    await page.screenshot({ path: path.join(OUT, '3a-ai-video-needed.png') });
    await page.click('#jxgbox .visual-notice-action');
    shown[2] = await playing(`/static/${aiName}`);
    check('"Generate AI video" made exactly one (mocked) request and plays the result', aiCalls.video === 1 && shown[2].endsWith(`/static/${aiName}`), shown[2] || await videoState());
    await settle();
    await page.screenshot({ path: path.join(OUT, '3b-ai-video-generated.png') });

    await show(3);
    await page.waitForFunction(() => {
        const img = document.getElementById('side-image-display');
        return document.getElementById('side-image-panel').classList.contains('active') && img.complete && img.naturalWidth > 0 && img.style.opacity === '1';
    }, null, { timeout: 20000 }).catch(() => {});
    shown[3] = (await page.$eval('#side-image-display', i => i.getAttribute('src'))) || '';
    check('preview scene 4 shows the shared Aadhi picture in the side panel', shown[3].includes(`/api/assets/${plans[3].side.asset_id}/content?token=`));
    await settle();
    await page.screenshot({ path: path.join(OUT, '4-aadhi-picture.png') });

    await page.evaluate(() => populateInfoModal());
    const badges = await page.$$eval('#info-modal-list .visual-source-badge', bs => bs.map(b => b.textContent));
    check('each scene shows where its visual comes from', ['🗂 Library', 'Side: 📐 Built-in visual', '✨ AI video', 'Side: 🦌 Shared Aadhi asset'].every(b => badges.includes(b)), badges.join(' · '));

    const debug = await page.evaluate(async () => {
        const data = await visualApi.plan(slides, AadhiVisuals.planOptions('images'), true);
        return AadhiVisuals.debugRows(data.plans).map(r => `${r.scene}/${r.slot} ${r.source}: ${r.trace}`);
    });
    fs.writeFileSync(path.join(OUT, 'plan-debug.txt'), debug.join('\n') + '\n');
    check('the debug view explains every decision', debug.length === 4 && debug.every(line => line.includes(':')), path.join(OUT, 'plan-debug.txt'));

    // ---- Export the same lesson ----------------------------------------------------------------
    // What each scene shows once it is fully on screen: sampled in the page every 100 ms while the
    // export plays the lesson, keeping each scene's last state (its visual fades in at the start)
    await page.evaluate(() => {
        window.visualSamples = {};
        setInterval(() => {
            if (!exportFlow.recording || !window.exportStartTime || isIntroRunning) return;
            const video = document.getElementById('active-ai-video');
            const img = document.getElementById('side-image-display');
            window.visualSamples[currentSlide] = {
                video: video && video.readyState >= 2 && video.offsetParent !== null ? video.getAttribute('src') : null,
                image: document.getElementById('side-image-panel').classList.contains('active') && img.naturalWidth > 0 ? img.getAttribute('src') : null,
                chart: document.getElementById('side-chart-panel').classList.contains('active')
            };
        }, 100);
    });
    await page.evaluate(() => exportFlow.open());
    await page.click('.export-start-btn');
    const warnings = [];
    for (;;) {
        const start = page.locator('.export-actions button', { hasText: 'Start Recording' });
        const anyway = page.locator('.export-actions button', { hasText: 'Record anyway' });
        await Promise.race([start.waitFor({ timeout: 300000 }), anyway.waitFor({ timeout: 300000 })]);
        if (await anyway.isVisible()) {
            warnings.push(await page.textContent('.export-message'));
            await anyway.click();
            continue;
        }
        await start.click();
        break;
    }
    check('export prepared every visual without warnings', warnings.length === 0, warnings.join(' | '));

    let outcome = null;
    const began = Date.now();
    while (!outcome && Date.now() - began < 420000) {
        await page.waitForTimeout(300);
        const s = await page.evaluate(() => ({
            ready: !document.querySelector('.export-ready').hidden,
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null
        }));
        if (s.ready) outcome = 'ready';
        else if (s.error) outcome = 'error: ' + s.error;
    }
    check('export finished with the video ready', outcome === 'ready', `${outcome}; ${Math.round((Date.now() - began) / 1000)}s`);
    const recorded = await page.evaluate(() => window.visualSamples);
    const strip = url => (url || '').split('?')[0];
    check('the export showed the same visuals as the preview',
        strip(recorded[0] && recorded[0].video) === strip(shown[0]) && recorded[1] && recorded[1].chart
        && strip(recorded[2] && recorded[2].video) === strip(shown[2]) && strip(recorded[3] && recorded[3].image) === strip(shown[3]),
        Object.entries(recorded).map(([i, r]) => `${+i + 1}: ${[strip(r.video), strip(r.image), r.chart ? 'chart' : ''].filter(Boolean).join(' + ') || '?'}`).join(', '));
    check('no AI request was made during the export (the generated video was reused)', aiCalls.video === 1 && aiCalls.image === 0);

    const used = (await api('GET', `/api/assets/${clipId}`, token)).data.used_in.filter(u => u.project_id === projectId).map(u => u.field);
    check('the chosen library clip is recorded as used by the lesson, once', used.length === 1 && used[0] === 'scenes[0].visual_plan.main', used.join(', '));

    // ---- The downloaded file ----------------------------------------------------------------------
    const [download] = await Promise.all([page.waitForEvent('download'), page.click('.export-ready button:has-text("Download Video")')]);
    const file = path.join(OUT, download.suggestedFilename());
    await download.saveAs(file);
    const probe = JSON.parse(run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', file]).stdout);
    const duration = parseFloat(probe.format.duration);
    check('downloaded a video with sound', probe.streams.some(s => s.codec_type === 'video') && probe.streams.some(s => s.codec_type === 'audio'),
        `${download.suggestedFilename()}, ${duration.toFixed(1)}s`);
    const log = await page.evaluate(() => window.exportSceneLog || []);
    console.log(`NOTE  scene start times in the recording: ${log.map((s, i) => `${i + 1}@${s.t.toFixed(1)}s`).join(' ')}`);
    // Late in each scene: its visual fades in during the first seconds
    const at = i => (i + 1 < log.length ? log[i + 1].t : duration) - 0.8;
    if (log.length === 4) {
        const c1 = centreColour(file, at(0));
        const c3 = centreColour(file, at(2));
        check('the recording shows the blue library clip in scene 1', near(c1, BLUE), `centre rgb(${c1})`);
        check('the recording shows the magenta generated clip in scene 3', near(c3, MAGENTA), `centre rgb(${c3})`);
        [0, 1, 2, 3].forEach(i => frameAt(file, at(i), `frame-${i + 1}.jpg`));
    } else {
        check('the recording logged all four scenes', false, JSON.stringify(log));
    }

    check('no console errors, page errors or failed requests', problems.length === 0, problems.join(' | '));
    await context.close();
} finally {
    await browser.close();
    fs.rmSync(aiClip, { force: true });
}

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Screenshots, frames and the video: ${OUT}`);
process.exit(failed ? 1 : 0);
