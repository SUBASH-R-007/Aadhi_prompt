'use strict';
// Unit tests for the video export client (export.js): resumable upload, the tab recorder,
// and the export flow. Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const {
    ExportError, ExportFlow, FRAME_RATE, LessonRecorder, VIDEO_BITS_PER_SECOND, bitrateFor, checkSupport, pickMimeType, uploadResumable, formatDuration, formatBytes
} = require('../export.js');

const tick = () => new Promise(resolve => setImmediate(resolve));
const blobOf = size => new Blob([new Uint8Array(size)]);
const httpError = (status, extra = {}) => Object.assign(new Error(`error ${status}`), { status }, extra);

// ---- uploadResumable ------------------------------------------------------------

// An in-memory server that accepts appends at the current offset
function fakeServer({ dropAt = [] } = {}) {
    const server = { received: 0, sends: [] };
    server.receivedFn = async () => server.received;
    server.send = async (offset, chunk, onProgress) => {
        server.sends.push([offset, chunk.size]);
        if (offset !== server.received) throw httpError(409, { received: server.received });
        if (dropAt.length && dropAt[0] === server.sends.length) {
            dropAt.shift();
            server.received += Math.floor(chunk.size / 2); // half arrived before the connection dropped
            throw httpError(0);
        }
        onProgress(chunk.size);
        server.received += chunk.size;
        return server.received;
    };
    return server;
}

test('upload sends the file in chunks and reports progress up to the full size', async () => {
    const server = fakeServer();
    const progress = [];
    const blob = blobOf(25);
    await uploadResumable({
        size: 25, slice: (a, b) => blob.slice(a, b), received: server.receivedFn, send: server.send,
        chunkSize: 10, onProgress: sent => progress.push(sent), wait: async () => {}
    });
    assert.deepEqual(server.sends, [[0, 10], [10, 10], [20, 5]]);
    assert.equal(server.received, 25);
    assert.equal(progress.at(-1), 25);
    assert.deepEqual(progress, [...progress].sort((a, b) => a - b)); // never goes backwards
});

test('upload resumes from what the server already has', async () => {
    const server = fakeServer();
    server.received = 12; // an earlier attempt got this far
    const blob = blobOf(30);
    await uploadResumable({ size: 30, slice: (a, b) => blob.slice(a, b), received: server.receivedFn, send: server.send, chunkSize: 10, wait: async () => {} });
    assert.deepEqual(server.sends[0], [12, 10]);
    assert.equal(server.received, 30);
});

test('a dropped connection is retried from the server\'s real offset', async () => {
    const server = fakeServer({ dropAt: [2] });
    const waits = [];
    const blob = blobOf(30);
    await uploadResumable({
        size: 30, slice: (a, b) => blob.slice(a, b), received: server.receivedFn, send: server.send,
        chunkSize: 10, wait: async ms => waits.push(ms)
    });
    assert.equal(server.received, 30);
    assert.deepEqual(waits, [1000]);
    assert.deepEqual(server.sends[2], [15, 10]); // resent from the 5 bytes that did arrive
});

test('upload gives up after repeated failures, and at once when the server refuses', async () => {
    const blob = blobOf(10);
    const alwaysDown = async () => { throw httpError(0); };
    await assert.rejects(uploadResumable({ size: 10, slice: (a, b) => blob.slice(a, b), received: async () => 0, send: alwaysDown, attempts: 3, wait: async () => {} }));
    let calls = 0;
    const loggedOut = async () => { calls++; throw httpError(401); };
    await assert.rejects(uploadResumable({ size: 10, slice: (a, b) => blob.slice(a, b), received: async () => 0, send: loggedOut, wait: async () => {} }),
        err => err.status === 401);
    assert.equal(calls, 1);
});

// ---- LessonRecorder -------------------------------------------------------------

function fakeTrack(kind, settings = {}) {
    const listeners = [];
    return {
        kind,
        stopped: false,
        stop() { this.stopped = true; },
        getSettings: () => settings,
        addEventListener: (type, fn) => listeners.push(fn),
        end() { listeners.forEach(fn => fn()); }
    };
}

