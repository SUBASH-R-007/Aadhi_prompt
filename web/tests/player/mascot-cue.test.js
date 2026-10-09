// @ts-check
/**
 * The cue bubble beside Aadhi (MascotController.mountCue / setState) and its wiring in the player:
 * live frames and render-mode states show the bubble the pure schedule computes, in the scene layer
 * (captured by the MP4 screenshots), at the same stage pixels.
 */
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { MascotController } = await import('../../js/player/mascot.js');
const { Player } = await import('../../js/player/player.js');
const { CUE_ANCHORS, THINK_DELAY } = await import('../../js/player/mascot-state.js');
const { sceneStateAt } = await import('../../js/player/schedule.js');

after(() => uninstallDom());

/** @type {{ destroy(): void }[]} */
let made = [];
afterEach(() => {
  for (const m of made) m.destroy();
  made = [];
  document.body.replaceChildren();
});

const branding = /** @type {any} */ (loadTimeline().branding);

/** @param {'live' | 'preview' | 'render'} mode */
function controller(mode) {
  const layer = document.createElement('div');
  const sceneRoot = document.createElement('div');
  document.body.append(layer, sceneRoot);
  const m = new MascotController(layer, branding, { mode });
  made.push(m);
  return { m, layer, sceneRoot };
}

/** @param {ParentNode} root */
const bubble = (root) => /** @type {HTMLElement | null} */ (root.querySelector('.ap-mascot-cue'));

describe('MascotController cue bubble', () => {
  test('mountCue places the bubble beside the head of the position, inside the given root', () => {
    for (const mode of /** @type {const} */ (['live', 'render'])) {
      const { m, layer, sceneRoot } = controller(mode);
      m.mountCue(sceneRoot, 'right');
      const el = /** @type {HTMLElement} */ (bubble(sceneRoot));
      assert.ok(el, mode);
      assert.equal(bubble(layer), null, 'not in the mascot layer (hidden in render mode)');
      assert.equal(el.style.left, `${CUE_ANCHORS.right.x}px`);
      assert.equal(el.style.top, `${CUE_ANCHORS.right.y}px`);
      assert.equal(el.getAttribute('aria-hidden'), 'true');
      assert.equal(el.querySelectorAll('.ap-mascot-cue-bubble > i').length, 3);
      assert.equal(el.classList.contains('is-shown'), false);
    }
  });

  test('popup and hidden positions get no bubble; a new scene replaces the old one', () => {
    const { m, sceneRoot } = controller('live');
    m.mountCue(sceneRoot, 'left');
    assert.ok(bubble(sceneRoot));
    m.mountCue(sceneRoot, 'popup_bottom_left');
    assert.equal(bubble(sceneRoot), null);
    m.setState('success', 'success');
    assert.equal(m.getStatus().cue, null, 'nothing to show without a bubble');
    assert.equal(m.getStatus().state, 'success');
    m.mountCue(sceneRoot, 'hidden');
    assert.equal(bubble(sceneRoot), null);
  });

  test('setState shows the cue, keeps its content while fading out, and writes data-state', () => {
    const { m, layer, sceneRoot } = controller('live');
    m.mountCue(sceneRoot, 'center');
    const el = /** @type {HTMLElement} */ (bubble(sceneRoot));
    m.setState('thinking', 'dots');
    assert.equal(el.dataset.cue, 'dots');
    assert.ok(el.classList.contains('is-shown'));
    assert.equal(/** @type {HTMLElement} */ (layer.querySelector('.ap-mascot')).dataset.state, 'thinking');
    m.setState('explaining', null);
    assert.equal(el.classList.contains('is-shown'), false);
    assert.equal(el.dataset.cue, 'dots', 'the dots stay while the bubble fades out');
    assert.deepEqual([m.getStatus().state, m.getStatus().cue], ['explaining', null]);
    m.setState('success', 'success');
    assert.equal(el.dataset.cue, 'success');
    m.setState(undefined, undefined);
    assert.equal(m.getStatus().state, 'idle');
  });

  test('the dots hold still while the lecture is paused; destroy removes the bubble', () => {
    const { m, sceneRoot } = controller('live');
    m.mountCue(sceneRoot, 'left');
    const el = /** @type {HTMLElement} */ (bubble(sceneRoot));
    assert.ok(el.classList.contains('is-paused'));
    m.setPlaying(true);
    assert.equal(el.classList.contains('is-paused'), false);
    m.setPlaying(false);
    assert.ok(el.classList.contains('is-paused'));
    m.destroy();
    made = [];
    assert.equal(bubble(sceneRoot), null);
  });
});

