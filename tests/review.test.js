'use strict';
// Unit tests for Visual Review (review.js). Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../review.js');

const A = 'a'.repeat(32);
const B = 'b'.repeat(32);

function lesson() {
    return [
        { type: 'ai_video', title: 'Bridge', prompt: 'a bridge at dawn', narration: 'Look at the bridge. It carries traffic.',
          visual_plan: { main: { source: 'ASSET', media: 'VIDEO', selection: 'matched', asset_id: A, url: '/api/assets/a/content?token=t', review_status: 'pending' } } },
        { type: 'content', title: 'Forces', subtitle: 'How loads spread', side_panel: { type: 'chart', data: { labels: ['x'], datasets: [] } },
          visual_plan: { side: { source: 'PROCEDURAL', media: 'ANIMATION', selection: 'builtin', renderer: 'chart', review_status: 'approved' } },
          visual_review: { side: { status: 'approved' } } },
        { type: 'content', title: 'Lighthouse', side_panel: { type: 'image', prompt: 'a lighthouse in a storm' },
          visual_plan: { side: { source: 'AI_IMAGE', media: 'STATIC_IMAGE', selection: 'planned', requires_generation: true, provider: 'pollinations', review_status: 'pending' } } },
        { type: 'content', title: 'Removed', side_panel: { type: 'image', prompt: 'x' },
          visual_plan: { side: { source: 'NONE', media: 'NONE', selection: 'removed', review_status: 'removed' } },
          visual_review: { side: { status: 'removed' } } },
        { type: 'quiz_checkpoint', question: '?' } // nothing to review
    ];
}

// A server stand-in: records decisions and answers like POST /api/visuals/review
function fakeApi(answer) {
    const calls = [];
    return {
        calls,
        async review(body) {
            calls.push(body);
            if (answer) return answer(body);
            const plans = {
                keep: { source: 'ASSET', media: 'VIDEO', selection: 'approved', asset_id: A, url: '/u', review_status: 'approved' },
                choose: { source: 'ASSET', media: 'VIDEO', selection: 'reviewed', asset_id: body.asset_id, url: '/new', review_status: 'changed' },
                remove: { source: 'NONE', media: 'NONE', selection: 'removed', review_status: 'removed' },
                reset: { source: 'ASSET', media: 'VIDEO', selection: 'matched', asset_id: A, url: '/u', review_status: 'pending' }
            };
            const statuses = { keep: 'approved', choose: 'changed', remove: 'removed' };
            return { review: statuses[body.action] ? { status: statuses[body.action], asset_id: body.asset_id } : null, plans: { [body.slot]: plans[body.action] } };
        }
    };
}

function session(options = {}) {
    const slides = options.slides || lesson();
    const api = options.api || fakeApi();
    const media = options.media || { calls: [], async generateVideo(p, o) { this.calls.push(['video', p, o]); return { status: 'success', asset_id: B, cache_hit: false, generated: true }; },
        async generateImage(p, o) { this.calls.push(['image', p, o]); return { status: 'success', asset_id: B, cache_hit: true, generated: false }; } };
    const asked = [];
    const s = new R.ReviewSession({ slides, api, media, confirm: text => { asked.push(text); return options.confirmAnswer !== false; },
        aiAvailable: () => options.ai !== false });
    return { s, slides, api, media, asked };
}

