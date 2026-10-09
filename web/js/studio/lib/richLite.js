// @ts-check
/**
 * Rich-lite tokenizer (aadhi/schemas/screenplay.py RICH_LITE) used by the editor preview
 * when the player's richtext.js is unavailable, and by `stripRichLite` for plain-text labels.
 *   **bold**  *italic*  `code`  $latex$  [[keyword]]  \$ \*  (escapes)
 * Never produces HTML: callers build DOM from tokens with textContent.
 */

/** @typedef {{ type: 'text' | 'bold' | 'italic' | 'code' | 'math' | 'keyword', text: string }} RichToken */

/**
 * @param {string} input
 * @returns {RichToken[]}
 */
export function tokenizeRichLite(input) {
  const s = String(input || '');
  /** @type {RichToken[]} */
  const out = [];
  let buf = '';
  const flush = () => {
    if (buf) out.push({ type: 'text', text: buf });
    buf = '';
  };
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c === '\\' && (s[i + 1] === '$' || s[i + 1] === '*' || s[i + 1] === '`')) {
      buf += s[i + 1];
      i += 2;
      continue;
    }
    if (c === '*' && s[i + 1] === '*') {
      const end = findClose(s, '**', i + 2);
      if (end > i + 2) {
        flush();
        out.push({ type: 'bold', text: unescape(s.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    } else if (c === '*') {
      const end = findClose(s, '*', i + 1);
      if (end > i + 1 && s[i + 1] !== ' ') {
        flush();
        out.push({ type: 'italic', text: unescape(s.slice(i + 1, end)) });
        i = end + 1;
        continue;
      }
    } else if (c === '`') {
      const end = s.indexOf('`', i + 1);
      if (end > i + 1) {
        flush();
        out.push({ type: 'code', text: s.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    } else if (c === '$') {
      const end = findClose(s, '$', i + 1);
      if (end > i + 1) {
        flush();
        out.push({ type: 'math', text: s.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    } else if (c === '[' && s[i + 1] === '[') {
      const end = s.indexOf(']]', i + 2);
      if (end > i + 2) {
        flush();
        out.push({ type: 'keyword', text: unescape(s.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }
    buf += c;
    i += 1;
  }
  flush();
  return out;
}

/**
 * Index of the next unescaped `marker` at or after `from` (-1 if none).
 * @param {string} s
 * @param {string} marker
 * @param {number} from
 */
function findClose(s, marker, from) {
  for (let i = from; i < s.length; i++) {
    if (s[i] === '\\') {
      i += 1;
      continue;
    }
    if (s.startsWith(marker, i)) {
      if (marker === '*' && s[i + 1] === '*') {
        i += 1;
        continue;
      }
      return i;
    }
  }
  return -1;
}

/** @param {string} s */
function unescape(s) {
  return s.replace(/\\([$*`])/g, '$1');
}

/**
 * Plain text without markup (scene list labels, accessible names).
 * @param {string} input
 */
export function stripRichLite(input) {
  return tokenizeRichLite(input)
    .map((t) => t.text)
    .join('');
}
