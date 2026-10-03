'use strict';
// Unit tests for the Aadhi mascot controller (mascot.js).
// Run from the repo root:  node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const {
    MascotController, PlaybackGate, resolvePlacement, requiredAssetKeys, coverPoint
} = require('../mascot.js');
const { buildMascotDom } = require('./helpers/fake-dom.js');

const flush = () => new Promise(resolve => setImmediate(resolve));

// A controller over the fake #mascot-bg markup, with timers under the test's control
function setup(t, { extraLayers, withGate = false, before } = {}) {
    t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
    t.mock.method(console, 'warn', () => {});
    const dom = buildMascotDom({ extraLayers });
    if (before) before(dom);
    const gate = withGate ? new PlaybackGate(dom.doc) : undefined;
    const mascot = new MascotController(dom.container, { gate });
    t.after(() => mascot.destroy());
    return { ...dom, gate, mascot, tick: ms => t.mock.timers.tick(ms) };
}

const visible = layers => Object.keys(layers).filter(k => layers[k].classList.contains('active-layer'));
const playing = layers => Object.keys(layers).filter(k => !layers[k].paused).sort();
const cue = container => container.querySelectorAll('.mascot-cue')[0].getAttribute('data-cue');
const currentFrame = container =>
    container.querySelectorAll('.mascot-fallback-frame').find(img => img.classList.contains('is-current'));

test('starts idle with the left clip on screen and playing', t => {
    const { mascot, container, layers } = setup(t);
    assert.equal(mascot.getCurrentState(), 'idle');
    assert.equal(container.getAttribute('data-state'), 'idle');
    assert.deepEqual(visible(layers), ['left']);
    assert.deepEqual(playing(layers), ['left']);
    assert.equal(mascot.getStatus().fallback, null);
});

test('state transitions are recorded and unknown states are rejected', t => {
    const { mascot, container } = setup(t);
    assert.equal(mascot.setState('talking'), true);
    const { from, to } = mascot.getStatus().lastTransition;
    assert.deepEqual({ from, to }, { from: 'idle', to: 'talking' });
    assert.equal(container.getAttribute('data-state'), 'talking');

    assert.equal(mascot.setState('dancing'), false);
    assert.equal(mascot.getCurrentState(), 'talking');
});

test('repeating the current state does not reload or restart the clip', t => {
    const { mascot, layers } = setup(t);
    layers.left.finishLoading();
    mascot.beginScene('left');
    mascot.narrationEvent('segment');
    assert.equal(mascot.narrationEvent('sync'), true);
    const before = {
        plays: layers.left.playCalls,
        loads: layers.left.loadCalls,
        srcs: layers.left.srcHistory.length,
        transition: mascot.getStatus().lastTransition
    };

    // Every further [SYNC] reveal and every repeated setState is a no-op
    assert.equal(mascot.narrationEvent('sync'), false);
    assert.equal(mascot.narrationEvent('sync'), false);
    assert.equal(mascot.setState('explaining'), false);

    assert.deepEqual({
        plays: layers.left.playCalls,
        loads: layers.left.loadCalls,
        srcs: layers.left.srcHistory.length,
        transition: mascot.getStatus().lastTransition
    }, before);
});

test('a state-specific clip crossfades in only when the state changes', t => {
    const { mascot, layers, tick } = setup(t, { extraLayers: [{ asset: 'left', state: 'talking' }] });
    const talking = layers['left/talking'];
    layers.left.finishLoading();

    mascot.setState('talking');
    // The incoming clip has no frame yet, so the current one stays on screen
    assert.deepEqual(visible(layers), ['left']);
    assert.equal(talking.paused, false);
    talking.finishLoading();
    assert.deepEqual(visible(layers), ['left/talking']);
    tick(600);
    assert.deepEqual(playing(layers), ['left/talking']);

    const plays = talking.playCalls;
    mascot.setState('talking');
    assert.equal(talking.playCalls, plays);

    mascot.setState('idle');
    assert.deepEqual(visible(layers), ['left']);
});

