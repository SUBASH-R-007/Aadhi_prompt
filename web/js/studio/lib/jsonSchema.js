// @ts-check
/**
 * Minimal JSON-Schema toolkit for Pydantic-generated schemas (Manim template params and
 * similar): `$ref`/`$defs` resolution, nullable detection (`anyOf [X, null]`), defaults and
 * validation. The DOM form generator lives in components/schemaForm.js.
 *
 * Supported: type string/number/integer/boolean/array/object/null, enum, const, $ref, allOf
 * (single), anyOf/oneOf with null, type arrays, minLength/maxLength/pattern, minimum/maximum,
 * exclusiveMinimum/exclusiveMaximum, multipleOf, minItems/maxItems/uniqueItems, items,
 * prefixItems (tuples), required, additionalProperties:false, default, title, description.
 * Anything else is edited as raw JSON ("json" kind) and only checked for syntax.
 */

import { clone, deepEqual } from '../util.js';

/** @typedef {Record<string, any>} Schema */
/**
 * @typedef {'string' | 'number' | 'integer' | 'boolean' | 'enum' | 'const' | 'array' | 'tuple' | 'object' | 'json' | 'null'} SchemaKind
 */
/**
 * @typedef {object} SchemaInfo
 * @property {SchemaKind} kind
 * @property {boolean} nullable
 * @property {Schema} schema   dereferenced schema without the null branch
 */
/** @typedef {{ path: string, message: string }} SchemaError */

const MAX_REF_DEPTH = 32;

/**
 * Resolve a local JSON pointer ("#/$defs/Foo").
 * @param {string} ref
 * @param {Schema} root
 * @returns {Schema}
 */
function resolvePointer(ref, root) {
  if (typeof ref !== 'string' || !ref.startsWith('#')) throw new Error(`unsupported $ref ${ref}`);
  const parts = ref
    .slice(1)
    .split('/')
    .filter(Boolean)
    .map((p) => decodeURIComponent(p.replace(/~1/g, '/').replace(/~0/g, '~')));
  /** @type {any} */
  let node = root;
  for (const p of parts) {
    if (!node || typeof node !== 'object' || !(p in node)) throw new Error(`unresolvable $ref ${ref}`);
    node = node[p];
  }
  return node;
}

/**
 * Follow `$ref` chains and single-member `allOf`, merging sibling keywords (title, default,
 * description) over the referenced schema.
 * @param {Schema} schema
 * @param {Schema} root
 * @returns {Schema}
 */
export function deref(schema, root) {
  let s = schema || {};
  for (let depth = 0; depth < MAX_REF_DEPTH; depth++) {
    if (s.$ref) {
      const { $ref, ...rest } = s;
      s = { ...resolvePointer($ref, root), ...rest };
      continue;
    }
    if (Array.isArray(s.allOf) && s.allOf.length === 1) {
      const { allOf, ...rest } = s;
      s = { ...allOf[0], ...rest };
      continue;
    }
    return s;
  }
  throw new Error('$ref nesting too deep (cyclic schema?)');
}

/**
 * @param {Schema} s
 */
function isNullSchema(s) {
  return s && (s.type === 'null' || (Array.isArray(s.enum) && s.enum.length === 1 && s.enum[0] === null) || s.const === null);
}

/**
 * Classify a schema for editing.
 * @param {Schema} schema
 * @param {Schema} root
 * @returns {SchemaInfo}
 */
export function analyze(schema, root) {
  let s = deref(schema, root);
  let nullable = false;
  const union = s.anyOf || s.oneOf;
  if (Array.isArray(union)) {
    const members = union.map((m) => deref(m, root));
    const nonNull = members.filter((m) => !isNullSchema(m));
    nullable = nonNull.length !== members.length;
    if (nonNull.length === 1) {
      const { anyOf: _a, oneOf: _o, ...rest } = s;
      s = { ...nonNull[0], ...rest };
    } else {
      return { kind: 'json', nullable, schema: s };
    }
  }
  let type = s.type;
  if (Array.isArray(type)) {
    const nonNull = type.filter((t) => t !== 'null');
    if (nonNull.length !== type.length) nullable = true;
    type = nonNull.length === 1 ? nonNull[0] : undefined;
    if (nonNull.length !== 1) return { kind: 'json', nullable, schema: s };
    s = { ...s, type };
  }
  if ('const' in s) return { kind: 'const', nullable, schema: s };
  if (Array.isArray(s.enum)) {
    const values = s.enum.filter((/** @type {any} */ v) => v !== null);
    if (values.length !== s.enum.length) nullable = true;
    return { kind: 'enum', nullable, schema: { ...s, enum: values } };
  }
  if (!type) {
    if (s.properties) type = 'object';
    else if (s.items || s.prefixItems) type = 'array';
  }
  switch (type) {
    case 'string':
    case 'number':
    case 'integer':
    case 'boolean':
    case 'null':
      return { kind: type, nullable, schema: s };
    case 'array':
      if (Array.isArray(s.prefixItems)) return { kind: 'tuple', nullable, schema: s };
      return { kind: 'array', nullable, schema: s };
    case 'object':
      if (s.properties && typeof s.properties === 'object') return { kind: 'object', nullable, schema: s };
      return { kind: 'json', nullable, schema: s };
    default:
      return { kind: 'json', nullable, schema: s };
  }
}

