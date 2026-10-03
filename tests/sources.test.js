'use strict';
// Unit tests for the Document Assistant (sources.js, Phase 11). Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const S = require('../sources.js');

const FIXTURES = path.join(__dirname, 'fixtures', 'sources');

// ---- extraction ----------------------------------------------------------------------------------------------

test('PDF text items become headings, paragraphs, lists, code, formulas and captions with page numbers', () => {
    const item = (str, y, fontSize = 11, x = 72, mono = false) => ({ str, x, y, fontSize, mono, width: str.length * fontSize * 0.5 });
    const pages = [
        { page: 1, items: [
            item('Photosynthesis', 760, 24),
            item('Introduction', 720, 16),
            item('Photosynthesis is the process by which plants make', 690), item('glucose from light.', 676),
            item('• Plants need light.', 640),
            item('6CO2 + 6H2O → C6H12O6 + 6O2', 610),
            item('Figure 1: A leaf.', 580),
            item('1', 40) // a page number
        ] },
        { page: 2, items: [
            item('Code', 760, 16),
            item('def f(x):', 730, 10, 72, true), item('return x', 718, 10, 96, true), // indented 4 characters
            item('The function returns its input.', 690)
        ] }
    ];
    const blocks = S.blocksFromPdfPages(pages);
    assert.deepEqual(blocks.map(b => [b.type, b.level || null, b.page]), [
        ['heading', 1, 1], ['heading', 2, 1], ['paragraph', null, 1], ['list_item', null, 1], ['formula', null, 1], ['caption', null, 1],
        ['heading', 2, 2], ['code', null, 2], ['paragraph', null, 2]]);
    assert.equal(blocks[2].text, 'Photosynthesis is the process by which plants make glucose from light.'); // lines joined
    assert.equal(blocks[3].text, 'Plants need light.'); // bullet removed
    assert.equal(blocks[7].text, 'def f(x):\n    return x'); // one code block, indentation rebuilt from the positions
    assert.ok(!blocks.some(b => b.text === '1'));
});

test('running headers and footers repeated on most pages are dropped', () => {
    const pages = [1, 2, 3, 4].map(p => ({ page: p, items: [
        { str: 'Physics Notes 2026', x: 72, y: 800, fontSize: 9 },
        { str: `Content of page ${p}.`, x: 72, y: 700, fontSize: 11 }] }));
    const blocks = S.blocksFromPdfPages(pages);
    assert.deepEqual(blocks.map(b => b.text), ['Content of page 1.', 'Content of page 2.', 'Content of page 3.', 'Content of page 4.']);
});

test('mammoth HTML keeps the title, headings, lists, tables, images, captions, code and formulas', () => {
    const html = '<h1 class="title">Cells &amp; Tissues</h1><h2>The cell</h2><p>A cell is the <strong>basic</strong> unit.</p>'
        + '<ul><li>Nucleus<ul><li>Nucleolus</li></ul></li><li>Membrane</li></ul>'
        + '<table><tr><th>Part</th><th>Role</th></tr><tr><td><p>Stoma</p></td><td>Gas exchange</td></tr></table>'
        + '<p><img src="data:image/png;base64,AAAA" alt="Leaf diagram" /></p><p class="caption">Figure 1: Leaf</p>'
        + '<pre>x = 1<br />y = 2</pre><p>E = m c²</p><p>Ignore &lt;script&gt; tags</p>';
    const blocks = S.blocksFromHtml(html);
    assert.deepEqual(blocks.map(b => b.type), ['title', 'heading', 'paragraph', 'list_item', 'list_item', 'list_item', 'table', 'image', 'caption', 'code', 'formula', 'paragraph']);
    assert.equal(blocks[0].text, 'Cells & Tissues');
    assert.equal(blocks[2].text, 'A cell is the basic unit.');
    assert.deepEqual(blocks[6].rows, [['Part', 'Role'], ['Stoma', 'Gas exchange']]);
    assert.equal(blocks[7].alt, 'Leaf diagram');
    assert.ok(!JSON.stringify(blocks).includes('base64')); // image data is not sent
    assert.equal(blocks[9].text, 'x = 1\ny = 2');
    assert.equal(blocks[11].text, 'Ignore <script> tags'); // text, never markup
});

