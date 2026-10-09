// @ts-check
/**
 * Board items editor for every BoardItemKind with a live preview (rich-lite + TeX):
 * heading/bullet/paragraph/callouts/takeaway (text), definition (term + text), formula (LaTeX,
 * meaning, variables), misconception (text, correction, linked misconception), code
 * (language + code), table (grid editor), figure (source figure or upload: a lecture without
 * source figures can still get one by uploading), example_step (text, justification, blank).
 * Reorder by drag/keyboard; deleting clears beat references.
 */

import { h } from '../../../shared/dom.js';
import { field, select, textarea, input, button, checkbox, group, formGrid } from '../../components/form.js';
import { chipsInput } from '../../components/chips.js';
import { sortableList } from '../../components/sortable.js';
import { confirmDialog } from '../../components/modal.js';
import * as E from '../../lib/screenplayEdit.js';
import { boardItemPreview, updateBoardItemPreview } from './richPreview.js';
import { mediaUpload } from './mediaUpload.js';

/** @typedef {import('./context.js').InspectorCtx} InspectorCtx */

const CODE_LANGUAGES = ['python', 'c', 'cpp', 'java', 'javascript', 'bash', 'sql', 'matlab', 'verilog', 'text'];
const RICH_HINT = 'Rich text: **bold**, *italic*, `code`, $x^2$ maths, [[keyword]].';
const CHUNK_RE = /^c\d{4,5}$/;
const MAX_TABLE_COLS = 8;
const MAX_TABLE_ROWS = 20;
const MAX_VARIABLES = 8;

/**
 * @param {InspectorCtx} ctx
 * @returns {HTMLElement}
 */
export function boardEditor(ctx) {
  const scene = ctx.scene;
  const items = scene.board || [];
  const reveal = E.revealIndex(scene);
  const list = sortableList({
    items,
    key: (it) => it.id,
    name: (it, i) => `board item ${i + 1}, ${E.BOARD_ITEM_LABELS[it.kind] || it.kind}`,
    label: 'Board items',
    className: 'board-list',
    stateKey: `board:${scene.id}`,
    render: (it, i) => itemEditor(ctx, it, i, reveal),
    onMove: (from, to) => ctx.editScene((s) => E.moveBoardItem(s, from, to), { structural: true }),
  });
  const hasFigures = !!(ctx.sp.figures && ctx.sp.figures.length);
  const kindSel = select({
    options: E.BOARD_ITEM_KINDS.map((k) => ({ value: k, label: k === 'figure' && !hasFigures ? `${E.BOARD_ITEM_LABELS[k]} (upload one)` : E.BOARD_ITEM_LABELS[k] })),
    value: 'bullet',
    ariaLabel: 'Kind of board item to add',
  });
  const full = items.length >= E.MAX_BOARD_ITEMS;
  const add = button('Add item', { kind: 'outline', small: true, icon: 'plus', disabled: full });
  add.addEventListener('click', () => {
    ctx.editScene(
      (s) => {
        const r = E.addBoardItem(s, kindSel.value, (s.board || []).length - 1, ctx.sp);
        // Open the new item's editor: the structural edit re-renders right after this updater.
        ctx.openSections.add(`item:${r.item.id}`);
        return r.scene;
      },
      { structural: true },
    );
  });
  return h(
    'section',
    { class: 'inspector-section board-section', 'aria-label': 'Board' },
    h('div', { class: 'section-head' }, h('h3', {}, 'Board'), h('span', { class: 'muted small' }, `${items.length}/${E.MAX_BOARD_ITEMS} items`)),
    h('p', { class: 'muted small' }, 'Layout follows this order; beats decide when each item appears.'),
    items.length ? list.el : h('p', { class: 'muted' }, 'The board is empty.'),
    h('div', { class: 'row gap' }, kindSel, add),
    full ? h('p', { class: 'muted small' }, 'A board holds at most 12 items; split long scenes in two.') : null,
  );
}

/**
 * @param {InspectorCtx} ctx
 * @param {any} item
 * @param {number} index
 * @param {Map<string, number>} reveal
 */
