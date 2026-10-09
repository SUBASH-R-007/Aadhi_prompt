import { resetDom } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { boardEditor } from '../../js/studio/views/editor/boardEditor.js';
import { sampleScreenplay } from './fixtures.js';

/** Inspector context for scene s2 whose formula s2-i2 (revealed by beat 2) has a legend. */
function setup(variables) {
  resetDom();
  const sp = sampleScreenplay();
  let scene = sp.scenes.find((s) => s.id === 's2');
  scene = { ...scene, board: scene.board.map((i) => (i.id === 's2-i2' ? { ...i, variables } : i)) };
  const edits = [];
  const ctx = {
    sp,
    scene,
    meta: {},
    projectId: 5,
    maxUploadMb: 50,
    editScene: (updater, opts) => {
      const copy = structuredClone(scene);
      const out = updater(copy) || copy;
      edits.push({ scene: out, opts });
    },
    editScreenplay: () => {},
    selectedBeat: null,
    selectBeat: () => {},
    regenerateScene: () => {},
    duplicateScene: () => {},
    deleteScene: () => {},
    openSections: new Set(),
  };
  const el = boardEditor(ctx);
  document.body.appendChild(el);
  return { el, edits };
}

test('a legend row can appear with a chosen beat from the formula reveal on (empty = automatic)', () => {
  const { el, edits } = setup([
    { symbol_latex: 'V', meaning: 'voltage', unit: 'V', beat_id: 's2-b3' },
    { symbol_latex: 'I', meaning: 'current', unit: 'A' },
  ]);
  const first = el.querySelector('[data-fk="item:s2-i2:var:0:beat"]');
  const second = el.querySelector('[data-fk="item:s2-i2:var:1:beat"]');
  assert.ok(first && second, 'one choice per legend row');
  assert.equal(first.value, 's2-b3');
  assert.equal(second.value, '');
  // beats before the reveal (s2-b1) are not offered
  assert.deepEqual([...second.options].map((o) => o.value), ['', 's2-b2', 's2-b3']);
  assert.match(second.options[1].textContent, /^With beat 2: Here is the formula\./);
  second.value = 's2-b2';
  second.dispatchEvent(new window.Event('change'));
  const formula = (e) => e.scene.board.find((i) => i.id === 's2-i2');
  assert.equal(formula(edits.at(-1)).variables[1].beat_id, 's2-b2');
  first.value = '';
  first.dispatchEvent(new window.Event('change'));
  assert.equal('beat_id' in formula(edits.at(-1)).variables[0], false, 'automatic again: the field is left out');
});
