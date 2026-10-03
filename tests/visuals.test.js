'use strict';
// Unit tests for the visual router client (visuals.js). Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const V = require('../visuals.js');

function memoryStorage(initial = {}) {
    const data = { ...initial };
    return { getItem: k => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
}

const aiImage = { source: 'AI_IMAGE', requires_generation: true, provider: 'pollinations' };
const aiVideo = { source: 'AI_VIDEO', requires_generation: true, provider: 'veo' };

test('the AI visuals setting defaults to images and survives broken storage', () => {
    assert.equal(V.getMode(memoryStorage()), 'images');
    assert.equal(V.getMode(memoryStorage({ aadhi_ai_visuals: 'all' })), 'all');
    assert.equal(V.getMode(memoryStorage({ aadhi_ai_visuals: 'bogus' })), 'images');
    assert.equal(V.getMode({ getItem() { throw new Error('private mode'); } }), 'images');
    assert.equal(V.getMode(null), 'images');

    const storage = memoryStorage();
    V.setMode(storage, 'off');
    assert.equal(V.getMode(storage), 'off');
    V.setMode(storage, 'nonsense'); // ignored
    assert.equal(V.getMode(storage), 'off');
    assert.doesNotThrow(() => V.setMode({ setItem() { throw new Error('quota'); } }, 'all'));
});

test('plan options follow the setting', () => {
    assert.deepEqual(V.planOptions('images'), { allow_ai_generation: true, prefer_existing_assets: true, prefer_procedural: true });
    assert.deepEqual(V.planOptions('all'), { allow_ai_generation: true, prefer_existing_assets: true, prefer_procedural: true });
    assert.equal(V.planOptions('off').allow_ai_generation, false);
});

test('only allowed AI media is generated without asking', () => {
    const matrix = [
        // plan, mode, expected
        [aiImage, 'images', true],
        [aiImage, 'all', true],
        [aiImage, 'off', false],
        [aiVideo, 'images', false], // AI videos wait for the user in the default mode
        [aiVideo, 'all', true],
        [aiVideo, 'off', false],
        [{ source: 'ASSET', asset_id: 'a'.repeat(32), url: '/api/assets/x/content' }, 'all', false],
        [{ source: 'MANIM', renderer: 'manim' }, 'all', false],
        [{ source: 'NONE', would_require: 'AI_VIDEO' }, 'all', false],
        [{ source: 'AI_VIDEO', requires_generation: false, url: '/static/done.mp4' }, 'all', false], // already generated
        [null, 'all', false],
        [undefined, 'images', false]
    ];
    matrix.forEach(([plan, mode, expected]) => {
        assert.equal(V.shouldAutoGenerate(plan, mode), expected, `${JSON.stringify(plan)} in ${mode}`);
    });
});

test('plans are stored in their scenes, compactly, and replace older ones', () => {
    const slides = [
        { type: 'ai_video', visual_plan: { main: { source: 'AI_VIDEO' } } },
        { type: 'content', visual_plan: { side: { source: 'ASSET' } } },
        { type: 'quiz_checkpoint' },
        null
    ];
    const plans = [
        { slot: 'main', scene_index: 0, source: 'ASSET', media: 'VIDEO', selection: 'matched', asset_id: 'a'.repeat(32),
          url: '/api/assets/a/content?token=t', score: 0.81, reason: 'matched', debug: ['explicit asset: none'] },
        { slot: 'side', scene_index: 0, source: 'PROCEDURAL', media: 'ANIMATION', renderer: 'chart', reason: 'chart' },
        { slot: 'html:' + 'b'.repeat(32), scene_index: 0, source: 'ASSET', asset_id: 'b'.repeat(32) }, // checked, not stored
        { slot: 'side', scene_index: 2, source: 'NONE', media: 'NONE', selection: 'none', reason: 'nothing needed' }
    ];
    assert.equal(V.applyPlans(slides, plans), plans);
    assert.deepEqual(slides[0].visual_plan, {
        main: { source: 'ASSET', media: 'VIDEO', selection: 'matched', asset_id: 'a'.repeat(32), url: '/api/assets/a/content?token=t', score: 0.81, reason: 'matched' },
        side: { source: 'PROCEDURAL', media: 'ANIMATION', renderer: 'chart', reason: 'chart' }
    });
    assert.equal('visual_plan' in slides[1], false); // no plan any more: the old one is removed
    assert.equal(slides[2].visual_plan.side.source, 'NONE');
    assert.deepEqual(V.applyPlans(slides, undefined), []);
    assert.equal('visual_plan' in slides[0], false);
});

test('source labels are short and say what is missing', () => {
    assert.equal(V.sourceLabel({ source: 'ASSET' }), '🗂 Library');
    assert.equal(V.sourceLabel({ source: 'SYSTEM_ASSET' }), '🦌 Shared Aadhi asset');
    assert.equal(V.sourceLabel({ source: 'PROCEDURAL', renderer: 'chart' }), '📐 Built-in visual');
    // Phase 21: what it is, not the tool that makes it
    assert.equal(V.sourceLabel({ source: 'MANIM' }), '🧮 Animation');
    assert.equal(V.sourceLabel({ source: 'EXTERNAL_MEDIA' }), '🎞 Animated picture');
    assert.doesNotMatch(Object.values(V.SOURCES).map(s => s.label).join(' '), /manim|\bgif\b/i);
    assert.equal(V.sourceLabel(aiVideo), '✨ AI video (not generated yet)');
    assert.equal(V.sourceLabel({ source: 'AI_VIDEO', url: '/static/x.mp4' }), '✨ AI video');
    assert.equal(V.sourceLabel({ source: 'NONE', error: 'asset_unavailable' }), '— No visual — unavailable');
    assert.equal(V.sourceLabel({ source: 'NONE', would_require: 'AI_IMAGE' }), '— AI image needed, but AI visuals are off');
    assert.equal(V.sourceLabel({ source: 'SOMETHING_NEW' }), '— No visual');
    assert.equal(V.sourceLabel(null), '');
});

test('debug rows and the summary describe every plan', () => {
    const plans = [
        { slot: 'main', scene_index: 0, source: 'ASSET', selection: 'matched', asset_id: 'abcdef0123456789'.repeat(2), score: 0.7,
          reason: 'matched library asset', debug: ['explicit asset: none', 'library match: clip.mp4 scored 0.70'] },
        { slot: 'side', scene_index: 1, source: 'NONE', selection: 'none', error: 'asset_unavailable', reason: 'asset gone' },
        { slot: 'main', scene_index: 1, source: 'AI_VIDEO', selection: 'planned', provider: 'veo', requires_generation: true, reason: 'last resort' }
    ];
    const rows = V.debugRows(plans);
    assert.deepEqual(rows[0], { scene: 1, slot: 'main', source: 'ASSET', selection: 'matched', via: '', asset: 'abcdef01', score: 0.7,
        generate: '', reason: 'matched library asset', trace: 'explicit asset: none | library match: clip.mp4 scored 0.70' });
    assert.equal(rows[1].reason, 'asset_unavailable: asset gone');
    assert.equal(rows[2].via, 'veo');
    assert.equal(rows[2].generate, 'needed');
    assert.deepEqual(V.summarize(plans), { ASSET: 1, NONE: 1, AI_VIDEO: 1 });
    assert.deepEqual(V.debugRows(null), []);
});

test('the API client posts the lesson and the options, and reports errors plainly', async () => {
    const calls = [];
    const ok = new V.VisualApi({
        fetch: async (url, init) => {
            calls.push({ url, init });
            return { ok: true, status: 200, json: async () => ({ plans: [], summary: {} }) };
        }
    });
    const slides = [{ type: 'content' }];
    assert.deepEqual(await ok.plan(slides, { allow_ai_generation: false, project_id: 7 }, true), { plans: [], summary: {} });
    assert.equal(calls[0].url, '/api/visuals/plan');
    assert.equal(calls[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(calls[0].init.body), { scenes: slides, allow_ai_generation: false, project_id: 7, debug: true });

    const denied = new V.VisualApi({ fetch: async () => ({ ok: false, status: 413, json: async () => ({ detail: 'A lesson can have at most 400 scenes.' }) }) });
    await assert.rejects(denied.plan(slides), err => err.status === 413 && err.message === 'A lesson can have at most 400 scenes.');

    const broken = new V.VisualApi({ fetch: async () => ({ ok: false, status: 502, json: async () => { throw new Error('not json'); } }) });
    await assert.rejects(broken.plan(slides), err => err.status === 502 && /error 502/.test(err.message));

    const offline = new V.VisualApi({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
    await assert.rejects(offline.plan(slides), err => err.status === 0 && /could not be reached/.test(err.message));
});

// ---- AI media cache (Phase 5) ---------------------------------------------------------------

function recordingFetch(responses) {
    const calls = [];
    const fetch = async (url, init) => {
        calls.push({ url, init, body: init && init.body ? JSON.parse(init.body) : null });
        const { status = 200, body } = responses.shift();
        return { ok: status < 400, status, json: async () => body };
    };
    return { fetch, calls };
}

test('the generation status says whether media was reused or newly made', () => {
    assert.equal(V.generationStatus({ status: 'success', cache_hit: true, generated: false }, 'video'), '♻ Reused existing video');
    assert.equal(V.generationStatus({ status: 'success', cache_hit: false, generated: true }, 'video'), '✨ Generated new video');
    assert.equal(V.generationStatus({ status: 'success', cache_hit: true }), '♻ Reused existing visual');
    assert.equal(V.generationStatus({ status: 'success', cached: true }, 'video'), '♻ Reused existing video'); // older field name
    assert.equal(V.generationStatus({ status: 'success', video_url: '/static/x.mp4' }, 'video'), ''); // older server: says nothing
    assert.equal(V.generationStatus({ status: 'success', cache_hit: false, generated: false, manual: true }, 'video'), ''); // placed by hand
    assert.equal(V.generationStatus({ status: 'manual_required', filename: 'ai_video_x.mp4' }, 'video'), '');
    assert.equal(V.generationStatus(null), '');
});

test('a plan reused from the AI cache is labelled, stored and never generated again', () => {
    const cached = { slot: 'main', scene_index: 0, source: 'AI_VIDEO', media: 'VIDEO', selection: 'cached', asset_id: 'c'.repeat(32),
        url: '/api/assets/c/content?token=t', provider: 'veo', cache_hit: true, requires_generation: false, reason: 'reused' };
    assert.equal(V.sourceLabel(cached), '✨ AI video (reused)');
    assert.equal(V.sourceLabel({ source: 'AI_IMAGE', cache_hit: true }), '✨ AI image (reused)');
    assert.equal(V.shouldAutoGenerate(cached, 'all'), false);
    const slides = [{ type: 'ai_video' }];
    V.applyPlans(slides, [cached]);
    assert.equal(slides[0].visual_plan.main.cache_hit, true);
    assert.equal(slides[0].visual_plan.main.selection, 'cached');
    // Plans from before the cache still work exactly as before
    V.applyPlans(slides, [{ slot: 'main', scene_index: 0, source: 'ASSET', selection: 'matched', url: '/u' }]);
    assert.equal('cache_hit' in slides[0].visual_plan.main, false);
    assert.equal(V.sourceLabel(slides[0].visual_plan.main), '🗂 Library');
});

test('the generator client asks for a new version only when told to', async () => {
    const { fetch, calls } = recordingFetch([
        { body: { status: 'success', video_url: '/static/a.mp4', asset_id: 'a'.repeat(32), cache_hit: true, generated: false } },
        { body: { status: 'success', video_url: '/static/b.mp4', asset_id: 'b'.repeat(32), cache_hit: false, generated: true } },
        { body: { status: 'success', url: '/api/assets/i/content?token=t', asset_id: 'i'.repeat(32), cache_hit: false, generated: true } },
        { body: { status: 'success', url: '/api/assets/j/content?token=t', asset_id: 'j'.repeat(32), cache_hit: false, generated: true } }
    ]);
    const api = new V.AiMediaApi({ fetch });
    const reused = await api.generateVideo('A bridge at dawn');
    const fresh = await api.generateVideo('A bridge at dawn', { force: true });
    await api.generateImage('A coral reef', { subjectName: 'Biology' });
    await api.generateImage('A coral reef', { force: true });
    assert.deepEqual(calls.map(c => [c.url, c.init.method]), [
        ['/generate-ai-video', 'POST'], ['/generate-ai-video', 'POST'], ['/generate-ai-image', 'POST'], ['/generate-ai-image', 'POST']]);
    assert.deepEqual(calls[0].body, { prompt: 'A bridge at dawn' }); // an ordinary request never bypasses the cache
    assert.deepEqual(calls[1].body, { prompt: 'A bridge at dawn', force_regenerate: true });
    assert.deepEqual(calls[2].body, { prompt: 'A coral reef', subject_name: 'Biology' });
    assert.deepEqual(calls[3].body, { prompt: 'A coral reef', subject_name: '', force_regenerate: true });
    assert.equal(reused.cache_hit, true);
    assert.notEqual(fresh.asset_id, reused.asset_id);
});

test('generator errors are readable, including AI being switched off', async () => {
    const { fetch } = recordingFetch([
        { status: 403, body: { detail: 'AI video generation is turned off on this server.' } },
        { status: 500, body: null }
    ]);
    const api = new V.AiMediaApi({ fetch });
    await assert.rejects(api.generateVideo('x'), err => err.status === 403 && /turned off/.test(err.message));
    await assert.rejects(api.generateImage('x'), err => err.status === 500 && /error 500/.test(err.message));
    const offline = new V.AiMediaApi({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
    await assert.rejects(offline.generateVideo('x'), err => err.status === 0);
});

// ---- Phase 8: background generations, provider provenance and status ----

test('a background video generation is followed through its run to the same answer', async () => {
    const result = { status: 'success', video_url: '/static/v.mp4', asset_id: 'v'.repeat(32), cache_hit: false, generated: true,
        provider: 'fake-alt', model: 'fake-alt-video-1', fallback_from: 'fake', run_id: 'r1' };
    const { fetch, calls } = recordingFetch([
        { status: 202, body: { status: 'queued', run_id: 'r1', cache_hit: false, generated: false } },
        { body: { run_id: 'r1', status: 'running', provider: 'fake' } },
        { status: 500, body: null }, // a moment without the server: keep waiting
        { body: { run_id: 'r1', status: 'completed', result } }
    ]);
    const progress = [];
    const api = new V.AiMediaApi({ fetch, background: true, wait: async () => {} });
    const answer = await api.generateVideo('A glacier', { onProgress: run => progress.push(run.status) });
    assert.deepEqual(answer, result);
    assert.deepEqual(calls[0].body, { prompt: 'A glacier', wait: false });
    assert.deepEqual(calls.slice(1).map(c => [c.url, c.init.method]), [
        ['/api/ai-media/runs/r1', 'GET'], ['/api/ai-media/runs/r1', 'GET'], ['/api/ai-media/runs/r1', 'GET']]);
    assert.deepEqual(progress, ['queued', 'running', 'completed']);
    assert.equal(V.generationStatus(answer, 'video'), '✨ Generated new video (backup AI provider)');
    assert.equal(V.provenanceText(answer), 'fake-alt · fake-alt-video-1 · backup for fake');
});

test('a failed or unknown background generation is reported, and waiting is bounded', async () => {
    const failed = recordingFetch([
        { status: 202, body: { status: 'queued', run_id: 'r2' } },
        { body: { run_id: 'r2', status: 'failed', error: { category: 'rate_limited', message: 'The provider is busy.', http_status: 429 } } }
    ]);
    const api = new V.AiMediaApi({ fetch: failed.fetch, background: true, wait: async () => {} });
    await assert.rejects(api.generateVideo('x'), err => err.status === 429 && err.message === 'The provider is busy.');
    const gone = recordingFetch([{ status: 202, body: { status: 'running', run_id: 'r3' } }, { status: 404, body: { detail: 'Generation not found.' } }]);
    await assert.rejects(new V.AiMediaApi({ fetch: gone.fetch, background: true, wait: async () => {} }).generateVideo('x'),
        err => err.status === 404);
    let now = 0;
    const slow = { fetch: async () => ({ ok: true, status: 200, json: async () => ({ status: 'running', run_id: 'r4' }) }) };
    const bounded = new V.AiMediaApi({ fetch: slow.fetch, background: true, maxWaitMs: 50, wait: async () => { now += 30; } });
    const realNow = Date.now;
    Date.now = () => realNow() + now;
    try {
        await assert.rejects(bounded.generateVideo('x'), err => err.status === 504);
    } finally {
        Date.now = realNow;
    }
});

test('answers that need no waiting are taken at once (cache hit, manual workflow, older server)', async () => {
    const { fetch, calls } = recordingFetch([
        { body: { status: 'success', video_url: '/static/a.mp4', asset_id: 'a'.repeat(32), cache_hit: true, generated: false, provider: 'veo' } },
        { body: { status: 'manual_required', filename: 'ai_video_x.mp4', prompt: 'x', cache_hit: false, generated: false } },
        { body: { status: 'success', video_url: '/static/old.mp4' } }
    ]);
    const api = new V.AiMediaApi({ fetch, background: true, wait: async () => { throw new Error('should not wait'); } });
    assert.equal((await api.generateVideo('a')).cache_hit, true);
    assert.equal((await api.generateVideo('b')).status, 'manual_required');
    assert.equal((await api.generateVideo('c')).video_url, '/static/old.mp4');
    assert.equal(calls.length, 3);
});

test('provider details stay in stored plans and in words', () => {
    const slides = [{ type: 'ai_video' }];
    V.applyPlans(slides, [{ slot: 'main', scene_index: 0, source: 'AI_VIDEO', selection: 'cached', asset_id: 'a'.repeat(32),
        provider: 'fake-alt', model: 'm1', fallback_from: 'fake', quality_warnings: ['looks blank (one flat colour)'], cache_hit: true, debug: ['x'] }]);
    const kept = slides[0].visual_plan.main;
    assert.deepEqual([kept.provider, kept.model, kept.fallback_from, kept.quality_warnings], ['fake-alt', 'm1', 'fake', ['looks blank (one flat colour)']]);
    assert.equal('debug' in kept, false);
    assert.equal(V.provenanceText({ provider: 'pollinations' }), 'pollinations');
    assert.equal(V.provenanceText({}), '');
    assert.equal(V.stateLabel('temporarily_unavailable'), 'Temporarily unavailable');
    assert.equal(V.stateLabel('not_configured'), 'Not configured');
    assert.equal(V.stateLabel('weird'), 'Unsupported');
    assert.equal(V.sourceLabel({ source: 'NONE', would_require: 'AI_VIDEO', reason: 'no AI video provider can be used now (veo: GEMINI_API_KEY is not set)' }),
        '⛔ AI video needed, but no AI provider is available'.replace('⛔', V.SOURCES.NONE.icon));
    assert.match(V.sourceLabel({ source: 'NONE', would_require: 'AI_VIDEO', reason: 'AI generation is turned off' }), /AI visuals are off/);
});

test('the providers client asks the status and recent generations endpoints', async () => {
    const { fetch, calls } = recordingFetch([{ body: { providers: [], order: {}, fallback: true } }, { body: { runs: [] } }]);
    const api = new V.ProvidersApi({ fetch });
    await api.status();
    await api.runs(5);
    assert.deepEqual(calls.map(c => [c.url, c.init.method]), [['/api/ai-media/providers', 'GET'], ['/api/ai-media/runs?limit=5', 'GET']]);
});

// ---- Phase 9: durable generations (reattaching, attention, batches) ----

test('a generation carries its scene, and the client reads, cancels, resolves and batches runs', async () => {
    const { fetch, calls } = recordingFetch([
        { status: 202, body: { status: 'queued', run_id: 'r9' } },
        { body: { run_id: 'r9', status: 'recovering' } },
        { body: { run_id: 'r9', status: 'completed', result: { status: 'success', video_url: '/static/v.mp4', asset_id: 'v'.repeat(32), generated: true } } },
        { body: { runs: [{ run_id: 'r9', status: 'running' }] } },
        { status: 202, body: { status: 'cancelling', run_id: 'r9' } },
        { body: { run_id: 'r9', status: 'queued' } },
        { body: { batch_id: 'b1', runs: [] } }
    ]);
    const progress = [];
    const api = new V.AiMediaApi({ fetch, background: true, wait: async () => {} });
    const answer = await api.generateVideo('A glacier', { projectId: 12, sceneIndex: 3, onProgress: r => progress.push(r.status) });
    assert.equal(answer.video_url, '/static/v.mp4');
    assert.deepEqual(calls[0].body, { prompt: 'A glacier', wait: false, project_id: 12, scene_index: 3, slot: 'main' });
    assert.deepEqual(progress, ['queued', 'recovering', 'completed']); // "recovering" is followed, not an error
    assert.deepEqual(await api.runs({ projectId: 12, active: true }), [{ run_id: 'r9', status: 'running' }]);
    await api.cancelRun('r9');
    await api.resolveRun('r9', 'retry');
    await api.startLessonBatch(12, 'video');
    assert.deepEqual(calls.slice(3).map(c => [c.url, c.init.method, c.body]), [
        ['/api/ai-media/runs?limit=50&project_id=12&active=true', 'GET', null],
        ['/api/ai-media/runs/r9/cancel', 'POST', {}],
        ['/api/ai-media/runs/r9/resolve', 'POST', { action: 'retry' }],
        ['/api/ai-media/lessons/12/generate', 'POST', { media: 'video' }]]);
});

test('a run needing attention ends the wait with its explanation, and runs read well', async () => {
    const { fetch } = recordingFetch([
        { status: 202, body: { status: 'running', run_id: 'r1' } },
        { body: { run_id: 'r1', status: 'needs_attention', explanation: 'No duplicate generation was started.',
            error: { category: 'ambiguous_submission', message: 'No duplicate generation was started.', http_status: 409 } } }
    ]);
    const api = new V.AiMediaApi({ fetch, background: true, wait: async () => {} });
    await assert.rejects(api.generateVideo('x'), err => err.status === 409 && /No duplicate/.test(err.message) && err.run.status === 'needs_attention');
    // Phase 21: the provider only for ?visualDebug (debug: true gives the earlier text exactly)
    assert.equal(V.runStatusText({ status: 'recovering', scene_index: 3, media_type: 'video', provider: 'veo' }), 'Scene 4 · video · Recovering after an interruption');
    assert.equal(V.runStatusText({ status: 'needs_attention', media_type: 'image', requested_provider: 'fake' }), 'image · Needs attention');
    assert.equal(V.runStatusText({ status: 'recovering', scene_index: 3, media_type: 'video', provider: 'veo' }, { debug: true }),
        'Scene 4 · video · Recovering after an interruption (veo)');
    assert.equal(V.runStatusText({ status: 'needs_attention', media_type: 'image', requested_provider: 'fake' }, { debug: true }), 'image · Needs attention (fake)');
    assert.equal(V.runStatusText({ status: 'queued', media_type: 'video', explanation: 'Waiting to try again after a temporary provider problem.' }),
        'video · Waiting to retry');
    assert.equal(V.runStatusText({ status: 'queued', media_type: 'video', provider: 'veo', explanation: 'Waiting to try again after a temporary provider problem.' },
        { debug: true }), 'video · Waiting to retry (veo)');
    assert.equal(V.runStatusText(null), '');
    assert.deepEqual(V.ACTIVE_RUN_STATES, ['queued', 'running', 'recovering', 'cancel_requested']);
});

// ---- Phase 21: where a visual came from, in plain words ----

test('originText says where a visual came from without naming a provider or model', () => {
    const cases = [
        // plan, expected
        [{ source: 'AI_VIDEO', selection: 'cached', asset_id: 'a'.repeat(32), url: '/u', provider: 'veo', model: 'veo-2.0', cache_hit: true }, 'Made with AI'],
        [{ source: 'AI_IMAGE', selection: 'reviewed', url: '/u', provider: 'fake-alt', model: 'm', fallback_from: 'fake' }, 'Made with AI (backup)'],
        [{ source: 'AI_VIDEO', selection: 'planned', requires_generation: true, provider: 'fake-alt', fallback_from: 'fake' }, 'Will be made with AI'],
        [{ source: 'AI_IMAGE', selection: 'planned', requires_generation: true, provider: 'pollinations' }, 'Will be made with AI'],
        [{ source: 'AI_VIDEO', url: '/static/old.mp4' }, 'Made with AI'], // an older plan without provider details
        [{ source: 'ASSET', selection: 'matched', asset_id: 'a'.repeat(32), url: '/u' }, 'From your library'],
        [{ source: 'UPLOADED_ASSET', selection: 'existing', url: '/static/x.mp4' }, 'From your library'],
        [{ source: 'ASSET', selection: 'matched', asset_id: 'a'.repeat(32), url: '/u', provider: 'fake-alt', model: 'm', fallback_from: 'fake' },
            'Made with AI earlier, from your library'],
        [{ source: 'ASSET', selection: 'existing', asset_id: 'a'.repeat(32), url: '/u', provider: 'manual' }, 'From your library'],
        [{ source: 'SYSTEM_ASSET', selection: 'matched', asset_id: 'a'.repeat(32), url: '/u' }, 'From the shared Aadhi library'],
        // nothing to say: placed by hand, built in, no visual, removed, unavailable
        [{ source: 'AI_VIDEO', selection: 'cached', url: '/static/x.mp4', provider: 'manual' }, ''],
        [{ source: 'AI_VIDEO', selection: 'planned', requires_generation: true, provider: 'manual' }, ''],
        [{ source: 'PROCEDURAL', renderer: 'chart', selection: 'builtin' }, ''],
        [{ source: 'MANIM', renderer: 'manim', selection: 'builtin' }, ''],
        [{ source: 'EXTERNAL_MEDIA', renderer: 'gif', selection: 'builtin' }, ''],
        [{ source: 'NONE', selection: 'none', would_require: 'AI_VIDEO' }, ''],
        [{ source: 'NONE', selection: 'removed' }, ''],
        [{ source: 'AI_IMAGE', selection: 'removed', provider: 'veo' }, ''],
        [{ source: 'NONE', selection: 'reviewed', error: 'asset_unavailable', reason: 'gone' }, ''],
        [null, ''],
        [undefined, '']
    ];
    cases.forEach(([plan, expected]) => {
        const text = V.originText(plan);
        assert.equal(text, expected, JSON.stringify(plan));
        assert.doesNotMatch(text, /veo|fake|pollinations|manual|provider|model/i);
    });
    // the provider details are still there for the debug view
    assert.equal(V.provenanceText(cases[1][0]), 'fake-alt · m · backup for fake');
});
