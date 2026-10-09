// @ts-check
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { createSceneView, viewKind } = await import('../../js/player/scenes/index.js');
const { QuizSceneView } = await import('../../js/player/scenes/quiz.js');
const { MediaSceneView, DEFAULT_MEDIA_ASPECT, placeMediaBox, titleFitsOneLine } = await import('../../js/player/scenes/media.js');
const { InteractiveSceneView, HEARTBEAT_TIMEOUT_MS, LOAD_TIMEOUT_MS } = await import('../../js/player/scenes/interactive.js');
const { sceneStateAt } = await import('../../js/player/schedule.js');
const { computeZones, fitAspect } = await import('../../js/player/layout.js');

const timeline = loadTimeline();

/**
 * @param {any} scene
 * @param {Partial<import('../../js/player/scenes/types.js').SceneContext>} [extra]
 */
function ctxFor(scene, extra = {}) {
  /** @type {{event: string, detail: any}[]} */
  const events = [];
  /** @type {string[]} */
  const sounds = [];
  const ctx = {
    mode: /** @type {any} */ ('live'),
    timeline,
    scene,
    sceneIndex: scene.index,
    zones: computeZones(scene.layout?.mascot_position, { sidePanel: false }),
    renderTex: fakeTex().render,
    loadPrism: async () => null,
    emit: (/** @type {string} */ event, /** @type {any} */ detail) => events.push({ event, detail }),
    playSound: (/** @type {string} */ name) => sounds.push(name),
    ...extra,
  };
  return { ctx: /** @type {any} */ (ctx), events, sounds };
}

const play = { playing: true, rate: 1, seeked: false };
const seek = { playing: false, rate: 1, seeked: true };

afterEach(() => document.body.replaceChildren());
after(() => uninstallDom());

describe('scene factory', () => {
  test('maps scene types to views', () => {
    assert.equal(viewKind('content'), 'board');
    assert.equal(viewKind('title'), 'board');
    assert.equal(viewKind('mystery'), 'board');
    assert.equal(viewKind('chapter_card'), 'card');
    assert.equal(viewKind('simulation'), 'media');
    assert.equal(viewKind('ai_video'), 'media');
    assert.equal(viewKind('quiz_checkpoint'), 'quiz');
    assert.equal(viewKind('interactive'), 'interactive');
  });

  test('chapter card shows label, title and subtitle as text', () => {
    const { ctx } = ctxFor(sceneById(timeline, 's-chapter'));
    const view = createSceneView(ctx);
    assert.equal(view.el.querySelector('.ap-card-badge')?.textContent, 'Part 2');
    assert.equal(view.el.querySelector('.ap-card-title')?.textContent, 'Worked example');
    assert.equal(view.el.querySelector('.ap-card-subtitle')?.textContent, 'Finding the current');
    view.destroy();
  });
});

