import { resetDom, tick, mockFetch, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { openModal, confirmDialog, promptDialog, closeAllModals } from '../../js/studio/components/modal.js';
import { toast, initToasts } from '../../js/studio/components/toast.js';
import { createTabs } from '../../js/studio/components/tabs.js';
import { chipsInput, chipsSelect } from '../../js/studio/components/chips.js';
import { sortableList, resetSortableState } from '../../js/studio/components/sortable.js';
import { dropZone } from '../../js/studio/components/dropzone.js';
import { menuButton } from '../../js/studio/components/menu.js';
import { jobProgress } from '../../js/studio/components/jobProgress.js';
import { field, input, button, checkbox, select } from '../../js/studio/components/form.js';
import { statusBadge, issueBadges, progressBar } from '../../js/studio/components/badges.js';
import { captureFocus, restoreFocus } from '../../js/studio/components/focusKeep.js';
import { icon, brandMark } from '../../js/studio/components/icons.js';

const key = (el, k, opts = {}) => el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...opts }));

test('field wires labels, hints and errors accessibly', () => {
  resetDom();
  const ctl = input({ value: 'x' });
  const f = field('Title', ctl, { hint: 'Short', required: true });
  document.body.appendChild(f);
  assert.equal(f.querySelector('label').contains(ctl), true);
  const ids = ctl.getAttribute('aria-describedby').split(' ');
  assert.equal(ids.length, 2);
  assert.equal(document.getElementById(ids[0]).textContent, 'Short');
  assert.equal(ctl.getAttribute('aria-required'), 'true');
  f.setError('Bad');
  assert.equal(ctl.getAttribute('aria-invalid'), 'true');
  assert.equal(document.getElementById(ids[1]).textContent, 'Bad');
  f.setError(null);
  assert.equal(ctl.hasAttribute('aria-invalid'), false);
  const s = select({ options: [{ value: 'a', label: 'A' }], value: 'zzz' });
  assert.equal(s.value, 'zzz', 'unknown current values stay visible');
  const cb = checkbox({ label: 'On', checked: true });
  assert.equal(cb.input.checked, true);
  const b = button('Delete', { iconOnly: true, icon: 'trash' });
  assert.equal(b.getAttribute('aria-label'), 'Delete');
});

test('modal traps focus, closes on Escape and restores focus', async () => {
  resetDom();
  const app = document.createElement('div');
  app.setAttribute('data-app-root', '');
  const opener = document.createElement('button');
  app.appendChild(opener);
  document.body.appendChild(app);
  opener.focus();
  const first = input({});
  const m = openModal({ title: 'Hello', body: first, actions: [{ label: 'Cancel', value: 'c' }, { label: 'OK', value: 'ok' }] });
  await tick();
  assert.equal(document.activeElement, first, 'initial focus inside');
  assert.equal(app.hasAttribute('inert'), true);
  const dialog = document.querySelector('[role="dialog"]');
  assert.equal(dialog.getAttribute('aria-modal'), 'true');
  assert.equal(document.getElementById(dialog.getAttribute('aria-labelledby')).textContent, 'Hello');
  // Tab from the last focusable wraps to the first
  const buttons = [...dialog.querySelectorAll('button')];
  buttons.at(-1).focus();
  key(document.activeElement, 'Tab');
  assert.equal(document.activeElement, buttons[0], 'wraps to first focusable (close button)');
  key(document.activeElement, 'Tab', { shiftKey: true });
  assert.equal(document.activeElement, buttons.at(-1));
  key(document.activeElement, 'Escape');
  assert.equal(await m.result, null);
  assert.equal(document.querySelector('[role="dialog"]'), null);
  assert.equal(app.hasAttribute('inert'), false);
  assert.equal(document.activeElement, opener);
});