test('plain text: markdown and numbered headings, lists, fenced code, formulas, page markers', () => {
    const blocks = S.blocksFromText(fs.readFileSync(path.join(FIXTURES, 'C_technical.txt'), 'utf8'));
    assert.deepEqual(blocks.slice(0, 4).map(b => b.type), ['title', 'heading', 'paragraph', 'formula']);
    const code = blocks.filter(b => b.type === 'code');
    assert.equal(code.length, 2);
    assert.equal(code[0].lang, 'python');
    assert.ok(code[0].text.includes('def area(r):\n    return 3.14159 * r * r'));
    assert.deepEqual(blocks.filter(b => b.type === 'list_item').map(b => b.text.slice(0, 2)), ['1.', '2.', '3.']);
    const paged = S.blocksFromText('--- Page 1 ---\nIntro text here.\n\n--- Page 2 ---\nMore text.');
    assert.deepEqual(paged.map(b => b.page), [1, 2]);
    const underlined = S.blocksFromText('Heading One\n===========\nBody text.\n\nSub\n---\nMore.');
    assert.deepEqual(underlined.map(b => [b.type, b.level || null]), [['heading', 1], ['paragraph', null], ['heading', 2], ['paragraph', null]]);
});

test('non-English text is kept exactly', () => {
    const text = fs.readFileSync(path.join(FIXTURES, 'D_tamil.txt'), 'utf8');
    const blocks = S.blocksFromText(text);
    assert.equal(blocks[0].type, 'title');
    assert.equal(blocks[0].text, 'ஒளிச்சேர்க்கை');
    for (const b of blocks) assert.ok(text.includes(b.text.split(' ')[0]));
});

test('file kinds, formula detection and escaping', () => {
    assert.equal(S.fileKind({ name: 'a.PDF', type: '' }), 'pdf');
    assert.equal(S.fileKind({ name: 'a.docx', type: '' }), 'docx');
    assert.equal(S.fileKind({ name: 'notes.txt', type: 'text/plain' }), 'txt');
    assert.equal(S.fileKind({ name: 'script.json', type: '' }), 'json');
    assert.equal(S.fileKind({ name: 'x.exe', type: '' }), null);
    assert.ok(S.looksLikeFormula('F = m × a'));
    assert.ok(S.looksLikeFormula('6CO2 + 6H2O → C6H12O6 + 6O2'));
    assert.ok(!S.looksLikeFormula('The result is equal to the sum of the parts = obvious to everyone reading'));
    assert.equal(S.escapeHtml('<b>"x"</b> & \'y\''), '&lt;b&gt;&quot;x&quot;&lt;/b&gt; &amp; &#39;y&#39;');
});

