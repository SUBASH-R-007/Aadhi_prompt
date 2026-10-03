'use strict';
// Unit tests for the video styling system in the page (Phase 17, cinematic.js): a scene's look (plan.style.look) applied by
// CinematicStage (CSS variables, body attributes, entrances, emphasis colour, transitions, the presenter's name card, the
// light-style readability guard), the "Video style" settings, the saved lesson's style and the Visual Review summary.
// A plan from before Phase 17 (no look) changes nothing.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../cinematic.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

// ---- the page, a fake clock, a plan (Scene B of tests/cinematic.test.js) ---------------------------------------------

function page() {
    const doc = fakeDoc();
    const add = (parent, tag, attrs = {}) => { const n = doc.createElement(tag); Object.entries(attrs).forEach(([k, v]) => (k === 'class' ? (n.className = v) : n.setAttribute(k, v))); parent.appendChild(n); return n; };
    const zone = add(doc.body, 'div', { class: 'lecture-overlay-zone' });
    const board = add(zone, 'div', { id: 'presentation-board' });
    const content = add(board, 'div', { id: 'slide-content-container' });
    const side = add(doc.body, 'div', { class: 'dynamic-side-zone' });
    const panel = add(side, 'div', { class: 'side-panel-view active' });
    const presenter = add(doc.body, 'div', { id: 'presenter-layer' });
    add(presenter, 'svg', { class: 'teacher-svg' });
    return { doc, add, zone, board, content, side, panel, presenter };
}
function clock() {
    let t = 0; let seq = 0; const timers = new Map();
    return {
        now: () => t,
        setTimeout: (fn, ms) => { const id = ++seq; timers.set(id, { at: t + ms, fn }); return id; },
        clearTimeout: id => timers.delete(id),
        advance(ms) { const end = t + ms; for (;;) { const due = [...timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0]; if (!due) break; timers.delete(due[0]); t = due[1].at; due[1].fn(); } t = end; }
    };
}
const box = (x, y, w, h) => ({ x, y, w, h });
function planB(extra = {}) {
    return {
        version: 1, template: 'presenter_plus_visual', template_label: 'Presenter + visual', shot: 'medium', duration: 12,
        style: { typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto', emphasis: 'clear' },
        background: { type: 'gradient', colors: ['#111111', '#222222', '#333333'] },
        presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 },
        camera: { shot: 'medium', movement: 'static', from: { x: 0, y: 0, w: 1 }, to: { x: 0, y: 0, w: 1 }, start: 0, duration: 12, easing: 'ease-in-out', target: 'visual' },
        layers: [
            { id: 'background', type: 'background', box: box(0, 0, 1, 1), z: 0, start: 0, enter: 'appear', important: false, camera: true },
            { id: 'visual', type: 'visual', role: 'image', box: box(0.05, 0.19, 0.2814, 0.52), z: 20, start: 0.3, enter: 'scale_in', important: true, camera: true },
            { id: 'board', type: 'board', role: 'body', style: 'body', box: box(0.3514, 0.19, 0.3186, 0.62), z: 30, start: 0.1, enter: 'fade_in', important: true, camera: true },
            { id: 'title', type: 'title', role: 'title', variant: 'band', subtitle: true, box: box(0.05, 0.05, 0.62, 0.1), z: 60, start: 0, enter: 'fade_in', important: true, camera: false },
            { id: 'presenter', type: 'presenter', role: 'illustrated', side: 'right', placement: 'side', box: box(0.71, 0.14, 0.25, 0.71), face: box(0.71, 0.14, 0.25, 0.2982), z: 40, start: 0.2, enter: 'slide_in', important: true, camera: true },
            { id: 'subtitles', type: 'subtitles', role: 'caption', box: box(0, 0.85, 1, 0.15), z: 90, start: 0, enter: null, important: false, camera: false }
        ],
        timeline: [], warnings: [], notes: [], review_status: 'pending', ...extra
    };
}
const scene = () => ({ type: 'content', title: 'Inside the leaf', narration: 'Look.' });
function stage(extra = {}) {
    const p = page();
    const c = clock();
    const settings = { ...C.DEFAULTS, mode: 'cinematic' };
    const st = new C.CinematicStage({ doc: p.doc, settings: () => settings, now: c.now, setTimeout: c.setTimeout, clearTimeout: c.clearTimeout,
        exporting: () => !!extra.exporting, reducedMotion: () => !!extra.reduced, presenterName: extra.presenterName });
    return { ...p, clock: c, st };
}

