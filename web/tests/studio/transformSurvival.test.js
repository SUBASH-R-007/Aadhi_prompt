// Transform survival (design §6.7, batch B0): every editor transform keeps the fields it does not own.
//
// The maximal Dynamic screenplay (tests/core/fixtures/transform_survival_dynamic.json, shared with the Python
// test tests/core/test_transform_survival.py) has every v3 field, every new board item kind, a clip scene, cuts
// and overlays, in every scene type. Each transform below may change only what it owns (listed per test); every
// other field, known to this build or not, must come out exactly as it went in. The transforms that work on any
// lecture also run on the schema's own maximal document (tests/schemas/fixtures/screenplay_v3_maximal.json, the
// canonical dump the server stores and serves). Pure functions: no DOM.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { splitScene, canSplitType } from '../../js/studio/views/editor/splitScene.js';
import { mergeDrafts } from '../../js/studio/views/editor/sceneMerge.js';
import { applyRepair } from '../../js/studio/views/editor/quality.js';
import { saveDraft, loadDraft } from '../../js/studio/lib/draftStore.js';
import { localProblems } from '../../js/studio/lib/validationIssues.js';
import { sampleScreenplay } from './fixtures.js';

const FIXTURE = new URL('../../../tests/core/fixtures/transform_survival_dynamic.json', import.meta.url);
const SCHEMA_MAXIMAL = new URL('../../../tests/schemas/fixtures/screenplay_v3_maximal.json', import.meta.url);
const SCHEMA = new URL('../../schemas/screenplay.schema.json', import.meta.url);

/** A fresh copy of the maximal Dynamic screenplay. */
const maximal = () => JSON.parse(readFileSync(FIXTURE, 'utf8'));
/** Both maximal documents (fresh copies), for the checks that work on any lecture. */
const DOCUMENTS = /** @type {const} */ ([
  ['transform_survival_dynamic', maximal],
  ['screenplay_v3_maximal', () => JSON.parse(readFileSync(SCHEMA_MAXIMAL, 'utf8'))],
]);

// ---------------------------------------------------------------------------------------------------------------
// Helpers: a screenplay as a map of leaf paths, keyed by ids (not positions) wherever a list has ids
// ---------------------------------------------------------------------------------------------------------------

/** JSON with sorted object keys (stable comparison of leaves). */
function stable(value) {
  return JSON.stringify(value, (_k, v) =>
    v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, v[k]])) : v,
  );
}

function isIdList(v) {
  return Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === 'object' && !Array.isArray(x) && typeof x.id === 'string');
}

/**
 * Leaf path -> stable JSON. Lists of objects with ids are keyed by id (`scenes[s2].beats[s2-b4].cuts`) and their
 * order is the leaf `<list>#ids`; other lists and empty objects are leaves.
 */
function flatten(node, path = '', out = new Map()) {
  if (isIdList(node)) {
    out.set(`${path}#ids`, stable(node.map((x) => x.id)));
    for (const x of node) flatten(x, `${path}[${x.id}]`, out);
  } else if (node && typeof node === 'object' && !Array.isArray(node) && Object.keys(node).some((k) => node[k] !== undefined)) {
    for (const [k, v] of Object.entries(node)) if (v !== undefined) flatten(v, path ? `${path}.${k}` : k, out);
  } else {
    out.set(path, stable(node));
  }
  return out;
}

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** Matches `path` and everything below it. */
const under = (path) => new RegExp(`^${esc(path)}(?:$|[.[#])`);

/**
 * Every leaf of `before` is in `after` with the same value, except the leaves an `owned` pattern matches (what the
 * transform may change or remove).
 */
function assertKeeps(before, after, owned, label) {
  const got = flatten(after);
  const lost = [];
  for (const [path, value] of flatten(before)) {
    if (owned.some((re) => re.test(path))) continue;
    if (!got.has(path)) lost.push(`${path}: gone`);
    else if (got.get(path) !== value) lost.push(`${path}: ${value} -> ${got.get(path)}`);
  }
  assert.deepEqual(lost, [], `${label}: fields it does not own were dropped or changed`);
}

/** `before` and `after` are equal outside the `owned` leaves (nothing lost, changed or added). */
function assertSameOutside(before, after, owned, label) {
  assertKeeps(before, after, owned, label);
  assertKeeps(after, before, owned, `${label} (added)`);
}

/** The same scene after a scene-level edit; the rest of the lecture is unchanged. */
function editScene(sp, sceneId, fn) {
  return E.updateScene(sp, sceneId, fn);
}