test('extractFile uses pdf.js / mammoth / text and hashes the file', async () => {
    const bytes = new TextEncoder().encode('Title: T\n\nSome text.');
    const file = { name: 'n.txt', type: 'text/plain', size: bytes.length, arrayBuffer: async () => bytes.buffer };
    const crypto = { subtle: { digest: async () => new Uint8Array(32).fill(171).buffer } };
    const txt = await S.extractFile(file, { crypto });
    assert.equal(txt.source_type, 'txt');
    assert.equal(txt.file_sha256, 'ab'.repeat(32));
    assert.deepEqual(txt.blocks.map(b => b.type), ['title', 'paragraph']);
    const pdfjsLib = { getDocument: () => ({ promise: Promise.resolve({ numPages: 1, getPage: async () => ({
        getTextContent: async () => ({ styles: { f1: { fontFamily: 'sans-serif' } }, items: [
            { str: 'Big Title', transform: [20, 0, 0, 20, 72, 700], width: 90, fontName: 'f1' },
            { str: 'Body text line.', transform: [10, 0, 0, 10, 72, 650], width: 80, fontName: 'f1' }] }) }) }) }) };
    const pdf = await S.extractFile({ name: 'a.pdf', type: 'application/pdf', size: 10, arrayBuffer: async () => new ArrayBuffer(10) }, { pdfjsLib, crypto });
    assert.equal(pdf.page_count, 1);
    assert.deepEqual(pdf.blocks.map(b => [b.type, b.page]), [['heading', 1], ['paragraph', 1]]);
    let styleMap = null;
    const mammoth = { convertToHtml: async (input, options) => { styleMap = options.styleMap; return { value: '<h1>H</h1><p>P.</p>' }; } };
    const docx = await S.extractFile({ name: 'a.docx', type: '', size: 10, arrayBuffer: async () => new ArrayBuffer(10) }, { mammoth, crypto });
    assert.equal(docx.source_type, 'docx');
    assert.ok(styleMap.some(s => s.includes("'Title'")));
    await assert.rejects(S.extractFile({ name: 'a.json', type: '', size: 1, arrayBuffer: async () => new ArrayBuffer(1) }, { crypto }), /PDF, Word/);
    await assert.rejects(S.extractFile({ name: 'big.pdf', type: '', size: 30 * 1024 * 1024, arrayBuffer: async () => new ArrayBuffer(1) }, { crypto }), /20 MB/);
});

// ---- editing ----------------------------------------------------------------------------------------------------

function view(extra = {}) {
    return {
        analysis_id: 'a'.repeat(32), status: 'completed', stale: false, document: { file_name: 'x.txt', version: 1, source_type: 'txt' },
        analysis: {
            document: { title: { text: 'Energy', provenance: 'source' }, subject: { text: null, status: 'requires_user_input' },
                language: { code: 'en', name: 'English' }, section_count: 2, source_type: 'txt' },
            readiness: { verdict: 'missing_context', ready_for_generation: false, open_warnings: 1, open_info: 0 },
            ai: { status: 'not_requested' },
            quality: { strengths: [{ text: 'Clear title' }], issues: [
                { id: 'i1', type: 'formula_undefined_variables', severity: 'warning', title: 'Formula symbols are not explained: v', why: 'w', suggestion: 's',
                  provenance: 'analysis', ref: { block_ids: ['b5'], page: 2, heading: 'Kinetic' }, status: 'open' }] },
            recommendations: [{ id: 'r1', title: 'Explain every symbol', why: 'w', provenance: 'analysis', status: 'pending' }],
            aadhi_ready: {
                title: { text: 'Energy', provenance: 'source' }, subject: { text: null }, audience: { text: null }, difficulty: { text: null },
                prerequisites: [], learning_objectives: [{ id: 'o1', text: 'Define energy.', provenance: 'source', accepted: true },
                    { id: 'o2', text: 'Explain kinetic energy', provenance: 'ai_suggestion', accepted: false }],
                sections: [
                    { id: 's1', title: 'Kinetic', title_provenance: 'source', included: true, subtopics: [{ id: 's1.1', title: 'Kinetic', definitions: [{ term: 'Kinetic energy', text: 'Kinetic energy is the energy of motion.', provenance: 'source', ref: { page: 2, heading: 'Kinetic' } }],
                        explanations: [], examples: [], formulas: [{ expression: 'KE = ½ m v²', undefined_variables: ['v'], unexplained_components: [], provenance: 'source', ref: {} }],
                        code: [], visual_opportunities: [], important_points: [], quiz_candidates: [] }] },
                    { id: 's2', title: 'Potential', title_provenance: 'source', included: true, subtopics: [] }],
                summary: [], assessment: []
            }
        },
        ...extra
    };
}

