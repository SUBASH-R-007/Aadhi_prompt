'use strict';
// Unit tests for the synchronization runtime in the page (Phase 16, cinematic.js CinematicStage): a scene's sync plan
// (plan.sync) played on the narration's own clock (the server's audio, the browser voice's segments, or the estimates),
// dispatched to the presenter, the visual, the board, the labels and the camera; plans without a sync plan play the
// Phase 13 timeline exactly as before.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../cinematic.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

// ---- the page, a board with every fixed target kind, a fake clock and a fake narration audio --------------------------

function page() {
    const doc = fakeDoc();
    const add = (parent, tag, attrs = {}, text) => {
        const n = doc.createElement(tag);
        Object.entries(attrs).forEach(([k, v]) => (k === 'class' ? (n.className = v) : n.setAttribute(k, v)));
        if (text) n.textContent = text;
        parent.appendChild(n);
        return n;
    };
    const zone = add(doc.body, 'div', { class: 'lecture-overlay-zone' });
    const board = add(zone, 'div', { id: 'presentation-board' });
    const content = add(board, 'div', { id: 'slide-content-container' });
    const side = add(doc.body, 'div', { class: 'dynamic-side-zone' });
    const panel = add(side, 'div', { class: 'side-panel-view active' });
    const presenter = add(doc.body, 'div', { id: 'presenter-layer' });
    add(presenter, 'svg', { class: 'teacher-svg' });
    // the board: a definition with two key terms, a list (one item has a list of its own), a second list, a table, code and
    // its output, a formula
    const def = add(content, 'div', { class: 'definition' });
    const term0 = add(def, 'strong', {}, 'Force');
    const term1 = add(def, 'span', { class: 'keyword' }, 'push');
    const ol = add(content, 'ol');
    const li0 = add(ol, 'li', {}, 'First');
    const li1 = add(ol, 'li', {}, 'Second');
    const inner = add(add(li1, 'ul'), 'li', {}, 'inside the second');
    const li2 = add(ol, 'li', {}, 'Third');
    const ul = add(content, 'ul');
    const li3 = add(ul, 'li', {}, 'Fourth');
    const table = add(content, 'table');
    const tr0 = add(table, 'tr');
    const th0 = add(tr0, 'th', {}, 'Speed');
    const th1 = add(tr0, 'th', {}, 'Velocity');
    const tr1 = add(table, 'tr');
    const td0 = add(tr1, 'td', {}, 'scalar');
    const td1 = add(tr1, 'td', {}, 'vector');
    const pre = add(content, 'pre', {}, 'print(2 + 2)');
    const output = add(content, 'p', { class: 'cascade-hidden' }, '4');
    const formula = add(content, 'div', { class: 'formula-block' }, 'F = ma');
    return { doc, zone, board, content, side, panel, presenter, def, term0, term1, ol, li0, li1, inner, li2, ul, li3, table, th0, th1, td0, td1,
        pre, output, formula };
}

function clock() {
    let t = 0; let seq = 0; const timers = new Map();
    return {
        now: () => t,
        setTimeout: (fn, ms) => { const id = ++seq; timers.set(id, { at: t + ms, fn }); return id; },
        clearTimeout: id => timers.delete(id),
        advance(ms) { const end = t + ms; for (;;) { const due = [...timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0]; if (!due) break; timers.delete(due[0]); t = due[1].at; due[1].fn(); } t = end; },
        pending: () => timers.size
    };
}

// A narration segment's <audio>: the test moves currentTime by hand
function fakeAudio(duration) {
    const listeners = {};
    return {
        duration, currentTime: 0, paused: true, ended: false, playbackRate: 1,
        addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        emit(type) { (listeners[type] || []).forEach(fn => fn({ type })); },
        play() { this.paused = false; this.emit('playing'); },
        end() { this.currentTime = this.duration; this.paused = true; this.ended = true; this.emit('pause'); this.emit('ended'); }
    };
}

