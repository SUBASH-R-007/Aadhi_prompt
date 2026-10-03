/*
 * Advanced Video Editor (Phase 19): the editing model behind the editor workspace.
 *
 * The editor is a refinement layer over the lesson the page already plays (scratchpad/phase19_contract.md): ONE lesson
 * model (the page's live `slides` array, mutated IN PLACE: spliced, never replaced), ONE renderer (the live page), ONE
 * planner. This module holds no DOM: it edits the scenes and the lesson-level editor data (payload.editor), records
 * every edit as an undoable command ({type, scene_id, before, after, at}: plain JSON, never media), groups a drag or a
 * typing burst into one undo step, works out the timeline (the same speaking pace as cinematic.estimate_seconds), and
 * keeps the unsaved commands so they can be replayed by scene_id onto a newer lesson after a stale save (409).
 *
 * What it does not do itself (the page does, through the channels it already has):
 *   composition overrides (template, camera, presenter size ...) are returned as {kind: 'composition', scene_id,
 *   overrides} for the existing /api/cinematic/review route; re-planning after a structural edit and the Phase 16
 *   retime after a narration edit are flagged on each result (structure / retime) for the page to run.
 *
 * Also here: Autosave (debounced PUT /api/editor with hold/release for drags, one save at a time, 409 reload + replay
 * + one retry) and a per-lesson localStorage draft of the unsaved commands (recovered after a refresh).
 *
 * Loaded as a classic <script> (window.AadhiEditor) and as a CommonJS module by tests/editor.test.js.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.AadhiEditor = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const VERSION = 1;
    const ID_RE = /^s-[0-9a-f]{12}$/;
    const ASSET_RE = /^[0-9a-f]{32}$/;
    const MAX_SCENES = 200;
    const UNDO_LIMIT = 200;
    const JOURNAL_LIMIT = 2000;
    const COALESCE_MS = 1200;   // edits of the same field closer than this are one undo step (a typing burst, a slider)
    const SNAP_SECONDS = 0.3;
    const HOLD_MIN = 0.5;
    const HOLD_MAX = 600;
    // cinematic.py estimate_seconds: words at a speaking pace plus the pauses, at least 4 s (an empty narration: 5 s)
    const WORDS_PER_SECOND = 2.6;
    const PAUSE_DEFAULT = 1.5;
    const MIN_SCENE = 4;
    const EMPTY_SCENE = 5;
    const MAX_SCENE = 600;
    const PAUSE_RE = /\[PAUSE(?::(\d+(?:\.\d+)?))?\]/gi;
    const WORD_RE = /[\p{L}\p{N}_']+/gu;  // Python's [\w']+ (str.isalnum letters and numbers, underscore, apostrophe)
    const TRANSITION_SECONDS = Object.freeze({ cut: 0, fade: 0.5, crossfade: 0.6, slide: 0.55, soft_fade: 0.8, zoom: 0.6, wipe: 0.6 });
    const TEXT_LIMITS = Object.freeze({ title: 300, subtitle: 600, narration: 20000, html: 200000, label: 80, labels: 6 });
    const SOURCE_FIELDS = Object.freeze(['title', 'subtitle', 'narration', 'html', 'labels']);
    const TEXT_FIELDS = Object.freeze(['title', 'subtitle', 'html', 'narration']);
    const FIELD_LABELS = Object.freeze({ title: 'title', subtitle: 'subtitle', html: 'board text', narration: 'narration', labels: 'labels' });
    // The composition review's vocabulary (cinematic.check_overrides; "auto" gives the choice back to the composer)
    const OVERRIDE_VALUES = Object.freeze({
        template: Object.freeze(['presenter_intro', 'presenter_explanation', 'presenter_plus_visual', 'visual_focus', 'formula_focus',
            'code_focus', 'diagram_focus', 'comparison', 'quiz', 'summary']),
        camera: Object.freeze(['static', 'slow_zoom_in', 'slow_zoom_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down', 'focus']),
        transition: Object.freeze(['fade', 'crossfade', 'slide', 'cut', 'soft_fade', 'zoom', 'wipe']),
        motion: Object.freeze(['none', 'subtle', 'moderate']),
        presenter_position: Object.freeze(['left', 'right', 'hidden']),
        presenter_size: Object.freeze(['dominant', 'secondary', 'small', 'hidden']),
        visual_position: Object.freeze(['left', 'right']),
        visual_size: Object.freeze(['dominant', 'secondary', 'side_panel', 'hidden']),
        background: Object.freeze(['auto', 'studio', 'gradient', 'solid', 'image', 'video', 'ai']),
        style_accent: Object.freeze(['default', 'gold', 'coral', 'crimson', 'teal', 'sky', 'violet', 'emerald']),
        style_background: Object.freeze(['default', 'plain', 'subtle', 'rich'])
    });
    const OVERRIDE_LABELS = Object.freeze({ template: 'layout', camera: 'camera', transition: 'transition', motion: 'motion',
        presenter_position: 'presenter side', presenter_size: 'presenter size', visual_position: 'visual side', visual_size: 'visual size',
        background: 'background', style_accent: 'accent colour', style_background: 'background density' });
    const APPROVAL_STATES = ['pending', 'approved', 'changed', 'removed'];
    const LOCKED = 'The lesson is still generating: scenes can be moved, added or deleted once it finishes.';
    const DRAFT_PREFIX = 'aadhi.editor.draft.';
    const DRAFT_MAX_CHARS = 2000000;

    // ---- small helpers --------------------------------------------------------------------------------------------

    const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
    const has = (o, k) => isObj(o) && Object.prototype.hasOwnProperty.call(o, k);
    const round2 = x => Math.round(x * 100) / 100;
    const clampInt = (n, lo, hi) => Math.max(lo, Math.min(hi, Math.trunc(n)));
    const jsonCopy = v => (v === undefined ? null : JSON.parse(JSON.stringify(v)));
    const sameJSON = (a, b) => JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
    const lowerFirst = s => (s ? s[0].toLowerCase() + s.slice(1) : s);

    function cleanStr(v, limit) {
        return typeof v === 'string' ? v.trim().slice(0, limit) : '';
    }

    function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    // ---- scene ids ------------------------------------------------------------------------------------------------

    function randomHex(bytes) {
        let values = null;
        try {
            const c = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
            if (c && typeof c.getRandomValues === 'function') values = c.getRandomValues(new Uint8Array(bytes));
        } catch (e) {
            values = null;
        }
        if (!values) values = Array.from({ length: bytes }, () => Math.floor(Math.random() * 256));
        return Array.from(values, b => b.toString(16).padStart(2, '0')).join('');
    }

    // "s-" + 12 lowercase hex (the contract's ^s-[0-9a-f]{12}$)
    function sceneId() {
        return 's-' + randomHex(6);
    }

    const validId = id => typeof id === 'string' && ID_RE.test(id);

    // Gives every scene a valid, unique scene_id IN PLACE (the first scene keeps a duplicated id); returns how many changed
    function ensureIds(scenes) {
        if (!Array.isArray(scenes)) return 0;
        const taken = new Set(scenes.filter(s => isObj(s) && validId(s.scene_id)).map(s => s.scene_id));
        const seen = new Set();
        let changed = 0;
        for (const scene of scenes) {
            if (!isObj(scene)) continue;
            if (!validId(scene.scene_id) || seen.has(scene.scene_id)) {
                let id;
                do { id = sceneId(); } while (taken.has(id));
                scene.scene_id = id;
                taken.add(id);
                changed += 1;
            }
            seen.add(scene.scene_id);
        }
        return changed;
    }

    // ---- timing (mirrors cinematic.py estimate_seconds) -----------------------------------------------------------

    function spokenWords(text) {
        return (String(text || '').replace(PAUSE_RE, ' ').split('[SYNC]').join(' ').match(WORD_RE) || []).length;
    }

    function pauseSeconds(text) {
        let total = 0;
        for (const m of String(text || '').matchAll(PAUSE_RE)) total += m[1] ? parseFloat(m[1]) : PAUSE_DEFAULT;
        return total;
    }

    function estimateSeconds(narration) {
        const text = typeof narration === 'string' ? narration : (narration === null || narration === undefined ? '' : String(narration));
        if (!text.trim()) return EMPTY_SCENE;
        return round2(Math.max(MIN_SCENE, Math.min(MAX_SCENE, spokenWords(text) / WORDS_PER_SECOND + pauseSeconds(text) + 0.8)));
    }

    const editOf = scene => (isObj(scene) && isObj(scene.edit) ? scene.edit : {});
    const isHidden = scene => editOf(scene).hidden === true;
    const validHold = v => typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= HOLD_MAX;

    // How long the scene plays (without its transition): the narration (or the plan's own length when the scene has none),
    // held at least min_seconds; a muted scene holds min_seconds, else 5 s
    function playSeconds(scene) {
        const edit = editOf(scene);
        const min = validHold(edit.min_seconds) ? edit.min_seconds : 0;
        let base;
        if (edit.narration_muted === true) {
            base = min ? 0 : EMPTY_SCENE;
        } else {
            const text = isObj(scene) && typeof scene.narration === 'string' ? scene.narration : '';
            const plan = isObj(scene) && isObj(scene.cinematic_plan) ? scene.cinematic_plan : null;
            if (text.trim()) base = estimateSeconds(text);
            else if (plan && typeof plan.duration === 'number' && plan.duration > 0 && plan.duration <= MAX_SCENE) base = plan.duration;
            else base = EMPTY_SCENE;
        }
        return round2(Math.max(min, base));
    }

    // The transition into the scene: the plan's own, else the lesson's (cinematic mode), else none (classic)
    function transitionOf(scene, settings) {
        const plan = isObj(scene) && isObj(scene.cinematic_plan) ? scene.cinematic_plan : null;
        const t = plan && isObj(plan.transition) ? plan.transition : null;
        if (t && typeof t.duration === 'number' && Number.isFinite(t.duration)) {
            return { kind: typeof t.in === 'string' && has(TRANSITION_SECONDS, t.in) ? t.in : 'fade', seconds: Math.max(0, Math.min(3, t.duration)) };
        }
        if (isObj(settings) && settings.mode === 'cinematic') {
            const kind = has(TRANSITION_SECONDS, settings.transitions) ? settings.transitions : 'fade';
            return { kind, seconds: TRANSITION_SECONDS[kind] };
        }
        return { kind: 'cut', seconds: 0 };
    }

    // ---- scene fields ---------------------------------------------------------------------------------------------

    function validAt(at) {
        return (typeof at === 'number' && Number.isFinite(at) && at >= 0 && at <= MAX_SCENE)
            || (isObj(at) && Number.isInteger(at.sync) && at.sync >= 0 && at.sync <= 1000);
    }

    // Labels: up to 6 strings (or {text, at} items, kept as they are), each at most 80 characters; null when not a list
    function cleanLabels(list) {
        if (!Array.isArray(list)) return null;
        const out = [];
        for (const item of list) {
            if (out.length >= TEXT_LIMITS.labels) break;
            if (typeof item === 'string') {
                const text = cleanStr(item, TEXT_LIMITS.label);
                if (text) out.push(text);
            } else if (isObj(item) && typeof item.text === 'string') {
                const text = cleanStr(item.text, TEXT_LIMITS.label);
                if (text) out.push(validAt(item.at) ? { text, at: jsonCopy(item.at) } : { text });
            }
        }
        return out;
    }

    // A field value as the model keeps it: text (bounded) or null (absent); labels: a cleaned list. undefined: not valid.
    function cleanFieldValue(field, v) {
        if (field === 'labels') return v === null ? [] : (cleanLabels(v) || undefined);
        if (v === null) return null;
        return typeof v === 'string' ? v.slice(0, TEXT_LIMITS[field]) : undefined;
    }

    function getField(scene, field) {
        if (field === 'labels') {
            const comp = isObj(scene.composition) ? scene.composition : {};
            return cleanLabels(comp.labels) || [];
        }
        return typeof scene[field] === 'string' ? scene[field] : null;
    }

    function setFieldValue(scene, field, v) {
        if (field === 'labels') {
            const list = Array.isArray(v) ? v : [];
            if (!list.length) {
                if (isObj(scene.composition)) {
                    delete scene.composition.labels;
                    if (!Object.keys(scene.composition).length) delete scene.composition;
                }
            } else {
                if (!isObj(scene.composition)) scene.composition = {};
                scene.composition.labels = jsonCopy(list);
            }
            return;
        }
        if (v === null || (v === '' && field === 'subtitle')) delete scene[field];
        else scene[field] = v;
    }

    function eqField(field, a, b) {
        if (field === 'labels') return sameJSON(cleanLabels(a) || [], cleanLabels(b) || []);
        return (typeof a === 'string' ? a : '') === (typeof b === 'string' ? b : '');
    }

    // The generated value kept in edit.original (bounded strings; labels a cleaned list)
    function boundOriginal(field, v) {
        if (field === 'labels') return cleanLabels(v) || [];
        return typeof v === 'string' ? v.slice(0, TEXT_LIMITS[field]) : '';
    }

    function ensureEdit(scene) {
        if (!isObj(scene.edit)) scene.edit = {};
        return scene.edit;
    }

    function tidyEdit(scene) {
        if (!isObj(scene.edit)) return;
        if (has(scene.edit, 'original') && (!isObj(scene.edit.original) || !Object.keys(scene.edit.original).length)) delete scene.edit.original;
        if (!Object.keys(scene.edit).length) delete scene.edit;
    }

    // A copy of a scene that is a NEW scene: its review decisions are not inherited (the plans themselves are kept)
    function freshCopy(scene) {
        const copy = jsonCopy(scene);
        delete copy.visual_review;
        for (const key of ['cinematic_plan', 'presenter_plan', 'visual_direction']) {
            if (isObj(copy[key])) {
                delete copy[key].review_status;
                delete copy[key].review_stale;
            }
        }
        if (isObj(copy.visual_plan)) {
            for (const slot of Object.keys(copy.visual_plan)) {
                if (isObj(copy.visual_plan[slot])) {
                    delete copy.visual_plan[slot].review_status;
                    delete copy.visual_plan[slot].review_stale;
                }
            }
        }
        return copy;
    }

    function approvalsOf(scene) {
        const reviews = isObj(scene) && isObj(scene.visual_review) ? scene.visual_review : {};
        const state = v => (APPROVAL_STATES.includes(v) ? v : null);
        const reviewed = slot => (isObj(reviews[slot]) ? state(reviews[slot].status) : null);
        const plan = isObj(scene) && isObj(scene.cinematic_plan) ? scene.cinematic_plan : null;
        const visual = isObj(scene) && isObj(scene.visual_plan) ? scene.visual_plan : {};
        const slot = name => (isObj(visual[name]) ? state(visual[name].review_status) || reviewed(name) || 'pending' : null);
        const presenter = isObj(scene) && isObj(scene.presenter_plan) && scene.presenter_plan.presenter_id ? reviewed('presenter') || 'pending' : reviewed('presenter');
        return {
            composition: plan && plan.template ? state(plan.review_status) || reviewed('composition') || 'pending' : reviewed('composition'),
            main: slot('main'),
            side: slot('side'),
            presenter
        };
    }

    function currentOverride(scene, key) {
        const reviews = isObj(scene) && isObj(scene.visual_review) ? scene.visual_review : {};
        const review = isObj(reviews.composition) ? reviews.composition : null;
        if (review && ['approved', 'changed'].includes(review.status) && isObj(review.overrides) && typeof review.overrides[key] === 'string') {
            return review.overrides[key];
        }
        return 'auto';
    }

    // The positions (in the current order) that keep the generated order: the longest increasing run; the rest moved
    function movedIds(scenes, order) {
        const pos = new Map();
        (Array.isArray(order) ? order : []).forEach((id, i) => { if (validId(id) && !pos.has(id)) pos.set(id, i); });
        const seq = [];
        for (const scene of scenes) if (isObj(scene) && pos.has(scene.scene_id)) seq.push({ id: scene.scene_id, p: pos.get(scene.scene_id) });
        const tails = [];
        const prev = new Array(seq.length).fill(-1);
        for (let i = 0; i < seq.length; i++) {
            let lo = 0;
            let hi = tails.length;
            while (lo < hi) {
                const mid = (lo + hi) >> 1;
                if (seq[tails[mid]].p < seq[i].p) lo = mid + 1;
                else hi = mid;
            }
            if (lo > 0) prev[i] = tails[lo - 1];
            tails[lo] = i;
        }
        const keep = new Set();
        for (let k = tails.length ? tails[tails.length - 1] : -1; k >= 0; k = prev[k]) keep.add(seq[k].id);
        return new Set(seq.filter(x => !keep.has(x.id)).map(x => x.id));
    }

    // payload.editor kept valid: version 1, captions {visible}, generated_order set once. Returns true if the order was set.
    function normalizeEditor(editor, scenes) {
        editor.version = VERSION;
        if (has(editor, 'captions') && !(isObj(editor.captions) && typeof editor.captions.visible === 'boolean')) delete editor.captions;
        if (Array.isArray(editor.generated_order)) {
            const seen = new Set();
            editor.generated_order = editor.generated_order.filter(id => validId(id) && !seen.has(id) && seen.add(id)).slice(0, 1000);
            return false;
        }
        editor.generated_order = scenes.filter(s => isObj(s) && validId(s.scene_id)).map(s => s.scene_id);
        return true;
    }

    // ---- commands -------------------------------------------------------------------------------------------------

    const PRESENCE = ['insert', 'duplicate', 'split', 'remove'];
    const EDIT_KEYS = { hide: 'hidden', duration: 'min_seconds', mute: 'narration_muted', captions: 'captions' };
    const TYPES = {
        move: { structural: true, label: () => 'Move scene' },
        insert: { structural: true, label: c => (c.after ? 'Add scene' : 'Remove added scene') },
        duplicate: { structural: true, label: () => 'Duplicate scene' },
        split: { structural: true, label: () => 'Split scene' },
        remove: { structural: true, label: () => 'Delete scene' },
        hide: { label: c => (c.after ? 'Hide scene' : 'Show scene') },
        duration: { label: () => 'Change duration' },
        mute: { label: c => (c.after ? 'Mute narration' : 'Unmute narration') },
        captions: { label: c => (c.after === 'off' ? 'Hide scene captions' : 'Show scene captions') },
        lesson_captions: { label: c => (c.after === false ? 'Hide captions' : 'Show captions') },
        text: { label: c => `${c.revert ? 'Revert' : 'Edit'} ${FIELD_LABELS[c.field] || 'text'}` },
        override: { label: c => `Change ${OVERRIDE_LABELS[Object.keys(c.after || {})[0]] || 'composition'}` }
    };

    function labelOf(cmd) {
        const t = cmd && TYPES[cmd.type];
        return t ? t.label(cmd) : 'Change';
    }

    const retimes = cmd => cmd.type === 'split' || (cmd.type === 'text' && (cmd.field === 'narration' || cmd.field === 'html'));

    function validEditValue(type, v) {
        if (v === null) return true;
        if (type === 'hide' || type === 'mute' || type === 'lesson_captions') return typeof v === 'boolean';
        if (type === 'duration') return validHold(v);
        if (type === 'captions') return v === 'off';
        return false;
    }

    function validOverride(map) {
        if (!isObj(map)) return false;
        const keys = Object.keys(map);
        return keys.length > 0 && keys.every(k => has(OVERRIDE_VALUES, k) && (map[k] === 'auto' || OVERRIDE_VALUES[k].includes(map[k])));
    }

    // A command from outside (a draft, a pending list): a validated plain copy, or null
    function cleanCommand(raw) {
        if (!isObj(raw) || !has(TYPES, raw.type)) return null;
        const cmd = jsonCopy(raw);
        if (has(cmd, 'split') && (cmd.type !== 'text' || !validId(cmd.split))) delete cmd.split;
        if (cmd.type === 'lesson_captions') {
            cmd.scene_id = null;
            return validEditValue(cmd.type, cmd.before) && validEditValue(cmd.type, cmd.after) ? cmd : null;
        }
        if (!validId(cmd.scene_id)) return null;
        if (cmd.type === 'move') return isObj(cmd.after) && Number.isInteger(cmd.after.index) ? cmd : null;
        if (PRESENCE.includes(cmd.type)) {
            const ok = v => v === null || (isObj(v) && Number.isInteger(v.index) && isObj(v.scene) && v.scene.scene_id === cmd.scene_id);
            return ok(cmd.before) && ok(cmd.after) ? cmd : null;
        }
        if (cmd.type === 'text') {
            const ok = v => isObj(v) && cleanFieldValue(cmd.field, v.value) !== undefined && (!has(v, 'original') || cleanFieldValue(cmd.field, v.original) !== undefined);
            return SOURCE_FIELDS.includes(cmd.field) && ok(cmd.before) && ok(cmd.after) ? cmd : null;
        }
        if (cmd.type === 'override') return validOverride(cmd.after) && (cmd.before === null || validOverride(cmd.before)) ? cmd : null;
        return validEditValue(cmd.type, cmd.before) && validEditValue(cmd.type, cmd.after) ? cmd : null;
    }

    function invert(cmd, at) {
        return { ...cmd, before: cmd.after, after: cmd.before, at };
    }

    // Composition changes for the page to send (per scene, the final value of each key). via: 'do' (an edit), 'undo' (an undo
    // or a cancelled transaction) or 'redo': an undo back to a layout the user had approved lets the page restore that approval
    function compositionEffects(commands, side, via = 'do') {
        const list = side === 'before' ? [...commands].reverse() : commands;
        const byScene = new Map();
        for (const cmd of list) {
            if (cmd.type !== 'override' || !isObj(cmd[side])) continue;
            byScene.set(cmd.scene_id, { ...(byScene.get(cmd.scene_id) || {}), ...cmd[side] });
        }
        return [...byScene].map(([id, overrides]) => ({ kind: 'composition', scene_id: id, overrides, via }));
    }

    const fail = error => ({ ok: false, changed: false, error });

    // ---- the model ------------------------------------------------------------------------------------------------

    class EditorModel {
        // scenes: the page's live slides array (mutated in place); editor: payload.editor (kept in place, created when absent);
        // settings: the cinematic settings (or a function returning them) for transitions; revision: GET /api/editor's token;
        // structureLocked(): true while the lesson has active AI runs (order / membership edits wait)
        constructor({ scenes, editor = null, settings = null, revision = null, projectId = null, structureLocked = null, now = null,
            coalesceMs = COALESCE_MS } = {}) {
            if (!Array.isArray(scenes)) throw new TypeError('The editor needs the lesson\'s scenes array.');
            this.scenes = scenes;
            this.editor = isObj(editor) ? editor : {};
            this.settings = settings;
            this.revision = revision === undefined ? null : revision;
            this.projectId = projectId === undefined ? null : projectId;
            this.structureLocked = typeof structureLocked === 'function' ? structureLocked : () => false;
            this.now = typeof now === 'function' ? now : () => Date.now();
            this.coalesceMs = typeof coalesceMs === 'number' && coalesceMs >= 0 ? coalesceMs : COALESCE_MS;
            this.undoStack = [];
            this.redoStack = [];
            this.journal = [];          // the commands applied since the last save: [{seq, command}]
            this.journalTruncated = false;
            this.seq = 0;
            this.savedSeq = 0;
            this.tx = null;
            this.listeners = new Set();
            const ids = ensureIds(scenes);
            const order = normalizeEditor(this.editor, scenes);
            this.normalized = { ids, generated_order: order };
            if (ids || order) this.seq = 1;  // the new ids / the generated order still need saving
        }

        get dirty() { return this.seq !== this.savedSeq; }
        get inTransaction() { return !!this.tx; }

        subscribe(fn) {
            if (typeof fn !== 'function') return () => {};
            this.listeners.add(fn);
            return () => this.listeners.delete(fn);
        }

        _emit(event) {
            for (const fn of [...this.listeners]) {
                try { fn(event); } catch (e) { /* a listener's problem is not the model's */ }
            }
        }

        _settings() {
            try {
                return typeof this.settings === 'function' ? this.settings() : this.settings;
            } catch (e) {
                return null;
            }
        }

        indexOf(id) {
            if (!validId(id)) return -1;
            return this.scenes.findIndex(s => isObj(s) && s.scene_id === id);
        }

        scene(id) {
            const i = this.indexOf(id);
            return i >= 0 ? this.scenes[i] : null;
        }

        _newId() {
            const taken = new Set(this.scenes.filter(isObj).map(s => s.scene_id));
            let id;
            do { id = sceneId(); } while (taken.has(id));
            return id;
        }

        // ---- applying and recording ----

        _journal(cmd) {
            this.seq += 1;
            this.journal.push({ seq: this.seq, command: jsonCopy(cmd) });
            if (this.journal.length > JOURNAL_LIMIT) {
                this.journal.shift();
                this.journalTruncated = true;
            }
        }

        _record(commands, { coalesce = false } = {}) {
            const at = this.now();
            for (const cmd of commands) {
                cmd.at = at;
                this._journal(cmd);
            }
            if (this.tx) {
                this.tx.commands.push(...commands);  // the redo stack is cleared when it commits (a cancel keeps it)
                return;
            }
            this.redoStack.length = 0;
            const top = this.undoStack[this.undoStack.length - 1];
            const one = commands[0];
            if (coalesce && commands.length === 1 && top && top.coalesce && top.commands.length === 1 && at - top.at <= this.coalesceMs) {
                const last = top.commands[0];
                if (last.type === one.type && last.scene_id === one.scene_id && last.field === one.field && !last.revert && !one.revert) {
                    last.after = one.after;
                    last.at = at;
                    top.at = at;
                    if (sameJSON(last.before, last.after)) this.undoStack.pop();  // typed back to where it was
                    return;
                }
            }
            this.undoStack.push({ label: labelOf(one), commands, at, coalesce });
            if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
        }

        // Applies one side ('before' / 'after') of a command to the lesson; {ok, reason?}
        _apply(cmd, side) {
            const value = cmd[side];
            const other = cmd[side === 'before' ? 'after' : 'before'];
            const type = cmd.type;
            if (type === 'override') return { ok: true };  // the page applies it (composition review route)
            if (type === 'lesson_captions') {
                if (!validEditValue(type, value)) return { ok: false, reason: 'invalid' };
                if (value === null) delete this.editor.captions;
                else this.editor.captions = { visible: value };
                return { ok: true };
            }
            const index = this.indexOf(cmd.scene_id);
            if (PRESENCE.includes(type)) {
                if (value === null) {
                    if (index < 0) return { ok: false, reason: 'missing' };
                    const [removed] = this.scenes.splice(index, 1);
                    if (isObj(other)) other.scene = jsonCopy(removed);  // the latest version comes back on redo / undo
                    return { ok: true, removed };
                }
                if (!isObj(value) || !isObj(value.scene) || value.scene.scene_id !== cmd.scene_id) return { ok: false, reason: 'invalid' };
                if (index >= 0) return { ok: false, reason: 'exists' };
                if (this.scenes.length >= MAX_SCENES) return { ok: false, reason: 'full' };
                const at = clampInt(Number.isInteger(value.index) ? value.index : this.scenes.length, 0, this.scenes.length);
                this.scenes.splice(at, 0, jsonCopy(value.scene));
                return { ok: true, index: at };
            }
            if (index < 0) return { ok: false, reason: 'missing' };
            const scene = this.scenes[index];
            if (type === 'move') {
                if (!isObj(value) || !Number.isInteger(value.index)) return { ok: false, reason: 'invalid' };
                const to = clampInt(value.index, 0, this.scenes.length - 1);
                if (to !== index) this.scenes.splice(to, 0, this.scenes.splice(index, 1)[0]);
                return { ok: true, index: to };
            }
            if (type === 'text') {
                const v = isObj(value) ? cleanFieldValue(cmd.field, value.value) : undefined;
                if (!SOURCE_FIELDS.includes(cmd.field) || v === undefined) return { ok: false, reason: 'invalid' };
                setFieldValue(scene, cmd.field, v);
                if (has(value, 'original')) {
                    const o = cleanFieldValue(cmd.field, value.original);
                    const edit = ensureEdit(scene);
                    edit.original = { ...(isObj(edit.original) ? edit.original : {}), [cmd.field]: o === null ? '' : o };
                } else if (isObj(scene.edit) && isObj(scene.edit.original)) {
                    delete scene.edit.original[cmd.field];
                }
                tidyEdit(scene);
                return { ok: true };
            }
            if (has(EDIT_KEYS, type)) {
                if (!validEditValue(type, value)) return { ok: false, reason: 'invalid' };
                const key = EDIT_KEYS[type];
                if (value === null || value === false) {
                    if (isObj(scene.edit)) delete scene.edit[key];
                } else {
                    ensureEdit(scene)[key] = value;
                }
                tidyEdit(scene);
                return { ok: true };
            }
            return { ok: false, reason: 'invalid' };
        }

        _outcome(commands, side, extra = {}) {
            const ids = [...new Set(commands.map(c => c.scene_id).filter(Boolean))];
            return {
                ok: true, changed: true, label: extra.label || labelOf(commands[0]), type: commands[0].type, scene_id: commands[0].scene_id,
                scene_ids: ids, structure: commands.some(c => TYPES[c.type].structural), retime: commands.some(retimes),
                via: extra.via || 'do', effects: compositionEffects(commands, side, extra.via || 'do'), ...extra
            };
        }

        _commit(commands, extra = {}, options = {}) {
            for (const cmd of commands) {
                const r = this._apply(cmd, 'after');
                if (!r.ok) throw new Error(`The ${cmd.type} edit could not be applied (${r.reason}).`);
            }
            this._record(commands, options);
            const result = this._outcome(commands, 'after', extra);
            this._emit({ kind: 'change', result });
            return result;
        }

        _noop(id, label) {
            return { ok: true, changed: false, label, scene_id: id, scene_ids: id ? [id] : [], structure: false, retime: false, effects: [] };
        }

        _canRestructure() {
            try { return !this.structureLocked(); } catch (e) { return true; }
        }

        // ---- transactions ----

        begin(label = 'Edit') {
            if (this.tx) this.tx.depth += 1;
            else this.tx = { label: cleanStr(label, 80) || 'Edit', commands: [], depth: 1 };
        }

        commit() {
            if (!this.tx) return null;
            this.tx.depth -= 1;
            if (this.tx.depth > 0) return null;
            const tx = this.tx;
            this.tx = null;
            if (!tx.commands.length) return this._noop(null, tx.label);
            this.redoStack.length = 0;
            this.undoStack.push({ label: tx.label, commands: tx.commands, at: this.now(), coalesce: false });
            if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
            const result = this._outcome(tx.commands, 'after', { label: tx.label, transaction: true });
            this._emit({ kind: 'change', result });
            return result;
        }

        // Rolls the open transaction back (every command in it, nested ones too)
        cancel() {
            if (!this.tx) return null;
            const tx = this.tx;
            this.tx = null;
            if (!tx.commands.length) return this._noop(null, tx.label);
            for (const cmd of [...tx.commands].reverse()) {
                this._apply(cmd, 'before');
                this._journal(invert(cmd, this.now()));
            }
            const result = this._outcome(tx.commands, 'before', { label: tx.label, cancelled: true, via: 'undo' });
            this._emit({ kind: 'change', result });
            return result;
        }

        // Groups every command fn makes into one undo step (a drag, a typing burst); fn may return a promise
        transaction(label, fn) {
            this.begin(label);
            let value;
            try {
                value = fn(this);
            } catch (e) {
                this.cancel();
                throw e;
            }
            if (value && typeof value.then === 'function') {
                return value.then(v => Object.assign(this.commit() || this._noop(null, label), { value: v }), e => { this.cancel(); throw e; });
            }
            return Object.assign(this.commit() || this._noop(null, label), { value });
        }

        // ---- undo / redo ----

        canUndo() { return !this.tx && this.undoStack.length > 0; }
        canRedo() { return !this.tx && this.redoStack.length > 0; }
        undoLabel() { const e = this.undoStack[this.undoStack.length - 1]; return e ? `Undo ${lowerFirst(e.label)}` : null; }
        redoLabel() { const e = this.redoStack[this.redoStack.length - 1]; return e ? `Redo ${lowerFirst(e.label)}` : null; }

        _step(from, to, side) {
            if (this.tx) return fail('Finish the current change first.');
            const entry = from[from.length - 1];
            if (!entry) return fail(side === 'before' ? 'Nothing to undo.' : 'Nothing to redo.');
            if (entry.commands.some(c => TYPES[c.type].structural) && !this._canRestructure()) return fail(LOCKED);
            from.pop();
            const list = side === 'before' ? [...entry.commands].reverse() : entry.commands;
            const problems = [];
            for (const cmd of list) {
                const r = this._apply(cmd, side);
                if (!r.ok) problems.push({ type: cmd.type, scene_id: cmd.scene_id, reason: r.reason });
                this._journal(side === 'before' ? invert(cmd, this.now()) : { ...cmd, at: this.now() });
            }
            entry.coalesce = false;
            to.push(entry);
            const top = this.undoStack[this.undoStack.length - 1];
            if (top) top.coalesce = false;
            const result = this._outcome(entry.commands, side, { label: entry.label, problems, via: side === 'before' ? 'undo' : 'redo' });
            this._emit({ kind: 'change', result });
            return result;
        }

        undo() { return this._step(this.undoStack, this.redoStack, 'before'); }
        redo() { return this._step(this.redoStack, this.undoStack, 'after'); }

        // ---- structure ----

        move(id, toIndex) {
            const from = this.indexOf(id);
            if (from < 0) return fail('Scene not found.');
            if (typeof toIndex !== 'number' || !Number.isFinite(toIndex)) return fail('Say where to move the scene.');
            if (!this._canRestructure()) return fail(LOCKED);
            const to = clampInt(toIndex, 0, this.scenes.length - 1);
            if (to === from) return this._noop(id, 'Move scene');
            return this._commit([{ type: 'move', scene_id: id, before: { index: from }, after: { index: to } }], { index: to });
        }

        moveBy(id, delta) {
            const from = this.indexOf(id);
            if (from < 0) return fail('Scene not found.');
            if (!Number.isInteger(delta)) return fail('Say how far to move the scene.');
            return this.move(id, from + delta);
        }

        moveToStart(id) { return this.move(id, 0); }
        moveToEnd(id) { return this.move(id, this.scenes.length - 1); }

        _insertScene(scene, index, type, extra = {}) {
            if (!this._canRestructure()) return fail(LOCKED);
            if (this.scenes.length >= MAX_SCENES) return fail(`A lesson can have at most ${MAX_SCENES} scenes.`);
            const at = clampInt(index, 0, this.scenes.length);
            const result = this._commit([{ type, scene_id: scene.scene_id, before: null, after: { index: at, scene: jsonCopy(scene) } }],
                { index: at, ...extra });
            result.scene = this.scenes[at];
            return result;
        }

        // A deep copy right after the scene, with a NEW id and none of the original's approvals
        duplicate(id) {
            const index = this.indexOf(id);
            if (index < 0) return fail('Scene not found.');
            const copy = freshCopy(this.scenes[index]);
            copy.scene_id = this._newId();
            const edit = ensureEdit(copy);
            edit.origin = 'duplicated';
            edit.from = id;
            return this._insertScene(copy, index + 1, 'duplicate', { new_scene_id: copy.scene_id });
        }

        // Removes the scene from the lesson (its assets are untouched); result.scene is the removed scene
        remove(id) {
            const index = this.indexOf(id);
            if (index < 0) return fail('Scene not found.');
            if (!this._canRestructure()) return fail(LOCKED);
            if (this.scenes.length <= 1) return fail('A lesson needs at least one scene.');
            const removed = this.scenes[index];
            if (!isHidden(removed) && !this.scenes.some(s => s !== removed && !isHidden(s))) return fail('A lesson needs at least one scene that plays.');
            const result = this._commit([{ type: 'remove', scene_id: id, before: { index, scene: jsonCopy(removed) }, after: null }], { index });
            result.scene = removed;
            return result;
        }

        // kind: 'blank' (a content scene), 'asset' (a picture scene from data.asset_id), 'copy' (data.scene, given a new id)
        insert(index, kind, data = {}) {
            const at = index === undefined || index === null ? this.scenes.length : index;
            if (typeof at !== 'number' || !Number.isFinite(at)) return fail('Say where to add the scene.');
            const d = isObj(data) ? data : {};
            let scene;
            if (kind === 'blank') {
                const text = cleanStr(d.text, 2000) || 'Add the explanation here.';
                scene = { type: 'content', title: cleanStr(d.title, TEXT_LIMITS.title) || 'New scene', html: `<p>${escapeHtml(text)}</p>`,
                    narration: typeof d.narration === 'string' ? d.narration.slice(0, TEXT_LIMITS.narration) : '' };
            } else if (kind === 'asset') {
                const assetId = typeof d.asset_id === 'string' ? d.asset_id : '';
                if (!ASSET_RE.test(assetId)) return fail('Choose a picture from your Library.');
                scene = { type: 'content', title: cleanStr(d.title, TEXT_LIMITS.title) || 'Picture', html: `<img src="asset:${assetId}" alt="">`, narration: '' };
            } else if (kind === 'copy') {
                if (!isObj(d.scene)) return fail('There is no scene to paste.');
                scene = freshCopy(d.scene);
                if (isObj(scene.edit)) delete scene.edit.from;
            } else {
                return fail('Unknown kind of scene.');
            }
            scene.scene_id = this._newId();
            ensureEdit(scene).origin = 'inserted';
            return this._insertScene(scene, at, 'insert', { new_scene_id: scene.scene_id });
        }

        setHidden(id, hidden) {
            const scene = this.scene(id);
            if (!scene) return fail('Scene not found.');
            if (typeof hidden !== 'boolean') return fail('Hidden must be true or false.');
            const current = isHidden(scene);
            if (current === hidden) return this._noop(id, hidden ? 'Hide scene' : 'Show scene');
            if (hidden && this.scenes.filter(s => !isHidden(s)).length <= 1) return fail('At least one scene must stay visible.');
            return this._commit([{ type: 'hide', scene_id: id, before: current, after: hidden }]);
        }

        // The [PAUSE] / [PAUSE:n] markers the scene can be split at (spoken words on both sides); [] = splitting not offered
        splitPoints(id) {
            const scene = this.scene(id);
            const text = scene && typeof scene.narration === 'string' ? scene.narration : '';
            const points = [];
            let n = 0;
            for (const m of text.matchAll(PAUSE_RE)) {
                const before = text.slice(0, m.index);
                const after = text.slice(m.index + m[0].length);
                if (spokenWords(before) && spokenWords(after)) {
                    const plain = s => s.replace(PAUSE_RE, ' ').split('[SYNC]').join(' ').replace(/\s+/g, ' ').trim();
                    const head = plain(before);
                    const tail = plain(after);
                    points.push({ index: n, marker: m[0], pause: m[1] ? parseFloat(m[1]) : PAUSE_DEFAULT, offset: m.index,
                        seconds: round2(spokenWords(before) / WORDS_PER_SECOND + pauseSeconds(before)),
                        before: head.length > 60 ? '…' + head.slice(-59) : head, after: tail.length > 60 ? tail.slice(0, 59) + '…' : tail });
                }
                n += 1;
            }
            return points;
        }

        // Splits the narration at the chosen marker into two scenes sharing the board (the second: a new id, origin 'split')
        splitAtPause(id, pauseIndex) {
            const index = this.indexOf(id);
            if (index < 0) return fail('Scene not found.');
            if (!Number.isInteger(pauseIndex)) return fail('Choose a pause to split at.');
            const point = this.splitPoints(id).find(p => p.index === pauseIndex);
            if (!point) return fail('This scene has no pause there to split at.');
            if (!this._canRestructure()) return fail(LOCKED);
            if (this.scenes.length >= MAX_SCENES) return fail(`A lesson can have at most ${MAX_SCENES} scenes.`);
            const scene = this.scenes[index];
            const text = scene.narration;
            const first = text.slice(0, point.offset).replace(/\s+$/, '');
            const second = text.slice(point.offset + point.marker.length).replace(/^\s+/, '');
            const edit = editOf(scene);
            const original = has(edit.original, 'narration') ? { original: edit.original.narration } : {};
            const copy = freshCopy(scene);
            copy.scene_id = this._newId();
            copy.narration = second;
            copy.edit = { origin: 'split', from: id };
            const commands = [
                { type: 'text', scene_id: id, field: 'narration', split: copy.scene_id, before: { value: text, ...original }, after: { value: first, ...original } },
                { type: 'split', scene_id: copy.scene_id, before: null, after: { index: index + 1, scene: copy } }
            ];
            this.begin('Split scene');
            try {
                for (const cmd of commands) {
                    const r = this._apply(cmd, 'after');
                    if (!r.ok) throw new Error(`The split could not be applied (${r.reason}).`);
                    this._record([cmd]);  // recorded one by one: a failure rolls back what was applied
                }
            } catch (e) {
                this.cancel();
                return fail(e.message);
            }
            const result = this.commit() || this._outcome(commands, 'after', { label: 'Split scene' });
            return Object.assign(result, { index: index + 1, new_scene_id: copy.scene_id, scene: this.scenes[index + 1] });
        }

        // ---- properties ----

        _setEdit(id, type, value, options = {}) {
            const scene = this.scene(id);
            if (!scene) return fail('Scene not found.');
            const key = EDIT_KEYS[type];
            const raw = editOf(scene)[key];
            const current = type === 'hide' || type === 'mute' ? raw === true : (validEditValue(type, raw) && raw !== undefined ? raw : null);
            if (sameJSON(current, value)) return this._noop(id, labelOf({ type, after: value }));
            return this._commit([{ type, scene_id: id, before: current, after: value }], {}, options);
        }

        // Hold the scene at least this long (0.5 to 600 s; never speeds the narration up); null removes the minimum
        setDuration(id, seconds) {
            let v = seconds;
            if (typeof v === 'string') v = /^\s*\d+(?:\.\d+)?\s*$/.test(v) ? parseFloat(v) : (v.trim() === '' ? null : NaN);
            if (v !== null && v !== undefined && (typeof v !== 'number' || !Number.isFinite(v))) return fail('The duration must be a number of seconds.');
            const value = v === null || v === undefined ? null : round2(Math.max(HOLD_MIN, Math.min(HOLD_MAX, v)));
            return this._setEdit(id, 'duration', value, { coalesce: true });
        }

        setNarrationMuted(id, muted) {
            if (typeof muted !== 'boolean') return fail('Muted must be true or false.');
            return this._setEdit(id, 'mute', muted);
        }

        // 'off' hides this scene's captions; null shows them as the lesson says
        setCaptions(id, value) {
            if (value !== 'off' && value !== null) return fail('Captions are "off" or follow the lesson.');
            return this._setEdit(id, 'captions', value);
        }

        setLessonCaptions(visible) {
            if (typeof visible !== 'boolean') return fail('Visible must be true or false.');
            const current = isObj(this.editor.captions) && typeof this.editor.captions.visible === 'boolean' ? this.editor.captions.visible : null;
            if (current === visible) return this._noop(null, visible ? 'Show captions' : 'Hide captions');
            return this._commit([{ type: 'lesson_captions', scene_id: null, before: current, after: visible }]);
        }

        _setField(id, field, raw) {
            const scene = this.scene(id);
            if (!scene) return fail('Scene not found.');
            const v = cleanFieldValue(field, raw);
            if (v === undefined) return fail(field === 'labels' ? 'Labels must be a list of words.' : `The ${FIELD_LABELS[field]} must be text.`);
            const current = getField(scene, field);
            if (eqField(field, current, v)) return this._noop(id, `Edit ${FIELD_LABELS[field]}`);
            const edit = editOf(scene);
            const hadOriginal = has(edit.original, field);
            const generated = hadOriginal ? edit.original[field] : boundOriginal(field, current);
            const before = { value: current, ...(hadOriginal ? { original: generated } : {}) };
            // back to the generated value: not edited any more (exactly the generated value again; cleared: absent again)
            const after = eqField(field, generated, v) ? { value: v === null ? null : generated } : { value: v, original: generated };
            return this._commit([{ type: 'text', scene_id: id, field, before, after }], {}, { coalesce: true });
        }

        setNarration(id, text) { return this._setField(id, 'narration', text); }

        // field: title, subtitle, html (the board) or narration
        setText(id, field, value) {
            if (!TEXT_FIELDS.includes(field)) return fail('Only the title, subtitle, board text and narration can be edited here.');
            return this._setField(id, field, value);
        }

        // scene.composition.labels: up to 6 short labels
        setLabels(id, labels) {
            if (!Array.isArray(labels)) return fail('Labels must be a list of words.');
            return this._setField(id, 'labels', labels);
        }

        // Back to the generated value of a source field
        revert(id, field) {
            const scene = this.scene(id);
            if (!scene) return fail('Scene not found.');
            if (!SOURCE_FIELDS.includes(field)) return fail('This field cannot be reverted.');
            const edit = editOf(scene);
            if (!has(edit.original, field)) return this._noop(id, `Revert ${FIELD_LABELS[field]}`);
            const original = edit.original[field];
            const value = field === 'labels' ? (cleanLabels(original) || []) : (typeof original === 'string' ? original : '');
            return this._commit([{ type: 'text', scene_id: id, field, revert: true, before: { value: getField(scene, field), original },
                after: { value: field === 'subtitle' && value === '' ? null : value } }]);
        }

        // A composition override: NOT applied here. The result {kind: 'composition', scene_id, overrides} goes through the
        // existing review route (cinematicApi.review({action: 'change', overrides})); undo sends the previous value ('auto').
        setOverride(id, key, value) {
            const scene = this.scene(id);
            if (!scene) return fail('Scene not found.');
            if (!has(OVERRIDE_VALUES, key)) return fail('This is not something a composition can change.');
            if (value !== 'auto' && !OVERRIDE_VALUES[key].includes(value)) return fail(`Unknown ${OVERRIDE_LABELS[key]}.`);
            const previous = currentOverride(scene, key);
            if (previous === value) return this._noop(id, `Change ${OVERRIDE_LABELS[key]}`);
            const result = this._commit([{ type: 'override', scene_id: id, before: { [key]: previous }, after: { [key]: value } }]);
            return Object.assign(result, { kind: 'composition', overrides: { [key]: value } });
        }

        // ---- what the UI shows ----

        timeline() {
            const settings = this._settings();
            const scenes = [];
            const transitions = [];
            let cursor = 0;
            let prev;
            this.scenes.forEach((scene, index) => {
                const id = isObj(scene) && validId(scene.scene_id) ? scene.scene_id : null;
                if (isHidden(scene)) {
                    const at = round2(cursor);
                    scenes.push({ scene_id: id, index, start: at, end: at, seconds: 0, play_seconds: 0, transition_seconds: 0, hidden: true });
                    return;
                }
                const play = playSeconds(scene);
                const t = prev !== undefined ? transitionOf(scene, settings) : null;
                const tr = t ? t.seconds : 0;
                const start = cursor;
                cursor += tr + play;
                if (t) transitions.push({ from: prev, to: id, kind: t.kind, start: round2(start), end: round2(start + tr), seconds: tr });
                scenes.push({ scene_id: id, index, start: round2(start), end: round2(cursor), seconds: round2(tr + play), play_seconds: play,
                    transition_seconds: tr, hidden: false });
                prev = id;
            });
            return { total: round2(cursor), scenes, transitions };
        }

        timeAt(id) {
            const entry = this.timeline().scenes.find(s => s.scene_id === id && id);
            return entry ? entry.start : null;
        }

        // The visible scene playing at this second (the last one at or after the end); null when nothing is visible
        sceneAt(seconds) {
            if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null;
            const tl = this.timeline();
            const visible = tl.scenes.filter(s => !s.hidden);
            if (!visible.length) return null;
            const found = visible.find(s => seconds >= s.start && seconds < s.end) || (seconds < 0 ? visible[0] : visible[visible.length - 1]);
            return { ...found, offset: round2(Math.max(0, Math.min(found.seconds, seconds - found.start))) };
        }

        // Snaps to a scene boundary within 0.3 s (else the time itself, kept inside the lesson)
        snap(seconds, tolerance = SNAP_SECONDS) {
            if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 0;
            const tl = this.timeline();
            const bounds = [0, ...tl.scenes.filter(s => !s.hidden).map(s => s.start), tl.total];
            let best = null;
            for (const b of bounds) if (Math.abs(b - seconds) <= tolerance && (best === null || Math.abs(b - seconds) < Math.abs(best - seconds))) best = b;
            return best !== null ? best : round2(Math.max(0, Math.min(tl.total, seconds)));
        }

        effective() {
            const tl = this.timeline();
            const moved = movedIds(this.scenes, this.editor.generated_order);
            return this.scenes.map((scene, index) => {
                const s = isObj(scene) ? scene : {};
                const edit = editOf(s);
                const t = tl.scenes[index];
                const hidden = isHidden(s);
                return {
                    scene_id: t.scene_id, index, visible: !hidden, hidden,
                    title: typeof s.title === 'string' ? s.title.slice(0, TEXT_LIMITS.title) : '',
                    type: typeof s.type === 'string' ? s.type : 'content',
                    estimate_seconds: estimateSeconds(s.narration),
                    min_seconds: validHold(edit.min_seconds) ? edit.min_seconds : null,
                    play_seconds: hidden ? playSeconds(s) : t.play_seconds,
                    transition_seconds: t.transition_seconds,
                    start: t.start, end: t.end,
                    edited: isObj(edit.original) ? Object.keys(edit.original).filter(f => SOURCE_FIELDS.includes(f)) : [],
                    origin: ['inserted', 'duplicated', 'split'].includes(edit.origin) ? edit.origin : 'generated',
                    from: validId(edit.from) ? edit.from : null,
                    moved: !!t.scene_id && moved.has(t.scene_id),
                    approvals: approvalsOf(s),
                    narration_muted: edit.narration_muted === true,
                    captions: edit.captions === 'off' ? 'off' : null,
                    can_split: this.splitPoints(t.scene_id).length > 0
                };
            });
        }

        // ---- saving ----

        // The commands applied since the last save (plain copies), for a replay after a 409 or a draft
        pending() {
            return this.journal.map(e => jsonCopy(e.command));
        }

        // seq: the model's seq when the payload was serialized (edits made during the save stay pending)
        markSaved(revision, seq = this.seq) {
            if (revision !== undefined) this.revision = revision;
            const upTo = Number.isInteger(seq) ? Math.min(seq, this.seq) : this.seq;
            this.savedSeq = Math.max(this.savedSeq, upTo);
            this.journal = this.journal.filter(e => e.seq > upTo);
            if (!this.journal.length) this.journalTruncated = false;
            this._emit({ kind: 'saved', revision: this.revision });
        }

        // The PUT /api/editor body (without expected_revision): plain copies of the scenes and the editor data
        serialize() {
            return { scenes: jsonCopy(this.scenes), editor: jsonCopy(this.editor) };
        }

        // Re-applies unsaved commands by scene_id onto a newer lesson (after a 409 or from a draft). scenes (optional): the
        // newer lesson's scenes, spliced into the live array first; info: {revision, editor}. Override commands are skipped
        // (the review route already saved them). A command whose scene is gone is a conflict (not applied); one whose
        // field changed meanwhile is applied and reported; structural ones while structureLocked() are conflicts ('locked',
        // not applied). Returns {applied, skipped, conflicts}.
        replay(commands, scenes = null, info = {}) {
            if (this.tx) {
                this.tx.depth = 1;  // an open transaction ends here (its edits are kept as one step)
                this.commit();
            }
            if (Array.isArray(scenes)) {
                this.scenes.splice(0, this.scenes.length, ...scenes);
                ensureIds(this.scenes);
            }
            const { revision, editor } = isObj(info) ? info : {};
            if (isObj(editor)) {
                for (const k of Object.keys(this.editor)) delete this.editor[k];
                Object.assign(this.editor, jsonCopy(editor));
                normalizeEditor(this.editor, this.scenes);
            }
            if (revision !== undefined) this.revision = revision;
            const truncated = this.journalTruncated;
            // while the lesson is generating, order / membership edits are not replayed (a split's narration half neither)
            const locked = !this._canRestructure();
            const conflicts = [];
            const done = [];
            let applied = 0;
            let skipped = 0;
            for (const raw of Array.isArray(commands) ? commands : []) {
                const cmd = cleanCommand(raw);
                if (!cmd) {
                    conflicts.push({ type: isObj(raw) && typeof raw.type === 'string' ? raw.type.slice(0, 40) : null,
                        scene_id: isObj(raw) && validId(raw.scene_id) ? raw.scene_id : null, reason: 'invalid', applied: false, label: 'An unreadable change' });
                    continue;
                }
                const outcome = locked && (TYPES[cmd.type].structural || (cmd.type === 'text' && cmd.split)) ? { reason: 'locked' } : this._replayOne(cmd);
                if (outcome.applied) {
                    applied += 1;
                    done.push(cmd);
                } else if (outcome.skipped) {
                    skipped += 1;
                }
                if (outcome.reason) conflicts.push({ type: cmd.type, scene_id: cmd.scene_id, reason: outcome.reason, applied: !!outcome.applied, label: labelOf(cmd) });
            }
            if (truncated) conflicts.push({ type: null, scene_id: null, reason: 'truncated', applied: false, label: 'Older unsaved changes' });
            this.journalTruncated = false;
            this.journal = [];
            this.seq += 1;
            for (const cmd of done) this._journal(cmd);
            if (!done.length) this.savedSeq = this.seq;  // the lesson is the server's again
            const result = { applied, skipped, conflicts };
            this._emit({ kind: 'change', result: { ok: true, changed: true, label: 'Reload', replay: result, structure: true, retime: true,
                scene_ids: [], effects: [] } });
            return result;
        }

        _replayOne(cmd) {
            const type = cmd.type;
            if (type === 'override') return { skipped: true };
            if (PRESENCE.includes(type)) {
                const exists = this.indexOf(cmd.scene_id) >= 0;
                if (cmd.after === null) {
                    if (!exists) return { skipped: true };  // already gone
                    const target = this.scene(cmd.scene_id);
                    if (!isHidden(target) && !this.scenes.some(s => s !== target && !isHidden(s))) return { reason: 'last_visible' };
                    const r = this._apply(cmd, 'after');
                    return r.ok ? { applied: true } : { reason: r.reason };
                }
                if (exists) return { skipped: true };  // already there
                const from = isObj(cmd.after.scene.edit) ? cmd.after.scene.edit.from : null;
                const source = (type === 'duplicate' || type === 'split') ? this.indexOf(from) : -1;
                const placed = source >= 0 ? { ...cmd, after: { ...cmd.after, index: source + 1 } } : cmd;
                const r = this._apply(placed, 'after');
                return r.ok ? { applied: true } : { reason: r.reason };
            }
            if (type === 'lesson_captions') {
                const r = this._apply(cmd, 'after');
                return r.ok ? { applied: true } : { reason: r.reason };
            }
            const scene = this.scene(cmd.scene_id);
            if (!scene) return { reason: 'missing' };
            let changed = false;
            if (type === 'text') changed = !eqField(cmd.field, getField(scene, cmd.field), cmd.before.value);
            else if (has(EDIT_KEYS, type)) {
                const raw = editOf(scene)[EDIT_KEYS[type]];
                const current = type === 'hide' || type === 'mute' ? raw === true : (raw === undefined ? null : raw);
                const before = type === 'hide' || type === 'mute' ? cmd.before === true : cmd.before;
                changed = !sameJSON(current, before);
            }
            if (type === 'hide' && cmd.after === true && this.scenes.filter(s => !isHidden(s) && s !== scene).length === 0) {
                return { reason: 'last_visible' };
            }
            const r = this._apply(cmd, 'after');
            if (!r.ok) return { reason: r.reason };
            return { applied: true, reason: changed ? 'changed' : null };
        }
    }

    // ---- Autosave ---------------------------------------------------------------------------------------------------
    // save(payload) -> the new revision; payload = {expected_revision, scenes, editor}. A failed save throws an error with
    // .status (409 for a stale or refused save) and, for a 409, .revision (the current token) or .locked (structural edits
    // wait for generation). reload() -> {revision, scenes, editor} (GET /api/editor). States: saved, saving, unsaved,
    // error, conflict; onState(state, info).

    class Autosave {
        constructor({ model, save, reload = null, delay = 1500, onState = null, setTimeout: st = null, clearTimeout: ct = null, watch = true } = {}) {
            if (!model || typeof save !== 'function') throw new TypeError('Autosave needs the editor model and a save function.');
            this.model = model;
            this.save = save;
            this.reload = typeof reload === 'function' ? reload : null;
            this.delay = typeof delay === 'number' && delay >= 0 ? delay : 1500;
            this.onState = typeof onState === 'function' ? onState : null;
            this.setTimer = st || ((fn, ms) => setTimeout(fn, ms));
            this.clearTimer = ct || (id => clearTimeout(id));
            this.timer = null;
            this.held = 0;
            this.wanted = false;
            this.running = null;
            this.again = false;
            this.disposed = false;
            this.lastReplay = null;
            this.state = model.dirty ? 'unsaved' : 'saved';
            this.info = {};
            this.off = watch && typeof model.subscribe === 'function'
                ? model.subscribe(ev => { if (ev && ev.kind === 'change') this.schedule(); }) : null;
        }

        _set(state, info = {}) {
            this.state = state;
            this.info = info;
            if (this.onState) {
                try { this.onState(state, info); } catch (e) { /* the page's display problem */ }
            }
        }

        _clear() {
            if (this.timer !== null) {
                this.clearTimer(this.timer);
                this.timer = null;
            }
        }

        // Saves `delay` ms after the last edit (later while held or while a transaction is open)
        schedule() {
            if (this.disposed) return;
            if (this.model.dirty && !this.running && this.state !== 'unsaved') this._set('unsaved');
            if (this.held > 0 || this.model.inTransaction) {
                this.wanted = true;
                return;
            }
            this._clear();
            this.timer = this.setTimer(() => {
                this.timer = null;
                if (this.held > 0 || this.model.inTransaction) {
                    this.wanted = true;  // saved when the drag / transaction ends
                    return;
                }
                this.flush().catch(() => {});
            }, this.delay);
        }

        // A drag in progress: nothing is saved until release()
        hold() {
            this.held += 1;
            if (this.timer !== null) this.wanted = true;
            this._clear();
        }

        release() {
            if (this.held > 0) this.held -= 1;
            if (this.held === 0 && (this.wanted || this.model.dirty)) {
                this.wanted = false;
                this.schedule();
            }
        }

        // Saves now (one save at a time: a flush during a save saves again once it ends). Resolves true when saved.
        flush() {
            this._clear();
            if (this.disposed) return Promise.resolve(false);
            if (this.running) {
                this.again = true;
                return this.running;
            }
            this.running = (async () => {
                try {
                    let ok;
                    do {
                        this.again = false;
                        ok = await this._saveOnce();
                    } while (ok && this.again && this.model.dirty && !this.disposed);
                    return ok;
                } finally {
                    this.running = null;
                }
            })();
            return this.running;
        }

        retry() { return this.flush(); }

        _payload() {
            return { seq: this.model.seq, payload: { expected_revision: this.model.revision, ...this.model.serialize() } };
        }

        async _saveOnce() {
            if (!this.model.dirty) {
                if (this.state !== 'conflict' && this.state !== 'saved') this._set('saved', { revision: this.model.revision });
                return true;
            }
            const { seq, payload } = this._payload();
            this._set('saving');
            try {
                const revision = await this.save(payload);
                this.model.markSaved(revision, seq);
                this._set(this.model.dirty ? 'unsaved' : 'saved', { revision });
                return true;
            } catch (err) {
                const status = err && err.status;
                const message = err && err.message ? String(err.message) : 'The edits could not be saved.';
                if (status === 409) {
                    const locked = !!(err.locked || (err.revision !== undefined && err.revision !== null && err.revision === payload.expected_revision));
                    if (locked) {
                        this._set('error', { error: message, status, locked: true });
                        return false;
                    }
                    return this._recover(message);
                }
                this._set('error', { error: message, status: status || null });
                return false;
            }
        }

        // A stale save: reload the newer lesson, replay the unsaved commands on it, retry once
        async _recover(message) {
            if (!this.reload) {
                this._set('conflict', { error: message, saved: false });
                return false;
            }
            let fresh;
            try {
                fresh = await this.reload();
            } catch (e) {
                this._set('error', { error: 'The newer lesson could not be loaded: ' + (e && e.message ? e.message : 'unknown error'), conflict: true });
                return false;
            }
            if (!isObj(fresh) || !Array.isArray(fresh.scenes)) {
                this._set('error', { error: 'The newer lesson could not be loaded.', conflict: true });
                return false;
            }
            const replay = this.model.replay(this.model.pending(), fresh.scenes, { revision: fresh.revision, editor: fresh.editor });
            this.lastReplay = replay;
            if (!this.model.dirty) {
                this._set(replay.conflicts.length ? 'conflict' : 'saved', { revision: this.model.revision, replay, conflicts: replay.conflicts, saved: true });
                return true;
            }
            const { seq, payload } = this._payload();
            this._set('saving', { retry: true });
            try {
                const revision = await this.save(payload);
                this.model.markSaved(revision, seq);
                if (replay.conflicts.length) this._set('conflict', { revision, replay, conflicts: replay.conflicts, saved: true });
                else this._set(this.model.dirty ? 'unsaved' : 'saved', { revision, replay });
                return true;
            } catch (err) {
                const status = err && err.status;
                this._set(status === 409 ? 'conflict' : 'error', { error: err && err.message ? String(err.message) : 'The edits could not be saved.',
                    status: status || null, replay, conflicts: replay.conflicts, saved: false });
                return false;
            }
        }

        dispose() {
            this.disposed = true;
            this._clear();
            if (this.off) this.off();
            this.off = null;
        }
    }

    // ---- the per-lesson draft (localStorage) ------------------------------------------------------------------------
    // The unsaved commands (replayable by scene_id on the reloaded lesson), so a refresh does not lose edits.

    function draftKey(projectId) {
        const id = projectId === null || projectId === undefined ? '' : String(projectId);
        return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? DRAFT_PREFIX + id : null;
    }

    function defaultStorage() {
        try {
            return typeof localStorage !== 'undefined' ? localStorage : null;
        } catch (e) {
            return null;
        }
    }

    // Writes the model's unsaved commands (none: the draft is removed). True when stored.
    function saveDraft(model, storage = defaultStorage()) {
        const key = model ? draftKey(model.projectId) : null;
        if (!key || !storage) return false;
        try {
            const commands = model.pending();
            if (!commands.length) {
                storage.removeItem(key);
                return true;
            }
            const text = JSON.stringify({ version: VERSION, project_id: String(model.projectId), revision: model.revision === undefined ? null : model.revision,
                saved_at: Date.now(), commands });
            if (text.length > DRAFT_MAX_CHARS) return false;
            storage.setItem(key, text);
            return true;
        } catch (e) {
            return false;
        }
    }

    // {project_id, revision, saved_at, commands} or null (none, unreadable, another lesson's)
    function loadDraft(projectId, storage = defaultStorage()) {
        const key = draftKey(projectId);
        if (!key || !storage) return null;
        try {
            const text = storage.getItem(key);
            if (typeof text !== 'string' || text.length > DRAFT_MAX_CHARS) return null;
            const data = JSON.parse(text);
            if (!isObj(data) || data.version !== VERSION || data.project_id !== String(projectId) || !Array.isArray(data.commands)) return null;
            const commands = data.commands.slice(0, JOURNAL_LIMIT).map(cleanCommand).filter(Boolean);
            if (!commands.length) return null;
            return { project_id: data.project_id, revision: typeof data.revision === 'string' || typeof data.revision === 'number' ? data.revision : null,
                saved_at: typeof data.saved_at === 'number' ? data.saved_at : null, commands };
        } catch (e) {
            return null;
        }
    }

    function clearDraft(projectId, storage = defaultStorage()) {
        const key = draftKey(projectId);
        if (!key || !storage) return false;
        try {
            storage.removeItem(key);
            return true;
        } catch (e) {
            return false;
        }
    }

    const Draft = Object.freeze({ key: draftKey, save: saveDraft, load: loadDraft, clear: clearDraft, saveDraft, loadDraft, clearDraft });

    return {
        VERSION, MAX_SCENES, UNDO_LIMIT, TEXT_LIMITS, SOURCE_FIELDS, OVERRIDE_VALUES, OVERRIDE_LABELS, TRANSITION_SECONDS, ID_RE, ASSET_RE,
        sceneId, ensureIds, estimateSeconds, playSeconds, cleanCommand, labelOf,
        EditorModel, Autosave, Draft, draftKey, saveDraft, loadDraft, clearDraft
    };
});
