// @ts-check
/**
 * Playback controls for the watch/preview page: play/pause, previous/next scene, volume + mute,
 * time, a scrubber with chapter markers, scene ticks and a hover preview, captions on/off and size,
 * speed 0.75-1.5x, a scene list drawer and fullscreen. Keyboard: space/k play-pause, arrows seek
 * (left/right) and volume (up/down), j/l -/+10 s, shift+arrows or p/n previous/next scene,
 * c captions, m mute, f fullscreen, Esc closes the drawer. Controls auto-hide while playing.
 * Everything is built with shared/dom.js (text only).
 */

import { Disposer, h } from '../shared/dom.js';
import { clamp, formatDurationLong, formatRate, formatTime } from '../shared/format.js';
import { chapterIndexAt, sceneIndexAt } from './schedule.js';
import { icon } from './icons.js';

/** @typedef {import('./player.js').Player} Player */
/** @typedef {import('../shared/types.js').Timeline} Timeline */

export const SPEEDS = Object.freeze([0.75, 1, 1.25, 1.5]);
export const SEEK_STEP = 5;
export const SEEK_JUMP = 10;
/** Caption box offset from the stage bottom (stage px, player.css .ap-captions bottom). */
const CAPTION_BOTTOM = 40;
/** Gap kept between lifted captions and the control bar (stage px). */
const CAPTION_GAP = 14;

/**
 * Stage pixels the captions must rise so the control bar (starting at client y `barTop`) does not
 * cover them; 0 when the bar is hidden or does not reach the caption box.
 * @param {number} stageBottom   client y of the stage's bottom edge
 * @param {number} barTop        client y of the top of the visible control bar
 * @param {number} scale         stage scale (CSS px per stage px)
 * @returns {number}
 */
export function captionLift(stageBottom, barTop, scale) {
  if (!(scale > 0) || !Number.isFinite(stageBottom) || !Number.isFinite(barTop)) return 0;
  const covered = (stageBottom - barTop) / scale;
  return Math.max(0, Math.round(covered - CAPTION_BOTTOM + CAPTION_GAP));
}
const CAPTION_SIZES = /** @type {const} */ (['s', 'm', 'l']);
const CAPTION_SIZE_LABEL = { s: 'Small', m: 'Medium', l: 'Large' };

const TYPE_LABELS = /** @type {Record<string, string>} */ ({
  title: 'Introduction',
  content: 'Lesson',
  example: 'Worked example',
  summary: 'Summary',
  key_takeaway: 'Key takeaways',
  recap: 'Recap',
  chapter_card: 'Chapter',
  simulation: 'Animation',
  ai_video: 'Video',
  interactive: 'Interactive',
  quiz_checkpoint: 'Quiz',
});

/**
 * Display title of a scene (title, else a label for its type).
 * @param {import('../shared/types.js').TimedScene} scene
 */
export function sceneLabel(scene) {
  return scene.title || TYPE_LABELS[scene.type] || 'Scene';
}

/**
 * Whether a keyboard event should be left to the focused control.
 * @param {KeyboardEvent} ev
 */
function isEditable(ev) {
  const t = /** @type {HTMLElement | null} */ (ev.target);
  if (!t || !t.tagName) return false;
  const tag = t.tagName.toLowerCase();
  return tag === 'input' || tag === 'select' || tag === 'textarea' || t.isContentEditable;
}

/**
 * @typedef {object} ControlsOptions
 * @property {EventTarget} [keyboardTarget]   where shortcuts are listened for (default document)
 * @property {number} [idleMs]                auto-hide delay while playing
 * @property {(prefs: {volume: number, muted: boolean, rate: number, captions: boolean, captionSize: string}) => void} [onPrefs]
 */

