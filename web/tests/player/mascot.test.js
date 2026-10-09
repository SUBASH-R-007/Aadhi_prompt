// @ts-check
/**
 * MascotController robustness (live/preview): posters, lesson preload, frame-gated crossfade,
 * load-error retries, play failures, the watchdog and the poster fallback. Media events are
 * dispatched by hand (jsdom does not load media); see _dom.js for the fake playback state.
 */
import { test, describe, after, afterEach, beforeEach, mock } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from './_dom.js';
import { loadTimeline } from './_fixture.js';

installDom();
const { MascotController, MASCOT_TIMING, posterUrl } = await import('../../js/player/mascot.js');

after(() => uninstallDom());

const branding = /** @type {any} */ (loadTimeline().branding);
const BASE = 'http://localhost';

/** @type {any[]} */
let made = [];
afterEach(() => {
  for (const m of made) m.destroy();
  made = [];
  mock.timers.reset();
  document.body.replaceChildren();
});

/**
 * @param {Partial<import('../../js/player/mascot.js').MascotOptions>} [opts]
 * @param {any} [b]
 */
function make(opts = {}, b = branding) {
  const layer = document.createElement('div');
  document.body.appendChild(layer);
  const m = new MascotController(layer, b, { mode: 'live', ...opts });
  made.push(m);
  return m;
}

/** @param {any} m @param {string} url */
const clip = (m, url) => /** @type {HTMLVideoElement} */ (m.clips.get(url));
/** @param {HTMLElement} el @param {string} type */
const fire = (el, type) => el.dispatchEvent(new window.Event(type));
/** @param {string} name */
const domError = (name) => Object.assign(new Error(name), { name });
const flush = () => new Promise((r) => setImmediate(r));
/** @param {any} m */
const currentFrame = (m) => /** @type {HTMLImageElement | undefined} */ (m.frames.find((/** @type {HTMLElement} */ f) => f.classList.contains('is-current')));

/**
 * Make a fake clip report no decoded frame yet (readyState 0) until `ready()` is called.
 * @param {HTMLVideoElement} v
 */
function notReady(v) {
  Object.defineProperty(v, 'readyState', { configurable: true, value: 0 });
  return () => {
    delete (/** @type {any} */ (v)).readyState;
    fire(v, 'loadeddata');
  };
}

describe('mascot posters', () => {
  test('posterUrl maps top-level branding clips to posters/<name>.jpg and nothing else', () => {
    assert.equal(posterUrl('/branding/aadhi_left_clean.mp4'), '/branding/posters/aadhi_left_clean.jpg');
    assert.equal(posterUrl('/branding/no_aadhi.webm'), '/branding/posters/no_aadhi.jpg');
    for (const bad of ['/media/a.mp4', '/branding/sub/a.mp4', '/branding/../a.mp4', '/branding/.a.mp4', '/branding/a.png',
      'https://cdn.example.com/branding/a.mp4', 'javascript:alert(1)', '', null, undefined]) {
      assert.equal(posterUrl(bad), null, String(bad));
    }
  });

  test('every clip shows its poster until it has a frame', () => {
    const m = make();
    assert.equal(clip(m, '/branding/aadhi_left.mp4').getAttribute('poster'), `${BASE}/branding/posters/aadhi_left.jpg`);
    assert.equal(clip(m, '/branding/aadhi_popup.mp4').getAttribute('poster'), `${BASE}/branding/posters/aadhi_popup.jpg`);
    const custom = make({}, { mascot_clips: { left: 'https://cdn.example.com/x.mp4' } });
    assert.equal(clip(custom, 'https://cdn.example.com/x.mp4').getAttribute('poster'), null, 'no poster outside /branding/');
  });

  test('render mode builds no clips, posters or fallback frames, and tick is inert', () => {
    const layer = document.createElement('div');
    const r = new MascotController(layer, branding, { mode: 'render' });
    made.push(r);
    assert.equal(layer.querySelectorAll('video, img').length, 0);
    r.setPlaying(true);
    r.setPosition('left');
    r.tick({ force: true });
    assert.equal(r.getStatus().clip, null);
    assert.ok(r.el.classList.contains('is-hidden'));
  });
});

