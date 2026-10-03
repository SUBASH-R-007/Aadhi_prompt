// Source Document Formatting Assistant check (Phase 11) in real Chrome against a throwaway server:
//
//   A  a well-structured lesson as a PDF (printed by Chrome, read by the page's pdf.js): analysed as well
//      structured, objectives / definitions / formula found with their source references
//   B  a poorly structured text file, analysed with the stand-in AI model: missing headings, a long paragraph,
//      repeated content and a missing figure found; AI findings labelled; an issue marked resolved
//   C  a technical Word document (real Word styles, read by the page's mammoth): formula and code preserved;
//      a section renamed, one left out, an objective added, saved; "Write the lesson from this" hands the
//      prepared structure to the existing lesson generation (the /generate-script call is answered by a fixed
//      screenplay here, no AI provider), the lesson is saved with its source, plays, shows in Visual Review
//      and is exported
//   D  a Tamil text: the language is recognised and the text is not translated
//   and pasted text, and a stale analysis (the file changed) that cannot be used for generation.
//
// Needs Chrome, playwright-core (npm install --no-save playwright-core, or PLAYWRIGHT_CORE_DIR) and the repo's
// virtualenv (.venv):   AADHI_PASSWORD=... node tests/source_documents_browser_check.mjs
// Silent: no audio output, browser speech stubbed, narration answered with silence.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const FIXTURES = path.join(REPO, 'tests', 'fixtures', 'sources');
const OUT = process.env.SOURCES_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-sources-check');
const PASSWORD = process.env.AADHI_PASSWORD;
const PORT = Number(process.env.SOURCES_CHECK_PORT || 9920 + (process.pid % 15));
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
for (const dir of ['assets', 'exports', 'static']) fs.mkdirSync(path.join(data, dir), { recursive: true });
const venvScripts = path.join(REPO, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
const PYTHON = path.join(venvScripts, process.platform === 'win32' ? 'python.exe' : 'python');
let server = null;

function startServer() {
    const log = fs.openSync(path.join(OUT, 'server.log'), 'w');
    server = spawn(PYTHON, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(PORT)], {
        cwd: REPO, stdio: ['ignore', log, log],
        env: {
            ...process.env, PATH: venvScripts + path.delimiter + process.env.PATH,
            DATABASE_URL: 'sqlite:///' + path.join(data, 'check.db').replace(/\\/g, '/'),
            ASSETS_DIR: path.join(data, 'assets'), EXPORTS_DIR: path.join(data, 'exports'), STATIC_DIR: path.join(data, 'static'),
            MANIM_SANDBOX_DIR: path.join(os.tmpdir(), `asc-${process.pid}`),
            JWT_SECRET: 'sources-check-' + Math.random().toString(36).slice(2), AI_FAKE_PROVIDER: '1', AI_GENERATION_ENABLED: '0',
            AI_RECOVERY_ENABLED: '1', AI_RECOVERY_INTERVAL: '2', AI_MEDIA_LOG: '1', GEMINI_API_KEY: '', OPENAI_API_KEY: ''
        }
    });
}
process.on('exit', () => { if (server && server.exitCode === null) spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']); });

async function waitForServer() {
    for (let i = 0; i < 120; i++) {
        if (server.exitCode !== null) throw new Error('the test server stopped; see ' + OUT);
        try { if ((await fetch(BASE + '/sources.js')).ok) return; } catch (e) { /* not up yet */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('the test server did not start');
}

let token = null;
async function api(method, route, body) {
    const res = await fetch(BASE + route, { method, headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined });
    return { status: res.status, data: await res.json().catch(() => null) };
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 60000, every = 300) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
        try { const v = await fn(); if (v) return v; } catch (e) { /* not yet */ }
        await sleep(every);
    }
    return null;
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

// Fixture A as a printable page (its text file turned into HTML headings, paragraphs and lists)
function htmlOf(text) {
    const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    let html = '<html><head><meta charset="utf-8"><style>body{font:12pt serif;margin:2cm} h1{font-size:24pt} h2{font-size:16pt}</style></head><body>';
    let list = false;
    for (const raw of text.split(/\r?\n/)) {
        const line = raw.trim();
        if (!line.startsWith('- ') && list) { html += '</ul>'; list = false; }
        if (!line) continue;
        if (line.startsWith('Title:')) html += `<h1>${esc(line.slice(6).trim())}</h1>`;
        else if (line.startsWith('## ')) html += `<h2>${esc(line.slice(3))}</h2>`;
        else if (line.startsWith('- ')) { if (!list) { html += '<ul>'; list = true; } html += `<li>${esc(line.slice(2))}</li>`; }
        else html += `<p>${esc(line)}</p>`;
    }
    return html + (list ? '</ul>' : '') + '</body></html>';
}

const screenplay = {
    subject_name: 'Motion and Algorithms', unit_name: 'Unit 1', session_number: 'Session 1', session_title: "Newton's Law of Motion", concept_map: null,
    scenes: [
        { type: 'content', title: "Newton's Law of Motion", aadhi_position: 'hidden', html: '<h2>F = m × a</h2><p>Force is defined as the product of mass and acceleration.</p>', narration: 'Force equals mass times acceleration.' },
        { type: 'content', title: 'Binary Search', aadhi_position: 'hidden', html: '<p>Binary search halves the search range at every step.</p>', narration: 'Binary search halves the range each step.',
          side_panel: { type: 'chart', chart_type: 'bar', data: { labels: ['8', '1024'], datasets: [{ label: 'Comparisons', data: [3, 10] }] } } }
    ]
};

const problems = [];
let browser;
try {
    startServer();
    await waitForServer();
    token = (await (await fetch(BASE + '/api/login', { method: 'POST', body: new URLSearchParams({ username: 'admin', password: PASSWORD }) })).json()).access_token;
    const { chromium } = await loadPlaywright();
    browser = await chromium.launch({ channel: 'chrome', headless: true, ignoreDefaultArgs: ['--mute-audio'],
        args: ['--auto-accept-this-tab-capture', '--autoplay-policy=no-user-gesture-required', '--disable-audio-output'] });

    // Fixture files: A printed to PDF by Chrome, C written as a Word document
    const printer = await browser.newPage();
    await printer.setContent(htmlOf(fs.readFileSync(path.join(FIXTURES, 'A_well_structured.txt'), 'utf8')));
    const pdfPath = path.join(OUT, 'A_well_structured.pdf');
    await printer.pdf({ path: pdfPath, format: 'A4' });
    await printer.close();
    const docxPath = path.join(OUT, 'C_technical.docx');
    spawnSync(PYTHON, [path.join(FIXTURES, 'make_docx.py'), docxPath], { stdio: 'inherit' });
    const txtB = path.join(FIXTURES, 'B_poorly_structured.txt');
    const txtD = path.join(FIXTURES, 'D_tamil.txt');

    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await context.addInitScript(t => localStorage.setItem('jwt_token', t), token);
    await context.addInitScript(silentPage);
    await context.route('**/generate-audio', r => r.fulfill({ json: { status: 'success', audio_url: `/__silence.wav?n=${Math.random()}` } }));
    await context.route('**/__silence.wav*', r => r.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav() }));
    let generateRequest = null;
    await context.route('**/generate-script', async r => {
        generateRequest = JSON.parse(r.request().postData() || '{}');
        await r.fulfill({ status: 200, contentType: 'text/plain', body: `STATUS:STARTED\nPROGRESS:100\nFINAL_JSON:${JSON.stringify(screenplay)}\n` });
    });
    const page = await context.newPage();
    page.on('pageerror', e => problems.push(`page error: ${e.message}`));
    page.on('console', m => {
        const text = m.text();
        if (m.type() === 'error' && !/Failed to load resource/.test(text) && !text.startsWith('Logo video play error AbortError')) problems.push(`console: ${text}`);
    });
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
    await page.goto(BASE + '/');
    await page.waitForSelector('#drop-zone', { state: 'visible', timeout: 30000 });
    // (Phase 21: "Write the lesson from this" goes to the Studio; this check verifies the one-step path, chosen as a teacher would)
    await page.click('#nav-settings');
    await page.check('#classic-generation');
    await page.click('#settings-close');
    const overlay = () => page.textContent('.doc-overlay.open');

    async function analyse(file, provider = null) {
        await page.evaluate(p => { window.documentAssistant.options = () => (p ? { provider: p, model: 'stand-in' } : { provider: 'gemini' }); }, provider);
        await page.setInputFiles('#file-input', file);
        await page.click('#analyze-btn');
        await page.waitForSelector('.doc-overlay.open .doc-card', { timeout: 60000 });
        await page.waitForFunction(() => !/AI analysis running/.test(document.querySelector('.doc-overlay.open .doc-ai').textContent), null, { timeout: 60000 });
        return overlay();
    }
    const close = () => page.click('.doc-overlay.open .export-close');

    // ---- A: well structured (PDF) ----------------------------------------------------------------------------------
    const a = await analyse(pdfPath);
    await page.screenshot({ path: path.join(OUT, 'A-analysis.png'), fullPage: false });
    const aView = await page.evaluate(() => window.documentAssistant.session.view);
    const objectivesA = aView.analysis.aadhi_ready.learning_objectives;
    check('A (PDF via pdf.js): the title, sections and learning objectives come from the document', /Photosynthesis in Green Plants/.test(a)
        && objectivesA.length === 3 && objectivesA.every(o => o.provenance === 'source') && /The Role of Chlorophyll/.test(a),
        `${objectivesA.length} objectives; ${aView.analysis.document.section_count} sections`);
    // (Phase 21: one plain verdict on screen; the kind of verdict is the server's)
    check('A: analysed as well structured and ready for generation', aView.analysis.readiness.verdict === 'well_structured'
        && /Ready to write the lesson/.test(a) && !/Could be clearer/.test(a), aView.analysis.readiness.verdict);
    const formulaA = await page.$$eval('.doc-overlay.open .doc-formula', els => els.map(e => e.textContent));
    const defA = aView.analysis.aadhi_ready.sections.flatMap(s => s.subtopics.flatMap(st => st.definitions));
    check('A: definitions and the formula are traced to their page and section', formulaA.includes('6CO2 + 6H2O → C6H12O6 + 6O2')
        && defA.length >= 2 && defA.every(d => d.ref && d.ref.page === 1 && d.ref.heading), defA.map(d => `${d.term} @ p.${d.ref && d.ref.page}`).join(', '));
    await close();

    // ---- B: poorly structured (TXT), AI analysis with the stand-in model ----------------------------------------------
    const b = await analyse(txtB, 'fake');
    await page.screenshot({ path: path.join(OUT, 'B-issues.png') });
    const bView = await page.evaluate(() => window.documentAssistant.session.view);
    const bTypes = bView.analysis.quality.issues.map(i => i.type);
    check('B (TXT): poorly structured content is identified with reasons, sources and suggestions', bView.analysis.readiness.verdict === 'needs_reorganization'
        && /Could be clearer — \d+ notes?/.test(b) && !/Ready to write the lesson/.test(b)
        && ['missing_headings', 'long_paragraph', 'duplicate_content', 'missing_figure'].every(t => bTypes.includes(t))
        && bView.analysis.quality.issues.every(i => i.why && i.suggestion), bTypes.join(', '));
    check('B: the AI analysis ran as a durable job and its findings are labelled as AI', bView.analysis.ai.status === 'completed' && bView.run_id
        && /Found by AI/.test(b) && /Suggested by AI/.test(b) && bTypes.includes('ambiguous_explanation'), bView.analysis.ai.provider);
    const openBefore = (b.match(/Things to check \((\d+)\)/) || [])[1];
    await page.click('.doc-overlay.open [data-issue="missing_headings"] .doc-small');
    const openAfter = ((await overlay()).match(/Things to check \((\d+)\)/) || [])[1];
    const donePressed = await page.getAttribute('.doc-overlay.open [data-issue="missing_headings"] .doc-small', 'aria-pressed');
    check('B: an issue can be marked done', Number(openAfter) === Number(openBefore) - 1 && donePressed === 'true', `${openBefore} → ${openAfter} open; Done pressed ${donePressed}`);
    await close();

    // ---- C: technical (DOCX): preserved, edited, saved, used for generation ------------------------------------------
    const c = await analyse(docxPath);
    const cView = await page.evaluate(() => window.documentAssistant.session.view);
    const codeC = await page.$$eval('.doc-overlay.open .doc-code', els => els.map(e => e.textContent));
    check('C (DOCX via mammoth): the Word title and headings, the formula and the code are preserved', /Motion and Algorithms/.test(c)
        && (await page.$$eval('.doc-overlay.open .doc-formula', els => els.map(e => e.textContent))).includes('F = m × a')
        && codeC.some(t => t.includes('def area(r):\n    return 3.14159 * r * r')), codeC[0] && JSON.stringify(codeC[0]));
    const visualsC = cView.analysis.aadhi_ready.sections.flatMap(s => s.subtopics.flatMap(st => st.visual_opportunities)).map(v => v.type);
    check('C: the table and caption, the equation and the code are listed as visual opportunities',
        visualsC.includes('table') && visualsC.includes('equation') && visualsC.includes('code_walkthrough'), visualsC.join(', '));
    // Edit: rename the first section, leave the last one out, add an objective
    const titles = await page.$$('.doc-overlay.open .doc-structure-item input[aria-label="Section title"]');
    await titles[0].fill("Newton's Law of Motion");
    await titles[0].dispatchEvent('change');
    await page.locator('.doc-overlay.open .doc-structure-item').last().locator('input[type="checkbox"]').click();
    await page.fill('.doc-overlay.open input[placeholder="Add a learning objective"]', 'Apply F = m × a to a simple problem');
    await page.click('.doc-overlay.open .doc-add .doc-small');
    await page.click('.doc-overlay.open .doc-save');
    await page.waitForFunction(() => /saved/.test(document.querySelector('.doc-overlay.open .doc-status').textContent), null, { timeout: 15000 });
    const saved = (await api('GET', `/api/source-analyses/${cView.analysis_id}`)).data;
    const savedSections = saved.analysis.aadhi_ready.sections;
    const savedShown = await page.textContent('.doc-overlay.open .doc-saved').catch(() => '');
    check('C: the edits are saved and kept apart from what was found', saved.edited && savedSections[0].title === "Newton's Law of Motion"
        && savedSections[0].title_provenance === 'user_edited' && savedSections[savedSections.length - 1].included === false
        && saved.analysis.aadhi_ready.learning_objectives.some(o => o.provenance === 'user' && o.text.startsWith('Apply F')) && savedShown === '✓ Saved',
        `${savedSections.map(s => s.title + (s.included ? '' : ' (left out)')).join(' | ')}; ${JSON.stringify(savedShown)}`);
    await page.screenshot({ path: path.join(OUT, 'C-edited.png') });
    await page.click('.doc-overlay.open .doc-use');
    // After generating, the page plays the new lesson at once (as before Phase 11)
    await Promise.race([page.waitForFunction(() => slides.length > 0 && !document.getElementById('presentation-board').classList.contains('hidden'), null, { timeout: 60000 }),
        page.waitForSelector('#loading-overlay .error-message', { timeout: 60000 })]).catch(async e => {
        await page.screenshot({ path: path.join(OUT, 'C-after-use.png') });
        const state = await page.evaluate(() => ({ log: document.getElementById('loading-log').textContent.slice(-600),
            overlay: document.getElementById('loading-overlay').className, slides: slides.length }));
        throw new Error(`no lesson after "Write the lesson from this": ${JSON.stringify(state)}; problems: ${problems.slice(0, 3).join(' | ')}`);
    });
    const generationError = await page.$eval('#loading-overlay .error-message', e => e.textContent).catch(() => null);
    if (generationError) {
        await page.screenshot({ path: path.join(OUT, 'C-generation-error.png') });
        throw new Error('lesson generation failed: ' + generationError.replace(/\s+/g, ' ').trim());
    }
    const prompt = (generateRequest && generateRequest.prompt_text) || '';
    check('C: "Write the lesson from this" gives the existing Lesson Director the prepared structure', prompt.includes('prepared and reviewed in the Document Assistant')
        && prompt.includes("SECTION 1: Newton's Law of Motion") && prompt.includes('F = m × a') && prompt.includes('    def area(r):')
        && !prompt.includes('Left out later') && prompt.includes('Apply F = m × a to a simple problem [USER]'), `${prompt.length} characters`);
    const projectId = await until(() => page.evaluate(() => currentProjectId), 15000);
    const project = projectId && (await api('GET', `/api/projects/${projectId}`)).data;
    check('C: the generated lesson is saved with the source it was prepared from', project && project.source_document
        && project.source_document.analysis_id === cView.analysis_id && project.scenes.length === 2, JSON.stringify(project && project.source_document));

    // The existing pipeline continues unchanged: play, plan, review, export
    await page.waitForFunction(() => window.lessonVisualPlans && window.lessonVisualPlans.length > 0, null, { timeout: 30000 }).catch(() => {});
    const played = await page.evaluate(() => ({ plans: (window.lessonVisualPlans || []).length, titles: slides.map(s => s.title),
        board: document.getElementById('presentation-board').textContent }));
    await page.screenshot({ path: path.join(OUT, 'C-playing.png') });
    check('C: the generated lesson plays (visual router planned, its scenes on the board)', played.plans > 0
        && /Newton's Law of Motion|Binary Search/.test(played.board), `${played.plans} plans; ${played.titles.join(' | ')}`);
    await page.goto(`${BASE}/?project_id=${projectId}`);
    await page.waitForSelector('#start-overlay', { state: 'visible', timeout: 30000 });
    await page.click('#start-review-btn');
    await page.waitForSelector('.review-overlay.open .review-item', { timeout: 30000 });
    const reviewCount = await page.$$eval('.review-overlay.open .review-item', els => els.length);
    await page.click('.review-panel .export-close');
    check('C: Visual Review shows the generated lesson\'s visual', reviewCount === 1, `${reviewCount} item`);
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
    const outcome = await until(() => page.evaluate(() => {
        const message = document.querySelector('.export-message');
        if (!document.querySelector('.export-ready').hidden) return 'ready';
        return message && message.getAttribute('data-kind') === 'error' ? 'error: ' + message.textContent : null;
    }), 300000, 1000);
    const job = await page.evaluate(() => exportFlow.job);
    const record = job && (await api('GET', `/api/exports/${job.id}`)).data;
    await page.screenshot({ path: path.join(OUT, 'C-exported.png') });
    check('C: the lesson generated from the prepared source is exported', outcome === 'ready' && record && record.status === 'COMPLETED',
        record && `${record.status}, ${record.duration_seconds}s`);

    // ---- D: Tamil (TXT) ------------------------------------------------------------------------------------------------
    await page.goto(BASE + '/');
    await page.waitForSelector('#drop-zone', { state: 'visible', timeout: 30000 });
    const d = await analyse(txtD);
    const dView = await page.evaluate(() => window.documentAssistant.session.view);
    check('D (Tamil): the language is recognised and the text is kept, not translated', /Tamil/.test(d) && d.includes('ஒளிச்சேர்க்கை')
        && dView.analysis.document.language.code === 'ta' && dView.analysis.aadhi_ready.learning_objectives.length === 1, dView.analysis.document.language.name);
    await close();

    // ---- pasted text, and a stale analysis ------------------------------------------------------------------------------
    await page.evaluate(() => window.documentAssistant.open(null)); // reopens on the last analysis
    await page.click('.doc-overlay.open .doc-new');
    await page.fill('.doc-overlay.open .doc-paste', 'Title: Levers\n\n## Types of lever\nA lever is a rigid bar that turns about a fixed point called the fulcrum.');
    await page.click('.doc-overlay.open .doc-analyze-text');
    await page.waitForSelector('.doc-overlay.open .doc-card', { timeout: 30000 });
    const pasted = await overlay();
    check('Pasted text is analysed like a document', /Levers/.test(pasted) && /Pasted text/.test(pasted) && /A lever is a rigid bar/.test(pasted));
    const blocks = [{ type: 'heading', level: 1, text: 'Stale check' }, { type: 'paragraph', text: 'First version of the text.' }];
    const v1 = (await api('POST', '/api/source-documents', { file_name: 'stale.txt', source_type: 'txt', blocks })).data.document.document_id;
    const v1Analysis = (await api('POST', `/api/source-documents/${v1}/analyze`, { mode: 'structural' })).data.analysis_id;
    await api('POST', '/api/source-documents', { file_name: 'stale.txt', source_type: 'txt', blocks: [...blocks, { type: 'paragraph', text: 'Changed.' }] });
    const staleView = (await api('GET', `/api/source-analyses/${v1Analysis}`)).data;
    await page.evaluate(v => window.documentAssistant.show(v), staleView);
    const staleShown = await page.isVisible('.doc-overlay.open .doc-stale');
    const useDisabled = await page.isDisabled('.doc-overlay.open .doc-use');
    check('A stale analysis (the file changed) is flagged and cannot be used for generation', staleView.stale && staleShown && useDisabled, staleView.stale_reason);
    await close();
    check('no unexpected page errors or failed requests', problems.length === 0, problems.slice(0, 4).join(' | '));
} catch (e) {
    check('check ran to the end', false, e.stack || e.message);
} finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F']);
}
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Artifacts: ${OUT}`);
process.exit(failed.length ? 1 : 0);
