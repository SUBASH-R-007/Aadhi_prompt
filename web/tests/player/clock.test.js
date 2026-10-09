// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { Clock, mediaMaster, isBuffering } from '../../js/player/clock.js';

/** Controllable ms clock + manual frame scheduler. */
function harness() {
  let now = 1000;
  /** @type {Map<number, FrameRequestCallback>} */
  const frames = new Map();
  let id = 0;
  const clock = new Clock({
    now: () => now,
    requestFrame: (cb) => {
      frames.set(++id, cb);
      return id;
    },
    cancelFrame: (fid) => frames.delete(fid),
  });
  return {
    clock,
    frames,
    /** @param {number} ms */
    advance(ms) {
      now += ms;
    },
    flushFrame() {
      const pending = [...frames.entries()];
      frames.clear();
      for (const [, cb] of pending) cb(now);
    },
  };
}

/** @param {number} a @param {number} b */
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

describe('Clock (performance clock)', () => {
  test('starts paused at 0 and does not advance while paused', () => {
    const { clock, advance } = harness();
    assert.equal(clock.playing, false);
    assert.equal(clock.time(), 0);
    advance(5000);
    assert.equal(clock.time(), 0);
  });

  test('advances in real time while playing and holds on pause', () => {
    const { clock, advance } = harness();
    clock.play();
    advance(1500);
    near(clock.time(), 1.5);
    clock.pause();
    advance(1000);
    near(clock.time(), 1.5);
    clock.play();
    advance(500);
    near(clock.time(), 2.0);
  });

  test('playback rate scales elapsed time and re-anchors continuously', () => {
    const { clock, advance } = harness();
    clock.play();
    advance(1000);
    clock.setRate(1.5);
    near(clock.time(), 1.0);
    advance(1000);
    near(clock.time(), 2.5);
    clock.setRate(100);
    assert.equal(clock.rate, 4, 'rate is clamped');
    clock.setRate(0);
    assert.equal(clock.rate, 1, 'invalid rate falls back to 1');
  });

  test('seek jumps (also backwards) while playing or paused', () => {
    const { clock, advance } = harness();
    clock.seek(42);
    assert.equal(clock.time(), 42);
    clock.play();
    advance(1000);
    near(clock.time(), 43);
    clock.seek(10);
    near(clock.time(), 10);
    clock.seek(-5);
    assert.equal(clock.time(), 0);
    clock.seek(NaN);
    assert.equal(clock.time(), 0);
  });
});

