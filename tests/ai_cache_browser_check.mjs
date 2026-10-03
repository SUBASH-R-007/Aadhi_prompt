// Browser check of the AI media cache (ai_cache.py) in real Chrome, against a separate, throwaway
// server whose AI providers are a local stand-in (AI_FAKE_PROVIDER=1): no real AI provider can be
// reached (the real ones refuse to run in that mode) and the normal database, library and media
// folders are never touched.
//
//   1. lesson A: the visual router plans an AI video and an AI image; the image is generated
//      automatically and the user clicks "Generate AI video" -> cache misses, one provider call each
//   2. lesson B asks for the same visuals: they are reused (same asset IDs), no generator request is
//      made and the providers are not called; asking the generator again is a cache hit
//   3. "New AI version" in the scene list: a new asset, exactly one more provider call, the old
//      version kept
//
// Needs Chrome, ffmpeg, playwright-core (npm install --no-save playwright-core, or PLAYWRIGHT_CORE_DIR)
// and the repo's virtualenv (.venv). Uses the default admin login of a fresh database:
//   AADHI_PASSWORD=... node tests/ai_cache_browser_check.mjs
// Silent: no audio output, browser speech stubbed, narration answered with silence.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.AI_CACHE_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-ai-cache-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.AI_CACHE_CHECK_PORT || 9950 + (process.pid % 40));
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

