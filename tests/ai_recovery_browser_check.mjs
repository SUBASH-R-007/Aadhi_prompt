// Recovery check (Phase 9) with real server processes that are killed and restarted, in real Chrome:
// a throwaway server whose only AI providers are the local stand-ins (AI_FAKE_PROVIDER=1). Their jobs
// live in files (AI_FAKE_STATE_DIR) like a provider's own servers, so they survive the server's death,
// and every job ever started is a file there: counting them proves no duplicate was submitted.
//
//   1+4. "Generate AI video" in the page, the page is refreshed (it finds the generation again and
//        follows it), then the server is killed while the provider's job runs and started again: the
//        page reconnects, the same job is recovered (no second job), the video plays, the asset is in the
//        library, Visual Review shows it
//   2.   the server dies after the provider finished, before the asset was registered: after the restart
//        the result is registered, no new job
//   3.   a lesson batch of three scenes, the server killed during the second: finished scene untouched,
//        interrupted one resumed, the last made; three jobs in all
//   5.   a provider job fails on the provider's side while the server is down: after the restart the
//        backup provider makes it; the failed provider is never asked again
//   6.   a paid provider, the server dies before the provider's answer was saved: after the restart the run
//        needs attention (no duplicate job); the providers panel explains it and "Retry" makes it once
//
// Needs Chrome, ffmpeg, playwright-core (npm install --no-save playwright-core, or PLAYWRIGHT_CORE_DIR)
// and the repo's virtualenv (.venv):   AADHI_PASSWORD=... node tests/ai_recovery_browser_check.mjs
// Silent: no audio output, browser speech stubbed, narration answered with silence.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.AI_RECOVERY_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-ai-recovery-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.AI_RECOVERY_CHECK_PORT || 9960 + (process.pid % 20));
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

fs.rmSync(OUT, { recursive: true, force: true });
const data = path.join(OUT, 'server-data');
const jobs = path.join(OUT, 'provider-jobs');
for (const dir of ['assets', 'exports', 'static']) fs.mkdirSync(path.join(data, dir), { recursive: true });
fs.mkdirSync(jobs, { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const JWT = 'ai-recovery-check-' + Math.random().toString(36).slice(2);
let server = null;
let serverRuns = 0;

function startServer(extraEnv = {}) {
    serverRuns++;
    const log = fs.openSync(path.join(OUT, `server-${serverRuns}.log`), 'w');
    server = spawn(path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python'),
        ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
            cwd: REPO, stdio: ['ignore', log, log],
            env: {
                ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
                DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
                ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
                JWT_SECRET: JWT, AI_FAKE_PROVIDER: '1', AI_GENERATION_ENABLED: '1', AI_FAKE_STATE_DIR: jobs,
                AI_RECOVERY_ENABLED: '1', AI_RECOVERY_INTERVAL: '1', AI_JOB_LEASE_SECONDS: '4', AI_JOB_HEARTBEAT_SECONDS: '1',
                FAKE_POLL_SECONDS: '0.5', FAKE_ALT_POLL_SECONDS: '0.5', FAKE_JOB_SECONDS: '6', FAKE_ALT_JOB_SECONDS: '1',
                AI_MEDIA_LOG: '1', ...extraEnv
            }
        });
    return server;
}
function killServer() {  // like a crash or power cut: no shutdown handlers run
    if (!server || server.exitCode !== null) return;
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
    else server.kill('SIGKILL');
}
process.on('exit', killServer);
const exited = proc => new Promise(resolve => { if (proc.exitCode !== null) resolve(proc.exitCode); else proc.on('exit', code => resolve(code)); });

async function waitForServer() {
    for (let i = 0; i < 120; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + OUT);
        try {
            if ((await fetch(BASE + '/visuals.js')).ok) return;
        } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('the test server did not start');
}
async function restart(extraEnv = {}) {
    killServer();
    await exited(server);
    startServer(extraEnv);
    await waitForServer();
}

let token = null;
async function api(method, route, body) {
    const res = await fetch(BASE + route, {
        method, headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined
    });
    return { status: res.status, data: await res.json().catch(() => null) };
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 60000, every = 300) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
        try {
            const v = await fn();
            if (v) return v;
        } catch (e) { /* server restarting */ }
        await sleep(every);
    }
    return null;
}
const run = id => api('GET', `/api/ai-media/runs/${id}`).then(r => r.data);
const jobFiles = prefix => fs.readdirSync(jobs).filter(f => new RegExp(`^${prefix}-[0-9a-f]{10}\\.json$`).test(f));

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
        window.speechSynthesis.speak = u => setTimeout(() => { if (u.onstart) u.onstart(new Event('start')); setTimeout(() => u.onend && u.onend(new Event('end')), 300); }, 0);
        window.speechSynthesis.cancel = () => {};
    }
}
const tag = Date.now().toString(36);
// Distinct words per request: the visual router's library match must not (rightly) reuse another scene's video
const words = () => Array.from({ length: 6 }, () => 'w' + Math.random().toString(36).slice(2, 9)).join(' ');
const topic = n => `${words()} ${n} ${tag}`;

