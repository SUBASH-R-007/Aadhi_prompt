// @ts-check
/**
 * New lecture: start from an uploaded source document (drag & drop, client-side type/size checks) or
 * from pasted notes, set lecture metadata and GenerationOptions, POST /api/projects (multipart), then
 * follow the generate_lecture job live. Success opens the editor; plan review pauses link to the review.
 * Pasted notes become a plain-text file (control characters removed) sent through the same upload, so
 * the server's validation, size limit and ingest apply unchanged.
 * "Let me check how Aadhi read my document before planning" (form field review_source) pauses the job
 * after the source was read; this page then opens the source report to check and continue.
 * When the user leaves the page while the upload is still running, nothing is shown or
 * followed afterwards (no SSE stream, no navigation): a toast links to the new project.
 * Before the upload starts, leaving with a chosen file, pasted notes or entered details asks first (e.g.
 * the engine hint's link to the API keys page would otherwise discard the form).
 * "Try an example" imports the bundled, ready-written example lecture (EXAMPLE_URL, a v2 screenplay)
 * through POST /api/projects/import as "Example: Ohm's law": no AI writing or pictures, only its voice-over
 * and built-in visuals are built. This page then follows the build and opens the project page.
 */

import { h, clear } from '../../shared/dom.js';
import { get } from '../../shared/api.js';
import { button, field, input, textarea, checkbox, spinner, errorState } from '../components/form.js';
import { dropZone } from '../components/dropzone.js';
import { createTabs } from '../components/tabs.js';
import { jobProgress } from '../components/jobProgress.js';
import { confirmDialog } from '../components/modal.js';
import { validateSourceFile, SOURCE_EXTENSIONS } from '../lib/optionsForm.js';
import { href } from '../router.js';
import { createOptionsForm } from './optionsFields.js';
import { pageHeader, uploadForm, breadcrumbs, SOURCE_REVIEW_LABEL } from './common.js';

/** The bundled example lecture (a v2 screenplay the import endpoint accepts; web/examples/). */
export const EXAMPLE_URL = '/web/examples/ohms-law.json';
/** Title of the project the example becomes (clearly marked as an example in the project list). */
export const EXAMPLE_TITLE = "Example: Ohm's law";

/** Pasted notes: characters accepted at most (lower when the server reads less of a source, see `pasteLimit`). */
export const MAX_PASTE_CHARS = 200_000;
/** Pasted notes: fewer readable characters than this are refused (too little to build a lecture from). */
export const MIN_PASTE_CHARS = 200;

/**
 * Pasted text as the server accepts it: Word's line and paragraph breaks become newlines, other control
 * characters (refused by the strict text check) are removed.
 * @param {string} text
 */