function itemEditor(ctx, item, index, reveal) {
  const scene = ctx.scene;
  const fk = (/** @type {string} */ f) => `item:${item.id}:${f}`;
  const preview = boardItemPreview(item, ctx.sp);
  /** Latest item state for the preview (text edits do not re-render). */
  let current = { ...item };
  /**
   * @param {Record<string, any>} patch
   * @param {string} coalesce
   */
  const patchItem = (patch, coalesce) => {
    current = { ...current, ...patch };
    updateBoardItemPreview(preview, current, ctx.sp);
    ctx.editScene((s) => E.updateBoardItem(s, item.id, patch), { coalesce });
  };
  /**
   * @param {string} key
   * @param {string} label
   * @param {{ rows?: number, max?: number, mono?: boolean, hint?: string, required?: boolean, placeholder?: string }} [o]
   */
  const textControl = (key, label, o = {}) => {
    const opts = {
      value: item[key] || '',
      maxLength: o.max,
      placeholder: o.placeholder,
      dataset: { fk: fk(key) },
      onInput: (/** @type {string} */ v) => patchItem({ [key]: key === 'text' ? v : v === '' ? null : v }, fk(key)),
    };
    const c = o.rows ? textarea({ ...opts, rows: o.rows, mono: o.mono }) : input(opts);
    return field(label, c, { hint: o.hint, required: o.required });
  };

  const kindSel = select({
    options: E.BOARD_ITEM_KINDS.map((k) => ({ value: k, label: E.BOARD_ITEM_LABELS[k] })),
    value: item.kind,
    ariaLabel: `Kind of board item ${index + 1}`,
    dataset: { fk: fk('kind') },
    onChange: (v) => ctx.editScene((s) => E.setBoardItemKind(s, item.id, v, ctx.sp), { structural: true }),
  });
  const remove = button(`Delete board item ${index + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash' });
  remove.addEventListener('click', async () => {
    const usedBy = (scene.beats || []).filter((/** @type {any} */ b) => b.board_item_id === item.id || b.fill_item_id === item.id || (b.highlight_item_ids || []).includes(item.id)).length;
    if (usedBy && !(await confirmDialog({ title: 'Delete board item?', message: `${usedBy} beat(s) reveal, fill or highlight this item. Those references will be cleared.`, confirmLabel: 'Delete', danger: true }))) return;
    ctx.editScene((s) => E.deleteBoardItem(s, item.id), { structural: true });
  });

  const r = reveal.get(item.id);
  const when = r === undefined ? 'Visible from the start' : `Revealed by beat ${r + 1}`;
  const fields = [];
  switch (item.kind) {
    case 'definition':
      fields.push(textControl('term', 'Term', { max: 240 }), textControl('text', 'Definition', { rows: 2, max: 1200, hint: RICH_HINT }));
      break;
    case 'formula':
      fields.push(textControl('latex', 'LaTeX', { rows: 2, max: 1200, mono: true, required: true, placeholder: 'V = I R' }), textControl('text', 'What it means (optional)', { max: 1200, hint: RICH_HINT }), variablesEditor(ctx, item));
      break;
    case 'code': {
      const langKnown = CODE_LANGUAGES.includes(item.language || '');
      const lang = select({
        options: [...CODE_LANGUAGES.map((l) => ({ value: l, label: l })), ...(langKnown || !item.language ? [] : [{ value: item.language, label: item.language }])],
        value: item.language || 'python',
        dataset: { fk: fk('language') },
        onChange: (v) => patchItem({ language: v }, fk('language')),
      });
      fields.push(field('Language', lang), textControl('code', 'Code', { rows: 8, max: 4000, mono: true, required: true }), textControl('text', 'Caption (optional)', { max: 1200 }));
      break;
    }
    case 'table':
      fields.push(tableEditor(ctx, item, (patch) => patchItem(patch, fk('table'))));
      break;
    case 'figure': {
      const figures = ctx.sp.figures || [];
      const known = figures.some((/** @type {any} */ f) => f.id === item.figure_id);
      const figSel = select({
        options: [
          ...(known ? [] : [{ value: '', label: figures.length ? 'Choose a figure or upload one' : 'No figures yet: upload one below' }]),
          ...figures.map((/** @type {any} */ f) => ({ value: f.id, label: `${f.id}${f.caption ? ` · ${f.caption.slice(0, 40)}` : ''}${f.page ? ` (p. ${f.page})` : ''}` })),
        ],
        value: known ? item.figure_id : '',
        disabled: !figures.length,
        dataset: { fk: fk('figure') },
        onChange: (v) => patchItem({ figure_id: v || null }, fk('figure')),
      });
      fields.push(
        field('Source figure', figSel, { required: true }),
        textControl('caption', 'Caption', { max: 600, hint: RICH_HINT }),
        mediaUpload({
          purpose: 'figure',
          projectId: ctx.projectId,
          assetKey: null,
          label: 'Upload a new figure',
          hint: 'Adds the image to this lecture’s figures and uses it here.',
          maxMb: ctx.maxUploadMb,
          onChange: (key, info) => {
            if (!key) return;
            ctx.editScreenplay(
              (sp) => {
                const res = E.addFigure(sp, { asset_key: key, caption: item.caption || '', width: info && info.width, height: info && info.height });
                return E.updateScene(res.screenplay, scene.id, (s) => E.updateBoardItem(s, item.id, { figure_id: res.figureId }));
              },
              { structural: true },
            );
          },
        }),
      );
      break;
    }
    case 'example_step':
      fields.push(
        textControl('text', 'Step', { rows: 2, max: 1200, required: true, hint: RICH_HINT }),
        textControl('justification', 'Why (justification)', { max: 800 }),
        checkbox({
          label: 'Start blank (a later beat fills it in: faded worked example)',
          checked: !!item.blank,
          dataset: { fk: fk('blank') },
          onChange: (v) => ctx.editScene((s) => E.setItemBlank(s, item.id, v), { structural: true }),
        }),
      );
      break;
    case 'misconception': {
      const miscs = ctx.sp.misconceptions || [];
      fields.push(
        textControl('text', 'Misconception (what students wrongly think)', { rows: 2, max: 1200, required: true, hint: RICH_HINT }),
        textControl('justification', 'Correction', { rows: 2, max: 800 }),
        field(
          'Linked misconception',
          select({
            options: [{ value: '', label: '— none —' }, ...miscs.map((/** @type {any} */ m) => ({ value: m.id, label: `${m.id}: ${String(m.statement).slice(0, 50)}` }))],
            value: item.misconception_id || '',
            dataset: { fk: fk('misc') },
            onChange: (v) => patchItem({ misconception_id: v || null }, fk('misc')),
          }),
        ),
      );
      break;
    }
    default:
      fields.push(textControl('text', item.kind === 'heading' ? 'Heading' : 'Text', { rows: item.kind === 'paragraph' ? 3 : 2, max: 1200, required: E.TEXT_REQUIRED_KINDS.has(item.kind), hint: RICH_HINT }));
  }

  const refs = chipsInput({
    separators: /[,\s]+/,
    values: item.source_refs || [],
    label: 'Source chunks',
    placeholder: 'c0001',
    maxItems: 10,
    maxLength: 6,
    validate: (v) => (CHUNK_RE.test(v) ? null : 'Use chunk ids like c0012.'),
    onChange: (v) => patchItem({ source_refs: v }, fk('refs')),
  });

  const openKey = `item:${item.id}`;
  const details = h(
    'details',
    { class: 'board-item', open: ctx.openSections.has(openKey) || ctx.openSections.has('item:all') },
    h('summary', {}, h('span', { class: 'item-kind badge badge-outline' }, E.BOARD_ITEM_LABELS[item.kind] || item.kind), h('span', { class: 'item-summary' }, summaryText(item)), h('span', { class: 'muted small' }, when)),
    h('div', { class: 'item-body' }, h('div', { class: 'row gap wrap' }, field('Kind', kindSel), h('span', { class: 'spacer' }), remove), formGrid(...fields), h('details', { class: 'sub-details' }, h('summary', {}, 'Sources'), group('Source chunks', refs.el)), h('div', { class: 'preview-wrap' }, h('div', { class: 'field-label' }, 'Preview'), preview)),
  );
  details.addEventListener('toggle', () => (details.open ? ctx.openSections.add(openKey) : ctx.openSections.delete(openKey)));
  return details;
}

/** @param {any} item */
function summaryText(item) {
  const t = String(item.term || item.text || item.latex || item.caption || item.code || (item.headers || []).join(' | ') || '').replace(/\s+/g, ' ').trim();
  return t ? (t.length > 70 ? `${t.slice(0, 70)}…` : t) : '(empty)';
}

/**
 * Formula legend editor (symbol, meaning, unit; max 8).
 * @param {InspectorCtx} ctx
 * @param {any} item
 */
function variablesEditor(ctx, item) {
  const vars = item.variables || [];
  // "Appears": a legend row shows when the narration first names it (automatic), or on a chosen beat from the
  // one revealing the formula on (FormulaVariable.beat_id; the timeline ignores, and lint flags, any other).
  const beats = ctx.scene.beats || [];
  const revealAt = beats.findIndex((/** @type {any} */ b) => b.board_item_id === item.id);
  const beatOptions = beats
    .map((/** @type {any} */ b, /** @type {number} */ n) => ({ b, n }))
    .filter(({ n }) => n >= Math.max(0, revealAt))
    .map(({ b, n }) => ({ value: b.id, label: `With beat ${n + 1}${b.narration ? `: ${String(b.narration).slice(0, 40)}${String(b.narration).length > 40 ? '…' : ''}` : ''}` }));
  const rows = vars.map((/** @type {any} */ v, /** @type {number} */ i) => {
    const set = (/** @type {string} */ key, /** @type {string} */ value) =>
      ctx.editScene((s) => {
        const it = s.board.find((/** @type {any} */ x) => x.id === item.id);
        if (it && it.variables[i]) it.variables[i][key] = value;
      }, { coalesce: `item:${item.id}:var:${i}:${key}` });
    const del = button(`Remove variable ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash' });
    del.addEventListener('click', () =>
      ctx.editScene((s) => {
        const it = s.board.find((/** @type {any} */ x) => x.id === item.id);
        if (it) it.variables = it.variables.filter((/** @type {any} */ _x, /** @type {number} */ j) => j !== i);
      }, { structural: true }),
    );
    return h(
      'div',
      { class: 'var-row' },
      input({ value: v.symbol_latex || '', maxLength: 60, ariaLabel: `Symbol (LaTeX) ${i + 1}`, placeholder: 'R', dataset: { fk: `item:${item.id}:var:${i}:sym` }, onInput: (x) => set('symbol_latex', x) }),
      input({ value: v.meaning || '', maxLength: 160, ariaLabel: `Meaning ${i + 1}`, placeholder: 'resistance', dataset: { fk: `item:${item.id}:var:${i}:mean` }, onInput: (x) => set('meaning', x) }),
      input({ value: v.unit || '', maxLength: 40, ariaLabel: `Unit ${i + 1}`, placeholder: 'Ω', dataset: { fk: `item:${item.id}:var:${i}:unit` }, onInput: (x) => set('unit', x) }),
      del,
      select({
        options: [{ value: '', label: 'Appears when the narration names it' }, ...beatOptions],
        value: v.beat_id || '',
        ariaLabel: `When variable ${i + 1} appears`,
        className: 'var-beat',
        dataset: { fk: `item:${item.id}:var:${i}:beat` },
        onChange: (x) =>
          ctx.editScene((s) => {
            const it = s.board.find((/** @type {any} */ y) => y.id === item.id);
            if (!it || !it.variables[i]) return;
            if (x) it.variables[i].beat_id = x;
            else delete it.variables[i].beat_id;
          }, { coalesce: `item:${item.id}:var:${i}:beat` }),
      }),
    );
  });
  const add = button('Add variable', { kind: 'ghost', small: true, icon: 'plus', disabled: vars.length >= MAX_VARIABLES });
  add.addEventListener('click', () =>
    ctx.editScene((s) => {
      const it = s.board.find((/** @type {any} */ x) => x.id === item.id);
      if (it) it.variables = [...(it.variables || []), { symbol_latex: '', meaning: '', unit: '' }];
    }, { structural: true }),
  );
  return group('Variables (legend)', h('div', { class: 'vars' }, rows, add), { hint: 'Symbol in LaTeX, meaning and unit; both symbol and meaning are required. A row appears when the narration first names its meaning (or the symbol’s name), else with the formula; or choose the beat it appears with.' });
}

