/*
 * Cinematic scenes (Phase 13) in the page: the lesson's scene style, the renderer of a scene's composition plan,
 * and the camera.
 *
 * The server's composer (cinematic.py) plans each scene (scene.cinematic_plan): a template, layers on the
 * normalized 16:9 frame (background, visual, board, presenter, labels, title, subtitles), a camera move that keeps
 * every important layer whole, a timeline and the lesson's transition. Here the plan is rendered with the page's
 * own elements - the board, the side panel (the educational visual), the presenter layer (presenter.js) - placed in
 * the plan's boxes, plus a background, a title overlay and label chips. The export records this same page, so
 * the preview and the video show the same composition.
 *
 * The camera moves the scene's layers (never the title overlay or the subtitles): one transform per layer, all
 * computed from the same framing, so they move as one picture. Formulas stay MathJax and code stays Prism: the
 * composition only places them. "Classic" (the default) leaves the page exactly as before.
 *
 * Loaded as a classic <script> (window.AadhiCinematic) and as a CommonJS module by tests/cinematic.test.js.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.AadhiCinematic = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const STORAGE_KEY = 'aadhi.cinematic';
    const VERSION = 1;
    // Phase 17: style = the lesson's video style family (null: the legacy typography's look), style_version, style_overrides
    // (a few bounded choices; "default" is never stored)
    const DEFAULTS = { mode: 'classic', typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto',
        background_asset_id: null, background_label: null, emphasis: 'clear', composer: 'rules', director: 'rules', learner_level: null,
        style: null, style_version: null, style_overrides: Object.freeze({}) };
    // Phase 14: who decides each scene's composition (the Intelligent Scene Composer, composer.py)
    const COMPOSERS = { rules: 'Automatic', ai: 'AI-assisted' };
    const SOURCES = { rules: 'Automatic', ai: 'AI-assisted', user: 'Your choice', screenplay: 'As the screenplay asks', fallback: 'Safe layout', phase13: 'Automatic' };
    // Phase 15: who decides each scene's visual direction (the AI Visual Director, visual_director.py), and for which learners
    const DIRECTORS = { rules: 'Automatic', ai: 'AI-assisted' };
    const LEARNERS = { '': 'General', beginner: 'Beginner', intermediate: 'Intermediate', advanced: 'Advanced' };
    const DIRECTION_SOURCES = { rules: 'Automatic', ai: 'AI-assisted', user: 'Your choice', fallback: 'Safe direction' };
    // The direction's vocabulary in plain words (the same codes as visual_director.py; the server's labels win once loaded)
    const STRATEGY_LABELS = {
        concept_overview: 'Concept overview', definition_visual: 'Definition with a simple picture', analogy: 'Analogy',
        conceptual_diagram: 'Diagram-first explanation', component_breakdown: 'Parts of the whole', relationship_map: 'Relationship map',
        step_by_step: 'Step-by-step process', workflow: 'Workflow', lifecycle: 'Life cycle', algorithm_flow: 'Algorithm, step by step',
        timeline: 'Timeline', side_by_side: 'Side-by-side comparison', before_after: 'Before and after',
        formula_explanation: 'Formula, symbol by symbol', equation_build: 'Equation built up', graph: 'Graph', numerical_example: 'Numerical example',
        code_walkthrough: 'Code walkthrough', code_to_output: 'Code and its output', mechanism: 'How it works', system_diagram: 'System diagram',
        simulation: 'Simulation', chart: 'Chart', table: 'Table', worked_example: 'Worked example', practical_demonstration: 'Demonstration',
        question_focus: 'Question focus', key_points: 'Key points', concept_map: 'Concept map', recap: 'Recap'
    };
    const VISUAL_LABELS = {
        board_text: 'The explanation on the board', definition_card: 'The definition, on its own card', step_flow: 'The steps as a flow',
        timeline: 'The events on a timeline', formula: 'The formula', code: 'The code', code_output: 'The code beside its output',
        comparison: 'Both sides, side by side', table: 'The table', key_points: 'The key points as cards', question: 'The question',
        diagram: 'The diagram', illustration: 'The illustration', chart: 'The chart', image: 'The picture', animation: 'The animation',
        video: 'The video', simulation: 'The simulation', presenter: 'The presenter, speaking', none: 'Nothing extra'
    };
    const PRESENTER_ROLES = { dominant: 'Presenter leads', secondary: 'Presenter beside the content', guide: 'Presenter small, pointing',
        demonstrator: 'Presenter demonstrates beside the visual', hidden: 'No presenter' };
    const MOTION_INTENTS = { progressive_build: 'Builds step by step', sequence: 'One part after another', compare: 'Both sides together',
        highlight: 'Highlights what matters', reveal: 'Appears with the narration', fade: 'Gentle fade', none: 'Still',
        move_along_path: 'Moves along a path', transform: 'Changes from one form into another', zoom_to_detail: 'Zooms in on a detail' };
    const CAMERA_INTENTS = { static: 'Still camera', slow_zoom: 'Slow zoom', focus: 'Moves in on what matters', pan: 'Pans across',
        follow_process: 'Follows the process', compare: 'Frames both sides', wide_to_detail: 'From the whole to a detail',
        detail_to_wide: 'From a detail to the whole' };
    const PREFER = { existing: 'Use existing visuals first', static: 'Keep it still (no camera or motion)' };
    const MODES = { classic: 'Classic', cinematic: 'Cinematic' };
    const BACKGROUNDS = { auto: 'Automatic', studio: "Aadhi's studio", gradient: 'Clean gradient', solid: 'Solid colour',
        image: 'Picture from your Library', video: 'Clip from your Library', ai: 'AI background' };
    const MOTIONS = { subtle: 'Subtle', none: 'None (still camera)' };
    const MOTION_CHOICES = { subtle: 'Gentle movement', none: 'Still camera' }; // Phase 21: the settings panel's words for MOTIONS
    const TRANSITIONS = { fade: 'Fade', soft_fade: 'Soft fade', crossfade: 'Crossfade', slide: 'Slide', zoom: 'Zoom', wipe: 'Wipe', cut: 'Cut' };
    // Transitions that move the picture: a plain fade instead for reduced motion (preview only)
    const MOVING_TRANSITIONS = ['slide', 'zoom', 'wipe'];
    const TEMPLATES = {
        presenter_intro: 'Presenter intro', presenter_explanation: 'Presenter + explanation', presenter_plus_visual: 'Presenter + visual',
        visual_focus: 'Visual focus', formula_focus: 'Formula focus', code_focus: 'Code focus', diagram_focus: 'Diagram focus',
        comparison: 'Comparison', quiz: 'Quiz', summary: 'Summary'
    };
    const PRESENTER_SIZES = { dominant: 'Large', secondary: 'Beside the content', small: 'Small', hidden: 'Hidden' };
    const VISUAL_SIZES = { dominant: 'Large', secondary: 'Medium', side_panel: 'Narrow', hidden: 'Hidden' };
    const MOTION_LEVELS = { none: 'None', subtle: 'Subtle', moderate: 'Moderate' };
    const CAMERA = { static: 'Still', slow_zoom_in: 'Slow zoom in', slow_zoom_out: 'Slow zoom out', pan_left: 'Pan left',
        pan_right: 'Pan right', pan_up: 'Pan up', pan_down: 'Pan down', focus: 'Focus' };
    const SHOTS = { wide: 'Wide', medium: 'Medium', close: 'Close', presenter: 'On the presenter', visual: 'On the visual',
        split: 'Split', full_canvas: 'Full canvas' };
    // The same numbers as cinematic.py
    const SUBTITLES = { x: 0, y: 0.85, w: 1, h: 0.15 };
    const EDGE = 0.02;
    const FULL = { x: 0, y: 0, w: 1 };
    const MIN_TEXT_SCALE = 0.8;
    const MIN_WIDE_SCALE = 0.45; // Phase 20: a wide formula or table is made smaller to fit its box, never below 45 % of its size
    const ANIMATION_MS = { fade_in: 500, slide_in: 650, scale_in: 650, appear: 1, highlight: 1600, fade_out: 400, slide_out: 450,
        scale_out: 450, disappear: 1 };
    const DEFAULT_EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';
    const DEFAULT_FOCUS_RGB = '255, 215, 0';

    // ---- Phase 17: the lesson's video style (styles.py; scratchpad/phase17_contract.md) ---------------------------------
    // The families when the server's vocabulary (GET /api/cinematic `looks`) is not loaded; the server's list wins once loaded
    const STYLE_FAMILIES = [
        { id: 'academic', version: 1, label: 'Academic', description: 'Clean and structured: a calm paper page with a strong hierarchy',
            tone: 'light', prefs: { transition: 'fade', camera: 'subtle' } },
        { id: 'cinematic_education', version: 1, label: 'Cinematic Education', description: 'Rich, deep and focused: visual storytelling with controlled depth',
            tone: 'dark', prefs: { transition: 'fade', camera: 'subtle' } },
        { id: 'children_education', version: 1, label: "Children's Education", description: 'Friendly and engaging: bright, rounded and clear',
            tone: 'light', prefs: { transition: 'soft_fade', camera: 'subtle' } },
        { id: 'corporate_training', version: 1, label: 'Corporate Training', description: 'Professional and focused: crisp navy, clear alignment, restrained',
            tone: 'dark', prefs: { transition: 'crossfade', camera: 'subtle' } }
    ];
    const STYLE_IDS = STYLE_FAMILIES.map(f => f.id);
    const DEFAULT_STYLE = 'cinematic_education';
    // The user's bounded choices (styles.OVERRIDE_OPTIONS); the server's options win once loaded
    const STYLE_OVERRIDES = { accent: ['default', 'gold', 'coral', 'crimson', 'teal', 'sky', 'violet', 'emerald'],
        text_size: ['default', 'standard', 'large', 'larger'], motion: ['default', 'low', 'standard'], background: ['default', 'plain', 'subtle', 'rich'],
        caption_size: ['default', 'standard', 'large'], code_size: ['default', 'standard', 'large'], formula_size: ['default', 'standard', 'large'],
        diagram_frame: ['default', 'panel', 'card', 'rounded', 'plain'] };
    const STYLE_OVERRIDE_LABELS = { accent: 'Accent colour', text_size: 'Text size', motion: 'Animation', background: 'Background', caption_size: 'Caption size',
        code_size: 'Code size', formula_size: 'Formula size', diagram_frame: 'Diagram frame' };
    const STYLE_MAIN = ['accent', 'text_size', 'motion', 'background', 'caption_size'];
    const STYLE_ADVANCED = ['code_size', 'formula_size', 'diagram_frame'];
    const TONES = ['dark', 'light'];
    // The body attributes a look sets (each value checked against its list: anything else is not set)
    const STYLE_ATTRS = [
        ['data-cine-style', (look) => look.family, STYLE_IDS],
        ['data-cine-tone', (look, prefs) => look.tone || prefs.tone, TONES],
        ['data-cine-emphasis', (look, prefs) => prefs.emphasis, ['glow', 'outline', 'soft']],
        ['data-cine-caption', (look, prefs) => prefs.caption, ['shadow', 'box']],
        ['data-cine-pframe', (look, prefs) => prefs.presenter_frame, ['none', 'clean', 'card', 'rounded']],
        ['data-cine-vframe', (look, prefs) => prefs.visual_frame, ['panel', 'card', 'rounded', 'plain']],
        ['data-cine-motion-level', (look, prefs) => prefs.motion, ['low', 'standard']]
    ];
    // A style's CSS variables are checked again here (the same rules as styles.css_variables): a name, and a value of plain
    // characters with no URL and no expression; anything else is skipped
    const STYLE_VAR_NAME = /^--st-[a-z0-9-]{1,40}$/;
    const STYLE_VAR_VALUE = /^[#(),.%\-A-Za-z0-9 '\/:]{0,200}$/; // ASCII only, spaces only
    // the only CSS functions a token may use (an allowlist, as in styles.py: nothing can fetch, reference or compute outside it)
    const STYLE_FUNCTIONS = new Set(['rgb', 'rgba', 'hsl', 'hsla', 'linear-gradient', 'radial-gradient', 'repeating-linear-gradient',
        'repeating-radial-gradient', 'clamp', 'calc', 'min', 'max', 'cubic-bezier', 'drop-shadow']);
    const MAX_STYLE_VARS = 300;
    const EASE_WORDS = ['ease', 'ease-in-out', 'ease-out', 'linear'];
    const MAX_CONTRAST_SCAN = 400;
    const MIN_CONTRAST = 4.5;

    function validStyleVar(name, value) {
        if (typeof name !== 'string' || !STYLE_VAR_NAME.test(name)) return false;
        if (typeof value !== 'string' || !STYLE_VAR_VALUE.test(value)) return false;
        const low = value.toLowerCase();
        if (low.includes('url') || low.includes('expression')) return false;
        const fns = value.match(/[A-Za-z-]+\(/g) || [];
        if (fns.some(f => !STYLE_FUNCTIONS.has(f.slice(0, -1).toLowerCase())) || (value.match(/\(/g) || []).length !== fns.length) return false;
        return !value.replace(/'[A-Za-z ]{1,40}'/g, '').includes("'"); // quotes only around a font's name
    }

    // The checked variables of a look's (or a family's) `css` ({name: value}, no prototype) and how many were skipped
    function styleVars(css) {
        const vars = Object.create(null);
        let skipped = 0;
        if (!css || typeof css !== 'object' || Array.isArray(css)) return { vars, skipped };
        const names = Object.keys(css);
        names.forEach((name, i) => {
            const raw = css[name];
            const value = typeof raw === 'number' && isFinite(raw) ? String(raw) : raw;
            if (i >= MAX_STYLE_VARS || !validStyleVar(name, value)) { skipped += 1; return; }
            vars[name] = value;
        });
        return { vars, skipped };
    }

    // A CSS property removed (the tests' DOM stand-in has no removeProperty: an empty value removes it too)
    function unsetProperty(style, name) {
        if (typeof style.removeProperty === 'function') style.removeProperty(name);
        else style.setProperty(name, '');
    }

    // The scene's effective style (plan.style.look), or null (a plan saved before Phase 17: nothing is set)
    function lookOf(plan) {
        const style = plan && plan.style;
        const look = style && typeof style === 'object' ? style.look : null;
        return look && typeof look === 'object' && !Array.isArray(look) ? look : null;
    }

    // ---- colours (WCAG relative luminance; the same arithmetic as styles.py) ----------------------------------------
    // [r, g, b, a] from #rgb(a), #rrggbb(aa), rgb()/rgba() (comma or space syntax), 'transparent'; null for anything else
    function parseColor(value) {
        const v = String(value === null || value === undefined ? '' : value).trim().toLowerCase();
        if (v.length > 64) return null; // a colour is short (keeps the patterns below linear)
        if (v === 'transparent') return [0, 0, 0, 0];
        let m = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(v);
        if (m) {
            const h = m[1].length <= 4 ? m[1].split('').map(c => c + c).join('') : m[1];
            return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1];
        }
        m = /^rgba?\(\s*(\d{1,3}(?:\.\d+)?)\s*(?:,\s*|\s+)(\d{1,3}(?:\.\d+)?)\s*(?:,\s*|\s+)(\d{1,3}(?:\.\d+)?)\s*(?:[,\/]\s*(\d*\.?\d+)(%?)\s*)?\)$/.exec(v);
        if (!m) return null;
        const ch = x => Math.min(255, Math.max(0, Math.round(parseFloat(x))));
        const a = m[4] === undefined ? 1 : Math.min(1, Math.max(0, parseFloat(m[4]) / (m[5] ? 100 : 1)));
        return isFinite(a) ? [ch(m[1]), ch(m[2]), ch(m[3]), a] : null;
    }
    const over = (top, bottom) => [0, 1, 2].map(i => Math.round(top[i] * top[3] + bottom[i] * (1 - top[3]))).concat(1);
    function luminance(c) {
        const ch = x => { const s = x / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
        return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]);
    }
    // The WCAG contrast ratio of two colours (values or [r, g, b, a]): fg over bg, a translucent bg over black (styles.contrast);
    // null when either cannot be read
    function contrastRatio(fg, bg) {
        let b = Array.isArray(bg) ? bg : parseColor(bg);
        let f = Array.isArray(fg) ? fg : parseColor(fg);
        if (!b || !f) return null;
        if (b[3] < 1) b = over(b, [0, 0, 0, 1]);
        if (f[3] < 1) f = over(f, b);
        const l1 = Math.max(luminance(f), luminance(b));
        const l2 = Math.min(luminance(f), luminance(b));
        return Math.round(((l1 + 0.05) / (l2 + 0.05)) * 100) / 100;
    }
    const toHex = c => `#${c.slice(0, 3).map(x => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0')).join('')}`;
    // The colour a style's text sits on (its surface, a translucent one composited over the background), or null
    function surfaceOf(vars) {
        const s = parseColor(vars['--st-surface-1']);
        if (!s) return null;
        if (s[3] >= 1) return s;
        const bg = parseColor(vars['--st-bg-2']);
        return over(s, bg ? (bg[3] < 1 ? over(bg, [0, 0, 0, 1]) : bg) : [0, 0, 0, 1]);
    }

    // How a look moves (read once per scene): entrance length, easing, slide distance and the emphasis colour
    function lookMotion(vars) {
        const num = name => (typeof vars[name] === 'string' && /^\s*\d+(\.\d+)?\s*$/.test(vars[name]) ? parseFloat(vars[name]) : null);
        const ms = num('--st-entrance-ms');
        const shift = num('--st-entrance-shift');
        const rgb = /^\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*$/.exec(vars['--st-focus-rgb'] || '');
        return {
            entranceMs: ms === null ? null : Math.round(Math.min(1200, Math.max(150, ms))),
            ease: validEase(vars['--st-ease']) || DEFAULT_EASE,
            shift: shift === null ? 1 : Math.min(1.5, Math.max(0, shift)),
            focusRgb: rgb && [rgb[1], rgb[2], rgb[3]].every(x => Number(x) <= 255) ? `${Number(rgb[1])}, ${Number(rgb[2])}, ${Number(rgb[3])}` : DEFAULT_FOCUS_RGB
        };
    }
    // An easing the Web Animations API accepts: a keyword, or cubic-bezier() with its x values within 0..1
    function validEase(value) {
        const v = typeof value === 'string' ? value.trim().toLowerCase() : '';
        if (EASE_WORDS.includes(v)) return v;
        const m = /^cubic-bezier\(\s*(-?\d*\.?\d+)\s*,\s*(-?\d*\.?\d+)\s*,\s*(-?\d*\.?\d+)\s*,\s*(-?\d*\.?\d+)\s*\)$/.exec(v);
        if (!m) return null;
        const [x1, y1, x2, y2] = m.slice(1).map(Number);
        return x1 >= 0 && x1 <= 1 && x2 >= 0 && x2 <= 1 && Math.abs(y1) <= 3 && Math.abs(y2) <= 3 ? `cubic-bezier(${x1}, ${y1}, ${x2}, ${y2})` : null;
    }
    const fmt = (x, d = 3) => String(+x.toFixed(d));

    // The lesson's style choices from saved settings or a saved lesson: a known family, a positive version, known overrides
    function cleanOverrides(raw, options = STYLE_OVERRIDES) {
        const out = {};
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
        Object.keys(STYLE_OVERRIDE_LABELS).forEach(key => {
            const v = raw[key];
            const allowed = Array.isArray(options[key]) ? options[key] : STYLE_OVERRIDES[key];
            if (typeof v === 'string' && v !== 'default' && allowed.includes(v)) out[key] = v;
        });
        return out;
    }
    // (overrides belong to a chosen style: a lesson without one keeps its original look, unchanged)
    function cleanStyleChoice(raw, options) {
        const r = raw && typeof raw === 'object' ? raw : {};
        const style = typeof r.style === 'string' && STYLE_IDS.includes(r.style) ? r.style : null;
        const version = style && Number.isInteger(r.style_version) && r.style_version > 0 && r.style_version < 10000 ? r.style_version : null;
        return { style, style_version: version, style_overrides: style ? cleanOverrides(r.style_overrides, options) : {} };
    }

    function loadSettings(storage) {
        try {
            const saved = JSON.parse((storage && storage.getItem(STORAGE_KEY)) || '{}');
            const s = { ...DEFAULTS, ...(saved && typeof saved === 'object' ? saved : {}) };
            return { ...s, ...cleanStyleChoice(s) };
        } catch (e) {
            return { ...DEFAULTS, style_overrides: {} };
        }
    }

    function saveSettings(storage, settings) {
        try { storage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch (e) { /* private mode: the defaults stay */ }
    }

    function isClassic(settings) { return !settings || settings.mode !== 'cinematic'; }

    // Plans from the server applied to the scenes (null in Classic: the scene renders as before)
    function applyPlans(slides, plans) {
        (plans || []).forEach((plan, i) => {
            if (!slides[i]) return;
            if (plan) slides[i].cinematic_plan = plan;
            else delete slides[i].cinematic_plan;
        });
        return slides.map(s => s && s.cinematic_plan);
    }

    // ---- camera maths (the same rules as cinematic.py) ----------------------------------------------------------

    function overlap(a, b) {
        return a.x < b.x + b.w - 1e-9 && b.x < a.x + a.w - 1e-9 && a.y < b.y + b.h - 1e-9 && b.y < a.y + a.h - 1e-9;
    }

    function through(framing, box) {
        const s = 1 / framing.w;
        return { x: (box.x - framing.x) * s, y: (box.y - framing.y) * s, w: box.w * s, h: box.h * s };
    }

    function framingOk(framing, important, forbidden) {
        if (framing.x < -1e-9 || framing.y < -1e-9 || framing.x + framing.w > 1 + 1e-9 || framing.y + framing.w > 1 + 1e-9) return false;
        return important.every(box => {
            const b = through(framing, box);
            if (b.x < EDGE - 1e-9 || b.y < EDGE - 1e-9 || b.x + b.w > 1 - EDGE + 1e-9 || b.y + b.h > 1 - EDGE + 1e-9) return false;
            return !forbidden.some(f => overlap(b, f));
        });
    }

    // The closest safe framing up to maxScale, as near the target's centre as allowed (FULL when none)
    function bestFraming(important, forbidden, target, maxScale, steps = 24) {
        const cx = target ? target.x + target.w / 2 : 0.5;
        const cy = target ? target.y + target.h / 2 : 0.5;
        for (let scale = maxScale; scale > 1.0049; scale -= 0.005) {
            const w = 1 / scale;
            let best = null;
            for (let i = 0; i <= steps; i++) {
                for (let j = 0; j <= steps; j++) {
                    const f = { x: (1 - w) * i / steps, y: (1 - w) * j / steps, w };
                    if (!framingOk(f, important, forbidden)) continue;
                    const d = (f.x + w / 2 - cx) ** 2 + (f.y + w / 2 - cy) ** 2;
                    if (!best || d < best.d) best = { d, f };
                }
            }
            if (best) return { x: +best.f.x.toFixed(4), y: +best.f.y.toFixed(4), w: +best.f.w.toFixed(4) };
        }
        return { ...FULL };
    }

    // The CSS transform that shows a layer at `box` (normalized) as the camera at `framing` sees it
    // (transform-origin 0 0 on the layer; vw / vh units, so a resize keeps it right)
    function cameraTransform(framing, box) {
        const s = 1 / framing.w;
        const tx = box.x * (s - 1) - framing.x * s;
        const ty = box.y * (s - 1) - framing.y * s;
        return `translate(${(tx * 100).toFixed(3)}vw, ${(ty * 100).toFixed(3)}vh) scale(${s.toFixed(4)})`;
    }

    function lerpFraming(a, b, k) {
        return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k, w: a.w + (b.w - a.w) * k };
    }

    function layerOf(plan, id) { return plan && (plan.layers || []).find(l => l.id === id) || null; }

    // The boxes the camera must keep whole, and the areas it must keep them out of
    function cameraConstraints(plan) {
        const layers = (plan && plan.layers) || [];
        const important = layers.filter(l => l.important && l.camera && l.role !== 'full_canvas').map(l => l.face || l.box);
        const title = layers.find(l => l.type === 'title' && !l.camera);
        return { important, forbidden: [SUBTITLES].concat(title ? [title.box] : []) };
    }

    // ---- words for the inspector (Visual Review) -----------------------------------------------------------

    function inspectorFacts(plan) {
        if (!plan) return [];
        const cam = plan.camera || {};
        const layers = (plan.layers || []).filter(l => !['background', 'subtitles'].includes(l.type));
        const summary = compositionSummary(plan);
        return [
            ...(summary ? [['Composition', `${summary.label} · ${summary.template}`], ['Scene', `${summary.purpose || '—'} · ${summary.density || '—'} density`]] : []),
            ['Template', plan.template_label || TEMPLATES[plan.template] || plan.template],
            ['Presenter', plan.presenter && plan.presenter.shown ? `${plan.presenter.presenter_id} · ${plan.presenter.side}` : 'Hidden'],
            ['Camera', `${SHOTS[cam.shot] || cam.shot} · ${(CAMERA[cam.movement] || cam.movement).toLowerCase()}`
                + (cam.target && cam.movement !== 'static' ? ` → ${cam.target}` : '')],
            ['Transition', TRANSITIONS[(plan.transition || {}).in] || (plan.transition || {}).in],
            ['Background', BACKGROUNDS[(plan.background || {}).type] || ({ canvas: 'The visual fills the frame' })[(plan.background || {}).type]
                || (plan.background || {}).type],
            ['Duration', `${plan.duration} s`],
            ['Layers', layers.map(l => `${l.id} ${l.start}s`).join(' · ')]
        ];
    }

    // Phase 21: ?visualDebug (a boolean, or a function asked each time): the server's reason why AI help was not available
    // (it names the provider) is shown in brackets; otherwise plain words only
    const debugOn = debug => !!(typeof debug === 'function' ? debug() : debug);
    const serverReason = (error, debug) => (debugOn(debug) && typeof error === 'string' && error ? ` (${error})` : '');

    // How the scene's composition was decided (Phase 14): by the composer's rules, with the AI's suggestion, by the user,
    // by the screenplay, or the safe fallback, with its short reasons and what was adjusted (never model reasoning).
    // options.debug: the server's reason when AI-assisted composition is not available
    function compositionSummary(plan, { debug = false } = {}) {
        const c = (plan && plan.composition) || null;
        if (!c) return null;
        const ai = c.ai || null;
        const aiNote = !ai ? null : ({ ok: 'AI suggestion used', repaired: 'AI suggestion used (after one repair)', invalid: 'AI suggestion rejected (not valid); the rules decided',
            failed: 'AI model failed; the rules decided', timeout: 'AI model too slow; the rules decided',
            skipped: 'AI not asked for this scene (a lesson asks about 6 scenes at most); the rules decided', unavailable: `AI-assisted composition is not available here${serverReason(ai.error, debug)}; the rules decided` })[ai.status] || null;
        const automatic = ['rules', 'ai', 'phase13'].includes(c.source);
        return { label: `${SOURCES[c.source] || c.source}${automatic ? ' ✓' : ''}`, source: c.source, automatic, template: plan.template_label || TEMPLATES[plan.template] || plan.template,
            reasons: (c.reasons || []).slice(0, 4), repairs: c.repairs || [], ai: aiNote, locked: c.locked || [],
            chosen: Array.isArray(c.chosen) ? c.chosen : (c.locked || []), purpose: c.intent && c.intent.purpose,
            density: c.intent && c.intent.density };
    }

    // What an AI direction model did for the scene, in the composition's words (never its reasoning or its raw answer)
    // options.debug: the server's reason when AI-assisted direction is not available
    function directionAiNote(ai, source, { debug = false } = {}) {
        if (!ai || !ai.status) return null;
        if (ai.status === 'ok' || ai.status === 'repaired') {
            if (source === 'ai') return ai.status === 'ok' ? 'AI suggestion used' : 'AI suggestion used (after one repair)';
            return source === 'user' ? 'AI suggestion checked; your choices come first'
                : source === 'fallback' ? 'AI suggestion checked; a safe direction was used' : 'AI suggestion checked; the rules fitted this scene better';
        }
        return ({ invalid: 'AI suggestion rejected (not valid); the rules decided', failed: 'AI model failed; the rules decided',
            timeout: 'AI model too slow; the rules decided', skipped: 'AI not asked for this scene (a lesson asks about 6 scenes at most); the rules decided',
            unavailable: `AI-assisted direction is not available here${serverReason(ai.error, debug)}; the rules decided` })[ai.status] || null;
    }

    // How the scene's visual direction was decided (Phase 15), in words for teachers: the strategy, the learning goal, what
    // teaches, the presenter's role, motion, camera, the learner's focus, short reasons and notes. The codes come from the plan's
    // direction summary (plan.direction); the lesson's own words (goal, focus) from scene.visual_direction when it is the one the
    // composition followed. Never a score, the confidence or model reasoning. options.debug: the server's reason when
    // AI-assisted direction is not available.
    function directionSummary(scene, plan, { debug = false } = {}) {
        const stored = scene && scene.visual_direction && typeof scene.visual_direction === 'object' ? scene.visual_direction : null;
        const d = plan && plan.direction && typeof plan.direction === 'object' ? plan.direction : null;
        const full = stored && (!d || !d.fingerprint || !stored.fingerprint || stored.fingerprint === d.fingerprint) ? stored : null;
        if (!d && !full) return null;
        const pick = (fromSummary, fromFull) => (d && d[fromSummary] !== undefined && d[fromSummary] !== null ? d[fromSummary] : fromFull);
        const source = pick('source', full && full.source) || 'rules';
        const automatic = source === 'rules' || source === 'ai';
        const strategy = pick('strategy', full && full.strategy);
        const strategyLabel = (d && d.label) || (full && full.strategy_label) || STRATEGY_LABELS[strategy] || String(strategy || '').replace(/_/g, ' ');
        const primaryKind = pick('primary_kind', full && full.primary_visual && full.primary_visual.kind);
        const primary = (d && d.primary) || (full && full.primary_visual && full.primary_visual.label) || VISUAL_LABELS[primaryKind] || '';
        const role = pick('presenter', full && full.presenter && full.presenter.role);
        const motion = pick('motion', full && full.motion_intent);
        const camera = pick('camera', full && full.camera_intent);
        const review = scene && scene.visual_review && scene.visual_review.direction;
        const chosen = review && review.status === 'changed' && review.overrides && typeof review.overrides === 'object' ? review.overrides : {};
        const words = (map, code) => (code ? map[code] || String(code).replace(/_/g, ' ') : '');
        const goal = full && full.learning_goal ? String(full.learning_goal) : '';
        const focus = full && full.learner_focus ? String(full.learner_focus) : '';
        const facts = [['Decided', DIRECTION_SOURCES[source] || source, 'source'], ['Goal', goal, 'goal'], ['What teaches', primary, 'what'],
            ['Presenter', words(PRESENTER_ROLES, role), 'presenter'], ['Motion', words(MOTION_INTENTS, motion), 'motion'],
            ['Camera', words(CAMERA_INTENTS, camera), 'camera'], ['Focus', focus, 'focus']].filter(([, text]) => text);
        return {
            label: `${strategyLabel}${automatic ? ' ✓' : ''}`, strategyLabel, source, sourceText: DIRECTION_SOURCES[source] || source, automatic,
            goal, focus, primary, presenter: words(PRESENTER_ROLES, role), motion: words(MOTION_INTENTS, motion), camera: words(CAMERA_INTENTS, camera),
            facts, reasons: ((d && d.reasons) || []).filter(r => typeof r === 'string').slice(0, 3),
            notes: ((d ? d.notes : full && full.notes) || []).filter(n => typeof n === 'string').slice(0, 3),
            ai: directionAiNote((d && d.ai) || (full && full.ai), source, { debug }),
            locked: ((d ? d.locked : full && full.locked) || []).slice(),
            current: { strategy, primary_visual: primaryKind, presenter_role: role, camera_intent: camera, motion_intent: motion, prefer: chosen.prefer || '' }
        };
    }

    // ---- the scene's synchronization in words (Phase 16, Visual Review) ----------------------------------------------
    // The plan (plan.sync) only points at the lesson ({kind, index}); the words shown are read from the scene itself: its board
    // HTML (walked like the server's board reader, scene_intent.board_facts), its title, its labels. Read-only, no DOM.
    const SYNC_ATTENTION = { PRESENTER: 'presenter', VISUAL: 'visual', TEXT: 'text', FORMULA: 'formula', CODE: 'code', RESULT: 'result' };
    const SYNC_SENTENCES = { look: 'when the narration points at it', formula: 'when the narration states the formula',
        output: 'when the narration mentions the result', 'key moment': 'at the key teaching moment', 'after the visual': 'once the visual moment has passed',
        summary: 'when the narration sums up' };
    const SYNC_PRESENTER = { presenter_point: 'the presenter points', presenter_explain: 'the presenter explains', presenter_pause: 'the presenter pauses',
        presenter_summarize: 'the presenter sums up', presenter_emphasis: 'the presenter stresses the point' };
    const SYNC_FIXED = { formula: 'the formula', output: "the code's output", visual: 'the picture', summary: 'the summary' };
    const SYNC_AI = { ok: 'AI suggestion used', repaired: 'AI suggestion used (after one repair)', invalid: 'AI suggestion rejected (not valid); the rules decided',
        failed: 'AI model failed; the rules decided', timeout: 'AI model too slow; the rules decided',
        skipped: 'AI not asked for this scene (a lesson asks about 6 scenes at most); the rules decided' };
    const MAX_KEY_MOMENTS = 8;
    const VOID_TAGS = ['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'];
    const COMPARE_TITLE = /\b(vs\.?|versus|compared?|comparison|differences?|contrast)\b/i;
    const COMPARE_SPLIT = /\s+(?:vs\.?|versus|compared (?:with|to)|and)\s+/i;

    const shortText = (text, n) => {
        const t = String(text || '').replace(/\s+/g, ' ').trim();
        return t.length <= n ? t : `${t.slice(0, n - 1).trimEnd()}…`;
    };
    const normTerm = t => String(t || '').toLowerCase().replace(/[^a-z0-9α-ω]+/g, ' ').trim();
    function decodeEntities(text) {
        const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
        return String(text).replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
            if (e[0] !== '#') return named[e.toLowerCase()] !== undefined ? named[e.toLowerCase()] : m;
            const code = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
            return code > 0 && code < 0x110000 ? String.fromCodePoint(code) : m;
        });
    }

    // What a reader sees on the board (the same structures as the server's board reader): the top-level list items, the first
    // table row's cells, key terms (.keyword / strong / b), the definition's text, and how many top-level lists and tables
    function boardWords(html) {
        const out = { items: [], headers: [], keywords: [], definition: '', topLists: 0, tables: 0 };
        const stack = [];
        let captures = [];
        let rows = 0;
        const finish = () => {
            const depth = stack.length;
            const still = [];
            captures.forEach(c => {
                if (c.depth <= depth) { still.push(c); return; }
                const text = c.buf.join('').replace(/\s+/g, ' ').trim();
                if (c.key === 'item' && text) out.items.push(text.slice(0, 160));
                else if (c.key === 'header') out.headers.push(text.slice(0, 60));
                else if (c.key === 'keyword' && text && text.length <= 40) out.keywords.push(text);
                else if (c.key === 'definition') out.definition = text.slice(0, 300);
            });
            captures = still;
        };
        const re = /<!--[\s\S]*?(?:-->|$)|<(\/?)([a-zA-Z][\w:-]*)((?:[^<>"']|"[^"]*"|'[^']*')*)>|([^<]+)|</g;
        const source = String(html || '').slice(0, 200000);
        let m;
        while ((m = re.exec(source))) {
            if (m[2]) {
                const tag = m[2].toLowerCase();
                if (m[1]) { // a closing tag: closes up to its own (a stray one is ignored)
                    const at = stack.map(s => s.tag).lastIndexOf(tag);
                    if (at >= 0) { stack.length = at; finish(); }
                    continue;
                }
                const cm = /(?:^|\s)class\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(m[3] || '');
                const cls = cm ? (cm[1] || cm[2] || cm[3] || '') : '';
                if (['li', 'ul', 'ol', 'p', 'div', 'br', 'tr', 'td', 'th'].includes(tag)) captures.forEach(c => c.buf.push(' '));
                const depth = stack.length + 1;
                if (tag === 'li' && !stack.some(s => s.tag === 'li')) captures.push({ key: 'item', depth, buf: [] });
                if ((tag === 'th' || tag === 'td') && rows === 1) captures.push({ key: 'header', depth, buf: [] });
                if ((tag === 'span' && cls.includes('keyword')) || tag === 'strong' || tag === 'b') captures.push({ key: 'keyword', depth, buf: [] });
                if (cls.includes('definition') && !out.definition) captures.push({ key: 'definition', depth, buf: [] });
                if ((tag === 'ul' || tag === 'ol') && !stack.some(s => s.tag === 'ul' || s.tag === 'ol')) out.topLists += 1;
                if (tag === 'table') out.tables += 1;
                if (tag === 'tr') rows += 1;
                stack.push({ tag });
                if (VOID_TAGS.includes(tag) || /\/\s*$/.test(m[3] || '')) { stack.pop(); finish(); }
            } else if (m[4]) {
                if (stack.some(s => s.tag === 'pre' || s.tag === 'script' || s.tag === 'style')) continue; // code is not prose
                const text = decodeEntities(m[4]);
                captures.forEach(c => c.buf.push(text));
            }
        }
        stack.length = 0;
        finish(); // an unclosed element still gives its words
        return out;
    }

    // The scene's own words a concept reference points at (null when the scene has none: a generic phrase is shown instead)
    function sceneWordsFor(scene, plan, concept, board) {
        if (!concept || typeof concept !== 'object' || !Number.isInteger(concept.index) || concept.index < 0) return null;
        const n = concept.index;
        const kind = concept.kind;
        if (SYNC_FIXED[kind]) return SYNC_FIXED[kind];
        if (kind === 'topic') return scene.title ? shortText(scene.title, 60) : 'the topic';
        if (kind === 'step' || kind === 'event' || kind === 'point') return board().items[n] ? shortText(board().items[n], 60) : null;
        if (kind === 'term') {
            // the scene's key terms in the server's order (the defined term, the board's key terms, the screenplay's keywords)
            const words = board();
            let defined = null;
            words.definition.replace(/([.!?])\s+/g, '$1\u0000').split('\u0000').some(sentence => {
                const hit = /^(?:an?\s+|the\s+)?([A-Z][\w\- ]{1,40}?)\s+(?:is|are|refers to|means|is defined as)\b/.exec(sentence);
                if (hit) defined = hit[1].trim();
                return !!hit;
            });
            const screenplay = scene.visual && Array.isArray(scene.visual.keywords) ? scene.visual.keywords.slice(0, 20) : [];
            const terms = [];
            const seen = new Set();
            [defined, ...Array.from(new Set(words.keywords)).slice(0, 10), ...screenplay].forEach(t => {
                if (terms.length >= 8 || typeof t !== 'string' || t.length < 1 || t.length > 40 || !normTerm(t) || seen.has(normTerm(t))) return;
                seen.add(normTerm(t));
                terms.push(t.trim());
            });
            return terms[n] ? shortText(terms[n], 40) : null;
        }
        if (kind === 'side') {
            const words = board();
            const heads = words.headers.filter(Boolean);
            const title = String(scene.title || '').replace(/\s+/g, ' ').slice(0, 200).trim();
            let sides = [];
            if (heads.length >= 2) {
                const feature = heads.length >= 3 && /^(feature|aspect|property|criteria|basis|point|parameter)s?$/i.test(heads[0]);
                sides = feature ? heads.slice(1, 3) : heads.slice(0, 2);
            } else if (COMPARE_TITLE.test(title)) {
                const parts = title.replace(/^(comparing|comparison of|differences? between)\s+/i, '').split(COMPARE_SPLIT)
                    .map(p => p.replace(/^[\s:?]+|[\s:?]+$/g, '')).filter(Boolean);
                sides = parts.length >= 2 ? parts.slice(0, 2) : [];
            } else if (words.topLists === 2 && words.tables === 0) {
                sides = ['the first list', 'the second list'];
            }
            return sides[n] ? shortText(sides[n], 40) : null;
        }
        if (kind === 'label') {
            // exactly the chip renderLabels draws: the screenplay's label, or the visual direction's annotation
            const layer = layerOf(plan, 'labels');
            const item = layer && Array.isArray(layer.items) ? layer.items[n] : null;
            const source = item && item.source && typeof item.source === 'object' ? item.source : null;
            if (!source) return null;
            const labels = (scene.composition && Array.isArray(scene.composition.labels)) ? scene.composition.labels : [];
            const annotations = (scene.visual_direction && Array.isArray(scene.visual_direction.annotations)) ? scene.visual_direction.annotations : [];
            const raw = source.kind === 'direction' ? annotations[source.index] : labels[source.index];
            const text = typeof raw === 'string' ? raw : raw && raw.text;
            return text ? shortText(String(text).trim().slice(0, 80), 60) : null;
        }
        return null;
    }

    // What anchors a moment, in words (the plan's vocabulary is mapped; a value the page does not know is never shown)
    function syncAnchorWords(anchor) {
        const a = anchor && typeof anchor === 'object' ? anchor : {};
        if (a.kind === 'sync') return Number.isInteger(a.value) && a.value > 1 ? `at [SYNC] beat ${a.value}` : 'at the [SYNC] beat';
        if (a.kind === 'concept') return 'when the narration names it';
        if (a.kind === 'sentence') return SYNC_SENTENCES[a.value] || 'at a matching sentence of the narration';
        if (a.kind === 'fraction') return a.value === 'planned start' ? 'at its planned moment' : 'a share of the scene';
        if (a.kind === 'after') return 'with the moment it follows';
        if (a.kind === 'segment') return Number.isInteger(a.value) && a.value >= 0 ? `when narration part ${a.value + 1} starts` : 'when a narration part starts';
        return 'at its planned moment';
    }

    // What happens at a moment, in words
    function syncWhat(ev, words) {
        const type = ev.type;
        if (SYNC_PRESENTER[type]) return SYNC_PRESENTER[type];
        if (type === 'visual_highlight') return 'the picture is highlighted';
        if (type === 'diagram_focus') return words && words !== 'the picture' ? `the diagram focuses on ${words}` : 'the diagram is highlighted';
        if (type === 'formula_emphasis') return 'the formula is highlighted';
        if (type === 'text_reveal') return `${words || "the code's output"} appears`;
        if (type === 'text_emphasis') return words ? `${words} is focused` : 'a part of the board is focused';
        if (type === 'label_enter') return words ? `${words} appears as a label` : 'a label appears';
        if (type === 'label_exit') return words ? `the ${words} label leaves` : 'a label leaves';
        if (type === 'camera_focus') return 'the camera leans in';
        if (type === 'camera_return') return 'the camera steps back to the whole scene';
        return null;
    }

    // The scene's synchronization in plain words for Visual Review, or null without one: when each moment happens (about how
    // many seconds in, and what anchors it), what happens, the attention flow, the timing source, notes and the AI's part.
    // Never a score or an internal metric's name; the lesson's words come from the scene, never from the plan.
    // options.debug: the server's reason when AI-assisted timing is not available
    function syncSummary(scene, plan, { debug = false } = {}) {
        const s = syncPlanOf(plan);
        if (!s) return null;
        const sc = scene && typeof scene === 'object' ? scene : {};
        let words = null;
        const board = () => words || (words = boardWords(typeof sc.html === 'string' ? sc.html : ''));
        const moments = syncEvents(s).map((rec, order) => {
            const ev = rec.ev;
            const target = ev.target || {};
            let concept = ev.concept && typeof ev.concept === 'object' ? ev.concept : null;
            if (!concept && SYNC_TYPES[ev.type] === 'labels' && Number.isInteger(target.item)) concept = { kind: 'label', index: target.item };
            if (!concept && ev.type === 'text_emphasis' && LIST_KINDS.includes(target.kind)) concept = { kind: 'step', index: Number.isInteger(target.index) ? target.index : 0 };
            const what = syncWhat(ev, sceneWordsFor(sc, plan, concept, board));
            const at = rec.estimate === null ? null : Math.round(rec.estimate * 10) / 10;
            return { at, when: at === null ? '' : `${at.toFixed(1)} s`, anchor: syncAnchorWords(ev.anchor), what, type: ev.type, layer: SYNC_TYPES[ev.type],
                priority: bounded(ev.priority, 1, 5, 3), order };
        }).filter(m => m.what);
        moments.sort((a, b) => (a.at === null ? Infinity : a.at) - (b.at === null ? Infinity : b.at) || a.order - b.order);
        moments.forEach((m, i) => { m.rank = i; });
        // the key moments: the most important ones when there are more than the inspector lists, still in time order
        const key = moments.length <= MAX_KEY_MOMENTS ? moments.slice()
            : moments.slice().sort((a, b) => a.priority - b.priority || a.rank - b.rank).slice(0, MAX_KEY_MOMENTS).sort((a, b) => a.rank - b.rank);
        const flow = [];
        (Array.isArray(s.attention) ? s.attention : []).forEach(a => {
            const w = a && SYNC_ATTENTION[a.state];
            if (w && flow[flow.length - 1] !== w) flow.push(w);
        });
        const metrics = s.metrics && typeof s.metrics === 'object' ? s.metrics : {};
        const count = k => (Number.isInteger(metrics[k]) && metrics[k] > 0 ? metrics[k] : 0);
        const notes = [];
        if (count('unsupported')) notes.push('the presenter cannot act at a moment here; the visual carries the emphasis');
        if (count('fallbacks')) notes.push(`${count('fallbacks')} moment${count('fallbacks') === 1 ? '' : 's'} placed by a share of the scene`);
        if (s.fallback === 'simplified') notes.push('the scene is short, so only its most important moments are kept');
        const ai = s.ai && typeof s.ai === 'object' && s.ai.status
            ? (s.ai.status === 'unavailable'
                ? `AI-assisted timing is not available here${serverReason(s.ai.error, debug)}; the rules decided`
                : SYNC_AI[s.ai.status] || null)
            : null;
        const strip = m => ({ at: m.at, when: m.when, anchor: m.anchor, what: m.what, type: m.type, layer: m.layer });
        return {
            source: s.timing_source === 'narration' ? 'narration' : 'estimate',
            timing: s.timing_source === 'narration' ? 'follows the narration audio' : 'estimated (no narration audio)',
            moments: moments.map(strip), keyMoments: key.map(strip), attention: flow.join(' → '), notes, ai,
            endHold: bounded(s.end_hold, 0, MAX_END_HOLD, 0) > 0 ? 'the result stays on screen a moment longer' : null
        };
    }

    // ---- the scene's style in words (Phase 17, Visual Review) --------------------------------------------------------
    // The scene's effective style from its plan (plan.style.look), or null for a plan without one. swatch: the style's
    // background, surface, text, heading and accent as #rrggbb (translucent tokens composited over what lies behind them, as
    // seen); legacy: the lesson chose no style (a hidden legacy variant, or settings without a style when they are given).
    // scene is accepted for symmetry with syncSummary (the look already carries the scene's own Visual Review choices).
    function styleSummary(scene, plan, settings) {
        const look = lookOf(plan);
        if (!look) return null;
        const { vars } = styleVars(look.css);
        const prefs = look.prefs && typeof look.prefs === 'object' ? look.prefs : {};
        const str = (v, n) => (typeof v === 'string' && v ? v.slice(0, n) : null);
        const pairs = o => (o && typeof o === 'object' && !Array.isArray(o)
            ? Object.keys(o).filter(k => k.length <= 40 && typeof o[k] === 'string' && o[k].length <= 40).sort().map(k => [k, o[k]]) : []);
        const family = str(look.family, 40);
        const known = STYLE_FAMILIES.find(f => f.id === family);
        const black = [0, 0, 0, 1];
        const solid = (name, under) => {
            const c = parseColor(vars[name]);
            return c ? (c[3] < 1 ? over(c, under) : c) : null;
        };
        const bg1 = solid('--st-bg-1', black);
        const bg2 = solid('--st-bg-2', black) || bg1 || black;
        const surface = solid('--st-surface-1', bg2);
        const under = surface || bg2;
        const swatch = [bg1, surface, solid('--st-text', under), solid('--st-heading', under), solid('--st-accent', under)].filter(Boolean).map(toHex);
        const tone = TONES.includes(look.tone) ? look.tone : (TONES.includes(prefs.tone) ? prefs.tone : null);
        const legacy = look.legacy === true || !!look.variant || (settings && typeof settings === 'object' ? !settings.style : false);
        return {
            id: str(look.id, 60), label: str(look.label, 60) || (known && known.label) || family, family,
            version: Number.isInteger(look.version) ? look.version : null, tone, fingerprint: str(look.fingerprint, 32),
            overrides: pairs(look.overrides), sceneOverrides: pairs(look.scene_overrides),
            adjustments: (Array.isArray(look.adjustments) ? look.adjustments : []).filter(a => typeof a === 'string').slice(0, 8).map(a => a.slice(0, 160)),
            swatch, legacy
        };
    }

    // The choices Visual Review offers for the direction (the server's vocabulary when loaded, else the same words here)
    function directionOptions(status) {
        const st = status && typeof status === 'object' ? status : {};
        const fromList = (list, map) => Object.fromEntries((Array.isArray(list) && list.length ? list : Object.keys(map))
            .map(code => [code, map[code] || String(code).replace(/_/g, ' ')]));
        return {
            strategy: st.strategies && Object.keys(st.strategies).length ? { ...st.strategies } : { ...STRATEGY_LABELS },
            primary_visual: st.visuals && Object.keys(st.visuals).length ? { ...st.visuals } : { ...VISUAL_LABELS },
            presenter_role: fromList(st.presenter_roles, PRESENTER_ROLES),
            camera_intent: fromList(st.camera, CAMERA_INTENTS),
            motion_intent: fromList(st.motion, MOTION_INTENTS),
            prefer: fromList((st.prefer || []).filter(p => p !== 'auto'), PREFER)  // "auto" is the select's own "Automatic"
        };
    }

    // Each element of the scene with its state: ✓ ready, ● generating / not made yet, ⚠ needs attention
    function elementStatuses(scene, plan, { activeRuns = [] } = {}) {
        if (!plan) return [];
        const rows = [];
        const bg = plan.background || {};
        const bgRun = activeRuns.find(r => r.slot === 'background' && ['queued', 'running', 'recovering'].includes(r.status));
        rows.push(['Background', bgRun ? 'generating' : bg.fallback ? 'warning' : 'ready',
            bgRun ? 'AI background generating…' : bg.fallback ? 'Clean gradient (the chosen background is not available)' : (BACKGROUNDS[bg.type] || bg.type)]);
        const presenter = plan.presenter || {};
        const pPlan = scene.presenter_plan || {};
        if (presenter.shown) {
            const needsClip = (presenter.type === 'ai_avatar' || presenter.type === 'custom') && !(pPlan.media && pPlan.media.url);
            rows.push(['Presenter', needsClip ? 'warning' : 'ready', needsClip ? 'No presenter clip yet' : presenter.presenter_id]);
        } else {
            rows.push(['Presenter', 'ready', 'Not in this scene']);
        }
        const visual = layerOf(plan, 'visual');
        const vPlan = scene.visual_plan && (scene.visual_plan.side || scene.visual_plan.main);
        if (visual) {
            const bad = vPlan && (vPlan.error || vPlan.requires_generation);
            rows.push(['Educational visual', bad ? 'warning' : 'ready', bad ? (vPlan.error ? 'Visual unavailable' : 'Not generated yet') : (visual.role || 'visual')]);
        } else if ((plan.warnings || []).some(w => w.includes('visual'))) {
            rows.push(['Educational visual', 'warning', 'Not shown by this template']);
        }
        const board = layerOf(plan, 'board');
        const title = layerOf(plan, 'title');
        if (board || title) rows.push(['Text', 'ready', [title ? 'title' : '', board ? board.role : ''].filter(Boolean).join(' + ')]);
        return rows;
    }

    // ---- API ------------------------------------------------------------------------------------------------

    class CinematicApi {
        constructor({ fetch }) { this.fetch = fetch; }

        async call(url, init = {}) {
            const res = await this.fetch(url, { ...init, headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(init.headers || {}) } });
            const data = await res.json().catch(() => ({}));
            if (!res.ok && res.status !== 202) {
                throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : `error ${res.status}`), { status: res.status });
            }
            return Object.assign(data || {}, { httpStatus: res.status });
        }

        vocabulary() { return this.call('/api/cinematic'); }
        // extra: { concept_map } (the lesson's concept map, when it has one)
        plan(scenes, settings, projectId, extra = {}) {
            return this.call('/api/cinematic/plan', { method: 'POST', body: JSON.stringify({ scenes, settings, project_id: projectId || undefined,
                ...lessonExtra(extra) }) });
        }
        review(body) { return this.call('/api/cinematic/review', { method: 'POST', body: JSON.stringify(body) }); }
        regenerate(body) { return this.call('/api/cinematic/regenerate', { method: 'POST', body: JSON.stringify(body) }); }
        background(body) { return this.call('/api/cinematic/background', { method: 'POST', body: JSON.stringify(body) }); }
        // The visual direction (Phase 15): data only, nothing is generated. extra: { project_id, concept_map }
        direction(scenes, settings, extra = {}) {
            return this.call('/api/cinematic/direction', { method: 'POST', body: JSON.stringify({ scenes, settings, ...lessonExtra(extra) }) });
        }
        regenerateDirection(scenes, index, settings, extra = {}) {
            return this.call('/api/cinematic/direction/regenerate', { method: 'POST',
                body: JSON.stringify({ scenes, scene_index: index, settings, ...lessonExtra(extra) }) });
        }
        // body: { project_id, scene_index, action: change | reset, overrides?, scene?, settings, concept_map? }
        reviewDirection(body) { return this.call('/api/cinematic/direction/review', { method: 'POST', body: JSON.stringify(body) }); }
        // The lesson's quality report (Phase 18, quality.py): read-only on the server (nothing is saved, generated or approved).
        // conceptMap: the lesson's concept map (a list, or { concept_map: [...] }), sent only when known
        quality(scenes, settings, projectId, conceptMap) {
            const map = Array.isArray(conceptMap) ? conceptMap : (conceptMap && Array.isArray(conceptMap.concept_map) ? conceptMap.concept_map : null);
            return this.call('/api/quality/lesson', { method: 'POST', body: JSON.stringify({ scenes, settings, project_id: projectId || undefined,
                ...(map ? { concept_map: map } : {}) }) });
        }
    }

    // The saved lesson and its concept map, sent only when known (an old lesson has no concept map)
    function lessonExtra(extra) {
        const out = {};
        if (extra && extra.project_id) out.project_id = extra.project_id;
        if (extra && Array.isArray(extra.concept_map)) out.concept_map = extra.concept_map;
        return out;
    }

    // ---- the stage: renders a scene's composition ------------------------------------------------------------

    // Board representations the visual direction may ask for (Phase 15) and the base board each restyles (cinematic.BOARD_STYLES)
    const BOARD_BASE = { steps: 'body', timeline: 'body', key_points: 'body', code_output: 'code' };
    const DATE = /\b(?:1[0-9]{3}|20[0-9]{2})s?\b|\b\d{1,2}(?:st|nd|rd|th)\s+century\b|\b\d+\s*(?:BCE|BC|CE|AD)\b/i;
    const CAMERA_TARGETS = { board: '.lecture-overlay-zone', visual: '.dynamic-side-zone', presenter: '#presenter-layer', labels: '#cine-labels',
        background: '#cine-background', title: '#cine-title' };
    const FORMULA_SELECTOR = '#slide-content-container mjx-container[display="true"], #slide-content-container .formula-block, #slide-content-container .math-block';

    // ---- Phase 16: the synchronization plan (plan.sync, sync_director.py; scratchpad/phase16_contract.md) ----------------
    // Each event type and the one layer it acts on; anything else is ignored (the server validates, the page is defensive)
    const SYNC_TYPES = { presenter_point: 'presenter', presenter_explain: 'presenter', presenter_pause: 'presenter', presenter_summarize: 'presenter',
        presenter_emphasis: 'presenter', visual_highlight: 'visual', diagram_focus: 'visual', text_emphasis: 'board', text_reveal: 'board',
        formula_emphasis: 'board', label_enter: 'labels', label_exit: 'labels', camera_focus: 'camera', camera_return: 'camera' };
    // Board targets are fixed kinds, never selectors: step / point / event = the n-th top-level list item, column = the n-th table
    // column, side = the n-th of two lists, term = the n-th key term, output = the paragraph after the code, formula = the formula
    const BOARD_KINDS = ['step', 'point', 'event', 'column', 'side', 'term', 'output', 'formula'];
    const LIST_KINDS = ['step', 'point', 'event'];
    const MAX_SYNC_EVENTS = 24;
    const MAX_END_HOLD = 0.7;

    const bounded = (v, lo, hi, dflt) => (typeof v === 'number' && isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt);

    // The scene's synchronization when it is one this page understands (null: the Phase 13 timeline plays, as before)
    function syncPlanOf(plan) {
        const s = plan && plan.sync;
        return s && typeof s === 'object' && s.version === 1 && Array.isArray(s.events) ? s : null;
    }

    function syncPosition(at) {
        return at && typeof at === 'object' && Number.isInteger(at.segment) && at.segment >= 0 && typeof at.ratio === 'number' && isFinite(at.ratio)
            ? { segment: at.segment, ratio: Math.min(1, Math.max(0, at.ratio)) } : null;
    }

    // An estimated second of the scene as a narration position (from the narration's own estimates), for an event without one
    function positionAtEstimate(segments, seconds) {
        if (!segments.length || !(seconds >= 0)) return null;
        for (const seg of segments) {
            const start = bounded(seg.start_estimate, 0, 3600, null);
            const est = bounded(seg.estimate, 0, 3600, 0);
            if (start === null || !est || !Number.isInteger(seg.index)) return null;
            if (seconds <= start + est + bounded(seg.pause_after, 0, 60, 0)) return { segment: seg.index, ratio: Math.min(1, Math.max(0, (seconds - start) / est)) };
        }
        const last = segments[segments.length - 1];
        return Number.isInteger(last.index) ? { segment: last.index, ratio: 1 } : null;
    }

    // The plan's events the page can play: known type, the right layer, a fixed board kind, a narration position or an estimate
    // How far before its word a synchronized event may fire: a frame (the narration's own clock is precise)
    const SYNC_LEAD = 0.06;

    function syncEvents(sync) {
        const segments = sync.narration && Array.isArray(sync.narration.segments) ? sync.narration.segments.filter(s => s && typeof s === 'object') : [];
        const out = [];
        sync.events.slice(0, MAX_SYNC_EVENTS).forEach((ev, i) => {
            if (!ev || typeof ev !== 'object' || !SYNC_TYPES[ev.type]) return;
            const target = ev.target && typeof ev.target === 'object' ? ev.target : null;
            if (!target || target.layer !== SYNC_TYPES[ev.type]) return;
            if (target.layer === 'board' && !BOARD_KINDS.includes(target.kind)) return;
            if ((ev.type === 'text_reveal' && target.kind !== 'output') || (ev.type === 'formula_emphasis' && target.kind !== 'formula')) return;
            const at = syncPosition(ev.at);
            let estimate = bounded(ev.estimate, 0, 3600, null);
            if (estimate === null && at) {
                const seg = segments.find(s => s.index === at.segment);
                estimate = seg ? bounded(seg.start_estimate, 0, 3600, 0) + at.ratio * bounded(seg.estimate, 0, 3600, 0) : null;
            }
            const pos = at || (estimate !== null ? positionAtEstimate(segments, estimate) : null);
            if (!pos && estimate === null) return; // it could never be placed
            out.push({ id: typeof ev.id === 'string' && ev.id ? ev.id : `#${i}`, ev, pos, estimate, tolerance: bounded(ev.tolerance, 0, 1.5, 0.25),
                deps: [], done: false });
        });
        const ids = new Set(out.map(r => r.id));
        out.forEach(r => { r.deps = (Array.isArray(r.ev.depends_on) ? r.ev.depends_on : []).filter(d => ids.has(d) && d !== r.id); });
        return out;
    }

    // CSS 'ease-in-out' (cubic-bezier(0.42, 0, 0.58, 1)) at progress k: where a camera move is when another one starts
    function easeInOut(k) {
        const bez = (t, p1, p2) => 3 * (1 - t) * (1 - t) * t * p1 + 3 * (1 - t) * t * t * p2 + t * t * t;
        let lo = 0;
        let hi = 1;
        for (let i = 0; i < 32; i++) {
            const mid = (lo + hi) / 2;
            if (bez(mid, 0.42, 0.58) < k) lo = mid; else hi = mid;
        }
        return bez((lo + hi) / 2, 0, 1);
    }

    // The board's own elements, walked the same way in the page and in the tests' DOM stand-in (no selector from the plan)
    const tagOf = node => String((node && (node.tagName || node.tag)) || '').toLowerCase();
    const elementKids = node => Array.from((node && node.children) || []).filter(c => c && c.nodeType !== 3 && tagOf(c) && tagOf(c) !== '#text');
    const parentOf = node => (node && (node.parentElement || node.parent || node.parentNode)) || null;
    function descendants(root) {
        const out = [];
        const visit = node => elementKids(node).forEach(c => { out.push(c); visit(c); });
        visit(root);
        return out;
    }
    function nextElement(node) {
        if (node.nextElementSibling !== undefined) return node.nextElementSibling;
        const kids = elementKids(parentOf(node));
        const i = kids.indexOf(node);
        return i >= 0 ? kids[i + 1] || null : null;
    }
    // Top-level list items (not inside another item), or top-level lists (not inside another list)
    function topLevel(root, what) {
        const out = [];
        const visit = (node, inside) => elementKids(node).forEach(c => {
            const t = tagOf(c);
            const hit = what === 'li' ? t === 'li' : (t === 'ol' || t === 'ul');
            if (hit && !inside) out.push(c);
            visit(c, inside || hit || (what === 'list' && t === 'li'));
        });
        visit(root, false);
        return out;
    }
    function tableColumn(root, n) {
        const table = descendants(root).find(c => tagOf(c) === 'table');
        const cells = [];
        const visit = node => elementKids(node).forEach(c => {
            const t = tagOf(c);
            if (t === 'table') return; // a table inside the table has columns of its own
            if (t === 'tr') {
                const cell = elementKids(c).filter(x => tagOf(x) === 'th' || tagOf(x) === 'td')[n];
                if (cell) cells.push(cell);
                return;
            }
            visit(c);
        });
        if (table) visit(table);
        return cells;
    }
    // Key terms (.keyword / strong) in the definition when it has some, else on the whole board
    function boardTerms(root) {
        const isTerm = el => tagOf(el) === 'strong' || !!(el.classList && el.classList.contains('keyword'));
        const collect = (node, out) => { elementKids(node).forEach(c => { if (isTerm(c)) out.push(c); else collect(c, out); }); return out; };
        const def = descendants(root).find(el => el.classList && el.classList.contains('definition'));
        const inDef = def ? collect(def, []) : [];
        return inDef.length ? inDef : collect(root, []);
    }
    // The paragraph right after the (first) code block
    function outputAfterCode(root) {
        let node = descendants(root).find(el => tagOf(el) === 'pre') || null;
        while (node && node !== root) {
            const next = nextElement(node);
            if (next) return tagOf(next) === 'p' || /output/i.test(String(next.className || '')) ? next : null;
            node = parentOf(node);
        }
        return null;
    }

    // The presenter's name card (Phase 17) inside the presenter layer
    const isNameCard = node => !!(node && node.classList && node.classList.contains('cine-presenter-name'));
    const nameCardIn = holder => elementKids(holder).find(isNameCard) || null;

    class CinematicStage {
        // deps: doc, settings() -> current settings, mediaUrl(url), now(), exporting() -> true while an export records,
        // reducedMotion() -> the viewer's reduced-motion preference (respected in the preview only)
        // Phase 16: presenterAct(params, event) -> the presenter acts in place ({gesture, expression}); markItem(el) -> the page's own
        // "currently narrated" highlight on a list item; raf(fn) -> the frame clock the synchronization follows the narration with
        // Phase 17: presenterName(plan) -> the presenter's display name for the style's name card (null / empty: no card)
        constructor({ doc, settings, mediaUrl = u => u, now = () => Date.now(), exporting = () => false, reducedMotion = () => false,
            setTimeout: st = null, clearTimeout: ct = null, raf = null, presenterAct = null, markItem = null, presenterName = null }) {
            this.doc = doc;
            this.settings = settings;
            this.mediaUrl = mediaUrl;
            this.now = now;
            this.exporting = exporting;
            this.reducedMotion = reducedMotion;
            this.setTimer = st || ((fn, ms) => setTimeout(fn, ms));
            this.clearTimer = ct || (id => clearTimeout(id));
            this.raf = raf || (fn => (typeof requestAnimationFrame === 'function' ? requestAnimationFrame(fn) : this.setTimer(fn, 16)));
            this.options = { presenterAct: typeof presenterAct === 'function' ? presenterAct : null, markItem: typeof markItem === 'function' ? markItem : null,
                presenterName: typeof presenterName === 'function' ? presenterName : null };
            this.plan = null;
            // Phase 17: the scene's look as applied (null: none), the --st-* variables set on <html>, inline colours the
            // readability guard replaced (restored when the look changes or the page goes back to Classic)
            this.look = null;
            this.styleVarsSet = new Map();
            this.contrastFixes = [];
            this.animations = [];
            this.timers = [];
            this.syncCount = 0;
            this.holding = false;
            this.pausedAt = null;
            this.t0 = 0;
            this.token = 0;
            this.sceneSeq = 0; // one per laid-out scene (start() does not change it): the synchronization's events die with their scene
            this.resetSync(null);
            this.stats = { scenes: 0, cameraMoves: 0, anchored: 0, fitted: 0, lastTemplate: null, lastFit: 1, syncFired: 0, syncLast: null };
        }

        planFor(slide) {
            if (isClassic(this.settings())) return null;
            const plan = slide && slide.cinematic_plan;
            return plan && plan.version === VERSION && Array.isArray(plan.layers) ? plan : null;
        }

        // Motion the viewer sees now: the plan's, without camera moves and slides for reduced motion (preview only)
        calm() { return !this.exporting() && !!this.reducedMotion(); }

        // Aadhi (the mascot): where his studio clip stands in this scene (he is part of the studio picture)
        mascotPlacement(plan, fallback) {
            if (!plan || !plan.presenter || plan.presenter.type !== 'mascot') return fallback;
            return plan.presenter.shown ? plan.presenter.side : 'hidden';
        }

        // The Phase 12 presenter plan with the composition's box and visibility (the presenter keeps its behaviour)
        presenterPlanFor(presenterPlan, plan) {
            if (!presenterPlan || !plan) return presenterPlan;
            const layer = layerOf(plan, 'presenter');
            if (!layer) return { ...presenterPlan, enabled: false };
            return { ...presenterPlan, enabled: true, box: layer.box, layout: layer.side, position: layer.side,
                placement: layer.placement === 'pip' ? 'pip' : 'side' };
        }

        ensure(id, cls) {
            let node = this.doc.getElementById(id);
            if (!node) {
                node = this.doc.createElement('div');
                node.id = id;
                node.className = cls;
                node.setAttribute('aria-hidden', id === 'cine-background' ? 'true' : 'false');
                this.doc.body.appendChild(node);
            }
            return node;
        }

        // The transition into a scene (set before the page's view transition starts); 'cut' = no view transition. A kind the
        // page does not know is a fade; slide, zoom and wipe are fades for reduced motion (preview only)
        prepareTransition(plan) {
            const html = this.doc.documentElement;
            if (!plan) { html.removeAttribute('data-cine-transition'); return null; }
            const t = plan.transition || {};
            let kind = typeof t.in === 'string' && Object.prototype.hasOwnProperty.call(TRANSITIONS, t.in) ? t.in : 'fade';
            if (MOVING_TRANSITIONS.includes(kind) && this.calm()) kind = 'fade';
            html.setAttribute('data-cine-transition', kind);
            const seconds = typeof t.duration === 'number' && isFinite(t.duration) ? Math.min(3, Math.max(0, t.duration)) : 0;
            html.style.setProperty('--cine-transition-seconds', `${seconds || 0.5}s`);
            return kind;
        }

        // Places everything for the scene (called inside the page's scene update, so the transition sees the new layout)
        layout(slide, plan, index = null) {
            this.endScene();
            if (!plan) return this.clear();
            const body = this.doc.body;
            const root = this.doc.documentElement;
            this.plan = plan;
            this.syncCount = 0; // [SYNC] beats are counted from here: one reached while the formulas are typeset is not lost
            this.resetSync(plan);
            body.setAttribute('data-cinematic', plan.template);
            body.setAttribute('data-cine-shot', plan.shot || '');
            body.setAttribute('data-cine-typography', (plan.style || {}).typography || 'academic');
            body.setAttribute('data-cine-bg', (plan.background || {}).type || 'gradient');
            const board = layerOf(plan, 'board');
            const visual = layerOf(plan, 'visual');
            body.setAttribute('data-cine-board', board ? (board.style === 'visual' ? 'visual' : (BOARD_BASE[board.role] || board.role)) : 'none');
            // the board's representation from the visual direction (Phase 15): styles on the board it restyles
            body.setAttribute('data-cine-rep', board && BOARD_BASE[board.role] ? board.role : 'none');
            body.setAttribute('data-cine-visual', visual ? (visual.role === 'full_canvas' ? 'canvas' : 'side') : 'none');
            const setBox = (name, box) => ['x', 'y', 'w', 'h'].forEach(k => root.style.setProperty(`--cine-${name}-${k}`, box ? String(box[k]) : '0'));
            setBox('board', board && board.box);
            setBox('visual', visual && visual.role !== 'full_canvas' ? visual.box : null);
            root.style.setProperty('--cine-text-scale', '1');
            this.applyLook(plan);
            this.renderBackground(plan.background || {});
            this.renderTitle(slide, layerOf(plan, 'title'));
            this.renderLabels(slide, layerOf(plan, 'labels'));
            this.renderPresenterName(plan);
            const bg = this.doc.getElementById('cine-background');
            if (bg) bg.setAttribute('data-scene', index === null ? '' : String(index));
            this.stats.scenes += 1;
            this.stats.lastTemplate = plan.template;
            return plan;
        }

        // Phase 17: the scene's look (plan.style.look) on the page, once per scene: its checked CSS variables on <html> (only
        // the ones that changed are written; the ones the new look does not set are removed), the body attributes the CSS
        // keys on, and how it moves (cached here: nothing is read per frame). A plan without a look sets nothing (today's
        // look from the CSS fallbacks); null removes everything.
        applyLook(plan) {
            const look = lookOf(plan);
            const style = this.doc.documentElement.style;
            const body = this.doc.body;
            const { vars, skipped } = look ? styleVars(look.css) : { vars: Object.create(null), skipped: 0 };
            Object.keys(vars).forEach(name => { if (this.styleVarsSet.get(name) !== vars[name]) style.setProperty(name, vars[name]); });
            this.styleVarsSet.forEach((value, name) => { if (!(name in vars)) unsetProperty(style, name); });
            this.styleVarsSet = new Map(Object.keys(vars).map(name => [name, vars[name]]));
            const prefs = look && look.prefs && typeof look.prefs === 'object' ? look.prefs : {};
            const attrs = {};
            STYLE_ATTRS.forEach(([attr, pick, allowed]) => {
                const value = look ? pick(look, prefs) : null;
                attrs[attr] = typeof value === 'string' && allowed.includes(value) ? value : null;
                if (attrs[attr]) body.setAttribute(attr, attrs[attr]); else body.removeAttribute(attr);
            });
            this.restoreContrast();
            if (!look) { this.look = null; return null; }
            const tone = attrs['data-cine-tone'];
            this.look = { id: typeof look.id === 'string' ? look.id.slice(0, 60) : null, family: attrs['data-cine-style'], tone,
                presenterLabel: prefs.presenter_label === true, surface: tone === 'light' ? surfaceOf(vars) : null, ...lookMotion(vars) };
            // counted only once a look is applied (the stats' keys of a stage without one are unchanged)
            this.stats.styleSkipped = (this.stats.styleSkipped || 0) + skipped;
            this.stats.contrastFixed = this.stats.contrastFixed || 0;
            this.stats.lastStyle = this.look.id;
            return this.look;
        }

        // The presenter's name card: a style with presenter_label, a presenter shown (never Aadhi, whose studio clip fills the
        // frame) and a name from the page. The name as text only, a child of the presenter layer (#presenter-layer, which
        // has the presenter's box and the camera's transform), so it moves with the presenter; the page's CSS places it at
        // the bottom centre. No presenter layer (or one the presenter left empty): no card.
        renderPresenterName(plan) {
            const holder = this.doc.getElementById('presenter-layer');
            const p = plan && plan.presenter;
            const state = holder ? holder.getAttribute('data-state') : null;
            let name = null;
            if (holder && this.look && this.look.presenterLabel && p && p.shown && p.type !== 'mascot' && layerOf(plan, 'presenter')
                && state !== 'hidden' && state !== 'missing' && this.options.presenterName) {
                try { name = this.options.presenterName(plan); } catch (e) { name = null; }
            }
            name = typeof name === 'string' ? name.replace(/\s+/g, ' ').trim().slice(0, 60) : '';
            if (!name) { this.hidePresenterName(); return null; }
            let node = nameCardIn(holder);
            if (!node) {
                node = this.doc.createElement('div');
                node.className = 'cine-presenter-name';
                holder.appendChild(node); // after the presenter: element('presenter') still finds the presenter itself
            }
            node.textContent = name; // the presenter's name, never HTML
            node.setAttribute('data-state', 'shown');
            return node;
        }

        hidePresenterName() {
            const holder = this.doc.getElementById('presenter-layer');
            const node = holder ? nameCardIn(holder) : null;
            if (!node) return;
            node.textContent = '';
            node.setAttribute('data-state', 'hidden');
            if (typeof node.remove === 'function') node.remove();
        }

        // A light style keeps the board readable: an inline text colour from the lesson that does not read on the style's
        // surface (WCAG 4.5:1) is replaced by the style's text colour. At most 400 coloured elements; code panels (dark in every
        // style) and text on its own inline background are left alone. Idempotent (a replaced colour is the style's own).
        guardContrast() {
            const look = this.look;
            if (!look || look.tone !== 'light' || !look.surface) return 0;
            const content = this.doc.getElementById('slide-content-container');
            if (!content || !content.querySelectorAll) return 0;
            let list = [];
            try { list = content.querySelectorAll('[style]'); } catch (e) { return 0; }
            const view = this.doc.defaultView;
            let seen = 0;
            let fixed = 0;
            for (let i = 0; i < list.length && seen < MAX_CONTRAST_SCAN && i < MAX_CONTRAST_SCAN * 10; i++) {
                const node = list[i];
                const raw = node && node.style ? node.style.color : '';
                if (!raw || typeof raw !== 'string' || /var\(/i.test(raw)) continue;
                seen += 1;
                if (this.ownSurface(node, content)) continue;
                let color = parseColor(raw);
                if (!color && view && typeof view.getComputedStyle === 'function') {
                    try { color = parseColor(view.getComputedStyle(node).color); } catch (e) { color = null; }
                }
                const ratio = color ? contrastRatio(color, look.surface) : null;
                if (ratio === null || ratio >= MIN_CONTRAST) continue;
                const priority = typeof node.style.getPropertyPriority === 'function' ? node.style.getPropertyPriority('color') : '';
                this.contrastFixes.push({ el: node, color: raw, priority });
                node.style.setProperty('color', 'var(--st-text)', 'important');
                fixed += 1;
            }
            this.stats.contrastFixed = (this.stats.contrastFixed || 0) + fixed;
            return fixed;
        }

        // Inside a code panel, or on an inline background of its own: not on the board's surface
        ownSurface(node, content) {
            let n = node;
            for (let depth = 0; n && n !== content && depth < 40; depth++) {
                const tag = tagOf(n);
                if (tag === 'pre' || tag === 'code') return true;
                const st = n.style;
                const bg = st ? (st.backgroundColor || st.background || st.backgroundImage) : '';
                if (bg && typeof bg === 'string' && !/^(transparent|none|initial|inherit|unset)$/i.test(bg.trim())) return true;
                n = parentOf(n);
            }
            return false;
        }

        restoreContrast() {
            this.contrastFixes.forEach(f => { try { f.el.style.setProperty('color', f.color, f.priority || ''); } catch (e) { /* gone */ } });
            this.contrastFixes = [];
        }

        renderBackground(background) {
            const node = this.ensure('cine-background', 'cine-background');
            node.textContent = '';
            node.setAttribute('data-type', background.type || 'gradient');
            const root = this.doc.documentElement;
            if (background.type === 'gradient') {
                const [a, b, c] = background.colors || ['#140a2e', '#2a1352', '#0d1b3d'];
                root.style.setProperty('--cine-bg-1', a);
                root.style.setProperty('--cine-bg-2', b);
                root.style.setProperty('--cine-bg-3', c);
            } else if (background.type === 'solid') {
                root.style.setProperty('--cine-bg-solid', background.color || '#1a1036');
            } else if ((background.type === 'image' || background.type === 'video') && background.url) {
                const media = this.doc.createElement(background.type === 'image' ? 'img' : 'video');
                media.className = 'cine-background-media';
                if (background.type === 'video') {
                    media.muted = true;
                    media.setAttribute('muted', '');
                    media.setAttribute('loop', '');
                    media.setAttribute('playsinline', '');
                    media.setAttribute('autoplay', '');
                } else {
                    media.alt = '';
                }
                media.src = this.mediaUrl(background.url);
                node.appendChild(media);
                const scrim = this.doc.createElement('div');
                scrim.className = 'cine-scrim';
                scrim.style.opacity = String(background.scrim == null ? 0.55 : background.scrim);
                node.appendChild(scrim);
            }
        }

        renderTitle(slide, layer) {
            const node = this.ensure('cine-title', 'cine-title');
            node.textContent = '';
            node.className = 'cine-title';
            if (!layer || !slide) { node.setAttribute('data-state', 'hidden'); return; }
            node.setAttribute('data-state', 'shown');
            node.setAttribute('data-variant', layer.variant || 'band');
            Object.assign(node.style, { left: `${layer.box.x * 100}%`, top: `${layer.box.y * 100}%`, width: `${layer.box.w * 100}%`,
                height: `${layer.box.h * 100}%` });
            const inner = this.doc.createElement('div');
            inner.className = 'cine-title-inner';
            if (slide.type === 'chapter_card' && slide.chapter_label) {
                const badge = this.doc.createElement('div');
                badge.className = 'cine-title-badge';
                badge.textContent = slide.chapter_label;
                inner.appendChild(badge);
            }
            const text = this.doc.createElement('h1');
            text.className = 'cine-title-text';
            text.textContent = slide.title || (slide.type === 'quiz_checkpoint' ? 'Knowledge Checkpoint' : ''); // the lesson's own words
            inner.appendChild(text);
            if (layer.subtitle && slide.subtitle) {
                const sub = this.doc.createElement('p');
                sub.className = 'cine-title-subtitle';
                sub.textContent = slide.subtitle;
                inner.appendChild(sub);
            }
            node.appendChild(inner);
        }

        renderLabels(slide, layer) {
            const node = this.ensure('cine-labels', 'cine-labels');
            node.textContent = '';
            if (!layer || !slide) { node.setAttribute('data-state', 'hidden'); return; }
            node.setAttribute('data-state', 'shown');
            Object.assign(node.style, { left: `${layer.box.x * 100}%`, top: `${layer.box.y * 100}%`, width: `${layer.box.w * 100}%`,
                height: `${layer.box.h * 100}%` });
            const labels = (slide.composition && slide.composition.labels) || [];
            // a label the visual direction asks for (a formula's symbol, a diagram's term) is read from the scene's direction
            const annotations = (slide.visual_direction && slide.visual_direction.annotations) || [];
            (layer.items || []).forEach((item, n) => {
                const raw = item.source && item.source.kind === 'direction' ? annotations[item.source.index] : labels[item.source.index];
                const text = typeof raw === 'string' ? raw : raw && raw.text;
                if (!text) return;
                const chip = this.doc.createElement('span');
                chip.className = 'cine-label';
                chip.setAttribute('data-item', String(n));
                chip.setAttribute('data-shown', 'false');
                chip.textContent = String(text).trim().slice(0, 80); // exactly the screenplay's (or the direction's) label
                node.appendChild(chip);
            });
        }

        element(id) {
            if (id === 'visual') return this.doc.querySelector('.dynamic-side-zone .side-panel-view.active') || null;
            if (id === 'presenter') { // the presenter itself (never its name card)
                const layer = this.doc.getElementById('presenter-layer');
                return layer ? elementKids(layer).find(c => !isNameCard(c)) || null : null;
            }
            if (id === 'title') return this.doc.querySelector('#cine-title .cine-title-inner');
            if (id === 'board' || id === 'formula') {
                if (id === 'formula') {
                    const f = this.doc.querySelector(FORMULA_SELECTOR);
                    if (f) return f;
                }
                return this.doc.getElementById('presentation-board');
            }
            return null;
        }

        // durationMs: a synchronized emphasis lasts as long as its event (Phase 16); otherwise the kind's own length. With a
        // style (Phase 17, read once per scene in applyLook): its entrance length and easing, its slide distance (0 = a plain
        // fade) and its emphasis colour; without one, exactly the values before Phase 17
        animate(el, kind, layer, delayMs, durationMs) {
            if (!el || !el.animate || !kind || kind === 'appear') return null;
            const calm = this.calm() || (this.plan && (this.plan.style || {}).motion === 'none');
            const side = layer && layer.side === 'left' ? -1 : 1;
            const look = this.look;
            const shift = look ? look.shift : 1;
            let frames;
            if (kind === 'highlight') {
                const rgb = look ? look.focusRgb : DEFAULT_FOCUS_RGB;
                frames = [{ boxShadow: `0 0 0 0 rgba(${rgb}, 0)` }, { boxShadow: `0 0 0 6px rgba(${rgb}, 0.55)`, offset: 0.3 },
                    { boxShadow: `0 0 0 0 rgba(${rgb}, 0)` }];
            } else if (calm || kind === 'fade_in' || ((kind === 'slide_in' || kind === 'scale_in') && shift === 0)) {
                frames = [{ opacity: 0 }, { opacity: 1 }];
            } else if (kind === 'slide_in') {
                const from = layer && layer.type === 'presenter' ? `${fmt(side * 3 * shift)}vw 0`
                    : (layer && layer.type === 'title' ? `0 ${fmt(-1.2 * shift)}vh` : `0 ${fmt(1.6 * shift)}vh`);
                frames = [{ opacity: 0, translate: from }, { opacity: 1, translate: '0 0' }];
            } else if (kind === 'scale_in') {
                frames = [{ opacity: 0, scale: fmt(1 - 0.04 * shift, 4) }, { opacity: 1, scale: '1' }];
            } else {
                return null;
            }
            const entrance = look && look.entranceMs && (kind === 'fade_in' || kind === 'slide_in' || kind === 'scale_in') ? look.entranceMs : null;
            const anim = el.animate(frames, { duration: durationMs || entrance || ANIMATION_MS[kind] || 500, delay: Math.max(0, delayMs),
                easing: look ? look.ease : DEFAULT_EASE, fill: kind === 'highlight' ? 'none' : 'backwards' });
            this.animations.push(anim);
            if (this.holding && anim.pause) anim.pause();
            return anim;
        }

        // Starts the scene's timeline: element entrances, the camera, labels and emphasis (after the page drew the scene)
        start(slide, plan) {
            if (!plan || plan !== this.plan) return;
            const token = ++this.token;
            this.t0 = this.now();
            this.slide = slide;
            (plan.layers || []).forEach(layer => {
                if (['background', 'subtitles', 'labels', 'board'].includes(layer.id)) return;
                this.animate(this.element(layer.id), layer.enter, layer, (layer.start || 0) * 1000);
            });
            // A synchronized scene (Phase 16): its labels, emphasis and camera follow the narration (plan.sync), not the timeline
            if (this.sync) {
                this.markDates(plan);
                const syncCamera = this.syncEvents.some(r => r.ev.type === 'camera_focus' || r.ev.type === 'camera_return');
                if (syncCamera) this.cameraReady(plan);
                else this.startCamera(plan); // no camera moment in the plan (a short scene, a zoom-out): the planned move plays as before
                this.fit();
                this.syncStarted = true;
                this.prepareBoard(); // (with a light style's readability guard over the typeset board)
                this.syncLoop();
                return;
            }
            (plan.timeline || []).forEach(entry => {
                if (entry.layer === 'labels' && entry.item !== undefined && entry.item !== null && entry.event !== 'highlight') {
                    this.at(entry, () => this.showLabel(entry.item), token);
                } else if (entry.event === 'highlight') {
                    this.at(entry, () => {
                        const el = entry.layer === 'labels' ? this.doc.querySelector(`#cine-labels .cine-label[data-item="${entry.item}"]`) : this.element(entry.layer);
                        this.animate(el, 'highlight', null, 0);
                    }, token);
                }
            });
            this.markDates(plan);
            this.startCamera(plan);
            this.fit();
            this.guardContrast(); // a light style: the typeset board's inline colours stay readable
        }

        // A timeline board (Phase 15): each event's date gets a badge (the page's own DOM; the lesson is unchanged)
        markDates(plan) {
            const board = layerOf(plan, 'board');
            if (!board || board.role !== 'timeline') return;
            const content = this.doc.getElementById('slide-content-container');
            if (!content || !content.querySelectorAll) return;
            let items = [];
            try { items = Array.from(content.querySelectorAll(':scope > ol > li, :scope > ul > li')); } catch (e) { return; }
            items.forEach(li => {
                if (li.querySelector('.cine-date')) return;
                const node = Array.from(li.childNodes).find(n => n.nodeType === 3 && DATE.test(n.nodeValue));
                if (!node) return;
                const m = node.nodeValue.match(DATE);
                const before = node.nodeValue.slice(0, m.index);
                const after = node.nodeValue.slice(m.index + m[0].length).replace(/^\s*[:–—-]\s*/, ' ');
                const badge = this.doc.createElement('span');
                badge.className = 'cine-date';
                badge.textContent = m[0];
                li.insertBefore(this.doc.createTextNode(before), node);
                li.insertBefore(badge, node);
                node.nodeValue = after;
            });
        }

        // A timeline entry at its time, or when the narration reaches its [SYNC] anchor (whichever comes first)
        at(entry, fn, token) {
            let done = false;
            const fire = () => { if (!done && token === this.token) { done = true; fn(); } };
            const record = { at: entry.at, fire, anchor: entry.anchor, done: () => done };
            // Anchored entries wait for the narration (the estimate is only a late safety net)
            record.due = entry.anchor ? entry.at + 4 : entry.at;
            this.timers.push(record);
            // its [SYNC] beat already came (the narration starts before the formulas are typeset): shown now, not lost
            if (entry.anchor && entry.anchor.sync && entry.anchor.sync <= this.syncCount) {
                this.stats.anchored += 1;
                fire();
                return;
            }
            this.arm(record);
        }

        arm(record) {
            if (this.holding || record.done()) return;
            const wait = Math.max(0, record.due * 1000 - (this.now() - this.t0));
            record.id = this.setTimer(record.fire, wait);
        }

        showLabel(n) {
            const chip = this.doc.querySelector(`#cine-labels .cine-label[data-item="${n}"]`);
            if (!chip || chip.getAttribute('data-shown') === 'true') return;
            chip.removeAttribute('data-exit');
            chip.setAttribute('data-shown', 'true');
            this.animate(chip, (layerOf(this.plan, 'labels') || {}).enter || 'fade_in', layerOf(this.plan, 'labels'), 0);
        }

        // A label chip leaves (Phase 16 label_exit): it fades out (index.html's CSS for data-exit)
        hideLabel(n) {
            const chip = this.doc.querySelector(`#cine-labels .cine-label[data-item="${n}"]`);
            if (!chip || chip.getAttribute('data-shown') !== 'true') return false;
            chip.setAttribute('data-exit', 'true');
            chip.setAttribute('data-shown', 'false');
            return true;
        }

        startCamera(plan) {
            const cam = plan.camera || {};
            const els = (plan.layers || []).filter(l => l.camera).map(l => ({ layer: l, el: this.doc.querySelector(CAMERA_TARGETS[l.id]) }))
                .filter(x => x.el);
            if (!els.length) return;
            let from = cam.from || FULL;
            let to = cam.to || FULL;
            if (cam.movement === 'focus' && cam.target === 'formula') to = this.aimAtFormula(plan, to);
            const still = cam.movement === 'static' || this.calm() || !els[0].el.animate;
            els.forEach(({ layer, el }) => {
                el.classList.add('cine-camera');
                if (still) { el.style.transform = ''; return; }
                const anim = el.animate([{ transform: cameraTransform(from, layer.box) }, { transform: cameraTransform(to, layer.box) }],
                    { duration: (cam.duration || 6) * 1000, delay: (cam.start || 0) * 1000, easing: cam.easing || 'ease-in-out', fill: 'both' });
                this.animations.push(anim);
                if (this.holding && anim.pause) anim.pause();
            });
            if (!still) this.stats.cameraMoves += 1;
        }

        // The formula's own place on the board, measured once drawn: the camera leans towards it as far as the
        // same safety rules allow (every important layer whole, clear of the title and subtitles)
        aimAtFormula(plan, fallback) {
            const f = this.element('formula');
            const view = this.doc.defaultView || {};
            if (!f || !f.getBoundingClientRect || !view.innerWidth) return fallback;
            const r = f.getBoundingClientRect();
            if (!r.width || !r.height) return fallback;
            const target = { x: r.left / view.innerWidth, y: r.top / view.innerHeight, w: r.width / view.innerWidth, h: r.height / view.innerHeight };
            const { important, forbidden } = cameraConstraints(plan);
            const cap = 1 / Math.min((plan.camera.to || FULL).w, (plan.camera.from || FULL).w);
            const framing = bestFraming(important, forbidden, target, cap);
            return framing.w < 0.995 ? framing : fallback;
        }

        // Board text that does not fit its box is made smaller, in small steps and never below 80 % (still readable)
        fit() {
            const board = this.doc.getElementById('presentation-board');
            const content = this.doc.getElementById('slide-content-container');
            const root = this.doc.documentElement;
            if (!board || !content || !this.plan || !layerOf(this.plan, 'board')) return 1;
            let scale = 1;
            root.style.setProperty('--cine-text-scale', '1');
            // too tall: the board scrolls, or (a board that centres its content hides overflow at both ends) the content is
            // taller than the board's inner height
            const view = this.doc.defaultView;
            const tooTall = () => {
                if (board.scrollHeight > board.clientHeight + 2) return true;
                if (!view || !view.getComputedStyle || !content.getBoundingClientRect) return false;
                const cs = view.getComputedStyle(board);
                const inner = board.clientHeight - (parseFloat(cs.paddingTop) || 0) - (parseFloat(cs.paddingBottom) || 0);
                const tall = content.getBoundingClientRect().height;
                return inner > 0 && tall > inner + 2;
            };
            while (tooTall() && scale - 0.04 >= MIN_TEXT_SCALE - 1e-9) {
                scale = +(scale - 0.04).toFixed(2);
                root.style.setProperty('--cine-text-scale', String(scale));
            }
            if (scale < 1) this.stats.fitted += 1;
            this.stats.lastFit = scale;
            this.stats.overflow = tooTall(); // still too long at 80 %: reported, not hidden
            this.fitWidth(content);
            return scale;
        }

        // Phase 20: a display formula or a table wider than its place on the board (a narrow board beside a picture or the
        // presenter) is made smaller until it fits, rather than cut off at the board's edge: never below 45 % of its size
        fitWidth(content) {
            const view = this.doc.defaultView;
            if (!content || typeof content.querySelectorAll !== 'function' || !view || !view.getComputedStyle) return 0;
            let fitted = 0;
            Array.from(content.querySelectorAll('mjx-container[display="true"], table')).forEach(el => {
                // a formula is made smaller through its block (MathJax keeps its own scale on the formula itself); a table itself
                const block = typeof el.closest === 'function' ? el.closest('.formula-block') : null;
                const target = block || el;
                const box = block || el.parentElement;
                if (!box) return;
                target.style.fontSize = '';
                // too wide: the block scrolls sideways (a formula), or the element is wider than its box's inner width (a table);
                // made smaller in steps (padding and MathJax's own sizes do not scale in proportion) until it fits
                const over = () => {
                    if (block) return block.scrollWidth > block.clientWidth + 1;
                    const cs = view.getComputedStyle(box);
                    const room = box.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
                    return room > 0 && el.scrollWidth > room + 1;
                };
                if (!over()) return;
                let pct = 100;
                while (over() && pct - 5 >= MIN_WIDE_SCALE * 100) {
                    pct -= 5;
                    target.style.fontSize = `${pct}%`;
                }
                fitted += 1;
            });
            this.stats.widthFitted = fitted;
            return fitted;
        }

        // Narration beats (the same ones Aadhi hears): [SYNC] anchors, holds and resumes
        narrationEvent(kind) {
            if (!this.plan) return;
            if (kind === 'sync') {
                this.syncCount += 1;
                this.timers.filter(t => t.anchor && t.anchor.sync === this.syncCount && !t.done()).forEach(t => {
                    if (t.id !== undefined) this.clearTimer(t.id);
                    this.stats.anchored += 1;
                    t.fire();
                });
            } else if (kind === 'hold') {
                this.hold();
            } else if (kind === 'resume' || kind === 'segment') {
                this.resume();
                if (kind === 'segment') this.speechSegment();
            }
        }

        hold() {
            if (this.holding) return;
            this.holding = true;
            this.pausedAt = this.now();
            this.animations.forEach(a => { if (a.playState === 'running' && a.pause) a.pause(); });
            this.timers.forEach(t => { if (t.id !== undefined) this.clearTimer(t.id); t.id = undefined; });
            this.syncTimers.forEach(t => t.pause());
        }

        resume() {
            if (!this.holding) return;
            this.holding = false;
            const held = this.now() - (this.pausedAt || this.now());
            this.t0 += held;
            this.narration.segmentAt += held;
            this.pausedAt = null;
            this.animations.forEach(a => { if (a.playState === 'paused' && a.play) a.play(); });
            this.timers.forEach(t => this.arm(t));
            this.syncTimers.forEach(t => t.arm());
            this.syncLoop();
        }

        endScene() {
            this.token++;
            this.sceneSeq++;
            this.animations.forEach(a => { try { a.cancel(); } catch (e) { /* already gone */ } });
            this.animations = [];
            this.timers.forEach(t => { if (t.id !== undefined) this.clearTimer(t.id); });
            this.timers = [];
            this.holding = false;
            this.pausedAt = null;
            Object.values(CAMERA_TARGETS).forEach(sel => {
                const el = this.doc.querySelector(sel);
                if (el) el.classList.remove('cine-camera');
            });
            // the synchronization's timers, glows and waiting results end with their scene
            this.syncTimers.forEach(t => t.cancel());
            this.focused.forEach((f, el) => el.classList.remove(f.cls));
            this.pending.forEach(el => el.classList.remove('cine-pending'));
            this.resetSync(null);
        }

        // ---- Phase 16: the synchronization plan, played on the narration's own clock ----------------------------------

        resetSync(plan) {
            this.sync = syncPlanOf(plan);
            this.syncEvents = this.sync ? syncEvents(this.sync) : [];
            this.syncFiredIds = new Set();
            this.syncStarted = false; // start() ran: the estimate clock (seconds from start()) is running
            this.syncLooping = false;
            this.syncTimers = [];
            this.focused = new Map(); // element -> { cls, mark, since }: glows the synchronization put on the board
            this.pending = []; // results hidden until their text_reveal
            this.cameraMove = null; // { from, to, ms, anims }: the camera move under way (one at a time)
            this.narration = { mode: null, segment: -1, audio: null, playing: false, segmentAt: this.now() };
        }

        // Uses the server's narration audio (preview and export then hear the same audio and follow the same clock)
        wantsServerAudio() { return !!this.sync; }

        // Seconds the result stays on screen after the narration before the next scene (plan.sync.end_hold, 0–0.7)
        endHold() { return this.sync ? bounded(this.sync.end_hold, 0, MAX_END_HOLD, 0) : 0; }

        // Which clock the narration offers (index.html, when it starts speaking): 'audio' = the server's audio, attached per
        // segment (events wait for it); 'speech' = the browser's voice (only segment starts are known); 'none' = the estimates.
        // fromSegment: the browser's voice takes over at that segment (the server's voice failed there): its next 'segment'
        // beat is that segment, not the first
        narrationSource(kind, fromSegment = 0) {
            if (!this.sync) return;
            const first = Number.isInteger(fromSegment) && fromSegment > 0 && fromSegment < 1000 ? fromSegment : 0;
            this.narration = { mode: ['audio', 'speech', 'none'].includes(kind) ? kind : null, segment: first - 1, audio: null, playing: false, segmentAt: this.now() };
            this.syncLoop();
        }

        // One narration segment's audio (index.html, next to presenterStage.attachAudio): the clock is its currentTime / duration
        attachNarration(segIndex, audio) {
            if (!this.sync || !audio || !Number.isInteger(segIndex) || segIndex < 0) return;
            const seq = this.sceneSeq;
            this.narration = { mode: 'audio', segment: segIndex, audio, playing: !audio.paused && audio.currentTime > 0, segmentAt: this.now() };
            const mine = () => seq === this.sceneSeq && this.narration.audio === audio;
            if (audio.addEventListener) {
                audio.addEventListener('playing', () => { if (mine()) { this.narration.playing = true; this.syncLoop(); } });
                audio.addEventListener('ended', () => { if (mine()) this.syncTick(); });
            }
            this.syncLoop();
        }

        // The browser's own voice started a segment (its 'segment' beat): the segment is known, the estimate runs inside it
        speechSegment() {
            const n = this.narration;
            if (!this.sync || n.mode === 'audio') return;
            n.mode = 'speech';
            n.segment += 1;
            n.segmentAt = this.now();
            this.syncLoop();
        }

        // The whole narration was spoken (index.html's finishNarration): the labels and the result still waiting are shown,
        // the other moments are over
        narrationFinished() {
            if (!this.sync || !this.plan) return;
            this.syncTick();
            this.syncEvents.forEach(rec => {
                if (rec.done) return;
                if (rec.ev.type === 'label_enter' || rec.ev.type === 'text_reveal') this.fireSync(rec);
                else rec.done = true;
            });
        }

        segmentEstimate(index) {
            const segs = this.sync && this.sync.narration && Array.isArray(this.sync.narration.segments) ? this.sync.narration.segments : [];
            const seg = segs.find(s => s && s.index === index);
            return seg ? bounded(seg.estimate, 0, 3600, 0) : 0;
        }

        // Where the narration is now: { segment, ratio, time, duration } (media seconds), or null before it is heard
        narrationPosition() {
            const n = this.narration;
            if (n.mode === 'audio') {
                const a = n.audio;
                if (!a || !(n.playing || a.currentTime > 0 || a.ended)) return null;
                const d = typeof a.duration === 'number' && isFinite(a.duration) && a.duration > 0 ? a.duration : this.segmentEstimate(n.segment);
                if (!d) return null;
                const t = a.ended ? d : Math.min(d, Math.max(0, Number(a.currentTime) || 0));
                return { segment: n.segment, ratio: t / d, time: t, duration: d };
            }
            if (n.mode === 'speech' && n.segment >= 0) {
                const d = this.segmentEstimate(n.segment);
                const t = Math.max(0, ((this.holding ? this.pausedAt : this.now()) - n.segmentAt) / 1000);
                return { segment: n.segment, ratio: d ? Math.min(1, t / d) : null, time: t, duration: d || null };
            }
            return null;
        }

        // An event is due when the narration reaches its position (segment first, then the ratio) — never before the word it
        // belongs to: only a frame's lead (SYNC_LEAD); its tolerance bounds how LATE it may come, not how early. Without a
        // narration clock, when the scene clock (seconds from start(), holds excluded) passes its estimate
        syncDue(rec, pos) {
            const mode = this.narration.mode;
            const lead = Math.min(rec.tolerance, SYNC_LEAD);
            if ((mode === 'audio' || mode === 'speech') && rec.pos) {
                if (!pos) return false; // the narration has not been heard yet: wait for it
                if (pos.segment !== rec.pos.segment) return pos.segment > rec.pos.segment;
                if (pos.duration) return pos.time >= rec.pos.ratio * pos.duration - lead;
            }
            if (!this.syncStarted || rec.estimate === null) return false;
            return ((this.holding ? this.pausedAt : this.now()) - this.t0) / 1000 >= rec.estimate - lead;
        }

        // Fires every event that is due and whose depends_on have fired (in the plan's order; each once)
        syncTick() {
            if (!this.sync || !this.plan || this.holding) return 0;
            const pos = this.narrationPosition();
            let fired = 0;
            let progress = true;
            while (progress) {
                progress = false;
                for (const rec of this.syncEvents) {
                    if (rec.done || !rec.deps.every(d => this.syncFiredIds.has(d)) || !this.syncDue(rec, pos)) continue;
                    this.fireSync(rec);
                    fired += 1;
                    progress = true;
                }
            }
            return fired;
        }

        // The frame loop (requestAnimationFrame: smoother than the audio's timeupdate); it stops on a hold, when every event
        // has fired, or when the scene changes
        syncLoop() {
            if (!this.sync || this.syncLooping || this.holding || this.syncEvents.every(r => r.done)) return;
            this.syncLooping = true;
            const seq = this.sceneSeq;
            const frame = () => {
                if (seq !== this.sceneSeq) return;
                if (this.holding || !this.sync) { this.syncLooping = false; return; }
                this.syncTick();
                if (this.syncEvents.every(r => r.done)) { this.syncLooping = false; return; }
                this.raf(frame);
            };
            this.raf(frame);
        }

        fireSync(rec) {
            rec.done = true;
            this.syncFiredIds.add(rec.id);
            try { this.dispatchSync(rec.ev); } catch (e) { /* one moment never stops the scene */ }
            this.stats.syncFired += 1;
            this.stats.syncLast = rec.ev.type;
        }

        dispatchSync(ev) {
            const type = ev.type;
            const target = ev.target;
            const ms = bounded(ev.duration, 0.2, 8, 1.6) * 1000;
            if (SYNC_TYPES[type] === 'presenter') {
                const raw = ev.params && typeof ev.params === 'object' ? ev.params : {};
                const params = {};
                ['gesture', 'expression', 'state'].forEach(k => { if (typeof raw[k] === 'string' && raw[k] && raw[k].length <= 24) params[k] = raw[k]; });
                if (this.options.presenterAct) this.options.presenterAct(params, ev);
                return true;
            }
            if (type === 'visual_highlight' || type === 'diagram_focus') return !!this.animate(this.element('visual'), 'highlight', null, 0, ms);
            if (type === 'formula_emphasis') return !!this.animate(this.element('formula'), 'highlight', null, 0, ms);
            if (type === 'text_emphasis') return this.emphasize(target, ms);
            if (type === 'text_reveal') return this.revealBoard(target);
            if (type === 'label_enter') return Number.isInteger(target.item) && target.item >= 0 ? (this.showLabel(target.item), true) : false;
            if (type === 'label_exit') return Number.isInteger(target.item) && target.item >= 0 ? this.hideLabel(target.item) : false;
            if (type === 'camera_focus' || type === 'camera_return') return this.moveCamera(ev.params, type);
            return false;
        }

        // The board element a target names: a fixed kind and index resolved on the page's own board (never a selector)
        boardTarget(target) {
            const content = this.doc.getElementById('slide-content-container');
            if (!content || !target || typeof target !== 'object') return [];
            const kind = target.kind;
            const n = target.index === undefined || target.index === null ? 0 : target.index;
            if (!Number.isInteger(n) || n < 0) return [];
            const one = el => (el ? [el] : []);
            if (LIST_KINDS.includes(kind)) return one(topLevel(content, 'li')[n]);
            if (kind === 'side') {
                const lists = topLevel(content, 'list');
                return n < 2 && lists.length >= 2 ? one(lists[n]) : [];
            }
            if (kind === 'column') return tableColumn(content, n);
            if (kind === 'term') return one(boardTerms(content)[n]);
            if (kind === 'output') return one(outputAfterCode(content));
            if (kind === 'formula') return one(this.doc.querySelector(FORMULA_SELECTOR));
            return [];
        }

        // text_emphasis: a list item gets the page's "currently narrated" glow (markItem); anything else glows (cine-focus)
        // for the event's duration. The item the narration names is shown if its reveal has not come yet (a timeline's date is
        // said before its [SYNC]: never a glow on a hidden item), and one emphasis holds the board at a time (the side named
        // before stops glowing).
        emphasize(target, ms) {
            const els = this.boardTarget(target);
            if (!els.length) return false;
            els.forEach(el => {
                if (!el.classList.contains('cascade-hidden')) return;
                el.classList.remove('cascade-hidden');
                el.classList.add('cascade-visible');
                this.animate(el, 'fade_in', null, 0);
            });
            const now = this.now();
            this.focused.forEach((f, el) => {
                if (f.cls === 'cine-focus' && f.since < now && !els.includes(el)) { el.classList.remove(f.cls); this.focused.delete(el); }
            });
            if (LIST_KINDS.includes(target.kind)) {
                if (this.options.markItem) { this.options.markItem(els[0]); return true; }
                return this.glow(els, 'narration-active', ms);
            }
            return this.glow(els, 'cine-focus', ms);
        }

        glow(els, cls, ms) {
            const mark = {};
            const since = this.now();
            els.forEach(el => { el.classList.add(cls); this.focused.set(el, { cls, mark, since }); });
            this.later(ms, () => els.forEach(el => {
                const f = this.focused.get(el);
                if (f && f.mark === mark) { el.classList.remove(cls); this.focused.delete(el); }
            }));
            return true;
        }

        // The result a text_reveal shows stays hidden until its moment (called by the page once the board is drawn, and by
        // start(); idempotent). A light style's readability guard runs here too, before the board is first seen.
        prepareBoard() {
            if (this.plan) this.guardContrast();
            if (!this.sync) return 0;
            let hidden = 0;
            this.syncEvents.forEach(rec => {
                if (rec.done || rec.ev.type !== 'text_reveal') return;
                this.boardTarget(rec.ev.target).forEach(el => {
                    if (this.pending.includes(el)) return;
                    el.classList.add('cine-pending');
                    this.pending.push(el);
                    hidden += 1;
                });
            });
            return hidden;
        }

        revealBoard(target) {
            const els = this.boardTarget(target);
            els.forEach(el => {
                el.classList.remove('cine-pending');
                el.classList.remove('cascade-hidden');
                el.classList.add('cascade-visible');
                this.pending = this.pending.filter(p => p !== el);
                this.animate(el, 'fade_in', null, 0);
            });
            return els.length > 0;
        }

        // A synchronized scene's camera starts still (whole frame): its camera events move it
        cameraReady(plan) {
            (plan.layers || []).filter(l => l.camera).forEach(l => {
                const el = this.doc.querySelector(CAMERA_TARGETS[l.id]);
                if (!el) return;
                el.classList.add('cine-camera');
                if (!this.cameraMove) el.style.transform = '';
            });
        }

        // Where the camera is now (the move under way read at its current progress; a move that cannot be read is finished)
        cameraFraming() {
            const m = this.cameraMove;
            if (!m) return { ...FULL };
            const a = m.anims[0];
            const t = a && typeof a.currentTime === 'number' && isFinite(a.currentTime) ? a.currentTime : m.ms;
            return lerpFraming(m.from, m.to, easeInOut(Math.min(1, Math.max(0, t / m.ms))));
        }

        // camera_focus / camera_return: one move from the current view to params.to (checked safe by the server), with the
        // same still rules as startCamera (a still plan, reduced motion in the preview, no Web Animations)
        moveCamera(params, type) {
            const plan = this.plan;
            const t = params && typeof params === 'object' ? params.to : null;
            if (!plan || !t || typeof t !== 'object') return false;
            if (!['x', 'y', 'w'].every(k => typeof t[k] === 'number' && isFinite(t[k]) && t[k] >= 0 && t[k] <= 1)) return false;
            if (t.w < 0.5 || t.x + t.w > 1 + 1e-6 || t.y + t.w > 1 + 1e-6) return false;
            const cam = plan.camera || {};
            const els = (plan.layers || []).filter(l => l.camera).map(l => ({ layer: l, el: this.doc.querySelector(CAMERA_TARGETS[l.id]) })).filter(x => x.el);
            if (!els.length) return false;
            els.forEach(({ el }) => el.classList.add('cine-camera'));
            if (cam.movement === 'static' || this.calm() || !els[0].el.animate) return false;
            let to = { x: t.x, y: t.y, w: t.w };
            if (type === 'camera_focus' && cam.movement === 'focus' && cam.target === 'formula') to = this.aimAtFormula(plan, to);
            const from = this.cameraFraming();
            const ms = bounded(params.duration, 0.3, 8, 1.2) * 1000;
            const previous = this.cameraMove ? this.cameraMove.anims : [];
            previous.forEach(a => { try { a.cancel(); } catch (e) { /* already gone */ } });
            this.animations = this.animations.filter(a => !previous.includes(a));
            const anims = els.map(({ layer, el }) => {
                const anim = el.animate([{ transform: cameraTransform(from, layer.box) }, { transform: cameraTransform(to, layer.box) }],
                    { duration: ms, easing: 'ease-in-out', fill: 'both' });
                this.animations.push(anim);
                if (this.holding && anim.pause) anim.pause();
                return anim;
            });
            this.cameraMove = { from, to, ms, anims };
            this.stats.cameraMoves += 1;
            return true;
        }

        // A timer of the synchronization: it waits during a hold and ends with its scene
        later(ms, fn) {
            const seq = this.sceneSeq;
            const rec = { left: ms, id: undefined, since: 0, over: false };
            const drop = () => { rec.over = true; this.syncTimers = this.syncTimers.filter(x => x !== rec); };
            rec.arm = () => {
                if (rec.over || rec.id !== undefined) return;
                rec.since = this.now();
                rec.id = this.setTimer(() => { rec.id = undefined; drop(); if (seq === this.sceneSeq) fn(); }, rec.left);
            };
            rec.pause = () => {
                if (rec.id === undefined) return;
                this.clearTimer(rec.id);
                rec.id = undefined;
                rec.left = Math.max(0, rec.left - (this.now() - rec.since));
            };
            rec.cancel = () => { if (rec.id !== undefined) this.clearTimer(rec.id); rec.id = undefined; drop(); };
            this.syncTimers.push(rec);
            if (!this.holding) rec.arm();
            return rec;
        }

        // Back to the classic page (Classic setting, or a scene without a plan)
        clear() {
            this.endScene();
            this.plan = null;
            const body = this.doc.body;
            ['data-cinematic', 'data-cine-shot', 'data-cine-typography', 'data-cine-bg', 'data-cine-board', 'data-cine-rep', 'data-cine-visual']
                .forEach(a => body.removeAttribute(a));
            this.doc.documentElement.removeAttribute('data-cine-transition');
            ['cine-background', 'cine-title', 'cine-labels'].forEach(id => {
                const node = this.doc.getElementById(id);
                if (node) { node.textContent = ''; node.setAttribute('data-state', 'hidden'); node.setAttribute('data-type', 'none'); }
            });
            this.applyLook(null); // the style's variables, attributes and replaced colours go too (Phase 17)
            this.hidePresenterName();
            return null;
        }

        // What the stage shows now (tests and ?visualDebug)
        state() {
            return { template: this.plan && this.plan.template, style: this.look ? this.look.id : null, syncCount: this.syncCount, holding: this.holding,
                animations: this.animations.length, timers: this.timers.length, stats: { ...this.stats },
                sync: this.sync ? { events: this.syncEvents.length, fired: this.syncEvents.filter(r => r.done).length, clock: this.narration.mode,
                    position: this.narrationPosition() } : null };
        }
    }

    // ---- Phase 21: captions that fit their band (stage-fit.css) ---------------------------------------------------------
    // The composition keeps the bottom SUBTITLES.h of the frame for the caption (108 px at 720p), but #subtitle-track is sized
    // in fixed px (60 px above the bottom, a 1.8rem font or the style's caption size): at 720p a two-line caption reached into
    // the presenter's name card and the board's lower edge. Fixed on the page only (plans, boxes, fingerprints and approvals are
    // unchanged): a caption taller than its band is lowered, never closer to the bottom than a small gutter; only when that is
    // not enough (large captions, three lines) its font is made smaller, never below 85 %. A caption that already fits (one
    // line, and the usual captions at 1080p) stays exactly where it is.
    const CAPTION_LIFT = 5; // the .active caption's translateY(-5px)
    const CAPTION_SLACK = 4; // px a caption may reach above its band before it is moved
    const CAPTION_GUTTER = 8; // the closest a lowered caption comes to the bottom: 8 px, or 1.5 % of the height when more
    const CAPTION_GUTTER_SHARE = 0.015;
    const MIN_CAPTION_SCALE = 0.85;
    const CAPTION_DROP_VAR = '--cine-caption-drop';
    const CAPTION_FIT_VAR = '--cine-caption-fit';

    // How to fit a caption into its band: viewportH = the frame's height (px), bottom = the caption's offset from the bottom at
    // its own place, height = its height at its own font size, lift = how far .active raises it. {drop: px lower, scale: the
    // font's factor}; {drop: 0, scale: 1} (unchanged) when it fits or the numbers cannot be used.
    function captionFit({ viewportH, bottom, height, lift = CAPTION_LIFT } = {}) {
        const none = { drop: 0, scale: 1 };
        const num = v => typeof v === 'number' && isFinite(v);
        if (![viewportH, bottom, height, lift].every(num) || viewportH <= 0 || height <= 0 || bottom < 0 || lift < 0) return none;
        const zone = viewportH * SUBTITLES.h;
        const over = bottom + height + lift - zone;
        if (over <= CAPTION_SLACK) return none;
        const gutter = Math.max(CAPTION_GUTTER, viewportH * CAPTION_GUTTER_SHARE);
        const drop = +Math.max(0, Math.min(over, bottom - gutter)).toFixed(1);
        if (over - drop <= CAPTION_SLACK) return { drop, scale: 1 };
        const scale = Math.max(MIN_CAPTION_SCALE, Math.min(1, (zone - (bottom - drop) - lift) / height));
        return { drop, scale: +scale.toFixed(3) };
    }

    // Keeps #subtitle-track inside its band (index.html calls it once at start-up). The caption is measured when its size
    // changes (a ResizeObserver: a new line, the style's caption size or box, a font that loaded), when the page is resized and
    // when the scene style changes (body[data-cinematic], data-cine-caption), and two inline variables on the track tell
    // stage-fit.css how to place it: --cine-caption-drop (px) and --cine-caption-fit (a factor), both unset while nothing has to
    // move. ResizeObserver callbacks run after layout and before paint, so a caption is never painted outside its band. Classic:
    // nothing is set. No track, no ResizeObserver or nothing to measure with: nothing happens (null). Inline variables only: the
    // export's caption log watches the track's class, data-captions and text, never its style, so no extra caption is logged.
    const captionWatches = new WeakMap();
    function watchCaptions(doc, { ResizeObserver: RO = null, MutationObserver: MO = null } = {}) {
        const view = doc && doc.defaultView;
        const track = doc && typeof doc.getElementById === 'function' ? doc.getElementById('subtitle-track') : null;
        const style = track && track.style;
        const Resize = RO || (view && view.ResizeObserver);
        if (!track || !style || typeof style.setProperty !== 'function' || !view || typeof view.getComputedStyle !== 'function'
            || typeof Resize !== 'function') return null;
        if (captionWatches.has(track)) return captionWatches.get(track);
        const state = { drop: 0, scale: 1, updates: 0 };
        const cinematic = () => !!(doc.body && typeof doc.body.getAttribute === 'function' && doc.body.getAttribute('data-cinematic') !== null);
        // the caption at its own place and size (the variables are lifted for the measurement and written again just after)
        const measure = () => {
            if (state.drop) unsetProperty(style, CAPTION_DROP_VAR);
            if (state.scale !== 1) unsetProperty(style, CAPTION_FIT_VAR);
            const height = track.offsetHeight;
            let bottom = NaN;
            try { bottom = parseFloat(view.getComputedStyle(track).bottom); } catch (e) { bottom = NaN; }
            const root = doc.documentElement;
            const viewportH = (root && root.clientHeight) || view.innerHeight;
            return typeof height === 'number' && isFinite(height) && isFinite(bottom) && viewportH > 0
                ? { viewportH, bottom, height, lift: CAPTION_LIFT } : null;
        };
        // true when the caption's font size changed (its box changes size)
        const update = () => {
            const before = { drop: state.drop, scale: state.scale };
            let fit = { drop: 0, scale: 1 };
            if (cinematic()) {
                const measured = measure();
                fit = measured ? captionFit(measured) : before; // nothing to measure with: as it was
            }
            if (fit.drop) style.setProperty(CAPTION_DROP_VAR, `${fit.drop}px`);
            else if (before.drop) unsetProperty(style, CAPTION_DROP_VAR);
            if (fit.scale !== 1) style.setProperty(CAPTION_FIT_VAR, String(fit.scale));
            else if (before.scale !== 1) unsetProperty(style, CAPTION_FIT_VAR);
            state.drop = fit.drop;
            state.scale = fit.scale;
            state.updates += 1;
            return fit.scale !== before.scale;
        };
        const raf = typeof view.requestAnimationFrame === 'function' ? fn => view.requestAnimationFrame(fn) : fn => setTimeout(fn, 16);
        let stopped = false;
        const observer = new Resize(() => {
            if (stopped || !update()) return;
            // the font changed size inside the observer's own callback: the track is watched again from the next frame, so the
            // browser never reports a resize loop (that frame's first report measures the fitted caption, and nothing changes)
            observer.unobserve(track);
            raf(() => { if (!stopped) observer.observe(track, { box: 'border-box' }); });
        });
        observer.observe(track, { box: 'border-box' }); // the box style's padding counts
        const Mutation = MO || view.MutationObserver;
        const mutations = typeof Mutation === 'function' && doc.body ? new Mutation(() => { if (!stopped) update(); }) : null;
        if (mutations) mutations.observe(doc.body, { attributes: true, attributeFilter: ['data-cinematic', 'data-cine-caption'] });
        const onResize = () => { if (!stopped) update(); };
        if (typeof view.addEventListener === 'function') view.addEventListener('resize', onResize);
        const watch = {
            update,
            state: () => ({ ...state }),
            stop() {
                stopped = true;
                observer.disconnect();
                if (mutations) mutations.disconnect();
                if (typeof view.removeEventListener === 'function') view.removeEventListener('resize', onResize);
                unsetProperty(style, CAPTION_DROP_VAR);
                unsetProperty(style, CAPTION_FIT_VAR);
                captionWatches.delete(track);
            }
        };
        captionWatches.set(track, watch);
        update();
        return watch;
    }

    // ---- settings panel (start screen) --------------------------------------------------------------------------

    function el(doc, tag, attrs = {}, ...children) {
        const node = doc.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v === undefined || v === null || v === false) continue;
            if (k === 'text') node.textContent = v;
            else if (k === 'class') node.className = v; // the same as the attribute (and what the tests' DOM stand-in matches)
            else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
            else if (k === 'value') node.value = v;
            else if (k === 'selected') node.selected = !!v;
            else node.setAttribute(k, v === true ? '' : v);
        }
        children.flat().forEach(c => { if (c !== null && c !== undefined && c !== false) node.appendChild(typeof c === 'string' ? doc.createTextNode(c) : c); });
        return node;
    }

    class CinematicSettingsPanel {
        // deps: doc, container, api (CinematicApi), storage, onChange(settings), pickBackground({kinds, onPick}),
        // presenterIsMascot() -> true when Aadhi (the mascot) presents, projectId() -> the open lesson or null,
        // status / directorStatus: GET /api/cinematic's composer / director parts (loadStatus() asks for them once signed in)
        // Phase 17: looks: GET /api/cinematic's `looks` (families, options; loaded with the rest), onStyleChange(choice) -> the
        // user changed the video style or an override here (choice: {style, style_version, style_overrides}, or null for the
        // legacy look), called after onChange so the page can keep the lesson's style
        // Phase 21: debug (boolean or () => boolean; the page's ?visualDebug): the server's reasons, the provider and raw error
        // messages are shown too; otherwise plain words only
        constructor({ doc, container, api, storage, onChange = () => {}, pickBackground = null, presenterIsMascot = () => false,
            projectId = () => null, follow = null, status = null, composerProvider = () => 'gemini', directorStatus = null, looks = null,
            onStyleChange = null, debug = false }) {
            this.doc = doc;
            this.container = container;
            this.api = api;
            this.storage = storage;
            this.onChange = onChange;
            this.pickBackground = pickBackground;
            this.presenterIsMascot = presenterIsMascot;
            this.projectId = projectId;
            this.follow = follow;
            this.settings = loadSettings(storage);
            this.status = '';
            this.busy = false;
            this.composerStatus = status; // GET /api/cinematic: whether AI-assisted composition can run here
            this.composerProvider = composerProvider; // the text model's provider the page would use (its lesson-generation choice)
            this.directorStatus = directorStatus; // GET /api/cinematic: whether AI-assisted direction can run here, and its vocabulary
            this.looks = looks && typeof looks === 'object' ? looks : null; // GET /api/cinematic: the style families and options
            this.onStyleChange = typeof onStyleChange === 'function' ? onStyleChange : null;
            this.advancedOpen = null; // the Advanced choices: open while one is set, until the user opens or closes them
            this.debug = debug;
            this.pendingFocus = null; // where the keyboard focus goes when the panel is drawn again (arrow keys in the style picker)
        }

        debugOn() { return debugOn(this.debug); }

        // What the server offers (loaded once signed in: the request needs a login)
        async loadStatus() {
            try {
                const vocab = await this.api.vocabulary();
                this.composerStatus = vocab.composer || null;
                this.directorStatus = vocab.director || null;
                this.looks = vocab.looks && typeof vocab.looks === 'object' ? vocab.looks : null;
            } catch (e) {
                this.composerStatus = null;
                this.directorStatus = null;
                this.looks = null;
            }
            this.render();
            return this.composerStatus;
        }

        // ---- Phase 17: the video style ----------------------------------------------------------------------------------

        // The families offered: the server's (checked), else the four known here (no preview colours then: the CSS fallbacks)
        styleFamilies() {
            const list = this.looks && Array.isArray(this.looks.families) ? this.looks.families : [];
            const out = [];
            list.slice(0, 12).forEach(f => {
                if (!f || typeof f !== 'object' || typeof f.id !== 'string' || !/^[a-z_]{1,40}$/.test(f.id) || out.some(o => o.id === f.id)) return;
                const known = STYLE_FAMILIES.find(k => k.id === f.id) || {};
                out.push({ id: f.id, version: Number.isInteger(f.version) && f.version > 0 ? f.version : (known.version || null),
                    label: typeof f.label === 'string' && f.label ? f.label.slice(0, 60) : (known.label || f.id),
                    description: typeof f.description === 'string' ? f.description.slice(0, 200) : (known.description || ''),
                    tone: TONES.includes(f.tone) ? f.tone : (known.tone || null),
                    prefs: f.prefs && typeof f.prefs === 'object' ? f.prefs : (known.prefs || {}), css: f.css && typeof f.css === 'object' ? f.css : null });
            });
            return out.length ? out : STYLE_FAMILIES.map(f => ({ ...f, prefs: { ...f.prefs }, css: null }));
        }

        defaultStyle(families = this.styleFamilies()) {
            const d = this.looks && this.looks.default;
            return typeof d === 'string' && families.some(f => f.id === d) ? d : DEFAULT_STYLE;
        }

        // The choices for one override: the server's (with "default" first), else the ones known here
        overrideOptions(key) {
            const fromServer = this.looks && this.looks.options && Array.isArray(this.looks.options[key])
                ? this.looks.options[key].filter(v => typeof v === 'string' && /^[a-z_]{1,24}$/.test(v)) : [];
            const list = fromServer.length ? fromServer : (STYLE_OVERRIDES[key] || []);
            return ['default', ...list.filter(v => v !== 'default')];
        }

        // Everything the page needs to offer the style elsewhere (Visual Review): families, options per key, the default
        styleOptions() {
            const families = this.styleFamilies();
            const options = {};
            Object.keys(STYLE_OVERRIDE_LABELS).forEach(key => { options[key] = this.overrideOptions(key); });
            const scene = this.looks && this.looks.options && Array.isArray(this.looks.options.scene)
                ? this.looks.options.scene.filter(k => typeof k === 'string' && STYLE_OVERRIDE_LABELS[k]) : ['accent', 'background'];
            return { default: this.defaultStyle(families), families, options, scene, labels: { ...STYLE_OVERRIDE_LABELS } };
        }

        // The lesson's style as the page stores it (null: no style, the lesson keeps its original look)
        styleChoice() {
            const s = this.settings;
            return s.style ? { style: s.style, style_version: s.style_version || null, style_overrides: cleanOverrides(s.style_overrides, this.styleOptions().options) }
                : null;
        }

        saveStyle() {
            saveSettings(this.storage, this.settings);
            this.status = '';
            this.render();
            this.onChange(this.settings);
            if (this.onStyleChange) this.onStyleChange(this.styleChoice());
        }

        // The user picks a family: only the style and its version change (the lesson's transitions and camera motion are
        // part of the composition, so a style never changes them by itself: its preferences are offered, see styleSuggestion)
        chooseStyle(id) {
            const family = this.styleFamilies().find(f => f.id === id);
            if (!family || this.settings.style === family.id) return false;
            this.settings.style = family.id;
            this.settings.style_version = Number.isInteger(family.version) ? family.version : null;
            this.saveStyle();
            return true;
        }

        // What the chosen family prefers and the lesson does not use yet ({changes, text}), or null when nothing differs
        styleSuggestion(family = this.styleFamilies().find(f => f.id === this.settings.style)) {
            if (!family || family.id !== this.settings.style) return null;
            const prefs = family.prefs || {};
            const s = this.settings;
            const changes = {};
            if (typeof prefs.transition === 'string' && Object.prototype.hasOwnProperty.call(TRANSITIONS, prefs.transition) && prefs.transition !== s.transitions) {
                changes.transitions = prefs.transition;
            }
            const motion = prefs.camera === 'still' ? 'none' : (prefs.camera === 'subtle' ? 'subtle' : null);
            if (motion && motion !== s.motion) changes.motion = motion;
            const words = [];
            if (changes.transitions) words.push(`${TRANSITIONS[changes.transitions]} transitions`);
            if (changes.motion) words.push(changes.motion === 'none' ? 'a still camera' : 'a subtly moving camera');
            return words.length ? { changes, text: `This style suggests ${words.join(' and ')}.` } : null;
        }

        // The user accepts the style's suggestions: applied through the normal settings path (one change event)
        applySuggestions() {
            const suggestion = this.styleSuggestion();
            if (!suggestion) return false;
            this.update(suggestion.changes);
            return true;
        }

        // One override ("default" removes it); none until a style is chosen (a lesson without one keeps its original look)
        setOverride(key, value) {
            if (!this.settings.style || !STYLE_OVERRIDE_LABELS[key]) return false;
            const next = cleanOverrides(this.settings.style_overrides, this.styleOptions().options);
            if (!value || value === 'default') delete next[key];
            else if (this.overrideOptions(key).includes(value)) next[key] = value;
            else return false;
            this.settings.style_overrides = next;
            this.saveStyle();
            return true;
        }

        // A saved lesson is opened: its style (null = a lesson from before Phase 17: the legacy look), checked, kept and shown;
        // nothing is pre-filled and onChange is not called (the page plans the lesson itself)
        applyLessonStyle(choice) {
            const clean = cleanStyleChoice(choice && typeof choice === 'object' ? choice : null, this.styleOptions().options);
            this.settings.style = clean.style;
            this.settings.style_version = clean.style_version;
            this.settings.style_overrides = clean.style_overrides;
            saveSettings(this.storage, this.settings);
            this.render();
            return clean;
        }

        renderStyle(h) {
            const s = this.settings;
            const families = this.styleFamilies();
            const chosen = families.find(f => f.id === s.style) || null; // none: the lesson keeps its original look, nothing is selected
            // a radio group: one Tab stop (the chosen style, else the first); the arrow keys move to and choose the next style
            const tabStop = chosen ? chosen.id : (families[0] && families[0].id);
            const STEP = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
            const onArrow = e => {
                const step = e && STEP[e.key];
                if (!step) return;
                if (typeof e.preventDefault === 'function') e.preventDefault();
                if (typeof e.stopPropagation === 'function') e.stopPropagation(); // not the lesson's own arrow keys
                const ids = families.map(f => f.id);
                const from = e.target && typeof e.target.getAttribute === 'function' ? e.target.getAttribute('data-style') : null;
                const at = Math.max(0, ids.indexOf(from || tabStop));
                const next = ids[(at + step + ids.length) % ids.length];
                this.pendingFocus = `[data-style="${next}"]`;
                if (!this.chooseStyle(next)) this.render();
            };
            const options = families.map(f => {
                const on = !!chosen && f.id === chosen.id;
                // the family's own colours on its swatch only (the same checks as the stage)
                const swatch = h('span', { class: 'style-swatch', 'aria-hidden': 'true' },
                    h('span', { class: 'style-swatch-title' }), h('span', { class: 'style-swatch-body' }), h('span', { class: 'style-swatch-card' }),
                    h('span', { class: 'style-swatch-chip' }), h('span', { class: 'style-swatch-presenter' }),
                    h('span', { class: 'style-swatch-bars' }, h('i'), h('i'), h('i')));
                const { vars } = styleVars(f.css);
                Object.keys(vars).forEach(name => swatch.style.setProperty(name, vars[name]));
                return h('button', { type: 'button', class: 'style-option ui-focusable', role: 'radio', 'aria-checked': on ? 'true' : 'false', 'data-style': f.id,
                    'data-selected': on ? 'true' : 'false', tabindex: f.id === tabStop ? '0' : '-1', title: f.description || f.label,
                    onclick: () => this.chooseStyle(f.id), onkeydown: onArrow },
                h('span', { class: 'style-option-name', text: f.label }), h('span', { class: 'style-option-desc', text: f.description }), swatch);
            });
            const enabled = !!chosen; // the overrides refine a chosen style
            const overrides = enabled ? cleanOverrides(s.style_overrides, this.styleOptions().options) : {};
            const words = v => (v === 'default' ? 'Style default' : `${v.charAt(0).toUpperCase()}${v.slice(1).replace(/_/g, ' ')}`);
            const field = key => {
                const id = `cinematic-style-${key.replace(/_/g, '-')}`;
                const current = overrides[key] || 'default';
                return h('div', { class: 'presenter-field' },
                    h('label', { class: 'config-label', for: id, text: STYLE_OVERRIDE_LABELS[key] }),
                    h('select', { id, class: 'neon-input ui-focusable', 'data-override': key, disabled: !enabled, onchange: e => this.setOverride(key, e.target.value) },
                        this.overrideOptions(key).map(v => h('option', { value: v, text: words(v), selected: v === current }))));
            };
            const rows = [];
            for (let i = 0; i < STYLE_MAIN.length; i += 2) rows.push(h('div', { class: 'presenter-row' }, STYLE_MAIN.slice(i, i + 2).map(field)));
            const advancedSet = STYLE_ADVANCED.some(k => overrides[k]);
            const advanced = h('details', { class: 'style-advanced', 'data-disabled': enabled ? null : 'true',
                open: enabled && (this.advancedOpen === null ? advancedSet : this.advancedOpen),
                ontoggle: e => { if (enabled) this.advancedOpen = !!(e.target && e.target.open); } },
            h('summary', { text: 'Advanced' }), h('div', { class: 'presenter-row' }, STYLE_ADVANCED.map(field)));
            const notes = [];
            if (!chosen) notes.push(h('p', { class: 'presenter-note style-note style-note-legacy', text: 'This lesson keeps its original look. Choose a style to restyle it.' }));
            const suggestion = chosen ? this.styleSuggestion(chosen) : null;
            if (suggestion) {
                notes.push(h('div', { class: 'presenter-note style-suggest' }, h('span', { class: 'style-suggest-text', text: suggestion.text }),
                    h('button', { type: 'button', class: 'neon-btn neon-btn-outline style-suggest-apply', text: "Use the style's suggestions",
                        onclick: () => this.applySuggestions() })));
            }
            notes.push(h('p', { class: 'presenter-note style-note', text: 'Changes the look only — pictures and clips are not made again.' }));
            return h('div', { class: 'style-section', role: 'group', 'aria-labelledby': 'cinematic-style-label' },
                h('div', { class: 'config-label style-heading', id: 'cinematic-style-label', text: 'Video style' }),
                h('div', { class: 'style-picker', role: 'radiogroup', 'aria-labelledby': 'cinematic-style-label' }, options),
                notes,
                h('div', { class: 'style-overrides', 'data-disabled': enabled ? null : 'true', 'aria-disabled': enabled ? null : 'true' }, rows, advanced));
        }

        set(key, value) { this.update({ [key]: value }); }

        // The settings path every user change takes: the values, saved, the panel drawn again, one change event
        update(changes) {
            Object.keys(changes || {}).forEach(key => {
                const value = changes[key];
                const before = this.settings[key];
                this.settings[key] = value === '' ? null : value;
                if (key === 'background' && value !== before) { // a picture chosen for one kind of background never stands in for another
                    this.settings.background_asset_id = null;
                    this.settings.background_label = null;
                }
            });
            saveSettings(this.storage, this.settings);
            this.status = '';
            this.render();
            this.onChange(this.settings);
        }

        chooseBackground() {
            if (!this.pickBackground) return;
            const kind = this.settings.background === 'video' ? 'video' : 'image';
            this.pickBackground({
                kinds: [kind], title: 'Choose a background',
                onPick: asset => {
                    this.settings.background_asset_id = asset.id;
                    this.settings.background_label = asset.file_name || (kind === 'video' ? 'chosen clip' : 'chosen picture'); // never an asset id
                    saveSettings(this.storage, this.settings);
                    this.status = `Background: ${this.settings.background_label}`;
                    this.render();
                    this.onChange(this.settings);
                }
            });
        }

        // An AI background is made only when asked (it may be billed), once per request: the AI cache reuses it
        async generateBackground(force = false) {
            this.busy = true;
            this.status = 'Generating the background…';
            this.render();
            try {
                // the style family's words for the prompt (a legacy lesson keeps its typography's words and its cached backgrounds)
                let result = await this.api.background({ typography: this.settings.typography, style: this.settings.style || undefined,
                    project_id: this.projectId() || undefined,
                    force_regenerate: !!force, wait: !this.follow });
                if (result.httpStatus === 202 && result.run_id && this.follow) result = await this.follow(result.run_id);
                if (!result || !result.asset_id) throw new Error('no background was returned');
                this.settings.background_asset_id = result.asset_id;
                this.settings.background_label = 'AI background';
                saveSettings(this.storage, this.settings);
                this.status = result.cache_hit ? 'Reused the background made earlier for the same style (nothing new was generated).'
                    : `Background generated and added to the lesson${this.debugOn() && result.provider ? ` (by ${result.provider})` : ''}.`;
                this.onChange(this.settings);
            } catch (e) {
                // what happened, that the lesson is safe, and what to do; the server's own words only in debug
                this.status = 'No AI background was made. Your lesson is safe: the clean gradient is used instead. '
                    + 'Try again later, or choose a picture from your Library.'
                    + (this.debugOn() && e && e.message ? ` (${e.message})` : '');
            } finally {
                this.busy = false;
                this.render();
            }
        }

        // The element that has the keyboard focus inside the panel, as a selector to find it again once the panel is drawn
        // again (every choice draws it again); null when the focus is elsewhere
        focusKey() {
            const active = this.doc.activeElement;
            if (!active || active === this.doc.body || typeof this.container.contains !== 'function' || !this.container.contains(active)) return null;
            if (active.id) return `#${active.id}`;
            const attr = ['data-style', 'data-action', 'data-override'].find(a => typeof active.getAttribute === 'function' && active.getAttribute(a));
            return attr ? `[${attr}="${active.getAttribute(attr)}"]` : null;
        }

        render() {
            const h = (...a) => el(this.doc, ...a);
            const s = this.settings;
            const focus = this.pendingFocus || this.focusKey();
            this.pendingFocus = null;
            this.container.textContent = '';
            const select = (id, label, key, options, extra = {}) => h('div', { class: 'presenter-field' },
                h('label', { class: 'config-label', for: id, text: label }),
                h('select', { id, class: 'neon-input ui-focusable', onchange: e => this.set(key, e.target.value), ...extra },
                    Object.entries(options).map(([value, text]) => h('option', { value, text, selected: String(s[key] || '') === value }))));
            const cinematic = s.mode === 'cinematic';
            const technical = this.debugOn();
            // why AI-assisted help cannot run here: the server's reason (it names the provider) in debug only
            const unavailable = (what, info, rest) => `AI-assisted ${what} is not available on this server`
                + (technical ? ` (${info && info.reason ? info.reason : 'no text model is configured'})` : '') + `: ${rest}`;
            // Phase 21: the disabled transitions say why, next to the control
            const transitions = select('cinematic-transition', 'Scene transitions', 'transitions', TRANSITIONS,
                { disabled: !cinematic, 'aria-describedby': cinematic ? null : 'cinematic-transition-why' });
            if (!cinematic) transitions.appendChild(h('p', { class: 'presenter-note cinematic-field-note', id: 'cinematic-transition-why', text: 'Needs the Cinematic layout.' }));
            const rows = [h('div', { class: 'presenter-row' }, select('cinematic-mode', 'Layout', 'mode', MODES), transitions)];
            if (cinematic) {
                rows.push(this.renderStyle(h)); // Phase 17: the video style comes first of the scene settings
                rows.push(h('div', { class: 'presenter-row' },
                    select('cinematic-background', 'Background', 'background', BACKGROUNDS),
                    select('cinematic-motion', 'Camera movement', 'motion', MOTION_CHOICES)));
                rows.push(h('div', { class: 'presenter-row' }, select('cinematic-composer', 'Composition', 'composer', COMPOSERS),
                    select('cinematic-director', 'Visual direction', 'director', DIRECTORS)));
                const st = this.composerStatus;
                if (s.composer === 'ai') {
                    const info = st && st.providers ? st.providers[this.composerProvider()] : null;
                    rows.push(h('p', { class: 'presenter-note cinematic-note', text: st && !(info && info.available)
                        ? unavailable('composition', info, 'the automatic rules decide every scene.')
                        : 'The AI is asked only about scenes where several elements compete; its suggestion is checked against the rules and never used directly.' }));
                }
                // AI-assisted direction uses the same text model as AI-assisted composition
                const dst = this.directorStatus;
                if (s.director === 'ai') {
                    const info = dst && dst.providers ? dst.providers[this.composerProvider()] : null;
                    rows.push(h('p', { class: 'presenter-note cinematic-note cinematic-director-note', text: dst && !(info && info.available)
                        ? unavailable('visual direction', info, 'the automatic rules decide how every scene teaches.')
                        : 'The AI is asked only about scenes where the best way to teach is not clear; its suggestion is checked against the rules and never used directly.' }));
                }
                rows.push(h('div', { class: 'presenter-row' }, select('cinematic-learners', 'Learners', 'learner_level', LEARNERS)));
                const actions = h('div', { class: 'cinematic-actions' });
                if (s.background === 'image' || s.background === 'video') {
                    const thing = s.background === 'video' ? 'clip' : 'picture';
                    actions.appendChild(h('button', { type: 'button', class: 'neon-btn neon-btn-outline cinematic-action ui-focusable', 'data-action': 'pick-background',
                        text: s.background_asset_id ? `Change ${thing} (${s.background_label || 'chosen'})` : 'Choose from your Library', onclick: () => this.chooseBackground() }));
                }
                if (s.background === 'ai') {
                    actions.appendChild(h('button', { type: 'button', class: 'neon-btn neon-btn-outline cinematic-action ui-focusable', 'data-action': 'generate-background',
                        disabled: this.busy, text: s.background_asset_id ? 'Generate a new background' : 'Generate AI background',
                        title: 'Makes one background picture for this lesson with AI (this may be billed). Asking again for the same style reuses it.',
                        onclick: () => this.generateBackground(!!s.background_asset_id) }));
                }
                if (actions.childNodes.length) rows.push(actions);
                const notes = [];
                if (this.presenterIsMascot() && !['auto', 'studio'].includes(s.background)) {
                    notes.push("Aadhi is filmed in his studio, so his scenes keep it; other backgrounds show with the Aadhi Teacher, an AI presenter or no presenter.");
                }
                if (this.presenterIsMascot()) notes.push("The camera stays still while Aadhi is on screen (his studio clip cannot move with it).");
                notes.forEach(text => rows.push(h('p', { class: 'presenter-note cinematic-note', text })));
            } else {
                rows.push(h('p', { class: 'presenter-note cinematic-note', text: "Classic: Aadhi's original layout. Choose Cinematic to use a video style and scene transitions." }));
            }
            if (this.status) rows.push(h('p', { class: 'presenter-note cinematic-status', role: 'status', text: this.status }));
            this.container.appendChild(h('div', { class: 'presenter-settings cinematic-settings' }, rows));
            // the keyboard focus stays on the control the user was on (or the style the arrow keys moved to)
            const target = focus && typeof this.container.querySelector === 'function' ? this.container.querySelector(focus) : null;
            if (target && typeof target.focus === 'function') target.focus();
        }
    }

    return { BACKGROUNDS, CAMERA, CAMERA_INTENTS, COMPOSERS, DEFAULTS, DIRECTION_SOURCES, DIRECTORS, LEARNERS, MODES, MOTIONS, MOTION_INTENTS,
        MOTION_LEVELS, PREFER, PRESENTER_ROLES, PRESENTER_SIZES, SHOTS, STRATEGY_LABELS, SUBTITLES, TEMPLATES, TRANSITIONS, VERSION, VISUAL_LABELS,
        VISUAL_SIZES, CinematicApi, CinematicSettingsPanel, CinematicStage, applyPlans, bestFraming, cameraConstraints, cameraTransform,
        compositionSummary, directionAiNote, directionOptions, directionSummary, elementStatuses, framingOk, inspectorFacts, isClassic, layerOf,
        lerpFraming, loadSettings, saveSettings, syncSummary, through,
        // Phase 21: captions that fit their band
        captionFit, watchCaptions,
        // Phase 17: the video style
        DEFAULT_STYLE, STYLE_FAMILIES, STYLE_OVERRIDES, STYLE_OVERRIDE_LABELS, cleanStyleChoice, contrastRatio, lookOf, parseColor, styleSummary,
        styleVars, validEase, validStyleVar };
});