test('modal actions: async onClick, keep open on false, errors shown', async () => {
  resetDom();
  let attempts = 0;
  const m = openModal({
    title: 'Save',
    body: 'x',
    actions: [
      {
        label: 'Go',
        onClick: async () => {
          attempts += 1;
          if (attempts === 1) throw new Error('Nope');
          if (attempts === 2) return false;
          return { saved: true };
        },
      },
    ],
  });
  const go = [...document.querySelectorAll('.modal-footer button')][0];
  go.click();
  await tick();
  assert.equal(document.querySelector('.modal-error').textContent, 'Nope');
  go.click();
  await tick();
  assert.ok(document.querySelector('[role="dialog"]'), 'still open');
  go.click();
  assert.deepEqual(await m.result, { saved: true });
});

test('confirm and prompt dialogs resolve with the chosen value', async () => {
  resetDom();
  const p = confirmDialog({ title: 'Sure?', message: 'Really', danger: true });
  await tick();
  [...document.querySelectorAll('.modal-footer button')].find((b) => b.textContent === 'Confirm').click();
  assert.equal(await p, true);
  const q = promptDialog({ title: 'Name', label: 'Label', required: true });
  await tick();
  const inp = document.querySelector('.modal input');
  const ok = [...document.querySelectorAll('.modal-footer button')].find((b) => b.textContent === 'OK');
  ok.click();
  await tick();
  assert.equal(document.querySelector('.modal .field-error').textContent, 'This field is required.');
  inp.value = '  Hi  ';
  key(inp, 'Enter');
  assert.equal(await q, 'Hi');
  const c = confirmDialog({ title: 'x', message: 'y' });
  closeAllModals();
  assert.equal(await c, false);
});

test('toasts use a live region and errors are alerts', async () => {
  resetDom();
  const region = initToasts(document.body);
  assert.equal(region.getAttribute('aria-live'), 'polite');
  toast('Saved', { kind: 'success', timeout: 0 });
  const dismiss = toast('Broken', { kind: 'error', timeout: 0, action: { label: 'Retry', onClick: () => {} } });
  assert.equal(region.querySelectorAll('.toast').length, 2);
  assert.equal(region.querySelector('.toast-error').getAttribute('role'), 'alert');
  assert.equal(region.querySelector('.toast-success').getAttribute('role'), 'status');
  dismiss();
  await tick(250);
  assert.equal(region.querySelectorAll('.toast').length, 1);
  for (let i = 0; i < 6; i++) toast(`t${i}`, { timeout: 0 });
  assert.equal(region.querySelectorAll('.toast').length, 4, 'capped');
});

test('tabs: aria wiring, lazy rendering and arrow keys', () => {
  resetDom();
  let renders = 0;
  const changes = [];
  const tabs = createTabs({
    label: 'Sections',
    tabs: [
      { key: 'a', label: 'A', render: () => (renders++, document.createTextNode('pa')) },
      { key: 'b', label: 'B', render: () => (renders++, document.createTextNode('pb')) },
    ],
    onChange: (k) => changes.push(k),
  });
  document.body.appendChild(tabs.el);
  const [ta, tb] = document.querySelectorAll('[role="tab"]');
  assert.equal(ta.getAttribute('aria-selected'), 'true');
  assert.equal(renders, 1);
  key(ta, 'ArrowRight');
  assert.equal(tb.getAttribute('aria-selected'), 'true');
  assert.equal(document.activeElement, tb);
  assert.equal(document.getElementById(tb.getAttribute('aria-controls')).hidden, false);
  key(tb, 'Home');
  key(ta, 'End');
  tabs.select('b');
  assert.equal(renders, 2, 'each panel rendered once');
  tabs.setBadge('b', '3', 'warn');
  assert.equal(tb.querySelector('.tab-badge').textContent, '3');
  assert.deepEqual(changes, ['a', 'b', 'a', 'b']);
});

