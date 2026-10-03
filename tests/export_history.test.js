'use strict';
// Unit tests for the export ↔ lesson link in the video export client (export.js, Phase 20): the lesson version a lesson
// export records goes with the timeline at /complete (optional lessonLink hook), and the export history shows the open
// lesson's videos with a "This lesson" / "All videos" switch, saying which videos match the lesson as it is now
// (optional lessonFingerprint hook). The panel is checked over the small fake DOM the other UI tests use.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const { ExportFlow, ExportPanel } = require('../export.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

const tick = () => new Promise(resolve => setImmediate(resolve));
const blobOf = size => new Blob([new Uint8Array(size)]);
const httpError = (status, extra = {}) => Object.assign(new Error(`error ${status}`), { status }, extra);
const FP = 'a1'.repeat(32);
const FP_LATER = 'b2'.repeat(32);
const LINK = { project_id: 7, fingerprint: FP, revision: '2026-10-02T09:15:00.123456' };

// ---- the lesson link at /complete -------------------------------------------------------------------------------------

// Follows the server's rules closely enough for the flow (as in tests/export.test.js)
function fakeApi({ dropUploads = 0, exportsList = null } = {}) {
    const jobs = {};
    const calls = [];
    let seq = 0;
    const api = {
        base: () => '',
        async create(body) {
            const job = { id: `job${++seq}`, status: 'QUEUED', received: 0, ...body };
            jobs[job.id] = job;
            calls.push(['create', body]);
            return { ...job };
        },
        async update(id, body) {
            calls.push(['update', id, body]);
            if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(jobs[id].status)) throw httpError(409);
            Object.assign(jobs[id], body);
            return { ...jobs[id] };
        },
        async get(id) { return { ...jobs[id] }; },
        async received(id) { return jobs[id].received; },
        async sendChunk(id, offset, chunk, total, onProgress) {
            if (dropUploads > 0) {
                dropUploads--;
                throw httpError(0);
            }
            jobs[id].received = offset + chunk.size;
            onProgress(chunk.size);
            return jobs[id].received;
        },
        async complete(id, body) {
            calls.push(['complete', id, body]);
            Object.assign(jobs[id], { status: 'COMPLETED', file_size: body.size, has_audio: true });
            return { ...jobs[id] };
        },
        async list(projectId) {
            calls.push(['list', projectId]);
            const all = exportsList || Object.values(jobs);
            return { exports: all.filter(e => projectId === undefined || e.project_id === projectId) };
        },
        async link(id) { return { preview_url: `/preview/${id}`, download_url: `/download/${id}` }; }
    };
    return { api, jobs, calls };
}

function fakePanel() {
    const panel = { calls: [], steps: {}, actions: [], histories: [] };
    const record = name => (...args) => { panel.calls.push([name, ...args]); };
    Object.assign(panel, {
        open: record('open'), close: record('close'), hide: record('hide'), show: record('show'),
        setLesson: record('setLesson'), setBusy: record('setBusy'), beginRun: record('beginRun'), previewError: record('previewError'),
        renderHistory(list, handlers) { panel.histories.push({ list, handlers }); },
        historyError(text, onRetry) { panel.historyErrorText = text; panel.historyRetry = onRetry; },
        step(key, state) { panel.steps[key] = state; },
        message() {},
        failActive(text) { panel.error = text; },
        setActions(actions) { panel.actions = actions; },
        ask(text, buttons) { return Promise.resolve(buttons[0].value); },
        showReady(exp) { panel.ready = exp; },
        setPreview() {}
    });
    return panel;
}

function fakeRecorder() {
    return class {
        constructor() { this.constructor.instance = this; }
        async capture() {}
        start() {
            this.finished = new Promise(resolve => { this.resolve = resolve; });
            return Promise.resolve();
        }
        async stop() {
            this.resolve({ blob: blobOf(5000), mimeType: 'video/webm;codecs=vp9,opus', bytes: 5000, durationMs: 42000 });
            return this.finished;
        }
        abort() {}
    };
}

const fakeWindow = () => ({
    MediaRecorder: { isTypeSupported: type => type.startsWith('video/webm') },
    navigator: { mediaDevices: { getDisplayMedia: () => {} } },
    document: { title: 'Lesson' }
});

