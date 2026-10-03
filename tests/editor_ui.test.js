'use strict';
// Unit tests for the editor workspace (Phase 19, editor_ui.js): the chrome around the live stage (top bar, scene list,
// timeline, inspector), driven by the real editor model (editor.js) over a small lesson, with a fake page adapter and a
// fake autosave. Every change must go through the model (one command per change; a typing session or a drag is one undo
// step), then reach the page (adapter.changed / applyComposition) and the autosave (schedule; hold / release for drags).
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const UI = require('../editor_ui.js');
const E = require('../editor.js');
const { fakeDoc, Node } = require('./helpers/cinematic-dom.js');

// The fake DOM has no remove(); the workspace detaches its chrome with it on close (as the browser does)
if (typeof Node.prototype.remove !== 'function') {
    Node.prototype.remove = function remove() {
        if (this.parent) {
            this.parent.children = this.parent.children.filter(c => c !== this);
            this.parent = null;
        }
    };
}

const tick = () => new Promise(r => setTimeout(r, 0));
const ID = n => `s-${String(n).padStart(12, '0')}`;
const A = 'a'.repeat(32);

function plan(extra = {}) {
    return {
        version: 1, template: 'presenter_plus_visual', template_label: 'Presenter + visual', duration: 9,
        presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        layers: [{ id: 'presenter', type: 'presenter', box: { x: 0.7, y: 0.1, w: 0.27, h: 0.7 } }],
        camera: { shot: 'medium', movement: 'slow_zoom_in' }, transition: { in: 'fade', out: 'fade', duration: 0.5 },
        background: { type: 'gradient' }, review_status: 'pending', fingerprint: 'feedfacecafebeef', plan_hash: 'abc123def456',
        ...extra
    };
}

function lesson() {
    return [
        { scene_id: ID(1), type: 'chapter_card', title: 'Plants <b>and</b> light',
          narration: 'Plants use light to make food. [PAUSE] They store the food as sugar in their leaves.',
          cinematic_plan: plan({ review_status: 'approved' }),
          visual_plan: { side: { source: 'ASSET', media: 'STATIC_IMAGE', review_status: 'approved' } } },
        { scene_id: ID(2), type: 'content', title: 'Roots', subtitle: 'Water from below', narration: 'Roots pull water up from the soil every day.',
          cinematic_plan: plan({ presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: false, side: 'left' } }),
          visual_plan: { side: { source: 'PROCEDURAL', media: 'ANIMATION', renderer: 'chart', review_status: 'pending' } },
          composition: { labels: [{ text: 'Root', at: 2 }, 'Soil'] } },
        { scene_id: ID(3), type: 'content', title: 'Hidden extra', narration: 'This one is skipped.', edit: { hidden: true } },
        { scene_id: ID(4), type: 'recap', title: 'Recap', narration: '' }
    ];
}

function quality(issues) {
    return { status: 'review', issues };
}

// A fake autosave with the editor.js Autosave surface the workspace uses
function fakeAutosave() {
    const calls = [];
    return {
        calls, state: 'saved', info: {}, onState: null,
        schedule() { calls.push('schedule'); },
        hold() { calls.push('hold'); },
        release() { calls.push('release'); },
        retry() { calls.push('retry'); return Promise.resolve(true); },
        flush() { calls.push('flush'); return Promise.resolve(true); },
        set(state, info = {}) { this.state = state; this.info = info; if (this.onState) this.onState(state, info); }
    };
}

// A document whose own listeners are recorded (the fake document ignores them)
function makeDoc() {
    const doc = fakeDoc();
    doc.listeners = [];
    doc.addEventListener = (type, fn, capture) => doc.listeners.push({ type, fn, capture: !!capture });
    doc.removeEventListener = (type, fn) => { doc.listeners = doc.listeners.filter(l => !(l.type === type && l.fn === fn)); };
    return doc;
}

// A key press as the document's listeners see it (target: the page body unless given)
function press(doc, key, opts = {}) {
    const ev = { key, target: opts.target || doc.body, ctrlKey: !!opts.ctrl, metaKey: !!opts.meta, shiftKey: !!opts.shift, altKey: !!opts.alt,
        stopped: false, prevented: false, stopPropagation() { this.stopped = true; }, preventDefault() { this.prevented = true; } };
    doc.listeners.filter(l => l.type === 'keydown').forEach(l => l.fn(ev));
    return ev;
}

function setup({ scenes = lesson(), adapter: extra = {}, report = null, autosave = fakeAutosave(), open = true, editor = {} } = {}) {
    const doc = makeDoc();
    const model = new E.EditorModel({ scenes, editor });
    // spies on the model methods the workspace may call (the real methods still run)
    const modelCalls = [];
    ['setHidden', 'duplicate', 'remove', 'move', 'insert', 'splitAtPause', 'setDuration', 'setNarrationMuted', 'setCaptions',
        'setLessonCaptions', 'setText', 'setLabels', 'revert', 'setOverride', 'undo', 'redo', 'begin', 'commit'].forEach(name => {
        const real = model[name].bind(model);
        model[name] = (...args) => { modelCalls.push([name, ...args]); return real(...args); };
    });
    const calls = [];
    const record = name => (...args) => { calls.push([name, ...args]); };
    const adapter = {
        lessonTitle: () => 'Photosynthesis <script>alert(1)</script>',
        currentScene: () => 0,
        seek: record('seek'), play: record('play'), pause: record('pause'), isPlaying: () => false,
        changed: record('changed'),
        applyComposition: async effect => { calls.push(['applyComposition', JSON.parse(JSON.stringify(effect))]); },
        chooseVisual: async (index, slot) => { calls.push(['chooseVisual', index, slot]); return true; },
        removeVisual: async (index, slot) => { calls.push(['removeVisual', index, slot]); return true; },
        pickAsset: async opts => { calls.push(['pickAsset', opts]); return { id: A, title: 'Leaf picture' }; },
        saveVersion: async () => { calls.push(['saveVersion']); },
        quality: () => report,
        showInReview: record('showInReview'),
        openStyleSettings: record('openStyleSettings'),
        closed: record('closed'),
        ...extra
    };
    const ws = new UI.EditorWorkspace({ doc, model, autosave, adapter });
    if (open) ws.open();
    const named = name => calls.filter(c => c[0] === name);
    const $ = sel => ws.root.querySelector(sel);
    const $$ = sel => ws.root.querySelectorAll(sel);
    const click = sel => { const n = typeof sel === 'string' ? $(sel) : sel; assert.ok(n, `no ${sel}`); n.fire('click'); return n; };
    return { doc, model, ws, adapter, autosave, calls, named, modelCalls, $, $$, click, scenes: model.scenes };
}

const listItems = ctx => ctx.ws.sceneList.querySelectorAll('.editor-scene');
const sceneBlocks = ctx => ctx.ws.timeline.querySelectorAll('[data-track="scene"] .editor-tl-block');
const section = (ctx, key) => ctx.ws.inspector.querySelector(`[data-section="${key}"]`);
const openSection = (ctx, key) => {
    const head = section(ctx, key).querySelector('.editor-section-head');
    if (head.getAttribute('aria-expanded') !== 'true') head.fire('click');
    return section(ctx, key);
};
const changeValue = (node, value) => { node.value = value; node.fire('change'); };
const pointer = (node, type, ev = {}) => (node.listeners[type] || []).forEach(fn => fn({ button: 0, pointerId: 1, preventDefault() {}, stopPropagation() {}, ...ev }));

// ---- building the chrome ---------------------------------------------------------------------------------------------

test('open() builds the chrome around the stage: body.editor-active, top bar, scene list, timeline tracks, inspector', () => {
    const ctx = setup();
    const { doc, ws } = ctx;
    assert.ok(doc.body.classList.contains('editor-active'));
    assert.equal(doc.body.children.filter(c => c.classList.contains('editor-root')).length, 1);
    assert.ok(ws.root.querySelector('.editor-stage-hole'), 'the centre stays free for the live stage');
    // Phase 21: a note in the free centre (shown by editor.css at 1280 px or less) says the panels cover part of the scene
    assert.equal(ws.root.querySelector('.editor-stage-hole .editor-stage-note').textContent, 'Part of the scene is under the panels — Preview (👁) shows all of it');
    assert.equal(ws.root.querySelector('.editor-stage-hole .editor-stage-note').textContent, UI.STAGE_NOTE);
    assert.equal(ws.root.querySelector('.editor-stage-hole').getAttribute('aria-hidden'), 'true');
    // top bar
    assert.ok(ctx.$('[data-action="close"]'));
    assert.equal(ctx.$('.editor-title').textContent, 'Photosynthesis <script>alert(1)</script>');
    assert.ok(ctx.$('[data-action="undo"]').getAttribute('disabled') !== null);
    assert.ok(ctx.$('[data-action="redo"]').getAttribute('disabled') !== null);
    assert.equal(ctx.$('.editor-save').textContent, '✓ Saved');
    assert.equal(ctx.$('.editor-save').getAttribute('role'), 'status');
    assert.equal(ctx.$('[data-action="quality"]').textContent, '◌ Quality not checked');
    assert.ok(ctx.$('[data-action="save-version"]'));
    assert.equal(ctx.$('[data-action="preview"]').getAttribute('aria-pressed'), 'false');
    assert.equal(ctx.$('[data-action="help"]').getAttribute('aria-expanded'), 'false');
    // scene list: index, title, type, duration
    const items = listItems(ctx);
    assert.equal(items.length, 4);
    assert.equal(items[0].querySelector('.editor-scene-index').textContent, '1');
    assert.equal(items[0].querySelector('.editor-scene-title').textContent, 'Plants <b>and</b> light');
    assert.match(items[0].querySelector('.editor-scene-meta').textContent, /^Chapter card · \d+(\.\d)? s$/);
    assert.equal(items[3].querySelector('.editor-scene-meta').textContent, 'Recap · 5 s');
    // timeline: ruler + five tracks, play controls, time labels
    assert.deepEqual(ws.timeline.querySelectorAll('.editor-tl-row').map(r => r.getAttribute('data-track')),
        ['ruler', 'scene', 'narration', 'presenter', 'visual', 'caption']);
    assert.ok(ws.timeline.querySelector('[data-action="play"]'));
    assert.ok(ws.timeline.querySelector('[data-action="prev-scene"]'));
    assert.ok(ws.timeline.querySelector('[data-action="next-scene"]'));
    assert.match(ws.timeline.querySelector('.editor-tl-time').textContent, /^0:00 \/ 0:\d\d$/);
    assert.ok(ws.timeline.querySelector('.editor-playhead'));
    assert.ok(ws.timeline.querySelectorAll('.editor-tl-tick').length >= 2);
    // the small-screen note is there (shown by editor.css at 700px or less): what still works there, in plain words
    assert.equal(ws.timeline.querySelector('.editor-tl-note').textContent, 'Editing works best on a larger screen; here you can change scenes in the list and the inspector.');
    assert.equal(ws.timeline.querySelector('.editor-tl-note').textContent, UI.SMALL_SCREEN_NOTE);
    // the inspector shown / hidden from the transport row (on a tablet it takes the tracks' place)
    assert.equal(ws.timeline.querySelector('[data-action="toggle-inspector"]').textContent, 'Hide inspector');
    assert.equal(ws.timeline.querySelector('[data-action="toggle-inspector"]').getAttribute('aria-controls'), 'editor-inspector');
    assert.equal(ws.inspector.getAttribute('id'), 'editor-inspector');
    // inspector on the first scene
    assert.equal(ws.inspector.querySelector('.editor-panel-head').textContent.startsWith('Scene 1 of 4'), true);
    // the workspace is a modal dialog; "Back" says where it goes (the lesson by default)
    assert.equal(ws.root.getAttribute('role'), 'dialog');
    assert.equal(ws.root.getAttribute('aria-modal'), 'true');
    assert.equal(ws.root.getAttribute('aria-label'), 'Lesson editor');
    assert.equal(ctx.$('[data-action="close"]').textContent, '‹Back to lesson');
    assert.equal(ctx.$('[data-action="close"]').getAttribute('aria-label'), 'Back to lesson (closes the editor)');
    // icon buttons have a name and a tooltip
    for (const action of ['help', 'zoom-in', 'zoom-out', 'prev-scene', 'next-scene', 'close-inspector']) {
        const node = ws.root.querySelector(`[data-action="${action}"]`);
        assert.ok(node.getAttribute('aria-label') && node.getAttribute('title'), action);
    }
    // keyboard: one capture listener on the document
    const keys = doc.listeners.filter(l => l.type === 'keydown');
    assert.equal(keys.length, 1);
    assert.equal(keys[0].capture, true);
    // opening twice builds nothing more
    ws.open();
    assert.equal(doc.body.children.filter(c => c.classList.contains('editor-root')).length, 1);
});

test('the timeline follows the model: blocks sized by play seconds, hidden scenes collapsed and striped, total duration', () => {
    const ctx = setup();
    const tl = ctx.model.timeline();
    const blocks = sceneBlocks(ctx);
    assert.equal(blocks.length, 4);
    const px = s => parseInt(s, 10);
    // widths follow the seconds (12 px a second when the width is unknown), a hidden scene is a narrow collapsed block
    const w = blocks.map(b => px(b.style.width));
    assert.ok(Math.abs(w[0] + 2 - tl.scenes[0].seconds * 12) <= 1, 'scene 1 width');
    assert.ok(Math.abs(w[3] + 2 - Math.max(18, tl.scenes[3].seconds * 12)) <= 1, 'scene 4 width');
    assert.ok(w[2] <= 14, 'the hidden scene is collapsed');
    assert.ok(blocks[2].classList.contains('is-hidden'));
    assert.equal(blocks[2].querySelector('.editor-tl-block-title'), null);
    // positions are cumulative
    assert.equal(px(blocks[1].style.left), px(blocks[0].style.left) + w[0] + 2);
    // title + seconds on the block
    assert.equal(blocks[0].querySelector('.editor-tl-block-title').textContent, '1. Plants <b>and</b> light');
    assert.match(blocks[0].querySelector('.editor-tl-block-time').textContent, /^\d+(\.\d)? s$/);
    // total
    assert.equal(ctx.ws.timeline.querySelector('.editor-tl-time').textContent, `0:00 / ${UI.clock(tl.total)}`);
    // narration bars only where there is narration; hidden scenes have placeholders in every other track
    const narration = ctx.ws.timeline.querySelectorAll('[data-track="narration"] .editor-tl-block');
    assert.deepEqual(narration.map(b => b.getAttribute('data-state')), ['on', 'on', 'hidden']);
    // presenter: shown / hidden per scene from the plan
    const presenter = ctx.ws.timeline.querySelectorAll('[data-track="presenter"] .editor-tl-block');
    assert.deepEqual(presenter.map(b => b.getAttribute('data-state')), ['shown', 'hidden', 'hidden', 'none']);
    assert.equal(presenter[0].textContent, 'Shown · Right');
    // visual source per scene
    const visual = ctx.ws.timeline.querySelectorAll('[data-track="visual"] .editor-tl-block');
    assert.deepEqual(visual.map(b => b.getAttribute('data-state')), ['picture', 'diagram', 'hidden', 'none']);
    assert.equal(visual[0].textContent, '🖼 Picture');
    // captions on / off
    const captions = ctx.ws.timeline.querySelectorAll('[data-track="caption"] .editor-tl-block');
    assert.deepEqual(captions.map(b => b.getAttribute('data-state')), ['on', 'on', 'hidden', 'on']);
    assert.equal(captions[0].textContent, 'Captions on'); // Phase 21: in words, never "CC On"
    assert.equal(presenter[3].textContent, 'No presenter'); // a recap without Aadhi
});

