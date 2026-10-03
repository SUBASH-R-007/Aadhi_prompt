'use strict';
// Unit tests for the Studio (Phase 20, studio.js; Phase 21 UX pass): the guided lesson workflow overlay. Home (Your lessons
// with plain-word stage chips, one "Create a lesson"), Create ("How would you like to start?": a document, your notes, a
// lesson file), the progress of a lesson being written (real stages only, Continue later, Stop writing asked in the panel,
// failure copy, Try again, opening the finished lesson) and the lesson's stage rail (seven stages, each an icon + words + one
// truthful line from the server's lesson state, the suggested next step, its actions), errors that say whether the work is
// safe, the dialog's keyboard (Tab stays inside, the focus goes back), polling paused in a hidden tab and per-user storage —
// over a fake page adapter, a fake DOM and fake timers (the 2 s / 4 s polls are stepped by hand).
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const S = require('../studio.js');
const { fakeDoc, Node } = require('./helpers/cinematic-dom.js');

// The fake DOM has no remove() / focus(); the Studio detaches its overlay on close and moves focus to view headings
if (typeof Node.prototype.remove !== 'function') {
    Node.prototype.remove = function remove() {
        if (this.parent) {
            this.parent.children = this.parent.children.filter(c => c !== this);
            this.parent = null;
        }
    };
}
if (typeof Node.prototype.focus !== 'function') {
    Node.prototype.focus = function focus() { this.doc.activeElement = this; };
}

const settle = async () => { for (let i = 0; i < 25; i++) await new Promise(r => setImmediate(r)); };
const FP = 'f'.repeat(64);
const PROVIDERS = /openai|gemini|gpt|pollinations|anthropic|claude/i;
const NO_WRITER = "No AI lesson writer is set up on this server, so a lesson can't be written from this content right now. You can still open your lessons or load a lesson file.";

function lessonsReply() {
    return {
        lessons: [
            { project_id: 12, title: 'Photosynthesis <script>alert(1)</script>', updated_at: '2026-10-02T10:05:00', stage: 'review', scene_count: 9,
                generating: false, attention: false, exported: false },
            { project_id: 8, title: 'Cells', updated_at: '2026-09-30T08:00:00', stage: 'completed', scene_count: 1, exported: true },
            { project_id: 5, title: 'Atoms', updated_at: 'not a date', stage: 'needs_attention', scene_count: 4, attention: true }
        ],
        runs: [{ run_id: 'run-9', status: 'running', stage: 'Checking the lesson' }]
    };
}

function sampleState(extra = {}) {
    return {
        project_id: 12, revision: '2026-10-02 10:00:00.000001', fingerprint: FP, title: 'Photosynthesis <img src=x onerror=alert(1)>',
        names: { subject_name: 'Biology', unit_name: 'Plants', session_number: 'Session 1', session_title: 'Photosynthesis' },
        scenes: 9, hidden: 1,
        source: { document_id: 3, analysis_id: 4, file_name: 'plants.pdf' },
        origin: { source: 7, ai: 2, edited: 1 },
        // the server's counts: `needed` = still missing (6 visuals: 1 missing, 3 ready, 1 being made, 1 needing attention)
        media: { needed: 1, ready: 3, generating: 1, attention: 2, failed: 0, items: [
            { scene_index: 2, scene_id: 's-3', slot: 'main', status: 'needs_attention', run_id: 'run-aaa', message: 'Pollinations returned 402 Payment Required' },
            { scene_index: 4, scene_id: 's-5', slot: 'presenter', status: 'failed', run_id: 'run-bbb', message: 'The picture could not be made' },
            { scene_index: 5, scene_id: 's-6', slot: 'side', status: 'running', run_id: 'run-ccc', message: null }
        ] },
        review: { approved: 5, changed: 1, pending: 3, removed: 0 },
        style: { style: 'cinematic_education', version: 2 },
        editor: { edited: 1, moved: 0, hidden: 1 },
        checkpoints: { structure: { at: '2026-10-02T10:01:00' } },
        quality: { status: 'review', counts: { info: 2, notice: 1, warning: 2, error: 0, blocking: 0 }, stale: true },
        exports: { count: 2, latest: { id: 7, status: 'COMPLETED', completed_at: '2026-10-02T09:00:00', matches_lesson: false } },
        stage: 'needs_attention',
        ...extra
    };
}

// Everything done: the state of a lesson ready and exported
function doneState() {
    return sampleState({
        hidden: 0,
        checkpoints: { structure: {}, lesson: { fingerprint: FP } },
        editor: { edited: 0, moved: 0, hidden: 0 },
        media: { needed: 0, ready: 4, generating: 0, attention: 0, failed: 0, items: [] },
        review: { approved: 9, changed: 0, pending: 0, removed: 0 },
        quality: { status: 'good', counts: { info: 1, notice: 0, warning: 0, error: 0, blocking: 0 }, stale: false },
        exports: { count: 1, latest: { id: 9, status: 'COMPLETED', completed_at: '2026-10-02T11:00:00', matches_lesson: true } },
        stage: 'completed'
    });
}

// Straight after writing: nothing confirmed, visuals still to make, never checked, no video (the server's stage: review)
function freshState() {
    return sampleState({
        hidden: 0, checkpoints: {}, editor: { edited: 0, moved: 0, hidden: 0 }, origin: { source: 9, ai: 0, edited: 0 },
        media: { needed: 5, ready: 0, generating: 0, attention: 0, failed: 0, items: [] },
        review: { approved: 0, changed: 0, pending: 0, removed: 0 }, style: null, quality: null, exports: { count: 0, latest: null }, stage: 'review'
    });
}

function fakeTimers() {
    const queue = [];
    let next = 1;
    return {
        queue,
        setTimeout: (fn, ms) => { const id = next++; queue.push({ id, fn, ms }); return id; },
        clearTimeout: id => { const i = queue.findIndex(t => t.id === id); if (i >= 0) queue.splice(i, 1); },
        delays: () => queue.map(t => t.ms),
        async fire() {
            const t = queue.shift();
            assert.ok(t, 'a poll is waiting');
            t.fn();
            await settle();
        }
    };
}

function memoryStorage(init = {}) {
    const map = new Map(Object.entries(init));
    return { map, getItem: k => (map.has(k) ? map.get(k) : null), setItem: (k, v) => { map.set(k, String(v)); }, removeItem: k => { map.delete(k); } };
}

// A document whose own listeners are recorded (the fake document ignores them)
function makeDoc() {
    const doc = fakeDoc();
    doc.listeners = [];
    doc.visibilityState = 'visible';
    doc.addEventListener = (type, fn, capture) => doc.listeners.push({ type, fn, capture: !!capture });
    doc.removeEventListener = (type, fn) => { doc.listeners = doc.listeners.filter(l => !(l.type === type && l.fn === fn)); };
    return doc;
}

function press(doc, key, target, extra = {}) {
    const ev = { key, target: target || doc.body, stopped: false, prevented: false, ...extra,
        stopPropagation() { this.stopped = true; }, preventDefault() { this.prevented = true; } };
    doc.listeners.filter(l => l.type === 'keydown').forEach(l => l.fn(ev));
    return ev;
}

function makeAdapter(over = {}) {
    const calls = [];
    const record = (name, fn) => (...args) => { calls.push([name, ...args]); return fn ? fn(...args) : undefined; };
    const base = {
        listLessons: async () => lessonsReply(),
        lessonState: async () => sampleState(),
        openLesson: async () => {},
        currentLesson: () => null,
        analyzeDocument: async () => ({ source: { document_id: 3, analysis_id: 4 }, title: 'Biology: Photosynthesis' }),
        startLesson: async () => ({ run_id: 'run-1', status: 'queued' }),
        runStatus: async () => ({ run_id: 'run-1', status: 'running', stage: 'Writing the lesson', project_id: null, message: null }),
        cancelRun: async () => ({ run_id: 'run-1', status: 'cancel_requested' }),
        mountSettings: (kind, container) => { container.appendChild(container.doc.createElement('form')); },
        unmountSettings: () => {},
        prepareMedia: async () => ({ batch_id: 'b-1', message: '3 visuals are being made' }),
        retryItem: async () => {},
        dismissItem: async () => {},
        chooseExisting: async () => {},
        openEditor: () => {},
        openReview: () => {},
        runQuality: async () => ({ status: 'good', counts: { info: 0, notice: 0, warning: 0, error: 0, blocking: 0 } }),
        preview: () => {},
        exportVideo: () => {},
        openVideos: () => {},
        checkpoint: async (name, value) => ({ revision: 'r2', checkpoints: { [name]: value } }),
        close: () => {}
    };
    const adapter = {};
    Object.entries({ ...base, ...over }).forEach(([name, fn]) => { adapter[name] = typeof fn === 'function' ? record(name, fn) : fn; });
    return { adapter, calls };
}

async function setup({ adapter: over = {}, storage = null, open = true, search = '' } = {}) {
    const doc = makeDoc();
    if (search) doc.defaultView.location = { search };
    const timers = fakeTimers();
    const { adapter, calls } = makeAdapter(over);
    const panel = new S.StudioPanel({ doc, adapter, storage, setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout });
    if (open) {
        panel.open();
        await settle();
    }
    const named = name => calls.filter(c => c[0] === name);
    const $ = sel => panel.root.querySelector(sel);
    const $$ = sel => panel.root.querySelectorAll(sel);
    const click = async sel => {
        const n = typeof sel === 'string' ? $(sel) : sel;
        assert.ok(n, `no ${sel}`);
        assert.equal(n.getAttribute('disabled'), null, `${sel} is disabled`);
        n.fire('click');
        await settle();
        return n;
    };
    const type = (sel, value) => { const n = $(sel); assert.ok(n, `no ${sel}`); n.value = value; n.fire('input'); return n; };
    // Home → "Create a lesson" → "How would you like to start?"
    const create = async () => { await click('[data-action="create"]'); assert.equal(panel.view, 'create'); };
    return { doc, panel, adapter, calls, named, timers, $, $$, click, type, create, text: () => panel.root.textContent };
}

const stageButton = (ctx, key) => ctx.$(`button[data-stage="${key}"]`);
const railFacts = ctx => ctx.$$('.studio-stage').map(b => ({
    key: b.getAttribute('data-stage'), status: b.getAttribute('data-status'), state: b.getAttribute('data-state'),
    text: b.querySelector('.studio-stage-status').textContent, summary: b.querySelector('.studio-stage-summary').textContent
}));
const steps = ctx => ctx.$$('.studio-step').map(li => [li.getAttribute('data-step'), li.querySelector('.studio-step-state').textContent]);
const noMarkup = root => root.all().forEach(n => assert.equal(n._html, undefined, 'innerHTML is never used'));

// ---- Home ---------------------------------------------------------------------------------------------------------------

test('Home: Your lessons (plain-word stage chips, when updated, Continue / Open), lessons being written, one primary "Create a lesson"', async () => {
    const ctx = await setup();
    const { panel, doc } = ctx;
    assert.equal(panel.isOpen, true);
    assert.equal(doc.body.children.filter(c => c.classList.contains('studio-root')).length, 1);
    assert.equal(panel.root.getAttribute('data-view'), 'home');
    assert.equal(ctx.$('#studio-heading').textContent, 'Your lessons');
    assert.equal(ctx.$('.studio-panel').getAttribute('role'), 'dialog');
    assert.equal(ctx.$('.studio-brand').textContent, '✦ Aadhi Studio');
    const create = ctx.$$('[data-action="create"]');
    assert.equal(create.length, 1, 'one "Create a lesson" on the screen');
    assert.equal(create[0].textContent, 'Create a lesson');
    assert.ok(create[0].classList.contains('studio-btn-primary'));
    // the ways to start are on their own screen
    assert.equal(ctx.$('[data-action="from-document"]'), null);
    assert.equal(ctx.$('[data-action="from-text"]'), null);
    assert.equal(ctx.named('listLessons').length, 1);
    assert.equal(ctx.$('.studio-lesson-list').getAttribute('aria-label'), 'Your lessons');
    const items = ctx.$$('.studio-lesson-item[data-project-id]');
    assert.deepEqual(items.map(i => i.getAttribute('data-project-id')), ['12', '8', '5']);
    // lesson titles are data: an XSS title stays text
    assert.equal(items[0].querySelector('.studio-lesson-title').textContent, 'Photosynthesis <script>alert(1)</script>');
    assert.equal(panel.root.querySelector('script'), null);
    assert.deepEqual(items.map(i => i.querySelector('.studio-chip').textContent), ['● Ready for review', '✓ Video ready', '⚠ Needs attention']);
    assert.deepEqual(items.map(i => i.querySelector('.studio-chip').getAttribute('data-lesson-stage')), ['review', 'completed', 'needs_attention']);
    assert.deepEqual(items.map(i => i.querySelector('[data-action="open-lesson"]').textContent), ['Continue', 'Open', 'Continue']);
    assert.deepEqual(items.map(i => i.querySelector('.studio-lesson-meta').textContent),
        ['9 scenes · Updated 2 Oct 2026, 10:05', '1 scene · Updated 30 Sep 2026, 08:00', '4 scenes']);
    // a run still writing: followable, its stage in words, never its id
    const run = ctx.$('.studio-run-item');
    assert.equal(run.querySelector('.studio-chip').textContent, '✎ Being written');
    assert.equal(run.querySelector('.studio-lesson-meta').textContent, 'Checking the lesson');
    assert.equal(run.querySelector('[data-action="follow-run"]').textContent, 'See progress');
    assert.ok(!ctx.text().includes('run-9'));
    // every chip is an icon and words
    ctx.$$('.studio-chip').forEach(c => assert.match(c.textContent, /^[✓●○⚠✎] \S/));
    // a lesson still being written keeps Home asking (every ~4 s)
    assert.deepEqual(ctx.timers.delays(), [4000]);
    await ctx.timers.fire();
    assert.equal(ctx.named('listLessons').length, 2);
    noMarkup(panel.root);
    // opening twice builds nothing more
    panel.open();
    assert.equal(doc.body.children.filter(c => c.classList.contains('studio-root')).length, 1);
});

