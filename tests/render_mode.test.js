// Phase 22: render mode (render_mode.js) for the server's rendered export, and its wiring in index.html. The pure parts run
// here: the configuration the worker sets, the frame grid, frame sets (which image shows at which time, 24 fps at 30 fps),
// the virtual media clock, the sound log and the quiz sounds' synthesis (from the page's own sound code), the range a page
// renders (pre-roll, capture start, done) and its timeline (clipped to the capture start). The page's side is checked in its
// source and, for its small helpers, by running them: the missing-visual rule, the recording's "no visual" state, the glass
// blur kept in render, the black-bar mask (a clip-path, never a scale), the facade and its hooks.
// Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const R = require('../render_mode.js');

const page = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const between = (text, start, end) => {
    const a = text.indexOf(start);
    assert.ok(a >= 0, `found: ${start}`);
    const b = text.indexOf(end, a + start.length);
    assert.ok(b > a, `found after it: ${end}`);
    return text.slice(a, b);
};
const plain = value => JSON.parse(JSON.stringify(value));

// ---- configuration ----------------------------------------------------------------------------------------------------

test('render mode is on only when __AADHI_RENDER__ is an object; its values are checked', () => {
    for (const off of [undefined, null, 0, 'yes', true, [], [1]]) assert.equal(R.readConfig(off), null);
    const d = R.readConfig({});
    assert.deepEqual(plain(d), { fps: 30, seed: 1, fromScene: 0, captureFromScene: null, captureScene: 0, captureAtStart: true, toScene: null,
        preroll: false, missingVisuals: 'refuse', frameBase: '/__render_frames/', projectId: null, tts: null });
    const c = R.readConfig({ fps: 25, seed: 42, fromScene: 3, toScene: 5, missingVisuals: 'omit', frameBase: '/frames/x/', projectId: '17',
        tts: { engine: 'gemini-2.5-flash', voice: 'en-US-GuyNeural', geminiVoice: 'Puck', rate: 0.9 } });
    assert.equal(c.fps, 25);
    assert.equal(c.seed, 42);
    assert.equal(c.fromScene, 3);
    assert.equal(c.toScene, 5);
    assert.equal(c.preroll, true, 'a later range pre-rolls the scene before it by default');
    assert.equal(c.missingVisuals, 'omit');
    assert.equal(c.frameBase, '/frames/x/');
    assert.equal(c.projectId, 17);
    assert.deepEqual(plain(c.tts), { engine: 'gemini-2.5-flash', voice: 'en-US-GuyNeural', geminiVoice: 'Puck', rate: 0.9 });
    assert.equal(R.readConfig({ fromScene: 3, preroll: false }).preroll, false);
    assert.equal(R.readConfig({ fromScene: 0, preroll: true }).preroll, false, 'nothing to pre-roll before the first scene');
    // anything unusable falls back to the default
    const bad = R.readConfig({ fps: 0, seed: 1.5, fromScene: -1, toScene: 'x', missingVisuals: 'record', frameBase: '/../etc/',
        projectId: -4, tts: { engine: '<script>', voice: 'a'.repeat(200), rate: 3 } });
    assert.equal(bad.fps, 30);
    assert.equal(bad.seed, 1);
    assert.equal(bad.fromScene, 0);
    assert.equal(bad.toScene, null);
    assert.equal(bad.missingVisuals, 'refuse');
    assert.equal(bad.frameBase, '/__render_frames/');
    assert.equal(bad.projectId, null);
    assert.deepEqual(plain(bad.tts), { engine: null, voice: null, geminiVoice: null, rate: null });
});

// ---- frame grid and frame sets -----------------------------------------------------------------------------------------

test('the frame grid: frame n at round(n x 1000 / fps) ms; the next frame strictly after a time; the first at or after', () => {
    assert.deepEqual([0, 1, 2, 3, 29, 30].map(n => R.frameTimeMs(n, 30)), [0, 33, 67, 100, 967, 1000]);
    assert.equal(R.frameTimeMs(1, 24), 42);
    assert.equal(R.nextFrameTime(0, 0, 30), 33);
    assert.equal(R.nextFrameTime(33, 0, 30), 67);
    assert.equal(R.nextFrameTime(50, 0, 30), 67);
    assert.equal(R.nextFrameTime(1000, 0, 30), 1033);
    assert.equal(R.nextFrameTime(5050, 5000, 30), 5067, 'on the grid that starts at the render start');
    assert.equal(R.frameAtOrAfter(0, 30), 0);
    assert.equal(R.frameAtOrAfter(33, 30), 1);
    assert.equal(R.frameAtOrAfter(34, 30), 2);
    assert.equal(R.frameAtOrAfter(3666.7, 30), 110);
    // every grid time maps back to its own frame
    for (let n = 0; n < 2000; n += 7) assert.equal(R.frameAtOrAfter(R.frameTimeMs(n, 30), 30), n);
});

test('a frame set: the nearest frame by time (round(t x fps)), looping sets wrap, others hold their last frame', () => {
    assert.equal(R.normalizeManifest(null), null);
    assert.equal(R.normalizeManifest({ fps: 24, frames: 0, pattern: '%06d.jpg' }), null);
    assert.equal(R.normalizeManifest({ fps: 24, frames: 10, pattern: '../%06d.jpg' }), null);
    assert.equal(R.normalizeManifest({ fps: 24, frames: 10, pattern: '%06d.gif' }), null);
    const m = R.normalizeManifest({ version: 1, fps: 24, frames: 192, width: 1280, height: 720, format: 'jpeg', alpha: false, loop: true, pattern: '%06d.jpg' });
    assert.equal(m.loop, true);
    assert.equal(R.frameIndex(0, m), 0);
    assert.equal(R.frameIndex(1 / 24, m), 1);
    assert.equal(R.frameIndex(1.0416666, m), 25, 'rounded, not floored');
    assert.equal(R.frameIndex(7.99, m), 0, 'round(191.76) = 192 wraps to the first frame');
    assert.equal(R.frameIndex(8.5, m), 12);
    assert.equal(R.frameIndex(-1, m), 168);
    assert.equal(R.frameIndex(NaN, m), 0);
    const once = R.normalizeManifest({ fps: 25, frames: 100, pattern: 'f%04d.png', loop: false, alpha: true });
    assert.equal(once.alpha, true);
    assert.equal(R.frameIndex(10, once), 99, 'held on its last frame');
    assert.equal(R.frameIndex(-2, once), 0);
    assert.equal(R.frameFileName(0, m), '000001.jpg', '1-based, zero-padded to the pattern width');
    assert.equal(R.frameFileName(191, m), '000192.jpg');
    assert.equal(R.frameFileName(9, once), 'f0010.png');
    assert.equal(R.frameUrl({ base: '/__render_frames/abc/', manifest: m }, 0.5), '/__render_frames/abc/000013.jpg');
});

