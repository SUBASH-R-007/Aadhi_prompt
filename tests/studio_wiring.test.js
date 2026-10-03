// Phase 20: the page's wiring of the Aadhi Studio (index.html), checked in its source: the Studio files load, the start screen
// and the player bar open it, the Document Assistant (Phase 11) hands its prepared source to the Studio, the lesson is written
// by the server run (POST /api/studio/lessons with the page's own prompt), the existing presenter / style panels are moved in
// (not rebuilt), media through the existing lesson batch, Visual Review / the editor / the quality check / the export as they
// are, a new lesson never inherits the previous one's id or editor data, every save keeps the source and the Studio record,
// a per-scene generation names its scene, and a still preview never outlives a pause. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const studio = page.slice(page.indexOf('// ---- Aadhi Studio (Phase 20, studio.js)'), page.indexOf("document.getElementById('studio-btn').addEventListener"));
const visuals = fs.readFileSync(path.join(__dirname, '..', 'visuals.js'), 'utf8');

test('the Studio files load and the start screen and the player bar open it', () => {
    assert.match(page, /<link rel="stylesheet" href="studio\.css">\n {4}<link rel="stylesheet" href="editor\.css">\n {4}<link rel="stylesheet" href="stage-fit\.css">\n {4}<link rel="stylesheet" href="product\.css">\n<\/head>/);
    assert.match(page, /<script src="editor_ui\.js"><\/script>\s*<script src="studio\.js"><\/script>/);
    // (Phase 21: "Open your lessons" on the start screen; its primary "Create a lesson" opens the Studio's create view)
    assert.match(page, /<button id="open-studio-btn" class="neon-btn neon-btn-outline animate-entrance-2" type="button"/);
    assert.match(page, /<button type="button" id="studio-btn" class="control-btn" title="Studio: this lesson's steps/);
    assert.match(page, /document\.getElementById\('open-studio-btn'\)\.addEventListener\('click', \(\) => openStudioHome\('lessons'\)\);/);
    assert.ok(studio.length > 2000, 'the Studio section is found');
    assert.match(studio, /new AadhiStudio\.StudioPanel\(\{ doc: document, adapter: studioAdapter, storage: localStorage \}\)/);
});

test('the source: the Document Assistant prepares it and hands it to the Studio (one parser, one assistant)', () => {
    assert.match(page, /if \(typeof studioSourceWanted === 'function'\) \{ studioSourceWanted\(prepared\); return; \}/);
    assert.match(page, /window\.preparedSource = prepared;\s*runGeneration\(\);/, 'without the Studio the assistant still generates as before');
    assert.match(studio, /documentAssistant\.open\(chosen\);/);
    assert.match(studio, /source: \{ document_id: prepared\.documentId, analysis_id: prepared\.analysisId \}/);
});

test('the lesson is written by the server run with the page\'s own prompt (no second lesson generator in the page)', () => {
    assert.match(studio, /system_prompt: getSystemPrompt\(\)/);
    assert.match(studio, /studioFetch\('POST', '\/api\/studio\/lessons', body\)/);
    assert.match(studio, /studioFetch\('GET', `\/api\/studio\/runs\/\$\{encodeURIComponent\(runId\)\}`\)/);
    assert.match(studio, /studioFetch\('POST', `\/api\/studio\/runs\/\$\{encodeURIComponent\(runId\)\}\/cancel`\)/);
    assert.match(studio, /studioFetch\('GET', `\/api\/studio\/lessons\/\$\{encodeURIComponent\(projectId\)\}`\)/);
    assert.match(studio, /studioFetch\('PUT', `\/api\/studio\/lessons\/\$\{currentProjectId\}\/checkpoint`, \{ name, value \}\)/);
});

test('the existing systems do the work: panels moved in, the lesson batch, Visual Review, the editor, quality, preview, export', () => {
    assert.match(studio, /document\.getElementById\(kind === 'presenter' \? 'presenter-settings' : 'cinematic-settings'\)/);
    assert.match(studio, /container\.appendChild\(node\);/);
    assert.match(studio, /m\.parent\.insertBefore\(m\.node/);
    assert.match(studio, /aiMediaApi\.startLessonBatch\(currentProjectId, mode === 'all' \? 'all' : 'image'\)/);
    // a run waiting for a person: its own retry / dismissal (Phase 9); a failed visual: made again through the path that made it,
    // and "Continue without" is the user's decision recorded in Visual Review (fact-check finding: /resolve refused failed runs,
    // and a dismissed run's visual was queued again by the next batch)
    assert.match(studio, /if \(item\.status === 'needs_attention' \|\| item\.status === 'attention'\) return aiMediaApi\.resolveRun\(runId, 'retry'\);/);
    assert.match(studio, /if \(item\.slot === 'presenter'\) return studioAdapter\.openReview\(index\);/);
    assert.match(studio, /aiMediaApi\.generateVideo\(scene\.prompt, \{ projectId: currentProjectId, sceneIndex: index, slot: 'main', sceneId: scene\.scene_id \}\)/);
    assert.match(studio, /return aiMediaApi\.startLessonBatch\(currentProjectId, 'image'\);/);
    assert.match(studio, /try \{ await aiMediaApi\.resolveRun\(runId, 'dismiss'\); \}/);
    assert.match(studio, /const removed = await reviewSession\.remove\(\);/);
    assert.match(studio, /if \(!removed\) throw new Error\(reviewSession\.message/);
    assert.match(studio, /visualReview\.showQualityScene\(sceneIndex, 'media'\)/);
    assert.match(studio, /await openLessonEditor\(\);/);
    assert.match(studio, /const report = await cinematicApi\.quality\(slides, cinematicPlanSettings\(\), currentProjectId \|\| null, lessonConceptMap\(\)\);/);
    assert.match(studio, /exportVideo: \(\) => \{ closeStudio\(\); exportFlow\.open\(\); \}/);
    assert.match(page, /if \(studioReturnAfterEditor\) \{ studioReturnAfterEditor = false; openStudio\(\); \}/);
});

test('a lesson opened from the Studio waits (no autoplay), keeps its URL; the stage is prepared paused for editing', () => {
    assert.match(studio, /openSavedProject\(data, id\);/);
    assert.match(studio, /window\.history\.replaceState\(null, '', `\?project_id=\$\{id\}`\);/);
    assert.match(studio, /ttsState\.isPlaying = false;\s*updateTTSButtons\(\);\s*const first = /);
    assert.match(studio, /window\.editorStillPreview = true;\s*renderSlide\(currentSlide\);/);
});

test('a new lesson never inherits the previous lesson\'s id, editor data or source; every save keeps source and Studio record', () => {
    assert.match(page, /currentProjectId = null;\s*window\.lessonEditor = null;\s*window\.lessonStudio = null;[^\n]*\n\s*window\.lessonSource = prepared \?/);
    const saves = page.match(/editor: window\.lessonEditor \|\| null, source_document: window\.lessonSource \|\| null, studio: window\.lessonStudio \|\| null/g) || [];
    assert.equal(saves.length, 5, 'every later save of the lesson (save version, export, script editor saves)');
    assert.match(page, /window\.lessonSource = data\.source_document && typeof data\.source_document === 'object' \? data\.source_document : null;/);
    assert.match(page, /window\.lessonStudio = data\.studio && typeof data\.studio === 'object' \? data\.studio : null;/);
});

test('a per-scene generation names its scene (a result finds its scene even after a move)', () => {
    assert.match(visuals, /if \(body\.project_id !== undefined && typeof sceneId === 'string' && \/\^s-\[0-9a-f\]\{12\}\$\/\.test\(sceneId\)\) body\.scene_id = sceneId;/);
    assert.match(page, /slot: 'main', sceneId: slide\.scene_id \}\);/);
    assert.match(page, /slot: 'main', sceneId: s\.scene_id \}\)/);
});

