// @ts-check
// templates/kinetic_statement.js (DYNAMIC_VIDEO_DESIGN §4.3 kinetic_statement, §2.3 the reel's promise beat, R-25 /
// R-31 the on-screen text is the caption, B2 plan §1.4 the template contract, §1.5 the cues, §1.11 the slots): the
// six fixture timelines (tests/compose/dynamic/make_kinetic_statement_fixtures.py) through the harness's contract and
// order gates, the pure helpers (data, the fit <-> drawsScene rule against templates.py, word counting as Python does
// it, phrases, the narration alignment and phrase times, lines, geometry, slots, TeX), the painted behaviour in both
// formats (phrases landing on their spoken words, both personalities' entrances, the per-word mask's clip, the
// completed-line dim, the frame-0 cover, the label and the attribution, reduced motion, the cues' geometry,
// preflight's entries and remedies) and the fit, on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { TYPE_FLOORS, ZONES, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { REVEAL_LEAD, itemTimings } from '../../../js/dynamic/state.js';
import { composite, contrastRatio } from '../../../js/motion/color.js';
import { CUE_HOLD, EXIT_FACTOR, TERM_DRAW } from '../../../js/dynamic/cues.js';
import kinetic, {
  ATTRIBUTION_DELAY, ATTRIBUTION_PREFIX, COLUMNS, DESIGN_WORDS, DIM_ALPHA, FALLBACK_GAP, ID, LABELS, LAYOUT,
  LINE_DIM, LINE_HEIGHT, MASK_PAD_EM, MATCH_WINDOW, MAX_WORDS, PHRASE_ENTER, PHRASE_MAX_WORDS, QUOTE_KIND,
  SETTLE_MARGIN, SIZE_STEP, STATEMENT_KINDS, TRACKING_EM, alignWords, attributionOf, boxes, countWords, dimmedInk,
  drawsScene, fitInHeight, hasText, lineDoneTimes, lineHeight, lineIndices, normWord, phrasesOf, phraseTimes,
  setsAtFrame0, slotRows, spokenWords, stackTop, statementOf, termCueEnds, termLines, texOf, wordTexts,
} from '../../../js/dynamic/templates/kinetic_statement.js';
import boardGeneric, { LABELS as BOARD_LABELS } from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['kinetic_statement.landscape.json', 'kinetic_statement.landscape.light.json',
  'kinetic_statement.landscape.stress.json', 'kinetic_statement.portrait.json',
  'kinetic_statement.portrait.light.json', 'kinetic_statement.portrait.stress.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { kinetic_statement: kinetic } };
const TEMPLATES_PY = readFileSync(new URL('../../../../aadhi/compose/dynamic/templates.py', import.meta.url), 'utf-8');

/**
 * A render stage of `tl` with kinetic_statement through the stage's test seam, and the context each scene was built
 * with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...kinetic, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return kinetic.build(ctx); } };
  const run = renderStage(tl, { templates: { kinetic_statement: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`; returns that frame's exact scene time. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.round((Number(scene.start) + t) * tl.fps);
    await run.stage.seek(f);
    return f / tl.fps - Number(scene.start);
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));
/** A hex colour as the DOM reports a painted one. @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** The scene index of scene id `id` in `tl`. @param {any} tl @param {string} id */
const indexOf = (tl, id) => tl.scenes.findIndex((/** @type {any} */ s) => s.scene_id === id);
/** The scene-relative entrance of item `id` (state.js itemTimings). */
const enterOf = (/** @type {any} */ scene, /** @type {string} */ id) => /** @type {number} */ (itemTimings(scene).find((x) => x.id === id)?.enter);
/** The scene-relative start of the first narration word that reads `word` (normWord). @param {any} scene @param {string} word */
const spokenAt = (scene, word) => {
  for (const b of scene.beats) for (const w of b.words) if (normWord(w.text) === normWord(word)) return Number(w.start);
  throw new Error(`no spoken ${word}`);
};

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(kinetic, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < total; f += 6) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      for (const b of s.beats) for (const w of b.words) frames.add(Math.min(total - 1, Math.round((s.start + w.start) * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps) + 3));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover what the B2 plan §G asks: both formats and modes, both entrances, every kind, part, script and cue', () => {
  const seen = {
    formats: new Set(), modes: new Set(), entrances: new Set(), kinds: new Set(), cues: new Set(), scripts: new Set(),
    parts: new Set(),
  };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    tl.scenes.forEach((/** @type {any} */ s, /** @type {number} */ i) => {
      if (s.look.template !== ID) {
        if (s.look.template === 'hook_question' && tl.presentation.format === 'portrait'
          && tl.scenes[i + 1]?.look.template === ID) seen.parts.add('promise-after-hook');
        return;
      }
      const item = /** @type {any} */ (statementOf(s));
      assert.ok(item && drawsScene(s, tl.presentation.format), `${name} ${s.scene_id}: drawn by this template`);
      seen.kinds.add(item.kind);
      for (const e of s.events || []) seen.cues.add(`${e.kind}:${parseTarget(e.target).form}`);
      if (setsAtFrame0(tl.presentation.format, s.index)) seen.parts.add('cover');
      if (attributionOf(item)) seen.parts.add('attribution');
      if (Object.prototype.hasOwnProperty.call(LABELS, item.kind)) seen.parts.add('label');
      const n = countWords(item.text);
      const fmt = /** @type {'landscape' | 'portrait'} */ (tl.presentation.format);
      if (n > DESIGN_WORDS[fmt]) seen.parts.add('stretched');
      if (n === MAX_WORDS[fmt]) seen.parts.add(`max-words-${fmt}`);
      const text = String(item.text);
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{30,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{3,}\b\s*){5}/.test(text)) seen.scripts.add('wide-caps');
      if (/\[\[[^\]]+\]\]/.test(text)) seen.scripts.add('term');
    });
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.kinds].sort(), ['callout_tip', 'callout_warning', 'heading', 'paragraph', 'quote', 'takeaway']);
  assert.deepEqual([...seen.cues].sort(), ['cue_term:item', 'cue_term:item:term', 'reveal:item']);
  assert.deepEqual([...seen.parts].sort(), ['attribution', 'cover', 'label', 'max-words-landscape', 'max-words-portrait',
    'promise-after-hook', 'stretched']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'term', 'tex', 'wide-caps']);
});

test('every fixture scene lasts what §4.3 gives the template, and every phrase is in before it ends', () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    const [min, max] = fmt === 'portrait' ? [2, 5] : [3, 7];
    const scale = Number(tl.presentation.motion_scale) || 1;
    for (const s of tl.scenes) {
      if (s.look.template !== ID) continue;
      assert.ok(s.duration >= min - 1e-9 && s.duration <= max + 1e-9, `${name} ${s.scene_id}: ${s.duration}s`);
      const item = /** @type {any} */ (statementOf(s));
      const words = wordTexts(item.text, 'en-IN');
      const times = phraseTimes(s, item, words, phrasesOf(words), [], enterOf(s, item.id), scale * PHRASE_ENTER);
      const lands = times.phrases[times.phrases.length - 1] + scale * PHRASE_ENTER;
      assert.ok(s.duration - lands >= SETTLE_MARGIN - 1e-9, `${name} ${s.scene_id}: the last phrase lands at ${lands}`);
    }
  }
});

// --- data, and the fit <-> drawsScene rule (B2c review CG-2) -----------------------------------------------------

/** @param {any[]} board @param {Record<string, any>} [extra] @returns {any} */
const scene = (board, extra = {}) => ({ scene_id: 's', index: 0, type: 'content', start: 0, duration: 5, beats: [], board, ...extra });