function fakeStream({ audio = true, surface = 'browser' } = {}) {
    const video = fakeTrack('video', { displaySurface: surface, width: 1920, height: 1080 });
    const tracks = audio ? [video, fakeTrack('audio')] : [video];
    return {
        video,
        getVideoTracks: () => [video],
        getAudioTracks: () => tracks.filter(t => t.kind === 'audio'),
        getTracks: () => tracks
    };
}

class FakeMediaRecorder {
    static isTypeSupported(type) { return type.startsWith('video/webm'); }
    constructor(stream, options) {
        this.stream = stream;
        this.options = options;
        this.mimeType = options.mimeType;
        this.state = 'inactive';
        FakeMediaRecorder.last = this;
    }
    start(timeslice) {
        this.timeslice = timeslice;
        this.state = 'recording';
        queueMicrotask(() => this.onstart());
    }
    emit(size) { this.ondataavailable({ data: blobOf(size) }); }
    stop() {
        if (this.state === 'inactive') return;
        this.state = 'inactive';
        this.emit(3); // the final flush arrives just before 'stop'
        queueMicrotask(() => this.onstop());
    }
}

function fakeWindow(getDisplayMedia) {
    return { MediaRecorder: FakeMediaRecorder, navigator: { mediaDevices: { getDisplayMedia } }, document: { title: 'Lesson' } };
}

test('picks WebM and reports missing browser features', () => {
    assert.equal(pickMimeType(FakeMediaRecorder), 'video/webm;codecs=vp9,opus');
    assert.equal(pickMimeType({ isTypeSupported: t => t === 'video/mp4' }), 'video/mp4');
    assert.deepEqual(checkSupport(fakeWindow(() => {})), { ok: true, mimeType: 'video/webm;codecs=vp9,opus', missing: [] });
    const old = checkSupport({ navigator: {} });
    assert.equal(old.ok, false);
    assert.deepEqual(old.missing, ['video recording', 'tab capture']);
});

test('capture: cancelling the share dialog is a cancellation, not a crash', async () => {
    const recorder = new LessonRecorder({ win: fakeWindow(async () => { throw Object.assign(new Error('denied'), { name: 'NotAllowedError' }); }) });
    await assert.rejects(recorder.capture(), err => err instanceof ExportError && err.status === 'CANCELLED' && /cancelled/.test(err.message));
});

test('capture: a stream without tab audio or from another window is refused and released', async () => {
    const silent = fakeStream({ audio: false });
    await assert.rejects(new LessonRecorder({ win: fakeWindow(async () => silent) }).capture(),
        err => err.status === 'CANCELLED' && /tab audio/.test(err.message));
    assert.ok(silent.video.stopped);

    const screen = fakeStream({ surface: 'monitor' });
    await assert.rejects(new LessonRecorder({ win: fakeWindow(async () => screen) }).capture(), /tab itself/);
    assert.ok(screen.getTracks().every(t => t.stopped));
});

test('capture: the wrong-surface message names the tab by its real title (Phase 21)', async () => {
    const win = fakeWindow(async () => fakeStream({ surface: 'window' }));
    win.document.title = 'Aadhi — Educational Video Studio';
    await assert.rejects(new LessonRecorder({ win }).capture(), /share the "Aadhi — Educational Video Studio" tab itself/);
    const untitled = fakeWindow(async () => fakeStream({ surface: 'monitor' }));
    untitled.document.title = '  ';
    // a page without a title: index.html's own title, never the old product name
    await assert.rejects(new LessonRecorder({ win: untitled }).capture(),
        err => /"Aadhi — Educational Video Studio" tab itself/.test(err.message) && !/EduEngine/.test(err.message));
});

