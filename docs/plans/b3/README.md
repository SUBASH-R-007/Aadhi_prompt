# Batch B3 working plan

This folder holds the engineering plan and records for Batch B3, the audio system of `docs/DYNAMIC_VIDEO_DESIGN.md`
(§5 and §10.3 B3). B3 covers:

- voice personas, casting and rotation history, and calibration;
- OpenAI TTS delivery as the primary voice, with Edge as the keyless fallback (owner instruction 2026-10-17);
- procedural and opt-in AI music, the mix and its loudness preflight, and R-49 sound effects;
- live-player audio, keys and money.

B3 is built in its own worktree and branch (`C:/t/b3`, `b3-audio`), in parallel with the rest of B2. The binding plan
is `b3_plan.md`. It is laid out like `../b2/b2_plan_v2.md`:

- sequence §A (B3-0 .. B3k, landed in groups L0..L6);
- spike policy §P;
- frozen contracts §1;
- rules §2;
- classic-invariance and determinism safeguards §3;
- branch and merge strategy §4;
- owner decisions with defaults §5;
- gates §G;
- deviation register §D;
- critique answered §C (revision 2).

Step workflow scripts, review records, the spike results and the acceptance record (listening checklist and blind
test) are added here as each step runs.