test('every scene visual becomes a review item with a status in words', () => {
    const items = R.reviewItems(lesson());
    assert.deepEqual(items.map(i => [i.sceneIndex, i.slot, i.status]), [[0, 'main', 'pending'], [1, 'side', 'approved'], [2, 'side', 'pending'], [3, 'side', 'removed']]);
    assert.equal(R.statusText('pending'), '● Needs review');
    assert.equal(R.statusText('approved'), '✓ Approved');
    assert.equal(R.statusText('changed'), '✎ Changed');
    assert.equal(R.statusText('removed'), '— Removed'); // Phase 21: the state in words (a presenter can be removed too)
    assert.equal(R.statusText('nonsense'), '● Needs review');
    // every state is an icon and words, never colour alone
    Object.values(R.STATUS).forEach(info => { assert.ok(info.icon.trim()); assert.match(info.label, /^[A-Z][a-z]+( [a-z]+)?$/); });
    const counts = R.summarize(items);
    assert.deepEqual(counts, { scenes: 4, total: 4, pending: 2, approved: 1, changed: 0, removed: 1, none: 1 });
    assert.equal(R.summaryText(counts), '4 scenes · 4 items · ● 2 to check · ✓ 1 approved · — 1 without visual');
    // Phase 21: a scene's presenter and layout are items of that scene, so the count names the scenes as well (a 12-scene
    // lesson is "12 scenes · 33 items", never "33 visuals"); every state keeps its icon
    const withExtras = [...items, { sceneIndex: 0, slot: 'presenter', status: 'pending', plan: {} }, { sceneIndex: 0, slot: 'composition', status: 'approved', plan: {} }];
    assert.equal(R.summaryText(R.summarize(withExtras)), '4 scenes · 6 items · ● 3 to check · ✓ 2 approved · — 1 without visual');
    assert.equal(R.summaryText(R.summarize(items.slice(0, 1))), '1 scene · 1 item · ● 1 to check · ✓ 0 approved');
    // A lesson from before Visual Review (no review fields at all) is simply pending
    const old = [{ type: 'ai_video', visual_plan: { main: { source: 'UPLOADED_ASSET', media: 'VIDEO', selection: 'existing', url: '/static/x.mp4' } } }];
    assert.equal(R.reviewItems(old)[0].status, 'pending');
    assert.deepEqual(R.reviewItems([{ type: 'content' }, null]), []);
});

test('sources and reasons are plain words, never internal names', () => {
    assert.equal(R.sourceText({ source: 'ASSET' }), 'Library'); // Phase 21: one name for the Library
    assert.equal(R.sourceText({ source: 'SYSTEM_ASSET' }), 'Shared Aadhi asset');
    assert.equal(R.sourceText({ source: 'PROCEDURAL', renderer: 'chart' }), 'Generated chart');
    // Phase 21: no tool names (Manim, GIF search) for normal users
    assert.equal(R.sourceText({ source: 'MANIM' }), 'Animation');
    assert.equal(R.sourceText({ source: 'EXTERNAL_MEDIA' }), 'Animated picture');
    assert.equal(R.sourceText({ source: 'PROCEDURAL', renderer: 'gif' }), 'Animated picture');
    assert.equal(R.sourceText({ source: 'PROCEDURAL', renderer: 'manim' }), 'Animation');
    assert.equal(R.sourceText({ source: 'AI_VIDEO' }), 'AI video');
    assert.equal(R.sourceText({ source: 'NONE', selection: 'removed' }), 'No visual');
    assert.equal(R.reasonText({ selection: 'matched' }), 'Matched an existing visual in your Library.');
    assert.equal(R.reasonText({ selection: 'builtin', source: 'PROCEDURAL', renderer: 'chart' }), "Drawn from the scene's own data (generated chart).");
    assert.equal(R.reasonText({ selection: 'builtin', source: 'MANIM', renderer: 'manim' }), "Drawn as an animation from the scene's own animation steps.");
    assert.equal(R.reasonText({ selection: 'builtin', source: 'EXTERNAL_MEDIA', renderer: 'gif' }), "An animated picture found for the scene's topic.");
    for (const plan of [{ source: 'MANIM', selection: 'builtin', renderer: 'manim' }, { source: 'EXTERNAL_MEDIA', selection: 'builtin', renderer: 'gif' },
        { source: 'PROCEDURAL', selection: 'builtin', renderer: 'gif' }]) {
        assert.doesNotMatch(`${R.sourceText(plan)} ${R.reasonText(plan)}`, /manim|\bgif\b/i);
    }
    assert.doesNotMatch(Object.values(R.FILTERS).map(f => f.label).join(' '), /manim|\bgif\b/i);
    assert.equal(R.reasonText({ selection: 'planned', source: 'AI_IMAGE' }), 'No suitable existing visual was found, so it has to be generated.');
    assert.equal(R.reasonText({ selection: 'cached', source: 'AI_VIDEO', cache_hit: true }), 'Reused a visual generated earlier for the same request.');
    assert.equal(R.reasonText({ selection: 'reviewed', error: 'asset_unavailable', reason: 'The visual you chose is unavailable: gone.' }), 'The visual you chose is unavailable: gone.');
    assert.equal(R.purposeText({ narration: 'Look at the bridge. [PAUSE] It carries traffic.' }), 'Look at the bridge.');
    assert.equal(R.purposeText({ subtitle: 'How loads spread', narration: 'x' }), 'How loads spread');
});

