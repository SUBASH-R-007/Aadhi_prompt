// @ts-check
/**
 * The mascot's speech motion follows the build-time loudness envelope (TimedScene.audio_envelope):
 * decoding and indexing (audio.js) and the player's choice envelope > WebAudio meter > synthetic bob.
 */
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { decodeEnvelope, envelopeLevel, hasEnvelope, needsSpeechMeter, ENVELOPE_FPS } = await import('../../js/player/audio.js');
const { Player } = await import('../../js/player/player.js');

after(() => uninstallDom());

/** @param {number[]} values */
const b64 = (values) => Buffer.from(Uint8Array.from(values)).toString('base64');

describe('envelope decoding and lookup', () => {
  test('decodes base64 bytes; empty or malformed envelopes are absent', () => {
    assert.deepEqual([...(/** @type {Uint8Array} */ (decodeEnvelope(b64([0, 128, 255]))))], [0, 128, 255]);
    assert.equal(decodeEnvelope(''), null);
    assert.equal(decodeEnvelope(null), null);
    assert.equal(decodeEnvelope(undefined), null);
    assert.equal(decodeEnvelope('***not base64***'), null);
  });

  test('indexes floor((sceneT - audio_offset) * fps); 0 outside the narration, null without an envelope', () => {
    const scene = /** @type {any} */ ({ scene_id: 's', audio_offset: 0.5, audio_envelope: b64([10, 20, 255, 0]), audio_envelope_fps: 2 });
    assert.equal(envelopeLevel(scene, 0.4), 0, 'before the narration');
    assert.equal(envelopeLevel(scene, 0.5), 10 / 255);
    assert.equal(envelopeLevel(scene, 0.99), 10 / 255);
    assert.equal(envelopeLevel(scene, 1.0), 20 / 255);
    assert.equal(envelopeLevel(scene, 1.5), 1);
    assert.equal(envelopeLevel(scene, 2.49), 0);
    assert.equal(envelopeLevel(scene, 2.5), 0, 'after the narration');
    assert.ok(hasEnvelope(scene));
    const none = /** @type {any} */ ({ scene_id: 'n', audio_offset: 0.5 });
    assert.equal(envelopeLevel(none, 1), null);
    assert.equal(hasEnvelope(none), false);
    const defaultFps = /** @type {any} */ ({ scene_id: 'd', audio_offset: 0, audio_envelope: b64(new Array(ENVELOPE_FPS + 1).fill(0).map((_, i) => i)) });
    assert.equal(envelopeLevel(defaultFps, 1), ENVELOPE_FPS / 255, `fps defaults to ${ENVELOPE_FPS}`);
  });

  test('the WebAudio meter is only needed for narrated, same-origin scenes without an envelope', () => {
    const env = b64([1]);
    assert.equal(needsSpeechMeter(/** @type {any} */ ([{ audio_url: '/a.mp3' }, { audio_url: null }])), true);
    assert.equal(needsSpeechMeter(/** @type {any} */ ([{ audio_url: '/a.mp3', audio_envelope: env }, { audio_url: null }])), false);
    assert.equal(needsSpeechMeter(/** @type {any} */ ([{ audio_url: '/a.mp3', audio_envelope: env }, { audio_url: '/b.mp3' }])), true);
    assert.equal(needsSpeechMeter(/** @type {any} */ ([{ audio_url: 'https://cdn.example.com/a.mp3' }])), false, 'cross-origin audio is never analysed');
  });
});

/** @type {any[]} */
let made = [];
afterEach(() => {
  for (const p of made) p.destroy();
  made = [];
  document.body.replaceChildren();
});

function setup() {
  let now = 0;
  /** @type {Map<number, FrameRequestCallback>} */
  const frames = new Map();
  let fid = 0;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const tex = fakeTex();
  const p = new Player(root, {
    mode: 'live',
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
  return {
    p,
    /** @param {number} ms */
    step(ms) {
      now += ms;
      const pending = [...frames.values()];
      frames.clear();
      for (const cb of pending) cb(now);
    },
  };
}

describe('player: speech motion from the envelope', () => {
  test('the mascot moves with the envelope; no WebAudio meter when every narrated scene has one', async () => {
    const timeline = loadTimeline();
    for (const s of timeline.scenes) {
      if (s.audio_url) {
        // loud for the first 2 s of each narration, silent afterwards
        s.audio_envelope = b64([...new Array(60).fill(255), ...new Array(600).fill(0)]);
        s.audio_envelope_fps = 30;
      }
    }
    const h = setup();
    await h.p.load(timeline);
    assert.equal(/** @type {any} */ (h.p).meter, null, 'no WebAudio graph');
    const mascot = /** @type {any} */ (h.p).mascot;
    const ohm = sceneById(timeline, 's-ohm');
    h.p.seek(ohm.start + 1.0); // 0.5 s into the narration: loud
    h.p.play();
    for (let i = 0; i < 10; i++) h.step(16);
    assert.ok(mascot.level > 0.9, `level follows the loud envelope (${mascot.level})`);
    assert.match(mascot.motion.style.transform, /translateY\(-/);
    h.p.seek(ohm.start + 3.5); // 3 s into the narration: silent, although b2 is a speaking beat
    for (let i = 0; i < 60; i++) h.step(16);
    assert.ok(mascot.level < 0.05, `level decays in silence (${mascot.level})`);
    h.p.pause();
  });

  test('timelines built before the envelope keep the meter (same-origin) and the synthetic bob', async () => {
    const timeline = loadTimeline();
    const h = setup();
    await h.p.load(timeline);
    assert.ok(/** @type {any} */ (h.p).meter, 'fallback meter for same-origin narration');
    const mascot = /** @type {any} */ (h.p).mascot;
    const ohm = sceneById(timeline, 's-ohm');
    h.p.seek(ohm.start + 1.0);
    h.p.play();
    for (let i = 0; i < 10; i++) h.step(16);
    assert.ok(mascot.level > 0.3, 'synthetic bob while a beat is spoken (the meter never attached in jsdom)');
    h.p.pause();
  });
});
