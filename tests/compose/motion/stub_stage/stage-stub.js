// Stub of the Dynamic render stage (stage contract v3, DYNAMIC_VIDEO_DESIGN §7.3) for the motion engine's tests.
//
// Every frame is a pure function of the frame number: the top quarter shows the frame number as 16 black/white
// blocks (most significant bit first), the second quarter the scene's colour, the bottom-left quarter the media
// frame the engine extracted (served at /__frames/... by the worker, never by the server) and, with the caption
// layer on, the bottom-right quarter is white. window.STUB (from /stub-config.js) picks a failure mode.

const cfg = window.STUB || {};
const BITS = 16;

let tl = null;
let fps = 30;
let W = 0;
let H = 0;
let spans = [];
let frames = 0;
let layers = { captions: false, stage: true };
const bitEls = [];
let band = null;
let media = null;
let caption = null;

function token() {
  const m = /token=([^&]+)/.exec(window.location.hash || '');
  window.history.replaceState(null, '', window.location.pathname);
  return m ? decodeURIComponent(m[1]) : '';
}

function fail(code, message) {
  const err = new Error(message);
  err.code = code;
  return err;
}

function build(t) {
  tl = t;
  fps = t.fps;
  W = t.width;
  H = t.height;
  spans = [];
  const edges = t.scenes.map((s) => Math.round(s.start * fps));
  edges.push(Math.round(t.total_duration * fps));
  for (let i = 0; i < t.scenes.length; i++) {
    const f0 = i ? spans[i - 1].f1 : edges[0];
    spans.push({ index: i, f0, f1: Math.max(f0 + 1, edges[i + 1]) });
  }
  frames = spans.length ? spans[spans.length - 1].f1 : 0;
  const stage = document.getElementById('stage');
  stage.style.width = `${W}px`;
  stage.style.height = `${H}px`;
  for (let b = 0; b < BITS; b++) {
    const el = document.createElement('div');
    el.className = 'bit';
    el.style.left = `${(b * W) / BITS}px`;
    el.style.width = `${W / BITS}px`;
    el.style.height = `${H / 4}px`;
    stage.appendChild(el);
    bitEls.push(el);
  }
  band = document.createElement('div');
  band.id = 'band';
  band.style.top = `${H / 4}px`;
  band.style.width = `${W}px`;
  band.style.height = `${H / 4}px`;
  stage.appendChild(band);
  media = document.createElement('img');
  media.id = 'media';
  media.alt = '';
  media.style.top = `${H / 2}px`;
  media.style.width = `${W / 2}px`;
  media.style.height = `${H / 2}px`;
  stage.appendChild(media);
  caption = document.createElement('div');
  caption.id = 'caption';
  caption.style.left = `${W / 2}px`;
  caption.style.top = `${(3 * H) / 4}px`;
  caption.style.width = `${W / 2}px`;
  caption.style.height = `${H / 4}px`;
  stage.appendChild(caption);
}

async function boot() {
  const tok = token();
  if (cfg.mode === 'boot_error') throw fail('timeline_invalid', 'stub boot failure');
  const res = await fetch('/api/render/timeline', { headers: { Authorization: `Bearer ${tok}` } });
  if (!res.ok) throw fail('timeline_http', `HTTP ${res.status}`);
  build(await res.json());
  return { webgl: false };
}

function sceneAt(frame) {
  for (const s of spans) if (frame >= s.f0 && frame < s.f1) return s.index;
  return spans.length - 1;
}

function videoOf(scene) {
  const m = scene && scene.media;
  return m && m.kind === 'video' && m.asset_key && m.render_in_mp4 !== false ? m : null;
}

function srcIndex(scene, m, frame) {
  const srcFps = m.src_fps || 30;
  const total = Math.max(1, Math.round((m.duration || 1) * srcFps));
  const n = Math.floor((frame / fps - scene.start + (m.trim_in || 0)) * srcFps + 1e-6);
  return Math.max(0, n) % total;
}

// The frames' name, as the real stage makes it (web/js/dynamic/media.js mediaKey): path-safe, carrying the size.
function framesKey(m) {
  return `v-${m.asset_key.slice(-12)}-${W / 2}x${H / 2}`;
}

function info() {
  return { width: W, height: H, fps, frames: cfg.mode === 'bad_info' ? frames + 1 : frames, scenes: spans };
}

function mediaPlan(f0, f1) {
  if (cfg.mode === 'bad_plan') return [{ key: '../../etc/passwd', kind: 'video', srcFps: 30, frames: [0], size: [8, 8] }];
  if (cfg.mode === 'foreign_media') {
    return [{ key: 'v-foreign-8x8', asset_key: `video-${'f'.repeat(40)}`, kind: 'video', srcFps: 30, frames: [0],
              size: [8, 8] }];
  }
  const out = [];
  for (const span of spans) {
    const scene = tl.scenes[span.index];
    const m = videoOf(scene);
    if (!m || cfg.media === false) continue;
    const a = Math.max(f0, span.f0);
    const b = Math.min(f1, span.f1);
    if (a >= b) continue;
    const wanted = new Set();
    for (let f = a; f < b; f++) wanted.add(srcIndex(scene, m, f));
    out.push({ key: framesKey(m), asset_key: m.asset_key, kind: 'video', srcFps: m.src_fps || 30,
               frames: [...wanted].sort((x, y) => x - y), size: [W / 2, H / 2] });
  }
  return out;
}

function frameTick() {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

async function seek(frame) {
  const f = frame | 0;
  for (let b = 0; b < BITS; b++) {
    bitEls[b].style.background = (f >> (BITS - 1 - b)) & 1 ? '#ffffff' : '#000000';
  }
  const si = sceneAt(f);
  const scene = tl.scenes[si];
  band.style.background = `hsl(${(si * 67) % 360}, 70%, 45%)`;
  let mediaKey = '';
  const m = layers.stage && cfg.media !== false ? videoOf(scene) : null;
  if (m) {
    const n = srcIndex(scene, m, f);
    // the real stage's URL: /__frames/<key>/<n, 6 digits>.jpg (web/js/dynamic/media.js frameUrl)
    const src = `/__frames/${framesKey(m)}/${String(n).padStart(6, '0')}.jpg`;
    if (media.getAttribute('src') !== src) media.setAttribute('src', src);
    await media.decode();
    media.style.visibility = 'visible';
    mediaKey = `${m.asset_key}:${n}`;
  } else {
    media.style.visibility = 'hidden';
  }
  caption.style.display = layers.captions ? 'block' : 'none';
  await frameTick();
  return { key: `f${f}|s${si}|${mediaKey}|c${layers.captions ? 1 : 0}` };
}

function textBoxes() {
  if (Array.isArray(cfg.boxes)) return cfg.boxes;
  return [{ x: W * 0.1, y: H * 0.3, w: W * 0.5, h: H * 0.1, role: 'body', critical: true, ink: '#ffffff',
            bg: '#202020', overflow: false }];
}

function setLayers(spec) {
  layers = { captions: !!(spec && spec.captions), stage: !(spec && spec.stage === false) };
  window.__stubLayers = layers;
}

const ready = boot();
ready.catch(() => {});

window.aadhiStage = {
  version: cfg.mode === 'bad_version' ? 2 : 3,
  ready,
  info,
  mediaPlan,
  seek,
  textBoxes,
  setLayers,
};
