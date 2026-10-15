// @ts-check
/**
 * `hook_question` (DYNAMIC_VIDEO_DESIGN §4.3 A, R1, class `prompt`; B2 plan §1.4 the template contract, §1.5 the cues,
 * §1.11 the slots): the prequestion that opens a lesson or a chapter — one question, set large, with nothing else on
 * the frame (the curiosity gap, LS §5).
 *
 * Data (§4.3, templates.py `_fit_hook_question`): a BoardScene with role `hook` and **one** `question` item (<= 12
 * words; <= 8 in 9:16), plus at most the opening scene's `heading` (`_shape`'s one allowed extra). The question is the
 * hook; the heading, when the scene has one, is set small above it as an eyebrow (board_generic's kicker size and
 * `ink2`), so no item a timeline names is dropped. Nothing else is drawn here: a scene with another item, or with a
 * side panel, is handed to `board_generic` whole (`drawsScene`, every hook handed on: list_build's and formula_focus's
 * convention, B2c review CG-2), and `_fit_hook_question` already scores such a scene 0, so direct.py never sends it.
 *
 * Variants (§4.3, the hook kind of §6.5; `templates.py` `variants`): `question`, `stakes`, `challenge` and (deviation
 * 16) `mystery` are one text in the hook position — §4.3 gives them no part of their own, and `mystery`'s image behind
 * the text at 30% is deferred to B4 with the asset-time blur, so it draws as a plain question here. `true_false` adds
 * the "True or false?" pill above the statement (§4.3; R-63: never a MYTH stamp, and never the `warn` role), which is
 * why it is the one variant with slots of its own: the pill takes room off the question's lines.
 *
 * 16:9 (§4.3): the question in `centre-60` at 112-140 px, at most 3 lines, the stack centred vertically between the
 * header row and the bottom of the critical box, left aligned on the region's edge (every other template's text
 * alignment; §4.3 names no other).
 *
 * 9:16 (§4.3, §2.5, §3.3): the hook band (the catalogue's `hook` region, x 192-888, y 340-760), auto-fitted to at most
 * 4 lines at 88-104 px by measured width (R-29's 9:16 `hook` range; design §4.6 measured four 96 px lines at 403 px in
 * this band). **frame0_hook** (§7.12, plan §2): when the scene is the video's first (`setsAtFrame0`), frame 0 already
 * shows the hook fully set at alpha 1 inside the band — a reel's first frame is its cover — so the words do not land
 * one by one there; in every other scene, and in 16:9 always, they land with the narration.
 *
 * Choreography (§4.3, §4.8): the words rise 24 px and fade in over 450 ms with a 70 ms stagger (board_generic's title
 * rule, which §4.3 repeats for this template word for word, so the per-word landing is the entrance here and the
 * personality's `ctx.motion.entrance` is not used; its duration scale is, through `_shared.js` `seconds` / `enterAt`),
 * the eyebrow fades in first, the pill enters with the question, and the "?" glyph pops once (a 1.08 scale over
 * 300 ms, measured order-independent in §P (a)/(b); no SFX, R-49, and none under reduced motion in the live player).
 * The caption band stays blank while the words are on screen (R-31: the on-screen text IS the caption; kinetic.py's
 * blanking, not this template's).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `itemEl` hands it the question's text (its `[[term]]` `.rt-k` spans,
 * kept by `_shared.js richWords`, so `<item>` and `<item>:term:<n>` both resolve: the keyword of the hook takes
 * `cue.term`), `focusRect` the question's own text box and `dimGroups` the eyebrow, the pill and the question. Nothing
 * here writes outline, text-decoration, filter, backdrop-filter or will-change, and no structure role colours the
 * text (§4.2).
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t; every property it paints is painted on every frame
 * with a value; translates are whole pixels and 2D (`paintRise`); the only scale is the "?" pop, on text (§P (a)).
 * `layout` measures the laid-out page only, and fits the question in 4 px steps the way `_shared.js fitLines` does,
 * with the box's height as a second condition (`fitInBox`: the hook band is 420 px tall, so four 104 px lines do not
 * fit it and the fit must see the height, which the line count alone cannot show).
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { TYPE_FLOORS, fixesOf, fx } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  RADIUS, SPACE, enterAt, group, paintRise, place, region, richWords, scrimmedBg, seconds, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'hook_question';

// ---------------------------------------------------------------------------------------------
// Variants (§4.3; templates.py `variants`)
// ---------------------------------------------------------------------------------------------

/** The hook kinds §4.3 names for this template (templates.py `variants`, in its order). */
export const VARIANTS = Object.freeze(['question', 'true_false', 'stakes', 'mystery', 'challenge']);
/** The variant of a look whose own is missing, `safe` (B1) or unknown. */
export const DEFAULT_VARIANT = 'question';
/**
 * The two layouts the variants draw in: `pill` (`true_false`: the pill above the statement) and `plain` (every other
 * kind, `mystery` included: deviation 16 defers its blurred image to B4). The slot table keys the pill layout under
 * the variant name `true_false`, so `_shared.js slotSpec` finds it and every other variant falls back to `default`.
 */
