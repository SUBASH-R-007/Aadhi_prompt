// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { segmentGraphemes, splitWords, splitLines, joinIndicConjuncts } from '../../js/motion/text.js';

/** Code points of a cluster, for readable failure messages. */
const cps = (s) => [...s].map((c) => `U+${c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}`).join(' ');

/** No cluster may start with a combining mark, a virama or a joiner (that would tear a syllable). */
function assertNoTornClusters(clusters, label) {
  for (const c of clusters) {
    assert.ok(!/^[\p{M}‌‍]/u.test(c), `${label}: cluster starts with a mark: ${cps(c)}`);
  }
}

describe('segmentGraphemes: Tamil', () => {
  test('vowel signs and the pulli stay on their consonant', () => {
    assert.deepEqual(segmentGraphemes('தமிழ்', 'ta-IN'), ['த', 'மி', 'ழ்']);
    assert.deepEqual(segmentGraphemes('கொ', 'ta-IN'), ['கொ']); // two-part vowel sign (U+0BCA)
    assert.deepEqual(segmentGraphemes('பள்ளிக்கூடம்', 'ta-IN'), ['ப', 'ள்', 'ளி', 'க்', 'கூ', 'ட', 'ம்']);
  });

  test('the kssa and shri ligatures stay whole', () => {
    assert.deepEqual(segmentGraphemes('க்ஷ', 'ta-IN'), ['க்ஷ']);
    assert.deepEqual(segmentGraphemes('ஸ்ரீ', 'ta-IN'), ['ஸ்ரீ']);
    assert.deepEqual(segmentGraphemes('லக்ஷ்மி', 'ta-IN'), ['ல', 'க்ஷ்', 'மி']);
  });
});

describe('segmentGraphemes: Devanagari', () => {
  test('conjuncts are one cluster', () => {
    assert.deepEqual(segmentGraphemes('हिन्दी', 'hi-IN'), ['हि', 'न्दी']);
    assert.deepEqual(segmentGraphemes('क्षत्रिय', 'hi-IN'), ['क्ष', 'त्रि', 'य']);
    assert.deepEqual(segmentGraphemes('स्त्री', 'hi-IN'), ['स्त्री']);
    assert.deepEqual(segmentGraphemes('विज्ञान', 'hi-IN'), ['वि', 'ज्ञा', 'न']);
  });

  test('ZWJ keeps a half form joined; ZWNJ breaks the conjunct', () => {
    assert.deepEqual(segmentGraphemes('क्‍ष', 'hi-IN'), ['क्‍ष']);
    assert.deepEqual(segmentGraphemes('क्‌ष', 'hi-IN'), ['क्‌', 'ष']);
  });

  test('nukta, candrabindu and visarga stay attached', () => {
    assert.deepEqual(segmentGraphemes('ज़रूरत', 'hi-IN'), ['ज़', 'रू', 'र', 'त']);
    assert.deepEqual(segmentGraphemes('हँसी दुःख', 'hi-IN'), ['हँ', 'सी', ' ', 'दुः', 'ख']);
  });
});

describe('segmentGraphemes: Telugu', () => {
  test('vowel signs and conjuncts stay whole', () => {
    assert.deepEqual(segmentGraphemes('తెలుగు', 'te-IN'), ['తె', 'లు', 'గు']);
    assert.deepEqual(segmentGraphemes('క్షేత్రం', 'te-IN'), ['క్షే', 'త్రం']);
    assert.deepEqual(segmentGraphemes('విద్యార్థి', 'te-IN'), ['వి', 'ద్యా', 'ర్థి']);
  });
});