test('Home: "Loading your lessons…", then the empty state (one "Create a lesson"); Continue opens the lesson on the page, then its stages', async () => {
    let answer;
    const empty = await setup({ adapter: { listLessons: () => new Promise(r => { answer = r; }) } });
    assert.equal(empty.$('.studio-loading').textContent, 'Loading your lessons…');
    assert.equal(empty.$$('[data-action="create"]').length, 1, 'creating is possible while the list loads');
    answer({ lessons: [], runs: [] });
    await settle();
    assert.equal(empty.$('.studio-empty-text').textContent, "You haven't created a lesson yet. Turn a document or your notes into an educational video.");
    assert.equal(empty.$$('[data-action="create"]').length, 1, 'still one: the empty state\'s own');
    assert.ok(empty.$('.studio-empty [data-action="create"]').classList.contains('studio-btn-primary'));
    assert.equal(empty.$('.studio-loading'), null);
    assert.deepEqual(empty.timers.delays(), [], 'nothing being written: no polling');
    await empty.click('.studio-empty [data-action="create"]');
    assert.equal(empty.panel.view, 'create');

    const ctx = await setup();
    await ctx.click('[data-project-id="12"] [data-action="open-lesson"]');
    assert.deepEqual(ctx.named('openLesson'), [['openLesson', 12]]);
    assert.deepEqual(ctx.named('lessonState'), [['lessonState', 12]]);
    assert.equal(ctx.panel.view, 'lesson');
    assert.equal(ctx.$('#studio-heading').textContent, 'Photosynthesis <img src=x onerror=alert(1)>');
    assert.equal(ctx.panel.root.querySelector('img'), null);
    assert.equal(ctx.$('.studio-lesson-head .studio-chip').textContent, '⚠ Needs attention');
    assert.equal(ctx.$('.studio-lesson-head .studio-lesson-meta').textContent, '9 scenes · From “plants.pdf”');
    // back to the list
    await ctx.click('.studio-top [data-action="home"]');
    assert.equal(ctx.panel.view, 'home');
    assert.equal(ctx.named('listLessons').length, 2);
});

// ---- create ------------------------------------------------------------------------------------------------------------

test('Create: "How would you like to start?" — upload a document, paste your notes, open a lesson file (when the page has one)', async () => {
    const ctx = await setup();
    await ctx.create();
    assert.equal(ctx.panel.root.getAttribute('data-view'), 'create');
    assert.equal(ctx.$('#studio-heading').textContent, 'How would you like to start?');
    assert.equal(ctx.doc.activeElement, ctx.$('#studio-heading'), 'the focus moves to the new view\'s heading');
    assert.equal(ctx.$('.studio-intro').textContent, 'Aadhi prepares the lesson; you can review and change everything before you export.');
    const choices = ctx.$$('.studio-choice');
    assert.deepEqual(choices.map(c => c.getAttribute('data-action')), ['from-document', 'from-text']);
    assert.deepEqual(choices.map(c => c.querySelector('.studio-choice-title').textContent), ['Upload a document', 'Paste your notes']);
    assert.equal(choices[0].querySelector('.studio-choice-text').textContent,
        'A PDF, Word or text file. Aadhi reads its structure first; your original file is kept.');
    // each choice is one button, named by its title and described by its line
    choices.forEach(c => {
        assert.equal(ctx.$(`#${c.getAttribute('aria-labelledby')}`).textContent, c.querySelector('.studio-choice-title').textContent);
        assert.equal(ctx.$(`#${c.getAttribute('aria-describedby')}`).textContent, c.querySelector('.studio-choice-text').textContent);
    });
    assert.equal(ctx.$('[data-action="open-file"]'), null, 'no lesson-file choice without adapter.openLessonFile');
    assert.ok(!/\b(script|parser|JSON|API|endpoint)\b/.test(ctx.$('.studio-create').textContent), 'no technical explanations');
    // back to the list
    assert.equal(ctx.$('.studio-top [data-action="home"]').getAttribute('hidden'), null);
    await ctx.click('.studio-top [data-action="home"]');
    assert.equal(ctx.panel.view, 'home');

    // with the page's own lesson-file upload
    const file = await setup({ adapter: { openLessonFile: async () => ({ projectId: 30 }) } });
    await file.create();
    assert.equal(file.$$('.studio-choice').length, 3);
    assert.equal(file.$('[data-choice="file"] .studio-choice-title').textContent, 'Open a lesson file');
    await file.click('[data-action="open-file"]');
    assert.equal(file.named('openLessonFile').length, 1);
    assert.equal(file.panel.view, 'lesson');
    assert.deepEqual(file.named('lessonState').at(-1), ['lessonState', 30]);
});

test('The page\'s own Home and Create: showCreate() / showHome() / open({view}) open the Studio there, or move it there', async () => {
    const ctx = await setup({ open: false, adapter: { currentLesson: () => ({ projectId: 12 }) } });
    ctx.panel.showCreate();
    await settle();
    assert.equal(ctx.panel.isOpen, true);
    assert.equal(ctx.panel.view, 'create', 'what the user asked for, not the page\'s open lesson');
    assert.equal(ctx.named('lessonState').length, 0);
    assert.equal(ctx.named('listLessons').length, 1, 'asked once, to know whether a lesson writer is set up');
    assert.deepEqual(ctx.timers.delays(), [], 'Create polls nothing');
    ctx.panel.showHome();
    await settle();
    assert.equal(ctx.panel.view, 'home');
    assert.equal(ctx.$('#studio-heading').textContent, 'Your lessons');
    ctx.panel.close();
    ctx.panel.open({ view: 'create' });
    await settle();
    assert.equal(ctx.panel.view, 'create');
    assert.equal(ctx.named('lessonState').length, 0);
    // a lesson list that cannot be loaded says nothing on Create (it only wanted to know about the writer)
    const failing = await setup({ open: false, adapter: { listLessons: async () => { throw new Error('Failed to fetch'); } } });
    failing.panel.showCreate();
    await settle();
    assert.equal(failing.$('.studio-error'), null);
});

test('From a document: the Document Assistant, then "Name your lesson" (prefilled from the title, once), then startLesson with names', async () => {
    const storage = memoryStorage();
    const ctx = await setup({ storage });
    await ctx.create();
    await ctx.click('[data-action="from-document"]');
    assert.equal(ctx.named('analyzeDocument').length, 1);
    assert.equal(ctx.panel.view, 'name');
    assert.equal(ctx.$('#studio-heading').textContent, 'Name your lesson');
    assert.equal(ctx.doc.activeElement, ctx.$('#studio-heading'), 'focus moves to the new view\'s heading');
    assert.match(ctx.$('.studio-intro').textContent, /From your document: “Biology: Photosynthesis”/);
    assert.equal(ctx.$('[data-field="subject_name"]').value, 'Biology');
    assert.equal(ctx.$('[data-field="session_title"]').value, 'Photosynthesis');
    assert.equal(ctx.$('[data-field="session_number"]').value, 'Session 1');
    assert.equal(ctx.$('[data-field="unit_name"]').value, '');
    assert.equal(ctx.$('.studio-next').textContent, 'Next, Aadhi writes the lesson. You can review and change everything before you export.');
    ctx.type('[data-field="unit_name"]', 'Plants');
    await ctx.click('[data-action="start-lesson"]');
    assert.deepEqual(ctx.named('startLesson'), [['startLesson', {
        source: { document_id: 3, analysis_id: 4 },
        names: { subject_name: 'Biology', unit_name: 'Plants', session_number: 'Session 1', session_title: 'Photosynthesis' }
    }]]);
    // the progress view: the source was analysed (✓), the lesson is being written (●)
    assert.equal(ctx.panel.view, 'progress');
    assert.deepEqual(ctx.named('runStatus'), [['runStatus', 'run-1']]);
    assert.deepEqual(steps(ctx), [
        ['source', '✓ Complete'], ['write', '● In progress'], ['check', '○ Not started'], ['save', '○ Not started']]);
    assert.deepEqual(ctx.$$('.studio-step-label').map(n => n.textContent),
        ['Understanding your content', 'Writing the lesson', 'Checking the lesson', 'Saving the lesson']);
    assert.equal(ctx.$('.studio-step[data-step="write"]').getAttribute('aria-current'), 'step');
    assert.equal(ctx.$('.studio-progress-title').textContent, 'Photosynthesis');
    // remembered, so a reload can follow it again
    assert.equal(JSON.parse(storage.map.get('aadhi.studio.run')).run_id, 'run-1');
    assert.ok(!ctx.text().includes('run-1'), 'the run id is not shown');

    // a document title with no subject in it: the session title only (never the same words twice on the title card)
    const single = await setup({ adapter: { analyzeDocument: async () => ({ source: { document_id: 3, analysis_id: 4 }, title: 'How Plants Make Food' }) } });
    await single.create();
    await single.click('[data-action="from-document"]');
    assert.equal(single.$('[data-field="subject_name"]').value, '');
    assert.equal(single.$('[data-field="session_title"]').value, 'How Plants Make Food');
    await single.click('[data-action="start-lesson"]');
    assert.deepEqual(single.named('startLesson')[0][1].names, { subject_name: '', unit_name: '', session_number: 'Session 1', session_title: 'How Plants Make Food' });
});

test('From a document: closing the assistant without choosing changes nothing', async () => {
    const ctx = await setup({ adapter: { analyzeDocument: async () => null } });
    await ctx.create();
    await ctx.click('[data-action="from-document"]');
    assert.equal(ctx.panel.view, 'create');
    assert.equal(ctx.named('startLesson').length, 0);
    assert.equal(ctx.$('.studio-error'), null);
});

test('From your notes: paste text, the session title follows its first line (the subject is left to you), startLesson({text, names}); checks before sending', async () => {
    const ctx = await setup({ adapter: { openLessonFile: async () => null } });
    await ctx.create();
    await ctx.click('[data-action="from-text"]');
    assert.equal(ctx.panel.view, 'paste');
    assert.equal(ctx.$('#studio-heading').textContent, 'Paste your notes');
    // nothing pasted: a plain sentence, nothing sent
    await ctx.click('[data-action="start-lesson"]');
    assert.equal(ctx.$('.studio-error').textContent, '⚠ Paste the lesson content first.');
    assert.equal(ctx.named('startLesson').length, 0);
    // said under the notes box (not at the top of the form), which is marked invalid, described by it and focused
    const area = ctx.$('[data-field="text"]');
    const said = ctx.$('.studio-field-error');
    assert.equal(ctx.$$('.studio-error').length, 1, 'said once');
    assert.equal(said.getAttribute('role'), 'alert');
    const formKids = ctx.$('.studio-form').children;
    assert.equal(formKids.indexOf(said), formKids.indexOf(area.parent) + 1, 'right under the notes box');
    assert.equal(area.getAttribute('aria-invalid'), 'true');
    assert.equal(area.getAttribute('aria-describedby'), 'studio-text-error studio-text-hint');
    assert.equal(said.id, 'studio-text-error');
    assert.equal(ctx.doc.activeElement, area);
    // a saved lesson (JSON) is pointed to "Open a lesson file"
    ctx.type('[data-field="text"]', JSON.stringify({ scenes: [{ type: 'content' }] }));
    // typing again: the error goes, in place (the same box keeps the caret)
    assert.equal(ctx.$('[data-field="text"]'), area);
    assert.equal(ctx.$('.studio-error'), null);
    assert.equal(area.getAttribute('aria-invalid'), null);
    assert.equal(area.getAttribute('aria-describedby'), 'studio-text-hint');
    assert.equal(ctx.$('.studio-json-note').getAttribute('hidden'), null);
    assert.ok(ctx.$('.studio-json-note [data-action="open-file"]'));
    // real content: the session title follows the first line until changed; the subject is never filled with the same words
    const text = '# Plant cells\n\nCells have walls.\n\n- Wall\n- Membrane';
    ctx.type('[data-field="text"]', text);
    assert.equal(ctx.$('.studio-json-note').getAttribute('hidden'), '');
    assert.equal(ctx.$('[data-field="session_title"]').value, 'Plant cells');
    assert.equal(ctx.$('[data-field="subject_name"]').value, '');
    ctx.type('[data-field="subject_name"]', 'Biology');
    ctx.type('[data-field="text"]', `# Cells of plants\n${text}`);
    assert.equal(ctx.$('[data-field="session_title"]').value, 'Cells of plants', 'typing a subject does not stop the title following');
    assert.equal(ctx.$('[data-field="subject_name"]').value, 'Biology', 'a name the user typed is kept');
    ctx.type('[data-field="session_title"]', 'Plant cells');
    ctx.type('[data-field="text"]', `${text}\n`);
    assert.equal(ctx.$('[data-field="session_title"]').value, 'Plant cells', 'a session title the user typed is kept');
    await ctx.click('[data-action="start-lesson"]');
    assert.deepEqual(ctx.named('startLesson'), [['startLesson', {
        text: `${text}\n`, names: { subject_name: 'Biology', unit_name: '', session_number: 'Session 1', session_title: 'Plant cells' }
    }]]);
    // no source document: no "Understanding your content" stage is claimed
    assert.equal(ctx.panel.view, 'progress');
    assert.equal(ctx.$('[data-step="source"]'), null);
    assert.deepEqual(steps(ctx).map(s => s[0]), ['write', 'check', 'save']);
});

