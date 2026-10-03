// Phase 19: the page's playback of the editor's per-scene settings (index.html), checked in its source and, for the small
// helpers, by running them: a scene hidden in the editor (scene.edit.hidden) is never played (next / previous, the
// automatic advance and the lesson's start skip it; the dots and the progress count only the scenes that play; an export
// loads, reports and records nothing for it); a minimum duration (scene.edit.min_seconds) holds the scene until it has
// played that long, pauses excluded (the next scene starts after max(800 ms + end hold, min_seconds − elapsed); a silent
// or muted scene holds max(5 s, min_seconds)); a muted narration (scene.edit.narration_muted) is never spoken, requested
// or captioned; captions switched off for the lesson (window.lessonEditor.captions.visible === false) or the scene
// (scene.edit.captions === 'off') are neither shown nor written into the export's subtitles. Lessons without editor data
// play exactly as before. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const page = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function between(start, end, from = 0) {
    const a = page.indexOf(start, from);
    assert.ok(a >= 0, `missing: ${start}`);
    const b = page.indexOf(end, a + start.length);
    assert.ok(b > a, `missing after ${start}: ${end}`);
    return page.slice(a, b);
}

const renderSlide = between('function renderSlide(index) {', 'document.addEventListener(\'keydown\'');
const checkAndAdvance = between('window.checkAndAdvanceSlide = () => {', '// Instantly kill any currently playing');
const finalize = between('const finalizeSlideAnimations = () => {', '}; // End of finalizeSlideAnimations');
const prepare = between('async function prepareLessonForExport(', 'return { warnings, quality };');
const helpersSource = between('function sceneEditOf(scene) {', '// The caption line follows the editor');
const captionsSource = between('function applySceneCaptions(scene, clearLine) {', '// The scene\'s own clock');
const clockSource = between('const sceneClock = {', '// Scene dots and progress lines');
const navigationSource = between('function updateSceneNavigation(index) {', '// The editor (wired by the page) after an edit');

// objects made inside the page sandbox, compared as plain data
const plain = value => JSON.parse(JSON.stringify(value));

// The helpers as the page runs them, over a lesson of our own
function helpers(slides, lessonEditor) {
    const context = { slides, window: { lessonEditor } };
    vm.createContext(context);
    vm.runInContext(helpersSource + '\nthis.api = { sceneEditOf, sceneHidden, sceneHoldMs, sceneNarrationMuted, sceneNarration, '
        + 'sceneCaptionsOff, playedScenes, nextPlayableScene, sceneNavigation };', context);
    return context.api;
}

test('the editor settings are read defensively (absent, malformed or out of range: the generated scene)', () => {
    const h = helpers([]);
    for (const scene of [undefined, null, {}, { edit: null }, { edit: 'hidden' }, { edit: [true] }, { edit: {} }]) {
        assert.equal(h.sceneHidden(scene), false);
        assert.equal(h.sceneHoldMs(scene), 0);
        assert.equal(h.sceneNarrationMuted(scene), false);
        assert.equal(h.sceneCaptionsOff(scene), false);
    }
    assert.equal(h.sceneHidden({ edit: { hidden: true } }), true);
    assert.equal(h.sceneHidden({ edit: { hidden: 'yes' } }), false, 'only true hides a scene');
    assert.equal(h.sceneHoldMs({ edit: { min_seconds: 12 } }), 12000);
    assert.equal(h.sceneHoldMs({ edit: { min_seconds: 0.5 } }), 500);
    assert.equal(h.sceneHoldMs({ edit: { min_seconds: 600 } }), 600000);
    for (const bad of [0, -3, 600.5, '10', true, NaN, Infinity, null, [5]]) {
        assert.equal(h.sceneHoldMs({ edit: { min_seconds: bad } }), 0, `min_seconds ${String(bad)}`);
    }
    assert.equal(h.sceneNarrationMuted({ edit: { narration_muted: true } }), true);
    assert.equal(h.sceneNarrationMuted({ edit: { narration_muted: 1 } }), false);
    assert.equal(h.sceneNarration({ narration: 'Hello.' }), 'Hello.');
    assert.equal(h.sceneNarration({ narration: 'Hello.', edit: { narration_muted: true } }), '');
    assert.equal(h.sceneNarration({}), '');
    assert.equal(h.sceneCaptionsOff({ edit: { captions: 'off' } }), true);
    assert.equal(h.sceneCaptionsOff({ edit: { captions: 'on' } }), false);
    // the lesson's caption visibility (window.lessonEditor, exposed by the page; read defensively)
    assert.equal(helpers([], { captions: { visible: false } }).sceneCaptionsOff({}), true);
    for (const editor of [undefined, null, {}, { captions: null }, { captions: { visible: true } }, { captions: { visible: 0 } }]) {
        assert.equal(helpers([], editor).sceneCaptionsOff({}), false);
    }
});