function setupFlow({ api: apiOptions, hooks = {}, panel = null, win = null, projectId = 7, debug } = {}) {
    const { api, jobs, calls } = fakeApi(apiOptions);
    const order = [];
    const info = { projectId, title: 'Stress — Lesson 1', sceneCount: 3, canExport: true };
    const flow = new ExportFlow({
        api, panel: panel || fakePanel(), win: win || fakeWindow(), Recorder: fakeRecorder(), wait: async () => {}, tailMs: 0, debug,
        hooks: {
            lessonInfo: () => info,
            ensureProject: async () => info.projectId || 7,
            prepare: async () => { order.push('prepare'); return { warnings: [] }; },
            play: () => { order.push('play'); setImmediate(() => flow.lessonFinished()); },
            stop: () => {},
            setRecordingUi: () => {},
            setManualRecording: () => {},
            extras: () => [],
            openProjectUrl: id => `/?project_id=${id}`,
            ...hooks
        }
    });
    return { flow, api, jobs, calls, panel: flow.panel, order, info };
}

const completion = calls => calls.find(c => c[0] === 'complete')[2];

test('a lesson export sends the lesson version it recorded with the timeline, read once after the lesson is prepared', async () => {
    let asked = 0;
    const { flow, calls, jobs, order } = setupFlow({ hooks: {
        lessonLink: async () => { asked++; order.push('lessonLink'); return { ...LINK, title: 'not sent' }; },
        timeline: () => ({ scenes: [{ t: 1, title: 'Stress' }], cues: [{ start: 1, end: 2, text: 'Stress is force over area.' }] })
    } });
    await flow.startLessonExport();
    assert.equal(jobs.job1.status, 'COMPLETED');
    assert.equal(asked, 1);
    assert.deepEqual(order, ['prepare', 'lessonLink', 'play']); // the lesson as it is about to be recorded
    assert.deepEqual(completion(calls).timeline, {
        scenes: [{ t: 1, title: 'Stress' }], cues: [{ start: 1, end: 2, text: 'Stress is force over area.' }], lesson: LINK
    });
});

test('without a timeline the link still goes; without the hook, or when it fails, nothing changes', async () => {
    const only = setupFlow({ hooks: { lessonLink: () => LINK } }); // a plain (not async) hook works too
    await only.flow.startLessonExport();
    assert.deepEqual(completion(only.calls).timeline, { lesson: LINK });

    const none = setupFlow();
    await none.flow.startLessonExport();
    assert.equal(completion(none.calls).timeline, null); // exactly as before Phase 20

    for (const lessonLink of [async () => null, () => 'nonsense', async () => { throw new Error('offline'); }, () => { throw new Error('boom'); }]) {
        const { flow, calls, jobs } = setupFlow({ hooks: { lessonLink } });
        const warn = console.warn;
        console.warn = () => {};
        try {
            await flow.startLessonExport();
        } finally {
            console.warn = warn;
        }
        assert.equal(jobs.job1.status, 'COMPLETED'); // the export never depends on it
        assert.equal(completion(calls).timeline, null);
    }
});

test('Retry upload keeps the same lesson link; a manual recording never claims one', async () => {
    let asked = 0;
    const { flow, calls, jobs, panel } = setupFlow({ api: { dropUploads: 4 }, hooks: { lessonLink: async () => { asked++; return LINK; } } });
    const error = console.error;
    console.error = () => {}; // the failed upload is reported on the console; expected here
    try {
        await flow.startLessonExport();
    } finally {
        console.error = error;
    }
    assert.equal(jobs.job1.status, 'UPLOADING');
    await panel.actions.find(a => a.label === 'Retry upload').onClick();
    assert.equal(jobs.job1.status, 'COMPLETED');
    assert.deepEqual(completion(calls).timeline, { lesson: LINK });
    assert.equal(asked, 1);

    let manualAsked = 0;
    const manual = setupFlow({ hooks: { lessonLink: async () => { manualAsked++; return LINK; } } });
    const running = manual.flow.toggleManualRecording();
    await tick();
    await tick();
    manual.flow.toggleManualRecording();
    await running;
    assert.equal(manual.jobs.job1.source, 'manual');
    assert.equal(manualAsked, 0);
    assert.equal(completion(manual.calls).timeline, null);
});

