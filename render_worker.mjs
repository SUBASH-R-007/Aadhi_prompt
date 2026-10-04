#!/usr/bin/env node
/*
 * Phase 22 render worker: renders a saved lesson to video frame by frame in headless Chrome.
 *
 * The server (renders.py) starts it with a fixed argument list (`node render_worker.mjs`: no shell, no user text, no
 * token in argv). Everything else arrives on STDIN:
 *   line 1          the job (JSON): server address, lesson, workspace, frame-set folders, ffmpeg, the render settings
 *                   and a short-lived normal login token
 *   later lines     control: {"type":"token","token":...} (a fresh login), {"type":"cancel"}
 * Progress goes to STDERR as one JSON object per line ({"type": ...}); STDOUT is not used.
 *
 *   plan     open the lesson in a page (render mode, contract §1), renderMode.prepare(): its scenes, the missing-visuals
 *            preflight (refused unless the job says "omit") and the clips it plays; contiguous scene ranges (default 3);
 *            kept in <workspace>/plan.json so a restarted render keeps the same ranges
 *   frames   every clip as a frame set (contract §2): a pre-made set in assets/mascot_frames/<clip key>/ for the mascot,
 *            else frames extracted once by ffmpeg into <RENDER_DIR>/frames/<sha256[:16]>/ (cache); served to the page
 *            from disk at /__render_frames/** (page.route, no server route)
 *   ranges   one page per range, side by side: virtual clock installed before navigation, prepare, useFrames, frozen
 *            clock, start; then per frame: stepFrame() (the clock moves one frame, the page applies it and waits for its
 *            gate), a CDP JPEG screenshot piped into ffmpeg (H.264, CFR 30). A range after the first starts one scene
 *            early (pre-roll, not captured) so a transition at the seam is the same, and captures from the frame its
 *            first scene begins. A finished range leaves ranges/range-<i>.mp4 and range-<i>.json; a restart skips it.
 *   join     the range videos joined with ffmpeg's concat demuxer (same encode settings: no re-encode) into video.mp4,
 *            and the range timelines merged onto the video's clock into timeline.full.json; the server mixes the sound
 *
 * Exit codes: 0 done, 1 failed, 2 bad job, 3 missing visuals refused (the list was reported), 4 the page failed,
 * 5 cancelled.
 *
 * Silent like every browser this project starts: --mute-audio --disable-audio-output, speech stubbed. The page's writes
 * that a render never needs (saving the lesson, uploads, AI generation, exports) are refused in the page.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const EXIT = { OK: 0, FAILED: 1, BAD_JOB: 2, MISSING: 3, PAGE: 4, CANCELLED: 5 };
export const CLOCK_START = 1767225600000; // 2026-01-01T00:00:00Z: the virtual epoch of every render page
export const FRAME_BASE = '/__render_frames/';
const REPO = path.dirname(fileURLToPath(import.meta.url));
const FRAME_FILE = /^\d{6}\.(jpg|jpeg|png|webp)$/;
const SET_ID = /^(?:[0-9a-f]{16}|m-[A-Za-z0-9_-]{1,64})$/;
const ALPHA_FORMATS = /^(yuva|rgba|bgra|argb|abgr|ya8|ya16|gbrap|rgba64|bgra64)/;
const IMAGE_TYPES = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp' };
const STORAGE_KEYS = ['aadhi.cinematic', 'aadhi.presenter', 'aadhi_ai_visuals'];

class WorkerError extends Error {
    constructor(code, message, exitCode = EXIT.FAILED, extra = {}) {
        super(message);
        this.code = code;
        this.exitCode = exitCode;
        Object.assign(this, extra);
    }
}

// ---- pure helpers (unit-tested in tests/render_worker.test.js) -----------------------------------------------------

// Virtual milliseconds of frame n: whole milliseconds, so the clock never drifts however long the lesson is
export function at(n, fps = 30) {
    return Math.round(n * 1000 / fps);
}

// Contiguous ranges of the scenes that play (hidden ones are skipped by the page). A range may only begin at a scene the page
// marks seamSafe (prepare(): scenes[].seamSafe === true: no looping clip runs across that boundary, so the next page can
// start it exactly as the preview shows it; a looping clip's phase cannot be reproduced at a seam). Among the safe seams
// the ranges are as even as possible: by the scenes' expected seconds when the page gives them all (scenes[].estimate; the
// intro counts with the first scene), else by their number. No safe seam: one page. Returns {ranges, seams} (seams: what
// was chosen and why, for the log).
export const INTRO_SECONDS = 14.5;
export function planSeams(scenes, count) {
    const playedScenes = (scenes || []).filter(s => s && !s.hidden);
    const played = playedScenes.map(s => s.index);
    if (!played.length) return { ranges: [{ index: 0, fromScene: 0, toScene: null }], seams: { safe: [], chosen: [], reason: 'no scene plays' } };
    const timed = playedScenes.every(s => typeof s.estimate === 'number' && Number.isFinite(s.estimate) && s.estimate > 0);
    const weights = playedScenes.map((s, i) => (timed ? s.estimate : 1) + (timed && i === 0 ? INTRO_SECONDS : 0));
    const before = [];
    let total = 0;
    weights.forEach((w, i) => { before[i] = total; total += w; });
    const safe = playedScenes.map((s, i) => (i > 0 && s.seamSafe === true ? i : null)).filter(i => i !== null);
    const n = Math.max(1, Math.min(count || 1, safe.length + 1));
    const cuts = []; // positions (into played) where a range after the first begins
    for (let k = 1; k < n; k++) {
        const last = cuts.length ? cuts[cuts.length - 1] : 0;
        const left = n - 1 - k; // safe seams still needed after this one
        const candidates = safe.filter(i => i > last && safe.filter(j => j > i).length >= left);
        if (!candidates.length) break;
        const target = k * total / n;
        cuts.push(candidates.reduce((best, i) => (Math.abs(before[i] - target) < Math.abs(before[best] - target) ? i : best), candidates[0]));
    }
    const starts = [0, ...cuts];
    const ranges = starts.map((at, r) => ({
        // the first range starts at the lesson's start (the intro, and hidden first scenes skipped by the page itself)
        index: r, fromScene: r ? played[at] : 0, toScene: r === starts.length - 1 ? null : played[starts[r + 1] - 1]
    }));
    const reason = !safe.length ? 'no scene boundary is free of a looping clip: one page renders the whole lesson'
        : count <= 1 ? 'one page asked for'
            : `${cuts.length} of ${safe.length} safe seams, chosen to even out the pages by ${timed ? 'expected seconds' : 'scene count'}`;
    return { ranges, seams: { safe: safe.map(i => played[i]), chosen: cuts.map(i => played[i]), balancedBy: timed ? 'estimate' : 'count', reason } };
}

export function planRanges(scenes, count) {
    return planSeams(scenes, count).ranges;
}

// Whole-lesson pre-roll (the default): every range page plays the lesson from its start (the intro included), stepping
// the same frames uncaptured, and captures from the frame its first scene begins. The page is deterministic, so its state
// at a seam is the previous page's exactly, and any scene boundary can be a seam. A page's time is about
//   overhead + preroll frames / preroll fps + captured frames / capture fps
// at the rates one page reaches while k pages share the machine (rate x k^-alpha). The split that makes the slowest page
// fastest is chosen (later pages capture fewer frames, since they pre-roll more), with as many pages as help (at most
// `count`). rates: {captureFps, prerollFps, alpha, overhead} (measured on earlier renders, see readRates).
// Rates by layout, as one page alone reaches them (measured on the development machine, Phase 22). Classic: pre-roll about
// 11-20 ms a frame, capture 137-168 ms; Cinematic Studio (camera moves, presenter, composed layers): pre-roll about 0.1 s a
// frame and capture about 0.4 s a frame with three pages at once (x 3^-0.65: the single-page rates below). Pages slow each
// other down: three pages reach about 1.5 times one page's total (alpha).
export const LAYOUT_RATES = {
    classic: { captureFps: 6.5, prerollFps: 90, alpha: 0.65, overhead: 10 },
    cinematic: { captureFps: 5.2, prerollFps: 20, alpha: 0.65, overhead: 10 }
};
export const DEFAULT_RATES = LAYOUT_RATES.classic;
const CLASSIC = { kind: 'classic', key: 'classic' };

export function planPages(scenes, count, rates = DEFAULT_RATES, fps = 30) {
    const r = { ...DEFAULT_RATES, ...(rates || {}) };
    const playedScenes = (scenes || []).filter(s => s && !s.hidden);
    const played = playedScenes.map(s => s.index);
    if (!played.length) {
        return { ranges: [{ index: 0, fromScene: 0, toScene: null }], seams: { mode: 'lesson', chosen: [], pages: [], reason: 'no scene plays' } };
    }
    const timed = playedScenes.every(s => typeof s.estimate === 'number' && Number.isFinite(s.estimate) && s.estimate > 0);
    const seconds = playedScenes.map((s, i) => (timed ? s.estimate : 10) + (i === 0 ? INTRO_SECONDS : 0));
    const before = [];
    let total = 0;
    seconds.forEach((w, i) => { before[i] = total; total += w; });
    // one page's time from its start (video seconds) and length (video seconds), with k pages at once
    const cost = (start, length, k) => {
        const f = Math.pow(k, -r.alpha);
        return r.overhead + (start * fps) / (r.prerollFps * f) + (length * fps) / (r.captureFps * f);
    };
    // the cuts (indices into played) for k pages that keep every page within T seconds, or null
    const fits = (T, k) => {
        const cuts = [];
        let at = 0;
        for (let page = 1; page <= k; page++) {
            const start = before[at];
            if (cost(start, total - start, k) <= T) return cuts;
            if (page === k) return null;
            let next = null;
            for (let i = at + 1; i < played.length; i++) if (cost(start, before[i] - start, k) <= T) next = i;
            if (next === null) return null;
            cuts.push(next);
            at = next;
        }
        return null;
    };
    let best = null;
    for (let k = 1; k <= Math.max(1, Math.min(count || 1, played.length)); k++) {
        let lo = 0;
        let hi = cost(0, total, 1) * 2 + 1;
        if (!fits(hi, k)) continue;
        for (let step = 0; step < 60; step++) {
            const mid = (lo + hi) / 2;
            if (fits(mid, k)) hi = mid; else lo = mid;
        }
        const cuts = fits(hi, k);
        const starts = [0, ...cuts];
        const pages = starts.map((at, i) => {
            const end = i + 1 < starts.length ? before[starts[i + 1]] : total;
            return { page: i, prerollSeconds: Math.round(before[at] * 10) / 10, captureSeconds: Math.round((end - before[at]) * 10) / 10,
                predicted: Math.round(cost(before[at], end - before[at], starts.length)) };
        });
        const wall = Math.max(...pages.map(pg => pg.predicted));
        // every extra page must pay for itself: at least 10 % less wall time per page added (the rates are estimates, and a page
        // that pre-rolls an hour of Cinematic lesson before capturing anything holds the machine for little gain)
        if (!best || wall < best.wall * (1 - 0.1 * (starts.length - best.starts.length))) best = { wall, cuts, starts, pages };
    }
    const ranges = best.starts.map((at, i) => ({ index: i, fromScene: i ? played[at] : 0,
        toScene: i === best.starts.length - 1 ? null : played[best.starts[i + 1] - 1] }));
    return { ranges, seams: { mode: 'lesson', chosen: best.cuts.map(i => played[i]), balancedBy: timed ? 'estimate' : 'count',
        pages: best.pages, predictedSeconds: best.wall, rates: r,
        reason: best.starts.length === 1 ? 'one page is fastest for this lesson (pre-rolling would cost more than a second page saves)'
            : `${best.starts.length} pages, split for the shortest wall time (whole-lesson pre-roll: every scene boundary is a seam)` } };
}

// The rates measured on this server (renders update them), per layout: <RENDER_DIR>/rates.json
//   { "classic": {captureFps, prerollFps, overhead, alpha?}, "cinematic": {...}, "cinematic:<style family>": {...} }
// A layout's own entry (its key, e.g. "cinematic:corporate_training"), else its kind's ("cinematic"), else the defaults of the
// kind. (A file from before per-layout rates holds one set: classic.)
function savedRates(renderDir) {
    const saved = readJson(path.join(renderDir, 'rates.json'));
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {};
    return 'captureFps' in saved || 'prerollFps' in saved ? { classic: saved } : saved;
}

export function layoutOf(layout) {
    const kind = layout && layout.kind === 'cinematic' ? 'cinematic' : 'classic';
    const key = layout && typeof layout.key === 'string' && /^(classic|cinematic)(:[a-z0-9_-]{1,40})?$/.test(layout.key) && layout.key.startsWith(kind)
        ? layout.key : kind;
    return { kind, key };
}

export function readRates(renderDir, layout = CLASSIC) {
    const { kind, key } = layoutOf(layout);
    const all = savedRates(renderDir);
    const saved = all[key] && typeof all[key] === 'object' ? all[key] : all[kind];
    const ok = v => typeof v === 'number' && Number.isFinite(v) && v > 0;
    const out = { ...LAYOUT_RATES[kind] };
    if (saved && typeof saved === 'object') {
        for (const name of ['captureFps', 'prerollFps', 'overhead']) if (ok(saved[name])) out[name] = saved[name];
        if (ok(saved.alpha) && saved.alpha < 2) out.alpha = saved.alpha; // how much pages slow each other down (set by hand)
    }
    return out;
}

// A render's measured rates folded into its layout's saved ones (as one page alone would reach them; a running average).
// The kind's entry learns too, so a style family not rendered before starts from what its kind measured.
export function updateRates(renderDir, measured, pages, alpha = DEFAULT_RATES.alpha, layout = CLASSIC) {
    const { kind, key } = layoutOf(layout);
    const all = savedRates(renderDir);
    const f = Math.pow(Math.max(1, pages), -alpha);
    const fold = name => {
        const current = readRates(renderDir, name === kind ? { kind, key: kind } : { kind, key });
        const mix = (old, value) => (typeof value === 'number' && Number.isFinite(value) && value > 0
            ? Math.round((old * 0.6 + (value / f) * 0.4) * 100) / 100 : old);
        return { ...(current.alpha !== LAYOUT_RATES[kind].alpha ? { alpha: current.alpha } : {}),
            captureFps: mix(current.captureFps, measured.captureFps), prerollFps: mix(current.prerollFps, measured.prerollFps),
            overhead: typeof measured.overhead === 'number' && measured.overhead > 0 ? Math.round(current.overhead * 0.6 + measured.overhead * 0.4) : current.overhead,
            updatedAt: new Date().toISOString() };
    };
    const next = fold(key);
    const out = { ...all, [key]: next };
    if (key !== kind) out[kind] = fold(kind);
    try { writeJson(path.join(renderDir, 'rates.json'), out); } catch (e) { /* only a better plan next time */ }
    return next;
}

