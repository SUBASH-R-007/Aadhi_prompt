import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount as mountKeys, describePersonal, describeServer, describeSwitchedOff, INTRO } from '../../js/studio/views/apiKeys.js';
import { mount as mountAdmin } from '../../js/studio/views/admin.js';
import { mount as mountUsage } from '../../js/studio/views/usage.js';
import { createOptionsForm } from '../../js/studio/views/optionsFields.js';
import { startStudio } from '../../js/studio/app.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { validateApiKey, scrubSecret, credentialStatus, lastTestText, providerLabel, API_KEY_LIMITS } from '../../js/studio/lib/apiKeys.js';
import { engineOptionLabel, personalKeysEnabled } from '../../js/studio/lib/optionsForm.js';
import { sampleMeta, sampleUser } from './fixtures.js';
import { fakeApp, byText, clickModal, modalButtons, typeInto, waitFor, jsonBody } from './_views.js';

/** A realistic-looking (fake) key; it must never reach the DOM, storage, URLs or the console. */
const SECRET = 'sk-ant-api03-Zq9vTESTONLYnotarealkey7Hd2a1B2';
const SAVED_AT = '2026-10-01T09:00:00Z';

function fresh() {
  closeAllModals();
  resetDom();
  localStorage.clear();
  sessionStorage.clear();
}

/** A saved key as GET /api/keys shows it. */
function view(overrides = {}) {
  return { hint: 'sk-proj-…9xYz', updated_at: SAVED_AT, last_verified_at: null, last_error: null, readable: true, ...overrides };
}

/** GET /api/keys: Claude not set, OpenAI saved and tested, Gemini unreadable (server has no Gemini key). */
function personalKeys(overrides = {}) {
  return {
    enabled: true,
    providers: [
      { provider: 'anthropic', label: 'Anthropic Claude', personal: null, server_available: true },
      { provider: 'openai', label: 'OpenAI', personal: view({ last_verified_at: new Date(Date.now() - 5 * 60000).toISOString() }), server_available: true },
      { provider: 'gemini', label: 'Google Gemini', personal: view({ hint: 'AIza…k3Q1', readable: false }), server_available: false },
    ],
    ...overrides,
  };
}

/** GET /api/admin/keys: Claude saved in the Studio (and in .env), OpenAI from .env, Gemini none. */
function serverKeys(overrides = {}) {
  return {
    enabled: true,
    providers: [
      { provider: 'anthropic', label: 'Anthropic Claude', stored: view({ hint: 'sk-ant-…a1B2' }), env: true, active_source: 'stored' },
      { provider: 'openai', label: 'OpenAI', stored: null, env: true, active_source: 'env' },
      { provider: 'gemini', label: 'Google Gemini', stored: null, env: false, active_source: null },
    ],
    ...overrides,
  };
}

/** @param {HTMLElement} root */
function card(root, provider) {
  return root.querySelector(`.key-card[data-provider="${provider}"]`);
}

/** Visible button labels of a card's action row. */
function actionLabels(cardEl) {
  const row = cardEl.querySelector('.key-actions');
  return row.hidden ? [] : [...row.querySelectorAll('button')].map((b) => b.textContent.trim());
}

/** Console calls recorded while `fn` runs. */
async function captureConsole(fn) {
  const seen = [];
  const saved = {};
  for (const name of ['log', 'info', 'warn', 'error', 'debug']) {
    saved[name] = console[name];
    console[name] = (...args) => seen.push(args.map((a) => (a instanceof Error ? `${a.message} ${a.stack}` : typeof a === 'string' ? a : JSON.stringify(a))).join(' '));
  }
  try {
    await fn();
  } finally {
    Object.assign(console, saved);
  }
  return seen;
}

async function mountPersonal(routes) {
  fresh();
  const calls = mockFetch(routes);
  const app = fakeApp();
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mountKeys(container, { app, params: {}, query: {} });
  return { calls, app, container, handle };
}

// --- pure helpers -------------------------------------------------------------------------------

test('validateApiKey mirrors the server: stripped, 16-512 characters, no whitespace or control characters', () => {
  assert.deepEqual(validateApiKey(`  ${SECRET}\n`), { ok: true, value: SECRET });
  assert.equal(validateApiKey('').ok, false);
  assert.match(validateApiKey('   ').error, /Paste your API key/);
  assert.match(validateApiKey('sk-short').error, /too short/);
  assert.match(validateApiKey('x'.repeat(API_KEY_LIMITS.max + 1)).error, /too long/);
  assert.equal(validateApiKey('x'.repeat(API_KEY_LIMITS.max)).ok, true);
  assert.match(validateApiKey('sk-ant-abc def-0123456789').error, /spaces or invisible/);
  assert.match(validateApiKey('sk-ant-abc​def-0123456789').error, /spaces or invisible/);
  assert.match(validateApiKey('sk-ant-abc\u0007def-0123456789').error, /spaces or invisible/);
  for (const notAKey of ['/api/keys/openai', 'provider=anthropic', 'assets.tts_fallback', 'sk-ant-ünïcode-key-1234']) {
    assert.match(validateApiKey(notAKey).error, /only letters, digits, '-' and '_'/, notAKey);
  }
  assert.equal(validateApiKey('AIzaSy-gemini_KEY-0123456789').ok, true);
});