// ---- the history: this lesson / all videos ----------------------------------------------------------------------------

const VIDEOS = [
    { id: 'v1', project_id: 7, title: 'Stress', source: 'lesson', status: 'COMPLETED', lesson_fingerprint: FP },
    { id: 'v2', project_id: 7, title: 'Stress', source: 'lesson', status: 'COMPLETED', lesson_fingerprint: FP_LATER },
    { id: 'v3', project_id: 7, title: 'Stress', source: 'lesson', status: 'COMPLETED', lesson_fingerprint: null },
    { id: 'v4', project_id: 7, title: 'Stress', source: 'lesson', status: 'FAILED', lesson_fingerprint: null },
    { id: 'v5', project_id: 9, title: 'Strain', source: 'lesson', status: 'COMPLETED', lesson_fingerprint: FP },
    { id: 'v6', project_id: null, title: 'Screen recording', source: 'manual', status: 'COMPLETED' }
];
const listCalls = calls => calls.filter(c => c[0] === 'list').map(c => (c[1] === undefined ? 'all' : c[1]));
const shown = panel => panel.histories.at(-1);

test('the history opens on the lesson\'s own videos and switches to all videos and back', async () => {
    const { flow, calls, panel, info } = setupFlow({ api: { exportsList: VIDEOS } });
    await flow.open();
    assert.deepEqual(listCalls(calls), [7]); // ?project_id= of the open lesson
    assert.deepEqual(shown(panel).list.map(e => e.id), ['v1', 'v2', 'v3', 'v4']);
    assert.equal(shown(panel).handlers.scope.current, 'lesson');

    await shown(panel).handlers.scope.onChange('all');
    assert.deepEqual(listCalls(calls), [7, 'all']);
    assert.equal(shown(panel).list.length, VIDEOS.length);
    assert.equal(shown(panel).handlers.scope.current, 'all');
    await flow.refreshHistory(); // the choice holds for the same lesson (Refresh, a finished export)
    assert.deepEqual(listCalls(calls).at(-1), 'all');

    await shown(panel).handlers.scope.onChange('lesson');
    assert.deepEqual(listCalls(calls).at(-1), 7);

    // Another lesson starts again on its own videos
    await shown(panel).handlers.scope.onChange('all');
    info.projectId = 9;
    await flow.open();
    assert.deepEqual(listCalls(calls).at(-1), 9);
    assert.deepEqual(shown(panel).list.map(e => e.id), ['v5']);
    assert.equal(shown(panel).handlers.scope.current, 'lesson');

    // A lesson not saved yet: every video, and no switch
    info.projectId = null;
    await flow.open();
    assert.deepEqual(listCalls(calls).at(-1), 'all');
    assert.equal(shown(panel).handlers.scope, null);
});

test('an older history answer never replaces a newer one', async () => {
    const { flow, api, panel } = setupFlow({ api: { exportsList: VIDEOS } });
    const pending = [];
    api.list = projectId => new Promise(resolve => pending.push(() => resolve({ exports: VIDEOS.filter(e => projectId === undefined || e.project_id === projectId) })));
    const first = flow.refreshHistory(); // this lesson
    await tick();
    const second = flow.setHistoryScope('all');
    await tick();
    pending[1]();
    await second;
    pending[0]();
    await first;
    assert.equal(panel.histories.length, 1);
    assert.equal(shown(panel).handlers.scope.current, 'all');
    assert.equal(shown(panel).list.length, VIDEOS.length);

    api.list = async () => { throw new Error('the server could not be reached'); };
    await flow.refreshHistory();
    assert.equal(panel.historyErrorText, 'Your videos could not be loaded. They are safe in your account.');
    assert.doesNotMatch(panel.historyErrorText, /the server could not be reached/); // Phase 21: no raw error text
    api.list = async () => { throw Object.assign(new Error('offline'), { status: 0 }); };
    await flow.refreshHistory();
    assert.equal(panel.historyErrorText, 'Your videos could not be loaded. The connection to the server was lost. They are safe in your account.');
    // Try again loads the history again
    api.list = async () => ({ exports: VIDEOS });
    const before = panel.histories.length;
    await panel.historyRetry();
    assert.equal(panel.histories.length, before + 1);

    // With ?visualDebug (or the debug option) the raw detail follows the plain words
    const debugged = setupFlow({ api: { exportsList: VIDEOS }, debug: true });
    debugged.api.list = async () => { throw Object.assign(new Error('The server had a problem (error 503).'), { status: 503 }); };
    await debugged.flow.refreshHistory();
    assert.equal(debugged.panel.historyErrorText,
        'Your videos could not be loaded. The server had a problem. They are safe in your account. ([503] The server had a problem (error 503).)');
    const byUrl = setupFlow({ win: { ...fakeWindow(), location: { search: '?project_id=7&visualDebug=1' } } });
    assert.equal(byUrl.flow.isDebug(), true);
    assert.equal(setupFlow({ win: { ...fakeWindow(), location: { search: '?project_id=7' } } }).flow.isDebug(), false);
    assert.equal(setupFlow({ debug: () => true }).flow.isDebug(), true);
});

