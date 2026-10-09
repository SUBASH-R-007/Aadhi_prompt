import { resetDom, mockFetch } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount as mountAdmin, describeMediaProvider, renderMediaProviders, PROVIDER_STATE_LABELS } from '../../js/studio/views/admin.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, waitFor } from './_views.js';

/** GET /api/admin/media-providers: Pollinations paywalled (cooling), Gemini backup, AI video off. */
function status() {
  return {
    image: {
      enabled: true,
      configured: 'pollinations',
      preferred: 'pollinations',
      usable: true,
      providers: [
        { name: 'pollinations', label: 'Pollinations', role: 'preferred', order: 0, state: 'cooling', reason: 'moved behind the others after a payment or account refusal', paid: false, model: '', cooldown_seconds_left: 540 },
        { name: 'gemini', label: 'Google Gemini / Imagen', role: 'backup', order: 1, state: 'available', reason: '', paid: true, model: 'gemini-2.5-flash-image', cooldown_seconds_left: 0 },
      ],
    },
    video: { enabled: false, configured: 'none', preferred: null, usable: false, providers: [] },
    cooldown_seconds: 60,
    auth_cooldown_seconds: 600,
  };
}

function fresh() {
  closeAllModals();
  resetDom();
}

test('media providers: one-line descriptions', () => {
  assert.equal(PROVIDER_STATE_LABELS.not_configured, 'Not configured');
  const [poll, gemini] = status().image.providers;
  assert.equal(describeMediaProvider(poll), 'Preferred · Temporarily behind the others · for about 9 min');
  assert.equal(describeMediaProvider(gemini), 'Backup 1 · Available · paid per generation');
  assert.equal(describeMediaProvider({ role: 'backup', order: 2, state: 'mystery' }), 'Backup 2 · mystery');
});

test('media providers: the panel lists each chain and says when a medium is off', () => {
  fresh();
  const host = document.createElement('section');
  renderMediaProviders(host, status());
  const items = [...host.querySelectorAll('li.media-provider')];
  assert.deepEqual(items.map((li) => li.getAttribute('data-provider')), ['pollinations', 'gemini']);
  assert.match(items[0].textContent, /payment or account refusal/);
  assert.match(items[1].textContent, /gemini-2\.5-flash-image/);
  assert.match(host.textContent, /AI video\s*Switched off on this server/);
  assert.equal(host.querySelector('.media-cooldown-source'), null, 'inline workers: no note');
  renderMediaProviders(host, { ...status(), cooldowns_source: 'recent worker job events' });
  assert.match(host.querySelector('.media-cooldown-source').textContent, /job workers recorded in recent job logs/);
  renderMediaProviders(host, null);
  assert.match(host.textContent, /Provider status is unavailable/);
});

test('admin: the media provider panel loads separately and a failure reports no error', async () => {
  fresh();
  const me = { id: 1, username: 'boss', role: 'admin', must_change_password: false, is_active: true, daily_budget_usd: null, last_login_at: null };
  const calls = mockFetch({
    'GET /api/admin/users': { items: [me] },
    'GET /api/admin/usage?days=30': { users: [], total_usd: 0 },
    'GET /api/admin/media-providers': status(),
  });
  const app = fakeApp({ user: () => me });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mountAdmin(container, { app, params: {}, query: {} });
  await waitFor(() => container.querySelectorAll('.media-providers li.media-provider').length === 2);
  assert.ok(calls.some((c) => c.path === '/api/admin/media-providers'));
  assert.equal(container.querySelectorAll('tbody tr').length, 1, 'the users table is unaffected');
  handle.destroy();

  fresh();
  mockFetch({ 'GET /api/admin/users': { items: [me] }, 'GET /api/admin/usage?days=30': { users: [], total_usd: 0 } });
  const app2 = fakeApp({ user: () => me });
  const container2 = document.createElement('div');
  document.body.appendChild(container2);
  const handle2 = mountAdmin(container2, { app: app2, params: {}, query: {} });
  await waitFor(() => /Provider status is unavailable/.test(container2.querySelector('.media-providers').textContent));
  assert.deepEqual(app2.rec.errors, []);
  handle2.destroy();
});