// The real Academic@1 look as styles.plan_look({"style": "academic"}) gives it (every value styles.py makes passes the page's checks)
const ACADEMIC_CSS = {
    "--st-bg-1": "#f4efe3", "--st-bg-2": "#fbf8f1", "--st-bg-3": "#ece5d4", "--st-bg-solid": "#f7f3ea", "--st-backdrop": "#f4efe4",
    "--st-bg-glow-1": "rgba(29, 78, 137, 0.06)", "--st-bg-glow-2": "rgba(155, 35, 53, 0.04)", "--st-vignette": "rgba(60, 40, 10, 0.10)",
    "--st-bg-pattern": "repeating-linear-gradient(0deg, rgba(31, 42, 68, 0.035) 0 1px, transparent 1px 34px)", "--st-scrim": "#f7f3ea",
    "--st-surface-1": "#ffffff", "--st-surface-2": "#fdfbf6", "--st-surface-border": "rgba(31, 42, 68, 0.14)",
    "--st-surface-shadow": "0 10px 30px rgba(31, 42, 68, 0.10), 0 1px 0 rgba(31, 42, 68, 0.04)", "--st-panel": "rgba(31, 42, 68, 0.035)",
    "--st-panel-border": "rgba(31, 42, 68, 0.13)", "--st-definition-bg": "rgba(29, 78, 137, 0.05)", "--st-formula-bg": "rgba(155, 35, 53, 0.04)",
    "--st-formula-border": "rgba(155, 35, 53, 0.28)", "--st-text": "#1c2638", "--st-text-secondary": "#3f4a5e", "--st-text-muted": "#5f6878",
    "--st-heading": "#1d3d6e", "--st-title-text": "#1c2638", "--st-heading-shadow": "none", "--st-title-shadow": "none", "--st-body-shadow": "none",
    "--st-accent": "#9b2335", "--st-accent-rgb": "155, 35, 53", "--st-accent-text": "#8a1f2f", "--st-on-accent": "#ffffff",
    "--st-accent-2-rgb": "155, 35, 53", "--st-date-text": "#8a1f2f", "--st-secondary": "#1d4e89", "--st-secondary-rgb": "29, 78, 137",
    "--st-focus-rgb": "29, 78, 137", "--st-info": "#1d4e89", "--st-success": "#2f6f4f", "--st-error": "#9b2335",
    "--st-callout-bg": "rgba(31, 42, 68, 0.04)", "--st-callout-text": "#1c2638", "--st-table-bg": "#ffffff",
    "--st-table-border": "rgba(31, 42, 68, 0.18)", "--st-table-head-bg": "rgba(29, 78, 137, 0.08)", "--st-table-head-text": "#1d3d6e",
    "--st-row-border": "rgba(31, 42, 68, 0.10)", "--st-stripe": "rgba(31, 42, 68, 0.025)", "--st-code-bg": "#1e2433",
    "--st-code-border": "rgba(31, 42, 68, 0.25)", "--st-output-bg": "#eef3f8", "--st-output-border": "rgba(29, 78, 137, 0.35)",
    "--st-output-text": "#1c2638", "--st-output-label": "#1d4e89", "--st-caption-text": "#ffffff", "--st-caption-bg": "rgba(20, 24, 36, 0.80)",
    "--st-caption-highlight": "#ffd479", "--st-caption-shadow": "none", "--st-caption-size": "1.8rem", "--st-label-bg": "#ffffff",
    "--st-label-border": "rgba(155, 35, 53, 0.45)", "--st-label-text": "#1c2638", "--st-title-bar": "#9b2335",
    "--st-presenter-border": "rgba(31, 42, 68, 0.16)", "--st-presenter-radius": "8px", "--st-presenter-shadow": "0 8px 24px rgba(31, 42, 68, 0.14)",
    "--st-presenter-drop": "drop-shadow(0 6px 12px rgba(31, 42, 68, 0.18))", "--st-visual-bg": "#ffffff", "--st-visual-border": "rgba(31, 42, 68, 0.14)",
    "--st-visual-radius": "8px", "--st-visual-shadow": "0 8px 24px rgba(31, 42, 68, 0.10)", "--st-chart-1": "#1d4e89", "--st-chart-2": "#9b2335",
    "--st-chart-3": "#2f7d6d", "--st-chart-4": "#b7791f", "--st-chart-grid": "rgba(31, 42, 68, 0.10)", "--st-chart-tick": "#3f4a5e",
    "--st-font-title": "Georgia, 'Times New Roman', serif", "--st-font-heading": "Georgia, 'Times New Roman', serif",
    "--st-font-body": "'Inter', sans-serif", "--st-font-code": "'JetBrains Mono', monospace", "--st-font-caption": "'Inter', sans-serif",
    "--st-title-weight": "700", "--st-heading-weight": "700", "--st-body-weight": "400", "--st-heading-case": "none", "--st-title-case": "none",
    "--st-title-tracking": "0", "--st-heading-tracking": "0", "--st-text-scale": "1", "--st-line-height": "1.5",
    "--st-label-size": "clamp(0.8rem, 1.1vw, 1.2rem)", "--st-space-xs": "0.3em", "--st-space-sm": "0.55em", "--st-space-md": "0.8em",
    "--st-space-lg": "1.2em", "--st-space-xl": "1.6em", "--st-board-pad": "clamp(0.9rem, 1.9vw, 2.2rem) clamp(1rem, 2.3vw, 2.6rem)",
    "--st-radius-sm": "3px", "--st-radius-md": "0.35em", "--st-radius-lg": "clamp(6px, 0.7vw, 10px)", "--st-radius-chip": "4px",
    "--st-ease": "cubic-bezier(0.22, 1, 0.36, 1)", "--st-motion-fast": "0.25s", "--st-motion-normal": "0.4s", "--st-motion-slow": "0.6s",
    "--st-entrance-ms": "500", "--st-entrance-shift": "0", "--st-code-scale": "1", "--st-formula-scale": "1"
};
const academic = (extra = {}) => ({ schema: 1, id: 'academic@1', family: 'academic', version: 1, label: 'Academic', variant: null, tone: 'light',
    prefs: { tone: 'light', emphasis: 'outline', caption: 'box', presenter_frame: 'clean', presenter_label: false, visual_frame: 'card', pattern: 'paper',
        motion: 'low', camera: 'subtle', transition: 'fade' },
    overrides: {}, scene_overrides: {}, adjustments: [], fingerprint: '424cb5b481bbeca2', css: { ...ACADEMIC_CSS }, ...extra });
// A dark look with a few tokens (Corporate Training's values)
const corporate = (css = {}, prefs = {}) => ({ schema: 1, id: 'corporate_training@1', family: 'corporate_training', version: 1, label: 'Corporate Training',
    variant: null, tone: 'dark',
    prefs: { tone: 'dark', emphasis: 'outline', caption: 'shadow', presenter_frame: 'card', presenter_label: true, visual_frame: 'card', pattern: 'grid',
        motion: 'standard', camera: 'subtle', transition: 'crossfade', ...prefs },
    overrides: { accent: 'teal' }, scene_overrides: { background: 'plain' }, adjustments: ['dates made darker or lighter to stay readable'],
    fingerprint: 'c0ffee0123456789',
    css: { '--st-bg-1': '#0b1224', '--st-bg-2': '#1b2a4a', '--st-surface-1': 'rgba(17, 27, 48, 0.95)', '--st-text': '#e5e9f0', '--st-heading': '#7fd4ff',
        '--st-accent': '#38bdf8', '--st-entrance-ms': '450', '--st-entrance-shift': '0.6', '--st-ease': 'cubic-bezier(0.4, 0, 0.2, 1)',
        '--st-focus-rgb': '56, 189, 248', ...css } });
const withLook = (look, extra = {}) => planB({ style: { ...planB().style, look }, ...extra });
const stVars = doc => Object.entries(doc.documentElement.style._props).filter(([k, v]) => k.startsWith('--st-') && v !== '');
const STYLE_ATTRS = ['data-cine-style', 'data-cine-tone', 'data-cine-emphasis', 'data-cine-caption', 'data-cine-pframe', 'data-cine-vframe', 'data-cine-motion-level'];
const attrs = doc => Object.fromEntries(STYLE_ATTRS.map(a => [a, doc.body.getAttribute(a)]));

// ---- the look on the page --------------------------------------------------------------------------------------------

test('a look is applied (its CSS variables on <html>, the body attributes) and fully removed', () => {
    const { st, doc } = stage();
    st.layout(scene(), withLook(academic()), 0);
    const root = doc.documentElement.style;
    assert.equal(stVars(doc).length, 109, 'every token of the real Academic look');
    assert.equal(root.getPropertyValue('--st-bg-1'), '#f4efe3');
    assert.equal(root.getPropertyValue('--st-font-title'), "Georgia, 'Times New Roman', serif");
    assert.equal(st.stats.styleSkipped, 0);
    assert.deepEqual(attrs(doc), { 'data-cine-style': 'academic', 'data-cine-tone': 'light', 'data-cine-emphasis': 'outline', 'data-cine-caption': 'box',
        'data-cine-pframe': 'clean', 'data-cine-vframe': 'card', 'data-cine-motion-level': 'low' });
    assert.equal(st.state().style, 'academic@1');
    // the next scene's look: the variables it does not set are removed, the others replaced
    st.layout(scene(), withLook(corporate()), 1);
    assert.equal(stVars(doc).length, 10);
    assert.equal(root.getPropertyValue('--st-bg-1'), '#0b1224');
    assert.equal(root.getPropertyValue('--st-font-title'), '');
    assert.equal(doc.body.getAttribute('data-cine-tone'), 'dark');
    assert.equal(doc.body.getAttribute('data-cine-pframe'), 'card');
    // an unknown value in the look's preferences is not set
    st.layout(scene(), withLook(corporate({}, { emphasis: 'neon', presenter_frame: '"><script>' })), 2);
    assert.equal(doc.body.getAttribute('data-cine-emphasis'), null);
    assert.equal(doc.body.getAttribute('data-cine-pframe'), null);
    assert.equal(doc.body.getAttribute('data-cine-caption'), 'shadow');
    st.clear();
    assert.deepEqual(stVars(doc), []);
    assert.ok(STYLE_ATTRS.every(a => doc.body.getAttribute(a) === null));
    assert.equal(doc.body.getAttribute('data-cinematic'), null);
    assert.equal(st.state().style, null);
});

