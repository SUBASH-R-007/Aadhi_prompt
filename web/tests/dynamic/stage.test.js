import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from '../player/_dom.js';
import { readFileSync } from 'node:fs';

import { BACKGROUNDS, TEMPLATES, blockInk2, createDynamicStage, fontStacks, isDynamic, mediaBoxOf, syncClip, templateFor } from '../../js/dynamic/stage.js';
import { chapterTitleAt } from '../../js/dynamic/state.js';
import { Painter, fixesOf, makeRoles, stageInfo, insideAny, ZONES } from '../../js/dynamic/core.js';
import { MediaView, mediaKey, mediaPlan as planMedia, mediaSize } from '../../js/dynamic/media.js';
import { blockColors } from '../../js/dynamic/backgrounds/colour_block.js';
import { composite, contrastRatio, toHex, toOklch } from '../../js/motion/color.js';
import { splitWords } from '../../js/motion/text.js';
import boardGeneric, {
  BODY_WEIGHT, CARD_EXIT, CARD_RADIUS, FIGURE_BOX_WIDTH, FIGURE_SLOT, FRAME_RADIUS, LABELS, LAYOUT, MIN_DWELL, MIN_LABEL,
  QUIZ_CLEAR, RING_GAP, RING_SIZE, RING_TRACK_ALPHA, SCRIM_ALPHA_HEX, TITLE_BLOCK_SLOTS,
  cardSpecs, kickerOf, mediaBoxMax, regions, scrimmedBg, slotTuning, variantOf,
} from '../../js/dynamic/templates/board_generic.js';
import { OWNER_DECISION, R1_PALETTES, classicTimeline, dynamicTimeline } from './_fixture.js';

before(() => installDom());
after(() => uninstallDom());

const fast = { texIdle: () => Promise.resolve(), frame: () => Promise.resolve() };

/** @param {'landscape' | 'portrait'} [format] @param {any} [opts] */
function makeStage(format = 'landscape', opts = {}) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const timeline = dynamicTimeline(format);
  const stage = createDynamicStage(root, timeline, { mode: 'render', ...fast, ...opts });
  return { root, timeline, stage };
}

/** Per-scene markup of the stage (slots keyed by scene), for comparing frames reached by different paths. */
function snapshot(stage) {
  return stage.el.outerHTML;
}

// --- board_generic: pure parts --------------------------------------------------------------------------------

test('board_generic variants: stack, title card, quiz cards, media and split', () => {
  const s = dynamicTimeline().scenes;
  assert.deepEqual(s.map(variantOf), ['stack', 'title', 'stack', 'media', 'split']);
  assert.equal(templateFor(s[0]), boardGeneric);
  assert.equal(templateFor(/** @type {any} */ ({ look: { template: 'formula_focus' } })), boardGeneric, 'unreleased templates fall back');
  assert.deepEqual(Object.keys(TEMPLATES), ['board_generic']);
  assert.deepEqual(Object.keys(BACKGROUNDS), ['mesh_drift', 'colour_block']);
});

test('quiz cards: every option, then the explanation once the wrong options have left', () => {
  const quiz = dynamicTimeline().scenes[2];
  const cards = cardSpecs(quiz);
  assert.deepEqual(cards.map((c) => c.id), ['option:0', 'option:1', 'option:2', 'explanation']);
  assert.equal(cards[3].enter, quiz.quiz.reveal_start + QUIZ_CLEAR + CARD_EXIT);
  for (let i = 1; i < 3; i++) assert.ok(cards[i].enter > cards[i - 1].enter, 'options stagger in');
});

test('a terminal panel enters as a card with its first output line, so its output is read before it collapses', () => {
  const base = dynamicTimeline().scenes[0];
  const scene = /** @type {any} */ ({
    ...base,
    duration: 18,
    side_panel: { panel: { kind: 'terminal', terminal: { command: 'python check.py', output: 'I = 6 / 3\nI = 2.0 A' } }, media: null, show_at: 0 },
    layout: { show_side_panel: true },
  });
  const cards = cardSpecs(scene);
  const term = cards.find((c) => c.kind === 'terminal');
  assert.ok(term && term.enter > 0.5, `the terminal waits for its output (entered at ${term && term.enter})`);
  assert.deepEqual(cards.map((c) => c.enter), [...cards.map((c) => c.enter)].sort((a, b) => a - b), 'entrance order');
  const noOutput = /** @type {any} */ ({ ...scene, side_panel: { ...scene.side_panel, panel: { kind: 'terminal', terminal: { command: 'ls', output: '' } }, show_at: 1.5 } });
  assert.equal(cardSpecs(noOutput).find((c) => c.kind === 'terminal')?.enter, 1.5, 'without output: when the panel opens');
});

test('kicker: a scene-type badge, the chapter (handed over by the stage), the quiz title or the chapter label', () => {
  const tl = dynamicTimeline();
  const chapter = (/** @type {any} */ scene) => chapterTitleAt(tl, scene.start);
  assert.equal(kickerOf(tl.scenes[0], chapter(tl.scenes[0])), 'Basics');
  assert.equal(kickerOf(tl.scenes[1], chapter(tl.scenes[1])), 'Part 2');
  assert.equal(kickerOf(tl.scenes[2], chapter(tl.scenes[2])), 'Quick check');
  assert.equal(kickerOf(tl.scenes[4], chapter(tl.scenes[4])), 'The law');
  assert.equal(kickerOf(/** @type {any} */ ({ ...tl.scenes[0], type: 'example', title: 'A ceiling fan' }), 'Basics'), LABELS.badges.example);
  assert.equal(kickerOf(/** @type {any} */ ({ ...tl.scenes[0], type: 'example', title: 'Worked example - a fan' }), 'Basics'), 'Basics');
  assert.equal(kickerOf(/** @type {any} */ ({ ...tl.scenes[0], title: 'Basics' }), 'Basics'), '', 'never the scene\'s own title again');
  assert.equal(kickerOf(tl.scenes[0], ''), '', 'no chapter, no kicker');
});

test('templates get no timeline: the stage hands each scene its chapter title (the kicker rule, chunks.py hashes it)', async () => {
  /** @type {any[]} */
  const seen = [];
  const build = boardGeneric.build;
  boardGeneric.build = function spy(/** @type {any} */ ctx) {
    seen.push(ctx);
    return build.call(this, ctx);
  };
  try {
    const { stage } = makeStage();
    for (const f of [0, 330, 450, 800, 1000]) await stage.seek(f);
    stage.destroy();
  } finally {
    boardGeneric.build = build;
  }
  assert.deepEqual(seen.map((ctx) => [ctx.index, ctx.chapter]), [[0, 'Basics'], [1, 'The law'], [2, 'The law'], [3, 'The law'], [4, 'The law']]);
  for (const ctx of seen) assert.ok(!('timeline' in ctx), `scene ${ctx.index}: no ctx.timeline (plan §1.4)`);
  const kicker = (/** @type {number} */ i) => seen[i].view.kickerText.textContent;
  assert.deepEqual([0, 1, 2, 3, 4].map(kicker), ['Basics', 'Part 2', 'Quick check', 'The law', 'The law']);
});

test('layout regions keep card text inside the safe zones of both formats (R-59)', () => {
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const zones = ZONES[format];
    for (const variant of /** @type {const} */ (['stack', 'split', 'media'])) {
      for (const titleBottom of [zones.title.y0 + 60, zones.title.y1]) {
        const r = regions(variant, zones, titleBottom);
        if (r.cards) {
          const text = { x: r.cards.x0 + 24, y: r.cards.y0, w: r.cards.x1 - r.cards.x0 - 48, h: r.cards.y1 - r.cards.y0 };
          if (format === 'portrait') {
            // the stack crosses y 840: its text column must fit both boxes
            assert.ok(insideAny({ ...text, h: Math.max(0, 840 - text.y) }, zones.critical) || text.y >= 840, `${format} ${variant}`);
            assert.ok(insideAny({ ...text, y: Math.max(text.y, 840), h: Math.max(0, text.y + text.h - Math.max(text.y, 840)) }, zones.critical), `${format} ${variant}`);
          } else {
            assert.ok(insideAny(text, zones.critical), `${format} ${variant}`);
          }
          assert.ok(r.cards.y1 <= zones.caption.y0, 'cards end above the caption band');
        }
        if (r.media) {
          assert.ok(r.media.x0 >= 0 && r.media.x1 <= zones.width && r.media.y1 <= zones.caption.y0);
        }
      }
    }
    for (const scene of dynamicTimeline(format).scenes) {
      const box = mediaBoxMax(scene, zones);
      assert.ok(box.w > 0 && box.w <= zones.width && box.h > 0 && box.h <= zones.height);
    }
  }
});

test('font stacks: vendored display faces only, Indic families behind every stack', () => {
  const f = fontStacks({ fonts: { display: 'poppins' } });
  assert.match(f.display, /^'Outfit'/, 'Poppins is not vendored yet (B2): Outfit');
  assert.match(f.body, /^'Inter', 'Noto Sans Tamil'/);
  assert.equal(fontStacks({ fonts: { display: 'inter' } }).display.split(',')[0], "'Inter'");
});

test("font stacks put the lecture's own script first (a shared character never pulls in another script's face)", () => {
  assert.match(fontStacks({}, 'hi-IN').body, /^'Inter', 'Noto Sans Devanagari', 'Noto Sans Tamil'/);
  assert.match(fontStacks({}, 'hi').display, /^'Outfit', 'Inter', 'Noto Sans Devanagari'/);
  assert.match(fontStacks({}, 'te-IN').body, /^'Inter', 'Noto Sans Telugu', 'Noto Sans Tamil', 'Noto Sans Devanagari'/);
  assert.match(fontStacks({}, 'ta-IN').body, /^'Inter', 'Noto Sans Tamil', 'Noto Sans Devanagari'/);
  assert.equal(fontStacks({}, 'en-IN').body, fontStacks({}).body, 'no Indic language: the default order');
  for (const lang of ['hi', 'te', 'kn', 'ml', 'ta']) {
    const families = fontStacks({}, lang).body.split(', ');
    assert.equal(new Set(families).size, families.length, `${lang}: every family once`);
    assert.equal(families.length, 7);
  }
  const root = document.createElement('div');
  const tl = dynamicTimeline();
  tl.board_language = 'hi-IN';
  const stage = createDynamicStage(root, tl, { mode: 'render', ...fast });
  assert.match(stage.el.style.getPropertyValue('--dyn-font-body'), /^'Inter', 'Noto Sans Devanagari'/);
  stage.destroy();
});

