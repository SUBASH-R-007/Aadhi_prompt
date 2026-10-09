// @ts-check
/**
 * Stage zones per mascot position, in stage pixels (fixed 1920x1080 logical stage).
 *
 * Measured from the branding clips (video_template/*.mp4, 1280x720 -> stage; saturated mascot
 * pixels over every frame sampled at 4 fps; percentages of the stage width):
 *   left    body 10-31%, gesturing arms 4.4-36.9% (x <= 708)
 *   right   body 70-96%, gesturing arms 67.2-98.1% (x >= 1290)
 *   center  body 41-60%, gesturing arms 36.9-65.6% (x 708..1260)
 *   popup   close-up head and shoulders 28.4-75% (horn tips reach x 545; face 42-62%), one hand
 *           gesture reaches ~89%; the whiteboard behind leaves a free strip on the left only
 *   hidden  empty room (no mascot)
 * left/right/center/hidden: text and media zones keep clear of the mascot.
 * popup is the CONTENT-FIRST layout (v1: "popup for content-heavy slides"; it is also the
 * canonical default of simulation/ai_video/interactive scenes): the close-up cannot leave room
 * for anything large, so the board/media take the stage [40, 1440) in front of it (like v1's
 * popup board at 25-100%), the side panel the strip on the right, and the close-up stays the
 * backdrop. A 16:9 animation then fits at 1266x712 instead of a thumbnail.
 * The bottom band (y >= 900) is reserved for captions, so the layout is identical with captions
 * on, off, or burned into an MP4.
 *
 * Pure module (no DOM access except applyZones).
 */

export const STAGE_WIDTH = 1920;
export const STAGE_HEIGHT = 1080;

const TOP = 56;
const CONTENT_BOTTOM = 880;
const SIDE_TOP = 110;
const SIDE_BOTTOM = 850;
const TITLE_BAND = 96;
const TITLE_GAP = 16;

/**
 * Horizontal extents [x0, x1) of each zone. `wide` = the board when there is no side panel.
 * @type {Record<string, {board: [number, number], side: [number, number], wide: [number, number], origin: string}>}
 */
const POSITIONS = {
  left: { board: [720, 1424], side: [1452, 1880], wide: [720, 1860], origin: '18% 88%' },
  right: { board: [500, 1250], side: [40, 472], wide: [60, 1250], origin: '83% 88%' },
  center: { board: [40, 668], side: [1296, 1880], wide: [40, 668], origin: '50% 88%' },
  popup: { board: [40, 1440], side: [1460, 1880], wide: [40, 1440], origin: '50% 100%' },
  hidden: { board: [80, 1360], side: [1400, 1860], wide: [160, 1760], origin: '50% 50%' },
};

/**
 * @typedef {object} Rect
 * @property {number} x
 * @property {number} y
 * @property {number} w
 * @property {number} h
 */

/**
 * @typedef {object} Zones
 * @property {string} position      normalised position key (left|right|center|popup|hidden)
 * @property {Rect} board           board card region
 * @property {Rect | null} side     side panel region (null without a side panel)
 * @property {Rect} title           title band above fullscreen media
 * @property {Rect} media           fullscreen media region (below the title band)
 * @property {Rect} captions        caption band
 * @property {string} mascotOrigin  transform-origin for the speech-reactive mascot motion
 */

/**
 * Normalise a Layout.mascot_position to a zone key.
 * @param {string | null | undefined} position
 * @returns {string}
 */
export function positionKey(position) {
  if (position === 'popup_bottom_left' || position === 'popup_bottom_right') return 'popup';
  return position && Object.prototype.hasOwnProperty.call(POSITIONS, position) ? position : 'left';
}

/**
 * @param {[number, number]} span
 * @param {number} y0
 * @param {number} y1
 * @returns {Rect}
 */
function rect(span, y0, y1) {
  return { x: span[0], y: y0, w: span[1] - span[0], h: y1 - y0 };
}

/**
 * Zones for a scene layout.
 * @param {string | null | undefined} position   Layout.mascot_position
 * @param {{ sidePanel?: boolean }} [opts]      sidePanel: the scene shows a side panel
 * @returns {Zones}
 */
export function computeZones(position, opts = {}) {
  const key = positionKey(position);
  const p = POSITIONS[key];
  const hasSide = !!opts.sidePanel;
  const boardSpan = hasSide ? p.board : p.wide;
  return {
    position: key,
    board: rect(boardSpan, TOP, CONTENT_BOTTOM),
    side: hasSide ? rect(p.side, SIDE_TOP, SIDE_BOTTOM) : null,
    title: rect(boardSpan, TOP, TOP + TITLE_BAND),
    media: rect(boardSpan, TOP + TITLE_BAND + TITLE_GAP, CONTENT_BOTTOM),
    captions: { x: 260, y: 900, w: 1400, h: 140 },
    mascotOrigin: p.origin,
  };
}

/**
 * Largest rect with the given aspect ratio (width / height) inside `zone`, horizontally centred and
 * top-aligned (so a title band above the zone sits right on top of it). Whole stage pixels.
 * A missing/invalid aspect returns the zone itself.
 * @param {Rect} zone
 * @param {number | null | undefined} aspect
 * @returns {Rect}
 */
export function fitAspect(zone, aspect) {
  if (!(typeof aspect === 'number' && Number.isFinite(aspect) && aspect > 0)) return { ...zone };
  let w = zone.w;
  let h = w / aspect;
  if (h > zone.h) {
    h = zone.h;
    w = h * aspect;
  }
  const rw = Math.round(w);
  return { x: zone.x + Math.round((zone.w - rw) / 2), y: zone.y, w: rw, h: Math.round(h) };
}

/**
 * Aspect ratio of a MediaRef from its width/height, or null when unknown.
 * @param {{ width?: number | null, height?: number | null } | null | undefined} media
 * @returns {number | null}
 */
export function mediaAspect(media) {
  const w = media && media.width;
  const h = media && media.height;
  return typeof w === 'number' && typeof h === 'number' && w > 0 && h > 0 ? w / h : null;
}

/**
 * Whether two rects intersect (used by tests to keep text clear of the mascot).
 * @param {Rect} a
 * @param {Rect} b
 */
export function intersects(a, b) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Write zones as CSS custom properties (--board-x, --board-y, --board-w, --board-h, --side-*,
 * --title-*, --media-*) on an element.
 * @param {HTMLElement} el
 * @param {Zones} zones
 */
export function applyZones(el, zones) {
  /** @type {[string, Rect | null][]} */
  const entries = [
    ['board', zones.board],
    ['side', zones.side],
    ['title', zones.title],
    ['media', zones.media],
  ];
  for (const [name, r] of entries) {
    if (!r) continue;
    el.style.setProperty(`--${name}-x`, `${r.x}px`);
    el.style.setProperty(`--${name}-y`, `${r.y}px`);
    el.style.setProperty(`--${name}-w`, `${r.w}px`);
    el.style.setProperty(`--${name}-h`, `${r.h}px`);
  }
  el.dataset.position = zones.position;
}
