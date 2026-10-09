// @ts-check
/**
 * The teacher's media library (GET /api/library): the pictures and video clips they uploaded, the
 * ones Aadhi made for their lectures and the figures taken from their documents. Shared by the
 * Library page (views/library.js) and the picker other pages open to reuse an item:
 *
 *   openLibraryPicker({ app, kind?, projectId?, title?, sources?, suggested? }) -> Promise<LibraryItem | null>
 *
 * `kind` limits the picker to pictures or videos, `sources` to some sources. With `projectId`, the chosen
 * item is attached to that lecture (POST /api/library/{id}/attach; the user's own lectures only) before the
 * promise resolves, and the result carries the key the attach returned, so the lecture's screenplay accepts
 * it at once. `suggested` items are shown first ("Best matches"). The picker can also upload a new file: it
 * lands in the library and is chosen. Screen readers hear a short count after each search, not the grid.
 *
 * Every string that comes from the server (titles, descriptions, keywords, prompts) is set as text,
 * never as HTML.
 */

import { h, clear } from '../../shared/dom.js';
import { api, get, post, ApiError, CSRF_HEADER } from '../../shared/api.js';
import { button, input, select, spinner, emptyState, errorState } from './form.js';
import { openModal } from './modal.js';
import { icon } from './icons.js';
import { progressBar } from './badges.js';
import { debounce, formatDate, formatDuration, plural } from '../util.js';
import { validateMediaFile } from '../lib/optionsForm.js';
import { errorMessage } from '../errors.js';

/** @typedef {import('../types.js').LibraryItem} LibraryItem */
/** @typedef {import('../types.js').AppContext} AppContext */

/** Items per request (the API default; it allows at most 200). */
export const LIBRARY_PAGE_SIZE = 48;

/** The server's limits for an item's details (PATCH /api/library/{id}). */
export const LIBRARY_LIMITS = Object.freeze({ title: 120, description: 1000, keywords: 20, keyword: 40 });

/** Upload size when /api/meta cannot be read (the server checks its own limit anyway). */
const DEFAULT_MAX_MB = 50;

export const KIND_LABELS = Object.freeze({ image: 'Picture', video: 'Video' });
export const SOURCE_LABELS = Object.freeze({ upload: 'Uploaded', generated: 'Made by AI', figure: 'From a document' });

/** Choices of the "Show" filter. */
export const KIND_FILTERS = Object.freeze([
  { value: '', label: 'Pictures and videos' },
  { value: 'image', label: 'Pictures' },
  { value: 'video', label: 'Videos' },
]);

const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.webp', '.gif'];
const VIDEO_EXTS = ['.mp4', '.webm'];

/**
 * File extensions a library upload accepts (the `accept` of a file input).
 * @param {string} [kind]  'image' | 'video'; anything else = both
 * @returns {string[]}
 */
export function acceptedExtensions(kind) {
  if (kind === 'image') return IMAGE_EXTS.slice();
  if (kind === 'video') return VIDEO_EXTS.slice();
  return [...IMAGE_EXTS, ...VIDEO_EXTS];
}

/**
 * Query string of GET /api/library; empty filters are left out.
 * @param {{ q?: string, kind?: string, source?: string, limit?: number, offset?: number }} params
 */
export function libraryQuery(params) {
  const qs = new URLSearchParams();
  const q = String(params.q || '').trim();
  if (q) qs.set('q', q);
  if (params.kind) qs.set('kind', params.kind);
  if (params.source) qs.set('source', params.source);
  qs.set('limit', String(params.limit || LIBRARY_PAGE_SIZE));
  qs.set('offset', String(params.offset || 0));
  return qs.toString();
}

/**
 * Keywords tidied like the server does: inner spaces collapsed, trimmed, cut to the length limit,
 * empty ones and repeats (ignoring case) dropped, at most LIBRARY_LIMITS.keywords.
 * @param {unknown} list
 * @returns {string[]}
 */