test('render seek waits for a face its text pulled in, then measures and paints the frame again', async () => {
  const doc = /** @type {any} */ (document);
  /** @type {Record<string, () => void>} */
  const listeners = {};
  const fonts = {
    status: 'loaded',
    ready: Promise.resolve(),
    addEventListener: (/** @type {string} */ name, /** @type {() => void} */ fn) => { listeners[name] = fn; },
    removeEventListener() {},
  };
  Object.defineProperty(doc, 'fonts', { configurable: true, value: fonts });
  const layout = boardGeneric.layout;
  let layouts = 0;
  boardGeneric.layout = function counted(/** @type {any} */ ctx) {
    layouts += 1;
    return layout.call(this, ctx);
  };
  try {
    const { stage } = makeStage();
    await stage.seek(30);
    const before = layouts;
    /** @type {() => void} */
    let release = () => undefined;
    fonts.status = 'loading';
    fonts.ready = new Promise((r) => {
      release = () => {
        fonts.status = 'loaded';
        r(undefined);
      };
    });
    let settled = false;
    const pending = stage.seek(31).then((x) => {
      settled = true;
      return x;
    });
    for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
    assert.equal(settled, false, 'the frame is not done while a face is loading');
    release();
    assert.match((await pending).key, /^[0-9a-f]{16}$/);
    assert.ok(layouts > before, 'measured again with the face in');
    // a face that came in between two seeks (its loadingdone event): the next frame measures again too
    const n = layouts;
    listeners.loadingdone();
    await stage.seek(32);
    assert.ok(layouts > n);
    const m = layouts;
    await stage.seek(33);
    assert.equal(layouts, m, 'nothing changed: no extra measuring');
    stage.destroy();
  } finally {
    boardGeneric.layout = layout;
    delete doc.fonts;
  }
});

// --- the stage --------------------------------------------------------------------------------------------------

test('a classic timeline never reaches the stage', () => {
  const root = document.createElement('div');
  assert.equal(isDynamic(classicTimeline()), false);
  assert.throws(() => createDynamicStage(root, classicTimeline(), { mode: 'render' }), /no Dynamic presentation/);
  assert.equal(root.childElementCount, 0);
});

test('info() is the frame plan; seek() paints a frame and resolves its key', async () => {
  const { stage, timeline, root } = makeStage();
  assert.deepEqual(stage.info(), stageInfo(timeline));
  const r = await stage.seek(0);
  assert.match(r.key, /^[0-9a-f]{16}$/);
  assert.equal(stage.el.style.width, '1920px');
  assert.equal(stage.el.querySelectorAll('.dyn-scene').length, 1);
  await assert.rejects(stage.seek(-1), RangeError);
  await assert.rejects(stage.seek(1200), RangeError);
  await assert.rejects(stage.seek(1.5), RangeError);
  assert.match((await stage.seek(5)).key, /^[0-9a-f]{16}$/, 'a rejected seek does not block the queue');
  stage.destroy();
  assert.equal(root.querySelector('.dyn-stage'), null);
});

test('frames are pure: the same frame gives the same DOM and key whatever was painted before', async () => {
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const frames = [0, 45, 150, 299, 300, 306, 313, 420, 540, 700, 780, 900, 960, 1100, 1199];
    const fresh = {};
    for (const f of frames) {
      const { stage } = makeStage(format);
      fresh[f] = { key: (await stage.seek(f)).key, html: snapshot(stage) };
      stage.destroy();
    }
    const { stage } = makeStage(format);
    for (const f of [...frames].reverse()) {
      const { key } = await stage.seek(f);
      assert.equal(key, fresh[f].key, `${format} frame ${f}: key`);
      assert.equal(snapshot(stage), fresh[f].html, `${format} frame ${f}: DOM`);
    }
    for (const f of frames) assert.equal((await stage.seek(f)).key, fresh[f].key, `${format} frame ${f} again`);
    stage.destroy();
  }
});

test('a crossfade paints the previous scene final state below the incoming one', async () => {
  const { stage } = makeStage();
  await stage.seek(306); // 0.2 s into the chapter card, entered by a 0.45 s crossfade
  const slots = [...stage.el.querySelectorAll('.dyn-scene')];
  assert.deepEqual(slots.map((s) => s.dataset.scene), ['0', '1'], 'in scene order');
  assert.equal(slots[0].style.zIndex, '1');
  assert.equal(slots[1].style.zIndex, '2');
  const op = Number(slots[1].style.opacity);
  assert.ok(op > 0 && op < 1);
  await stage.seek(320);
  assert.deepEqual([...stage.el.querySelectorAll('.dyn-scene')].map((s) => s.dataset.scene), ['1'], 'after the fade only the scene');
  stage.destroy();
});

test('cards enter on their beats and old ones collapse into the "earlier" chip', async () => {
  const { stage } = makeStage();
  const cardOpacity = (id) => Number(stage.el.querySelector(`.dyn-card[data-item="${id}"]`).style.opacity);
  await stage.seek(5); // 0.17 s
  assert.equal(cardOpacity('b2'), 0, 'not revealed yet');
  await stage.seek(60); // 2.0 s: b1 and b2 shown, b3 entering (2.2 - 0.12)
  assert.equal(cardOpacity('b2'), 1);
  await stage.seek(270); // 9 s: five cards shown in turn, three at a time
  assert.equal(cardOpacity('b1'), 0, 'collapsed');
  assert.equal(cardOpacity('b5'), 1);
  assert.equal(stage.el.querySelector('.dyn-chip').textContent, LABELS.earlier(2));
  // the blank step is filled at 6.2 s
  const blank = stage.el.querySelector('.dyn-card[data-item="b4"] .dyn-blank');
  assert.equal(Number(blank.style.opacity), 0);
  // the chip's room beside the kicker is kept only in a scene where a card collapses
  const header = (/** @type {number} */ i) => /** @type {HTMLElement} */ (stage.el.querySelector(`.dyn-scene[data-scene="${i}"] .dyn-header`));
  assert.equal(header(0).classList.contains('is-chipless'), false);
  await stage.seek(900); // the clip scene: no cards, no chip, the kicker takes the whole line
  assert.equal(header(3).classList.contains('is-chipless'), true);
  stage.destroy();
});

test('cards no beat reveals are each shown long enough to read; the chip counts only cards that were seen', async () => {
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const root = document.createElement('div');
    document.body.appendChild(root);
    const tl = dynamicTimeline(format);
    for (const b of tl.scenes[0].beats) {
      b.board_item_id = null;
      b.fill_item_id = null;
    }
    const stage = createDynamicStage(root, tl, { mode: 'render', ...fast });
    /** @type {Map<string, number>} */
    const fullFrom = new Map();
    for (let f = 0; f < 300; f++) {
      await stage.seek(f);
      for (const el of stage.el.querySelectorAll('.dyn-scene[data-scene="0"] .dyn-card')) {
        const card = /** @type {HTMLElement} */ (el);
        if (Number(card.style.opacity) >= 0.999 && !fullFrom.has(String(card.dataset.item))) fullFrom.set(String(card.dataset.item), f);
      }
      const chip = stage.el.querySelector('.dyn-scene[data-scene="0"] .dyn-chip');
      const shown = Number(((chip && chip.textContent) || '').replace(/[^0-9]/g, '') || 0);
      assert.ok(shown <= fullFrom.size, `${format} frame ${f}: the chip counts ${shown} cards, ${fullFrom.size} were seen`);
    }
    assert.deepEqual([...fullFrom.keys()].sort(), ['b1', 'b2', 'b3', 'b4', 'b5'], `${format}: every card is fully shown`);
    // and stays fully shown for MIN_DWELL before it starts to collapse (one card after another, never all at once)
    const starts = [...fullFrom.values()].sort((a, b) => a - b);
    assert.ok(starts.at(-1) - starts[0] > 30 * MIN_DWELL, `${format}: the cards were spread over the scene`);
    stage.destroy();
    root.remove();
  }
});

test('a card the narration reveals still enters on its beat (only cards without an anchor are re-timed)', () => {
  const tl = dynamicTimeline();
  const specs = cardSpecs(tl.scenes[0]);
  assert.deepEqual(specs.map((s) => [s.id, s.anchored]), [['b1', false], ['b2', true], ['b3', true], ['b4', true], ['b5', true]]);
  const portraitMyth = cardSpecs(/** @type {any} */ ({ ...tl.scenes[0], board: [{ id: 'm', kind: 'misconception', text: 'Current is used up', justification: 'It is not' }], beats: [] }), 'portrait');
  assert.deepEqual(portraitMyth.map((s) => [s.id, s.anchored, s.lead || null]), [['m', false, null], ['m:fact', false, 'm']]);
});

test('text on a colour block keeps 3:1 (ink2: the chapter subtitle and kicker) for every R1 family and chapter', () => {
  // resolved by aadhi/compose/presentation.py (content_tokens, structure_tokens(accent_hue(family, chapter)))
  const FAMILIES = {
    midnight_indigo: { ink: '#F3F5F9', ink2: '#B9BEC7', bg: '#10132C', accents: ['#FF936D', '#BBBC22', '#EFA30F'] },
    deep_teal: { ink: '#F3F5F9', ink2: '#B9BEC7', bg: '#001B1C', accents: ['#FF89B0', '#F49F1E', '#FF9277'] },
    paper_ink: { ink: '#141822', ink2: '#484D58', bg: '#FDF2DD', accents: ['#BE3D70', '#A16600', '#C44323'] },
    mint_studio: { ink: '#141822', ink2: '#484D58', bg: '#E4F9F1', accents: ['#4F69D7', '#B24393', '#8C54C3'] },
  };
  for (const [name, f] of Object.entries(FAMILIES)) {
    f.accents.forEach((accent, chapter) => {
      const roles = makeRoles(/** @type {any} */ ({ content: { ink: f.ink, ink2: f.ink2 } }), /** @type {any} */ ({ tokens: { 'struct.accent': accent, bg: f.bg } }));
      const { base, highlight } = blockColors(roles);
      const ink = roles.inkOn([base, highlight]);
      const ink2 = blockInk2(ink, [base, highlight]);
      const worst = Math.min(contrastRatio(ink2, base), contrastRatio(ink2, highlight));
      assert.ok(worst >= 3, `${name} chapter ${chapter}: ink2 ${ink2} on the block is ${worst.toFixed(2)}:1`);
    });
  }
});

