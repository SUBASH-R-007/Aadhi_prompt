'use strict';
// Unit tests for the style in Visual Review (Phase 17): the read-only "Style" block of the scene inspector (review.js
// renderStyle, fed by the composition adapter's styleSummary(scene, plan)) and the scene's own accent / background in the
// composition's "Change" form (adapter styleOptions(); sent as the composition overrides style_accent / style_background).
// A style change is a composition decision: it never touches a visual's review.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../review.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

const box = (x, y, w, h) => ({ x, y, w, h });
const tick = () => new Promise(r => setTimeout(r, 0));

function plan(extra = {}) {
    return {
        version: 1, template: 'comparison', template_label: 'Comparison', duration: 11, shot: 'medium',
        style: { typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto', emphasis: 'clear' },
        background: { type: 'gradient' }, presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 },
        camera: { shot: 'medium', movement: 'static' },
        layers: [
            { id: 'visual', type: 'visual', role: 'image', box: box(0.05, 0.19, 0.35, 0.52), z: 20, start: 0.15, important: true, camera: true },
            { id: 'board', type: 'board', role: 'comparison', box: box(0.42, 0.19, 0.53, 0.5), z: 30, start: 0.1, important: true, camera: true }
        ],
        timeline: [], warnings: [], notes: [], review_status: 'pending',
        composition: { version: 1, mode: 'rules', source: 'rules', decision: { template: 'comparison' }, locked: [], reasons: ['both sides are taught'], repairs: [] },
        ...extra
    };
}

function summary(extra = {}) {
    return {
        id: 'academic@1', label: 'Academic', family: 'academic', version: 1, tone: 'light', fingerprint: '0123456789abcdef',
        overrides: [['accent', 'teal'], ['background', 'plain'], ['text_size', 'large']],
        sceneOverrides: [['accent', 'coral']],
        adjustments: ['body text made darker or lighter to stay readable', 'captions made darker or lighter to stay readable'],
        swatch: ['#f4efe3', '#9b2335', '#fff'],
        legacy: false,
        ...extra
    };
}

const OPTIONS = { accent: ['default', 'gold', 'coral', 'crimson', 'teal', 'sky', 'violet', 'emerald'], background: ['default', 'plain', 'subtle', 'rich'] };

// The scene inspector with a fake composition adapter (the real one is wired in index.html to AadhiCinematic)
function inspector(slides, adapterExtra = {}, { api = {}, debug = false } = {}) {
    const doc = fakeDoc();
    const calls = [];
    const seen = [];
    const session = new R.ReviewSession({ slides, api, media: {}, composition: {
        enabled: () => true,
        facts: () => [['Layout', 'Comparison']],
        summary: () => ({ source: 'rules', label: 'Automatic ✓', template: 'Comparison', reasons: [], repairs: [], ai: null, locked: [], chosen: [] }),
        statuses: () => [],
        review: async body => {
            calls.push(['review', body]);
            const overrides = { ...((slides[body.scene_index].visual_review || {}).composition || {}).overrides, ...body.overrides };
            Object.keys(overrides).forEach(k => { if (overrides[k] === 'auto') delete overrides[k]; });
            return { review: Object.keys(overrides).length ? { status: 'changed', overrides } : null,
                plan: { ...slides[body.scene_index].cinematic_plan, review_status: Object.keys(overrides).length ? 'changed' : 'pending' } };
        },
        options: { camera: { static: 'Still', focus: 'Focus' } },
        syncSummary: () => ({ source: 'narration', timing: 'follows the narration audio', moments: [], keyMoments: [], attention: '', notes: [], endHold: null, ai: null }),
        styleSummary: (scene, p) => { seen.push([scene, p]); return summary(); },
        ...adapterExtra } });
    const panel = new R.VisualReviewPanel({ doc, session, pickAsset: () => {}, showInLesson: () => {}, debug });
    panel.build();
    panel.render();
    return { doc, session, panel, calls, seen };
}

const controls = node => node.querySelectorAll('button').length + node.querySelectorAll('select').length + node.querySelectorAll('input').length;
const optionValues = select => select.children.map(o => o.attrs.value);

// ---- the Style block -------------------------------------------------------------------------------------------------

