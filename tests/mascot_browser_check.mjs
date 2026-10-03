// End-to-end check of the Aadhi mascot in a real browser (Chrome or Edge).
//
// Plays tests/fixtures/mascot_lesson.json through the real page and checks what
// the unit tests cannot: real <video> playback and crossfades, narration sync
// with real <audio>, the fallback under a broken clip, the Enable playback click
// under a (simulated) autoplay block, and the built-in demo lesson (older
// screenplay without aadhi_position).
//
// Needs the server running, Chrome or Edge installed, and playwright-core:
//   npm install --no-save playwright-core
//   node tests/mascot_browser_check.mjs [http://127.0.0.1:9942]
// Narration audio and the login check are stubbed, so no API keys or password are needed.
// Screenshots go to $MASCOT_CHECK_OUT (default: <tmp>/aadhi-mascot-check).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const BASE = (process.argv[2] || 'http://127.0.0.1:9942').replace(/\/$/, '');
const OUT = process.env.MASCOT_CHECK_OUT || path.join(os.tmpdir(), 'aadhi-mascot-check');
const lesson = JSON.parse(fs.readFileSync(path.join(here, 'fixtures', 'mascot_lesson.json'), 'utf8'));

const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: !!ok, detail });
}

async function loadPlaywright() {
    try {
        return await import('playwright-core');
    } catch (e) {
        if (process.env.PLAYWRIGHT_CORE_DIR) {
            return import(pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE_DIR, 'index.mjs')).href);
        }
        throw new Error('playwright-core not found: run `npm install --no-save playwright-core` (or set PLAYWRIGHT_CORE_DIR)');
    }
}

// No sound for whoever runs the check: Chrome is muted and sends nothing to the speakers, and
// browser speech (which Windows speaks outside Chrome's audio) is replaced by a silent stand-in
function silenceSpeech() {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.speak = utterance => setTimeout(() => {
        if (utterance.onstart) utterance.onstart(new Event('start'));
        setTimeout(() => utterance.onend && utterance.onend(new Event('end')), 300);
    }, 0);
    window.speechSynthesis.cancel = () => {};
}

async function launch(chromium, autoplayPolicy) {
    let lastError;
    for (const channel of ['chrome', 'msedge']) {
        try {
            return await chromium.launch({ channel, headless: true, args: [`--autoplay-policy=${autoplayPolicy}`, '--disable-audio-output'] });
        } catch (e) {
            lastError = e;
        }
    }
    throw lastError;
}

// 16 kHz mono tone standing in for narration audio
function toneWav(seconds) {
    const rate = 16000;
    const n = Math.round(seconds * rate);
    const buf = Buffer.alloc(44 + n * 2);
    buf.write('RIFF', 0);
    buf.writeUInt32LE(36 + n * 2, 4);
    buf.write('WAVEfmt ', 8);
    buf.writeUInt32LE(16, 16);
    buf.writeUInt16LE(1, 20);
    buf.writeUInt16LE(1, 22);
    buf.writeUInt32LE(rate, 24);
    buf.writeUInt32LE(rate * 2, 28);
    buf.writeUInt16LE(2, 32);
    buf.writeUInt16LE(16, 34);
    buf.write('data', 36);
    buf.writeUInt32LE(n * 2, 40);
    for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 220 * i / rate) * 3000), 44 + i * 2);
    return buf;
}

// Browser automation always counts as a user gesture, so a real autoplay block cannot be
// triggered. This reproduces one: audible play() rejects with NotAllowedError until a
// trusted click, while muted clips may play (the rule browsers apply).
function simulateStrictAutoplay() {
    let activated = false;
    window.addEventListener('click', e => { if (e.isTrusted) activated = true; }, true);
    const realPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
        if (!activated && !this.muted) {
            return Promise.reject(new DOMException('play() needs a user gesture', 'NotAllowedError'));
        }
        return realPlay.call(this);
    };
}

