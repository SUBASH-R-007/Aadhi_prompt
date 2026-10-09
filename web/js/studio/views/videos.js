// @ts-check
/**
 * Videos (#/videos): every video made from the signed-in user's lectures, newest first (GET /api/videos,
 * pages of PAGE_SIZE with "Load more"). Filters, kept in the URL (`?filter=`): all videos, the ones that
 * match their lecture's current script, and the ones made before later changes. A finished video plays
 * in a dialog (HTML5 <video controls> on its signed, streamable `preview_url`) and downloads from
 * `download_url`; each links to its lecture. While a video is still being made the list is read again
 * every POLL_MS, never while the tab is hidden (keyboard focus stays on its control when the list is drawn
 * again). A read that changes nothing on screen (a presigned `preview_url` is new on every read) leaves the list as it
 * is; videos loaded past the re-read window (`MAX_REFRESH`) are kept. Lectures that share a title get the same short
 * suffix as on the project list; render numbers are technical details shown only with `?debug`. The preview asks
 * for a fresh link once when its link no longer loads (an expired signed URL), and focus goes back to the video's
 * Watch button when the dialog closes, even after the list was drawn again.
 */

import { h, clear } from '../../shared/dom.js';
import { get } from '../../shared/api.js';
import { button, linkButton, spinner, emptyState, errorState } from '../components/form.js';
import { statusBadge } from '../components/badges.js';
import { icon } from '../components/icons.js';
import { openVideoPreview } from '../components/videoPreview.js';
import { captureFocus, restoreFocus } from '../components/focusKeep.js';
import { href } from '../router.js';
import { formatBytes, formatDate, formatDuration, formatRelative } from '../util.js';
import { showTechnical } from '../lib/debug.js';
import { distinctTitles, withSuffix } from '../lib/names.js';
import { pageHidden, onceVisible } from '../lib/visibility.js';
import { pageHeader } from './common.js';

/** @typedef {import('../types.js').VideoItem} VideoItem */

export const PAGE_SIZE = 24;
/** Largest page asked for when the loaded list is read again. */
const MAX_REFRESH = 100;
/** How often the list is read again while a video is being made. */
export const POLL_MS = 10_000;

/** @type {Record<string, { label: string, test: (v: VideoItem) => boolean, empty: string }>} */
export const FILTERS = {
  all: { label: 'All videos', test: () => true, empty: '' },
  current: { label: 'Up to date', test: (v) => v.status === 'succeeded' && v.matches_current === true, empty: 'None of the loaded videos matches its lecture’s current script.' },
  outdated: { label: 'Out of date', test: (v) => v.status === 'succeeded' && v.matches_current === false, empty: 'None of the loaded videos is out of date.' },
};

/** @param {VideoItem} v */
const active = (v) => v.status === 'queued' || v.status === 'running';

/**
 * What the list on screen shows of the videos: a presigned `preview_url` is new on every read, so only whether there
 * is one counts.
 * @param {VideoItem[]} list
 */
const shownKey = (list) => JSON.stringify(list.map((v) => ({ ...v, preview_url: v.preview_url ? 1 : null })));

/**
 * The lecture title a video shows (before any suffix), as the project list's cards do.
 * @param {VideoItem} v
 */
const lectureTitle = (v) => v.project_title || v.session_title || 'Untitled lecture';

/**
 * One line of facts about a video ("Version 2 · 3:24 · 1920 × 1080 · 24 MB").
 * @param {VideoItem} v
 * @param {boolean} technical
 */