test('the inspector shows a read-only Style block under the synchronization: label, version, choices, adjustments, colours', () => {
    const slides = [{ title: 'Plants and animals', cinematic_plan: plan() }];
    const { panel, seen } = inspector(slides);
    const block = panel.detail.querySelector('.review-style');
    assert.ok(block);
    // asked with the scene and its plan
    assert.equal(seen[0][0], slides[0]);
    assert.equal(seen[0][1], slides[0].cinematic_plan);
    // placed after the synchronization, above the composition block
    const order = panel.detail.children.map(c => c.className);
    assert.ok(order.indexOf('review-sync') >= 0 && order.indexOf('review-sync') < order.indexOf('review-style'), 'after the synchronization');
    assert.ok(order.indexOf('review-style') < order.indexOf('review-composition-decided'), 'above the composition block');
    assert.equal(block.querySelector('.review-style-auto').textContent, 'Style: Academic (v1)');
    assert.equal(block.getAttribute('data-style'), 'academic');
    assert.equal(block.getAttribute('data-tone'), 'light');
    assert.equal(block.getAttribute('data-fingerprint'), '0123456789abcdef');
    assert.equal(block.getAttribute('data-legacy'), null);
    // the lesson's choices and this scene's own, in plain words
    const choices = block.querySelectorAll('li');
    assert.deepEqual(choices.map(li => li.textContent),
        ['Accent colour: Teal', 'Background: Plain', 'Text size: Large', 'Accent colour: Coral (this scene only)']);
    assert.deepEqual(choices.map(li => li.getAttribute('data-scope')), ['lesson', 'lesson', 'lesson', 'scene']);
    assert.deepEqual(choices.map(li => li.getAttribute('data-key')), ['accent', 'background', 'text_size', 'accent']);
    // accessibility adjustments as notes
    const notes = block.querySelector('.review-style-notes');
    assert.ok(notes);
    assert.deepEqual(notes.querySelectorAll('p').map(p => p.textContent),
        ['Kept readable: body text made darker or lighter to stay readable', 'Kept readable: captions made darker or lighter to stay readable']);
    // a small row of the style's colours
    const swatch = block.querySelector('.review-style-swatch');
    assert.ok(swatch);
    const chips = swatch.querySelectorAll('span');
    assert.deepEqual(chips.map(c => c.style.backgroundColor), ['#f4efe3', '#9b2335', '#fff']);
    assert.match(swatch.getAttribute('aria-label'), /Academic/);
    // read-only: nothing to press or pick inside the block
    assert.equal(controls(block), 0);
    assert.equal(block.querySelector('[data-action]'), null);
    // the composition's own controls are still there
    for (const action of ['keep', 'change', 'apply']) assert.ok(panel.detail.querySelector(`[data-action="${action}"]`), action);
});

test('legacy wording for a lesson saved before styles; a style with no choices or adjustments shows only its name', () => {
    const { panel } = inspector([{ title: 'Old lesson', cinematic_plan: plan() }], {
        styleSummary: () => summary({ id: 'cinematic_education@1', label: 'Cinematic Education', family: 'cinematic_education', tone: 'dark',
            legacy: true, overrides: [], sceneOverrides: [], adjustments: [], swatch: ['#140a2e', '#FFD700'] }) });
    const block = panel.detail.querySelector('.review-style');
    assert.equal(block.querySelector('.review-style-auto').textContent, "Style: Cinematic Education (v1) · the lesson's original look");
    assert.equal(block.getAttribute('data-legacy'), 'true');
    assert.equal(block.getAttribute('data-tone'), 'dark');
    assert.equal(block.querySelector('li'), null);
    assert.equal(block.querySelector('.review-style-notes'), null);
    assert.equal(block.querySelectorAll('.review-style-swatch span').length, 2);
    // "legacy" means exactly true; anything else is a chosen style
    const chosen = inspector([{ title: 'x', cinematic_plan: plan() }], { styleSummary: () => summary({ legacy: 'yes' }) });
    assert.equal(chosen.panel.detail.querySelector('.review-style-auto').textContent, 'Style: Academic (v1)');
    // a "default" value means "not set": never listed
    const defaults = inspector([{ title: 'x', cinematic_plan: plan() }], { styleSummary: () => summary({ overrides: [['accent', 'default']], sceneOverrides: [] }) });
    assert.equal(defaults.panel.detail.querySelector('.review-style li'), null);
});

