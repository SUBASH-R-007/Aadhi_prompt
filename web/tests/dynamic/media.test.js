import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from '../player/_dom.js';
import {
  FRAMES_PREFIX,
  MediaView,
  frameUrl,
  mediaKey,
  mediaPlan,
  mediaSize,
  presenterOf,
  sceneClips,
  shownIn,
  sourceFrame,
  sourceTime,
} from '../../js/dynamic/media.js';
import { Painter, ZONES } from '../../js/dynamic/core.js';
import { mediaBoxMax, mediaOf, variantOf } from '../../js/dynamic/templates/board_generic.js';
import { dynamicTimeline } from './_fixture.js';

before(() => installDom());
after(() => uninstallDom());

const boxFor = (/** @type {any} */ scene) => mediaBoxMax(scene, ZONES.landscape);

test('mediaSize contains the source in the box, never upscales, even sides', () => {
  assert.deepEqual(mediaSize({ width: 1280, height: 720 }, { w: 1920, h: 1080 }), [1280, 720]);
  assert.deepEqual(mediaSize({ width: 3840, height: 2160 }, { w: 1000, h: 1000 }), [1000, 562]);
  assert.deepEqual(mediaSize({ width: 1281, height: 721 }, { w: 1920, h: 1080 }), [1282, 722]);
  assert.deepEqual(mediaSize({}, { w: 800, h: 450 }), [800, 450]);
});

test('frame keys are path-safe, stable and carry the size; URLs are zero-padded', () => {
  const k = mediaKey('video', 'assets/manim/abc 1', [640, 360]);
  assert.match(k, /^v-[0-9a-f]{16}-640x360$/);
  assert.equal(k, mediaKey('video', 'assets/manim/abc 1', [640, 360]));
  assert.notEqual(k, mediaKey('video', 'assets/manim/abc 2', [640, 360]));
  assert.match(mediaKey('aadhi', 'aadhi_left.mp4', [720, 720]), /^a-/);
  assert.equal(frameUrl(k, 12), `${FRAMES_PREFIX}${k}/000012.jpg`);
});

test('source frames: floor((t + trim) * fps + 1e-6), looped, frozen or clamped', () => {
  const loop = { duration: 2, end_behavior: 'loop' };
  assert.equal(sourceFrame(/** @type {any} */ (loop), 0, 30), 0);
  assert.equal(sourceFrame(/** @type {any} */ (loop), 1 / 30, 30), 1, '1e-6 keeps exact frame times on their frame');
  assert.equal(sourceFrame(/** @type {any} */ (loop), 2.5, 30), 15);
  assert.equal(sourceFrame(/** @type {any} */ ({ duration: 2, end_behavior: 'freeze' }), 5, 30), 59);
  assert.equal(sourceFrame(/** @type {any} */ ({ duration: 2, end_behavior: 'clamp' }), 5, 30), 59);
  assert.equal(sourceFrame(/** @type {any} */ ({ duration: 2, end_behavior: 'freeze', trim_in: 1 }), 0.5, 24), 36);
  assert.equal(sourceFrame(/** @type {any} */ ({}), 10, 24), 240, 'no duration: no wrap');
  assert.ok(Math.abs(sourceTime(/** @type {any} */ (loop), 2.5) - 0.5) < 1e-9);
});

test('shownIn: the render page shows clips as extracted frames, never a GIF or media kept out of the MP4', () => {
  const clip = { kind: 'video', asset_key: 'manim-1', url: '/m.mp4' };
  assert.equal(shownIn(/** @type {any} */ (clip), 'render'), true);
  assert.equal(shownIn(/** @type {any} */ ({ ...clip, asset_key: null }), 'render'), false, 'no frames without an asset');
  assert.equal(shownIn(/** @type {any} */ ({ ...clip, asset_key: null }), 'live'), true, 'live plays the URL');
  const gif = { kind: 'gif', url: 'https://media.giphy.com/x.gif', render_in_mp4: false };
  assert.equal(shownIn(/** @type {any} */ (gif), 'render'), false);
  assert.equal(shownIn(/** @type {any} */ (gif), 'live'), true);
  assert.equal(shownIn(/** @type {any} */ ({ kind: 'image', url: '/a.png', render_in_mp4: false }), 'render'), false);
  assert.equal(shownIn(/** @type {any} */ ({ kind: 'image', url: '' }), 'live'), false);
  assert.equal(shownIn(null, 'live'), false);
});

