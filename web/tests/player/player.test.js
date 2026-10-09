// @ts-check
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex, advanceMedia, tick } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { Player, PLAYER_EVENTS, BACKGROUND_TICK_MS } = await import('../../js/player/player.js');
const { renderStateList, sceneStateAt } = await import('../../js/player/schedule.js');
const { computeZones } = await import('../../js/player/layout.js');

after(() => uninstallDom());

/** Fake side-panel module recording calls. */
function fakePanels() {
  /** @type {any[]} */
  const created = [];
  return {
    created,
    /** @type {(container: HTMLElement, rsp: any, ctx: any) => any} */
    createPanel(container, rsp, ctx) {
      const el = document.createElement('div');
      el.className = 'fake-panel';
      container.appendChild(el);
      const rec = { rsp, ctx, updates: /** @type {any[]} */ ([]), destroyed: false, readyCalls: 0 };
      created.push(rec);
      return {
        el,
        update: (/** @type {number} */ t, /** @type {any} */ s) => rec.updates.push({ t, visible: s.panelVisible, lines: s.terminalLines.length }),
        mediaRect: () => ({ x: 1500, y: 300, width: 320, height: 180 }),
        ready: () => {
          rec.readyCalls++;
          return Promise.resolve();
        },
        destroy: () => {
          rec.destroyed = true;
          el.remove();
        },
      };
    },
    /** @param {any} scene */
    panelStateTimes(scene) {
      return scene.side_panel ? [scene.side_panel.show_at, scene.duration - 1] : [];
    },
  };
}

/**
 * @param {'live' | 'preview' | 'render'} mode
 * @param {Partial<import('../../js/player/player.js').PlayerOptions>} [opts]
 */
function setup(mode, opts = {}) {
  let now = 0;
  /** @type {Map<number, FrameRequestCallback>} */
  const frames = new Map();
  let fid = 0;
  /** @type {any[]} */
  const sent = [];
  const panels = fakePanels();
  const tex = fakeTex();
  /** @type {Promise<unknown>} */
  let texGate = Promise.resolve();
  const root = document.createElement('div');
  document.body.appendChild(root);
  const player = new Player(root, {
    mode,
    analytics: mode === 'live' ? { shareToken: 'share-token-123456' } : null,
    ...opts,
    deps: {
      renderTex: tex.render,
      texIdle: () => texGate,
      loadPrism: async () => null,
      createPanel: panels.createPanel,
      panelStateTimes: panels.panelStateTimes,
      now: () => now,
      requestFrame: (cb) => {
        frames.set(++fid, cb);
        return fid;
      },
      cancelFrame: (id) => frames.delete(id),
      sendAnalytics: async (body) => {
        sent.push(body);
      },
      waitForFonts: false,
      ...(opts.deps || {}),
    },
  });
  /** @type {Record<string, any[]>} */
  const events = {};
  for (const name of PLAYER_EVENTS) player.on(name, (d) => (events[name] = events[name] || []).push(d));
  return {
    player,
    root,
    events,
    sent,
    panels,
    tex,
    /** @param {Promise<unknown>} p */
    gateTex(p) {
      texGate = p;
    },
    /** Advance the clock (and playing media) by ms and run one animation frame. */
    step(/** @type {number} */ ms) {
      now += ms;
      advanceMedia(ms / 1000);
      const pending = [...frames.values()];
      frames.clear();
      for (const cb of pending) cb(now);
    },
    /** @param {string} id */
    item(id) {
      return /** @type {HTMLElement | null} */ (root.querySelector(`[data-item="${id}"]`));
    },
  };
}

/** @type {any[]} */
let live = [];
afterEach(() => {
  for (const p of live) p.destroy();
  live = [];
  document.body.replaceChildren();
});

