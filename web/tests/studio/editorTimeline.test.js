// Editor timeline strip (views/editor/timelineStrip.js): lanes from a preview Timeline, seeking, selecting,
// moving scenes, zoom, keyboard, the scene-list fallback, and the shortcuts dialog component.
import { resetDom, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createTimelineStrip, stripModel, fallbackModel, tickStep, sceneAt, DEFAULT_PPS, TICK_STEPS } from '../../js/studio/views/editor/timelineStrip.js';
import { openShortcutsDialog } from '../../js/studio/components/shortcutsDialog.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { estimateSceneSeconds } from '../../js/studio/views/editor/sceneList.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { sampleScreenplay } from './fixtures.js';

/** A small preview Timeline for the sample screenplay with s3 skipped: intro 0-4 s, s1 4-10, s2 10-30, s4 30-45. */
function timeline() {
  return {
    total_duration: 45,
    estimated: true,
    intro: { duration: 4, cards: [] },
    chapters: [{ start: 4, title: 'Foundations' }, { start: 30, title: 'Practice' }],
    captions: [{ start: 4.2, end: 6, text: 'Welcome to the session.' }, { start: 10.1, end: 14, text: 'Voltage pushes charge.' }],
    scenes: [
      { scene_id: 's1', index: 0, type: 'title', start: 4, duration: 6, beats: [{ beat_id: 's1-b1', index: 0, start: 0.2, speech_end: 2, end: 2, narration: 'Welcome' }] },
      {
        scene_id: 's2',
        index: 1,
        type: 'content',
        start: 10,
        duration: 20,
        side_panel: { panel: { kind: 'skill_tree' }, show_at: 5 },
        beats: [
          { beat_id: 's2-b1', index: 0, start: 0.1, speech_end: 4, end: 4, narration: 'a', board_item_id: 's2-i1' },
          { beat_id: 's2-b2', index: 1, start: 4, speech_end: 9, end: 9.5, narration: 'b', board_item_id: 's2-i2', estimated: true },
        ],
      },
      { scene_id: 's4', index: 2, type: 'quiz_checkpoint', start: 30, duration: 15, beats: [] },
    ],
  };
}

function skippedScreenplay() {
  return E.updateScene(sampleScreenplay(), 's3', (s) => {
    s.hidden = true;
  });
}

/** px value of a style length. */
const px = (v) => Number(String(v).replace('px', ''));

function mountStrip(overrides = {}) {
  const calls = { seek: [], select: [], move: [], play: 0, step: [] };
  const strip = createTimelineStrip({
    onSeek: (t) => calls.seek.push(t),
    onSelectScene: (id) => calls.select.push(id),
    onMove: (id, before) => calls.move.push([id, before]),
    onTogglePlay: () => {
      calls.play += 1;
    },
    onStep: (d) => calls.step.push(d),
    ...overrides,
  });
  document.body.appendChild(strip.el);
  return { strip, calls };
}

function fresh() {
  resetDom();
  closeAllModals();
  localStorage.clear();
}

test('model: scenes, beats, visuals, captions, chapters, intro and skipped scenes from the preview timeline', () => {
  const m = stripModel(timeline(), skippedScreenplay());
  assert.equal(m.total, 45);
  assert.deepEqual(m.intro, { start: 0, end: 4 });
  assert.deepEqual(m.scenes.map((s) => [s.id, s.n, s.start, s.end]), [['s1', 1, 4, 10], ['s2', 2, 10, 30], ['s4', 4, 30, 45]], 'numbers follow the lecture, not the timeline');
  assert.equal(m.scenes[1].title, 'The law');
  assert.equal(m.scenes[1].estimated, true);
  assert.equal(m.scenes[0].estimated, false);
  assert.deepEqual(m.beats.map((b) => [b.sceneId, b.k, b.start, b.end, b.estimated]), [['s1', 1, 4.2, 6, false], ['s2', 1, 10.1, 14, false], ['s2', 2, 14, 19.5, true]]);
  assert.deepEqual(m.visuals, [{ sceneId: 's2', start: 15, end: 30, label: 'Concept map' }]);
  assert.deepEqual(m.reveals.map((r) => r.at), [10.1, 14]);
  assert.equal(m.captions.length, 2);
  assert.deepEqual(m.chapters.map((c) => c.title), ['Foundations', 'Practice']);
  assert.deepEqual(m.hidden, [{ id: 's3', n: 3, title: 'Worked example', at: 30 }], 'a skipped scene is marked where it would have played');
  assert.equal(m.estimated, true);
  assert.equal(m.fallback, false);
});

