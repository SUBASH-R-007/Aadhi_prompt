'use strict';
// Unit tests for the Library panel (assets.js, Phase 21): Aadhi's shared assets and your own items asked as separate
// pages (so a library with hundreds of uploads still shows the shared ones), narration clips hidden by default, plain
// words with the technical details only in debug mode (?visualDebug), and keyboard use of the dialog. Checked over the
// small fake DOM the other UI tests use. Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const { AssetLibraryPanel, OWN_PAGE, SHARED_PAGE, plainReason } = require('../assets.js');
const { Node, fakeDoc } = require('./helpers/cinematic-dom.js');

// What the fake DOM lacks for these tests: focus, containment, media and scrolling
const scrolled = [];
Node.prototype.focus = function () { this.doc.activeElement = this; };
Node.prototype.contains = function (node) { for (let n = node; n; n = n.parent) if (n === this) return true; return false; };
Node.prototype.pause = function () {};
Node.prototype.scrollIntoView = function () { scrolled.push(this.getAttribute('data-id')); };
Node.prototype.remove = function () { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); };

const tick = () => new Promise(resolve => setImmediate(resolve));
const at = (day, n = 0) => `2026-${day}T10:00:${String(n % 60).padStart(2, '0')}.${String(n).padStart(6, '0')}`;
const sharedAsset = (id, file_name, kind, source, n) => ({ id, file_name, kind, source, scope: 'system', owned: false, status: 'ready',
    mime_type: kind === 'image' ? 'image/jpeg' : kind === 'audio' ? 'audio/mpeg' : 'video/mp4', sha256: 'f'.repeat(64), file_size: 2048, created_at: at('01-01', n) });
const ownAsset = (id, file_name, kind, source, created_at) => ({ id, file_name, kind, source, scope: 'private', owned: true, status: 'ready',
    mime_type: kind === 'image' ? 'image/png' : kind === 'audio' ? 'audio/mpeg' : 'video/mp4', sha256: 'e'.repeat(64), file_size: 4096, created_at });

// The 13 shared assets the server registers at its first start (assets.py SYSTEM_ASSETS)
const SHARED = [
    ...['left', 'right', 'center', 'popup'].map((p, i) => sharedAsset(`s-${p}`, `aadhi_${p}.mp4`, 'video', 'mascot', i)),
    sharedAsset('s-none', 'no_aadhi.mp4', 'video', 'mascot', 4),
    ...['left', 'right', 'center', 'popup'].map((p, i) => sharedAsset(`s-poster-${p}`, `aadhi_${p}.jpg`, 'image', 'mascot', 5 + i)),
    sharedAsset('s-poster-none', 'no_aadhi.jpg', 'image', 'mascot', 9),
    sharedAsset('s-bg', 'static_background.png', 'image', 'system', 10),
    sharedAsset('s-logo', 'logo_animation.mp4', 'video', 'system', 11),
    sharedAsset('s-music', 'bgm.mp3', 'audio', 'system', 12)
];
// A teacher after a few lessons: three uploads, then dozens of narration clips (newer than the uploads)
const UPLOADS = [
    ownAsset('u-diagram', 'beam-diagram.png', 'image', 'upload', at('09-01', 1)),
    ownAsset('u-clip', 'crane-at-work.mp4', 'video', 'upload', at('09-02', 2)),
    ownAsset('u-sound', 'bell.mp3', 'audio', 'upload', at('09-03', 3))
];
const NARRATION = Array.from({ length: 60 }, (_, i) => ownAsset(`n-${i}`, `audio_${i}.mp3`, 'audio', 'narration', at('10-01', i)));