export function normalizeKeywords(list) {
  /** @type {string[]} */
  const out = [];
  const seen = new Set();
  for (const raw of Array.isArray(list) ? list : []) {
    const word = String(raw ?? '').replace(/\s+/g, ' ').trim().slice(0, LIBRARY_LIMITS.keyword).trim();
    if (!word) continue;
    const key = word.toLocaleLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(word);
    if (out.length >= LIBRARY_LIMITS.keywords) break;
  }
  return out;
}

/**
 * What is wrong with a keyword the teacher typed, or null.
 * @param {string} word
 * @param {string[]} current  the keywords already there
 * @returns {string | null}
 */
export function keywordProblem(word, current) {
  const w = String(word || '').replace(/\s+/g, ' ').trim();
  if (w.length > LIBRARY_LIMITS.keyword) return `A keyword can be at most ${LIBRARY_LIMITS.keyword} characters long.`;
  if (current.some((k) => k.toLocaleLowerCase() === w.toLocaleLowerCase())) return `“${w}” is already a keyword.`;
  if (current.length >= LIBRARY_LIMITS.keywords) return `Use at most ${LIBRARY_LIMITS.keywords} keywords.`;
  return null;
}

/**
 * Check a file before it is uploaded (the server checks it again).
 * @param {{ name: string, size: number, type?: string }} file
 * @param {number} maxMb
 * @param {string} [kind]  'image' | 'video': only that kind is accepted
 * @returns {string | null}
 */
export function validateLibraryFile(file, maxMb, kind) {
  if (kind === 'video') {
    const name = String((file && file.name) || '').toLowerCase();
    const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
    if (!VIDEO_EXTS.includes(ext)) return `Upload a video (${VIDEO_EXTS.join(', ')}).`;
  }
  return validateMediaFile(file, kind === 'image' ? 'figure' : 'scene_media', maxMb);
}

/**
 * The title shown for an item (an untitled one is named after its kind).
 * @param {{ title?: string | null, kind?: string }} item
 */
export function itemTitle(item) {
  const title = String((item && item.title) || '').trim();
  if (title) return title;
  return item && item.kind === 'video' ? 'Untitled video' : 'Untitled picture';
}

/**
 * "Added to 2 lectures" / "Not added to a lecture yet": the lectures the item was ever added to or made for
 * (`used_in`; a lecture whose scene later shows other media still counts).
 * @param {number | null | undefined} count
 */
export function usedInText(count) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  return n > 0 ? `Added to ${plural(n, 'lecture')}` : 'Not added to a lecture yet';
}

/**
 * Size and length in words, e.g. "1920 × 1080 · 0:12" ('' when unknown).
 * @param {{ kind?: string, width?: number | null, height?: number | null, duration_s?: number | null }} item
 */
export function itemFacts(item) {
  /** @type {string[]} */
  const parts = [];
  if (item.width && item.height) parts.push(`${item.width} × ${item.height}`);
  const seconds = Number(item.duration_s);
  if (item.kind === 'video' && Number.isFinite(seconds) && seconds > 0) parts.push(formatDuration(seconds));
  return parts.join(' · ');
}

/**
 * Small picture of an item: the image itself, a video's poster, or a placeholder. Decorative
 * (alt=""): the card or button around it names the item.
 * @param {LibraryItem} item
 * @returns {HTMLElement}
 */
export function libraryThumb(item) {
  const video = item.kind === 'video';
  const placeholder = () => h('span', { class: 'library-thumb-empty' }, icon(video ? 'video' : 'file', { size: 28 }));
  const src = video ? item.poster_url : item.url;
  /** @type {HTMLElement} */
  let media = placeholder();
  if (src) {
    const img = h('img', { class: 'library-thumb-img', src, alt: '', loading: 'lazy', decoding: 'async' });
    // An expired link or a missing file shows the placeholder instead of a broken image.
    img.addEventListener('error', () => img.replaceWith(placeholder()), { once: true });
    media = img;
  }
  const seconds = Number(item.duration_s);
  return h(
    'span',
    { class: ['library-thumb', video ? 'library-thumb-video' : 'library-thumb-image'] },
    media,
    video ? h('span', { class: 'library-thumb-badge' }, icon('play', { size: 12 }), seconds > 0 ? formatDuration(seconds) : 'Video') : null,
  );
}

