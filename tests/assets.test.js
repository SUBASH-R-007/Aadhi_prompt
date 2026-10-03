'use strict';
// Unit tests for the asset library client (assets.js). Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const { AssetApi, METADATA_LIMITS, applyResolved, cleanText, collectAssetIds, metadataProblem, parseKeywords, resolveLesson, wordsChanged } = require('../assets.js');

const A = 'a'.repeat(32);
const B = 'b'.repeat(32);
const C = 'c'.repeat(32);
const D = 'd'.repeat(32);

function lesson() {
    return [
        { type: 'ai_video', video_asset_id: A, video_url: '/static/old.mp4' },
        { type: 'simulation', manim_asset_id: B },
        { type: 'content', side_panel: { type: 'manim', video_asset_id: C },
          html: `<p>Look:</p><img src="asset:${D}" alt="diagram"><img src='asset:${A}'>`,
          uploaded_image_assets: { img1: D } },
        { type: 'content', html: '<p>No media here</p>' },
        { type: 'quiz_checkpoint', question: 'Q?' }
    ];
}

const urls = ids => Object.fromEntries(ids.map(id => [id, { url: `/api/assets/${id}/content?token=t-${id.slice(0, 1)}` }]));

test('finds every asset a lesson refers to, once', () => {
    assert.deepEqual(collectAssetIds(lesson()).sort(), [A, B, C, D]);
    assert.deepEqual(collectAssetIds([]), []);
    assert.deepEqual(collectAssetIds([{ type: 'content', video_url: '/static/x.mp4' }]), []); // plain URLs are not references
});

test('fills the fields the renderer reads and keeps the asset IDs', () => {
    const slides = lesson();
    applyResolved(slides, urls([A, B, C, D]));
    assert.equal(slides[0].video_url, `/api/assets/${A}/content?token=t-a`);
    assert.equal(slides[0].video_asset_id, A);
    assert.equal(slides[1].manim_video_url, `/api/assets/${B}/content?token=t-b`);
    assert.equal(slides[2].side_panel.video_url, `/api/assets/${C}/content?token=t-c`);
    assert.equal(slides[2].uploaded_images.img1, `/api/assets/${D}/content?token=t-d`);
    assert.equal(slides[2].html,
        `<p>Look:</p><img src="/api/assets/${D}/content?token=t-d" data-asset-src="asset:${D}" alt="diagram">` +
        `<img src='/api/assets/${A}/content?token=t-a' data-asset-src='asset:${A}'>`);
    assert.equal(slides[3].html, '<p>No media here</p>');
});

test('a resolved lesson can be resolved again when its links expire', () => {
    const slides = lesson();
    applyResolved(slides, urls([A, B, C, D]));
    assert.deepEqual(collectAssetIds(slides).sort(), [A, B, C, D]); // the markers survive
    const fresh = Object.fromEntries([A, B, C, D].map(id => [id, { url: `/fresh/${id.slice(0, 1)}` }]));
    applyResolved(slides, fresh);
    assert.equal(slides[2].html, `<p>Look:</p><img src="/fresh/d" data-asset-src="asset:${D}" alt="diagram"><img src='/fresh/a' data-asset-src='asset:${A}'>`);
    assert.equal(slides[0].video_url, '/fresh/a');
});

test('assets that cannot be resolved leave the scene as it was', () => {
    const slides = lesson();
    applyResolved(slides, urls([B]));
    assert.equal(slides[0].video_url, '/static/old.mp4'); // falls back to its old URL
    assert.equal(slides[1].manim_video_url, `/api/assets/${B}/content?token=t-b`);
    assert.ok(slides[2].html.includes(`src="asset:${D}"`));
    assert.equal(slides[2].side_panel.video_url, undefined);
});

test('resolveLesson asks the server once and reports what is missing', async () => {
    const asked = [];
    const api = { resolve: async ids => { asked.push(ids); return { assets: urls([A, B]), missing: [C, D] }; } };
    const slides = lesson();
    assert.deepEqual(await resolveLesson(slides, api), { resolved: 2, missing: [C, D] });
    assert.equal(asked.length, 1);
    assert.deepEqual(asked[0].sort(), [A, B, C, D]);
    // A lesson without references never calls the server
    assert.deepEqual(await resolveLesson([{ type: 'title' }], { resolve: () => assert.fail('called') }), { resolved: 0, missing: [] });
});

function fakeFetch(responses) {
    const calls = [];
    const fetch = async (url, init) => {
        calls.push([url, init]);
        const { status = 200, body } = responses.shift();
        return { ok: status < 400, status, json: async () => body };
    };
    return { fetch, calls };
}