// The server's list endpoint (assets.py list_assets): scope, kind, source and search filters, newest first, a capped page
function fakeApi({ shared = SHARED, mine = [...UPLOADS, ...NARRATION], failShared = null, failMine = null } = {}) {
    const calls = [];
    const rows = { system: shared, mine };
    const api = {
        calls,
        rows,
        async list(params) {
            calls.push({ ...params });
            if (params.scope === 'system' && failShared) throw failShared;
            if (params.scope === 'mine' && failMine) throw failMine;
            const found = rows[params.scope].filter(a => (!params.kind || a.kind === params.kind) && (!params.source || a.source === params.source)
                && (!params.q || a.file_name.includes(params.q)))
                .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
            return { assets: found.slice(0, Math.min(params.limit || 100, 200)), total: found.length };
        },
        async get(id) {
            const asset = [...rows.system, ...rows.mine].find(a => a.id === id);
            return { ...asset, references: id === 'u-diagram' ? 1 : 0, deletable: id !== 'u-diagram' && asset.owned, used_in: [] };
        },
        async resolve(ids) { return { assets: Object.fromEntries(ids.map(id => [id, { url: `/api/assets/${id}/content?token=t` }])), missing: [] }; },
        async update(id, fields) { return { id, details: fields }; },
        async remove() { return { deleted: true }; },
        async upload(file) {
            const asset = ownAsset('u-new', file.name, 'image', 'upload', at('11-01', 0));
            rows.mine = [...rows.mine, asset];
            return { asset, deduplicated: false };
        }
    };
    return api;
}

