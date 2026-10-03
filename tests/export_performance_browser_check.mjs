// Recording performance check and benchmark (Phase 7), in real Chrome against a separate, throwaway
// server whose AI providers are a local stand-in (AI_FAKE_PROVIDER=1): no real AI provider can be
// reached and the normal database, library and media folders are never touched.
//
// Exports a seven-scene lesson (a chart, a clip chosen in Visual Review, a removed picture, a quiz,
// Aadhi moving between placements) and checks the recording pipeline end to end: start card hidden
// and lesson playing before recording starts, reviewed and removed visuals, Aadhi and narration,
// start/stop, the glass blur off only while recording, a valid non-empty file that downloads and survives a reload, the Aadhi fallback not
// stuck, no AI generation; subtitles from the narration, chapters from the scenes and the MP4 copy
// (with the WebM kept). It also measures the run (capture settings, delivered frames, file size
// and bitrate, Aadhi stalls and fallbacks, page frame gaps, Chrome CPU time) for the benchmark:
//
//   EXPORT_TEST_FPS=60|30|24     recording frame rate for this run (default: the production setting)
//   EXPORT_TEST_BITRATE=8000000  video bitrate for this run (default: the production setting)
//   EXPORT_TEST_VIEWPORT=1920x1080  browser window size (default 1280x720)
//   EXPORT_TEST_MAX_HEIGHT=720   largest capture height for this run (default: the production cap)
//   EXPORT_TEST_LABEL=name       label of the run in the output
//
// Needs Chrome, ffmpeg + ffprobe, playwright-core (npm install --no-save playwright-core, or
// PLAYWRIGHT_CORE_DIR) and the repo's virtualenv (.venv). Uses the default admin login of a fresh
// database:  AADHI_PASSWORD=... node tests/export_performance_browser_check.mjs
// Silent: no audio output, browser speech stubbed, the captured tab's sound not played locally.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const FPS = process.env.EXPORT_TEST_FPS ? Number(process.env.EXPORT_TEST_FPS) : null;
const BITRATE = process.env.EXPORT_TEST_BITRATE ? Number(process.env.EXPORT_TEST_BITRATE) : null;
const [VIEW_W, VIEW_H] = (process.env.EXPORT_TEST_VIEWPORT || '1280x720').split('x').map(Number);
const MAX_HEIGHT = process.env.EXPORT_TEST_MAX_HEIGHT ? Number(process.env.EXPORT_TEST_MAX_HEIGHT) : null;
const LABEL = process.env.EXPORT_TEST_LABEL || [FPS ? `${FPS}fps` : 'default', BITRATE ? `${BITRATE / 1e6}Mbps` : ''].filter(Boolean).join('-');
const OUT = process.env.EXPORT_PERF_OUT || path.join(os.tmpdir(), 'aadhi-export-perf', LABEL);
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.EXPORT_PERF_PORT || 9870 + (process.pid % 30));
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

function run(cmd, args, options = {}) {
    const r = spawnSync(cmd, args, { encoding: 'utf8', maxBuffer: 1 << 26, ...options });
    if (r.status !== 0) throw new Error(`${cmd} failed: ${r.stderr}`);
    return r;
}