test('next, previous and the start find the scenes that play; old lessons step one scene at a time', () => {
    const old = helpers([{}, {}, {}]);
    assert.deepEqual([old.nextPlayableScene(0, 1), old.nextPlayableScene(1, 1), old.nextPlayableScene(2, 1)], [1, 2, -1]);
    assert.deepEqual([old.nextPlayableScene(0, -1), old.nextPlayableScene(2, -1)], [-1, 1]);
    assert.deepEqual(plain(old.sceneNavigation(1)), { rank: 1, total: 3, shown: [0, 1, 2] });
    const hidden = { edit: { hidden: true } };
    const h = helpers([hidden, {}, hidden, hidden, {}, hidden]);
    assert.equal(h.nextPlayableScene(-1, 1), 1, 'the lesson starts at the first scene that plays');
    assert.equal(h.nextPlayableScene(1, 1), 4);
    assert.equal(h.nextPlayableScene(4, 1), -1, 'nothing after: the lesson ends, as at its last scene');
    assert.equal(h.nextPlayableScene(4, -1), 1);
    assert.equal(h.nextPlayableScene(1, -1), -1);
    assert.equal(h.playedScenes().length, 2);
    assert.deepEqual(plain(h.sceneNavigation(4)), { rank: 1, total: 2, shown: [1, 4] });
    assert.deepEqual(plain(h.sceneNavigation(2)), { rank: 1, total: 2, shown: [1, 4] }, 'a hidden scene opened directly ranks after');
    const none = helpers([hidden, hidden]);
    assert.equal(none.nextPlayableScene(-1, 1), -1);
    assert.equal(none.playedScenes().length, 0);
});