test('scrubSecret removes a typed key from any message', () => {
  assert.equal(scrubSecret(`api_key: ${SECRET} is invalid (${SECRET})`, [SECRET]), 'api_key: [key hidden] is invalid ([key hidden])');
  assert.equal(scrubSecret('nothing to hide', [SECRET, '', null]), 'nothing to hide');
  assert.equal(scrubSecret('abc', ['ab']), 'abc', 'very short strings are not treated as secrets');
});

test('status and last-test texts of a saved key', () => {
  assert.deepEqual(credentialStatus(null), { text: 'Not set', kind: 'muted' });
  assert.deepEqual(credentialStatus(view()), { text: 'Saved', kind: 'ok' });
  assert.deepEqual(credentialStatus(view({ readable: false })), { text: 'Needs re-entry', kind: 'attention' });
  const now = Date.parse('2026-10-04T12:00:00Z');
  assert.equal(lastTestText(null, now), null);
  assert.equal(lastTestText(view({ readable: false }), now), null);
  assert.deepEqual(lastTestText(view(), now), { text: 'Not tested yet', ok: null });
  assert.deepEqual(lastTestText(view({ last_verified_at: '2026-10-04T11:55:00Z' }), now), { text: 'Last tested 5 min ago', ok: true });
  assert.deepEqual(lastTestText(view({ last_verified_at: '2026-10-04T11:55:00Z', last_error: 'Invalid key' }), now), { text: 'Last test failed: Invalid key', ok: false });
  assert.equal(providerLabel({ provider: 'anthropic' }), 'Anthropic Claude');
  assert.equal(providerLabel({ provider: 'openai', label: 'OpenAI (org)' }), 'OpenAI (org)');
});

test('describePersonal / describeServer pick the actions for each state', () => {
  const [none, saved, unreadable] = personalKeys().providers;
  assert.deepEqual([describePersonal(none).saveLabel, describePersonal(none).canTest, describePersonal(none).canRemove], ['Add key', false, false]);
  assert.deepEqual([describePersonal(saved).saveLabel, describePersonal(saved).canTest, describePersonal(saved).canRemove], ['Replace', true, true]);
  assert.deepEqual([describePersonal(unreadable).saveLabel, describePersonal(unreadable).canTest, describePersonal(unreadable).canRemove], ['Re-enter key', false, true]);
  assert.equal(describePersonal(unreadable).hint, null, 'no hint for a key the server cannot read');
  const [stored, env, missing] = serverKeys().providers;
  assert.equal(describeServer(stored, true).status.text, 'Saved in Studio');
  assert.equal(describeServer(env, true).status.text, 'From .env');
  assert.equal(describeServer(missing, true).status.text, 'Not set');
  assert.equal(describeServer(stored, false).saveLabel, null, 'no saving while Studio keys are off');
  assert.equal(describeServer(stored, false).canRemove, true, 'a stored key can always be removed');
  assert.equal(describeServer(missing, true).canTest, false);
  assert.equal(describeServer(env, true).canTest, true, 'the .env key can be tested');
});

// --- personal API keys page -------------------------------------------------------------------