test('From your notes: too much text, or no names at all, is refused in plain words', async () => {
    const ctx = await setup();
    await ctx.create();
    await ctx.click('[data-action="from-text"]');
    ctx.type('[data-field="text"]', 'x'.repeat(262145));
    await ctx.click('[data-action="start-lesson"]');
    assert.match(ctx.$('.studio-error').textContent, /too long for one lesson/);
    assert.equal(ctx.$('[data-field="text"]').getAttribute('aria-invalid'), 'true', 'about the notes: said under the box');
    assert.equal(ctx.doc.activeElement, ctx.$('[data-field="text"]'));
    ctx.type('[data-field="text"]', 'Short notes');
    ctx.type('[data-field="subject_name"]', '  ');
    ctx.type('[data-field="session_title"]', '');
    await ctx.click('[data-action="start-lesson"]');
    assert.equal(ctx.$('.studio-error').textContent, '⚠ Give the lesson a subject or a session title first.');
    assert.equal(ctx.$('[data-field="text"]').getAttribute('aria-invalid'), null, 'not about the notes');
    assert.equal(ctx.named('startLesson').length, 0);
    // Cancel goes back home
    await ctx.click('[data-action="cancel-form"]');
    assert.equal(ctx.panel.view, 'home');
});

// ---- progress ----------------------------------------------------------------------------------------------------------

test('Progress: runStatus every 2 s while active, truthful stages only (no percentages); "Stop writing" is asked in the panel first', async () => {
    const replies = [
        { run_id: 'run-1', status: 'queued', stage: null },
        { run_id: 'run-1', status: 'running', stage: 'Checking the lesson' },
        { run_id: 'run-1', status: 'running', stage: 'Saving the lesson' },
        { run_id: 'run-1', status: 'running', stage: 'Saving the lesson' },
        { run_id: 'run-1', status: 'cancel_requested', stage: 'Saving the lesson' },
        { run_id: 'run-1', status: 'cancelled', stage: 'Saving the lesson', message: 'Stopped on request.' }
    ];
    const ctx = await setup({ adapter: { runStatus: async () => replies.shift() } });
    await ctx.create();
    await ctx.click('[data-action="from-text"]');
    ctx.type('[data-field="text"]', 'Photosynthesis\nPlants make food.');
    await ctx.click('[data-action="start-lesson"]');
    assert.deepEqual(steps(ctx), [['write', '● Waiting to start'], ['check', '○ Not started'], ['save', '○ Not started']]);
    assert.deepEqual(ctx.timers.delays(), [2000]);
    assert.equal(ctx.$('.studio-leave').textContent, 'You can safely leave this page: the lesson keeps being written.');
    assert.ok(ctx.$('[data-action="continue-later"]'));
    assert.equal(ctx.$('[data-action="cancel-run"]').textContent, 'Stop writing');
    await ctx.timers.fire();
    assert.deepEqual(steps(ctx), [['write', '✓ Complete'], ['check', '● In progress'], ['save', '○ Not started']]);
    await ctx.timers.fire();
    assert.deepEqual(steps(ctx), [['write', '✓ Complete'], ['check', '✓ Complete'], ['save', '● In progress']]);
    assert.ok(!/%/.test(ctx.text()), 'no made-up percentage');
    assert.equal(ctx.named('runStatus').length, 3);
    // Stop writing: a question in the panel (no window.confirm), the safe choice focused; nothing is sent yet
    await ctx.click('[data-action="cancel-run"]');
    assert.equal(ctx.named('cancelRun').length, 0);
    assert.equal(ctx.$('.studio-confirm-text').textContent, 'Stop writing this lesson? Nothing has been saved yet.');
    assert.equal(ctx.$('.studio-confirm').getAttribute('aria-labelledby'), 'studio-confirm-text');
    assert.equal(ctx.doc.activeElement, ctx.$('[data-action="keep-writing"]'));
    assert.equal(ctx.$('[data-action="cancel-run"]').getAttribute('disabled'), '');
    // Keep writing: the question goes, nothing was sent
    await ctx.click('[data-action="keep-writing"]');
    assert.equal(ctx.$('.studio-confirm'), null);
    assert.equal(ctx.doc.activeElement, ctx.$('[data-action="cancel-run"]'));
    assert.equal(ctx.named('cancelRun').length, 0);
    // asked again: a poll meanwhile keeps the question
    await ctx.click('[data-action="cancel-run"]');
    await ctx.timers.fire();
    assert.equal(ctx.named('runStatus').length, 4);
    assert.ok(ctx.$('.studio-confirm'));
    // confirmed: asked once, then the run is asked about at once
    await ctx.click('[data-action="confirm-cancel-run"]');
    assert.deepEqual(ctx.named('cancelRun'), [['cancelRun', 'run-1']]);
    assert.equal(ctx.named('runStatus').length, 5);
    assert.equal(ctx.$('.studio-confirm'), null);
    assert.deepEqual(steps(ctx).at(-1), ['save', '● Stopping…']);
    assert.equal(ctx.$('[data-action="cancel-run"]').getAttribute('disabled'), '');
    assert.equal(ctx.$('.studio-status').textContent, 'Stopping…');
    await ctx.timers.fire();
    assert.equal(ctx.$('#studio-heading').textContent, 'Writing stopped');
    assert.equal(ctx.$('.studio-status').textContent, '', 'the header no longer says "Stopping…" once writing stopped');
    assert.equal(ctx.$('.studio-run-failed p').textContent, '⚠ You stopped writing this lesson. Nothing was saved; your document or notes are safe.');
    assert.deepEqual(steps(ctx).at(-1), ['save', '⚠ Stopped']);
    assert.equal(ctx.$('[data-action="cancel-run"]'), null);
    assert.equal(ctx.$('[data-action="continue-later"]'), null);
    assert.deepEqual(ctx.timers.delays(), [], 'polling stops when the run ends');
});

test('Progress: a failed run says "Something needs attention" (never the server\'s raw message); Try again sends the same request', async () => {
    const storage = memoryStorage();
    const ctx = await setup({ storage, adapter: {
        runStatus: async () => ({ run_id: 'run-1', status: 'failed', stage: 'Writing the lesson', message: 'openai: 429 quota exceeded for gpt-4o' }) } });
    await ctx.create();
    await ctx.click('[data-action="from-document"]');
    await ctx.click('[data-action="start-lesson"]');
    const failed = ctx.$('.studio-run-failed');
    assert.ok(failed);
    assert.equal(failed.getAttribute('role'), 'alert');
    assert.equal(failed.querySelector('p').textContent, "⚠ We couldn't write the lesson from this content. Your document or notes are safe.");
    assert.equal(ctx.$('#studio-heading').textContent, 'Something needs attention');
    assert.deepEqual(steps(ctx), [['source', '✓ Complete'], ['write', '⚠ Stopped'], ['check', '○ Not started'], ['save', '○ Not started']]);
    assert.ok(failed.querySelector('[data-action="back-home"]'), 'another way: back to the lessons');
    assert.ok(!PROVIDERS.test(ctx.text()));
    assert.deepEqual(ctx.timers.delays(), []);
    assert.equal(storage.map.has('aadhi.studio.run'), false, 'an ended run is forgotten');
    const first = ctx.named('startLesson')[0][1];
    await ctx.click('[data-action="retry-run"]');
    assert.equal(ctx.named('startLesson').length, 2);
    assert.deepEqual(ctx.named('startLesson')[1][1], first);
});

test('Progress: a finished run opens the lesson on the page, then its stage rail', async () => {
    const storage = memoryStorage();
    const replies = [
        { run_id: 'run-1', status: 'running', stage: 'Writing the lesson' },
        { run_id: 'run-1', status: 'completed', stage: 'Saving the lesson', project_id: 21 }
    ];
    const ctx = await setup({ storage, adapter: {
        runStatus: async () => replies.shift(),
        lessonState: async id => sampleState({ project_id: id }) } });
    await ctx.create();
    await ctx.click('[data-action="from-document"]');
    await ctx.click('[data-action="start-lesson"]');
    await ctx.timers.fire();
    assert.deepEqual(ctx.named('openLesson'), [['openLesson', 21]]);
    assert.deepEqual(ctx.named('lessonState'), [['lessonState', 21]]);
    assert.equal(ctx.panel.view, 'lesson');
    assert.equal(ctx.$$('.studio-stage').length, 7);
    assert.equal(storage.map.has('aadhi.studio.run'), false);
});

test('Progress: a run being written is followed again after a reload; one that ended meanwhile opens nothing by itself', async () => {
    const at = Date.now();
    const active = memoryStorage({ 'aadhi.studio.run': JSON.stringify({ run_id: 'run-7', from_source: true, title: 'Plants', at }) });
    const ctx = await setup({ storage: active, adapter: { runStatus: async () => ({ run_id: 'run-7', status: 'running', stage: 'Checking the lesson' }) } });
    assert.equal(ctx.panel.view, 'progress');
    assert.deepEqual(ctx.named('runStatus'), [['runStatus', 'run-7']]);
    assert.deepEqual(steps(ctx), [['source', '✓ Complete'], ['write', '✓ Complete'], ['check', '● In progress'], ['save', '○ Not started']]);
    assert.equal(ctx.$('.studio-progress-title').textContent, 'Plants');

    const ended = memoryStorage({ 'aadhi.studio.run': JSON.stringify({ run_id: 'run-7', from_source: true, title: 'Plants', at }) });
    const later = await setup({ storage: ended, adapter: { runStatus: async () => ({ run_id: 'run-7', status: 'completed', project_id: 40 }) } });
    assert.equal(later.panel.view, 'home');
    assert.equal(later.named('openLesson').length, 0);
    assert.equal(ended.map.has('aadhi.studio.run'), false);

    // Follow from Your lessons (the content is unknown here: no source stage is claimed)
    const list = await setup();
    await list.click('[data-action="follow-run"]');
    assert.equal(list.panel.view, 'progress');
    assert.deepEqual(list.named('runStatus'), [['runStatus', 'run-9']]);
    assert.equal(list.$('[data-step="source"]'), null);
    // a failed run followed after a reload: Try again goes to the ways to start (the content is chosen again)
    const lost = await setup({ adapter: { runStatus: async () => ({ run_id: 'run-9', status: 'failed', stage: 'Writing the lesson' }) } });
    await lost.click('[data-action="follow-run"]');
    await lost.click('[data-action="retry-run"]');
    assert.equal(lost.panel.view, 'create');
    assert.equal(lost.$('.studio-status').textContent, 'Choose the content again to write the lesson once more.');
});

test('Progress: "Continue later" and closing both leave the lesson being written; it is followed again from Your lessons', async () => {
    let failed = false;
    const ctx = await setup({ adapter: {
        listLessons: async () => ({ lessons: [], runs: [{ run_id: 'run-1', status: 'running', stage: 'Writing the lesson' }] }),
        runStatus: async () => (failed ? { run_id: 'run-1', status: 'failed', stage: 'Writing the lesson' } : { run_id: 'run-1', status: 'running', stage: 'Writing the lesson' }) } });
    await ctx.create();
    await ctx.click('[data-action="from-document"]');
    await ctx.click('[data-action="start-lesson"]');
    assert.deepEqual(ctx.timers.delays(), [2000]);
    ctx.panel.close();
    assert.deepEqual(ctx.timers.delays(), [], 'no polling while closed');
    ctx.panel.open();
    await settle();
    assert.equal(ctx.panel.view, 'progress');
    assert.equal(ctx.named('runStatus').length, 2);
    assert.deepEqual(ctx.timers.delays(), [2000]);
    // Continue later: Your lessons, where the lesson being written is listed; nothing was cancelled
    await ctx.click('[data-action="continue-later"]');
    assert.equal(ctx.panel.view, 'home');
    assert.equal(ctx.named('cancelRun').length, 0);
    assert.equal(ctx.$$('.studio-run-item').length, 1);
    assert.equal(ctx.$('.studio-run-item .studio-lesson-title').textContent, 'Photosynthesis', 'its title (remembered by the Studio)');
    // reopened from there: the same run, its request kept for Try again
    failed = true;
    await ctx.click('[data-action="follow-run"]');
    assert.equal(ctx.panel.view, 'progress');
    assert.ok(ctx.$('[data-step="source"]'), 'still known to come from a document');
    await ctx.click('[data-action="retry-run"]');
    assert.equal(ctx.named('startLesson').length, 2);
    assert.deepEqual(ctx.named('startLesson')[1][1], ctx.named('startLesson')[0][1]);
});