test('each finished video of the open lesson says whether it matches the lesson as it is now', async () => {
    let fingerprint = FP;
    const { flow, panel } = setupFlow({ api: { exportsList: VIDEOS }, hooks: { lessonFingerprint: async () => fingerprint } });
    await flow.setHistoryScope('all');
    const match = id => shown(panel).handlers.lessonMatch(VIDEOS.find(e => e.id === id));
    assert.deepEqual(match('v1'), { current: true, text: 'Matches the current lesson' });
    assert.deepEqual(match('v2'), { current: false, text: 'Made before your latest changes' });
    assert.equal(match('v3'), null); // recorded before the server kept the lesson version
    assert.equal(match('v4'), null); // not a finished video
    assert.equal(match('v5'), null); // another lesson's video, even with the same fingerprint
    assert.equal(match('v6'), null);

    // After an edit the same video is older than the lesson
    fingerprint = FP_LATER;
    await flow.refreshHistory();
    assert.equal(match('v1').text, 'Made before your latest changes');
    assert.equal(match('v2').text, 'Matches the current lesson');

    // Nothing is claimed when the page cannot say (no hook, an unusable value, a failure)
    for (const lessonFingerprint of [undefined, () => FP.toUpperCase(), () => 'abc', async () => { throw new Error('offline'); }, () => { throw new Error('boom'); }]) {
        const other = setupFlow({ api: { exportsList: VIDEOS }, hooks: lessonFingerprint ? { lessonFingerprint } : {} });
        await other.flow.refreshHistory();
        assert.equal(other.panel.histories.length, 1, 'the history still shows');
        assert.equal(other.panel.histories[0].handlers.lessonMatch(VIDEOS[0]), null);
    }
});

// ---- the panel (fake DOM) --------------------------------------------------------------------------------------------

function panelOnFakeDom() {
    const doc = fakeDoc();
    const panel = new ExportPanel(doc);
    panel.build();
    const scopeButtons = () => panel.historyScope.querySelectorAll('button');
    const items = () => panel.historyList.querySelectorAll('.export-history-item');
    return { doc, panel, scopeButtons, items };
}

const noop = () => {};
const baseHandlers = { onRefresh: noop, onPreview: noop, onDownload: noop, onDownloadOutput: noop, retryFor: () => null };