test('a Classic scene says "Aadhi (no presenter)" where Aadhi stands (mascot.js placement), else "No presenter"', () => {
    const text = scene => UI.presenterInfo(scene).text;
    assert.equal(text({ type: 'content', title: 'A', aadhi_position: 'left' }), 'Aadhi (no presenter)');
    assert.equal(text({ type: 'content', title: 'A', aadhi_position: 'popup_bottom_right' }), 'Aadhi (no presenter)');
    assert.equal(text({ type: 'title', title: 'A' }), 'Aadhi (no presenter)'); // a lesson from before aadhi_position
    assert.equal(text({ type: 'content', title: 'A', aadhi_position: 'hidden' }), 'No presenter');
    assert.equal(text({ type: 'recap', title: 'A' }), 'No presenter');
    // another presenter takes Aadhi's place: no claim about Aadhi
    assert.equal(text({ type: 'content', aadhi_position: 'left', presenter_plan: { presenter_id: 'p1', type: 'ai_avatar' } }), 'No presenter');
    assert.equal(text({ type: 'content', aadhi_position: 'left', presenter_plan: { presenter_id: 'aadhi', type: 'mascot' } }), 'Aadhi (no presenter)');
    // the state stays "none" (the inspector's layout is unchanged), and a composition without a presenter is still "No presenter"
    assert.equal(UI.presenterInfo({ type: 'content', aadhi_position: 'left' }).state, 'none');
    assert.equal(text({ type: 'content', aadhi_position: 'left', cinematic_plan: { template: 'text_focus' } }), 'No presenter');
    const ctx = setup({ scenes: [{ scene_id: ID(1), type: 'content', title: 'Classic', narration: 'Hello there.', aadhi_position: 'right' }] });
    assert.equal(ctx.ws.timeline.querySelector('[data-track="presenter"] .editor-tl-block').textContent, 'Aadhi (no presenter)');
});

test('the timeline fits its width; zoom in / out / fit', () => {
    const ctx = setup();
    const total = ctx.model.timeline().total;
    const px = s => parseInt(s, 10);
    const span = () => sceneBlocks(ctx).filter(b => !b.classList.contains('is-hidden')).reduce((sum, b) => sum + px(b.style.width) + 2, 0);
    // the scroll area the timeline measures: 1000 px for the scenes after the track names
    const measured = () => { ctx.ws.tlScroll.rect = { left: 0, top: 0, width: 1108, height: 120 }; };
    measured();
    ctx.ws.renderTimeline();
    assert.ok(Math.abs(span() - 1000) <= 4, `fits: ${span()}`);
    measured();
    ctx.click(ctx.ws.timeline.querySelector('[data-action="zoom-in"]'));
    assert.ok(Math.abs(span() - 1500) <= 6, `zoomed in: ${span()}`);
    measured();
    ctx.click(ctx.ws.timeline.querySelector('[data-action="zoom-fit"]'));
    assert.ok(Math.abs(span() - 1000) <= 4, `back to fit: ${span()}`);
    measured();
    ctx.click(ctx.ws.timeline.querySelector('[data-action="zoom-out"]'));
    assert.ok(span() < 1000);
    assert.ok(total > 0);
});

test('a muted narration is shown muted; a small presenter is shown small; transitions sit between blocks', () => {
    const scenes = lesson();
    scenes[0].edit = { narration_muted: true };
    scenes[1].cinematic_plan.presenter.shown = true;
    scenes[1].cinematic_plan.layers = [{ id: 'presenter', type: 'presenter', box: { x: 0.8, y: 0.5, w: 0.15, h: 0.3 } }];
    scenes[3].narration = 'The recap says what we learned today.';
    scenes[3].cinematic_plan = plan({ transition: { in: 'crossfade', out: 'fade', duration: 0.6 } });
    const ctx = setup({ scenes });
    const narration = ctx.ws.timeline.querySelectorAll('[data-track="narration"] .editor-tl-block');
    assert.equal(narration[0].getAttribute('data-state'), 'muted');
    assert.equal(narration[0].textContent, '🔇 Muted');
    const presenter = ctx.ws.timeline.querySelectorAll('[data-track="presenter"] .editor-tl-block');
    assert.equal(presenter[1].getAttribute('data-state'), 'small');
    assert.equal(presenter[1].textContent, 'Small · Left');
    const markers = ctx.ws.timeline.querySelectorAll('.editor-tl-transition');
    assert.deepEqual(markers.map(m => m.getAttribute('data-transition')), ['fade', 'crossfade']);
    assert.match(markers[1].getAttribute('aria-label'), /Transition into scene 4: Crossfade/);
    // the block after a transition shows the scene's own play time, the same as the scene list (browser check finding); its
    // width also covers the transition
    const entry = ctx.model.timeline().scenes[3];
    assert.equal(entry.transition_seconds, 0.6);
    const block = sceneBlocks(ctx)[3];
    assert.equal(block.querySelector('.editor-tl-block-time').textContent, UI.secondsText(entry.play_seconds));
    assert.equal(block.querySelector('.editor-tl-block-time').textContent, listItems(ctx)[3].querySelector('.editor-scene-meta').textContent.split(' · ')[1]);
    assert.ok(Math.abs(parseInt(block.style.width, 10) + 2 - Math.max(18, entry.seconds * 12)) <= 1, 'the width includes the transition');
    // a transition marker opens the inspector's Transition section on that scene
    markers[1].fire('click');
    assert.equal(ctx.ws.selectedId, ID(4));
    assert.equal(section(ctx, 'transition').querySelector('.editor-section-head').getAttribute('aria-expanded'), 'true');
});

// ---- selection ---------------------------------------------------------------------------------------------------------

test('selecting a scene syncs the list, the timeline and the inspector, and shows it on the stage', () => {
    const ctx = setup();
    listItems(ctx)[1].fire('click');
    assert.equal(ctx.ws.selectedId, ID(2));
    assert.deepEqual(listItems(ctx).map(i => i.getAttribute('aria-current')), ['false', 'true', 'false', 'false']);
    assert.deepEqual(sceneBlocks(ctx).map(b => b.getAttribute('aria-current')), ['false', 'true', 'false', 'false']);
    assert.ok(ctx.ws.inspector.querySelector('.editor-panel-head').textContent.startsWith('Scene 2 of 4'));
    assert.equal(ctx.ws.inspector.querySelector('.editor-fact-title').textContent, 'Roots');
    assert.deepEqual(ctx.named('seek'), [['seek', 1]]);
    // the playhead moved to the scene's start
    assert.equal(ctx.ws.time, ctx.model.timeline().scenes[1].start);
    // a timeline block selects too
    sceneBlocks(ctx)[3].fire('click');
    assert.equal(ctx.ws.selectedId, ID(4));
    assert.equal(listItems(ctx)[3].getAttribute('aria-current'), 'true');
    assert.deepEqual(ctx.named('seek').map(c => c[1]), [1, 3]);
    // previous / next step through the scenes
    ctx.click(ctx.ws.timeline.querySelector('[data-action="prev-scene"]'));
    assert.equal(ctx.ws.selectedId, ID(3));
    ctx.click(ctx.ws.timeline.querySelector('[data-action="next-scene"]'));
    ctx.click(ctx.ws.timeline.querySelector('[data-action="next-scene"]'));
    assert.equal(ctx.ws.selectedId, ID(4), 'stays on the last scene');
});

test('the editor starts on the scene the stage shows; the page reports playback with sync()', () => {
    const ctx = setup({ adapter: { currentScene: () => 3 } });
    assert.equal(ctx.ws.selectedId, ID(4));
    ctx.ws.sync({ scene: 1, elapsed: 2, playing: true });
    assert.equal(ctx.ws.selectedId, ID(2));
    assert.equal(ctx.ws.time, ctx.model.timeline().scenes[1].start + 2);
    assert.equal(ctx.ws.timeline.querySelector('[data-action="play"]').getAttribute('aria-pressed'), 'true');
    assert.equal(ctx.ws.timeline.querySelector('[data-action="play"]').textContent, '❚❚ Pause');
    const left = parseInt(ctx.ws.playhead.style.left, 10);
    ctx.ws.sync({ scene: 1, elapsed: 3, playing: true });
    assert.ok(parseInt(ctx.ws.playhead.style.left, 10) > left, 'the playhead moves along');
});

test('play / pause go to the page', () => {
    const ctx = setup();
    const play = () => ctx.ws.timeline.querySelector('[data-action="play"]');
    assert.equal(play().getAttribute('aria-pressed'), 'false');
    assert.equal(play().textContent, '▶ Play');
    const button = play();
    button.fire('click');
    assert.deepEqual(ctx.named('play'), [['play']]);
    assert.equal(play().getAttribute('aria-pressed'), 'true');
    assert.equal(play(), button, 'changed in place (keeps the focus)');
    assert.equal(play().getAttribute('aria-label'), 'Pause');
    play().fire('click');
    assert.deepEqual(ctx.named('pause'), [['pause']]);
});

// ---- the inspector's controls ------------------------------------------------------------------------------------------

test('advanced inspector sections are collapsed by default; a section opens and closes (aria-expanded)', () => {
    const ctx = setup();
    const expanded = key => section(ctx, key).querySelector('.editor-section-head').getAttribute('aria-expanded');
    // scene 1 is laid out "presenter + visual" with a picture: its Visual section leads (first after Scene, open)
    for (const key of ['scene', 'timing', 'narration', 'text', 'visual']) assert.equal(expanded(key), 'true', key);
    for (const key of ['presenter', 'camera', 'background', 'style', 'captions', 'quality']) {
        assert.equal(expanded(key), 'false', key);
        assert.equal(section(ctx, key).querySelector('.editor-section-body'), null);
    }
    // the first scene has no transition before it: no Transition section
    assert.equal(section(ctx, 'transition'), null);
    // the advanced ones come last, under "Look and motion"
    const order = ctx.ws.inspector.querySelectorAll('.editor-section').map(s => s.getAttribute('data-section'));
    assert.deepEqual(order, ['scene', 'visual', 'narration', 'text', 'timing', 'presenter', 'captions', 'quality', 'camera', 'background', 'style']);
    assert.equal(ctx.ws.inspector.querySelector('.editor-group-head').textContent, 'Look and motion');
    // scene 2: the transition into it is offered, collapsed
    listItems(ctx)[1].fire('click');
    assert.equal(expanded('transition'), 'false');
    listItems(ctx)[0].fire('click');
    openSection(ctx, 'camera');
    assert.equal(expanded('camera'), 'true');
    assert.ok(section(ctx, 'camera').querySelector('select[data-field="camera"]'));
    section(ctx, 'camera').querySelector('.editor-section-head').fire('click');
    assert.equal(expanded('camera'), 'false');
    // the user's choice is kept from scene to scene (the lead closed stays closed)
    section(ctx, 'visual').querySelector('.editor-section-head').fire('click');
    assert.equal(expanded('visual'), 'false');
    listItems(ctx)[1].fire('click');
    assert.equal(expanded('visual'), 'false');
});

