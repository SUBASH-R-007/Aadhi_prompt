// Video checks for exported lessons (Phase 22): what a file is, whether its frames come at a constant rate, black edges,
// single frames, frame differences, frozen stretches, the frame a region changes on, and how loud its sound is. ffmpeg /
// ffprobe only (on PATH); nothing is ever played. Used by tests/render_export_browser_check.mjs and the QA scripts.
//
//   probe(file)                          size, codecs, frame rates, duration, frame count, audio stream, loudness
//   frameTimes(file)                     every video frame's time (s), in presentation order
//   cfrStats(times, fps) / checkCfr(file, fps)   constant frame rate: intervals, gaps, the expected frame count
//   blackEdges(file, times)              letterbox / pillarbox: the mean brightness of the outer 8 px strips
//   frameAt(file, t, out)                the frame nearest t as an image
//   meanDiff(a, tA, b, tB)               mean absolute RGB difference (of 255) on a 32x18 grid: the Phase 14-20 method
//   frozenRun(file, from, to)            the longest stretch of identical consecutive frames in a region
//   syncFrame(file, from, to, region)    the first frame on which a region changes
//   sharpness(file, t, region)           how sharp code / formulas / small labels are (compare the export with the preview)
//   colourArea(file, t, test, region)    pixels of a colour (the notice card's gold button: isNoticeGold)
//   audioLevels / audioOnset / loudness  short-window levels, the first sound after a moment, volumedetect
//   parseVtt / parseChapters             the export's text outputs
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';

const BIG = 1 << 30; // raw frames can be large; spawnSync's buffer must hold them
const HALF = 0.5; // half a frame: "the frame nearest t" selects from t - half a frame

function tool(cmd, args, encoding = 'buffer') {
    const r = spawnSync(cmd, args, { encoding, maxBuffer: BIG, windowsHide: true });
    if (r.error) throw r.error;
    return r;
}
export function ffmpeg(...args) {
    const r = tool('ffmpeg', ['-v', 'error', '-nostdin', '-y', ...args]);
    if (r.status !== 0) throw new Error('ffmpeg failed: ' + String(r.stderr).slice(-600));
    return r.stdout;
}
function ffprobeJson(args) {
    const r = tool('ffprobe', ['-v', 'error', ...args, '-of', 'json'], 'utf8');
    if (r.status !== 0) throw new Error('ffprobe failed: ' + String(r.stderr).slice(-600));
    return JSON.parse(r.stdout || '{}');
}
const num = v => (v === undefined || v === null || v === '' || v === 'N/A' ? null : Number(v));
const rate = v => { // "30/1" -> 30
    if (!v || v === '0/0') return null;
    const [a, b] = String(v).split('/').map(Number);
    return b ? a / b : a;
};
const round = (v, n = 3) => (v === null || v === undefined || !Number.isFinite(v) ? v : +v.toFixed(n));

// ---- what the file is ------------------------------------------------------------------------------------------------------
// opts.count: also count the video packets (exact frame count without decoding; a WebM has no nb_frames)
// opts.volume: also measure the sound (volumedetect over the whole file)
export function probe(file, opts = {}) {
    const { count = true, volume = true } = opts;
    const data = ffprobeJson([...(count ? ['-count_packets'] : []), '-show_entries',
        'format=duration,size,bit_rate,format_name:stream=index,codec_type,codec_name,profile,width,height,pix_fmt,avg_frame_rate,r_frame_rate,nb_frames,nb_read_packets,duration,sample_rate,channels,time_base,start_time',
        file]);
    const streams = data.streams || [];
    const v = streams.find(s => s.codec_type === 'video') || null;
    const a = streams.find(s => s.codec_type === 'audio') || null;
    const out = {
        file, size: num((data.format || {}).size) ?? (fs.existsSync(file) ? fs.statSync(file).size : null),
        container: (data.format || {}).format_name || null, duration: num((data.format || {}).duration),
        width: v ? v.width : null, height: v ? v.height : null, codec: v ? v.codec_name : null, profile: v ? v.profile || null : null,
        pixFmt: v ? v.pix_fmt || null : null, avgFrameRate: v ? v.avg_frame_rate : null, rFrameRate: v ? v.r_frame_rate : null,
        fps: v ? rate(v.avg_frame_rate) : null, videoDuration: v ? num(v.duration) : null, videoStart: v ? num(v.start_time) : null,
        frames: v ? (num(v.nb_frames) ?? num(v.nb_read_packets)) : null, packets: v ? num(v.nb_read_packets) : null,
        audio: a ? { codec: a.codec_name, sampleRate: num(a.sample_rate), channels: num(a.channels), duration: num(a.duration) } : null,
        meanVolume: null, maxVolume: null
    };
    if (volume && a) Object.assign(out, (({ mean, max }) => ({ meanVolume: mean, maxVolume: max }))(loudness(file)));
    return out;
}

