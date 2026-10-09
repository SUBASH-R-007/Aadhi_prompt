// @ts-check
/**
 * Synchronisation of a timed scene in plain words, for the Studio (editor preview / review): when
 * each beat starts and the word-anchored moments (TimedScene.sync_cues, aadhi/compose/sync.py),
 * e.g. "1.3 s: “Force” appears in the formula legend, when the narration says “force”".
 * Read-only and pure (no DOM): the words come from the scene itself (board text, legend meanings,
 * the spoken anchor words), never from anything that is not in the lecture.
 */

import { plainText } from './richtext.js';

/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').BoardItem} BoardItem */

/** How many moments the summary lists as key moments. */
export const MAX_KEY_MOMENTS = 8;

/** Plain names of the side visuals (focus moments). */
const PANEL_NOUNS = /** @type {Record<string, string>} */ ({
  figure: 'figure',
  image: 'picture',
  chart: 'chart',
  graph: 'graph',
  model_3d: '3D model',
  manim: 'animation',
  gif: 'GIF',
});

/** Kind order when two moments share a time (what a viewer notices first). */
const KIND_ORDER = /** @type {Record<string, number>} */ ({ focus: 0, output: 1, var: 2, emphasis: 3 });

/**
 * @typedef {object} SyncMoment
 * @property {number} at          scene-relative seconds
 * @property {string} when        e.g. "1.3 s"
 * @property {string} what        what happens, in words
 * @property {string} anchor      what places it, in words
 * @property {'var' | 'emphasis' | 'output' | 'focus'} kind
 * @property {string | null} beatId
 */

/**
 * @typedef {object} BeatTiming
 * @property {string} beatId
 * @property {number} start
 * @property {string} when
 * @property {boolean} estimated   timed from an estimate (no narration audio yet)
 */

/**
 * @typedef {object} SyncSummary
 * @property {'narration' | 'estimate'} source
 * @property {string} timing              in words
 * @property {BeatTiming[]} beats
 * @property {SyncMoment[]} moments       every moment, in time order
 * @property {SyncMoment[]} keyMoments    at most MAX_KEY_MOMENTS, in time order
 */

/** @param {number} t */
export function formatSeconds(t) {
  return `${(Math.round(t * 10) / 10).toFixed(1)} s`;
}

/**
 * Short quoted label of a text (rich-lite stripped, at most `max` characters).
 * @param {string | null | undefined} text
 * @param {number} [max]
 */
function quote(text, max = 48) {
  const s = plainText(text).replace(/\s+/g, ' ').trim();
  if (!s) return '';
  return `“${s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s}”`;
}

/**
 * @param {BoardItem | undefined} item
 * @param {string | null | undefined} part
 */
function emphasisWhat(item, part) {
  if (!item) return 'a board item is highlighted';
  const column = /^column:(\d+)$/.exec(part || '');
  if (column) {
    const header = quote((item.headers || [])[Number(column[1])]);
    return header ? `the ${header} column is highlighted` : 'a table column is highlighted';
  }
  if (part === 'term' && item.term) return `the term ${quote(item.term)} is highlighted`;
  const label = quote(item.kind === 'figure' ? item.caption : item.text || item.term);
  return label ? `${label} is highlighted` : 'a board item is highlighted';
}

/**
 * @param {BoardItem | undefined} item
 * @param {string | null | undefined} part
 */
function varWhat(item, part) {
  const m = /^var:(\d+)$/.exec(part || '');
  const v = item && m ? (item.variables || [])[Number(m[1])] : undefined;
  return v && v.meaning ? `${quote(v.meaning)} appears in the formula legend` : 'a formula legend row appears';
}

/**
 * The scene's synchronisation in words (null for a missing scene).
 * @param {TimedScene | null | undefined} scene
 * @param {{ maxMoments?: number }} [opts]
 * @returns {SyncSummary | null}
 */
export function syncSummary(scene, opts = {}) {
  if (!scene || typeof scene !== 'object') return null;
  const max = Math.max(0, Math.floor(opts.maxMoments ?? MAX_KEY_MOMENTS));
  const beats = (Array.isArray(scene.beats) ? scene.beats : []).map((b) => ({
    beatId: String(b.beat_id),
    start: Number(b.start) || 0,
    when: formatSeconds(Number(b.start) || 0),
    estimated: !!b.estimated,
  }));
  const estimated = beats.some((b) => b.estimated);
  /** @type {Map<string, BoardItem>} */
  const items = new Map((scene.board || []).map((i) => [i.id, i]));
  const panelKind = scene.side_panel && scene.side_panel.panel ? scene.side_panel.panel.kind : '';
  /** @type {(SyncMoment & { order: number })[]} */
  const moments = [];
  (Array.isArray(scene.sync_cues) ? scene.sync_cues : []).forEach((c, order) => {
    const at = c ? Number(c.start) : NaN;
    if (!Number.isFinite(at) || at < 0) return;
    const item = c.item_id ? items.get(c.item_id) : undefined;
    /** @type {string | null} */
    let what = null;
    if (c.kind === 'var') what = varWhat(item, c.part);
    else if (c.kind === 'emphasis') what = emphasisWhat(item, c.part);
    else if (c.kind === 'output') what = 'the program output starts to appear';
    else if (c.kind === 'focus') what = `the ${PANEL_NOUNS[panelKind] || 'side panel'} is highlighted`;
    if (!what) return;
    const words = String(c.words || '').replace(/\s+/g, ' ').trim();
    moments.push({
      at,
      when: formatSeconds(at),
      what,
      anchor: words ? `when the narration says “${words}”` : 'when its beat starts',
      kind: c.kind,
      beatId: c.beat_id || null,
      order,
    });
  });
  moments.sort((a, b) => a.at - b.at || (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) || a.order - b.order);
  const strip = (/** @type {SyncMoment & { order: number }} */ m) => ({
    at: m.at,
    when: m.when,
    what: m.what,
    anchor: m.anchor,
    kind: m.kind,
    beatId: m.beatId,
  });
  const all = moments.map(strip);
  return {
    source: estimated ? 'estimate' : 'narration',
    timing: estimated ? 'estimated (no narration audio yet): final times follow the voice' : 'follows the narration audio',
    beats,
    moments: all,
    keyMoments: all.slice(0, max),
  };
}
