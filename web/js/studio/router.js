// @ts-check
/**
 * Hash router for the Studio (ARCHITECTURE §10):
 *   #/login  #/change-password  #/projects  #/new  #/p/:id  #/p/:id/v/:vid/plan
 *   #/p/:id/v/:vid/source  #/p/:id/v/:vid/edit  #/p/:id/analytics  #/usage  #/keys  #/admin
 *   #/library  #/v/:vid/review  #/videos
 * Parsing/matching are pure functions (unit-tested); `HashRouter` wires them to `hashchange`
 * and supports a leave guard (unsaved editor changes).
 */

/**
 * @typedef {'public' | 'pending' | 'user'} RouteAccess
 *   public  = no session required (login);
 *   pending = signed in, allowed while must_change_password is set;
 *   user    = signed in and password not pending.
 */

/**
 * @typedef {object} RouteDef
 * @property {string} name
 * @property {string} pattern   e.g. "/p/:id/v/:vid/edit"; params are positive integers
 * @property {RouteAccess} access
 * @property {string[]} [roles] roles allowed (default: any signed-in role)
 * @property {string} title     document title fragment
 */

/** @type {RouteDef[]} */
export const ROUTES = [
  { name: 'login', pattern: '/login', access: 'public', title: 'Sign in' },
  { name: 'changePassword', pattern: '/change-password', access: 'pending', title: 'Change password' },
  { name: 'projects', pattern: '/projects', access: 'user', title: 'Projects' },
  { name: 'newProject', pattern: '/new', access: 'user', title: 'New lecture' },
  { name: 'project', pattern: '/p/:id', access: 'user', title: 'Project' },
  { name: 'planReview', pattern: '/p/:id/v/:vid/plan', access: 'user', title: 'Plan review' },
  { name: 'sourceReport', pattern: '/p/:id/v/:vid/source', access: 'user', title: 'How Aadhi read your document' },
  { name: 'editor', pattern: '/p/:id/v/:vid/edit', access: 'user', title: 'Editor' },
  { name: 'visualReview', pattern: '/v/:vid/review', access: 'user', title: 'Visual review' },
  { name: 'analytics', pattern: '/p/:id/analytics', access: 'user', title: 'Analytics' },
  { name: 'usage', pattern: '/usage', access: 'user', title: 'Usage' },
  { name: 'apiKeys', pattern: '/keys', access: 'user', title: 'API keys' },
  { name: 'library', pattern: '/library', access: 'user', title: 'Library' },
  { name: 'videos', pattern: '/videos', access: 'user', title: 'Videos' },
  { name: 'admin', pattern: '/admin', access: 'user', roles: ['admin'], title: 'Admin' },
];

export const DEFAULT_ROUTE = '#/projects';

/** Max accepted integer param (guards against absurd ids). */
const MAX_ID = Number.MAX_SAFE_INTEGER;

/**
 * Split a location hash into a normalised path and query object.
 * "#/p/3?tab=renders" -> { path: "/p/3", query: { tab: "renders" } }
 * Hashes that are not routes (e.g. "#main" skip links) give `path: null`.
 * @param {string} hash
 * @returns {{ path: string | null, query: Record<string, string> }}
 */
export function parseHash(hash) {
  let raw = String(hash || '');
  if (raw.startsWith('#')) raw = raw.slice(1);
  if (raw === '') return { path: '', query: {} };
  if (!raw.startsWith('/')) return { path: null, query: {} };
  const qIndex = raw.indexOf('?');
  let pathPart = qIndex >= 0 ? raw.slice(0, qIndex) : raw;
  const queryPart = qIndex >= 0 ? raw.slice(qIndex + 1) : '';
  /** @type {Record<string, string>} */
  const query = {};
  if (queryPart) {
    for (const [k, v] of new URLSearchParams(queryPart)) query[k] = v;
  }
  pathPart = pathPart.replace(/\/{2,}/g, '/');
  if (pathPart.length > 1 && pathPart.endsWith('/')) pathPart = pathPart.slice(0, -1);
  return { path: pathPart, query };
}

/**
 * Match a path against the route table. Integer params (`:id`, `:vid`) must be positive
 * decimal integers without leading zeros; they are returned as numbers.
 * @param {string} path
 * @param {RouteDef[]} [routes]
 * @returns {{ route: RouteDef, params: Record<string, number> } | null}
 */