test('the API client builds requests and prefixes content URLs with the server address', async () => {
    const { fetch, calls } = fakeFetch([
        { body: { assets: [], total: 0 } },
        { body: { assets: { [A]: { id: A, url: `/api/assets/${A}/content?token=x` } }, missing: [B] } }
    ]);
    const api = new AssetApi({ fetch, base: () => 'https://api.example' });
    await api.list({ kind: 'video', source: '', q: 'bridge clip', limit: 200 });
    assert.equal(calls[0][0], '/api/assets?kind=video&q=bridge%20clip&limit=200');
    const data = await api.resolve([A, B]);
    assert.equal(calls[1][0], '/api/assets/resolve');
    assert.deepEqual(JSON.parse(calls[1][1].body), { ids: [A, B] });
    assert.equal(data.assets[A].url, `https://api.example/api/assets/${A}/content?token=x`);
    assert.deepEqual(await api.resolve([]), { assets: {}, missing: [] }); // no request for nothing
    assert.equal(calls.length, 2);
});

test('server errors become readable messages', async () => {
    const { fetch } = fakeFetch([
        { status: 409, body: { detail: { message: 'This asset is used by 1 saved lesson item(s), so it was kept.', references: 1 } } },
        { status: 400, body: { detail: 'This file cannot be added to the library: not a supported image, audio or video file.' } },
        { status: 500, body: null }
    ]);
    const api = new AssetApi({ fetch });
    await assert.rejects(api.remove(A), err => err.status === 409 && /used by 1 saved lesson/.test(err.message));
    await assert.rejects(api.list(), /not a supported image/);
    await assert.rejects(api.get(A), /server had a problem \(error 500\)/);
    const offline = new AssetApi({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
    await assert.rejects(offline.list(), err => err.status === 0 && /could not be reached/.test(err.message));
});

test('keywords are split on commas, tidied and kept once', () => {
    assert.deepEqual(parseKeywords('bridge, traffic, structural load, stress'), ['bridge', 'traffic', 'structural load', 'stress']);
    assert.deepEqual(parseKeywords(' Bridge ,bridge,, BRIDGE , structural   load,\tload , Load'),['Bridge', 'structural load', 'load']);
    assert.deepEqual(parseKeywords(''), []);
    assert.deepEqual(parseKeywords(' , ,, '), []);
    assert.deepEqual(parseKeywords(undefined), []);
});

test('descriptions are trimmed with whitespace runs collapsed', () => {
    assert.equal(cleanText('  A steel bridge\n  carrying   heavy traffic  '), 'A steel bridge carrying heavy traffic');
    assert.equal(cleanText('   '), '');
    assert.equal(cleanText(null), '');
});

test('metadata limits match the server and explain themselves', () => {
    assert.deepEqual(METADATA_LIMITS, { description: 1000, keywords: 30, keyword: 60 });
    const lesson = 'A labelled cross-section of a reinforced concrete beam under load, showing the neutral axis. '.repeat(8).trim();
    assert.equal(metadataProblem(lesson, ['beam', 'neutral axis']), null); // a long, normal description is fine
    assert.equal(metadataProblem('', []), null); // both optional
    assert.equal(metadataProblem('x'.repeat(1000), ['k'.repeat(60)]), null);
    assert.match(metadataProblem('x'.repeat(1001), []), /1000 characters or fewer \(it has 1001\)/);
    assert.match(metadataProblem('', Array.from({ length: 31 }, (_, i) => `w${i}`)), /at most 30 keywords \(there are 31\)/);
    assert.match(metadataProblem('', ['ok', 'k'.repeat(61)]), /60 characters or fewer/);
});

test('saving metadata sends one PATCH with the tidied values', async () => {
    const { fetch, calls } = fakeFetch([
        { body: { id: A, details: { description: 'Bridge carrying heavy traffic', keywords: ['bridge', 'traffic'] } } },
        { status: 403, body: { detail: 'Shared system assets cannot be edited.' } }
    ]);
    const api = new AssetApi({ fetch });
    const updated = await api.update(A, { description: 'Bridge carrying heavy traffic', keywords: ['bridge', 'traffic'] });
    assert.equal(calls[0][0], `/api/assets/${A}`);
    assert.equal(calls[0][1].method, 'PATCH');
    assert.deepEqual(JSON.parse(calls[0][1].body), { description: 'Bridge carrying heavy traffic', keywords: ['bridge', 'traffic'] });
    assert.deepEqual(updated.details.keywords, ['bridge', 'traffic']);
    await assert.rejects(api.update(B, { description: 'x' }), err => err.status === 403 && /cannot be edited/.test(err.message));
});

test('only real edits to a description count as unsaved changes', () => {
    const saved = { description: 'A steel bridge', keywords: ['bridge', 'load'] };
    assert.equal(wordsChanged(saved, 'A steel bridge', 'bridge, load'), false);
    assert.equal(wordsChanged(saved, '  A steel   bridge ', ' bridge ,load, Bridge '), false); // tidied the same as a save
    assert.equal(wordsChanged(saved, 'A steel bridge at night', 'bridge, load'), true);
    assert.equal(wordsChanged(saved, 'A steel bridge', 'bridge'), true);
    assert.equal(wordsChanged({ description: '', keywords: [] }, '', ''), false);
    assert.equal(wordsChanged({ description: '', keywords: [] }, 'x', ''), true);
});
