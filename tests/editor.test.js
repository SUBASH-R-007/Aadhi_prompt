'use strict';
// Unit tests for the Advanced Video Editor model (editor.js, Phase 19). Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('../editor.js');

const A = 'a'.repeat(32);
const ID = /^s-[0-9a-f]{12}$/;

// A small lesson: ids given, so the tests can name scenes
function lesson() {
    return [
        { scene_id: 's-000000000001', type: 'content', title: 'Intro', subtitle: 'Why bridges', html: '<p>Bridges</p>',
          narration: 'Bridges carry loads across gaps. [PAUSE] They spread the weight to the ground.',
          composition: { labels: ['load', 'span'] },
          cinematic_plan: { template: 'presenter_intro', duration: 6, review_status: 'approved', review_stale: true, transition: { in: 'fade', duration: 0.5 } },
          visual_plan: { side: { source: 'ASSET', asset_id: A, review_status: 'approved' } },
          presenter_plan: { presenter_id: 'aadhi', type: 'mascot' },
          visual_review: { composition: { status: 'approved' }, side: { status: 'approved' }, presenter: { status: 'approved' } } },
        { scene_id: 's-000000000002', type: 'content', title: 'Forces', html: '<p>Forces</p>', narration: 'Tension pulls and compression pushes.' },
        { scene_id: 's-000000000003', type: 'content', title: 'Arches', html: '<p>Arches</p>', narration: 'An arch turns weight into compression.' },
        { scene_id: 's-000000000004', type: 'quiz_checkpoint', title: 'Quiz', question: 'Which part is in tension?', narration: '' }
    ];
}

function model(options = {}) {
    const slides = options.slides || lesson();
    const editor = options.editor === undefined ? { version: 1, generated_order: slides.map(s => s.scene_id) } : options.editor;
    const m = new E.EditorModel({ scenes: slides, editor, revision: 'r1', projectId: 42, ...options.extra });
    return { m, slides, editor: m.editor };
}

const ids = slides => slides.map(s => s.scene_id);
const S1 = 's-000000000001';
const S2 = 's-000000000002';
const S3 = 's-000000000003';
const S4 = 's-000000000004';

// ---- ids --------------------------------------------------------------------------------------------------------

test('sceneId is "s-" + 12 lowercase hex, unique, with and without crypto', () => {
    const seen = new Set();
    for (let i = 0; i < 300; i++) {
        const id = E.sceneId();
        assert.match(id, ID);
        seen.add(id);
    }
    assert.equal(seen.size, 300);
    const desc = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
    Object.defineProperty(globalThis, 'crypto', { value: undefined, configurable: true, writable: true });
    try {
        assert.match(E.sceneId(), ID);  // Math.random fallback
    } finally {
        Object.defineProperty(globalThis, 'crypto', desc);
    }
    assert.match(E.sceneId(), ID);
});

test('ensureIds adds missing ids and fixes invalid and duplicated ones in place', () => {
    const scenes = [{ title: 'a' }, { scene_id: 's-00000000000a' }, { scene_id: 's-00000000000a' }, { scene_id: 'S-BAD' }, null, { scene_id: 's-00000000000b' }];
    const same = scenes;
    assert.equal(E.ensureIds(scenes), 3);
    assert.equal(scenes, same);
    assert.equal(scenes[1].scene_id, 's-00000000000a');  // the first keeps its id
    assert.equal(scenes[5].scene_id, 's-00000000000b');
    const all = scenes.filter(Boolean).map(s => s.scene_id);
    all.forEach(id => assert.match(id, ID));
    assert.equal(new Set(all).size, all.length);
    assert.equal(E.ensureIds(scenes), 0);
    assert.equal(E.ensureIds('nope'), 0);
});

test('an old lesson gets ids and its generated order when opened, and needs saving', () => {
    const slides = [{ title: 'a', narration: 'x' }, { title: 'b', narration: 'y' }];
    const editor = {};
    const m = new E.EditorModel({ scenes: slides, editor });
    assert.equal(m.scenes, slides);
    assert.equal(m.editor, editor);
    assert.deepEqual(m.normalized, { ids: 2, generated_order: true });
    assert.equal(editor.version, 1);
    assert.deepEqual(editor.generated_order, ids(slides));
    assert.equal(m.dirty, true);
    // a lesson that already has both is clean; a bad captions record is dropped; the order is set once
    const { m: m2, editor: ed2 } = model({ editor: { version: 1, generated_order: [S4, S3, 'junk', S3], captions: { visible: 'yes' } } });
    assert.equal(m2.dirty, false);
    assert.deepEqual(ed2.generated_order, [S4, S3]);
    assert.equal('captions' in ed2, false);
    assert.throws(() => new E.EditorModel({ scenes: null }), TypeError);
});

// ---- structure --------------------------------------------------------------------------------------------------

test('move, moveBy, moveToStart and moveToEnd splice the same array and undo / redo', () => {
    const { m, slides } = model();
    const same = slides;
    let r = m.move(S1, 2);
    assert.equal(r.ok, true);
    assert.equal(r.structure, true);
    assert.deepEqual(ids(slides), [S2, S3, S1, S4]);
    assert.equal(m.undoLabel(), 'Undo move scene');
    m.undo();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    assert.equal(m.redoLabel(), 'Redo move scene');
    m.redo();
    assert.deepEqual(ids(slides), [S2, S3, S1, S4]);
    r = m.moveBy(S1, -1);
    assert.deepEqual(ids(slides), [S2, S1, S3, S4]);
    m.moveToEnd(S2);
    assert.deepEqual(ids(slides), [S1, S3, S4, S2]);
    m.moveToStart(S4);
    assert.deepEqual(ids(slides), [S4, S1, S3, S2]);
    assert.equal(m.move(S4, 99).index, 3);  // clamped
    assert.deepEqual(ids(slides), [S1, S3, S2, S4]);
    assert.equal(m.move(S4, 3).changed, false);  // already there: nothing recorded
    assert.equal(m.move('s-ffffffffffff', 0).ok, false);
    assert.equal(m.move(S1, 'x').ok, false);
    assert.equal(m.moveBy(S1, 1.5).ok, false);
    assert.equal(slides, same);
    while (m.canUndo()) m.undo();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    assert.equal(slides, same);
});

test('duplicate gets a new id, origin and from, and none of the approvals; undo removes it', () => {
    const { m, slides } = model();
    const same = slides;
    const r = m.duplicate(S1);
    assert.equal(r.ok, true);
    assert.equal(slides, same);
    assert.equal(slides.length, 5);
    const copy = slides[1];
    assert.equal(r.scene, copy);
    assert.equal(r.new_scene_id, copy.scene_id);
    assert.match(copy.scene_id, ID);
    assert.notEqual(copy.scene_id, S1);
    assert.deepEqual(copy.edit, { origin: 'duplicated', from: S1 });
    assert.equal('visual_review' in copy, false);
    assert.equal('review_status' in copy.cinematic_plan, false);
    assert.equal('review_stale' in copy.cinematic_plan, false);
    assert.equal('review_status' in copy.visual_plan.side, false);
    assert.equal(copy.cinematic_plan.template, 'presenter_intro');  // the plans themselves are kept
    assert.equal(copy.visual_plan.side.asset_id, A);
    assert.notEqual(copy.composition, slides[0].composition);  // a deep copy
    assert.equal(slides[0].visual_review.composition.status, 'approved');  // the original keeps its approvals
    const eff = m.effective();
    assert.deepEqual(eff[1].approvals, { composition: 'pending', main: null, side: 'pending', presenter: 'pending' });
    assert.equal(eff[1].origin, 'duplicated');
    assert.equal(eff[1].from, S1);
    assert.equal(m.undoLabel(), 'Undo duplicate scene');
    m.undo();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    m.redo();
    assert.equal(slides[1].scene_id, copy.scene_id);
    assert.equal(slides.length, 5);
});

