// @ts-check
/**
 * The Dynamic stage's type (DYNAMIC_VIDEO_DESIGN §4.6, §7.3; B2 plan §1.3, §2 "Fonts"): the CSS font stacks a timeline
 * is drawn with and the faces the render page loads before `ready`, from `presentation.fonts` and the one face table
 * (faces.gen.js, generated from aadhi/compose/dynamic/faces.py, which mirrors this module: `stage_roles`, `stack`).
 *
 * `presentation.fonts` comes in two forms, told apart by its fields (never by the look-engine generation, §7.3):
 * - the **B1 form** (B1 and B2a timelines; no `indic`): drawn exactly as the B1 stage drew it. Display `outfit` or
 *   `inter` as named, any other id as Outfit (the faces the page had then); body Inter; mono JetBrains Mono; the Noto
 *   Sans Indic faces. The render page loads B1's requests (render/fonts.js `fontRequests`, unchanged).
 * - the **B2 form** (`indic` names an Indic group; the B2b resolver writes it): each role is the family it names when
 *   that family is vendored for the role (display: a display or body face; body, mono: their own), else the role's
 *   default; `indic` is `noto_sans` (Noto Sans per script) or `anek` (Anek per script). The render page loads every
 *   requested weight of each family a stack holds, and the Indic family of every script the timeline uses
 *   (`stageFontRequests`). For the faces the B2b resolver names (B1's), the stacks are the B1 ones, string for
 *   string, so no look changes (web/tests/dynamic/fonts.test.js); the requests are B1's `fontRequests` when display is
 *   Outfit, and B1's without the Outfit faces when display is Inter (no stack holds Outfit then, so it is not loaded:
 *   the pixels are the same). B2e's direct.py rotates the display face.
 *
 * Ids are resolved through the face table only, so no stored string ever reaches CSS as text. The stacks hold, in
 * order: display = display face, body face, Indic families; body = body face, Indic families; mono = the mono face.
 * The Indic families come in the lecture's own script first: the Noto subsets share characters (the Tamil faces also
 * claim U+0964-0965 DANDA, ZWJ/ZWNJ, U+20B9), so a Hindi danda must come from the Devanagari face the render page
 * preloaded, not from a Tamil face loaded lazily mid-render (its text would be invisible until it is in, and drawn
 * with another script's glyph).
 */

import { FACES } from './faces.gen.js';
import { LATIN_SAMPLE, SCRIPT_FONTS, detectScripts, fontRequests, scriptForLanguage } from '../render/fonts.js';

/** @typedef {import('../render/fonts.js').FontRequest} FontRequest */
/** @typedef {{display: string, body: string, mono: string, indic: string}} StageRoles */
/** @typedef {{display: string, body: string, mono: string}} FontStacks */

/** B1 form: the display faces the B1 page had vendored (any other id was drawn as Outfit). faces.py B1_DISPLAY. */
export const B1_DISPLAY_FACES = Object.freeze(/** @type {Record<string, string>} */ ({
  outfit: 'Outfit', inter: 'Inter',
}));
/** B1 form: the Indic families behind every stack (web/vendor/fonts.css), in their default order. */
const B1_INDIC_FAMILIES = Object.freeze([
  'Noto Sans Tamil', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Kannada', 'Noto Sans Malayalam',
]);

/**
 * @param {object} obj @param {unknown} key
 * @returns {boolean}
 */
const owns = (obj, key) => typeof key === 'string' && Object.prototype.hasOwnProperty.call(obj, key);

/**
 * Whether `presentation.fonts` is the B2 form (it names an Indic group).
 * @param {unknown} fonts
 */
export function isB2Fonts(fonts) {
  return !!fonts && typeof fonts === 'object' && typeof (/** @type {any} */ (fonts)).indic === 'string';
}

/**
 * Whether `id` names a vendored family whose role is one of `roles`.
 * @param {unknown} id @param {string[]} roles
 */
function vendoredAs(id, roles) {
  if (!owns(FACES.families, id)) return false;
  const f = /** @type {Record<string, any>} */ (FACES.families)[/** @type {string} */ (id)];
  return !!f.vendored && roles.includes(f.role);
}

/**
 * The family id the stage draws each role with (faces.py `stage_roles`).
 * @param {any} presentation
 * @returns {StageRoles}
 */
export function stageRoles(presentation) {
  const fonts = (presentation && presentation.fonts) || {};
  const d = FACES.defaults;
  if (!isB2Fonts(fonts)) {
    const display = owns(B1_DISPLAY_FACES, fonts.display) ? fonts.display : 'outfit';
    return { display, body: 'inter', mono: 'jetbrains_mono', indic: 'noto_sans' };
  }
  return {
    display: vendoredAs(fonts.display, ['display', 'body']) ? fonts.display : d.display,
    body: vendoredAs(fonts.body, ['body']) ? fonts.body : d.body,
    mono: vendoredAs(fonts.mono, ['mono']) ? fonts.mono : d.mono,
    indic: owns(FACES.indic, fonts.indic) ? fonts.indic : d.indic,
  };
}