describe('quiz scene', () => {
  const scene = sceneById(timeline, 's-quiz');
  const q = /** @type {any} */ (scene.quiz);

  test('live: options are buttons; a click records the answer once and emits quizanswer', () => {
    const { ctx, events } = ctxFor(scene);
    const view = new QuizSceneView(ctx);
    document.body.appendChild(view.el);
    view.update(1, sceneStateAt(scene, 1), play);
    const buttons = /** @type {HTMLButtonElement[]} */ ([...view.el.querySelectorAll('button.ap-quiz-opt')]);
    assert.equal(buttons.length, 4);
    assert.equal(buttons[0].querySelector('.ap-quiz-letter')?.textContent, 'A');
    buttons[0].click();
    assert.deepEqual(events, [{ event: 'quizanswer', detail: { sceneIndex: scene.index, sceneId: 's-quiz', choice: 0, correct: false } }]);
    assert.ok(buttons[0].classList.contains('is-chosen'));
    assert.equal(buttons[0].getAttribute('aria-pressed'), 'true');
    buttons[2].click();
    assert.equal(events.length, 1, 'answer is locked in');
    view.update(1.1, sceneStateAt(scene, 1.1), play);
    assert.ok(buttons.every((b) => b.disabled));
    view.destroy();
  });

  test('a correct answer reports correct: true', () => {
    const { ctx, events } = ctxFor(scene);
    const view = new QuizSceneView(ctx);
    view.choose(q.correct_index);
    assert.equal(events[0].detail.correct, true);
    view.choose(-1);
    assert.equal(events.length, 1);
  });

  test('countdown ring ticks every second while playing; ding on reveal; no sounds when seeking', () => {
    const { ctx, sounds } = ctxFor(scene);
    const view = new QuizSceneView(ctx);
    const cs = q.countdown_start;
    view.update(cs - 0.1, sceneStateAt(scene, cs - 0.1), play);
    assert.deepEqual(sounds, [], 'no tick before the countdown');
    view.update(cs, sceneStateAt(scene, cs), play);
    assert.ok(view.countdown.classList.contains('is-active'));
    assert.equal(view.ringNumber.textContent, '5');
    assert.deepEqual(sounds, ['tick'], 'the first tick is at countdown_start (as in the MP4)');
    view.update(cs + 0.5, sceneStateAt(scene, cs + 0.5), play);
    assert.deepEqual(sounds, ['tick']);
    view.update(cs + 1, sceneStateAt(scene, cs + 1), play);
    assert.equal(view.ringNumber.textContent, '4');
    assert.deepEqual(sounds, ['tick', 'tick']);
    const p = Number(view.ring.style.getPropertyValue('--p'));
    assert.ok(Math.abs(p - 0.8) < 1e-3, 'live ring sweeps continuously');
    view.update(cs + 3, sceneStateAt(scene, cs + 3), seek);
    assert.deepEqual(sounds, ['tick', 'tick'], 'seeking is silent');
    view.update(q.reveal_start, sceneStateAt(scene, q.reveal_start), play);
    assert.deepEqual(sounds, ['tick', 'tick', 'ding']);
    assert.equal(view.countdown.classList.contains('is-active'), false);
    view.destroy();
  });

  test('a played-through countdown ticks exactly at countdown_start + k, k = 0..n-1 (MP4 timing)', () => {
    /** @type {string[]} */
    const heard = [];
    let now = 0;
    const { ctx } = ctxFor(scene, { playSound: (/** @type {string} */ name) => heard.push(`${name}@${now.toFixed(2)}`) });
    const view = new QuizSceneView(ctx);
    view.update(q.countdown_start - 1, sceneStateAt(scene, q.countdown_start - 1), seek); // first frame of the scene
    for (now = q.countdown_start - 1; now <= q.reveal_start + 0.5; now = Math.round((now + 0.01) * 100) / 100) {
      view.update(now, sceneStateAt(scene, now), play);
    }
    const expected = [];
    for (let k = 0; k < q.countdown_seconds; k++) expected.push(`tick@${(q.countdown_start + k).toFixed(2)}`);
    expected.push(`ding@${q.reveal_start.toFixed(2)}`);
    assert.deepEqual(heard, expected);
    view.destroy();
  });

  test('entering the scene mid-countdown (first frame is seeked) stays silent until the next second', () => {
    const { ctx, sounds } = ctxFor(scene);
    const view = new QuizSceneView(ctx);
    const t = q.countdown_start + 2.5;
    view.update(t, sceneStateAt(scene, t), { playing: true, rate: 1, seeked: true });
    assert.deepEqual(sounds, []);
    view.update(t + 0.4, sceneStateAt(scene, t + 0.4), play);
    assert.deepEqual(sounds, []);
    view.update(t + 0.5, sceneStateAt(scene, t + 0.5), play);
    assert.deepEqual(sounds, ['tick']);
    view.destroy();
  });

  test('reveal styles correct/wrong options, shows feedback and explanation; reversible on seek back', () => {
    const { ctx } = ctxFor(scene);
    const view = new QuizSceneView(ctx);
    view.update(q.reveal_start + 1, sceneStateAt(scene, q.reveal_start + 1), seek);
    const opts = [...view.el.querySelectorAll('.ap-quiz-opt')];
    assert.ok(opts[q.correct_index].classList.contains('is-correct'));
    assert.ok(opts[0].classList.contains('is-wrong'));
    assert.equal(opts[0].querySelector('.ap-quiz-why')?.textContent, 'That is V times R');
    assert.equal(opts[q.correct_index].querySelector('.ap-quiz-why'), null, 'no feedback for the correct option');
    assert.ok(view.card.classList.contains('is-revealed'));
    assert.ok(view.el.querySelector('.ap-quiz-explanation'));
    view.update(1, sceneStateAt(scene, 1), seek);
    assert.equal(view.card.classList.contains('is-revealed'), false);
    assert.equal(opts[0].classList.contains('is-wrong'), false);
  });

  test('render mode: static options, discrete ring, no sounds', () => {
    const { ctx, sounds } = ctxFor(scene, { mode: 'render' });
    const view = new QuizSceneView(ctx);
    assert.equal(view.el.querySelectorAll('button').length, 0);
    const t = q.countdown_start + 1.5;
    view.update(t, sceneStateAt(scene, t), { playing: true, rate: 1, seeked: false });
    assert.equal(view.ringNumber.textContent, '4');
    assert.equal(view.ring.style.getPropertyValue('--p'), (4 / 5).toFixed(4));
    assert.deepEqual(sounds, []);
  });

  test('question text uses rich-lite safely', () => {
    const { ctx } = ctxFor(scene);
    const view = new QuizSceneView(ctx);
    assert.equal(view.el.querySelector('.ap-quiz-q strong')?.textContent, 'I');
  });
});