test('API keys page: intro, one card per provider, not set / saved / needs re-entry', async () => {
  const { container, handle } = await mountPersonal({ 'GET /api/keys': personalKeys() });
  await waitFor(() => container.querySelectorAll('.key-card').length === 3);
  assert.equal(container.querySelector('h1').textContent, 'API keys');
  assert.equal(container.querySelector('.page-subtitle').textContent, INTRO);

  const claude = card(container, 'anthropic');
  assert.equal(claude.querySelector('h2').textContent, 'Anthropic Claude');
  assert.equal(claude.querySelector('.badge').textContent, 'Not set');
  assert.equal(claude.querySelector('.key-hint').hidden, true);
  assert.match(claude.textContent, /Without your key, your lectures use the server's key\./);
  assert.deepEqual(actionLabels(claude), ['Add key']);

  const openai = card(container, 'openai');
  assert.equal(openai.querySelector('.badge').textContent, 'Saved');
  assert.equal(openai.querySelector('.key-hint code').textContent, 'sk-proj-…9xYz');
  assert.match(openai.textContent, /Last tested 5 min ago/);
  assert.deepEqual(actionLabels(openai), ['Replace', 'Test', 'Remove']);

  const gemini = card(container, 'gemini');
  assert.equal(gemini.querySelector('.badge').textContent, 'Needs re-entry');
  assert.match(gemini.textContent, /can no longer be read/);
  assert.match(gemini.textContent, /The server has no key for this engine/);
  assert.equal(gemini.querySelector('.key-hint').hidden, true);
  assert.deepEqual(actionLabels(gemini), ['Re-enter key', 'Remove']);
  handle.destroy();
});

test('API keys page: a server without personal keys shows a short notice and no cards', async () => {
  const { container, handle } = await mountPersonal({ 'GET /api/keys': { enabled: false, providers: [] } });
  await waitFor(() => container.querySelector('.notice'));
  assert.match(container.querySelector('.notice').textContent, /Personal API keys are turned off on this server/);
  assert.equal(container.querySelectorAll('.key-card').length, 0);
  handle.destroy();
});

test('API keys page: keys saved before personal keys were turned off can still be removed', async () => {
  const saved = { provider: 'openai', label: 'OpenAI', personal: view({ hint: 'sk-…abcd' }), server_available: true };
  const data = { enabled: false, providers: [saved, { provider: 'gemini', label: 'Google Gemini', personal: null, server_available: false }] };
  const { calls, container, handle } = await mountPersonal({ 'GET /api/keys': data, 'DELETE /api/keys/openai': { status: 204, body: null } });
  await waitFor(() => container.querySelector('.notice'));
  assert.match(container.querySelector('.notice').textContent, /Personal API keys are turned off on this server/);
  assert.equal(container.querySelectorAll('.key-card').length, 1, 'only the saved key, no empty cards');
  const openai = card(container, 'openai');
  assert.equal(openai.querySelector('.badge').textContent, 'Not in use');
  assert.equal(openai.querySelector('.key-hint code').textContent, 'sk-…abcd');
  assert.match(openai.textContent, /You can still remove it from the server/);
  assert.deepEqual(actionLabels(openai), ['Remove'], 'no saving or testing while switched off');
  byText(openai, 'Remove', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal-body').textContent, /not in use while personal keys are turned off/);
  clickModal('Remove key');
  await waitFor(() => calls.some((c) => c.method === 'DELETE'));
  assert.equal(calls.find((c) => c.method === 'DELETE').path, '/api/keys/openai');
  await waitFor(() => openai.querySelector('.badge').textContent === 'Removed');
  assert.deepEqual(actionLabels(openai), []);
  assert.deepEqual(describeSwitchedOff({ ...saved, personal: view({ readable: false }) }).hint, null, 'no hint for an unreadable key');
  handle.destroy();
});

test('API keys page: a failed load offers a retry', async () => {
  let fail = true;
  const { container, app, handle } = await mountPersonal({ 'GET /api/keys': () => (fail ? { status: 500, body: { detail: 'x' } } : personalKeys()) });
  await waitFor(() => container.querySelector('.error-state'));
  assert.equal(app.rec.errors.length, 1);
  fail = false;
  byText(container, 'Try again', 'button').click();
  await waitFor(() => container.querySelectorAll('.key-card').length === 3);
  handle.destroy();
});

/** The key field is a password input (masked in every browser) that saved passwords never fill. */
function assertMaskedKeyInput(keyInput) {
  assert.equal(keyInput.type, 'password', 'masked natively; CSS text-security is not supported everywhere');
  assert.equal(keyInput.getAttribute('autocomplete'), 'new-password');
  assert.equal(keyInput.getAttribute('data-lpignore'), 'true');
  assert.equal(keyInput.getAttribute('data-1p-ignore'), 'true');
}

test('saving a key: hygienic masked input, PUT body, input cleared, the key appears nowhere afterwards', async () => {
  const saved = { provider: 'anthropic', label: 'Anthropic Claude', personal: view({ hint: 'sk-ant-…a1B2' }), server_available: true };
  const { calls, app, container, handle } = await mountPersonal({ 'GET /api/keys': personalKeys(), 'PUT /api/keys/anthropic': saved });
  await waitFor(() => card(container, 'anthropic'));
  const claude = card(container, 'anthropic');
  const logs = await captureConsole(async () => {
    byText(claude, 'Add key', 'button').click();
    const keyInput = claude.querySelector('input');
    assertMaskedKeyInput(keyInput);
    assert.equal(keyInput.getAttribute('spellcheck'), 'false');
    assert.equal(keyInput.getAttribute('autocapitalize'), 'off');
    assert.equal(keyInput.getAttribute('aria-label'), 'Anthropic Claude API key');
    assert.equal(document.activeElement, keyInput, 'the input gets focus');
    assert.equal(claude.querySelector('.key-actions').hidden, true, 'actions hide while entering a key');

    // Too short: refused locally, nothing is sent.
    typeInto(keyInput, 'sk-ant-short');
    byText(claude, 'Save key', 'button').click();
    assert.match(claude.querySelector('.field-error').textContent, /too short/);
    assert.equal(calls.filter((c) => c.method === 'PUT').length, 0);

    typeInto(keyInput, `  ${SECRET}  `);
    assert.equal(claude.querySelector('.field-error').hidden, true, 'typing clears the error');
    keyInput.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await waitFor(() => claude.querySelector('.badge').textContent === 'Saved');
    const putCall = calls.find((c) => c.method === 'PUT');
    assert.equal(putCall.path, '/api/keys/anthropic');
    assert.deepEqual(jsonBody(putCall), { api_key: SECRET });
    assert.equal(putCall.init.headers['X-Aadhi-CSRF'], '1');
    assert.equal(keyInput.value, '', 'the input was emptied');
    assert.equal(keyInput.isConnected, false, 'the entry closed');
  });
  assert.equal(claude.querySelector('.key-hint code').textContent, 'sk-ant-…a1B2');
  assert.deepEqual(actionLabels(claude), ['Replace', 'Test', 'Remove']);
  assert.ok(app.rec.toasts.some((t) => t.message === 'Anthropic Claude key saved.' && t.kind === 'success'));
  assert.equal(app.rec.metaInvalidations, 1, 'engine availability is re-read from /api/meta');
  // Nowhere: DOM text, markup, URLs, storage, console.
  assert.equal(document.body.textContent.includes(SECRET), false);
  assert.equal(document.documentElement.outerHTML.includes(SECRET), false);
  assert.equal(calls.some((c) => c.path.includes(SECRET)), false);
  assert.equal(location.href.includes(SECRET), false);
  for (const store of [localStorage, sessionStorage]) {
    for (let i = 0; i < store.length; i++) assert.equal(String(store.getItem(store.key(i))).includes(SECRET), false);
  }
  assert.equal(logs.some((l) => l.includes(SECRET)), false);
  handle.destroy();
});

test('a rejected save keeps the entry open and never echoes the key in the error', async () => {
  let reply = { status: 422, body: { detail: `api_key: ${SECRET} is not a valid key`, code: 'validation' } };
  const { calls, container, handle } = await mountPersonal({ 'GET /api/keys': personalKeys(), 'PUT /api/keys/anthropic': () => reply });
  await waitFor(() => card(container, 'anthropic'));
  const claude = card(container, 'anthropic');
  byText(claude, 'Add key', 'button').click();
  const keyInput = claude.querySelector('input');
  typeInto(keyInput, SECRET);
  byText(claude, 'Save key', 'button').click();
  await waitFor(() => !claude.querySelector('.field-error').hidden);
  const shown = claude.querySelector('.field-error').textContent;
  assert.equal(shown.includes(SECRET), false);
  assert.match(shown, /\[key hidden\] is not a valid key/);
  assert.equal(keyInput.isConnected, true, 'the user can correct it');
  assert.equal(keyInput.disabled, false);
  assert.equal(document.body.textContent.includes(SECRET), false);

  reply = { status: 403, body: { detail: 'disabled', code: 'api_keys_disabled' } };
  byText(claude, 'Save key', 'button').click();
  await waitFor(() => /turned off/.test(claude.querySelector('.field-error').textContent));
  assert.equal(calls.filter((c) => c.method === 'PUT').length, 2);

  byText(claude, 'Cancel', 'button').click();
  assert.equal(keyInput.value, '', 'Cancel empties the input');
  assert.equal(keyInput.isConnected, false);
  assert.deepEqual(actionLabels(claude), ['Add key']);
  assert.equal(document.activeElement, byText(claude, 'Add key', 'button'), 'focus returns to the button');
  handle.destroy();
});

test('testing a key shows the result and the refreshed last-test line', async () => {
  let testReply = { ok: false, message: 'Incorrect API key provided.' };
  let rows = personalKeys();
  const { calls, container, handle } = await mountPersonal({
    'GET /api/keys': () => rows,
    'POST /api/keys/openai/test': () => testReply,
  });
  await waitFor(() => card(container, 'openai'));
  const openai = card(container, 'openai');
  rows = personalKeys();
  rows.providers[1].personal = view({ last_verified_at: new Date().toISOString(), last_error: 'Incorrect API key provided.' });
  byText(openai, 'Test', 'button').click();
  await waitFor(() => openai.querySelector('.key-result.bad'));
  assert.equal(calls.find((c) => c.method === 'POST').path, '/api/keys/openai/test');
  assert.equal(openai.querySelector('.key-result').textContent, 'Test failed. Incorrect API key provided.');
  assert.equal(openai.querySelector('.key-result').getAttribute('role'), 'status');
  await waitFor(() => /Last test failed: Incorrect API key provided\./.test(openai.textContent));
  assert.equal(byText(openai, 'Test', 'button').disabled, false, 'buttons are usable again');

  testReply = { ok: true, message: 'Key works.' };
  rows = personalKeys();
  byText(openai, 'Test', 'button').click();
  await waitFor(() => openai.querySelector('.key-result.ok'));
  assert.equal(openai.querySelector('.key-result').textContent, 'Test passed. Key works.');
  await waitFor(() => /Last tested/.test(openai.textContent));
  handle.destroy();
});

test('keyboard focus stays on the card after a key test (the buttons are rebuilt)', async () => {
  const { container, handle } = await mountPersonal({ 'GET /api/keys': personalKeys(), 'POST /api/keys/openai/test': { ok: true, message: 'Key works.' } });
  await waitFor(() => card(container, 'openai'));
  const openai = card(container, 'openai');
  const before = byText(openai, 'Test', 'button');
  before.focus();
  assert.equal(document.activeElement, before);
  before.click();
  await waitFor(() => openai.querySelector('.key-result.ok') && openai.getAttribute('aria-busy') === 'false');
  await tick(5);
  const after = byText(openai, 'Test', 'button');
  assert.equal(before.isConnected, false, 'the old button was replaced');
  assert.equal(document.activeElement, after, 'focus moved to the new Test button, not <body>');
  handle.destroy();
});

test('admin: a Studio key the server ignores (Studio keys off) shows no hint as if it were in use', () => {
  const stored = view({ hint: 'sk-ant-…cdef' });
  const withEnv = describeServer({ provider: 'anthropic', label: 'Anthropic Claude', stored, env: true, active_source: 'env' }, false);
  assert.equal(withEnv.status.text, 'From .env');
  assert.equal(withEnv.hint, null, 'the hint belongs to the ignored Studio key, not the .env key in use');
  const texts = (state) => state.lines.filter(Boolean).map((n) => n.textContent);
  assert.equal(texts(withEnv).filter((t) => /A key saved in the Studio \(sk-ant-…cdef\) is not used/.test(t)).length, 1);
  assert.equal(withEnv.canRemove, true, 'the ignored key can still be removed');
  const noEnv = describeServer({ provider: 'anthropic', label: 'Anthropic Claude', stored, env: false, active_source: null }, false);
  assert.equal(noEnv.status.text, 'Not set');
  assert.equal(noEnv.hint, null);
  assert.ok(texts(noEnv).includes('No .env key for this engine, so the server has no active key.'));
  assert.ok(!texts(noEnv).includes('No server key for this engine.'), 'no contradictory line');
});

test('removing a key asks first; Cancel keeps it, Remove deletes it', async () => {
  const { calls, app, container, handle } = await mountPersonal({ 'GET /api/keys': personalKeys(), 'DELETE /api/keys/openai': { status: 204, body: null } });
  await waitFor(() => card(container, 'openai'));
  const openai = card(container, 'openai');
  byText(openai, 'Remove', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal-title').textContent, /Remove your OpenAI key\?/);
  assert.match(document.querySelector('.modal-body').textContent, /use the server's key again/);
  assert.deepEqual(modalButtons().map((b) => b.textContent.trim()), ['Cancel', 'Remove key']);
  clickModal('Cancel');
  await tick(5);
  assert.equal(calls.filter((c) => c.method === 'DELETE').length, 0);
  assert.equal(openai.querySelector('.badge').textContent, 'Saved');

  byText(openai, 'Remove', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Remove key');
  await waitFor(() => openai.querySelector('.badge').textContent === 'Not set');
  assert.equal(calls.find((c) => c.method === 'DELETE').path, '/api/keys/openai');
  assert.deepEqual(actionLabels(openai), ['Add key']);
  assert.ok(app.rec.toasts.some((t) => t.message === 'OpenAI key removed.'));
  assert.equal(app.rec.metaInvalidations, 1);
  handle.destroy();
});

// --- admin: server API keys ----------------------------------------------------------------------

async function mountAdminPage(routes) {
  fresh();
  const me = { id: 1, username: 'boss', role: 'admin', must_change_password: false, is_active: true, daily_budget_usd: null, last_login_at: null };
  const calls = mockFetch({
    'GET /api/admin/users': { items: [me] },
    'GET /api/admin/usage?days=30': { users: [], total_usd: 0 },
    ...routes,
  });
  const app = fakeApp({ user: () => me });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mountAdmin(container, { app, params: {}, query: {} });
  return { calls, app, container, handle };
}

test('admin: server API keys show their source with Replace / Test / Remove', async () => {
  const { container, handle } = await mountAdminPage({ 'GET /api/admin/keys': serverKeys() });
  await waitFor(() => container.querySelectorAll('.server-keys .key-card').length === 3);
  const section = container.querySelector('.server-keys');
  assert.equal(section.querySelector('h2').textContent, 'Server API keys');
  assert.match(section.textContent, /replace the keys in the server's \.env file/);

  const claude = card(section, 'anthropic');
  assert.equal(claude.querySelector('.badge').textContent, 'Saved in Studio');
  assert.equal(claude.querySelector('.key-hint code').textContent, 'sk-ant-…a1B2');
  assert.match(claude.textContent, /it applies again if you remove this one/);
  assert.deepEqual(actionLabels(claude), ['Replace', 'Test', 'Remove']);

  const openai = card(section, 'openai');
  assert.equal(openai.querySelector('.badge').textContent, 'From .env');
  assert.deepEqual(actionLabels(openai), ['Add key', 'Test']);

  const gemini = card(section, 'gemini');
  assert.equal(gemini.querySelector('.badge').textContent, 'Not set');
  assert.deepEqual(actionLabels(gemini), ['Add key']);
  handle.destroy();
});

test('admin: saving, testing and removing server keys', async () => {
  const geminiSaved = { provider: 'gemini', label: 'Google Gemini', stored: view({ hint: 'AIza…k3Q1' }), env: false, active_source: 'stored' };
  const { calls, app, container, handle } = await mountAdminPage({
    'GET /api/admin/keys': serverKeys(),
    'PUT /api/admin/keys/gemini': geminiSaved,
    'POST /api/admin/keys/openai/test': { ok: true, message: 'OK' },
    'DELETE /api/admin/keys/anthropic': { status: 204, body: null },
  });
  await waitFor(() => container.querySelectorAll('.server-keys .key-card').length === 3);
  const section = container.querySelector('.server-keys');

  // Save a Gemini server key.
  const gemini = card(section, 'gemini');
  byText(gemini, 'Add key', 'button').click();
  const keyInput = gemini.querySelector('input');
  assertMaskedKeyInput(keyInput);
  typeInto(keyInput, 'AIzaSyTESTONLY-not-a-real-key-k3Q1');
  byText(gemini, 'Save key', 'button').click();
  await waitFor(() => gemini.querySelector('.badge').textContent === 'Saved in Studio');
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'PUT' && c.path === '/api/admin/keys/gemini')), { api_key: 'AIzaSyTESTONLY-not-a-real-key-k3Q1' });
  assert.equal(keyInput.value, '');
  assert.equal(section.textContent.includes('AIzaSyTESTONLY'), false);
  assert.equal(gemini.querySelector('.key-hint code').textContent, 'AIza…k3Q1');

  // Test the active (.env) OpenAI key.
  const openai = card(section, 'openai');
  byText(openai, 'Test', 'button').click();
  await waitFor(() => openai.querySelector('.key-result.ok'));
  assert.ok(calls.some((c) => c.method === 'POST' && c.path === '/api/admin/keys/openai/test'));

  // Remove the Studio key: the .env key applies again.
  const claude = card(section, 'anthropic');
  byText(claude, 'Remove', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal-title').textContent, /Remove the server's Anthropic Claude key\?/);
  assert.match(document.querySelector('.modal-body').textContent, /The key in the server's \.env file then applies again\./);
  clickModal('Remove key');
  await waitFor(() => claude.querySelector('.badge').textContent === 'From .env');
  assert.ok(calls.some((c) => c.method === 'DELETE' && c.path === '/api/admin/keys/anthropic'));
  assert.deepEqual(actionLabels(claude), ['Add key', 'Test']);
  assert.equal(app.rec.metaInvalidations, 2, 'saved + removed');
  handle.destroy();
});

test('admin: with Studio keys turned off only the .env keys are shown and tested', async () => {
  const data = serverKeys({ enabled: false });
  data.providers[0] = { provider: 'anthropic', label: 'Anthropic Claude', stored: null, env: false, active_source: null };
  const { container, handle } = await mountAdminPage({ 'GET /api/admin/keys': data });
  await waitFor(() => container.querySelectorAll('.server-keys .key-card').length === 3);
  const section = container.querySelector('.server-keys');
  assert.match(section.querySelector('.notice').textContent, /turned off on this server/);
  assert.deepEqual(actionLabels(card(section, 'openai')), ['Test']);
  assert.deepEqual(actionLabels(card(section, 'anthropic')), []);
  handle.destroy();
});

test('admin: servers without saved keys (404) hide the section; the users table still loads', async () => {
  const { container, handle } = await mountAdminPage({});
  await waitFor(() => container.querySelectorAll('tbody tr').length === 1);
  await waitFor(() => container.querySelector('.server-keys').hidden);
  assert.equal(container.querySelectorAll('.key-card').length, 0);
  handle.destroy();
});

test('admin: spending shows the part paid with users’ own keys only when there is some', async () => {
  const spending = (/** @type {Element} */ root) => [...root.querySelectorAll('section h2')].find((x) => x.textContent.startsWith('Spending'))?.parentElement;
  const row = { user_id: 1, username: 'boss', total_usd: 9, today_usd: 2 };
  let page = await mountAdminPage({ 'GET /api/admin/usage?days=30': { users: [{ ...row, own_key_usd: 4 }], total_usd: 9, own_key_usd: 4 } });
  await waitFor(() => spending(page.container)?.querySelector('table'));
  let panel = spending(page.container);
  assert.match(panel.textContent, /Total: \$9\.00 \(\$4\.00 of it paid with users’ own API keys\)/);
  assert.deepEqual([...panel.querySelectorAll('thead th')].map((th) => th.textContent), ['User', 'Today (server)', '30 days', 'Own keys, 30 days']);
  assert.equal(panel.querySelector('tbody td:last-child').textContent, '$4.00');
  page.handle.destroy();

  page = await mountAdminPage({ 'GET /api/admin/usage?days=30': { users: [{ ...row, own_key_usd: 0 }], total_usd: 9, own_key_usd: 0 } });
  await waitFor(() => spending(page.container)?.querySelector('table'));
  panel = spending(page.container);
  assert.doesNotMatch(panel.textContent, /own/i);
  assert.equal(panel.querySelectorAll('thead th').length, 3);
  page.handle.destroy();
});

// --- options form: engine dropdown ---------------------------------------------------------------

/** Production-like meta for a user with their own Claude key; OpenAI has no key at all. */
function ownKeyMeta() {
  const meta = sampleMeta();
  meta.llm.provider = 'gemini';
  meta.llm.engines = meta.llm.engines.filter((e) => e.id !== 'fake');
  meta.llm.engines = meta.llm.engines.map((e) => ({ ...e, key_source: e.id === 'anthropic' ? 'personal' : e.configured ? 'env' : null }));
  meta.api_keys = { enabled: true, personal_enabled: true };
  return meta;
}

function engineSelect(form) {
  const lab = [...form.el.querySelectorAll('label.field')].find((l) => l.querySelector('.field-label').textContent === 'Engine');
  return lab.querySelector('select');
}

test('engine dropdown: "(your key)" for engines on the user\'s key, unconfigured ones stay disabled, hint link to API keys', () => {
  resetDom();
  const meta = ownKeyMeta();
  const form = createOptionsForm(meta, {});
  document.body.appendChild(form.el);
  const sel = engineSelect(form);
  assert.deepEqual(
    [...sel.options].map((o) => [o.value, o.textContent, o.disabled]),
    [
      ['', 'Server default (Google Gemini)', false],
      ['gemini', 'Google Gemini', false],
      ['openai', 'OpenAI (not configured)', true],
      ['anthropic', 'Anthropic Claude (your key)', false],
    ],
  );
  const link = form.el.querySelector('.own-key-hint a');
  assert.ok(link, 'hint link shown');
  assert.equal(form.el.querySelector('.own-key-hint').textContent, 'Have your own key? Add it on the API keys page.');
  assert.ok(link.getAttribute('href').endsWith('#/keys'));
  assert.match(sel.closest('.field-wrap').textContent, /the server's or your own/);
});

test('engine dropdown: no hint without personal keys or when every engine has a key; the default can be on your key', () => {
  resetDom();
  let meta = ownKeyMeta();
  meta.api_keys.personal_enabled = false;
  let form = createOptionsForm(meta, {});
  assert.equal(form.el.querySelector('.own-key-hint'), null);
  assert.match(engineSelect(form).closest('.field-wrap').textContent, /Each engine needs its API key on the server\./);

  meta = ownKeyMeta();
  meta.llm.engines = meta.llm.engines.map((e) => ({ ...e, configured: true }));
  form = createOptionsForm(meta, {});
  assert.equal(form.el.querySelector('.own-key-hint'), null);

  meta = ownKeyMeta();
  meta.llm.provider = 'anthropic';
  form = createOptionsForm(meta, {});
  assert.equal(engineSelect(form).options[0].textContent, 'Server default (Anthropic Claude, your key)');

  form = createOptionsForm(sampleMeta(), {});
  assert.equal(form.el.querySelector('.own-key-hint'), null, 'servers without api_keys in meta');
});

test('engineOptionLabel and personalKeysEnabled', () => {
  assert.equal(engineOptionLabel({ id: 'openai', label: 'OpenAI', configured: false, key_source: null, models: {} }), 'OpenAI (not configured)');
  assert.equal(engineOptionLabel({ id: 'openai', label: 'OpenAI', configured: true, key_source: 'personal', models: {} }), 'OpenAI (your key)');
  assert.equal(engineOptionLabel({ id: 'openai', label: 'OpenAI', configured: true, key_source: 'server', models: {} }), 'OpenAI');
  assert.equal(personalKeysEnabled({ api_keys: { enabled: true, personal_enabled: true } }), true);
  assert.equal(personalKeysEnabled({ api_keys: { enabled: true, personal_enabled: false } }), false);
  assert.equal(personalKeysEnabled(sampleMeta()), false);
  assert.equal(personalKeysEnabled(null), false);
});

// --- usage ---------------------------------------------------------------------------------------

const usageBody = (own) => ({ daily_budget_usd: 15, today_usd: 2, total_usd: 9, ...(own === undefined ? {} : { own_key_usd: own }), by_day: [], by_provider: [], by_operation: [] });

test('usage: spending paid with your own keys is shown next to the totals (outside the budget)', async () => {
  fresh();
  mockFetch({ 'GET /api/usage/me?days=30': usageBody(1.5) });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mountUsage(container, { app: fakeApp(), params: {}, query: {} });
  await waitFor(() => container.querySelector('.stat-card.own-keys'));
  const ownCard = container.querySelector('.stat-card.own-keys');
  assert.equal(ownCard.querySelector('.stat-value').textContent, '$1.50');
  assert.equal(ownCard.querySelector('.stat-label').textContent, 'Paid with your own keys, last 30 days');
  assert.match(ownCard.textContent, /Not counted toward your daily budget/);
  const budgetCard = container.querySelector('.stat-grid .stat-card');
  assert.equal(budgetCard.querySelector('.stat-label').textContent, 'Budget used today', 'today_usd is the server-billed spend');
  assert.match(budgetCard.textContent, /Excludes spending paid with your own keys/);
  assert.equal(container.querySelectorAll('.stat-grid .stat-card').length, 3);
  handle.destroy();
});

test('usage: no own-key card when nothing was paid with your keys (or the server does not say)', async () => {
  for (const own of [0, undefined]) {
    fresh();
    mockFetch({ 'GET /api/usage/me?days=30': usageBody(own) });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const handle = mountUsage(container, { app: fakeApp(), params: {}, query: {} });
    await waitFor(() => container.querySelector('.stat-grid'));
    assert.equal(container.querySelector('.own-keys'), null);
    assert.doesNotMatch(container.textContent, /own keys/);
    assert.equal(container.querySelector('.stat-grid .stat-card .stat-label').textContent, 'Budget used today');
    handle.destroy();
  }
});

// --- app chrome: nav item and account menu ----------------------------------------------------

async function startWith(meta) {
  fresh();
  window.history.replaceState(null, '', '#/projects');
  mockFetch({
    'GET /api/auth/me': { user: sampleUser() },
    'GET /api/meta': meta,
    'GET /api/projects?limit=24&offset=0': { items: [], total: 0 },
    'GET /api/keys': personalKeys(),
  });
  const root = document.createElement('div');
  document.body.appendChild(root);
  return { root, ...(await startStudio(root)) };
}

test('top bar: "API keys" link and menu item when personal keys are allowed; the page opens', async () => {
  const meta = { ...sampleMeta(), api_keys: { enabled: true, personal_enabled: true } };
  const { root, app, dispose } = await startWith(meta);
  await waitFor(() => byText(root.querySelector('.topnav'), 'API keys', 'a'));
  const link = byText(root.querySelector('.topnav'), 'API keys', 'a');
  assert.ok(link.getAttribute('href').endsWith('#/keys'));
  byText(root, 'teacher1', 'button').click();
  await tick();
  assert.ok(byText(document.body, 'API keys', '[role="menuitem"]'), 'also in the account menu');
  byText(document.body, 'API keys', '[role="menuitem"]').click();
  await waitFor(() => root.querySelectorAll('.key-card').length === 3);
  assert.equal(location.hash, '#/keys');
  assert.equal(byText(root.querySelector('.topnav'), 'API keys', 'a').getAttribute('aria-current'), 'page');
  assert.equal(typeof app.invalidateMeta, 'function');
  dispose();
});

test('top bar: no "API keys" link when personal keys are off (or meta does not say)', async () => {
  for (const meta of [{ ...sampleMeta(), api_keys: { enabled: true, personal_enabled: false } }, sampleMeta()]) {
    const { root, dispose } = await startWith(meta);
    await waitFor(() => /No lectures yet/.test(root.textContent));
    await tick(10);
    assert.equal(byText(root.querySelector('.topnav'), 'API keys', 'a'), null);
    byText(root, 'teacher1', 'button').click();
    await tick();
    assert.equal(byText(document.body, 'API keys', '[role="menuitem"]'), null);
    dispose();
  }
});