export class Controls {
  /**
   * @param {HTMLElement} host   the player container (goes fullscreen; receives the idle class)
   * @param {Player} player
   * @param {ControlsOptions} [opts]
   */
  constructor(host, player, opts = {}) {
    this.host = host;
    this.player = player;
    this.opts = opts;
    this.disposer = new Disposer();
    const timeline = /** @type {Timeline} */ (player.timeline);
    this.timeline = timeline;
    this.duration = player.duration;
    this.dragging = false;
    /** @type {ReturnType<typeof setTimeout> | null} */
    this.idleTimer = null;
    this.captionSize = /** @type {'s' | 'm' | 'l'} */ (player.captions ? player.captions.size : 'm');

    // --- buttons -------------------------------------------------------------------------
    /** @type {HTMLButtonElement} */
    this.playBtn = this.button('play', 'Play (k)', () => player.toggle());
    /** @type {HTMLButtonElement} */
    this.prevBtn = this.button('prev', 'Previous scene (p)', () => this.stepScene(-1));
    /** @type {HTMLButtonElement} */
    this.nextBtn = this.button('next', 'Next scene (n)', () => this.stepScene(1));
    /** @type {HTMLButtonElement} */
    this.muteBtn = this.button('volume', 'Mute (m)', () => player.setMuted(!player.muted));
    /** @type {HTMLInputElement} */
    this.volume = h('input', {
      class: 'ap-volume',
      type: 'range',
      min: '0',
      max: '1',
      step: '0.05',
      value: String(player.volume),
      'aria-label': 'Volume',
      onInput: () => player.setVolume(Number(this.volume.value)),
    });
    /** @type {HTMLSpanElement} */
    this.time = h('span', { class: 'ap-time', 'aria-live': 'off' });
    /** @type {HTMLSpanElement} */
    this.sceneTitle = h('span', { class: 'ap-now', 'aria-live': 'polite' });
    /** @type {HTMLButtonElement} */
    this.ccBtn = this.button('captions', 'Captions (c)', () => player.setCaptions(!player.captionsOn));
    /** @type {HTMLButtonElement} */
    this.sizeBtn = h('button', { class: 'ap-btn ap-btn--text', type: 'button', onClick: () => this.cycleCaptionSize() });
    /** @type {HTMLSelectElement} */
    this.speed = h(
      'select',
      { class: 'ap-speed', 'aria-label': 'Playback speed', onChange: () => player.setRate(Number(this.speed.value)) },
      SPEEDS.map((s) => h('option', { value: String(s), text: formatRate(s) })),
    );
    this.speed.value = String(SPEEDS.includes(player.rate) ? player.rate : 1);
    /** @type {HTMLButtonElement} */
    this.listBtn = this.button('list', 'Scenes', () => this.toggleDrawer());
    this.listBtn.setAttribute('aria-expanded', 'false');
    /** @type {HTMLButtonElement} */
    this.fsBtn = this.button('fullscreen', 'Fullscreen (f)', () => this.toggleFullscreen());

    // --- scrubber ------------------------------------------------------------------------
    /** @type {HTMLDivElement} */
    this.fill = h('div', { class: 'ap-scrub-fill' });
    /** @type {HTMLDivElement} */
    this.thumb = h('div', { class: 'ap-scrub-thumb' });
    /** @type {HTMLDivElement} */
    this.hover = h('div', { class: 'ap-scrub-hover' });
    /** @type {HTMLDivElement} */
    this.tooltip = h('div', { class: 'ap-scrub-tooltip', 'aria-hidden': 'true' });
    const ticks = timeline.scenes.map((s) => h('div', { class: 'ap-scrub-tick', style: { left: this.pct(s.start) } }));
    const chapters = (timeline.chapters || []).map((c) =>
      h('div', { class: 'ap-scrub-chapter', style: { left: this.pct(c.start) }, title: c.title }),
    );
    /** @type {HTMLDivElement} */
    this.track = h('div', { class: 'ap-scrub-track' }, ticks, this.hover, this.fill, chapters, this.thumb);
    /** @type {HTMLDivElement} */
    this.scrubber = h(
      'div',
      {
        class: 'ap-scrubber',
        role: 'slider',
        tabindex: '0',
        'aria-label': 'Seek',
        'aria-valuemin': '0',
        'aria-valuemax': String(Math.round(this.duration)),
        'aria-valuenow': '0',
      },
      this.track,
      this.tooltip,
    );

    // --- drawer --------------------------------------------------------------------------
    /** @type {HTMLButtonElement[]} */
    this.sceneButtons = timeline.scenes.map((s, i) =>
      h(
        'button',
        { class: 'ap-drawer-item', type: 'button', dataset: { index: String(i) }, onClick: () => this.jumpTo(i) },
        h('span', { class: 'ap-drawer-num', text: String(i + 1) }),
        h(
          'span',
          { class: 'ap-drawer-text' },
          h('span', { class: 'ap-drawer-title', text: sceneLabel(s) }),
          h('span', { class: 'ap-drawer-meta', text: `${s.chapter_label ? `${s.chapter_label} · ` : ''}${formatTime(s.start)}` }),
        ),
      ),
    );
    /** @type {HTMLElement} */
    this.drawer = h(
      'aside',
      { class: 'ap-drawer', 'aria-label': 'Scenes', 'aria-hidden': 'true' },
      h(
        'div',
        { class: 'ap-drawer-head' },
        h('span', { class: 'ap-drawer-heading', text: 'Scenes' }),
        this.button('close', 'Close scene list', () => this.toggleDrawer(false)),
      ),
      h('div', { class: 'ap-drawer-list' }, this.sceneButtons),
    );

    // --- big play overlay ------------------------------------------------------------------
    /** @type {HTMLButtonElement} */
    this.bigPlay = h('button', { class: 'ap-bigplay', type: 'button', 'aria-label': 'Play lecture', onClick: () => player.play() }, icon('play'));

    /** @type {HTMLDivElement} */
    this.el = h(
      'div',
      { class: 'ap-controls' },
      this.scrubber,
      h(
        'div',
        { class: 'ap-controls-row' },
        h('div', { class: 'ap-controls-left' }, this.playBtn, this.prevBtn, this.nextBtn, h('div', { class: 'ap-vol' }, this.muteBtn, this.volume), this.time),
        h('div', { class: 'ap-controls-center' }, this.sceneTitle),
        h('div', { class: 'ap-controls-right' }, this.ccBtn, this.sizeBtn, this.speed, this.listBtn, this.fsBtn),
      ),
    );
    host.classList.add('ap-host');
    host.append(this.bigPlay, this.el, this.drawer);

    this.bindPlayer();
    this.bindScrubber();
    this.bindKeyboard();
    this.bindIdle();
    this.disposer.listen(document, 'fullscreenchange', () => this.syncFullscreen());
    this.syncAll();
  }