export function matchRoute(path, routes = ROUTES) {
  const segs = splitPath(path);
  for (const route of routes) {
    const pat = splitPath(route.pattern);
    if (pat.length !== segs.length) continue;
    /** @type {Record<string, number>} */
    const params = {};
    let ok = true;
    for (let i = 0; i < pat.length; i++) {
      const p = pat[i];
      const s = segs[i];
      if (p.startsWith(':')) {
        if (!/^[1-9]\d{0,15}$/.test(s)) {
          ok = false;
          break;
        }
        const n = Number(s);
        if (!Number.isSafeInteger(n) || n > MAX_ID) {
          ok = false;
          break;
        }
        params[p.slice(1)] = n;
      } else if (p !== s) {
        ok = false;
        break;
      }
    }
    if (ok) return { route, params };
  }
  return null;
}

/** @param {string} path */
function splitPath(path) {
  return String(path || '')
    .split('/')
    .filter((s) => s !== '');
}

/**
 * @typedef {object} ResolvedRoute
 * @property {RouteDef} route
 * @property {Record<string, number>} params
 * @property {Record<string, string>} query
 * @property {string} path
 * @property {string} hash  canonical hash ("#/p/3?tab=x")
 */

/**
 * Resolve a full hash; null when it is not a known route.
 * @param {string} hash
 * @param {RouteDef[]} [routes]
 * @returns {ResolvedRoute | null}
 */
export function resolveHash(hash, routes = ROUTES) {
  const { path, query } = parseHash(hash);
  if (path === null || path === '') return null;
  const m = matchRoute(path, routes);
  if (!m) return null;
  return { route: m.route, params: m.params, query, path, hash: buildHash(path, query) };
}

/**
 * Build a hash for a named route.
 * @param {string} name
 * @param {Record<string, number | string>} [params]
 * @param {Record<string, string | number | boolean | null | undefined>} [query]
 * @param {RouteDef[]} [routes]
 * @returns {string}
 */
export function href(name, params = {}, query = {}, routes = ROUTES) {
  const route = routes.find((r) => r.name === name);
  if (!route) throw new Error(`unknown route ${name}`);
  const path = route.pattern.replace(/:([a-z]+)/g, (_, key) => {
    const v = params[key];
    if (v === undefined || v === null || !/^[1-9]\d*$/.test(String(v))) throw new Error(`route ${name}: bad param ${key}`);
    return String(v);
  });
  return buildHash(path, query);
}

/**
 * @param {string} path
 * @param {Record<string, string | number | boolean | null | undefined>} query
 */
function buildHash(path, query) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query || {})) {
    if (v === undefined || v === null || v === '') continue;
    qs.set(k, String(v));
  }
  const s = qs.toString();
  return `#${path}${s ? `?${s}` : ''}`;
}

/**
 * Decide where an auth state may go.
 * @param {RouteDef} route
 * @param {{ role: string, must_change_password: boolean } | null} user
 * @returns {{ redirect: string } | { ok: true }}
 */
export function guardRoute(route, user) {
  if (route.access === 'public') {
    if (user) return { redirect: user.must_change_password ? '#/change-password' : DEFAULT_ROUTE };
    return { ok: true };
  }
  if (!user) return { redirect: '#/login' };
  if (user.must_change_password && route.access !== 'pending') return { redirect: '#/change-password' };
  if (route.roles && !route.roles.includes(user.role)) return { redirect: DEFAULT_ROUTE };
  return { ok: true };
}

/**
 * @callback LeaveGuard
 * @param {ResolvedRoute | null} next
 * @returns {boolean | Promise<boolean>}  false = stay
 */

/** history.state key holding the router's position in the session history. */
const HISTORY_INDEX_KEY = 'aadhiIdx';

/**
 * Thin hashchange wiring. `onRoute` receives every accepted navigation (or `null` for
 * unknown routes; the caller decides the fallback).
 *
 * Each accepted history entry is stamped with its position (`history.state.aadhiIdx`), so
 * when the leave guard rejects a navigation the router can undo exactly that move: a new entry
 * (link, `location.hash = …`) or a forward move goes back, a Back move goes forward again, and
 * a replace navigation restores the URL in place. History entries are never overwritten, so
 * the previous page stays reachable with Back.
 */
