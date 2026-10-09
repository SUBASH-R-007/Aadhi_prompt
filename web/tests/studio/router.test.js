import { window, tick } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHash, matchRoute, resolveHash, href, guardRoute, HashRouter, ROUTES, DEFAULT_ROUTE } from '../../js/studio/router.js';

/**
 * Wait until `cond()` holds (history traversals in jsdom are asynchronous and slower under a
 * parallel test run), then let follow-up events settle.
 * @param {() => boolean} cond
 * @param {number} [ms]
 */
async function waitFor(cond, ms = 3000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('waitFor timed out');
    await tick(5);
  }
  await tick(10);
}

test('parseHash splits path and query', () => {
  assert.deepEqual(parseHash('#/p/3?tab=renders&x=1'), { path: '/p/3', query: { tab: 'renders', x: '1' } });
  assert.deepEqual(parseHash('#/projects'), { path: '/projects', query: {} });
  assert.deepEqual(parseHash(''), { path: '', query: {} });
  assert.deepEqual(parseHash('#'), { path: '', query: {} });
});

test('parseHash normalises slashes and ignores in-page anchors', () => {
  assert.equal(parseHash('#/projects/').path, '/projects');
  assert.equal(parseHash('#//p//3').path, '/p/3');
  assert.equal(parseHash('#main').path, null);
});

test('parseHash decodes query values', () => {
  assert.deepEqual(parseHash('#/projects?q=ohm%27s%20law').query, { q: "ohm's law" });
});

test('matchRoute matches every documented route', () => {
  const cases = [
    ['/login', 'login', {}],
    ['/change-password', 'changePassword', {}],
    ['/projects', 'projects', {}],
    ['/new', 'newProject', {}],
    ['/p/12', 'project', { id: 12 }],
    ['/p/12/v/7/plan', 'planReview', { id: 12, vid: 7 }],
    ['/p/12/v/7/edit', 'editor', { id: 12, vid: 7 }],
    ['/p/12/v/7/source', 'sourceReport', { id: 12, vid: 7 }],
    ['/v/7/review', 'visualReview', { vid: 7 }],
    ['/p/12/analytics', 'analytics', { id: 12 }],
    ['/usage', 'usage', {}],
    ['/keys', 'apiKeys', {}],
    ['/library', 'library', {}],
    ['/videos', 'videos', {}],
    ['/admin', 'admin', {}],
  ];
  for (const [path, name, params] of cases) {
    const m = matchRoute(path);
    assert.ok(m, `no match for ${path}`);
    assert.equal(m.route.name, name);
    assert.deepEqual(m.params, params);
  }
});

test('matchRoute rejects malformed ids and unknown paths', () => {
  for (const path of ['/p/0', '/p/-1', '/p/abc', '/p/01', '/p/1.5', '/p/1/v/x/edit', '/p/1/v/2', '/nope', '/p', '/p/1/edit', '/p/99999999999999999']) {
    assert.equal(matchRoute(path), null, path);
  }
});

test('resolveHash returns canonical hash and params', () => {
  const r = resolveHash('#/p/3/v/9/edit?scene=s2');
  assert.equal(r.route.name, 'editor');
  assert.deepEqual(r.params, { id: 3, vid: 9 });
  assert.deepEqual(r.query, { scene: 's2' });
  assert.equal(r.hash, '#/p/3/v/9/edit?scene=s2');
  assert.equal(resolveHash('#/unknown'), null);
  assert.equal(resolveHash(''), null);
});

test('href builds hashes and validates params', () => {
  assert.equal(href('editor', { id: 3, vid: 9 }), '#/p/3/v/9/edit');
  assert.equal(href('projects', {}, { q: 'ohm law', empty: '', n: null }), '#/projects?q=ohm+law');
  assert.equal(href('analytics', { id: 5 }, { version_id: 2 }), '#/p/5/analytics?version_id=2');
  assert.throws(() => href('editor', { id: 3 }));
  assert.throws(() => href('project', { id: 'x' }));
  assert.throws(() => href('nope'));
  // round trip
  for (const r of ROUTES) {
    const params = {};
    for (const m of r.pattern.matchAll(/:([a-z]+)/g)) params[m[1]] = 4;
    assert.equal(resolveHash(href(r.name, params)).route.name, r.name);
  }
});

test('guardRoute enforces auth, pending password and roles', () => {
  const login = ROUTES.find((r) => r.name === 'login');
  const admin = ROUTES.find((r) => r.name === 'admin');
  const projects = ROUTES.find((r) => r.name === 'projects');
  const change = ROUTES.find((r) => r.name === 'changePassword');
  const editor = { role: 'editor', must_change_password: false };
  const pending = { role: 'editor', must_change_password: true };
  const boss = { role: 'admin', must_change_password: false };
  assert.deepEqual(guardRoute(projects, null), { redirect: '#/login' });
  assert.deepEqual(guardRoute(login, null), { ok: true });
  assert.deepEqual(guardRoute(login, editor), { redirect: DEFAULT_ROUTE });
  assert.deepEqual(guardRoute(login, pending), { redirect: '#/change-password' });
  assert.deepEqual(guardRoute(projects, pending), { redirect: '#/change-password' });
  assert.deepEqual(guardRoute(change, pending), { ok: true });
  assert.deepEqual(guardRoute(admin, editor), { redirect: DEFAULT_ROUTE });
  assert.deepEqual(guardRoute(admin, boss), { ok: true });
});