// A throwaway server: its own database, asset library, media folder, and the stand-in AI providers
fs.rmSync(OUT, { recursive: true, force: true });
const data = path.join(OUT, 'server-data');
for (const dir of ['assets', 'exports', 'static']) fs.mkdirSync(path.join(data, dir), { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const serverLog = fs.openSync(path.join(OUT, 'server.log'), 'w');
const server = spawn(path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python'),
    ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
        cwd: REPO,
        stdio: ['ignore', serverLog, serverLog],
        env: {
            ...process.env,
            PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'),
            EXPORTS_DIR: path.join(data, 'exports'),
            STATIC_DIR: path.join(data, 'static'),
            JWT_SECRET: 'ai-cache-check-' + Math.random().toString(36).slice(2),
            AI_FAKE_PROVIDER: '1',
            AI_GENERATION_ENABLED: '1'
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
        try {
            if ((await fetch(BASE + '/visuals.js')).ok) return;
        } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('the test server did not start');
}

async function api(method, route, token, body) {
    const res = await fetch(BASE + route, {
        method,
        headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined
    });
    return { status: res.status, data: await res.json().catch(() => null) };
}

// A short silent WAV for narration, so the check never needs the TTS service
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
    if (window.speechSynthesis) {
        window.speechSynthesis.speak = utterance => setTimeout(() => {
            if (utterance.onstart) utterance.onstart(new Event('start'));
            setTimeout(() => utterance.onend && utterance.onend(new Event('end')), 300);
        }, 0);
        window.speechSynthesis.cancel = () => {};
    }
}

const tag = Date.now().toString(36);
const videoPrompt = `A slow tracking shot of a glass bridge over a canyon at golden hour ${tag}`;
const imagePrompt = `hyper-realistic stock photo of a lighthouse in a storm ${tag}`;
const lesson = name => ({
    subject_name: `AI cache check — ${name}`, session_number: '1', session_title: name,
    concept_map: [{ id: 'c1', label: 'Bridges' }],
    scenes: [
        { type: 'ai_video', concept_id: 'c1', title: `BRIDGE (${name})`, aadhi_position: 'hidden', prompt: videoPrompt, narration: 'A bridge.' },
        { type: 'content', concept_id: 'c1', title: `LIGHTHOUSE (${name})`, aadhi_position: 'popup_bottom_left', html: '<p>Light.</p>',
          side_panel: { type: 'image', prompt: imagePrompt }, narration: 'A lighthouse.' }
    ]
});

const problems = [];
const requests = { video: [], image: [] };
let browser;
try {
    await waitForServer();
    const token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const stats = async () => (await api('GET', '/api/ai-cache/stats', token)).data;
    const start = await stats();
    check('the test server uses the stand-in AI providers', start.video_provider === 'fake' && start.image_provider === 'fake',
        `${start.video_provider} / ${start.image_provider}`);

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });

    async function openLesson(projectId) {
        const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
        await context.addInitScript(t => localStorage.setItem('jwt_token', t), token);
        await context.addInitScript(silentPage);
        await context.route('**/generate-audio', r => r.fulfill({ json: { status: 'success', audio_url: `/__silence.wav?n=${Math.random()}` } }));
        await context.route('**/__silence.wav*', r => r.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav() }));
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(`page error: ${e.message}`));
        page.on('console', m => {
            if (m.type() === 'error' && !(m.location().url || '').endsWith('/favicon.ico') && !m.text().startsWith('Logo video play error AbortError')) problems.push(`console: ${m.text()}`);
        });
        page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
        page.on('request', r => {
            if (r.url().endsWith('/generate-ai-video')) requests.video.push(r.postDataJSON());
            if (r.url().endsWith('/generate-ai-image')) requests.image.push(r.postDataJSON());
        });
        page.on('dialog', d => d.accept()); // "Generate a new version…?" -> yes
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => window.lessonVisualPlans.length > 0 && ttsState.isPlaying
            && !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 30000 });
        await page.evaluate(() => { ttsState.isPlaying = false; });
        return { context, page };
    }
    const saveLesson = async (page, body) => page.evaluate(async d => (await (await fetch('/save-history', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) })).json()).id, body);
    const playingVideo = page => page.waitForFunction(() => {
        const v = document.getElementById('active-ai-video');
        return v && v.readyState >= 2 && v.offsetParent !== null ? v.getAttribute('src') : null;
    }, null, { timeout: 30000 }).then(h => h.jsonValue());
    const sideImage = page => page.waitForFunction(() => {
        const img = document.getElementById('side-image-display');
        return document.getElementById('side-image-panel').classList.contains('active') && img.complete && img.naturalWidth > 0
            && img.style.opacity === '1' ? img.getAttribute('src') : null;
    }, null, { timeout: 30000 }).then(h => h.jsonValue());
    const show = (page, i) => page.evaluate(n => { currentSlide = n; renderSlide(n); }, i);

    // ---- 1. Lesson A: first requests are cache misses -------------------------------------------
    const setup = await browser.newContext();
    const setupPage = await setup.newPage();
    await setupPage.addInitScript(t => localStorage.setItem('jwt_token', t), token);
    await setupPage.goto(BASE + '/');
    const projectA = await saveLesson(setupPage, lesson('Lesson A'));
    const projectB = await saveLesson(setupPage, lesson('Lesson B'));
    await setup.close();

    const a = await openLesson(projectA);
    const plansA = await a.page.evaluate(() => slides.map(s => s.visual_plan || {}));
    check('lesson A: the visual router plans an AI video and an AI image (not generated yet)',
        plansA[0].main && plansA[0].main.source === 'AI_VIDEO' && plansA[0].main.requires_generation
        && plansA[1].side && plansA[1].side.source === 'AI_IMAGE' && plansA[1].side.requires_generation);

    await show(a.page, 0);
    await a.page.waitForSelector('#jxgbox .visual-notice-action', { timeout: 20000 });
    await a.page.click('#jxgbox .visual-notice-action'); // "Generate AI video"
    const videoA = await playingVideo(a.page);
    const statusA = await a.page.waitForSelector('#jxgbox .visual-generation-status', { timeout: 10000 }).then(el => el.textContent());
    const videoAssetA = await a.page.evaluate(() => slides[0].video_asset_id);
    await a.page.screenshot({ path: path.join(OUT, '1-lesson-a-generated.png') });
    await show(a.page, 1);
    const imageA = await sideImage(a.page);
    const afterA = await stats();
    const imageAssetA = (imageA.match(/\/api\/assets\/([0-9a-f]{32})\/content/) || [])[1];
    check('first video request: cache miss, generated once, "Generated new video" shown',
        requests.video.length === 1 && afterA.provider_calls.video - start.provider_calls.video === 1 && /Generated new video/.test(statusA) && !!videoAssetA,
        `${statusA}; asset ${String(videoAssetA).slice(0, 8)}`);
    check('first image request: cache miss, generated once and served from the asset library',
        requests.image.length === 1 && afterA.provider_calls.image - start.provider_calls.image === 1 && !!imageAssetA);
    const imageAsset = (await api('GET', `/api/assets/${imageAssetA}`, token)).data;
    check('the generated media are library assets with their generation recorded',
        imageAsset && imageAsset.source === 'ai-image' && imageAsset.details.generation.provider === 'fake'
        && (await api('GET', `/api/assets/${videoAssetA}`, token)).data.details.generation.hash.length === 64);
    await a.context.close();

    // ---- 2. Lesson B: the same requests are reused ---------------------------------------------------
    const before = { video: requests.video.length, image: requests.image.length, stats: await stats() };
    const b = await openLesson(projectB);
    const plansB = await b.page.evaluate(() => slides.map(s => s.visual_plan || {}));
    await show(b.page, 0);
    const videoB = await playingVideo(b.page);
    await b.page.screenshot({ path: path.join(OUT, '2-lesson-b-reused.png') });
    await show(b.page, 1);
    const imageB = await sideImage(b.page);
    const afterB = await stats();
    check('lesson B: the same AI video and image are reused (same asset IDs)',
        plansB[0].main.asset_id === videoAssetA && plansB[1].side.asset_id === imageAssetA && !plansB[0].main.requires_generation
        && (videoB.includes(`/api/assets/${videoAssetA}/`) || videoB.split('?')[0] === videoA.split('?')[0]) && imageB.includes(`/api/assets/${imageAssetA}/`),
        `video: ${plansB[0].main.selection}, image: ${plansB[1].side.selection}`);
    check('lesson B: no generator request and no provider call',
        requests.video.length === before.video && requests.image.length === before.image
        && afterB.provider_calls.video === before.stats.provider_calls.video && afterB.provider_calls.image === before.stats.provider_calls.image);

    // The visual router's cache step on its own (library matching off) and the generators themselves
    const cacheOnly = await b.page.evaluate(async () => (await visualApi.plan(slides.map(s => ({ ...s, visual_plan: undefined, video_asset_id: undefined, video_url: undefined })),
        { allow_ai_generation: true, prefer_existing_assets: false, prefer_procedural: true })).plans);
    const again = await b.page.evaluate(async p => aiMediaApi.generateVideo(p), videoPrompt);
    const againImage = await b.page.evaluate(async p => aiMediaApi.generateImage(p), imagePrompt);
    const afterAgain = await stats();
    check('the AI cache answers the router and the generators: same assets, cache hits, no provider call',
        cacheOnly[0].selection === 'cached' && cacheOnly[0].asset_id === videoAssetA && cacheOnly[1].asset_id === imageAssetA
        && again.cache_hit && again.asset_id === videoAssetA && againImage.cache_hit && againImage.asset_id === imageAssetA
        && afterAgain.provider_calls.video === before.stats.provider_calls.video && afterAgain.provider_calls.image === before.stats.provider_calls.image,
        `plan ${cacheOnly[0].selection}/${cacheOnly[1].selection}; generator cache_hit ${again.cache_hit}/${againImage.cache_hit}`);

    // ---- 3. A new version on purpose ------------------------------------------------------------------
    const beforeForce = { requests: requests.video.length, calls: (await stats()).provider_calls.video };
    await b.page.evaluate(() => { populateInfoModal(); document.getElementById('info-modal').classList.add('active'); });
    await b.page.click('#info-modal-list .visual-regenerate');
    await b.page.waitForFunction(() => /Generated new video|Could not/.test((document.querySelector('.visual-regenerate-status') || {}).textContent || ''), null, { timeout: 30000 });
    const forceStatus = await b.page.textContent('.visual-regenerate-status');
    const newAsset = await b.page.evaluate(() => slides[0].video_asset_id);
    const afterForce = await stats();
    const forced = requests.video.slice(beforeForce.requests);
    await b.page.screenshot({ path: path.join(OUT, '3-new-version.png') });
    check('"New AI version": a new asset, exactly one generator request and one provider call',
        newAsset && newAsset !== videoAssetA && forced.length === 1 && forced[0].force_regenerate === true
        && afterForce.provider_calls.video - beforeForce.calls === 1 && /Generated new video/.test(forceStatus),
        `${forceStatus}; ${String(newAsset).slice(0, 8)}`);
    // Lesson B's saved plan pointed at the first version (recorded when it was planned): still there
    const oldAsset = (await api('GET', `/api/assets/${videoAssetA}`, token)).data;
    const oldContent = await fetch(`${BASE}/api/assets/${videoAssetA}/content`, { headers: { Authorization: 'Bearer ' + token } });
    check('the earlier version is kept, playable, and its lesson reference untouched', oldAsset.status === 'ready' && oldContent.ok
        && oldAsset.used_in.some(u => u.project_id === projectB), `${oldAsset.status}, used by ${oldAsset.used_in.map(u => u.project_id).join(',')}`);
    await b.context.close();

    check('no console errors, page errors or failed requests', problems.length === 0, problems.join(' | '));
} catch (err) {
    check('the check ran to the end', false, err.stack || String(err));
} finally {
    if (browser) await browser.close();
    stopServer();
}

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Screenshots and server log: ${OUT}`);
process.exit(failed ? 1 : 0);