test('the session records edits and builds the payload the server validates', () => {
    const s = new S.AssistantSession(view());
    assert.equal(s.dirty, false);
    assert.ok(s.renameSection('s2', 'Stored energy'));
    assert.ok(s.moveSection('s2', -1));
    assert.ok(!s.moveSection('s2', -1)); // already first
    assert.ok(s.toggleSection('s1', false));
    assert.ok(s.editObjective(0, 'Define energy and work.'));
    assert.ok(s.acceptObjective(1, true));
    assert.ok(s.addObjective('Compare the two forms'));
    assert.ok(!s.addObjective('   '));
    assert.ok(s.addPrerequisite('Basic algebra'));
    assert.ok(s.setIssue('i1', 'resolved'));
    assert.ok(!s.setIssue('i9', 'resolved'));
    assert.ok(s.setRecommendation('r1', 'accepted'));
    assert.ok(s.setField('audience', 'Grade 9'));
    assert.equal(s.dirty, true);
    assert.deepEqual(s.edits(), {
        audience: 'Grade 9',
        sections: [{ id: 's2', title: 'Stored energy', included: true }, { id: 's1', title: 'Kinetic', included: false }],
        objectives: [{ id: 'o1', text: 'Define energy and work.', accepted: true }, { id: 'o2', text: 'Explain kinetic energy', accepted: true },
            { id: null, text: 'Compare the two forms', accepted: true }],
        prerequisites: [{ id: null, text: 'Basic algebra', accepted: true }],
        issues: { i1: 'resolved' },
        recommendations: { r1: 'accepted' }
    });
});

test('labels: provenance badges, references, AI status, counts', () => {
    // Phase 21: plain words in sentence case, never all caps
    assert.deepEqual(S.badge('source'), { text: 'From your document', cls: 'source' });
    assert.deepEqual(S.badge('ai_suggestion'), { text: 'Suggested by AI', cls: 'ai' });
    assert.deepEqual(S.badge('ai_suggestion', 'issue'), { text: 'Found by AI', cls: 'ai' });
    assert.deepEqual(S.badge('analysis'), { text: 'Suggested by Aadhi', cls: 'analysis' });
    assert.deepEqual(S.badge('analysis', 'issue'), { text: 'Found by Aadhi', cls: 'analysis' });
    assert.deepEqual(S.badge('user'), { text: 'Yours', cls: 'user' });
    assert.deepEqual(S.badge('user_edited'), { text: 'Edited', cls: 'user' });
    for (const prov of ['source', 'ai_suggestion', 'analysis', 'user', 'user_edited']) {
        for (const kind of ['item', 'issue']) assert.notEqual(S.badge(prov, kind).text, S.badge(prov, kind).text.toUpperCase(), `${prov} ${kind}`);
    }
    assert.equal(S.refLabel({ page: 4, heading: "Newton's Second Law" }), 'p. 4 · “Newton\'s Second Law”');
    assert.equal(S.refLabel({ heading: 'Intro', paragraph: 3 }), '“Intro” · paragraph 3');
    assert.match(S.aiStatusText(view({ analysis: { ...view().analysis, ai: { status: 'running', chunks_done: 1, chunks_total: 3 } } })), /1 of 3/);
    // Phase 21: plain words; the provider, model and the server's reason only in debug (?visualDebug)
    const ai = status => view({ analysis: { ...view().analysis, ai: status } });
    const completed = ai({ status: 'completed', provider: 'gemini', model: 'm', unverified_items_dropped: 2 });
    assert.match(S.aiStatusText(completed), /^Analysed with AI — 2 AI finding/);
    assert.doesNotMatch(S.aiStatusText(completed), /gemini/);
    assert.match(S.aiStatusText(completed, { debug: true }), /^Analysed with AI \(gemini, m\) — 2 AI finding/);
    const unavailable = ai({ status: 'unavailable', reason: 'no Gemini API key is configured on this server' });
    assert.equal(S.aiStatusText(unavailable), 'Read without AI: headings, formulas, code and figures are found; definitions and examples only in English text.');
    assert.doesNotMatch(S.aiStatusText(unavailable), /Gemini|API key|no AI model|server/);
    assert.match(S.aiStatusText(unavailable, { debug: true }), /^Read without AI \(no Gemini API key is configured on this server\): headings, formulas/);
    const failed = ai({ status: 'failed', message: 'ProviderError: 500 upstream' });
    assert.equal(S.aiStatusText(failed), "The AI analysis did not finish; the analysis by the document's structure is shown.");
    assert.match(S.aiStatusText(failed, { debug: true }), /\(ProviderError: 500 upstream\)\.$/);
    assert.match(S.aiStatusText(ai({ status: 'cancelled', message: 'cancelled' })), /^You stopped the AI analysis/);
    assert.equal(S.aiStatusText(view()), 'Analysed by its structure.');
    assert.doesNotMatch(S.aiStatusText(view()), /rule-based/);
    assert.deepEqual(S.counts(view().analysis.aadhi_ready.sections[0].subtopics[0]), ['1 definition', '1 formula']);
});

