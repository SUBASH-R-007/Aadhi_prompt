// @ts-check
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex } from './_dom.js';
import { loadTimeline } from './_fixture.js';

installDom();
const { Player } = await import('../../js/player/player.js');
const { Controls, SPEEDS, sceneLabel, captionLift } = await import('../../js/player/controls.js');

after(() => uninstallDom());

async function setup() {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const timeline = loadTimeline();
  let now = 0;
  const player = new Player(host, {
    mode: 'preview',
    analytics: null,
    deps: {
      renderTex: fakeTex().render,
      texIdle: () => Promise.resolve(),
      loadPrism: async () => null,
      createPanel: (c) => {
        const el = document.createElement('div');
        c.appendChild(el);
        return { el, update() {}, destroy() {} };
      },
      now: () => now,
      requestFrame: () => 0,
      cancelFrame: () => {},
      waitForFonts: false,
    },
  });
  await player.load(timeline);
  /** @type {any[]} */
  const prefs = [];
  const controls = new Controls(host, player, { onPrefs: (p) => prefs.push(p), idleMs: 10 });
  return { host, player, controls, timeline, prefs };
}

/** @param {string} key @param {Record<string, any>} [init] @param {EventTarget} [target] */
function press(key, init = {}, target = document) {
  const ev = new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(ev);
  return ev;
}

/** @type {{ player: any, controls: any }[]} */
let made = [];
afterEach(() => {
  for (const m of made) {
    m.controls.destroy();
    m.player.destroy();
  }
  made = [];
  document.body.replaceChildren();
});