test('a scene whose panel the render cannot show is laid out without a media frame (no empty box in the MP4)', () => {
  const tl = dynamicTimeline();
  const gifScene = /** @type {any} */ ({
    ...tl.scenes[4],
    side_panel: { ...tl.scenes[4].side_panel, panel: { kind: 'gif', title: 'You did it!' }, media: { kind: 'gif', url: 'https://media.giphy.com/x.gif', render_in_mp4: false } },
  });
  assert.equal(variantOf(gifScene, 'live'), 'split', 'the live player shows the GIF');
  assert.equal(variantOf(gifScene, 'render'), 'stack');
  assert.equal(mediaOf(gifScene, 'render'), null);
  assert.deepEqual(variantOf(tl.scenes[4], 'render'), variantOf(tl.scenes[4], 'live'), 'a picture keeps its layout in both');
});

test('sceneClips lists the MP4 videos of a scene (main media and a video panel)', () => {
  const tl = dynamicTimeline();
  assert.deepEqual(sceneClips(tl.scenes[3]).map((c) => c.slot), ['main']);
  assert.deepEqual(sceneClips(tl.scenes[4]), [], 'an image panel needs no frames');
  const gif = { ...tl.scenes[3], media: { kind: 'video', asset_key: null, url: 'https://x/y.mp4', render_in_mp4: false } };
  assert.deepEqual(sceneClips(/** @type {any} */ (gif)), []);
});

test('mediaPlan lists the frames a chunk needs, with a size that never depends on the chunk', () => {
  const tl = dynamicTimeline();
  const whole = mediaPlan(tl, 0, 1200, boxFor);
  assert.equal(whole.length, 1);
  const e = whole[0];
  assert.equal(e.kind, 'video');
  assert.equal(e.srcFps, 30);
  assert.equal(e.asset_key, 'manim-sim');
  assert.equal(e.frames.length, 120, 'freeze: the 4 s clip shows 120 distinct frames over a 6 s scene');
  assert.deepEqual(e.frames.slice(0, 3), [0, 1, 2]);
  assert.equal(e.frames.at(-1), 119);
  const a = mediaPlan(tl, 780, 870, boxFor);
  const b = mediaPlan(tl, 870, 960, boxFor);
  assert.equal(a[0].key, b[0].key, 'same key (and size) in every chunk');
  assert.deepEqual([...a[0].frames, ...b[0].frames], e.frames);
  assert.deepEqual(mediaPlan(tl, 0, 300, boxFor), [], 'no clip in the first scene');
  assert.ok(e.size[0] <= 1280 && e.size[1] <= 720);
});

test('mediaPlan adds the outgoing scene final frame during a crossfade into the next scene', () => {
  const tl = dynamicTimeline();
  tl.scenes[4].look.transition_in = { kind: 'crossfade', seconds: 0.45 };
  const plan = mediaPlan(tl, 960, 965, boxFor);
  assert.equal(plan.length, 1);
  assert.deepEqual(plan[0].frames, [119], 'the clip of scene 3 at its end (frozen on its last frame)');
});

/** The fixture with scene 4's picture panel swapped for a video panel shown from a later beat (show_at 3.65 s). */
function panelClipTimeline(/** @type {Record<string, any>} */ extra = {}) {
  const tl = dynamicTimeline();
  const scene = tl.scenes[4];
  scene.side_panel = {
    panel: { kind: 'manim', title: 'Current' },
    media: { kind: 'video', asset_key: 'clip-1', url: '/media/clip.mp4', duration: 4, width: 1280, height: 720, end_behavior: 'freeze', ...extra },
    show_at: 3.65,
  };
  return tl;
}

test('mediaPlan lists the clamped first frame of a video panel before show_at (the page paints it while hidden)', () => {
  const tl = panelClipTimeline();
  const f0 = Math.round(tl.scenes[4].start * 30);
  const before = mediaPlan(tl, f0, f0 + 60, boxFor);
  assert.equal(before.length, 1, 'a chunk wholly before show_at still serves the hidden panel its frame');
  assert.deepEqual(before[0].frames, [0]);
  const fast = mediaPlan(panelClipTimeline({ src_fps: 60 }), f0, f0 + 240, boxFor);
  assert.equal(fast[0].srcFps, 60);
  assert.equal(fast[0].frames[0], 0, 'a source faster than the timeline: the first planned frame is still 0');
  const trimmed = mediaPlan(panelClipTimeline({ trim_in: 0.51 }), f0, f0 + 240, boxFor);
  assert.equal(trimmed[0].frames[0], Math.floor(0.51 * 30 + 1e-6), 'a trim off the frame grid: the trimmed first frame');
});

