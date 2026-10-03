// End-to-end test of lesson video export in a real Chrome:
// LESSON -> RECORD -> UPLOAD -> PERSIST -> REFRESH -> PREVIEW -> DOWNLOAD -> INSPECT THE FILE,
// plus a cancelled screen share and a second account that must not reach the video.
//
// Needs: the server running (with the virtualenv on PATH so Manim can render), Chrome,
// ffmpeg + ffprobe, playwright-core (npm install --no-save playwright-core), and the login of an
// account allowed to create users (admin), to check ownership with a second account:
//   AADHI_USER=admin AADHI_PASSWORD=... node tests/export_e2e.mjs [http://127.0.0.1:9942]
// Chrome runs headless with --auto-accept-this-tab-capture, which answers the browser's
// "share this tab" dialog the way a person would: this tab, with its audio.
// Downloads and extracted frames go to $EXPORT_E2E_OUT (default <tmp>/aadhi-export-e2e).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.dirname(here);
const BASE = (process.argv[2] || 'http://127.0.0.1:9942').replace(/\/$/, '');
const OUT = process.env.EXPORT_E2E_OUT || path.join(os.tmpdir(), 'aadhi-export-e2e');
const USER = process.env.AADHI_USER || 'admin';
const PASSWORD = process.env.AADHI_PASSWORD;
const lesson = JSON.parse(fs.readFileSync(path.join(here, 'fixtures', 'export_lesson.json'), 'utf8'));

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
    const r = spawnSync(cmd, args, { encoding: 'utf8' });
    if (r.status !== 0) throw new Error(`${cmd} failed: ${r.stderr}`);
    return r;
}

// Media the lesson points at: an image, and a clip standing in for a generated AI video
function makeFixtureMedia() {
    const dir = path.join(REPO, 'static_videos');
    fs.mkdirSync(dir, { recursive: true });
    const image = path.join(dir, 'e2e_diagram.png');
    const clip = path.join(dir, 'e2e_ai_clip.mp4');
    if (!fs.existsSync(image)) run('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'mandelbrot=size=640x360', '-frames:v', '1', image]);
    if (!fs.existsSync(clip)) {
        run('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=6', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', clip]);
    }
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
    return { status: res.status, data: await res.json().catch(() => null), res };
}

async function login(username, password) {
    const res = await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username, password }) });
    return (await res.json()).access_token;
}

