// @ts-check
/**
 * 3D model panel (three.js ES module, self-hosted). Primitives with labels as DOM overlays,
 * hemisphere + key + rim lights, orbit camera framed on the bounding sphere.
 *  - live:    pointer/keyboard drag rotation, optional auto-rotate (paused while interacting)
 *  - preview: drag rotation, no auto-rotate
 *  - render:  one frame at a fixed "nice" angle, preserveDrawingBuffer so the canvas is captured
 * destroy() disposes geometries/materials/renderer, forces the WebGL context loss and removes
 * every listener. Context loss is handled (rendering pauses, resumes on restore).
 */

import { Disposer, h } from '../../shared/dom.js';
import { loadThree } from '../../shared/libs.js';
import { clamp, finiteOr, prefersReducedMotion, showNotice, stripRich } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */

/** Camera angles (radians) used for MP4 renders and as the initial live view. */
export const RENDER_ANGLE = Object.freeze({ yaw: -0.62, pitch: 0.38 });
export const AUTO_ROTATE_SPEED = 0.35; // rad/s
const INTERACTION_PAUSE_MS = 2500;
const MAX_PITCH = 1.25;

/**
 * @param {unknown[] | undefined} size
 * @param {number} i
 * @param {number} fallback
 */
function dim(size, i, fallback) {
  const v = Array.isArray(size) ? Number(size[i]) : NaN;
  return clamp(Number.isFinite(v) && v > 0 ? v : fallback, 0.02, 100);
}

/**
 * Build meshes for Model3DSpec primitives.
 * @param {any} THREE   three.js namespace
 * @param {any[]} primitives
 * @returns {{ root: any, labels: { text: string, anchor: any }[] }}
 */
export function buildPrimitives(THREE, primitives) {
  const root = new THREE.Group();
  /** @type {{ text: string, anchor: any }[]} */
  const labels = [];
  const list = Array.isArray(primitives) ? primitives.slice(0, 40) : [];
  for (const p of list) {
    if (!p || typeof p !== 'object') continue;
    const pos = Array.isArray(p.position) ? p.position : [0, 0, 0];
    const position = new THREE.Vector3(
      clamp(finiteOr(Number(pos[0]), 0), -1000, 1000),
      clamp(finiteOr(Number(pos[1]), 0), -1000, 1000),
      clamp(finiteOr(Number(pos[2]), 0), -1000, 1000),
    );
    const color = /^#[0-9a-f]{6}$/i.test(String(p.color)) ? String(p.color) : '#b026ff';
    const material = new THREE.MeshStandardMaterial({ color, metalness: 0.15, roughness: 0.45 });
    material.emissive = new THREE.Color(color).multiplyScalar(0.08);
    let object;
    let top = 0.5; // label anchor height above the position
    switch (p.shape) {
      case 'sphere': {
        const r = dim(p.size, 0, 1);
        object = new THREE.Mesh(new THREE.SphereGeometry(r, 48, 24), material);
        top = r;
        break;
      }
      case 'box': {
        const w = dim(p.size, 0, 1);
        const hh = dim(p.size, 1, w);
        const d = dim(p.size, 2, w);
        object = new THREE.Mesh(new THREE.BoxGeometry(w, hh, d), material);
        top = hh / 2;
        break;
      }
      case 'cylinder': {
        const r = dim(p.size, 0, 0.5);
        const hh = dim(p.size, 1, r * 2);
        object = new THREE.Mesh(new THREE.CylinderGeometry(r, r, hh, 40), material);
        top = hh / 2;
        break;
      }
      case 'cone': {
        const r = dim(p.size, 0, 0.5);
        const hh = dim(p.size, 1, r * 2);
        object = new THREE.Mesh(new THREE.ConeGeometry(r, hh, 40), material);
        top = hh / 2;
        break;
      }
      case 'torus': {
        const big = dim(p.size, 0, 1);
        const tube = Math.min(dim(p.size, 1, big * 0.3), big);
        object = new THREE.Mesh(new THREE.TorusGeometry(big, tube, 24, 72), material);
        top = big + tube;
        break;
      }
      case 'arrow': {
        const raw = Array.isArray(p.size) ? p.size : [];
        const dir = new THREE.Vector3(finiteOr(Number(raw[0]), 0), finiteOr(Number(raw[1]), 0), finiteOr(Number(raw[2]), 0));
        let len = dir.length();
        if (!(len > 1e-6)) {
          dir.set(0, 1, 0);
          len = 1;
        }
        len = clamp(len, 0.05, 100);
        dir.normalize();
        const headLen = Math.min(len * 0.6, clamp(len * 0.25, 0.08, 1.2));
        const shaftLen = Math.max(0.001, len - headLen);
        const radius = clamp(len * 0.025, 0.02, 0.25);
        const shaft = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, shaftLen, 20), material);
        shaft.position.y = shaftLen / 2;
        const head = new THREE.Mesh(new THREE.ConeGeometry(radius * 2.6, headLen, 24), material);
        head.position.y = shaftLen + headLen / 2;
        object = new THREE.Group();
        object.add(shaft, head);
        object.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
        object.position.copy(position);
        root.add(object);
        const label = stripRich(p.label);
        if (label) labels.push({ text: label, anchor: position.clone().add(dir.clone().multiplyScalar(len)).add(new THREE.Vector3(0, 0.25, 0)) });
        continue;
      }
      default:
        material.dispose();
        continue;
    }
    object.position.copy(position);
    root.add(object);
    const label = stripRich(p.label);
    if (label) labels.push({ text: label, anchor: position.clone().add(new THREE.Vector3(0, top + 0.3, 0)) });
  }
  return { root, labels };
}