test('remove returns the removed scene, keeps the array, and undo puts it back where it was', () => {
    const { m, slides } = model();
    const same = slides;
    const scene = slides[1];
    const r = m.remove(S2);
    assert.equal(r.ok, true);
    assert.equal(r.scene, scene);
    assert.equal(slides, same);
    assert.deepEqual(ids(slides), [S1, S3, S4]);
    assert.equal(m.undoLabel(), 'Undo delete scene');
    m.undo();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    assert.deepEqual(slides[1], scene);
    m.redo();
    assert.deepEqual(ids(slides), [S1, S3, S4]);
    const single = new E.EditorModel({ scenes: [{ scene_id: S1, title: 'only' }], editor: {} });
    assert.equal(single.remove(S1).ok, false);  // a lesson keeps at least one scene
    assert.equal(m.remove('nope').ok, false);
});

test('remove refuses the last scene that plays (hidden ones can still go)', () => {
    const { m, slides } = model();
    m.setHidden(S2, true);
    m.setHidden(S3, true);
    m.setHidden(S4, true);
    const r = m.remove(S1);
    assert.equal(r.ok, false);
    assert.equal(r.error, 'A lesson needs at least one scene that plays.');
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    assert.equal(m.remove(S3).ok, true);  // a hidden scene
    assert.deepEqual(ids(slides), [S1, S2, S4]);
    m.setHidden(S2, false);
    assert.equal(m.remove(S1).ok, true);  // another scene plays now
    assert.deepEqual(ids(slides), [S2, S4]);
});

test('insert adds blank, picture and pasted scenes with new ids; undo / redo', () => {
    const { m, slides } = model();
    const same = slides;
    let r = m.insert(1, 'blank', { title: 'Recap <b>', text: 'Tom & <Jerry>' });
    assert.equal(r.ok, true);
    assert.equal(slides, same);
    const blank = slides[1];
    assert.match(blank.scene_id, ID);
    assert.deepEqual({ ...blank, scene_id: 'x' }, { scene_id: 'x', type: 'content', title: 'Recap <b>', html: '<p>Tom &amp; &lt;Jerry&gt;</p>', narration: '',
        edit: { origin: 'inserted' } });
    r = m.insert(99, 'asset', { asset_id: A, title: 'Bridge photo' });
    assert.equal(r.index, 5);
    assert.equal(slides[5].html, `<img src="asset:${A}" alt="">`);
    assert.equal(slides[5].title, 'Bridge photo');
    assert.equal(m.insert(0, 'asset', { asset_id: 'not-an-id' }).ok, false);
    assert.equal(m.insert(0, 'asset', { asset_id: A.toUpperCase() }).ok, false);
    assert.equal(m.insert(0, 'video', {}).ok, false);
    assert.equal(m.insert(0, 'copy', {}).ok, false);
    slides[3].visual_review = { composition: { status: 'approved' } };
    r = m.insert(0, 'copy', { scene: slides[3] });  // a pasted copy of S3: a new id, no approvals
    const pasted = slides[0];
    assert.equal(slides[4].scene_id, S3);
    assert.notEqual(pasted.scene_id, S3);
    assert.equal(pasted.title, 'Arches');
    assert.equal('visual_review' in pasted, false);
    assert.equal(pasted.edit.origin, 'inserted');
    assert.equal(slides.length, 7);
    assert.equal(m.undoLabel(), 'Undo add scene');
    m.undo();
    m.undo();
    m.undo();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    m.redo();
    assert.equal(slides[1].title, 'Recap <b>');
    assert.equal(m.insert(undefined, 'blank').index, slides.length - 1);  // no index: at the end
    assert.equal(slides[slides.length - 1].title, 'New scene');
});

test('undoing an insert keeps what the page added to the scene (its plan) for the redo', () => {
    const { m, slides } = model();
    const id = m.insert(1, 'blank', { title: 'Recap' }).new_scene_id;
    slides[1].cinematic_plan = { template: 'summary', duration: 5 };  // the page re-planned the new scene
    m.undo();
    assert.equal(m.indexOf(id), -1);
    m.redo();
    assert.equal(slides[1].scene_id, id);
    assert.deepEqual(slides[1].cinematic_plan, { template: 'summary', duration: 5 });
    // and a scene deleted, restored, deleted again comes back with its latest data
    m.remove(S3);
    m.undo();
    m.scene(S3).cinematic_plan = { template: 'diagram_focus', duration: 4 };
    m.redo();
    m.undo();
    assert.equal(m.scene(S3).cinematic_plan.template, 'diagram_focus');
});

test('a lesson holds at most 200 scenes', () => {
    const slides = Array.from({ length: 200 }, (_, i) => ({ scene_id: 's-' + String(i).padStart(12, '0'), title: String(i) }));
    const m = new E.EditorModel({ scenes: slides, editor: {} });
    assert.equal(m.insert(0, 'blank').ok, false);
    assert.equal(m.duplicate(slides[0].scene_id).ok, false);
    assert.equal(slides.length, 200);
});

test('hidden scenes stay in the lesson, play for 0 s in the timeline, and the last visible one cannot be hidden', () => {
    const { m, slides } = model();
    const before = m.timeline();
    const r = m.setHidden(S2, true);
    assert.equal(r.ok, true);
    assert.deepEqual(slides[1].edit, { hidden: true });
    assert.equal(m.undoLabel(), 'Undo hide scene');
    const tl = m.timeline();
    const hidden = tl.scenes[1];
    assert.equal(hidden.hidden, true);
    assert.equal(hidden.seconds, 0);
    assert.equal(hidden.start, hidden.end);
    assert.equal(tl.total, Math.round((before.total - before.scenes[1].seconds) * 100) / 100);
    assert.equal(tl.transitions.some(t => t.to === S2 || t.from === S2), false);
    assert.equal(m.effective()[1].visible, false);
    m.undo();
    assert.equal('edit' in slides[1], false);
    m.redo();
    assert.equal(m.setHidden(S2, true).changed, false);
    m.setHidden(S1, true);
    m.setHidden(S3, true);
    assert.equal(m.setHidden(S4, true).ok, false);  // at least one scene stays visible
    assert.equal(m.setHidden(S4, 'yes').ok, false);
});

