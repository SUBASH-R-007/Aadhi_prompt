// Stub of web/js/dynamic/stage.js for the QA tests: createDynamicStage(root, timeline, {mode}) whose every pixel is a
// pure function of (timeline, t). It draws a drifting gradient background (look.background.preset), a moving dot, a
// headline (critical), a body line (critical) and the caption of the moment, crossfades scenes whose
// look.transition_in.kind is "crossfade", and reports textBoxes() like the real stage.
//
// Faults (window.STUB.faults = {"<scene index>": ["overflow" | "unsafe" | "contrast" | "caption_overflow"]}) make a
// scene fail one preflight check ("caption_overflow": its captions run out of the caption box);
// window.STUB.honour = ["shrink", "safe", "scrim"] lists the remedies the stub applies when the timeline asks for them
// (the headline and the body only), read exactly as the real stage reads them (web/js/dynamic/core.js fixesOf, used by
// board_generic's slotTuning): the fixes channel first, B1's legacy fields second. A slot's size is
// look.fixes.slots[slot].size when it is a positive number, else look.text_fit[slot].size; its scrim and the safe
// layout hold when either channel asks (fixes.slots[slot].scrim / text_fit[slot].scrim === true; fixes.safe === true /
// variant === "safe"). window.STUB.channel = "fixes" or "legacy" reads only that channel (preflight writes only fixes
// from B2a step B: a legacy-only stage never sees a remedy).
// window.STUB.random adds Math.random() jitter (a non-deterministic stage); window.STUB.strayTimer calls setTimeout
// inside seek().
//
// As the real stage (web/js/dynamic/stage.js, core.js stageInfo): every scene has at least one frame (a scene shorter
// than half a frame still gets one, which can make the video one frame longer than its rounded duration), and a
// render-mode page draws captions as presentation.captions_burn says until setLayers() is called.

const cfg = () => /** @type {any} */ (window).STUB || {};

const PALETTE = ['#1d2b6b', '#0f5c5c', '#5c1d4f', '#2b4f1d'];
const INK = '#f4f4fa';

/** @param {string} tag @param {Record<string, string>} style */
function el(tag, style) {
  const node = document.createElement(tag);
  Object.assign(node.style, style);
  return node;
}