test('chips input adds, validates and removes values (token lists split on separators)', () => {
  resetDom();
  let values = [];
  const c = chipsInput({ values: ['a'], label: 'Refs', maxItems: 3, separators: /[,\s]+/, validate: (v) => (v.startsWith('c') ? null : 'bad'), onChange: (v) => (values = v) });
  document.body.appendChild(c.el);
  c.input.value = 'c1, c2';
  key(c.input, 'Enter');
  assert.deepEqual(values, ['a', 'c1', 'c2']);
  assert.equal(c.input.disabled, true, 'max reached');
  c.el.querySelector('.chip-remove').click();
  assert.deepEqual(values, ['c1', 'c2']);
  c.input.value = 'zzz';
  key(c.input, 'Enter');
  assert.equal(c.el.querySelector('.field-error').textContent, 'bad');
  c.input.value = '';
  key(c.input, 'Backspace');
  assert.deepEqual(values, ['c1']);
});

test('chips input keeps sentences with commas whole by default (key points)', () => {
  resetDom();
  let values = [];
  const c = chipsInput({ values: [], label: 'Key points', onChange: (v) => (values = v) });
  document.body.appendChild(c.el);
  c.input.value = 'In a series circuit, the current is the same everywhere';
  // A typed comma is text, not a separator.
  const comma = new KeyboardEvent('keydown', { key: ',', bubbles: true, cancelable: true });
  c.input.dispatchEvent(comma);
  assert.equal(comma.defaultPrevented, false);
  assert.deepEqual(values, []);
  key(c.input, 'Enter');
  assert.deepEqual(values, ['In a series circuit, the current is the same everywhere']);
  // Blur commits too, still whole.
  c.input.value = 'Voltage, current and resistance are linked';
  c.input.dispatchEvent(new FocusEvent('blur'));
  assert.deepEqual(values, ['In a series circuit, the current is the same everywhere', 'Voltage, current and resistance are linked']);

  // Token lists: the separator key commits the token.
  let refs = [];
  const r = chipsInput({ values: [], label: 'Refs', separators: /[,\s]+/g, onChange: (v) => (refs = v) });
  document.body.appendChild(r.el);
  r.input.value = 'c0001';
  const sep = new KeyboardEvent('keydown', { key: ',', bubbles: true, cancelable: true });
  r.input.dispatchEvent(sep);
  assert.equal(sep.defaultPrevented, true);
  r.input.value = 'c0002';
  key(r.input, ' ');
  assert.deepEqual(refs, ['c0001', 'c0002'], 'a global regex is used statelessly');
});

test('chips select respects the maximum and exposes aria-pressed', () => {
  resetDom();
  let values = [];
  const c = chipsSelect({ options: [{ value: 'x', label: 'X' }, { value: 'y', label: 'Y' }, { value: 'z', label: 'Z' }], values: ['x', 'ghost'], max: 2, label: 'H', onChange: (v) => (values = v) });
  document.body.appendChild(c.el);
  let buttons = c.el.querySelectorAll('button');
  assert.equal(buttons[0].getAttribute('aria-pressed'), 'true');
  buttons[1].click();
  assert.deepEqual(values, ['x', 'y']);
  buttons = c.el.querySelectorAll('button');
  assert.equal(buttons[2].disabled, true, 'third option disabled at max');
  buttons[0].click();
  assert.deepEqual(values, ['y']);
  const empty = chipsSelect({ options: [], values: [], label: 'E', emptyText: 'none', onChange: () => {} });
  assert.equal(empty.el.textContent, 'none');
});