test('Scene section: hide / show, duplicate, move, insert, each one model command then changed() and the autosave', async () => {
    const ctx = setup();
    const sec = () => section(ctx, 'scene');
    // hide
    ctx.click(sec().querySelector('[data-action="toggle-hidden"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['setHidden', ID(1), true]);
    assert.equal(ctx.scenes[0].edit.hidden, true);
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['hidden']);
    assert.equal(ctx.named('changed').at(-1)[1].scene_id, ID(1));
    assert.equal(ctx.autosave.calls.filter(c => c === 'schedule').length, 1);
    assert.equal(sec().querySelector('[data-action="toggle-hidden"]').getAttribute('aria-pressed'), 'true');
    assert.equal(sec().querySelector('[data-action="toggle-hidden"]').textContent, '👁 Show scene');
    ctx.click(sec().querySelector('[data-action="toggle-hidden"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['setHidden', ID(1), false]);
    // duplicate: a new scene after it, selected
    ctx.click(sec().querySelector('[data-action="duplicate"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['duplicate', ID(1)]);
    assert.equal(ctx.scenes.length, 5);
    assert.equal(ctx.ws.selectedId, ctx.scenes[1].scene_id);
    assert.notEqual(ctx.scenes[1].scene_id, ID(1));
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['insert']);
    assert.equal(ctx.named('changed').at(-1)[1].structure, true);
    // move later / earlier / end / start
    ctx.click(sec().querySelector('[data-action="move-right"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['move', ctx.ws.selectedId, 2]);
    ctx.click(sec().querySelector('[data-action="move-end"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['move', ctx.ws.selectedId, 4]);
    assert.ok(sec().querySelector('[data-action="move-end"]').getAttribute('disabled') !== null, 'already at the end');
    ctx.click(sec().querySelector('[data-action="move-start"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['move', ctx.ws.selectedId, 0]);
    assert.ok(sec().querySelector('[data-action="move-left"]').getAttribute('disabled') !== null, 'already at the start');
    assert.equal(ctx.scenes[0].scene_id, ctx.ws.selectedId);
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['order']);
    // insert a blank scene after it
    const before = ctx.scenes.length;
    ctx.click(sec().querySelector('[data-action="insert-blank"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['insert', 1, 'blank', undefined]);
    assert.equal(ctx.scenes.length, before + 1);
    assert.equal(ctx.ws.selectedId, ctx.scenes[1].scene_id);
    // insert a picture from the library: the Asset Library, then the model
    ctx.click(sec().querySelector('[data-action="insert-picture"]'));
    await tick();
    assert.deepEqual(ctx.named('pickAsset')[0][1].kinds, ['image']);
    assert.deepEqual(ctx.modelCalls.at(-1), ['insert', 2, 'asset', { asset_id: A, title: 'Leaf picture' }]);
    assert.equal(ctx.scenes[2].title, 'Leaf picture');
    // "Duplicate" is offered once (the insert row no longer repeats it)
    assert.equal(sec().querySelector('[data-action="insert-duplicate"]'), null);
    assert.equal(sec().querySelectorAll('button').filter(b => /Duplicate/.test(b.textContent)).length, 1);
    ctx.click(sec().querySelector('[data-action="duplicate"]'));
    assert.equal(ctx.modelCalls.at(-1)[0], 'duplicate');
    // one autosave.schedule() per change
    const changes = ctx.named('changed').length;
    assert.equal(ctx.autosave.calls.filter(c => c === 'schedule').length, changes);
});

test('picking nothing in the Asset Library inserts nothing', async () => {
    const ctx = setup({ adapter: { pickAsset: async () => null } });
    ctx.click(section(ctx, 'scene').querySelector('[data-action="insert-picture"]'));
    await tick();
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'insert').length, 0);
    assert.equal(ctx.scenes.length, 4);
    // the Library failing to open: its one name (Phase 21), the edits safe, nothing inserted
    const failing = setup({ adapter: { pickAsset: async () => { throw new Error('offline'); } } });
    failing.click(section(failing, 'scene').querySelector('[data-action="insert-picture"]'));
    await tick();
    await tick();
    assert.equal(failing.$('.editor-top-message').textContent, '✕ The Library could not be opened. Your edits are safe: please try again.');
    assert.equal(failing.scenes.length, 4);
});

test('Delete asks first: Cancel keeps the scene, "Delete scene" removes it through the model and selects its neighbour', async () => {
    const ctx = setup();
    listItems(ctx)[1].fire('click');
    ctx.click(section(ctx, 'scene').querySelector('[data-action="delete"]'));
    const box = ctx.ws.confirmBox;
    assert.equal(box.getAttribute('hidden'), null);
    assert.equal(box.getAttribute('role'), 'alertdialog');
    assert.match(box.textContent, /Scene 2 “Roots” will be removed/);
    box.querySelector('[data-action="confirm-cancel"]').fire('click');
    await tick();
    assert.ok(box.getAttribute('hidden') !== null);
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'remove').length, 0);
    assert.equal(ctx.scenes.length, 4);
    ctx.click(section(ctx, 'scene').querySelector('[data-action="delete"]'));
    box.querySelector('[data-action="confirm-ok"]').fire('click');
    await tick();
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'remove'), [['remove', ID(2)]]);
    assert.equal(ctx.scenes.length, 3);
    assert.equal(ctx.ws.selectedId, ID(3), 'the next scene takes its place');
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['delete']);
    assert.equal(ctx.model.undoLabel(), 'Undo delete scene');
});

test('a command the model refuses shows its reason and changes nothing', async () => {
    const scenes = lesson().slice(0, 1);
    const ctx = setup({ scenes });
    ctx.click(section(ctx, 'scene').querySelector('[data-action="toggle-hidden"]'));
    assert.match(ctx.$('.editor-top-message').textContent, /At least one scene must stay visible/);
    assert.equal(ctx.$('.editor-top-message').getAttribute('data-kind'), 'error');
    assert.equal(ctx.named('changed').length, 0);
    assert.equal(ctx.autosave.calls.length, 0);
    ctx.click(section(ctx, 'scene').querySelector('[data-action="delete"]'));
    ctx.ws.confirmBox.querySelector('[data-action="confirm-ok"]').fire('click');
    await tick();
    assert.match(ctx.$('.editor-top-message').textContent, /A lesson needs at least one scene/);
    assert.equal(ctx.scenes.length, 1);
});

test('split at a pause: offered only when the model has split points', () => {
    const ctx = setup();
    const sec = section(ctx, 'scene');
    const select = sec.querySelector('select[data-field="split-point"]');
    assert.ok(select);
    assert.match(select.children[0].textContent, /^After “Plants use light to make food\.” \(at 0:0\d\)$/);
    ctx.click(sec.querySelector('[data-action="split"]'));
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'splitAtPause'), [['splitAtPause', ID(1), 0]]);
    assert.equal(ctx.scenes.length, 5);
    assert.equal(ctx.ws.selectedId, ctx.scenes[1].scene_id, 'the new second half is selected');
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['split']);
    assert.equal(ctx.named('changed').at(-1)[1].retime, true);
    // a scene without a pause: no split control
    listItems(ctx)[2].fire('click');
    assert.equal(section(ctx, 'scene').querySelector('[data-action="split"]'), null);
});

test('Timing: the minimum duration with the generated estimate; clear; invalid values refused', async () => {
    const ctx = setup();
    const sec = () => section(ctx, 'timing');
    const estimate = ctx.model.effective()[0].play_seconds;
    assert.equal(sec().querySelector('[data-generated="estimate"]').textContent, `Generated: about ${UI.secondsText(estimate)}`);
    assert.equal(sec().querySelector('[data-field-box="min_seconds"] .editor-state').textContent, 'Automatic'); // Phase 21: "Automatic", not "Generated"
    changeValue(sec().querySelector('input[data-field="min_seconds"]'), '20');
    assert.deepEqual(ctx.modelCalls.at(-1), ['setDuration', ID(1), 20]);
    assert.equal(ctx.scenes[0].edit.min_seconds, 20);
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['timing']);
    await tick(); // a number field changes as it loses focus: drawn again on the next turn
    assert.equal(sec().querySelector('[data-field-box="min_seconds"] .editor-state').textContent, '✎ Edited');
    // the generated estimate stays what the scene would play without the minimum
    assert.equal(sec().querySelector('[data-generated="estimate"]').textContent, `Generated: about ${UI.secondsText(estimate)}`);
    assert.equal(sceneBlocks(ctx)[0].querySelector('.editor-tl-block-time').textContent, '20 s');
    ctx.click(sec().querySelector('[data-action="clear-min"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['setDuration', ID(1), null]);
    await tick();
    assert.equal((ctx.scenes[0].edit || {}).min_seconds, undefined);
    const count = ctx.modelCalls.length;
    changeValue(sec().querySelector('input[data-field="min_seconds"]'), '9000');
    assert.equal(ctx.modelCalls.length, count, 'out of range: not sent');
    assert.match(ctx.$('.editor-top-message').textContent, /between 0\.5 and 600/);
});

test('Narration: typing is ONE transaction per focus session; generated value + Revert; mute', async () => {
    const ctx = setup();
    const area = () => section(ctx, 'narration').querySelector('textarea[data-field="narration"]');
    const original = ctx.scenes[0].narration;
    assert.equal(area().value, original);
    const field = area();
    field.fire('focus');
    field.value = original + ' More';
    field.fire('input');
    field.value = original + ' More words';
    field.fire('input');
    field.value = original + ' More words here.';
    field.fire('input');
    // nothing re-planned while typing; the list / timeline follow
    assert.equal(ctx.named('changed').length, 0);
    assert.equal(ctx.model.inTransaction, true);
    field.fire('blur');
    assert.equal(ctx.model.inTransaction, false);
    await tick(); // the inspector is drawn again on the next turn (never under the pointer / the next field)
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'begin').length, 1);
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'setText').length, 3);
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'setText').at(-1), ['setText', ID(1), 'narration', original + ' More words here.']);
    assert.equal(ctx.model.undoStack.length, 1, 'one undo step for the whole session');
    assert.equal(ctx.model.undoLabel(), 'Undo edit narration');
    assert.equal(ctx.named('changed').length, 1);
    assert.deepEqual(ctx.named('changed')[0][1].kinds, ['narration']);
    assert.equal(ctx.named('changed')[0][1].retime, true);
    assert.equal(ctx.autosave.calls.filter(c => c === 'schedule').length, 1);
    // generated vs edited, Revert
    const box = section(ctx, 'narration').querySelector('[data-field-box="narration"]');
    assert.equal(box.querySelector('.editor-state').textContent, '✎ Edited');
    assert.equal(box.querySelector('[data-generated="narration"]').textContent, `Generated: ${original}`);
    ctx.click(box.querySelector('[data-action="revert"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['revert', ID(1), 'narration']);
    assert.equal(ctx.scenes[0].narration, original);
    assert.equal(section(ctx, 'narration').querySelector('[data-field-box="narration"] .editor-state').textContent, 'Automatic');
    // focus without typing: no change
    const changes = ctx.named('changed').length;
    area().fire('focus');
    area().fire('blur');
    assert.equal(ctx.named('changed').length, changes);
    // mute
    const mute = () => section(ctx, 'narration').querySelector('[data-action="toggle-mute"]');
    assert.equal(mute().getAttribute('aria-pressed'), 'false');
    ctx.click(mute());
    assert.deepEqual(ctx.modelCalls.at(-1), ['setNarrationMuted', ID(1), true]);
    assert.equal(mute().getAttribute('aria-pressed'), 'true');
    assert.equal(mute().textContent, '🔇 Narration muted');
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['mute']);
});

test('leaving a field never redraws under the pointer: the clicked control survives until the press is released', async () => {
    const ctx = setup();
    const title = ctx.ws.inspector.querySelector('input[data-field="title"]');
    const hide = ctx.ws.inspector.querySelector('[data-action="toggle-hidden"]');
    title.fire('focus');
    title.value = 'Plants and sunlight';
    title.fire('input');
    // the press on "Hide scene" moves the focus: the field's session ends at once (one command, the page told) ...
    ctx.ws.root.listeners.pointerdown.forEach(fn => fn({}));
    title.fire('blur');
    assert.equal(ctx.model.inTransaction, false);
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['text']);
    await tick();
    // ... but the inspector is not drawn again while the pointer is down: the button is still the one pressed
    assert.equal(ctx.ws.inspector.querySelector('[data-action="toggle-hidden"]'), hide);
    ctx.doc.listeners.filter(l => l.type === 'pointerup').forEach(l => l.fn({}));
    hide.fire('click');
    assert.equal(ctx.scenes[0].edit.hidden, true, 'the click did its work');
    await tick();
    assert.notEqual(ctx.ws.inspector.querySelector('[data-action="toggle-hidden"]'), hide, 'drawn again afterwards');
    assert.equal(ctx.model.undoStack.length, 2);
});

test('Text: title / subtitle / labels through the model; labels keep their timing; markup typed stays text', async () => {
    const ctx = setup();
    listItems(ctx)[1].fire('click');
    const sec = () => section(ctx, 'text');
    const title = sec().querySelector('input[data-field="title"]');
    title.fire('focus');
    title.value = 'Roots <img src=x onerror=alert(1)>';
    title.fire('input');
    // Enter ends the session
    (title.listeners.keydown || []).forEach(fn => fn({ key: 'Enter', preventDefault() {} }));
    await tick();
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'setText').at(-1), ['setText', ID(2), 'title', 'Roots <img src=x onerror=alert(1)>']);
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['text']);
    // shown as text everywhere
    assert.equal(listItems(ctx)[1].querySelector('.editor-scene-title').textContent, 'Roots <img src=x onerror=alert(1)>');
    assert.equal(ctx.ws.root.querySelectorAll('img').length, 0);
    assert.equal(sec().querySelector('[data-generated="title"]').textContent, 'Generated: Roots');
    // subtitle
    const subtitle = sec().querySelector('input[data-field="subtitle"]');
    assert.equal(subtitle.value, 'Water from below');
    subtitle.fire('focus');
    subtitle.value = 'Water from the soil';
    subtitle.fire('input');
    subtitle.fire('blur');
    await tick();
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'setText').at(-1), ['setText', ID(2), 'subtitle', 'Water from the soil']);
    // labels: one per line; the first keeps its "at"
    const labels = sec().querySelector('textarea[data-field="labels"]');
    assert.equal(labels.value, 'Root\nSoil');
    labels.fire('focus');
    labels.value = 'Root hair\nSoil\nWater';
    labels.fire('input');
    labels.fire('blur');
    await tick();
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'setLabels').at(-1), ['setLabels', ID(2), [{ text: 'Root hair', at: 2 }, 'Soil', 'Water']]);
    assert.deepEqual(ctx.scenes[1].composition.labels, [{ text: 'Root hair', at: 2 }, 'Soil', 'Water']);
    assert.equal(sec().querySelector('[data-generated="labels"]').textContent, 'Generated: Root · Soil');
    ctx.click(sec().querySelector('[data-field-box="labels"] [data-action="revert"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['revert', ID(2), 'labels']);
    // three sessions, three undo steps (plus the revert)
    assert.equal(ctx.model.undoStack.length, 4);
});

test('Presenter: position and size are composition overrides (model.setOverride, then adapter.applyComposition)', async () => {
    const ctx = setup();
    const sec = () => openSection(ctx, 'presenter');
    const btn = (key, value) => sec().querySelector(`[data-key="${key}"][data-value="${value}"]`);
    assert.equal(btn('presenter_position', 'right').getAttribute('aria-pressed'), 'true');
    assert.equal(btn('presenter_size', 'secondary').getAttribute('aria-pressed'), 'true');
    assert.equal(sec().querySelector('[data-field-box="presenter_position"] .editor-state').textContent, 'Automatic');
    ctx.click(btn('presenter_position', 'left'));
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'presenter_position', 'left']);
    assert.deepEqual(ctx.named('applyComposition').at(-1)[1].overrides, { presenter_position: 'left' });
    assert.equal(ctx.named('applyComposition').at(-1)[1].scene_id, ID(1));
    assert.equal(ctx.named('applyComposition').at(-1)[1].kind, 'composition');
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['composition']);
    assert.equal(ctx.autosave.calls.filter(c => c === 'schedule').length, 1);
    // the page's review route saved the override: shown as the user's choice, with "Automatic" to give it back
    ctx.scenes[0].visual_review = { composition: { status: 'changed', overrides: { presenter_position: 'left' } } };
    ctx.ws.render();
    assert.equal(btn('presenter_position', 'left').getAttribute('aria-pressed'), 'true');
    assert.equal(sec().querySelector('[data-field-box="presenter_position"] .editor-state').textContent, '✎ Edited');
    ctx.click(btn('presenter_position', 'auto'));
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'presenter_position', 'auto']);
    ctx.click(btn('presenter_size', 'small'));
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'presenter_size', 'small']);
    assert.deepEqual(ctx.named('applyComposition').at(-1)[1].overrides, { presenter_size: 'small' });
    // the presenter's own size list has no "hidden" (the position hides it)
    assert.equal(btn('presenter_size', 'hidden'), null);
    assert.ok(btn('presenter_position', 'hidden'));
});

test('Camera, Transition, Background, Style: overrides through the model, with the page vocabulary', async () => {
    const ctx = setup({ adapter: { vocabulary: () => ({ camera: { static: 'Still', focus: 'Focus' }, transition: { fade: 'Fade', cut: 'Cut' },
        background: { gradient: 'Clean gradient', studio: "Aadhi's studio" }, accent: ['default', 'teal'], background_density: ['default', 'rich'] }) } });
    const camera = openSection(ctx, 'camera').querySelector('select[data-field="camera"]');
    assert.deepEqual(camera.children.map(o => o.value), ['auto', 'static', 'focus']);
    assert.equal(camera.children[0].textContent, 'Automatic (Slow zoom in)', 'the vocabulary has no label for it: the value in words');
    changeValue(camera, 'focus');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'camera', 'focus']);
    assert.deepEqual(ctx.named('applyComposition').at(-1)[1].overrides, { camera: 'focus' });
    // the transition into a scene (the first scene has none before it: scene 2)
    assert.equal(section(ctx, 'transition'), null);
    listItems(ctx)[1].fire('click');
    const transition = openSection(ctx, 'transition').querySelector('select[data-field="transition"]');
    changeValue(transition, 'cut');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(2), 'transition', 'cut']);
    listItems(ctx)[0].fire('click');
    const background = openSection(ctx, 'background').querySelector('select[data-field="background"]');
    changeValue(background, 'studio');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'background', 'studio']);
    const density = section(ctx, 'background').querySelector('select[data-field="style_background"]');
    assert.deepEqual(density.children.map(o => o.textContent), ['Same as the lesson', 'Rich']);
    changeValue(density, 'rich');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'style_background', 'rich']);
    const accent = openSection(ctx, 'style').querySelector('select[data-field="style_accent"]');
    changeValue(accent, 'teal');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'style_accent', 'teal']);
    // "Same as the lesson" gives a scene's own choice back
    ctx.scenes[0].visual_review = { composition: { status: 'changed', overrides: { style_accent: 'teal' } } };
    ctx.ws.render();
    const again = section(ctx, 'style').querySelector('select[data-field="style_accent"]');
    assert.equal(again.value, 'teal');
    assert.equal(section(ctx, 'style').querySelector('[data-field-box="style_accent"] .editor-state').textContent, '✎ Edited');
    changeValue(again, 'default');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'style_accent', 'auto']);
    // the lesson style settings
    ctx.click(section(ctx, 'style').querySelector('[data-action="style-settings"]'));
    assert.equal(ctx.named('openStyleSettings').length, 1);
    // the same value again: nothing sent
    const count = ctx.modelCalls.length;
    changeValue(section(ctx, 'camera').querySelector('select[data-field="camera"]'), 'auto');
    assert.equal(ctx.modelCalls.length, count);
});