test('model: without a timeline the scene lane comes from the scene list estimates (skipped scenes left out)', () => {
  const sp = skippedScreenplay();
  const m = fallbackModel(sp);
  assert.equal(m.fallback, true);
  assert.deepEqual(m.scenes.map((s) => s.id), ['s1', 's2', 's4']);
  const d2 = Math.max(1, estimateSceneSeconds(E.findScene(sp, 's2')));
  assert.ok(Math.abs(m.scenes[1].end - m.scenes[1].start - d2) < 1e-9);
  assert.equal(m.hidden[0].id, 's3');
  assert.equal(sceneAt(m, m.scenes[1].start + 0.1).id, 's2');
  assert.equal(sceneAt(m, 0).id, 's1');
  assert.equal(tickStep(DEFAULT_PPS), 15, 'ticks about 70 px apart');
  assert.equal(tickStep(100), 1);
  assert.equal(tickStep(0.01), TICK_STEPS[TICK_STEPS.length - 1]);
});

test('strip: blocks as wide as the scene plays; a click selects; the ruler seeks; the playhead moves', () => {
  fresh();
  const { strip, calls } = mountStrip();
  strip.setTimeline(timeline(), skippedScreenplay());
  const blocks = [...strip.el.querySelectorAll('.tl-block')];
  assert.deepEqual(blocks.map((b) => b.dataset.sceneId), ['s1', 's2', 's4']);
  const w = blocks.map((b) => px(b.style.width) + 1);
  assert.ok(Math.abs(w[1] / w[0] - 20 / 6) < 0.05, 'width follows the duration');
  assert.equal(px(blocks[1].style.left), 10 * DEFAULT_PPS);
  assert.match(blocks[1].getAttribute('aria-label'), /^Scene 2: The law, 0:20 \(estimated\)$/);
  assert.ok(blocks[1].classList.contains('is-estimated'), 'estimated scenes are hatched');
  assert.equal(strip.el.querySelectorAll('.tl-beat.is-estimated').length, 1);
  assert.equal(strip.el.querySelectorAll('.tl-cue').length, 2);
  assert.equal(strip.el.querySelectorAll('.tl-chapter').length, 2);
  assert.ok(strip.el.querySelector('.tl-intro'));
  const hidden = strip.el.querySelector('.tl-hidden');
  assert.equal(hidden.dataset.sceneId, 's3');
  assert.match(hidden.getAttribute('aria-label'), /Scene 3 \(skipped in the video\)/);
  hidden.click();
  blocks[2].click();
  assert.deepEqual(calls.select, ['s3', 's4']);
  // press on the ruler at 12 s, drag to 20 s
  const ruler = strip.el.querySelector('.tl-ruler');
  ruler.dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true, clientX: 12 * DEFAULT_PPS, button: 0 }));
  window.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 20 * DEFAULT_PPS }));
  window.dispatchEvent(new window.MouseEvent('pointerup', {}));
  window.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 30 * DEFAULT_PPS }));
  assert.deepEqual(calls.seek, [12, 20], 'the drag ends with the pointer');
  // the narration lane seeks too
  strip.el.querySelector('.tl-narration').dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true, clientX: 5 * DEFAULT_PPS, button: 0 }));
  window.dispatchEvent(new window.MouseEvent('pointerup', {}));
  assert.deepEqual(calls.seek, [12, 20, 5]);
  strip.setTime(25, false);
  assert.equal(px(strip.el.querySelector('.tl-playhead').style.left), 25 * DEFAULT_PPS);
  assert.equal(strip.el.querySelector('.tl-time').textContent, '0:25 / 0:45');
  assert.equal(ruler.getAttribute('aria-valuenow'), '25');
  // selection and playing marks change without a redraw
  strip.setSelected('s2');
  assert.equal(strip.el.querySelector('.tl-block[data-scene-id="s2"]').getAttribute('aria-current'), 'true');
  assert.equal(strip.el.querySelector('.tl-block[data-scene-id="s2"]'), blocks[1], 'same element');
  strip.setPlayingScene('s4');
  assert.ok(blocks[2].classList.contains('is-playing'));
  strip.setPlaying(true);
  assert.equal(strip.el.querySelector('[aria-pressed]').getAttribute('aria-pressed'), 'true');
  strip.destroy();
});