test('the template reports the chapter subtitle, a chapter label as critical, and the R-29 weights and label sizes', () => {
  // the template's own entries (jsdom has no layout, so the stage's textBoxes() would drop every box without a size)
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const tl = dynamicTimeline(format);
    const root = document.createElement('div');
    document.body.appendChild(root);
    const chapter = tl.scenes[1];
    const ctx = /** @type {any} */ ({
      scene: chapter, index: 1, chapter: chapterTitleAt(tl, chapter.start), look: chapter.look, roles: makeRoles(tl.presentation, chapter.look),
      zones: ZONES[format], format, mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
      media: () => { throw new Error('no media'); }, onBackground: { ink: '#FFFFFF', ink2: '#EEEEEE', bg: '#CC684E', block: true },
      rectOf: () => null, locale: 'en', rng: () => 0.5,
    });
    ctx.view = boardGeneric.build(ctx);
    boardGeneric.layout(ctx);
    boardGeneric.update(ctx, 3, /** @type {any} */ ({ terminalLines: [], countdownRemaining: null }));
    const texts = boardGeneric.texts(ctx);
    const sub = texts.find((e) => e.role === 'subtitle');
    assert.ok(sub, `${format}: the subtitle is reported`);
    assert.equal(sub.ink, '#EEEEEE');
    assert.equal(sub.critical, true);
    assert.equal(sub.slot, 'title', 'the title scrim remedy covers it');
    assert.equal(sub.size, format === 'portrait' ? 48 : 52);
    assert.equal(sub.weight, format === 'portrait' ? 600 : 400);
    const kicker = texts.find((e) => e.slot === 'kicker');
    assert.ok(kicker && kicker.critical, `${format}: a chapter label is critical`);
    root.remove();
  }
  // card text and the chip
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const tl = dynamicTimeline(format);
    const root = document.createElement('div');
    document.body.appendChild(root);
    const scene = tl.scenes[0];
    const ctx = /** @type {any} */ ({
      scene, index: 0, chapter: chapterTitleAt(tl, scene.start), look: scene.look, roles: makeRoles(tl.presentation, scene.look),
      zones: ZONES[format], format, mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
      media: () => { throw new Error('no media'); }, onBackground: { ink: '#FFFFFF', ink2: '#EEEEEE', bg: '#10132C', block: false },
      rectOf: () => null, locale: 'en', rng: () => 0.5,
    });
    ctx.view = boardGeneric.build(ctx);
    boardGeneric.layout(ctx);
    boardGeneric.update(ctx, 9.5, /** @type {any} */ ({ terminalLines: [], countdownRemaining: null }));
    const texts = boardGeneric.texts(ctx);
    assert.ok(texts.filter((e) => e.role === 'body').every((e) => e.weight === BODY_WEIGHT[format]));
    assert.equal(BODY_WEIGHT.portrait, 600);
    const chip = texts.find((e) => e.role === 'chip');
    assert.ok(chip, `${format}: the chip is on screen at 9.5 s`);
    assert.equal(chip.size, MIN_LABEL[format]);
    assert.ok(MIN_LABEL.landscape >= 40 && MIN_LABEL.portrait >= 44);
    root.remove();
  }
});

test('a table wider than two columns is drawn one line per row in 9:16, as a grid in 16:9', async () => {
  for (const format of /** @type {const} */ (['portrait', 'landscape'])) {
    const root = document.createElement('div');
    document.body.appendChild(root);
    const timeline = dynamicTimeline(format);
    timeline.scenes[0].board.push({ id: 'tb', kind: 'table', headers: ['Quantity', 'Symbol', 'Unit'], rows: [['Voltage', 'V', 'volt'], ['Current', 'I', 'ampere']] });
    const stage = createDynamicStage(root, timeline, { mode: 'render', ...fast });
    await stage.seek(0);
    const card = stage.el.querySelector('.dyn-card[data-item="tb"]');
    if (format === 'portrait') {
      assert.equal(card.querySelector('table'), null);
      assert.deepEqual([...card.querySelectorAll('.dyn-tbl-row')].map((r) => r.textContent), ['Voltage V · volt', 'Current I · ampere']);
      assert.equal(card.querySelector('.dyn-tbl-legend').textContent, 'Quantity Symbol · Unit');
    } else {
      assert.equal(card.querySelectorAll('table tbody tr').length, 2);
    }
    stage.destroy();
  }
});

test('the quiz: countdown ring, then the answer; the hold freezes the background', async () => {
  const { stage, timeline } = makeStage();
  const f0 = 420;
  await stage.seek(f0 + 4 * 30); // countdown (3..8 s)
  const ring = stage.el.querySelector('.dyn-ring');
  assert.equal(ring.style.visibility, 'visible');
  assert.equal(ring.querySelector('.dyn-ring-num').textContent, '4');
  await stage.seek(f0 + 11 * 30);
  assert.equal(stage.el.querySelector('.dyn-ring').style.visibility, 'hidden');
  const opt = (i) => Number(stage.el.querySelector(`.dyn-card[data-item="option:${i}"]`).style.opacity);
  assert.equal(opt(1), 1, 'the correct option stays');
  assert.equal(opt(0), 0, 'wrong options leave');
  assert.equal(Number(stage.el.querySelector('.dyn-card[data-item="explanation"]').style.opacity), 1);
  // a settled hold repeats its key: static-run deduplication (§7.2)
  const k1 = (await stage.seek(f0 + 10 * 30 + 15)).key;
  const k2 = (await stage.seek(f0 + 11 * 30 + 15)).key;
  assert.equal(k1, k2);
  assert.ok(timeline.scenes[2].look.background.frozen_at === 0);
  stage.destroy();
});

test('captions are drawn in the stage and the caption-only layer leaves the page transparent', async () => {
  const { stage } = makeStage();
  await stage.seek(30); // 1 s: "Charge flows like water."
  const cap = stage.el.querySelector('.dyn-captions');
  assert.equal(cap.style.visibility, 'visible');
  assert.equal(cap.textContent, 'Charge flows like water.');
  const full = (await stage.seek(30)).key;
  stage.setLayers({ stage: false, captions: true });
  assert.ok(stage.el.classList.contains('is-captions-only'));
  const capOnly = (await stage.seek(30)).key;
  assert.notEqual(capOnly, full);
  assert.equal(stage.el.querySelector('.dyn-scenes').style.visibility, 'hidden');
  assert.equal((await stage.seek(31)).key, capOnly, 'the same caption: the same caption-layer key');
  assert.notEqual((await stage.seek(80)).key, capOnly);
  stage.setLayers({ stage: true, captions: false });
  await stage.seek(30);
  assert.equal(stage.el.querySelector('.dyn-captions').style.visibility, 'hidden');
  assert.deepEqual(stage.layers(), { stage: true, captions: false });
  stage.destroy();
});

test('render mode follows presentation.captions_burn until the worker sets the layers', () => {
  const root = document.createElement('div');
  const tl = dynamicTimeline();
  tl.presentation.captions_burn = false;
  const stage = createDynamicStage(root, tl, { mode: 'render', ...fast });
  assert.deepEqual(stage.layers(), { stage: true, captions: false });
  stage.destroy();
});

test('textBoxes: stage-px boxes with role, ink, background and the critical flag of fully shown text', async (t) => {
  // jsdom has no layout: every element gets a 100 x 50 box at the origin (the stage itself 1920 wide, scale 1)
  const proto = window.HTMLElement.prototype;
  const original = proto.getBoundingClientRect;
  proto.getBoundingClientRect = function rect() {
    const w = this.classList && this.classList.contains('dyn-stage') ? 1920 : 100;
    return /** @type {any} */ ({ left: 0, top: 0, width: w, height: 50, right: w, bottom: 50, x: 0, y: 0 });
  };
  t.after(() => {
    proto.getBoundingClientRect = original;
  });
  const { stage } = makeStage();
  await stage.seek(30); // 1 s: two cards and a caption
  const boxes = stage.textBoxes();
  const roles = new Set(boxes.map((b) => b.role));
  assert.ok(roles.has('title') && roles.has('body') && roles.has('caption') && roles.has('label'));
  for (const b of boxes) {
    for (const k of ['x', 'y', 'w', 'h', 'role', 'critical', 'ink', 'bg', 'overflow']) assert.ok(k in b, k);
    assert.match(b.ink, /^#[0-9A-F]{6}$/i);
    assert.equal(b.scene, 0);
    assert.deepEqual([b.x, b.y, b.w, b.h], [0, 0, 100, 50]);
  }
  const title = boxes.find((b) => b.role === 'title');
  assert.ok(title.critical);
  assert.deepEqual([title.slot, title.size, title.min, title.weight, title.text, title.overflow], ['title', 96, 72, 700, 'Voltage and current', 0]);
  const caption = boxes.find((b) => b.role === 'caption');
  assert.ok(caption.critical);
  assert.deepEqual([caption.slot, caption.size, caption.min], ['caption', 48, 48]);
  assert.equal(boxes.find((b) => b.role === 'label').critical, false);
  const body = boxes.find((b) => b.role === 'body');
  assert.deepEqual([body.slot, body.size, body.min], ['body', 52, 39]);
  assert.equal(body.text, 'Charge flows like water');
  await stage.seek(66); // 2.2 s: b3 is entering (fading and rising): not checked as critical yet
  const bodies = stage.textBoxes().filter((b) => b.role === 'body');
  assert.equal(bodies.length, 3);
  const entering = bodies.filter((b) => b.alpha < 0.99);
  assert.equal(entering.length, 1);
  assert.equal(entering[0].critical, false);
  stage.destroy();
});

test("preflight's remedies: the safe variant, a slot's size cap and the scrim (§7.12)", async () => {
  const root = document.createElement('div');
  document.body.appendChild(root); // attached: a detached stage cannot measure, so it never lays out
  const tl = dynamicTimeline();
  tl.scenes[0].look.variant = 'safe';
  tl.scenes[0].look.text_fit = { title: { size: 60, scrim: true }, body: { size: 44 } };
  const stage = createDynamicStage(root, tl, { mode: 'render', ...fast });
  await stage.seek(270);
  const block = stage.el.querySelector('.dyn-titleblock');
  assert.ok(block.classList.contains('has-scrim'));
  assert.equal(stage.el.querySelector('.dyn-title').style.fontSize, '60px', 'capped by text_fit.title.size');
  assert.equal(stage.el.querySelector('.dyn-cards').style.getPropertyValue('--body'), '44px');
  const shown = [...stage.el.querySelectorAll('.dyn-card')].filter((c) => Number(c.style.opacity) > 0);
  assert.equal(shown.length, 1, 'the safe variant shows one card at a time');
  stage.destroy();
});

test('slotTuning never goes below the R-29 floors', () => {
  const t = slotTuning(/** @type {any} */ ({ text_fit: { title: { size: 10 }, body: { size: 'x' } } }), 'portrait');
  assert.equal(t.titleSpec({ max: 88, min: 56, lines: 2, lh: 1 }).max, 40);
  assert.equal(t.bodyPx(56), 56);
  assert.equal(slotTuning(null, 'landscape').safe, false);
});

test('mediaPlan comes from the timeline and the template boxes', () => {
  const { stage } = makeStage();
  const plan = stage.mediaPlan(780, 960);
  assert.equal(plan.length, 1);
  assert.equal(plan[0].frames.length, 120);
  stage.destroy();
});

test('live mode: update(t) paints any time, lays scenes out and asks for a repaint', async () => {
  const root = document.createElement('div');
  let invalidated = 0;
  const stage = createDynamicStage(root, dynamicTimeline(), { mode: 'live', onInvalidate: () => invalidated++ });
  const k = stage.update(5);
  assert.match(k, /^[0-9a-f]{16}$/);
  assert.equal(stage.update(5), k, 'pure');
  stage.update(1e9);
  stage.update(-3);
  await new Promise((r) => setImmediate(r));
  assert.ok(invalidated >= 1);
  stage.update(28); // the clip scene plays a <video> live
  assert.ok(stage.el.querySelector('video'));
  stage.destroy();
});

// --- rich-lite quiz questions (TeX, bold, terms, code) ------------------------------------------------------------

const RICH_QUESTION = 'If $V = IR$ and **R** doubles, what happens to the [[current]] `I`?';

/** A TeX converter for the tests: a marked element per TeX string; resolves once `gate` does. */
function fakeTex(/** @type {Promise<unknown>} */ gate = Promise.resolve()) {
  /** @type {string[]} */
  const seen = [];
  /** @param {string} tex @param {boolean} display */
  const renderTex = async (tex, display) => {
    seen.push(tex);
    await gate;
    const el = document.createElement('span');
    el.className = 'fake-tex';
    el.dataset.tex = tex;
    el.dataset.display = String(display);
    el.textContent = 'TEX';
    return el;
  };
  return { renderTex, seen };
}

/** @param {any} tl @param {any} [opts] */
function stageOf(tl, opts = {}) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const stage = createDynamicStage(root, tl, { mode: 'render', ...fast, ...opts });
  return { root, stage };
}