export function videoFacts(v, technical) {
  return [
    technical ? `Render #${v.render_id}` : '',
    `Version ${v.version_number}`,
    v.duration_s ? formatDuration(v.duration_s) : '',
    v.width && v.height ? `${v.width} × ${v.height}` : '',
    v.size_bytes ? formatBytes(v.size_bytes) : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app, query }) {
  /** @type {VideoItem[]} */
  let items = [];
  let total = 0;
  let filter = Object.prototype.hasOwnProperty.call(FILTERS, query.filter) ? query.filter : 'all';
  let loadToken = 0;
  let destroyed = false;
  let loaded = false;
  /** @type {ReturnType<typeof setTimeout> | null} */
  let pollTimer = null;
  /** @type {(() => void) | null} */
  let cancelVisibleWait = null;
  /** @type {import('../components/modal.js').ModalHandle | null} */
  let preview = null;
  const technical = showTechnical();

  const filtersEl = h('div', { class: 'videos-filters row gap wrap', role: 'group', 'aria-label': 'Show' });
  const list = h('div', { class: 'video-list', 'aria-live': 'polite', 'aria-busy': 'false' });
  const footer = h('div', { class: 'list-footer' });
  container.append(
    pageHeader('Videos', 'Every video made from your lectures, newest first. Watch one here or download it.', linkButton('Your lectures', '#/projects', { kind: 'outline', icon: 'board' })),
    filtersEl,
    list,
    footer,
  );

  const stopPolling = () => {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
    if (cancelVisibleWait) cancelVisibleWait();
    cancelVisibleWait = null;
  };

  /** Read the list again in POLL_MS while a video is being made (paused while the tab is hidden). */
  const schedulePoll = () => {
    if (destroyed || pollTimer || cancelVisibleWait || !items.some(active)) return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      if (pageHidden()) {
        cancelVisibleWait = onceVisible(() => {
          cancelVisibleWait = null;
          void refresh();
        });
        return;
      }
      void refresh();
    }, POLL_MS);
  };

  /** @param {boolean} append */
  async function load(append) {
    const token = ++loadToken;
    stopPolling();
    if (!append) {
      clear(list);
      clear(footer);
      list.appendChild(spinner('Loading videos…'));
    }
    list.setAttribute('aria-busy', 'true');
    try {
      const res = await get(`/api/videos?limit=${PAGE_SIZE}&offset=${append ? items.length : 0}`);
      if (destroyed || token !== loadToken) return;
      const page = res && Array.isArray(res.items) ? res.items.filter((/** @type {any} */ v) => v && Number.isInteger(v.render_id)) : [];
      items = append ? [...items, ...page.filter((/** @type {VideoItem} */ v) => !items.some((x) => x.render_id === v.render_id))] : page;
      total = res && Number.isInteger(res.total) ? res.total : items.length;
      loaded = true;
      render();
    } catch (err) {
      if (destroyed || token !== loadToken) return;
      if (append) {
        app.reportError(err, 'Could not load more videos.');
        return;
      }
      clear(list);
      list.appendChild(errorState('Could not load your videos.', () => void load(false)));
      app.reportError(err);
    } finally {
      if (token === loadToken) list.setAttribute('aria-busy', 'false');
    }
  }

  /** The first videos again (newest first), at most MAX_REFRESH of them. */
  function readWindow() {
    const limit = Math.min(MAX_REFRESH, Math.max(PAGE_SIZE, items.length));
    return get(`/api/videos?limit=${limit}&offset=0`).then((/** @type {any} */ res) => ({ res, limit }));
  }

  /** Read the loaded part of the list again (videos being made finish, new ones appear). */
  async function refresh() {
    const token = ++loadToken;
    try {
      const { res, limit } = await readWindow();
      if (destroyed || token !== loadToken) return;
      const fresh = res && Array.isArray(res.items) ? res.items.filter((/** @type {any} */ v) => v && Number.isInteger(v.render_id)) : items;
      // The re-read covers at most MAX_REFRESH videos (newest first): the older ones loaded past it are kept. Only
      // when the window came back full: a shorter answer is the whole list (videos of deleted lectures are gone).
      const last = res && Array.isArray(res.items) && fresh.length === limit ? fresh[fresh.length - 1] : null;
      const next = last ? [...fresh, ...items.filter((v) => v.render_id < last.render_id)] : fresh;
      const nextTotal = res && Number.isInteger(res.total) ? res.total : total;
      if (nextTotal === total && shownKey(next) === shownKey(items)) {
        items = next; // the freshest preview links; the list on screen stays as it is
        schedulePoll();
        return;
      }
      items = next;
      total = nextTotal;
      render();
    } catch {
      if (destroyed || token !== loadToken) return;
      schedulePoll(); // try again later; the list on screen stays
    }
  }

  function renderFilters() {
    clear(filtersEl);
    for (const [key, f] of Object.entries(FILTERS)) {
      const n = items.filter(f.test).length;
      const b = button(key === 'all' ? `${f.label} (${total})` : `${f.label} (${n})`, { kind: key === filter ? 'primary' : 'ghost', small: true, ariaPressed: key === filter, dataset: { filter: key, fk: `filter:${key}` } });
      b.addEventListener('click', () => setFilter(key));
      filtersEl.appendChild(b);
    }
  }

  function render() {
    stopPolling();
    const focus = captureFocus(container);
    renderFilters();
    clear(list);
    clear(footer);
    if (!items.length) {
      filtersEl.hidden = true;
      list.appendChild(
        emptyState(
          'No videos yet',
          'When a lecture is built, choose “Make the video” on its page. Every video you make is listed here.',
          linkButton('Go to your lectures', '#/projects', { kind: 'gold', icon: 'board' }),
        ),
      );
      return;
    }
    filtersEl.hidden = false;
    // the same fields as the project list, so a lecture gets the same suffix on both pages (older servers: "(n)")
    const suffixes = distinctTitles(items.map((v) => ({
      id: v.project_id,
      title: lectureTitle(v),
      session_number: v.session_number,
      unit_name: v.unit_name,
      subject_name: v.subject_name,
      language: v.project_language,
      created_at: v.project_created_at,
    })));
    const shown = items.filter(FILTERS[filter].test);
    if (!shown.length) {
      list.appendChild(emptyState('No videos here', FILTERS[filter].empty, button('Show all videos', { kind: 'outline', onClick: () => setFilter('all') })));
    }
    for (const v of shown) list.appendChild(videoCard(v, suffixes.get(v.project_id)));
    footer.append(h('span', { class: 'muted' }, filter === 'all' ? `Showing ${items.length} of ${total}` : `${shown.length} of ${items.length} loaded videos`));
    if (items.length < total) footer.append(button('Load more', { kind: 'outline', onClick: () => void load(true), dataset: { fk: 'more' } }));
    restoreFocus(container, focus);
    schedulePoll();
  }

  /**
   * @param {VideoItem} v
   * @param {string | undefined} suffix
   */
  function videoCard(v, suffix) {
    const title = lectureTitle(v);
    const done = v.status === 'succeeded';
    const watch = done && v.preview_url ? button('Watch', { kind: 'primary', small: true, icon: 'play', dataset: { fk: `watch:${v.render_id}` } }) : null;
    if (watch && v.preview_url) {
      watch.addEventListener('click', () => {
        // read when clicked: a list that was not drawn again still holds the newest signed link
        const src = items.find((x) => x.render_id === v.render_id)?.preview_url || v.preview_url || '';
        if (preview) preview.close();
        preview = openVideoPreview({
          title: withSuffix(title, suffix),
          src,
          downloadUrl: v.download_url,
          details: [videoFacts(v, technical), formatDate(v.created_at)].filter(Boolean).join(' · '),
          // the list may have been drawn again while the video played: back to this video's new Watch button
          returnFocus: () => /** @type {HTMLElement | null} */ (
            container.querySelector(`[data-fk="watch:${v.render_id}"]`)
            || container.querySelector(`[data-render-id="${v.render_id}"] a, [data-render-id="${v.render_id}"] button`)
            || filtersEl.querySelector('[aria-pressed="true"]')
          ),
          // an expired signed link: one fresh link for this video
          refresh: async () => {
            const { res } = await readWindow();
            const fresh = res && Array.isArray(res.items) ? res.items.find((/** @type {any} */ x) => x && x.render_id === v.render_id) : null;
            return fresh && typeof fresh.preview_url === 'string' ? fresh.preview_url : null;
          },
        });
        const opened = preview;
        void opened.result.then(() => {
          if (preview === opened) preview = null;
        });
      });
    }
    const download = done && v.download_url ? linkButton('Download MP4', v.download_url, { kind: 'gold', small: true, icon: 'download', download: true }) : null;
    if (download) download.dataset.fk = `download:${v.render_id}`;
    const open = linkButton('Open lecture', href('project', { id: v.project_id }, { version: v.version_id }), { kind: 'outline', small: true });
    open.dataset.fk = `open:${v.render_id}`;
    return h(
      'article',
      { class: ['card', 'video-card', active(v) ? 'is-busy' : ''], dataset: { renderId: String(v.render_id), status: v.status } },
      h(
        'h2',
        { class: 'card-title' },
        h('a', { href: href('project', { id: v.project_id }, { version: v.version_id }), dataset: { fk: `title:${v.render_id}` } }, title, suffix ? h('span', { class: 'title-suffix' }, suffix.startsWith('(') ? ` ${suffix}` : ` · ${suffix}`) : null),
      ),
      h('p', { class: 'card-meta' }, videoFacts(v, technical)),
      h(
        'div',
        { class: 'row gap wrap card-badges' },
        statusBadge(v.status, active(v) ? 'Being made' : undefined),
        done && v.matches_current === true ? h('span', { class: 'badge badge-ok', title: 'Made from the lecture’s current script' }, icon('check', { size: 12 }), 'Up to date') : null,
        done && v.matches_current === false ? h('span', { class: 'badge badge-attention', title: 'Made before later changes to the script' }, icon('refresh', { size: 12 }), 'Out of date') : null,
        done && v.qa_ok === false ? h('span', { class: 'badge badge-attention', title: 'The automatic check of the finished video found something to look at' }, icon('warning', { size: 12 }), 'Check this video') : null,
      ),
      v.status === 'failed' ? h('p', { class: 'error-text small' }, 'This video could not be made. Open the lecture to try again.') : null,
      v.status === 'cancelled' ? h('p', { class: 'muted small' }, 'This video was cancelled.') : null,
      h('p', { class: 'card-foot muted' }, h('time', { datetime: v.created_at, title: formatDate(v.created_at) }, `Made ${formatRelative(v.created_at)}`)),
      h(
        'div',
        { class: 'card-actions row gap wrap' },
        watch,
        download,
        open,
      ),
    );
  }

  /** @param {string} key */
  function setFilter(key) {
    if (!FILTERS[key] || key === filter) return;
    filter = key;
    app.replaceHash(href('videos', {}, { filter: key === 'all' ? undefined : key }));
    if (loaded) render();
    const pressed = /** @type {HTMLElement | null} */ (filtersEl.querySelector(`[data-filter="${key}"]`));
    if (pressed) pressed.focus();
  }

  void load(false);
  return {
    destroy() {
      destroyed = true;
      stopPolling();
      if (preview) preview.close();
      preview = null;
    },
  };
}
