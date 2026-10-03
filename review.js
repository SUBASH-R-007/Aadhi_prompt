/*
 * Visual Review (Phase 6): see the visual of every scene before exporting, and keep it, change it,
 * remove it or generate a new AI version.
 *
 * The visual router (visuals.py) still makes the first choice and the AI cache (ai_cache.py) still
 * decides whether an AI visual already exists. A decision made here is recorded in the saved lesson
 * (POST /api/visuals/review -> scene.visual_review.<slot>) and the router applies it before anything
 * else, so the preview, a reload and the video export all show the reviewed visual:
 *   pending   the router's choice, not reviewed yet (not an error)
 *   approved  kept as it is                     changed  replaced by the user
 *   removed   no visual for this scene
 * Opening the review never generates anything.
 *
 * Phase 21: everything is said in plain words (where a visual came from, what went wrong and that the lesson is safe);
 * the provider, model, asset ids and raw error text appear only in the debug view (?visualDebug, the panel's `debug`).
 *
 * Loaded as a classic <script> (window.AadhiReview, after visuals.js) and as a CommonJS module by
 * the Node unit tests in tests/review.test.js.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory(require('./visuals.js'));
    } else {
        root.AadhiReview = factory(root.AadhiVisuals);
    }
})(typeof self !== 'undefined' ? self : this, function (Visuals) {
    'use strict';

    const SLOTS = ['main', 'side'];
    // A review state is always an icon and words (Phase 21: "Removed" for a visual, presenter or composition taken out)
    const STATUS = {
        pending: { icon: '●', label: 'Needs review' },
        approved: { icon: '✓', label: 'Approved' },
        changed: { icon: '✎', label: 'Changed' },
        removed: { icon: '—', label: 'Removed' }
    };
    const AI = ['AI_IMAGE', 'AI_VIDEO'];
    // The list's filters, in the words the panel uses for what a scene shows (the keys are what the page and tests use)
    const FILTERS = {
        all: { label: 'All', test: () => true },
        pending: { label: 'Needs review', test: item => item.status === 'pending' },
        approved: { label: 'Approved', test: item => item.status === 'approved' },
        changed: { label: 'Changed', test: item => item.status === 'changed' },
        none: { label: 'No visual', test: item => item.status === 'removed' || item.plan.source === 'NONE' },
        ai: { label: 'Made with AI', test: item => AI.includes(item.plan.source) },
        library: { label: 'From the library', test: item => ['ASSET', 'SYSTEM_ASSET', 'UPLOADED_ASSET'].includes(item.plan.source) },
        builtin: { label: 'Built-in', test: item => ['PROCEDURAL', 'EXTERNAL_MEDIA'].includes(item.plan.source) },
        manim: { label: 'Animations', test: item => item.plan.source === 'MANIM' },
        composition: { label: 'Layout', test: item => item.slot === 'composition' }, // a scene's composition, called its layout
        // scenes the lesson's quality check (Phase 18) found something in (notice or more serious); set by the session
        quality: { label: 'Quality', test: item => !!(item.quality && item.quality.count > 0) }
    };
    const RENDERERS = {
        chart: 'Generated chart', graph: 'Interactive graph', '3d_model': '3D model', terminal: 'Terminal animation',
        animation: 'Animated background', skill_tree: 'Concept map', quiz: 'Side quiz', p5: 'Interactive simulation',
        svg: 'Drawn diagram', gif: 'Animated picture', mathjax: 'Typeset equation', manim: 'Animation'
    };
    // The parts of a scene's layout drawing (cinematic.py layer ids), in words
    const LAYERS = { visual: 'Visual', board: 'Text', title: 'Title', labels: 'Labels', presenter: 'Presenter', subtitles: 'Captions', background: 'Background' };
    const POSITIONS = { left: 'Left', right: 'Right', center: 'Center', pip: 'Small corner' };

    // ---- what a scene shows, in words -------------------------------------------------------

    function sourceText(plan) {
        if (!plan || plan.selection === 'removed') return 'No visual';
        switch (plan.source) {
            case 'ASSET': return 'Library';
            case 'SYSTEM_ASSET': return 'Shared Aadhi asset';
            case 'UPLOADED_ASSET': return 'Existing media';
            case 'PROCEDURAL': return RENDERERS[plan.renderer] || 'Built-in visual';
            case 'EXTERNAL_MEDIA': return 'Animated picture';
            case 'MANIM': return 'Animation';
            case 'AI_IMAGE': return 'AI image';
            case 'AI_VIDEO': return 'AI video';
            default: return 'No visual';
        }
    }

    // Why this visual: the router's own reason in plain words (never the raw routing trace)
    function reasonText(plan) {
        if (!plan) return '';
        if (plan.error) return plan.reason || 'This visual is unavailable.';
        switch (plan.selection) {
            case 'removed': return 'You removed the visual for this scene.';
            case 'reviewed': return 'You chose this visual.';
            case 'approved': return 'You approved this visual.';
            case 'explicit': return 'The lesson names this asset.';
            case 'existing': return 'The scene already had this media.';
            case 'matched': return 'Matched an existing visual in your Library.';
            case 'kept': return 'Matched earlier in your Library and kept, so the preview and the export show the same visual.';
            case 'planned': return 'No suitable existing visual was found, so it has to be generated.';
            case 'cached': return 'Reused a visual generated earlier for the same request.';
            case 'builtin':
                if (plan.source === 'MANIM') return "Drawn as an animation from the scene's own animation steps.";
                if (plan.renderer === 'mathjax') return 'The equation is typeset from the scene text.';
                if (plan.renderer === 'gif') return "An animated picture found for the scene's topic.";
                return `Drawn from the scene's own data (${(RENDERERS[plan.renderer] || 'built-in visual').toLowerCase()}).`;
            case 'none':
                return plan.would_require ? 'AI visuals are turned off and nothing in your Library matches.' : 'No visual is needed for this scene.';
            default: return plan.reason || '';
        }
    }

    function statusText(status) {
        const info = STATUS[status] || STATUS.pending;
        return `${info.icon} ${info.label}`;
    }

    // How the review panel can show this visual without making anything new
    function previewKind(plan, scene) {
        if (!plan) return 'none';
        if (plan.error) return 'unavailable';
        if (plan.selection === 'removed') return 'removed';
        if (plan.url) return plan.media === 'STATIC_IMAGE' ? 'image' : 'video';
        if (plan.requires_generation) return 'not-generated';
        if (plan.source === 'PROCEDURAL' && plan.renderer === 'chart' && scene && scene.side_panel && scene.side_panel.data) return 'chart';
        if (plan.source === 'NONE') return 'none';
        return 'described';
    }

    // The AI request that would make this slot's visual, or null (same prompts the router uses)
    function generationTarget(scene, slot) {
        if (!scene) return null;
        if (slot === 'main') return scene.type === 'ai_video' && scene.prompt ? { media: 'video', prompt: scene.prompt } : null;
        const panel = scene.side_panel;
        if (panel && panel.type === 'image') return { media: 'image', prompt: panel.prompt || scene.title || '' };
        if (!panel && scene.visual && scene.visual.type !== 'none') {
            const prompt = scene.visual.description || scene.visual.concept || scene.title;
            return prompt && !['video', 'animation', 'chart', 'graph', 'equation', 'formula'].includes(scene.visual.type) ? { media: 'image', prompt } : null;
        }
        return null;
    }

    function purposeText(scene) {
        if (!scene) return '';
        if (scene.subtitle) return scene.subtitle;
        const narration = String(scene.narration || '').replace(/\[[A-Z:0-9]+\]/g, '').replace(/\s+/g, ' ').trim();
        const first = narration.split(/(?<=[.!?])\s/)[0] || '';
        return first.length > 140 ? first.slice(0, 137) + '…' : first;
    }

    // ---- the list of visuals to review --------------------------------------------------------

    // The scene's presenter (Phase 12, presenters.py): reviewed like a visual, decided through its own endpoint
    function presenterItem(scene, sceneIndex) {
        const plan = scene && scene.presenter_plan;
        if (!plan || !plan.presenter_id || (plan.type === 'mascot' && plan.source === 'lesson')) return null;
        const review = (scene.visual_review && scene.visual_review.presenter) || null;
        return { sceneIndex, slot: 'presenter', scene, plan, review, several: true, presenter: true,
            status: review && STATUS[review.status] ? review.status : 'pending', stale: false };
    }

    // The scene's cinematic composition (Phase 13, cinematic.py): reviewed like a visual, decided through its own endpoint
    function compositionItem(scene, sceneIndex) {
        const plan = scene && scene.cinematic_plan;
        if (!plan || !plan.template) return null;
        const review = (scene.visual_review && scene.visual_review.composition) || null;
        return { sceneIndex, slot: 'composition', scene, plan, review, several: true, composition: true,
            status: STATUS[plan.review_status] ? plan.review_status : 'pending', stale: !!plan.review_stale };
    }

    function reviewItems(slides, withPresenter = false, withComposition = false) {
        const items = [];
        (slides || []).forEach((scene, sceneIndex) => {
            if (!scene) return;
            const presenter = withPresenter ? presenterItem(scene, sceneIndex) : null;
            const composition = withComposition ? compositionItem(scene, sceneIndex) : null;
            if (!scene.visual_plan) {
                if (presenter) items.push(presenter);
                if (composition) items.push(composition);
                return;
            }
            const slots = SLOTS.filter(slot => scene.visual_plan[slot]);
            slots.forEach(slot => {
                const plan = scene.visual_plan[slot];
                const review = (scene.visual_review && scene.visual_review[slot]) || null;
                items.push({
                    sceneIndex, slot, scene, plan, review, several: slots.length > 1,
                    status: STATUS[plan.review_status] ? plan.review_status : (review && STATUS[review.status] ? review.status : 'pending'),
                    stale: !!plan.review_stale
                });
            });
            if (presenter) items.push(presenter);
            if (composition) items.push(composition);
        });
        return items;
    }

    function summarize(items) {
        const counts = { scenes: new Set(items.map(item => item.sceneIndex)).size, total: items.length, pending: 0, approved: 0, changed: 0, removed: 0, none: 0 };
        items.forEach(item => {
            counts[item.status] += 1;
            if (item.status === 'removed' || item.plan.source === 'NONE') counts.none += 1;
        });
        return counts;
    }

    // Phase 21: scenes and the items in them (a scene can have a visual, a side panel, a presenter and a layout), so the
    // count matches the lesson's scenes; "to check" is the Studio's word for what still needs a look
    function summaryText(counts) {
        const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
        const parts = [plural(counts.scenes || 0, 'scene'), plural(counts.total, 'item'), `${STATUS.pending.icon} ${counts.pending} to check`,
            `${STATUS.approved.icon} ${counts.approved} approved`];
        if (counts.changed) parts.push(`${STATUS.changed.icon} ${counts.changed} changed`);
        if (counts.none) parts.push(`${STATUS.removed.icon} ${counts.none} without visual`);
        return parts.join(' · ');
    }

    function filterItems(items, filter) {
        const f = FILTERS[filter] || FILTERS.all;
        return items.filter(item => f.test(item));
    }

    // ---- the review session: current visual, decisions, navigation ----------------------------------

    const DONE = {
        keep: 'Kept: this visual will be used.',
        choose: 'Changed: your chosen visual will be used.',
        remove: 'Removed: this scene will have no visual.',
        reset: 'Back to the automatic choice.'
    };
    const PRESENTER_DONE = {
        keep: 'Kept: the presenter stays as shown.',
        choose: 'Changed: your chosen presenter clip or picture will be used.',
        move: 'Moved: the presenter stands there in this scene.',
        remove: 'Removed: no presenter in this scene.',
        reset: "Back to the Presenter Director's choice."
    };
    const COMPOSITION_DONE = {
        keep: 'Kept: the scene is composed as shown.',
        change: 'Changed: the scene is composed your way.',
        reset: 'Back to the automatic layout.'
    };
    const DIRECTION_DONE = {
        change: 'Changed: the scene follows your visual direction (nothing was generated).',
        reset: 'Back to the automatic visual direction.'
    };
    // A failure in plain words (Phase 21): what happened, that the lesson is safe, what to do. The technical reason (the
    // server's or the adapter's own words) goes to session.detail, shown only in the debug view (?visualDebug).
    const SAVE_FAILED = 'The change could not be saved. Your lesson is safe: nothing was changed. Please try again.';
    function whyText(err) {
        const status = err && err.status;
        if (status === 0) return ' (the server could not be reached)';
        if (status === 403) return ' (AI generation is turned off)';
        if (status === 429) return ' (the AI service is busy)';
        if (status === 504) return ' (it took too long; it may still finish later)';
        return '';
    }

    // ---- a scene's style (Phase 17, styles.py), in words ------------------------------------------------------------
    // The lesson's choices and the scene's own (Visual Review) are named options, never raw values. A scene may differ from the
    // lesson only in its accent and background (composition overrides style_accent / style_background, checked by the server);
    // a style change is a composition decision: it never touches a visual's review.
    const STYLE_KEYS = { accent: 'Accent colour', text_size: 'Text size', motion: 'Motion', background: 'Background', caption_size: 'Caption size',
        code_size: 'Code size', formula_size: 'Formula size', diagram_frame: 'Diagram frame' };
    const STYLE_SCENE_KEYS = [['style_accent', 'accent', 'Scene accent'], ['style_background', 'background', 'Scene background']];
    const STYLE_DONE = "Changed: this scene's style (nothing was generated; the visuals keep their review).";
    const STYLE_HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
    const STYLE_WORD = /^[a-z0-9_]{1,40}$/;

    function styleText(value) {
        const text = String(value).replace(/^style_/, '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 60);
        return text.charAt(0).toUpperCase() + text.slice(1);
    }
    function styleKeyText(key) {
        const bare = String(key).replace(/^style_/, '');
        return STYLE_KEYS[bare] || styleText(bare);
    }
    const styleScalar = v => (typeof v === 'string' && v.trim() !== '') || (typeof v === 'number' && Number.isFinite(v));

    // The scene-level choices the "Change" form offers ({accent: [...], background: [...]}, "default" first), or null
    function styleChoiceLists(adapter) {
        let raw = null;
        try { raw = adapter && (typeof adapter.styleOptions === 'function' ? adapter.styleOptions() : adapter.styleOptions); } catch (e) { raw = null; }
        if (!raw || typeof raw !== 'object') return null;
        const out = {};
        STYLE_SCENE_KEYS.forEach(([, option]) => {
            const values = Array.isArray(raw[option]) ? raw[option].filter(v => typeof v === 'string' && STYLE_WORD.test(v) && v !== 'default') : [];
            if (values.length) out[option] = ['default', ...new Set(values)];
        });
        return Object.keys(out).length ? out : null;
    }

    // ---- the lesson's quality (Phase 18, quality.py), in words ---------------------------------------------------------
    // The report comes from the composition adapter: quality() (the current report or null), qualityRun() (check again),
    // qualityStale() (the lesson changed since the report), qualityRepair(issue) (an automatic re-plan), qualityApply(issue)
    // (a suggestion applied through the composition review), selectScene(index); each optional (absent: that part is
    // hidden). The report's strings are data: shown with textContent only; only validated enum values reach attributes.
    // Quality is information: it never changes a review, a status or an approval.
    const Q_SEVERITIES = ['info', 'notice', 'warning', 'error', 'blocking'];
    const Q_RANK = Object.fromEntries(Q_SEVERITIES.map((s, i) => [s, i]));
    const QUALITY_SEVERITY = {
        info: { icon: 'ℹ', label: 'Noted' },
        notice: { icon: '⚠', label: 'Worth a look' },
        warning: { icon: '⚠', label: 'Please check' },
        error: { icon: '✕', label: 'Needs fixing' },
        blocking: { icon: '✕', label: 'Blocks the export' }
    };
    const Q_STATUSES = ['good', 'review', 'attention', 'blocked'];
    const Q_DIMENSION_STATUSES = ['pass', 'notice', 'warning', 'error', 'blocking'];
    const Q_REPAIR_KINDS = ['none', 'suggest', 'auto'];
    const Q_ACTION_TYPES = ['replan', 'scene_style', 'composition', 'none'];
    const Q_REPAIR_CLASSES = ['presentation', 'timing', 'composition', 'visual', 'presenter', 'content'];
    const Q_MAX_SCENES = 200;
    const Q_MAX_ISSUES = 500;
    const Q_MAX_SHOWN = 60;
    const Q_RULE = /^[a-z_]{1,40}\.[a-z0-9_]{1,60}$/;
    const Q_WORD = /^[a-z0-9_]{1,40}$/;
    const Q_HASH = /^[0-9a-f]{1,64}$/i;
    const qPlain = v => !!v && typeof v === 'object' && !Array.isArray(v);
    const qScene = v => Number.isInteger(v) && v >= 0 && v < Q_MAX_SCENES;
    const qText = (v, limit) => (typeof v === 'string' ? v.replace(/\s+/g, ' ').trim().slice(0, limit) : '');
    const qCount = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    const qMark = severity => (Q_RANK[severity] >= Q_RANK.error ? '✕' : '⚠');

    // One finding as the panel can use it, or null (malformed: ignored). What a button may do is decided here: "auto" only
    // for a re-plan of listed scenes, "apply" only for a scene_style / composition suggestion with its overrides.
    function qualityIssue(raw) {
        if (!qPlain(raw) || !Q_SEVERITIES.includes(raw.severity)) return null;
        const message = qText(raw.message, 400);
        if (!message) return null;
        if (raw.scene !== null && raw.scene !== undefined && !qScene(raw.scene)) return null;
        const rep = qPlain(raw.repair) ? raw.repair : {};
        const kind = Q_REPAIR_KINDS.includes(rep.kind) ? rep.kind : 'none';
        const action = qPlain(rep.action) ? rep.action : {};
        const type = Q_ACTION_TYPES.includes(action.type) ? action.type : 'none';
        const open = raw.repair_status === undefined || raw.repair_status === 'available';
        let fix = null;
        if (open && kind === 'auto' && type === 'replan' && Array.isArray(action.scenes) && action.scenes.length && action.scenes.every(qScene)) fix = 'auto';
        // a re-plan the engine only suggests (it would ask an approved scene again): applied the same way, on the user's click
        else if (open && kind === 'suggest' && type === 'replan' && Array.isArray(action.scenes) && action.scenes.length && action.scenes.every(qScene)) fix = 'replan';
        else if (open && kind === 'suggest' && (type === 'scene_style' || type === 'composition') && qScene(action.scene)
            && qPlain(action.overrides) && Object.keys(action.overrides).length) fix = 'apply';
        return {
            raw, severity: raw.severity, message, scene: qScene(raw.scene) ? raw.scene : null,
            id: typeof raw.id === 'string' && Q_HASH.test(raw.id) ? raw.id : null,
            rule: typeof raw.rule === 'string' && Q_RULE.test(raw.rule) ? raw.rule : null,
            dimension: typeof raw.dimension === 'string' && Q_WORD.test(raw.dimension) ? raw.dimension : null,
            kind, type, fix, klass: Q_REPAIR_CLASSES.includes(rep.class) ? rep.class : null, label: qText(rep.label, 80),
            scenes: fix === 'auto' || fix === 'replan' ? [...new Set(action.scenes)].slice(0, 20) : fix === 'apply' ? [action.scene] : []
        };
    }

    // The report as the panel shows it, or null. The headline count is what the list shows (malformed findings left out).
    function qualityReport(raw) {
        if (!qPlain(raw)) return null;
        const issues = (Array.isArray(raw.issues) ? raw.issues.slice(0, Q_MAX_ISSUES) : []).map(qualityIssue).filter(Boolean);
        const worst = issues.reduce((w, f) => Math.max(w, Q_RANK[f.severity]), -1);
        const derived = worst >= Q_RANK.blocking ? 'blocked' : worst >= Q_RANK.error ? 'attention' : worst >= Q_RANK.notice ? 'review' : 'good';
        const summary = qPlain(raw.summary) ? raw.summary : {};
        const scenes = Number.isInteger(summary.scenes) && summary.scenes >= 0 ? summary.scenes : (Array.isArray(raw.scenes) ? raw.scenes.length : null);
        const dimensions = [];
        if (qPlain(raw.dimensions)) {
            Object.entries(raw.dimensions).slice(0, 40).forEach(([key, d]) => {
                if (!Q_WORD.test(key) || !qPlain(d) || !Q_DIMENSION_STATUSES.includes(d.status)) return;
                dimensions.push({ key, label: qText(d.label, 60) || styleText(key), status: d.status,
                    count: issues.filter(f => f.dimension === key && f.severity !== 'info').length });
            });
        }
        return {
            raw, issues, scenes, dimensions,
            status: Q_STATUSES.includes(raw.status) ? raw.status : derived,
            attention: issues.filter(f => f.severity !== 'info').length,
            limitations: [...new Set((Array.isArray(raw.limitations) ? raw.limitations : []).map(l => qText(l, 200)).filter(Boolean))].slice(0, 8)
        };
    }

    // One plain line (Phase 21): "✓ Quality looks good" or "⚠ N things to review" (how serious each one is, the list says);
    // only a finding that blocks the export has its own words
    function qualityHeadline(report) {
        if (!report) return 'Quality not checked yet';
        switch (report.status) {
            case 'blocked': return '✕ The lesson cannot be exported as reviewed';
            case 'attention':
            case 'review': return `⚠ ${qCount(report.attention, 'thing', 'things')} to review`;
            default: return '✓ Quality looks good';
        }
    }

    // Each scene's findings (notice or more serious; info too when asked): {count, worst, issues}
    function qualityByScene(report, withInfo = false) {
        const map = new Map();
        (report ? report.issues : []).forEach(f => {
            if (f.scene === null || (!withInfo && f.severity === 'info')) return;
            const entry = map.get(f.scene) || { count: 0, worst: null, issues: [] };
            entry.issues.push(f);
            if (f.severity !== 'info') {
                entry.count += 1;
                if (!entry.worst || Q_RANK[f.severity] > Q_RANK[entry.worst]) entry.worst = f.severity;
            }
            map.set(f.scene, entry);
        });
        return map;
    }

    class ReviewSession {
        // api.review(body) records a decision; media: AiMediaApi; confirm(text) asks the user
        // presenter: {enabled(), review(body), generate(sceneIndex, {force}), canGenerate(plan)} (presenter.js adapter, Phase 12)
        // composition: {enabled(), review(body), facts(plan), statuses(scene, plan), options} (cinematic.js adapter, Phase 13), plus
        // summary(plan), regenerate(sceneIndex) (Phase 14) and the scene's visual direction (Phase 15): direction(scene, plan),
        // directionOptions(), reviewDirection(body), regenerateDirection(sceneIndex), and its synchronization (Phase 16, read-only):
        // syncSummary(scene, plan), and its style (Phase 17): styleSummary(scene, plan) (read-only) and styleOptions() (the scene's
        // accent / background choices in the composition's "Change" form)
        constructor({ slides, api, media, confirm = () => true, aiAvailable = () => true, presenter = null, composition = null }) {
            this.presenter = presenter;
            this.composition = composition;
            this.slides = slides;
            this.api = api;
            this.media = media;
            this.confirm = confirm;
            this.aiAvailable = aiAvailable;
            this.filter = 'all';
            this.index = 0;
            this.busy = false;
            this.message = '';
            this.error = false;
            this.detail = null; // {message, text}: the technical detail of that message, for the debug view only
            this.lastGeneration = null;
            // the lesson's quality (Phase 18): what the panel is doing with it ('run' | 'repair' | 'apply' | null) and its message
            this.qualityBusy = null;
            this.qualityMessage = '';
            this.qualityError = false;
            this.qualityLast = null; // the report the last "Check again" gave (used only when the adapter has no quality())
            this.refresh();
        }

        refresh(keep) {
            this.all = reviewItems(this.slides, !!(this.presenter && this.presenter.enabled()), !!(this.composition && this.composition.enabled()));
            this.annotateQuality();
            this.items = filterItems(this.all, this.filter);
            if (keep) {
                const again = this.items.findIndex(i => i.sceneIndex === keep.sceneIndex && i.slot === keep.slot);
                if (again >= 0) this.index = again;
            }
            this.index = Math.max(0, Math.min(this.index, this.items.length - 1));
        }

        get current() { return this.items[this.index] || null; }
        get summary() { return summarize(this.all); }

        // A message for the panel, with its technical detail kept apart: the debug view shows the detail next to this
        // message only (a later message never carries an older detail)
        say(message, { error = false, detail = '' } = {}) {
            this.message = message;
            this.error = error;
            this.detail = detail ? { message, text: String(detail).slice(0, 300) } : null;
        }

        detailText() {
            return this.detail && this.detail.message === this.message ? this.detail.text : '';
        }

        // A decision still on its way to the server is not dropped silently
        leave() {
            return !this.busy || this.confirm('Discard this change?');
        }

        select(index) {
            if (index === this.index || index < 0 || index >= this.items.length) return index === this.index;
            if (!this.leave()) return false;
            this.index = index;
            this.message = '';
            this.lastGeneration = null;
            return true;
        }

        next() { return this.select(this.index + 1); }
        prev() { return this.select(this.index - 1); }

        // The next visual that still needs review, after the current one (wrapping round)
        nextPending() {
            const n = this.items.length;
            for (let step = 1; step <= n; step++) {
                const i = (this.index + step) % n;
                if (this.items[i].status === 'pending') return this.select(i);
            }
            return false;
        }

        setFilter(filter) {
            if (!FILTERS[filter] || !this.leave()) return false;
            this.filter = filter;
            this.index = 0;
            this.refresh();
            return true;
        }

        canGenerate(item = this.current) {
            if (item && item.slot === 'presenter') return !!(this.presenter && this.presenter.canGenerate(item.plan));
            if (item && item.slot === 'composition') return false;
            return !!(item && generationTarget(item.scene, item.slot));
        }

        async decide(action, extra = {}) {
            const item = this.current;
            if (!item) return null;
            this.busy = true;
            this.error = false;
            this.message = 'Saving…';
            try {
                if (item.slot === 'composition') {
                    const data = await this.composition.review({ scene_index: item.sceneIndex, action, ...extra });
                    const scene = item.scene;
                    scene.visual_review = scene.visual_review || {};
                    if (data.review) scene.visual_review.composition = data.review;
                    else delete scene.visual_review.composition;
                    if (!Object.keys(scene.visual_review).length) delete scene.visual_review;
                    scene.cinematic_plan = data.plan;
                    if (data.direction) scene.visual_direction = data.direction; // the direction this composition follows (its labels)
                    this.refresh({ sceneIndex: item.sceneIndex, slot: 'composition' });
                    this.message = COMPOSITION_DONE[action] || '';
                    return data;
                }
                if (item.slot === 'presenter') {
                    const data = await this.presenter.review({ scene_index: item.sceneIndex, action, ...extra });
                    const scene = item.scene;
                    scene.visual_review = scene.visual_review || {};
                    if (data.review) scene.visual_review.presenter = data.review;
                    else delete scene.visual_review.presenter;
                    if (!Object.keys(scene.visual_review).length) delete scene.visual_review;
                    scene.presenter_plan = data.plan;
                    // the composition follows the presenter decision (as after a visual decision): no stale plan in the preview
                    if (this.composition && typeof this.composition.replan === 'function' && this.composition.enabled && this.composition.enabled()) {
                        await Promise.resolve(this.composition.replan()).catch(() => {});
                    }
                    this.refresh({ sceneIndex: item.sceneIndex, slot: 'presenter' });
                    this.message = PRESENTER_DONE[action] || '';
                    return data;
                }
                const data = await this.api.review({ scene_index: item.sceneIndex, slot: item.slot, action, ...extra });
                const scene = item.scene;
                scene.visual_review = scene.visual_review || {};
                if (data.review) scene.visual_review[item.slot] = data.review;
                else delete scene.visual_review[item.slot];
                if (!Object.keys(scene.visual_review).length) delete scene.visual_review;
                scene.visual_plan = scene.visual_plan || {};
                Object.entries(data.plans || {}).forEach(([slot, plan]) => { scene.visual_plan[slot] = Visuals.compactPlan(plan); });
                // the composition and its synchronization follow the new visual (a removed one takes its moments with it)
                if (this.composition && typeof this.composition.replan === 'function' && this.composition.enabled && this.composition.enabled()) {
                    await Promise.resolve(this.composition.replan()).catch(() => {});
                }
                this.refresh({ sceneIndex: item.sceneIndex, slot: item.slot });
                this.message = DONE[action] || '';
                return data;
            } catch (err) {
                this.say(SAVE_FAILED, { error: true, detail: err && err.message });
                return null;
            } finally {
                this.busy = false;
            }
        }

        keep() { return this.decide('keep'); }
        remove() { return this.decide('remove'); }
        reset() { return this.decide('reset'); }
        chooseAsset(asset) { return this.decide('choose', { asset_id: asset.id }); }

        // force: a new version on purpose (Phase 5 force_regenerate; the earlier asset is kept).
        // Without force the AI cache may answer with a visual generated earlier for the same request.
        async generate({ force = false } = {}) {
            const item = this.current;
            if (item && item.slot === 'presenter') return this.generatePresenter(item, force);
            if (item && item.slot === 'composition') return null; // a composition is arranged, never generated
            const target = item && generationTarget(item.scene, item.slot);
            if (!target) return null;
            if (!this.aiAvailable()) {
                this.error = true;
                this.message = 'AI generation is turned off, so no new visual can be made. You can still choose one from your Library.';
                return null;
            }
            this.busy = true;
            this.error = false;
            this.message = force ? 'Generating a new version…' : 'Generating…';
            let result;
            try {
                result = target.media === 'video'
                    ? await this.media.generateVideo(target.prompt, { force })
                    : await this.media.generateImage(target.prompt, { force });
                if (result.status === 'manual_required') {
                    // the manual workflow (a person places the file): what a teacher can do; where the file goes, for the debug view
                    this.busy = false;
                    this.say("AI videos aren't made automatically here. Choose a clip from your Library, or ask your administrator to turn on AI videos. "
                        + 'Your lesson is safe: the scene keeps its current visual.', { error: true, detail: `manual_required: its file goes at static_videos/${result.filename}` });
                    return null;
                }
                if (result.status !== 'success' || !result.asset_id) throw new Error('no visual was returned');
            } catch (err) {
                this.busy = false;
                this.say(`No new visual was made${whyText(err)}. Your lesson is safe: the scene keeps its current visual. `
                    + 'Try again, or choose one from your Library.', { error: true, detail: err && err.message });
                return null;
            }
            this.busy = false;
            // A new version replaces the router's choice; the planned visual, once made, is simply kept
            const saved = await this.decide(force ? 'choose' : 'keep', { asset_id: result.asset_id });
            if (saved) {
                this.lastGeneration = result;
                // which provider made it: the debug view only
                this.say(Visuals.generationStatus(result, target.media) || this.message, { detail: Visuals.provenanceText(result) });
            }
            return saved ? result : null;
        }
    }

    // The composition decided again (Phase 14): only the composition, never media; a regenerated composition is not
    // approved (it needs a new look), the user's explicit choices stay
    ReviewSession.prototype.regenerateComposition = async function () {
        const item = this.current;
        if (!item || item.slot !== 'composition' || !this.composition || !this.composition.regenerate) return null;
        this.busy = true;
        this.error = false;
        this.message = 'Deciding the layout again…';
        try {
            const data = await this.composition.regenerate(item.sceneIndex);
            const scene = item.scene;
            scene.cinematic_plan = data.plan;
            if (data.direction) scene.visual_direction = data.direction;
            if (scene.visual_review) {
                if (data.review) scene.visual_review.composition = data.review;
                else delete scene.visual_review.composition;
                if (!Object.keys(scene.visual_review).length) delete scene.visual_review;
            }
            this.refresh({ sceneIndex: item.sceneIndex, slot: 'composition' });
            this.message = data.review && data.review.status === 'changed'
                ? 'Layout decided again around your choices (no picture, clip or presenter was generated).'
                : 'Layout decided again (no picture, clip or presenter was generated). It needs your review.';
            return data;
        } catch (err) {
            this.say('The layout could not be decided again. Your lesson is safe: the scene keeps its layout. Please try again.',
                { error: true, detail: err && err.message });
            return null;
        } finally {
            this.busy = false;
        }
    };

    // The scene's visual direction (Phase 15), reviewed inside its composition item: the user's choices (change) or none
    // (reset), kept in the saved lesson as scene.visual_review.direction; the composition follows the new direction.
    // Nothing is generated.
    ReviewSession.prototype.decideDirection = async function (action, extra = {}) {
        const item = this.current;
        if (!item || item.slot !== 'composition' || !this.composition || !this.composition.reviewDirection) return null;
        this.busy = true;
        this.error = false;
        this.message = 'Saving…';
        try {
            const data = await this.composition.reviewDirection({ scene_index: item.sceneIndex, action, ...extra });
            const scene = item.scene;
            scene.visual_review = scene.visual_review || {};
            if (data.review) scene.visual_review.direction = data.review;
            else delete scene.visual_review.direction;
            if (!Object.keys(scene.visual_review).length) delete scene.visual_review;
            if (data.direction) scene.visual_direction = data.direction;
            else delete scene.visual_direction;
            if (data.plan) scene.cinematic_plan = data.plan;
            this.refresh({ sceneIndex: item.sceneIndex, slot: 'composition' });
            this.message = DIRECTION_DONE[action] || '';
            return data;
        } catch (err) {
            this.say(SAVE_FAILED, { error: true, detail: err && err.message });
            return null;
        } finally {
            this.busy = false;
        }
    };

    // The visual direction decided again: only the direction and the composition that follows it, never media
    ReviewSession.prototype.regenerateDirection = async function () {
        const item = this.current;
        if (!item || item.slot !== 'composition' || !this.composition || !this.composition.regenerateDirection) return null;
        this.busy = true;
        this.error = false;
        this.message = 'Deciding the visual direction again…';
        try {
            const data = await this.composition.regenerateDirection(item.sceneIndex);
            const scene = item.scene;
            if (data.direction) scene.visual_direction = data.direction;
            else delete scene.visual_direction;
            if (data.plan) scene.cinematic_plan = data.plan;
            this.refresh({ sceneIndex: item.sceneIndex, slot: 'composition' });
            this.message = 'Visual direction decided again (no picture, clip or presenter was generated).';
            return data;
        } catch (err) {
            this.say('The visual direction could not be decided again. Your lesson is safe: the scene keeps its direction. Please try again.',
                { error: true, detail: err && err.message });
            return null;
        } finally {
            this.busy = false;
        }
    };

    // A presenter clip for the scene (AI presenters only): explicit, cached like any AI media (a New Version makes
    // another and keeps the earlier one), then kept for the scene through the presenter review endpoint
    ReviewSession.prototype.generatePresenter = async function (item, force) {
        if (!this.canGenerate(item)) {
            this.error = true;
            this.message = 'This presenter is drawn by the page, or no AI presenter service is set up: there is nothing to generate.';
            return null;
        }
        this.busy = true;
        this.error = false;
        this.message = force ? 'Generating a new presenter version…' : 'Generating the presenter clip…';
        let result;
        try {
            result = await this.presenter.generate(item.sceneIndex, { force });
            if (!result || !result.asset_id) throw new Error('no clip was returned');
        } catch (err) {
            this.busy = false;
            this.say(`No presenter clip was made${whyText(err)}. Your lesson is safe: the scene keeps its presenter. `
                + 'Try again, or choose a clip from your Library.', { error: true, detail: err && err.message });
            return null;
        }
        this.busy = false;
        const saved = await this.decide(force ? 'choose' : 'keep', { asset_id: result.asset_id });
        // the clip's own warnings are for the person who checks it; which provider made it, for the debug view only
        const warnings = (result.warnings || []).filter(w => typeof w === 'string' && w.trim());
        if (saved) this.say(result.cache_hit ? 'Reused the presenter clip made earlier for the same scene (nothing new was generated).'
            : `Presenter clip made with AI for this scene${warnings.length ? ` (please check: ${warnings.join('; ')})` : ''}.`,
            { detail: result.provider ? `generated by ${result.provider}` : '' });
        return saved ? result : null;
    };

    // ---- the lesson's quality in the session (Phase 18) ------------------------------------------------------------------
    // Read-only towards the reviews: nothing here reads or writes an approval. The only change it can make is one the user
    // asks for: an automatic re-plan (the page's qualityRepair) or a suggestion applied as their composition choice.

    ReviewSession.prototype.qualityCan = function (name) {
        return !!(this.composition && typeof this.composition[name] === 'function');
    };

    // The current report (validated), or null: the adapter's quality() when it has one, else the last "Check again" answer
    ReviewSession.prototype.qualityReport = function () {
        let raw = null;
        if (this.qualityCan('quality')) {
            try { raw = this.composition.quality(); } catch (e) { raw = null; }
        } else {
            raw = this.qualityLast;
        }
        return qualityReport(raw);
    };

    ReviewSession.prototype.qualityIsStale = function () {
        if (!this.qualityCan('qualityStale')) return false;
        try { return this.composition.qualityStale() === true; } catch (e) { return false; }
    };

    // Each review item learns its scene's findings (the "Quality" filter and the list's chips); statuses stay as they are
    ReviewSession.prototype.annotateQuality = function () {
        const map = qualityByScene(this.qualityReport());
        (this.all || []).forEach(item => {
            const entry = map.get(item.sceneIndex);
            if (entry && entry.count) item.quality = { count: entry.count, worst: entry.worst };
            else delete item.quality;
        });
    };

    // One quality action at a time; lock: a change to the lesson is on its way (navigation asks first, decisions wait).
    // Errors become a short plain message (the adapter's own words only in the debug view); the panel keeps working.
    ReviewSession.prototype.qualityTask = async function (kind, working, work, done, failed, lock) {
        this.qualityBusy = kind;
        if (lock) this.busy = true;
        this.qualityError = false;
        this.qualityErrorDetail = '';
        this.qualityMessage = working;
        let result = null;
        let ok = true;
        try {
            result = await work();
        } catch (err) {
            ok = false;
            this.qualityErrorDetail = err && err.message ? String(err.message).slice(0, 200) : '';
        }
        this.qualityBusy = null;
        if (lock) this.busy = false;
        this.qualityMessage = ok ? done : failed;
        this.qualityError = !ok;
        const now = this.current;
        this.refresh(now ? { sceneIndex: now.sceneIndex, slot: now.slot } : undefined);
        if (!ok) return null;
        return result === undefined || result === null ? true : result;
    };

    ReviewSession.prototype.checkQuality = function () {
        if (!this.qualityCan('qualityRun') || this.qualityBusy) return Promise.resolve(null);
        return this.qualityTask('run', 'Checking…', async () => {
            const report = await this.composition.qualityRun();
            if (qPlain(report)) this.qualityLast = report;
            return report;
        }, '', 'The quality check could not run. Please try again.', false);
    };

    // A finding of the current report (validated again here, whatever the caller passes)
    const qualityFinding = issue => (issue && issue.raw && 'fix' in issue ? qualityIssue(issue.raw) : qualityIssue(issue));

    // kind auto: the page plans the listed scenes again (a derived plan: approval-safe, nothing is generated)
    ReviewSession.prototype.repairQuality = function (issue) {
        const f = qualityFinding(issue);
        if (!f || (f.fix !== 'auto' && f.fix !== 'replan') || !this.qualityCan('qualityRepair') || this.qualityBusy || this.busy) return Promise.resolve(null);
        return this.qualityTask('repair', 'Fixing…', () => this.composition.qualityRepair(f.raw),
            `Fixed: ${f.scenes.length === 1 ? 'the scene was' : 'the scenes were'} planned again (nothing was generated; ` +
            (f.fix === 'replan' ? 'review the scene again in Visual Review).' : 'your approvals are kept).'),
            'The automatic fix could not be applied. Please try again.', true);
    };

    // kind suggest (scene_style / composition): the page applies it through the composition review; an answer shaped like a
    // composition decision ({review, plan, direction?}) is recorded in the scene exactly as decide() records one
    ReviewSession.prototype.applyQuality = function (issue) {
        const f = qualityFinding(issue);
        if (!f || f.fix !== 'apply' || !this.qualityCan('qualityApply') || this.qualityBusy || this.busy) return Promise.resolve(null);
        return this.qualityTask('apply', 'Applying the suggestion…', async () => {
            const data = await this.composition.qualityApply(f.raw);
            const scene = this.slides[f.scenes[0]];
            if (scene && qPlain(data) && qPlain(data.plan)) {
                scene.visual_review = scene.visual_review || {};
                if (qPlain(data.review)) scene.visual_review.composition = data.review;
                else delete scene.visual_review.composition;
                if (!Object.keys(scene.visual_review).length) delete scene.visual_review;
                scene.cinematic_plan = data.plan;
                if (qPlain(data.direction)) scene.visual_direction = data.direction;
            }
            return data;
        }, 'Applied: the scene now uses this suggestion as your choice (nothing was generated).', 'The suggestion could not be applied. Please try again.', true);
    };

    // ---- the panel ---------------------------------------------------------------------------------

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

    // The panel's buttons (Phase 21): the product's button look (ui.css .ui-btn, gold for the main action) on top of the
    // classes the page's CSS and the checks already know
    const buttonClass = primary => (primary ? 'export-action ui-btn ui-btn-primary' : 'export-action export-action-secondary ui-btn');

    class VisualReviewPanel {
        // pickAsset({kinds, title, onPick}) opens the Asset Library to choose; showInLesson(index) or
        // null; mediaUrl(url) makes server-relative links loadable; onChange(sceneIndex) after a decision
        constructor({ doc, session, pickAsset, showInLesson = null, mediaUrl = url => url, onChange = () => {}, debug = false, drawChart = null }) {
            this.doc = doc;
            this.session = session;
            this.pickAsset = pickAsset;
            this.showInLesson = showInLesson;
            this.mediaUrl = mediaUrl;
            this.onChange = onChange;
            this.debug = debug;
            this.drawChart = drawChart;
            this.root = null;
            this.changeOpen = false;
            this.directionOpen = null; // the scene whose "Change direction" choices are open
            this.qualityAllAreas = false; // the lesson's quality: the areas with nothing to look at are folded away
            this.qualityCollapsed = false; // the quality details (areas, findings, notes) hidden by the user
        }

        build() {
            if (this.root) return;
            const h = (...args) => el(this.doc, ...args);
            this.summary = h('p', { class: 'review-summary', 'aria-live': 'polite' });
            this.filters = h('div', { class: 'review-filters', role: 'group', 'aria-labelledby': 'review-filters-label' },
                h('span', { id: 'review-filters-label', class: 'review-filters-label', text: 'Show:' }));
            Object.entries(FILTERS).forEach(([key, f]) => {
                this.filters.appendChild(h('button', { type: 'button', class: 'review-filter', 'data-filter': key, 'aria-pressed': 'false', text: f.label,
                    onclick: () => { if (this.session.setFilter(key)) { this.changeOpen = false; this.render(); } } }));
            });
            this.status = h('div', { class: 'asset-status review-status', role: 'status', 'aria-live': 'polite' });
            this.list = h('ul', { class: 'review-list', 'aria-label': 'Scenes' });
            this.detail = h('div', { class: 'review-detail' });
            // the lesson's quality (Phase 18): drawn by renderQuality, hidden when the page offers no quality report
            this.qualityBox = h('section', { class: 'quality-panel', 'aria-label': 'Quality', hidden: true });
            // the heading takes the focus when the panel opens (and when the focused control is redrawn away)
            this.title = h('h2', { id: 'review-panel-title', tabindex: '-1', text: 'Visual Review' });
            this.panel = h('div', { class: 'asset-panel review-panel', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'review-panel-title' },
                h('div', { class: 'asset-head' },
                    this.title,
                    h('button', { type: 'button', class: 'export-close', 'aria-label': 'Close Visual Review', title: 'Close', text: '✕', onclick: () => this.close() })),
                h('p', { class: 'asset-intro', text: 'Check the visual of each scene before exporting: keep it, choose another one, or remove it.' }),
                this.qualityBox, this.summary, this.filters, this.status,
                h('div', { class: 'review-body' }, this.list, this.detail));
            this.root = h('div', { class: 'asset-overlay review-overlay', 'data-review-runtime': true }, this.panel);
            this.root.addEventListener('keydown', e => e.stopPropagation()); // slide shortcuts stay out
            this.root.addEventListener('click', e => { if (e.target === this.root) this.close(); });
            this.root.addEventListener('focusin', e => this.noteFocus(e.target));
            // Escape closes, Tab stays inside the panel (Phase 21); both give way to the Asset Library opened on top of it
            this.onEscape = e => {
                if (!this.root.classList.contains('open') || this.doc.querySelector('.asset-overlay.open:not(.review-overlay)')) return;
                if (typeof this.root.closest === 'function' && this.root.closest('[inert]')) return; // a dialog above this one (e.g. sign in) made it inert: the keys are not ours
                if (e.key === 'Escape') this.close();
                else if (e.key === 'Tab') this.trapTab(e);
            };
            this.doc.body.appendChild(this.root);
        }

        open() {
            this.build();
            const wasOpen = this.root.classList.contains('open');
            if (!wasOpen) {
                const active = this.doc.activeElement;
                this.opener = active && active !== this.doc.body && !this.within(active) ? active : null;
            }
            this.session.refresh();
            this.changeOpen = false;
            this.directionOpen = null;
            this.lastFocus = null;
            this.root.classList.add('open');
            this.doc.addEventListener('keydown', this.onEscape, true);
            this.render();
            this.parkFocus();
        }

        close() {
            if (!this.root) return;
            const wasOpen = this.root.classList.contains('open');
            this.root.classList.remove('open');
            this.doc.removeEventListener('keydown', this.onEscape, true);
            this.detail.querySelectorAll('video').forEach(v => v.pause());
            // back to the control that opened the panel (never left on <body>)
            const opener = this.opener;
            this.opener = null;
            if (wasOpen && opener && typeof opener.focus === 'function' && opener.isConnected !== false) {
                try { opener.focus(); } catch (e) { /* not essential */ }
            }
        }

        // ---- focus (Phase 21): into the panel on open, kept inside it, and kept on the same control across a redraw ----------

        within(node) {
            return !!(node && this.panel && typeof this.panel.contains === 'function' && this.panel.contains(node));
        }

        // The controls Tab moves between: enabled, shown, not taken out of the order
        focusables() {
            return Array.from(this.panel.querySelectorAll('button, select, input, textarea, a[href], video[controls], [tabindex]')).filter(node =>
                !node.disabled && node.getAttribute('tabindex') !== '-1' && node.getAttribute('disabled') === null
                && (typeof node.getClientRects !== 'function' || node.getClientRects().length > 0));
        }

        trapTab(e) {
            const list = this.focusables();
            if (!list.length) return;
            const active = this.doc.activeElement;
            const at = list.indexOf(active);
            let to = null;
            if (!this.within(active)) to = e.shiftKey ? list[list.length - 1] : list[0];
            else if (e.shiftKey && at <= 0) to = list[list.length - 1];
            else if (!e.shiftKey && at === list.length - 1) to = list[0];
            if (!to) return;
            if (typeof e.preventDefault === 'function') e.preventDefault();
            to.focus();
        }

        // The control focused last, as a selector that finds it again after the panel was drawn again (null: not one of ours)
        focusKey(node) {
            if (!node || typeof node.getAttribute !== 'function') return null;
            const q = v => String(v === null || v === undefined ? '' : v).replace(/["\\\]]/g, '');
            const has = name => node.classList && node.classList.contains(name);
            const tag = String(node.tagName || node.tag || '').toLowerCase();
            if (has('review-item')) return `.review-item[data-scene="${q(node.getAttribute('data-scene'))}"][data-slot="${q(node.getAttribute('data-slot'))}"]`;
            if (has('review-filter')) return `.review-filter[data-filter="${q(node.getAttribute('data-filter'))}"]`;
            if (has('export-close')) return '.review-panel .export-close';
            if (tag === 'select' && node.id) return `#${q(node.id)}`;
            const action = node.getAttribute('data-action');
            if (!action) return null;
            const region = this.qualityBox && typeof this.qualityBox.contains === 'function' && this.qualityBox.contains(node) ? '.quality-panel' : '.review-detail';
            const issue = typeof node.closest === 'function' ? node.closest('.quality-issue[data-issue]') : null;
            return issue ? `${region} .quality-issue[data-issue="${q(issue.getAttribute('data-issue'))}"] [data-action="${q(action)}"]`
                : `${region} [data-action="${q(action)}"]`;
        }

        noteFocus(node) {
            if (node === this.title) return;
            const key = this.focusKey(node);
            if (key) {
                this.lastFocus = key;
                this.focusParked = false;
            }
        }

        // The heading holds the focus until a control is focused again
        parkFocus() {
            if (!this.title || typeof this.title.focus !== 'function') return;
            this.focusParked = true;
            try { this.title.focus(); } catch (e) { /* not essential */ }
        }

        // After a redraw: the same control again if the focused one was drawn away (or the heading, while it holds the focus)
        restoreFocus() {
            if (!this.root || !this.root.classList.contains('open') || !('activeElement' in this.doc)) return;
            if (this.doc.querySelector('.asset-overlay.open:not(.review-overlay)')) return; // the Asset Library on top has it
            const active = this.doc.activeElement;
            if (this.within(active) && !(this.focusParked && active === this.title)) return;
            const again = this.lastFocus ? this.panel.querySelector(this.lastFocus) : null;
            if (again && this.focusables().includes(again)) {
                this.focusParked = false;
                try { again.focus(); } catch (e) { /* not essential */ }
                return;
            }
            if (active !== this.title) this.parkFocus();
        }

        render() {
            const s = this.session;
            s.annotateQuality(); // the report may have changed since the list was made (the chips follow it)
            this.summary.textContent = summaryText(s.summary);
            const quality = s.qualityCan('quality') || s.qualityCan('qualityRun');
            this.filters.querySelectorAll('.review-filter').forEach(b => {
                b.setAttribute('aria-pressed', String(b.getAttribute('data-filter') === s.filter));
                if (b.getAttribute('data-filter') === 'quality') {
                    if (quality || s.filter === 'quality') b.removeAttribute('hidden');
                    else b.setAttribute('hidden', '');
                }
            });
            // the technical reason next to the plain message: the debug view only
            const detail = this.debug && typeof s.detailText === 'function' ? s.detailText() : '';
            this.status.textContent = s.message + (s.message && detail ? ` (${detail})` : '');
            this.status.setAttribute('data-kind', s.error ? 'error' : 'info');
            this.renderQuality();
            this.renderList();
            this.renderDetail();
        }

        renderList() {
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            this.list.textContent = '';
            if (!s.items.length) {
                this.list.appendChild(h('li', { class: 'asset-empty', text: s.all.length ? 'No scene matches this filter.' : 'This lesson has no visuals to review.' }));
                return;
            }
            const chipped = new Set(); // the scene's quality chip once, on its first row shown
            s.items.forEach((item, i) => {
                const title = `Scene ${item.sceneIndex + 1}${item.several ? ({ side: ' · Side panel', presenter: ' · Presenter', composition: ' · Layout' }[item.slot] || ' · Main visual') : ''}`;
                const q = item.quality && item.quality.count > 0 && Q_SEVERITIES.includes(item.quality.worst) && !chipped.has(item.sceneIndex) ? item.quality : null;
                if (q) chipped.add(item.sceneIndex);
                const button = h('button', { type: 'button', class: 'review-item' + (i === s.index ? ' selected' : ''), 'data-scene': String(item.sceneIndex), 'data-slot': item.slot,
                    'aria-current': i === s.index ? 'true' : null, onclick: () => { if (s.select(i)) { this.changeOpen = false; this.render(); } } },
                h('span', { class: 'review-item-main' },
                    h('span', { class: 'review-item-title', text: `${title}: ${item.scene.title || 'Untitled'}` }),
                    h('span', { class: 'review-item-meta', text: item.composition ? `Layout · ${item.plan.template_label || item.plan.template}`
                        : item.presenter ? (item.plan.enabled ? `Presenter · ${POSITIONS[item.plan.position] || styleText(item.plan.position || '')}` : 'Presenter hidden')
                        : sourceText(item.plan) })),
                h('span', { class: 'review-chip', 'data-status': item.status, text: statusText(item.status) }),
                q ? h('span', { class: 'quality-chip', 'data-severity': q.worst, title: `Quality: ${qCount(q.count, 'thing', 'things')} to look at in this scene`,
                    text: `${qMark(q.worst)} ${q.count}` }) : null);
                this.list.appendChild(h('li', {}, button));
            });
        }

        renderPreview(item) {
            const h = (...args) => el(this.doc, ...args);
            const { plan, scene } = item;
            const kind = previewKind(plan, scene);
            const alt = `Visual for scene ${item.sceneIndex + 1}: ${scene.title || 'untitled'}`;
            const box = h('div', { class: 'review-preview', 'data-kind': kind });
            if (kind === 'image') {
                box.appendChild(h('img', { src: this.mediaUrl(plan.url), alt }));
            } else if (kind === 'video') {
                box.appendChild(h('video', { src: this.mediaUrl(plan.url), controls: true, muted: true, playsinline: true, preload: 'metadata', 'aria-label': alt }));
            } else if (kind === 'chart' && this.drawChart) {
                const canvas = h('canvas', { role: 'img', 'aria-label': `${alt} (chart)` });
                box.appendChild(canvas);
                setTimeout(() => this.drawChart(canvas, scene.side_panel), 0);
            } else {
                const text = {
                    'not-generated': 'Not generated yet',
                    removed: 'No visual for this scene',
                    none: 'No visual',
                    unavailable: 'Visual unavailable',
                    described: `${sourceText(plan)}, drawn when the scene plays`,
                    chart: `${sourceText(plan)}, drawn when the scene plays`
                }[kind] || sourceText(plan);
                box.appendChild(h('p', { class: 'review-placeholder', text }));
            }
            return box;
        }

        // The current item's detail, drawn again; the focus stays on the same control (Phase 21)
        renderDetail() {
            this.drawDetail();
            this.restoreFocus();
        }

        drawDetail() {
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            const item = s.current;
            this.detail.querySelectorAll('video').forEach(v => v.pause());
            this.detail.textContent = '';
            if (!item) {
                this.detail.appendChild(h('p', { class: 'asset-detail-empty', text: 'Nothing to review here.' }));
                return;
            }
            if (item.slot === 'presenter') return this.renderPresenterDetail(item);
            if (item.slot === 'composition') return this.renderCompositionDetail(item);
            const { plan, scene } = item;
            const button = (text, handler, extra = {}) => h('button', { type: 'button', class: buttonClass(extra.primary),
                text, disabled: s.busy || extra.disabled, title: extra.title, 'data-action': extra.action, onclick: handler });
            const run = async promise => { this.render(); const result = await promise; this.changeOpen = false; this.render(); if (result) this.onChange(item.sceneIndex); };
            const aiOk = s.aiAvailable();
            const target = generationTarget(scene, item.slot);

            // Where it came from, in plain words for everyone ("Made with AI", "From your library"); which AI provider and
            // model made (or will make) it only in the debug view (?visualDebug)
            const origin = Visuals.originText ? Visuals.originText(plan) : '';
            const provider = this.debug && plan.provider && plan.provider !== 'manual' && Visuals.provenanceText
                ? [h('dt', { text: 'AI provider' }), h('dd', { class: 'review-provider', text: Visuals.provenanceText(plan) })] : [];
            const facts = h('dl', { class: 'asset-meta review-facts' },
                h('dt', { text: 'Visual' }), h('dd', { class: 'review-source', text: sourceText(plan) + (plan.cache_hit ? ' · ♻ Reused existing visual' : '') }),
                origin ? [h('dt', { text: 'Origin' }), h('dd', { class: 'review-origin', text: origin })] : null,
                ...provider,
                h('dt', { text: 'Status' }), h('dd', { class: 'review-state', 'data-status': item.status, text: statusText(item.status) }),
                h('dt', { text: 'Why' }), h('dd', { class: 'review-reason', text: reasonText(plan) }));
            const notes = [];
            if ((plan.quality_warnings || []).length) notes.push(h('p', { class: 'review-note review-quality', text:
                `Please check this visual: ${plan.quality_warnings.join('; ')}.` }));
            if (item.stale) notes.push(h('p', { class: 'review-note', text: item.status === 'pending'
                ? 'The scene changed after you approved its visual, so it needs a new look.'
                : 'The scene changed after this choice. Your choice is kept until you change it.' }));
            if (plan.error) notes.push(h('p', { class: 'review-note review-note-error', text: 'Choose another visual, generate a new version, or go back to the automatic choice.' }));

            const actions = h('div', { class: 'asset-actions review-actions' });
            if (plan.error) {
                actions.append(button('Choose another visual', () => this.openPicker(item), { primary: true, action: 'pick' }));
            } else if (item.status !== 'removed') {
                actions.append(button(item.status === 'approved' ? '✓ Kept' : 'Keep', () => run(s.keep()), { primary: true, action: 'keep', disabled: item.status === 'approved' }));
            }
            actions.append(button(this.changeOpen ? 'Change ▴' : 'Change ▾', () => { this.changeOpen = !this.changeOpen; this.renderDetail(); }, { action: 'change' }));
            if (item.status === 'removed') {
                actions.append(button('Restore automatic choice', () => run(s.reset()), { action: 'reset' }));
            } else {
                actions.append(button('Remove', () => run(s.remove()), { action: 'remove' }));
            }

            const change = h('div', { class: 'review-change', hidden: !this.changeOpen });
            change.append(button('Choose from your Library', () => this.openPicker(item), { action: 'pick' }));
            if (target) {
                const hasAi = AI.includes(plan.source) && !!plan.asset_id;
                const label = hasAi || plan.url ? 'Generate new AI version' : `Generate AI ${target.media}`;
                change.append(button(label, () => run(s.generate({ force: hasAi || !!plan.url })), {
                    action: 'generate', disabled: !aiOk,
                    title: aiOk ? 'Makes a new visual with AI; the current one stays in your library' : 'AI visuals are turned off'
                }));
            }
            if (item.status === 'changed' || item.status === 'approved') change.append(button('Back to automatic choice', () => run(s.reset()), { action: 'reset' }));
            if (!aiOk && target) change.append(h('p', { class: 'review-note', text: 'AI generation is off: new AI visuals cannot be made, but cached ones and your Library still work.' }));

            const nav = h('div', { class: 'review-nav' },
                button('‹ Previous', () => { if (s.prev()) { this.changeOpen = false; this.render(); } }, { action: 'prev', disabled: s.index === 0 }),
                button('Next ›', () => { if (s.next()) { this.changeOpen = false; this.render(); } }, { action: 'next', disabled: s.index >= s.items.length - 1 }),
                button('Next needing review', () => { if (s.nextPending()) { this.changeOpen = false; this.render(); } }, { action: 'next-pending', disabled: !s.items.some(i => i.status === 'pending') }));
            if (this.showInLesson) nav.append(button('Show in lesson', () => { this.close(); this.showInLesson(item.sceneIndex); }, { action: 'show' }));

            const debug = this.debug ? h('pre', { class: 'review-debug', text: JSON.stringify({ source: plan.source, selection: plan.selection, reason: plan.reason,
                renderer: plan.renderer || null, provider: plan.provider || null, model: plan.model || null, asset_id: plan.asset_id || null,
                cache_hit: !!plan.cache_hit, review: item.review }, null, 2) }) : null;

            // Appended like el() children: the notes list flattened, absent parts skipped (a plain append
            // would turn the list and nulls into text)
            [h('p', { class: 'review-position', text: `Scene ${item.sceneIndex + 1} of ${this.session.slides.length}${item.several ? (item.slot === 'side' ? ' · Side panel' : ' · Main visual') : ''}` }),
                h('h3', { class: 'asset-detail-name review-title', text: scene.title || 'Untitled scene' }),
                purposeText(scene) ? h('p', { class: 'review-purpose', text: purposeText(scene) }) : null,
                this.renderPreview(item), facts, notes, actions, change, nav, debug]
                .flat().filter(Boolean).forEach(part => this.detail.appendChild(part));
        }

        renderPresenterDetail(item) {
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            const { plan, scene } = item;
            const adapter = s.presenter;
            const button = (text, handler, extra = {}) => h('button', { type: 'button', class: buttonClass(extra.primary),
                text, disabled: s.busy || extra.disabled, title: extra.title, 'data-action': extra.action, onclick: handler });
            const run = async promise => { this.render(); const result = await promise; this.changeOpen = false; this.render(); if (result) this.onChange(item.sceneIndex); };
            const preview = h('div', { class: 'review-preview', 'data-kind': 'presenter' });
            const media = plan.media;
            if (!plan.enabled) {
                preview.appendChild(h('p', { class: 'review-placeholder', text: 'No presenter in this scene' }));
            } else if (media && media.url && media.kind === 'video') {
                preview.appendChild(h('video', { src: this.mediaUrl(media.url), controls: true, muted: true, playsinline: true, preload: 'metadata', 'aria-label': 'Presenter clip' }));
            } else if (media && media.url && media.kind === 'image') {
                preview.appendChild(h('img', { src: this.mediaUrl(media.url), alt: 'Presenter picture' }));
            } else if (plan.type === 'illustrated' && adapter.figure) {
                const figure = h('div', { class: 'review-presenter-figure' });
                figure.innerHTML = adapter.figure(plan);
                preview.appendChild(figure);
            } else {
                preview.appendChild(h('p', { class: 'review-placeholder', text: plan.type === 'mascot' ? 'Aadhi, the animated mascot' : 'No presenter clip yet' }));
            }
            const facts = h('dl', { class: 'asset-meta review-facts' },
                (adapter.facts ? adapter.facts(plan) : []).flatMap(([dt, dd]) => [h('dt', { text: dt }), h('dd', { class: 'review-presenter-' + dt.toLowerCase().replace(/\s+/g, '-'), text: dd })]),
                h('dt', { text: 'Status' }), h('dd', { class: 'review-state', 'data-status': item.status, text: statusText(item.status) }));
            const actions = h('div', { class: 'asset-actions review-actions' });
            if (item.status !== 'removed') actions.append(button(item.status === 'approved' ? '✓ Kept' : 'Keep', () => run(s.keep()), { primary: true, action: 'keep', disabled: item.status === 'approved' }));
            actions.append(button(this.changeOpen ? 'Change ▴' : 'Change ▾', () => { this.changeOpen = !this.changeOpen; this.renderDetail(); }, { action: 'change' }));
            actions.append(item.status === 'removed' ? button('Restore automatic choice', () => run(s.reset()), { action: 'reset' })
                : button('Remove presenter', () => run(s.remove()), { action: 'remove' }));
            const change = h('div', { class: 'review-change', hidden: !this.changeOpen });
            change.append(h('div', { class: 'review-presenter-moves' },
                [['left', 'Left'], ['right', 'Right'], ['center', 'Center'], ['pip', 'Small corner']].map(([pos, label]) => button(label, () => run(s.decide('move', { position: pos })),
                    { action: 'move-' + pos, disabled: plan.enabled && (plan.placement === 'pip' ? pos === 'pip' : plan.position === pos && pos !== 'pip') }))));
            change.append(button('Choose clip or picture from your Library', () => this.openPicker(item), { action: 'pick' }));
            if (plan.type === 'ai_avatar' || plan.type === 'custom') {
                const hasClip = !!(media && media.asset_id);
                change.append(button(hasClip ? 'Generate new presenter version' : 'Generate presenter clip', () => run(s.generate({ force: hasClip })), {
                    action: 'generate', disabled: !s.canGenerate(item),
                    title: s.canGenerate(item) ? 'Makes a presenter clip for this scene with AI (may be billed); an earlier clip stays in your library'
                        : 'No AI presenter service is set up on this server' }));
            }
            if (item.status === 'changed' || item.status === 'approved') change.append(button("Back to the Director's choice", () => run(s.reset()), { action: 'reset' }));
            const nav = h('div', { class: 'review-nav' },
                button('‹ Previous', () => { if (s.prev()) { this.changeOpen = false; this.render(); } }, { action: 'prev', disabled: s.index === 0 }),
                button('Next ›', () => { if (s.next()) { this.changeOpen = false; this.render(); } }, { action: 'next', disabled: s.index >= s.items.length - 1 }));
            if (this.showInLesson) nav.append(button('Show in lesson', () => { this.close(); this.showInLesson(item.sceneIndex); }, { action: 'show' }));
            [h('p', { class: 'review-position', text: `Scene ${item.sceneIndex + 1} of ${s.slides.length} · Presenter` }),
                h('h3', { class: 'asset-detail-name review-title', text: scene.title || 'Untitled scene' }),
                preview, facts, actions, change, nav].forEach(part => this.detail.appendChild(part));
        }

        // The scene inspector (Phase 13): the composition drawn to scale, what it contains, each element's state, and
        // simple changes (template, sides, camera, transition, background). "Preview this scene" plays it for real.
        renderCompositionDetail(item) {
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            const { plan, scene } = item;
            const adapter = s.composition;
            const button = (text, handler, extra = {}) => h('button', { type: 'button', class: buttonClass(extra.primary),
                text, disabled: s.busy || extra.disabled, title: extra.title, 'data-action': extra.action, onclick: handler });
            const run = async promise => { this.render(); const result = await promise; this.changeOpen = false; this.render(); if (result) this.onChange(item.sceneIndex); };
            const layerText = id => LAYERS[id] || styleText(String(id || ''));
            const shown = (plan.layers || []).filter(l => !['background', 'subtitles'].includes(l.type)).map(l => layerText(l.id));
            const frame = h('div', { class: 'review-composition-frame', role: 'img', 'data-template': plan.template,
                'data-background': (plan.background || {}).type || '', 'aria-label': `Layout of scene ${item.sceneIndex + 1}: ${shown.join(', ')}` });
            (plan.layers || []).filter(l => l.type !== 'background').forEach(l => {
                const box = h('div', { class: 'review-composition-box', 'data-layer': l.id, text: layerText(l.id) });
                Object.assign(box.style, { left: `${l.box.x * 100}%`, top: `${l.box.y * 100}%`, width: `${l.box.w * 100}%`, height: `${l.box.h * 100}%` });
                frame.appendChild(box);
            });
            const cam = plan.camera || {};
            if (cam.movement && cam.movement !== 'static' && cam.to) {
                const f = h('div', { class: 'review-composition-camera', title: 'Where the camera ends' });
                Object.assign(f.style, { left: `${cam.to.x * 100}%`, top: `${cam.to.y * 100}%`, width: `${cam.to.w * 100}%`, height: `${cam.to.w * 100}%` });
                frame.appendChild(f);
            }
            const preview = h('div', { class: 'review-preview', 'data-kind': 'composition' }, frame);
            const facts = h('dl', { class: 'asset-meta review-facts' },
                (adapter.facts ? adapter.facts(plan) : []).flatMap(([dt, dd]) => [h('dt', { text: dt }),
                    h('dd', { class: 'review-composition-fact', 'data-fact': dt.toLowerCase().replace(/\s+/g, '-'), text: dd })]),
                h('dt', { text: 'Status' }), h('dd', { class: 'review-state', 'data-status': item.status, text: statusText(item.status) }));
            const marks = { ready: '✓', generating: '●', warning: '⚠' };
            const statuses = h('ul', { class: 'review-composition-elements', 'aria-label': 'Elements of this scene' },
                (adapter.statuses ? adapter.statuses(scene, plan) : []).map(([name, state, text]) => h('li', { 'data-state': state, 'data-element': name },
                    h('span', { class: 'review-composition-mark', text: marks[state] || '⚠' }), ` ${name}: ${text}`)));
            // How it was decided (Phase 14): short reasons and what was adjusted, never model reasoning
            const summary = adapter.summary ? adapter.summary(plan) : null;
            const decided = summary ? h('div', { class: 'review-composition-decided', 'data-source': summary.source },
                h('p', { class: 'review-composition-auto', text: `Layout: ${summary.label} · ${summary.template}` }), // (Phase 21: the row's word)
                summary.reasons.length ? h('ul', { class: 'review-composition-reasons', 'aria-label': 'Why this layout' },
                    summary.reasons.map(r => h('li', { text: r }))) : null,
                summary.repairs.length ? h('p', { class: 'review-note review-composition-repairs', text: `Adjusted: ${summary.repairs.join('; ')}.` }) : null,
                summary.ai ? h('p', { class: 'review-note review-composition-ai', text: summary.ai }) : null) : null;
            // The visual direction the composition follows (Phase 15), above it
            const direction = adapter.direction ? adapter.direction(scene, plan) : null;
            const directed = direction ? this.renderDirection(item, direction) : null;
            // When things happen with the narration (Phase 16), under the direction: read-only
            const sync = adapter.syncSummary ? adapter.syncSummary(scene, plan) : null;
            const synced = sync ? this.renderSync(sync) : null;
            // The lesson's style on this scene (Phase 17), under the synchronization: read-only
            const style = this.styleSummaryOf(item);
            const styled = this.renderStyle(item, style);
            // What the lesson's quality check found in this scene (Phase 18), under the drawing: information, never a status
            const quality = this.renderSceneQuality(item);
            const notes = [];
            if (item.stale) notes.push(h('p', { class: 'review-note', text: item.status === 'pending'
                ? 'The scene changed after you approved its layout (for example its visual or its presenter), so it needs a new look.'
                : 'The scene changed after this choice. Your choice is kept until you change it.' }));
            (plan.warnings || []).concat(plan.notes || []).forEach(text => notes.push(h('p', { class: 'review-note review-composition-note', text })));
            const actions = h('div', { class: 'asset-actions review-actions' });
            actions.append(button(item.status === 'approved' ? '✓ Kept' : 'Keep', () => run(s.keep()), { primary: true, action: 'keep', disabled: item.status === 'approved' }));
            actions.append(button(this.changeOpen ? 'Change ▴' : 'Change ▾', () => { this.changeOpen = !this.changeOpen; this.renderDetail(); }, { action: 'change' }));
            if (item.status === 'changed' || item.status === 'approved') actions.append(button('Back to automatic', () => run(s.reset()), { action: 'reset' }));
            if (adapter.regenerate) actions.append(button('Decide the layout again', () => run(s.regenerateComposition()), { action: 'regenerate',
                title: 'Decides the layout again (no picture, clip or presenter is generated)' }));
            const change = h('div', { class: 'review-change review-composition-change', hidden: !this.changeOpen });
            const options = adapter.options || {};
            const current = { template: plan.template, presenter_position: plan.presenter && plan.presenter.shown ? plan.presenter.side : 'hidden',
                camera: cam.movement, transition: (plan.transition || {}).in, background: (plan.background || {}).type };
            const picks = {};
            const locked = (summary && summary.locked) || [];
            const chosen = (summary && summary.chosen) || locked;  // the user's own choices ("Automatic" gives those back)
            [['template', 'Layout'], ['presenter_position', 'Presenter'], ['presenter_size', 'Presenter size'], ['visual_size', 'Visual size'],
                ['visual_position', 'Visual side'], ['camera', 'Camera'], ['motion', 'Motion'], ['transition', 'Transition'], ['background', 'Background']].forEach(([key, label]) => {
                const choices = options[key];
                if (!choices) return;
                const id = `composition-${key}`;
                const select = h('select', { id, class: 'neon-input ui-focusable', 'data-key': key, 'aria-label': label },
                    h('option', { value: '', text: `${label}: as it is${chosen.includes(key) ? ' (your choice)' : locked.includes(key) ? ' (as the screenplay asks)' : ' (automatic)'}` }),
                    chosen.includes(key) ? h('option', { value: 'auto', text: `${label}: Automatic` }) : null,
                    Object.entries(choices).map(([value, text]) => h('option', { value, text: `${label}: ${text}` })));
                picks[key] = select;
                change.appendChild(select);
            });
            // The scene's own accent and background (Phase 17): "Same as the lesson" when the scene has none; shown only when the
            // page knows the vocabulary
            const stylePicks = {};
            const styleChoices = styleChoiceLists(adapter);
            const saved = item.review && item.review.overrides && typeof item.review.overrides === 'object' ? item.review.overrides : {};
            STYLE_SCENE_KEYS.forEach(([key, option, label]) => {
                const values = styleChoices && styleChoices[option];
                if (!values) return;
                const now = values.includes(saved[key]) ? saved[key] : 'default';
                const select = h('select', { id: `composition-${key}`, class: 'neon-input ui-focusable review-style-select', 'data-key': key, 'aria-label': label },
                    values.map(value => h('option', { value, selected: value === now,
                        text: `${label}: ${value === 'default' ? 'Same as the lesson' : styleText(value)}${value === now && now !== 'default' ? ' (this scene)' : ''}` })));
                select.value = now;
                stylePicks[key] = { select, values, now };
                change.appendChild(select);
            });
            change.appendChild(button('Apply', () => {
                const overrides = {};
                Object.entries(picks).forEach(([key, select]) => { if (select.value && select.value !== current[key]) overrides[key] = select.value; });
                // an unchanged style choice is not sent; "Same as the lesson" gives a scene's own choice back ("auto" removes it)
                Object.entries(stylePicks).forEach(([key, { select, values, now }]) => {
                    const value = typeof select.value === 'string' && select.value ? select.value : now;
                    if (value !== now && values.includes(value)) overrides[key] = value === 'default' ? 'auto' : value;
                });
                if (!Object.keys(overrides).length) { s.message = 'Nothing to change: pick a new value first.'; this.render(); return; }
                const styleOnly = Object.keys(overrides).every(key => key in stylePicks);
                run(s.decide('change', { overrides }).then(result => { if (result && styleOnly) s.message = STYLE_DONE; return result; }));
            }, { primary: true, action: 'apply' }));
            const nav = h('div', { class: 'review-nav' },
                button('‹ Previous', () => { if (s.prev()) { this.changeOpen = false; this.render(); } }, { action: 'prev', disabled: s.index === 0 }),
                button('Next ›', () => { if (s.next()) { this.changeOpen = false; this.render(); } }, { action: 'next', disabled: s.index >= s.items.length - 1 }));
            if (this.showInLesson) nav.append(button('Preview this scene', () => { this.close(); this.showInLesson(item.sceneIndex); }, { action: 'show' }));
            const debug = this.debug ? h('pre', { class: 'review-debug', text: JSON.stringify({ template: plan.template, camera: plan.camera,
                transition: plan.transition, plan_hash: plan.plan_hash, fingerprint: plan.fingerprint, review: item.review,
                style: style ? { id: style.id, fingerprint: style.fingerprint } : null }, null, 2) }) : null;
            [h('p', { class: 'review-position', text: `Scene ${item.sceneIndex + 1} of ${s.slides.length} · Layout` }),
                h('h3', { class: 'asset-detail-name review-title', text: scene.title || 'Untitled scene' }),
                preview, quality, directed, synced, styled, decided, facts, statuses, notes, actions, change, nav, debug].flat().filter(Boolean).forEach(part => this.detail.appendChild(part));
        }

        // ---- the lesson's quality (Phase 18) -------------------------------------------------------------------------------
        // A lesson-wide section above the review: the headline from the report's status, the areas checked (those with something
        // to look at first, the rest folded), the findings grouped by scene with "Show scene" and, when the page offers one, a
        // fix ("Fix automatically" re-plans; a suggestion is applied through the composition review), "Check again", the
        // check's limitations and a stale note. Engineering details only in the debug view. Built with textContent only.
        renderQuality() {
            this.drawQuality();
            this.restoreFocus();
        }

        drawQuality() {
            const box = this.qualityBox;
            if (!box) return;
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            box.textContent = '';
            if (!s.qualityCan('quality') && !s.qualityCan('qualityRun')) {
                box.setAttribute('hidden', '');
                box.removeAttribute('data-status');
                return;
            }
            box.removeAttribute('hidden');
            const report = s.qualityReport();
            const stale = !!report && s.qualityIsStale();
            box.setAttribute('data-status', report ? report.status : 'none');
            if (s.qualityBusy === 'run') box.setAttribute('aria-busy', 'true');
            else box.removeAttribute('aria-busy');

            const head = h('div', { class: 'quality-head' },
                h('p', { class: 'quality-headline', 'data-status': report ? report.status : 'none', text: qualityHeadline(report) }),
                report && report.scenes !== null ? h('p', { class: 'quality-scenes', text: `${qCount(report.scenes, 'scene', 'scenes')} checked` }) : null);
            const run = () => this.qualityAct(s.checkQuality(), []);
            if (s.qualityCan('qualityRun')) {
                head.appendChild(h('button', { type: 'button', class: buttonClass(false) + ' quality-run', 'data-action': 'quality-run',
                    text: report ? 'Check again' : 'Check quality', disabled: !!s.qualityBusy,
                    title: 'Checks the whole lesson again (nothing is saved, generated or approved)', onclick: run }));
            }
            if (report && report.attention) {
                head.appendChild(h('button', { type: 'button', class: buttonClass(false) + ' quality-toggle', 'data-action': 'quality-toggle',
                    'aria-expanded': String(!this.qualityCollapsed), 'aria-controls': 'review-quality-details', text: this.qualityCollapsed ? 'Show details ▾' : 'Hide details ▴',
                    onclick: () => { this.qualityCollapsed = !this.qualityCollapsed; this.renderQuality(); } }));
            }
            box.appendChild(head);
            box.appendChild(h('p', { class: 'quality-help', text: 'Checks your lesson for readability and consistency.' }));
            // stale: said with an icon and words, and the way out right there ("Check again"; the fixes wait for it)
            if (stale) {
                box.appendChild(h('p', { class: 'review-note quality-stale' },
                    h('span', { class: 'quality-mark', 'aria-hidden': 'true', text: '⚠' }), ' ',
                    h('span', { class: 'quality-stale-text', text: 'The lesson changed since this check.' }),
                    s.qualityCan('qualityRun') ? [' ', h('button', { type: 'button', class: 'ui-btn ui-btn-link quality-stale-run', 'data-action': 'quality-run-stale',
                        text: 'Check again', 'aria-label': 'Check the lesson again', disabled: !!s.qualityBusy, onclick: run })] : null));
            }
            const message = s.qualityMessage ? s.qualityMessage + (s.qualityError && this.debug && s.qualityErrorDetail ? ` (${s.qualityErrorDetail})` : '') : '';
            box.appendChild(h('p', { class: 'quality-state', role: 'status', 'aria-live': 'polite', 'data-kind': s.qualityError ? 'error' : 'info', text: message }));
            if (!report) return;

            const details = h('div', { class: 'quality-details', id: 'review-quality-details', hidden: this.qualityCollapsed && report.attention > 0 });
            details.appendChild(this.renderQualityAreas(report));
            const shown = report.issues.filter(f => this.debug || f.severity !== 'info');
            if (shown.length) {
                // grouped by scene, the whole lesson first; each group keeps the report's order (most serious first)
                const groups = new Map();
                shown.forEach(f => { const key = f.scene === null ? -1 : f.scene; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(f); });
                const list = h('div', { class: 'quality-issues', 'aria-label': 'What the check found' });
                let left = Q_MAX_SHOWN;
                [...groups.keys()].sort((a, b) => a - b).forEach(key => {
                    if (left <= 0) return;
                    const mine = groups.get(key).slice(0, left);
                    left -= mine.length;
                    const title = key < 0 ? 'Whole lesson' : this.qualitySceneTitle(key);
                    list.appendChild(h('div', { class: 'quality-group', 'data-scene': key < 0 ? 'lesson' : String(key) },
                        h('p', { class: 'quality-group-title', text: title }),
                        h('ul', { class: 'quality-issue-list' }, mine.map(f => this.renderQualityIssue(f, { stale, showScene: true })))));
                });
                if (shown.length > Q_MAX_SHOWN) list.appendChild(h('p', { class: 'review-note quality-more',
                    text: `…and ${qCount(shown.length - Q_MAX_SHOWN, 'more thing', 'more things')} (each scene's own list has them all).` }));
                details.appendChild(list);
            }
            if (report.limitations.length) {
                details.appendChild(h('div', { class: 'quality-limitations' },
                    report.limitations.map(text => h('p', { class: 'review-note quality-limitation', text }))));
            }
            if (this.debug) {
                details.appendChild(h('pre', { class: 'review-debug quality-debug-report', text: JSON.stringify({
                    rules: qText(report.raw.rules, 80) || null, version: Number.isInteger(report.raw.version) ? report.raw.version : null,
                    fingerprint: qText(report.raw.fingerprint, 64) || null, status: report.status }, null, 2) }));
            }
            box.appendChild(details);
        }

        // The areas checked: those with something to look at, each with its mark and how many; the rest folded into one line
        renderQualityAreas(report) {
            const h = (...args) => el(this.doc, ...args);
            const wrap = h('div', { class: 'quality-dimensions' });
            if (!report.dimensions.length) return wrap;
            const fine = report.dimensions.filter(d => d.status === 'pass');
            const list = h('ul', { class: 'quality-dimension-list', 'aria-label': 'Areas checked' },
                report.dimensions.map(d => {
                    const mark = d.status === 'pass' ? '✓' : qMark(d.status);
                    return h('li', { class: 'quality-dimension', 'data-dimension': d.key, 'data-status': d.status, hidden: d.status === 'pass' && !this.qualityAllAreas },
                        h('span', { class: 'quality-mark', 'aria-hidden': 'true', text: mark }),
                        h('span', { class: 'quality-dimension-label', text: ` ${d.label}` }),
                        d.status !== 'pass' && d.count ? h('span', { class: 'quality-dimension-count', text: ` · ${qCount(d.count, 'thing', 'things')} to look at` })
                            : d.status === 'pass' ? h('span', { class: 'quality-dimension-count', text: ' · looks fine' }) : null);
                }));
            wrap.appendChild(list);
            if (fine.length) {
                const all = fine.length === report.dimensions.length;
                wrap.appendChild(h('p', { class: 'quality-dimensions-fine' },
                    h('span', { text: `✓ ${all ? `All ${fine.length} areas look fine` : `${qCount(fine.length, 'area looks', 'areas look')} fine`}` }),
                    ' ',
                    h('button', { type: 'button', class: 'quality-areas', 'data-action': 'quality-areas', 'aria-expanded': String(this.qualityAllAreas),
                        text: this.qualityAllAreas ? 'Hide them' : 'Show them', onclick: () => { this.qualityAllAreas = !this.qualityAllAreas; this.renderQuality(); } })));
            }
            return wrap;
        }

        qualitySceneTitle(index) {
            const scene = this.session.slides && this.session.slides[index];
            const title = scene && qText(scene.title, 60);
            return `Scene ${index + 1}${title ? ` · ${title}` : ''}`;
        }

        // One finding: its severity as an icon AND words, the message, "Show scene", the fix the page offers; debug: the facts
        renderQualityIssue(f, { stale = false, showScene = false } = {}) {
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            const sev = QUALITY_SEVERITY[f.severity];
            const busy = !!s.qualityBusy || s.busy;
            const later = stale ? 'Check again first: the lesson changed since this check' : null;
            const actions = [];
            if (showScene && f.scene !== null && this.canShowQualityScene(f.scene)) {
                actions.push(h('button', { type: 'button', class: 'quality-show', 'data-action': 'quality-show', 'data-scene': String(f.scene), text: 'Show scene',
                    'aria-label': `Show scene ${f.scene + 1}`, title: `Show scene ${f.scene + 1} in the list`,
                    onclick: () => this.showQualityScene(f.scene, f.dimension) }));
            }
            if ((f.fix === 'auto' || f.fix === 'replan') && s.qualityCan('qualityRepair')) {
                const suggested = f.fix === 'replan';
                actions.push(h('button', { type: 'button', class: buttonClass(false) + ' quality-fix', 'data-action': 'quality-repair',
                    text: suggested ? (f.label || 'Re-plan the scene') : 'Fix automatically', disabled: busy || stale,
                    title: later || (suggested ? 'Plans the scene again: its approval will be asked again (nothing is generated)'
                        : 'Plans the scene again (nothing is generated; your approvals are kept)'),
                    onclick: () => this.qualityAct(s.repairQuality(f), f.scenes) }));
            } else if (f.fix === 'apply' && s.qualityCan('qualityApply')) {
                actions.push(h('button', { type: 'button', class: buttonClass(false) + ' quality-fix', 'data-action': 'quality-apply',
                    text: f.label || 'Apply suggestion', disabled: busy || stale,
                    title: later || "Changes the scene through Visual Review: it becomes your choice (nothing is generated)",
                    onclick: () => this.qualityAct(s.applyQuality(f), f.scenes) }));
            }
            let debug = null;
            if (this.debug) {
                let evidence = '{}';
                try { evidence = JSON.stringify(qPlain(f.raw.evidence) ? f.raw.evidence : {}, null, 2) || '{}'; } catch (e) { evidence = '{}'; }
                debug = h('div', { class: 'quality-debug' },
                    h('p', { class: 'quality-debug-facts', text: [`rule: ${f.rule || '?'}`, `dimension: ${f.dimension || '?'}`, `severity: ${f.severity}`,
                        `repair: ${f.kind}${f.klass ? ` (${f.klass})` : ''}${f.type !== 'none' ? ` · ${f.type}` : ''}`].join(' · ') }),
                    h('pre', { class: 'review-debug quality-evidence', text: evidence.slice(0, 4000) }));
            }
            return h('li', { class: 'quality-issue', 'data-severity': f.severity, 'data-issue': f.id },
                h('span', { class: 'quality-severity', 'data-severity': f.severity },
                    h('span', { class: 'quality-mark', 'aria-hidden': 'true', text: sev.icon }), ` ${sev.label}`),
                h('span', { class: 'quality-message', text: ` ${f.message}` }),
                actions.length ? h('span', { class: 'quality-actions' }, actions) : null,
                debug);
        }

        // The scene's own findings in its composition inspector (no "Show scene": it is the scene shown)
        renderSceneQuality(item) {
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            if (!item || (!s.qualityCan('quality') && !s.qualityCan('qualityRun'))) return null;
            const report = s.qualityReport();
            const entry = qualityByScene(report, this.debug).get(item.sceneIndex);
            if (!entry || !entry.issues.length) return null;
            const stale = s.qualityIsStale();
            const lead = entry.count ? `Quality: ${qCount(entry.count, 'thing', 'things')} to look at in this scene` : 'Quality: only notes in this scene';
            return h('div', { class: 'review-quality', 'data-severity': entry.worst || 'info' },
                h('p', { class: 'review-quality-auto', text: lead + (stale ? ' (from an earlier check: the lesson changed since)' : '') }),
                h('ul', { class: 'quality-issue-list' }, entry.issues.map(f => this.renderQualityIssue(f, { stale }))));
        }

        canShowQualityScene(index) {
            const s = this.session;
            return s.all.some(item => item.sceneIndex === index) || s.qualityCan('selectScene');
        }

        // "Show scene": the scene's item in the list (the composition, or the presenter / the visual for those findings); a
        // filter that hides it gives way to "All"; a scene with nothing to review is left to the page (selectScene)
        showQualityScene(index, dimension) {
            const s = this.session;
            const want = { presenter: ['presenter'], media: ['main', 'side'] }[dimension] || ['composition'];
            const find = items => {
                const mine = items.map((item, i) => ({ item, i })).filter(x => x.item.sceneIndex === index);
                return mine.length ? (mine.find(x => want.includes(x.item.slot)) || mine[0]).i : -1;
            };
            let at = find(s.items);
            if (at < 0 && find(s.all) >= 0) {
                if (!s.setFilter('all')) return false;
                at = find(s.items);
            }
            if (at >= 0) {
                if (!s.select(at)) return false;
                this.changeOpen = false;
                this.directionOpen = null;
                this.render();
                if (this.detail && typeof this.detail.scrollIntoView === 'function') {
                    try {
                        this.detail.scrollIntoView({ block: 'nearest' });
                        if (typeof this.detail.scrollTop === 'number') this.detail.scrollTop = 0;
                        const block = typeof this.detail.querySelector === 'function' ? this.detail.querySelector('div.review-quality') : null;
                        if (block && typeof block.scrollIntoView === 'function') block.scrollIntoView({ block: 'nearest' });
                    } catch (e) { /* not essential */ }
                }
                return true;
            }
            if (!s.qualityCan('selectScene')) return false;
            try { s.composition.selectScene(index); } catch (e) { return false; }
            return true;
        }

        // A quality action: drawn while it runs ("Checking…", "Fixing…"), drawn again after; a change is saved like any decision
        async qualityAct(promise, scenes) {
            this.render();
            const result = await promise;
            this.render();
            if (result) scenes.forEach(i => this.onChange(i));
            return result;
        }

        // The scene's style summary from the adapter (Phase 17): null for Classic, an older plan, an older page or a failure (the
        // style is information here; it never stops the inspector)
        styleSummaryOf(item) {
            const adapter = this.session.composition;
            if (!item || !adapter || typeof adapter.styleSummary !== 'function') return null;
            let summary = null;
            try { summary = adapter.styleSummary(item.scene, item.plan); } catch (e) { summary = null; }
            return summary && typeof summary === 'object' && !Array.isArray(summary) ? summary : null;
        }

        // The lesson's style on this scene (Phase 17): its family and version, the lesson's and the scene's own choices in plain
        // words, what accessibility adjusted, and a few of its colours. Read-only: the scene's accent and background are changed
        // in the composition's "Change" form, the lesson's style in the cinematic settings. Built with textContent only.
        renderStyle(item, summary) {
            const h = (...args) => el(this.doc, ...args);
            const sum = summary === undefined ? this.styleSummaryOf(item) : summary;
            if (!sum || typeof sum !== 'object' || Array.isArray(sum)) return null;
            const label = typeof sum.label === 'string' && sum.label.trim() ? sum.label.trim().slice(0, 80) : "The lesson's style";
            const v = typeof sum.version === 'number' || (typeof sum.version === 'string' && /^\d{1,4}$/.test(sum.version)) ? Number(sum.version) : NaN;
            const version = Number.isInteger(v) && v > 0 ? ` (v${v})` : '';
            const legacy = sum.legacy === true;
            const entries = list => (Array.isArray(list) ? list : (list && typeof list === 'object' ? Object.entries(list) : []))
                .filter(p => Array.isArray(p) && p.length >= 2 && styleScalar(p[0]) && styleScalar(p[1]) && p[1] !== 'default');
            const choice = (scope, suffix) => ([key, value]) => h('li', { 'data-scope': scope, 'data-key': STYLE_WORD.test(String(key)) ? String(key) : null,
                text: `${styleKeyText(key)}: ${styleText(value)}${suffix}` });
            const choices = entries(sum.overrides).slice(0, 12).map(choice('lesson', ''))
                .concat(entries(sum.sceneOverrides).slice(0, 4).map(choice('scene', ' (this scene only)')));
            const adjustments = [...new Set((Array.isArray(sum.adjustments) ? sum.adjustments : []).filter(a => typeof a === 'string' && a.trim())
                .map(a => a.trim().slice(0, 160)))].slice(0, 8);
            const colours = (Array.isArray(sum.swatch) ? sum.swatch : []).filter(c => typeof c === 'string' && STYLE_HEX.test(c.trim()))
                .map(c => c.trim()).slice(0, 8);
            const swatch = colours.length ? h('div', { class: 'review-style-swatch', role: 'img', 'aria-label': `Colours of the ${label} style` },
                colours.map(c => { const chip = h('span', { 'data-colour': c }); chip.style.backgroundColor = c; return chip; })) : null;
            return h('div', { class: 'review-style',
                'data-style': typeof sum.family === 'string' && STYLE_WORD.test(sum.family) ? sum.family : null,
                'data-tone': ['dark', 'light'].includes(sum.tone) ? sum.tone : null,
                'data-fingerprint': typeof sum.fingerprint === 'string' && /^[0-9a-f]{1,64}$/i.test(sum.fingerprint) ? sum.fingerprint : null,
                'data-legacy': legacy ? 'true' : null },
                h('p', { class: 'review-style-auto', text: `Style: ${label}${version}${legacy ? " · the lesson's original look" : ''}` }),
                swatch,
                choices.length ? h('ul', { class: 'review-style-choices', 'aria-label': 'Style choices' }, choices) : null,
                adjustments.length ? h('div', { class: 'review-style-notes' },
                    adjustments.map(a => h('p', { class: 'review-note review-style-note', text: `Kept readable: ${a}` }))) : null);
        }

        // The scene's synchronization (Phase 16): the timing source, the key moments ("0.6 s — the camera leans in", with what
        // anchors each one), the attention flow and short notes, in plain words. Read-only: nothing here edits the timing; it
        // shows the plan the scene has now (a new direction, composition or narration brings a new one).
        renderSync(sync) {
            const h = (...args) => el(this.doc, ...args);
            const moments = (sync.keyMoments || sync.moments || []).slice(0, 8);
            const more = (sync.moments || []).length - moments.length;
            return h('div', { class: 'review-sync', 'data-source': sync.source },
                h('p', { class: 'review-sync-auto', text: `Synchronization: ${sync.timing}` }),
                moments.length
                    ? h('ul', { class: 'review-sync-moments', 'aria-label': 'Key moments' }, moments.map(m => h('li', { 'data-type': m.type, 'data-layer': m.layer },
                        h('span', { class: 'review-sync-what', text: `${m.when ? `${m.when} — ` : ''}${m.what}` }),
                        m.anchor ? h('span', { class: 'review-sync-anchor', text: ` · ${m.anchor}` }) : null)))
                    : h('p', { class: 'review-note review-sync-empty', text: 'Nothing in this scene is timed to the narration.' }),
                more > 0 ? h('p', { class: 'review-note review-sync-more', text: `…and ${more} smaller moment${more === 1 ? '' : 's'}.` }) : null,
                sync.attention ? h('p', { class: 'review-sync-attention', text: `Attention: ${sync.attention}` }) : null,
                (sync.notes || []).map(text => h('p', { class: 'review-note review-sync-note', text })),
                sync.endHold ? h('p', { class: 'review-note review-sync-hold', text: sync.endHold }) : null,
                sync.ai ? h('p', { class: 'review-note review-sync-ai', text: sync.ai }) : null);
        }

        // The scene's visual direction (Phase 15): how the scene teaches, in plain words (strategy, goal, what teaches, the
        // presenter's role, motion, camera, the learner's focus, short reasons), with simple changes and "Regenerate direction".
        // Never a score or model reasoning; nothing here generates a picture, clip or presenter.
        renderDirection(item, d) {
            const h = (...args) => el(this.doc, ...args);
            const s = this.session;
            const adapter = s.composition;
            const button = (text, handler, extra = {}) => h('button', { type: 'button', class: buttonClass(extra.primary),
                text, disabled: s.busy || extra.disabled, title: extra.title, 'data-action': extra.action, onclick: handler });
            const run = async promise => { this.render(); const result = await promise; this.directionOpen = null; this.render(); if (result) this.onChange(item.sceneIndex); };
            const open = this.directionOpen === item.sceneIndex;
            const review = item.scene.visual_review && item.scene.visual_review.direction;
            const chosen = !!(review && review.status === 'changed') || d.locked.length > 0;
            const block = h('div', { class: 'review-direction', 'data-source': d.source },
                h('p', { class: 'review-direction-auto', text: `Visual direction: ${d.label}` }),
                h('dl', { class: 'asset-meta review-direction-facts' },  // its own list: "the inspector's facts" stay the composition's
                    d.facts.flatMap(([dt, dd, key]) => [h('dt', { text: dt }), h('dd', { class: 'review-direction-fact', 'data-fact': key, text: dd })])),
                d.reasons.length ? h('ul', { class: 'review-direction-reasons', 'aria-label': 'Why this direction' }, d.reasons.map(r => h('li', { text: r }))) : null,
                d.notes.map(text => h('p', { class: 'review-note review-direction-note', text })),
                d.ai ? h('p', { class: 'review-note review-direction-ai', text: d.ai }) : null);
            const actions = h('div', { class: 'asset-actions review-actions review-direction-actions' });
            if (adapter.reviewDirection) {
                actions.append(button(open ? 'Change direction ▴' : 'Change direction ▾', () => { this.directionOpen = open ? null : item.sceneIndex; this.renderDetail(); },
                    { action: 'direction-change' }));
                if (chosen) actions.append(button('Back to automatic direction', () => run(s.decideDirection('reset')), { action: 'direction-reset' }));
            }
            if (adapter.regenerateDirection) actions.append(button('Regenerate direction', () => run(s.regenerateDirection()), { action: 'direction-regenerate',
                title: 'Decides the visual direction again (no picture, clip or presenter is generated)' }));
            if (actions.childNodes.length) block.appendChild(actions);
            if (!adapter.reviewDirection) return block;
            const change = h('div', { class: 'review-change review-direction-change', hidden: !open });
            const options = (typeof adapter.directionOptions === 'function' ? adapter.directionOptions() : adapter.directionOptions) || {};
            const picks = {};
            [['strategy', 'Way of teaching'], ['primary_visual', 'What teaches'], ['presenter_role', 'Presenter'], ['camera_intent', 'Camera'],
                ['motion_intent', 'Motion'], ['prefer', 'Preference']].forEach(([key, label]) => {
                const choices = options[key];
                if (!choices) return;
                const mine = d.locked.includes(key); // only the user's own choices can be given back ("Automatic")
                const select = h('select', { id: `direction-${key}`, class: 'neon-input ui-focusable', 'data-key': key, 'aria-label': label },
                    h('option', { value: '', text: `${label}: as it is${mine ? ' (your choice)' : ' (automatic)'}` }),
                    mine ? h('option', { value: 'auto', text: `${label}: Automatic` }) : null,
                    Object.entries(choices).map(([value, text]) => h('option', { value, text: `${label}: ${text}` })));
                picks[key] = select;
                change.appendChild(select);
            });
            change.appendChild(button('Apply', () => {
                const overrides = {};
                Object.entries(picks).forEach(([key, select]) => { if (select.value && select.value !== d.current[key]) overrides[key] = select.value; });
                if (!Object.keys(overrides).length) { s.message = 'Nothing to change: pick a new value first.'; this.render(); return; }
                run(s.decideDirection('change', { overrides }));
            }, { primary: true, action: 'direction-apply' }));
            block.appendChild(change);
            return block;
        }

        openPicker(item) {
            this.pickAsset({
                kinds: item.slot === 'main' ? ['video'] : ['image', 'video'],
                title: `Choose a visual for scene ${item.sceneIndex + 1}`,
                onPick: async asset => {
                    this.render();
                    const result = await this.session.chooseAsset(asset);
                    this.changeOpen = false;
                    this.render();
                    if (result) this.onChange(item.sceneIndex);
                }
            });
        }
    }

    // ---- a narration edited in the script editor (Phase 16) ------------------------------------------------------------
    // The scenes whose narration differs between two copies of the lesson (scenes or their narration texts; a scene added or
    // removed counts as changed)
    function narrationChanges(before, after) {
        const part = (x, key) => (x && typeof x === 'object' ? (typeof x[key] === 'string' ? x[key] : '') : (key === 'narration' && typeof x === 'string' ? x : ''));
        const a = Array.isArray(before) ? before : [];
        const b = Array.isArray(after) ? after : [];
        const changed = [];
        for (let i = 0; i < Math.max(a.length, b.length); i++) {
            // the board counts too when both sides carry it (a board edit moves what the moments point at)
            const boards = a[i] && typeof a[i] === 'object' && 'html' in a[i] && b[i] && typeof b[i] === 'object';
            if (part(a[i], 'narration') !== part(b[i], 'narration') || (boards && part(a[i], 'html') !== part(b[i], 'html'))) changed.push(i);
        }
        return changed;
    }

    // After a script edit, the timing follows the new narration: a cinematic lesson whose narration changed is planned again
    // (plan() = index.html's planLessonCinematic: every scene is recomposed and its synchronization recompiled; the direction,
    // composition and media come back the same, they are deterministic), then the current scene is drawn again (render()).
    // A classic lesson, or an edit that left every narration as it was: nothing is asked.
    async function retimeAfterNarrationEdit({ cinematic = false, before = [], after = [], plan = null, render = null } = {}) {
        const changed = narrationChanges(before, after);
        if (!cinematic || !changed.length || typeof plan !== 'function') return { replanned: false, changed };
        await plan();
        if (typeof render === 'function') render();
        return { replanned: true, changed };
    }

    class ReviewApi {
        // body -> POST /api/visuals/review with the lesson, the page's copy of the scene and the AI setting
        constructor({ fetch, projectId, sceneAt, allowAi = () => true }) {
            this.fetchFn = fetch;
            this.projectId = projectId;
            this.sceneAt = sceneAt;
            this.allowAi = allowAi;
        }

        async review(body) {
            const projectId = await this.projectId();
            let res;
            try {
                res = await this.fetchFn('/api/visuals/review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ project_id: projectId, scene: this.sceneAt(body.scene_index), allow_ai_generation: this.allowAi(), ...body })
                });
            } catch (e) {
                throw Object.assign(new Error('the server could not be reached'), { status: 0 });
            }
            const data = await res.json().catch(() => null);
            if (!res.ok) {
                const detail = data && data.detail;
                throw Object.assign(new Error(typeof detail === 'string' ? detail : `error ${res.status}`), { status: res.status });
            }
            return data;
        }
    }

    return { FILTERS, QUALITY_SEVERITY, STATUS, ReviewApi, ReviewSession, VisualReviewPanel, compositionItem, filterItems, generationTarget, narrationChanges,
        previewKind, purposeText, qualityByScene, qualityHeadline, qualityIssue, qualityReport, reasonText, retimeAfterNarrationEdit, reviewItems, sourceText,
        statusText, summarize, summaryText };
});
