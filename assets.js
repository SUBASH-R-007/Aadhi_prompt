/*
 * Asset library client: API calls, resolving a lesson's asset references to playable URLs, and
 * the Asset Library panel. The server side is assets.py.
 *
 * A scene can refer to library assets instead of file URLs:
 *   video_asset_id (ai_video)        -> video_url
 *   manim_asset_id (simulation)      -> manim_video_url
 *   side_panel.video_asset_id        -> side_panel.video_url
 *   uploaded_image_assets[imgId]     -> uploaded_images[imgId]
 *   <img src="asset:ID"> in html     -> <img src="URL" data-asset-src="asset:ID">
 * resolveLesson() fills in the URL fields, so the renderer and the video exporter keep using plain
 * URLs and never need to know where a file is stored. The asset IDs stay in the lesson.
 *
 * Loaded as a classic <script> (window.AadhiAssets) and as a CommonJS module by the Node unit tests
 * in tests/assets.test.js.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.AadhiAssets = api;
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const ID = '[0-9a-f]{32}';
    const MARKER = new RegExp(`asset:(${ID})`, 'g');
    // A standalone src attribute only (not the end of "data-asset-src")
    const IMG_MARKER = new RegExp(`(?<![\\w-])src=(["'])asset:(${ID})\\1`, 'g');
    const IMG_RESOLVED = new RegExp(`(?<![\\w-])src=(["'])[^"']*\\1(\\s+)data-asset-src=(["'])asset:(${ID})\\3`, 'g');

    function errorMessage(data, status) {
        const detail = data && data.detail;
        if (typeof detail === 'string') return detail;
        if (detail && typeof detail.message === 'string') return detail.message;
        if (status === 401) return 'Your login has expired. Please log in again.';
        if (status === 413) return 'The file is larger than the library accepts.';
        if (status >= 500) return `The server had a problem (error ${status}).`;
        return `The request failed (error ${status}).`;
    }

    class AssetApi {
        // fetch: the page's fetch (adds the login and server address); base()/token() are for the XHR upload
        constructor({ fetch, base = () => '', token = () => null, XMLHttpRequest }) {
            this.fetchFn = fetch;
            this.base = base;
            this.token = token;
            this.XHR = XMLHttpRequest;
        }

        async request(method, path, body) {
            const init = { method, headers: {} };
            if (body !== undefined) {
                init.headers['Content-Type'] = 'application/json';
                init.body = JSON.stringify(body);
            }
            let res;
            try {
                res = await this.fetchFn(path, init);
            } catch (e) {
                throw Object.assign(new Error('The server could not be reached. Check the connection and try again.'), { status: 0 });
            }
            const data = await res.json().catch(() => null);
            if (!res.ok) throw Object.assign(new Error(errorMessage(data, res.status)), { status: res.status, data });
            return data;
        }

        list(params = {}) {
            const query = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
                .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
            return this.request('GET', '/api/assets' + (query ? `?${query}` : ''));
        }

        get(id) { return this.request('GET', `/api/assets/${id}`); }
        remove(id) { return this.request('DELETE', `/api/assets/${id}`); }
        // { description, keywords } -> the updated asset
        update(id, fields) { return this.request('PATCH', `/api/assets/${id}`, fields); }

        // IDs -> { assets: { id: {..., url} }, missing: [...] }, with URLs ready to use in the page
        async resolve(ids) {
            if (!ids.length) return { assets: {}, missing: [] };
            const data = await this.request('POST', '/api/assets/resolve', { ids });
            Object.values(data.assets).forEach(asset => { asset.url = this.base() + asset.url; });
            return data;
        }

        // XHR rather than fetch: only XHR reports upload progress
        upload(file, onProgress = () => {}) {
            return new Promise((resolve, reject) => {
                const form = new FormData();
                form.append('file', file, file.name);
                const xhr = new this.XHR();
                xhr.open('POST', `${this.base()}/api/assets`);
                const token = this.token();
                if (token) xhr.setRequestHeader('Authorization', 'Bearer ' + token);
                xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
                xhr.onload = () => {
                    let data = null;
                    try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
                    if (xhr.status >= 200 && xhr.status < 300 && data) resolve(data);
                    else reject(Object.assign(new Error(errorMessage(data, xhr.status)), { status: xhr.status, data }));
                };
                xhr.onerror = () => reject(Object.assign(new Error('The upload connection was lost.'), { status: 0 }));
                xhr.send(form);
            });
        }
    }

    // ---- Lessons -----------------------------------------------------------------------

    function collectAssetIds(slides) {
        const ids = new Set();
        (slides || []).forEach(s => {
            if (!s || typeof s !== 'object') return;
            if (s.video_asset_id) ids.add(s.video_asset_id);
            if (s.manim_asset_id) ids.add(s.manim_asset_id);
            if (s.side_panel && s.side_panel.video_asset_id) ids.add(s.side_panel.video_asset_id);
            Object.values(s.uploaded_image_assets || {}).forEach(id => id && ids.add(id));
            if (typeof s.html === 'string') {
                for (const match of s.html.matchAll(MARKER)) ids.add(match[1]);
            }
        });
        return [...ids];
    }

    // Writes resolved URLs into the fields the renderer reads; asset IDs stay untouched
    function applyResolved(slides, resolved) {
        const url = id => resolved[id] && resolved[id].url;
        (slides || []).forEach(s => {
            if (!s || typeof s !== 'object') return;
            if (url(s.video_asset_id)) s.video_url = url(s.video_asset_id);
            if (url(s.manim_asset_id)) s.manim_video_url = url(s.manim_asset_id);
            if (s.side_panel && url(s.side_panel.video_asset_id)) s.side_panel.video_url = url(s.side_panel.video_asset_id);
            Object.entries(s.uploaded_image_assets || {}).forEach(([imgId, id]) => {
                if (!url(id)) return;
                s.uploaded_images = s.uploaded_images || {};
                s.uploaded_images[imgId] = url(id);
            });
            if (typeof s.html === 'string') {
                s.html = s.html
                    .replace(IMG_RESOLVED, (m, q, space, q2, id) => (url(id) ? `src=${q}${url(id)}${q}${space}data-asset-src=${q2}asset:${id}${q2}` : m))
                    .replace(IMG_MARKER, (m, q, id) => (url(id) ? `src=${q}${url(id)}${q} data-asset-src=${q}asset:${id}${q}` : m));
            }
        });
    }

    // Resolves every asset a lesson refers to. Resolves with the IDs that could not be used
    // (deleted, missing, not yours); those scenes keep whatever URL they already had.
    async function resolveLesson(slides, api) {
        const ids = collectAssetIds(slides);
        if (!ids.length) return { resolved: 0, missing: [] };
        const data = await api.resolve(ids);
        applyResolved(slides, data.assets);
        return { resolved: Object.keys(data.assets).length, missing: data.missing };
    }

    // ---- Description and keywords -----------------------------------------------------------
    // Lessons find library assets by these words (visual router, visuals.py). Same limits and
    // tidying as the server (assets.py), checked here so mistakes are explained before saving.

    const METADATA_LIMITS = { description: 1000, keywords: 30, keyword: 60 };

    function cleanText(text) {
        return String(text || '').split(/\s+/).filter(Boolean).join(' ');
    }

    // "bridge, Traffic ,, structural   load, bridge" -> ['bridge', 'Traffic', 'structural load']
    function parseKeywords(text) {
        const seen = new Set();
        const keywords = [];
        String(text || '').split(',').forEach(part => {
            const keyword = cleanText(part);
            if (keyword && !seen.has(keyword.toLowerCase())) {
                seen.add(keyword.toLowerCase());
                keywords.push(keyword);
            }
        });
        return keywords;
    }

    // Why the tidied values cannot be saved, or null
    function metadataProblem(description, keywords) {
        const L = METADATA_LIMITS;
        if (description.length > L.description) return `Keep the description to ${L.description} characters or fewer (it has ${description.length}).`;
        if (keywords.length > L.keywords) return `Use at most ${L.keywords} keywords (there are ${keywords.length}).`;
        const long = keywords.find(k => k.length > L.keyword);
        if (long) return `Keep each keyword to ${L.keyword} characters or fewer ("${long.slice(0, 24)}…" is longer).`;
        return null;
    }

    // Whether the description editor holds changes that were not saved (tidied the same way as a save)
    function wordsChanged(original, descriptionText, keywordsText) {
        return cleanText(descriptionText) !== cleanText(original.description)
            || parseKeywords(keywordsText).join('\u0000') !== parseKeywords((original.keywords || []).join(',')).join('\u0000');
    }

    function describedBy(asset) {
        const details = (asset && asset.details) || {};
        return {
            description: typeof details.description === 'string' ? details.description : '',
            keywords: Array.isArray(details.keywords) ? details.keywords : []
        };
    }

    // ---- Panel ---------------------------------------------------------------------------

    const KIND_LABELS = { video: 'Video', audio: 'Sound', image: 'Picture' };
    const KIND_ORDER = ['image', 'video', 'audio'];
    const KIND_FILTERS = { image: 'Pictures', video: 'Videos', audio: 'Sounds' };
    // The Type filter's default (Phase 21): every lesson adds narration clips, which would crowd out your pictures and
    // clips; they stay one choice away ("All files", or Source: Narration)
    const NO_NARRATION = 'no-narration';
    const LIBRARY_TITLE = 'Library';
    const LIBRARY_INTRO = 'Pictures, sounds and clips you can reuse in any lesson: your uploads, the media made for your lessons, and Aadhi\'s shared clips.';
    const LIBRARY_EMPTY = 'Your reusable visuals will appear here. Upload a picture or clip, or let Aadhi make them for your lessons.';
    const SOURCE_LABELS = { mascot: 'Aadhi', narration: 'Narration', manim: 'Animation', 'ai-video': 'AI video', 'ai-image': 'AI picture', 'ai-presenter': 'AI presenter', upload: 'Upload', system: 'Built-in', export: 'Exported video' };
    // An asset that cannot be used, in words (a ready one needs no status line); the raw status only with ?visualDebug
    const STATUS_WORDS = { processing: '⏳ Still being prepared', failed: '⚠ Missing or damaged: lessons cannot use it', deleted: 'Deleted' };
    // Two pages per load (Phase 21): Aadhi's shared assets are registered at the first start, so they are the oldest
    // rows and would never fit in a page of your newest uploads; your own items keep their newest-first page of 200
    const SHARED_PAGE = 50;
    const OWN_PAGE = 200;
    const GROUP_TITLES = { system: 'Shared Aadhi assets', mine: 'Your files' };

    function formatBytes(bytes) {
        if (!bytes && bytes !== 0) return '';
        const units = ['B', 'KB', 'MB', 'GB'];
        let value = bytes;
        let unit = 0;
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024;
            unit++;
        }
        return `${value.toFixed(unit < 2 ? 0 : 1)} ${units[unit]}`;
    }

    function formatDuration(seconds) {
        if (!seconds && seconds !== 0) return '';
        const s = Math.round(seconds);
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }

    function usedBy(references) {
        if (references === undefined || references === null) return '';
        if (!references) return 'No saved lesson yet';
        return references === 1 ? '1 saved lesson' : `${references} places in your saved lessons`;
    }

    // The server's own explanation of a refused request (its messages are written for people), or ''. A part in
    // brackets after a space carries technical detail (e.g. a decoder's words), so it is left out.
    function serverSays(err) {
        const detail = err && err.data && err.data.detail;
        const text = typeof detail === 'string' ? detail : (detail && typeof detail.message === 'string' ? detail.message : '');
        const plain = text.replace(/\s+\([^()]*\)/g, '').trim();
        return plain && !/[.!?]$/.test(plain) ? `${plain}.` : plain;
    }

    // Why a request failed, in plain words: never a status code or exception text (those only with ?visualDebug)
    function plainReason(err) {
        const status = err && typeof err.status === 'number' ? err.status : null;
        if (status === 0) return 'The server could not be reached. Check your connection.';
        if (status === 401) return 'Your login has expired. Please log in again.';
        if (status === 413) return 'The file is larger than the library accepts.';
        const said = serverSays(err);
        if (said) return said;
        return status >= 500 ? 'The server had a problem.' : '';
    }

    function technicalDetail(err) {
        if (!err) return '';
        const status = typeof err.status === 'number' ? `[${err.status}] ` : '';
        return `${status}${err.message || String(err)}`;
    }

    function debugFromLocation(doc) {
        const loc = doc && (doc.location || (doc.defaultView && doc.defaultView.location));
        return !!loc && /[?&]visualDebug(=|&|$)/.test(String(loc.search || ''));
    }

    function el(doc, tag, props, ...children) {
        const node = doc.createElement(tag);
        Object.entries(props || {}).forEach(([key, value]) => {
            if (value === null || value === undefined || value === false) return;
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value === true ? '' : value);
        });
        children.flat(Infinity).forEach(child => {
            if (child !== null && child !== undefined) node.appendChild(typeof child === 'string' ? doc.createTextNode(child) : child);
        });
        return node;
    }

    const settle = promise => promise.then(data => ({ data }), error => ({ error }));

    // Your own items from one or more requests, newest first, as one page (the totals added up)
    function mergePages(pages) {
        const seen = new Set();
        const assets = pages.flatMap(page => (page && Array.isArray(page.assets) ? page.assets : []))
            .filter(asset => !seen.has(asset.id) && seen.add(asset.id))
            .sort((a, b) => {
                const x = String(a.created_at || '');
                const y = String(b.created_at || '');
                return x < y ? 1 : x > y ? -1 : 0;
            });
        return { assets: assets.slice(0, OWN_PAGE), total: pages.reduce((n, page) => n + (page && typeof page.total === 'number' ? page.total : 0), 0) };
    }

    // ---- keyboard use of a modal panel (Phase 21): the focus moves in on open, Tab stays inside, and it goes back to
    // the control that opened the panel on close (the same helpers in export.js)
    function contains(root, node) {
        return !!(root && node && typeof root.contains === 'function' && root.contains(node));
    }

    function tabbable(root) {
        return Array.from(root.querySelectorAll('button, select, input, textarea, a[href], video[controls], audio[controls], [tabindex]')).filter(node =>
            !node.disabled && node.getAttribute('tabindex') !== '-1' && node.getAttribute('disabled') === null && node.getAttribute('hidden') === null
            && (typeof node.getClientRects !== 'function' || node.getClientRects().length > 0));
    }

    function trapTab(doc, root, e) {
        const list = tabbable(root);
        if (!list.length) return;
        const active = doc.activeElement;
        const at = list.indexOf(active);
        let to = null;
        if (!contains(root, active)) to = e.shiftKey ? list[list.length - 1] : list[0];
        else if (e.shiftKey && at <= 0) to = list[list.length - 1];
        else if (!e.shiftKey && at === list.length - 1) to = list[0];
        if (!to) return;
        if (typeof e.preventDefault === 'function') e.preventDefault();
        to.focus();
    }

    function focusNode(node) {
        if (node && typeof node.focus === 'function') {
            try { node.focus(); } catch (e) { /* not essential */ }
        }
    }

    class AssetLibraryPanel {
        // debug: bool | () => bool — technical details (MIME types, raw status, hashes, asset IDs, raw error text);
        // without it, the page's ?visualDebug decides
        constructor({ doc, api, clipboard, debug, confirm = text => (typeof window !== 'undefined' && window.confirm ? window.confirm(text) : true) }) {
            this.doc = doc;
            this.api = api;
            this.clipboard = clipboard;
            this.debug = debug;
            this.confirm = confirm;
            this.pick = null; // while choosing a visual for a scene (Visual Review): { kinds, title, onPick, onCancel }
            this.root = null;
            this.assets = []; // everything listed, in the order shown
            this.groups = [];
            this.selected = null;
            this.current = null; // the asset shown in the detail pane
            this.editing = null; // ID of the asset whose description/keywords are being edited
            this.requestSeq = 0;
        }

        isDebug() {
            if (typeof this.debug === 'function') return !!this.debug();
            if (this.debug !== undefined && this.debug !== null) return !!this.debug;
            return debugFromLocation(this.doc);
        }

        // What happened, why (plain words), what to do; the technical detail only in debug mode
        explain(what, err, next) {
            const text = [what, plainReason(err), next].filter(Boolean).join(' ');
            return this.isDebug() && err ? `${text} (${technicalDetail(err)})` : text;
        }

        build() {
            if (this.root) return;
            const doc = this.doc;
            const h = (...args) => el(doc, ...args);
            doc.querySelectorAll('[data-asset-runtime]').forEach(node => node.remove()); // exported HTML copies

            const option = (value, label) => h('option', { value, text: label });
            this.kindSelect = h('select', { class: 'asset-filter ui-focusable', 'aria-label': 'Type', onchange: () => this.guarded(() => this.refresh()) },
                option(NO_NARRATION, 'All except narration'), option('', 'All files'), ...KIND_ORDER.map(k => option(k, KIND_FILTERS[k])));
            this.kindSelect.value = NO_NARRATION;
            this.sourceSelect = h('select', { class: 'asset-filter ui-focusable', 'aria-label': 'Source', onchange: () => this.guarded(() => this.refresh()) },
                option('', 'All sources'), ...Object.entries(SOURCE_LABELS).map(([k, v]) => option(k, v)));
            this.sourceSelect.value = '';
            this.search = h('input', { class: 'asset-filter asset-search ui-focusable', type: 'search', placeholder: 'Search by name or description', 'aria-label': 'Search by name, description or keywords' });
            this.search.value = '';
            this.search.addEventListener('input', () => {
                clearTimeout(this.searchTimer);
                this.searchTimer = setTimeout(() => this.guarded(() => this.refresh()), 300);
            });
            this.fileInput = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/gif,image/webp,audio/*,video/mp4,video/webm,video/quicktime', hidden: true, 'aria-label': 'Choose a file to upload' });
            this.fileInput.addEventListener('change', () => this.uploadSelected());
            this.uploadButton = h('button', { type: 'button', class: 'btn-gold asset-upload-btn ui-focusable', text: '⬆ Upload file', title: 'Upload a picture, sound or clip', onclick: () => this.fileInput.click() });

            this.status = h('div', { class: 'asset-status', role: 'status', 'aria-live': 'polite' });
            this.list = h('ul', { class: 'asset-list', 'aria-label': 'Library items' });
            this.detail = h('div', { class: 'asset-detail', 'aria-live': 'polite' });

            this.panel = h('div', { class: 'asset-panel', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'asset-panel-title' },
                h('div', { class: 'asset-head' },
                    this.titleEl = h('h2', { id: 'asset-panel-title', tabindex: '-1', text: LIBRARY_TITLE }),
                    h('button', { type: 'button', class: 'export-close ui-focusable', 'aria-label': 'Close the library', title: 'Close', text: '✕', onclick: () => this.close() })),
                this.introEl = h('p', { class: 'asset-intro', text: LIBRARY_INTRO }),
                h('div', { class: 'asset-toolbar' }, this.kindSelect, this.sourceSelect, this.search, this.uploadButton, this.fileInput),
                this.status,
                h('div', { class: 'asset-body' }, this.list, this.detail));
            this.root = h('div', { class: 'asset-overlay', 'data-asset-runtime': true }, this.panel);
            this.root.addEventListener('keydown', e => e.stopPropagation()); // keep slide shortcuts out of the panel
            // Escape closes it wherever the focus is (e.g. back on the page after a dropdown); while a description is
            // being edited, Escape only cancels the edit. Tab stays inside the panel (it opens on top of every other one).
            this.onEscape = e => {
                if (!this.root.classList.contains('open')) return;
                if (typeof this.root.closest === 'function' && this.root.closest('[inert]')) return; // a dialog above this one (e.g. sign in) made it inert: the keys are not ours
                if (e.key === 'Tab') {
                    trapTab(this.doc, this.panel, e);
                    return;
                }
                if (e.key !== 'Escape' && e.key !== 'Esc') return;
                if (e.target === this.search && this.search.value) return; // the search field clears itself first
                if (this.editing && this.current && this.current.id === this.editing) {
                    e.preventDefault();
                    this.cancelWords(this.current);
                } else {
                    this.close();
                }
            };
            this.root.addEventListener('click', e => { if (e.target === this.root) this.close(); });
            doc.body.appendChild(this.root);
        }

        // options.pick: choose a picture or video for a scene instead of managing the library
        open(options = {}) {
            this.build();
            this.pick = options.pick || null;
            this.titleEl.textContent = this.pick ? (this.pick.title || 'Choose a visual') : LIBRARY_TITLE;
            this.introEl.textContent = this.pick ? 'Search, preview, then “Use this visual”. Upload a file here if it is not in your library yet.' : LIBRARY_INTRO;
            this.kindSelect.querySelectorAll('option').forEach(option => {
                option.disabled = !!this.pick && option.value !== '' && !this.pick.kinds.includes(option.value);
            });
            const kind = this.kindSelect.value;
            if (this.pick && kind && kind !== NO_NARRATION && !this.pick.kinds.includes(kind)) this.kindSelect.value = NO_NARRATION;
            this.root.classList.toggle('picking', !!this.pick);
            if (!this.root.classList.contains('open')) {
                const active = this.doc.activeElement;
                this.opener = active && active !== this.doc.body && !contains(this.root, active) ? active : null;
            }
            this.root.classList.add('open');
            this.doc.addEventListener('keydown', this.onEscape, true);
            // Into the panel: the search field while choosing a visual, else the heading (the list is still loading)
            focusNode(this.pick ? this.search : this.titleEl);
            return this.refresh();
        }

        close(picked = false) {
            if (!this.root) return;
            if (!picked && this.hasUnsavedWords() && !this.confirm('Discard this change?')) return;
            const wasOpen = this.root.classList.contains('open');
            this.root.classList.remove('open');
            this.doc.removeEventListener('keydown', this.onEscape, true);
            this.stopPreview();
            const pick = this.pick;
            this.pick = null;
            // back to the control that opened the panel (never left on <body> or behind a closed panel)
            const opener = this.opener;
            this.opener = null;
            if (wasOpen && opener && opener.isConnected !== false) focusNode(opener);
            if (pick && !picked && pick.onCancel) pick.onCancel();
        }

        // Unsaved description/keyword edits are not thrown away without asking
        hasUnsavedWords() {
            const form = this.wordsForm;
            return !!form && this.editing === form.asset.id
                && wordsChanged(describedBy(form.asset), form.descriptionInput.value, form.keywordsInput.value);
        }

        guarded(action) {
            if (this.hasUnsavedWords() && !this.confirm('Discard this change?')) return false;
            this.editing = null;
            action();
            return true;
        }

        // action: { label, onClick } shown as a button after the text (e.g. Try again)
        setStatus(text, kind = 'info', action = null) {
            this.status.textContent = text || '';
            this.status.setAttribute('data-kind', kind);
            if (action) {
                this.status.append(' ', el(this.doc, 'button', { type: 'button', class: 'export-link-btn asset-status-action ui-focusable', text: action.label, onclick: action.onClick }));
            }
        }

        // A filter the user chose (the default "All except narration" is not one)
        filtered() {
            const kind = this.kindSelect.value || '';
            return !!((kind && kind !== NO_NARRATION) || this.sourceSelect.value || this.search.value.trim());
        }

        // The requests for your own items. With no type or source chosen, one per kind, so a long run of one kind (a
        // lesson's narration clips) never pushes your pictures and clips off the page; "All except narration" asks
        // sounds you uploaded only. While choosing a visual, only the kinds the scene can show are asked.
        ownQueries(kindChoice, source) {
            if (source || (kindChoice && kindChoice !== NO_NARRATION)) return [{ kind: kindChoice === NO_NARRATION ? '' : kindChoice, source }];
            if (!this.pick && kindChoice !== NO_NARRATION) return [{}]; // All files: everything, newest first
            return (this.pick ? KIND_ORDER.filter(k => this.pick.kinds.includes(k)) : KIND_ORDER)
                .map(kind => (kind === 'audio' && kindChoice === NO_NARRATION ? { kind, source: 'upload' } : { kind }));
        }

        // keepSelection: the asset to select again after loading; options are passed to select()
        async refresh(keepSelection, options = {}) {
            const seq = ++this.requestSeq;
            this.setStatus('Loading your library…');
            this.list.setAttribute('aria-busy', 'true');
            const kindChoice = this.kindSelect.value || '';
            const source = this.sourceSelect.value || '';
            const q = this.search.value.trim();
            try {
                // Aadhi's shared assets and your own, asked together with the same search and filters (the shared ones
                // are never narration, so the default needs no extra request for them)
                const ownPages = this.ownQueries(kindChoice, source).map(filters => settle(this.api.list({ ...filters, q, scope: 'mine', limit: OWN_PAGE })));
                const [shared, ...own] = await Promise.all([
                    settle(this.api.list({ kind: kindChoice === NO_NARRATION ? '' : kindChoice, source, q, scope: 'system', limit: SHARED_PAGE })),
                    ...ownPages
                ]);
                if (seq !== this.requestSeq) return; // a newer filter change won
                const ownFailure = own.find(page => page.error);
                const mine = ownFailure ? { error: ownFailure.error } : { data: mergePages(own.map(page => page.data)) };
                if (shared.error && mine.error) throw mine.error;
                // While choosing a scene visual, only the kinds that scene can show
                const usable = data => (data && Array.isArray(data.assets) ? data.assets : []).filter(a => !this.pick || this.pick.kinds.includes(a.kind));
                const group = (key, page) => ({ key, title: GROUP_TITLES[key], assets: usable(page.data),
                    total: page.data && typeof page.data.total === 'number' ? page.data.total : 0, listed: page.data && page.data.assets ? page.data.assets.length : 0 });
                const sharedGroup = group('system', shared);
                const ownGroup = group('mine', mine);
                // Browsing: the shared assets first (they are few and the same for everyone); choosing: your own first
                this.groups = this.pick ? [ownGroup, sharedGroup] : [sharedGroup, ownGroup];
                this.assets = this.groups.flatMap(g => g.assets);
                this.renderList();
                if (mine.error) {
                    this.setStatus(this.explain('Your own files could not be loaded.', mine.error, 'Aadhi\'s shared assets are below.'), 'error',
                        { label: 'Try again', onClick: () => this.guarded(() => this.refresh()) });
                } else if (shared.error) {
                    this.setStatus(this.explain('Aadhi\'s shared assets could not be loaded right now; your own files are below.', shared.error, ''), 'warn',
                        { label: 'Try again', onClick: () => this.guarded(() => this.refresh()) });
                } else {
                    this.setStatus(this.pageNote(ownGroup, sharedGroup));
                }
                const again = keepSelection && this.assets.find(a => a.id === keepSelection);
                if (again) await this.select(again, options);
                else this.showDetail(null);
            } catch (err) {
                if (seq !== this.requestSeq) return;
                this.assets = [];
                this.groups = [];
                this.list.textContent = '';
                this.showDetail(null);
                this.setStatus(this.explain('Your library could not be loaded.', err, 'Your files are safe.'), 'error',
                    { label: 'Try again', onClick: () => this.guarded(() => this.refresh()) });
            } finally {
                if (seq === this.requestSeq) this.list.removeAttribute('aria-busy');
            }
        }

        // When a page does not hold everything that matches (the server's totals)
        pageNote(own, shared) {
            const notes = [];
            if (own.total > own.listed) notes.push(`Showing your newest ${own.listed} of ${own.total} files. Search or filter to find older ones.`);
            if (shared.total > shared.listed) notes.push(`Showing ${shared.listed} of ${shared.total} shared assets.`);
            return notes.join(' ');
        }

        renderList() {
            const doc = this.doc;
            this.list.textContent = '';
            const groups = this.groups.filter(g => g.assets.length);
            if (!groups.length) {
                const search = this.search.value.trim();
                this.list.appendChild(el(doc, 'li', { class: 'asset-empty',
                    text: this.filtered() ? `No assets match ${search ? 'this search' : 'these filters'}. Try other words, or choose All types and All sources.` : LIBRARY_EMPTY }));
                return;
            }
            groups.forEach(group => {
                const count = group.total > group.listed && !this.pick ? `${group.assets.length} of ${group.total}` : String(group.assets.length);
                this.list.appendChild(el(doc, 'li', { class: 'asset-group-head', role: 'presentation', 'data-group': group.key },
                    el(doc, 'h3', { class: 'asset-group-title', text: `${group.title} (${count})` })));
                group.assets.forEach(asset => this.list.appendChild(this.renderItem(asset)));
            });
        }

        renderItem(asset) {
            const doc = this.doc;
            const meta = [KIND_LABELS[asset.kind], asset.duration_seconds ? formatDuration(asset.duration_seconds) : '',
                asset.width ? `${asset.width}×${asset.height}` : '', formatBytes(asset.file_size)].filter(Boolean).join(' · ');
            const badge = asset.scope === 'system' ? 'Shared' : (SOURCE_LABELS[asset.source] || asset.source);
            const item = el(doc, 'li', { class: 'asset-item ui-focusable', 'data-kind': asset.kind, 'data-id': asset.id, 'data-scope': asset.scope, tabindex: '0', role: 'button',
                'aria-pressed': asset.id === this.selected ? 'true' : 'false', 'aria-label': [asset.file_name, meta, badge].filter(Boolean).join(', ') },
            el(doc, 'span', { class: 'asset-kind-icon', 'aria-hidden': 'true' }),
            el(doc, 'span', { class: 'asset-item-main' },
                el(doc, 'span', { class: 'asset-item-name', text: asset.file_name }),
                el(doc, 'span', { class: 'asset-item-meta', text: meta })),
            el(doc, 'span', { class: 'asset-badge', 'data-source': asset.source, text: badge }));
            if (asset.id === this.selected) item.classList.add('selected');
            item.addEventListener('click', () => this.select(asset));
            item.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.select(asset); } });
            return item;
        }

        // options.describe: open the description editor if the asset is yours and not described yet
        async select(asset, options = {}) {
            if (asset.id !== this.selected && this.hasUnsavedWords() && !this.confirm('Discard this change?')) return;
            this.selected = asset.id;
            this.list.querySelectorAll('.asset-item').forEach(item => {
                const on = item.getAttribute('data-id') === asset.id;
                item.classList.toggle('selected', on);
                item.setAttribute('aria-pressed', on ? 'true' : 'false');
                // e.g. a new upload, listed below the shared assets: brought into view
                if (on && typeof item.scrollIntoView === 'function') item.scrollIntoView({ block: 'nearest' });
            });
            this.showDetail(asset);
            try {
                const [detail, links] = await Promise.all([this.api.get(asset.id), this.api.resolve([asset.id])]);
                if (this.selected !== asset.id) return;
                this.showDetail(detail, links.assets[asset.id] && links.assets[asset.id].url, true);
                const words = describedBy(detail);
                if (options.describe && this.canEdit(detail) && !words.description && !words.keywords.length) this.editWords(detail);
            } catch (err) {
                if (this.selected === asset.id) {
                    this.setStatus(this.explain(`${asset.file_name} could not be opened.`, err, 'Select it again to retry.'), 'error');
                }
            }
        }

        stopPreview() {
            this.detail.querySelectorAll('video, audio').forEach(media => media.pause());
        }

        // ready: the full detail has loaded (only then can the description be edited)
        showDetail(asset, url, ready = false) {
            this.stopPreview();
            this.detail.textContent = '';
            this.current = asset;
            this.editing = null;
            if (!asset) {
                this.detail.appendChild(el(this.doc, 'p', { class: 'asset-detail-empty', text: 'Select an item to preview it.' }));
                return;
            }
            const h = (...args) => el(this.doc, ...args);
            const debug = this.isDebug();
            let preview;
            if (!url) {
                preview = h('div', { class: 'asset-preview asset-preview-loading', text: asset.status === 'ready' ? 'Loading preview…' : 'This file is not available: it is missing or damaged.' });
            } else if (asset.kind === 'image') {
                preview = h('img', { class: 'asset-preview', src: url, alt: asset.file_name });
            } else if (asset.kind === 'audio') {
                preview = h('audio', { class: 'asset-preview', src: url, controls: true, preload: 'metadata', 'aria-label': `Listen to ${asset.file_name}` });
            } else {
                preview = h('video', { class: 'asset-preview', src: url, controls: true, preload: 'metadata', playsinline: true, 'aria-label': `Preview of ${asset.file_name}` });
            }
            if (url) preview.addEventListener('error', () => { this.setStatus('The preview could not be loaded. The file itself is safe; select it again to retry.', 'error'); });

            // Plain facts; MIME type, raw status, content hash and asset ID only in debug mode (?visualDebug)
            const rows = [
                ['Type', debug && asset.mime_type ? `${KIND_LABELS[asset.kind] || asset.kind} (${asset.mime_type})` : (KIND_LABELS[asset.kind] || asset.kind)],
                ['Source', SOURCE_LABELS[asset.source] || asset.source],
                ['Access', asset.scope === 'system' ? 'Shared with everyone by Aadhi (read-only)' : 'Private to you'],
                ['File size', formatBytes(asset.file_size)],
                ['Duration', asset.duration_seconds ? formatDuration(asset.duration_seconds) : ''],
                ['Picture size', asset.width ? `${asset.width} × ${asset.height}` : ''],
                ['Sound', asset.kind === 'video' && asset.has_audio !== null && asset.has_audio !== undefined ? (asset.has_audio ? 'Yes' : 'No') : ''],
                ['Status', debug ? asset.status : (STATUS_WORDS[asset.status] || '')],
                ['Used by', usedBy(asset.references)],
                ['Added', asset.created_at ? new Date(asset.created_at).toLocaleString() : '']
            ];
            if (debug) {
                rows.push(['Problem', asset.error_message || ''], ['Content hash', asset.sha256 ? `${asset.sha256.slice(0, 16)}…` : ''], ['Asset ID', asset.id]);
            }
            const shown = rows.filter(([, v]) => v);
            if (asset.details && asset.details.text) shown.splice(2, 0, ['Narration', asset.details.text]);
            if (asset.details && asset.details.prompt) shown.splice(2, 0, ['Description used to make it', asset.details.prompt]); // (an AI request, in plain words)

            const buttons = [];
            if (this.pick && ready && asset.status === 'ready' && this.pick.kinds.includes(asset.kind)) {
                const pick = this.pick;
                buttons.push(h('button', { type: 'button', class: 'export-action asset-pick ui-focusable', text: 'Use this visual', onclick: () => {
                    if (this.hasUnsavedWords() && !this.confirm('Discard this change?')) return;
                    this.close(true);
                    pick.onPick(asset);
                } }));
            }
            if (debug) {
                buttons.push(h('button', { type: 'button', class: 'export-action export-action-secondary ui-focusable', text: 'Copy asset ID', onclick: () => this.copy(asset.id, 'Asset ID copied.') }));
                if (asset.kind === 'image') {
                    buttons.push(h('button', { type: 'button', class: 'export-action export-action-secondary ui-focusable', text: 'Copy image tag',
                        onclick: () => this.copy(`<img src="asset:${asset.id}" alt="">`, 'Image tag copied: paste it into a scene\'s HTML.') }));
                }
            }
            if (asset.owned && !this.pick) {
                const del = h('button', { type: 'button', class: 'export-action asset-delete ui-focusable', text: 'Delete', onclick: () => this.remove(asset) });
                if (asset.deletable === false) {
                    del.disabled = true;
                    del.title = 'Used by a saved lesson, so it is kept';
                }
                buttons.push(del);
            }
            this.words = h('section', { class: 'asset-words', 'aria-label': 'Description and keywords' });
            this.renderWords(asset, ready);
            this.detail.append(preview,
                h('h3', { class: 'asset-detail-name', text: asset.file_name }),
                h('dl', { class: 'asset-meta' }, shown.map(([k, v]) => [h('dt', { text: k }), h('dd', { text: String(v) })])),
                this.words);
            if (buttons.length) this.detail.append(h('div', { class: 'asset-actions' }, buttons));
        }

        // Your own assets can be described; shared assets are read-only and deleted ones are gone
        canEdit(asset) {
            return !!asset && asset.owned === true && asset.status !== 'deleted';
        }

        // The description and keywords, and "Edit description" for your own assets
        renderWords(asset, ready = true) {
            const h = (...args) => el(this.doc, ...args);
            const { description, keywords } = describedBy(asset);
            this.words.textContent = '';
            if (description || keywords.length) {
                this.words.appendChild(h('dl', { class: 'asset-meta' },
                    description ? [h('dt', { text: 'Description' }), h('dd', { class: 'asset-description', text: description })] : null,
                    keywords.length ? [h('dt', { text: 'Keywords' }),
                        h('dd', { class: 'asset-keywords' }, keywords.map(k => h('span', { class: 'asset-keyword', text: k })))] : null));
            } else if (this.canEdit(asset)) {
                this.words.appendChild(h('p', { class: 'asset-words-hint',
                    text: 'Not described yet. Add a few words about what it shows, so lessons can find and reuse it instead of making a new one.' }));
            }
            if (this.canEdit(asset) && ready) {
                this.words.appendChild(h('button', { type: 'button', class: 'export-action export-action-secondary asset-edit-meta ui-focusable',
                    text: description || keywords.length ? 'Edit description' : 'Add a description', onclick: () => this.editWords(asset) }));
            }
        }

        editWords(asset) {
            const h = (...args) => el(this.doc, ...args);
            const L = METADATA_LIMITS;
            const { description, keywords } = describedBy(asset);
            this.editing = asset.id;
            this.words.textContent = '';
            const descriptionInput = h('textarea', { class: 'asset-input asset-description-input ui-focusable', rows: '3', maxlength: String(L.description),
                placeholder: 'What it shows, e.g. A steel bridge carrying heavy rush-hour traffic' });
            descriptionInput.value = description;
            const count = h('span', { class: 'asset-count' });
            const updateCount = () => { count.textContent = `${descriptionInput.value.length}/${L.description}`; };
            descriptionInput.addEventListener('input', updateCount);
            updateCount();
            const keywordsInput = h('input', { class: 'asset-input asset-keywords-input ui-focusable', type: 'text',
                placeholder: 'bridge, traffic, structural load, stress' });
            keywordsInput.value = keywords.join(', ');
            const save = h('button', { type: 'submit', class: 'export-action asset-meta-save ui-focusable', text: 'Save' });
            const form = h('form', { class: 'asset-words-form', onsubmit: e => {
                e.preventDefault();
                this.saveWords(asset, descriptionInput.value, keywordsInput.value, save);
            } },
            h('label', { class: 'asset-field' }, h('span', { class: 'asset-field-label' }, 'Description ', count), descriptionInput),
            h('label', { class: 'asset-field' }, h('span', { class: 'asset-field-label', text: 'Keywords (separate with commas)' }), keywordsInput),
            h('p', { class: 'asset-words-hint', text: 'Lessons find this item by these words; its file name hardly counts.' }),
            h('div', { class: 'asset-actions' }, save,
                h('button', { type: 'button', class: 'export-action export-action-secondary asset-meta-cancel ui-focusable', text: 'Cancel', onclick: () => this.cancelWords(asset) })));
            this.words.appendChild(form);
            this.wordsForm = { asset, descriptionInput, keywordsInput };
            if (typeof descriptionInput.focus === 'function') descriptionInput.focus();
        }

        cancelWords(asset) {
            this.editing = null;
            if (this.current && this.current.id === asset.id) this.renderWords(asset);
            this.setStatus('');
        }

        async saveWords(asset, descriptionText, keywordsText, button) {
            const description = cleanText(descriptionText);
            const keywords = parseKeywords(keywordsText);
            const problem = metadataProblem(description, keywords);
            if (problem) {
                this.setStatus(problem, 'error');
                return;
            }
            button.disabled = true;
            this.setStatus('Saving…');
            try {
                const updated = await this.api.update(asset.id, { description, keywords });
                // Update the open detail and the list's copy in place; nothing else is reloaded
                asset.details = updated.details;
                const listed = this.assets.find(a => a.id === asset.id);
                if (listed) listed.details = updated.details;
                if (this.editing === asset.id) this.editing = null;
                if (this.current && this.current.id === asset.id) this.renderWords(asset);
                this.setStatus('✓ Details saved: lessons can now find it by these words.');
            } catch (err) {
                button.disabled = false;
                this.setStatus(this.explain('The description was not saved.', err, 'Your text is still here: select Save to try again.'), 'error');
            }
        }

        async copy(text, message) {
            try {
                await this.clipboard.writeText(text);
                this.setStatus(message);
            } catch (e) {
                this.setStatus(`Copy this: ${text}`);
            }
        }

        async remove(asset) {
            this.setStatus(`Deleting ${asset.file_name}…`);
            try {
                await this.api.remove(asset.id);
                await this.refresh(); // reload first so the confirmation is not replaced by "Loading…"
                this.setStatus(`${asset.file_name} was deleted.`);
            } catch (err) {
                this.setStatus(this.explain(`${asset.file_name} was not deleted.`, err, ''), 'error');
            }
        }

        async uploadSelected() {
            const file = this.fileInput.files[0];
            this.fileInput.value = '';
            if (!file) return;
            this.uploadButton.disabled = true;
            try {
                const result = await this.api.upload(file, fraction => this.setStatus(`Uploading ${file.name}… ${Math.round(fraction * 100)}%`));
                this.kindSelect.value = NO_NARRATION;
                this.sourceSelect.value = '';
                this.search.value = '';
                // A new picture or video opens its description editor: lessons can only find it by its words
                const kind = result.asset.kind;
                const options = { describe: kind === 'image' || kind === 'video' };
                await this.refresh(result.asset.id, options);
                if (!this.assets.some(a => a.id === result.asset.id) && this.status.getAttribute('data-kind') !== 'error') {
                    // the same content as a narration clip (kept, not stored twice): shown with all files
                    this.kindSelect.value = '';
                    await this.refresh(result.asset.id, options);
                }
                const describing = this.editing === result.asset.id ? ' Describe it below so lessons can find it.' : '';
                this.setStatus((result.deduplicated
                    ? `${file.name} is already in your library (same content), so the existing item was kept.`
                    : `${file.name} was added to your library.`) + describing);
            } catch (err) {
                this.setStatus(this.explain(`${file.name} was not added.`, err, 'Try another file, or try again.'), 'error');
            } finally {
                this.uploadButton.disabled = false;
            }
        }
    }

    return { AssetApi, AssetLibraryPanel, METADATA_LIMITS, OWN_PAGE, SHARED_PAGE, applyResolved, cleanText, collectAssetIds, metadataProblem, parseKeywords, plainReason, resolveLesson, wordsChanged };
});