// ---- frame timing -----------------------------------------------------------------------------------------------------------
// Every video frame's presentation time in seconds, sorted. opts.packets: read packet times (no decoding; much faster on long
// files, identical for intra / P-frame streams once sorted)
export function frameTimes(file, opts = {}) {
    const what = opts.packets ? 'packet=pts_time,dts_time' : 'frame=pts_time,best_effort_timestamp_time,pkt_dts_time';
    const r = tool('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', what, '-of', 'csv=p=0', file], 'utf8');
    if (r.status !== 0) throw new Error('ffprobe failed: ' + String(r.stderr).slice(-600));
    const times = [];
    for (const line of String(r.stdout).split(/\r?\n/)) {
        const t = line.split(',').map(x => parseFloat(x)).find(Number.isFinite);
        if (t !== undefined) times.push(t);
    }
    return times.sort((x, y) => x - y);
}

// Interval statistics of frame times against a constant rate: tolMs is how far an interval may be from 1/fps
export function cfrStats(times, fps = 30, tolMs = 1) {
    const step = 1 / fps;
    const iv = [];
    for (let i = 1; i < times.length; i++) iv.push(times[i] - times[i - 1]);
    const sorted = [...iv].sort((x, y) => x - y);
    const off = iv.map((d, i) => ({ i: i + 1, t: times[i + 1], ms: d * 1000 })).filter(x => Math.abs(x.ms - step * 1000) > tolMs);
    const span = times.length > 1 ? times[times.length - 1] - times[0] : 0;
    return {
        frames: times.length, first: round(times[0]), last: round(times[times.length - 1]),
        effectiveFps: span > 0 ? round((times.length - 1) / span, 2) : null,
        intervalMs: iv.length ? { min: round(sorted[0] * 1000, 2), median: round(sorted[sorted.length >> 1] * 1000, 2),
            mean: round(iv.reduce((s, d) => s + d, 0) / iv.length * 1000, 2), max: round(sorted[sorted.length - 1] * 1000, 2) } : null,
        offIntervals: off.length, firstOff: off.slice(0, 5).map(x => ({ frame: x.i, t: round(x.t), ms: round(x.ms, 2) })),
        gapsOver100ms: iv.filter(d => d > 0.1).length, gapsOver200ms: iv.filter(d => d > 0.2).length,
        maxGapMs: iv.length ? round(sorted[sorted.length - 1] * 1000, 1) : null,
        // frames that a constant-rate video would have shown but this one did not (each gap counted in whole frames)
        missingFrames: iv.reduce((s, d) => s + Math.max(0, Math.round(d / step) - 1), 0)
    };
}

// The rendered export's contract: every interval 1/fps within tolMs, the stream says fps both ways, and the frame count is
// round(duration x fps) +- 1 (duration: the video stream's, else the container's)
export function checkCfr(file, fps = 30, opts = {}) {
    const p = opts.probe || probe(file, { volume: false });
    const times = frameTimes(file, opts);
    const stats = cfrStats(times, fps, opts.tolMs ?? 1);
    const duration = p.videoDuration ?? p.duration;
    const expected = Number.isFinite(duration) ? Math.round(duration * fps) : null;
    const declared = rate(p.avgFrameRate) === fps && rate(p.rFrameRate) === fps;
    const ok = stats.offIntervals === 0 && declared && expected !== null && Math.abs(stats.frames - expected) <= 1;
    return { ok, fps, declared, avgFrameRate: p.avgFrameRate, rFrameRate: p.rFrameRate, duration: round(duration),
        formatDuration: round(p.duration), expectedFrames: expected, ...stats };
}