/** Every board reference of a scene points at an item of its own board, and it is already repaired. */
function refsValid(scene, label) {
  const items = new Set((scene.board || []).map((i) => i.id));
  for (const b of scene.beats) {
    if (b.board_item_id) assert.ok(items.has(b.board_item_id), `${label}: ${scene.id} reveals ${b.board_item_id}`);
    if (b.fill_item_id) assert.ok(items.has(b.fill_item_id), `${label}: ${scene.id} fills ${b.fill_item_id}`);
    for (const id of b.highlight_item_ids || []) assert.ok(items.has(id), `${label}: ${scene.id} highlights ${id}`);
  }
  assert.deepEqual(E.repairSceneRefs(scene), scene, `${label}: ${scene.id} references already repaired`);
}

const scene = (sp, id) => /** @type {any} */ (E.findScene(sp, id));
const allBeats = (sp) => sp.scenes.flatMap((s) => [...(s.beats || []), ...(s.reveal_beats || [])]);
const allItems = (sp) => sp.scenes.flatMap((s) => s.board || []);

// ---------------------------------------------------------------------------------------------------------------
// The fixture and the editor's key lists
// ---------------------------------------------------------------------------------------------------------------

test('the maximal fixture has every v3 field, every new item kind, a clip scene, cuts and overlays', () => {
  const sp = maximal();
  const presentationKeys = [
    'style', 'format', 'length_profile', 'reel_profile', 'destination', 'look_seed', 'look_epoch', 'layout_epoch',
    'theme_family', 'personality', 'display_font', 'caption_style', 'cue_term_style', 'intro_layout', 'reel_card_x',
    'hook_kind', 'check_order', 'greeting_id', 'asset_style', 'voices', 'music', 'entity_colors', 'hook', 'aadhi_recap',
    'captions_burn', 'sfx', 'motion', 'pins',
  ];
  assert.deepEqual(presentationKeys.filter((k) => !(k in sp.presentation)), [], 'Presentation');
  assert.deepEqual(['narrator', 'aadhi', 'student', 'calibration'].filter((k) => !(k in sp.presentation.voices)), []);
  assert.deepEqual(['source', 'provider', 'mood', 'asset_key', 'mode', 'level'].filter((k) => !(k in sp.presentation.music)), []);
  assert.deepEqual(['question_item', 'kind', 'answer_beat'].filter((k) => !(k in sp.presentation.hook)), []);
  assert.ok(sp.chapters.some((c) => c.builds_on === false), 'Chapter.builds_on');
  const looks = sp.scenes.map((s) => s.look).filter(Boolean);
  const lookKeys = ['template', 'variant', 'background', 'palette_slot', 'transition_in', 'hold_reason', 'presenter', 'continues_from', 'lock'];
  assert.deepEqual(lookKeys.filter((k) => !looks.some((l) => k in l)), [], 'SceneLook');
  assert.deepEqual(['auto', 'settled', 'pinned'].filter((v) => !looks.some((l) => l.lock === v)), [], 'SceneLook.lock');
  assert.ok(sp.scenes.some((s) => s.colour_is_content === true), 'SceneBase.colour_is_content');
  assert.ok(sp.scenes.some((s) => s.side_panel && s.side_panel.colour_is_content === true), 'SidePanel.colour_is_content');
  assert.ok(sp.scenes.some((s) => s.intent && s.intent.visual_intent), 'SceneIntent.visual_intent');
  assert.deepEqual(['pretraining', 'predict', 'recap'].filter((r) => !sp.scenes.some((s) => s.intent && s.intent.narrative_role === r)), []);
  assert.ok(sp.scenes.some((s) => E.isBoardScene(s) && s.sequence), 'BoardScene.sequence');
  // classic fields left out at their defaults
  assert.ok(sp.scenes.some((s) => s.hidden === true) && sp.scenes.some((s) => typeof s.min_seconds === 'number'));
  assert.ok(sp.scenes.some((s) => s.variant > 0) && sp.scenes.some((s) => s.side_panel && s.side_panel.variant > 0));
  assert.ok(allItems(sp).some((i) => (i.variables || []).some((v) => v.beat_id)), 'FormulaVariable.beat_id');
  // beats
  const beats = allBeats(sp);
  assert.deepEqual(['aadhi', 'student'].filter((v) => !beats.some((b) => b.speaker === v)), [], 'Beat.speaker');
  assert.ok(beats.some((b) => b.answers) && beats.some((b) => b.voice_override_asset_key), 'Beat.answers / voice_override_asset_key');
  const cuts = beats.flatMap((b) => b.cuts || []);
  assert.deepEqual(['splice', 'revoice'].filter((m) => !cuts.some((c) => c.mode === m)), [], 'WordRange.mode');
  const overlays = beats.flatMap((b) => b.overlays || []);
  const overlayKeys = ['id', 'at_word', 'at_text', 'clip', 'region', 'sound', 'concept_id', 'keep_full_length'];
  assert.deepEqual(overlayKeys.filter((k) => !overlays.some((o) => k in o)), [], 'Overlay');
  // board items
  const items = allItems(sp);
  assert.deepEqual(['question', 'stat', 'step', 'quote', 'pair'].filter((k) => !items.some((i) => i.kind === k)), [], 'new kinds');
  assert.deepEqual(['value', 'unit', 'date', 'icon_hint', 'pins', 'level'].filter((k) => !items.some((i) => k in i)), [], 'new item fields');
  const blankQuestions = new Set(items.filter((i) => i.kind === 'question' && i.blank).map((i) => i.id));
  assert.ok(blankQuestions.size && beats.some((b) => blankQuestions.has(b.fill_item_id)), 'a beat fills a blank question');
  // the clip scene
  const clip = sp.scenes.find((s) => s.type === 'clip');
  assert.ok(clip, 'a clip scene');
  const clipKeys = ['clip', 'sound', 'gain_db', 'fit', 'focus', 'end_behavior', 'captions', 'credit'];
  assert.deepEqual(clipKeys.filter((k) => !(k in clip)), [], 'ClipScene');
});