describe('media scene', () => {
  const sim = sceneById(timeline, 's-sim');
  const broll = sceneById(timeline, 's-broll');

  test('video follows scene time: plays, seeks on discontinuity, freezes at the end', () => {
    const { ctx } = ctxFor(sim);
    const view = new MediaSceneView(ctx);
    const v = /** @type {HTMLVideoElement} */ (view.video);
    assert.ok(v.muted);
    assert.equal(v.loop, false, 'freeze end behaviour does not loop');
    view.update(2, sceneStateAt(sim, 2), seek);
    assert.equal(v.currentTime, 2);
    assert.ok(v.paused);
    view.update(2.1, sceneStateAt(sim, 2.1), play);
    assert.equal(v.paused, false);
    v.currentTime = 2.12; // within drift tolerance: untouched
    view.update(2.2, sceneStateAt(sim, 2.2), play);
    assert.equal(v.currentTime, 2.12);
    v.currentTime = 1.0; // drifted: corrected
    view.update(2.3, sceneStateAt(sim, 2.3), play);
    assert.ok(Math.abs(v.currentTime - 2.3) < 1e-9);
    view.update(6.5, sceneStateAt(sim, 6.5), play); // media duration 6
    assert.ok(v.paused, 'frozen on the last frame');
    assert.ok(Math.abs(v.currentTime - 5.96) < 1e-9);
    view.destroy();
  });

  test('image with Ken Burns gets a transform computed from t', () => {
    const { ctx } = ctxFor(broll);
    const view = new MediaSceneView(ctx);
    const img = /** @type {HTMLImageElement} */ (view.image);
    assert.ok(img.classList.contains('has-kenburns'));
    view.update(0, sceneStateAt(broll, 0), seek);
    const a = img.style.transform;
    view.update(broll.duration, sceneStateAt(broll, broll.duration), seek);
    assert.notEqual(img.style.transform, a);
    assert.match(img.style.transform, /scale\(1\.2/);
  });

  test('render mode: media is a hole; rect element and fit reported; nothing plays', () => {
    const { ctx } = ctxFor(broll, { mode: 'render' });
    const view = new MediaSceneView(ctx);
    assert.equal(view.mediaElement(), view.box);
    assert.equal(view.mediaFit(), 'cover');
    view.update(1, sceneStateAt(broll, 1), play);
    assert.equal(/** @type {HTMLImageElement} */ (view.image).style.transform, '');
    assert.equal(view.el.querySelector('.ap-media-title')?.textContent, broll.title);
  });

  test('hotlinked GIFs are not rendered into MP4s', () => {
    const scene = { ...broll, media: { kind: 'gif', url: 'https://media.giphy.com/x.gif', render_in_mp4: false, attribution: 'Powered by GIPHY', link_url: 'https://giphy.com/x' } };
    const live = new MediaSceneView(ctxFor(scene).ctx);
    assert.equal(live.el.querySelector('a.ap-media-attribution')?.getAttribute('rel'), 'noopener noreferrer');
    const render = new MediaSceneView(ctxFor(scene, { mode: 'render' }).ctx);
    assert.equal(render.mediaElement(), null);
    assert.equal(render.mediaFit(), null);
    assert.ok(render.el.querySelector('.ap-media-missing'));
  });

  test('media box takes the media aspect inside the media zone; the title band aligns with it', () => {
    const { ctx } = ctxFor(sim); // left layout, 16:9 default (no declared size)
    const view = new MediaSceneView(ctx);
    const zone = ctx.zones.media;
    assert.deepEqual(view.rect, fitAspect(zone, DEFAULT_MEDIA_ASPECT));
    assert.equal(view.box.style.width, `${view.rect.w}px`);
    assert.equal(view.box.style.height, `${view.rect.h}px`);
    assert.equal(view.box.style.left, `${view.rect.x - zone.x}px`);
    assert.equal(view.box.style.top, '0px', 'top-aligned under the title');
    const title = /** @type {HTMLElement} */ (view.el.querySelector('.ap-zone--title'));
    assert.equal(title.style.left, `${view.rect.x}px`);
    assert.equal(title.style.width, `${view.rect.w}px`);

    const portrait = { ...broll, media: { ...broll.media, width: 900, height: 1200 } };
    const pv = new MediaSceneView(ctxFor(portrait).ctx);
    const pz = computeZones(portrait.layout?.mascot_position, { sidePanel: false }).media;
    assert.equal(pv.rect.h, pz.h, 'height-limited');
    assert.equal(pv.rect.w, Math.round(pz.h * 0.75));
    assert.equal(pv.rect.x, pz.x + Math.round((pz.w - pv.rect.w) / 2), 'centred');
    assert.ok(placeMediaBox, 'exported for other media views');
  });

  test('a title too long for one line gets the smaller two-line style instead of being cut', () => {
    const short = new MediaSceneView(ctxFor(sim).ctx);
    assert.equal(short.el.querySelector('.ap-zone--title')?.classList.contains('is-long'), false);
    const longTitle = 'Current through a resistor as the voltage rises step by step';
    const popup = { ...sim, title: longTitle, layout: { ...sim.layout, mascot_position: 'popup_bottom_left' } };
    const view = new MediaSceneView(ctxFor(popup).ctx);
    assert.ok(view.el.querySelector('.ap-zone--title')?.classList.contains('is-long'));
    assert.equal(view.el.querySelector('.ap-media-title')?.textContent, longTitle, 'full text kept (CSS wraps it)');
    assert.equal(titleFitsOneLine('Ohm in action', 1266), true);
    assert.equal(titleFitsOneLine('x'.repeat(60), 1266), false);
    assert.equal(titleFitsOneLine('', 0), false);
  });

  test('adopts a preloaded video element', () => {
    const pre = document.createElement('video');
    const { ctx } = ctxFor(sim, { takePreloaded: (/** @type {string} */ url) => (url === sim.media?.url ? pre : null) });
    const view = new MediaSceneView(ctx);
    assert.equal(view.video, pre);
    assert.ok(pre.classList.contains('ap-media-el'));
  });
});

describe('interactive scene (sandboxed p5)', () => {
  const scene = sceneById(timeline, 's-play');

  /** @param {any} view */
  function frameOf(view) {
    const iframe = /** @type {HTMLIFrameElement} */ (view.el.querySelector('iframe'));
    /** @type {any[]} */
    const posted = [];
    const win = /** @type {any} */ (iframe.contentWindow);
    win.postMessage = (/** @type {any} */ msg, /** @type {string} */ origin) => posted.push({ msg, origin });
    return { iframe, win, posted };
  }

  /** @param {any} source @param {any} data @param {string} [origin] */
  function message(source, data, origin = 'null') {
    window.dispatchEvent(new MessageEvent('message', { data, origin, source }));
  }

  test('iframe is sandboxed with allow-scripts only and points at /sandbox/p5', () => {
    let now = 0;
    const view = new InteractiveSceneView(ctxFor(scene, { now: () => now }).ctx);
    document.body.appendChild(view.el);
    const iframe = /** @type {HTMLIFrameElement} */ (view.el.querySelector('iframe'));
    assert.equal(iframe.getAttribute('sandbox'), 'allow-scripts');
    assert.equal(iframe.getAttribute('src'), 'http://localhost/sandbox/p5');
    assert.equal(iframe.getAttribute('srcdoc'), null);
    assert.equal(iframe.getAttribute('id'), null);
    view.destroy();
  });

  test('sends the code only after a ready message from the iframe itself', () => {
    const view = new InteractiveSceneView(ctxFor(scene, { now: () => 0 }).ctx);
    document.body.appendChild(view.el);
    const { win, posted } = frameOf(view);
    message(window, { type: 'ready' }); // wrong source
    message(win, { type: 'ready' }, 'http://evil.example'); // wrong origin
    message(win, 'ready'); // malformed
    assert.equal(posted.length, 0);
    view.setPlaying(true);
    message(win, { type: 'ready' });
    assert.deepEqual(posted[0], { msg: { type: 'run', code: scene.p5_code }, origin: '*' });
    assert.equal(view.status, 'running');
    view.setPlaying(false);
    assert.deepEqual(posted[posted.length - 1].msg, { type: 'pause' });
    view.destroy();
  });

  test('update() follows the clock: a sketch that becomes ready while the lecture plays keeps running', () => {
    const view = new InteractiveSceneView(ctxFor(scene, { now: () => 0 }).ctx);
    document.body.appendChild(view.el);
    const { win, posted } = frameOf(view);
    view.update(0.2, sceneStateAt(scene, 0.2), play);
    assert.equal(view.playing, true);
    message(win, { type: 'ready' });
    assert.deepEqual(posted.map((x) => x.msg.type), ['run'], 'no pause while playing');
    view.update(0.3, sceneStateAt(scene, 0.3), seek); // paused frame
    assert.deepEqual(posted.map((x) => x.msg.type), ['run', 'pause']);
    view.update(0.4, sceneStateAt(scene, 0.4), play);
    view.update(0.5, sceneStateAt(scene, 0.5), play);
    assert.deepEqual(posted.map((x) => x.msg.type), ['run', 'pause', 'resume'], 'one message per change');
    view.destroy();
  });

  test('a paused lecture freezes the sketch as soon as it is ready', () => {
    const view = new InteractiveSceneView(ctxFor(scene, { now: () => 0 }).ctx);
    document.body.appendChild(view.el);
    const { win, posted } = frameOf(view);
    view.update(0, sceneStateAt(scene, 0), seek);
    message(win, { type: 'ready' });
    assert.deepEqual(posted.map((x) => x.msg.type), ['run', 'pause']);
    view.destroy();
  });

  test('watchdog tears the iframe down when heartbeats stop, then shows the poster', () => {
    let now = 0;
    const view = new InteractiveSceneView(ctxFor(scene, { now: () => now }).ctx);
    document.body.appendChild(view.el);
    const { win } = frameOf(view);
    message(win, { type: 'ready' });
    now = HEARTBEAT_TIMEOUT_MS - 10;
    message(win, { type: 'heartbeat' });
    now += HEARTBEAT_TIMEOUT_MS - 10;
    view.checkWatchdog();
    assert.equal(view.status, 'running');
    now += 100;
    view.checkWatchdog();
    assert.equal(view.status, 'failed');
    assert.equal(view.el.querySelector('iframe'), null);
    assert.equal(view.el.querySelector('img.ap-poster')?.getAttribute('src'), 'http://localhost/media/assets/poster/play.png');
    assert.ok(view.message.textContent?.length);
    view.destroy();
  });

  test('watchdog fails a sandbox that never becomes ready; error messages fail too', () => {
    let now = 0;
    const a = new InteractiveSceneView(ctxFor(scene, { now: () => now }).ctx);
    document.body.appendChild(a.el);
    now = LOAD_TIMEOUT_MS + 1;
    a.checkWatchdog();
    assert.equal(a.status, 'failed');
    a.destroy();
    const b = new InteractiveSceneView(ctxFor(scene, { now: () => 0 }).ctx);
    document.body.appendChild(b.el);
    const { win } = frameOf(b);
    message(win, { type: 'ready' });
    message(win, { type: 'error', message: '<img src=x onerror=alert(1)>' });
    assert.equal(b.status, 'failed');
    assert.equal(b.el.querySelector('img[src="x"]'), null);
    b.destroy();
  });

  test('render mode never creates the iframe and draws the poster into the frame', async () => {
    const view = new InteractiveSceneView(ctxFor(scene, { mode: 'render' }).ctx);
    assert.equal(view.el.querySelector('iframe'), null);
    assert.ok(view.el.querySelector('img.ap-poster'));
    assert.equal(view.mediaElement(), null);
    await view.ready();
    view.destroy();
  });

  test('without a poster the fallback is a title card', () => {
    const view = new InteractiveSceneView(ctxFor({ ...scene, poster: null }, { mode: 'render' }).ctx);
    assert.equal(view.el.querySelector('.ap-media-missing')?.textContent, scene.title);
  });
});