test('a scene splits only at a [PAUSE] marker, into two scenes sharing the board', () => {
    const { m, slides } = model();
    assert.deepEqual(m.splitPoints(S2), []);  // no pause: splitting is not offered
    assert.equal(m.splitAtPause(S2, 0).ok, false);
    assert.equal(m.effective()[1].can_split, false);
    assert.equal(m.effective()[0].can_split, true);
    const points = m.splitPoints(S1);
    assert.equal(points.length, 1);
    assert.equal(points[0].index, 0);
    assert.equal(points[0].marker, '[PAUSE]');
    assert.equal(points[0].before, 'Bridges carry loads across gaps.');
    assert.equal(m.splitAtPause(S1, 1).ok, false);
    assert.equal(m.splitAtPause(S1, 'x').ok, false);
    const original = slides[0].narration;
    const r = m.splitAtPause(S1, 0);
    assert.equal(r.ok, true);
    assert.equal(r.structure, true);
    assert.equal(r.retime, true);
    assert.equal(slides.length, 5);
    assert.equal(slides[0].narration, 'Bridges carry loads across gaps.');
    const second = slides[1];
    assert.equal(r.new_scene_id, second.scene_id);
    assert.match(second.scene_id, ID);
    assert.equal(second.narration, 'They spread the weight to the ground.');
    assert.equal(second.html, slides[0].html);  // the same board
    assert.deepEqual(second.edit, { origin: 'split', from: S1 });
    assert.equal('visual_review' in second, false);
    assert.equal(m.undoLabel(), 'Undo split scene');  // one step
    m.undo();
    assert.equal(slides.length, 4);
    assert.equal(slides[0].narration, original);
    m.redo();
    assert.equal(slides[1].scene_id, second.scene_id);
    assert.equal(slides[0].narration, 'Bridges carry loads across gaps.');
    // [PAUSE:n] markers, and a marker with no words on one side is not offered
    const m2 = new E.EditorModel({ scenes: [{ scene_id: S1, narration: '[PAUSE] One two. [PAUSE:2] Three four. [PAUSE]' }], editor: {} });
    assert.deepEqual(m2.splitPoints(S1).map(p => [p.index, p.marker, p.pause]), [[1, '[PAUSE:2]', 2]]);
    assert.equal(m2.splitAtPause(S1, 0).ok, false);
    assert.equal(m2.splitAtPause(S1, 1).ok, true);
    assert.deepEqual(m2.scenes.map(s => s.narration), ['[PAUSE] One two.', 'Three four. [PAUSE]']);
});

// ---- properties -------------------------------------------------------------------------------------------------

test('setDuration holds a scene at least 0.5 to 600 s, null removes it; undo / redo', () => {
    const { m, slides } = model();
    assert.equal(m.setDuration(S2, 12).ok, true);
    assert.equal(slides[1].edit.min_seconds, 12);
    assert.equal(m.undoLabel(), 'Undo change duration');
    assert.equal(m.timeline().scenes[1].play_seconds, 12);
    m.undo();
    assert.equal('edit' in slides[1], false);
    m.redo();
    assert.equal(slides[1].edit.min_seconds, 12);
    const m2 = model({ extra: { coalesceMs: 0 } }).m;
    m2.setDuration(S2, 0.1);
    assert.equal(m2.scene(S2).edit.min_seconds, 0.5);
    m2.setDuration(S2, 5000);
    assert.equal(m2.scene(S2).edit.min_seconds, 600);
    m2.setDuration(S2, '7.5');
    assert.equal(m2.scene(S2).edit.min_seconds, 7.5);
    m2.setDuration(S2, null);
    assert.equal('edit' in m2.scene(S2), false);
    assert.equal(m2.setDuration(S2, 'abc').ok, false);
    assert.equal(m2.setDuration(S2, NaN).ok, false);
    assert.equal(m2.setDuration(S2, Infinity).ok, false);
    assert.equal(m2.setDuration('s-ffffffffffff', 3).ok, false);
    // never speeds the narration up: a shorter minimum changes nothing
    m2.setDuration(S1, 1);
    assert.equal(m2.timeline().scenes[0].play_seconds, E.estimateSeconds(m2.scene(S1).narration));
});

test('setNarration records the generated narration once, and revert brings it back', () => {
    const { m, slides } = model({ extra: { coalesceMs: 0 } });
    const generated = slides[1].narration;
    const r = m.setNarration(S2, 'Tension pulls.');
    assert.equal(r.ok, true);
    assert.equal(r.retime, true);  // the page retimes (Phase 16)
    assert.equal(slides[1].narration, 'Tension pulls.');
    assert.deepEqual(slides[1].edit, { original: { narration: generated } });
    m.setNarration(S2, 'Tension pulls hard.');
    assert.deepEqual(slides[1].edit.original, { narration: generated });  // recorded the first time only
    assert.deepEqual(m.effective()[1].edited, ['narration']);
    assert.equal(m.revert(S2, 'narration').label, 'Revert narration');
    assert.equal(slides[1].narration, generated);
    assert.equal('edit' in slides[1], false);
    assert.deepEqual(m.effective()[1].edited, []);
    assert.equal(m.undoLabel(), 'Undo revert narration');
    m.undo();
    assert.equal(slides[1].narration, 'Tension pulls hard.');
    assert.deepEqual(slides[1].edit.original, { narration: generated });
    assert.equal(m.revert(S3, 'narration').changed, false);  // nothing to revert
    assert.equal(m.revert(S3, 'question').ok, false);
    // typing the generated text again: not edited any more
    m.setNarration(S2, generated);
    assert.equal('edit' in slides[1], false);
    assert.equal(m.setNarration(S2, 42).ok, false);
    assert.equal(m.setNarration(S2, 'x'.repeat(30000)).ok, true);
    assert.equal(slides[1].narration.length, E.TEXT_LIMITS.narration);  // bounded
});

test('setText edits the title, subtitle and board; an absent subtitle comes back absent', () => {
    const { m, slides } = model({ extra: { coalesceMs: 0 } });
    m.setText(S2, 'title', 'Forces at work');
    m.setText(S2, 'subtitle', 'Push and pull');
    const html = m.setText(S2, 'html', '<p>New board</p>');
    assert.equal(html.retime, true);  // the board moved what the moments point at
    assert.deepEqual(slides[1].edit.original, { title: 'Forces', subtitle: '', html: '<p>Forces</p>' });
    assert.deepEqual(m.effective()[1].edited, ['title', 'subtitle', 'html']);
    m.revert(S2, 'subtitle');
    assert.equal('subtitle' in slides[1], false);
    m.undo();
    assert.equal(slides[1].subtitle, 'Push and pull');
    m.setText(S2, 'subtitle', '');  // cleared: back to the generated (absent) subtitle
    assert.equal('subtitle' in slides[1], false);
    assert.deepEqual(Object.keys(slides[1].edit.original), ['title', 'html']);
    assert.equal(m.setText(S2, 'question', 'x').ok, false);  // unknown field
    assert.equal(m.setText(S2, 'title', null).ok, true);
    assert.equal(m.setText(S2, 'title', { evil: true }).ok, false);
    assert.equal(m.setText(S2, 'title', 'x'.repeat(1000)).ok, true);
    assert.equal(slides[1].title.length, E.TEXT_LIMITS.title);
});

test('setLabels keeps up to 6 short labels in scene.composition, with the generated ones kept for revert', () => {
    const { m, slides } = model({ extra: { coalesceMs: 0 } });
    m.setLabels(S1, ['  load  ', 'span', '', 'x'.repeat(200), 'a', 'b', 'c', 'd']);
    assert.deepEqual(slides[0].composition.labels, ['load', 'span', 'x'.repeat(80), 'a', 'b', 'c']);
    assert.deepEqual(slides[0].edit.original, { labels: ['load', 'span'] });
    assert.equal(m.undoLabel(), 'Undo edit labels');
    m.setLabels(S2, ['tension']);
    assert.deepEqual(slides[1].composition, { labels: ['tension'] });
    assert.deepEqual(slides[1].edit.original, { labels: [] });
    m.revert(S2, 'labels');
    assert.equal('composition' in slides[1], false);
    m.revert(S1, 'labels');
    assert.deepEqual(slides[0].composition.labels, ['load', 'span']);
    assert.equal(m.setLabels(S1, 'load').ok, false);
    assert.equal(m.setLabels(S1, ['load', 'span']).changed, false);
});

