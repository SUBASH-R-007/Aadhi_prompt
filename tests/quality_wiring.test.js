// Phase 18: the page's wiring of the Quality & Consistency Engine (index.html, export.js), checked in their source: the
// report is asked for on request and before an export (never saved: no history entry is created for it), "Fix
// automatically" applies fresh plans only to the scenes the finding names (nothing saved by the planning call; a failure
// reaches the panel), a suggestion goes through Visual Review's own composition review, the panel knows when the lesson
// changed since the check (a key taken before the request, from everything the checks read), and the export shows the
// findings under their own heading without being blocked. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const exporter = fs.readFileSync(path.join(__dirname, '..', 'export.js'), 'utf8');

test('the review adapter offers the quality report, a re-check, the stale state, repairs and suggestions', () => {
    assert.match(page, /quality: \(\) => lessonQuality\.report,/);
    // a check never creates a history entry (the open lesson's id or none, never ensureExportProject); the key first
    // (Phase 19: per-scene inputs are remembered too, for targeted invalidation)
    assert.match(page, /qualityRun: async \(\) => \{\s*const key = lessonQualityKey\(\);[\s\S]{0,220}const report = await cinematicApi\.quality\(slides, cinematicPlanSettings\(\), currentProjectId \|\| null, lessonConceptMap\(\)\);[\s\S]{0,120}lessonQuality\.key = key;/);
    assert.match(page, /qualityStale: \(\) => !!lessonQuality\.report && lessonQuality\.key !== lessonQualityKey\(\),/);
    // "Fix automatically": fresh plans, applied only to the named scenes (cinematicApi.plan throws on failure: the panel says so)
    const repair = page.slice(page.indexOf('qualityRepair: async issue => {'), page.indexOf('qualityApply:'));
    assert.match(repair, /const targets = Array\.isArray\(action\.scenes\) \? action\.scenes\.filter\(i => Number\.isInteger\(i\) && slides\[i\]\) : \[\];/);
    assert.match(repair, /await cinematicApi\.plan\(slides, cinematicPlanSettings\(\), currentProjectId, lessonConceptMap\(\)\);/);
    assert.match(repair, /targets\.forEach\(i => \{ if \(plans\[i\]\) slides\[i\]\.cinematic_plan = plans\[i\]; else delete slides\[i\]\.cinematic_plan; \}\);/);
    assert.doesNotMatch(repair, /planLessonCinematic\(\)/, 'never the whole lesson (other scenes keep their plans and approvals)');
    // a suggestion: Visual Review's composition review with the suggested overrides (it becomes the user's choice)
    assert.match(page, /qualityApply: async issue => cinematicApi\.review\(\{[\s\S]{0,300}action: 'change',\s*overrides: issue\.repair\.action\.overrides \}\),/);
});

test('the stale key covers everything the checks read, without what changes on its own', () => {
    assert.match(page, /const QUALITY_VOLATILE = new Set\(\['reviewed_at', 'url', 'ai', 'notes', 'httpStatus', 'created_at', 'updated_at', 'quality'\]\);/);
    assert.match(page, /return JSON\.stringify\(\[cinematicPlanSettings\(\), conceptMapData \|\| null, slides \|\| \[\]\],\s*\(k, v\) => \(QUALITY_VOLATILE\.has\(k\) \? undefined : v\)\);/);
});

test('before an export the findings have their own heading; the export is never blocked by an optional check', () => {
    const start = page.indexOf('async function prepareLessonForExport(');
    const prep = page.slice(start, page.indexOf('return { warnings, quality };', start) + 40);
    assert.match(prep, /const key = lessonQualityKey\(\);\s*rememberQualityInputs\(\);\s*const report = await cinematicApi\.quality\(slides, cinematicPlanSettings\(\), currentProjectId \|\| null, lessonConceptMap\(\)\);/);
    assert.match(prep, /\['blocking', 'error', 'warning'\]\.includes\(f\.severity\)/);
    assert.match(prep, /const listed = new Set\(\['media\.missing', 'style\.background_fallback'\]\);/, 'what the warnings already list is not repeated');
    assert.match(prep, /\} catch \(e\) \{\s*console\.warn\('The lesson quality could not be checked before the export:', e\);/);
    assert.ok(prep.includes('return { warnings, quality };'));
    assert.match(exporter, /const \{ warnings, quality = \[\] \} = await this\.hooks\.prepare\(/);
    assert.match(exporter, /The quality check found things to review \(Visual Review → Quality\):/);
    assert.match(exporter, /if \(warnings\.length \|\| notes\.length\) \{/);
});