test('a 24 fps clip at 30 fps: one clip frame in four is shown twice (one repeat per five output frames), nothing skipped', () => {
    const m = R.normalizeManifest({ fps: 24, frames: 192, pattern: '%06d.jpg' });
    const shown = Array.from({ length: 30 }, (_, n) => R.frameIndex(n / 30, m));
    assert.deepEqual(shown.slice(0, 10), [0, 1, 2, 2, 3, 4, 5, 6, 6, 7]);
    for (let i = 1; i < shown.length; i++) assert.ok(shown[i] - shown[i - 1] <= 1, 'never skips a clip frame');
    assert.equal(new Set(shown).size, 24, 'all 24 frames of the second shown');
    const m25 = R.normalizeManifest({ fps: 25, frames: 100, pattern: '%06d.jpg' });
    const shown25 = Array.from({ length: 30 }, (_, n) => R.frameIndex(n / 30, m25));
    assert.equal(new Set(shown25).size, 25, '25 fps: one repeat per six output frames');
});

test('media addresses key the frame map without query or fragment', () => {
    assert.equal(R.normalizeSrc('video_template/aadhi_left.mp4?t=123', 'http://127.0.0.1:9700/'), 'http://127.0.0.1:9700/video_template/aadhi_left.mp4');
    assert.equal(R.normalizeSrc('http://127.0.0.1:9700/static/a.mp4?token=x#y'), 'http://127.0.0.1:9700/static/a.mp4');
    assert.equal(R.normalizeSrc(''), '');
    assert.equal(R.normalizeSrc(null), '');
});

// ---- determinism -------------------------------------------------------------------------------------------------------

test('Math.random is seeded: the same sequence every render, a different one for another seed; the GIF search is pinned', () => {
    const a = R.seededRandom(7), b = R.seededRandom(7), c = R.seededRandom(8);
    const seqA = Array.from({ length: 50 }, a), seqB = Array.from({ length: 50 }, b), seqC = Array.from({ length: 50 }, c);
    assert.deepEqual(seqA, seqB);
    assert.notDeepEqual(seqA, seqC);
    assert.ok(seqA.every(v => v >= 0 && v < 1));
    assert.ok(new Set(seqA).size === 50);
    assert.equal(R.pinGifRequest('/get-gif?query=spinning%20engine&randomize=true'), '/get-gif?query=spinning%20engine&randomize=false');
    assert.equal(R.pinGifRequest('/get-gif?randomize=true&query=x'), '/get-gif?randomize=false&query=x');
    assert.equal(R.pinGifRequest('/api/projects/1?randomize=true'), '/api/projects/1?randomize=true', 'only the GIF search');
    assert.equal(R.pinGifRequest(42), 42);
});

// ---- virtual media -----------------------------------------------------------------------------------------------------

test('a virtual element\'s media time follows the virtual clock at its rate; it wraps when looping and holds at the end', () => {
    const playing = { playing: true, anchorAt: 1000, base: 2, rate: 0.9 };
    assert.equal(R.mediaTime(playing, 1000, 10, false), 2);
    assert.equal(R.mediaTime(playing, 2000, 10, false), 2.9);
    assert.equal(R.mediaTime(playing, 20000, 10, false), 10, 'held at the end');
    assert.ok(Math.abs(R.mediaTime({ playing: true, anchorAt: 0, base: 0, rate: 1 }, 8500, 8, true) - 0.5) < 1e-9, 'wraps');
    assert.equal(R.mediaTime({ playing: false, anchorAt: null, base: 3.25, rate: 1 }, 99999, 8, true), 3.25, 'paused: still');
    assert.equal(R.mediaTime({ playing: true, anchorAt: null, base: 1, rate: 1 }, 5000, 8, false), 1, 'not anchored yet (no metadata)');
    assert.equal(R.mediaTime({ playing: true, anchorAt: 0, base: 0, rate: 1 }, 5000, NaN, false), 5, 'unknown length: runs on');
    // the end, in virtual ms: narration of 6.73 s at 0.9 from t = 3033 ends at 3033 + 7477.8
    assert.ok(Math.abs(R.mediaEndAt({ playing: true, anchorAt: 3033, base: 0, rate: 0.9 }, 6.73) - (3033 + 6730 / 0.9)) < 1e-6);
    assert.equal(R.mediaEndAt({ playing: false, anchorAt: null, base: 0, rate: 1 }, 5), null);
    assert.equal(R.mediaEndAt({ playing: true, anchorAt: 0, base: 0, rate: 1 }, NaN), null);
});

test('what a sound is for the mix, and the log of what was heard (one entry per unchanged stretch)', () => {
    assert.equal(R.soundKind({ id: 'intro-logo-video', tag: 'VIDEO', src: '/video_template/logo_animation.mp4' }), 'logo');
    assert.equal(R.soundKind({ id: '', tag: 'AUDIO', src: 'http://x/video_template/bgm.mp3' }), 'music');
    assert.equal(R.soundKind({ id: 'active-ai-video', tag: 'VIDEO', src: '/static/clip.mp4' }), 'video-voice');
    assert.equal(R.soundKind({ id: '', tag: 'AUDIO', src: '/static/audio_ab.mp3?t=1' }), 'narration');
    const log = new R.SoundLog();
    const e = log.open({ at: 100, kind: 'narration', src: 'a.mp3', offset: 0, rate: 0.9, volume: 1, loop: false, clipDuration: 6.73 });
    assert.equal(e.endAt, null);
    log.close(e, 7578);
    log.close(e, 9000);
    assert.equal(e.endAt, 7578, 'closed once');
    const bad = log.open({ at: 10, kind: 'music', src: 'b.mp3', offset: 0, rate: 1, volume: 0.01, loop: true, clipDuration: NaN });
    assert.equal(bad.clipDuration, null);
    log.close(bad, 5);
    assert.equal(bad.endAt, 10, 'never before it started');
});

// The page's own quiz sounds (index.html), run against the recording AudioContext
function pageSounds(now, onSound) {
    const source = between(page, '        // --- Experiential Sound Engine ---', '        // --- Quiz Checkpoint Scene');
    const FakeAudioContext = R.createFakeAudioContext(now, onSound);
    const context = { window: { AudioContext: FakeAudioContext }, document: { addEventListener() {} } };
    vm.createContext(context);
    vm.runInContext(source + '\nthis.api = { playWhoosh, playDing, playTick, audioCtx };', context);
    return context.api;
}