test('no style summary, no block: Classic, an older plan, an older page, a failing adapter', () => {
    const scene = () => [{ title: 'x', cinematic_plan: plan() }];
    assert.equal(inspector(scene(), { styleSummary: () => null }).panel.detail.querySelector('.review-style'), null);
    assert.equal(inspector(scene(), { styleSummary: undefined }).panel.detail.querySelector('.review-style'), null);
    assert.equal(inspector(scene(), { styleSummary: () => 'Academic' }).panel.detail.querySelector('.review-style'), null);
    assert.equal(inspector(scene(), { styleSummary: () => [summary()] }).panel.detail.querySelector('.review-style'), null);
    const failing = inspector(scene(), { styleSummary: () => { throw new Error('boom'); } });
    assert.equal(failing.panel.detail.querySelector('.review-style'), null);
    assert.ok(failing.panel.detail.querySelector('[data-action="keep"]'), 'the inspector still works');
    // renderStyle itself: nothing for nothing
    assert.equal(failing.panel.renderStyle(failing.session.current, null), null);
    // the block follows the plan: a decision that brings a plan without a look takes the block away
    let look = summary();
    const live = inspector(scene(), { styleSummary: () => look });
    assert.ok(live.panel.detail.querySelector('.review-style'));
    look = null;
    live.panel.render();
    assert.equal(live.panel.detail.querySelector('.review-style'), null);
});

test('only #rgb / #rrggbb colours reach the swatch; the rest is skipped', () => {
    const { panel } = inspector([{ title: 'x', cinematic_plan: plan() }], { styleSummary: () => summary({
        swatch: ['#abc', 'red', 'url(x)', '#12345', '#1234567', 'rgb(0, 0, 0)', '#a1b2c3', ' #ABCDEF ', 42, null, { c: '#fff' },
            '#fff;background:url(x)', 'expression(alert(1))', '#ggg'] }) });
    const chips = panel.detail.querySelectorAll('.review-style-swatch span');
    assert.deepEqual(chips.map(c => c.style.backgroundColor), ['#abc', '#a1b2c3', '#ABCDEF']);
    assert.deepEqual(chips.map(c => c.getAttribute('data-colour')), ['#abc', '#a1b2c3', '#ABCDEF']);
    // no valid colour: no swatch row at all; a swatch that is not a list: none either
    const none = inspector([{ title: 'x', cinematic_plan: plan() }], { styleSummary: () => summary({ swatch: ['red', 'url(a.png)'] }) });
    assert.equal(none.panel.detail.querySelector('.review-style-swatch'), null);
    const odd = inspector([{ title: 'x', cinematic_plan: plan() }], { styleSummary: () => summary({ swatch: '#fff' }) });
    assert.equal(odd.panel.detail.querySelector('.review-style-swatch'), null);
});

test('strings with markup stay text; odd values never become attributes', () => {
    const { panel } = inspector([{ title: 'x', cinematic_plan: plan() }], { styleSummary: () => summary({
        label: '<img src=x onerror=alert(1)>', version: '1; drop', family: '"><script>x</script>', tone: 'neon', fingerprint: 'not a <hash>',
        overrides: [['<b>accent</b>', '<script>alert(1)</script>'], ['background', { toString: () => 'x' }], 'nope', [null, 'teal']],
        sceneOverrides: [['background', '<i>rich</i>']],
        adjustments: ['<em>body</em> text made darker', 7, null, '<em>body</em> text made darker'] }) });
    const block = panel.detail.querySelector('.review-style');
    assert.equal(block.querySelector('.review-style-auto').textContent, 'Style: <img src=x onerror=alert(1)>');
    assert.deepEqual(block.querySelectorAll('li').map(li => li.textContent),
        ['<b>accent</b>: <script>alert(1)</script>', 'Background: <i>rich</i> (this scene only)']);
    assert.deepEqual(block.querySelectorAll('.review-style-notes p').map(p => p.textContent), ['Kept readable: <em>body</em> text made darker']);
    // only the elements the block builds: no markup was parsed, nothing went through innerHTML
    const tags = new Set(block.all().filter(n => n.tag !== '#text').map(n => n.tag));
    assert.deepEqual([...tags].sort(), ['div', 'li', 'p', 'span', 'ul']);
    assert.ok(block.all().every(n => n._html === undefined));
    for (const name of ['data-style', 'data-tone', 'data-fingerprint']) assert.equal(block.getAttribute(name), null, name);
    assert.equal(block.querySelectorAll('li')[0].getAttribute('data-key'), null);
});

