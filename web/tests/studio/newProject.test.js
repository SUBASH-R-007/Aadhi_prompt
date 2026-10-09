import { resetDom, mockFetch, tick, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount } from '../../js/studio/views/newProject.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, deferred, byText, waitFor, clickModal, typeInto } from './_views.js';

const CREATED = {
  project: { id: 5, title: "Ohm's Law" },
  version: { id: 9, number: 1, status: 'generating' },
  job: { id: 21, kind: 'generate_lecture', status: 'queued', stage: '', progress: 0 },
};

/** Mount the page and choose a PDF in the drop zone. */
async function mountWithFile(app) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: {}, query: {} });
  const zone = container.querySelector('.dropzone');
  const ev = new Event('drop', { bubbles: true, cancelable: true });
  ev.dataTransfer = { files: [new File(['%PDF-1.4'], 'ohm.pdf', { type: 'application/pdf' })] };
  zone.dispatchEvent(ev);
  return { container, handle };
}

/** @param {HTMLElement} container */
function submit(container) {
  container.querySelector('form.new-project-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

function fresh() {
  resetDom();
  closeAllModals();
  FakeEventSource.instances = [];
}

test('upload starts generation, follows the job live and opens the editor on success', async () => {
  fresh();
  const calls = mockFetch({ 'POST /api/projects': { status: 201, body: CREATED } });
  const app = fakeApp();
  const { container, handle } = await mountWithFile(app);
  submit(container);
  await waitFor(() => FakeEventSource.instances.length === 1);
  const post = calls.find((c) => c.path === '/api/projects');
  assert.ok(post.init.body instanceof FormData);
  assert.equal(post.init.body.get('file').name, 'ohm.pdf');
  const options = JSON.parse(post.init.body.get('options'));
  assert.equal(options.language, 'en-IN');
  assert.equal(container.querySelector('form.new-project-form').hidden, true);
  assert.match(container.querySelector('.progress-host').textContent, /Generating “Ohm's Law”/);
  const es = FakeEventSource.instances[0];
  assert.equal(es.url, '/api/jobs/21/stream');
  es.emit('end', { ...CREATED.job, status: 'succeeded', progress: 1 });
  await tick();
  assert.deepEqual(app.rec.navs, ['#/p/5/v/9/edit']);
  handle.destroy();
  assert.equal(es.closed, true);
});

test('leaving the page during the upload: no job stream, no later navigation, a toast links to the project', async () => {
  fresh();
  const upload = deferred();
  mockFetch({ 'POST /api/projects': () => upload.promise });
  const app = fakeApp();
  const { container, handle } = await mountWithFile(app);
  submit(container);
  await tick();
  handle.destroy(); // the user navigated away while the request was in flight
  upload.resolve({ status: 201, body: CREATED });
  await tick(20);
  assert.equal(FakeEventSource.instances.length, 0, 'no SSE slot is held by a destroyed page');
  assert.deepEqual(app.rec.navs, [], 'the user is never pulled back');
  const note = app.rec.toasts.find((t) => /is being generated/.test(t.message));
  assert.ok(note && note.action, 'a toast offers the project page');
  note.action.onClick();
  assert.deepEqual(app.rec.navs, ['#/p/5']);
});

test('a failed upload re-enables the form but keeps server-disabled options disabled', async () => {
  fresh();
  mockFetch({ 'POST /api/projects': { status: 413, body: { detail: 'File too large', code: 'too_large' } } });
  const app = fakeApp();
  const { container, handle } = await mountWithFile(app);
  const gifs = byText(container, 'Reaction GIFs', 'label').querySelector('input');
  const quizzes = byText(container, 'Include quiz checkpoints', 'label').querySelector('input');
  assert.equal(gifs.disabled, true, 'GIFs are not configured on this server');
  submit(container);
  await waitFor(() => app.rec.errors.length === 1);
  assert.equal(app.rec.errors[0], 'Upload failed.');
  assert.equal(quizzes.disabled, false, 'ordinary options are editable again');
  assert.equal(gifs.disabled, true, 'feature-gated options stay disabled');
  assert.equal(container.querySelector('form.new-project-form button[type="submit"]').disabled, false);
  handle.destroy();
});

test('leaving a filled-in form asks first; Cancel keeps the form, an untouched form leaves freely', async () => {
  fresh();
  mockFetch({});
  const app = fakeApp();
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: {}, query: {} });
  assert.equal(typeof app.rec.guard, 'function', 'a leave guard is installed');
  assert.equal(await app.rec.guard('#/keys'), true, 'nothing entered yet: no question');
  typeInto(container.querySelector('input.input'), 'My lecture');
  let answer = app.rec.guard('#/keys');
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal-title').textContent, /Discard this lecture\?/);
  clickModal('Cancel');
  assert.equal(await answer, false, 'Cancel stays on the page');
  assert.equal(container.querySelector('input.input').value, 'My lecture', 'the form is kept');
  answer = app.rec.guard('#/keys');
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Discard');
  assert.equal(await answer, true);
  handle.destroy();
  assert.equal(app.rec.guard, null, 'the guard is removed with the page');
});

test('a chosen file also counts; the guard is lifted while uploading and back after a failed upload', async () => {
  fresh();
  const upload = deferred();
  mockFetch({ 'POST /api/projects': () => upload.promise });
  const app = fakeApp();
  const { container, handle } = await mountWithFile(app);
  const answer = app.rec.guard('#/projects');
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Cancel');
  assert.equal(await answer, false, 'a chosen file would be lost');
  submit(container);
  await tick();
  assert.equal(app.rec.guard, null, 'leaving during the upload is allowed (a toast links to the project)');
  upload.resolve({ status: 500, body: { detail: 'boom', code: 'internal' } });
  await waitFor(() => app.rec.errors.length === 1);
  assert.equal(typeof app.rec.guard, 'function', 'the form is editable again, so it is guarded again');
  handle.destroy();
});

test('a failed upload after leaving the page is still reported', async () => {
  fresh();
  const upload = deferred();
  mockFetch({ 'POST /api/projects': () => upload.promise });
  const app = fakeApp();
  const { container, handle } = await mountWithFile(app);
  submit(container);
  await tick();
  handle.destroy();
  upload.resolve({ status: 500, body: { detail: 'boom', code: 'internal' } });
  await tick(20);
  assert.deepEqual(app.rec.errors, ['Uploading the new lecture failed.']);
  assert.equal(FakeEventSource.instances.length, 0);
});