test('every clip frame the render page paints is in the media plan of its chunk, whatever the chunk split', async () => {
  const tl = panelClipTimeline();
  const { createDynamicStage } = await import('../../js/dynamic/stage.js');
  const { LOOKAHEAD } = await import('../../js/dynamic/media.js');
  const f0 = Math.round(tl.scenes[4].start * 30);
  const f1 = Math.round((tl.scenes[4].start + tl.scenes[4].duration) * 30);
  for (const sizes of [[240], [37, 61, 13, 90, 39], [109, 2, 129], [1, 140, 99]]) {
    const root = document.createElement('div');
    document.body.appendChild(root);
    const stage = createDynamicStage(root, tl, { mode: 'render', texIdle: () => Promise.resolve(), frame: () => Promise.resolve() });
    let a = f0;
    for (const size of sizes) {
      const b = Math.min(f1, a + size);
      const plan = new Set(stage.mediaPlan(a, b + LOOKAHEAD).flatMap((e) => e.frames.map((n) => frameUrl(e.key, n))));
      for (let f = a; f < b; f++) {
        await stage.seek(f);
        for (const img of stage.el.querySelectorAll('img')) {
          const src = img.getAttribute('src') || '';
          if (src.startsWith(FRAMES_PREFIX)) assert.ok(plan.has(src), `chunk ${a}-${b} frame ${f}: ${src} is not in the plan`);
        }
      }
      a = b;
    }
    stage.destroy();
    root.remove();
  }
});

test('presenterOf normalises ResolvedLook.presenter and mediaPlan lists its frames', () => {
  const tl = dynamicTimeline();
  tl.scenes[0].look.presenter = { clip_key: 'aadhi_left_clean.mp4', rect: [0, 0, 720, 720], t_exit: 2, src_fps: 24 };
  const p = presenterOf(tl.scenes[0]);
  assert.deepEqual(p.size, [720, 720]);
  const plan = mediaPlan(tl, 0, 90, boxFor).filter((x) => x.kind === 'aadhi');
  assert.equal(plan.length, 1);
  assert.equal(plan[0].srcFps, 24);
  assert.equal(plan[0].frames.length, 48, 'two seconds at 24 fps, then the presenter has left');
  assert.equal(presenterOf(/** @type {any} */ ({ look: { presenter: { clip_key: 'x', rect: [0, 0, 0, 10] } } })), null);
});

test('MediaView: render mode points an <img> at the extracted frame; never a <video>', () => {
  const tl = dynamicTimeline();
  const ref = tl.scenes[3].media;
  const view = new MediaView(ref, { mode: 'render', box: { w: 1280, h: 720 }, sceneDuration: 6, fps: 30 });
  assert.equal(view.el.querySelector('video'), null);
  const p = new Painter();
  p.begin();
  view.update(p, 1);
  assert.equal(view.img.getAttribute('src'), frameUrl(view.key, 30));
  assert.equal(view.prefetch.length, 10, 'a lookahead of 10 frames');
  view.update(p, 10);
  assert.equal(view.img.getAttribute('src'), frameUrl(view.key, 119), 'freeze holds the last frame');
  view.destroy();
});

test('MediaView: live mode plays a muted <video> kept within 0.1 s of the stage', async () => {
  const ref = dynamicTimeline().scenes[3].media;
  const view = new MediaView(ref, { mode: 'live', box: { w: 1280, h: 720 }, sceneDuration: 6, fps: 30 });
  const v = view.video;
  assert.ok(v && v.muted);
  const p = new Painter();
  view.update(p, 1, { playing: true, rate: 1, seeked: true });
  assert.ok(Math.abs(v.currentTime - 1) < 1e-9);
  assert.equal(v.paused, false);
  v.currentTime = 1.05;
  view.update(p, 1.1, { playing: true, rate: 1 });
  assert.ok(Math.abs(v.currentTime - 1.05) < 1e-9, 'within the drift tolerance: left alone');
  view.update(p, 1.5, { playing: true, rate: 1 });
  assert.ok(Math.abs(v.currentTime - 1.5) < 1e-9, 're-sought when drifting further');
  view.update(p, 1.5, { playing: false });
  assert.equal(v.paused, true);
  view.destroy();
});

test('MediaView: GIFs are live only; pictures keep their Ken Burns transform', () => {
  const gif = new MediaView(/** @type {any} */ ({ kind: 'gif', url: 'https://media.giphy.com/x.gif', render_in_mp4: false }), { mode: 'render', box: { w: 100, h: 100 }, sceneDuration: 5, fps: 30 });
  assert.ok(gif.missing);
  const pic = new MediaView(/** @type {any} */ ({ kind: 'image', url: '/media/a.png', ken_burns: { start: { scale: 1 }, end: { scale: 1.2 } } }), { mode: 'render', box: { w: 100, h: 100 }, sceneDuration: 10, fps: 30 });
  const p = new Painter();
  pic.update(p, 5);
  assert.match(pic.img.style.transform, /scale\(1\.1/);
  const calm = new MediaView(/** @type {any} */ ({ kind: 'image', url: '/media/a.png', ken_burns: { end: { scale: 1.2 } } }), { mode: 'live', box: { w: 100, h: 100 }, sceneDuration: 10, fps: 30, reducedMotion: true });
  calm.update(p, 5);
  assert.equal(calm.img.style.transform, '', 'reduced motion switches Ken Burns off (live only)');
});
