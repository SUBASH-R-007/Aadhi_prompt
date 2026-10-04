'use strict';
// Unit tests for the render worker's pure parts (render_worker.mjs, Phase 22): the frame clock, the scene ranges, the
// frame-set requests and manifests, the merged timeline and the encoder settings. No browser or ffmpeg is started.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const load = () => import(pathToFileURL(path.join(__dirname, '..', 'render_worker.mjs')).href);

test('the frame clock moves in whole milliseconds and never drifts', async () => {
    const { at } = await load();
    assert.deepEqual([0, 1, 2, 3].map(n => at(n)), [0, 33, 67, 100]);
    const steps = Array.from({ length: 1800 }, (_, n) => at(n + 1) - at(n));
    assert.ok(steps.every(s => s === 33 || s === 34));
    assert.equal(steps.reduce((a, b) => a + b, 0), 60000); // 60 s of video is exactly 60 000 virtual ms
    assert.equal(at(30 * 6000), 6000000); // and 100 minutes exactly 6 000 000
});

test('scene ranges are contiguous, skip hidden scenes, and the first starts at the lesson start', async () => {
    const { planRanges } = await load();
    const scenes = [0, 1, 2, 3, 4, 5, 6].map(i => ({ index: i, hidden: i === 2, seamSafe: true }));
    assert.deepEqual(planRanges(scenes, 3), [
        { index: 0, fromScene: 0, toScene: 1 }, { index: 1, fromScene: 3, toScene: 4 }, { index: 2, fromScene: 5, toScene: null }]);
    assert.deepEqual(planRanges(scenes.slice(0, 2), 3), [{ index: 0, fromScene: 0, toScene: 0 }, { index: 1, fromScene: 1, toScene: null }]);
    // a hidden first scene: the first range still starts at 0 (the intro; the page skips the hidden scene itself)
    assert.deepEqual(planRanges([{ index: 0, hidden: true }, { index: 1, seamSafe: true }, { index: 2, seamSafe: true }], 2),
        [{ index: 0, fromScene: 0, toScene: 1 }, { index: 1, fromScene: 2, toScene: null }]);
    assert.deepEqual(planRanges([], 3), [{ index: 0, fromScene: 0, toScene: null }]);
    // with the page's expected seconds per scene the ranges are about as long as each other (the intro counts with scene 0)
    const timed = [5, 5, 5].map((estimate, index) => ({ index, estimate, seamSafe: true }));
    assert.deepEqual(planRanges(timed, 2), [{ index: 0, fromScene: 0, toScene: 0 }, { index: 1, fromScene: 1, toScene: null }]);
    const long = [10, 60, 10, 10, 10, 60, 10].map((estimate, index) => ({ index, estimate, seamSafe: true }));
    assert.deepEqual(planRanges(long, 3).map(r => [r.fromScene, r.toScene]), [[0, 1], [2, 4], [5, null]]);
    // every range keeps at least one scene, however uneven the estimates
    assert.deepEqual(planRanges([1, 1, 500].map((estimate, index) => ({ index, estimate, seamSafe: true })), 3).map(r => [r.fromScene, r.toScene]),
        [[0, 0], [1, 1], [2, null]]);
    assert.deepEqual(planRanges(scenes, 1), [{ index: 0, fromScene: 0, toScene: null }]);
});