test('a plan from before Phase 17 (no look) sets nothing: today\'s look from the CSS fallbacks', () => {
    const { st, doc } = stage();
    st.layout(scene(), planB(), 0);
    assert.deepEqual(stVars(doc), []);
    assert.ok(STYLE_ATTRS.every(a => doc.body.getAttribute(a) === null));
    assert.equal(doc.body.getAttribute('data-cine-typography'), 'academic'); // the Phase 13 attribute, as before
    assert.deepEqual(Object.keys(st.stats), ['scenes', 'cameraMoves', 'anchored', 'fitted', 'lastTemplate', 'lastFit', 'syncFired', 'syncLast']);
    assert.equal(st.state().style, null);
    // after a styled scene, an old plan removes what the look set
    st.layout(scene(), withLook(academic()), 1);
    st.layout(scene(), planB({ style: { ...planB().style, look: 'academic' } }), 2); // not an object: no look
    assert.deepEqual(stVars(doc), []);
    assert.equal(doc.body.getAttribute('data-cine-style'), null);
    assert.equal(C.lookOf(planB()), null);
    assert.equal(C.lookOf(null), null);
});

test('unsafe variable names and values are skipped (url(), expression, ";", "}"), the rest applied', () => {
    const bad = { '--st-Bad': '#fff', '--x-color': '#fff', ['--st-' + 'a'.repeat(41)]: '#fff', '--st-img': 'url(https://evil.example/x.png)',
        '--st-img2': 'URL(x)', '--st-expr': 'expression(alert(1))', '--st-semi': 'red; background: blue', '--st-brace': '#fff}body{color:red',
        '--st-quote': 'a"b', '--st-long': 'x'.repeat(201), '--st-obj': { v: 1 }, constructor: 'x' };
    const { st, doc } = stage();
    st.layout(scene(), withLook(corporate(bad)), 0);
    const names = stVars(doc).map(([k]) => k).sort();
    assert.deepEqual(names, ['--st-accent', '--st-bg-1', '--st-bg-2', '--st-ease', '--st-entrance-ms', '--st-entrance-shift', '--st-focus-rgb', '--st-heading',
        '--st-surface-1', '--st-text']);
    assert.equal(st.stats.styleSkipped, Object.keys(bad).length);
    assert.equal(C.validStyleVar('--st-bg-1', 'rgba(1, 2, 3, 0.5)'), true);
    assert.equal(C.validStyleVar('--st-font', "'Inter', sans-serif"), true);
    assert.equal(C.validStyleVar('--st-x', 'uRl(a)'), false);
    assert.equal(C.validStyleVar('--st-x', 'Expression(a)'), false);
    assert.equal(C.validStyleVar('--st-x', 'a;b'), false);
    assert.equal(C.validStyleVar('--st-x', '}'), false);
    assert.equal(C.validStyleVar('--st-x', 'a\\62 c'), false); // a CSS escape
    assert.equal(C.validStyleVar('--st-x', 5), false);
    assert.equal(C.validStyleVar('--sT-x', '#fff'), false);
    // the allowlist (security audit): no image or reference function, ASCII only, quotes only around font names
    for (const v of ["image-set('https://evil.example/x.png' 1x)", "-webkit-image-set('//e/x' 1x)", "cross-fade('//e/a', '//e/b', 50%)",
        "src('//e/x')", 'var(--x)', 'attr(title)', 'env(x)', 'paint(x)', '(1px)', '\uff55\uff52\uff4c(x)', 'red\u2028', 'a\tb', "'Inter", "'a1'"]) {
        assert.equal(C.validStyleVar('--st-x', v), false, v);
    }
    for (const v of ['radial-gradient(rgba(31, 42, 68, 0.1) 1.5px, transparent 1.6px) 0 0 / 26px 26px', 'drop-shadow(0 8px 18px rgba(0, 0, 0, 0.45))',
        'cubic-bezier(0.22, 1, 0.36, 1)', 'clamp(0.8rem, 1.1vw, 1.2rem)', "Georgia, 'Times New Roman', serif"]) {
        assert.equal(C.validStyleVar('--st-x', v), true, v);
    }
    assert.equal(C.parseColor('rgb(' + '1, '.repeat(40) + '1)'), null); // long input: refused at once
});

// ---- entrances and emphasis --------------------------------------------------------------------------------------------

const presenterAnim = s => s.presenter.firstElementChild.animations[0];
const titleAnim = s => s.doc.querySelector('#cine-title .cine-title-inner').animations[0];
function play(s, plan) { s.st.layout(scene(), plan, 0); s.st.start(scene(), s.st.plan); }

test('entrances: today\'s lengths, easing and distances without a look; the look\'s with one (read once per scene)', () => {
    const plain = stage();
    play(plain, planB());
    assert.deepEqual([plain.panel.animations[0].opts.duration, plain.panel.animations[0].opts.easing], [650, 'cubic-bezier(0.22, 1, 0.36, 1)']);
    assert.deepEqual(plain.panel.animations[0].frames[0], { opacity: 0, scale: '0.96' });
    assert.equal(presenterAnim(plain).frames[0].translate, '3vw 0');
    assert.equal(presenterAnim(plain).opts.duration, 650);
    assert.equal(titleAnim(plain).opts.duration, 500);
    // a look: its entrance length for every entrance, its easing, its distance (0.6 of today's)
    const styled = stage();
    play(styled, withLook(corporate()));
    assert.deepEqual([styled.panel.animations[0].opts.duration, styled.panel.animations[0].opts.easing], [450, 'cubic-bezier(0.4, 0, 0.2, 1)']);
    assert.deepEqual(styled.panel.animations[0].frames[0], { opacity: 0, scale: '0.976' });
    assert.equal(presenterAnim(styled).frames[0].translate, '1.8vw 0');
    assert.equal(presenterAnim(styled).opts.duration, 450);
    assert.equal(titleAnim(styled).opts.duration, 450);
    const left = stage();
    play(left, withLook(corporate({ '--st-entrance-shift': '1.2' }), { layers: planB().layers.map(l => (l.id === 'presenter' ? { ...l, side: 'left' } : l)) }));
    assert.equal(presenterAnim(left).frames[0].translate, '-3.6vw 0');
    // shift 0 (a "low" motion style): slides and scales become plain fades
    const low = stage();
    play(low, withLook(academic()));
    assert.deepEqual(presenterAnim(low).frames, [{ opacity: 0 }, { opacity: 1 }]);
    assert.deepEqual(low.panel.animations[0].frames, [{ opacity: 0 }, { opacity: 1 }]);
    assert.equal(presenterAnim(low).opts.duration, 500);
    // bounded and checked: 150–1200 ms, a shift up to 1.5, an easing the Web Animations API accepts
    const wild = stage();
    play(wild, withLook(corporate({ '--st-entrance-ms': '5000', '--st-entrance-shift': '9', '--st-ease': 'steps(4)' })));
    assert.deepEqual([presenterAnim(wild).opts.duration, presenterAnim(wild).opts.easing, presenterAnim(wild).frames[0].translate],
        [1200, 'cubic-bezier(0.22, 1, 0.36, 1)', '4.5vw 0']);
    const quick = stage();
    play(quick, withLook(corporate({ '--st-entrance-ms': '20', '--st-ease': 'cubic-bezier(1.5, 0, 0, 1)' })));
    assert.deepEqual([presenterAnim(quick).opts.duration, presenterAnim(quick).opts.easing], [150, 'cubic-bezier(0.22, 1, 0.36, 1)']);
    const words = stage();
    play(words, withLook(corporate({ '--st-ease': 'ease-out', '--st-entrance-ms': 'fast' })));
    assert.deepEqual([presenterAnim(words).opts.easing, presenterAnim(words).opts.duration], ['ease-out', 650]); // no usable length: today's
    assert.equal(C.validEase('linear'), 'linear');
    assert.equal(C.validEase('cubic-bezier(0.4,0,0.2,1)'), 'cubic-bezier(0.4, 0, 0.2, 1)');
    assert.equal(C.validEase('var(--x)'), null);
    // reduced motion in the preview still turns every entrance into a fade (with the look's length)
    const calm = stage({ reduced: true });
    play(calm, withLook(corporate()));
    assert.deepEqual(presenterAnim(calm).frames, [{ opacity: 0 }, { opacity: 1 }]);
    // a synchronized emphasis keeps its event's own length
    assert.equal(styled.st.animate(styled.panel, 'fade_in', null, 0, 900).opts.duration, 900);
});