async function openPage(browser, { blockClip, strictAutoplay } = {}) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    await context.addInitScript(silenceSpeech);
    if (strictAutoplay) await context.addInitScript(simulateStrictAutoplay);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // A signed-in session (the login modal would cover the page); every API the lesson calls is stubbed
    await context.addInitScript(() => localStorage.setItem('jwt_token', 'mascot-check'));
    await page.route('**/api/me', r => r.fulfill({ json: { username: 'mascot-check' } }));
    await page.route('**/generate-audio', r => {
        const { text } = r.request().postDataJSON();
        const seconds = Math.min(4, Math.max(1.2, text.length * 0.045));
        return r.fulfill({ json: { status: 'success', audio_url: `/__mascot_check_audio.wav?d=${seconds.toFixed(2)}&n=${Math.random()}` } });
    });
    await page.route('**/__mascot_check_audio.wav*', r => {
        const seconds = parseFloat(new URL(r.request().url()).searchParams.get('d'));
        return r.fulfill({ status: 200, contentType: 'audio/wav', body: toneWav(seconds) });
    });
    // Generation endpoints are out of scope here; a 404 keeps the page from asking for a login
    for (const api of ['generate-ai-video', 'generate-ai-image', 'render', 'get-image', 'get-gif', 'save-history', 'api/visuals/plan', 'api/presenters', 'api/cinematic']) {
        await page.route(`**/${api}*`, r => r.fulfill({ status: 404, json: { detail: 'stubbed by mascot check' } }));
    }
    if (blockClip) await page.route(`**/${blockClip}*`, r => r.abort('failed'));
    await page.goto(BASE + '/?mascotDebug=1');
    await page.waitForFunction(() => typeof mascot !== 'undefined' && typeof showStartOverlay === 'function');
    // Narrate through the (stubbed) audio pipeline rather than the browser's speechSynthesis
    await page.evaluate(() => {
        const engine = document.getElementById('tts-engine-select');
        engine.value = Array.from(engine.options).find(o => o.value !== 'default').value;
    });
    return { context, page, errors };
}

async function startFixtureLesson(page) {
    await page.evaluate(data => {
        slides.length = 0;
        slides.push(...data.scenes);
        conceptMapData = data.concept_map;
        currentSubjectName = data.subject_name;
        currentUnitName = data.unit_name;
        currentSessionNumber = data.session_number;
        currentSessionTitle = data.session_title;
        showStartOverlay();
    }, lesson);
    await page.waitForTimeout(300);
    await page.evaluate(() => skipIntroSequence());
}

function sample(page) {
    return page.evaluate(() => {
        const layers = Array.from(document.querySelectorAll('#mascot-bg .mascot-layer'));
        const shown = layers.find(v => v.classList.contains('active-layer'));
        const fallbackEl = document.querySelector('#mascot-bg .mascot-fallback');
        const frame = document.querySelector('#mascot-bg .mascot-fallback-frame.is-current');
        const s = mascot.getStatus();
        return {
            t: performance.now(),
            slide: currentSlide,
            done: !ttsState.isPlaying && currentSlide === slides.length - 1,
            state: s.state,
            placement: s.placement,
            audio: s.audio,
            fallback: s.fallback,
            visible: layers.filter(v => v.classList.contains('active-layer')).map(v => v.dataset.asset),
            playing: layers.filter(v => !v.paused).map(v => v.dataset.asset),
            shownAsset: shown ? shown.dataset.asset : null,
            shownTime: shown ? shown.currentTime : null,
            cue: document.querySelector('#mascot-bg .mascot-cue').getAttribute('data-cue'),
            fallbackOpacity: fallbackEl ? parseFloat(getComputedStyle(fallbackEl).opacity) : 0,
            frameTransform: frame ? getComputedStyle(frame).transform : null,
            frameAnimation: frame ? getComputedStyle(frame).animationName : null,
            gate: !!document.querySelector('.playback-gate.visible'),
            hud: (document.querySelector('.mascot-debug-hud') || {}).textContent || ''
        };
    });
}