const box = (x, y, w, h) => ({ x, y, w, h });
function basePlan(extra = {}) { // Scene B of tests/cinematic.test.js: presenter right, visual left, labels under it
    return {
        version: 1, template: 'presenter_plus_visual', template_label: 'Presenter + visual', shot: 'medium', duration: 12,
        style: { typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto', emphasis: 'clear' },
        background: { type: 'gradient', colors: ['#111111', '#222222', '#333333'] },
        presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 },
        camera: { shot: 'medium', movement: 'slow_zoom_in', from: { x: 0, y: 0, w: 1 }, to: { x: 0.0283, y: 0.0094, w: 0.9434 }, start: 0.6, duration: 11, easing: 'ease-in-out', target: 'visual' },
        layers: [
            { id: 'background', type: 'background', box: box(0, 0, 1, 1), z: 0, start: 0, enter: 'appear', important: false, camera: true },
            { id: 'visual', type: 'visual', role: 'image', box: box(0.05, 0.19, 0.2814, 0.52), z: 20, start: 0.3, enter: 'scale_in', important: true, camera: true },
            { id: 'board', type: 'board', role: 'body', style: 'body', box: box(0.3514, 0.19, 0.3186, 0.62), z: 30, start: 0.1, enter: 'fade_in', important: true, camera: true },
            { id: 'title', type: 'title', role: 'title', variant: 'band', subtitle: true, box: box(0.05, 0.05, 0.62, 0.1), z: 60, start: 0, enter: 'fade_in', important: true, camera: false },
            { id: 'labels', type: 'label', role: 'label', box: box(0.05, 0.725, 0.2814, 0.085), z: 50, start: 1.2, enter: 'slide_in', important: true, camera: true,
                items: [{ source: { kind: 'composition', field: 'labels', index: 0 }, at: 1.2, anchor: null }, { source: { kind: 'composition', field: 'labels', index: 1 }, at: 3, anchor: { sync: 2 } }] },
            { id: 'presenter', type: 'presenter', role: 'illustrated', side: 'right', placement: 'side', box: box(0.71, 0.14, 0.25, 0.71), face: box(0.71, 0.14, 0.25, 0.2982), z: 40, start: 0.2, enter: 'slide_in', important: true, camera: true },
            { id: 'subtitles', type: 'subtitles', role: 'caption', box: box(0, 0.85, 1, 0.15), z: 90, start: 0, enter: null, important: false, camera: false }
        ],
        timeline: [{ layer: 'labels', item: 0, at: 1.2, anchor: null, event: 'slide_in' }, { layer: 'labels', item: 1, at: 3, anchor: { sync: 2 }, event: 'slide_in' },
            { layer: 'visual', at: 2, anchor: { sync: 1 }, event: 'highlight', duration: 1.6 }],
        warnings: [], notes: [], review_status: 'pending', ...extra
    };
}
// Two narration segments: 0 is about 4 s, then a 1.5 s [PAUSE], then 1 (about 2 s)
const SEGMENTS = [{ index: 0, chars: 40, pause_after: 1.5, start_estimate: 0, estimate: 4 }, { index: 1, chars: 20, pause_after: 0, start_estimate: 5.5, estimate: 2 }];
const estimateOf = at => (at ? SEGMENTS[at.segment].start_estimate + at.ratio * SEGMENTS[at.segment].estimate : 0);
function ev(id, type, target, at, extra = {}) {
    return { id, type, target, at, estimate: estimateOf(at), duration: 1.6, tolerance: 0.25, priority: 2, anchor: { kind: 'segment', value: 0 },
        concept: null, depends_on: [], params: {}, ...extra };
}
function syncPlan(events, sync = {}) {
    return basePlan({ sync: { version: 1, fingerprint: 'f'.repeat(16), timing_source: 'narration', narration: { segments: SEGMENTS, estimate_total: 7.5 },
        events, attention: [], end_hold: 0.4, fallback: null, metrics: {}, ...sync } });
}
const sceneB = () => ({ type: 'content', title: 'Inside the leaf', narration: 'Look. [SYNC] One. [SYNC] Two.',
    composition: { labels: ['Sunlight → Chlorophyll → Glucose', 'Glucose'] }, presenter_plan: { presenter_id: 'aadhi-teacher', type: 'illustrated', enabled: true } });

function stage(extra = {}) {
    const p = page();
    const c = clock();
    const settings = { ...C.DEFAULTS, mode: 'cinematic' };
    const acts = [];
    const marked = [];
    const st = new C.CinematicStage({ doc: p.doc, settings: () => settings, now: c.now, setTimeout: c.setTimeout, clearTimeout: c.clearTimeout,
        raf: fn => c.setTimeout(fn, 16), exporting: () => !!extra.exporting, reducedMotion: () => !!extra.reduced,
        presenterAct: extra.noPresenter ? null : (params, event) => acts.push([event.id, params]),
        markItem: extra.noMark ? null : el => marked.push(el) });
    const play = plan => { st.layout(sceneB(), plan, 1); return st.plan; };
    return { ...p, clock: c, st, acts, marked, play, chips: () => p.doc.querySelectorAll('.cine-label') };
}
const glows = el => el.animations.filter(a => a.frames.some(f => f.boxShadow));
const cameraAnims = doc => doc.documentElement.all().flatMap(n => n.animations || []).filter(a => a.opts && a.opts.fill === 'both' && !a.cancelled);

// ---- the narration clock ------------------------------------------------------------------------------------------------