test('HashRouter routes a fresh visit with an empty hash (regression: app stuck on "Starting…")', async () => {
  window.history.replaceState(null, '', '/');
  assert.equal(window.location.hash, '');
  const seen = [];
  const router = new HashRouter({ onRoute: (r, hash) => seen.push([r ? r.route.name : null, hash]), win: window });
  router.start();
  await tick();
  assert.deepEqual(seen, [[null, '']], 'the app gets a chance to redirect to its default route');
  router.stop();
});

test('HashRouter dispatches routes and honours the leave guard', async () => {
  window.history.replaceState(null, '', '#/projects');
  const seen = [];
  const router = new HashRouter({ onRoute: (r, hash) => seen.push(r ? r.route.name : `null:${hash}`), win: window });
  router.start();
  await tick();
  assert.deepEqual(seen, ['projects']);

  router.navigate('#/p/4');
  await tick(5);
  assert.deepEqual(seen, ['projects', 'project']);

  // Guard says no -> hash restored, no dispatch.
  let asked = 0;
  router.setGuard(() => {
    asked += 1;
    return false;
  });
  router.navigate('#/usage');
  // The rejected entry is undone by a history traversal (async).
  await waitFor(() => asked === 1 && window.location.hash === '#/p/4');
  assert.equal(asked, 1);
  assert.equal(window.location.hash, '#/p/4');
  assert.deepEqual(seen, ['projects', 'project']);

  // Guard says yes -> navigation proceeds and the guard is cleared.
  router.setGuard(async () => true);
  router.navigate('#/usage');
  await tick(5);
  assert.deepEqual(seen, ['projects', 'project', 'usage']);
  assert.equal(router.guard, null);

  // Unknown routes are reported as null; in-page anchors are ignored.
  router.navigate('#/nope');
  await tick(5);
  assert.equal(seen.at(-1), 'null:#/nope');
  const before = seen.length;
  window.location.hash = '#main';
  await tick(5);
  assert.equal(seen.length, before);

  // replace + reload
  router.navigate('#/admin', { replace: true });
  await tick(5);
  assert.equal(seen.at(-1), 'admin');
  router.reload();
  await tick(5);
  assert.equal(seen.at(-1), 'admin');
  // Replacing with the current hash re-runs the route (error-state "Try again" buttons).
  const count = seen.length;
  router.navigate('#/admin', { replace: true });
  await tick(5);
  assert.equal(seen.length, count + 1);
  assert.equal(seen.at(-1), 'admin');
  // ...and a plain navigate to the current hash does too.
  router.navigate('#/admin');
  await tick(5);
  assert.equal(seen.length, count + 2);
  router.stop();
});

test('HashRouter: a rejected Back navigation keeps the previous page reachable (no history rewrite)', async () => {
  window.history.replaceState(null, '', '#/projects');
  const seen = [];
  const router = new HashRouter({ onRoute: (r, hash) => seen.push(hash), win: window });
  router.start();
  await tick();
  router.navigate('#/p/4');
  await tick(10);
  router.navigate('#/p/4/v/9/edit');
  await tick(10);
  assert.deepEqual(seen, ['#/projects', '#/p/4', '#/p/4/v/9/edit']);
  const length = window.history.length;

  // The editor's guard says "stay" when the user presses Back.
  let asked = 0;
  router.setGuard(() => {
    asked += 1;
    return false;
  });
  window.history.back();
  await waitFor(() => asked === 1);
  await waitFor(() => window.location.hash === '#/p/4/v/9/edit');
  assert.equal(asked, 1);
  assert.equal(window.location.hash, '#/p/4/v/9/edit', 'URL restored by moving forward again');
  assert.equal(window.history.length, length, 'no entry added or rewritten');
  assert.equal(seen.length, 3);

  // Once the guard allows it, Back reaches the project page, and Back again the list.
  router.setGuard(() => true);
  window.history.back();
  await waitFor(() => seen.at(-1) === '#/p/4');
  assert.equal(window.location.hash, '#/p/4');
  window.history.back();
  await waitFor(() => seen.at(-1) === '#/projects');
  assert.equal(window.location.hash, '#/projects');

  // Forward with a rejecting guard goes back to where we were.
  let refused = 0;
  router.setGuard(() => {
    refused += 1;
    return false;
  });
  window.history.forward();
  await waitFor(() => refused === 1);
  await waitFor(() => window.location.hash === '#/projects');
  assert.equal(window.location.hash, '#/projects');
  assert.equal(seen.at(-1), '#/projects');

  // A rejected replace navigation restores the URL in place.
  router.navigate('#/usage', { replace: true });
  await tick(20);
  assert.equal(window.location.hash, '#/projects');
  assert.equal(window.history.length, length);
  router.stop();
});
