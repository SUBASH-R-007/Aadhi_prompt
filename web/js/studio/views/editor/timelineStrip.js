// @ts-check
/**
 * Timeline strip under the editor: a time ruler, then lanes for the scenes (blocks as wide as the scene
 * plays), the narration (one bar per beat; estimated beats hatched), the visuals (side panels from the moment
 * they show, full-screen footage/animations/sketches, ticks where a beat reveals a board item) and the
 * captions, with chapter marks on the ruler and a playhead. Everything comes from the same preview Timeline
 * the embedded player plays (POST /timeline/preview or GET /timeline), so it shows what the video will do.
 * Scenes left out of the video (`hidden`) are thin marks between the blocks.
 *
 * Pressing or dragging on the ruler or a detail lane seeks the player; a scene block selects its scene
 * (dragging a block more than a few pixels moves the scene instead); zoom − / Fit / + is remembered
 * (`timelineZoom`). Keyboard: the ruler is a slider (arrows ±1 s, Shift ±10 s, Home/End) and the scene blocks
 * are one tab stop (arrows move between them, Enter selects). setTime() only moves the playhead: the lanes
 * are rebuilt when a new timeline arrives, never while it plays. Without a timeline (the preview is blocked
 * or failed) the scene lane shows the scene list's estimates and choosing a time selects that scene.
 */

import { h, clear } from '../../../shared/dom.js';
import { button } from '../../components/form.js';
import { loadPref, savePref } from '../../lib/draftStore.js';
import { SCENE_TYPE_LABELS } from '../../lib/screenplayEdit.js';
import { stripRichLite } from '../../lib/richLite.js';
import { clamp, formatDuration } from '../../util.js';
import { estimateSceneSeconds } from './sceneList.js';

/** Ruler tick steps (seconds): the first one at least TICK_PX apart is used. */
export const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
/** Zoom factors over "Fit" (1 = the whole lecture fits the strip's width). */
export const ZOOM_LEVELS = [1, 1.5, 2, 3, 4, 6, 8, 12, 16];
const TICK_PX = 70;
/** Pixels per second while the strip has no measurable width (hidden, tests). */
export const DEFAULT_PPS = 6;
const MAX_PPS = 400;
const DRAG_PX = 5;
const PAD_PX = 8;

const FULLSCREEN_LABELS = /** @type {Record<string, string>} */ ({
  simulation: 'Animation',
  ai_video: 'Footage',
  interactive: 'Interactive sketch',
});
const PANEL_LABELS = /** @type {Record<string, string>} */ ({
  skill_tree: 'Concept map',
  figure: 'Figure',
  image: 'Picture',
  chart: 'Chart',
  graph: 'Graph',
  model_3d: '3D model',
  manim: 'Animation',
  terminal: 'Terminal',
  quiz: 'Quiz teaser',
  gif: 'GIF',
});

/**
 * @typedef {object} StripScene
 * @property {string} id
 * @property {number} n          scene number in the lecture (draft order, 1-based)
 * @property {string} title
 * @property {string} type
 * @property {number} start      absolute seconds
 * @property {number} end
 * @property {boolean} estimated
 */

/**
 * @typedef {object} StripModel
 * @property {number} total
 * @property {{ start: number, end: number } | null} intro
 * @property {StripScene[]} scenes
 * @property {Array<{ sceneId: string, n: number, k: number, start: number, end: number, estimated: boolean }>} beats
 * @property {Array<{ sceneId: string, start: number, end: number, label: string }>} visuals
 * @property {Array<{ sceneId: string, at: number }>} reveals
 * @property {Array<{ start: number, end: number, text: string }>} captions
 * @property {Array<{ start: number, title: string }>} chapters
 * @property {Array<{ id: string, n: number, title: string, at: number }>} hidden   scenes left out of the video
 * @property {boolean} estimated   some timings are estimates (no voice yet)
 * @property {boolean} fallback    built from the scene list (no preview timeline)
 */

/** @param {any} v */
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** Whole seconds shown like the player's own clock ("0:56" for 56.5 s). @param {number} t */
const clock = (t) => formatDuration(Math.floor(Math.max(0, t)));

/**
 * @param {any} scene   screenplay scene
 * @param {number} n
 */
