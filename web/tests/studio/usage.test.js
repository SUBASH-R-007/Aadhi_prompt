import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount } from '../../js/studio/views/usage.js';
import { fakeApp, deferred, waitFor } from './_views.js';

class FakeChart {
  static instances = [];
  constructor(canvas) {
    this.canvas = canvas;
    this.destroyed = false;
    FakeChart.instances.push(this);
  }
  destroy() {
    this.destroyed = true;
  }
}

const usage = (today) => ({ daily_budget_usd: 15, today_usd: today, total_usd: today * 3, by_day: [], by_provider: [], by_operation: [] });

test('usage: a late response or chart of an older period never survives a newer one', async () => {
  resetDom();
  const slow7 = deferred();
  mockFetch({
    'GET /api/usage/me?days=30': usage(1),
    'GET /api/usage/me?days=7': () => slow7.promise,
    'GET /api/usage/me?days=90': usage(9),
  });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mount(container, { app: fakeApp(), params: {}, query: {} });
  const script = await waitFor(() => document.head.querySelector('script[src*="chartjs"]'));
  const range = container.querySelector('select');
  // 30 d (rendered, chart pending) -> 7 d (slow) -> 90 d (fast).
  range.value = '7';
  range.dispatchEvent(new window.Event('change'));
  range.value = '90';
  range.dispatchEvent(new window.Event('change'));
  await waitFor(() => /\$9/.test(container.textContent));
  slow7.resolve(usage(7));
  await tick(10);
  assert.doesNotMatch(container.textContent, /\$7\.00/, "the 7-day answer arrived last but is ignored");
  window.Chart = FakeChart;
  script.onload();
  await tick(10);
  const alive = FakeChart.instances.filter((c) => !c.destroyed);
  assert.equal(alive.length, 1);
  assert.equal(alive[0].canvas.isConnected, true);
  handle.destroy();
  assert.equal(FakeChart.instances.filter((c) => !c.destroyed).length, 0);
});