describe('Player: load (live)', () => {
  test('builds the fixed stage, intro first, emits ready', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const el = /** @type {HTMLElement} */ (h.root.querySelector('.aadhi-player.mode-live'));
    assert.ok(el);
    const stage = /** @type {HTMLElement} */ (el.querySelector('.ap-viewport > .ap-stage'));
    assert.equal(stage.style.width, '1920px');
    assert.equal(stage.style.height, '1080px');
    assert.equal(h.events.ready?.[0].duration, timeline.total_duration);
    assert.equal(h.player.sceneIndex, -1, 'intro is showing');
    assert.equal(h.root.querySelector('.ap-intro')?.classList.contains('is-hidden'), false);
    assert.equal(el.lang, 'en-IN');
    assert.equal(h.root.querySelectorAll('.ap-mascot-clip').length, 5, 'distinct mascot clips preloaded');
    assert.ok([...h.root.querySelectorAll('.ap-mascot-clip')].every((v) => /** @type {HTMLVideoElement} */ (v).muted));
    const deck = /** @type {any} */ (h.player).narration;
    const first = timeline.scenes[0];
    assert.ok(
      deck.slots.some((/** @type {any} */ s) => s.index === 0 && String(s.el.getAttribute('src')).endsWith(String(first.audio_url))),
      'first scene narration preloads during the intro',
    );
  });

  test('rejects malformed timelines', async () => {
    const h = setup('live');
    live.push(h.player);
    await assert.rejects(() => h.player.load(/** @type {any} */ ({ scenes: 'nope' })), TypeError);
  });
});

describe('Player: seeking and scene rendering (live)', () => {
  test('seek renders exactly the schedule state of the target scene', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    h.player.seek(ohm.start + 0.5);
    assert.equal(h.player.sceneIndex, 1);
    assert.equal(h.events.scenechange?.at(-1).index, 1);
    assert.ok(h.item('i-def')?.classList.contains('is-visible'));
    assert.ok(h.item('i-def')?.classList.contains('is-active'));
    assert.equal(h.item('i-formula')?.classList.contains('is-visible'), false);
    assert.equal(h.root.querySelector('.ap-caption-text')?.textContent, 'Resistance opposes the flow of current.');
    assert.equal(h.root.querySelector('.ap-intro')?.classList.contains('is-hidden'), true);
    const scene = /** @type {HTMLElement} */ (h.root.querySelector('.ap-scene'));
    assert.equal(scene.dataset.position, 'left');
    assert.equal(scene.style.getPropertyValue('--board-x'), `${computeZones('left', { sidePanel: true }).board.x}px`);
    h.player.seek(ohm.start + 7.2);
    assert.ok(h.item('i-formula')?.classList.contains('is-highlight'));
    const tu = h.events.timeupdate.at(-1);
    assert.equal(tu.sceneIndex, 1);
    assert.ok(Math.abs(tu.sceneT - 7.2) < 1e-9);
  });

  test('side panel: created through createPanel with ctx, shown from show_at, updated with state', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    h.player.seek(ohm.start + 1);
    const rec = h.panels.created.at(-1);
    assert.equal(rec.rsp.panel.kind, 'terminal');
    assert.equal(rec.ctx.mode, 'live');
    assert.equal(rec.ctx.sceneIndex, 1);
    assert.equal(rec.ctx.conceptState.activeId, 'ohms-law');
    assert.ok(rec.ctx.stageEl.classList.contains('ap-stage'));
    const zone = /** @type {HTMLElement} */ (h.root.querySelector('.ap-zone--side'));
    assert.equal(zone.classList.contains('is-visible'), false);
    h.player.seek(ohm.start + ohm.duration - 0.1);
    assert.ok(zone.classList.contains('is-visible'));
    assert.deepEqual(rec.updates.at(-1), { t: ohm.duration - 0.1, visible: true, lines: 3 });
    h.player.seek(sceneById(timeline, 's-chapter').start);
    assert.equal(rec.destroyed, true, 'panel destroyed when leaving the scene');
  });

  test('scene types mount their views; quiz answers become events and analytics', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const quiz = sceneById(timeline, 's-quiz');
    h.player.seek(quiz.start + 1);
    const opt = /** @type {HTMLButtonElement} */ (h.root.querySelector('button.ap-quiz-opt[data-index="2"]'));
    opt.click();
    assert.deepEqual(h.events.quizanswer[0], { sceneIndex: quiz.index, sceneId: 's-quiz', choice: 2, correct: true });
    h.player.seek(sceneById(timeline, 's-chapter').start + 1);
    assert.ok(h.root.querySelector('.ap-card-title'));
    h.player.seek(sceneById(timeline, 's-play').start + 1);
    assert.equal(h.root.querySelector('iframe')?.getAttribute('sandbox'), 'allow-scripts');
    h.player.seek(sceneById(timeline, 's-sim').start + 1);
    assert.equal(h.root.querySelector('iframe'), null, 'interactive scene torn down');
    assert.ok(h.root.querySelector('video.ap-media-el'));
  });

  test('seeking back into the intro announces scenechange -1 (no scene)', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    assert.deepEqual(h.events.scenechange?.at(-1), { index: -1, scene: null }, 'load starts in the intro');
    h.player.seek(timeline.scenes[3].start + 1);
    assert.equal(h.events.scenechange.at(-1).index, 3);
    h.player.seek(2);
    assert.equal(h.player.sceneIndex, -1);
    assert.deepEqual(h.events.scenechange.at(-1), { index: -1, scene: null });
    assert.equal(h.root.querySelector('.ap-scene'), null);
  });

  test('media scenes in the default popup layout get a large media box', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    const sim = sceneById(timeline, 's-sim');
    sim.layout = { ...sim.layout, mascot_position: 'popup_bottom_left' }; // canonical default for media scenes
    await h.player.load(timeline);
    h.player.seek(sim.start + 1);
    const view = /** @type {any} */ (h.player).sceneView;
    assert.equal(/** @type {HTMLElement} */ (h.root.querySelector('.ap-scene')).dataset.position, 'popup');
    assert.ok(view.rect.w >= 1200 && view.rect.h >= 680, `media ${view.rect.w}x${view.rect.h}`);
    assert.equal(view.box.style.width, `${view.rect.w}px`);
    const play = sceneById(timeline, 's-play'); // fixture: interactive in popup_bottom_left
    assert.equal(play.layout?.mascot_position, 'popup_bottom_left');
    h.player.seek(play.start + 1);
    const box = /** @type {HTMLElement} */ (h.root.querySelector('.ap-interactive-box'));
    assert.ok(parseInt(box.style.width, 10) >= 1200, `sketch ${box.style.width}`);
  });

  test('seekScene clamps and jumps to scene starts', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seekScene(3);
    assert.equal(h.player.currentTime, timeline.scenes[3].start);
    h.player.seekScene(99);
    assert.equal(h.player.sceneIndex, timeline.scenes.length - 1);
    h.player.seekScene(-4);
    assert.equal(h.player.sceneIndex, 0);
    h.player.seek(1e9);
    assert.equal(h.player.currentTime, timeline.total_duration);
  });
});