test('a quiz question keeps its rich-lite markup: TeX, bold, a [[term]] and code, word by word', async () => {
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const tl = dynamicTimeline(format);
    tl.scenes[2].quiz.question = RICH_QUESTION;
    const tex = fakeTex();
    const { root, stage } = stageOf(tl, { renderTex: tex.renderTex });
    await stage.seek(Math.round((14 + 2.5) * 30)); // 2.5 s into the quiz: the question is in
    const title = /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="2"] .dyn-title'));
    const text = title.textContent || '';
    for (const literal of ['$', '**', '[[', ']]', '`']) assert.ok(!text.includes(literal), `${format}: no literal ${literal} in "${text}"`);
    assert.deepEqual(tex.seen, ['V = IR'], 'the TeX went through the stage renderer (the render page converted it before ready)');
    const math = /** @type {HTMLElement} */ (title.querySelector('.rt-math'));
    assert.ok(math && !math.classList.contains('is-pending'), `${format}: the TeX is converted`);
    assert.equal(/** @type {HTMLElement} */ (math.querySelector('.fake-tex')).dataset.tex, 'V = IR');
    assert.equal(math.dataset.display, '0', 'inline TeX');
    assert.equal(title.querySelector('.rt-b')?.textContent, 'R');
    assert.equal(title.querySelector('.rt-k')?.textContent, 'current');
    assert.equal(title.querySelector('code.rt-code')?.textContent, 'I');
    // one word span per word of the plain text, a TeX or code span counting as one unit with what touches it
    const words = [...title.querySelectorAll('.dyn-word')];
    assert.deepEqual(words.map((w) => (w.textContent || '').replace('TEX', '{tex}')),
      ['If', '{tex}', 'and', 'R', 'doubles,', 'what', 'happens', 'to', 'the', 'current', 'I?']);
    assert.ok(words[1].querySelector('.rt-math') && words[10].querySelector('code.rt-code'));
    assert.ok(words.every((w) => w.parentElement === title || w.parentElement?.closest('.dyn-title') === title));
    assert.ok(words.every((w) => Number(w.style.opacity) === 1), 'all in after 2.5 s');
    // the entrance is still word by word: early on the first word is in, the last not yet
    await stage.seek(Math.round((14 + 0.2) * 30));
    const early = [...stage.el.querySelectorAll('.dyn-scene[data-scene="2"] .dyn-title .dyn-word')].map((w) => Number(/** @type {HTMLElement} */ (w).style.opacity));
    assert.ok(early[0] > 0 && early[10] === 0, `${format}: words enter one after another (${early.join(', ')})`);
    stage.destroy();
    root.remove();
  }
});

test('the render page waits for a quiz question\'s TeX before the frame is done (as it does for the cards)', async () => {
  const tl = dynamicTimeline();
  tl.scenes[2].quiz.question = RICH_QUESTION;
  /** @type {() => void} */
  let release = () => undefined;
  const tex = fakeTex(new Promise((r) => { release = () => r(undefined); }));
  const { root, stage } = stageOf(tl, { renderTex: tex.renderTex });
  let settled = false;
  const pending = stage.seek(Math.round((14 + 2.5) * 30)).then((x) => {
    settled = true;
    return x;
  });
  for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  assert.equal(settled, false, 'the frame waits for the question\'s TeX');
  release();
  assert.match((await pending).key, /^[0-9a-f]{16}$/);
  assert.ok(stage.el.querySelector('.dyn-title .rt-math .fake-tex'), 'and shows it once converted');
  stage.destroy();
  root.remove();
});

test('the title of a rich question is reported as its plain text; a plain one is drawn as before', () => {
  for (const [question, plain] of [
    [RICH_QUESTION, 'If V = IR and R doubles, what happens to the current I?'],
    ['We double the voltage. What happens to the current?', 'We double the voltage. What happens to the current?'],
    ['Is it worth \\$5 at 2 * 3 A?', 'Is it worth $5 at 2 * 3 A?'], // an escape shows as the character (renderRich)
  ]) {
    const tl = dynamicTimeline();
    const scene = tl.scenes[2];
    scene.quiz.question = question;
    const root = document.createElement('div');
    document.body.appendChild(root);
    const ctx = /** @type {any} */ ({
      scene, index: 2, chapter: chapterTitleAt(tl, scene.start), look: scene.look, roles: makeRoles(tl.presentation, scene.look), zones: ZONES.landscape,
      format: 'landscape', mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
      media: () => { throw new Error('no media'); }, onBackground: { ink: '#F3F5F9', ink2: '#B9BEC7', bg: '#181D37', block: false },
      rectOf: () => null, locale: 'en', rng: () => 0.5, renderTex: fakeTex().renderTex,
    });
    ctx.view = boardGeneric.build(ctx);
    boardGeneric.layout(ctx);
    boardGeneric.update(ctx, 3, /** @type {any} */ ({ terminalLines: [], countdownRemaining: 5 }));
    const title = boardGeneric.texts(ctx).find((e) => e.role === 'title');
    assert.equal(title?.text, plain);
    if (question !== RICH_QUESTION) {
      // the plain path: exactly the word spans of the plain text, nothing else inside the title
      assert.deepEqual(ctx.view.words.map((/** @type {HTMLElement} */ w) => w.textContent), splitWords(plain, 'en').map((w) => w.text));
      assert.equal(ctx.view.title.querySelector('.rt-b, .rt-k, .rt-math, code'), null);
    }
    root.remove();
  }
});

test('a TeX word wider than the column even at the minimum size holds the question there; its overflow is measured there', () => {
  // jsdom lays nothing out: the title column is COLUMN px wide, every word one line high, a TeX word `perPx` x the
  // font size wide (a plain word 2 x: it always fits). The title's line count fits at every size.
  const COLUMN = 900;
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const spec = format === 'landscape' ? { max: 76, min: 56 } : { max: 68, min: 50 }; // LAYOUT[format].titleQuiz
    for (const [perPx, size, overflow, why] of [
      [20, spec.min, 20 * spec.min - COLUMN, 'wider than the column at the minimum: held there, overflow at that size'],
      // 14 x size: too wide at 76/72/68 (16:9) and 68 (9:16), fits at 64 (896 px) in both; avoidable at the minimum
      [14, 64, 0, 'too wide only when large: the largest size it fits at, no overflow'],
    ]) {
      const tl = dynamicTimeline(format);
      const scene = tl.scenes[2];
      scene.quiz.question = 'Which is larger, $\\frac{a}{b}$ or 1?';
      const root = document.createElement('div');
      document.body.appendChild(root);
      const ctx = /** @type {any} */ ({
        scene, index: 2, chapter: chapterTitleAt(tl, scene.start), look: scene.look, roles: makeRoles(tl.presentation, scene.look), zones: ZONES[format],
        format, mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
        media: () => { throw new Error('no media'); }, onBackground: { ink: '#F3F5F9', ink2: '#B9BEC7', bg: '#181D37', block: false },
        rectOf: () => null, locale: 'en', rng: () => 0.5, renderTex: fakeTex().renderTex,
      });
      ctx.view = boardGeneric.build(ctx);
      const v = ctx.view;
      assert.deepEqual([v.titleSpec.max, v.titleSpec.min], [spec.max, spec.min], `${format}: the quiz title spec`);
      assert.equal(v.wordTex.filter(Boolean).length, 1, 'one TeX word');
      const px = () => Number.parseFloat(v.title.style.fontSize) || 0;
      const line = () => px() * v.titleSpec.lh;
      Object.defineProperty(v.title, 'clientWidth', { configurable: true, get: () => COLUMN });
      Object.defineProperty(v.title, 'offsetHeight', { configurable: true, get: line });
      v.words.forEach((/** @type {HTMLElement} */ w, /** @type {number} */ i) => {
        Object.defineProperty(w, 'offsetWidth', { configurable: true, get: () => (v.wordTex[i] ? perPx : 2) * px() });
        Object.defineProperty(w, 'offsetHeight', { configurable: true, get: line });
      });
      boardGeneric.layout(ctx);
      assert.equal(v.titleSize, size, `${format}, ${why}: size`);
      assert.equal(px(), size, `${format}: drawn at that size`);
      assert.equal(v.titleOverflow, overflow, `${format}, ${why}: overflow`);
      const title = boardGeneric.texts(ctx).find((e) => e.role === 'title');
      assert.equal(title?.size, size);
      assert.equal(title?.overflow, overflow, 'what preflight reads (at the floor: blocked, no remedy round)');
      root.remove();
    }
  }
});

// --- preflight's contrast remedy: the title block's scrim ----------------------------------------------------------

/**
 * The chapter card (title variant: its kicker is critical) built by the template alone, with `textFit` as the look's
 * remedies and a background whose colours are `bgs` (the worst against the ink first).
 * @param {Record<string, any> | null} textFit @param {string[]} bgs @param {string} ink2
 */
function chapterTexts(textFit, bgs, ink2) {
  const tl = dynamicTimeline();
  const scene = tl.scenes[1];
  if (textFit) scene.look.text_fit = textFit;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctx = /** @type {any} */ ({
    scene, index: 1, chapter: chapterTitleAt(tl, scene.start), look: scene.look, roles: makeRoles(tl.presentation, scene.look), zones: ZONES.landscape,
    format: 'landscape', mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
    media: () => { throw new Error('no media'); }, onBackground: { ink: '#F3F5F9', ink2, bg: bgs[0], bgs, block: false },
    rectOf: () => null, locale: 'en', rng: () => 0.5,
  });
  ctx.view = boardGeneric.build(ctx);
  boardGeneric.layout(ctx);
  boardGeneric.update(ctx, 3, /** @type {any} */ ({ terminalLines: [], countdownRemaining: null }));
  const texts = boardGeneric.texts(ctx);
  root.remove();
  return { texts, view: ctx.view, ctx };
}