describe('Clock (master source)', () => {
  test('follows the master while it agrees, extrapolating between updates (capped)', () => {
    const { clock, advance } = harness();
    let mt = 0;
    clock.setMaster({ time: () => mt });
    clock.play();
    advance(100);
    mt = 0.1;
    near(clock.time(), 0.1);
    advance(100); // master not updated yet: extrapolate
    near(clock.time(), 0.2);
    assert.equal(clock.mastered, true);
    advance(1000); // master stalled (buffering): extrapolation capped at 0.25 s
    near(clock.time(), 0.35);
    advance(1000);
    near(clock.time(), 0.35, 1e-9);
  });

  test('never runs backwards while playing (master slightly behind)', () => {
    const { clock, advance } = harness();
    let mt = 0;
    clock.setMaster({ time: () => mt });
    clock.play();
    advance(300);
    mt = 0.3;
    near(clock.time(), 0.3);
    mt = 0.25; // audio clock reports a smaller value
    near(clock.time(), 0.3);
  });

  test('ignores a master that disagrees after a seek until it re-aligns', () => {
    const { clock, advance } = harness();
    let mt = 50;
    clock.setMaster({ time: () => mt });
    clock.play();
    advance(10);
    near(clock.time(), 0.01, 1e-6); // perf clock 0.01 vs master 50: master ignored
    assert.equal(clock.mastered, false);
    clock.seek(50);
    mt = 50.05;
    advance(50);
    near(clock.time(), 50.05);
    assert.equal(clock.mastered, true);
  });

  test('null master time falls back to the performance clock seamlessly', () => {
    const { clock, advance } = harness();
    /** @type {number | null} */
    let mt = 0;
    clock.setMaster({ time: () => mt });
    clock.play();
    advance(1000);
    mt = 1.0;
    near(clock.time(), 1.0);
    mt = null; // narration ended
    advance(500);
    near(clock.time(), 1.5);
  });

  test('mediaMaster stays authoritative while the element should play (also buffering/seeking)', () => {
    const el = /** @type {any} */ ({ paused: false, ended: false, seeking: false, readyState: 4, currentTime: 2, error: null });
    const m = mediaMaster(el, 10);
    assert.equal(m.time(), 12);
    el.readyState = 1; // buffering: the clock must wait for the narration, not skip it
    assert.equal(m.time(), 12);
    el.seeking = true;
    assert.equal(m.time(), 12);
    assert.equal(isBuffering(el), true);
    el.seeking = false;
    el.readyState = 4;
    assert.equal(isBuffering(el), false);
    el.paused = true;
    assert.equal(m.time(), null);
    assert.equal(isBuffering(el), false, 'a paused element is not buffering');
    el.paused = false;
    el.ended = true;
    assert.equal(m.time(), null);
    el.ended = false;
    el.error = { code: 4 }; // failed to load: fall back to the performance clock
    assert.equal(m.time(), null);
    assert.equal(isBuffering(el), false);
    assert.equal(isBuffering(null), false);
  });

  test('waits for a buffering master, but gives up on one frozen longer than maxStall', () => {
    let now = 0;
    const clock = new Clock({ now: () => now, requestFrame: () => 0, cancelFrame: () => {}, maxStall: 3 });
    let mt = 0;
    clock.setMaster({ time: () => mt });
    clock.play();
    now = 1000;
    mt = 1.0;
    near(clock.time(), 1.0);
    now = 3000; // audio stuck at 1.0 (buffering): the clock holds within the extrapolation cap
    near(clock.time(), 1.25);
    assert.equal(clock.mastered, true);
    now = 4500; // > maxStall without progress: give up and continue on the performance clock
    const t = clock.time();
    assert.equal(clock.mastered, false);
    near(t, 1.25);
    now = 5500;
    near(clock.time(), 2.25);
    assert.equal(clock.mastered, false, 'the same frozen value is not trusted again');
    mt = 2.3; // audio recovered (re-synced by the player): trusted again
    now = 5600;
    near(clock.time(), 2.3, 1e-6);
    assert.equal(clock.mastered, true);
    now = 5700; // and extrapolated between its updates again
    near(clock.time(), 2.4, 1e-6);
  });
});

describe('Clock ticks', () => {
  test('emits rAF ticks only while playing and stops on pause/unsubscribe', () => {
    const { clock, frames, advance, flushFrame } = harness();
    /** @type {number[]} */
    const ticks = [];
    const off = clock.onTick((t) => ticks.push(t));
    assert.equal(frames.size, 0, 'no frames while paused');
    clock.play();
    assert.equal(frames.size, 1);
    advance(16);
    flushFrame();
    advance(16);
    flushFrame();
    assert.equal(ticks.length, 2);
    near(ticks[1], 0.032);
    clock.pause();
    assert.equal(frames.size, 0);
    clock.play();
    off();
    assert.equal(frames.size, 0);
  });

  test('ticking disabled (render mode) never schedules frames', () => {
    let scheduled = 0;
    const clock = new Clock({ ticking: false, requestFrame: () => ++scheduled, now: () => 0 });
    clock.onTick(() => {});
    clock.play();
    assert.equal(scheduled, 0);
  });

  test('a throwing tick handler does not stop other handlers', () => {
    const { clock, advance, flushFrame } = harness();
    let ok = 0;
    const origError = console.error;
    console.error = () => {};
    try {
      clock.onTick(() => {
        throw new Error('boom');
      });
      clock.onTick(() => ok++);
      clock.play();
      advance(16);
      flushFrame();
    } finally {
      console.error = origError;
    }
    assert.equal(ok, 1);
  });

  test('destroy stops everything', () => {
    const { clock, frames } = harness();
    clock.onTick(() => {});
    clock.play();
    clock.destroy();
    assert.equal(frames.size, 0);
    assert.equal(clock.playing, false);
  });
});