function sceneTitle(scene, n) {
  const raw = scene ? stripRichLite(scene.title || (scene.type === 'quiz_checkpoint' ? scene.question || '' : '')) : '';
  return raw.trim() || (scene && SCENE_TYPE_LABELS[scene.type]) || `Scene ${n}`;
}

/**
 * Scenes left out of the video, each placed where it would have played (before the next scene that plays).
 * @param {any} sp
 * @param {Map<string, number>} startOf   visible scene id -> start
 * @param {number} total
 */
function hiddenMarks(sp, startOf, total) {
  const scenes = (sp && sp.scenes) || [];
  /** @type {StripModel['hidden']} */
  const out = [];
  scenes.forEach((/** @type {any} */ s, /** @type {number} */ i) => {
    if (!s || s.hidden !== true) return;
    let at = total;
    for (let j = i + 1; j < scenes.length; j++) {
      const start = startOf.get(scenes[j].id);
      if (start !== undefined) {
        at = start;
        break;
      }
    }
    out.push({ id: s.id, n: i + 1, title: sceneTitle(s, i + 1), at });
  });
  return out;
}

/**
 * The strip's content from a preview Timeline (and the draft, for scene numbers, titles and hidden scenes).
 * @param {any} timeline
 * @param {any} sp
 * @returns {StripModel}
 */
export function stripModel(timeline, sp) {
  const draftScenes = (sp && sp.scenes) || [];
  const numberOf = new Map(draftScenes.map((/** @type {any} */ s, /** @type {number} */ i) => [s.id, i + 1]));
  const draftOf = new Map(draftScenes.map((/** @type {any} */ s) => [s.id, s]));
  const tlScenes = Array.isArray(timeline && timeline.scenes) ? timeline.scenes : [];
  /** @type {StripModel} */
  const model = { total: Math.max(0, num(timeline && timeline.total_duration)), intro: null, scenes: [], beats: [], visuals: [], reveals: [], captions: [], chapters: [], hidden: [], estimated: !!(timeline && timeline.estimated), fallback: false };
  /** @type {Map<string, number>} */
  const startOf = new Map();
  tlScenes.forEach((/** @type {any} */ ts, /** @type {number} */ i) => {
    const id = String(ts.scene_id || '');
    const start = num(ts.start);
    const end = start + Math.max(0, num(ts.duration));
    const n = numberOf.get(id) || i + 1;
    const draft = draftOf.get(id);
    const beats = Array.isArray(ts.beats) ? ts.beats : [];
    startOf.set(id, start);
    model.scenes.push({ id, n, title: draft ? sceneTitle(draft, n) : stripRichLite(ts.title || '').trim() || SCENE_TYPE_LABELS[ts.type] || `Scene ${n}`, type: String(ts.type || ''), start, end, estimated: beats.some((/** @type {any} */ b) => b && b.estimated) });
    beats.forEach((/** @type {any} */ b, /** @type {number} */ k) => {
      const bs = start + num(b.start);
      const be = start + Math.max(num(b.start), num(b.end), num(b.speech_end));
      model.beats.push({ sceneId: id, n, k: k + 1, start: bs, end: Math.min(be, end), estimated: !!b.estimated });
      if (b.board_item_id) model.reveals.push({ sceneId: id, at: bs });
      if (!Array.isArray(timeline.captions)) {
        for (const c of Array.isArray(b.captions) ? b.captions : []) model.captions.push({ start: start + num(c.start), end: start + num(c.end), text: String(c.text || '') });
      }
    });
    if (FULLSCREEN_LABELS[ts.type]) model.visuals.push({ sceneId: id, start, end, label: FULLSCREEN_LABELS[ts.type] });
    const panel = ts.side_panel && ts.side_panel.panel;
    if (panel && panel.kind) model.visuals.push({ sceneId: id, start: Math.min(end, start + num(ts.side_panel.show_at)), end, label: PANEL_LABELS[panel.kind] || String(panel.kind) });
  });
  if (tlScenes.length && num(tlScenes[0].start) > 0) model.intro = { start: 0, end: num(tlScenes[0].start) };
  if (Array.isArray(timeline && timeline.captions)) {
    for (const c of timeline.captions) model.captions.push({ start: num(c.start), end: num(c.end), text: String(c.text || '') });
  }
  for (const c of Array.isArray(timeline && timeline.chapters) ? timeline.chapters : []) model.chapters.push({ start: num(c.start), title: String(c.title || '') });
  const last = model.scenes[model.scenes.length - 1];
  if (last && model.total < last.end) model.total = last.end;
  model.hidden = hiddenMarks(sp, startOf, model.total);
  return model;
}