test('mute, scene captions and lesson captions; undo / redo', () => {
    const { m, slides, editor } = model();
    m.setNarrationMuted(S2, true);
    assert.deepEqual(slides[1].edit, { narration_muted: true });
    assert.equal(m.undoLabel(), 'Undo mute narration');
    assert.equal(m.timeline().scenes[1].play_seconds, 5);  // a muted scene holds 5 s ...
    m.setDuration(S2, 9);
    assert.equal(m.timeline().scenes[1].play_seconds, 9);  // ... or its minimum
    m.setCaptions(S2, 'off');
    assert.equal(slides[1].edit.captions, 'off');
    assert.equal(m.effective()[1].captions, 'off');
    assert.equal(m.setCaptions(S2, 'on').ok, false);
    m.setLessonCaptions(false);
    assert.deepEqual(editor.captions, { visible: false });
    assert.equal(m.undoLabel(), 'Undo hide captions');
    m.undo();
    assert.equal('captions' in editor, false);
    m.redo();
    assert.deepEqual(editor.captions, { visible: false });
    m.undo();
    m.undo();  // scene captions
    assert.equal('captions' in slides[1].edit, false);
    m.undo();  // duration
    m.undo();  // mute
    assert.equal('edit' in slides[1], false);
    assert.equal(m.setNarrationMuted(S2, 1).ok, false);
    assert.equal(m.setLessonCaptions('no').ok, false);
});

test('edit, edit, undo, undo, redo, redo', () => {
    const { m, slides } = model();
    m.setText(S3, 'title', 'Stone arches');
    m.move(S3, 0);
    assert.deepEqual(ids(slides), [S3, S1, S2, S4]);
    assert.equal(m.undoLabel(), 'Undo move scene');
    m.undo();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    assert.equal(slides[2].title, 'Stone arches');
    assert.equal(m.undoLabel(), 'Undo edit title');
    m.undo();
    assert.equal(slides[2].title, 'Arches');
    assert.equal(m.canUndo(), false);
    assert.equal(m.undo().ok, false);
    assert.equal(m.redoLabel(), 'Redo edit title');
    m.redo();
    assert.equal(slides[2].title, 'Stone arches');
    m.redo();
    assert.deepEqual(ids(slides), [S3, S1, S2, S4]);
    assert.equal(m.canRedo(), false);
    assert.equal(m.redo().ok, false);
    // a new edit clears the redo stack
    m.undo();
    m.setHidden(S4, true);
    assert.equal(m.canRedo(), false);
});

test('a transaction (a drag) is one undo step; nested ones join it; a throw rolls it back', async () => {
    const { m, slides } = model();
    const r = m.transaction('Drag scene', () => {
        m.move(S1, 1);
        m.move(S1, 2);
        m.move(S1, 3);
        m.transaction('inner', () => m.setHidden(S2, true));
        assert.equal(m.inTransaction, true);
        assert.equal(m.canUndo(), false);  // not while a change is open
    });
    assert.equal(r.ok, true);
    assert.equal(r.transaction, true);
    assert.equal(r.structure, true);
    assert.deepEqual(ids(slides), [S2, S3, S4, S1]);
    assert.equal(m.undoStack.length, 1);
    assert.equal(m.undoLabel(), 'Undo drag scene');
    m.undo();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    assert.equal('edit' in slides[1], false);
    m.redo();
    assert.deepEqual(ids(slides), [S2, S3, S4, S1]);
    m.undo();
    // a failure inside rolls everything back and records nothing (the redo stack is kept)
    assert.throws(() => m.transaction('Broken', () => { m.move(S1, 3); throw new Error('boom'); }), /boom/);
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    assert.equal(m.inTransaction, false);
    assert.equal(m.undoLabel(), null);
    assert.equal(m.redoLabel(), 'Redo drag scene');
    // begin / commit (pointer down ... pointer up) and cancel (Escape)
    m.begin('Drag scene');
    m.move(S4, 0);
    assert.equal(m.undo().ok, false);
    m.cancel();
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    // an async transaction
    const done = await m.transaction('Typing', async () => {
        m.setText(S2, 'title', 'F');
        await Promise.resolve();
        m.setText(S2, 'title', 'Fo');
        return 'typed';
    });
    assert.equal(done.value, 'typed');
    m.undo();
    assert.equal(slides[1].title, 'Forces');
    // an empty transaction records nothing
    assert.equal(m.transaction('Nothing', () => {}).changed, false);
});

test('a typing burst on one field is one undo step (coalesced)', () => {
    let now = 1000;
    const { m, slides } = model({ extra: { now: () => now } });
    m.setText(S2, 'title', 'F');
    now += 300;
    m.setText(S2, 'title', 'Fo');
    now += 300;
    m.setText(S2, 'title', 'Foo');
    assert.equal(m.undoStack.length, 1);
    now += 5000;  // a pause: a new step
    m.setText(S2, 'title', 'Food');
    assert.equal(m.undoStack.length, 2);
    now += 100;
    m.setText(S3, 'title', 'Other scene');  // another target: a new step
    assert.equal(m.undoStack.length, 3);
    m.undo();
    m.undo();
    assert.equal(slides[1].title, 'Foo');
    m.undo();
    assert.equal(slides[1].title, 'Forces');
    assert.equal('edit' in slides[1], false);
    // typing back to where it was leaves no step
    m.setText(S2, 'title', 'X');
    now += 100;
    m.setText(S2, 'title', 'Forces');
    assert.equal(m.canUndo(), false);
});

test('the undo stack is bounded (200)', () => {
    const { m } = model({ extra: { coalesceMs: 0 } });
    for (let i = 0; i < 230; i++) m.setHidden(S2, i % 2 === 0);
    assert.equal(m.undoStack.length, E.UNDO_LIMIT);
});

// ---- composition overrides -------------------------------------------------------------------------------------

test('setOverride returns the composition change for the review route; undo returns the previous value', () => {
    const { m, slides } = model();
    const r = m.setOverride(S2, 'camera', 'pan_left');
    assert.equal(r.ok, true);
    assert.equal(r.kind, 'composition');
    assert.equal(r.scene_id, S2);
    assert.deepEqual(r.overrides, { camera: 'pan_left' });
    assert.deepEqual(r.effects, [{ kind: 'composition', scene_id: S2, overrides: { camera: 'pan_left' }, via: 'do' }]);
    assert.equal('visual_review' in slides[1], false);  // not applied here: the page sends it
    assert.equal(m.undoLabel(), 'Undo change camera');
    const u = m.undo();
    assert.deepEqual(u.effects, [{ kind: 'composition', scene_id: S2, overrides: { camera: 'auto' }, via: 'undo' }]);
    const re = m.redo();
    assert.deepEqual(re.effects, [{ kind: 'composition', scene_id: S2, overrides: { camera: 'pan_left' }, via: 'redo' }]);
    // the previous value comes from the scene's saved review
    slides[2].visual_review = { composition: { status: 'changed', overrides: { presenter_size: 'small' } } };
    m.setOverride(S3, 'presenter_size', 'dominant');
    assert.deepEqual(m.undo().effects[0].overrides, { presenter_size: 'small' });
    // a transaction merges the changes per scene (the final value of each key)
    const t = m.transaction('Restyle', () => {
        m.setOverride(S2, 'template', 'visual_focus');
        m.setOverride(S2, 'visual_size', 'dominant');
    });
    assert.deepEqual(t.effects, [{ kind: 'composition', scene_id: S2, overrides: { template: 'visual_focus', visual_size: 'dominant' }, via: 'do' }]);
    assert.deepEqual(m.undo().effects, [{ kind: 'composition', scene_id: S2, overrides: { template: 'auto', visual_size: 'auto' }, via: 'undo' }]);
    // validated against the composition vocabulary
    assert.equal(m.setOverride(S2, 'shot', 'wide').ok, false);
    assert.equal(m.setOverride(S2, 'camera', 'spin').ok, false);
    assert.equal(m.setOverride(S2, 'camera', 'auto').changed, false);  // already automatic
    assert.equal(m.setOverride(S2, 'style_accent', 'teal').ok, true);
});

