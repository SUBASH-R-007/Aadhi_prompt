'use strict';
// Unit tests for the videos panel (export.js, Phase 21): "Your videos" as the one place for exported videos, the finished
// export first with "Export again" second, statuses as icon + words, errors in plain words (the raw text only with
// ?visualDebug) and keyboard use of the dialog. Checked over the small fake DOM the other UI tests use.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const { ExportError, ExportFlow, ExportPanel, formatName, plainReason } = require('../export.js');
const { Node, fakeDoc } = require('./helpers/cinematic-dom.js');

// What the fake DOM lacks for these tests: focus, containment and media
Node.prototype.focus = function () { this.doc.activeElement = this; };
Node.prototype.contains = function (node) { for (let n = node; n; n = n.parent) if (n === this) return true; return false; };
Node.prototype.pause = function () {};
Node.prototype.load = function () {};
Node.prototype.remove = function () { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); };

const blobOf = size => new Blob([new Uint8Array(size)]);
const httpError = (status, message = `error ${status}`, data = null) => Object.assign(new Error(message), { status, data });

function keyboardDoc() {
    const doc = fakeDoc();
    const listeners = [];
    doc.addEventListener = (type, fn) => listeners.push({ type, fn });
    doc.removeEventListener = (type, fn) => {
        const i = listeners.findIndex(l => l.type === type && l.fn === fn);
        if (i >= 0) listeners.splice(i, 1);
    };
    doc.press = (key, shift = false) => {
        const e = { key, shiftKey: shift, target: doc.activeElement, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
        listeners.filter(l => l.type === 'keydown').slice().forEach(l => l.fn(e));
        return e;
    };
    doc.activeElement = doc.body;
    return doc;
}

function realPanel() {
    const doc = keyboardDoc();
    const panel = new ExportPanel(doc);
    panel.build();
    return { doc, panel };
}

const noop = () => {};
const VIDEO = { id: 'v1', project_id: 7, title: 'Stress — Lesson 1', status: 'COMPLETED', format: 'webm', height: 720, duration_seconds: 83, file_size: 5 * 1048576,
    completed_at: '2026-10-02T09:15:00Z', has_audio: true };

test('the panel is "Your videos": the finished export comes first, Download is the main action and Export again the second', () => {
    const { panel } = realPanel();
    assert.equal(panel.panel.querySelector('#export-panel-title').textContent, 'Your videos');
    const sections = panel.panel.children.filter(c => c.tag === 'section').map(c => c.className);
    assert.deepEqual(sections, ['export-run', 'export-ready', 'export-start', 'export-history']);

    panel.setLesson({ title: 'Stress — Lesson 1', canExport: true }, noop);
    assert.equal(panel.startButton.className, 'btn-gold export-start-btn ui-focusable');
    assert.equal(panel.startButton.textContent, '● Export video');

    panel.showReady(VIDEO, [{ label: '⬇ Download Video (WebM)', primary: true, onClick: noop }], null, { finished: true });
    assert.equal(panel.readyTitle.textContent, '✓ Export complete');
    assert.equal(panel.readyMeta.textContent, 'Stress — Lesson 1 · WebM video · 720p · 1:23 · 5.0 MB · ' + panel.readyMeta.textContent.split(' · ').at(-1));
    assert.match(panel.readyNext.textContent, /^Your video is saved in your account\. Download it to share it/);
    assert.equal(panel.readyActions.querySelector('button').className, 'btn-gold export-action ui-focusable');
    assert.ok(panel.startButton.classList.contains('export-start-btn') && panel.startButton.classList.contains('export-action-secondary'));
    assert.equal(panel.startButton.textContent, '● Export again');
    assert.equal(panel.startHint.hidden, true);

    // A video opened from the list: no "complete" claim, and the start button stays secondary
    panel.showReady({ ...VIDEO, has_audio: false }, [], null);
    assert.equal(panel.readyTitle.textContent, 'Your video (no sound)');
    assert.equal(panel.readyNext.textContent, '');
    assert.equal(panel.startButton.textContent, '● Export video');
    assert.ok(panel.startButton.classList.contains('export-action-secondary'));
    panel.showReady({ ...VIDEO, has_audio: false }, [], null, { finished: true });
    assert.equal(panel.readyTitle.textContent, '⚠ Export complete, but the video has no sound');

    // A new run: the start button is the main one again (disabled while busy)
    panel.beginRun([['prepare', 'Preparing lesson']]);
    assert.equal(panel.readySection.hidden, true);
    assert.equal(panel.startButton.className, 'btn-gold export-start-btn ui-focusable');
});

test('progress steps say their state in words, and the preview shows its first frame', () => {
    const { panel } = realPanel();
    panel.beginRun([['prepare', 'Preparing lesson'], ['upload', 'Uploading video']]);
    const state = key => panel.steps[key].li.querySelector('.export-step-state');
    assert.equal(state('prepare').textContent, 'not started');
    assert.ok(state('prepare').classList.contains('ui-visually-hidden'));
    panel.step('prepare', 'active', 'Saving the lesson', null);
    assert.equal(state('prepare').textContent, 'in progress');
    panel.step('prepare', 'done', 'Everything the lesson needs is loaded');
    assert.equal(state('prepare').textContent, 'done');
    panel.step('upload', 'active', '1 MB of 5 MB (20%)', 0.2);
    panel.failActive('The upload stopped.');
    assert.equal(state('upload').textContent, 'failed');
    assert.equal(panel.steps.upload.li.getAttribute('data-state'), 'failed');
    assert.equal(panel.messageEl.getAttribute('data-kind'), 'error');

    panel.setPreview('/api/exports/v1/download?token=t&inline=1', noop);
    assert.equal(panel.video.src, '/api/exports/v1/download?token=t&inline=1#t=0.1');
    panel.setPreview('/x#t=2', noop);
    assert.equal(panel.video.src, '/x#t=2');
    let retried = 0;
    panel.previewError('The preview could not be loaded.', () => retried++);
    panel.previewStatus.querySelector('button').fire('click');
    assert.equal(retried, 1);
});

test('the list: statuses as icon + words, a failed video without its raw error text unless in debug mode', () => {
    const { panel } = realPanel();
    panel.setLesson({ title: 'Stress', canExport: true }, noop);
    const failed = { id: 'v2', project_id: 7, title: 'Stress', status: 'FAILED', source: 'lesson', created_at: '2026-10-02T09:00:00Z',
        error_message: 'The recording could not be read as a video: Invalid data found when processing input.' };
    const handlers = { onRefresh: noop, onPreview: noop, onDownload: noop, onDownloadOutput: noop, retryFor: () => ({ label: 'Retry', run: noop }) };
    panel.renderHistory([VIDEO, failed], handlers);
    const rows = panel.historyList.querySelectorAll('.export-history-item');
    const badge = row => row.querySelector('.export-badge');
    assert.equal(badge(rows[0]).getAttribute('data-status'), 'COMPLETED');
    assert.equal(badge(rows[0]).textContent, '✓ Ready');
    assert.equal(badge(rows[0]).querySelector('[aria-hidden="true"]').textContent, '✓ ');
    assert.equal(badge(rows[1]).textContent, '✕ Failed');
    assert.deepEqual(rows[0].querySelectorAll('button').map(b => b.textContent), ['Preview', 'Download']);
    assert.equal(rows[0].querySelectorAll('button')[1].getAttribute('aria-label'), 'Download: Stress — Lesson 1');
    assert.deepEqual(rows[1].querySelectorAll('button').map(b => b.textContent), ['Retry']);
    assert.doesNotMatch(rows[1].textContent, /Invalid data/);
    assert.match(rows[0].querySelector('.export-history-detail').textContent, /^WebM video · 720p/);

    panel.renderHistory([failed], { ...handlers, debug: true });
    assert.match(panel.historyList.textContent, /Invalid data found/);

    let again = 0;
    panel.historyError('Your videos could not be loaded. They are safe in your account.', () => again++);
    assert.equal(panel.historyList.querySelector('.export-history-empty').textContent, 'Your videos could not be loaded. They are safe in your account. Try again');
    panel.historyList.querySelector('button').fire('click');
    assert.equal(again, 1);
});

test('keyboard: the focus moves into the panel, Tab stays inside, Escape closes (never while recording) and the focus goes back', () => {
    const doc = keyboardDoc();
    const opener = doc.createElement('button');
    doc.body.appendChild(opener);
    opener.focus();
    const panel = new ExportPanel(doc);
    panel.open();
    assert.equal(doc.activeElement, panel.titleEl);
    assert.equal(panel.panel.getAttribute('role'), 'dialog');
    assert.equal(panel.panel.getAttribute('aria-labelledby'), 'export-panel-title');
    assert.equal(panel.closeButton.getAttribute('title'), 'Close');

    opener.focus();
    assert.ok(doc.press('Tab').defaultPrevented);
    assert.equal(doc.activeElement, panel.closeButton); // the first control
    assert.ok(doc.press('Tab', true).defaultPrevented);
    assert.equal(doc.activeElement, panel.historyRefresh); // the last one (the list is empty)
    doc.press('Tab');
    assert.equal(doc.activeElement, panel.closeButton);

    panel.open(); // opened again while open (a run starting): the opener is kept
    panel.hide(); // recording: Escape and Tab belong to the page
    assert.equal(doc.press('Escape').defaultPrevented, false);
    assert.ok(panel.root.classList.contains('open'));
    panel.show();
    const library = doc.querySelector;
    doc.querySelector = sel => (/asset-overlay\.open/.test(sel) ? {} : library(sel)); // the Library opened on top has the keys
    doc.press('Escape');
    assert.ok(panel.root.classList.contains('open'));
    doc.querySelector = library;
    doc.press('Escape');
    assert.ok(!panel.root.classList.contains('open'));
    assert.equal(doc.activeElement, opener);
});

// ---- the flow's messages ------------------------------------------------------------------------------------------------

function fakeApi({ failUpload = null, failComplete = null, failLink = null } = {}) {
    const jobs = {};
    let seq = 0;
    return {
        jobs,
        base: () => '',
        async create(body) { const job = { id: `job${++seq}`, status: 'QUEUED', received: 0, ...body }; jobs[job.id] = job; return { ...job }; },
        async update(id, body) { Object.assign(jobs[id], body); return { ...jobs[id] }; },
        async get(id) { return { ...jobs[id] }; },
        async received(id) { return jobs[id].received; },
        async sendChunk(id, offset, chunk, total, onProgress) {
            if (failUpload) throw failUpload;
            jobs[id].received = offset + chunk.size;
            onProgress(chunk.size);
            return jobs[id].received;
        },
        async complete(id, body) {
            if (failComplete) throw failComplete;
            Object.assign(jobs[id], { status: 'COMPLETED', file_size: body.size, has_audio: true, format: 'webm' });
            return { ...jobs[id] };
        },
        async list() { return { exports: Object.values(jobs) }; },
        async link(id) {
            if (failLink) throw failLink;
            return { preview_url: `/preview/${id}`, download_url: `/download/${id}`, output_urls: {} };
        }
    };
}

function fakeRecorder() {
    return class {
        async capture() {}
        start() { this.finished = new Promise(resolve => { this.resolve = resolve; }); return Promise.resolve(); }
        async stop() { this.resolve({ blob: blobOf(5000), mimeType: 'video/webm;codecs=vp9,opus', bytes: 5000, durationMs: 42000 }); return this.finished; }
        abort() {}
    };
}

function setupFlow({ api: apiOptions, debug } = {}) {
    const api = fakeApi(apiOptions);
    const doc = keyboardDoc();
    const win = { MediaRecorder: { isTypeSupported: t => t.startsWith('video/webm') }, navigator: { mediaDevices: { getDisplayMedia: () => {} } },
        document: doc, location: { search: '' } };
    const panel = new ExportPanel(doc);
    // "Start Recording" is answered by clicking the panel's own first button
    const ask = panel.ask.bind(panel);
    panel.ask = (text, buttons) => {
        const answer = ask(text, buttons);
        panel.runActions.querySelector('button').fire('click');
        return answer;
    };
    const flow = new ExportFlow({
        api, panel, win, Recorder: fakeRecorder(), wait: async () => {}, tailMs: 0, debug,
        hooks: {
            lessonInfo: () => ({ projectId: 7, title: 'Stress — Lesson 1', sceneCount: 3, canExport: true }),
            ensureProject: async () => 7,
            prepare: async () => ({ warnings: [] }),
            play: () => { setImmediate(() => flow.lessonFinished()); },
            stop: noop, setRecordingUi: noop, setManualRecording: noop, extras: () => [], openProjectUrl: id => `/?project_id=${id}`
        }
    });
    return { flow, api, panel, doc };
}

async function quietly(run) {
    const error = console.error;
    console.error = () => {};
    try { return await run(); } finally { console.error = error; }
}

test('a finished export: the recorded format in words, "Export complete" and the next step', async () => {
    const { flow, panel } = setupFlow();
    flow.open();
    await flow.startLessonExport();
    assert.equal(panel.steps.finalize.detail.textContent, '5 KB · WebM video');
    assert.equal(panel.readyTitle.textContent, '✓ Export complete');
    assert.equal(panel.readyActions.querySelector('button').textContent, '⬇ Download Video (WebM)');
    assert.equal(panel.startButton.textContent, '● Export again');
    assert.equal(formatName('video/webm;codecs=vp9,opus'), 'WebM');
    assert.equal(formatName('mp4'), 'MP4');
    assert.equal(formatName(''), '');
});

test('export errors are plain words that say the lesson is safe and what to do; the raw text only in debug mode', async () => {
    // a dropped upload: the recording is kept, Retry upload continues it
    const dropped = setupFlow({ api: { failUpload: httpError(0, 'the connection to the server was lost') } });
    await quietly(() => dropped.flow.startLessonExport());
    assert.equal(dropped.panel.messageEl.textContent, 'The upload stopped before the whole video reached the server. The connection to the server was lost. '
        + 'The recording is still in this tab and your lesson is saved: select Retry upload to continue from where it stopped.');
    assert.equal(dropped.panel.runActions.querySelectorAll('button')[0].textContent, 'Retry upload');

    // the server could not save the file: no decoder text for people
    const refused = httpError(400, 'The recording could not be read as a video: Invalid data found when processing input.');
    const broken = setupFlow({ api: { failComplete: refused } });
    await quietly(() => broken.flow.startLessonExport());
    assert.equal(broken.panel.messageEl.textContent, 'The server could not save the video. Your lesson changes are saved. Try the export again.');
    assert.equal(broken.api.jobs.job1.error_message, 'The server could not save the video. Your lesson changes are saved. Try the export again.');
    assert.deepEqual(broken.panel.runActions.querySelectorAll('button').map(b => b.textContent), ['Retry export', 'Close']);

    // an incomplete upload can be resumed
    const partial = setupFlow({ api: { failComplete: httpError(400, 'The upload is incomplete (10 of 5000 bytes received).') } });
    await quietly(() => partial.flow.startLessonExport());
    assert.match(partial.panel.messageEl.textContent, /^Part of the video did not reach the server\..*select Retry upload/);

    // an unexpected failure (here the export could not even be created): the plain reason, never the raw message
    const loggedOut = setupFlow();
    loggedOut.api.create = async () => { throw httpError(401, 'Your login has expired. Please log in again.'); };
    await quietly(() => loggedOut.flow.startLessonExport());
    assert.equal(loggedOut.panel.messageEl.textContent, 'Something went wrong during the export. Your login has expired. Please log in again. Your lesson is still saved; you can retry.');

    // debug mode: the same words, then the detail
    const debugged = setupFlow({ api: { failComplete: refused }, debug: true });
    await quietly(() => debugged.flow.startLessonExport());
    assert.equal(debugged.panel.messageEl.textContent, 'The server could not save the video. Your lesson changes are saved. Try the export again.'
        + '\n\nDetails: [400] The recording could not be read as a video: Invalid data found when processing input.');
    assert.equal(debugged.api.jobs.job1.error_message, 'The server could not save the video. Your lesson changes are saved. Try the export again.'); // stored plain
});

test('a download or preview that fails says so where it is seen, in plain words', async () => {
    const gone = httpError(410, 'The video file is missing on the server. Please export the lesson again.',
        { detail: 'The video file is missing on the server. Please export the lesson again.' });
    const { flow, panel } = setupFlow({ api: { failLink: gone } });
    flow.open();
    await flow.download(VIDEO);
    assert.equal(panel.noticeEl.hidden, false);
    assert.equal(panel.noticeEl.getAttribute('data-kind'), 'error');
    assert.equal(panel.noticeEl.textContent, 'The download could not start. The video file is missing on the server. Please export the lesson again. '
        + 'Your video is safe in your account; try Download again.');
    await flow.loadPreview(VIDEO);
    assert.match(panel.previewStatus.textContent, /^The preview could not be loaded\. The video file is missing on the server\./);
    assert.equal(panel.previewStatus.querySelector('button').textContent, 'Try again');

    const offline = setupFlow({ api: { failLink: httpError(0, 'The server could not be reached. Check the connection and try again.') } });
    await offline.flow.downloadOutput(VIDEO, 'mp4');
    assert.equal(offline.panel.noticeEl.textContent, 'The download could not start. The connection to the server was lost. Your video is safe in your account; try again.');
    const notReady = setupFlow();
    await notReady.flow.downloadOutput(VIDEO, 'vtt');
    assert.equal(notReady.panel.noticeEl.textContent, 'That file is not ready yet. Try again in a moment.');

    // plain reasons never carry a status code; a server sentence loses its technical part in brackets
    assert.equal(plainReason(httpError(500, 'The server had a problem (error 500).')), 'The server had a problem.');
    assert.equal(plainReason(httpError(413)), 'The video is larger than the server accepts.');
    assert.equal(plainReason(httpError(409, 'x', { detail: 'This export has already finished (COMPLETED).' })), 'This export has already finished.');
    assert.equal(plainReason(new Error('no status')), '');
    assert.ok(new ExportError('x') instanceof Error);
});
