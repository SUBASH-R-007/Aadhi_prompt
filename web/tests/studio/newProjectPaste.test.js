import { resetDom, mockFetch, tick, window, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount, cleanPasted, firstLineTitle, pastedFileName, pastedProblem, pasteLimit, MIN_PASTE_CHARS, MAX_PASTE_CHARS } from '../../js/studio/views/newProject.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, byText, waitFor, clickModal, typeInto } from './_views.js';

const CREATED = {
  project: { id: 5, title: 'Photosynthesis' },
  version: { id: 9, number: 1, status: 'generating' },
  job: { id: 21, kind: 'generate_lecture', status: 'queued', stage: '', progress: 0 },
};
const NOTES = `# Photosynthesis\n\n1. Light reactions\n\nChlorophyll absorbs red and blue light inside the chloroplasts of a leaf cell, and the energy is used to split water.\n\n2. The Calvin cycle\n\nCarbon dioxide is fixed into sugar using the energy captured in the light reactions.\n`;

/** jsdom's File has no text(): read it like a browser would. */
function readText(blob) {
  return new Promise((resolve, reject) => {
    const reader = new window.FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsText(blob);
  });
}

function fresh() {
  resetDom();
  closeAllModals();
  FakeEventSource.instances = [];
}

async function mountPaste(app) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: {}, query: {} });
  byText(container, 'Paste your notes', '[role="tab"]').click();
  await tick();
  return { container, handle, notes: container.querySelector('textarea') };
}