test('events fire in narration order on the audio clock (segment first, then ratio), not on their estimates', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e1', 'label_enter', { layer: 'labels', item: 0 }, { segment: 0, ratio: 0.5 }),
        ev('e2', 'visual_highlight', { layer: 'visual' }, { segment: 1, ratio: 0.25 }, { duration: 1.2 }),
        ev('e3', 'label_enter', { layer: 'labels', item: 1 }, { segment: 1, ratio: 0.75 })
    ]));
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const chips = s.chips();
    s.clock.advance(9000); // far past every estimate (and the timeline's 1.2 s label): the events wait for the narration's audio
    assert.equal(s.st.stats.syncFired, 0);
    assert.equal(chips[0].getAttribute('data-shown'), 'false');
    const a0 = fakeAudio(4);
    s.st.attachNarration(0, a0);
    s.clock.advance(100);
    assert.equal(s.st.stats.syncFired, 0); // attached, not playing yet
    a0.play();
    a0.currentTime = 1.0;
    s.clock.advance(20);
    assert.equal(chips[0].getAttribute('data-shown'), 'false'); // 1 s of 4: the label is due at 2 s (never before its word)
    a0.currentTime = 1.9;
    s.clock.advance(20);
    assert.equal(chips[0].getAttribute('data-shown'), 'false', 'not a tolerance early: the word has not been said');
    a0.currentTime = 2.0;
    s.clock.advance(20);
    assert.equal(chips[0].getAttribute('data-shown'), 'true');
    assert.deepEqual([s.st.stats.syncFired, s.st.stats.syncLast], [1, 'label_enter']);
    a0.end();
    s.clock.advance(1500); // the [PAUSE] between the segments: segment 1's events wait for its audio
    assert.equal(s.st.stats.syncFired, 1);
    assert.deepEqual(s.st.narrationPosition(), { segment: 0, ratio: 1, time: 4, duration: 4 });
    const a1 = fakeAudio(2);
    s.st.attachNarration(1, a1);
    a1.play();
    a1.currentTime = 0.5;
    s.clock.advance(20);
    assert.equal(glows(s.panel).length, 1, 'the visual pulses at its moment');
    assert.equal(glows(s.panel)[0].opts.duration, 1200, 'for the event\'s duration');
    assert.equal(chips[1].getAttribute('data-shown'), 'false');
    a1.currentTime = 1.5;
    s.clock.advance(20);
    assert.equal(chips[1].getAttribute('data-shown'), 'true');
    assert.equal(s.st.stats.syncFired, 3);
    s.clock.advance(1000);
    assert.equal(s.clock.pending(), 0, 'every event fired: the frame loop stopped');
});

test('tolerance bounds lateness only: an event fires at its word, at most a frame early', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e1', 'presenter_point', { layer: 'presenter' }, { segment: 0, ratio: 0.5 }, { tolerance: 0.25, params: { gesture: 'point' } }),
        ev('e2', 'presenter_explain', { layer: 'presenter' }, { segment: 0, ratio: 0.75 }, { tolerance: 0, params: { expression: 'engaged' } }),
        ev('e3', 'presenter_pause', { layer: 'presenter' }, { segment: 0, ratio: 0.9 }, { tolerance: 60, params: { expression: 'thinking' } })
    ]));
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const a0 = fakeAudio(4);
    s.st.attachNarration(0, a0);
    a0.play();
    const at = t => { a0.currentTime = t; s.clock.advance(20); return s.acts.map(([id]) => id); };
    assert.deepEqual(at(1.76), [], 'a tolerance never makes an event early: the word comes at 2 s');
    assert.deepEqual(at(1.90), []);
    assert.deepEqual(at(1.95), ['e1']); // 2 s, less a frame's lead (60 ms)
    assert.deepEqual(at(2.99), ['e1']);
    assert.deepEqual(at(3.00), ['e1', 'e2']); // no tolerance: exactly at 3 s
    assert.deepEqual(at(3.50), ['e1', 'e2'], 'even a huge tolerance does not make it early');
    assert.deepEqual(at(3.55), ['e1', 'e2', 'e3']); // 3.6 s, less a frame
});

test('depends_on: an event waits for the events it depends on (an unknown dependency is ignored)', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e1', 'presenter_point', { layer: 'presenter' }, { segment: 0, ratio: 0.6 }, { params: { gesture: 'point' } }),
        ev('e2', 'presenter_explain', { layer: 'presenter' }, { segment: 0, ratio: 0.1 }, { depends_on: ['e1'], params: { gesture: 'explaining' } }),
        ev('e3', 'presenter_summarize', { layer: 'presenter' }, { segment: 0, ratio: 0.2 }, { depends_on: ['ghost'], params: { gesture: 'open_hand' } })
    ]));
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const a0 = fakeAudio(4);
    s.st.attachNarration(0, a0);
    a0.play();
    a0.currentTime = 1.0;
    s.clock.advance(20);
    assert.deepEqual(s.acts.map(([id]) => id), ['e3']); // e2's position passed, but e1 has not fired
    a0.currentTime = 2.4;
    s.clock.advance(20);
    assert.deepEqual(s.acts.map(([id]) => id), ['e3', 'e1', 'e2'], 'e1, then what waited for it, in the same frame');
});