test('a scrim for the kicker, the title or the subtitle puts one scrim behind the whole title block', () => {
  assert.deepEqual([...TITLE_BLOCK_SLOTS], ['kicker', 'title']);
  const bgs = ['#5A6070', '#10132C'];
  // preflight plans its remedies per slot texts() reports (§7.12): the title block's slots are exactly the slots its
  // text is reported in (the subtitle in slot 'title'), so none is listed that preflight could never plan a scrim for
  const { texts: reported, view: card } = chapterTexts(null, bgs, '#B9BEC7');
  const inBlock = reported.filter((e) => card.titleBlock.contains(e.el));
  assert.deepEqual(inBlock.map((e) => e.role), ['label', 'title', 'subtitle'], 'the kicker, the title and the subtitle');
  assert.deepEqual(inBlock.map((e) => e.slot), ['kicker', 'title', 'title'], 'the subtitle is remedied through slot title');
  assert.deepEqual([...new Set(inBlock.map((e) => e.slot))], [...TITLE_BLOCK_SLOTS]);
  for (const slot of TITLE_BLOCK_SLOTS) {
    const { view } = chapterTexts({ [slot]: { scrim: true } }, bgs, '#B9BEC7');
    assert.ok(view.titleBlock.classList.contains('has-scrim'), `a ${slot} scrim`);
    assert.equal(view.titleBlock.style.getPropertyValue('--scrim'), `#10132C${SCRIM_ALPHA_HEX}`, 'the scene background at 78%');
  }
  for (const fit of [null, { body: { scrim: true } }, { kicker: { size: 40 } }, { title: { scrim: false } }, { subtitle: { scrim: true } }]) {
    const { view, texts } = chapterTexts(fit, bgs, '#B9BEC7');
    assert.equal(view.titleBlock.classList.contains('has-scrim'), false, JSON.stringify(fit));
    assert.ok(texts.filter((e) => e.slot === 'kicker' || e.slot === 'title').every((e) => e.bg === bgs[0]), 'no scrim: the background');
  }
});

test('text over the scrim is reported on the scrim, so preflight measures the remedy and reports it fixed (R-53)', () => {
  // a kicker (40 px, weight 600: R-53 asks 3:1 of large text) in a grey too close to the background's lightest colour
  const ink2 = '#8C919B';
  const bgs = ['#5A6070', '#10132C', '#3A3F55'];
  const before = chapterTexts(null, bgs, ink2).texts.find((e) => e.slot === 'kicker');
  assert.ok(before && before.critical, 'a chapter label is critical');
  assert.equal(before.bg, '#5A6070');
  assert.ok(contrastRatio(ink2, before.bg) < 3, `the finding: ${contrastRatio(ink2, before.bg).toFixed(2)}:1`);
  const { texts } = chapterTexts({ kicker: { scrim: true } }, bgs, ink2);
  const after = texts.find((e) => e.slot === 'kicker');
  // the scrim (#10132C at 0xc7) over the background colour that is worst for the ink; the value aadhi/compose/dynamic/
  // colour.py gives: to_hex(composite(parse_colour('#10132Cc7'), parse_colour('#5A6070')))
  assert.equal(after?.bg, '#20243b');
  assert.ok(contrastRatio(ink2, /** @type {string} */ (after?.bg)) >= 3, 'the second pass measures the scrim: fixed');
  // the title and the subtitle are in the block too: on the scrim, each against its own ink's worst case
  const title = texts.find((e) => e.role === 'title');
  const sub = texts.find((e) => e.role === 'subtitle');
  assert.equal(title?.bg, scrimmedBg(`#10132C${SCRIM_ALPHA_HEX}`, { bg: bgs[0], bgs }, '#F3F5F9'));
  assert.equal(sub?.bg, after?.bg, 'the subtitle shares the kicker\'s ink (ink2)');
  for (const e of [after, title, sub]) assert.match(String(e?.bg), /^#[0-9a-f]{6}$/i, 'opaque: preflight can measure it');
});

test('scrimmedBg: the scrim over each background colour, the worst one for the ink', () => {
  const scrim = '#10132Cc7';
  const bgs = ['#10132C', '#5A6070', '#3A3F55'];
  const got = scrimmedBg(scrim, { bg: bgs[0], bgs }, '#F3F5F9');
  const all = bgs.map((b) => composite(scrim, b));
  assert.ok(all.includes(got));
  assert.equal(Math.min(...all.map((c) => contrastRatio('#F3F5F9', c))), contrastRatio('#F3F5F9', got));
  assert.equal(scrimmedBg(scrim, { bg: '#5A6070' }, '#F3F5F9'), '#20243b', 'without bgs: the declared worst background');
});

test('the stage passes every background colour to the template: textBoxes reports the kicker on its scrim', async (t) => {
  const proto = window.HTMLElement.prototype;
  const original = proto.getBoundingClientRect;
  proto.getBoundingClientRect = function rect() {
    const w = this.classList && this.classList.contains('dyn-stage') ? 1920 : 100;
    return /** @type {any} */ ({ left: 0, top: 0, width: w, height: 50, right: w, bottom: 50, x: 0, y: 0 });
  };
  t.after(() => {
    proto.getBoundingClientRect = original;
  });
  const kickerAt = async (/** @type {any} */ fit) => {
    const tl = dynamicTimeline();
    if (fit) tl.scenes[1].look.text_fit = fit;
    const { root, stage } = stageOf(tl);
    await stage.seek(12 * 30); // the chapter card (a colour block), settled
    const box = stage.textBoxes().find((b) => b.slot === 'kicker');
    stage.destroy();
    root.remove();
    return box;
  };
  const plain = await kickerAt(null);
  const scrimmed = await kickerAt({ kicker: { scrim: true } });
  // what the stage computes: the block's colours (colour_block) and the ink2 on them (blockInk2)
  const tl = dynamicTimeline();
  const roles = makeRoles(tl.presentation, tl.scenes[1].look);
  const { base, highlight } = blockColors(roles);
  const ink2 = blockInk2(roles.inkOn([base, highlight]), [base, highlight]);
  assert.equal(scrimmed.ink, ink2);
  assert.equal(scrimmed.bg, scrimmedBg(`${roles.tokens.bg}${SCRIM_ALPHA_HEX}`, { bg: base, bgs: [base, highlight] }, ink2));
  assert.notEqual(scrimmed.bg, plain.bg);
  assert.ok(contrastRatio(ink2, scrimmed.bg) > contrastRatio(ink2, plain.bg));
});

// --- the quiz countdown ring and the question ----------------------------------------------------------------------

const LONG_QUESTION = 'A circuit runs on a 12 V battery and we swap it for a 24 V battery while the resistance stays exactly the same: what happens to the current that flows through the lamp?';

test('16:9: the title block keeps clear of the countdown ring, so a long question or kicker never runs under it', async () => {
  const tl = dynamicTimeline('landscape');
  tl.scenes[2].quiz.question = LONG_QUESTION;
  tl.scenes[2].title = 'A very long checkpoint title that would reach the right edge of the frame';
  for (const fit of [null, { title: { scrim: true } }]) {
    if (fit) tl.scenes[2].look.text_fit = fit;
    const { root, stage } = stageOf(tl);
    await stage.seek((14 + 4) * 30); // the countdown runs (3..8 s)
    const scene = /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="2"]'));
    const block = /** @type {HTMLElement} */ (scene.querySelector('.dyn-titleblock'));
    const ring = /** @type {HTMLElement} */ (scene.querySelector('.dyn-ring'));
    assert.equal(ring.style.visibility, 'visible');
    const px = (/** @type {string} */ v) => Number.parseFloat(v);
    const ringLeft = px(ring.style.left);
    const zone = ZONES.landscape.critical[0];
    assert.equal(ringLeft + RING_SIZE, zone.x1, 'the ring at the right of the content box');
    // the block's right edge (its scrim's margin-left: -24 px only moves it further left)
    const right = px(block.style.left) + px(block.style.width);
    assert.ok(right <= ringLeft - RING_GAP, `title block ends at ${right}, the ring starts at ${ringLeft}`);
    assert.equal(px(block.style.left), zone.x0);
    assert.ok(block.contains(scene.querySelector('.dyn-header')) && block.contains(scene.querySelector('.dyn-title')), 'kicker and question');
    stage.destroy();
    root.remove();
  }
  // a scene without a ring keeps the full width
  const { root, stage } = stageOf(dynamicTimeline('landscape'));
  await stage.seek(5 * 30);
  const block = /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="0"] .dyn-titleblock'));
  assert.equal(block.style.width, `${ZONES.landscape.title.x1 - ZONES.landscape.title.x0}px`);
  stage.destroy();
  root.remove();
});

test('9:16: the ring sits below the question, right of the option column; the title keeps the whole column', async () => {
  const tl = dynamicTimeline('portrait');
  tl.scenes[2].quiz.question = LONG_QUESTION;
  const { root, stage } = stageOf(tl);
  await stage.seek((14 + 4) * 30);
  const scene = /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="2"]'));
  const block = /** @type {HTMLElement} */ (scene.querySelector('.dyn-titleblock'));
  const ring = /** @type {HTMLElement} */ (scene.querySelector('.dyn-ring'));
  const cards = /** @type {HTMLElement} */ (scene.querySelector('.dyn-cards'));
  const z = ZONES.portrait;
  assert.equal(block.style.width, `${z.title.x1 - z.title.x0}px`);
  assert.equal(Number.parseFloat(ring.style.left), z.textColumn.x1 + 32, 'right of the column (x 780 + 32)');
  assert.ok(Number.parseFloat(ring.style.left) >= Number.parseFloat(cards.style.left) + Number.parseFloat(cards.style.width), 'beside the options, not on them');
  assert.equal(ring.style.top, cards.style.top, 'level with the first option, below the question');
  assert.ok(Number.parseFloat(ring.style.left) + RING_SIZE / 2 < 888, 'its number left of the 9:16 safe edge (x 888)');
  stage.destroy();
  root.remove();
});

test('the countdown number is reported as a label (not critical) at the R-29 label size while it shows', () => {
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    const tl = dynamicTimeline(format);
    const scene = tl.scenes[2];
    const root = document.createElement('div');
    document.body.appendChild(root);
    const ctx = /** @type {any} */ ({
      scene, index: 2, chapter: chapterTitleAt(tl, scene.start), look: scene.look, roles: makeRoles(tl.presentation, scene.look), zones: ZONES[format],
      format, mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
      media: () => { throw new Error('no media'); }, onBackground: { ink: '#F3F5F9', ink2: '#B9BEC7', bg: '#181D37', block: false },
      rectOf: () => null, locale: 'en', rng: () => 0.5,
    });
    ctx.view = boardGeneric.build(ctx);
    boardGeneric.layout(ctx);
    boardGeneric.update(ctx, 5, /** @type {any} */ ({ terminalLines: [], countdownRemaining: 3 }));
    const ring = boardGeneric.texts(ctx).find((e) => e.slot === 'ring');
    assert.ok(ring, `${format}: the number is reported`);
    assert.deepEqual([ring.role, ring.critical, ring.text, ring.size, ring.weight, ring.ink], ['label', false, '3', MIN_LABEL[format], 700, '#F3F5F9']);
    assert.ok(Number(ring.alpha) > 0.99);
    boardGeneric.update(ctx, 11, /** @type {any} */ ({ terminalLines: [], countdownRemaining: null }));
    assert.equal(boardGeneric.texts(ctx).find((e) => e.slot === 'ring'), undefined, `${format}: gone after the reveal`);
    root.remove();
  }
  // the stylesheet draws it at that size: the label floor of each format (R-29)
  const css = readFileSync(new URL('../../css/dynamic.css', import.meta.url), 'utf8');
  assert.match(css, /\.dyn-ring-num \{[^}]*font-size: var\(--min-label, 40px\)/);
  assert.match(css, /\.dyn-board--portrait \{[^}]*--min-label: 44px/);
});

