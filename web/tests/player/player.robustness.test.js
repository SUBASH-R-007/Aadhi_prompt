// @ts-check
/**
 * Player-level robustness: autoplay-blocked narration pauses the player once (no per-frame retry or
 * error spam), the mascot preloads only the lecture's clips and its watchdog is driven by the player.
 */
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex, advanceMedia } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { Player, PLAYER_EVENTS } = await import('../../js/player/player.js');

after(() => uninstallDom());

/** @type {any[]} */
let live = [];
afterEach(() => {
  for (const p of live) p.destroy();
  live = [];
  document.body.replaceChildren();
});

/** @param {'live' | 'preview'} mode */
function setup(mode = 'live') {
  let now = 0;
  /** @type {Map<number, FrameRequestCallback>} */
  const frames = new Map();
  let fid = 0;
  /** @type {any[]} */
  const sent = [];
  const root = document.createElement('div');
  document.body.appendChild(root);
  const player = new Player(root, {
    mode,
    analytics: mode === 'live' ? { shareToken: 'share-token-123456' } : null,
    deps: {
      renderTex: fakeTex().render,
      texIdle: () => Promise.resolve(),
      loadPrism: async () => null,
      createPanel: (container) => ({ el: container.appendChild(document.createElement('div')), update() {}, destroy() {} }),
      now: () => now,
      requestFrame: (cb) => {
        frames.set(++fid, cb);
        return fid;
      },
      cancelFrame: (id) => frames.delete(id),
      sendAnalytics: async (body) => {
        sent.push(body);
      },
      waitForFonts: false,
    },
  });
  live.push(player);
  /** @type {Record<string, any[]>} */
  const events = {};
  for (const name of PLAYER_EVENTS) player.on(name, (d) => (events[name] = events[name] || []).push(d));
  return {
    player,
    root,
    events,
    sent,
    /** @param {number} ms */
    step(ms) {
      now += ms;
      advanceMedia(ms / 1000);
      const pending = [...frames.values()];
      frames.clear();
      for (const cb of pending) cb(now);
    },
  };
}

const flush = () => new Promise((r) => setImmediate(r));
/** @param {string} name */
const domError = (name) => Object.assign(new Error(name), { name });

describe('Player: blocked narration', () => {
  test('NotAllowedError pauses once with a blocked event; the next play() tries again', async () => {
    const h = setup('live');
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(sceneById(timeline, 's-ohm').start + 1);
    const el = /** @type {any} */ (h.player).narration.current.el;
    let calls = 0;
    el.play = () => {
      calls++;
      return Promise.reject(domError('NotAllowedError'));
    };
    h.player.play();
    // Frames are separate tasks: the rejection is handled (a microtask) before the next frame.
    await flush();
    for (let i = 0; i < 30; i++) {
      h.step(16);
      await flush();
    }
    assert.equal(h.player.paused, true, 'the player paused itself');
    assert.equal(h.events.blocked?.length, 1);
    assert.equal(h.events.blocked[0].reason, 'autoplay');
    assert.deepEqual(h.events.pause?.at(-1)?.reason, 'blocked');
    assert.equal(h.events.error, undefined, 'no error spam');
    assert.equal(calls, 1, 'no per-frame retries');
    await /** @type {any} */ (h.player).analytics.flush();
    const pauses = h.sent.flatMap((b) => b.events || []).filter((/** @type {any} */ e) => e.event === 'pause');
    assert.deepEqual(pauses.map((/** @type {any} */ e) => e.data), [{ reason: 'blocked' }], 'analytics pause tagged');
    el.play = () => {
      calls++;
      return Promise.resolve();
    };
    h.player.play(); // the click on the big play button is the gesture
    assert.equal(calls, 2);
    assert.equal(h.player.paused, false);
  });

  test('other rejections report one error and retry at most once a second', async () => {
    const h = setup('preview');
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(sceneById(timeline, 's-ohm').start + 1);
    const el = /** @type {any} */ (h.player).narration.current.el;
    let calls = 0;
    el.play = () => {
      calls++;
      return Promise.reject(domError('NotSupportedError'));
    };
    h.player.play();
    await flush();
    for (let i = 0; i < 10; i++) {
      h.step(16);
      await flush();
    }
    assert.equal(calls, 1, 'not retried every frame');
    assert.equal(h.events.error?.length, 1);
    assert.equal(h.player.paused, false, 'only autoplay blocks pause the player');
    const real = Date.now;
    Date.now = () => real() + 1500;
    try {
      h.step(16);
      await flush();
    } finally {
      Date.now = real;
    }
    assert.equal(calls, 2, 'retried after a second');
  });
});

describe('Player: mascot wiring', () => {
  test('only the clips of the lecture positions preload eagerly', async () => {
    const h = setup('live');
    const timeline = loadTimeline();
    for (const s of timeline.scenes) if (s.layout) s.layout.mascot_position = s.scene_id === 's-example' ? 'right' : 'left';
    await h.player.load(timeline);
    const preload = (/** @type {string} */ name) =>
      /** @type {HTMLVideoElement} */ (h.root.querySelector(`.ap-mascot-clip[src$="/branding/${name}"]`)).getAttribute('preload');
    assert.equal(h.root.querySelectorAll('.ap-mascot-clip').length, 5);
    assert.equal(preload('aadhi_left.mp4'), 'auto');
    assert.equal(preload('aadhi_right.mp4'), 'auto');
    assert.equal(preload('aadhi_center.mp4'), 'metadata');
    assert.equal(preload('aadhi_popup.mp4'), 'metadata');
    assert.equal(preload('no_aadhi.mp4'), 'metadata');
  });

  test('the player drives the watchdog on frames and when the tab becomes visible', async () => {
    const h = setup('live');
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(sceneById(timeline, 's-ohm').start + 1);
    const mascot = /** @type {any} */ (h.player).mascot;
    /** @type {any[]} */
    const calls = [];
    const tick = mascot.tick.bind(mascot);
    mascot.tick = (/** @type {any} */ opts) => {
      calls.push(opts || {});
      tick(opts);
    };
    h.player.play();
    h.step(16);
    assert.ok(calls.length >= 1, 'ticked from the frame loop');
    const left = /** @type {HTMLVideoElement} */ (mascot.active);
    left.pause(); // paused by the browser while the tab was hidden
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    try {
      document.dispatchEvent(new window.Event('visibilitychange'));
    } finally {
      delete (/** @type {any} */ (document)).visibilityState;
    }
    assert.ok(calls.some((c) => c.force), 'forced tick on visibility');
    assert.equal(left.paused, false, 'the clip runs again');
  });
});
