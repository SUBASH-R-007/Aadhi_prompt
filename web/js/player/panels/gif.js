// @ts-check
/**
 * GIF panel (GIPHY hotlink). Live/preview only: the GIF with its required attribution and a
 * link to the source. GIFs are not composited into MP4s (`render_in_mp4: false`), so render
 * mode shows the panel title only.
 */

import { Disposer, h, safeUrl } from '../../shared/dom.js';
import { decodeImage } from './figure.js';
import { showNotice, stripRich } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */

export const DEFAULT_GIF_ATTRIBUTION = 'Powered by GIPHY';

/** @type {PanelFactory} */
export function createGifPanel(body, rsp, ctx, chrome) {
  const disposer = new Disposer();
  const media = rsp.media || null;
  if (ctx.mode === 'render') {
    // Hotlinked GIFs are never composited into the MP4 (MediaRef.render_in_mp4 is false).
    chrome.el.classList.add('ap-panel--title-only');
    return { destroy: () => disposer.dispose() };
  }
  const url = media && media.url ? String(media.url) : '';
  if (!url) {
    showNotice(body, 'The GIF is not available.');
    return { destroy: () => disposer.dispose() };
  }
  const img = h('img', {
    class: 'ap-img is-contain',
    src: url,
    alt: stripRich(rsp.panel && (rsp.panel.title || rsp.panel.gif_query)) || 'Animated GIF',
    decoding: 'async',
    referrerpolicy: 'no-referrer',
    draggable: 'false',
  });
  const attribution = stripRich(media && media.attribution) || DEFAULT_GIF_ATTRIBUTION;
  const link = media && media.link_url ? safeUrl(String(media.link_url)) : 'about:blank';
  const credit =
    link !== 'about:blank'
      ? h('a', { class: 'ap-gif-credit', href: link, target: '_blank', rel: 'noopener noreferrer nofollow', text: attribution })
      : h('span', { class: 'ap-gif-credit', text: attribution });
  body.append(h('figure', { class: 'ap-figure' }, h('div', { class: 'ap-media-box' }, img), h('figcaption', { class: 'ap-caption' }, credit)));
  disposer.listen(img, 'error', () => showNotice(body, 'The GIF could not be loaded.', 'warn'));

  return {
    ready: () => decodeImage(img),
    destroy() {
      disposer.dispose();
      img.removeAttribute('src');
    },
  };
}