test('statementOf: the board\'s one item, when it is a statement kind or a quote with text', () => {
  for (const kind of STATEMENT_KINDS) assert.equal(statementOf(scene([{ id: 'a', kind, text: 'One idea.' }]))?.id, 'a', kind);
  assert.equal(statementOf(scene([{ id: 'q', kind: QUOTE_KIND, text: 'Words.', caption: 'Someone' }]))?.id, 'q');
  assert.equal(statementOf(scene([{ id: 'b', kind: 'bullet', text: 'A bullet' }])), null, 'not a statement kind');
  assert.equal(statementOf(scene([])), null);
  assert.equal(statementOf(scene([{ id: 'a', kind: 'takeaway', text: '  　' }])), null, 'blank (Python strip)');
  assert.equal(statementOf(scene([{ id: 'a', kind: 'takeaway', text: 'x' }, { id: 'e', kind: 'heading', text: '' }])), null,
    'two items, even one without text: _fit_kinetic_statement counts every item');
});

test('countWords is templates.py _words: Python whitespace, a $TeX$ span one word, markup counted as written', () => {
  assert.equal(countWords('Energy is never created'), 4);
  assert.equal(countWords('  spaced\tout\nwords  '), 3);
  assert.equal(countWords('Power is $P = I^2 R$ here'), 4, 'one TeX span is one word');
  assert.equal(countWords('a \\$5 note and $x$'), 5, 'an escaped dollar opens no span');
  assert.equal(countWords('[[Ohm\'s law]] holds'), 3, 'markup is counted as written');
  assert.equal(countWords('ten volts'), 2, 'Python splits at a no-break space');
  assert.equal(countWords('word﻿word'), 1, 'a BOM is not whitespace to Python');
  assert.equal(countWords('a\x1cb\x85c'), 3, 'nor are these anything but whitespace');
  assert.equal(countWords(''), 0);
  assert.equal(countWords(null), 0);
  assert.ok(hasText('x') && !hasText(' \t') && !hasText(null) && hasText('﻿'), 'hasText is Python\'s strip()');
  // the regex is templates.py's own
  const m = /^_TEX = re\.compile\(r"(.*)"\)$/m.exec(TEMPLATES_PY);
  assert.ok(m, 'templates.py _TEX');
  assert.equal(/** @type {RegExpExecArray} */ (m)[1], '(?<!\\\\)\\$[^$]*\\$');
});

test('STATEMENT_KINDS is templates.py STATEMENT_KINDS (the statement row and this template\'s fit)', () => {
  const m = /STATEMENT_KINDS: Final = frozenset\(\{([^}]*)\}\)/.exec(TEMPLATES_PY);
  assert.ok(m);
  const kinds = [...(/** @type {RegExpExecArray} */ (m))[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]).sort();
  assert.deepEqual([...STATEMENT_KINDS].sort(), kinds);
});

test('drawsScene: one statement within MAX_WORDS, no shown panel, no medium; else board_generic draws it whole', () => {
  const words = (/** @type {number} */ n) => Array.from({ length: n }, () => 'word').join(' ');
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    assert.ok(drawsScene(scene([{ id: 't', kind: 'takeaway', text: words(MAX_WORDS[fmt]) }]), fmt), `${fmt}: at the cap`);
    assert.ok(!drawsScene(scene([{ id: 't', kind: 'takeaway', text: words(MAX_WORDS[fmt] + 1) }]), fmt), `${fmt}: past it`);
    assert.ok(MAX_WORDS[fmt] > DESIGN_WORDS[fmt], 'a stretched band exists past §4.3\'s limit');
  }
  const ok = scene([{ id: 't', kind: 'takeaway', text: 'One idea.' }]);
  assert.ok(drawsScene(ok) && drawsScene(ok, 'portrait'));
  const panel = { ...ok, side_panel: { panel: { kind: 'image', title: 'P' }, show_at: 0 } };
  assert.ok(!drawsScene(panel), 'typography only: a panel is board_generic\'s');
  assert.ok(drawsScene({ ...panel, layout: { show_side_panel: false } }), 'a hidden panel is not drawn anyway');
  assert.ok(!drawsScene({ ...ok, media: { kind: 'image' } }), 'a medium of its own');
  assert.ok(!drawsScene(scene([{ id: 't', kind: 'takeaway', text: 'x' }, { id: 'b', kind: 'bullet', text: 'y' }])));
  assert.ok(drawsScene(scene([{ id: 'q', kind: 'quote', text: 'Words.', caption: 'Someone' }])), 'the quote row\'s alternative');
});

test('MAX_WORDS is templates.py KINETIC_STATEMENT_WORDS, and _fit_kinetic_statement refuses what drawsScene refuses', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates)[ID];
  const m = /KINETIC_STATEMENT_WORDS[^=]*=\s*MappingProxyType\(\s*\{\s*"landscape":\s*(\d+),\s*"portrait":\s*(\d+)\s*\}\)/.exec(TEMPLATES_PY);
  if (!m) {
    // before the integrator applies this template's request, templates.py has neither the cap nor the guards; it
    // must not be registered without them (the director would pick it and the page would hand the scene on)
    assert.equal(spec.implemented, false, 'templates.py: kinetic_statement is implemented without KINETIC_STATEMENT_WORDS');
    return;
  }
  assert.deepEqual([Number(m[1]), Number(m[2])], [MAX_WORDS.landscape, MAX_WORDS.portrait]);
  const fit = /def _fit_kinetic_statement[\s\S]*?\n\n\n/.exec(TEMPLATES_PY);
  assert.ok(fit, '_fit_kinetic_statement');
  const body = /** @type {RegExpExecArray} */ (fit)[0];
  assert.match(body, /_panel\(scene\) is not None/, 'refuses a side panel (drawsScene: typography only)');
  assert.match(body, /KINETIC_STATEMENT_WORDS\[fmt\]/, 'refuses past the cap');
  assert.match(body, /\.strip\(\)/, 'refuses an item without text');
});

test('phrasesOf: split after a clause\'s punctuation, then evenly into at most PHRASE_MAX_WORDS words', () => {
  const w = (/** @type {string} */ s) => s.split(' ');
  assert.deepEqual(phrasesOf(w('Energy is never created or destroyed, only changed in form.')),
    [[0, 1, 2], [3, 4, 5], [6, 7], [8, 9]], 'six words to the comma: 3 + 3; then four: 2 + 2');
  assert.deepEqual(phrasesOf(w('One two three four five')), [[0, 1, 2], [3, 4]], 'five: 3 + 2 (the earlier one longer)');
  assert.deepEqual(phrasesOf(w('Short.')), [[0]]);
  assert.deepEqual(phrasesOf(w('a b c d e f g')), [[0, 1, 2], [3, 4], [5, 6]]);
  assert.deepEqual(phrasesOf(w('Why? "Because," she said.')), [[0], [1], [2, 3]], 'a closing quote after the comma');
  assert.deepEqual(phrasesOf(w('हर तार में प्रतिरोध। होता है')), [[0, 1], [2, 3], [4, 5]], 'the danda ends a clause');
  assert.deepEqual(phrasesOf([]), []);
  for (const p of phrasesOf(w('x y z w v u t s r q p o n m l k j i'))) assert.ok(p.length <= PHRASE_MAX_WORDS);
});

test('wordTexts: richWords\' own words from the data (a TeX or code span one placeholder joined to its punctuation)', () => {
  assert.deepEqual(wordTexts('[[Power]] grows with $P = I^2 R$.', 'en-IN'), ['Power', 'grows', 'with', '￼.']);
  assert.deepEqual(wordTexts('**Bold** and `code`', 'en-IN'), ['Bold', 'and', '￼']);
  assert.deepEqual(wordTexts('மின்னழுத்தம் கூடினால் [[மின்னோட்டம்]] கூடும்', 'ta-IN').length, 4);
  // the same split the DOM gets (the words are the template's only text runs)
  const s = spyStage(load('kinetic_statement.landscape.light.json'));
  return s.seekTo(1, 0.5).then(() => {
    const v = s.ctx(1).view;
    assert.equal(v.words.length, wordTexts(v.item.text, 'en-IN').length);
    s.done();
  });
});