test('sortable list: keyboard pick-up, move, drop, cancel and Alt+Arrow', () => {
  resetDom();
  let items = ['a', 'b', 'c'];
  const moves = [];
  const list = sortableList({
    items,
    key: (x) => x,
    name: (x) => `item ${x}`,
    label: 'Things',
    render: (x) => document.createTextNode(x),
    onMove: (from, to) => {
      moves.push([from, to]);
      const next = items.slice();
      const [it] = next.splice(from, 1);
      next.splice(to, 0, it);
      items = next;
      list.update(items);
    },
  });
  document.body.appendChild(list.el);
  const handle = (k) => [...list.el.querySelectorAll('.drag-handle')].find((b) => b.dataset.sortKey === k);
  handle('a').focus();
  key(handle('a'), ' ');
  assert.equal(handle('a').getAttribute('aria-pressed'), 'true');
  assert.equal(document.activeElement, handle('a'));
  key(handle('a'), 'ArrowDown');
  key(document.activeElement, 'ArrowDown');
  assert.deepEqual(items, ['b', 'c', 'a']);
  assert.equal(document.activeElement, handle('a'), 'focus follows the moved item');
  key(document.activeElement, 'Escape');
  assert.deepEqual(items, ['a', 'b', 'c'], 'escape returns to the origin');
  key(handle('c'), 'ArrowUp', { altKey: true });
  assert.deepEqual(items, ['a', 'c', 'b']);
  assert.match(list.el.querySelector('[aria-live]').textContent, /moved to position 2 of 3/);
  key(handle('a'), ' ');
  key(handle('a'), 'ArrowUp');
  key(handle('a'), 'Enter');
  assert.equal(handle('a').getAttribute('aria-pressed'), 'false');
  assert.ok(moves.length >= 4);
});

test('sortable list keeps the keyboard grab when the owner re-renders a new list (stateKey)', async () => {
  resetDom();
  resetSortableState();
  let items = ['a', 'b', 'c', 'd'];
  const host = document.createElement('div');
  document.body.appendChild(host);
  /** Owner that rebuilds the whole list on every change, like the editor inspector. */
  const renderOwner = () => {
    const snap = captureFocus(host);
    const list = sortableList({
      items,
      key: (x) => x,
      name: (x) => `item ${x}`,
      label: 'Beats',
      stateKey: 'beats:s1:main',
      render: (x) => document.createTextNode(x),
      onMove: (from, to) => {
        const next = items.slice();
        const [it] = next.splice(from, 1);
        next.splice(to, 0, it);
        items = next;
        renderOwner();
      },
    });
    host.replaceChildren(list.el);
    restoreFocus(host, snap);
  };
  renderOwner();
  const handle = (k) => [...host.querySelectorAll('.drag-handle')].find((b) => b.dataset.sortKey === k);
  assert.equal(handle('a').dataset.fk, 'beats:s1:main:a', 'handles carry a focus key');
  handle('b').focus();
  key(handle('b'), ' ');
  key(document.activeElement, 'ArrowDown');
  assert.deepEqual(items, ['a', 'c', 'b', 'd']);
  assert.equal(document.activeElement, handle('b'), 'focus restored to the moved item in the new list');
  assert.equal(handle('b').getAttribute('aria-pressed'), 'true', 'still picked up after the re-render');
  key(document.activeElement, 'ArrowDown');
  assert.deepEqual(items, ['a', 'c', 'd', 'b']);
  await tick(80);
  assert.match(host.querySelector('[aria-live]').textContent, /item b moved to position 4 of 4/);
  key(document.activeElement, 'Escape');
  assert.deepEqual(items, ['a', 'b', 'c', 'd'], 'escape returns to the original position across re-renders');
  assert.equal(handle('b').getAttribute('aria-pressed'), 'false');
  // Alt+Arrow: immediate move, focus follows, nothing stays picked up.
  handle('d').focus();
  key(handle('d'), 'ArrowUp', { altKey: true });
  assert.deepEqual(items, ['a', 'b', 'd', 'c']);
  assert.equal(document.activeElement, handle('d'));
  assert.equal(handle('d').getAttribute('aria-pressed'), 'false');
  // Moving focus away while picked up drops the item where it is.
  key(handle('d'), ' ');
  assert.equal(handle('d').getAttribute('aria-pressed'), 'true');
  const outside = document.createElement('button');
  document.body.appendChild(outside);
  outside.focus();
  await tick(5);
  assert.equal(handle('d').getAttribute('aria-pressed'), 'false');
  renderOwner();
  assert.equal(handle('d').getAttribute('aria-pressed'), 'false', 'a dropped grab is not resumed by later renders');
});

