// @ts-check
// The live player's shell around a Dynamic stage (DYNAMIC_VIDEO_DESIGN §7.3 "Live player", §9.7): the page's box takes
// a reel's shape, the captions are lifted from the stage's own caption band, their size is the video's (the size
// control says so and the viewer's saved size survives), and a Dynamic lecture does no classic work it has no use for:
// no quiz tick/ding download, no classic scene state without music to duck, no painting in a hidden tab, one report
// per stage failure instead of one per frame. Classic lectures keep their behaviour (web/tests/player/*.test.js).
import { test, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { installDom, uninstallDom, tick } from './_dom.js';
import { classicTimeline, dynamicTimeline } from '../dynamic/_fixture.js';

installDom();
const { Player, MAX_STAGE_FAILURES, BACKGROUND_TICK_MS } = await import('../../js/player/player.js');
const { Controls, CAPTION_SIZE_FIXED_LABEL } = await import('../../js/player/controls.js');

const here = dirname(fileURLToPath(import.meta.url));
const css = (/** @type {string} */ name) => readFileSync(join(here, '..', '..', 'css', name), 'utf8').replace(/\r\n/g, '\n');

after(() => uninstallDom());

/** @type {{ destroy(): void }[]} */
let made = [];
afterEach(() => {
  for (const m of made.splice(0).reverse()) m.destroy();
  document.body.replaceChildren();
});

/**
 * A recording stand-in for createDynamicStage; `fail(t)` returns an error to throw from update(t), or null.
 * @param {(t: number) => Error | null} [fail]
 */
function fakeStage(fail = () => null) {
  const rec = { updates: /** @type {any[]} */ ([]), created: 0 };
  return {
    rec,
    /** @param {HTMLElement} root */
    create(root) {
      rec.created += 1;
      const el = document.createElement('div');
      el.className = 'fake-dyn-stage';
      root.appendChild(el);
      return {
        update: (/** @type {number} */ t, /** @type {any} */ f) => {
          rec.updates.push({ t, ...f });
          const err = fail(t);
          if (err) throw err;
        },
        setLayers() {},
        destroy: () => el.remove(),
      };
    },
  };
}

/**
 * @param {any} [deps]
 * @param {any} [opts]
 */
function setup(deps = {}, opts = {}) {
  let now = 0;
  /** @type {Map<number, FrameRequestCallback>} */
  const frames = new Map();
  let fid = 0;
  const root = document.createElement('div');
  root.className = 'watch-player';
  document.body.appendChild(root);
  const player = new Player(root, {
    mode: 'live',
    analytics: null,
    ...opts,
    deps: {
      now: () => now,
      requestFrame: (/** @type {FrameRequestCallback} */ cb) => {
        frames.set(++fid, cb);
        return fid;
      },
      cancelFrame: (/** @type {number} */ id) => frames.delete(id),
      waitForFonts: false,
      ...deps,
    },
  });
  made.push(player);
  /** @type {Record<string, any[]>} */
  const events = { error: [], timeupdate: [] };
  for (const name of Object.keys(events)) player.on(name, (/** @type {any} */ d) => events[name].push(d));
  return {
    player,
    root,
    events,
    /** advance the fake clock and run the animation frames */
    step(/** @type {number} */ ms) {
      now += ms;
      const pending = [...frames.values()];
      frames.clear();
      for (const cb of pending) cb(now);
    },
    /** advance the fake clock only (a hidden tab: no animation frames) */
    advance(/** @type {number} */ ms) {
      now += ms;
    },
  };
}

const shape = (/** @type {HTMLElement} */ root) => ({
  shape: root.dataset.stageShape,
  w: root.style.getPropertyValue('--ap-stage-w'),
  h: root.style.getPropertyValue('--ap-stage-h'),
});
const NONE = { shape: undefined, w: '', h: '' };

// --- the page's box follows the stage --------------------------------------------------------------------------------

test('a reel tells its box the 9:16 shape; classic and 16:9 lectures never touch the box', async () => {
  const fake = fakeStage();
  const { player, root } = setup({ createDynamicStage: fake.create });
  await player.load(dynamicTimeline('portrait'));
  assert.deepEqual(shape(root), { shape: 'portrait', w: '1080', h: '1920' });
  await player.load(classicTimeline());
  assert.deepEqual(shape(root), NONE, 'back to the classic 16:9 box');
  await player.load(dynamicTimeline('landscape'));
  assert.deepEqual(shape(root), NONE, 'a 16:9 Dynamic lecture keeps the 16:9 box');
  // a portrait screenplay rendered classic (DYNAMIC_ENABLED off): the classic stage is 1920x1080 whatever the size says
  const flagOff = dynamicTimeline('portrait');
  delete flagOff.presentation;
  for (const s of flagOff.scenes) delete s.look;
  await player.load(flagOff);
  assert.deepEqual(shape(root), NONE);
  assert.equal(player.stage.width, 1920);
  await player.load(dynamicTimeline('portrait'));
  player.destroy();
  assert.deepEqual(shape(root), NONE, 'destroy gives the box back as it was');
});

test('the watch and Studio boxes take the announced shape; their 16:9 rules are unchanged', () => {
  const player = css('player.css');
  assert.match(player, /\.watch-player \{\n {2}position: relative;\n {2}width: min\(100%, 1400px, calc\(\(100vh - 160px\) \* 16 \/ 9\)\);\n {2}aspect-ratio: 16 \/ 9;/);
  const shaped = /\.watch-player\[data-stage-shape\] \{([^}]*)\}/.exec(player);
  assert.ok(shaped, 'player.css: .watch-player[data-stage-shape]');
  assert.match(shaped[1], /aspect-ratio: var\(--ap-stage-w\) \/ var\(--ap-stage-h\);/);
  assert.match(shaped[1], /width: min\(100%, 1400px, calc\(\(100vh - 160px\) \* var\(--ap-stage-w\) \/ var\(--ap-stage-h\)\)\);/);
  assert.ok(player.indexOf('.watch-player[data-stage-shape]') < player.indexOf('.watch-player:fullscreen'), 'fullscreen still wins');
  const studio = css('studio.css');
  assert.match(studio, /\.preview-box \{\n {2}position: relative;\n {2}width: 100%;\n {2}aspect-ratio: 16 \/ 9;/);
  const box = /\.preview-box\[data-stage-shape\] \{([^}]*)\}/.exec(studio);
  assert.ok(box, 'studio.css: .preview-box[data-stage-shape]');
  assert.match(box[1], /aspect-ratio: var\(--ap-stage-w\) \/ var\(--ap-stage-h\);/);
  assert.match(box[1], /width: min\(100%, calc\(60vh \* var\(--ap-stage-w\) \/ var\(--ap-stage-h\)\)\);/);
});

test('a reel box is measured on the small viewport (svh), after a 100vh fallback; the 16:9 box keeps 100vh', () => {
  const player = css('player.css');
  const shaped = /\.watch-player\[data-stage-shape\] \{([^}]*)\}/.exec(player);
  assert.ok(shaped);
  const widths = shaped[1].split('\n').filter((line) => /^\s*width:/.test(line)).map((line) => line.trim());
  // A browser without svh drops the second line and keeps the first (a phone's large viewport, toolbars retracted).
  assert.deepEqual(widths, [
    'width: min(100%, 1400px, calc((100vh - 160px) * var(--ap-stage-w) / var(--ap-stage-h)));',
    'width: min(100%, 1400px, calc((100svh - 160px) * var(--ap-stage-w) / var(--ap-stage-h)));',
  ]);
  const classic = /\n\.watch-player \{([^}]*)\}/.exec(player);
  assert.ok(classic);
  assert.doesNotMatch(classic[1], /svh/);
});