test('normWord / alignWords: words compared case-, form- and punctuation-free, in order, within MATCH_WINDOW', () => {
  assert.equal(normWord('Earth?'), 'earth');
  assert.equal(normWord('“Long-term,”'), 'longterm');
  assert.equal(normWord('￼.'), '', 'TeX never matches');
  assert.equal(normWord('ﬁre'), 'fire', 'NFKC');
  assert.equal(normWord('प्रतिरोध,'), 'प्रतिरोध', 'marks are kept');
  const spoken = 'so remember energy is never created at all'.split(' ').map((text, i) => ({ text, start: i }));
  assert.deepEqual(alignWords(['energy', 'is', 'never', 'created'], spoken), [2, 3, 4, 5]);
  assert.deepEqual(alignWords(['energy', 'zzz', 'never'], spoken), [2, null, 4], 'a word not said stays null; the search does not move');
  assert.deepEqual(alignWords(['', 'is'], spoken), [null, 3], '\'\' never matches');
  const far = [...Array.from({ length: MATCH_WINDOW }, (_, i) => ({ text: `x${i}`, start: i })), { text: 'late', start: 99 }];
  assert.deepEqual(alignWords(['late'], far), [null], 'past the window: not this word');
});

test('spokenWords: every beat\'s words from the entrance on, in spoken order', () => {
  const s = scene([], { beats: [{ start: 0, words: [{ text: 'b', start: 1.2 }, { text: 'a', start: 0.2 }] },
    { start: 2, words: [{ text: 'c', start: 2.1 }] }] });
  assert.deepEqual(spokenWords(s, 0).map((w) => w.text), ['a', 'b', 'c']);
  assert.deepEqual(spokenWords(s, 1).map((w) => w.text), ['b', 'c']);
});

test('phraseTimes: REVEAL_LEAD before the first spoken word, filled between neighbours, spread over the beat without any', () => {
  const words = 'Energy is never created or destroyed, only changed in form.'.split(' ');
  const phrases = phrasesOf(words);
  const said = (/** @type {number} */ t0, /** @type {string[]} */ list, step = 0.25) => list.map((text, i) => ({ text, start: t0 + i * step, end: t0 + (i + 1) * step }));
  const beats = [{ start: 0.5, speech_end: 1.5, board_item_id: 't', words: said(0.5, words.slice(0, 4)) },
    { start: 1.65, speech_end: 2.65, words: said(1.65, words.slice(4, 6), 0.5) },
    { start: 2.8, speech_end: 3.8, words: said(2.8, words.slice(6)) }];
  const s = scene([{ id: 't', kind: 'takeaway', text: words.join(' ') }], { duration: 5.1, beats });
  const t = phraseTimes(s, { id: 't' }, words, phrases, [], 0.38, 0.5);
  const lead = (/** @type {number} */ x) => x - REVEAL_LEAD;
  assert.deepEqual(t.phrases.map((x) => +x.toFixed(6)), [lead(0.5), lead(1.25), lead(2.8), lead(3.3)].map((x) => +x.toFixed(6)));
  // a phrase the narration does not say is placed between its neighbours by word position
  const odd = structuredClone(s);
  odd.beats[1].words = [];
  const t2 = phraseTimes(odd, { id: 't' }, words, phrases, [], 0.38, 0.5);
  assert.equal(+t2.phrases[1].toFixed(6), +lead(1.25).toFixed(6), '"created" is still said');
  const odd2 = structuredClone(s);
  odd2.beats[0].words = odd2.beats[0].words.slice(0, 3);
  odd2.beats[1].words = [];
  const t3 = phraseTimes(odd2, { id: 't' }, words, phrases, [], 0.38, 0.5);
  const a = lead(0.5);
  const b = lead(2.8);
  assert.ok(Math.abs(t3.phrases[1] - (a + (b - a) * (3 / 6))) < 1e-9, `interpolated: ${t3.phrases[1]}`);
  // with no word timing, the phrases are spread over the revealing beat's speech
  const none = structuredClone(s);
  for (const beat of none.beats) beat.words = [];
  const t4 = phraseTimes(none, { id: 't' }, words, phrases, [], 0.38, 0.5);
  assert.deepEqual(t4.phrases.map((x) => +x.toFixed(6)),
    [0, 3, 6, 8].map((i) => +Math.max(0.38, 0.5 + 1.0 * (i / 10) - REVEAL_LEAD).toFixed(6)));
  // and with no beat at all, FALLBACK_GAP apart from the entrance
  const bare = scene([{ id: 't', kind: 'takeaway', text: words.join(' ') }], { duration: 9 });
  assert.deepEqual(phraseTimes(bare, { id: 't' }, words, phrases, [], 0.35, 0.5).phrases.map((x) => +x.toFixed(6)),
    [0, 1, 2, 3].map((k) => +(0.35 + FALLBACK_GAP * k).toFixed(6)));
});

test('phraseTimes: never before the entrance or the phrase ahead, always in before the scene ends; the attribution', () => {
  const words = 'a b c d e f'.split(' ');
  const phrases = phrasesOf(words); // [a b c] [d e f]
  const beats = [{ start: 0.5, speech_end: 1.5, words: [{ text: 'd', start: 0.6 }, { text: 'a', start: 4.9 }] }];
  const s = scene([], { duration: 5, beats });
  const t = phraseTimes(s, { id: 't' }, words, phrases, [], 0.38, 0.5);
  assert.ok(t.phrases[0] >= 0.38 && t.phrases[1] >= t.phrases[0], `monotonic: ${t.phrases}`);
  assert.ok(t.phrases[1] <= 5 - 0.5 - SETTLE_MARGIN + 1e-9, 'in before the end');
  // a quote's attribution: when the narration names it, else ATTRIBUTION_DELAY after the last phrase is in
  const q = scene([], { duration: 6, beats: [{ start: 0.5, speech_end: 2.5, words: [
    { text: 'a', start: 0.5 }, { text: 'd', start: 1.0 }, { text: 'Marie', start: 2.0 }, { text: 'Curie', start: 2.3 }] }] });
  const named = phraseTimes(q, { id: 't' }, words, phrases, ['Marie', 'Curie'], 0.38, 0.5);
  assert.ok(Math.abs(named.attribution - (2.0 - REVEAL_LEAD)) < 1e-9);
  const unnamed = phraseTimes(q, { id: 't' }, words, phrases, ['Ada'], 0.38, 0.5);
  assert.ok(Math.abs(unnamed.attribution - (1.0 - REVEAL_LEAD + 0.5 + ATTRIBUTION_DELAY)) < 1e-9);
});

test('lineIndices / lineDoneTimes: lines from the masks\' tops; a line is done when the next one starts landing', () => {
  const masks = /** @type {any[]} */ ([0, 0, 0, 105, 105, 210].map((offsetTop) => ({ offsetTop })));
  const lines = lineIndices(masks, 105);
  assert.deepEqual(lines, [0, 0, 0, 1, 1, 2]);
  assert.deepEqual(lineDoneTimes(lines, [0.4, 0.4, 0.4, 1.1, 1.2, 2.0]), [1.1, 2.0, Infinity]);
  assert.deepEqual(lineDoneTimes([], []), []);
  assert.deepEqual(lineIndices(/** @type {any[]} */ ([{ offsetTop: 0 }, { offsetTop: 30 }]), 105), [0, 0], 'a TeX word\'s taller box is one line');
  // a line holding a term cue waits for it to leave; the last line never dims
  assert.deepEqual(lineDoneTimes(lines, [0.4, 0.4, 0.4, 1.1, 1.2, 2.0], [3.0, -Infinity, 9]), [3.0, 2.0, Infinity]);
  assert.deepEqual(lineDoneTimes(lines, [0.4, 0.4, 0.4, 1.1, 1.2, 2.0], [0.5]), [1.1, 2.0, Infinity], 'a cue gone earlier changes nothing');
});