describe('Player: playback (live)', () => {
  test('narration audio is the master clock and starts at audio_offset', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    h.player.seek(ohm.start);
    const deck = /** @type {any} */ (h.player).narration;
    const audio = /** @type {HTMLAudioElement} */ (deck.current.el);
    assert.equal(audio.getAttribute('src'), 'http://localhost/media/assets/audio/s-ohm.mp3');
    h.player.play();
    assert.ok(audio.paused, 'lead-in: narration not started yet');
    for (let i = 0; i < 40; i++) h.step(16); // 0.64 s
    assert.equal(audio.paused, false, 'narration started after audio_offset');
    for (let i = 0; i < 60; i++) h.step(16);
    const t = h.player.currentTime - ohm.start;
    assert.ok(Math.abs(audio.currentTime - (t - (ohm.audio_offset || 0))) < 0.3, 'audio and clock agree');
    assert.ok(h.events.play.length === 1);
    h.player.pause();
    assert.ok(audio.paused);
    assert.ok(h.events.pause.length === 1);
  });

  test('buffering narration holds the clock and shows the buffering state', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    h.player.seek(ohm.start + 2);
    h.player.play();
    for (let i = 0; i < 10; i++) h.step(16);
    const audio = /** @type {any} */ (h.player).narration.current.el;
    assert.equal(audio.paused, false);
    assert.equal(h.player.buffering, false);
    Object.defineProperty(audio, 'readyState', { configurable: true, value: 1 }); // network stall
    const frozen = audio.currentTime;
    /** @param {number} n */
    const run = (n) => {
      for (let i = 0; i < n; i++) {
        h.step(16);
        audio.currentTime = frozen; // a stalled element does not advance
      }
    };
    run(5);
    const held = h.player.currentTime;
    run(60); // ~1 s of wall time
    assert.ok(h.player.currentTime - held < 0.3, `clock waits for the narration (moved ${h.player.currentTime - held})`);
    assert.equal(h.player.buffering, true);
    const el = /** @type {HTMLElement} */ (h.root.querySelector('.aadhi-player'));
    assert.ok(el.classList.contains('is-buffering'));
    assert.deepEqual(h.events.buffering?.at(-1), { buffering: true });
    delete audio.readyState; // data arrived
    for (let i = 0; i < 5; i++) h.step(16);
    assert.equal(h.player.buffering, false);
    assert.equal(el.classList.contains('is-buffering'), false);
    assert.ok(h.player.currentTime > held, 'playback continues where it waited');
    Object.defineProperty(audio, 'readyState', { configurable: true, value: 1 });
    h.step(16);
    assert.equal(h.player.buffering, true);
    h.player.pause();
    assert.equal(h.player.buffering, false, 'pausing clears the buffering state');
    delete audio.readyState;
  });

  test('a hidden tab keeps advancing on a slow timer; visible tabs use animation frames', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(sceneById(timeline, 's-ohm').start + 1);
    const setVisibility = (/** @type {string} */ v) => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => v });
      document.dispatchEvent(new window.Event('visibilitychange'));
    };
    const p = /** @type {any} */ (h.player);
    try {
      setVisibility('hidden');
      assert.equal(p._bgTimer, null, 'paused: no timer');
      h.player.play();
      assert.notEqual(p._bgTimer, null, 'playing while hidden: timer runs');
      const before = h.events.timeupdate.length;
      // Poll with a generous deadline: under CPU load timers fire late, never early.
      const deadline = Date.now() + 5000;
      while (h.events.timeupdate.length < before + 2 && Date.now() < deadline) await tick(BACKGROUND_TICK_MS);
      assert.ok(h.events.timeupdate.length >= before + 2, 'frames rendered without rAF');
      setVisibility('visible');
      assert.equal(p._bgTimer, null, 'visible again: rAF only');
      setVisibility('hidden');
      assert.notEqual(p._bgTimer, null);
      h.player.pause();
      assert.equal(p._bgTimer, null, 'pause stops the timer');
      h.player.play();
      h.player.destroy();
      assert.equal(p._bgTimer, null, 'destroy stops the timer');
    } finally {
      delete (/** @type {any} */ (document)).visibilityState;
    }
  });

  test('plays through scene boundaries: preloads, scene_enter / scene_complete analytics', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const chapter = sceneById(timeline, 's-chapter');
    h.player.seek(chapter.start + chapter.duration - 0.2);
    h.player.play();
    for (let i = 0; i < 30; i++) h.step(16);
    assert.equal(h.player.sceneIndex, chapter.index + 1);
    const analytics = /** @type {any} */ (h.player).analytics;
    await analytics.flush();
    const events = h.sent.flatMap((b) => b.events);
    assert.equal(h.sent[0].share_token, 'share-token-123456');
    assert.match(h.sent[0].viewer_id, /^[A-Za-z0-9_-]{16,64}$/);
    const names = events.map((e) => e.event);
    assert.deepEqual(names.slice(0, 2), ['seek', 'session_start']);
    const complete = events.find((e) => e.event === 'scene_complete');
    assert.equal(complete.scene_id, 's-chapter');
    const enter = events.filter((e) => e.event === 'scene_enter').at(-1);
    assert.equal(enter.scene_id, 's-example');
    assert.equal(enter.scene_t, 0);
    assert.ok(events.every((e) => typeof e.t === 'number'));
    const deck = /** @type {any} */ (h.player).narration;
    assert.ok(deck.slots.some((/** @type {any} */ s) => s.index === chapter.index + 2), 'next scene narration preloaded');
  });

  test('seeking does not count as completing a scene', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(timeline.scenes[1].start + 1);
    h.player.play();
    h.step(16);
    h.player.seekScene(2);
    h.step(16);
    const analytics = /** @type {any} */ (h.player).analytics;
    await analytics.flush();
    const names = h.sent.flatMap((b) => b.events).map((/** @type {any} */ e) => e.event);
    assert.equal(names.includes('scene_complete'), false);
    assert.ok(names.includes('seek'));
  });

  test('seek events are attributed to the scene at the target time (none in the intro)', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    const example = sceneById(timeline, 's-example');
    h.player.seek(ohm.start + 2);
    h.player.seek(example.start + 3);
    h.player.seek(1); // back into the intro
    const analytics = /** @type {any} */ (h.player).analytics;
    await analytics.flush();
    const seeks = h.sent.flatMap((b) => b.events).filter((/** @type {any} */ e) => e.event === 'seek');
    assert.equal(seeks.length, 3);
    assert.equal(seeks[0].scene_id, 's-ohm');
    assert.ok(Math.abs(seeks[0].scene_t - 2) < 1e-6);
    assert.equal(seeks[1].scene_id, 's-example');
    assert.ok(Math.abs(seeks[1].scene_t - 3) < 1e-6);
    assert.deepEqual(seeks[1].data, { from: ohm.start + 2, to: example.start + 3 });
    assert.equal(seeks[2].scene_id, undefined, 'intro has no scene');
    assert.equal(seeks[2].scene_t, undefined);
  });

  test('reaching the end pauses, emits ended and complete; play() restarts', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(timeline.total_duration - 0.05);
    h.player.play();
    for (let i = 0; i < 10; i++) h.step(16);
    assert.equal(h.events.ended.length, 1);
    assert.equal(h.events.ended[0].completed, true);
    assert.equal(h.player.paused, true);
    assert.equal(h.player.ended, true);
    assert.equal(h.player.currentTime, timeline.total_duration);
    assert.equal(h.events.timeupdate.at(-1).t, timeline.total_duration, 'the final frame is rendered');
    await tick();
    const events = h.sent.flatMap((b) => b.events);
    const names = events.map((/** @type {any} */ e) => e.event);
    assert.ok(names.includes('complete'));
    assert.equal(events.find((/** @type {any} */ e) => e.event === 'scene_complete')?.scene_id, timeline.scenes.at(-1)?.scene_id);
    h.player.play();
    assert.ok(h.player.currentTime < 0.1, 'restarted from the beginning');
  });

  test('seeking to the end while playing stops there without counting a completion', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(timeline.scenes[1].start + 1);
    h.player.play();
    for (let i = 0; i < 10; i++) h.step(16);
    h.player.seek(timeline.total_duration); // scrubber drag / End key
    assert.equal(h.player.paused, true);
    assert.equal(h.player.ended, true);
    assert.equal(h.player.currentTime, timeline.total_duration);
    assert.equal(h.events.ended.at(-1).completed, false);
    assert.equal(h.player.sceneIndex, timeline.scenes.length - 1, 'the final frame shows the last scene');
    assert.equal(/** @type {HTMLElement} */ (h.root.querySelector('.ap-scene')).dataset.sceneIndex, String(timeline.scenes.length - 1));
    const deck = /** @type {any} */ (h.player).narration;
    assert.ok(deck.current.el.paused, 'narration stopped');
    for (let i = 0; i < 5; i++) h.step(16);
    assert.equal(h.events.ended.length, 1, 'stays ended');
    await /** @type {any} */ (h.player).analytics.flush();
    const names = h.sent.flatMap((b) => b.events).map((/** @type {any} */ e) => e.event);
    assert.ok(names.includes('seek'));
    assert.equal(names.includes('complete'), false);
    assert.equal(names.includes('scene_complete'), false);
    h.player.play();
    assert.ok(h.player.currentTime < 0.1, 'play() after the end restarts');
  });

  test('a seek near the end, then playing through, still completes', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.play();
    h.player.seek(timeline.total_duration - 0.1);
    for (let i = 0; i < 12; i++) h.step(16);
    assert.equal(h.events.ended.at(-1).completed, true);
    await /** @type {any} */ (h.player).analytics.flush();
    assert.ok(h.sent.flatMap((b) => b.events).some((/** @type {any} */ e) => e.event === 'complete'));
  });

  test('playing into an interactive scene runs the sketch when the sandbox reports ready', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const play = sceneById(timeline, 's-play');
    h.player.seek(play.start - 0.5);
    h.player.play();
    for (let i = 0; i < 60; i++) h.step(16);
    assert.equal(h.player.sceneIndex, play.index);
    assert.equal(h.player.paused, false);
    const iframe = /** @type {HTMLIFrameElement} */ (h.root.querySelector('iframe.ap-sandbox'));
    /** @type {string[]} */
    const posted = [];
    /** @type {any} */ (iframe.contentWindow).postMessage = (/** @type {any} */ m) => posted.push(m.type);
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'ready' }, origin: 'null', source: iframe.contentWindow }));
    assert.deepEqual(posted, ['run'], 'no pause while the lecture plays');
    h.player.pause();
    assert.deepEqual(posted, ['run', 'pause']);
    h.player.play();
    assert.deepEqual(posted, ['run', 'pause', 'resume']);
  });

  test('seeking into an interactive scene while playing runs the sketch too', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(timeline.scenes[1].start + 1);
    h.player.play();
    h.step(16);
    h.player.seek(sceneById(timeline, 's-play').start + 1);
    const view = /** @type {any} */ (h.player).sceneView;
    assert.equal(view.playing, true, 'told about the playing clock on mount');
    const iframe = /** @type {HTMLIFrameElement} */ (h.root.querySelector('iframe.ap-sandbox'));
    /** @type {string[]} */
    const posted = [];
    /** @type {any} */ (iframe.contentWindow).postMessage = (/** @type {any} */ m) => posted.push(m.type);
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'ready' }, origin: 'null', source: iframe.contentWindow }));
    assert.deepEqual(posted, ['run']);
  });

  test('load() again starts over: fresh scene, one frame per tick, new analytics session', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(timeline.scenes[0].start + 1);
    h.player.play();
    h.step(16);
    assert.equal(h.player.sceneIndex, 0);
    await h.player.load(loadTimeline());
    const p = /** @type {any} */ (h.player);
    assert.equal(h.player.paused, true);
    assert.equal(h.player.ended, false);
    assert.equal(p.sessionStarted, false);
    assert.equal(h.player.sceneIndex, -1, 'the intro shows again');
    h.player.seek(timeline.scenes[0].start + 1);
    assert.equal(h.player.sceneIndex, 0);
    assert.equal(h.root.querySelectorAll('.ap-scene').length, 1, 'scene mounted after the re-load');
    assert.ok(p.sceneView, 'scene view exists');
    let frames = 0;
    const render = p._renderFrame.bind(p);
    p._renderFrame = (/** @type {number} */ t) => {
      frames++;
      render(t);
    };
    h.player.play();
    frames = 0;
    h.step(16);
    assert.equal(frames, 1, 'one render per animation frame after a re-load');
    await p.analytics.flush();
    const starts = h.sent.flatMap((b) => b.events).filter((/** @type {any} */ e) => e.event === 'session_start');
    assert.equal(starts.length, 2, 'the re-loaded lecture starts its own session');
  });

  test('overlapping load() calls: the latest wins, nothing from the first one leaks', async () => {
    const h = setup('live');
    live.push(h.player);
    const a = loadTimeline();
    const b = loadTimeline();
    b.scenes[0].title = 'Second timeline';
    const first = h.player.load(a);
    const second = h.player.load(b);
    await Promise.all([first, second]);
    assert.equal(/** @type {any} */ (h.player).timeline, b);
    assert.equal(h.events.ready.length, 1, 'only the latest load announces ready');
    assert.equal(h.root.querySelectorAll('.ap-mascot-clip').length, 5, 'one set of mascot clips');
    assert.equal(h.root.querySelectorAll('.ap-intro').length, 1, 'one intro');
    let frames = 0;
    const p = /** @type {any} */ (h.player);
    const render = p._renderFrame.bind(p);
    p._renderFrame = (/** @type {number} */ t) => {
      frames++;
      render(t);
    };
    h.player.play();
    frames = 0;
    h.step(16);
    assert.equal(frames, 1);
  });

  test('rate, volume, mute and captions are applied and announced', async () => {
    const h = setup('live');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(timeline.scenes[1].start + 1);
    const deck = /** @type {any} */ (h.player).narration;
    h.player.setRate(1.5);
    assert.equal(deck.current.el.playbackRate, 1.5);
    assert.equal(h.events.ratechange.at(-1).rate, 1.5);
    h.player.setVolume(0.4);
    assert.ok(Math.abs(deck.current.el.volume - 0.4) < 1e-9);
    h.player.setMuted(true);
    assert.equal(deck.current.el.muted, true);
    assert.deepEqual(h.events.volumechange.at(-1), { volume: 0.4, muted: true });
    h.player.setCaptions(false);
    assert.ok(h.root.querySelector('.ap-captions')?.classList.contains('is-off'));
    h.player.setCaptionSize('l');
    assert.equal(/** @type {HTMLElement} */ (h.root.querySelector('.ap-captions')).dataset.size, 'l');
  });

  test('preview mode sends no analytics', async () => {
    const h = setup('preview', { analytics: null });
    live.push(h.player);
    await h.player.load(loadTimeline());
    h.player.play();
    h.step(16);
    assert.equal(/** @type {any} */ (h.player).analytics, null);
    assert.ok(h.root.querySelector('.aadhi-player.mode-preview'));
  });

  test('destroy releases media, panels, listeners and DOM', async () => {
    const h = setup('live');
    const timeline = loadTimeline();
    await h.player.load(timeline);
    h.player.seek(sceneById(timeline, 's-ohm').start + 4);
    h.player.play();
    const deck = /** @type {any} */ (h.player).narration;
    const audio = deck.current.el;
    const rec = h.panels.created.at(-1);
    h.player.destroy();
    assert.equal(h.root.children.length, 0);
    assert.equal(rec.destroyed, true);
    assert.equal(audio.getAttribute('src'), null);
    assert.ok(audio.paused);
    await tick();
    assert.ok(h.sent.length >= 1, 'analytics flushed on destroy');
    h.player.destroy(); // idempotent
    h.player.play(); // no-op after destroy
  });
});

