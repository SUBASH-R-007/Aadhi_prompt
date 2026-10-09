// @ts-check
/**
 * Library page (#/library): the teacher's own pictures and video clips (their uploads, the media
 * Aadhi made for their lectures and the figures from their documents), to find and reuse.
 *
 *  - search (debounced) and kind / source filters, kept in the URL (?q=&kind=&source=);
 *  - cards with the picture or a video's poster, title, badges, keywords and how many lectures use it;
 *  - upload by button or by dropping files anywhere on the page, one file after the other, with
 *    progress and the reason a file was refused;
 *  - preview, edit details (title, description, keywords; "Suggest with AI" only when /api/meta
 *    `library.ai_describe_enabled` is on) and remove (the lectures that use an item keep working).
 *
 * GET / POST /api/library, PATCH / DELETE /api/library/{id}, POST /api/library/{id}/describe.
 * Server strings are always set as text, never as HTML.
 */

import { h, clear } from '../../shared/dom.js';
import { get, post, patch, del, ApiError } from '../../shared/api.js';
import { button, input, textarea, select, field, group, spinner, emptyState, errorState } from '../components/form.js';
import { openModal, confirmDialog } from '../components/modal.js';
import { chipsInput } from '../components/chips.js';
import { progressBar } from '../components/badges.js';
import { icon } from '../components/icons.js';
import { href } from '../router.js';
import { debounce, formatBytes, plural } from '../util.js';
import { errorMessage, isAuthError } from '../errors.js';
import { pageHeader } from './common.js';
import {
  LIBRARY_LIMITS,
  KIND_FILTERS,
  acceptedExtensions,
  libraryQuery,
  normalizeKeywords,
  keywordProblem,
  validateLibraryFile,
  itemTitle,
  usedInText,
  libraryCard,
  libraryThumb,
  openLibraryPreview,
  uploadToLibrary,
} from '../components/libraryPicker.js';

/** @typedef {import('../types.js').LibraryItem} LibraryItem */

/** Files one drop or one "Choose files" may add (they are uploaded one after the other). */
export const MAX_UPLOAD_BATCH = 20;

const SOURCE_FILTERS = Object.freeze([
  { value: '', label: 'All sources' },
  { value: 'upload', label: 'Uploaded' },
  { value: 'generated', label: 'Made by AI' },
  { value: 'figure', label: 'From a document' },
]);
const KINDS = new Set(['image', 'video']);
const SOURCES = new Set(['upload', 'generated', 'figure']);

/**
 * "Suggest with AI" is offered only when /api/meta says it can be used (`library.ai_describe_enabled`:
 * switched on by the server and an AI engine configured for this user).
 * @param {any} meta
 */
export function aiDescribeEnabled(meta) {
  return !!(meta && meta.library && meta.library.ai_describe_enabled === true);
}

/**
 * Words for the end of an upload batch.
 * @param {number} added
 * @param {number} failed
 */
export function uploadSummaryText(added, failed) {
  if (added && failed) return `${plural(added, 'file')} added to your library. ${plural(failed, 'file')} could not be added: see the list for why.`;
  if (added) return `${plural(added, 'file')} added to your library.`;
  if (failed) return `${failed === 1 ? 'The file' : 'The files'} could not be added: see the list for why.`;
  return '';
}