test('previews never need anything new to be made', () => {
    assert.equal(R.previewKind({ url: '/a.png', media: 'STATIC_IMAGE' }), 'image');
    assert.equal(R.previewKind({ url: '/a.mp4', media: 'VIDEO' }), 'video');
    assert.equal(R.previewKind({ source: 'AI_IMAGE', requires_generation: true }), 'not-generated');
    assert.equal(R.previewKind({ source: 'PROCEDURAL', renderer: 'chart' }, { side_panel: { data: {} } }), 'chart');
    assert.equal(R.previewKind({ source: 'MANIM', renderer: 'manim' }), 'described');
    assert.equal(R.previewKind({ source: 'NONE', selection: 'removed' }), 'removed');
    assert.equal(R.previewKind({ source: 'NONE', selection: 'none' }), 'none');
    assert.equal(R.previewKind({ source: 'NONE', error: 'asset_unavailable', url: '/stale' }), 'unavailable'); // missing asset: recoverable state
});

test('Keep, Change (choose an asset) and Remove record decisions and update the scene', async () => {
    const { s, slides, api } = session();
    assert.equal(s.current.sceneIndex, 0);
    await s.keep();
    assert.deepEqual(api.calls[0], { scene_index: 0, slot: 'main', action: 'keep' });
    assert.equal(slides[0].visual_review.main.status, 'approved');
    assert.equal(slides[0].visual_plan.main.review_status, 'approved');
    assert.equal(s.current.status, 'approved');
    assert.match(s.message, /Kept/);

    await s.chooseAsset({ id: B });
    assert.deepEqual(api.calls[1], { scene_index: 0, slot: 'main', action: 'choose', asset_id: B });
    assert.equal(slides[0].visual_plan.main.asset_id, B);
    assert.equal(slides[0].visual_review.main.status, 'changed');

    await s.remove();
    assert.equal(slides[0].visual_plan.main.selection, 'removed');
    assert.equal(s.current.status, 'removed');

    await s.reset(); // back to the automatic choice
    assert.equal('visual_review' in slides[0], false);
    assert.equal(s.current.status, 'pending');
});

test('a failed save leaves the scene as it was and says why', async () => {
    const { s, slides } = session({ api: { async review() { throw Object.assign(new Error('Asset not found.'), { status: 404 }); } } });
    const before = JSON.stringify(slides[0]);
    assert.equal(await s.chooseAsset({ id: B }), null);
    assert.equal(JSON.stringify(slides[0]), before);
    assert.equal(s.error, true);
    // Phase 21: what happened, that the lesson is safe, what to do; the server's own words only for the debug view
    assert.equal(s.message, 'The change could not be saved. Your lesson is safe: nothing was changed. Please try again.');
    assert.equal(s.detailText(), 'Asset not found.');
    assert.equal(s.busy, false);
    // the detail belongs to that message only: a later message never carries it
    s.message = 'Saving…';
    assert.equal(s.detailText(), '');
});

test('a new AI version bypasses the cache and becomes the chosen visual', async () => {
    const { s, api, media } = session();
    const result = await s.generate({ force: true });
    assert.deepEqual(media.calls, [['video', 'a bridge at dawn', { force: true }]]);
    assert.deepEqual(api.calls[0], { scene_index: 0, slot: 'main', action: 'choose', asset_id: B });
    assert.equal(result.generated, true);
    assert.equal(s.message, '✨ Generated new video');
});

test('generating the planned AI visual keeps it (and says when it was reused from the cache)', async () => {
    const { s, api, media } = session();
    assert.equal(s.select(2), true); // the planned AI image
    await s.generate();
    assert.deepEqual(media.calls, [['image', 'a lighthouse in a storm', { force: false }]]);
    assert.deepEqual(api.calls[0], { scene_index: 2, slot: 'side', action: 'keep', asset_id: B });
    assert.equal(s.message, '♻ Reused existing image');
});

test('with AI switched off nothing is generated, and choosing assets still works', async () => {
    const { s, api, media } = session({ ai: false });
    assert.equal(await s.generate({ force: true }), null);
    assert.equal(media.calls.length, 0);
    assert.equal(api.calls.length, 0);
    assert.match(s.message, /AI generation is turned off/);
    await s.chooseAsset({ id: B });
    assert.equal(api.calls.length, 1);
    // Only AI-capable slots offer generation at all
    assert.equal(s.canGenerate(), true);
    s.select(1);
    assert.equal(s.canGenerate(), false); // a chart
    assert.deepEqual(R.generationTarget({ type: 'content', title: 'T', visual: { concept: 'a volcano', type: 'image' } }, 'side'), { media: 'image', prompt: 'a volcano' });
    assert.equal(R.generationTarget({ type: 'content', visual: { concept: 'x', type: 'chart' } }, 'side'), null);
});

