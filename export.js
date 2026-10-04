/*
 * Lesson video export.
 *
 * Records the lesson tab (getDisplayMedia + MediaRecorder), uploads the recording to the
 * server in resumable chunks, and shows progress, the finished video and export history.
 * The server side is exports.py; index.html supplies the lesson hooks (prepare, play, stop).
 *
 * Recording order (Phase 7): share the tab, start the lesson, wait until it is visibly playing,
 * then start the recorder, so a video never opens on a stale frame of the page. With the upload
 * goes the recording's timeline (scene starts, narration lines as shown) for the server's
 * subtitles, chapters and MP4 copy.
 *
 * Phase 22: "Render video (recommended)" asks the server to render the saved lesson frame by frame
 * (POST /api/exports/render, renders.py): a 1080p MP4 that does not depend on this tab, so it keeps
 * going if the panel or the page is closed; the panel follows it through the export's `render`
 * progress and picks it up again when it is opened later. "Record the screen" is the tab recording
 * above, kept as it was. Scenes without a visual are never recorded or rendered silently: the
 * teacher chooses to go on without those visuals, or cancels.
 *
 * Loaded as a classic <script> (window.AadhiExport) and as a CommonJS module by the Node
 * unit tests in tests/export.test.js.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.AadhiExport = api;
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // WebM first (what this app has always recorded); MP4 only for browsers without WebM recording
    const MIME_CANDIDATES = [
        'video/webm;codecs=vp9,opus',
        'video/webm;codecs=vp8,opus',
        'video/webm;codecs=h264,opus',
        'video/webm',
        'video/mp4'
    ];
    // Recording settings chosen from measurements (Phase 7, see all-phases-details.md): at 60 fps Chrome
    // delivered only ~40 fps and Aadhi's clips stalled 3-4 times per lesson (their decoder starved while
    // the tab was captured and encoded); at 30 fps every frame was delivered with no stalls, and 8 Mbit/s
    // kept lesson text as sharp as 15 Mbit/s at half the file size. A 1080p tab records smoothly only with
    // the page's glass blur off during recording (index.html, body[data-recording]); scaling the capture
    // down did not help, so the cap only stops HiDPI/4K tabs from producing oversized files.
    const FRAME_RATE = 30;
    const MAX_CAPTURE = { width: 1920, height: 1080 }; // larger captures (HiDPI screens) are scaled down to 1080p, smaller never up
    const VIDEO_BITS_PER_SECOND = 8000000;  // up to 720p
    const VIDEO_BITS_PER_SECOND_HD = 12000000; // above 720p (up to the 1080p cap)
    const TIMESLICE_MS = 1000;  // flush recorded data every second instead of holding it all until stop
    const TAIL_MS = 1500;       // keep recording briefly after the last scene so its final words and frame are kept
    const CHUNK_BYTES = 8 * 1024 * 1024;
    const REPORT_EVERY_MS = 2000;
    const RENDER_POLL_MS = 2000;     // how often a server render's progress is read
    const RENDER_POLL_FAILURES = 30; // reads in a row that may fail (the server restarting) before the panel gives up following
    const ACTIVE_STATUSES = ['QUEUED', 'PREPARING', 'RECORDING', 'UPLOADING', 'PROCESSING'];
    const RENDER_STEPS = [
        ['prepare', 'Preparing the lesson'],
        ['render', 'Rendering the video'],
        ['mix', 'Mixing the sound'],
        ['save', 'Saving the video']
    ];
    const RENDER_PHASE_STEPS = { preparing: 'prepare', capturing: 'render', mixing: 'mix', finishing: 'save' };
    const RENDER_SETTINGS_KEYS = ['aadhi.cinematic', 'aadhi.presenter']; // the teacher's playback settings a render page restores
    const AI_VISUALS_MODES = ['images', 'all', 'off'];

    class ExportError extends Error {
        // status: the export status to record (FAILED or CANCELLED).
        // retryUpload: the recording is still in memory, so the upload can resume.
        constructor(message, { status = 'FAILED', detail = null, retryUpload = false } = {}) {
            super(message);
            this.status = status;
            this.detail = detail;
            this.retryUpload = retryUpload;
        }
    }

    function pickMimeType(Recorder) {
        if (!Recorder || typeof Recorder.isTypeSupported !== 'function') return null;
        return MIME_CANDIDATES.find(type => Recorder.isTypeSupported(type)) || null;
    }

    function checkSupport(win) {
        const missing = [];
        if (!win.MediaRecorder) missing.push('video recording');
        if (!win.navigator.mediaDevices || typeof win.navigator.mediaDevices.getDisplayMedia !== 'function') missing.push('tab capture');
        const mimeType = pickMimeType(win.MediaRecorder);
        if (win.MediaRecorder && !mimeType) missing.push('a WebM or MP4 recording format');
        return { ok: missing.length === 0, mimeType, missing };
    }

    // A readable message from a FastAPI error body
    function errorMessage(data, status) {
        const detail = data && data.detail;
        if (typeof detail === 'string') return detail;
        if (detail && typeof detail.message === 'string') return detail.message;
        if (status === 401) return 'Your login has expired. Please log in again.';
        if (status === 413) return 'The video is larger than the server accepts.';
        if (status >= 500) return `The server had a problem (error ${status}).`;
        return `The request failed (error ${status}).`;
    }

    function withStatus(err, status, data) {
        err.status = status;
        err.data = data;
        return err;
    }

    // Why a request failed, in plain words (Phase 21): the server's own sentence for a refused request (its messages
    // are written for people; a part in brackets after a space holds technical detail and is left out), never a status
    // code or exception text — those only with ?visualDebug (technicalDetail)
    function plainReason(err) {
        const status = err && typeof err.status === 'number' ? err.status : null;
        if (status === 0) return 'The connection to the server was lost.';
        if (status === 401) return 'Your login has expired. Please log in again.';
        if (status === 413) return 'The video is larger than the server accepts.';
        if (status >= 500 || status === null) return status === null ? '' : 'The server had a problem.';
        const detail = err.data && err.data.detail;
        const text = typeof detail === 'string' ? detail : (detail && typeof detail.message === 'string' ? detail.message : '');
        const plain = text.replace(/\s+\([^()]*\)/g, '').trim();
        return plain && !/[.!?]$/.test(plain) ? `${plain}.` : plain;
    }

    // The tab's name as the browser's share dialog lists it: the page's title (Phase 21), or index.html's own title
    const TAB_TITLE = 'Aadhi — Educational Video Studio';
    function tabTitle(win) {
        const doc = win && win.document;
        const title = doc && typeof doc.title === 'string' ? doc.title.trim() : '';
        return title || TAB_TITLE;
    }

    function technicalDetail(err) {
        if (!err) return '';
        if (typeof err === 'string') return err;
        const status = typeof err.status === 'number' ? `[${err.status}] ` : '';
        return `${status}${err.message || String(err)}`;
    }

    // ---- keyboard use of a modal panel (Phase 21): the focus moves in on open, Tab stays inside, and it goes back to
    // the control that opened the panel on close (the same helpers in assets.js)
    function contains(root, node) {
        return !!(root && node && typeof root.contains === 'function' && root.contains(node));
    }

    function tabbable(root) {
        return Array.from(root.querySelectorAll('button, select, input, textarea, a[href], video[controls], audio[controls], [tabindex]')).filter(node =>
            !node.disabled && node.getAttribute('tabindex') !== '-1' && node.getAttribute('disabled') === null && node.getAttribute('hidden') === null
            && (typeof node.getClientRects !== 'function' || node.getClientRects().length > 0));
    }

    function trapTab(doc, root, e) {
        const list = tabbable(root);
        if (!list.length) return;
        const active = doc.activeElement;
        const at = list.indexOf(active);
        let to = null;
        if (!contains(root, active)) to = e.shiftKey ? list[list.length - 1] : list[0];
        else if (e.shiftKey && at <= 0) to = list[list.length - 1];
        else if (!e.shiftKey && at === list.length - 1) to = list[0];
        if (!to) return;
        if (typeof e.preventDefault === 'function') e.preventDefault();
        to.focus();
    }

    function focusNode(node) {
        if (node && typeof node.focus === 'function') {
            try { node.focus(); } catch (e) { /* not essential */ }
        }
    }

    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

    // Uploads `size` bytes in chunks from the server's current offset. A dropped connection or
    // server hiccup is retried with backoff and resumes from what the server actually received.
    async function uploadResumable({ size, slice, received, send, onProgress = () => {}, chunkSize = CHUNK_BYTES, attempts = 4, wait = sleep }) {
        let offset = await received();
        let failures = 0;
        onProgress(offset, size);
        while (offset < size) {
            const start = offset;
            try {
                offset = await send(start, slice(start, Math.min(start + chunkSize, size)), sent => onProgress(start + sent, size));
                failures = 0;
            } catch (err) {
                if (typeof err.received === 'number') {
                    offset = err.received; // the server says where to resume
                } else if (err.status && err.status < 500 && err.status !== 408 && err.status !== 429) {
                    throw err; // refused (login expired, not uploading, too large): retrying will not help
                } else if (++failures >= attempts) {
                    throw err;
                } else {
                    await wait(1000 * 2 ** (failures - 1));
                    offset = await received().catch(() => start);
                }
            }
            onProgress(offset, size);
        }
        return offset;
    }

    class ExportApi {
        // fetch: the page's fetch (adds the login and server address); base()/token() are for the XHR upload
        constructor({ fetch, base = () => '', token = () => null, XMLHttpRequest }) {
            this.fetchFn = fetch;
            this.base = base;
            this.token = token;
            this.XHR = XMLHttpRequest;
        }

        async request(method, path, body) {
            const init = { method, headers: {} };
            if (body !== undefined) {
                init.headers['Content-Type'] = 'application/json';
                init.body = JSON.stringify(body);
            }
            let res;
            try {
                res = await this.fetchFn(path, init);
            } catch (e) {
                throw withStatus(new Error('The server could not be reached. Check the connection and try again.'), 0);
            }
            const data = await res.json().catch(() => null);
            if (!res.ok) throw withStatus(new Error(errorMessage(data, res.status)), res.status, data);
            return data;
        }

        create(body) { return this.request('POST', '/api/exports', body); }
        update(id, body) { return this.request('PATCH', `/api/exports/${id}`, body); }
        get(id) { return this.request('GET', `/api/exports/${id}`); }
        list(projectId) { return this.request('GET', '/api/exports' + (projectId ? `?project_id=${projectId}` : '')); }
        link(id) { return this.request('POST', `/api/exports/${id}/link`); }
        complete(id, body) { return this.request('POST', `/api/exports/${id}/complete`, body); }
        retryMp4(id) { return this.request('POST', `/api/exports/${id}/outputs/mp4`); }
        received(id) { return this.request('GET', `/api/exports/${id}/upload`).then(data => data.received); }
        // Phase 22: a server render ({project_id, missing_visuals, page_settings, retry_of_id}); cancelled like any export
        render(body) { return this.request('POST', '/api/exports/render', body); }
        cancel(id) { return this.update(id, { status: 'CANCELLED' }); }

        // XHR rather than fetch: only XHR reports upload progress
        sendChunk(id, offset, chunk, total, onProgress) {
            return new Promise((resolve, reject) => {
                const xhr = new this.XHR();
                xhr.open('PUT', `${this.base()}/api/exports/${id}/upload?offset=${offset}&total=${total}`);
                const token = this.token();
                if (token) xhr.setRequestHeader('Authorization', 'Bearer ' + token);
                xhr.setRequestHeader('Content-Type', 'application/octet-stream');
                xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded); };
                xhr.onload = () => {
                    let data = null;
                    try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
                    if (xhr.status >= 200 && xhr.status < 300 && data) return resolve(data.received);
                    const err = withStatus(new Error(errorMessage(data, xhr.status)), xhr.status, data);
                    if (xhr.status === 409 && data && data.detail && typeof data.detail.received === 'number') err.received = data.detail.received;
                    reject(err);
                };
                xhr.onerror = () => reject(withStatus(new Error('the connection to the server was lost'), 0));
                xhr.send(chunk);
            });
        }
    }

    // Captures this tab with its sound and records it. The file (Blob) is assembled only after
    // the recorder's final `stop` event, once every chunk has arrived.
    // The video bitrate for a captured size (a fixed bitrate given to the recorder wins)
    function bitrateFor(track) {
        return track && track.height > 720 ? VIDEO_BITS_PER_SECOND_HD : VIDEO_BITS_PER_SECOND;
    }

    class LessonRecorder {
        constructor({ win, mimeType, timesliceMs = TIMESLICE_MS, videoBitsPerSecond = null, audioBitsPerSecond = null,
            frameRate = FRAME_RATE, maxCapture = MAX_CAPTURE, now = () => Date.now() }) {
            this.win = win;
            this.mimeType = mimeType;
            this.timesliceMs = timesliceMs;
            this.videoBitsPerSecond = videoBitsPerSecond;
            this.audioBitsPerSecond = audioBitsPerSecond;
            this.frameRate = frameRate;
            this.maxCapture = maxCapture;
            this.now = now;
            this.stream = null;
            this.recorder = null;
            this.onSharingEnded = null; // set to treat "Stop sharing" as a normal stop (manual recordings)
            // Measurements of this recording (Phase 7): configuration, what the browser granted, timings
            this.metrics = { requestedFps: frameRate, mimeType, videoBitsPerSecond, audioBitsPerSecond, track: null,
                chunks: 0, bytes: 0, startLatencyMs: null, stopLatencyMs: null, durationMs: null };
        }

        // Call straight from a click: browsers open the share dialog only on a user gesture
        async capture() {
            let stream;
            try {
                stream = await this.win.navigator.mediaDevices.getDisplayMedia({
                    // No size here: with only a maximum, Chrome captures at that maximum and upscales smaller tabs
                    video: { displaySurface: 'browser', frameRate: { ideal: this.frameRate, max: this.frameRate } },
                    audio: true,
                    preferCurrentTab: true,
                    selfBrowserSurface: 'include',
                    surfaceSwitching: 'exclude',
                    systemAudio: 'include'
                });
            } catch (err) {
                if (err && (err.name === 'NotAllowedError' || err.name === 'AbortError')) {
                    throw new ExportError('Screen sharing was cancelled, so nothing was recorded. Your lesson is unchanged; you can try again.',
                        { status: 'CANCELLED', detail: err.name });
                }
                throw new ExportError('The browser could not start recording this tab. Your lesson is unchanged; try again, or use a recent version of Chrome or Edge.',
                    { detail: err && `${err.name}: ${err.message}` });
            }
            const video = stream.getVideoTracks()[0];
            let settings = video && video.getSettings ? video.getSettings() : {};
            const cap = this.maxCapture;
            if (video && video.applyConstraints && (settings.width > cap.width || settings.height > cap.height)) {
                // A tab larger than 1080p (HiDPI screen): record it scaled down, keeping its shape
                try {
                    await video.applyConstraints({ width: { max: cap.width }, height: { max: cap.height },
                        frameRate: { ideal: this.frameRate, max: this.frameRate } });
                    settings = video.getSettings();
                } catch (e) { /* not supported: record at the tab's own size */ }
            }
            let problem = null;
            if (!video) {
                problem = 'The shared screen has no picture, so it cannot be recorded.';
            } else if (settings.displaySurface && settings.displaySurface !== 'browser') {
                problem = `Please share the "${tabTitle(this.win)}" tab itself, not a window or the whole screen, so the lesson and its sound are recorded.`;
            } else if (!stream.getAudioTracks().length) {
                problem = 'The tab\'s sound was not shared, so the narration would be missing. Start again and leave "Also share tab audio" switched on.';
            }
            if (problem) {
                stream.getTracks().forEach(track => track.stop());
                throw new ExportError(problem, { status: 'CANCELLED' });
            }
            this.stream = stream;
            this.metrics.track = { width: settings.width || null, height: settings.height || null, frameRate: settings.frameRate || null,
                audioTracks: stream.getAudioTracks().length };
            return settings;
        }

        // Resolves once recording has actually begun. `this.finished` settles when it ends:
        // with the recording, or with an ExportError if it was interrupted or failed.
        start() {
            const videoBitsPerSecond = this.videoBitsPerSecond || bitrateFor(this.metrics.track);
            this.metrics.videoBitsPerSecond = videoBitsPerSecond;
            const options = { mimeType: this.mimeType, videoBitsPerSecond };
            if (this.audioBitsPerSecond) options.audioBitsPerSecond = this.audioBitsPerSecond;
            const rec = new this.win.MediaRecorder(this.stream, options);
            const requestedAt = this.now();
            this.recorder = rec;
            this.chunks = [];
            this.bytes = 0;
            this.stopping = false;
            let failure = null;
            let settled = false;

            this.finished = new Promise((resolve, reject) => {
                const finish = () => {
                    if (settled) return;
                    settled = true;
                    this.releaseStream();
                    if (failure) return reject(failure);
                    if (!this.stopping) {
                        return reject(new ExportError('Recording was interrupted because screen sharing stopped. Your lesson is still saved; you can retry the export.',
                            { status: 'CANCELLED' }));
                    }
                    const blob = new Blob(this.chunks, { type: rec.mimeType || this.mimeType });
                    this.chunks = []; // the Blob holds the recording now; do not keep a second copy
                    const durationMs = this.now() - this.startedAt;
                    Object.assign(this.metrics, { bytes: blob.size, durationMs, mimeType: blob.type || this.mimeType,
                        stopLatencyMs: this.stopRequestedAt ? this.now() - this.stopRequestedAt : null });
                    resolve({ blob, mimeType: blob.type, bytes: blob.size, durationMs });
                };
                rec.ondataavailable = e => {
                    if (e.data && e.data.size > 0) {
                        this.chunks.push(e.data);
                        this.bytes += e.data.size;
                        this.metrics.chunks++;
                    }
                };
                rec.onerror = e => {
                    failure = new ExportError('The browser stopped the recording because of an error. Your lesson is still saved; you can retry the export.',
                        { detail: e && e.error ? e.error.name : 'MediaRecorder error' });
                    if (rec.state === 'inactive') finish();
                };
                rec.onwarning = e => console.warn('[export] recorder warning', e); // only some older browsers fire this
                rec.onstop = finish;
            });
            this.finished.catch(() => {}); // callers await it; this only silences "unhandled" noise before they do

            // The browser's own "Stop sharing" button ends the video track
            this.stream.getVideoTracks()[0].addEventListener('ended', () => {
                if (this.onSharingEnded) this.onSharingEnded();
                else if (rec.state !== 'inactive') rec.stop();
            });

            return new Promise((resolve, reject) => {
                rec.onstart = () => {
                    this.startedAt = this.now();
                    this.metrics.startLatencyMs = this.startedAt - requestedAt;
                    resolve();
                };
                try {
                    rec.start(this.timesliceMs);
                } catch (e) {
                    this.releaseStream();
                    reject(new ExportError('The browser refused to start the recording.', { detail: e && e.name }));
                }
            });
        }

        async stop(tailMs = 0) {
            this.stopping = true; // from here on, sharing ending is not an interruption
            if (tailMs) await sleep(tailMs);
            this.stopRequestedAt = this.now();
            if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
            return this.finished;
        }

        abort() {
            if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
            this.releaseStream();
        }

        releaseStream() {
            if (this.stream) this.stream.getTracks().forEach(track => track.stop());
        }
    }

    // ---- Formatting ----------------------------------------------------------------

    function formatBytes(bytes) {
        if (!bytes && bytes !== 0) return '';
        const units = ['B', 'KB', 'MB', 'GB'];
        let value = bytes;
        let unit = 0;
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024;
            unit++;
        }
        return `${value.toFixed(unit < 2 ? 0 : 1)} ${units[unit]}`;
    }

    function formatDuration(seconds) {
        if (!seconds && seconds !== 0) return '';
        const s = Math.round(seconds);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = String(s % 60).padStart(2, '0');
        return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
    }

    function formatDate(iso) {
        if (!iso) return '';
        return new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    // 'webm' / 'video/webm;codecs=vp9' -> 'WebM'
    const FORMAT_NAMES = { webm: 'WebM', mp4: 'MP4' };
    function formatName(value) {
        const key = String(value || '').split(';')[0].replace(/^video\//, '').trim().toLowerCase();
        return FORMAT_NAMES[key] || key.toUpperCase();
    }

    function describeExport(exp) {
        return [
            exp.format ? `${formatName(exp.format)} video` : '',
            exp.height ? `${exp.height}p` : '',
            formatDuration(exp.duration_seconds),
            formatBytes(exp.file_size),
            formatDate(exp.completed_at || exp.created_at)
        ].filter(Boolean).join(' · ');
    }

    // A video's status: an icon (hidden from screen readers) and words
    const STATUS_LABELS = {
        QUEUED: ['⏳', 'Waiting'], PREPARING: ['⏳', 'Preparing'], RECORDING: ['●', 'Recording'], UPLOADING: ['⬆', 'Uploading'],
        PROCESSING: ['⏳', 'Saving'], COMPLETED: ['✓', 'Ready'], FAILED: ['✕', 'Failed'], CANCELLED: ['⊘', 'Cancelled']
    };
    const STEP_STATES = { pending: 'not started', active: 'in progress', done: 'done', failed: 'failed' };

    // ---- Panel -----------------------------------------------------------------------

    function el(doc, tag, props, ...children) {
        const node = doc.createElement(tag);
        Object.entries(props || {}).forEach(([key, value]) => {
            if (value === null || value === undefined || value === false) return;
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value === true ? '' : value);
        });
        children.flat().forEach(child => {
            if (child !== null && child !== undefined) node.appendChild(typeof child === 'string' ? doc.createTextNode(child) : child);
        });
        return node;
    }

    class ExportPanel {
        constructor(doc) {
            this.doc = doc;
            this.root = null;
            this.steps = {};
        }

        build() {
            if (this.root) return;
            const doc = this.doc;
            const h = (...args) => el(doc, ...args);
            // An exported HTML copy of the page may carry an old panel
            doc.querySelectorAll('[data-export-runtime]').forEach(node => node.remove());

            this.lessonTitle = h('div', { class: 'export-lesson-title' });
            // Phase 22: "Render video (recommended)" (the server renders it) and "Record the screen" (this tab is recorded);
            // without a render action (setLesson), the one button records as before
            this.renderButton = h('button', { type: 'button', class: 'btn-gold export-render-btn ui-focusable', text: 'Render video (recommended)', hidden: true });
            this.startButton = h('button', { type: 'button', class: 'btn-gold export-start-btn ui-focusable', text: '● Export video' });
            this.startHint = h('p', { class: 'export-hint', text: 'The lesson plays from the start while this tab is recorded (it takes as long as the lesson), then the video is saved to your account.' });
            this.startSection = h('section', { class: 'export-start', 'aria-label': 'Export this lesson' }, this.lessonTitle, this.renderButton, this.startButton, this.startHint);

            this.stepsEl = h('ol', { class: 'export-steps', 'aria-label': 'Export progress' });
            this.messageEl = h('div', { class: 'export-message', role: 'status', 'aria-live': 'polite' });
            this.runActions = h('div', { class: 'export-actions' });
            this.runSection = h('section', { class: 'export-run', hidden: true, 'aria-label': 'Export progress' }, this.stepsEl, this.messageEl, this.runActions);

            this.readyTitle = h('div', { class: 'export-ready-title', text: 'Your video' });
            this.readyMeta = h('div', { class: 'export-ready-meta' });
            this.video = h('video', { class: 'export-preview', controls: true, preload: 'metadata', playsinline: true, 'aria-label': 'Video preview' });
            this.previewStatus = h('div', { class: 'export-preview-status', role: 'status', 'aria-live': 'polite' });
            this.readyActions = h('div', { class: 'export-actions' });
            this.readyNote = h('div', { class: 'export-ready-note', role: 'status', 'aria-live': 'polite' });
            this.readyNext = h('p', { class: 'export-hint export-ready-next' });
            this.readySection = h('section', { class: 'export-ready', hidden: true, 'aria-label': 'Your video' },
                this.readyTitle, this.readyMeta, this.video, this.previewStatus, this.readyActions, this.readyNote, this.readyNext);

            // Problems with a download or preview, wherever it was started (the run's own message may be out of sight)
            this.noticeEl = h('div', { class: 'export-notice', role: 'status', 'aria-live': 'polite', hidden: true });

            this.historyList = h('ul', { class: 'export-history-list', 'aria-label': 'Videos' });
            this.historyRefresh = h('button', { type: 'button', class: 'export-link-btn ui-focusable', text: 'Refresh', 'aria-label': 'Refresh the list of videos' });
            this.historyHeading = h('h3', { text: 'Your exported videos' });
            // Phase 20: the open lesson's videos or all of them (shown once the lesson is saved)
            this.historyLessonButton = h('button', { type: 'button', class: 'export-link-btn ui-focusable', 'data-scope': 'lesson', 'aria-pressed': 'false', text: 'This lesson' });
            this.historyAllButton = h('button', { type: 'button', class: 'export-link-btn ui-focusable', 'data-scope': 'all', 'aria-pressed': 'false', text: 'All videos' });
            this.historyScope = h('div', { class: 'export-history-scope', role: 'group', 'aria-label': 'Which videos to show' },
                this.historyLessonButton, ' · ', this.historyAllButton);
            this.historyScope.hidden = true;
            this.historySection = h('section', { class: 'export-history' },
                h('div', { class: 'export-history-head' }, this.historyHeading, this.historyScope, this.historyRefresh),
                this.historyList);

            this.titleEl = h('h2', { id: 'export-panel-title', tabindex: '-1', text: 'Your videos' });
            this.closeButton = h('button', { type: 'button', class: 'export-close ui-focusable', 'aria-label': 'Close', title: 'Close', text: '✕' });
            // The run and the finished video come first; "Export video" follows them, so once a video is ready its
            // Download is the first thing to do and exporting again is the second
            this.panel = h('div', { class: 'export-panel', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'export-panel-title' },
                h('div', { class: 'export-head' }, this.titleEl, this.closeButton),
                this.runSection, this.readySection, this.startSection, this.noticeEl, this.historySection);
            this.root = h('div', { class: 'export-overlay', 'data-export-runtime': true }, this.panel);
            this.root.addEventListener('keydown', e => e.stopPropagation()); // keep slide shortcuts out of the panel
            this.closeButton.addEventListener('click', () => this.close());
            // Escape closes it and Tab stays inside it while it is in sight (never while the tab is being recorded)
            this.onKeys = e => {
                if (!this.root.classList.contains('open') || this.root.classList.contains('recording-hidden')) return;
                if (typeof this.root.closest === 'function' && this.root.closest('[inert]')) return; // a dialog above this one (e.g. sign in) made it inert: the keys are not ours
                if (doc.querySelector && doc.querySelector('.asset-overlay.open:not(.review-overlay)')) return; // the Library on top has the keys
                if (e.key === 'Tab') trapTab(doc, this.panel, e);
                else if (e.key === 'Escape' || e.key === 'Esc') this.close();
            };

            const v = this.video;
            v.addEventListener('loadstart', () => { if (v.getAttribute('src')) this.previewStatus.textContent = 'Loading preview…'; });
            v.addEventListener('waiting', () => { this.previewStatus.textContent = 'Buffering…'; });
            v.addEventListener('canplay', () => { this.previewStatus.textContent = ''; });
            v.addEventListener('playing', () => { this.previewStatus.textContent = ''; });
            v.addEventListener('error', () => {
                if (!v.getAttribute('src')) return;
                this.previewStatus.textContent = 'The preview could not be loaded. Your video is safe. ';
                if (this.onPreviewError) {
                    this.previewStatus.appendChild(el(doc, 'button', { type: 'button', class: 'export-link-btn ui-focusable', text: 'Try again', onclick: this.onPreviewError }));
                }
            });
            doc.body.appendChild(this.root);
        }

        open() {
            this.build();
            if (!this.root.classList.contains('open')) {
                const active = this.doc.activeElement;
                this.opener = active && active !== this.doc.body && !contains(this.root, active) ? active : null;
                this.root.classList.add('open');
                this.doc.addEventListener('keydown', this.onKeys, true);
                focusNode(this.titleEl); // into the dialog: its heading
            }
        }

        close() {
            if (!this.root) return;
            const wasOpen = this.root.classList.contains('open');
            this.root.classList.remove('open');
            this.doc.removeEventListener('keydown', this.onKeys, true);
            this.video.pause();
            // back to the control that opened the panel (never left on <body>)
            const opener = this.opener;
            this.opener = null;
            if (wasOpen && opener && opener.isConnected !== false) focusNode(opener);
        }

        // Out of sight while the tab is being recorded
        hide() {
            this.build();
            this.video.pause();
            this.root.classList.add('recording-hidden');
        }

        show() {
            if (this.root) this.root.classList.remove('recording-hidden');
        }

        // onRender (Phase 22): the server render, offered first as the recommended way; onStart records the screen
        setLesson(info, onStart, onRender = null) {
            this.build();
            this.startSection.hidden = !info.canExport;
            this.lessonTitle.textContent = info.title || '';
            this.startButton.onclick = onStart;
            this.renderButton.onclick = onRender;
            this.canRender = typeof onRender === 'function';
            this.updateStart();
        }

        setBusy(busy) {
            this.build();
            this.startButton.disabled = busy;
            this.renderButton.disabled = busy;
        }

        // The start button is the panel's main action until a video is shown; then Download is, and this one is secondary.
        // With the server render, "Render video (recommended)" is the main start action and "Record the screen" the second.
        updateStart(again = this.exported) {
            const videoShown = !!this.videoShown;
            const main = 'btn-gold';
            const second = 'export-action export-action-secondary';
            this.renderButton.hidden = !this.canRender;
            if (this.canRender) {
                this.renderButton.className = `${videoShown ? second : main} export-render-btn ui-focusable`;
                this.renderButton.textContent = videoShown && again ? 'Render again' : 'Render video (recommended)';
                this.startButton.className = `${second} export-start-btn ui-focusable`;
                this.startButton.textContent = 'Record the screen';
                this.startHint.textContent = 'Render video makes a 1080p MP4 on the server, frame by frame; it keeps going if you close this page. '
                    + 'Record the screen plays the lesson in this tab and records it (it takes as long as the lesson).';
            } else {
                this.startButton.className = `${videoShown ? second : main} export-start-btn ui-focusable`;
                this.startButton.textContent = videoShown && again ? '● Export again' : '● Export video';
                this.startHint.textContent = 'The lesson plays from the start while this tab is recorded (it takes as long as the lesson), then the video is saved to your account.';
            }
            this.startHint.hidden = videoShown;
        }

        beginRun(steps) {
            this.build();
            this.runSection.hidden = false;
            this.readySection.hidden = true;
            this.videoShown = false;
            this.notice('');
            this.clearPreview();
            this.updateStart();
            this.stepsEl.textContent = '';
            this.steps = {};
            steps.forEach(([key, label]) => {
                const fill = el(this.doc, 'div', { class: 'export-step-fill' });
                const bar = el(this.doc, 'div', { class: 'export-step-bar', hidden: true }, fill);
                const detail = el(this.doc, 'span', { class: 'export-step-detail' });
                const state = el(this.doc, 'span', { class: 'export-step-state ui-visually-hidden', text: STEP_STATES.pending });
                const li = el(this.doc, 'li', { class: 'export-step', 'data-state': 'pending' },
                    el(this.doc, 'span', { class: 'export-step-icon', 'aria-hidden': 'true' }),
                    el(this.doc, 'span', { class: 'export-step-label', text: label }), state, detail, bar);
                this.stepsEl.appendChild(li);
                this.steps[key] = { li, detail, bar, fill, state };
            });
            this.message('');
            this.setActions([]);
        }

        // state: pending | active | done | failed. progress: 0..1, or null while unknown.
        step(key, state, detail, progress) {
            const s = this.steps[key];
            if (!s) return;
            s.li.setAttribute('data-state', state);
            s.state.textContent = STEP_STATES[state] || state; // the icon is drawn by CSS; screen readers get the words
            if (detail !== undefined) s.detail.textContent = detail || '';
            const known = typeof progress === 'number';
            s.bar.hidden = state !== 'active';
            s.bar.classList.toggle('indeterminate', state === 'active' && !known);
            s.fill.style.width = known ? `${Math.round(Math.min(Math.max(progress, 0), 1) * 100)}%` : '';
        }

        failActive(message) {
            Object.values(this.steps).forEach(s => {
                if (s.li.getAttribute('data-state') === 'active') {
                    s.li.setAttribute('data-state', 'failed');
                    s.state.textContent = STEP_STATES.failed;
                    s.bar.hidden = true;
                }
            });
            this.message(message, 'error');
        }

        message(text, kind = 'info') {
            this.build();
            this.messageEl.textContent = text || '';
            this.messageEl.setAttribute('data-kind', kind);
        }

        notice(text, kind = 'info') {
            this.build();
            this.noticeEl.textContent = text || '';
            this.noticeEl.setAttribute('data-kind', kind);
            this.noticeEl.hidden = !text;
        }

        setActions(actions, container = this.runActions) {
            container.textContent = '';
            actions.forEach(({ label, primary, onClick }) => {
                container.appendChild(el(this.doc, 'button', {
                    type: 'button', class: primary ? 'btn-gold export-action ui-focusable' : 'export-action export-action-secondary ui-focusable', text: label, onclick: onClick
                }));
            });
        }

        ask(text, buttons) {
            this.message(text, 'prompt');
            return new Promise(resolve => {
                this.setActions(buttons.map(b => ({ ...b, onClick: () => { this.setActions([]); resolve(b.value); } })));
            });
        }

        // options.finished: the video of the export that just ended (else one opened from the list)
        showReady(exp, actions, note = null, options = {}) {
            this.build();
            const silent = exp.has_audio === false;
            this.readySection.hidden = false;
            this.videoShown = true;
            this.exported = !!options.finished;
            this.readyTitle.textContent = options.finished
                ? (silent ? '⚠ Export complete, but the video has no sound' : '✓ Export complete')
                : (silent ? 'Your video (no sound)' : 'Your video');
            this.readyMeta.textContent = [exp.title, describeExport(exp)].filter(Boolean).join(' · ');
            this.readyNext.textContent = options.finished
                ? 'Your video is saved in your account. Download it to share it; it also stays in the list below.' : '';
            this.readyNext.hidden = !options.finished;
            this.setActions(actions, this.readyActions);
            this.setNote(note);
            this.updateStart(this.exported);
        }

        // note: { text, action?: { label, onClick } } about the MP4 copy and the other files
        setNote(note) {
            this.build();
            this.readyNote.textContent = note ? note.text : '';
            if (note && note.action) {
                this.readyNote.append(' ', el(this.doc, 'button', { type: 'button', class: 'export-link-btn ui-focusable', text: note.action.label, onclick: note.action.onClick }));
            }
        }

        // The first frame shows before playing (a media fragment: the browser fetches that frame, not a blank one)
        setPreview(url, onError) {
            this.onPreviewError = onError;
            this.previewStatus.textContent = 'Loading preview…';
            this.video.src = /#/.test(url) ? url : `${url}#t=0.1`;
        }

        // onRetry: offered as "Try again"
        previewError(text, onRetry) {
            this.build();
            this.previewStatus.textContent = text;
            if (onRetry) this.previewStatus.append(' ', el(this.doc, 'button', { type: 'button', class: 'export-link-btn ui-focusable', text: 'Try again', onclick: onRetry }));
        }

        clearPreview() {
            if (!this.video) return;
            this.video.pause();
            this.video.removeAttribute('src');
            this.video.load();
            this.previewStatus.textContent = '';
        }

        // scope: { current: 'lesson' | 'all', onChange(scope) } while a saved lesson is open, else null
        renderScope(scope) {
            this.build();
            this.historyScope.hidden = !scope;
            this.historyHeading.textContent = scope && scope.current === 'lesson' ? 'Videos of this lesson' : 'Your exported videos';
            [[this.historyLessonButton, 'lesson'], [this.historyAllButton, 'all']].forEach(([button, value]) => {
                const pressed = !!scope && scope.current === value;
                button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
                button.onclick = scope && !pressed ? () => scope.onChange(value) : null;
            });
        }

        // handlers.scope: see renderScope; handlers.lessonMatch(exp) -> { current, text } | null (Phase 20);
        // handlers.debug: also the stored error text of a failed video (Phase 21: only with ?visualDebug)
        renderHistory(exportsList, handlers) {
            this.build();
            const doc = this.doc;
            this.historyRefresh.onclick = handlers.onRefresh;
            this.renderScope(handlers.scope || null);
            this.historyList.textContent = '';
            if (!exportsList.length) {
                const lessonOnly = handlers.scope && handlers.scope.current === 'lesson';
                const next = this.startSection.hidden ? 'Open a lesson and choose Export video.' : 'Choose Export video above to make one.';
                this.historyList.appendChild(el(doc, 'li', { class: 'export-history-empty',
                    text: lessonOnly ? `No videos of this lesson yet. ${next}` : `Your exported videos will appear here. ${next}` }));
                return;
            }
            exportsList.forEach(exp => {
                const actions = el(doc, 'div', { class: 'export-history-actions' });
                const add = (label, onClick, name) => actions.appendChild(el(doc, 'button', { type: 'button', class: 'export-link-btn ui-focusable', text: label,
                    'aria-label': name ? `${name}: ${exp.title || 'video'}` : null, onclick: onClick }));
                if (exp.status === 'COMPLETED') {
                    add('Preview', () => handlers.onPreview(exp), 'Preview');
                    add('Download', () => handlers.onDownload(exp), 'Download');
                    if (exp.outputs && exp.outputs.mp4 && exp.outputs.mp4.status === 'ready' && handlers.onDownloadOutput) {
                        add('MP4', () => handlers.onDownloadOutput(exp, 'mp4'), 'MP4 copy');
                    }
                } else if (exp.status === 'FAILED' || exp.status === 'CANCELLED') {
                    const retry = handlers.retryFor(exp);
                    if (retry) add(retry.label, retry.run);
                }
                const finished = exp.status === 'FAILED' || exp.status === 'CANCELLED';
                const detail = exp.status === 'COMPLETED' ? describeExport(exp)
                    : [finished ? (handlers.debug ? exp.error_message : '') : exp.stage, formatDate(exp.created_at)].filter(Boolean).join(' · ');
                const match = handlers.lessonMatch ? handlers.lessonMatch(exp) : null;
                const [icon, words] = STATUS_LABELS[exp.status] || ['', exp.status];
                this.historyList.appendChild(el(doc, 'li', { class: 'export-history-item', 'data-status': exp.status },
                    el(doc, 'div', { class: 'export-history-main' },
                        el(doc, 'span', { class: 'export-history-title', text: exp.title }),
                        el(doc, 'span', { class: 'export-badge', 'data-status': exp.status }, icon ? el(doc, 'span', { 'aria-hidden': 'true', text: `${icon} ` }) : null, words),
                        match ? el(doc, 'span', { class: 'export-history-match', 'data-match': match.current ? 'current' : 'older' },
                            el(doc, 'span', { 'aria-hidden': 'true', text: match.current ? '✓ ' : '⚠ ' }), match.text) : null,
                        el(doc, 'span', { class: 'export-history-detail', text: detail })),
                    actions));
            });
        }

        // onRetry: offered as "Try again"
        historyError(text, onRetry) {
            this.build();
            this.historyList.textContent = '';
            const item = el(this.doc, 'li', { class: 'export-history-empty', role: 'alert', text });
            if (onRetry) item.append(' ', el(this.doc, 'button', { type: 'button', class: 'export-link-btn ui-focusable', text: 'Try again', onclick: onRetry }));
            this.historyList.appendChild(item);
        }
    }

    // ---- Flow ------------------------------------------------------------------------

    // hooks (from index.html):
    //   lessonInfo()          -> { projectId, title, sceneCount, canExport }
    //   ensureProject()       -> Promise<projectId>  (saves an unsaved lesson first)
    //   prepare(report)       -> Promise<{ warnings }>, report(done, total, label)
    //   play()                starts the lesson in export mode; it calls flow.sceneStarted / flow.lessonFinished
    //   stop()                stops the lesson if an export ends early
    //   setRecordingUi(on)    hides the page's controls while the lesson is recorded
    //   setManualRecording(on) toggles the record button between ● and ■
    //   extras()              -> [{ label, run }] extra downloads for a lesson just exported
    //   openProjectUrl(id)    -> URL of a saved lesson
    //   lessonLink()          optional (Phase 20) -> { project_id, fingerprint, revision } | null (may be async): the lesson
    //                         version a lesson export records; sent with the timeline, kept by the server with the video
    //   lessonFingerprint()   optional (Phase 20) -> the open lesson's current fingerprint | null (may be async): the history
    //                         then says which of its videos match the lesson as it is now
    //   renderSettings()      optional (Phase 22) -> { tts_engine, voice, gemini_voice, rate }: the voice settings the page
    //                         plays with, sent with a server render (with the 'aadhi.cinematic', 'aadhi.presenter' and
    //                         'aadhi_ai_visuals' settings this browser keeps) so the render sounds and looks like the preview
    // prepare() may also give missing: [{ index, title, reason }] (or plain strings): scenes that have no visual yet
    // Frames the page actually painted while recording (requestAnimationFrame), to tell a busy page
    // (long gaps between frames) from a clip that stalls on its own
    function watchFrames(win) {
        const raf = win.requestAnimationFrame ? win.requestAnimationFrame.bind(win) : null;
        const stats = { frames: 0, longFrames: 0, maxGapMs: 0, startedAt: Date.now() };
        if (!raf) return { stop: () => Object.assign(stats, { seconds: 0 }) };
        let last = null;
        let running = true;
        const tick = time => {
            if (!running) return;
            if (last !== null) {
                const gap = time - last;
                stats.frames++;
                if (gap > 50) stats.longFrames++;
                if (gap > stats.maxGapMs) stats.maxGapMs = Math.round(gap);
            }
            last = time;
            raf(tick);
        };
        raf(tick);
        return {
            stop() {
                running = false;
                const seconds = (Date.now() - stats.startedAt) / 1000;
                return Object.assign(stats, { seconds, fps: seconds ? Math.round(stats.frames / seconds * 10) / 10 : 0 });
            }
        };
    }

    // Scenes without a visual, in words ('Scene 4 "Loads in the real world": no AI video yet')
    function missingList(missing) {
        if (!Array.isArray(missing)) return [];
        return missing.map(item => {
            if (typeof item === 'string') return item.trim();
            if (!item || typeof item !== 'object') return '';
            const name = Number.isInteger(item.index) ? `Scene ${item.index + 1}` : 'A scene';
            const title = typeof item.title === 'string' && item.title.trim() ? ` "${item.title.trim()}"` : '';
            const reason = typeof item.reason === 'string' && item.reason.trim() ? `: ${item.reason.trim()}` : '';
            return `${name}${title}${reason}`;
        }).filter(Boolean);
    }

    class ExportFlow {
        // recorderOptions(): extra LessonRecorder options (e.g. a frame rate set for a measurement run)
        // debug: bool | () => bool — raw server and browser error text in messages (Phase 21); without it, ?visualDebug decides
        constructor({ api, hooks, win, panel, Recorder = LessonRecorder, wait = sleep, tailMs = TAIL_MS, recorderOptions = () => ({}), debug }) {
            this.api = api;
            this.debug = debug;
            this.hooks = hooks;
            this.win = win;
            this.panel = panel || new ExportPanel(win.document);
            this.Recorder = Recorder;
            this.wait = wait;
            this.tailMs = tailMs;
            this.busy = false;
            this.job = null;
            this.result = null;
            this.recording = false;
            this.lessonDone = null;
            this.manualStop = null;
            this._lastReport = 0;
            this.recorderOptions = recorderOptions;
            this.lastMetrics = null; // measurements of the latest recording (development / tests)
            this.lessonLink = null; // Phase 20: the lesson version the current lesson export records
            this.historyScope = 'lesson'; // 'lesson' (the open lesson's videos) | 'all'
            this._historyFor = undefined; // the lesson the history scope was chosen for
            this._historySeq = 0;
        }

        isDebug() {
            if (typeof this.debug === 'function') return !!this.debug();
            if (this.debug !== undefined && this.debug !== null) return !!this.debug;
            const loc = this.win && this.win.location;
            return !!loc && /[?&]visualDebug(=|&|$)/.test(String(loc.search || ''));
        }

        // What happened, why (plain words), what to do; the technical detail only in debug mode
        explain(what, err, next) {
            const text = [what, plainReason(err), next].filter(Boolean).join(' ');
            return this.isDebug() && err ? `${text} (${technicalDetail(err)})` : text;
        }

        // A problem with a download or a preview, shown where it is seen (the run's message may be hidden)
        notify(text, kind = 'error') {
            if (typeof this.panel.notice === 'function') this.panel.notice(text, kind);
            else this.panel.message(text, kind);
        }

        open() {
            this.panel.open();
            this.panel.setLesson(this.hooks.lessonInfo(), () => this.startLessonExport(),
                typeof this.api.render === 'function' ? () => this.startRender() : null);
            this.panel.setBusy(this.busy);
            return this.refreshHistory();
        }

        // Called by the lesson (index.html) while an export is recording
        sceneStarted(index, total) {
            if (!this.recording) return;
            this.win.document.title = `● Recording ${index + 1}/${total} · ${this.originalTitle}`;
            this.reportSoon({ stage: `Recording scene ${index + 1} of ${total}`, progress: total ? index / total : null });
        }

        lessonFinished() {
            if (this.lessonDone) this.lessonDone();
        }

        async startLessonExport(retryOf) {
            if (this.busy) return this.panel.open();
            this.begin('lesson', [
                ['prepare', 'Preparing lesson'],
                ['capture', 'Starting recording'],
                ['record', 'Recording lesson'],
                ['finalize', 'Finishing the recording'],
                ['upload', 'Uploading video'],
                ['save', 'Checking and saving the video']
            ]);
            const panel = this.panel;
            try {
                const support = this.requireSupport();
                const info = this.hooks.lessonInfo();
                if (!info.sceneCount) throw new ExportError('Open a lesson before exporting it.');

                panel.step('prepare', 'active', 'Saving the lesson', null);
                const projectId = await this.hooks.ensureProject();
                this.job = await this.api.create({ project_id: projectId, title: info.title, source: 'lesson', retry_of_id: retryOf || null });
                await this.report({ status: 'PREPARING', stage: 'Loading lesson assets', progress: 0 });
                const prepared = await this.hooks.prepare((done, total, label) => {
                    const progress = total ? done / total : null;
                    panel.step('prepare', 'active', `${label} (${done} of ${total})`, progress);
                    this.reportSoon({ stage: `Loading lesson assets (${done} of ${total})`, progress });
                });
                const missing = missingList(prepared.missing);
                // the older warning about a scene without its video is not repeated next to the missing-visual list
                const missingScenes = (Array.isArray(prepared.missing) ? prepared.missing : []).map(m => m && Number.isInteger(m.index) ? `Scene ${m.index + 1}` : null).filter(Boolean);
                const warnings = (prepared.warnings || []).filter(w => !(typeof w === 'string' && /no (ai )?(video|visual|animation)/i.test(w)
                    && missingScenes.some(name => w.startsWith(`${name} `) || w.startsWith(`${name}:`))));
                const { quality = [] } = prepared;
                panel.step('prepare', 'done', warnings.length ? `${warnings.length} item(s) could not be prepared` : 'Everything the lesson needs is loaded');
                // Phase 18: what the lesson's quality check found, under its own heading (it never stops the export)
                const notes = Array.isArray(quality) ? quality.filter(q => typeof q === 'string' && q).slice(0, 12) : [];
                const parts = [];
                if (missing.length) parts.push('These scenes have no visual yet. If you go on, they are recorded without one (the rest of each scene stays as it is):\n• ' + missing.join('\n• '));
                if (warnings.length) parts.push('Some parts of the lesson could not be prepared. They will look the same as they do when you play the lesson:\n• ' + warnings.join('\n• '));
                if (notes.length) parts.push('The quality check found things to review (Visual Review → Quality):\n• ' + notes.join('\n• '));
                if (missing.length || warnings.length || notes.length) {
                    // Phase 22: a missing visual is the teacher's decision, never a default ("Record anyway" only for the rest)
                    const buttons = missing.length
                        ? [{ label: 'Record without these visuals', value: true }, { label: 'Cancel', value: false, primary: true }]
                        : [{ label: 'Record anyway', value: true, primary: true }, { label: 'Cancel', value: false }];
                    const go = await panel.ask(parts.join('\n\n'), buttons);
                    if (!go) throw new ExportError('The export was cancelled before recording.', { status: 'CANCELLED' });
                }
                this.lessonLink = await this.readLessonLink(); // the lesson as it is about to be recorded

                const recorder = new this.Recorder({ win: this.win, mimeType: support.mimeType, ...this.recorderOptions() });
                panel.step('capture', 'active', 'Waiting for you to choose this tab', null);
                const choice = await panel.ask(`Select Start Recording, then choose this tab ("${tabTitle(this.win)}") in the browser's share dialog and keep "Also share tab audio" switched on. The lesson then plays from the start and is recorded; the page controls are hidden until it ends.`,
                    [{ label: 'Start Recording', value: true, primary: true }, { label: 'Cancel', value: false }]);
                if (!choice) throw new ExportError('The export was cancelled before recording.', { status: 'CANCELLED' });
                await recorder.capture(); // runs inside the click, as the browser requires
                panel.step('capture', 'done', 'This tab and its sound are shared');

                const lessonEnded = new Promise(resolve => { this.lessonDone = resolve; });
                // The lesson starts first; recording begins once it is confirmed playing (see record)
                this.result = await this.record(recorder, () => lessonEnded, true, () => this.hooks.play());
                await this.uploadAndSave();
            } catch (err) {
                await this.fail(err);
            } finally {
                this.end();
            }
        }

        // ---- Phase 22: the server render ----

        // "Render video (recommended)": the server renders the saved lesson; the panel follows its progress. missingVisuals:
        // 'refuse' (scenes without a visual stop it, and the teacher is asked) or 'omit' (they are rendered without one)
        async startRender(retryOf = null, missingVisuals = 'refuse') {
            if (this.busy) return this.panel.open();
            this.begin('render', RENDER_STEPS);
            try {
                const info = this.hooks.lessonInfo();
                if (!info.sceneCount) throw new ExportError('Open a lesson before exporting it.');
                this.panel.step('prepare', 'active', 'Saving the lesson', null);
                const projectId = await this.hooks.ensureProject();
                // the editor's pending changes are saved first, as for a recording (lessonLink flushes the autosave), so the
                // server renders the lesson exactly as it is on screen
                await this.readLessonLink();
                let missing = missingVisuals;
                let retry = retryOf || null;
                for (;;) {
                    this.job = await this.api.render({ project_id: projectId, missing_visuals: missing, page_settings: this.pageSettings(), retry_of_id: retry });
                    const outcome = await this.followRender(this.job);
                    if (outcome !== 'omit') break;
                    missing = 'omit'; // the teacher chose to render without the missing visuals: a new render, linked to the refused one
                    retry = this.job.id;
                    this.panel.beginRun(RENDER_STEPS);
                }
            } catch (err) {
                await this.fail(err);
            } finally {
                this.end();
            }
        }

        // A render still running on the server (the page was closed or reloaded meanwhile): followed again
        async resumeRender(exp) {
            if (this.busy) return;
            this.begin('render', RENDER_STEPS);
            this.job = exp;
            let outcome = null;
            try {
                outcome = await this.followRender(exp);
            } catch (err) {
                await this.fail(err);
            } finally {
                this.end();
            }
            if (outcome === 'omit') return this.startRender(exp.id, 'omit');
            return outcome;
        }

        // Follows a render until it ends: 'done' (the video is shown), 'omit' (it stopped at scenes without a visual and the
        // teacher chose to render without them), null (another follow replaced this one); a failure or cancel is thrown
        async followRender(job) {
            const watch = this._renderWatch = (this._renderWatch || 0) + 1;
            this.panel.setActions([{ label: 'Cancel render', onClick: () => this.cancelRender(job.id) },
                { label: 'Close', onClick: () => this.panel.close() }]);
            this.panel.message('The video is rendered on the server. You can close this panel or this page: it keeps going, and the video appears in your list when it is ready.');
            let latest = job;
            let failures = 0;
            for (;;) {
                this.showRenderProgress(latest);
                if (!ACTIVE_STATUSES.includes(latest.status)) break;
                await this.wait(RENDER_POLL_MS);
                if (watch !== this._renderWatch) return null;
                try {
                    latest = await this.api.get(job.id);
                    failures = 0;
                } catch (err) {
                    if (++failures >= RENDER_POLL_FAILURES) {
                        throw new ExportError(['The progress of the render could not be read.', plainReason(err),
                            'The server keeps rendering; open Your videos again later to see it.'].filter(Boolean).join(' '), { detail: err });
                    }
                }
            }
            this.job = latest;
            this._renderEnded = this._renderEnded || new Set();
            this._renderEnded.add(job.id); // a listing read before it ended must not start following it again
            const render = latest.render || {};
            if (latest.status === 'COMPLETED') {
                RENDER_STEPS.forEach(([key]) => this.panel.step(key, 'done'));
                this.panel.message('');
                this.showReady(latest);
                this.refreshHistory();
                return 'done';
            }
            if (render.error_code === 'missing_visuals') {
                const scenes = missingList(render.missing);
                this.panel.step('prepare', 'failed');
                const go = await this.panel.ask(['These scenes have no visual yet, so the video was not rendered:', ...scenes.map(s => `• ${s}`),
                    '', 'Render without these visuals (the rest of each scene stays as it is), or cancel and add the visuals first.'].join('\n'),
                [{ label: 'Render without these visuals', value: true }, { label: 'Cancel', value: false, primary: true }]);
                if (go) return 'omit';
                throw new ExportError('The render was cancelled before it started. Your lesson is unchanged.', { status: 'CANCELLED' });
            }
            if (latest.status === 'CANCELLED') {
                throw new ExportError(latest.error_message || 'The render was cancelled. Your lesson is unchanged.', { status: 'CANCELLED' });
            }
            throw new ExportError(latest.error_message || 'The video could not be rendered. Your lesson is unchanged; try again.');
        }

        // The render's phase on the steps, in words (phase: preparing → capturing → mixing → finishing)
        showRenderProgress(exp) {
            const render = exp.render || {};
            const current = RENDER_PHASE_STEPS[render.phase] || (exp.status === 'COMPLETED' ? null : 'prepare');
            const at = RENDER_STEPS.findIndex(([key]) => key === current);
            RENDER_STEPS.forEach(([key], i) => {
                if (current === null || i < at) this.panel.step(key, 'done');
                else if (i === at) this.panel.step(key, 'active', render.message || exp.stage || '', key === 'render' && typeof exp.progress === 'number' ? exp.progress : null);
                else this.panel.step(key, 'pending');
            });
        }

        async cancelRender(id) {
            try {
                const job = await this.api.cancel(id);
                if (job) this.job = job;
                this.panel.message('Cancelling the render…');
            } catch (err) {
                this.notify(this.explain('The render could not be cancelled.', err, 'Try again in a moment.'));
            }
        }

        // What the render page restores so the video looks and sounds like this browser's preview: the cinematic and
        // presenter settings, the AI-visuals mode, and the voice settings (renderSettings hook). Nothing else.
        pageSettings() {
            const out = {};
            let storage = null;
            try { storage = this.win && this.win.localStorage; } catch (e) { storage = null; }
            if (storage) {
                RENDER_SETTINGS_KEYS.forEach(key => {
                    try {
                        const value = JSON.parse(storage.getItem(key) || 'null');
                        if (value && typeof value === 'object' && !Array.isArray(value)) out[key] = value;
                    } catch (e) { /* unreadable: the defaults */ }
                });
                try {
                    const mode = storage.getItem('aadhi_ai_visuals');
                    if (AI_VISUALS_MODES.includes(mode)) out.aadhi_ai_visuals = mode;
                } catch (e) { /* blocked */ }
            }
            if (typeof this.hooks.renderSettings === 'function') {
                try {
                    const s = this.hooks.renderSettings() || {};
                    ['tts_engine', 'voice', 'gemini_voice'].forEach(key => {
                        if (typeof s[key] === 'string' && s[key]) out[key] = s[key];
                    });
                    if (typeof s.rate === 'number' && Number.isFinite(s.rate)) out.rate = Math.min(1.5, Math.max(0.5, s.rate));
                } catch (e) { /* the page's defaults */ }
            }
            // never more than the server takes (16 KB of compact UTF-8 JSON, measured the same way there)
            const size = value => (typeof TextEncoder === 'function' ? new TextEncoder().encode(JSON.stringify(value)).length : JSON.stringify(value).length * 3);
            if (size(out) > 15000) RENDER_SETTINGS_KEYS.forEach(key => { delete out[key]; });
            return out;
        }

        // The ● button: record whatever happens on screen until it is pressed again
        toggleManualRecording() {
            if (this.manualStop) return this.manualStop();
            if (this.busy) return this.panel.open();
            let support;
            try {
                support = this.requireSupport();
            } catch (err) {
                this.panel.open();
                this.panel.beginRun([['capture', 'Starting recording']]);
                this.panel.failActive(err.message);
                return;
            }
            const recorder = new this.Recorder({ win: this.win, mimeType: support.mimeType, ...this.recorderOptions() });
            // First, while the click still counts as a user gesture
            const captured = recorder.capture();
            return this.runManual(recorder, captured);
        }

        async runManual(recorder, captured) {
            this.begin('manual', [
                ['capture', 'Starting recording'],
                ['record', 'Recording'],
                ['upload', 'Uploading video'],
                ['save', 'Saving video']
            ]);
            this.panel.step('capture', 'active', 'Choose this tab in the browser\'s share dialog', null);
            try {
                await captured;
                this.panel.step('capture', 'done', 'This tab and its sound are shared');
                const info = this.hooks.lessonInfo();
                const projectId = await this.hooks.ensureProject();
                this.job = await this.api.create({ project_id: projectId, title: `${info.title} (recording)`, source: 'manual' });
                const stopped = new Promise(resolve => { this.manualStop = resolve; });
                recorder.onSharingEnded = () => this.manualStop && this.manualStop(); // "Stop sharing" also ends it normally
                this.hooks.setManualRecording(true);
                this.result = await this.record(recorder, () => stopped, false);
                await this.uploadAndSave();
            } catch (err) {
                await this.fail(err);
            } finally {
                this.manualStop = null;
                this.hooks.setManualRecording(false);
                this.end();
            }
        }

        async retryUpload() {
            if (this.busy || !this.result || !this.job) return;
            this.busy = true;
            this.panel.setBusy(true);
            this.panel.message('');
            this.panel.setActions([]);
            try {
                await this.uploadAndSave();
            } catch (err) {
                await this.fail(err);
            } finally {
                this.end();
            }
        }

        // ---- steps ----

        begin(mode, steps) {
            this.busy = true;
            this.mode = mode;
            this.job = null;
            this.result = null;
            this.lessonLink = null; // manual recordings never claim a lesson version
            this.panel.open();
            this.panel.setBusy(true);
            this.panel.beginRun(steps);
        }

        end() {
            this.busy = false;
            this.lessonDone = null;
            this.panel.setBusy(false);
        }

        requireSupport() {
            const support = checkSupport(this.win);
            if (!support.ok) {
                throw new ExportError(`This browser cannot record the lesson (no ${support.missing.join(', ')}). Please use a recent version of Chrome or Edge.`);
            }
            return support;
        }

        // Phase 20: which lesson, and which version of it, a lesson export records (the server checks it and keeps it
        // with the video's timeline). Optional: without the hook, or if it fails, the export goes on without it.
        async readLessonLink() {
            if (!this.hooks.lessonLink) return null;
            try {
                const link = await this.hooks.lessonLink();
                if (!link || typeof link !== 'object') return null;
                return { project_id: link.project_id, fingerprint: link.fingerprint, revision: link.revision };
            } catch (e) {
                console.warn('[export] the lesson version could not be read; the video is saved without it', e);
                return null;
            }
        }

        // startPlayback(): starts the lesson and resolves once it is visibly playing (lesson exports)
        async record(recorder, driveLesson, hideControls, startPlayback = null) {
            await this.report({ status: 'RECORDING', stage: 'Recording', progress: null });
            this.panel.step('record', 'active', hideControls ? 'Recording the lesson…' : 'Recording… press ● again to stop', null);
            this.recording = true;
            this.originalTitle = this.win.document.title;
            this.panel.hide(); // never part of the video
            if (hideControls) this.hooks.setRecordingUi(true);
            let frames = null;
            this.timeline = null;
            try {
                if (startPlayback) await startPlayback();
                await recorder.start();
                frames = watchFrames(this.win);
                if (this.hooks.recordingStarted) this.hooks.recordingStarted(recorder.startedAt);
                await Promise.race([driveLesson(), recorder.finished]);
                const result = await recorder.stop(hideControls ? this.tailMs : 0);
                this.lastMetrics = { recorder: Object.assign({}, recorder.metrics), page: frames.stop(),
                    mascot: this.hooks.metrics ? this.hooks.metrics() : null };
                frames = null;
                this.timeline = this.hooks.timeline ? this.hooks.timeline() : null;
                if (!result.bytes) throw new ExportError('The recording came out empty, so nothing was saved. Please retry the export.');
                this.panel.step('record', 'done', `${formatDuration(result.durationMs / 1000)} recorded`);
                const kind = formatName(result.mimeType);
                this.panel.step('finalize', 'done', [formatBytes(result.bytes), kind ? `${kind} video` : ''].filter(Boolean).join(' · '));
                return result;
            } catch (err) {
                recorder.abort();
                throw err;
            } finally {
                if (frames) frames.stop();
                this.recording = false;
                this.win.document.title = this.originalTitle;
                if (hideControls) {
                    this.hooks.setRecordingUi(false);
                    this.hooks.stop();
                }
                this.panel.show();
            }
        }

        async uploadAndSave() {
            const { blob, mimeType, durationMs } = this.result;
            if (this.job.status !== 'UPLOADING') await this.report({ status: 'UPLOADING', stage: 'Uploading video', progress: 0 });
            this.panel.step('upload', 'active', `0 of ${formatBytes(blob.size)}`, 0);
            try {
                await uploadResumable({
                    size: blob.size,
                    slice: (start, end) => blob.slice(start, end),
                    received: () => this.api.received(this.job.id),
                    send: (offset, chunk, onProgress) => this.api.sendChunk(this.job.id, offset, chunk, blob.size, onProgress),
                    onProgress: (sent, total) => {
                        const pct = total ? Math.floor(sent / total * 100) : 100;
                        this.panel.step('upload', 'active', `${formatBytes(sent)} of ${formatBytes(total)} (${pct}%)`, total ? sent / total : 1);
                    },
                    wait: this.wait
                });
            } catch (err) {
                throw new ExportError(['The upload stopped before the whole video reached the server.', plainReason(err),
                    'The recording is still in this tab and your lesson is saved: select Retry upload to continue from where it stopped.'].filter(Boolean).join(' '),
                { retryUpload: true, detail: err });
            }
            this.panel.step('upload', 'done', formatBytes(blob.size));
            this.panel.step('save', 'active', 'Checking and storing the video', null);

            let saved;
            const timeline = this.lessonLink ? { ...(this.timeline || {}), lesson: this.lessonLink } : (this.timeline || null);
            try {
                saved = await this.api.complete(this.job.id, { size: blob.size, mime_type: mimeType, duration_seconds: durationMs / 1000,
                    timeline });
            } catch (err) {
                if (err.status === 0) {
                    // The connection dropped while the server was saving: it may have finished anyway
                    const latest = await this.api.get(this.job.id).catch(() => null);
                    if (latest && latest.status === 'COMPLETED') saved = latest;
                }
                if (!saved) {
                    const resumable = err.status === 400 && /incomplete/i.test(err.message);
                    // The server's own reason is often technical (what the decoder said): plain words here, the detail in debug mode
                    const why = err.status === 0 ? 'The connection was lost while the video was being saved.' : err.status === 401 ? 'Your login has expired. Please log in again.' : '';
                    throw new ExportError(resumable
                        ? 'Part of the video did not reach the server. The recording is still in this tab: select Retry upload to send the rest.'
                        : ['The server could not save the video.', why, 'Your lesson changes are saved. Try the export again.'].filter(Boolean).join(' '),
                    { retryUpload: resumable, detail: err });
                }
            }
            this.job = saved;
            this.result = null; // the server has it now; free the memory
            this.panel.step('save', 'done', saved.has_audio === false ? 'Saved, but the video has no sound' : 'Saved to your account');
            this.panel.message(saved.has_audio === false
                ? 'The video was saved without sound. Check that "Also share tab audio" was on and that this tab is not muted, then retry.' : '',
            saved.has_audio === false ? 'warn' : 'info');
            this.showReady(saved);
            this.refreshHistory();
        }

        async fail(err) {
            const error = err instanceof ExportError ? err
                : new ExportError(['Something went wrong during the export.', plainReason(err), 'Your lesson is still saved; you can retry.'].filter(Boolean).join(' '), { detail: err });
            if (error.status === 'CANCELLED') console.info('[export]', error.message); // the user's own choice, not a fault
            else console.error('[export]', error.message, error.detail || '');
            if (this.job && !error.retryUpload && this.mode !== 'render') {
                // Already final on the server when it rejected the file itself (a server render is always settled by the server)
                await this.api.update(this.job.id, { status: error.status, error_message: error.message }).catch(() => {});
            }
            this.panel.failActive(this.isDebug() && error.detail ? `${error.message}\n\nDetails: ${technicalDetail(error.detail)}` : error.message);
            const actions = [];
            if (error.retryUpload) actions.push({ label: 'Retry upload', primary: true, onClick: () => this.retryUpload() });
            if (this.mode === 'render') {
                actions.push({ label: 'Render again', primary: true, onClick: () => this.retry() });
                actions.push({ label: 'Record the screen', onClick: () => this.startLessonExport() });
            } else {
                actions.push({ label: this.mode === 'manual' ? 'Record again' : 'Retry export', primary: !error.retryUpload, onClick: () => this.retry() });
            }
            actions.push({ label: 'Close', onClick: () => this.panel.close() });
            this.panel.setActions(actions);
            this.refreshHistory();
        }

        retry() {
            const previous = this.job && this.job.id;
            if (this.mode === 'manual') return this.toggleManualRecording();
            if (this.mode === 'render') return this.startRender(previous);
            return this.startLessonExport(previous);
        }

        // ---- server reporting ----

        async report(body) {
            this._lastReport = Date.now();
            this.job = await this.api.update(this.job.id, body);
            return this.job;
        }

        // Progress detail for the record (visible in history); throttled, never blocks the export
        reportSoon(body) {
            if (!this.job || Date.now() - this._lastReport < REPORT_EVERY_MS) return;
            this._lastReport = Date.now();
            this.api.update(this.job.id, body).then(job => { this.job = job; }, () => {});
        }

        // ---- finished videos ----

        // The video (WebM), and the MP4 copy, subtitles and chapters as they become ready
        readyActions(exp, withExtras) {
            const outputs = exp.outputs || {};
            const ready = kind => outputs[kind] && outputs[kind].status === 'ready';
            const actions = [{ label: `⬇ Download Video${exp.format ? ` (${formatName(exp.format)})` : ''}`, primary: true, onClick: () => this.download(exp) }];
            if (ready('mp4')) actions.push({ label: '⬇ MP4 copy', onClick: () => this.downloadOutput(exp, 'mp4') });
            if (ready('vtt')) actions.push({ label: 'Subtitles (.vtt)', onClick: () => this.downloadOutput(exp, 'vtt') });
            if (ready('chapters')) actions.push({ label: 'Chapters (.txt)', onClick: () => this.downloadOutput(exp, 'chapters') });
            if (withExtras) this.hooks.extras().forEach(extra => actions.push({ label: extra.label, onClick: extra.run }));
            return actions;
        }

        outputNote(exp) {
            const mp4 = (exp.outputs || {}).mp4;
            if (!mp4 || mp4.status === 'ready' || exp.format === 'mp4') return null; // a rendered video is an MP4 itself
            if (mp4.status === 'pending' || mp4.status === 'processing') return { text: 'Making an MP4 copy… (the video above is already saved)' };
            // The server's reason (e.g. what ffmpeg said) only in debug mode
            const detail = this.isDebug() && mp4.error ? ` (${mp4.error})` : '';
            if (mp4.status === 'failed') return { text: `The MP4 copy could not be made. The WebM video above is complete and ready to download.${detail}`, action: { label: 'Try the MP4 again', onClick: () => this.retryMp4(exp) } };
            return { text: `This server cannot make MP4 copies. The WebM video above is complete and plays in most browsers and video players.${detail}` };
        }

        showReady(exp) {
            const withExtras = (this.mode === 'lesson' || this.mode === 'render') && exp.id === (this.job && this.job.id);
            this.panel.showReady(exp, this.readyActions(exp, withExtras), this.outputNote(exp), { finished: true });
            this.loadPreview(exp);
            this.watchOutputs(exp, withExtras);
        }

        // Follows the MP4 copy while the server makes it (every few seconds, up to half an hour)
        watchOutputs(exp, withExtras) {
            const token = this._outputsWatch = (this._outputsWatch || 0) + 1;
            const busy = e => e.outputs && e.outputs.mp4 && ['pending', 'processing'].includes(e.outputs.mp4.status);
            if (!busy(exp)) return;
            const started = Date.now();
            const poll = async () => {
                if (token !== this._outputsWatch || Date.now() - started > 30 * 60 * 1000) return;
                const latest = await this.api.get(exp.id).catch(() => null);
                if (token !== this._outputsWatch) return;
                if (latest && !busy(latest)) {
                    this.panel.setActions(this.readyActions(latest, withExtras), this.panel.readyActions);
                    this.panel.setNote(this.outputNote(latest));
                    this.refreshHistory();
                    return;
                }
                await this.wait(3000);
                poll();
            };
            this.wait(3000).then(poll);
        }

        async retryMp4(exp) {
            try {
                const latest = await this.api.retryMp4(exp.id);
                this.panel.setNote(this.outputNote(latest));
                this.watchOutputs(latest, false);
            } catch (err) {
                this.panel.setNote({ text: this.explain('The MP4 copy could not be started.', err, 'The WebM video above is complete; try again in a moment.') });
            }
        }

        async downloadOutput(exp, kind) {
            try {
                const link = await this.api.link(exp.id);
                const url = link.output_urls && link.output_urls[kind];
                if (!url) {
                    this.notify('That file is not ready yet. Try again in a moment.');
                    return;
                }
                const a = this.win.document.createElement('a');
                a.href = this.api.base() + url;
                a.download = '';
                this.win.document.body.appendChild(a);
                a.click();
                a.remove();
            } catch (err) {
                this.notify(this.explain('The download could not start.', err, 'Your video is safe in your account; try again.'));
            }
        }

        async loadPreview(exp) {
            try {
                const link = await this.api.link(exp.id);
                this.panel.setPreview(this.api.base() + link.preview_url, () => this.loadPreview(exp));
            } catch (err) {
                this.panel.previewError(this.explain('The preview could not be loaded.', err, 'Your video is safe in your account.'), () => this.loadPreview(exp));
            }
        }

        async download(exp) {
            try {
                const link = await this.api.link(exp.id);
                const doc = this.win.document;
                const a = doc.createElement('a');
                a.href = this.api.base() + link.download_url;
                a.download = exp.file_name || '';
                doc.body.appendChild(a);
                a.click();
                a.remove();
            } catch (err) {
                this.notify(this.explain('The download could not start.', err, 'Your video is safe in your account; try Download again.'));
            }
        }

        preview(exp) {
            this.mode = null;
            this.panel.showReady(exp, this.readyActions(exp, false), this.outputNote(exp));
            this.loadPreview(exp);
            this.watchOutputs(exp, false);
        }

        async refreshHistory() {
            const seq = ++this._historySeq;
            try {
                const projectId = this.historyProject();
                const lessonOnly = !!projectId && this.historyScope === 'lesson';
                const [data, fingerprint] = await Promise.all([lessonOnly ? this.api.list(projectId) : this.api.list(), this.currentFingerprint()]);
                if (seq !== this._historySeq) return; // a newer refresh (or a switch) replaces this one
                // Phase 22: a render still running on the server (started before this page was opened) is followed again
                const ended = this._renderEnded || new Set();
                const running = !this.busy && (data.exports || []).find(e => e.render && ACTIVE_STATUSES.includes(e.status) && !ended.has(e.id));
                if (running) this.resumeRender(running);
                this.panel.renderHistory(data.exports, {
                    onRefresh: () => this.refreshHistory(),
                    onPreview: exp => this.preview(exp),
                    onDownload: exp => this.download(exp),
                    onDownloadOutput: (exp, kind) => this.downloadOutput(exp, kind),
                    retryFor: exp => this.retryFor(exp),
                    debug: this.isDebug(),
                    scope: projectId ? { current: lessonOnly ? 'lesson' : 'all', onChange: scope => this.setHistoryScope(scope) } : null,
                    lessonMatch: exp => this.lessonMatch(exp, projectId, fingerprint)
                });
            } catch (err) {
                if (seq !== this._historySeq) return;
                this.panel.historyError(this.explain('Your videos could not be loaded.', err, 'They are safe in your account.'), () => this.refreshHistory());
            }
        }

        // Phase 20: the history shows the open lesson's videos ("This lesson") unless "All videos" is chosen, and every
        // video while the lesson is not saved yet; another lesson starts again on its own videos
        historyProject() {
            const projectId = (this.hooks.lessonInfo() || {}).projectId || null;
            if (projectId !== this._historyFor) {
                this._historyFor = projectId;
                this.historyScope = 'lesson';
            }
            return projectId;
        }

        setHistoryScope(scope) {
            this.historyProject(); // the choice belongs to the lesson open now
            this.historyScope = scope === 'all' ? 'all' : 'lesson';
            return this.refreshHistory();
        }

        // The open lesson's fingerprint now (optional hook); null when unknown
        async currentFingerprint() {
            if (!this.hooks.lessonFingerprint) return null;
            try {
                const value = await this.hooks.lessonFingerprint();
                return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value) ? value : null;
            } catch (e) {
                return null;
            }
        }

        // Whether a finished video of the open lesson shows the lesson as it is now: known only when the server kept
        // the lesson version with the video (lesson_fingerprint) and the page gives the current one
        lessonMatch(exp, projectId, fingerprint) {
            if (!fingerprint || !projectId || exp.status !== 'COMPLETED' || Number(exp.project_id) !== Number(projectId) || !exp.lesson_fingerprint) return null;
            return exp.lesson_fingerprint === fingerprint
                ? { current: true, text: 'Matches the current lesson' }
                : { current: false, text: 'Made before your latest changes' };
        }

        retryFor(exp) {
            const info = this.hooks.lessonInfo();
            if (exp.source === 'lesson' && exp.project_id && exp.project_id === info.projectId && info.canExport) {
                // a server render is retried as a render (Phase 22), a recording as a recording
                if (exp.render && typeof this.api.render === 'function') return { label: 'Render again', run: () => this.startRender(exp.id) };
                return { label: 'Retry', run: () => this.startLessonExport(exp.id) };
            }
            if (exp.project_id) {
                return { label: 'Open lesson', run: () => { this.win.location.href = this.hooks.openProjectUrl(exp.project_id); } };
            }
            return null;
        }
    }

    return {
        ExportApi,
        ExportError,
        ExportFlow,
        ExportPanel,
        FRAME_RATE,
        LessonRecorder,
        MAX_CAPTURE,
        VIDEO_BITS_PER_SECOND,
        VIDEO_BITS_PER_SECOND_HD,
        bitrateFor,
        checkSupport,
        pickMimeType,
        uploadResumable,
        formatDuration,
        formatName,
        plainReason,
        formatBytes,
        missingList,
        RENDER_STEPS,
        TAIL_MS,
        TIMESLICE_MS
    };
});