test('a range only begins where no looping clip runs across the seam; no safe seam means one page', async () => {
    const { planSeams } = await load();
    // the page marks scenes 3 and 5 seam-safe (its mascot clip does not loop across those boundaries)
    const scenes = [10, 10, 10, 10, 10, 10, 10].map((estimate, index) => ({ index, estimate, seamSafe: index === 3 || index === 5 }));
    const plan = planSeams(scenes, 3);
    assert.deepEqual(plan.ranges.map(r => [r.fromScene, r.toScene]), [[0, 2], [3, 4], [5, null]]);
    assert.deepEqual(plan.seams.safe, [3, 5]);
    assert.deepEqual(plan.seams.chosen, [3, 5]);
    assert.match(plan.seams.reason, /2 of 2 safe seams, chosen to even out the pages by expected seconds/);
    // balanced among the safe seams: two pages pick the safe seam nearest the middle
    const two = planSeams([20, 20, 20, 20, 20, 20].map((estimate, index) => ({ index, estimate, seamSafe: index !== 3 })), 2);
    assert.deepEqual(two.ranges.map(r => [r.fromScene, r.toScene]), [[0, 1], [2, null]]); // 14.5 s of intro tips it to scene 2
    // nothing safe (or the page does not say): the whole lesson in one page, and the log says why
    for (const flags of [undefined, false]) {
        const none = planSeams([0, 1, 2, 3].map(index => ({ index, estimate: 10, seamSafe: flags })), 3);
        assert.deepEqual(none.ranges, [{ index: 0, fromScene: 0, toScene: null }]);
        assert.match(none.seams.reason, /no scene boundary is free of a looping clip: one page/);
    }
    // a seam-safe flag on the first played scene is no seam
    assert.equal(planSeams([{ index: 0, seamSafe: true }, { index: 1 }], 3).ranges.length, 1);
});

test('only frame-set files are served from disk', async () => {
    const { frameRequest } = await load();
    assert.deepEqual(frameRequest('http://127.0.0.1:9720/__render_frames/0123456789abcdef/000001.jpg'), { setId: '0123456789abcdef', file: '000001.jpg' });
    assert.deepEqual(frameRequest('http://127.0.0.1:9720/__render_frames/m-aadhi_left/manifest.json'), { setId: 'm-aadhi_left', file: 'manifest.json' });
    for (const bad of ['http://x/__render_frames/../server.py', 'http://x/__render_frames/0123456789abcdef/../../a.jpg',
        'http://x/__render_frames/0123456789abcdef/1.jpg', 'http://x/__render_frames/zz/000001.jpg', 'http://x/__render_frames/0123456789abcdef/000001.exe',
        'http://x/static/000001.jpg', 'not a url']) {
        assert.equal(frameRequest(bad), null, bad);
    }
});

test('manifests and frame rates are checked', async () => {
    const { validManifest, parseRate } = await load();
    const good = { version: 1, fps: 24, frames: 192, format: 'jpeg', pattern: '%06d.jpg' };
    assert.ok(validManifest(good));
    assert.ok(validManifest({ ...good, format: 'png', pattern: '%06d.png', alpha: true }));
    for (const bad of [null, { ...good, version: 2 }, { ...good, frames: 0 }, { ...good, format: 'gif' }, { ...good, pattern: '../%06d.jpg' }, { ...good, fps: -1 }]) {
        assert.equal(validManifest(bad), false, JSON.stringify(bad));
    }
    assert.deepEqual(parseRate('24000/1001'), { value: 24000 / 1001, text: '24000/1001' });
    assert.deepEqual(parseRate('30'), { value: 30, text: '30/1' });
    assert.equal(parseRate('0/0'), null);
});