// ---- decoding frames -------------------------------------------------------------------------------------------------------
// Raw frames of [from, to) (or `count` frames from `from`), each with its source time. crop {x, y, w, h} in source pixels;
// size {w, h} scales with area averaging (identical input frames stay identical); format 'gray' | 'rgb24'. ffmpeg's accurate
// seek with -copyts keeps the source times; showinfo reports each frame's pts_time.
export function readFrames(file, { from = 0, to = null, count = null, crop = null, size = null, format = 'gray', fps = 30 } = {}) {
    const start = Math.max(0, from - HALF / fps);
    const filters = ['showinfo'];
    if (crop) filters.push(`crop=${Math.round(crop.w)}:${Math.round(crop.h)}:${Math.round(crop.x)}:${Math.round(crop.y)}`);
    if (size) filters.push(`scale=${size.w}:${size.h}:flags=area`);
    filters.push(`format=${format}`);
    const n = count ?? Math.max(1, Math.ceil(((to ?? from + 1) - start) * fps + 1));
    const args = ['-v', 'info', '-nostdin', '-hide_banner', '-copyts', ...(start > 0 ? ['-ss', start.toFixed(4)] : []), '-i', file,
        '-map', '0:v:0', '-vf', filters.join(','), '-frames:v', String(n), '-fps_mode', 'passthrough', '-f', 'rawvideo', '-'];
    const r = tool('ffmpeg', args);
    if (r.status !== 0) throw new Error('ffmpeg failed: ' + String(r.stderr).slice(-600));
    // exact times from the integer pts and the time base (showinfo prints pts_time to 4 decimals only)
    const tb = /config in time_base:\s*(\d+)\/(\d+)/.exec(String(r.stderr));
    const times = [...String(r.stderr).matchAll(/n:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:(-?[\d.]+)/g)]
        .map(m => (tb && +tb[2] ? +m[1] * +tb[1] / +tb[2] : parseFloat(m[2])));
    const w = size ? size.w : crop ? Math.round(crop.w) : null;
    const h = size ? size.h : crop ? Math.round(crop.h) : null;
    let dims = w && h ? { w, h } : null;
    if (!dims) { // full frames: the stream's own size
        const p = ffprobeJson(['-select_streams', 'v:0', '-show_entries', 'stream=width,height', file]).streams[0];
        dims = { w: p.width, h: p.height };
    }
    const bpp = format === 'rgb24' ? 3 : 1;
    const frameBytes = dims.w * dims.h * bpp;
    const frames = [];
    for (let i = 0; i + frameBytes <= r.stdout.length; i += frameBytes) frames.push(r.stdout.subarray(i, i + frameBytes));
    // frames before `from` (the half-frame margin) and after `to` are dropped; times line up with frames in order
    const list = frames.map((buf, i) => ({ t: times[i], buf })).filter(f => f.t === undefined || (f.t >= from - HALF / fps - 0.001 && (to === null || f.t < to)));
    return { w: dims.w, h: dims.h, format, frames: list.map(f => f.buf), times: list.map(f => f.t) };
}

const meanAbs = (a, b) => {
    let s = 0;
    for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
    return s / a.length;
};

// ---- single frames -----------------------------------------------------------------------------------------------------------
// The frame nearest t (seconds) written as an image (png / jpg by extension); opts.scale: e.g. '640:-1'
export function frameAt(file, t, out, opts = {}) {
    const fps = opts.fps || 30;
    const target = Math.max(0, t - HALF / fps);
    const start = Math.max(0, target - 1);
    const vf = [`select=gte(t\\,${target.toFixed(4)})`, ...(opts.scale ? [`scale=${opts.scale}`] : [])].join(',');
    const quality = /\.jpe?g$/i.test(out) ? ['-q:v', '2'] : [];
    if (fs.existsSync(out)) fs.rmSync(out);
    ffmpeg('-copyts', ...(start > 0 ? ['-ss', start.toFixed(4)] : []), '-i', file, '-map', '0:v:0', '-vf', vf, '-frames:v', '1',
        '-fps_mode', 'passthrough', ...quality, out);
    // past the last frame (a variable-rate file ends before its duration says): the file's final frame
    if (!fs.existsSync(out)) ffmpeg('-sseof', '-1.5', '-i', file, '-map', '0:v:0', '-update', '1', '-fps_mode', 'passthrough', ...quality, out);
    if (!fs.existsSync(out)) throw new Error(`no frame at ${t}s in ${file}`);
    return out;
}

