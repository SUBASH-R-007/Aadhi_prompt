// @ts-check
/**
 * Still image panels (source figures, generated illustrations, uploaded images).
 * The <img> is part of the render-mode screenshot (not a media hole), so ready() waits until it
 * is decoded.
 */

import { Disposer, h } from '../../shared/dom.js';
import { showNotice, stripRich } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */
/** @typedef {import('./types.js').ResolvedSidePanel} ResolvedSidePanel */
/** @typedef {import('./types.js').PanelImpl} PanelImpl */

/**
 * Shared implementation for figure / image panels.
 * @param {HTMLElement} body
 * @param {ResolvedSidePanel} rsp
 * @param {{ missingText: string }} opts
 * @returns {PanelImpl}
 */
export function createStillPanel(body, rsp, opts) {
  const disposer = new Disposer();
  const media = rsp.media || null;
  const url = media && media.url ? String(media.url) : '';
  if (!url) {
    showNotice(body, opts.missingText);
    return { destroy: () => disposer.dispose() };
  }
  const alt = stripRich(rsp.panel && rsp.panel.title) || '';
  const img = h('img', {
    class: ['ap-img', media && media.fit === 'cover' ? 'is-cover' : 'is-contain'],
    src: url,
    alt,
    decoding: 'async',
    loading: 'eager',
    draggable: 'false',
    referrerpolicy: 'no-referrer',
  });
  if (media && media.width && media.height) {
    img.setAttribute('width', String(media.width));
    img.setAttribute('height', String(media.height));
  }
  const frame = h('figure', { class: 'ap-figure' }, h('div', { class: 'ap-media-box' }, img));
  const caption = stripRich(media && media.attribution);
  if (caption) frame.appendChild(h('figcaption', { class: 'ap-caption', text: caption }));
  body.appendChild(frame);

  let failed = false;
  disposer.listen(img, 'error', () => {
    if (failed) return;
    failed = true;
    frame.classList.add('is-broken');
    showNotice(body, 'This image could not be loaded.', 'warn');
  });

  /** @type {Promise<void> | null} */
  let readyPromise = null;
  return {
    ready() {
      if (!readyPromise) readyPromise = decodeImage(img);
      return readyPromise;
    },
    destroy() {
      disposer.dispose();
      img.removeAttribute('src');
    },
  };
}

/**
 * Resolve when the image is decoded (or failed: a broken image must not hang the renderer).
 * @param {HTMLImageElement} img
 * @returns {Promise<void>}
 */
export function decodeImage(img) {
  if (typeof img.decode === 'function') {
    return img.decode().then(
      () => undefined,
      () => undefined,
    );
  }
  if (img.complete) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      img.removeEventListener('load', done);
      img.removeEventListener('error', done);
      resolve();
    };
    img.addEventListener('load', done);
    img.addEventListener('error', done);
  });
}

/** @type {PanelFactory} */
export function createFigurePanel(body, rsp) {
  return createStillPanel(body, rsp, { missingText: 'The figure is not available yet.' });
}
