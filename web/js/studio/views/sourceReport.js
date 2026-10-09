// @ts-check
/**
 * "How Aadhi read your document" (GET /api/projects/{id}/versions/{vid}/source-report): an advisory
 * readiness verdict, things to check (why / where / suggestion; "mark as done" is remembered in this
 * browser only), the outline with section roles, what was set aside before planning (counts by
 * category, never the removed text), the concepts with the parts they come from, formulas whose symbols
 * the document never explains, every part of the document with its status, and which parts each scene
 * cites.
 *
 * While the generation waits for the teacher's source review (`review.active`), parts can be set aside
 * or restored and concepts renamed (PUT /api/versions/{vid}/source-review), then the lecture is planned
 * (POST /api/versions/{vid}/approve-source). Unsaved changes are guarded. All text goes through
 * textContent (the shared `h` helper).
 */

import { h, clear } from '../../shared/dom.js';
import { get, put, post } from '../../shared/api.js';
import { button, linkButton, input, spinner, errorState, emptyState } from '../components/form.js';
import { confirmDialog } from '../components/modal.js';
import { captureFocus, restoreFocus } from '../components/focusKeep.js';
import { href } from '../router.js';
import { pageHeader, breadcrumbs, jobModal } from './common.js';

/** Readiness verdicts in the teacher's words. */
export const VERDICTS = /** @type {const} */ ({
  well_structured: { label: 'Ready: well structured', tone: 'ok' },
  partially_structured: { label: 'Ready, with a few notes', tone: 'ok' },
  needs_reorganization: { label: 'Could be better organised', tone: 'attention' },
  missing_context: { label: 'Some things are not explained', tone: 'attention' },
  ambiguous: { label: 'Some text may confuse the lecture', tone: 'attention' },
  incomplete: { label: 'Something seems to be missing', tone: 'attention' },
});
/** Worst first (mirrors aadhi/pipeline/source_review.py PRECEDENCE). */
const PRECEDENCE = ['needs_reorganization', 'incomplete', 'missing_context', 'ambiguous', 'partially_structured'];

export const STATUS_LABELS = /** @type {Record<string, string>} */ ({
  teaching: 'Taught',
  context: 'Background',
  set_aside: 'Set aside by Aadhi',
  set_aside_by_you: 'Set aside by you',
  restored: 'Restored by you',
});
const STATUS_TONE = /** @type {Record<string, string>} */ ({ teaching: 'ok', context: 'outline', set_aside: 'muted', set_aside_by_you: 'attention', restored: 'gold' });
const ROLE_LABELS = /** @type {Record<string, string>} */ ({ objectives: 'Objectives', prerequisites: 'Prerequisites', summary: 'Summary', questions: 'Questions', references: 'References' });
const REASON_LABELS = /** @type {Record<string, string>} */ ({
  administrative: 'document details',
  production: 'production notes',
  scaffolding: 'video packaging',
  duplicate: 'a repeat',
  off_topic: 'off topic',
});
const KIND_LABELS = /** @type {Record<string, string>} */ ({ pdf: 'PDF', docx: 'Word document', markdown: 'Markdown', text: 'Text' });
const NOT_AVAILABLE = /** @type {Record<string, string>} */ ({
  no_source: 'This version was not generated from a document here (for example an imported lecture), so there is no reading to show.',
  unreadable: 'The document this version was generated from can no longer be read.',
});
export const MAX_CONCEPT_NAME = 160;

/**
 * Readiness from the findings still open (mirrors source_review.readiness).
 * @param {Array<{ id: string, severity: string, category: string }>} findings
 * @param {Set<string>} done
 */
export function readinessOf(findings, done) {
  const open = findings.filter((f) => !done.has(f.id));
  const warnings = open.filter((f) => f.severity === 'warning' || f.severity === 'error');
  let verdict = 'well_structured';
  if (warnings.length) {
    verdict = warnings.map((f) => f.category).sort((a, b) => rank(a) - rank(b))[0];
  } else if (open.length) {
    verdict = 'partially_structured';
  }
  return { verdict, ready: !warnings.length, warnings: warnings.length, infos: open.length - warnings.length };
}