test('sortable list refocuses the moved item when the owner re-renders without focusKeep', async () => {
  resetDom();
  resetSortableState();
  let items = ['x', 'y'];
  const host = document.createElement('div');
  document.body.appendChild(host);
  const renderOwner = () => {
    const list = sortableList({
      items,
      key: (v) => v,
      name: (v) => v,
      label: 'L',
      stateKey: 'board:s9',
      render: (v) => document.createTextNode(v),
      onMove: (from, to) => {
        const next = items.slice();
        next.splice(to, 0, next.splice(from, 1)[0]);
        items = next;
        renderOwner();
      },
    });
    host.replaceChildren(list.el);
  };
  renderOwner();
  const handle = (k) => [...host.querySelectorAll('.drag-handle')].find((b) => b.dataset.sortKey === k);
  handle('y').focus();
  key(handle('y'), 'ArrowUp', { altKey: true });
  assert.deepEqual(items, ['y', 'x']);
  await tick(0);
  assert.equal(document.activeElement, handle('y'));
  // A stale hand-off (owner never re-rendered) is ignored after it expires.
  resetSortableState();
  renderOwner();
  assert.equal(handle('y').getAttribute('aria-pressed'), 'false');
});

test('drop zone validates files and reports selection', () => {
  resetDom();
  const chosen = [];
  const dz = dropZone({ accept: ['.pdf'], label: 'Drop', validate: (f) => (f.name.endsWith('.pdf') ? null : 'PDF only'), onFile: (f) => chosen.push(f) });
  document.body.appendChild(dz.el);
  const zone = dz.el.querySelector('.dropzone');
  const drop = (files) => {
    const ev = new Event('drop', { bubbles: true, cancelable: true });
    ev.dataTransfer = { files };
    zone.dispatchEvent(ev);
  };
  drop([new File(['x'], 'a.txt')]);
  assert.equal(dz.el.querySelector('.field-error').textContent, 'PDF only');
  drop([new File(['x'], 'a.pdf'), new File(['y'], 'b.pdf')]);
  assert.match(dz.el.querySelector('.field-error').textContent, /one file/);
  drop([new File(['%PDF'], 'notes.pdf')]);
  assert.equal(chosen.length, 1);
  assert.equal(dz.file().name, 'notes.pdf');
  assert.match(dz.el.querySelector('.file-name').textContent, /notes\.pdf/);
  dz.el.querySelector('.file-pill button').click();
  assert.equal(dz.file(), null);
  assert.equal(chosen.at(-1), null);
  dz.setDisabled(true);
  drop([new File(['%PDF'], 'x.pdf')]);
  assert.equal(dz.file(), null, 'disabled zone ignores drops');
});

test('menu button: keyboard open, navigation and escape', () => {
  resetDom();
  const clicked = [];
  const m = menuButton('More', [{ label: 'One', onClick: () => clicked.push(1) }, { label: 'Two', disabled: true }, { label: 'Three', onClick: () => clicked.push(3) }]);
  document.body.appendChild(m.el);
  const trigger = m.el.querySelector('button[aria-haspopup="menu"]');
  key(trigger, 'ArrowDown');
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');
  const items = [...m.el.querySelectorAll('[role="menuitem"]')];
  assert.equal(document.activeElement, items[0]);
  key(items[0], 'ArrowDown');
  assert.equal(document.activeElement, items[2], 'disabled item skipped');
  key(items[2], 'Escape');
  assert.equal(trigger.getAttribute('aria-expanded'), 'false');
  assert.equal(document.activeElement, trigger);
  trigger.click();
  items[2].click();
  assert.deepEqual(clicked, [3]);
  m.destroy();
});

test('badges and progress bar carry text and aria values', () => {
  assert.equal(statusBadge('awaiting_review').textContent, 'Awaiting review');
  assert.equal(issueBadges({ error: 2, warning: 1, info: 3 }).textContent, '2 errors1 warning3 notes');
  assert.equal(issueBadges({}, { showClean: true }).textContent, 'No issues');
  const bar = progressBar(0.456, 'Upload');
  assert.equal(bar.getAttribute('aria-valuenow'), '46');
  assert.equal(bar.getAttribute('role'), 'progressbar');
});