describe('mascot lesson preload', () => {
  test('only the clips of the lecture positions load eagerly; others upgrade on first use', () => {
    const m = make({ positions: ['left', 'popup_bottom_right', null] }); // null scene position = left
    const preload = (/** @type {string} */ url) => clip(m, url).getAttribute('preload');
    assert.equal(preload('/branding/aadhi_left.mp4'), 'auto');
    assert.equal(preload('/branding/aadhi_popup.mp4'), 'auto');
    for (const url of ['/branding/aadhi_right.mp4', '/branding/aadhi_center.mp4', '/branding/no_aadhi.mp4']) {
      assert.equal(preload(url), 'metadata', url);
    }
    assert.equal(m.getStatus().clips['/branding/aadhi_left.mp4'], 'loading');
    assert.equal(m.getStatus().clips['/branding/aadhi_right.mp4'], 'idle');
    m.setPosition('right');
    assert.equal(preload('/branding/aadhi_right.mp4'), 'auto');
    assert.equal(m.getStatus().status, 'loading');
    fire(clip(m, '/branding/aadhi_right.mp4'), 'loadeddata');
    assert.equal(m.getStatus().status, 'loaded');
    assert.equal(make().clips.size, 5);
    assert.ok([...make().clips.values()].every((v) => v.getAttribute('preload') === 'auto'), 'no positions: all eager');
  });
});

describe('mascot frame-gated crossfade', () => {
  test('the clip on screen stays until the incoming clip has a frame', () => {
    const m = make();
    m.setPlaying(true);
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    const right = clip(m, '/branding/aadhi_right.mp4');
    const ready = notReady(right);
    m.setPosition('right');
    assert.equal(m.active, right, 'active switches at once');
    assert.equal(right.paused, false, 'the incoming clip starts loading/playing');
    assert.ok(left.classList.contains('is-active') && !right.classList.contains('is-active'), 'left still on screen');
    assert.equal(m.getStatus().shown, '/branding/aadhi_left.mp4');
    ready();
    assert.ok(right.classList.contains('is-active') && !left.classList.contains('is-active'));
    assert.equal(left.paused, false, 'both play through the crossfade');
  });

  test('the wait is capped; rapid changes never leave stray clips playing', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    const m = make();
    m.setPlaying(true);
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    const right = clip(m, '/branding/aadhi_right.mp4');
    const center = clip(m, '/branding/aadhi_center.mp4');
    const readyRight = notReady(right);
    notReady(center);
    m.setPosition('right');
    m.setPosition('center');
    assert.equal(right.paused, true, 'the superseded incoming clip is paused');
    readyRight();
    assert.ok(left.classList.contains('is-active'), 'a stale loadeddata does not swap');
    mock.timers.tick(MASCOT_TIMING.swapTimeoutMs);
    assert.ok(center.classList.contains('is-active') && !left.classList.contains('is-active'), 'swapped after the cap');
    mock.timers.tick(MASCOT_TIMING.crossfadeMs);
    const playing = [...m.clips.values()].filter((v) => !v.paused);
    assert.deepEqual(playing, [center], 'only the active clip keeps playing');
  });
});