test('hold and resume: a hold pauses the clock, the camera and the glows; the scene clock excludes the held time', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e1', 'label_enter', { layer: 'labels', item: 0 }, null, { estimate: 1.0, tolerance: 0 }),
        ev('e2', 'text_emphasis', { layer: 'board', kind: 'column', index: 1 }, null, { estimate: 0.2, tolerance: 0, duration: 1 })
    ]));
    s.st.start(sceneB(), plan); // no narration clock (muted): the estimates, from start()
    s.clock.advance(500);
    assert.ok(s.td1.classList.contains('cine-focus'));
    s.st.narrationEvent('hold');
    s.clock.advance(5000);
    assert.equal(s.chips()[0].getAttribute('data-shown'), 'false', 'held: the label due at 1 s still waits');
    assert.ok(s.td1.classList.contains('cine-focus'), 'held: the glow waits too');
    s.st.narrationEvent('resume');
    s.clock.advance(400);
    assert.equal(s.chips()[0].getAttribute('data-shown'), 'false'); // 0.9 s of scene time
    s.clock.advance(150);
    assert.equal(s.chips()[0].getAttribute('data-shown'), 'true');
    s.clock.advance(300);
    assert.equal(s.td1.classList.contains('cine-focus'), false, 'the glow lasted its 1 s of unheld time');
    // on the audio clock: a hold stops the frame loop, the resume picks the position up
    const a = stage();
    const p2 = a.play(syncPlan([ev('e1', 'presenter_point', { layer: 'presenter' }, { segment: 0, ratio: 0.5 }, { params: { gesture: 'point' } })]));
    a.st.narrationSource('audio');
    a.st.start(sceneB(), p2);
    const a0 = fakeAudio(4);
    a.st.attachNarration(0, a0);
    a0.play();
    a0.currentTime = 1.0;
    a.clock.advance(20);
    a.st.narrationEvent('hold');
    a0.currentTime = 3.0;
    a.clock.advance(500);
    assert.equal(a.acts.length, 0);
    assert.equal(a.clock.pending(), 0, 'held: the frame loop stopped');
    a0.emit('ended'); // even a late audio event fires nothing while held
    assert.equal(a.acts.length, 0);
    a.st.narrationEvent('resume');
    a.clock.advance(20);
    assert.equal(a.acts.length, 1);
});

test('a scene change cancels everything still waiting: no event, glow, hidden result or frame loop survives it', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e1', 'text_emphasis', { layer: 'board', kind: 'column', index: 0 }, { segment: 0, ratio: 0.1 }, { duration: 3 }),
        ev('e2', 'label_enter', { layer: 'labels', item: 0 }, { segment: 0, ratio: 0.8 }),
        ev('e3', 'text_reveal', { layer: 'board', kind: 'output' }, { segment: 1, ratio: 0.5 })
    ]));
    s.st.prepareBoard();
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const a0 = fakeAudio(4);
    s.st.attachNarration(0, a0);
    a0.play();
    a0.currentTime = 1.0;
    s.clock.advance(20);
    assert.ok(s.th0.classList.contains('cine-focus'));
    assert.ok(s.output.classList.contains('cine-pending'));
    assert.equal(s.st.stats.syncFired, 1);
    s.st.layout(sceneB(), basePlan(), 2); // the next scene (no sync plan)
    assert.equal(s.th0.classList.contains('cine-focus'), false);
    assert.equal(s.output.classList.contains('cine-pending'), false);
    a0.currentTime = 4;
    a0.emit('playing');
    a0.end();
    s.st.attachNarration(1, fakeAudio(2));
    s.clock.advance(5000);
    assert.equal(s.st.stats.syncFired, 1);
    assert.equal(s.clock.pending(), 0, 'the old frame loop and timers are gone');
    assert.equal(s.st.state().sync, null);
    // two synchronized scenes in a row: one frame loop at a time
    const t = stage();
    const first = t.play(syncPlan([ev('e1', 'presenter_point', { layer: 'presenter' }, null, { estimate: 30 })]));
    t.st.start(sceneB(), first);
    t.clock.advance(100);
    const second = t.play(syncPlan([ev('e1', 'presenter_point', { layer: 'presenter' }, null, { estimate: 30 })]));
    t.st.start(sceneB(), second);
    t.clock.advance(100);
    assert.equal(t.clock.pending(), 1);
});

test('nothing fires twice: a narration restarted in the same scene, the end of the narration, another tick', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e1', 'label_enter', { layer: 'labels', item: 0 }, { segment: 0, ratio: 0.25 }),
        ev('e2', 'presenter_point', { layer: 'presenter' }, { segment: 0, ratio: 0.5 }, { params: { gesture: 'point' } })
    ]));
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const a0 = fakeAudio(4);
    s.st.attachNarration(0, a0);
    a0.play();
    a0.end();
    s.clock.advance(20);
    assert.equal(s.st.stats.syncFired, 2);
    s.st.narrationSource('audio'); // the viewer pressed play again: the narration starts over
    const again = fakeAudio(4);
    s.st.attachNarration(0, again);
    again.play();
    again.currentTime = 3;
    s.clock.advance(100);
    s.st.narrationFinished();
    assert.equal(s.st.syncTick(), 0);
    assert.equal(s.st.stats.syncFired, 2);
    assert.equal(s.acts.length, 1);
});

test('without a narration clock (muted, no narration) the estimates count from start(); before start() nothing fires', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e1', 'presenter_point', { layer: 'presenter' }, { segment: 0, ratio: 0.25 }, { tolerance: 0, params: { gesture: 'point' } }),
        ev('e2', 'presenter_explain', { layer: 'presenter' }, { segment: 1, ratio: 0.5 }, { tolerance: 0, params: { gesture: 'explaining' } })
    ]));
    s.st.narrationSource('none');
    s.clock.advance(5000); // the formulas are being typeset: the scene clock has not started
    assert.equal(s.acts.length, 0);
    s.st.start(sceneB(), plan);
    s.clock.advance(990);
    assert.equal(s.acts.length, 0);
    s.clock.advance(20);
    assert.deepEqual(s.acts.map(([id]) => id), ['e1']); // estimate 1.0 s
    s.clock.advance(5500);
    assert.deepEqual(s.acts.map(([id]) => id), ['e1', 'e2']); // estimate 6.5 s
});