test('range timelines are merged onto the video clock', async () => {
    const { mergeTimelines } = await load();
    const merged = mergeTimelines([
        { index: 0, fromScene: 0, toScene: 1, frames: 90, timeline: {
            scenes: [{ t: 0, title: 'Intro' }, { t: 2, title: 'Stress', index: 1 }, { t: 2.98, title: 'Strain', index: 2 }], // the next range's first scene
            cues: [{ start: 2.1, end: 2.9, text: 'Stress is…' }],
            audio: [{ t: 0, kind: 'logo', src: '/video_template/logo_animation.mp4', offset: 0, rate: 1 },
                { t: 2.99, kind: 'narration', src: '/static/late.wav', offset: 0, rate: 0.9 }], // began with the next range: dropped here
            sync: [{ t: 2.5, id: 'reveal-1' }] } },
        { index: 1, fromScene: 2, toScene: null, frames: 60, timeline: {
            scenes: [{ t: 0, title: 'Strain', index: 2 }], // counted from its first captured frame; the range before logged 2.98
            cues: [{ start: 0, end: 0.4, text: 'Stress is…' }, { start: 0.5, end: 1.2, text: 'Strain is…' }],
            audio: [{ t: 0, kind: 'narration', src: '/static/a.wav', offset: 0.4, rate: 0.9 }, // clipped by the page at the capture start
                { t: 0, kind: 'narration', src: '/static/late.wav', offset: 0, rate: 0.9 }, { t: 1, kind: 'sfx', synth: { freq: 880, duration: 0.15 } }],
            notes: [{ t: 0.2, scene: 2, text: 'no visual' }] } }]);
    assert.equal(merged.frames, 150);
    assert.equal(merged.duration, 5);
    assert.deepEqual(merged.ranges.map(r => r.offset), [0, 3]);
    // Strain once, at the moment it began (as a one-page render logs it), not at the next range's first frame
    assert.deepEqual(merged.scenes.map(s => [s.t, s.title]), [[0, 'Intro'], [2, 'Stress'], [2.98, 'Strain']]);
    assert.deepEqual(merged.cues.map(c => [c.start, c.end]), [[2.1, 2.9], [3, 3.4], [3.5, 4.2]]);
    assert.deepEqual(merged.audio.map(a => [a.t, a.src || 'synth']), [[0, '/video_template/logo_animation.mp4'], [3, '/static/late.wav'], [4, 'synth']]);
    assert.deepEqual(merged.notes, [{ t: 3.2, scene: 2, text: 'no visual' }]);
    assert.deepEqual(merged.sync, [{ t: 2.5, id: 'reveal-1' }]); // lists the page adds are kept and shifted too
});

test('every range is encoded the same way: constant 30 fps H.264, limited-range BT.709, no sound', async () => {
    const { encoderArgs } = await load();
    const args = encoderArgs('range-0.part.mp4');
    const after = flag => args[args.indexOf(flag) + 1];
    assert.equal(after('-f'), 'image2pipe');
    assert.equal(after('-framerate'), '30');
    assert.equal(after('-i'), 'pipe:0');
    assert.equal(after('-c:v'), 'mjpeg');
    assert.ok(args.includes('libx264') && args.includes('-an'));
    assert.equal(after('-crf'), '18');
    assert.equal(after('-pix_fmt'), 'yuv420p');
    assert.equal(after('-r'), '30');
    assert.equal(after('-fps_mode'), 'cfr');
    assert.match(after('-vf'), /out_range=tv/);
    assert.equal(after('-colorspace'), 'bt709');
    assert.equal(args.at(-1), 'range-0.part.mp4');
});

test('a job is checked before anything starts; the token never needs argv', async () => {
    const { checkJob } = await load();
    const job = { base: 'http://127.0.0.1:9720', projectId: 4, token: 'x'.repeat(40), workspace: 'C:/w', renderDir: 'C:/r', ffmpeg: 'ffmpeg',
        ffprobe: 'ffprobe', missingVisuals: 'refuse' };
    assert.deepEqual(checkJob(job), []);
    assert.deepEqual(checkJob({ ...job, base: 'https://example.com' }), ['base']); // only this machine's server
    assert.deepEqual(checkJob({ ...job, missingVisuals: 'record anyway', token: '' }), ['token', 'missingVisuals']);
    assert.deepEqual(checkJob(null), ['the job is not an object']);
});

