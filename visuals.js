/*
 * Visual router client: asks the server how each scene should get its visual (visuals.py), keeps
 * the answers in the lesson, and decides when the page may generate AI media.
 *
 * Planning never generates. A plan that needs AI media says requires_generation, and the page
 * generates only when the "AI visuals" setting allows it:
 *   images (default)  AI images automatically, AI videos only when the user asks for one
 *   all               AI images and videos automatically
 *   off               never; the scene shows what is missing instead
 * Plans are stored in each scene (scene.visual_plan.main / .side) and saved with the lesson, so
 * the preview and the video export show the same visuals.
 *
 * AI media cache (Phase 5, ai_cache.py): the generators answer from the cache when the same request
 * was generated before (cache_hit, no AI call), and plans reuse such media directly (selection
 * "cached"). AiMediaApi calls the generators; force regenerates a new version on purpose.
 *
 * Loaded as a classic <script> (window.AadhiVisuals) and as a CommonJS module by the Node unit
 * tests in tests/visuals.test.js.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.AadhiVisuals = api;
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MODES = {
        images: 'AI images automatically, AI videos on request',
        all: 'AI images and videos automatically',
        off: 'Never generate AI visuals'
    };
    const DEFAULT_MODE = 'images';
    const MODE_KEY = 'aadhi_ai_visuals';
    const STORED_SLOTS = ['main', 'side'];
    const KEPT_FIELDS = ['source', 'media', 'selection', 'asset_id', 'url', 'renderer', 'provider', 'model', 'fallback_from', 'quality_warnings',
        'requires_generation', 'would_require', 'cache_hit', 'review_status', 'review_stale', 'score', 'reason', 'error'];

    const SOURCES = {
        ASSET: { icon: '🗂', label: 'Library' }, // Phase 21: the panel's own name (the top bar's "Library")
        SYSTEM_ASSET: { icon: '🦌', label: 'Shared Aadhi asset' },
        UPLOADED_ASSET: { icon: '📁', label: 'Existing media' },
        PROCEDURAL: { icon: '📐', label: 'Built-in visual' },
        MANIM: { icon: '🧮', label: 'Animation' },
        EXTERNAL_MEDIA: { icon: '🎞', label: 'Animated picture' },
        AI_IMAGE: { icon: '✨', label: 'AI image' },
        AI_VIDEO: { icon: '✨', label: 'AI video' },
        NONE: { icon: '—', label: 'No visual' }
    };

    function getMode(storage) {
        try {
            const value = storage && storage.getItem(MODE_KEY);
            return MODES[value] ? value : DEFAULT_MODE;
        } catch (e) {
            return DEFAULT_MODE;
        }
    }

    function setMode(storage, mode) {
        if (!MODES[mode]) return;
        try { storage.setItem(MODE_KEY, mode); } catch (e) { /* private mode: the default applies */ }
    }

    // What the planner may consider; generation itself is decided by shouldAutoGenerate
    function planOptions(mode) {
        return { allow_ai_generation: mode !== 'off', prefer_existing_assets: true, prefer_procedural: true };
    }

    // Whether the page may start generating this plan's media without the user asking
    function shouldAutoGenerate(plan, mode) {
        if (!plan || !plan.requires_generation || mode === 'off') return false;
        return plan.source === 'AI_IMAGE' || (plan.source === 'AI_VIDEO' && mode === 'all');
    }

    function compact(plan) {
        const kept = {};
        KEPT_FIELDS.forEach(key => { if (plan[key] !== undefined) kept[key] = plan[key]; });
        return kept;
    }

    // Stores each scene's main/side plan in the lesson (replacing older ones) and returns all plans
    function applyPlans(slides, plans) {
        const byScene = new Map();
        (plans || []).forEach(plan => {
            if (!STORED_SLOTS.includes(plan.slot)) return; // explicit HTML/board assets are only checked
            if (!byScene.has(plan.scene_index)) byScene.set(plan.scene_index, {});
            byScene.get(plan.scene_index)[plan.slot] = compact(plan);
        });
        (slides || []).forEach((scene, index) => {
            if (!scene || typeof scene !== 'object') return;
            if (byScene.has(index)) scene.visual_plan = byScene.get(index);
            else delete scene.visual_plan;
        });
        return plans || [];
    }

    function sourceLabel(plan) {
        if (!plan) return '';
        const info = SOURCES[plan.source] || SOURCES.NONE;
        let text = `${info.icon} ${info.label}`;
        if (plan.selection === 'removed') return `${SOURCES.NONE.icon} No visual (removed)`;
        if (plan.error) text += ' — unavailable';
        else if (plan.requires_generation) text += ' (not generated yet)';
        else if (plan.cache_hit) text += ' (reused)';
        else if (plan.would_require) {
            const why = /^no AI \w+ provider/.test(plan.reason || '') ? 'no AI provider is available' : 'AI visuals are off';
            text = `${SOURCES.NONE.icon} ${SOURCES[plan.would_require] ? SOURCES[plan.would_require].label : 'AI'} needed, but ${why}`;
        }
        return text;
    }

    // One line per plan for console.table when ?visualDebug=1
    function debugRows(plans) {
        return (plans || []).map(plan => ({
            scene: plan.scene_index + 1,
            slot: plan.slot,
            source: plan.source,
            selection: plan.selection,
            via: plan.renderer || plan.provider || '',
            asset: plan.asset_id ? plan.asset_id.slice(0, 8) : '',
            score: plan.score !== undefined ? plan.score : '',
            generate: plan.requires_generation ? 'needed' : '',
            reason: plan.error ? `${plan.error}: ${plan.reason}` : plan.reason,
            trace: (plan.debug || []).join(' | ')
        }));
    }

    function summarize(plans) {
        const counts = {};
        (plans || []).forEach(plan => { counts[plan.source] = (counts[plan.source] || 0) + 1; });
        return counts;
    }

    // The short status after a generator answered: reused from the AI media cache, or newly made
    function generationStatus(result, kind = 'visual') {
        if (!result || result.status !== 'success') return '';
        // A backup provider is worth a word; which one it was stays in Visual Review and the provider panel
        const backup = result.fallback_from ? ' (backup AI provider)' : '';
        if (result.cache_hit || result.cached) return `♻ Reused existing ${kind}${backup}`;
        if (result.generated) return `✨ Generated new ${kind}${backup}`;
        return '';
    }

    // Provider details of a generated visual for advanced views (?visualDebug): "veo · veo-2.0 · backup for ltx"
    function provenanceText(info) {
        if (!info || !info.provider) return '';
        const parts = [info.provider];
        if (info.model) parts.push(info.model);
        if (info.fallback_from) parts.push(`backup for ${info.fallback_from}`);
        return parts.join(' · ');
    }

    // Where a visual came from, in plain words for everyone (Phase 21): never a provider or model name ('' when there is
    // nothing to say: no visual, a built-in one, or media placed by hand through the manual workflow)
    function originText(plan) {
        if (!plan || plan.selection === 'removed' || plan.error) return '';
        const ai = !!plan.provider && plan.provider !== 'manual';
        if (plan.source === 'AI_IMAGE' || plan.source === 'AI_VIDEO') {
            if (plan.provider === 'manual') return '';
            if (plan.requires_generation) return 'Will be made with AI';
            return plan.fallback_from ? 'Made with AI (backup)' : 'Made with AI';
        }
        if (plan.source === 'SYSTEM_ASSET') return 'From the shared Aadhi library';
        if (plan.source === 'ASSET' || plan.source === 'UPLOADED_ASSET') return ai ? 'Made with AI earlier, from your library' : 'From your library';
        return '';
    }

    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

    // The AI generators (/generate-ai-video, /generate-ai-image). Older servers without the cache
    // fields still work: their answers simply carry no cache_hit / generated. With `background: true`
    // videos are generated in the background (wait: false) and followed through their run, so a slow
    // provider never holds one HTTP request open for minutes; a server that answers at once (cache hit,
    // manual workflow, older server) is simply taken at its word.
    class AiMediaApi {
        constructor({ fetch, background = false, wait = sleep, pollMs = 2000, maxWaitMs = 20 * 60 * 1000 }) {
            this.fetchFn = fetch;
            this.background = background;
            this.wait = wait;
            this.pollMs = pollMs;
            this.maxWaitMs = maxWaitMs;
        }

        async request(path, init) {
            let res;
            try {
                res = await this.fetchFn(path, init);
            } catch (e) {
                throw Object.assign(new Error('The server could not be reached.'), { status: 0 });
            }
            const data = await res.json().catch(() => null);
            if (!res.ok) {
                const detail = data && data.detail;
                throw Object.assign(new Error(typeof detail === 'string' ? detail : `Generation failed (error ${res.status}).`), { status: res.status });
            }
            return data || {};
        }

        post(path, body) {
            return this.request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        }

        // Follows a background generation until it is done; returns the same answer a waiting request gets
        async follow(runId, onProgress) {
            const started = Date.now();
            let lastError = null;
            while (Date.now() - started < this.maxWaitMs) {
                await this.wait(this.pollMs);
                let run;
                try {
                    run = await this.request(`/api/ai-media/runs/${encodeURIComponent(runId)}`, { method: 'GET' });
                } catch (e) {
                    if (e.status && e.status !== 0 && e.status < 500) throw e;
                    lastError = e; // a moment without the server: keep waiting
                    continue;
                }
                if (onProgress) onProgress(run);
                if (run.status === 'completed' && run.result) return run.result;
                if (run.status === 'failed' || run.status === 'cancelled' || run.status === 'needs_attention') {
                    const error = run.error || {};
                    throw Object.assign(new Error(error.message || run.explanation || 'The generation failed.'),
                        { status: error.http_status || (run.status === 'needs_attention' ? 409 : 502), run });
                }
            }
            throw Object.assign(new Error(lastError ? lastError.message : 'The generation is taking too long; it may still finish later.'), { status: 504 });
        }

        // `projectId`, `sceneIndex`, `slot`: the scene it is for, so the generation can be found again after a
        // refresh and the lesson is updated even if the page was closed meanwhile; `sceneId` (Phase 20): the scene's own
        // id, so a result finds its scene even if scenes were moved meanwhile
        async generateVideo(prompt, { force = false, onProgress = null, projectId = null, sceneIndex = null, slot = null, sceneId = null } = {}) {
            const body = force ? { prompt, force_regenerate: true } : { prompt };
            if (this.background) body.wait = false;
            if (projectId !== null && projectId !== undefined) Object.assign(body, { project_id: projectId, scene_index: sceneIndex, slot: slot || 'main' });
            if (body.project_id !== undefined && typeof sceneId === 'string' && /^s-[0-9a-f]{12}$/.test(sceneId)) body.scene_id = sceneId;
            const first = await this.post('/generate-ai-video', body);
            if (first && first.run_id && ['queued', 'running'].includes(first.status)) {
                if (onProgress) onProgress(first);
                return this.follow(first.run_id, onProgress);
            }
            return first;
        }

        // The user's generations: of one lesson, only those in progress or needing attention, or of one batch
        runs({ projectId = null, active = false, batchId = null, limit = 50 } = {}) {
            const q = new URLSearchParams({ limit: String(limit) });
            if (projectId !== null && projectId !== undefined) q.set('project_id', String(projectId));
            if (active) q.set('active', 'true');
            if (batchId) q.set('batch_id', batchId);
            return this.request(`/api/ai-media/runs?${q}`, { method: 'GET' }).then(data => data.runs || []);
        }

        run(runId) {
            return this.request(`/api/ai-media/runs/${encodeURIComponent(runId)}`, { method: 'GET' });
        }

        cancelRun(runId) {
            return this.post(`/api/ai-media/runs/${encodeURIComponent(runId)}/cancel`, {});
        }

        // retry: generate it anyway (accepting a possible duplicate); dismiss: leave it
        resolveRun(runId, action) {
            return this.post(`/api/ai-media/runs/${encodeURIComponent(runId)}/resolve`, { action });
        }

        // Every AI visual a saved lesson still needs, generated on the server one by one (survives closing the page)
        startLessonBatch(projectId, media = 'video') {
            return this.post(`/api/ai-media/lessons/${encodeURIComponent(projectId)}/generate`, { media });
        }

        generateImage(prompt, { subjectName = '', force = false } = {}) {
            const body = { prompt, subject_name: subjectName || '' };
            if (force) body.force_regenerate = true;
            return this.post('/generate-ai-image', body);
        }
    }

    // The provider panel (settings): providers and their state, and the user's recent generations
    class ProvidersApi {
        constructor({ fetch }) {
            this.api = new AiMediaApi({ fetch });
        }

        status() {
            return this.api.request('/api/ai-media/providers', { method: 'GET' });
        }

        runs(limit = 10) {
            return this.api.request(`/api/ai-media/runs?limit=${limit}`, { method: 'GET' });
        }
    }

    const ACTIVE_RUN_STATES = ['queued', 'running', 'recovering', 'cancel_requested'];

    // A generation run in words, for people: never an invented percentage; the provider only for ?visualDebug (debug: true)
    function runStatusText(run, { debug = false } = {}) {
        if (!run) return '';
        const where = run.scene_index !== null && run.scene_index !== undefined ? `Scene ${run.scene_index + 1}` : '';
        const who = debug ? run.provider || run.requested_provider : '';
        const state = {
            queued: run.explanation && /temporary provider problem/.test(run.explanation) ? 'Waiting to retry' : 'Queued',
            running: 'Generating', recovering: 'Recovering after an interruption', cancel_requested: 'Cancelling',
            completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', needs_attention: 'Needs attention'
        }[run.status] || run.status;
        return [where, run.media_type, state, who ? `(${who})` : ''].filter(Boolean).join(' · ').replace(' · (', ' (');
    }

    const STATE_LABELS = {
        available: 'Available',
        not_configured: 'Not configured',
        disabled: 'Disabled',
        temporarily_unavailable: 'Temporarily unavailable'
    };

    function stateLabel(state) {
        return STATE_LABELS[state] || 'Unsupported';
    }

    class VisualApi {
        constructor({ fetch }) {
            this.fetchFn = fetch;
        }

        async plan(slides, options = {}, debug = false) {
            let res;
            try {
                res = await this.fetchFn('/api/visuals/plan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ scenes: slides, ...options, debug })
                });
            } catch (e) {
                throw Object.assign(new Error('The server could not be reached to plan the visuals.'), { status: 0 });
            }
            const data = await res.json().catch(() => null);
            if (!res.ok) {
                const detail = data && data.detail;
                throw Object.assign(new Error(typeof detail === 'string' ? detail : `Visual planning failed (error ${res.status}).`), { status: res.status });
            }
            return data;
        }
    }

    return { MODES, DEFAULT_MODE, SOURCES, ACTIVE_RUN_STATES, AiMediaApi, ProvidersApi, VisualApi, applyPlans, compactPlan: compact, debugRows,
        generationStatus, getMode, originText, planOptions, provenanceText, runStatusText, setMode, shouldAutoGenerate, sourceLabel, stateLabel, summarize };
});
