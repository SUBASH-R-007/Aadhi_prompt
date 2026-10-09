// @ts-check
/**
 * In-browser preview of a finished MP4 in a dialog: an HTML5 <video controls> on the signed, streamable
 * preview URL (the server answers byte ranges, so seeking works), with a download link under it. Closing
 * the dialog stops the video and releases its connection. A video that cannot be loaded is tried once more with a
 * fresh link when the caller can give one (an expired signed link), then says so and keeps the download link.
 */

import { h } from '../../shared/dom.js';
import { openModal } from './modal.js';
import { linkButton } from './form.js';

/**
 * Stop a media element and drop its source (frees the connection and the decoder).
 * @param {HTMLMediaElement} media
 */
export function releaseVideo(media) {
  try {
    media.pause();
  } catch {
    /* not playing */
  }
  media.removeAttribute('src');
  try {
    media.load();
  } catch {
    /* nothing to reset */
  }
}

/**
 * `refresh`: a fresh preview URL for the same video (a signed link may have expired since the list was loaded); on
 * the first load error the player tries it once, at the same time, before saying the video could not be loaded.
 * `returnFocus`: where focus goes on close when the button that opened the preview was redrawn meanwhile.
 * @param {{ title: string, src: string, downloadUrl?: string | null, details?: string,
 *   refresh?: () => Promise<string | null>, returnFocus?: () => HTMLElement | null }} opts
 * @returns {import('./modal.js').ModalHandle}
 */
export function openVideoPreview(opts) {
  const video = /** @type {HTMLVideoElement} */ (h('video', { class: 'video-preview-player', controls: true, preload: 'metadata', playsinline: true, src: opts.src }));
  const failed = h('p', { class: 'notice warning small', role: 'alert', hidden: true }, 'This video could not be loaded here. Reload the page to try again, or download it to watch it.');
  let retried = false;
  video.addEventListener('error', async () => {
    if (!video.isConnected) return; // closed: releasing the source is not a failure
    if (opts.refresh && !retried) {
      retried = true;
      const at = video.currentTime || 0;
      /** @type {string | null} */
      let next = null;
      try {
        next = await opts.refresh();
      } catch {
        next = null;
      }
      if (next && video.isConnected) {
        if (at > 0) video.addEventListener('loadedmetadata', () => { video.currentTime = at; }, { once: true });
        video.src = next;
        try {
          video.load();
        } catch {
          /* setting src loads it anyway */
        }
        return;
      }
    }
    if (video.isConnected) failed.hidden = false;
  });
  return openModal({
    title: opts.title,
    size: 'xl',
    body: h(
      'div',
      { class: 'video-preview' },
      video,
      failed,
      opts.details || opts.downloadUrl
        ? h(
            'div',
            { class: 'row gap wrap video-preview-foot' },
            opts.downloadUrl ? linkButton('Download MP4', opts.downloadUrl, { kind: 'gold', small: true, icon: 'download', download: true }) : null,
            opts.details ? h('span', { class: 'muted small' }, opts.details) : null,
          )
        : null,
    ),
    actions: [{ label: 'Close', kind: 'outline', value: null }],
    returnFocus: opts.returnFocus,
    onClose: () => releaseVideo(video),
  });
}