test('frame numbers in the merged timeline are on the video clock too', async () => {
    const { mergeTimelines } = await load();
    const merged = mergeTimelines([
        { index: 0, fromScene: 0, toScene: 0, frames: 570, timeline: { sync: [{ t: 10, frame: 300, id: 'a' }], scenes: [{ t: 0, title: 'A', index: 0 }] } },
        { index: 1, fromScene: 1, toScene: null, frames: 300, timeline: {
            sync: [{ t: 4.9, frame: 147, id: 'b' }], notes: [{ t: 1, scene: 1, text: 'n' }], cues: [{ start: 2, end: 3, text: 'c' }],
            scenes: [{ t: 0, title: 'B', index: 1 }] } }]);
    // range 1 starts 19 s into the video: its 4.9 s mark is 23.9 s, frame 717 (not its own frame 147)
    assert.deepEqual(merged.sync.map(s => [s.id, s.t, s.frame]), [['a', 10, 300], ['b', 23.9, 717]]);
    assert.deepEqual(merged.notes.map(n => n.t), [20]);
    assert.deepEqual(merged.cues.map(c => [c.start, c.end]), [[21, 22]]);
    assert.deepEqual(merged.scenes.map(s => s.t), [0, 19]);
});

test('the join stamps every frame at exactly n/30 s, also after a seam whose segment is a few ticks short', async t => {
    const { joinArgs, onGrid, encoderArgs } = await load();
    const { spawnSync } = require('node:child_process');
    const fs = require('node:fs');
    const os = require('node:os');
    assert.ok(joinArgs('list.txt', 'out.mp4').includes('setts=time_base=1/15360:pts=N*512:dts=N*512:duration=512'));
    assert.ok(encoderArgs('x.mp4').includes('-bf') && encoderArgs('x.mp4').includes('15360'));
    assert.ok(onGrid('0\n512\n1024\n'));
    assert.ok(!onGrid('0\n512\n1014\n'));
    if (spawnSync('ffmpeg', ['-version']).status !== 0) return t.skip('needs ffmpeg');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aadhi-join-'));
    try {
        // two ranges encoded as the worker does (JPEG frames on a pipe), the first made 10 ticks short like the one a real
        // render showed (frame 934 at 31.13268 s instead of 31.13333 s)
        for (const [name, frames] of [['a', 34], ['b', 20]]) {
            const jpegs = spawnSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=30', '-frames:v', String(frames),
                '-f', 'image2pipe', '-c:v', 'mjpeg', '-'], { maxBuffer: 64 * 1024 * 1024 }).stdout;
            const enc = spawnSync('ffmpeg', encoderArgs(path.join(dir, `${name}.mp4`)), { input: jpegs });
            assert.equal(enc.status, 0, String(enc.stderr));
        }
        assert.equal(spawnSync('ffmpeg', ['-v', 'error', '-y', '-i', path.join(dir, 'a.mp4'), '-c', 'copy', '-bsf:v',
            'setts=duration=if(eq(N\\,33)\\,502\\,DURATION)', '-video_track_timescale', '15360', path.join(dir, 'short.mp4')]).status, 0);
        fs.writeFileSync(path.join(dir, 'list.txt'), "file 'short.mp4'\nfile 'b.mp4'\n");
        const pts = file => spawnSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'packet=pts', '-of', 'csv=p=0', file],
            { encoding: 'utf8' }).stdout;
        const plain = joinArgs(path.join(dir, 'list.txt'), path.join(dir, 'plain.mp4'), { restamp: false });
        assert.equal(spawnSync('ffmpeg', plain).status, 0);
        assert.equal(onGrid(pts(path.join(dir, 'plain.mp4'))), false); // the problem: the frames after the seam are 10 ticks early
        assert.equal(spawnSync('ffmpeg', joinArgs(path.join(dir, 'list.txt'), path.join(dir, 'joined.mp4'))).status, 0);
        const fixed = pts(path.join(dir, 'joined.mp4'));
        assert.ok(onGrid(fixed));
        assert.equal(fixed.trim().split(/\r?\n/).length, 54);
        const rate = spawnSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=avg_frame_rate', '-of', 'csv=p=0',
            path.join(dir, 'joined.mp4')], { encoding: 'utf8' }).stdout.trim();
        assert.equal(rate, '30/1');
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
});