export function createDynamicStage(root, timeline, { mode = 'render' } = {}) {
  const fps = timeline.fps || 30;
  const W = timeline.width;
  const H = timeline.height;
  const portrait = H > W;
  const edges = [...timeline.scenes.map((s) => s.start), timeline.total_duration].map((e) => Math.floor(e * fps + 0.5));
  const scenes = [];
  timeline.scenes.forEach((s, i) => {
    const f0 = i ? scenes[i - 1].f1 : edges[0];
    scenes.push({ index: s.index, f0, f1: Math.max(f0 + 1, edges[i + 1]), s });
  });
  const frames = scenes.length ? scenes[scenes.length - 1].f1 : 0;
  const presentation = timeline.presentation || {};
  let layers = { captions: mode === 'render' ? !!presentation.captions_burn : true, stage: true };
  let shown = null;

  root.textContent = '';
  Object.assign(root.style, { width: `${W}px`, height: `${H}px`, overflow: 'hidden', position: 'fixed' });
  document.documentElement.style.width = `${W}px`;
  document.documentElement.style.height = `${H}px`;
  const bg = el('div', { position: 'absolute', left: '0', top: '0', width: `${W}px`, height: `${H}px` });
  const dot = el('div', { position: 'absolute', width: '60px', height: '60px', borderRadius: '30px', background: '#ffd34d' });
  const title = el('div', {
    position: 'absolute', whiteSpace: 'nowrap', overflow: 'hidden', fontFamily: 'Arial, sans-serif', fontWeight: '700',
    color: INK, lineHeight: '1.15',
  });
  const body = el('div', { // R-29 like the real stage: 9:16 text at <= 64 px is weight >= 600
    position: 'absolute', whiteSpace: 'nowrap', overflow: 'hidden', fontFamily: 'Arial, sans-serif',
    fontWeight: portrait ? '600' : '400', fontSize: '52px', lineHeight: '1.35',
  });
  const cap = el('div', {
    position: 'absolute', whiteSpace: 'nowrap', overflow: 'hidden', fontFamily: 'Arial, sans-serif', fontWeight: '600',
    fontSize: portrait ? '56px' : '48px', color: '#ffffff', background: '#111118', textAlign: 'center',
  });
  for (const node of [bg, dot, title, body, cap]) root.appendChild(node);

  const faults = (index) => (cfg().faults || {})[String(index)] || [];
  const honours = (what) => (cfg().honour || []).includes(what);
  const reads = (channel) => !cfg().channel || cfg().channel === channel;
  const fixes = (s) => (reads('fixes') && s.look && s.look.fixes) || {};
  const legacy = (s, slot) => (reads('legacy') && s.look && s.look.text_fit && s.look.text_fit[slot]) || {};
  const sizeOf = (entry) => {
    const v = entry && typeof entry === 'object' ? Number(entry.size) : NaN;
    return Number.isFinite(v) && v > 0 ? v : undefined;
  };
  // one slot's remedies (core.js fixesOf): the fixes channel's size when it has one, else the legacy one; a scrim
  // from either channel
  const fit = (s, slot) => {
    const own = (fixes(s).slots || {})[slot] || {};
    const old = legacy(s, slot);
    const size = sizeOf(own) !== undefined ? sizeOf(own) : sizeOf(old);
    return { size, scrim: own.scrim === true || old.scrim === true };
  };
  const safeLayout = (s) => fixes(s).safe === true || (reads('legacy') && !!s.look && s.look.variant === 'safe');

  function sceneAt(t) {
    const f = Math.floor(t * fps + 1e-6);
    return scenes.find((sc) => f >= sc.f0 && f < sc.f1) || scenes[scenes.length - 1];
  }

  function captionAt(t, sc) {
    if (portrait) {
      const rel = t - sc.s.start;
      const chunk = (sc.s.kinetic_captions || []).find((c) => !c.blank && rel >= c.start && rel < c.end);
      return chunk ? chunk.lines.join(String.fromCharCode(10)) : '';
    }
    const cue = (timeline.captions || []).find((c) => t >= c.start && t < c.end);
    return cue ? cue.text : '';
  }

  function update(t) {
    const sc = sceneAt(t);
    const s = sc.s;
    const look = s.look || {};
    const preset = (look.background && look.background.preset) || 'mesh_drift';
    const colour = PALETTE[sc.index % PALETTE.length];
    const jitter = cfg().random ? Math.random() * 40 : 0;
    if (preset === 'colour_block') {
      bg.style.background = colour;
    } else {
      const angle = ((t * 9) % 360).toFixed(3);
      bg.style.background = `linear-gradient(${angle}deg, ${colour}, #0b0b14)`;
    }
    dot.style.left = `${(100 + ((t * 137) % (W - 260)) + jitter).toFixed(2)}px`;
    dot.style.top = `${(H * 0.5 + 120 * Math.sin(t * 1.7)).toFixed(2)}px`;

    const f = faults(sc.index);
    // headline (critical, slot "headline")
    const shrink = honours('shrink') ? fit(s, 'headline').size : undefined;
    const size = shrink || (portrait ? 88 : 96);
    title.style.fontSize = `${size}px`;
    title.textContent = f.includes('overflow') ? 'Far too long a headline here' : s.title || `Scene ${sc.index + 1}`;
    const safe = honours('safe') && safeLayout(s);
    const left = f.includes('unsafe') && !safe ? 20 : portrait ? 200 : 140;
    Object.assign(title.style, {
      left: `${left}px`, top: portrait ? '360px' : '120px', width: portrait ? '680px' : '800px', height: `${Math.ceil(size * 1.2)}px`,
    });
    // body (critical, slot "body")
    const scrim = honours('scrim') && fit(s, 'body').scrim === true;
    const lowContrast = f.includes('contrast') && !scrim;
    body.textContent = `Point ${sc.index + 1}: V = I R`;
    Object.assign(body.style, {
      left: portrait ? '200px' : '140px', top: portrait ? '520px' : '320px', width: portrait ? '680px' : '1200px',
      height: '72px', color: lowContrast ? '#4a4a5a' : INK, background: scrim ? '#05050a' : 'transparent',
    });
    body.dataset.bg = scrim ? '#05050a' : '#202030';
    // transition: crossfade the scene's text in
    const tr = look.transition_in || {};
    const p = tr.kind === 'crossfade' && tr.seconds > 0 ? Math.min(1, Math.max(0, (t - s.start) / tr.seconds)) : 1;
    title.style.opacity = body.style.opacity = p.toFixed(4);
    bg.style.visibility = dot.style.visibility = title.style.visibility = body.style.visibility =
      layers.stage ? 'visible' : 'hidden';
    // the caption-only layer (setLayers({stage: false})) is captured as a transparent PNG
    document.documentElement.style.background = document.body.style.background = layers.stage ? '' : 'transparent';
    // caption band
    const said = layers.captions ? captionAt(t, sc) : '';
    const text = said && f.includes('caption_overflow') ? `${said} ${'and much more text than the box holds '.repeat(4)}` : said;
    cap.textContent = text;
    Object.assign(cap.style, portrait
      ? { left: '120px', top: '1060px', width: '660px', height: '140px', whiteSpace: 'pre', lineHeight: '1.2',
          visibility: text ? 'visible' : 'hidden' }
      : { left: '160px', top: '940px', width: '1600px', height: '64px', visibility: text ? 'visible' : 'hidden' });
    shown = { t, sc };
    return `${sc.index}:${Math.floor(t * fps + 1e-6)}`;
  }

  /** @param {HTMLElement} node */
  function box(node, role, critical, slot, bgColour) {
    const r = node.getBoundingClientRect();
    const cs = getComputedStyle(node);
    return {
      x: r.x, y: r.y, w: r.width, h: r.height, role, critical, slot,
      ink: cs.color, bg: bgColour,
      overflow: Math.max(0, node.scrollWidth - node.clientWidth, node.scrollHeight - node.clientHeight),
      scene: shown ? shown.sc.index : 0, size: parseFloat(cs.fontSize), weight: Number(cs.fontWeight) || 400, min: 36,
      text: node.textContent || '',
    };
  }

  return {
    update,
    async seek(frame) {
      if (cfg().strayTimer) setTimeout(() => {}, 0);
      const key = update(frame / fps);
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
      return { key };
    },
    info: () => ({ width: W, height: H, fps, frames, scenes: scenes.map(({ index, f0, f1 }) => ({ index, f0, f1 })) }),
    textBoxes() {
      if (!shown) return [];
      const colour = PALETTE[shown.sc.index % PALETTE.length];
      const out = [box(title, 'headline', true, 'headline', colour), box(body, 'body', true, 'body', body.dataset.bg)];
      if (cap.textContent) out.push(box(cap, 'caption', false, 'caption', '#111118'));
      return out;
    },
    async setLayers(spec) {
      layers = { captions: spec.captions !== false, stage: spec.stage !== false };
      if (shown) update(shown.t);
    },
    destroy() {
      root.textContent = '';
    },
    mode,
  };
}