test('strip: keyboard (ruler slider, scene blocks as one tab stop), transport buttons and zoom (remembered)', () => {
  fresh();
  const { strip, calls } = mountStrip();
  strip.setTimeline(timeline(), sampleScreenplay());
  strip.setSelected('s1');
  const ruler = strip.el.querySelector('.tl-ruler');
  strip.setTime(10, false);
  ruler.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  ruler.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', shiftKey: true, bubbles: true }));
  ruler.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
  ruler.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  assert.deepEqual(calls.seek, [11, 21, 0, 45]);
  const blocks = () => [...strip.el.querySelectorAll('.tl-block')];
  assert.deepEqual(blocks().map((b) => b.tabIndex), [0, -1, -1], 'only the selected block is a tab stop');
  blocks()[0].focus();
  blocks()[0].dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(document.activeElement, blocks()[1]);
  assert.deepEqual(blocks().map((b) => b.tabIndex), [-1, 0, -1]);
  blocks()[1].dispatchEvent(new window.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  assert.equal(document.activeElement, blocks()[2]);
  const byLabel = (l) => [...strip.el.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === l || b.textContent.trim() === l);
  byLabel('Previous scene').click();
  byLabel('Next scene').click();
  byLabel('Play').click();
  assert.deepEqual(calls.step, [-1, 1]);
  assert.equal(calls.play, 1);
  const before = px(blocks()[1].style.width) + 1;
  byLabel('Zoom in').click();
  const after = px(blocks()[1].style.width) + 1;
  assert.ok(Math.abs(after / before - 1.5) < 0.05, 'zoomed in');
  assert.equal(localStorage.getItem('aadhi.studio.pref.timelineZoom'), '1.5');
  byLabel('Fit the whole lecture in the timeline').click();
  assert.equal(px(blocks()[1].style.width) + 1, before);
  byLabel('Zoom out').click();
  assert.equal(px(blocks()[1].style.width) + 1, before, 'never smaller than the whole lecture');
  strip.destroy();
  // a new strip starts at the remembered zoom
  localStorage.setItem('aadhi.studio.pref.timelineZoom', '2');
  const again = mountStrip().strip;
  again.setTimeline(timeline(), sampleScreenplay());
  assert.equal(px(again.el.querySelector('.tl-block[data-scene-id="s2"]').style.width) + 1, before * 2);
  again.destroy();
});

test('strip: dragging a scene block moves the scene (a short press stays a click)', () => {
  fresh();
  const { strip, calls } = mountStrip();
  strip.setTimeline(timeline(), sampleScreenplay());
  const s1 = strip.el.querySelector('.tl-block[data-scene-id="s1"]');
  // drag s1 (4-10 s) past the middle of s2 (10-30 s) and of s4 (30-45 s): after the last scene
  s1.dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true, clientX: 5 * DEFAULT_PPS, button: 0 }));
  window.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 44 * DEFAULT_PPS }));
  assert.equal(strip.el.querySelector('.tl-drop').hidden, false, 'the drop place is shown');
  window.dispatchEvent(new window.MouseEvent('pointerup', {}));
  assert.deepEqual(calls.move, [['s1', null]]);
  s1.click(); // the click that ends a drag is not a selection
  assert.deepEqual(calls.select, []);
  // s4 dragged before the middle of s2: before s2
  const s4 = strip.el.querySelector('.tl-block[data-scene-id="s4"]');
  s4.dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true, clientX: 40 * DEFAULT_PPS, button: 0 }));
  window.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 12 * DEFAULT_PPS }));
  window.dispatchEvent(new window.MouseEvent('pointerup', {}));
  assert.deepEqual(calls.move[1], ['s4', 's2']);
  // a press that moves less than the threshold changes nothing
  s4.dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true, clientX: 40 * DEFAULT_PPS, button: 0 }));
  window.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 40 * DEFAULT_PPS + 2 }));
  window.dispatchEvent(new window.MouseEvent('pointerup', {}));
  assert.equal(calls.move.length, 2);
  // locked (a job writes the version): no moves
  strip.setLocked(true);
  s4.dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true, clientX: 40 * DEFAULT_PPS, button: 0 }));
  window.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 5 * DEFAULT_PPS }));
  window.dispatchEvent(new window.MouseEvent('pointerup', {}));
  assert.equal(calls.move.length, 2);
  assert.match(strip.el.querySelector('.tl-note').textContent, /waits until the running job finishes/);
  strip.destroy();
});

