// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { computeZones, positionKey, intersects, fitAspect, mediaAspect, STAGE_WIDTH, STAGE_HEIGHT } from '../../js/player/layout.js';
import { computeFit } from '../../js/player/stage.js';
import { kenBurnsAt, kenBurnsTransform } from '../../js/player/kenburns.js';
import { mediaTimeAt } from '../../js/player/scenes/media.js';
import { clamp, formatTime, formatDurationLong, formatRate, fnv1a, lerp, num, optionLetter, roundTo, truncate } from '../../js/shared/format.js';

/**
 * Worst-case mascot extents measured from video_template/*.mp4 (saturated pixels over every frame
 * sampled at 4 fps, incl. gestures), as fractions of the stage width. The popup close-up (head and
 * shoulders 0.284-0.75, face 0.42-0.62) is the backdrop of the content-first popup layout, so it is
 * not a keep-clear region (see the popup test below).
 */
const MASCOT = {
  left: { x0: 0.044, x1: 0.369 },
  right: { x0: 0.672, x1: 0.981 },
  center: { x0: 0.369, x1: 0.656 },
};
const POPUP_FACE = { x0: 0.42, x1: 0.62 };

/** @param {{x: number, y: number, w: number, h: number}} r */
const inStage = (r) => r.x >= 0 && r.y >= 0 && r.x + r.w <= STAGE_WIDTH && r.y + r.h <= STAGE_HEIGHT && r.w > 0 && r.h > 0;