test('Busy: while an action runs its buttons are off and the status says so; a second click sends nothing', async () => {
    let finish;
    const ctx = await setup({ adapter: { startLesson: () => new Promise(r => { finish = r; }) } });
    await ctx.create();
    await ctx.click('[data-action="from-document"]');
    ctx.$('[data-action="start-lesson"]').fire('click');
    await settle();
    assert.equal(ctx.$('[data-action="start-lesson"]').getAttribute('disabled'), '');
    assert.equal(ctx.$('[data-action="cancel-form"]').getAttribute('disabled'), '');
    assert.equal(ctx.$('[data-action="close"]').getAttribute('disabled'), null, 'closing is always possible');
    assert.equal(ctx.$('.studio-status').textContent, '⟳ Working…');
    assert.equal(ctx.panel.root.getAttribute('aria-busy'), 'true');
    ctx.$('[data-action="start-lesson"]').fire('click');
    await settle();
    assert.equal(ctx.named('startLesson').length, 1);
    finish({ run_id: 'run-1' });
    await settle();
    assert.equal(ctx.panel.view, 'progress');
    assert.equal(ctx.panel.root.getAttribute('aria-busy'), 'false');
    assert.equal(ctx.$('[data-action="cancel-run"]').getAttribute('disabled'), null);
});

// ---- the lesson: stage rail ------------------------------------------------------------------------------------------

test('Lesson view: seven stages, each icon + words + one truthful summary; the suggested next step is marked and opens first', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    assert.equal(ctx.panel.view, 'lesson', 'the page\'s open lesson opens on its stages');
    assert.deepEqual(railFacts(ctx), [
        { key: 'content', status: 'done', state: 'complete', text: '✓ Complete', summary: 'From your document: 7 scenes · AI-written: 2 · Edited by you: 1' },
        // "Looks right" is the user's optional check: Ready, never "In progress"
        { key: 'lesson', status: 'todo', state: 'ready', text: '→ Ready', summary: '9 scenes · 1 edited by you · 1 hidden' },
        { key: 'visuals', status: 'attention', state: 'attention', text: '⚠ Needs attention', summary: '1 needs attention · 3 of 6 ready · 1 being made' }, // a failed presenter clip is listed, not counted (the lesson plays with its fallback)
        // the style by the name its card shows ("Cinematic Education")
        { key: 'style', status: 'done', state: 'complete', text: '✓ Complete', summary: 'Style: Cinematic Education' },
        // what is counted, said: the scene visuals (Visual Review also lists presenters and layouts)
        { key: 'review', status: 'active', state: 'in-progress', text: '● In progress', summary: '3 visuals to check · Changed since the check' },
        // a state, not the stage's help line again
        { key: 'preview', status: 'todo', state: 'ready', text: '→ Ready', summary: 'Not previewed yet' },
        // a video exists (made before the latest changes): exporting again is ready
        { key: 'export', status: 'todo', state: 'ready', text: '→ Ready', summary: 'Your last video was made before your latest changes' }
    ]);
    assert.deepEqual(ctx.$$('.studio-stage-name').map(n => n.textContent),
        ['Content', 'Lesson', 'Visuals & presenter', 'Style', 'Review & edit', 'Preview', 'Export']);
    assert.deepEqual(ctx.$$('.studio-stage-short').map(n => n.textContent), ['Content', 'Lesson', 'Visuals', 'Style', 'Review', 'Preview', 'Export']);
    // the suggestion: one stage, said in words, and the rail opens on it
    assert.deepEqual(ctx.$$('.studio-stage[data-next="true"]').map(b => b.getAttribute('data-stage')), ['visuals']);
    assert.equal(stageButton(ctx, 'visuals').querySelector('.studio-stage-next').textContent, 'Next step');
    assert.equal(stageButton(ctx, 'visuals').getAttribute('aria-current'), 'step');
    assert.equal(ctx.$$('.studio-stage[aria-current="step"]').length, 1);
    assert.equal(ctx.$('.studio-detail-title').textContent, '3 · Visuals & presenter');
    assert.equal(ctx.$('.studio-detail-status').textContent, '⚠ Needs attention');
    assert.equal(ctx.$('.studio-detail-status').getAttribute('data-state'), 'attention');
    assert.equal(ctx.$('.studio-detail-next').textContent, 'Suggested next step');
    assert.equal(ctx.$('.studio-detail-summary').textContent, '1 needs attention · 3 of 6 ready · 1 being made');
    assert.equal(ctx.$('.studio-detail .studio-help').textContent, 'The pictures, clips and presenter your lesson uses.');
    // every number of the summary is in the counts line (1 to prepare + 3 ready + 1 being made + 1 needing attention = 6)
    assert.deepEqual(ctx.$$('.studio-count').map(n => n.textContent), ['○ 1 to prepare', '✓ 3 ready', '● 1 being made', '⚠ 1 needs attention']);
    assert.deepEqual(ctx.$$('.studio-count').map(n => n.getAttribute('data-count')), ['needed', 'ready', 'generating', 'attention']);
    // internal details stay out
    assert.ok(!ctx.text().includes(FP));
    assert.ok(!ctx.text().includes('2026-10-02 10:00:00.000001'));
    assert.equal(ctx.$('.studio-debug'), null);
    // any stage opens directly: the content stage (the origin said once in the detail; the source facts read-only)
    await ctx.click(stageButton(ctx, 'content'));
    assert.equal(stageButton(ctx, 'content').getAttribute('aria-current'), 'step');
    assert.match(ctx.$('.studio-facts').textContent, /plants\.pdf/);
    assert.equal(ctx.$$('.studio-origin').length, 1);
    assert.equal(ctx.$('.studio-origin').textContent, 'From your document: 7 scenes · AI-written: 2 · Edited by you: 1');
    assert.ok(!/Where they come from/.test(ctx.$('.studio-detail').textContent));
    // from here, one link to the suggested next step (the focus follows)
    const next = ctx.$('[data-action="next-stage"]');
    assert.equal(next.textContent, 'Next step: Visuals & presenter →');
    await ctx.click(next);
    assert.equal(ctx.panel.stage, 'visuals');
    assert.equal(ctx.doc.activeElement, ctx.$('#studio-detail-title'));
    assert.equal(ctx.$('[data-action="next-stage"]'), null, 'not on the suggested stage itself');
    // on a stage after the suggestion, the link never says "Next step" pointing back: it is still to do (same action)
    await ctx.click(stageButton(ctx, 'preview'));
    const back = ctx.$('[data-action="next-stage"]');
    assert.equal(back.textContent, 'Still to do: 3 · Visuals & presenter →');
    assert.ok(back.getAttribute('aria-label').startsWith('Still to do'), back.getAttribute('aria-label'));
    await ctx.click(back);
    assert.equal(ctx.panel.stage, 'visuals');
    assert.equal(ctx.doc.activeElement, ctx.$('#studio-detail-title'));
    // the five stage states keep their names
    assert.deepEqual(Object.values(S.STATE), ['✓ Complete', '● In progress', '→ Ready', '⚠ Needs attention', '○ Not started']);
    noMarkup(ctx.panel.root);
});

test('lessonStages: the rules for every stage (all done, changed since, serious quality, media, style, exports, preview) and the states', () => {
    const done = S.lessonStages(S.normalizeState(doneState()), { previewed: FP });
    assert.deepEqual(done.map(s => [s.key, s.status, s.summary]), [
        ['content', 'done', 'From your document: 7 scenes · AI-written: 2 · Edited by you: 1'],
        ['lesson', 'done', '9 scenes'],
        ['visuals', 'done', 'All 4 pictures and clips ready · nothing left to make'],
        ['style', 'done', 'Style: Cinematic Education'],
        ['review', 'done', 'All 9 visuals checked · Quality looks good'],
        ['preview', 'done', 'You previewed this version'],
        ['export', 'done', 'Your video matches your current lesson']
    ]);
    assert.equal(S.defaultStage(done), 'export');
    done.forEach(s => assert.deepEqual([s.text, s.state, s.next], ['✓ Complete', 'complete', false]));

    const stage = (state, key, opts) => S.lessonStages(S.normalizeState(state), opts).find(s => s.key === key);
    // "Looks right" for another version of the lesson: ready to say again (an optional check is never "In progress")
    const changed = stage({ ...doneState(), checkpoints: { structure: {}, lesson: { fingerprint: 'a'.repeat(64) } } }, 'lesson');
    assert.deepEqual([changed.status, changed.state, changed.text, changed.summary], ['todo', 'ready', '→ Ready', 'Changed since you said it looked right · 9 scenes']);
    assert.deepEqual([stage({ ...doneState(), checkpoints: {} }, 'lesson').status, stage({ ...doneState(), checkpoints: {} }, 'lesson').state], ['todo', 'ready']);
    assert.deepEqual([stage({ ...doneState(), checkpoints: {} }, 'content').status, stage({ ...doneState(), checkpoints: {} }, 'content').state], ['todo', 'ready']);
    assert.equal(stage({ ...doneState(), origin: { source: 4, ai: 0, edited: 0 } }, 'content').summary, 'From your document: 4 scenes', 'no "AI-written: 0 · Edited by you: 0"');
    // no scenes yet: nothing to confirm; the lesson (the editor) is where to go
    const noScenes = S.lessonStages(S.normalizeState({ ...freshState(), scenes: 0, stage: 'draft' }));
    assert.deepEqual(noScenes.filter(s => s.key !== 'visuals').map(s => [s.key, s.state]),
        [['content', 'not-started'], ['lesson', 'ready'], ['style', 'not-started'], ['review', 'not-started'], ['preview', 'not-started'], ['export', 'not-started']]);
    assert.equal(noScenes[0].summary, 'No scenes yet.');
    assert.equal(S.defaultStage(noScenes, 'draft'), 'lesson');
    assert.deepEqual(noScenes.filter(s => s.next).map(s => s.key), ['lesson']);
    // a serious quality finding needs attention; a stale check is not "good"
    const blocked = stage({ ...doneState(), quality: { status: 'blocked', counts: { blocking: 1, warning: 1 }, stale: false } }, 'review');
    assert.deepEqual([blocked.status, blocked.text, blocked.summary], ['attention', '⚠ Needs attention', 'All 9 visuals checked · 2 things to review']);
    assert.equal(stage({ ...doneState(), review: { approved: 0, changed: 0, pending: 1, removed: 0 } }, 'review').summary, '1 visual to check · Quality looks good');
    assert.equal(stage({ ...doneState(), review: { approved: 1, changed: 0, pending: 0, removed: 0 } }, 'review').summary, '1 visual checked · Quality looks good');
    assert.equal(stage({ ...doneState(), quality: { status: 'good', counts: {}, stale: true } }, 'review').status, 'active');
    // a lesson whose video is made but whose quality was never checked: Review is ready (never "Not started")
    const unchecked = stage({ ...doneState(), quality: null, review: {} }, 'review');
    assert.deepEqual([unchecked.status, unchecked.state, unchecked.summary], ['todo', 'ready', 'Quality not checked yet']);
    // media
    const none = stage({ ...doneState(), media: { needed: 0, ready: 0, generating: 0, items: [] } }, 'visuals');
    assert.deepEqual([none.status, none.summary], ['done', 'No pictures or clips to make']);
    assert.equal(stage({ ...doneState(), media: { needed: 0, ready: 1, generating: 0, items: [] } }, 'visuals').summary, '1 picture or clip ready · nothing left to make', 'never "All 1 ready"');
    const todo = stage({ ...doneState(), media: { needed: 5, ready: 0, generating: 0, items: [] } }, 'visuals');
    assert.deepEqual([todo.status, todo.summary], ['todo', '5 to prepare']);
    const part = stage({ ...doneState(), media: { needed: 3, ready: 2, generating: 0, items: [] } }, 'visuals');
    assert.deepEqual([part.status, part.state, part.summary], ['active', 'in-progress', '2 of 5 ready · Prepare the rest']);
    const making = stage({ ...doneState(), media: { needed: 0, ready: 2, generating: 3, items: [] } }, 'visuals');
    assert.deepEqual([making.status, making.summary], ['active', '3 being made · 2 of 5 ready']);
    const counted = stage({ ...doneState(), media: { needed: 1, ready: 2, generating: 0, attention: 1, failed: 1 } }, 'visuals');
    assert.deepEqual([counted.status, counted.summary], ['attention', '2 need attention · 2 of 5 ready']);
    // style: the default style is a fine choice, ready to change
    const plainStyle = stage({ ...doneState(), style: null }, 'style');
    assert.deepEqual([plainStyle.status, plainStyle.state, plainStyle.summary], ['todo', 'ready', 'Using the default style']);
    // the names the style cards show
    assert.equal(stage({ ...doneState(), style: { style: 'corporate_training', version: 1 } }, 'style').summary, 'Style: Corporate Training');
    assert.equal(stage({ ...doneState(), style: { style: 'children_education', version: 1 } }, 'style').summary, "Style: Children's Education");
    // the Classic layout shows no style: never "✓ Complete" with a style the video does not show (ready: Cinematic can be chosen)
    const classic = stage(doneState(), 'style', { previewed: FP, layout: 'classic' });
    assert.deepEqual([classic.status, classic.state, classic.text, classic.summary],
        ['todo', 'ready', '→ Ready', 'Classic layout — video styles need the Cinematic layout']);
    assert.equal(stage({ ...doneState(), style: null }, 'style', { layout: 'classic' }).summary, 'Classic layout — video styles need the Cinematic layout');
    assert.deepEqual([stage(doneState(), 'style', { layout: 'cinematic' }).status, stage(doneState(), 'style', { layout: 'cinematic' }).summary], ['done', 'Style: Cinematic Education']);
    // exports
    const exp = latest => stage({ ...doneState(), exports: { count: 1, latest } }, 'export');
    assert.deepEqual([stage({ ...doneState(), exports: { count: 0, latest: null } }, 'export').status], ['todo']);
    assert.deepEqual([exp({ status: 'RECORDING' }).status, exp({ status: 'RECORDING' }).summary], ['active', 'Your video is being made']);
    assert.deepEqual([exp({ status: 'FAILED' }).status, exp({ status: 'FAILED' }).text], ['attention', '⚠ Needs attention']);
    assert.equal(exp({ status: 'CANCELLED' }).status, 'todo');
    assert.deepEqual([exp({ status: 'COMPLETED', matches_lesson: null }).status, exp({ status: 'COMPLETED' }).summary], ['done', 'Your video is ready']);
    // preview: another version previewed
    assert.deepEqual([stage(doneState(), 'preview', { previewed: 'b'.repeat(64) }).summary, stage(doneState(), 'preview', { previewed: 'b'.repeat(64) }).state],
        ['Changed since you last previewed it', 'ready']);
    assert.equal(stage(doneState(), 'preview', {}).status, 'todo');
});

