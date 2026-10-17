// @ts-check
// web/js/dynamic/presenter.js (DYNAMIC_VIDEO_DESIGN §3.2-§3.3; B2 plan §1.2 ResolvedLook.presenter, §1.12 the crop):
// the presenter's normalised spec, its frame key (unchanged without a crop, the crop hashed in with one), the frame
// on screen at t, its paint (settle, slide, fade) as a pure function of t, the live video's box, the brand wipe's
// progress and the placements (tests/compose/dynamic/test_presenter.py pins these against presenter.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { mediaKey, presenterOf } from '../../js/dynamic/media.js';
import {
  CARD_RADIUS, CLIP_FPS, ENTER_KINDS, EXIT_KINDS, INTRO_LAYOUTS, LANDSCAPE_PLACEMENTS, REEL_CARD, REEL_CARD_XS, REEL_CROP,
  SETTLE_RISE, SETTLE_SECONDS, SLIDE_SECONDS, WALL_TITLE_BOX, WIPE_SECONDS, liveVideoBox, placement, presenterFrame,
  presenterKey, presenterPaint, presenterShown, presenterSpec, wallInks, wipeColour, wipeProgress,
} from '../../js/dynamic/presenter.js';

/** A scene whose look carries `presenter`. @param {any} presenter @param {number} [duration] @returns {any} */
const sceneWith = (presenter, duration = 5) => ({ scene_id: 's', index: 0, start: 0, duration, look: { presenter } });

const CARD = {
  clip_key: 'aadhi_left_clean.mp4', crop: [0, 0, 720, 720], rect: [96, 180, 720, 720], radius: 40, t_exit: 4.4,
  exit_kind: 'wipe', exit_s: 0.6, enter_kind: 'none', src_fps: 24, layout: 'card_left',
};
const REEL = {
  clip_key: 'aadhi_center_clean.mp4', crop: [280, 0, 720, 720], rect: [168, 800, 744, 744], radius: 40, t_exit: 4.7,
  exit_kind: 'slide_down', exit_s: 0.3, enter_kind: 'rise', src_fps: 24, layout: 'card',
};

test('presenterSpec: null without a valid presenter; the stored values normalised', () => {
  assert.equal(presenterSpec(/** @type {any} */ ({ duration: 5, look: null })), null);
  assert.equal(presenterSpec(sceneWith({ ...CARD, clip_key: '' })), null);
  assert.equal(presenterSpec(sceneWith({ ...CARD, rect: [0, 0, 0, 10] })), null);
  assert.equal(presenterSpec(sceneWith({ ...CARD, rect: [0, 0, 10] })), null);
  const s = /** @type {any} */ (presenterSpec(sceneWith(CARD)));
  assert.deepEqual(s.rect, { x: 96, y: 180, w: 720, h: 720 });
  assert.deepEqual(s.crop, [0, 0, 720, 720]);
  assert.equal(s.radius, 40);
  assert.equal(s.t_exit, 4.4);
  assert.equal(s.exit_kind, 'wipe');
  assert.equal(s.exit_s, 0.6);
  assert.equal(s.t_hide, 4.4 + 0.6);
  assert.deepEqual(s.size, [720, 720]);
  assert.equal(s.layout, 'card_left');
  // unknown kinds and a missing t_exit fall back to the stage's earlier rule: hidden at t_exit (or the scene's end)
  const odd = /** @type {any} */ (presenterSpec(sceneWith({ ...CARD, exit_kind: 'iris', enter_kind: 'zoom', t_exit: undefined }, 6)));
  assert.equal(odd.exit_kind, 'none');
  assert.equal(odd.exit_s, 0);
  assert.equal(odd.enter_kind, 'none');
  assert.equal(odd.t_exit, 6);
  assert.equal(odd.t_hide, 6);
  // a crop outside the stored form is dropped (the clip shown whole, as before the crop)
  assert.equal(/** @type {any} */ (presenterSpec(sceneWith({ ...CARD, crop: [-4, 0, 720, 720] }))).crop, null);
  assert.equal(/** @type {any} */ (presenterSpec(sceneWith({ ...CARD, radius: undefined }))).radius, null);
  assert.deepEqual([...EXIT_KINDS], ['wipe', 'slide_down', 'fade']);
  assert.deepEqual([...ENTER_KINDS], ['none', 'rise']);
});