test('recording collects every chunk and builds the file only after the recorder stops', async () => {
    const stream = fakeStream();
    let clock = 1000;
    const recorder = new LessonRecorder({ win: fakeWindow(async () => stream), mimeType: 'video/webm;codecs=vp9,opus', now: () => clock });
    await recorder.capture();
    await recorder.start();
    const rec = FakeMediaRecorder.last;
    assert.equal(rec.timeslice, 1000);
    rec.emit(100);
    rec.emit(50);
    let settled = false;
    recorder.finished.then(() => { settled = true; });
    await tick();
    assert.equal(settled, false); // no file while still recording
    clock = 61000;
    const result = await recorder.stop();
    assert.equal(result.bytes, 153); // including the final flush
    assert.equal(result.blob.size, 153);
    assert.equal(result.mimeType, 'video/webm;codecs=vp9,opus');
    assert.equal(result.durationMs, 60000);
    assert.ok(stream.getTracks().every(t => t.stopped));
});

test('recording that ends because sharing stopped is reported as interrupted', async () => {
    const stream = fakeStream();
    const recorder = new LessonRecorder({ win: fakeWindow(async () => stream), mimeType: 'video/webm' });
    await recorder.capture();
    await recorder.start();
    stream.video.end(); // the browser's "Stop sharing" button
    await assert.rejects(recorder.finished, err => err.status === 'CANCELLED' && /interrupted/.test(err.message));
});

test('a recorder error fails the recording', async () => {
    const stream = fakeStream();
    const recorder = new LessonRecorder({ win: fakeWindow(async () => stream), mimeType: 'video/webm' });
    await recorder.capture();
    await recorder.start();
    const rec = FakeMediaRecorder.last;
    rec.onerror({ error: { name: 'UnknownError' } });
    rec.stop();
    await assert.rejects(recorder.finished, err => err.status === 'FAILED' && /error/.test(err.message));
});

// ---- ExportFlow ----------------------------------------------------------------

// Mirrors the server's rules closely enough to follow the flow
function fakeApi({ dropUploads = 0 } = {}) {
    const jobs = {};
    const calls = [];
    let seq = 0;
    const final = s => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(s);
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
            if (final(jobs[id].status)) throw httpError(409);
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
        async list() { return { exports: Object.values(jobs) }; },
        async link(id) { return { preview_url: `/preview/${id}`, download_url: `/download/${id}` }; }
    };
    return { api, jobs, calls };
}

// Records what the flow shows; ask() answers from a script (first button by default)
function fakePanel(answers = []) {
    const panel = { calls: [], steps: {}, actions: [], messages: [], ready: null, preview: null };
    const record = name => (...args) => { panel.calls.push([name, ...args]); };
    Object.assign(panel, {
        open: record('open'), close: record('close'), hide: record('hide'), show: record('show'),
        setLesson: record('setLesson'), setBusy: record('setBusy'), beginRun: record('beginRun'),
        renderHistory: record('renderHistory'), historyError: record('historyError'), previewError: record('previewError'),
        step(key, state, detail) { panel.steps[key] = state; panel.calls.push(['step', key, state, detail]); },
        message(text, kind) { panel.messages.push([text, kind]); },
        failActive(text) { panel.error = text; },
        setActions(actions) { panel.actions = actions; },
        ask(text, buttons) { panel.calls.push(['ask', text]); return Promise.resolve(answers.length ? answers.shift() : buttons[0].value); },
        showReady(exp) { panel.ready = exp; },
        setPreview(url) { panel.preview = url; }
    });
    return panel;
}

// Recorder stand-in: capture/record outcomes are scripted per test
function fakeRecorder({ captureError = null, interrupt = false, size = 5000 } = {}) {
    return class {
        constructor() { this.constructor.instance = this; }
        async capture() { if (captureError) throw captureError; }
        start() {
            this.finished = new Promise((resolve, reject) => { this.resolve = resolve; this.reject = reject; });
            this.finished.catch(() => {});
            if (interrupt) setImmediate(() => this.reject(new ExportError('Recording was interrupted because screen sharing stopped.', { status: 'CANCELLED' })));
            return Promise.resolve();
        }
        async stop(tailMs) {
            this.tailMs = tailMs;
            this.resolve({ blob: blobOf(size), mimeType: 'video/webm;codecs=vp9,opus', bytes: size, durationMs: 42000 });
            return this.finished;
        }
        abort() { this.aborted = true; }
    };
}