test('Where a lesson lands follows its own stage (its chip): just written → Visuals; ready to export / video ready → Export; required stages after the suggestion are "Not started"', () => {
    const at = state => {
        const stages = S.lessonStages(S.normalizeState(state));
        return { land: S.defaultStage(stages, state.stage), next: stages.filter(s => s.next).map(s => s.key), states: stages.map(s => s.state) };
    };
    // straight after writing (chip "Ready for review"): never Content; the visuals still to make are next
    const fresh = at(freshState());
    assert.equal(fresh.land, 'visuals');
    assert.deepEqual(fresh.next, ['visuals']);
    assert.deepEqual(fresh.states, ['ready', 'ready', 'ready', 'ready', 'not-started', 'ready', 'not-started']);
    // visuals ready, never checked: Review & edit
    assert.equal(at({ ...freshState(), media: { needed: 0, ready: 5, generating: 0, items: [] } }).land, 'review');
    // being made (chip "Generating visuals"): Visuals
    assert.equal(at({ ...freshState(), media: { needed: 2, ready: 1, generating: 2, items: [] }, stage: 'generating' }).land, 'visuals');
    // chip "Ready to export": Export, ready and suggested
    const ready = at({ ...doneState(), exports: { count: 0, latest: null }, stage: 'ready_to_export' });
    assert.equal(ready.land, 'export');
    assert.deepEqual([ready.next, ready.states[6]], [['export'], 'ready']);
    // chip "Video ready": Export (complete: nothing more suggested), every earlier stage Complete or Ready
    const made = at({ ...freshState(), media: { needed: 0, ready: 5, generating: 0, items: [] },
        exports: { count: 1, latest: { status: 'COMPLETED', completed_at: '2026-10-02T11:00:00', matches_lesson: true } }, stage: 'completed' });
    assert.equal(made.land, 'export');
    assert.deepEqual(made.next, []);
    assert.ok(!made.states.includes('not-started'), made.states.join(' '));
    // something that needs attention comes first; a failed export only when nothing else does
    assert.equal(at(sampleState()).land, 'visuals');
    assert.equal(at({ ...doneState(), exports: { count: 1, latest: { status: 'FAILED' } }, stage: 'review' }).land, 'export');
});

test('Lesson view: on narrow screens the stages are one select ("Stage 3 of 7"), each option with its state; choosing opens that stage', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    const sel = ctx.$('select.studio-stage-select');
    assert.ok(sel);
    assert.ok(ctx.$('.studio-rail .studio-stage-picker select'), 'inside the stage navigation, with its label');
    assert.equal(ctx.$('.studio-stage-picker-label').textContent, 'Stage 3 of 7');
    assert.equal(sel.querySelectorAll('option').length, 7);
    assert.equal(sel.value, 'visuals');
    const option = key => sel.querySelectorAll('option').find(o => o.value === key);
    assert.equal(option('visuals').textContent, '3. Visuals & presenter — ⚠ Needs attention · Next step');
    assert.equal(option('style').textContent, '4. Style — ✓ Complete');
    assert.equal(option('visuals').getAttribute('selected'), '');
    sel.value = 'export';
    sel.fire('change');
    await settle();
    assert.equal(ctx.panel.stage, 'export');
    assert.equal(ctx.$('select.studio-stage-select'), sel, 'the same control (the focus stays on it)');
    assert.equal(ctx.$('.studio-stage-picker-label').textContent, 'Stage 7 of 7');
    assert.equal(ctx.$('.studio-detail').getAttribute('data-stage'), 'export');
    assert.equal(sel.querySelectorAll('option').length, 7);
});

test('Lesson view: the attention list (Scene N … needs attention) calls retryItem / chooseExisting / dismissItem with the right ids', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    const items = ctx.$$('.studio-attention-item');
    assert.equal(items.length, 2, 'only the items that need attention');
    assert.deepEqual(items.map(i => i.querySelector('.studio-attention-text').textContent),
        ['⚠ Scene 3 visual needs attention', '⚠ Scene 5 presenter clip needs attention']);
    assert.deepEqual(items.map(i => i.getAttribute('data-scene-index')), ['2', '4']);
    // a message that names a provider is not shown; a plain one is
    assert.equal(items[0].querySelector('.studio-hint'), null);
    assert.equal(items[1].querySelector('.studio-hint').textContent, 'The picture could not be made.');
    assert.ok(!PROVIDERS.test(ctx.text()));
    assert.ok(!/run-(aaa|bbb|ccc)/.test(ctx.text()), 'run ids are not shown');
    // every control is labelled with its visible words first
    items.forEach(li => li.querySelectorAll('button').forEach(b => assert.ok(b.getAttribute('aria-label').startsWith(b.textContent), b.getAttribute('aria-label'))));
    const states = () => ctx.named('lessonState').length;
    let before = states();
    await ctx.click('[data-item="media-0"] [data-action="retry-item"]');
    // the item's details travel with the action (a failed visual is retried / skipped differently from one that needs attention)
    assert.deepEqual(ctx.named('retryItem'), [['retryItem', 'run-aaa', { sceneIndex: 2, sceneId: 's-3', slot: 'main', status: 'needs_attention' }]]);
    assert.equal(states(), before + 1, 'a refresh after the action');
    before = states();
    await ctx.click('[data-item="media-1"] [data-action="choose-existing"]');
    assert.deepEqual(ctx.named('chooseExisting'), [['chooseExisting', 4]]);
    assert.equal(states(), before + 1);
    // a presenter clip that failed is listed (to retry) but not counted, and has no "Continue without": the lesson already plays
    // with its fallback presenter (fact-check finding: it was counted, and its dismissal always failed on the server)
    assert.equal(ctx.$('[data-item="media-1"] [data-action="dismiss-item"]'), null);
    assert.equal(ctx.$('.studio-count[data-count="attention"]').textContent, '⚠ 1 needs attention');
    await ctx.click('[data-item="media-0"] [data-action="dismiss-item"]');
    assert.deepEqual(ctx.named('dismissItem'), [['dismissItem', 'run-aaa', { sceneIndex: 2, sceneId: 's-3', slot: 'main', status: 'needs_attention' }]]);
    assert.equal(ctx.$('.studio-status').textContent, '✓ The lesson continues without it.');
    // Prepare visuals: the existing batch, its plain message (a quiet confirmation in the status line), a refresh
    before = states();
    await ctx.click('[data-action="prepare-media"]');
    assert.equal(ctx.named('prepareMedia').length, 1);
    assert.equal(ctx.$('.studio-status').textContent, '3 visuals are being made.');
    assert.equal(ctx.$('.studio-status').getAttribute('aria-live'), 'polite');
    assert.equal(states(), before + 1);
});

test('Lesson view: lessonState is asked every ~4 s while media is being made, and no longer once nothing is', async () => {
    const replies = [sampleState(), sampleState({ media: { needed: 0, ready: 6, generating: 0, items: [] } })];
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), lessonState: async () => replies.shift() || sampleState() } });
    assert.deepEqual(ctx.timers.delays(), [4000]);
    await ctx.timers.fire();
    assert.equal(ctx.named('lessonState').length, 2);
    assert.equal(stageButton(ctx, 'visuals').getAttribute('data-status'), 'done');
    assert.equal(ctx.$('.studio-count[data-count="attention"]').textContent, '✓ Nothing needs attention', 'no alarm sign for none');
    assert.deepEqual(ctx.timers.delays(), []);
});

test('Polling: a hidden tab asks nothing (the poll waits), and asks once the tab is shown again', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    assert.deepEqual(ctx.timers.delays(), [4000], 'media being made: lessonState every ~4 s');
    ctx.doc.visibilityState = 'hidden';
    await ctx.timers.fire();
    assert.equal(ctx.named('lessonState').length, 1, 'nothing asked while hidden');
    assert.deepEqual(ctx.timers.delays(), [], 'and nothing more scheduled');
    const listener = ctx.doc.listeners.find(l => l.type === 'visibilitychange');
    listener.fn();
    assert.equal(ctx.named('lessonState').length, 1, 'still hidden: still nothing');
    ctx.doc.visibilityState = 'visible';
    ctx.panel.lastRefresh = Date.now(); // (the poll that waited is asked even right after another refresh)
    listener.fn();
    await settle();
    assert.equal(ctx.named('lessonState').length, 2);
    assert.deepEqual(ctx.timers.delays(), [4000], 'and the polling goes on');
    // the progress of a lesson being written waits the same way
    const run = await setup();
    await run.create();
    await run.click('[data-action="from-document"]');
    await run.click('[data-action="start-lesson"]');
    run.doc.visibilityState = 'hidden';
    await run.timers.fire();
    assert.equal(run.named('runStatus').length, 1);
    run.doc.visibilityState = 'visible';
    run.doc.listeners.find(l => l.type === 'visibilitychange').fn();
    await settle();
    assert.equal(run.named('runStatus').length, 2);
});

test('Lesson view: checkpoints (Confirm structure, Looks right) and the editor / preview step the Studio aside', async () => {
    const storage = memoryStorage();
    const ctx = await setup({ storage, adapter: { currentLesson: () => ({ projectId: 12 }) } });
    await ctx.click(stageButton(ctx, 'content'));
    assert.equal(ctx.$('[data-action="confirm-structure"]').textContent, 'Confirm again', 'already confirmed: not the primary action');
    assert.ok(!ctx.$('[data-action="confirm-structure"]').classList.contains('studio-btn-primary'));
    await ctx.click('[data-action="confirm-structure"]');
    assert.deepEqual(ctx.named('checkpoint'), [['checkpoint', 'structure', { scenes: 9 }]]);
    assert.equal(ctx.$('.studio-status').textContent, '✓ Structure confirmed.');
    await ctx.click(stageButton(ctx, 'lesson'));
    assert.equal(ctx.$('.studio-status').textContent, '', 'a confirmation is said once, not carried to the next stage');
    assert.ok(ctx.$('[data-action="open-editor"]').classList.contains('studio-btn-primary'));
    assert.equal(ctx.$('[data-action="open-editor"]').textContent, 'Open the editor');
    await ctx.click('[data-action="confirm-lesson"]');
    assert.deepEqual(ctx.named('checkpoint')[1], ['checkpoint', 'lesson', { scenes: 9, fingerprint: FP }]);
    assert.equal(ctx.$('.studio-status').textContent, '✓ Marked as looking right.');
    // the editor needs the live stage: the Studio closes itself first (the page is not told the user closed it)
    await ctx.click('[data-action="open-editor"]');
    assert.equal(ctx.named('openEditor').length, 1);
    assert.equal(ctx.panel.isOpen, false);
    assert.equal(ctx.named('close').length, 0);
    assert.equal(ctx.doc.body.querySelector('.studio-root'), null);
    // opened again: back on the same lesson and stage
    ctx.panel.open();
    await settle();
    assert.equal(ctx.panel.view, 'lesson');
    assert.equal(stageButton(ctx, 'lesson').getAttribute('aria-current'), 'step');
    // the preview: steps aside too, and the version previewed is remembered
    await ctx.click(stageButton(ctx, 'preview'));
    assert.equal(ctx.$('[data-action="preview"]').textContent, 'Preview the lesson');
    await ctx.click('[data-action="preview"]');
    assert.equal(ctx.named('preview').length, 1);
    assert.equal(ctx.panel.isOpen, false);
    ctx.panel.open();
    await settle();
    assert.deepEqual([stageButton(ctx, 'preview').getAttribute('data-status'), stageButton(ctx, 'preview').querySelector('.studio-stage-summary').textContent],
        ['done', 'You previewed this version']);
    assert.equal(ctx.$('[data-action="preview"]').textContent, 'Preview again');
});