test('Visual: choose from the library / remove (asks first) go to the page; size and side are overrides', async () => {
    const ctx = setup();
    const sec = () => openSection(ctx, 'visual');
    assert.equal(sec().querySelector('[data-visual]').textContent, 'Now: 🖼 Picture');
    ctx.click(sec().querySelector('[data-action="choose-visual"]'));
    await tick();
    assert.deepEqual(ctx.named('chooseVisual'), [['chooseVisual', 0, 'side']]);
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['visual']);
    ctx.click(sec().querySelector('[data-action="remove-visual"]'));
    assert.match(ctx.ws.confirmBox.textContent, /play without this visual/);
    ctx.ws.confirmBox.querySelector('[data-action="confirm-cancel"]').fire('click');
    await tick();
    assert.equal(ctx.named('removeVisual').length, 0);
    ctx.click(sec().querySelector('[data-action="remove-visual"]'));
    ctx.ws.confirmBox.querySelector('[data-action="confirm-ok"]').fire('click');
    await tick();
    assert.deepEqual(ctx.named('removeVisual'), [['removeVisual', 0, 'side']]);
    changeValue(sec().querySelector('select[data-field="visual_size"]'), 'dominant');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'visual_size', 'dominant']);
    changeValue(sec().querySelector('select[data-field="visual_position"]'), 'left');
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'visual_position', 'left']);
    // a cancelled choice changes nothing
    const ctx2 = setup({ adapter: { chooseVisual: async () => false } });
    ctx2.click(openSection(ctx2, 'visual').querySelector('[data-action="choose-visual"]'));
    await tick();
    assert.equal(ctx2.named('changed').length, 0);
});

test('Captions: the lesson on / off and this scene off, through the model', () => {
    const ctx = setup();
    const sec = () => openSection(ctx, 'captions');
    assert.equal(sec().querySelector('[data-action="lesson-captions"]').getAttribute('aria-pressed'), 'true');
    ctx.click(sec().querySelector('[data-action="scene-captions"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['setCaptions', ID(1), 'off']);
    assert.equal(ctx.scenes[0].edit.captions, 'off');
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['captions']);
    assert.equal(sec().querySelector('[data-action="scene-captions"]').getAttribute('aria-pressed'), 'true');
    assert.equal(ctx.ws.timeline.querySelectorAll('[data-track="caption"] .editor-tl-block')[0].getAttribute('data-state'), 'off');
    ctx.click(sec().querySelector('[data-action="scene-captions"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['setCaptions', ID(1), null]);
    ctx.click(sec().querySelector('[data-action="lesson-captions"]'));
    assert.deepEqual(ctx.modelCalls.at(-1), ['setLessonCaptions', false]);
    assert.deepEqual(ctx.model.editor.captions, { visible: false });
    assert.equal(ctx.named('changed').at(-1)[1].scene_id, null);
    assert.ok(sec().querySelector('[data-action="scene-captions"]').getAttribute('disabled') !== null, 'nothing to hide per scene');
    assert.deepEqual(ctx.ws.timeline.querySelectorAll('[data-track="caption"] .editor-tl-block').map(b => b.getAttribute('data-state')), ['off', 'off', 'hidden', 'off']);
    assert.equal(ctx.ws.timeline.querySelectorAll('[data-track="caption"] .editor-tl-block')[0].textContent, 'Captions off');
    assert.equal(sec().querySelector('[data-action="lesson-captions"]').textContent, 'Captions off for the lesson');
    assert.match(sec().textContent, /caption size/);
});

// ---- undo / redo -------------------------------------------------------------------------------------------------------

test('Undo / Redo buttons: labels and tooltips from the model; an undone composition choice goes back to the page', async () => {
    const ctx = setup();
    const undo = () => ctx.$('[data-action="undo"]');
    const redo = () => ctx.$('[data-action="redo"]');
    ctx.click(section(ctx, 'scene').querySelector('[data-action="toggle-hidden"]'));
    assert.equal(undo().getAttribute('disabled'), null);
    assert.equal(undo().getAttribute('aria-label'), 'Undo hide scene');
    assert.equal(undo().getAttribute('title'), 'Undo hide scene (Ctrl+Z)');
    ctx.click(undo());
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'undo').length, 1);
    assert.equal((ctx.scenes[0].edit || {}).hidden, undefined);
    assert.ok(undo().getAttribute('disabled') !== null);
    assert.equal(redo().getAttribute('aria-label'), 'Redo hide scene');
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['history']);
    ctx.click(redo());
    assert.equal(ctx.scenes[0].edit.hidden, true);
    // a composition choice undone: the previous value ("auto") is sent through the page
    openSection(ctx, 'camera');
    changeValue(section(ctx, 'camera').querySelector('select[data-field="camera"]'), 'static');
    await tick();
    ctx.click(undo());
    await tick();
    assert.deepEqual(ctx.named('applyComposition').at(-1)[1], { kind: 'composition', scene_id: ID(1), overrides: { camera: 'auto' }, via: 'undo' });
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['composition']);
    ctx.click(redo());
    await tick();
    assert.deepEqual(ctx.named('applyComposition').at(-1)[1].overrides, { camera: 'static' });
    // undoing a delete brings the scene back and selects it
    listItems(ctx)[1].fire('click');
    ctx.click(section(ctx, 'scene').querySelector('[data-action="delete"]'));
    ctx.ws.confirmBox.querySelector('[data-action="confirm-ok"]').fire('click');
    await tick();
    assert.equal(ctx.model.indexOf(ID(2)), -1);
    ctx.click(undo());
    assert.equal(ctx.model.indexOf(ID(2)), 1);
    assert.equal(ctx.ws.selectedId, ID(2));
});

// ---- the save state, the version, the quality ----------------------------------------------------------------------

