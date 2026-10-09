// @ts-check
/**
 * "Technical details" switch for the Studio. Teachers see plain words; ids (render and job numbers),
 * screenplay revisions and raw job stage names are shown only when the Studio's URL carries `?debug`:
 * in the page query (`/?debug=1#/projects`, kept while moving between pages) or in the hash query
 * (`#/p/5?debug`, kept only on that page). `debug=0`, `false`, `no` or `off` turns it off. Pure apart
 * from reading the location it is given.
 */

const OFF = new Set(['0', 'false', 'no', 'off']);

/**
 * Query part of a location hash ("#/p/5?debug=1" -> "debug=1").
 * @param {string} hash
 */
function hashQuery(hash) {
  const i = hash.indexOf('?');
  return i >= 0 ? hash.slice(i + 1) : '';
}

/**
 * True when technical details should be shown (`?debug` in the page or hash query).
 * @param {{ search?: string, hash?: string } | null} [loc]  defaults to the page location
 */
export function showTechnical(loc = typeof location === 'undefined' ? null : location) {
  if (!loc) return false;
  for (const part of [loc.search || '', hashQuery(loc.hash || '')]) {
    const params = new URLSearchParams(part.replace(/^\?/, ''));
    if (params.has('debug')) return !OFF.has(String(params.get('debug') || '').trim().toLowerCase());
  }
  return false;
}
