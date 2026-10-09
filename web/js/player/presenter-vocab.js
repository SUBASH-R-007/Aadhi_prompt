// @ts-check
/**
 * Presenter vocabulary with a capability-based nearest fallback (pure; no DOM, safe in Node tests).
 *
 * A presenter shows only what it can. A wanted behaviour, expression or gesture it cannot show becomes
 * the nearest one it can, following the fallback chains below (welcoming -> talking -> idle,
 * surprised -> engaged -> friendly -> neutral, counting -> explaining -> open_hand -> none). The
 * substitution is reported as a note ("welcoming shown as talking"), never as an error, and a
 * vocabulary the presenter has nothing of (e.g. gestures for the looping clips) maps to null: nothing
 * is faked.
 *
 * Aadhi's branding clips (CLIP_CAPABILITIES) loop one picture per position, with one face and no
 * gestures on cue, so they can show behaviour states only, through the cue bubble beside his head
 * (mascot-state.js). An artist-made rig (Branding.mascot_rig_url, not loaded yet) would declare its own
 * capabilities and get the same mapping.
 */

export const BEHAVIOURS = Object.freeze([
  'idle', 'talking', 'explaining', 'thinking', 'listening', 'question', 'success', 'welcoming', 'concluding',
]);
export const EXPRESSIONS = Object.freeze(['neutral', 'friendly', 'engaged', 'thinking', 'surprised', 'happy', 'encouraging', 'serious']);
export const GESTURES = Object.freeze(['none', 'open_hand', 'point', 'counting', 'explaining', 'emphasis', 'thinking', 'welcome']);

/** Nearest behaviour to try when one cannot be shown. @type {Readonly<Record<string, string>>} */
export const BEHAVIOUR_FALLBACK = Object.freeze({
  welcoming: 'talking',
  concluding: 'explaining',
  question: 'talking',
  success: 'talking',
  listening: 'thinking',
  thinking: 'idle',
  explaining: 'talking',
  talking: 'idle',
});

/** @type {Readonly<Record<string, string>>} */
export const EXPRESSION_FALLBACK = Object.freeze({
  surprised: 'engaged',
  happy: 'friendly',
  encouraging: 'friendly',
  serious: 'neutral',
  thinking: 'neutral',
  engaged: 'friendly',
  friendly: 'neutral',
});

/** @type {Readonly<Record<string, string>>} */
export const GESTURE_FALLBACK = Object.freeze({
  counting: 'explaining',
  emphasis: 'explaining',
  point: 'open_hand',
  welcome: 'open_hand',
  thinking: 'none',
  explaining: 'open_hand',
  open_hand: 'none',
});

/**
 * @typedef {object} PresenterCapabilities
 * @property {readonly string[]} behaviours
 * @property {readonly string[]} expressions
 * @property {readonly string[]} gestures
 */

/** What the looping branding clips can show. @type {Readonly<PresenterCapabilities>} */
export const CLIP_CAPABILITIES = Object.freeze({
  behaviours: Object.freeze(['idle', 'talking', 'explaining', 'thinking', 'listening', 'question', 'success']),
  expressions: Object.freeze([]),
  gestures: Object.freeze([]),
});

/**
 * The wanted value when the presenter supports it, else the nearest supported one along `fallback`
 * (cycle safe), else `fallbackDefault` when supported, else the first supported value. With nothing
 * supported the value is null.
 * @param {string | null | undefined} wanted
 * @param {readonly string[] | null | undefined} supported
 * @param {Readonly<Record<string, string>>} fallback
 * @param {string} fallbackDefault   used when nothing is wanted, and as the last resort
 * @returns {{ value: string | null, note: string | null }}   note: "<wanted> shown as <value>" / "<wanted> not shown"
 */
export function mapVocab(wanted, supported, fallback, fallbackDefault) {
  if (!supported || supported.length === 0) return { value: null, note: wanted ? `${wanted} not shown` : null };
  /** @type {string | undefined} */
  let value = wanted || fallbackDefault;
  const seen = new Set();
  while (value && !supported.includes(value) && !seen.has(value)) {
    seen.add(value);
    value = Object.prototype.hasOwnProperty.call(fallback, value) ? fallback[value] : undefined;
  }
  if (!value || !supported.includes(value)) value = supported.includes(fallbackDefault) ? fallbackDefault : supported[0];
  return { value, note: !wanted || value === wanted ? null : `${wanted} shown as ${value}` };
}

/**
 * @typedef {object} Acting
 * @property {string | null} behaviour
 * @property {string | null} expression
 * @property {string | null} gesture
 * @property {string[]} notes   one per substitution (empty when everything is shown as wanted)
 */

/**
 * Map wanted acting onto a presenter's capabilities (each part with its own fallback chain).
 * @param {{ behaviour?: string | null, expression?: string | null, gesture?: string | null }} wanted
 * @param {Readonly<PresenterCapabilities>} [capabilities]   default: the branding clips
 * @returns {Acting}
 */
export function actingFor(wanted, capabilities = CLIP_CAPABILITIES) {
  const b = mapVocab(wanted.behaviour, capabilities.behaviours, BEHAVIOUR_FALLBACK, 'idle');
  const x = mapVocab(wanted.expression, capabilities.expressions, EXPRESSION_FALLBACK, 'neutral');
  const g = mapVocab(wanted.gesture, capabilities.gestures, GESTURE_FALLBACK, 'none');
  /** @type {string[]} */
  const notes = [];
  for (const n of [b.note, x.note, g.note]) if (n) notes.push(n);
  return { behaviour: b.value, expression: x.value, gesture: g.value, notes };
}