/**
 * Camera position on an orbit around `center`.
 * @param {{ x: number, y: number, z: number }} center
 * @param {number} dist
 * @param {number} yaw
 * @param {number} pitch
 */
export function orbitPosition(center, dist, yaw, pitch) {
  return {
    x: center.x + dist * Math.cos(pitch) * Math.sin(yaw),
    y: center.y + dist * Math.sin(pitch),
    z: center.z + dist * Math.cos(pitch) * Math.cos(yaw),
  };
}

/**
 * Distance at which a sphere of `radius` fits both the vertical and horizontal field of view.
 * @param {number} radius
 * @param {number} fovDeg  vertical FOV
 * @param {number} aspect
 */
export function fitDistance(radius, fovDeg, aspect) {
  const v = (fovDeg * Math.PI) / 180;
  const hz = 2 * Math.atan(Math.tan(v / 2) * Math.max(0.1, aspect));
  return (radius / Math.sin(Math.min(v, hz) / 2)) * 1.08;
}

/**
 * Factory with injectable dependencies (tests pass a fake three namespace).
 * @param {{ loadThree: () => Promise<any> }} deps
 * @returns {PanelFactory}
 */
export function model3dFactory(deps) {
  return function createModel3dPanel(body, rsp, ctx) {
    const disposer = new Disposer();
    const spec = rsp.panel && rsp.panel.model_3d;
    const mode = ctx.mode;
    const render = mode === 'render';
    const autoRotate = mode === 'live' && !!(spec && spec.auto_rotate !== false) && !prefersReducedMotion();
    const interactive = mode !== 'render';
    const canvas = h('canvas', {
      class: 'ap-3d-canvas',
      role: 'img',
      'aria-label': stripRich(rsp.panel && rsp.panel.title) || '3D model',
      ...(interactive ? { tabindex: '0' } : {}),
    });
    const labelLayer = h('div', { class: 'ap-3d-labels', 'aria-hidden': 'true' });
    const lostNotice = h('div', { class: 'ap-notice ap-3d-lost', hidden: true, text: '3D view paused (graphics context lost).' });
    const stage = h('div', { class: 'ap-3d-stage' }, canvas, labelLayer, lostNotice);
    body.appendChild(stage);
    const hint = interactive ? h('p', { class: 'ap-3d-hint', text: 'Drag to rotate' }) : null;
    if (hint) body.appendChild(hint);

    /** @type {any} */ let THREE = null;
    /** @type {any} */ let renderer = null;
    /** @type {any} */ let scene = null;
    /** @type {any} */ let camera = null;
    /** @type {any} */ let center = null;
    let radius = 1;
    /** @type {{ el: HTMLElement, anchor: any }[]} */
    let labels = [];
    /** @type {number} */
    let yaw = RENDER_ANGLE.yaw;
    /** @type {number} */
    let pitch = RENDER_ANGLE.pitch;
    let lost = false;
    let destroyed = false;
    let visible = false;
    let failed = false;
    let rafId = 0;
    let loopOn = false;
    let lastFrame = 0;
    let lastInteraction = -Infinity;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let size = { w: 0, h: 0 };

    /**
     * Show a failure notice once (the panel stays a static card) and stop interaction.
     * @param {string} message  user-facing text
     * @param {string} [logMessage]
     * @param {unknown} [err]
     */
    const fail = (message, logMessage, err) => {
      if (failed) return;
      failed = true;
      if (logMessage) console.error(logMessage, err);
      if (hint) hint.hidden = true;
      canvas.hidden = true;
      showNotice(body, message, 'warn');
    };

    const resize = () => {
      if (!renderer || !camera) return;
      const w = Math.max(1, stage.clientWidth || 400);
      const hgt = Math.max(1, stage.clientHeight || 300);
      if (w === size.w && hgt === size.h) return;
      size = { w, h: hgt };
      renderer.setSize(w, hgt, false);
      camera.aspect = w / hgt;
      camera.updateProjectionMatrix();
    };
    const placeCamera = () => {
      const dist = fitDistance(radius, camera.fov, camera.aspect);
      const p = orbitPosition(center, dist, yaw, pitch);
      camera.position.set(p.x, p.y, p.z);
      camera.lookAt(center);
    };
    const updateLabels = () => {
      for (const l of labels) {
        const v = l.anchor.clone().project(camera);
        const off = v.z > 1 || v.z < -1;
        l.el.hidden = off;
        if (!off) l.el.style.transform = `translate(${Math.round(((v.x + 1) / 2) * size.w)}px, ${Math.round(((1 - v.y) / 2) * size.h)}px) translate(-50%, -100%)`;
      }
    };
    const draw = () => {
      if (!renderer || !scene || !camera || lost || destroyed || failed) return;
      resize();
      placeCamera();
      renderer.render(scene, camera);
      updateLabels();
    };
    const requestDraw = () => {
      if (rafId || destroyed) return;
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        draw();
      });
    };
    const loop = (/** @type {number} */ now) => {
      rafId = 0;
      if (!loopOn || destroyed) return;
      const dt = lastFrame ? Math.min(0.1, (now - lastFrame) / 1000) : 0;
      lastFrame = now;
      if (!dragging && now - lastInteraction > INTERACTION_PAUSE_MS) yaw += AUTO_ROTATE_SPEED * dt;
      draw();
      rafId = requestAnimationFrame(loop);
    };
    const startLoop = () => {
      if (loopOn || !autoRotate || lost || !renderer) return;
      loopOn = true;
      lastFrame = 0;
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(loop);
    };
    const stopLoop = () => {
      loopOn = false;
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
    };
    disposer.add(stopLoop);

    /** Build lights, the model, the grid, the camera and the labels (renderer must exist). */
    const buildScene = () => {
      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(render ? 1 : Math.min(2, typeof devicePixelRatio === 'number' ? devicePixelRatio : 1));
      scene = new THREE.Scene();
      scene.add(new THREE.HemisphereLight(0xffffff, 0x3a1a5a, 1.4));
      const key = new THREE.DirectionalLight(0xffffff, 2.2);
      key.position.set(4, 8, 6);
      const rim = new THREE.DirectionalLight(0xb026ff, 0.9);
      rim.position.set(-6, -2, -5);
      scene.add(key, rim);
      const built = buildPrimitives(THREE, spec.primitives);
      scene.add(built.root);
      const box = new THREE.Box3().setFromObject(built.root);
      const sphere = box.getBoundingSphere(new THREE.Sphere());
      center = sphere.center;
      radius = Math.max(0.5, finiteOr(sphere.radius, 1));
      const grid = new THREE.GridHelper(Math.ceil(radius * 4), 12, 0x6c159e, 0x3a1f55);
      grid.material.transparent = true;
      grid.material.opacity = 0.45;
      grid.position.set(center.x, box.min.y - 0.002, center.z);
      scene.add(grid);
      camera = new THREE.PerspectiveCamera(40, 4 / 3, radius * 0.02, radius * 60);
      labels = built.labels.map((l) => {
        const el = h('span', { class: 'ap-3d-label', text: l.text });
        labelLayer.appendChild(el);
        return { el, anchor: l.anchor };
      });
    };

    /** @type {Promise<void> | null} */
    let initPromise = null;
    const init = () => {
      if (initPromise) return initPromise;
      initPromise = (async () => {
        if (!spec || !Array.isArray(spec.primitives) || !spec.primitives.length) {
          fail('3D model data is missing.');
          return;
        }
        try {
          THREE = await deps.loadThree();
        } catch (e) {
          // Infrastructure problem: reported here (live, preview and render alike) and rethrown
          // so ready() rejects in render mode. The rejected promise is cached: no retry storm.
          fail('3D models are unavailable right now.', 'model_3d panel: three.js failed to load', e);
          throw e;
        }
        if (destroyed) return;
        try {
          renderer = new THREE.WebGLRenderer({
            canvas,
            antialias: true,
            alpha: true,
            preserveDrawingBuffer: render,
            powerPreference: 'low-power',
          });
        } catch (e) {
          fail('3D view is not supported on this device.', 'model_3d panel: WebGL unavailable', e);
          return;
        }
        try {
          buildScene();
        } catch (e) {
          fail('This 3D model could not be displayed.', 'model_3d panel: building the scene failed', e);
          return;
        }
        disposer.listen(canvas, 'webglcontextlost', (ev) => {
          ev.preventDefault();
          lost = true;
          stopLoop();
          lostNotice.hidden = false;
        });
        disposer.listen(canvas, 'webglcontextrestored', () => {
          lost = false;
          lostNotice.hidden = true;
          if (visible) {
            if (autoRotate) startLoop();
            else requestDraw();
          }
        });
        if (!render && typeof ResizeObserver === 'function') {
          const ro = new ResizeObserver(() => visible && requestDraw());
          ro.observe(stage);
          disposer.add(() => ro.disconnect());
        }
      })();
      return initPromise;
    };

    if (interactive) {
      const interact = () => {
        lastInteraction = performance.now();
      };
      disposer.listen(canvas, 'pointerdown', (ev) => {
        const pe = /** @type {PointerEvent} */ (ev);
        if (pe.button !== 0) return;
        dragging = true;
        lastX = pe.clientX;
        lastY = pe.clientY;
        interact();
        try {
          canvas.setPointerCapture(pe.pointerId);
        } catch {
          /* synthetic events */
        }
      });
      disposer.listen(canvas, 'pointermove', (ev) => {
        if (!dragging) return;
        const pe = /** @type {PointerEvent} */ (ev);
        yaw -= (pe.clientX - lastX) * 0.008;
        pitch = clamp(pitch + (pe.clientY - lastY) * 0.006, -MAX_PITCH, MAX_PITCH);
        lastX = pe.clientX;
        lastY = pe.clientY;
        interact();
        requestDraw();
      });
      const end = (/** @type {Event} */ ev) => {
        if (!dragging) return;
        dragging = false;
        interact();
        try {
          canvas.releasePointerCapture(/** @type {PointerEvent} */ (ev).pointerId);
        } catch {
          /* not captured */
        }
      };
      disposer.listen(canvas, 'pointerup', end);
      disposer.listen(canvas, 'pointercancel', end);
      disposer.listen(canvas, 'keydown', (ev) => {
        const k = /** @type {KeyboardEvent} */ (ev).key;
        const step = 0.12;
        if (k === 'ArrowLeft') yaw += step;
        else if (k === 'ArrowRight') yaw -= step;
        else if (k === 'ArrowUp') pitch = clamp(pitch + step, -MAX_PITCH, MAX_PITCH);
        else if (k === 'ArrowDown') pitch = clamp(pitch - step, -MAX_PITCH, MAX_PITCH);
        else return;
        ev.preventDefault();
        interact();
        requestDraw();
      });
    }

    return {
      update(_t, _state, isVisible) {
        if (isVisible === visible) return;
        visible = isVisible;
        if (render) return;
        if (!isVisible) {
          stopLoop();
          return;
        }
        init().then(
          () => {
            if (!visible || destroyed || failed) return;
            if (autoRotate) startLoop();
            else requestDraw();
          },
          () => undefined,
        );
      },
      async ready() {
        try {
          await init();
        } catch (e) {
          // three.js failed to load (init() already showed the notice): fatal for MP4 renders.
          if (render) throw e;
          return;
        }
        if (failed) return;
        if (render) {
          yaw = RENDER_ANGLE.yaw;
          pitch = RENDER_ANGLE.pitch;
          draw(); // one synchronous frame at the fixed angle (drawing buffer preserved)
        } else if (visible && !autoRotate) requestDraw();
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        disposer.dispose();
        if (scene) {
          const geometries = new Set();
          const materials = new Set();
          scene.traverse((/** @type {any} */ obj) => {
            if (obj.geometry) geometries.add(obj.geometry);
            const m = obj.material;
            if (Array.isArray(m)) m.forEach((x) => materials.add(x));
            else if (m) materials.add(m);
          });
          geometries.forEach((g) => /** @type {any} */ (g).dispose());
          materials.forEach((m) => /** @type {any} */ (m).dispose());
          scene.clear?.();
        }
        if (renderer) {
          renderer.dispose();
          renderer.forceContextLoss?.();
          renderer = null;
        }
        stage.remove();
      },
    };
  };
}

/** Default factory used by createPanel. */
export const createModel3dPanel = model3dFactory({ loadThree });
