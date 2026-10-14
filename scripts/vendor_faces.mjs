// @ts-check
// Vendoring of the Dynamic stage's B2b faces (DYNAMIC_VIDEO_DESIGN §4.6, §4.12; B2 plan §2 "Fonts"), called by
// scripts/vendor.mjs and unit-tested on its own (web/tests/tooling/vendorFaces.test.js).
//
// The one face table is aadhi/compose/dynamic/faces.py; its generated copy web/js/dynamic/faces.gen.js lists every
// face, and the ones whose family has css "dynamic" are copied into web/vendor/fonts/dynamic/<family>/, each family
// with its package's LICENSE. They are declared by the generated web/css/dynamic-fonts.css, never by
// web/vendor/fonts.css, and live in a sub-directory, so the classic burn-in's font copy (aadhi/compose/video.py
// _copy_fonts copies the files of web/vendor/fonts/, not its folders) and the classic fonts.css stay exactly as they
// were.
//
// Fail closed: a missing package, a package of another version than the table pins, a licence that is not the one the
// table records (OFL-1.1: the LICENSE must hold the OFL 1.1 text; no other licence is accepted for a face), a family
// with no face, a face outside its family's folder, or a face whose unicode-range differs from its package's
// unicode.json stops vendoring (the page would otherwise declare ranges its files do not have).
import { cpSync, existsSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

export const OFL_TEXT = 'SIL OPEN FONT LICENSE Version 1.1';

/**
 * Copy every B2b face (and each family's LICENSE) from `nodeModules` into `out` (web/vendor).
 * @param {any} faces        FACES of web/js/dynamic/faces.gen.js
 * @param {string} nodeModules
 * @param {string} out
 * @returns {string[]} the files written, relative to `out`, in the order written
 */
export function vendorFaces(faces, nodeModules, out) {
  /** @type {string[]} */
  const written = [];
  /** @param {string} from relative to nodeModules @param {string} to relative to out */
  const copy = (from, to) => {
    const src = join(nodeModules, from);
    if (!existsSync(src)) throw new Error(`missing ${src} — run npm ci`);
    const dst = join(out, to);
    mkdirSync(dirname(dst), { recursive: true });
    cpSync(src, dst);
    written.push(to);
  };
  for (const [id, fam] of Object.entries(faces.families)) {
    if (fam.css !== 'dynamic') continue;
    const pkgDir = join(nodeModules, ...String(fam.package).split('/'));
    if (!existsSync(join(pkgDir, 'package.json'))) throw new Error(`missing ${pkgDir} — run npm ci`);
    const version = JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf8')).version;
    if (version !== fam.version) throw new Error(`${fam.package} is ${version}; the face table pins ${fam.version}`);
    const licence = existsSync(join(pkgDir, 'LICENSE')) ? readFileSync(join(pkgDir, 'LICENSE'), 'utf8') : '';
    if (fam.licence !== 'OFL-1.1' || !licence.includes(OFL_TEXT)) {
      const why = `${fam.package}/LICENSE is not the ${fam.licence} the face table records (expected the OFL 1.1 text)`;
      throw new Error(why);
    }
    const ranges = JSON.parse(readFileSync(join(pkgDir, 'unicode.json'), 'utf8'));
    const rows = faces.faces.filter((/** @type {any} */ f) => f.family === id);
    if (!rows.length) throw new Error(`the face table lists no face of ${id}`);
    for (const face of rows) {
      if (ranges[face.subset] !== face.unicode_range.join(',')) {
        throw new Error(`${face.source}: unicode-range differs from ${fam.package}/unicode.json (${face.subset})`);
      }
      if (!face.file.startsWith(`${fam.dir}/`) || face.file.includes('..')) {
        throw new Error(`${face.file} is outside ${fam.dir}`);
      }
      if (!face.source.startsWith(`${fam.package}/files/`) || face.source.includes('..')) {
        throw new Error(`${face.source} is not a file of ${fam.package}`);
      }
    }
    copy(`${fam.package}/LICENSE`, `${fam.dir}/LICENSE`);
    for (const face of rows) copy(face.source, face.file);
  }
  return written;
}