test('Lesson view: Review & edit — Visual Review and Quality each with one line of help; "N things to review", changed since the check; runQuality kept as a checkpoint', async () => {
    let checked = false;
    const ctx = await setup({ adapter: {
        currentLesson: () => ({ projectId: 12 }),
        lessonState: async () => (checked ? sampleState({ quality: { status: 'good', counts: { info: 0 }, stale: false } }) : sampleState()),
        runQuality: async () => { checked = true; return { status: 'good', counts: { info: 0, notice: 0, warning: 0, error: 0, blocking: 0 } }; } } });
    await ctx.click(stageButton(ctx, 'review'));
    assert.equal(ctx.$('.studio-detail-title').textContent, '5 · Review & edit');
    assert.deepEqual(ctx.$$('.studio-tool-title').map(n => n.textContent), ['Visual Review', 'Quality']);
    assert.equal(ctx.$('[data-tool="review"] .studio-help').textContent, 'Review each visual before you export.');
    assert.equal(ctx.$('[data-tool="quality"] .studio-help').textContent, 'Checks your lesson for readability and consistency.');
    assert.equal(ctx.$('.studio-quality').textContent, '⚠ 3 things to review');
    assert.equal(ctx.$('.studio-quality-stale').textContent, '◌ Changed since the check');
    // what it counts, said (the server's review counts are the scene visuals; Visual Review's own total also has a presenter
    // and a layout item per scene)
    assert.equal(ctx.$('.studio-review-counts').textContent, 'Scene visuals: 5 approved · 1 changed · 3 to check');
    assert.equal(ctx.$('.studio-review-scope').textContent, 'Presenters and layouts are checked in Visual Review too.');
    assert.equal(stageButton(ctx, 'review').querySelector('.studio-stage-summary').textContent, '3 visuals to check · Changed since the check');
    // one primary action: visuals still to review come first
    assert.deepEqual(ctx.$$('.studio-detail .studio-btn-primary').map(b => b.getAttribute('data-action')), ['open-review']);
    assert.equal(ctx.$('[data-action="open-review"]').textContent, 'Open Visual Review');
    await ctx.click('[data-action="run-quality"]');
    assert.equal(ctx.named('runQuality').length, 1);
    assert.deepEqual(ctx.named('checkpoint'), [['checkpoint', 'quality', { status: 'good', counts: { info: 0, notice: 0, warning: 0, error: 0, blocking: 0 }, fingerprint: FP }]]);
    assert.equal(ctx.$('.studio-status').textContent, '✓ Quality looks good');
    assert.equal(ctx.$('.studio-quality').textContent, '✓ Quality looks good');
    assert.equal(ctx.$('.studio-quality-stale'), null);
    // Visual Review and the editor from here
    await ctx.click('[data-action="open-review"]');
    assert.equal(ctx.named('openReview').length, 1);
    assert.equal(ctx.panel.isOpen, true, 'Visual Review opens above the Studio');
    assert.ok(ctx.$('.studio-detail [data-action="open-editor"]'));
    // a result the page already kept is not written twice; nothing left to review: the check is the primary action
    const again = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), lessonState: async () => sampleState({ review: { approved: 9, changed: 0, pending: 0, removed: 0 } }),
        runQuality: async () => ({ status: 'review', counts: { warning: 1 }, recorded: true }) } });
    await again.click(stageButton(again, 'review'));
    assert.deepEqual(again.$$('.studio-detail .studio-btn-primary').map(b => b.getAttribute('data-action')), ['run-quality']);
    await again.click('[data-action="run-quality"]');
    assert.equal(again.named('checkpoint').length, 0);
    assert.equal(again.$('.studio-status').textContent, '⚠ 1 thing to review');
});

test('Lesson view: Export says whether the latest video matches the lesson; one "Export video" (Your videos only when the page has a separate list)', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    await ctx.click(stageButton(ctx, 'export'));
    assert.equal(ctx.$('.studio-export-latest').textContent, 'Latest video: ✓ Made 2 Oct 2026, 09:00');
    assert.equal(ctx.$('.studio-export-match').textContent, '⚠ Made before your latest changes');
    assert.equal(ctx.$('.studio-export-count').textContent, '2 exports of this lesson so far.');
    // exportVideo and openVideos open the same panel on this page: one button, no duplicate
    assert.equal(ctx.$('[data-action="open-videos"]'), null);
    assert.equal(ctx.$('[data-action="export-video"]').textContent, 'Export video');
    assert.ok(ctx.$('[data-action="export-video"]').classList.contains('studio-btn-primary'));
    await ctx.click('[data-action="export-video"]');
    assert.equal(ctx.named('exportVideo').length, 1);
    // a page whose videos are a list of their own (adapter.separateVideos)
    const separate = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), separateVideos: true } });
    await separate.click(stageButton(separate, 'export'));
    assert.equal(separate.$('[data-action="open-videos"]').textContent, 'Your videos (2)');
    assert.ok(!separate.$('[data-action="open-videos"]').classList.contains('studio-btn-primary'));
    assert.equal(separate.$('.studio-export-count'), null, 'the count is on the button');
    await separate.click('[data-action="open-videos"]');
    assert.equal(separate.named('openVideos').length, 1);

    const matching = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), lessonState: async () => doneState() } });
    assert.equal(matching.panel.stage, 'export', 'everything done: the rail opens on Export');
    assert.equal(matching.$('.studio-export-match').textContent, '✓ Matches your current lesson');
    assert.equal(matching.$('[data-action="export-video"]').textContent, 'Export again');
    assert.equal(matching.$$('.studio-detail .studio-btn-primary').length, 0, 'nothing left to do here');
    assert.equal(S.matchWords(null), null);
    assert.equal(S.exportWords(null), 'No video yet');
});

test('Settings: mountSettings places the presenter / style panels in the open stage, keeps them across refreshes, unmounts on change and close', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    const mounts = () => ctx.named('mountSettings');
    const unmounts = () => ctx.named('unmountSettings').map(c => c[1]);
    // the rail opened on Visuals & presenter: the presenter settings are in
    assert.equal(mounts().length, 1);
    assert.equal(mounts()[0][1], 'presenter');
    const container = mounts()[0][2];
    assert.ok(container.classList.contains('studio-settings-mount'));
    assert.equal(ctx.$('[data-settings="presenter"] .studio-settings-mount'), container);
    assert.equal(ctx.$('[data-settings="presenter"] h4').textContent, 'Presenter');
    // a refresh (and a poll) keeps the same panel in place
    await ctx.panel.refresh();
    await ctx.timers.fire();
    assert.equal(mounts().length, 1);
    assert.equal(ctx.$('[data-settings="presenter"] .studio-settings-mount'), container);
    assert.ok(container.querySelector('form'), 'the page\'s panel is still there');
    // another stage: the presenter goes back, the style comes in (with its one line of help)
    await ctx.click(stageButton(ctx, 'style'));
    assert.deepEqual(unmounts(), ['presenter']);
    assert.equal(mounts()[1][1], 'style');
    assert.equal(ctx.$('[data-settings="presenter"]'), null);
    assert.ok(ctx.$('[data-settings="style"] .studio-settings-mount'));
    assert.equal(ctx.$('.studio-style-note').textContent, 'Changes how the lesson looks without making its pictures or clips again.');
    // a stage without settings
    await ctx.click(stageButton(ctx, 'content'));
    assert.deepEqual(unmounts(), ['presenter', 'style']);
    assert.equal(ctx.$('.studio-settings'), null);
    // back, then closed: given back to the page
    await ctx.click(stageButton(ctx, 'visuals'));
    assert.equal(mounts().length, 3);
    ctx.panel.close();
    assert.deepEqual(unmounts(), ['presenter', 'style', 'presenter']);
    // and a view change unmounts too
    const home = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    await home.click('.studio-top [data-action="home"]');
    assert.deepEqual(home.named('unmountSettings').map(c => c[1]), ['presenter']);
});

test('Settings: a panel that cannot be mounted says so in plain words', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), mountSettings: () => { throw new Error('TypeError: x is undefined'); } } });
    assert.match(ctx.$('[data-settings="presenter"] .studio-settings-mount').textContent, /presenter settings couldn't be shown here/);
    assert.ok(!/TypeError|undefined/.test(ctx.text()));
    // the page has no such panel (mountSettings answers false): the same plain note
    const missing = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), mountSettings: () => false } });
    await missing.click(stageButton(missing, 'style'));
    assert.match(missing.$('[data-settings="style"] .studio-settings-mount').textContent, /lesson style settings couldn't be shown here/);
});

test('Style: a style changed in the Style stage is confirmed once, quietly, in the status line', async () => {
    let style = 'cinematic_education';
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), lessonState: async () => sampleState({ style: style ? { style, version: 2 } : null }) } });
    await ctx.click(stageButton(ctx, 'style'));
    assert.equal(ctx.$('.studio-status').textContent, '');
    style = 'academic';
    await ctx.panel.refresh(); // (the page refreshes the Studio when its style panel saved a change)
    assert.equal(ctx.$('.studio-status').textContent, '✓ Style updated: Academic.');
    assert.equal(ctx.$('.studio-status').getAttribute('role'), 'status');
    assert.equal(stageButton(ctx, 'style').querySelector('.studio-stage-summary').textContent, 'Style: Academic');
    // nothing changed: nothing said again
    await ctx.click(stageButton(ctx, 'content'));
    await ctx.panel.refresh();
    assert.equal(ctx.$('.studio-status').textContent, '');
    style = null;
    await ctx.panel.refresh();
    assert.equal(ctx.$('.studio-status').textContent, '✓ Using the default style.');
});

test('Style: in the Classic layout the Style stage says a video style needs the Cinematic layout (adapter.layout, else the page\'s cinematic settings); a Layout change in its panel redraws the stages', async () => {
    const summary = ctx => stageButton(ctx, 'style').querySelector('.studio-stage-summary').textContent;
    const CLASSIC = 'Classic layout — video styles need the Cinematic layout';
    // the page says its layout
    let layout = 'classic';
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), layout: () => layout } });
    assert.deepEqual([stageButton(ctx, 'style').getAttribute('data-state'), summary(ctx)], ['ready', CLASSIC]);
    assert.equal(stageButton(ctx, 'style').querySelector('.studio-stage-status').textContent, '→ Ready');
    await ctx.click(stageButton(ctx, 'style'));
    assert.equal(ctx.$('.studio-detail-summary').textContent, CLASSIC);
    // Cinematic chosen in the style panel the stage hosts (its change event): the stages follow, the panel stays
    const mounted = ctx.$('[data-settings="style"]');
    layout = 'cinematic';
    ctx.$('.studio-settings-slot').fire('change');
    assert.deepEqual([stageButton(ctx, 'style').getAttribute('data-state'), summary(ctx)], ['complete', 'Style: Cinematic Education']);
    assert.equal(ctx.$('[data-settings="style"]'), mounted, 'the page\'s panel is not rebuilt');
    assert.equal(ctx.named('mountSettings').length, 2);
    const title = ctx.$('.studio-detail-title');
    const states = ctx.named('lessonState').length;
    ctx.$('.studio-settings-slot').fire('change'); // another choice (the layout unchanged): nothing redrawn, nothing asked
    assert.equal(ctx.$('.studio-detail-title'), title);
    assert.equal(ctx.named('lessonState').length, states);
    // without adapter.layout: the page's own cinematic settings (window.cinematicSettings)
    const page = await setup({ open: false, adapter: { currentLesson: () => ({ projectId: 12 }) } });
    page.doc.defaultView.cinematicSettings = { settings: { mode: 'classic', style: 'cinematic_education' } };
    page.panel.open();
    await settle();
    assert.equal(summary(page), CLASSIC);
    page.doc.defaultView.cinematicSettings.settings.mode = 'cinematic';
    await page.panel.refresh();
    assert.equal(summary(page), 'Style: Cinematic Education');
    // a page that does not say: as before (the lesson's style)
    const unknown = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), layout: 'sideways' } });
    assert.equal(summary(unknown), 'Style: Cinematic Education');
});