test('save state labels follow the autosave: Saved, Saving…, Unsaved changes, Couldn\'t save — Retry, changed elsewhere', () => {
    const ctx = setup();
    const save = () => ctx.$('.editor-save');
    assert.equal(save().textContent, '✓ Saved');
    // the top bar is updated in place: a focused or pressed button is never replaced by a save-state change
    const buttons = ['undo', 'redo', 'quality', 'preview', 'save-version', 'help', 'close'].map(a => ctx.$(`[data-action="${a}"]`));
    ctx.autosave.set('saving');
    assert.deepEqual(['undo', 'redo', 'quality', 'preview', 'save-version', 'help', 'close'].map(a => ctx.$(`[data-action="${a}"]`)), buttons);
    assert.equal(save().textContent, '⟳ Saving…');
    assert.equal(save().getAttribute('data-state'), 'saving');
    ctx.autosave.set('unsaved');
    assert.equal(save().textContent, '● Unsaved changes');
    ctx.autosave.set('error', { error: 'the server could not be reached (TypeError: Failed to fetch)', status: null });
    // plain words: what happened, that the edits are safe, what to do; the raw error text only with debug
    assert.match(save().textContent, /^✕ Couldn't saveRetryYour edits are still here\. Check the connection, then Retry/);
    assert.ok(!save().textContent.includes('Failed to fetch'), 'no raw error text');
    assert.equal(save().getAttribute('data-state'), 'error');
    ctx.click(save().querySelector('[data-action="retry-save"]'));
    assert.ok(ctx.autosave.calls.includes('retry'));
    // structural edits wait while pictures are being made (a locked save)
    ctx.autosave.set('error', { error: 'generating', status: 409, locked: true });
    assert.match(save().textContent, /Pictures or clips are still being made.*Your edits are still here\./);
    // signed out
    ctx.autosave.set('error', { error: 'Not authenticated', status: 401 });
    assert.match(save().textContent, /sign in again\. Your edits are still here/);
    assert.ok(!save().textContent.includes('Not authenticated'));
    ctx.autosave.set('conflict', { saved: true, conflicts: [{ type: 'text', scene_id: ID(2), reason: 'missing', applied: false, label: 'Edit title' }] });
    assert.match(save().textContent, /Changed elsewhere/);
    assert.match(save().textContent, /your edits were applied to the newer version\. 1 could not be applied: Edit title\./);
    assert.equal(save().querySelector('[data-action="retry-save"]'), null, 'saved: nothing to retry');
    ctx.autosave.set('conflict', { saved: false, error: 'stale' });
    assert.match(save().textContent, /your edits were not saved/);
    assert.match(save().textContent, /They are still here: Retry applies them to the newer version\./);
    assert.ok(save().querySelector('[data-action="retry-save"]'));
    assert.ok(!save().textContent.includes('stale'), 'no raw error text');
    ctx.autosave.set('saved');
    assert.equal(save().textContent, '✓ Saved');
    // with ?visualDebug the error's own text follows the plain words
    const dbg = setup({ adapter: { debug: true } });
    dbg.autosave.set('error', { error: 'TypeError: Failed to fetch' });
    assert.match(dbg.$('.editor-save').textContent, /Your edits are still here\..*\(TypeError: Failed to fetch\)$/);
});

test('the page\'s own onState keeps working while the editor is open, and is given back on close', () => {
    const autosave = fakeAutosave();
    const seen = [];
    const pageOnState = state => seen.push(state);
    autosave.onState = pageOnState;
    const ctx = setup({ autosave });
    autosave.set('saving');
    assert.deepEqual(seen, ['saving']);
    assert.equal(ctx.$('.editor-save').textContent, '⟳ Saving…');
    ctx.ws.close();
    assert.equal(autosave.onState, pageOnState);
});

test('Save as a copy calls the page (a new lesson, edited from then on) and reports the result', async () => {
    const ctx = setup();
    // Phase 21: the page saves a NEW lesson (/save-history) and goes on editing it, so the button says so
    const button = ctx.$('[data-action="save-version"]');
    assert.equal(button.textContent, 'Save as a copy');
    assert.equal(button.getAttribute('title'),
        'Saves a copy of this lesson in Your lessons and goes on editing the copy (videos already exported stay with the original)');
    assert.doesNotMatch(button.getAttribute('title'), /history|version/i);
    ctx.click('[data-action="save-version"]');
    assert.equal(ctx.$('[data-action="save-version"]').textContent, 'Saving a copy…');
    assert.ok(ctx.$('[data-action="save-version"]').getAttribute('disabled') !== null);
    await tick();
    assert.equal(ctx.named('saveVersion').length, 1);
    assert.equal(ctx.$('.editor-top-message').textContent, '✓ Copy saved in Your lessons. You are now editing the copy; the original keeps its videos.');
    assert.equal(ctx.$('[data-action="save-version"]').textContent, 'Save as a copy');
    const failing = setup({ adapter: { saveVersion: async () => { throw new Error('offline'); } } });
    failing.click('[data-action="save-version"]');
    await tick();
    await tick();
    // plain words (what happened, the edits are safe, what to do), never the exception's own text
    assert.equal(failing.$('.editor-top-message').textContent,
        '✕ The copy could not be saved. Your edits are safe in the editor: please try “Save as a copy” again in a moment.');
    assert.equal(failing.$('.editor-top-message').getAttribute('data-kind'), 'error');
    assert.equal(failing.$('[data-action="save-version"]').getAttribute('disabled'), null, 'it can be tried again');
    // with ?visualDebug the exception's text is added
    const debug = setup({ adapter: { debug: true, saveVersion: async () => { throw new Error('offline'); } } });
    debug.click('[data-action="save-version"]');
    await tick();
    await tick();
    assert.match(debug.$('.editor-top-message').textContent, /✕ The copy could not be saved\..* \(offline\)$/);
});

test('quality: the top summary and the scene chips use an icon and words; the inspector lists the findings', () => {
    const report = quality([
        { severity: 'warning', message: 'The title is <b>long</b>.', scene: 0, rule: 'text.title_long' },
        { severity: 'error', message: 'The narration is cut.', scene: 1, rule: 'timing.cut' },
        { severity: 'notice', message: 'Small text.', scene: 1, rule: 'text.small' },
        { severity: 'info', message: 'Just so you know.', scene: 0 },
        { severity: 'bogus', message: 'ignored', scene: 0 },
        { severity: 'warning', message: '', scene: 0 }
    ]);
    const ctx = setup({ report });
    const top = ctx.$('[data-action="quality"]');
    assert.equal(top.textContent, '✕ 3 things to review');
    assert.equal(top.getAttribute('data-status'), 'attention');
    ctx.click(top);
    assert.deepEqual(ctx.named('showInReview'), [['showInReview', null]]);
    const chips = listItems(ctx).map(i => i.querySelector('.editor-quality-chip'));
    assert.equal(chips[0].textContent, '⚠ 1 · Please check');
    assert.equal(chips[0].getAttribute('data-severity'), 'warning');
    assert.equal(chips[1].textContent, '✕ 2 · Needs fixing');
    assert.equal(chips[2], null);
    // the inspector's Quality section: severity in words, message as text, "Show in Visual Review"
    const sec = openSection(ctx, 'quality');
    assert.equal(sec.querySelector('.editor-section-summary').textContent, '⚠ Please check (1)');
    const findings = sec.querySelectorAll('.editor-finding');
    assert.deepEqual(findings.map(f => f.querySelector('.editor-finding-severity').textContent), ['⚠ Please check', 'ℹ Noted']);
    assert.equal(findings[0].querySelector('.editor-finding-message').textContent, 'The title is <b>long</b>.');
    assert.equal(sec.querySelector('.editor-finding-rule'), null, 'no rule names without debug');
    ctx.click(sec.querySelector('[data-action="show-in-review"]'));
    assert.deepEqual(ctx.named('showInReview').at(-1), ['showInReview', 0]);
    // good / not checked / stale
    assert.equal(setup({ report: quality([{ severity: 'info', message: 'fine', scene: 0 }]) }).$('[data-action="quality"]').textContent, '✓ Good');
    assert.equal(setup({ report: quality([{ severity: 'notice', message: 'a', scene: 0 }]) }).$('[data-action="quality"]').textContent, '⚠ 1 thing to review');
    const stale = setup({ report, adapter: { qualityStale: () => true } });
    assert.equal(stale.$('[data-action="quality"]').textContent, '◌ Quality: check again');
    assert.equal(stale.ws.sceneList.querySelector('.editor-quality-chip'), null, 'stale findings are not pinned to scenes');
});

// ---- targeted quality (findings by scene id; only a changed scene is stale) ------------------------------------------

// The page's side of targeted quality: the last check's findings mapped to scene ids; a scene the editor changed (not just
// moved) is stale until the next check; lesson-level findings apart
function targetedQuality({ counts = null } = {}) {
    const state = {
        byId: {
            [ID(1)]: [{ severity: 'warning', message: 'The title is long.', scene: 0, rule: 'text.title_long' }],
            [ID(2)]: [{ severity: 'error', message: 'The narration is cut.', scene: 1 }, { severity: 'notice', message: 'Small text.', scene: 1 },
                { severity: 'bogus', message: 'ignored' }]
        },
        lesson: [{ severity: 'warning', message: 'The lesson has no recap <b>scene</b>.', scene: null }, { severity: 'info', message: 'Checked 4 scenes.', scene: null }],
        changed: new Set(),
        runs: 0,
        asked: []
    };
    const adapter = {
        qualityFor: id => { state.asked.push(id); return { stale: state.changed.has(id), issues: state.byId[id] || [] }; },
        qualityLesson: () => ({ stale: false, issues: state.lesson, counts }),
        qualityRun: async () => { state.runs += 1; state.changed.clear(); },
        changed: ({ scene_id, kinds }) => { if (scene_id && !kinds.includes('order')) state.changed.add(scene_id); },
        // the older path must not be used when qualityFor is there
        quality: () => { throw new Error('the whole report is not read'); }
    };
    return { state, adapter };
}

const chip = item => item.querySelector('.editor-quality-chip');
const blockMark = block => block.querySelector('.editor-tl-quality');

test('targeted quality: a reorder keeps a scene\'s findings on that scene (list chip, timeline mark, inspector)', () => {
    const { adapter } = targetedQuality();
    const ctx = setup({ adapter });
    assert.equal(chip(listItems(ctx)[0]).textContent, '⚠ 1 · Please check');
    assert.equal(chip(listItems(ctx)[1]).textContent, '✕ 2 · Needs fixing');
    assert.equal(chip(listItems(ctx)[1]).getAttribute('data-severity'), 'error');
    assert.equal(chip(listItems(ctx)[3]), null, 'no findings, no chip');
    assert.equal(blockMark(sceneBlocks(ctx)[0]).textContent, '⚠ Please check (1)');
    assert.equal(blockMark(sceneBlocks(ctx)[1]).textContent, '✕ Needs fixing (2)');
    assert.match(sceneBlocks(ctx)[1].getAttribute('aria-label'), /quality: 2 things to look at \(Needs fixing\)$/);
    // move "Roots" to the start: its findings move with it
    listItems(ctx)[1].fire('click');
    press(ctx.doc, 'ArrowLeft', { alt: true });
    assert.deepEqual(ctx.scenes.map(sc => sc.scene_id).slice(0, 2), [ID(2), ID(1)]);
    assert.equal(listItems(ctx)[0].querySelector('.editor-scene-title').textContent, 'Roots');
    assert.equal(chip(listItems(ctx)[0]).textContent, '✕ 2 · Needs fixing');
    assert.equal(chip(listItems(ctx)[1]).textContent, '⚠ 1 · Please check');
    assert.equal(blockMark(sceneBlocks(ctx)[0]).textContent, '✕ Needs fixing (2)');
    assert.equal(blockMark(sceneBlocks(ctx)[1]).textContent, '⚠ Please check (1)');
    const sec = openSection(ctx, 'quality');
    assert.equal(sec.querySelector('.editor-section-summary').textContent, '✕ Needs fixing (2)');
    assert.deepEqual(sec.querySelectorAll('.editor-finding').map(f => f.textContent), ['✕ Needs fixingThe narration is cut.', '⚠ Worth a lookSmall text.']);
    // the whole-report path was never read (it would have shown "Something went wrong")
    assert.equal(ctx.$('.editor-top-message').textContent, '');
});

test('targeted quality: an edit marks only that scene stale; "Check again" runs the check and shows the findings again', async () => {
    const { state, adapter } = targetedQuality();
    const ctx = setup({ adapter });
    changeValue(section(ctx, 'timing').querySelector('input[data-field="min_seconds"]'), '15');
    await tick();
    assert.deepEqual([...state.changed], [ID(1)]);
    // only scene 1 is stale: its chip and mark say so in words; scene 2 keeps its findings
    assert.equal(chip(listItems(ctx)[0]).textContent, '◌ Changed since the check');
    assert.equal(chip(listItems(ctx)[0]).getAttribute('data-stale'), 'true');
    assert.equal(chip(listItems(ctx)[1]).textContent, '✕ 2 · Needs fixing');
    assert.equal(blockMark(sceneBlocks(ctx)[0]).textContent, '◌ Changed');
    assert.equal(blockMark(sceneBlocks(ctx)[1]).textContent, '✕ Needs fixing (2)');
    // the inspector on the stale scene: no old findings, "Changed since the last check" + "Check again"
    let sec = openSection(ctx, 'quality');
    assert.equal(sec.querySelector('.editor-section-summary').textContent, '◌ Changed');
    assert.equal(sec.querySelector('[data-quality="stale"]').textContent, '◌ Changed since the last check');
    assert.equal(sec.querySelector('.editor-finding'), null);
    const again = sec.querySelector('[data-action="quality-run"]');
    assert.equal(again.textContent, 'Check again');
    again.fire('click');
    assert.equal(section(ctx, 'quality').querySelector('[data-action="quality-run"]').textContent, 'Checking…');
    assert.ok(section(ctx, 'quality').querySelector('[data-action="quality-run"]').getAttribute('disabled') !== null);
    await tick();
    assert.equal(state.runs, 1);
    sec = section(ctx, 'quality');
    assert.equal(sec.querySelector('[data-quality="stale"]'), null);
    assert.equal(sec.querySelector('.editor-finding').textContent, '⚠ Please checkThe title is long.');
    assert.equal(chip(listItems(ctx)[0]).textContent, '⚠ 1 · Please check');
    assert.equal(ctx.$('.editor-top-message').textContent, '✓ Quality checked again.');
    // the scene the user did not touch was never stale
    listItems(ctx)[1].fire('click');
    assert.equal(section(ctx, 'quality').querySelector('[data-quality="stale"]'), null);
    // a failing check says so
    const failing = setup({ adapter: { ...adapter, qualityRun: async () => { throw new Error('offline'); } } });
    state.changed.add(ID(1));
    failing.ws.render();
    openSection(failing, 'quality').querySelector('[data-action="quality-run"]').fire('click');
    await tick();
    assert.equal(failing.$('.editor-top-message').textContent, '✕ The quality could not be checked. Your edits are safe: try “Check again” in a moment.');
    assert.ok(!failing.$('.editor-top-message').textContent.includes('offline'), 'no raw error text');
    assert.equal(section(failing, 'quality').querySelector('[data-action="quality-run"]').getAttribute('disabled'), null, 'it can be tried again');
});

test('targeted quality: the top bar sums the lesson-level and scene findings and shows the lesson-level ones', async () => {
    const { state, adapter } = targetedQuality();
    const ctx = setup({ adapter });
    // no counts from the page: the lesson's own finding (1) + scene 1 (1) + scene 2 (2)
    assert.equal(ctx.$('[data-action="quality"]').textContent, '✕ 4 things to review');
    assert.equal(ctx.$('[data-action="quality"]').getAttribute('data-status'), 'attention');
    const note = ctx.$('.editor-quality-lesson');
    assert.equal(note.getAttribute('hidden'), null);
    assert.equal(note.textContent, '⚠ Please check: The lesson has no recap <b>scene</b>. (+1 more)');
    assert.equal(note.getAttribute('data-severity'), 'warning');
    assert.equal(note.getAttribute('title'), '⚠ Please check: The lesson has no recap <b>scene</b>.\nℹ Noted: Checked 4 scenes.');
    assert.equal(ctx.ws.root.querySelectorAll('b').length, 0, 'lesson text stays text');
    // an edited scene's old findings are left out of the count, and the summary says a scene changed
    changeValue(section(ctx, 'timing').querySelector('input[data-field="min_seconds"]'), '15');
    await tick();
    assert.equal(ctx.$('[data-action="quality"]').textContent, '✕ 3 things to review · 1 changed since the check');
    // the page's own counts win
    const counted = setup({ adapter: targetedQuality({ counts: { notice: 0, warning: 1, error: 0, blocking: 0 } }).adapter });
    assert.equal(counted.$('[data-action="quality"]').textContent, '⚠ 1 thing to review');
    const good = setup({ adapter: { ...targetedQuality().adapter, qualityLesson: () => ({ stale: false, issues: [], counts: { notice: 0, warning: 0, error: 0, blocking: 0 } }) } });
    assert.equal(good.$('[data-action="quality"]').textContent, '✓ Good');
    assert.ok(good.$('.editor-quality-lesson').getAttribute('hidden') !== null);
    const unchecked = setup({ adapter: { ...targetedQuality().adapter, qualityLesson: () => null, qualityFor: () => null } });
    assert.equal(unchecked.$('[data-action="quality"]').textContent, '◌ Quality not checked');
    assert.equal(unchecked.ws.sceneList.querySelector('.editor-quality-chip'), null);
    assert.equal(openSection(unchecked, 'quality').querySelector('[data-action="quality-run"]').textContent, 'Check quality');
    assert.ok(state.asked.length > 0);
});

test('targeted quality: rule names only with debug; the older whole-report path is the fallback', () => {
    const { adapter } = targetedQuality();
    const plainCtx = setup({ adapter });
    assert.equal(openSection(plainCtx, 'quality').querySelector('.editor-finding-rule'), null);
    const debugCtx = setup({ adapter: { ...adapter, debug: true } });
    assert.equal(openSection(debugCtx, 'quality').querySelector('.editor-finding-rule').textContent, ' text.title_long');
    // without qualityFor: quality() / qualityStale() as before (findings by position, all stale together)
    const report = quality([{ severity: 'warning', message: 'Check this.', scene: 0 }]);
    const legacy = setup({ report });
    assert.equal(chip(listItems(legacy)[0]).textContent, '⚠ 1 · Please check');
    assert.equal(blockMark(sceneBlocks(legacy)[0]).textContent, '⚠ Please check (1)');
    const stale = setup({ report, adapter: { qualityStale: () => true, qualityRun: async () => {} } });
    assert.equal(stale.$('[data-action="quality"]').textContent, '◌ Quality: check again');
    assert.equal(stale.ws.sceneList.querySelector('.editor-quality-chip'), null);
    assert.equal(blockMark(sceneBlocks(stale)[0]), null);
    const sec = openSection(stale, 'quality');
    assert.equal(sec.querySelector('[data-quality="stale"]').textContent, '◌ The lesson changed since its quality was checked.');
    assert.ok(sec.querySelector('[data-action="quality-run"]'));
});

// ---- markers ------------------------------------------------------------------------------------------------------------

test('scene list markers: approval, Hidden (dimmed), Edited, Moved, New, all as icon + words', async () => {
    const ctx = setup();
    const marks = i => listItems(ctx)[i].querySelectorAll('.editor-mark').map(m => m.textContent);
    assert.deepEqual(marks(0), ['✓ Approved']);
    assert.deepEqual(marks(1), ['● Needs review']);
    assert.deepEqual(marks(2), ['⊘ Hidden']);
    assert.ok(listItems(ctx)[2].classList.contains('is-hidden'));
    assert.match(listItems(ctx)[2].querySelector('.editor-scene-meta').textContent, /not played/);
    assert.equal(ctx.ws.sceneCount.textContent, '4 · 1 hidden');
    // a screen reader hears the markers too
    assert.equal(listItems(ctx)[2].getAttribute('aria-label'), 'Scene 3: Hidden extra, Content, not played; Hidden');
    assert.match(listItems(ctx)[0].getAttribute('aria-label'), /^Scene 1: Plants <b>and<\/b> light, Chapter card, \d+(\.\d)? s; Approved$/);
    // move scene 4 to the start: it is the one marked moved (the others kept their order)
    listItems(ctx)[3].fire('click');
    ctx.click(section(ctx, 'scene').querySelector('[data-action="move-start"]'));
    assert.deepEqual(marks(0), ['⇄ Moved']);
    assert.deepEqual(marks(1), ['✓ Approved']);
    // an edit marks the scene edited
    changeValue(section(ctx, 'timing').querySelector('input[data-field="min_seconds"]'), '12');
    await tick();
    assert.deepEqual(marks(0), ['⇄ Moved', '✎ Edited']);
    // a duplicate is new
    ctx.click(section(ctx, 'scene').querySelector('[data-action="duplicate"]'));
    assert.ok(marks(1).includes('＋ New'));
    // statuses are never colour alone: each mark carries words
    listItems(ctx).forEach(item => item.querySelectorAll('.editor-mark').forEach(m => assert.match(m.textContent, /[A-Za-z]/)));
});

// ---- keyboard ---------------------------------------------------------------------------------------------------------

test('keyboard shortcuts while the editor is open; the page\'s slide keys never see them', async () => {
    const ctx = setup();
    const { doc } = ctx;
    let ev = press(doc, ' ');
    assert.deepEqual(ctx.named('play'), [['play']]);
    assert.ok(ev.stopped && ev.prevented);
    press(doc, ' ');
    assert.deepEqual(ctx.named('pause'), [['pause']]);
    ev = press(doc, 'ArrowRight');
    assert.ok(ev.stopped && ev.prevented);
    assert.equal(ctx.ws.selectedId, ID(2));
    assert.deepEqual(ctx.named('seek').at(-1), ['seek', 1]);
    press(doc, 'ArrowLeft');
    assert.equal(ctx.ws.selectedId, ID(1));
    // undo / redo
    ctx.click(section(ctx, 'scene').querySelector('[data-action="toggle-hidden"]'));
    ev = press(doc, 'z', { ctrl: true });
    assert.ok(ev.stopped && ev.prevented);
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'undo').length, 1);
    press(doc, 'Z', { ctrl: true, shift: true });
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'redo').length, 1);
    press(doc, 'z', { meta: true });
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'undo').length, 2);
    press(doc, 'y', { ctrl: true });
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'redo').length, 2);
    // other browser shortcuts stay the browser's
    ev = press(doc, 'c', { ctrl: true });
    assert.equal(ev.stopped, false);
    // B / T / H (page shortcuts) are kept out
    for (const key of ['b', 't', 'h', 'B']) assert.equal(press(doc, key).stopped, true, key);
    // Delete asks first
    ev = press(doc, 'Delete');
    assert.ok(ev.stopped);
    assert.equal(ctx.ws.confirmBox.getAttribute('hidden'), null);
    // while the confirmation is open, other keys do nothing; Escape cancels it
    press(doc, 'ArrowRight');
    assert.equal(ctx.ws.selectedId, ID(1));
    press(doc, 'Escape');
    await tick();
    assert.ok(ctx.ws.confirmBox.getAttribute('hidden') !== null);
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'remove').length, 0);
    // Escape: the inspector first, then the editor
    press(doc, 'Escape');
    assert.equal(ctx.ws.inspectorOpen, false);
    assert.ok(ctx.ws.inspector.getAttribute('hidden') !== null);
    assert.equal(ctx.ws.root.getAttribute('data-inspector'), 'closed');
    assert.equal(ctx.ws.isOpen, true);
    press(doc, 'Escape');
    assert.equal(ctx.ws.isOpen, false);
    // closed: the listener is gone, keys reach the page again
    assert.equal(doc.listeners.filter(l => l.type === 'keydown').length, 0);
});

test('no shortcut while typing in an input, a textarea, a select or an editable area', () => {
    const ctx = setup();
    const { doc } = ctx;
    const targets = [
        ctx.ws.inspector.querySelector('input[data-field="title"]'),
        ctx.ws.inspector.querySelector('textarea[data-field="narration"]'),
        ctx.ws.inspector.querySelector('select[data-field="split-point"]'),
        Object.assign(doc.createElement('div'), {}),
        Object.assign(doc.createElement('div'), { isContentEditable: true })
    ];
    targets[3].setAttribute('contenteditable', 'true');
    for (const target of targets) {
        for (const [key, opts] of [[' ', {}], ['ArrowRight', {}], ['ArrowLeft', { alt: true }], ['Delete', {}], ['z', { ctrl: true }], ['y', { ctrl: true }]]) {
            const ev = press(doc, key, { ...opts, target });
            assert.equal(ev.prevented, false, `${key} in ${target.tag}`);
        }
    }
    assert.equal(ctx.named('play').length, 0);
    assert.equal(ctx.named('seek').length, 0);
    assert.equal(ctx.modelCalls.filter(c => ['undo', 'redo', 'move', 'remove'].includes(c[0])).length, 0);
    assert.ok(ctx.ws.confirmBox.getAttribute('hidden') !== null);
    // the chrome itself stops keys typed inside it before the page's slide shortcuts (bubbling)
    const ev = { key: 'ArrowRight', stopped: false, stopPropagation() { this.stopped = true; } };
    ctx.ws.root.listeners.keydown.forEach(fn => fn(ev));
    assert.equal(ev.stopped, true);
    // Escape in a field leaves the field (its typing session ends), it does not close anything
    const title = ctx.ws.inspector.querySelector('input[data-field="title"]');
    title.fire('focus');
    title.value = 'Plants';
    title.fire('input');
    const esc = press(doc, 'Escape', { target: title });
    assert.equal(esc.stopped, true);
    assert.equal(ctx.model.inTransaction, false);
    assert.equal(ctx.ws.inspectorOpen, true);
    assert.equal(ctx.ws.isOpen, true);
});