function setupFlow({ answers, recorder, api: apiOptions, warnings = [], win } = {}) {
    const { api, jobs, calls } = fakeApi(apiOptions);
    const panel = fakePanel(answers);
    const events = [];
    const w = win || fakeWindow(() => {});
    const flow = new ExportFlow({
        api, panel, win: w, Recorder: recorder || fakeRecorder(), wait: async () => {}, tailMs: 1500,
        hooks: {
            lessonInfo: () => ({ projectId: 7, title: 'Stress — Lesson 1', sceneCount: 3, canExport: true }),
            ensureProject: async () => 7,
            prepare: async report => { report(1, 2, 'Narration'); report(2, 2, 'Images'); return { warnings }; },
            play: () => {
                events.push('play');
                // The lesson runs its scenes, then reports the end
                setImmediate(() => {
                    flow.sceneStarted(0, 3);
                    events.push(`title:${w.document.title}`);
                    flow.sceneStarted(2, 3);
                    flow.lessonFinished();
                });
            },
            stop: () => events.push('stop'),
            setRecordingUi: on => events.push(`ui:${on}`),
            setManualRecording: on => events.push(`manual:${on}`),
            extras: () => [{ label: 'Download Chapters', run: () => {} }],
            openProjectUrl: id => `/?project_id=${id}`
        }
    });
    return { flow, api, jobs, calls, panel, events, win: w };
}

const statusUpdates = calls => calls.filter(c => c[0] === 'update' && c[2].status).map(c => c[2].status);

test('lesson export: prepare, record, upload and save, then show the video', async () => {
    const { flow, jobs, calls, panel, events, win } = setupFlow();
    await flow.startLessonExport();

    const job = jobs.job1;
    assert.equal(job.status, 'COMPLETED');
    assert.equal(job.project_id, 7);
    assert.equal(job.source, 'lesson');
    assert.deepEqual(statusUpdates(calls), ['PREPARING', 'RECORDING', 'UPLOADING']);
    assert.equal(job.received, 5000); // the whole recording reached the server
    const complete = calls.find(c => c[0] === 'complete');
    assert.deepEqual(complete[2], { size: 5000, mime_type: 'video/webm;codecs=vp9,opus', duration_seconds: 42, timeline: null });

    assert.deepEqual(events.filter(e => !e.startsWith('title')), ['ui:true', 'play', 'ui:false', 'stop']);
    assert.equal(events.find(e => e.startsWith('title')), 'title:● Recording 1/3 · Lesson');
    assert.equal(win.document.title, 'Lesson'); // restored
    assert.equal(flow.Recorder.instance.tailMs, 1500); // recording runs on briefly after the last scene
    assert.ok(panel.calls.some(c => c[0] === 'hide')); // the panel is never in the video
    assert.deepEqual(['prepare', 'capture', 'record', 'finalize', 'upload', 'save'].map(k => panel.steps[k]), ['done', 'done', 'done', 'done', 'done', 'done']);
    assert.equal(panel.ready.id, 'job1');
    assert.equal(panel.preview, '/preview/job1');
    assert.equal(flow.busy, false);
    // the share-dialog instructions quote the tab's title, as the browser lists it
    const ask = panel.calls.find(c => c[0] === 'ask' && /share dialog/.test(c[1]));
    assert.match(ask[1], /choose this tab \("Lesson"\)/);
    assert.doesNotMatch(ask[1], /EduEngine/);
});

test('cancelling the share dialog marks the export cancelled and offers a retry', async () => {
    const cancelled = new ExportError('Screen sharing was cancelled, so nothing was recorded.', { status: 'CANCELLED' });
    const { flow, jobs, panel } = setupFlow({ recorder: fakeRecorder({ captureError: cancelled }) });
    await flow.startLessonExport();
    assert.equal(jobs.job1.status, 'CANCELLED');
    assert.match(jobs.job1.error_message, /cancelled/);
    assert.match(panel.error, /cancelled/);
    assert.deepEqual(panel.actions.map(a => a.label), ['Retry export', 'Close']);

    // Retry is a new attempt linked to the failed one; the old record stays
    flow.Recorder = fakeRecorder();
    await panel.actions[0].onClick();
    assert.equal(jobs.job2.retry_of_id, 'job1');
    assert.equal(jobs.job2.status, 'COMPLETED');
    assert.equal(jobs.job1.status, 'CANCELLED');
});