describe('segmentGraphemes: other scripts and invariants', () => {
  test('Kannada conjuncts (not joined by Unicode 15.1) are joined', () => {
    assert.deepEqual(segmentGraphemes('ಕನ್ನಡ', 'kn-IN'), ['ಕ', 'ನ್ನ', 'ಡ']);
  });

  test('emoji sequences and combining accents are one cluster', () => {
    assert.deepEqual(segmentGraphemes('👨‍👩‍👧 é', 'en'), ['👨‍👩‍👧', ' ', 'é']);
    assert.deepEqual(segmentGraphemes('🇮🇳', 'en'), ['🇮🇳']);
  });

  test('join is lossless and nothing starts with a mark', () => {
    const samples = {
      ta: 'ஒளியின் வேகம் வினாடிக்கு 3 லட்சம் கி.மீ. க்ஷேத்திரம் ஸ்ரீ',
      hi: 'प्रकाश की गति ३ लाख किलोमीटर प्रति सेकंड है। क्षेत्र त्र ज्ञ श्र',
      te: 'కాంతి వేగం సెకనుకు 3 లక్షల కిలోమీటర్లు. క్ష్మ స్త్రీ',
      kn: 'ಬೆಳಕಿನ ವೇಗ ಸೆಕೆಂಡಿಗೆ ಮೂರು ಲಕ್ಷ ಕಿಲೋಮೀಟರ್',
      ml: 'പ്രകാശത്തിന്റെ വേഗത',
      bn: 'আলোর গতি সেকেন্ডে',
    };
    for (const [lang, text] of Object.entries(samples)) {
      const clusters = segmentGraphemes(text, lang);
      assert.equal(clusters.join(''), text, lang);
      assertNoTornClusters(clusters, lang);
    }
  });

  test('empty and nullish input', () => {
    assert.deepEqual(segmentGraphemes(''), []);
    assert.deepEqual(segmentGraphemes(/** @type {any} */ (null)), []);
  });

  test('an invalid locale tag falls back to the root rules', () => {
    assert.deepEqual(segmentGraphemes('தமிழ்', 'not a locale!'), ['த', 'மி', 'ழ்']);
  });
});

describe('joinIndicConjuncts: engines without Unicode 15.1 (older ICU, Safari)', () => {
  test('re-joins conjuncts that an older segmenter split', () => {
    assert.deepEqual(joinIndicConjuncts(['क्', 'ष', 'त्', 'रि', 'य']), ['क्ष', 'त्रि', 'य']);
    assert.deepEqual(joinIndicConjuncts(['स्', 'त्', 'री']), ['स्त्री']);
    assert.deepEqual(joinIndicConjuncts(['క్', 'షే', 'త్', 'రం']), ['క్షే', 'త్రం']);
    assert.deepEqual(joinIndicConjuncts(['க்', 'ஷ']), ['க்ஷ']);
  });

  test('leaves Tamil pulli clusters, ZWNJ, word ends and other scripts alone', () => {
    assert.deepEqual(joinIndicConjuncts(['ப', 'ள்', 'ளி']), ['ப', 'ள்', 'ளி']);
    assert.deepEqual(joinIndicConjuncts(['क्‌', 'ष']), ['क्‌', 'ष']);
    assert.deepEqual(joinIndicConjuncts(['क्', ' ', 'ष']), ['क्', ' ', 'ष']);
    assert.deepEqual(joinIndicConjuncts(['क्', 'క']), ['क्', 'క'], 'a virama never joins a consonant of another script');
    assert.deepEqual(joinIndicConjuncts(['्', 'क']), ['्', 'क'], 'a bare virama is not a conjunct');
  });
});