test('a provider failure or manual workflow is reported, not treated as a new visual', async () => {
    const failing = { async generateVideo() { throw Object.assign(new Error('AI video generation is turned off on this server.'), { status: 403 }); } };
    const { s, api } = session({ media: failing });
    assert.equal(await s.generate({ force: true }), null);
    assert.equal(api.calls.length, 0);
    // Phase 21: the reason in plain words (from the answer's status), the lesson safe, what to do; the raw text for debug only
    assert.equal(s.message, 'No new visual was made (AI generation is turned off). Your lesson is safe: the scene keeps its current visual. '
        + 'Try again, or choose one from your Library.');
    assert.equal(s.error, true);
    assert.equal(s.detailText(), 'AI video generation is turned off on this server.');
    const manual = { async generateVideo() { return { status: 'manual_required', filename: 'ai_video_x.mp4' }; } };
    const m = session({ media: manual });
    assert.equal(await m.s.generate(), null);
    // Phase 21: what a teacher can do (a library clip, or ask the administrator); where the file goes: the debug view only
    assert.equal(m.s.message, "AI videos aren't made automatically here. Choose a clip from your Library, or ask your administrator to turn on AI videos. "
        + 'Your lesson is safe: the scene keeps its current visual.');
    assert.doesNotMatch(m.s.message, /static_videos|by hand/);
    assert.match(m.s.detailText(), /static_videos\/ai_video_x\.mp4/);
    assert.equal(m.s.error, true);
    assert.equal(m.s.busy, false);
    assert.equal(m.api.calls.length, 0);
    // an unknown failure: no reason invented, the raw text kept for the debug view
    const odd = session({ media: { async generateVideo() { throw Object.assign(new Error('Traceback (most recent call last): KeyError'), { status: 500 }); } } });
    await odd.s.generate({ force: true });
    assert.match(odd.s.message, /^No new visual was made\. Your lesson is safe/);
    assert.doesNotMatch(odd.s.message, /Traceback|KeyError|500/);
    assert.match(odd.s.detailText(), /Traceback/);
    const offline = session({ media: { async generateVideo() { throw Object.assign(new Error('The server could not be reached.'), { status: 0 }); } } });
    await offline.s.generate({ force: true });
    assert.match(offline.s.message, /^No new visual was made \(the server could not be reached\)\./);
});

test('navigation moves between visuals and finds the next one needing review', () => {
    const { s } = session();
    assert.equal(s.next(), true);
    assert.equal(s.current.sceneIndex, 1);
    assert.equal(s.prev(), true);
    assert.equal(s.prev(), false); // already the first
    assert.equal(s.nextPending(), true);
    assert.equal(s.current.sceneIndex, 2);
    assert.equal(s.nextPending(), true); // wraps round to the first pending one
    assert.equal(s.current.sceneIndex, 0);
    assert.equal(s.setFilter('approved'), true);
    assert.deepEqual(s.items.map(i => i.sceneIndex), [1]);
    assert.equal(s.setFilter('none'), true);
    assert.deepEqual(s.items.map(i => i.sceneIndex), [3]);
    assert.equal(s.setFilter('ai'), true);
    assert.deepEqual(s.items.map(i => i.sceneIndex), [2]);
    assert.equal(s.setFilter('bogus'), false);
    s.setFilter('pending');
    assert.equal(s.nextPending(), true);
});

test('a decision still being saved is not dropped without asking', async () => {
    let finish;
    const slow = { review: body => new Promise(resolve => { finish = () => resolve({ review: { status: 'approved' }, plans: { main: { source: 'ASSET', review_status: 'approved' } } }); }) };
    const declined = session({ api: slow, confirmAnswer: false });
    const pending = declined.s.keep();
    assert.equal(declined.s.busy, true);
    assert.equal(declined.s.next(), false); // "Discard this change?" -> no
    assert.deepEqual(declined.asked, ['Discard this change?']);
    assert.equal(declined.s.current.sceneIndex, 0);
    finish();
    await pending;
    assert.equal(declined.s.next(), true); // saved: moving on needs no question
    assert.equal(declined.asked.length, 1);
});