// Samples every ~100ms until until(sample) is true or timeoutMs passes; shots maps a label to a predicate
async function record(page, { until, timeoutMs, shots = {} }) {
    const samples = [];
    const taken = new Set();
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        const s = await sample(page);
        samples.push(s);
        for (const [label, when] of Object.entries(shots)) {
            if (!taken.has(label) && when(s)) {
                taken.add(label);
                await page.screenshot({ path: path.join(OUT, label + '.png') });
            }
        }
        if (until(s)) break;
        await page.waitForTimeout(100);
    }
    return samples;
}

const uniq = list => Array.from(new Set(list));

// Longest stretch the on-screen clip showed the same frame (same clip, same currentTime)
function longestFreezeMs(samples) {
    let longest = 0;
    let since = null;
    for (let i = 1; i < samples.length; i++) {
        const a = samples[i - 1];
        const b = samples[i];
        const frozen = a.shownAsset === b.shownAsset && a.shownTime === b.shownTime && !b.fallback;
        if (frozen) {
            since = since === null ? a.t : since;
            longest = Math.max(longest, b.t - since);
        } else {
            since = null;
        }
    }
    return Math.round(longest);
}

async function healthyLesson(browser) {
    const { context, page, errors } = await openPage(browser);
    await startFixtureLesson(page);
    const samples = await record(page, {
        until: s => s.done,
        timeoutMs: 90000,
        shots: {
            'A1-right-explaining': s => s.placement === 'right' && s.state === 'explaining',
            'A2-quiz-question': s => s.state === 'question',
            'A3-quiz-thinking': s => s.state === 'thinking' && s.slide === 3,
            'A4-quiz-success': s => s.state === 'success'
        }
    });
    const states = uniq(samples.map(s => s.state));
    const placements = uniq(samples.map(s => s.placement));
    check('A lesson finished', samples.at(-1).done, `ended on scene ${samples.at(-1).slide}`);
    check('A every behaviour state was reached',
        ['idle', 'talking', 'explaining', 'thinking', 'question', 'success'].every(x => states.includes(x)), states.join(' '));
    check('A every placement was shown',
        ['left', 'right', 'center', 'popup_bottom_left', 'hidden'].every(x => placements.includes(x)), placements.join(' '));
    check('A [SYNC] scene switched talking -> explaining',
        samples.some(s => s.slide === 1 && s.state === 'talking') && samples.some(s => s.slide === 1 && s.state === 'explaining'));
    check('A [PAUSE] beat showed thinking', samples.some(s => s.slide === 1 && s.state === 'thinking' && s.audio === 'PAUSE'));
    check('A mascot_state "explaining" scene narrated as explaining',
        samples.some(s => s.slide === 2 && s.state === 'explaining') && !samples.some(s => s.slide === 2 && s.state === 'talking'));
    check('A quiz went question -> thinking -> success', (() => {
        const quiz = samples.filter(s => s.slide === 3).map(s => s.state);
        const q = quiz.indexOf('question');
        const th = quiz.indexOf('thinking', q);
        const su = quiz.indexOf('success', th);
        return q >= 0 && th > q && su > th;
    })());
    check('A exactly one clip on screen in every sample', samples.every(s => s.visible.length === 1));
    // Two clips may play only while one fades into the other: within 1.5 s (0.6 s fade plus sampling
    // slack) of a placement change. Checked directly rather than as a share of samples, which varies
    // with how often the lesson changes placement.
    let lastChange = -Infinity;
    const outsideFade = samples.filter((s, i) => {
        if (i > 0 && s.placement !== samples[i - 1].placement) lastChange = s.t;
        return s.playing.length === 2 && s.t - lastChange > 1500;
    });
    const single = samples.filter(s => s.playing.length === 1).length / samples.length;
    check('A never more than two clips playing, and two only during a crossfade',
        samples.every(s => s.playing.length >= 1 && s.playing.length <= 2) && outsideFade.length === 0,
        `${Math.round(single * 100)}% of samples had one playing; ${outsideFade.length} with two outside a crossfade`);
    const freeze = longestFreezeMs(samples);
    check('A on-screen clip never froze (>1.2s on one frame)', freeze <= 1200, `longest freeze ${freeze}ms`);
    check('A no fallback needed with healthy clips', samples.every(s => !s.fallback));
    check('A debug HUD reports the state', /State: [A-Z]+/.test(samples.at(-1).hud));
    check('A no page errors', errors.length === 0, errors.join(' | '));
    await context.close();
    return samples.length;
}