test('the panel shows the switch only for a saved lesson, with the current choice pressed', () => {
    const { panel, scopeButtons } = panelOnFakeDom();
    assert.equal(panel.historyScope.hidden, true); // before any history is shown
    const changes = [];
    panel.renderHistory([], { ...baseHandlers, scope: { current: 'lesson', onChange: scope => changes.push(scope) } });
    assert.equal(panel.historyScope.hidden, false);
    assert.equal(panel.historyHeading.textContent, 'Videos of this lesson');
    const [lesson, all] = scopeButtons();
    assert.deepEqual([lesson.textContent, all.textContent], ['This lesson', 'All videos']);
    assert.deepEqual([lesson.getAttribute('aria-pressed'), all.getAttribute('aria-pressed')], ['true', 'false']);
    assert.equal(panel.historyScope.getAttribute('role'), 'group');
    assert.equal(panel.historyList.textContent, 'No videos of this lesson yet. Choose Export video above to make one.');
    assert.equal(lesson.onclick, null); // already showing it
    all.onclick();
    assert.deepEqual(changes, ['all']);

    panel.renderHistory([], { ...baseHandlers, scope: { current: 'all', onChange: scope => changes.push(scope) } });
    assert.equal(panel.historyHeading.textContent, 'Your exported videos');
    assert.deepEqual([lesson.getAttribute('aria-pressed'), all.getAttribute('aria-pressed')], ['false', 'true']);
    assert.equal(panel.historyList.textContent, 'Your exported videos will appear here. Choose Export video above to make one.');
    lesson.onclick();
    assert.deepEqual(changes, ['all', 'lesson']);

    panel.renderHistory([], { ...baseHandlers }); // no saved lesson (and older callers without a scope)
    assert.equal(panel.historyScope.hidden, true);
    assert.equal(panel.historyHeading.textContent, 'Your exported videos');
    assert.equal(panel.historyList.textContent, 'Your exported videos will appear here. Choose Export video above to make one.');
    // No lesson open to export (Phase 21): the empty list says where videos come from
    panel.setLesson({ title: '', canExport: false }, noop);
    panel.renderHistory([], { ...baseHandlers });
    assert.equal(panel.historyList.textContent, 'Your exported videos will appear here. Open a lesson and choose Export video.');
});

test('the panel labels videos that match or predate the current lesson, and nothing else', () => {
    const { panel, items } = panelOnFakeDom();
    const labels = { v1: { current: true, text: 'Matches the current lesson' }, v2: { current: false, text: 'Made before your latest changes' } };
    panel.renderHistory(VIDEOS.slice(0, 4), { ...baseHandlers, lessonMatch: exp => labels[exp.id] || null });
    const rows = items();
    assert.equal(rows.length, 4);
    const tag = row => row.querySelector('.export-history-match');
    // Phase 21: an icon (hidden from screen readers) and the words
    const icon = row => tag(row).querySelector('[aria-hidden="true"]');
    assert.equal(tag(rows[0]).textContent, '✓ Matches the current lesson');
    assert.equal(icon(rows[0]).textContent, '✓ ');
    assert.equal(tag(rows[0]).getAttribute('data-match'), 'current');
    assert.equal(tag(rows[1]).textContent, '⚠ Made before your latest changes');
    assert.equal(icon(rows[1]).textContent, '⚠ ');
    assert.equal(tag(rows[1]).getAttribute('data-match'), 'older');
    assert.equal(tag(rows[2]), null);
    assert.equal(tag(rows[3]), null);
    // Without the handler (older callers) the rows are as before
    panel.renderHistory(VIDEOS.slice(0, 2), { ...baseHandlers });
    assert.equal(panel.historyList.querySelectorAll('.export-history-match').length, 0);
    assert.equal(items().length, 2);
});

test('the flow drives the real panel: the switch lists this lesson, then all videos', async () => {
    const { panel, scopeButtons, items } = panelOnFakeDom();
    const { flow, calls } = setupFlow({ panel, api: { exportsList: VIDEOS }, hooks: { lessonFingerprint: () => FP } });
    await flow.open();
    assert.equal(items().length, 4);
    assert.equal(panel.historyHeading.textContent, 'Videos of this lesson');
    assert.deepEqual(panel.historyList.querySelectorAll('.export-history-match').map(n => n.textContent),
        ['✓ Matches the current lesson', '⚠ Made before your latest changes']);
    await scopeButtons()[1].onclick();
    assert.deepEqual(listCalls(calls), [7, 'all']);
    assert.equal(items().length, VIDEOS.length);
    assert.equal(panel.historyHeading.textContent, 'Your exported videos');
    assert.equal(scopeButtons()[1].getAttribute('aria-pressed'), 'true');
    // Only the open lesson's videos are labelled, also among all videos
    assert.equal(panel.historyList.querySelectorAll('.export-history-match').length, 2);
});