const problems = [];
let serverDown = false;
let browser;
try {
    startServer({ FAKE_JOB_SECONDS: '20' });  // long enough to refresh the page and kill the server mid-job
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;

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
        const text = m.text();
        if (m.type() === 'error' && !serverDown && !/Failed to load resource|ERR_CONNECTION/.test(text) && !text.startsWith('Logo video play error AbortError')
            && !(m.location().url || '').endsWith('/favicon.ico')) problems.push(`console: ${text}`);
    });
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });

    async function openScene(projectId, index) {
        await page.goto(`${BASE}/?project_id=${projectId}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-lecture-btn');
        await page.evaluate(() => skipIntroSequence());
        await page.waitForFunction(() => window.lessonVisualPlans.length > 0 && ttsState.isPlaying
            && !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 30000 });
        await page.evaluate(n => { ttsState.isPlaying = false; currentSlide = n; renderSlide(n); }, index);
    }
    const lesson = (name, count) => ({ subject_name: `Recovery check — ${name}`, session_title: name,
        scenes: Array.from({ length: count }, (_, i) => ({ type: 'ai_video', title: `PART ${i + 1}`, aadhi_position: 'hidden', prompt: topic(`${name} ${i + 1}`), narration: 'A river.' })) });

    // ---- 1+4. Page: generate, refresh, server killed mid-job, restart --------------------------------------------
    const p1 = (await api('POST', '/save-history', lesson('page', 1))).data.id;
    await openScene(p1, 0);
    await page.waitForSelector('#jxgbox .visual-notice-action', { timeout: 20000 });
    await page.click('#jxgbox .visual-notice-action'); // "Generate AI video"
    const started = await until(async () => (await api('GET', `/api/ai-media/runs?project_id=${p1}&active=true`)).data.runs.find(r => r.provider_job_id));
    check('the generation runs on the server with its provider job saved', started && started.status === 'running' && jobFiles('fake').length === 1,
        started && started.provider_job_id);
    await openScene(p1, 0); // the page is refreshed while it runs
    const reattached = await page.waitForSelector('#jxgbox .ai-run-note', { timeout: 10000 }).then(el => el.textContent()).catch(() => '');
    check('after a refresh the page finds the generation and follows it (nothing restarted)', /Still generating on the server/.test(reattached)
        && jobFiles('fake').length === 1, reattached);
    await page.screenshot({ path: path.join(OUT, '1-reattached.png') });
    serverDown = true;
    killServer();
    await exited(server);
    await sleep(1500); // the page keeps polling a server that is gone
    startServer({ FAKE_JOB_SECONDS: '20' });
    await waitForServer();
    serverDown = false;
    const recovered = await until(async () => { const r = await run(started.run_id); return r && r.status === 'completed' ? r : null; }, 90000);
    await page.waitForFunction(() => { const v = document.getElementById('active-ai-video'); return v && v.readyState >= 2; }, null, { timeout: 30000 }).catch(() => {});
    const playing = await page.evaluate(() => { const v = document.getElementById('active-ai-video'); return v ? { ready: v.readyState, w: v.videoWidth, h: v.videoHeight } : null; });
    await page.screenshot({ path: path.join(OUT, '2-recovered-playing.png') });
    check('after the server was killed and restarted the same provider job was recovered (no second job)',
        recovered && recovered.recovery_count >= 1 && recovered.provider_job_id === started.provider_job_id && jobFiles('fake').length === 1
        && /picked up again/.test(recovered.explanation || ''), recovered && recovered.explanation);
    check('the page reconnected and plays the recovered video', playing && playing.ready >= 2 && playing.w === 640 && playing.h === 360, JSON.stringify(playing));
    const asset = recovered && (await api('GET', `/api/assets/${recovered.result.asset_id}`)).data;
    check('the recovered video is a normal library asset (same checks as any generation)', asset && asset.status === 'ready' && asset.source === 'ai-video'
        && asset.width === 640 && asset.height === 360 && Math.abs(asset.duration_seconds - 4) < 0.3 && asset.details.generation.run_id === started.run_id);
    // Visual Review (Phase 21): plain words for everyone ("Made with AI"), the provider line only with ?visualDebug; who made
    // it is checked in the asset's own provenance
    const openReview = async (query = '') => {
        await page.goto(`${BASE}/?project_id=${p1}${query}`);
        await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
        await page.click('#start-review-btn');
        await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
    };
    await openReview();
    const reviewAsset = await page.evaluate(() => (slides[0].visual_plan || {}).main && slides[0].visual_plan.main.asset_id);
    const reviewOrigin = await page.textContent('.review-detail .review-origin').catch(() => '');
    const providerLineShown = !!(await page.$('.review-detail .review-provider'));
    const madeBy = asset && asset.details && asset.details.generation ? asset.details.generation.provider : null;
    check('Visual Review shows the recovered video for the scene, made with AI (by the stand-in, as its provenance says)',
        reviewAsset === recovered.result.asset_id && /Made with AI/.test(reviewOrigin) && !providerLineShown && madeBy === 'fake',
        `origin "${reviewOrigin}"; provider line ${providerLineShown ? 'SHOWN' : 'hidden'}; provenance ${madeBy}`);
    await page.click('.review-panel .export-close');
    await openReview('&visualDebug=1');
    const reviewProvider = await page.textContent('.review-detail .review-provider').catch(() => '');
    check('with ?visualDebug Visual Review names the provider that made it', /fake/.test(reviewProvider), reviewProvider);
    await page.click('.review-panel .export-close');

    // ---- 2. Crash after the provider finished, before registration ------------------------------------------------
    await restart({ AI_TEST_CRASH_AT: 'after_download', FAKE_JOB_SECONDS: '1' });
    const before2 = jobFiles('fake').length;
    const r2 = (await api('POST', '/generate-ai-video', { prompt: topic('crash-after-download'), wait: false })).data;
    const died = await Promise.race([exited(server), sleep(30000).then(() => 'alive')]);
    startServer({ FAKE_JOB_SECONDS: '1' });
    await waitForServer();
    const done2 = await until(async () => { const r = await run(r2.run_id); return r && r.status === 'completed' ? r : null; }, 60000);
    check('a crash after the provider finished: the result is registered after the restart, no new job',
        died === 97 && done2 && jobFiles('fake').length === before2 + 1 && /nothing was generated twice/.test(done2.explanation || ''),
        `exit ${died}; ${done2 && done2.explanation}`);

    // ---- 3. Lesson batch, server killed during the second scene ------------------------------------------------------
    await restart({ AI_JOB_CONCURRENCY: '1', FAKE_JOB_SECONDS: '3' });
    const p3 = (await api('POST', '/save-history', lesson('batch', 3))).data.id;
    const before3 = jobFiles('fake').length;
    const batch = (await api('POST', `/api/ai-media/lessons/${p3}/generate`, { media: 'video' })).data;
    const byScene = Object.fromEntries(batch.runs.map(r => [r.scene_index, r.run_id]));
    const second = await until(async () => {
        const [a, b] = await Promise.all([run(byScene[0]), run(byScene[1])]);
        return a.status === 'completed' && b.provider_job_id ? { first: a, second: b } : null;
    }, 60000);
    killServer();
    await exited(server);
    startServer({ AI_JOB_CONCURRENCY: '1', FAKE_JOB_SECONDS: '3' });
    await waitForServer();
    const all = await until(async () => {
        const rs = await Promise.all([0, 1, 2].map(i => run(byScene[i])));
        return rs.every(r => r.status === 'completed') ? rs : null;
    }, 90000);
    const plans = JSON.parse(JSON.stringify((await api('GET', `/api/projects/${p3}`)).data));
    const stored = ((plans.json_data ? JSON.parse(plans.json_data) : plans).scenes || []).map(s => s.visual_plan && s.visual_plan.main && s.visual_plan.main.asset_id);
    check('a lesson batch continues after a restart: finished scene untouched, interrupted one resumed, last one made',
        second && all && all[0].result.asset_id === second.first.result.asset_id && all[1].recovery_count >= 1
        && all[1].provider_job_id === second.second.provider_job_id && jobFiles('fake').length === before3 + 3,
        all && all.map(r => `${r.status}/${r.recovery_count}`).join(' '));
    check('the saved lesson holds every scene\'s video', all && stored.length === 3 && stored.every((id, i) => id === all[i].result.asset_id), stored.join(','));

    // ---- 5. The provider's job fails while the server is down: the backup makes it -------------------------------------
    await restart({ FAKE_JOB_SECONDS: '30' });
    const r5 = (await api('POST', '/generate-ai-video', { prompt: topic('provider-failure'), wait: false })).data;
    const job5 = await until(async () => { const r = await run(r5.run_id); return r && r.provider_job_id; }, 30000);
    killServer();
    await exited(server);
    const file5 = path.join(jobs, `${job5}.json`);
    fs.writeFileSync(file5, JSON.stringify({ ...JSON.parse(fs.readFileSync(file5, 'utf8')), kind: 'jobfail' }));
    const alt5 = jobFiles('fake-alt').length, fake5 = jobFiles('fake').length;
    startServer({ FAKE_JOB_SECONDS: '30' });
    await waitForServer();
    const done5 = await until(async () => { const r = await run(r5.run_id); return r && ['completed', 'failed', 'needs_attention'].includes(r.status) ? r : null; }, 60000);
    check('a job that failed on the provider falls back to the backup after the restart, never resubmitted to the first',
        done5 && done5.status === 'completed' && done5.provider === 'fake-alt' && done5.fallback_from === 'fake'
        && jobFiles('fake').length === fake5 && jobFiles('fake-alt').length === alt5 + 1, done5 && `${done5.status} ${done5.provider}`);

    // ---- 6. Paid provider, crash before its answer was saved: needs attention, then Retry ---------------------------------
    await restart({ FAKE_PAID: '1', AI_TEST_CRASH_AT: 'before_job_saved', FAKE_JOB_SECONDS: '1' });
    const r6 = (await api('POST', '/generate-ai-video', { prompt: topic('ambiguous'), wait: false })).data;
    const died6 = await Promise.race([exited(server), sleep(30000).then(() => 'alive')]);
    const jobs6 = jobFiles('fake').length;
    startServer({ FAKE_PAID: '1', FAKE_JOB_SECONDS: '1' });
    await waitForServer();
    const attention = await until(async () => { const r = await run(r6.run_id); return r && r.status === 'needs_attention' ? r : null; }, 60000);
    await sleep(3000);
    check('an ambiguous paid submission is never sent again by itself: the run needs attention',
        died6 === 97 && attention && attention.error.category === 'ambiguous_submission' && jobFiles('fake').length === jobs6,
        attention && attention.explanation);
    await page.goto(BASE + '/');
    await page.click('#nav-settings'); // (Phase 21: the providers panel is in Settings → Visuals & AI)
    await page.waitForSelector('#settings-overlay:not([hidden])', { timeout: 15000 });
    await page.click('#ai-providers-panel summary');
    const row = await page.waitForSelector(`.ai-run-row[data-run="${r6.run_id}"]`, { timeout: 15000 });
    const rowText = await row.textContent();
    await page.screenshot({ path: path.join(OUT, '3-needs-attention.png') });
    check('the providers panel explains it and offers Retry and Dismiss', /Needs attention/.test(rowText) && /No duplicate generation was started/.test(rowText)
        && (await row.$('[data-action="retry"]')) && (await row.$('[data-action="dismiss"]')), rowText.slice(0, 120));
    await page.click(`.ai-run-row[data-run="${r6.run_id}"] [data-action="retry"]`);
    const retried = await until(async () => { const r = await run(r6.run_id); return r && r.status === 'completed' ? r : null; }, 60000);
    check('after the person chose Retry it is generated once', retried && jobFiles('fake').length === jobs6 + 1
        && retried.history.map(h => h.state).join(',') === 'ambiguous,completed', retried && retried.history.map(h => h.state).join(','));

    check('no console errors, page errors or failed requests (outside the moments the server was down)', problems.length === 0, problems.join(' | '));
    await context.close();
} catch (err) {
    check('the check ran to the end', false, err.stack || String(err));
} finally {
    if (browser) await browser.close();
    killServer();
}

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Screenshots, provider jobs and server logs: ${OUT}`);
process.exit(failed ? 1 : 0);
