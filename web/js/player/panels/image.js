// @ts-check
/**
 * Generated / uploaded illustration panel (same behaviour as a figure: part of the screenshot).
 */

import { createStillPanel } from './figure.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */

/** @type {PanelFactory} */
export function createImagePanel(body, rsp) {
  return createStillPanel(body, rsp, { missingText: 'The illustration has not been generated yet.' });
}