test('composition effects say whether they come from an edit, an undo or a redo (a cancelled change counts as an undo)', () => {
    const { m } = model();
    const r = m.setOverride(S2, 'presenter_size', 'small');
    assert.equal(r.via, 'do');
    assert.equal(r.effects[0].via, 'do');
    const u = m.undo();
    assert.equal(u.via, 'undo');
    assert.deepEqual(u.effects, [{ kind: 'composition', scene_id: S2, overrides: { presenter_size: 'auto' }, via: 'undo' }]);
    assert.deepEqual(m.redo().effects, [{ kind: 'composition', scene_id: S2, overrides: { presenter_size: 'small' }, via: 'redo' }]);
    m.begin('Try a layout');
    m.setOverride(S3, 'template', 'comparison');
    const c = m.cancel();
    assert.deepEqual(c.effects, [{ kind: 'composition', scene_id: S3, overrides: { template: 'auto' }, via: 'undo' }]);
    // results without composition changes carry no effects
    assert.deepEqual(m.setHidden(S2, true).effects, []);
});

// ---- timeline ---------------------------------------------------------------------------------------------------

test('estimateSeconds mirrors cinematic.estimate_seconds', () => {
    // values computed by cinematic.py estimate_seconds for the same text
    assert.equal(E.estimateSeconds(''), 5);
    assert.equal(E.estimateSeconds('   '), 5);
    assert.equal(E.estimateSeconds(null), 5);
    assert.equal(E.estimateSeconds('Hello.'), 4);
    assert.equal(E.estimateSeconds('One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen.'), 7.72);
    assert.equal(E.estimateSeconds('Look [PAUSE] here [PAUSE:2] and there [SYNC] now [pause:0.5] done.'), 7.11);
    assert.equal(E.estimateSeconds("It's the learner's turn: don't stop, 3.14 is pi_value!"), 4.65);
    assert.equal(E.estimateSeconds('தமிழ் மொழி ஒரு அழகான மொழி. [PAUSE] நன்றி.'), 6.53);
    assert.equal(E.estimateSeconds('word '.repeat(5000)), 600);
});

test('timeline: scene seconds, transitions, hidden scenes, timeAt, sceneAt and snap', () => {
    const settings = { mode: 'cinematic', transitions: 'crossfade' };
    const { m, slides } = model({ extra: { settings: () => settings } });
    const tl = m.timeline();
    const est = n => E.estimateSeconds(slides[n].narration);
    // scene 1: its plan's transition (0.5); the others: the lesson's crossfade (0.6); the first has none
    assert.equal(tl.scenes[0].start, 0);
    assert.equal(tl.scenes[0].seconds, est(0));
    assert.equal(tl.scenes[1].transition_seconds, 0.6);
    assert.equal(tl.scenes[1].seconds, Math.round((est(1) + 0.6) * 100) / 100);
    assert.equal(tl.scenes[3].play_seconds, 5);  // no narration and no plan: 5 s
    assert.equal(tl.transitions.length, 3);
    assert.deepEqual(tl.transitions[0], { from: S1, to: S2, kind: 'crossfade', start: tl.scenes[1].start, end: Math.round((tl.scenes[1].start + 0.6) * 100) / 100, seconds: 0.6 });
    const total = tl.scenes.reduce((a, s) => a + s.seconds, 0);
    assert.ok(Math.abs(tl.total - total) < 0.02);
    assert.equal(tl.scenes[3].end, tl.total);
    assert.equal(m.timeAt(S3), tl.scenes[2].start);
    assert.equal(m.timeAt('s-ffffffffffff'), null);
    assert.equal(m.sceneAt(0).scene_id, S1);
    assert.equal(m.sceneAt(tl.scenes[2].start + 0.01).scene_id, S3);
    assert.equal(m.sceneAt(tl.scenes[2].start + 0.01).offset, 0.01);
    assert.equal(m.sceneAt(tl.total + 10).scene_id, S4);
    assert.equal(m.sceneAt(-3).scene_id, S1);
    assert.equal(m.sceneAt('x'), null);
    assert.equal(m.snap(tl.scenes[2].start + 0.2), tl.scenes[2].start);
    assert.equal(m.snap(tl.scenes[2].start - 0.25), tl.scenes[2].start);
    assert.equal(m.snap(tl.scenes[2].start + 1), Math.round((tl.scenes[2].start + 1) * 100) / 100);
    assert.equal(m.snap(0.1), 0);
    assert.equal(m.snap(tl.total - 0.1), tl.total);
    assert.equal(m.snap(-5), 0);
    assert.equal(m.snap(tl.total + 99), tl.total);
    // hiding a scene: its time and its transition leave the lesson; the hidden scene's start is where it would be
    m.setHidden(S2, true);
    const after = m.timeline();
    assert.ok(Math.abs(after.total - (tl.total - tl.scenes[1].seconds)) < 0.02);
    assert.equal(m.sceneAt(after.scenes[1].start).scene_id, S3);
    // no narration: the plan's own length
    slides[3].cinematic_plan = { template: 'quiz', duration: 6 };
    assert.equal(m.timeline().scenes[3].play_seconds, 6);
    // classic (no cinematic settings): no transition time
    const classic = model().m.timeline();
    assert.equal(classic.transitions.every(t => t.seconds === 0 || t.from === null), true);
    assert.equal(classic.scenes[1].transition_seconds, 0);
});

test('effective: edited fields, origin, moved (vs the generated order), approvals', () => {
    const { m, slides } = model();
    m.setText(S2, 'title', 'Forces at work');
    m.move(S4, 0);  // one scene moved: only it is marked
    const eff = m.effective();
    assert.deepEqual(eff.map(e => e.scene_id), [S4, S1, S2, S3]);
    assert.deepEqual(eff.map(e => e.moved), [true, false, false, false]);
    const forces = eff.find(e => e.scene_id === S2);
    assert.deepEqual(forces.edited, ['title']);
    assert.equal(forces.title, 'Forces at work');
    assert.equal(forces.origin, 'generated');
    assert.equal(forces.type, 'content');
    assert.equal(forces.estimate_seconds, E.estimateSeconds(slides[2].narration));
    const intro = eff.find(e => e.scene_id === S1);
    assert.deepEqual(intro.approvals, { composition: 'approved', main: null, side: 'approved', presenter: 'approved' });
    assert.equal(eff[0].index, 0);
    assert.equal(eff[0].start, 0);
    assert.equal(eff[0].type, 'quiz_checkpoint');
    m.duplicate(S3);
    const dup = m.effective()[4];
    assert.equal(dup.origin, 'duplicated');
    assert.equal(dup.moved, false);  // a new scene is not "moved"
});