  /**
   * @param {string} name
   * @param {string} label
   * @param {() => void} onClick
   * @returns {HTMLButtonElement}
   */
  button(name, label, onClick) {
    return h('button', { class: 'ap-btn', type: 'button', 'aria-label': label, title: label, onClick }, icon(name));
  }

  /** @param {HTMLButtonElement} btn @param {string} name */
  setIcon(btn, name) {
    btn.replaceChildren(icon(name));
  }

  /** @param {number} t */
  pct(t) {
    return `${this.duration > 0 ? clamp((t / this.duration) * 100, 0, 100) : 0}%`;
  }

  bindPlayer() {
    const p = this.player;
    const offs = [
      p.on('timeupdate', (d) => this.onTime(d.t)),
      p.on('play', () => this.syncPlaying()),
      p.on('pause', () => this.syncPlaying()),
      p.on('ended', () => this.syncPlaying()),
      p.on('scenechange', () => this.syncScene()),
      p.on('volumechange', () => this.syncVolume()),
      p.on('ratechange', () => {
        this.speed.value = String(p.rate);
        this.emitPrefs();
      }),
      p.on('captionschange', () => this.syncCaptions()),
    ];
    for (const off of offs) this.disposer.add(off);
  }

  syncAll() {
    this.onTime(this.player.currentTime);
    this.syncPlaying();
    this.syncScene();
    this.syncVolume();
    this.syncCaptions();
    this.syncFullscreen();
  }

  /** @param {number} t */
  onTime(t) {
    if (this.dragging) return;
    this.showTime(t);
  }

  /** @param {number} t */
  showTime(t) {
    const p = this.pct(t);
    this.fill.style.width = p;
    this.thumb.style.left = p;
    this.time.textContent = `${formatTime(t)} / ${formatTime(this.duration)}`;
    this.scrubber.setAttribute('aria-valuenow', String(Math.round(t)));
    this.scrubber.setAttribute('aria-valuetext', `${formatDurationLong(t)} of ${formatDurationLong(this.duration)}`);
  }

