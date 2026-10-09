// @ts-check
/**
 * REC - Aadhi EduEngine Studio: bootstrap, auth, top bar, hash routing, global errors.
 * Views are loaded lazily (native dynamic import) and receive an AppContext plus an
 * AbortSignal that fires when the user leaves the route (a view still loading must then stop:
 * no leave guard, no dialogs). Local screenplay drafts are removed on sign-out and when a
 * different user signs in on this browser (lib/draftStore.js).
 * The "API keys" page is linked from the top bar and the account menu only when /api/meta
 * says personal keys are allowed (`api_keys.personal_enabled`).
 */

import { get, post, onAuthError, ApiError } from '../shared/api.js';
import { h, clear } from '../shared/dom.js';
import { HashRouter, guardRoute, DEFAULT_ROUTE } from './router.js';
import { initToasts, toast } from './components/toast.js';
import { closeAllModals } from './components/modal.js';
import { brandMark, icon } from './components/icons.js';
import { menuButton } from './components/menu.js';
import { spinner, errorState } from './components/form.js';
import { errorMessage, isAuthError } from './errors.js';
import { APP_TITLE, APP_SUBTITLE } from './brand.js';
import { clearAllDrafts, noteSignedInUser } from './lib/draftStore.js';

/** @typedef {import('./types.js').User} User */
/** @typedef {import('./types.js').AppContext} AppContext */
/** @typedef {import('./types.js').ViewHandle} ViewHandle */
/** @typedef {import('./router.js').ResolvedRoute} ResolvedRoute */

export { APP_TITLE, APP_SUBTITLE };

/** Lazy view loaders keyed by route name. @type {Record<string, () => Promise<{ mount: import('./types.js').ViewMount }>>} */
const VIEWS = {
  login: () => import('./views/login.js'),
  changePassword: () => import('./views/changePassword.js'),
  projects: () => import('./views/projects.js'),
  newProject: () => import('./views/newProject.js'),
  project: () => import('./views/projectDetail.js'),
  planReview: () => import('./views/planReview.js'),
  sourceReport: () => import('./views/sourceReport.js'),
  editor: () => import('./views/editor/editor.js'),
  visualReview: () => import('./views/visualReview.js'),
  analytics: () => import('./views/analytics.js'),
  usage: () => import('./views/usage.js'),
  apiKeys: () => import('./views/apiKeys.js'),
  library: () => import('./views/library.js'),
  videos: () => import('./views/videos.js'),
  admin: () => import('./views/admin.js'),
};

/** Top-bar links; `personalKeys` items only show while personal API keys are allowed. */
const NAV = [
  { name: 'projects', label: 'Projects', hash: '#/projects', icon: 'board' },
  { name: 'newProject', label: 'New lecture', hash: '#/new', icon: 'plus' },
  { name: 'videos', label: 'Videos', hash: '#/videos', icon: 'video' },
  { name: 'library', label: 'Library', hash: '#/library', icon: 'film' },
  { name: 'usage', label: 'Usage', hash: '#/usage', icon: 'chart' },
  { name: 'apiKeys', label: 'API keys', hash: '#/keys', icon: 'key', personalKeys: true },
  { name: 'admin', label: 'Admin', hash: '#/admin', icon: 'user', roles: ['admin'] },
];

/**
 * Create and start the Studio inside `root`.
 * @param {HTMLElement} root
 */