/** @param {'live' | 'render'} mode */
function player(mode) {
  let now = 0;
  /** @type {Map<number, FrameRequestCallback>} */
  const frames = new Map();
  let fid = 0;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const tex = fakeTex();
  const p = new Player(root, {
    mode,
    deps: {
      renderTex: tex.render,
      texIdle: () => Promise.resolve(),
      loadPrism: async () => null,
      createPanel: (container) => {
        const el = document.createElement('div');
        container.appendChild(el);
        return { el, update() {}, destroy() { el.remove(); } };
      },
      panelStateTimes: () => [],
      now: () => now,
      requestFrame: (cb) => {
        frames.set(++fid, cb);
        return fid;
      },
      cancelFrame: (id) => frames.delete(id),
      waitForFonts: false,
    },
  });
  made.push(p);
  return { p, root };
}

describe('player wiring', () => {
  test('render mode: the thinking state is its own screenshot with the dots bubble in the scene layer', async () => {
    const { p, root } = player('render');
    const timeline = loadTimeline();
    await p.load(timeline);
    const index = timeline.scenes.findIndex((s) => s.scene_id === 's-example');
    const example = timeline.scenes[index];
    const from = 4.35 + THINK_DELAY; // b2: speech ends 4.35, next beat at 6.5
    const states = p.states(index);
    const thinking = states.find((s) => Math.abs(s.t - from) < 1e-9);
    assert.ok(thinking, 'a state starts with the bubble');
    let r = await p.renderState({ sceneIndex: index, t: from });
    assert.equal(r.state_key, thinking.key);
    const el = /** @type {HTMLElement} */ (root.querySelector('.ap-layer--scene .ap-scene .ap-mascot-cue'));
    assert.ok(el && el.classList.contains('is-shown'));
    assert.equal(el.dataset.cue, 'dots');
    assert.equal(el.style.left, `${CUE_ANCHORS.right.x}px`, 'the example scene stands right');
    r = await p.renderState({ sceneIndex: index, t: 3 });
    assert.equal(sceneStateAt(example, 3).mascotCue, null);
    assert.equal(el.classList.contains('is-shown'), false);
    // the popup quiz has no bubble at all
    await p.renderState({ sceneIndex: timeline.scenes.findIndex((s) => s.scene_id === 's-quiz'), t: 9 });
    assert.equal(root.querySelector('.ap-mascot-cue'), null);
  });

  test('live: seeking shows the state of the target time (no event history)', async () => {
    const { p, root } = player('live');
    const timeline = loadTimeline();
    await p.load(timeline);
    const example = sceneById(timeline, 's-example');
    const mascot = /** @type {any} */ (p).mascot;
    p.seek(example.start + 5.0);
    assert.deepEqual([mascot.getStatus().state, mascot.getStatus().cue], ['thinking', 'dots']);
    assert.ok(root.querySelector('.ap-mascot-cue.is-shown[data-cue="dots"]'));
    p.seek(example.start + 1.0);
    assert.deepEqual([mascot.getStatus().state, mascot.getStatus().cue], ['explaining', null]);
    assert.equal(root.querySelector('.ap-mascot-cue.is-shown'), null);
    p.seek(example.start + 5.0); // back again: same as the first visit
    assert.deepEqual([mascot.getStatus().state, mascot.getStatus().cue], ['thinking', 'dots']);
    assert.equal(/** @type {HTMLElement} */ (root.querySelector('.ap-mascot')).dataset.state, 'thinking');
  });
});