// A page much slower (or faster) than its plan said: the times the next plan uses improve (updateRates) and the log says so
export function rateWarning(stage, measuredFps, plannedFps) {
    if (!(measuredFps > 0) || !(plannedFps > 0)) return null;
    const ratio = measuredFps / plannedFps;
    if (ratio >= 0.5 && ratio <= 2) return null;
    return { type: 'plan_warning', stage, measuredFps: Math.round(measuredFps * 100) / 100, plannedFps: Math.round(plannedFps * 100) / 100,
        message: `The ${stage} runs ${ratio < 1 ? `${Math.round(1 / ratio * 10) / 10} times slower` : `${Math.round(ratio * 10) / 10} times faster`} than planned.` };
}

// What a range page is told (window.__AADHI_RENDER__ range fields, render_mode.js readConfig). Whole-lesson pre-roll: it
// plays from the lesson's start (fromScene 0, the intro included) and captures from the frame scene captureFromScene begins;
// scene pre-roll (the fallback): it starts one played scene before fromScene (preroll).
export function rangeFields(range, mode) {
    if (mode === 'scene') return range.index === 0 ? { fromScene: 0, toScene: range.toScene, preroll: false }
        : { fromScene: range.fromScene, toScene: range.toScene, preroll: true };
    return { fromScene: 0, captureFromScene: range.index === 0 ? 0 : range.fromScene, toScene: range.toScene };
}

// The frame-set folder and file a /__render_frames/<set>/<file> request asks for, or null for anything else
export function frameRequest(url) {
    let pathname;
    try { pathname = new URL(url).pathname; } catch (e) { return null; }
    if (!pathname.startsWith(FRAME_BASE)) return null;
    const parts = pathname.slice(FRAME_BASE.length).split('/');
    if (parts.length !== 2 || !SET_ID.test(parts[0])) return null;
    if (parts[1] !== 'manifest.json' && !FRAME_FILE.test(parts[1])) return null;
    return { setId: parts[0], file: parts[1] };
}