test('termCueEnds / termLines: when the stage\'s term cues on the statement leave, and which lines hold each term', () => {
  const cue = (/** @type {string} */ target, /** @type {number} */ t, /** @type {number | undefined} */ dur) => ({ kind: 'cue_term', target, t, ...(dur ? { dur } : {}) });
  const exit = TERM_DRAW * EXIT_FACTOR;
  const s = scene([{ id: 't', kind: 'takeaway', text: 'x' }], { events: [cue('t', 1.0), cue('t', 4.0, 0.5), cue('other', 9)] });
  // `<item>` marks every term (cues.js CUE_HOLD without `dur`); a cue on another item is not this statement's
  assert.deepEqual(termCueEnds(s, 't', 2), [4.0 + 0.5 + exit, 4.0 + 0.5 + exit].map((x) => Math.max(x, 1.0 + CUE_HOLD + exit)));
  assert.deepEqual(termCueEnds(scene([]), 't', 1), [-Infinity]);
  // `<item>:term:<n>` marks the n-th term only, when this template's consumes lets the form through (cueEvents)
  const forms = /** @type {any} */ (CATALOGUE.templates)[ID].consumes.forms.cue_term;
  const one = termCueEnds(scene([], { events: [cue('t:term:1', 2.0)] }), 't', 2);
  assert.deepEqual(one, forms.includes('item:term') ? [-Infinity, 2.0 + CUE_HOLD + exit] : [-Infinity, -Infinity]);
  // the lines: from the masks holding a term's pieces (the space piece between two words counts on neither)
  const text = document.createElement('p');
  text.innerHTML = '<span class="dyn-ks-mask"><span class="dyn-word"><span class="rt-k" data-dyn-term="0">a</span></span></span>'
    + '<span class="rt-k" data-dyn-term="0"> </span>'
    + '<span class="dyn-ks-mask"><span class="dyn-word"><span class="rt-k" data-dyn-term="0">b</span></span></span> '
    + '<span class="dyn-ks-mask"><span class="dyn-word">c</span></span> '
    + '<span class="dyn-ks-mask"><span class="dyn-word"><span class="rt-k" data-dyn-term="1">d</span></span></span>';
  const masks = /** @type {HTMLElement[]} */ ([...text.querySelectorAll('.dyn-ks-mask')]);
  const lines = termLines(text, masks, [0, 1, 1, 2]);
  assert.deepEqual([...lines.keys()], [0, 1]);
  assert.deepEqual([.../** @type {Set<number>} */ (lines.get(0))], [0, 1], 'a term wrapped over two lines holds both');
  assert.deepEqual([.../** @type {Set<number>} */ (lines.get(1))], [2]);
});

test('dimmedInk: the ink at the line\'s opacity over the text\'s background (what preflight checks); 70% still reads', () => {
  assert.equal(dimmedInk('#F3F5F9', 1, '#101320'), '#F3F5F9');
  const d = dimmedInk('#F3F5F9', DIM_ALPHA, '#101320');
  assert.match(d, /^#[0-9a-f]{6}$/i);
  assert.equal(d, composite(`#F3F5F9${Math.round(DIM_ALPHA * 255).toString(16)}`, '#101320'));
  assert.ok(contrastRatio(d, '#101320') >= 3, 'large text at the dim still passes 3:1');
  assert.equal(DIM_ALPHA, /** @type {any} */ (CATALOGUE).layout.alpha.dim, 'the catalogue\'s dim alpha');
  assert.ok(LINE_DIM * 0.85 >= 0.3, 'R-54: a dim ramps over >= 300 ms at Crisp\'s scale');
});

test('setsAtFrame0: a reel\'s first scene is its feed cover (R-03); everywhere else the phrases land', () => {
  assert.ok(setsAtFrame0('portrait', 0));
  assert.ok(!setsAtFrame0('portrait', 1));
  assert.ok(!setsAtFrame0('landscape', 0));
});

test('texOf / attributionOf: the $...$ spans of the statement and a quote\'s caption, inline', () => {
  const quote = scene([{ id: 'q', kind: 'quote', text: 'It is $V / R$.', caption: 'Georg $\\Omega$hm' }]);
  assert.deepEqual(texOf(quote), [{ tex: 'V / R', display: false }, { tex: '\\Omega', display: false }]);
  assert.deepEqual(kinetic.tex?.(quote, null, 'portrait'), texOf(quote));
  assert.equal(attributionOf(/** @type {any} */ ({ kind: 'quote', caption: ' ' })), null);
  assert.equal(attributionOf(/** @type {any} */ ({ kind: 'takeaway', caption: 'x' })), null, 'only a quote has one');
  assert.deepEqual(texOf(scene([{ id: 't', kind: 'takeaway', text: 'No maths' }])), []);
});

test('lineHeight and the type scale: Latin and Indic display / body (plan §2)', () => {
  assert.equal(lineHeight('display', 'Energy is conserved'), LINE_HEIGHT.display.latin);
  assert.equal(lineHeight('display', 'மின்னோட்டம் கூடும்'), LINE_HEIGHT.display.indic);
  assert.equal(lineHeight('body', 'मारी क्यूरी'), LINE_HEIGHT.body.indic);
  const scale = /** @type {any} */ (CATALOGUE).type.scale;
  assert.equal(LINE_HEIGHT.display.latin, scale.display.line_height.latin[1]);
  assert.equal(LINE_HEIGHT.display.indic, scale.display.line_height.indic[0]);
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0]);
  assert.ok(TRACKING_EM >= scale.display.tracking_em[0] && TRACKING_EM <= scale.display.tracking_em[1]);
});