/**
 * Table grid editor (headers + rows of rich-lite cells).
 * @param {InspectorCtx} ctx
 * @param {any} item
 * @param {(patch: Record<string, any>) => void} onCells  text edits (no re-render)
 */
function tableEditor(ctx, item, onCells) {
  const headers = item.headers || [];
  const rows = item.rows || [];
  /** @param {(it: any) => void} fn */
  const structural = (fn) =>
    ctx.editScene((s) => {
      const it = s.board.find((/** @type {any} */ x) => x.id === item.id);
      if (it) fn(it);
    }, { structural: true });
  /** current cell values for text edits */
  let cur = { headers: headers.slice(), rows: rows.map((/** @type {string[]} */ r) => r.slice()) };
  const table = h(
    'table',
    { class: 'table-editor' },
    h(
      'thead',
      {},
      h(
        'tr',
        {},
        headers.map((/** @type {string} */ c, /** @type {number} */ j) =>
          h(
            'th',
            {},
            input({
              value: c,
              ariaLabel: `Header ${j + 1}`,
              dataset: { fk: `item:${item.id}:h:${j}` },
              onInput: (v) => {
                cur = { ...cur, headers: cur.headers.map((x, k) => (k === j ? v : x)) };
                onCells({ headers: cur.headers.slice() });
              },
            }),
          ),
        ),
        h('th', { class: 'table-tools' }),
      ),
    ),
    h(
      'tbody',
      {},
      rows.map((/** @type {string[]} */ row, /** @type {number} */ i) => {
        const delRow = button(`Remove row ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash', disabled: rows.length <= 1 });
        delRow.addEventListener('click', () => structural((it) => it.rows.splice(i, 1)));
        return h(
          'tr',
          {},
          row.map((c, j) =>
            h(
              'td',
              {},
              input({
                value: c,
                ariaLabel: `Row ${i + 1}, column ${j + 1}`,
                dataset: { fk: `item:${item.id}:c:${i}:${j}` },
                onInput: (v) => {
                  cur = { ...cur, rows: cur.rows.map((r, k) => (k === i ? r.map((x, m) => (m === j ? v : x)) : r)) };
                  onCells({ rows: cur.rows.map((r) => r.slice()) });
                },
              }),
            ),
          ),
          h('td', { class: 'table-tools' }, delRow),
        );
      }),
    ),
  );
  const addRow = button('Add row', { kind: 'ghost', small: true, icon: 'plus', disabled: rows.length >= MAX_TABLE_ROWS });
  addRow.addEventListener('click', () => structural((it) => it.rows.push(new Array(it.headers.length).fill(''))));
  const addCol = button('Add column', { kind: 'ghost', small: true, icon: 'plus', disabled: headers.length >= MAX_TABLE_COLS });
  addCol.addEventListener('click', () =>
    structural((it) => {
      it.headers.push(`Column ${it.headers.length + 1}`);
      it.rows = it.rows.map((/** @type {string[]} */ r) => [...r, '']);
    }),
  );
  const delCol = button('Remove last column', { kind: 'ghost', small: true, icon: 'trash', disabled: headers.length <= 1 });
  delCol.addEventListener('click', () =>
    structural((it) => {
      it.headers.pop();
      it.rows = it.rows.map((/** @type {string[]} */ r) => r.slice(0, it.headers.length));
    }),
  );
  const wrap = h('div', { class: 'table-editor-wrap' }, h('div', { class: 'table-scroll' }, table), h('div', { class: 'row gap wrap' }, addRow, addCol, delCol));
  return group('Table', wrap, { hint: `${RICH_HINT} Up to ${MAX_TABLE_COLS} columns and ${MAX_TABLE_ROWS} rows.` });
}