/**
 * Default value for a schema (respects `default`, enums, minItems, required properties).
 * @param {Schema} schema
 * @param {Schema} root
 * @param {number} [depth]
 * @returns {any}
 */
export function defaultFor(schema, root, depth = 0) {
  if (depth > 20) return null;
  const info = analyze(schema, root);
  const s = info.schema;
  if ('default' in s) return clone(s.default);
  if (info.nullable) return null;
  switch (info.kind) {
    case 'const':
      return clone(s.const);
    case 'enum':
      return s.enum.length ? clone(s.enum[0]) : null;
    case 'string':
      return '';
    case 'integer':
    case 'number': {
      if (typeof s.minimum === 'number') return info.kind === 'integer' ? Math.ceil(s.minimum) : s.minimum;
      if (typeof s.exclusiveMinimum === 'number') return info.kind === 'integer' ? Math.floor(s.exclusiveMinimum) + 1 : s.exclusiveMinimum + 1;
      if (typeof s.maximum === 'number' && s.maximum < 0) return s.maximum;
      return 0;
    }
    case 'boolean':
      return false;
    case 'array': {
      const n = Math.max(0, Number(s.minItems) || 0);
      return Array.from({ length: n }, () => defaultFor(s.items || {}, root, depth + 1));
    }
    case 'tuple':
      return s.prefixItems.map((/** @type {Schema} */ p) => defaultFor(p, root, depth + 1));
    case 'object': {
      /** @type {Record<string, any>} */
      const out = {};
      const required = new Set(s.required || []);
      for (const [key, prop] of Object.entries(s.properties || {})) {
        const p = deref(prop, root);
        if ('default' in p) out[key] = clone(p.default);
        else if (required.has(key)) out[key] = defaultFor(prop, root, depth + 1);
      }
      return out;
    }
    case 'null':
      return null;
    default:
      return s.type === 'object' ? {} : null;
  }
}

/**
 * @param {string} path
 * @param {string | number} key
 */
export function joinPath(path, key) {
  if (typeof key === 'number') return `${path}[${key}]`;
  return path ? `${path}.${key}` : key;
}

/** @type {Map<string, RegExp | null>} */
const regexCache = new Map();

/** @param {string} pattern */
function compilePattern(pattern) {
  if (regexCache.has(pattern)) return regexCache.get(pattern) || null;
  let re = null;
  try {
    re = new RegExp(pattern, 'u');
  } catch {
    re = null; // Python-only syntax: the server remains authoritative
  }
  regexCache.set(pattern, re);
  return re;
}

/**
 * Validate a value against a schema.
 * @param {Schema} schema
 * @param {any} value
 * @param {Schema} [root]
 * @param {string} [path]
 * @returns {SchemaError[]}
 */
