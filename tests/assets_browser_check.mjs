// Browser check of the Asset Library panel and of lessons that use library assets, in real Chrome.
//
// Needs the server running, Chrome, ffmpeg, playwright-core (npm install --no-save playwright-core)
// and an account allowed to create users (admin), to check what a second account can see:
//   AADHI_USER=admin AADHI_PASSWORD=... node tests/assets_browser_check.mjs [http://127.0.0.1:9942]
// Screenshots go to $ASSETS_CHECK_OUT (default <tmp>/aadhi-assets-check).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const BASE = (process.argv[2] || 'http://127.0.0.1:9942').replace(/\/$/, '');
const OUT = process.env.ASSETS_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-assets-check');
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

async function loadPlaywright() {
    try {
        return await import('playwright-core');
    } catch (e) {
        if (process.env.PLAYWRIGHT_CORE_DIR) return import(pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE_DIR, 'index.mjs')).href);
        throw new Error('playwright-core not found: run `npm install --no-save playwright-core` (or set PLAYWRIGHT_CORE_DIR)');
    }
}

function ffmpeg(...args) {
    const r = spawnSync('ffmpeg', ['-v', 'error', '-y', ...args], { encoding: 'utf8' });
    if (r.status !== 0) throw new Error(r.stderr);
}

async function api(method, route, token, body) {
    const res = await fetch(BASE + route, {
        method,
        headers: { ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined
    });
    return { status: res.status, data: await res.json().catch(() => null) };
}

async function login(username, password) {
    const res = await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username, password }) });
    return (await res.json()).access_token;
}

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
// Unique media for this run, so uploads are new and a second upload is a true duplicate
const tag = Date.now().toString(16).slice(-6);
const image = path.join(OUT, `check-image-${tag}.png`);
const audio = path.join(OUT, `check-audio-${tag}.mp3`);
const clip = path.join(OUT, `check-clip-${tag}.mp4`);
ffmpeg('-f', 'lavfi', '-i', `color=c=0x${tag}:size=320x180`, '-f', 'lavfi', '-i', 'testsrc=size=160x90', '-filter_complex', 'overlay=80:45', '-frames:v', '1', image);
ffmpeg('-f', 'lavfi', '-i', `sine=frequency=${200 + (parseInt(tag, 16) % 1800)}:duration=2`, '-c:a', 'libmp3lame', audio);
ffmpeg('-f', 'lavfi', '-i', `testsrc2=size=640x360:rate=25:duration=4`, '-vf', `drawbox=c=0x${tag}:t=fill:w=120:h=120`, '-pix_fmt', 'yuv420p', '-c:v', 'libx264', clip);

const token = await login(USER, PASSWORD);
const { chromium } = await loadPlaywright();
// No sound for whoever runs the check: Chrome is muted and sends nothing to the speakers, and
// browser speech (which Windows speaks outside Chrome's audio) is replaced by a silent stand-in
function silenceSpeech() {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.speak = utterance => setTimeout(() => {
        if (utterance.onstart) utterance.onstart(new Event('start'));
        setTimeout(() => utterance.onend && utterance.onend(new Event('end')), 300);
    }, 0);
    window.speechSynthesis.cancel = () => {};
}
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });
const problems = [];
let expectingServerError = false; // true only while the error-state step injects a failing response

// Console errors that are explained and expected, each with its reason
function expectedConsoleError(message) {
    const url = message.location().url || '';
    const text = message.text();
    if (url.endsWith('/favicon.ico')) return true; // the app has never had a favicon
    if (text.startsWith('Logo video play error AbortError')) return true; // this check skips the intro while its logo starts
    return expectingServerError && /status of 503/.test(text);
}