// ---- saving -----------------------------------------------------------------------------------------------------

test('dirty, pending, markSaved and serialize', () => {
    const { m, slides } = model();
    assert.equal(m.dirty, false);
    assert.deepEqual(m.pending(), []);
    m.move(S1, 1);
    m.setNarration(S3, 'Arches push outward.');
    assert.equal(m.dirty, true);
    const pending = m.pending();
    assert.deepEqual(pending.map(c => c.type), ['move', 'text']);
    for (const c of pending) {
        assert.deepEqual(Object.keys(c).filter(k => ['type', 'scene_id', 'before', 'after', 'at'].includes(k)).sort(), ['after', 'at', 'before', 'scene_id', 'type']);
        assert.equal(typeof c.at, 'number');
        assert.deepEqual(JSON.parse(JSON.stringify(c)), c);  // plain JSON
    }
    const seq = m.seq;
    const payload = m.serialize();
    assert.deepEqual(payload.scenes, JSON.parse(JSON.stringify(slides)));
    assert.notEqual(payload.scenes, slides);
    assert.equal(payload.editor.version, 1);
    m.setHidden(S4, true);  // made while the save is in flight: stays pending
    m.markSaved('r2', seq);
    assert.equal(m.revision, 'r2');
    assert.equal(m.dirty, true);
    assert.deepEqual(m.pending().map(c => c.type), ['hide']);
    m.markSaved('r3');
    assert.equal(m.dirty, false);
    assert.deepEqual(m.pending(), []);
    m.undo();  // an undo is a change too
    assert.equal(m.dirty, true);
    assert.deepEqual(m.pending().map(c => [c.type, c.after]), [['hide', false]]);
});

test('replay after a 409: the unsaved commands land on the newer lesson by scene_id; a deleted target is a conflict', () => {
    const { m, slides } = model();
    const same = slides;
    m.setText(S2, 'title', 'Forces at work');
    m.setDuration(S3, 10);
    m.move(S1, 3);
    const dup = m.duplicate(S2).new_scene_id;
    m.setOverride(S2, 'camera', 'focus');
    m.setLessonCaptions(false);
    m.setNarration(S4, 'Think about it.');
    const pending = m.pending();
    // the server's newer lesson: S3 deleted by someone else, S4's narration changed, S2 has a new plan
    const newer = lesson().filter(s => s.scene_id !== S3);
    newer[1].cinematic_plan = { template: 'visual_focus', duration: 4 };
    newer[2].narration = 'Someone else wrote this.';
    const result = m.replay(pending, newer, { revision: 'r9', editor: { version: 1, generated_order: [S1, S2, S3, S4] } });
    assert.equal(slides, same);  // the live array, spliced
    assert.equal(m.revision, 'r9');
    assert.equal(result.applied, 5);
    assert.equal(result.skipped, 1);  // the override: the review route already saved it
    assert.deepEqual(result.conflicts.map(c => [c.type, c.scene_id, c.reason, c.applied]), [
        ['duration', S3, 'missing', false],
        ['text', S4, 'changed', true]
    ]);
    assert.equal(result.conflicts[0].label, 'Change duration');
    assert.deepEqual(ids(slides), [S2, dup, S4, S1]);  // duplicate placed right after its source
    assert.equal(slides[0].title, 'Forces at work');
    assert.equal(slides[0].cinematic_plan.template, 'visual_focus');  // the newer lesson's data is kept
    assert.equal(slides[2].narration, 'Think about it.');
    assert.deepEqual(m.editor.captions, { visible: false });
    assert.equal(m.dirty, true);
    assert.equal(m.pending().length, 5);
    // replaying the same commands again: inserts already there are skipped
    const again = m.replay(m.pending(), null);
    assert.equal(again.skipped >= 1, true);
    assert.equal(slides.filter(s => s.scene_id === dup).length, 1);
    // nothing applicable: the lesson is the server's again (clean)
    const { m: m2 } = model();
    m2.setDuration(S3, 4);
    const none = m2.replay(m2.pending(), lesson().filter(s => s.scene_id !== S3), { revision: 'r5' });
    assert.equal(none.applied, 0);
    assert.equal(m2.dirty, false);
    // unreadable commands are conflicts, never silent
    const bad = m2.replay([{ type: 'explode', scene_id: S1 }, { type: 'text', scene_id: S1, field: 'question', before: { value: 'a' }, after: { value: 'b' } }, null]);
    assert.equal(bad.conflicts.length, 3);
    assert.equal(bad.conflicts.every(c => c.reason === 'invalid'), true);
});

test('replay while the lesson is generating: structural commands are locked conflicts, property edits still land', () => {
    const { m: source } = model();
    source.setText(S2, 'title', 'Forces at work');
    source.move(S1, 3);
    const dup = source.duplicate(S3).new_scene_id;
    source.remove(S4);
    const added = source.insert(0, 'blank', { title: 'Recap' }).new_scene_id;
    const split = source.splitAtPause(S1, 0).new_scene_id;
    source.setHidden(S2, true);  // hiding is a property edit: allowed
    source.setDuration(S3, 8);
    const pending = source.pending();
    const { m, slides } = model({ extra: { structureLocked: () => true } });
    const generatedNarration = slides[0].narration;
    const r = m.replay(pending, lesson(), { revision: 'r4' });
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);  // order and membership untouched
    assert.equal(slides[0].narration, generatedNarration);  // the split's narration half waits with it
    assert.equal(slides[1].title, 'Forces at work');
    assert.equal(slides[1].edit.hidden, true);
    assert.equal(slides[2].edit.min_seconds, 8);
    assert.equal(r.applied, 3);
    const locked = r.conflicts.filter(c => c.reason === 'locked');
    assert.deepEqual(locked.map(c => [c.type, c.scene_id]), [
        ['move', S1], ['duplicate', dup], ['remove', S4], ['insert', added], ['text', S1], ['split', split]
    ]);
    assert.equal(locked.every(c => c.applied === false), true);
    assert.equal(r.conflicts.length, locked.length);
    assert.deepEqual(m.pending().map(c => c.type), ['text', 'hide', 'duration']);
    // the same commands once generation has finished: everything lands
    const { m: later, slides: s2 } = model({ extra: { structureLocked: () => false } });
    const r2 = later.replay(pending, lesson(), { revision: 'r5' });
    assert.deepEqual(r2.conflicts, []);
    assert.deepEqual(ids(s2), [added, S2, S3, dup, S1, split]);
    assert.equal(s2.find(s => s.scene_id === S1).narration, 'Bridges carry loads across gaps.');
    // a draft replayed at open goes through the same lock
    const storage = memoryStorage();
    assert.equal(E.saveDraft(source, storage), true);
    const { m: opened, slides: s3 } = model({ extra: { structureLocked: () => true } });
    const r3 = opened.replay(E.loadDraft(42, storage).commands);
    assert.equal(r3.conflicts.filter(c => c.reason === 'locked').length, 6);
    assert.deepEqual(ids(s3), [S1, S2, S3, S4]);
});