test('sizes keep §4.3 and R-29: 100-140 px over 4 lines in 16:9, the 9:16 hook range over 4 lines that always fit the box', () => {
  assert.deepEqual([LAYOUT.landscape.statement.min, LAYOUT.landscape.statement.max, LAYOUT.landscape.statement.lines], [100, 140, 4]);
  assert.deepEqual([LAYOUT.portrait.statement.min, LAYOUT.portrait.statement.max], [...TYPE_FLOORS.portrait.hook]);
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    assert.ok(L.statement.min >= floors.text_min);
    assert.ok(L.label >= floors.label_min && L.attribution >= floors.label_min);
    if (floors.heavy_max_px) assert.ok(L.statement.min > floors.heavy_max_px, '9:16: never in the heavy range');
    // the statement's lines at the maximum fit the stack box in every script, so the line-count fit Python does and
    // the page's height check agree whenever nothing else is in the stack
    const b = boxes(fmt, ZONES[fmt]).stack;
    assert.ok(L.statement.lines * L.statement.max * LINE_HEIGHT.display.indic <= b.y1 - b.y0, `${fmt}: four Indic lines at the max`);
    // and with a label, or a two-line attribution, at the minimum: four lines always fit beside a label, and beside a
    // two-line attribution in Latin; only a four-line Indic quote with a two-line Indic caption is past the 9:16 box
    // (the page reports it and preflight blocks with "Shorten text": the LAYOUT reading, pinned here)
    const room = b.y1 - b.y0 + 1;
    const label = L.label * LINE_HEIGHT.body.latin + L.gap;
    const attr = (/** @type {number} */ lh) => L.attributionLines * L.attribution * lh + L.gap;
    for (const lh of [LINE_HEIGHT.display.latin, LINE_HEIGHT.display.indic]) {
      assert.ok(L.statement.lines * L.statement.min * lh + label <= room, `${fmt}: four lines and a label at the min`);
    }
    assert.ok(L.statement.lines * L.statement.min * LINE_HEIGHT.display.latin + attr(LINE_HEIGHT.body.latin) <= room,
      `${fmt}: four Latin lines and a two-line attribution`);
    assert.equal(L.statement.lines * L.statement.min * LINE_HEIGHT.display.indic + attr(LINE_HEIGHT.body.indic) <= room,
      fmt === 'landscape', `${fmt}: four Indic lines and a two-line Indic attribution`);
  }
  assert.equal(SIZE_STEP, 4);
  assert.ok(MASK_PAD_EM >= 0.25, 'the reveal\'s clip reaches past the tight display line box');
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, each box it overlaps holding its
 * whole width.
 * @param {{x: number, y: number, w: number, h: number}} r
 * @param {{x0: number, y0: number, x1: number, y1: number}[]} bands
 */
function insideBands(r, bands) {
  let covered = 0;
  for (const b of bands) {
    const overlap = Math.min(r.y + r.h, b.y1) - Math.max(r.y, b.y0);
    if (overlap <= 1) continue;
    if (r.x < b.x0 - 1 || r.x + r.w > b.x1 + 1) return false;
    covered += overlap;
  }
  return covered >= r.h - 1;
}

test('geometry: 16:9 grid columns 1-10 below the header; 9:16 the text-above box, centred on the hook band', () => {
  const land = boxes('landscape', ZONES.landscape);
  assert.deepEqual([land.stack.x0, land.stack.x1], [96, 1532], 'ten grid columns from the left edge');
  assert.deepEqual(COLUMNS, [0, 9]);
  assert.ok(land.stack.y0 >= ZONES.landscape.header.y1, 'clear of the kicker row');
  assert.equal(land.stack.y1, 912);
  const port = boxes('portrait', ZONES.portrait);
  assert.deepEqual(port.stack, CATALOGUE.regions.portrait['text-above'], 'the upper critical box (R-59)');
  assert.deepEqual(port.band, CATALOGUE.regions.portrait.hook, 'the hook band the reel\'s hook headline holds');
  for (const [fmt, b] of /** @type {const} */ ([['landscape', land.stack], ['portrait', port.stack]])) {
    assert.ok(insideBands({ x: b.x0, y: b.y0, w: b.x1 - b.x0, h: b.y1 - b.y0 }, ZONES[fmt].critical), `${fmt}: inside the critical area`);
  }
  // a short stack sits in the middle of the hook band; a taller one grows evenly, then stays inside the box
  assert.equal(stackTop(port, 200), 450);
  assert.equal(stackTop(port, 420), 340, 'the band exactly');
  assert.equal(stackTop(port, 500), 300);
  assert.equal(stackTop(port, 552), 288, 'the whole box');
  assert.equal(stackTop(port, 700), 288, 'past it: from the top, the overflow reported');
  assert.equal(stackTop(land, 400), Math.round((land.stack.y0 + land.stack.y1) / 2 - 200));
});

test('slotRows: the slot table templates.py mirrors (the boxes\' widths, the statement, the label and the attribution)', () => {
  const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(Object.keys(rows).sort(), ['attribution', 'label', 'statement']);
  assert.equal(rows.statement.landscape.default.w, 1436);
  assert.equal(rows.statement.portrait.default.w, 696);
  assert.deepEqual(rows.statement.landscape.default.size, [100, 140]);
  assert.deepEqual(rows.statement.portrait.default.size, [88, 104]);
  for (const fmt of ['landscape', 'portrait']) {
    const st = rows.statement[fmt].default;
    assert.deepEqual([st.role, st.face, st.weight, st.max_lines, st.line_height, st.tracking_em], ['title', 'display', 700, 4, 1.05, TRACKING_EM]);
    assert.deepEqual([rows.label[fmt].default.max_lines, rows.label[fmt].default.weight, rows.label[fmt].default.role], [1, 600, 'label']);
    assert.equal(rows.attribution[fmt].default.max_lines, LAYOUT[/** @type {'landscape'} */ (fmt)].attributionLines);
    for (const slot of ['statement', 'label', 'attribution']) assert.deepEqual(Object.keys(rows[slot][fmt]), ['default'], 'no look variants');
  }
});

test('the catalogue\'s kinetic_statement entry (templates.py) is slotRows exactly with its consumes, once it is registered', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates)[ID];
  const cat = /** @type {any} */ (templateSlots(ID));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.equal(spec.class, 'statement');
  assert.equal(spec.release, 'R1');
  assert.deepEqual(spec.formats, ['landscape', 'portrait']);
  assert.deepEqual(spec.variants, { landscape: [], portrait: [] }, '§4.3 gives it no variants');
  if (!spec.implemented) {
    // before the integrator registers it (templates.py implemented=True, slots=_kinetic_statement_slots()), the
    // catalogue has no slot table for it; this test turns strict the moment it does
    assert.deepEqual(cat || {}, {}, 'an unregistered template has no slots in the catalogue');
    return;
  }
  for (const slot of Object.keys(want)) {
    for (const fmt of Object.keys(want[slot])) {
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face,
          weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines, tracking_em: got.tracking_em },
        want[slot][fmt][variant], `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  assert.deepEqual(spec.consumes.forms, { reveal: ['item'], cue_term: ['item', 'item:term'] });
  assert.equal(spec.push_ok, false, 'typography: no camera push');
  assert.equal(spec.dense, false);
});

test('mediaBox: none (typography only); a scene handed on keeps board_generic\'s', () => {
  const s = scene([{ id: 't', kind: 'takeaway', text: 'One idea.' }]);
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const slot of ['main', 'panel', undefined]) assert.equal(kinetic.mediaBox?.(s, ZONES[fmt], 'render', slot, { safe: false, slots: {} }), null);
  }
  const other = scene([{ id: 't', kind: 'takeaway', text: 'x' }, { id: 'b', kind: 'bullet', text: 'y' }]);
  assert.deepEqual(kinetic.mediaBox?.(other, ZONES.landscape, 'render', 'main', { safe: false, slots: {} }),
    boardGeneric.mediaBox?.(other, ZONES.landscape, 'render', 'main', { safe: false, slots: {} }));
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9: each phrase lands on its first spoken word (R-17 lead), the later ones still out, in the personality\'s time', async () => {
  const tl = load('kinetic_statement.landscape.json'); // Soft: fade_rise, x1.15
  const scale = tl.presentation.motion_scale;
  const i = indexOf(tl, 'ks-energy');
  const sc = tl.scenes[i];
  const at = spokenAt(sc, 'created') - REVEAL_LEAD; // the second phrase: "created or destroyed,"
  const s = spyStage(tl);
  await s.seekTo(i, at - 0.05);
  let v = s.ctx(i).view;
  assert.deepEqual(v.phrases, [[0, 1, 2], [3, 4, 5], [6, 7], [8, 9]]);
  const alpha = (/** @type {number} */ k) => op(v.words[k].style.opacity);
  assert.ok(alpha(0) > 0.9, 'the first phrase is in');
  assert.deepEqual([3, 4, 5, 6, 7, 8, 9].map(alpha), [0, 0, 0, 0, 0, 0, 0], 'nothing ahead of its word');
  await s.seekTo(i, at + 0.2);
  v = s.ctx(i).view;
  assert.ok(alpha(3) > 0 && alpha(3) < 1 && alpha(3) === alpha(4) && alpha(4) === alpha(5), 'the phrase lands as one');
  assert.equal(alpha(6), 0);
  assert.match(v.words[3].style.transform, /^translate\(0, \d+px\)$/, 'a whole-px 2D rise (§P)');
  const dy = Number(/translate\(0, (\d+)px/.exec(v.words[3].style.transform)?.[1]);
  assert.ok(dy > 0 && dy <= 16, 'Soft\'s 16 px rise');
  assert.equal(v.masks[3].style.overflow, 'visible', 'Soft never clips');
  await s.seekTo(i, at + scale * PHRASE_ENTER + 0.05);
  assert.equal(alpha(3), 1);
  assert.equal(v.words[3].style.transform, 'none');
  // the attribution of the quote scene lands when the narration names it
  const j = indexOf(tl, 'ks-curie');
  const named = spokenAt(tl.scenes[j], 'Marie') - REVEAL_LEAD;
  await s.seekTo(j, named - 0.05);
  const w = s.ctx(j).view;
  assert.equal(op(w.attr.el.style.opacity), 0);
  assert.equal(w.attr.el.textContent, `${ATTRIBUTION_PREFIX}Marie Curie`);
  await s.seekTo(j, named + scale * PHRASE_ENTER + 0.05);
  assert.equal(op(w.attr.el.style.opacity), 1);
  assert.equal(w.attr.el.style.color, rgb(s.ctx(j).onBackground.ink2));
  s.done();
});

test('Crisp: each word is revealed from its baseline inside its mask, clipped only while it moves', async () => {
  const tl = load('kinetic_statement.landscape.light.json'); // Crisp: baseline_reveal
  const i = indexOf(tl, 'ks-live');
  const sc = tl.scenes[i];
  const at = spokenAt(sc, 'Never') - REVEAL_LEAD;
  const s = spyStage(tl);
  await s.seekTo(i, at + 0.1);
  const v = s.ctx(i).view;
  assert.ok(v.reveal);
  assert.equal(v.words[0].style.opacity, '1', 'the reveal shows the word whole, moving');
  assert.equal(v.masks[0].style.overflow, 'hidden', 'the mask clips while the word moves');
  assert.match(v.masks[0].style.padding, /0\.3em/);
  assert.match(v.masks[0].style.margin, /-0\.3em/, 'pulled back: the paragraph flows as plain text');
  await s.seekTo(i, at + PHRASE_ENTER + 0.1);
  assert.equal(v.masks[0].style.overflow, 'visible', 'settled text is never clipped');
  assert.equal(v.words[0].style.transform, 'none');
  s.done();
});

test('the label of a callout: board_generic\'s word for its kind, above the statement in ink2, in with the item', async () => {
  const tl = load('kinetic_statement.landscape.light.json');
  const i = indexOf(tl, 'ks-live');
  const s = spyStage(tl);
  await s.seekTo(i, enterOf(tl.scenes[i], 'w') + 0.5);
  const ctx = s.ctx(i);
  const v = ctx.view;
  assert.equal(v.label.el.textContent, BOARD_LABELS.warning);
  assert.equal(LABELS.callout_tip, BOARD_LABELS.tip);
  assert.equal(v.label.el.style.color, rgb(ctx.onBackground.ink2));
  assert.equal(v.stack.firstElementChild, v.label.g, 'above the statement');
  assert.equal(op(v.label.el.style.opacity), 1);
  s.done();
});

test('9:16 promise beat: after the hook, in the hook band, landing with the narration; a reel\'s first scene is set whole', async () => {
  const tl = load('kinetic_statement.portrait.json');
  assert.equal(tl.scenes[0].look.template, 'hook_question', 'the Hook act opens with the hook');
  const s = spyStage(tl);
  await s.seekTo(1, 0.1);
  let v = s.ctx(1).view;
  assert.ok(!v.cover);
  assert.ok(v.words.every((/** @type {any} */ w) => op(w.style.opacity) === 0), 'nothing before the first word');
  s.done();
  const cover = load('kinetic_statement.portrait.light.json');
  const c = spyStage(cover);
  await c.seekTo(0, 0);
  v = c.ctx(0).view;
  assert.ok(v.cover, 'the reel\'s first scene: its feed cover');
  assert.deepEqual([...new Set(v.words.map((/** @type {any} */ w) => `${op(w.style.opacity)}|${w.style.transform}`))], ['1|none'],
    'frame 0 shows the statement fully set, in place');
  assert.deepEqual([...new Set(v.masks.map((/** @type {any} */ m) => op(m.style.opacity)))], [1], 'and lit: no dim on the cover');
  const entries = kinetic.texts?.(c.ctx(0)) || [];
  assert.ok(entries.every((e) => e.alpha === 1), 'preflight checks it on frame 0');
  // a later scene still lands
  await c.seekTo(1, 0.1);
  assert.ok(c.ctx(1).view.words.every((/** @type {any} */ w) => op(w.style.opacity) < 1));
  c.done();
});

test('reduced motion (live only): every entrance fades in place', async () => {
  const tl = load('kinetic_statement.landscape.light.json');
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  const i = indexOf(tl, 'ks-live');
  await s.seekTo(i, spokenAt(tl.scenes[i], 'Never') - REVEAL_LEAD + 0.1);
  const v = s.ctx(i).view;
  assert.deepEqual([...new Set(v.words.map((/** @type {any} */ w) => w.style.transform))], ['none']);
  assert.deepEqual([...new Set(v.masks.map((/** @type {any} */ m) => m.style.overflow))], ['visible']);
  assert.ok(op(v.words[0].style.opacity) > 0 && op(v.words[0].style.opacity) < 1, 'a fade');
  s.done();
});

test('a scene this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  for (const name of ['kinetic_statement.landscape.json', 'kinetic_statement.portrait.stress.json']) {
    const tl = load(name);
    const i = tl.scenes.findIndex((/** @type {any} */ x) => x.look.template === ID);
    tl.scenes[i].board.push({ id: 'b1', kind: 'bullet', text: 'An extra point this template does not lay out' });
    const s = spyStage(tl);
    await s.seekTo(i, 1.0);
    assert.ok(s.stage.el.querySelector('.dyn-board'), `${name}: board_generic drew it`);
    assert.equal(s.stage.el.querySelector('.dyn-ks'), null);
    const ctx = s.ctx(i);
    assert.ok(ctx.view.ksFallback);
    const sc = tl.scenes[i];
    const fmt = tl.presentation.format;
    assert.deepEqual(kinetic.tex?.(sc, sc.look, fmt), boardGeneric.tex?.(sc, sc.look, fmt));
    assert.deepEqual(kinetic.texts?.(ctx), boardGeneric.texts?.(ctx));
    const id = sc.board[0].id;
    assert.equal(kinetic.itemEl?.(ctx, id), boardGeneric.itemEl?.(ctx, id));
    assert.deepEqual(kinetic.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
    assert.deepEqual(kinetic.focusRect?.(ctx, id), boardGeneric.focusRect?.(ctx, id));
    s.done();
  }
});

test('the cues the stage draws from this template\'s geometry: term spans in field order, whole-px rects, stable groups', async () => {
  const tl = load('kinetic_statement.landscape.json');
  const s = spyStage(tl);
  const i = indexOf(tl, 'ks-energy');
  await s.seekTo(i, 2.0);
  const ctx = s.ctx(i);
  const v = ctx.view;
  assert.ok(v.text.querySelectorAll('.rt-k').length > 0, 'richWords kept the [[term]] spans inside the masks');
  assert.ok(v.text.querySelector('.dyn-ks-mask .rt-k'), 'inside a mask');
  assert.equal(kinetic.itemEl?.(ctx, 't'), v.text);
  assert.equal(kinetic.itemEl?.(ctx, 'nope'), null);
  const r = kinetic.focusRect?.(ctx, 't:term:0');
  assert.ok(r && ['x', 'y', 'w', 'h'].every((k) => Number.isInteger(/** @type {any} */ (r.rect)[k])), 'whole px');
  assert.deepEqual([r?.shape, r?.kind, r?.el], ['rect', 'text', v.textGroup]);
  assert.equal(kinetic.focusRect?.(ctx, 'panel'), null);
  const groups = kinetic.dimGroups?.(ctx) || [];
  assert.deepEqual(groups, [v.textGroup]);
  await s.seekTo(i, 4.0);
  assert.deepEqual(kinetic.dimGroups?.(ctx), groups, 'the same elements at every t');
  assert.equal(kinetic.anchor?.(ctx), null);
  // the quote: the statement, then its attribution (the item's text and caption fields, in order)
  const j = indexOf(tl, 'ks-curie');
  await s.seekTo(j, 1.0);
  const qc = s.ctx(j);
  assert.deepEqual(kinetic.itemEl?.(qc, 'qt'), [qc.view.text, qc.view.attr.el]);
  assert.deepEqual(kinetic.dimGroups?.(qc), [qc.view.textGroup, qc.view.attr.g]);
  s.done();
});

test('preflight\'s text entries (§7.12): every statement word in its own box and ink, the label and the attribution', async () => {
  const tl = load('kinetic_statement.landscape.light.json');
  const s = spyStage(tl);
  const i = indexOf(tl, 'ks-live');
  await s.seekTo(i, tl.scenes[i].duration - 0.05);
  const ctx = s.ctx(i);
  const v = ctx.view;
  const entries = kinetic.texts?.(ctx) || [];
  assert.deepEqual([...new Set(entries.map((e) => e.slot))], ['label', 'statement']);
  const words = entries.filter((e) => e.slot === 'statement');
  assert.equal(words.length, v.words.length);
  assert.deepEqual(words.map((e) => e.el), v.words);
  for (const e of words) {
    assert.deepEqual([e.role, e.critical, e.weight, e.alpha], ['title', true, 700, 1], 'settled at the end');
    assert.equal(e.bg, ctx.onBackground.bg, 'no scrim planned: the background itself');
    assert.ok(Number(e.size) >= LAYOUT.landscape.statement.min && Number(e.size) <= LAYOUT.landscape.statement.max);
    assert.equal(e.text, 'Never touch a live wire with wet hands.');
  }
  const label = /** @type {any} */ (entries.find((e) => e.slot === 'label'));
  assert.deepEqual([label.role, label.weight, label.size, label.ink], ['label', 600, LAYOUT.landscape.label, ctx.onBackground.ink2]);
  s.done();
});

test('preflight\'s remedies (§7.12): a scrim fix puts the stack on a scrim at the slot\'s width; a size cap bounds the fit', async () => {
  const tl = load('kinetic_statement.portrait.stress.json');
  tl.scenes[1].look.fixes = { safe: false, slots: { statement: { scrim: true } } };
  const s = spyStage(tl);
  await s.seekTo(1, 3.0);
  const ctx = s.ctx(1);
  assert.ok(ctx.view.scrim);
  assert.ok(ctx.view.holder.style.background.length > 0, 'drawn behind the whole stack');
  const b = boxes('portrait', ZONES.portrait).stack;
  assert.equal(ctx.view.holder.style.width, `${b.x1 - b.x0 + 2 * 24}px`, 'the content keeps the slot\'s width');
  assert.equal(ctx.view.holder.style.left, `${b.x0 - 24}px`);
  const st = /** @type {any} */ ((kinetic.texts?.(ctx) || []).find((e) => e.slot === 'statement'));
  assert.notEqual(st.bg, ctx.onBackground.bg, 'reported on the composited scrim');
  assert.match(st.bg, /^#[0-9a-f]{6}$/i);
  s.done();
  const tl2 = load('kinetic_statement.portrait.stress.json');
  tl2.scenes[1].look.fixes = { safe: false, slots: { statement: { size: LAYOUT.portrait.statement.min } } };
  const capped = spyStage(tl2);
  await capped.seekTo(1, 1.0);
  assert.equal(capped.ctx(1).view.size, LAYOUT.portrait.statement.min);
  capped.done();
});

// --- the fit and the lines, on a fake text layout -------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily in the
 * holder's content width, a word wider than its line breaks across lines; a mask sits at its word's line's top (less
 * its padding). Enough for this template's fit, placement and lines. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'scrollWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  /** @param {Element | null} el @returns {number} */
  const fontSize = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const v = Number.parseFloat(/** @type {HTMLElement} */ (e).style?.fontSize || '');
      if (v > 0) return v;
    }
    return 16;
  };
  /** @param {HTMLElement} el */
  const lineH = (el) => {
    const v = Number.parseFloat(el.style.lineHeight || '');
    return (v > 0 ? v : 1.3) * fontSize(el);
  };
  /** @param {Element} el the content width of the holder `el` is in */
  const boxW = (el) => {
    const holder = /** @type {HTMLElement | null} */ (el.closest('.dyn-ks-holder'));
    if (!holder) return 0;
    const pad = (holder.style.padding || '0px 0px').split(/\s+/).map((x) => Number.parseFloat(x) || 0);
    return (Number.parseFloat(holder.style.width) || 0) - 2 * (pad[1] ?? pad[0] ?? 0);
  };
  /** @param {HTMLElement} host @returns {{lines: number, of: Map<Element, number>}} */
  const flow = (host) => {
    const size = fontSize(host);
    const width = boxW(host);
    let n = 1;
    let x = 0;
    /** @type {Map<Element, number>} */
    const of = new Map();
    for (const w of host.querySelectorAll('.dyn-word')) {
      const ww = (w.textContent || '').length * CW * size;
      if (width > 0 && ww > width) {
        if (x > 0) n += 1;
        of.set(w, n - 1);
        n += Math.ceil(ww / width) - 1;
        x = ww % width;
        continue;
      }
      const need = x > 0 ? x + CW * size + ww : ww;
      if (width > 0 && need > width) {
        n += 1;
        x = ww;
      } else {
        x = need;
      }
      of.set(w, n - 1);
    }
    return { lines: n, of };
  };
  /** @param {HTMLElement} el */
  const height = (el) => {
    if (el.classList.contains('dyn-word')) {
      const ww = (el.textContent || '').length * CW * fontSize(el);
      const box = boxW(el);
      const host = /** @type {HTMLElement} */ (el.closest('.dyn-ks-text, .dyn-ks-attr') || el);
      return (box > 0 && ww > box ? Math.ceil(ww / box) : 1) * lineH(host);
    }
    if (el.classList.contains('dyn-ks-mask')) {
      return height(/** @type {HTMLElement} */ (el.firstElementChild)) + 2 * MASK_PAD_EM * fontSize(el);
    }
    if (el.classList.contains('dyn-ks-text') || el.classList.contains('dyn-ks-attr')) return flow(el).lines * lineH(el);
    if (el.classList.contains('dyn-ks-label')) return lineH(el);
    if (el.classList.contains('dyn-reveal-mask') || el.classList.contains('dyn-group')) {
      return el.firstElementChild ? height(/** @type {HTMLElement} */ (el.firstElementChild)) : 0;
    }
    if (el.classList.contains('dyn-ks-stack')) {
      const kids = [...el.children];
      const gap = Number.parseFloat(el.style.gap) || 0;
      return kids.reduce((a, k) => a + height(/** @type {HTMLElement} */ (k)), 0) + gap * Math.max(0, kids.length - 1);
    }
    if (el.classList.contains('dyn-ks-holder')) {
      const pad = (el.style.padding || '0px').split(/\s+/).map((x) => Number.parseFloat(x) || 0);
      return height(/** @type {HTMLElement} */ (el.firstElementChild)) + 2 * (pad[0] || 0);
    }
    return 0;
  };
  Object.defineProperty(proto, 'offsetHeight', { configurable: true, get() { return height(/** @type {HTMLElement} */ (this)); } });
  Object.defineProperty(proto, 'offsetTop', {
    configurable: true,
    get() {
      const el = /** @type {HTMLElement} */ (this);
      if (!el.classList.contains('dyn-ks-mask')) return 0;
      const host = /** @type {HTMLElement} */ (el.closest('.dyn-ks-text'));
      const line = flow(host).of.get(/** @type {Element} */ (el.firstElementChild)) || 0;
      return Math.round(line * lineH(host) - MASK_PAD_EM * fontSize(el));
    },
  });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return boxW(/** @type {HTMLElement} */ (this)); } });
  Object.defineProperty(proto, 'scrollWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), /** @type {PropertyDescriptor} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

/** The scene `statementTimeline` writes: the stress fixture's second (never the 9:16 cover). */
const AT = 1;

/**
 * The stress fixture of `fmt` with its second scene's statement replaced by `text` (kind `kind`).
 * @param {'landscape' | 'portrait'} fmt @param {string} text @param {{kind?: string, caption?: string}} [opts]
 */
function statementTimeline(fmt, text, opts = {}) {
  const tl = load(fmt === 'portrait' ? 'kinetic_statement.portrait.stress.json' : 'kinetic_statement.landscape.stress.json');
  tl.scenes[AT].board = [{ id: 't', kind: opts.kind || 'paragraph', text, ...(opts.caption ? { caption: opts.caption } : {}) }];
  tl.scenes[AT].events = [];
  return tl;
}

test('fitInHeight: the largest 4 px size whose lines AND height fit, the minimum last, overflow only at the floor', () => {
  const restore = fakeLayout();
  try {
    const host = document.createElement('div');
    host.className = 'dyn-ks-holder';
    host.style.width = '696px';
    const el = document.createElement('p');
    el.className = 'dyn-ks-text';
    el.style.lineHeight = String(LINE_HEIGHT.display.latin);
    host.appendChild(el);
    document.body.appendChild(host);
    /** @param {string[]} texts */
    const words = (texts) => texts.map((t) => {
      const w = document.createElement('span');
      w.className = 'dyn-word';
      w.textContent = t;
      el.appendChild(w);
      return w;
    });
    const spec = { max: 104, min: 88, lines: 4, lh: LINE_HEIGHT.display.latin, step: SIZE_STEP };
    assert.deepEqual(fitInHeight(el, spec, words(['Energy', 'counts']), [], 552), { size: 104, fits: true, overflow: 0 });
    el.textContent = '';
    const many = words('More volts mean more amps in the same wire'.split(' '));
    const fit = fitInHeight(el, spec, many, [], 552);
    assert.ok(fit.fits && fit.size % SIZE_STEP === 0, `fits at ${fit.size}`);
    assert.ok(fitInHeight(el, spec, many, [], 200).size <= fit.size, 'a lower height never allows a larger size');
    el.textContent = '';
    const huge = words('Why does this very long statement run past every line the column has any room for today'.split(' '));
    const over = fitInHeight(el, spec, huge, [], 552);
    assert.ok(!over.fits && over.size === 88 && over.overflow > 0, `overflow at the floor: ${JSON.stringify(over)}`);
  } finally {
    restore();
    document.body.textContent = '';
  }
});

test('fit (16:9): §4.3\'s 100-140 px over at most 4 lines; completed lines dim to 70% and are reported dimmed', async () => {
  const restore = fakeLayout();
  try {
    const short = spyStage(statementTimeline('landscape', 'Energy is conserved.'));
    await short.seekTo(AT, 1.0);
    assert.equal(short.ctx(AT).view.size, LAYOUT.landscape.statement.max, 'a short statement takes the largest size');
    short.done();
    const tl = load('kinetic_statement.landscape.stress.json');
    const s = spyStage(tl);
    const i = indexOf(tl, 'ks-long');
    await s.seekTo(i, tl.scenes[i].duration - 0.05);
    const ctx = s.ctx(i);
    const v = ctx.view;
    const lines = Math.max(...v.lineOf) + 1;
    assert.ok(lines >= 2 && lines <= LAYOUT.landscape.statement.lines, `${lines} lines at ${v.size}px`);
    assert.equal(v.overflow, 0);
    // every line but the last is dimmed by the end; the last stays lit
    v.masks.forEach((/** @type {any} */ m, /** @type {number} */ k) => {
      const want = v.lineOf[k] < lines - 1 ? DIM_ALPHA : 1;
      assert.equal(op(m.style.opacity), want, `word ${k} on line ${v.lineOf[k]}`);
    });
    const entries = (kinetic.texts?.(ctx) || []).filter((e) => e.slot === 'statement');
    const dimmed = entries.filter((_, k) => v.lineOf[k] < lines - 1);
    assert.ok(dimmed.length > 0 && dimmed.every((e) => e.ink === dimmedInk(ctx.onBackground.ink, DIM_ALPHA, ctx.onBackground.bg)));
    assert.ok(entries.filter((_, k) => v.lineOf[k] === lines - 1).every((e) => e.ink === ctx.onBackground.ink));
    // a line dims only once the next line starts landing, over LINE_DIM
    const done = v.lineDone[0];
    assert.ok(Number.isFinite(done));
    await s.seekTo(i, done - 0.05);
    assert.equal(op(v.masks[0].style.opacity), 1, 'not before the next line');
    s.done();
  } finally {
    restore();
  }
});

test('a line holding a term cue keeps its full strength until the cue has left, then dims', async () => {
  const restore = fakeLayout();
  try {
    const tl = load('kinetic_statement.landscape.json');
    const scale = tl.presentation.motion_scale;
    const i = indexOf(tl, 'ks-energy');
    const s = spyStage(tl);
    await s.seekTo(i, 0.5);
    const v = s.ctx(i).view;
    const lines = termLines(v.text, v.masks, v.lineOf);
    const l = Math.min(.../** @type {Set<number>} */ (lines.get(0)));
    assert.ok(l < Math.max(...v.lineOf), 'the term is not on the last line (which never dims anyway)');
    const next = v.wordAt[v.lineOf.findIndex((/** @type {number} */ x) => x > l)];
    const ends = termCueEnds(tl.scenes[i], 't', 1)[0];
    assert.ok(ends > next && ends + scale * LINE_DIM + 0.1 < tl.scenes[i].duration, 'the case this rule is for');
    const k = v.lineOf.indexOf(l);
    await s.seekTo(i, (next + ends) / 2);
    assert.equal(op(v.masks[k].style.opacity), 1, 'the next line is landing, the cue is still on: not dimmed');
    await s.seekTo(i, ends + scale * LINE_DIM + 0.1);
    assert.equal(op(v.masks[k].style.opacity), DIM_ALPHA, 'dimmed once it has left');
    s.done();
  } finally {
    restore();
  }
});

test('fit (9:16): a short promise sits mid-band, a taller one grows into the stage box, a too-long one is reported', async () => {
  const restore = fakeLayout();
  try {
    const b = boxes('portrait', ZONES.portrait);
    const short = spyStage(statementTimeline('portrait', 'Energy counts.'));
    await short.seekTo(AT, 1.0);
    let v = short.ctx(AT).view;
    const h = v.holder.offsetHeight;
    assert.equal(v.size, LAYOUT.portrait.statement.max);
    assert.equal(Number.parseFloat(v.holder.style.top), Math.round((b.band.y0 + b.band.y1) / 2 - h / 2), 'centred on the hook band');
    short.done();
    const tall = spyStage(statementTimeline('portrait', 'Every volt pushes current harder through the thin wire',
      { kind: 'quote', caption: 'A very patient physics teacher' }));
    await tall.seekTo(AT, 1.0);
    v = tall.ctx(AT).view;
    const top = Number.parseFloat(v.holder.style.top);
    assert.ok(top >= b.stack.y0 && top + v.holder.offsetHeight <= b.stack.y1 + 1, 'inside the text-above box');
    assert.ok(v.attr && v.attr.overflow === 0);
    tall.done();
    const long = spyStage(statementTimeline('portrait', 'Extraordinarily unbelievable electromagnetically interconnected superconductors behave surprisingly'));
    await long.seekTo(AT, 1.0);
    v = long.ctx(AT).view;
    assert.equal(v.size, LAYOUT.portrait.statement.min);
    assert.ok(v.overflow > 0, 'over at the floor: reported, never clipped');
    const entry = /** @type {any} */ ((kinetic.texts?.(long.ctx(AT)) || []).find((e) => e.slot === 'statement'));
    assert.ok(Number(entry.overflow) > 0, 'preflight sees it');
    long.done();
  } finally {
    restore();
  }
});