/**
 * Kind and source of an item as text badges.
 * @param {LibraryItem} item
 */
export function itemBadges(item) {
  return h(
    'span',
    { class: 'badges' },
    h('span', { class: 'badge badge-outline' }, KIND_LABELS[item.kind] || 'Media'),
    h('span', { class: ['badge', item.source === 'generated' ? 'badge-busy' : 'badge-muted'] }, SOURCE_LABELS[item.source] || 'Media'),
  );
}

/**
 * Read-only keyword chips (the first `max`, then "+N").
 * @param {unknown} keywords
 * @param {number} [max]
 * @returns {HTMLElement | null}
 */
export function keywordChips(keywords, max = 6) {
  const list = normalizeKeywords(keywords);
  if (!list.length) return null;
  const shown = list.slice(0, max);
  return h(
    'ul',
    { class: 'chips library-keywords', 'aria-label': 'Keywords' },
    shown.map((k) => h('li', { class: 'chip' }, h('span', { class: 'chip-text' }, k))),
    list.length > shown.length ? h('li', { class: 'chip chip-more', title: list.slice(max).join(', ') }, `+${list.length - shown.length}`) : null,
  );
}

/**
 * One item as a card: picture, title, badges, size, description, keywords, use count, actions.
 * @param {LibraryItem} item
 * @param {{ onPreview?: (item: LibraryItem) => void, actions?: HTMLElement[], heading?: 'h2' | 'h3' | 'h4' }} [opts]
 * @returns {HTMLElement}
 */
export function libraryCard(item, opts = {}) {
  const title = itemTitle(item);
  const onPreview = opts.onPreview;
  const thumb = onPreview
    ? h('button', { type: 'button', class: 'library-thumb-button', 'aria-label': `Preview: ${title}`, title: 'Preview', onClick: () => onPreview(item) }, libraryThumb(item))
    : libraryThumb(item);
  const facts = itemFacts(item);
  const description = String(item.description || '').trim();
  return h(
    'article',
    { class: 'card library-card', dataset: { itemId: String(item.id) } },
    thumb,
    h(opts.heading || 'h2', { class: 'card-title library-title' }, title),
    h('div', { class: 'row gap wrap' }, itemBadges(item), facts ? h('span', { class: 'card-meta' }, facts) : null),
    description ? h('p', { class: 'library-desc' }, description) : null,
    keywordChips(item.keywords),
    h('p', { class: 'card-foot library-used' }, usedInText(item.used_in)),
    opts.actions && opts.actions.length ? h('div', { class: 'card-actions row gap wrap' }, opts.actions) : null,
  );
}

/**
 * A larger look at one item, with its details. Resolves 'edit' when the teacher asks to edit it
 * (only offered with `canEdit`), else null.
 * @param {LibraryItem} item
 * @param {{ canEdit?: boolean }} [opts]
 * @returns {Promise<'edit' | null>}
 */
export async function openLibraryPreview(item, opts = {}) {
  const title = itemTitle(item);
  const media =
    item.kind === 'video'
      ? h('video', { class: 'library-preview-media', src: item.url, poster: item.poster_url || undefined, controls: true, preload: 'metadata', playsinline: true })
      : h('img', { class: 'library-preview-media', src: item.url, alt: String(item.description || '').trim() || title });
  const madeWith = [item.provider, item.model].filter(Boolean).join(' · ');
  /** @type {Array<[string, string]>} */
  const facts = [
    ['Kind', KIND_LABELS[item.kind] || 'Media'],
    ['Source', SOURCE_LABELS[item.source] || 'Media'],
  ];
  const size = itemFacts(item);
  if (size) facts.push(['Size', size]);
  if (madeWith) facts.push(['Made with', madeWith]);
  facts.push(['Added', formatDate(item.created_at)], ['Last used', item.last_used_at ? formatDate(item.last_used_at) : 'Not yet'], ['Lectures', usedInText(item.used_in)]);
  const description = String(item.description || '').trim();
  const prompt = String(item.prompt || '').trim();
  // Focus starts on the media's frame (Tab then reaches a video's controls), so a tall dialog opens at
  // its top rather than scrolled down to its buttons.
  const frame = h('div', { class: 'library-preview-frame', tabindex: '-1' }, media);
  const modal = openModal({
    title,
    size: 'lg',
    initialFocus: frame,
    body: h(
      'div',
      { class: 'library-preview' },
      frame,
      description ? h('p', {}, description) : null,
      keywordChips(item.keywords, LIBRARY_LIMITS.keywords),
      h('dl', { class: 'library-facts' }, facts.map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)])),
      prompt ? h('div', { class: 'library-prompt' }, h('div', { class: 'field-label' }, 'Made by AI from this description'), h('p', {}, prompt)) : null,
    ),
    actions: [
      { label: 'Close', kind: 'outline', value: null, autofocus: !opts.canEdit },
      ...(opts.canEdit ? [{ label: 'Edit details', kind: /** @type {const} */ ('gold'), value: 'edit', autofocus: true }] : []),
    ],
  });
  const v = await modal.result;
  return v === 'edit' ? 'edit' : null;
}

