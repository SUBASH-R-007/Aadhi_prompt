// @ts-check
/**
 * Word-anchored sync cues (TimedScene.sync_cues, aadhi/compose/sync.py) in the schedule, the board,
 * the side panels and the Studio summary. The render page uses the same schedule, so what these tests
 * pin for the live player holds for the MP4 too.
 */
import { test, describe, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, fakeTex } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const {
  sceneStateAt,
  stateTimes,
  stateKey,
  renderStateList,
  terminalLineTimes,
  syncPlan,
  planScene,
} = await import('../../js/player/schedule.js');
const { BoardView } = await import('../../js/player/board.js');
const timing = await import('../../js/player/panels/timing.js');
const { createPanel } = await import('../../js/player/panels/index.js');
const { syncSummary, MAX_KEY_MOMENTS } = await import('../../js/player/syncSummary.js');

const timeline = loadTimeline();
const ohm = sceneById(timeline, 's-ohm'); // formula i-formula revealed by b2 at 3.15; terminal panel from 3.15
const example = sceneById(timeline, 's-example');

/**
 * A copy of a fixture scene with sync cues (fresh object: schedule plans are cached per scene).
 * @param {any} scene
 * @param {any[]} cues
 * @returns {any}
 */
function withCues(scene, cues) {
  return { ...JSON.parse(JSON.stringify(scene)), sync_cues: cues };
}

const VAR_CUES = [
  { kind: 'var', start: 4.0, item_id: 'i-formula', part: 'var:0', beat_id: 's-ohm-b2', words: 'voltage' },
  { kind: 'var', start: 4.6, item_id: 'i-formula', part: 'var:1', beat_id: 's-ohm-b2', words: 'current' },
];

/** Full output key (visual key + phase, beat and caption), to check stateTimes completeness. */
const fullKey = (/** @type {any} */ scene, /** @type {number} */ t) => {
  const s = sceneStateAt(scene, t);
  return `${stateKey(scene, s)}|${s.phase}|${s.beatIndex}|${s.caption}|${s.countdownRemaining}|${s.terminalLines.length}`;
};

/** @param {any} scene */
function assertComplete(scene) {
  const times = stateTimes(scene);
  for (let t = 0; t < scene.duration; t += 0.01) {
    let k = 0;
    while (k + 1 < times.length && times[k + 1] <= t) k++;
    assert.equal(fullKey(scene, t), fullKey(scene, times[k]), `${scene.scene_id} t=${t.toFixed(2)}`);
  }
}

describe('schedule: scenes without cues are unchanged', () => {
  test('an empty or missing cue list gives identical states, times and render keys', () => {
    for (const scene of timeline.scenes) {
      const a = JSON.parse(JSON.stringify(scene));
      const b = withCues(scene, []);
      assert.deepEqual(stateTimes(b), stateTimes(a), scene.scene_id);
      assert.deepEqual(renderStateList(b), renderStateList(a), scene.scene_id);
      const s = sceneStateAt(a, scene.duration / 2);
      assert.equal(s.pendingParts?.size, 0);
      assert.equal(s.emphasisParts?.size, 0);
      assert.equal(s.panelFocus, false);
    }
  });

  test('malformed cues are ignored', () => {
    const scene = withCues(ohm, [
      { kind: 'var', start: -1, item_id: 'i-formula', part: 'var:0' },
      { kind: 'var', start: 'x', item_id: 'i-formula', part: 'var:1' },
      { kind: 'emphasis', start: 5, end: 4, item_id: 'i-def' },
      { kind: 'emphasis', start: 5, end: 6 },
      { kind: 'bogus', start: 5 },
      null,
    ]);
    const plan = syncPlan(scene);
    assert.equal(plan.partAt.size, 0);
    assert.equal(plan.emphasis.length, 0);
    assert.deepEqual(stateTimes(scene), stateTimes(JSON.parse(JSON.stringify(ohm))));
  });
});