test('keys are left alone while another panel (Asset Library, Visual Review, export) is open on top', () => {
    const ctx = setup();
    const overlay = ctx.doc.createElement('div');
    overlay.className = 'asset-overlay review-overlay open';
    ctx.doc.body.appendChild(overlay);
    const ev = press(ctx.doc, 'Escape');
    assert.equal(ev.stopped, false);
    assert.equal(ctx.ws.isOpen, true);
    assert.equal(press(ctx.doc, ' ').stopped, false);
    assert.equal(ctx.named('play').length, 0);
});

test('Space on a focused button presses the button (no play / pause), still kept from the page', () => {
    const ctx = setup();
    const button = ctx.$('[data-action="save-version"]');
    const ev = press(ctx.doc, ' ', { target: button });
    assert.equal(ev.stopped, true);
    assert.equal(ev.prevented, false);
    assert.equal(ctx.named('play').length, 0);
});

test('the "?" button and key show the shortcuts', () => {
    const ctx = setup();
    const help = ctx.ws.help;
    assert.ok(help.getAttribute('hidden') !== null);
    ctx.click('[data-action="help"]');
    assert.equal(help.getAttribute('hidden'), null);
    assert.equal(ctx.$('[data-action="help"]').getAttribute('aria-expanded'), 'true');
    const text = help.textContent;
    for (const words of ['Play / pause', 'Previous / next scene', 'Undo', 'Redo', 'Delete the selected scene', 'Close the inspector']) assert.ok(text.includes(words), words);
    press(ctx.doc, 'Escape');
    assert.ok(help.getAttribute('hidden') !== null, 'Escape closes the help first');
    assert.equal(ctx.ws.inspectorOpen, true);
    press(ctx.doc, '?');
    assert.equal(help.getAttribute('hidden'), null);
});

// ---- reordering -------------------------------------------------------------------------------------------------------

test('reorder with the keyboard: Alt+ArrowRight / Alt+ArrowLeft move the selected scene (one command each)', () => {
    const ctx = setup();
    let ev = press(ctx.doc, 'ArrowRight', { alt: true });
    assert.ok(ev.stopped && ev.prevented);
    assert.deepEqual(ctx.modelCalls.at(-1), ['move', ID(1), 1]);
    assert.deepEqual(ctx.scenes.map(s => s.scene_id), [ID(2), ID(1), ID(3), ID(4)]);
    assert.equal(ctx.ws.selectedId, ID(1), 'the moved scene stays selected');
    assert.equal(listItems(ctx)[1].getAttribute('aria-current'), 'true');
    press(ctx.doc, 'ArrowLeft', { alt: true });
    assert.deepEqual(ctx.scenes.map(s => s.scene_id), [ID(1), ID(2), ID(3), ID(4)]);
    // at the start: nothing to do
    const count = ctx.modelCalls.length;
    press(ctx.doc, 'ArrowLeft', { alt: true });
    assert.equal(ctx.modelCalls.length, count);
    assert.equal(ctx.model.undoStack.length, 2);
});

test('reorder by dragging a timeline block: one move when the drag ends, the autosave held meanwhile', () => {
    const ctx = setup();
    const blocks = sceneBlocks(ctx);
    const px = s => parseInt(s, 10);
    const startX = px(blocks[0].style.left) + 5;
    const target = px(blocks[3].style.left) + px(blocks[3].style.width) - 2; // past the middle of the last block
    pointer(blocks[0], 'pointerdown', { clientX: startX, clientY: 5 });
    pointer(blocks[0], 'pointermove', { clientX: startX + 2, clientY: 5 });
    assert.deepEqual(ctx.autosave.calls, [], 'a few pixels is still a press');
    pointer(blocks[0], 'pointermove', { clientX: target, clientY: 5 });
    assert.deepEqual(ctx.autosave.calls, ['hold']);
    assert.ok(blocks[0].classList.contains('is-dragging'));
    assert.equal(ctx.ws.tlDrop.getAttribute('hidden'), null, 'the drop place is shown');
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'move').length, 0, 'nothing changes while dragging');
    pointer(blocks[0], 'pointerup', { clientX: target, clientY: 5 });
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'move'), [['move', ID(1), 3]]);
    assert.deepEqual(ctx.scenes.map(s => s.scene_id), [ID(2), ID(3), ID(4), ID(1)]);
    assert.equal(ctx.model.undoStack.length, 1, 'one undo step');
    assert.deepEqual(ctx.autosave.calls, ['hold', 'schedule', 'release']);
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['order']);
    // the click after a drag does not select / seek
    blocks[0].fire('click');
    assert.equal(ctx.named('seek').length, 0);
    // a plain press is a click (select), never a move
    const again = sceneBlocks(ctx);
    pointer(again[1], 'pointerdown', { clientX: px(again[1].style.left) + 3, clientY: 5 });
    pointer(again[1], 'pointerup', { clientX: px(again[1].style.left) + 3, clientY: 5 });
    again[1].fire('click');
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'move').length, 1);
    assert.equal(ctx.ws.selectedId, ID(3));
});

test('reorder by dragging in the scene list; a cancelled drag changes nothing', () => {
    const ctx = setup();
    let items = listItems(ctx);
    items.forEach((item, i) => { item.rect = { left: 0, top: i * 50, width: 200, height: 40 }; });
    pointer(items[3], 'pointerdown', { clientX: 10, clientY: 170 });
    pointer(items[3], 'pointermove', { clientX: 10, clientY: 30 });
    assert.equal(items[1].getAttribute('data-drop'), 'before', 'lands before scene 2');
    pointer(items[3], 'pointerup', { clientX: 10, clientY: 30 });
    assert.deepEqual(ctx.modelCalls.filter(c => c[0] === 'move'), [['move', ID(4), 1]]);
    assert.deepEqual(ctx.scenes.map(s => s.scene_id), [ID(1), ID(4), ID(2), ID(3)]);
    assert.deepEqual(ctx.autosave.calls, ['hold', 'schedule', 'release']);
    items = listItems(ctx);
    items.forEach((item, i) => { item.rect = { left: 0, top: i * 50, width: 200, height: 40 }; });
    pointer(items[0], 'pointerdown', { clientX: 10, clientY: 10 });
    pointer(items[0], 'pointermove', { clientX: 10, clientY: 160 });
    pointer(items[0], 'pointercancel', {});
    assert.equal(ctx.modelCalls.filter(c => c[0] === 'move').length, 1);
    assert.equal(ctx.autosave.calls.filter(c => c === 'release').length, 2, 'released after a cancelled drag too');
    assert.equal(listItems(ctx).filter(i => i.getAttribute('data-drop')).length, 0);
});

test('the playhead: dragged as UI state only, snapped to a scene boundary on release, then the stage seeks', () => {
    const ctx = setup();
    const tl = ctx.model.timeline();
    const ruler = ctx.ws.ruler;
    const undo = ctx.model.undoStack.length;
    const blocks = sceneBlocks(ctx);
    const px = s => parseInt(s, 10);
    // press inside scene 1 near its end, drag into scene 2 a little after its start
    pointer(ruler, 'pointerdown', { clientX: px(blocks[0].style.left) + 20 });
    assert.ok(ctx.ws.time > 0);
    pointer(ruler, 'pointermove', { clientX: px(blocks[1].style.left) + 6 });
    assert.ok(ctx.ws.time > tl.scenes[1].start);
    assert.match(ctx.ws.timeLabel.textContent, / \/ /);
    assert.equal(ctx.named('seek').length, 0, 'no seek while dragging');
    pointer(ruler, 'pointerup', { clientX: px(blocks[1].style.left) + 6 });
    assert.equal(ctx.ws.time, tl.scenes[1].start, 'snapped to the start of scene 2');
    assert.deepEqual(ctx.named('seek'), [['seek', 1]]);
    assert.equal(ctx.ws.selectedId, ID(2));
    assert.equal(ctx.model.undoStack.length, undo, 'never a model change');
    assert.equal(ctx.autosave.calls.length, 0);
    // the handle on the playhead drags the same way
    const handle = ctx.ws.playhead.firstElementChild;
    pointer(handle, 'pointerdown', { clientX: 1 });
    pointer(handle, 'pointerup', { clientX: 1 });
    assert.deepEqual(ctx.named('seek').at(-1), ['seek', 0]);
});

// ---- preview, debug, text safety, close -------------------------------------------------------------------------------

test('Preview hides the chrome to see the whole frame; Escape or "Back to editing" brings it back', () => {
    const ctx = setup();
    ctx.click('[data-action="preview"]');
    assert.ok(ctx.ws.root.classList.contains('is-previewing'));
    assert.equal(ctx.$('[data-action="preview"]').getAttribute('aria-pressed'), 'true');
    press(ctx.doc, 'Escape');
    assert.equal(ctx.ws.root.classList.contains('is-previewing'), false);
    assert.equal(ctx.ws.inspectorOpen, true, 'Escape only left the preview');
    ctx.click('[data-action="preview"]');
    ctx.click('[data-action="preview-exit"]');
    assert.equal(ctx.ws.root.classList.contains('is-previewing'), false);
});

test('no internal ids, fingerprints or rule names unless adapter.debug', () => {
    const report = quality([{ severity: 'warning', message: 'Check this.', scene: 0, rule: 'text.title_long' }]);
    const plainCtx = setup({ report });
    ['quality', 'presenter', 'visual', 'camera', 'background', 'style', 'captions'].forEach(key => openSection(plainCtx, key));
    let text = plainCtx.ws.root.textContent;
    // (the transition into scene 2, and scene 2's hidden presenter line)
    listItems(plainCtx)[1].fire('click');
    openSection(plainCtx, 'transition');
    text += plainCtx.ws.root.textContent;
    for (const secret of [ID(1), ID(2), 'feedfacecafebeef', 'abc123def456', 'text.title_long', 'aadhi-teacher', 'ASSET', 'PROCEDURAL']) {
        assert.ok(!text.includes(secret), `"${secret}" shown without debug`);
    }
    assert.equal(plainCtx.ws.inspector.querySelector('[data-section="debug"]'), null);
    const debugCtx = setup({ report, adapter: { debug: true } });
    openSection(debugCtx, 'quality');
    const dbg = debugCtx.ws.inspector.querySelector('[data-section="debug"]');
    assert.ok(dbg);
    assert.ok(dbg.textContent.includes(ID(1)));
    assert.ok(dbg.textContent.includes('feedfacecafebeef'));
    assert.equal(debugCtx.ws.inspector.querySelector('.editor-finding-rule').textContent, ' text.title_long');
});

test('markup in lesson text stays text (titles, narration, the lesson title); nothing is written as HTML', () => {
    const scenes = lesson();
    scenes[0].title = '<img src=x onerror="alert(1)">Evil';
    scenes[0].narration = '<script>alert(2)</script> Narration';
    const ctx = setup({ scenes, report: quality([{ severity: 'warning', message: '<b>bold</b> finding', scene: 0 }]) });
    openSection(ctx, 'quality');
    assert.equal(listItems(ctx)[0].querySelector('.editor-scene-title').textContent, '<img src=x onerror="alert(1)">Evil');
    assert.equal(sceneBlocks(ctx)[0].querySelector('.editor-tl-block-title').textContent, '1. <img src=x onerror="alert(1)">Evil');
    assert.equal(ctx.ws.inspector.querySelector('textarea[data-field="narration"]').value, '<script>alert(2)</script> Narration');
    assert.equal(ctx.ws.root.querySelectorAll('img').length, 0);
    assert.equal(ctx.ws.root.querySelectorAll('script').length, 0);
    assert.equal(ctx.ws.root.querySelectorAll('b').length, 0);
    assert.equal(ctx.ws.root.all().filter(n => n._html !== undefined).length, 0, 'innerHTML never used');
});

test('close() removes the chrome and body.editor-active, gives the player bar back, saves pending edits', () => {
    const ctx = setup();
    ctx.click(section(ctx, 'scene').querySelector('[data-action="toggle-hidden"]'));
    assert.equal(ctx.model.dirty, true);
    const root = ctx.ws.root;
    ctx.click('[data-action="close"]');
    assert.equal(ctx.ws.isOpen, false);
    assert.equal(ctx.doc.body.classList.contains('editor-active'), false);
    assert.equal(ctx.doc.body.children.includes(root), false);
    assert.equal(ctx.doc.querySelector('.editor-root'), null);
    assert.equal(ctx.doc.listeners.length, 0);
    assert.ok(ctx.autosave.calls.includes('flush'));
    assert.deepEqual(ctx.named('closed'), [['closed']]);
    // a later model change does not draw anything
    ctx.model.setHidden(ID(1), false);
    assert.equal(ctx.doc.querySelector('.editor-root'), null);
    // it opens again
    ctx.ws.open();
    assert.ok(ctx.doc.querySelector('.editor-root'));
    assert.ok(ctx.doc.body.classList.contains('editor-active'));
    ctx.ws.close();
    // closing an unchanged lesson saves nothing
    const clean = setup();
    clean.ws.close();
    assert.equal(clean.autosave.calls.includes('flush'), true, 'new ids still need saving: the model is dirty after normalizing');
});

test('a change from elsewhere (a reload and replay after a stale save) redraws the editor', () => {
    const ctx = setup();
    const fresh = lesson();
    fresh[1].title = 'Roots (updated elsewhere)';
    ctx.model.replay([], fresh, { revision: 'r2' });
    assert.equal(listItems(ctx)[1].querySelector('.editor-scene-title').textContent, 'Roots (updated elsewhere)');
});