test('the API client sends the documented requests and reports server errors', async () => {
    const calls = [];
    const api = new S.DocumentsApi({ fetch: async (url, init) => {
        calls.push([url, init.method || 'GET', init.body ? JSON.parse(init.body) : null]);
        if (url.endsWith('/lesson-input')) return { ok: false, status: 409, json: async () => ({ detail: 'A newer version was uploaded.' }) };
        return { ok: true, status: 200, json: async () => ({ document: { document_id: 'd' } }) };
    } });
    await api.submit({ source_type: 'txt', blocks: [{ type: 'paragraph', text: 'x' }] }, 'n.txt');
    await api.analyze('d', { provider: 'gemini' });
    await api.saveEdits('a', { issues: {} });
    await assert.rejects(api.lessonInput('a'), err => err.status === 409 && /newer version/.test(err.message));
    assert.deepEqual(calls.map(c => [c[0], c[1]]), [['/api/source-documents', 'POST'], ['/api/source-documents/d/analyze', 'POST'],
        ['/api/source-analyses/a/edits', 'PUT'], ['/api/source-analyses/a/lesson-input', 'GET']]);
    assert.equal(calls[0][2].extractor_version, S.EXTRACTOR_VERSION);
    assert.equal(calls[0][2].file_name, 'n.txt');
    assert.deepEqual(calls[1][2], { mode: 'auto', provider: 'gemini' });
});

// ---- panel (a small DOM stand-in) ---------------------------------------------------------------------------------

class Node {
    constructor(doc, tag) { this.doc = doc; this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this._text = ''; this.value = ''; this.checked = false;
        const names = new Set(); this.classList = { add: n => names.add(n), remove: n => names.delete(n), contains: n => names.has(n) }; }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'class') String(v).split(/\s+/).forEach(n => this.classList.add(n)); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    removeEventListener() {}
    appendChild(c) { this.children.push(c); c.parent = this; return c; }
    get textContent() { return this.tag === '#text' ? this._text : this.children.map(c => c.textContent).join(''); }
    set textContent(v) { this.children = []; if (this.tag === '#text') this._text = String(v); else if (v) this.children.push(Object.assign(new Node(this.doc, '#text'), { _text: String(v) })); }
    all() { return this.children.flatMap(c => [c, ...c.all()]); }
    find(fn) { return this.all().find(fn); }
    byClass(name) { return this.all().filter(n => n.classList.contains(name)); }
    fire(type, props = {}) { (this.listeners[type] || []).forEach(fn => fn({ type, target: this, ...props })); }
}
function fakeDoc() {
    const doc = { createElement: tag => new Node(doc, tag), createTextNode: t => Object.assign(new Node(doc, '#text'), { _text: t }),
        addEventListener() {}, removeEventListener() {} };
    doc.body = new Node(doc, 'body');
    return doc;
}