test('COMMON_SCENE_KEYS and BOARD_SCENE_KEYS cover the schema (web/schemas/screenplay.schema.json)', () => {
  const schema = JSON.parse(readFileSync(SCHEMA, 'utf8'));
  const refs = [...new Set(Object.values(schema.properties.scenes.items.discriminator.mapping))];
  const props = refs.map((ref) => new Set(Object.keys(schema.$defs[String(ref).split('/').pop()].properties)));
  const common = [...props[0]].filter((k) => k !== 'type' && props.every((p) => p.has(k)));
  // A field every scene type has must survive a type change: a field added to SceneBase without being added here
  // would be dropped by changeSceneType.
  assert.deepEqual(common.filter((k) => !E.COMMON_SCENE_KEYS.includes(k)), [], 'SceneBase fields missing from COMMON_SCENE_KEYS');
  const board = Object.keys(schema.$defs.BoardScene.properties).filter((k) => k !== 'type' && !common.includes(k));
  assert.deepEqual(board.filter((k) => !E.BOARD_SCENE_KEYS.includes(k)), [], 'BoardScene fields missing from BOARD_SCENE_KEYS');
  // B0 item 5: the teacher's timing choices, the look and the colour flag are common to every scene type.
  for (const k of ['hidden', 'min_seconds', 'look', 'colour_is_content']) assert.ok(E.COMMON_SCENE_KEYS.includes(k), k);
});

// ---------------------------------------------------------------------------------------------------------------
// convertSceneShape (changeSceneType)
// ---------------------------------------------------------------------------------------------------------------

test('changeSceneType keeps hidden, min_seconds and look (a classic lecture too)', () => {
  const sp = sampleScreenplay();
  const look = { template: 'list_build', palette_slot: 2, lock: 'pinned' };
  const held = E.updateScene(sp, 's2', (s) => ({ ...s, hidden: true, min_seconds: 14, look, colour_is_content: true }));
  for (const type of ['summary', 'quiz_checkpoint', 'chapter_card', 'simulation', 'ai_video', 'interactive']) {
    const out = scene(E.changeSceneType(held, 's2', type), 's2');
    assert.equal(out.type, type);
    assert.equal(out.hidden, true, `${type}: hidden`);
    assert.equal(out.min_seconds, 14, `${type}: min_seconds`);
    assert.deepEqual(out.look, look, `${type}: look`);
    assert.equal(out.colour_is_content, true, `${type}: colour_is_content`);
  }
  // a scene that never had them does not get them
  const plain = scene(E.changeSceneType(sp, 's2', 'summary'), 's2');
  for (const k of ['hidden', 'min_seconds', 'look', 'colour_is_content']) assert.equal(k in plain, false, k);
});