test('the CSS keeps the stage: fixed panels, the player bar hidden only while editing, responsive and calm', () => {
    const css = require('fs').readFileSync(require('path').join(__dirname, '..', 'editor.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    assert.match(css, /body\.editor-active \.voice-control-bar\s*\{\s*display: none !important;/);
    assert.match(css, /\.editor-root \{[^}]*position: fixed;[^}]*display: grid;/s);
    assert.match(css, /\.editor-stage-hole \{[^}]*pointer-events: none;/s);
    assert.match(css, /@media \(max-width: 1100px\)/);
    assert.match(css, /@media \(max-width: 700px\)/);
    assert.match(css, /prefers-reduced-motion/);
    assert.match(css, /:focus-visible/);
    // the stage's own CSS is never touched
    for (const sel of ['#presentation-board', '.dynamic-side-zone', '.cinematic-scene-container']) assert.ok(!css.includes(sel), sel);
    // Phase 21: the stage note shows at 1280 px or less only (where the panels cover the scene), at the hole's top edge, lets the
    // pointer through, and never shows in Preview or a recording
    assert.match(css, /\.editor-stage-note \{[^}]*display: none;[^}]*position: absolute;[^}]*pointer-events: none;/s);
    assert.match(css, /@media \(max-width: 1280px\) \{\s*\.editor-stage-note \{\s*display: block;/);
    assert.match(css, /\.editor-root\.is-previewing \.editor-stage-note,\s*body\[data-recording\] \.editor-stage-note,[^{]*\{\s*display: none !important;/);
});

// ---- Phase 21: the inspector follows the scene ------------------------------------------------------------------------

// A lesson with one scene of each kind: presenter-led, formula, code (its presenter hidden), plain text without a
// composition yet, code without a composition, a picture drawn into the board
function kinds() {
    const small = [{ id: 'presenter', type: 'presenter', box: { x: 0.8, y: 0.6, w: 0.15, h: 0.3 } }];
    return [
        { scene_id: ID(1), type: 'content', title: 'Why plants matter', html: '<p>Almost every breath.</p>', narration: 'Think about this breath.',
          cinematic_plan: plan({ template: 'presenter_explanation', template_label: 'Presenter + explanation' }) },
        { scene_id: ID(2), type: 'content', title: "Newton's second law", html: "<div class='formula-block'>\\[F = ma\\]</div>", narration: 'F equals m times a.',
          cinematic_plan: plan({ template: 'formula_focus', template_label: 'Formula focus', layers: small }) },
        { scene_id: ID(3), type: 'content', title: 'A loop', html: '<pre><code>for i in range(3):\n    print(i)</code></pre>', narration: 'Here is a loop.',
          cinematic_plan: plan({ template: 'code_focus', template_label: 'Code focus', presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: false, side: 'right' } }) },
        { scene_id: ID(4), type: 'content', title: 'Plain words', html: '<p>Just text.</p>', narration: 'Only words here.' },
        { scene_id: ID(5), type: 'content', title: 'Code, not laid out yet', html: '<pre><code>x = 1</code></pre>', narration: 'One line of code.' },
        { scene_id: ID(6), type: 'content', title: 'A drawn picture', html: '<p>Look</p><img src="leaf.png">', narration: 'Look at the leaf.' }
    ];
}

const order = ctx => ctx.ws.inspector.querySelectorAll('.editor-section').map(s => s.getAttribute('data-section'));
const isOpen = (ctx, key) => !!section(ctx, key).querySelector('.editor-section-body');

test('inspectorLayout / sceneFocus: the lead section first, what cannot apply left out (pure functions)', () => {
    const s = kinds();
    assert.deepEqual(s.map(sc => UI.sceneFocus(sc)), ['presenter', 'formula', 'code', 'text', 'code', 'visual']);
    assert.deepEqual(UI.inspectorLayout(s[0], 3).order,
        ['scene', 'presenter', 'narration', 'text', 'timing', 'captions', 'quality', 'camera', 'transition', 'background', 'style']);
    assert.deepEqual(UI.inspectorLayout(s[1], 1).order.slice(0, 6), ['scene', 'text', 'narration', 'timing', 'presenter', 'captions']);
    const code = UI.inspectorLayout(s[2], 2);
    assert.equal(code.presenter, 'hidden');
    assert.equal(code.lead, 'text');
    // no composition yet: no presenter, no "Look and motion"
    assert.deepEqual(UI.inspectorLayout(s[3], 3).order, ['scene', 'narration', 'text', 'timing', 'captions', 'quality']);
    assert.deepEqual(UI.inspectorLayout(s[4], 4).order, ['scene', 'text', 'narration', 'timing', 'captions', 'quality']);
    assert.deepEqual(UI.inspectorLayout(s[5], 5).order, ['scene', 'visual', 'narration', 'text', 'timing', 'captions', 'quality']);
    // the first scene: no transition before it, unless one was chosen for it (so it can be given back)
    assert.ok(!UI.inspectorLayout(s[0], 0).order.includes('transition'));
    const chosen = { ...s[0], visual_review: { composition: { status: 'changed', overrides: { transition: 'cut' } } } };
    assert.ok(UI.inspectorLayout(chosen, 0).order.includes('transition'));
    // a presenter-led layout whose presenter is hidden does not lead with it
    const hiddenLead = { ...s[0], visual_review: { composition: { status: 'changed', overrides: { presenter_position: 'hidden' } } } };
    assert.equal(UI.sceneFocus(hiddenLead), 'text');
    assert.equal(UI.hasVisualSlot(lesson()[0]), true);
    assert.equal(UI.hasVisualSlot(s[5]), false);
});

test('the inspector shows each scene\'s own settings: presenter-led, formula, code, plain text, a drawn picture', () => {
    const ctx = setup({ scenes: kinds(), adapter: { currentScene: () => 0 } });
    const fact = () => ctx.ws.inspector.querySelector('[data-fact="layout"]').textContent;
    // presenter-led: Presenter first (after Scene) and open; no Visual section (the scene has none)
    assert.deepEqual(order(ctx).slice(0, 5), ['scene', 'presenter', 'narration', 'text', 'timing']);
    assert.ok(isOpen(ctx, 'presenter'));
    assert.equal(section(ctx, 'visual'), null);
    assert.equal(fact(), 'Presenter + explanation');
    assert.equal(ctx.ws.inspector.getAttribute('data-focus'), 'presenter');
    assert.ok(section(ctx, 'presenter').querySelector('[data-key="presenter_position"][data-value="left"]'));
    // formula: Text first (title, subtitle, labels); the small presenter's section collapsed after Timing
    listItems(ctx)[1].fire('click');
    assert.deepEqual(order(ctx).slice(0, 5), ['scene', 'text', 'narration', 'timing', 'presenter']);
    assert.ok(section(ctx, 'text').querySelector('textarea[data-field="labels"]'));
    assert.equal(isOpen(ctx, 'presenter'), false);
    assert.equal(section(ctx, 'presenter').querySelector('.editor-section-summary').textContent, 'Small · Right');
    assert.equal(fact(), 'Formula focus');
    // code: Text first; the hidden presenter is one line with "Show presenter" (no position / size controls)
    listItems(ctx)[2].fire('click');
    assert.equal(order(ctx)[1], 'text');
    const row = section(ctx, 'presenter');
    assert.ok(row.classList.contains('editor-section-compact'));
    assert.equal(row.querySelector('.editor-section-head'), null);
    assert.equal(row.querySelector('[data-key="presenter_size"]'), null);
    assert.equal(row.querySelector('[data-action="show-presenter"]').textContent, '👤 Show presenter');
    assert.match(row.textContent, /Hidden in this scene/);
    assert.equal(row.querySelector('.editor-state').textContent, 'Automatic');
    // "Look and motion" last, collapsed
    const heads = ctx.ws.inspector.querySelectorAll('.editor-group-head, .editor-section').map(n => n.getAttribute('data-section') || 'LOOK');
    assert.deepEqual(heads.slice(-5), ['LOOK', 'camera', 'transition', 'background', 'style']);
    for (const key of ['camera', 'transition', 'background', 'style']) assert.equal(isOpen(ctx, key), false, key);
    // a scene not laid out yet: no presenter, no layout settings, nothing irrelevant
    listItems(ctx)[3].fire('click');
    assert.deepEqual(order(ctx), ['scene', 'narration', 'text', 'timing', 'captions', 'quality']);
    assert.equal(ctx.ws.inspector.querySelector('.editor-group-head'), null);
    assert.equal(fact(), 'Text');
    // a picture drawn into the board: Visual leads, without "Choose from library" (nothing the Asset Library can replace)
    listItems(ctx)[5].fire('click');
    assert.equal(order(ctx)[1], 'visual');
    assert.ok(isOpen(ctx, 'visual'));
    assert.equal(section(ctx, 'visual').querySelector('[data-action="choose-visual"]'), null);
    assert.match(section(ctx, 'visual').textContent, /insert a picture scene/);
    assert.equal(section(ctx, 'visual').querySelector('select[data-field="visual_size"]'), null, 'no layout yet: no size / side');
});

test('"Show presenter" on a hidden presenter: a visible side through the composition review; "Automatic" when the user hid it', async () => {
    const ctx = setup();
    // scene 2: the composer hid the presenter (its plan's side: left)
    listItems(ctx)[1].fire('click');
    const show = () => section(ctx, 'presenter').querySelector('[data-action="show-presenter"]');
    assert.equal(section(ctx, 'presenter').querySelector('[data-value="auto"]'), null, 'not the user\'s choice: no "Automatic"');
    ctx.click(show());
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(2), 'presenter_position', 'left']);
    assert.deepEqual(ctx.named('applyComposition').at(-1)[1].overrides, { presenter_position: 'left' });
    assert.deepEqual(ctx.named('changed').at(-1)[1].kinds, ['composition']);
    // the page's review route answered: the presenter shows, its whole section is back (side and size)
    ctx.scenes[1].cinematic_plan.presenter.shown = true;
    ctx.scenes[1].visual_review = { composition: { status: 'changed', overrides: { presenter_position: 'left' } } };
    ctx.ws.render();
    assert.ok(section(ctx, 'presenter').querySelector('.editor-section-head'));
    assert.equal(section(ctx, 'presenter').querySelector('[data-action="show-presenter"]'), null);
    assert.equal(openSection(ctx, 'presenter').querySelector('[data-key="presenter_position"][data-value="left"]').getAttribute('aria-pressed'), 'true');
    // a formula-free text scene (no lead) without the HTML heuristics: the default order
    assert.equal(UI.sceneFocus({ scene_id: ID(9), type: 'content', html: '<p>F = ma in words</p>' }), 'text');
    assert.equal(UI.sceneFocus({ scene_id: ID(9), type: 'content', html: "<div class='formula-block'>\\(E = mc^2\\)</div>" }), 'formula');
    // scene 1: the user hid it (an override): "✎ Edited", "Automatic", and "Show presenter" puts it on the plan's side
    listItems(ctx)[0].fire('click');
    ctx.scenes[0].visual_review = { composition: { status: 'changed', overrides: { presenter_position: 'hidden' } } };
    ctx.ws.render();
    assert.equal(section(ctx, 'presenter').querySelector('.editor-state').textContent, '✎ Edited');
    assert.ok(section(ctx, 'presenter').querySelector('[data-action="override"][data-key="presenter_position"][data-value="auto"]'));
    ctx.click(show());
    await tick();
    assert.deepEqual(ctx.modelCalls.at(-1), ['setOverride', ID(1), 'presenter_position', 'right']);
    // hidden by its size: the size goes back to automatic (the side the composer gave stays)
    ctx.scenes[0].visual_review = { composition: { status: 'changed', overrides: { presenter_size: 'hidden' } } };
    ctx.ws.render();
    const before = ctx.modelCalls.length;
    ctx.click(show());
    await tick();
    assert.deepEqual(ctx.modelCalls.slice(before).filter(c => c[0] === 'setOverride'), [['setOverride', ID(1), 'presenter_size', 'auto']]);
});

test('"Back to the Studio" when the editor was opened from the Studio (adapter.returnTo)', () => {
    const ctx = setup({ adapter: { returnTo: () => 'studio' } });
    const back = ctx.$('[data-action="close"]');
    assert.equal(back.textContent, '‹Back to the Studio');
    assert.equal(back.getAttribute('aria-label'), 'Back to the Studio (closes the editor)');
    assert.match(back.getAttribute('aria-label'), new RegExp(back.querySelector('.editor-label-wide').textContent), 'the visible words are in its name');
    ctx.click(back);
    assert.equal(ctx.ws.isOpen, false);
    assert.deepEqual(ctx.named('closed'), [['closed']]);
});

test('"Show / Hide inspector" in the transport row (Escape closes it; nothing else could bring it back before)', () => {
    const ctx = setup();
    const toggle = () => ctx.ws.timeline.querySelector('[data-action="toggle-inspector"]');
    ctx.click(toggle());
    assert.equal(ctx.ws.inspectorOpen, false);
    assert.equal(ctx.ws.root.getAttribute('data-inspector'), 'closed');
    assert.ok(ctx.ws.inspector.getAttribute('hidden') !== null);
    assert.equal(toggle().textContent, 'Show inspector');
    ctx.click(toggle());
    assert.equal(ctx.ws.inspectorOpen, true);
    assert.equal(ctx.ws.inspector.getAttribute('hidden'), null);
    assert.equal(toggle().textContent, 'Hide inspector');
    // the transport row is always there, before the tracks (editor.css keeps it above them)
    const kids = ctx.ws.timeline.children.map(c => c.className);
    assert.equal(kids[0], 'editor-tl-bar');
    assert.ok(kids.indexOf('editor-tl-scroll') > kids.indexOf('editor-tl-sheet-note'));
});

test('errors in plain words: a page function that throws, a model command that throws (the details only with debug)', () => {
    const throwing = { lessonTitle: () => { throw new Error('TypeError: x is undefined at index.html:8690'); } };
    const ctx = setup({ adapter: throwing });
    const message = ctx.$('.editor-top-message').textContent;
    assert.equal(message, '✕ That did not work. Your edits are safe: please try again.');
    assert.ok(!message.includes('TypeError'));
    assert.equal(ctx.$('.editor-title').textContent, 'Untitled lesson');
    const debug = setup({ adapter: { ...throwing, debug: true } });
    assert.match(debug.$('.editor-top-message').textContent, /please try again\. \(TypeError: x is undefined at index\.html:8690\)$/);
    // a model command that throws (a bug): nothing changed, said plainly
    const m = setup();
    m.model.setHidden = () => { throw new Error('Cannot read properties of undefined'); };
    m.click(section(m, 'scene').querySelector('[data-action="toggle-hidden"]'));
    assert.equal(m.$('.editor-top-message').textContent, '✕ That change could not be made. Nothing was changed and your other edits are safe: please try again.');
    assert.equal(m.named('changed').length, 0);
    // a composition the page could not apply
    return (async () => {
        const c = setup({ adapter: { applyComposition: async () => { throw new Error('HTTP 500 Internal Server Error'); } } });
        openSection(c, 'camera');
        changeValue(section(c, 'camera').querySelector('select[data-field="camera"]'), 'static');
        await tick();
        assert.equal(c.$('.editor-top-message').textContent, '✕ The layout change could not be made. Your other edits are safe: please try again in a moment.');
    })();
});

// ---- Phase 21: focus (the workspace and its question are dialogs) -----------------------------------------------------

// The fake DOM has no focus(): one that records document.activeElement, for the length of a test
async function withFocus(fn) {
    const had = Object.prototype.hasOwnProperty.call(Node.prototype, 'focus');
    const previous = Node.prototype.focus;
    Node.prototype.focus = function focus() { this.doc.activeElement = this; };
    try {
        return await fn();
    } finally {
        if (had) Node.prototype.focus = previous;
        else delete Node.prototype.focus;
    }
}

test('focus: the editor starts on the selected scene and gives the focus back to its opener on close', () => withFocus(() => {
    const ctx = setup({ open: false });
    const opener = ctx.doc.createElement('button');
    ctx.doc.body.appendChild(opener);
    opener.focus();
    ctx.ws.open();
    assert.equal(ctx.doc.activeElement, listItems(ctx)[0], 'keyboard users start on the selected scene');
    ctx.click('[data-action="close"]');
    assert.equal(ctx.doc.activeElement, opener);
}));

test('focus: a danger question starts on "Cancel", stays inside while open (Tab), a press outside cancels, focus comes back', () => withFocus(async () => {
    const ctx = setup();
    const del = section(ctx, 'scene').querySelector('[data-action="delete"]');
    del.focus();
    ctx.click(del);
    const box = ctx.ws.confirmBox;
    const cancel = box.querySelector('[data-action="confirm-cancel"]');
    const ok = box.querySelector('[data-action="confirm-ok"]');
    assert.equal(ctx.doc.activeElement, cancel, 'the least destructive answer has the keyboard');
    assert.ok(ok.classList.contains('editor-btn-danger'));
    assert.equal(ctx.ws.confirmBackdrop.getAttribute('hidden'), null);
    // Tab cycles between the two answers
    let ev = press(ctx.doc, 'Tab', { target: cancel });
    assert.equal(ev.prevented, false, 'Cancel → OK: the browser moves on');
    ok.focus();
    ev = press(ctx.doc, 'Tab', { target: ok });
    assert.equal(ev.prevented, true);
    assert.equal(ctx.doc.activeElement, cancel, 'past the last answer: back to the first');
    ev = press(ctx.doc, 'Tab', { target: cancel, shift: true });
    assert.equal(ctx.doc.activeElement, ok);
    // a redraw of the chrome while asking keeps the question (and its focus)
    ctx.ws.render();
    assert.equal(box.querySelector('[data-action="confirm-ok"]'), ok);
    // a press on the backdrop is "Cancel"
    ctx.ws.confirmBackdrop.fire('click');
    await tick();
    assert.ok(box.getAttribute('hidden') !== null);
    assert.ok(ctx.ws.confirmBackdrop.getAttribute('hidden') !== null);
    assert.equal(ctx.scenes.length, 4, 'nothing deleted');
    assert.equal(ctx.doc.activeElement.getAttribute('data-action'), 'delete', 'the focus is back where the question came from');
}));