test('the panel analyses pasted text, follows a running AI analysis, saves edits and hands the prepared source over', async () => {
    const running = view({ status: 'running', analysis: { ...view().analysis, ai: { status: 'running', chunks_done: 0, chunks_total: 2 } } });
    const calls = [];
    let polls = 0;
    const api = {
        submit: async (extraction, name) => { calls.push(['submit', extraction.source_type, name, extraction.blocks.length]); return { document: { document_id: 'd'.repeat(32) } }; },
        analyze: async (id, options) => { calls.push(['analyze', options]); return running; },
        get: async () => { polls++; return polls < 2 ? running : view(); },
        saveEdits: async (id, edits) => { calls.push(['save', edits.sections.map(s => s.title)]); return view(); },
        lessonInput: async () => ({ text: 'AADHI-READY SOURCE ...', document_id: 'd'.repeat(32), analysis_id: 'a'.repeat(32), title: 'Energy' })
    };
    const timers = [];
    let used = null;
    const doc = fakeDoc();
    const panel = new S.DocumentAssistantPanel({ doc, api, extract: async () => { throw new Error('not used'); }, options: () => ({ provider: 'gemini' }),
        onUse: prepared => { used = prepared; }, setTimeout: fn => timers.push(fn) });
    panel.open();
    assert.ok(panel.root.classList.contains('open'));
    assert.ok(panel.body.byClass('doc-paste').length === 1); // empty state: paste box
    await panel.analyzeText('Title: Energy\n\n## Kinetic\nKinetic energy is the energy of motion.');
    assert.deepEqual(calls[0], ['submit', 'text', 'Pasted text', 3]); // title, heading, paragraph
    assert.deepEqual(calls[1], ['analyze', { provider: 'gemini' }]);
    assert.match(panel.body.textContent, /AI analysis running \(0 of 2 parts\)/);
    assert.equal(panel.body.byClass('doc-use')[0].attrs.disabled, ''); // not usable while running
    await timers.shift()(); // poll: still running
    await timers.shift()(); // poll: done
    const text = panel.body.textContent;
    // (Phase 21: the cards in plain words)
    for (const part of ['Document overview', 'Learning objectives', 'Content structure', 'Things to check (1)', 'Suggested improvements', 'Sections to teach']) {
        assert.ok(text.includes(part), part);
    }
    assert.doesNotMatch(text, /Content issues|Recommended restructuring|Aadhi-ready structure|Use for Lesson Generation/);
    assert.ok(text.includes('From your document') && text.includes('Suggested by AI') && text.includes('Found by Aadhi') && text.includes('Suggested by Aadhi'));
    assert.ok(text.includes('KE = ½ m v²'));
    assert.ok(text.includes('p. 2 · “Kinetic”'));
    // Edit a section title, then use it: the edits are saved first, then the prepared source is handed over
    const titleInput = panel.body.all().find(n => n.tag === 'input' && n.attrs['aria-label'] === 'Section title' && n.value === 'Kinetic');
    titleInput.value = 'Motion energy';
    titleInput.fire('change');
    assert.equal(panel.session.dirty, true);
    await panel.use();
    assert.deepEqual(calls.find(c => c[0] === 'save'), ['save', ['Motion energy', 'Potential']]);
    assert.deepEqual(used, { text: 'AADHI-READY SOURCE ...', documentId: 'd'.repeat(32), analysisId: 'a'.repeat(32), title: 'Energy', fileName: 'x.txt' });
    assert.ok(!panel.root.classList.contains('open'));
});

