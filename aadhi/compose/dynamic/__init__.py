"""Dynamic video (motion engine) quality gates: preflight before capture, container checks after it.

Modules (DYNAMIC_VIDEO_DESIGN §7.3, §7.4, §7.12):

* ``colour_math`` the one colour module (§4.7): sRGB transfer, OKLab/OKLCH, the CSS Color 4 gamut mapping, hex with
                  ``Math.round``, WCAG luminance and contrast, compositing, OKLCH mixing, desaturation, Machado 2009
                  colour-vision simulation; a line-by-line port of ``web/js/motion/color.js`` (parity vectors in
                  ``tests/fixtures/colour_vectors.json``) plus B1's palette generator (``palette_hex``);
* ``themes``      the theme catalogue (content palettes, the 12 families, structure and derived scene colours,
                  personalities, caption styles, faces, cue colours) and its validator (R-52..R-56, R-53, §7.5);
* ``zones``       the one source of the safe zones (R-59), the §2.5 regions and 16:9 grid, the platform zones and their
                  intersection, the R-29 type floors and the layout tokens;
* ``templates``   the 41 TemplateSpecs of §4.3, the §4.2 semantic-class table, the §4.4 signal rows and ``fit``, the
                  visual-event target grammar, the colour roles and the background / transition registries;
* ``catalogue``   generates ``web/js/dynamic/catalogue.gen.js`` (the stage's frozen catalogue; ``--check`` in CI);
* ``colour``      CSS colour parsing (hex, ``rgb()``, ``hsl()``, ``oklch()``, ``oklab()``, ``color(srgb ...)``), alpha
                  compositing and the WCAG 2.x contrast ratio used by the analytic contrast check (R-53), on
                  ``colour_math``'s maths;
* ``stage_page``  a Playwright driver for the render page's stage contract v3 (``/render-dynamic``,
                  ``window.aadhiStage``): the same browser flags, network policy and token-in-fragment scheme as the
                  classic capture, the timeline the worker holds served to the page, CDP JPEG capture;
* ``preflight``   loads the stage once, seeks the sampled frames (24 spread frames, every scene start and end, and
                  every caption-chunk start in 9:16), reads ``textBoxes()`` and checks text overflow, safe zones and
                  contrast; plans the automatic remedies (shrink to the slot minimum, the template's safe variant, the
                  scrim), applies them to the timeline (``look.fixes`` only; the stage still reads B1's legacy
                  ``variant`` / ``text_fit`` for engine_version 1 timelines) and checks again; what is still wrong
                  blocks with a teacher action ("Shorten text") or stays a warning (contrast never blocks);
* ``container``   the post-render container checks (exact frame count per chunk and in total, first video PTS 0 and
                  the audio start within one frame, colour tags, frame grid and declared rate, caption track, sound)
                  with their automatic remedies (re-render the chunks, re-encode the audio, re-mux), and the
                  ``Render.options["qa"]`` document of a motion render;
* ``determinism`` frame hashes of a stage in any order (1x300, 3x100, reverse: §7.4 test (a)).

The classic (Aadhi presenter) renderer never imports this package.
"""