test('the highlight pulse takes the look\'s focus colour (gold without a look, or with an unusable one)', () => {
    const glow = s => s.st.animate(s.panel, 'highlight', null, 0).frames[1].boxShadow;
    const plain = stage();
    plain.st.layout(scene(), planB(), 0);
    assert.equal(glow(plain), '0 0 0 6px rgba(255, 215, 0, 0.55)');
    assert.equal(plain.st.animate(plain.panel, 'highlight', null, 0).opts.duration, 1600);
    const styled = stage();
    styled.st.layout(scene(), withLook(academic()), 0);
    assert.equal(glow(styled), '0 0 0 6px rgba(29, 78, 137, 0.55)');
    assert.equal(styled.st.animate(styled.panel, 'highlight', null, 0).frames[0].boxShadow, '0 0 0 0 rgba(29, 78, 137, 0)');
    styled.st.layout(scene(), withLook(corporate({ '--st-focus-rgb': '300, 0, 0' })), 1);
    assert.equal(glow(styled), '0 0 0 6px rgba(255, 215, 0, 0.55)');
    styled.st.layout(scene(), planB(), 2); // back to a plan without a look: gold again
    assert.equal(glow(styled), '0 0 0 6px rgba(255, 215, 0, 0.55)');
});

// ---- the presenter's name card ----------------------------------------------------------------------------------------

test('the presenter name card rides inside the presenter layer: only with presenter_label and a name, as text, never for Aadhi', () => {
    const cards = s => s.presenter.children.filter(c => c.classList && c.classList.contains('cine-presenter-name'));
    const shown = s => cards(s).some(c => c.getAttribute('data-state') === 'shown' && c.textContent !== '');
    const asked = [];
    const s = stage({ presenterName: plan => { asked.push(plan.presenter.presenter_id); return '  Ms.   Priya  '; } });
    s.st.layout(scene(), withLook(corporate()), 0);
    assert.ok(shown(s));
    const node = cards(s)[0];
    assert.equal(node.parent, s.presenter, 'a child of #presenter-layer (the presenter\'s box and the camera\'s transform)');
    assert.equal(node.className, 'cine-presenter-name');
    assert.equal(node.textContent, 'Ms. Priya');
    assert.deepEqual(asked, ['aadhi-teacher']);
    assert.deepEqual(Object.keys(node.style._props), [], 'placed by the page\'s CSS: no inline position');
    assert.equal(s.doc.querySelectorAll('.cine-presenter-name').length, 1, 'never a separate overlay');
    // the presenter's entrance still animates the presenter, never its name card
    s.st.start(scene(), s.st.plan);
    assert.equal(s.presenter.firstElementChild.animations.length, 1);
    assert.equal(node.animations.length, 0);
    // the next scene reuses the card
    s.st.layout(scene(), withLook(corporate()), 1);
    assert.equal(cards(s).length, 1);
    // a style without the label (Academic): no card
    s.st.layout(scene(), withLook(academic()), 2);
    assert.equal(shown(s), false);
    assert.equal(cards(s)[0].getAttribute('data-state'), 'hidden');
    // a hidden presenter, a plan without a look, no presenter layer in the plan: no card
    s.st.layout(scene(), withLook(corporate(), { presenter: { ...planB().presenter, shown: false } }), 3);
    assert.equal(shown(s), false);
    s.st.layout(scene(), planB(), 4);
    assert.equal(shown(s), false);
    s.st.layout(scene(), withLook(corporate(), { layers: planB().layers.filter(l => l.id !== 'presenter') }), 5);
    assert.equal(shown(s), false);
    // Aadhi (the mascot): his studio clip fills the frame, never a card
    s.st.layout(scene(), withLook(corporate(), { presenter: { type: 'mascot', presenter_id: 'aadhi', shown: true, side: 'right' } }), 6);
    assert.equal(shown(s), false);
    // a presenter layer the presenter left empty (an AI presenter without its clip) or hidden: no card
    s.presenter.setAttribute('data-state', 'missing');
    s.st.layout(scene(), withLook(corporate()), 7);
    assert.equal(shown(s), false);
    s.presenter.setAttribute('data-state', 'idle');
    s.st.layout(scene(), withLook(corporate()), 8);
    assert.ok(shown(s));
    s.st.clear();
    assert.equal(shown(s), false);
    // the card never stands in for the presenter, even when it comes first in the layer
    const first = stage({ presenterName: () => 'Ms. Priya' });
    first.presenter.children.length = 0;
    first.st.layout(scene(), withLook(corporate()), 0);
    assert.equal(first.st.element('presenter'), null);
    const svg = first.add(first.presenter, 'svg', { class: 'teacher-svg' });
    assert.equal(first.st.element('presenter'), svg);
    // no presenter layer element on the page: no card anywhere
    const bare = stage({ presenterName: () => 'Ms. Priya' });
    bare.presenter.id = 'somewhere-else';
    bare.st.layout(scene(), withLook(corporate()), 0);
    assert.equal(bare.doc.querySelectorAll('.cine-presenter-name').length, 0);
    // no name from the page (or no presenterName option, or one that fails): no card
    const none = stage({ presenterName: () => '   ' });
    none.st.layout(scene(), withLook(corporate()), 0);
    assert.equal(shown(none), false);
    const noOption = stage();
    noOption.st.layout(scene(), withLook(corporate()), 0);
    assert.equal(shown(noOption), false);
    const failing = stage({ presenterName: () => { throw new Error('no presenter'); } });
    failing.st.layout(scene(), withLook(corporate()), 0);
    assert.equal(shown(failing), false);
    // a name that looks like HTML stays text
    const evil = '<img src=x onerror=alert(1)>';
    const html = stage({ presenterName: () => evil });
    html.st.layout(scene(), withLook(corporate()), 0);
    const n = cards(html)[0];
    assert.equal(n.textContent, evil);
    assert.ok(n.children.every(c => c.tag === '#text'));
    assert.equal(n._html, undefined, 'innerHTML is never used');
    assert.equal(html.doc.querySelector('img'), null);
});