test('preload loads the lesson clips and resolves with their status', async t => {
    const { mascot, layers } = setup(t);
    const done = mascot.preload(['left', 'popup']);
    assert.equal(layers.popup.preload, 'auto');
    assert.equal(layers.popup.loadCalls, 1);
    assert.equal(layers.right.preload, 'metadata'); // not used by this lesson

    layers.left.finishLoading();
    layers.popup.finishLoading();
    const assets = await done;
    assert.equal(assets.left, 'loaded');
    assert.equal(assets.popup, 'loaded');
    assert.equal(assets.right, 'idle');
});

test('preload retries a failing clip, then gives up without blocking the lesson', async t => {
    const { mascot, layers, tick } = setup(t);
    const done = mascot.preload(['right']);

    layers.right.fail();
    assert.equal(mascot.getStatus().assets.right, 'error');
    tick(1000); // first retry, with a fresh URL
    assert.match(layers.right.src, /retry=1/);
    layers.right.fail();
    tick(3000); // second retry
    layers.right.fail();

    const assets = await done;
    assert.equal(assets.right, 'failed');
    assert.equal(layers.right.srcHistory.length, 3);
    assert.equal(console.warn.mock.callCount(), 1); // one warning, no console flood
});

test('preload stops waiting after its timeout', async t => {
    const { mascot, tick } = setup(t);
    let settled = false;
    const done = mascot.preload(['center'], 5000);
    done.then(() => { settled = true; });

    tick(4999);
    await flush();
    assert.equal(settled, false);
    tick(1);
    const assets = await done;
    assert.equal(assets.center, 'loading'); // keeps loading in the background
});

test('an on-screen clip error shows the animated fallback, then recovers', t => {
    const { mascot, container, layers, tick } = setup(t);
    layers.left.finishLoading();

    layers.left.fail();
    assert.ok(container.classList.contains('mascot-fallback-active'));
    assert.match(mascot.getStatus().fallback, /retrying/);
    assert.equal(currentFrame(container).getAttribute('src'), 'video_template/posters/aadhi_left.jpg');

    tick(1000);
    layers.left.fail();
    tick(3000);
    layers.left.fail();
    assert.equal(mascot.getStatus().fallback, 'clip failed to load');
    assert.equal(mascot.getStatus().playback, 'FAILED');

    // The next lesson gives it another chance; once it plays, the fallback steps aside
    mascot.reset();
    layers.left.finishLoading();
    assert.equal(mascot.getStatus().fallback, null);
    assert.ok(!container.classList.contains('mascot-fallback-active'));
});

test('a clip that ends unexpectedly restarts instead of freezing', t => {
    const { mascot, layers } = setup(t);
    layers.left.finishLoading();
    layers.left.currentTime = 7.9;
    const plays = layers.left.playCalls;

    layers.left.end();
    assert.equal(layers.left.currentTime, 0);
    assert.equal(layers.left.paused, false);
    assert.equal(layers.left.playCalls, plays + 1);
    assert.equal(mascot.getStatus().fallback, null);
});

test('a clip that cannot restart after ending falls back to the animation', async t => {
    const { mascot, layers, tick } = setup(t);
    layers.left.finishLoading();
    layers.left.playResult = 'NotSupportedError';

    layers.left.end(); // restart attempt 1
    await flush();
    tick(1000); // watchdog attempt 2
    await flush();
    assert.equal(mascot.getStatus().fallback, null);
    tick(1000); // attempt 3
    await flush();
    assert.equal(mascot.getStatus().fallback, 'playback keeps failing');
});

test('only the on-screen clip keeps playing', t => {
    const { mascot, layers, tick } = setup(t);
    layers.left.finishLoading();
    layers.right.finishLoading();

    mascot.setPlacement('right');
    assert.deepEqual(visible(layers), ['right']);
    assert.deepEqual(playing(layers), ['left', 'right']); // both during the 0.5s crossfade
    tick(600);
    assert.deepEqual(playing(layers), ['right']);

    // Anything else that starts a mascot clip is stopped at once
    layers.center.readyState = 4;
    layers.center.play();
    assert.deepEqual(playing(layers), ['right']);
});