test('strip: without a preview the estimates show, a time selects its scene and there is no playhead', () => {
  fresh();
  const { strip, calls } = mountStrip();
  const sp = sampleScreenplay();
  strip.setTimeline(null, sp);
  assert.ok(strip.el.classList.contains('is-fallback'));
  assert.equal(strip.el.querySelectorAll('.tl-block').length, 4);
  assert.equal(strip.el.querySelector('.tl-playhead').hidden, true);
  assert.match(strip.el.querySelector('.tl-note').textContent, /Estimated from the scene list/);
  const m = strip.model();
  const t = (m.scenes[2].start + m.scenes[2].end) / 2;
  strip.el.querySelector('.tl-ruler').dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true, clientX: t * DEFAULT_PPS, button: 0 }));
  assert.deepEqual(calls.select, ['s3']);
  assert.deepEqual(calls.seek, []);
  // every scene skipped: nothing plays
  const none = { total_duration: 0, scenes: [] };
  strip.setTimeline(none, sp);
  assert.match(strip.el.querySelector('.tl-note').textContent, /No scene plays/);
  strip.destroy();
});

test('strip: without a preview the ruler slider still moves through the lecture by keyboard (no seek, no playhead)', () => {
  fresh();
  const { strip, calls } = mountStrip();
  strip.setTimeline(null, sampleScreenplay());
  const m = strip.model();
  const ruler = strip.el.querySelector('.tl-ruler');
  const key = (k, shiftKey = false) => ruler.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, shiftKey, bubbles: true, cancelable: true }));
  const now = [];
  for (let i = 0; i < 4; i++) {
    key('PageUp');
    now.push(ruler.getAttribute('aria-valuenow'));
  }
  key('ArrowRight', true);
  now.push(ruler.getAttribute('aria-valuenow'));
  const expectedTimes = [10, 20, 30, 40, 50].map((t) => Math.min(t, m.total));
  assert.deepEqual(now, expectedTimes.map((t) => String(Math.floor(t))), 'the slider value follows the keys, up to its maximum');
  assert.ok(Number(now.at(-1)) <= Number(ruler.getAttribute('aria-valuemax')), 'clamped at the end of the lecture');
  assert.deepEqual(calls.select, expectedTimes.map((t) => sceneAt(m, t).id), 'each step selects the scene at that time');
  assert.ok(new Set(calls.select).size > 1, 'it moves past the first scene');
  assert.deepEqual(calls.seek, []);
  assert.equal(strip.el.querySelector('.tl-playhead').hidden, true);
  // a player left on screen keeps the clock of another timeline: ignored while the strip shows estimates
  strip.setTime(1, false);
  assert.equal(ruler.getAttribute('aria-valuenow'), String(Math.floor(expectedTimes.at(-1))));
  strip.destroy();
});

test('shortcuts dialog: groups of keys as a definition list; opening it twice keeps one dialog', async () => {
  fresh();
  const groups = [{ title: 'Editor', keys: [['Ctrl + S', 'Save'], ['Ctrl + Shift + Z / Ctrl + Y', 'Redo']] }];
  const m = openShortcutsDialog(groups);
  assert.equal(openShortcutsDialog(groups), m);
  assert.equal(document.querySelectorAll('.modal').length, 1);
  const dialog = document.querySelector('.modal');
  assert.match(dialog.textContent, /Keyboard shortcuts/);
  assert.deepEqual([...dialog.querySelectorAll('dt')][1].textContent, 'Ctrl + Shift + Z or Ctrl + Y');
  assert.equal(dialog.querySelectorAll('dt kbd').length, 7, 'Ctrl, S; Ctrl, Shift, Z; Ctrl, Y');
  assert.deepEqual([...dialog.querySelectorAll('dd')].map((d) => d.textContent), ['Save', 'Redo']);
  m.close();
  await m.result;
  assert.notEqual(openShortcutsDialog(groups), m, 'a closed dialog opens anew');
  closeAllModals();
});