test('focus: Tab never leaves the workspace (a modal dialog); the shortcuts move the focus in and back out', () => withFocus(() => {
    const ctx = setup();
    const items = ctx.ws.focusables(ctx.ws.root);
    assert.ok(items.length > 20);
    assert.ok(!items.some(n => n.getAttribute('data-action') === 'preview-exit'), 'hidden controls are skipped');
    assert.ok(!items.some(n => n.getAttribute('disabled') !== null), 'disabled controls are skipped');
    items[items.length - 1].focus();
    let ev = press(ctx.doc, 'Tab', { target: items[items.length - 1] });
    assert.equal(ev.prevented, true);
    assert.equal(ctx.doc.activeElement, items[0]);
    ev = press(ctx.doc, 'Tab', { target: items[0], shift: true });
    assert.equal(ctx.doc.activeElement, items[items.length - 1]);
    // from outside the workspace (the page behind): into it
    ctx.doc.activeElement = ctx.doc.body;
    press(ctx.doc, 'Tab');
    assert.equal(ctx.doc.activeElement, items[0]);
    // in the middle: the browser's own order
    items[3].focus();
    assert.equal(press(ctx.doc, 'Tab', { target: items[3] }).prevented, false);
    // the shortcuts: "Close" gets the focus; Escape gives it back to the "?" button
    const help = ctx.$('[data-action="help"]');
    help.focus();
    ctx.click(help);
    assert.equal(ctx.doc.activeElement, ctx.ws.help.querySelector('[data-action="help-close"]'));
    assert.equal(ctx.ws.help.getAttribute('aria-labelledby'), 'editor-help-title');
    press(ctx.doc, 'Escape');
    assert.equal(ctx.doc.activeElement, help);
    ctx.click(help);
    ctx.click(ctx.ws.help.querySelector('[data-action="help-close"]'));
    assert.ok(ctx.ws.help.getAttribute('hidden') !== null);
    assert.equal(ctx.doc.activeElement, help);
    // Preview: the focus goes to "Back to editing", then back to "Preview"
    ctx.click('[data-action="preview"]');
    assert.equal(ctx.doc.activeElement, ctx.ws.previewExit);
    assert.equal(ctx.ws.previewExit.getAttribute('hidden'), null);
    ctx.click('[data-action="preview-exit"]');
    assert.equal(ctx.doc.activeElement.getAttribute('data-action'), 'preview');
}));

// ---- Phase 21: the timeline at the product's widths -------------------------------------------------------------------

test('the timeline fits 1440, 1280, 1024 and 820 px: blocks fill the lane, ruler labels apart, transitions on the boundaries', () => {
    const scenes = lesson();
    scenes[3].narration = 'The recap says what we learned today.';
    scenes[3].cinematic_plan = plan({ transition: { in: 'crossfade', out: 'fade', duration: 0.6 } });
    const ctx = setup({ scenes });
    const px = s => parseInt(s, 10);
    for (const width of [1440, 1280, 1024, 820]) {
        // the timeline spans the whole width at every one of these sizes (editor.css: its grid area spans every column)
        ctx.ws.tlScroll.rect = { left: 0, top: 0, width, height: 170 };
        ctx.ws.renderTimeline();
        const blocks = sceneBlocks(ctx);
        const visible = blocks.filter(b => !b.classList.contains('is-hidden'));
        const span = visible.reduce((sum, b) => sum + px(b.style.width) + 2, 0);
        assert.ok(Math.abs(span - (width - 92 - 16)) <= 4, `${width}: the scenes fill the lane (${span})`);
        // ruler ticks at least ~70 px apart: their 12 px labels never touch
        const ticks = ctx.ws.timeline.querySelectorAll('.editor-tl-tick').map(t => px(t.style.left));
        ticks.slice(1).forEach((x, i) => assert.ok(x - ticks[i] >= 68, `${width}: ticks ${ticks[i]} → ${x}`));
        // each transition marker sits on its scene's left edge (editor.css gives the block's text room on both sides)
        ctx.ws.timeline.querySelectorAll('.editor-tl-transition').forEach(m => {
            const block = blocks.find(b => b.getAttribute('data-scene-id') === m.getAttribute('data-scene-id'));
            assert.equal(px(m.style.left), px(block.style.left), `${width}: the marker on the boundary`);
        });
        // every played block says its time (12 px text in a 40 px row)
        visible.forEach(b => assert.match(b.querySelector('.editor-tl-block-time').textContent, /\d s/));
    }
});

// ---- Phase 21: the CSS: shared tokens, 12 px text, no overlaps at any width ---------------------------------------------

// editor.css as rules: [{media, selector, body}] (one level of @media)
function cssRules() {
    const css = require('fs').readFileSync(require('path').join(__dirname, '..', 'editor.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const out = [];
    const walk = (text, media) => {
        let depth = 0;
        let from = 0;
        let selector = '';
        let start = 0;
        for (let i = 0; i < text.length; i++) {
            if (text[i] === '{') {
                if (depth === 0) { selector = text.slice(from, i).trim(); start = i + 1; }
                depth += 1;
            } else if (text[i] === '}') {
                depth -= 1;
                if (depth === 0) {
                    const body = text.slice(start, i);
                    if (selector.startsWith('@media')) walk(body, selector.replace('@media', '').trim());
                    else out.push({ media, selector, body });
                    from = i + 1;
                }
            }
        }
    };
    walk(css, null);
    return { css, rules: out };
}
const ruleOf = (rules, media, selector) => rules.find(r => r.media === media && r.selector.split(',').map(s => s.trim()).includes(selector));
const prop = (rule, name) => { const m = rule && new RegExp(`(?:^|[;\\s])${name}:\\s*([^;]+);`).exec(rule.body); return m ? m[1].trim() : null; };
const areas = rule => (prop(rule, 'grid-template-areas') || '').match(/"[^"]*"/g).map(row => row.slice(1, -1).trim().split(/\s+/));

test('the CSS uses the shared product tokens (never the lesson style), 12 px text or more, no all-caps, a focus ring', () => {
    const { css, rules } = cssRules();
    assert.ok(!/var\(--st-/.test(css), 'never the lesson style\'s tokens');
    assert.ok(!/var\(--text-gold|var\(--font-/.test(css), 'never the stage\'s gold or fonts');
    // the colour tokens are the shared ones
    const root = ruleOf(rules, null, '.editor-root');
    for (const name of ['bg', 'bg-2', 'bg-3', 'line', 'soft', 'text', 'strong', 'muted', 'gold', 'accent', 'select', 'playhead']) {
        assert.match(prop(root, `--ed-${name}`) || '', /^var\(--ui-[a-z0-9-]+\)$/, `--ed-${name}`);
    }
    assert.equal(prop(root, '--ed-gold'), 'var(--ui-primary)');
    assert.equal(prop(root, 'font-family'), 'var(--ui-font-sans)');
    // text 12 px or more: the shared type scale, or a size of at least 0.75rem / 12px
    const scale = { caption: 0.75, secondary: 0.82, body: 0.9, card: 0.95, section: 1.05, title: 1.5, mono: 0.8, display: 2.5 };
    const sizes = [...css.matchAll(/font-size:\s*([^;]+);/g)].map(m => m[1].trim());
    assert.ok(sizes.length > 30);
    sizes.forEach(size => {
        const token = /^var\(--ui-text-([a-z]+)\)$/.exec(size);
        if (token) return assert.ok(scale[token[1]] >= 0.75, size);
        const rem = /^([\d.]+)rem$/.exec(size);
        const pixels = /^([\d.]+)px$/.exec(size);
        assert.ok((rem && +rem[1] >= 0.75) || (pixels && +pixels[1] >= 12), `font-size ${size}`);
    });
    assert.ok(!/text-transform:\s*uppercase/.test(css), 'no all-caps');
    // every control: the shared focus ring; a timeline block's own ring outside its selected outline
    assert.equal(prop(ruleOf(rules, null, '.editor-root :focus-visible'), 'outline'), 'var(--ui-focus-width) solid var(--ui-focus)');
    assert.match(prop(ruleOf(rules, null, '.editor-tl-block:focus-visible'), 'outline'), /var\(--ui-focus\)/);
    // the selected scene is not shown by colour alone: an outline / a bar
    assert.ok(ruleOf(rules, null, '.editor-tl-block[aria-current="true"]::after'));
    assert.ok(ruleOf(rules, null, '.editor-scene[aria-current="true"]::before'));
    // the primary button is the product's gold, the danger one the product's danger colours
    assert.equal(prop(ruleOf(rules, null, '.editor-btn-primary'), 'background'), 'var(--ui-primary)');
    assert.equal(prop(ruleOf(rules, null, '.editor-btn-danger'), 'color'), 'var(--ui-danger)');
    assert.equal(prop(ruleOf(rules, null, '.editor-btn'), 'min-height'), 'var(--ui-control-md)');
    assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test('the CSS layout per width: no panel paints over another; the transport row always shows; the top bar fits', () => {
    const { rules } = cssRules();
    // the panels that hold their own layers are isolated (the tracks' labels / markers / playhead stay inside the timeline)
    for (const sel of ['.editor-timeline', '.editor-inspector', '.editor-scenes']) assert.equal(prop(ruleOf(rules, null, sel), 'isolation'), 'isolate', sel);
    // the inspector is never a fixed sheet laid over the timeline, at any width
    rules.filter(r => /\.editor-inspector\b/.test(r.selector)).forEach(r => assert.notEqual(prop(r, 'position'), 'fixed', `${r.media}: ${r.selector}`));
    // the transport row is never hidden
    rules.filter(r => /\.editor-tl-bar\b/.test(r.selector)).forEach(r => assert.notEqual(prop(r, 'display'), 'none', `${r.media}: ${r.selector}`));
    const distinct = (grid, a, b) => grid.every(row => !(row.includes(a) && row.includes(b)) || a === b);
    // wider than 1100 px (1440 / 1280) and 901-1100 px (1024): scene list | stage | inspector over the timeline
    const desktop = areas(ruleOf(rules, null, '.editor-root'));
    assert.deepEqual(desktop, [['top', 'top', 'top'], ['scenes', 'stage', 'inspector'], ['timeline', 'timeline', 'timeline']]);
    assert.ok(ruleOf(rules, '(max-width: 1100px)', '.editor-root'), 'narrower columns at 1024');
    assert.equal(prop(ruleOf(rules, '(max-width: 1100px)', '.editor-top .editor-label-mid'), 'display'), 'none', 'secondary labels become icons');
    // 701-900 px (820): the open inspector takes a row of its own below the timeline; the timeline keeps its ruler (scrub)
    // and scene row, the four detail tracks make way
    const tabletOpen = areas(ruleOf(rules, '(max-width: 900px)', '.editor-root[data-inspector="open"]'));
    assert.deepEqual(tabletOpen, [['top', 'top'], ['scenes', 'stage'], ['timeline', 'timeline'], ['inspector', 'inspector']]);
    assert.ok(distinct(tabletOpen, 'timeline', 'inspector'));
    assert.equal(prop(ruleOf(rules, '(max-width: 900px)', '.editor-root[data-inspector="open"] .editor-tl-row:not([data-track="ruler"]):not([data-track="scene"])'), 'display'), 'none');
    assert.ok(!rules.some(r => r.media === '(max-width: 900px)' && /\.editor-tl-scroll\b/.test(r.selector) && prop(r, 'display') === 'none'), 'the scrub stays');
    assert.equal(prop(ruleOf(rules, '(max-width: 900px)', '.editor-tl-scroll'), 'max-height'), '200px');
    assert.equal(prop(ruleOf(rules, '(max-width: 900px)', '.editor-root[data-inspector="open"] .editor-tl-sheet-note'), 'display'), 'block');
    const tabletClosed = areas(ruleOf(rules, '(max-width: 900px)', '.editor-root[data-inspector="closed"]'));
    assert.deepEqual(tabletClosed, [['top', 'top'], ['scenes', 'stage'], ['timeline', 'timeline']]);
    // ... and the top bar wraps to two rows (nothing runs off its right edge); 36 px touch targets
    assert.equal(prop(ruleOf(rules, '(max-width: 900px)', '.editor-top'), 'flex-wrap'), 'wrap');
    assert.equal(prop(ruleOf(rules, '(max-width: 900px)', '.editor-top-break'), 'flex'), '0 0 100%');
    assert.equal(prop(ruleOf(rules, '(max-width: 900px)', '.editor-timeline .editor-btn'), 'min-height'), 'var(--ui-control-md)');
    // 700 px or less (600): one column, the inspector below the scene list; header buttons 36 x 36
    const phoneOpen = areas(ruleOf(rules, '(max-width: 700px)', '.editor-root[data-inspector="open"]'));
    assert.deepEqual(phoneOpen, [['top'], ['timeline'], ['scenes'], ['inspector']]);
    const phoneTop = ruleOf(rules, '(max-width: 700px)', '.editor-top .editor-btn');
    assert.equal(prop(phoneTop, 'min-width'), 'var(--ui-control-md)');
    assert.equal(prop(phoneTop, 'min-height'), 'var(--ui-control-md)');
    // the top bar's own buttons are 36 px everywhere (the shared control size)
    assert.equal(prop(ruleOf(rules, null, '.editor-icon-btn'), 'min-width'), 'var(--ui-control-md)');
});

test('the CSS keeps a transition marker clear of the block titles; the rows hold 12 px text', () => {
    const { rules } = cssRules();
    const marker = ruleOf(rules, null, '.editor-tl-transition');
    const size = parseFloat(prop(marker, 'width'));
    const reach = (size * Math.SQRT2) / 2; // the rotated diamond's half diagonal, either side of the boundary
    const block = ruleOf(rules, null, '.editor-tl-row[data-track="scene"] .editor-tl-block');
    const [, right, , left] = prop(block, 'padding').split(/\s+/).map(v => parseFloat(v));
    assert.ok(left >= reach + 1, `the title starts after the marker (${left} px ≥ ${reach.toFixed(1)} + the border)`);
    assert.ok(right >= reach, `the previous block's text ends before it (${right} px)`);
    // the scene row holds a title line and a time line of 12 px (line height 1.2) inside its border
    const sceneRow = parseFloat(prop(ruleOf(rules, null, '.editor-tl-row[data-track="scene"]'), 'height'));
    assert.ok(sceneRow >= 2 * 12 * 1.2 + 2 + 4, `scene row ${sceneRow} px`);
    const row = parseFloat(prop(ruleOf(rules, null, '.editor-tl-row'), 'height'));
    assert.ok(row >= 12 * 1.2 + 4, `track row ${row} px`);
    // the track names: sentence case, 12 px, never under a lane (sticky, opaque, above the blocks)
    const label = ruleOf(rules, null, '.editor-tl-label');
    assert.equal(prop(label, 'font-size'), 'var(--ui-text-caption)');
    assert.equal(prop(label, 'position'), 'sticky');
    assert.equal(prop(label, 'background'), 'var(--ed-bg)');
    // the drop place while dragging is a visible line (3 px, gold)
    assert.match(prop(ruleOf(rules, null, '.editor-tl-drop'), 'border-left'), /^3px solid var\(--ed-gold\)$/);
});
