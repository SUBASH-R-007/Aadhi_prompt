import { resetDom, mockFetch, tick, window, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { mount, EXAMPLE_URL, EXAMPLE_TITLE } from '../../js/studio/views/newProject.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, byText, waitFor, clickModal, typeInto } from './_views.js';

const EXAMPLE_FILE = fileURLToPath(new URL('../../examples/ohms-law.json', import.meta.url));
const example = () => JSON.parse(readFileSync(EXAMPLE_FILE, 'utf8'));
const IMPORTED = {
  project: { id: 12, title: EXAMPLE_TITLE },
  version: { id: 30, number: 1, status: 'building' },
  job: { id: 77, kind: 'build_assets', status: 'queued', stage: '', progress: 0 },
  warnings: [],
};

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

async function mountPage(routes, app = fakeApp()) {
  const calls = mockFetch(routes);
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: {}, query: {} });
  return { container, handle, calls, app };
}

test('the bundled example is a small v2 lecture that needs no paid media', () => {
  assert.equal(EXAMPLE_URL, '/web/examples/ohms-law.json');
  const sp = example();
  assert.equal(sp.schema_version, 2);
  assert.equal(sp.language, 'en-IN');
  assert.ok(sp.scenes.length >= 4 && sp.scenes.length <= 6, `${sp.scenes.length} scenes`);
  assert.ok(sp.scenes.some((s) => s.type === 'quiz_checkpoint'), 'has a quiz');
  for (const s of sp.scenes) {
    assert.ok(!['ai_video', 'simulation', 'interactive'].includes(s.type), `${s.id}: no generated video or animation`);
    if (s.side_panel) assert.ok(!['image', 'gif', 'manim'].includes(s.side_panel.kind), `${s.id}: built-in visual only`);
    assert.ok(!s.override_asset_key && !(s.side_panel && s.side_panel.override_asset_key), `${s.id}: no asset keys`);
  }
  assert.deepEqual(sp.figures, []);
  assert.match(EXAMPLE_TITLE, /^Example: /, 'clearly labelled in the project list');
});

test('"Try an example" imports the bundled lecture and follows its build', async () => {
  fresh();
  const { container, handle, calls, app } = await mountPage({
    [`GET ${EXAMPLE_URL}`]: example(),
    'POST /api/projects/import': { status: 201, body: IMPORTED },
  });
  const panel = container.querySelector('.example-lesson');
  assert.match(panel.textContent, /Try an example/);
  assert.match(panel.textContent, /only the voice-over is made/);
  byText(panel, 'Try the Ohm’s law example', 'button').click();
  await waitFor(() => calls.some((c) => c.path === '/api/projects/import'));
  const post = calls.find((c) => c.path === '/api/projects/import');
  assert.equal(post.method, 'POST');
  const form = post.init.body;
  assert.equal(form.get('title'), EXAMPLE_TITLE);
  const file = form.get('file');
  assert.equal(file.name, 'ohms-law.json');
  assert.equal(file.type, 'application/json');
  assert.deepEqual(JSON.parse(await readText(file)), example());
  await waitFor(() => /Building “Example: Ohm's law”/.test(container.textContent));
  assert.equal(container.querySelector('form.new-project-form').hidden, true);
  assert.equal(panel.hidden, true);
  assert.equal(app.rec.guard, null, 'nothing to lose any more');
  const es = FakeEventSource.instances.at(-1);
  assert.equal(es.url, '/api/jobs/77/stream');
  es.emit('end', { ...IMPORTED.job, status: 'succeeded', progress: 1 });
  await tick();
  assert.deepEqual(app.rec.navs, ['#/p/12']);
  assert.ok(app.rec.toasts.some((t) => /example lecture is ready/.test(t.message)));
  handle.destroy();
});

test('"Try an example" asks before discarding a started lecture, and reports a refused import', async () => {
  fresh();
  const { container, handle, calls, app } = await mountPage({
    [`GET ${EXAMPLE_URL}`]: example(),
    'POST /api/projects/import': { status: 429, body: { detail: 'Your daily AI budget is used up.', code: 'budget' } },
  });
  byText(container, 'Paste your notes', '[role="tab"]').click();
  await tick();
  typeInto(container.querySelector('textarea'), 'Some notes I started typing.');
  byText(container, 'Try the Ohm’s law example', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Cancel');
  await tick(10);
  assert.ok(!calls.some((c) => c.path === EXAMPLE_URL), 'kept the notes');
  byText(container, 'Try the Ohm’s law example', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Discard');
  await waitFor(() => app.rec.errors.length);
  assert.deepEqual(app.rec.errors, ['Could not add the example lecture.']);
  assert.equal(container.querySelector('form.new-project-form').hidden, false, 'the form is back');
  assert.equal(byText(container, 'Try the Ohm’s law example', 'button').disabled, false);
  assert.equal(typeof app.rec.guard, 'function', 'the leave guard is back');
  handle.destroy();
});