test('the review API sends the lesson, the page copy of the scene and the AI setting', async () => {
    const sent = [];
    const api = new R.ReviewApi({
        fetch: async (url, init) => { sent.push([url, JSON.parse(init.body)]); return { ok: true, status: 200, json: async () => ({ review: null, plans: {} }) }; },
        projectId: async () => 42, sceneAt: i => ({ index: i }), allowAi: () => false
    });
    await api.review({ scene_index: 3, slot: 'side', action: 'remove' });
    assert.deepEqual(sent[0], ['/api/visuals/review', { project_id: 42, scene: { index: 3 }, allow_ai_generation: false, scene_index: 3, slot: 'side', action: 'remove' }]);
    const denied = new R.ReviewApi({ fetch: async () => ({ ok: false, status: 404, json: async () => ({ detail: 'Asset not found.' }) }), projectId: async () => 1, sceneAt: () => ({}) });
    await assert.rejects(denied.review({ scene_index: 0, slot: 'main', action: 'choose', asset_id: A }), err => err.status === 404 && err.message === 'Asset not found.');
    const offline = new R.ReviewApi({ fetch: async () => { throw new TypeError('Failed to fetch'); }, projectId: async () => 1, sceneAt: () => ({}) });
    await assert.rejects(offline.review({ scene_index: 0, slot: 'main', action: 'keep' }), err => err.status === 0);
});

// ---- Phase 21: the panel in plain words, the debug view, buttons and focus ----------------------------------------------

const { fakeDoc, Node } = require('./helpers/cinematic-dom.js');

// Focus for the DOM stand-in: activeElement, focus() (with a bubbling focusin), contains() that knows removed nodes, closest()
const attached = (root, n) => { while (n) { if (n === root) return true; const p = n.parent; if (!p || !p.children.includes(n)) return false; n = p; } return false; };
Node.prototype.contains = function (n) { return attached(this, n); };
Node.prototype.closest = function (sel) { let n = this; while (n && n.tag !== '#text') { if (n.matches(sel)) return n; n = n.parent; } return null; };
Node.prototype.focus = function () {
    this.doc.activeElement = this;
    for (let n = this; n; n = n.parent) (n.listeners.focusin || []).forEach(fn => fn({ type: 'focusin', target: this }));
};
Object.defineProperty(Node.prototype, 'isConnected', { get() { return attached(this.doc.documentElement, this); } });
Node.prototype.pause = function () {}; // a <video> preview (the panel pauses it when it draws again)

function panelFor({ slides = lesson(), debug = false, api = fakeApi(), media, presenter = null, composition = null } = {}) {
    const doc = fakeDoc();
    doc.activeElement = doc.body;
    const s = new R.ReviewSession({ slides, api, media: media || { async generateVideo() { return { status: 'success', asset_id: B, generated: true }; } },
        presenter, composition });
    const changes = [];
    const panel = new R.VisualReviewPanel({ doc, session: s, pickAsset: () => {}, showInLesson: () => {}, debug, onChange: i => changes.push(i) });
    return { doc, s, panel, changes, slides };
}
const backupPlan = { source: 'AI_VIDEO', media: 'VIDEO', selection: 'cached', asset_id: A, url: '/api/assets/a/content?token=t', provider: 'fake-alt',
    model: 'fake-alt-video-1', fallback_from: 'fake', cache_hit: true, review_status: 'pending' };
const PROVIDER_NAMES = /fake-alt|fake-alt-video-1|\bfake\b|pollinations|\bveo\b|provider|model/i;