// --- light families (paper_ink, mint_studio): no dark literal colour on the stage -----------------------------------

const LIGHT = {
  content: { ink: '#141822', ink2: '#484D58', 'entity:0': '#0267C7', 'entity:1': '#80369E', 'entity:2': '#870C62', warn: '#9F4503', ok: '#016F50', mark: '#F2D935', focus: '#141822' },
  // aadhi/compose/presentation.py structure_tokens(paper_ink, its first accent)
  tokens: { 'struct.accent': '#BE3D70', bg: '#FDF2DD', 'bg-alt': '#F3E8D3', 'bg-tint': '#FFEAEF', surface: '#FFFDFA' },
};

/** The fixture in the light paper_ink family. @param {'landscape' | 'portrait'} [format] */
function lightTimeline(format = 'landscape') {
  const tl = dynamicTimeline(format);
  tl.presentation.content = { ...LIGHT.content };
  for (const s of tl.scenes) s.look.tokens = { ...LIGHT.tokens };
  return tl;
}

test('light family: the stage, each scene and the ring track take their colours from the roles', async () => {
  const { root, stage } = stageOf(lightTimeline());
  assert.equal(stage.el.style.getPropertyValue('--dyn-ink'), '#141822');
  assert.equal(stage.el.style.getPropertyValue('--dyn-page'), '#FDF2DD');
  await stage.seek((14 + 4) * 30); // the quiz (background tone bg-alt) during its countdown
  const scene = /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="2"]'));
  assert.equal(scene.style.getPropertyValue('--dyn-page'), LIGHT.tokens['bg-alt'], 'the matte around media: the scene\'s own page');
  assert.equal(scene.style.getPropertyValue('--dyn-ink'), '#141822');
  const track = /** @type {SVGElement} */ (scene.querySelector('.dyn-ring-track'));
  assert.equal(track.getAttribute('stroke'), '#141822');
  assert.equal(Number(track.getAttribute('stroke-opacity')), RING_TRACK_ALPHA.darkInk);
  // the track holds a visible step off the light page; the old white at 18% was invisible on it
  const page = LIGHT.tokens['bg-alt'];
  const drawn = composite(`#141822${Math.round(RING_TRACK_ALPHA.darkInk * 255).toString(16)}`, page);
  assert.ok(contrastRatio(drawn, page) >= 1.6, `track ${contrastRatio(drawn, page).toFixed(2)}:1`);
  assert.ok(contrastRatio(composite('#FFFFFF2e', page), page) < 1.05, 'the literal it replaces');
  assert.ok(Math.abs(toOklch(drawn).l - toOklch(page).l) > 0.15);
  stage.destroy();
  root.remove();
  // dark family: ink at 18% (as before, within a level of the old white)
  const dark = stageOf(dynamicTimeline());
  await dark.stage.seek((14 + 4) * 30);
  const darkTrack = /** @type {SVGElement} */ (dark.stage.el.querySelector('.dyn-scene[data-scene="2"] .dyn-ring-track'));
  assert.equal(darkTrack.getAttribute('stroke'), '#F3F5F9');
  assert.equal(Number(darkTrack.getAttribute('stroke-opacity')), RING_TRACK_ALPHA.lightInk);
  assert.equal(dark.stage.el.style.getPropertyValue('--dyn-page'), '#10132C');
  dark.stage.destroy();
  dark.root.remove();
});

test('dynamic.css: no literal colour but neutral shadows and the captions; the stage, matte and ring come from roles', () => {
  const css = readFileSync(new URL('../../css/dynamic.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  /** @type {Map<string, string>} */
  const rules = new Map();
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) rules.set(m[1].trim(), m[2]);
  const decl = (/** @type {string} */ sel, /** @type {string} */ prop) => {
    const body = rules.get(sel) || '';
    const m = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`).exec(body);
    return m ? m[1].trim() : null;
  };
  assert.equal(decl('.dyn-stage', 'color'), 'var(--dyn-ink)');
  assert.equal(decl('.dyn-stage', 'background'), 'var(--dyn-page)');
  assert.equal(decl('.dyn-media', 'background'), 'var(--dyn-page)');
  assert.equal(decl('.dyn-ring-track', 'stroke'), null, 'the track colour is the ink, set by the template');
  const LITERAL = /#[0-9a-f]{3,8}\b|rgba?\(/gi;
  for (const [sel, body] of rules) {
    for (const d of body.split(';')) {
      const [prop] = d.split(':');
      if (!LITERAL.test(d)) continue;
      LITERAL.lastIndex = 0;
      const shadow = /^\s*box-shadow\s*$/.test(prop);
      const caption = /\.dyn-caption/.test(sel);
      assert.ok(shadow || caption, `literal colour outside shadows and captions: ${sel} { ${d.trim()} }`);
    }
  }
});

// --- the live presenter clip follows the player's rate -------------------------------------------------------------

test('syncClip plays a live clip at the player\'s rate (1x without one)', () => {
  const v = document.createElement('video');
  v.setAttribute('src', '/media/aadhi.mp4');
  syncClip(v, 1, true, { playing: true, rate: 1.5, seeked: true });
  assert.equal(v.playbackRate, 1.5);
  assert.equal(v.paused, false);
  syncClip(v, 1.2, true, { playing: true, rate: 0.75 });
  assert.equal(v.playbackRate, 0.75);
  syncClip(v, 1.3, true, { playing: true });
  assert.equal(v.playbackRate, 1);
  syncClip(v, 1.3, false, { playing: false, rate: 2 });
  assert.equal(v.playbackRate, 2);
  assert.equal(v.paused, true);
});

test('live presenter: the clip plays at the player\'s rate, so it neither drifts nor is re-sought at 1.5x', () => {
  const tl = dynamicTimeline();
  tl.scenes[0].look.presenter = { clip_key: 'aadhi_left_clean.mp4', rect: [0, 0, 720, 720], t_exit: 6, src_fps: 24, url: '/media/aadhi_left.mp4' };
  const root = document.createElement('div');
  document.body.appendChild(root);
  const stage = createDynamicStage(root, tl, { mode: 'live' });
  stage.update(1, { playing: true, rate: 1.5, seeked: true });
  const v = /** @type {HTMLVideoElement} */ (stage.el.querySelector('.dyn-presenter video'));
  assert.ok(v && v.muted);
  assert.equal(v.playbackRate, 1.5);
  assert.equal(v.paused, false);
  // played on at 1.5x for 0.5 s of stage time: the clip moved as far, so no re-seek
  v.currentTime = 1.75;
  stage.update(1.75, { playing: true, rate: 1.5 });
  assert.equal(v.currentTime, 1.75);
  stage.update(2, { playing: true, rate: 1 });
  assert.equal(v.playbackRate, 1);
  stage.destroy();
  root.remove();
});

// --- B2a: an engine_version 2 look draws its stored tokens, a B1 look the B1 derivation (plan §1.2, §1.9) ------------

/** A colour as the page holds it, normalised (jsdom writes a colour property as rgb()). @param {unknown} c */
const norm = (c) => toHex(String(c).trim()).toLowerCase();

/**
 * The fixture in one palette slot: its content inks, `tokens` on every scene, and a title-block scrim (preflight's
 * remedy, through the fixes channel) on the chapter card, which stands on a colour block.
 * @param {Record<string, string>} content @param {Record<string, string>} tokens
 */
function paletteTimeline(content, tokens) {
  const tl = dynamicTimeline();
  tl.presentation.content = { ...content };
  for (const s of tl.scenes) s.look.tokens = { ...tokens };
  tl.scenes[1].look.fixes = { safe: false, slots: { title: { scrim: true } } };
  return tl;
}

/**
 * What the settled chapter card draws: its block's colours (the background's colour and gradient), the inks of the
 * text on it (the scene's --dyn-ink, the title, the pip; the kicker and the subtitle in the quieter ink), the title
 * block's scrim, and the kicker as preflight reads it (ink, and the scrim over the block's colours behind it).
 * @param {any} tl
 */
async function blockDrawing(tl) {
  const proto = window.HTMLElement.prototype;
  const original = proto.getBoundingClientRect;
  proto.getBoundingClientRect = function rect() {
    const w = this.classList && this.classList.contains('dyn-stage') ? 1920 : 100;
    return /** @type {any} */ ({ left: 0, top: 0, width: w, height: 50, right: w, bottom: 50, x: 0, y: 0 });
  };
  const { root, stage } = stageOf(tl);
  try {
    await stage.seek(12 * 30);
    const scene = /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="1"]'));
    const q = (/** @type {string} */ sel) => /** @type {HTMLElement} */ (scene.querySelector(sel));
    const block = q('.dyn-bg--block');
    const gradient = (block.style.getPropertyValue('background-image').match(/#[0-9a-f]{6,8}|rgba?\([^)]*\)/gi) || []).map(norm);
    const kicker = stage.textBoxes().find((b) => b.slot === 'kicker');
    return {
      'block.base': norm(block.style.getPropertyValue('background-color')),
      'block.highlight': gradient[0],
      gradientBase: gradient[1],
      'block.ink': norm(scene.style.getPropertyValue('--dyn-ink')),
      title: norm(q('.dyn-title').style.color),
      pip: norm(q('.dyn-pip').style.backgroundColor),
      'block.ink2': norm(q('.dyn-header').style.color),
      subtitle: norm(q('.dyn-subtitle').style.color),
      scrim: norm(q('.dyn-titleblock').style.getPropertyValue('--scrim')),
      kickerInk: norm(kicker.ink),
      kickerBg: kicker.bg,
      card: norm(/** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="1"]')).style.getPropertyValue('--dyn-page')),
    };
  } finally {
    stage.destroy();
    root.remove();
    proto.getBoundingClientRect = original;
  }
}

test('a v2 look draws its stored block, inks and scrim; a B1 look what B1 drew; they differ only by the owner decision', async () => {
  assert.equal(R1_PALETTES.length, 12);
  /** @type {Record<string, string[]>} */
  const changed = {};
  for (const { name, stored, b1 } of R1_PALETTES) {
    const v2 = await blockDrawing(paletteTimeline(b1.content, stored));
    const old = await blockDrawing(paletteTimeline(b1.content, b1.tokens));
    /** @param {any} drawn @param {Record<string, string>} want @param {string} which */
    const check = (drawn, want, which) => {
      const at = `${name} (${which})`;
      assert.equal(drawn['block.base'], norm(want['block.base']), `${at}: the block`);
      assert.equal(drawn.gradientBase, drawn['block.base'], `${at}: the gradient ends on the block`);
      assert.equal(drawn['block.highlight'], norm(want['block.highlight']), `${at}: its highlight`);
      for (const k of ['block.ink', 'title', 'pip']) assert.equal(drawn[k], norm(want['block.ink']), `${at}: ${k} in block.ink`);
      for (const k of ['block.ink2', 'subtitle']) assert.equal(drawn[k], norm(want['block.ink2']), `${at}: ${k} in block.ink2`);
      assert.equal(drawn.scrim, norm(want.scrim), `${at}: the scrim`);
      assert.equal(drawn.kickerInk, norm(want['block.ink2']), `${at}: preflight reads the kicker's ink`);
      const bgs = [want['block.base'], want['block.highlight']];
      assert.equal(drawn.kickerBg, scrimmedBg(want.scrim, { bg: bgs[0], bgs }, want['block.ink2']), `${at}: ... on the scrim over the block`);
    };
    check(v2, stored, 'engine_version 2: the stored tokens');
    const derived = {
      'block.base': b1.block.base, 'block.highlight': b1.block.highlight, 'block.ink': b1.block.ink,
      'block.ink2': b1.block.ink2, scrim: b1.scrim,
    };
    check(old, derived, 'B1 look: the B1 derivation');
    const differ = ['block.base', 'block.highlight', 'block.ink', 'block.ink2', 'scrim'].filter((k) => v2[k] !== old[k]);
    if (differ.length) changed[name] = differ;
    assert.equal(v2.card, old.card, `${name}: the page colour is the same B1 token`);
  }
  assert.deepEqual(changed, OWNER_DECISION, 'pixel colours differ from B1 exactly where the owner decided they do');
});

