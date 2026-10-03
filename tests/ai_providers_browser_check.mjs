// Browser check of the multi-provider AI media layer (Phase 8) in real Chrome, against a separate,
// throwaway server whose only AI providers are the two local stand-ins (AI_FAKE_PROVIDER=1: "fake" is
// preferred, "fake-alt" is the backup; no real provider is even registered). The preferred stand-in is
// made to fail for videos and to return a blank picture for images (AI_FAKE_FAIL), so the check sees:
//
//   1. the AI providers panel in the settings: states, order, no secret, and no provider called to show it
//   2. "Generate AI video": a background generation followed through its run; the preferred provider
//      fails (retried once), the backup makes the video; "Generated new video (backup AI provider)"
//   3. the video is a library asset whose provenance names the provider that really made it
//   4. a second lesson reuses it from the cache (no provider call), the plan naming provider and backup
//   5. Visual Review says where the visual came from in plain words (no provider name), flags the blank picture for a
//      person to check, and shows the provider line with ?visualDebug (Phase 21: technical details only in the debug view)
//   6. the panel afterwards: the failing provider temporarily unavailable, the recent generation listed
//
// Needs Chrome, ffmpeg, playwright-core (npm install --no-save playwright-core, or PLAYWRIGHT_CORE_DIR)
// and the repo's virtualenv (.venv). Uses the default admin login of a fresh database:
//   AADHI_PASSWORD=... node tests/ai_providers_browser_check.mjs
// Silent: no audio output, browser speech stubbed, narration answered with silence.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.AI_PROVIDERS_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-ai-providers-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.AI_PROVIDERS_CHECK_PORT || 9990 + (process.pid % 8));
const BASE = `http://127.0.0.1:${PORT}`;
const SECRET = 'AIzaSyBROWSERCHECKSECRET0000000000000';
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
            JWT_SECRET: 'ai-providers-check-' + Math.random().toString(36).slice(2),
            AI_FAKE_PROVIDER: '1',
            AI_GENERATION_ENABLED: '1',
            AI_FAKE_FAIL: 'fake:video:unavailable,fake:image:blank',
            AI_PROVIDER_RETRY_DELAY: '0.05',
            FAKE_POLL_SECONDS: '0.1',
            FAKE_ALT_POLL_SECONDS: '0.1',
            GEMINI_API_KEY: SECRET  // configured but unused in stand-in mode: it must never reach the page
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
const videoPrompt = `An aerial shot of a suspension bridge swaying gently in the wind ${tag}`;
const imagePrompt = `a detailed diagram of a cable-stayed bridge ${tag}`;
const lesson = name => ({
    subject_name: `AI providers check — ${name}`, session_number: '1', session_title: name,
    concept_map: [{ id: 'c1', label: 'Bridges' }],
    scenes: [
        { type: 'ai_video', concept_id: 'c1', title: `BRIDGE (${name})`, aadhi_position: 'hidden', prompt: videoPrompt, narration: 'A bridge.' },
        { type: 'content', concept_id: 'c1', title: `DIAGRAM (${name})`, aadhi_position: 'popup_bottom_left', html: '<p>Cables.</p>',
          side_panel: { type: 'image', prompt: imagePrompt }, narration: 'A diagram.' }
    ]
});