test('the visual detail says where a visual came from in plain words; the AI provider line only in the debug view', () => {
    const slides = [{ type: 'ai_video', title: 'Bridge', prompt: 'a bridge', narration: 'A bridge.', visual_plan: { main: { ...backupPlan } } }];
    const { panel } = panelFor({ slides });
    panel.open();
    const detail = panel.detail;
    assert.equal(detail.querySelector('.review-provider'), null, 'no provider line for normal users');
    assert.equal(detail.querySelector('.review-origin').textContent, 'Made with AI (backup)');
    assert.doesNotMatch(detail.textContent, PROVIDER_NAMES);
    assert.equal(detail.querySelector('.review-debug'), null);
    assert.deepEqual(detail.querySelectorAll('.review-facts dt').map(dt => dt.textContent), ['Visual', 'Origin', 'Status', 'Why']);
    // the debug view (?visualDebug): the exact provenance, as before, plus the plan's facts
    const debug = panelFor({ slides: [{ ...slides[0], visual_plan: { main: { ...backupPlan } } }], debug: true });
    debug.panel.open();
    assert.equal(debug.panel.detail.querySelector('.review-provider').textContent, 'fake-alt · fake-alt-video-1 · backup for fake');
    assert.equal(debug.panel.detail.querySelector('.review-origin').textContent, 'Made with AI (backup)');
    assert.deepEqual(debug.panel.detail.querySelectorAll('.review-facts dt').map(dt => dt.textContent), ['Visual', 'Origin', 'AI provider', 'Status', 'Why']);
    const facts = JSON.parse(debug.panel.detail.querySelector('.review-debug').textContent);
    assert.equal(facts.provider, 'fake-alt');
    assert.equal(facts.model, 'fake-alt-video-1');
    // the other origins, and none where there is nothing to say
    const origin = plan => {
        const p = panelFor({ slides: [{ type: 'content', title: 'T', side_panel: { type: 'image', prompt: 'x' }, visual_plan: { side: plan } }] });
        p.panel.open();
        const dd = p.panel.detail.querySelector('.review-origin');
        return dd ? dd.textContent : null;
    };
    assert.equal(origin({ source: 'ASSET', media: 'STATIC_IMAGE', selection: 'matched', asset_id: A, url: '/u', provider: 'pollinations', model: 'flux' }),
        'Made with AI earlier, from your library');
    assert.equal(origin({ source: 'ASSET', media: 'STATIC_IMAGE', selection: 'matched', asset_id: A, url: '/u' }), 'From your library');
    assert.equal(origin({ source: 'AI_IMAGE', media: 'STATIC_IMAGE', selection: 'planned', requires_generation: true, provider: 'pollinations' }), 'Will be made with AI');
    assert.equal(origin({ source: 'NONE', media: 'NONE', selection: 'none' }), null);
    assert.equal(origin({ source: 'PROCEDURAL', media: 'ANIMATION', selection: 'builtin', renderer: 'chart' }), null);
    assert.equal(origin({ source: 'AI_IMAGE', media: 'STATIC_IMAGE', selection: 'cached', provider: 'manual', url: '/u' }), null);
});

test('the main actions use the product buttons; the hooks the page and the checks use stay', () => {
    const { panel } = panelFor();
    panel.open();
    const keep = panel.detail.querySelector('[data-action="keep"]');
    for (const name of ['export-action', 'ui-btn', 'ui-btn-primary']) assert.ok(keep.classList.contains(name), name);
    const change = panel.detail.querySelector('[data-action="change"]');
    for (const name of ['export-action', 'export-action-secondary', 'ui-btn']) assert.ok(change.classList.contains(name), name);
    assert.equal(change.classList.contains('ui-btn-primary'), false);
    for (const action of ['keep', 'change', 'remove', 'pick', 'generate', 'prev', 'next', 'next-pending', 'show']) {
        assert.ok(panel.detail.querySelector(`[data-action="${action}"]`), action);
    }
    // the close button is labelled; the filters are labelled and in plain words
    const close = panel.panel.querySelector('.export-close');
    assert.equal(close.getAttribute('aria-label'), 'Close Visual Review');
    assert.equal(close.getAttribute('title'), 'Close');
    assert.equal(panel.filters.getAttribute('aria-labelledby'), 'review-filters-label');
    assert.equal(panel.filters.querySelector('#review-filters-label').textContent, 'Show:');
    assert.deepEqual(panel.filters.querySelectorAll('.review-filter').map(b => b.textContent),
        ['All', 'Needs review', 'Approved', 'Changed', 'No visual', 'Made with AI', 'From the library', 'Built-in', 'Animations', 'Layout', 'Quality']);
    // a removed visual says so with an icon and words
    assert.equal(panel.list.querySelector('.review-item[data-scene="3"] .review-chip').textContent, '— Removed');
});

test('the debug view adds the technical reason to the status line; normal users see the plain message only', async () => {
    const failing = { async review() { throw Object.assign(new Error('Asset not found.'), { status: 404 }); } };
    const plain = panelFor({ api: failing });
    plain.panel.open();
    await plain.s.chooseAsset({ id: B });
    plain.panel.render();
    assert.equal(plain.panel.status.textContent, 'The change could not be saved. Your lesson is safe: nothing was changed. Please try again.');
    assert.equal(plain.panel.status.getAttribute('data-kind'), 'error');
    assert.equal(plain.panel.status.getAttribute('role'), 'status');
    const debug = panelFor({ api: failing, debug: true });
    debug.panel.open();
    await debug.s.chooseAsset({ id: B });
    debug.panel.render();
    assert.equal(debug.panel.status.textContent, 'The change could not be saved. Your lesson is safe: nothing was changed. Please try again. (Asset not found.)');
    // a generated visual: which provider made it, in the debug view only
    const made = { async generateVideo() { return { status: 'success', asset_id: B, generated: true, provider: 'fake-alt', model: 'm1', fallback_from: 'fake' }; } };
    const p = panelFor({ media: made });
    p.panel.open();
    await p.s.generate({ force: true });
    p.panel.render();
    assert.equal(p.panel.status.textContent, '✨ Generated new video (backup AI provider)');
    const d = panelFor({ media: made, debug: true });
    d.panel.open();
    await d.s.generate({ force: true });
    d.panel.render();
    assert.equal(d.panel.status.textContent, '✨ Generated new video (backup AI provider) (fake-alt · m1 · backup for fake)');
});