test('a still preview never outlives a pause (a silent or muted scene cannot swallow the next narration)', () => {
    assert.match(page, /if \(ttsState\.isPlaying\) window\.editorStillPreview = false;[^\n]*\n\s*if \(window\.editorStillPreview && text\) \{/);
});

test('an export names the lesson as it is (the server fingerprint after pending edits are saved); history can say if it matches', () => {
    assert.match(page, /lessonLink: async \(\) => \{\s*if \(!currentProjectId\) return null;/);
    assert.match(page, /await lessonEditorSession\.autosave\.flush\(\);\s*const state = await studioFetch\('GET', `\/api\/studio\/lessons\/\$\{currentProjectId\}`\);/);
    assert.match(page, /\{ project_id: currentProjectId, fingerprint: state\.fingerprint, revision: String\(state\.revision \|\| ''\)\.slice\(0, 40\) \}/);
    assert.match(page, /lessonFingerprint: async \(\) => \{/);
    assert.match(page, /\.export-history-match\[data-match="current"\]/);
});

test('the quality check is recorded with the fingerprint of the lesson it checked (read before it runs); renders name their scene', () => {
    // audit finding: the fingerprint was read after the check (a lesson changed meanwhile looked checked)
    assert.match(studio, /let fingerprint = null;\s*if \(currentProjectId\) \{ try \{ fingerprint = \(await studioAdapter\.lessonState\(currentProjectId\)\)\.fingerprint \|\| null; \}/);
    assert.ok(studio.indexOf('fingerprint = (await studioAdapter.lessonState') < studio.indexOf('const report = await runLessonQuality();'), 'read before the check');
    assert.match(studio, /await studioAdapter\.checkpoint\('quality', \{ status, counts, fingerprint \}\);\s*recorded = true;/);
    assert.match(page, /slot: 'main', scene_id: slide\.scene_id \|\| undefined \}\)/);
    assert.match(page, /slot: 'main', scene_id: s\.scene_id \|\| undefined \}\)/);
    assert.match(page, /slot: 'side', scene_id: s\.scene_id \|\| undefined \}\)/);
});