test('a v2 look on mesh_drift draws as B1 did: the derived tokens are only drawn on a block (and the scrim)', async () => {
  for (const { name, stored, b1 } of R1_PALETTES.filter((p) => p.name.endsWith('/0'))) {
    const keys = [];
    for (const tokens of [stored, b1.tokens]) {
      const { root, stage } = stageOf(paletteTimeline(b1.content, tokens));
      keys.push((await stage.seek(5 * 30)).key);
      const scene = /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene[data-scene="0"]'));
      assert.equal(norm(scene.style.getPropertyValue('--dyn-ink')), norm(b1.content.ink), `${name}: the content ink off a block`);
      assert.equal(norm(/** @type {HTMLElement} */ (scene.querySelector('.dyn-card')).style.getPropertyValue('--surface')), norm(b1.tokens.surface));
      stage.destroy();
      root.remove();
    }
    assert.equal(keys[0], keys[1], `${name}: the same frame key (every painted value equal)`);
  }
});

// --- B2a: preflight's remedies through the fixes channel (plan §1.2), the legacy channel second ------------------------

/** The safe layout, a 60 px title cap, a 44 px body cap and a title scrim on scene 0, through `channel`. */
async function remedied(/** @type {(look: any) => void} */ apply) {
  const tl = dynamicTimeline();
  apply(tl.scenes[0].look);
  const { root, stage } = stageOf(tl);
  await stage.seek(270);
  const q = (/** @type {string} */ sel) => /** @type {HTMLElement} */ (stage.el.querySelector(sel));
  const out = {
    scrim: q('.dyn-titleblock').classList.contains('has-scrim'),
    title: q('.dyn-title').style.fontSize,
    body: q('.dyn-cards').style.getPropertyValue('--body'),
    shown: [...stage.el.querySelectorAll('.dyn-card')].filter((c) => Number(/** @type {HTMLElement} */ (c).style.opacity) > 0).length,
  };
  stage.destroy();
  root.remove();
  return out;
}

test('preflight\'s remedies reach the stage through the fixes channel exactly as through B1\'s fields', async () => {
  const want = { scrim: true, title: '60px', body: '44px', shown: 1 };
  assert.deepEqual(await remedied((look) => {
    look.fixes = { safe: true, slots: { title: { size: 60, scrim: true }, body: { size: 44 } } };
  }), want, 'fixes');
  assert.deepEqual(await remedied((look) => {
    look.variant = 'safe';
    look.text_fit = { title: { size: 60, scrim: true }, body: { size: 44 } };
  }), want, 'legacy (a timeline remedied before B2a)');
  assert.deepEqual(await remedied(() => undefined), { scrim: false, title: '96px', body: '52px', shown: 3 }, 'none');
  // both: the fixes channel's size first; a scrim or the safe layout from either
  assert.deepEqual(await remedied((look) => {
    look.fixes = { safe: false, slots: { title: { size: 64 }, body: { scrim: false } } };
    look.text_fit = { title: { size: 60, scrim: true }, body: { size: 44 } };
    look.variant = 'safe';
  }), { scrim: true, title: '64px', body: '44px', shown: 1 });
});

// --- B2a: the template contract (plan §1.4) ----------------------------------------------------------------------------

/**
 * board_generic built for scene `index` of the fixture by itself, with a stand-in rectOf.
 * @param {number} index @param {any} [tl] @param {(el: Element) => any} [rectOf]
 */
function boardFor(index, tl = dynamicTimeline(), rectOf = () => ({ x: 10.4, y: 20.6, w: 100.5, h: 50.2 })) {
  const scene = tl.scenes[index];
  const format = tl.presentation.format === 'portrait' ? 'portrait' : 'landscape';
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctx = /** @type {any} */ ({
    scene, index, look: scene.look, chapter: chapterTitleAt(tl, scene.start), roles: makeRoles(tl.presentation, scene.look),
    zones: ZONES[format], format, mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
    media: (/** @type {any} */ ref) => new MediaView(ref, { mode: 'render', box: { w: 640, h: 360 }, sceneDuration: 8, fps: 30 }),
    onBackground: { ink: '#F3F5F9', ink2: '#B9BEC7', bg: '#10132C', block: false }, rectOf, locale: 'en', rng: () => 0.5,
  });
  ctx.view = boardGeneric.build(ctx);
  /** @type {any} */ (boardGeneric).layout(ctx);
  boardGeneric.update(ctx, 9, /** @type {any} */ ({ terminalLines: [], countdownRemaining: null }));
  return { ctx, v: ctx.view, done: () => root.remove() };
}

test('focusRect: the target\'s card or the media frame, its box on whole px, a rect with its own radius (§1.7 targets)', () => {
  const tpl = /** @type {any} */ (boardGeneric);
  const board = boardFor(0);
  const card = (/** @type {string} */ id) => board.v.cards.find((/** @type {any} */ c) => c.spec.id === id).el;
  /** @type {Element[]} */
  const asked = [];
  board.ctx.rectOf = (/** @type {Element} */ el) => {
    asked.push(el);
    return { x: 10.4, y: 20.6, w: 100.5, h: 50.2 };
  };
  for (const target of ['b2', 'b2:line:1', 'b2:step:0']) {
    asked.length = 0;
    assert.deepEqual(tpl.focusRect(board.ctx, target), { rect: { x: 10, y: 21, w: 101, h: 50 }, shape: 'rect', radius: CARD_RADIUS }, target);
    assert.deepEqual(asked, [card('b2')], `${target}: the item's card`);
  }
  for (const target of ['panel', 'nope', '', 'option:0']) assert.equal(tpl.focusRect(board.ctx, target), null, `${target}: none here`);
  board.ctx.rectOf = () => null;
  assert.equal(tpl.focusRect(board.ctx, 'b2'), null, 'not on the page');
  board.done();
  const quiz = boardFor(2);
  quiz.ctx.rectOf = (/** @type {Element} */ el) => (el === quiz.v.cards.find((/** @type {any} */ c) => c.spec.id === 'option:1').el ? { x: 1, y: 2, w: 3, h: 4 } : null);
  assert.deepEqual(tpl.focusRect(quiz.ctx, 'option:1'), { rect: { x: 1, y: 2, w: 3, h: 4 }, shape: 'rect', radius: CARD_RADIUS });
  quiz.done();
  const split = boardFor(4);
  split.ctx.rectOf = (/** @type {Element} */ el) => (el === split.v.frame ? { x: 1100.5, y: 300.49, w: 700.2, h: 393.3 } : null);
  for (const target of ['panel', 'panel:bar:0:1', 'panel:row:2']) {
    assert.deepEqual(tpl.focusRect(split.ctx, target), { rect: { x: 1101, y: 300, w: 700, h: 394 }, shape: 'rect', radius: FRAME_RADIUS }, target);
  }
  split.done();
});

test('itemEl, dimGroups and anchor: the item\'s text (its [[terms]] kept), the content groups, no morph anchor', () => {
  const tl = dynamicTimeline();
  tl.scenes[0].board[1].text = 'Voltage is the [[push]] on charge';
  const tpl = /** @type {any} */ (boardGeneric);
  const { ctx, v, done } = boardFor(0, tl);
  const b2 = tpl.itemEl(ctx, 'b2');
  assert.equal(b2, v.cards.find((/** @type {any} */ c) => c.spec.id === 'b2').body);
  assert.equal(b2.querySelector('.rt-k')?.textContent, 'push', 'cue.term finds the term inside');
  assert.equal(tpl.itemEl(ctx, 'b2:line:0'), b2);
  for (const target of ['panel', 'zzz', 'option:0']) assert.equal(tpl.itemEl(ctx, target), null, target);
  assert.deepEqual(tpl.dimGroups(ctx), [v.titleBlock, ...v.cardsEl.children], 'the title block and every card, in page order');
  assert.equal(tpl.anchor(ctx), null);
  done();
  const split = boardFor(4);
  assert.deepEqual(tpl.dimGroups(split.ctx), [split.v.titleBlock, split.v.frame, ...split.v.cardsEl.children]);
  split.done();
  const quiz = boardFor(2);
  assert.equal(tpl.itemEl(quiz.ctx, 'option:2').textContent, 'CIt stays');
  quiz.done();
});

test("itemEl in 9:16: a misconception with a justification is two cards, and every target reaches the myth's", () => {
  // Pinned as it stands (B2a): the §1.7 grammar has no part form for the fact card, so a [[term]] of the correction is
  // out of reach in 9:16 (in 16:9 one card holds both). board_generic consumes no cue_term or look_here event
  // (templates.py), so no cue reaches it; B2c settles how a split item is reached before cues.js uses itemEl.
  const tpl = /** @type {any} */ (boardGeneric);
  for (const format of /** @type {const} */ (['portrait', 'landscape'])) {
    const tl = dynamicTimeline(format);
    tl.scenes[0].board.push({ id: 'm1', kind: 'misconception', text: 'Current is used up', justification: 'The [[current]] is the same all round' });
    const { ctx, v, done } = boardFor(0, tl);
    const ids = v.cards.map((/** @type {any} */ c) => c.spec.id);
    const myth = v.cards.find((/** @type {any} */ c) => c.spec.id === 'm1');
    for (const target of ['m1', 'm1:line:1', 'm1:step:1']) assert.equal(tpl.itemEl(ctx, target), myth.body, `${format} ${target}`);
    if (format === 'portrait') {
      assert.ok(ids.includes('m1') && ids.includes('m1:fact'), 'two cards');
      assert.equal(myth.body.querySelector('.rt-k'), null, 'the myth card holds no term');
      const fact = v.cards.find((/** @type {any} */ c) => c.spec.id === 'm1:fact');
      assert.equal(fact.body.querySelector('.rt-k')?.textContent, 'current', 'the term is on the fact card');
    } else {
      assert.ok(!ids.includes('m1:fact'), 'one card');
      assert.equal(myth.body.querySelector('.rt-k')?.textContent, 'current', 'one card holds both texts');
    }
    done();
  }
});

