// @ts-check
/**
 * Test helpers: load the fixture timeline and build small synthetic scenes.
 *
 * fixtures/timeline.json is a hand-written lecture (every scene type, board item kind, quiz,
 * side panels, intro) that validates against the backend contract; re-check it after editing with
 *   .venv/Scripts/python.exe -c "import json; from aadhi.schemas.timeline import Timeline;
 *     Timeline.model_validate(json.load(open('web/tests/player/fixtures/timeline.json', encoding='utf8')))"
 * (timeline.test.js checks the same time-base invariants in JS).
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

/** @returns {import('../../js/shared/types.js').Timeline} a fresh deep copy of the fixture */
export function loadTimeline() {
  return JSON.parse(readFileSync(join(here, 'fixtures', 'timeline.json'), 'utf8'));
}

/**
 * @param {import('../../js/shared/types.js').Timeline} timeline
 * @param {string} sceneId
 */
export function sceneById(timeline, sceneId) {
  const s = timeline.scenes.find((x) => x.scene_id === sceneId);
  if (!s) throw new Error(`no scene ${sceneId}`);
  return s;
}

/**
 * Minimal synthetic beat.
 * @param {Partial<import('../../js/shared/types.js').TimedBeat> & {start: number, speech_end: number, end: number}} b
 * @returns {import('../../js/shared/types.js').TimedBeat}
 */
export function beat(b) {
  return { beat_id: b.beat_id || `b${b.start}`, index: b.index ?? 0, phase: 'main', narration: 'x', highlight_item_ids: [], captions: [], ...b };
}