test('Content: "Made from" names what the lesson came from when the Studio knows it (its document, your text, a lesson file opened here), else says it is not recorded', async () => {
    const madeFrom = async (state, over = {}) => {
        const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), lessonState: async () => state, ...over } });
        await ctx.click(stageButton(ctx, 'content'));
        const facts = ctx.$$('.studio-facts dt').map((dt, i) => [dt.textContent, ctx.$$('.studio-facts dd')[i].textContent]);
        return facts[0];
    };
    assert.deepEqual(await madeFrom(sampleState()), ['Made from', 'plants.pdf']);
    assert.deepEqual(await madeFrom(sampleState({ source: { document_id: 3, analysis_id: 4, file_name: null } })), ['Made from', 'Your document']);
    assert.deepEqual(await madeFrom(sampleState({ source: null })), ['Made from', 'Your text']);
    assert.deepEqual(await madeFrom(sampleState({ source: null, origin: { source: 0, ai: 0, edited: 0 } })), ['Made from', 'Not recorded']);
    assert.deepEqual(await madeFrom(sampleState({ source: null, origin: null })), ['Made from', 'Not recorded']);
    // a lesson file opened from the Studio (even one whose scenes carry their text's traces)
    const file = await setup({ adapter: { openLessonFile: async () => ({ projectId: 30 }), lessonState: async () => sampleState({ project_id: 30, source: null }) } });
    await file.create();
    await file.click('[data-action="open-file"]');
    await file.click(stageButton(file, 'content'));
    assert.equal(file.$('.studio-facts dd').textContent, 'A lesson file');
    assert.ok(!/Notes you pasted or a lesson file/.test(file.text()));
});

test('Visuals: "Choose existing" says it opens Visual Review on that scene; its failure names Visual Review', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), chooseExisting: async () => { throw new Error(''); } } });
    const choose = ctx.$('[data-item="media-0"] [data-action="choose-existing"]');
    assert.equal(choose.textContent, 'Choose existing');
    assert.equal(choose.getAttribute('title'), 'Choose a picture or clip for this scene in Visual Review');
    await ctx.click(choose);
    assert.equal(ctx.$('.studio-error-text').textContent, "⚠ We couldn't open Visual Review.");
    assert.ok(!/library/i.test(ctx.$('.studio-error').textContent));
});

// ---- failures, debug, keyboard ----------------------------------------------------------------------------------------

test('Failures: what happened, whether the work is safe, Try again — one plain sentence each; Try again asks again', async () => {
    let fail = true;
    const ctx = await setup({ adapter: { listLessons: async () => { if (fail) throw new Error('Traceback (most recent call last):\n  File "server.py"'); return lessonsReply(); } } });
    const box = ctx.$('.studio-error');
    assert.equal(box.getAttribute('role'), 'alert');
    assert.equal(box.querySelector('p').textContent, "⚠ We couldn't load your lessons.");
    assert.equal(ctx.$('.studio-error-safe').textContent, 'Your lessons are not affected.');
    assert.ok(!/Traceback|server\.py/.test(ctx.text()));
    assert.equal(ctx.$('.studio-empty'), null, 'a list that failed to load is never called empty');
    assert.equal(ctx.$$('[data-action="create"]').length, 1, 'creating stays possible');
    fail = false;
    await ctx.click('.studio-error [data-action="retry"]');
    assert.equal(ctx.named('listLessons').length, 2);
    assert.equal(ctx.$('.studio-error'), null);
    assert.equal(ctx.$$('.studio-lesson-item[data-project-id]').length, 3);

    // a plain reason is kept; a network failure is said plainly
    const plainReason = await setup({ adapter: { startLesson: async () => { throw new Error('The document is still being analysed. Try again in a moment'); } } });
    await plainReason.create();
    await plainReason.click('[data-action="from-document"]');
    await plainReason.click('[data-action="start-lesson"]');
    assert.equal(plainReason.$('.studio-error p').textContent,
        "⚠ We couldn't start writing the lesson. The document is still being analysed. Try again in a moment.");
    assert.equal(plainReason.$('.studio-error-safe').textContent, 'What you entered is still here.');
    assert.equal(plainReason.panel.view, 'name', 'the form stays, with what was typed');
    await plainReason.click('.studio-error [data-action="retry"]');
    assert.equal(plainReason.named('startLesson').length, 2);

    const lessonFails = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), lessonState: async () => { throw new TypeError('Failed to fetch'); } } });
    assert.equal(lessonFails.$('.studio-error p').textContent, "⚠ We couldn't load this lesson's progress. The server could not be reached. Check the connection.");
    assert.equal(lessonFails.$('.studio-error-safe').textContent, 'Your lesson is not affected.');
    assert.ok(lessonFails.$('.studio-error [data-action="retry"]'));

    // words that already say the work is safe are not repeated
    const saved = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), checkpoint: async () => { throw new Error('The server could not be reached. Your work is saved; try again in a moment.'); } } });
    await saved.click(stageButton(saved, 'lesson'));
    await saved.click('[data-action="confirm-lesson"]');
    assert.equal(saved.$('.studio-error p').textContent, "⚠ We couldn't save that step. The server could not be reached. Your work is saved; try again in a moment.");
    assert.equal(saved.$('.studio-error-safe'), null);
});

test('No provider or model names, run ids or fingerprints unless ?visualDebug / adapter.debug', async () => {
    const rejecting = { startLesson: async () => { throw new Error('OpenAI API key invalid for gpt-4o-mini (401)'); } };
    const ctx = await setup({ adapter: rejecting });
    await ctx.create();
    await ctx.click('[data-action="from-document"]');
    await ctx.click('[data-action="start-lesson"]');
    assert.equal(ctx.$('.studio-error p').textContent, "⚠ We couldn't start writing the lesson.");
    assert.ok(!PROVIDERS.test(ctx.text()));
    assert.equal(ctx.$('.studio-debug'), null);

    // debug through the page URL
    const dbg = await setup({ adapter: rejecting, search: '?visualDebug=1' });
    assert.equal(dbg.panel.debug, true);
    await dbg.create();
    await dbg.click('[data-action="from-document"]');
    await dbg.click('[data-action="start-lesson"]');
    assert.match(dbg.$('.studio-error .studio-debug').textContent, /OpenAI API key invalid/);
    // debug through the adapter: run ids, revisions, fingerprints and raw item messages appear
    const lesson = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), debug: () => true } });
    assert.match(lesson.text(), new RegExp(FP));
    assert.match(lesson.text(), /run run-aaa/);
    assert.match(lesson.text(), /Pollinations returned 402/);
    // the pure helper
    assert.equal(S.plainDetail('gemini-2.5-flash timed out'), '');
    assert.equal(S.plainDetail('Pollinations answered 402'), '');
    assert.equal(S.plainDetail('{"detail": "x"}'), '');
    assert.equal(S.plainDetail('The lesson changed elsewhere'), 'The lesson changed elsewhere.');
    // browser check finding: a provider's own detail in brackets, whatever the provider is called, is never shown
    assert.equal(S.plainDetail('The picture could not be made (fake: stand-in failure: rejected)'), 'The picture could not be made.');
    assert.equal(S.plainDetail('The picture could not be made (acme-images: quota exceeded).'), 'The picture could not be made.');
    assert.equal(S.plainDetail('Two scenes (the second and third) need a picture.'), 'Two scenes (the second and third) need a picture.');
    assert.equal(S.plainError("We couldn't save that step.", new Error('Error: at Object.<anonymous> (studio.js:1:1)')), "We couldn't save that step.");
});

test('Esc closes the Studio (and tells the page), unless typing or a panel above it is open; the focus goes back to what opened it', async () => {
    const ctx = await setup({ open: false });
    const { doc, panel } = ctx;
    const opener = doc.createElement('button');
    doc.body.appendChild(opener);
    opener.focus();
    panel.open();
    await settle();
    assert.equal(doc.activeElement, ctx.$('#studio-heading'));
    const keys = doc.listeners.filter(l => l.type === 'keydown');
    assert.equal(keys.length, 1);
    assert.equal(keys[0].capture, true);
    // typing in a field keeps the key
    await ctx.create();
    await ctx.click('[data-action="from-text"]');
    press(doc, 'Escape', ctx.$('[data-field="text"]'));
    assert.equal(panel.isOpen, true);
    // a panel above the Studio closes first
    const review = doc.createElement('div');
    review.className = 'asset-overlay review-overlay open';
    doc.body.appendChild(review);
    press(doc, 'Escape');
    assert.equal(panel.isOpen, true);
    review.remove();
    const editor = doc.createElement('div');
    editor.className = 'editor-root';
    doc.body.appendChild(editor);
    press(doc, 'Escape');
    assert.equal(panel.isOpen, true);
    editor.remove();
    // other keys do nothing
    press(doc, 'a');
    assert.equal(panel.isOpen, true);
    const ev = press(doc, 'Escape');
    assert.equal(panel.isOpen, false);
    assert.equal(ev.prevented, true);
    assert.equal(ctx.named('close').length, 1);
    assert.equal(doc.body.querySelector('.studio-root'), null);
    assert.equal(doc.listeners.filter(l => l.type === 'keydown').length, 0, 'the key listener is removed');
    assert.deepEqual(ctx.timers.delays(), [], 'no poll keeps running');
    await settle();
    assert.equal(doc.activeElement, opener, 'the focus is back on what opened the Studio');
    // the close button does the same
    panel.open();
    await settle();
    await ctx.click('[data-action="close"]');
    assert.equal(panel.isOpen, false);
    assert.equal(ctx.named('close').length, 2);
    await settle();
    assert.equal(doc.activeElement, opener);
    // a panel the page opens as the Studio closes (the export) keeps the focus
    const exporting = await setup({ open: false, adapter: { close: () => {
        const panelAbove = exporting.doc.createElement('div');
        panelAbove.className = 'export-overlay open';
        exporting.doc.body.appendChild(panelAbove);
    } } });
    const button = exporting.doc.createElement('button');
    exporting.doc.body.appendChild(button);
    button.focus();
    exporting.panel.open();
    await settle();
    press(exporting.doc, 'Escape');
    await settle();
    assert.notEqual(exporting.doc.activeElement, button);
});

test('Keyboard: the Studio is a modal dialog — Tab and Shift+Tab go round inside it while no panel is open above it', async () => {
    const ctx = await setup();
    const { doc } = ctx;
    const items = ctx.panel.focusables();
    const first = items[0];
    const last = items[items.length - 1];
    assert.equal(first.getAttribute('data-action'), 'close', 'Home: the hidden "Your lessons" button is skipped');
    assert.ok(!items.includes(ctx.$('#studio-heading')), 'the heading takes the focus only when the view changes');
    assert.equal(last.getAttribute('data-project-id'), '5');
    last.focus();
    let ev = press(doc, 'Tab', last);
    assert.equal(ev.prevented, true);
    assert.equal(doc.activeElement, first);
    ev = press(doc, 'Tab', first, { shiftKey: true });
    assert.equal(ev.prevented, true);
    assert.equal(doc.activeElement, last);
    // in between: the browser moves the focus
    items[1].focus();
    ev = press(doc, 'Tab', items[1]);
    assert.equal(ev.prevented, false);
    assert.equal(doc.activeElement, items[1]);
    // the focus fell to the page behind: Tab brings it back in
    doc.activeElement = doc.body;
    ev = press(doc, 'Tab', doc.body);
    assert.equal(doc.activeElement, first);
    // disabled buttons are skipped
    ctx.panel.busy = 'x';
    ctx.panel.applyBusy();
    assert.ok(!ctx.panel.focusables().includes(last));
    ctx.panel.busy = null;
    ctx.panel.applyBusy();
    // a panel above the Studio has its own Tab
    const above = doc.createElement('div');
    above.className = 'review-overlay open';
    doc.body.appendChild(above);
    last.focus();
    ev = press(doc, 'Tab', last);
    assert.equal(ev.prevented, false);
    above.remove();
});

test('Keyboard: a panel the Studio opened above itself (Visual Review) gives the focus back to its button when it closes', async () => {
    const ref = {};
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }), openReview: () => {
        ref.overlay = ref.doc.createElement('div');
        ref.overlay.className = 'review-overlay open';
        ref.doc.body.appendChild(ref.overlay);
        ref.doc.activeElement = ref.overlay;
    } } });
    ref.doc = ctx.doc;
    await ctx.click(stageButton(ctx, 'review'));
    ctx.$('[data-action="open-review"]').focus();
    await ctx.click('[data-action="open-review"]');
    assert.equal(ctx.doc.activeElement, ref.overlay, 'Visual Review has the focus while it is open');
    // closed, leaving the focus nowhere; the page refreshes the Studio
    ref.overlay.remove();
    ctx.doc.activeElement = ctx.doc.body;
    await ctx.panel.refresh();
    assert.equal(ctx.doc.activeElement, ctx.$('[data-action="open-review"]'));
    // only once: a later refresh moves nothing
    ctx.doc.activeElement = ctx.doc.body;
    await ctx.panel.refresh();
    assert.equal(ctx.doc.activeElement, ctx.doc.body);
});