/** @param {HTMLElement} container */
function submit(container) {
  container.querySelector('form.new-project-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

test('pure helpers: cleaning, title, file name and refusals', () => {
  const vt = String.fromCharCode(11);
  const bell = String.fromCharCode(7);
  const lineSep = String.fromCharCode(0x2028);
  assert.equal(cleanPasted(`a${vt}b${bell}c\r\nd${lineSep}e`), 'a\nbc\nd\ne');
  assert.equal(firstLineTitle('\n\n# Ohm’s law\nmore'), 'Ohm’s law');
  assert.equal(firstLineTitle('Title: Photosynthesis in Plants\n'), 'Photosynthesis in Plants');
  assert.equal(pastedFileName("Ohm's Law: basics"), 'ohms-law-basics.txt');
  assert.equal(pastedFileName('ஒளிச்சேர்க்கை'), 'pasted-notes.txt');
  assert.match(pastedProblem('', 50), /Paste your notes first/);
  assert.match(pastedProblem('short notes', 50), new RegExp(`${MIN_PASTE_CHARS} characters`));
  assert.match(pastedProblem(`<!DOCTYPE html><html>${'x'.repeat(300)}`, 50), /web page/);
  assert.match(pastedProblem(JSON.stringify({ scenes: [], session_title: 'x'.repeat(300) }), 50), /Import lecture/);
  assert.equal(pastedProblem(NOTES, 50), null);
  assert.match(pastedProblem('é'.repeat(400), 0.0003), /upload limit/);
  assert.match(pastedProblem('word '.repeat(80), 50, 300), /longer than 300 characters/);
  assert.equal(pasteLimit({ limits: { max_source_chars: 50_000 } }), 50_000, 'the server reads less: its limit');
  assert.equal(pasteLimit({ limits: { max_source_chars: 400_000 } }), MAX_PASTE_CHARS);
  assert.equal(pasteLimit({ limits: {} }), MAX_PASTE_CHARS, 'older server: the Studio limit');
});

test('pasted notes are sent as a plain-text file through the normal upload', async () => {
  fresh();
  const calls = mockFetch({ 'POST /api/projects': { status: 201, body: CREATED } });
  const app = fakeApp();
  const { container, handle, notes } = await mountPaste(app);
  typeInto(notes, `${NOTES}${String.fromCharCode(7)}`);
  assert.equal(container.querySelector('input.input').placeholder, 'Photosynthesis', 'the first line suggests the title');
  assert.match(container.textContent, /of 200,000 characters/);
  submit(container);
  await waitFor(() => FakeEventSource.instances.length === 1);
  const post = calls.find((c) => c.path === '/api/projects');
  const file = post.init.body.get('file');
  assert.equal(file.name, 'photosynthesis.txt');
  assert.equal(file.type, 'text/plain');
  const text = await readText(file);
  assert.ok(text.startsWith('# Photosynthesis') && !text.includes(String.fromCharCode(7)), 'control characters are removed');
  assert.equal(post.init.body.get('title'), 'Photosynthesis');
  assert.equal(post.init.body.get('review_source'), null, 'no source review unless asked');
  handle.destroy();
});

test('too little text, a web page or a lecture file is refused under the box', async () => {
  fresh();
  const calls = mockFetch({});
  const app = fakeApp();
  const { container, handle, notes } = await mountPaste(app);
  submit(container);
  await tick();
  assert.equal(notes.getAttribute('aria-invalid'), 'true');
  assert.match(container.querySelector('.start-paste .field-error').textContent, /Paste your notes first/);
  typeInto(notes, 'Just a line.');
  assert.equal(notes.getAttribute('aria-invalid'), null, 'typing clears the error');
  submit(container);
  await tick();
  assert.match(container.querySelector('.start-paste .field-error').textContent, /paragraph or two/);
  typeInto(notes, `<html><body>${'notes '.repeat(80)}</body></html>`);
  submit(container);
  await tick();
  assert.match(container.querySelector('.start-paste .field-error').textContent, /web page/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  handle.destroy();
});

test('notes longer than the limit are refused with a message, never cut silently', async () => {
  fresh();
  const calls = mockFetch({});
  const app = fakeApp({ meta: async () => ({ ...(await fakeApp().meta()), limits: { upload_max_mb: 50, max_source_chars: 300 } }) });
  const { container, handle, notes } = await mountPaste(app);
  assert.equal(notes.hasAttribute('maxlength'), false, 'a maxlength would make the browser cut pasted text without a word');
  typeInto(notes, 'word '.repeat(200));
  assert.match(container.textContent, /1,000 of 300 characters \(too long/);
  submit(container);
  await tick();
  assert.match(container.querySelector('.start-paste .field-error').textContent, /longer than 300 characters/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  handle.destroy();
});

test('pasted notes count for the leave guard', async () => {
  fresh();
  mockFetch({});
  const app = fakeApp();
  const { handle, notes } = await mountPaste(app);
  assert.equal(await app.rec.guard('#/keys'), true);
  typeInto(notes, 'Some notes I do not want to lose');
  const answer = app.rec.guard('#/keys');
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Cancel');
  assert.equal(await answer, false);
  handle.destroy();
});

test('"check how Aadhi read my document" asks for a source review and opens it when the job pauses', async () => {
  fresh();
  const calls = mockFetch({ 'POST /api/projects': { status: 201, body: CREATED } });
  const app = fakeApp();
  const { container, handle, notes } = await mountPaste(app);
  typeInto(notes, NOTES);
  byText(container, 'Let me check how Aadhi read my document', 'label').querySelector('input').click();
  submit(container);
  await waitFor(() => FakeEventSource.instances.length === 1);
  const post = calls.find((c) => c.path === '/api/projects');
  assert.equal(post.init.body.get('review_source'), 'true');
  assert.equal(byText(container, 'Review plan'), null, 'no plan-review link for a source pause');
  FakeEventSource.instances[0].emit('end', { ...CREATED.job, status: 'awaiting_review', progress: 0.08 });
  await waitFor(() => app.rec.navs.length === 1);
  assert.deepEqual(app.rec.navs, ['#/p/5/v/9/source']);
  assert.match(app.rec.toasts.at(-1).message, /Check it before the lecture is planned/);
  handle.destroy();
});