const problems = [];
const requests = { video: [], runs: 0 };
let browser;
try {
    await waitForServer();
    const token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const stats = async () => (await api('GET', '/api/ai-cache/stats', token)).data;
    const start = await stats();

    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
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
        if (/\/api\/ai-media\/runs\/[0-9a-f]{32}$/.test(r.url())) requests.runs++;
    });

    // ---- 1. The AI providers panel ---------------------------------------------------------------------
    // (Phase 21: the panel is in Settings → Visuals & AI; the order the providers are tried in and their models are
    // technical details, shown only with ?visualDebug)
    async function openPanel(query = '') {
        await page.goto(BASE + '/' + query);
        await page.click('#nav-settings');
        await page.waitForSelector('#settings-overlay:not([hidden])', { timeout: 15000 });
        await page.click('#ai-providers-panel summary');
        await page.waitForSelector('#ai-providers-body .ai-provider-row', { timeout: 15000 });
        return page.evaluate(() => ({
            rows: [...document.querySelectorAll('#ai-providers-body .ai-provider-row')].map(r => [r.dataset.provider,
                r.querySelector('.ai-provider-state').textContent]),
            text: document.getElementById('ai-providers-panel').textContent
        }));
    }
    const plainPanel = await openPanel();
    await page.screenshot({ path: path.join(OUT, '1-providers-panel-plain.png') });
    check('without ?visualDebug the providers panel lists each service and its state in words, without the order they are tried in',
        JSON.stringify(plainPanel.rows) === JSON.stringify([['fake', 'Available'], ['fake-alt', 'Available']])
        && !/Images: |Videos: |→/.test(plainPanel.text), plainPanel.text.replace(/\s+/g, ' ').slice(0, 300));
    const panel = await openPanel('?visualDebug=1');
    await page.screenshot({ path: path.join(OUT, '1-providers-panel.png') });
    const afterPanel = await stats();
    check('the providers panel lists the providers with their state and order (?visualDebug)',
        JSON.stringify(panel.rows) === JSON.stringify([['fake', 'Available'], ['fake-alt', 'Available']])
        && /Images: fake → fake-alt/.test(panel.text) && /Videos: fake → fake-alt/.test(panel.text), JSON.stringify(panel.rows));
    check('showing the panel reveals no secret and calls no provider', !panel.text.includes(SECRET) && !plainPanel.text.includes(SECRET)
        && JSON.stringify(afterPanel.provider_calls) === JSON.stringify(start.provider_calls));

    // ---- 2-3. Generate: the preferred provider fails, the backup makes it ---------------------------------
    const projectA = (await api('POST', '/save-history', token, lesson('Lesson A'))).data.id;
    const projectB = (await api('POST', '/save-history', token, lesson('Lesson B'))).data.id;
    async function openLesson(projectId) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => window.lessonVisualPlans.length > 0 && ttsState.isPlaying
            && !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 30000 });
        await page.evaluate(() => { ttsState.isPlaying = false; });
    }
    const show = i => page.evaluate(n => { currentSlide = n; renderSlide(n); }, i);
    await openLesson(projectA);
    const planned = await page.evaluate(() => slides[0].visual_plan.main);
    check('the plan names the provider that would make the video', planned.source === 'AI_VIDEO' && planned.provider === 'fake'
        && planned.model === 'fake-video-1' && planned.requires_generation, `${planned.provider} ${planned.model}`);
    await show(0);
    await page.waitForSelector('#jxgbox .visual-notice-action', { timeout: 20000 });
    await page.click('#jxgbox .visual-notice-action'); // "Generate AI video"
    await page.waitForFunction(() => {
        const v = document.getElementById('active-ai-video');
        return v && v.readyState >= 2 && v.offsetParent !== null;
    }, null, { timeout: 60000 });
    const status = await page.waitForSelector('#jxgbox .visual-generation-status', { timeout: 10000 }).then(el => el.textContent());
    const videoAsset = await page.evaluate(() => slides[0].video_asset_id);
    await page.screenshot({ path: path.join(OUT, '2-generated-by-backup.png') });
    check('the video is generated in the background and followed through its run',
        requests.video.length === 1 && requests.video[0].wait === false && requests.runs >= 1, `${requests.runs} status polls`);
    const videoRun = (await api('GET', '/api/ai-media/runs?limit=10', token)).data.runs.find(r => r.media_type === 'video');
    const tried = videoRun.tried.map(t => `${t.provider}:${t.category || 'ok'}`).join(' ');
    check('the preferred provider failed (retried once) and the backup made it; the page says so',
        /Generated new video \(backup AI provider\)/.test(status) && tried === 'fake:unavailable fake:unavailable fake-alt:ok'
        && videoRun.provider === 'fake-alt' && videoRun.fallback_from === 'fake', `${status}; ${tried}`);
    const asset = (await api('GET', `/api/assets/${videoAsset}`, token)).data;
    const listed = (await api('GET', '/api/assets?source=ai-video&limit=50', token)).data.assets.map(a => a.id);
    check('the video is a library asset whose provenance names who really made it',
        asset.source === 'ai-video' && listed.includes(videoAsset) && asset.details.generation.provider === 'fake-alt'
        && asset.details.generation.fallback_from === 'fake' && asset.details.generation.model === 'fake-alt-video-1',
        JSON.stringify({ provider: asset.details.generation.provider, fallback_from: asset.details.generation.fallback_from }));
    await show(1);
    await page.waitForFunction(() => {
        const img = document.getElementById('side-image-display');
        return document.getElementById('side-image-panel').classList.contains('active') && img.complete && img.naturalWidth > 0;
    }, null, { timeout: 30000 });

    // ---- 4. A second lesson reuses it from the cache ----------------------------------------------------------
    const beforeB = await stats();
    await openLesson(projectB);
    const planB = await page.evaluate(() => slides[0].visual_plan.main);
    await show(0);
    await page.waitForFunction(() => {
        const v = document.getElementById('active-ai-video');
        return v && v.readyState >= 2 && v.offsetParent !== null;
    }, null, { timeout: 30000 });
    const afterB = await stats();
    // Reused through the library match on its prompt (which runs before the cache step) or the cache: same asset, no call
    check('a second lesson reuses the video without a provider call; the plan names provider and backup',
        planB.asset_id === videoAsset && !planB.requires_generation && planB.provider === 'fake-alt' && planB.fallback_from === 'fake'
        && requests.video.length === 1 && JSON.stringify(afterB.provider_calls) === JSON.stringify(beforeB.provider_calls),
        `${planB.selection} ${planB.provider} (backup for ${planB.fallback_from})`);

    // ---- 5. Visual Review: where the visual came from, the flagged picture, the provider line with ?visualDebug ----------
    async function openReview(query = '') {
        await page.goto(`${BASE}/?project_id=${projectB}${query}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-review-btn');
        await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
        await page.click('.review-item[data-scene="0"]');
    }
    // the normal view (Phase 21): plain words only, no provider line and no provider or model name anywhere in the detail
    await openReview();
    await page.waitForSelector('.review-detail .review-origin', { timeout: 10000 }).catch(() => null);
    const plainView = await page.evaluate(() => {
        const detail = document.querySelector('.review-detail');
        const origin = detail && detail.querySelector('.review-origin');
        return { origin: origin ? origin.textContent : null, providerLine: !!(detail && detail.querySelector('.review-provider')),
            text: detail ? detail.textContent : '', plan: (slides[0].visual_plan || {}).main || {} };
    });
    await page.screenshot({ path: path.join(OUT, '3a-review-origin.png') });
    // reused through the library match (an Asset Library visual that AI made) or the cache (the AI video itself): see 4.
    const expectedOrigin = plainView.plan.source === 'AI_VIDEO' ? 'Made with AI (backup)' : 'Made with AI earlier, from your library';
    const named = /\bfake(?:-alt)?\b|fake-alt-video-1|fake-video-1|provider|\bmodel\b/i.exec(plainView.text);
    await page.click('.review-item[data-scene="1"]');
    const sidePlan = await page.evaluate(() => JSON.stringify((slides[1].visual_plan || {}).side || null));
    const quality = await page.waitForSelector('.review-detail .review-quality', { timeout: 10000 }).then(el => el.textContent())
        .catch(() => `no note; side plan ${sidePlan}`);
    await page.screenshot({ path: path.join(OUT, '4-review-flagged.png') });
    await page.click('.review-panel .export-close');
    // the debug view (?visualDebug): the exact provenance, as before Phase 21
    await openReview('&visualDebug=1');
    const providerLine = await page.waitForSelector('.review-detail .review-provider', { timeout: 10000 }).then(el => el.textContent());
    const debugOrigin = await page.textContent('.review-detail .review-origin').catch(() => null);
    await page.screenshot({ path: path.join(OUT, '3-review-provider.png') });
    check('Visual Review says where the visual came from in plain words, without the provider line or any provider name',
        !plainView.providerLine && plainView.origin === expectedOrigin && plainView.plan.provider === 'fake-alt' && plainView.plan.fallback_from === 'fake' && !named,
        `"${plainView.origin}" (plan ${plainView.plan.source}/${plainView.plan.selection}, expected "${expectedOrigin}")${named ? `; names "${named[0]}"` : ''}`);
    check('Visual Review flags the blank picture for a person to check', /looks blank/.test(quality), quality);
    check('with ?visualDebug Visual Review shows which provider made the visual', providerLine === 'fake-alt · fake-alt-video-1 · backup for fake'
        && debugOrigin === expectedOrigin, `${providerLine}; origin "${debugOrigin}"`);
    await page.click('.review-panel .export-close');

    // ---- 6. The panel afterwards -------------------------------------------------------------------------------
    // (Phase 21: which provider made a recent generation is listed only with ?visualDebug; a teacher reads it in plain words)
    const laterPlain = await openPanel();
    await page.screenshot({ path: path.join(OUT, '5-providers-after-plain.png') });
    const recentPlain = laterPlain.text.split('Your recent generations')[1] || '';
    check('afterwards, without ?visualDebug: the failing provider shows as temporarily unavailable and the generation is listed in plain words, without who made it',
        laterPlain.rows.find(r => r[0] === 'fake')[1] === 'Temporarily unavailable' && laterPlain.rows.find(r => r[0] === 'fake-alt')[1] === 'Available'
        && /Video for scene \d+ · ✓ Made/.test(recentPlain) && !/fake|completed|backup for/.test(recentPlain),
        `${JSON.stringify(laterPlain.rows)}; recent "${recentPlain.replace(/\s+/g, ' ').slice(0, 160)}"`);
    const later = await openPanel('?visualDebug=1');
    await page.screenshot({ path: path.join(OUT, '5-providers-after.png') });
    check('afterwards the failing provider shows as temporarily unavailable and the generation is listed (?visualDebug: with its provider)',
        later.rows.find(r => r[0] === 'fake')[1] === 'Temporarily unavailable' && later.rows.find(r => r[0] === 'fake-alt')[1] === 'Available'
        && /video · completed · fake-alt · fake-alt-video-1 · backup for fake/.test(later.text), JSON.stringify(later.rows));
    check('no real AI provider exists on the test server', (await api('GET', '/api/ai-media/providers', token)).data.providers
        .every(p => p.name.startsWith('fake')));
    check('no console errors, page errors or failed requests', problems.length === 0, problems.join(' | '));
    await context.close();
} catch (err) {
    check('the check ran to the end', false, err.stack || String(err));
} finally {
    if (browser) await browser.close();
    stopServer();
}

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Screenshots and server log: ${OUT}`);
process.exit(failed ? 1 : 0);