test('Accessibility: a labelled dialog, headings that take the focus on a view change, labelled buttons, status as words', async () => {
    const ctx = await setup({ adapter: { currentLesson: () => ({ projectId: 12 }) } });
    assert.equal(ctx.$('.studio-panel').getAttribute('aria-modal'), 'true');
    assert.equal(ctx.$('.studio-panel').getAttribute('aria-labelledby'), 'studio-heading');
    assert.equal(ctx.doc.activeElement, ctx.$('#studio-heading'));
    assert.equal(ctx.$('#studio-heading').getAttribute('tabindex'), '-1');
    assert.equal(ctx.$('[data-action="close"]').getAttribute('aria-label'), 'Close the Studio');
    assert.equal(ctx.$('[data-action="close"]').getAttribute('title'), 'Close the Studio (Esc)');
    assert.equal(ctx.$('.studio-top [data-action="home"]').getAttribute('aria-label'), 'Back to your lessons');
    assert.equal(ctx.$('.studio-status').getAttribute('role'), 'status');
    assert.equal(ctx.$('.studio-rail').getAttribute('aria-label'), 'Lesson stages');
    assert.equal(ctx.$('.studio-detail').getAttribute('aria-labelledby'), 'studio-detail-title');
    assert.match(stageButton(ctx, 'visuals').getAttribute('aria-label'), /^3 Visuals & presenter: Needs attention\. 1 needs attention.* Suggested next step\.$/);
    assert.match(stageButton(ctx, 'lesson').getAttribute('aria-label'), /^2 Lesson: Ready\. 9 scenes/);
    ctx.$$('button').forEach(b => assert.ok(b.textContent.trim() || b.getAttribute('aria-label'), 'every button has a name'));
    // every status is an icon and words
    ctx.$$('.studio-stage-status').forEach(n => assert.match(n.textContent, /^[✓●→⚠○] (Complete|In progress|Ready|Needs attention|Not started)$/));
    await ctx.click('.studio-top [data-action="home"]');
    assert.equal(ctx.doc.activeElement.textContent, 'Your lessons');
});

test('Visibility: the Studio refreshes when the tab is visible again', async () => {
    const ctx = await setup();
    const listener = ctx.doc.listeners.find(l => l.type === 'visibilitychange');
    assert.ok(listener);
    ctx.panel.lastRefresh = 0;
    ctx.doc.visibilityState = 'hidden';
    listener.fn();
    assert.equal(ctx.named('listLessons').length, 1, 'not while hidden');
    ctx.doc.visibilityState = 'visible';
    listener.fn();
    await settle();
    assert.equal(ctx.named('listLessons').length, 2);
});

test('The page\'s lesson changed while the Studio was closed: it opens on that lesson, or Home when none is open', async () => {
    let current = { projectId: 12 };
    const ctx = await setup({ adapter: { currentLesson: () => current, lessonState: async id => sampleState({ project_id: id }) } });
    assert.deepEqual(ctx.named('lessonState').at(-1), ['lessonState', 12]);
    ctx.panel.close();
    current = { projectId: 14 };
    ctx.panel.open();
    await settle();
    assert.equal(ctx.panel.view, 'lesson');
    assert.deepEqual(ctx.named('lessonState').at(-1), ['lessonState', 14]);
    ctx.panel.close();
    current = null;
    ctx.panel.open();
    await settle();
    assert.equal(ctx.panel.view, 'home');
});

test('Storage: per user when the page names its user (only a hash in the key; only the run id, title, source flag and time kept); nothing while signed out', async () => {
    let user = 'teacher@example.com';
    const storage = memoryStorage({ 'aadhi.studio.run': JSON.stringify({ run_id: 'old', title: 'Someone else\'s lesson', at: Date.now() }) });
    const ctx = await setup({ storage, adapter: { userKey: () => user } });
    assert.equal(storage.map.has('aadhi.studio.run'), false, 'what the shared key held is shown to nobody');
    assert.equal(ctx.panel.view, 'home');
    assert.equal(ctx.named('runStatus').length, 0);
    await ctx.create();
    await ctx.click('[data-action="from-text"]');
    ctx.type('[data-field="text"]', '# Plant cells\nSecret notes about walls.');
    await ctx.click('[data-action="start-lesson"]');
    const keys = [...storage.map.keys()].filter(k => k.startsWith('aadhi.studio.run'));
    assert.equal(keys.length, 1);
    assert.match(keys[0], /^aadhi\.studio\.run\.u[0-9a-f]{8}$/);
    assert.ok(!keys[0].includes('teacher'), 'the user id never appears');
    const kept = JSON.parse(storage.map.get(keys[0]));
    assert.deepEqual(Object.keys(kept).sort(), ['at', 'from_source', 'run_id', 'title']);
    assert.deepEqual([kept.run_id, kept.title, kept.from_source], ['run-1', 'Plant cells', false]);
    assert.ok(!storage.map.get(keys[0]).includes('Secret'), 'never the content');
    // the same user after a reload: followed again
    const back = await setup({ storage, adapter: { userKey: () => user } });
    assert.equal(back.panel.view, 'progress');
    assert.equal(back.$('.studio-progress-title').textContent, 'Plant cells');
    // another user in the same browser: nothing of it
    user = 'other@example.com';
    const other = await setup({ storage, adapter: { userKey: () => user } });
    assert.equal(other.panel.view, 'home');
    assert.equal(other.named('runStatus').length, 0);
    assert.ok(!other.text().includes('Plant cells'));
    // signed out: nothing read, nothing kept
    user = null;
    const out = await setup({ storage, adapter: { userKey: () => user } });
    assert.equal(out.panel.view, 'home');
    const before = storage.map.size;
    await out.create();
    await out.click('[data-action="from-document"]');
    await out.click('[data-action="start-lesson"]');
    assert.equal(storage.map.size, before);
});

test('helpers: names from a title (once), first line of pasted text, run steps, chips, quality words', () => {
    assert.deepEqual(S.prefillNames('Biology – Photosynthesis.pdf'),
        { subject_name: 'Biology', unit_name: '', session_number: 'Session 1', session_title: 'Photosynthesis' });
    assert.deepEqual(S.prefillNames('Plant cells'),
        { subject_name: '', unit_name: '', session_number: 'Session 1', session_title: 'Plant cells' });
    assert.deepEqual(S.prefillNames('Forces Around Us: Forces around us'),
        { subject_name: '', unit_name: '', session_number: 'Session 1', session_title: 'Forces around us' }, 'the same words are never both');
    assert.equal(S.firstLine('\n\n## Light reactions\nText'), 'Light reactions');
    assert.deepEqual(S.runSteps({ status: 'completed', stage: 'Saving the lesson' }, true).map(s => s.text), ['✓ Complete', '✓ Complete', '✓ Complete', '✓ Complete']);
    assert.deepEqual(S.runSteps({ status: 'recovering', stage: 'Writing the lesson' }, false).map(s => s.text), ['● Picking up again', '○ Not started', '○ Not started']);
    assert.deepEqual(S.runSteps({ status: 'needs_attention', stage: 'Checking the lesson' }, false).map(s => s.status), ['done', 'stopped', 'todo']);
    assert.equal(S.lessonChip('mystery_stage'), '○ Mystery stage');
    assert.deepEqual(['draft', 'generating', 'needs_attention', 'review', 'ready_to_export', 'exporting', 'completed'].map(S.lessonChip),
        ['○ Ready to edit', '● Generating visuals', '⚠ Needs attention', '● Ready for review', '✓ Ready to export', '● Exporting the video', '✓ Video ready']);
    assert.equal(S.qualityWords({ status: 'attention', counts: { error: 1 } }), '⚠ 1 thing to review');
    assert.equal(S.qualityWords({ status: 'review', counts: { attention: 4 } }), '⚠ 4 things to review');
    assert.equal(S.qualityWords(null), null);
    assert.equal(S.looksLikeLessonJson('{"slides": []}'), true);
    assert.equal(S.looksLikeLessonJson('{ not json'), false);
    assert.equal(S.mediaItemText({ sceneIndex: null, slot: 'background' }), "The lesson's background needs attention");
    const lessons = S.normalizeLessons([{ project_id: 3, title: '' }, { run_id: 'r', status: 'completed' }, 'junk']);
    assert.deepEqual([lessons.lessons.length, lessons.lessons[0].title, lessons.runs.length], [1, 'Untitled lesson', 0]);
});

test('No lesson writer on this server: said on Home and Create; "Write the lesson" is off with the note in place; documents and lesson files stay; a 503 offers no Try again', async () => {
    const none = await setup({ adapter: { openLessonFile: async () => null,
        listLessons: () => ({ lessons: [], runs: [], writer: { available: false, provider: null, stand_in: false } }) } });
    // what is missing, who can fix it, and what still works (truthfully: a document can be prepared, but not written from)
    assert.equal(none.$('.studio-writer-note').textContent, "ⓘ This copy of Aadhi can't write lessons with AI yet — ask your administrator to set up a lesson writer. "
        + 'You can still prepare a document, open your lessons or open a lesson file.');
    assert.equal(none.$('.studio-writer-note').getAttribute('role'), 'note');
    await none.click('.studio-empty [data-action="create"]');
    assert.ok(none.$('.studio-writer-note'), 'told before choosing how to start');
    assert.equal(none.$('[data-action="from-document"]').getAttribute('disabled'), null, 'documents can still be prepared');
    assert.equal(none.$('[data-action="open-file"]').getAttribute('disabled'), null, 'lesson files can still be opened');
    await none.click('[data-action="from-text"]');
    assert.equal(none.$('[data-action="start-lesson"]').getAttribute('disabled'), '');
    assert.ok(none.$('.studio-form .studio-writer-note'), 'the note where the button is');
    assert.equal(none.$('.studio-next'), null);
    none.type('[data-field="text"]', 'Some notes');
    none.$('.studio-form').fire('submit'); // (Enter in a field)
    await settle();
    assert.equal(none.named('startLesson').length, 0);
    const some = await setup({ adapter: { listLessons: () => ({ lessons: [], runs: [], writer: { available: true, provider: 'gemini', stand_in: false } }) } });
    assert.equal(some.$('.studio-writer-note'), null);
    assert.ok(!/gemini/i.test(some.text()), 'no provider name');
    // the server refuses (503: no writer): trying again cannot work — back to the lessons instead
    const refused = await setup({ adapter: { startLesson: async () => { const e = new Error(NO_WRITER); e.status = 503; throw e; } } });
    await refused.create();
    await refused.click('[data-action="from-document"]');
    await refused.click('[data-action="start-lesson"]');
    assert.match(refused.$('.studio-error p').textContent, /^⚠ We couldn't start writing the lesson\. No AI lesson writer is set up on this server/);
    assert.equal(refused.$('.studio-error [data-action="retry"]'), null);
    assert.ok(refused.$('.studio-error [data-action="back-home"]'));
    assert.equal(refused.$('[data-action="start-lesson"]').getAttribute('disabled'), '');
    assert.ok(refused.$('.studio-writer-note'));
    await refused.click('.studio-error [data-action="back-home"]');
    assert.equal(refused.panel.view, 'home');
    assert.equal(refused.named('startLesson').length, 1);
});

test('studio.css: the shared product tokens only (never the lesson style\'s --st-*, the stage\'s fonts or gold), text never below 12 px, focus rings, reduced motion', () => {
    const css = fs.readFileSync(path.join(__dirname, '..', 'studio.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    assert.ok(!/--st-/.test(css), 'no --st-* (the lesson style\'s names, Phase 17)');
    assert.ok(!/var\(--(font-|text-gold|cine)/.test(css), 'never the stage\'s tokens');
    assert.ok((css.match(/var\(--ui-/g) || []).length > 50, 'the shared --ui-* tokens');
    const sizes = [...css.matchAll(/font-size:\s*([0-9.]+)(rem|px)/g)].map(m => (m[2] === 'px' ? Number(m[1]) : Number(m[1]) * 16));
    sizes.forEach(px => assert.ok(px >= 12, `${px}px`));
    assert.ok(!/font-size:\s*[0-9.]+em\b/.test(css), 'no em font sizes (they shrink when nested)');
    assert.match(css, /\.studio-root :focus-visible\s*\{\s*outline: var\(--ui-focus-width\) solid var\(--ui-focus\);/);
    assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
    assert.match(css, /body\[data-recording\] \.studio-root\s*\{\s*display: none !important;/);
    assert.ok(!/text-transform:\s*uppercase/.test(css), 'no all-caps');
    // the header's buttons stay at least 36 × 36 px at every width
    assert.match(css, /\.studio-top \.studio-btn\s*\{[^}]*min-width: var\(--ui-control-md\);[^}]*min-height: var\(--ui-control-md\);/);
});