test('focusKeep restores focus and caret by data-fk', () => {
  resetDom();
  const host = document.createElement('div');
  document.body.appendChild(host);
  const a = input({ value: 'hello', dataset: { fk: 'k1' } });
  host.appendChild(a);
  a.focus();
  a.setSelectionRange(2, 3);
  const snap = captureFocus(host);
  host.textContent = '';
  const b = input({ value: 'hello', dataset: { fk: 'k1' } });
  host.appendChild(b);
  restoreFocus(host, snap);
  assert.equal(document.activeElement, b);
  assert.equal(b.selectionStart, 2);
  assert.equal(captureFocus(document.createElement('div')), null);
});

test('job progress follows SSE events and fires callbacks once', async () => {
  resetDom();
  FakeEventSource.instances = [];
  const calls = [];
  const job = { id: 5, kind: 'generate_lecture', status: 'running', stage: 'plan', progress: 0.1, message: 'Planning', cost_usd: 0 };
  const w = jobProgress(job, { onSuccess: (j) => calls.push(['success', j.status]), onFinished: (j) => calls.push(['finished', j.status]) });
  document.body.appendChild(w.el);
  const es = FakeEventSource.instances[0];
  assert.equal(es.url, '/api/jobs/5/stream');
  es.emit('job_event', { id: 1, job_id: 5, created_at: new Date().toISOString(), level: 'info', stage: 'plan', message: 'Plan ready', progress: 0.18 });
  es.emit('job_event', { id: 1, job_id: 5, created_at: new Date().toISOString(), level: 'info', stage: 'plan', message: 'dup', progress: 0.18 });
  es.emit('job', { ...job, stage: 'script', progress: 0.3, message: 'Writing scene 2/5', cost_usd: 0.12 });
  assert.equal(w.el.querySelectorAll('.log-line').length, 1, 'events de-duplicated');
  assert.match(w.el.querySelector('.job-message').textContent, /Writing scene 2\/5/);
  assert.equal(w.el.querySelector('.step-active .step-label').textContent, 'Write scenes');
  assert.equal(w.el.querySelector('[role="progressbar"]').getAttribute('aria-valuenow'), '30');
  assert.equal(w.el.querySelector('.job-cost').textContent, '$0.12');
  es.emit('end', { ...job, status: 'succeeded', progress: 1, stage: 'timeline' });
  es.emit('job', { ...job, status: 'succeeded', progress: 1 });
  await tick();
  assert.deepEqual(calls, [['success', 'succeeded'], ['finished', 'succeeded']]);
  assert.equal(es.closed, true);
  w.destroy();
});

test('job progress falls back to polling when the stream is refused, and supports cancel/retry', async () => {
  resetDom();
  FakeEventSource.instances = [];
  let status = 'running';
  const fetchCalls = mockFetch({
    'GET /api/jobs/9': () => ({ id: 9, kind: 'build_assets', status, stage: 'assets', progress: 0.5, message: 'Voicing' }),
    're:GET /api/jobs/9/events': { items: [{ id: 3, created_at: new Date().toISOString(), level: 'warning', stage: 'assets', message: 'Slow TTS' }] },
    'POST /api/jobs/9/cancel': () => {
      status = 'cancelled';
      return { status: 202, body: { job: { id: 9, kind: 'build_assets', status: 'cancelled', stage: 'assets', progress: 0.5 } } };
    },
    'POST /api/jobs/9/retry': { status: 202, body: { job: { id: 10, kind: 'build_assets', status: 'queued', stage: '', progress: 0 } } },
  });
  const replaced = [];
  const finished = [];
  const w = jobProgress({ id: 9, kind: 'build_assets', status: 'running', stage: 'assets', progress: 0.4 }, { pollMs: 20, onReplaced: (j) => replaced.push(j.id), onFinished: (j) => finished.push(j.status) });
  document.body.appendChild(w.el);
  FakeEventSource.instances[0].fail();
  await tick(60);
  assert.ok(fetchCalls.some((c) => c.path === '/api/jobs/9'));
  assert.equal(w.el.querySelectorAll('.log-line').length, 1);
  // cancel (confirm dialog)
  [...w.el.querySelectorAll('.job-actions button')].find((b) => b.textContent.includes('Cancel')).click();
  await tick();
  [...document.querySelectorAll('.modal-footer button')].find((b) => b.textContent === 'Cancel job').click();
  await tick(30);
  assert.deepEqual(finished, ['cancelled']);
  assert.equal(fetchCalls.find((c) => c.path === '/api/jobs/9/cancel').init.headers['X-Aadhi-CSRF'], '1');
  // retry swaps to the new job and reconnects
  [...w.el.querySelectorAll('.job-actions button')].find((b) => b.textContent.includes('Retry')).click();
  await tick(10);
  assert.deepEqual(replaced, [10]);
  assert.equal(FakeEventSource.instances.at(-1).url, '/api/jobs/10/stream');
  w.destroy();
});