for (const [name, load] of DOCUMENTS) test(`${name}: changeSceneType, every scene to every type, keeps what it does not own`, () => {
  const sp = load();
  for (const before of sp.scenes) {
    for (const type of E.SCENE_TYPES) {
      if (type === before.type) continue;
      const label = `${before.id} ${before.type} -> ${type}`;
      const out = E.changeSceneType(sp, before.id, type);
      assertSameOutside(sp, out, [under(`scenes[${before.id}]`)], label);
      const after = scene(out, before.id);
      assert.equal(after.type, type, label);
      const bothBoard = E.isBoardScene(before) && E.isBoardScene({ type });
      const kept = new Set([...E.COMMON_SCENE_KEYS, ...(bothBoard ? E.BOARD_SCENE_KEYS : [])]);
      // Owned: the old type's own fields, board references (gone outside board scenes), beats a chapter card cannot hold.
      const owned = [/^type$/, ...Object.keys(before).filter((k) => !kept.has(k)).map((k) => under(k))];
      if (!E.isBoardScene({ type })) owned.push(/^beats\[[^\]]+\]\.(board_item_id|fill_item_id|highlight_item_ids)$/);
      const keptBeats = new Set(after.beats.map((b) => b.id));
      const dropped = before.beats.filter((b) => !keptBeats.has(b.id)).map((b) => b.id);
      if (dropped.length) {
        assert.equal(type, 'chapter_card', `${label}: only a chapter card drops beats`);
        owned.push(/^beats#ids$/, ...dropped.map((id) => under(`beats[${id}]`)));
        if (before.side_panel && dropped.includes(before.side_panel.show_from_beat_id)) owned.push(/^side_panel\.show_from_beat_id$/);
      }
      assertKeeps(before, after, owned, label);
      for (const k of ['hidden', 'min_seconds', 'look', 'colour_is_content']) {
        if (k in before) assert.deepEqual(after[k], before[k], `${label}: ${k}`);
      }
      refsValid(after, label);
    }
  }
});

// ---------------------------------------------------------------------------------------------------------------
// Reference repair, sanitising, drafts
// ---------------------------------------------------------------------------------------------------------------

for (const [name, load] of DOCUMENTS) test(`${name}: repairSceneRefs and sanitizeScreenplayRefs leave it exactly as it is`, () => {
  const sp = load();
  assert.deepEqual(localProblems(sp), [], 'the editor saves it as it is (autosave is not held)');
  for (const s of sp.scenes) assert.deepEqual(E.repairSceneRefs(s), s, `${s.id}: repairSceneRefs`);
  assert.deepEqual(E.sanitizeScreenplayRefs(sp), sp, 'sanitizeScreenplayRefs (draft restore, merges)');
  assert.deepEqual(E.syncChapterOrder(sp), sp, 'syncChapterOrder');
});

test('a blank question and the beat that fills it survive reference repair', () => {
  const sp = maximal();
  // a blank question stays blank and its fill stays (the server allows both, design §6.7)
  const s7 = scene(E.sanitizeScreenplayRefs(sp), 's7');
  assert.equal(s7.board.find((i) => i.id === 's7-i4').blank, true);
  assert.equal(s7.beats.find((b) => b.id === 's7-b5').fill_item_id, 's7-i4');
});

test('a blank question is fillable like a blank example step; other kinds still cannot be blank', () => {
  const sp = maximal();
  const s7 = scene(sp, 's7');
  const cleared = E.setBeatFill(s7, 4, null);
  assert.deepEqual(E.fillOptions(cleared, 4).map((i) => i.id), ['s7-i4']);
  assert.deepEqual(scene({ scenes: [E.setBeatFill(cleared, 4, 's7-i4')] }, 's7'), s7);
  const s11 = scene(sp, 's11');
  const blanked = E.setItemBlank(s11, 's11-i2', true);
  assert.equal(blanked.board.find((i) => i.id === 's11-i2').blank, true);
  assert.throws(() => E.setItemBlank(s11, 's11-i1', true), /only example steps and questions can be blank/);
  // changing a blank question to a bullet clears blank and the fill (bullets cannot be blank)
  const bullet = E.setBoardItemKind(s7, 's7-i4', 'bullet');
  assert.equal(bullet.board.find((i) => i.id === 's7-i4').blank, false);
  assert.equal(bullet.beats.find((b) => b.id === 's7-b5').fill_item_id, null);
});

for (const [name, load] of DOCUMENTS) test(`${name}: a local draft keeps the whole screenplay (saveDraft / loadDraft)`, () => {
  const store = new Map();
  const storage = {
    get length() { return store.size; },
    key: (i) => [...store.keys()][i] ?? null,
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => void store.set(k, String(v)),
    removeItem: (k) => void store.delete(k),
    clear: () => store.clear(),
  };
  const sp = load();
  assert.equal(saveDraft(7, 3, { revision: 4, screenplay: sp, base: load() }, /** @type {any} */ (storage)), true);
  const draft = loadDraft(7, 3, /** @type {any} */ (storage));
  assert.ok(draft);
  assert.deepEqual(draft.screenplay, sp);
  assert.deepEqual(draft.base, sp);
});

// ---------------------------------------------------------------------------------------------------------------
// Scene, beat and item edits (screenplayEdit.js, quality.js)
// ---------------------------------------------------------------------------------------------------------------

