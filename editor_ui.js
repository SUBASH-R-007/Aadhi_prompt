/*
 * Advanced video editor workspace (Phase 19): the chrome AROUND the live stage.
 *
 * The editor is an override / refinement layer over the lesson the page already plays (scratchpad/phase19_contract.md):
 * one lesson model (the page's `slides`), one renderer (the live stage stays the preview), one planner. This file only
 * draws the workspace: a top bar, a scene list (left), a timeline (bottom, in place of the player bar) and an inspector
 * (right drawer) for the selected scene. Every change goes through the editor model (editor.js, AadhiEditor.EditorModel:
 * one command per change; a typing session in a field is ONE transaction), then tells the page (adapter.changed: it
 * re-plans and re-renders) and the autosave (autosave.schedule; hold / release around a drag). Composition choices come
 * back from the model as {kind: 'composition', scene_id, overrides} and go to the page (adapter.applyComposition), which
 * sends them through the existing composition review. Nothing here talks to the server.
 *
 * Lesson text is data: it reaches the page through textContent only (never innerHTML). No internal ids, provider names,
 * cache keys, fingerprints or raw error text are shown unless adapter.debug: a problem is said in plain words (what
 * happened, whether the edits are safe, what to do). No animated transitions in the chrome.
 *
 * Phase 21: the workspace is a modal dialog (focus stays inside it and goes back where it was on close); the inspector
 * shows what applies to the selected scene (inspectorLayout: its lead section first, a hidden presenter as one "Show
 * presenter" control, "Look and motion" collapsed at the end); editor.css draws it with the product's shared tokens (ui.css).
 *
 * Loaded as a classic <script> (window.AadhiEditorUI, after editor.js and mascot.js) and as a CommonJS module by the Node unit
 * tests in tests/editor_ui.test.js. The page's adapter is described at the end of this file.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory(require('./mascot.js'));
    } else {
        root.AadhiEditorUI = factory(root.AadhiMascot);
    }
})(typeof self !== 'undefined' ? self : this, function (Mascot) {
    'use strict';

    // Panels of the page that own the keyboard while open (the editor's shortcuts stay out of their way)
    const OTHER_OVERLAYS = '.asset-overlay.open, .review-overlay.open, .export-overlay.open, .glass-overlay.active:not(#loading-overlay), .history-modal-overlay.active';
    const COLLAPSED_PX = 14;      // a hidden scene's collapsed block in the timeline
    const MIN_BLOCK_PX = 18;      // the narrowest visible block (a very short scene stays clickable)
    const DEFAULT_PPS = 12;       // pixels per second when the timeline's width is unknown
    const DRAG_PX = 5;            // pointer travel before a press becomes a drag
    const LABEL_PX = 92;          // the timeline's track-name column (editor.css --ed-label)
    const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const EMPTY_SECONDS = 5;      // a scene without narration or plan length (editor.js EMPTY_SCENE)

    // The inspector's sections (open: open until the user closes it; advanced: under "Look and motion"). Which ones a scene
    // shows, and in what order, follows the scene: inspectorLayout()
    const SECTIONS = [
        { key: 'scene', label: 'Scene', open: true },
        { key: 'timing', label: 'Timing', open: true },
        { key: 'narration', label: 'Narration', open: true },
        { key: 'text', label: 'Text', open: true },
        { key: 'presenter', label: 'Presenter' },
        { key: 'visual', label: 'Visual' },
        { key: 'camera', label: 'Camera', advanced: true },
        { key: 'transition', label: 'Transition', advanced: true },
        { key: 'background', label: 'Background', advanced: true },
        { key: 'style', label: 'Style', advanced: true },
        { key: 'captions', label: 'Captions' },
        { key: 'quality', label: 'Quality' }
    ];
    const SECTION_BY_KEY = new Map(SECTIONS.map(s => [s.key, s]));

    // What leads a scene (its composition's layout, or its board when it has none) and the inspector's order for it: the
    // lead section first (open), the scene's other settings, then "Look and motion" (collapsed)
    const LEAD_TEMPLATES = { presenter_intro: 'presenter', presenter_explanation: 'presenter', formula_focus: 'formula', code_focus: 'code',
        visual_focus: 'visual', diagram_focus: 'visual', presenter_plus_visual: 'visual' };
    const MAIN_ORDER = {
        presenter: ['presenter', 'narration', 'text', 'timing', 'visual'],
        visual: ['visual', 'narration', 'text', 'timing', 'presenter'],
        formula: ['text', 'narration', 'timing', 'visual', 'presenter'],
        code: ['text', 'narration', 'timing', 'visual', 'presenter'],
        text: ['narration', 'text', 'timing', 'visual', 'presenter']
    };
    const FOCUS_TEXT = { presenter: 'Presenter-led', visual: 'Picture or diagram', formula: 'Formula', code: 'Code', text: 'Text' };
    const LOOK_SECTIONS = ['camera', 'transition', 'background', 'style'];

    // Where "Back" goes (adapter.returnTo())
    const RETURN = {
        studio: { text: 'Back to the Studio', label: 'Back to the Studio (closes the editor)' },
        lesson: { text: 'Back to lesson', label: 'Back to lesson (closes the editor)' }
    };

    const SMALL_SCREEN_NOTE = 'Editing works best on a larger screen; here you can change scenes in the list and the inspector.';
    // Phase 21: at 1280 px or less the panels cover part of the scene (editor.css shows this note there, at the stage's top edge)
    const STAGE_NOTE = 'Part of the scene is under the panels — Preview (👁) shows all of it';

    const SHORTCUTS = [
        ['Space', 'Play / pause'],
        ['← / →', 'Previous / next scene'],
        ['Alt + ← / →', 'Move the selected scene earlier / later'],
        ['Ctrl + Z  (⌘ Z)', 'Undo'],
        ['Ctrl + Shift + Z, Ctrl + Y', 'Redo'],
        ['Delete', 'Delete the selected scene (asks first)'],
        ['Esc', 'Close the inspector, then the editor'],
        ['?', 'Show or hide these shortcuts']
    ];

    // editor.js Autosave states
    const SAVE_TEXT = {
        saved: '✓ Saved',
        saving: '⟳ Saving…',
        unsaved: '● Unsaved changes',
        error: "✕ Couldn't save",
        conflict: '⚠ Changed elsewhere'
    };

    const QUALITY_SEVERITY = {
        info: { icon: 'ℹ', label: 'Noted' },
        notice: { icon: '⚠', label: 'Worth a look' },
        warning: { icon: '⚠', label: 'Please check' },
        error: { icon: '✕', label: 'Needs fixing' },
        blocking: { icon: '✕', label: 'Blocks the export' }
    };
    const Q_RANK = { info: 0, notice: 1, warning: 2, error: 3, blocking: 4 };

    const APPROVAL = {
        approved: '✓ Approved',
        changed: '✎ Changed',
        pending: '● Needs review'
    };

    const TYPES = {
        ai_video: 'AI video', simulation: 'Simulation', quiz_checkpoint: 'Quiz', quiz: 'Quiz', chapter_card: 'Chapter card',
        title: 'Title', recap: 'Recap', example: 'Example', custom: 'Custom', ai_avatar: 'Presenter', manim: 'Animation',
        visual: 'Visual', image: 'Picture', video: 'Clip', chart: 'Chart', graph: 'Graph', terminal: 'Terminal', content: 'Content'
    };

    const VISUAL_KIND = { picture: '🖼 Picture', clip: '🎞 Clip', diagram: '📐 Diagram', none: 'No visual' };

    // The Phase 13 / 17 vocabulary when the page gives none (the page's adapter.vocabulary() wins)
    const DEFAULT_VOCABULARY = {
        presenter_position: { left: 'Left', right: 'Right', hidden: 'Hidden' },
        presenter_size: { dominant: 'Dominant', secondary: 'Secondary', small: 'Small' },
        visual_size: { dominant: 'Large', secondary: 'Medium', side_panel: 'Narrow' },
        visual_position: { left: 'Left of the text', right: 'Right of the text' },
        camera: { static: 'Still', slow_zoom_in: 'Slow zoom in', slow_zoom_out: 'Slow zoom out', pan_left: 'Pan left', pan_right: 'Pan right', focus: 'Focus' },
        transition: { fade: 'Fade', soft_fade: 'Soft fade', crossfade: 'Crossfade', slide: 'Slide', zoom: 'Zoom', wipe: 'Wipe', cut: 'Cut' },
        background: { gradient: 'Clean gradient', solid: 'Solid colour', studio: "Aadhi's studio" },
        accent: ['default', 'gold', 'coral', 'crimson', 'teal', 'sky', 'violet', 'emerald'],
        background_density: ['default', 'plain', 'subtle', 'rich']
    };

    // ---- small helpers ---------------------------------------------------------------------------------------------

    function el(doc, tag, props, ...children) {
        const node = doc.createElement(tag);
        Object.entries(props || {}).forEach(([key, value]) => {
            if (value === null || value === undefined || value === false) return;
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key === 'value') node.value = value;
            else if (key === 'css') Object.entries(value).forEach(([k, v]) => { node.style[k] = v; });
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value === true ? '' : value);
        });
        children.flat(Infinity).forEach(child => {
            if (child !== null && child !== undefined && child !== false) node.appendChild(typeof child === 'string' ? doc.createTextNode(child) : child);
        });
        return node;
    }

    function detach(node) {
        if (!node) return;
        if (typeof node.remove === 'function') node.remove();
        else if (node.parentNode && typeof node.parentNode.removeChild === 'function') node.parentNode.removeChild(node);
    }

    function setHidden(node, hidden) {
        if (!node) return;
        if (hidden) node.setAttribute('hidden', '');
        else node.removeAttribute('hidden');
    }

    function focusNode(node) {
        if (node && typeof node.focus === 'function') {
            try { node.focus(); } catch (e) { /* not focusable */ }
        }
    }

    const plain = v => !!v && typeof v === 'object' && !Array.isArray(v);
    const str = (v, limit = 400) => (typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v)).slice(0, limit);
    const finite = v => typeof v === 'number' && Number.isFinite(v);
    const list = nodes => Array.prototype.slice.call(nodes || []);

    function humanize(word) {
        const text = String(word || '').replace(/[_-]+/g, ' ').trim();
        return text ? text[0].toUpperCase() + text.slice(1) : '';
    }

    function clock(seconds) {
        const s = Math.max(0, Math.round(finite(seconds) ? seconds : 0));
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = String(s % 60).padStart(2, '0');
        return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
    }

    function secondsText(seconds) {
        if (!finite(seconds) || seconds <= 0) return '0 s';
        if (seconds < 1) return '<1 s';
        return seconds < 10 && Math.round(seconds * 10) % 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`;
    }

    function typeText(scene) {
        const type = scene && typeof scene.type === 'string' ? scene.type : '';
        return TYPES[type] || humanize(type) || 'Scene';
    }

    function sceneTitle(scene) {
        const title = scene && typeof scene.title === 'string' ? scene.title.replace(/\s+/g, ' ').trim() : '';
        return title || 'Untitled scene';
    }

    function edit(scene) {
        return scene && plain(scene.edit) ? scene.edit : {};
    }

    function isHidden(scene) {
        return edit(scene).hidden === true;
    }

    // The scene's own composition choice for `key` ('auto' when none): the same rule as editor.js currentOverride
    function override(scene, key) {
        const reviews = scene && plain(scene.visual_review) ? scene.visual_review : {};
        const review = plain(reviews.composition) ? reviews.composition : null;
        if (review && ['approved', 'changed'].includes(review.status) && plain(review.overrides) && typeof review.overrides[key] === 'string') {
            return review.overrides[key];
        }
        return 'auto';
    }

    function labelItems(scene) {
        return scene && plain(scene.composition) && Array.isArray(scene.composition.labels) ? scene.composition.labels : [];
    }

    function labelsText(items) {
        return (Array.isArray(items) ? items : []).map(l => (plain(l) ? str(l.text, 120) : str(l, 120))).filter(Boolean).join('\n');
    }

    // One approval mark per scene from editor.js effective().approvals: the least settled of its reviewable parts
    function approvalMark(approvals) {
        const states = plain(approvals) ? Object.values(approvals).filter(Boolean) : [];
        if (!states.length) return null;
        if (states.includes('pending')) return 'pending';
        if (states.includes('changed') || states.includes('removed')) return 'changed';
        return 'approved';
    }

    function visualKind(scene) {
        if (!scene) return 'none';
        const vp = plain(scene.visual_plan) ? scene.visual_plan : null;
        if (vp) {
            const p = ['main', 'side'].map(s => vp[s]).find(x => plain(x) && x.selection !== 'removed' && x.source && x.source !== 'NONE');
            if (!p) return 'none';
            if (p.source === 'PROCEDURAL' || p.source === 'MANIM') return 'diagram';
            if (p.media === 'STATIC_IMAGE' || p.source === 'AI_IMAGE') return 'picture';
            if (p.media === 'NONE') return 'none';
            return 'clip';
        }
        const panel = plain(scene.side_panel) ? scene.side_panel : null;
        if (scene.type === 'ai_video' || scene.type === 'video') return 'clip';
        if (panel && panel.type === 'image') return 'picture';
        if (panel && panel.type) return 'diagram';
        if (typeof scene.html === 'string' && /<img\b/i.test(scene.html)) return 'picture';
        return 'none';
    }

    function presenterInfo(scene) {
        const plan = scene && plain(scene.cinematic_plan) ? scene.cinematic_plan : null;
        const p = plan && plain(plan.presenter) ? plan.presenter : null;
        if (!p) return { state: 'none', text: !plan && mascotShown(scene) ? 'Aadhi (no presenter)' : 'No presenter', position: '', size: '' };
        const position = override(scene, 'presenter_position') !== 'auto' ? override(scene, 'presenter_position') : (p.shown ? p.side : 'hidden');
        let size = override(scene, 'presenter_size') !== 'auto' ? override(scene, 'presenter_size') : '';
        if (!p.shown || position === 'hidden' || size === 'hidden') return { state: 'hidden', text: 'Hidden', position: 'hidden', size: '' };
        if (!size) {
            const layer = (Array.isArray(plan.layers) ? plan.layers : []).find(l => plain(l) && l.type === 'presenter');
            const w = layer && plain(layer.box) && finite(layer.box.w) ? layer.box.w : null;
            size = w === null ? '' : w >= 0.4 ? 'dominant' : w >= 0.2 ? 'secondary' : 'small';
        }
        const side = position === 'left' ? 'Left' : position === 'right' ? 'Right' : '';
        if (size === 'small') return { state: 'small', text: `Small${side ? ' · ' + side : ''}`, position, size };
        return { state: 'shown', text: `Shown${side ? ' · ' + side : ''}`, position, size };
    }

    // Aadhi on screen in a scene without a composition (the Classic layout): the page's own placement rule (mascot.js), unless
    // another presenter takes his place
    function mascotShown(scene) {
        if (!Mascot || typeof Mascot.resolvePlacement !== 'function') return false;
        const other = plain(scene.presenter_plan) && scene.presenter_plan.type && scene.presenter_plan.type !== 'mascot';
        return !other && Mascot.resolvePlacement(scene) !== 'hidden';
    }

    // A visual the scene's visual plan has a place for (main / side): one the Library can replace
    function hasVisualSlot(scene) {
        const vp = scene && plain(scene.visual_plan) ? scene.visual_plan : null;
        return !!vp && ['main', 'side'].some(s => plain(vp[s]));
    }

    // What leads the scene: presenter, visual, formula, code or text (its composition's layout, else its board)
    function sceneFocus(scene) {
        if (!scene) return 'text';
        const plan = plain(scene.cinematic_plan) ? scene.cinematic_plan : null;
        const html = typeof scene.html === 'string' ? scene.html : '';
        const visual = hasVisualSlot(scene) || visualKind(scene) !== 'none';
        let focus = plan && typeof plan.template === 'string' ? LEAD_TEMPLATES[plan.template] || null : null;
        if (!focus) {
            if (/<pre[\s>]|<code[\s>]/i.test(html)) focus = 'code';
            else if (/formula-block|\\\[|\\\(|\$\$/.test(html)) focus = 'formula';
            else if (!plan && visual) focus = 'visual';
        }
        // a lead the scene does not show (the presenter hidden, no visual) gives way
        if (focus === 'presenter' && presenterInfo(scene).state !== 'shown') focus = null;
        if (focus === 'visual' && !visual) focus = null;
        if (!focus && presenterInfo(scene).size === 'dominant') focus = 'presenter';
        return focus || 'text';
    }

    // The inspector for one scene: {focus, order (section keys), lead (open by default), presenter (none / hidden / small /
    // shown)}. A section that cannot apply is left out: the presenter of a scene without one, a hidden presenter shown as
    // one "Show presenter" control, the visual of a scene without any, the transition into the first scene, the layout
    // ("Look and motion") of a scene with no composition yet.
    function inspectorLayout(scene, index) {
        const composed = !!(scene && plain(scene.cinematic_plan));
        const focus = sceneFocus(scene);
        const presenter = presenterInfo(scene).state;
        const applies = {
            presenter: composed && presenter !== 'none',
            visual: hasVisualSlot(scene) || visualKind(scene) !== 'none',
            camera: composed,
            transition: composed && (index > 0 || override(scene, 'transition') !== 'auto'),
            background: composed,
            style: composed
        };
        const main = MAIN_ORDER[focus] || MAIN_ORDER.text;
        const order = ['scene', ...main, 'captions', 'quality', ...LOOK_SECTIONS].filter(key => applies[key] !== false);
        return { focus, order, lead: main[0], presenter };
    }

    const isSeverity = v => typeof v === 'string' && Object.prototype.hasOwnProperty.call(Q_RANK, v);
    const worse = (a, b) => (!a || (b && Q_RANK[b] > Q_RANK[a]) ? b : a);

    // One quality finding as the editor shows it, or null (malformed: left out). Its strings are data.
    function cleanIssue(f) {
        if (!plain(f) || !isSeverity(f.severity)) return null;
        const message = str(f.message, 400).replace(/\s+/g, ' ').trim();
        if (!message) return null;
        const scene = Number.isInteger(f.scene) && f.scene >= 0 && f.scene < 200 ? f.scene : null;
        return { severity: f.severity, message, scene, rule: typeof f.rule === 'string' ? f.rule.slice(0, 100) : null };
    }

    // {all, count (notice or more serious), worst}; the most serious first
    function countFindings(issues) {
        const all = (Array.isArray(issues) ? issues : []).slice().sort((a, b) => Q_RANK[b.severity] - Q_RANK[a.severity]);
        const counted = all.filter(f => f.severity !== 'info');
        return { all, count: counted.length, worst: counted.reduce((w, f) => worse(w, f.severity), null) };
    }

    // The lesson's quality report (Phase 18) as the editor uses it: valid findings only (their strings are data)
    function qualityReport(raw) {
        if (!plain(raw)) return null;
        const issues = (Array.isArray(raw.issues) ? raw.issues.slice(0, 500) : []).map(cleanIssue).filter(Boolean);
        const attention = issues.filter(f => f.severity !== 'info');
        const worst = attention.reduce((w, f) => (Q_RANK[f.severity] > Q_RANK[w] ? f.severity : w), 'info');
        return { issues, attention: attention.length, worst };
    }

    function sceneFindings(report, index) {
        return countFindings(report ? report.issues.filter(f => f.scene === index) : []);
    }

    // "⚠ Please check" (never a colour alone)
    function severityText(severity) {
        const sev = QUALITY_SEVERITY[severity] || QUALITY_SEVERITY.notice;
        return `${sev.icon} ${sev.label}`;
    }

    function qualityHeadline(report, stale) {
        if (stale) return { status: 'stale', text: '◌ Quality: check again' };
        if (!report) return { status: 'none', text: '◌ Quality not checked' };
        if (!report.attention) return { status: 'good', text: '✓ Good' };
        const n = report.attention;
        const serious = Q_RANK[report.worst] >= Q_RANK.error;
        return { status: serious ? 'attention' : 'review', text: `${serious ? '✕' : '⚠'} ${n} ${n === 1 ? 'thing' : 'things'} to review` };
    }

    // The top bar's summary from lessonQuality(): the older whole-report wording when the page has no qualityLesson()
    function lessonHeadline(lq) {
        if (!lq || !lq.checked) return { status: 'none', text: '◌ Quality not checked' };
        if (lq.legacyStale) return { status: 'stale', text: '◌ Quality: check again' };
        const changed = lq.changed ? ` · ${lq.changed} changed since the check` : '';
        if (!lq.attention) return { status: lq.changed || lq.stale ? 'stale' : 'good', text: `✓ Good${changed}` };
        const n = lq.attention;
        const serious = Q_RANK[lq.worst] >= Q_RANK.error;
        return { status: serious ? 'attention' : 'review', text: `${serious ? '✕' : '⚠'} ${n} ${n === 1 ? 'thing' : 'things'} to review${changed}` };
    }

    // An error's own text (an exception, the server's words): shown only with adapter.debug, after the plain words
    function detailOf(e) {
        if (e === null || e === undefined) return '';
        return str(typeof e === 'string' ? e : e.message, 160).replace(/\s+/g, ' ').trim();
    }

    function isTyping(target) {
        if (!target) return false;
        const tag = String(target.tagName || target.tag || '').toUpperCase();
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (target.isContentEditable === true) return true;
        const ce = typeof target.getAttribute === 'function' ? target.getAttribute('contenteditable') : null;
        return ce !== null && ce !== undefined && ce !== 'false';
    }

    // A control Space presses (a scene in the list or the timeline is not: Space plays there, Enter selects)
    function isButton(target) {
        if (!target) return false;
        if (/\beditor-(scene|tl-block)\b/.test(String(target.className || ''))) return false;
        const tag = String(target.tagName || target.tag || '').toUpperCase();
        return tag === 'BUTTON' || tag === 'A' || (typeof target.getAttribute === 'function' && target.getAttribute('role') === 'button');
    }

    // ---- the workspace -----------------------------------------------------------------------------------------------

    class EditorWorkspace {
        // doc: the page's document; model: AadhiEditor.EditorModel over the page's live slides; autosave: AadhiEditor.Autosave
        // (optional); adapter: the page's functions (see the end of this file)
        constructor({ doc, model, autosave = null, adapter = {} }) {
            this.doc = doc;
            this.model = model;
            this.autosave = autosave;
            this.adapter = adapter || {};
            this.root = null;
            this.isOpen = false;
            this.selectedId = null;
            this.time = 0;
            this.playing = false;
            this.inspectorOpen = true;
            this.previewing = false;
            this.helpOpen = false;
            this.zoom = 1;
            this.sectionChoice = new Map(); // a section the user opened (true) or closed (false); the others follow the scene
            this.opener = null;      // the control that had the focus before the editor opened (it gets it back on close)
            this.typing = null;      // the text field being typed in: one model transaction per focus session
            this.drag = null;        // a scene being dragged (timeline or list) or the playhead
            this.suppressClick = false;
            this.confirmState = null;
            this.message = '';
            this.messageKind = 'info';
            this.versionBusy = false;
            this.acting = 0;         // > 0 while the workspace itself is changing the model (its own render follows)
            this.cache = null;
            this.pointerDown = false; // a press inside the chrome not released yet
            this.renderPending = false;
            this.onKey = e => this.handleKey(e);
            // a press released: a redraw that waited for it happens now (after the click it belongs to)
            this.onPointerUp = () => {
                this.pointerDown = false;
                if (this.renderPending) this.later(() => this.flushRender());
            };
            this.unsubscribe = [];
        }

        get debug() { return !!this.adapter.debug; }

        h(...args) { return el(this.doc, ...args); }

        later(fn) {
            setTimeout(() => { if (this.isOpen) fn(); }, 0);
        }

        // A redraw after a text field lost focus waits: redrawing at once would replace the control being clicked or
        // tabbed to. It happens on the next turn, or when the pointer pressed in the chrome is released.
        renderLater() {
            this.renderPending = true;
            if (!this.pointerDown) {
                this.later(() => this.flushRender());
                return;
            }
            // a release the page never saw (outside the window): the redraw does not wait for ever
            const timer = setTimeout(() => {
                if (!this.isOpen || !this.renderPending) return;
                this.pointerDown = false;
                this.flushRender();
            }, 2000);
            if (timer && typeof timer.unref === 'function') timer.unref(); // (Node) never keeps a test run alive
        }

        flushRender() {
            if (this.renderPending && this.isOpen && !this.pointerDown) this.render();
        }

        // appends the children that exist (a missing optional control is null)
        add(node, ...children) {
            children.flat(Infinity).forEach(c => {
                if (c !== null && c !== undefined && c !== false) node.appendChild(typeof c === 'string' ? this.doc.createTextNode(c) : c);
            });
            return node;
        }

        call(name, ...args) {
            const fn = this.adapter[name];
            if (typeof fn !== 'function') return undefined;
            try {
                return fn.apply(this.adapter, args);
            } catch (e) {
                // (reported once: the report redraws the top bar, which may call the same failing function again)
                if (!this.reporting) {
                    this.reporting = true;
                    try { this.problem('That did not work. Your edits are safe: please try again.', e); } finally { this.reporting = false; }
                }
                return undefined;
            }
        }

        // A problem in plain words (what happened, whether the edits are safe, what to do); its own text only with debug
        problem(text, error = null) {
            const detail = this.debug ? detailOf(error) : '';
            this.note(detail ? `${text} (${detail})` : text, 'error');
        }

        scenes() { return Array.isArray(this.model.scenes) ? this.model.scenes : []; }

        // What the model says about every scene (timeline, edited / moved / origin, approvals), once per change
        view() {
            if (!this.cache) {
                const eff = typeof this.model.effective === 'function' ? this.model.effective() : [];
                const tl = typeof this.model.timeline === 'function' ? this.model.timeline() : { total: 0, scenes: [], transitions: [] };
                const byId = new Map(eff.map(e => [e.scene_id, e]));
                this.cache = { eff, tl, byId };
            }
            return this.cache;
        }

        info(id) { return this.view().byId.get(id) || null; }

        // ---- open / close ------------------------------------------------------------------------------------------

        open() {
            if (this.isOpen) return;
            this.isOpen = true;
            this.cache = null;
            const scenes = this.scenes();
            if (!this.selectedId || this.model.indexOf(this.selectedId) < 0) {
                const current = this.call('currentScene');
                const start = Number.isInteger(current) ? current : 0;
                const scene = scenes[Math.max(0, Math.min(start, scenes.length - 1))];
                this.selectedId = scene ? scene.scene_id : null;
            }
            this.time = this.startOf(this.selectedId);
            this.playing = !!this.call('isPlaying');
            const active = this.doc.activeElement;
            this.opener = active && active !== this.doc.body && typeof active.focus === 'function' ? active : null;
            this.build();
            this.doc.body.classList.add('editor-active');
            this.doc.addEventListener('keydown', this.onKey, true);
            this.doc.addEventListener('pointerup', this.onPointerUp, true);
            this.doc.addEventListener('pointercancel', this.onPointerUp, true);
            if (typeof this.model.subscribe === 'function') {
                const off = this.model.subscribe(ev => this.modelEvent(ev));
                if (typeof off === 'function') this.unsubscribe.push(off);
            }
            // the save state follows the autosave (its onState is chained while the editor is open, then given back)
            const a = this.autosave;
            if (a && 'onState' in a) {
                const previous = a.onState;
                const mine = (state, info) => {
                    if (typeof previous === 'function') { try { previous(state, info); } catch (e) { /* the page's problem */ } }
                    if (this.isOpen) this.renderTop();
                };
                a.onState = mine;
                this.unsubscribe.push(() => { if (a.onState === mine) a.onState = previous; });
            }
            this.render();
            // the timeline fits its width once it is on the page (and again when the window is resized)
            this.renderTimeline();
            const win = this.doc.defaultView;
            if (win && typeof win.addEventListener === 'function') {
                let waiting = false;
                const onResize = () => {
                    if (waiting) return;
                    waiting = true;
                    setTimeout(() => { waiting = false; if (this.isOpen && !this.drag) this.renderTimeline(); }, 150);
                };
                win.addEventListener('resize', onResize);
                this.unsubscribe.push(() => win.removeEventListener('resize', onResize));
            }
            // keyboard users start on the selected scene
            focusNode(list(this.sceneList.querySelectorAll('.editor-scene')).find(n => n.getAttribute('data-scene-id') === this.selectedId));
        }

        close() {
            if (!this.isOpen) return;
            if (this.typing) this.endTyping();
            if (this.drag) this.cancelDrag();
            if (this.confirmState) this.answer(false);
            this.isOpen = false;
            this.renderPending = false;
            this.pointerDown = false;
            this.doc.removeEventListener('keydown', this.onKey, true);
            this.doc.removeEventListener('pointerup', this.onPointerUp, true);
            this.doc.removeEventListener('pointercancel', this.onPointerUp, true);
            this.unsubscribe.splice(0).forEach(off => { try { off(); } catch (e) { /* already gone */ } });
            detach(this.root);
            this.root = null;
            this.top = this.topEls = this.sceneList = this.timeline = this.inspector = null;
            this.doc.body.classList.remove('editor-active');
            // the keyboard focus goes back to the control that opened the editor (when it is still on the page)
            const opener = this.opener;
            this.opener = null;
            if (opener && opener.isConnected !== false) focusNode(opener);
            // edits not saved yet are saved now (the autosave keeps one save at a time)
            if (this.autosave && this.model.dirty && typeof this.autosave.flush === 'function') {
                try {
                    const p = this.autosave.flush();
                    if (p && typeof p.catch === 'function') p.catch(() => {});
                } catch (e) { /* the autosave reports its own state */ }
            }
            this.call('closed');
        }

        modelEvent(ev) {
            this.cache = null;
            if (!this.isOpen || !ev) return;
            if (ev.kind === 'saved') { this.renderTop(); return; }
            if (this.acting || this.drag) return; // the workspace renders after its own change
            if (this.typing) { this.renderTop(); this.renderList(); this.renderTimeline(); return; }
            this.render(); // a change from elsewhere (a reload and replay after a stale save)
        }

        build() {
            const h = (...a) => this.h(...a);
            this.top = h('header', { class: 'editor-top', 'aria-label': 'Editor toolbar' });
            this.buildTop();
            this.sceneList = h('ol', { class: 'editor-scene-list', 'aria-label': 'Scenes' });
            this.sceneCount = h('span', { class: 'editor-scene-count' });
            this.scenesPanel = h('aside', { class: 'editor-scenes', 'aria-label': 'Scene list' },
                h('div', { class: 'editor-panel-head' }, h('span', { text: 'Scenes' }), this.sceneCount), this.sceneList);
            this.timeline = h('section', { class: 'editor-timeline', 'aria-label': 'Timeline' });
            this.inspector = h('aside', { class: 'editor-inspector', id: 'editor-inspector', 'aria-label': 'Scene inspector' });
            this.helpClose = h('button', { type: 'button', class: 'editor-btn editor-btn-sm', 'data-action': 'help-close', text: 'Close',
                'aria-label': 'Close the keyboard shortcuts', onclick: () => this.toggleHelp(false) });
            this.help = h('div', { class: 'editor-help', id: 'editor-help', role: 'dialog', 'aria-labelledby': 'editor-help-title', hidden: true },
                h('div', { class: 'editor-help-head' }, h('h3', { id: 'editor-help-title', text: 'Keyboard shortcuts' }), this.helpClose),
                h('dl', {}, SHORTCUTS.map(([key, what]) => [h('dt', {}, h('kbd', { text: key })), h('dd', { text: what })])),
                h('p', { class: 'editor-note', text: 'Shortcuts work while the editor is open and you are not typing in a field.' }));
            // the question asked before deleting / removing: a modal dialog over a backdrop (a press outside it is "Cancel")
            this.confirmBackdrop = h('div', { class: 'editor-confirm-backdrop', 'aria-hidden': 'true', hidden: true, onclick: () => this.answer(false) });
            this.confirmBox = h('div', { class: 'editor-confirm', role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': 'editor-confirm-title',
                'aria-describedby': 'editor-confirm-text', hidden: true });
            this.previewExit = h('button', { type: 'button', class: 'editor-btn editor-btn-primary editor-preview-exit', 'data-action': 'preview-exit',
                text: '✎ Back to editing', onclick: () => this.setPreview(false) });
            // the workspace is a modal dialog over the page (the live stage behind it is the preview)
            this.root = h('div', { class: 'editor-root', 'data-editor-runtime': true, 'data-inspector': 'open', role: 'dialog', 'aria-modal': 'true',
                'aria-label': 'Lesson editor' },
                this.top, this.scenesPanel,
                h('div', { class: 'editor-stage-hole', 'aria-hidden': 'true' }, h('p', { class: 'editor-stage-note', text: STAGE_NOTE })), this.inspector, this.timeline,
                this.help, this.confirmBackdrop, this.confirmBox, this.previewExit);
            // keys typed inside the chrome never reach the page's slide shortcuts (bubbling stops here)
            this.root.addEventListener('keydown', e => { if (e && typeof e.stopPropagation === 'function') e.stopPropagation(); });
            this.root.addEventListener('pointerdown', () => { this.pointerDown = true; }, true);
            this.doc.body.appendChild(this.root);
        }

        // ---- rendering ---------------------------------------------------------------------------------------------

        render() {
            if (!this.root) return;
            this.renderPending = false;
            this.cache = null;
            const focus = this.focusKey();
            this.ensureSelection();
            this.root.className = 'editor-root' + (this.previewing ? ' is-previewing' : '');
            this.root.setAttribute('data-inspector', this.inspectorOpen ? 'open' : 'closed');
            this.renderTop();
            this.renderList();
            this.renderTimeline();
            if (!this.typing) this.renderInspector();
            setHidden(this.help, !this.helpOpen);
            setHidden(this.previewExit, !this.previewing);
            this.renderConfirm();
            this.restoreFocus(focus);
        }

        // Keyboard focus survives a re-render: the same control again (a scene item follows the selection)
        focusKey() {
            const active = this.doc.activeElement;
            if (!active || !this.root || typeof active.getAttribute !== 'function') return null;
            if (typeof this.root.contains === 'function' && !this.root.contains(active)) return null;
            const scene = active.getAttribute('data-scene-id');
            if (scene && /\beditor-(scene|tl-block)\b/.test(active.className || '')) return { selector: /\beditor-scene\b/.test(active.className) ? '.editor-scene' : 'button.editor-tl-block', follow: true };
            for (const attr of ['data-action', 'data-field']) {
                const value = active.getAttribute(attr);
                if (value) {
                    const extra = ['data-section', 'data-key', 'data-value', 'data-slot'].map(a => (active.getAttribute(a) ? `[${a}="${active.getAttribute(a)}"]` : '')).join('');
                    return { selector: `[${attr}="${value}"]${extra}` };
                }
            }
            return null;
        }

        restoreFocus(key) {
            if (!key || !this.root || this.typing) return;
            let node = null;
            try {
                if (key.follow) node = list(this.root.querySelectorAll(key.selector)).find(n => n.getAttribute('data-scene-id') === this.selectedId) || null;
                else node = this.root.querySelector(key.selector);
            } catch (e) {
                node = null; // a value that makes no selector: focus stays where the browser puts it
            }
            if (node && node !== this.doc.activeElement) focusNode(node);
        }

        // the selected scene is gone (an undo removed it, a reload): the scene now at its place is selected
        ensureSelection() {
            const scenes = this.scenes();
            const index = this.selectedId ? this.model.indexOf(this.selectedId) : -1;
            if (index >= 0) { this.lastIndex = index; return; }
            const at = Math.max(0, Math.min(Number.isInteger(this.lastIndex) ? this.lastIndex : 0, scenes.length - 1));
            this.selectedId = scenes[at] ? scenes[at].scene_id : null;
            this.lastIndex = scenes[at] ? at : 0;
        }

        // The older whole-report path (quality() / qualityStale()): used when the page offers no qualityFor()
        quality() {
            const stale = typeof this.adapter.qualityStale === 'function' && !!this.call('qualityStale');
            return { stale, report: stale ? null : qualityReport(this.call('quality')) };
        }

        targetedQuality() { return typeof this.adapter.qualityFor === 'function'; }

        // A scene's quality: {stale, targeted, all, count, worst}, or null (not checked). With qualityFor(sceneId) the findings
        // follow the scene (a reorder keeps them on it) and only a scene changed since the check is stale; without it, the
        // whole report's findings by position, all stale together.
        sceneQuality(scene, index) {
            if (!scene) return null;
            const v = this.view();
            if (!v.quality) v.quality = new Map();
            const key = `${scene.scene_id}|${index}`;
            if (v.quality.has(key)) return v.quality.get(key);
            let out = null;
            if (this.targetedQuality()) {
                const r = this.call('qualityFor', scene.scene_id);
                if (plain(r)) {
                    const issues = (Array.isArray(r.issues) ? r.issues.slice(0, 100) : []).map(cleanIssue).filter(Boolean);
                    out = { stale: r.stale === true, targeted: true, ...countFindings(issues) };
                }
            } else {
                const q = this.quality();
                if (q.stale) out = { stale: true, targeted: false, all: [], count: 0, worst: null };
                else if (q.report) out = { stale: false, targeted: false, ...sceneFindings(q.report, index) };
            }
            v.quality.set(key, out);
            return out;
        }

        // The lesson's quality for the top bar: {checked, stale, attention, worst, lesson (lesson-level findings), changed}
        lessonQuality() {
            if (typeof this.adapter.qualityLesson === 'function') {
                const r = this.call('qualityLesson');
                if (!plain(r)) return { checked: false };
                const lesson = countFindings((Array.isArray(r.issues) ? r.issues.slice(0, 100) : []).map(cleanIssue).filter(Boolean));
                const scenes = this.scenes();
                const changed = this.targetedQuality() ? scenes.filter((sc, i) => { const q = this.sceneQuality(sc, i); return q && q.stale; }).length : 0;
                const c = plain(r.counts) ? r.counts : {};
                const levels = ['notice', 'warning', 'error', 'blocking'];
                let attention = null;
                let worst = null;
                if (levels.some(k => Number.isInteger(c[k]))) {
                    attention = levels.reduce((n, k) => n + (Number.isInteger(c[k]) && c[k] > 0 ? c[k] : 0), 0);
                    worst = levels.reduce((w, k) => (Number.isInteger(c[k]) && c[k] > 0 ? worse(w, k) : w), null);
                } else if (Number.isInteger(c.attention) && c.attention >= 0) {
                    attention = c.attention;
                    worst = isSeverity(c.worst) ? c.worst : lesson.worst;
                } else {
                    // from the findings themselves: the lesson's own, then each scene's (a scene changed since the check is left out)
                    attention = lesson.count;
                    worst = lesson.worst;
                    scenes.forEach((sc, i) => {
                        const q = this.sceneQuality(sc, i);
                        if (q && !q.stale && q.count) { attention += q.count; worst = worse(worst, q.worst); }
                    });
                }
                return { checked: true, stale: r.stale === true, attention, worst: worst || 'info', lesson, changed };
            }
            const q = this.quality();
            return { checked: !!q.report || q.stale, legacyStale: q.stale, stale: q.stale, attention: q.report ? q.report.attention : 0,
                worst: q.report ? q.report.worst : 'info', lesson: null, changed: 0 };
        }

        // "Check again" (adapter.qualityRun): the page checks the lesson; the editor shows the new findings
        async runQuality() {
            if (this.qualityBusy || typeof this.adapter.qualityRun !== 'function') return;
            if (this.typing) this.endTyping();
            this.qualityBusy = true;
            this.render();
            let failed = null;
            try {
                await this.adapter.qualityRun();
            } catch (e) {
                failed = e || new Error('');
            }
            this.qualityBusy = false;
            if (!this.isOpen) return;
            if (failed) this.problem('✕ The quality could not be checked. Your edits are safe: try “Check again” in a moment.', failed);
            else this.note('✓ Quality checked again.');
            this.render();
        }

        saveState() {
            const a = this.autosave;
            if (!a) return { state: 'saved', info: {} };
            return { state: SAVE_TEXT[a.state] ? a.state : 'saved', info: plain(a.info) ? a.info : {} };
        }

        saveNote(state, info) {
            const conflicts = Array.isArray(info.conflicts) ? info.conflicts : [];
            const failed = conflicts.filter(c => plain(c) && !c.applied);
            const changed = conflicts.filter(c => plain(c) && c.applied && c.reason === 'changed');
            const detail = this.debug && info.error ? ` (${detailOf(info.error)})` : '';
            if (state === 'conflict') {
                const parts = [info.saved ? 'The lesson changed elsewhere: your edits were applied to the newer version.'
                    : 'The lesson changed elsewhere and your edits were not saved yet. They are still here: Retry applies them to the newer version.'];
                if (failed.length) parts.push(`${failed.length} could not be applied: ${failed.slice(0, 3).map(c => str(c.label, 60)).filter(Boolean).join(', ')}.`);
                if (changed.length) parts.push(`${changed.length} replaced a newer change.`);
                return parts.join(' ') + detail;
            }
            if (state === 'error') {
                // what happened, that the edits are safe, what to do (the server's own words only with debug)
                if (info.locked) return `Pictures or clips are still being made: the new scene order is saved once they finish. Your edits are still here.${detail}`;
                if (info.status === 401 || info.status === 403) return `You may need to sign in again. Your edits are still here: sign in, then Retry.${detail}`;
                return `Your edits are still here. Check the connection, then Retry (the next change tries again too).${detail}`;
            }
            return '';
        }

        // The top bar is built once and updated in place: a save-state change never replaces a focused or pressed button
        buildTop() {
            const h = (...a) => this.h(...a);
            const t = {};
            t.close = h('button', { type: 'button', class: 'editor-btn editor-back', 'data-action': 'close', title: 'Close the editor (Esc)',
                onclick: () => this.close() }, h('span', { 'aria-hidden': 'true', text: '‹' }), t.closeText = h('span', { class: 'editor-label-wide' }));
            t.title = h('h2', { class: 'editor-title', id: 'editor-title' });
            // undo / redo say what they undo (label and tooltip from the model)
            t.undo = h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'undo', onclick: () => this.undo() },
                h('span', { 'aria-hidden': 'true', text: '↶' }), h('span', { class: 'editor-label-mid', text: 'Undo' }));
            t.redo = h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'redo', onclick: () => this.redo() },
                h('span', { 'aria-hidden': 'true', text: '↷' }), h('span', { class: 'editor-label-mid', text: 'Redo' }));
            // the save state: icon + words, announced (Saved / Saving… / Unsaved changes / Couldn't save + Retry)
            t.save = h('span', { class: 'editor-save', role: 'status', 'aria-live': 'polite' });
            t.quality = h('button', { type: 'button', class: 'editor-quality', 'data-action': 'quality', title: "Open the lesson's quality in Visual Review",
                onclick: () => this.call('showInReview', null) });
            t.lessonNote = h('span', { class: 'editor-quality-lesson', hidden: true });
            t.message = h('span', { class: 'editor-note editor-top-message', role: 'status', 'aria-live': 'polite', hidden: true });
            t.preview = h('button', { type: 'button', class: 'editor-btn', 'data-action': 'preview', 'aria-label': 'Preview: hide the editor to see the whole frame',
                title: 'Hide the editor to see the whole frame', onclick: () => this.setPreview(!this.previewing) },
                h('span', { 'aria-hidden': 'true', text: '👁' }), h('span', { class: 'editor-label-mid', text: 'Preview' }));
            // (the page saves a NEW lesson and goes on editing it: the original stays in Your lessons with its videos)
            t.version = h('button', { type: 'button', class: 'editor-btn', 'data-action': 'save-version',
                title: 'Saves a copy of this lesson in Your lessons and goes on editing the copy (videos already exported stay with the original)',
                onclick: () => this.saveVersion() });
            t.help = h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'help', 'aria-label': 'Keyboard shortcuts',
                title: 'Keyboard shortcuts (?)', 'aria-controls': 'editor-help', 'aria-haspopup': 'dialog', onclick: () => this.toggleHelp() },
                h('span', { 'aria-hidden': 'true', text: '?' }), h('span', { class: 'editor-label-mid', text: 'Shortcuts' }));
            // (on a tablet or a phone the bar wraps after the title: editor.css .editor-top-break)
            this.add(this.top, t.close, t.title, h('span', { class: 'editor-top-break', 'aria-hidden': 'true' }),
                h('div', { class: 'editor-top-group', role: 'group', 'aria-label': 'Undo and redo' }, t.undo, t.redo),
                t.save, t.quality, t.lessonNote, t.message,
                h('div', { class: 'editor-top-group editor-top-actions' }, t.preview, typeof this.adapter.saveVersion === 'function' ? t.version : null, t.help));
            this.topEls = t;
        }

        // Where "Back" goes: the Studio when the editor was opened from it (adapter.returnTo() === 'studio'), else the lesson
        returnInfo() {
            return this.call('returnTo') === 'studio' ? RETURN.studio : RETURN.lesson;
        }

        renderTop() {
            const t = this.topEls;
            if (!this.top || !t) return;
            const h = (...a) => this.h(...a);
            const m = this.model;
            const set = (node, attr, value) => { if (value === null || value === false) node.removeAttribute(attr); else node.setAttribute(attr, value === true ? '' : value); };
            const back = this.returnInfo();
            if (t.closeText.textContent !== back.text) t.closeText.textContent = back.text;
            t.close.setAttribute('aria-label', back.label);
            const title = str(this.call('lessonTitle'), 200).trim() || 'Untitled lesson';
            if (t.title.textContent !== title) t.title.textContent = title;
            t.title.setAttribute('title', title);
            const canUndo = typeof m.canUndo === 'function' && !!m.canUndo();
            const canRedo = typeof m.canRedo === 'function' && !!m.canRedo();
            const undoLabel = canUndo && typeof m.undoLabel === 'function' ? str(m.undoLabel(), 80) : '';
            const redoLabel = canRedo && typeof m.redoLabel === 'function' ? str(m.redoLabel(), 80) : '';
            set(t.undo, 'disabled', !canUndo);
            t.undo.setAttribute('title', canUndo ? `${undoLabel || 'Undo'} (Ctrl+Z)` : 'Nothing to undo');
            t.undo.setAttribute('aria-label', canUndo ? undoLabel || 'Undo' : 'Undo: nothing to undo');
            set(t.redo, 'disabled', !canRedo);
            t.redo.setAttribute('title', canRedo ? `${redoLabel || 'Redo'} (Ctrl+Shift+Z)` : 'Nothing to redo');
            t.redo.setAttribute('aria-label', canRedo ? redoLabel || 'Redo' : 'Redo: nothing to redo');
            // the save state: icon + words, Retry when a save failed, a note when the lesson changed elsewhere
            const save = this.saveState();
            const note = this.saveNote(save.state, save.info);
            const retry = save.state === 'error' || (save.state === 'conflict' && !save.info.saved);
            const key = `${save.state}|${retry}|${note}`;
            if (t.save.getAttribute('data-key') !== key) {
                t.save.setAttribute('data-key', key);
                t.save.setAttribute('data-state', save.state);
                t.save.textContent = '';
                this.add(t.save,
                    h('span', { class: 'editor-save-text', text: SAVE_TEXT[save.state] }),
                    retry ? h('button', { type: 'button', class: 'editor-btn editor-btn-link', 'data-action': 'retry-save', text: 'Retry',
                        'aria-label': 'Try saving the edits again', onclick: () => this.retrySave() }) : null,
                    note ? h('span', { class: 'editor-save-note', text: note, title: note }) : null);
            }
            const lq = this.lessonQuality();
            const headline = lessonHeadline(lq);
            if (t.quality.textContent !== headline.text) t.quality.textContent = headline.text;
            t.quality.setAttribute('data-status', headline.status);
            // the lesson-level findings (not about one scene): the most serious one, the rest in its tooltip
            const own = lq.lesson && lq.lesson.all.length ? lq.lesson.all : [];
            const lessonText = own.length ? `${severityText(own[0].severity)}: ${own[0].message}${own.length > 1 ? ` (+${own.length - 1} more)` : ''}${lq.stale ? ' · changed since the check' : ''}` : '';
            if (t.lessonNote.textContent !== lessonText) t.lessonNote.textContent = lessonText;
            set(t.lessonNote, 'hidden', !lessonText);
            if (own.length) {
                t.lessonNote.setAttribute('data-severity', own[0].severity);
                t.lessonNote.setAttribute('title', own.map(f => `${severityText(f.severity)}: ${f.message}`).join('\n'));
            }
            if (t.message.textContent !== this.message) t.message.textContent = this.message;
            t.message.setAttribute('data-kind', this.messageKind);
            t.message.setAttribute('title', this.message); // (the whole line when the bar is too narrow for it)
            set(t.message, 'hidden', !this.message);
            t.preview.setAttribute('aria-pressed', String(this.previewing));
            const version = this.versionBusy ? 'Saving a copy…' : 'Save as a copy';
            if (t.version.textContent !== version) t.version.textContent = version;
            set(t.version, 'disabled', this.versionBusy);
            t.help.setAttribute('aria-expanded', String(this.helpOpen));
        }

        renderList() {
            if (!this.sceneList) return;
            const h = (...a) => this.h(...a);
            const scenes = this.scenes();
            this.sceneList.textContent = '';
            const hidden = scenes.filter(isHidden).length;
            this.sceneCount.textContent = hidden ? `${scenes.length} · ${hidden} hidden` : String(scenes.length);
            if (!scenes.length) {
                this.sceneList.appendChild(h('li', { class: 'editor-empty', text: 'This lesson has no scenes yet.' }));
                return;
            }
            scenes.forEach((scene, index) => {
                const id = scene.scene_id;
                const info = this.info(id) || {};
                const selected = id === this.selectedId;
                const hiddenScene = isHidden(scene);
                const seconds = info.play_seconds;
                const marks = [];
                const approval = approvalMark(info.approvals);
                if (approval) marks.push(h('span', { class: 'editor-mark', 'data-approval': approval, text: APPROVAL[approval] }));
                const f = this.sceneQuality(scene, index);
                if (f && f.stale && f.targeted) {
                    marks.push(h('span', { class: 'editor-mark editor-quality-chip', 'data-stale': 'true',
                        title: 'Quality: this scene changed since the last check', text: '◌ Changed since the check' }));
                } else if (f && !f.stale && f.count) {
                    const sev = QUALITY_SEVERITY[f.worst];
                    marks.push(h('span', { class: 'editor-mark editor-quality-chip', 'data-severity': f.worst,
                        title: `Quality: ${f.count} ${f.count === 1 ? 'thing' : 'things'} to look at in this scene`, text: `${sev.icon} ${f.count} · ${sev.label}` }));
                }
                if (hiddenScene) marks.push(h('span', { class: 'editor-mark', 'data-mark': 'hidden', text: '⊘ Hidden' }));
                if (info.origin && info.origin !== 'generated') marks.push(h('span', { class: 'editor-mark', 'data-mark': 'new', text: '＋ New' }));
                else if (info.moved) marks.push(h('span', { class: 'editor-mark', 'data-mark': 'moved', text: '⇄ Moved' }));
                if (this.isEdited(scene, info)) marks.push(h('span', { class: 'editor-mark', 'data-mark': 'edited', text: '✎ Edited' }));
                const button = h('button', { type: 'button', class: 'editor-scene' + (hiddenScene ? ' is-hidden' : ''), 'data-scene-id': id, 'data-index': String(index),
                    'aria-current': selected ? 'true' : 'false',
                    'aria-label': `Scene ${index + 1}: ${sceneTitle(scene)}, ${typeText(scene)}, ${hiddenScene ? 'not played' : secondsText(seconds)}${marks.length ? '; ' + marks.map(m => m.textContent.replace(/^\W+/u, '')).join(', ') : ''}`,
                    onclick: () => { if (this.suppressClick) { this.suppressClick = false; return; } this.select(id, { seek: true }); } },
                h('span', { class: 'editor-scene-index', text: String(index + 1) }),
                h('span', { class: 'editor-scene-title', text: sceneTitle(scene) }),
                h('span', { class: 'editor-scene-meta', text: `${typeText(scene)} · ${hiddenScene ? 'not played' : secondsText(seconds)}` }),
                marks.length ? h('span', { class: 'editor-scene-marks' }, marks) : null);
                this.attachSceneDrag(button, id, 'list');
                this.sceneList.appendChild(h('li', { class: 'editor-scene-item' }, button));
            });
        }

        isEdited(scene, info) {
            const e = edit(scene);
            return !!((Array.isArray(info.edited) && info.edited.length) || finite(e.min_seconds) || e.captions === 'off' || e.narration_muted === true);
        }

        // ---- timeline maths (pixels <-> seconds, from the model's timeline) -------------------------------------------

        segments() {
            const { tl } = this.view();
            const scenes = this.scenes();
            const entries = Array.isArray(tl.scenes) ? tl.scenes : [];
            const pps = this.pixelsPerSecond(finite(tl.total) ? tl.total : 0);
            let x = 0;
            return scenes.map((scene, index) => {
                const t = entries[index] || { start: 0, end: 0, hidden: isHidden(scene) };
                const hidden = !!t.hidden;
                const seconds = hidden ? 0 : Math.max(0, (t.end || 0) - (t.start || 0));
                const w = hidden ? COLLAPSED_PX : Math.max(MIN_BLOCK_PX, seconds * pps);
                const seg = { scene, id: scene.scene_id, index, hidden, start: t.start || 0, seconds, play: t.play_seconds || 0,
                    transition: t.transition_seconds || 0, x, w };
                x += w;
                return seg;
            });
        }

        pixelsPerSecond(total) {
            let width = 0;
            const box = this.tlScroll && typeof this.tlScroll.getBoundingClientRect === 'function' ? this.tlScroll.getBoundingClientRect() : null;
            if (box && box.width) width = box.width - LABEL_PX - 16;
            const fit = width > 0 && total > 0 ? width / total : DEFAULT_PPS;
            return Math.max(2, Math.min(200, fit * this.zoom));
        }

        totalSeconds() {
            const total = this.view().tl.total;
            return finite(total) ? total : 0;
        }

        startOf(id) {
            const seg = this.segments().find(s => s.id === id);
            return seg ? seg.start : 0;
        }

        xAt(time, segs) {
            const visible = segs.filter(s => !s.hidden);
            const last = segs[segs.length - 1];
            if (!last) return 0;
            for (const s of visible) {
                if (time < s.start + s.seconds || s === visible[visible.length - 1]) {
                    const k = s.seconds > 0 ? Math.max(0, Math.min(1, (time - s.start) / s.seconds)) : 0;
                    return s.x + k * s.w;
                }
            }
            return last.x + last.w;
        }

        timeAt(x, segs) {
            for (const s of segs) {
                if (x < s.x + s.w) {
                    if (s.hidden || s.seconds <= 0) return s.start;
                    return s.start + Math.max(0, Math.min(1, (x - s.x) / s.w)) * s.seconds;
                }
            }
            return this.totalSeconds();
        }

        // the scene boundary (a visible scene's start) nearest to `time`
        snap(time, segs) {
            let best = null;
            segs.filter(s => !s.hidden).forEach(s => { if (!best || Math.abs(s.start - time) < Math.abs(best.start - time)) best = s; });
            return best;
        }

        renderTimeline() {
            if (!this.timeline) return;
            const h = (...a) => this.h(...a);
            const segs = this.segments();
            const total = this.totalSeconds();
            const width = segs.length ? segs[segs.length - 1].x + segs[segs.length - 1].w : 0;
            const transitions = new Map((this.view().tl.transitions || []).map(t => [t.to, t]));
            this.time = Math.max(0, Math.min(this.time, total));
            this.timeline.textContent = '';
            const bar = h('div', { class: 'editor-tl-bar' },
                h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'prev-scene', 'aria-label': 'Previous scene', title: 'Previous scene (←)',
                    text: '⏮', onclick: () => this.step(-1) }),
                h('button', { type: 'button', class: 'editor-btn', 'data-action': 'play', 'aria-pressed': String(this.playing),
                    'aria-label': this.playing ? 'Pause' : 'Play', title: 'Play / pause (Space)', text: this.playing ? '❚❚ Pause' : '▶ Play',
                    onclick: () => this.togglePlay() }),
                h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'next-scene', 'aria-label': 'Next scene', title: 'Next scene (→)',
                    text: '⏭', onclick: () => this.step(1) }),
                this.timeLabel = h('span', { class: 'editor-tl-time', title: 'Current time / total duration', text: `${clock(this.time)} / ${clock(total)}` }),
                h('span', { class: 'editor-tl-spacer' }),
                // the inspector shown / hidden (on a tablet it takes the tracks' place: this brings them back)
                h('button', { type: 'button', class: 'editor-btn', 'data-action': 'toggle-inspector', 'aria-controls': 'editor-inspector',
                    title: this.inspectorOpen ? 'Hide the scene inspector (Esc)' : 'Show the scene inspector',
                    text: this.inspectorOpen ? 'Hide inspector' : 'Show inspector', onclick: () => this.setInspector(!this.inspectorOpen) }),
                h('span', { class: 'editor-row editor-tl-zoom', role: 'group', 'aria-label': 'Timeline zoom' },
                    h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'zoom-out', 'aria-label': 'Zoom out', title: 'Zoom out', text: '−',
                        onclick: () => { this.zoom = Math.max(0.25, this.zoom / 1.5); this.renderTimeline(); } }),
                    h('button', { type: 'button', class: 'editor-btn', 'data-action': 'zoom-fit', 'aria-label': 'Fit the lesson in the timeline', title: 'Fit the lesson in the timeline',
                        text: 'Fit', onclick: () => { this.zoom = 1; this.renderTimeline(); } }),
                    h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'zoom-in', 'aria-label': 'Zoom in', title: 'Zoom in', text: '+',
                        onclick: () => { this.zoom = Math.min(16, this.zoom * 1.5); this.renderTimeline(); } })));
            // (shown by editor.css: on a phone the tracks are left out; on a tablet the open inspector takes the detail tracks' place)
            const note = h('p', { class: 'editor-tl-note', text: SMALL_SCREEN_NOTE });
            const sheetNote = h('p', { class: 'editor-tl-sheet-note', text: 'Only the scenes show while the inspector is open: “Hide inspector” shows every track.' });
            const lane = () => h('div', { class: 'editor-tl-lane', css: { width: `${Math.round(width)}px` } });
            const row = (track, label, laneNode) => h('div', { class: 'editor-tl-row', 'data-track': track }, h('span', { class: 'editor-tl-label', text: label }), laneNode);
            const box = seg => ({ left: `${Math.round(seg.x)}px`, width: `${Math.max(1, Math.round(seg.w) - 2)}px` });

            // time ruler: a tick about every 70 px
            const ruler = lane();
            const sample = segs.find(s => !s.hidden && s.seconds > 0);
            const perSecond = sample ? sample.w / sample.seconds : DEFAULT_PPS;
            const step = TICK_STEPS.find(s => s * perSecond >= 70) || TICK_STEPS[TICK_STEPS.length - 1];
            for (let t = 0; t <= total + 1e-6; t += step) {
                ruler.appendChild(h('span', { class: 'editor-tl-tick', css: { left: `${Math.round(this.xAt(t, segs))}px` } }, h('span', { text: clock(t) })));
            }
            ruler.setAttribute('title', 'Press or drag to move the playhead');
            this.attachPlayheadDrag(ruler);

            const sceneLane = lane();
            const narrationLane = lane();
            const presenterLane = lane();
            const visualLane = lane();
            const captionLane = lane();
            const lessonCaptions = this.captionsVisible();
            const vocab = this.vocabulary();
            const block = (seg, extra, ...children) => h('div', Object.assign({ class: 'editor-tl-block' + (seg.hidden ? ' is-hidden' : ''),
                'data-scene-id': seg.id, css: box(seg) }, extra), ...children);
            segs.forEach(seg => {
                const s = seg.scene;
                const title = sceneTitle(s);
                const selected = seg.id === this.selectedId;
                const mark = seg.hidden ? null : this.blockQualityMark(s, seg.index);
                // the scene block: title + its own play time, as in the scene list (+ its quality mark); the width also covers the
                // transition into it; hidden scenes collapsed and striped
                const sceneBlock = h('button', { type: 'button', class: 'editor-tl-block' + (seg.hidden ? ' is-hidden' : ''), 'data-scene-id': seg.id,
                    'data-index': String(seg.index), 'aria-current': selected ? 'true' : 'false', css: box(seg),
                    'aria-label': `Scene ${seg.index + 1}: ${title}, ${seg.hidden ? 'hidden' : secondsText(seg.play)}${mark ? `; quality: ${mark.getAttribute('title').replace(/^Quality: /, '')}` : ''}`,
                    title: `${seg.index + 1}. ${title} · ${seg.hidden ? 'Hidden' : secondsText(seg.play)}`,
                    onclick: () => { if (this.suppressClick) { this.suppressClick = false; return; } this.select(seg.id, { seek: true }); } },
                seg.hidden ? null : h('span', { class: 'editor-tl-block-title', text: `${seg.index + 1}. ${title}` }),
                seg.hidden ? null : h('span', { class: 'editor-tl-block-time' }, secondsText(seg.play), mark ? ' ' : null, mark));
                this.attachSceneDrag(sceneBlock, seg.id, 'timeline');
                sceneLane.appendChild(sceneBlock);
                // the transition into the scene (between two played blocks)
                const t = transitions.get(seg.id);
                if (t && !seg.hidden) {
                    const words = vocab.transition[t.kind] || humanize(t.kind) || 'Transition';
                    sceneLane.appendChild(h('button', { type: 'button', class: 'editor-tl-transition', 'data-scene-id': seg.id, 'data-transition': str(t.kind, 20),
                        css: { left: `${Math.round(seg.x)}px` }, 'aria-label': `Transition into scene ${seg.index + 1}: ${words}`,
                        title: `Transition: ${words}${t.seconds ? ` (${secondsText(t.seconds)})` : ''}`,
                        onclick: () => { this.selectedId = seg.id; this.openSection('transition'); } }));
                }
                if (seg.hidden) {
                    [narrationLane, presenterLane, visualLane, captionLane].forEach(l => l.appendChild(block(seg, { 'data-state': 'hidden', 'aria-hidden': 'true' })));
                    return;
                }
                // narration: a bar where the scene has narration; muted shown striped
                const narration = str(s.narration, 20000).replace(/\[[A-Z]+(?::[0-9.]+)?\]/g, '').trim();
                if (narration) {
                    const muted = edit(s).narration_muted === true;
                    narrationLane.appendChild(block(seg, { 'data-state': muted ? 'muted' : 'on', title: muted ? 'Narration muted' : 'Narration' },
                        h('span', { text: muted ? '🔇 Muted' : '🗣 Narration' })));
                }
                const pres = presenterInfo(s);
                presenterLane.appendChild(block(seg, { 'data-state': pres.state, title: `Presenter: ${pres.text}` }, h('span', { text: pres.text })));
                const vk = visualKind(s);
                visualLane.appendChild(block(seg, { 'data-state': vk, title: `Visual: ${VISUAL_KIND[vk]}` }, h('span', { text: VISUAL_KIND[vk] })));
                const captionsOn = lessonCaptions && edit(s).captions !== 'off';
                captionLane.appendChild(block(seg, { 'data-state': captionsOn ? 'on' : 'off', title: `Captions ${captionsOn ? 'on' : 'off'}` },
                    h('span', { text: captionsOn ? 'Captions on' : 'Captions off' })));
            });
            this.tlDrop = h('div', { class: 'editor-tl-drop', hidden: true });
            sceneLane.appendChild(this.tlDrop);
            this.playhead = h('div', { class: 'editor-playhead', 'aria-hidden': 'true', css: { left: `${LABEL_PX + Math.round(this.xAt(this.time, segs))}px` } },
                h('span', { class: 'editor-playhead-handle' }));
            this.attachPlayheadDrag(this.playhead.firstElementChild);
            const content = h('div', { class: 'editor-tl-content', css: { width: `${LABEL_PX + Math.round(width) + 16}px` } },
                row('ruler', 'Time', ruler),
                row('scene', 'Scenes', sceneLane),
                row('narration', 'Narration', narrationLane),
                row('presenter', 'Presenter', presenterLane),
                row('visual', 'Visual', visualLane),
                row('caption', 'Captions', captionLane),
                this.playhead);
            this.sceneLane = sceneLane;
            this.ruler = ruler;
            const scroll = this.tlScroll ? this.tlScroll.scrollLeft : 0;
            this.tlScroll = h('div', { class: 'editor-tl-scroll' }, content);
            if (scroll) this.tlScroll.scrollLeft = scroll;
            this.add(this.timeline, bar, note, sheetNote, this.tlScroll);
        }

        // The scene block's quality mark (icon + words), or null
        blockQualityMark(scene, index) {
            const q = this.sceneQuality(scene, index);
            if (!q) return null;
            if (q.stale && q.targeted) {
                return this.h('span', { class: 'editor-tl-quality', 'data-stale': 'true', title: 'Quality: changed since the last check', text: '◌ Changed' });
            }
            if (q.stale || !q.count) return null;
            return this.h('span', { class: 'editor-tl-quality', 'data-severity': q.worst,
                title: `Quality: ${q.count} ${q.count === 1 ? 'thing' : 'things'} to look at (${QUALITY_SEVERITY[q.worst].label})`, text: `${severityText(q.worst)} (${q.count})` });
        }

        updatePlayhead() {
            const segs = this.segments();
            if (this.playhead) this.playhead.style.left = `${LABEL_PX + Math.round(this.xAt(this.time, segs))}px`;
            if (this.timeLabel) this.timeLabel.textContent = `${clock(this.time)} / ${clock(this.totalSeconds())}`;
        }

        captionsVisible() {
            const c = plain(this.model.editor) ? this.model.editor.captions : null;
            return !(plain(c) && c.visible === false);
        }

        vocabulary() {
            const given = this.call('vocabulary');
            const v = plain(given) ? given : {};
            const out = {};
            Object.keys(DEFAULT_VOCABULARY).forEach(key => {
                const ok = Array.isArray(DEFAULT_VOCABULARY[key]) ? Array.isArray(v[key]) && v[key].length : plain(v[key]) && Object.keys(v[key]).length;
                out[key] = ok ? v[key] : DEFAULT_VOCABULARY[key];
            });
            return out;
        }

        // ---- inspector ---------------------------------------------------------------------------------------------

        renderInspector() {
            if (!this.inspector) return;
            const h = (...a) => this.h(...a);
            this.inspector.textContent = '';
            setHidden(this.inspector, !this.inspectorOpen);
            const scene = this.model.scene(this.selectedId);
            const index = this.model.indexOf(this.selectedId);
            this.inspector.appendChild(h('div', { class: 'editor-panel-head' },
                h('span', { text: scene ? `Scene ${index + 1} of ${this.scenes().length}` : 'Inspector' }),
                h('button', { type: 'button', class: 'editor-btn editor-icon-btn', 'data-action': 'close-inspector', 'aria-label': 'Close the inspector',
                    title: 'Close the inspector (Esc)', text: '✕', onclick: () => this.setInspector(false) })));
            const body = h('div', { class: 'editor-inspector-body' });
            this.inspector.appendChild(body);
            if (!scene) {
                body.appendChild(h('p', { class: 'editor-empty', text: 'Choose a scene to change it.' }));
                return;
            }
            const info = this.info(scene.scene_id) || {};
            const findings = this.sceneQuality(scene, index);
            const layout = inspectorLayout(scene, index);
            this.inspector.setAttribute('data-focus', layout.focus);
            const summaries = {
                timing: isHidden(scene) ? 'Hidden' : secondsText(info.play_seconds),
                narration: edit(scene).narration_muted === true ? 'Muted' : '',
                presenter: presenterInfo(scene).text,
                visual: VISUAL_KIND[visualKind(scene)],
                quality: findings && findings.stale ? '◌ Changed' : findings && findings.count ? `${severityText(findings.worst)} (${findings.count})` : ''
            };
            layout.order.forEach(key => {
                const sec = SECTION_BY_KEY.get(key);
                if (sec.advanced && !body.querySelector('.editor-group-head')) body.appendChild(h('h3', { class: 'editor-group-head', text: 'Look and motion' }));
                // a hidden presenter: one line and "Show presenter" (the position and size come back with it)
                if (key === 'presenter' && layout.presenter === 'hidden') {
                    body.appendChild(this.presenterShowRow(scene));
                    return;
                }
                const open = this.sectionChoice.has(key) ? this.sectionChoice.get(key) : !!sec.open || key === layout.lead;
                const bodyId = `editor-section-${key}`;
                const section = h('section', { class: 'editor-section', 'data-section': key },
                    h('button', { type: 'button', class: 'editor-section-head', 'aria-expanded': String(open), 'aria-controls': bodyId, 'data-action': 'section',
                        'data-section': key, onclick: () => this.toggleSection(key) },
                    h('span', { text: sec.label }),
                    summaries[key] ? h('span', { class: 'editor-section-summary', text: summaries[key] }) : null));
                if (open) {
                    const content = h('div', { class: 'editor-section-body', id: bodyId });
                    this[`section_${key}`](content, scene, index, { info, findings });
                    section.appendChild(content);
                }
                body.appendChild(section);
            });
            if (this.debug) {
                const plan = plain(scene.cinematic_plan) ? scene.cinematic_plan : {};
                body.appendChild(h('section', { class: 'editor-section', 'data-section': 'debug' },
                    h('pre', { class: 'editor-debug', text: JSON.stringify({ scene_id: scene.scene_id, edit: scene.edit || null,
                        overrides: plain(scene.visual_review) && plain(scene.visual_review.composition) ? scene.visual_review.composition.overrides || null : null,
                        plan_hash: plan.plan_hash || null, fingerprint: plan.fingerprint || null }, null, 2) })));
            }
        }

        // Whether a section is open for this scene (the user's own choice, else open by default / the scene's lead)
        sectionOpen(key) {
            if (this.sectionChoice.has(key)) return this.sectionChoice.get(key);
            const scene = this.model.scene(this.selectedId);
            const sec = SECTION_BY_KEY.get(key);
            return !!(sec && sec.open) || (!!scene && inspectorLayout(scene, this.model.indexOf(this.selectedId)).lead === key);
        }

        // The hidden presenter's line: "Show presenter" (its side from the plan), "Automatic" when hiding it was the user's choice
        presenterShowRow(scene) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const chosen = override(scene, 'presenter_position') === 'hidden' || override(scene, 'presenter_size') === 'hidden';
            return h('section', { class: 'editor-section editor-section-compact', 'data-section': 'presenter', 'data-presenter': 'hidden' },
                h('span', { class: 'editor-compact-label', text: 'Presenter' }),
                h('span', { class: 'editor-state', 'data-state': chosen ? 'edited' : 'generated', text: chosen ? '✎ Edited' : 'Automatic' }),
                h('span', { class: 'editor-note', text: 'Hidden in this scene.' }),
                h('button', { type: 'button', class: 'editor-btn', 'data-action': 'show-presenter', text: '👤 Show presenter',
                    title: 'Show the presenter in this scene (then choose its side and size)', onclick: () => this.showPresenter(id) }),
                chosen && override(scene, 'presenter_position') === 'hidden' ? h('button', { type: 'button', class: 'editor-btn editor-btn-link', 'data-action': 'override',
                    'data-key': 'presenter_position', 'data-value': 'auto', text: 'Automatic', 'aria-label': 'Automatic: let the layout decide whether the presenter shows',
                    onclick: () => this.setOverride(id, 'presenter_position', 'auto') }) : null);
        }

        fieldHead(label, edited, { revert = null, forId = null } = {}) {
            const h = (...a) => this.h(...a);
            return h('div', { class: 'editor-field-head' },
                forId ? h('label', { for: forId, text: label }) : h('span', { text: label }),
                h('span', { class: 'editor-state', 'data-state': edited ? 'edited' : 'generated', text: edited ? '✎ Edited' : 'Automatic' }),
                edited && revert ? h('button', { type: 'button', class: 'editor-btn editor-btn-link', 'data-action': 'revert', 'data-field': revert.field,
                    text: 'Revert', 'aria-label': `Revert the ${label.toLowerCase().replace(/\s*\(.*\)$/, '')} to the generated text`, onclick: revert.fn }) : null);
        }

        // A text field typed in as ONE model transaction per focus session
        textField(scene, field, label, value, { multiline = false, write, original = v => str(v, 4000) } = {}) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const originals = plain(edit(scene).original) ? edit(scene).original : {};
            const edited = Object.prototype.hasOwnProperty.call(originals, field);
            const inputId = `editor-field-${field}`;
            const input = h(multiline ? 'textarea' : 'input', { id: inputId, class: multiline ? 'editor-textarea' : 'editor-input', 'data-field': field,
                type: multiline ? null : 'text', value });
            const read = () => String(input.value === undefined || input.value === null ? '' : input.value);
            input.addEventListener('focus', () => this.beginTyping(id, field, label));
            input.addEventListener('input', () => this.typeValue(id, field, label, () => write(read())));
            input.addEventListener('blur', () => this.endTyping());
            if (!multiline) {
                input.addEventListener('keydown', e => {
                    if (e && e.key === 'Enter') {
                        if (e.preventDefault) e.preventDefault();
                        this.endTyping();
                    }
                });
            }
            return h('div', { class: 'editor-field', 'data-field-box': field },
                this.fieldHead(label, edited, { revert: { field, fn: () => this.revertField(id, field) }, forId: inputId }),
                input,
                edited ? h('p', { class: 'editor-generated', 'data-generated': field, text: `Generated: ${original(originals[field]) || '(empty)'}` }) : null);
        }

        section_scene(box, scene, index, { info }) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const count = this.scenes().length;
            const hidden = isHidden(scene);
            const btn = (text, action, fn, extra = {}) => h('button', Object.assign({ type: 'button', class: 'editor-btn', 'data-action': action, text, onclick: fn }, extra));
            const plan = plain(scene.cinematic_plan) ? scene.cinematic_plan : {};
            const layoutText = typeof plan.template_label === 'string' && plan.template_label.trim() ? str(plan.template_label, 60).trim() : '';
            this.add(box,
                h('dl', { class: 'editor-facts' },
                    h('dt', { text: 'Title' }), h('dd', { class: 'editor-fact-title', text: sceneTitle(scene) }),
                    h('dt', { text: 'Type' }), h('dd', { text: typeText(scene) }),
                    // what leads the scene (the inspector shows its settings first)
                    h('dt', { text: 'Layout' }), h('dd', { 'data-fact': 'layout', text: layoutText || FOCUS_TEXT[sceneFocus(scene)] }),
                    info.origin && info.origin !== 'generated' ? [h('dt', { text: 'Added' }), h('dd', { text: { inserted: 'Added in the editor',
                        duplicated: 'A copy of another scene', split: 'Split from the scene before' }[info.origin] || 'Added in the editor' })] : null),
                h('div', { class: 'editor-row' },
                    btn(hidden ? '👁 Show scene' : '⊘ Hide scene', 'toggle-hidden', () => this.run(id, ['hidden'], () => this.model.setHidden(id, !hidden)),
                        { 'aria-pressed': String(hidden), title: hidden ? 'Play this scene again' : 'Skip this scene in the preview and the export (it stays in the lesson)' }),
                    btn('⧉ Duplicate', 'duplicate', () => this.duplicate(id)),
                    btn('🗑 Delete', 'delete', () => this.deleteScene(id), { class: 'editor-btn editor-btn-danger' })),
                h('div', { class: 'editor-field' },
                    h('div', { class: 'editor-field-head', text: 'Move' }),
                    h('div', { class: 'editor-row' },
                        btn('⇤ Start', 'move-start', () => this.moveTo(id, 0), { disabled: index === 0, 'aria-label': 'Move the scene to the start' }),
                        btn('◀ Earlier', 'move-left', () => this.moveTo(id, index - 1), { disabled: index === 0, 'aria-label': 'Move the scene one place earlier' }),
                        btn('Later ▶', 'move-right', () => this.moveTo(id, index + 1), { disabled: index >= count - 1, 'aria-label': 'Move the scene one place later' }),
                        btn('End ⇥', 'move-end', () => this.moveTo(id, count - 1), { disabled: index >= count - 1, 'aria-label': 'Move the scene to the end' }))),
                h('div', { class: 'editor-field' },
                    h('div', { class: 'editor-field-head', text: 'Insert after this scene' }),
                    // (a copy of this scene: "Duplicate" above, offered once)
                    h('div', { class: 'editor-row' },
                        btn('＋ Blank scene', 'insert-blank', () => this.insertAfter(id, 'blank')),
                        typeof this.adapter.pickAsset === 'function' ? btn('🖼 Picture from library', 'insert-picture', () => this.insertPicture(id)) : null)));
            const points = typeof this.model.splitPoints === 'function' ? this.model.splitPoints(id) || [] : [];
            if (points.length) {
                const select = h('select', { id: 'editor-field-split', class: 'editor-select', 'data-field': 'split-point', 'aria-label': 'Where to split' },
                    points.map((p, i) => h('option', { value: String(i), text: this.splitText(p, i) })));
                select.value = '0';
                box.appendChild(h('div', { class: 'editor-field' },
                    h('div', { class: 'editor-field-head' }, h('label', { for: 'editor-field-split', text: 'Split at a pause in the narration' })),
                    h('div', { class: 'editor-row' }, select,
                        btn('✂ Split here', 'split', () => {
                            const i = parseInt(select.value, 10);
                            const point = points[Number.isInteger(i) && points[i] ? i : 0];
                            this.split(id, point.index);
                        }))));
            }
        }

        splitText(point, i) {
            const before = str(point && point.before, 80).trim();
            const at = point && finite(point.seconds) ? ` (at ${clock(point.seconds)})` : '';
            return (before ? `After “${before}”` : `Pause ${i + 1}`) + at;
        }

        section_timing(box, scene, index, { info }) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const min = finite(info.min_seconds) ? info.min_seconds : null;
            const input = h('input', { id: 'editor-field-min-seconds', class: 'editor-input editor-input-short', type: 'number', min: '0.5', max: '600', step: '0.5',
                'data-field': 'min_seconds', value: min === null ? '' : String(min), placeholder: 'Auto' });
            input.addEventListener('change', () => {
                const raw = String(input.value === undefined || input.value === null ? '' : input.value).trim();
                // a number field's change comes as it loses focus: the redraw waits (the control clicked next survives)
                if (!raw) { if (min !== null) this.run(id, ['timing'], () => this.model.setDuration(id, null), { later: true }); return; }
                const value = Number(raw);
                if (!finite(value) || value <= 0 || value > 600) {
                    input.value = min === null ? '' : String(min);
                    this.note('The minimum duration is between 0.5 and 600 seconds: type a number in that range, or leave it empty for automatic.', 'error');
                    return;
                }
                if (value !== min) this.run(id, ['timing'], () => this.model.setDuration(id, value), { later: true });
            });
            box.appendChild(h('div', { class: 'editor-field', 'data-field-box': 'min_seconds' },
                this.fieldHead('Minimum duration (seconds)', min !== null, { forId: 'editor-field-min-seconds' }),
                h('div', { class: 'editor-row' }, input,
                    min !== null ? h('button', { type: 'button', class: 'editor-btn', 'data-action': 'clear-min', text: 'Clear',
                        'aria-label': 'Clear the minimum duration', onclick: () => this.run(id, ['timing'], () => this.model.setDuration(id, null)) }) : null),
                h('p', { class: 'editor-generated', 'data-generated': 'estimate', text: `Generated: about ${secondsText(this.generatedSeconds(scene, info))}` }),
                h('p', { class: 'editor-note', text: isHidden(scene) ? 'This scene is hidden: it is not played.'
                    : `Plays for ${secondsText(info.play_seconds)}. The scene holds at least the minimum; the narration is never sped up.` })));
        }

        // How long the generated scene plays (without the editor's minimum or mute)
        generatedSeconds(scene, info) {
            const e = edit(scene);
            if (!finite(e.min_seconds) && e.narration_muted !== true && finite(info.play_seconds)) return info.play_seconds;
            const text = typeof scene.narration === 'string' ? scene.narration : '';
            const plan = plain(scene.cinematic_plan) ? scene.cinematic_plan : {};
            if (text.trim() && finite(info.estimate_seconds)) return info.estimate_seconds;
            if (finite(plan.duration) && plan.duration > 0) return plan.duration;
            return EMPTY_SECONDS;
        }

        section_narration(box, scene) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const muted = edit(scene).narration_muted === true;
            this.add(box, 
                this.textField(scene, 'narration', 'Narration', str(scene.narration, 20000), { multiline: true, write: v => this.model.setText(id, 'narration', v) }),
                h('div', { class: 'editor-field', 'data-field-box': 'narration_muted' },
                    this.fieldHead('Sound', muted),
                    h('button', { type: 'button', class: 'editor-btn', 'data-action': 'toggle-mute', 'aria-pressed': String(muted),
                        text: muted ? '🔇 Narration muted' : '🗣 Mute narration', title: muted ? 'Play the narration again' : 'Play this scene without its narration',
                        onclick: () => this.run(id, ['mute'], () => this.model.setNarrationMuted(id, !muted)) })));
        }

        section_text(box, scene) {
            const id = scene.scene_id;
            const items = labelItems(scene);
            this.add(box, 
                this.textField(scene, 'title', 'Title', str(scene.title, 300), { write: v => this.model.setText(id, 'title', v) }),
                this.textField(scene, 'subtitle', 'Subtitle', str(scene.subtitle, 600), { write: v => this.model.setText(id, 'subtitle', v) }),
                this.textField(scene, 'labels', 'Labels (one per line, at most 6)', labelsText(items), {
                    multiline: true,
                    // each line keeps the timing ("at") of the label it replaces
                    write: v => this.model.setLabels(id, v.split('\n').map(t => t.trim()).filter(Boolean).slice(0, 6)
                        .map((text, i) => (plain(items[i]) && items[i].at !== undefined ? { text, at: items[i].at } : text))),
                    original: v => (Array.isArray(v) ? labelsText(v).split('\n').join(' · ') : str(v, 4000))
                }));
        }

        // A composition choice (a Phase 13 override through the existing composition review)
        overrideSelect(scene, key, label, choices, current) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const chosen = override(scene, key);
            const entries = Object.entries(choices || {}).filter(([v]) => v !== 'auto');
            const nowText = current ? str((choices || {})[current], 80) || humanize(current) : '';
            const selectId = `editor-field-${key}`;
            const select = h('select', { id: selectId, class: 'editor-select', 'data-field': key },
                h('option', { value: 'auto', text: `Automatic${chosen === 'auto' && nowText ? ` (${nowText})` : ''}` }),
                entries.map(([value, text]) => h('option', { value, text: str(text, 80), selected: value === chosen })));
            select.value = entries.some(([v]) => v === chosen) ? chosen : 'auto';
            select.addEventListener('change', () => {
                const value = String(select.value || 'auto');
                if (value !== chosen) this.setOverride(id, key, value);
            });
            return h('div', { class: 'editor-field', 'data-field-box': key }, this.fieldHead(label, chosen !== 'auto', { forId: selectId }), select);
        }

        // Segmented buttons for a composition choice (the presenter's position and size)
        overrideButtons(scene, key, label, choices, current) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const chosen = override(scene, key);
            const active = chosen !== 'auto' ? chosen : current || '';
            return h('div', { class: 'editor-field', 'data-field-box': key },
                this.fieldHead(label, chosen !== 'auto'),
                h('div', { class: 'editor-segmented', role: 'group', 'aria-label': label },
                    Object.entries(choices).map(([value, text]) => h('button', { type: 'button', class: 'editor-btn', 'data-action': 'override', 'data-key': key,
                        'data-value': value, 'aria-pressed': String(value === active), text: str(text, 40),
                        onclick: () => { if (value !== chosen) this.setOverride(id, key, value); } })),
                    chosen !== 'auto' ? h('button', { type: 'button', class: 'editor-btn', 'data-action': 'override', 'data-key': key, 'data-value': 'auto',
                        text: 'Automatic', 'aria-pressed': 'false', onclick: () => this.setOverride(id, key, 'auto') }) : null));
        }

        section_presenter(box, scene) {
            const vocab = this.vocabulary();
            const info = presenterInfo(scene);
            const sizes = Object.fromEntries(Object.entries(vocab.presenter_size).filter(([k]) => k !== 'hidden'));
            this.add(box, 
                this.overrideButtons(scene, 'presenter_position', 'Position', vocab.presenter_position, info.position),
                this.overrideButtons(scene, 'presenter_size', 'Size', sizes, info.size),
                this.h('p', { class: 'editor-note', text: `Now: ${info.text}` }));
        }

        section_visual(box, scene) {
            const h = (...a) => this.h(...a);
            const vocab = this.vocabulary();
            const vp = plain(scene.visual_plan) ? scene.visual_plan : {};
            const slots = ['main', 'side'].filter(s => plain(vp[s]));
            const kind = visualKind(scene);
            box.appendChild(h('p', { class: 'editor-note', 'data-visual': kind, text: `Now: ${VISUAL_KIND[kind]}` }));
            // the Asset Library replaces a visual the scene's visual plan has a place for (a picture drawn into the board does not)
            if (!slots.length) {
                box.appendChild(h('p', { class: 'editor-note', text: 'This visual is part of the scene itself. To show another picture, insert a picture scene (Scene → Insert after this scene).' }));
            }
            slots.forEach(slot => {
                const plan = vp[slot];
                const shown = plan && plan.selection !== 'removed' && plan.source && plan.source !== 'NONE';
                box.appendChild(h('div', { class: 'editor-field', 'data-slot': slot },
                    h('div', { class: 'editor-field-head', text: slots.length > 1 ? (slot === 'main' ? 'Main visual' : 'Side visual') : 'Visual' }),
                    h('div', { class: 'editor-row' },
                        h('button', { type: 'button', class: 'editor-btn', 'data-action': 'choose-visual', 'data-slot': slot, text: '🖼 Choose from library',
                            onclick: () => this.chooseVisual(scene.scene_id, slot) }),
                        shown ? h('button', { type: 'button', class: 'editor-btn editor-btn-danger', 'data-action': 'remove-visual', 'data-slot': slot,
                            text: 'Remove visual', onclick: () => this.removeVisual(scene.scene_id, slot) }) : null)));
            });
            // its size and side belong to the scene's layout (a scene not laid out yet has none)
            if (plain(scene.cinematic_plan)) {
                this.add(box,
                    this.overrideSelect(scene, 'visual_size', 'Visual size', vocab.visual_size, ''),
                    this.overrideSelect(scene, 'visual_position', 'Visual side', vocab.visual_position, ''));
            }
        }

        section_camera(box, scene) {
            const plan = plain(scene.cinematic_plan) ? scene.cinematic_plan : {};
            box.appendChild(this.overrideSelect(scene, 'camera', 'Camera', this.vocabulary().camera, plain(plan.camera) ? plan.camera.movement : ''));
        }

        section_transition(box, scene, index) {
            const plan = plain(scene.cinematic_plan) ? scene.cinematic_plan : {};
            box.appendChild(this.overrideSelect(scene, 'transition', 'Transition into this scene', this.vocabulary().transition,
                plain(plan.transition) ? plan.transition.in : ''));
            if (index === 0) box.appendChild(this.h('p', { class: 'editor-note', text: 'The first scene opens the lesson: no transition comes before it.' }));
        }

        section_background(box, scene) {
            const plan = plain(scene.cinematic_plan) ? scene.cinematic_plan : {};
            const vocab = this.vocabulary();
            this.add(box, 
                this.overrideSelect(scene, 'background', 'Background', vocab.background, plain(plan.background) ? plan.background.type : ''),
                this.styleSelect(scene, 'style_background', 'Background density', vocab.background_density));
        }

        // The scene's own Phase 17 style choice ("Same as the lesson": none of its own)
        styleSelect(scene, key, label, values) {
            const h = (...a) => this.h(...a);
            const chosen = override(scene, key);
            const now = values.includes(chosen) ? chosen : 'default';
            const selectId = `editor-field-${key}`;
            const select = h('select', { id: selectId, class: 'editor-select', 'data-field': key },
                values.map(v => h('option', { value: v, selected: v === now, text: v === 'default' ? 'Same as the lesson' : humanize(v) })));
            select.value = now;
            select.addEventListener('change', () => {
                const value = String(select.value || 'default');
                if (value === now || !values.includes(value)) return;
                this.setOverride(scene.scene_id, key, value === 'default' ? 'auto' : value);
            });
            return h('div', { class: 'editor-field', 'data-field-box': key }, this.fieldHead(label, now !== 'default', { forId: selectId }), select);
        }

        section_style(box, scene) {
            const h = (...a) => this.h(...a);
            this.add(box, 
                this.styleSelect(scene, 'style_accent', 'Scene accent colour', this.vocabulary().accent),
                typeof this.adapter.openStyleSettings === 'function' ? h('button', { type: 'button', class: 'editor-btn editor-btn-link', 'data-action': 'style-settings',
                    text: 'Lesson style settings…', onclick: () => this.call('openStyleSettings') }) : null);
        }

        section_captions(box, scene) {
            const h = (...a) => this.h(...a);
            const id = scene.scene_id;
            const lesson = this.captionsVisible();
            const off = edit(scene).captions === 'off';
            this.add(box, 
                h('div', { class: 'editor-field', 'data-field-box': 'lesson_captions' },
                    h('div', { class: 'editor-field-head', text: 'Lesson captions' }),
                    h('button', { type: 'button', class: 'editor-btn', 'data-action': 'lesson-captions', 'aria-pressed': String(lesson),
                        text: lesson ? 'Captions on for the lesson' : 'Captions off for the lesson', title: lesson ? 'Hide the captions in the whole lesson' : 'Show the captions in the lesson',
                        onclick: () => this.run(null, ['captions'], () => this.model.setLessonCaptions(!lesson)) })),
                h('div', { class: 'editor-field', 'data-field-box': 'captions' },
                    this.fieldHead('This scene', off),
                    h('button', { type: 'button', class: 'editor-btn', 'data-action': 'scene-captions', 'aria-pressed': String(off), disabled: !lesson,
                        text: off ? 'Captions off in this scene' : 'Hide captions in this scene',
                        onclick: () => this.run(id, ['captions'], () => this.model.setCaptions(id, off ? null : 'off')) })),
                h('p', { class: 'editor-note', text: 'The caption size is set for the whole lesson in the lesson style settings.' }));
        }

        section_quality(box, scene, index, { findings }) {
            const h = (...a) => this.h(...a);
            const check = label => (typeof this.adapter.qualityRun === 'function' ? h('button', { type: 'button', class: 'editor-btn', 'data-action': 'quality-run',
                disabled: !!this.qualityBusy, text: this.qualityBusy ? 'Checking…' : label, onclick: () => this.runQuality() }) : null);
            if (!findings) {
                this.add(box, h('p', { class: 'editor-note', 'data-quality': 'none', text: "The lesson's quality has not been checked yet." }), check('Check quality'));
            } else if (findings.stale) {
                this.add(box, h('p', { class: 'editor-note', 'data-quality': 'stale', text: findings.targeted ? '◌ Changed since the last check'
                    : '◌ The lesson changed since its quality was checked.' }), check('Check again'));
            } else if (!findings.all.length) {
                box.appendChild(h('p', { class: 'editor-note', 'data-quality': 'good', text: '✓ Nothing to look at in this scene.' }));
            } else {
                box.appendChild(h('ul', { class: 'editor-findings' }, findings.all.slice(0, 30).map(f => {
                    const sev = QUALITY_SEVERITY[f.severity];
                    return h('li', { class: 'editor-finding', 'data-severity': f.severity },
                        h('span', { class: 'editor-finding-severity', text: `${sev.icon} ${sev.label}` }),
                        h('span', { class: 'editor-finding-message', text: f.message }),
                        this.debug && f.rule ? h('code', { class: 'editor-finding-rule', text: ` ${f.rule}` }) : null);
                })));
            }
            if (typeof this.adapter.showInReview === 'function') {
                box.appendChild(h('button', { type: 'button', class: 'editor-btn', 'data-action': 'show-in-review', text: 'Show in Visual Review',
                    onclick: () => this.call('showInReview', index) }));
            }
        }

        renderConfirm() {
            if (!this.confirmBox) return;
            const h = (...a) => this.h(...a);
            const c = this.confirmState;
            if (c && this.confirmShown === c) return; // drawn already: a redraw of the chrome keeps its focus
            this.confirmShown = c;
            this.confirmBox.textContent = '';
            setHidden(this.confirmBox, !c);
            setHidden(this.confirmBackdrop, !c);
            if (!c) return;
            // a question about removing something: its answer is a danger button, and the keyboard starts on "Cancel"
            const ok = h('button', { type: 'button', class: `editor-btn ${c.danger ? 'editor-btn-danger' : 'editor-btn-primary'}`, 'data-action': 'confirm-ok',
                text: c.ok, onclick: () => this.answer(true) });
            const cancel = h('button', { type: 'button', class: 'editor-btn', 'data-action': 'confirm-cancel', text: 'Cancel', onclick: () => this.answer(false) });
            this.add(this.confirmBox,
                h('h3', { id: 'editor-confirm-title', text: c.title }),
                h('p', { id: 'editor-confirm-text', text: c.text }),
                h('div', { class: 'editor-confirm-actions' }, cancel, ok));
            focusNode(c.danger ? cancel : ok);
        }

        // ---- UI state ------------------------------------------------------------------------------------------------

        select(id, { seek = false } = {}) {
            const index = this.model.indexOf(id);
            if (index < 0) return false;
            if (this.typing) this.endTyping();
            this.selectedId = id;
            if (seek) {
                this.time = this.startOf(id);
                this.call('seek', index);
            }
            this.render();
            return true;
        }

        step(delta) {
            const scenes = this.scenes();
            if (!scenes.length) return;
            const index = this.model.indexOf(this.selectedId);
            const next = Math.max(0, Math.min(scenes.length - 1, (index < 0 ? 0 : index) + delta));
            this.select(scenes[next].scene_id, { seek: true });
        }

        togglePlay() {
            if (this.playing) { this.playing = false; this.call('pause'); }
            else { this.playing = true; this.call('play'); }
            this.updatePlayButton();
        }

        // The page reports where playback is (renderSlide / progress): the playhead and the selection follow
        sync({ scene, elapsed = 0, playing } = {}) {
            if (!this.isOpen) return;
            if (typeof playing === 'boolean') this.playing = playing;
            const segs = this.segments();
            const seg = Number.isInteger(scene) ? segs[scene] : null;
            if (seg) {
                this.time = seg.start + Math.max(0, Math.min(seg.seconds, finite(elapsed) ? elapsed : 0));
                if (this.playing && this.selectedId !== seg.id && !this.typing && !this.drag && !this.confirmState && !this.pointerDown) {
                    this.selectedId = seg.id;
                    this.render();
                    return;
                }
            }
            if (this.drag) return;
            this.updatePlayhead();
            this.updatePlayButton();
        }

        // the play / pause button changed in place (it keeps the keyboard focus)
        updatePlayButton() {
            const play = this.timeline && this.timeline.querySelector('[data-action="play"]');
            if (!play) return;
            play.setAttribute('aria-pressed', String(this.playing));
            play.setAttribute('aria-label', this.playing ? 'Pause' : 'Play');
            play.textContent = this.playing ? '❚❚ Pause' : '▶ Play';
        }

        setInspector(open) {
            if (!open && this.typing) this.endTyping();
            this.inspectorOpen = !!open;
            this.render();
        }

        setPreview(on) {
            if (on && this.typing) this.endTyping();
            this.previewing = !!on;
            this.render();
            // the keyboard follows: to "Back to editing" while previewing (the rest is hidden), then back to "Preview"
            focusNode(this.previewing ? this.previewExit : this.topEls && this.topEls.preview);
        }

        // The shortcuts: the keyboard moves into them ("Close") and back to where it was when they close
        toggleHelp(force) {
            const open = typeof force === 'boolean' ? force : !this.helpOpen;
            if (open === this.helpOpen) return;
            this.helpOpen = open;
            setHidden(this.help, !open);
            this.renderTop();
            if (open) {
                const active = this.doc.activeElement;
                this.helpReturn = active && this.help && typeof this.help.contains === 'function' && this.help.contains(active) ? null : active;
                focusNode(this.helpClose);
            } else {
                const back = this.helpReturn && this.helpReturn.isConnected !== false ? this.helpReturn : this.topEls && this.topEls.help;
                this.helpReturn = null;
                focusNode(back);
            }
        }

        toggleSection(key) {
            this.sectionChoice.set(key, !this.sectionOpen(key));
            this.render();
        }

        openSection(key) {
            this.sectionChoice.set(key, true);
            this.inspectorOpen = true;
            this.render();
        }

        // A line in the top bar (announced); a problem always starts with its icon (never the colour alone)
        note(text, kind = 'info') {
            const words = str(text, 300);
            this.message = kind === 'error' && words && !/^[✕⚠]/u.test(words) ? `✕ ${words}` : words;
            this.messageKind = kind;
            this.renderTop();
        }

        // An in-editor confirmation (never window.confirm): resolves true / false
        ask(title, text, ok, { danger = false } = {}) {
            if (this.confirmState) this.answer(false);
            const focus = this.focusKey();
            return new Promise(resolve => {
                this.confirmState = { title, text, ok, resolve, focus, danger };
                this.renderConfirm();
            });
        }

        answer(yes) {
            const c = this.confirmState;
            if (!c) return;
            this.confirmState = null;
            this.renderConfirm();
            c.resolve(!!yes);
            // keyboard focus goes back to where the question came from (once the answer's change is drawn)
            this.later(() => { if (!this.confirmState) this.restoreFocus(c.focus); });
        }

        // ---- changes (through the model) -----------------------------------------------------------------------------

        // Runs one model command: a refused one shows its reason; a real change tells the page and the autosave.
        // after(result): UI state that follows the change (the selection), set before the chrome is drawn again;
        // notify: false when the page hears about it later (a composition choice, once the review route answered);
        // later: the chrome is drawn again on the next turn (a change that fires as a field loses focus)
        run(id, kinds, fn, { after = null, notify = true, later = false } = {}) {
            if (this.typing) this.endTyping();
            let result;
            let thrown = null;
            this.acting += 1;
            try {
                result = fn();
            } catch (e) {
                thrown = e || new Error('');
                result = { ok: false };
            } finally {
                this.acting -= 1;
            }
            this.cache = null;
            if (thrown || !result) {
                this.problem('That change could not be made. Nothing was changed and your other edits are safe: please try again.', thrown);
                return null;
            }
            if (result.ok === false) {
                // the model's own reason, in plain words ("At least one scene must stay visible.")
                this.note(`That change could not be made: ${str(result.error, 200) || 'please try again.'}`, 'error');
                return null;
            }
            if (result.changed === false) {
                if (later) this.renderLater();
                else this.render();
                return result;
            }
            if (typeof after === 'function') after(result);
            if (notify) this.changed(result.scene_id !== undefined && result.scene_id !== null ? result.scene_id : id, kinds, result, { later });
            return result;
        }

        // After every model change: the page re-plans / re-renders, the autosave is told, the chrome is drawn again
        changed(id, kinds, result = null, { later = false } = {}) {
            this.cache = null;
            this.message = '';
            this.call('changed', { scene_id: id || null, kinds, label: result && result.label ? str(result.label, 80) : '',
                structure: !!(result && result.structure), retime: !!(result && result.retime) });
            if (this.autosave && typeof this.autosave.schedule === 'function') this.autosave.schedule();
            if (!this.isOpen) return;
            if (later) this.renderLater();
            else this.render();
        }

        moveTo(id, to) {
            const count = this.scenes().length;
            const from = this.model.indexOf(id);
            const target = Math.max(0, Math.min(count - 1, to));
            if (from < 0 || target === from) return false;
            const r = this.run(id, ['order'], () => this.model.move(id, target), { after: () => {
                this.cache = null;
                this.selectedId = id;
                this.time = this.startOf(id);
            } });
            return !!r;
        }

        // a new scene (duplicate, insert, split) is selected
        selectNew(result) {
            if (result && typeof result.new_scene_id === 'string' && this.model.indexOf(result.new_scene_id) >= 0) {
                this.cache = null;
                this.selectedId = result.new_scene_id;
                this.time = this.startOf(result.new_scene_id);
            }
        }

        duplicate(id) {
            return this.run(id, ['insert'], () => this.model.duplicate(id), { after: r => this.selectNew(r) });
        }

        insertAfter(id, kind, data) {
            const index = this.model.indexOf(id);
            if (index < 0) return null;
            return this.run(id, ['insert'], () => this.model.insert(index + 1, kind, data), { after: r => this.selectNew(r) });
        }

        async insertPicture(id) {
            let asset = null;
            try {
                asset = await this.adapter.pickAsset({ kinds: ['image'], title: 'Choose a picture for the new scene' });
            } catch (e) {
                this.problem('The Library could not be opened. Your edits are safe: please try again.', e);
                return;
            }
            if (!plain(asset) || !this.isOpen || this.model.indexOf(id) < 0) return;
            const assetId = typeof asset.asset_id === 'string' ? asset.asset_id : str(asset.id, 64);
            const title = str(asset.title || asset.name, 300).trim();
            this.insertAfter(id, 'asset', title ? { asset_id: assetId, title } : { asset_id: assetId });
        }

        split(id, pauseIndex) {
            return this.run(id, ['split'], () => this.model.splitAtPause(id, pauseIndex), { after: r => this.selectNew(r) });
        }

        async deleteScene(id) {
            const index = this.model.indexOf(id);
            if (index < 0) return false;
            const scene = this.model.scene(id);
            const yes = await this.ask('Delete this scene?', `Scene ${index + 1} “${sceneTitle(scene)}” will be removed from the lesson. You can undo this (Ctrl+Z).`, 'Delete scene', { danger: true });
            if (!yes || !this.isOpen || this.model.indexOf(id) < 0) return false;
            const at = this.model.indexOf(id);
            const r = this.run(id, ['delete'], () => this.model.remove(id), { after: () => {
                // the scene that took its place (or the one before, at the end) is selected
                const scenes = this.scenes();
                const next = scenes[Math.min(at, scenes.length - 1)];
                this.cache = null;
                this.selectedId = next ? next.scene_id : null;
                this.time = this.startOf(this.selectedId);
            } });
            return !!r;
        }

        async setOverride(id, key, value) {
            const r = this.run(id, ['composition'], () => this.model.setOverride(id, key, value), { notify: false });
            if (!r || r.changed === false) return;
            await this.applyEffects([r], id);
        }

        // "Show presenter" on a scene that hides it: a visible side (the plan's own, else the right) shows it; a size the user
        // set to hidden goes back to automatic. Each is a composition choice (one undo step each).
        async showPresenter(id) {
            const scene = this.model.scene(id);
            if (!scene) return;
            const p = plain(scene.cinematic_plan) && plain(scene.cinematic_plan.presenter) ? scene.cinematic_plan.presenter : {};
            const side = ['left', 'right'].includes(p.side) ? p.side : 'right';
            if (override(scene, 'presenter_size') === 'hidden') await this.setOverride(id, 'presenter_size', 'auto');
            const now = this.model.scene(id);
            if (now && (override(now, 'presenter_position') === 'hidden' || !p.shown)) await this.setOverride(id, 'presenter_position', side);
        }

        // Composition changes (setOverride, or the effects of an undo / redo) go to the page, one per scene, in order
        async applyEffects(effects, id) {
            for (const effect of effects) {
                if (!plain(effect) || effect.kind !== 'composition') continue;
                if (typeof this.adapter.applyComposition === 'function') {
                    try {
                        await this.adapter.applyComposition(effect);
                    } catch (e) {
                        this.problem('✕ The layout change could not be made. Your other edits are safe: please try again in a moment.', e);
                        return false;
                    }
                }
                if (!this.isOpen) return false;
                this.changed(effect.scene_id || id || null, ['composition'], { label: effect.label });
            }
            return true;
        }

        async chooseVisual(id, slot) {
            const index = this.model.indexOf(id);
            if (index < 0) return;
            let done;
            try {
                done = await this.call('chooseVisual', index, slot);
            } catch (e) {
                this.problem('The visual could not be changed. Your edits are safe: please try again.', e);
                return;
            }
            if (done === false || !this.isOpen) return;
            this.changed(id, ['visual']);
        }

        async removeVisual(id, slot) {
            const index = this.model.indexOf(id);
            if (index < 0) return;
            const yes = await this.ask('Remove this visual?', `Scene ${index + 1} will play without this visual. You can choose another one later.`, 'Remove visual', { danger: true });
            if (!yes || !this.isOpen || this.model.indexOf(id) < 0) return;
            let done;
            try {
                done = await this.call('removeVisual', this.model.indexOf(id), slot);
            } catch (e) {
                this.problem('The visual could not be removed. Your edits are safe: please try again.', e);
                return;
            }
            if (done === false || !this.isOpen) return;
            this.changed(id, ['visual']);
        }

        revertField(id, field) {
            this.run(id, [field === 'narration' ? 'narration' : 'text'], () => this.model.revert(id, field));
        }

        // typing: one model transaction per focus session (begin on focus, commit on blur / Enter)
        beginTyping(id, field, label) {
            if (this.typing && (this.typing.id !== id || this.typing.field !== field)) this.endTyping();
            if (this.typing) return;
            this.typing = { id, field, label, error: false };
            this.model.begin(`Edit ${label.toLowerCase().replace(/\s*\(.*\)$/, '')}`);
        }

        typeValue(id, field, label, write) {
            if (!this.typing || this.typing.id !== id || this.typing.field !== field) this.beginTyping(id, field, label);
            let r;
            let thrown = null;
            this.acting += 1;
            try {
                r = write();
            } catch (e) {
                thrown = e || new Error('');
                r = { ok: false };
            } finally {
                this.acting -= 1;
            }
            this.cache = null;
            if (!r || r.ok === false) {
                if (!this.typing.error) {
                    if (thrown || !r) this.problem('That text could not be used. Your other edits are safe: please try again.', thrown);
                    else this.note(`That text could not be used: ${str(r.error, 160) || 'please try again.'}`, 'error');
                }
                this.typing.error = true;
                return;
            }
            this.renderList();
            this.renderTimeline();
        }

        endTyping() {
            const t = this.typing;
            if (!t) return;
            this.typing = null;
            let r = null;
            this.acting += 1;
            try {
                r = this.model.commit();
            } finally {
                this.acting -= 1;
            }
            this.cache = null;
            if (r && r.ok !== false && r.changed) this.changed(t.id, [t.field === 'narration' ? 'narration' : 'text'], r, { later: true });
            else if (this.isOpen) this.renderLater();
        }

        undo() {
            if (this.typing) this.endTyping();
            if (!this.model.canUndo()) return;
            this.history(() => this.model.undo());
        }

        redo() {
            if (this.typing) this.endTyping();
            if (!this.model.canRedo()) return;
            this.history(() => this.model.redo());
        }

        // An undone / redone step: its composition choices go back through the composition review
        history(fn) {
            const r = this.run(null, ['history'], fn, { after: res => {
                // a structural step shows the scene it moved / brought back
                if (res.structure && res.scene_id && this.model.indexOf(res.scene_id) >= 0) {
                    this.cache = null;
                    this.selectedId = res.scene_id;
                    this.time = this.startOf(res.scene_id);
                }
            } });
            if (!r) return null;
            if (Array.isArray(r.problems) && r.problems.length) this.note(`${r.problems.length} part${r.problems.length === 1 ? '' : 's'} of that step could not be applied.`, 'error');
            const effects = Array.isArray(r.effects) ? r.effects : [];
            return effects.length ? this.applyEffects(effects, r.scene_id) : null;
        }

        async saveVersion() {
            if (this.versionBusy || typeof this.adapter.saveVersion !== 'function') return;
            if (this.typing) this.endTyping();
            this.versionBusy = true;
            this.message = '';
            this.renderTop();
            try {
                await this.adapter.saveVersion();
                this.versionBusy = false;
                this.note('✓ Copy saved in Your lessons. You are now editing the copy; the original keeps its videos.');
            } catch (e) {
                this.versionBusy = false;
                this.problem('✕ The copy could not be saved. Your edits are safe in the editor: please try “Save as a copy” again in a moment.', e);
            }
        }

        retrySave() {
            const a = this.autosave;
            if (!a) return;
            const fn = typeof a.retry === 'function' ? a.retry : a.flush;
            if (typeof fn === 'function') {
                try {
                    const p = fn.call(a);
                    if (p && typeof p.then === 'function') p.then(() => this.isOpen && this.renderTop(), () => this.isOpen && this.renderTop());
                } catch (e) { /* the autosave reports its own state */ }
            }
            this.renderTop();
        }

        // ---- dragging (pointer events) -----------------------------------------------------------------------------

        attachSceneDrag(node, id, where) {
            node.addEventListener('pointerdown', e => {
                if (!e || (e.button !== undefined && e.button !== 0) || this.drag) return;
                this.drag = { type: 'scene', where, id, node, startX: e.clientX || 0, startY: e.clientY || 0, active: false, target: null };
                if (typeof node.setPointerCapture === 'function' && e.pointerId !== undefined) {
                    try { node.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
                }
            });
            node.addEventListener('pointermove', e => this.dragMove(e, node));
            node.addEventListener('pointerup', () => this.dragEnd(node, false));
            node.addEventListener('pointercancel', () => this.dragEnd(node, true));
        }

        dragMove(e, node) {
            const d = this.drag;
            if (!d || d.type !== 'scene' || d.node !== node || !e) return;
            if (!d.active) {
                if (Math.abs((e.clientX || 0) - d.startX) < DRAG_PX && Math.abs((e.clientY || 0) - d.startY) < DRAG_PX) return;
                d.active = true;
                if (this.typing) this.endTyping();
                node.classList.add('is-dragging');
                if (this.autosave && typeof this.autosave.hold === 'function') this.autosave.hold();
            }
            if (e.preventDefault) e.preventDefault();
            d.target = d.where === 'timeline' ? this.timelineTarget(e, d.id) : this.listTarget(e, d.id);
            this.showDrop(d);
        }

        // A drag is ONE command (move) when it ends; nothing changes while it moves
        dragEnd(node, cancelled) {
            const d = this.drag;
            if (!d || d.type !== 'scene' || d.node !== node) return;
            this.drag = null;
            node.classList.remove('is-dragging');
            this.hideDrop();
            if (!d.active) return; // a plain press: the click selects
            this.suppressClick = true;
            setTimeout(() => { this.suppressClick = false; }, 0); // only the click that ends this drag is ignored
            const from = this.model.indexOf(d.id);
            const moved = !cancelled && Number.isInteger(d.target) && d.target !== from ? this.moveTo(d.id, d.target) : false;
            if (this.autosave && typeof this.autosave.release === 'function') this.autosave.release();
            if (!moved) this.render();
        }

        cancelDrag() {
            const d = this.drag;
            this.drag = null;
            this.hideDrop();
            if (d && d.type === 'scene' && d.active && this.autosave && typeof this.autosave.release === 'function') this.autosave.release();
        }

        // the final index the dragged scene would get, from the pointer's place among the other scenes
        timelineTarget(e, id) {
            const segs = this.segments();
            const box = this.sceneLane && typeof this.sceneLane.getBoundingClientRect === 'function' ? this.sceneLane.getBoundingClientRect() : null;
            const x = (e.clientX || 0) - ((box && box.left) || 0);
            return segs.filter(s => s.id !== id && s.x + s.w / 2 < x).length;
        }

        listTarget(e, id) {
            let n = 0;
            list(this.sceneList ? this.sceneList.querySelectorAll('.editor-scene') : []).forEach(item => {
                if (item.getAttribute('data-scene-id') === id) return;
                const r = item.getBoundingClientRect();
                if (r.top + r.height / 2 < (e.clientY || 0)) n += 1;
            });
            return n;
        }

        showDrop(d) {
            this.hideDrop();
            if (!Number.isInteger(d.target)) return;
            if (d.where === 'timeline') {
                const others = this.segments().filter(s => s.id !== d.id);
                const at = others[d.target];
                const last = others[others.length - 1];
                const x = at ? at.x : last ? last.x + last.w : 0;
                if (this.tlDrop) {
                    this.tlDrop.style.left = `${Math.round(x)}px`;
                    setHidden(this.tlDrop, false);
                }
            } else {
                const items = list(this.sceneList ? this.sceneList.querySelectorAll('.editor-scene') : []).filter(i => i.getAttribute('data-scene-id') !== d.id);
                const at = items[d.target];
                if (at) at.setAttribute('data-drop', 'before');
                else if (items.length) items[items.length - 1].setAttribute('data-drop', 'after');
            }
        }

        hideDrop() {
            if (this.tlDrop) setHidden(this.tlDrop, true);
            list(this.sceneList ? this.sceneList.querySelectorAll('[data-drop]') : []).forEach(i => i.removeAttribute('data-drop'));
        }

        // The playhead: dragged as UI state only (never a model change); on release it snaps to a scene boundary and seeks
        attachPlayheadDrag(node) {
            if (!node) return;
            const timeFrom = e => {
                const box = this.ruler && typeof this.ruler.getBoundingClientRect === 'function' ? this.ruler.getBoundingClientRect() : null;
                return this.timeAt(Math.max(0, (e.clientX || 0) - ((box && box.left) || 0)), this.segments());
            };
            node.addEventListener('pointerdown', e => {
                if (!e || (e.button !== undefined && e.button !== 0) || this.drag) return;
                if (e.stopPropagation) e.stopPropagation();
                this.drag = { type: 'playhead', node };
                if (typeof node.setPointerCapture === 'function' && e.pointerId !== undefined) {
                    try { node.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
                }
                this.time = timeFrom(e);
                this.updatePlayhead();
            });
            node.addEventListener('pointermove', e => {
                if (!this.drag || this.drag.type !== 'playhead' || this.drag.node !== node || !e) return;
                this.time = timeFrom(e);
                this.updatePlayhead();
            });
            const end = (e, cancelled) => {
                if (!this.drag || this.drag.type !== 'playhead' || this.drag.node !== node) return;
                this.drag = null;
                if (e && !cancelled && e.clientX !== undefined) this.time = timeFrom(e);
                const seg = this.snap(this.time, this.segments());
                if (!seg) { this.updatePlayhead(); return; }
                this.time = seg.start;
                this.selectedId = seg.id;
                this.call('seek', seg.index);
                this.render();
            };
            node.addEventListener('pointerup', e => end(e, false));
            node.addEventListener('pointercancel', e => end(e, true));
        }

        // ---- keyboard ----------------------------------------------------------------------------------------------

        // The controls Tab can reach in `scope` (enabled, not hidden, drawn), in page order
        focusables(scope) {
            if (!scope || typeof scope.querySelectorAll !== 'function') return [];
            return list(scope.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')).filter(n => {
                if (n.disabled === true || n.getAttribute('disabled') !== null || n.getAttribute('tabindex') === '-1') return false;
                for (let p = n; p && p !== scope; p = p.parentNode || p.parent) {
                    if (typeof p.getAttribute === 'function' && p.getAttribute('hidden') !== null) return false;
                }
                return typeof n.getClientRects !== 'function' || n.getClientRects().length > 0;
            });
        }

        // Tab from the last control goes to the first (Shift+Tab from the first to the last); from outside, into the dialog
        trapTab(e) {
            const scope = this.confirmState ? this.confirmBox : this.root;
            const items = this.focusables(scope);
            if (!items.length) return;
            const active = this.doc.activeElement;
            const at = items.indexOf(active);
            let next = null;
            if (at < 0) {
                const inside = active && typeof scope.contains === 'function' && scope.contains(active);
                if (!inside) next = e.shiftKey ? items[items.length - 1] : items[0];
            } else if (e.shiftKey && at === 0) next = items[items.length - 1];
            else if (!e.shiftKey && at === items.length - 1) next = items[0];
            if (!next) return;
            if (typeof e.preventDefault === 'function') e.preventDefault();
            focusNode(next);
        }

        handleKey(e) {
            if (!this.isOpen || !e) return;
            if (this.doc.querySelector && this.doc.querySelector(OTHER_OVERLAYS)) return; // another panel has the keyboard
            if (this.root && typeof this.root.closest === 'function' && this.root.closest('[inert]')) return; // a dialog above this one (e.g. sign in) made it inert: the keys are not ours
            const key = e.key;
            const typing = isTyping(e.target);
            const stop = () => { if (typeof e.stopPropagation === 'function') e.stopPropagation(); };
            const prevent = () => { if (typeof e.preventDefault === 'function') e.preventDefault(); };
            if (key === 'Escape' || key === 'Esc') {
                stop();
                if (typing && !this.confirmState) {
                    // leave the field first (its typing session is committed); the next Escape closes panels
                    if (e.target && typeof e.target.blur === 'function') e.target.blur();
                    this.endTyping();
                    return;
                }
                prevent();
                if (this.confirmState) this.answer(false);
                else if (this.helpOpen) this.toggleHelp(false);
                else if (this.previewing) this.setPreview(false);
                else if (this.inspectorOpen) this.setInspector(false);
                else this.close();
                return;
            }
            // a modal dialog: Tab stays inside the workspace (inside the question while one is asked)
            if (key === 'Tab') {
                this.trapTab(e);
                return;
            }
            if (typing) return; // the field gets the key; the chrome stops it before the page's slide shortcuts
            if (this.confirmState) {
                if (key !== 'Enter') stop(); // the confirmation's own buttons
                return;
            }
            const mod = !!(e.ctrlKey || e.metaKey);
            const lower = typeof key === 'string' ? key.toLowerCase() : '';
            if (mod && !e.altKey && lower === 'z') {
                stop(); prevent();
                if (e.shiftKey) this.redo();
                else this.undo();
                return;
            }
            if (mod && !e.altKey && lower === 'y') {
                stop(); prevent();
                this.redo();
                return;
            }
            if (mod) return; // other browser shortcuts (copy, find, reload...) stay the browser's
            if (e.altKey && (key === 'ArrowLeft' || key === 'ArrowRight')) {
                stop(); prevent();
                const index = this.model.indexOf(this.selectedId);
                if (index >= 0) this.moveTo(this.selectedId, index + (key === 'ArrowLeft' ? -1 : 1));
                return;
            }
            if (key === 'ArrowLeft' || key === 'ArrowRight') {
                stop(); prevent();
                this.step(key === 'ArrowLeft' ? -1 : 1);
                return;
            }
            if (key === ' ' || key === 'Spacebar') {
                stop();
                if (isButton(e.target)) return; // Space presses the focused button
                prevent();
                this.togglePlay();
                return;
            }
            if (key === 'Delete') {
                stop(); prevent();
                if (this.selectedId) this.deleteScene(this.selectedId);
                return;
            }
            if (key === '?') {
                stop(); prevent();
                this.toggleHelp();
                return;
            }
            // the page's other slide shortcuts (B / T background, H player bar) stay out while editing
            if (['b', 't', 'h'].includes(lower) && !e.altKey) stop();
        }
    }

    return {
        EditorWorkspace, SECTIONS, SHORTCUTS, SAVE_TEXT, QUALITY_SEVERITY, DEFAULT_VOCABULARY, SMALL_SCREEN_NOTE, STAGE_NOTE,
        approvalMark, clock, countFindings, hasVisualSlot, inspectorLayout, isTyping, lessonHeadline, presenterInfo, qualityHeadline, qualityReport,
        sceneFindings, sceneFocus, secondsText, severityText, typeText, visualKind
    };
});

/*
 * The page's adapter (index.html wires it; every function optional):
 *   lessonTitle() -> string                          the lesson's title for the top bar
 *   currentScene() -> index                          the scene the stage shows when the editor opens (selected first)
 *   seek(sceneIndex)                                 render that scene on the stage (the preview)
 *   play() / pause() / isPlaying() -> bool           the page's playback
 *   changed({scene_id, kinds, label, structure, retime})
 *                                                    after every model change: re-plan (structure) / Phase 16 retime (retime) /
 *                                                    re-render. kinds: order, insert, delete, split, hidden, timing, narration,
 *                                                    mute, text, composition, visual, captions, history
 *   applyComposition({kind: 'composition', scene_id, overrides}) -> Promise
 *                                                    send the override through cinematicApi.review({action: 'change', overrides})
 *                                                    and update the scene's review / plan in the slides (undo / redo send the
 *                                                    previous value, 'auto' = back to the composer); changed(...kinds
 *                                                    ['composition']) follows once it resolves
 *   chooseVisual(sceneIndex, slot) -> Promise<bool>  the Asset Library + the existing visual review route (false: cancelled)
 *   removeVisual(sceneIndex, slot) -> Promise<bool>  the existing visual review route (remove); the editor asks first
 *   pickAsset({kinds, title}) -> Promise<asset|null> the Asset Library in pick mode, for "insert a picture" (asset.id: 32 hex)
 *   saveVersion() -> Promise                         "Save as a copy": the existing /save-history (a new lesson, on purpose; the
 *                                                    page then edits the copy)
 *   qualityFor(sceneId) -> {stale, issues: [finding]} | null   that scene's findings from the last check, by scene id (so a
 *                                                    reorder keeps them on the scene); stale only when THAT scene changed since
 *   qualityLesson() -> {stale, issues: [lesson-level findings], counts: {notice, warning, error, blocking} | {attention, worst}}
 *                                                    the top bar's summary (without counts: the findings are counted)
 *   qualityRun() -> Promise                          "Check again" (the page checks the lesson; the editor draws the result)
 *   quality() -> report | null, qualityStale() -> bool   the older whole-report path, used when qualityFor is absent
 *   showInReview(sceneIndex | null)                  open Visual Review (on that scene; null: the lesson's quality)
 *   openStyleSettings()                              the Phase 17 lesson style settings
 *   vocabulary() -> {presenter_position, presenter_size, visual_size, visual_position, camera, transition, background:
 *                    {value: label}, accent, background_density: [values]}   (defaults: DEFAULT_VOCABULARY)
 *   closed()                                         after close(): the editor's chrome is gone, the player bar is back
 *   returnTo() -> 'studio' | 'lesson'                where close() goes back to ("Back to the Studio" / "Back to lesson";
 *                                                    default 'lesson')
 *   debug: bool                                      show internal details (scene ids, fingerprints, rule names, error text)
 * The page calls workspace.render() when its quality findings change outside the editor (a check in Visual Review).
 * The page calls workspace.sync({scene, elapsed, playing}) while it plays so the playhead and the selection follow, and
 * passes the same Autosave it saves with (its onState is chained while the editor is open for the save state).
 */