describe('layout zones', () => {
  const positions = ['left', 'right', 'center', 'popup_bottom_left', 'popup_bottom_right', 'hidden'];

  test('every zone is inside the stage, with and without a side panel', () => {
    for (const p of positions) {
      for (const sidePanel of [true, false]) {
        const z = computeZones(p, { sidePanel });
        for (const r of [z.board, z.title, z.media, z.captions]) assert.ok(inStage(r), `${p} ${JSON.stringify(r)}`);
        if (sidePanel) assert.ok(z.side && inStage(z.side));
        else assert.equal(z.side, null);
      }
    }
  });

  test('board, media and side panel never overlap the mascot or each other', () => {
    for (const p of positions) {
      const key = positionKey(p);
      const body = /** @type {Record<string, {x0: number, x1: number}>} */ (MASCOT)[key];
      for (const sidePanel of [true, false]) {
        const z = computeZones(p, { sidePanel });
        if (body) {
          const x = body.x0 * STAGE_WIDTH;
          const mascot = { x, y: 0, w: body.x1 * STAGE_WIDTH - x, h: STAGE_HEIGHT };
          assert.equal(intersects(z.board, mascot), false, `${p}: board overlaps mascot`);
          assert.equal(intersects(z.media, mascot), false, `${p}: media overlaps mascot`);
          assert.equal(intersects(z.title, mascot), false, `${p}: title overlaps mascot`);
          if (z.side) assert.equal(intersects(z.side, mascot), false, `${p}: side panel overlaps mascot`);
        }
        if (z.side) assert.equal(intersects(z.board, z.side), false, `${p}: board overlaps side panel`);
      }
    }
  });

  test('fitAspect: largest box of the aspect, centred horizontally, top-aligned, inside the zone', () => {
    const zone = { x: 720, y: 168, w: 1140, h: 712 };
    const wide = fitAspect(zone, 16 / 9);
    assert.deepEqual(wide, { x: 720, y: 168, w: 1140, h: 641 }, 'width-limited 16:9');
    const portrait = fitAspect(zone, 3 / 4);
    assert.equal(portrait.h, 712);
    assert.equal(portrait.w, 534);
    assert.equal(portrait.x, 720 + Math.round((1140 - 534) / 2));
    assert.equal(portrait.y, zone.y);
    for (const a of [0.2, 0.75, 1, 16 / 9, 4, 21 / 9]) {
      const r = fitAspect(zone, a);
      assert.ok(r.x >= zone.x && r.y >= zone.y && r.x + r.w <= zone.x + zone.w && r.y + r.h <= zone.y + zone.h, `aspect ${a}`);
      assert.ok(r.w === zone.w || r.h === zone.h, 'touches the zone on one axis');
    }
    assert.deepEqual(fitAspect(zone, null), zone);
    assert.deepEqual(fitAspect(zone, NaN), zone);
    assert.deepEqual(fitAspect(zone, -1), zone);
    assert.notEqual(fitAspect(zone, null), zone, 'returns a copy');
  });

  test('mediaAspect reads MediaRef width/height', () => {
    assert.equal(mediaAspect({ width: 1280, height: 720 }), 16 / 9);
    assert.equal(mediaAspect({ width: 0, height: 720 }), null);
    assert.equal(mediaAspect({ width: null, height: 720 }), null);
    assert.equal(mediaAspect(null), null);
  });

  test('text zones keep clear of the caption band', () => {
    for (const p of positions) {
      const z = computeZones(p, { sidePanel: true });
      assert.ok(z.board.y + z.board.h <= z.captions.y);
      assert.ok(z.media.y + z.media.h <= z.captions.y);
      assert.ok(z.side && z.side.y + z.side.h <= z.captions.y);
    }
  });

  test('v1 placement: left puts the side panel on the right, right puts it on the left', () => {
    const left = computeZones('left', { sidePanel: true });
    assert.ok(left.side && left.side.x > left.board.x);
    assert.ok(left.side && left.side.x >= 0.75 * STAGE_WIDTH);
    const right = computeZones('right', { sidePanel: true });
    assert.ok(right.side && right.side.x < right.board.x);
    assert.ok(right.side && right.side.x + right.side.w <= 0.25 * STAGE_WIDTH);
  });

  test('without a side panel the board widens into the panel area', () => {
    for (const p of ['left', 'right', 'hidden']) {
      const a = computeZones(p, { sidePanel: true });
      const b = computeZones(p, { sidePanel: false });
      assert.ok(b.board.w > a.board.w, p);
    }
  });

  test('fullscreen media sits below the title band inside the board region', () => {
    const z = computeZones('left', { sidePanel: false });
    assert.equal(z.media.x, z.board.x);
    assert.equal(z.media.w, z.board.w);
    assert.ok(z.media.y >= z.title.y + z.title.h);
    assert.ok(z.media.w >= 1100, 'media is large');
    const hidden = computeZones('hidden', { sidePanel: false });
    assert.ok(hidden.media.w >= 1500);
  });

  test('popup is the content-first layout: large media and board in front of the close-up', () => {
    for (const p of ['popup_bottom_left', 'popup_bottom_right']) {
      for (const sidePanel of [true, false]) {
        const z = computeZones(p, { sidePanel });
        const media = fitAspect(z.media, 16 / 9);
        assert.ok(media.w >= 1200 && media.h >= 680, `${p}: 16:9 media is large (${media.w}x${media.h})`);
        assert.ok(media.w * media.h >= 0.4 * STAGE_WIDTH * STAGE_HEIGHT, 'not a thumbnail');
        assert.ok(z.board.w >= 1300, `${p}: board/quiz get a full-size card`);
        assert.ok(z.title.w >= 1300, 'long titles get room');
        assert.equal(z.media.x, z.board.x);
        assert.equal(z.media.w, z.board.w);
        const face = { x: POPUP_FACE.x0 * STAGE_WIDTH, y: 0, w: (POPUP_FACE.x1 - POPUP_FACE.x0) * STAGE_WIDTH, h: STAGE_HEIGHT };
        assert.ok(intersects(media, face), 'the media covers the close-up (like v1 popup), it is not squeezed beside it');
        if (sidePanel) {
          assert.ok(z.side && z.side.x >= z.board.x + z.board.w, 'side panel on the right of the content');
          assert.ok(z.side && z.side.w >= 400);
        }
      }
    }
    assert.deepEqual(computeZones('popup_bottom_left', { sidePanel: false }).media, computeZones('popup_bottom_left', { sidePanel: true }).media);
  });

  test('media zones are large in every position (no thumbnails)', () => {
    for (const p of positions) {
      const media = fitAspect(computeZones(p, { sidePanel: false }).media, 16 / 9);
      if (p === 'center') continue; // centre keeps the mascot in the middle: media beside it
      assert.ok(media.w >= 1100, `${p}: ${media.w}`);
    }
  });

  test('popup positions share one layout; unknown positions fall back to left', () => {
    assert.deepEqual(computeZones('popup_bottom_left'), computeZones('popup_bottom_right'));
    assert.equal(positionKey('nonsense'), 'left');
    assert.equal(positionKey(undefined), 'left');
    assert.equal(positionKey('hidden'), 'hidden');
  });
});

describe('stage fit', () => {
  test('letterboxes a 16:9 stage into any container', () => {
    assert.deepEqual(computeFit(1920, 1080), { scale: 1, x: 0, y: 0 });
    assert.deepEqual(computeFit(960, 540), { scale: 0.5, x: 0, y: 0 });
    const tall = computeFit(1000, 1000);
    assert.ok(Math.abs(tall.scale - 1000 / 1920) < 1e-12);
    assert.ok(Math.abs(tall.x) < 1e-9);
    assert.ok(Math.abs(tall.y - (1000 - 1080 * tall.scale) / 2) < 1e-9);
    const wide = computeFit(3000, 1080);
    assert.equal(wide.scale, 1);
    assert.equal(wide.x, (3000 - 1920) / 2);
  });

  test('degenerate containers fall back to scale 1', () => {
    assert.deepEqual(computeFit(0, 0), { scale: 1, x: 0, y: 0 });
    assert.deepEqual(computeFit(NaN, 500), { scale: 1, x: 0, y: 0 });
  });
});