async function openPage(authToken, viewport = { width: 1280, height: 800 }) {
    const context = await browser.newContext({ viewport, permissions: ['clipboard-read', 'clipboard-write'] });
    await context.addInitScript(t => localStorage.setItem('jwt_token', t), authToken);
    await context.addInitScript(silenceSpeech);
    const page = await context.newPage();
    page.on('pageerror', e => problems.push(`page error: ${e.message}`));
    page.on('console', m => {
        if (m.type() === 'error' && !expectedConsoleError(m)) problems.push(`console: ${m.text()} ${m.location().url || ''}`);
    });
    page.on('requestfailed', r => {
        // ERR_ABORTED is the browser cancelling a media range request (clip switched, preview closed,
        // page left): a cancellation, not a failure
        const reason = r.failure() && r.failure().errorText;
        if (reason !== 'net::ERR_ABORTED') problems.push(`request failed: ${r.url()} ${reason}`);
    });
    page.on('response', r => {
        if (r.status() >= 400 && !(expectingServerError && r.status() === 503)) problems.push(`HTTP ${r.status()}: ${r.url()}`);
    });
    await page.goto(BASE + '/');
    await page.waitForFunction(() => typeof assetLibrary !== 'undefined');
    return { context, page };
}

const items = page => page.$$eval('.asset-item', els => els.map(e => ({ id: e.dataset.id, kind: e.dataset.kind, text: e.textContent })));
const status = page => page.textContent('.asset-status');
const waitStatus = (page, re) => page.waitForFunction(r => new RegExp(r).test(document.querySelector('.asset-status').textContent), re.source, { timeout: 30000 });
const loaded = page => page.waitForFunction(() => !document.querySelector('.asset-list').hasAttribute('aria-busy'), null, { timeout: 30000 });
// Uploads through the panel's file picker and waits for the message about this file
async function uploadThroughPanel(page, file) {
    const name = typeof file === 'string' ? path.basename(file) : file.name;
    await page.setInputFiles('.asset-toolbar input[type=file]', file);
    await page.waitForFunction(n => {
        const text = document.querySelector('.asset-status').textContent;
        return text.startsWith(n + ' ') && /was added|already in your library/.test(text);
    }, name, { timeout: 30000 });
    await loaded(page);
    return { status: await status(page), id: await page.$eval('.asset-item.selected', e => e.dataset.id) };
}

