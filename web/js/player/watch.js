// @ts-check
/**
 * Watch / preview page bootstrap (web/watch.html, CSP: script-src 'self', no inline code).
 *   /watch/{token}    -> GET /api/public/watch/{token}   (public share link, live mode + analytics)
 *   /preview/{vid}    -> GET /api/versions/{vid}/timeline (signed-in teacher, preview mode)
 * Builds the REC-branded page chrome, the Player and its Controls, and shows loading/error states.
 * Viewer preferences (volume, speed, captions) persist in localStorage when available.
 */

import { ApiError, get } from '../shared/api.js';
import { h, replace } from '../shared/dom.js';
import { Player } from './player.js';
import { Controls, SPEEDS } from './controls.js';

/** @typedef {import('../shared/types.js').Timeline} Timeline */

const PREFS_KEY = 'aadhi_player_prefs';

/**
 * @typedef {{ kind: 'watch', token: string } | { kind: 'preview', versionId: number }} Route
 */

/**
 * Parse the page route from a pathname.
 * @param {string} pathname
 * @returns {Route | null}
 */
export function parseRoute(pathname) {
  const watch = /^\/watch\/([A-Za-z0-9_-]{6,200})\/?$/.exec(pathname);
  if (watch) return { kind: 'watch', token: watch[1] };
  const preview = /^\/preview\/([1-9][0-9]{0,11})\/?$/.exec(pathname);
  if (preview) return { kind: 'preview', versionId: Number(preview[1]) };
  return null;
}

/**
 * @typedef {object} Lecture
 * @property {Timeline} timeline
 * @property {string} title
 * @property {string} subtitle
 * @property {'live' | 'preview'} mode
 * @property {{ shareToken?: string | null, versionId?: number | null } | null} analytics
 */

/**
 * Fetch the timeline (and display metadata) for a route.
 * @param {Route} route
 * @param {(path: string) => Promise<any>} [fetchJson]
 * @returns {Promise<Lecture>}
 */
export async function fetchLecture(route, fetchJson = (path) => get(path)) {
  if (route.kind === 'watch') {
    const data = await fetchJson(`/api/public/watch/${encodeURIComponent(route.token)}`);
    const project = data && data.project ? data.project : {};
    const meta = data && data.timeline && data.timeline.meta ? data.timeline.meta : {};
    return {
      timeline: data.timeline,
      title: String(project.title || meta.session_title || 'Lecture'),
      subtitle: [project.subject_name || meta.subject_name, project.session_title].filter(Boolean).join(' · '),
      mode: 'live',
      analytics: { shareToken: String(data.share_token || route.token) },
    };
  }
  const timeline = await fetchJson(`/api/versions/${route.versionId}/timeline`);
  const meta = (timeline && timeline.meta) || {};
  return {
    timeline,
    title: String(meta.session_title || meta.subject_name || `Version ${route.versionId}`),
    subtitle: [meta.subject_name, meta.session_number].filter(Boolean).join(' · '),
    mode: 'preview',
    analytics: null, // teacher previews must not pollute learner analytics
  };
}

/**
 * Human message for a load failure.
 * @param {unknown} err
 * @param {Route | null} route
 * @returns {{ title: string, detail: string, retry: boolean, signIn: boolean }}
 */
export function describeError(err, route) {
  if (!route) return { title: 'Link not recognised', detail: 'Check the lecture link and try again.', retry: false, signIn: false };
  if (err instanceof ApiError) {
    if (err.status === 404) {
      return route.kind === 'watch'
        ? { title: 'Lecture unavailable', detail: 'This lecture link is invalid, has expired or was revoked. Ask your teacher for a new link.', retry: false, signIn: false }
        : { title: 'No timeline yet', detail: 'Build this version in the Studio before previewing it.', retry: false, signIn: false };
    }
    if (err.status === 401) return { title: 'Sign in required', detail: 'Sign in to the Studio to preview this version.', retry: false, signIn: true };
    if (err.status === 403) return { title: 'Not allowed', detail: 'You do not have access to this lecture.', retry: false, signIn: err.code === 'password_change_required' };
    if (err.status === 429) return { title: 'Too many requests', detail: 'Please wait a moment and try again.', retry: true, signIn: false };
  }
  return { title: 'Could not load the lecture', detail: 'Check your connection and try again.', retry: true, signIn: false };
}

/** @returns {Record<string, any>} */
function loadPrefs() {
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    const v = raw ? JSON.parse(raw) : {};
    return v && typeof v === 'object' ? v : {};
  } catch {
    return {};
  }
}