test('replay skips override commands after a reload or from a draft (the review route already saved them)', () => {
    const { m: source } = model();
    source.setOverride(S2, 'camera', 'pan_left');
    source.setOverride(S2, 'camera', 'focus');
    source.undo();
    source.setText(S2, 'title', 'Forces at work');
    const pending = source.pending();
    assert.deepEqual(pending.map(c => c.type), ['override', 'override', 'override', 'text']);
    const newer = lesson();
    newer[1].visual_review = { composition: { status: 'changed', overrides: { camera: 'pan_left' } } };  // what the server saved
    const events = [];
    const { m, slides } = model();
    m.subscribe(e => events.push(e));
    const r = m.replay(pending, newer, { revision: 'r6' });
    assert.equal(r.skipped, 3);
    assert.equal(r.applied, 1);
    assert.deepEqual(r.conflicts, []);
    assert.deepEqual(slides[1].visual_review, { composition: { status: 'changed', overrides: { camera: 'pan_left' } } });
    assert.equal(slides[1].title, 'Forces at work');
    assert.deepEqual(events[0].result.effects, []);  // nothing for the page to send again
    assert.deepEqual(m.pending().map(c => c.type), ['text']);
    const storage = memoryStorage();
    assert.equal(E.saveDraft(source, storage), true);
    const { m: opened } = model();
    const r2 = opened.replay(E.loadDraft(42, storage).commands);
    assert.equal(r2.skipped, 3);
    assert.deepEqual(r2.conflicts, []);
});

test('structural edits wait while the lesson is generating; property edits are allowed', () => {
    let busy = true;
    const { m, slides } = model({ extra: { structureLocked: () => busy } });
    assert.equal(m.move(S1, 2).ok, false);
    assert.match(m.move(S1, 2).error, /generating/);
    assert.equal(m.duplicate(S1).ok, false);
    assert.equal(m.remove(S1).ok, false);
    assert.equal(m.insert(0, 'blank').ok, false);
    assert.equal(m.splitAtPause(S1, 0).ok, false);
    assert.equal(m.setText(S1, 'title', 'Hello').ok, true);
    assert.equal(m.setHidden(S2, true).ok, true);
    assert.deepEqual(ids(slides), [S1, S2, S3, S4]);
    busy = false;
    m.move(S1, 2);
    busy = true;
    assert.equal(m.undo().ok, false);  // undoing a move is structural too
    busy = false;
    assert.equal(m.undo().ok, true);
});

test('subscribers hear every change; the module needs no DOM', () => {
    assert.equal(typeof globalThis.document, 'undefined');
    const { m } = model();
    const events = [];
    const off = m.subscribe(e => events.push(e.kind));
    m.subscribe(() => { throw new Error('a broken listener'); });
    m.setHidden(S2, true);
    m.undo();
    m.markSaved('r2');
    off();
    m.setHidden(S2, true);
    assert.deepEqual(events, ['change', 'change', 'saved']);
});

// ---- autosave ---------------------------------------------------------------------------------------------------

function clock() {
    let t = 0;
    let seq = 0;
    const timers = new Map();
    return {
        setTimeout: (fn, ms) => { const id = ++seq; timers.set(id, { at: t + ms, fn }); return id; },
        clearTimeout: id => { timers.delete(id); },
        async tick(ms) {
            t += ms;
            for (const [id, x] of [...timers].sort((a, b) => a[1].at - b[1].at)) {
                if (x.at <= t && timers.has(id)) {
                    timers.delete(id);
                    x.fn();
                }
            }
            await settle();
        },
        get count() { return timers.size; }
    };
}
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
function deferred() {
    let resolve;
    let reject;
    const p = new Promise((a, b) => { resolve = a; reject = b; });
    return { p, resolve, reject };
}
const conflict = (revision, message = 'stale') => Object.assign(new Error(message), { status: 409, revision });

function autosave(options = {}) {
    const c = clock();
    const { m, slides } = model(options.model || {});
    const states = [];
    const saves = [];
    let next = 2;
    const save = options.save || (async payload => { saves.push(payload); return 'r' + next++; });
    const a = new E.Autosave({ model: m, save, reload: options.reload, delay: 1500, onState: (s, info) => states.push([s, info]),
        setTimeout: c.setTimeout, clearTimeout: c.clearTimeout });
    return { a, m, slides, c, states, saves };
}

test('autosave debounces edits and saves once, 1.5 s after the last one', async () => {
    const { a, m, c, states, saves } = autosave();
    assert.equal(a.state, 'saved');
    m.setText(S2, 'title', 'F');
    await c.tick(1000);
    m.setText(S2, 'title', 'Fo');
    await c.tick(1000);
    assert.equal(saves.length, 0);
    assert.equal(a.state, 'unsaved');
    await c.tick(600);
    assert.equal(saves.length, 1);
    assert.equal(saves[0].expected_revision, 'r1');
    assert.equal(saves[0].scenes[1].title, 'Fo');
    assert.ok(saves[0].editor);
    assert.equal(a.state, 'saved');
    assert.equal(m.revision, 'r2');
    assert.equal(m.dirty, false);
    assert.deepEqual(states.map(s => s[0]), ['unsaved', 'saving', 'saved']);
});

test('autosave holds during a drag and saves only after it ends', async () => {
    const { a, m, c, saves } = autosave();
    a.hold();
    m.begin('Drag scene');
    m.move(S1, 1);
    await c.tick(2000);
    m.move(S1, 2);
    await c.tick(5000);
    m.commit();
    await c.tick(5000);
    assert.equal(saves.length, 0);  // still held
    a.release();
    await c.tick(1499);
    assert.equal(saves.length, 0);
    await c.tick(1);
    assert.equal(saves.length, 1);
    assert.deepEqual(saves[0].scenes.map(s => s.scene_id), [S2, S3, S1, S4]);
    // a transaction alone (no hold) also waits for its end
    m.begin('Drag scene');
    m.move(S1, 0);
    await c.tick(5000);
    assert.equal(saves.length, 1);
    m.commit();
    await c.tick(1500);
    assert.equal(saves.length, 2);
});

test('flush saves now; never two saves at once (a flush during a save saves again after it)', async () => {
    const pending = [];
    const { a, m, c, saves } = autosave({ save: payload => { saves.push(payload); const d = deferred(); pending.push(d); return d.p; } });
    m.setText(S2, 'title', 'One');
    const first = a.flush();
    assert.equal(a.state, 'saving');
    assert.equal(saves.length, 1);
    m.setText(S3, 'title', 'Two');  // made during the save
    const second = a.flush();
    assert.equal(saves.length, 1);  // not two at once
    pending[0].resolve('r2');
    await settle();
    assert.equal(saves.length, 2);  // then saved again
    assert.equal(saves[1].expected_revision, 'r2');
    assert.equal(saves[1].scenes[2].title, 'Two');
    pending[1].resolve('r3');
    assert.equal(await first, true);
    assert.equal(await second, true);
    assert.equal(a.state, 'saved');
    assert.equal(m.revision, 'r3');
    assert.equal(c.count <= 1, true);
    // nothing to save: no request
    await a.flush();
    assert.equal(saves.length, 2);
});

