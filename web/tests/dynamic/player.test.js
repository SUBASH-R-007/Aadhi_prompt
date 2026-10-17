// @ts-check
// The live player with a Dynamic timeline (DYNAMIC_VIDEO_DESIGN §7.3 "Live player"): the motion engine's stage draws
// every frame, the narration stays the master clock, the stage draws the captions, and classic timelines never
// load it.
import { test, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from '../player/_dom.js';
import { classicTimeline, dynamicTimeline } from './_fixture.js';

installDom();
const { Player, isDynamicTimeline } = await import('../../js/player/player.js');

after(() => uninstallDom());

/** @type {any[]} */
let players = [];
afterEach(() => {
  for (const p of players) p.destroy();
  players = [];
  document.body.replaceChildren();
});

/** A recording stand-in for createDynamicStage. */
function fakeStage() {
  const rec = { created: /** @type {any[]} */ ([]), updates: /** @type {any[]} */ ([]), layers: /** @type {any[]} */ ([]), destroyed: 0 };
  return {
    rec,
    /** @param {HTMLElement} root @param {any} timeline @param {any} opts */
    create(root, timeline, opts) {
      rec.created.push({ root, timeline, opts });
      const el = document.createElement('div');
      el.className = 'fake-dyn-stage';
      root.appendChild(el);
      return {
        update: (/** @type {number} */ t, /** @type {any} */ f) => rec.updates.push({ t, ...f }),
        setLayers: (/** @type {any} */ spec) => rec.layers.push(spec),
        destroy: () => {
          rec.destroyed += 1;
          el.remove();
        },
      };
    },
  };
}

/** @param {any} [deps] @param {any} [opts] */
function setup(deps = {}, opts = {}) {
  let now = 0;
  /** @type {Map<number, FrameRequestCallback>} */
  const frames = new Map();
  let fid = 0;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const player = new Player(root, {
    mode: 'live',
    analytics: null,
    ...opts,
    deps: {
      now: () => now,
      requestFrame: (cb) => {
        frames.set(++fid, cb);
        return fid;
      },
      cancelFrame: (id) => frames.delete(id),
      waitForFonts: false,
      ...deps,
    },
  });
  players.push(player);
  return {
    player,
    root,
    step(/** @type {number} */ ms) {
      now += ms;
      const pending = [...frames.values()];
      frames.clear();
      for (const cb of pending) cb(now);
    },
  };
}

test('isDynamicTimeline: only a presentation with style "dynamic"', () => {
  assert.equal(isDynamicTimeline(dynamicTimeline()), true);
  assert.equal(isDynamicTimeline(classicTimeline()), false);
  assert.equal(isDynamicTimeline({ presentation: { style: 'other' } }), false);
  assert.equal(isDynamicTimeline(null), false);
});

test('a Dynamic timeline is drawn by the stage: no mascot, no classic captions, stage painted per frame', async () => {
  const fake = fakeStage();
  const { player, root, step } = setup({ createDynamicStage: fake.create });
  await player.load(dynamicTimeline());
  assert.equal(fake.rec.created.length, 1);
  const { opts } = fake.rec.created[0];
  assert.equal(opts.mode, 'live');
  assert.equal(opts.captions, true);
  assert.equal(player.mascot, null);
  assert.equal(player.captions, null);
  assert.equal(root.querySelector('.ap-captions'), null, 'the stage draws the captions');
  assert.ok(root.querySelector('.ap-layer--scene .fake-dyn-stage'));
  assert.equal(player.sceneIndex, 0);
  player.seek(12);
  const last = fake.rec.updates.at(-1);
  assert.equal(last.t, 12);
  assert.equal(last.seeked, true);
  assert.equal(player.sceneIndex, 1);
  player.play();
  step(16);
  assert.ok(fake.rec.updates.at(-1).playing);
  player.setCaptions(false);
  assert.deepEqual(fake.rec.layers.at(-1), { captions: false });
});

test('a reel resizes the player stage to 1080x1920, and a classic load restores 1920x1080', async () => {
  const fake = fakeStage();
  const { player, root } = setup({ createDynamicStage: fake.create });
  await player.load(dynamicTimeline('portrait'));
  const stage = /** @type {HTMLElement} */ (root.querySelector('.ap-stage'));
  assert.equal(stage.style.width, '1080px');
  assert.equal(stage.style.height, '1920px');
  assert.equal(player.stage.width, 1080);
  await player.load(classicTimeline());
  assert.equal(fake.rec.destroyed, 1, 'the stage of the previous timeline is destroyed');
  assert.equal(stage.style.width, '1920px');
  assert.equal(stage.style.height, '1080px');
  assert.equal(fake.rec.created.length, 1, 'a classic timeline never creates the stage');
  assert.ok(player.mascot, 'classic: the mascot is back');
});

test('render mode stays classic even with a Dynamic timeline (Dynamic MP4s use /render-dynamic)', async () => {
  const fake = fakeStage();
  const { player } = setup({ createDynamicStage: fake.create, waitForFonts: false }, { mode: 'render' });
  await player.load(dynamicTimeline());
  assert.equal(fake.rec.created.length, 0);
});

test('the real stage module loads on demand and paints the lecture', async () => {
  const { player, root } = setup();
  await player.load(dynamicTimeline());
  const el = root.querySelector('.dyn-stage');
  assert.ok(el, 'web/js/dynamic/stage.js was imported and mounted');
  player.seek(1);
  assert.equal(root.querySelector('.dyn-captions')?.textContent, 'Charge flows like water.');
  player.seek(20);
  assert.equal(player.sceneIndex, 2);
  player.destroy();
  assert.equal(root.querySelector('.dyn-stage'), null);
});

test('a Dynamic timeline of a newer look engine is refused before anything is built (timeline_invalid, as the render page)', async () => {
  for (const v of [6, 9]) {
    const fake = fakeStage();
    const { player, root } = setup({ createDynamicStage: fake.create });
    const tl = dynamicTimeline('portrait');
    tl.presentation.engine_version = v;
    await assert.rejects(player.load(tl), (/** @type {any} */ e) => e.code === 'timeline_invalid' && /look engine/.test(e.message));
    assert.equal(fake.rec.created.length, 0, 'the stage is never created');
    assert.equal(player.timeline, null, 'nothing was loaded');
    const stage = /** @type {HTMLElement} */ (root.querySelector('.ap-stage'));
    assert.equal(stage.style.width, '1920px', 'not even resized for the reel');
    // the same player loads a timeline it can draw afterwards: step 5's, step 4's, the director's, B2a's and B1's look
    // engines
    const drawn = [5, 4, 3, 2, 1];
    for (const ok of drawn) {
      tl.presentation.engine_version = ok;
      await player.load(tl);
      assert.equal(player.timeline, tl);
      assert.equal(stage.style.width, '1080px');
    }
    assert.equal(fake.rec.created.length, drawn.length);
  }
  // render mode never draws a Dynamic timeline with the stage, so it never asks
  const fake = fakeStage();
  const { player } = setup({ createDynamicStage: fake.create, waitForFonts: false }, { mode: 'render' });
  const tl = dynamicTimeline();
  tl.presentation.engine_version = 6;
  await player.load(tl);
  assert.equal(fake.rec.created.length, 0);
});