try {
    // ---- Panel: list, filters, previews -------------------------------------------------------
    const { context, page } = await openPage(token);
    await page.click('#open-assets-btn');
    await page.waitForSelector('.asset-overlay.open');
    await loaded(page);
    const all = await items(page);
    check('Assets button opens the library with the shared Aadhi clips', all.filter(i => /Shared/.test(i.text)).length >= 13, `${all.length} assets listed`);

    await page.selectOption('.asset-filter[aria-label="Type"]', 'video');
    await page.selectOption('.asset-filter[aria-label="Source"]', 'mascot');
    await loaded(page);
    const mascot = await items(page);
    check('filters narrow the list (videos from Aadhi)', mascot.length === 5 && mascot.every(i => i.kind === 'video'), mascot.map(i => i.text.split(' ')[0]).join(', '));

    await page.click('.asset-item');
    await page.waitForFunction(() => { const v = document.querySelector('.asset-detail video'); return v && v.readyState >= 1 && v.duration > 0; }, null, { timeout: 20000 });
    const shared = await page.textContent('.asset-detail');
    check('a shared clip previews as video and cannot be deleted or edited', /Shared with everyone/.test(shared) && !(await page.$('.asset-delete'))
        && !(await page.$('.asset-edit-meta')) && !(await page.$('.asset-words-form')));
    await page.screenshot({ path: path.join(OUT, '1-library-mascot.png') });

    // ---- Upload, duplicate upload, preview kinds ------------------------------------------------
    const added = await uploadThroughPanel(page, image);
    const imageId = added.id;
    await page.waitForFunction(() => { const i = document.querySelector('.asset-detail img'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 20000 });
    check('uploading an image adds it and previews it', /was added/.test(added.status), added.status);

    const countBefore = (await items(page)).length;
    const duplicate = await uploadThroughPanel(page, { name: 'same-picture-renamed.png', mimeType: 'image/png', buffer: fs.readFileSync(image) });
    check('the same picture under another name is recognised, not stored twice',
        /already in your library/.test(duplicate.status) && duplicate.id === imageId && (await items(page)).length === countBefore, duplicate.status);

    const audioId = (await uploadThroughPanel(page, audio)).id;
    await page.waitForFunction(() => { const a = document.querySelector('.asset-detail audio'); return a && a.readyState >= 1 && a.duration > 1; }, null, { timeout: 20000 });
    check('uploading audio adds it and it plays in the preview', true);

    const clipId = (await uploadThroughPanel(page, clip)).id;

    // ---- Description and keywords (what lessons find an asset by) --------------------------------
    const editorOpen = async () => !!(await page.$('.asset-words-form'));
    const patches = [];
    page.on('request', r => { if (r.method() === 'PATCH') patches.push(r.url()); });
    check('a new video opens its description editor right after upload',
        (await editorOpen()) && /Describe it below/.test(await status(page))
        && await page.evaluate(() => document.activeElement && document.activeElement.classList.contains('asset-description-input')), await status(page));
    await page.evaluate(() => { window.metadataCheckNoReload = true; });
    await page.fill('.asset-description-input', 'text that should be thrown away');
    await page.fill('.asset-keywords-input', 'discard, me');
    await page.click('.asset-meta-cancel');
    const afterCancel = (await api('GET', `/api/assets/${clipId}`, token)).data;
    check('Cancel closes the editor and saves nothing',
        !(await editorOpen()) && patches.length === 0 && Object.keys(afterCancel.details).length === 0
        && /Not described yet/.test(await page.textContent('.asset-words')));
    await page.click('.asset-edit-meta');
    await page.keyboard.press('Escape');
    check('Escape while editing cancels the edit and keeps the library open', !(await editorOpen()) && !!(await page.$('.asset-overlay.open')));

    await page.click('.asset-edit-meta');
    await page.fill('.asset-description-input', '  A steel bridge   carrying heavy traffic  ');
    await page.fill('.asset-keywords-input', 'bridge, Traffic ,, load, stress, bridge,  structural   load ');
    await page.screenshot({ path: path.join(OUT, '1a-asset-editor.png') });
    await page.click('.asset-meta-save');
    await waitStatus(page, /✓ Details saved/);
    const described = await page.evaluate(() => ({
        description: (document.querySelector('.asset-words .asset-description') || {}).textContent,
        keywords: [...document.querySelectorAll('.asset-words .asset-keyword')].map(k => k.textContent),
        editor: !!document.querySelector('.asset-words-form'),
        noReload: window.metadataCheckNoReload === true
    }));
    const saved = (await api('GET', `/api/assets/${clipId}`, token)).data.details;
    check('Save stores the tidied description and keywords and updates the panel in place (no reload)',
        described.description === 'A steel bridge carrying heavy traffic' && described.keywords.join('|') === 'bridge|Traffic|load|stress|structural load'
        && !described.editor && described.noReload && patches.length === 1
        && saved.description === described.description && saved.keywords.join('|') === described.keywords.join('|'),
        `"${described.description}" [${described.keywords.join(', ')}]`);
    await page.screenshot({ path: path.join(OUT, '1b-asset-described.png') });

    await page.click(`.asset-item[data-id="${imageId}"]`);
    await page.waitForFunction(() => { const i = document.querySelector('.asset-detail img'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 20000 });
    await page.click(`.asset-item[data-id="${clipId}"]`);
    await page.waitForFunction(() => document.querySelector('.asset-detail video') && document.querySelector('.asset-edit-meta'), null, { timeout: 20000 });
    check('an asset opened again shows its saved description and keywords',
        (await page.textContent('.asset-words .asset-description')) === 'A steel bridge carrying heavy traffic'
        && (await page.$$('.asset-words .asset-keyword')).length === 5);

    const routed = await page.evaluate(async () => {
        const res = await fetch('/api/visuals/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scenes: [{ type: 'ai_video', title: 'Bridge under heavy traffic', prompt: 'Show a bridge experiencing heavy traffic load' }], allow_ai_generation: false }) });
        return (await res.json()).plans[0];
    });
    check('the visual router now picks this upload for a matching scene', routed.asset_id === clipId && routed.selection === 'matched',
        `${routed.source} ${routed.selection}, relevance ${routed.score}`);

    await page.fill('.asset-search', `check-audio-${tag}`);
    await page.waitForFunction(() => document.querySelectorAll('.asset-item').length === 1, null, { timeout: 10000 });
    check('search finds an asset by name', (await items(page))[0].id === audioId);

    // (Phase 21: asset ids and the copy buttons are developer details, shown in the debug view only; the normal view must not
    // show them)
    await page.click('.asset-item');
    await page.waitForSelector('.asset-detail audio');
    check('the normal view shows no asset id or copy buttons', !(await page.isVisible('button:has-text("Copy asset ID")'))
        && !(await page.textContent('.asset-detail')).includes(audioId));
    await page.evaluate(() => { assetLibrary.debug = true; });
    await page.click('.asset-item'); // drawn again, in the debug view
    await page.waitForSelector('button:has-text("Copy asset ID")', { timeout: 10000 });
    await page.click('button:has-text("Copy asset ID")');
    await waitStatus(page, /copied/);
    check('Copy asset ID puts the ID on the clipboard', (await page.evaluate(() => navigator.clipboard.readText())) === audioId);

    await page.click('.asset-delete');
    await waitStatus(page, /was deleted/);
    await loaded(page);
    check('an unused asset can be deleted', !(await items(page)).some(i => i.id === audioId) && (await api('GET', `/api/assets/${audioId}`, token)).status === 404);

    await page.fill('.asset-search', 'zz-no-such-asset-zz');
    await page.waitForSelector('.asset-empty');
    check('a search with no results says so', /No assets match/.test(await page.textContent('.asset-empty')));
    await page.fill('.asset-search', '');

    // ---- An asset used by a saved lesson is kept ---------------------------------------------
    const scenes = [
        { type: 'title', title: 'LIBRARY LESSON', aadhi_position: 'left', narration: 'Hello.' },
        { type: 'content', title: 'A PICTURE FROM THE LIBRARY', aadhi_position: 'right', html: `<p>Diagram:</p><img id="lib-img" src="asset:${imageId}" alt="diagram">` },
        { type: 'ai_video', title: 'A CLIP FROM THE LIBRARY', aadhi_position: 'left', prompt: 'library clip', video_asset_id: clipId }
    ];
    const projectId = await page.evaluate(async s => {
        const res = await fetch('/save-history', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject_name: 'Library check', scenes: s }) });
        return (await res.json()).id;
    }, scenes);
    await page.fill('.asset-search', `check-image-${tag}`);
    await page.waitForFunction(() => document.querySelectorAll('.asset-item').length === 1, null, { timeout: 10000 });
    await page.click('.asset-item');
    await page.waitForFunction(() => /Used by1 saved lesson/.test(document.querySelector('.asset-detail').textContent), null, { timeout: 10000 });
    const lock = await page.$eval('.asset-delete', b => ({ disabled: b.disabled, title: b.title }));
    const forced = await api('DELETE', `/api/assets/${imageId}`, token);
    check('an asset used by a saved lesson cannot be deleted', lock.disabled && forced.status === 409, `${lock.title}; API ${forced.status}`);
    await page.screenshot({ path: path.join(OUT, '2-library-image.png') });

    // ---- Error state ---------------------------------------------------------------------------------
    expectingServerError = true;
    await page.route('**/api/assets?*', r => r.fulfill({ status: 503, json: { detail: 'Library temporarily unavailable' } }));
    await page.selectOption('.asset-filter[aria-label="Type"]', 'image');
    await waitStatus(page, /could not be loaded/);
    check('a server error is shown, not a blank list', /Library temporarily unavailable/.test(await status(page)), await status(page));
    await page.unroute('**/api/assets?*');
    expectingServerError = false;
    await page.keyboard.press('Escape');
    check('Escape closes the panel', !(await page.$('.asset-overlay.open')));

    // ---- A lesson that uses library assets plays them --------------------------------------------
    await page.goto(`${BASE}/?project_id=${projectId}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 20000 });
    await page.click('#start-lecture-btn');
    await page.waitForFunction(() => document.body.classList.contains('presentation-active'));
    await page.evaluate(() => skipIntroSequence());
    await page.waitForFunction(() => currentSlide === 0 && document.querySelector('.main-title'), null, { timeout: 20000 });
    await page.evaluate(() => { currentSlide = 1; renderSlide(1); });
    await page.waitForFunction(() => { const i = document.getElementById('lib-img'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 20000 });
    const imgSrc = await page.$eval('#lib-img', i => i.getAttribute('src'));
    check('a scene\'s <img src="asset:ID"> shows the library picture', imgSrc.includes(`/api/assets/${imageId}/content?token=`), imgSrc.slice(0, 60) + '…');
    await page.evaluate(() => { currentSlide = 2; renderSlide(2); });
    await page.waitForFunction(() => { const v = document.getElementById('active-ai-video'); return v && v.readyState >= 2; }, null, { timeout: 20000 });
    const videoSrc = await page.$eval('#active-ai-video', v => v.getAttribute('src'));
    check('a scene\'s video_asset_id plays the library clip', videoSrc.includes(`/api/assets/${clipId}/content?token=`));
    await page.screenshot({ path: path.join(OUT, '3-lesson-uses-library.png') });
    await context.close();

    // ---- Another account, on a phone-sized screen -------------------------------------------------
    const viewerName = 'assets_viewer_' + Date.now();
    const viewerPassword = 'viewer-' + Math.random().toString(36).slice(2);
    await api('POST', '/api/register', token, { username: viewerName, password: viewerPassword });
    const viewer = await login(viewerName, viewerPassword);
    const phone = await openPage(viewer, { width: 390, height: 844 });
    // Opened directly: the start screen itself is taller than a phone screen and cannot scroll
    // (a limitation of that screen, older than the library), which hides its top buttons
    await phone.page.evaluate(() => assetLibrary.open());
    await phone.page.waitForSelector('.asset-overlay.open');
    await loaded(phone.page);
    const theirs = await items(phone.page);
    check('another account sees only the shared assets, not your uploads',
        theirs.length > 0 && theirs.every(i => /Shared/.test(i.text)) && !theirs.some(i => [imageId, clipId].includes(i.id)), `${theirs.length} shared assets`);
    const peek = await api('GET', `/api/assets/${imageId}`, viewer);
    const resolved = (await api('POST', '/api/assets/resolve', viewer, { ids: [imageId] })).data;
    check('another account cannot open or resolve your asset', peek.status === 404 && resolved.missing.includes(imageId));
    await phone.page.click('.asset-item');
    await phone.page.waitForSelector('.asset-detail .asset-preview');
    await phone.page.waitForFunction(() => document.querySelector('.asset-detail video, .asset-detail img, .asset-detail audio'), null, { timeout: 20000 });
    const foreignEdit = await api('PATCH', `/api/assets/${clipId}`, viewer, { description: 'not yours' });
    check('another account cannot describe your asset, and shared assets offer no editor',
        foreignEdit.status === 404 && !(await phone.page.$('.asset-edit-meta'))
        && (await api('GET', `/api/assets/${clipId}`, token)).data.details.description === 'A steel bridge carrying heavy traffic', `PATCH ${foreignEdit.status}`);
    const layout = await phone.page.evaluate(() => {
        const list = document.querySelector('.asset-list').getBoundingClientRect();
        const detail = document.querySelector('.asset-detail').getBoundingClientRect();
        const panel = document.querySelector('.asset-panel').getBoundingClientRect();
        return { panelFits: panel.left >= 0 && panel.right <= innerWidth, stacked: detail.top >= list.bottom - 1, noSideScroll: document.documentElement.scrollWidth <= innerWidth };
    });
    check('on a phone the panel fits and the preview sits below the list', layout.panelFits && layout.stacked && layout.noSideScroll, JSON.stringify(layout));
    await phone.page.screenshot({ path: path.join(OUT, '4-library-phone.png') });
    await phone.context.close();

    check('no console errors, page errors or failed requests', problems.length === 0, problems.join(' | '));
} finally {
    await browser.close();
}

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Screenshots: ${OUT}`);
process.exit(failed ? 1 : 0);