test('browser speech: each segment start is known (its "segment" beat); the estimate runs inside the segment', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e0', 'presenter_point', { layer: 'presenter' }, { segment: 0, ratio: 0.5 }, { params: { gesture: 'point' } }),
        ev('e1', 'presenter_explain', { layer: 'presenter' }, { segment: 1, ratio: 0.5 }, { params: { gesture: 'explaining' } })
    ]));
    s.st.narrationSource('speech');
    s.st.start(sceneB(), plan);
    s.clock.advance(3000);
    assert.equal(s.acts.length, 0, 'no segment has started yet');
    s.st.narrationEvent('segment');
    s.clock.advance(1700);
    assert.equal(s.acts.length, 0);
    s.clock.advance(100); // 1.8 s into segment 0: the word (0.5 × 4 s) has not come yet
    assert.equal(s.acts.length, 0);
    s.clock.advance(200); // 2.0 s
    assert.deepEqual(s.acts.map(([id]) => id), ['e0']);
    s.clock.advance(10000);
    assert.equal(s.acts.length, 1, 'segment 1 has not started, though its estimate has passed');
    s.st.narrationEvent('segment');
    s.clock.advance(800);
    assert.equal(s.acts.length, 1);
    s.clock.advance(200);
    assert.deepEqual(s.acts.map(([id]) => id), ['e0', 'e1']);
    // a page that does not say which clock it uses: a segment beat means the browser's voice
    const t = stage();
    t.play(syncPlan([]));
    t.st.narrationEvent('segment');
    assert.equal(t.st.state().sync.clock, 'speech');
});

test('the server voice failing at a later segment: the browser voice takes over THAT segment, its moments still fire', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('e0', 'presenter_point', { layer: 'presenter' }, { segment: 0, ratio: 0.5 }, { params: { gesture: 'point' } }),
        ev('e1', 'presenter_explain', { layer: 'presenter' }, { segment: 1, ratio: 0.5 }, { params: { gesture: 'explaining' } })
    ]));
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const a0 = fakeAudio(4);
    s.st.attachNarration(0, a0);
    a0.play();
    a0.currentTime = 2.0;
    s.clock.advance(20);
    assert.deepEqual(s.acts.map(([id]) => id), ['e0']);
    a0.end();
    s.st.narrationSource('speech', 1); // segment 1's audio could not be fetched: the page speaks it with the browser's voice
    s.st.narrationEvent('segment');
    s.clock.advance(800);
    assert.deepEqual(s.acts.map(([id]) => id), ['e0'], 'not before its word (0.5 × 2 s into segment 1)');
    s.clock.advance(200);
    assert.deepEqual(s.acts.map(([id]) => id), ['e0', 'e1']);
    assert.equal(s.st.state().sync.clock, 'speech');
    // a nonsense segment falls back to the first
    const t = stage();
    t.play(syncPlan([]));
    t.st.narrationSource('speech', -3);
    t.st.narrationEvent('segment');
    assert.equal(t.st.narrationPosition().segment, 0);
});

test('an event without a narration position is placed on the narration by its estimate', () => {
    const s = stage();
    const plan = s.play(syncPlan([ev('e1', 'presenter_point', { layer: 'presenter' }, null, { estimate: 6.0, tolerance: 0, params: { gesture: 'point' } })]));
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const a0 = fakeAudio(5); // the real audio is longer than estimated
    s.st.attachNarration(0, a0);
    a0.play();
    a0.end();
    s.clock.advance(8000);
    assert.equal(s.acts.length, 0, '6.0 s is in segment 1 (a quarter into it), not 6 s of the scene');
    const a1 = fakeAudio(3);
    s.st.attachNarration(1, a1);
    a1.play();
    a1.currentTime = 0.7;
    s.clock.advance(20);
    assert.equal(s.acts.length, 0);
    a1.currentTime = 0.75;
    s.clock.advance(20);
    assert.equal(s.acts.length, 1);
});

// ---- what each event does ---------------------------------------------------------------------------------------------

