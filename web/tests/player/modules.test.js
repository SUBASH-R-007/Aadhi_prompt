// @ts-check
/** Intro, mascot, audio plumbing, panel loader, fonts and icons. */
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from './_dom.js';
import { loadTimeline } from './_fixture.js';

installDom();
const { IntroView, introStates, introKey, cardOpacity } = await import('../../js/player/intro.js');
const { MascotController } = await import('../../js/player/mascot.js');
const { NarrationDeck, SoundEffects, BackgroundMusic, SpeechMeter, isSameOrigin } = await import('../../js/player/audio.js');
const { buildPanel, emptyPanel, loadPanelModule, loadPanelFactory } = await import('../../js/player/panel-loader.js');
const { fontSpecsFor, ensureFonts } = await import('../../js/player/fonts.js');
const { icon, ICON_NAMES } = await import('../../js/player/icons.js');

after(() => uninstallDom());
afterEach(() => document.body.replaceChildren());

const timeline = loadTimeline();
const intro = /** @type {any} */ (timeline.intro);

describe('intro', () => {
  test('state list: logo phase then one state per card, keys match renderAt', () => {
    const states = introStates(intro);
    assert.deepEqual(states.map((s) => s.t), [0, 5.5, 8.5]);
    const layer = document.createElement('div');
    const view = new IntroView(layer, intro, { mode: 'render' });
    for (const s of states) assert.equal(view.renderAt(s.t + 0.1), s.key);
    assert.equal(view.renderAt(1), 'intro-logo');
    assert.equal(introStates(null).length, 0);
    assert.match(introKey(intro, 0), /^intro-0-[0-9a-f]{8}$/);
    view.destroy();
  });

  test('render mode has no media elements; cards are text', () => {
    const layer = document.createElement('div');
    const view = new IntroView(layer, intro, { mode: 'render' });
    assert.equal(layer.querySelectorAll('video, img').length, 0);
    view.renderAt(6);
    assert.equal(layer.querySelector('.ap-intro-line1')?.textContent, 'Basic Electrical Engineering');
    view.renderAt(100);
    assert.ok(view.el.classList.contains('is-hidden'));
  });

  test('live: logo video synced to the clock, cards fade in and out', () => {
    const layer = document.createElement('div');
    document.body.appendChild(layer);
    const view = new IntroView(layer, intro, { mode: 'live' });
    const logo = /** @type {HTMLVideoElement} */ (view.logo);
    view.update(2, { playing: true, rate: 1, seeked: true });
    assert.equal(logo.currentTime, 2);
    assert.equal(logo.paused, false);
    assert.ok(view.el.classList.contains('is-logo'));
    view.update(6, { playing: true, rate: 1, seeked: false });
    assert.ok(logo.paused, 'logo stops after its duration');
    assert.equal(view.card.style.opacity, String(cardOpacity(intro.cards[0], 6)));
    assert.equal(cardOpacity(intro.cards[0], 5.5), 0);
    assert.equal(cardOpacity(intro.cards[0], 7), 1);
    assert.ok(cardOpacity(intro.cards[0], 8.3) < 1);
    assert.equal(cardOpacity(intro.cards[0], 8.5), 0);
    view.update(20, { playing: true, rate: 1, seeked: false });
    assert.ok(view.el.classList.contains('is-hidden'));
    view.setVolume(0.5, true);
    assert.equal(logo.muted, true);
    view.destroy();
    assert.equal(logo.getAttribute('src'), null, 'released');
  });
});