export function parseRate(text) {
    const [num, den] = String(text || '').split('/').map(Number);
    if (!num || !Number.isFinite(num)) return null;
    const value = den ? num / den : num;
    return value > 0 && value <= 240 ? { value, text: den ? `${num}/${den}` : `${num}/1` } : null;
}

export function validManifest(m) {
    return !!m && typeof m === 'object' && m.version === 1 && Number.isFinite(m.fps) && m.fps > 0 && Number.isInteger(m.frames)
        && m.frames > 0 && ['jpeg', 'webp', 'png'].includes(m.format) && typeof m.pattern === 'string'
        && /^%0?6d\.(jpg|jpeg|png|webp)$/.test(m.pattern);
}

// The range timelines on the video's clock. Each range's times are seconds from its own capture start; a range starts
// `offset` seconds into the video. A range after the first drops what it logged during its pre-roll (t < 0), and a sound
// the page clipped to its capture start (t = 0 with the offset advanced): the range before it logged that sound whole.
// Every range but the last drops what began after its end (the next range logged that).
// The start of a range's first scene (a seam) is taken from the range before it, which played through it: it logged the
// moment the scene began (mid-frame), where the range itself starts counting at its first captured frame. So a chapter
// starts at the same time as in a render made in one page.
export function mergeTimelines(ranges, fps = 30) {
    const merged = { scenes: [], cues: [], audio: [], notes: [] };
    const extra = new Set();
    let offset = 0;
    const out = [];
    const seamFrom = new Set(); // ranges whose first scene the range before them logged
    ranges.forEach((range, i) => {
        const tl = range.timeline && typeof range.timeline === 'object' ? range.timeline : {};
        const length = range.frames / fps;
        const last = i === ranges.length - 1;
        // a range ends where the next range's first scene began (logged a moment before its last frame): from there on
        // the next range has it all, except that scene's start
        const nextScenes = last ? [] : (tl.scenes || []).filter(s => s && typeof s.index === 'number' && range.toScene !== null
            && s.index > range.toScene && typeof s.t === 'number');
        const end = Math.min(length, ...nextScenes.map(s => s.t));
        const keep = t => typeof t === 'number' && Number.isFinite(t) && t >= -0.0005 && (last || t < end - 0.0005);
        const seamScene = !last && nextScenes.length ? nextScenes.reduce((a, b) => (b.t < a.t ? b : a)) : null;
        const shift = (item, keys) => {
            const copy = { ...item };
            keys.forEach(k => { if (typeof copy[k] === 'number') copy[k] = round3(Math.max(0, copy[k]) + offset); });
            return copy;
        };
        for (const [key, value] of Object.entries(tl)) {
            if (!Array.isArray(value)) continue;
            if (!(key in merged)) { merged[key] = []; extra.add(key); }
            for (const item of value) {
                if (!item || typeof item !== 'object') continue;
                const start = 't' in item ? item.t : item.start;
                if (key === 'scenes' && item === seamScene && start <= length + 0.0005) {
                    merged.scenes.push(shift(item, ['t']));
                    seamFrom.add(i + 1);
                    continue;
                }
                if (key === 'scenes' && seamFrom.has(i) && item.index === range.fromScene && start <= 1 / fps + 0.0005) continue;
                if (!keep(start)) continue;
                if (key === 'audio' && i > 0 && start <= 0.0005 && typeof item.offset === 'number' && item.offset > 0.0005) continue;
                const moved = shift(item, ['t', 'start', 'end']);
                // a frame number is on the video's clock too (the range's own frame + its first video frame)
                if (typeof moved.frame === 'number') moved.frame = Math.round(('t' in moved ? moved.t : moved.start) * fps);
                if (typeof moved.endFrame === 'number' && typeof moved.end === 'number') moved.endFrame = Math.round(moved.end * fps);
                merged[key].push(moved);
            }
        }
        out.push({ index: range.index, fromScene: range.fromScene, toScene: range.toScene, frames: range.frames, offset: round3(offset) });
        offset += length;
    });
    const total = ranges.reduce((sum, r) => sum + r.frames, 0);
    return { version: 1, fps, frames: total, duration: round3(total / fps), ranges: out, ...merged };
}

function round3(x) {
    return Math.round(x * 1000) / 1000;
}

export const TICKS_PER_FRAME = 512; // the video's time base is 1/15360 s at 30 fps: every frame time is a whole number of ticks

// The join: the ranges back to back (concat demuxer, no re-encode), every frame re-stamped at exactly n/30 s, so a seam can
// never move the frames after it off the 1/30 grid (a range's container duration may be a few ticks short)
export function joinArgs(list, out, { fps = 30, restamp = true } = {}) {
    const ticks = TICKS_PER_FRAME * fps;
    return ['-hide_banner', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list, '-map', '0:v:0', '-c', 'copy', '-an',
        ...(restamp ? ['-bsf:v', `setts=time_base=1/${ticks}:pts=N*${TICKS_PER_FRAME}:dts=N*${TICKS_PER_FRAME}:duration=${TICKS_PER_FRAME}`] : []),
        '-video_track_timescale', String(ticks), out];
}

// True when every frame of the video is exactly on the 1/fps grid (pts = n x 512 ticks, n = 0, 1, 2, ...)
export function onGrid(ptsLines, fps = 30) {
    const values = String(ptsLines || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean).map(Number).sort((a, b) => a - b);
    return values.length > 0 && values.every((v, n) => v === n * TICKS_PER_FRAME);
}

// ffmpeg arguments of one range: Chrome's full-range BT.601 JPEGs to limited-range BT.709 H.264, constant 30 fps
export function encoderArgs(out, { fps = 30, crf = 18 } = {}) {
    return ['-hide_banner', '-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-thread_queue_size', '512',
        '-i', 'pipe:0', '-an', '-vf', 'scale=in_range=pc:out_range=tv:in_color_matrix=bt601:out_color_matrix=bt709,format=yuv420p',
        // no B-frames: presentation order is decode order, so the join can stamp frame n at exactly n/30 (n x 512 ticks)
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', String(crf), '-bf', '0', '-pix_fmt', 'yuv420p', '-r', String(fps), '-fps_mode', 'cfr',
        '-video_track_timescale', String(TICKS_PER_FRAME * fps),
        '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-threads', '2', out];
}

// ---- small runtime helpers ---------------------------------------------------------------------------------------------

function emit(event) {
    try { process.stderr.write(JSON.stringify(event) + '\n'); } catch (e) { /* the server went away */ }
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export const KEEPALIVE_MS = 5000;

// While `promise` is pending, a progress line every KEEPALIVE_MS (describe() -> {event} | {fail}): the server's watchdog
// stops a worker only after real silence, so a long but moving step (narration made for a big lesson, a long clip's frames)
// is never mistaken for a hang
export async function keepalive(promise, describe, everyMs = KEEPALIVE_MS) {
    let fail;
    const stuck = new Promise((_, reject) => { fail = reject; });
    stuck.catch(() => {});
    promise.catch(() => {}); // a failure after a stuck verdict is not "unhandled"
    const timer = setInterval(async () => {
        try {
            const state = await describe();
            if (state && state.fail) fail(state.fail);
            else if (state && state.event) emit(state.event);
        } catch (e) { /* the next tick tries again */ }
    }, everyMs);
    try {
        return await Promise.race([promise, stuck]);
    } finally {
        clearInterval(timer);
    }
}

// The page's own words for how far prepare() is (renderMode.progress: {phase, done, total, label, changes})
export function prepareMessage(progress) {
    if (!progress || typeof progress !== 'object') return 'Preparing the lesson…';
    const label = typeof progress.label === 'string' ? progress.label.trim().slice(0, 80) : '';
    if (progress.total > 0 && Number.isInteger(progress.done)) {
        return `Preparing the lesson: ${progress.done} of ${progress.total} ready${label ? ` (${label.toLowerCase()})` : ''}…`;
    }
    return label ? `Preparing the lesson: ${label.toLowerCase()}…` : 'Preparing the lesson…';
}

function run(exe, args, { input = null, timeoutMs = 600000 } = {}) {
    return new Promise((resolve, reject) => {
        const child = spawn(exe, args, { stdio: [input ? 'pipe' : 'ignore', 'pipe', 'pipe'], windowsHide: true });
        let out = '';
        let err = '';
        const timer = setTimeout(() => { child.kill('SIGKILL'); }, timeoutMs);
        child.stdout.on('data', d => { out += d; });
        child.stderr.on('data', d => { err += d; if (err.length > 20000) err = err.slice(-10000); });
        child.on('error', e => { clearTimeout(timer); reject(e); });
        child.on('close', code => { clearTimeout(timer); resolve({ code, out, err }); });
        if (input) child.stdin.end(input);
    });
}

function writeJson(file, value) {
    fs.writeFileSync(file + '.part', JSON.stringify(value));
    fs.renameSync(file + '.part', file);
}

function readJson(file) {
    try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return null; }
}

