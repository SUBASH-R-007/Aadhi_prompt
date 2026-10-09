// The in-browser video preview (components/videoPreview.js): one retry with a fresh link when the signed link no
// longer loads, then a warning that does not blame the browser; focus back to a redrawn opener on close.
import { resetDom, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { openVideoPreview } from '../../js/studio/components/videoPreview.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { clickModal } from './_views.js';

/** Let pending promise chains settle. */
async function settle(rounds = 8) {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

function fresh() {
  resetDom();
  closeAllModals();
}

const warning = () => /** @type {HTMLElement} */ (document.querySelector('.modal [role="alert"].notice'));

test('preview: a link that does not load is replaced once by a fresh one, then the warning shows', async () => {
  fresh();
  const asked = [];
  openVideoPreview({
    title: 'Video',
    src: '/s3/v.mp4?sig=old',
    refresh: async () => {
      asked.push(1);
      return '/s3/v.mp4?sig=new';
    },
  });
  const video = /** @type {HTMLVideoElement} */ (document.querySelector('.modal video'));
  video.dispatchEvent(new window.Event('error'));
  await settle();
  assert.equal(asked.length, 1, 'a fresh link is asked for');
  assert.match(video.getAttribute('src') || '', /sig=new$/);
  assert.equal(warning().hidden, true, 'no warning while the fresh link is tried');
  video.dispatchEvent(new window.Event('error'));
  await settle();
  assert.equal(asked.length, 1, 'only once');
  assert.equal(warning().hidden, false);
  assert.match(warning().textContent, /could not be loaded here/);
  assert.doesNotMatch(warning().textContent, /cannot be played in the browser/);
  clickModal('Close');
  await settle();
  assert.equal(video.hasAttribute('src'), false, 'closing releases the video');
});

test('preview: without a way to refresh (or when it finds nothing) the first error shows the warning', async () => {
  fresh();
  openVideoPreview({ title: 'Video', src: '/v.mp4' });
  const video = document.querySelector('.modal video');
  video.dispatchEvent(new window.Event('error'));
  await settle();
  assert.equal(warning().hidden, false);
  closeAllModals();

  openVideoPreview({ title: 'Video', src: '/v.mp4', refresh: async () => null });
  const other = document.querySelector('.modal video');
  other.dispatchEvent(new window.Event('error'));
  await settle();
  assert.equal(warning().hidden, false);
  assert.match(other.getAttribute('src') || '', /\/v\.mp4$/, 'the link is not changed');
  closeAllModals();
});

test('preview: closing gives focus back to the opener, or to a redrawn one when the opener is gone', async () => {
  fresh();
  const host = document.createElement('div');
  document.body.appendChild(host);
  const make = () => {
    const b = document.createElement('button');
    b.dataset.fk = 'watch:1';
    b.textContent = 'Watch';
    host.replaceChildren(b);
    return b;
  };
  const first = make();
  first.focus();
  openVideoPreview({ title: 'Video', src: '/v.mp4', returnFocus: () => host.querySelector('[data-fk="watch:1"]') });
  clickModal('Close');
  assert.equal(document.activeElement, first, 'the opener when it is still there');
  first.focus();
  openVideoPreview({ title: 'Video', src: '/v.mp4', returnFocus: () => host.querySelector('[data-fk="watch:1"]') });
  const redrawn = make(); // the list was drawn again while the video played
  clickModal('Close');
  assert.equal(document.activeElement, redrawn);
});