test('a long step reports every few seconds, and a step that stops moving fails instead of hanging', async () => {
    const { keepalive, prepareMessage } = await load();
    const lines = [];
    const write = process.stderr.write;
    process.stderr.write = chunk => { lines.push(String(chunk)); return true; };
    try {
        const slow = new Promise(resolve => setTimeout(() => resolve('done'), 200));
        assert.equal(await keepalive(slow, () => ({ event: { type: 'progress', message: 'Preparing the lesson…' } }), 10), 'done');
        assert.ok(lines.filter(l => l.includes('Preparing the lesson')).length >= 2);
        const never = new Promise(() => {});
        await assert.rejects(keepalive(never, () => ({ fail: new Error('stuck') }), 10), /stuck/);
    } finally {
        process.stderr.write = write;
    }
    assert.equal(prepareMessage({ phase: 'preparing', done: 12, total: 140, label: 'Narration', changes: 3 }), 'Preparing the lesson: 12 of 140 ready (narration)…');
    assert.equal(prepareMessage({ phase: 'preparing', done: 0, total: 0, label: 'Opening the lesson' }), 'Preparing the lesson: opening the lesson…');
    assert.equal(prepareMessage(null), 'Preparing the lesson…');
});

test('a render page never saves, uploads or generates; narration, plans and the quality check stay allowed', async () => {
    const { deniedInRender, clipDemuxer, frameSetBytes } = await load();
    for (const p of ['/render', '/regenerate-manim', '/generate-ai-video', '/generate-ai-image', '/save-history', '/api/editor/7',
        '/api/presenters/generate', '/api/cinematic/background', '/api/cinematic/direction/regenerate', '/api/visuals/review',
        '/api/studio/lessons/7/checkpoint', '/api/ai-media/lessons/7/generate', '/api/exports', '/upload-media', '/start-ai-server']) {
        assert.equal(deniedInRender('POST', p), true, p);
    }
    for (const p of ['/generate-audio', '/api/presenters/speech', '/api/visuals/plan', '/api/cinematic/plan', '/api/quality/lesson',
        '/render_mode.js', '/renderer']) {
        assert.equal(deniedInRender('POST', p), false, p);
    }
    assert.equal(deniedInRender('GET', '/render'), false); // reading is never refused
    assert.equal(clipDemuxer('mov,mp4,m4a,3gp,3g2,mj2'), 'mov');
    assert.equal(clipDemuxer('matroska,webm'), 'matroska');
    assert.equal(clipDemuxer('hls'), null);
    assert.equal(clipDemuxer('concat'), null);
    assert.ok(frameSetBytes({ duration: 8, rate: { value: 24 }, width: 1280, height: 720, alpha: false }) < 40 * 1024 ** 2);
});