/**
 * Upload with progress through XMLHttpRequest (fetch cannot report upload progress), with the same
 * CSRF header and error envelope as the shared API client.
 * @param {string} path
 * @param {FormData} form
 * @param {{ onProgress?: (fraction: number) => void, signal?: AbortSignal }} opts
 * @returns {Promise<any>}
 */
function xhrUpload(path, form, opts) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const aborted = () => reject(Object.assign(new Error('Upload cancelled.'), { name: 'AbortError' }));
    if (opts.signal && opts.signal.aborted) {
      aborted();
      return;
    }
    xhr.open('POST', path);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader(CSRF_HEADER, '1');
    const onProgress = opts.onProgress;
    if (onProgress && xhr.upload) {
      xhr.upload.addEventListener('progress', (ev) => {
        if (ev.lengthComputable && ev.total > 0) onProgress(Math.min(1, ev.loaded / ev.total));
      });
    }
    xhr.addEventListener('load', () => {
      const text = xhr.responseText || '';
      /** @type {any} */
      let body = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        body = xhr.status >= 200 && xhr.status < 300 ? text : { detail: xhr.statusText };
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
        return;
      }
      const ra = xhr.getResponseHeader('Retry-After');
      const err = new ApiError(xhr.status, body, ra ? Number(ra) : null);
      // The shared client signs the user out on 401; a request through it lets that happen here too.
      if (xhr.status === 401) void get('/api/auth/me').catch(() => {});
      reject(err);
    });
    xhr.addEventListener('error', () => reject(new TypeError('Network error')));
    xhr.addEventListener('abort', aborted);
    if (opts.signal) opts.signal.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

/**
 * Add a file to the library (POST /api/library, multipart). Reports upload progress (0..1) where the
 * browser can; otherwise the request goes through the shared API client.
 * @param {File} file
 * @param {{ title?: string, description?: string, keywords?: string[] }} [details]
 * @param {{ onProgress?: (fraction: number) => void, signal?: AbortSignal }} [opts]
 * @returns {Promise<LibraryItem>}
 */
export function uploadToLibrary(file, details = {}, opts = {}) {
  const form = new FormData();
  form.append('file', file);
  if (details.title) form.append('title', details.title);
  if (details.description) form.append('description', details.description);
  const keywords = normalizeKeywords(details.keywords);
  if (keywords.length) form.append('keywords', keywords.join(','));
  if (typeof XMLHttpRequest === 'undefined') return api('/api/library', { method: 'POST', form, signal: opts.signal });
  return xhrUpload('/api/library', form, opts);
}

/**
 * The upload limit in MB from /api/meta (DEFAULT_MAX_MB when it cannot be read).
 * @param {AppContext | null | undefined} app
 * @returns {Promise<number>}
 */
export async function uploadLimitMb(app) {
  try {
    const meta = app ? await app.meta() : null;
    const mb = Number(meta && meta.limits && meta.limits.upload_max_mb);
    return mb > 0 ? mb : DEFAULT_MAX_MB;
  } catch {
    return DEFAULT_MAX_MB;
  }
}