test('navigation, the automatic advance and the start skip hidden scenes', () => {
    assert.match(page, /function nextSlide\(\) \{\s*const next = nextPlayableScene\(currentSlide, 1\);\s*if \(next !== -1\) \{\s*currentSlide = next;\s*renderSlide\(currentSlide\);/);
    assert.match(page, /function prevSlide\(\) \{\s*const previous = nextPlayableScene\(currentSlide, -1\);\s*if \(previous !== -1\) \{\s*currentSlide = previous;\s*renderSlide\(currentSlide\);/);
    assert.doesNotMatch(page, /currentSlide\+\+|currentSlide--/, 'never one scene further without asking which scene plays');
    // the automatic advance: the next scene that plays, else the lesson ends (and an export stops recording)
    assert.match(checkAndAdvance, /const proceed = \(\) => \{[\s\S]*?const nextIndex = nextPlayableScene\(currentSlide, 1\);\s*if \(nextIndex !== -1\) \{\s*currentSlide = nextIndex;[\s\S]*?\} else \{\s*ttsState\.isPlaying = false;\s*updateTTSButtons\(\);[\s\S]*?if \(isAutoExporting\) exportFlow\.lessonFinished\(\);/);
    const start = between('function startMainPresentation() {', 'function startPresentationSequence(');
    assert.match(start, /const first = sceneHidden\(slides\[currentSlide\]\) \? nextPlayableScene\(currentSlide, 1\) : currentSlide;/);
    assert.match(start, /if \(first === -1\) \{[\s\S]*?ttsState\.isPlaying = false;[\s\S]*?if \(isAutoExporting\) exportFlow\.lessonFinished\(\);\s*return;\s*\}\s*currentSlide = first;/);
    assert.match(start, /updateSidePanel\(slides\[nextPlayableScene\(-1, 1\)\]\);/);
    // the Aadhi clips and side images the lesson needs: the scenes that play
    assert.match(page, /mascot\.preload\(AadhiMascot\.requiredAssetKeys\(playedScenes\(\)\)\);/);
    const preload = between('function preloadSideImages() {', 'const aiVisualsSelect');
    assert.match(preload, /if \(sceneHidden\(s\)\) return;/);
    // the background shown next under a full-screen scene is never a hidden scene's
    assert.match(renderSlide, /for \(let i = index \+ 1; i < slides\.length; i\+\+\) \{\s*if \(sceneHidden\(slides\[i\]\)\) continue;/);
});

test('the dots and the progress count the scenes that play; the dots follow the scene list, not only its length', () => {
    assert.match(renderSlide, /updateSceneNavigation\(index\);/);
    assert.doesNotMatch(page, /scrubber\.children\.length !== slides\.length/, 'the old count-only check is gone');
    assert.match(navigationSource, /const key = slides\.map\(\(s, i\) => \[s && s\.scene_id \? s\.scene_id : i, sceneHidden\(s\) \? 1 : 0, \(s && s\.title\) \|\| ''\]\.join\(':'\)\)\.join\('\|'\);/);
    assert.match(navigationSource, /if \(scrubber\.dataset\.sceneKey !== key\) \{\s*scrubber\.dataset\.sceneKey = key;\s*scrubber\.innerHTML = '';\s*nav\.shown\.forEach\(idx => \{/);
    assert.match(navigationSource, /dot\.dataset\.scene = String\(idx\);/);
    assert.match(navigationSource, /if \(Number\(dot\.dataset\.scene\) === index\) dot\.classList\.add\('active'\);/);
    assert.match(navigationSource, /progFill\.style\.width = \(\(nav\.rank \+ 1\) \/ nav\.total\) \* 100 \+ '%';/);
    assert.match(navigationSource, /const progress = \(nav\.rank \/ \(nav\.total - 1\)\) \* 100;/);
    // the editor refreshes them (and the captions) after an edit, without redrawing the scene
    assert.match(page, /window\.refreshScenePlayback = \(\) => \{\s*if \(!slides\[currentSlide\]\) return;\s*applySceneCaptions\(slides\[currentSlide\], false\);\s*updateSceneNavigation\(currentSlide\);/);
    // an export's progress: the scene among the scenes that play
    assert.match(renderSlide, /exportFlow\.sceneStarted\(nav\.rank, nav\.total\);/);
});

// The scene clock with a fake time source: it counts only while the lesson plays
function clockHarness() {
    const timers = new Map();
    let nextId = 1;
    const env = {
        now: 0, ttsState: { isPlaying: true }, window: { currentSlideRenderId: 1 },
        Date: { now: () => env.now },
        setInterval: (fn) => { const id = nextId++; timers.set(id, fn); return id; },
        clearInterval: (id) => { timers.delete(id); },
        Promise
    };
    vm.createContext(env);
    vm.runInContext(clockSource + '\nthis.sceneClock = sceneClock;', env);
    env.advance = async (ms, step = 100) => {
        for (let t = 0; t < ms; t += step) {
            env.now += step;
            Array.from(timers.values()).forEach(fn => fn());
            await Promise.resolve();
        }
        await new Promise(r => setImmediate(r));
    };
    env.timers = timers;
    return env;
}

test('the scene clock counts played time only, and a wait ends with its scene', async () => {
    const env = clockHarness();
    const clock = env.sceneClock;
    clock.start(1);
    const done = [];
    clock.waitUntil(1, 3000).then(ok => done.push(['3s', ok]));
    await env.advance(1000);
    assert.equal(clock.elapsed(1), 1000);
    env.ttsState.isPlaying = false; // paused: the clock stands still
    await env.advance(5000);
    assert.equal(clock.elapsed(1), 1000);
    assert.deepEqual(done, []);
    env.ttsState.isPlaying = true;
    await env.advance(2000);
    assert.deepEqual(done, [['3s', true]]);
    assert.equal(clock.elapsed(2), 0, 'another scene\'s clock reads nothing');
    // a wait already passed resolves at once; another scene drawn ends the waits (false) and the clock
    assert.equal(await clock.waitUntil(1, 500), true);
    const late = clock.waitUntil(1, 60000);
    env.window.currentSlideRenderId = 2;
    await env.advance(100);
    assert.equal(await late, false);
    assert.equal(env.timers.size, 0);
    assert.equal(await clock.waitUntil(1, 10), false);
    // stop() (a scene without editor timing) also ends what was waiting
    env.window.currentSlideRenderId = 3;
    clock.start(3);
    const stopped = clock.waitUntil(3, 1000);
    clock.stop();
    assert.equal(await stopped, false);
    assert.equal(env.timers.size, 0);
});

test('a minimum duration holds the scene: max(800 ms + end hold, min_seconds − elapsed), pauses excluded', () => {
    // only scenes with editor timing run the clock
    assert.match(renderSlide, /const holdMs = sceneHoldMs\(slide\);\s*if \(holdMs > 0 \|\| narrationMuted\) sceneClock\.start\(thisSlideRenderId\);\s*else sceneClock\.stop\(\);/);
    assert.match(clockSource, /if \(ttsState\.isPlaying\) this\.played \+= now - this\.last;/);
    // the usual pause before the next scene is kept, and a longer hold waits on the scene's own clock
    assert.match(checkAndAdvance, /const endHoldMs = Math\.round\(Math\.min\(0\.7, Math\.max\(0, cinematicStage\.endHold\(\) \|\| 0\)\) \* 1000\);/);
    assert.match(checkAndAdvance, /setTimeout\(\(\) => \{\s*if \(ttsState\.isPlaying\) renderSlide\(currentSlide\);\s*\}, 800 \+ endHoldMs\);/);
    assert.match(checkAndAdvance, /const leadMs = nextPlayableScene\(currentSlide, 1\) !== -1 \? 800 \+ endHoldMs : 0;/);
    assert.match(checkAndAdvance, /if \(holdMs > 0 && holdMs - sceneClock\.elapsed\(thisSlideRenderId\) > leadMs\) \{\s*syncState\.holding = true;\s*sceneClock\.waitUntil\(thisSlideRenderId, holdMs - leadMs\)\.then\(ok => \{\s*syncState\.holding = false;\s*if \(ok && slides\[currentSlide\] === slide\) proceed\(\);/);
    assert.match(checkAndAdvance, /if \(syncState\.isReadyToAdvance\(\) && ttsState\.isPlaying && !syncState\.holding\) \{/);
    // a silent or muted scene: max(5 s, min_seconds) on the clock; a silent scene without editor timing keeps its 5 s timer
    assert.match(finalize, /if \(holdMs > 0 \|\| narrationMuted\) \{[\s\S]*?sceneClock\.waitUntil\(thisSlideRenderId, Math\.max\(5000, holdMs - leadMs\)\)\.then\(ok => \{\s*if \(!ok \|\| slides\[currentSlide\] !== slide\) return;\s*if \(narrationMuted\) cinematicStage\.narrationFinished\(\);\s*window\.slideSyncState\.audioFinished = true;\s*window\.checkAndAdvanceSlide\(\);/);
    assert.match(finalize, /\} else if \(ttsState\.isPlaying \|\| isAutoExporting\) \{\s*\/\/ Fallback auto-advance for silent slides\s*setTimeout\(\(\) => \{\s*window\.slideSyncState\.audioFinished = true;\s*window\.checkAndAdvanceSlide\(\);\s*\}, 5000\);/);
    // resuming during the hold continues it (the narration is over: never replayed)
    assert.match(page, /if \(window\.slideSyncState && window\.slideSyncState\.holding\) \{\s*\/\/ Phase 19: the scene is holding its minimum duration/);
});

test('a muted narration is never spoken, requested or captioned; the scene plays as a silent one', () => {
    assert.match(renderSlide, /const narrationMuted = sceneNarrationMuted\(slide\);\s*const videoVoice = slide\.type === 'ai_video' && slide\.ai_audio_source === 'video' && !narrationMuted;/);
    assert.match(renderSlide, /&& \(videoVoice \|\| \(slide\.type === 'simulation' && \(slide\.manim_code \|\| slide\.simulation_code \|\| slide\.code\)\)\),\s*hasAudio: !!slide\.narration && !narrationMuted && !videoVoice,/);
    assert.match(renderSlide, /let videoAttrs = videoVoice/, 'an AI video carrying the narration plays muted and looped');
    assert.match(finalize, /\} else if \(videoVoice\) \{[\s\S]*?\} else if \(sceneNarration\(slide\)\) \{\s*speakNarration\(slide\.narration, Array\.from\(animateItems\)\);/);
    // the quiz: no question or reveal narration when muted (its countdown and reveal still run)
    const quiz = between('function runQuizCheckpoint(slide, renderId, attempt) {', 'function updateSidePanel(slide) {');
    assert.match(quiz, /const revealNarration = sceneNarrationMuted\(slide\) \? '' : \(slide\.reveal_narration \|\| slide\.explanation \|\| ''\);/);
    assert.match(quiz, /if \(sceneNarration\(slide\)\) \{\s*speakNarration\(slide\.narration, \[\], \{ onEnded: startCountdown, mascotState: 'question' \}\);/);
    // the play / unmute buttons never start a muted narration
    assert.equal((page.match(/if \(slide && sceneNarration\(slide\)\) \{ \/\/ \(Phase 19: never a narration muted in the editor\)\s*speakNarration\(slide\.narration\);/g) || []).length, 2);
    assert.doesNotMatch(page, /if \(slide && slide\.narration\) \{\s*speakNarration\(slide\.narration\);/);
    // no caption line for it
    assert.match(renderSlide, /speakNarration\(null\);\s*\/\/ Phase 19: this scene's captions[^\n]*\n\s*applySceneCaptions\(slide, narrationMuted\);/);
    // the export makes no narration audio (nor presenter speech) for it
    assert.match(prepare, /const muted = sceneNarrationMuted\(s\);[^\n]*\n\s*if \(s\.narration && !muted && !\(s\.type === 'ai_video' && s\.ai_audio_source === 'video'\)\) narrations\.push\(s\.narration\);/);
    assert.match(prepare, /if \(s\.type === 'quiz_checkpoint' && !muted && \(s\.reveal_narration \|\| s\.explanation\)\)/);
    assert.match(prepare, /\} else if \(s\.narration && !muted\) \{\s*const \{ voice, engine \} = currentVoice\(\);/);
});

// A caption track of our own, as the page's #subtitle-track
function fakeTrack() {
    const classes = new Set(['active']);
    return {
        dataset: {}, style: { visibility: '' }, textContent: 'An earlier line.',
        classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
        classes
    };
}

function captions(track, lessonEditor) {
    const context = { window: { lessonEditor }, document: { getElementById: id => (id === 'subtitle-track' ? track : null) } };
    vm.createContext(context);
    vm.runInContext(helpersSource + captionsSource + '\nthis.applySceneCaptions = applySceneCaptions;', context);
    return context.applySceneCaptions;
}

test('captions switched off are hidden and marked for the caption log; Phase 17 styling is untouched', () => {
    const track = fakeTrack();
    const apply = captions(track);
    apply({ narration: 'Hello.' }, false); // an old scene: nothing changes
    assert.deepEqual([track.dataset, track.style.visibility, track.textContent, track.classes.has('active')], [{}, '', 'An earlier line.', true]);
    apply({ edit: { captions: 'off' } }, false);
    assert.equal(track.dataset.captions, 'off');
    assert.equal(track.style.visibility, 'hidden');
    track.textContent = 'Spoken while hidden.';
    apply({}, false); // shown again: the hidden line never appears
    assert.equal(track.dataset.captions, undefined);
    assert.equal(track.style.visibility, '');
    assert.equal(track.textContent, '');
    assert.equal(track.classes.has('active'), false);
    // a muted scene starts without a line
    const muted = fakeTrack();
    captions(muted)({ edit: { narration_muted: true } }, true);
    assert.equal(muted.textContent, '');
    assert.equal(muted.classes.has('active'), false);
    // the lesson's own setting hides every scene's line
    const lessonOff = fakeTrack();
    captions(lessonOff, { captions: { visible: false } })({}, false);
    assert.equal(lessonOff.dataset.captions, 'off');
    // the page never restyles the captions for this (no class, no font or colour change: Phase 17 owns those)
    assert.doesNotMatch(captionsSource, /className|fontSize|color|setProperty/);
});

test('the export\'s caption log skips a hidden line, so the exported subtitles match the picture', () => {
    const log = between('function startCaptionLog() {', 'function stopCaptionLog() {');
    assert.match(log, /const text = track\.classList\.contains\('active'\) && track\.dataset\.captions !== 'off'\s*\? \(track\.textContent \|\| ''\)\.replace\(\/\\s\+\/g, ' '\)\.trim\(\) : '';/);
    assert.match(log, /attributeFilter: \['class', 'data-captions'\]/);
});

test('an export loads, reports and records nothing for a hidden scene', () => {
    assert.match(prepare, /if \(!playedScenes\(\)\.length\) \{\s*throw new AadhiExport\.ExportError\('Every scene of this lesson is hidden in the editor\./);
    assert.ok(prepare.indexOf('if (!playedScenes().length)') < prepare.indexOf('ttsState.isPlaying = false;'), 'refused before anything changes');
    assert.match(prepare, /if \(plan\.error && !sceneHidden\(slides\[plan\.scene_index\]\)\) warnings\.push\(/);
    // a library asset only a hidden scene uses is not reported missing (old lessons: nothing is filtered)
    assert.match(prepare, /const playedAssetIds = new Set\(AadhiAssets\.collectAssetIds\(playedScenes\(\)\)\);\s*const hiddenOnlyIds = new Set\(AadhiAssets\.collectAssetIds\(slides\.filter\(sceneHidden\)\)\.filter\(id => !playedAssetIds\.has\(id\)\)\);\s*assetCheck\.missing\.filter\(id => !hiddenOnlyIds\.has\(id\)\)\.forEach\(id => \{/);
    assert.match(prepare, /run: \(\) => mascot\.preload\(AadhiMascot\.requiredAssetKeys\(playedScenes\(\)\)\)/);
    assert.equal((prepare.match(/slides\.forEach\(\(s, i\) => \{\s*if \(sceneHidden\(s\)\) return;/g) || []).length, 2, 'backgrounds and every scene asset');
    assert.match(prepare, /const hiddenNeedsAiVideo = slides\.some\(\(s, i\) => sceneHidden\(s\) && needsAiVideo\(s, i\)\);\s*if \(currentProjectId && mode === 'all' && !hiddenNeedsAiVideo && slides\.some\(needsAiVideo\)\) \{/);
    assert.match(page, /\.filter\(f => !\(Number\.isInteger\(f\.scene\) && sceneHidden\(slides\[f\.scene\]\)\)\)/, 'no quality note about a hidden scene');
    // the chapters come from the scenes drawn (renderSlide), so a scene never played is never logged
    assert.equal((page.match(/window\.exportSceneLog\.push\(/g) || []).length, 1);
    assert.ok(renderSlide.includes('window.exportSceneLog.push('));
});

// Phase 21 (browser check finding): two redraws of a paused, synchronized scene in a row (the editor after an undo: the
// history and then the composition) — the first render's view transition is skipped by the second, but its update still
// runs first. The page's own finalize step, run twice in that order: only the newest render narrates, so it finds the
// editor's still preview and nothing plays; a single render still narrates as before.
function finalizeRuns(order) {
    const item = () => {
        const set = new Set();
        return { classList: { add: c => set.add(c), remove: c => set.delete(c), has: c => set.has(c) }, matches: () => false, parentElement: null };
    };
    const ctx = {
        window: { currentSlideRenderId: 0, editorStillPreview: false }, ttsState: { isPlaying: false },
        document: { querySelector: () => null, getElementById: () => null }, setTimeout: () => 0, Promise, Array, calls: [], item,
        isAutoExporting: false, currentSlide: 0, nextPlayableScene: () => -1, updateTTSButtons() {}, runQuizCheckpoint() {},
        cinematicStage: { prepareBoard() {}, start() {}, endHold: () => 0, narrationFinished() {} },
        sceneClock: { waitUntil: () => Promise.resolve(false) }, sceneNarration: s => s.narration || ''
    };
    // the head of the page's speakNarration (the still preview) and its synchronized-scene path, which marks the lesson playing
    ctx.speakNarration = (text, els) => {
        ctx.calls.push(text);
        if (ctx.ttsState.isPlaying) ctx.window.editorStillPreview = false;
        if (ctx.window.editorStillPreview && text) { ctx.window.editorStillPreview = false; (els || []).forEach(el => el.classList.add('cascade-visible')); return; }
        if (text) ctx.ttsState.isPlaying = true;
    };
    vm.createContext(ctx);
    vm.runInContext(`this.make = (thisSlideRenderId, slide) => {
        const items = [item(), item()];
        const container = { querySelectorAll: () => items };
        items.forEach(i => { i.parentElement = container; });
        const isGoingFullscreen = false, cinePlan = { reveal: 'cascade' }, videoVoice = false, holdMs = 0, narrationMuted = false;
        ${finalize}};
        return finalizeSlideAnimations;
    };`, ctx);
    const slide = { type: 'content', narration: 'Plants make food from light.' };
    const renders = order.map(() => { ctx.window.editorStillPreview = true; return ctx.make(++ctx.window.currentSlideRenderId, slide); });
    renders.forEach(run => run());
    return { playing: ctx.ttsState.isPlaying, calls: ctx.calls.length };
}

test('a scene drawn twice in a row while paused stays paused: only the newest render narrates (browser check finding)', () => {
    assert.deepEqual(finalizeRuns(['older', 'newer']), { playing: false, calls: 1 });
    assert.deepEqual(finalizeRuns(['only']), { playing: false, calls: 1 }, 'a single still render narrates as before (and stays still)');
    assert.match(finalize, /if \(window\.currentSlideRenderId !== thisSlideRenderId\) \{\s*animateItems\.forEach\(item => \{ item\.classList\.remove\('cascade-hidden'\); item\.classList\.add\('cascade-visible'\); \}\);\s*\} else if \(slide\.type === 'quiz_checkpoint'\) \{/);
});