describe('mascot', () => {
  const branding = /** @type {any} */ (timeline.branding);

  test('preloads distinct clips muted+looping and crossfades on position change', () => {
    const layer = document.createElement('div');
    const m = new MascotController(layer, branding, { mode: 'live' });
    const clips = [...layer.querySelectorAll('video')];
    assert.equal(clips.length, 5, 'popup positions share one clip');
    assert.ok(clips.every((v) => /** @type {HTMLVideoElement} */ (v).muted && /** @type {HTMLVideoElement} */ (v).loop));
    m.setPlaying(true);
    m.setPosition('left', { origin: '18% 88%' });
    const left = /** @type {HTMLVideoElement} */ (m.active);
    assert.ok(left.classList.contains('is-active'));
    assert.equal(left.paused, false);
    m.setPosition('popup_bottom_left');
    const popup = /** @type {HTMLVideoElement} */ (m.active);
    m.setPosition('popup_bottom_right');
    assert.equal(m.active, popup, 'same clip for both popup positions');
    assert.equal(left.classList.contains('is-active'), false);
    assert.equal(m.motion.style.transformOrigin, '18% 88%');
    m.destroy();
  });

  test('speech level drives a subtle, smoothed scale/bob; render mode stays hidden and inert', () => {
    const layer = document.createElement('div');
    const m = new MascotController(layer, branding, { mode: 'live' });
    m.setLevel(1);
    assert.match(m.motion.style.transform, /translateY\(-\d\.\d+px\) scale\(1\.00\d+\)/);
    for (let i = 0; i < 200; i++) m.setLevel(0);
    assert.equal(m.motion.style.transform, '');
    const r = new MascotController(document.createElement('div'), branding, { mode: 'render' });
    assert.ok(r.el.classList.contains('is-hidden'));
    assert.equal(r.clips.size, 0);
    r.setLevel(1);
    r.setPosition('left');
    assert.equal(r.motion.style.transform, '');
  });

  test('falls back to the static background without clips; rig hook is a stub', async () => {
    const layer = document.createElement('div');
    const m = new MascotController(layer, { static_background_url: '/branding/static_background.png', mascot_rig_url: '/rig.riv' }, { mode: 'live' });
    m.setPosition('right');
    assert.ok(m.background?.classList.contains('is-active'));
    assert.equal(m.rigUrl, '/rig.riv');
    assert.equal(await m.loadRig(), null);
  });
});

describe('audio plumbing', () => {
  test('NarrationDeck reuses the preloaded element for the next scene', () => {
    const deck = new NarrationDeck();
    const a = deck.use(0, '/media/a.mp3');
    deck.preload(1, '/media/b.mp3');
    const spare = deck.slots.find((s) => s.index === 1);
    assert.ok(spare && spare.el !== a);
    const b = deck.use(1, '/media/b.mp3');
    assert.equal(b, spare?.el, 'preloaded element reused');
    assert.ok(a?.paused);
    assert.equal(deck.use(2, null), null, 'silent scenes have no narration');
    assert.equal(deck.use(3, 'javascript:alert(1)'), null, 'unsafe URLs rejected');
    deck.setVolume(0.3, true);
    assert.ok(deck.elements.every((el) => el.muted && Math.abs(el.volume - 0.3) < 1e-9));
    deck.setRate(1.25);
    assert.ok(deck.elements.every((el) => el.playbackRate === 1.25));
    deck.destroy();
    assert.ok(deck.elements.every((el) => el.getAttribute('src') === null));
  });

  test('sound effects respect mute; BGM ducks and stops', () => {
    const sfx = new SoundEffects({ tick: '/branding/tick.wav', ding: null });
    const before = /** @type {any} */ (window).__plays || 0;
    sfx.play('tick');
    sfx.play('ding'); // not configured
    assert.equal((/** @type {any} */ (window).__plays || 0) - before, 1);
    sfx.setVolume(1, true);
    sfx.play('tick');
    assert.equal((/** @type {any} */ (window).__plays || 0) - before, 1);
    sfx.destroy();
    const bgm = new BackgroundMusic('/branding/bgm.mp3', 0.06);
    bgm.setPlaying(true);
    const el = /** @type {HTMLAudioElement} */ (bgm.el);
    assert.equal(el.loop, true);
    assert.ok(Math.abs(el.volume - 0.06) < 1e-9);
    bgm.setDucked(true);
    assert.ok(Math.abs(el.volume - 0.03) < 1e-9);
    bgm.setVolume(0.5, false);
    assert.ok(Math.abs(el.volume - 0.015) < 1e-9);
    bgm.setPlaying(false);
    assert.ok(el.paused);
    bgm.destroy();
    new BackgroundMusic(null, 0.06).setPlaying(true); // no-op without a URL
  });

  test('same-origin detection gates the WebAudio analyser', async () => {
    assert.equal(isSameOrigin('/media/a.mp3'), true);
    assert.equal(isSameOrigin('http://localhost/media/a.mp3'), true);
    assert.equal(isSameOrigin('https://cdn.example.com/a.mp3'), false);
    assert.equal(isSameOrigin(null), true);
    const meter = new SpeechMeter();
    assert.equal(await meter.attach([]), false, 'no AudioContext in jsdom: degrade gracefully');
    assert.equal(meter.level(), null);
    meter.destroy();
  });
});