test('static mediaBox: B1\'s boxes (mediaBoxMax; a figure\'s FIGURE_BOX_WIDTH x figureMax), whatever the slot\'s fixes', () => {
  const tpl = /** @type {any} */ (boardGeneric);
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const scene of dynamicTimeline(format).scenes) {
      for (const mode of /** @type {const} */ (['render', 'live'])) {
        const want = mediaBoxMax(scene, ZONES[format], mode);
        for (const slot of ['main', 'panel', undefined]) {
          for (const fixes of [fixesOf(null), fixesOf(/** @type {any} */ ({ fixes: { safe: true, slots: { title: { size: 40 } } } }))]) {
            assert.deepEqual(tpl.mediaBox(scene, ZONES[format], mode, slot, fixes), want, `${format} ${scene.scene_id} ${mode} ${slot}`);
          }
        }
        assert.deepEqual(tpl.mediaBox(scene, ZONES[format], mode, FIGURE_SLOT, fixesOf(null)), { w: FIGURE_BOX_WIDTH, h: LAYOUT[format].figureMax });
        assert.deepEqual(mediaBoxOf(scene, ZONES[format], mode, 'main'), want, 'the stage asks the template');
      }
    }
  }
  // a template without the hook: the whole stage (as B1 sized every other template's clips)
  const hook = tpl.mediaBox;
  try {
    delete tpl.mediaBox;
    assert.deepEqual(mediaBoxOf(dynamicTimeline().scenes[3], ZONES.landscape, 'render', 'main'), { w: 1920, h: 1080 });
  } finally {
    tpl.mediaBox = hook;
  }
});

/** The fixture with a video side panel on scene 4, a figure card on scene 0 and a title fix on the clip scene. */
function mediaTimeline(/** @type {'landscape' | 'portrait'} */ format, /** @type {'dark' | 'light'} */ mode) {
  const tl = mode === 'light' ? lightTimeline(format) : dynamicTimeline(format);
  tl.scenes[4].side_panel.media = { kind: 'video', asset_key: 'panel-clip', url: '/media/panel.mp4', duration: 3, width: 960, height: 540, fit: 'contain', end_behavior: 'loop' };
  tl.scenes[0].board.push({ id: 'fig', kind: 'figure', caption: 'A circuit' });
  tl.scenes[0].figures = { fig: { kind: 'image', asset_key: 'img-2', url: '/media/fig.png', width: 800, height: 600, fit: 'contain' } };
  tl.scenes[3].look.fixes = { safe: true, slots: { title: { size: 40 } } };
  return tl;
}

test('every media frame key a template creates is in mediaPlan(0, frames), and every plan key is B1\'s', async () => {
  const build = boardGeneric.build;
  /** @type {any[]} */
  let created = [];
  boardGeneric.build = function spy(/** @type {any} */ ctx) {
    const media = ctx.media;
    ctx.media = (/** @type {any} */ ref, /** @type {any} */ opts) => {
      const view = media(ref, opts);
      created.push(view);
      return view;
    };
    return build.call(this, ctx);
  };
  try {
    for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
      for (const mode of /** @type {const} */ (['dark', 'light'])) {
        for (const tl of [dynamicTimeline(format), mediaTimeline(format, mode)]) {
          created = [];
          const { root, stage } = stageOf(tl);
          const info = stage.info();
          for (const s of info.scenes) for (const f of [s.f0, Math.floor((s.f0 + s.f1) / 2), s.f1 - 1]) await stage.seek(f);
          const plan = stage.mediaPlan(0, info.frames);
          const planned = new Set(plan.map((e) => e.key));
          const keys = created.map((view) => view.key).filter(Boolean);
          assert.ok(keys.length >= 1, `${format} ${mode}: clips were shown`);
          for (const key of keys) assert.ok(planned.has(key), `${format} ${mode}: ${key} is planned`);
          // the plan B1's stage made: board_generic's mediaBoxMax for every scene, in the render layout
          assert.deepEqual(plan, planMedia(tl, 0, info.frames, (/** @type {any} */ scene) => mediaBoxMax(scene, ZONES[format])), `${format} ${mode}: B1's keys`);
          // the figure (a picture: no key) keeps B1's box
          const figure = created.find((view) => view.ref.asset_key === 'img-2');
          if (figure) assert.deepEqual(figure.box, { w: FIGURE_BOX_WIDTH, h: LAYOUT[format].figureMax });
          stage.destroy();
          root.remove();
        }
      }
      // live: clips play as <video>, no frame keys at all
      created = [];
      const root = document.createElement('div');
      const live = createDynamicStage(root, mediaTimeline(format, 'dark'), { mode: 'live' });
      for (const t of [5, 27, 35]) live.update(t);
      assert.ok(created.length >= 2 && created.every((view) => view.key === null), `${format}: live keys`);
      live.destroy();
    }
  } finally {
    boardGeneric.build = build;
  }
});

test('ctx.media sizes a clip by the template\'s mediaBox (a box passed for it is ignored); a picture may pass its own', async () => {
  const build = boardGeneric.build;
  /** @type {any} */
  let ctxSeen = null;
  boardGeneric.build = function spy(/** @type {any} */ ctx) {
    if (ctx.index === 3) ctxSeen = ctx;
    return build.call(this, ctx);
  };
  try {
    const tl = dynamicTimeline();
    const { root, stage } = stageOf(tl);
    await stage.seek(800);
    const clip = tl.scenes[3].media;
    const want = mediaBoxOf(tl.scenes[3], ZONES.landscape, 'render', 'main');
    assert.deepEqual(ctxSeen.media(clip, { slot: 'main', box: { w: 10, h: 10 } }).box, want);
    assert.equal(ctxSeen.media(clip, { slot: 'main', box: { w: 10, h: 10 } }).key, mediaKey('video', 'manim-sim', mediaSize(clip, want)));
    assert.deepEqual(ctxSeen.media(clip, {}).box, want, 'no slot: the template\'s box (board_generic shows one medium)');
    const picture = { kind: 'image', url: '/media/p.png', width: 100, height: 100 };
    assert.deepEqual(ctxSeen.media(picture, { box: { w: 10, h: 12 } }).box, { w: 10, h: 12 });
    assert.deepEqual(ctxSeen.media(picture, { slot: FIGURE_SLOT }).box, { w: FIGURE_BOX_WIDTH, h: LAYOUT.landscape.figureMax });
    assert.deepEqual(ctxSeen.media(picture, { box: { w: 0, h: 12 } }).box, want, 'not a box: the template\'s');
    stage.destroy();
    root.remove();
  } finally {
    boardGeneric.build = build;
  }
});

test('static tex: every TeX string the template renders for a scene, in render order (render.js converts these)', () => {
  const scene = /** @type {any} */ ({
    board: [
      { id: 'f', kind: 'formula', latex: 'V = IR', variables: [{ symbol_latex: 'V', meaning: 'voltage' }, { symbol_latex: ' ', meaning: 'x' }], text: 'so $I = V/R$' },
      { id: 't', kind: 'table', headers: ['$R$', 'b'], rows: [['$2\\Omega$', 'c']] },
      { id: 'd', kind: 'definition', term: '$P$', text: 'power', justification: 'since $P = VI$' },
      { id: 'c', kind: 'figure', caption: 'a $\\theta$' },
    ],
    quiz: { question: 'Is $x$ big?', explanation: '$x = 2$', options: ['$1$', 'two'] },
  });
  assert.deepEqual(/** @type {any} */ (boardGeneric).tex(scene, null, 'landscape'), [
    { tex: 'V = IR', display: true }, { tex: 'V', display: false }, { tex: 'I = V/R', display: false },
    { tex: 'R', display: false }, { tex: '2\\Omega', display: false }, { tex: 'P', display: false },
    { tex: 'P = VI', display: false }, { tex: '\\theta', display: false }, { tex: 'x', display: false },
    { tex: 'x = 2', display: false }, { tex: '1', display: false },
  ]);
  assert.deepEqual(/** @type {any} */ (boardGeneric).tex({ board: [], quiz: null }, null, 'portrait'), []);
});

test('a timeline of a newer look engine is refused (timeline_invalid); B1\'s, B2\'s and an unversioned one are drawn', () => {
  for (const v of [3, 12]) {
    const tl = dynamicTimeline();
    tl.presentation.engine_version = v;
    const root = document.createElement('div');
    assert.throws(() => createDynamicStage(root, tl, { mode: 'render' }), (/** @type {any} */ e) => e.code === 'timeline_invalid' && /look engine/.test(e.message));
    assert.equal(root.childElementCount, 0, 'nothing built');
  }
  for (const v of [1, 2, undefined]) {
    const tl = dynamicTimeline();
    tl.presentation.engine_version = v;
    const root = document.createElement('div');
    const stage = createDynamicStage(root, tl, { mode: 'live' });
    assert.ok(root.querySelector('.dyn-stage'));
    stage.destroy();
  }
  assert.throws(() => createDynamicStage(document.createElement('div'), classicTimeline()), (/** @type {any} */ e) => e.code === 'timeline_invalid');
});

test('every stored block role is what the stage draws, each on its own (synthetic values no derivation gives)', async () => {
  const b1 = R1_PALETTES[0].b1;
  const stored = {
    ...b1.tokens, 'block.base': '#123456', 'block.highlight': '#234567', 'block.ink': '#ABCDEF', 'block.ink2': '#BCDEF0',
    scrim: '#01020380',
  };
  const drawn = await blockDrawing(paletteTimeline(b1.content, stored));
  assert.deepEqual(
    ['block.base', 'block.highlight', 'block.ink', 'title', 'pip', 'block.ink2', 'subtitle', 'scrim'].map((k) => drawn[k]),
    ['#123456', '#234567', '#abcdef', '#abcdef', '#abcdef', '#bcdef0', '#bcdef0', '#01020380'],
  );
  // one stored ink, the other derived from the stored block (blockInk2 of the stored ink over it)
  const { 'block.ink2': _drop, ...partial } = stored;
  const half = await blockDrawing(paletteTimeline(b1.content, partial));
  assert.equal(half['block.ink'], '#abcdef');
  assert.equal(half['block.ink2'], norm(blockInk2('#ABCDEF', ['#123456', '#234567'])));
});