export class HashRouter {
  /**
   * @param {{ onRoute: (r: ResolvedRoute | null, hash: string) => void, routes?: RouteDef[], win?: Window }} opts
   */
  constructor(opts) {
    this.onRoute = opts.onRoute;
    this.routes = opts.routes || ROUTES;
    this.win = opts.win || window;
    /** @type {LeaveGuard | null} */
    this.guard = null;
    this.currentHash = '';
    this.suppress = false;
    /** Position of the current entry in the session history (see HISTORY_INDEX_KEY). */
    this.index = 0;
    this.handle = () => {
      void this.dispatch();
    };
  }

  /** Listen for hash changes and route the current location (also when the hash is empty). */
  start() {
    this.win.addEventListener('hashchange', this.handle);
    const known = this.entryIndex();
    this.index = known === null ? 0 : known;
    void this.dispatch(true, true);
  }

  stop() {
    this.win.removeEventListener('hashchange', this.handle);
  }

  /** @param {LeaveGuard | null} fn */
  setGuard(fn) {
    this.guard = fn;
  }

  /**
   * Go to a hash. Navigating to the current hash re-runs its route (retry buttons, reloads).
   * @param {string} hash
   * @param {{ replace?: boolean, force?: boolean }} [opts]  force = skip the leave guard
   */
  navigate(hash, opts = {}) {
    if (opts.force) this.guard = null;
    const loc = this.win.location;
    if (opts.replace) {
      this.win.history.replaceState(this.win.history.state, '', hash);
      void this.dispatch(this.currentHash !== '' && this.win.location.hash === this.currentHash, true);
    } else if (loc.hash === hash) {
      void this.dispatch(true);
    } else {
      loc.hash = hash;
    }
  }

  /**
   * Update the URL (e.g. a search query) without re-dispatching the route.
   * @param {string} hash
   */
  replaceQuiet(hash) {
    this.win.history.replaceState(this.win.history.state, '', hash);
    this.currentHash = this.win.location.hash;
  }

  /** Re-run the current route (e.g. after login). */
  reload() {
    void this.dispatch(true, true);
  }

  /**
   * Position stamped on the current history entry, or null for a new (unstamped) entry.
   * @returns {number | null}
   */
  entryIndex() {
    const state = this.win.history.state;
    const v = state && typeof state === 'object' ? state[HISTORY_INDEX_KEY] : undefined;
    return typeof v === 'number' && Number.isInteger(v) ? v : null;
  }

  /** Stamp the current history entry with `this.index` (keeps other state keys). */
  stampEntry() {
    const state = this.win.history.state;
    const base = state && typeof state === 'object' ? state : {};
    if (base[HISTORY_INDEX_KEY] === this.index) return;
    this.win.history.replaceState({ ...base, [HISTORY_INDEX_KEY]: this.index }, '', this.win.location.href);
  }

  /**
   * Undo a navigation the leave guard rejected, without rewriting history entries.
   * @param {boolean} replaced  the navigation replaced the current entry (no new entry)
   */
  restoreAfterRejection(replaced) {
    const hist = this.win.history;
    if (replaced) {
      this.suppress = true;
      hist.replaceState(hist.state, '', this.currentHash);
      this.suppress = false;
      return;
    }
    const target = this.entryIndex();
    // Unstamped = a new entry was pushed (link click, location.hash): go back to ours.
    const delta = target === null ? -1 : this.index - target;
    if (delta !== 0) {
      hist.go(delta); // the resulting hashchange matches currentHash and is ignored
      return;
    }
    this.suppress = true;
    hist.replaceState(hist.state, '', this.currentHash);
    this.suppress = false;
  }

  /**
   * @param {boolean} [force]     re-dispatch even when the hash did not change
   * @param {boolean} [replaced]  the URL was replaced in place (no history entry was added)
   */
  async dispatch(force = false, replaced = false) {
    if (this.suppress) return;
    const hash = this.win.location.hash;
    const parsed = parseHash(hash);
    if (parsed.path === null) return; // in-page anchors (#main skip link) are not routes
    if (!force && hash === this.currentHash) return;
    const resolved = resolveHash(hash, this.routes);
    if (this.guard && this.currentHash && hash !== this.currentHash) {
      let ok = true;
      try {
        ok = await this.guard(resolved);
      } catch {
        ok = false;
      }
      if (!ok) {
        this.restoreAfterRejection(replaced);
        return;
      }
      this.guard = null;
    }
    if (!replaced) {
      const known = this.entryIndex();
      if (known !== null) this.index = known;
      else if (this.currentHash !== '' && hash !== this.currentHash) this.index += 1;
    }
    this.stampEntry();
    this.currentHash = hash;
    this.onRoute(resolved, hash);
  }
}