function keyboardDoc() {
    const doc = fakeDoc();
    const listeners = [];
    doc.addEventListener = (type, fn) => listeners.push({ type, fn });
    doc.removeEventListener = (type, fn) => {
        const i = listeners.findIndex(l => l.type === type && l.fn === fn);
        if (i >= 0) listeners.splice(i, 1);
    };
    doc.press = (key, shift = false) => {
        const e = { key, shiftKey: shift, target: doc.activeElement, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
        listeners.filter(l => l.type === 'keydown').slice().forEach(l => l.fn(e));
        return e;
    };
    doc.activeElement = doc.body;
    return doc;
}

async function openPanel({ api = fakeApi(), debug, pick, confirm } = {}) {
    const doc = keyboardDoc();
    const panel = new AssetLibraryPanel({ doc, api, clipboard: { writeText: async () => {} }, debug, confirm });
    await panel.open(pick ? { pick } : {});
    return { doc, panel, api };
}

const items = panel => panel.list.querySelectorAll('.asset-item');
const ids = panel => items(panel).map(i => i.getAttribute('data-id'));
const heads = panel => panel.list.querySelectorAll('.asset-group-head').map(h => h.textContent);
const ownCalls = api => api.calls.filter(c => c.scope === 'mine');
const sharedCalls = api => api.calls.filter(c => c.scope === 'system');
const query = call => Object.fromEntries(Object.entries(call).filter(([, v]) => v !== '' && v !== undefined));
const statusText = panel => panel.status.textContent;

test('the Library opens with Aadhi\'s shared assets first, then your own items, asked as separate pages', async () => {
    const { panel, api } = await openPanel();
    assert.deepEqual(sharedCalls(api).map(query), [{ scope: 'system', limit: SHARED_PAGE }]);
    assert.equal(SHARED_PAGE, 50);
    assert.equal(OWN_PAGE, 200);
    assert.deepEqual(heads(panel), ['Shared Aadhi assets (13)', 'Your files (3)']);
    const listed = items(panel);
    assert.equal(listed.length, 16);
    assert.ok(listed.slice(0, 13).every(i => i.getAttribute('data-scope') === 'system' && i.querySelector('.asset-badge').textContent === 'Shared'));
    // your own items newest first after them
    assert.deepEqual(ids(panel).slice(13), ['u-sound', 'u-clip', 'u-diagram']);
    assert.equal(statusText(panel), '');
    assert.equal(panel.list.getAttribute('aria-busy'), null);
});

test('narration clips are hidden by default, one choice away, and never crowd out your pictures and clips', async () => {
    const { panel, api } = await openPanel();
    assert.equal(panel.kindSelect.value, 'no-narration');
    assert.deepEqual(panel.kindSelect.querySelectorAll('option').map(o => o.textContent), ['All except narration', 'All files', 'Pictures', 'Videos', 'Sounds']);
    // one request per kind: your sounds from uploads only, so the 60 newer narration clips cannot fill the page
    assert.deepEqual(ownCalls(api).map(query), [
        { kind: 'image', scope: 'mine', limit: 200 }, { kind: 'video', scope: 'mine', limit: 200 },
        { kind: 'audio', source: 'upload', scope: 'mine', limit: 200 }]);
    assert.ok(!ids(panel).some(id => id.startsWith('n-')));

    // "All files": everything, newest first, in one request
    api.calls.length = 0;
    panel.kindSelect.value = '';
    await panel.refresh();
    assert.deepEqual(ownCalls(api).map(query), [{ scope: 'mine', limit: 200 }]);
    assert.equal(ids(panel).filter(id => id.startsWith('n-')).length, 60);
    assert.deepEqual(heads(panel), ['Shared Aadhi assets (13)', 'Your files (63)']);

    // Source: Narration shows them even with the default type
    api.calls.length = 0;
    panel.kindSelect.value = 'no-narration';
    panel.sourceSelect.value = 'narration';
    await panel.refresh();
    assert.deepEqual(ownCalls(api).map(query), [{ source: 'narration', scope: 'mine', limit: 200 }]);
    assert.equal(items(panel).length, 60);
    assert.deepEqual(heads(panel), ['Your files (60)']); // no shared asset is narration
});

test('a library with hundreds of uploads still shows the 13 shared assets, and says when older items are not listed', async () => {
    const many = Array.from({ length: 300 }, (_, i) => ownAsset(`p-${i}`, `photo-${i}.png`, 'image', 'upload', at('08-01', i)));
    const { panel } = await openPanel({ api: fakeApi({ mine: [...many, ...NARRATION] }) });
    assert.equal(items(panel).filter(i => i.getAttribute('data-scope') === 'system').length, 13);
    const own = ids(panel).slice(13);
    assert.equal(own.length, 200);
    assert.equal(own[0], 'p-299'); // newest first
    assert.deepEqual(heads(panel), ['Shared Aadhi assets (13)', 'Your files (200 of 300)']);
    assert.equal(statusText(panel), 'Showing your newest 200 of 300 files. Search or filter to find older ones.');
});

test('the type, source and search go to both requests, so a search narrows the whole list', async () => {
    const { panel, api } = await openPanel();
    api.calls.length = 0;
    panel.kindSelect.value = 'video';
    panel.sourceSelect.value = 'mascot';
    await panel.refresh();
    assert.deepEqual(sharedCalls(api).map(query), [{ kind: 'video', source: 'mascot', scope: 'system', limit: 50 }]);
    assert.deepEqual(ownCalls(api).map(query), [{ kind: 'video', source: 'mascot', scope: 'mine', limit: 200 }]);
    assert.equal(items(panel).length, 5); // Aadhi's five clips
    assert.ok(items(panel).every(i => i.getAttribute('data-kind') === 'video'));

    api.calls.length = 0;
    panel.kindSelect.value = 'no-narration';
    panel.sourceSelect.value = '';
    panel.search.value = '  bell ';
    await panel.refresh();
    assert.ok(api.calls.every(c => c.q === 'bell'));
    assert.deepEqual(ids(panel), ['u-sound']);
    assert.deepEqual(heads(panel), ['Your files (1)']);
});

test('choosing a visual for a scene lists your own items first, only the kinds the scene can show', async () => {
    const picked = [];
    const { panel, api } = await openPanel({ pick: { kinds: ['image'], title: 'Choose a picture', onPick: a => picked.push(a.id) } });
    assert.equal(panel.titleEl.textContent, 'Choose a picture');
    assert.deepEqual(ownCalls(api).map(query), [{ kind: 'image', scope: 'mine', limit: 200 }]);
    assert.deepEqual(heads(panel), ['Your files (1)', 'Shared Aadhi assets (6)']);
    assert.equal(ids(panel)[0], 'u-diagram');
    assert.ok(items(panel).every(i => i.getAttribute('data-kind') === 'image'));
    await panel.select(panel.assets[0]);
    panel.detail.querySelector('.asset-pick').fire('click');
    assert.deepEqual(picked, ['u-diagram']);
    assert.ok(!panel.root.classList.contains('open'));
});

test('when the shared assets fail your own items still show; failures are explained in plain words with Try again', async () => {
    const down = Object.assign(new Error('The server had a problem (error 503).'), { status: 503, data: { detail: 'Library temporarily unavailable' } });
    const sharedDown = await openPanel({ api: fakeApi({ failShared: down }) });
    assert.deepEqual(ids(sharedDown.panel), ['u-sound', 'u-clip', 'u-diagram']);
    assert.equal(sharedDown.panel.status.getAttribute('data-kind'), 'warn');
    assert.match(statusText(sharedDown.panel), /^Aadhi's shared assets could not be loaded right now; your own files are below\./);

    const mineDown = await openPanel({ api: fakeApi({ failMine: down }) });
    assert.equal(items(mineDown.panel).length, 13);
    assert.equal(mineDown.panel.status.getAttribute('data-kind'), 'error');
    assert.match(statusText(mineDown.panel), /^Your own files could not be loaded\. Library temporarily unavailable\. Aadhi's shared assets are below\./);

    const api = fakeApi({ failShared: down, failMine: down });
    const both = await openPanel({ api });
    assert.equal(items(both.panel).length, 0);
    assert.equal(statusText(both.panel), 'Your library could not be loaded. Library temporarily unavailable. Your files are safe. Try again');
    assert.doesNotMatch(statusText(both.panel), /error 503|\[503\]/); // no status codes for people
    const retry = both.panel.status.querySelector('.asset-status-action');
    assert.equal(retry.textContent, 'Try again');
    api.calls.length = 0;
    retry.fire('click');
    await tick();
    assert.ok(api.calls.length >= 2);

    // With ?visualDebug (or the debug option) the raw detail follows
    const debugged = await openPanel({ api: fakeApi({ failShared: down, failMine: down }), debug: true });
    assert.match(statusText(debugged.panel), /Your files are safe\. \(\[503\] The server had a problem \(error 503\)\.\) Try again$/);

    // plain reasons: no connection, an expired login, the server's own sentence without its technical part
    assert.equal(plainReason({ status: 0 }), 'The server could not be reached. Check your connection.');
    assert.equal(plainReason({ status: 401 }), 'Your login has expired. Please log in again.');
    assert.equal(plainReason({ status: 400, data: { detail: 'This file cannot be added to the library: the file is damaged or unreadable (moov atom not found).' } }),
        'This file cannot be added to the library: the file is damaged or unreadable.');
    assert.equal(plainReason({ status: 409, data: { detail: { message: 'This asset is used by 1 saved lesson item(s), so it was kept.' } } }),
        'This asset is used by 1 saved lesson item(s), so it was kept.');
    assert.equal(plainReason({ status: 500, data: null }), 'The server had a problem.');
});

test('empty states say what will appear and what to do', async () => {
    const { panel } = await openPanel({ api: fakeApi({ shared: [], mine: [] }) });
    assert.equal(panel.list.querySelector('.asset-empty').textContent,
        'Your reusable visuals will appear here. Upload a picture or clip, or let Aadhi make them for your lessons.');
    panel.search.value = 'zz-no-such-asset-zz';
    await panel.refresh();
    assert.match(panel.list.querySelector('.asset-empty').textContent, /^No assets match this search\. Try other words/);
    panel.search.value = '';
    panel.kindSelect.value = 'video';
    await panel.refresh();
    assert.match(panel.list.querySelector('.asset-empty').textContent, /^No assets match these filters\./);
    assert.equal(panel.titleEl.textContent, 'Library');
});

test('the detail is in plain words; MIME type, raw status, hash, asset ID and the copy buttons only in debug mode', async () => {
    const { panel } = await openPanel();
    await panel.select(SHARED[0]);
    const text = panel.detail.textContent;
    assert.match(text, /TypeVideo/);
    assert.match(text, /Shared with everyone/);
    for (const hidden of ['video/mp4', 'Content hash', 'Asset ID', 's-left', 'Status']) assert.ok(!text.includes(hidden), hidden);
    assert.ok(!panel.detail.querySelectorAll('button').some(b => /Copy/.test(b.textContent)));
    assert.equal(panel.detail.querySelector('.asset-delete'), null); // shared: read-only

    await panel.select(UPLOADS[0]);
    assert.match(panel.detail.textContent, /Used by1 saved lesson/);
    assert.equal(panel.detail.querySelector('.asset-delete').disabled, true);
    assert.equal(panel.detail.querySelector('.asset-edit-meta').textContent, 'Add a description');

    panel.debug = true;
    await panel.select(SHARED[0]);
    const technical = panel.detail.textContent;
    for (const shown of ['Video (video/mp4)', 'Content hash', 'Asset IDs-left', 'Statusready']) assert.ok(technical.includes(shown), shown);
    assert.deepEqual(panel.detail.querySelectorAll('button').map(b => b.textContent), ['Copy asset ID']);
    await panel.select(UPLOADS[0]);
    assert.deepEqual(panel.detail.querySelectorAll('.asset-actions button').map(b => b.textContent), ['Copy asset ID', 'Copy image tag', 'Delete']);

    // ?visualDebug in the page's address turns it on when no option is given
    const doc = keyboardDoc();
    doc.location = { search: '?visualDebug=1' };
    assert.equal(new AssetLibraryPanel({ doc, api: fakeApi() }).isDebug(), true);
    doc.location = { search: '?project_id=4' };
    assert.equal(new AssetLibraryPanel({ doc, api: fakeApi() }).isDebug(), false);
});

test('an AI request is named in plain words, and saving the words says "Details saved" (Phase 21)', async () => {
    const made = { ...ownAsset('u-ai', 'ai_image_bc96e36f7fae.jpg', 'image', 'ai', at('09-04', 4)), details: { prompt: 'a leaf in sunlight' } };
    const { panel } = await openPanel({ api: fakeApi({ mine: [...UPLOADS, made] }) });
    await panel.select(made);
    assert.match(panel.detail.textContent, /Description used to make ita leaf in sunlight/);
    assert.ok(!panel.detail.textContent.includes('Prompt'));
    await panel.saveWords(made, 'A green leaf in the sun', 'leaf, sun', { disabled: false });
    assert.equal(statusText(panel), '✓ Details saved: lessons can now find it by these words.');
    assert.doesNotMatch(statusText(panel), /Metadata/);
});

test('a new upload is selected below the shared assets and brought into view, with its description editor open', async () => {
    const { panel, doc } = await openPanel();
    scrolled.length = 0;
    panel.fileInput.files = [{ name: 'new-diagram.png' }];
    panel.kindSelect.value = 'video';
    await panel.uploadSelected();
    assert.equal(panel.kindSelect.value, 'no-narration'); // filters back to the default
    const selected = panel.list.querySelector('.asset-item.selected');
    assert.equal(selected.getAttribute('data-id'), 'u-new');
    assert.equal(selected.getAttribute('aria-pressed'), 'true');
    assert.ok(ids(panel).indexOf('u-new') > 12); // after the 13 shared assets
    assert.deepEqual(scrolled, ['u-new']);
    assert.match(statusText(panel), /^new-diagram\.png was added to your library\. Describe it below/);
    assert.ok(doc.activeElement.classList.contains('asset-description-input'));
});

test('keyboard: the focus moves into the Library, Tab stays inside, Escape closes and the focus goes back', async () => {
    const doc = keyboardDoc();
    const opener = doc.createElement('button');
    doc.body.appendChild(opener);
    opener.focus();
    const panel = new AssetLibraryPanel({ doc, api: fakeApi(), clipboard: null });
    await panel.open();
    assert.equal(doc.activeElement, panel.titleEl);
    assert.equal(panel.titleEl.getAttribute('tabindex'), '-1');
    assert.equal(panel.panel.getAttribute('role'), 'dialog');
    assert.equal(panel.panel.getAttribute('aria-modal'), 'true');

    const close = panel.panel.querySelector('.export-close');
    assert.equal(close.getAttribute('aria-label'), 'Close the library');
    opener.focus(); // focus left behind the dialog: Tab brings it back in
    let e = doc.press('Tab');
    assert.ok(e.defaultPrevented);
    assert.equal(doc.activeElement, close); // the first control (the heading is not a Tab stop)
    e = doc.press('Tab', true); // Shift+Tab from the first control goes to the last
    assert.ok(e.defaultPrevented);
    const last = doc.activeElement;
    assert.ok(panel.panel.contains(last) && last.classList.contains('asset-item'));
    assert.equal(last.getAttribute('data-id'), ids(panel).at(-1));
    e = doc.press('Tab'); // and Tab from the last back to the first
    assert.ok(e.defaultPrevented);
    assert.equal(doc.activeElement, close);
    panel.search.focus(); // inside, not at an end: Tab is left to the browser
    assert.equal(doc.press('Tab').defaultPrevented, false);

    // the search field clears itself with the first Escape
    panel.search.value = 'bell';
    panel.search.focus();
    doc.press('Escape');
    assert.ok(panel.root.classList.contains('open'));
    panel.search.value = '';
    doc.press('Escape');
    assert.ok(!panel.root.classList.contains('open'));
    assert.equal(doc.activeElement, opener);
    // closed: keys are the page's again
    panel.titleEl.focus();
    assert.equal(doc.press('Tab').defaultPrevented, false);

    // while choosing a visual the search field gets the focus
    await panel.open({ pick: { kinds: ['image', 'video'], onPick: () => {}, onCancel: () => {} } });
    assert.equal(doc.activeElement, panel.search);
});