test('job progress shows a review link while awaiting review and does not fire for already-finished jobs', async () => {
  resetDom();
  FakeEventSource.instances = [];
  const calls = [];
  const w = jobProgress({ id: 3, kind: 'generate_lecture', status: 'awaiting_review', stage: 'plan', progress: 0.18 }, { reviewHref: '#/p/1/v/2/plan', onAwaitingReview: () => calls.push('review') });
  assert.equal(FakeEventSource.instances.length, 0, 'no stream for paused jobs');
  assert.equal(w.el.querySelector('a.btn').getAttribute('href'), 'http://localhost/#/p/1/v/2/plan');
  await tick();
  assert.deepEqual(calls, []);
  const w2 = jobProgress({ id: 4, kind: 'render_video', status: 'failed', error: 'ffmpeg died', error_code: 'failed' }, { fireInitial: true, onFinished: () => calls.push('fin') });
  await tick();
  assert.deepEqual(calls, ['fin']);
  assert.equal(w2.el.querySelector('.job-error').textContent, 'ffmpeg died');
  w.destroy();
  w2.destroy();
});

test('icons keep their case-sensitive SVG viewBox (dom.js lower-cases attribute names)', () => {
  const close = icon('close', { size: 14 });
  assert.equal(close.getAttribute('viewBox'), '0 0 24 24');
  assert.equal(close.getAttribute('aria-hidden'), 'true');
  assert.equal(close.querySelectorAll('path').length, 2);
  assert.equal(brandMark(40).getAttribute('viewBox'), '0 0 48 48');
});

test('modal: while an action runs, Escape/close/backdrop do not dismiss it; the result still arrives', async () => {
  resetDom();
  closeAllModals();
  let release;
  const work = new Promise((r) => (release = r));
  const m = openModal({
    title: 'Busy',
    body: 'Working',
    actions: [
      { label: 'Cancel', value: null },
      { label: 'Go', kind: 'gold', onClick: () => work },
    ],
  });
  [...m.el.querySelectorAll('.modal-footer button')].find((b) => b.textContent.includes('Go')).click();
  await tick();
  assert.equal(m.el.getAttribute('aria-busy'), 'true');
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  m.el.querySelector('.modal-close').click();
  m.el.parentElement.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
  assert.ok(m.el.isConnected, 'not dismissed while busy');
  assert.equal(m.el.querySelector('.modal-close').disabled, true);
  release({ job: { id: 1 } });
  assert.deepEqual(await m.result, { job: { id: 1 } });
  assert.equal(m.el.isConnected, false);

  // Programmatic close (route change) still works while busy.
  const m2 = openModal({ title: 'Busy 2', body: 'x', actions: [{ label: 'Go', onClick: () => new Promise(() => {}) }] });
  m2.el.querySelector('.modal-footer button').click();
  await tick();
  closeAllModals();
  assert.equal(await m2.result, null);
  // Not busy: Escape dismisses as before.
  const m3 = openModal({ title: 'Idle', body: 'x', actions: [{ label: 'OK', value: true }] });
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert.equal(await m3.result, null);
});