test('rapid placement changes never leave two clips playing', t => {
    const { mascot, layers, tick } = setup(t);
    layers.left.finishLoading();

    mascot.setPlacement('center'); // not loaded: left stays on screen meanwhile
    mascot.setPlacement('popup_bottom_right');
    mascot.setPlacement('left');
    tick(2000);
    assert.deepEqual(visible(layers), ['left']);
    assert.deepEqual(playing(layers), ['left']);
});

test('narration drives talking, explaining, thinking and idle', t => {
    const { mascot } = setup(t);
    mascot.beginScene('right');
    const seen = [];
    const step = (type, state) => {
        mascot.narrationEvent(type, state);
        seen.push(mascot.getCurrentState());
    };
    step('segment'); // audio for segment 1 starts
    step('sync');    // [SYNC] reveal
    step('sync');
    step('pause');   // [PAUSE] beat
    step('segment');
    step('sync');
    step('hold');    // viewer pressed pause
    step('resume');
    step('end');
    assert.deepEqual(seen, [
        'talking', 'explaining', 'explaining', 'thinking', 'talking', 'explaining', 'idle', 'explaining', 'idle'
    ]);
    assert.equal(mascot.getStatus().audio, 'SILENT');
});

test('the optional scene field mascot_state sets how Aadhi narrates', t => {
    const { mascot } = setup(t);
    mascot.beginScene('center', 'EXPLAINING ');
    mascot.narrationEvent('segment');
    assert.equal(mascot.getCurrentState(), 'explaining');

    mascot.beginScene('left', 'moonwalk'); // unknown values fall back to talking
    assert.equal(mascot.getCurrentState(), 'idle');
    mascot.narrationEvent('segment');
    assert.equal(mascot.getCurrentState(), 'talking');
});

test('quiz checkpoint: question, thinking, then success', t => {
    const { mascot, container } = setup(t);
    mascot.beginScene('center');

    mascot.narrationEvent('segment', 'question'); // question narration
    assert.equal(mascot.getCurrentState(), 'question');
    assert.equal(cue(container), 'question');
    mascot.narrationEvent('sync'); // reveals never override the question
    assert.equal(mascot.getCurrentState(), 'question');

    mascot.narrationEvent('end');
    mascot.setState('thinking'); // countdown
    assert.equal(cue(container), 'thinking');

    mascot.setState('success'); // answer revealed
    assert.equal(mascot.narrationEvent('segment', 'success'), false); // reveal narration keeps it
    assert.equal(cue(container), 'success');
    mascot.narrationEvent('end');
    assert.equal(mascot.getCurrentState(), 'idle');
    assert.equal(cue(container), '');

    // No bubble when Aadhi is off screen, or behind the board in the popup layout
    mascot.beginScene('hidden');
    mascot.setState('question');
    assert.equal(cue(container), '');
    mascot.beginScene('popup_bottom_left');
    mascot.setState('question');
    assert.equal(cue(container), '');
});

test('recording keeps Aadhi animating and keeps the debug HUD out of the video', t => {
    const { mascot, doc, container, layers } = setup(t);
    layers.left.finishLoading();
    mascot.setDebug(true);
    const hud = doc.querySelectorAll('.mascot-debug-hud')[0];
    assert.match(hud.textContent, /State: IDLE/);
    assert.match(hud.textContent, /Playback: PLAYING/);
    assert.match(hud.textContent, /Fallback: NO/);

    mascot.pause();
    assert.deepEqual(playing(layers), []);
    mascot.setRecording(true);
    assert.deepEqual(playing(layers), ['left']);
    assert.deepEqual(visible(layers), ['left']);
    assert.ok(hud.classList.contains('is-recording'));
    assert.ok(!container.classList.contains('mascot-fallback-active')); // nothing covers the live clip

    mascot.setRecording(false);
    assert.ok(!hud.classList.contains('is-recording'));
});