// ---- a throwaway server with the stand-in AI providers ----------------------------------------------
fs.rmSync(OUT, { recursive: true, force: true });
const data = path.join(OUT, 'server-data');
for (const dir of ['assets', 'exports', 'static']) fs.mkdirSync(path.join(data, dir), { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const serverLog = fs.openSync(path.join(OUT, 'server.log'), 'w');
const server = spawn(path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python'),
    ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
        cwd: REPO, stdio: ['ignore', serverLog, serverLog],
        env: {
            ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
            JWT_SECRET: 'perf-check-' + Math.random().toString(36).slice(2), AI_FAKE_PROVIDER: '1', AI_GENERATION_ENABLED: '1'
        }
    });
function stopServer() {
    if (server.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
    else server.kill('SIGTERM');
}
process.on('exit', stopServer);

async function waitForServer() {
    for (let i = 0; i < 120; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + path.join(OUT, 'server.log'));
        try { if ((await fetch(BASE + '/export.js')).ok) return; } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('the test server did not start');
}

async function api(method, route, token, body) {
    const res = await fetch(BASE + route, { method, headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined });
    return { status: res.status, data: await res.json().catch(() => null) };
}

// Narration: a quiet tone as long as the text would take to say, so the recording has real sound
function toneWav(seconds) {
    const rate = 16000, samples = Math.round(rate * seconds);
    const buf = Buffer.alloc(44 + samples * 2);
    buf.write('RIFF', 0); buf.writeUInt32LE(36 + samples * 2, 4); buf.write('WAVE', 8); buf.write('fmt ', 12);
    buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24);
    buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36);
    buf.writeUInt32LE(samples * 2, 40);
    for (let i = 0; i < samples; i++) buf.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 330 * i / rate) * 6000), 44 + i * 2);
    return buf;
}

function silentPage(opts) {
    try {
        if (opts.fps) localStorage.setItem('aadhi_export_fps', String(opts.fps)); else localStorage.removeItem('aadhi_export_fps');
        if (opts.bitrate) localStorage.setItem('aadhi_export_bitrate', String(opts.bitrate)); else localStorage.removeItem('aadhi_export_bitrate');
        if (opts.maxHeight) localStorage.setItem('aadhi_export_max_height', String(opts.maxHeight)); else localStorage.removeItem('aadhi_export_max_height');
    } catch (e) { /* storage unavailable */ }
    const devices = navigator.mediaDevices;
    if (devices && devices.getDisplayMedia) {
        const capture = devices.getDisplayMedia.bind(devices);
        devices.getDisplayMedia = (constraints = {}) => capture({ ...constraints,
            audio: constraints.audio ? { ...(typeof constraints.audio === 'object' ? constraints.audio : {}), suppressLocalAudioPlayback: true } : false });
    }
    if (window.speechSynthesis) {
        window.speechSynthesis.speak = u => setTimeout(() => { if (u.onstart) u.onstart(new Event('start')); setTimeout(() => u.onend && u.onend(new Event('end')), 300); }, 0);
        window.speechSynthesis.cancel = () => {};
    }
}

// Longest stretch (seconds) in which the given 360-px-wide column hardly changes between frames
function longestStill(file, start, seconds, x) {
    const log = path.join(OUT, 'ydif.txt');
    spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-ss', String(start), '-t', String(seconds), '-i', path.basename(file),
        '-vf', `scale=1280:720,crop=360:720:${Math.round(x * 1280 / VIEW_W)}:0,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=ydif.txt`, '-f', 'null', '-'],
    { cwd: path.dirname(file), encoding: 'utf8' });
    let longest = 0, stillSince = null, t = 0;
    for (const line of fs.readFileSync(log, 'utf8').split('\n')) {
        const time = /pts_time:([\d.]+)/.exec(line);
        if (time) t = parseFloat(time[1]);
        const diff = /YDIF=([\d.]+)/.exec(line);
        if (!diff) continue;
        if (parseFloat(diff[1]) < 0.05) { stillSince = stillSince === null ? t : stillSince; longest = Math.max(longest, t - stillSince); }
        else stillSince = null;
    }
    return longest;
}

function fileFacts(file) {
    const info = JSON.parse(run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration,bit_rate:stream=codec_type,codec_name,width,height,avg_frame_rate',
        '-of', 'json', file]).stdout);
    const frames = parseInt(run('ffprobe', ['-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', file]).stdout, 10);
    const vol = spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-i', file, '-vn', '-af', 'volumedetect', '-f', 'null', '-'], { encoding: 'utf8' }).stderr;
    const video = info.streams.find(s => s.codec_type === 'video') || {};
    const audio = info.streams.find(s => s.codec_type === 'audio');
    const duration = parseFloat(info.format.duration);
    return { duration, sizeBytes: fs.statSync(file).size, frames, effectiveFps: frames / duration, width: video.width, height: video.height,
        videoCodec: video.codec_name, audioCodec: audio && audio.codec_name, audioPeakDb: parseFloat((/max_volume: ([-\d.]+)/.exec(vol) || [])[1]),
        bitrateMbps: fs.statSync(file).size * 8 / duration / 1e6 };
}

