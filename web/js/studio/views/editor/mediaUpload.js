// @ts-check
/**
 * Override-media control: upload an image/video through POST /api/uploads (multipart:
 * file, purpose, project_id) and set the returned asset key on the screenplay; shows the
 * current override and lets the teacher remove it. With `pickLibrary`, "Choose from library" reuses
 * an item of the teacher's library instead (the picker attaches it to the lecture and returns its key),
 * limited to pictures or videos when the control accepts only one of them.
 */

import { h, clear, append } from '../../../shared/dom.js';
import { button } from '../../components/form.js';
import { icon } from '../../components/icons.js';
import { validateMediaFile, MEDIA_RULES } from '../../lib/optionsForm.js';
import { uploadForm } from '../common.js';
import { errorMessage } from '../../errors.js';

/**
 * @typedef {{ asset_key: string, url?: string, mime?: string, kind?: string, width?: number, height?: number, duration_s?: number }} UploadResult
 */

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif']);
const VIDEO_EXTS = new Set(['.mp4', '.webm']);

/**
 * The library kind a control accepting `exts` can use (undefined: pictures and videos).
 * @param {string[]} exts
 * @returns {'image' | 'video' | undefined}
 */
export function libraryKindOf(exts) {
  if (exts.length && exts.every((e) => IMAGE_EXTS.has(e))) return 'image';
  if (exts.length && exts.every((e) => VIDEO_EXTS.has(e))) return 'video';
  return undefined;
}

/**
 * @param {{ purpose: keyof typeof MEDIA_RULES, projectId: number, assetKey: string | null, label: string, maxMb: number,
 *   hint?: string, onChange: (assetKey: string | null, info: UploadResult | null) => void, accept?: string[],
 *   pickLibrary?: (kind?: 'image' | 'video') => Promise<any> }} opts
 * @returns {HTMLElement}
 */
export function mediaUpload(opts) {
  const rule = MEDIA_RULES[opts.purpose];
  const exts = opts.accept || rule.exts;
  const libraryKind = libraryKindOf(exts);
  const fileInput = h('input', { type: 'file', class: 'sr-only', accept: exts.join(','), tabindex: '-1', 'aria-hidden': 'true' });
  const status = h('div', { class: 'upload-status', role: 'status', 'aria-live': 'polite' });
  const error = h('div', { class: 'field-error', role: 'alert', hidden: true });
  const current = h('div', { class: 'upload-current' });
  const choose = button(opts.assetKey ? 'Replace file' : 'Upload file', { kind: 'outline', small: true, icon: 'upload' });
  /** @type {string | null} */
  let key = opts.assetKey;
  /** @type {UploadResult | null} */
  let info = null;

  const renderCurrent = () => {
    clear(current);
    choose.querySelector('.btn-label')?.replaceChildren(key ? 'Replace file' : 'Upload file');
    if (!key) {
      current.appendChild(h('span', { class: 'muted' }, 'Using generated media.'));
      return;
    }
    const remove = button('Remove custom media', { kind: 'ghost', small: true, icon: 'close' });
    remove.addEventListener('click', () => {
      key = null;
      info = null;
      renderCurrent();
      opts.onChange(null, null);
      choose.focus();
    });
    let preview = null;
    if (info && info.url) {
      preview = info.kind === 'video' || (info.mime || '').startsWith('video/')
        ? h('video', { class: 'upload-preview', src: info.url, controls: true, muted: true, preload: 'metadata' })
        : h('img', { class: 'upload-preview', src: info.url, alt: 'Uploaded media preview' });
    }
    append(current, [h('div', { class: 'row gap wrap' }, icon('file'), h('code', { class: 'small' }, key), remove), preview]);
  };

  choose.addEventListener('click', () => fileInput.click());
  const pickBtn = opts.pickLibrary ? button('Choose from library', { kind: 'outline', small: true, icon: 'search', dataset: { action: 'library' } }) : null;
  if (pickBtn) {
    pickBtn.addEventListener('click', async () => {
      if (!opts.pickLibrary) return;
      error.hidden = true;
      /** @type {any} */
      let item = null;
      try {
        item = await opts.pickLibrary(libraryKind);
      } catch (err) {
        error.textContent = errorMessage(err, 'The library could not be opened.');
        error.hidden = false;
        return;
      }
      if (!item || typeof item.asset_key !== 'string' || !item.asset_key) return;
      if (libraryKind && item.kind && item.kind !== libraryKind) {
        error.textContent = libraryKind === 'video' ? 'Choose a video here.' : 'Choose a picture here.';
        error.hidden = false;
        return;
      }
      key = item.asset_key;
      /** @type {UploadResult} */
      const chosen = { asset_key: item.asset_key, url: item.url, kind: item.kind };
      if (typeof item.mime === 'string') chosen.mime = item.mime;
      if (typeof item.width === 'number') chosen.width = item.width;
      if (typeof item.height === 'number') chosen.height = item.height;
      if (typeof item.duration_s === 'number') chosen.duration_s = item.duration_s;
      info = chosen;
      status.textContent = item.title ? `Using “${item.title}” from your library.` : 'Using an item from your library.';
      renderCurrent();
      opts.onChange(key, chosen);
    });
  }
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    error.hidden = true;
    const problem = validateMediaFile(file, opts.purpose, opts.maxMb);
    if (problem) {
      error.textContent = problem;
      error.hidden = false;
      return;
    }
    choose.disabled = true;
    if (pickBtn) pickBtn.disabled = true;
    status.textContent = `Uploading ${file.name}…`;
    try {
      /** @type {UploadResult} */
      const res = await uploadForm('/api/uploads', { file, purpose: String(opts.purpose), project_id: String(opts.projectId) });
      key = res.asset_key;
      info = res;
      status.textContent = 'Uploaded.';
      renderCurrent();
      opts.onChange(key, res);
    } catch (err) {
      status.textContent = '';
      error.textContent = errorMessage(err, 'Upload failed.');
      error.hidden = false;
    } finally {
      choose.disabled = false;
      if (pickBtn) pickBtn.disabled = false;
    }
  });
  renderCurrent();
  return h(
    'div',
    { class: 'media-upload' },
    h('div', { class: 'field-label' }, opts.label),
    opts.hint ? h('div', { class: 'field-hint' }, opts.hint) : null,
    current,
    h('div', { class: 'row gap wrap' }, choose, pickBtn, fileInput),
    status,
    error,
  );
}