test('an interrupted recording is cancelled and the lesson is stopped', async () => {
    const { flow, jobs, events, panel } = setupFlow({ recorder: fakeRecorder({ interrupt: true }) });
    flow.hooks.play = () => events.push('play'); // the lesson never finishes on its own
    await flow.startLessonExport();
    assert.equal(jobs.job1.status, 'CANCELLED');
    assert.match(panel.error, /interrupted/);
    assert.ok(flow.Recorder.instance.aborted);
    assert.ok(events.includes('stop'));
    assert.ok(events.includes('ui:false'));
});

test('a failed upload keeps the recording and resumes on Retry upload', async () => {
    const { flow, jobs, panel } = setupFlow({ api: { dropUploads: 4 } });
    await flow.startLessonExport();
    assert.equal(jobs.job1.status, 'UPLOADING'); // not failed: the server keeps what arrived
    assert.match(panel.error, /Retry upload/);
    assert.deepEqual(panel.actions.map(a => a.label), ['Retry upload', 'Retry export', 'Close']);

    await panel.actions[0].onClick();
    assert.equal(jobs.job1.status, 'COMPLETED');
    assert.equal(jobs.job1.received, 5000);
    assert.equal(Object.keys(jobs).length, 1); // no second recording needed
});

test('an unsupported browser gets a clear message and no export is created', async () => {
    const { flow, jobs, panel } = setupFlow({ win: { navigator: {}, document: { title: 'Lesson' } } });
    await flow.startLessonExport();
    assert.equal(Object.keys(jobs).length, 0);
    assert.match(panel.error, /Chrome or Edge/);
});

test('assets that could not be prepared are shown before recording, and can cancel it', async () => {
    const { flow, jobs, panel } = setupFlow({ warnings: ['Scene 2: the AI video could not be generated.'], answers: [false] });
    await flow.startLessonExport();
    assert.ok(panel.calls.some(c => c[0] === 'ask' && /Scene 2/.test(c[1])));
    assert.equal(jobs.job1.status, 'CANCELLED');
});

test('manual recording: the record button starts and stops it, and it is saved too', async () => {
    const { flow, jobs, events } = setupFlow();
    const running = flow.toggleManualRecording();
    await tick();
    await tick();
    assert.ok(events.includes('manual:true'));
    flow.toggleManualRecording(); // second press stops
    await running;
    assert.equal(jobs.job1.source, 'manual');
    assert.equal(jobs.job1.status, 'COMPLETED');
    assert.equal(flow.Recorder.instance.tailMs, 0);
    assert.deepEqual(events, ['manual:true', 'manual:false']); // the lesson itself is not driven
});

test('history offers a retry only for the lesson that is open', () => {
    const { flow } = setupFlow();
    assert.equal(flow.retryFor({ source: 'lesson', project_id: 7, status: 'FAILED' }).label, 'Retry');
    assert.equal(flow.retryFor({ source: 'lesson', project_id: 9, status: 'FAILED' }).label, 'Open lesson');
    assert.equal(flow.retryFor({ source: 'manual', project_id: null, status: 'FAILED' }), null);
});

test('formats durations and sizes for people', () => {
    assert.equal(formatDuration(762), '12:42');
    assert.equal(formatDuration(3725), '1:02:05');
    assert.equal(formatBytes(1536), '2 KB');
    assert.equal(formatBytes(5 * 1024 * 1024 * 1024), '5.0 GB');
});

// ---- Phase 7: recording configuration, order, timeline, outputs --------------------------------------