/** @param {Record<string, any>} prefs */
function savePrefs(prefs) {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable */
  }
}

/**
 * Status panel (loading or error) inside the player box.
 * @param {HTMLElement} box
 * @param {{ loading?: boolean, title: string, detail?: string, retry?: () => void, signIn?: boolean }} s
 */
function showStatus(box, s) {
  const actions = [];
  if (s.retry) actions.push(h('button', { class: 'btn btn-gold', type: 'button', text: 'Try again', onClick: s.retry }));
  if (s.signIn) actions.push(h('a', { class: 'btn btn-outline', href: '/#/login', text: 'Open the Studio' }));
  replace(
    box,
    h(
      'div',
      { class: ['watch-status', { 'is-error': !s.loading }], role: s.loading ? 'status' : 'alert', 'aria-live': 'polite' },
      s.loading ? h('div', { class: 'spinner', 'aria-hidden': 'true' }) : h('div', { class: 'watch-status-mark', 'aria-hidden': 'true', text: '!' }),
      h('p', { class: 'watch-status-title', text: s.title }),
      s.detail ? h('p', { class: 'watch-status-detail', text: s.detail }) : null,
      actions.length ? h('div', { class: 'watch-status-actions' }, actions) : null,
    ),
  );
}

/**
 * Boot the page.
 * @param {{ root?: ParentNode, pathname?: string, fetchJson?: (path: string) => Promise<any>, playerDeps?: import('./player.js').PlayerDeps }} [opts]
 * @returns {Promise<{ player: Player, controls: Controls } | null>}
 */
export async function boot(opts = {}) {
  const root = opts.root || document;
  const box = /** @type {HTMLElement | null} */ (root.querySelector('[data-player]'));
  const titleEl = /** @type {HTMLElement | null} */ (root.querySelector('[data-title]'));
  if (!box) return null;
  const route = parseRoute(opts.pathname ?? location.pathname);
  const start = async () => {
    showStatus(box, { loading: true, title: 'Loading lecture…' });
    if (!route) {
      const e = describeError(null, null);
      showStatus(box, { title: e.title, detail: e.detail });
      return null;
    }
    /** @type {Lecture} */
    let lecture;
    try {
      lecture = await fetchLecture(route, opts.fetchJson);
    } catch (err) {
      const e = describeError(err, route);
      showStatus(box, { title: e.title, detail: e.detail, retry: e.retry ? () => void start() : undefined, signIn: e.signIn });
      return null;
    }
    if (titleEl) {
      replace(
        titleEl,
        h('h1', { class: 'watch-title-main', text: lecture.title }),
        lecture.subtitle ? h('p', { class: 'watch-title-sub', text: lecture.subtitle }) : null,
        lecture.mode === 'preview' ? h('span', { class: 'watch-badge', text: lecture.timeline.estimated ? 'Preview · estimated timing' : 'Preview' }) : null,
      );
    }
    document.title = `${lecture.title} · REC Aadhi EduEngine`;
    const prefs = loadPrefs();
    box.replaceChildren();
    const player = new Player(box, {
      mode: lecture.mode,
      analytics: lecture.analytics,
      captions: prefs.captions !== false,
      captionSize: ['s', 'm', 'l'].includes(prefs.captionSize) ? prefs.captionSize : 'm',
      volume: typeof prefs.volume === 'number' ? prefs.volume : 1,
      muted: !!prefs.muted,
      rate: SPEEDS.includes(prefs.rate) ? prefs.rate : 1,
      deps: opts.playerDeps,
    });
    try {
      await player.load(lecture.timeline);
    } catch (err) {
      player.destroy();
      showStatus(box, { title: 'This lecture could not be played', detail: 'The lecture data is incomplete. Ask your teacher to rebuild it.' });
      console.error(err);
      return null;
    }
    const controls = new Controls(box, player, { onPrefs: (p) => savePrefs(p) });
    player.on('error', ({ message }) => console.warn('player:', message));
    /** @param {PageTransitionEvent} ev */
    const onPageHide = (ev) => {
      if (ev.persisted) {
        player.pause(); // kept in the back/forward cache: resume where the viewer left off
        return;
      }
      window.removeEventListener('pagehide', onPageHide);
      controls.destroy();
      player.destroy();
    };
    window.addEventListener('pagehide', onPageHide);
    return { player, controls };
  };
  return start();
}

if (typeof document !== 'undefined' && document.querySelector('[data-player]') && !(/** @type {any} */ (globalThis).__AADHI_NO_AUTOBOOT__)) {
  boot().catch((err) => console.error('watch page failed to start', err));
}