test('only a shaped box is the control bar\'s size container: classic and 16:9 boxes are untouched', () => {
  // The bar's fit to a narrow reel box (player.css section 12) is checked in Chromium by
  // tests/web/test_player_controls_fit.py; here, that every rule of it is gated on the shape.
  const player = css('player.css').replace(/\/\*[\s\S]*?\*\//g, '');
  const studio = css('studio.css').replace(/\/\*[\s\S]*?\*\//g, '');
  const declares = (/** @type {string} */ text) => [...text.matchAll(/([^{}]*)\{[^{}]*\bcontainer(?:-name)?:\s*ap-shaped-box\b/g)].map((m) => m[1].trim());
  assert.deepEqual(declares(player), ['.watch-player[data-stage-shape]']);
  assert.deepEqual(declares(studio), ['.preview-box[data-stage-shape]']);
  const queries = [...player.matchAll(/@container\s+([\w-]+)?\s*\(max-width: (\d+)px\)/g)].filter((m) => m[1] === 'ap-shaped-box').map((m) => Number(m[2]));
  assert.deepEqual(queries, [720, 410, 370, 260, 220], 'the phone bar, then without mute, the time, the scene list, the speed');
  assert.doesNotMatch(studio, /@container/, 'studio.css only declares the container');
});

// --- captions: lift and size -----------------------------------------------------------------------------------------

test('captionBottom: the stage caption band (48 px in 16:9, 680 px in 9:16), null for classic captions', async () => {
  const fake = fakeStage();
  const { player } = setup({ createDynamicStage: fake.create });
  await player.load(dynamicTimeline('portrait'));
  assert.equal(player.captionBottom, 680);
  await player.load(dynamicTimeline('landscape'));
  assert.equal(player.captionBottom, 48);
  await player.load(classicTimeline());
  assert.equal(player.captionBottom, null);
});

/**
 * Controls over a player with faked boxes (jsdom has no layout): the stage scaled 0.5, bottom at 540, bar top at 480,
 * so the bar covers 120 stage px (classic captions would rise 120 - 40 + 14 = 94 px).
 * @param {any} timeline
 */
async function liftWith(timeline) {
  const fake = fakeStage();
  const { player, root } = setup({ createDynamicStage: fake.create });
  await player.load(timeline);
  const controls = new Controls(root, player, { idleMs: 60_000 });
  made.push(controls);
  player.stage.el.getBoundingClientRect = () => /** @type {DOMRect} */ ({ top: 0, bottom: 540, left: 0, right: 960, width: 960, height: 540 });
  controls.scrubber.getBoundingClientRect = () => /** @type {DOMRect} */ ({ top: 480, bottom: 498, left: 0, right: 960, width: 960, height: 18 });
  player.stage.scale = 0.5;
  controls.wake();
  return Number.parseFloat(player.el.style.getPropertyValue('--caption-lift') || '0');
}

test('the control bar lifts Dynamic captions only as far as it covers their band: not at all in 9:16', async () => {
  assert.equal(await liftWith(dynamicTimeline('portrait')), 0, 'the 9:16 band (y 1040-1240) stays out of the card column');
  assert.equal(await liftWith(dynamicTimeline('landscape')), 120 - 48 + 14);
  assert.equal(await liftWith(classicTimeline()), 120 - 40 + 14, 'classic: as before');
});

test('the caption size of a Dynamic video is its own: the button is disabled and says so, the saved size survives', async () => {
  const fake = fakeStage();
  const { player, root } = setup({ createDynamicStage: fake.create }, { captionSize: 'l' });
  await player.load(dynamicTimeline('portrait'));
  /** @type {any[]} */
  const prefs = [];
  const controls = new Controls(root, player, { onPrefs: (p) => prefs.push(p) });
  made.push(controls);
  assert.equal(player.captionSizeAdjustable, false);
  assert.equal(player.captionSize, 'l');
  const btn = controls.sizeBtn;
  assert.equal(btn.disabled, true);
  assert.equal(btn.getAttribute('aria-label'), CAPTION_SIZE_FIXED_LABEL);
  assert.equal(btn.title, CAPTION_SIZE_FIXED_LABEL);
  assert.equal(btn.textContent, 'Aa');
  controls.cycleCaptionSize();
  player.setCaptions(false);
  player.setCaptions(true);
  assert.equal(btn.disabled, true, 'turning captions back on does not enable it');
  assert.equal(prefs.at(-1).captionSize, 'l', "the viewer's size for classic lectures is not reset to M");
  assert.ok(prefs.every((p) => p.captionSize === 'l'));
  // a classic lecture in the same player: the size works again
  controls.destroy();
  made = made.filter((m) => m !== controls);
  await player.load(classicTimeline());
  const classic = new Controls(root, player, {});
  made.push(classic);
  assert.equal(player.captionSizeAdjustable, true);
  assert.equal(classic.sizeBtn.disabled, false);
  assert.match(classic.sizeBtn.getAttribute('aria-label') || '', /^Caption size: (Small|Medium|Large)$/);
});

// --- no classic work without an effect --------------------------------------------------------------------------------

test('a Dynamic lecture does not download the classic quiz tick and ding (its stage plays neither live)', async () => {
  const fake = fakeStage();
  const { player } = setup({ createDynamicStage: fake.create });
  const branded = (/** @type {any} */ t) => ({ ...t, branding: { tick_url: '/branding/tick.wav', ding_url: '/branding/ding.wav' } });
  await player.load(branded(dynamicTimeline()));
  assert.equal(player.sfx, null);
  assert.equal(document.querySelectorAll('audio[src*="tick"], audio[src*="ding"]').length, 0);
  await player.load(branded(classicTimeline()));
  assert.ok(player.sfx && player.sfx.sounds.size === 2, 'classic: as before');
});

/**
 * A Dynamic timeline whose scenes count the reads of `quiz` (only schedule.sceneStateAt reads it per frame here).
 * @param {any} timeline
 */
function counted(timeline) {
  const reads = { n: 0 };
  for (const s of timeline.scenes) {
    const value = s.quiz;
    Object.defineProperty(s, 'quiz', { configurable: true, enumerable: true, get: () => ((reads.n += 1), value) });
  }
  return { timeline, reads };
}

test('the classic scene state is computed only to duck the branding music, which still ducks while a beat speaks', async () => {
  const fake = fakeStage();
  const plain = counted(dynamicTimeline());
  const a = setup({ createDynamicStage: fake.create });
  await a.player.load(plain.timeline);
  a.player.play();
  for (let k = 0; k < 20; k++) a.step(100);
  assert.equal(plain.reads.n, 0, 'no music: no classic state per frame');
  assert.equal(a.player.state, null);

  const music = counted({ ...dynamicTimeline(), branding: { bgm_url: '/branding/bgm.mp3', bgm_volume: 0.2 } });
  const b = setup({ createDynamicStage: fake.create });
  await b.player.load(music.timeline);
  const bgm = /** @type {any} */ (b.player).bgm;
  assert.equal(bgm.hasTrack, true);
  b.player.seek(1); // scene 1, beat k1 speaks 0.5-2.0
  assert.equal(bgm.ducked, true);
  b.player.seek(2.1); // the pause after it
  assert.equal(bgm.ducked, false);
  b.player.seek(14 + 1); // the quiz's beat speaks 0.5-2.5
  assert.equal(bgm.ducked, true);
  assert.ok(music.reads.n > 0);
});

test('a stage failure is reported once per scene and message, not on every frame', async () => {
  const fake = fakeStage((t) => (t < 10 ? new Error('scene 0 broke') : t < 14 ? new Error('scene 1 broke') : null));
  const { player, events, step } = setup({ createDynamicStage: fake.create });
  await player.load(dynamicTimeline());
  player.play();
  for (let k = 0; k < 30; k++) step(100);
  assert.equal(events.error.length, 1);
  assert.equal(events.error[0].message, 'stage update failed');
  assert.match(String(events.error[0].error), /scene 0 broke/);
  assert.ok(events.timeupdate.length >= 30, 'the frames still run: narration and time go on');
  player.seek(11);
  for (let k = 0; k < 10; k++) step(100);
  assert.equal(events.error.length, 2, 'another scene, another report');
  player.seek(1);
  step(100);
  assert.equal(events.error.length, 2, 'scene 0 was reported already');
  await player.load(dynamicTimeline()); // a new timeline starts afresh
  assert.equal(events.error.length, 3);
});

test('a failure whose message changes every frame is capped', async () => {
  const fake = fakeStage((t) => new Error(`bad frame at ${t}`));
  const { player, events, step } = setup({ createDynamicStage: fake.create });
  await player.load(dynamicTimeline());
  player.play();
  for (let k = 0; k < MAX_STAGE_FAILURES * 3; k++) step(10);
  assert.equal(events.error.length, MAX_STAGE_FAILURES);
});

test('in a hidden tab the stage is painted only when the scene changes; time and scenes go on; visible paints again', async () => {
  const fake = fakeStage();
  const h = setup({ createDynamicStage: fake.create });
  await h.player.load(dynamicTimeline());
  h.player.play();
  const hide = (/** @type {boolean} */ hidden) => {
    if (hidden) Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    else delete (/** @type {any} */ (document)).visibilityState;
    document.dispatchEvent(new window.Event('visibilitychange'));
  };
  const wait = () => tick(BACKGROUND_TICK_MS + 120);
  try {
    hide(true);
    const painted = fake.rec.updates.length;
    const ticks = h.events.timeupdate.length;
    h.advance(3000);
    await wait();
    assert.ok(h.events.timeupdate.length > ticks, 'the hidden-tab timer still runs the lecture');
    assert.ok(Math.abs(h.events.timeupdate.at(-1).t - 3) < 1e-6);
    assert.equal(fake.rec.updates.length, painted, 'nothing painted for nobody');
    h.advance(9000); // t = 12: scene 1
    await wait();
    assert.equal(h.player.sceneIndex, 1);
    assert.equal(fake.rec.updates.length, painted + 1, 'the scene change is painted (slots and clips follow)');
    assert.ok(Math.abs(fake.rec.updates.at(-1).t - 12) < 1e-6);
    h.advance(500);
    await wait();
    assert.equal(fake.rec.updates.length, painted + 1);
    h.player.pause(); // e.g. the system media controls: the paused frame is painted
    assert.equal(fake.rec.updates.length, painted + 2);
    assert.equal(fake.rec.updates.at(-1).playing, false);
    h.player.play();
    hide(false);
    const before = fake.rec.updates.length;
    h.step(16);
    assert.equal(fake.rec.updates.length, before + 1, 'visible: the next animation frame paints');
  } finally {
    delete (/** @type {any} */ (document)).visibilityState;
  }
});