test('a failed save keeps the edits and offers a retry', async () => {
    let fail = true;
    const saves = [];
    const { a, m, slides } = autosave({ save: async payload => { saves.push(payload); if (fail) throw Object.assign(new Error('Server unavailable'), { status: 503 }); return 'r2'; } });
    m.setText(S2, 'title', 'Kept');
    assert.equal(await a.flush(), false);
    assert.equal(a.state, 'error');
    assert.equal(a.info.error, 'Server unavailable');
    assert.equal(slides[1].title, 'Kept');  // local state kept
    assert.equal(m.dirty, true);
    assert.equal(m.pending().length, 1);
    fail = false;
    assert.equal(await a.retry(), true);
    assert.equal(a.state, 'saved');
    assert.equal(m.dirty, false);
    assert.equal(saves.length, 2);
});

test('a stale save (409) reloads, replays the unsaved commands and retries once', async () => {
    const saves = [];
    let reloads = 0;
    const newer = lesson();
    newer[2].narration = 'The server changed this.';
    const { a, m, slides, states } = autosave({
        save: async payload => { saves.push(payload); if (saves.length === 1) throw conflict('r7'); return 'r8'; },
        reload: async () => { reloads += 1; return { revision: 'r7', scenes: JSON.parse(JSON.stringify(newer)), editor: { version: 1, generated_order: [S1, S2, S3, S4] } }; }
    });
    const same = slides;
    m.setText(S2, 'title', 'Mine');
    assert.equal(await a.flush(), true);
    assert.equal(reloads, 1);
    assert.equal(saves.length, 2);
    assert.equal(saves[1].expected_revision, 'r7');
    assert.equal(saves[1].scenes[1].title, 'Mine');
    assert.equal(saves[1].scenes[2].narration, 'The server changed this.');
    assert.equal(slides, same);
    assert.equal(a.state, 'saved');
    assert.equal(m.revision, 'r8');
    assert.equal(a.lastReplay.applied, 1);
    assert.deepEqual(states.map(s => s[0]).slice(-3), ['saving', 'saving', 'saved']);
});

test('a replay conflict is shown (state conflict), and a second 409 stops with the edits kept', async () => {
    // the target was deleted on the server: the retry saves the rest, the state says what could not be re-applied
    const { a, m } = autosave({
        save: (() => { let n = 0; return async () => { n += 1; if (n === 1) throw conflict('r7'); return 'r8'; }; })(),
        reload: async () => ({ revision: 'r7', scenes: lesson().filter(s => s.scene_id !== S3), editor: { version: 1, generated_order: [S1, S2, S3, S4] } })
    });
    m.setText(S2, 'title', 'Mine');
    m.setDuration(S3, 9);
    assert.equal(await a.flush(), true);
    assert.equal(a.state, 'conflict');
    assert.equal(a.info.saved, true);
    assert.deepEqual(a.info.conflicts.map(c => [c.scene_id, c.reason]), [[S3, 'missing']]);
    // a 409 again after the retry: conflict, nothing lost
    const b = autosave({
        save: async () => { throw conflict('r' + Math.random()); },
        reload: async () => ({ revision: 'r7', scenes: lesson(), editor: { version: 1, generated_order: [S1, S2, S3, S4] } })
    });
    b.m.setText(S2, 'title', 'Mine');
    assert.equal(await b.a.flush(), false);
    assert.equal(b.a.state, 'conflict');
    assert.equal(b.a.info.saved, false);
    assert.equal(b.slides[1].title, 'Mine');
    assert.equal(b.m.dirty, true);
    // no reload function: a conflict, edits kept
    const c = autosave({ save: async () => { throw conflict('r9'); } });
    c.m.setHidden(S2, true);
    assert.equal(await c.a.flush(), false);
    assert.equal(c.a.state, 'conflict');
    // a refused structural save while generating (same revision): an error to retry later, no reload
    let reloaded = false;
    const d = autosave({ save: async () => { throw conflict('r1', 'Scenes can be reordered once generation finishes.'); }, reload: async () => { reloaded = true; return {}; } });
    d.m.move(S1, 2);
    assert.equal(await d.a.flush(), false);
    assert.equal(d.a.state, 'error');
    assert.equal(d.a.info.locked, true);
    assert.equal(reloaded, false);
    // the reload itself failing: an error, edits kept
    const e = autosave({ save: async () => { throw conflict('r2'); }, reload: async () => { throw new Error('offline'); } });
    e.m.setHidden(S2, true);
    assert.equal(await e.a.flush(), false);
    assert.equal(e.a.state, 'error');
    assert.equal(e.m.dirty, true);
});

test('dispose stops the autosave', async () => {
    const { a, m, c, saves } = autosave();
    m.setHidden(S2, true);
    a.dispose();
    await c.tick(5000);
    m.setHidden(S3, true);
    await c.tick(5000);
    assert.equal(saves.length, 0);
    assert.equal(await a.flush(), false);
});

// ---- the draft ----------------------------------------------------------------------------------------------------

function memoryStorage() {
    const data = new Map();
    return { data, getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => { data.set(k, String(v)); }, removeItem: k => { data.delete(k); } };
}
const throwing = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); }, removeItem() { throw new Error('denied'); } };

test('a per-lesson draft keeps the unsaved commands and recovers them after a refresh', () => {
    const storage = memoryStorage();
    const { m } = model();
    assert.equal(E.draftKey(42), 'aadhi.editor.draft.42');
    m.setText(S2, 'title', 'Unsaved title');
    m.move(S1, 2);
    assert.equal(E.saveDraft(m, storage), true);
    assert.equal(storage.data.has('aadhi.editor.draft.42'), true);
    const draft = E.loadDraft(42, storage);
    assert.equal(draft.project_id, '42');
    assert.equal(draft.revision, 'r1');
    assert.deepEqual(draft.commands.map(c => c.type), ['text', 'move']);
    // after a refresh: the saved lesson again, the draft replayed onto it
    const { m: fresh, slides } = model();
    const r = fresh.replay(draft.commands);
    assert.equal(r.applied, 2);
    assert.equal(slides.find(s => s.scene_id === S2).title, 'Unsaved title');
    assert.deepEqual(ids(slides), [S2, S3, S1, S4]);
    assert.equal(E.loadDraft(43, storage), null);  // another lesson's
    assert.equal(E.Draft.clear(42, storage), true);
    assert.equal(E.loadDraft(42, storage), null);
    // nothing unsaved: the draft is removed
    E.saveDraft(m, storage);
    m.markSaved('r2');
    E.saveDraft(m, storage);
    assert.equal(storage.data.size, 0);
    // unreadable / foreign drafts are ignored
    storage.setItem('aadhi.editor.draft.42', '{not json');
    assert.equal(E.loadDraft(42, storage), null);
    storage.setItem('aadhi.editor.draft.42', JSON.stringify({ version: 1, project_id: '42', commands: [{ type: 'explode' }] }));
    assert.equal(E.loadDraft(42, storage), null);
    assert.equal(E.draftKey('../etc'), null);
    assert.equal(E.saveDraft({ projectId: null, pending: () => [] }, storage), false);
});

test('the draft survives a storage that throws on every access', () => {
    const { m } = model();
    m.setHidden(S2, true);
    assert.equal(E.saveDraft(m, throwing), false);
    assert.equal(E.loadDraft(42, throwing), null);
    assert.equal(E.clearDraft(42, throwing), false);
    // no storage at all (node: no localStorage)
    assert.equal(E.Draft.save(m, null), false);
    assert.equal(E.Draft.load(42, null), null);
});