describe('mascot recovery', () => {
  test('a load error retries at 1 s and 3 s with a cache-busting URL, then fails over to the poster', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    const warn = mock.method(console, 'warn', () => {});
    const m = make();
    m.setPlaying(true);
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    fire(left, 'error');
    assert.equal(m.getStatus().status, 'error');
    assert.ok(m.el.classList.contains('is-fallback'));
    assert.equal(m.getStatus().fallback, 'clip error, retrying');
    assert.equal(currentFrame(m)?.getAttribute('src'), `${BASE}/branding/posters/aadhi_left.jpg`);
    mock.timers.tick(999);
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4`);
    mock.timers.tick(1);
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4?r=1`);
    assert.equal(left.getAttribute('preload'), 'auto');
    assert.equal(m.getStatus().status, 'loading');
    fire(left, 'error');
    mock.timers.tick(MASCOT_TIMING.retryDelaysMs[1]);
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4?r=2`);
    fire(left, 'error');
    assert.equal(m.getStatus().status, 'failed');
    assert.equal(m.getStatus().fallback, 'clip failed to load');
    assert.equal(warn.mock.callCount(), 1);
    mock.timers.tick(60000);
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4?r=2`, 'no more automatic retries');
    assert.deepEqual(m.getStatus().counts, { switches: 1, fallbacks: 1, stalls: 0, reloads: 2, errors: 3, playFailures: 0 });
    // Becoming active again gives the failed clip one more try.
    m.setPosition('right');
    m.setPosition('left');
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4?r=3`);
    assert.equal(m.getStatus().status, 'loading');
    fire(left, 'error');
    assert.equal(m.getStatus().status, 'failed', 'the extra try does not restart the retry ladder');
    assert.equal(warn.mock.callCount(), 1, 'warned once per clip');
  });

  test('destroy clears pending retries', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    const m = make();
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    fire(left, 'error');
    m.destroy();
    mock.timers.tick(5000);
    assert.equal(m.getStatus().counts.reloads, 0);
    assert.equal(left.getAttribute('src'), null, 'released');
  });

  test('the fallback ends once the active clip plays again; posters crossfade on a position change', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    const m = make();
    m.setPlaying(true);
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    fire(left, 'error');
    const first = currentFrame(m);
    m.setPosition('right'); // healthy clip: its poster while the fallback is up
    const second = currentFrame(m);
    assert.notEqual(first, second);
    assert.equal(second?.getAttribute('src'), `${BASE}/branding/posters/aadhi_right.jpg`);
    fire(clip(m, '/branding/aadhi_right.mp4'), 'playing');
    assert.equal(m.el.classList.contains('is-fallback'), false);
    assert.equal(m.getStatus().fallback, null);
  });

  test('a missing poster falls back to the static background, then to nothing', () => {
    const m = make();
    m.setPlaying(true);
    m.setPosition('hidden');
    fire(clip(m, '/branding/no_aadhi.mp4'), 'error');
    const frame = /** @type {HTMLImageElement} */ (currentFrame(m));
    assert.equal(frame.getAttribute('src'), `${BASE}/branding/posters/no_aadhi.jpg`);
    assert.equal(m.el.dataset.position, 'hidden', 'CSS: the empty studio does not breathe');
    fire(frame, 'error');
    assert.equal(frame.getAttribute('src'), `${BASE}/branding/static_background.png`);
    assert.ok(frame.classList.contains('is-still'));
    fire(frame, 'error');
    assert.equal(currentFrame(m), undefined, 'no broken image on stage');
  });

  test('play() failures: AbortError ignored, three others fall back, NotAllowedError waits for a gesture', async () => {
    const m = make();
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    let calls = 0;
    /** @type {string} */
    let reject = 'AbortError';
    left.play = () => {
      calls++;
      return Promise.reject(domError(reject));
    };
    m.setPlaying(true);
    await flush();
    assert.equal(m.getStatus().fallback, null, 'AbortError is not a failure');
    reject = 'NotSupportedError';
    for (let i = 0; i < 2; i++) m.setPlaying(true);
    await flush();
    assert.equal(m.getStatus().fallback, null);
    m.setPlaying(true);
    await flush();
    assert.equal(m.getStatus().fallback, 'playback keeps failing');
    assert.equal(m.getStatus().counts.playFailures, 3);

    const m2 = make({ now: () => 0 });
    m2.setPosition('left');
    const l2 = clip(m2, '/branding/aadhi_left.mp4');
    l2.play = () => {
      calls++;
      return Promise.reject(domError('NotAllowedError'));
    };
    m2.setPlaying(true);
    await flush();
    assert.equal(m2.getStatus().blocked, true);
    assert.equal(m2.getStatus().fallback, 'autoplay blocked');
    const before = calls;
    m2.tick({ force: true });
    assert.equal(calls, before, 'the watchdog does not retry a blocked clip');
    m2.setPlaying(true); // the next play is a user gesture
    assert.equal(calls, before + 1);
  });

  test('an unexpected ended restarts the clip', () => {
    const m = make();
    m.setPlaying(true);
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    left.currentTime = 7.9;
    left.pause();
    fire(left, 'ended');
    assert.equal(left.currentTime, 0);
    assert.equal(left.paused, false);
  });
});

describe('mascot watchdog', () => {
  /** @type {number} */
  let now = 0;
  beforeEach(() => {
    now = 0;
  });

  test('restarts a clip the browser paused and pauses strays, only while the lecture plays', () => {
    const m = make({ now: () => now });
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    const right = clip(m, '/branding/aadhi_right.mp4');
    m.tick();
    assert.equal(left.paused, true, 'lecture paused: nothing restarts');
    m.setPlaying(true);
    left.pause(); // power saving / media keys
    right.play(); // a stray clip
    now += MASCOT_TIMING.watchdogMs;
    m.tick();
    assert.equal(left.paused, false);
    assert.equal(right.paused, true);
    left.pause();
    m.tick(); // throttled
    assert.equal(left.paused, true);
    m.setVisible(false); // intro
    m.tick({ force: true });
    assert.equal(left.paused, true, 'not while the layer is hidden');
    m.setVisible(true);
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    try {
      m.tick({ force: true });
      assert.equal(left.paused, true, 'not in a hidden tab (browsers pause background video on purpose)');
    } finally {
      delete (/** @type {any} */ (document)).visibilityState;
    }
    m.tick({ force: true });
    assert.equal(left.paused, false, 'resumed once visible');
  });

  test('a frozen clip gets the poster at once and a reload on the second strike', () => {
    const m = make({ now: () => now });
    m.setPlaying(true);
    m.setPosition('left');
    const left = clip(m, '/branding/aadhi_left.mp4');
    const step = (/** @type {number} */ ms, /** @type {boolean} */ progress) => {
      now += ms;
      if (progress) left.currentTime += ms / 1000;
      m.tick();
    };
    step(1000, true); // first tick: baseline
    for (let i = 0; i < 6; i++) step(1000, true);
    assert.equal(m.getStatus().fallback, null, 'progressing clip is healthy');
    for (let i = 0; i < 3; i++) step(1000, false);
    assert.equal(m.getStatus().fallback, null, 'frozen 3 s: not yet');
    step(1000, false);
    assert.equal(m.getStatus().fallback, 'clip stalled');
    assert.equal(m.getStatus().counts.stalls, 1);
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4`, 'first strike: no reload');
    for (let i = 0; i < 4; i++) step(1000, false);
    assert.equal(m.getStatus().counts.stalls, 2);
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4?r=1`, 'second strike reloads');
    step(1000, true);
    assert.equal(m.getStatus().fallback, null, 'progress ends the fallback');
  });

  test('no stall verdict right after a gap (hidden tab, paused lecture)', () => {
    const m = make({ now: () => now });
    m.setPlaying(true);
    m.setPosition('left');
    m.tick();
    now += 60000; // no ticks for a minute, currentTime unchanged
    m.tick();
    assert.equal(m.getStatus().fallback, null);
    now += 1000;
    m.tick();
    assert.equal(m.getStatus().fallback, null);
  });

  test('getStatus reports position, playback and per-clip status', () => {
    const m = make({ now: () => now });
    m.setPlaying(true);
    m.setPosition('popup_bottom_left');
    const s = m.getStatus();
    assert.equal(s.position, 'popup_bottom_left');
    assert.equal(s.clip, '/branding/aadhi_popup.mp4');
    assert.equal(s.shown, '/branding/aadhi_popup.mp4');
    assert.equal(s.playing, true);
    assert.equal(s.visible, true);
    assert.equal(Object.keys(s.clips).length, 5);
    assert.equal(s.counts.switches, 1);
  });
});

describe('mascot watchdog: slow buffering', () => {
  test('a clip still buffering while data arrives keeps its poster and is not reloaded', () => {
    let now = 0;
    const m = make({ now: () => now });
    const left = clip(m, '/branding/aadhi_left.mp4');
    Object.defineProperty(left, 'readyState', { configurable: true, value: 1 }); // HAVE_METADATA
    left.play = () => {
      Object.defineProperty(left, 'paused', { configurable: true, value: false });
      return new Promise(() => {}); // pending until enough data has arrived
    };
    m.setPosition('left');
    m.setPlaying(true);
    for (let i = 0; i < 30; i++) {
      now += 1000;
      fire(left, 'progress'); // the download is still moving
      m.tick();
    }
    assert.equal(left.getAttribute('src'), `${BASE}/branding/aadhi_left.mp4`, 'the partial download is kept');
    assert.equal(m.getStatus().counts.reloads, 0);
    assert.equal(m.getStatus().counts.stalls, 0);
    assert.equal(m.getStatus().fallback, 'clip buffering');
    for (let i = 0; i < 12; i++) {
      now += 1000; // the download hangs: no more data
      m.tick();
    }
    assert.ok(m.getStatus().counts.reloads >= 1, 'a hung download still recovers');
  });
});