describe('Ken Burns', () => {
  const kb = { start: { cx: 0.4, cy: 0.5, scale: 1 }, end: { cx: 0.6, cy: 0.4, scale: 1.2 } };

  test('linear interpolation over the scene, clamped at both ends', () => {
    const a = kenBurnsAt(kb, 0, 10);
    assert.equal(a.scale, 1);
    assert.equal(a.cx, 0.5, 'scale 1 forces the centre (no room to pan)');
    const mid = kenBurnsAt(kb, 5, 10);
    assert.ok(Math.abs(mid.scale - 1.1) < 1e-12);
    const end = kenBurnsAt(kb, 10, 10);
    assert.ok(Math.abs(end.scale - 1.2) < 1e-12);
    assert.ok(Math.abs(end.cx - 0.5833333333) < 1e-6, 'centre clamped so the window stays inside the image');
    assert.deepEqual(kenBurnsAt(kb, 99, 10), end);
    assert.deepEqual(kenBurnsAt(kb, -5, 10), a);
  });

  test('the focus point maps to the centre of the box', () => {
    const f = kenBurnsAt(kb, 7, 10);
    // transform-origin 0 0: x' = tx% + scale * x  => focus cx lands at 50%
    assert.ok(Math.abs(f.tx + f.scale * f.cx * 100 - 50) < 1e-9);
    assert.ok(Math.abs(f.ty + f.scale * f.cy * 100 - 50) < 1e-9);
    assert.match(kenBurnsTransform(f), /^translate\(-?\d+\.\d{4}%, -?\d+\.\d{4}%\) scale\(\d\.\d{5}\)$/);
  });

  test('defaults: 1 -> 1.15 centred; missing spec and zero duration are safe', () => {
    assert.ok(Math.abs(kenBurnsAt(null, 10, 10).scale - 1.15) < 1e-12);
    assert.equal(kenBurnsAt({}, 5, 0).scale, 1);
  });
});

describe('media time', () => {
  test('loop wraps, freeze holds just before the end, unknown duration passes through', () => {
    assert.equal(mediaTimeAt(7, 3, 'loop'), 1);
    assert.equal(mediaTimeAt(2, 3, 'loop'), 2);
    assert.ok(Math.abs(mediaTimeAt(7, 3, 'freeze') - 2.96) < 1e-9);
    assert.equal(mediaTimeAt(1, 3, 'freeze'), 1);
    assert.equal(mediaTimeAt(5, NaN, 'loop'), 5);
    assert.equal(mediaTimeAt(-1, 3, 'loop'), 0);
  });
});

describe('format helpers', () => {
  test('time and numbers', () => {
    assert.equal(formatTime(0), '0:00');
    assert.equal(formatTime(65.9), '1:05');
    assert.equal(formatTime(3725), '1:02:05');
    assert.equal(formatTime(-3), '0:00');
    assert.equal(formatTime(NaN), '0:00');
    assert.equal(formatDurationLong(61), '1 minute 1 second');
    assert.equal(formatDurationLong(0), '0 seconds');
    assert.equal(formatDurationLong(7322), '2 hours 2 minutes 2 seconds');
    assert.equal(formatRate(1.25), '1.25x');
    assert.equal(formatRate(1), '1x');
    assert.equal(clamp(5, 0, 1), 1);
    assert.equal(clamp(NaN, 0, 1), 0);
    assert.equal(lerp(0, 10, 0.25), 2.5);
    assert.equal(num('x', 7), 7);
    assert.equal(num(Infinity, 7), 7);
    assert.equal(roundTo(1.23456, 2), 1.23);
    assert.equal(truncate('abcdef', 4), 'abc…');
    assert.equal(truncate('abc', 4), 'abc');
    assert.equal(optionLetter(2), 'C');
  });

  test('fnv1a is deterministic 8-hex', () => {
    assert.equal(fnv1a(''), '811c9dc5');
    assert.equal(fnv1a('a'), 'e40c292c');
    assert.match(fnv1a('anything'), /^[0-9a-f]{8}$/);
    assert.notEqual(fnv1a('ab'), fnv1a('ba'));
  });
});
