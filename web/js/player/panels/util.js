// @ts-check
/**
 * Small helpers shared by the side-panel modules (pure where possible).
 */

/** @typedef {import('./types.js').PanelCtx} PanelCtx */
/** @typedef {import('./types.js').PanelMode} PanelMode */
/** @typedef {import('./types.js').StageRect} StageRect */

/** Theme colours (mirrors web/css/tokens.css; canvas/WebGL code cannot read CSS variables cheaply). */
export const THEME = Object.freeze({
  gold: '#ffd700',
  purple: '#b026ff',
  purpleDeep: '#6c159e',
  cyan: '#00e5ff',
  green: '#00ff88',
  red: '#ff5555',
  orange: '#ff9f43',
  text: '#ffffff',
  textDim: 'rgba(255, 255, 255, 0.72)',
  textFaint: 'rgba(255, 255, 255, 0.45)',
  grid: 'rgba(255, 255, 255, 0.10)',
  axis: 'rgba(255, 255, 255, 0.55)',
  surfaceSolid: '#241036',
  fontSans: "Inter, 'Noto Sans Tamil', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Kannada', 'Noto Sans Malayalam', system-ui, sans-serif",
  fontTitle: "Outfit, Inter, sans-serif",
  fontMono: "'JetBrains Mono', ui-monospace, monospace",
});

/** Series palette (charts, graph functions). */
export const PALETTE = Object.freeze([THEME.gold, THEME.cyan, THEME.purple, THEME.green, THEME.red, THEME.orange]);

/**
 * @param {number} v
 * @param {number} lo
 * @param {number} hi
 */
export function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

/** Round to milliseconds (state times must compare exactly between player and panels). @param {number} v */
export function round3(v) {
  return Math.round(v * 1000) / 1000;
}

/** @param {unknown} v @param {number} fallback */
export function finiteOr(v, fallback) {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

/**
 * `#rrggbb` + alpha -> `rgba(...)`. Invalid input falls back to the gold accent.
 * @param {string} hex
 * @param {number} a
 */
export function withAlpha(hex, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || ''));
  const n = parseInt(m ? m[1] : 'ffd700', 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${clamp(a, 0, 1)})`;
}

/**
 * Rich-lite markup -> plain text (for SVG labels, chart labels, canvas text).
 * Keeps the content of `**b**`, `*i*`, `` `c` ``, `[[k]]` and `$tex$` (TeX shown as source).
 * @param {unknown} text
 */
export function stripRich(text) {
  return String(text ?? '')
    .replace(/\\([$*])/g, '\u0000$1')
    .replace(/\[\[([^\]]*)\]\]/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\$([^$]*)\$/g, '$1')
    .replace(/\u0000([$*])/g, '$1')
    .trim();
}

/**
 * Normalise ctx.mode.
 * @param {unknown} mode
 * @returns {PanelMode}
 */
export function normalizeMode(mode) {
  return mode === 'render' || mode === 'preview' ? mode : 'live';
}

/** True when the user asked for reduced motion (never true in render mode callers' eyes). */
export function prefersReducedMotion() {
  try {
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/**
 * Scene duration for the panel's scene (falls back to a generous window when unknown), without the hold the
 * teacher's minimum duration added at its end (TimedScene.hold_seconds; the player's schedule.js contentEnd): the
 * terminal lines and the quiz teaser are timed as if there were no hold.
 * @param {PanelCtx} ctx
 * @param {number} showAt
 */
export function sceneDuration(ctx, showAt) {
  const scenes = ctx && ctx.timeline && Array.isArray(ctx.timeline.scenes) ? ctx.timeline.scenes : null;
  const idx = ctx && typeof ctx.sceneIndex === 'number' ? ctx.sceneIndex : -1;
  const scene = scenes && idx >= 0 ? scenes[idx] : null;
  const duration = finiteOr(scene && scene.duration, showAt + 10);
  const hold = finiteOr(scene && scene.hold_seconds, 0);
  return hold > 0 ? Math.max(showAt, round3(duration - hold)) : duration;
}

/**
 * Find the logical stage element (1920x1080 before the CSS transform).
 * @param {Element} el
 * @param {PanelCtx} ctx
 * @returns {Element | null}
 */
export function findStage(el, ctx) {
  if (ctx && ctx.stageEl) return ctx.stageEl;
  return el.closest('.ap-stage, [data-aadhi-stage], [data-stage], .stage, .player-stage');
}

/**
 * Bounding box of `el` in logical stage pixels (undoes the stage's CSS scale).
 * @param {Element} el
 * @param {PanelCtx} ctx
 * @param {DOMRect | { left: number, top: number, width: number, height: number }} [inner] optional client rect to convert instead of el's
 * @returns {StageRect}
 */
export function rectInStage(el, ctx, inner) {
  const r = inner || el.getBoundingClientRect();
  const stage = findStage(el, ctx);
  let ox = 0;
  let oy = 0;
  let scale = 1;
  if (stage) {
    const s = stage.getBoundingClientRect();
    const logicalW = finiteOr(ctx && ctx.timeline && ctx.timeline.width, 1920);
    ox = s.left;
    oy = s.top;
    scale = s.width > 0 ? s.width / logicalW : 1;
  }
  return {
    x: Math.round((r.left - ox) / scale),
    y: Math.round((r.top - oy) / scale),
    width: Math.round(r.width / scale),
    height: Math.round(r.height / scale),
  };
}

/**
 * Largest rect with aspect `aw:ah` centred inside `box` (object-fit: contain). With an unknown
 * aspect the whole box is returned. Always a plain object with own fields: `box` may be a DOMRect,
 * whose fields are prototype getters (an object spread of it is `{}`).
 * @param {{ left: number, top: number, width: number, height: number }} box
 * @param {number} aw
 * @param {number} ah
 * @returns {{ left: number, top: number, width: number, height: number }}
 */
export function containRect(box, aw, ah) {
  if (!(aw > 0 && ah > 0) || !(box.width > 0 && box.height > 0)) {
    return { left: box.left, top: box.top, width: box.width, height: box.height };
  }
  const s = Math.min(box.width / aw, box.height / ah);
  const width = aw * s;
  const height = ah * s;
  return { left: box.left + (box.width - width) / 2, top: box.top + (box.height - height) / 2, width, height };
}

/**
 * A placeholder message inside a panel body (textContent only).
 * @param {HTMLElement} body
 * @param {string} message
 * @param {string} [variant]
 */
export function showNotice(body, message, variant = 'info') {
  const doc = body.ownerDocument;
  const el = doc.createElement('div');
  el.className = `ap-notice ap-notice--${variant}`;
  el.textContent = message;
  body.appendChild(el);
  return el;
}

/** @returns {Promise<void>} */
export function nextFrame() {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve());
    else setTimeout(resolve, 16);
  });
}

/**
 * Resolve after `ms` or when `p` settles, whichever is first (never rejects).
 * @param {Promise<unknown>} p
 * @param {number} ms
 * @returns {Promise<void>}
 */
export function settleWithin(p, ms) {
  return new Promise((resolve) => {
    const id = setTimeout(resolve, ms);
    p.then(
      () => {
        clearTimeout(id);
        resolve();
      },
      () => {
        clearTimeout(id);
        resolve();
      },
    );
  });
}