// ---- the light-style readability guard ----------------------------------------------------------------------------------

test('a light style replaces an inline text colour that does not read on its surface; readable ones, code and own backgrounds stay', () => {
    const s = stage();
    const colored = (parent, tag, color, bg) => {
        const n = s.add(parent, tag, { style: `color: ${color}` });
        n.style.color = color;
        if (bg) n.style.backgroundColor = bg;
        return n;
    };
    const faint = colored(s.content, 'p', '#cccccc');          // 1.6:1 on white
    const yellow = colored(s.content, 'span', 'rgb(255, 215, 0)'); // the dark style's gold: 1.4:1 on white
    const readable = colored(s.content, 'span', '#1c2638');   // 15:1
    const pre = s.add(s.content, 'pre');
    const code = colored(pre, 'span', '#eeeeee');              // a code panel stays dark in every style
    const chip = colored(s.content, 'span', '#ffffff', '#9b2335'); // white on its own crimson background
    const already = colored(s.content, 'span', 'var(--st-text)');
    const plainP = s.add(s.content, 'p', { style: 'margin: 0' }); // inline style without a colour
    s.st.layout(scene(), withLook(academic()), 0);
    assert.equal(s.st.prepareBoard(), 0); // the page calls it once the board is drawn (no sync plan: nothing hidden)
    assert.equal(faint.style.getPropertyValue('color'), 'var(--st-text)');
    assert.equal(yellow.style.color, 'var(--st-text)');
    assert.equal(readable.style.color, '#1c2638');
    assert.equal(code.style.color, '#eeeeee');
    assert.equal(chip.style.color, '#ffffff');
    assert.equal(already.style.color, 'var(--st-text)');
    assert.equal(plainP.style.color, '');
    assert.equal(s.st.stats.contrastFixed, 2);
    s.st.start(scene(), s.st.plan); // runs again after typesetting: already fixed, nothing more
    assert.equal(s.st.stats.contrastFixed, 2);
    // back to Classic: the lesson's own colours come back
    s.st.clear();
    assert.equal(faint.style.color, '#cccccc');
    assert.equal(yellow.style.color, 'rgb(255, 215, 0)');
    // a dark style (or a plan without a look) leaves the board as the lesson wrote it
    s.st.layout(scene(), withLook(corporate()), 1);
    s.st.prepareBoard();
    assert.equal(faint.style.color, '#cccccc');
    s.st.layout(scene(), planB(), 2);
    s.st.prepareBoard();
    assert.equal(faint.style.color, '#cccccc');
    // at most 400 coloured elements are looked at
    const many = stage();
    const list = [];
    for (let i = 0; i < 450; i++) { const n = many.add(many.content, 'span', { style: 'color: #dddddd' }); n.style.color = '#dddddd'; list.push(n); }
    many.st.layout(scene(), withLook(academic()), 0);
    many.st.prepareBoard();
    assert.equal(many.st.stats.contrastFixed, 400);
    assert.equal(list[449].style.color, '#dddddd');
});

test('the contrast helper: WCAG ratios as styles.py computes them', () => {
    assert.equal(C.contrastRatio('#999999', '#ffffff'), 2.85);
    assert.equal(C.contrastRatio('#1c2638', '#ffffff'), 15.18);
    assert.equal(C.contrastRatio('rgba(255, 255, 255, 0.95)', 'rgba(40, 19, 82, 0.92)'), 15.07); // translucent: composited
    assert.equal(C.contrastRatio('#000', '#fff'), 21);
    assert.equal(C.contrastRatio('red', '#fff'), null); // a value it cannot read
    assert.equal(C.contrastRatio('#fff', 'linear-gradient(red, blue)'), null);
    assert.deepEqual(C.parseColor('#abc'), [170, 187, 204, 1]);
    assert.deepEqual(C.parseColor('rgba(10, 20, 30, 0.5)'), [10, 20, 30, 0.5]);
    assert.deepEqual(C.parseColor('rgb(10 20 30 / 50%)'), [10, 20, 30, 0.5]);
    assert.deepEqual(C.parseColor('transparent'), [0, 0, 0, 0]);
    assert.equal(C.parseColor('url(x)'), null);
});

// ---- transitions ----------------------------------------------------------------------------------------------------------

test('transitions: soft fade, zoom and wipe are accepted; moving ones become fades for reduced motion in the preview only', () => {
    const t = (inKind, duration) => planB({ transition: { in: inKind, out: inKind, duration } });
    const a = stage();
    const html = a.doc.documentElement;
    assert.equal(a.st.prepareTransition(t('soft_fade', 0.8)), 'soft_fade');
    assert.equal(html.getAttribute('data-cine-transition'), 'soft_fade');
    assert.equal(html.style.getPropertyValue('--cine-transition-seconds'), '0.8s');
    assert.equal(a.st.prepareTransition(t('zoom', 0.6)), 'zoom');
    assert.equal(a.st.prepareTransition(t('wipe', 0.6)), 'wipe');
    assert.equal(html.style.getPropertyValue('--cine-transition-seconds'), '0.6s');
    assert.equal(a.st.prepareTransition(t('spin', 0.6)), 'fade'); // a kind the page does not know
    assert.equal(a.st.prepareTransition(t('constructor', 0.6)), 'fade');
    assert.equal(a.st.prepareTransition(t('fade', 99)), 'fade');
    assert.equal(html.style.getPropertyValue('--cine-transition-seconds'), '3s');
    const calm = stage({ reduced: true });
    assert.deepEqual(['slide', 'zoom', 'wipe', 'soft_fade', 'crossfade', 'cut'].map(k => calm.st.prepareTransition(t(k, 0.6))),
        ['fade', 'fade', 'fade', 'soft_fade', 'crossfade', 'cut']);
    const recording = stage({ reduced: true, exporting: true });
    assert.equal(recording.st.prepareTransition(t('zoom', 0.6)), 'zoom'); // the video follows the plan
    assert.equal(Object.fromEntries(C.inspectorFacts(t('soft_fade', 0.8))).Transition, 'Soft fade');
    assert.deepEqual(Object.keys(C.TRANSITIONS), ['fade', 'soft_fade', 'crossfade', 'slide', 'zoom', 'wipe', 'cut']);
});

// ---- the "Video style" settings ------------------------------------------------------------------------------------------