test('the quiz sounds are recorded with their synthesis (never played), at their virtual time; no key press is needed', async () => {
    let clock = 5000;
    const heard = [];
    const sounds = pageSounds(() => clock, s => heard.push(s));
    assert.equal(sounds.audioCtx.state, 'running', 'the page\'s keydown unlock is not needed in a render');
    clock = 7500;
    sounds.playDing();
    clock = 9000;
    sounds.playTick();
    sounds.playWhoosh();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(heard.length, 3);
    const [ding, tick, whoosh] = heard;
    assert.equal(ding.at, 7500);
    assert.deepEqual(plain(ding.synth), { type: 'sine', freq: 1200, gain: 0.3, attack: 0.05, floor: 0.0333, duration: 1 });
    assert.equal(tick.at, 9000);
    assert.deepEqual(plain(tick.synth), { type: 'sine', freq: 880, gain: 0.15, attack: 0.02, floor: 0.0067, duration: 0.15 });
    assert.deepEqual(plain(whoosh.synth), { type: 'sine', freq: 800, gain: 0.5, attack: 0.1, floor: 0.02, duration: 0.4, freqEnd: 100, sweep: 0.3,
        filter: { type: 'lowpass', freq: 2000, freqEnd: 200 } });
});

// ---- preparation progress ----------------------------------------------------------------------------------------------

test('preparation progress is plain data: phase, whole counts (done never above a known total), a plain label, a change count', () => {
    const p = R.createProgress();
    assert.deepEqual(plain(p), { phase: 'idle', done: 0, total: 0, label: '', changes: 0 });
    assert.equal(R.setProgress(p, 'preparing', 0, 0, 'Opening the lesson'), p, 'updated in place (the worker reads the same object)');
    assert.deepEqual(plain(p), { phase: 'preparing', done: 0, total: 0, label: 'Opening the lesson', changes: 1 });
    R.setProgress(p, 'preparing', 12, 140, 'Narration');
    assert.deepEqual([p.phase, p.done, p.total, p.label, p.changes], ['preparing', 12, 140, 'Narration', 2]);
    R.setProgress(p, 'preparing', 12, 140, 'Narration');
    assert.equal(p.changes, 3, 'every update counts (a slow step is not a stuck one)');
    R.setProgress(p, 'preparing', 150.7, 140, '  Images\n ');
    assert.deepEqual([p.done, p.total, p.label], [140, 140, 'Images']);
    R.setProgress(p, 'preparing', -3, NaN, 42);
    assert.deepEqual([p.done, p.total, p.label], [0, 0, '']);
    R.setProgress(p, 'rendering?', 1, 2, 'x'.repeat(400));
    assert.equal(p.phase, 'preparing', 'an unknown phase keeps the current one');
    assert.equal(p.label.length, 160);
    R.setProgress(p, 'failed', 3, 9, 'This lesson has no scenes yet.');
    assert.deepEqual([p.phase, p.done, p.total], ['failed', 3, 9]);
    assert.doesNotThrow(() => JSON.stringify(p));
});