describe('panel loader', () => {
  test('buildPanel normalises instances and survives throwing factories', () => {
    const container = document.createElement('div');
    /** @type {unknown[]} */
    const errors = [];
    const p = buildPanel(
      () => {
        throw new Error('broken panel');
      },
      container,
      /** @type {any} */ ({ panel: { kind: 'chart' } }),
      /** @type {any} */ ({}),
      (e) => errors.push(e),
    );
    assert.equal(errors.length, 1);
    assert.ok(container.classList.contains('is-empty'));
    p.update(1, /** @type {any} */ ({}));
    assert.equal(p.mediaRect, undefined);
    p.destroy();
    const c2 = document.createElement('div');
    const el = document.createElement('section');
    const q = buildPanel(
      () => ({
        el,
        update() {
          throw new Error('update failed');
        },
        destroy() {},
      }),
      c2,
      /** @type {any} */ ({ panel: { kind: 'chart' } }),
      /** @type {any} */ ({}),
      (e) => errors.push(e),
    );
    assert.equal(el.parentNode, c2, 'element attached when the factory did not');
    q.update(0, /** @type {any} */ ({}));
    assert.equal(errors.length, 2);
    q.destroy();
    assert.equal(el.isConnected, false);
  });

  test('emptyPanel and injected modules', async () => {
    const c = document.createElement('div');
    const p = emptyPanel(c, /** @type {any} */ ({}), /** @type {any} */ ({}));
    assert.ok(p.el.classList.contains('ap-panel--empty'));
    const fake = () => p;
    const mod = await loadPanelModule({ createPanel: /** @type {any} */ (fake) });
    assert.equal(mod.createPanel, fake);
    assert.equal(mod.panelStateTimes, null);
    assert.equal(await loadPanelFactory(/** @type {any} */ (fake)), fake);
  });

  test('loads the real panels module when present, or falls back', async () => {
    const warn = console.warn;
    console.warn = () => {};
    try {
      const mod = await loadPanelModule();
      assert.equal(typeof mod.createPanel, 'function');
    } finally {
      console.warn = warn;
    }
  });
});

describe('fonts and icons', () => {
  test('font specs include the lecture script', () => {
    const ta = fontSpecsFor('ta-IN').map(([f]) => f);
    assert.ok(ta.some((f) => f.includes('Noto Sans Tamil')));
    assert.ok(ta.some((f) => f.includes('Outfit')));
    assert.ok(fontSpecsFor('hi').some(([f]) => f.includes('Devanagari')));
    assert.equal(fontSpecsFor('en-IN').some(([f]) => f.includes('Noto')), false);
  });

  test('ensureFonts degrades without the Font Loading API and honours timeouts', async () => {
    assert.equal(await ensureFonts(['ta-IN']), true);
    const doc = /** @type {any} */ (document);
    doc.fonts = { load: () => new Promise(() => {}), ready: Promise.resolve() };
    try {
      assert.equal(await ensureFonts(['en'], { timeoutMs: 20 }), false);
    } finally {
      delete doc.fonts;
    }
  });

  test('icons are inline SVG with a case-correct viewBox', () => {
    for (const name of ICON_NAMES) {
      const el = icon(name);
      assert.equal(el.getAttribute('viewBox'), '0 0 24 24');
      assert.equal(el.getAttribute('aria-hidden'), 'true');
      assert.ok(el.querySelector('path'));
    }
  });
});