function rmQuiet(target) {
    try { fs.rmSync(target, { recursive: true, force: true }); } catch (e) { /* in use: the server's sweep removes it */ }
}

// ---- the job ---------------------------------------------------------------------------------------------------------

export function checkJob(job) {
    const problems = [];
    if (!job || typeof job !== 'object') return ['the job is not an object'];
    if (typeof job.base !== 'string' || !/^http:\/\/(127\.0\.0\.1|localhost):\d{2,5}$/.test(job.base)) problems.push('base');
    if (!Number.isInteger(job.projectId) || job.projectId <= 0) problems.push('projectId');
    if (typeof job.token !== 'string' || job.token.length < 20) problems.push('token');
    for (const key of ['workspace', 'renderDir', 'ffmpeg', 'ffprobe']) if (typeof job[key] !== 'string' || !job[key]) problems.push(key);
    if (!['refuse', 'omit'].includes(job.missingVisuals)) problems.push('missingVisuals');
    return problems;
}

export function settings(job) {
    return {
        fps: 30, width: 1920, height: 1080, quality: Math.max(92, Math.min(100, job.quality || 92)), crf: job.crf || 18,
        ranges: Math.max(1, Math.min(8, job.ranges || 3)), parallel: Math.max(1, Math.min(8, job.parallel || job.ranges || 3)),
        seed: Number.isInteger(job.seed) ? job.seed : 1, tailFrames: Number.isInteger(job.tailFrames) ? job.tailFrames : 45,
        maxFrames: job.maxFrames || 30 * 60 * 180, stallMs: (job.stallSeconds || 120) * 1000,
        // one frame may wait for the page's own limits (a request 180 s, a clip 120 s), after which frameReady says why
        frameMs: Math.max((job.frameSeconds || 300) * 1000, (job.stallSeconds || 120) * 1000),
        // prepare may run for long on a big lesson (narration made for the first time): it fails only when it stops moving
        prepareMs: (job.prepareSeconds || 4 * 3600) * 1000, prepareStallMs: (job.prepareStallSeconds || 900) * 1000,
        minFreeBytes: Math.max(0, job.minFreeGb === undefined ? 2 : Number(job.minFreeGb) || 0) * 1024 ** 3,
        premadeDir: job.premadeDir || path.join(REPO, 'assets', 'mascot_frames'),
        // 'lesson': whole-lesson pre-roll, any scene boundary is a seam; 'scene': one scene of pre-roll, seams only where the page
        // marks them safe (the server chooses: RENDER_PREROLL)
        prerollMode: job.prerollMode === 'scene' ? 'scene' : 'lesson'
    };
}

// ---- frame sets (contract §2) -----------------------------------------------------------------------------------------

// The containers a lesson clip may be in; ffmpeg is then held to that one demuxer (and to local files)
export const CLIP_FORMATS = { mov: 'mov', mp4: 'mov', m4a: 'mov', '3gp': 'mov', matroska: 'matroska', webm: 'matroska', gif: 'gif', ogg: 'ogg' };
export function clipDemuxer(formatName) {
    for (const name of String(formatName || '').split(',')) if (CLIP_FORMATS[name.trim()]) return CLIP_FORMATS[name.trim()];
    return null;
}

async function probeVideo(job, file) {
    const res = await run(job.ffprobe, ['-v', 'error', '-protocol_whitelist', 'file', '-select_streams', 'v:0', '-show_entries',
        'stream=codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate:stream_tags=alpha_mode:format=format_name,duration', '-of', 'json', file],
    { timeoutMs: 60000 });
    if (res.code !== 0) throw new WorkerError('frames_failed', 'A clip of the lesson could not be read.');
    const data = JSON.parse(res.out || '{}');
    const stream = (data.streams || [])[0];
    if (!stream) throw new WorkerError('frames_failed', 'A clip of the lesson has no picture.');
    const demuxer = clipDemuxer(data.format && data.format.format_name);
    if (!demuxer) throw new WorkerError('frames_failed', 'A clip of the lesson is not a video format the renderer reads.');
    const rate = parseRate(stream.avg_frame_rate) || parseRate(stream.r_frame_rate);
    if (!rate) throw new WorkerError('frames_failed', 'A clip of the lesson has no usable frame rate.');
    const tags = stream.tags || {};
    const alpha = ALPHA_FORMATS.test(stream.pix_fmt || '') || String(tags.alpha_mode || tags.ALPHA_MODE || '') === '1';
    const duration = Number(data.format && data.format.duration);
    return { codec: stream.codec_name, width: stream.width, height: stream.height, rate, alpha, demuxer,
        duration: Number.isFinite(duration) && duration > 0 ? duration : null };
}

// Bytes a frame set of this clip will take, roughly (JPEG about 0.2 byte per pixel, PNG with alpha about 1.5)
export function frameSetBytes(info) {
    const frames = Math.ceil((info.duration || 60) * info.rate.value);
    return frames * (info.width || 1920) * (info.height || 1080) * (info.alpha ? 1.5 : 0.2);
}

function freeBytes(dir) {
    try {
        const st = fs.statfsSync(dir);
        return Number(st.bavail) * Number(st.bsize);
    } catch (e) {
        return Infinity; // unknown: the extraction itself reports a full disk
    }
}

// Downloads a clip the page plays (same server, the render's login) into the workspace, hashing it on the way
async function fetchSource(job, src, dest) {
    const url = new URL(src, job.base);
    if (url.origin !== new URL(job.base).origin) throw new WorkerError('frames_failed', 'A clip of the lesson is not on this server.');
    const res = await fetch(url, { headers: { Authorization: `Bearer ${job.token}` } });
    if (!res.ok) throw new WorkerError('frames_failed', `A clip of the lesson could not be loaded (error ${res.status}).`);
    const hash = crypto.createHash('sha256');
    const out = fs.createWriteStream(dest);
    for await (const chunk of res.body) {
        hash.update(chunk);
        if (!out.write(chunk)) await new Promise(r => out.once('drain', r));
    }
    await new Promise((resolve, reject) => out.end(err => (err ? reject(err) : resolve())));
    return hash.digest('hex');
}