async function brokenClip(browser) {
    const { context, page, errors } = await openPage(browser, { blockClip: 'aadhi_right.mp4' });
    await startFixtureLesson(page);
    const samples = await record(page, {
        until: s => s.done,
        timeoutMs: 90000,
        shots: { 'B1-fallback-right': s => s.placement === 'right' && s.fallbackOpacity > 0.99 && s.cue === 'talking' }
    });
    const onRight = samples.filter(s => s.placement === 'right');
    const fallbackShown = onRight.filter(s => s.fallback && s.fallbackOpacity > 0.99);
    const transforms = uniq(fallbackShown.map(s => s.frameTransform));
    check('B lesson still finished with a broken clip', samples.at(-1).done);
    check('B broken clip showed the fallback', fallbackShown.length > 0, onRight.map(s => s.fallback).filter(Boolean)[0] || 'no fallback');
    check('B fallback is animated (transform keeps changing)', transforms.length >= 3, `${transforms.length} distinct transforms`);
    check('B talking indicator while narrating over the fallback',
        fallbackShown.some(s => s.cue === 'talking' && s.audio === 'SPEAKING'));
    check('B healthy clips after it play normally again',
        samples.filter(s => s.slide === 2).some(s => !s.fallback && s.shownAsset === 'center'));
    check('B no page errors', errors.length === 0, errors.join(' | '));
    await context.close();
}

async function autoplayGate(browser) {
    const { context, page, errors } = await openPage(browser, { strictAutoplay: true });
    await startFixtureLesson(page); // no trusted click yet
    const before = await record(page, { until: s => s.gate, timeoutMs: 15000 });
    const blocked = before.at(-1);
    check('C blocked narration shows the Enable playback button', blocked.gate);
    check('C Aadhi keeps moving while waiting for the click', blocked.playing.length === 1 && !blocked.fallback,
        `playing ${blocked.playing.join(',')}, fallback ${blocked.fallback}`);
    if (blocked.gate) {
        await page.screenshot({ path: path.join(OUT, 'C1-enable-playback.png') });
        await page.click('.playback-gate');
        const after = await record(page, { until: s => s.audio === 'SPEAKING', timeoutMs: 8000 });
        const last = after.at(-1);
        check('C one click starts narration and Aadhi talks', last.audio === 'SPEAKING' && !last.gate,
            `state ${last.state}, audio ${last.audio}`);
    }
    check('C no page errors', errors.length === 0, errors.join(' | '));
    await context.close();
}

async function demoLesson(browser) {
    const { context, page, errors } = await openPage(browser);
    await page.click('#demo-btn');
    await page.waitForTimeout(300);
    await page.evaluate(() => skipIntroSequence());
    const samples = await record(page, { until: s => s.slide >= 3, timeoutMs: 60000 });
    const states = uniq(samples.map(s => s.state));
    check('D demo lesson (no aadhi_position) reaches scene 3', samples.at(-1).slide >= 3, `scene ${samples.at(-1).slide}`);
    check('D demo lesson: Aadhi placed by the old rules and narrating',
        samples.some(s => s.placement === 'left') && states.includes('talking'), states.join(' '));
    check('D exactly one clip on screen, no fallback', samples.every(s => s.visible.length === 1 && !s.fallback));
    check('D no page errors', errors.length === 0, errors.join(' | '));
    await context.close();
}

fs.mkdirSync(OUT, { recursive: true });
const { chromium } = await loadPlaywright();
const browser = await launch(chromium, 'no-user-gesture-required');
try {
    await healthyLesson(browser);
    await brokenClip(browser);
    await demoLesson(browser);
    await autoplayGate(browser);
} finally {
    await browser.close();
}

for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.detail ? '  (' + r.detail + ')' : ''}`);
const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Screenshots: ${OUT}`);
process.exit(failed ? 1 : 0);