test('presenterKey: without a crop it is media.js mediaKey exactly (no key the stage used before changes)', () => {
  const plain = { clip_key: 'aadhi_left_clean.mp4', rect: [10, 20, 641, 360], t_exit: 3 };
  const s = /** @type {any} */ (presenterSpec(sceneWith(plain)));
  assert.equal(s.crop, null);
  assert.equal(s.key, mediaKey('aadhi', 'aadhi_left_clean.mp4', [642, 360]));
  const before = /** @type {any} */ (presenterOf(/** @type {any} */ (sceneWith(plain))));
  assert.deepEqual(s.size, before.size);
  // with a crop the crop is hashed in, so two crops of one clip at one size never share frames
  const a = presenterKey('aadhi_left_clean.mp4', [0, 0, 720, 720], [480, 480]);
  const b = presenterKey('aadhi_left_clean.mp4', [0, 0, 576, 720], [480, 480]);
  assert.notEqual(a, b);
  assert.match(a, /^a-[0-9a-f]+-480x480$/);
  assert.notEqual(a, mediaKey('aadhi', 'aadhi_left_clean.mp4', [480, 480]));
  assert.match(a, /^[A-Za-z0-9_.:-]{1,200}$/, 'the worker accepts it (motion/media.py _KEY_RE)');
});

test('presenterFrame: floor(t x 24) from the scene start (the 1-1-1-2 cadence at 30 fps), never negative', () => {
  const s = /** @type {any} */ (presenterSpec(sceneWith(CARD)));
  assert.equal(presenterFrame(s, 0), 0);
  assert.equal(presenterFrame(s, -1), 0);
  const frames = [0, 1, 2, 3, 4].map((f) => presenterFrame(s, f / 30));
  assert.deepEqual(frames, [0, 0, 1, 2, 3]);
  assert.equal(presenterFrame(s, 7.99), 191, 'the last frame of an 8.000 s clip at the longest scene');
  assert.equal(s.src_fps, CLIP_FPS);
});

test('presenterPaint: the 9:16 card settles by a whole-pixel rise, then slides down at t_exit and is gone after', () => {
  const s = /** @type {any} */ (presenterSpec(sceneWith(REEL)));
  const p0 = presenterPaint(s, 0);
  assert.deepEqual(p0, { visible: true, dy: SETTLE_RISE, opacity: 1 }, 'frame 0: the card is whole (the reel cover), 16 px low');
  assert.equal(presenterPaint(s, SETTLE_SECONDS).dy, 0);
  const mid = presenterPaint(s, 0.2);
  assert.ok(mid.dy > 0 && mid.dy < SETTLE_RISE && Number.isInteger(mid.dy));
  assert.deepEqual(presenterPaint(s, 2), { visible: true, dy: 0, opacity: 1 });
  assert.deepEqual(presenterPaint(s, s.t_exit), { visible: true, dy: 0, opacity: 1 });
  const sliding = presenterPaint(s, s.t_exit + SLIDE_SECONDS / 2, { stageHeight: 1920 });
  assert.ok(sliding.visible && sliding.dy > 0 && Number.isInteger(sliding.dy) && sliding.opacity === 1);
  assert.equal(presenterPaint(s, s.t_exit + SLIDE_SECONDS).visible, false);
  assert.equal(presenterShown(s, s.t_exit + SLIDE_SECONDS - 0.001), true);
  // reduced motion (live only): no settle and no slide, still gone at the end of the exit
  assert.deepEqual(presenterPaint(s, 0, { reducedMotion: true }), { visible: true, dy: 0, opacity: 1 });
  assert.equal(presenterPaint(s, s.t_exit + 0.2, { reducedMotion: true }).dy, 0);
});

test('presenterPaint: the recap card fades out; under the wipe he stays unmoved until the block covers him', () => {
  const fade = /** @type {any} */ (presenterSpec(sceneWith({ ...CARD, exit_kind: 'fade', exit_s: 0.3, t_exit: 4.7 })));
  assert.equal(presenterPaint(fade, 4.7).opacity, 1);
  const o = presenterPaint(fade, 4.85).opacity;
  assert.ok(o > 0 && o < 1);
  assert.equal(presenterPaint(fade, 5).visible, false);
  const wipe = /** @type {any} */ (presenterSpec(sceneWith(CARD)));
  for (const t of [0, 4.4, 4.7, 4.99]) assert.deepEqual(presenterPaint(wipe, t), { visible: true, dy: 0, opacity: 1 }, `t ${t}`);
  assert.equal(presenterPaint(wipe, 5.0).visible, false);
  // the stage's earlier rule for a presenter without an exit: hidden from t_exit
  const bare = /** @type {any} */ (presenterSpec(sceneWith({ clip_key: 'x.mp4', rect: [0, 0, 100, 100], t_exit: 2 })));
  assert.equal(presenterPaint(bare, 1.99).visible, true);
  assert.equal(presenterPaint(bare, 2).visible, false);
});