/** [label, transform, owned leaves] for edits whose ids stay stable. */
const EDITS = [
  ['updateScene (title)', (sp) => editScene(sp, 's3', (s) => ({ ...s, title: 'Measuring' })), [/^scenes\[s3\]\.title$/]],
  ['moveScene', (sp) => E.moveScene(sp, 2, 7), [/^scenes#ids$/, /^chapters\[[^\]]+\]\.scene_ids$/]],
  ['setSceneChapter', (sp) => E.setSceneChapter(sp, 's3', 'ch2'), [/^scenes\[s3\]\.chapter_id$/, /^chapters\[[^\]]+\]\.scene_ids$/]],
  ['deleteScene', (sp) => E.deleteScene(sp, 8), [/^scenes#ids$/, under('scenes[s9]'), /^chapters\[ch2\]\.scene_ids$/]],
  ['addScene', (sp) => E.addScene(sp, 'content', 4).screenplay, [/^scenes#ids$/, /^chapters\[ch1\]\.scene_ids$/]],
  ['updateBeat (narration)', (sp) => editScene(sp, 's2', (s) => E.updateBeat(s, 3, { narration: 'Ohm put it simply.' })),
    [/^scenes\[s2\]\.beats\[s2-b4\]\.narration$/]],
  ['addBeat', (sp) => editScene(sp, 's3', (s) => E.addBeat(s, 0).scene), [/^scenes\[s3\]\.beats#ids$/]],
  ['removeBeat', (sp) => editScene(sp, 's7', (s) => E.removeBeat(s, 5)), [/^scenes\[s7\]\.beats#ids$/, under('scenes[s7].beats[s7-b6]')]],
  ['moveBeat', (sp) => editScene(sp, 's3', (s) => E.moveBeat(s, 2, 1)), [/^scenes\[s3\]\.beats#ids$/]],
  ['setBeatReveal', (sp) => editScene(sp, 's3', (s) => E.setBeatReveal(s, 2, 's3-i4')), [/^scenes\[s3\]\.beats\[s3-b3\]\.board_item_id$/]],
  ['setBeatHighlights', (sp) => editScene(sp, 's2', (s) => E.setBeatHighlights(s, 4, ['s2-i1', 's2-i3'])),
    [/^scenes\[s2\]\.beats\[s2-b5\]\.highlight_item_ids$/]],
  ['setBeatFill', (sp) => editScene(sp, 's7', (s) => E.setBeatFill(s, 4, null)), [/^scenes\[s7\]\.beats\[s7-b5\]\.fill_item_id$/]],
  ['addBoardItem', (sp) => editScene(sp, 's3', (s) => E.addBoardItem(s, 'bullet', 3, sp).scene), [/^scenes\[s3\]\.board#ids$/]],
  ['deleteBoardItem', (sp) => editScene(sp, 's3', (s) => E.deleteBoardItem(s, 's3-i4')), [/^scenes\[s3\]\.board#ids$/, under('scenes[s3].board[s3-i4]')]],
  ['moveBoardItem', (sp) => editScene(sp, 's2', (s) => E.moveBoardItem(s, 0, 4)), [/^scenes\[s2\]\.board#ids$/]],
  ['updateBoardItem', (sp) => editScene(sp, 's2', (s) => E.updateBoardItem(s, 's2-i2', { text: 'Mains voltage at home' })),
    [/^scenes\[s2\]\.board\[s2-i2\]\.text$/]],
  ['setBoardItemKind', (sp) => editScene(sp, 's3', (s) => E.setBoardItemKind(s, 's3-i4', 'bullet', sp)), [/^scenes\[s3\]\.board\[s3-i4\]\.kind$/]],
  ['setItemBlank', (sp) => editScene(sp, 's11', (s) => E.setItemBlank(s, 's11-i2', true)), [/^scenes\[s11\]\.board\[s11-i2\]\.blank$/]],
  ['addQuizOption', (sp) => editScene(sp, 's10', (s) => E.addQuizOption(s)),
    [/^scenes\[s10\]\.(options|feedback_wrong|option_misconception_ids)$/]],
  ['removeQuizOption', (sp) => editScene(sp, 's10', (s) => E.removeQuizOption(s, 2)),
    [/^scenes\[s10\]\.(options|feedback_wrong|option_misconception_ids)$/]],
  ['moveQuizOption', (sp) => editScene(sp, 's10', (s) => E.moveQuizOption(s, 0, 2)),
    [/^scenes\[s10\]\.(options|feedback_wrong|option_misconception_ids|correct_index)$/]],
  ['sidePanelOfKind (same kind)', (sp) => editScene(sp, 's3', (s) => ({ ...s, side_panel: E.sidePanelOfKind('image', s.side_panel, sp) })), []],
  ['applyRepair', (sp) => applyRepair(sp, {
    edits: [{ scene_id: 's2', path: ['beats', 3, 'narration'], before: 'Ohm himself, very famously, put it simply.', after: 'Ohm himself put it simply.' }],
  }), [/^scenes\[s2\]\.beats\[s2-b4\]\.narration$/]],
];

for (const [label, transform, owned] of EDITS) {
  test(`${label} keeps every field it does not own`, () => {
    const sp = maximal();
    const before = maximal();
    const out = /** @type {any} */ (transform(sp));
    assert.ok(out && out !== sp, `${label}: an edit`);
    assertKeeps(before, out, owned, label);
    assert.deepEqual(sp, before, `${label}: the input is not changed`);
  });
}

for (const [name, load] of DOCUMENTS) test(`${name}: duplicateScene, the copy keeps every field of the original (with its own ids)`, () => {
  const sp = load();
  for (const [index, original] of sp.scenes.entries()) {
    const { screenplay: out, scene: copy } = E.duplicateScene(sp, index);
    const label = `duplicate ${original.id}`;
    assertKeeps(sp, out, [/^scenes#ids$/, /^chapters\[[^\]]+\]\.scene_ids$/], label);
    const owned = [/^id$/, /^title$/, /^side_panel\.show_from_beat_id$/];
    // Compare beat by beat and item by item (the copy's ids are new; its references point at its own items).
    const lists = { beats: [], reveal_beats: [], board: [] };
    assertKeeps({ ...original, ...lists }, { ...copy, ...lists }, owned, label);
    for (const key of ['beats', 'reveal_beats']) {
      assert.equal((copy[key] || []).length, (original[key] || []).length, `${label} ${key}`);
      (original[key] || []).forEach((b, i) =>
        assertKeeps(b, copy[key][i], [/^id$/, /^(board_item_id|fill_item_id|highlight_item_ids)$/], `${label} beat ${b.id}`));
    }
    (original.board || []).forEach((it, i) => assertKeeps(it, copy.board[i], [/^id$/, /^variables$/], `${label} item ${it.id}`));
    refsValid(copy, label);
  }
});

// ---------------------------------------------------------------------------------------------------------------
// splitScene
// ---------------------------------------------------------------------------------------------------------------

/**
 * Split `sceneId` before beat `k` and check both parts and the rest of the lecture.
 * @returns {{ out: any, itemMap: Map<string, string>, beatMap: Map<string, string>, bId: string }}
 */
function checkSplit(sp, sceneId, k) {
  const label = `split ${sceneId} at ${k}`;
  const before = scene(sp, sceneId);
  const { screenplay: out, scene: b } = splitScene(sp, sceneId, k);
  const a = scene(out, sceneId);
  const moved = before.beats.slice(k);
  const board = E.isBoardScene(before);
  const movingItems = new Set(board ? moved.flatMap((x) => [x.board_item_id, x.fill_item_id]).filter(Boolean) : []);
  const beatMap = new Map(moved.map((x, i) => [x.id, b.beats[i].id]));
  const movedItemList = (before.board || []).filter((it) => movingItems.has(it.id));
  const itemMap = new Map(movedItemList.map((it, i) => [it.id, b.board[i].id]));
  const mapRef = (ref, map) => {
    if (typeof ref !== 'string' || !ref.startsWith(`${sceneId}/`)) return ref;
    const to = map.get(ref.slice(sceneId.length + 1));
    return to ? `${b.id}/${to}` : ref;
  };

  // Part A keeps everything except the moved beats and items and the references that pointed across the cut.
  const ownedA = [/^beats#ids$/, /^board#ids$/, ...moved.map((x) => under(`beats[${x.id}]`)), ...[...movingItems].map((id) => under(`board[${id}]`))];
  for (const x of before.beats.slice(0, k)) {
    // an item revealed here but filled after the cut moves with its fill
    if (movingItems.has(x.board_item_id)) ownedA.push(new RegExp(`^beats\\[${esc(x.id)}\\]\\.board_item_id$`));
    if (movingItems.has(x.fill_item_id)) ownedA.push(new RegExp(`^beats\\[${esc(x.id)}\\]\\.fill_item_id$`));
    if ((x.highlight_item_ids || []).some((h) => movingItems.has(h))) ownedA.push(new RegExp(`^beats\\[${esc(x.id)}\\]\\.highlight_item_ids$`));
  }
  if (before.side_panel && beatMap.has(before.side_panel.show_from_beat_id)) ownedA.push(/^side_panel\.show_from_beat_id$/);
  for (const it of before.board || []) {
    if ((it.variables || []).some((v) => beatMap.has(v.beat_id))) ownedA.push(under(`board[${it.id}].variables`));
  }
  assertKeeps(before, a, ownedA, `${label} (A)`);

  // Part B: the moved beats and items with every field (new ids; references renamed with them).
  assert.equal(b.type, board ? before.type : 'content', label);
  moved.forEach((x, i) => {
    assertKeeps(x, b.beats[i], [/^id$/, /^(board_item_id|fill_item_id|highlight_item_ids)$/], `${label} beat ${x.id}`);
    assert.equal(b.beats[i].board_item_id ?? null, x.board_item_id ? itemMap.get(x.board_item_id) ?? null : null);
  });
  movedItemList.forEach((it, i) => assertKeeps(it, b.board[i], [/^id$/, /^variables$/], `${label} item ${it.id}`));
  assert.equal(b.hidden === true, before.hidden === true, `${label}: B is hidden like A`);
  assert.equal(b.sequence ?? null, board ? before.sequence ?? null : null, `${label}: B keeps the board's sequence`);
  assert.equal('look' in b, false, `${label}: B gets its look when the lecture is saved`);
  refsValid(a, label);
  refsValid(b, label);
  assert.deepEqual(localProblems(out), [], `${label}: both parts are valid`);

  // The rest of the lecture: unchanged, except references to what moved, which follow it.
  const refLeaves = [
    /^scenes\[[^\]]+\]\.(beats|reveal_beats)\[[^\]]+\]\.answers$/,
    /^presentation\.hook\.(question_item|answer_beat)$/,
    /^companion_sheet\.(key_formulas|definitions)$/,
  ];
  assertSameOutside(sp, out, [under(`scenes[${sceneId}]`), under(`scenes[${b.id}]`), /^scenes#ids$/, /^chapters\[[^\]]+\]\.scene_ids$/, ...refLeaves], label);
  for (const s of sp.scenes) {
    if (s.id === sceneId) continue;
    for (const x of [...s.beats, ...(s.reveal_beats || [])]) {
      const got = allBeats(out).find((y) => y.id === x.id);
      assert.equal(got.answers, mapRef(x.answers, itemMap), `${label}: ${x.id}.answers`);
    }
  }
  const hook = sp.presentation.hook;
  assert.deepEqual(out.presentation.hook, {
    ...hook,
    question_item: mapRef(hook.question_item, itemMap),
    answer_beat: mapRef(hook.answer_beat, beatMap),
  }, `${label}: hook`);
  for (const key of ['key_formulas', 'definitions']) {
    assert.deepEqual(out.companion_sheet[key], sp.companion_sheet[key].map((e) => ({ ...e, source_item_id: mapRef(e.source_item_id, itemMap) })), `${label}: ${key}`);
  }
  return { out, itemMap, beatMap, bId: b.id };
}

for (const [name, load] of DOCUMENTS) test(`${name}: splitScene, every splittable scene at every beat, keeps what it does not own`, () => {
  const sp = load();
  let splits = 0;
  for (const s of sp.scenes) {
    if (!canSplitType(s)) continue;
    for (let k = 1; k < s.beats.length; k++) {
      checkSplit(sp, s.id, k);
      splits += 1;
    }
  }
  assert.ok(splits >= 5, `only ${splits} splits`);
});

test('splitScene: references follow a moved question, a moved answering beat and a moved definition', () => {
  const sp = maximal();
  // s7's blank question moves to the second part: the beat in s8 that answers it follows it
  let { out } = checkSplit(sp, 's7', 3);
  assert.equal(allBeats(out).find((x) => x.id === 's8-b2').answers, 's7-2/s7-2-i1');
  assert.deepEqual(scene(out, 's7-2').beats.map((x) => x.fill_item_id), [null, 's7-2-i1', null]);
  assert.equal(scene(out, 's7-2').board[0].blank, true);
  assert.deepEqual(scene(out, 's7-2').beats[2].cuts, scene(sp, 's7').beats[5].cuts, 'the cut moves with its beat');
  // s3's answering beat (the hook's answer) moves: the hook follows it; the beat still answers s2's question
  ({ out } = checkSplit(sp, 's3', 1));
  assert.deepEqual(out.presentation.hook, { question_item: 's2/s2-i1', kind: 'question', answer_beat: 's3-2/s3-2-b1' });
  assert.equal(scene(out, 's3-2').beats[0].answers, 's2/s2-i1');
  assert.equal(scene(out, 's3-2').beats[0].voice_override_asset_key, 'narration-take-1');
  assert.equal(scene(out, 's3-2').sequence, 'flow');
  assert.equal(scene(out, 's3').min_seconds, 12.5, 'A keeps its hold');
  assert.equal(scene(out, 's3-2').min_seconds, undefined);
  // s2's definition moves: the companion sheet entry follows it; the overlay moves with its beat
  ({ out } = checkSplit(sp, 's2', 3));
  assert.equal(out.companion_sheet.definitions[0].source_item_id, 's2-2/s2-2-i2');
  assert.deepEqual(scene(out, 's2-2').beats[0].overlays, scene(sp, 's2').beats[3].overlays);
  assert.deepEqual(scene(out, 's2').look, scene(sp, 's2').look, 'A keeps its look');
});

// ---------------------------------------------------------------------------------------------------------------
// Merges (save conflicts, a job's result, a restored draft: sceneMerge.js + lib/conflict.js)
// ---------------------------------------------------------------------------------------------------------------

for (const [name, load] of DOCUMENTS) test(`${name}: mergeDrafts with no changes returns the screenplay unchanged`, () => {
  const sp = load();
  const merged = mergeDrafts(sp, load(), load());
  assert.deepEqual(merged.conflicts, []);
  assert.deepEqual(merged.screenplay, sp);
});

test('mergeDrafts keeps every field through merges of different scenes, the same scene and lecture fields', () => {
  const base = maximal();
  const cases = [
    {
      label: 'different scenes',
      mine: (sp) => editScene(sp, 's3', (s) => ({ ...s, title: 'Measuring' })),
      theirs: (sp) => editScene(sp, 's7', (s) => E.updateBeat(s, 1, { narration: 'Take two amps through three ohms now.' })),
      changed: [/^scenes\[s3\]\.title$/, /^scenes\[s7\]\.beats\[s7-b2\]\.narration$/],
    },
    {
      label: 'the same scene, different fields',
      mine: (sp) => editScene(sp, 's2', (s) => ({ ...s, title: 'Start with a question' })),
      theirs: (sp) => editScene(sp, 's2', (s) => E.updateBeat(s, 3, { pause_after: 1.5 })),
      changed: [/^scenes\[s2\]\.title$/, /^scenes\[s2\]\.beats\[s2-b4\]\.pause_after$/],
    },
    {
      label: 'a look filled on save next to a teacher edit',
      mine: (sp) => editScene(sp, 's2', (s) => ({ ...s, notes: 'Pause on the stat.' })),
      theirs: (sp) => editScene(sp, 's2', (s) => ({ ...s, look: { ...s.look, palette_slot: 4 } })),
      changed: [/^scenes\[s2\]\.notes$/, /^scenes\[s2\]\.look\.palette_slot$/],
    },
    {
      label: 'a clip scene edited on both sides',
      mine: (sp) => editScene(sp, 's5', (s) => ({ ...s, credit: 'Lab demo by Ms. Rao' })),
      theirs: (sp) => editScene(sp, 's5', (s) => ({ ...s, gain_db: -6 })),
      changed: [/^scenes\[s5\]\.(credit|gain_db)$/],
    },
    {
      label: 'lecture fields',
      mine: (sp) => ({ ...sp, session_title: "Ohm's Law, revisited" }),
      theirs: (sp) => ({ ...sp, presentation: { ...sp.presentation, look_epoch: 3 } }),
      changed: [/^session_title$/, /^presentation\.look_epoch$/],
    },
  ];
  for (const c of cases) {
    const mine = c.mine(maximal());
    const theirs = c.theirs(maximal());
    const merged = mergeDrafts(base, mine, theirs);
    assert.deepEqual(merged.conflicts, [], c.label);
    assertKeeps(base, merged.screenplay, c.changed, c.label);
    // both sides' edits are in the result
    const was = flatten(base);
    const got = flatten(merged.screenplay);
    for (const side of [mine, theirs]) {
      const edited = [...flatten(side)].filter(([path, v]) => was.get(path) !== v);
      assert.ok(edited.length, `${c.label}: an edit`);
      for (const [path, v] of edited) assert.equal(got.get(path), v, `${c.label}: ${path}`);
    }
  }
});

test('a conflicting merge keeps the teacher version with all its fields', () => {
  const base = maximal();
  const mine = editScene(maximal(), 's2', (s) => ({ ...s, title: 'Mine' }));
  const theirs = editScene(maximal(), 's2', (s) => ({ ...s, title: 'Theirs' }));
  const merged = mergeDrafts(base, mine, theirs);
  assert.equal(merged.conflicts.length, 1);
  assert.deepEqual(merged.screenplay, mine);
});
