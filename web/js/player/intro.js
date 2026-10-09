// @ts-check
/**
 * Intro sequence: the REC logo animation, then the title cards (IntroSpec.cards: subject + unit,
 * session number + title) on the static background. Live mode plays the logo video (with its sound)
 * in sync with the clock and fades the cards; render mode shows each card at full opacity on a
 * transparent stage (ffmpeg composites the logo video, background and fades).
 */

import { h } from '../shared/dom.js';
import { clamp, fnv1a, num } from '../shared/format.js';
import { introCardAt } from './schedule.js';
import { releaseMedia } from './audio.js';

/** @typedef {import('../shared/types.js').IntroSpec} IntroSpec */
/** @typedef {import('../shared/types.js').PlayerMode} PlayerMode */

const CARD_FADE = 0.6;
const DRIFT_TOLERANCE = 0.3;

/**
 * Card opacity at absolute time t (live mode fades; 0 outside the card).
 * @param {import('../shared/types.js').TitleCard} card
 * @param {number} t
 */
export function cardOpacity(card, t) {
  const into = t - card.start;
  const left = card.start + card.duration - t;
  if (into < 0 || left <= 0) return 0;
  return clamp(Math.min(into / CARD_FADE, left / CARD_FADE, 1), 0, 1);
}

/**
 * Render-mode states of the intro: [{t, key}] (logo phase, then one state per card).
 * @param {IntroSpec | null | undefined} intro
 * @returns {{t: number, key: string}[]}
 */
export function introStates(intro) {
  if (!intro || !(num(intro.duration, 0) > 0)) return [];
  /** @type {{t: number, key: string}[]} */
  const out = [];
  const cards = intro.cards || [];
  if (!cards.length || cards[0].start > 0) out.push({ t: 0, key: introKey(intro, -1) });
  cards.forEach((c, i) => out.push({ t: c.start, key: introKey(intro, i) }));
  return out;
}

/**
 * @param {IntroSpec} intro
 * @param {number} cardIndex   -1 for the logo phase
 */
export function introKey(intro, cardIndex) {
  const card = cardIndex >= 0 ? (intro.cards || [])[cardIndex] : null;
  return card ? `intro-${cardIndex}-${fnv1a(`${card.line1}|${card.line2 || ''}`)}` : 'intro-logo';
}

export class IntroView {
  /**
   * @param {HTMLElement} layer
   * @param {IntroSpec} intro
   * @param {{ mode: PlayerMode }} opts
   */
  constructor(layer, intro, opts) {
    this.intro = intro;
    this.mode = opts.mode;
    this.duration = num(intro.duration, 0);
    this.logoDuration = num(intro.logo_duration, 0);
    this.cardIndex = -2;
    this.playing = false;
    this.rate = 1;
    this.volume = 1;
    this.muted = false;
    /** @type {HTMLVideoElement | null} */
    this.logo = null;
    /** @type {HTMLImageElement | null} */
    this.background = null;
    if (this.mode !== 'render') {
      if (intro.logo_video_url) {
        this.logo = h('video', { class: 'ap-intro-logo', src: intro.logo_video_url, preload: 'auto', playsinline: true });
      }
      if (intro.background_url) {
        this.background = h('img', { class: 'ap-intro-bg', src: intro.background_url, alt: '', decoding: 'async' });
      }
    }
    /** @type {HTMLDivElement} */
    this.line1 = h('div', { class: 'ap-intro-line1', dir: 'auto' });
    /** @type {HTMLDivElement} */
    this.line2 = h('div', { class: 'ap-intro-line2', dir: 'auto' });
    /** @type {HTMLDivElement} */
    this.card = h('div', { class: 'ap-intro-card' }, this.line1, this.line2);
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: 'ap-intro is-hidden', 'aria-live': 'polite' }, this.background, this.logo, this.card);
    layer.appendChild(this.el);
  }

  /** Whether absolute time t is inside the intro. @param {number} t */
  covers(t) {
    return t < this.duration;
  }

  /**
   * @param {number} index
   */
  _showCard(index) {
    if (index === this.cardIndex) return;
    this.cardIndex = index;
    const card = index >= 0 ? (this.intro.cards || [])[index] : null;
    this.line1.textContent = card ? card.line1 : '';
    this.line2.textContent = card ? card.line2 || '' : '';
    this.card.classList.toggle('has-line2', !!(card && card.line2));
  }

  /**
   * Live/preview update at absolute time t.
   * @param {number} t
   * @param {{ playing: boolean, rate: number, seeked: boolean }} frame
   */
  update(t, frame) {
    const inside = this.covers(t);
    this.el.classList.toggle('is-hidden', !inside);
    if (!inside) {
      if (this.logo && !this.logo.paused) this.logo.pause();
      return;
    }
    const inLogo = t < this.logoDuration;
    this.el.classList.toggle('is-logo', inLogo);
    if (this.logo) this._syncLogo(t, inLogo, frame);
    const idx = introCardAt(this.intro, t);
    this._showCard(idx);
    const card = idx >= 0 ? (this.intro.cards || [])[idx] : null;
    this.card.style.opacity = card ? String(cardOpacity(card, t)) : '0';
  }

  /**
   * @param {number} t
   * @param {boolean} inLogo
   * @param {{ playing: boolean, rate: number, seeked: boolean }} frame
   */
  _syncLogo(t, inLogo, frame) {
    const v = /** @type {HTMLVideoElement} */ (this.logo);
    if (!inLogo) {
      if (!v.paused) v.pause();
      return;
    }
    v.playbackRate = frame.rate;
    v.volume = this.volume;
    v.muted = this.muted;
    if (frame.seeked || Math.abs(v.currentTime - t) > DRIFT_TOLERANCE) {
      try {
        v.currentTime = t;
      } catch {
        /* not seekable yet */
      }
    }
    if (frame.playing && v.paused) {
      const p = v.play();
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } else if (!frame.playing && !v.paused) {
      v.pause();
    }
  }

  /** @param {number} volume @param {boolean} muted */
  setVolume(volume, muted) {
    this.volume = volume;
    this.muted = muted;
    if (this.logo) {
      this.logo.volume = volume;
      this.logo.muted = muted;
    }
  }

  /**
   * Render mode: show the card at absolute time t at full opacity (logo phase = empty stage).
   * @param {number} t
   * @returns {string} state key
   */
  renderAt(t) {
    const inside = this.covers(t);
    this.el.classList.toggle('is-hidden', !inside);
    const idx = inside ? introCardAt(this.intro, t) : -1;
    this.el.classList.toggle('is-logo', inside && t < this.logoDuration);
    this._showCard(idx);
    this.card.style.opacity = idx >= 0 ? '1' : '0';
    return introKey(this.intro, idx);
  }

  destroy() {
    if (this.logo) releaseMedia(this.logo);
    this.el.remove();
  }
}