const familyCss = (bg, extra = {}) => ({ '--st-bg-1': bg, '--st-surface-1': '#ffffff', '--st-accent': '#38bdf8', '--st-bad': 'url(x)', ...extra });
const LOOKS = {
    default: 'cinematic_education',
    families: [
        { id: 'academic', version: 1, label: 'Academic', description: 'Clean and structured', tone: 'light', prefs: { transition: 'fade', camera: 'subtle' }, css: familyCss('#f4efe3') },
        { id: 'cinematic_education', version: 1, label: 'Cinematic Education', description: 'Rich, deep and focused', tone: 'dark', prefs: { transition: 'fade', camera: 'subtle' }, css: familyCss('#140a2e') },
        { id: 'children_education', version: 1, label: "Children's Education", description: 'Friendly and engaging', tone: 'light', prefs: { transition: 'soft_fade', camera: 'subtle' }, css: familyCss('#fff4e0') },
        { id: 'corporate_training', version: 2, label: 'Corporate Training', description: 'Professional and focused', tone: 'dark', prefs: { transition: 'crossfade', camera: 'still' }, css: familyCss('#0b1224') }
    ],
    options: { accent: ['default', 'gold', 'coral', 'crimson', 'teal', 'sky', 'violet', 'emerald'], text_size: ['default', 'standard', 'large', 'larger'],
        motion: ['default', 'low', 'standard'], background: ['default', 'plain', 'subtle', 'rich'], caption_size: ['default', 'standard', 'large'],
        code_size: ['default', 'standard', 'large'], formula_size: ['default', 'standard', 'large'], diagram_frame: ['default', 'panel', 'card', 'rounded', 'plain'],
        scene: ['accent', 'background'] }
};
function settingsPanel(extra = {}) {
    const doc = fakeDoc();
    const container = doc.createElement('div');
    const store = extra.store || {};
    const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    const changes = [];
    const styles = [];
    const calls = [];
    const api = { vocabulary: async () => ({ composer: null, director: null, looks: extra.looks === undefined ? LOOKS : extra.looks }),
        background: async body => { calls.push(body); return { asset_id: 'a'.repeat(32), cache_hit: false }; } };
    const panel = new C.CinematicSettingsPanel({ doc, container, api, storage, onChange: s => changes.push({ ...s }), onStyleChange: c => styles.push(c) });
    return { doc, container, store, panel, changes, styles, calls, saved: () => JSON.parse(store['aadhi.cinematic'] || '{}') };
}
const pick = (container, id) => container.querySelector(`.style-option[data-style="${id}"]`);
const choose = (select, value) => { select.value = value; select.fire('change'); };

const overridesDisabled = container => {
    const selects = container.querySelector('.style-overrides').querySelectorAll('select');
    return { selects: selects.length, disabled: selects.filter(sel => sel.getAttribute('disabled') !== null).length,
        box: container.querySelector('.style-overrides').getAttribute('data-disabled'),
        advanced: container.querySelector('details.style-advanced').getAttribute('data-disabled') };
};

test('the Video style picker: four families from the vocabulary; a lesson without a style selects none and keeps its original look', async () => {
    assert.deepEqual([C.DEFAULTS.style, C.DEFAULTS.style_version, C.DEFAULTS.style_overrides], [null, null, {}]);
    const p = settingsPanel();
    await p.panel.loadStatus();
    assert.equal(p.container.querySelector('.style-picker'), null); // Classic: no style to choose
    p.panel.set('mode', 'cinematic');
    const picker = p.container.querySelector('.style-picker');
    const options = picker.querySelectorAll('.style-option');
    assert.deepEqual(options.map(o => o.getAttribute('data-style')), ['academic', 'cinematic_education', 'children_education', 'corporate_training']);
    assert.deepEqual(options.map(o => o.getAttribute('data-selected')), ['false', 'false', 'false', 'false'], 'no style: nothing selected');
    assert.ok(options.every(o => o.getAttribute('aria-checked') === 'false'));
    const legacy = p.container.querySelector('.style-note-legacy');
    assert.ok(legacy.classList.contains('style-note'));
    assert.equal(legacy.textContent, 'This lesson keeps its original look. Choose a style to restyle it.');
    assert.equal(p.panel.settings.style, null);
    assert.equal(p.saved().style, null);
    const first = options[0];
    assert.equal(first.querySelector('.style-option-name').textContent, 'Academic');
    assert.equal(first.querySelector('.style-option-desc').textContent, 'Clean and structured');
    const swatch = first.querySelector('.style-swatch');
    assert.equal(swatch.style.getPropertyValue('--st-bg-1'), '#f4efe3'); // the family's colours on its swatch only
    assert.equal(swatch.style.getPropertyValue('--st-bad'), '');
    assert.equal(p.doc.documentElement.style.getPropertyValue('--st-bg-1'), '');
    ['.style-swatch-title', '.style-swatch-body', '.style-swatch-card', '.style-swatch-chip', '.style-swatch-presenter', '.style-swatch-bars']
        .forEach(sel => assert.ok(swatch.querySelector(sel), sel));
    assert.equal(swatch.querySelector('.style-swatch-bars').querySelectorAll('i').length, 3);
    assert.ok(p.container.querySelectorAll('.style-note').some(n => n.textContent === 'Changes the look only — pictures and clips are not made again.'));
    assert.equal(p.container.querySelector('.style-suggest'), null);
    // the style comes first of the scene settings (right after the Scene style row)
    const rows = p.container.firstElementChild.children;
    assert.ok(rows[0].querySelector('#cinematic-mode'));
    assert.ok(rows[1].querySelector('.style-picker'));
    assert.ok(rows.findIndex(r => r.querySelector('#cinematic-background')) > 1);
    // the new transitions are offered
    const transitions = p.container.querySelector('#cinematic-transition');
    assert.deepEqual(transitions.children.map(o => o.value), ['fade', 'soft_fade', 'crossfade', 'slide', 'zoom', 'wipe', 'cut']);
    assert.deepEqual(transitions.children.slice(1, 2).concat(transitions.children.slice(4, 6)).map(o => o.textContent), ['Soft fade', 'Zoom', 'Wipe']);
});

test('without a style the overrides and Advanced are disabled (and ignored) until a family is chosen', async () => {
    const p = settingsPanel();
    await p.panel.loadStatus();
    p.panel.set('mode', 'cinematic');
    assert.deepEqual(overridesDisabled(p.container), { selects: 8, disabled: 8, box: 'true', advanced: 'true' });
    assert.equal(p.container.querySelector('.style-overrides').getAttribute('aria-disabled'), 'true');
    assert.equal(p.container.querySelector('details.style-advanced').getAttribute('open'), null);
    const changes = p.changes.length;
    assert.equal(p.panel.setOverride('accent', 'teal'), false);
    choose(p.container.querySelector('#cinematic-style-accent'), 'teal'); // even if a disabled select fired
    assert.deepEqual(p.panel.settings.style_overrides, {});
    assert.deepEqual([p.changes.length, p.styles.length], [changes, 0]);
    pick(p.container, 'academic').fire('click');
    assert.deepEqual(overridesDisabled(p.container), { selects: 8, disabled: 0, box: null, advanced: null });
    assert.equal(p.container.querySelector('.style-note-legacy'), null);
    assert.equal(p.panel.setOverride('accent', 'teal'), true);
    assert.deepEqual(p.saved().style_overrides, { accent: 'teal' });
    // the lesson's original look again (a saved lesson without a style): disabled, and its overrides gone
    p.panel.applyLessonStyle(null);
    assert.deepEqual(overridesDisabled(p.container), { selects: 8, disabled: 8, box: 'true', advanced: 'true' });
    assert.equal(p.container.querySelector('#cinematic-style-accent').children.find(o => o.selected).value, 'default');
    assert.deepEqual(p.panel.settings.style_overrides, {});
});