/** @param {string} category */
function rank(category) {
  const i = PRECEDENCE.indexOf(category);
  return i < 0 ? PRECEDENCE.length : i;
}

/**
 * @typedef {{ excluded: Set<string>, restored: Set<string>, names: Record<string, string> }} Draft
 */

/**
 * The editable corrections from the report's saved overrides.
 * @param {any} overrides
 * @returns {Draft}
 */
export function draftFrom(overrides) {
  const o = overrides || {};
  return { excluded: new Set(o.excluded_chunk_ids || []), restored: new Set(o.restored_chunk_ids || []), names: { ...(o.concept_names || {}) } };
}

/**
 * PUT /source-review body (chunk ids in document order).
 * @param {Draft} draft
 * @param {string[]} order chunk ids in document order
 */
export function draftBody(draft, order) {
  const sorted = (/** @type {Set<string>} */ ids) => order.filter((id) => ids.has(id));
  /** @type {Record<string, string>} */
  const names = {};
  for (const [k, v] of Object.entries(draft.names)) if (String(v).trim()) names[k] = String(v).trim().slice(0, MAX_CONCEPT_NAME);
  return { excluded_chunk_ids: sorted(draft.excluded), restored_chunk_ids: sorted(draft.restored), concept_names: names };
}

/**
 * A part's status with the (possibly unsaved) corrections applied.
 * @param {{ id: string, aadhi_status: string }} chunk
 * @param {Draft} draft
 */
export function effectiveStatus(chunk, draft) {
  if (draft.excluded.has(chunk.id)) return 'set_aside_by_you';
  if (chunk.aadhi_status === 'set_aside' && draft.restored.has(chunk.id)) return 'restored';
  return chunk.aadhi_status;
}

/** @param {number} vid */
function doneKey(vid) {
  return `aadhi.sourceReport.done.v${vid}`;
}

/** @param {number} vid @returns {Set<string>} */
function loadDone(vid) {
  try {
    const raw = window.localStorage.getItem(doneKey(vid));
    const list = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(list) ? list.filter((x) => typeof x === 'string') : []);
  } catch {
    return new Set();
  }
}

/** @param {number} vid @param {Set<string>} done */
function saveDone(vid, done) {
  try {
    window.localStorage.setItem(doneKey(vid), JSON.stringify([...done]));
  } catch {
    /* private mode or blocked storage: marks last for this visit only */
  }
}

/** @param {string} status @param {string} [label] */
function pill(status, label) {
  return h('span', { class: ['badge', `badge-${STATUS_TONE[status] || 'muted'}`] }, label || STATUS_LABELS[status] || status);
}