describe('Player: render mode', () => {
  test('load builds a transparent render stage without audio, mascot or captions', async () => {
    const h = setup('render');
    live.push(h.player);
    await h.player.load(loadTimeline());
    const el = /** @type {HTMLElement} */ (h.root.querySelector('.aadhi-player'));
    assert.ok(el.classList.contains('render-mode'));
    assert.equal(/** @type {any} */ (h.player).narration, null);
    assert.equal(h.root.querySelectorAll('video').length, 0, 'no mascot clips or logo video');
    assert.ok(h.root.querySelector('.ap-captions')?.classList.contains('is-off'));
    h.player.play();
    assert.equal(h.player.paused, true, 'render mode never plays');
  });

  test('states(i) lists schedule states merged with panel state times', async () => {
    const h = setup('render');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    const states = h.player.states(1);
    assert.deepEqual(states, renderStateList(ohm, h.panels.panelStateTimes(ohm)));
    assert.ok(states.some((s) => Math.abs(s.t - (ohm.duration - 1)) < 1e-9), 'panel-only change included');
    assert.throws(() => h.player.states(99), RangeError);
  });

  test('renderState renders exactly sceneStateAt and returns matching keys', async () => {
    const h = setup('render');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    for (const s of h.player.states(1)) {
      const r = await h.player.renderState({ sceneIndex: 1, t: s.t });
      assert.equal(r.state_key, s.key);
      const expected = sceneStateAt(ohm, s.t);
      for (const item of ohm.board || []) {
        assert.equal(h.item(item.id)?.classList.contains('is-visible'), expected.visibleItemIds.has(item.id), `${item.id}@${s.t}`);
        assert.equal(h.item(item.id)?.classList.contains('is-active'), expected.activeItemId === item.id);
      }
      assert.equal(r.media_rect, null, 'board scenes have no media hole');
      assert.equal(r.media_fit, null);
      if (expected.panelVisible) assert.deepEqual(r.panel_media_rect, { x: 1500, y: 300, width: 320, height: 180, w: 320, h: 180 });
      else assert.equal(r.panel_media_rect, null);
      const sceneRoot = /** @type {HTMLElement} */ (h.root.querySelector('.ap-scene'));
      assert.equal(sceneRoot.style.opacity, '', 'no fade in render mode');
    }
  });

  test('media scenes report the media hole rect and fit', async () => {
    const h = setup('render');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const sim = sceneById(timeline, 's-sim');
    const r = await h.player.renderState({ sceneIndex: sim.index, t: 1 });
    assert.ok(r.media_rect);
    assert.deepEqual(Object.keys(/** @type {any} */ (r.media_rect)).sort(), ['h', 'height', 'w', 'width', 'x', 'y']);
    assert.equal(r.media_fit, 'contain');
    const broll = sceneById(timeline, 's-broll');
    const r2 = await h.player.renderState({ sceneIndex: broll.index, t: 2 });
    assert.equal(r2.media_fit, 'cover');
  });

  test('renderState waits for TeX and panels before resolving; calls are serialised', async () => {
    const h = setup('render');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    /** @type {() => void} */
    let release = () => {};
    h.gateTex(new Promise((r) => (release = () => r(undefined))));
    /** @type {string[]} */
    const order = [];
    const a = h.player.renderState({ sceneIndex: 1, t: 0.5 }).then(() => order.push('a'));
    const b = h.player.renderState({ sceneIndex: 3, t: 1 }).then(() => order.push('b'));
    await tick(50);
    assert.deepEqual(order, [], 'blocked on texIdle');
    release();
    await Promise.all([a, b]);
    assert.deepEqual(order, ['a', 'b']);
    assert.equal(h.player.sceneIndex, 3);
    assert.ok(h.panels.created[0].readyCalls >= 1, 'panel.ready() awaited');
  });

  test('renderIntro shows the title card at full opacity with a full-stage media rect', async () => {
    const h = setup('render');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    await h.player.renderState({ sceneIndex: 1, t: 1 });
    const states = h.player.introStates();
    assert.deepEqual(states.map((s) => s.t), [0, 5.5, 8.5]);
    const r = await h.player.renderIntro({ t: 9 });
    assert.equal(r.state_key, states[2].key);
    assert.equal(h.root.querySelector('.ap-intro-line1')?.textContent, 'Session 2');
    assert.equal(h.root.querySelector('.ap-intro-line2')?.textContent, "Ohm's Law");
    assert.equal(/** @type {HTMLElement} */ (h.root.querySelector('.ap-intro-card')).style.opacity, '1');
    assert.equal(h.root.querySelector('.ap-scene'), null, 'scene unmounted during the intro');
    assert.deepEqual(r.media_rect, { x: 0, y: 0, width: 1920, height: 1080, w: 1920, h: 1080 });
    assert.equal(r.media_fit, 'cover');
    const logo = await h.player.renderIntro({ t: 1 });
    assert.equal(logo.state_key, 'intro-logo');
    const back = await h.player.renderState({ sceneIndex: 0, t: 0 });
    assert.ok(back.state_key.startsWith('s0-'));
    assert.ok(h.root.querySelector('.ap-intro')?.classList.contains('is-hidden'));
  });

  test('load() again in render mode renders the requested scene, not a stale blank stage', async () => {
    const h = setup('render');
    live.push(h.player);
    const timeline = loadTimeline();
    await h.player.load(timeline);
    const ohm = sceneById(timeline, 's-ohm');
    const first0 = await h.player.renderState({ sceneIndex: 0, t: 1 });
    const firstOhm = await h.player.renderState({ sceneIndex: ohm.index, t: 3 });
    await h.player.load(loadTimeline());
    assert.equal(h.player.sceneIndex, -2, 'nothing mounted after a render-mode re-load');
    const again = await h.player.renderState({ sceneIndex: ohm.index, t: 3 });
    assert.equal(again.state_key, firstOhm.state_key);
    assert.equal(h.root.querySelectorAll('.ap-scene').length, 1);
    assert.ok(h.root.querySelectorAll('[data-item]').length > 0, 'board items rendered');
    await h.player.load(loadTimeline());
    const again0 = await h.player.renderState({ sceneIndex: 0, t: 1 });
    assert.equal(again0.state_key, first0.state_key);
    assert.equal(h.root.querySelectorAll('.ap-scene').length, 1);
    assert.equal(/** @type {HTMLElement} */ (h.root.querySelector('.ap-scene')).dataset.sceneIndex, '0');
  });

  test('invalid scene indexes reject', async () => {
    const h = setup('render');
    live.push(h.player);
    await h.player.load(loadTimeline());
    await assert.rejects(() => h.player.renderState({ sceneIndex: 42, t: 0 }), RangeError);
    // the queue keeps working after a failure
    const ok = await h.player.renderState({ sceneIndex: 0, t: 0 });
    assert.ok(ok.state_key);
  });
});