describe('Controls UI', () => {
  test('builds buttons, scrubber markers and the scene drawer', async () => {
    const s = await setup();
    made.push(s);
    const { host, timeline } = s;
    assert.ok(host.classList.contains('ap-host'));
    assert.equal(host.querySelectorAll('.ap-scrub-tick').length, timeline.scenes.length);
    assert.equal(host.querySelectorAll('.ap-scrub-chapter').length, timeline.chapters?.length);
    const ch = /** @type {HTMLElement} */ (host.querySelector('.ap-scrub-chapter:nth-of-type(2)') || host.querySelectorAll('.ap-scrub-chapter')[1]);
    assert.ok(ch.style.left.endsWith('%'));
    const items = host.querySelectorAll('.ap-drawer-item');
    assert.equal(items.length, timeline.scenes.length);
    assert.equal(items[0].querySelector('.ap-drawer-title')?.textContent, "Ohm's Law");
    assert.equal(items[6].querySelector('.ap-drawer-title')?.textContent, 'Quick check');
    assert.deepEqual([...host.querySelectorAll('.ap-speed option')].map((o) => /** @type {HTMLOptionElement} */ (o).value), SPEEDS.map(String));
    const slider = /** @type {HTMLElement} */ (host.querySelector('[role="slider"]'));
    assert.equal(slider.getAttribute('aria-valuemax'), String(Math.round(timeline.total_duration)));
    assert.match(/** @type {string} */ (host.querySelector('.ap-time')?.textContent), /^0:00 \/ 1:31$/);
    assert.equal(sceneLabel({ ...timeline.scenes[0], title: '' }), 'Introduction');
  });

  test('play button and big play toggle playback; icons follow state', async () => {
    const s = await setup();
    made.push(s);
    const play = /** @type {HTMLButtonElement} */ (s.host.querySelector('.ap-controls-left .ap-btn'));
    assert.equal(play.getAttribute('aria-label'), 'Play (k)');
    play.click();
    assert.equal(s.player.paused, false);
    assert.equal(play.getAttribute('aria-label'), 'Pause (k)');
    assert.ok(s.host.classList.contains('is-playing'));
    play.click();
    assert.equal(s.player.paused, true);
    /** @type {HTMLButtonElement} */ (s.host.querySelector('.ap-bigplay')).click();
    assert.equal(s.player.paused, false);
  });

  test('keyboard shortcuts', async () => {
    const s = await setup();
    made.push(s);
    const { player } = s;
    player.seek(30);
    press(' ');
    assert.equal(player.paused, false);
    press('k');
    assert.equal(player.paused, true);
    press('ArrowRight');
    assert.equal(player.currentTime, 35);
    press('ArrowLeft');
    assert.equal(player.currentTime, 30);
    press('l');
    assert.equal(player.currentTime, 40);
    press('j');
    assert.equal(player.currentTime, 30);
    const before = player.sceneIndex;
    press('ArrowRight', { shiftKey: true });
    assert.equal(player.sceneIndex, before + 1);
    press('n');
    assert.equal(player.sceneIndex, before + 2);
    press('p');
    assert.equal(player.sceneIndex, before + 1);
    press('c');
    assert.equal(player.captionsOn, false);
    press('m');
    assert.equal(player.muted, true);
    press('ArrowDown');
    assert.ok(Math.abs(player.volume - 0.9) < 1e-9);
    const ev = press('x');
    assert.equal(ev.defaultPrevented, false, 'unhandled keys pass through');
    press('k', { ctrlKey: true });
    assert.equal(player.paused, true, 'modified keys are ignored');
  });

  test('typing in inputs and space on buttons are left alone', async () => {
    const s = await setup();
    made.push(s);
    const volume = /** @type {HTMLInputElement} */ (s.host.querySelector('.ap-volume'));
    press('k', {}, volume);
    assert.equal(s.player.paused, true);
    const btn = /** @type {HTMLButtonElement} */ (s.host.querySelector('.ap-controls-right .ap-btn'));
    press(' ', {}, btn);
    assert.equal(s.player.paused, true);
  });

  test('scrubber keyboard seeks and reports aria values', async () => {
    const s = await setup();
    made.push(s);
    const slider = /** @type {HTMLElement} */ (s.host.querySelector('[role="slider"]'));
    s.player.seek(20);
    press('ArrowRight', {}, slider);
    assert.equal(s.player.currentTime, 25);
    press('End', {}, slider);
    assert.equal(s.player.currentTime, s.timeline.total_duration);
    press('Home', {}, slider);
    assert.equal(s.player.currentTime, 0);
    assert.equal(slider.getAttribute('aria-valuenow'), '0');
    assert.match(/** @type {string} */ (slider.getAttribute('aria-valuetext')), /^0 seconds of 1 minute 31 seconds$/);
  });

  test('previous restarts the current scene after 3 s, else goes back', async () => {
    const s = await setup();
    made.push(s);
    const { player, timeline } = s;
    player.seek(timeline.scenes[3].start + 5);
    s.controls.stepScene(-1);
    assert.equal(player.currentTime, timeline.scenes[3].start);
    s.controls.stepScene(-1);
    assert.equal(player.sceneIndex, 2);
  });

  test('seeking back into the intro clears the scene label, drawer highlight and prev button', async () => {
    const s = await setup();
    made.push(s);
    const { player, timeline, controls } = s;
    player.seek(timeline.scenes[3].start + 1);
    assert.ok(controls.sceneTitle.textContent?.length);
    assert.ok(controls.sceneButtons[3].classList.contains('is-current'));
    assert.equal(controls.prevBtn.disabled, false);
    player.seek(2); // intro
    assert.equal(player.sceneIndex, -1);
    assert.equal(controls.sceneTitle.textContent, '');
    assert.equal(controls.sceneButtons.some((/** @type {HTMLElement} */ b) => b.classList.contains('is-current')), false);
    assert.equal(controls.sceneButtons.some((/** @type {HTMLElement} */ b) => b.hasAttribute('aria-current')), false);
    assert.equal(controls.prevBtn.disabled, true);
    assert.equal(controls.nextBtn.disabled, false);
  });

  test('drawer opens, jumps to a scene and closes with Escape', async () => {
    const s = await setup();
    made.push(s);
    const list = /** @type {HTMLButtonElement} */ (s.host.querySelector('.ap-controls-right .ap-btn:nth-of-type(2)') || s.controls.listBtn);
    s.controls.listBtn.click();
    assert.ok(s.host.classList.contains('drawer-open'));
    assert.equal(s.controls.listBtn.getAttribute('aria-expanded'), 'true');
    /** @type {HTMLButtonElement} */ (s.host.querySelectorAll('.ap-drawer-item')[4]).click();
    assert.equal(s.player.sceneIndex, 4);
    assert.ok(s.host.querySelectorAll('.ap-drawer-item')[4].classList.contains('is-current'));
    press('Escape');
    assert.equal(s.host.classList.contains('drawer-open'), false);
    assert.ok(list);
  });

  test('speed, captions and volume controls update the player and persist prefs', async () => {
    const s = await setup();
    made.push(s);
    s.controls.speed.value = '1.5';
    s.controls.speed.dispatchEvent(new window.Event('change'));
    assert.equal(s.player.rate, 1.5);
    s.controls.ccBtn.click();
    assert.equal(s.player.captionsOn, false);
    assert.equal(s.controls.ccBtn.getAttribute('aria-pressed'), 'false');
    s.controls.ccBtn.click();
    s.controls.sizeBtn.click();
    assert.equal(s.player.captions?.size, 'l');
    s.controls.volume.value = '0.3';
    s.controls.volume.dispatchEvent(new window.Event('input'));
    assert.ok(Math.abs(s.player.volume - 0.3) < 1e-9);
    const last = s.prefs.at(-1);
    assert.equal(last.rate, 1.5);
    assert.equal(last.captionSize, 'l');
    assert.ok(Math.abs(last.volume - 0.3) < 1e-9);
  });

  test('controls auto-hide while playing and wake on activity', async () => {
    const s = await setup();
    made.push(s);
    s.player.play();
    s.controls.wake();
    await new Promise((r) => setTimeout(r, 30));
    assert.ok(s.host.classList.contains('is-idle'));
    s.host.dispatchEvent(new window.Event('pointermove'));
    assert.equal(s.host.classList.contains('is-idle'), false);
  });

  test('captionLift: stage pixels the captions rise above a visible control bar', () => {
    // stage scaled to 0.5; bar top 60 css px above the stage bottom -> 120 stage px covered
    assert.equal(captionLift(500, 440, 0.5), 120 - 40 + 14);
    assert.equal(captionLift(500, 499, 0.5), 0, 'a bar below the caption box needs no lift');
    assert.equal(captionLift(500, 440, 0), 0);
    assert.equal(captionLift(NaN, 440, 1), 0);
  });

  test('visible controls lift the captions; idle controls drop them; render players ignore it', async () => {
    const s = await setup();
    made.push(s);
    const el = /** @type {HTMLElement} */ (s.host.querySelector('.aadhi-player'));
    // jsdom has no layout: fake the stage and bar boxes
    const stageEl = /** @type {any} */ (s.player).stage.el;
    stageEl.getBoundingClientRect = () => ({ top: 0, bottom: 540, left: 0, right: 960, width: 960, height: 540 });
    /** @type {any} */ (s.controls.scrubber).getBoundingClientRect = () => ({ top: 480, bottom: 498, left: 0, right: 960, width: 960, height: 18 });
    /** @type {any} */ (s.player).stage.scale = 0.5;
    s.controls.wake();
    assert.equal(el.style.getPropertyValue('--caption-lift'), `${(540 - 480) / 0.5 - 40 + 14}px`);
    s.player.play();
    s.controls.wake();
    await new Promise((r) => setTimeout(r, 30));
    assert.ok(s.host.classList.contains('is-idle'));
    assert.equal(el.style.getPropertyValue('--caption-lift'), '0px');
    const renderHost = document.createElement('div');
    const rp = new Player(renderHost, { mode: 'render' });
    rp.setCaptionLift(200);
    assert.equal(rp.el.style.getPropertyValue('--caption-lift'), '');
    rp.destroy();
  });

  test('destroy removes the UI and listeners', async () => {
    const s = await setup();
    s.controls.destroy();
    assert.equal(s.host.querySelector('.ap-controls'), null);
    assert.equal(s.host.querySelector('.ap-drawer'), null);
    press('k');
    assert.equal(s.player.paused, true, 'keyboard listener removed');
    s.player.destroy();
  });
});