// Frames of one source file: JPEG (ffmpeg -q:v 2, about quality 95) for opaque video, PNG when it has alpha; the source's
// own frame rate, as a constant rate (index = round(t * fps) is then exact). Built in a temporary folder, renamed into
// place, so a set that exists is complete.
export async function extractFrameSet(job, file, sha, { loop = false, source = '' } = {}) {
    const root = path.join(job.renderDir, 'frames');
    const id = sha.slice(0, 16);
    const dir = path.join(root, id);
    const existing = readJson(path.join(dir, 'manifest.json'));
    if (validManifest(existing)) {
        const now = new Date();
        try { fs.utimesSync(path.join(dir, 'manifest.json'), now, now); } catch (e) { /* only the cache's age */ }
        return { id, dir, manifest: existing, cached: true };
    }
    const info = await probeVideo(job, file);
    const ext = info.alpha ? 'png' : 'jpg';
    fs.mkdirSync(root, { recursive: true });
    if (freeBytes(root) < frameSetBytes(info) * 1.5 + (job.minFreeBytes || 0)) {
        throw new WorkerError('disk_full', 'The server is running out of disk space, so the video could not be rendered. '
            + 'Free some space on the server (or ask its administrator), then render again.', EXIT.FAILED);
    }
    const tmp = path.join(root, `${id}.tmp-${process.pid}-${Date.now()}`);
    fs.mkdirSync(tmp, { recursive: true });
    const decoder = info.alpha && info.codec === 'vp9' ? ['-c:v', 'libvpx-vp9'] : info.alpha && info.codec === 'vp8' ? ['-c:v', 'libvpx'] : [];
    const quality = info.alpha ? ['-pix_fmt', 'rgba'] : ['-q:v', '2'];
    const res = await run(job.ffmpeg, ['-hide_banner', '-v', 'error', '-y', '-protocol_whitelist', 'file', '-f', info.demuxer, ...decoder,
        '-i', file, '-map', '0:v:0', '-an',
        '-vf', `fps=${info.rate.text}`, ...quality, '-start_number', '1', path.join(tmp, `%06d.${ext}`)], { timeoutMs: 1800000 });
    const frames = res.code === 0 ? fs.readdirSync(tmp).filter(f => FRAME_FILE.test(f)).length : 0;
    if (!frames) {
        rmQuiet(tmp);
        throw new WorkerError('frames_failed', 'The frames of a clip of the lesson could not be prepared.');
    }
    const manifest = { version: 1, fps: Math.round(info.rate.value * 1e6) / 1e6, fpsRational: info.rate.text, frames, width: info.width,
        height: info.height, format: info.alpha ? 'png' : 'jpeg', alpha: info.alpha, loop: !!loop, pattern: `%06d.${ext}`,
        source: path.basename(source.split('?')[0] || file), sourceSha256: sha };
    writeJson(path.join(tmp, 'manifest.json'), manifest);
    try {
        fs.renameSync(tmp, dir);
    } catch (e) {
        rmQuiet(tmp); // another render made the same set meanwhile
        const theirs = readJson(path.join(dir, 'manifest.json'));
        if (!validManifest(theirs)) throw new WorkerError('frames_failed', 'The frames of a clip of the lesson could not be stored.');
        return { id, dir, manifest: theirs, cached: true };
    }
    return { id, dir, manifest, cached: false };
}

// The frame sets of every clip the page reported: { sets: {setId: dir}, map: {src: {base, manifest}} } (map is what the
// page's renderMode.useFrames receives; a set's `loop` follows how the page plays that clip)
export async function frameSets(job, cfg, media, notes = []) {
    const sets = {};
    const map = {};
    const temp = path.join(job.workspace, 'temp');
    fs.mkdirSync(temp, { recursive: true });
    const bySrc = new Map();
    for (const m of media || []) if (m && typeof m.src === 'string' && !bySrc.has(m.src)) bySrc.set(m.src, m);
    let n = 0;
    for (const m of bySrc.values()) {
        n++;
        emit({ type: 'progress', phase: 'preparing', message: `Preparing the lesson's clips (${n} of ${bySrc.size})` });
        if (m.kind === 'mascot' && typeof m.key === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(m.key)) {
            const dir = path.join(cfg.premadeDir, m.key);
            const manifest = readJson(path.join(dir, 'manifest.json'));
            if (validManifest(manifest)) {
                const setId = `m-${m.key}`;
                sets[setId] = dir;
                map[m.src] = { base: `${FRAME_BASE}${setId}/`, manifest: { ...manifest, loop: !!m.loop } };
                continue;
            }
        }
        const file = path.join(temp, `source-${n}`);
        const message = `Preparing the lesson's clips (${n} of ${bySrc.size})…`;
        try {
            const set = await keepalive((async () => {
                const sha = await fetchSource(job, m.src, file);
                return extractFrameSet({ ...job, minFreeBytes: cfg.minFreeBytes }, file, sha,
                    { loop: !!m.loop, source: new URL(m.src, job.base).pathname });
            })(), () => ({ event: { type: 'progress', phase: 'preparing', message } }));
            sets[set.id] = set.dir;
            map[m.src] = { base: `${FRAME_BASE}${set.id}/`, manifest: { ...set.manifest, loop: !!m.loop } };
        } catch (err) {
            if (err instanceof WorkerError && err.code === 'disk_full') throw err;
            // one clip that cannot be loaded or read never stops the render: the page shows that clip from its own element
            const name = String(m.key || new URL(m.src, job.base).pathname.split('/').pop() || 'a clip').slice(0, 80);
            const text = `The clip ${name} could not be prepared frame by frame; it is shown from the video itself.`;
            notes.push({ t: 0, scene: null, text });
            emit({ type: 'note', text, detail: String((err && err.message) || err).slice(0, 200) });
        } finally {
            rmQuiet(file);
        }
    }
    return { sets, map };
}

// ---- the page --------------------------------------------------------------------------------------------------------

// What a render page may never ask the server to do (any non-GET request): save or change the lesson, upload, export, or
// generate media or code (AI pictures and clips, Manim renders and their AI repairs, presenter clips, backgrounds, new
// compositions). A render uses what exists; a scene whose visual is not there yet is a missing visual. Narration (TTS), the
// visual plans and the quality check stay allowed: they make or read what the lesson already says.
export const RENDER_DENIED_SOURCES = [
    '^/(save-history|upload-image|upload-media|generate-ai-video|generate-ai-image|regenerate-manim|render|start-ai-server|generate-script|api/register)$',
    '^/api/(editor|exports|assets|ai-media|source-documents|source-analyses|studio)(/|$)',
    '^/api/presenters/(generate|profiles|review)(/|$)',
    '^/api/cinematic/(background|regenerate|review|style|direction/regenerate|direction/review)(/|$)',
    '^/api/visuals/review(/|$)',
    '/checkpoint$'
];
export function deniedInRender(method, pathname) {
    const m = String(method || 'GET').toUpperCase();
    if (m === 'GET' || m === 'HEAD') return false;
    return RENDER_DENIED_SOURCES.some(source => new RegExp(source).test(pathname));
}

// Runs in the page before any of its scripts (every frame): render mode, the login, the teacher's playback settings,
// silent speech, and no writes or generation a render never needs
function pageInit(init) {
    try {
        if (window.top === window) {
            window.__AADHI_RENDER__ = init.render;
            try {
                localStorage.setItem('jwt_token', init.token);
                for (const [key, value] of Object.entries(init.storage || {})) localStorage.setItem(key, value);
            } catch (e) { /* storage blocked: render mode reports it */ }
        }
        if (window.speechSynthesis) {
            window.speechSynthesis.speak = () => {};
            window.speechSynthesis.cancel = () => {};
        }
        const rules = (init.denied || []).map(source => new RegExp(source));
        const denied = (method, url) => {
            const m = String(method || 'GET').toUpperCase();
            if (m === 'GET' || m === 'HEAD') return false;
            let p;
            try { p = new URL(String(url), location.href).pathname; } catch (e) { return false; }
            return rules.some(rule => rule.test(p));
        };
        const realFetch = window.fetch;
        if (realFetch) {
            window.fetch = function (resource, config) {
                const url = typeof resource === 'string' || resource instanceof URL ? resource : resource && resource.url;
                const method = (config && config.method) || (resource && typeof resource === 'object' && resource.method) || 'GET';
                if (denied(method, url)) return Promise.reject(new TypeError('Not available while a video is rendered.'));
                return realFetch.apply(this, arguments);
            };
        }
        const open = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function (method, url) {
            if (denied(method, url)) throw new TypeError('Not available while a video is rendered.');
            return open.apply(this, arguments);
        };
    } catch (e) { /* a frame without these APIs */ }
}

function storageValues(job) {
    const out = {};
    const given = (job.settings && job.settings.storage) || {};
    for (const key of STORAGE_KEYS) {
        if (!(key in given)) continue;
        const value = given[key];
        out[key] = typeof value === 'string' ? value : JSON.stringify(value);
    }
    return out;
}

class Session {
    constructor(job, cfg, browser) {
        this.job = job;
        this.cfg = cfg;
        this.browser = browser;
        this.pages = new Set();
        this.sets = {};
        this.cancelled = false;
        this.failure = null; // a range failed: the others stop too
        this.notes = []; // what the video shows differently from the preview (a clip shown from its own element)
        this.timings = []; // per page: time, pre-roll and capture frames and seconds (they improve the next plan)
        this.pageErrors = 0;
    }

