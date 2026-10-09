import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { startStudio } from '../../js/studio/app.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { draftKey } from '../../js/studio/lib/draftStore.js';
import { sampleMeta, sampleUser, sampleVersion, sampleScreenplay } from './fixtures.js';
import { deferred, byText, waitFor } from './_views.js';

function fresh(hash) {
  closeAllModals();
  resetDom();
  localStorage.clear();
  window.history.replaceState(null, '', hash);
}

const draft = (title) => JSON.stringify({ revision: 4, savedAt: new Date().toISOString(), screenplay: { ...sampleScreenplay(), session_title: title }, base: null });

test('leaving the editor while it loads: the stale mount sets no leave guard and opens no dialog', async () => {
  fresh('#/p/5/v/9/edit');
  localStorage.setItem('aadhi.studio.lastUser', '7');
  localStorage.setItem(draftKey(7, 9), draft('Unsaved'));
  const slow = deferred();
  mockFetch({
    'GET /api/auth/me': { user: sampleUser() },
    'GET /api/meta': sampleMeta(),
    'GET /api/versions/9': () => slow.promise,
    'GET /api/projects?limit=24&offset=0': { items: [], total: 0 },
  });
  const root = document.createElement('div');
  document.body.appendChild(root);
  const { app, router, dispose } = await startStudio(root);
  await tick(20);
  app.navigate('#/projects');
  await waitFor(() => /No lectures yet/.test(root.textContent));
  slow.resolve(sampleVersion());
  await tick(50);
  assert.equal(router.guard, null, 'no guard from the abandoned editor');
  assert.equal(document.querySelector('.modal'), null, 'no restore dialog over the projects page');
  assert.ok(localStorage.getItem(draftKey(7, 9)), 'the draft is untouched');
  assert.equal(location.hash, '#/projects');
  dispose();
});

test('an abandoned editor mount cannot touch the guard of the editor that replaced it', async () => {
  fresh('#/p/5/v/9/edit');
  localStorage.setItem('aadhi.studio.lastUser', '7');
  const slow9 = deferred();
  mockFetch({
    'GET /api/auth/me': { user: sampleUser() },
    'GET /api/meta': sampleMeta(),
    'GET /api/versions/9': () => slow9.promise,
    'GET /api/versions/10': sampleVersion({ id: 10 }),
    'POST /api/versions/10/lint': { issues: [] },
    'POST /api/versions/10/timeline/preview': { status: 404, body: { detail: 'x', code: 'not_found' } },
    'GET /api/jobs?project_id=5&limit=20': { items: [], total: 0 },
  });
  const root = document.createElement('div');
  document.body.appendChild(root);
  const { app, router, dispose } = await startStudio(root);
  await tick(20);
  app.navigate('#/p/5/v/10/edit');
  await waitFor(() => root.querySelector('.editor'));
  const guard10 = router.guard;
  assert.equal(typeof guard10, 'function', 'editor 10 installed its guard');
  slow9.resolve(sampleVersion());
  await tick(50);
  assert.equal(router.guard, guard10, "editor 9's late mount neither replaced nor cleared it");
  assert.equal(document.querySelectorAll('.editor').length, 1);
  dispose();
});

test('sign-out removes every local draft; a different user signing in removes the previous user’s', async () => {
  fresh('#/projects');
  // Previous user (id 3) left drafts on this PC; legacy unscoped drafts too.
  localStorage.setItem('aadhi.studio.lastUser', '3');
  localStorage.setItem(draftKey(3, 9), draft('Teacher three notes'));
  localStorage.setItem('aadhi.studio.draft.v9', draft('legacy'));
  localStorage.setItem('aadhi.studio.pref.previewAuto', 'true');
  const calls = mockFetch({
    'GET /api/auth/me': { user: sampleUser({ id: 7 }) },
    'GET /api/meta': sampleMeta(),
    'GET /api/projects?limit=24&offset=0': { items: [], total: 0 },
    'POST /api/auth/logout': { status: 204, body: null },
  });
  const root = document.createElement('div');
  document.body.appendChild(root);
  const { dispose } = await startStudio(root);
  await waitFor(() => /No lectures yet/.test(root.textContent));
  assert.equal(localStorage.getItem(draftKey(3, 9)), null, "user 3's draft is gone");
  assert.equal(localStorage.getItem('aadhi.studio.draft.v9'), null, 'legacy draft is gone');
  assert.equal(localStorage.getItem('aadhi.studio.lastUser'), '7');
  assert.equal(localStorage.getItem('aadhi.studio.pref.previewAuto'), 'true', 'preferences stay');

  localStorage.setItem(draftKey(7, 9), draft('Mine'));
  byText(root, 'teacher1', 'button').click();
  await tick();
  byText(document.body, 'Sign out', '[role="menuitem"]').click();
  await waitFor(() => calls.some((c) => c.path === '/api/auth/logout'));
  await waitFor(() => location.hash === '#/login');
  assert.equal(localStorage.getItem(draftKey(7, 9)), null, 'nothing of the lecture stays after sign-out');
  dispose();
});