describe('splitWords', () => {
  const texts = (parts) => parts.map((p) => p.text);

  test('English: punctuation attached, whitespace kept for reassembly', () => {
    const s = 'Hello, world!  It’s (roughly) 3.14 m/s.';
    const parts = splitWords(s, 'en-IN');
    assert.deepEqual(texts(parts), ['Hello,', 'world!', 'It’s', '(roughly)', '3.14', 'm/s.']);
    assert.equal(parts[1].ws, '  ');
    assert.equal(parts.map((p) => p.text + p.ws).join(''), s);
    assert.equal(parts[2].index, s.indexOf('It’s'));
  });

  test('a no-break space glues a number to its unit', () => {
    assert.deepEqual(texts(splitWords('V = 10 V', 'en')), ['V', '=', '10 V']);
  });

  test('symbol-only units are not word-like', () => {
    const parts = splitWords('a — b = c', 'en');
    assert.deepEqual(parts.map((p) => p.wordLike), [true, false, true, false, true]);
  });

  test('Tamil, Hindi and Telugu sentences split at spaces, keeping full stops and dandas', () => {
    assert.deepEqual(texts(splitWords('நான் தமிழ் பேசுவேன்.', 'ta-IN')), ['நான்', 'தமிழ்', 'பேசுவேன்.']);
    assert.deepEqual(texts(splitWords('यह क्षत्रिय है।', 'hi-IN')), ['यह', 'क्षत्रिय', 'है।']);
    assert.deepEqual(texts(splitWords('నేను తెలుగు మాట్లాడతాను.', 'te-IN')), ['నేను', 'తెలుగు', 'మాట్లాడతాను.']);
  });

  test('scripts without spaces split at word boundaries', () => {
    const parts = splitWords('日本語のテキスト', 'ja');
    assert.ok(parts.length >= 2, JSON.stringify(parts));
    assert.equal(parts.map((p) => p.text).join(''), '日本語のテキスト');
  });

  test('leading whitespace is dropped, newlines stay in ws', () => {
    const parts = splitWords('  one\ntwo', 'en');
    assert.deepEqual(texts(parts), ['one', 'two']);
    assert.equal(parts[0].ws, '\n');
    assert.deepEqual(splitWords('', 'en'), []);
    assert.deepEqual(splitWords('   ', 'en'), []);
  });
});

describe('splitLines', () => {
  /** Monospace measure: 10 px per grapheme. */
  const mono = (s) => segmentGraphemes(s).length * 10;

  test('greedy breaking at word units', () => {
    assert.deepEqual(splitLines('The quick brown fox jumps over the lazy dog', 160, mono), [
      'The quick brown',
      'fox jumps over',
      'the lazy dog',
    ]);
  });

  test('balance evens the lines without changing their number', () => {
    const greedy = splitLines('Light travels three lakh kilometres every second', 300, mono);
    const balanced = splitLines('Light travels three lakh kilometres every second', 300, mono, { balance: true });
    assert.equal(balanced.length, greedy.length);
    const spread = (lines) => Math.max(...lines.map(mono)) - Math.min(...lines.map(mono));
    assert.ok(spread(balanced) <= spread(greedy), `${JSON.stringify(greedy)} vs ${JSON.stringify(balanced)}`);
    assert.equal(balanced.join(' '), greedy.join(' '));
  });

  test('hard line breaks are kept; lines carry no edge whitespace', () => {
    const lines = splitLines('one two\n\nthree   four', 1000, mono);
    assert.deepEqual(lines, ['one two', '', 'three   four']);
  });

  test('a word wider than the line breaks between graphemes, never inside an Indic cluster', () => {
    const lines = splitLines('क्षत्रियक्षत्रिय', 25, mono, { locale: 'hi-IN' });
    assert.deepEqual(lines, ['क्षत्रि', 'यक्ष', 'त्रिय']);
    assertNoTornClusters(lines, 'hi');
    const ta = splitLines('பள்ளிக்கூடம்', 30, mono, { locale: 'ta-IN' });
    assert.equal(ta.join(''), 'பள்ளிக்கூடம்');
    assertNoTornClusters(ta, 'ta');
  });

  test('every line fits whenever its words fit', () => {
    const text = 'ஒளியின் வேகம் வினாடிக்கு மூன்று லட்சம் கிலோமீட்டர் ஆகும்';
    for (const width of [60, 90, 120, 200]) {
      const lines = splitLines(text, width, mono, { locale: 'ta-IN' });
      for (const l of lines) assert.ok(mono(l) <= width || segmentGraphemes(l).length === 1, `${width}: ${l}`);
      assert.equal(lines.join(' ').replace(/\s+/g, ''), text.replace(/\s+/g, ''));
    }
  });
});