test('presenter clips: plain words for everyone, the provider for the debug view; failures say the lesson is safe', async () => {
    const slides = [{ type: 'content', title: 'Teacher', narration: 'Hello.', presenter_plan: { presenter_id: 'p1', type: 'ai_avatar', enabled: true, position: 'pip' } }];
    const presenter = {
        enabled: () => true, canGenerate: () => true,
        generate: async () => ({ asset_id: B, provider: 'fake-presenter', warnings: ['no sound track'] }),
        review: async body => ({ review: { status: body.action === 'keep' ? 'approved' : 'changed' }, plan: { ...slides[0].presenter_plan, media: { asset_id: B } } })
    };
    const { s, panel } = panelFor({ slides, presenter });
    panel.open();
    assert.equal(s.current.slot, 'presenter');
    assert.equal(panel.list.querySelector('.review-item-meta').textContent, 'Presenter · Small corner');
    assert.equal(panel.list.querySelector('.review-item-title').textContent, 'Scene 1 · Presenter: Teacher'); // Phase 21: capitalised, in words
    assert.equal(panel.detail.querySelector('.review-position').textContent, 'Scene 1 of 1 · Presenter');
    panel.changeOpen = true;
    panel.renderDetail();
    assert.equal(panel.detail.querySelector('[data-action="pick"]').textContent, 'Choose clip or picture from your Library');
    panel.changeOpen = false;
    await s.generate();
    assert.equal(s.message, 'Presenter clip made with AI for this scene (please check: no sound track).');
    assert.equal(s.detailText(), 'generated by fake-presenter');
    assert.doesNotMatch(s.message, PROVIDER_NAMES);
    panel.render();
    const generate = panel.detail.querySelector('[data-action="generate"]');
    assert.doesNotMatch(generate.getAttribute('title'), /provider/i);
    // a failure
    presenter.generate = async () => { throw Object.assign(new Error('fake-presenter: HTTP 500 upstream'), { status: 502 }); };
    await s.generate({ force: true });
    assert.equal(s.message, 'No presenter clip was made. Your lesson is safe: the scene keeps its presenter. Try again, or choose a clip from your Library.');
    assert.equal(s.error, true);
    assert.match(s.detailText(), /HTTP 500/);
    // nothing to generate: no provider word either
    presenter.canGenerate = () => false;
    await s.generate();
    assert.doesNotMatch(s.message, /provider/i);
});

test('composition and visual direction failures: what happened, the lesson is safe, what to do (the raw text for debug only)', async () => {
    const slides = [{ title: 'Leaf', cinematic_plan: { template: 'comparison', review_status: 'pending', layers: [] } }];
    const composition = {
        enabled: () => true,
        regenerate: async () => { throw Object.assign(new Error('Traceback (most recent call last)'), { status: 500 }); },
        reviewDirection: async () => { throw new Error('Lesson not found.'); },
        regenerateDirection: async () => { throw new Error('the server could not be reached'); }
    };
    const { s } = panelFor({ slides, composition });
    assert.equal(s.current.slot, 'composition');
    assert.equal(await s.regenerateComposition(), null);
    assert.equal(s.message, 'The layout could not be decided again. Your lesson is safe: the scene keeps its layout. Please try again.');
    assert.match(s.detailText(), /Traceback/);
    assert.equal(await s.decideDirection('reset'), null);
    assert.equal(s.message, 'The change could not be saved. Your lesson is safe: nothing was changed. Please try again.');
    assert.equal(s.detailText(), 'Lesson not found.');
    assert.equal(await s.regenerateDirection(), null);
    assert.equal(s.message, 'The visual direction could not be decided again. Your lesson is safe: the scene keeps its direction. Please try again.');
    assert.equal(s.detailText(), 'the server could not be reached');
    assert.equal(s.error, true);
    assert.equal(s.busy, false);
});