test('liveVideoBox: the whole clip placed so its crop fills the box (null without a crop)', () => {
  const card = /** @type {any} */ (presenterSpec(sceneWith(CARD)));
  assert.deepEqual(liveVideoBox(card), { left: 0, top: 0, width: 1280, height: 720 });
  const reel = /** @type {any} */ (presenterSpec(sceneWith(REEL)));
  assert.deepEqual(liveVideoBox(reel), { left: -289, top: 0, width: 1323, height: 744 });
  const full = /** @type {any} */ (presenterSpec(sceneWith({ ...CARD, rect: [0, 0, 1920, 1080], crop: [0, 0, 1280, 720] })));
  assert.deepEqual(liveVideoBox(full), { left: 0, top: 0, width: 1920, height: 1080 });
  const split = /** @type {any} */ (presenterSpec(sceneWith({ ...CARD, rect: [0, 0, 864, 1080], crop: [0, 0, 576, 720] })));
  assert.deepEqual(liveVideoBox(split), { left: 0, top: 0, width: 1920, height: 1080 });
  assert.equal(liveVideoBox(/** @type {any} */ (presenterSpec(sceneWith({ clip_key: 'x', rect: [0, 0, 10, 10] })))), null);
});

test('placements: every crop has its box\'s aspect, lies inside the 1280x720 clip and the box inside the stage', () => {
  for (const layout of INTRO_LAYOUTS) {
    const p = placement('landscape', layout);
    const [, , cw, ch] = p.crop;
    const [x, y, w, hh] = p.rect;
    assert.ok(Math.abs(w * ch - hh * cw) <= Math.max(cw, ch), layout);
    assert.ok(p.crop[0] >= 0 && p.crop[0] + cw <= 1280 && p.crop[1] + ch <= 720, layout);
    assert.ok(x >= 0 && y >= 0 && x + w <= 1920 && y + hh <= 1080, layout);
  }
  assert.equal(LANDSCAPE_PLACEMENTS.card_left.radius, CARD_RADIUS);
  assert.equal(LANDSCAPE_PLACEMENTS.fullframe.radius, 0);
  for (const cx of REEL_CARD_XS) {
    const p = placement('portrait', 'card', cx);
    assert.deepEqual(p.rect, [cx, REEL_CARD.y, 744, 744]);
    assert.deepEqual(p.crop, [...REEL_CROP]);
    assert.ok(cx + 744 <= 1080 && REEL_CARD.y + 744 <= 1920);
  }
  assert.throws(() => placement('portrait', 'card', 500));
  assert.throws(() => placement('landscape', 'card'));
  assert.deepEqual(placement('portrait', 'card').rect[0], REEL_CARD_XS[0]);
});

test('wipeProgress: 0 before t_exit, ease.hero over 600 ms, 1 from its end; wipeColour and wallInks read the look', () => {
  assert.equal(wipeProgress(4.0, 4.4), 0);
  assert.equal(wipeProgress(4.4, 4.4), 0);
  const mid = wipeProgress(4.7, 4.4);
  assert.ok(mid > 0.5 && mid < 1, 'ease.hero decelerates: most of the sweep is done by the half');
  assert.equal(wipeProgress(4.4 + WIPE_SECONDS, 4.4), 1);
  assert.equal(wipeColour({ refs: { accent_next: '#123456' } }, '#000000'), '#123456');
  assert.equal(wipeColour({ refs: { accent_next: 'red' } }, '#000000'), '#000000');
  assert.equal(wipeColour(null, '#ABCDEF'), '#ABCDEF');
  assert.deepEqual(wallInks({ presenter: { wall: '#CCCBCE', wall_ink: '#141822', wall_ink2: '#484D58' } }),
    { ink: '#141822', ink2: '#484D58', bg: '#CCCBCE' });
  assert.equal(wallInks({ presenter: { wall_ink: '#141822' } }), null);
  assert.deepEqual({ ...WALL_TITLE_BOX }, { x0: 860, y0: 54, x1: 1824, y1: 855 });
});