describe('schedule: formula legend rows', () => {
  const scene = withCues(ohm, VAR_CUES);

  test('a row is pending from the formula reveal until its word, then shown', () => {
    assert.equal(sceneStateAt(scene, 3.0).pendingParts?.size, 0, 'formula not visible yet: nothing pending');
    assert.deepEqual([...(sceneStateAt(scene, 3.15).pendingParts || [])].sort(), ['i-formula|var:0', 'i-formula|var:1']);
    assert.deepEqual([...(sceneStateAt(scene, 4.0).pendingParts || [])], ['i-formula|var:1']);
    assert.equal(sceneStateAt(scene, 4.6).pendingParts?.size, 0);
    assert.equal(sceneStateAt(scene, 19).pendingParts?.size, 0);
  });

  test('cue times are state times, the schedule stays complete and the render list gains one state per row', () => {
    const times = stateTimes(scene);
    assert.ok(times.includes(4.0) && times.includes(4.6));
    assertComplete(scene);
    const plain = renderStateList(JSON.parse(JSON.stringify(ohm)));
    const synced = renderStateList(scene);
    assert.equal(synced.length, plain.length + 2);
    assert.notEqual(stateKey(scene, sceneStateAt(scene, 3.5)), stateKey(scene, sceneStateAt(scene, 4.2)));
  });
});

describe('schedule: word-anchored emphasis', () => {
  // b3 (7.15-9) highlights i-formula; the cue moves its start to the spoken word
  const scene = withCues(ohm, [
    { kind: 'emphasis', start: 7.6, end: 9, item_id: 'i-formula', part: null, beat_id: 's-ohm-b3', words: 'formula' },
    { kind: 'emphasis', start: 13.5, end: 15, item_id: 'i-def', part: 'term', beat_id: 's-ohm-b6', words: 'resistance' },
  ]);

  test('the authored highlight waits for its word; other highlights keep their beat timing', () => {
    assert.equal(sceneStateAt(scene, 7.15).highlightItemIds.has('i-formula'), false);
    assert.equal(sceneStateAt(scene, 7.6).highlightItemIds.has('i-formula'), true);
    assert.equal(sceneStateAt(scene, 9).highlightItemIds.has('i-formula'), false);
    // b6 highlights i-def (anchored, with its term) and i-formula (not anchored: beat-long)
    const atStart = sceneStateAt(scene, 13.15);
    assert.equal(atStart.highlightItemIds.has('i-formula'), true);
    assert.equal(atStart.highlightItemIds.has('i-def'), false);
    const later = sceneStateAt(scene, 13.6);
    assert.equal(later.highlightItemIds.has('i-def'), true);
    assert.deepEqual([...(later.emphasisParts || [])], ['i-def|term']);
    assertComplete(scene);
  });

  test('table columns: one at a time, keyed into the render state', () => {
    const tableId = (example.board || []).find((i) => i.kind === 'table')?.id;
    assert.ok(tableId);
    const s = withCues(example, [
      { kind: 'emphasis', start: 1.0, end: 1.6, item_id: tableId, part: 'column:0' },
      { kind: 'emphasis', start: 1.6, end: 2.2, item_id: tableId, part: 'column:1' },
    ]);
    const vis = sceneStateAt(s, 1.2).visibleItemIds.has(tableId);
    const a = sceneStateAt(s, 1.2);
    const b = sceneStateAt(s, 1.8);
    if (vis) {
      assert.deepEqual([...(a.emphasisParts || [])], [`${tableId}|column:0`]);
      assert.deepEqual([...(b.emphasisParts || [])], [`${tableId}|column:1`]);
      assert.notEqual(stateKey(s, a), stateKey(s, b));
    } else {
      assert.equal(a.emphasisParts?.size, 0, 'an item that is not visible is never emphasised');
    }
    assertComplete(s);
  });
});