test('dispatch: the presenter acts with its params; text emphasis resolves only the fixed kinds on the page\'s own board', () => {
    const s = stage();
    const at0 = { estimate: 0, tolerance: 0 };
    const plan = s.play(syncPlan([
        ev('p1', 'presenter_point', { layer: 'presenter' }, null, { ...at0, params: { gesture: 'point', expression: 'engaged', state: 'explaining', html: '<b>x</b>' } }),
        ev('t1', 'text_emphasis', { layer: 'board', kind: 'step', index: 1 }, null, at0),
        ev('t2', 'text_emphasis', { layer: 'board', kind: 'column', index: 1 }, null, at0),
        ev('t3', 'text_emphasis', { layer: 'board', kind: 'side', index: 1 }, null, at0),
        ev('t4', 'text_emphasis', { layer: 'board', kind: 'term', index: 1 }, null, at0),
        ev('t5', 'text_emphasis', { layer: 'board', kind: 'output' }, null, at0),
        ev('t6', 'text_emphasis', { layer: 'board', kind: 'formula' }, null, at0),
        ev('f1', 'formula_emphasis', { layer: 'board', kind: 'formula' }, null, { ...at0, duration: 2 }),
        // ignored: a selector instead of a kind, the wrong layer, an unknown type; no element there; not an integer index / item
        ev('x1', 'text_emphasis', { layer: 'board', kind: '#slide-content-container li' }, null, at0),
        ev('x2', 'text_emphasis', { layer: 'visual', kind: 'step', index: 0 }, null, at0),
        ev('x3', 'explode', { layer: 'board', kind: 'step', index: 0 }, null, at0),
        ev('x4', 'text_emphasis', { layer: 'board', kind: 'step', index: 99 }, null, at0),
        ev('x5', 'text_emphasis', { layer: 'board', kind: 'term', index: '1' }, null, at0),
        ev('x6', 'label_enter', { layer: 'labels', item: '0"], .x[data-item="1' }, null, at0)
    ]));
    assert.equal(s.st.state().sync.events, 11, 'the unknown kind, layer and type are dropped');
    s.st.start(sceneB(), plan);
    s.clock.advance(20);
    assert.deepEqual(s.acts, [['p1', { gesture: 'point', expression: 'engaged', state: 'explaining' }]]);
    assert.deepEqual(s.marked, [s.li1], 'the second top-level item (the nested list does not count) gets the page\'s narration glow');
    const focused = s.doc.documentElement.all().filter(n => n.classList && n.classList.contains('cine-focus'));
    assert.deepEqual(focused, [s.term1, s.ul, s.th1, s.td1, s.output, s.formula]);
    assert.equal(glows(s.formula)[0].opts.duration, 2000, 'the formula pulses');
    assert.equal(s.chips().filter(c => c.getAttribute('data-shown') === 'true').length, 0);
    assert.equal(s.st.stats.syncFired, 11);
    s.clock.advance(1700);
    assert.equal(s.doc.documentElement.all().some(n => n.classList && n.classList.contains('cine-focus')), false, 'each glow lasts its event\'s duration');
    // without the page's options: a list item glows with the same class for its duration; no presenter to act
    const bare = stage({ noMark: true, noPresenter: true });
    const p2 = bare.play(syncPlan([ev('t1', 'text_emphasis', { layer: 'board', kind: 'event', index: 3 }, null, { ...at0, duration: 1 }),
        ev('p1', 'presenter_emphasis', { layer: 'presenter' }, null, { ...at0, params: { gesture: 'open_hand' } })]));
    bare.st.start(sceneB(), p2);
    bare.clock.advance(20);
    assert.ok(bare.li3.classList.contains('narration-active'));
    assert.equal(bare.st.stats.syncFired, 2);
    bare.clock.advance(1000);
    assert.equal(bare.li3.classList.contains('narration-active'), false);
});

test('an emphasised item still waiting for its reveal is shown with its glow; the side named before stops glowing', () => {
    const s = stage();
    s.li2.classList.add('cascade-hidden'); // a timeline's date is said before the [SYNC] that reveals its item
    const plan = s.play(syncPlan([
        ev('t1', 'text_emphasis', { layer: 'board', kind: 'event', index: 2 }, null, { estimate: 0.5, tolerance: 0 }),
        ev('c0', 'text_emphasis', { layer: 'board', kind: 'column', index: 0 }, null, { estimate: 1, tolerance: 0, duration: 3 }),
        ev('c1', 'text_emphasis', { layer: 'board', kind: 'column', index: 1 }, null, { estimate: 2, tolerance: 0, duration: 3 })
    ]));
    s.st.start(sceneB(), plan);
    s.clock.advance(600);
    assert.deepEqual(s.marked, [s.li2]);
    assert.equal(s.li2.classList.contains('cascade-hidden'), false, 'never a glow on a hidden item');
    assert.ok(s.li2.classList.contains('cascade-visible'));
    s.clock.advance(500);
    assert.ok(s.th0.classList.contains('cine-focus') && s.td0.classList.contains('cine-focus'));
    s.clock.advance(1000); // the second side is named while the first would still glow for 2 s
    assert.ok(s.th1.classList.contains('cine-focus') && s.td1.classList.contains('cine-focus'));
    assert.equal(s.th0.classList.contains('cine-focus') || s.td0.classList.contains('cine-focus'), false, 'one emphasis holds the board');
    s.clock.advance(3100);
    assert.equal(s.doc.documentElement.all().some(n => n.classList && n.classList.contains('cine-focus')), false);
    assert.equal(s.st.stats.syncFired, 3);
});

