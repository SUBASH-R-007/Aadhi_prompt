// @ts-check
/**
 * Inline SVG icon set (static path data only; 24x24 viewBox, stroke icons).
 * Icons are decorative (`aria-hidden`); every control that uses one also has a text label.
 */

import { svg } from '../../shared/dom.js';

/** @type {Record<string, string[]>} */
const PATHS = {
  plus: ['M12 5v14', 'M5 12h14'],
  trash: ['M4 7h16', 'M10 11v6', 'M14 11v6', 'M6 7l1 13h10l1-13', 'M9 7V4h6v3'],
  copy: ['M9 9h11v11H9z', 'M5 15H4V4h11v1'],
  up: ['M12 19V5', 'M6 11l6-6 6 6'],
  down: ['M12 5v14', 'M6 13l6 6 6-6'],
  grip: ['M9 6h.01', 'M15 6h.01', 'M9 12h.01', 'M15 12h.01', 'M9 18h.01', 'M15 18h.01'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  check: ['M5 12l5 5L20 7'],
  warning: ['M12 3l10 18H2z', 'M12 10v4', 'M12 17h.01'],
  error: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M9 9l6 6', 'M15 9l-6 6'],
  info: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M12 11v6', 'M12 7h.01'],
  play: ['M7 5l12 7-12 7z'],
  eye: ['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z', 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z'],
  download: ['M12 4v12', 'M7 11l5 5 5-5', 'M5 20h14'],
  upload: ['M12 20V8', 'M7 13l5-5 5 5', 'M5 4h14'],
  save: ['M5 4h11l3 3v13H5z', 'M8 4v5h7V4', 'M8 20v-6h8v6'],
  undo: ['M9 14L4 9l5-5', 'M4 9h11a5 5 0 0 1 0 10h-3'],
  redo: ['M15 14l5-5-5-5', 'M20 9H9a5 5 0 0 0 0 10h3'],
  build: ['M14 7l3-3 3 3-3 3z', 'M4 20l10-10', 'M7 4l2 2', 'M4 7l2 2'],
  film: ['M4 4h16v16H4z', 'M8 4v16', 'M16 4v16', 'M4 9h4', 'M4 15h4', 'M16 9h4', 'M16 15h4'],
  board: ['M3 5h18v12H3z', 'M8 21l4-4 4 4', 'M7 9h6', 'M7 13h10'],
  quiz: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14', 'M12 17h.01'],
  chapter: ['M4 5h7a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H4z', 'M20 5h-4a2 2 0 0 0-2 2'],
  sim: ['M3 12h4l3-8 4 16 3-8h4'],
  video: ['M3 6h12v12H3z', 'M15 10l6-4v12l-6-4'],
  code: ['M8 7l-5 5 5 5', 'M16 7l5 5-5 5'],
  title: ['M5 5h14', 'M12 5v14'],
  star: ['M12 3l2.8 5.8 6.2.9-4.5 4.4 1 6.2L12 17.4 6.5 20.3l1-6.2L3 9.7l6.2-.9z'],
  share: ['M18 8a3 3 0 1 0 0-6a3 3 0 1 0 0 6z', 'M6 15a3 3 0 1 0 0-6a3 3 0 1 0 0 6z', 'M18 22a3 3 0 1 0 0-6a3 3 0 1 0 0 6z', 'M8.6 13.5l6.8 4', 'M15.4 6.5l-6.8 4'],
  chart: ['M4 20V10', 'M10 20V4', 'M16 20v-7', 'M3 20h18'],
  user: ['M12 12a4 4 0 1 0 0-8a4 4 0 1 0 0 8z', 'M4 21a8 8 0 0 1 16 0'],
  logout: ['M15 4h4v16h-4', 'M10 16l-4-4 4-4', 'M6 12h11'],
  search: ['M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z', 'M21 21l-5-5'],
  refresh: ['M20 11a8 8 0 0 0-14-5l-2 2', 'M4 4v4h4', 'M4 13a8 8 0 0 0 14 5l2-2', 'M20 20v-4h-4'],
  link: ['M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1', 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'],
  globe: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M3 12h18', 'M12 3a14 14 0 0 1 0 18', 'M12 3a14 14 0 0 0 0 18'],
  wand: ['M4 20L16 8', 'M15 4v2', 'M20 9h-2', 'M18 4l-1.5 1.5'],
  file: ['M6 3h8l4 4v14H6z', 'M14 3v4h4'],
  more: ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'],
  stop: ['M6 6h12v12H6z'],
  retry: ['M4 12a8 8 0 1 0 2.3-5.7', 'M4 4v4h4'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  key: ['M8 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z', 'M12 12h9', 'M17 12v3', 'M20 12v2'],
};

/**
 * @param {string} name
 * @param {{ size?: number, className?: string }} [opts]
 * @returns {SVGElement}
 */
export function icon(name, opts = {}) {
  const size = opts.size || 18;
  const paths = PATHS[name] || PATHS.info;
  return withViewBox(svg(
    'svg',
    {
      class: ['icon', opts.className || ''],
      width: String(size),
      height: String(size),
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'aria-hidden': 'true',
      focusable: 'false',
    },
    paths.map((d) => svg('path', { d })),
  ), '0 0 24 24');
}

/**
 * Set the (case-sensitive) SVG viewBox directly: shared/dom.js lower-cases attribute names,
 * and a lower-case `viewbox` is ignored by browsers, which clips the drawing.
 * @param {SVGElement} el
 * @param {string} box
 * @returns {SVGElement}
 */
export function withViewBox(el, box) {
  el.setAttribute('viewBox', box);
  return el;
}

/** Icon name per scene type (scene list). @type {Record<string, string>} */
export const SCENE_TYPE_ICONS = {
  title: 'title',
  content: 'board',
  example: 'wand',
  summary: 'board',
  key_takeaway: 'star',
  recap: 'refresh',
  chapter_card: 'chapter',
  simulation: 'sim',
  ai_video: 'video',
  interactive: 'code',
  quiz_checkpoint: 'quiz',
};

/**
 * The Aadhi mark (gold monogram in a hexagon) for the top bar.
 * @param {number} [size]
 */
export function brandMark(size = 36) {
  return withViewBox(svg(
    'svg',
    { class: 'st-brand-mark', width: String(size), height: String(size), 'aria-hidden': 'true', focusable: 'false' },
    svg('path', { d: 'M24 3l18 10.5v21L24 45 6 34.5v-21z', fill: 'rgba(108,21,158,0.85)', stroke: '#ffd700', 'stroke-width': '2' }),
    svg('path', { d: 'M16 34l8-20 8 20', fill: 'none', stroke: '#ffd700', 'stroke-width': '3', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }),
    svg('path', { d: 'M19.5 27h9', fill: 'none', stroke: '#ffd700', 'stroke-width': '3', 'stroke-linecap': 'round' }),
  ), '0 0 48 48');
}
