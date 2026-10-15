import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from '../player/_dom.js';
import {
  BACKDROP_BLUR,
  BACKDROP_BRIGHTNESS,
  BACKDROP_WIDTH,
  FRAMES_PREFIX,
  MediaView,
  backdropShown,
  coverCrop,
  frameUrl,
  mediaFit,
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

// --- fit: "contain_blur" ------------------------------------------------------------------------------------------

/**
 * A recording 2D context installed for canvases (jsdom has none): every drawImage with the state it was drawn in.
 * @param {import('node:test').TestContext} t
 */
function fakeCanvas(t) {
  const win = /** @type {any} */ (window);
  const proto = win.HTMLCanvasElement.prototype;
  const original = proto.getContext;
  const hadCtor = 'CanvasRenderingContext2D' in win;
  /** @type {any[]} */
  const draws = [];
  /** @type {WeakMap<object, any>} */
  const contexts = new WeakMap();
  win.CanvasRenderingContext2D = class {};
  proto.getContext = function getContext() {
    let g = contexts.get(this);
    if (!g) {
      const canvas = this;
      /** @type {any[]} */
      const stack = [];
      g = {
        filter: 'none',
        imageSmoothingEnabled: true,
        imageSmoothingQuality: 'low',
        save() {
          stack.push({ filter: g.filter, imageSmoothingEnabled: g.imageSmoothingEnabled, imageSmoothingQuality: g.imageSmoothingQuality });
        },
        restore() {
          Object.assign(g, stack.pop() || {});
        },
        clearRect() {},
        drawImage(/** @type {any[]} */ ...args) {
          draws.push({
            canvas, source: args[0], args: args.slice(1), filter: g.filter, smoothing: g.imageSmoothingEnabled,
            quality: g.imageSmoothingQuality, size: [canvas.width, canvas.height],
          });
        },
      };
      contexts.set(this, g);
    }
    return g;
  };
  t.after(() => {
    proto.getContext = original;
    if (!hadCtor) delete win.CanvasRenderingContext2D;
  });
  return draws;
}

/** Make an <img> look loaded (jsdom loads no pictures): complete, with its natural size. @param {HTMLImageElement} img */
function loaded(img, w = 1280, h = 720) {
  Object.defineProperty(img, 'complete', { configurable: true, get: () => true });
  Object.defineProperty(img, 'naturalWidth', { configurable: true, value: w });
  Object.defineProperty(img, 'naturalHeight', { configurable: true, value: h });
}

test('mediaFit and coverCrop', () => {
  assert.equal(mediaFit(/** @type {any} */ ({ fit: 'contain_blur' })), 'contain_blur');
  assert.equal(mediaFit(/** @type {any} */ ({ fit: 'cover' })), 'cover');
  assert.equal(mediaFit(/** @type {any} */ ({ fit: 'contain' })), 'contain');
  assert.equal(mediaFit(/** @type {any} */ ({})), 'contain');
  assert.equal(mediaFit(/** @type {any} */ ({ fit: 'stretch' })), 'contain');
  // a 16:9 picture covering a 9:16 box: its full height, the middle of its width
  const c = coverCrop(1280, 720, 64, 114);
  assert.ok(Math.abs(c.h - 720) < 1e-9 && Math.abs(c.w - (720 * 64) / 114) < 1e-9);
  assert.ok(Math.abs(c.x - (1280 - c.w) / 2) < 1e-9 && c.y === 0);
  assert.deepEqual(coverCrop(800, 600, 800, 600), { x: 0, y: 0, w: 800, h: 600 });
  const wide = coverCrop(600, 800, 64, 36); // a portrait picture behind a 16:9 box: its middle band
  assert.ok(Math.abs(wide.w - 600) < 1e-9 && Math.abs(wide.h / wide.w - 36 / 64) < 1e-9 && wide.x === 0);
});

test('contain_blur: the clip contained over a canvas backdrop; contain and cover have none', () => {
  const ref = { ...dynamicTimeline().scenes[3].media, fit: 'contain_blur' };
  for (const mode of /** @type {const} */ (['render', 'live'])) {
    const view = new MediaView(/** @type {any} */ (ref), { mode, box: { w: 1080, h: 1920 }, sceneDuration: 6, fps: 30 });
    assert.ok(view.el.classList.contains('dyn-media--contain_blur'));
    const [backdrop, el] = [...view.el.children];
    assert.equal(backdrop.tagName, 'CANVAS');
    assert.ok(backdrop.classList.contains('dyn-media-backdrop') && backdrop.getAttribute('aria-hidden') === 'true');
    assert.equal(el, mode === 'render' ? view.img : view.video, 'the medium above it');
    assert.equal(view.el.querySelectorAll('img, video').length, 1, 'one medium: the backdrop draws the same element');
    assert.ok(view.el.classList.contains('dyn-media--clip'), 'a clip: dynamic.css gives its box a layer of its own');
    view.destroy();
  }
  const picture = new MediaView(/** @type {any} */ ({ kind: 'image', url: '/media/a.png', fit: 'contain_blur' }), { mode: 'render', box: { w: 1080, h: 1920 }, sceneDuration: 6, fps: 30 });
  assert.ok(picture.backdrop && !picture.el.classList.contains('dyn-media--clip'), 'a picture keeps no layer (Ken Burns scales it)');
  for (const fit of ['contain', 'cover', undefined]) {
    const view = new MediaView(/** @type {any} */ ({ ...ref, fit }), { mode: 'render', box: { w: 1080, h: 1920 }, sceneDuration: 6, fps: 30 });
    assert.equal(view.el.querySelector('canvas'), null, String(fit));
  }
  const missing = new MediaView(/** @type {any} */ ({ ...ref, asset_key: null }), { mode: 'render', box: { w: 100, h: 100 }, sceneDuration: 6, fps: 30 });
  assert.ok(missing.missing && missing.el.querySelector('canvas') === null, 'nothing to blur');
});

test('contain_blur needs no other frames: the same frame keys, sizes and frames as contain', () => {
  const tl = dynamicTimeline();
  const blur = dynamicTimeline();
  blur.scenes[3].media.fit = 'contain_blur';
  for (const [f0, f1] of [[0, 1200], [780, 870], [870, 960]]) {
    assert.deepEqual(mediaPlan(blur, f0, f1, boxFor), mediaPlan(tl, f0, f1, boxFor), `frames ${f0}-${f1}`);
  }
  const portrait = (/** @type {any} */ scene) => mediaBoxMax(scene, ZONES.portrait);
  assert.deepEqual(mediaPlan(blur, 0, 1200, portrait), mediaPlan(tl, 0, 1200, portrait));
});

test('contain_blur (render): the backdrop is the decoded frame itself, cover-fit, blurred and darkened in a small canvas', async (t) => {
  const draws = fakeCanvas(t);
  const ref = { ...dynamicTimeline().scenes[3].media, fit: 'contain_blur' };
  const view = new MediaView(/** @type {any} */ (ref), { mode: 'render', box: { w: 1080, h: 1920 }, sceneDuration: 6, fps: 30 });
  const img = /** @type {HTMLImageElement} */ (view.img);
  const p = new Painter();
  p.begin();
  view.update(p, 1, { noFrames: true });
  assert.equal(draws.length, 0, 'the boot paint shows no frame: nothing to blur');
  view.update(p, 1);
  assert.equal(img.getAttribute('src'), frameUrl(String(view.key), 30));
  assert.equal(draws.length, 0, 'not before the frame is in (the <img> still shows the previous one meanwhile)');
  loaded(img);
  await view.ready();
  assert.equal(draws.length, 1, 'drawn once the frame is decoded, before the capture');
  const d = draws[0];
  assert.equal(d.source, img, 'from the <img> of the planned frame: no other extraction');
  const ch = Math.round((BACKDROP_WIDTH * 1920) / 1080);
  assert.deepEqual(d.size, [BACKDROP_WIDTH, ch], "a small canvas at the box's aspect");
  const crop = coverCrop(1280, 720, BACKDROP_WIDTH, ch);
  const pad = 3 * BACKDROP_BLUR;
  assert.deepEqual(d.args, [crop.x, crop.y, crop.w, crop.h, -pad, -pad, BACKDROP_WIDTH + 2 * pad, ch + 2 * pad]);
  assert.equal(d.filter, `blur(${BACKDROP_BLUR}px) brightness(${BACKDROP_BRIGHTNESS})`);
  assert.equal(d.quality, 'high');
  assert.ok(d.smoothing);
  // the same frame again (another seek of it, a repaint): the canvas already holds it
  view.update(p, 1);
  await view.ready();
  assert.equal(draws.length, 1);
  // the next frame, not yet in: Chromium then reports complete = false but still the previous picture's natural size
  // (measured: naturalWidth is that of the old picture right after src changes), so the size alone would draw the
  // previous frame under the new frame's key, and ready() would never draw again (the order of renders would show)
  Object.defineProperty(img, 'complete', { configurable: true, get: () => false });
  view.update(p, 2);
  assert.equal(img.getAttribute('src'), frameUrl(String(view.key), 60));
  assert.equal(img.naturalWidth, 1280, 'the previous picture is still reported');
  assert.equal(draws.length, 1, 'not drawn from the previous picture while the new frame loads');
  // drawn from it once it is in, exactly once
  loaded(img);
  await view.ready();
  assert.equal(draws.length, 2);
  assert.equal(draws[1].source, img);
  await view.ready();
  view.update(p, 2);
  assert.equal(draws.length, 2, 'that frame is in the canvas already');
  view.destroy();
});

test('contain_blur: a medium that covers its box has no backdrop (not drawn, its canvas not shown)', async (t) => {
  // board_generic sizes a medium's frame to the medium's aspect when its size is known: nothing of a backdrop would
  // show, so it costs no drawing and no composited canvas layer
  assert.equal(backdropShown(1280, 720, 1280, 720), false);
  assert.equal(backdropShown(640, 480, 722, 541), false, 'less than a pixel at each side');
  assert.equal(backdropShown(1280, 720, 1284, 720), true, 'two pixels: shown');
  assert.equal(backdropShown(1280, 720, 1080, 1920), true);
  assert.equal(backdropShown(1600, 1200, 1600, 900), true, 'a 4:3 picture in a 16:9 box: bands at its sides');
  const draws = fakeCanvas(t);
  const ref = { ...dynamicTimeline().scenes[3].media, fit: 'contain_blur' }; // a 1280 x 720 clip
  const view = new MediaView(/** @type {any} */ (ref), { mode: 'render', box: { w: 1280, h: 720 }, sceneDuration: 6, fps: 30 });
  const img = /** @type {HTMLImageElement} */ (view.img);
  const canvas = /** @type {HTMLCanvasElement} */ (view.backdrop);
  const p = new Painter();
  p.begin();
  view.update(p, 1);
  loaded(img);
  await view.ready();
  assert.equal(draws.length, 0, 'covered: not drawn');
  assert.equal(canvas.style.display, 'none', 'and not shown');
  // laid out anew in a box of another aspect: the backdrop shows, drawn from the frame already in
  Object.defineProperty(view.el, 'clientWidth', { configurable: true, get: () => 1080 });
  Object.defineProperty(view.el, 'clientHeight', { configurable: true, get: () => 1920 });
  view.update(p, 1);
  assert.equal(canvas.style.display, '');
  assert.equal(draws.length, 1);
  assert.deepEqual(draws[0].size, [BACKDROP_WIDTH, Math.round((BACKDROP_WIDTH * 1920) / 1080)]);
  view.destroy();
});

test('contain_blur (live): the backdrop follows the playing clip, never a frame from before a seek', (t) => {
  const draws = fakeCanvas(t);
  const ref = { ...dynamicTimeline().scenes[3].media, fit: 'contain_blur' };
  const view = new MediaView(/** @type {any} */ (ref), { mode: 'live', box: { w: 1600, h: 1200 }, sceneDuration: 6, fps: 30 });
  const v = /** @type {HTMLVideoElement} */ (view.video);
  Object.defineProperty(v, 'videoWidth', { configurable: true, value: 1280 });
  Object.defineProperty(v, 'videoHeight', { configurable: true, value: 720 });
  const p = new Painter();
  view.update(p, 1, { playing: true, rate: 1, seeked: true });
  assert.equal(draws.length, 1);
  assert.equal(draws[0].source, v);
  assert.deepEqual(draws[0].size, [BACKDROP_WIDTH, 48], 'a 16:9 clip in a 4:3 box: the backdrop above and below it');
  view.update(p, 1, { playing: true, rate: 1 });
  assert.equal(draws.length, 1, 'the same clip time: nothing new to draw');
  v.currentTime = 1.04; // played on
  view.update(p, 1.04, { playing: true, rate: 1 });
  assert.equal(draws.length, 2);
  // a seek: the element takes the new time at once, its frame comes later (seeking until then)
  let seeking = true;
  Object.defineProperty(v, 'seeking', { configurable: true, get: () => seeking });
  v.currentTime = 3;
  view.update(p, 3, { playing: true, rate: 1, seeked: true });
  assert.equal(draws.length, 2, 'while seeking the element still shows the old frame: not drawn');
  seeking = false;
  view.update(p, 3, { playing: true, rate: 1 });
  assert.equal(draws.length, 3, 'the frame at the new time is in: drawn');
  view.destroy();
});

/**
 * A live clip that behaves like Chromium's <video> (measured with Playwright): no seek before its metadata; setting
 * currentTime starts a seek (`seeking` until the test ends it), and a seek past the clip's real end is clamped there.
 * @param {HTMLVideoElement} v
 * @param {{readyState?: number, end?: number}} [opts]
 */
function browserClip(v, opts = {}) {
  const clip = { readyState: opts.readyState ?? 0, seeking: false, time: 0, end: opts.end ?? Infinity, sought: /** @type {number[]} */ ([]) };
  Object.defineProperty(v, 'readyState', { configurable: true, get: () => clip.readyState });
  Object.defineProperty(v, 'seeking', { configurable: true, get: () => clip.seeking });
  Object.defineProperty(v, 'currentTime', {
    configurable: true,
    get: () => clip.time,
    set: (/** @type {number} */ x) => {
      clip.sought.push(Number(x));
      clip.time = Math.min(Number(x), clip.end);
      clip.seeking = true;
    },
  });
  Object.defineProperty(v, 'videoWidth', { configurable: true, value: 1280 });
  Object.defineProperty(v, 'videoHeight', { configurable: true, value: 720 });
  /** the seek in flight ends: its frame is in (readyState 4 at 'seeked', as Chromium reports it) */
  const seeked = () => {
    clip.seeking = false;
    clip.readyState = 4;
    v.dispatchEvent(new window.Event('seeked'));
  };
  return { clip, seeked };
}

test('contain_blur (live, paused): the backdrop is drawn when the clip frame arrives, with no repaint', (t) => {
  // the paused live player paints once per seek or relayout: a frame that arrives after that paint is drawn by the
  // clip's own events ('loadeddata', 'seeked'), else the backdrop kept the frame before the seek (or nothing) until play
  const draws = fakeCanvas(t);
  const ref = { ...dynamicTimeline().scenes[3].media, fit: 'contain_blur' };
  const view = new MediaView(/** @type {any} */ (ref), { mode: 'live', box: { w: 1600, h: 1200 }, sceneDuration: 6, fps: 30 });
  const v = /** @type {HTMLVideoElement} */ (view.video);
  const { clip, seeked } = browserClip(v, { readyState: 1 });
  const p = new Painter();
  // the first paint of a paused scene at its start: the clip has its metadata, not yet a frame
  view.update(p, 0, { playing: false });
  assert.deepEqual(clip.sought, [], 'already at its start');
  assert.equal(draws.length, 0, 'no frame to blur yet');
  clip.readyState = 2;
  v.dispatchEvent(new window.Event('loadeddata'));
  assert.equal(draws.length, 1, 'its first frame is in: drawn without another paint');
  assert.equal(draws[0].source, v);
  // a paused seek: the one paint finds the clip seeking (it still shows the old frame then)
  view.update(p, 3, { playing: false, seeked: true });
  assert.deepEqual(clip.sought, [3]);
  assert.equal(draws.length, 1, 'not drawn while seeking');
  seeked();
  assert.equal(draws.length, 2, 'the frame at the new time is in: drawn once, with no further paint');
  assert.equal(draws[1].source, v);
  v.dispatchEvent(new window.Event('seeked'));
  assert.equal(draws.length, 2, 'the same frame again: the canvas already holds it');
  // a destroyed view no longer listens
  view.destroy();
  clip.time = 1;
  v.dispatchEvent(new window.Event('seeked'));
  v.dispatchEvent(new window.Event('loadeddata'));
  assert.equal(draws.length, 2);
});

test('MediaView (live, paused): the clip takes the time the stage asked for last as soon as it can seek', () => {
  const ref = dynamicTimeline().scenes[3].media; // a 4 s clip, frozen on its end
  const view = new MediaView(ref, { mode: 'live', box: { w: 1280, h: 720 }, sceneDuration: 6, fps: 30 });
  const v = /** @type {HTMLVideoElement} */ (view.video);
  const { clip, seeked } = browserClip(v, { readyState: 0, end: 3.5 });
  const p = new Painter();
  // a paused seek into the scene paints once, before the clip has its metadata: it cannot seek then
  view.update(p, 2, { playing: false, seeked: true });
  assert.deepEqual(clip.sought, [], 'no metadata yet');
  clip.readyState = 1;
  v.dispatchEvent(new window.Event('loadedmetadata'));
  assert.deepEqual(clip.sought, [sourceTime(ref, 2)], 'sought to the time asked for once it can, with no repaint');
  // a scrub while that seek is in flight: one paint per position, none of which can seek now ...
  view.update(p, 2.5, { playing: false, seeked: true });
  view.update(p, 3, { playing: false, seeked: true });
  assert.equal(clip.sought.length, 1);
  seeked();
  assert.deepEqual(clip.sought.slice(1), [3], '... the last position is applied when the seek ends');
  seeked();
  assert.equal(clip.sought.length, 2, 'and that seek done, nothing more');
  // a clip shorter than its declared duration: the browser clamps the seek to its end; that time is never chased
  view.update(p, 5, { playing: false, seeked: true });
  assert.equal(clip.sought.at(-1), sourceTime(ref, 5));
  assert.equal(clip.time, 3.5, 'clamped');
  seeked();
  seeked();
  assert.equal(clip.sought.length, 3, 'no seek loop');
  // playing: every animation frame syncs the clip, so its events re-apply nothing
  view.update(p, 1, { playing: true, rate: 1, seeked: true });
  assert.equal(clip.sought.length, 4);
  view.update(p, 1.5, { playing: true, rate: 1 });
  seeked();
  assert.equal(clip.sought.length, 4);
  view.destroy();
});

test('a picture shown contain_blur on the stage: its backdrop is in the media frame, the plan unchanged', async () => {
  const { createDynamicStage } = await import('../../js/dynamic/stage.js');
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const tl = dynamicTimeline(format);
    tl.scenes[4].side_panel.media = { ...tl.scenes[4].side_panel.media, fit: 'contain_blur', width: null, height: null };
    const root = document.createElement('div');
    document.body.appendChild(root);
    const stage = createDynamicStage(root, tl, { mode: 'render', texIdle: () => Promise.resolve(), frame: () => Promise.resolve() });
    await stage.seek(Math.round((32 + 3) * 30));
    const media = stage.el.querySelector('.dyn-scene[data-scene="4"] .dyn-frame .dyn-media--contain_blur');
    assert.ok(media, `${format}: the picture is shown contain_blur`);
    assert.equal(media.firstElementChild?.tagName, 'CANVAS');
    assert.match(String(media.querySelector('img')?.getAttribute('src')), /\/media\/circuit\.png$/, 'the picture itself is the medium');
    assert.deepEqual(stage.mediaPlan(0, 1200), mediaPlan(dynamicTimeline(format), 0, 1200, (/** @type {any} */ s) => mediaBoxMax(s, ZONES[format])));
    stage.destroy();
    root.remove();
  }
});