export function cleanPasted(text) {
  return String(text || '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\u000b\u2028\u2029]/g, '\n')
    .replace(/[\u0000-\u0008\u000c\u000e-\u001f\u007f-\u009f]/g, '');
}

/**
 * A suggested title: the first line of the notes without Markdown heading marks or a "Title:" label.
 * @param {string} text
 */
export function firstLineTitle(text) {
  const line = String(text || '').split('\n').map((l) => l.trim()).find(Boolean) || '';
  return line.replace(/^#{1,6}\s+/, '').replace(/^title\s*[:\-–—]\s*/i, '').trim().slice(0, 255);
}

/**
 * File name for pasted notes ("Ohm's law" -> "ohms-law.txt").
 * @param {string} title
 */
export function pastedFileName(title) {
  const slug = String(title || '')
    .normalize('NFKD')
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
  return `${slug || 'pasted-notes'}.txt`;
}

/**
 * Characters the paste box accepts: MAX_PASTE_CHARS, or less when the server reads less of any source
 * (`/api/meta` `limits.max_source_chars`, MAX_SOURCE_CHARS).
 * @param {any} meta
 */
export function pasteLimit(meta) {
  const server = Number(meta && meta.limits && meta.limits.max_source_chars);
  return Number.isInteger(server) && server > 0 ? Math.min(MAX_PASTE_CHARS, server) : MAX_PASTE_CHARS;
}

/**
 * Why pasted notes cannot be sent (null when they can).
 * @param {string} text  cleaned text
 * @param {number} maxMb upload limit
 * @param {number} [maxChars] characters accepted (`pasteLimit`)
 */
export function pastedProblem(text, maxMb, maxChars = MAX_PASTE_CHARS) {
  const t = text.trim();
  if (!t) return 'Paste your notes first.';
  if (/^<(?:!doctype\s+html|html[\s>]|svg[\s>]|\?xml|script[\s>]|body[\s>]|head[\s>])/i.test(t)) {
    return 'This looks like a web page. Paste the text itself, or save the page as a file and upload it.';
  }
  if (t.startsWith('{')) {
    try {
      const parsed = JSON.parse(t);
      if (parsed && typeof parsed === 'object') return 'This looks like a lecture file (JSON). Use “Import lecture” on the Projects page instead.';
    } catch {
      /* not JSON: ordinary notes that start with a brace */
    }
  }
  if (t.replace(/\s+/g, '').length < MIN_PASTE_CHARS) return `Paste at least a paragraph or two (${MIN_PASTE_CHARS} characters or more) so there is something to teach.`;
  if (t.length > maxChars) return `The notes are longer than ${maxChars.toLocaleString('en-US')} characters. Upload them as a file, or split them into several lectures.`;
  if (new TextEncoder().encode(text).length > (Number(maxMb) || 50) * 1024 * 1024) return `The notes are larger than the ${maxMb} MB upload limit.`;
  return null;
}

/** @type {import('../types.js').ViewMount} */
export async function mount(container, { app, signal }) {
  container.append(breadcrumbs([{ label: 'Projects', hash: '#/projects' }, { label: 'New lecture' }]), pageHeader('New lecture', 'Upload or paste your notes; Aadhi plans, scripts and voices a lecture you can edit.'));
  const body = h('div', {}, spinner('Loading options…'));
  container.appendChild(body);
  /** @type {any} */
  let meta;
  try {
    meta = await app.meta();
  } catch (err) {
    if (signal && signal.aborted) return;
    clear(body);
    body.appendChild(errorState('Could not load generation options.', () => app.navigate('#/new', { replace: true })));
    app.reportError(err);
    return;
  }
  if (signal && signal.aborted) return;
  const user = app.user();
  const maxMb = (meta.limits && meta.limits.upload_max_mb) || 50;
  const maxChars = pasteLimit(meta);
  /** @type {File | null} */
  let file = null;
  /** @type {{ destroy: () => void } | null} */
  let progress = null;
  let submitting = false;
  let destroyed = false;

  const title = input({ maxLength: 255, placeholder: 'Defaults to the session title or file name' });
  const options = createOptionsForm(meta, { isAdmin: !!user && user.role === 'admin', includeMeta: true });
  const startValues = JSON.stringify(options.values());
  // No maxlength: a browser silently cuts pasted text at it, so longer notes would lose their end without a word.
  // The limit is enforced on submit (pastedProblem) with a message, and the counter says when it is passed.
  const notes = textarea({ rows: 12, placeholder: 'Paste your lecture notes here: headings, explanations, formulas, examples, questions…' });
  const notesCount = h('div', { class: 'field-hint', 'aria-live': 'polite' });
  const notesField = field('Your notes', notes, { hint: 'Plain text. Lines like “1. Introduction”, “CHAPTER 2” or Markdown “#” headings become sections. Aadhi reads it exactly like an uploaded text file.' });
  const reviewSource = checkbox({
    label: 'Let me check how Aadhi read my document before planning',
    hint: 'Generation pauses after reading: you see the outline, what was set aside and the concepts found, and can adjust them before anything is planned.',
  });
  /** Something the user would lose by leaving: a chosen file, pasted notes, a title or changed options. */
  const dirty = () =>
    !submitting && (!!file || !!notes.value.trim() || !!title.value.trim() || reviewSource.input.checked || JSON.stringify(options.values()) !== startValues);
  const leaveGuard = async () =>
    !dirty() || confirmDialog({ title: 'Discard this lecture?', message: 'The file or notes and the details you entered will be lost.', confirmLabel: 'Discard', danger: true });
  const drop = dropZone({
    accept: [...SOURCE_EXTENSIONS],
    label: 'Drop your PDF, Word, text or Markdown file here',
    hint: `Up to ${maxMb} MB. Scanned PDFs work too.`,
    validate: (f) => validateSourceFile(f, maxMb),
    onFile: (f) => {
      file = f;
      fileError.hidden = true;
      if (f && !title.value.trim()) title.placeholder = f.name.replace(/\.[^.]+$/, '');
    },
  });
  const fileError = h('div', { class: 'field-error', role: 'alert', hidden: true });
  const updateNotes = () => {
    const n = notes.value.length;
    notesCount.textContent = `${n.toLocaleString('en-US')} of ${maxChars.toLocaleString('en-US')} characters${n > maxChars ? ' (too long: upload it as a file or split it)' : ''}`;
    notesCount.classList.toggle('is-over', n > maxChars);
    notesField.setError(null);
    const suggested = firstLineTitle(notes.value);
    if (!title.value.trim()) title.placeholder = suggested || 'Defaults to the first line of your notes';
  };
  notes.addEventListener('input', updateNotes);
  updateNotes();
  const source = createTabs({
    label: 'How would you like to start?',
    tabs: [
      { key: 'file', label: 'Upload a file', render: () => h('div', { class: 'start-file' }, drop.el, fileError) },
      { key: 'paste', label: 'Paste your notes', render: () => h('div', { class: 'start-paste' }, notesField, notesCount) },
    ],
    onChange: (key) => {
      if (title.value.trim()) return;
      title.placeholder = key === 'paste' ? firstLineTitle(notes.value) || 'Defaults to the first line of your notes' : file ? file.name.replace(/\.[^.]+$/, '') : 'Defaults to the session title or file name';
    },
  });
  const status = h('div', { class: 'form-status', role: 'alert', hidden: true });
  const submit = button('Generate lecture', { kind: 'gold', type: 'submit', icon: 'wand' });
  const exampleBtn = button('Try the Ohm’s law example', { kind: 'outline', icon: 'play' });
  const exampleStatus = h('span', { class: 'muted small', role: 'status' });
  const example = h(
    'aside',
    { class: 'panel glass example-lesson', 'aria-label': 'Try an example' },
    h('h2', {}, 'Try an example'),
    h(
      'p',
      { class: 'muted' },
      'New to Aadhi? Start from a ready-written example: a short lecture on Ohm’s law in five scenes. Aadhi builds its voice-over and visuals in a few minutes; then you can watch it, change it in the editor and make the video.',
    ),
    h('div', { class: 'row gap wrap' }, exampleBtn, exampleStatus),
    h('p', { class: 'field-hint' }, `It is added to your projects as “${EXAMPLE_TITLE}”. No AI writing or pictures are needed; only the voice-over is made, with this server’s voice settings.`),
  );
  const progressHost = h('section', { class: 'progress-host', 'aria-label': 'Generation progress' });

  const form = h(
    'form',
    { class: 'new-project-form', novalidate: true },
    h(
      'section',
      { class: 'panel glass' },
      h('h2', {}, '1. Source document'),
      h('p', { class: 'muted' }, 'How would you like to start? Upload a document, or paste your notes.'),
      source.el,
      field('Project title', title, { hint: 'Shown in your project list and as the video file name.' }),
      reviewSource,
    ),
    h('section', { class: 'panel glass' }, h('h2', {}, '2. Lecture options'), options.el),
    h('div', { class: 'form-actions row gap' }, submit, status),
  );

  /**
   * The file to upload (the chosen file, or the pasted notes as a .txt file), or null after showing why not.
   * @returns {File | null}
   */
  function sourceFile() {
    if (source.current() === 'paste') {
      const text = cleanPasted(notes.value);
      const problem = pastedProblem(text, maxMb, maxChars);
      if (problem) {
        notesField.setError(problem);
        notes.focus();
        return null;
      }
      const pasted = new File([text], pastedFileName(title.value.trim() || firstLineTitle(text)), { type: 'text/plain' });
      const bad = validateSourceFile(pasted, maxMb);
      if (bad) {
        notesField.setError(bad);
        return null;
      }
      return pasted;
    }
    if (!file) {
      fileError.textContent = 'Choose a source document first.';
      fileError.hidden = false;
      drop.el.querySelector('button')?.focus();
      return null;
    }
    const problem = validateSourceFile(file, maxMb);
    if (problem) {
      fileError.textContent = problem;
      fileError.hidden = false;
      return null;
    }
    return file;
  }

  /** @param {boolean} disabled */
  function setFormDisabled(disabled) {
    submit.disabled = disabled;
    exampleBtn.disabled = disabled;
    options.setDisabled(disabled);
    drop.setDisabled(disabled);
    notes.disabled = disabled;
    reviewSource.input.disabled = disabled;
  }

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (submitting) return;
    status.hidden = true;
    const upload = sourceFile();
    if (!upload) return;
    const { options: payload, errors } = options.build();
    if (Object.keys(errors).length) {
      status.textContent = 'Please fix the highlighted options.';
      status.hidden = false;
      return;
    }
    const pasted = source.current() === 'paste';
    const checkSource = reviewSource.input.checked;
    submitting = true;
    app.setLeaveGuard(null); // leaving during the upload is fine: a toast links to the project
    setFormDisabled(true);
    submit.setAttribute('aria-busy', 'true');
    status.textContent = 'Uploading…';
    status.hidden = false;
    status.setAttribute('role', 'status');
    try {
      /** @type {Record<string, string | Blob>} */
      const fields = { file: upload, options: JSON.stringify(payload) };
      const typedTitle = title.value.trim() || (pasted ? firstLineTitle(cleanPasted(notes.value)) : '');
      if (typedTitle) fields.title = typedTitle;
      if (checkSource) fields.review_source = 'true';
      // Leaving the page does not cancel the upload; the result is just no longer shown here.
      const res = await uploadForm('/api/projects', fields);
      if (destroyed) {
        // The job runs on, but this page no longer follows it (no stream, no navigation).
        app.toast(`“${res.project.title}” is being generated.`, {
          kind: 'info',
          action: { label: 'Open project', onClick: () => app.navigate(href('project', { id: res.project.id })) },
        });
        return;
      }
      status.hidden = true;
      showProgress(res, checkSource);
    } catch (err) {
      if (destroyed) {
        app.reportError(err, 'Uploading the new lecture failed.');
        return;
      }
      status.setAttribute('role', 'alert');
      status.textContent = '';
      status.hidden = true;
      app.reportError(err, 'Upload failed.');
      submitting = false;
      app.setLeaveGuard(leaveGuard);
      setFormDisabled(false);
    } finally {
      if (!destroyed) submit.removeAttribute('aria-busy');
    }
  });

  exampleBtn.addEventListener('click', async () => {
    if (submitting) return;
    if (dirty() && !(await confirmDialog({ title: 'Discard this lecture?', message: 'The file or notes and the details you entered will be lost.', confirmLabel: 'Discard', danger: true }))) return;
    if (destroyed || submitting) return;
    submitting = true;
    app.setLeaveGuard(null);
    setFormDisabled(true);
    exampleBtn.setAttribute('aria-busy', 'true');
    exampleStatus.textContent = 'Adding the example…';
    try {
      const blob = await get(EXAMPLE_URL, { as: 'blob' });
      const file = new File([blob], 'ohms-law.json', { type: 'application/json' });
      const res = await uploadForm('/api/projects/import', { file, title: EXAMPLE_TITLE });
      if (destroyed) {
        app.toast(`“${res.project.title}” is being built.`, { kind: 'info', action: { label: 'Open project', onClick: () => app.navigate(href('project', { id: res.project.id })) } });
        return;
      }
      exampleStatus.textContent = '';
      showExampleProgress(res);
    } catch (err) {
      if (destroyed) return;
      exampleStatus.textContent = '';
      app.reportError(err, 'Could not add the example lecture.');
      submitting = false;
      app.setLeaveGuard(leaveGuard);
      setFormDisabled(false);
    } finally {
      if (!destroyed) exampleBtn.removeAttribute('aria-busy');
    }
  });

  /**
   * Follow the example's build here; when it is done, open its project page.
   * @param {any} res {project, version, job, warnings}
   */
  function showExampleProgress(res) {
    const projectId = res.project.id;
    form.hidden = true;
    example.hidden = true;
    clear(progressHost);
    const widget = res.job
      ? jobProgress(res.job, {
          onSuccess: () => {
            if (destroyed) return;
            app.toast('The example lecture is ready.', { kind: 'success' });
            app.navigate(href('project', { id: projectId }));
          },
        })
      : null;
    progress = widget;
    progressHost.append(
      h('h2', {}, `Building “${res.project.title}”`),
      h('p', { class: 'muted' }, 'Aadhi is making the voice-over and the visuals. This takes a few minutes. You can leave this page; the build keeps running and appears on the project page.'),
    );
    if (widget) progressHost.append(widget.el);
    progressHost.append(
      h('div', { class: 'row gap' }, h('a', { class: 'btn btn-outline', href: href('project', { id: projectId }) }, 'Go to project page'), h('a', { class: 'btn btn-ghost', href: '#/projects' }, 'Back to projects')),
    );
    progressHost.querySelector('h2')?.setAttribute('tabindex', '-1');
    /** @type {HTMLElement | null} */ (progressHost.querySelector('h2'))?.focus();
  }

  /**
   * @param {any} res {project, version, job}
   * @param {boolean} checkSource the job pauses first for the source review
   */
  function showProgress(res, checkSource) {
    const projectId = res.project.id;
    const versionId = res.version.id;
    form.hidden = true;
    example.hidden = true;
    clear(progressHost);
    const editorHash = href('editor', { id: projectId, vid: versionId });
    const reviewHash = href('planReview', { id: projectId, vid: versionId });
    const sourceHash = href('sourceReport', { id: projectId, vid: versionId });
    const widget = jobProgress(res.job, {
      // the first pause is the source review: its page (not the plan review) is where to go
      reviewHref: checkSource ? sourceHash : reviewHash,
      reviewLabel: checkSource ? SOURCE_REVIEW_LABEL : undefined,
      onSuccess: () => {
        if (destroyed) return;
        app.toast('Your lecture is ready.', { kind: 'success' });
        app.navigate(editorHash);
      },
      onAwaitingReview: () => {
        if (checkSource) {
          if (destroyed) return;
          app.toast('Aadhi has read your document. Check it before the lecture is planned.', { kind: 'info' });
          app.navigate(sourceHash);
          return;
        }
        app.toast('The lecture plan is ready for your review.', { kind: 'info' });
      },
    });
    progress = widget;
    progressHost.append(
      h('h2', {}, `Generating “${res.project.title}”`),
      h('p', { class: 'muted' }, checkSource ? 'Aadhi is reading your document; you can check how it was read before anything is planned. You can leave this page; the job keeps running and appears on the project page.' : 'This usually takes a few minutes. You can leave this page; the job keeps running and appears on the project page.'),
      widget.el,
      h('div', { class: 'row gap' }, h('a', { class: 'btn btn-outline', href: href('project', { id: projectId }) }, 'Go to project page'), h('a', { class: 'btn btn-ghost', href: '#/projects' }, 'Back to projects')),
    );
    progressHost.querySelector('h2')?.setAttribute('tabindex', '-1');
    /** @type {HTMLElement | null} */ (progressHost.querySelector('h2'))?.focus();
  }

  clear(body);
  body.append(example, form, progressHost);
  app.setLeaveGuard(leaveGuard);
  return {
    destroy() {
      destroyed = true;
      app.setLeaveGuard(null);
      if (progress) progress.destroy();
      progress = null;
      drop.destroy();
      source.destroy();
    },
  };
}