test('fallback: poster frame, talking indicator, last-resort still', t => {
    const { mascot, container, layers, tick } = setup(t);
    layers.left.fail();
    assert.equal(currentFrame(container).getAttribute('src'), 'video_template/posters/aadhi_left.jpg');

    mascot.beginScene('left');
    mascot.narrationEvent('segment');
    assert.equal(cue(container), 'talking'); // shown only while the fallback is up

    // A new placement crossfades to that clip's poster
    mascot.setPlacement('center');
    tick(1500);
    assert.equal(currentFrame(container).getAttribute('src'), 'video_template/posters/aadhi_center.jpg');
    assert.equal(container.querySelectorAll('.mascot-fallback-frame').filter(f => f.classList.contains('is-current')).length, 1);

    // Poster missing too: the empty studio still, never a black screen
    currentFrame(container).dispatch('error');
    assert.equal(currentFrame(container).getAttribute('src'), 'video_template/static_background.png');
});

test('autoplay block: fallback until one Enable playback click resumes Aadhi', async t => {
    const { mascot, doc, layers, tick } = setup(t, {
        withGate: true,
        before: dom => { dom.layers.left.playResult = 'NotAllowedError'; }
    });
    await flush();
    const button = doc.querySelectorAll('.playback-gate')[0];
    assert.ok(button.classList.contains('visible'));
    assert.equal(mascot.getStatus().gestureRequired, true);
    assert.match(mascot.getStatus().fallback, /autoplay blocked/);

    // No play() hammering while waiting for the click
    const attempts = layers.left.playCalls;
    tick(3000);
    await flush();
    assert.equal(layers.left.playCalls, attempts);

    layers.left.playResult = null;
    button.dispatch('click');
    assert.ok(!button.classList.contains('visible'));
    assert.equal(layers.left.paused, false);
    layers.left.finishLoading();
    assert.equal(mascot.getStatus().fallback, null);
});

test('one click retries every blocked request', t => {
    const { doc } = buildMascotDom();
    const gate = new PlaybackGate(doc);
    const ran = [];
    gate.request(() => ran.push('narration'));
    gate.request(() => ran.push('mascot'));
    assert.equal(gate.isBlocked(), true);
    doc.querySelectorAll('.playback-gate')[0].dispatch('click');
    assert.deepEqual(ran, ['narration', 'mascot']);
    assert.equal(gate.isBlocked(), false);
});

test('a frozen clip gets the fallback at once, then a reload', t => {
    const { mascot, layers, tick } = setup(t);
    layers.left.finishLoading();
    layers.left.advance(0.5);

    tick(3000);
    assert.equal(mascot.getStatus().fallback, null);
    tick(1000); // no progress for 4s
    assert.equal(mascot.getStatus().fallback, 'clip stalled');

    const srcs = layers.left.srcHistory.length;
    tick(4000); // still frozen: reload with a fresh URL
    assert.equal(layers.left.srcHistory.length, srcs + 1);
    layers.left.finishLoading();
    assert.equal(mascot.getStatus().fallback, null);
});

test('re-initialising on an exported copy does not duplicate runtime nodes', t => {
    t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
    const { doc, container } = buildMascotDom();
    new MascotController(container, { debug: true }).destroy();
    const second = new MascotController(container, { debug: true });
    t.after(() => second.destroy());
    assert.equal(doc.querySelectorAll('.mascot-fallback').length, 1);
    assert.equal(doc.querySelectorAll('.mascot-cue').length, 1);
    assert.equal(doc.querySelectorAll('.mascot-debug-hud').length, 1);
});