  syncPlaying() {
    const playing = !this.player.paused;
    const ended = this.player.ended;
    this.setIcon(this.playBtn, playing ? 'pause' : ended ? 'replay' : 'play');
    this.playBtn.setAttribute('aria-label', playing ? 'Pause (k)' : ended ? 'Replay' : 'Play (k)');
    this.playBtn.title = this.playBtn.getAttribute('aria-label') || '';
    this.host.classList.toggle('is-playing', playing);
    this.host.classList.toggle('is-ended', ended);
    this.bigPlay.replaceChildren(icon(ended ? 'replay' : 'play'));
    this.bigPlay.setAttribute('aria-label', ended ? 'Replay lecture' : 'Play lecture');
    if (!playing) this.wake();
  }

  syncScene() {
    const i = this.player.sceneIndex;
    const scene = i >= 0 ? this.timeline.scenes[i] : null;
    const ch = chapterIndexAt(this.timeline.chapters, this.player.currentTime);
    const chapter = ch >= 0 ? (this.timeline.chapters || [])[ch] : null;
    this.sceneTitle.textContent = scene ? (chapter && chapter.title !== sceneLabel(scene) ? `${chapter.title} · ${sceneLabel(scene)}` : sceneLabel(scene)) : '';
    this.sceneButtons.forEach((b, k) => {
      b.classList.toggle('is-current', k === i);
      if (k === i) b.setAttribute('aria-current', 'step');
      else b.removeAttribute('aria-current');
    });
    this.prevBtn.disabled = i <= 0;
    this.nextBtn.disabled = i >= this.timeline.scenes.length - 1;
  }

  syncVolume() {
    const p = this.player;
    const silent = p.muted || p.volume === 0;
    this.setIcon(this.muteBtn, silent ? 'mute' : 'volume');
    this.muteBtn.setAttribute('aria-label', silent ? 'Unmute (m)' : 'Mute (m)');
    this.volume.value = String(p.muted ? 0 : p.volume);
    this.emitPrefs();
  }

  syncCaptions() {
    const on = this.player.captionsOn;
    this.ccBtn.classList.toggle('is-on', on);
    this.ccBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    this.captionSize = /** @type {'s' | 'm' | 'l'} */ (this.player.captions ? this.player.captions.size : this.captionSize);
    this.sizeBtn.textContent = `Aa ${this.captionSize.toUpperCase()}`;
    this.sizeBtn.setAttribute('aria-label', `Caption size: ${CAPTION_SIZE_LABEL[this.captionSize]}`);
    this.sizeBtn.title = this.sizeBtn.getAttribute('aria-label') || '';
    this.sizeBtn.disabled = !on;
    this.emitPrefs();
  }

  cycleCaptionSize() {
    const idx = CAPTION_SIZES.indexOf(this.captionSize);
    this.player.setCaptionSize(CAPTION_SIZES[(idx + 1) % CAPTION_SIZES.length]);
  }

  emitPrefs() {
    if (!this.opts.onPrefs) return;
    const p = this.player;
    this.opts.onPrefs({ volume: p.volume, muted: p.muted, rate: p.rate, captions: p.captionsOn, captionSize: this.captionSize });
  }

  /** @param {number} delta */
  stepScene(delta) {
    const scenes = this.timeline.scenes;
    if (!scenes.length) return;
    const t = this.player.currentTime;
    let i = this.player.sceneIndex;
    if (i < 0) i = delta > 0 ? -1 : 0;
    // "previous" restarts the current scene when more than 3 s into it
    if (delta < 0 && i >= 0 && t - scenes[i].start > 3) this.player.seekScene(i);
    else this.player.seekScene(clamp(i + delta, 0, scenes.length - 1));
  }

  /** @param {number} i */
  jumpTo(i) {
    this.player.seekScene(i);
    if (this.host.clientWidth < 900) this.toggleDrawer(false);
  }