test('Phase 21: the overview speaks plain words; the version, raw kind and server reason only with debug', () => {
    const base = view();
    const sections = base.analysis.aadhi_ready.sections.map((sec, i) => ({ ...sec, ref: i === 0 ? { page: 3, heading: 'Energy', paragraph: null } : { heading: 'Energy' } }));
    const shown = (debug, extra = {}) => {
        const doc = fakeDoc();
        const panel = new S.DocumentAssistantPanel({ doc, api: {}, extract: async () => ({}), setTimeout: () => {}, debug });
        panel.build();
        panel.show(view({ ...extra, analysis: { ...base.analysis, aadhi_ready: { ...base.analysis.aadhi_ready, sections },
            ai: { status: 'unavailable', reason: 'no Gemini API key is configured on this server' } } }));
        return panel;
    };
    const normal = shown(false);
    const text = normal.body.textContent;
    assert.ok(text.includes('x.txt (Text file)'), 'the kind in words, no version');
    assert.doesNotMatch(text, /version 1|Gemini|API key|Requires your input/);
    assert.ok(text.includes('Read without AI: headings, formulas, code and figures are found'));
    assert.ok(text.includes('Not found in the document. You can add it under “Sections to teach” below.'), 'the subject: where to add it');
    // a section's reference is its page, never the document's title it sits under (its own title is next to it)
    const refs = normal.body.byClass('doc-section-title').map(n => n.byClass('doc-ref').map(r => r.textContent).join(''));
    assert.deepEqual(refs, ['p. 3', '']);
    assert.ok(normal.body.byClass('doc-ref').some(r => r.textContent === 'p. 2 · “Kinetic”'), "an item's own reference is kept");
    assert.equal(normal.body.all().find(n => n.attrs['data-field'] === 'subject').attrs.placeholder, 'Add the subject');
    // debug (?visualDebug, a boolean or a function): the technical details as well
    const debug = shown(() => true).body.textContent;
    assert.ok(debug.includes('x.txt (txt, version 1)'));
    assert.ok(debug.includes('(no Gemini API key is configured on this server)'));
    // the subject the user typed in the Aadhi-ready structure ("Sections to teach") is what the overview shows
    const typed = shown(false);
    typed.session.setField('subject', 'Physics');
    typed.render();
    assert.ok(typed.body.textContent.includes('SubjectPhysics'));
});

test('Phase 21: the Document Assistant speaks to teachers: one verdict, things to check, sections to teach, a saved status', async () => {
    const make = (extra = {}, debug = false) => {
        const doc = fakeDoc();
        const panel = new S.DocumentAssistantPanel({ doc, api: { saveEdits: async () => view(extra) }, extract: async () => ({}), setTimeout: () => {}, debug });
        panel.build();
        panel.show(view(extra));
        return panel;
    };
    const panel = make();
    assert.equal(panel.panel.byClass('doc-flow')[0].textContent, 'Check how Aadhi read your document, then write the lesson.');
    // one verdict, never two side by side: the open warning keeps the document from being ready; its kind only in debug
    assert.deepEqual(panel.body.byClass('doc-verdict').map(n => n.textContent), ['Could be clearer — 1 note']);
    assert.ok(panel.body.byClass('doc-verdict')[0].classList.contains('doc-not-ready'));
    assert.equal(panel.body.byClass('doc-ready').length, 0);
    assert.doesNotMatch(panel.body.textContent, /Missing context|Ready for generation|Needs improvement/);
    const ready = make({ analysis: { ...view().analysis, readiness: { verdict: 'partially_structured', ready_for_generation: true, open_warnings: 0, open_info: 1 } } });
    assert.deepEqual(ready.body.byClass('doc-verdict').map(n => n.textContent), ['Ready to write the lesson']);
    assert.ok(ready.body.byClass('doc-verdict')[0].classList.contains('doc-ready'));
    assert.doesNotMatch(ready.body.textContent, /Partially structured/);
    assert.deepEqual(make({}, true).body.byClass('doc-verdict').map(n => n.textContent), ['Could be clearer — 1 note (Missing context)']);
    // things to check: "Done" is a toggle (pressed when done) and the count follows it
    const doneButton = () => panel.body.byClass('doc-issue')[0].byClass('doc-small')[0];
    assert.deepEqual([doneButton().textContent, doneButton().attrs['aria-pressed']], ['Done', 'false']);
    doneButton().fire('click');
    assert.equal(panel.session.issues.i1, 'resolved');
    assert.deepEqual([doneButton().textContent, doneButton().attrs['aria-pressed']], ['✓ Done', 'true']);
    assert.ok(panel.body.textContent.includes('Things to check (0)'));
    doneButton().fire('click');
    assert.equal(panel.session.issues.i1, 'open');
    assert.ok(panel.body.textContent.includes('Things to check (1)'));
    // sections to teach: each checkbox is named by its section; an unticked section is left out
    assert.ok(panel.body.textContent.includes('The lesson is written from these sections. Untick a section to leave it out.'));
    const boxes = () => panel.body.all().filter(n => n.tag === 'input' && n.attrs.type === 'checkbox' && n.parent.classList.contains('doc-structure-item'));
    assert.deepEqual(boxes().map(b => b.attrs['aria-label']), ['Teach “Kinetic”', 'Teach “Potential”']);
    const potential = boxes()[1];
    potential.checked = false;
    potential.fire('change');
    assert.deepEqual(panel.session.edits().sections.map(x => [x.id, x.included]), [['s1', true], ['s2', false]]);
    assert.ok(panel.body.find(n => n.attrs['data-section'] === 's2').classList.contains('doc-excluded'));
    assert.deepEqual(panel.body.byClass('doc-sub').map(n => n.textContent), ['1 part', '0 parts']);
    // nothing to save: "✓ Saved" is a status, not a disabled button; a change brings "Save changes" back
    const fresh = make();
    assert.equal(fresh.body.byClass('doc-save').length, 0);
    assert.deepEqual(fresh.body.byClass('doc-saved').map(n => [n.tag, n.textContent]), [['span', '✓ Saved']]);
    assert.equal(panel.body.byClass('doc-saved').length, 0);
    const save = panel.body.byClass('doc-save')[0];
    assert.equal(save.textContent, 'Save changes');
    assert.equal(save.attrs.disabled, undefined);
    await panel.save();
    assert.deepEqual(panel.body.byClass('doc-saved').map(n => n.textContent), ['✓ Saved']);
    assert.equal(panel.status.textContent, 'Your changes are saved.');
    assert.equal(panel.status.attrs['aria-live'], 'polite', 'the save is announced');
    assert.equal(fresh.body.byClass('doc-use')[0].textContent, 'Write the lesson from this');
});

