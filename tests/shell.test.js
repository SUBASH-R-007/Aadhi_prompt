// Phase 21: the app shell (index.html, app.js, product.css), checked in its source and, for its small helpers, by running them:
// the top bar (Home · Create · Library · Videos · Settings) with accessible names, Home reachable once a lesson is open (the
// lesson stops cleanly), the Settings dialog holding the start screen's controls (moved, not copied), the classic generation's
// error that closes its overlay, one plain placeholder card on the stage (technical details only with ?visualDebug and never
// while recording, everything escaped), the player bar's names and More menu, focus and reduced motion in the product CSS,
// and the security fixes (no service key in the page, a sandboxed p5 frame, sign-out clears the person's data).
// Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const page = read('index.html');
const shell = read('app.js');
const css = read('product.css');
const between = (text, start, end) => {
    const a = text.indexOf(start);
    assert.ok(a >= 0, `found: ${start}`);
    const b = text.indexOf(end, a + start.length);
    assert.ok(b > a, `found after it: ${end}`);
    return text.slice(a, b);
};
const markup = page.slice(page.indexOf('<body>'));
const startScreen = between(page, '<main class="glass-panel start-screen', '</main>');
const settings = between(page, '<div class="settings-overlay" id="settings-overlay" hidden>', '<!-- Plain notices');
const bar = between(page, '<div class="voice-control-bar" id="voice-control-bar"', '<!-- The lesson file editor');
const withoutComments = text => text.replace(/\/\*[\s\S]*?\*\//g, '');

test('the product name, ui.css first, then the page, studio, editor, caption fit and product styles', () => {
    assert.match(page, /<title>Aadhi — Educational Video Studio<\/title>/);
    const ui = page.indexOf('<link rel="stylesheet" href="ui.css">');
    assert.ok(ui > 0 && ui < page.indexOf('<style'), 'ui.css before the page\'s own styles');
    assert.match(page, /<link rel="stylesheet" href="studio\.css">\n {4}<link rel="stylesheet" href="editor\.css">\n {4}<link rel="stylesheet" href="stage-fit\.css">\n {4}<link rel="stylesheet" href="product\.css">\n<\/head>/);
    assert.match(page, /<script src="app\.js" defer><\/script>/);
    assert.doesNotMatch(markup, /Aadhi Generator|Academic Lecture Overlay|View History|Restore\s+Previous|Play Demo/);
});

test('the top bar: Home · Create · Library · Videos · Settings, each with an accessible name, outside the start screen', () => {
    const nav = between(page, '<nav class="app-nav" aria-label="Main">', '</nav>');
    const names = [...nav.matchAll(/<button type="button" class="app-nav-btn" id="([a-z-]+)" data-nav="([a-z]+)" title="[^"]+" aria-label="([^"]+)"/g)]
        .map(m => [m[1], m[2], m[3]]);
    assert.deepEqual(names, [['nav-home', 'home', 'Home'], ['nav-create', 'create', 'Create'], ['open-assets-btn', 'library', 'Library'],
        ['open-videos-btn', 'videos', 'Videos'], ['nav-settings', 'settings', 'Settings']]);
    assert.ok(markup.indexOf('id="app-bar"') < markup.indexOf('id="upload-screen"'), 'the bar is not part of the start screen');
    assert.doesNotMatch(startScreen, /id="app-bar"|class="app-nav"/);
    // each entry's work is the page's own: the Studio's Home / create view, the Asset Library, the export panel, Settings
    assert.match(shell, /\$\('nav-home'\)\.addEventListener\('click', \(\) => \{ closeAll\(\); const home = pageFn\('goHome'\); if \(home\) home\(\); \}\);/);
    assert.match(shell, /\$\('nav-create'\)\.addEventListener\('click', \(\) => \{ closeAll\(\); const create = pageFn\('openStudioHome'\); if \(create\) create\('create'\); \}\);/);
    assert.match(shell, /\$\('open-assets-btn'\)\.addEventListener\('click', \(\) => \{ closeAll\(\); if \(typeof assetLibrary !== 'undefined'\) assetLibrary\.open\(\); \}\);/);
    assert.match(shell, /\$\('open-videos-btn'\)\.addEventListener\('click', \(\) => \{ closeAll\(\); if \(typeof exportFlow !== 'undefined'\) exportFlow\.open\(\); \}\);/);
    assert.match(shell, /\$\('nav-settings'\)\.addEventListener\('click', \(\) => \(settings\.hidden \? openSettings\(\) : closeSettings\(\)\)\);/);
    // hidden while a video is recorded; it steps aside while the lesson plays
    assert.match(css, /body\[data-recording\] \.app-bar,\s*body\[data-mode="exporting"\] \.app-bar,/);
    assert.match(css, /body\.presentation-active\.lesson-playing \.app-bar:not\(:hover\):not\(:focus-within\) \{\s*transform: translateY\(-100%\);/);
});

test('Home is reachable once a lesson is open: the lesson stops cleanly, is closed, and the start screen comes back', () => {
    const leave = between(page, 'function leaveLesson() {', 'window.leaveLesson = leaveLesson;');
    assert.match(leave, /if \(isAutoExporting\) return;/, 'never while a video is recorded');
    assert.match(leave, /stageSession \+= 1;\s*resetStage\(\);/, 'the intro, the narration and the editor stop; a lesson start on its way is dropped');
    assert.match(leave, /cinematicStage\.clear\(\)/, 'the classic page again (no scene layout on the start screen)');
    assert.match(leave, /startOverlay\.style\.display = 'none';\s*uploadScreen\.classList\.remove\('hidden'\);\s*currentProjectId = null;/);
    assert.match(leave, /params\.delete\('project_id'\);/, 'a refresh does not reopen it');
    assert.match(page, /const session = stageSession;\s*Promise\.all\(\[mascot\.whenReady\(\)[^\n]*\n\s*if \(session !== stageSession\) return;/);
    assert.match(page, /function goHome\(\) \{\s*leaveLesson\(\);\s*openStudioHome\('lessons'\);\s*\}/);
    assert.match(page, /if \(typeof studioPanel\.showHome === 'function'\) \{\s*if \(view === 'home'\) studioPanel\.showHome\(\); else studioPanel\.showCreate\(\);/);
    // the start card of an opened lesson: Open in Studio (primary), Play, its videos and visuals, Back to Home
    const card = between(page, '<div id="start-overlay"', '<!-- Intro Sequence Container -->');
    assert.match(card, /<button id="start-studio-btn" class="neon-btn neon-btn-gold" type="button"[\s\S]*?>Open in Studio<\/button>/);
    assert.match(card, /id="start-lecture-btn"[\s\S]*?id="start-videos-btn"[\s\S]*?id="start-review-btn"[\s\S]*?id="start-home-btn"/);
    assert.match(page, /document\.getElementById\('start-studio-btn'\)\.addEventListener\('click', openStudio\);\s*document\.getElementById\('start-home-btn'\)\.addEventListener\('click', goHome\);/);
    // the start screen: one primary "Create a lesson", "Open your lessons"
    assert.match(startScreen, /<button id="start-create-btn" class="neon-btn neon-btn-gold animate-entrance-2" type="button"[\s\S]*?>Create a lesson<\/button>/);
    assert.match(startScreen, /<button id="open-studio-btn" class="neon-btn neon-btn-outline animate-entrance-2" type="button"[\s\S]*?>Open your lessons<\/button>/);
    assert.match(page, /document\.getElementById\('start-create-btn'\)\.addEventListener\('click', \(\) => openStudioHome\('create'\)\);/);
});

test('Settings holds the start screen\'s controls, moved (not copied), before the page script that wires them', () => {
    const ids = ['logout-btn', 'admin-btn', 'presenter-settings', 'cinematic-settings', 'ai-visuals-select', 'ai-providers-panel', 'tts-engine-select',
        'voice-select', 'gemini-voice-select', 'provider-select', 'model-select', 'classic-generation', 'hardware-select', 'start-server-btn',
        'download-master-prompt-btn', 'download-json-btn', 'export-html-btn', 'edit-script-btn', 'info-btn'];
    ids.forEach(id => {
        assert.ok(settings.includes(`id="${id}"`), `${id} is in Settings`);
        assert.equal((page.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1, `${id} exists once`);
        assert.ok(!startScreen.includes(`id="${id}"`), `${id} is no longer on the start screen`);
    });
    assert.ok(page.indexOf('id="settings-overlay"') < page.indexOf('// --- VERCEL / RAILWAY DEPLOYMENT CONFIG ---'), 'the panels find their containers');
    assert.match(settings, /<section class="settings-panel" id="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">/);
    assert.match(settings, /id="settings-close" aria-label="Close settings"/);
    ['general', 'lesson', 'visuals', 'voice', 'advanced'].forEach(key => assert.match(settings, new RegExp(`<section class="settings-section" id="settings-${key}" data-section="${key}"`)));
    // plain labels: no raw model ids, engine names or server profiles shown
    assert.doesNotMatch(settings, />gemini-[0-9]|Default \(Edge-TTS\)|AI Server\s+Profile|Option [1-4]:|Download Master Prompt/);
    assert.doesNotMatch(page, /<option value="[^"]+"[^>]*>(gemini-|gpt-4o)/);
    // the developer tools need a lesson; the raw editor and the scene list only in the debug view
    assert.match(settings, /id="edit-script-btn" class="ui-btn" data-needs-lesson data-debug-only/);
    assert.match(settings, /id="info-btn" class="ui-btn" data-needs-lesson data-debug-only/);
    assert.match(shell, /settingsDialog\.querySelectorAll\('\[data-debug-only\]'\)\.forEach\(b => \{ b\.hidden = !debug\(\); \}\);/);
    // the Studio still moves the presenter and style panels in and back (from Settings now)
    assert.match(page, /const node = document\.getElementById\(kind === 'presenter' \? 'presenter-settings' : 'cinematic-settings'\);/);
    // Esc closes; focus comes back where it was
    assert.match(shell, /if \(top === settings\) \{ e\.preventDefault\(\); closeSettings\(\); \}/);
    assert.match(shell, /if \(back && doc\.contains\(back\) && typeof back\.focus === 'function' && !back\.closest\('\[inert\]'\)\) back\.focus\(\);/);
});

test('the page behind an open panel is inert (keyboard focus stays in the Studio, the editor, a panel or a dialog)', () => {
    assert.match(shell, /const OVERLAYS = '\.studio-root, \.editor-root, \.asset-overlay\.open, \.export-overlay\.open:not\(\.recording-hidden\), '/);
    assert.match(shell, /const want = !!top && el !== top && !el\.contains\(top\);/);
    assert.match(shell, /el\.setAttribute\('inert', ''\);\s*el\.dataset\.shellInert = '1';/);
    assert.match(shell, /else if \(!want && el\.dataset\.shellInert === '1'\) \{\s*el\.removeAttribute\('inert'\);/, 'only its own inert is removed');
    assert.match(shell, /const top = recording\(\) \? null : topOverlay\(\);/, 'nothing changes while a video is recorded');
});

test('a failed one-step generation explains itself in plain words, and Dismiss closes the overlay', () => {
    const run = between(page, 'async function runGeneration() {', "generateBtn.addEventListener('click'");
    const failure = between(run, '} catch (err) {', '} finally {');
    assert.doesNotMatch(failure, /innerHTML/, 'built from text, never markup');
    assert.match(failure, /dismiss\.className = 'neon-btn neon-btn-outline retry-btn';/);
    assert.match(failure, /const closeOverlay = \(\) => \{\s*errDiv\.remove\(\);\s*loadingOverlay\.classList\.remove\('active'\);/);
    assert.match(failure, /dismiss\.addEventListener\('click', closeOverlay\);/);
    assert.match(failure, /retry\.addEventListener\('click', \(\) => \{ closeOverlay\(\); runGeneration\(\); \}\);/);
    assert.match(failure, /if \(visualDebug && err && err\.message\) \{\s*const raw = document\.createElement\('pre'\);[\s\S]{0,120}raw\.textContent = /);
    assert.match(failure, /errDiv\.className = 'error-message';/, 'the check\'s hook');
    // the steps in plain words: no service names, parser names, token counts or raw server answers
    const said = run.split('\n').filter(line => !/^\s*\/\//.test(line)).join('\n'); // (what it says, not its comments)
    assert.doesNotMatch(said, /Sending to Gemini|Communicating with|PDF\.js parser|Mammoth\.js|65,536 tokens|API returned|Bypassing AI generation/);
    assert.match(run, /logStatus\('Writing your lesson…', '2\. Writing the lesson \(this can take a few minutes\)'\);/);
});

test('the Document Assistant hands its source to the Studio; the one-step path only when chosen (Settings → Advanced)', () => {
    assert.match(page, /if \(typeof studioSourceWanted === 'function'\) \{ studioSourceWanted\(prepared\); return; \}[^\n]*\n\s*if \(typeof AadhiStudio !== 'undefined' && !classicGeneration\(\)\) \{ studioWriteFrom\(prepared\); return; \}\s*window\.preparedSource = prepared;\s*runGeneration\(\);/);
    assert.match(page, /function classicGeneration\(\) \{\s*try \{ return localStorage\.getItem\(CLASSIC_KEY\) === '1'; \}/);
    assert.match(page, /if \(studioPreparedSource\) \{ const ready = studioPreparedSource; studioPreparedSource = null; return ready; \}/);
    assert.match(page, /studioPanel\.fromDocument\(\);/);
    assert.match(shell, /if \(classic\.checked\) localStorage\.setItem\(CLASSIC_KEY, '1'\);/);
});

test('no developer text on the stage: one plain placeholder card, escaped, its details only in the debug view and never recording', () => {
    ['[System Debug]', 'Raw Slide Object', 'AI Video Error', 'Compiling Manim', 'Connecting to build server', 'You selected <b>Option 3',
        "The AI failed to generate", 'Manual AI Generation Required', 'Generating AI Video (approx', 'Searching Giphy'].forEach(text =>
        assert.ok(!page.includes(text), `"${text}" is gone`));
    assert.doesNotMatch(page, /innerHTML = `[^`]*\$\{err\.message\}/, 'no raw error message in markup');
    assert.doesNotMatch(page, /\$\{data\.prompt\}<\/div>/);
    assert.match(page, /function stageShowsDebug\(\) \{\s*return new URLSearchParams\(window\.location\.search\)\.has\('visualDebug'\) && !isRecordingVideo\(\);\s*\}/);
    assert.match(page, /function isRecordingVideo\(\) \{\s*return document\.body\.hasAttribute\('data-recording'\) \|\| \(typeof isAutoExporting !== 'undefined' && !!isAutoExporting\);/);
    assert.match(page, /if \(event\.data\.startsWith\('MANIM_LOG:'\) && stageShowsDebug\(\)\) \{/, 'the renderer\'s log lines too');
    // the actions the browser checks use are kept (Generate AI video, the generation's status)
    assert.match(page, /visualNotice\('This scene needs an AI video', [^\n]*'Generate AI video', \[\['Prompt', prompt\]\]\);\s*jxgbox\.querySelector\('\.visual-notice-action'\)\.addEventListener\('click', \(\) => generateAiVideo\(\)\);/);
    assert.match(page, /className = 'visual-generation-status';/);
    assert.match(page, /<span class="ai-run-note"/);
});

test('the placeholder card, run: escaped; details only with ?visualDebug, never while recording', () => {
    const source = between(page, '        // Phase 21: is a video being recorded?', '        // A plain notice from the page');
    const make = ({ search = '', recording = false, exporting = false } = {}) => new Function('window', 'document', 'isAutoExporting', 'URLSearchParams',
        `${source}; return { visualNotice, plainServerText, stageShowsDebug };`)(
        { location: { search } }, { body: { hasAttribute: name => name === 'data-recording' && recording } }, exporting, URLSearchParams);
    const plainView = make();
    const card = plainView.visualNotice('This <b>visual</b> isn\'t ready yet', 'Reason & "words"', 'Try <i>again</i>', [['Scene code', 'os.system("<b>x</b>")']]);
    assert.ok(card.includes('This &lt;b&gt;visual&lt;/b&gt; isn&#39;t ready yet') && card.includes('Reason &amp; &quot;words&quot;'));
    assert.ok(card.includes('class="btn-gold visual-notice-action">Try &lt;i&gt;again&lt;/i&gt;</button>'));
    assert.ok(!card.includes('os.system') && !card.includes('visual-notice-debug'), 'no details for a teacher');
    const debugCard = make({ search: '?project_id=3&visualDebug=1' }).visualNotice('T', 'R', '', [['Message', 'boom'], ['Scene code', '<b>x</b>'], ['Empty', '']]);
    assert.ok(debugCard.includes('<div class="visual-notice-debug"><b>Message</b><pre>boom</pre><b>Scene code</b><pre>&lt;b&gt;x&lt;/b&gt;</pre></div>'));
    assert.ok(!debugCard.includes('Empty'));
    assert.ok(!make({ search: '?visualDebug', recording: true }).visualNotice('T', 'R', '', [['Message', 'boom']]).includes('boom'), 'never recorded');
    assert.ok(!make({ search: '?visualDebug', exporting: true }).visualNotice('T', 'R', '', [['Message', 'boom']]).includes('boom'), 'never exported');
    // a server's words pass when plain; tracebacks, paths, code, status lines and keys do not
    assert.equal(plainView.plainServerText('This animation requested an operation that is not allowed in the Manim sandbox.'),
        'This animation requested an operation that is not allowed in the Manim sandbox.');
    assert.equal(plainView.plainServerText('No lesson writer is set up'), 'No lesson writer is set up.');
    ['Traceback (most recent call last): File "server.py", line 3', 'error 500', 'The server answered 500: boom', 'C:\\Users\\x\\file.py failed',
        'GEMINI_API_KEY is not set', '<b>x</b>', 'line one\nline two', 'x'.repeat(300)].forEach(raw => assert.equal(plainView.plainServerText(raw), '', raw.slice(0, 40)));
});

test('the player bar: every icon button has a name and a title; More holds the rest (keyboard: arrows, Esc)', () => {
    const buttons = [...bar.matchAll(/<button type="button" id="([a-z-]+)" class="control-btn"([^>]*)>/g)];
    assert.ok(buttons.length >= 8, `${buttons.length} buttons`);
    buttons.forEach(([, id, attrs]) => {
        assert.match(attrs, /title="[^"]{4,}"/, `${id} has a title`);
        assert.match(attrs, /aria-label="[^"]{3,}"/, `${id} has an accessible name`);
    });
    ['tts-play-btn', 'tts-stop-btn', 'review-btn', 'editor-btn', 'studio-btn', 'auto-export-btn', 'tts-mute-btn', 'player-more-btn']
        .forEach(id => assert.ok(buttons.some(b => b[1] === id), `${id} is on the bar`));
    assert.match(bar, /<label class="control-speed" for="tts-rate-slider"[^>]*>[^<]*<span aria-hidden="true">⧗<\/span> Speed<\/label>/);
    assert.match(bar, /id="player-more-btn"[^>]*aria-haspopup="menu" aria-expanded="false" aria-controls="player-more-menu"/);
    assert.match(bar, /<div class="control-menu" id="player-more-menu" role="menu" aria-label="More actions" hidden>/);
    ['download-companion-btn', 'reload-slide-btn', 'record-btn', 'player-dev-btn'].forEach(id =>
        assert.match(bar, new RegExp(`<button type="button" role="menuitem" id="${id}"`)));
    assert.match(shell, /else if \(e\.key === 'Escape'\) \{ e\.preventDefault\(\); e\.stopPropagation\(\); closeMenu\(true\); \}/);
    assert.match(shell, /else if \(e\.key === 'Escape' && !menu\.hidden\) \{ e\.preventDefault\(\); closeMenu\(true\); \}/);
    // the play / mute buttons say their state in words, to everyone
    assert.match(page, /ttsPlayBtn\.setAttribute\('aria-label', 'Pause'\);/);
    assert.match(page, /ttsMuteBtn\.setAttribute\('aria-label', 'Unmute'\);/);
    // legible on light lesson styles (an opaque bar), visible while paused and, while the lesson plays, on activity (also on
    // touch screens: a tap) — not whenever the pointer is on the page; its progress line at the bottom edge
    assert.match(css, /\.voice-control-bar \{\s*height: auto;[\s\S]{0,200}background: var\(--ui-surface\);/);
    assert.match(css, /body\.presentation-active:not\(\.lesson-playing\) \.voice-control-bar \{\s*transform: translateY\(0\);\s*opacity: 1;/);
    assert.match(css, /body\.presentation-active\.player-active \.voice-control-bar \{\s*transform: translateY\(0\);\s*opacity: 1;/);
    // kept under the pointer, with keyboard focus in it (not a mouse click's focus: browser check finding) and while More is open
    assert.match(css, /body\.presentation-active\.lesson-playing:not\(\.player-active\) \.voice-control-bar:not\(:hover\):not\(:has\(:focus-visible\)\):not\(:has\(\.control-menu:not\(\[hidden\]\)\)\) \{\s*transform: translateY\(100%\);\s*opacity: 0;/);
    assert.doesNotMatch(withoutComments(css), /\.voice-control-bar:not\(:hover\):not\(:focus-within\)/);
    assert.doesNotMatch(withoutComments(css), /@media \(hover: none\)/, 'a tap shows it on touch screens');
    assert.match(css, /\.voice-control-bar \.progress-bar-container \{\s*top: auto;\s*bottom: 0;/);
});

test('product.css: only the ui tokens (never the lesson style), focus always visible, reduced motion for the product UI only', () => {
    const plain = withoutComments(css);
    assert.doesNotMatch(plain, /var\(--(st|cine)-|var\(--text-gold\)|var\(--font-/, 'never the lesson style or the stage\'s tokens');
    assert.doesNotMatch(plain, /#presentation-board|\.cinematic-scene-container|\.board-body|\.cine-|data-cine|#subtitle-track|\.presenter-layer|\.side-panel-view|intro-/,
        'nothing the video records');
    assert.match(plain, /\.neon-btn:focus-visible,\s*\.btn-gold:focus-visible,\s*\.btn-outline:focus-visible,\s*\.control-btn:focus-visible,/);
    assert.match(plain, /#tts-rate-slider:focus-visible,/);
    assert.match(plain, /\.export-panel :is\(button, select, input, a, video\):focus-visible,/);
    assert.match(plain, /\.asset-item:focus-visible,/);
    assert.match(plain, /outline: var\(--ui-focus-width\) solid var\(--ui-focus\);/);
    const motion = plain.slice(plain.indexOf('@media (prefers-reduced-motion: reduce)'));
    assert.ok(motion.length > 100, 'a reduced-motion block');
    ['.ambient-particle', '.animate-entrance-1', '.drop-zone-icon', '.btn-gold::after', '.enhanced-progress::after', '.export-step-bar.indeterminate .export-step-fill',
        '.glass-overlay .glass-panel', '.neon-btn:hover:not(:disabled)', '.app-bar'].forEach(sel => assert.ok(motion.includes(sel), sel));
    // the particles are part of the recorded stage: always made, hidden under reduced motion only while no export records
    // (review finding: skipping them dropped them from a reduced-motion user's exported video)
    assert.doesNotMatch(page, /prefers-reduced-motion: reduce\)'\)\.matches\) return; \/\/ \(Phase 21\)/);
    assert.match(motion, /body:not\(\[data-recording\]\) \.ambient-particle \{\s*display: none !important;/);
    assert.doesNotMatch(shell, /mousemove/, 'no cursor-following buttons');
    // no all-caps buttons or headings (tiny labels excepted)
    assert.match(plain, /\.neon-btn,\s*\.btn-gold,\s*\.config-label,\s*\.slider-label,\s*\.revamp-subtitle \{\s*text-transform: none;/);
    // the start screen stacks on narrow screens; the player bar wraps
    assert.match(plain, /@media \(max-width: 640px\) \{\s*#upload-screen\.start-screen/);
    assert.match(plain, /@media \(max-width: 1100px\) \{\s*\.voice-control-bar \{ flex-wrap: wrap;/);
});

test('Visual Review\'s layout preview has neutral colours (not the last played scene\'s style)', () => {
    const frame = between(page, '.review-composition-frame { position: relative;', '.review-composition-frame[data-background="studio"]');
    assert.doesNotMatch(frame, /--st-/);
    assert.match(frame, /linear-gradient\(155deg, var\(--ui-surface-2\), var\(--ui-surface-3\)\)/);
    assert.doesNotMatch(page, /body\[data-cine-tone="light"\] \.review-composition-frame/);
});

test('security: no service key in the page, the p5 scene in a sandboxed frame, documents read by libraries loaded on demand', () => {
    assert.doesNotMatch(page, /api_key=|api\.giphy\.com/);
    assert.match(page, /fetch\(`\/get-gif\?query=\$\{encodeURIComponent\(query\)\}&randomize=true`\)/);
    const p5 = between(page, "} else if (slide.type === 'p5_simulation') {", "} else if (slide.type === 'visual' || slide.type === 'simulation') {");
    assert.match(p5, /iframe\.setAttribute\('sandbox', 'allow-scripts'\);\s*iframe\.srcdoc = p5Html;/);
    assert.doesNotMatch(p5, /allow-same-origin|createObjectURL|app\.js/);
    assert.doesNotMatch(page.slice(0, page.indexOf('<style')), /pdf\.min\.js|mammoth\.browser|p5\.min\.js/, 'not loaded with the page');
    // only the reader a file needs (review finding: a text file waited for both readers, so it needed the CDN)
    assert.match(page, /extract: async \(file, progress\) => \{\s*await loadDocumentLibraries\(AadhiSources\.fileKind\(file\)\);/);
    assert.match(page, /await loadDocumentLibraries\('pdf'\);/);
    assert.match(page, /await loadDocumentLibraries\('docx'\);/);
    assert.match(page, /const wanted = kind === 'pdf' \? \['pdf'\] : kind === 'docx' \? \['docx'\] : kind \? \[\] : \['pdf', 'docx'\];/);
});

test('security: signing out (or another person signing in) removes the person\'s data; no lesson stays in the page', () => {
    const auth = between(page, "const USER_KEY = 'aadhi.user';", 'async function loadInitialProject()');
    assert.match(auth, /\['jwt_token', 'saved_slides_backup', 'aadhi\.studio\.run', 'aadhi\.studio\.previewed', USER_KEY\]\.forEach\(k => localStorage\.removeItem\(k\)\);/);
    assert.match(auth, /Object\.keys\(localStorage\)\.filter\(k => k\.startsWith\('aadhi\.editor\.draft\.'\)\)\.forEach\(k => localStorage\.removeItem\(k\)\);/);
    assert.match(auth, /function logout\(\) \{\s*clearUserState\(\);\s*window\.location\.replace\(window\.location\.pathname\);/);
    assert.match(auth, /if \(previous && previous !== username\) \{[\s\S]{0,400}clearUserState\(\);[\s\S]{0,300}if \(pageHeldTheirs\) \{ window\.location\.replace\(window\.location\.pathname\); return; \}/);
    // the old browser copy of the lesson (signed media links included) is no longer written, nor restored at start-up
    assert.doesNotMatch(page, /localStorage\.setItem\('saved_slides_backup'/);
    assert.doesNotMatch(page, /getElementById\('setup-screen'\)|getElementById\('restore-btn'\)/);
    assert.match(page, /function saveProjectBackup\(\) \{\s*try \{\s*localStorage\.removeItem\('saved_slides_backup'\);/);
    // the Studio keeps its per-person memory under the person signed in
    assert.match(page, /userKey: \(\) => \(typeof signedInUser !== 'undefined' && signedInUser \? signedInUser : null\),/);
});

test('sign in: a form with labels (Enter submits), plain words; a lesson link that cannot open says so, with the way Home', () => {
    assert.match(page, /<form class="auth-box" id="auth-form" novalidate>/);
    assert.match(page, /<label class="auth-label" for="auth-username">Username<\/label>\s*<input type="text" id="auth-username" class="auth-input" autocomplete="username" required \/>/);
    assert.match(page, /<label class="auth-label" for="auth-password">Password<\/label>\s*<input type="password" id="auth-password"/);
    assert.match(page, /<button class="auth-btn" type="submit">Sign in<\/button>/);
    assert.match(page, /document\.getElementById\('auth-form'\)\.addEventListener\('submit', e => \{ e\.preventDefault\(\); submitAuth\(\); \}\);/);
    assert.match(page, /<form class="auth-box" id="admin-form" novalidate>/);
    assert.doesNotMatch(page, /errDiv\.innerText = err\.message/);
    const load = between(page, 'async function loadInitialProject() {', '</script>');
    assert.match(load, /action: \{ label: 'Go to Home', run: \(\) => \{ if \(typeof goHome === 'function'\) goHome\(\); \} \}/);
    assert.match(load, /'This lesson could not be found\. It may have been deleted, or it belongs to another account\.'/);
});

test('plain words instead of raw alerts; Visual Review and the editor say they are opening', () => {
    assert.deepEqual(page.match(/alert\(/g), ['alert('], 'only the fallback of a page without the shell');
    assert.match(page, /const busy = shellNotice\('Opening Visual Review…', \{ kind: 'busy' \}\);/);
    assert.match(page, /shellNotice\('This lesson has no scenes yet, so there is nothing to review\.'\);/);
    assert.match(page, /const busy = shellNotice\('Opening the editor…', \{ kind: 'busy' \}\);/);
    assert.doesNotMatch(between(page, 'async function openVisualReview() {', "document.getElementById('review-btn')"), /before exporting|err\.message \|\|/);
    assert.match(shell, /item\.setAttribute\('role', kind === 'error' \? 'alert' : 'status'\);/);
    assert.match(shell, /if \(detail && debug\(\)\) \{/, 'a raw detail only in the debug view');
    assert.match(page, /const said = visualDebug \? raw : plainServerText\(raw\);/, 'the Studio never shows a traceback');
});

test('a lesson\'s name is shown once (empty and repeated parts dropped for display)', () => {
    const source = between(page, 'function lessonDisplayName(parts) {', '// Phase 21: Home.');
    const name = new Function(`${source}; return lessonDisplayName;`)();
    assert.equal(name(['Physics', '', 'physics ']), 'Physics');
    assert.equal(name(['Biology', 'Session 1', 'How plants make food']), 'Biology — Session 1 — How plants make food');
    assert.equal(name([null, undefined, '']), '');
    assert.match(page, /return lessonDisplayName\(\[currentSubjectName, currentSessionNumber, currentSessionTitle\]\)/);
    assert.match(page, /startOverlay\.querySelector\('h1'\)\.textContent = lessonDisplayName\(\[currentSubjectName, currentSessionTitle\]\) \|\| 'Your lesson';/);
});

test('agent S\'s panels are wired: the caption band, the debug flag of the three panels and of the inspector\'s summaries', () => {
    assert.match(page, /if \(typeof AadhiCinematic\.watchCaptions === 'function'\) AadhiCinematic\.watchCaptions\(document\);/);
    assert.equal((page.match(/debug: \(\) => new URLSearchParams\(location\.search\)\.has\('visualDebug'\)/g) || []).length, 3, 'presenter, style, document assistant');
    assert.match(page, /facts: plan => AadhiPresenter\.reviewFacts\(plan, presenterSettings\.profile\(plan\.presenter_id\), \{ debug: visualDebug \}\)/);
    assert.match(page, /summary: plan => AadhiCinematic\.compositionSummary\(plan, \{ debug: visualDebug \}\),/);
    assert.match(page, /direction: \(scene, plan\) => AadhiCinematic\.directionSummary\(scene, plan, \{ debug: visualDebug \}\),/);
    assert.match(page, /AadhiVisuals\.runStatusText\(r, \{ debug: visualDebug \}\)/);
});

test('"See an example lesson" always plays the built-in example, never a lesson opened before', () => {
    assert.match(page, /const EXAMPLE_LESSON = JSON\.parse\(JSON\.stringify\(slides\)\);/);
    const demo = between(page, "document.getElementById('demo-btn').addEventListener('click', () => {", 'showStartOverlay();');
    assert.match(demo, /slides\.splice\(0, slides\.length, \.\.\.JSON\.parse\(JSON\.stringify\(EXAMPLE_LESSON\)\)\);/);
    assert.match(demo, /currentProjectId = null;/);
    assert.match(startScreen, /New here\? <button type="button" id="demo-btn" class="start-link">See an example lesson<\/button>/);
});

test('focus is put right once inert is lifted (review finding: panels gave focus back to inert elements, so it fell to <body>)', () => {
    assert.match(shell, /doc\.addEventListener\('focusin', e => \{/);
    assert.match(shell, /if \(root\) lastFocusIn\.set\(root, t\);\s*else if \(t !== doc\.body\) lastFocusOutside = t;/);
    // a panel opened or back on top whose own focus() met inert: focus goes in (to where it was in that panel, else its heading)
    assert.match(shell, /if \(!active \|\| active === doc\.body \|\| !top\.contains\(active\)\) focusPanel\(top\);/);
    // every panel closed: focus returns to where it was on the page
    assert.match(shell, /&& focusable\(lastFocusOutside\)\) lastFocusOutside\.focus\(\);/);
    // the classic generation's overlay is a panel while active (the page behind it, top bar included, is inert); the
    // Studio and the editor do not take it for a panel above them
    assert.match(shell, /#settings-overlay:not\(\[hidden\]\), #loading-overlay\.active'/);
    assert.match(shell, /watch\(\$\('loading-overlay'\)\);/);
    for (const name of ['studio.js', 'editor_ui.js']) assert.match(read(name), /\.glass-overlay\.active:not\(#loading-overlay\)/, name);
    // a dialog above a panel (sign in) makes it inert: its keys (Tab trap, Esc) are not its own
    for (const name of ['studio.js', 'editor_ui.js', 'review.js', 'assets.js', 'export.js']) {
        assert.match(read(name), /closest\('\[inert\]'\)\) return;/, name);
    }
    // mascot.js's "Enable playback" button stays usable above any panel
    assert.match(shell, /el\.classList\.contains\('playback-gate'\)\) return;/);
});

test('browser check findings: a paused lesson stays paused when the scene is drawn again; Tab stays inside Settings', () => {
    // a lesson setting, a review decision, the editor closing, "Reload scene": the scene is shown still while paused
    assert.match(page, /function redrawCurrentScene\(\) \{\s*if \(!slides\[currentSlide\]\) return;\s*if \(!ttsState\.isPlaying\) window\.editorStillPreview = true;\s*renderSlide\(currentSlide\);/);
    assert.ok((page.match(/redrawCurrentScene\)|redrawCurrentScene\(\);/g) || []).length >= 6, 'the re-draws after changes use it');
    assert.doesNotMatch(page, /\.then\(\(\) => renderSlide\(currentSlide\)\)/, 'no settings change re-draws a paused lesson into playing');
    // Settings wraps Tab among its visible controls
    assert.match(shell, /if \(e\.key === 'Tab' && !settings\.hidden && !settings\.closest\('\[inert\]'\)\) \{/);
    assert.match(shell, /else if \(!e\.shiftKey && \(!inside \|\| doc\.activeElement === last\)\) \{ e\.preventDefault\(\); first\.focus\(\); \}/);
});

test('while the player bar shows, the caption is lifted above it and the name card steps aside (the preview only; never while an export records)', () => {
    // app.js measures how far the caption must rise to sit 8 px above the bar (one row on desktops, two on tablets), from the
    // caption's own bottom (the style's box and the caption fit move it), and keeps it on <html>, never on the caption
    assert.match(shell, /const lift = Math\.max\(5, Math\.round\(playerBar\.offsetHeight \+ 8 - \(isFinite\(bottom\) \? bottom : 60\)\)\);/);
    assert.match(shell, /doc\.documentElement\.style\.setProperty\('--ui-caption-lift', `\$\{lift\}px`\);/);
    assert.match(shell, /new window\.ResizeObserver\(placeCaption\)\.observe\(playerBar\);/);
    assert.match(shell, /new MutationObserver\(placeCaption\)\.observe\(captionTrack, \{ attributes: true, attributeFilter: \['style'\] \}\);/);
    assert.doesNotMatch(shell, /captionTrack\.style\.setProperty/);
    // the page's own stage styles (product.css never touches what the video records)
    const plain = withoutComments(between(page, '#subtitle-track.active {', '#subtitle-track .highlight {'));
    const shows = 'body\\.presentation-active:is\\(:not\\(\\.lesson-playing\\), \\.player-active\\):not\\(\\[data-recording\\]\\):not\\(\\[data-mode="exporting"\\]\\)';
    const lifts = plain.match(/[^{}]*#subtitle-track\.active \{[^}]*\}/g).filter(r => /body\.presentation-active/.test(r));
    assert.equal(lifts.length, 1, 'one rule: whenever the bar shows (paused, or on activity while playing)');
    const [selector, body] = lifts[0].split('{');
    assert.match(selector.trim(), new RegExp(`^${shows} #subtitle-track\\.active$`), 'never while recording');
    // a transform only: the caption fit (cinematic.js) measures the caption's own bottom and size, so it is not changed
    assert.equal(body.replace('}', '').trim(), 'transform: translateX(-50%) translateY(calc(-1 * var(--ui-caption-lift, 5px)));');
    // the lifted caption would cover the presenter's name card: it is hidden while a caption shows above a visible bar
    assert.match(plain, new RegExp(`${shows}:has\\(#subtitle-track\\.active:not\\(:empty\\)\\):has\\(#voice-control-bar:not\\(\\.hidden-force\\)\\) \\.cine-presenter-name \\{\\s*visibility: hidden;\\s*\\}`));
    // stage-fit.css (the recorded caption fit) and product.css are untouched by it
    assert.doesNotMatch(read('stage-fit.css'), /caption-lift|lesson-playing|player-active/);
    assert.doesNotMatch(withoutComments(css), /caption-lift|#subtitle-track|cine-presenter-name/);
});

test('while a lesson plays, the player bar shows on activity and goes 3 s later (not while the pointer, keyboard focus or its menu is on it)', t => {
    assert.match(shell, /\['pointermove', 'pointerdown', 'keydown', 'focusin'\]\.forEach\(type => doc\.addEventListener\(type, showPlayer, true\)\);/);
    const block = between(shell, '    let playerIdle = null;', "    ['pointermove'");
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const classes = new Set(['presentation-active', 'lesson-playing']);
    const body = { classList: { contains: c => classes.has(c), add: c => classes.add(c), remove: c => classes.delete(c) } };
    let hover = false;
    let focusInside = false; // keyboard focus (:focus-visible)
    let menuOpen = false;
    const play = {};
    const doc = { body, activeElement: play }; // a mouse click on ▶ leaves the focus there, without :focus-visible
    const playerBar = { matches: s => s === ':hover' && hover, contains: n => n === play,
        querySelector: s => (s === ':focus-visible' ? (focusInside ? play : null) : s === '.control-menu:not([hidden])' ? (menuOpen ? {} : null) : null) };
    const showPlayer = new Function('doc', 'playerBar', `let playerIdle = null;${block.replace('let playerIdle = null;', '')}; return showPlayer;`)(doc, playerBar);
    showPlayer();
    assert.ok(classes.has('player-active'));
    t.mock.timers.tick(2999);
    assert.ok(classes.has('player-active'));
    showPlayer(); // more activity: 3 s from now
    t.mock.timers.tick(2999);
    assert.ok(classes.has('player-active'));
    t.mock.timers.tick(1);
    assert.ok(!classes.has('player-active'), 'gone 3 s after the last activity, though a mouse click left the focus on ▶');
    showPlayer();
    hover = true;
    t.mock.timers.tick(9000);
    assert.ok(classes.has('player-active'), 'kept under the pointer');
    hover = false;
    t.mock.timers.tick(3000);
    assert.ok(!classes.has('player-active'));
    showPlayer();
    focusInside = true;
    t.mock.timers.tick(9000);
    assert.ok(classes.has('player-active'), 'kept with the keyboard in it');
    focusInside = false;
    t.mock.timers.tick(3000);
    assert.ok(!classes.has('player-active'));
    showPlayer();
    menuOpen = true;
    t.mock.timers.tick(9000);
    assert.ok(classes.has('player-active'), 'kept while More is open');
    menuOpen = false;
    t.mock.timers.tick(3000);
    assert.ok(!classes.has('player-active'));
    classes.delete('lesson-playing');
    showPlayer();
    assert.ok(!classes.has('player-active'), 'paused: nothing to do (the bar shows anyway)');
});

test('browser check finding: focus goes back to the same control in a panel that drew itself again (the Studio, under Visual Review)', () => {
    assert.match(shell, /if \(remembered && !doc\.contains\(remembered\)\) remembered = sameControl\(top, remembered\);\s*if \(focusable\(remembered\)\) \{ remembered\.focus\(\); return; \}/);
    const block = between(shell, '    const CONTROL_KEYS', '    function focusPanel(top) {');
    const make = byId => new Function('$', 'CSS', `${block}; return sameControl;`)(id => byId[id] || null, { escape: s => String(s).replace(/"/g, '\\"') });
    const fresh = { tagName: 'BUTTON' };
    const outside = {};
    const top = { contains: n => n === fresh, querySelector: sel => (sel === 'button[data-action="open-review"]' ? fresh : null) };
    const old = (attrs, id = '') => ({ nodeType: 1, id, tagName: 'BUTTON', getAttribute: a => attrs[a] || null });
    // by the attributes the Studio names its controls with
    assert.equal(make({})(top, old({ 'data-action': 'open-review' })), fresh);
    // by its id, only inside that panel
    assert.equal(make({ again: fresh })(top, old({}, 'again')), fresh);
    assert.equal(make({ again: outside })(top, old({}, 'again')), null);
    // nothing to find it by: the panel's heading is used instead (focusPanel)
    assert.equal(make({})(top, old({ class: 'x' })), null);
    assert.equal(make({})(top, null), null);
});

test('educator audit: player tools named where there is room, developer items in debug only, Settings in plain words', () => {
    // the four lesson tools show their names on wide screens and tablets; the accessible name contains the visible word
    for (const [id, label, name] of [['review-btn', 'Review', 'Visual review'], ['editor-btn', 'Edit', 'Edit video'], ['studio-btn', 'Studio', 'Studio'], ['auto-export-btn', 'Export', 'Export video']]) {
        const button = between(bar, `id="${id}"`, '</button>');
        assert.match(button, new RegExp(`aria-label="${name}"`), id);
        assert.match(button, new RegExp(`<span class="control-tool-label">${label}</span>`), id);
        assert.ok(name.toLowerCase().includes(label.toLowerCase()), `${id}: the visible word is in its accessible name`);
    }
    const plain = withoutComments(css);
    assert.match(plain, /\.control-tool-label \{\s*display: none;\s*\}/);
    assert.match(plain, /@media \(min-width: 1280px\), \(min-width: 641px\) and \(max-width: 1100px\) \{\s*\.control-tool-label \{ display: inline; \}/);
    // "Developer tools…" (and its separator) only with ?visualDebug: they are in Settings → Advanced for everyone
    assert.match(bar, /<div class="control-menu-sep" role="separator" data-debug-only hidden><\/div>/);
    assert.match(bar, /id="player-dev-btn" class="control-menu-item" data-debug-only hidden>/);
    assert.match(shell, /menu\.querySelectorAll\('\[data-debug-only\]'\)\.forEach\(el => \{ el\.hidden = !debug\(\); \}\);/);
    // Settings: no developer sentences; the AI video button says what it does; the second voice list only when it applies
    assert.doesNotMatch(settings, /Read from the server's settings only|no service is contacted|Kept for compatibility|Visuals &amp; generation/);
    assert.match(settings, /data-section="visuals">Visuals &amp; AI<\/button>/);
    assert.match(settings, /Starts the AI video service chosen above on this server[\s\S]{0,200}id="start-server-btn"[^>]*>Start the AI video service<\/button>/);
    assert.match(plain, /\.settings-row > \.config-group:has\(> #gemini-voice-select:disabled\) \{\s*display: none;/);
    assert.match(startScreen, /Drop a file here, or click to choose one/);
    // Visual Review's heading shows the focus ring for keyboard users only (after a mouse click it read as a selected field)
    assert.doesNotMatch(page, /#review-panel-title:focus,/);
    assert.match(page, /#review-panel-title:focus-visible \{/);
    // the export panel's "Videos of this lesson" no longer touches the hint above it
    assert.match(page, /\.export-history-head \{\s*display: flex;\s*align-items: center;\s*justify-content: space-between;\s*margin-top: 16px;/);
});

test('educator audit: no scene transition while the Studio covers the stage (its snapshot showed over the Studio)', () => {
    assert.match(page, /if \(document\.startViewTransition && cineTransition !== 'cut' && !document\.querySelector\('\.studio-root'\)\) \{/);
    // the scene is still drawn (without the transition)
    const after = page.slice(page.indexOf("&& !document.querySelector('.studio-root')) {"));
    assert.match(after, /\} else \{\s*performDOMUpdate\(\);\s*finalizeSlideAnimations\(\);/);
});