  /** @param {boolean} [open] */
  toggleDrawer(open) {
    const next = open === undefined ? !this.host.classList.contains('drawer-open') : open;
    this.host.classList.toggle('drawer-open', next);
    this.drawer.setAttribute('aria-hidden', next ? 'false' : 'true');
    this.listBtn.setAttribute('aria-expanded', next ? 'true' : 'false');
    if (next) {
      const current = this.sceneButtons[this.player.sceneIndex];
      if (current) {
        current.focus({ preventScroll: true });
        if (typeof current.scrollIntoView === 'function') current.scrollIntoView({ block: 'nearest' });
      }
    }
  }

  toggleFullscreen() {
    const doc = /** @type {any} */ (document);
    if (doc.fullscreenElement) {
      doc.exitFullscreen().catch(() => {});
    } else if (typeof this.host.requestFullscreen === 'function') {
      this.host.requestFullscreen().catch(() => {});
    }
  }

  syncFullscreen() {
    const on = /** @type {any} */ (document).fullscreenElement === this.host;
    this.setIcon(this.fsBtn, on ? 'exitFullscreen' : 'fullscreen');
    this.fsBtn.setAttribute('aria-label', on ? 'Exit fullscreen (f)' : 'Fullscreen (f)');
    this.host.classList.toggle('is-fullscreen', on);
  }

  // --- scrubber ----------------------------------------------------------------------------

  /** @param {number} clientX @returns {number} absolute seconds */
  timeAtX(clientX) {
    const r = this.track.getBoundingClientRect();
    const u = r.width > 0 ? clamp((clientX - r.left) / r.width, 0, 1) : 0;
    return u * this.duration;
  }

  /** @param {number} t @param {number} clientX */
  showTooltip(t, clientX) {
    const r = this.track.getBoundingClientRect();
    const i = sceneIndexAt(this.timeline, t);
    const scene = i >= 0 ? this.timeline.scenes[i] : null;
    this.tooltip.textContent = scene ? `${formatTime(t)} · ${sceneLabel(scene)}` : formatTime(t);
    this.tooltip.style.left = `${clamp(clientX - r.left, 0, r.width)}px`;
    this.hover.style.width = this.pct(t);
    this.scrubber.classList.add('is-hover');
  }

  bindScrubber() {
    const s = this.scrubber;
    /** @type {number | null} */
    let pointerId = null;
    let startT = 0;
    this.disposer.listen(s, 'pointerdown', (e) => {
      const ev = /** @type {PointerEvent} */ (e);
      if (ev.button !== 0) return;
      pointerId = ev.pointerId;
      if (typeof s.setPointerCapture === 'function') s.setPointerCapture(ev.pointerId);
      this.dragging = true;
      startT = this.player.currentTime;
      const t = this.timeAtX(ev.clientX);
      this.showTime(t);
      this.player.seek(t, { track: false });
      ev.preventDefault();
    });
    this.disposer.listen(s, 'pointermove', (e) => {
      const ev = /** @type {PointerEvent} */ (e);
      const t = this.timeAtX(ev.clientX);
      this.showTooltip(t, ev.clientX);
      if (this.dragging && ev.pointerId === pointerId) {
        this.showTime(t);
        this.player.seek(t, { track: false });
      }
    });
    const end = (/** @type {Event} */ e) => {
      const ev = /** @type {PointerEvent} */ (e);
      if (!this.dragging || ev.pointerId !== pointerId) return;
      this.dragging = false;
      pointerId = null;
      const t = this.timeAtX(ev.clientX);
      // one analytics seek per drag (from where the drag started)
      this.player.seek(t, { from: startT });
    };
    this.disposer.listen(s, 'pointerup', end);
    this.disposer.listen(s, 'pointercancel', end);
    this.disposer.listen(s, 'pointerleave', () => s.classList.remove('is-hover'));
    this.disposer.listen(s, 'keydown', (e) => {
      const ev = /** @type {KeyboardEvent} */ (e);
      const t = this.player.currentTime;
      /** @type {number | null} */
      let to = null;
      if (ev.key === 'ArrowLeft' || ev.key === 'ArrowDown') to = t - SEEK_STEP;
      else if (ev.key === 'ArrowRight' || ev.key === 'ArrowUp') to = t + SEEK_STEP;
      else if (ev.key === 'PageDown') to = t - 30;
      else if (ev.key === 'PageUp') to = t + 30;
      else if (ev.key === 'Home') to = 0;
      else if (ev.key === 'End') to = this.duration;
      if (to !== null) {
        ev.preventDefault();
        ev.stopPropagation();
        this.player.seek(to);
      }
    });
  }