export function validateValue(schema, value, root = schema, path = '') {
  /** @type {SchemaError[]} */
  const errors = [];
  const push = (/** @type {string} */ message) => errors.push({ path, message });
  let info;
  try {
    info = analyze(schema, root);
  } catch (e) {
    return [{ path, message: /** @type {Error} */ (e).message }];
  }
  const s = info.schema;
  if (value === null) {
    if (info.nullable || info.kind === 'null' || info.kind === 'json') return errors;
    push('A value is required.');
    return errors;
  }
  if (value === undefined) {
    push('A value is required.');
    return errors;
  }
  switch (info.kind) {
    case 'const':
      if (!deepEqual(value, s.const)) push(`Must be ${JSON.stringify(s.const)}.`);
      break;
    case 'enum':
      if (!s.enum.some((/** @type {any} */ v) => deepEqual(v, value))) push(`Must be one of: ${s.enum.map(String).join(', ')}.`);
      break;
    case 'string': {
      if (typeof value !== 'string') {
        push('Must be text.');
        break;
      }
      const len = [...value].length;
      if (typeof s.minLength === 'number' && len < s.minLength) {
        push(s.minLength === 1 ? 'Must not be empty.' : `At least ${s.minLength} characters.`);
      }
      if (typeof s.maxLength === 'number' && len > s.maxLength) push(`At most ${s.maxLength} characters.`);
      if (typeof s.pattern === 'string') {
        const re = compilePattern(s.pattern);
        if (re && !re.test(value)) push('Does not match the required format.');
      }
      break;
    }
    case 'integer':
    case 'number': {
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        push('Must be a number.');
        break;
      }
      if (info.kind === 'integer' && !Number.isInteger(value)) push('Must be a whole number.');
      if (typeof s.minimum === 'number' && value < s.minimum) push(`Must be at least ${s.minimum}.`);
      if (typeof s.maximum === 'number' && value > s.maximum) push(`Must be at most ${s.maximum}.`);
      if (typeof s.exclusiveMinimum === 'number' && value <= s.exclusiveMinimum) push(`Must be greater than ${s.exclusiveMinimum}.`);
      if (typeof s.exclusiveMaximum === 'number' && value >= s.exclusiveMaximum) push(`Must be less than ${s.exclusiveMaximum}.`);
      if (typeof s.multipleOf === 'number' && s.multipleOf > 0) {
        const q = value / s.multipleOf;
        if (Math.abs(q - Math.round(q)) > 1e-9) push(`Must be a multiple of ${s.multipleOf}.`);
      }
      break;
    }
    case 'boolean':
      if (typeof value !== 'boolean') push('Must be true or false.');
      break;
    case 'null':
      push('Must be empty.');
      break;
    case 'array':
    case 'tuple': {
      if (!Array.isArray(value)) {
        push('Must be a list.');
        break;
      }
      if (typeof s.minItems === 'number' && value.length < s.minItems) push(`Add at least ${s.minItems} item${s.minItems === 1 ? '' : 's'}.`);
      if (typeof s.maxItems === 'number' && value.length > s.maxItems) push(`At most ${s.maxItems} items.`);
      if (s.uniqueItems) {
        for (let i = 0; i < value.length; i++) {
          if (value.slice(0, i).some((v) => deepEqual(v, value[i]))) {
            push('Items must be unique.');
            break;
          }
        }
      }
      if (info.kind === 'tuple') {
        const prefix = /** @type {Schema[]} */ (s.prefixItems);
        if (value.length !== prefix.length && s.items === false) push(`Must have exactly ${prefix.length} items.`);
        prefix.forEach((p, i) => {
          if (i < value.length) errors.push(...validateValue(p, value[i], root, joinPath(path, i)));
          else errors.push({ path: joinPath(path, i), message: 'A value is required.' });
        });
      } else if (s.items && typeof s.items === 'object') {
        value.forEach((v, i) => errors.push(...validateValue(s.items, v, root, joinPath(path, i))));
      }
      break;
    }
    case 'object': {
      if (typeof value !== 'object' || Array.isArray(value)) {
        push('Must be an object.');
        break;
      }
      const props = s.properties || {};
      for (const key of s.required || []) {
        if (value[key] === undefined) errors.push({ path: joinPath(path, key), message: 'A value is required.' });
      }
      for (const [key, v] of Object.entries(value)) {
        if (v === undefined) continue;
        if (props[key]) errors.push(...validateValue(props[key], v, root, joinPath(path, key)));
        else if (s.additionalProperties === false) errors.push({ path: joinPath(path, key), message: 'Unknown field.' });
        else if (s.additionalProperties && typeof s.additionalProperties === 'object') {
          errors.push(...validateValue(s.additionalProperties, v, root, joinPath(path, key)));
        }
      }
      break;
    }
    default:
      // json: open dicts are checked against additionalProperties; other shapes are left to
      // the server.
      if (s.type === 'object') {
        if (typeof value !== 'object' || Array.isArray(value)) push('Must be an object.');
        else if (s.additionalProperties && typeof s.additionalProperties === 'object') {
          for (const [key, v] of Object.entries(value)) errors.push(...validateValue(s.additionalProperties, v, root, joinPath(path, key)));
        }
      }
      break;
  }
  return errors;
}

/**
 * "max_value" -> "Max value"
 * @param {string} key
 */
export function humanize(key) {
  const s = String(key).replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim();
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