test('the debug view names the scene\'s style', () => {
    const { panel } = inspector([{ title: 'x', cinematic_plan: plan() }], {}, { debug: true });
    const debug = JSON.parse(panel.detail.querySelector('.review-debug').textContent);
    assert.deepEqual(debug.style, { id: 'academic@1', fingerprint: '0123456789abcdef' });
});

// ---- the scene's own accent and background in the "Change" form ----------------------------------------------------------

test('the Change form offers "Scene accent" and "Scene background" only when the page knows the choices', () => {
    const scene = () => [{ title: 'x', cinematic_plan: plan() }];
    // no styleOptions (an older page), or none known yet: no selects
    for (const extra of [{}, { styleOptions: () => null }, { styleOptions: () => ({}) }, { styleOptions: () => { throw new Error('x'); } },
        { styleOptions: () => ({ accent: ['default'], background: 'plain' }) }]) {
        const { panel } = inspector(scene(), extra);
        assert.equal(panel.detail.querySelector('#composition-style_accent'), null);
        assert.equal(panel.detail.querySelector('#composition-style_background'), null);
        assert.ok(panel.detail.querySelector('#composition-camera'), 'the composition selects stay');
    }
    const { panel } = inspector(scene(), { styleOptions: () => OPTIONS });
    const accent = panel.detail.querySelector('#composition-style_accent');
    const background = panel.detail.querySelector('#composition-style_background');
    assert.ok(accent && background);
    assert.ok(panel.detail.querySelector('.review-composition-change #composition-style_accent'), 'inside the Change form');
    assert.equal(accent.getAttribute('data-key'), 'style_accent');
    assert.equal(accent.getAttribute('aria-label'), 'Scene accent');
    assert.equal(background.getAttribute('aria-label'), 'Scene background');
    assert.deepEqual(optionValues(accent), OPTIONS.accent);
    assert.deepEqual(optionValues(background), OPTIONS.background);
    assert.equal(accent.children[0].textContent, 'Scene accent: Same as the lesson');
    assert.equal(accent.children[4].textContent, 'Scene accent: Teal');
    assert.deepEqual(background.children.map(o => o.textContent),
        ['Scene background: Same as the lesson', 'Scene background: Plain', 'Scene background: Subtle', 'Scene background: Rich']);
    // nothing chosen for the scene: "Same as the lesson" is selected
    assert.equal(accent.value, 'default');
    assert.equal(accent.children[0].getAttribute('selected'), '');
    assert.equal(background.value, 'default');
    // the selects sit before Apply
    const form = panel.detail.querySelector('.review-composition-change');
    const keys = form.children.map(c => c.getAttribute('data-key') || c.getAttribute('data-action'));
    assert.ok(keys.indexOf('style_background') < keys.indexOf('apply'));
    // an object works as well as a function; "default" is always offered first, unknown words are dropped
    const plain = inspector(scene(), { styleOptions: { accent: ['teal', 'gold', 'teal', '<b>x</b>'] } });
    assert.deepEqual(optionValues(plain.panel.detail.querySelector('#composition-style_accent')), ['default', 'teal', 'gold']);
    assert.equal(plain.panel.detail.querySelector('#composition-style_background'), null);
});

test('the scene\'s own choices are preselected', () => {
    const slides = [{ title: 'x', cinematic_plan: plan({ review_status: 'changed' }),
        visual_review: { composition: { status: 'changed', overrides: { camera: 'focus', style_accent: 'teal', style_background: 'plain' } } } }];
    const { panel } = inspector(slides, { styleOptions: () => OPTIONS });
    const accent = panel.detail.querySelector('#composition-style_accent');
    const background = panel.detail.querySelector('#composition-style_background');
    assert.equal(accent.value, 'teal');
    assert.equal(background.value, 'plain');
    const teal = accent.children.find(o => o.attrs.value === 'teal');
    assert.equal(teal.getAttribute('selected'), '');
    assert.equal(teal.textContent, 'Scene accent: Teal (this scene)');
    assert.equal(accent.children[0].getAttribute('selected'), null);
    // an unknown saved value: "Same as the lesson" is shown (the server ignores it as well)
    const odd = inspector([{ title: 'x', cinematic_plan: plan(), visual_review: { composition: { status: 'changed', overrides: { style_accent: 'neon' } } } }],
        { styleOptions: () => OPTIONS });
    assert.equal(odd.panel.detail.querySelector('#composition-style_accent').value, 'default');
});