test('browser check findings: an undo\'s plan is never overwritten by an older re-plan; the style refreshes the Studio; Preview plays the whole lesson', () => {
    // a lesson re-plan whose scenes' layout decisions changed meanwhile is not applied (an undo's review answered first)
    assert.match(page, /const order = \(\) => slides\.map\(s => `\$\{\(s && s\.scene_id\) \|\| ''\}:\$\{JSON\.stringify\(\(s && s\.visual_review && s\.visual_review\.composition\) \|\| null\)\}`\)\.join\('\|'\);/);
    assert.match(page, /if \(order\(\) !== sent\) \{\s*if \(attempt < 2\) return planLessonCinematic\(attempt \+ 1\);/);
    // a style chosen in the Studio's Style stage shows at once
    assert.match(page, /\.then\(\(\) => \{ if \(typeof studioPanel !== 'undefined' && studioPanel && studioPanel\.isOpen\) studioPanel\.refresh\(\); \}\)/);
    // Preview: from the first scene that plays (the stage already up: no intro again), else from the very start
    assert.match(studio, /const first = nextPlayableScene\(-1, 1\);\s*if \(first === -1\) return;\s*window\.editorStillPreview = false;\s*currentSlide = first;\s*ttsState\.isPlaying = true;/);
    assert.match(studio, /renderSlide\(first\);\s*\} else \{\s*startBtn\.click\(\);/);
});

test('rendered-video findings: captions never cut a number at its decimal point; the skill tree refits; the quiz explanation fits', () => {
    assert.ok(page.includes('const sentenceMatches = cleanText.match(/[^]*?[.!?]+(?=\\s|$)|[^]+$/g) || [cleanText];'));
    const re = /[^]*?[.!?]+(?=\s|$)|[^]+$/g;
    const text = 'Empty: Acceleration 2 m/s^2. Full: Acceleration 0.5 m/s^2.';
    assert.deepEqual(text.match(re).map(s => s.trim()), ['Empty: Acceleration 2 m/s^2.', 'Full: Acceleration 0.5 m/s^2.']);
    assert.equal(text.match(re).join(''), text, 'every character is in a caption (the timings count them)');
    assert.match(page, /if \(typeof ResizeObserver !== 'undefined'\) new ResizeObserver\(\(\) => fitConceptMap\(\)\)\.observe\(conceptMapPanel\);/);
    assert.match(page, /\.quiz-cp-reveal \.quiz-cp-countdown \{\s*display: none;\s*\}/);
    assert.match(page, /if \(track\) \{ track\.classList\.remove\('active'\); track\.textContent = ''; \}\s*startCaptionLog\(\);/);
});
