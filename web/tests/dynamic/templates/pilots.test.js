// @ts-check
// The B2c pilot templates as the stage registers them (B2 plan §A step 3, §1.4 template contract): stage.js TEMPLATES
// holds exactly the catalogue's `implemented` ids, and for every pilot the media frames it shows are the frames the
// render extracts: each `/__frames/<key>/<n>.jpg` a pilot's page requests, through `ctx.media` and its static
// `mediaBox` (also when formula_focus hands a scene with a panel to board_generic), is an entry and a frame of
// `stage.mediaPlan(0, frames)` (plan §1.4: "mediaBox is the single function both the plan and ctx.media use"; a frame
// outside the plan is a blank picture in the MP4). Each template's own behaviour is its own test file.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { TEMPLATES } from '../../../js/dynamic/stage.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { installDom, uninstallDom } from '../../player/_dom.js';
import { recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const PILOTS = ['formula_focus', 'list_build', 'media_split'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
const FIXTURES = readdirSync(DIR).filter((n) => n.endsWith('.json')).sort();
/** @param {string} name */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));

test('stage.js registers the three pilots, and the catalogue lists exactly the registered templates as implemented', () => {
  for (const id of PILOTS) {
    assert.ok(TEMPLATES[id], `${id} is registered`);
    assert.equal(TEMPLATES[id].id, id);
    assert.equal(CATALOGUE.templates[id].implemented, true, `${id}: implemented`);
  }
  const implemented = Object.keys(CATALOGUE.templates).filter((id) => CATALOGUE.templates[id].implemented).sort();
  assert.deepEqual(Object.keys(TEMPLATES).sort(), implemented);
  for (const id of PILOTS) {
    assert.ok(FIXTURES.some((n) => n.startsWith(`${id}.landscape`)) && FIXTURES.some((n) => n.startsWith(`${id}.portrait`)),
      `${id}: a fixture in each format`);
  }
});

const CLIP = { kind: 'video', asset_key: 'pilot-clip', url: '/media/pilot.mp4', duration: 30, width: 1920, height: 1080, fit: 'contain', end_behavior: 'freeze' };
const MAIN = { kind: 'video', asset_key: 'pilot-main', url: '/media/main.mp4', duration: 40, width: 1280, height: 720, fit: 'contain', end_behavior: 'loop' };

/**
 * A fixture with clips on its first scene drawn by its template: a video side panel shown from 0.4 s and (variant
 * `main`) a main video, each a case a pilot may draw (media_split, list_build's 16:9 panel) or hand on (formula_focus
 * gives a scene with a panel to board_generic).
 * @param {string} name @param {'panel' | 'main'} variant
 */
function withClips(name, variant) {
  const tl = load(name);
  const scene = tl.scenes.find((/** @type {any} */ s) => s.look && s.look.template === name.split('.')[0]);
  if (variant === 'panel') {
    scene.side_panel = { panel: { kind: 'figure', title: 'A clip', rationale: '', figure_id: 'fig' }, media: { ...CLIP }, show_at: 0.4 };
  } else {
    scene.media = { ...MAIN };
  }
  return tl;
}

/** The (key, frame) pairs the page requests: every `/__frames/<key>/<n>.jpg` source in the stage. @param {Element} el */
function requested(el) {
  /** @type {string[]} */
  const out = [];
  for (const img of el.querySelectorAll('img')) {
    const m = /\/__frames\/([^/]+)\/(\d{6})\.jpg$/.exec(String(img.getAttribute('src') || ''));
    if (m) out.push(`${m[1]}#${Number(m[2])}`);
  }
  return out;
}

test('every media frame a pilot shows is a frame of mediaPlan(0, frames) (clips on a panel and as the main medium)', async () => {
  let shown = 0;
  for (const name of FIXTURES.filter((n) => PILOTS.includes(n.split('.')[0]))) {
    for (const variant of /** @type {const} */ (['panel', 'main'])) {
      const tl = withClips(name, variant);
      const { stage, done } = renderStage(tl, { renderTex: recordingTex().renderTex });
      try {
        const info = stage.info();
        const plan = stage.mediaPlan(0, info.frames);
        const planned = new Set(plan.flatMap((e) => e.frames.map((f) => `${e.key}#${f}`)));
        for (let f = 0; f < info.frames; f += 4) {
          await stage.seek(f);
          for (const pair of requested(stage.el)) {
            shown += 1;
            assert.ok(planned.has(pair), `${name} (${variant}) frame ${f}: ${pair} is not in the plan ${JSON.stringify(plan.map((e) => [e.key, e.frames.length]))}`);
          }
        }
      } finally {
        done();
      }
    }
  }
  assert.ok(shown > 0, 'some pilot showed a clip (the check ran)');
});
