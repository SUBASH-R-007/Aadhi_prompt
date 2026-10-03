/*
 * Aadhi Studio (Phase 20): one guided place to make a lesson from start to finish.
 *
 * The Studio ORCHESTRATES what already exists (scratchpad/phase20_contract.md §6): it never parses, writes, plans,
 * generates, reviews, styles, checks or exports anything itself. Every step goes through the page's adapter (index.html
 * wires it to the Phase 11 Document Assistant, the durable lesson writer (/api/studio), the Phase 12 presenter and Phase 17
 * style settings, Visual Review, the editor, the Phase 18 quality check and the export panel). This file only draws:
 *   Home       Your lessons (a stage chip in plain words, when it was updated, Continue; lessons still being written can
 *              be followed) and one primary action, "Create a lesson"
 *   Create     "How would you like to start?": upload a document · paste your notes · open a lesson file, then the
 *              naming / paste form
 *   Progress   the REAL stages of a lesson being written (no made-up percentages), Continue later, Stop writing (asked
 *              in the panel first), plain failure copy, Try again
 *   Lesson     a stage rail: 1 Content · 2 Lesson · 3 Visuals & presenter · 4 Style · 5 Review & edit · 6 Preview ·
 *              7 Export. Every stage says ✓ Complete / ● In progress / → Ready (the suggested next step) / ⚠ Needs
 *              attention / ○ Not started with one truthful line derived from the server's lesson state
 *              (GET /api/studio/lessons/{id}); nothing is guessed. Any stage can be opened directly
 *
 * Lesson text is data: it reaches the page through textContent only (never innerHTML). Provider or model names, run ids,
 * revisions and fingerprints are never shown unless the page is in debug mode (?visualDebug, or adapter.debug). Every
 * failure says what happened, whether the user's work is safe and what to do (Try again), in plain words (no stack traces,
 * no server text that is not plain words). No animated transitions. Phase 21: the dialog keeps Tab inside it, gives the
 * focus back to what opened it (or to the button that opened a panel above it), and polls nothing while the tab is hidden.
 *
 * Loaded as a classic <script> (window.AadhiStudio) and as a CommonJS module by tests/studio.test.js. The page's adapter
 * is described at the end of this file.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.AadhiStudio = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // Panels of the page that sit above the Studio and own Esc while open (the editor too: it opens over the Studio)
    const OTHER_OVERLAYS = '.editor-root, .asset-overlay.open, .review-overlay.open, .export-overlay.open, .glass-overlay.active:not(#loading-overlay), .history-modal-overlay.active';
    const RUN_POLL_MS = 2000;      // a lesson being written: its run is asked about every 2 s
    const STATE_POLL_MS = 4000;    // media being made (or lessons being written, on Home): every 4 s
    const MAX_TEXT = 262144;       // POST /api/studio/lessons text limit
    // storage: the lesson being written (so a reload can follow it again: its run id, title, whether it came from a document
    // and when it started, nothing else) and the lesson version last previewed, per lesson. Per user when the page names its
    // user (adapter.userKey): the next person in the same browser never sees them
    const RUN_KEY = 'aadhi.studio.run';
    const PREVIEW_KEY = 'aadhi.studio.previewed';
    const RUN_MAX_AGE_MS = 2 * 60 * 60 * 1000;     // a remembered run older than this is not followed again

    // The five stage states (always an icon and words, never a colour alone). A stage keeps its Phase 20 data-status
    // (done · active · todo · attention: what the page's browser checks read) and says its state in data-state
    const STATE = {
        complete: '✓ Complete',
        'in-progress': '● In progress',
        ready: '→ Ready',
        attention: '⚠ Needs attention',
        'not-started': '○ Not started'
    };
    const STATE_OF = { done: 'complete', active: 'in-progress', todo: 'not-started', attention: 'attention' };
    const STATUS = { done: STATE.complete, active: STATE['in-progress'], todo: STATE['not-started'], attention: STATE.attention };

    // short: the label of the compact stepper (tablets); help: the one line under each stage's summary (what it is for)
    const STAGES = [
        { key: 'content', name: 'Content', short: 'Content', help: 'What the lesson was made from. Change its scenes in the Lesson stage.' },
        { key: 'lesson', name: 'Lesson', short: 'Lesson', help: 'Read through the scenes and change anything you want.' },
        { key: 'visuals', name: 'Visuals & presenter', short: 'Visuals', help: 'The pictures, clips and presenter your lesson uses.' },
        { key: 'style', name: 'Style', short: 'Style', help: 'Changes how the lesson looks without making its pictures or clips again.' },
        { key: 'review', name: 'Review & edit', short: 'Review', help: 'Check each visual and the whole lesson before you export.' },
        { key: 'preview', name: 'Preview', short: 'Preview', help: 'Watch the whole lesson as your students will see it.' },
        { key: 'export', name: 'Export', short: 'Export', help: 'Records the lesson as a video file, saved to your account.' }
    ];
    const HELP_REVIEW = 'Review each visual before you export.';
    const HELP_QUALITY = 'Checks your lesson for readability and consistency.';

    // The existing settings panel each stage hosts (adapter.mountSettings)
    const SETTINGS_FOR = { visuals: 'presenter', style: 'style' };
    const SETTINGS_TITLE = { presenter: 'Presenter', style: 'Lesson style' };

    // The lesson's own stage (GET /api/studio/lessons ... stage) as a chip, in plain words
    const LESSON_STAGE = {
        draft: '○ Ready to edit',
        generating: '● Generating visuals',
        needs_attention: '⚠ Needs attention',
        review: '● Ready for review',
        ready_to_export: '✓ Ready to export',
        exporting: '● Exporting the video',
        completed: '✓ Video ready',
        writing: '✎ Being written'
    };

    // ai_runs.RunState
    const RUN_ACTIVE = ['queued', 'running', 'recovering', 'cancel_requested'];
    const RUN_END = ['completed', 'failed', 'cancelled', 'needs_attention'];

    // The lesson writer's real stages (run.detail.stage words: "Writing the lesson" → "Checking the lesson" → "Saving the lesson")
    const STEPS = [
        { key: 'source', label: 'Understanding your content' },
        { key: 'write', label: 'Writing the lesson' },
        { key: 'check', label: 'Checking the lesson' },
        { key: 'save', label: 'Saving the lesson' }
    ];
    const STEP_WORDS = { done: '✓ Complete', active: '● In progress', todo: '○ Not started', stopped: '⚠ Stopped' };

    const WRITE_FAILED = "We couldn't write the lesson from this content. Your document or notes are safe.";
    const STOP_QUESTION = 'Stop writing this lesson? Nothing has been saved yet.';
    const STOPPING = 'Stopping…';
    // Who can fix a missing lesson writer, and what still works without one
    const NO_WRITER_NOTE = "ⓘ This copy of Aadhi can't write lessons with AI yet — ask your administrator to set up a lesson writer. "
        + 'You can still prepare a document, open your lessons or open a lesson file.';

    // "Is my work safe?": the line an error adds (unless its own words already say it)
    const LESSON_SAFE = 'Your lesson is unchanged.';
    const SAFE_SAID = /\b(safe|saved|not affected|unchanged|still here|keeps being written)\b/i;

    // exports.py statuses
    const EXPORT_ACTIVE = ['QUEUED', 'PREPARING', 'RECORDING', 'UPLOADING', 'PROCESSING'];

    const MEDIA_ATTENTION = ['needs_attention', 'failed', 'attention'];
    const SLOT_WORD = { main: 'visual', side: 'visual', presenter: 'presenter clip', background: 'background' };

    // The video styles by the names their cards show (styles.py labels, cinematic.js STYLE_FAMILIES)
    const STYLE_NAMES = { academic: 'Academic', cinematic_education: 'Cinematic Education', children_education: "Children's Education",
        corporate_training: 'Corporate Training' };
    const CLASSIC_STYLE = 'Classic layout — video styles need the Cinematic layout';

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // Words that never reach the page outside debug mode (an error or a run message may carry them)
    const PROVIDER_WORDS = /\b(gemini|openai|open ai|chatgpt|gpt|anthropic|claude|pollinations|replicate|fal|runway(ml)?|kling|luma|pika|eleven ?labs|stability|dall-?e|imagen|veo|sora|midjourney|groq|mistral|llama|deepseek|hugging ?face|azure|vertex|bedrock|cohere|ollama)\b/i;
    const MODEL_LIKE = /\b[a-z]{2,}-\d[\w.-]*/i; // gemini-2.5-flash, gpt-4o
    const TECHNICAL = /(traceback|exception|error:|\bat\s+[\w.$<>]+\s*\(|\bstack\b|undefined|\bnan\b|\bnull\b|[{}<>[\]`]|https?:|\/api\/|\\|\.py\b|\.js\b|status code|\b[45]\d\d\b|sqlalchemy|api[ _-]?key|secret|token)/i;
    const NETWORK = /failed to fetch|network ?error|load failed|net::/i;

    // ---- small helpers ---------------------------------------------------------------------------------------------

    function el(doc, tag, props, ...children) {
        const node = doc.createElement(tag);
        Object.entries(props || {}).forEach(([key, value]) => {
            if (value === null || value === undefined || value === false) return;
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key === 'value') node.value = value;
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value === true ? '' : value);
        });
        children.flat(Infinity).forEach(child => {
            if (child !== null && child !== undefined && child !== false) node.appendChild(typeof child === 'string' ? doc.createTextNode(child) : child);
        });
        return node;
    }

    function detach(node) {
        if (!node) return;
        if (typeof node.remove === 'function') node.remove();
        else if (node.parentNode && typeof node.parentNode.removeChild === 'function') node.parentNode.removeChild(node);
    }

    function parentOf(node) {
        return node ? node.parentNode || node.parent || null : null;
    }

    function inside(node, ancestor) {
        for (let n = node; n && ancestor; n = parentOf(n)) if (n === ancestor) return true;
        return false;
    }

    // A short, stable, non-reversible name for a user id (a storage key never shows who signed in): FNV-1a, 32 bits
    function hashText(text) {
        let h = 0x811c9dc5;
        for (let i = 0; i < text.length; i++) {
            h ^= text.charCodeAt(i);
            h = Math.imul(h, 0x01000193) >>> 0;
        }
        return h.toString(16).padStart(8, '0');
    }

    function clear(node) {
        if (node) node.textContent = '';
    }

    function setHidden(node, hidden) {
        if (!node) return;
        if (hidden) node.setAttribute('hidden', '');
        else node.removeAttribute('hidden');
    }

    function focusNode(node) {
        if (node && typeof node.focus === 'function') {
            try { node.focus(); } catch (e) { /* not focusable */ }
        }
    }

    // Calls fn with the value, or with what the promise resolves to (an adapter method may be sync or async)
    function whenReady(value, fn) {
        if (value && typeof value.then === 'function') return value.then(fn);
        return fn(value);
    }

    const plain = v => !!v && typeof v === 'object' && !Array.isArray(v);
    const str = (v, limit = 400) => (typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v)).slice(0, limit);
    const finite = v => typeof v === 'number' && Number.isFinite(v);
    const count = v => (Array.isArray(v) ? v.length : Number.isInteger(v) && v > 0 ? Math.min(v, 1e6) : 0);
    const arr = v => (Array.isArray(v) ? v : []);
    const pad = n => String(n).padStart(2, '0');

    function projectIdOf(v) {
        if (Number.isInteger(v) && v > 0) return v;
        if (typeof v === 'string' && /^\d{1,12}$/.test(v) && Number(v) > 0) return Number(v);
        return null;
    }

    function plural(n, word) {
        return `${n} ${word}${n === 1 ? '' : 's'}`;
    }

    function humanize(word) {
        const text = String(word || '').replace(/[_-]+/g, ' ').trim();
        return text ? text[0].toUpperCase() + text.slice(1) : '';
    }

    function styleName(id) {
        return STYLE_NAMES[id] || humanize(id);
    }

    // "2 Oct 2026, 14:03" (local time), '' when the value is not a date
    function dateText(value) {
        if (!value) return '';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return '';
        return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function isTyping(target) {
        if (!target) return false;
        const tag = String(target.tagName || target.tag || '').toUpperCase();
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (target.isContentEditable === true) return true;
        const ce = typeof target.getAttribute === 'function' ? target.getAttribute('contenteditable') : null;
        return ce !== null && ce !== undefined && ce !== 'false';
    }

    // A message from the server or an adapter as one plain sentence, or '' when it is not plain words (a stack trace, JSON,
    // a provider or model name, a status code...)
    function plainDetail(err) {
        const raw = typeof err === 'string' ? err : err && typeof err.message === 'string' ? err.message : '';
        if (!raw) return '';
        if (NETWORK.test(raw)) return 'The server could not be reached. Check the connection.';
        // a provider's own detail in brackets ("… (provider: reason)") is never shown, whatever the provider is called
        const text = raw.replace(/\s+/g, ' ').trim().replace(/\s*\([^()]*:[^()]*\)\s*([.!?…]?)$/, '$1').trim();
        if (!text || text.length > 200 || /[\r\n]/.test(raw.trim())) return '';
        if (TECHNICAL.test(text) || PROVIDER_WORDS.test(text) || MODEL_LIKE.test(text)) return '';
        return /[.!?…]$/.test(text) ? text : `${text}.`;
    }

    // "We couldn't load your lessons. <the plain reason, when there is one>"
    function plainError(fallback, err) {
        const detail = plainDetail(err);
        return detail && detail !== fallback ? `${fallback} ${detail}` : fallback;
    }

    function rawText(err) {
        if (typeof err === 'string') return err.slice(0, 600);
        if (err && typeof err.message === 'string') return err.message.slice(0, 600);
        return '';
    }

    // The naming step's fields from a document title: "Photosynthesis: Light reactions" → subject + session title. A title
    // with no subject in it goes in the session title only (the video's title card and the export name show both: the same
    // words twice read "How Plants Make Food — How Plants Make Food"; an empty subject is the lesson writer's to fill)
    function prefillNames(title) {
        const t = str(title, 200).replace(/\.(pdf|docx?|txt|md)$/i, '').replace(/_+/g, ' ').replace(/\s+/g, ' ').trim();
        const m = t.match(/^(.{2,80}?)\s*(?::|\s[-–—|]\s)\s*(.{2,})$/);
        const subject = m ? m[1].trim() : '';
        const session = m ? m[2].trim() : t;
        return {
            subject_name: subject.toLowerCase() === session.toLowerCase() ? '' : subject,
            unit_name: '',
            session_number: 'Session 1',
            session_title: session
        };
    }

    // The first line of pasted content as a title ("# Photosynthesis" → "Photosynthesis")
    function firstLine(text) {
        const line = String(text || '').split(/\r?\n/).map(l => l.replace(/^\s*(#{1,6}|[-*•]|\d+[.)])\s*/, '').trim()).find(Boolean) || '';
        return line.slice(0, 120);
    }

    // A pasted saved lesson (JSON with scenes / slides) belongs to "Open a lesson file", not to the lesson writer
    function looksLikeLessonJson(text) {
        const t = String(text || '').trim();
        if (!/^[{[]/.test(t)) return false;
        try {
            const v = JSON.parse(t);
            return plain(v) && (Array.isArray(v.scenes) || Array.isArray(v.slides));
        } catch (e) {
            return false;
        }
    }

    function cleanNames(names) {
        const n = plain(names) ? names : {};
        return {
            subject_name: str(n.subject_name, 200).trim(),
            unit_name: str(n.unit_name, 200).trim(),
            session_number: str(n.session_number, 40).trim(),
            session_title: str(n.session_title, 200).trim()
        };
    }

    // ---- what the adapter returns, as the Studio uses it -------------------------------------------------------------

    function runStatusOf(run) {
        return plain(run) && typeof run.status === 'string' ? run.status.toLowerCase() : '';
    }

    function normalizeRun(raw) {
        const r = plain(raw) ? raw : {};
        return {
            runId: str(r.run_id, 80),
            status: runStatusOf(r),
            stage: str(r.stage, 80),
            projectId: projectIdOf(r.project_id),
            message: str(r.message, 400),
            raw: r
        };
    }

    // 0 writing, 1 checking, 2 saving (from the run's stage words)
    function stepIndex(stage) {
        const s = String(stage || '').toLowerCase();
        if (/sav/.test(s)) return 2;
        if (/check/.test(s)) return 1;
        return 0;
    }

    // The progress list: only stages that really happen, each with its status (icon + words)
    function runSteps(run, fromSource) {
        const status = runStatusOf(run);
        const at = stepIndex(run && run.stage);
        const out = [];
        if (fromSource) out.push({ key: 'source', label: STEPS[0].label, status: 'done', text: STEP_WORDS.done });
        STEPS.slice(1).forEach((step, i) => {
            let state;
            if (status === 'completed' || i < at) state = 'done';
            else if (i > at) state = 'todo';
            else if (RUN_END.includes(status)) state = 'stopped';
            else state = 'active';
            let text = STEP_WORDS[state];
            if (state === 'active') {
                if (status === 'queued') text = '● Waiting to start';
                else if (status === 'cancel_requested') text = '● Stopping…';
                else if (status === 'recovering') text = '● Picking up again';
            }
            out.push({ key: step.key, label: step.label, status: state, text });
        });
        return out;
    }

    function normalizeLessons(raw) {
        let items = [];
        if (Array.isArray(raw)) items = raw;
        else if (plain(raw)) items = arr(raw.lessons).concat(arr(raw.runs));
        const lessons = [];
        const runs = [];
        items.slice(0, 200).forEach(item => {
            if (!plain(item)) return;
            const pid = projectIdOf(item.project_id);
            if (pid) {
                lessons.push({
                    projectId: pid,
                    title: str(item.title, 200).replace(/\s+/g, ' ').trim() || 'Untitled lesson',
                    updated: dateText(item.updated_at),
                    stage: str(item.stage, 40),
                    scenes: count(item.scene_count),
                    generating: item.generating === true || count(item.generating) > 0,
                    attention: item.attention === true || count(item.attention) > 0,
                    exported: item.exported === true || count(item.exported) > 0
                });
            } else if (typeof item.run_id === 'string' && item.run_id) {
                const status = runStatusOf(item);
                if (['completed', 'failed', 'cancelled'].includes(status)) return; // no longer being written
                runs.push({ runId: item.run_id.slice(0, 80), status, stage: str(item.stage, 80) });
            }
        });
        const writer = plain(raw) && plain(raw.writer) ? { available: raw.writer.available !== false } : null;
        return { lessons: lessons.slice(0, 50), runs: runs.slice(0, 20), writer };
    }

    // A presenter clip or background that failed: listed (to retry) but not counted — the lesson plays with its fallback
    function fallbackItem(item) {
        return !!item && item.status === 'failed' && ['presenter', 'background'].includes(item.slot);
    }

    // What the page needs to retry or skip a media item (a plain copy: never the panel's own object)
    function itemDetails(item) {
        return { sceneIndex: item.sceneIndex, sceneId: item.sceneId, slot: item.slot, status: item.status };
    }

    // A lesson stage chip: icon + words
    function lessonChip(stage) {
        return LESSON_STAGE[stage] || `○ ${humanize(stage) || 'Draft'}`;
    }

    function normalizeState(raw) {
        const s = plain(raw) ? raw : {};
        const names = plain(s.names) ? s.names : {};
        const o = plain(s.origin) ? s.origin : null;
        const m = plain(s.media) ? s.media : {};
        const r = plain(s.review) ? s.review : {};
        const ed = plain(s.editor) ? s.editor : {};
        const q = plain(s.quality) ? s.quality : null;
        const ex = plain(s.exports) ? s.exports : {};
        const latest = plain(ex.latest) ? ex.latest : null;
        const items = arr(m.items).slice(0, 300).filter(plain).map(item => ({
            sceneIndex: Number.isInteger(item.scene_index) && item.scene_index >= 0 ? item.scene_index : null,
            sceneId: str(item.scene_id, 80),
            slot: str(item.slot, 20),
            status: str(item.status, 40).toLowerCase(),
            runId: str(item.run_id, 80),
            message: str(item.message, 400)
        }));
        const title = [s.title, names.session_title, names.subject_name].map(t => str(t, 200).replace(/\s+/g, ' ').trim()).find(Boolean);
        return {
            projectId: projectIdOf(s.project_id),
            revision: str(s.revision, 60),
            fingerprint: str(s.fingerprint, 80),
            title: title || 'Untitled lesson',
            sceneCount: count(s.scenes),
            hidden: count(s.hidden),
            source: plain(s.source) ? { fileName: str(s.source.file_name, 200).replace(/\s+/g, ' ').trim() } : null,
            origin: o ? { source: count(o.source), ai: count(o.ai), edited: count(o.edited) } : null,
            media: {
                needed: count(m.needed), ready: count(m.ready), generating: count(m.generating),
                // the items are the truth when the server lists them; else its counts (needs attention + failed)
                attention: items.length ? items.filter(i => MEDIA_ATTENTION.includes(i.status) && !fallbackItem(i)).length : count(m.attention) + count(m.failed),
                items,
                attentionItems: items.filter(i => MEDIA_ATTENTION.includes(i.status))
            },
            review: { approved: count(r.approved), changed: count(r.changed), pending: count(r.pending), removed: count(r.removed) },
            style: plain(s.style) && typeof s.style.style === 'string' && s.style.style ? { style: str(s.style.style, 60), version: s.style.version } : null,
            editor: { edited: count(ed.edited), moved: count(ed.moved), hidden: count(ed.hidden) },
            checkpoints: plain(s.checkpoints) ? s.checkpoints : {},
            quality: q ? { status: str(q.status, 20).toLowerCase(), counts: plain(q.counts) ? q.counts : {}, stale: q.stale === true } : null,
            exports: {
                count: count(ex.count),
                latest: latest ? {
                    status: str(latest.status, 20).toUpperCase(),
                    completedAt: dateText(latest.completed_at),
                    matches: typeof latest.matches_lesson === 'boolean' ? latest.matches_lesson : null
                } : null
            },
            stage: str(s.stage, 40)
        };
    }

    // Phase 18 counts {notice, warning, error, blocking} (or {attention}) → the number of things to review
    function qualityCount(counts) {
        const c = plain(counts) ? counts : {};
        const levels = ['notice', 'warning', 'error', 'blocking'];
        if (levels.some(k => Number.isInteger(c[k]))) return levels.reduce((n, k) => n + (Number.isInteger(c[k]) && c[k] > 0 ? c[k] : 0), 0);
        return Number.isInteger(c.attention) && c.attention > 0 ? c.attention : 0;
    }

    // "✓ Quality looks good" / "⚠ 3 things to review" / null (not checked)
    function qualityWords(q) {
        if (!plain(q) || !q.status) return null;
        if (q.status === 'good' || q.status === 'pass') return '✓ Quality looks good';
        const n = qualityCount(q.counts);
        return n ? `⚠ ${n} ${n === 1 ? 'thing' : 'things'} to review` : '⚠ Some things to review';
    }

    function mediaItemText(item) {
        const what = SLOT_WORD[item.slot] || 'visual';
        if (item.sceneIndex === null) return `The lesson's ${what} needs attention`;
        return `Scene ${item.sceneIndex + 1} ${what} needs attention`;
    }

    function exportWords(latest) {
        if (!latest) return 'No video yet';
        if (EXPORT_ACTIVE.includes(latest.status)) return '● Your video is being made';
        if (latest.status === 'COMPLETED') return latest.completedAt ? `✓ Made ${latest.completedAt}` : '✓ Video made';
        if (latest.status === 'FAILED') return "⚠ The last export didn't finish";
        if (latest.status === 'CANCELLED') return '○ The last export was stopped';
        return `○ ${humanize(latest.status.toLowerCase()) || 'Unknown'}`;
    }

    function matchWords(latest) {
        if (!latest || latest.status !== 'COMPLETED' || latest.matches === null) return null;
        return latest.matches ? '✓ Matches your current lesson' : '⚠ Made before your latest changes';
    }

    // A checkpoint is current when it was made for this version of the lesson (no fingerprint stored: it stays current)
    function checkpointCurrent(cp, fingerprint) {
        if (!plain(cp)) return false;
        if (typeof cp.fingerprint === 'string' && cp.fingerprint && fingerprint) return cp.fingerprint === fingerprint;
        return true;
    }

    function contentFacts(st) {
        const o = st.origin;
        if (!o) return plural(st.sceneCount, 'scene');
        const parts = [];
        if (st.source) parts.push(`From your document: ${plural(o.source, 'scene')}`);
        else if (o.source > 0) parts.push(`From your text: ${plural(o.source, 'scene')}`);
        else parts.push(plural(st.sceneCount, 'scene'));
        // (a count of none says nothing: "AI-written: 0 · Edited by you: 0" is left out)
        if (o.ai) parts.push(`AI-written: ${o.ai}`);
        if (o.edited) parts.push(`Edited by you: ${o.edited}`);
        return parts.join(' · ');
    }

    // Stages whose work is the user's optional check: never "In progress", and "→ Ready" until it is done
    const OPTIONAL = ['content', 'lesson', 'style', 'preview'];

    // The seven stages from the lesson state: [{key, number, name, help, status, state, text, summary, next}]. status is the
    // Phase 20 word (done · active · todo · attention), state its Phase 21 name (complete · in-progress · ready · attention ·
    // not-started), next marks the suggested next stage (defaultStage). Rules (all from data):
    //   Content    ✓ when the structure was confirmed; → ready to confirm otherwise; ○ with no scenes
    //   Lesson     ✓ when "Looks right" was said for this version; → ready otherwise (edited, changed since, or untouched)
    //   Visuals    ⚠ when a visual needs attention; ● while being made or partly ready; ✓ when all are ready (or none are
    //              needed); ○ none ready
    //   Style      ✓ when a style was chosen and the layout shows it; → ready while the default style is used, or while the
    //              page's layout is Classic (opts.layout: a style shows only in the Cinematic layout)
    //   Review     ⚠ when the quality check found serious problems; ✓ when every scene visual is checked and the check is
    //              good and current; ● when started; ○ otherwise. It counts the scene visuals (the server's review counts);
    //              Visual Review also lists each scene's presenter and layout
    //   Preview    ✓ when this version was previewed (this browser); → ready otherwise
    //   Export     ✓ when the latest video matches the lesson; ● while one is being made; ⚠ when the last one failed; ○ otherwise
    // A stage not started says "→ Ready" when it can be done now (an optional stage of a lesson with scenes, the suggested
    // next stage and any stage before it, Export once a video exists); "○ Not started" is left for the required stages after
    // the suggestion, and for every stage but Lesson while the lesson has no scenes. So a lesson whose video is made shows
    // its earlier stages Complete or Ready, never Not started.
    function lessonStages(st, opts = {}) {
        const out = [];
        const add = (key, status, summary) => {
            const i = STAGES.findIndex(s => s.key === key);
            out.push({ key, number: i + 1, name: STAGES[i].name, short: STAGES[i].short, help: STAGES[i].help, status, state: STATE_OF[status], text: STATUS[status], summary, next: false });
        };
        const cp = st.checkpoints;

        // 1 Content
        if (!st.sceneCount) {
            add('content', 'todo', 'No scenes yet.');
            out[0].empty = true;
        } else {
            add('content', plain(cp.structure) ? 'done' : 'todo', contentFacts(st));
        }

        // 2 Lesson
        const e = st.editor;
        const lessonParts = [plural(st.sceneCount, 'scene')];
        if (e.edited) lessonParts.push(`${e.edited} edited by you`);
        if (e.moved) lessonParts.push(`${e.moved} moved`);
        if (e.hidden || st.hidden) lessonParts.push(`${Math.max(e.hidden, st.hidden)} hidden`);
        if (plain(cp.lesson) && checkpointCurrent(cp.lesson, st.fingerprint)) add('lesson', 'done', lessonParts.join(' · '));
        else if (plain(cp.lesson)) add('lesson', 'todo', `Changed since you said it looked right · ${lessonParts.join(' · ')}`);
        else add('lesson', 'todo', lessonParts.join(' · '));

        // 3 Visuals & presenter
        const m = st.media;
        const attention = Math.max(m.attention, m.attentionItems.filter(i => !fallbackItem(i)).length);
        // the server's counts: `needed` = still missing (not the total), `ready`, `generating`, needing attention / failed
        const ready = m.ready;
        const visualsTotal = m.needed + m.ready + m.generating + attention;
        const readyText = visualsTotal ? `${ready} of ${visualsTotal} ready` : `${ready} ready`;
        if (attention) add('visuals', 'attention', [`${attention} ${attention === 1 ? 'needs' : 'need'} attention`, readyText, m.generating ? `${m.generating} being made` : null].filter(Boolean).join(' · '));
        else if (m.generating) add('visuals', 'active', `${m.generating} being made · ${readyText}`);
        else if (!visualsTotal) add('visuals', 'done', 'No pictures or clips to make');
        else if (!m.needed) add('visuals', 'done', `${visualsTotal === 1 ? '1 picture or clip' : `All ${visualsTotal} pictures and clips`} ready · nothing left to make`);
        else if (ready > 0) add('visuals', 'active', `${readyText} · Prepare the rest`);
        else add('visuals', 'todo', `${m.needed} to prepare`);

        // 4 Style (in the Classic layout the lesson keeps its original look, whatever style is kept with it)
        if (opts.layout === 'classic') add('style', 'todo', CLASSIC_STYLE);
        else if (st.style) add('style', 'done', `Style: ${styleName(st.style.style)}`);
        else add('style', 'todo', 'Using the default style');

        // 5 Review & edit
        const r = st.review;
        const total = r.approved + r.changed + r.pending + r.removed;
        const q = st.quality;
        const qWords = q ? qualityWords(q) : null;
        const reviewParts = [];
        if (total) reviewParts.push(r.pending ? `${plural(r.pending, 'visual')} to check` : total === 1 ? '1 visual checked' : `All ${total} visuals checked`);
        if (!q) reviewParts.push('Quality not checked yet');
        else if (q.stale) reviewParts.push('Changed since the check');
        else if (qWords) reviewParts.push(qWords.replace(/^[✓⚠] /, ''));
        const serious = q && !q.stale && (q.status === 'attention' || q.status === 'blocked');
        const good = q && !q.stale && (q.status === 'good' || q.status === 'pass');
        if (serious) add('review', 'attention', reviewParts.join(' · '));
        else if (good && !r.pending) add('review', 'done', reviewParts.join(' · '));
        else if (q || r.approved || r.changed || r.removed) add('review', 'active', reviewParts.join(' · '));
        else add('review', 'todo', reviewParts.join(' · '));

        // 6 Preview
        const seen = typeof opts.previewed === 'string' && opts.previewed;
        if (seen && st.fingerprint && seen === st.fingerprint) add('preview', 'done', 'You previewed this version');
        else if (seen) add('preview', 'todo', 'Changed since you last previewed it');
        else add('preview', 'todo', 'Not previewed yet');

        // 7 Export
        const latest = st.exports.latest;
        if (!latest) add('export', 'todo', 'No video yet');
        else if (EXPORT_ACTIVE.includes(latest.status)) add('export', 'active', 'Your video is being made');
        else if (latest.status === 'COMPLETED' && latest.matches === false) add('export', 'todo', 'Your last video was made before your latest changes');
        else if (latest.status === 'COMPLETED') add('export', 'done', latest.matches ? 'Your video matches your current lesson' : 'Your video is ready');
        else if (latest.status === 'FAILED') add('export', 'attention', "The last export didn't finish");
        else add('export', 'todo', 'The last export was stopped');

        // the suggested next stage (the one the rail opens on), and which stages not started can be done now
        const at = out.findIndex(s => s.key === defaultStage(out, st.stage));
        out.forEach((s, i) => {
            if (s.status !== 'todo') return;
            const now = st.sceneCount ? OPTIONAL.includes(s.key) || i <= at || (s.key === 'export' && st.exports.count > 0) : s.key === 'lesson';
            if (now) {
                s.state = 'ready';
                s.text = STATE.ready;
            }
        });
        if (at >= 0 && out[at].status !== 'done') out[at].next = true;
        return out;
    }

    // The stage the rail opens on, and suggests next: the first that needs attention (Export last), then where the lesson's
    // own stage points (its chip: Ready to edit → Lesson, Generating visuals → Visuals, Ready to export / Exporting / Video
    // ready → Export), else the first required stage not complete (Visuals, Review & edit, Export). Content, Lesson, Style
    // and Preview are optional checks: a lesson never lands on them, unless it has no scenes yet (Lesson: the editor)
    function defaultStage(stages, lessonStage = '') {
        const by = key => stages.find(s => s.key === key) || { status: 'todo' };
        const attention = stages.find(s => s.status === 'attention' && s.key !== 'export');
        if (attention) return attention.key;
        if (lessonStage === 'draft' || by('content').empty) return 'lesson';
        if (lessonStage === 'generating') return 'visuals';
        if (['ready_to_export', 'exporting', 'completed'].includes(lessonStage)) return 'export';
        return ['visuals', 'review', 'export'].find(key => by(key).status !== 'done') || 'export';
    }

    // ---- the panel ---------------------------------------------------------------------------------------------------

    // A button's selector (the focus goes back to it after a panel it opened closes)
    function selectorFor(action, attrs) {
        const a = plain(attrs) ? attrs : {};
        return `button[data-action="${action}"]${a['data-item'] ? `[data-item="${a['data-item']}"]` : ''}${a['data-project-id'] ? `[data-project-id="${a['data-project-id']}"]` : ''}`;
    }

    // What can take the keyboard focus inside the Studio (Tab stays among these while it is open)
    const FOCUSABLE = 'a, button, input, select, textarea, summary, [tabindex]';

    class StudioPanel {
        // doc: the page's document; adapter: the page's functions (see the end of this file); storage: localStorage-like
        // (optional; every read and write may fail); setTimeout / clearTimeout / pollMs: for tests
        constructor({ doc, adapter = {}, storage = null, setTimeout: setT = null, clearTimeout: clearT = null, pollMs = {} } = {}) {
            this.doc = doc;
            this.adapter = adapter || {};
            this.storage = storage;
            this.setT = setT || ((fn, ms) => {
                const t = setTimeout(fn, ms);
                if (t && typeof t.unref === 'function') t.unref(); // (Node) never keeps a test run alive
                return t;
            });
            this.clearT = clearT || (t => clearTimeout(t));
            this.runPollMs = finite(pollMs && pollMs.run) ? pollMs.run : RUN_POLL_MS;
            this.statePollMs = finite(pollMs && pollMs.state) ? pollMs.state : STATE_POLL_MS;
            this.root = null;
            this.isOpen = false;
            this.view = 'home';      // home | create | name | paste | progress | lesson
            this.seq = 0;            // bumped on every view change: an answer meant for an older view is dropped
            this.timer = null;       // the one poll waiting
            this.pollPaused = false; // a poll came due while the tab was hidden: asked again once the tab is shown
            this.busy = null;        // the action in flight (its buttons are disabled meanwhile)
            this.error = null;       // {text, safe, retry, alt, raw, kind: 'load' | 'action' | 'form'}
            this.note = '';
            this.lessons = null;     // {lessons, runs, writer} from listLessons ({..., failed: true} when the first load failed)
            this.noWriter = false;   // the server said no lesson writer is set up (a 503 when writing was asked)
            this.draft = null;       // the naming / paste form {kind, source, title, text, names, touched}
            this.progress = null;    // {runId, fromSource, request, title, run, resumed}
            this.confirmStop = false; // "Stop writing this lesson?" is being asked in the progress view
            this.projectId = null;
            this.state = null;       // normalizeState(lessonState(projectId))
            this.stage = null;       // the stage open in the rail
            this.lastQuality = null; // {projectId, status, counts}: the check run in this session
            this.lastCurrent = null; // the page's lesson when the Studio last looked (currentLesson)
            this.mounted = new Map(); // settings kind -> its section (adapter.mountSettings placed the page's panel in it)
            this.lessonEls = null;
            this.lastRefresh = 0;
            this.opener = null;      // what had the focus when the Studio opened: it gets it back when the user closes it
            this.clicked = null;     // the selector of the last Studio button used
            this.returnTo = null;    // the button that opened a panel above the Studio: the focus goes back to it after
            this.legacyDropped = false;
            this.fileLessons = new Set(); // lessons opened from a lesson file here (the Content stage says so)
            this.shownLayout = null; // the layout the stages were drawn for (Classic / Cinematic: what the Style stage says)
            this.fieldError = null;  // the error under the notes box (paste form)
            this.onKey = e => this.handleKey(e);
            this.onVisible = () => this.regainVisibility();
        }

        h(...args) { return el(this.doc, ...args); }

        get debug() {
            const d = this.adapter.debug;
            if (typeof d === 'function') {
                try { if (d.call(this.adapter)) return true; } catch (e) { /* not in debug mode */ }
            } else if (d === true) {
                return true;
            }
            const win = this.doc && this.doc.defaultView;
            const search = (win && win.location && win.location.search) || (this.doc && this.doc.location && this.doc.location.search) || '';
            return /[?&]visualDebug(=|&|$)/.test(String(search));
        }

        has(name) { return typeof this.adapter[name] === 'function'; }

        // An optional true / false the page may declare (a value, or a function returning it)
        flag(name) {
            const v = this.adapter[name];
            if (typeof v === 'function') {
                try { return v.call(this.adapter) === true; } catch (e) { return false; }
            }
            return v === true;
        }

        // The page's layout, 'classic' or 'cinematic' (a lesson style shows only in Cinematic): adapter.layout (a value or a
        // function), else the page's own cinematic settings (window.cinematicSettings); null when the page does not say
        layoutOf() {
            let v = this.adapter.layout;
            if (typeof v === 'function') {
                try { v = v.call(this.adapter); } catch (e) { v = null; }
            }
            if (v === undefined || v === null) {
                const win = this.doc && this.doc.defaultView;
                const s = win && win.cinematicSettings ? win.cinematicSettings.settings : null;
                v = plain(s) ? (s.mode === 'cinematic' ? 'cinematic' : 'classic') : null; // (cinematic.js isClassic)
            }
            return v === 'classic' || v === 'cinematic' ? v : null;
        }

        // An adapter method as a promise (a missing method, a throw or a rejection all reject)
        invoke(name, ...args) {
            const fn = this.adapter[name];
            if (typeof fn !== 'function') return Promise.reject(new Error('This step is not available on this page.'));
            try {
                return Promise.resolve(fn.apply(this.adapter, args));
            } catch (e) {
                return Promise.reject(e);
            }
        }

        // An adapter method whose failure only matters to the page (never throws)
        safeCall(name, ...args) {
            const fn = this.adapter[name];
            if (typeof fn !== 'function') return undefined;
            try {
                const out = fn.apply(this.adapter, args);
                if (out && typeof out.catch === 'function') out.catch(() => {});
                return out;
            } catch (e) {
                return undefined;
            }
        }

        // ---- storage (a convenience: the Studio works without it) -------------------------------------------------

        // The key a value is kept under: per user when the page names who is signed in (adapter.userKey(), any stable id,
        // kept only as a hash), so the next person in the same browser never sees it; null (nothing kept or read) when the
        // page has that hook but nobody is signed in. Without the hook: the shared Phase 20 key.
        storeKey(base) {
            if (!this.has('userKey')) return base;
            let id = null;
            try { id = this.adapter.userKey(); } catch (e) { id = null; }
            const text = typeof id === 'string' || (typeof id === 'number' && Number.isFinite(id)) ? String(id).trim() : '';
            if (!text) return null;
            if (!this.legacyDropped) {
                // what was kept under the shared keys (before the page named its users) is shown to nobody
                this.legacyDropped = true;
                try {
                    if (this.storage && typeof this.storage.removeItem === 'function') [RUN_KEY, PREVIEW_KEY].forEach(k => this.storage.removeItem(k));
                } catch (e) { /* blocked storage */ }
            }
            return `${base}.u${hashText(text)}`;
        }

        readStore(base) {
            try {
                const key = this.storeKey(base);
                const raw = key && this.storage && typeof this.storage.getItem === 'function' ? this.storage.getItem(key) : null;
                return raw ? JSON.parse(raw) : null;
            } catch (e) {
                return null;
            }
        }

        writeStore(base, value) {
            try {
                const key = this.storeKey(base);
                if (!this.storage || !key) return;
                if (value === null) { if (typeof this.storage.removeItem === 'function') this.storage.removeItem(key); }
                else if (typeof this.storage.setItem === 'function') this.storage.setItem(key, JSON.stringify(value));
            } catch (e) { /* private window, blocked storage: nothing is remembered */ }
        }

        // only what following the run again needs: its id, its title, whether it came from a document, when it started
        rememberRun(p) {
            this.writeStore(RUN_KEY, p ? { run_id: p.runId, from_source: !!p.fromSource, title: str(p.title, 200), at: Date.now() } : null);
        }

        // the remembered run ended: forgotten (another run remembered meanwhile stays)
        forgetRun(runId) {
            const v = this.readStore(RUN_KEY);
            if (plain(v) && (!runId || v.run_id === runId)) this.writeStore(RUN_KEY, null);
        }

        rememberedRun() {
            const v = this.readStore(RUN_KEY);
            if (!plain(v) || typeof v.run_id !== 'string' || !v.run_id) return null;
            if (finite(v.at) && Date.now() - v.at > RUN_MAX_AGE_MS) { this.writeStore(RUN_KEY, null); return null; }
            return v;
        }

        previewedVersion(projectId) {
            const v = this.readStore(PREVIEW_KEY);
            return plain(v) && typeof v[projectId] === 'string' ? v[projectId] : null;
        }

        rememberPreview(projectId, fingerprint) {
            if (!projectId) return;
            const v = this.readStore(PREVIEW_KEY);
            const map = plain(v) ? v : {};
            delete map[projectId];
            map[projectId] = fingerprint || 'seen';
            const keys = Object.keys(map);
            keys.slice(0, Math.max(0, keys.length - 50)).forEach(k => delete map[k]); // the 50 most recent lessons
            this.writeStore(PREVIEW_KEY, map);
        }

        // ---- open / close ------------------------------------------------------------------------------------------

        // options.view: 'home' (Your lessons) or 'create' ("How would you like to start?") opens there; without it the
        // Studio opens where it makes sense (a lesson still being written → its progress; the page's lesson → its stages)
        open(options) {
            const want = plain(options) && (options.view === 'home' || options.view === 'create') ? options.view : null;
            if (this.isOpen) {
                if (want) this.goTo(want);
                else this.focusHeading();
                return;
            }
            this.isOpen = true;
            const active = this.doc.activeElement;
            this.opener = active && active !== this.doc.body && active !== this.doc.documentElement && typeof active.focus === 'function' ? active : null;
            this.build();
            const win = this.doc.defaultView;
            // Esc (and Tab) are heard on the window first (capture), so a panel above the Studio still has them while open
            this.keyTarget = win && typeof win.addEventListener === 'function' ? win : this.doc;
            this.keyTarget.addEventListener('keydown', this.onKey, true);
            if (typeof this.doc.addEventListener === 'function') this.doc.addEventListener('visibilitychange', this.onVisible);
            if (win && typeof win.addEventListener === 'function') win.addEventListener('focus', this.onVisible);
            this.render();
            if (want) {
                this.goTo(want);
                return;
            }
            const current = this.has('currentLesson') ? this.safeCall('currentLesson') : null;
            const started = whenReady(current, cur => this.start(cur));
            if (started && typeof started.catch === 'function') started.catch(() => this.start(null));
        }

        // The page's own entries (its Home and Create): the Studio opens (or moves) there
        showHome() { this.open({ view: 'home' }); }

        showCreate() { this.open({ view: 'create' }); }

        goTo(view) {
            if (view === 'create') this.startCreate();
            else this.goHome();
        }

        start(current) {
            if (!this.isOpen) return;
            const pid = plain(current) ? projectIdOf(current.projectId !== undefined ? current.projectId : current.project_id) : null;
            if (this.view === 'home' && !this.progress) {
                const saved = this.rememberedRun();
                if (saved) {
                    this.progress = { runId: saved.run_id, fromSource: !!saved.from_source, request: null, title: str(saved.title, 200), run: null, resumed: true };
                    this.setView('progress');
                    this.pollRun();
                    return;
                }
            }
            if (['home', 'lesson'].includes(this.view) && pid && pid !== this.lastCurrent) {
                // the page opened another lesson since the Studio last looked: its stages
                this.lastCurrent = pid;
                this.showLesson(pid);
                return;
            }
            if (this.view === 'lesson' && this.has('currentLesson') && !pid) {
                // the lesson the Studio showed is no longer open on the page: its actions would act on nothing
                this.lastCurrent = null;
                this.setView('home');
            } else {
                this.render();
                this.focusHeading();
            }
            this.refresh();
        }

        // reason: 'user' (✕, Esc, or the page closing it) tells the page (adapter.close) and gives the focus back to what
        // opened the Studio; 'stage' hides the Studio for an action that needs the live stage (editor, preview), which takes
        // the focus. The Studio remembers its place for the next open()
        close(reason = 'user') {
            if (!this.isOpen) return;
            this.isOpen = false;
            this.stopTimer();
            this.pollPaused = false;
            this.confirmStop = false;
            this.returnTo = null;
            this.syncSettings(); // every settings panel goes back to the page
            if (this.keyTarget) this.keyTarget.removeEventListener('keydown', this.onKey, true);
            if (typeof this.doc.removeEventListener === 'function') this.doc.removeEventListener('visibilitychange', this.onVisible);
            const win = this.doc.defaultView;
            if (win && typeof win.removeEventListener === 'function') win.removeEventListener('focus', this.onVisible);
            const root = this.root;
            const opener = this.opener;
            this.opener = null;
            detach(root);
            this.root = this.main = this.statusEl = this.backBtn = this.lessonEls = null;
            if (reason === 'user') {
                this.safeCall('close');
                // after the page's own close handling (a panel it opens instead keeps the focus)
                if (opener) Promise.resolve().then(() => this.giveFocusBack(opener, root));
            }
        }

        giveFocusBack(opener, root) {
            if (this.isOpen || opener.isConnected === false) return;
            if (this.doc.querySelector && this.doc.querySelector(OTHER_OVERLAYS)) return;
            const active = this.doc.activeElement;
            const lost = !active || active === this.doc.body || active === this.doc.documentElement || inside(active, root);
            if (lost) focusNode(opener);
        }

        build() {
            const h = (...a) => this.h(...a);
            this.backBtn = h('button', { type: 'button', class: 'studio-btn studio-btn-quiet studio-back', 'data-action': 'home', 'aria-label': 'Back to your lessons',
                title: 'Back to your lessons', onclick: () => { if (!this.busy) this.goHome(); } },
                h('span', { class: 'studio-btn-icon', 'aria-hidden': 'true', text: '‹' }), h('span', { class: 'studio-label-wide', text: 'Your lessons' }));
            this.statusEl = h('p', { class: 'studio-status', role: 'status', 'aria-live': 'polite' });
            const closeBtn = h('button', { type: 'button', class: 'studio-btn studio-btn-quiet studio-close', 'data-action': 'close', 'aria-label': 'Close the Studio',
                title: 'Close the Studio (Esc)', onclick: () => this.close() },
                h('span', { class: 'studio-btn-icon', 'aria-hidden': 'true', text: '✕' }), h('span', { class: 'studio-label-wide', text: 'Close' }));
            this.main = h('main', { class: 'studio-main' });
            const panel = h('div', { class: 'studio-panel', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'studio-heading' },
                h('header', { class: 'studio-top' },
                    h('span', { class: 'studio-brand' }, h('span', { 'aria-hidden': 'true', text: '✦ ' }), 'Aadhi Studio'),
                    this.backBtn, this.statusEl, closeBtn),
                this.main);
            this.root = h('div', { class: 'studio-root', 'data-studio-runtime': true, 'data-view': this.view }, panel);
            // keys typed inside the Studio never reach the page's slide shortcuts
            this.root.addEventListener('keydown', e => { if (e && typeof e.stopPropagation === 'function') e.stopPropagation(); });
            this.doc.body.appendChild(this.root);
        }

        // ---- views -------------------------------------------------------------------------------------------------

        setView(view) {
            this.seq++;
            this.stopTimer();
            this.view = view;
            this.error = null;
            this.note = '';
            this.confirmStop = false;
            this.returnTo = null;
            this.render();
            this.focusHeading();
        }

        goHome() {
            this.projectId = null;
            this.state = null;
            this.stage = null;
            this.draft = null;
            if (this.progress && !this.isActiveRun(this.progress)) this.progress = null;
            this.setView('home');
            this.refresh();
        }

        // "How would you like to start?"
        startCreate() {
            this.projectId = null;
            this.state = null;
            this.stage = null;
            this.draft = null;
            if (this.progress && !this.isActiveRun(this.progress)) this.progress = null;
            this.setView('create');
            this.refresh();
        }

        showLesson(projectId) {
            if (this.projectId !== projectId) {
                this.state = null;
                this.stage = null;
            }
            this.projectId = projectId;
            this.setView('lesson');
            this.refresh();
        }

        focusHeading() {
            if (!this.root) return;
            focusNode(this.root.querySelector('#studio-heading'));
        }

        // Re-fetches what the open view shows (also called by the page, e.g. after a panel above the Studio closed)
        refresh() {
            if (!this.isOpen) return Promise.resolve();
            this.lastRefresh = Date.now();
            let out = Promise.resolve();
            if (this.view === 'home') out = this.loadLessons();
            else if (this.view === 'create' && !this.lessons) out = this.loadLessons(); // (whether a lesson writer is set up)
            else if (this.view === 'progress') out = this.pollRun();
            else if (this.view === 'lesson') out = this.loadState();
            return Promise.resolve(out).then(() => this.focusBack());
        }

        // A panel the Studio opened above itself (Visual Review, the library) closed and left the focus nowhere: it goes
        // back to the button that opened it
        focusBack() {
            const sel = this.returnTo;
            if (!sel || !this.isOpen || !this.root) return;
            if (this.doc.querySelector && this.doc.querySelector(OTHER_OVERLAYS)) return; // still open
            this.returnTo = null;
            const active = this.doc.activeElement;
            if (active && active !== this.doc.body && active !== this.doc.documentElement) return;
            let node = null;
            try { node = this.root.querySelector(sel); } catch (e) { node = null; }
            focusNode(node && node.getAttribute('disabled') === null ? node : this.root.querySelector('#studio-heading'));
        }

        hidden() {
            return !!this.doc && (this.doc.visibilityState === 'hidden' || this.doc.hidden === true);
        }

        // The tab became visible again, or the window got the focus back (a file dialog, another window)
        regainVisibility() {
            if (!this.isOpen || this.busy || this.hidden()) return;
            const paused = this.pollPaused;
            this.pollPaused = false;
            if (!paused && Date.now() - this.lastRefresh < 1000) return;
            this.refresh();
        }

        stopTimer() {
            if (this.timer !== null) this.clearT(this.timer);
            this.timer = null;
        }

        // One poll; while the tab is hidden nothing is asked (the poll waits until the tab is shown again)
        schedule(ms, fn) {
            this.stopTimer();
            const seq = this.seq;
            this.timer = this.setT(() => {
                this.timer = null;
                if (!this.isOpen || seq !== this.seq) return;
                if (this.hidden()) {
                    this.pollPaused = true;
                    return;
                }
                fn();
            }, ms);
        }

        // An error: what happened (fallback + the plain reason, when there is one), whether the user's work is safe (safe,
        // unless the words already say it), and what to do (retry → Try again; alt → another way, {label, action, run})
        setError(fallback, err, retry, kind = 'action', safe = '', alt = null) {
            const text = plainError(fallback, err);
            this.error = { text, safe: safe && !SAFE_SAID.test(text) ? safe : '', retry, alt, raw: rawText(err), kind };
        }

        setNote(text) {
            this.note = text || '';
            this.renderStatus();
        }

        renderStatus() {
            if (!this.statusEl) return;
            this.statusEl.textContent = this.busy ? '⟳ Working…' : this.note;
        }

        // Buttons are disabled while an action is in flight (in place: nothing is rebuilt, focus stays)
        applyBusy() {
            if (!this.root) return;
            this.root.setAttribute('aria-busy', this.busy ? 'true' : 'false');
            this.root.querySelectorAll('.studio-btn').forEach(b => { // never the page's own settings controls
                const action = b.getAttribute('data-action');
                if (!action || action === 'close' || b.getAttribute('data-off') === 'true') return;
                if (this.busy) b.setAttribute('disabled', '');
                else b.removeAttribute('disabled');
            });
            this.renderStatus();
        }

        // One button; data-action is the hook the page's browser checks use. extra: primary, className (studio-btn-quiet,
        // studio-btn-link, studio-btn-danger, studio-btn-lg), off (shown but unavailable), label (aria-label), title, attrs
        btn(label, action, onclick, extra = {}) {
            const off = extra.off === true;
            const props = { type: 'button', class: `studio-btn${extra.primary ? ' studio-btn-primary' : ''}${extra.className ? ' ' + extra.className : ''}`,
                'data-action': action, 'aria-label': extra.label || null, title: extra.title || null, disabled: off || (!!this.busy && action !== 'close'),
                'data-off': off ? 'true' : null,
                onclick: () => {
                    if (this.busy) return;
                    this.clicked = selectorFor(action, extra.attrs);
                    onclick();
                } };
            Object.keys(extra.attrs || {}).forEach(k => { props[k] = extra.attrs[k]; });
            return this.h('button', props, label);
        }

        errorBox() {
            if (!this.error || this.error.field) return null; // (an error about one field is said under that field)
            const h = (...a) => this.h(...a);
            const e = this.error;
            const buttons = [
                typeof e.retry === 'function' ? this.btn('Try again', 'retry', () => { this.error = null; e.retry(); }) : null,
                e.alt ? this.btn(e.alt.label, e.alt.action, () => e.alt.run()) : null
            ].filter(Boolean);
            return h('div', { class: 'studio-error', role: 'alert' },
                h('p', { class: 'studio-error-text', text: `⚠ ${e.text}` }),
                e.safe ? h('p', { class: 'studio-error-safe', text: e.safe }) : null,
                buttons.length ? h('div', { class: 'studio-actions' }, buttons) : null,
                this.debug && e.raw ? h('code', { class: 'studio-debug', text: e.raw }) : null);
        }

        heading(text) {
            return this.h('h2', { class: 'studio-heading', id: 'studio-heading', tabindex: '-1', 'data-focus': 'heading', text });
        }

        // No lesson writer on this server (listLessons said so, or writing was refused with a 503)
        writerMissing() {
            return this.noWriter || !!(this.lessons && this.lessons.writer && !this.lessons.writer.available);
        }

        writerNote() {
            if (!this.writerMissing()) return null;
            return this.h('p', { class: 'studio-note studio-writer-note', role: 'note', text: NO_WRITER_NOTE });
        }

        render() {
            if (!this.root || !this.main) return;
            const focus = this.focusKey();
            this.root.setAttribute('data-view', this.view);
            setHidden(this.backBtn, this.view === 'home');
            if (this.view !== 'lesson') {
                this.syncSettings();
                this.lessonEls = null;
                clear(this.main);
            }
            if (this.view === 'home') this.renderHome();
            else if (this.view === 'create') this.renderCreate();
            else if (this.view === 'name') this.renderForm('name');
            else if (this.view === 'paste') this.renderForm('paste');
            else if (this.view === 'progress') this.renderProgress();
            else if (this.view === 'lesson') this.renderLesson();
            this.applyBusy();
            this.restoreFocus(focus);
        }

        // Keyboard focus survives a redraw: the same control again
        focusKey() {
            const active = this.doc.activeElement;
            if (!active || !this.root || typeof active.getAttribute !== 'function') return null;
            if (typeof this.root.contains === 'function' && !this.root.contains(active)) return null;
            const parts = ['data-action', 'data-stage', 'data-item', 'data-project-id', 'data-focus', 'data-field']
                .map(a => (active.getAttribute(a) ? `[${a}="${active.getAttribute(a)}"]` : '')).join('');
            const tag = String(active.tagName || active.tag || '').toLowerCase();
            return parts ? `${/^[a-z0-9]+$/.test(tag) ? tag : ''}${parts}` : null;
        }

        restoreFocus(selector) {
            if (!selector || !this.root) return;
            let node = null;
            try { node = this.root.querySelector(selector); } catch (e) { node = null; }
            if (node && node !== this.doc.activeElement) focusNode(node);
        }

        // ---- Home ----------------------------------------------------------------------------------------------------

        renderHome() {
            const h = (...a) => this.h(...a);
            const data = this.lessons;
            const empty = !!data && !data.failed && !data.lessons.length && !data.runs.length;
            this.main.appendChild(h('div', { class: 'studio-view studio-home' },
                h('div', { class: 'studio-view-head' },
                    h('div', { class: 'studio-view-titles' },
                        this.heading('Your lessons'),
                        empty ? null : h('p', { class: 'studio-intro', text: 'Continue a lesson, or turn a document or your notes into a new one.' })),
                    // (the empty state has its own: one "Create a lesson" on the screen)
                    empty ? null : this.btn('Create a lesson', 'create', () => this.startCreate(), { primary: true, className: 'studio-btn-lg' })),
                this.errorBox(),
                this.writerNote(),
                this.lessonList()));
        }

        lessonList() {
            const h = (...a) => this.h(...a);
            const data = this.lessons;
            if (!data) return h('p', { class: 'studio-muted studio-loading', text: 'Loading your lessons…' });
            if (!data.lessons.length && !data.runs.length) {
                if (data.failed) return null; // (the error says so: never "you haven't created a lesson")
                return h('div', { class: 'studio-empty' },
                    h('p', { class: 'studio-empty-text', text: "You haven't created a lesson yet. Turn a document or your notes into an educational video." }),
                    h('div', { class: 'studio-actions' }, this.btn('Create a lesson', 'create', () => this.startCreate(), { primary: true, className: 'studio-btn-lg' })));
            }
            const listEl = h('ul', { class: 'studio-lesson-list', 'aria-label': 'Your lessons' });
            const remembered = this.rememberedRun();
            data.runs.forEach((run, i) => {
                const attention = run.status === 'needs_attention';
                // its title, when this Studio started it (or remembers it)
                const mine = this.progress && this.progress.runId === run.runId && this.progress.title ? this.progress.title : '';
                const title = mine || (remembered && remembered.run_id === run.runId && remembered.title ? remembered.title : 'A new lesson');
                const verb = attention ? 'See what happened' : 'See progress';
                listEl.appendChild(h('li', { class: 'studio-lesson-item studio-run-item', 'data-item': `run-${i}` },
                    h('div', { class: 'studio-lesson-info' },
                        h('span', { class: 'studio-lesson-title', text: title }),
                        h('span', { class: 'studio-lesson-meta', text: attention ? 'Writing was interrupted' : STEPS[stepIndex(run.stage) + 1].label }),
                        this.debug ? h('code', { class: 'studio-debug', text: run.runId }) : null),
                    h('span', { class: 'studio-chip', 'data-lesson-stage': attention ? 'needs_attention' : 'writing', text: attention ? '⚠ Interrupted' : LESSON_STAGE.writing }),
                    this.btn(verb, 'follow-run', () => this.followRun(run), { attrs: { 'data-item': `run-${i}` }, label: `${verb}: ${title}` })));
            });
            data.lessons.forEach(lesson => {
                const meta = [plural(lesson.scenes, 'scene'), lesson.updated ? `Updated ${lesson.updated}` : null].filter(Boolean).join(' · ');
                const verb = lesson.stage === 'completed' ? 'Open' : 'Continue';
                listEl.appendChild(h('li', { class: 'studio-lesson-item', 'data-project-id': String(lesson.projectId) },
                    h('div', { class: 'studio-lesson-info' },
                        h('span', { class: 'studio-lesson-title', text: lesson.title }),
                        h('span', { class: 'studio-lesson-meta', text: meta })),
                    h('span', { class: 'studio-chip', 'data-lesson-stage': lesson.stage || 'draft', text: lessonChip(lesson.stage) }),
                    this.btn(verb, 'open-lesson', () => this.openLesson(lesson.projectId), { label: `${verb} “${lesson.title}”`,
                        attrs: { 'data-project-id': String(lesson.projectId) } })));
            });
            return listEl;
        }

        async loadLessons() {
            const seq = this.seq;
            const home = this.view === 'home';
            if (!this.lessons && home) this.render();
            let raw;
            try {
                raw = await this.invoke('listLessons');
            } catch (e) {
                if (seq !== this.seq || !this.isOpen || !home) return; // (Create only wanted to know about the writer)
                if (!this.lessons) this.lessons = { lessons: [], runs: [], writer: null, failed: true };
                this.setError("We couldn't load your lessons.", e, () => this.refresh(), 'load', 'Your lessons are not affected.');
                this.render();
                return;
            }
            if (seq !== this.seq || !this.isOpen) return;
            this.lessons = normalizeLessons(raw);
            if (this.lessons.writer && this.lessons.writer.available) this.noWriter = false;
            if (this.error && this.error.kind === 'load') this.error = null;
            this.render();
            if (home && this.lessons.runs.some(r => RUN_ACTIVE.includes(r.status) || !r.status)) this.schedule(this.statePollMs, () => this.refresh());
        }

        openLesson(projectId) {
            return this.act('open-lesson', async () => {
                await this.invoke('openLesson', projectId);
                this.lastCurrent = projectId;
                this.showLesson(projectId);
            }, "We couldn't open this lesson.", { refresh: false, safe: 'Your lesson is not affected.' });
        }

        openLessonFile() {
            return this.act('open-file', async () => {
                const out = await this.invoke('openLessonFile');
                const pid = plain(out) ? projectIdOf(out.projectId !== undefined ? out.projectId : out.project_id) : null;
                if (pid) this.fileLessons.add(pid);
                if (pid && this.isOpen) {
                    this.lastCurrent = pid;
                    this.showLesson(pid);
                }
            }, "We couldn't open the lesson file.", { safe: 'Your lessons are not affected.' });
        }

        // ---- create: "How would you like to start?" ------------------------------------------------------------------

        renderCreate() {
            const h = (...a) => this.h(...a);
            // one choice: a single large button (its title names it; its line says what happens)
            const choice = (key, action, icon, title, text, onclick) => h('li', { class: 'studio-choice-item' },
                this.btn([
                    h('span', { class: 'studio-choice-icon', 'aria-hidden': 'true', text: icon }),
                    h('span', { class: 'studio-choice-body' },
                        h('span', { class: 'studio-choice-title', id: `studio-choice-${key}`, text: title }),
                        h('span', { class: 'studio-choice-text', id: `studio-choice-${key}-text`, text }))
                ], action, onclick, { className: 'studio-choice', attrs: { 'data-choice': key, 'aria-labelledby': `studio-choice-${key}`, 'aria-describedby': `studio-choice-${key}-text` } }));
            this.main.appendChild(h('div', { class: 'studio-view studio-create' },
                this.heading('How would you like to start?'),
                h('p', { class: 'studio-intro', text: 'Aadhi prepares the lesson; you can review and change everything before you export.' }),
                this.errorBox(),
                this.writerNote(),
                h('ul', { class: 'studio-choices', 'aria-label': 'Ways to start' },
                    choice('document', 'from-document', '📄', 'Upload a document',
                        'A PDF, Word or text file. Aadhi reads its structure first; your original file is kept.', () => this.fromDocument()),
                    choice('structured', 'from-text', '📝', 'Paste your notes',
                        'Notes or lesson content. Headings, lists, tables, formulas and code are kept.', () => this.startPaste()),
                    this.has('openLessonFile')
                        ? choice('file', 'open-file', '📂', 'Open a lesson file', 'A lesson file (.json) saved from Aadhi.', () => this.openLessonFile())
                        : null)));
        }

        // ---- create: from a document ----------------------------------------------------------------------------------

        fromDocument() {
            return this.act('from-document', async () => {
                const out = await this.invoke('analyzeDocument');
                if (!plain(out) || !plain(out.source)) return; // the assistant was closed without choosing: nothing changes
                const source = { document_id: out.source.document_id, analysis_id: out.source.analysis_id };
                const title = str(out.title, 200).trim();
                this.draft = { kind: 'name', source, title, text: '', names: prefillNames(title), touched: true };
                this.setView('name');
            }, "We couldn't open the document assistant.", { refresh: false, safe: 'Nothing has changed.' });
        }

        // ---- create: from your notes ------------------------------------------------------------------------------------

        startPaste() {
            this.draft = { kind: 'paste', source: null, title: '', text: '', names: prefillNames(''), touched: false };
            this.setView('paste');
        }

        renderForm(kind) {
            const h = (...a) => this.h(...a);
            const d = this.draft || { names: prefillNames(''), text: '' };
            const field = (key, label, extra = {}) => h('label', { class: 'studio-field' },
                h('span', { class: 'studio-field-label', text: label }),
                h('input', { type: 'text', 'data-field': key, value: d.names[key] || '', maxlength: extra.max || 200, placeholder: extra.placeholder || null,
                    autocomplete: 'off',
                    oninput: e => { d.names[key] = String((e && e.target ? e.target : {}).value || ''); if (key === 'session_title') d.touched = true; } }));
            const names = h('fieldset', { class: 'studio-names' },
                h('legend', { class: 'studio-legend', text: 'Lesson names' }),
                h('div', { class: 'studio-fields' },
                    field('subject_name', 'Subject', { placeholder: 'For example: Biology' }),
                    field('unit_name', 'Unit', { placeholder: 'Optional' }),
                    field('session_number', 'Session', { max: 40 }),
                    field('session_title', 'Session title')),
                h('p', { class: 'studio-hint', text: 'The subject and the session title open the video.' }));
            const blocked = this.writerMissing();
            const actions = h('div', { class: 'studio-actions' },
                this.btn('Write the lesson', 'start-lesson', () => this.submitForm(), { primary: true, off: blocked }),
                this.btn('Cancel', 'cancel-form', () => this.goHome()));
            const form = h('form', { class: 'studio-form', onsubmit: e => {
                if (e && typeof e.preventDefault === 'function') e.preventDefault();
                if (!this.busy && !this.writerMissing()) this.submitForm();
            } });
            this.fieldError = null;
            if (kind === 'paste') {
                this.jsonNote = h('p', { class: 'studio-hint studio-json-note', hidden: !looksLikeLessonJson(d.text) },
                    'This looks like a saved lesson file. ',
                    this.has('openLessonFile') ? this.btn('Open a lesson file', 'open-file', () => this.openLessonFile(), { className: 'studio-btn-link' }) : 'Open it with "Open a lesson file" instead.');
                // an error about the notes themselves (none, too long) is said under the box, which is marked invalid
                const wrong = this.error && this.error.field === 'text' ? this.error : null;
                const area = h('textarea', { class: 'studio-text', 'data-field': 'text', rows: '12', 'aria-describedby': wrong ? 'studio-text-error studio-text-hint' : 'studio-text-hint',
                    'aria-invalid': wrong ? 'true' : null, value: d.text,
                    oninput: e => this.pasteChanged(String((e && e.target ? e.target : {}).value || '')) });
                form.appendChild(h('label', { class: 'studio-field studio-field-wide' }, h('span', { class: 'studio-field-label', text: 'Your notes or content' }), area));
                if (wrong) {
                    this.fieldError = h('p', { class: 'studio-error studio-field-error', id: 'studio-text-error', role: 'alert', text: `⚠ ${wrong.text}` });
                    form.appendChild(this.fieldError);
                }
                form.appendChild(h('p', { class: 'studio-hint', id: 'studio-text-hint', text: 'Headings, lists, tables, formulas and code are kept. The lesson is written from this text.' }));
                form.appendChild(this.jsonNote);
            }
            form.appendChild(names);
            // what happens next (or, with no lesson writer here, why it cannot)
            form.appendChild(blocked ? this.writerNote()
                : h('p', { class: 'studio-hint studio-next', text: 'Next, Aadhi writes the lesson. You can review and change everything before you export.' }));
            form.appendChild(actions);
            const intro = kind === 'name'
                ? `From your document${d.title ? `: “${d.title}”` : ''}. Check the names, then write the lesson.`
                : 'Paste the notes or content the lesson should teach.';
            this.main.appendChild(h('div', { class: `studio-view studio-${kind}` },
                this.heading(kind === 'name' ? 'Name your lesson' : 'Paste your notes'),
                h('p', { class: 'studio-intro', text: intro }),
                this.errorBox(),
                form));
        }

        // Typing in the paste box: the session title follows its first line until the user changes it (the subject is
        // left to the user: the same words in both would read twice on the title card)
        pasteChanged(text) {
            const d = this.draft;
            if (!d) return;
            d.text = text;
            if (this.error && this.error.field === 'text') {
                // typed again: the notes' error goes (in place: the caret stays); "Write the lesson" checks them again
                this.error = null;
                detach(this.fieldError);
                this.fieldError = null;
                const area = this.root ? this.root.querySelector('[data-field="text"]') : null;
                if (area) {
                    area.removeAttribute('aria-invalid');
                    area.setAttribute('aria-describedby', 'studio-text-hint');
                }
            }
            if (!d.touched) {
                const line = firstLine(text);
                d.names.session_title = line;
                const input = this.root ? this.root.querySelector('[data-field="session_title"]') : null;
                if (input) input.value = line;
            }
            if (this.jsonNote) setHidden(this.jsonNote, !looksLikeLessonJson(text));
        }

        submitForm() {
            const d = this.draft;
            if (!d || this.writerMissing()) return undefined;
            // field: the error is about that field (said under it; the focus moves there)
            const refuse = (text, field = null) => {
                this.error = { text, safe: '', retry: null, alt: null, raw: '', kind: 'form', field };
                this.render();
                if (field && this.root) focusNode(this.root.querySelector(`[data-field="${field}"]`));
                return undefined;
            };
            const text = d.kind === 'paste' ? String(d.text || '') : '';
            if (d.kind === 'paste' && !text.trim()) return refuse('Paste the lesson content first.', 'text');
            if (text.length > MAX_TEXT) return refuse('This content is too long for one lesson. Split it into smaller parts (up to about 250,000 characters each).', 'text');
            const names = cleanNames(d.names);
            if (!names.subject_name && !names.session_title) return refuse('Give the lesson a subject or a session title first.');
            if (!names.session_number) names.session_number = 'Session 1';
            const title = names.session_title || names.subject_name;
            if (d.kind === 'paste') return this.startLesson({ text, names }, false, title);
            return this.startLesson({ source: d.source, names }, true, title);
        }

        startLesson(request, fromSource, title) {
            return this.act('start-lesson', async () => {
                const out = await this.invoke('startLesson', request);
                const runId = plain(out) && typeof out.run_id === 'string' ? out.run_id : '';
                if (!runId) throw new Error('');
                this.progress = { runId, fromSource: !!fromSource, request, title: str(title, 200), run: null, resumed: false };
                this.rememberRun(this.progress);
                this.draft = null;
                this.setView('progress'); // (closed meanwhile: the next open() shows the progress)
                if (this.isOpen) this.pollRun();
            }, "We couldn't start writing the lesson.", { refresh: false, safe: 'What you entered is still here.', failed: e => {
                // no lesson writer on this server (503): trying again cannot work; the user is pointed back to the lessons
                if (!e || e.status !== 503 || !this.error) return;
                this.noWriter = true;
                this.error.retry = null;
                this.error.alt = { label: 'Back to your lessons', action: 'back-home', run: () => this.goHome() };
            } });
        }

        // ---- progress -----------------------------------------------------------------------------------------------

        isActiveRun(p) {
            const status = p && p.run ? p.run.status : '';
            return !RUN_END.includes(status);
        }

        followRun(run) {
            if (this.progress && this.progress.runId === run.runId) {
                // the run started here: its request is still known (Try again sends it again)
                this.setView('progress');
                this.pollRun();
                return;
            }
            const saved = this.rememberedRun();
            const mine = saved && saved.run_id === run.runId;
            this.progress = { runId: run.runId, fromSource: mine ? !!saved.from_source : null, request: null,
                title: mine ? str(saved.title, 200) : '', run: normalizeRun({ run_id: run.runId, status: run.status, stage: run.stage }), resumed: false };
            this.setView('progress');
            this.pollRun();
        }

        async pollRun() {
            const p = this.progress;
            if (!p || !p.runId || this.view !== 'progress') return;
            const seq = this.seq;
            let raw;
            try {
                raw = await this.invoke('runStatus', p.runId);
            } catch (e) {
                if (seq !== this.seq || this.progress !== p || !this.isOpen) return;
                this.setError("We couldn't check on the lesson. It keeps being written in the background.", e, () => this.refresh(), 'load');
                this.render();
                return;
            }
            if (seq !== this.seq || this.progress !== p || !this.isOpen) return;
            const run = normalizeRun(raw);
            if (!run.status) run.status = 'running';
            const first = p.resumed;
            p.resumed = false;
            p.run = run;
            if (this.error && this.error.kind === 'load') this.error = null;
            if (RUN_END.includes(run.status)) {
                this.confirmStop = false;
                if (this.note === STOPPING) this.note = ''; // (the heading now says how it ended)
                this.forgetRun(run.runId || p.runId);
                if (first) {
                    // it ended while the page was away: the lesson (if any) is in Your lessons; nothing opens by itself
                    this.progress = null;
                    this.setView('home');
                    this.refresh();
                    return;
                }
            }
            this.render();
            if (run.status === 'completed') {
                await this.finishRun(p);
                return;
            }
            if (!RUN_END.includes(run.status)) this.schedule(this.runPollMs, () => this.pollRun());
        }

        // The lesson is written and saved: the page opens it, then its stages
        async finishRun(p) {
            const pid = p.run ? p.run.projectId : null;
            if (!pid) {
                this.setError("Your lesson was written, but it couldn't be opened.", null, () => this.refresh(), 'load');
                this.render();
                return;
            }
            try {
                await this.invoke('openLesson', pid);
            } catch (e) {
                if (!this.isOpen || this.progress !== p) return;
                this.setError("Your lesson was written and saved, but it couldn't be opened.", e, () => this.finishRun(p), 'action');
                this.render();
                return;
            }
            if (this.progress !== p) return;
            this.progress = null;
            this.lastCurrent = pid;
            if (this.isOpen) this.showLesson(pid);
            else {
                this.projectId = pid;
                this.state = null;
                this.stage = null;
                this.view = 'lesson';
            }
        }

        // "Stop writing" asks first, in the panel (a cancelled run saves nothing)
        askStop() {
            if (!this.progress || this.busy) return;
            this.confirmStop = true;
            this.render();
            focusNode(this.root && this.root.querySelector('[data-action="keep-writing"]'));
        }

        keepWriting() {
            this.confirmStop = false;
            this.render();
            focusNode(this.root && this.root.querySelector('[data-action="cancel-run"]'));
        }

        cancelRun() {
            const p = this.progress;
            if (!p) return undefined;
            this.confirmStop = false;
            return this.act('cancel-run', async () => {
                await this.invoke('cancelRun', p.runId);
                this.setNote(STOPPING);
            }, "We couldn't stop writing the lesson.", { refresh: true, safe: 'The lesson keeps being written.' });
        }

        retryRun() {
            const p = this.progress;
            if (p && p.request) return this.startLesson(p.request, p.fromSource, p.title);
            // followed from the list after a reload: the content is not known here, so it is chosen again
            this.progress = null;
            this.startCreate();
            this.setNote('Choose the content again to write the lesson once more.');
            return undefined;
        }

        renderProgress() {
            const h = (...a) => this.h(...a);
            const p = this.progress;
            if (!p) {
                this.main.appendChild(h('div', { class: 'studio-view studio-progress' }, this.heading('Writing your lesson'), this.errorBox()));
                return;
            }
            const run = p.run || { status: 'queued', stage: '' };
            const status = run.status || 'queued';
            const ended = RUN_END.includes(status) && status !== 'completed';
            const active = !RUN_END.includes(status);
            const steps = runSteps(run, p.fromSource === true);
            const listEl = h('ol', { class: 'studio-steps', 'aria-label': 'Progress' },
                steps.map(s => h('li', { class: 'studio-step', 'data-step': s.key, 'data-status': s.status, 'aria-current': s.status === 'active' ? 'step' : null },
                    h('span', { class: 'studio-step-label', text: s.label }),
                    h('span', { class: 'studio-step-state', text: s.text }))));
            let outcome = null;
            if (ended) {
                const text = status === 'cancelled' ? 'You stopped writing this lesson. Nothing was saved; your document or notes are safe.'
                    : status === 'needs_attention' ? 'Writing this lesson was interrupted. Your document or notes are safe.'
                        : WRITE_FAILED;
                outcome = h('div', { class: 'studio-error studio-run-failed', role: 'alert', 'data-status': status },
                    h('p', { class: 'studio-error-text', text: `⚠ ${text}` }),
                    h('div', { class: 'studio-actions' },
                        this.btn('Try again', 'retry-run', () => this.retryRun(), { primary: true }),
                        this.btn('Back to your lessons', 'back-home', () => this.goHome())),
                    this.debug && run.message ? h('code', { class: 'studio-debug', text: run.message }) : null);
            } else if (status === 'completed') {
                outcome = h('p', { class: 'studio-done', text: '✓ Your lesson is ready. Opening it…' });
            }
            const stopping = status === 'cancel_requested';
            const asking = active && this.confirmStop && !stopping;
            const confirm = asking ? h('div', { class: 'studio-confirm', role: 'group', 'aria-labelledby': 'studio-confirm-text' },
                h('p', { class: 'studio-confirm-text', id: 'studio-confirm-text', text: STOP_QUESTION }),
                h('div', { class: 'studio-actions' },
                    this.btn('Stop writing', 'confirm-cancel-run', () => this.cancelRun(), { className: 'studio-btn-danger' }),
                    this.btn('Keep writing', 'keep-writing', () => this.keepWriting()))) : null;
            const heading = ended ? (status === 'cancelled' ? 'Writing stopped' : 'Something needs attention')
                : status === 'completed' ? 'Your lesson is written' : 'Writing your lesson';
            this.main.appendChild(h('div', { class: 'studio-view studio-progress', 'data-status': status },
                this.heading(heading),
                p.title ? h('p', { class: 'studio-intro studio-progress-title', text: p.title }) : null,
                this.errorBox(),
                listEl,
                outcome,
                active ? h('p', { class: 'studio-hint studio-leave', text: 'You can safely leave this page: the lesson keeps being written.' }) : null,
                active ? h('div', { class: 'studio-actions' },
                    this.btn('Continue later', 'continue-later', () => this.goHome(), { title: 'Back to your lessons (the lesson keeps being written)' }),
                    this.btn(stopping ? STOPPING : 'Stop writing', 'cancel-run', () => this.askStop(), { off: stopping || asking,
                        className: 'studio-btn-quiet', label: 'Stop writing this lesson' })) : null,
                confirm,
                this.debug ? h('code', { class: 'studio-debug', text: `run ${p.runId} · ${status}${run.stage ? ' · ' + run.stage : ''}` }) : null));
        }

        // ---- the lesson: stage rail ---------------------------------------------------------------------------------

        async loadState() {
            const seq = this.seq;
            const pid = this.projectId;
            if (!pid) return;
            let raw;
            try {
                raw = await this.invoke('lessonState', pid);
            } catch (e) {
                if (seq !== this.seq || pid !== this.projectId || !this.isOpen) return;
                this.setError("We couldn't load this lesson's progress.", e, () => this.refresh(), 'load', 'Your lesson is not affected.');
                this.render();
                return;
            }
            if (seq !== this.seq || pid !== this.projectId || !this.isOpen) return;
            const before = this.state;
            this.state = normalizeState(raw);
            // the style changed (the Style stage's panel): said once, quietly
            const styleOf = s => (s && s.style ? s.style.style : '');
            if (before && styleOf(before) !== styleOf(this.state)) {
                this.note = this.state.style ? `✓ Style updated: ${styleName(this.state.style.style)}.` : '✓ Using the default style.';
            }
            if (this.error && this.error.kind === 'load') this.error = null;
            this.render();
            if (this.state.media.generating > 0) this.schedule(this.statePollMs, () => this.refresh());
        }

        stages() {
            if (!this.state) return [];
            this.shownLayout = this.layoutOf();
            return lessonStages(this.state, { previewed: this.previewedVersion(this.projectId), layout: this.shownLayout });
        }

        // A choice made in the settings panel the open stage hosts: the Layout (Classic / Cinematic) changes what the Style
        // stage can truthfully say, so the stages are drawn again (the page's panel itself stays as it is)
        settingsChanged() {
            if (!this.isOpen || this.view !== 'lesson' || !this.state || this.layoutOf() === this.shownLayout) return;
            this.render();
        }

        renderLesson() {
            const h = (...a) => this.h(...a);
            let L = this.lessonEls;
            if (!L || parentOf(L.wrap) !== this.main) {
                this.syncSettings(true);
                clear(this.main);
                L = this.lessonEls = {};
                L.head = h('div', { class: 'studio-lesson-head' });
                // narrow screens: the stages as one select ("Stage 3 of 7"); wider: the rail (a stepper on tablets)
                L.select = h('select', { class: 'studio-stage-select', 'data-focus': 'stage-select',
                    onchange: e => this.selectStage(String((e && e.target ? e.target : L.select).value || '')) });
                L.pickerLabel = h('span', { class: 'studio-stage-picker-label' });
                L.picker = h('label', { class: 'studio-stage-picker' }, L.pickerLabel, L.select);
                L.rail = h('ol', { class: 'studio-rail-list' });
                L.detailMain = h('div', { class: 'studio-detail-main' });
                L.slot = h('div', { class: 'studio-settings-slot', onchange: () => this.settingsChanged() });
                L.detail = h('section', { class: 'studio-detail', 'aria-labelledby': 'studio-detail-title' }, L.detailMain, L.slot);
                L.body = h('div', { class: 'studio-lesson-body' }, h('nav', { class: 'studio-rail', 'aria-label': 'Lesson stages' }, L.picker, L.rail), L.detail);
                L.wrap = h('div', { class: 'studio-view studio-lesson' }, L.head, L.body);
                this.main.appendChild(L.wrap);
            }
            clear(L.head);
            clear(L.rail);
            clear(L.detailMain);
            clear(L.select);
            const st = this.state;
            if (!st) {
                L.head.appendChild(this.heading(this.error ? 'Your lesson' : 'Loading the lesson…'));
                const err = this.errorBox();
                if (err) L.head.appendChild(err);
                setHidden(L.body, true);
                this.syncSettings();
                return;
            }
            setHidden(L.body, false);
            const stages = this.stages();
            if (!this.stage || !STAGES.some(s => s.key === this.stage)) this.stage = defaultStage(stages, st.stage);
            L.head.appendChild(h('div', { class: 'studio-lesson-title-row' },
                this.heading(st.title),
                h('span', { class: 'studio-chip', 'data-lesson-stage': st.stage || 'draft', text: lessonChip(st.stage) })));
            L.head.appendChild(h('p', { class: 'studio-lesson-meta', text: [plural(st.sceneCount, 'scene'), st.source && st.source.fileName ? `From “${st.source.fileName}”` : null].filter(Boolean).join(' · ') }));
            if (this.debug) {
                L.head.appendChild(h('code', { class: 'studio-debug', text: `project ${st.projectId || this.projectId} · revision ${st.revision || '-'} · fingerprint ${st.fingerprint || '-'}` }));
            }
            const err = this.errorBox();
            if (err) L.head.appendChild(err);
            const s = stages.find(x => x.key === this.stage) || stages[0];
            const next = stages.find(x => x.next) || null;
            L.pickerLabel.textContent = `Stage ${s.number} of ${stages.length}`;
            stages.forEach(x => {
                const current = x.key === s.key;
                L.select.appendChild(h('option', { value: x.key, selected: current }, `${x.number}. ${x.name} — ${x.text}${x.next ? ' · Next step' : ''}`));
                L.rail.appendChild(h('li', { class: 'studio-rail-item' },
                    h('button', { type: 'button', class: `studio-stage${current ? ' is-current' : ''}${x.next ? ' is-next' : ''}`, 'data-stage': x.key,
                        'data-status': x.status, 'data-state': x.state, 'data-next': x.next ? 'true' : null, 'aria-current': current ? 'step' : 'false',
                        'aria-label': `${x.number} ${x.name}: ${x.text.slice(2)}. ${x.summary}${x.next ? ' Suggested next step.' : ''}`,
                        onclick: () => this.selectStage(x.key) },
                        h('span', { class: 'studio-stage-num', 'aria-hidden': 'true', text: String(x.number) }),
                        h('span', { class: 'studio-stage-text' },
                            h('span', { class: 'studio-stage-line' },
                                h('span', { class: 'studio-stage-name', text: x.name }),
                                h('span', { class: 'studio-stage-short', 'aria-hidden': 'true', text: x.short }),
                                x.next ? h('span', { class: 'studio-stage-next', text: 'Next step' }) : null),
                            h('span', { class: 'studio-stage-status', text: x.text }),
                            h('span', { class: 'studio-stage-summary', text: x.summary })))));
            });
            L.select.value = s.key;
            L.detail.setAttribute('data-stage', s.key);
            L.detailMain.appendChild(h('h3', { class: 'studio-detail-title', id: 'studio-detail-title', tabindex: '-1', 'data-focus': 'detail', text: `${s.number} · ${s.name}` }));
            L.detailMain.appendChild(h('div', { class: 'studio-detail-meta' },
                h('p', { class: 'studio-detail-status', 'data-status': s.status, 'data-state': s.state, text: s.text }),
                s.next ? h('span', { class: 'studio-detail-next', text: 'Suggested next step' }) : null));
            // (Content: its summary is where the scenes come from, said once)
            L.detailMain.appendChild(h('p', { class: `studio-detail-summary${s.key === 'content' ? ' studio-origin' : ''}`, text: s.summary }));
            L.detailMain.appendChild(h('p', { class: `studio-help${s.key === 'style' ? ' studio-style-note' : ''}`, text: s.help }));
            const body = this.stageBody(s, st);
            if (body) L.detailMain.appendChild(body);
            if (next && next.key !== s.key) {
                // the suggestion comes after the open stage: "Next step"; before it: still to do (never "next" pointing back)
                const back = next.number < s.number;
                L.detailMain.appendChild(h('div', { class: 'studio-actions studio-next-stage' },
                    this.btn(back ? `Still to do: ${next.number} · ${next.name} →` : `Next step: ${next.name} →`, 'next-stage', () => this.selectStage(next.key, true), {
                        className: 'studio-btn-link', label: back ? `Still to do: go back to stage ${next.number}, ${next.name}` : `Go to the suggested next step: ${next.number} ${next.name}` })));
            }
            this.syncSettings();
        }

        // Any stage opens directly (focusDetail: the focus moves to its title, e.g. from "Next step")
        selectStage(key, focusDetail = false) {
            if (!STAGES.some(s => s.key === key) || key === this.stage) return;
            this.stage = key;
            this.note = '';
            this.render();
            this.renderStatus();
            if (focusDetail && this.root) focusNode(this.root.querySelector('#studio-detail-title'));
        }

        stageBody(s, st) {
            const h = (...a) => this.h(...a);
            const body = (...children) => h('div', { class: 'studio-stage-body' }, children);
            const actions = (...b) => h('div', { class: 'studio-actions' }, b);
            const fp = st.fingerprint || null;
            const done = s.status === 'done';
            if (s.key === 'content') {
                // where the lesson came from, when that is known: its document, a lesson file opened here, the text it is traced to
                const from = st.source ? st.source.fileName || 'Your document' : this.fileLessons.has(this.projectId) ? 'A lesson file'
                    : st.origin && st.origin.source > 0 ? 'Your text' : 'Not recorded';
                return body(
                    h('dl', { class: 'studio-facts' },
                        h('dt', { text: 'Made from' }), h('dd', { text: from }),
                        h('dt', { text: 'Scenes' }), h('dd', { text: String(st.sceneCount) })),
                    actions(this.btn(done ? 'Confirm again' : 'Confirm structure', 'confirm-structure',
                        () => this.checkpoint('structure', { scenes: st.sceneCount }, '✓ Structure confirmed.'), { primary: !done, off: !st.sceneCount })));
            }
            if (s.key === 'lesson') {
                return body(actions(
                    this.btn('Open the editor', 'open-editor', () => this.stageAction('openEditor', "We couldn't open the editor."), { primary: !done }),
                    done ? null : this.btn('Looks right', 'confirm-lesson', () => this.checkpoint('lesson', { scenes: st.sceneCount, fingerprint: fp }, '✓ Marked as looking right.'),
                        { off: !st.sceneCount, title: 'Mark this version of the lesson as checked' })));
            }
            if (s.key === 'visuals') {
                const m = st.media;
                const attention = Math.max(m.attention, m.attentionItems.filter(i => !fallbackItem(i)).length);
                // every number of the stage's summary, so they add up (`needed`: still to prepare)
                const counts = h('p', { class: 'studio-counts' },
                    h('span', { class: 'studio-count', 'data-count': 'needed', text: `○ ${m.needed} to prepare` }), ' · ',
                    h('span', { class: 'studio-count', 'data-count': 'ready', text: `✓ ${m.ready} ready` }), ' · ',
                    h('span', { class: 'studio-count', 'data-count': 'generating', text: `● ${m.generating} being made` }), ' · ',
                    h('span', { class: 'studio-count', 'data-count': 'attention',
                        text: attention ? `⚠ ${attention} ${attention === 1 ? 'needs' : 'need'} attention` : '✓ Nothing needs attention' }));
                const items = m.attentionItems.slice(0, 50);
                const listEl = items.length ? h('ul', { class: 'studio-attention', 'aria-label': 'Needs attention' },
                    items.map((item, i) => {
                        const message = this.debug ? item.message : plainDetail(item.message);
                        const noRun = !item.runId;
                        return h('li', { class: 'studio-attention-item', 'data-item': `media-${i}`, 'data-scene-index': item.sceneIndex === null ? null : String(item.sceneIndex) },
                            h('p', { class: 'studio-attention-text', text: `⚠ ${mediaItemText(item)}` }),
                            message ? h('p', { class: 'studio-hint', text: message }) : null,
                            this.debug && item.runId ? h('code', { class: 'studio-debug', text: `run ${item.runId} · ${item.slot} · ${item.status}` }) : null,
                            h('div', { class: 'studio-actions' },
                                this.btn('Retry', 'retry-item', () => this.itemAction('retryItem', item.runId, '⟳ Trying that again.', "We couldn't try that visual again.", itemDetails(item)),
                                    { off: noRun, attrs: { 'data-item': `media-${i}` }, label: `Retry: ${mediaItemText(item)}` }),
                                // (it opens Visual Review on that scene, where a picture or clip is chosen)
                                this.btn('Choose existing', 'choose-existing', () => this.panelAction('chooseExisting', "We couldn't open Visual Review.", item.sceneIndex),
                                    { off: item.sceneIndex === null, attrs: { 'data-item': `media-${i}` }, label: `Choose existing: a visual for scene ${item.sceneIndex === null ? '' : item.sceneIndex + 1}`.trim(),
                                        title: 'Choose a picture or clip for this scene in Visual Review' }),
                                fallbackItem(item) ? null : this.btn('Continue without', 'dismiss-item', () => this.itemAction('dismissItem', item.runId, '✓ The lesson continues without it.', "We couldn't skip that visual.", itemDetails(item)),
                                    { off: noRun, attrs: { 'data-item': `media-${i}` }, label: `Continue without: ${mediaItemText(item)}` })));
                    })) : null;
                return body(
                    counts,
                    actions(this.btn('Prepare visuals', 'prepare-media', () => this.prepareMedia(), { primary: !done && !attention,
                        title: 'Make the pictures and clips the lesson still needs (presenter clips are made in Visual Review)' })),
                    listEl);
            }
            if (s.key === 'style') return null; // (its one line of help, then the page's style panel)
            if (s.key === 'review') {
                const r = st.review;
                const total = r.approved + r.changed + r.pending + r.removed;
                const q = st.quality || (this.lastQuality && this.lastQuality.projectId === this.projectId ? { status: this.lastQuality.status, counts: this.lastQuality.counts, stale: false } : null);
                const words = q ? qualityWords(q) : null;
                // one primary action: what is still to do here
                const primary = r.pending ? 'review' : !q || q.stale || q.status === 'attention' || q.status === 'blocked' ? 'quality' : null;
                return body(
                    h('div', { class: 'studio-tool', 'data-tool': 'review' },
                        h('h4', { class: 'studio-tool-title', text: 'Visual Review' }),
                        h('p', { class: 'studio-help', text: HELP_REVIEW }),
                        // the server counts the scene visuals; Visual Review lists each scene's presenter and layout too (its total is higher)
                        total ? h('p', { class: 'studio-review-counts',
                            text: `Scene visuals: ${r.approved} approved · ${r.changed} changed · ${r.pending} to check${r.removed ? ` · ${r.removed} removed` : ''}` }) : null,
                        total ? h('p', { class: 'studio-hint studio-review-scope', text: 'Presenters and layouts are checked in Visual Review too.' }) : null,
                        actions(this.btn('Open Visual Review', 'open-review', () => this.panelAction('openReview', "We couldn't open Visual Review."), { primary: primary === 'review' }))),
                    h('div', { class: 'studio-tool', 'data-tool': 'quality' },
                        h('h4', { class: 'studio-tool-title', text: 'Quality' }),
                        h('p', { class: 'studio-help', text: HELP_QUALITY }),
                        h('p', { class: 'studio-quality', 'data-status': q ? q.status : 'none', text: words || '○ Quality not checked yet' }),
                        q && q.stale ? h('p', { class: 'studio-quality-stale', text: '◌ Changed since the check' }) : null,
                        actions(this.btn(q ? 'Check again' : 'Check quality', 'run-quality', () => this.runQuality(), { primary: primary === 'quality' }))),
                    actions(this.btn('Open the editor', 'open-editor', () => this.stageAction('openEditor', "We couldn't open the editor."), { className: 'studio-btn-link',
                        title: 'Change scenes, narration and visuals in the editor' })));
            }
            if (s.key === 'preview') {
                return body(actions(this.btn(done ? 'Preview again' : 'Preview the lesson', 'preview', () => this.preview(), { primary: !done })));
            }
            if (s.key === 'export') {
                const latest = st.exports.latest;
                const match = matchWords(latest);
                const n = st.exports.count;
                // "Your videos" only when the page's list of videos is not the export panel itself (adapter.separateVideos)
                const separate = this.has('openVideos') && this.flag('separateVideos');
                return body(
                    h('p', { class: 'studio-export-latest', 'data-status': latest ? latest.status.toLowerCase() : 'none', text: `Latest video: ${exportWords(latest)}` }),
                    match ? h('p', { class: 'studio-export-match', 'data-matches': String(latest.matches), text: match }) : null,
                    !separate && n ? h('p', { class: 'studio-hint studio-export-count', text: `${plural(n, 'export')} of this lesson so far.` }) : null,
                    actions(
                        this.btn(done ? 'Export again' : 'Export video', 'export-video', () => this.panelAction('exportVideo', "We couldn't start the export."), { primary: !done }),
                        separate ? this.btn(n ? `Your videos (${n})` : 'Your videos', 'open-videos', () => this.panelAction('openVideos', "We couldn't open your videos.")) : null));
            }
            return null;
        }

        // The settings panel the open stage hosts (Phase 12 presenter / Phase 17 style), moved in by the page and given
        // back when the stage, the view or the Studio closes. A redraw keeps the same section (the page's panel stays).
        syncSettings(dropAll = false) {
            const want = !dropAll && this.isOpen && this.view === 'lesson' && this.state && this.lessonEls ? SETTINGS_FOR[this.stage] || null : null;
            this.mounted.forEach((node, kind) => {
                if (kind === want) return;
                this.mounted.delete(kind);
                this.safeCall('unmountSettings', kind);
                detach(node);
            });
            if (!want || !this.has('mountSettings')) return;
            const slot = this.lessonEls.slot;
            let node = this.mounted.get(want);
            if (!node) {
                const mount = this.h('div', { class: 'studio-settings-mount' });
                node = this.h('section', { class: 'studio-settings', 'data-settings': want, 'aria-label': SETTINGS_TITLE[want] },
                    this.h('h4', { text: SETTINGS_TITLE[want] }), mount);
                this.mounted.set(want, node);
                slot.appendChild(node);
                const failed = () => {
                    if (this.mounted.get(want) !== node) return;
                    mount.textContent = `The ${SETTINGS_TITLE[want].toLowerCase()} settings couldn't be shown here. Close the Studio to change them on the page.`;
                };
                try {
                    const out = this.adapter.mountSettings(want, mount);
                    if (out === false) failed(); // the page has no such panel right now
                    else if (out && typeof out.then === 'function') out.then(ok => { if (ok === false) failed(); }, failed);
                } catch (e) {
                    failed();
                }
            } else if (parentOf(node) !== slot) {
                slot.appendChild(node);
            }
        }

        // ---- actions ------------------------------------------------------------------------------------------------

        // One action: buttons off while it runs; a failure becomes what happened + whether the work is safe + Try again
        // (failed(e) may adjust that error, e.g. a retry that cannot work); then a refresh
        async act(key, run, fallback, { refresh = true, safe = LESSON_SAFE, failed: onFailed = null } = {}) {
            if (this.busy) return;
            this.busy = key;
            this.error = null;
            this.note = '';
            this.applyBusy();
            let failed = null;
            try {
                await run();
            } catch (e) {
                failed = e;
            }
            this.busy = null;
            if (failed) {
                this.setError(fallback, failed, () => this.act(key, run, fallback, { refresh, safe, failed: onFailed }), 'action', safe);
                if (typeof onFailed === 'function') onFailed(failed);
            }
            if (!this.isOpen) return;
            if (failed || !refresh) this.render();
            else this.applyBusy();
            if (refresh) await this.refresh();
        }

        lessonAction(key, run, note, fallback) {
            return this.act(key, async () => {
                await run();
                if (note) this.note = note;
            }, fallback);
        }

        checkpoint(name, value, note) {
            return this.lessonAction(`checkpoint-${name}`, () => this.invoke('checkpoint', name, value), note, "We couldn't save that step.");
        }

        itemAction(method, arg, note, fallback, details) {
            return this.lessonAction(`item-${method}`, () => this.invoke(method, arg, details), note, fallback);
        }

        // An action that opens one of the page's panels above the Studio (Visual Review, the Asset Library, the export
        // panel): the Studio is not held busy while that panel is open; it refreshes when the call settles, and the focus
        // goes back to the button that opened it when the panel leaves it nowhere
        panelAction(method, fallback, ...args) {
            if (this.busy) return undefined;
            this.error = null;
            this.note = '';
            this.returnTo = this.focusKey() || this.clicked;
            return this.invoke(method, ...args).then(() => this.refresh(), e => {
                if (!this.isOpen) return undefined;
                this.setError(fallback, e, () => this.panelAction(method, fallback, ...args), 'action', LESSON_SAFE);
                this.render();
                return this.refresh();
            });
        }

        // An action that needs the live stage (the editor, the preview): the Studio steps aside first
        stageAction(method, fallback) {
            if (this.busy) return undefined;
            this.close('stage');
            return this.invoke(method).catch(e => { this.setError(fallback, e, null, 'action', LESSON_SAFE); });
        }

        preview() {
            const pid = this.projectId;
            const fp = this.state ? this.state.fingerprint : '';
            if (this.busy) return undefined;
            this.close('stage');
            return this.invoke('preview').then(() => this.rememberPreview(pid, fp), e => { this.setError("We couldn't start the preview.", e, null, 'action', LESSON_SAFE); });
        }

        prepareMedia() {
            return this.act('prepare-media', async () => {
                const out = await this.invoke('prepareMedia');
                const message = plainDetail(plain(out) ? out.message : '');
                this.note = message || (plain(out) && out.batch_id ? '⟳ Making the visuals now.' : '✓ Nothing new to make.');
            }, "We couldn't prepare the visuals.");
        }

        runQuality() {
            return this.act('run-quality', async () => {
                const out = await this.invoke('runQuality');
                if (!plain(out) || typeof out.status !== 'string') throw new Error('');
                const status = out.status.toLowerCase();
                const counts = plain(out.counts) ? out.counts : {};
                this.lastQuality = { projectId: this.projectId, status, counts };
                const words = qualityWords({ status, counts }) || '';
                this.note = words;
                // the result is kept with the lesson (checkpoint "quality"), so the stage and Your lessons follow it
                if (out.recorded !== true) {
                    const fingerprint = typeof out.fingerprint === 'string' && out.fingerprint ? out.fingerprint : this.state ? this.state.fingerprint : null;
                    try {
                        await this.invoke('checkpoint', 'quality', { status, counts, fingerprint });
                    } catch (e) {
                        this.note = `${words} · The result couldn't be kept with the lesson.`;
                    }
                }
            }, "We couldn't check the lesson's quality.");
        }

        // ---- keyboard ------------------------------------------------------------------------------------------------

        handleKey(e) {
            if (!this.isOpen || !e || e.defaultPrevented) return;
            if (this.root && typeof this.root.closest === 'function' && this.root.closest('[inert]')) return; // a dialog above this one (e.g. sign in) made it inert: the keys are not ours
            if (e.key === 'Tab') {
                this.trapTab(e);
                return;
            }
            if (e.key !== 'Escape' && e.key !== 'Esc') return;
            if (isTyping(e.target)) return; // the field keeps the key
            if (this.doc.querySelector && this.doc.querySelector(OTHER_OVERLAYS)) return; // a panel above the Studio closes first
            if (typeof e.stopPropagation === 'function') e.stopPropagation();
            if (typeof e.preventDefault === 'function') e.preventDefault();
            this.close();
        }

        // The Studio is a modal dialog: Tab and Shift+Tab go round inside it (never to the page hidden behind), while no
        // panel of the page is open above it; the focus somewhere else the Studio does not own is left alone
        trapTab(e) {
            if (!this.root || (this.doc.querySelector && this.doc.querySelector(OTHER_OVERLAYS))) return;
            const active = this.doc.activeElement;
            const lost = !active || active === this.doc.body || active === this.doc.documentElement;
            if (!lost && !inside(active, this.root)) return;
            const items = this.focusables();
            if (!items.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            let to = null;
            if (lost) to = e.shiftKey ? last : first;
            else if (e.shiftKey && active === first) to = last;
            else if (!e.shiftKey && active === last) to = first;
            if (!to) return;
            if (typeof e.preventDefault === 'function') e.preventDefault();
            focusNode(to);
        }

        focusables() {
            if (!this.root) return [];
            return Array.from(this.root.querySelectorAll(FOCUSABLE)).filter(n => {
                const tag = String(n.tagName || n.tag || '').toLowerCase();
                if (n.getAttribute('disabled') !== null || n.getAttribute('tabindex') === '-1' || n.getAttribute('type') === 'hidden') return false;
                if (tag === 'a' && n.getAttribute('href') === null) return false;
                for (let x = n; x && x !== this.root; x = parentOf(x)) {
                    if (typeof x.getAttribute === 'function' && x.getAttribute('hidden') !== null) return false;
                }
                if (typeof n.getClientRects === 'function' && !n.getClientRects().length) return false; // not shown (display: none)
                return true;
            });
        }
    }

    return {
        StudioPanel, STAGES, STATUS, STATE, STEPS, LESSON_STAGE, WRITE_FAILED, STOP_QUESTION,
        lessonStages, defaultStage, lessonChip, normalizeLessons, normalizeState, plainDetail, plainError, prefillNames, firstLine,
        qualityWords, qualityCount, runSteps, mediaItemText, exportWords, matchWords, looksLikeLessonJson
    };
});

/*
 * The page's adapter (index.html wires it; every method may return a promise; a rejection's message is shown only when it
 * is plain words):
 *   listLessons() -> {lessons: [{project_id, title, updated_at, stage, scene_count, generating, attention, exported}],
 *                     runs: [{run_id, status, stage}]}          (GET /api/studio/lessons; a plain array of lessons works too)
 *   lessonState(projectId) -> GET /api/studio/lessons/{id}       (contract §3; every field optional)
 *   openLesson(projectId)                                         load the saved lesson into the page (no autoplay)
 *   currentLesson() -> {projectId} | null                         the lesson open on the page (the Studio opens on its stages)
 *   openLessonFile() -> {projectId} | null                        optional: the page's existing .json lesson upload
 *   analyzeDocument() -> {source: {document_id, analysis_id}, title} | null
 *                                                                 the Phase 11 Document Assistant; null when closed unused
 *   startLesson({source, names} | {text, names}) -> {run_id}      POST /api/studio/lessons (the page adds system_prompt,
 *                                                                 provider, model, cinematic_style). names: {subject_name,
 *                                                                 unit_name, session_number, session_title} (strings)
 *   runStatus(runId) -> {run_id, status, stage, project_id, message}   GET /api/studio/runs/{id}
 *   cancelRun(runId)                                              POST /api/studio/runs/{id}/cancel
 *   mountSettings('presenter' | 'style', container) / unmountSettings(kind)
 *                                                                 move the Phase 12 presenter / Phase 17 style settings into
 *                                                                 the container, and back to the page
 *   prepareMedia() -> {batch_id | null, message}                  the existing lesson batch (POST /api/ai-media/lessons/{id}/generate)
 *   retryItem(runId) · dismissItem(runId) · chooseExisting(sceneIndex)   a media item that needs attention
 *   openEditor() · preview()                                      the Studio closes itself first (close('stage')): the live
 *                                                                 stage must be visible; the page may open() it again after
 *   openReview(sceneIndex?) · exportVideo()                       panels that open above the Studio
 *   openVideos()                                                  optional: the page's list of videos; "Your videos" is offered
 *                                                                 in the Export stage only with separateVideos: true (when it is
 *                                                                 the export panel itself, "Export video" is the one button)
 *   separateVideos: bool | () => bool                             optional (Phase 21), see openVideos
 *   layout: 'classic' | 'cinematic' | () => either                optional (Phase 21): the page's layout; a lesson style shows
 *                                                                 only in Cinematic, so in Classic the Style stage says so.
 *                                                                 Without it the Studio reads window.cinematicSettings.settings.mode
 *   runQuality() -> {status, counts, fingerprint?, recorded?}     the Phase 18 check; unless recorded is true the Studio keeps
 *                                                                 the result with checkpoint('quality', {status, counts, fingerprint})
 *   checkpoint(name, value) -> {revision, checkpoints}            PUT /api/studio/lessons/{id}/checkpoint
 *   close()                                                       the user closed the Studio (✕, Esc, or studio.close())
 *   userKey() -> string | number | null                           optional (Phase 21, synchronous): who is signed in (any stable
 *                                                                 id; only its hash is used). The remembered run and previews
 *                                                                 are then kept per user, and nothing is kept while it is null
 *   debug: bool | () => bool                                      show run ids, revisions, fingerprints, raw messages (?visualDebug
 *                                                                 in the page URL does the same)
 * A rejection with status 503 from startLesson means no lesson writer is set up: no Try again, "Back to your lessons" instead.
 *
 * What the page may call: open() (where it makes sense) · open({view: 'home' | 'create'}) · showHome() · showCreate() (the
 * page's own Home / Create entries) · refresh() (something the Studio shows changed outside it, e.g. a panel above it
 * closed) · close() · isOpen.
 */
