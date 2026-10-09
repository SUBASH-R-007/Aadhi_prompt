// @ts-check
import { test, describe, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { CaptionsView, selectCue } = await import('../../js/player/captions.js');
const { sceneStateAt } = await import('../../js/player/schedule.js');

after(() => uninstallDom());

describe('caption selection', () => {
  const timeline = loadTimeline();
  const ohm = sceneById(timeline, 's-ohm');

  test('scene state captions come from the beat cues (scene-relative)', () => {
    assert.equal(sceneStateAt(ohm, 0.2).caption, null);
    assert.equal(sceneStateAt(ohm, 1).caption, 'Resistance opposes the flow of current.');
    assert.equal(sceneStateAt(ohm, 3.5).caption, "Ohm's law links voltage, current and resistance.");
    assert.equal(sceneStateAt(ohm, 6.5).caption, null, 'no caption during the pause');
  });

  test('timeline captions (absolute) agree with the scene cues', () => {
    for (const cue of timeline.captions || []) {
      const mid = (cue.start + cue.end) / 2;
      assert.equal(selectCue(timeline.captions, mid)?.text, cue.text);
      const scene = timeline.scenes.find((s) => s.start <= mid && mid < s.start + s.duration);
      assert.ok(scene);
      assert.equal(sceneStateAt(/** @type {any} */ (scene), mid - /** @type {any} */ (scene).start).caption, cue.text);
    }
  });
});

describe('CaptionsView', () => {
  test('shows text with textContent and hides when empty', () => {
    const host = document.createElement('div');
    const view = new CaptionsView(host);
    assert.equal(view.el.classList.contains('has-text'), false);
    view.show('<b>Hello</b>\nworld');
    assert.equal(view.text.textContent, '<b>Hello</b>\nworld');
    assert.equal(view.el.querySelector('b'), null);
    assert.ok(view.el.classList.contains('has-text'));
    view.show(null);
    assert.equal(view.el.classList.contains('has-text'), false);
    assert.equal(view.text.textContent, '');
  });

  test('toggle and size', () => {
    const host = document.createElement('div');
    const view = new CaptionsView(host, { enabled: false, size: 'l' });
    assert.ok(view.el.classList.contains('is-off'));
    assert.equal(view.el.dataset.size, 'l');
    view.setEnabled(true);
    assert.equal(view.el.classList.contains('is-off'), false);
    view.setSize(/** @type {any} */ ('xxl'));
    assert.equal(view.size, 'm', 'unknown sizes fall back to medium');
    view.destroy();
    assert.equal(host.children.length, 0);
  });
});