test('Apply sends style_accent / style_background through the composition review; "Same as the lesson" is never sent as a value', async () => {
    const slides = [{ title: 'x', cinematic_plan: plan() }];
    const { panel, calls, session } = inspector(slides, { styleOptions: () => OPTIONS });
    const apply = () => panel.detail.querySelector('[data-action="apply"]').fire('click');
    // nothing picked: nothing sent
    apply();
    await tick();
    assert.equal(calls.length, 0);
    assert.match(session.message, /Nothing to change/);
    // a new accent; the background stays "Same as the lesson" and is omitted
    panel.detail.querySelector('#composition-style_accent').value = 'teal';
    apply();
    await tick();
    assert.deepEqual(calls[0], ['review', { scene_index: 0, action: 'change', overrides: { style_accent: 'teal' } }]);
    assert.equal(session.message, "Changed: this scene's style (nothing was generated; the visuals keep their review).");
    assert.deepEqual(slides[0].visual_review.composition.overrides, { style_accent: 'teal' });
    // drawn again with the saved choice selected
    panel.render();
    assert.equal(panel.detail.querySelector('#composition-style_accent').value, 'teal');
    // back to the lesson's accent: the scene's choice is given back ("auto" removes it), never "default"
    panel.detail.querySelector('#composition-style_accent').value = 'default';
    panel.detail.querySelector('#composition-style_background').value = 'rich';
    apply();
    await tick();
    assert.deepEqual(calls[1], ['review', { scene_index: 0, action: 'change', overrides: { style_accent: 'auto', style_background: 'rich' } }]);
    assert.deepEqual(slides[0].visual_review.composition.overrides, { style_background: 'rich' });
    assert.ok(calls.every(([, body]) => !Object.values(body.overrides).includes('default')));
    // a value that is not offered is never sent
    panel.render();
    panel.detail.querySelector('#composition-style_accent').value = 'neon';
    apply();
    await tick();
    assert.equal(calls.length, 2);
    // with a composition change, the composition's own message
    panel.render();
    panel.detail.querySelector('#composition-camera').value = 'focus';
    panel.detail.querySelector('#composition-style_accent').value = 'gold';
    apply();
    await tick();
    assert.deepEqual(calls[2], ['review', { scene_index: 0, action: 'change', overrides: { camera: 'focus', style_accent: 'gold' } }]);
    assert.equal(session.message, 'Changed: the scene is composed your way.');
});

test('a style change is a composition decision, never a visual one: the visual keeps its review', async () => {
    const mediaCalls = [];
    const slides = [{ title: 'x', cinematic_plan: plan({ review_status: 'approved' }),
        visual_plan: { main: { source: 'ASSET', selection: 'approved', review_status: 'approved', asset_id: 7, url: '/media/a.png', media: 'STATIC_IMAGE' } },
        visual_review: { main: { status: 'approved', asset_id: 7 }, composition: { status: 'approved' } } }];
    const before = JSON.stringify({ plan: slides[0].visual_plan, review: slides[0].visual_review.main });
    const { panel, session, calls } = inspector(slides, { styleOptions: () => OPTIONS },
        { api: { review: async body => { mediaCalls.push(body); return { review: null, plans: {} }; } } });
    // the media item comes first: no style block or style select in a visual's detail
    assert.equal(session.current.slot, 'main');
    assert.equal(panel.detail.querySelector('.review-style'), null);
    assert.equal(panel.detail.querySelector('#composition-style_accent'), null);
    const approvedBefore = session.summary.approved;
    // to the composition, change the scene's accent
    assert.ok(session.next());
    panel.render();
    assert.equal(session.current.slot, 'composition');
    panel.detail.querySelector('#composition-style_accent').value = 'emerald';
    panel.detail.querySelector('[data-action="apply"]').fire('click');
    await tick();
    assert.equal(calls.length, 1);
    assert.equal(mediaCalls.length, 0, 'the visual review endpoint is never asked');
    assert.equal(JSON.stringify({ plan: slides[0].visual_plan, review: slides[0].visual_review.main }), before, 'the visual and its review are untouched');
    const main = session.all.find(i => i.slot === 'main');
    assert.equal(main.status, 'approved');
    assert.equal(main.stale, false);
    assert.equal(session.all.find(i => i.slot === 'composition').status, 'changed');
    assert.equal(session.summary.approved, approvedBefore - 1, 'only the composition left "approved" (it is now "changed")');
    assert.ok(R.filterItems(session.all, 'approved').some(i => i.slot === 'main'));
});