/** @type {import('../types.js').ViewMount} */
export async function mount(container, { app, query, signal }) {
  /** @type {any} */
  let meta = null;
  try {
    meta = await app.meta();
  } catch {
    meta = null; // the page works without it: default upload limit, no AI suggestions
  }
  if (signal && signal.aborted) return;
  const maxMb = Number(meta && meta.limits && meta.limits.upload_max_mb) > 0 ? Number(meta.limits.upload_max_mb) : 50;
  const aiDescribe = aiDescribeEnabled(meta);

  let q = String(query.q || '').trim().slice(0, 120);
  let kind = KINDS.has(query.kind) ? query.kind : '';
  let source = SOURCES.has(query.source) ? query.source : '';
  /** @type {LibraryItem[]} */
  let items = [];
  let total = 0;
  /** Rows the server returned so far (the next page's offset). */
  let fetched = 0;
  let loadToken = 0;
  let destroyed = false;
  let uploading = false;
  /** @type {AbortController | null} */
  let uploadAbort = null;
  /** Card element per item id. @type {Map<number, HTMLElement>} */
  const cards = new Map();

  // --- page structure -----------------------------------------------------------------------
  const fileInput = h('input', { type: 'file', class: 'sr-only', accept: acceptedExtensions().join(','), multiple: true, tabindex: '-1', 'aria-hidden': 'true' });
  const chooseBtn = button('Choose files', { kind: 'gold', icon: 'upload' });
  const drop = h(
    'div',
    { class: 'library-drop' },
    icon('upload', { size: 28 }),
    h(
      'div',
      { class: 'library-drop-text' },
      h('p', { class: 'library-drop-title' }, 'Add pictures or videos: choose files, or drop them anywhere on this page.'),
      h('p', { class: 'field-hint' }, `PNG, JPG, WebP, GIF, MP4 or WebM, up to ${maxMb} MB each.`),
    ),
    chooseBtn,
  );
  const uploadList = h('ul', { class: 'library-uploads', 'aria-label': 'Uploads' });
  const uploadSummary = h('p', { class: 'library-upload-summary', role: 'status', 'aria-live': 'polite' });
  const clearUploads = button('Clear this list', { kind: 'ghost', small: true, icon: 'close' });
  const uploadBox = h('div', { class: 'library-upload-box', hidden: true }, uploadList, h('div', { class: 'row gap wrap' }, uploadSummary, clearUploads));

  const search = input({ type: 'search', value: q, placeholder: 'Search titles, descriptions and keywords', ariaLabel: 'Search your library', maxLength: 120 });
  const kindSelect = select({ options: KIND_FILTERS.map((o) => ({ ...o })), value: kind, onChange: (v) => setFilters({ kind: v }) });
  const sourceSelect = select({ options: SOURCE_FILTERS.map((o) => ({ ...o })), value: source, onChange: (v) => setFilters({ source: v }) });
  const countLine = h('p', { class: 'sr-only', role: 'status', 'aria-live': 'polite' });
  const grid = h('div', { class: 'card-grid library-grid', 'aria-busy': 'false' });
  const footer = h('div', { class: 'list-footer' });

  container.append(
    pageHeader('Library', 'Pictures and video clips you can use again in any lecture: your uploads, the media Aadhi made for your lectures and the figures from your documents.'),
    drop,
    fileInput,
    uploadBox,
    h(
      'div',
      { class: 'toolbar library-toolbar' },
      h('label', { class: 'search-box' }, icon('search'), search),
      field('Show', kindSelect, { className: 'library-filter' }),
      field('Source', sourceSelect, { className: 'library-filter' }),
    ),
    countLine,
    grid,
    footer,
  );

  // --- list -----------------------------------------------------------------------------------
  /** @param {boolean} append */
  async function load(append) {
    const token = ++loadToken;
    if (!append) {
      clear(grid);
      clear(footer);
      cards.clear();
      grid.appendChild(spinner('Loading your library…'));
    }
    grid.setAttribute('aria-busy', 'true');
    try {
      const res = await get(`/api/library?${libraryQuery({ q, kind, source, offset: append ? fetched : 0 })}`);
      if (destroyed || token !== loadToken) return;
      const rows = res && Array.isArray(res.items) ? res.items.filter(Boolean) : [];
      const before = append ? items.length : 0;
      items = append ? [...items, ...rows] : rows;
      fetched = (append ? fetched : 0) + rows.length;
      total = Number(res && res.total) || 0;
      render(append && rows.length ? before : undefined);
    } catch (err) {
      if (destroyed || token !== loadToken) return;
      clear(grid);
      clear(footer);
      if (isAuthError(err)) return;
      grid.appendChild(errorState(errorMessage(err, 'Could not load your library.'), () => void load(false)));
      countLine.textContent = '';
    } finally {
      if (token === loadToken) grid.setAttribute('aria-busy', 'false');
    }
  }

  /** @param {number} [firstNew]  index of the first item of a page just added (focus moves there) */
  function render(firstNew) {
    clear(grid);
    cards.clear();
    if (!items.length) {
      clear(footer);
      const filtered = !!(q || kind || source);
      countLine.textContent = filtered ? 'Nothing matches.' : 'Your library is empty.';
      grid.appendChild(
        filtered
          ? emptyState(
              'Nothing matches',
              q ? `Nothing in your library matches “${q}” with these filters.` : 'Nothing in your library matches these filters.',
              button('Clear search and filters', { kind: 'outline', onClick: () => setFilters({ q: '', kind: '', source: '' }, true) }),
            )
          : emptyState(
              'Your library is empty',
              'Add a picture or a video above. The files you upload in the editor, and the pictures and clips Aadhi makes for your lectures, are kept here too, so you can use them again.',
            ),
      );
      return;
    }
    for (const item of items) {
      const el = card(item);
      cards.set(item.id, el);
      grid.appendChild(el);
    }
    renderFooter();
    countLine.textContent = `${plural(Math.max(total, items.length), 'item')} found.`;
    if (firstNew !== undefined) focusAction(/** @type {HTMLElement | null} */ (grid.children[firstNew] || null), 'edit');
  }

  function renderFooter() {
    clear(footer);
    if (!items.length) return;
    footer.append(h('span', { class: 'muted' }, `Showing ${items.length} of ${Math.max(total, items.length)}`));
    if (fetched < total) footer.append(button('Load more', { kind: 'outline', onClick: () => void load(true) }));
  }

  /** @param {LibraryItem} item */
  function card(item) {
    const title = itemTitle(item);
    const edit = button('Edit details', { kind: 'outline', small: true, onClick: () => void editItem(item) });
    edit.setAttribute('aria-label', `Edit details: ${title}`);
    edit.dataset.action = 'edit';
    const remove = button('Remove', { kind: 'ghost', small: true, icon: 'trash', onClick: () => void removeItem(item) });
    remove.setAttribute('aria-label', `Remove: ${title}`);
    remove.dataset.action = 'remove';
    return libraryCard(item, { onPreview: (it) => void preview(it), actions: [edit, remove] });
  }

  /**
   * @param {HTMLElement | null | undefined} el
   * @param {string} action
   */
  function focusAction(el, action) {
    const target = /** @type {HTMLElement | null} */ (el ? el.querySelector(`[data-action="${action}"]`) : null);
    if (target) target.focus();
  }

  /**
   * Put a changed item in the list and redraw its card.
   * @param {LibraryItem} updated
   * @returns {HTMLElement | null} the new card
   */
  function replaceItem(updated) {
    const i = items.findIndex((x) => x.id === updated.id);
    if (i < 0) return null;
    items[i] = updated;
    const fresh = card(updated);
    const old = cards.get(updated.id);
    cards.set(updated.id, fresh);
    if (old && old.isConnected) old.replaceWith(fresh);
    return fresh;
  }

  /**
   * @param {{ q?: string, kind?: string, source?: string }} next
   * @param {boolean} [focusSearch]
   */
  function setFilters(next, focusSearch = false) {
    if (next.q !== undefined) q = next.q.trim().slice(0, 120);
    if (next.kind !== undefined) kind = KINDS.has(next.kind) ? next.kind : '';
    if (next.source !== undefined) source = SOURCES.has(next.source) ? next.source : '';
    search.value = q;
    kindSelect.value = kind;
    sourceSelect.value = source;
    app.replaceHash(href('library', {}, { q, kind, source }));
    void load(false);
    if (focusSearch) search.focus();
  }

  const onSearch = debounce(() => {
    const next = search.value.trim();
    if (next !== q) setFilters({ q: next });
  }, 300);
  search.addEventListener('input', onSearch);
  search.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') onSearch.flush();
    if (ev.key === 'Escape' && search.value) {
      search.value = '';
      onSearch();
    }
  });

  // --- preview, edit, remove ------------------------------------------------------------------
  /** @param {LibraryItem} item */
  async function preview(item) {
    const choice = await openLibraryPreview(item, { canEdit: true });
    if (choice === 'edit' && !destroyed) await editItem(items.find((x) => x.id === item.id) || item);
  }

  /**
   * Edit an item's title, description and keywords.
   * @param {LibraryItem} item
   * @returns {Promise<LibraryItem | null>} the saved item (null when cancelled)
   */
  async function editItem(item) {
    let keywords = normalizeKeywords(item.keywords);
    let open = true;
    const titleInput = input({ value: String(item.title || ''), maxLength: LIBRARY_LIMITS.title });
    const titleField = field('Title', titleInput, { required: true });
    const desc = textarea({ value: String(item.description || ''), maxLength: LIBRARY_LIMITS.description, rows: 4 });
    const counter = h('div', { class: 'field-hint library-counter' });
    const countChars = () => {
      counter.textContent = `${desc.value.length} of ${LIBRARY_LIMITS.description} characters`;
    };
    countChars();
    desc.addEventListener('input', countChars);
    const descField = field('Description', desc, { hint: 'What it shows, in a sentence or two. Aadhi uses it to find this file for your lectures.' });
    descField.appendChild(counter);
    const chips = chipsInput({
      label: 'Keywords',
      values: keywords,
      maxItems: LIBRARY_LIMITS.keywords,
      maxLength: LIBRARY_LIMITS.keyword,
      separators: /[,;\r\n]+/,
      placeholder: 'Type a keyword, then press Enter',
      // One typed keyword gets a reason when it is refused; repeats in pasted lists are dropped quietly
      // (the chips field stops at the first refused part, so it must not refuse inside a list).
      validate: (v) => (/[,;\r\n]/.test(chips.input.value) ? null : keywordProblem(v, keywords)),
      onChange: (values) => {
        const tidy = normalizeKeywords(values);
        keywords = tidy;
        if (tidy.join('\n') !== values.join('\n')) chips.setValues(tidy);
      },
    });
    const keywordsGroup = group('Keywords', chips.el, {
      hint: `Up to ${LIBRARY_LIMITS.keywords} short words or phrases, such as “resistor” or “Ohm's law”. Press Enter or type a comma after each one.`,
    });

    /** @type {HTMLElement | null} */
    let aiBlock = null;
    if (aiDescribe) {
      const suggest = button('Suggest with AI', { kind: 'outline', small: true, icon: 'wand' });
      const undo = button('Undo the suggestion', { kind: 'ghost', small: true, icon: 'undo' });
      undo.hidden = true;
      const aiStatus = h('p', { class: 'field-hint library-ai-status', role: 'status', 'aria-live': 'polite' });
      /** @type {{ title: string, description: string, keywords: string[] } | null} */
      let before = null;
      suggest.addEventListener('click', async () => {
        const previous = { title: titleInput.value, description: desc.value, keywords: keywords.slice() };
        suggest.disabled = true;
        undo.hidden = true;
        aiStatus.textContent = 'Asking AI for suggestions…';
        try {
          const s = await post(`/api/library/${encodeURIComponent(String(item.id))}/describe`, {});
          if (!open) return;
          const sTitle = String((s && s.title) || '').replace(/\s+/g, ' ').trim().slice(0, LIBRARY_LIMITS.title);
          const sDescription = String((s && s.description) || '').trim().slice(0, LIBRARY_LIMITS.description);
          if (sTitle) titleInput.value = sTitle;
          if (sDescription) desc.value = sDescription;
          countChars();
          keywords = normalizeKeywords([...keywords, ...(s && Array.isArray(s.keywords) ? s.keywords : [])]);
          chips.setValues(keywords);
          before = previous;
          undo.hidden = false;
          aiStatus.textContent = 'Suggestions filled in. Check them and change what you like, then save.';
        } catch (err) {
          if (!open) return;
          aiStatus.textContent =
            err instanceof ApiError && err.code === 'feature_disabled'
              ? 'AI suggestions are turned off on this server.'
              : err instanceof ApiError && (err.code === 'describe_failed' || err.code === 'unavailable') && err.message
                ? err.message // the server's own plain reason (no AI engine, a key it refused, …)
                : errorMessage(err, 'Could not get suggestions. Try again later.');
        } finally {
          suggest.disabled = false;
        }
      });
      undo.addEventListener('click', () => {
        if (!before) return;
        titleInput.value = before.title;
        desc.value = before.description;
        countChars();
        keywords = before.keywords.slice();
        chips.setValues(keywords);
        before = null;
        undo.hidden = true;
        aiStatus.textContent = 'Your own words are back.';
        suggest.focus();
      });
      aiBlock = h(
        'div',
        { class: 'library-ai' },
        h('div', { class: 'row gap wrap' }, suggest, undo),
        h('p', { class: 'field-hint' }, 'AI suggests a title, a description and keywords; you can change them before you save. This uses AI and counts toward your daily AI budget.'),
        aiStatus,
      );
    }

    const prompt = String(item.prompt || '').trim();
    const save = async () => {
      // A keyword still being typed counts (the field adds it when it loses focus).
      if (chips.input.value.trim()) chips.input.dispatchEvent(new Event('blur'));
      const title = titleInput.value.replace(/\s+/g, ' ').trim();
      if (!title) {
        titleField.setError('Give it a title, so you can find it again.');
        titleInput.focus();
        return false;
      }
      titleField.setError(null);
      const description = desc.value.trim();
      /** @type {Record<string, any>} */
      const changes = {};
      if (title !== String(item.title || '')) changes.title = title;
      if (description !== String(item.description || '').trim()) changes.description = description;
      if (keywords.join('\n') !== normalizeKeywords(item.keywords).join('\n')) changes.keywords = keywords.slice();
      if (!Object.keys(changes).length) return item;
      try {
        return await patch(`/api/library/${encodeURIComponent(String(item.id))}`, changes);
      } catch (err) {
        throw new Error(errorMessage(err, 'Could not save the details.'));
      }
    };

    const modal = openModal({
      title: 'Edit details',
      size: 'lg',
      initialFocus: titleInput,
      onClose: () => {
        open = false;
      },
      body: h(
        'div',
        { class: 'library-edit' },
        h('div', { class: 'library-edit-media' }, libraryThumb(item)),
        h(
          'div',
          { class: 'library-edit-fields' },
          titleField,
          descField,
          keywordsGroup,
          aiBlock,
          prompt ? h('div', { class: 'library-prompt' }, h('div', { class: 'field-label' }, 'Made by AI from this description'), h('p', {}, prompt)) : null,
        ),
      ),
      actions: [
        { label: 'Cancel', kind: 'outline', value: null },
        { label: 'Save', kind: 'gold', onClick: save },
      ],
    });
    const saved = await modal.result;
    if (destroyed || !saved || typeof saved !== 'object') return null;
    const result = /** @type {LibraryItem} */ (saved);
    if (result !== item) {
      const el = replaceItem(result);
      app.toast('Details saved.', { kind: 'success' });
      focusAction(el, 'edit');
    }
    return result;
  }

  /** @param {LibraryItem} item */
  async function removeItem(item) {
    const title = itemTitle(item);
    const used = Math.max(0, Number(item.used_in) || 0);
    const ok = await confirmDialog({
      title: 'Remove from your library?',
      message: `“${title}” will no longer be listed in your library.`,
      details: [
        used ? `${usedInText(used)}.` : 'Not added to a lecture yet.',
        'Lectures that use it keep working; it is only taken off this list.',
      ],
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok || destroyed) return;
    try {
      await del(`/api/library/${encodeURIComponent(String(item.id))}`);
    } catch (err) {
      if (!destroyed) app.reportError(err, 'Could not remove it from your library.');
      return;
    }
    if (destroyed) return;
    const i = items.findIndex((x) => x.id === item.id);
    const el = cards.get(item.id);
    const next = /** @type {HTMLElement | null} */ (el ? el.nextElementSibling || el.previousElementSibling : null);
    if (i >= 0) items.splice(i, 1);
    cards.delete(item.id);
    total = Math.max(0, total - 1);
    fetched = Math.max(0, fetched - 1);
    if (el) el.remove();
    app.toast(`Removed “${title}” from your library.`, { kind: 'success' });
    if (!items.length) {
      if (total > 0) void load(false);
      else render();
      search.focus();
      return;
    }
    renderFooter();
    countLine.textContent = `${plural(Math.max(total, items.length), 'item')} found.`;
    if (next) focusAction(next, 'edit');
    else search.focus();
  }

  // --- uploads --------------------------------------------------------------------------------
  /** @param {File} file */
  function uploadRow(file) {
    const bar = progressBar(0, `Uploading ${file.name}`);
    const fill = /** @type {HTMLElement} */ (bar.querySelector('.progress-fill'));
    bar.hidden = true;
    const status = h('span', { class: 'library-upload-status small muted' }, 'Waiting…');
    const actions = h('span', { class: 'row gap' });
    const el = h(
      'li',
      { class: 'library-upload' },
      icon('file'),
      h('span', { class: 'library-upload-name' }, file.name),
      h('span', { class: 'muted small' }, formatBytes(file.size)),
      bar,
      status,
      actions,
    );
    return {
      el,
      file,
      start() {
        el.classList.add('is-active');
        bar.hidden = false;
        status.textContent = 'Uploading…';
      },
      /** @param {number} fraction */
      progress(fraction) {
        const pct = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
        fill.style.width = `${pct}%`;
        bar.setAttribute('aria-valuenow', String(pct));
        status.textContent = `Uploading… ${pct}%`;
      },
      /**
       * @param {LibraryItem} item
       * @param {boolean} known  it was in the library already
       */
      done(item, known) {
        el.classList.remove('is-active');
        el.classList.add('is-done');
        bar.hidden = true;
        status.textContent = known ? 'Already in your library.' : 'Added to your library.';
        const details = button('Add details', { kind: 'outline', small: true });
        details.setAttribute('aria-label', `Add details: ${file.name}`);
        details.addEventListener('click', () => void editItem(items.find((x) => x.id === item.id) || item));
        actions.append(details);
      },
      /** @param {string} message */
      fail(message) {
        el.classList.remove('is-active');
        el.classList.add('is-failed');
        bar.hidden = true;
        status.classList.remove('muted');
        status.classList.add('field-error');
        status.textContent = message;
      },
    };
  }

  /** @param {boolean} value */
  function setUploading(value) {
    uploading = value;
    chooseBtn.disabled = value;
    clearUploads.hidden = value;
    drop.classList.toggle('is-busy', value);
    if (value) {
      container.dataset.dirty = 'true'; // the browser asks before closing the tab
      app.setLeaveGuard(() =>
        confirmDialog({
          title: 'Stop uploading?',
          message: 'Some files are still uploading. If you leave this page now, the unfinished ones are not added to your library.',
          confirmLabel: 'Leave the page',
          cancelLabel: 'Stay',
          danger: true,
        }),
      );
    } else {
      delete container.dataset.dirty;
      app.setLeaveGuard(null);
    }
  }

  /** @param {ArrayLike<File> | null | undefined} list */
  async function uploadFiles(list) {
    const files = Array.from(list || []);
    if (!files.length) return;
    uploadBox.hidden = false;
    if (uploading) {
      uploadSummary.textContent = 'Wait until the current files are uploaded, then add more.';
      return;
    }
    if (files.length > MAX_UPLOAD_BATCH) {
      clear(uploadList);
      uploadSummary.textContent = `Add at most ${MAX_UPLOAD_BATCH} files at a time.`;
      return;
    }
    clear(uploadList);
    const rows = files.map((f) => uploadRow(f));
    for (const row of rows) uploadList.appendChild(row.el);
    let added = 0;
    let failed = 0;
    const todo = [];
    for (const row of rows) {
      const problem = validateLibraryFile(row.file, maxMb);
      if (problem) {
        row.fail(problem);
        failed += 1;
      } else {
        todo.push(row);
      }
    }
    if (todo.length) {
      const abort = new AbortController();
      uploadAbort = abort;
      setUploading(true);
      for (let i = 0; i < todo.length; i++) {
        const row = todo[i];
        uploadSummary.textContent = `Uploading ${i + 1} of ${todo.length}: ${row.file.name}`;
        row.start();
        try {
          const item = await uploadToLibrary(row.file, {}, { signal: abort.signal, onProgress: (f) => row.progress(f) });
          if (destroyed) return;
          row.done(item, items.some((x) => x.id === item.id));
          added += 1;
        } catch (err) {
          if (destroyed) return;
          if (isAuthError(err)) {
            failed += todo.length - i;
            row.fail('Your session has ended.');
            break;
          }
          row.fail(errorMessage(err, 'Upload failed.'));
          failed += 1;
        }
      }
      uploadAbort = null;
      setUploading(false);
    }
    uploadSummary.textContent = uploadSummaryText(added, failed);
    if (added) void load(false);
  }

  chooseBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    const files = fileInput.files ? Array.from(fileInput.files) : [];
    fileInput.value = '';
    void uploadFiles(files);
  });
  clearUploads.addEventListener('click', () => {
    clear(uploadList);
    uploadSummary.textContent = '';
    uploadBox.hidden = true;
    chooseBtn.focus();
  });

  // Drop files anywhere on the page; the drop area lights up while files are dragged over.
  let dragDepth = 0;
  /** @param {DragEvent} ev */
  const carriesFiles = (ev) => {
    const dt = ev.dataTransfer;
    if (!dt) return false;
    return !dt.types || Array.from(dt.types).includes('Files');
  };
  container.addEventListener('dragenter', (ev) => {
    if (!carriesFiles(ev)) return;
    ev.preventDefault();
    dragDepth += 1;
    drop.classList.add('dragover');
  });
  container.addEventListener('dragover', (ev) => {
    if (!carriesFiles(ev)) return;
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = uploading ? 'none' : 'copy';
  });
  container.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) drop.classList.remove('dragover');
  });
  container.addEventListener('drop', (ev) => {
    if (!carriesFiles(ev)) return;
    ev.preventDefault();
    dragDepth = 0;
    drop.classList.remove('dragover');
    void uploadFiles(ev.dataTransfer ? ev.dataTransfer.files : null);
  });

  void load(false);
  return {
    destroy() {
      destroyed = true;
      onSearch.cancel();
      if (uploadAbort) uploadAbort.abort();
      uploadAbort = null;
    },
  };
}