const tag = Date.now().toString(36);
const clip = path.join(OUT, `clip-${tag}.mp4`);
run('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=10', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', clip]);
const narr = text => text;
const scenes = [
    { type: 'title', concept_id: 'c1', title: 'MEASURING EXPORTS', subtitle: 'A recording benchmark', aadhi_position: 'left',
      narration: narr('Welcome. This short lesson measures how smoothly the export records Aadhi and every visual.') },
    { type: 'content', concept_id: 'c1', title: 'LOAD DATA', aadhi_position: 'right', html: '<p>Loads grow with traffic.</p><p>Stress follows the load.</p>',
      side_panel: { type: 'chart', chart_type: 'bar', title: 'Load (kN)', data: { labels: ['Light', 'Medium', 'Heavy'], datasets: [{ label: 'kN', data: [3, 7, 12] }] } },
      narration: narr('[SYNC] Loads grow with traffic on the bridge. [SYNC] The stress in each cable follows the load, as the chart shows.') },
    { type: 'ai_video', concept_id: 'c1', title: 'THE HARBOUR', aadhi_position: 'left', prompt: `container ships in a busy harbour ${tag}`,
      narration: narr('Container ships unload their cargo while cranes move above the quay.') },
    { type: 'content', concept_id: 'c2', title: 'THE LIGHTHOUSE', aadhi_position: 'right', html: '<p>A light that guides ships at night.</p>',
      side_panel: { type: 'image', prompt: `a lighthouse in a storm ${tag}` }, narration: narr('The lighthouse guides the ships safely into the harbour at night.') },
    { type: 'content', concept_id: 'c2', title: 'KEY POINTS', aadhi_position: 'popup_bottom_right', html: '<ul><li>Load</li><li>Stress</li><li>Safety</li></ul>',
      narration: narr('[SYNC] Load. [SYNC] Stress. [SYNC] And safety margins that keep every structure standing.') },
    { type: 'quiz_checkpoint', concept_id: 'c2', title: 'CHECKPOINT', aadhi_position: 'center', question: 'What does stress follow?',
      options: ['The load', 'The colour'], correct_index: 0, narration: narr('Quick check: what does the stress in a cable follow?'), reveal_narration: 'It follows the load.' },
    { type: 'content', concept_id: 'c2', title: 'WRAP UP', aadhi_position: 'left', html: '<p>Well done.</p>', side_panel: { type: 'skill_tree' },
      narration: narr('Well done. You have seen loads, stress and how a harbour keeps ships safe.') }
];
const AADHI_X = { left: Math.round(100 * VIEW_W / 1280), right: Math.round(880 * VIEW_W / 1280) };

const problems = [];
const metrics = { label: LABEL, requestedFps: FPS, requestedBitrate: BITRATE };
let browser;
try {
    await waitForServer();
    const token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const stats = async () => (await api('GET', '/api/ai-cache/stats', token)).data;
    const aiBefore = await stats();

    const form = new FormData();
    form.append('file', new Blob([fs.readFileSync(clip)]), `clip-${tag}.mp4`);
    const clipId = (await (await fetch(BASE + '/api/assets', { method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: form })).json()).asset.id;
    const projectId = (await api('POST', '/save-history', token, { subject_name: 'Export benchmark', session_title: 'Recording performance',
        concept_map: [{ id: 'c1', title: 'Loads' }, { id: 'c2', title: 'Safety' }], scenes })).data.id;
    await api('POST', '/api/visuals/review', token, { project_id: projectId, scene_index: 2, slot: 'main', action: 'choose', asset_id: clipId });
    await api('POST', '/api/visuals/review', token, { project_id: projectId, scene_index: 3, slot: 'side', action: 'remove' });

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true,
        args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'],
        ignoreDefaultArgs: ['--mute-audio'] });
    const context = await browser.newContext({ viewport: { width: VIEW_W, height: VIEW_H }, acceptDownloads: true });
    await context.addInitScript(t => localStorage.setItem('jwt_token', t), token);
    await context.addInitScript(silentPage, { fps: FPS, bitrate: BITRATE, maxHeight: MAX_HEIGHT });
    await context.route('**/generate-audio', r => {
        const text = (r.request().postDataJSON() || {}).text || '';
        const seconds = Math.min(7, Math.max(3, text.length * 0.06));
        return r.fulfill({ json: { status: 'success', audio_url: `/__tone.wav?d=${seconds.toFixed(2)}&n=${Math.random()}` } });
    });
    await context.route('**/__tone.wav*', r => r.fulfill({ status: 200, contentType: 'audio/wav', body: toneWav(parseFloat(new URL(r.request().url()).searchParams.get('d'))) }));
    const generatorRequests = [];
    const page = await context.newPage();
    page.on('pageerror', e => problems.push(`page error: ${e.message}`));
    page.on('console', m => {
        if (m.type() === 'error' && !(m.location().url || '').endsWith('/favicon.ico') && !m.text().startsWith('Logo video play error AbortError')) problems.push(`console: ${m.text()}`);
    });
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
    page.on('request', r => { if (/\/generate-ai-(video|image)$/.test(r.url())) generatorRequests.push(r.url()); });

    await page.goto(`${BASE}/?project_id=${projectId}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    // What the page shows the moment the recorder starts, and each scene's final visual (in-page, every 50 ms)
    await page.evaluate(() => {
        window.perfSamples = {};
        window.perfAtStart = null;
        setInterval(() => {
            if (!exportFlow.recording) return;
            if (!window.perfBlur) window.perfBlur = getComputedStyle(document.getElementById('presentation-board')).backdropFilter;
            const overlay = document.getElementById('start-overlay');
            if (!window.perfAtStart) {
                window.perfAtStart = window.exportRecordingState || {
                    startCardHidden: getComputedStyle(overlay).display === 'none' || getComputedStyle(overlay).opacity === '0',
                    playing: !!(isIntroRunning || (document.body.classList.contains('presentation-active') && ttsState.isPlaying && !document.getElementById('intro-logo-video').paused)),
                    polled: true
                };
            }
            if (!window.exportStartTime || isIntroRunning) return;
            const video = document.getElementById('active-ai-video');
            const img = document.getElementById('side-image-display');
            const s = mascot.getStatus();
            window.perfSamples[currentSlide] = {
                video: video && video.readyState >= 2 && video.offsetParent !== null ? video.getAttribute('src') : null,
                image: document.getElementById('side-image-panel').classList.contains('active') && img.naturalWidth > 0 ? img.getAttribute('src') : null,
                chart: document.getElementById('side-chart-panel').classList.contains('active'),
                conceptMap: document.getElementById('concept-map-panel').classList.contains('active'),
                mascotPlaying: s.playback === 'playing' || /play/i.test(String(s.playback)), narration: s.audio
            };
        }, 50);
    });

    const cdp = await browser.newBrowserCDPSession();
    const cpuTime = async () => (await cdp.send('SystemInfo.getProcessInfo')).processInfo.reduce((sum, p) => sum + p.cpuTime, 0);

    await page.click('#start-videos-btn');
    await page.click('.export-start-btn');
    const warnings = [];
    let cpuStart = null, clickedAt = null;
    for (;;) {
        const startBtn = page.locator('.export-actions button', { hasText: 'Start Recording' });
        const anyway = page.locator('.export-actions button', { hasText: 'Record anyway' });
        await Promise.race([startBtn.waitFor({ timeout: 300000 }), anyway.waitFor({ timeout: 300000 })]);
        if (await anyway.isVisible()) { warnings.push(await page.textContent('.export-message')); await anyway.click(); continue; }
        cpuStart = await cpuTime();
        clickedAt = Date.now();
        await startBtn.click();
        break;
    }
    let outcome = null;
    const statuses = new Set();
    let recordingEndedAt = null;
    while (!outcome && Date.now() - clickedAt < 420000) {
        await page.waitForTimeout(250);
        const s = await page.evaluate(() => ({
            ready: !document.querySelector('.export-ready').hidden, recording: exportFlow.recording, status: exportFlow.job && exportFlow.job.status,
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null
        }));
        statuses.add(s.status);
        if (!s.recording && statuses.has('RECORDING') && !recordingEndedAt) recordingEndedAt = Date.now();
        if (s.ready) outcome = 'ready'; else if (s.error) outcome = 'error: ' + s.error;
    }
    const cpuEnd = await cpuTime();
    const exportSeconds = (Date.now() - clickedAt) / 1000;
    // The page's own record of the recorder's start (index.html recordingStarted), else the in-page sample
    const page_ = await page.evaluate(() => ({ metrics: exportFlow.lastMetrics, atStart: window.exportRecordingState || window.perfAtStart, samples: window.perfSamples,
        sceneLog: window.exportSceneLog || [], job: exportFlow.job, mascotNow: mascot.getStatus(),
        blurDuring: window.perfBlur, blurAfter: getComputedStyle(document.getElementById('presentation-board')).backdropFilter }));
    const m = page_.metrics || {};
    check('recording starts and stops, and the export finishes', outcome === 'ready' && m.recorder && m.recorder.startLatencyMs !== null && m.recorder.stopLatencyMs !== null,
        `${outcome}; start ${m.recorder && m.recorder.startLatencyMs} ms, stop ${m.recorder && m.recorder.stopLatencyMs} ms`);
    check('the start card is hidden before recording starts', page_.atStart && page_.atStart.startCardHidden, JSON.stringify(page_.atStart));
    check('the lesson is already playing when recording starts', page_.atStart && page_.atStart.playing && page_.atStart.logoPlaying !== false, JSON.stringify(page_.atStart));
    check('recording mode drops the glass blur while recording and restores it after', page_.blurDuring === 'none' && /blur/.test(page_.blurAfter),
        `during ${page_.blurDuring}, after ${page_.blurAfter}`);
    const strip = url => (url || '').split('?')[0];
    const sm = page_.samples || {};
    check('the visual chosen in Visual Review is recorded', sm[2] && strip(sm[2].video).includes(`/api/assets/${clipId}/`), strip(sm[2] && sm[2].video));
    check('the visual removed in Visual Review stays absent', sm[3] && !sm[3].image && sm[3].conceptMap);
    check('Aadhi plays while recording', m.mascot && m.mascot.switches > 0 && Object.values(sm).some(s => s.mascotPlaying), `${m.mascot && m.mascot.switches} clip switches`);
    check('the Aadhi fallback does not stay on', m.mascot && !page_.mascotNow.fallback && !m.mascot.inFallback,
        `${m.mascot && m.mascot.fallbacks} fallback(s), ${m.mascot && m.mascot.fallbackMs} ms in fallback`);
    check('no AI generation during the export', generatorRequests.length === 0 && (await stats()).provider_calls.video === aiBefore.provider_calls.video
        && (await stats()).provider_calls.image === aiBefore.provider_calls.image);

    // ---- the file ---------------------------------------------------------------------------------------
    const job = page_.job;
    const [download] = await Promise.all([page.waitForEvent('download'), page.click('.export-ready button:has-text("Download Video")')]);
    const file = path.join(OUT, download.suggestedFilename());
    await download.saveAs(file);
    const facts = fileFacts(file);
    check('the final file exists, is not empty and downloads whole', fs.existsSync(file) && facts.sizeBytes > 0 && facts.sizeBytes === job.file_size,
        `${(facts.sizeBytes / 1048576).toFixed(1)} MB`);
    check('narration is in the recording', facts.audioPeakDb > -40, `peak ${facts.audioPeakDb} dB`);
    const log = page_.sceneLog;
    const still = log.map((s, i) => ({ i, pos: scenes[i] && scenes[i].aadhi_position, from: s.t + 1, to: (log[i + 1] ? log[i + 1].t : facts.duration) - 1 }))
        .filter(s => AADHI_X[s.pos] && s.to > s.from)
        .map(s => ({ scene: s.i + 1, seconds: longestStill(file, s.from, s.to - s.from, AADHI_X[s.pos]) }));
    const longest = Math.max(0, ...still.map(s => s.seconds));
    check('Aadhi keeps moving in the recorded video (never still for 1 s in his scenes)', still.length >= 3 && longest < 1,
        still.map(s => `scene ${s.scene}: ${s.seconds.toFixed(2)}s`).join(', '));

    // ---- subtitles, chapters and the MP4 copy (made by the server after the upload) ----------------------
    let latest = null;
    for (let i = 0; i < 240; i++) {
        latest = (await api('GET', `/api/exports/${job.id}`, token)).data;
        if (latest.outputs && latest.outputs.mp4 && !['pending', 'processing'].includes(latest.outputs.mp4.status)) break;
        await new Promise(r => setTimeout(r, 1000));
    }
    const outputUrls = (await api('POST', `/api/exports/${job.id}/link`, token)).data.output_urls || {};
    const vtt = outputUrls.vtt ? await (await fetch(BASE + outputUrls.vtt)).text() : '';
    const secs = (m, i) => Number(m[i]) * 3600 + Number(m[i + 1]) * 60 + Number(m[i + 2]) + Number(m[i + 3]) / 1000;
    const cueTimes = [...vtt.matchAll(/(\d{2}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2}):(\d{2}):(\d{2})\.(\d{3})/g)].map(m => [secs(m, 1), secs(m, 5)]);
    const orderedCues = cueTimes.every(([s, e], i) => s < e && (i === 0 || s >= cueTimes[i - 1][1]));
    check('subtitles come from the narration, in order, inside the video', vtt.startsWith('WEBVTT') && cueTimes.length >= 6 && orderedCues
        && cueTimes[cueTimes.length - 1][1] <= facts.duration + 0.05 && vtt.includes('Container ships unload their cargo'),
        `${cueTimes.length} cues`);
    const chapters = outputUrls.chapters ? await (await fetch(BASE + outputUrls.chapters)).text() : '';
    const chapterLines = chapters.trim().split('\n');
    const toSeconds = stamp => stamp.split(':').reduce((a, b) => a * 60 + Number(b), 0);
    const chapterTimes = chapterLines.map(l => toSeconds(l.split(' ')[0]));
    check('chapters follow the scenes, from 00:00, in order', chapterLines[0].startsWith('00:00 ') && chapterTimes.every((s, i) => i === 0 || s > chapterTimes[i - 1])
        && ['LOAD DATA', 'THE HARBOUR', 'WRAP UP'].every(title => chapters.includes(title)), chapterLines.join(' | '));
    const mp4Status = latest.outputs && latest.outputs.mp4 && latest.outputs.mp4.status;
    if (mp4Status === 'ready') {
        const mp4File = path.join(OUT, 'export.mp4');
        fs.writeFileSync(mp4File, Buffer.from(await (await fetch(BASE + outputUrls.mp4)).arrayBuffer()));
        const mp4 = JSON.parse(run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration:stream=codec_name', '-of', 'json', mp4File]).stdout);
        const codecs = new Set(mp4.streams.map(s => s.codec_name));
        check('the MP4 copy is H.264/AAC with subtitles, as long as the WebM', codecs.has('h264') && codecs.has('aac') && codecs.has('mov_text')
            && Math.abs(parseFloat(mp4.format.duration) - facts.duration) < 2, `${[...codecs].join(', ')}, ${parseFloat(mp4.format.duration).toFixed(1)}s`);
        await page.waitForFunction(() => /MP4/.test(document.querySelector('.export-ready .export-actions').textContent), null, { timeout: 30000 }).catch(() => {});
        const offered = await page.$$eval('.export-ready .export-actions button', bs => bs.map(b => b.textContent));
        check('the ready panel offers the video, the MP4, subtitles and chapters', ['Download Video', 'MP4', 'Subtitles', 'Chapters'].every(l => offered.some(o => o.includes(l))),
            offered.join(' · '));
    } else {
        check('the MP4 copy is H.264/AAC with subtitles, as long as the WebM', mp4Status === 'unavailable', `MP4 ${mp4Status}: ${latest.outputs && latest.outputs.mp4 && latest.outputs.mp4.error}`);
    }
    metrics.outputs = { mp4: mp4Status, cues: cueTimes.length, chapters: chapterLines.length };

    await page.reload();
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.click('#start-videos-btn');
    const listed = page.locator('.export-history-item', { hasText: 'Export benchmark' }).filter({ has: page.locator('.export-badge[data-status="COMPLETED"]') }).first();
    await listed.waitFor({ timeout: 30000 });
    const linkAfter = await api('POST', `/api/exports/${job.id}/link`, token);
    const firstBytes = linkAfter.status === 200 ? await fetch(BASE + linkAfter.data.download_url, { headers: { Range: 'bytes=0-1023' } }) : null;
    check('the export survives a reload (listed, still downloadable)', firstBytes && firstBytes.status < 400
        && (await firstBytes.arrayBuffer()).byteLength > 0, `link ${linkAfter.status}, download ${firstBytes && firstBytes.status}`);

    Object.assign(metrics, {
        trackSettings: m.recorder && m.recorder.track, mimeType: m.recorder && m.recorder.mimeType,
        videoBitsPerSecond: m.recorder && m.recorder.videoBitsPerSecond, chunks: m.recorder && m.recorder.chunks,
        startLatencyMs: m.recorder && m.recorder.startLatencyMs, stopLatencyMs: m.recorder && m.recorder.stopLatencyMs,
        recordedMs: m.recorder && m.recorder.durationMs, page: m.page, file: facts, longestStillSeconds: longest, still,
        mascot: m.mascot && { switches: m.mascot.switches, fallbacks: m.mascot.fallbacks, fallbackReasons: m.mascot.fallbackReasons, fallbackMs: m.mascot.fallbackMs,
            stalls: m.mascot.stalls, waiting: m.mascot.waiting, stalledEvents: m.mascot.stalledEvents, reloads: m.mascot.reloads, playFailures: m.mascot.playFailures,
            stallEvents: m.mascot.events.filter(e => ['stall', 'fallback', 'recovered', 'waiting', 'stalled', 'reload'].includes(e.type)) },
        cpuSeconds: cpuStart !== null ? Math.round((cpuEnd - cpuStart) * 10) / 10 : null, exportSeconds: Math.round(exportSeconds),
        uploadAndSaveSeconds: recordingEndedAt ? Math.round((Date.now() - recordingEndedAt) / 1000) : null, warnings, sceneStarts: log.map(s => Math.round(s.t * 10) / 10)
    });
    fs.writeFileSync(path.join(OUT, 'metrics.json'), JSON.stringify(metrics, null, 2));
    const mm = metrics.mascot || {};
    console.log(`RESULT ${JSON.stringify({ label: LABEL, fps: FPS || 'default', track: metrics.trackSettings && `${metrics.trackSettings.width}x${metrics.trackSettings.height}@${metrics.trackSettings.frameRate}`,
        duration: +facts.duration.toFixed(1), frames: facts.frames, effectiveFps: +facts.effectiveFps.toFixed(1), sizeMB: +(facts.sizeBytes / 1048576).toFixed(1),
        bitrateMbps: +facts.bitrateMbps.toFixed(2), stalls: mm.stalls, fallbacks: mm.fallbacks, fallbackMs: mm.fallbackMs, waiting: mm.waiting,
        longestStill: +longest.toFixed(2), pageFps: m.page && m.page.fps, longFrames: m.page && m.page.longFrames, maxGapMs: m.page && m.page.maxGapMs,
        cpuSeconds: metrics.cpuSeconds, exportSeconds: metrics.exportSeconds })}`);

    check('no console errors, page errors or failed requests', problems.length === 0, problems.join(' | '));
    await context.close();
} catch (err) {
    check('the check ran to the end', false, err.stack || String(err));
} finally {
    if (browser) await browser.close();
    stopServer();
}

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Metrics, video and server log: ${OUT}`);
process.exit(failed ? 1 : 0);
