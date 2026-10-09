// @ts-check
/**
 * User-facing error messages for API failures (docs/API.md error codes). Server `detail`
 * strings are already redacted server-side; network failures get a generic message.
 */

import { ApiError } from '../shared/api.js';

/**
 * @param {unknown} err
 * @param {string} [fallback]
 * @returns {string}
 */
export function errorMessage(err, fallback = 'Something went wrong. Please try again.') {
  if (err instanceof ApiError) {
    switch (err.code) {
      case 'rate_limited':
        return `Too many requests. Try again${err.retryAfter ? ` in ${Math.ceil(err.retryAfter)} s` : ' shortly'}.`;
      case 'budget':
        return `Your daily AI budget is used up${err.retryAfter ? `; it resets in about ${Math.ceil(err.retryAfter / 3600)} h` : ''}. Ask an admin to raise it.`;
      case 'csrf':
        return 'Your session could not be verified. Reload the page and try again.';
      case 'unauthenticated':
        return 'Your session has ended. Please sign in again.';
      case 'password_change_required':
        return 'Please change your password to continue.';
      case 'forbidden':
        return 'You do not have permission to do that.';
      case 'not_found':
        return 'Not found. It may have been deleted.';
      case 'job_in_progress':
        return 'Another job is already running for this version. Wait for it to finish.';
      case 'version_busy':
        return 'This version is busy with a running job. Try again when it finishes.';
      case 'timeline_stale':
        return 'The video timeline is out of date. Build the lecture first.';
      case 'revision_conflict':
        return 'Someone else saved changes to this lecture.';
      case 'too_large':
        return 'The file or request is too large.';
      case 'unsupported_type':
        return 'That file type is not supported.';
      case 'api_keys_disabled':
        return 'Saving API keys in the Studio is turned off on this server.';
      case 'render_unavailable': // 503 with a plain reason (missing ffmpeg / encoder / browser)
        return err.message || 'This server cannot render videos right now.';
      default:
        if (err.status >= 500) return 'The server had a problem. Please try again in a moment.';
        return err.message || fallback;
    }
  }
  if (err instanceof TypeError) return 'Network error: check your connection and try again.';
  if (err instanceof Error && err.name === 'AbortError') return 'Request cancelled.';
  return fallback;
}

/**
 * True for errors the global auth handler already deals with (no extra toast needed).
 * @param {unknown} err
 */
export function isAuthError(err) {
  return err instanceof ApiError && (err.status === 401 || err.code === 'password_change_required');
}