describe('schedule: side panel focus and terminal output', () => {
  test('focus only while the panel is visible, inside its window', () => {
    const scene = withCues(ohm, [
      { kind: 'focus', start: 1.0, end: 2.6 }, // before the panel shows (3.15): ignored
      { kind: 'focus', start: 5.0, end: 6.6, words: 'look at this' },
    ]);
    assert.equal(sceneStateAt(scene, 1.5).panelFocus, false);
    assert.equal(sceneStateAt(scene, 4.99).panelFocus, false);
    assert.equal(sceneStateAt(scene, 5.0).panelFocus, true);
    assert.equal(sceneStateAt(scene, 6.6).panelFocus, false);
    assert.notEqual(stateKey(scene, sceneStateAt(scene, 5.5)), stateKey(scene, sceneStateAt(scene, 4.5)));
    assertComplete(scene);
  });

  test('an output cue starts the terminal lines at the spoken word', () => {
    const scene = withCues(ohm, [{ kind: 'output', start: 10.0, beat_id: 's-ohm-b4', words: 'prints' }]);
    const times = planScene(scene).terminal?.times || [];
    const step = (scene.duration - 10) / 3;
    assert.deepEqual(times, [10, 10 + step, 10 + 2 * step]);
    assert.equal(sceneStateAt(scene, 9.99).terminalLines.length, 0);
    assert.deepEqual(sceneStateAt(scene, 10).terminalLines, ['V = 12 V']);
    assert.equal(sceneStateAt(scene, scene.duration - 0.001).terminalLines.length, 3);
    assertComplete(scene);
  });

  test('an output cue before the panel appears starts the lines with the panel', () => {
    assert.deepEqual(terminalLineTimes(2, 3, 9, 1), [3, 6]);
    assert.deepEqual(terminalLineTimes(0, 3, 9, 5), []);
  });

  test('schedule and panel timing agree with and without the cue (render states come from both)', () => {
    for (const outputAt of [null, 1.0, 2.5, 7.25]) {
      for (const n of [0, 1, 3, 40]) {
        assert.deepEqual(terminalLineTimes(n, 1.5, 20, outputAt), timing.terminalLineTimes(1.5, 20, n, outputAt));
      }
    }
    const scene = withCues(ohm, [{ kind: 'output', start: 10.0 }]);
    assert.equal(timing.terminalOutputAt(scene), 10);
    assert.equal(timing.terminalOutputAt(ohm), null);
    const panelTimes = timing.panelStateTimes(scene);
    for (const t of planScene(scene).terminal?.times || []) assert.ok(panelTimes.some((x) => Math.abs(x - t) < 1e-9), `${t}`);
  });
});

describe('board: legend rows and parts', () => {
  after(() => document.body.replaceChildren());

  test('pending rows keep their slot but are hidden; named columns and terms are marked', () => {
    const scene = withCues(ohm, VAR_CUES);
    const board = new BoardView(scene, { mode: 'live', renderTex: fakeTex().render });
    document.body.appendChild(board.el);
    const rows = [...board.el.querySelectorAll('[data-item="i-formula"] .bi-var')];
    assert.equal(rows.length, 3);
    assert.deepEqual(rows.map((r) => r.getAttribute('data-part')), ['var:0', 'var:1', 'var:2']);
    board.update(sceneStateAt(scene, 3.5));
    assert.deepEqual(rows.map((r) => r.classList.contains('is-unsaid')), [true, true, false]);
    assert.equal(rows[0].getAttribute('aria-hidden'), 'true');
    board.update(sceneStateAt(scene, 4.2));
    assert.deepEqual(rows.map((r) => r.classList.contains('is-unsaid')), [false, true, false]);
    assert.equal(rows[0].hasAttribute('aria-hidden'), false);
    board.update(sceneStateAt(scene, 5));
    assert.ok(rows.every((r) => !r.classList.contains('is-unsaid')));

    const term = board.el.querySelector('[data-item="i-def"] .bi-term');
    board.update({ ...sceneStateAt(scene, 5), emphasisParts: new Set(['i-def|term']) });
    assert.ok(term?.classList.contains('is-emph'));
    board.update(sceneStateAt(scene, 5));
    assert.equal(term?.classList.contains('is-emph'), false);
    board.destroy();
  });

  test('table column cells (header and body) follow their column part', () => {
    const board = new BoardView(example, { mode: 'live', renderTex: fakeTex().render });
    document.body.appendChild(board.el);
    const table = (example.board || []).find((i) => i.kind === 'table');
    assert.ok(table);
    const state = sceneStateAt(example, example.duration - 0.01);
    board.update({ ...state, emphasisParts: new Set([`${table.id}|column:1`]) });
    const marked = [...board.el.querySelectorAll('.bi-table .is-emph')];
    assert.equal(marked.length, 1 + (table.rows || []).length);
    for (const cell of marked) assert.equal(/** @type {HTMLTableCellElement} */ (cell).cellIndex, 1);
    board.update(state);
    assert.equal(board.el.querySelectorAll('.bi-table .is-emph').length, 0);
    board.destroy();
  });

  test('states without the new fields (older callers) render as before', () => {
    const board = new BoardView(ohm, { mode: 'live', renderTex: fakeTex().render });
    const s = sceneStateAt(ohm, 5);
    const legacy = { ...s };
    delete legacy.pendingParts;
    delete legacy.emphasisParts;
    board.update(legacy);
    assert.equal(board.el.querySelectorAll('.is-unsaid, .is-emph').length, 0);
    board.destroy();
  });
});