test('choosing a family changes only style and version (no pre-fill); the suggestion button applies its preferences in one change', async () => {
    const p = settingsPanel();
    await p.panel.loadStatus();
    p.panel.set('mode', 'cinematic');
    p.panel.set('motion', 'none'); // a deliberate still camera
    let changes = p.changes.length;
    pick(p.container, 'children_education').fire('click');
    assert.deepEqual([p.panel.settings.style, p.panel.settings.style_version, p.panel.settings.transitions, p.panel.settings.motion],
        ['children_education', 1, 'fade', 'none'], 'transitions and motion untouched (they are part of the composition)');
    assert.deepEqual([p.saved().style, p.saved().style_version, p.saved().transitions, p.saved().motion], ['children_education', 1, 'fade', 'none']);
    assert.equal(p.changes.length, changes + 1);
    assert.deepEqual(p.styles, [{ style: 'children_education', style_version: 1, style_overrides: {} }]);
    assert.equal(pick(p.container, 'children_education').getAttribute('data-selected'), 'true');
    assert.equal(pick(p.container, 'cinematic_education').getAttribute('data-selected'), 'false');
    // the style's preferences are offered, not applied
    const suggest = p.container.querySelector('.style-suggest');
    assert.equal(suggest.querySelector('.style-suggest-text').textContent, 'This style suggests Soft fade transitions and a subtly moving camera.');
    changes = p.changes.length;
    suggest.querySelector('button.style-suggest-apply').fire('click');
    assert.deepEqual([p.panel.settings.transitions, p.panel.settings.motion], ['soft_fade', 'subtle']);
    assert.deepEqual([p.saved().transitions, p.saved().motion], ['soft_fade', 'subtle']);
    assert.equal(p.changes.length, changes + 1, 'one change event for both');
    assert.equal(p.styles.length, 1, 'the style itself did not change');
    assert.equal(p.container.querySelector('.style-suggest'), null, 'nothing differs any more');
    assert.equal(p.container.querySelector('#cinematic-transition').children.find(o => o.selected).value, 'soft_fade');
    // the same family again: nothing happens
    changes = p.changes.length;
    assert.equal(p.panel.chooseStyle('children_education'), false);
    pick(p.container, 'children_education').fire('click');
    assert.deepEqual([p.changes.length, p.styles.length], [changes, 1]);
    // a family that prefers a still camera and crossfades: offered, only what differs is named
    pick(p.container, 'corporate_training').fire('click');
    assert.deepEqual([p.panel.settings.style, p.panel.settings.style_version, p.panel.settings.transitions, p.panel.settings.motion],
        ['corporate_training', 2, 'soft_fade', 'subtle']);
    assert.equal(p.container.querySelector('.style-suggest-text').textContent, 'This style suggests Crossfade transitions and a still camera.');
    p.panel.set('transitions', 'crossfade');
    assert.equal(p.container.querySelector('.style-suggest-text').textContent, 'This style suggests a still camera.');
    p.panel.set('motion', 'none');
    assert.equal(p.container.querySelector('.style-suggest'), null);
    assert.equal(p.panel.styleSuggestion(), null);
    assert.equal(p.panel.applySuggestions(), false);
    assert.equal(p.panel.chooseStyle('retro'), false);
    // without the vocabulary (not signed in yet): the four families known here, no preview colours
    const offline = settingsPanel({ looks: null });
    offline.panel.set('mode', 'cinematic');
    const names = offline.container.querySelectorAll('.style-option-name').map(n => n.textContent);
    assert.deepEqual(names, ['Academic', 'Cinematic Education', "Children's Education", 'Corporate Training']);
    assert.deepEqual(Object.keys(offline.container.querySelector('.style-swatch').style._props), []);
    pick(offline.container, 'academic').fire('click');
    assert.deepEqual([offline.panel.settings.style, offline.panel.settings.style_version, offline.panel.settings.transitions], ['academic', 1, 'fade']);
    assert.equal(offline.container.querySelector('.style-suggest'), null); // Academic prefers what the lesson already uses
    pick(offline.container, 'children_education').fire('click');
    assert.equal(offline.container.querySelector('.style-suggest-text').textContent, 'This style suggests Soft fade transitions.');
    assert.deepEqual(C.STYLE_FAMILIES.map(f => f.id), ['academic', 'cinematic_education', 'children_education', 'corporate_training']);
});

test('the style overrides: plain-words selects; "default" is never stored; Advanced holds code, formula and diagram', async () => {
    const p = settingsPanel();
    await p.panel.loadStatus();
    p.panel.set('mode', 'cinematic');
    pick(p.container, 'academic').fire('click');
    const box = p.container.querySelector('.style-overrides');
    assert.deepEqual(box.querySelectorAll('label').slice(0, 5).map(l => l.textContent), ['Accent colour', 'Text size', 'Animation', 'Background', 'Caption size']);
    const accent = p.container.querySelector('#cinematic-style-accent');
    assert.deepEqual(accent.children.map(o => o.value), ['default', 'gold', 'coral', 'crimson', 'teal', 'sky', 'violet', 'emerald']);
    assert.deepEqual(accent.children.slice(0, 3).map(o => o.textContent), ['Style default', 'Gold', 'Coral']);
    assert.equal(accent.children[0].selected, true);
    choose(accent, 'teal');
    assert.deepEqual(p.panel.settings.style_overrides, { accent: 'teal' });
    assert.deepEqual(p.saved().style_overrides, { accent: 'teal' });
    assert.deepEqual(p.styles.at(-1), { style: 'academic', style_version: 1, style_overrides: { accent: 'teal' } });
    choose(p.container.querySelector('#cinematic-style-text-size'), 'larger');
    assert.deepEqual(p.panel.settings.style_overrides, { accent: 'teal', text_size: 'larger' });
    assert.equal(p.container.querySelector('#cinematic-style-text-size').children.find(o => o.selected).textContent, 'Larger');
    choose(p.container.querySelector('#cinematic-style-accent'), 'default');
    assert.deepEqual(p.panel.settings.style_overrides, { text_size: 'larger' });
    assert.equal(p.panel.setOverride('accent', 'neon'), false); // not an option
    assert.equal(p.panel.setOverride('font', 'comic'), false); // not a choice
    assert.deepEqual(p.panel.settings.style_overrides, { text_size: 'larger' });
    choose(p.container.querySelector('#cinematic-style-text-size'), 'default');
    assert.deepEqual(p.saved().style_overrides, {});
    assert.deepEqual(p.styles.at(-1), { style: 'academic', style_version: 1, style_overrides: {} });
    // Advanced: closed until one of its choices is set
    const advanced = p.container.querySelector('details.style-advanced');
    assert.equal(advanced.querySelector('summary').textContent, 'Advanced');
    assert.equal(advanced.getAttribute('open'), null);
    assert.deepEqual(advanced.querySelectorAll('select').map(s => s.getAttribute('data-override')), ['code_size', 'formula_size', 'diagram_frame']);
    assert.deepEqual(advanced.querySelectorAll('label').map(l => l.textContent), ['Code size', 'Formula size', 'Diagram frame']);
    choose(p.container.querySelector('#cinematic-style-code-size'), 'large');
    assert.equal(p.container.querySelector('details.style-advanced').getAttribute('open'), '');
    assert.deepEqual(p.panel.settings.style_overrides, { code_size: 'large' });
    choose(p.container.querySelector('#cinematic-style-diagram-frame'), 'rounded');
    assert.deepEqual(p.saved().style_overrides, { code_size: 'large', diagram_frame: 'rounded' });
    // changing the family keeps the overrides (they refine whichever style is chosen)
    pick(p.container, 'corporate_training').fire('click');
    assert.deepEqual(p.styles.at(-1), { style: 'corporate_training', style_version: 2, style_overrides: { code_size: 'large', diagram_frame: 'rounded' } });
    // what the page offers elsewhere (Visual Review)
    const offer = p.panel.styleOptions();
    assert.equal(offer.default, 'cinematic_education');
    assert.equal(offer.families.length, 4);
    assert.deepEqual(offer.options.accent, LOOKS.options.accent);
    assert.deepEqual(offer.scene, ['accent', 'background']);
    assert.equal(p.panel.looks, LOOKS);
});