/** @param {number} n @param {string} one @param {string} [many] */
function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || `${one}s`}`;
}

/** @type {import('../types.js').ViewMount} */
export async function mount(container, { app, params, signal }) {
  const { id: projectId, vid } = params;
  const self = href('sourceReport', { id: projectId, vid });
  container.append(breadcrumbs([{ label: 'Projects', hash: '#/projects' }, { label: 'Project', hash: href('project', { id: projectId }) }, { label: 'How Aadhi read your document' }]));
  const body = h('div', { class: 'source-report' }, spinner('Loading the report…'));
  container.appendChild(body);
  const url = `/api/projects/${projectId}/versions/${vid}/source-report`;

  /** @type {any} */
  let data;
  /** @type {any} */
  let meta = null;
  try {
    [data, meta] = await Promise.all([get(url), app.meta().catch(() => null)]);
  } catch (err) {
    if (signal && signal.aborted) return;
    clear(body);
    body.appendChild(errorState('Could not load the report.', () => app.navigate(self, { replace: true })));
    app.reportError(err);
    return;
  }
  if (signal && signal.aborted) return;
  app.setTitle('How Aadhi read your document');
  if (!data.available) {
    clear(body);
    body.appendChild(emptyState('No source report', NOT_AVAILABLE[data.reason] || NOT_AVAILABLE.no_source, h('a', { class: 'btn btn-gold', href: href('project', { id: projectId }) }, 'Go to project')));
    return;
  }

  /** @type {any} */
  let report = data.report;
  let editable = !!(data.review && data.review.editable);
  /** @type {Draft} */
  let draft = draftFrom(report.overrides);
  let saved = JSON.stringify(draftBody(draft, report.chunks.map((/** @type {any} */ c) => c.id)));
  const done = loadDone(vid);
  let busy = false;
  let destroyed = false;
  const root = h('div', {});
  clear(body);
  body.appendChild(root);

  const order = () => report.chunks.map((/** @type {any} */ c) => c.id);
  const dirty = () => editable && JSON.stringify(draftBody(draft, order())) !== saved;
  const languageLabel = (/** @type {string | null} */ code) => {
    const found = meta && Array.isArray(meta.languages) ? meta.languages.find((/** @type {any} */ l) => l.code === code) : null;
    return found ? found.label : code || '';
  };

  function render() {
    const snap = captureFocus(root);
    clear(root);
    root.dataset.dirty = dirty() ? 'true' : 'false';
    const subtitle = [report.file, KIND_LABELS[report.source_kind] || '', report.pages ? plural(report.pages, 'page') : '', languageLabel(report.language)].filter(Boolean).join(' · ');
    const saveBtn = button('Save changes', { kind: 'outline', icon: 'save', disabled: busy || !dirty(), dataset: { fk: 'save' } });
    saveBtn.addEventListener('click', () => void save());
    const continueBtn = button('Continue: plan the lecture', { kind: 'gold', icon: 'check', disabled: busy, dataset: { fk: 'continue' } });
    continueBtn.addEventListener('click', () => void approve());
    root.append(
      pageHeader(
        'How Aadhi read your document',
        subtitle || null,
        ...(editable ? [saveBtn, continueBtn] : [linkButton('Back to project', href('project', { id: projectId }), { kind: 'outline' })]),
      ),
    );
    if (editable) {
      root.append(
        h(
          'div',
          { class: 'notice', role: 'status' },
          h('strong', {}, 'Aadhi has read your document and is waiting for you. '),
          'Set aside parts that should not be taught, restore parts Aadhi set aside, rename concepts, then continue. Nothing is planned or written until you do.',
        ),
      );
    }
    root.append(overviewSection(), findingsSection(), outlineSection(), scopeSection(), conceptsSection(), contentSection(), partsSection());
    if (report.scenes && report.scenes.length) root.append(scenesSection());
    restoreFocus(root, snap);
  }

  // --- overview ---------------------------------------------------------------------------
  function overviewSection() {
    const r = readinessOf(report.findings, done);
    const verdict = VERDICTS[/** @type {keyof typeof VERDICTS} */ (r.verdict)] || VERDICTS.partially_structured;
    const statuses = report.chunks.map((/** @type {any} */ c) => (editable ? effectiveStatus(c, draft) : c.status));
    const aside = statuses.filter((/** @type {string} */ s) => s === 'set_aside' || s === 'set_aside_by_you').length;
    const teaching = report.outline.sections.filter((/** @type {any} */ s) => s.role === 'teaching' && s.title).length;
    const inv = report.inventory;
    const stat = (/** @type {string | number} */ value, /** @type {string} */ label) => h('div', { class: 'stat-card glass' }, h('div', { class: 'stat-value' }, String(value)), h('div', { class: 'stat-label' }, label));
    return h(
      'section',
      { class: 'panel glass', 'data-section': 'overview' },
      h('h2', {}, 'Overview'),
      h(
        'p',
        { class: 'row gap wrap', 'data-verdict': r.verdict },
        h('span', { class: ['badge', `badge-${verdict.tone}`] }, verdict.label),
        h('span', { class: 'muted' }, r.ready ? 'Nothing needs fixing before the lecture is written.' : `${plural(r.warnings, 'thing')} worth fixing in your document. This is advice: generation is never blocked.`),
      ),
      report.outline.title ? h('p', {}, h('strong', {}, 'Title: '), report.outline.title) : h('p', { class: 'muted' }, 'No title found in the document.'),
      h(
        'div',
        { class: 'stat-grid' },
        stat(teaching, 'Teaching sections'),
        stat(report.chunks.length - aside, 'Parts taught'),
        stat(aside, 'Parts set aside'),
        stat(inv.formula_count, 'Formulas'),
        stat(inv.code_blocks, 'Code examples'),
        stat(inv.figures || inv.figure_markers, 'Figures'),
        stat(inv.tables, 'Tables'),
        stat(inv.questions, 'Questions'),
      ),
      report.strengths.length ? h('ul', { class: 'plain-list small' }, report.strengths.map((/** @type {string} */ s) => h('li', {}, `✓ ${s}`))) : null,
      report.warnings.length
        ? h('div', { class: 'notice warning' }, h('strong', {}, 'While reading the document'), h('ul', {}, report.warnings.map((/** @type {string} */ w) => h('li', {}, w))))
        : null,
      report.truncated ? h('p', { class: 'notice warning' }, 'The document is long: only its first part was used.') : null,
      report.attach_original ? h('p', { class: 'muted small' }, 'Some pages were hard to read as text (scans or equations), so the original file is also given to the AI when it can read files.') : null,
    );
  }

  // --- things to check --------------------------------------------------------------------
  function findingsSection() {
    const section = h('section', { class: 'panel glass', 'data-section': 'findings' }, h('h2', {}, 'Things to check'));
    if (!report.findings.length) {
      section.append(h('p', { class: 'muted' }, 'Nothing to check: the document reads well.'));
      return section;
    }
    if (report.findings_total > report.findings.length) section.append(h('p', { class: 'muted small' }, `Showing the first ${report.findings.length} of ${report.findings_total}.`));
    const list = h('ul', { class: 'plain-list finding-list' });
    for (const f of report.findings) {
      const isDone = done.has(f.id);
      const toggle = button(isDone ? 'Marked as done' : 'Mark as done', { kind: 'ghost', small: true, icon: isDone ? 'check' : undefined, ariaPressed: isDone, dataset: { fk: `done-${f.id}` } });
      toggle.addEventListener('click', () => {
        if (done.has(f.id)) done.delete(f.id);
        else done.add(f.id);
        saveDone(vid, done);
        render();
      });
      const where = [f.heading ? `“${f.heading}”` : '', f.chunk_ids.length ? `part ${f.chunk_ids.join(', ')}` : ''].filter(Boolean).join(' · ');
      list.append(
        h(
          'li',
          { class: ['finding', isDone ? 'done' : ''], 'data-finding': f.code },
          h('div', { class: 'row gap wrap' }, h('span', { class: ['badge', f.severity === 'warning' ? 'badge-attention' : 'badge-muted'] }, f.severity === 'warning' ? 'Worth fixing' : 'Note'), h('strong', {}, f.message), toggle),
          f.why ? h('div', { class: 'small' }, f.why) : null,
          where ? h('div', { class: 'muted small' }, `Where: ${where}`) : null,
          f.suggestion ? h('div', { class: 'small' }, `Suggestion: ${f.suggestion}`) : null,
        ),
      );
    }
    section.append(list);
    return section;
  }

  // --- outline ----------------------------------------------------------------------------
  /** @param {any} node @param {boolean} top */
  function outlineItem(node, top) {
    const role = ROLE_LABELS[node.role];
    const details = [plural(node.chunk_ids.length, 'part'), node.set_aside_chunks ? `${node.set_aside_chunks} set aside` : '', node.visual_notes ? plural(node.visual_notes, 'visual note') : ''].filter(Boolean).join(' · ');
    return h(
      'li',
      {},
      h('div', { class: 'row gap wrap' }, h(top ? 'strong' : 'span', {}, node.title || (top ? 'Opening text' : 'Untitled')), role ? h('span', { class: 'badge badge-outline' }, role) : null, node.empty ? h('span', { class: 'badge badge-attention' }, 'Empty') : null, h('span', { class: 'muted small' }, details)),
      node.subtopics && node.subtopics.length ? h('ul', { class: 'outline-sub' }, node.subtopics.map((/** @type {any} */ t) => outlineItem(t, false))) : null,
    );
  }

  function outlineSection() {
    return h(
      'section',
      { class: 'panel glass', 'data-section': 'outline' },
      h('h2', {}, 'Outline'),
      h('p', { class: 'muted small' }, 'Sections from the headings of your document. Objectives, summaries and questions are read for what they are; the lecture still writes its own objectives, quizzes and recap.'),
      report.outline.sections.length ? h('ul', { class: 'outline' }, report.outline.sections.map((/** @type {any} */ s) => outlineItem(s, true))) : h('p', { class: 'muted' }, 'No sections found.'),
    );
  }

  // --- set aside before planning ------------------------------------------------------------
  function scopeSection() {
    const acc = report.accounting;
    const rules = report.scope.filter((/** @type {any} */ s) => s.source === 'rules');
    const brief = report.scope.filter((/** @type {any} */ s) => s.source === 'brief');
    const row = (/** @type {any} */ s) => h('li', {}, h('strong', {}, String(s.count)), ` · ${s.label || s.category}`);
    const reasons = Object.entries(acc.skipped_by_reason || {}).map(([k, n]) => `${n} ${REASON_LABELS[k] || k}`);
    return h(
      'section',
      { class: 'panel glass', 'data-section': 'scope' },
      h('h2', {}, 'Set aside before planning'),
      h('p', { class: 'muted small' }, 'Only counts are shown: what was set aside is never shown, sent to the AI or put in the lecture.'),
      rules.length ? h('ul', { class: 'plain-list' }, rules.map(row)) : h('p', { class: 'muted' }, 'No administrative or production details were found.'),
      brief.length ? h('div', {}, h('p', { class: 'small' }, 'Also set aside while finding the concepts:'), h('ul', { class: 'plain-list' }, brief.map(row))) : null,
      report.brief_available
        ? h('p', { class: 'small' }, `Of ${plural(acc.chunks, 'part')}: ${acc.cited} taught, ${acc.context} kept as background, ${acc.set_aside} set aside by Aadhi${reasons.length ? ` (${reasons.join(', ')})` : ''}, ${acc.set_aside_by_you} set aside by you, ${acc.restored} restored by you.`)
        : h('p', { class: 'muted small' }, 'The concepts could not be extracted first, so every part of the document goes to the planner.'),
      report.visual_notes ? h('p', { class: 'small' }, `${plural(report.visual_notes, 'animation or visual suggestion')} from the author ${report.visual_notes === 1 ? 'is' : 'are'} kept as ideas for visuals (never narrated).`) : null,
    );
  }

  // --- concepts -----------------------------------------------------------------------------
  function conceptsSection() {
    const section = h('section', { class: 'panel glass', 'data-section': 'concepts' }, h('h2', {}, 'Concepts Aadhi found'));
    if (!report.concepts.length) {
      section.append(h('p', { class: 'muted' }, report.brief_available ? 'No concepts.' : 'The concepts were not extracted for this version.'));
      return section;
    }
    const list = h('ul', { class: 'plain-list concept-list' });
    for (const c of report.concepts) {
      const dropped = editable ? c.chunk_ids.length > 0 && c.chunk_ids.every((/** @type {string} */ id) => draft.excluded.has(id)) : c.dropped;
      const original = c.original_name || c.name;
      /** @type {HTMLElement} */
      let title;
      if (editable) {
        const field = input({ value: draft.names[c.key] ?? c.name, maxLength: MAX_CONCEPT_NAME, ariaLabel: `Name of the concept “${original}”`, dataset: { fk: `name-${c.key}` } });
        field.addEventListener('input', () => {
          const value = field.value;
          if (!value.trim() || value.trim() === original) delete draft.names[c.key];
          else draft.names[c.key] = value;
          updateDirty();
        });
        title = field;
      } else {
        title = h('strong', {}, c.name);
      }
      list.append(
        h(
          'li',
          { class: ['concept', dropped ? 'dropped' : ''], 'data-concept': c.key },
          h('div', { class: 'row gap wrap' }, title, dropped ? h('span', { class: 'badge badge-muted' }, 'Not planned: every part it comes from is set aside') : null),
          c.headings.length ? h('div', { class: 'muted small' }, `From: ${c.headings.join(' · ')}`) : null,
        ),
      );
    }
    section.append(list);
    return section;
  }

  // --- formulas and other content ----------------------------------------------------------
  function contentSection() {
    const inv = report.inventory;
    const section = h('section', { class: 'panel glass', 'data-section': 'content' }, h('h2', {}, 'Formulas'));
    if (!inv.formulas.length) {
      section.append(h('p', { class: 'muted' }, 'No formulas found.'));
      return section;
    }
    section.append(
      h(
        'ul',
        { class: 'plain-list' },
        inv.formulas.map((/** @type {any} */ f) =>
          h(
            'li',
            {},
            h('code', { class: 'mono' }, f.expression),
            ' ',
            f.undefined_symbols.length ? h('span', { class: 'badge badge-attention' }, `Not explained: ${f.undefined_symbols.join(', ')}`) : h('span', { class: 'badge badge-ok' }, 'All symbols explained'),
            h('span', { class: 'muted small' }, ` · part ${f.chunk_id}`),
          ),
        ),
      ),
    );
    if (inv.formula_count > inv.formulas.length) section.append(h('p', { class: 'muted small' }, `Showing ${inv.formulas.length} of ${inv.formula_count}.`));
    return section;
  }

  // --- parts --------------------------------------------------------------------------------
  function partsSection() {
    const rows = report.chunks.map((/** @type {any} */ c) => {
      const status = editable ? effectiveStatus(c, draft) : c.status;
      const reason = (status === 'set_aside' || status === 'restored') && c.reason ? ` (${REASON_LABELS[c.reason] || c.reason})` : '';
      /** @type {HTMLElement | null} */
      let action = null;
      if (editable) {
        if (status === 'set_aside_by_you') action = actionButton('Keep', c.id, () => draft.excluded.delete(c.id));
        else if (status === 'set_aside') action = actionButton('Restore', c.id, () => draft.restored.add(c.id));
        else if (status === 'restored') action = actionButton('Set aside again', c.id, () => draft.restored.delete(c.id));
        else action = actionButton('Set aside', c.id, () => draft.excluded.add(c.id));
      }
      return h(
        'tr',
        { 'data-chunk': c.id, 'data-status': status },
        h('th', { scope: 'row', class: 'mono small' }, c.id),
        h('td', {}, c.heading || h('span', { class: 'muted' }, 'Opening text'), c.page ? h('div', { class: 'muted small' }, `page ${c.page}`) : null),
        h('td', {}, pill(status, `${STATUS_LABELS[status] || status}${reason}`)),
        h('td', { class: 'small' }, c.excerpt),
        editable ? h('td', {}, action) : null,
      );
    });
    return h(
      'section',
      { class: 'panel glass', 'data-section': 'parts' },
      h('h2', {}, 'Parts of your document'),
      h('p', { class: 'muted small' }, 'Aadhi cuts your document into parts and cites them in the lecture. Background parts are not tied to a concept but the planner still reads them.'),
      h(
        'div',
        { class: 'table-wrap' },
        h(
          'table',
          { class: 'table compact' },
          h('caption', { class: 'sr-only' }, 'Parts of the document'),
          h('thead', {}, h('tr', {}, ['Part', 'Heading', 'Status', 'Starts with', ...(editable ? ['Change'] : [])].map((t) => h('th', { scope: 'col' }, t)))),
          h('tbody', {}, rows),
        ),
      ),
    );
  }

  /**
   * @param {string} label
   * @param {string} chunkId
   * @param {() => void} change
   */
  function actionButton(label, chunkId, change) {
    const b = button(label, { kind: 'ghost', small: true, disabled: busy, dataset: { fk: `part-${chunkId}` } });
    b.addEventListener('click', () => {
      const before = draftFrom(draftBody(draft, order()));
      change();
      const aside = (/** @type {any} */ c) => ['set_aside', 'set_aside_by_you'].includes(effectiveStatus(c, draft));
      if (report.chunks.every(aside)) {
        draft = { ...before, names: draft.names };
        app.toast('Keep at least one part of the document to teach.', { kind: 'warning' });
      }
      render();
    });
    return b;
  }

  // --- scene trace --------------------------------------------------------------------------
  function scenesSection() {
    const byId = new Map(report.chunks.map((/** @type {any} */ c) => [c.id, c]));
    const citing = report.scenes.filter((/** @type {any} */ s) => s.chunk_ids.length).length;
    return h(
      'section',
      { class: 'panel glass', 'data-section': 'scenes' },
      h('h2', {}, 'Where each scene comes from'),
      h('p', { class: 'muted small' }, `${citing} of ${plural(report.scenes.length, 'scene')} cite parts of your document. Scenes without a citation (openings, transitions, quizzes written from the plan) are not wrong by themselves.`),
      h(
        'ol',
        { class: 'plain-list scene-trace' },
        report.scenes.map((/** @type {any} */ s) =>
          h(
            'li',
            { 'data-scene': s.scene_id },
            h('strong', {}, s.title || s.scene_id),
            h('span', { class: 'muted small' }, ` · ${s.type}`),
            h(
              'div',
              { class: 'small' },
              s.chunk_ids.length
                ? `From: ${s.chunk_ids.map((/** @type {string} */ id) => { const c = /** @type {any} */ (byId.get(id)); return c && c.heading ? `${id} (${c.heading})` : id; }).join(', ')}`
                : 'No part of the document cited.',
              s.unknown_refs ? ` ${plural(s.unknown_refs, 'citation')} to parts that do not exist.` : '',
            ),
          ),
        ),
      ),
    );
  }

  // --- saving and continuing ---------------------------------------------------------------
  function updateDirty() {
    root.dataset.dirty = dirty() ? 'true' : 'false';
    const saveBtn = /** @type {HTMLButtonElement | null} */ (root.querySelector('[data-fk="save"]'));
    if (saveBtn) saveBtn.disabled = busy || !dirty();
  }

  async function save() {
    busy = true;
    render();
    try {
      const bodyJson = draftBody(draft, order());
      await put(`/api/versions/${vid}/source-review`, bodyJson);
      const fresh = await get(url);
      if (destroyed) return true;
      report = fresh.report || report;
      editable = !!(fresh.review && fresh.review.editable);
      draft = draftFrom(report.overrides);
      saved = JSON.stringify(draftBody(draft, order()));
      app.toast('Changes saved.', { kind: 'success' });
      return true;
    } catch (err) {
      app.reportError(err, 'Could not save your changes.');
      return false;
    } finally {
      busy = false;
      if (!destroyed) render();
    }
  }

  async function approve() {
    if (dirty() && !(await save())) return;
    const ok = await confirmDialog({ title: 'Plan the lecture now?', message: 'Aadhi will plan the lecture from your document with your changes. You can still review the plan if you asked for that, and edit everything afterwards.', confirmLabel: 'Continue' });
    if (!ok || destroyed) return;
    busy = true;
    render();
    try {
      const res = await post(`/api/versions/${vid}/approve-source`, {});
      app.setLeaveGuard(null);
      if (destroyed) return;
      void jobModal('Planning the lecture', res.job, {
        reviewHref: href('planReview', { id: projectId, vid }),
        onSuccess: () => {
          if (!destroyed) app.navigate(href('editor', { id: projectId, vid }));
        },
        description: 'You can close this dialog; progress also shows on the project page.',
      }).then((job) => {
        if (!destroyed && job && job.status !== 'succeeded') app.navigate(href('project', { id: projectId }));
      });
    } catch (err) {
      app.reportError(err, 'Could not continue.');
      busy = false;
      render();
    }
  }

  const guarded = editable;
  if (guarded) {
    app.setLeaveGuard(async () => !dirty() || confirmDialog({ title: 'Discard your changes?', message: 'You changed how parts of the document are used but did not save.', confirmLabel: 'Discard', danger: true }));
  }
  render();
  return {
    destroy() {
      destroyed = true;
      if (guarded) app.setLeaveGuard(null);
    },
  };
}