test('the recorder uses the configured frame rate and bitrates and measures the recording', async () => {
    const stream = fakeStream();
    let asked = null;
    let clock = 5000;
    const recorder = new LessonRecorder({ win: fakeWindow(async constraints => { asked = constraints; return stream; }), mimeType: 'video/webm;codecs=vp9,opus',
        frameRate: 30, videoBitsPerSecond: 8000000, audioBitsPerSecond: 128000, now: () => clock });
    await recorder.capture();
    assert.deepEqual(asked.video.frameRate, { ideal: 30, max: 30 });
    await recorder.start();
    const rec = FakeMediaRecorder.last;
    assert.deepEqual(rec.options, { mimeType: 'video/webm;codecs=vp9,opus', videoBitsPerSecond: 8000000, audioBitsPerSecond: 128000 });
    rec.emit(100);
    clock = 65000;
    await recorder.stop();
    const m = recorder.metrics;
    assert.equal(m.requestedFps, 30);
    assert.deepEqual(m.track, { width: 1920, height: 1080, frameRate: null, audioTracks: 1 });
    assert.equal(m.chunks, 2); // the one emitted and the final flush
    assert.equal(m.bytes, 103);
    assert.equal(m.durationMs, 60000);
    assert.equal(m.startLatencyMs, 0);
    assert.equal(recorder.chunks.length, 0); // the Blob holds the recording; no second copy is kept
    // The defaults (chosen from the Phase 7 measurements) apply unless a run sets its own
    const plainStream = fakeStream();
    let plainAsked = null;
    const plain = new LessonRecorder({ win: fakeWindow(async c => { plainAsked = c; return plainStream; }), mimeType: 'video/webm' });
    assert.equal(plain.frameRate, 30);
    await plain.capture();
    assert.deepEqual(plainAsked.video.frameRate, { ideal: 30, max: 30 });
    assert.equal(plainAsked.video.width, undefined); // no size asked: a smaller tab is never upscaled
    await plain.start();
    assert.equal(FakeMediaRecorder.last.options.videoBitsPerSecond, 12000000); // the fake stream is 1080p
    assert.equal(bitrateFor({ width: 1280, height: 720 }), 8000000);
    assert.equal(bitrateFor({ width: 1920, height: 1080 }), 12000000);
    assert.equal(bitrateFor(null), VIDEO_BITS_PER_SECOND);
    assert.equal(FRAME_RATE, 30);
});

test('a capture larger than 1080p is scaled down, a smaller one is left alone', async () => {
    const huge = fakeStream();
    let applied = null;
    let size = { displaySurface: 'browser', width: 2560, height: 1440 };
    huge.video.getSettings = () => size;
    huge.video.applyConstraints = async c => { applied = c; size = { displaySurface: 'browser', width: 1920, height: 1080 }; };
    const big = new LessonRecorder({ win: fakeWindow(async () => huge), mimeType: 'video/webm' });
    await big.capture();
    assert.deepEqual([applied.width, applied.height], [{ max: 1920 }, { max: 1080 }]);
    assert.deepEqual([big.metrics.track.width, big.metrics.track.height], [1920, 1080]);

    const small = fakeStream();
    let touched = false;
    small.video.getSettings = () => ({ displaySurface: 'browser', width: 1280, height: 720 });
    small.video.applyConstraints = async () => { touched = true; };
    const normal = new LessonRecorder({ win: fakeWindow(async () => small), mimeType: 'video/webm' });
    await normal.capture();
    assert.equal(touched, false);
    await normal.start();
    assert.equal(FakeMediaRecorder.last.options.videoBitsPerSecond, 8000000);
});

test('recording starts only once the lesson is confirmed playing', async () => {
    const order = [];
    let confirm;
    const Recorder = class extends fakeRecorder() {
        start() { order.push('recorder.start'); return super.start(); }
    };
    const { flow } = setupFlow({ recorder: Recorder });
    const originalPlay = flow.hooks.play;
    flow.hooks.play = () => {
        order.push('play');
        return new Promise(resolve => { confirm = () => { order.push('playing'); resolve(); originalPlay(); }; });
    };
    flow.hooks.recordingStarted = () => order.push('recordingStarted');
    const run = flow.startLessonExport();
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.deepEqual(order, ['play']); // the recorder waits for the lesson
    confirm();
    await run;
    assert.deepEqual(order.slice(0, 4), ['play', 'playing', 'recorder.start', 'recordingStarted']);
});