test('the panel shows extraction and server errors, and a stale analysis cannot be used', async () => {
    const doc = fakeDoc();
    const panel = new S.DocumentAssistantPanel({ doc, api: { submit: async () => { throw Object.assign(new Error('The document has no readable content.'), { status: 422 }); } },
        extract: async () => ({ source_type: 'txt', blocks: [] }), setTimeout: () => {} });
    panel.open();
    await panel.analyzeFile({ name: 'empty.txt' });
    assert.equal(panel.state, 'error');
    assert.match(panel.body.textContent, /could not be analysed: The document has no readable content/);
    panel.show(view({ stale: true, stale_reason: 'A newer version (v2) of “x.txt” was uploaded; analyse it again.' }));
    assert.match(panel.body.textContent, /newer version \(v2\)/);
    assert.equal(panel.body.byClass('doc-use')[0].attrs.disabled, '');
});

test('Phase 21: the Document Assistant\'s errors are plain words with the file\'s safety; raw text only in the debug view', () => {
    const plainError = (err, what, debug = false) => S.DocumentAssistantPanel.prototype.plainError.call({ debugOn: () => debug }, err, what);
    const damaged = new Error("Can't find end of central directory : is this a zip file ? If it is, see https://stuk.github.io/jszip/");
    assert.equal(plainError(damaged), 'The file could not be read or prepared. Your original file is safe — try again.');
    assert.ok(plainError(damaged, '', true).includes('central directory'), 'the debug view keeps the reader\'s message');
    const server = Object.assign(new Error('Request failed (500)'), { status: 500 });
    assert.equal(plainError(server, 'Your changes could not be saved.'), 'Your changes could not be saved. Something went wrong on the server. Your original file is safe — try again.');
    const stale = Object.assign(new Error('The AI analysis is still running'), { status: 409 });
    assert.equal(plainError(stale), 'The AI analysis is still running. Your original file is safe — try again.', 'a plain server sentence is kept');
    assert.equal(plainError(Object.assign(new Error('x'), { status: 401 })), 'Please sign in again. Your original file is safe — try again.');
});
