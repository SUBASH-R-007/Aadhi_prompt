// @ts-check
/**
 * Inline SVG icons (static, trusted path data) so icons render identically in the render worker,
 * which only has the vendored fonts (no emoji or symbol fallback fonts).
 */

import { svg } from '../shared/dom.js';

/** 24x24 path data (stroke icons unless listed in FILLED). */
const PATHS = /** @type {Record<string, string[]>} */ ({
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  cross: ['M6 6l12 12', 'M18 6L6 18'],
  star: ['M12 3.2l2.7 5.6 6.1.8-4.5 4.2 1.1 6-5.4-3-5.4 3 1.1-6-4.5-4.2 6.1-.8z'],
  info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 11v6', 'M12 7.5v.5'],
  tip: ['M9 18h6', 'M10 21h4', 'M12 3a6 6 0 0 0-3.6 10.8c.6.5 1.1 1.3 1.1 2.2h5c0-.9.5-1.7 1.1-2.2A6 6 0 0 0 12 3z'],
  warning: ['M12 3.5L2.5 20h19z', 'M12 10v5', 'M12 17.5v.5'],
  play: ['M7 4.5v15l13-7.5z'],
  pause: ['M7 4.5h3.5v15H7z', 'M13.5 4.5H17v15h-3.5z'],
  replay: ['M4 12a8 8 0 1 0 2.4-5.7', 'M4 4v5h5'],
  prev: ['M6 5v14', 'M19 5l-10 7 10 7z'],
  next: ['M18 5v14', 'M5 5l10 7-10 7z'],
  volume: ['M4 9.5h4l5-4v13l-5-4H4z', 'M16.5 8.5a5 5 0 0 1 0 7', 'M19 6a8.5 8.5 0 0 1 0 12'],
  mute: ['M4 9.5h4l5-4v13l-5-4H4z', 'M16.5 9.5l5 5', 'M21.5 9.5l-5 5'],
  captions: ['M3.5 5.5h17v13h-17z', 'M10.5 10.2a2.2 2.2 0 1 0 0 3.6', 'M17 10.2a2.2 2.2 0 1 0 0 3.6'],
  list: ['M8 6.5h12', 'M8 12h12', 'M8 17.5h12', 'M4 6.5h.5', 'M4 12h.5', 'M4 17.5h.5'],
  fullscreen: ['M4 9V4h5', 'M15 4h5v5', 'M20 15v5h-5', 'M9 20H4v-5'],
  exitFullscreen: ['M9 4v5H4', 'M20 9h-5V4', 'M15 20v-5h5', 'M4 15h5v5'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  speed: ['M12 20a8 8 0 1 1 8-8', 'M12 12l4-4'],
});

const FILLED = new Set(['star', 'play', 'pause', 'prev', 'next']);

/**
 * Create an icon element.
 * @param {string} name
 * @param {{ class?: string, title?: string }} [opts]
 * @returns {SVGElement}
 */
export function icon(name, opts = {}) {
  const paths = PATHS[name] || PATHS.info;
  const filled = FILLED.has(name);
  const el = svg(
    'svg',
    {
      class: ['ap-icon', `ap-icon--${name}`, opts.class || ''],
      width: '24',
      height: '24',
      'aria-hidden': 'true',
      focusable: 'false',
      fill: filled ? 'currentColor' : 'none',
      stroke: 'currentColor',
      'stroke-width': filled ? '1' : '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
    },
    paths.map((d) => svg('path', { d })),
  );
  // dom.js lower-cases attribute names; SVG's viewBox is case-sensitive, so set it directly.
  el.setAttribute('viewBox', '0 0 24 24');
  return el;
}

/** Names of the available icons. */
export const ICON_NAMES = Object.freeze(Object.keys(PATHS));
