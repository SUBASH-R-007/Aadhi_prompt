import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from '../player/_dom.js';
import { BACKGROUNDS, TEMPLATES, blockInk2, createDynamicStage, fontStacks, isDynamic, templateFor } from '../../js/dynamic/stage.js';
import { Painter, makeRoles, stageInfo, insideAny, ZONES } from '../../js/dynamic/core.js';
import { blockColors } from '../../js/dynamic/backgrounds/colour_block.js';
import { contrastRatio } from '../../js/motion/color.js';
import boardGeneric, { BODY_WEIGHT, CARD_EXIT, LABELS, MIN_DWELL, MIN_LABEL, QUIZ_CLEAR, cardSpecs, kickerOf, mediaBoxMax, regions, slotTuning, variantOf } from '../../js/dynamic/templates/board_generic.js';
import { classicTimeline, dynamicTimeline } from './_fixture.js';

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

test('kicker: a scene-type badge, the chapter, the quiz title or the chapter label', () => {
  const tl = dynamicTimeline();
  assert.equal(kickerOf(tl.scenes[0], tl), 'Basics');
  assert.equal(kickerOf(tl.scenes[1], tl), 'Part 2');
  assert.equal(kickerOf(tl.scenes[2], tl), 'Quick check');
  assert.equal(kickerOf(/** @type {any} */ ({ ...tl.scenes[0], type: 'example', title: 'A ceiling fan' }), tl), LABELS.badges.example);
  assert.equal(kickerOf(/** @type {any} */ ({ ...tl.scenes[0], type: 'example', title: 'Worked example - a fan' }), tl), 'Basics');
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
      scene: chapter, index: 1, timeline: tl, look: chapter.look, roles: makeRoles(tl.presentation, chapter.look),
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
      scene, index: 0, timeline: tl, look: scene.look, roles: makeRoles(tl.presentation, scene.look),
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