/**
 * The scene lane from the scene list's estimates, when there is no preview timeline.
 * @param {any} sp
 * @returns {StripModel}
 */
export function fallbackModel(sp) {
  /** @type {StripModel} */
  const model = { total: 0, intro: null, scenes: [], beats: [], visuals: [], reveals: [], captions: [], chapters: [], hidden: [], estimated: true, fallback: true };
  /** @type {Map<string, number>} */
  const startOf = new Map();
  let t = 0;
  ((sp && sp.scenes) || []).forEach((/** @type {any} */ s, /** @type {number} */ i) => {
    if (!s || s.hidden === true) return;
    const d = Math.max(1, estimateSceneSeconds(s));
    startOf.set(s.id, t);
    model.scenes.push({ id: s.id, n: i + 1, title: sceneTitle(s, i + 1), type: s.type, start: t, end: t + d, estimated: true });
    t += d;
  });
  model.total = t;
  model.hidden = hiddenMarks(sp, startOf, t);
  return model;
}

/**
 * Ruler step for a scale (pixels per second).
 * @param {number} pps
 */
export function tickStep(pps) {
  return TICK_STEPS.find((s) => s * pps >= TICK_PX) || TICK_STEPS[TICK_STEPS.length - 1];
}

/**
 * The scene playing at `t` (the last scene that starts at or before it).
 * @param {StripModel} model
 * @param {number} t
 */
export function sceneAt(model, t) {
  let found = model.scenes[0] || null;
  for (const s of model.scenes) {
    if (s.start <= t + 1e-6) found = s;
    else break;
  }
  return found;
}

/**
 * @param {{ onSeek: (t: number) => void, onSelectScene: (sceneId: string) => void,
 *   onMove?: (sceneId: string, beforeId: string | null) => void, onTogglePlay?: () => void,
 *   onStep?: (delta: number) => void }} opts
 */