// Opens the lesson from its History link, as a person would. It is not played first: the export
// plays it (and the test stays silent until the recording, whose sound is not played locally).
async function openLesson(page, projectId) {
    await page.goto(`${BASE}/?project_id=${projectId}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 20000 });
}

// Export this lesson from its start screen ("Exported videos of this lesson") up to the moment recording starts
async function startExport(page) {
    await page.click('#start-videos-btn');
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
        return warnings;
    }
}

function ffprobe(file) {
    const r = run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height', '-of', 'json', file]);
    const data = JSON.parse(r.stdout);
    const video = data.streams.find(s => s.codec_type === 'video');
    const audio = data.streams.find(s => s.codec_type === 'audio');
    return { duration: parseFloat(data.format.duration), video, audio };
}

function loudness(file, start, seconds) {
    const args = ['-hide_banner', '-nostats'];
    if (start !== undefined) args.push('-ss', String(start), '-t', String(seconds));
    args.push('-i', file, '-map', '0:a', '-af', 'volumedetect', '-f', 'null', '-');
    const r = spawnSync('ffmpeg', args, { encoding: 'utf8' });
    const mean = /mean_volume: (-?[\d.]+|-inf) dB/.exec(r.stderr);
    const max = /max_volume: (-?[\d.]+|-inf) dB/.exec(r.stderr);
    return { mean: mean ? parseFloat(mean[1]) : -Infinity, max: max ? parseFloat(max[1]) : -Infinity };
}

// Longest stretch (seconds) in which a vertical strip of the video does not change, from ffmpeg's
// per-frame difference (YDIF). Used on Aadhi's area: a frozen mascot shows up as a long still stretch.
function longestStill(file, start, seconds, x) {
    const log = 'ydif.txt';
    spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-ss', String(start), '-t', String(seconds), '-i', path.basename(file),
        '-vf', `crop=360:720:${x}:0,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=${log}`, '-f', 'null', '-'],
    { cwd: path.dirname(file), encoding: 'utf8' });
    let longest = 0;
    let stillSince = null;
    let t = 0;
    for (const line of fs.readFileSync(path.join(path.dirname(file), log), 'utf8').split('\n')) {
        const time = /pts_time:([\d.]+)/.exec(line);
        if (time) t = parseFloat(time[1]);
        const diff = /YDIF=([\d.]+)/.exec(line);
        if (!diff) continue;
        if (parseFloat(diff[1]) < 0.05) {
            stillSince = stillSince === null ? t : stillSince;
            longest = Math.max(longest, t - stillSince);
        } else {
            stillSince = null;
        }
    }
    return longest;
}

function frameAt(file, seconds, name) {
    const out = path.join(OUT, name);
    run('ffmpeg', ['-v', 'error', '-y', '-ss', String(seconds), '-i', file, '-frames:v', '1', '-vf', 'scale=640:-1', out]);
    return out;
}

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
makeFixtureMedia();
const t0 = Date.now();
const { chromium } = await loadPlaywright();
// Silent for whoever runs the test: Chrome's own mute cannot be used (a muted tab records pure
// silence), so nothing is sent to the speakers (--disable-audio-output) and the captured tab's
// sound is not played locally (suppressLocalAudioPlayback, see silentPage); the recording still
// gets the full sound.
const browser = await chromium.launch({
    channel: 'chrome', headless: true,
    args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'],
    ignoreDefaultArgs: ['--mute-audio']
});

// Runs in every test page before the app: capture without local playback, and no browser speech
// (Windows speaks speechSynthesis itself, outside Chrome's audio)
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

try {
    // ---- 1. Log in through the page's own form, save the lesson as a project -------------
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, acceptDownloads: true });
    await context.addInitScript(silentPage);
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', e => pageErrors.push(e.message));
    await page.goto(BASE + '/');
    await page.fill('#auth-username', USER);
    await page.fill('#auth-password', PASSWORD);
    await page.click('.auth-btn');
    await page.waitForFunction(() => !!localStorage.getItem('jwt_token'), null, { timeout: 15000 });
    const token = await page.evaluate(() => localStorage.getItem('jwt_token'));
    check('logged in through the login form', !!token);

    // E2E_USE_ASSETS=1: the lesson refers to its picture and AI clip only by asset-library ID
    let libraryIds = null;
    if (process.env.E2E_USE_ASSETS === '1') {
        const upload = async file => {
            const form = new FormData();
            form.append('file', new Blob([fs.readFileSync(file)]), path.basename(file));
            const res = await fetch(BASE + '/api/assets', { method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: form });
            return (await res.json()).asset.id;
        };
        libraryIds = {
            image: await upload(path.join(REPO, 'static_videos', 'e2e_diagram.png')),
            clip: await upload(path.join(REPO, 'static_videos', 'e2e_ai_clip.mp4'))
        };
        const scenes = lesson.scenes;
        scenes[1].html = scenes[1].html.replace('src="/static/e2e_diagram.png"', `src="asset:${libraryIds.image}"`);
        delete scenes[3].video_url;
        scenes[3].video_asset_id = libraryIds.clip;
        check('lesson refers to its picture and AI clip by asset ID only',
            scenes[1].html.includes(`asset:${libraryIds.image}`) && !JSON.stringify(scenes).includes('/static/e2e_'), `image ${libraryIds.image.slice(0, 8)}…, clip ${libraryIds.clip.slice(0, 8)}…`);
    }

    const projectId = await page.evaluate(async data => {
        const res = await fetch('/save-history', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        return (await res.json()).id;
    }, lesson);
    if (libraryIds) {
        const used = await Promise.all(Object.values(libraryIds).map(id => api('GET', `/api/assets/${id}`, token)));
        check('saving the lesson recorded that it uses both library assets',
            used.every(r => r.data.used_in.some(u => u.project_id === projectId)), used.map(r => r.data.used_in.map(u => u.field).join(',')).join(' | '));
        const refused = await api('DELETE', `/api/assets/${libraryIds.clip}`, token);
        check('a library asset used by the lesson cannot be deleted', refused.status === 409);
    }
    await openLesson(page, projectId);
    check('History link opens the saved lesson', await page.evaluate(n => slides.length === n, lesson.scenes.length), `project ${projectId}`);

    // ---- 2. Export: prepare, record the whole lesson, upload, save ------------------------
    const warnings = await startExport(page);
    check('every lesson asset was prepared before recording', warnings.length === 0, warnings.join(' | '));
    const recordingStarted = Date.now();
    const seen = { titles: new Set(), placements: new Set(), states: new Set(), fallback: new Set(), statuses: new Set(), panelVisibleWhileRecording: false };
    let outcome = null;
    while (!outcome && Date.now() - recordingStarted < 420000) {
        await page.waitForTimeout(500);
        const s = await page.evaluate(() => ({
            title: document.title,
            ready: !document.querySelector('.export-ready').hidden,
            error: document.querySelector('.export-message').getAttribute('data-kind') === 'error' ? document.querySelector('.export-message').textContent : null,
            recording: exportFlow.recording,
            panelHidden: document.querySelector('.export-overlay').classList.contains('recording-hidden'),
            status: exportFlow.job && exportFlow.job.status,
            mascot: mascot.getStatus()
        }));
        seen.titles.add(s.title);
        seen.statuses.add(s.status);
        if (s.recording) {
            seen.placements.add(s.mascot.placement);
            seen.states.add(s.mascot.state);
            if (s.mascot.fallback) seen.fallback.add(`${s.mascot.fallback} (${s.mascot.placement}, error: ${s.mascot.lastError})`);
            if (!s.panelHidden) seen.panelVisibleWhileRecording = true;
        }
        if (s.ready) outcome = 'ready';
        else if (s.error) outcome = 'error: ' + s.error;
    }
    const pipelineSeconds = Math.round((Date.now() - recordingStarted) / 1000);
    await page.screenshot({ path: path.join(OUT, '1-video-ready.png') });
    const job = await page.evaluate(() => exportFlow.job);
    const sceneLog = await page.evaluate(() => window.exportSceneLog || []);
    check('export finished with the video ready', outcome === 'ready', `${outcome}; ${pipelineSeconds}s from Start Recording`);
    check('status went PREPARING → RECORDING → UPLOADING → COMPLETED',
        ['RECORDING', 'UPLOADING', 'COMPLETED'].every(st => seen.statuses.has(st)), [...seen.statuses].join(' '));
    check('tab title showed recording progress', [...seen.titles].some(t => /^● Recording \d+\/6/.test(t)), [...seen.titles].filter(t => t.startsWith('●')).slice(-1)[0]);
    check('export panel stayed out of the recording', !seen.panelVisibleWhileRecording);
    check('Aadhi was on screen while recording', seen.placements.has('left') && seen.placements.has('right'), [...seen.placements].join(' '));
    if (seen.fallback.size) {
        // Phase 1's animated fallback stepping in for a clip that stalled (e.g. CPU-starved headless encoding)
        console.log(`NOTE  mascot fallback was used while recording: ${[...seen.fallback].join(' | ')}`);
    }
    console.log(`NOTE  scene start times in the recording: ${sceneLog.map((s, i) => `${i + 1}@${s.t.toFixed(1)}s`).join(' ')}`);
    check('scenes were recorded in order, through the final scene',
        sceneLog.map(s => s.title).join('|') === lesson.scenes.map(s => s.title).join('|'), sceneLog.map(s => s.title).join(' > '));

    const record = (await api('GET', `/api/exports/${job.id}`, token)).data;
    if (libraryIds) {
        const played = await page.evaluate(() => ({ html: slides[1].html, video: slides[3].video_url }));
        check('the recorded lesson showed the library picture (resolved through the asset library)',
            played.html.includes(`/api/assets/${libraryIds.image}/content?token=`) && played.html.includes(`data-asset-src="asset:${libraryIds.image}"`));
        check('the recorded lesson played the library AI clip (resolved through the asset library)',
            (played.video || '').includes(`/api/assets/${libraryIds.clip}/content?token=`));
    }
    check('export record stored as COMPLETED', record && record.status === 'COMPLETED',
        record && `${record.file_name}, ${(record.file_size / 1048576).toFixed(1)} MB, ${record.duration_seconds}s, ${record.width}x${record.height}, audio ${record.has_audio}`);
    const stored = path.join(REPO, 'exports', String(fs.readdirSync(path.join(REPO, 'exports')).find(d => fs.existsSync(path.join(REPO, 'exports', d, `${job.id}.webm`)))), `${job.id}.webm`);
    check('video file persisted on the server', fs.existsSync(stored) && fs.statSync(stored).size === record.file_size, stored);
    check('download name is meaningful and safe', record.file_name === 'aadhi-eduengine-strength-of-materials-lesson-01-stress-in-structures.webm', record.file_name);

    // ---- 3. Preview from the stored file, then download ----------------------------------------
    await page.waitForFunction(() => {
        const v = document.querySelector('.export-preview');
        return v && v.readyState >= 1 && isFinite(v.duration) && v.duration > 0;
    }, null, { timeout: 30000 });
    const preview = await page.evaluate(async () => {
        const v = document.querySelector('.export-preview');
        v.muted = true;
        await v.play();
        await new Promise(r => setTimeout(r, 1500));
        const playedTo = v.currentTime;
        v.currentTime = v.duration / 2;
        await new Promise(r => v.addEventListener('seeked', r, { once: true }));
        v.pause();
        return { duration: v.duration, playedTo, seekedTo: v.currentTime, src: v.currentSrc };
    });
    check('preview plays the stored video', preview.playedTo > 0.5 && /\/api\/exports\/.+\/download\?token=.+&inline=1/.test(preview.src),
        `${preview.duration.toFixed(1)}s long, played to ${preview.playedTo.toFixed(1)}s`);
    check('preview can seek', Math.abs(preview.seekedTo - preview.duration / 2) < 1.5, `seeked to ${preview.seekedTo.toFixed(1)}s`);

    const [download] = await Promise.all([page.waitForEvent('download'), page.click('.export-ready button:has-text("Download Video")')]);
    const file = path.join(OUT, download.suggestedFilename());
    await download.saveAs(file);
    check('Download Video saves the file', fs.existsSync(file) && fs.statSync(file).size === record.file_size, download.suggestedFilename());

    // ---- 4. Refresh the browser, come back, preview and download again -------------------------
    await page.reload();
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 20000 });
    await page.click('#start-videos-btn');
    const item = page.locator('.export-history-item', { hasText: 'Stress in Structures' }).filter({ has: page.locator('.export-badge[data-status="COMPLETED"]') }).first();
    await item.waitFor({ timeout: 15000 });
    check('after a refresh the export is still listed', await item.isVisible(), await item.textContent());
    await item.locator('button', { hasText: 'Preview' }).click();
    await page.waitForFunction(() => { const v = document.querySelector('.export-preview'); return v && v.readyState >= 2; }, null, { timeout: 30000 });
    check('after a refresh the preview loads', true);
    await page.screenshot({ path: path.join(OUT, '2-after-refresh.png') });
    const [again] = await Promise.all([page.waitForEvent('download'), item.locator('button', { hasText: 'Download' }).click()]);
    const file2 = path.join(OUT, 'after-refresh-' + again.suggestedFilename());
    await again.saveAs(file2);
    check('after a refresh the download works', fs.statSync(file2).size === record.file_size);

    await page.goto(BASE + '/');
    await page.click('#open-videos-btn');
    await page.locator('.export-history-item', { hasText: 'Stress in Structures' }).first().waitFor({ timeout: 15000 });
    check('leaving the lesson and reopening My Videos still lists it', true);
    check('no page errors during the export', pageErrors.length === 0, pageErrors.join(' | '));

    // ---- 5. Cancelled screen share: CANCELLED, useful message, retry offered -------------------
    const cancelContext = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    await cancelContext.addInitScript(silentPage);
    await cancelContext.addInitScript(t => {
        localStorage.setItem('jwt_token', t);
        navigator.mediaDevices.getDisplayMedia = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
    }, token);
    const cancelPage = await cancelContext.newPage();
    await openLesson(cancelPage, projectId);
    await startExport(cancelPage);
    await cancelPage.waitForSelector('.export-message[data-kind="error"]', { timeout: 20000 });
    const cancelMessage = await cancelPage.textContent('.export-message');
    const cancelActions = await cancelPage.$$eval('.export-run .export-actions button', bs => bs.map(b => b.textContent));
    const cancelJob = await cancelPage.evaluate(() => exportFlow.job.id);
    const cancelRecord = (await api('GET', `/api/exports/${cancelJob}`, token)).data;
    check('cancelled screen share is marked CANCELLED with a clear message', cancelRecord.status === 'CANCELLED' && /cancelled/i.test(cancelMessage), cancelMessage);
    check('a cancelled export offers Retry export', cancelActions.includes('Retry export'), cancelActions.join(', '));
    await cancelContext.close();

    // ---- 6. Another account cannot reach the video ----------------------------------------------
    const otherName = 'e2e_viewer_' + Date.now();
    const otherPassword = 'viewer-' + Math.random().toString(36).slice(2);
    const registered = await api('POST', '/api/register', token, { username: otherName, password: otherPassword });
    const other = await login(otherName, otherPassword);
    const own = (await api('GET', `/api/exports/${job.id}/download`, token)).status;
    const asOther = [
        (await api('GET', `/api/exports/${job.id}`, other)).status,
        (await api('GET', `/api/exports/${job.id}/download`, other)).status,
        (await api('POST', `/api/exports/${job.id}/link`, other)).status
    ];
    const otherList = (await api('GET', '/api/exports', other)).data.exports.map(e => e.id);
    check('a second account cannot see, link or download the video', registered.status === 200 && asOther.every(s => s === 404) && !otherList.includes(job.id) && own === 200,
        `owner ${own}, other ${asOther.join('/')}`);
    check('no login, no download', (await api('GET', `/api/exports/${job.id}/download`)).status === 401);

    // ---- 7. Open the downloaded file outside the app ----------------------------------------------
    const info = ffprobe(file);
    check('downloaded file is a WebM with video and audio', info.video && info.audio && ['vp9', 'vp8'].includes(info.video.codec_name) && info.audio.codec_name === 'opus',
        `${info.video && info.video.codec_name} ${info.video && info.video.width}x${info.video && info.video.height} + ${info.audio && info.audio.codec_name}`);
    const lastScene = sceneLog[sceneLog.length - 1];
    check('file runs past the start of the final scene', info.duration > lastScene.t + 3, `${info.duration.toFixed(1)}s long; final scene starts at ${lastScene.t.toFixed(1)}s`);
    const overall = loudness(file);
    check('the file has real sound', overall.max > -20, `mean ${overall.mean} dB, peak ${overall.max} dB`);
    // Narration of scene 2 ("What is stress?") is audible while that scene is on screen
    const scene2 = sceneLog[1];
    const narration = loudness(file, scene2.t + 1.2, 3);
    check('narration is audible during its scene', narration.max > -25, `scene 2 at ${scene2.t.toFixed(1)}s: peak ${narration.max} dB`);

    // Aadhi's scenes in the lesson: title (left), "What is stress?" (right), the AI video (left)
    const still = [[0, 100], [1, 880], [3, 100]].map(([i, x]) => {
        const from = sceneLog[i].t + 1;
        const to = (sceneLog[i + 1] ? sceneLog[i + 1].t : info.duration) - 1;
        return { scene: i + 1, seconds: longestStill(file, from, to - from, x) };
    });
    check('Aadhi keeps moving in the recorded video (never still for 1 s in his scenes)', still.every(s => s.seconds < 1),
        still.map(s => `scene ${s.scene}: longest still ${s.seconds.toFixed(2)}s`).join(', '));

    const frames = sceneLog.map((s, i) => {
        const next = sceneLog[i + 1] ? sceneLog[i + 1].t : info.duration;
        return frameAt(file, Math.min(s.t + 3.5, (s.t + next) / 2), `frame-${i + 1}-${s.title.replace(/[^A-Za-z]+/g, '_')}.jpg`);
    });
    // Scene 2's side panel flips from the skill tree to its animated chart after 3 s
    frames.push(frameAt(file, Math.min(scene2.t + 6.5, sceneLog[2].t - 0.5), 'frame-2b-chart.jpg'));
    frames.push(frameAt(file, Math.max(0, info.duration - 1), 'frame-last.jpg'));
    // (Phase 21: compared by content; two different frames once had the same byte size, 19825 B)
    const digests = frames.map(f => createHash('sha256').update(fs.readFileSync(f)).digest('hex'));
    check('frames of every scene were extracted and differ from each other', new Set(digests).size === digests.length, frames.map(f => path.basename(f)).join(', '));
    console.log(`\nFiles for a visual check: ${OUT}`);
    await context.close();
} finally {
    await browser.close();
}

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed in ${Math.round((Date.now() - t0) / 1000)}s`);
process.exit(failed ? 1 : 0);