    // A fresh context and page in render mode for a range (rangeFields: where it starts, what it captures); the virtual clock
    // is installed first
    async open(fields) {
        const { job, cfg } = this;
        const context = await this.browser.newContext({ viewport: { width: cfg.width, height: cfg.height }, deviceScaleFactor: 1,
            locale: 'en-US', timezoneId: 'UTC', serviceWorkers: 'block' });
        const page = await context.newPage();
        await page.clock.install({ time: CLOCK_START });
        const tts = (job.settings && job.settings.tts) || null;
        await context.addInitScript(pageInit, {
            token: job.token, storage: storageValues(job), denied: RENDER_DENIED_SOURCES,
            render: { fps: cfg.fps, seed: cfg.seed, ...fields, toScene: fields.toScene === undefined ? null : fields.toScene,
                missingVisuals: job.missingVisuals, frameBase: FRAME_BASE, projectId: job.projectId, ...(tts ? { tts } : {}) }
        });
        await context.route(`${job.base}${FRAME_BASE}**`, route => {
            const want = frameRequest(route.request().url());
            const dir = want && this.sets[want.setId];
            if (!dir) return route.fulfill({ status: 404, body: '' });
            const file = path.join(dir, want.file);
            if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
            const ext = want.file.split('.').pop();
            return route.fulfill({ path: file, contentType: ext === 'json' ? 'application/json' : IMAGE_TYPES[ext],
                headers: { 'Cache-Control': 'max-age=86400' } });
        });
        page.on('pageerror', err => {
            this.pageErrors++;
            if (this.pageErrors <= 5) emit({ type: 'page_warning', message: String((err && err.message) || err).slice(0, 300) });
        });
        const entry = { context, page };
        this.pages.add(entry);
        const url = `${job.base}/?project_id=${job.projectId}`;
        try {
            await page.goto(url, { waitUntil: 'load', timeout: 120000 });
        } catch (e) {
            throw new WorkerError('page_error', 'The lesson page could not be opened on the server.', EXIT.PAGE);
        }
        const started = Date.now();
        while (!(await page.evaluate(() => !!(window.renderMode && typeof window.renderMode.prepare === 'function')).catch(() => false))) {
            this.check();
            if (Date.now() - started > 60000) throw new WorkerError('page_error', 'The lesson page has no render mode.', EXIT.PAGE);
            await sleep(200);
        }
        return entry;
    }

    async close(entry) {
        this.pages.delete(entry);
        await entry.context.close().catch(() => {});
    }

    check() {
        if (this.cancelled) throw new WorkerError('cancelled', 'The render was cancelled.', EXIT.CANCELLED);
        if (this.failure) throw new WorkerError('stopped', 'Another part of the render failed.', EXIT.FAILED);
    }

    async setToken(token) {
        this.job.token = token;
        for (const { page } of this.pages) {
            await page.evaluate(t => { try { localStorage.setItem('jwt_token', t); } catch (e) { /* blocked */ } }, token).catch(() => {});
        }
    }
}

// renderMode.prepare(), reported every few seconds in the page's own words (renderMode.progress; phase 'preparing' for the
// first page, the range pages only keep the worker alive). Fails when the page's progress has not moved for prepareStallMs,
// or after prepareMs.
async function prepare(page, cfg, report = true) {
    let changes = null;
    let movedAt = Date.now();
    const started = Date.now();
    const describe = async () => {
        const progress = await page.evaluate(() => (window.renderMode && window.renderMode.progress) || null).catch(() => null);
        if (progress && progress.changes !== changes) {
            changes = progress.changes;
            movedAt = Date.now();
        }
        if (Date.now() - movedAt > cfg.prepareStallMs || Date.now() - started > cfg.prepareMs) {
            return { fail: new WorkerError('page_error', 'The lesson stopped responding while it was being prepared for rendering.', EXIT.PAGE) };
        }
        return { event: report ? { type: 'progress', phase: 'preparing', message: prepareMessage(progress) } : { type: 'keepalive' } };
    };
    try {
        return await keepalive(page.evaluate(() => window.renderMode.prepare()), describe);
    } catch (e) {
        if (e instanceof WorkerError) throw e;
        throw new WorkerError('page_error', 'The lesson could not be prepared for rendering.', EXIT.PAGE, { detail: String(e.message || e).slice(0, 300) });
    }
}

async function lessonLink(page) {
    return page.evaluate(async () => {
        if (!window.renderMode || typeof window.renderMode.lessonLink !== 'function') return null;
        try {
            const link = await window.renderMode.lessonLink();
            return link && typeof link === 'object' ? { project_id: link.project_id, fingerprint: link.fingerprint, revision: link.revision } : null;
        } catch (e) { return null; }
    }).catch(() => null);
}

// THE adapter between the worker's frame counter and the page (agent R's spike, contract §1): the virtual clock moves to
// frame n+1 (whole milliseconds, no drift), then the page applies that frame and waits for its gate. Only this changes if
// the page's stepping does.
async function stepFrame(page, n, fps = 30) {
    await page.clock.runFor(at(n + 1, fps) - at(n, fps));
    return page.evaluate(t => window.renderMode.frameReady(t), at(n + 1, fps));
}

// After install the virtual clock runs in real time: it is frozen (pauseAt) a moment ahead of the page's own time, since
// it keeps moving while the call travels
async function freezeClock(page) {
    for (const ahead of [200, 1000, 5000]) {
        const now = await page.evaluate(() => Date.now());
        try {
            await page.clock.pauseAt(now + ahead);
            return;
        } catch (e) {
            if (!/past/i.test(String(e && e.message))) throw e;
        }
    }
    throw new WorkerError('page_error', 'The clock of the lesson page could not be stopped.', EXIT.PAGE);
}

// The page's own words when it stops a frame (render mode rejects frameReady: "The render stopped: a clip did not finish
// within 120 seconds (aadhi_left.mp4).") become the render's failure, in place of a generic one
export function pageStopped(err, what) {
    const text = String((err && err.message) || err || '');
    const said = text.match(/The render stopped[^\n]*/);
    if (said) return new WorkerError('page_error', said[0].trim(), EXIT.PAGE);
    if (err instanceof WorkerError) return err;
    return new WorkerError('page_error', `The lesson page failed while ${what}.`, EXIT.PAGE, { detail: text.slice(0, 300) });
}

async function withStall(promise, ms, what) {
    let timer;
    try {
        return await Promise.race([promise, new Promise((_, reject) => {
            timer = setTimeout(() => reject(new WorkerError('page_error', `The lesson stopped responding while ${what}.`, EXIT.PAGE)), ms);
        })]);
    } finally {
        clearTimeout(timer);
    }
}

function sceneLabel(scenes, index) {
    const scene = (scenes || []).find(s => s.index === index);
    return scene && scene.title ? String(scene.title).slice(0, 80) : null;
}

