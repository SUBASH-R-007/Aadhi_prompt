// Phase 19: the page's wiring of the Advanced Video Editor (index.html), checked in its source: the editor files load, the
// 🎬 button opens it, edits are saved in place through PUT /api/editor (never a history entry per edit), a stale save is
// reported with its revision (the model reloads and replays), layout choices go through Visual Review's composition review,
// visuals through Visual Review's own session, the live stage is the preview (renderSlide), every save of the lesson
// carries its editor data, a saved lesson brings it back. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const editor = page.slice(page.indexOf('// ---- Advanced Video Editor (Phase 19'), page.indexOf("document.getElementById('editor-btn').addEventListener"));

test('the editor files load and the 🎬 button opens the editor', () => {
    // (Phase 21: the caption fit and the product shell's styles follow it, before </head>)
    assert.match(page, /<link rel="stylesheet" href="editor\.css">\n {4}<link rel="stylesheet" href="stage-fit\.css">\n {4}<link rel="stylesheet" href="product\.css">\n<\/head>/);
    assert.match(page, /<script src="cinematic\.js"><\/script>\s*<script src="editor\.js"><\/script>\s*<script src="editor_ui\.js"><\/script>/);
    assert.match(page, /<button type="button" id="editor-btn" class="control-btn" title="Edit video: scenes, timeline and timing" aria-label="Edit video">/);
    assert.match(page, /document\.getElementById\('editor-btn'\)\.addEventListener\('click', openLessonEditor\);/);
});

test('edits are saved in place with a revision; a stale or locked save is reported with its revision', () => {
    assert.match(editor, /window\.fetch\(`\/api\/editor\/\$\{encodeURIComponent\(projectId\)\}`, \{\s*method: 'PUT'/);
    assert.match(editor, /err\.status = res\.status;/);
    assert.match(editor, /err\.revision = detail\.revision;/);
    assert.match(editor, /err\.locked = !!detail\.generating;/);
    // an old lesson's ids are kept when the editor opens, before any edit (the server's own scenes, unchanged)
    assert.match(page, /if \(Number\(data\.new_ids\) > 0 && Array\.isArray\(data\.scenes\)\) \{\s*try \{\s*data\.revision = await editorApi\.save\(projectId, \{ expected_revision: data\.revision, scenes: data\.scenes, editor: data\.editor \|\| null \}\);/);
    assert.match(editor, /new AadhiEditor\.Autosave\(\{ model,/);
    assert.match(editor, /reload: async \(\) => \{\s*const fresh = await editorApi\.load\(pid\(\)\);/);
    assert.doesNotMatch(editor.slice(0, editor.indexOf('saveVersion:')), /\/save-history/, 'editing never creates a history entry');
    assert.match(editor, /saveVersion: async \(\) => \{\s*await autosave\.flush\(\);\s*const res = await fetch\('\/save-history'/, 'a version is a history entry on purpose');
});

test('the model edits the page\'s own slides; drafts are offered back; the live stage is the preview', () => {
    assert.match(editor, /new AadhiEditor\.EditorModel\(\{ scenes: slides, editor: window\.lessonEditor,/);
    assert.match(editor, /structureLocked: \(\) => generating > 0/);
    assert.match(editor, /const draft = AadhiEditor\.loadDraft\(projectId\);/);
    assert.match(editor, /model\.subscribe\(\(\) => AadhiEditor\.saveDraft\(model\)\);/);
    assert.match(editor, /seek: index => showScene\(index\),/);
    // editing never starts playback on its own: a paused lesson shows the scene still (browser check finding)
    assert.match(editor, /if \(ttsState\.isPlaying\) \{ stillShown = false; renderSlide\(index\); return; \}/);
    assert.match(editor, /window\.editorStillPreview = true;\s*renderSlide\(index\);/);
    assert.match(page, /if \(window\.editorStillPreview && text\) \{ \/\/ Phase 19: the editor shows this scene while the lesson is paused \(nothing plays\)\s*window\.editorStillPreview = false;/);
    assert.match(editor, /if \(replan\) await planLessonCinematic\(\);/, 'the existing planner (Phase 16 retimes from the narration)');
    assert.match(page, /if \(typeof window\.lessonEditorSync === 'function'\) window\.lessonEditorSync\(index\);/);
});

test('layout choices go through Visual Review\'s composition review; visuals through Visual Review\'s session', () => {
    // Visual Review writes a scene by its position: the editor's own edits are saved first, or nothing is sent (audit)
    assert.match(editor, /const ensureSaved = async \(\) => \{\s*const ok = await autosave\.flush\(\);\s*if \(ok === false \|\| model\.dirty\) throw new Error/);
    assert.match(editor, /applyComposition: async result => \{\s*await ensureSaved\(\);/);
    assert.match(editor, /cinematicApi\.review\(\{ project_id: pid\(\), scene: slides\[at\], scenes: slides, settings: cinematicPlanSettings\(\),\s*\.\.\.lessonConceptMap\(\), scene_index: at, action,/);
    assert.match(editor, /const scene = slides\.find\(s => s && s\.scene_id === result\.scene_id\);/, 'the reply goes to the scene by id');
    assert.match(editor, /if \(restore && restore\.status === 'approved'\) await send\('keep'\);/, 'an undo gives back an approval of that same layout');
    assert.match(editor, /`\/api\/editor\/\$\{encodeURIComponent\(pid\(\)\)\}\/status`/, 'the generation lock is polled');
    assert.match(editor, /await reviewSession\.chooseAsset\(asset\);/);
    assert.match(editor, /await reviewSession\.remove\(\);/);
    assert.match(editor, /assetLibrary\.open\(\{ pick: \{ kinds: slot === 'main' \? \['video'\] : \['image', 'video'\]/);
});

test('every save of the lesson carries its editor data; a saved lesson brings it back', () => {
    const saves = page.split("fetch('/save-history'").slice(1).map(s => s.slice(0, s.indexOf('})')));
    assert.ok(saves.length >= 6, `${saves.length} saves`);
    saves.forEach((body, i) => assert.match(body, /editor: window\.lessonEditor \|\| null/, `save ${i + 1}`));
    assert.match(page, /window\.lessonEditor = data\.editor && typeof data\.editor === 'object' \? data\.editor : null;/);
});

test('quality is invalidated per scene (by scene id) so one edit never makes every scene look stale', () => {
    assert.match(page, /function rememberQualityInputs\(\) \{[\s\S]{0,400}lessonQuality\.sceneKeys\[s\.scene_id\] = qualitySceneKey\(s\);/);
    const fn = page.slice(page.indexOf('function qualityFor(sceneId)'), page.indexOf('function qualityLesson()'));
    assert.match(fn, /const at = lessonQuality\.sceneIds\.indexOf\(sceneId\);/, 'findings follow the scene by id after a move');
    assert.match(fn, /lessonQuality\.sceneKeys\[sceneId\] !== qualitySceneKey\(scene\)/, 'only that scene\'s own change makes it stale');
    assert.match(fn, /issues: report\.issues\.filter\(f => f && f\.scene === at\)/);
    assert.match(editor, /qualityFor: sceneId => qualityFor\(sceneId\),/);
    assert.match(editor, /qualityLesson: \(\) => qualityLesson\(\),/);
});
