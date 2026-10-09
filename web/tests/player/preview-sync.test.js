// @ts-check
/**
 * The Studio editor preview (web/js/studio/views/editor/previewPane.js) shows, below the real player, the
 * shown scene's timing in words: when each beat starts and the moments placed on spoken words
 * (syncSummary over TimedScene.sync_cues), marked "estimated" for previews timed from an estimate.
 */
import { test, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { createPreviewPane } = await import('../../js/studio/views/editor/previewPane.js');

after(() => uninstallDom());

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  document.body.replaceChildren();
});

/** @param {any} timeline */
function serve(timeline) {
  globalThis.fetch = /** @type {any} */ (
    async () => new Response(JSON.stringify(timeline), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
}

/** @param {any} timeline @param {number} index */
async function mountPane(timeline, index) {
  serve(timeline);
  const pane = createPreviewPane({ versionId: 9, getSource: () => ({ mode: 'built', screenplay: null, blockedReason: null }) });
  document.body.append(pane.el);
  pane.seekScene(index);
  await pane.refresh();
  return pane;
}

test('the preview lists when each beat of the shown scene starts and its word-anchored moments', async () => {
  const timeline = /** @type {any} */ (loadTimeline());
  const index = timeline.scenes.findIndex((/** @type {any} */ s) => s.scene_id === 's-ohm');
  const ohm = sceneById(timeline, 's-ohm');
  const formula = /** @type {any} */ (ohm.board.find((i) => i.kind === 'formula'));
  /** @type {any} */ (ohm).sync_cues = [{ kind: 'var', start: 4.2, item_id: formula.id, part: 'var:0', beat_id: ohm.beats[2].beat_id, words: 'voltage' }];
  const pane = await mountPane(timeline, index);
  const box = /** @type {HTMLDetailsElement} */ (pane.el.querySelector('.preview-sync'));
  assert.equal(box.hidden, false);
  assert.match(String(box.querySelector('summary')?.textContent), new RegExp(`^Timing of scene ${index + 1}`));
  assert.equal(box.querySelector('.badge'), null, 'built from the narration audio: no "estimated" mark');
  const beats = [...box.querySelectorAll('[data-sync="beats"] li')].map((li) => li.textContent);
  assert.equal(beats.length, ohm.beats.length);
  assert.equal(beats[0], `Beat 1: ${(Math.round(ohm.beats[0].start * 10) / 10).toFixed(1)} s`);
  const moments = [...box.querySelectorAll('[data-sync="moments"] li')].map((li) => li.textContent);
  assert.equal(moments.length, 1);
  assert.match(String(moments[0]), /^4\.2 s: “.+” appears in the formula legend, when the narration says “voltage”$/);
  pane.destroy();
  assert.equal(box.hidden, true, 'nothing is listed once the preview is gone');
});

test('an estimated preview is marked, and a scene without anchored moments says when items appear', async () => {
  const timeline = /** @type {any} */ (loadTimeline());
  const index = timeline.scenes.findIndex((/** @type {any} */ s) => s.scene_id === 's-example');
  for (const b of sceneById(timeline, 's-example').beats) /** @type {any} */ (b).estimated = true;
  timeline.estimated = true;
  const pane = await mountPane(timeline, index);
  const box = /** @type {HTMLDetailsElement} */ (pane.el.querySelector('.preview-sync'));
  assert.equal(box.querySelector('.badge')?.textContent, 'estimated');
  assert.match(String(box.textContent), /final times follow the voice/);
  assert.match(String(box.textContent), /No moments placed on spoken words: board items appear when their beat starts\./);
  pane.destroy();
});

test('the timing summary numbers the scene as the editor does when told how (skipped scenes counted)', async () => {
  const timeline = /** @type {any} */ (loadTimeline());
  const index = timeline.scenes.findIndex((/** @type {any} */ s) => s.scene_id === 's-ohm');
  serve(timeline);
  const asked = /** @type {string[]} */ ([]);
  const pane = createPreviewPane({
    versionId: 9,
    getSource: () => ({ mode: 'built', screenplay: null, blockedReason: null }),
    // the editor's draft has a skipped scene before it: its number is one more than its place in the timeline
    sceneNumber: (id) => {
      asked.push(id);
      return id === 's-ohm' ? index + 2 : 0;
    },
  });
  document.body.append(pane.el);
  pane.seekScene(index);
  await pane.refresh();
  const box = /** @type {HTMLDetailsElement} */ (pane.el.querySelector('.preview-sync'));
  assert.match(String(box.querySelector('summary')?.textContent), new RegExp(`^Timing of scene ${index + 2}`));
  assert.ok(asked.includes('s-ohm'));
  pane.destroy();
});