// One range in its own page: pre-roll (not captured), then frames into ffmpeg until the range ends
async function renderRange(session, plan, range, frameMap, ranges) {
    const { job, cfg } = session;
    const last = range.index === ranges.length - 1;
    const fields = rangeFields(range, cfg.prerollMode);
    const preroll = range.index > 0; // a range after the first plays (part of) the lesson before it first, uncaptured
    const started = Date.now();
    const entry = await session.open(fields);
    const { page, context } = entry;
    const dir = path.join(job.workspace, 'ranges');
    const part = path.join(dir, `range-${range.index}.part.mp4`);
    let encoder = null;
    let ticker = null;
    try {
        const prepared = await prepare(page, cfg, false);
        const missing = (prepared && prepared.preflight && prepared.preflight.missing) || [];
        if (missing.length && job.missingVisuals === 'refuse') {
            throw new WorkerError('missing_visuals', 'Some scenes have no visual yet.', EXIT.MISSING, { missing });
        }
        if (plan.lesson && plan.lesson.fingerprint) {
            const link = await lessonLink(page);
            if (link && link.fingerprint && link.fingerprint !== plan.lesson.fingerprint) {
                throw new WorkerError('lesson_changed', 'The lesson was changed while it was being rendered.', EXIT.PAGE);
            }
        }
        await page.evaluate(map => window.renderMode.useFrames(map), frameMap);
        await freezeClock(page);
        await withStall(page.evaluate(() => window.renderMode.start()).catch(e => { throw pageStopped(e, 'starting'); }), cfg.frameMs, 'starting');
        const cdp = await context.newCDPSession(page);
        encoder = startEncoder(job, part, cfg);
        // a frame may wait long for the server (a narration being made): the worker keeps saying it is alive meanwhile, and
        // the page's own limits (or frameMs) decide when a frame is stuck
        ticker = setInterval(() => emit({ type: 'keepalive' }), KEEPALIVE_MS);
        let r = await withStall(page.evaluate(t => window.renderMode.frameReady(t), at(0, cfg.fps))
            .catch(e => { throw pageStopped(e, 'preparing its first frame'); }), cfg.frameMs, 'preparing its first frame');
        let n = 0;
        let captured = 0;
        let capturing = !preroll;
        let tail = null;
        let reported = 0;
        const loopStart = Date.now();
        let captureStart = capturing ? loopStart : null;
        let prerollFrames = 0;
        const planned = (plan.seams && plan.seams.rates) || null;
        const shared = planned ? Math.pow(Math.max(1, plan.ranges.length), -(planned.alpha || DEFAULT_RATES.alpha)) : 1;
        const checked = { preroll: false, capture: false };
        const checkRate = (stage, frames, since, plannedFps) => {
            if (checked[stage] || !planned || since === null || Date.now() - since < 20000) return;
            checked[stage] = true;
            const warning = rateWarning(stage === 'preroll' ? 'pre-roll' : 'capture', frames / ((Date.now() - since) / 1000), plannedFps * shared);
            if (warning) emit({ ...warning, range: range.index });
        };
        for (;;) {
            session.check();
            r = r || {};
            // captureFrom: the page's own word (null until the range's first scene began); else its scene index
            if (!capturing && ('captureFrom' in r ? r.captureFrom !== null && r.captureFrom !== undefined
                : typeof r.scene === 'number' && r.scene >= range.fromScene)) {
                capturing = true;
                captureStart = Date.now();
                prerollFrames = n;
            }
            if (r.done && tail === null) {
                if (!last) break; // the next range's first scene began: that frame is the next range's first
                tail = cfg.tailFrames;
            }
            if (capturing) {
                const shot = await withStall(cdp.send('Page.captureScreenshot', { format: 'jpeg', quality: cfg.quality, optimizeForSpeed: true }),
                    cfg.stallMs, 'capturing a frame');
                await encoder.write(Buffer.from(shot.data, 'base64'));
                captured++;
                if (captured > cfg.maxFrames) throw new WorkerError('too_long', 'The lesson is longer than this server renders.', EXIT.FAILED);
            }
            if (tail !== null && tail-- <= 0) break;
            if (Date.now() - reported > 1000) {
                reported = Date.now();
                emit({ type: 'frames', range: range.index, frames: captured, scene: typeof r.scene === 'number' ? r.scene : null,
                    title: sceneLabel(plan.scenes, r.scene), capturing });
            }
            if (planned) {
                if (!capturing) checkRate('preroll', n, loopStart, planned.prerollFps);
                else checkRate('capture', captured, captureStart, planned.captureFps);
            }
            r = await withStall(stepFrame(page, n, cfg.fps).catch(e => { throw pageStopped(e, 'rendering a frame'); }), cfg.frameMs,
                'rendering a frame');
            n++;
            if (n % 30 === 0 && await page.evaluate(() => {
                const modal = document.getElementById('auth-modal');
                return !!modal && getComputedStyle(modal).display !== 'none';
            }).catch(() => false)) {
                throw new WorkerError('page_error', 'The lesson page asked to sign in again.', EXIT.PAGE);
            }
        }
        await encoder.finish();
        encoder = null;
        if (!captured) throw new WorkerError('page_error', 'A part of the lesson showed nothing to render.', EXIT.PAGE);
        const timeline = await page.evaluate(() => window.renderMode.timeline());
        const now = Date.now();
        const timing = { seconds: Math.round((now - started) / 100) / 10, overhead: Math.round((loopStart - started) / 100) / 10,
            prerollFrames, prerollSeconds: Math.round(((captureStart || now) - loopStart) / 100) / 10, capturedFrames: captured,
            captureSeconds: Math.round((now - (captureStart || now)) / 100) / 10 };
        const predicted = ((plan.seams && plan.seams.pages) || []).find(pg => pg.page === range.index);
        fs.renameSync(part, path.join(dir, `range-${range.index}.mp4`));
        writeJson(path.join(dir, `range-${range.index}.json`), { ...range, frames: captured, timeline, timing });
        emit({ type: 'range_done', range: range.index, frames: captured, timing, predicted: predicted ? predicted.predicted : null });
        session.timings.push(timing);
        return captured;
    } finally {
        if (ticker) clearInterval(ticker);
        if (encoder) encoder.abort();
        await session.close(entry);
    }
}

function startEncoder(job, out, cfg) {
    const child = spawn(job.ffmpeg, encoderArgs(out, cfg), { stdio: ['pipe', 'ignore', 'pipe'], windowsHide: true });
    let err = '';
    let failed = null;
    const closed = new Promise(resolve => child.on('close', code => resolve(code)));
    child.stderr.on('data', d => { err = (err + d).slice(-4000); });
    child.on('error', e => { failed = e; });
    child.stdin.on('error', e => { failed = failed || e; });
    return {
        async write(buf) {
            const stopped = () => new WorkerError('encode_failed', 'The video encoder stopped.', EXIT.FAILED, { detail: err });
            if (failed || child.exitCode !== null) throw stopped();
            if (child.stdin.write(buf)) return;
            await new Promise((resolve, reject) => { // the encoder may die instead of draining: never wait forever
                const done = error => {
                    child.stdin.off('drain', onDrain);
                    child.off('close', onClose);
                    child.stdin.off('error', onClose);
                    if (error) reject(error); else resolve();
                };
                const onDrain = () => done(null);
                const onClose = () => done(stopped());
                child.stdin.once('drain', onDrain);
                child.once('close', onClose);
                child.stdin.once('error', onClose);
            });
        },
        async finish() {
            child.stdin.end();
            const code = await closed;
            if (code !== 0) throw new WorkerError('encode_failed', 'The video encoder failed.', EXIT.FAILED, { detail: err });
        },
        abort() {
            try { child.stdin.destroy(); } catch (e) { /* closed */ }
            child.kill('SIGKILL');
        }
    };
}

// ---- plan, join -------------------------------------------------------------------------------------------------------

async function makePlan(session) {
    const { job, cfg } = session;
    const file = path.join(job.workspace, 'plan.json');
    const kept = readJson(file);
    if (kept && Array.isArray(kept.ranges) && kept.ranges.length && kept.projectId === job.projectId) {
        cfg.prerollMode = kept.prerollMode === 'scene' ? 'scene' : (kept.prerollMode || 'scene'); // ranges are redone as they were made
        emit({ type: 'plan', ranges: kept.ranges, scenes: kept.scenes.length, resumed: true });
        if (kept.lesson) emit({ type: 'lesson', link: kept.lesson });
        return kept;
    }
    emit({ type: 'progress', phase: 'preparing', message: 'Opening the lesson' });
    const entry = await session.open({ fromScene: 0, toScene: null, preroll: false });
    try {
        const prepared = await prepare(entry.page, cfg, true);
        const scenes = Array.isArray(prepared && prepared.scenes) ? prepared.scenes.map(s => ({
            index: s.index, title: String(s.title || '').slice(0, 200), type: String(s.type || '').slice(0, 40), hidden: !!s.hidden,
            seamSafe: s.seamSafe === true,
            ...(typeof s.estimate === 'number' && Number.isFinite(s.estimate) && s.estimate > 0 ? { estimate: s.estimate } : {}) })) : [];
        const missing = (prepared && prepared.preflight && Array.isArray(prepared.preflight.missing)) ? prepared.preflight.missing : [];
        if (missing.length) emit({ type: 'missing', missing, refused: job.missingVisuals === 'refuse' });
        if (missing.length && job.missingVisuals === 'refuse') {
            throw new WorkerError('missing_visuals', 'Some scenes have no visual yet.', EXIT.MISSING, { missing });
        }
        const lesson = await lessonLink(entry.page);
        if (lesson) emit({ type: 'lesson', link: lesson });
        // the lesson version the frames show: the page's reading after prepare() (which may store visual plans), else the server's
        const layout = layoutOf(job.layout);
        const seamPlan = cfg.prerollMode === 'scene' ? planSeams(scenes, cfg.ranges)
            : planPages(scenes, cfg.ranges, { ...readRates(job.renderDir, layout), ...(job.rates || {}) }, cfg.fps);
        emit({ type: 'seams', mode: cfg.prerollMode, layout: layout.key, ...seamPlan.seams });
        const plan = { version: 1, projectId: job.projectId, fingerprint: (lesson && lesson.fingerprint) || job.fingerprint || null,
            title: (prepared && prepared.title) || '',
            prerollMode: cfg.prerollMode, layout, scenes, ranges: seamPlan.ranges, seams: { mode: cfg.prerollMode, layout: layout.key, ...seamPlan.seams },
            media: Array.isArray(prepared && prepared.media) ? prepared.media : [],
            lesson, missing, createdAt: new Date().toISOString() };
        fs.mkdirSync(path.join(job.workspace, 'ranges'), { recursive: true });
        writeJson(file, plan);
        emit({ type: 'plan', ranges: plan.ranges, scenes: scenes.length, resumed: false });
        return plan;
    } finally {
        await session.close(entry);
    }
}