/**
 * Let the teacher choose an item of their library.
 * `sources` (optional) offers only items of those sources, for a field that accepts only some of
 * them (a lecture figure takes uploads and document figures, not AI pictures). They are sent to the
 * server as `source` (comma-separated), and checked here again.
 * `suggested` (optional): items that fit the place the item is for (GET /api/library/suggestions), shown
 * first under "Best matches" while nothing is searched.
 * @param {{ app?: AppContext | null, kind?: 'image' | 'video' | null, projectId?: number | null, title?: string,
 *   sources?: Array<'upload' | 'generated' | 'figure'> | null, suggested?: LibraryItem[] | null }} opts
 * @returns {Promise<LibraryItem | null>}  the chosen item; null when the dialog is closed
 */
export function openLibraryPicker(opts) {
  const kind = opts.kind === 'image' || opts.kind === 'video' ? opts.kind : '';
  const projectId = opts.projectId && opts.projectId > 0 ? opts.projectId : null;
  const sources = Array.isArray(opts.sources) ? opts.sources.filter((s) => s in SOURCE_LABELS) : [];
  const serverSource = sources.join(',');
  /** @param {LibraryItem} it */
  const usable = (it) => !!it && (!kind || it.kind === kind) && (!sources.length || sources.includes(it.source));
  const suggested = (Array.isArray(opts.suggested) ? opts.suggested : []).filter((it) => usable(it) && Number.isInteger(it.id));
  const suggestedIds = new Set(suggested.map((it) => it.id));
  let q = '';
  let kindFilter = kind;
  /** @type {LibraryItem[]} */
  let items = [];
  let total = 0;
  /** Rows the server returned so far (the next page's offset). */
  let fetched = 0;
  let token = 0;
  let closed = false;
  let busy = false;
  let maxMb = DEFAULT_MAX_MB;
  void uploadLimitMb(opts.app).then((mb) => {
    maxMb = mb;
  });
  const uploads = new AbortController();

  const search = input({ type: 'search', placeholder: 'Search titles, descriptions and keywords', ariaLabel: 'Search your library', maxLength: 120 });
  const kindSelect = kind
    ? null
    : select({
        options: KIND_FILTERS.map((o) => ({ ...o })),
        value: '',
        ariaLabel: 'Show',
        className: 'library-kind-select',
        onChange: (v) => {
          kindFilter = v === 'image' || v === 'video' ? v : '';
          void load(false);
        },
      });
  // A short count is announced, never the whole grid (each card has a title, words and buttons).
  const countLine = h('p', { class: 'sr-only', role: 'status', 'aria-live': 'polite' });
  const bestHost = h('div', { class: 'library-suggested-host' });
  const grid = h('div', { class: 'card-grid library-grid', 'aria-busy': 'false' });
  const footer = h('div', { class: 'list-footer' });
  const uploadLabel = kind === 'video' ? 'Upload a video' : kind === 'image' ? 'Upload a picture' : 'Upload a file';
  const uploadBtn = button(uploadLabel, { kind: 'outline', icon: 'upload' });
  uploadBtn.hidden = sources.length > 0 && !sources.includes('upload'); // an upload would be of a source the field refuses
  const fileInput = h('input', { type: 'file', class: 'sr-only', accept: acceptedExtensions(kind).join(','), tabindex: '-1', 'aria-hidden': 'true' });
  const uploadState = h('div', { class: 'library-picker-upload', role: 'status', 'aria-live': 'polite' });
  const toolbar = h('div', { class: 'toolbar library-toolbar' }, h('label', { class: 'search-box' }, icon('search'), search), kindSelect, uploadBtn, fileInput);

  const dialogTitle =
    opts.title || (kind === 'video' ? 'Choose a video from your library' : kind === 'image' ? 'Choose a picture from your library' : 'Choose from your library');
  const modal = openModal({
    title: dialogTitle,
    size: 'xl',
    body: h('div', { class: 'library-picker' }, toolbar, uploadState, countLine, bestHost, grid, footer),
    initialFocus: search,
    actions: [{ label: 'Cancel', kind: 'outline', value: null }],
    onClose: () => {
      closed = true;
      token += 1;
      onSearch.cancel();
      uploads.abort();
    },
  });

  /** @param {boolean} value */
  const setBusy = (value) => {
    busy = value;
    modal.setBusy(value);
    uploadBtn.disabled = value;
    for (const b of /** @type {HTMLButtonElement[]} */ (Array.from(grid.querySelectorAll('button')))) b.disabled = value;
    for (const b of /** @type {HTMLButtonElement[]} */ (Array.from(bestHost.querySelectorAll('button')))) b.disabled = value;
  };

  /**
   * Close the dialog with this item (after attaching it to the lecture, when one was given).
   * @param {LibraryItem} item
   */
  async function choose(item) {
    if (busy || closed) return;
    modal.setError(null);
    if (!projectId) {
      modal.close(item);
      return;
    }
    setBusy(true);
    try {
      const res = await post(`/api/library/${encodeURIComponent(String(item.id))}/attach`, { project_id: projectId });
      if (closed) return;
      setBusy(false);
      modal.close({ ...item, asset_key: res && typeof res.asset_key === 'string' && res.asset_key ? res.asset_key : item.asset_key });
    } catch (err) {
      if (closed) return;
      setBusy(false);
      modal.setError(
        err instanceof ApiError && err.status === 404
          ? 'This item could not be added to this lecture. Items from your library can be used only in your own lectures.'
          : errorMessage(err, 'Could not add it to this lecture.'),
      );
    }
  }

  /**
   * @param {LibraryItem} item
   * @param {number} [index]  position in the loaded list (focus after "Load more")
   */
  function card(item, index) {
    const title = itemTitle(item);
    const use = button('Use this', { kind: 'gold', small: true, icon: 'check', onClick: () => void choose(item) });
    use.setAttribute('aria-label', `Use this: ${title}`);
    use.dataset.action = 'use';
    if (index !== undefined) use.dataset.index = String(index);
    return libraryCard(item, { heading: suggestedIds.has(item.id) && index === undefined ? 'h4' : 'h3', actions: [use], onPreview: (it) => void openLibraryPreview(it) });
  }

  /** The suggested items that fit the current filter ("Best matches", shown while nothing is searched). */
  function best() {
    return q ? [] : suggested.filter((it) => !kindFilter || it.kind === kindFilter);
  }

  function drawBest() {
    clear(bestHost);
    const shown = best();
    if (!shown.length) return;
    bestHost.appendChild(
      h(
        'section',
        { class: 'library-suggested', 'aria-label': 'Best matches' },
        h('h3', { class: 'library-suggested-title' }, shown.length === 1 ? 'Best match' : 'Best matches'),
        h('div', { class: 'card-grid library-grid' }, shown.map((it) => card(it))),
      ),
    );
  }

  /** @param {number} [firstNew]  index of the first item of a page just added (focus moves there) */
  function render(firstNew) {
    clear(grid);
    clear(footer);
    drawBest();
    const loadMore = () => button('Load more', { kind: 'outline', onClick: () => void load(true) });
    if (!items.length) {
      const filtered = !!q || (!!kindFilter && !kind);
      const what = kind === 'video' ? 'videos' : kind === 'image' ? 'pictures' : '';
      const emptyTitle = sources.length
        ? `Nothing ${what ? `(${what}) ` : ''}you can use here yet`
        : what ? `No ${what} in your library yet` : 'Your library is empty';
      grid.appendChild(
        filtered
          ? emptyState('Nothing matches', q ? `Nothing in your library matches “${q}”.` : 'Nothing of this kind in your library yet.', button('Show everything', { kind: 'outline', onClick: () => resetFilters() }))
          : emptyState(
              emptyTitle,
              'Upload a file here. Files you upload, and the pictures and clips Aadhi makes for your lectures, are kept in your library so you can use them again.',
            ),
      );
      if (fetched < total) footer.append(loadMore());
      countLine.textContent = filtered ? 'Nothing matches.' : `${emptyTitle}.`;
      return;
    }
    // Items shown under "Best matches" are not repeated below.
    const above = new Set(best().map((it) => it.id));
    items.forEach((item, i) => {
      if (!above.has(item.id)) grid.appendChild(card(item, i));
    });
    footer.append(h('span', { class: 'muted' }, `Showing ${items.length} of ${Math.max(total, items.length)}`));
    if (fetched < total) footer.append(loadMore());
    countLine.textContent = `${plural(Math.max(total, items.length), 'item')} found.`;
    if (firstNew !== undefined) {
      const target = /** @type {HTMLElement | undefined} */ (
        [...grid.querySelectorAll('[data-action="use"]')].find((b) => Number(/** @type {HTMLElement} */ (b).dataset.index) >= firstNew)
      );
      if (target) target.focus();
    }
  }

  function resetFilters() {
    q = '';
    search.value = '';
    kindFilter = kind;
    if (kindSelect) kindSelect.value = '';
    void load(false);
    search.focus();
  }

  /** @param {boolean} append */
  async function load(append) {
    const mine = ++token;
    if (!append) {
      clear(footer);
      clear(grid);
      countLine.textContent = ''; // the same count is read again after a new search
      grid.appendChild(spinner('Loading your library…'));
    }
    grid.setAttribute('aria-busy', 'true');
    try {
      const res = await get(`/api/library?${libraryQuery({ q, kind: kindFilter, source: serverSource, offset: append ? fetched : 0 })}`);
      if (closed || mine !== token) return;
      const rows = res && Array.isArray(res.items) ? res.items : [];
      // The server filters by kind and sources already; this keeps a kind or a source the caller cannot use
      // out of a limited picker whatever it sends.
      const got = rows.filter((/** @type {LibraryItem} */ it) => usable(it));
      const before = append ? items.length : 0;
      items = append ? [...items, ...got] : got;
      fetched = (append ? fetched : 0) + rows.length;
      total = Number(res && res.total) || 0;
      render(append && got.length ? before : undefined);
    } catch (err) {
      if (closed || mine !== token) return;
      clear(grid);
      clear(footer);
      countLine.textContent = ''; // the error state announces itself
      grid.appendChild(errorState(errorMessage(err, 'Could not load your library.'), () => void load(false)));
    } finally {
      if (mine === token) grid.setAttribute('aria-busy', 'false');
    }
  }

  /** @param {File} file */
  async function upload(file) {
    if (busy || closed) return;
    clear(uploadState);
    const problem = validateLibraryFile(file, maxMb, kind || undefined);
    if (problem) {
      uploadState.append(h('p', { class: 'field-error' }, `${file.name}: ${problem}`));
      return;
    }
    const bar = progressBar(0, `Uploading ${file.name}`);
    const fill = /** @type {HTMLElement} */ (bar.querySelector('.progress-fill'));
    const label = h('span', { class: 'small muted' }, `Uploading ${file.name}…`);
    uploadState.append(h('div', { class: 'library-upload-progress' }, label, bar));
    setBusy(true);
    try {
      const item = await uploadToLibrary(file, {}, {
        signal: uploads.signal,
        onProgress: (f) => {
          const pct = Math.round(f * 100);
          fill.style.width = `${pct}%`;
          bar.setAttribute('aria-valuenow', String(pct));
        },
      });
      if (closed) return;
      setBusy(false);
      clear(uploadState);
      if (kind && item && item.kind !== kind) {
        uploadState.append(h('p', { class: 'field-error' }, `${file.name} was added to your library, but it is not a ${kind === 'video' ? 'video' : 'picture'}.`));
        void load(false);
        return;
      }
      await choose(item);
    } catch (err) {
      if (closed) return;
      setBusy(false);
      clear(uploadState);
      uploadState.append(h('p', { class: 'field-error' }, `${file.name}: ${errorMessage(err, 'Upload failed.')}`));
    }
  }

  const onSearch = debounce(() => {
    const next = search.value.trim();
    if (next === q) return;
    q = next;
    void load(false);
  }, 300);
  search.addEventListener('input', onSearch);
  search.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      onSearch.flush();
    }
  });
  uploadBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (file) void upload(file);
  });

  void load(false);
  return modal.result.then((v) => (v && typeof v === 'object' ? /** @type {LibraryItem} */ (v) : null));
}