test('labels enter and leave; the output stays hidden until its text_reveal, then fades in', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('l1', 'label_enter', { layer: 'labels', item: 0 }, null, { estimate: 0.5, tolerance: 0 }),
        ev('l2', 'label_exit', { layer: 'labels', item: 0 }, null, { estimate: 1.0, tolerance: 0 }),
        ev('r1', 'text_reveal', { layer: 'board', kind: 'output' }, null, { estimate: 1.5, tolerance: 0 }),
        ev('l3', 'label_enter', { layer: 'labels', item: 0 }, null, { estimate: 2.0, tolerance: 0 })
    ]));
    assert.equal(s.st.prepareBoard(), 1); // the page calls it once the board is drawn
    assert.ok(s.output.classList.contains('cine-pending'));
    assert.equal(s.pre.classList.contains('cine-pending'), false);
    s.st.start(sceneB(), plan); // start() prepares again: nothing twice
    assert.equal(s.st.prepareBoard(), 0);
    const chip = s.chips()[0];
    s.clock.advance(600);
    assert.equal(chip.getAttribute('data-shown'), 'true');
    s.clock.advance(500);
    assert.deepEqual([chip.getAttribute('data-shown'), chip.getAttribute('data-exit')], ['false', 'true']); // fades out (CSS)
    assert.ok(s.output.classList.contains('cine-pending'));
    s.clock.advance(500);
    assert.equal(s.output.classList.contains('cine-pending'), false);
    assert.equal(s.output.classList.contains('cascade-hidden'), false);
    assert.ok(s.output.classList.contains('cascade-visible'));
    assert.deepEqual(s.output.animations[0].frames, [{ opacity: 0 }, { opacity: 1 }]);
    s.clock.advance(500);
    assert.deepEqual([chip.getAttribute('data-shown'), chip.getAttribute('data-exit')], ['true', null], 'shown again');
});

test('camera events: one move from the current view to the planned framing; a new move starts where the camera is', () => {
    const s = stage();
    const to = { x: 0.0283, y: 0.0094, w: 0.9434 };
    const boardBox = basePlan().layers[2].box;
    const plan = s.play(syncPlan([
        ev('c1', 'camera_focus', { layer: 'camera' }, null, { estimate: 0, tolerance: 0, params: { to, duration: 2 } }),
        ev('c2', 'camera_return', { layer: 'camera' }, null, { estimate: 1.0, tolerance: 0, params: { to: { x: 0, y: 0, w: 1 }, duration: 2 } })
    ]));
    s.st.start(sceneB(), plan);
    assert.equal(cameraAnims(s.doc).length, 0, 'the plan\'s own camera move is not started: the camera events own the camera');
    assert.ok(s.zone.classList.contains('cine-camera'));
    s.clock.advance(20);
    const first = s.zone.animations.find(a => a.opts.fill === 'both');
    assert.equal(first.frames[0].transform, C.cameraTransform({ x: 0, y: 0, w: 1 }, boardBox));
    assert.equal(first.frames[1].transform, C.cameraTransform(to, boardBox));
    assert.deepEqual([first.opts.duration, first.opts.easing], [2000, 'ease-in-out']);
    assert.ok(s.side.animations.find(a => a.opts.fill === 'both'), 'every camera layer moves as one picture');
    cameraAnims(s.doc).forEach(a => { a.currentTime = 1000; }); // half-way when the return starts
    s.clock.advance(1000);
    assert.ok(first.cancelled, 'one move at a time');
    const second = s.zone.animations.filter(a => a.opts.fill === 'both' && !a.cancelled);
    assert.equal(second.length, 1);
    const mid = C.lerpFraming({ x: 0, y: 0, w: 1 }, to, 0.5); // ease-in-out is half-way at half the time
    assert.equal(second[0].frames[0].transform, C.cameraTransform(mid, boardBox));
    assert.equal(second[0].frames[1].transform, C.cameraTransform({ x: 0, y: 0, w: 1 }, boardBox));
    assert.equal(s.st.stats.cameraMoves, 2);
    assert.equal(s.st.stats.syncLast, 'camera_return');
    // the same still rules as the plan's camera: reduced motion in the preview, a still plan; a framing out of range is ignored
    const events = [ev('c1', 'camera_focus', { layer: 'camera' }, null, { estimate: 0, params: { to, duration: 2 } })];
    for (const [opts, p] of [[{ reduced: true }, syncPlan(events)], [{}, { ...syncPlan(events), camera: { ...basePlan().camera, movement: 'static' } }],
        [{}, syncPlan([ev('c1', 'camera_focus', { layer: 'camera' }, null, { estimate: 0, params: { to: { x: 0.6, y: 0, w: 0.6 }, duration: 2 } })])]]) {
        const still = stage(opts);
        still.st.start(sceneB(), still.play(p));
        still.clock.advance(100);
        assert.equal(cameraAnims(still.doc).length, 0);
        assert.equal(still.st.stats.syncFired, 1);
    }
    const recording = stage({ reduced: true, exporting: true }); // the video follows the plan
    recording.st.start(sceneB(), recording.play(syncPlan(events)));
    recording.clock.advance(100);
    assert.equal(recording.st.stats.cameraMoves, 1);
});