export function createTimelineStrip(opts) {
  /** @type {StripModel} */
  let model = fallbackModel(null);
  /** @type {string | null} */
  let selectedId = null;
  /** @type {string | null} */
  let playingId = null;
  let time = 0;
  let playing = false;
  let locked = false;
  let zoom = clamp(Number(loadPref('timelineZoom', 1)) || 1, ZOOM_LEVELS[0], ZOOM_LEVELS[ZOOM_LEVELS.length - 1]);
  let pps = DEFAULT_PPS;
  let destroyed = false;
  /** Set while a press on a scene block became a drag (the click that follows is not a selection). */
  let suppressClick = false;

  const timeLabel = h('span', { class: 'tl-time', 'aria-live': 'off' }, '0:00 / 0:00');
  const prevBtn = button('Previous scene', { kind: 'ghost', small: true, title: 'Previous scene ([)' });
  const playBtn = button('Play', { kind: 'ghost', small: true, icon: 'play', title: 'Play or pause (Space)', ariaPressed: false });
  const nextBtn = button('Next scene', { kind: 'ghost', small: true, title: 'Next scene (])' });
  const zoomOut = button('Zoom out', { kind: 'ghost', small: true, className: 'tl-zoom-btn', title: 'Zoom out' });
  const zoomFit = button('Fit', { kind: 'ghost', small: true, title: 'Fit the whole lecture' });
  const zoomIn = button('Zoom in', { kind: 'ghost', small: true, className: 'tl-zoom-btn', title: 'Zoom in' });
  zoomOut.textContent = '−';
  zoomOut.setAttribute('aria-label', 'Zoom out');
  zoomIn.textContent = '+';
  zoomIn.setAttribute('aria-label', 'Zoom in');
  zoomFit.setAttribute('aria-label', 'Fit the whole lecture in the timeline');
  const note = h('p', { class: 'tl-note muted small', role: 'status' });

  const ruler = h('div', { class: 'tl-lane tl-ruler', role: 'slider', tabindex: '0', 'aria-label': 'Playhead position', 'aria-valuemin': '0', 'aria-valuemax': '0', 'aria-valuenow': '0', 'aria-valuetext': '0:00' });
  const sceneLane = h('div', { class: 'tl-lane tl-scenes', role: 'group', 'aria-label': 'Scenes on the timeline' });
  const beatLane = h('div', { class: 'tl-lane tl-narration', 'aria-hidden': 'true' });
  const visualLane = h('div', { class: 'tl-lane tl-visuals', 'aria-hidden': 'true' });
  const captionLane = h('div', { class: 'tl-lane tl-captions', 'aria-hidden': 'true' });
  const playhead = h('div', { class: 'tl-playhead', 'aria-hidden': 'true' });
  const drop = h('div', { class: 'tl-drop', hidden: true, 'aria-hidden': 'true' });
  const content = h(
    'div',
    { class: 'tl-content' },
    h('div', { class: 'tl-row', dataset: { track: 'ruler' } }, ruler),
    h('div', { class: 'tl-row', dataset: { track: 'scenes' } }, sceneLane),
    h('div', { class: 'tl-row', dataset: { track: 'narration' } }, beatLane),
    h('div', { class: 'tl-row', dataset: { track: 'visuals' } }, visualLane),
    h('div', { class: 'tl-row', dataset: { track: 'captions' } }, captionLane),
    playhead,
    drop,
  );
  const scroll = h('div', { class: 'tl-scroll' }, content);
  const labels = h(
    'div',
    { class: 'tl-labels', 'aria-hidden': 'true' },
    h('span', { class: 'tl-label', dataset: { track: 'ruler' } }, 'Time'),
    h('span', { class: 'tl-label', dataset: { track: 'scenes' } }, 'Scenes'),
    h('span', { class: 'tl-label', dataset: { track: 'narration' } }, 'Narration'),
    h('span', { class: 'tl-label', dataset: { track: 'visuals' } }, 'Visuals'),
    h('span', { class: 'tl-label', dataset: { track: 'captions' } }, 'Captions'),
  );
  const el = h(
    'section',
    { class: 'tl-strip', 'aria-label': 'Timeline' },
    h(
      'div',
      { class: 'tl-bar' },
      prevBtn,
      playBtn,
      nextBtn,
      timeLabel,
      h('span', { class: 'spacer' }),
      h('span', { class: 'tl-zoom', role: 'group', 'aria-label': 'Timeline zoom' }, zoomOut, zoomFit, zoomIn),
    ),
    h('div', { class: 'tl-body' }, labels, scroll),
    note,
  );

  /** @param {number} t */
  const x = (t) => Math.round(t * pps);
  /** @param {number} a @param {number} b */
  const span = (a, b) => ({ left: `${x(a)}px`, width: `${Math.max(2, x(b) - x(a) - 1)}px` });

  function measurePps() {
    const width = scroll.clientWidth || 0;
    const fit = width > 40 && model.total > 0 ? (width - PAD_PX * 2) / model.total : DEFAULT_PPS;
    return clamp(fit * zoom, 0.05, MAX_PPS);
  }

  function renderNote() {
    if (model.fallback) note.textContent = model.scenes.length ? 'Estimated from the scene list: the preview is not available right now.' : '';
    else if (!model.scenes.length) note.textContent = 'No scene plays in the video.';
    else if (model.estimated) note.textContent = 'Some timings are estimated: the voice is not generated yet for edited beats.';
    else note.textContent = '';
    if (locked) note.textContent = `${note.textContent} Moving scenes waits until the running job finishes.`.trim();
  }

  function render() {
    if (destroyed) return;
    const focusedId = document.activeElement && sceneLane.contains(document.activeElement) ? /** @type {HTMLElement} */ (document.activeElement).dataset.sceneId || null : null;
    const scrollLeft = scroll.scrollLeft;
    pps = measurePps();
    const width = x(model.total) + PAD_PX;
    content.style.width = `${width}px`;
    el.classList.toggle('is-fallback', model.fallback);
    for (const lane of [ruler, sceneLane, beatLane, visualLane, captionLane]) clear(lane);

    // ruler: ticks and chapter marks
    const step = tickStep(pps);
    for (let t = 0; t <= model.total + 1e-6; t += step) {
      ruler.appendChild(h('span', { class: 'tl-tick', style: { left: `${x(t)}px` } }, h('span', { class: 'tl-tick-label' }, formatDuration(t))));
    }
    for (const c of model.chapters) ruler.appendChild(h('span', { class: 'tl-chapter', style: { left: `${x(c.start)}px` }, title: `Chapter: ${c.title}` }));
    ruler.setAttribute('aria-valuemax', String(Math.round(model.total)));

    // scenes
    if (model.intro) sceneLane.appendChild(h('div', { class: 'tl-intro', style: span(model.intro.start, model.intro.end), title: 'Intro (logo and title cards)' }, h('span', { class: 'tl-block-title' }, 'Intro')));
    const tabbable = model.scenes.some((s) => s.id === selectedId) ? selectedId : model.scenes[0] ? model.scenes[0].id : null;
    for (const s of model.scenes) {
      const dur = s.end - s.start;
      const block = h(
        'button',
        {
          type: 'button',
          class: ['tl-block', s.id === selectedId ? 'selected' : '', s.id === playingId ? 'is-playing' : '', s.estimated ? 'is-estimated' : ''],
          style: span(s.start, s.end),
          tabindex: s.id === tabbable ? '0' : '-1',
          'aria-current': s.id === selectedId ? 'true' : undefined,
          'aria-label': `Scene ${s.n}: ${s.title}, ${formatDuration(dur)}${s.estimated ? ' (estimated)' : ''}`,
          title: `${s.n}. ${s.title} · ${formatDuration(dur)}${s.estimated ? ' (estimated)' : ''}`,
          dataset: { sceneId: s.id, type: s.type },
        },
        h('span', { class: 'tl-block-title' }, `${s.n}. ${s.title}`),
        h('span', { class: 'tl-block-time' }, formatDuration(dur)),
      );
      block.addEventListener('click', () => {
        if (suppressClick) {
          suppressClick = false;
          return;
        }
        opts.onSelectScene(s.id);
      });
      block.addEventListener('keydown', (ev) => onBlockKey(ev, s.id));
      block.addEventListener('pointerdown', (ev) => startBlockDrag(/** @type {PointerEvent} */ (ev), s.id));
      sceneLane.appendChild(block);
    }
    for (const m of model.hidden) {
      const mark = h('button', { type: 'button', class: 'tl-hidden', tabindex: '-1', style: { left: `${Math.max(0, x(m.at) - 3)}px` }, 'aria-label': `Scene ${m.n} (skipped in the video): ${m.title}`, title: `Scene ${m.n} is skipped in the video: ${m.title}`, dataset: { sceneId: m.id } });
      mark.addEventListener('click', () => opts.onSelectScene(m.id));
      sceneLane.appendChild(mark);
    }

    // narration, visuals, captions (decorative detail; the scene lane and the ruler are the accessible parts)
    for (const b of model.beats) {
      beatLane.appendChild(h('div', { class: ['tl-beat', b.estimated ? 'is-estimated' : ''], style: span(b.start, b.end), title: `Scene ${b.n}, beat ${b.k}: ${formatDuration(b.start)}–${formatDuration(b.end)}${b.estimated ? ' (estimated)' : ''}` }));
    }
    for (const v of model.visuals) visualLane.appendChild(h('div', { class: 'tl-visual', style: span(v.start, v.end), title: v.label }, h('span', {}, v.label)));
    for (const r of model.reveals) visualLane.appendChild(h('span', { class: 'tl-reveal', style: { left: `${x(r.at)}px` }, title: 'A board item appears' }));
    for (const c of model.captions) captionLane.appendChild(h('div', { class: 'tl-cue', style: span(c.start, c.end), title: c.text.slice(0, 120) }));

    renderNote();
    placePlayhead();
    scroll.scrollLeft = scrollLeft;
    if (focusedId) {
      const again = /** @type {HTMLElement | null} */ ([...sceneLane.querySelectorAll('.tl-block')].find((b) => /** @type {HTMLElement} */ (b).dataset.sceneId === focusedId) || null);
      if (again) again.focus({ preventScroll: true });
    }
  }

  function placePlayhead() {
    playhead.hidden = model.fallback || !model.scenes.length;
    playhead.style.left = `${x(time)}px`;
    timeLabel.textContent = model.fallback ? `≈ ${formatDuration(model.total)}` : `${clock(time)} / ${clock(model.total)}`;
    ruler.setAttribute('aria-valuenow', String(Math.floor(time)));
    ruler.setAttribute('aria-valuetext', `${clock(time)} of ${clock(model.total)}`);
  }

  /** Keep the playhead in view while the lecture plays. */
  function follow() {
    const px = x(time);
    const w = scroll.clientWidth || 0;
    if (w <= 0) return;
    if (px < scroll.scrollLeft || px > scroll.scrollLeft + w - 24) scroll.scrollLeft = Math.max(0, px - 40);
  }

  /** @param {number} t */
  function choose(t) {
    const at = clamp(t, 0, model.total);
    time = at;
    placePlayhead(); // in fallback the playhead stays hidden; the slider's value follows the keyboard all the same
    if (model.fallback) {
      const s = sceneAt(model, at);
      if (s) opts.onSelectScene(s.id);
      return;
    }
    opts.onSeek(at);
  }

  /** @param {MouseEvent} ev */
  const timeOfEvent = (ev) => (ev.clientX - content.getBoundingClientRect().left) / (pps || DEFAULT_PPS);

  /** Press (and drag) on the ruler or a detail lane: seek. @param {PointerEvent} ev */
  function startScrub(ev) {
    if (ev.button !== undefined && ev.button !== 0) return;
    ev.preventDefault();
    choose(timeOfEvent(ev));
    if (model.fallback) return;
    const move = (/** @type {Event} */ e) => choose(timeOfEvent(/** @type {MouseEvent} */ (e)));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  }

  /**
   * Press on a scene block: a click selects it; a drag of more than DRAG_PX moves the scene.
   * @param {PointerEvent} ev
   * @param {string} sceneId
   */
  function startBlockDrag(ev, sceneId) {
    if ((ev.button !== undefined && ev.button !== 0) || !opts.onMove || locked) return;
    const startX = ev.clientX;
    let dragging = false;
    /** @type {string | null | undefined} */
    let target;
    const others = model.scenes.filter((s) => s.id !== sceneId);
    const from = model.scenes.findIndex((s) => s.id === sceneId);
    const move = (/** @type {Event} */ e) => {
      const me = /** @type {MouseEvent} */ (e);
      if (!dragging && Math.abs(me.clientX - startX) < DRAG_PX) return;
      dragging = true;
      const t = timeOfEvent(me);
      const pos = others.filter((s) => (s.start + s.end) / 2 < t).length;
      target = pos === from ? undefined : others[pos] ? others[pos].id : null;
      const edge = others[pos] ? others[pos].start : others.length ? others[others.length - 1].end : 0;
      drop.hidden = target === undefined;
      drop.style.left = `${x(edge)}px`;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      drop.hidden = true;
      if (!dragging) return;
      suppressClick = true;
      setTimeout(() => {
        suppressClick = false;
      }, 0);
      if (target !== undefined && opts.onMove) opts.onMove(sceneId, target);
    };
    const cancel = () => {
      dragging = false;
      up();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
  }

  /**
   * Arrow keys move between the scene blocks (one tab stop); Home / End jump.
   * @param {KeyboardEvent} ev
   * @param {string} sceneId
   */
  function onBlockKey(ev, sceneId) {
    const blocks = /** @type {HTMLElement[]} */ ([...sceneLane.querySelectorAll('.tl-block')]);
    const i = blocks.findIndex((b) => b.dataset.sceneId === sceneId);
    let next = -1;
    if (ev.key === 'ArrowRight') next = Math.min(blocks.length - 1, i + 1);
    else if (ev.key === 'ArrowLeft') next = Math.max(0, i - 1);
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = blocks.length - 1;
    if (next < 0 || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    ev.preventDefault();
    for (const b of blocks) b.tabIndex = -1;
    blocks[next].tabIndex = 0;
    blocks[next].focus();
  }

  ruler.addEventListener('pointerdown', (ev) => startScrub(/** @type {PointerEvent} */ (ev)));
  for (const lane of [beatLane, visualLane, captionLane]) lane.addEventListener('pointerdown', (ev) => startScrub(/** @type {PointerEvent} */ (ev)));
  ruler.addEventListener('keydown', (ev) => {
    const stepS = ev.shiftKey ? 10 : 1;
    /** @type {number | null} */
    let t = null;
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowUp') t = time + stepS;
    else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowDown') t = time - stepS;
    else if (ev.key === 'PageUp') t = time + 10;
    else if (ev.key === 'PageDown') t = time - 10;
    else if (ev.key === 'Home') t = 0;
    else if (ev.key === 'End') t = model.total;
    if (t === null) return;
    ev.preventDefault();
    choose(t);
  });
  prevBtn.addEventListener('click', () => opts.onStep && opts.onStep(-1));
  nextBtn.addEventListener('click', () => opts.onStep && opts.onStep(1));
  playBtn.addEventListener('click', () => opts.onTogglePlay && opts.onTogglePlay());
  /** @param {number} z */
  const setZoom = (z) => {
    zoom = clamp(z, ZOOM_LEVELS[0], ZOOM_LEVELS[ZOOM_LEVELS.length - 1]);
    savePref('timelineZoom', zoom);
    render();
    follow();
  };
  zoomOut.addEventListener('click', () => setZoom([...ZOOM_LEVELS].reverse().find((z) => z < zoom - 1e-6) ?? ZOOM_LEVELS[0]));
  zoomIn.addEventListener('click', () => setZoom(ZOOM_LEVELS.find((z) => z > zoom + 1e-6) ?? ZOOM_LEVELS[ZOOM_LEVELS.length - 1]));
  zoomFit.addEventListener('click', () => setZoom(1));

  /** @type {ResizeObserver | null} */
  let resize = null;
  let lastWidth = -1;
  if (typeof ResizeObserver === 'function') {
    resize = new ResizeObserver(() => {
      const w = scroll.clientWidth || 0;
      if (Math.abs(w - lastWidth) < 2) return;
      lastWidth = w;
      render();
    });
    resize.observe(scroll);
  }

  render();

  return {
    el,
    /**
     * Show a timeline (null: the scene list's estimates).
     * @param {any} timeline
     * @param {any} sp
     */
    setTimeline(timeline, sp) {
      model = timeline && Array.isArray(timeline.scenes) ? stripModel(timeline, sp) : fallbackModel(sp);
      time = clamp(time, 0, model.total);
      render();
    },
    /** @param {string | null} id */
    setSelected(id) {
      if (id === selectedId) return;
      selectedId = id;
      const tabbable = model.scenes.some((s) => s.id === id) ? id : null;
      for (const b of sceneLane.querySelectorAll('.tl-block')) {
        const el2 = /** @type {HTMLElement} */ (b);
        const on = el2.dataset.sceneId === id;
        el2.classList.toggle('selected', on);
        if (on) el2.setAttribute('aria-current', 'true');
        else el2.removeAttribute('aria-current');
        if (tabbable && !sceneLane.contains(document.activeElement)) el2.tabIndex = on ? 0 : -1;
      }
    },
    /** @param {string | null} id   the scene the player is playing (null when paused) */
    setPlayingScene(id) {
      playingId = id;
      for (const b of sceneLane.querySelectorAll('.tl-block')) b.classList.toggle('is-playing', /** @type {HTMLElement} */ (b).dataset.sceneId === id);
    },
    /**
     * Move the playhead (called on every player time update: nothing else is redrawn).
     * @param {number} t
     * @param {boolean} isPlaying
     */
    setTime(t, isPlaying) {
      if (model.fallback) return; // estimates: a player left on screen keeps the clock of another timeline
      time = clamp(Number(t) || 0, 0, model.total);
      placePlayhead();
      if (isPlaying) follow();
    },
    /** @param {boolean} on */
    setPlaying(on) {
      if (on === playing) return;
      playing = on;
      playBtn.setAttribute('aria-pressed', String(on));
      const lbl = playBtn.querySelector('.btn-label');
      if (lbl) lbl.textContent = on ? 'Pause' : 'Play';
    },
    /** @param {boolean} on   a job is writing the version: scenes cannot be moved */
    setLocked(on) {
      locked = on;
      el.classList.toggle('is-locked', on);
      renderNote();
    },
    /** @returns {StripModel} */
    model: () => model,
    destroy() {
      destroyed = true;
      if (resize) resize.disconnect();
      clear(el);
    },
  };
}
