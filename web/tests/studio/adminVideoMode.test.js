import { resetDom } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { renderVideoMode } from '../../js/studio/views/admin.js';
import { closeAllModals } from '../../js/studio/components/modal.js';

/** GET /api/admin/settings: the Dynamic video mode, and which source set it. */
function state(value, source = 'environment', environment = false) {
  return { dynamic_enabled: { value, source, environment } };
}

function fresh() {
  closeAllModals();
  resetDom();
  return document.createElement('section');
}

test('video mode: the switch shows what a NEW lecture will be generated as', () => {
  const host = fresh();
  renderVideoMode(host, state(false), async () => {});
  const box = host.querySelector('input.checkbox');
  assert.equal(box.checked, false);
  assert.match(host.textContent, /Classic \(Aadhi presenter\)/);
  assert.match(host.textContent, /classic Aadhi presenter videos/);

  renderVideoMode(host, state(true, 'studio'), async () => {});
  assert.equal(host.querySelector('input.checkbox').checked, true);
  assert.match(host.textContent, /Dynamic video/);
});

test('video mode: it names the source, so the environment is never a surprise', () => {
  const host = fresh();
  renderVideoMode(host, state(false, 'environment'), async () => {});
  assert.match(host.textContent, /Set by the environment \(DYNAMIC_ENABLED\)/);

  renderVideoMode(host, state(true, 'studio', false), async () => {});
  assert.match(host.textContent, /Set here\. The environment alone would be classic\./);
});

test('video mode: an unreadable state retries the read, and never switches the mode', () => {
  const host = fresh();
  let toggled = null;
  let retried = 0;
  renderVideoMode(host, null, async (on) => { toggled = on; }, async () => { retried += 1; });
  assert.equal(host.querySelector('input.checkbox'), null);
  assert.match(host.textContent, /Could not read the video mode/);
  // The retry must re-read: wired to onToggle it would silently switch the mode OFF for everyone.
  const retry = host.querySelector('button');
  assert.ok(retry, 'the error state offers a retry');
  retry.click();
  assert.equal(retried, 1);
  assert.equal(toggled, null);
});