test('resolvePlacement keeps the existing layout rules and repairs common generator slips', () => {
    const cases = [
        [{ aadhi_position: 'right' }, 'right'],
        [{ aadhi_position: 'Left ' }, 'left'],
        [{ aadhi_position: 'popup' }, 'popup_bottom_left'],
        [{ aadhi_position: 'sideways' }, 'hidden'],
        [{ type: 'ai_video' }, 'left'],
        [{ type: 'ai_video', aadhi_position: 'hidden' }, 'left'],
        [{ type: 'ai_video', aadhi_position: 'right' }, 'right'],
        [{ type: 'title' }, 'left'],
        [{ type: 'content', html: 'x'.repeat(301) }, 'hidden'],
        [{ type: 'quiz_checkpoint' }, 'hidden'],
        [{ type: 'content', force_background: 'tree' }, 'hidden'],
        [{ type: 'simulation', force_background: 'mascot' }, 'left']
    ];
    cases.forEach(([slide, expected]) => assert.equal(resolvePlacement(slide), expected, JSON.stringify(slide)));
});

test('requiredAssetKeys lists each clip a lesson needs once', () => {
    const slides = [
        { type: 'title', aadhi_position: 'left' },
        { type: 'content', aadhi_position: 'popup_bottom_right' },
        { type: 'content', aadhi_position: 'popup_bottom_left' },
        { type: 'quiz_checkpoint' },
        { type: 'ai_video' }
    ];
    assert.deepEqual(requiredAssetKeys(slides).sort(), ['left', 'none', 'popup']);
});

test('coverPoint maps clip coordinates under object-fit: cover', () => {
    assert.deepEqual(coverPoint(0.5, 0.5, 1600, 900), { x: 800, y: 450 });
    // 4:3 viewport crops the clip's sides: the frame renders 1600px wide, offset -200px
    assert.deepEqual(coverPoint(0.25, 0.5, 1200, 900), { x: 200, y: 450 });
});

test('playback measurements count stalls, fallbacks, switches and reloads', t => {
    const { mascot, layers, tick } = setup(t);
    layers.left.finishLoading();
    layers.left.advance(0.5);
    mascot.resetMetrics();

    mascot.setPlacement('right');
    layers.right.finishLoading();
    layers.right.advance(0.4);
    tick(4000); // frozen: stall and fallback
    tick(4000); // still frozen: a second strike reloads it
    layers.right.finishLoading(); // playing again: the fallback steps aside

    const m = mascot.getMetrics();
    assert.equal(m.switches, 1);
    assert.equal(m.stalls, 2);
    assert.equal(m.fallbacks, 1); // one fallback episode, however many strikes
    assert.deepEqual(m.fallbackReasons, { 'clip stalled': 1 });
    assert.equal(m.reloads, 1);
    assert.equal(m.inFallback, false);
    assert.ok(m.fallbackMs >= 4000);
    const stall = m.events.find(e => e.type === 'stall');
    assert.equal(stall.asset, 'right');
    assert.ok(stall.frozenMs >= 3500);
    assert.ok('readyState' in stall && 'ahead' in stall); // enough to tell buffering from a starved decoder
    assert.ok(m.events.some(e => e.type === 'recovered'));

    mascot.resetMetrics();
    assert.deepEqual([mascot.getMetrics().stalls, mascot.getMetrics().events.length], [0, 0]);
});

test('a clip covered by a cinematic background stops decoding and plays again when uncovered (Phase 13)', t => {
    const { mascot, layers, tick } = setup(t);
    assert.deepEqual(playing(layers), ['left']);
    mascot.suspend(true);
    assert.deepEqual(playing(layers), []);
    mascot.setRecording(true); // recording normally forces Aadhi to play: not while he is covered
    mascot.beginScene('right');
    tick(5000);
    assert.deepEqual(playing(layers), []);
    assert.equal(mascot.getStatus().fallback, null); // a covered clip is paused on purpose, not stalled
    mascot.suspend(false);
    assert.deepEqual(playing(layers), ['right']);
    mascot.pause();
    mascot.suspend(true);
    mascot.suspend(false);
    assert.deepEqual(playing(layers), []); // it was paused before being covered: it stays paused
    mascot.setRecording(false);
});
