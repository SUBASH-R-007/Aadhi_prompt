// User-facing messages for the B0 error codes (docs/API.md): a document saved by a newer build (409
// newer_document) and a Dynamic-video feature this server does not enable (422 feature_unavailable).
import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from '../../js/shared/api.js';
import { errorMessage } from '../../js/studio/errors.js';

test('newer_document asks for a server update instead of showing the schema detail', () => {
  const err = new ApiError(409, {
    code: 'newer_document',
    detail: 'This screenplay was saved by a newer version of Aadhi EduEngine (schema version 4); this server reads up to version 3. Update the server before opening it.',
    document: 'screenplay',
    schema_version: 4,
    reader_version: 3,
  }, null);
  const msg = errorMessage(err);
  assert.match(msg, /newer version of Aadhi EduEngine/);
  assert.match(msg, /administrator/);
  assert.doesNotMatch(msg, /schema/i);
});

test('feature_unavailable names the features in plain words, never the setting', () => {
  const err = new ApiError(422, {
    code: 'feature_unavailable',
    features: ['cuts', 'presentation'],
    detail: [
      { loc: ['body', 'screenplay', 'presentation'], msg: "Dynamic video styling: not available on this server yet (DYNAMIC_FEATURES does not include 'presentation').", type: 'feature_unavailable' },
      { loc: ['body', 'screenplay', 'scenes', 0, 'beats', 1, 'cuts'], msg: "Narration cuts: not available on this server yet (DYNAMIC_FEATURES does not include 'cuts').", type: 'feature_unavailable' },
    ],
  }, null);
  const msg = errorMessage(err);
  assert.match(msg, /Dynamic video styling; Narration cuts/);
  assert.doesNotMatch(msg, /DYNAMIC_FEATURES|screenplay\./);
  // without a usable detail the message still explains what happened
  const bare = errorMessage(new ApiError(422, { code: 'feature_unavailable', detail: 'x' }, null));
  assert.match(bare, /^This lecture uses Dynamic-video features this server does not offer yet\. /);
});