test('the page reports its preparation: the export\'s own progress steps while prepare() runs, then ready or failed', () => {
    const facade = between(page, '        // --- Phase 22: the server\'s rendered export (render_mode.js, render_worker.mjs) ---', '        // --- Publishing Companion');
    assert.match(facade, /progress: window\.AadhiRenderMode\.createProgress\(\),/);
    assert.match(facade, /const step = \(done, total, label\) => window\.AadhiRenderMode\.setProgress\(progress, 'preparing', done, total, label\);/);
    assert.match(facade, /const report = await prepareLessonForExport\(step\);/, 'prepareLessonForExport\'s own callback (done, total, label)');
    assert.match(facade, /window\.AadhiRenderMode\.setProgress\(progress, 'failed', progress\.done, progress\.total,/);
    assert.match(facade, /window\.AadhiRenderMode\.setProgress\(progress, 'ready', progress\.total, progress\.total, 'Ready to render'\);/);
    assert.match(facade, /if \(preparing\) return preparing;/, 'one preparation, however often it is asked for');
    // the export reports every task as it finishes
    const prepare = between(page, 'async function prepareLessonForExport(', 'return { warnings, quality, missing };');
    assert.match(prepare, /report\(0, tasks\.length, 'Loading lesson assets'\);\s*await runLimited\(tasks, 3, async task => \{\s*await task\.run\(\);\s*report\(\+\+done, tasks\.length, task\.label\);/);
});

// ---- the readiness gate ------------------------------------------------------------------------------------------------

test('the gate waits for every piece of real work (the clock never moves meanwhile); one unfinished past its limit is a stall', () => {
    let clock = 0;
    const work = new R.PendingWork(() => clock, { network: 1000, images: 500 });
    assert.deepEqual(work.busy(), []);
    assert.equal(work.stalled(), null);
    const a = work.start('network', '/api/projects/1');
    const b = work.start('network', '/generate-audio');
    assert.deepEqual(work.busy(), ['network']);
    work.end(a);
    assert.deepEqual(work.busy(), ['network'], 'one request still on its way: still waited for');
    clock = 999;
    assert.equal(work.stalled(), null);
    clock = 1001;
    assert.deepEqual(work.busy(), ['network'], 'never given up on quietly (that would differ from run to run)');
    assert.deepEqual(plain(work.stalled()), { kind: 'network', what: '/generate-audio', since: 0, tag: null }, 'named, for the plain message');
    work.end(b);
    assert.equal(work.stalled(), null);
    // observed work (an image loading): a new address is a new piece; one that is gone stops counting
    const img = {};
    work.observe('images', [[img, 'a.png', 'a.png']]);
    assert.deepEqual(work.busy(), ['images']);
    clock = 1400;
    work.observe('images', [[img, 'a.png', 'a.png']]);
    clock = 1600;
    assert.equal(work.stalled().what, 'a.png', 'the same piece keeps its start');
    work.observe('images', [[img, 'b.png', 'b.png']]);
    assert.equal(work.stalled(), null, 'a new address starts again');
    work.observe('images', []);
    assert.deepEqual(work.busy(), [], 'loaded (or removed)');
    const c = work.start('network');
    work.observe('network', []);
    assert.deepEqual(work.busy(), ['network'], 'observing never ends a started request');
    work.end(c);
});

test('finished real work is told to the page in a fixed order (by kind, then by when it was asked for), however it finished', () => {
    const heard = [];
    const q = new R.DeliveryQueue();
    // finished in a machine-dependent order: an image, two responses (the later request first), a clip's loading
    q.add(2, () => 0, () => heard.push('image'));
    q.add(1, () => 7, () => heard.push('response 7'));
    q.add(1, () => 3, () => heard.push('response 3'));
    q.add(0, () => 12, () => heard.push('clip 12'));
    q.add(1, () => 5, () => { heard.push('response 5'); throw new Error('a listener failing'); });
    assert.equal(q.size, 5);
    assert.equal(q.drain(), 5);
    assert.deepEqual(heard, ['clip 12', 'response 3', 'response 5', 'response 7', 'image']);
    assert.equal(q.size, 0);
    assert.equal(q.drain(), 0);
});

// ---- range and timeline ------------------------------------------------------------------------------------------------

test('range 0 captures from frame 0 and ends at the first frame at or after the lesson\'s end', () => {
    const r = new R.RangeTracker(R.readConfig({ fromScene: 0, toScene: null }));
    assert.deepEqual(r.frame(0), { scene: null, captureFrom: 0, done: false });
    r.sceneBegan(0, 14500, 'Intro scene', 'title');
    r.sceneBegan(1, 30000, 'Next', 'content');
    assert.equal(r.frame(30033).scene, 1);
    r.lessonEnded(61010);
    assert.equal(r.frame(61000).done, false);
    assert.equal(r.frame(61033).done, true);
    assert.equal(r.endReason, 'lesson ended');
});

test('a later range: the scene before it plays uncaptured; capture starts at the first frame at or after its first scene; it ends where the next range begins', () => {
    const r = new R.RangeTracker(R.readConfig({ fromScene: 2, toScene: 3, preroll: true }));
    assert.equal(r.frame(0).captureFrom, null);
    r.sceneBegan(1, 0, 'Before', 'content');
    assert.equal(r.frame(1000).captureFrom, null, 'the pre-roll scene is not captured');
    r.sceneBegan(2, 3012.5, 'First', 'content');
    assert.equal(r.frame(3000).captureFrom, null);
    assert.equal(r.frame(3033).captureFrom, 3033);
    assert.equal(r.frame(5000).captureFrom, 3033, 'stays');
    r.sceneBegan(3, 9000, 'Second', 'content');
    assert.equal(r.frame(9000).done, false, 'toScene itself is in the range');
    r.sceneBegan(4, 15010, 'Next range', 'content');
    assert.equal(r.frame(15000).done, false);
    assert.equal(r.frame(15033).done, true, 'the next range\'s first frame is not captured here');
    assert.equal(r.endReason, 'next scene');
    // without a pre-roll, a later range captures from frame 0
    const direct = new R.RangeTracker(R.readConfig({ fromScene: 2, preroll: false }));
    assert.equal(direct.frame(0).captureFrom, 0);
});

test('whole-lesson pre-roll: every page plays from scene 0 (the intro too) and captures from the frame its first scene begins', () => {
    const c = R.readConfig({ fromScene: 0, captureFromScene: 3, toScene: 5 });
    assert.deepEqual([c.fromScene, c.captureFromScene, c.captureScene, c.captureAtStart, c.preroll], [0, 3, 3, false, false]);
    const r = new R.RangeTracker(c);
    assert.equal(r.frame(0).captureFrom, null, 'the intro and scenes 0-2 play uncaptured');
    r.sceneBegan(0, 14500, 'A', 'title');
    r.sceneBegan(2, 30000, 'C', 'content');
    assert.equal(r.frame(30033).captureFrom, null);
    r.sceneBegan(3, 41010, 'D', 'content');
    assert.equal(r.frame(41000).captureFrom, null);
    assert.equal(r.frame(41033).captureFrom, 41033, 'the first frame at or after scene 3 began: the same frame the page before it stopped at');
    r.sceneBegan(6, 70000, 'G', 'content');
    assert.equal(r.frame(70000).done, true, 'the scene after toScene: the first frame of the next page');
    // the first range: from frame 0, intro included
    const first = R.readConfig({ fromScene: 0, captureFromScene: 0, toScene: 2 });
    assert.deepEqual([first.captureAtStart, new R.RangeTracker(first).frame(0).captureFrom], [true, 0]);
    // a hidden first scene of the range: the next scene that plays starts it
    const hidden = new R.RangeTracker(R.readConfig({ fromScene: 0, captureFromScene: 4 }));
    hidden.sceneBegan(5, 50000, 'F', 'content');
    assert.equal(hidden.frame(50000).captureFrom, 50000);
    // the page plays from fromScene as given (no one-scene pre-roll) and lists the intro's logo only when it plays it
    const facade = between(page, '                start() {', '                // Applies the frame at virtual time tMs');
    assert.match(facade, /if \(!from\) \{\s*startPresentationSequence\(true\);\s*\} else if \(config\.captureFromScene !== null\) \{/);
    assert.match(page, /media: renderMediaList\(config\.fromScene\)/);
});

test('the worker may also ask for the whole-lesson pre-roll as preroll "lesson" (the first scene of the range in fromScene)', () => {
    const c = R.readConfig({ fromScene: 4, toScene: 6, preroll: 'lesson', prerollFrom: 0 });
    assert.deepEqual([c.fromScene, c.captureFromScene, c.captureScene, c.captureAtStart, c.preroll, c.toScene], [0, 4, 4, false, false, 6]);
    assert.deepEqual(plain(R.readConfig({ fromScene: 0, preroll: false })).captureAtStart, true, 'range 0 as before');
    assert.equal(R.readConfig({ fromScene: 4, preroll: true }).preroll, true, 'the one-scene pre-roll stays');
});

test('the timeline counts from the first captured frame: sounds crossing it are clipped, earlier events dropped', () => {
    const tl = R.buildTimeline({
        fps: 30, captureFrom: 3033, rangeStartAt: 3012.5,
        scenes: [{ at: 0, index: 1, title: 'Before', type: 'content' }, { at: 3012.5, index: 2, title: 'First', type: 'content' },
            { at: 9000, index: 3, title: 'Second', type: 'title' }],
        cues: [{ start: 1, end: 2.9, text: 'pre-roll line' }, { start: 2.9, end: 4, text: 'crossing' }, { start: 4, end: null, text: 'open' }],
        sounds: [
            { at: 0, endAt: 2900, kind: 'narration', src: 'a.mp3', offset: 0, rate: 0.9, volume: 1, loop: false, clipDuration: 2.61 },
            { at: 1033, endAt: 5033, kind: 'video-voice', src: 'clip.mp4', offset: 1, rate: 1, volume: 1, loop: false, clipDuration: 9 },
            { at: 0, endAt: null, kind: 'music', src: 'bgm.mp3', offset: 0, rate: 1, volume: 0.01, loop: true, clipDuration: 2 },
            { at: 3100, endAt: null, kind: 'narration', src: 'b.mp3', offset: 0, rate: 0.9, volume: 1, loop: false, clipDuration: 4 }],
        sfx: [{ at: 2000, synth: { duration: 0.15 } }, { at: 4033, synth: { type: 'sine', freq: 1200, gain: 0.3, attack: 0.05, floor: 0.03, duration: 1 } }],
        notes: [{ at: 100, scene: 1, text: 'old' }, { at: 3013, scene: 2, text: 'Scene 3 is recorded without its visual: no AI video has been made for it yet.' }],
        sync: [{ at: 2000, scene: 1, kind: 'cascade_reveal', target: { item: 0 } }, { at: 3666.7 + 3033, scene: 2, kind: 'cascade_reveal', target: { item: 1 }, extra: { due: 3.3 } }]
    });
    assert.deepEqual(tl.scenes.map(s => [s.t, s.title, s.index]), [[0, 'First', 2], [5.967, 'Second', 3]]);
    assert.deepEqual(tl.cues, [{ start: 0, end: 0.967, text: 'crossing' }, { start: 0.967, end: null, text: 'open' }]);
    const voice = tl.audio.find(a => a.kind === 'video-voice');
    assert.deepEqual([voice.t, voice.offset, voice.end, voice.duration], [0, 3, 2, 2], 'started 2 s before: offset +2 s, 2 s left');
    const music = tl.audio.find(a => a.kind === 'music');
    assert.deepEqual([music.t, music.offset, music.loop, music.end, music.duration], [0, 1.033, true, null, null], 'a loop wraps its offset');
    assert.ok(!tl.audio.some(a => a.src === 'a.mp3'), 'ended before the capture: dropped');
    const b = tl.audio.find(a => a.src === 'b.mp3');
    assert.equal(b.t, 0.067);
    const sfx = tl.audio.filter(a => a.kind === 'sfx');
    assert.equal(sfx.length, 1);
    assert.deepEqual([sfx[0].t, sfx[0].end, sfx[0].synth.freq], [1, 2, 1200]);
    assert.deepEqual(tl.audio.map(a => a.t), tl.audio.map(a => a.t).slice().sort((x, y) => x - y), 'in time order');
    assert.deepEqual(tl.notes, [{ t: 0, scene: 2, text: 'Scene 3 is recorded without its visual: no AI video has been made for it yet.' }]);
    assert.equal(tl.sync.length, 1);
    assert.deepEqual([tl.sync[0].frame, tl.sync[0].t, tl.sync[0].due], [110, 3.667, 3.3], 'the frame it shows on, counted from the capture');
});

test('outside a browser nothing is installed', () => {
    assert.equal(R.activate(undefined), null);
    assert.equal(R.activate({}), null);
    assert.equal(R.activate({ document: {}, __AADHI_RENDER__: null }), null);
    assert.equal(R.runtime, null);
});

// ---- the page ----------------------------------------------------------------------------------------------------------

test('render_mode.js is the page\'s first script (before the page binds fetch or creates its AudioContext)', () => {
    const head = page.slice(0, page.indexOf('</head>'));
    const first = head.indexOf('<script');
    assert.equal(head.indexOf('<script src="render_mode.js"></script>'), first);
    assert.ok(page.indexOf('render_mode.js') < page.indexOf('const originalFetch = window.fetch.bind(window);'));
    assert.ok(page.indexOf('render_mode.js') < page.indexOf('const audioCtx = new AudioContext();'));
});

test('the glass blur stays on in render mode; the tab capture still turns it off', () => {
    const css = page.slice(0, page.indexOf('</style>', page.indexOf('/* --- Concept Map (Skill Tree) Panel --- */')));
    assert.match(css, /body\[data-recording\]:not\(\[data-render\]\) \*,\s*body\[data-recording\]:not\(\[data-render\]\) \*::before,\s*body\[data-recording\]:not\(\[data-render\]\) \*::after \{\s*backdrop-filter: none !important;/);
    assert.doesNotMatch(css, /body\[data-recording\] \*,/, 'no rule left that removes it for every recording');
});

test('the black bars of Aadhi\'s left clip are masked with a clip-path in whole pixels, never scaled; the cue positions are untouched', () => {
    const mask = between(page, '        /* Phase 22: aadhi_left.mp4 and its poster carry black pillars', '        /* Mascot fallback (mascot.js)');
    assert.match(mask, /\.mascot-layer\.layer-left,\s*\.mascot-fallback-frame\[data-for\*="aadhi_left"\] \{\s*clip-path: inset\(var\(--mascot-top\) var\(--mascot-bar\) 0 var\(--mascot-bar\)\);\s*\}/);
    assert.match(mask, /--mascot-bar: round\(up, max\(0px, calc\(var\(--mascot-frame-x\) \+ var\(--mascot-frame-w\) \* 52 \/ 1280\)\), 1px\);/);
    assert.match(mask, /--mascot-top: round\(up, max\(0px, calc\(var\(--mascot-frame-y\) \+ var\(--mascot-frame-h\) \* 3 \/ 720\)\), 1px\);/);
    // the strips behind are the clip's own studio still (never black), behind the layers
    assert.match(mask, /#mascot-bg::before,\s*#mascot-bg::after \{[^}]*z-index: -1;[^}]*background: url\('video_template\/posters\/aadhi_left\.jpg'\) no-repeat;/);
    // a mask, not a rescale: no transform or size change on the clip (object-fit: cover as before)
    const layerRules = page.match(/\.mascot-layer[^{]*\{[^}]*\}/g).join('\n');
    assert.doesNotMatch(layerRules, /transform:\s*scale|object-view-box|width:\s*(?!100vw)1[0-9]{2}(\.\d+)?vw/);
    assert.match(page, /\.mascot-layer \{\s*position: absolute;\s*top: 0;\s*left: 0;\s*width: 100vw;\s*height: 100vh;\s*object-fit: cover;/);
    const mascot = fs.readFileSync(path.join(__dirname, '..', 'mascot.js'), 'utf8');
    assert.match(mascot, /left: \{ cue: \[0\.11, 0\.10\], pivot: \[0\.21, 0\.92\] \}/, 'the hand-measured cue stays');
});

// The page's missing-visual rule and the recording's "no visual" state, run as the page runs them
function recordingHelpers({ recording = true, exporting = false, render = null } = {}) {
    const source = between(page, '        // Phase 22: why a scene\'s main visual is missing now', '        // the stage may show technical details');
    const timers = [];
    const container = { innerHTML: '<div id="jxgbox">card</div>' };
    const context = {
        window: { slideSyncState: null, currentSlideRenderId: 7, checkAndAdvanceSlide() { context.window.advanced = (context.window.advanced || 0) + 1; },
            renderMode: render, exportStartTime: exporting ? 1000 : null },
        document: { getElementById: id => (id === 'slide-visual-container' ? container : null), body: { hasAttribute: () => recording } },
        isAutoExporting: exporting, setTimeout: (fn, ms) => timers.push({ fn, ms }), Set,
        captionClock: () => 12.3456
    };
    vm.createContext(context);
    vm.runInContext(source + '\nthis.api = { missingSceneVisual, omitVisualWhileRecording, noteRecording };', context);
    return { ...context.api, context, container, timers };
}

test('which scenes would show no visual: the same rule as renderSlide\'s notice branches', () => {
    const { missingSceneVisual: miss } = recordingHelpers();
    assert.equal(miss({ type: 'content' }, 'images'), null);
    assert.equal(miss({ type: 'ai_video', prompt: 'x', video_url: '/static/a.mp4' }, 'images'), null);
    assert.equal(miss({ type: 'ai_video', prompt: 'x', visual_plan: { main: { url: '/static/b.mp4' } } }, 'images'), null);
    assert.equal(miss({ type: 'ai_video', prompt: 'x', video_url: '/static/a.mp4', visual_plan: { main: { selection: 'removed' } } }, 'images'), null,
        'removed in Visual Review: no visual by choice');
    assert.equal(miss({ type: 'ai_video', prompt: 'x' }, 'images'), 'no AI video has been made for it yet');
    assert.equal(miss({ type: 'ai_video', prompt: 'x', video_url: '/static/a.mp4', visual_plan: { main: { source: 'AI_VIDEO' } } }, 'images'),
        'no AI video has been made for it yet', 'a plan without a url wins over the old video_url (as the stage does)');
    assert.equal(miss({ type: 'ai_video', prompt: 'x', visual_plan: { main: { error: true, reason: 'gone' } } }, 'all'), 'its video is no longer available');
    assert.equal(miss({ type: 'ai_video' }, 'all'), 'it doesn\'t say what its video should show');
    assert.equal(miss({ type: 'ai_video', prompt: 'x' }, 'off'), 'AI visuals are off and nothing in your library matches it');
    assert.equal(miss({ type: 'ai_video', prompt: 'x', visual_plan: { main: { would_require: true } } }, 'all'), 'AI visuals are off and nothing in your library matches it');
    assert.equal(miss({ type: 'simulation', manim_code: 'x', manim_video_url: '/static/m.mp4' }, 'images'), null);
    assert.equal(miss({ type: 'simulation', manim_code: 'x' }, 'images'), 'its animation could not be rendered');
    assert.equal(miss({ type: 'visual', svg: '<svg/>' }, 'images'), null);
    assert.equal(miss({ type: 'visual' }, 'images'), 'it has no animation yet');
    assert.equal(miss({ type: 'simulation', manim_code: 'x', visual_plan: { main: { error: true } } }, 'images'), 'its animation is no longer available');
    assert.equal(miss(null, 'images'), null);
});

test('while recording, a missing visual is drawn as no visual, noted once, and the scene still moves on', () => {
    const notes = [];
    const h = recordingHelpers({ render: { note: (i, t) => notes.push([i, t]) } });
    // a narrated simulation: its clip counts as played, the narration moves it on
    h.context.window.slideSyncState = { hasVideo: true, hasAudio: true, videoFinished: false };
    h.omitVisualWhileRecording(2, 7, 'its animation could not be rendered');
    assert.equal(h.container.innerHTML, '', 'Visual Review\'s removed state: nothing in the visual box');
    assert.equal(h.context.window.slideSyncState.videoFinished, true);
    assert.deepEqual(notes, [[2, 'Scene 3 is recorded without its visual: its animation could not be rendered.']]);
    h.omitVisualWhileRecording(2, 7, 'its animation could not be rendered');
    assert.equal(notes.length, 1, 'once per scene and text');
    // a scene whose only voice was its clip holds 5 s, then moves on (unless another scene was drawn meanwhile)
    h.context.window.slideSyncState = { hasVideo: true, hasAudio: false, videoFinished: false };
    h.omitVisualWhileRecording(3, 7, 'no AI video has been made for it yet');
    assert.equal(h.timers.length, 1);
    assert.equal(h.timers[0].ms, 5000);
    h.timers[0].fn();
    assert.equal(h.context.window.slideSyncState.videoFinished, true);
    assert.equal(h.context.window.advanced, 1);
    h.context.window.slideSyncState = { hasVideo: true, hasAudio: false, videoFinished: false };
    h.omitVisualWhileRecording(4, 7, 'x');
    h.context.window.currentSlideRenderId = 8;
    h.timers[1].fn();
    assert.equal(h.context.window.advanced, 1, 'a newer scene was drawn: nothing moves on twice');
    // the tab capture keeps its notes for the timeline hook
    const tab = recordingHelpers({ exporting: true });
    tab.noteRecording(1, 'Scene 2 is recorded without its visual: x.');
    assert.deepEqual(plain(tab.context.window.exportNotes), [{ t: 12.346, scene: 1, text: 'Scene 2 is recorded without its visual: x.' }]);
});

test('renderSlide: both visual branches check the rule while recording before any notice card; the preview keeps its cards', () => {
    const render = between(page, 'function renderSlide(index) {', "document.addEventListener('keydown'");
    assert.match(render, /const missingVideo = isRecordingVideo\(\) \? missingSceneVisual\(slide, mode\) : null;\s*if \(missingVideo\) \{\s*omitVisualWhileRecording\(index, thisSlideRenderId, missingVideo\);\s*\} else if \(mainPlan && mainPlan\.selection === 'removed'\) \{/);
    assert.match(render, /const missingAnimation = isRecordingVideo\(\) \? missingSceneVisual\(slide, AadhiVisuals\.getMode\(localStorage\)\) : null;\s*if \(missingAnimation\) \{\s*omitVisualWhileRecording\(index, thisSlideRenderId, missingAnimation\);\s*\} else if \(mainPlan && mainPlan\.selection === 'removed'\) \{/);
    assert.ok(render.indexOf('const missingVideo') < render.indexOf("visualNotice('This scene needs an AI video'"));
    assert.ok(render.indexOf('const missingAnimation') < render.lastIndexOf("visualNotice(\"This visual isn't ready yet\","));
    // the side panel's own cards and the empty skill tree
    assert.match(page, /if \(isRecordingVideo\(\)\) \{\s*canvas\.innerHTML = '';\s*noteRecording\(null, 'The lesson has no skill tree: its panel is left empty\.'\);\s*return;\s*\}/);
    assert.match(page, /if \(isRecordingVideo\(\)\) \{ \/\/ \(Phase 22: a recording shows no card, only the empty panel\)\s*loaderEl\.innerHTML = '';/);
    assert.match(page, /if \(isRecordingVideo\(\)\) \{ \/\/ \(Phase 22: a recording shows no card, only the empty panel\)\s*noteRecording\(currentSlide, `Scene \$\{currentSlide \+ 1\}'s side animation is left out/);
});

test('the export\'s preflight lists the scenes without a visual (played scenes only)', () => {
    const prepare = between(page, 'async function prepareLessonForExport(', 'return { warnings, quality, missing };');
    assert.match(prepare, /const missing = \[\];\s*slides\.forEach\(\(s, i\) => \{\s*if \(sceneHidden\(s\)\) return;\s*const reason = missingSceneVisual\(s, mode\);\s*if \(reason\) missing\.push\(\{ index: i, title: s\.title \|\| `Scene \$\{i \+ 1\}`, reason \}\);/);
    assert.ok(prepare.lastIndexOf('const missing = []') > prepare.indexOf('await runLimited(tasks, 3'), 'after the generation and rendering tasks ran');
});

test('the facade: only in render mode; it prepares, plays and reports like the export hooks, and the page reports scenes, the end and reveals', () => {
    const facade = between(page, '        // --- Phase 22: the server\'s rendered export (render_mode.js, render_worker.mjs) ---', '        // --- Publishing Companion');
    assert.match(facade, /const renderRuntime = window\.AadhiRenderMode && window\.AadhiRenderMode\.runtime;/);
    assert.match(facade, /if \(renderRuntime\) \{\s*let prepared = null;\s*let preparing = null;\s*const config = renderRuntime\.config;/);
    assert.match(facade, /async function prepareRenderLesson\(step, progress\) \{[\s\S]*\}\s*window\.renderMode = \{/);
    for (const name of ['async prepare()', 'useFrames(map)', 'lessonLink: () => renderRuntime.pump(exportLessonLink())', 'start()', 'frameReady(tMs)', 'timeline()', 'sceneBegan(index)', 'lessonEnded()', 'note(index, text)', 'noteSync(kind, target, extra)']) {
        assert.ok(facade.includes(name), name);
    }
    // the lesson of the page's link; the worker's project id must agree
    assert.match(facade, /if \(linked && config\.projectId && Number\(linked\) !== config\.projectId\) \{/);
    // the voice is set before any narration is requested (prepareLessonForExport fetches it)
    assert.ok(facade.indexOf('applyRenderVoice(config.tts);') < facade.indexOf('await prepareLessonForExport('));
    // a refused preflight never starts; the intro only from scene 0; a later range starts at the scene before it
    assert.match(facade, /if \(prepared\.preflight\.refused\) \{/);
    assert.match(facade, /if \(!from\) \{\s*startPresentationSequence\(true\);\s*\} else if \(config\.captureFromScene !== null\) \{[\s\S]{0,300}\} else \{\s*const first = sceneHidden\(slides\[from\]\) \? nextPlayableScene\(from, 1\) : from;\s*const before = config\.preroll \? nextPlayableScene\(from, -1\) : -1;/);
    assert.match(facade, /renderRuntime\.markStart\(\);[\s\S]{0,900}isAutoExporting = true;\s*document\.body\.dataset\.mode = 'exporting';/);
    // a whole-lesson pre-roll draws the clips' images from the scene before the range's first one (the transition into the
    // range snapshots the page as drawn)
    assert.match(facade, /const before = firstOfRange === -1 \? -1 : nextPlayableScene\(firstOfRange, -1\);\s*renderRuntime\.presentFrom\(before >= config\.fromScene \? before : null\);/);
    // the page's calls
    assert.match(page, /exportFlow\.sceneStarted\(nav\.rank, nav\.total\);\s*\}\s*if \(window\.renderMode\) window\.renderMode\.sceneBegan\(index\);/);
    assert.equal((page.match(/if \(window\.renderMode\) window\.renderMode\.lessonEnded\(\);[^\n]*\n\s*if \(isAutoExporting\) exportFlow\.lessonFinished\(\);/g) || []).length, 2);
    assert.match(page, /if \(window\.renderMode && !shown\) window\.renderMode\.noteSync\('cascade_reveal', \{ segment: segIdx, item: i,/);
    assert.match(facade, /cinematicStage\.fireSync = rec => \{/);
    // the voice the export hands the render
    assert.match(page, /renderSettings: \(\) => \(\{\s*tts_engine: ttsEngineSelect \? ttsEngineSelect\.value : 'default',\s*voice: document\.getElementById\('voice-select'\)\.value,\s*gemini_voice: document\.getElementById\('gemini-voice-select'\)\.value,\s*rate: ttsState\.rate\s*\}\),/);
    // notes go with the tab capture's timeline too
    assert.match(page, /notes: \(window\.exportNotes \|\| \[\]\)\.slice\(\)/);
});

test('each scene\'s rough length for splitting a lesson into ranges of similar length', () => {
    const helpers = between(page, 'function sceneEditOf(scene) {', '// The caption line follows the editor');
    const parse = between(page, '        function parseNarrationSegments(rawText) {', '        // Signaling: move the golden');
    const estimate = between(page, '        function renderSceneEstimate(s) {', '        // The lesson from scene `index`');
    const context = { slides: [], window: {}, ttsState: { rate: 0.9 } };
    vm.createContext(context);
    vm.runInContext(`${helpers}\n${parse}\n${estimate}\nthis.estimate = renderSceneEstimate;`, context);
    const est = context.estimate;
    // 135 characters at 15 a second at 0.9, a 2 s pause, the 0.8 s step
    const narration = '[SYNC] ' + 'a'.repeat(60) + ' [PAUSE:2] ' + 'b'.repeat(75);
    assert.equal(est({ type: 'content', narration }), Math.round((135 / 15 / 0.9 + 2 + 0.8) * 10) / 10);
    assert.equal(est({ type: 'content' }), 5.8, 'a silent scene holds 5 s');
    assert.equal(est({ type: 'content', narration, edit: { narration_muted: true } }), 5.8);
    assert.equal(est({ type: 'content', narration: 'Short.', edit: { min_seconds: 20 } }), 20.8, 'the minimum duration');
    assert.equal(est({ type: 'quiz_checkpoint', narration: 'q'.repeat(27), countdown_seconds: 5, reveal_narration: 'r'.repeat(27) }), Math.round((2 + 5 + 2 + 0.8) * 10) / 10);
    assert.equal(est({ type: 'quiz_checkpoint' }), 12.8, 'the default 8 s countdown and the 4 s reveal');
    assert.equal(est({ type: 'content', narration, edit: { hidden: true } }), null);
    assert.match(page, /hidden: sceneHidden\(s\),\s*estimate: renderSceneEstimate\(s\), seamSafe: renderSeamSafe\(i\) \}\)\),/);
});

test('a range may start only where the cut cannot be seen: the first scene, or after a Classic full-screen animation scene', () => {
    const helpers = between(page, 'function sceneEditOf(scene) {', '// The caption line follows the editor');
    const seam = between(page, '        function renderSeamSafe(index) {', '        // The lesson from scene `index`');
    const lesson = [
        { type: 'title', aadhi_position: 'left' },                 // 0 first: safe
        { type: 'content', aadhi_position: 'right' },              // 1 after a title with Aadhi: not safe
        { type: 'simulation', manim_code: 'x' },                   // 2 after content: not safe
        { type: 'content', aadhi_position: 'left' },               // 3 after a full-screen simulation: safe
        { type: 'visual', svg: '<svg/>' },                         // 4 not safe
        { type: 'content', edit: { hidden: true } },               // 5 hidden: never a range start
        { type: 'quiz_checkpoint' },                               // 6 the scene before it that plays is the visual (4): safe
        { type: 'ai_video', prompt: 'x', video_url: '/v.mp4' },    // 7 after a quiz (Aadhi on screen): not safe
        { type: 'p5_simulation' },                                 // 8 not safe
        { type: 'content' },                                       // 9 after p5 (not full screen): not safe
        { type: 'simulation', cine: true },                        // 10
        { type: 'content' },                                       // 11 after a cinematic scene: not safe (unsure: false)
        { type: 'simulation' },                                    // 12
        { type: 'content', cine: true }                            // 13 a cinematic scene after a Classic simulation: not safe
    ];
    const context = { slides: lesson, window: {}, cinematicStage: { planFor: s => (s && s.cine ? { template: 'x' } : null) } };
    vm.createContext(context);
    vm.runInContext(`${helpers}\n${seam}\nthis.seamSafe = renderSeamSafe;`, context);
    const safe = lesson.map((_, i) => context.seamSafe(i));
    assert.deepEqual(safe, [true, false, false, true, false, false, true, false, false, false, false, false, false, false]);
    // the first scene that plays is safe even when scene 0 is hidden
    const hiddenFirst = [{ type: 'title', edit: { hidden: true } }, { type: 'content' }, { type: 'content' }];
    context.slides = hiddenFirst;
    assert.deepEqual(hiddenFirst.map((_, i) => context.seamSafe(i)), [false, true, false]);
    assert.match(page, /estimate: renderSceneEstimate\(s\), seamSafe: renderSeamSafe\(i\) \}\)\),/);
    // the full-screen board covers the stage: above the side zone (5), the presenter (6), the particles (1) and Aadhi (0)
    assert.match(page, /\.lecture-overlay-zone \{\s*position: fixed;[^}]*z-index: 10;/);
    assert.match(page, /\.cinematic-scene-container\.cinematic-fullscreen \{[^}]*width: 100vw !important;[^}]*height: 100vh !important;[^}]*background: rgba\(0, 0, 0, 1\) !important;/);
    assert.match(page, /const isGoingFullscreen = \(slide\.type === 'visual' \|\| slide\.type === 'simulation'\);/);
});

test('an item a segment\'s end reveals is in the timeline when it shows; one shown already is not logged again by its [SYNC]', () => {
    // the page's own leftover reveal, run with fake items and timers (the reveal itself is unchanged: preview behaviour)
    const source = between(page, '                const revealSegmentElements = () => {', '                try {');
    const timers = [];
    const logged = [];
    const item = (text, visible) => {
        const classes = new Set(visible ? ['cascade-visible'] : ['cascade-hidden']);
        return { textContent: ` ${text} `, classList: { contains: c => classes.has(c), add: c => classes.add(c), remove: c => classes.delete(c) }, classes };
    };
    const elements = [item('Lesson 01', false), item('Shown by its [SYNC]', true)];
    const context = { elements, segIdx: 0, setTimeout: (fn, ms) => timers.push({ fn, ms }),
        window: { renderMode: { noteSync: (kind, target, extra) => logged.push([kind, target, extra]) } } };
    vm.createContext(context);
    vm.runInContext(`${source}\nthis.reveal = revealSegmentElements;`, context);
    context.reveal();
    assert.deepEqual(timers.map(t => t.ms), [200, 600], 'when they show: as before');
    timers.forEach(t => t.fn());
    assert.ok(elements.every(el => el.classes.has('cascade-visible') && !el.classes.has('cascade-hidden')));
    assert.deepEqual(plain(logged), [['cascade_reveal', { segment: 0, item: 0, text: 'Lesson 01' }, { leftover: true }]], 'only the item that showed now');
    context.reveal();
    timers.slice(2).forEach(t => t.fn());
    assert.equal(logged.length, 1, 'called again on later timeupdates: nothing new shows, nothing logged');
    // the [SYNC] path and the end-of-narration reveal log only an item that actually shows then
    assert.match(page, /const shown = elements\[i\]\.classList\.contains\('cascade-visible'\);[\s\S]{0,700}if \(window\.renderMode && !shown\) window\.renderMode\.noteSync\('cascade_reveal', \{ segment: segIdx, item: i,/);
    assert.match(page, /const triggerFallbackReveal = \(\) => \{[\s\S]{0,900}if \(window\.renderMode && !shown\) window\.renderMode\.noteSync\('cascade_reveal', \{ item: index,/);
});

test('every clip the render may show is listed for its frame set (Aadhi\'s by file name)', () => {
    const list = between(page, '        function renderMediaList(fromScene) {', '        // The lesson from scene `index`');
    assert.match(list, /add\(stem\(src\), 'mascot', src, true, false\)/);
    assert.match(list, /if \(!fromScene\) add\('logo_animation', 'logo', document\.getElementById\('intro-logo-video'\)\.getAttribute\('src'\), false, true\);/);
    assert.match(list, /add\(`scene-\$\{i\}`, 'video', main \? main\.url : s\.video_url, !voice, voice\);/);
    assert.match(list, /add\(`scene-\$\{i\}-presenter`, 'presenter', presenter\.media\.url, false, false\);/);
});

test('a clip without a known duration lasts as long as its frame set (review L2: its scene still moves on)', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'render_mode.js'), 'utf8');
    const body = between(src, 'const durationOf = el => {', '};');
    assert.match(body, /if \(d > 0 && Number\.isFinite\(d\)\) return d;/, 'a known duration is used as it is');
    assert.match(body, /state\.frameSets\.get\(normalizeSrc\(/, 'otherwise the clip\'s frame set');
    assert.match(body, /set\.manifest\.frames \/ set\.manifest\.fps : NaN;/, 'frames / fps, or still unknown without a set');
});