// A picture (t null: an image file) or a video frame (the one nearest t) as a w x h RGB grid (area-averaged)
export function grid(file, t = null, w = 32, h = 18, fps = 30) {
    if (t === null || t === undefined) return [...ffmpeg('-i', file, '-vf', `scale=${w}:${h}:flags=area`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')];
    const f = readFrames(file, { from: t, count: 1, size: { w, h }, format: 'rgb24', fps });
    if (!f.frames.length) throw new Error(`no frame at ${t}s in ${file}`);
    return [...f.frames[0]];
}

// Mean absolute difference over RGB (of 255) between two pictures / frames on a 32x18 grid (the Phase 14-20 parity method).
// opts.rows: compare only the top N grid rows (Phase 20 left out the bottom two: the preview's player controls)
// opts.mask: [{x, y, w, h}] areas (in pixels of a frame of opts.frame = {w, h}, default 1920x1080) whose grid cells are left out
// (a cell is left out when its centre is inside one)
export function meanDiff(fileA, tA, fileB, tB, opts = {}) {
    const { w = 32, h = 18, rows = h, mask = [], frame = { w: 1920, h: 1080 } } = opts;
    const a = grid(fileA, tA, w, h);
    const b = grid(fileB, tB, w, h);
    let s = 0, n = 0;
    for (let y = 0; y < rows; y++) for (let x = 0; x < w; x++) {
        const cx = (x + 0.5) * frame.w / w, cy = (y + 0.5) * frame.h / h;
        if (mask.some(m => cx >= m.x && cx < m.x + m.w && cy >= m.y && cy < m.y + m.h)) continue;
        for (let c = 0; c < 3; c++) { const i = (y * w + x) * 3 + c; s += Math.abs(a[i] - b[i]); n++; }
    }
    return n ? round(s / n, 2) : null;
}

// ---- black edges -------------------------------------------------------------------------------------------------------------
const luma = (buf, i) => 0.2126 * buf[i] + 0.7152 * buf[i + 1] + 0.0722 * buf[i + 2];
function regionStats(buf, W, x0, y0, x1, y1) {
    let s = 0, s2 = 0, n = 0;
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
        const v = luma(buf, (y * W + x) * 3);
        s += v; s2 += v * v; n++;
    }
    const mean = s / n;
    return { mean: round(mean, 1), sd: round(Math.sqrt(Math.max(0, s2 / n - mean * mean)), 1) };
}
// How many columns (or rows) from an edge are uniformly near-black: a baked-in bar's width in pixels
function barWidth(buf, W, H, side, dark, limit) {
    const line = k => {
        if (side === 'left' || side === 'right') {
            const x = side === 'left' ? k : W - 1 - k;
            return regionStats(buf, W, x, 0, x + 1, H);
        }
        const y = side === 'top' ? k : H - 1 - k;
        return regionStats(buf, W, 0, y, W, y + 1);
    };
    let k = 0;
    while (k < limit) { const s = line(k); if (s.mean >= dark || s.sd > 6) break; k++; }
    return k;
}
// How straight a dark edge is: for every 8th line across the side, the run of dark pixels from the edge (capped at a quarter of
// the frame). A letterbox / pillarbox ends on one straight line (the runs agree within 2 px on >= 90 % of the lines, short of
// the cap); a dark page background with content on it does not.
function straightEdge(buf, W, H, side, dark) {
    const vertical = side === 'left' || side === 'right';
    const lines = vertical ? H : W;
    const cap = Math.round((vertical ? W : H) / 4);
    const runs = [];
    for (let k = 0; k < lines; k += 8) {
        let n = 0;
        while (n < cap) {
            const x = vertical ? (side === 'left' ? n : W - 1 - n) : k;
            const y = vertical ? k : (side === 'top' ? n : H - 1 - n);
            if (luma(buf, (y * W + x) * 3) >= dark) break;
            n++;
        }
        runs.push(n);
    }
    const sorted = [...runs].sort((a, b) => a - b);
    const median = sorted[sorted.length >> 1];
    const agree = runs.filter(n => Math.abs(n - median) <= 2).length / runs.length;
    return { run: median, straight: median > 0 && median < cap && agree >= 0.9 };
}
// For each time: the mean brightness (luma, 0-255 full range) of the outer `strip` px on each side and of the frame's centre
// half. A side is flagged (a letterbox / pillarbox) when its strip is dark (< dark) and nearly uniform, the dark edge ends on
// one straight line, and the centre is not dark (> centreMin). `bars` is each side's run of uniformly dark lines (e.g. 72 px
// for aadhi_left.mp4's baked-in pillars drawn at 1920 wide).
export function blackEdges(file, times, opts = {}) {
    const { strip = 8, dark = 16, centreMin = 40, fps = 30 } = opts;
    return times.map(t => {
        const f = readFrames(file, { from: t, count: 1, format: 'rgb24', fps });
        if (!f.frames.length) return { t, error: 'no frame' };
        const { w: W, h: H } = f;
        const buf = f.frames[0];
        const sides = {
            left: regionStats(buf, W, 0, 0, strip, H), right: regionStats(buf, W, W - strip, 0, W, H),
            top: regionStats(buf, W, 0, 0, W, strip), bottom: regionStats(buf, W, 0, H - strip, W, H)
        };
        const centre = regionStats(buf, W, W >> 2, H >> 2, (3 * W) >> 2, (3 * H) >> 2);
        const edges = Object.fromEntries(Object.keys(sides).map(k => [k, straightEdge(buf, W, H, k, dark)]));
        const flagged = Object.entries(sides).filter(([k, s]) => s.mean < dark && s.sd <= 8 && edges[k].straight && centre.mean > centreMin).map(([k]) => k);
        const bars = Object.fromEntries(Object.keys(sides).map(k => [k, barWidth(buf, W, H, k, dark, Math.round((k === 'left' || k === 'right' ? W : H) / 4))]));
        // dark but not a bar: a uniformly dark strip without one straight edge (a dark page background), reported for a look
        const darkSides = Object.entries(sides).filter(([k, s]) => s.mean < dark && !flagged.includes(k)).map(([k]) => k);
        return { t: round(f.times[0] ?? t), left: sides.left.mean, right: sides.right.mean, top: sides.top.mean, bottom: sides.bottom.mean,
            centre: centre.mean, bars, flagged, darkSides };
    });
}

// ---- motion ------------------------------------------------------------------------------------------------------------------
// Consecutive-frame differences (mean absolute luma, of 255) in a region (crop {x,y,w,h}; default the whole frame), scaled down
// to `size` with area averaging
export function frameDiffs(file, from, to, opts = {}) {
    const { crop = null, size = { w: 192, h: 108 }, fps = 30 } = opts;
    const f = readFrames(file, { from, to, crop, size, format: 'gray', fps });
    const diffs = [];
    for (let i = 1; i < f.frames.length; i++) diffs.push(meanAbs(f.frames[i], f.frames[i - 1]));
    return { times: f.times, diffs };
}

// The longest stretch of identical consecutive frames in [from, to): `longest` is the number of frames showing one picture
// (1 = every frame differs from the one before; 2 = one repeat, the 24 -> 30 fps pattern; more = a freeze). Also the repeat
// count and the histogram of stretch lengths, so a caller can check the 24 -> 30 pattern (one repeat in every 5 frames).
// "Identical" after H.264 is never exactly 0: x264 re-codes a repeated picture with a small residual (measured on Aadhi's clip
// through the worker's JPEG -> x264 pipeline: repeats 0.03-0.16, the slowest real motion 0.18). So a pair counts as identical
// when its difference is below `eps`, or below `rel` x the median of its neighbours (+-6 frames) and below `epsMax`.
export function frozenRun(file, from, to, opts = {}) {
    const { eps = 0.1, rel = 0.55, epsMax = 0.5, fps = 30 } = opts;
    const { times, diffs } = frameDiffs(file, from, to, opts);
    const localMedian = i => {
        const near = diffs.slice(Math.max(0, i - 6), i).concat(diffs.slice(i + 1, i + 7)).sort((x, y) => x - y);
        return near.length ? near[near.length >> 1] : Infinity;
    };
    const same = diffs.map((d, i) => d < eps || (d < epsMax && d < rel * localMedian(i)));
    const runs = {};
    let longest = 1, at = times[0] ?? from, cur = 1, curStart = times[0];
    for (let i = 0; i < diffs.length; i++) {
        if (same[i]) {
            cur += 1;
            if (cur > longest) { longest = cur; at = curStart; }
        } else {
            if (cur > 1) runs[cur] = (runs[cur] || 0) + 1;
            cur = 1;
            curStart = times[i + 1];
        }
    }
    if (cur > 1) runs[cur] = (runs[cur] || 0) + 1;
    const repeats = same.filter(Boolean).length;
    const sorted = [...diffs].sort((x, y) => x - y);
    // the frame numbers (round(t x fps)) that repeat the one before: for 24 -> 30 they fall on one residue modulo 5
    const repeatFrames = same.map((s, i) => (s ? Math.round(times[i + 1] * fps) : null)).filter(n => n !== null);
    return { frames: times.length, longest, longestSeconds: round(longest / fps, 3), at: round(at), repeats, repeatFrames,
        repeatShare: diffs.length ? round(repeats / diffs.length, 3) : 0, runs,
        diff: diffs.length ? { min: round(sorted[0], 3), median: round(sorted[sorted.length >> 1], 3), max: round(sorted[sorted.length - 1], 3) } : null,
        diffs: diffs.map(d => round(d, 3)) };
}

// The first frame in [from, to) on which a region (crop {x,y,w,h}) differs from how it looked at `from`: its index
// (round(t x fps)), time and difference. The baseline is the per-pixel mean of the first `settle` frames. Two measures: the
// mean absolute difference (any change) and the absolute mean signed difference (text fading in brightens or darkens the region
// as a whole, while noise and small motion average out). Each has its noise floor from the settle frames; a change counts when
// either exceeds its threshold. Start the window several frames (>= settle) before the expected moment.
export function syncFrame(file, from, to, region, opts = {}) {
    const { settle = 4, minDiff = 1.0, minShift = 0.5, fps = 30, maxWidth = 320 } = opts;
    const whole = !region;
    if (whole) { // the whole frame, scaled down like any region
        const d = ffprobeJson(['-select_streams', 'v:0', '-show_entries', 'stream=width,height', file]).streams[0];
        region = { x: 0, y: 0, w: d.width, h: d.height };
    }
    const scale = region.w > maxWidth ? maxWidth / region.w : 1;
    const size = { w: Math.max(2, Math.round(region.w * scale / 2) * 2), h: Math.max(2, Math.round(region.h * scale / 2) * 2) };
    const f = readFrames(file, { from, to, crop: whole ? null : region, size, format: 'gray', fps });
    if (f.frames.length <= settle) return { frame: null, t: null, error: 'too few frames' };
    const len = f.frames[0].length;
    const base = new Float64Array(len);
    for (let k = 0; k < settle; k++) for (let i = 0; i < len; i++) base[i] += f.frames[k][i] / settle;
    const measure = b => {
        let abs = 0, signed = 0;
        for (let i = 0; i < len; i++) { const d = b[i] - base[i]; abs += Math.abs(d); signed += d; }
        return { abs: abs / len, shift: Math.abs(signed / len) };
    };
    const series = f.frames.map(measure);
    const noiseAbs = Math.max(...series.slice(0, settle).map(s => s.abs));
    const noiseShift = Math.max(...series.slice(0, settle).map(s => s.shift));
    const thrAbs = Math.max(minDiff, noiseAbs * 1.5 + 0.5);
    const thrShift = Math.max(minShift, noiseShift * 3 + 0.3);
    const i = series.findIndex((s, k) => k >= settle && (s.abs > thrAbs || s.shift > thrShift));
    return { frame: i >= 0 ? Math.round(f.times[i] * fps) : null, t: i >= 0 ? round(f.times[i]) : null,
        diff: i >= 0 ? round(series[i].abs, 2) : null, shift: i >= 0 ? round(series[i].shift, 2) : null,
        noise: round(noiseAbs, 2), threshold: round(thrAbs, 2), shiftThreshold: round(thrShift, 2), from: round(f.times[0]),
        series: series.map((s, k) => [Math.round(f.times[k] * fps), round(s.abs, 2), round(s.shift, 2)]) };
}

// ---- sharpness ---------------------------------------------------------------------------------------------------------------
// How sharp a region is (code, formulas, small labels): the mean absolute luma step between neighbouring pixels at full size, of
// a picture (t null) or the video frame nearest t. Compare the export with the preview: blur, JPEG and H.264 softening lower it.
export function sharpness(file, t, region, fps = 30) {
    let buf, w, h;
    if (t === null || t === undefined) {
        buf = ffmpeg('-i', file, '-vf', `crop=${region.w}:${region.h}:${region.x}:${region.y},format=gray`, '-f', 'rawvideo', '-');
        w = region.w; h = region.h;
    } else {
        const f = readFrames(file, { from: t, count: 1, crop: region, format: 'gray', fps });
        buf = f.frames[0]; w = f.w; h = f.h;
    }
    let s = 0, n = 0;
    for (let y = 0; y < h - 1; y++) for (let x = 0; x < w - 1; x++) {
        const i = y * w + x;
        s += Math.abs(buf[i + 1] - buf[i]) + Math.abs(buf[i + w] - buf[i]);
        n += 2;
    }
    return round(s / n, 3);
}

// ---- colours -----------------------------------------------------------------------------------------------------------------
// How many pixels of the frame nearest t (in region, default the whole frame) a predicate accepts: ({r,g,b}) => bool. Used for
// the notice card's gold button (#FFD700 -> #FF8A00), which a recording must never show
export function colourArea(file, t, test, region = null, fps = 30) {
    const f = t === null ? null : readFrames(file, { from: t, count: 1, crop: region, format: 'rgb24', fps });
    let buf, w, h;
    if (f) { buf = f.frames[0]; w = f.w; h = f.h; } else { // an image file
        const probeOut = ffprobeJson(['-show_entries', 'stream=width,height', file]).streams[0];
        const c = region || { x: 0, y: 0, w: probeOut.width, h: probeOut.height };
        buf = ffmpeg('-i', file, '-vf', `crop=${c.w}:${c.h}:${c.x}:${c.y}`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-');
        w = c.w; h = c.h;
    }
    if (!buf) return { pixels: 0, share: 0 };
    let n = 0;
    for (let i = 0; i + 2 < buf.length; i += 3) if (test({ r: buf[i], g: buf[i + 1], b: buf[i + 2] })) n++;
    return { pixels: n, share: round(n / (w * h), 4) };
}
// The gold of .btn-gold (a 135deg gradient from #FFD700 to #FF8A00): red full, green between, no blue (pure yellow and white
// left out by r - g >= 30)
export const isNoticeGold = ({ r, g, b }) => r >= 232 && g >= 120 && g <= 228 && b <= 64 && r - g >= 30;
// A filled block of a colour (a button), not text in that colour: the rows of the region whose longest run of accepted pixels is
// at least minRun px. Gold heading letters give short runs; the notice card's gold button gives dozens of rows of 100+ px.
export function colourRows(file, t, test, region, minRun = 90, fps = 30) {
    let buf, w, h;
    if (t === null || t === undefined) {
        buf = ffmpeg('-i', file, '-vf', `crop=${region.w}:${region.h}:${region.x}:${region.y}`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-');
        w = region.w; h = region.h;
    } else {
        const f = readFrames(file, { from: t, count: 1, crop: region, format: 'rgb24', fps });
        buf = f.frames[0]; w = f.w; h = f.h;
    }
    let rows = 0, longest = 0;
    for (let y = 0; y < h; y++) {
        let run = 0, best = 0;
        for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 3;
            run = test({ r: buf[i], g: buf[i + 1], b: buf[i + 2] }) ? run + 1 : 0;
            if (run > best) best = run;
        }
        if (best >= minRun) rows++;
        if (best > longest) longest = best;
    }
    return { rows, longest };
}

// ---- two videos, frame by frame ------------------------------------------------------------------------------------------------
// Two renders of the same lesson compared frame by frame (frame n with frame n; mean absolute luma of 255 at `size`, in `crop`
// when given): the worst frame, how many frames differ by more than `threshold`, and the first of them
// keep: also return every frame's difference ({n, diff})
export function compareVideos(fileA, fileB, { crop = null, size = { w: 192, h: 108 }, threshold = 0.5, fps = 30, keep = false } = {}) {
    const end = f => { const p = probe(f, { volume: false }); return (p.videoDuration ?? p.duration) + 1; };
    const A = readFrames(fileA, { from: 0, to: end(fileA), crop, size, format: 'gray', fps });
    const B = readFrames(fileB, { from: 0, to: end(fileB), crop, size, format: 'gray', fps });
    const byN = new Map(B.times.map((t, i) => [Math.round(t * fps), B.frames[i]]));
    const diffs = [];
    A.times.forEach((t, i) => { const n = Math.round(t * fps); const b = byN.get(n); if (b) diffs.push({ n, diff: meanAbs(A.frames[i], b) }); });
    const over = diffs.filter(d => d.diff > threshold);
    const worst = diffs.reduce((m, d) => (d.diff > m.diff ? d : m), { n: null, diff: 0 });
    return { framesA: A.frames.length, framesB: B.frames.length, compared: diffs.length, over: over.length, firstOver: over[0] ? { n: over[0].n, diff: round(over[0].diff, 2) } : null,
        worst: { n: worst.n, diff: round(worst.diff, 2) }, mean: diffs.length ? round(diffs.reduce((s, d) => s + d.diff, 0) / diffs.length, 3) : null,
        overRuns: over.reduce((runs, d) => { const last = runs[runs.length - 1]; if (last && d.n === last[1] + 1) last[1] = d.n; else runs.push([d.n, d.n]); return runs; }, []).slice(0, 12),
        ...(keep ? { diffs: diffs.map(d => ({ n: d.n, diff: round(d.diff, 3) })) } : {}) };
}

// ---- mascot clip timing --------------------------------------------------------------------------------------------------------
// Which frame of a clip each video frame in [from, to) shows, in a region where the clip is drawn unobstructed: the clip drawn
// as the stage draws it (scaled to stage {w, h}: 16:9 cover at 1920x1080 is a plain scale), the same crop, each video frame
// matched to its nearest clip frame. margin = the second-best distance minus the best: small where the clip itself barely moves
// (consecutive clip frames almost alike), so only confident matches should be judged. Steps between consecutive confident
// frames: 24 -> 30 fps nearest-frame-by-time is 1,1,1,1,0 repeating (wrapping at the clip's end when it loops).
export function clipSequence(video, clip, from, to, region, opts = {}) {
    const { stage = { w: 1920, h: 1080 }, size = { w: 88, h: Math.max(2, Math.round(88 * region.h / region.w / 2) * 2) }, minMargin = 0.4, fps = 30 } = opts;
    const raw = ffmpeg('-i', clip, '-vf', `scale=${stage.w}:${stage.h},crop=${region.w}:${region.h}:${region.x}:${region.y},scale=${size.w}:${size.h}:flags=area,format=gray`, '-f', 'rawvideo', '-');
    const n = size.w * size.h;
    const clipFrames = [];
    for (let i = 0; i + n <= raw.length; i += n) clipFrames.push(raw.subarray(i, i + n));
    const f = readFrames(video, { from, to, crop: region, size, format: 'gray', fps });
    const seq = f.frames.map((buf, k) => {
        let best = -1, bd = Infinity, second = Infinity;
        clipFrames.forEach((c, i) => { const d = meanAbs(buf, c); if (d < bd) { second = bd; bd = d; best = i; } else if (d < second) second = d; });
        return { n: Math.round(f.times[k] * fps), idx: best, d: round(bd, 2), margin: round(second - bd, 2) };
    });
    const steps = [];
    for (let k = 1; k < seq.length; k++) {
        if (seq[k].margin < minMargin || seq[k - 1].margin < minMargin || seq[k].n !== seq[k - 1].n + 1) continue;
        steps.push({ n: seq[k].n, step: (seq[k].idx - seq[k - 1].idx + clipFrames.length) % clipFrames.length });
    }
    const hist = steps.reduce((m, s) => ((m[s.step] = (m[s.step] || 0) + 1), m), {});
    // a freeze beyond the design: two zero steps in a row between confident frames (three frames of one clip frame)
    const doubleZero = steps.filter((s, k) => k > 0 && s.step === 0 && steps[k - 1].step === 0 && steps[k - 1].n === s.n - 1).map(s => s.n);
    const odd = steps.filter(s => s.step > 1).map(s => ({ n: s.n, step: s.step }));
    return { clipFrames: clipFrames.length, frames: seq.length, confident: seq.filter(s => s.margin >= minMargin).length, steps: steps.length, hist,
        zeroShare: steps.length ? round((hist[0] || 0) / steps.length, 3) : null, doubleZero, odd, worstMatch: Math.max(...seq.map(s => s.d)), seq };
}

// ---- sound -------------------------------------------------------------------------------------------------------------------
// volumedetect over the whole file or [start, start + seconds): { mean, max } in dB (-Infinity: no sound / no audio stream)
export function loudness(file, start, seconds) {
    const args = ['-hide_banner', '-nostats', '-nostdin'];
    if (start !== undefined && start !== null) args.push('-ss', String(Math.max(0, start)), '-t', String(seconds));
    args.push('-i', file, '-map', '0:a:0?', '-af', 'volumedetect', '-f', 'null', '-');
    const r = tool('ffmpeg', args, 'utf8');
    const mean = /mean_volume: (-?[\d.]+|-inf) dB/.exec(r.stderr || '');
    const max = /max_volume: (-?[\d.]+|-inf) dB/.exec(r.stderr || '');
    return { mean: mean && mean[1] !== '-inf' ? parseFloat(mean[1]) : -Infinity, max: max && max[1] !== '-inf' ? parseFloat(max[1]) : -Infinity };
}
// RMS level (dBFS) of every `win` seconds of [from, to), mono at `rate` Hz: [{ t, db }]
export function audioLevels(file, from, to, opts = {}) {
    const { win = 0.02, rate: hz = 16000 } = opts;
    const start = Math.max(0, from);
    const pcm = ffmpeg('-ss', start.toFixed(4), '-t', (to - start).toFixed(4), '-i', file, '-map', '0:a:0', '-ac', '1', '-ar', String(hz),
        '-f', 's16le', '-acodec', 'pcm_s16le', '-');
    const samples = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.length >> 1);
    const step = Math.max(1, Math.round(win * hz));
    const out = [];
    for (let i = 0; i + step <= samples.length; i += step) {
        let s = 0;
        for (let k = i; k < i + step; k++) s += samples[k] * samples[k];
        const rms = Math.sqrt(s / step) / 32768;
        out.push({ t: round(start + i / hz, 3), db: rms > 0 ? round(20 * Math.log10(rms), 1) : -120 });
    }
    return out;
}
// The first moment in [t - before, t + after) whose level rises above `db` after at least `quiet` s below it (a sound that
// starts, not one that goes on): { t, lead } (lead: onset - t, negative = early), or null
export function audioOnset(file, t, opts = {}) {
    const { before = 0.5, after = 1.5, db = -45, quiet = 0.1, win = 0.01 } = opts;
    const levels = audioLevels(file, t - before, t + after, { win });
    const need = Math.max(1, Math.round(quiet / win));
    let below = 0;
    for (const l of levels) {
        if (l.db < db) { below++; continue; }
        if (below >= need) return { t: l.t, lead: round(l.t - t, 3), db: l.db };
        below = 0;
    }
    return null;
}

// ---- text outputs ------------------------------------------------------------------------------------------------------------
const clock = s => s.split(':').reduce((a, v) => a * 60 + parseFloat(v), 0);
// WebVTT cues: [{ start, end, text }]
export function parseVtt(text) {
    return String(text || '').split(/\r?\n\r?\n/).filter(b => /-->/.test(b)).map(b => {
        const lines = b.split(/\r?\n/);
        const k = lines.findIndex(l => /-->/.test(l));
        const m = /([\d:.]+)\s*-->\s*([\d:.]+)/.exec(lines[k]);
        return { start: clock(m[1]), end: clock(m[2]), text: lines.slice(k + 1).join(' ').trim() };
    });
}
// The chapter list ("mm:ss Title" lines): [{ t, title }]
export function parseChapters(text) {
    return String(text || '').split(/\r?\n/).map(l => /^\s*([\d:.]+)\s+(.*\S)\s*$/.exec(l)).filter(Boolean).map(m => ({ t: clock(m[1]), title: m[2] }));
}