export const PILL_VARIANT = 'true_false';

/** §4.3: a "True or false?" pill above the statement (never a MYTH stamp, R-63). English until B4's i18n. */
export const PILL_TEXT = 'True or false?';

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 hook_question, §4.8)
// ---------------------------------------------------------------------------------------------

/** §4.3: the words rise this far ... */
export const RISE = 24;
/** ... and fade in over 450 ms ... */
export const WORD_SECONDS = 0.45;
/** ... with a 70 ms stagger (board_generic's WORD_STAGGER) ... */
export const WORD_STAGGER = 0.07;
/** ... compressed so a long hook still lands inside this window (board_generic's rule). */
export const WORD_STAGGER_TOTAL = 0.8;
/** The pill labels the statement, so the words follow it by board_generic's title delay. */
export const TEXT_DELAY = 0.08;
/** The eyebrow (the scene's heading) fades in with the frame (board_generic's header). */
export const EYEBROW_ENTER = 0.35;
/** §4.3: the "?" glyph pops once — up and back over 300 ms (§4.8 small move), to this scale (§P (a), (b)). */
export const POP = 0.3;
export const POP_SCALE = 0.08;
/** §4.3: the scene holds at least this long after the last word (events.py / direct.py own the length; tested). */
export const HOLD_AFTER = 0.8;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; the catalogue's regions, type floors and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (catalogue `type.scale`; plan §2: Indic display >= 1.25, Indic body >= 1.45). */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
/** The display face's tracking (§4.6 type scale display -0.02..-0.01, as formula_focus and media_split set it). */
export const TRACKING_EM = -0.015;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

/**
 * Per-format layout. `question`: the hook's size range and line count (16:9 §4.3's 112-140 px and 3 lines; 9:16
 * R-29's `hook` range 88-104 and §2.5 / §4.6's 4 lines in the band). `pillLines`: the line count Python fits the
 * question at in the `true_false` variant, whose pill takes room off it (the page's own cap is the box's height, which
 * `fitInBox` measures, so a heading costs lines the same way without a variant of its own). `pill`: the "True or
 * false?" label, at or above R-29's label floor and at weight 700 (R-29: 9:16 text at <= 64 px carries at least 600).
 * `eyebrow`: the scene's heading above the hook, at board_generic's kicker size and weight. `gap`: between the stack's rows (the 8 px spacing scale). `pillPad`: the
 * pill's padding (y, x). Every size is a B2 proposal pending the owner's review of the reference frames.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    question: Object.freeze({ max: 140, min: 112, lines: 3 }),
    pillLines: 3,
    pill: 48,
    eyebrow: 40,
    gap: SPACE[4],      // 40
    pillPad: Object.freeze([12, 28]),
  }),
  portrait: Object.freeze({
    question: Object.freeze({ max: 104, min: 88, lines: 4 }),
    // the band is 420 px tall, so the pill costs the hook a line there (the page measures it: `fitInBox`)
    pillLines: 3,
    pill: 44,
    eyebrow: 44,
    // 24 px, not 32: the band is 420 px and the pill's row takes 79 of them, so the gap is what buys the hook its
    // third line (3 x 88 x 1.05 = 277 px). An INDIC statement under the pill holds two lines at the hook's floor
    // (2 x 88 x 1.25 = 220; a third would need 330 of the 317 px left): a 9:16 true_false hook in an Indic script is
    // short, and preflight blocks a longer one with "Shorten text" rather than clipping it (hook_question.test.js
    // pins this budget; recorded as an open item for the owner's review of the reference frames)
    gap: SPACE[2],      // 24
    pillPad: Object.freeze([10, 24]),
  }),
});

/**
 * The boxes of a format (pure; whole stage px): `stack` (where the eyebrow, the pill and the question are set,
 * vertically centred) — 16:9 the `centre-60` region from below the header row (so a 3-line hook never reaches the
 * kicker row) to the bottom of the critical box; 9:16 the `hook` region exactly (x 192-888, y 340-760: the band
 * frame0_hook checks).
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @returns {{stack: Box}}
 */
