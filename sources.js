/*
 * Source Document Formatting Assistant (Phase 11): prepares the source document before a lesson is generated.
 *
 *   PDF (pdf.js, already on the page) / DOCX (mammoth, already on the page) / TXT / pasted text
 *     -> blocks that keep the structure (title, headings, paragraphs, lists, tables, code, formulas, captions,
 *        images, page numbers)            blocksFromPdfPages / blocksFromHtml / blocksFromText
 *     -> POST /api/source-documents, POST /api/source-documents/{id}/analyze (source_documents.py)
 *     -> the Document Assistant panel: overview, learning objectives, content structure, content issues ("Things to
 *        check"), recommendations and the Aadhi-ready structure ("Sections to teach"), which the user can correct
 *        (saved as edits)
 *     -> "Write the lesson from this" (before Phase 21: "Use for Lesson Generation"): the prepared source goes to the
 *        existing lesson generation (the page's system prompt and /generate-script, unchanged)
 *
 * Every item shows where it comes from: "From your document", "Suggested by Aadhi" / "Found by Aadhi" (a rule-based
 * finding), "Suggested by AI" / "Found by AI" (from an AI model), and the user's own changes ("Yours", "Edited").
 * "Generate Video" without the assistant keeps working as before.
 *
 * Loaded as a classic <script> (window.AadhiSources) and as a CommonJS module by tests/sources.test.js.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.AadhiSources = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const EXTRACTOR_VERSION = 1;
    const MAX_FILE_MB = 20;
    const FORMULA_OPS = ['=', '→', '⇌', '->', '<->', '≈', '≤', '≥', '∝', '⟶'];
    const LIST_ITEM = /^\s*(?:[•●▪◦‣\-–*]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)])\s+/;
    const CAPTION = /^(?:fig(?:ure)?\.?|table|chart|diagram|graph)\s*\d+(?:\.\d+)?\s*[:.\-–—]/i;

    // ---- extraction -------------------------------------------------------------------------------------------

    function looksLikeFormula(text) {
        const t = String(text || '').trim();
        if (!t || t.length > 200 || t.includes('\n')) return false;
        if (!FORMULA_OPS.some(op => t.includes(op))) return false;
        const lowerWords = (t.match(/[A-Za-z]{4,}/g) || []).filter(w => w === w.toLowerCase());
        if (lowerWords.length > 3 || t.split(/\s+/).length > 16) return false;
        return /[A-Za-z0-9₀-₉]/.test(t);
    }

    function lineKind(text) {
        if (CAPTION.test(text) && text.length <= 300) return 'caption';
        if (looksLikeFormula(text)) return 'formula';
        if (LIST_ITEM.test(text)) return 'list_item';
        return 'paragraph';
    }

    const escapeHtml = text => String(text == null ? '' : text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    // A bullet is layout, not content (numbered items keep their number: the order may matter)
    const stripBullet = text => text.replace(/^\s*[•●▪◦‣\-–*]\s+/, '');

    // pages: [{page, items: [{str, x, y, fontSize, mono}]}] (from pdf.js getTextContent; y grows upwards)
    function blocksFromPdfPages(pages) {
        const lines = [];
        for (const p of pages) {
            const items = p.items.filter(i => i.str && i.str.trim()).slice().sort((a, b) => (b.y - a.y) || (a.x - b.x));
            let line = null;
            for (const item of items) {
                const size = item.fontSize || 10;
                if (line && Math.abs(item.y - line.y) < size * 0.5) {
                    const gap = item.x - line.endX;
                    line.text += (gap > size * 0.15 && !line.text.endsWith(' ') && !item.str.startsWith(' ') ? ' ' : '') + item.str;
                    line.size = Math.max(line.size, size);
                    line.mono = line.mono && !!item.mono;
                    line.endX = item.x + (item.width || item.str.length * size * 0.5);
                } else {
                    line = { page: p.page, x: item.x, y: item.y, text: item.str, size, mono: !!item.mono, endX: item.x + (item.width || item.str.length * size * 0.5) };
                    lines.push(line);
                }
            }
        }
        lines.forEach(l => { l.text = l.text.replace(/\s+/g, ' ').trim(); });
        // Running headers/footers (the same short line on most pages) and bare page numbers are not content
        const pageCount = new Set(lines.map(l => l.page)).size;
        const repeats = {};
        lines.forEach(l => { if (l.text.length <= 60) (repeats[l.text] = repeats[l.text] || new Set()).add(l.page); });
        const content = lines.filter(l => l.text && !/^(?:page\s*)?\d+(?:\s*(?:of|\/)\s*\d+)?$/i.test(l.text)
            && !(pageCount >= 3 && repeats[l.text] && repeats[l.text].size >= Math.max(3, pageCount * 0.5)));
        // Body text: the font size that carries the most characters
        const bySize = {};
        content.forEach(l => { const k = Math.round(l.size * 2) / 2; bySize[k] = (bySize[k] || 0) + l.text.length; });
        const body = Number(Object.keys(bySize).sort((a, b) => bySize[b] - bySize[a])[0] || 10);
        const headingSizes = [...new Set(content.filter(l => l.size >= body * 1.15 && l.text.length <= 150).map(l => Math.round(l.size)))].sort((a, b) => b - a);
        const blocks = [];
        let prev = null;
        for (const l of content) {
            const isHeading = l.size >= body * 1.15 && l.text.length <= 150 && !/[.,;]$/.test(l.text);
            if (isHeading) {
                const level = Math.min(3, headingSizes.indexOf(Math.round(l.size)) + 1 || 3);
                const last = blocks[blocks.length - 1];
                if (last && last.type === 'heading' && prev && prev.page === l.page && last.level === level && prev.y - l.y < l.size * 1.6) {
                    last.text += ' ' + l.text; // a heading wrapped over two lines
                } else {
                    blocks.push({ type: 'heading', level, text: l.text, page: l.page });
                }
                prev = l;
                continue;
            }
            if (l.mono) {
                const last = blocks[blocks.length - 1];
                if (last && last.type === 'code' && last.page === l.page) last.lines.push(l);
                else blocks.push({ type: 'code', text: '', page: l.page, lines: [l] });
                prev = l;
                continue;
            }
            const kind = lineKind(l.text);
            const last = blocks[blocks.length - 1];
            // A line continues the paragraph above when it follows closely on the same page; a sentence end followed by
            // paragraph spacing starts a new one
            const gap = prev ? prev.y - l.y : Infinity;
            const joins = kind === 'paragraph' && last && (last.type === 'paragraph' || last.type === 'list_item') && prev && prev.page === l.page
                && !prev.mono && gap <= Math.max(l.size, prev.size) * 1.6;
            if (joins && !(/[.!?]$/.test(last.text) && gap > l.size * 1.35)) {
                last.text += ' ' + l.text;
            } else {
                blocks.push({ type: kind, text: kind === 'list_item' ? stripBullet(l.text) : l.text, page: l.page });
            }
            prev = l;
        }
        // Code keeps its indentation: pdf.js gives positions, not spaces (a monospace character is about 0.6 em wide)
        for (const b of blocks) {
            if (!b.lines) continue;
            const left = Math.min(...b.lines.map(l => l.x));
            b.text = b.lines.map(l => ' '.repeat(Math.max(0, Math.round((l.x - left) / (l.size * 0.6)))) + l.text).join('\n');
            delete b.lines;
        }
        return blocks;
    }

    const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
    function decode(text) {
        return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
            if (e[0] === '#') return String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
            return ENTITIES[e.toLowerCase()] !== undefined ? ENTITIES[e.toLowerCase()] : m;
        });
    }

    // mammoth's HTML (convertToHtml with MAMMOTH_STYLE_MAP) -> blocks. A small tag walker: no DOM needed.
    function blocksFromHtml(html) {
        const blocks = [];
        const BLOCKS = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'pre']);
        const stack = [];
        let buffer = '';
        let table = null;
        let cell = null;
        const flush = tag => {
            const text = tag && tag.name === 'pre' ? buffer.replace(/^\n+|\s+$/g, '') : buffer.replace(/\s+/g, ' ').trim();
            buffer = '';
            if (!tag || !text) return;
            const cls = tag.cls;
            if (/^h[1-6]$/.test(tag.name)) {
                if (/\btitle\b/.test(cls)) blocks.push({ type: 'title', text });
                else blocks.push({ type: 'heading', level: Number(tag.name[1]), text });
            } else if (tag.name === 'pre' || /\bcode\b/.test(cls)) blocks.push({ type: 'code', text });
            else if (/\bcaption\b/.test(cls) || (CAPTION.test(text) && text.length <= 300)) blocks.push({ type: 'caption', text });
            else if (tag.name === 'li') blocks.push({ type: 'list_item', text });
            else if (looksLikeFormula(text)) blocks.push({ type: 'formula', text });
            else blocks.push({ type: 'paragraph', text });
        };
        const current = () => { for (let i = stack.length - 1; i >= 0; i--) if (BLOCKS.has(stack[i].name)) return stack[i]; return null; };
        const re = /<(\/?)([a-zA-Z0-9]+)([^>]*)>|([^<]+)/g;
        let m;
        while ((m = re.exec(html))) {
            if (m[4] !== undefined) {
                const text = decode(m[4]);
                if (cell !== null) cell += text;
                else buffer += text;
                continue;
            }
            const closing = m[1] === '/';
            const name = m[2].toLowerCase();
            const attrs = m[3] || '';
            const cls = (attrs.match(/class\s*=\s*"([^"]*)"/i) || [])[1] || '';
            if (name === 'br') { if (cell !== null) cell += ' '; else buffer += (current() && current().name === 'pre') ? '\n' : ' '; continue; }
            if (name === 'img') {
                if (table) continue;
                flush(current());
                const alt = decode((attrs.match(/alt\s*=\s*"([^"]*)"/i) || [])[1] || '');
                blocks.push({ type: 'image', alt, text: alt });
                continue;
            }
            if (name === 'table') {
                if (!closing) { flush(current()); table = []; }
                else if (table) { blocks.push({ type: 'table', rows: table.filter(r => r.some(c => c)), text: '' }); table = null; }
                continue;
            }
            if (table) {
                if (name === 'tr' && !closing) table.push([]);
                else if ((name === 'td' || name === 'th') && !closing) cell = '';
                else if ((name === 'td' || name === 'th') && closing && cell !== null) {
                    if (!table.length) table.push([]);
                    table[table.length - 1].push(cell.replace(/\s+/g, ' ').trim());
                    cell = null;
                }
                continue;
            }
            if (!BLOCKS.has(name)) continue;
            if (!closing) {
                flush(current()); // text of an outer block before a nested one (a list item holding a list)
                stack.push({ name, cls });
            } else {
                const idx = stack.map(s => s.name).lastIndexOf(name);
                if (idx < 0) continue;
                flush(stack[idx]);
                stack.splice(idx);
            }
        }
        flush(current());
        return blocks;
    }

    // Plain text (TXT upload or pasted): markdown-style and numbered headings, lists, ``` code, formulas, pages
    function blocksFromText(text) {
        const blocks = [];
        const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
        let page = null;
        let para = null;
        let code = null;
        const end = () => { para = null; };
        for (let i = 0; i < lines.length; i++) {
            const raw = lines[i];
            const line = raw.trim();
            const next = (lines[i + 1] || '').trim();
            if (code) {
                if (/^```/.test(line)) { blocks.push({ type: 'code', text: code.lines.join('\n'), lang: code.lang || undefined, page }); code = null; }
                else code.lines.push(raw.replace(/\s+$/, ''));
                continue;
            }
            const pageMark = line.match(/^-{2,}\s*page\s+(\d+)\s*-{2,}$/i);
            if (pageMark) { page = Number(pageMark[1]); end(); continue; }
            if (/^```/.test(line)) { end(); code = { lang: line.slice(3).trim().toLowerCase(), lines: [] }; continue; }
            if (!line) { end(); continue; }
            if (/^(=){3,}$|^(-){3,}$/.test(line)) continue; // an underline already used below
            const md = line.match(/^(#{1,6})\s+(.*)$/);
            if (md) { end(); blocks.push({ type: 'heading', level: md[1].length, text: md[2].trim(), page }); continue; }
            if (/^={3,}$/.test(next) || /^-{3,}$/.test(next)) { end(); blocks.push({ type: 'heading', level: next[0] === '=' ? 1 : 2, text: line, page }); i++; continue; }
            const titled = !blocks.length && line.match(/^title\s*:\s*(.+)$/i);
            if (titled) { end(); blocks.push({ type: 'title', text: titled[1].trim(), page }); continue; }
            const numbered = line.match(/^(\d+(?:\.\d+){0,3})\.?\s+(\S.{0,78})$/);
            if (numbered && !/[.,;:!?]$/.test(line) && numbered[2].split(/\s+/).length <= 10 && (!next || !LIST_ITEM.test(next) || /^\d+(\.\d+)+/.test(next))) {
                end(); blocks.push({ type: 'heading', level: Math.min(4, numbered[1].split('.').filter(Boolean).length + 1), text: line, page }); continue;
            }
            if (line.length <= 80 && /[A-Z]/.test(line) && line === line.toUpperCase() && /[A-Z]{2}/.test(line) && !looksLikeFormula(line)) {
                end(); blocks.push({ type: 'heading', level: 2, text: line, page }); continue;
            }
            if (!blocks.length && line.length <= 100 && !/[.,;:!?]$/.test(line) && next === '') {
                end(); blocks.push({ type: 'heading', level: 1, text: line, page }); continue;
            }
            if (/^( {4}|\t)/.test(raw) && !para) {
                const last = blocks[blocks.length - 1];
                if (last && last.type === 'code' && last.indented) last.text += '\n' + raw.replace(/^( {4}|\t)/, '');
                else blocks.push({ type: 'code', text: raw.replace(/^( {4}|\t)/, ''), page, indented: true });
                continue;
            }
            const kind = lineKind(line);
            if (kind !== 'paragraph') { end(); blocks.push({ type: kind, text: kind === 'list_item' ? stripBullet(line) : line, page }); continue; }
            if (para) para.text += ' ' + line;
            else { para = { type: 'paragraph', text: line, page }; blocks.push(para); }
        }
        if (code) blocks.push({ type: 'code', text: code.lines.join('\n'), page });
        return blocks.map(b => { const { indented, ...rest } = b; return rest; });
    }

    // Word styles mammoth should keep: the title, captions and code (the rest is its default mapping)
    const MAMMOTH_STYLE_MAP = [
        "p[style-name='Title'] => h1.title:fresh",
        "p[style-name='Subtitle'] => h2:fresh",
        "p[style-name='Caption'] => p.caption:fresh",
        "p[style-name='Code'] => pre:separator('\\n')",
        "p[style-name='Source Code'] => pre:separator('\\n')",
        "p[style-name='HTML Preformatted'] => pre:separator('\\n')"
    ];

    function fileKind(file) {
        const name = (file && file.name || '').toLowerCase();
        const type = (file && file.type) || '';
        if (type === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
        if (name.endsWith('.docx') || type.includes('wordprocessingml')) return 'docx';
        if (name.endsWith('.txt') || name.endsWith('.md') || type === 'text/plain' || type === 'text/markdown') return 'txt';
        if (name.endsWith('.json') || type === 'application/json') return 'json';
        return null;
    }

    async function sha256Hex(buffer, cryptoImpl) {
        const subtle = cryptoImpl && cryptoImpl.subtle;
        if (!subtle) return null;
        const digest = await subtle.digest('SHA-256', buffer);
        return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
    }

    // The file as blocks: {source_type, blocks, page_count, file_sha256}. deps: {pdfjsLib, mammoth, crypto}
    async function extractFile(file, deps, progress = () => {}) {
        const kind = fileKind(file);
        if (!kind || kind === 'json') throw new Error('Choose a PDF, Word (.docx) or text (.txt) document to analyse.');
        if (file.size > MAX_FILE_MB * 1024 * 1024) throw new Error(`The file is larger than ${MAX_FILE_MB} MB.`);
        const buffer = await file.arrayBuffer();
        const hash = await sha256Hex(buffer, deps.crypto);
        if (kind === 'pdf') {
            const pdf = await deps.pdfjsLib.getDocument(new Uint8Array(buffer.slice(0))).promise;
            const pages = [];
            for (let i = 1; i <= pdf.numPages; i++) {
                progress(`Reading page ${i} of ${pdf.numPages}`);
                const page = await pdf.getPage(i);
                const content = await page.getTextContent();
                const styles = content.styles || {};
                pages.push({ page: i, items: content.items.map(it => ({
                    str: it.str, x: it.transform[4], y: it.transform[5], width: it.width,
                    fontSize: Math.hypot(it.transform[0], it.transform[1]) || it.height || 10,
                    mono: ((styles[it.fontName] || {}).fontFamily || '') === 'monospace'
                })) });
            }
            return { source_type: 'pdf', blocks: blocksFromPdfPages(pages), page_count: pdf.numPages, file_sha256: hash };
        }
        if (kind === 'docx') {
            progress('Reading the Word document');
            const result = await deps.mammoth.convertToHtml({ arrayBuffer: buffer }, { styleMap: MAMMOTH_STYLE_MAP });
            return { source_type: 'docx', blocks: blocksFromHtml(result.value), page_count: null, file_sha256: hash };
        }
        progress('Reading the text');
        return { source_type: 'txt', blocks: blocksFromText(new TextDecoder('utf-8').decode(buffer)), page_count: null, file_sha256: hash };
    }

    // ---- API ------------------------------------------------------------------------------------------------------

    class DocumentsApi {
        constructor({ fetch }) { this.fetch = fetch; }

        async call(url, init = {}) {
            const res = await this.fetch(url, { ...init, headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(init.headers || {}) } });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const err = new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
                err.status = res.status;
                throw err;
            }
            return data;
        }

        submit(extraction, fileName) {
            return this.call('/api/source-documents', { method: 'POST', body: JSON.stringify({ file_name: fileName, extractor_version: EXTRACTOR_VERSION, ...extraction }) });
        }
        analyze(documentId, options = {}) {
            return this.call(`/api/source-documents/${documentId}/analyze`, { method: 'POST', body: JSON.stringify({ mode: 'auto', ...options }) });
        }
        get(analysisId) { return this.call(`/api/source-analyses/${analysisId}`); }
        saveEdits(analysisId, edits) { return this.call(`/api/source-analyses/${analysisId}/edits`, { method: 'PUT', body: JSON.stringify(edits) }); }
        lessonInput(analysisId) { return this.call(`/api/source-analyses/${analysisId}/lesson-input`); }
        cancel(analysisId) { return this.call(`/api/source-analyses/${analysisId}/cancel`, { method: 'POST' }); }
    }

    // ---- editing (what the user changes, before it is saved) -------------------------------------------------------

    const clone = value => JSON.parse(JSON.stringify(value));

    class AssistantSession {
        constructor(view = null) { this.load(view); }

        load(view) {
            this.view = view;
            this.dirty = false;
            if (!view) return;
            const a = view.analysis;
            const ready = a.aadhi_ready;
            this.fields = {};
            for (const key of ['title', 'subject', 'audience', 'difficulty']) this.fields[key] = (ready[key] && ready[key].text) || '';
            this.sections = ready.sections.map(s => ({ id: s.id, title: s.title || '', included: s.included !== false, source: s }));
            this.objectives = ready.learning_objectives.map(o => ({ id: o.id, text: o.text, accepted: o.accepted !== false, provenance: o.provenance }));
            this.prerequisites = ready.prerequisites.map(p => ({ id: p.id, text: p.text, accepted: p.accepted !== false, provenance: p.provenance }));
            this.issues = Object.fromEntries(a.quality.issues.map(i => [i.id, i.status]));
            this.recommendations = Object.fromEntries(a.recommendations.map(r => [r.id, r.status]));
        }

        change(fn) { fn(); this.dirty = true; return true; }
        setField(key, text) { return this.change(() => { this.fields[key] = text; }); }
        renameSection(id, title) { const s = this.sections.find(x => x.id === id); return !!s && this.change(() => { s.title = title; }); }
        toggleSection(id, included) { const s = this.sections.find(x => x.id === id); return !!s && this.change(() => { s.included = included; }); }
        moveSection(id, delta) {
            const i = this.sections.findIndex(x => x.id === id);
            const j = i + delta;
            if (i < 0 || j < 0 || j >= this.sections.length) return false;
            return this.change(() => { const [s] = this.sections.splice(i, 1); this.sections.splice(j, 0, s); });
        }
        editObjective(index, text) { const o = this.objectives[index]; return !!o && this.change(() => { o.text = text; }); }
        acceptObjective(index, accepted) { const o = this.objectives[index]; return !!o && this.change(() => { o.accepted = accepted; }); }
        addObjective(text) { return !!String(text || '').trim() && this.change(() => { this.objectives.push({ id: null, text: text.trim(), accepted: true, provenance: 'user' }); }); }
        removeObjective(index) { return index in this.objectives && this.change(() => { this.objectives.splice(index, 1); }); }
        editPrerequisite(index, text) { const p = this.prerequisites[index]; return !!p && this.change(() => { p.text = text; }); }
        acceptPrerequisite(index, accepted) { const p = this.prerequisites[index]; return !!p && this.change(() => { p.accepted = accepted; }); }
        addPrerequisite(text) { return !!String(text || '').trim() && this.change(() => { this.prerequisites.push({ id: null, text: text.trim(), accepted: true, provenance: 'user' }); }); }
        removePrerequisite(index) { return index in this.prerequisites && this.change(() => { this.prerequisites.splice(index, 1); }); }
        setIssue(id, status) { return id in this.issues && this.change(() => { this.issues[id] = status; }); }
        setRecommendation(id, status) { return id in this.recommendations && this.change(() => { this.recommendations[id] = status; }); }

        // The edits the server keeps (PUT /api/source-analyses/{id}/edits)
        edits() {
            const out = {};
            const ready = this.view.analysis.aadhi_ready;
            for (const [key, text] of Object.entries(this.fields)) {
                const before = (ready[key] && ready[key].text) || '';
                if (text.trim() && text.trim() !== before) out[key] = text.trim();
            }
            out.sections = this.sections.map(s => ({ id: s.id, title: s.title.trim() || null, included: s.included }));
            out.objectives = this.objectives.filter(o => o.text.trim()).map(o => ({ id: o.id, text: o.text.trim(), accepted: o.accepted }));
            out.prerequisites = this.prerequisites.filter(p => p.text.trim()).map(p => ({ id: p.id, text: p.text.trim(), accepted: p.accepted }));
            out.issues = { ...this.issues };
            out.recommendations = { ...this.recommendations };
            return out;
        }
    }

    // ---- labels -----------------------------------------------------------------------------------------------------

    const VERDICTS = {
        well_structured: 'Well structured', partially_structured: 'Partially structured', needs_reorganization: 'Needs reorganization',
        missing_context: 'Missing context', ambiguous: 'Ambiguous in places', incomplete: 'Incomplete'
    };

    // Phase 21: plain words in sentence case (an issue is found, anything else is suggested)
    function badge(provenance, kind = 'item') {
        if (provenance === 'source') return { text: 'From your document', cls: 'source' };
        if (provenance === 'ai_suggestion') return { text: kind === 'issue' ? 'Found by AI' : 'Suggested by AI', cls: 'ai' };
        if (provenance === 'user') return { text: 'Yours', cls: 'user' };
        if (provenance === 'user_edited') return { text: 'Edited', cls: 'user' };
        return { text: kind === 'issue' ? 'Found by Aadhi' : 'Suggested by Aadhi', cls: 'analysis' };
    }

    function refLabel(ref) {
        if (!ref) return '';
        const parts = [];
        if (ref.page) parts.push(`p. ${ref.page}`);
        if (ref.heading) parts.push(`“${ref.heading}”`);
        if (!ref.page && ref.paragraph) parts.push(`paragraph ${ref.paragraph}`);
        return parts.join(' · ');
    }

    // How the document was analysed, in plain words. options.debug (the page's ?visualDebug): the provider and model, the
    // server's reason and its raw message too.
    function aiStatusText(view, { debug = false } = {}) {
        const ai = view.analysis.ai || {};
        const detail = text => (debug && text ? ` (${text})` : '');
        if (ai.status === 'running') return `AI analysis running (${ai.chunks_done || 0} of ${ai.chunks_total || '?'} parts)…`;
        if (ai.status === 'completed') {
            return `Analysed with AI${detail([ai.provider, ai.model].filter(Boolean).join(', '))}`
                + (ai.unverified_items_dropped ? ` — ${ai.unverified_items_dropped} AI finding(s) left out because they could not be traced to the document` : '');
        }
        if (ai.status === 'cancelled') return `You stopped the AI analysis; the analysis by the document's structure is shown${detail(ai.message)}.`;
        if (ai.status === 'failed') return `The AI analysis did not finish; the analysis by the document's structure is shown${detail(ai.message)}.`;
        if (ai.status === 'unavailable') {
            return `Read without AI${detail(ai.reason)}: headings, formulas, code and figures are found; definitions and examples only in English text.`;
        }
        return 'Analysed by its structure.';
    }

    // The document's kind in words (the server's source_type)
    const SOURCE_KINDS = { pdf: 'PDF', docx: 'Word document', txt: 'Text file', text: 'Pasted text' };

    function counts(st) {
        return [['definitions', 'definition'], ['explanations', 'explanation'], ['examples', 'example'], ['formulas', 'formula'], ['code', 'code example'],
            ['visual_opportunities', 'visual idea'], ['important_points', 'important point'], ['quiz_candidates', 'quiz idea']]
            .filter(([k]) => (st[k] || []).length).map(([k, label]) => `${st[k].length} ${label}${st[k].length > 1 ? 's' : ''}`);
    }

    // ---- panel --------------------------------------------------------------------------------------------------------

    function el(doc, tag, attrs = {}, ...children) {
        const node = doc.createElement(tag);
        for (const [key, value] of Object.entries(attrs || {})) {
            if (value === undefined || value === null || value === false) continue;
            if (key === 'text') node.textContent = value;
            else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
            else if (key === 'value') node.value = value;
            else if (key === 'checked') node.checked = !!value;
            else node.setAttribute(key, value === true ? '' : value);
        }
        for (const child of children.flat()) {
            if (child === null || child === undefined || child === false) continue;
            node.appendChild(typeof child === 'string' ? doc.createTextNode(child) : child);
        }
        return node;
    }

    class DocumentAssistantPanel {
        // deps: {doc, api, extract(file, progress), options() -> {provider, model}, onUse({text, documentId, analysisId, title, fileName}), setTimeout}
        // Phase 21: debug (boolean or () => boolean; the page's ?visualDebug): the provider, model, server reasons and the
        // document's version are shown too; otherwise plain words only
        constructor({ doc, api, extract, options = () => ({}), onUse = () => {}, setTimeout: timer = (fn, ms) => setTimeout(fn, ms), debug = false }) {
            this.doc = doc;
            this.api = api;
            this.extract = extract;
            this.options = options;
            this.onUse = onUse;
            this.timer = timer;
            this.debug = debug;
            this.session = new AssistantSession();
            this.state = 'empty';
            this.error = null;
            this.progress = '';
            this.fileName = null;
            this.pollToken = 0;
        }

        debugOn() { return !!(typeof this.debug === 'function' ? this.debug() : this.debug); }

        // Phase 21: an error in plain words — what happened, that the original file is safe, what to do; the raw text (a
        // status code, a reader's exception such as a damaged .docx's) only in the debug view
        plainError(e, what = '') {
            const status = e && typeof e.status === 'number' ? e.status : null;
            const raw = String((e && e.message) || e || '').replace(/\s+/g, ' ').trim();
            const plain = raw.length > 0 && raw.length <= 200 && !/https?:|[{}<>]|\bError\b|Request failed|exception|traceback|zip file|\(\d{3}\)/i.test(raw);
            let reason = '';
            if (status === 401) reason = 'Please sign in again.';
            else if (status === 413) reason = 'The document is too large to prepare.';
            else if (status === 0 || /failed to fetch|network/i.test(raw)) reason = 'The server could not be reached.';
            else if (status !== null && status >= 500) reason = 'Something went wrong on the server.';
            else if (plain) reason = /[.!?]$/.test(raw) ? raw : `${raw}.`;
            else reason = 'The file could not be read or prepared.';
            const text = [what, reason, 'Your original file is safe — try again.'].filter(Boolean).join(' ');
            return this.debugOn() && raw && !text.includes(raw) ? `${text} (${raw})` : text;
        }

        build() {
            if (this.root) return;
            const h = (...a) => el(this.doc, ...a);
            this.body = h('div', { class: 'doc-body' });
            this.status = h('div', { class: 'asset-status doc-status', role: 'status', 'aria-live': 'polite' });
            this.panel = h('div', { class: 'asset-panel doc-panel', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'doc-panel-title' },
                h('div', { class: 'asset-head' },
                    h('h2', { id: 'doc-panel-title', text: 'Document Assistant' }),
                    h('button', { type: 'button', class: 'export-close', 'aria-label': 'Close the document assistant', text: '✕', onclick: () => this.close() })),
                h('p', { class: 'asset-intro doc-flow', text: 'Check how Aadhi read your document, then write the lesson.' }),
                this.status, this.body);
            this.root = h('div', { class: 'asset-overlay doc-overlay', 'data-doc-runtime': true }, this.panel);
            this.root.addEventListener('keydown', e => e.stopPropagation());
            this.root.addEventListener('click', e => { if (e.target === this.root) this.close(); });
            this.onEscape = e => { if (e.key === 'Escape' && this.root.classList.contains('open')) this.close(); };
            this.doc.body.appendChild(this.root);
        }

        open(file = null) {
            this.build();
            this.root.classList.add('open');
            this.doc.addEventListener('keydown', this.onEscape, true);
            if (file) this.analyzeFile(file);
            else this.render();
        }

        close() {
            if (!this.root) return;
            this.root.classList.remove('open');
            this.doc.removeEventListener('keydown', this.onEscape, true);
            this.pollToken++;
        }

        setStatus(text, kind = 'info') {
            this.status.textContent = text || '';
            this.status.setAttribute('data-kind', kind);
        }

        async analyzeFile(file) {
            this.fileName = file.name;
            await this.run(async () => this.api.submit(await this.extract(file, text => { this.progress = text; this.render(); }), file.name));
        }

        async analyzeText(text, name) {
            this.fileName = name || 'Pasted text';
            await this.run(async () => this.api.submit({ source_type: 'text', blocks: blocksFromText(text), page_count: null, file_sha256: null }, this.fileName));
        }

        async run(submit) {
            this.state = 'loading';
            this.error = null;
            this.progress = 'Extracting the document…';
            this.render();
            try {
                const stored = await submit();
                this.progress = 'Analysing the structure…';
                this.render();
                const view = await this.api.analyze(stored.document.document_id, this.options());
                this.show(view);
            } catch (e) {
                this.state = 'error';
                this.error = this.plainError(e);
                this.render();
            }
        }

        show(view) {
            this.session.load(view);
            this.state = 'result';
            this.render();
            if (view.status === 'running') this.poll(view.analysis_id);
        }

        poll(analysisId) {
            const token = ++this.pollToken;
            const tick = async () => {
                if (token !== this.pollToken) return;
                try {
                    const view = await this.api.get(analysisId);
                    if (token !== this.pollToken) return;
                    if (view.status === 'running') {
                        this.session.view.analysis.ai = view.analysis.ai;
                        this.render();
                        this.timer(tick, 1500);
                    } else {
                        this.show(view);
                    }
                } catch (e) {
                    this.setStatus(this.plainError(e, 'The analysis could not be followed.'), 'error');
                }
            };
            this.timer(tick, 1500);
        }

        async save() {
            const view = await this.api.saveEdits(this.session.view.analysis_id, this.session.edits());
            this.session.load(view);
            this.setStatus('Your changes are saved.', 'ok');
            this.render();
            return view;
        }

        async use() {
            try {
                if (this.session.dirty) await this.save();
                const input = await this.api.lessonInput(this.session.view.analysis_id);
                const fileName = this.session.view.document.file_name;
                this.close();
                this.onUse({ text: input.text, documentId: input.document_id, analysisId: input.analysis_id, title: input.title, fileName });
            } catch (e) {
                this.setStatus(this.plainError(e, 'The prepared source could not be used yet.'), 'error');
            }
        }

        render() {
            const h = (...a) => el(this.doc, ...a);
            const scroll = this.body.scrollTop || 0; // an edit re-renders the panel: the user stays where they were
            this.body.textContent = '';
            if (this.doc.defaultView && this.doc.defaultView.requestAnimationFrame) this.doc.defaultView.requestAnimationFrame(() => { this.body.scrollTop = scroll; });
            if (this.state === 'empty') {
                const area = h('textarea', { class: 'neon-input doc-paste', rows: 8, 'aria-label': 'Source text', placeholder: 'Paste the source text here, or choose a PDF, Word or text file on the start screen.' });
                this.body.appendChild(h('div', { class: 'doc-empty' },
                    h('p', { text: 'Aadhi reads your notes or document, shows how they are organised and what is missing or unclear, and lists the sections to teach before the lesson is written.' }),
                    area,
                    h('button', { type: 'button', class: 'neon-btn neon-btn-gold doc-analyze-text', text: 'Analyse text',
                        onclick: () => { if (area.value.trim()) this.analyzeText(area.value); } })));
                return;
            }
            if (this.state === 'loading') {
                this.body.appendChild(h('div', { class: 'doc-loading', 'aria-busy': 'true' }, h('div', { class: 'spinner' }), h('p', { class: 'doc-progress', text: this.progress })));
                return;
            }
            if (this.state === 'error') {
                this.body.appendChild(h('div', { class: 'doc-error', role: 'alert' },
                    h('p', {}, h('strong', { text: 'The document could not be analysed: ' }), this.error),
                    h('button', { type: 'button', class: 'neon-btn neon-btn-outline', text: 'Start again', onclick: () => { this.state = 'empty'; this.render(); } })));
                return;
            }
            this.renderResult(h);
        }

        renderResult(h) {
            const s = this.session;
            const view = s.view;
            const a = view.analysis;
            const readiness = a.readiness;
            const doc = a.document;
            const tag = (prov, kind) => { const b = badge(prov, kind); return h('span', { class: `doc-badge doc-badge-${b.cls}`, text: b.text }); };
            const where = ref => (refLabel(ref) ? h('span', { class: 'doc-ref', text: refLabel(ref) }) : null);
            const card = (title, ...children) => h('section', { class: 'doc-card' }, h('h3', { text: title }), ...children);
            if (view.stale) this.body.appendChild(h('p', { class: 'doc-stale', role: 'alert', text: `⚠ ${view.stale_reason}` }));
            const technical = this.debugOn();
            const kind = doc.source_type || view.document.source_type;
            const sourceKind = technical ? kind : (SOURCE_KINDS[kind] || kind);

            // Overview
            this.body.appendChild(card('Document overview',
                h('dl', { class: 'doc-overview' },
                    h('dt', { text: 'Title' }), h('dd', {}, (a.aadhi_ready.title && a.aadhi_ready.title.text) || 'Not provided', ' ', a.aadhi_ready.title && a.aadhi_ready.title.text ? tag(a.aadhi_ready.title.provenance) : null),
                    h('dt', { text: 'Subject' }), doc.subject && doc.subject.text ? h('dd', {}, doc.subject.text, ' ', tag(doc.subject.provenance))
                        : h('dd', { class: s.fields.subject ? null : 'doc-missing' }, s.fields.subject || 'Not found in the document. You can add it under “Sections to teach” below.'),
                    h('dt', { text: 'Language' }), h('dd', { text: `${doc.language.name}` }),
                    h('dt', { text: 'Source' }), h('dd', { text: `${view.document.file_name} (${sourceKind}${doc.page_count ? `, ${doc.page_count} pages` : ''}${technical ? `, version ${view.document.version}` : ''})` }),
                    h('dt', { text: 'Sections' }), h('dd', { text: String(doc.section_count) }),
                    // Phase 21: one plain verdict (the server's: ready when no warning is open); its kind only in the debug view
                    h('dt', { text: 'Readiness' }), h('dd', {}, h('span', { class: `doc-verdict doc-verdict-${readiness.verdict} ${readiness.ready_for_generation ? 'doc-ready' : 'doc-not-ready'}`,
                        text: (readiness.ready_for_generation ? 'Ready to write the lesson' : `Could be clearer — ${readiness.open_warnings} note${readiness.open_warnings === 1 ? '' : 's'}`)
                            + (technical ? ` (${VERDICTS[readiness.verdict] || readiness.verdict})` : '') }))),
                h('p', { class: 'doc-ai', text: aiStatusText(view, { debug: technical }) }),
                a.quality.strengths.length ? h('ul', { class: 'doc-strengths' }, a.quality.strengths.map(x => h('li', { text: `✓ ${x.text}` }))) : null));

            // Learning objectives + prerequisites
            const listEditor = (items, kind) => h('ul', { class: `doc-list doc-${kind}` }, items.map((o, i) => h('li', { class: 'doc-editable' },
                h('input', { type: 'checkbox', checked: o.accepted, 'aria-label': `Use this ${kind === 'objectives' ? 'objective' : 'prerequisite'}`,
                    onchange: e => { if (kind === 'objectives') s.acceptObjective(i, e.target.checked); else s.acceptPrerequisite(i, e.target.checked); this.render(); } }),
                h('input', { type: 'text', class: 'neon-input doc-text', value: o.text, 'aria-label': 'Text',
                    onchange: e => { if (kind === 'objectives') s.editObjective(i, e.target.value); else s.editPrerequisite(i, e.target.value); this.render(); } }),
                tag(o.provenance),
                h('button', { type: 'button', class: 'doc-small', text: 'Remove', onclick: () => { kind === 'objectives' ? s.removeObjective(i) : s.removePrerequisite(i); this.render(); } }))));
            const adder = (placeholder, add) => {
                const input = h('input', { type: 'text', class: 'neon-input doc-text', placeholder, 'aria-label': placeholder });
                return h('div', { class: 'doc-add' }, input, h('button', { type: 'button', class: 'doc-small', text: 'Add', onclick: () => { if (add(input.value)) this.render(); } }));
            };
            this.body.appendChild(card('Learning objectives',
                s.objectives.length ? listEditor(s.objectives, 'objectives') : h('p', { class: 'doc-missing', text: 'Not provided in the document.' }),
                adder('Add a learning objective', text => s.addObjective(text)),
                h('h4', { text: 'Prerequisites' }),
                s.prerequisites.length ? listEditor(s.prerequisites, 'prerequisites') : h('p', { class: 'doc-missing', text: 'Not provided in the document.' }),
                adder('Add a prerequisite', text => s.addPrerequisite(text))));

            // Content structure (as found)
            const detail = (label, items, render) => items.length ? h('div', { class: 'doc-items' }, h('h5', { text: label }), h('ul', {}, items.map(render))) : null;
            this.body.appendChild(card('Content structure',
                h('ol', { class: 'doc-tree' }, a.aadhi_ready.sections.map(sec => h('li', { class: 'doc-section' },
                    // a section's own place: its page (the heading next to it is the section's own title; its reference's heading is
                    // the document's title, which says nothing here)
                    h('div', { class: 'doc-section-title' }, sec.title || 'Untitled section', ' ', tag(sec.title_provenance), ' ',
                        where(sec.ref && sec.ref.page ? { page: sec.ref.page } : null)),
                    h('ul', {}, sec.subtopics.map(st => h('li', { class: 'doc-subtopic' },
                        h('details', {},
                            h('summary', {}, st.title || 'Opening text', ' — ', counts(st).join(', ') || 'no content'),
                            detail('Definitions', st.definitions, d => h('li', {}, h('strong', { text: d.term }), ': ', d.text, ' ', tag(d.provenance), ' ', where(d.ref))),
                            detail('Formulas', st.formulas, f => h('li', {}, h('code', { class: 'doc-formula', text: f.expression }), ' ', tag(f.provenance), ' ', where(f.ref),
                                (f.undefined_variables.length || f.unexplained_components.length) ? h('span', { class: 'doc-warn', text: ` not explained: ${f.undefined_variables.concat(f.unexplained_components).join(', ')}` }) : null)),
                            detail('Code', st.code, c => h('li', {}, h('pre', { class: 'doc-code', text: c.code }), c.language ? `${c.language} ` : '', tag(c.provenance), ' ', where(c.ref),
                                c.explained ? null : h('span', { class: 'doc-warn', text: ' no explanation' }))),
                            detail('Examples', st.examples, e => h('li', {}, e.text, ' ', tag(e.provenance), ' ', where(e.ref))),
                            detail('Ideas for visuals', st.visual_opportunities, v => h('li', {}, `${v.type}${v.caption ? ': ' + v.caption : ''}`, ' ', tag(v.provenance), ' ', where(v.ref))),
                            detail('Important points', st.important_points, p => h('li', {}, p.text, ' ', tag(p.provenance), ' ', where(p.ref))),
                            detail('Ideas for quiz questions', st.quiz_candidates, q => h('li', {}, q.question, ' ', tag(q.provenance), ' ', where(q.ref))),
                            detail('Explanations', st.explanations, e => h('li', { class: 'doc-explanation' }, e.text.length > 400 ? e.text.slice(0, 400) + '…' : e.text, ' ', tag(e.provenance), ' ', where(e.ref))))))))))));

            // Issues (Phase 21: "Things to check"; "Done" is a toggle, like Accept / Reject)
            const issues = a.quality.issues;
            this.body.appendChild(card(`Things to check (${issues.filter(i => s.issues[i.id] !== 'resolved').length})`,
                issues.length ? h('ul', { class: 'doc-issues' }, issues.map(i => h('li', { class: `doc-issue doc-issue-${i.severity}${s.issues[i.id] === 'resolved' ? ' doc-resolved' : ''}`, 'data-issue': i.type },
                    h('div', { class: 'doc-issue-head' }, i.severity === 'warning' ? '⚠ ' : 'ℹ ', h('strong', { text: i.title }), ' ', tag(i.provenance, 'issue')),
                    h('p', {}, h('em', { text: 'Why: ' }), i.why),
                    i.ref ? h('p', {}, h('em', { text: 'Source: ' }), refLabel(i.ref) || 'the whole document') : null,
                    h('p', {}, h('em', { text: 'Suggestion: ' }), i.suggestion),
                    h('button', { type: 'button', class: 'doc-small', 'aria-pressed': String(s.issues[i.id] === 'resolved'), text: s.issues[i.id] === 'resolved' ? '✓ Done' : 'Done',
                        onclick: () => { s.setIssue(i.id, s.issues[i.id] === 'resolved' ? 'open' : 'resolved'); this.render(); } })))) : h('p', { text: 'Nothing to check.' })));

            // Recommendations
            const recs = a.recommendations;
            if (recs.length) {
                this.body.appendChild(card('Suggested improvements', h('ul', { class: 'doc-recs' }, recs.map(r => h('li', { class: `doc-rec doc-rec-${s.recommendations[r.id]}` },
                    h('strong', { text: r.title }), ' ', tag(r.provenance), h('p', { text: r.why }),
                    h('div', { class: 'doc-rec-actions' },
                        h('button', { type: 'button', class: 'doc-small', 'aria-pressed': String(s.recommendations[r.id] === 'accepted'), text: 'Accept', onclick: () => { s.setRecommendation(r.id, s.recommendations[r.id] === 'accepted' ? 'pending' : 'accepted'); this.render(); } }),
                        h('button', { type: 'button', class: 'doc-small', 'aria-pressed': String(s.recommendations[r.id] === 'rejected'), text: 'Reject', onclick: () => { s.setRecommendation(r.id, s.recommendations[r.id] === 'rejected' ? 'pending' : 'rejected'); this.render(); } })))))));
            }

            // Aadhi-ready structure (editable; "Sections to teach": an unticked section is left out of the lesson's input)
            const field = (key, label) => h('label', { class: 'doc-field' }, h('span', { text: label }),
                h('input', { type: 'text', class: 'neon-input doc-text', value: s.fields[key], placeholder: `Add the ${label.toLowerCase()}`, 'data-field': key,
                    onchange: e => { s.setField(key, e.target.value); this.render(); } }));
            this.body.appendChild(card('Sections to teach',
                h('p', { class: 'doc-hint', text: 'The lesson is written from these sections. Untick a section to leave it out.' }),
                h('div', { class: 'doc-fields' }, field('title', 'Title'), field('subject', 'Subject'), field('audience', 'Audience'), field('difficulty', 'Difficulty')),
                h('ol', { class: 'doc-structure' }, s.sections.map((sec, i) => h('li', { class: `doc-structure-item${sec.included ? '' : ' doc-excluded'}`, 'data-section': sec.id },
                    h('input', { type: 'checkbox', checked: sec.included, 'aria-label': `Teach “${sec.title.trim() || 'Untitled section'}”`, onchange: e => { s.toggleSection(sec.id, e.target.checked); this.render(); } }),
                    h('input', { type: 'text', class: 'neon-input doc-text', value: sec.title, placeholder: 'Untitled section', 'aria-label': 'Section title',
                        onchange: e => { s.renameSection(sec.id, e.target.value); this.render(); } }),
                    h('button', { type: 'button', class: 'doc-small', text: '▲', 'aria-label': 'Move up', disabled: i === 0, onclick: () => { s.moveSection(sec.id, -1); this.render(); } }),
                    h('button', { type: 'button', class: 'doc-small', text: '▼', 'aria-label': 'Move down', disabled: i === s.sections.length - 1, onclick: () => { s.moveSection(sec.id, 1); this.render(); } }),
                    h('span', { class: 'doc-sub', text: `${sec.source.subtopics.length} part${sec.source.subtopics.length === 1 ? '' : 's'}` }))))));

            // Actions
            const running = view.status === 'running';
            this.body.appendChild(h('div', { class: 'doc-actions' },
                h('button', { type: 'button', class: 'neon-btn neon-btn-outline doc-new', text: 'Analyse other text',
                    onclick: () => { this.pollToken++; this.state = 'empty'; this.setStatus(''); this.render(); } }),
                running ? h('button', { type: 'button', class: 'neon-btn neon-btn-outline doc-cancel', text: 'Stop AI analysis', onclick: async () => { try { this.show(await this.api.cancel(view.analysis_id)); } catch (e) { this.setStatus(this.plainError(e, 'The AI analysis could not be stopped.'), 'error'); } } }) : null,
                // nothing to save: a status, not a disabled button (the save itself is announced in the panel's live status)
                s.dirty ? h('button', { type: 'button', class: 'neon-btn neon-btn-outline doc-save', text: 'Save changes', disabled: running,
                    onclick: async () => { try { await this.save(); } catch (e) { this.setStatus(this.plainError(e, 'Your changes could not be saved (they are still on screen).'), 'error'); } } })
                    : h('span', { class: 'doc-saved', text: '✓ Saved' }),
                h('button', { type: 'button', class: 'neon-btn neon-btn-gold doc-use', text: 'Write the lesson from this', disabled: running || view.stale,
                    onclick: () => this.use() })));
        }
    }

    return { EXTRACTOR_VERSION, MAMMOTH_STYLE_MAP, VERDICTS, AssistantSession, DocumentAssistantPanel, DocumentsApi, aiStatusText, badge,
        blocksFromHtml, blocksFromPdfPages, blocksFromText, counts, escapeHtml, extractFile, fileKind, looksLikeFormula, refLabel };
});