test('the recording\'s timeline goes with the upload, taken when recording stops', async () => {
    const { flow, calls } = setupFlow();
    const timeline = { scenes: [{ t: 14.6, title: 'Stress' }], cues: [{ start: 15, end: 17, text: 'Stress is force over area.' }] };
    let asked = 0;
    flow.hooks.timeline = () => { asked++; return timeline; };
    await flow.startLessonExport();
    assert.equal(asked, 1);
    assert.deepEqual(calls.find(c => c[0] === 'complete')[2].timeline, timeline);
});

test('an empty recording fails clearly instead of saving an empty video', async () => {
    const { flow, jobs, panel } = setupFlow({ recorder: fakeRecorder({ size: 0 }) });
    await flow.startLessonExport();
    assert.equal(jobs.job1.status, 'FAILED');
    assert.match(panel.error, /came out empty/);
    assert.ok(!('received' in jobs.job1) || jobs.job1.received === 0);
});

test('MP4, subtitles and chapters are offered as they become ready', async () => {
    const { flow, api, panel } = setupFlow();
    const exp = { id: 'e1', format: 'webm', outputs: { mp4: { status: 'processing' }, vtt: { status: 'ready' }, chapters: { status: 'ready' } } };
    assert.deepEqual(flow.readyActions(exp, false).map(a => a.label), ['⬇ Download Video (WebM)', 'Subtitles (.vtt)', 'Chapters (.txt)']);
    assert.match(flow.outputNote(exp).text, /Making an MP4 copy/);
    const done = { ...exp, outputs: { ...exp.outputs, mp4: { status: 'ready' } } };
    assert.deepEqual(flow.readyActions(done, false).map(a => a.label), ['⬇ Download Video (WebM)', '⬇ MP4 copy', 'Subtitles (.vtt)', 'Chapters (.txt)']);
    assert.equal(flow.outputNote(done), null);
    const failed = { ...exp, outputs: { mp4: { status: 'failed', error: 'The MP4 copy could not be made: boom. The WebM video is complete.' } } };
    assert.equal(flow.outputNote(failed).action.label, 'Try the MP4 again');
    // Phase 21: plain words; what ffmpeg said only with ?visualDebug
    assert.equal(flow.outputNote(failed).text, 'The MP4 copy could not be made. The WebM video above is complete and ready to download.');
    const unavailable = { ...exp, outputs: { mp4: { status: 'unavailable', error: 'no H.264 encoder' } } };
    assert.match(flow.outputNote(unavailable).text, /^This server cannot make MP4 copies\. The WebM video above is complete/);
    assert.doesNotMatch(flow.outputNote(unavailable).text, /H\.264/);
    flow.debug = true;
    assert.match(flow.outputNote(unavailable).text, /\(no H\.264 encoder\)$/);
    assert.match(flow.outputNote(failed).text, /boom/);
    flow.debug = false;
    // Older exports without outputs: just the video
    assert.deepEqual(flow.readyActions({ id: 'old' }, false).map(a => a.label), ['⬇ Download Video']);

    // While the MP4 is being made, the panel follows it until it is ready
    let polls = 0;
    api.get = async () => (++polls < 2 ? exp : done);
    const notes = [];
    panel.setNote = note => notes.push(note);
    const shown = [];
    panel.setActions = (actions, container) => shown.push(actions.map(a => a.label));
    panel.readyActions = 'ready-container';
    flow.watchOutputs(exp, false);
    for (let i = 0; i < 10 && !notes.length; i++) await tick();
    assert.equal(polls, 2);
    assert.deepEqual(shown.pop(), ['⬇ Download Video (WebM)', '⬇ MP4 copy', 'Subtitles (.vtt)', 'Chapters (.txt)']);
    assert.equal(notes.pop(), null);
});