test('a clip that cannot be loaded is skipped with a note: the page shows it from its own element', async () => {
    const { frameSets, settings } = await load();
    const http = require('node:http');
    const fs = require('node:fs');
    const os = require('node:os');
    const server = http.createServer((req, res) => { res.writeHead(404); res.end(); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aadhi-sets-'));
    const write = process.stderr.write;
    const lines = [];
    process.stderr.write = chunk => { lines.push(String(chunk)); return true; };
    try {
        const job = { base, token: 'x'.repeat(40), workspace: dir, renderDir: dir, ffmpeg: 'ffmpeg', ffprobe: 'ffprobe', missingVisuals: 'refuse', projectId: 1 };
        const notes = [];
        const { sets, map } = await frameSets(job, settings(job), [{ key: 'gone', kind: 'video', src: `${base}/static/gone.mp4`, loop: false }], notes);
        assert.deepEqual([sets, map], [{}, {}]);
        assert.equal(notes.length, 1);
        assert.match(notes[0].text, /The clip gone could not be prepared frame by frame; it is shown from the video itself\./);
        assert.ok(lines.some(l => l.includes('"type":"note"')));
    } finally {
        process.stderr.write = write;
        server.close();
        fs.rmSync(dir, { recursive: true, force: true });
    }
});

test('when the page stops a frame, its own words are the reason the render gives', async () => {
    const { pageStopped } = await load();
    const said = pageStopped(new Error('page.evaluate: Error: The render stopped: a clip did not finish within 120 seconds (aadhi_left.mp4).\n    at x'),
        'rendering a frame');
    assert.equal(said.message, 'The render stopped: a clip did not finish within 120 seconds (aadhi_left.mp4).');
    assert.equal(said.code, 'page_error');
    const other = pageStopped(new Error('Target closed'), 'rendering a frame');
    assert.equal(other.message, 'The lesson page failed while rendering a frame.');
});

test('whole-lesson pre-roll: every boundary can be a seam, and the split evens out the pages\' wall time', async () => {
    const { planPages, rangeFields } = await load();
    const rates = { captureFps: 10, prerollFps: 50, alpha: 0, overhead: 0 }; // no slowdown from sharing, for clear numbers
    const scenes = Array.from({ length: 12 }, (_, index) => ({ index, estimate: 30, seamSafe: false })); // no safe seam needed
    const plan = planPages(scenes, 3, rates);
    assert.equal(plan.ranges.length, 3);
    assert.equal(plan.ranges[0].fromScene, 0);
    assert.equal(plan.ranges.at(-1).toScene, null);
    plan.ranges.slice(1).forEach((r, i) => assert.equal(r.fromScene, plan.ranges[i].toScene + 1)); // contiguous
    const pages = plan.seams.pages;
    // later pages pre-roll more, so they capture less
    assert.ok(pages[0].captureSeconds > pages[1].captureSeconds && pages[1].captureSeconds > pages[2].captureSeconds);
    assert.deepEqual(pages.map(p => p.prerollSeconds), [0, pages[0].captureSeconds, pages[0].captureSeconds + pages[1].captureSeconds]);
    const worst = Math.max(...pages.map(p => p.predicted));
    assert.equal(plan.seams.predictedSeconds, worst);
    assert.ok(worst < (374.5 * 30) / 10 * 0.5); // well under half of one page's 1123 s
    assert.equal(plan.seams.mode, 'lesson');
    assert.match(plan.seams.reason, /3 pages, split for the shortest wall time/);
    // when pages slow each other down a lot, or a lesson is short, fewer pages are chosen
    const slow = planPages(scenes, 3, { captureFps: 10, prerollFps: 50, alpha: 1, overhead: 0 });
    assert.equal(slow.ranges.length, 1);
    assert.match(slow.seams.reason, /one page is fastest/);
    const short = planPages([{ index: 0, estimate: 5 }, { index: 1, estimate: 5 }], 3, { ...rates, alpha: 0.65, overhead: 60 });
    assert.equal(short.ranges.length, 1);
    // hidden scenes are never a range's first scene
    const hidden = planPages(scenes.map(s => ({ ...s, hidden: s.index % 2 === 1 })), 3, rates);
    assert.ok(hidden.ranges.every(r => r.fromScene % 2 === 0));
    // the fields a range page gets
    assert.deepEqual(rangeFields({ index: 0, fromScene: 0, toScene: 4 }, 'lesson'), { fromScene: 0, captureFromScene: 0, toScene: 4 });
    assert.deepEqual(rangeFields({ index: 0, fromScene: 0, toScene: 4 }, 'scene'), { fromScene: 0, toScene: 4, preroll: false });
    assert.deepEqual(rangeFields({ index: 1, fromScene: 5, toScene: null }, 'lesson'), { fromScene: 0, captureFromScene: 5, toScene: null });
    assert.deepEqual(rangeFields({ index: 1, fromScene: 5, toScene: null }, 'scene'), { fromScene: 5, toScene: null, preroll: true });
});

test('the rates a plan uses are measured on earlier renders', async () => {
    const { readRates, updateRates, DEFAULT_RATES } = await load();
    const fs = require('node:fs');
    const os = require('node:os');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aadhi-rates-'));
    try {
        assert.deepEqual(readRates(dir), DEFAULT_RATES);
        // two pages measured 6 fps capture and 30 fps pre-roll each: as one page alone that is 6 / 2^-0.65 = 9.4 fps, etc.
        const next = updateRates(dir, { captureFps: 6, prerollFps: 30, overhead: 10 }, 2);
        assert.equal(next.captureFps, Math.round((DEFAULT_RATES.captureFps * 0.6 + (6 / Math.pow(2, -0.65)) * 0.4) * 100) / 100);
        assert.equal(readRates(dir).prerollFps, next.prerollFps);
        assert.equal(readRates(dir).overhead, Math.round(DEFAULT_RATES.overhead * 0.6 + 10 * 0.4));
        fs.writeFileSync(path.join(dir, 'rates.json'), '{"captureFps": 7, "alpha": 0.4}');
        assert.deepEqual(readRates(dir), { ...DEFAULT_RATES, captureFps: 7, alpha: 0.4 }); // an alpha set by hand is used and kept
        assert.equal(updateRates(dir, {}, 1).alpha, 0.4);
        fs.writeFileSync(path.join(dir, 'rates.json'), '{"captureFps": -1, "prerollFps": "x", "alpha": 9}');
        assert.deepEqual(readRates(dir), DEFAULT_RATES); // nonsense is never used
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
});

test('rates are kept per layout: a Cinematic Studio lesson plans with its own, slower rates', async () => {
    const { readRates, updateRates, planPages, LAYOUT_RATES, layoutOf, rateWarning } = await load();
    const fs = require('node:fs');
    const os = require('node:os');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aadhi-layout-rates-'));
    try {
        const cine = { kind: 'cinematic', key: 'cinematic:corporate_training' };
        assert.deepEqual(readRates(dir, cine), LAYOUT_RATES.cinematic); // until measured: the cinematic defaults
        assert.ok(LAYOUT_RATES.cinematic.prerollFps < LAYOUT_RATES.classic.prerollFps / 3);
        // a measured cinematic render teaches its family and its kind, never Classic
        updateRates(dir, { captureFps: 2.5, prerollFps: 9.8, overhead: 8 }, 3, 0.65, cine);
        const saved = JSON.parse(fs.readFileSync(path.join(dir, 'rates.json'), 'utf8'));
        assert.deepEqual(Object.keys(saved).sort(), ['cinematic', 'cinematic:corporate_training']);
        assert.deepEqual(readRates(dir), LAYOUT_RATES.classic);
        assert.notDeepEqual(readRates(dir, { kind: 'cinematic', key: 'cinematic:academic' }), LAYOUT_RATES.cinematic); // the kind learned
        // a file from before per-layout rates is Classic's
        fs.writeFileSync(path.join(dir, 'rates.json'), '{"captureFps": 7}');
        assert.equal(readRates(dir).captureFps, 7);
        assert.deepEqual(readRates(dir, cine), LAYOUT_RATES.cinematic);
        assert.deepEqual(layoutOf({ kind: 'cinematic', key: 'classic:../x' }), { kind: 'cinematic', key: 'cinematic' });
        assert.deepEqual(layoutOf(null), { kind: 'classic', key: 'classic' });
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
    // T's real Cinematic lesson (6805 frames) and a 100-minute one: one page, where pre-rolling would cost more than a page saves;
    // the same lessons in Classic still split
    const lesson = (seconds, n) => Array.from({ length: n }, (_, index) => ({ index, estimate: (seconds - 14.5) / n }));
    for (const [seconds, n, classicPages] of [[6805 / 30, 20, 2], [6000, 200, 3]]) {
        assert.equal(planPages(lesson(seconds, n), 3, LAYOUT_RATES.cinematic).ranges.length, 1, `${seconds} s cinematic`);
        assert.equal(planPages(lesson(seconds, n), 3, LAYOUT_RATES.classic).ranges.length, classicPages, `${seconds} s classic`);
    }
    // a page far off its plan is reported
    assert.equal(rateWarning('pre-roll', 10, 50).message, 'The pre-roll runs 5 times slower than planned.');
    assert.equal(rateWarning('capture', 6, 5), null);
});