describe('side panel focus', () => {
  test('the card gets is-focus while the schedule says so, never while hidden', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const rsp = { show_at: 0, panel: { kind: 'terminal', terminal: { command: 'ls', output: 'a' } } };
    const tl = { scenes: [{ scene_id: 's', index: 0, type: 'content', start: 0, duration: 10, side_panel: rsp }], total_duration: 10 };
    const p = createPanel(host, /** @type {any} */ (rsp), /** @type {any} */ ({ mode: 'render', timeline: tl, sceneIndex: 0 }));
    p.update(1, { panelVisible: true, panelFocus: false });
    assert.equal(p.el.classList.contains('is-focus'), false);
    p.update(2, { panelVisible: true, panelFocus: true });
    assert.equal(p.el.classList.contains('is-focus'), true);
    p.update(2, { panelVisible: false, panelFocus: true });
    assert.equal(p.el.classList.contains('is-focus'), false);
    p.update(3, { panelVisible: true });
    assert.equal(p.el.classList.contains('is-focus'), false);
    p.destroy();
    host.remove();
  });

  test('without the player state the terminal panel times its lines from the output cue', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const rsp = { show_at: 1, panel: { kind: 'terminal', terminal: { command: 'python add.py', output: '5\n6' } } };
    const tl = {
      scenes: [{ scene_id: 's', index: 0, type: 'content', start: 0, duration: 9, side_panel: rsp, sync_cues: [{ kind: 'output', start: 5 }] }],
      total_duration: 9,
    };
    const p = createPanel(host, /** @type {any} */ (rsp), /** @type {any} */ ({ mode: 'render', timeline: tl, sceneIndex: 0 }));
    const shown = () => [...p.el.querySelectorAll('.ap-term-out')].filter((l) => !(/** @type {HTMLElement} */ (l).hidden)).length;
    p.update(4.9, {});
    assert.equal(shown(), 0);
    p.update(5, {});
    assert.equal(shown(), 1);
    p.update(7, {});
    assert.equal(shown(), 2);
    p.destroy();
    host.remove();
  });
});

describe('Studio summary', () => {
  test('moments in plain words, from the scene itself, in time order', () => {
    const scene = withCues(ohm, [
      ...VAR_CUES,
      { kind: 'emphasis', start: 13.5, end: 15, item_id: 'i-def', part: 'term', beat_id: 's-ohm-b6', words: 'resistance' },
      { kind: 'output', start: 10, words: 'prints' },
      { kind: 'focus', start: 4.0, end: 5.6, words: 'look at this' },
    ]);
    const s = syncSummary(scene);
    assert.ok(s);
    assert.equal(s.source, 'narration');
    assert.equal(s.beats.length, (ohm.beats || []).length);
    assert.equal(s.beats[1].when, '3.2 s');
    assert.deepEqual(
      s.moments.map((m) => `${m.when} ${m.what} · ${m.anchor}`),
      [
        '4.0 s the side panel is highlighted · when the narration says “look at this”',
        '4.0 s “voltage” appears in the formula legend · when the narration says “voltage”',
        '4.6 s “current” appears in the formula legend · when the narration says “current”',
        '10.0 s the program output starts to appear · when the narration says “prints”',
        '13.5 s the term “Resistance” is highlighted · when the narration says “resistance”',
      ],
    );
    assert.equal(s.keyMoments.length, Math.min(MAX_KEY_MOMENTS, s.moments.length));
  });

  test('estimated previews say so; columns and missing words read naturally', () => {
    const table = (example.board || []).find((i) => i.kind === 'table');
    assert.ok(table);
    const scene = withCues(example, [{ kind: 'emphasis', start: 1, end: 2, item_id: table.id, part: 'column:1' }]);
    for (const b of scene.beats) b.estimated = true;
    const s = syncSummary(scene, { maxMoments: 0 });
    assert.ok(s);
    assert.equal(s.source, 'estimate');
    assert.match(s.timing, /estimated/);
    assert.equal(s.moments.length, 1);
    assert.match(s.moments[0].what, /^the “.+” column is highlighted$/);
    assert.equal(s.moments[0].anchor, 'when its beat starts');
    assert.deepEqual(s.keyMoments, []);
    assert.equal(syncSummary(null), null);
  });
});