export function boxes(format, zones) {
  const ctx = { format };
  if (format === 'portrait') return { stack: region(ctx, 'hook') };
  const mid = region(ctx, 'centre-60');
  return { stack: { x0: mid.x0, y0: Math.max(mid.y0, zones.header.y1 + SPACE[2]), x1: mid.x1, y1: mid.y1 } };
}

/**
 * The text slots of this template (templates.py `SlotSpec`, what textfit.py fits in Python; plan §1.6, §1.11):
 * slot -> format -> variant -> spec. `question` has a `default` (every variant but one) and a `true_false` spec, whose
 * pill costs it a line; `pill` and `eyebrow` are one-line labels in the stack's width. Line heights are the Latin
 * ones (Indic text takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure uses). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @type {Record<string, Record<string, Record<string, any>>>} */
  const out = { question: {}, pill: {}, eyebrow: {} };
  for (const [fmt, zones] of /** @type {const} */ ([['landscape', landscape], ['portrait', portrait]])) {
    const L = LAYOUT[fmt];
    const b = boxes(fmt, zones).stack;
    const w = b.x1 - b.x0;
    /** @param {number} lines */
    const question = (lines) => ({
      w, max_lines: lines, size: [L.question.min, L.question.max], role: 'title', face: 'display', weight: 700,
      line_height: LINE_HEIGHT.display.latin, tracking_em: TRACKING_EM, prefer_lines: null,
    });
    /** @param {number} size @param {number} weight */
    const label = (size, weight) => ({
      w, max_lines: 1, size: [size, size], role: 'label', face: 'body', weight,
      line_height: LINE_HEIGHT.body.latin, prefer_lines: null,
    });
    out.question[fmt] = { default: question(L.question.lines), [PILL_VARIANT]: question(L.pillLines) };
    out.pill[fmt] = { default: label(L.pill, 700) };
    out.eyebrow[fmt] = { default: label(L.eyebrow, 600) };
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (plan §2). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value).
 * @param {'display' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/**
 * The scene's hook (its first `question` item with text), its eyebrow (the first `heading` with text) and every other
 * item with text (`others`: this template draws none of them, so a scene that has any is board_generic's).
 * @param {TimedScene} scene
 * @returns {{question: BoardItem | null, eyebrow: BoardItem | null, others: BoardItem[]}}
 */
export function hookItems(scene) {
  /** @type {BoardItem | null} */
  let question = null;
  /** @type {BoardItem | null} */
  let eyebrow = null;
  /** @type {BoardItem[]} */
  const others = [];
  for (const item of scene.board || []) {
    const text = typeof item.text === 'string' ? item.text.trim() : '';
    if (!text) continue;
    if (item.kind === 'question' && !question) question = item;
    else if (item.kind === 'heading' && !eyebrow) eyebrow = item;
    else others.push(item);
  }
  return { question, eyebrow, others };
}

/**
 * The scene's side panel when the layout shows it (null without one): this template draws none.
 * @param {TimedScene} scene
 */
export function shownPanel(scene) {
  const sp = scene.side_panel;
  return sp && scene.layout?.show_side_panel !== false ? sp : null;
}

/**
 * Whether this template draws the scene whole (else board_generic does, every hook handed on; `_fit_hook_question`
 * scores such a scene 0): one `question` item with text, nothing else with text, no shown side panel.
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  const { question, others } = hookItems(scene);
  return !!question && others.length === 0 && !shownPanel(scene);
}

/**
 * The variant drawn for a look (`look.variant` when §4.3 names it, else `question`); `mystery` draws as a plain
 * question (deviation 16).
 * @param {import('../../shared/types.js').ResolvedLook | null | undefined} look
 */
export function variantOf(look) {
  const v = look && /** @type {any} */ (look).variant;
  return VARIANTS.includes(String(v)) ? String(v) : DEFAULT_VARIANT;
}

/** Whether `variant` draws the "True or false?" pill (§4.3). @param {string} variant */
export function hasPill(variant) {
  return variant === PILL_VARIANT;
}

/**
 * Whether the hook is set whole on frame 0 (the **frame0_hook** rule, §3.3 / §7.12): a 9:16 scene that opens the video
 * (its first scene), whose first frame is the reel's cover. Everywhere else the words land with the narration.
 * @param {'landscape' | 'portrait'} format @param {number} index
 */
export function setsAtFrame0(format, index) {
  return format === 'portrait' && index === 0;
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the eyebrow and the question, inline (`richWords` mounts each with display off).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { question, eyebrow } = hookItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const item of [eyebrow, question]) {
    if (!item) continue;
    for (const tex of mathSpans(String(item.text || ''))) if (tex.trim()) out.push({ tex, display: false });
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// Fitting and the "?" glyph (layout time)
// ---------------------------------------------------------------------------------------------

/**
 * `_shared.js fitLines`' fit with the box's height as a second condition: the largest size, in `step` px steps from
 * `max` down to `min` (the minimum itself last), at which the text takes at most `lines` lines, is at most `height` px
 * tall and keeps every word whole (a word broken even at the minimum does not count against a size; a TeX word wider
 * than the box holds the text at its minimum). The hook band is 420 px tall, so four 104 px lines fit the line count
 * and not the band: the line count alone cannot see that. Leaves the element at the chosen size. `overflow`: px past
 * the smaller of `lines` lines and `height` at the minimum, or the widest TeX word's excess (> 2 px rounding).
 * @param {HTMLElement} el
 * @param {{max: number, min: number, lines: number, lh: number, step?: number}} spec
 * @param {HTMLElement[]} [words]
 * @param {boolean[]} [texFlags]
 * @param {number} [height]
 * @returns {{size: number, fits: boolean, overflow: number}}
 */
export function fitInBox(el, spec, words = [], texFlags = [], height = Infinity) {
  const step = spec.step && spec.step > 0 ? spec.step : SIZE_STEP;
  const isTex = (/** @type {number} */ i) => !!texFlags[i];
  const broken = (/** @type {number} */ s) => words.map((w, i) => (isTex(i)
    ? w.offsetWidth > el.clientWidth + 1
    : w.offsetHeight > 1.5 * s * spec.lh));
  el.style.fontSize = `${spec.min}px`;
  const unavoidable = broken(spec.min);
  const texTooWide = unavoidable.some((b, i) => b && isTex(i));
  const atMin = el.offsetHeight;
  let size = spec.min;
  let fits = false;
  for (let s = texTooWide ? spec.min : Math.max(spec.min, spec.max); ; s = Math.max(spec.min, s - step)) {
    el.style.fontSize = `${s}px`;
    const whole = broken(s).every((b, i) => !b || unavoidable[i]);
    const tall = el.offsetHeight;
    if (whole && Math.round(tall / (s * spec.lh)) <= spec.lines && tall <= height + 1) {
      size = s;
      fits = true;
      break;
    }
    if (s <= spec.min) break;
  }
  el.style.fontSize = `${size}px`;
  const allowed = Math.min(spec.lines * spec.min * spec.lh, height);
  let overflow = fits ? 0 : Math.max(1, Math.round(atMin - allowed));
  const wide = Math.max(0, ...words.map((w, i) => (isTex(i) ? w.offsetWidth - el.clientWidth : 0)));
  if (wide > 2) overflow = Math.max(overflow, wide);
  return { size, fits, overflow };
}

/**
 * The last non-empty text node of `node` in document order (null without one): what `questionGlyph` splits.
 * @param {Node} node
 * @returns {Text | null}
 */
function lastTextNode(node) {
  const kids = node.childNodes;
  for (let i = kids.length - 1; i >= 0; i--) {
    const child = kids[i];
    if (child.nodeType === 3) {
      const text = /** @type {Text} */ (child);
      if (String(text.data).length) return text;
    } else {
      const inner = lastTextNode(child);
      if (inner) return inner;
    }
  }
  return null;
}

/**
 * The hook's final "?" as a span of its own, so it can pop (§4.3) without scaling the words around it: the last text
 * node of `host`, split before its trailing question mark ('?' or the full-width '？'). Null when the hook does not
 * end in one (a `stakes` or `challenge` statement, a Devanagari danda). The span stays inside the word it belongs to,
 * so the word's own entrance still covers it.
 * @param {HTMLElement} host
 * @returns {HTMLElement | null}
 */
export function questionGlyph(host) {
  const last = lastTextNode(host);
  if (!last) return null;
  const trimmed = String(last.data).replace(/\s+$/u, '');
  if (!/[?？]$/u.test(trimmed)) return null;
  const mark = last.splitText(trimmed.length - 1);
  const tail = mark.data.length > 1 ? mark.splitText(1) : null;
  const span = h('span', { class: 'dyn-hq-qm', style: { display: 'inline-block', transformOrigin: '50% 60%' } });
  const parent = /** @type {Node} */ (mark.parentNode);
  parent.insertBefore(span, mark);
  span.appendChild(mark);
  if (tail && !tail.data.trim()) tail.remove();
  return span;
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.hqFallback);

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * The DOM: a stack (eyebrow, pill, question) in a group per part, so the stage owns the dim and this template
   * animates only their children. No measuring here.
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene)) {
      // another item or a side panel: board_generic draws the scene whole (list_build's convention, review CG-2)
      const inner = boardGeneric.build(ctx);
      inner.hqFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { question, eyebrow } = hookItems(scene);
    const item = /** @type {BoardItem} */ (question);
    const variant = variantOf(ctx.look);
    const pill = hasPill(variant);
    const fixes = fixesOf(ctx.look);
    const ink = ctx.onBackground.ink;
    const ink2 = ctx.onBackground.ink2;
    const cover = setsAtFrame0(format, ctx.index);
    const root = h('div', {
      class: ['dyn-hq', `dyn-hq--${format}`, `dyn-hq--${pill ? 'pill' : 'plain'}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // the stack: the eyebrow, the pill and the question, centred in the box (`layout` places it)
    const stack = h('div', {
      class: 'dyn-hq-stack',
      style: {
        display: 'flex', flexDirection: 'column', alignItems: 'flex-start', justifyContent: 'center',
        height: '100%', gap: `${L.gap}px`, boxSizing: 'border-box',
      },
    });

    /** @type {any} */
    let eye = null;
    if (eyebrow) {
      const el = h('div', {
        class: 'dyn-hq-eyebrow', dir: 'auto',
        style: {
          color: ink2, fontFamily: 'var(--dyn-font-body)', fontWeight: '600', fontSize: `${L.eyebrow}px`,
          lineHeight: String(lineHeight('body', eyebrow.text || '')), letterSpacing: '0.02em', maxWidth: '100%',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        },
      });
      const { words, tex } = richWords(ctx, el, String(eyebrow.text || ''));
      const g = group('dyn-hq-eyebrow-group', el);
      stack.appendChild(g);
      eye = { item: eyebrow, el, g, words, tex, alpha: 0, overflow: 0 };
    }

    /** @type {any} */
    let badge = null;
    if (pill) {
      const el = h('span', {
        class: 'dyn-hq-pill', text: PILL_TEXT,
        style: {
          display: 'inline-block', color: ink, background: ctx.roles.color('surface'),
          fontFamily: 'var(--dyn-font-body)', fontWeight: '700', fontSize: `${L.pill}px`,
          lineHeight: String(LINE_HEIGHT.body.latin), padding: `${L.pillPad[0]}px ${L.pillPad[1]}px`,
          borderRadius: `${RADIUS.chip}px`, whiteSpace: 'nowrap',
        },
      });
      const row = h('div', { class: 'dyn-hq-pillrow' }, el);
      const g = group('dyn-hq-pill-group', row);
      stack.appendChild(g);
      badge = { el, g, alpha: 0 };
    }

    const text = h('h2', {
      class: 'dyn-hq-text', dir: 'auto', dataset: { item: item.id },
      style: {
        margin: '0px', color: ink, fontFamily: 'var(--dyn-font-display)', fontWeight: '700',
        lineHeight: String(lineHeight('display', item.text || '')), letterSpacing: `${TRACKING_EM}em`,
        overflowWrap: 'anywhere', maxWidth: '100%',
      },
    });
    const { words, tex } = richWords(ctx, text, String(item.text || ''));
    const glyph = questionGlyph(text);
    const textGroup = group('dyn-hq-textgroup', text);
    stack.appendChild(textGroup);

    // R-53's scrim remedy behind the whole stack when preflight asked for one for any of its slots
    const scrim = ['question', 'pill', 'eyebrow'].some((s) => fixes.slots[s] && fixes.slots[s].scrim)
      ? ctx.roles.color('scrim') : null;
    const holder = h('div', { class: 'dyn-hq-holder' }, stack);
    if (scrim) {
      Object.assign(holder.style, {
        padding: `${SPACE[1]}px ${SPACE[2]}px`, marginLeft: `-${SPACE[2]}px`, borderRadius: `${RADIUS.card}px`,
        background: scrim, boxSizing: 'border-box',
      });
    }
    root.appendChild(holder);
    ctx.root.appendChild(root);

    const enter = new Map(itemTimings(scene).map((x) => [x.id, x.enter]));
    const start = cover ? 0 : Math.max(0, Number(enter.get(item.id)) || 0);
    return {
      root, holder, stack, variant, pill, cover, item, eye, badge, text, textGroup, words, wordTex: tex, glyph, scrim,
      start, size: L.question.max, min: L.question.min, overflow: 0, alpha: 0, popAt: start,
    };
  },

  /**
   * Measure and fit (idempotent; runs again when a late face arrives): place the stack on its box, then fit the
   * question in the height the eyebrow and the pill leave it.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const { format } = ctx;
    const L = LAYOUT[format];
    const b = boxes(format, ctx.zones).stack;
    place(v.holder, b);
    // what the other rows of the stack take off the question (as laid out, so a wrapped eyebrow counts)
    let taken = 0;
    if (v.eye) taken += v.eye.el.offsetHeight + L.gap;
    if (v.badge) taken += v.badge.el.offsetHeight + L.gap;
    const pad = v.scrim ? 2 * SPACE[1] : 0;
    const height = Math.max(0, b.y1 - b.y0 - pad - taken);
    const range = slotRange(ctx, 'question', { min: L.question.min, max: L.question.max, role: 'title' });
    const spec = {
      max: range.max, min: range.min, lines: L.question.lines, lh: lineHeight('display', v.item.text || ''),
      step: SIZE_STEP,
    };
    const fit = fitInBox(v.text, spec, v.words, v.wordTex, height || Infinity);
    v.size = fit.size;
    v.min = range.min;
    v.overflow = fit.overflow;
    if (v.eye) {
      // one line at board_generic's kicker size: px it runs past its column (never clipped silently, §7.12)
      v.eye.overflow = Math.max(0, v.eye.el.scrollWidth - v.eye.el.clientWidth);
    }
  },

  /**
   * Paint scene time t: the eyebrow's fade, the pill's entrance, each word's rise and the "?" pop. A pure function of
   * t: every property is painted on every frame, with a value.
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {import('../../shared/types.js').SceneState} _state
   */
  update(ctx, t, _state) {
    if (isFallback(ctx)) {
      boardGeneric.update(ctx, t, _state);
      return;
    }
    const v = ctx.view;
    const p = ctx.paint;
    const cover = !!v.cover;

    if (v.eye) {
      const e = cover ? 1 : enterAt(ctx, t, 0, EYEBROW_ENTER);
      v.eye.alpha = e;
      p.style(v.eye.el, 'opacity', fx(e));
      p.style(v.eye.el, 'transform', 'none');
    }
    if (v.badge) {
      const e = cover ? 1 : enterAt(ctx, t, v.start, WORD_SECONDS);
      v.badge.alpha = e;
      paintRise(ctx, v.badge.el, e, RISE);
    }

    const n = v.words.length;
    const each = n > 1 ? Math.min(WORD_STAGGER, WORD_STAGGER_TOTAL / (n - 1)) : 0;
    const delay = v.badge ? TEXT_DELAY : 0;
    let alpha = cover ? 1 : 0;
    v.words.forEach((/** @type {HTMLElement} */ w, i) => {
      const e = cover ? 1 : enterAt(ctx, t, v.start + seconds(ctx, delay + each * i), WORD_SECONDS);
      if (i === n - 1) alpha = e;
      paintRise(ctx, w, e, RISE);
    });
    v.alpha = n ? alpha : 1;
    v.popAt = cover ? 0 : v.start + seconds(ctx, delay + each * Math.max(0, n - 1) + WORD_SECONDS);

    if (v.glyph) {
      // one pop: up over the first half (ease.enter), back over the second (ease.exit); none under reduced motion
      const u = progress(t, v.popAt, seconds(ctx, POP));
      const k = ctx.reducedMotion ? 0
        : u < 0.5 ? ease('ease.enter', u * 2) : 1 - ease('ease.exit', (u - 0.5) * 2);
      p.style(v.glyph, 'transform', k > 0 ? `scale(${fx(1 + POP_SCALE * k)})` : 'none');
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.7): the question's own text box (`<item>`, `<item>:term:<n>`) or the
   * eyebrow's, as painted now, on whole stage px; null for anything else or off the page.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const id = String(target || '').split(':')[0];
    const hit = id === v.item.id ? { el: v.text, g: v.textGroup }
      : v.eye && id === v.eye.item.id ? { el: v.eye.el, g: v.eye.g } : null;
    const r = hit ? ctx.rectOf(hit.el) : null;
    if (!r || !hit) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    return {
      rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 },
      shape: 'rect', radius: 0, el: hit.g, kind: 'text',
    };
  },

  /**
   * The element holding an item's text, where cue.term finds its `[[term]]` spans (`.rt-k`, `_shared.js richWords`):
   * the question's text, or the eyebrow's. Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    if (itemId === v.item.id) return v.text;
    return v.eye && itemId === v.eye.item.id ? v.eye.el : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the eyebrow, the pill and the question — the same
   * elements at every t, and this template never writes their opacity (it animates their children).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.eye ? v.eye.g : null, v.badge ? v.badge.g : null, v.textGroup].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none — a hook is not a visual another scene continues (B2f pairs the templates that
   * have one).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: no medium (`mystery`'s image is deferred to B4, deviation 16); a scene this template does not draw keeps
   * board_generic's box.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene)) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the question (slot `question`), the pill (`pill`) and the eyebrow
   * (`eyebrow`), all critical, in the ink, size and weight painted now, on the background's worst case (or on the
   * R-53 scrim over it).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const floors = TYPE_FLOORS[ctx.format];
    /** @param {string} ink */
    const bgOf = (ink) => (v.scrim ? scrimmedBg(v.scrim, ctx.onBackground, ink) : ctx.onBackground.bg);
    /** @type {TextEntry[]} */
    const out = [];
    if (v.eye) {
      out.push({
        el: v.eye.el, role: 'label', slot: 'eyebrow', critical: true, ink: ctx.onBackground.ink2,
        bg: bgOf(ctx.onBackground.ink2), alpha: v.eye.alpha, overflow: v.eye.overflow, size: L.eyebrow,
        min: Math.max(floors.label_min, L.eyebrow), weight: 600, text: plainText(String(v.eye.item.text || '')),
      });
    }
    if (v.badge) {
      out.push({
        el: v.badge.el, role: 'label', slot: 'pill', critical: true, ink: ctx.onBackground.ink,
        bg: ctx.roles.color('surface'), alpha: v.badge.alpha, overflow: 0, size: L.pill,
        min: Math.max(floors.label_min, L.pill), weight: 700, text: PILL_TEXT,
      });
    }
    out.push({
      el: v.text, role: 'title', slot: 'question', critical: true, ink: ctx.onBackground.ink,
      bg: bgOf(ctx.onBackground.ink), alpha: v.alpha, overflow: v.overflow, size: v.size, min: v.min, weight: 700,
      text: plainText(String(v.item.text || '')),
    });
    return out;
  },
};