test('the layout drawing names its parts in words (the layer ids stay in data-layer)', () => {
    const box = (x, y, w, h) => ({ x, y, w, h });
    const slides = [{ title: 'Leaf', cinematic_plan: { template: 'comparison', review_status: 'pending', layers: [
        { id: 'background', type: 'background', box: box(0, 0, 1, 1) }, { id: 'board', type: 'board', box: box(0.4, 0.2, 0.5, 0.5) },
        { id: 'presenter', type: 'presenter', box: box(0.7, 0.3, 0.2, 0.6) }, { id: 'subtitles', type: 'subtitles', box: box(0.1, 0.85, 0.8, 0.1) }] } }];
    const { panel } = panelFor({ slides, composition: { enabled: () => true } });
    panel.open();
    const frame = panel.detail.querySelector('.review-composition-frame');
    assert.deepEqual(frame.querySelectorAll('.review-composition-box').map(b => [b.getAttribute('data-layer'), b.textContent]),
        [['board', 'Text'], ['presenter', 'Presenter'], ['subtitles', 'Captions']]);
    assert.equal(frame.getAttribute('aria-label'), 'Layout of scene 1: Text, Presenter');
});

test('focus: into the panel on open, kept inside it with Tab, kept on the same control across a redraw, back to the opener on close', () => {
    const { doc, panel, s } = panelFor();
    const opener = doc.createElement('button');
    doc.body.appendChild(opener);
    opener.focus();
    panel.open();
    // the dialog's heading takes the focus (it is not in the Tab order itself)
    assert.equal(doc.activeElement, panel.title);
    assert.equal(panel.title.getAttribute('tabindex'), '-1');
    assert.equal(panel.panel.getAttribute('aria-labelledby'), panel.title.getAttribute('id'));
    const list = panel.focusables();
    assert.ok(list.length > 5);
    assert.ok(list.every(n => n.getAttribute('disabled') === null && n.getAttribute('tabindex') !== '-1'), 'only enabled controls');
    assert.equal(list.includes(panel.title), false);
    const tab = (shiftKey = false) => { let prevented = false; panel.onEscape({ key: 'Tab', shiftKey, preventDefault() { prevented = true; } }); return prevented; };
    // Shift+Tab from the heading: the last control; Tab from the last control: the first one
    assert.equal(tab(true), true);
    assert.equal(doc.activeElement, list[list.length - 1]);
    assert.equal(tab(), true);
    assert.equal(doc.activeElement, list[0]);
    assert.equal(tab(true), true);
    assert.equal(doc.activeElement, list[list.length - 1]);
    // in the middle the browser moves on by itself
    list[2].focus();
    assert.equal(tab(), false);
    assert.equal(doc.activeElement, list[2]);
    // focus that left the panel comes back
    doc.activeElement = doc.body;
    assert.equal(tab(), true);
    assert.equal(doc.activeElement, list[0]);
    // a redraw keeps the focus on the same control (a new node), here a scene in the list
    const item = panel.list.querySelector('.review-item[data-scene="2"]');
    item.focus();
    item.fire('click');
    assert.equal(s.current.sceneIndex, 2);
    const again = panel.list.querySelector('.review-item[data-scene="2"]');
    assert.notEqual(again, item, 'the list was drawn again');
    assert.equal(doc.activeElement, again);
    // "Change ▾" redraws the detail: the focus stays on it
    const change = panel.detail.querySelector('[data-action="change"]');
    change.focus();
    change.fire('click');
    assert.notEqual(panel.detail.querySelector('[data-action="change"]'), change);
    assert.equal(doc.activeElement, panel.detail.querySelector('[data-action="change"]'));
    assert.equal(doc.activeElement.textContent, 'Change ▴');
    // a control drawn away disabled: the heading holds the focus (never <body>)
    const next = panel.detail.querySelector('[data-action="next"]');
    next.focus();
    next.fire('click'); // to the last visual: "Next ›" is disabled there
    assert.equal(s.current.sceneIndex, 3);
    assert.equal(doc.activeElement, panel.title);
    // Escape closes and gives the focus back to the opener
    panel.onEscape({ key: 'Escape' });
    assert.equal(panel.root.classList.contains('open'), false);
    assert.equal(doc.activeElement, opener);
    // Tab does nothing while the panel is closed
    assert.equal(tab(), false);
    // opened again: the focus goes in again; close() gives it back
    panel.open();
    assert.equal(doc.activeElement, panel.title);
    panel.close();
    assert.equal(doc.activeElement, opener);
});