  // --- keyboard ----------------------------------------------------------------------------

  bindKeyboard() {
    const target = this.opts.keyboardTarget || document;
    this.disposer.listen(target, 'keydown', (e) => {
      const ev = /** @type {KeyboardEvent} */ (e);
      if (ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey || isEditable(ev)) return;
      const onButton = /** @type {HTMLElement | null} */ (ev.target)?.tagName === 'BUTTON';
      const p = this.player;
      const key = ev.key.length === 1 ? ev.key.toLowerCase() : ev.key;
      let handled = true;
      switch (key) {
        case ' ':
          if (onButton) return; // the focused button handles space itself
          p.toggle();
          break;
        case 'k':
          p.toggle();
          break;
        case 'ArrowLeft':
          if (ev.shiftKey) this.stepScene(-1);
          else p.seek(p.currentTime - SEEK_STEP);
          break;
        case 'ArrowRight':
          if (ev.shiftKey) this.stepScene(1);
          else p.seek(p.currentTime + SEEK_STEP);
          break;
        case 'j':
          p.seek(p.currentTime - SEEK_JUMP);
          break;
        case 'l':
          p.seek(p.currentTime + SEEK_JUMP);
          break;
        case 'ArrowUp':
          p.setVolume(clamp(p.volume + 0.1, 0, 1));
          break;
        case 'ArrowDown':
          p.setVolume(clamp(p.volume - 0.1, 0, 1));
          break;
        case 'p':
          this.stepScene(-1);
          break;
        case 'n':
          this.stepScene(1);
          break;
        case 'c':
          p.setCaptions(!p.captionsOn);
          break;
        case 'm':
          p.setMuted(!p.muted);
          break;
        case 'f':
          this.toggleFullscreen();
          break;
        case 'Escape':
          if (this.host.classList.contains('drawer-open')) this.toggleDrawer(false);
          else handled = false;
          break;
        default:
          handled = false;
      }
      if (handled) {
        ev.preventDefault();
        this.wake();
      }
    });
  }

  // --- auto-hide -----------------------------------------------------------------------------

  bindIdle() {
    const wake = () => this.wake();
    for (const type of ['pointermove', 'pointerdown', 'focusin']) this.disposer.listen(this.host, type, wake);
    this.disposer.add(() => {
      if (this.idleTimer) clearTimeout(this.idleTimer);
    });
    const relayout = () => this.updateCaptionLift();
    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(relayout);
      ro.observe(this.host);
      this.disposer.add(() => ro.disconnect());
    } else {
      this.disposer.listen(window, 'resize', relayout);
    }
  }

  /** Show the controls and restart the idle timer. */
  wake() {
    this.host.classList.remove('is-idle');
    this.updateCaptionLift();
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => {
      if (!this.player.paused && !this.host.classList.contains('drawer-open') && !this.dragging) {
        this.host.classList.add('is-idle');
        this.updateCaptionLift();
      }
    }, this.opts.idleMs ?? 2600);
  }

  /** Keep captions above the control bar while it is shown (they drop back when it auto-hides). */
  updateCaptionLift() {
    if (this.host.classList.contains('is-idle') || !this.player.stage) {
      this.player.setCaptionLift(0);
      return;
    }
    const stage = this.player.stage;
    const lift = captionLift(stage.el.getBoundingClientRect().bottom, this.scrubber.getBoundingClientRect().top, stage.scale);
    this.player.setCaptionLift(lift);
  }

  destroy() {
    this.disposer.dispose();
    this.player.setCaptionLift(0);
    this.el.remove();
    this.drawer.remove();
    this.bigPlay.remove();
    this.host.classList.remove('ap-host', 'is-idle', 'is-playing', 'is-ended', 'drawer-open', 'is-fullscreen');
  }
}