test('saved settings are checked: an unknown style, version or override is dropped (the server would refuse it)', () => {
    const store = { 'aadhi.cinematic': JSON.stringify({ mode: 'cinematic', style: 'retro', style_version: 'x',
        style_overrides: { accent: 'neon', motion: 'low', bogus: 'x', text_size: 'default' } }) };
    const storage = { getItem: k => store[k] || null, setItem: () => {} };
    const s = C.loadSettings(storage);
    assert.deepEqual([s.style, s.style_version, s.style_overrides], [null, null, {}], 'no style: no overrides (the original look)');
    store['aadhi.cinematic'] = JSON.stringify({ style: 'academic', style_version: 'x', style_overrides: { accent: 'neon', motion: 'low', bogus: 'x', text_size: 'default' } });
    const k = C.loadSettings(storage);
    assert.deepEqual([k.style, k.style_version, k.style_overrides], ['academic', null, { motion: 'low' }]);
    store['aadhi.cinematic'] = JSON.stringify({ style: 'academic', style_version: 1, style_overrides: { accent: 'teal' } });
    const t = C.loadSettings(storage);
    assert.deepEqual([t.style, t.style_version, t.style_overrides], ['academic', 1, { accent: 'teal' }]);
    assert.notEqual(C.loadSettings(storage).style_overrides, C.loadSettings(storage).style_overrides); // never one shared object
    assert.ok(Object.isFrozen(C.DEFAULTS.style_overrides));
    assert.deepEqual(C.loadSettings({ getItem: () => '{broken' }).style_overrides, {});
});

test('a saved lesson\'s style: applied, checked and kept; nothing pre-filled, no change event', async () => {
    const p = settingsPanel();
    await p.panel.loadStatus();
    p.panel.set('mode', 'cinematic');
    p.panel.set('transitions', 'cut');
    const changes = p.changes.length;
    const clean = p.panel.applyLessonStyle({ style: 'children_education', style_version: 1, style_overrides: { accent: 'teal', bogus: 1, motion: 'fast' } });
    assert.deepEqual(clean, { style: 'children_education', style_version: 1, style_overrides: { accent: 'teal' } });
    assert.deepEqual([p.panel.settings.style, p.panel.settings.style_version, p.panel.settings.style_overrides], ['children_education', 1, { accent: 'teal' }]);
    assert.deepEqual([p.saved().style, p.saved().style_overrides], ['children_education', { accent: 'teal' }]);
    assert.equal(p.panel.settings.transitions, 'cut', 'not pre-filled');
    assert.equal(p.changes.length, changes, 'onChange is not called');
    assert.equal(p.styles.length, 0, 'onStyleChange is not called');
    assert.equal(pick(p.container, 'children_education').getAttribute('data-selected'), 'true');
    assert.equal(p.container.querySelector('#cinematic-style-accent').children.find(o => o.selected).value, 'teal');
    // an old lesson: its original look, nothing selected
    p.panel.applyLessonStyle(null);
    assert.deepEqual([p.panel.settings.style, p.panel.settings.style_version, p.panel.settings.style_overrides], [null, null, {}]);
    assert.equal(p.saved().style, null);
    assert.ok(p.container.querySelectorAll('.style-option').every(o => o.getAttribute('data-selected') === 'false'));
    assert.ok(p.container.querySelector('.style-note-legacy'));
    // anything else is dropped (no style: no overrides either)
    assert.deepEqual(p.panel.applyLessonStyle({ style: 'retro', style_version: 3, style_overrides: { accent: 'teal' } }),
        { style: null, style_version: null, style_overrides: {} });
    assert.deepEqual(p.panel.applyLessonStyle({ style: 'academic', style_version: -1, style_overrides: [] }),
        { style: 'academic', style_version: null, style_overrides: {} });
    assert.deepEqual(p.panel.applyLessonStyle({ style: 'academic', style_version: 1.5, style_overrides: 'x' }).style_version, null);
    assert.equal(p.changes.length, changes);
    assert.equal(p.styles.length, 0);
});

test('an AI background is asked for with the lesson\'s style family (a legacy lesson keeps its typography\'s words)', async () => {
    const p = settingsPanel();
    await p.panel.generateBackground(false);
    assert.equal(p.calls[0].style, undefined);
    assert.equal(p.calls[0].typography, 'academic');
    p.panel.applyLessonStyle({ style: 'academic', style_version: 1 });
    await p.panel.generateBackground(false);
    assert.equal(p.calls[1].style, 'academic');
});

// ---- Visual Review: the scene's style in words ----------------------------------------------------------------------

test('styleSummary: the look\'s family, version, choices, adjustments and hex swatch (null without a look)', () => {
    const a = C.styleSummary(scene(), withLook(academic()));
    assert.deepEqual(a, { id: 'academic@1', label: 'Academic', family: 'academic', version: 1, tone: 'light', fingerprint: '424cb5b481bbeca2',
        overrides: [], sceneOverrides: [], adjustments: [], swatch: ['#f4efe3', '#ffffff', '#1c2638', '#1d3d6e', '#9b2335'], legacy: false });
    const c = C.styleSummary(scene(), withLook(corporate()));
    assert.deepEqual(c.overrides, [['accent', 'teal']]);
    assert.deepEqual(c.sceneOverrides, [['background', 'plain']]);
    assert.deepEqual(c.adjustments, ['dates made darker or lighter to stay readable']);
    assert.equal(c.tone, 'dark');
    // translucent tokens are shown as seen: the surface over the background, as #rrggbb
    assert.deepEqual(c.swatch, ['#0b1224', '#121c31', '#e5e9f0', '#7fd4ff', '#38bdf8']);
    assert.ok(c.swatch.every(h => /^#[0-9a-f]{6}$/.test(h)));
    // the legacy look: a hidden variant (Phase 13's "modern"), or settings without a style when they are given
    assert.equal(C.styleSummary(scene(), withLook(corporate({}, {}))).legacy, false);
    assert.equal(C.styleSummary(scene(), withLook({ ...academic(), variant: 'modern' })).legacy, true);
    assert.equal(C.styleSummary(scene(), withLook(academic()), { style: null }).legacy, true);
    assert.equal(C.styleSummary(scene(), withLook(academic()), { style: 'academic' }).legacy, false);
    // an unreadable colour is left out; unsafe ones are never read
    const odd = C.styleSummary(scene(), withLook(academic({ css: { '--st-bg-1': 'url(x)', '--st-text': 'red', '--st-accent': '#123' } })));
    assert.deepEqual(odd.swatch, ['#112233']);
    assert.equal(C.styleSummary(scene(), planB()), null);
    assert.equal(C.styleSummary(scene(), null), null);
});
