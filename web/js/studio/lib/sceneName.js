// @ts-check
/**
 * How the Studio names the scene a render or preflight item is about (pure).
 *
 * A screenplay scene is "Scene N" by the editor's numbering: the server's `scene_number` (hidden scenes counted), else
 * - for an older server or a stored render warning without one - the timeline position (`scene_index` + 1).
 *
 * Aadhi's synthesised appearances in a Dynamic video (`aadhi/compose/dynamic/presenter.py`: the intro every Dynamic
 * video opens with, and the 16:9 recap card) are in the timeline but not in the editor, so the server gives them no
 * `scene_number`. They are named by what they are ("Aadhi's intro"), never by the timeline fallback, which would call
 * the intro "Scene 1", the teacher's first scene (step-5 review R5-3).
 */

/** The ids presenter.py gives a synthesised appearance (`_SYNTH_RE`: `aadhi-intro`, `aadhi-recap`, `aadhi-intro-2`). */
export const APPEARANCE_RE = /^aadhi-(intro|recap)(?:-[0-9]+)?$/;

/** presenter.py `APPEARANCE_NAMES`. */
export const APPEARANCE_NAMES = Object.freeze({ intro: "Aadhi's intro", recap: "Aadhi's recap" });

/**
 * The name of a synthesised Aadhi appearance, or null: an item with no editor number (no screenplay scene has its id)
 * whose id has the synthesised form.
 * @param {any} it
 * @returns {string | null}
 */
export function appearanceName(it) {
  if (!it || Number.isInteger(it.scene_number)) return null;
  const m = APPEARANCE_RE.exec(String(it.scene_id || ''));
  return m ? APPEARANCE_NAMES[/** @type {'intro' | 'recap'} */ (m[1])] : null;
}

/**
 * An item's scene number as the editor's scene list shows it: `scene_number`; else, for an item that is not a
 * synthesised appearance, the timeline position (older servers and stored render warnings); else null.
 * @param {any} it
 * @returns {number | null}
 */
export function sceneNumberOf(it) {
  if (!it) return null;
  if (Number.isInteger(it.scene_number)) return it.scene_number;
  if (appearanceName(it)) return null;
  return Number.isInteger(it.scene_index) ? it.scene_index + 1 : null;
}