/**
 * The CSS family name of a family id of the table.
 * @param {string} id
 */
function familyName(id) {
  return /** @type {Record<string, {name: string}>} */ (FACES.families)[id].name;
}

/**
 * The Indic group's family ids, the lecture's own script first, then the table's script order.
 * @param {string} group @param {string} language
 * @returns {string[]}
 */
export function indicFamilies(group, language = '') {
  const perScript = /** @type {Record<string, Record<string, string>>} */ (FACES.indic)[group];
  const own = scriptForLanguage(language);
  const order = own ? [own, ...FACES.scripts.filter((s) => s !== own)] : [...FACES.scripts];
  return order.map((s) => perScript[s]);
}

/**
 * CSS font stacks of the presentation (display, body, mono) for a lecture in `language` (BCP-47, e.g. "hi-IN").
 * @param {any} presentation
 * @param {string} [language]
 * @returns {FontStacks}
 */
export function fontStacks(presentation, language = '') {
  const fonts = (presentation && presentation.fonts) || {};
  if (!isB2Fonts(fonts)) {
    // the B1 stage's own code (stage.js before B2b), but for an own-property lookup of the display id (B1 read an id
    // such as "constructor" off the object's prototype and wrote a function's text into the stack)
    const display = owns(B1_DISPLAY_FACES, fonts.display) ? B1_DISPLAY_FACES[fonts.display] : 'Outfit';
    const script = scriptForLanguage(language);
    const own = script ? /** @type {Record<string, {family: string}>} */ (SCRIPT_FONTS)[script].family : null;
    const order = own ? [own, ...B1_INDIC_FAMILIES.filter((f) => f !== own)] : B1_INDIC_FAMILIES;
    const indic = order.map((f) => `'${f}'`).join(', ');
    return {
      display: `'${display}', 'Inter', ${indic}, sans-serif`,
      body: `'Inter', ${indic}, sans-serif`,
      mono: "'JetBrains Mono', ui-monospace, monospace",
    };
  }
  const r = stageRoles(presentation);
  const quoted = (/** @type {string} */ id) => `'${familyName(id)}'`;
  const indic = indicFamilies(r.indic, language).map(quoted).join(', ');
  return {
    display: `${quoted(r.display)}, ${quoted(r.body)}, ${indic}, ${FACES.generic.display}`,
    body: `${quoted(r.body)}, ${indic}, ${FACES.generic.body}`,
    mono: `${quoted(r.mono)}, ${FACES.generic.mono}`,
  };
}

/**
 * The faces the render page loads before `ready` (§7.3: every family, weight and script in use). B1 form:
 * render/fonts.js `fontRequests` (B1's list, unchanged). B2 form: each requested weight of the body, display and mono
 * families (each family once, in that order) with Latin sample text, then for every Indic script of the timeline's
 * text and languages (render/fonts.js `detectScripts`, `scriptForLanguage`) the group's family of that script with
 * its sample text. For the B2b resolver's fonts: display Outfit gives the same list as `fontRequests`; display Inter
 * gives that list without the Outfit faces (Outfit is in no stack then).
 * @param {any} timeline
 * @returns {FontRequest[]}
 */
export function stageFontRequests(timeline) {
  const presentation = timeline && timeline.presentation;
  if (!isB2Fonts(presentation && presentation.fonts)) return fontRequests(timeline);
  const r = stageRoles(presentation);
  const families = /** @type {Record<string, {name: string, requests: number[]}>} */ (FACES.families);
  /** @type {FontRequest[]} */
  const out = [];
  for (const id of new Set([r.body, r.display, r.mono])) {
    for (const weight of families[id].requests) out.push({ family: families[id].name, weight, sample: LATIN_SAMPLE });
  }
  const scripts = detectScripts(timeline);
  for (const lang of [timeline && timeline.language, timeline && timeline.board_language]) {
    const s = scriptForLanguage(lang);
    if (s) scripts.add(s);
  }
  const group = /** @type {Record<string, Record<string, string>>} */ (FACES.indic)[r.indic];
  for (const key of [...scripts].sort()) {
    const fam = families[group[key]];
    const sample = /** @type {Record<string, {sample: string}>} */ (SCRIPT_FONTS)[key].sample;
    for (const weight of fam.requests) out.push({ family: fam.name, weight, sample: `${sample}${LATIN_SAMPLE}` });
  }
  return out;
}