function finishedRange(job, range) {
    const info = readJson(path.join(job.workspace, 'ranges', `range-${range.index}.json`));
    const video = path.join(job.workspace, 'ranges', `range-${range.index}.mp4`);
    return info && info.frames > 0 && fs.existsSync(video) && fs.statSync(video).size > 0 ? info : null;
}

async function join(job, plan, notes = []) {
    const infos = plan.ranges.map(r => finishedRange(job, r));
    if (infos.some(i => !i)) throw new WorkerError('render_failed', 'A part of the video is missing.');
    const list = path.join(job.workspace, 'ranges', 'list.txt');
    fs.writeFileSync(list, plan.ranges.map(r => `file 'range-${r.index}.mp4'`).join('\n') + '\n');
    const part = path.join(job.workspace, 'video.part.mp4');
    // ranges kept from before B-frames were left out cannot be re-stamped frame by frame: joined as they are
    const bframes = await Promise.all(plan.ranges.map(r => run(job.ffprobe, ['-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'stream=has_b_frames', '-of', 'csv=p=0', path.join(job.workspace, 'ranges', `range-${r.index}.mp4`)], { timeoutMs: 60000 })
        .then(x => Number(String(x.out).trim()) > 0, () => true)));
    const restamp = !bframes.some(Boolean);
    const res = await keepalive(run(job.ffmpeg, joinArgs(list, part, { restamp }), { timeoutMs: 3600000 }), () => ({ event: { type: 'keepalive' } }));
    if (res.code !== 0) throw new WorkerError('encode_failed', 'The parts of the video could not be joined.', EXIT.FAILED, { detail: res.err.slice(-500) });
    const pts = await keepalive(run(job.ffprobe, ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'packet=pts', '-of', 'csv=p=0', part],
        { timeoutMs: 600000 }), () => ({ event: { type: 'keepalive' } }));
    if (restamp && !onGrid(pts.out)) {
        throw new WorkerError('encode_failed', 'The parts of the video could not be joined on exact frame times.', EXIT.FAILED);
    }
    const merged = mergeTimelines(infos);
    merged.lesson = plan.lesson || null;
    merged.title = plan.title || '';
    merged.missing = plan.missing || [];
    merged.notes = [...notes, ...(merged.notes || [])];
    writeJson(path.join(job.workspace, 'timeline.full.json'), merged);
    fs.renameSync(part, path.join(job.workspace, 'video.mp4'));
    return merged;
}

// ---- main -------------------------------------------------------------------------------------------------------------

async function loadPlaywright() {
    try {
        return await import('playwright-core');
    } catch (e) {
        if (process.env.PLAYWRIGHT_CORE_DIR) return import(pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE_DIR, 'index.mjs')).href);
        throw new WorkerError('unavailable', 'The render tools (playwright-core) are not installed on this server.');
    }
}

function readStdin(onLine) {
    let buffer = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => {
        buffer += chunk;
        let i;
        while ((i = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, i).trim();
            buffer = buffer.slice(i + 1);
            if (line) onLine(line);
        }
    });
}

async function main() {
    let session = null;
    let resolveJob;
    const jobArrived = new Promise(resolve => { resolveJob = resolve; });
    let gotJob = false;
    const cancel = () => { if (session) session.cancelled = true; else process.exit(EXIT.CANCELLED); };
    readStdin(line => {
        let msg;
        try { msg = JSON.parse(line); } catch (e) { return; }
        if (!gotJob) { gotJob = true; resolveJob(msg); return; }
        if (msg && msg.type === 'cancel') cancel();
        else if (msg && msg.type === 'token' && typeof msg.token === 'string' && session) session.setToken(msg.token);
    });
    process.stdin.on('end', () => { if (!gotJob) resolveJob(null); });
    process.on('SIGTERM', cancel);
    process.on('SIGINT', cancel);

    const job = await jobArrived;
    const problems = checkJob(job);
    if (problems.length) {
        emit({ type: 'error', code: 'bad_job', message: 'The render job is not valid.', fields: problems });
        return EXIT.BAD_JOB;
    }
    const cfg = settings(job);
    fs.mkdirSync(path.join(job.workspace, 'ranges'), { recursive: true });
    let browser = null;
    try {
        const { chromium } = await loadPlaywright();
        emit({ type: 'progress', phase: 'preparing', message: 'Starting the renderer' });
        browser = await chromium.launch({ channel: job.channel || 'chrome', headless: true,
            args: ['--mute-audio', '--disable-audio-output', '--autoplay-policy=no-user-gesture-required', '--hide-scrollbars',
                '--force-device-scale-factor=1', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                // text edges drawn the same way on every run: two renders of one lesson give bit-identical frames
                '--font-render-hinting=none', '--disable-lcd-text', '--disable-font-subpixel-positioning'] });
        emit({ type: 'browser', version: browser.version() });
        session = new Session(job, cfg, browser);
        const plan = await makePlan(session);
        session.check();
        const todo = plan.ranges.filter(r => !finishedRange(job, r));
        const kept = plan.ranges.length - todo.length;
        if (kept) emit({ type: 'kept', ranges: plan.ranges.filter(r => finishedRange(job, r)).map(r => ({ index: r.index, frames: finishedRange(job, r).frames })) });
        if (todo.length) {
            const { sets, map } = await frameSets(job, cfg, plan.media, session.notes);
            session.sets = sets;
            emit({ type: 'progress', phase: 'capturing', message: 'Rendering the video' });
            let next = 0;
            const lanes = Array.from({ length: Math.min(cfg.parallel, todo.length) }, async () => {
                while (next < todo.length && !session.failure && !session.cancelled) {
                    const range = todo[next++];
                    try {
                        await renderRange(session, plan, range, map, plan.ranges);
                    } catch (err) {
                        if (!session.failure && !(err instanceof WorkerError && err.code === 'stopped')) session.failure = err;
                    }
                }
            });
            await Promise.all(lanes);
            if (session.failure) throw session.failure;
        }
        session.check();
        emit({ type: 'progress', phase: 'joining', message: 'Joining the parts of the video' });
        const merged = await join(job, plan, session.notes);
        if (session.timings.length && plan.prerollMode !== 'scene') {
            const sum = key => session.timings.reduce((a, t) => a + (t[key] || 0), 0);
            const layout = layoutOf(plan.layout || job.layout);
            const rates = updateRates(job.renderDir, {
                captureFps: sum('captureSeconds') ? sum('capturedFrames') / sum('captureSeconds') : null,
                prerollFps: sum('prerollSeconds') > 1 ? sum('prerollFrames') / sum('prerollSeconds') : null,
                overhead: sum('overhead') / session.timings.length }, plan.ranges.length, readRates(job.renderDir, layout).alpha, layout);
            emit({ type: 'rates', layout: layout.key, ...rates });
        }
        emit({ type: 'done', frames: merged.frames, duration: merged.duration, video: 'video.mp4', timeline: 'timeline.full.json',
            page_warnings: session.pageErrors });
        return EXIT.OK;
    } catch (err) {
        const e = err instanceof WorkerError ? err : new WorkerError('render_failed', 'The render stopped because of an unexpected problem.', EXIT.FAILED,
            { detail: String((err && err.stack) || err).slice(0, 800) });
        if (session && session.cancelled && e.exitCode !== EXIT.MISSING) {
            emit({ type: 'error', code: 'cancelled', message: 'The render was cancelled.' });
            return EXIT.CANCELLED;
        }
        emit({ type: 'error', code: e.code, message: e.message, ...(e.missing ? { missing: e.missing } : {}), ...(e.detail ? { detail: e.detail } : {}) });
        return e.exitCode;
    } finally {
        if (browser) await browser.close().catch(() => {});
    }
}

const entry = process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;
if (entry) {
    main().then(code => { process.exitCode = code; setTimeout(() => process.exit(code), 2000).unref(); },
        err => { emit({ type: 'error', code: 'render_failed', message: 'The renderer failed to start.', detail: String(err).slice(0, 300) }); process.exit(EXIT.FAILED); });
}
