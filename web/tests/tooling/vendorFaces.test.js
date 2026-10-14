// @ts-check
// scripts/vendor_faces.mjs: the B2b faces are vendored from the generated face table, each family with its LICENSE,
// and vendoring fails closed on a missing package, another version, a licence that is not the OFL text, a family
// without faces, a face outside its folder or a unicode-range that is not the package's (DYNAMIC_VIDEO_DESIGN §4.6,
// §4.12; B2 plan §2 "Fonts"). The real table against the real packages is checked by the repository's own vendoring
// (npm ci) and tests/compose/dynamic/test_faces.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { OFL_TEXT, vendorFaces } from '../../../scripts/vendor_faces.mjs';
import { FACES } from '../../js/dynamic/faces.gen.js';

const LATIN = 'U+0000-00FF,U+0131';

/** A face table with one B2b family (and one classic family, which is never copied). */
function table() {
  return {
    families: {
      demo: { css: 'dynamic', package: '@fontsource/demo', version: '5.3.0', licence: 'OFL-1.1', dir: 'fonts/dynamic/demo' },
      old: { css: 'classic', package: '@fontsource/old', version: '5.3.0', licence: 'OFL-1.1', dir: 'fonts' },
    },
    faces: [
      { family: 'demo', subset: 'latin', file: 'fonts/dynamic/demo/demo-latin-400-normal.woff2', source: '@fontsource/demo/files/demo-latin-400-normal.woff2', unicode_range: LATIN.split(',') },
      { family: 'old', subset: 'latin', file: 'fonts/old-latin-400-normal.woff2', source: '@fontsource/old/files/old-latin-400-normal.woff2', unicode_range: LATIN.split(',') },
    ],
  };
}

/** node_modules with the demo package; `over` changes one of its files. @param {Record<string, string | null>} [over] */
function tree(over = {}) {
  const root = mkdtempSync(join(tmpdir(), 'vendor-faces-'));
  const pkg = join(root, 'node_modules', '@fontsource', 'demo');
  mkdirSync(join(pkg, 'files'), { recursive: true });
  const files = {
    'package.json': JSON.stringify({ version: '5.3.0' }),
    LICENSE: `Copyright 2021 The Demo Project Authors\n\n${OFL_TEXT} - 26 February 2007\n`,
    'unicode.json': JSON.stringify({ latin: LATIN }),
    'files/demo-latin-400-normal.woff2': 'wOF2',
    ...over,
  };
  for (const [name, text] of Object.entries(files)) if (text !== null) writeFileSync(join(pkg, name), text);
  return { root, nm: join(root, 'node_modules'), out: join(root, 'vendor') };
}

test('the B2b faces and each family\'s LICENSE are copied; classic faces are not', () => {
  const t = tree();
  try {
    assert.deepEqual(vendorFaces(table(), t.nm, t.out), ['fonts/dynamic/demo/LICENSE', 'fonts/dynamic/demo/demo-latin-400-normal.woff2']);
    assert.equal(readFileSync(join(t.out, 'fonts/dynamic/demo/demo-latin-400-normal.woff2'), 'utf8'), 'wOF2');
    assert.match(readFileSync(join(t.out, 'fonts/dynamic/demo/LICENSE'), 'utf8'), /SIL OPEN FONT LICENSE Version 1\.1/);
    assert.ok(!existsSync(join(t.out, 'fonts/old-latin-400-normal.woff2')));
  } finally {
    rmSync(t.root, { recursive: true, force: true });
  }
});

test('vendoring fails closed', () => {
  /** @type {[Record<string, string | null>, (t: any) => void, RegExp][]} */
  const cases = [
    [{ 'package.json': null }, () => undefined, /missing .*run npm ci/],
    [{ 'package.json': JSON.stringify({ version: '5.2.0' }) }, () => undefined, /is 5\.2\.0; the face table pins 5\.3\.0/],
    [{ LICENSE: 'MIT License\n' }, () => undefined, /is not the OFL-1\.1/],
    [{ LICENSE: null }, () => undefined, /is not the OFL-1\.1/],
    [{}, (t) => { t.families.demo.licence = 'MIT'; }, /is not the MIT/],
    [{ 'unicode.json': JSON.stringify({ latin: 'U+0000-00FF' }) }, () => undefined, /unicode-range differs/],
    [{}, (t) => { t.faces[0].subset = 'latin-ext'; }, /unicode-range differs/],
    [{}, (t) => { t.faces = t.faces.slice(1); }, /lists no face of demo/],
    [{}, (t) => { t.faces[0].file = 'fonts/demo-latin-400-normal.woff2'; }, /is outside fonts\/dynamic\/demo/],
    [{}, (t) => { t.faces[0].file = 'fonts/dynamic/demo/../../x.woff2'; }, /is outside/],
    [{}, (t) => { t.faces[0].source = '@fontsource/other/files/x.woff2'; }, /is not a file of @fontsource\/demo/],
    [{ 'files/demo-latin-400-normal.woff2': null }, () => undefined, /missing .*demo-latin-400-normal\.woff2/],
  ];
  for (const [over, edit, error] of cases) {
    const t = tree(over);
    try {
      const faces = table();
      edit(faces);
      assert.throws(() => vendorFaces(faces, t.nm, t.out), error, String(error));
      assert.ok(!existsSync(join(t.out, 'fonts/dynamic/demo/LICENSE')) || error.source.includes('missing'), 'checked before copying');
    } finally {
      rmSync(t.root, { recursive: true, force: true });
    }
  }
});

test('the real table names only OFL faces in their own folders', () => {
  for (const [id, fam] of Object.entries(FACES.families)) {
    if (fam.css !== 'dynamic') continue;
    assert.equal(fam.licence, 'OFL-1.1', id);
    assert.equal(fam.dir, `fonts/dynamic/${id}`);
    for (const f of FACES.faces.filter((x) => x.family === id)) {
      assert.ok(f.file.startsWith(`${fam.dir}/`) && f.source.startsWith(`${fam.package}/files/`), f.file);
    }
  }
});
