// Phase 17: the page's wiring of the video styling system (index.html), checked in its source: the lesson keeps its style
// (every save carries it; a style change is stored in place; opening a lesson restores it, an old lesson has none), the
// presenter's name card uses the presenter profile's own name, Visual Review gets the style summary and the scene options,
// and charts read the style's chart colours. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

test('every save of the lesson carries its video style', () => {
    const saves = page.split("fetch('/save-history'").slice(1).map(s => s.slice(0, s.indexOf('})')));
    assert.ok(saves.length >= 5, `${saves.length} saves`);
    saves.forEach((body, i) => assert.match(body, /cinematic_style: lessonStyleChoice\(\)/, `save ${i + 1}`));
    assert.match(page, /function lessonStyleChoice\(\) \{[\s\S]{0,400}typeof s\.style === 'string' && s\.style \?[\s\S]{0,300}: null;/);
});

test('a style change is kept with the open lesson in place; opening a lesson restores its style (none: the original look)', () => {
    assert.match(page, /onStyleChange: choice => \{\s*styleChangeSeq \+= 1;[^\n]*\n\s*if \(!currentProjectId\) return;\s*window\.fetch\('\/api\/cinematic\/style'/);
    assert.match(page, /project_id: currentProjectId, \.\.\.\(choice \|\| \{ style: null \}\)/);
    assert.match(page, /cinematicSettings\.applyLessonStyle\(data\.cinematic_style \|\| null\)/);
    // a style chosen while a save is on its way follows the new lesson (the save carried the older choice)
    assert.match(page, /styleChangeSeq \+= 1;/);
    assert.match(page, /function rememberSavedProject\(res\) \{[\s\S]{0,400}if \(styleChangeSeq !== styleSentSeq\) \{[\s\S]{0,200}'\/api\/cinematic\/style'/);
    assert.match(page, /function lessonStyleChoice\(\) \{\s*styleSentSeq = styleChangeSeq;/);
    const exportSave = page.slice(page.indexOf('async function ensureExportProject('), page.indexOf('async function runLimited('));
    assert.match(exportSave, /if \(currentProjectId && styleChangeSeq !== styleSentSeq\) \{[\s\S]{0,200}'\/api\/cinematic\/style'/);
});

test('the presenter name card shows the presenter profile\'s own name; Visual Review gets the style summary and options', () => {
    assert.match(page, /presenterName: plan => \{[\s\S]{0,300}presenterSettings\.profile\(id\)[\s\S]{0,120}profile\.name/);
    assert.match(page, /styleSummary: \(scene, plan\) => AadhiCinematic\.styleSummary\(scene, plan\)/);
    assert.match(page, /styleOptions: \(\) => \{[\s\S]{0,300}accent: looks\.options\.accent, background: looks\.options\.background/);
});

test('charts read the style\'s chart colours only in a styled cinematic scene (today\'s colours otherwise)', () => {
    const chart = page.slice(page.indexOf('function renderSideChart('), page.indexOf('let current3DScene'));
    assert.match(chart, /AadhiCinematic\.lookOf\(chartPlan\)/); // this scene's look, from its plan (drawn before the look is applied)
    assert.match(chart, /chartLook\.legacy !== true/); // a lesson with no style chosen keeps today's colours
    assert.match(chart, /AadhiCinematic\.validStyleVar\(/);
    assert.match(chart, /\['pie', 'doughnut', 'polarArea'\]\.includes\(config\.chart_type\)/);
    assert.match(chart, /Array\.isArray\(d\.backgroundColor\) \? d/);
    assert.match(chart, /token\('chart-tick'\) \|\| 'rgba\(255, 255, 255, 0\.6\)'/);
    assert.match(chart, /token\('chart-grid'\) \|\| 'rgba\(255, 255, 255, 0\.1\)'/);
    assert.match(chart, /series\[i % 4\] \|\| d\.backgroundColor/);
});