export async function startStudio(root) {
  /** @type {User | null} */
  let user = null;
  /** @type {Promise<any> | null} */
  let metaPromise = null;
  /** @type {ViewHandle | null} */
  let currentView = null;
  /** @type {string | null} */
  let currentRouteName = null;
  let mountToken = 0;
  /** @type {AbortController | null} */
  let mountAbort = null;
  /** @type {string | null} */
  let returnTo = null;
  /** @type {((next: any) => boolean | Promise<boolean>) | null} */
  let leaveGuard = null;
  /** /api/meta `api_keys.personal_enabled` for the signed-in user (links to the API keys page). */
  let personalKeys = false;
  /** The /api/meta request `personalKeys` was (or is being) read from. @type {Promise<any> | null} */
  let flagsSource = null;

  clear(root);
  root.setAttribute('data-app-root', '');
  const main = h('main', { class: 'app-main', tabindex: '-1', __trusted: true, id: 'main-content' });
  const nav = h('nav', { class: 'topnav', 'aria-label': 'Main' });
  const userSlot = h('div', { class: 'topbar-user' });
  const skip = h('a', { class: 'skip-link', href: '#main-content' }, 'Skip to main content');
  skip.addEventListener('click', (ev) => {
    ev.preventDefault();
    main.focus();
  });
  const header = h(
    'header',
    { class: 'topbar' },
    h(
      'a',
      { class: 'st-brand', href: DEFAULT_ROUTE, 'aria-label': `${APP_TITLE} home` },
      brandMark(40),
      h('span', { class: 'st-brand-text' }, h('span', { class: 'st-brand-title' }, APP_TITLE), h('span', { class: 'st-brand-subtitle' }, APP_SUBTITLE)),
    ),
    nav,
    userSlot,
  );
  root.append(skip, header, main);
  initToasts(document.body);

  const router = new HashRouter({ onRoute: (r, hash) => void onRoute(r, hash) });

  /** @type {AppContext} */
  const app = {
    user: () => user,
    meta: () => {
      if (!metaPromise) {
        metaPromise = get('/api/meta');
        metaPromise.catch(() => {
          metaPromise = null;
        });
      }
      return metaPromise;
    },
    invalidateMeta: () => {
      metaPromise = null;
    },
    navigate: (hash, opts) => router.navigate(hash, opts),
    replaceHash: (hash) => router.replaceQuiet(hash),
    toast: (message, opts) => toast(message, opts),
    setUser: (u) => {
      user = u;
      if (u) noteSignedInUser(u.id);
      renderChrome();
    },
    setTitle: (title) => {
      document.title = title ? `${title} · ${APP_TITLE}` : APP_TITLE;
    },
    setLeaveGuard: (guard) => {
      leaveGuard = guard;
      router.setGuard(guard);
    },
    reportError: (err, fallback) => {
      if (isAuthError(err)) return;
      console.error(err);
      toast(errorMessage(err, fallback), { kind: 'error' });
    },
    continueAfterLogin: () => {
      const target = returnTo && returnTo !== '#/login' ? returnTo : DEFAULT_ROUTE;
      returnTo = null;
      router.navigate(user && user.must_change_password ? '#/change-password' : target, { replace: true, force: true });
    },
  };

  function renderChrome() {
    clear(nav);
    clear(userSlot);
    header.classList.toggle('signed-out', !user || user.must_change_password);
    if (!user || user.must_change_password) {
      if (user) userSlot.appendChild(signOutButton());
      return;
    }
    syncMetaFlags();
    for (const item of NAV) {
      if (item.roles && !item.roles.includes(user.role)) continue;
      if (item.personalKeys && !personalKeys) continue;
      const active = currentRouteName === item.name || (item.name === 'projects' && ['project', 'planReview', 'sourceReport', 'editor', 'visualReview', 'analytics'].includes(currentRouteName || ''));
      nav.appendChild(h('a', { class: ['nav-link', active ? 'active' : ''], href: item.hash, 'aria-current': active ? 'page' : undefined }, icon(item.icon), h('span', {}, item.label)));
    }
    const menu = menuButton(
      user.username,
      [
        ...(personalKeys ? [{ label: 'API keys', icon: 'key', onClick: () => router.navigate('#/keys') }] : []),
        { label: 'Change password', icon: 'user', onClick: () => router.navigate('#/change-password') },
        { label: 'Sign out', icon: 'logout', onClick: () => void signOut() },
      ],
      { icon: 'user', kind: 'ghost' },
    );
    userSlot.append(h('span', { class: ['role-pill', user.role === 'admin' ? 'admin' : ''] }, user.role), menu.el);
  }

  /**
   * Read the chrome's /api/meta flags (personal API keys) once per meta request; the top bar is
   * drawn again when they change. A failed request leaves the links hidden.
   */
  function syncMetaFlags() {
    const pending = app.meta();
    if (pending === flagsSource) return;
    flagsSource = pending;
    pending.then(
      (meta) => {
        if (flagsSource !== pending) return;
        const next = !!(meta && meta.api_keys && meta.api_keys.personal_enabled);
        if (next === personalKeys) return;
        personalKeys = next;
        renderChrome();
      },
      () => {},
    );
  }

  function signOutButton() {
    const b = h('button', { type: 'button', class: 'btn btn-ghost btn-sm' }, icon('logout'), h('span', {}, 'Sign out'));
    b.addEventListener('click', () => void signOut());
    return b;
  }

  async function signOut() {
    if (leaveGuard && !(await leaveGuard(null))) return;
    leaveGuard = null;
    router.setGuard(null);
    try {
      await post('/api/auth/logout', {});
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) app.reportError(err, 'Could not sign out cleanly.');
    }
    user = null;
    metaPromise = null;
    personalKeys = false;
    flagsSource = null;
    clearAllDrafts(); // shared lab PCs: nothing of this lecture stays in the browser
    renderChrome();
    toast('Signed out.', { kind: 'success' });
    router.navigate('#/login', { force: true });
  }

  /**
   * @param {ResolvedRoute | null} resolved
   * @param {string} hash
   */
  async function onRoute(resolved, hash) {
    if (!resolved) {
      router.navigate(user ? DEFAULT_ROUTE : '#/login', { replace: true });
      if (hash && hash !== '#' && hash !== '#/') toast('Page not found.', { kind: 'warning' });
      return;
    }
    const verdict = guardRoute(resolved.route, user);
    if ('redirect' in verdict) {
      if (!user && resolved.route.access !== 'public') returnTo = resolved.hash;
      router.navigate(verdict.redirect, { replace: true });
      return;
    }
    await mountView(resolved);
  }

  /** @param {ResolvedRoute} resolved */
  async function mountView(resolved) {
    const token = ++mountToken;
    if (mountAbort) mountAbort.abort();
    const abort = new AbortController();
    mountAbort = abort;
    /** The view's AppContext: a view that is no longer current cannot change the leave guard. */
    /** @type {AppContext} */
    const viewApp = {
      ...app,
      setLeaveGuard: (guard) => {
        if (!abort.signal.aborted && token === mountToken) app.setLeaveGuard(guard);
      },
    };
    if (currentView && currentView.destroy) {
      try {
        currentView.destroy();
      } catch (err) {
        console.warn('view destroy failed', err);
      }
    }
    currentView = null;
    leaveGuard = null;
    router.setGuard(null);
    closeAllModals();
    currentRouteName = resolved.route.name;
    renderChrome();
    app.setTitle(resolved.route.title);
    clear(main);
    main.setAttribute('aria-busy', 'true');
    main.appendChild(spinner());
    const container = h('div', { class: ['view', `view-${resolved.route.name}`] });
    try {
      const mod = await VIEWS[resolved.route.name]();
      if (token !== mountToken) return;
      const handle = await mod.mount(container, { params: resolved.params, query: resolved.query, app: viewApp, signal: abort.signal });
      if (token !== mountToken) {
        if (handle && handle.destroy) handle.destroy();
        return;
      }
      currentView = handle || null;
      clear(main);
      main.appendChild(container);
      const heading = /** @type {HTMLElement | null} */ (container.querySelector('h1'));
      if (heading && !container.contains(document.activeElement)) {
        heading.setAttribute('tabindex', '-1');
        heading.focus({ preventScroll: true });
      }
    } catch (err) {
      if (token !== mountToken) return;
      clear(main);
      if (isAuthError(err)) return;
      console.error(err);
      main.appendChild(errorState(errorMessage(err, 'This page could not be loaded.'), () => router.reload()));
    } finally {
      if (token === mountToken) main.removeAttribute('aria-busy');
    }
  }

  // --- global error handling ------------------------------------------------------------
  let lastGlobalToast = 0;
  /** @type {Array<() => void>} */
  const cleanups = [];
  /**
   * @param {string} type
   * @param {(ev: any) => void} fn
   */
  const listen = (type, fn) => {
    window.addEventListener(type, fn);
    cleanups.push(() => window.removeEventListener(type, fn));
  };
  const dispose = () => {
    if (mountAbort) mountAbort.abort();
    router.stop();
    for (const fn of cleanups.splice(0)) fn();
    if (currentView && currentView.destroy) currentView.destroy();
    currentView = null;
  };
  listen('unhandledrejection', (ev) => {
    const reason = ev.reason;
    if (isAuthError(reason)) {
      ev.preventDefault();
      return;
    }
    console.error('Unhandled rejection', reason);
    if (Date.now() - lastGlobalToast > 3000) {
      lastGlobalToast = Date.now();
      toast(errorMessage(reason), { kind: 'error' });
    }
  });
  listen('error', (ev) => {
    if (!(ev instanceof ErrorEvent) || !ev.error) return; // resource load errors are handled locally
    console.error('Uncaught error', ev.error);
    if (Date.now() - lastGlobalToast > 3000) {
      lastGlobalToast = Date.now();
      toast('Something went wrong on this page. Your work is autosaved locally.', { kind: 'error' });
    }
  });
  listen('beforeunload', (ev) => {
    if (document.querySelector('[data-dirty="true"]')) {
      ev.preventDefault();
      ev.returnValue = '';
    }
  });

  // --- auth bootstrap ---------------------------------------------------------------------
  main.appendChild(spinner('Starting…'));
  try {
    const res = await get('/api/auth/me');
    user = res && res.user ? res.user : null;
    if (user) noteSignedInUser(user.id);
  } catch (err) {
    if (!(err instanceof ApiError && (err.status === 401 || err.code === 'password_change_required'))) {
      clear(main);
      main.appendChild(errorState(errorMessage(err, 'Could not reach the server.'), () => location.reload()));
      return { app, router, dispose };
    }
    user = null;
  }

  const offAuth = onAuthError((err) => {
    if (err.status === 401) {
      const wasSignedIn = !!user;
      user = null;
      metaPromise = null;
      personalKeys = false;
      flagsSource = null;
      renderChrome();
      if (wasSignedIn) {
        returnTo = location.hash && location.hash !== '#/login' ? location.hash : null;
        toast('Your session has ended. Please sign in again.', { kind: 'warning' });
        router.navigate('#/login', { force: true });
      }
    } else if (err.code === 'password_change_required') {
      if (user) user = { ...user, must_change_password: true };
      renderChrome();
      router.navigate('#/change-password', { force: true });
    }
  });

  cleanups.push(() => offAuth());

  renderChrome();
  router.start();
  return { app, router, dispose };
}

// Auto-start in the browser (not when imported by tests).
if (typeof document !== 'undefined' && document.querySelector('[data-studio-root]')) {
  const rootEl = /** @type {HTMLElement} */ (document.querySelector('[data-studio-root]'));
  void startStudio(rootEl);
}