test('the end of the narration shows the labels and result still waiting; the other moments are over', () => {
    const s = stage();
    const plan = s.play(syncPlan([
        ev('p1', 'presenter_point', { layer: 'presenter' }, { segment: 1, ratio: 0.2 }, { params: { gesture: 'point' } }),
        ev('l1', 'label_enter', { layer: 'labels', item: 1 }, { segment: 1, ratio: 0.5 }),
        ev('r1', 'text_reveal', { layer: 'board', kind: 'output' }, { segment: 1, ratio: 0.9 })
    ]));
    s.st.narrationSource('audio');
    s.st.start(sceneB(), plan);
    const a0 = fakeAudio(4);
    s.st.attachNarration(0, a0);
    a0.play();
    a0.end(); // segment 1's audio could not be made: the narration ends after segment 0
    s.clock.advance(100);
    assert.equal(s.st.stats.syncFired, 0);
    assert.ok(s.output.classList.contains('cine-pending'));
    s.st.narrationFinished();
    assert.equal(s.chips()[1].getAttribute('data-shown'), 'true');
    assert.equal(s.output.classList.contains('cine-pending'), false);
    assert.equal(s.acts.length, 0);
    assert.equal(s.st.stats.syncFired, 2);
});

test('end hold, server audio and the stats the tests and ?visualDebug read', () => {
    const s = stage();
    assert.deepEqual(Object.keys(s.st.stats), ['scenes', 'cameraMoves', 'anchored', 'fitted', 'lastTemplate', 'lastFit', 'syncFired', 'syncLast']);
    assert.deepEqual([s.st.endHold(), s.st.wantsServerAudio()], [0, false]);
    s.play(syncPlan([], { end_hold: 0.4 }));
    assert.deepEqual([s.st.endHold(), s.st.wantsServerAudio()], [0.4, true]);
    s.play(syncPlan([], { end_hold: 3 }));
    assert.equal(s.st.endHold(), 0.7);
    s.play(syncPlan([], { end_hold: -1 }));
    assert.equal(s.st.endHold(), 0);
    s.play(syncPlan([], { end_hold: 'long' }));
    assert.equal(s.st.endHold(), 0);
    s.play(basePlan({ sync: { version: 2, events: [], end_hold: 0.5 } })); // a sync plan from another version is not guessed at
    assert.deepEqual([s.st.endHold(), s.st.wantsServerAudio(), s.st.state().sync], [0, false, null]);
    s.st.clear();
    assert.deepEqual([s.st.endHold(), s.st.wantsServerAudio()], [0, false]);
});

// ---- plans without a sync plan: the Phase 13 timeline, exactly as before -------------------------------------------------

test('without plan.sync the timeline plays as before (labels, emphasis, the camera move); with it the timeline steps aside', () => {
    const s = stage();
    const plan = s.play(basePlan());
    s.st.start(sceneB(), plan);
    assert.ok(s.zone.animations.find(a => a.opts.fill === 'both'), 'the plan\'s camera move');
    assert.equal(s.st.stats.cameraMoves, 1);
    s.clock.advance(1300);
    assert.equal(s.chips()[0].getAttribute('data-shown'), 'true'); // the timeline's label at 1.2 s
    s.st.narrationEvent('sync');
    assert.equal(glows(s.panel).length, 1, 'the timeline\'s emphasis at its [SYNC]');
    // the synchronization's calls are harmless without a sync plan
    s.st.narrationSource('audio');
    s.st.attachNarration(0, fakeAudio(3));
    assert.equal(s.st.prepareBoard(), 0);
    s.st.narrationFinished();
    s.clock.advance(1000);
    assert.equal(s.output.classList.contains('cine-pending'), false);
    assert.equal(s.st.stats.syncFired, 0);
    // a sync plan without a camera moment (a short scene, a zoom-out): the planned camera move plays as before
    const c = stage();
    const noCamera = c.play(syncPlan([]));
    c.st.start(sceneB(), noCamera);
    assert.ok(cameraAnims(c.doc).length > 0, 'the planned move still plays');
    // a sync plan with a camera moment: no timeline labels or emphasis, the camera waits for its moment; entrances unchanged
    const t = stage();
    const synced = t.play(syncPlan([ev('c1', 'camera_focus', { layer: 'camera' }, { segment: 0, ratio: 0.9 },
        { params: { to: { x: 0.03, y: 0.03, w: 0.94 }, duration: 2 } })]));
    t.st.start(sceneB(), synced);
    assert.equal(cameraAnims(t.doc).length, 0);
    assert.equal(t.panel.animations[0].opts.delay, 300, 'the visual still scales in at 0.3 s');
    t.clock.advance(1300);
    t.st.narrationEvent('sync');
    t.st.narrationEvent('sync');
    assert.equal(t.chips().some(c => c.getAttribute('data-shown') === 'true'), false);
    assert.equal(glows(t.panel).length, 0);
});

test('an early [SYNC] beat (while the formulas are typeset, before start()) is no longer lost', () => {
    const s = stage();
    const plan = s.play(basePlan());
    s.st.narrationEvent('sync'); // the narration starts up to 800 ms before the timeline
    s.st.start(sceneB(), plan);
    assert.equal(glows(s.panel).length, 1, 'the emphasis of [SYNC] 1 shown at once');
    assert.equal(s.st.stats.anchored, 1);
    s.st.narrationEvent('sync');
    assert.equal(s.chips()[1].getAttribute('data-shown'), 'true'); // [SYNC] 2: the second label
    assert.equal(s.st.stats.anchored, 2);
    // each scene counts its own beats
    const next = s.play(basePlan());
    s.st.start(sceneB(), next);
    assert.equal(s.st.syncCount, 0);
    assert.equal(glows(s.panel).length, 1, 'no new emphasis before this scene\'s first beat');
});
