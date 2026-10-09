// @ts-check
/**
 * The editor preview's hooks for the timeline strip (web/js/studio/views/editor/previewPane.js) with the real
 * player: the loaded timeline (null when the preview fails), the playhead once per frame, play / pause,
 * seeking by time and by scene id (positions differ from the draft when scenes are skipped), and the shown
 * scene kept by id across refreshes. The strip's model reads the same fixture timeline.
 */
import { test, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from './_dom.js';
import { loadTimeline } from './_fixture.js';

installDom();
const { createPreviewPane } = await import('../../js/studio/views/editor/previewPane.js');
const { stripModel } = await import('../../js/studio/views/editor/timelineStrip.js');

after(() => uninstallDom());

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  document.body.replaceChildren();
});

/** @param {any} body @param {number} [status] */
function serve(body, status = 200) {
  globalThis.fetch = /** @type {any} */ (async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
}

/** @param {() => any} cond */
async function until(cond, ms = 2000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 5));
  }
}

function mountPane() {
  /** @type {{ timelines: any[], times: number[], plays: boolean[], scenes: Array<[number, string | null]> }} */
  const rec = { timelines: [], times: [], plays: [], scenes: [] };
  const pane = createPreviewPane({
    versionId: 9,
    getSource: () => ({ mode: 'built', screenplay: null, blockedReason: null }),
    onTimeline: (t) => rec.timelines.push(t),
    onTimeUpdate: (t) => rec.times.push(t),
    onPlayState: (p) => rec.plays.push(p),
    onSceneChange: (i, id) => rec.scenes.push([i, id]),
  });
  document.body.append(pane.el);
  return { pane, rec };
}

test('the preview hands its timeline, playhead and play state to the strip, and seeks by time or scene id', async () => {
  const timeline = loadTimeline();
  serve(timeline);
  const { pane, rec } = mountPane();
  await pane.refresh();
  assert.equal(rec.timelines.length, 1);
  assert.equal(rec.timelines[0].scenes.length, timeline.scenes.length);
  assert.deepEqual(rec.plays, [false]);
  assert.equal(pane.isPlaying(), false);
  assert.equal(pane.seekSceneId('s-example'), true);
  assert.deepEqual(rec.scenes[rec.scenes.length - 1], [3, 's-example'], 'scene changes carry the scene id');
  assert.equal(pane.sceneId(), 's-example');
  assert.equal(pane.seekSceneId('not-in-the-video'), false, 'a skipped scene is not in the preview');
  pane.seek(62);
  await until(() => rec.times.includes(62));
  assert.deepEqual(rec.scenes[rec.scenes.length - 1], [4, 's-sim']);
  pane.togglePlay();
  assert.equal(pane.isPlaying(), true);
  assert.equal(rec.plays[rec.plays.length - 1], true);
  pane.togglePlay();
  assert.equal(pane.isPlaying(), false);
  assert.equal(rec.plays[rec.plays.length - 1], false);
  pane.destroy();
});

test('the shown scene is kept by id when a refresh changes positions; a failed refresh hands null', async () => {
  const timeline = loadTimeline();
  serve(timeline);
  const { pane, rec } = mountPane();
  await pane.refresh();
  pane.seekSceneId('s-broll');
  // the next preview leaves out two scenes before it (skipped): it is now at position 3
  const fewer = loadTimeline();
  fewer.scenes = fewer.scenes.filter((/** @type {any} */ s) => s.scene_id !== 's-chapter' && s.scene_id !== 's-sim');
  serve(fewer);
  await pane.refresh();
  assert.deepEqual(rec.scenes[rec.scenes.length - 1], [3, 's-broll']);
  serve({ detail: 'boom', code: 'internal' }, 500);
  await pane.refresh();
  assert.equal(rec.timelines[rec.timelines.length - 1], null);
  assert.match(String(pane.el.querySelector('.preview-status')?.textContent), /Preview unavailable/);
  pane.destroy();
});

test('the strip model reads the real timeline: scene spans, beats and an intro before the first scene', () => {
  const timeline = loadTimeline();
  const m = stripModel(timeline, null);
  assert.equal(m.total, timeline.total_duration);
  assert.deepEqual(m.intro, { start: 0, end: timeline.scenes[0].start });
  assert.equal(m.scenes.length, timeline.scenes.length);
  for (const [i, s] of m.scenes.entries()) {
    assert.equal(s.start, timeline.scenes[i].start);
    assert.ok(Math.abs(s.end - (timeline.scenes[i].start + timeline.scenes[i].duration)) < 1e-9);
  }
  for (const b of m.beats) {
    const s = /** @type {any} */ (m.scenes.find((x) => x.id === b.sceneId));
    assert.ok(b.start >= s.start - 1e-9 && b.end <= s.end + 1e-9, 'beats lie inside their scene');
  }
  assert.ok(m.visuals.some((v) => v.label === 'Animation'), 'a simulation shows as a full-scene visual');
});
