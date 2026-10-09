"""Source scoping: keep only the teachable content of an uploaded source.

``scope_source`` runs on the extracted Markdown of every source (``ingest``), after PDF/DOCX
extraction and before truncation and chunking. It is deterministic and conservative: only material
that is clearly not teaching content is taken out, and every removal is recorded.

* **Document metadata** (subject / unit / session lines and header-table rows) moves to
  ``SourceMeta``; it only fills title cards the teacher left empty. A title-page line that combines
  them ("Basic Electrical Engineering - Unit 1: Electric Circuits - Session 2", "Subject: X | Unit 2 |
  Session 3", "Course: X, Module 4, Lecture 2") is parsed by ``parse_title_line`` in the front matter
  or right under the document's title, in every source format, and removed from the content. Administrative fragments inside
  those lines and inside clip / segment titles ("(Duration: 15 min)", "(Video No: 07)", "| Dr. X",
  a course code or regulation) are cut out and recorded.
* **Administrative data** - people (SME, author, reviewer, designation, department, contact
  details), video durations and word counts, dates, versions, course codes, semesters, clip
  numbers, "page x of y", copyright boilerplate - is removed and recorded as ``ExcludedItem``.
  Keys are matched by family ("Name of the Resource Person", "Video Duration (approx.)", "Course
  Code & Name", "Reviewed and approved by"), and every key checks its value: a person key needs a
  person's name, a duration key a video length ("Duration: 2 ms" and "Estimated duration: 4 hours"
  are subject matter), a code key a code. Only unambiguous keys count anywhere in the document;
  the others count only in a header context: the front matter (before the first content, a title
  heading included), YAML front matter, a metadata table (row-wise, an optional serial-number
  column and a "Particulars | Details" header row allowed; column-wise only with one data row in
  the front matter), a sign-off at the end, or a block that also holds an unambiguous key. A block
  that follows "for example:" or whose values read as definitions is content.
* **Timecodes** ("[0:10 - 1:15]", en-dash and mojibake variants) are stripped from SME headings and
  labels; in narration only bracketed ranges are. Notes headings lose a timecode only when several
  headings carry square-bracketed ranges or a chain of increasing video timings.
* **SME video scripts** (``source_format == "sme_script"``: production markers such as "CLIP n
  SCRIPT", "Aadhi speaks:", BOARD / ANIMATION labels, bracketed timecode ranges, or an AV script
  table) become one continuous lecture: "CLIP n SCRIPT - X" keeps only the title X, "SEGMENT/SCENE
  n - X" becomes a heading X, per-clip title cards, recaps at the start of a later clip, repeated
  "what this video will cover" blocks, "bridge to next part" / "what's next" segments and sign-offs
  are dropped (AV-table rows the same way), "SOURCE IMAGE ...: caption" becomes the figure's
  caption, narrator and board labels are dropped so their content reads as ordinary text, and the
  author's ANIMATION / VISUAL / ON SCREEN directions move to ``visual_notes_raw`` (visual ideas that
  are never narrated). Editing notes, stage directions and recording remarks that name the author
  are removed, and so are camera and editing labels (CAMERA, CUT TO, LOWER THIRD, NOTE TO EDITOR,
  B-ROLL, SFX, PRODUCTION NOTE). Inside the narration (``narration``), sentences that state the
  video's length, sign-offs and greetings ("Don't forget to like and subscribe!"), and pointers to
  other videos of a series are dropped; "In this video, we will learn X" becomes "We will learn X"
  and "as we saw in Clip 1" becomes "as we saw earlier". A board line that repeats a line of the
  same section word for word is dropped (``_drop_board_repeats``).
* **The authors' names in the text** (every format): once the names in the person fields are known,
  a sentence that is a presenter cue or self-reference naming them ("Dr. X will now demonstrate
  this", "Hello, I am X") is dropped and any other mention of the full name, or of an honorific with
  part of it, becomes "the instructor" (``scrub_people``). A first name or surname alone only counts
  in a presenter cue: elsewhere it may be a scientist the lesson is about.

Prose sentences, formulas, examples, quiz questions and summaries are never removed: a sentence
that merely contains "duration", a date, a time or a person's name stays.

**Fenced code blocks** (``codeblocks``) are one unit that is always kept verbatim: no line inside a
fence is read as a heading, label, admin field or timecode ("# comment", "Duration = 5" and
"Prepared by: x" in code are code), only an excluded author's spaced name in it is replaced by "the
instructor" (nothing is dropped or re-laid out; an identifier such as ``DrKumar`` stays), and its comments
and blank lines stay.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ..schemas.screenplay import SourceFigure
from .base import ExcludedCategory, ExcludedItem, SourceChunk, SourceMeta, VisualNote
from .codeblocks import closes_fence, fence_open, outside_fences

SourceFormat = Literal["sme_script", "notes", "textbook", "unknown"]

MAX_EXCLUDED = 400  # IngestResult.excluded allows 500
MAX_VISUAL_NOTES = 500
VISUAL_NOTE_CHARS = 1500
_MAX_VISUAL_PARAS = 4  # paragraphs captured after a bare "DETAILED ANIMATION:" label (SME scripts only)
# after the first captured paragraph, further ones must read like visual directions
_VISUAL_WORDS = re.compile(
    r"\b(?:show|shows|animate|animates|animation|display|displays|reveal|reveals|highlight|highlights|fade|fades|zoom"
    r"|camera|appear|appears|draw|draws|slide|slides|glow|glows|pulse|pulses|move|moves|place|places|frame|screen"
    r"|transition|dissolve|icon|icons|overlay|visual|visible)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------

_DASH = r"(?:-|–|—|‒|−|â€“|â€”|â€’|to)"
_TC = r"\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?"
_TC_RANGE = rf"{_TC}\s*{_DASH}\s*{_TC}"
_TIMECODE = re.compile(rf"\s*[\[(]\s*{_TC}\s*(?:{_DASH}\s*{_TC}\s*)?[\])]")
_TIMECODE_SQUARE = re.compile(rf"\s*\[\s*{_TC_RANGE}\s*\]")  # the only form stripped from narration
_TIMECODE_BARE = re.compile(rf"\s+{_TC_RANGE}\s*$")
_DURATION_TAG = re.compile(
    r"\s*[\[(]\s*(?:(?:approx(?:imately|\.)?|about|duration|time|length|run\s*time|running\s+time|est(?:imated|\.)?)"
    r"\s*[:\-–]?\s*)?(?:~\s*)?\d+(?:\.\d+)?\s*(?:(?:-|–|—|to)\s*\d+(?:\.\d+)?\s*)?(?:s|secs?|seconds?|mins?|minutes?)\.?\s*[\])]",
    re.I,
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_PAGE = re.compile(r"^\s*<!--\s*page\s+\d+\s*-->\s*$")
_HTML_COMMENT = re.compile(r"<!--(?!\s*page\s+\d+\s*-->)(.*?)-->", re.S | re.I)
_FIGURE = re.compile(r"^\s*\[Figure ([A-Za-z0-9\-]+)(?::\s*(.*?))?\]\s*$")
_LIST = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_PLACEHOLDER_CELL = re.compile(r"^Col\d+$")
_YAML_KEY = re.compile(r"^\s*[A-Za-z_][\w \-]{0,40}:")

# "Key : value" / "Key - value" (the key is matched against the known key families below)
_KV_RE = re.compile(r"^(?P<key>[^\W\d_][\w .&/()'’#,\-]{0,58}?)\s*(?::|：|\s[-–—]\s)\s*(?P<value>.*)$")
# "Prepared by Dr. X", "Approved by<TAB>Dr. Y", "Total running time 15 minutes" (header contexts only)
_KV_LOOSE = re.compile(
    r"^(?P<key>(?:[^\W\d_][\w .&/'’\-]{0,48}?\s)?(?:by|duration|running\s+time|run\s*time|runtime|word\s+count))"
    r"(?:\t+|\s+)(?P<value>\S.*)$",
    re.I,
)

# SME script structure (matched on the plain text of a line or heading)
_CLIP = re.compile(r"^(?:video\s+)?(?:clip|part|video)\s*(?:no\.?\s*)?(?P<n>\d+[a-z]?)\s*(?:script)?\s*"
                   r"(?:[-–—:.]\s*(?P<title>.*))?$", re.I)
_SEGMENT = re.compile(r"^(?:segment|scene|shot|sequence)\s*(?:no\.?\s*)?(?P<n>\d+[a-z]?)\s*"
                      r"(?:[-–—:.]\s*(?P<title>.*))?$", re.I)
_SESSION_LINE = re.compile(r"^(?:session|lecture)\s*(?:no\.?\s*)?(?P<n>\d+[a-z]?)\s*"
                           r"(?:[-–—:.]\s*(?P<title>.+))?$", re.I)
_UNIT_LINE = re.compile(r"^(?:unit|module)\s*(?:no\.?\s*)?(?P<n>\d+|[ivx]+)\s*(?:[-–—:.]\s*(?P<title>.+))?$", re.I)
_TITLE_CARD = re.compile(
    r"^(?:(?:opening|intro|end|outro|closing|main|video|session|lecture)\s+)?title\s*(?:card|screen|slide|animation|sequence)\b"
    r"|^(?:intro|end|outro|closing)\s+(?:card|screen|slide)\b|^(?:opening|intro)\s+(?:sequence|animation|titles?)\b"
    r"|^logo\s+(?:animation|reveal|sting|card)\b|^(?:channel|college|institution)\s+logo\b",
    re.I,
)
_TITLE_ONLY = re.compile(r"^(?:opening\s+)?titles?$", re.I)  # "SCENE 1 - TITLE" (segment titles only)
_INTRO_ONLY = re.compile(r"^intro(?:duction)?$", re.I)  # a title card when its body is only a title line
# whole-title bridges between clips ("TRANSITION TO THE NEXT STATE" and "NEXT PART OF THE CYCLE" are content)
_BRIDGE_TITLE = re.compile(
    r"^(?:bridge(?:\s+to\s+(?:the\s+)?next(?:\s+(?:part|clip|video|segment|session|lesson|topic|concept))?)?"
    r"|coming\s+up(?:\s+next)?|up\s+next|what'?s\s+next|what\s+comes\s+next|looking\s+ahead"
    r"|teaser(?:\s+for\s+(?:the\s+)?next\s+(?:part|clip|video|session|lesson))?"
    r"|next\s+(?:part|clip|video|episode)|preview\s+of\s+(?:the\s+)?next\s+(?:part|clip|video|session|lesson)"
    r"|(?:transition|connecting|connection|link|linking|leading)\s+(?:in)?to\s+(?:the\s+)?next"
    r"\s+(?:part|clip|video|segment|concept|topic|lesson|session))[\s:.!?]*$",
    re.I,
)
_TRANSITION_ONLY = re.compile(r"^transition[\s:.!?]*$", re.I)  # a bridge only as the last segment of a clip
_BRIDGE_ROW = re.compile(r"^(?:coming\s+up(?:\s+next)?|up\s+next|next\s+(?:video|clip|part|episode)|bridge|teaser"
                         r"|preview\s+of\s+(?:the\s+)?next|what'?s\s+next)\b", re.I)
_BRIDGE_SENTENCE = re.compile(
    r"^(?:in|for|during)\s+the\s+(?:next|upcoming|coming)\s+(?:video|clip|part|session|lesson|episode)\b(?!\s+of\b)"
    r"|^(?:coming\s+up\s+next|up\s+next)\b|^see\s+you\s+in\s+the\s+next\b", re.I)
# the whole segment title must be an objectives label ("Objectives of a compiler", "Outcomes" are content)
_OBJECTIVES = re.compile(
    r"^(?:what\s+(?:this|the|today'?s)\s+(?:video|clip|part|session|lesson|lecture|module)\s+(?:will\s+)?covers?"
    r"|what\s+(?:you|we)\s+will\s+(?:learn|cover)|(?:learning\s+)?objectives?|learning\s+outcomes?"
    r"|(?:session|course|lesson|clip|video|lecture)\s+(?:objectives?|outcomes?)|in\s+this\s+(?:video|clip|session|lesson))"
    r"(?:\s+(?:of|for|in)\s+(?:this|the|today'?s)\s+(?:video|clip|part|session|lesson|lecture))?\s*[:.!?]*\s*$",
    re.I,
)
_OUTRO = re.compile(r"^(?:outro|sign[\s-]?off|end\s+of\s+(?:the\s+)?(?:clip|video|part|segment)|thank\s+you(?:\s+for\s+watching)?"
                    r"|thanks\s+for\s+watching|end\s+(?:card|credits|screen)|farewell|goodbye)[\s.!]*$", re.I)
# dropped only when the segment teaches nothing (a "wrap up" may hold a real summary)
_OUTRO_SOFT = re.compile(r"^(?:closing(?:\s+(?:remarks|notes|thoughts|words))?|wrap(?:ping)?[\s-]*up"
                         r"|conclusion\s+and\s+thank\s+you)[\s.!]*$", re.I)
# a recap at the start of a later clip ("WELCOME BACK", "RECAP OF PREVIOUS CLIP")
_RECAP = re.compile(
    r"^(?:(?:a\s+)?(?:quick\s+|brief\s+|short\s+)?recap(?:\s+of\s+(?:the\s+)?(?:previous|last|earlier|first)"
    r"\s+(?:clip|video|part|segment|session|lesson)|\s+of\s+(?:clip|part|video)\s*\d+)?|previously(?:\s+on\b.*)?"
    r"|welcome\s+back|what\s+we\s+(?:learned|learnt|saw|covered|discussed)(?:\s+(?:in|so\s+far)\b.*)?"
    r"|review\s+of\s+(?:the\s+)?(?:previous|last)\s+(?:clip|video|part))[\s:.!?]*$",
    re.I,
)
_SIGN_OFF_SENTENCE = re.compile(
    r"^(?:thank\s+you(?:\s+(?:for\s+watching|all|everyone|so\s+much|for\s+listening))?|thanks\s+for\s+(?:watching|listening)"
    r"|see\s+you\s+(?:(?:all\s+)?in\s+the\s+next\s+(?:video|clip|part|session|class|lecture)|next\s+time|soon)"
    r"|happy\s+learning|bye(?:\s+for\s+now)?|goodbye"
    r"|that(?:'s|\s+is)\s+all\s+for\s+(?:this|today'?s?|now)(?:\s+(?:video|clip|part|session))?"
    # "Don't forget to like and subscribe!", "Please like, share and subscribe to the channel."
    r"|(?:(?:and\s+)?(?:please\s+)?(?:do\s+)?(?:don'?t|do\s+not)\s+forget\s+to\s+|please\s+)?(?:like|share|subscribe|comment)"
    r"(?:\s*(?:,|and|&)\s*(?:like|share|subscribe|comment|hit\s+the\s+bell(?:\s+icon)?|press\s+the\s+bell(?:\s+icon)?))*"
    r"(?:\s+(?:to|for|on)\s+(?:this|our|the|my)\s+(?:channel|video|videos|series))?"
    r"|(?:i\s+|we\s+)?hope\s+you\s+(?:enjoyed|liked|loved|found\s+\w+\s+(?:useful|helpful))(?:\s+(?:this|the|today'?s)"
    r"\s+(?:video|clip|session|lecture|lesson|class|part|episode))?"
    r"|(?:hi|hello|hey|welcome(?:\s+back)?|greetings|good\s+(?:morning|afternoon|evening))(?:\s*,?\s*(?:to\s+)?(?:everyone|"
    r"everybody|all|students|friends|learners|guys|folks|there|dear\s+(?:students|learners|friends)|my\s+dear\s+students))?)"
    r"[\s.!]*$",
    re.I,
)
# channel talk anywhere in a sentence ("... so like, share and subscribe!", "hit the bell icon")
_CHANNEL_TALK = re.compile(r"\b(?:like|share)\b[\s,&]*(?:and\s+)?(?:share\b[\s,&]*(?:and\s+)?)?subscribe\b"
                           r"|\bsubscribe\s+to\s+(?:our|my|the|this)\s+(?:youtube\s+)?channel\b|\b(?:hit|press)\s+the\s+bell\b",
                           re.I)
# narration that states the length of the video ("This video is about 7 minutes long, so grab a notebook.")
_LENGTH = r"~?\s*\d+(?:\.\d+)?\s*(?:-\s*|\s+)?(?:min(?:ute)?s?|sec(?:ond)?s?|hours?|hrs?)\b"
# the video itself (never "segment", "part" or "class": a signal segment or a timetable's class is subject matter)
_PACKAGE = r"(?:video|clip|session|lecture|lesson|episode|recording)"
_DURATION_SENTENCE = re.compile(
    rf"^(?:(?:this|today'?s|our)\s+(?:\w+\s+)?{_PACKAGE}|the\s+(?:whole\s+|entire\s+)?(?:video|clip|episode|recording))"
    rf"\s+(?:is|runs?|lasts?|will\s+(?:run|last|take|be)|takes?|should\s+(?:run|last|take))\s+(?:for\s+)?(?:only\s+|just\s+)?"
    rf"(?:about|around|approximately|approx\.?|roughly|nearly|under|over|less\s+than|more\s+than|close\s+to|up\s+to)?\s*"
    rf"{_LENGTH}"
    rf"|^(?:the\s+)?(?:total\s+)?(?:duration|length|running\s+time|run\s*time)\s+of\s+(?:this|the)\s+{_PACKAGE}\s+is\b",
    re.I,
)
# "In this 10-minute video, we will ..." -> "In this video, we will ..." (then the lead-in goes)
_LENGTH_IN_PHRASE = re.compile(rf"\b(this|the\s+next|the\s+following)\s+{_LENGTH}\s*(?:-\s*)?(?:long\s+)?(?={_PACKAGE}\b)", re.I)
# "In this video, we will learn X" -> "We will learn X"
_LEAD_IN_PHRASE = re.compile(
    r"^(?:so\s*,?\s*)?(?:in|through|with)\s+(?:this|today'?s)\s+(?:short\s+)?(?:video|clip|segment|part|session|lesson|lecture"
    r"|class|episode|module)\s*,?\s*(?=(?:we|you|i|let'?s|let\s+us)\b)", re.I)
# pointers to other videos of a series ("Welcome to video 2 of the network analysis series.")
_SERIES_SENTENCE = re.compile(  # "part 2 of the proof" is content: a part, lecture or session needs "series"
    r"\b(?:video|clip|episode)\s+(?:no\.?\s*|number\s+)?\d+[a-z]?\s+(?:of|in)\s+(?:the|this|our|a)\b"
    r"|\b(?:part|lecture|session)\s+\d+\s+(?:of|in)\s+(?:the|this|our|a)\s+(?:[\w'’-]+\s+){0,6}?"
    r"(?:series|playlist)(?:\s+(?:on|about)\b|[\s.,!?]*$)"
    r"|\b(?:first|second|third|fourth|fifth|next|last|final|\d+(?:st|nd|rd|th))\s+(?:video|clip|episode|part)\s+(?:of|in)"
    r"\s+(?:the|this|our|a)\s+(?:[\w'’-]+\s+){0,6}?(?:series|playlist)(?:\s+(?:on|about)\b|[\s.,!?]*$)"
    r"|^(?:hi|hello|hey|welcome)\b.{0,40}\bwelcome\s+to\s+(?:the\s+)?(?:video|clip|episode)\b"
    r"|^welcome\s+to\s+(?:the\s+)?(?:video|clip|episode)\s+\d+",
    re.I,
)
# "As we saw in Clip 1, ..." / "in the previous video" -> "earlier" ("in part 2", "in the first part of the
# experiment" are content)
_CLIP_POINTER = re.compile(
    r"\b(?:in|from|during)\s+(?:the\s+)?(?:clip|video|episode)\s+(?:no\.?\s*)?\d+[a-z]?\b"
    r"|\b(?:in|from|during)\s+(?:the|our)\s+(?:previous|last|earlier|first|preceding)\s+(?:clip|video|episode|session"
    r"|lecture|lesson)\b(?!\s+of\b)",
    re.I,
)

# labels inside SME scripts
_NARRATOR = re.compile(r"^(?:aadhi|narrator|narration|voice[\s-]?over|v\.?\s?o\.?|host|presenter|anchor|mascot)$", re.I)
_SPEAKER = re.compile(
    r"^(?:(?P<who>[A-Za-z][\w'’.]{0,20}(?:\s+[A-Za-z][\w'’.]{0,20})?)\s+(?:speaks|says|narrates|explains|asks)"
    r"|(?P<label>narration|narrator|voice[\s-]?over|v\.?o\.?|dialogue))\s*(?:\([^)]{0,60}\))?\s*:\s*(?P<rest>.*)$",
    re.I,
)
_BOARD_TITLE = re.compile(r"^board\s+(?:title|heading)\s*[:\-–—]\s*(?P<rest>.*)$", re.I)
_BOARD_POINTS = re.compile(
    r"^board(?:\s+(?:displays?|shows?|content|points?|text|items?|writes?)\b[^:]{0,60})?\s*:\s*(?P<rest>.*)$", re.I)
_VISUAL_LABEL = re.compile(
    r"^(?P<label>(?:detailed\s+)?(?:animations?|visuals?)(?:\s*[/&]\s*(?:animations?|visuals?|graphics?))?"
    r"|motion\s+graphics?|graphics?|shots?|image\s+prompt|stock\s+footage|footage"
    r"|on[\s-]?screen(?:\s+(?:text|visuals?|graphics?))?)"
    r"(?:\s*\([^)]{0,60}\)|\s+[-–—]\s+[^:]{0,80}|\s+for\s+[^:]{0,60}"
    r"|\s+(?:directions?|description|cues?|ideas?|notes?|suggestions?)){0,2}\s*:\s*(?P<rest>.*)$",
    re.I,
)
# visual labels that need no capitals in an SME script (GRAPHICS / FOOTAGE / SHOT must be written as labels)
_VISUAL_ANY_CASE = re.compile(r"^(?:detailed\s+)?(?:animations?|visuals?)\b|^on[\s-]?screen\b"
                              r"|^image\s+prompt\b|^stock\s+footage\b", re.I)
_PRODUCTION = re.compile(
    r"^(?P<label>sfx|sound\s+effects?|music|bgm|background\s+music|editor(?:'s|’s)?\s+notes?|editing\s+notes?"
    r"|production\s+notes?|director(?:'s|’s)?\s+notes?|recording\s+notes?|camera\s+notes?|editor\s+notes?"
    r"|notes?\s+(?:to|for)\s+(?:the\s+)?(?:video\s+)?(?:editor|editing\s+team|animators?|camera\s*(?:man|person|crew)?|crew)"
    r"|(?:aadhi|mascot)\s+(?:actions?|gestures?|expressions?|positions?|poses?|cue))\b[^:]{0,60}:\s*(?P<rest>.*)$",
    re.I,
)
# camera and editing directions that count only as ALL-CAPS labels ("CAMERA: zoom in", "CUT TO: close-up", "B-ROLL: ...";
# "Camera: a device that records images" is content)
_PRODUCTION_CAPS = re.compile(
    r"^(?P<label>camera(?:\s+(?:angle|move|movement|shot|direction|cue))?|cut\s+to|lower[\s-]+thirds?|b[\s-]?roll"
    r"|super(?:impose)?|(?:close|wide|medium)[\s-]+(?:up|shot)|zoom(?:\s+(?:in|out))?|vfx)\s*:\s*(?P<rest>.*)$",
    re.I,
)


def production_label(p: str) -> re.Match[str] | None:
    """A recording / editing direction label in an SME script (never a visual suggestion)."""
    m = _PRODUCTION.match(p)
    if m is not None and (not m.group("label").lower().startswith("music") or _caps(m.group("label"))):
        return m
    m = _PRODUCTION_CAPS.match(p)
    return m if m is not None and _caps(m.group("label")) else None
_NOTE = re.compile(r"^(?:note|notes|n\.\s*b\.?)\s*:\s*(?P<rest>.*)$", re.I)
_NOTE_PRODUCTION = re.compile(
    r"\b(?:fades?\s+(?:in|out|to)|faded\s+(?:in|out)|farewell|goodbye|cut\s+to|sfx|voice[\s-]?over|aadhi|mascot"
    r"|title\s+card|lower\s+third|b-roll|black\s+screen|on[\s-]screen)\b",
    re.I,
)
# recording / editing talk in a NOTE or a stage direction
_RECORDING = re.compile(
    r"\b(?:will\s+(?:re-?)?record|to\s+be\s+(?:re-?)?recorded|(?:re-?)?record(?:ing)?\s+(?:this|the)\s+(?:segment|clip|video|part|scene)"
    r"|recording\s+(?:session|studio|date|schedule)|re-?takes?|re-?shoot|shoot(?:ing)?\s+(?:this|the)\s+(?:segment|clip|video|scene|part)"
    r"|this\s+(?:segment|clip|video)|(?:slide|template)\s+(?:no\.?\s*)?\d+|(?:use|insert|add)\s+the\s+(?:slide|template)"
    r"|video\s+editor|editor\s+(?:should|must|will|to|please)|(?:for|to)\s+the\s+editor|studio)\b",
    re.I,
)
_SELF_INTRO = re.compile(r"\b(?:i\s+am|i'm|my\s+name\s+is|this\s+is|presented\s+by|brought\s+to\s+you\s+by|thanks?\s+to"
                         r"|thank\s+you|your\s+(?:faculty|instructor|teacher|host))\b", re.I)
_STAGE_DIRECTION = re.compile(r"^[\[(][^\])]{1,200}[\])]$")
_STAGE_WORDS = re.compile(
    r"\b(?:aadhi|narrator|mascot|presenter|host|pauses?|smiles?|gestures?|nods?|waves?|winks?|beat"
    r"|fades?\s+(?:in|out|to)|cut\s+to|zooms?\s+(?:in|out)|pans?\s+(?:to|left|right|across)|dissolves?|sfx|b-roll"
    r"|title\s+card|lower\s+third|black\s+screen|on[\s-]screen|music\s+(?:in|out|up|down|fades?|plays?|swells?|cue)"
    r"|camera\s+(?:pans?|zooms?|cuts?|moves?|tracks?|pushes?|pulls?))\b",
    re.I,
)
_SOURCE_IMAGE = re.compile(
    r"^(?P<label>source\s+image|image\s+from\s+(?:the\s+)?source|preserved\s+image|insert\s+image|image\s+placeholder)"
    r"\b[^:]{0,120}:\s*(?P<cap>.*)$",
    re.I,
)
_SOURCE_IMAGE_ANY_CASE = re.compile(r"^(?:source\s+image|image\s+from\s+(?:the\s+)?source|preserved\s+image)$", re.I)
_DOC_LABEL = re.compile(
    r"^(?:(?:video|lecture|lesson|clip|e-?content|sme|session)(?:\s+(?:video|lecture|lesson|clip|content))*\s+"
    r"(?:script|storyboard|template|scripting\s+template|script\s+template)|storyboard|video\s+script\s+template)$",
    re.I,
)
_PAGE_OF = re.compile(r"^(?:page|pg\.?|p\.)\s*\d+\s*(?:of|/)\s*\d+$", re.I)
_CONFIDENTIAL = re.compile(
    r"^(?:strictly\s+)?(?:confidential|private\s+and\s+confidential|for\s+internal\s+use\s+only|internal\s+use\s+only"
    r"|not\s+for\s+distribution|do\s+not\s+distribute|draft\s+copy)[.!]?$",
    re.I,
)
_COPYRIGHT_LINE = re.compile(
    r"^(?:©|\(c\)|copyright)\s*(?:©|\(c\))?\s*(?:19|20)\d{2}(?:\s*[-–]\s*(?:19|20)?\d{2})?\s*[,.]?\s*"
    r"(?P<owner>[^.]{0,80}?)\s*[.,]?\s*(?:all\s+rights\s+reserved\.?)?$",
    re.I,
)
_ALL_RIGHTS = re.compile(r"^all\s+rights\s+reserved\.?$", re.I)
_OWNER_SMALL = frozenset("of and the & for pvt ltd inc co llp limited".split())
_BY_FRAGMENT = re.compile(
    r"^(?P<key>(?:prepared|reviewed|approved|developed|created|compiled|written|scripted|presented)"
    r"(?:\s+(?:and|&)\s+\w+)?\s+by)\s*[:\-]?\s*(?P<name>.+)$",
    re.I,
)
# "This script was prepared by Dr. X for the e-content cell."
_CREDIT = re.compile(
    r"^(?:this|the)\s+(?:(?:video|lecture|e-?content|session)\s+)?(?:script|video|lecture|document|e-?content|content"
    r"|material|module|presentation|notes?|lesson|session|storyboard|clip)\s+(?:was|is|has\s+been|have\s+been|were|are"
    r"|had\s+been)\s+(?:(?:jointly|carefully|kindly)\s+)?(?:prepared|developed|written|created|reviewed|scripted|recorded"
    r"|presented|compiled|designed|narrated|approved|edited|authored|vetted)(?:\s+and\s+\w+)?\s+by\s+"
    r"(?P<name>[^,;]+?)(?=\s+(?:for|at|of|in|from|under|with)\b|[,;]|\.?$)",
    re.I,
)
_NUMBERED_HEADING = re.compile(r"^#{1,6}\s+(?:chapter\s+\d+|\d+(?:\.\d+)+\.?\s)", re.I)
_EXAMPLE_CUE = re.compile(r"\b(?:for\s+example|for\s+instance|e\.g\.|such\s+as|sample\s+(?:record|tuple|row|data))\b"
                          r"|\bexamples?\s*:\s*$", re.I)
_EXAMPLE_HEADING = re.compile(r"^(?:worked\s+)?examples?\b|^sample\b", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# value shapes
_DATE_VALUE = re.compile(
    r"\b\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}\b|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+\d{2,4}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{2,4}\b|^\s*\d{4}\s*$",
    re.I,
)
_VERSION_VALUE = re.compile(r"^(?:v(?:er(?:sion)?)?\.?\s*)?\d+(?:\.\d+)*[a-z]?(?:\s*\((?:draft|final)\))?$"
                            r"|^(?:draft|final|reviewed|approved|revised)(?:\s+(?:v\.?\s*)?\d+(?:\.\d+)*)?$", re.I)
_CODE_VALUE = re.compile(r"^(?=[A-Za-z0-9\-/.]*\d)[A-Z0-9][A-Za-z0-9\-/.]{2,24}(?:\s*(?:&|and|/|-|–|:)\s*.{0,100})?$")
_NUMBER_VALUE = re.compile(r"^#?\s*\d{1,4}[A-Za-z]?$")
_REGULATION_VALUE = re.compile(r"^(?:R\s*-?\s*)?(?:19|20)\d{2}$|^R\s*-?\s*\d{2,4}$", re.I)
_TERM_VALUE = re.compile(
    r"^(?:[IVX]{1,4}|\d{1,2}(?:st|nd|rd|th)?|(?:19|20)\d{2}(?:\s*[-–/]\s*\d{2,4})?)"
    r"(?:\s*(?:/|&|and|-|–)\s*(?:[IVX]{1,4}|\d{1,2}(?:st|nd|rd|th)?))?(?:\s+(?:year|yr|sem(?:ester)?))?$"
    r"|^\d(?:\s*[-/ ]\s*\d){2,3}$",
    re.I,
)
_VIDEO_LENGTH = re.compile(
    r"^(?:approx(?:imately|\.)?\s*|about\s+|around\s+|nearly\s+|~\s*)?(?P<n>\d{1,4}(?:\.\d+)?)"
    r"(?:\s*(?:-|–|—|to)\s*\d{1,4}(?:\.\d+)?)?\s*(?P<u>s|sec|secs|seconds?|mins?|minutes?)\.?"
    r"(?:\s*(?:and\s+)?\d{1,2}\s*(?:s|secs?|seconds?)\.?)?\s*(?:\((?:approx\.?|approximately)\))?$",
    re.I,
)
_CLOCK = re.compile(rf"^(?:\d{{1,2}}:)?\d{{1,2}}:\d{{2}}(?:\s*(?:mins?|minutes?))?$|^{_TC_RANGE}$", re.I)
_WORDS_VALUE = re.compile(r"^(?:approx\.?\s*|about\s+|~\s*)?\d[\d,]*\s*(?:words?)?$", re.I)
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE = re.compile(r"^\+?[\d\s\-().]{7,22}$")
_ARTICLE_START = re.compile(r"^(?:a|an|the)\s+\w+", re.I)
_VERBISH = re.compile(
    r"\b(?:is|are|was|were|be|been|means|refers?|denotes?|has|have|had|can|could|will|would|should|may|must|does|do|did"
    r"|consists?|contains?|uses?|used|gives?|converts?|stores?|takes?|makes?|allows?|sent|sends|carries|carry|touch|touches)\b",
    re.I,
)
_HONORIFIC = re.compile(r"^(?:dr|prof|mr|mrs|ms|miss|er|shri|smt|thiru|tmt|selvi|sri)\.?(?:\s+|(?<=\.)(?=[A-Z]))", re.I)
_NAME_TOKEN = re.compile(r"^(?:[A-Z][A-Za-z'’\-]+|[A-Z]\.?|(?:[A-Z]\.){2,3})$")
_NOT_NAME_WORDS = frozenset("""
professor assistant associate asst assoc lecturer senior junior head hod dean principal director chairman chairperson
department dept engineering college university institute institution school faculty member members grade sr jr research
scholar fellow coordinator manager officer clerk accountant engineer team cell centre center committee board assembly
council government ministry company ltd limited pvt inc corp corporation science sciences technology technologies system
systems electronics electrical mechanical civil computer communication communications physics chemistry mathematics ece
eee cse mech it ap asp ug pg phd me mtech btech msc unit session chapter video clip part lecture course subject lab
laboratory theory circuit circuits law laws algebra the of and for in on at
""".split())

_SMALL_WORDS = frozenset("a an and as at but by for from in into is of on or per the to vs via with".split())
_ACRONYMS = frozenset(
    "AI ML DL IOT IOE VLSI CMOS DSP PLC DC AC RF IC CPU GPU OS DBMS SQL HTML CSS API UI UX PCB BJT FET MOSFET LED "
    "ECE EEE CSE IT EIE MECH CIVIL ICT NLP CAD CAM CNC PID PWM ADC DAC FPGA ASIC RTL HDL HVDC SCR UPS LAN WAN".split()
)
_ROMAN = re.compile(r"^X{0,3}(?:IX|IV|V?I{0,3})$")


def _keys(text: str) -> list[str]:
    return [k.strip() for k in text.split(",") if k.strip()]


# ---------------------------------------------------------------------------
# key families
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _KeyInfo:
    category: ExcludedCategory | None  # None for document metadata
    meta_field: str | None
    tier: int  # 1: a strong key; 2: only in a header context; 3: only next to a strong person/admin key
    kind: str = "meta"  # value shape: name, by, designation, institution, contact, id, video, words, ...
    anywhere: bool = False  # unambiguous: counts outside a header context when the value has its shape


_META_KEYS = (
    ("subject_name", "subject name, name of the subject, subject title, course name, course title, name of the course, "
                     "paper name, paper title", "subject, course, paper"),
    ("unit_name", "unit name, unit title, name of the unit, module title, chapter name, chapter title",
     "unit, module, chapter, module name"),
    ("session_number", "session no, session number, lecture no, lecture number, class no, session #", "session, lecture"),
    ("session_title", "session title, session name, session topic, lecture title, lecture topic, topic name, "
                      "title of the session, title of the video, video title, title of the lecture", "topic, title, topic title"),
)
_ADMIN_KEYS: tuple[tuple[ExcludedCategory, str, int, bool, str], ...] = (
    ("person", "name", 1, True,
     "sme, sme name, sme names, name of the sme, name of sme, sme details, subject matter expert, subject matter expert name, "
     "subject expert, content expert, expert name, resource person, name of the resource person, resource person name, "
     "content developer, content writer, content reviewer, content creator, content author, instructional designer, "
     "script writer, scriptwriter, voice over artist, voice over by"),
    ("person", "name", 1, False,
     "faculty name, faculty names, name of the faculty, faculty in charge, faculty incharge, course instructor, "
     "instructor name, presenter name, speaker name, author name, author names, name & designation, name and designation, "
     "sme name & designation, sme name and designation, course faculty, handling faculty, subject faculty, faculty member"),
    ("person", "by", 1, False,
     "prepared by, compiled by, script by, scripted by, script written by, reviewed by, approved by, verified by, "
     "checked by, validated by, submitted by, submitted to, guided by, edited by, animated by, narrated by, authored by, "
     "developed by, created by, handled by, written by, presented by, designed by, recorded by"),
    ("person", "designation", 2, False, "designation"),
    ("person", "institution", 2, False,
     "department, dept, department name, name of the department, college, college name, institution, institution name, "
     "institute, institute name, name of the institution, university, school, organisation, organization, campus"),
    ("person", "contact", 2, False,
     "email, e mail, email id, e mail id, mail id, email address, e mail address, phone, phone no, phone number, mobile, "
     "mobile no, mobile number, contact, contact no, contact number, contact details"),
    ("person", "id", 2, False, "employee id, emp id, employee code, staff id, faculty id"),
    ("person", "name", 2, False, "faculty, hod, head of the department, head of department, coordinator, course coordinator"),
    ("person", "name", 3, False,
     "name, names, author, authors, teacher, teacher name, instructor, presenter, speaker, mentor, guide, reviewer, editor, "
     "narrator"),
    ("duration", "video", 1, True,
     "video duration, duration of the video, duration of video, length of the video, length of video, video length, "
     "clip duration, clip length, duration of the clip, duration of this clip, segment duration, total video duration, "
     "narration duration, video run time, video runtime"),
    ("duration", "words", 1, True,
     "word count, total word count, no of words, number of words, total words, estimated word count"),
    ("duration", "qualified", 1, False,
     "estimated duration, total duration, approximate duration, approx duration, expected duration, target duration, "
     "estimated time, estimated length, estimated run time, estimated runtime, run time, runtime, running time, "
     "total run time, total running time, duration min, duration mins, duration minutes"),
    ("duration", "bare", 2, False, "duration, length, time, timing, total time"),
    ("duration", "words", 2, False, "words"),
    ("admin", "code", 1, True,
     "course code, subject code, paper code, module code, unit code, course code & name, course code and name, "
     "subject code & name, subject code and name, course code/name, subject code/name, paper code & name"),
    ("admin", "regulation", 1, True, "regulation, regulations"),
    ("admin", "version", 1, True, "document version, doc version, version no"),
    ("admin", "date", 1, True,
     "date of recording, recording date, date of submission, submission date, date of preparation, date of review, "
     "review date, last updated, last modified, updated on, prepared on, reviewed on, created on"),
    ("admin", "number", 1, True, "video no, video number, clip no, clip number, script no"),
    ("admin", "term", 1, True, "ltpc, l t p c, year/semester, year & semester, year and semester, year/sem, academic year"),
    ("admin", "id", 2, False,
     "course id, video id, clip id, lecture id, session id, script id, document id, doc id, file name, filename"),
    ("admin", "date", 2, False, "date, dated"),
    ("admin", "version", 2, False, "version, version number, revision, revision no"),
    ("admin", "term", 2, False,
     "semester, sem, batch, year, credits"),
    ("admin", "number", 2, False, "slide no, slide number, page no"),
    ("admin", "status", 2, False, "status"),
    ("admin", "institution", 2, False, "program, programme, branch"),
)


def _key_table() -> dict[str, _KeyInfo]:
    table: dict[str, _KeyInfo] = {}
    for f, strong, weak in _META_KEYS:
        for k in _keys(strong):
            table[k] = _KeyInfo(None, f, 1)
        for k in _keys(weak):
            table[k] = _KeyInfo(None, f, 2)
    for category, kind, tier, anywhere, keys in _ADMIN_KEYS:
        for k in _keys(keys):
            table.setdefault(k, _KeyInfo(category, None, tier, kind, anywhere))
    return table


_KEY_TABLE = _key_table()
_BY_VERBS = (r"prepared|reviewed|approved|developed|created|handled|checked|verified|validated|compiled|submitted|edited"
             r"|guided|scripted|written|narrated|animated|presented|authored|designed|recorded|vetted|proofread")
_BY_FAMILY = re.compile(rf"^(?:(?:script|content|video|document|notes?|e ?content)\s+)?(?:{_BY_VERBS})"
                        rf"(?:\s*(?:and|&|/)\s*(?:{_BY_VERBS}))?\s+(?:by|to)$")
_PERSON_FAMILY = re.compile(r"\bsmes?\b|subject\s+matter\s+expert|resource\s+person|content\s+(?:developer|writer|reviewer"
                            r"|creator|author|expert)|instructional\s+designer|script\s*writer")
_FACULTY_FAMILY = re.compile(r"\bfaculty\b(?!.*\b(?:id|code|no|number)\b)")
_VIDEO_DURATION_FAMILY = re.compile(
    r"\b(?:video|clip|segment)\b.*\b(?:duration|length|run\s*time|runtime|running\s+time)\b"
    r"|\b(?:duration|length|run\s*time|runtime|running\s+time)\b.*\b(?:video|clip|segment)\b")
_QUALIFIED_DURATION_FAMILY = re.compile(
    r"^(?:total|estimated|expected|approx|approximate|target|overall)\b.*\b(?:duration|length|run\s*time|runtime|running\s+time)$")
_CODE_FAMILY = re.compile(r"\b(?:course|subject|paper|module|unit)\s+code\b")


def _norm_key(key: str) -> str:
    """Lower-case key without parentheticals, punctuation or extra spaces ("Year / Semester" -> "year/semester")."""
    k = key.lower().replace("’", "'").replace("(s)", "")
    k = re.sub(r"\([^)]*\)", " ", k)
    k = re.sub(r"[^a-z0-9&/#' ]+", " ", k)
    k = re.sub(r"\s*/\s*", "/", k)
    return re.sub(r"\s+", " ", k).strip(" '")


def _lookup(norm: str) -> _KeyInfo | None:
    """Key info of a normalised key: the key tables first, then the key families."""
    info = _KEY_TABLE.get(norm)
    if info is not None:
        return info
    for prefix in ("script ", "the "):
        if norm.startswith(prefix) and (info := _KEY_TABLE.get(norm[len(prefix):])) is not None:
            return info
    if not norm or len(norm.split()) > 8:
        return None
    if _BY_FAMILY.match(norm):
        return _KeyInfo("person", None, 1, "by")
    if _PERSON_FAMILY.search(norm):
        return _KeyInfo("person", None, 1, "name", True)
    if _VIDEO_DURATION_FAMILY.search(norm):
        return _KeyInfo("duration", None, 1, "video", True)
    if _QUALIFIED_DURATION_FAMILY.search(norm):
        return _KeyInfo("duration", None, 1, "qualified")
    if _CODE_FAMILY.search(norm):
        return _KeyInfo("admin", None, 1, "code", True)
    if _FACULTY_FAMILY.search(norm):
        return _KeyInfo("person", None, 1, "name")
    return None


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------


@dataclass
class ScopeResult:
    """Scoped source: teachable Markdown plus everything moved out of it."""

    markdown: str
    document_meta: SourceMeta = field(default_factory=SourceMeta)
    visual_notes_raw: list[tuple[str, str]] = field(default_factory=list)  # (anchor heading/snippet, text)
    excluded: list[ExcludedItem] = field(default_factory=list)
    source_format: SourceFormat = "unknown"
    figures: list[SourceFigure] = field(default_factory=list)  # captions filled from "SOURCE IMAGE" lines
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def plain_text(line: str) -> str:
    """A line without heading/list/quote markers and ``*``/``**``/``_label_`` emphasis (for classification)."""
    s = line.strip()
    s = re.sub(r"^#{1,6}\s+", "", s)
    s = re.sub(r"^(?:>\s*)+", "", s)
    s = _LIST.sub("", s, count=1) if _LIST.match(s) else s
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"^_(?!\s)([^_]+?)(?<!\s)_(?=[\s:]|$)", r"\1", s)
    return s.strip()


def strip_timecodes(text: str) -> tuple[str, list[str]]:
    """Remove bracketed timecodes / bracketed run-time tags (and a bare range at the end) from a heading or label."""
    found: list[str] = []

    def take(m: re.Match[str]) -> str:
        found.append(m.group(0).strip())
        return ""

    out = _TIMECODE.sub(take, text)
    out = _DURATION_TAG.sub(take, out)
    out = _TIMECODE_BARE.sub(take, out)
    return out.strip(), found


def strip_body_timecodes(text: str) -> tuple[str, list[str]]:
    """Remove square-bracketed timecode ranges ("[0:10 - 1:15]") from narration; times and durations stay."""
    found: list[str] = []

    def take(m: re.Match[str]) -> str:
        found.append(m.group(0).strip())
        return ""

    return _TIMECODE_SQUARE.sub(take, text).rstrip(), found


def _seconds(tc: str) -> int:
    parts = [int(float(x)) for x in tc.split(":")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def looks_like_person(value: str) -> bool:
    """A person's name, optionally followed by ", designation": an honorific, or 2-5 capitalised name tokens."""
    v = re.sub(r"\s+", " ", value or "").strip(" .,;:-–—*_\"'")
    if not v:
        return False
    first = re.split(r"\s*[,;(|/]\s*|\s+[-–—]\s+", v, maxsplit=1)[0].strip(" .")
    hon = _HONORIFIC.match(first)
    rest = first[hon.end():].strip() if hon else first
    tokens = rest.split()
    if not tokens or len(tokens) > 5:
        return False
    if any(t.lower().strip(".,") in _NOT_NAME_WORDS for t in tokens):
        return False
    if not all(_NAME_TOKEN.match(t.rstrip(",")) for t in tokens):
        return False
    if hon:
        return True
    return len(tokens) >= 2 and any(len(t.strip(".")) > 1 for t in tokens)


def contact_value(value: str) -> bool:
    """An e-mail address or a phone number."""
    v = (value or "").strip()
    return bool(_EMAIL.search(v)) or (bool(_PHONE.match(v)) and sum(c.isdigit() for c in v) >= 7)


def _definition_like(value: str) -> bool:
    """A value that reads as a definition or a sentence, not as a header field."""
    v = value.strip()
    words = v.split()
    return (bool(_ARTICLE_START.match(v)) and len(words) >= 3) or len(words) > 8 or (len(words) >= 4 and bool(_VERBISH.search(v)))


def _video_length(value: str, *, bare_ok: bool = False) -> bool:
    """A video/clip length: seconds or minutes (at most two hours), "mm:ss", or (``bare_ok``) a bare number of minutes."""
    v = value.strip(" .*_[]()")
    if _CLOCK.match(v):
        return True
    m = _VIDEO_LENGTH.match(v)
    if m:
        n = float(m.group("n"))
        return n <= (120 if m.group("u").lower().startswith("m") else 7200)
    return bare_ok and bool(re.fullmatch(r"\d{1,3}(?:\.\d+)?", v)) and float(v) <= 120


def _minutes_value(value: str, key: str = "") -> bool:
    """A length in minutes ("12 min", "15 minutes", or a bare number under a "(in minutes)" key)."""
    m = _VIDEO_LENGTH.match(value.strip(" .*_"))
    if m:
        return m.group("u").lower().startswith("m") and float(m.group("n")) <= 120
    return bool(re.fullmatch(r"\d{1,3}", value.strip())) and "min" in key


def _timing_value(value: str) -> bool:
    """Segment timing in an SME script: a timecode or a duration with a spelled-out unit ("45 sec", not "5 s")."""
    v = value.strip(" .*_[]()")
    if _CLOCK.match(v):
        return True
    m = _VIDEO_LENGTH.match(v)
    return bool(m) and m.group("u").lower() != "s"


def _value_ok(info: _KeyInfo, value: str, mode: str) -> bool:
    """Does ``value`` have the shape of ``info``'s field? ``mode``: strict (anywhere), lenient (header context),
    relaxed (metadata table, YAML front matter)."""
    v = value.strip().strip("*_").strip()
    if not v or len(v) > 240:
        return False
    if info.meta_field is not None:
        return True
    relaxed = mode == "relaxed"
    words = len(v.split())
    k = info.kind
    if k == "by":
        return looks_like_person(v)
    if k == "name":
        if mode == "strict":
            return looks_like_person(v)
        return looks_like_person(v) or (words <= (20 if relaxed else 12) and not _definition_like(v))
    if k in ("designation", "institution", "status"):
        return words <= (16 if relaxed else 10) and (relaxed or not _definition_like(v))
    if k == "contact":
        return contact_value(v) or (relaxed and len(v) <= 80)
    if k == "id":
        return bool(re.fullmatch(r"[\w.\-/#]{1,40}", v)) or (relaxed and len(v) <= 60)
    has_digit = bool(re.search(r"\d", v))
    if k in ("video", "qualified"):
        return _video_length(v, bare_ok=True) or (relaxed and len(v) <= 40 and has_digit)
    if k == "bare":
        return _video_length(v) or (relaxed and len(v) <= 40 and has_digit)
    if k == "words":
        return bool(_WORDS_VALUE.match(v)) or (relaxed and len(v) <= 40 and has_digit)
    if k == "code":
        return bool(_CODE_VALUE.match(v)) or (relaxed and len(v) <= 120)
    if k == "regulation":
        return bool(_REGULATION_VALUE.match(v)) or (relaxed and len(v) <= 40)
    if k == "version":
        return len(v) <= 40 and bool(_VERSION_VALUE.match(v))
    if k == "date":
        return len(v) <= 40 and words <= 6 and (bool(_DATE_VALUE.search(v)) or relaxed)
    if k == "number":
        return bool(_NUMBER_VALUE.match(v)) or (relaxed and len(v) <= 20)
    if k == "term":
        return bool(_TERM_VALUE.match(v)) or (relaxed and len(v) <= 40)
    return len(v) <= 240


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in _SMALL_WORDS}


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.75


def _same_title(a: str, b: str) -> bool:
    """Near-identical titles (token Jaccard >= 0.8)."""
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.8


def smart_title(value: str) -> str:
    """Title-case an ALL-CAPS value (keeps acronyms, codes such as EC3352 and roman numerals); other values are
    returned unchanged."""
    letters = [c for c in value if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) < 0.9 * len(letters):
        return value
    out: list[str] = []
    after_colon = True
    for word in value.split():
        core = re.sub(r"[^A-Za-z0-9]", "", word)
        alpha = re.sub(r"[^A-Za-z]", "", word)
        if (core.upper() in _ACRONYMS or (alpha and re.search(r"\d", core))
                or (alpha and _ROMAN.match(alpha.upper()) and alpha == core)
                or (alpha and not re.search(r"[AEIOUY]", alpha.upper()) and len(alpha) <= 5)):
            out.append(word)
        elif not after_colon and core.lower() in _SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:].lower())
        after_colon = word.endswith(":")
    return " ".join(out)


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _label_title(title: str) -> bool:
    """A structure label's title ("WHAT IS A DIODE?"), not a sentence ("Part 2: the denominator is never zero.")."""
    t = (title or "").strip()
    if not t:
        return True
    if t[0].islower():
        return False
    words = t.split()
    return not (t.endswith(".") and not t.endswith("..") and len(words) >= 3) and len(words) <= 18


def _label_like(p: str) -> bool:
    """A production-style label line: ALL CAPS or ending with a colon."""
    letters = [c for c in p if c.isalpha()]
    return p.rstrip().endswith(":") or (bool(letters) and all(c.isupper() for c in letters))


def _caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


# "In this video, we will learn:" with nothing after it (a lead-in to the clip's objective list)
_LEAD_IN = re.compile(r"^(?:in\s+this\s+(?:video|clip|segment|part|session|lesson)|today)\s*,?\s*(?:we|you)\s+(?:will|are\s+going\s+to)"
                      r"\s+(?:learn|see|cover|study|discuss|understand|explore)(?:\s+about)?\s*:?\s*$", re.I)
_WATCHING = re.compile(r"^(?:thank(?:s|\s+you)\s+for\s+(?:watching|listening)|see\s+you\s+(?:all\s+)?in\s+the\s+next\s+"
                       r"(?:video|clip|part|class|lecture))[\s.!]*$", re.I)


def _sign_off(text: str) -> bool:
    """Every sentence of ``text`` is a sign-off or a pointer to the next clip ("Thank you for watching.")."""
    parts = _sentences(text.replace("’", "'"))
    return bool(parts) and all(_SIGN_OFF_SENTENCE.match(s.strip()) or _BRIDGE_SENTENCE.match(s.strip()) for s in parts)


_ABBREVIATION = re.compile(r"(?:\b(?i:dr|prof|mr|mrs|ms|er|st|sr|jr|no|vs|fig|eq|approx|etc|viz|cf|e\.g|i\.e)|\b[A-Z])\.$")
_LINE_PREFIX = re.compile(r"^(\s*(?:(?:[-*+•]|\d+[.)])\s+|>\s*)?)(.*)$", re.S)
_BARE_LEAD_IN = re.compile(r"^(?:we|you)\s+(?:will|are\s+going\s+to|shall)\s+(?:learn|see|cover|study|discuss|understand"
                           r"|explore)(?:\s+about)?\s*:?\s*$", re.I)


def split_sentences(text: str) -> list[str]:
    """Sentences of ``text``; never split after an honorific or an initial ("Dr. A. Kumar said ...")."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        if out and _ABBREVIATION.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        elif part:
            out.append(part)
    return out


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _map_sentences(line: str, fn: Callable[[str], str | None]) -> str:
    """``line`` with ``fn`` applied to each sentence (``fn`` returns the new sentence, or None to drop it). A list
    marker or quote prefix is kept, and the line is returned unchanged when no sentence changed."""
    m = _LINE_PREFIX.match(line)
    prefix, body = (m.group(1), m.group(2)) if m else ("", line)
    parts = split_sentences(body)
    new = [fn(s) for s in parts]
    if all(a == b for a, b in zip(parts, new, strict=True)):
        return line
    kept = [s for s in new if s and s.strip()]
    return prefix + " ".join(kept) if kept else ""


# A sentence that is a presenter cue or self-reference once the author's name is replaced by NAME: "NAME will now
# demonstrate this on the board", "Hello, I am NAME", "Your instructor NAME", "NAME here".
_CUE_STEMS = (r"demonstrat|show|explain|walk|tak|draw|solv|deriv|discuss|present|record|teach|guid|introduc|cover|work"
              r"|explor|illustrat|sketch|writ|tell|help|narrat|join|welcom|talk|answer|summari[sz]|recap|conclud|begin"
              r"|start|continu|describ|us|go")
_PRESENTER_CUE = re.compile(
    r"\bNAME\b(?:\s+(?:will|shall|would|can|could|is\s+going\s+to|is\s+about\s+to|'ll|’ll|now|also|then|again|first"
    rf"|next|quickly|briefly))+\s+(?:{_CUE_STEMS})\w*"
    rf"|^(?:(?:so|now|next|here)\s*,?\s*)?NAME\s+(?:{_CUE_STEMS})\w*?(?:s|es)\b"
    r"|\b(?:i\s+am|i'm|i’m|myself|my\s+name\s+is|this\s+is|it'?s|presented\s+by|brought\s+to\s+you\s+by|narrated\s+by"
    r"|hosted\s+by|your\s+(?:faculty|instructor|teacher|host|presenter|professor|lecturer|guide|sme|tutor|trainer|mentor)"
    r"(?:\s+(?:is|for\s+(?:this|today'?s?)\s+\w+))?)\s*,?\s*NAME\b"
    r"|\bNAME\s*,?\s+here\b",
    re.I,
)
_HONORIFIC_WORD = r"(?:dr|prof|mr|mrs|ms|miss|er|shri|smt|thiru|tmt|selvi|sri)\.?"
_INITIALS = r"(?:(?-i:[A-Z])(?:\.\s*|\s+)){0,3}"


def _name_regex(tokens: Sequence[str]) -> str:
    """"K. Srinivasan" -> ``K\\.?\\s*Srinivasan`` (initials may lose their dot, words are separated by spaces)."""
    out = ""
    for i, t in enumerate(tokens):
        if i:
            out += r"\s*" if len(tokens[i - 1]) == 1 else r"\s+"
        out += re.escape(t) + (r"\.?" if len(t) == 1 else "")
    return out


@dataclass
class _PersonMatcher:
    """The excluded authors' names in a line: ``strong`` (the full name, or an honorific with a part of it) and
    ``weak`` (a capitalised part of the name alone, which may well be someone else: it only counts in a presenter
    cue)."""

    strong: re.Pattern[str] | None
    weak: re.Pattern[str] | None

    @classmethod
    def build(cls, names: Sequence[Sequence[str]]) -> _PersonMatcher:
        full: list[str] = []
        parts: set[str] = set()
        for tokens in names:
            toks = [t for t in tokens if t]
            if len(toks) >= 2:
                full.append(rf"(?:{_HONORIFIC_WORD}\s*)?{_name_regex(toks)}")
            parts.update(t for t in toks if len(t) >= 3 and t.isalpha() and t.lower() not in _NOT_NAME_WORDS)
        alts = sorted(set(full), key=len, reverse=True)
        if parts:
            names_rx = "|".join(re.escape(p) for p in sorted(parts, key=len, reverse=True))
            alts.append(rf"{_HONORIFIC_WORD}\s*{_INITIALS}(?:{names_rx})")
        strong = re.compile(r"(?<![\w.])(?:" + "|".join(alts) + r")(?![\w])", re.I) if alts else None
        weak = None
        if parts:  # capitalised as a name ("Meena"; never "RAM" for an author called Ram)
            caps = "|".join(re.escape(p[:1].upper() + p[1:].lower()) for p in sorted(parts, key=len, reverse=True))
            weak = re.compile(rf"(?<![\w.])(?:{caps})(?![\w])")
        return cls(strong, weak)

    def spans(self, text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        strong = [m.span() for m in self.strong.finditer(text)] if self.strong else []
        weak = [m.span() for m in self.weak.finditer(text)] if self.weak else []
        weak = [w for w in weak if not any(a <= w[0] < b for a, b in strong)]
        return strong, weak


def _mark(text: str, spans: Sequence[tuple[int, int]], repl: str) -> str:
    out, last = [], 0
    for a, b in sorted(spans):
        out.append(text[last:a])
        out.append(repl)
        last = b
    out.append(text[last:])
    return "".join(out)


# ---------------------------------------------------------------------------
# administrative fragments inside titles
# ---------------------------------------------------------------------------

_FRAG_NUMBER = re.compile(r"^(?:video|clip|lecture|session|slide|page|module|unit|script)\s*(?:no\.?|number|#|id)\s*[:\-.]?\s*[\w\-]+$", re.I)
_FRAG_DURATION = re.compile(r"^(?:duration|time|length|run\s*time|running\s+time|approx\.?|about|est(?:imated|\.)?)?\s*[:\-]?\s*"
                            r"~?\s*\d+(?:\.\d+)?\s*(?:s|secs?|seconds?|mins?|minutes?)\.?$", re.I)
_FRAG_REGULATION = re.compile(r"^(?:regulation\s*[:\-]?\s*)?R\s?-?(?:19|20)\d{2}$", re.I)
_FRAG_CODE = re.compile(r"^(?:(?:course|subject|paper)\s+code\s*[:\-]?\s*)?[A-Z]{2,5}\s?-?\d{3,5}[A-Z]?$")
_TRAILING_DURATION = re.compile(r"\s*[-–—|,:]\s*~?\s*\d+(?:\.\d+)?\s*(?:secs?|seconds?|mins?|minutes?)\.?\s*$", re.I)
_TRAILING_BY = re.compile(r"\s+(?:by|[-–—])\s+(?P<name>(?:dr|prof|mr|mrs|ms)\.?\s*[A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,4})\s*$", re.I)
_LEADING_CODE = re.compile(r"^(?P<code>[A-Z]{2,5}\s?-?\d{3,5}[A-Z]?)\s*(?:[-–—:|]\s*|\s+(?=[A-Z]))")
_TRAILING_CODE = re.compile(r"\s*[-–—:|,]\s*(?P<code>(?:[A-Z]{2,5}\s?-?\d{3,5}[A-Z]?|R\s?-?(?:19|20)\d{2}))\s*$")


def _admin_fragment(inner: str, codes: bool) -> tuple[ExcludedCategory, str] | None:
    s = inner.strip()
    if _FRAG_NUMBER.match(s):
        return "admin", s
    if _FRAG_DURATION.match(s):
        return "duration", s if ":" in s else f"Duration: {s}"
    if _HONORIFIC.match(s) and looks_like_person(s):
        return "person", f"Name in a title: {s}"
    if codes and _FRAG_REGULATION.match(s):
        return "admin", s if ":" in s else f"Regulation: {s}"
    if codes and _FRAG_CODE.match(s):
        return "admin", s if ":" in s else f"Course code: {s}"
    if _DATE_VALUE.fullmatch(s) and not re.fullmatch(r"\d{4}", s):  # a bare year in a title is content
        return "admin", f"Date: {s}"
    return None


def strip_title_admin(title: str, *, codes: bool = False) -> tuple[str, list[tuple[ExcludedCategory, str]]]:
    """``title`` without administrative fragments: "(Duration: 15 min)", "(Video No: 07)", "| Dr. X", "by Dr. X",
    a trailing "- 12 min" and (``codes``: subject / unit / session lines) a course code or regulation."""
    found: list[tuple[ExcludedCategory, str]] = []
    t = title

    def bracket(m: re.Match[str]) -> str:
        frag = _admin_fragment(m.group(1), codes)
        if frag is None:
            return m.group(0)
        found.append(frag)
        return ""

    t = re.sub(r"\s*[\[(]([^\])]{1,60})[\])]", bracket, t)
    if "|" in t:
        kept: list[str] = []
        for part in re.split(r"\s*\|\s*", t):
            frag = _admin_fragment(part, codes) if part.strip() else None
            if frag is None and looks_like_person(part) and _HONORIFIC.match(part.strip()):
                frag = ("person", f"Name in a title: {part.strip()}")
            if frag is None:
                kept.append(part)
            else:
                found.append(frag)
        t = " | ".join(p for p in kept if p.strip())
    if (m := _TRAILING_BY.search(t)) and looks_like_person(m.group("name")):
        found.append(("person", f"Name in a title: {m.group('name').strip()}"))
        t = t[:m.start()]
    if (m := _TRAILING_DURATION.search(t)) and len(t[:m.start()].split()) >= 1:
        found.append(("duration", f"Duration: {m.group(0).strip(' -–—|,:')}"))
        t = t[:m.start()]
    if codes:
        if (m := _LEADING_CODE.match(t)) and t[m.end():].strip():
            found.append(("admin", f"Course code: {m.group('code')}"))
            t = t[m.end():]
        if (m := _TRAILING_CODE.search(t)) and t[:m.start()].strip():
            code = m.group("code")
            found.append(("admin", f"Regulation: {code}" if code.upper().startswith("R") and _FRAG_REGULATION.match(code)
                          else f"Course code: {code}"))
            t = t[:m.start()]
    return re.sub(r"\s+", " ", t).strip(" -–—:|,;"), found


# ---------------------------------------------------------------------------
# title-page lines: "Subject - Unit 1: Title - Session 2"
# ---------------------------------------------------------------------------

_TL_NUMBER_KEY = r"\s*(?:(?i:no\.?|number|#)\s*)?[:\-–—.]?\s*"
_TL_UNIT = re.compile(rf"^(?P<key>(?i:unit|module|chapter|block)){_TL_NUMBER_KEY}(?P<n>\d{{1,3}}[A-Za-z]?|[IVXL]{{1,5}})"
                      r"(?![\w'’])\s*(?:[:.\-–—]\s*(?P<title>.+))?$")
_TL_SESSION = re.compile(rf"^(?i:session|lecture|lesson){_TL_NUMBER_KEY}(?P<n>\d{{1,3}}[A-Za-z]?)(?![\w'’])"
                         r"\s*(?:[:.\-–—]\s*(?P<title>.+))?$")
_TL_SUBJECT = re.compile(r"^(?i:subject|course|paper)(?:\s+(?i:name|title))?\s*[:\-–—]\s*(?P<v>.+)$")
_TL_TOPIC = re.compile(r"^(?i:topic|session\s+title|lecture\s+title)\s*[:\-–—]\s*(?P<v>.+)$")
_TL_TERM = re.compile(r"^(?i:semester|sem|year|yr|class|grade|std|standard)\.?\s*[:\-]?\s*(?:[IVX]{1,4}|\d{1,2})$"
                      r"|^(?:[IVX]{1,4}|\d{1,2})(?i:st|nd|rd|th)?\s+(?i:semester|sem|year|yr)$")
_TL_SPLITS = (re.compile(r"\s*[|·•]\s*"), re.compile(r"\s+[-–—]\s+|\s*[–—]\s*"), re.compile(r"\s*[,;]\s*"))


@dataclass
class TitleLine:
    """The fields of a title-page line such as "Basic Electrical Engineering - Unit 1: Electric Circuits - Session 2"."""

    subject_name: str = ""
    unit_name: str = ""  # the unit's title, else "Unit n"
    session_number: str = ""  # "Session n"
    session_title: str = ""
    admin: list[tuple[ExcludedCategory, str]] = field(default_factory=list)  # codes, terms, names, durations

    def kinds(self) -> int:
        return sum(1 for v in (self.subject_name, self.unit_name, self.session_number) if v)


def _title_like(part: str) -> bool:
    """A name such as a subject or a unit title: a few words, no sentence, no equation."""
    words = part.split()
    return (1 <= len(words) <= 12 and len(part) <= 120 and (part[0].isupper() or part[0].isdigit())
            and not re.search(r"[.!?]$|[=<>]", part) and any(c.isalpha() for c in part))


def parse_title_line(text: str) -> TitleLine | None:
    """The subject / unit / session fields of a title-page line, or None when ``text`` is not one.

    The line is split at ``|``, a spaced dash or (failing those) commas; every part must be a subject
    ("Subject: X" or one unlabelled name), a unit ("Unit 1: Title", "Module 4", "Chapter 3"), a session
    ("Session 2", "Lecture 2"), a topic ("Topic: X") or an administrative fragment (a course code, a
    semester, a regulation, a duration, a name). At least two of subject / unit / session are needed,
    one of them a unit or a session, so "Unit 3: Logic gates" or "Module name: math" alone are not
    title lines. An unlabelled part right after a bare "Unit 1" or "Session 2" is its title.
    """
    s = plain_text(text).strip(" *_")
    if not s or len(s) > 240 or (s.endswith((".", "?", "!")) and not re.search(r"\b(?:no|sem|yr)\.$", s, re.I)):
        return None
    parts: list[str] = [s]
    for sep in _TL_SPLITS:
        found = [p.strip(" *_") for p in sep.split(s)]
        if len(found) >= 2:
            parts = [p for p in found if p]
            break
    if len(parts) < 2 or len(parts) > 6:
        return None
    out = TitleLine()
    open_title = ""  # "unit" / "session": the previous part was a bare unit or session (an unlabelled title follows)
    for part in parts:
        if (m := _TL_SUBJECT.match(part)) is not None:
            if out.subject_name:
                return None
            out.subject_name, open_title = m.group("v").strip(), ""
        elif (m := _TL_TOPIC.match(part)) is not None:
            out.session_title, open_title = m.group("v").strip(), ""
        elif (m := _TL_UNIT.match(part)) is not None:
            if out.unit_name:
                return None
            title = (m.group("title") or "").strip()
            out.unit_name = title or f"{m.group('key').title()} {m.group('n')}"
            open_title = "" if title else "unit"
        elif (m := _TL_SESSION.match(part)) is not None:
            if out.session_number:
                return None
            out.session_number = f"Session {m.group('n')}"
            title = (m.group("title") or "").strip()
            if title:
                out.session_title = title
            open_title = "" if title else "session"
        elif (frag := _admin_fragment(part, True)) is not None or _TL_TERM.match(part):
            out.admin.append(frag or ("admin", f"Semester / year: {part}"))
            open_title = ""
        elif _title_like(part):
            if open_title == "unit":
                out.unit_name = part
            elif open_title == "session" and not out.session_title:
                out.session_title = part
            elif not out.subject_name:
                out.subject_name = part
            else:
                return None
            open_title = ""
        else:
            return None
    if out.kinds() < 2 or not (out.unit_name or out.session_number):
        return None
    return out


# ---------------------------------------------------------------------------
# key-value classification
# ---------------------------------------------------------------------------


@dataclass
class _KV:
    meta_field: str | None  # SourceMeta field, or None for administrative data
    category: ExcludedCategory | None
    tier: int  # 1 strong, 2 header context, 3 next to a strong person/admin key
    key: str
    value: str
    kind: str = "meta"
    anywhere: bool = False
    strict: bool = True  # the value has the key's strict shape (a person's name for a person key, ...)
    loose: bool = False  # written without a separator ("Prepared by Dr. X")


_KEY_UNIT = re.compile(r"\(\s*(?:in\s+)?(mins?|minutes|secs?|seconds)\s*\)", re.I)


def key_info(cell: str) -> _KeyInfo | None:
    """Tier/category of a bare key such as a table cell ("SME Name"), or None."""
    return _lookup(_norm_key(plain_text(cell)))


def classify_kv(text: str, *, relaxed: bool = False) -> _KV | None:
    """Recognise "Key: value" metadata / administrative lines (``relaxed``: inside a metadata table)."""
    m = _KV_RE.match(plain_text(text))
    if not m:
        return None
    key = _norm_key(m.group("key"))
    info = _lookup(key)
    if info is None:
        return None
    value = m.group("value").strip().strip("*_").strip()
    if not value:
        if info.category in ("person", "duration") and info.tier == 1:
            return _KV(None, info.category, 1, key, "", info.kind, info.anywhere, False)
        return None
    check = value
    unit = _KEY_UNIT.search(m.group("key"))
    if info.category == "duration" and unit and re.fullmatch(r"\d{1,3}(?:\.\d+)?", value):
        check = f"{value} {unit.group(1)}"  # "Duration (in minutes): 12"
        key = f"{key} ({unit.group(1).lower()})"
    if not _value_ok(info, check, "relaxed" if relaxed else "lenient"):
        return None
    return _KV(info.meta_field, info.category, info.tier, key, value, info.kind, info.anywhere,
               _value_ok(info, check, "strict"))


def _loose_kv(text: str) -> _KV | None:
    """ "Prepared by Dr. X" / "Approved by<TAB>Dr. Y" / "Total running time 15 minutes" (no separator)."""
    m = _KV_LOOSE.match(plain_text(text))
    if not m or len(m.group("key").split()) > 5:
        return None
    key = _norm_key(m.group("key"))
    info = _lookup(key)
    if info is None or info.category not in ("person", "duration") or info.kind not in ("by", "video", "qualified", "bare", "words"):
        return None
    value = m.group("value").strip()
    if not _value_ok(info, value, "strict"):
        return None
    return _KV(None, info.category, info.tier, key, value, info.kind, info.anywhere, True, True)


def header_line_value(line: str) -> tuple[ExcludedCategory, str] | None:
    """(category, value) when ``line`` is a header field with a strong key and a value of its shape: a "Key: value"
    line or a table row ("| 1 | Name of the SME | Dr. X |"). Used by the leak lint to catch values that got past
    scoping."""
    s = line.strip()
    if s.startswith("|"):
        cells = [plain_text(c) for c in _cells(s)]
        cells = [c for c in cells if c and not _SEPARATOR_CELL.match(c)]
        if cells and _SERIAL_CELL.match(cells[0]):
            cells = cells[1:]
        if len(cells) < 2:
            return None
        key, value = cells[0], " ".join(cells[1:])
    else:
        m = _KV_RE.match(plain_text(s))
        if not m:
            return None
        key, value = m.group("key"), m.group("value").strip()
    info = _lookup(_norm_key(key))
    if info is None or info.category is None or not value or info.tier != 1:
        return None
    if info.kind in ("name", "by") and not looks_like_person(value):
        return None
    if not _value_ok(info, value, "strict"):
        return None
    return info.category, value


def admin_key_line(line: str) -> bool:
    """A header field: a "Key: value" line or a table row ("| 1 | Name of the SME | Dr. X |") whose key is a
    person / duration / administrative key (any tier) and whose value has that key's shape. "Prepared by:
    heating ammonium chloride" is not one."""
    s = line.strip()
    if s.startswith("|"):
        cells = [plain_text(c) for c in _cells(s)]
        cells = [c for c in cells if c and not _SEPARATOR_CELL.match(c)]
        if cells and _SERIAL_CELL.match(cells[0]):
            cells = cells[1:]
        if len(cells) < 2:
            return False
        s = f"{cells[0]}: {' '.join(cells[1:])}"
    kv = classify_kv(s) or _loose_kv(s)
    return kv is not None and kv.category is not None and bool(kv.value)


# ---------------------------------------------------------------------------
# tokenising
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    kind: str  # blank | page | heading | table | figure | list | text | yaml | code (a whole fenced block)
    lines: list[str]
    plain: str = ""
    level: int = 0
    kv: _KV | None = None
    loose: _KV | None = None
    merged: bool = False  # the value line of the "Key:" line before it
    owner: int = -1  # index of that "Key:" line


def _yaml_block(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line indexes of a leading YAML front-matter block (the fences included)."""
    start = next((i for i, line in enumerate(lines) if line.strip()), None)
    if start is None or lines[start].strip() != "---":
        return None
    for end in range(start + 1, min(len(lines), start + 60)):
        if lines[end].strip() in ("---", "..."):
            body = [line for line in lines[start + 1:end] if line.strip()]
            if body and sum(1 for line in body if _YAML_KEY.match(line)) * 2 >= len(body):
                return start, end
            return None
    return None


def _collapse_blank_runs(lines: list[str]) -> str:
    """The lines joined, two or more consecutive blank lines collapsed to one empty line, except inside fenced code."""
    out: list[str] = []
    run: list[str] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        if fence is None and not line.strip(" \t"):
            run.append(line)
            continue
        if run:
            out.extend(run if len(run) == 1 else [""])
            run = []
        out.append(line)
        if fence is not None:
            if closes_fence(line, *fence):
                fence = None
        elif (opened := fence_open(line)) is not None:
            fence = opened[0], opened[1]
    if run:
        out.extend(run if len(run) == 1 else [""])
    return "\n".join(out)


def front_matter_span(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line indexes of a leading YAML front-matter block, fences included (None without one)."""
    return _yaml_block(lines)


def _units(markdown: str) -> list[_Unit]:
    out: list[_Unit] = []
    table: list[str] = []
    fence: tuple[str, int] | None = None

    def flush_table() -> None:
        if table:
            out.append(_Unit("table", list(table)))
            table.clear()

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    yaml = _yaml_block(lines)
    for n, line in enumerate(lines):
        s = line.strip()
        if fence is not None:  # one unit per fenced block: code is never classified line by line
            out[-1].lines.append(line)
            if closes_fence(line, *fence):
                fence = None
            continue
        if yaml and yaml[0] <= n <= yaml[1]:
            out.append(_Unit("yaml", [line], plain="" if n in yaml else s))
            continue
        if (opened := fence_open(line)) is not None:
            flush_table()
            fence = opened[0], opened[1]
            out.append(_Unit("code", [line]))
            continue
        if s.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if not s:
            out.append(_Unit("blank", [line]))
        elif _PAGE.match(s):
            out.append(_Unit("page", [line]))
        elif _FIGURE.match(s):
            out.append(_Unit("figure", [line]))
        elif h := _HEADING.match(s):
            out.append(_Unit("heading", [line], plain=plain_text(h.group(2)), level=len(h.group(1))))
        elif _LIST.match(s):
            out.append(_Unit("list", [line], plain=plain_text(s)))
        else:
            out.append(_Unit("text", [line], plain=plain_text(s)))
    flush_table()
    return out


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _table_rows(unit: _Unit) -> list[list[str]]:
    rows = []
    for line in unit.lines:
        cells = _cells(line)
        if cells and all(_SEPARATOR_CELL.match(c) for c in cells if c):
            continue
        rows.append(["" if _PLACEHOLDER_CELL.match(c) else c for c in cells])
    return rows


_SERIAL_CELL = re.compile(r"^(?:\d{1,3}|[ivxlc]{1,6}|[a-z])[.)]?$", re.I)
_SERIAL_HEADER = re.compile(r"^(?:s\.?\s*no\.?|sl\.?\s*no\.?|sr\.?\s*no\.?|serial\s+(?:no\.?|number)|no\.?|#|item\s+no\.?)$", re.I)
_KV_HEADER_WORDS = frozenset(
    "particulars particular field fields item items parameter parameters key keys attribute attributes property "
    "description details detail value values information info entry entries data remarks specification".split())
_TABLE_TITLE = re.compile(
    r"^(?:(?:video|document|course|session|lecture|script|clip|general|basic|sme|faculty|author|subject|e-?content|"
    r"programme|program|module)\s+)*(?:details|information|info|particulars|metadata|profile)$", re.I)


def _kv_layout(rows: list[list[str]]) -> tuple[int, bool]:
    """(key column, has a "Particulars | Details" header row) of a row-wise table; a leading serial-number column
    ("S.No", "Sl. No", or 1, 2, 3 ...) is skipped."""
    if not rows:
        return 0, False
    col = 0
    first = plain_text(rows[0][0]) if rows[0] else ""
    width = max(len(r) for r in rows)
    if first and _SERIAL_HEADER.match(first):
        col = 1
    elif width >= 3:
        serials = [plain_text(r[0]) for r in rows[1:] if r and r[0]]
        if len(serials) >= 2 and all(_SERIAL_CELL.match(c) for c in serials):
            col = 1
    head = [plain_text(c).lower().strip(" :") for c in rows[0][col:] if c]
    return col, bool(head) and all(c in _KV_HEADER_WORDS for c in head)


def _title_row(row: list[str]) -> bool:
    cells = {plain_text(c) for c in row if c and c.strip()}
    return len(cells) == 1 and bool(_TABLE_TITLE.match(next(iter(cells)).strip(" :")))


# ---------------------------------------------------------------------------
# format detection
# ---------------------------------------------------------------------------

_SCRIPT_VISUAL_COL = re.compile(
    r"^(?:visuals?|visual\s+(?:description|cues?|directions?|elements?)|on[\s-]?screen(?:\s+(?:text|visuals?|action|graphics?))?"
    r"|animations?(?:\s*(?:/|&|and)\s*visuals?)?|visuals?\s*(?:/|&|and)\s*animations?|graphics?|scene\s+description"
    r"|shot\s+description|video|screen|what\s+(?:is\s+)?shown|b[\s-]?roll)$", re.I)
_SCRIPT_TEXT_COL = re.compile(
    r"^(?:narration|narration\s*(?:/|&)\s*audio|audio(?:\s*(?:/|&)\s*narration)?|audio\s+script|voice[\s-]?over|v\.?o\.?"
    r"|script|aadhi\s+speaks|what\s+aadhi\s+says|dialogue|spoken\s+text|speech)$", re.I)
_SCRIPT_TIME_COL = re.compile(r"^(?:time|duration|timecode|time\s*code|timing|time\s*\([^)]*\)|duration\s*\([^)]*\)|secs?|seconds"
                              r"|mins?|minutes|start|end|in|out)$", re.I)
_SCRIPT_NUM_COL = re.compile(r"^(?:s\.?\s*no\.?|sl\.?\s*no\.?|sr\.?\s*no\.?|no\.?|#|scene(?:\s+no\.?)?|shot(?:\s+no\.?)?|segment|clip)$", re.I)


def _script_table_columns(rows: list[list[str]]) -> dict[str, list[int]] | None:
    """Column roles of an AV script table (whole-cell headers: Visual | Narration [| Time]) or None."""
    if len(rows) < 2:
        return None
    roles: dict[str, list[int]] = {"visual": [], "text": [], "time": [], "num": [], "other": []}
    for i, cell in enumerate(rows[0]):
        c = plain_text(cell).strip(" :")
        if not c:
            continue
        if _SCRIPT_NUM_COL.match(c):
            roles["num"].append(i)
        elif _SCRIPT_TIME_COL.match(c):
            roles["time"].append(i)
        elif _SCRIPT_VISUAL_COL.match(c):
            roles["visual"].append(i)
        elif _SCRIPT_TEXT_COL.match(c):
            roles["text"].append(i)
        else:
            roles["other"].append(i)
    return roles if roles["visual"] and roles["text"] else None


def is_script_label(text: str) -> bool:
    """A narrator, board, visual, note or production label line of an SME script ("Aadhi speaks:", "BOARD
    displays:", "ANIMATION: ...", "NOTE: ...", "CAMERA: ...")."""
    p = plain_text(text)
    return bool(_speaker_rest(p) is not None or _BOARD_TITLE.match(p) or _BOARD_POINTS.match(p) or _VISUAL_LABEL.match(p)
                or _NOTE.match(p) or production_label(p) or _SOURCE_IMAGE.match(p))


def _speaker_rest(p: str) -> str | None:
    """The text after a narrator label ("Aadhi speaks:", "Narration:", or any "X says:" alone on its line)."""
    m = _SPEAKER.match(p)
    if not m:
        return None
    rest = m.group("rest") or ""
    if m.group("label") or not rest.strip() or _NARRATOR.match((m.group("who") or "").strip()):
        return rest
    return None


def detect_format(units: Sequence[_Unit]) -> SourceFormat:
    """sme_script when production markers are frequent and at least one is specific to SME video scripts (CLIP n
    SCRIPT, a narrator label, a BOARD label, an ANIMATION label, a bracketed timecode range) or an AV script table
    has three rows; textbook / notes otherwise."""
    kinds: Counter[str] = Counter()
    specific = 0
    headings = numbered = pages = 0
    readable = False
    for u in units:
        if u.kind == "page":
            pages += 1
            continue
        if u.kind == "table":
            readable = True
            rows = _table_rows(u)
            if _script_table_columns(rows):
                kinds["script_table"] += 2
                specific += 1
                if sum(1 for r in rows[1:] if any(c.strip() for c in r)) >= 3:
                    return "sme_script"
            continue
        p = u.plain
        if not p or u.kind == "yaml":
            continue
        readable = True
        if u.kind == "heading":
            headings += 1
            if _NUMBERED_HEADING.match(u.lines[0].strip()):
                numbered += 1
        clean = strip_timecodes(p)[0]
        if (m := _SPEAKER.match(p)) is not None:
            kinds["speaker"] += 1
            who = (m.group("who") or "").strip()
            if re.match(r"^aadhi$", who, re.I) or (_speaker_rest(p) is not None and not (m.group("rest") or "").strip()):
                specific += 1
        elif _BOARD_TITLE.match(p) or _BOARD_POINTS.match(p):
            kinds["board"] += 1
            specific += 1
        elif _SEGMENT.match(clean) and len(p) <= 140 and _label_title(_SEGMENT.match(clean).group("title") or ""):
            kinds["segment"] += 1
        elif _CLIP.match(clean) and re.search(r"\bclip\b|\bscript\b", p, re.I) and len(p) <= 140:
            kinds["clip"] += 1
            specific += 1
        elif (vm := _VISUAL_LABEL.match(p)) is not None and _VISUAL_ANY_CASE.match(vm.group("label")):
            kinds["visual"] += 1
            if _caps(vm.group("label")):
                specific += 1
        if re.search(rf"[\[(]\s*{_TC_RANGE}\s*[\])]", p) and len(p) <= 200:
            kinds["timecode"] += 1
            specific += 1
    total = sum(kinds.values())
    if total >= 4 and len(kinds) >= 2 and specific >= 1:
        return "sme_script"
    if not readable:
        return "unknown"
    if numbered >= 3 or pages >= 8:
        return "textbook"
    return "notes"


# ---------------------------------------------------------------------------
# the scoper
# ---------------------------------------------------------------------------


class _Scoper:
    def __init__(self, markdown: str, figures: Sequence[SourceFigure], *, safe: bool = False) -> None:
        self.safe = safe  # fallback mode: no block drops and no multi-paragraph visual capture
        self.excluded: list[ExcludedItem] = []
        self.comments: list[str] = []
        # hidden comments go, except inside fenced code (an HTML lesson's "<!-- ... -->" is code)
        self.units = _units(outside_fences(markdown, lambda part: _HTML_COMMENT.sub(self._take_comment, part)))
        self.figures = list(figures)
        self.captions: dict[str, str] = {}
        self.fmt: SourceFormat = detect_format(self.units)
        self.sme = self.fmt == "sme_script"
        self.meta: dict[str, str] = {"subject_name": "", "unit_name": "", "session_number": "", "session_title": ""}
        self.timecodes: list[str] = []
        self.labels: Counter[str] = Counter()
        self.notes: list[tuple[str, str]] = []
        self.out: list[str] = []
        self.person_tokens: set[str] = set()
        self.person_pairs: set[tuple[str, str]] = set()
        self.person_names: set[tuple[str, ...]] = set()  # excluded authors' names as written (honorific removed)
        self._matcher: _PersonMatcher | None = None
        # walk state
        self.heading = ""  # current kept heading (visual-note anchor)
        self.last_content = ""  # last emitted content line (anchor when there are no headings)
        self.visual_buf: list[str] = []  # visual directions of the current section
        self.visual_anchor = ""
        self.carry_visual: list[str] = []  # first title card's visuals -> next kept section
        self.capture: int = 0  # paragraphs still to capture into visual_buf after a bare label
        self.captured = 0  # paragraphs captured since that label
        self.segment_since_clip = False
        self.kept_in_clip = False
        self.drop: str | None = None  # title_card | bridge | objectives | outro | recap
        self.drop_title = ""
        self.drop_text = ""
        self.drop_first_card = False
        self.in_segment = False
        self.objectives_seen = False
        self.title_cards = 0
        self.clips = 0
        self.clip_label = ""
        self.pending_caption: str | None = None
        self.pending_caption_ttl = 0
        self.meta_seen: set[str] = set()
        self.title_lines: set[str] = set()  # normalised title-page lines already taken
        self.struct: list[int] = []
        if self.sme:
            self.struct = [i for i, u in enumerate(self.units) if u.kind in ("heading", "text") and u.plain
                           and (self._clip_match(u.plain) or self._segment_match(u.plain))]
        self.clip_total = sum(1 for i in self.struct if self._clip_match(self.units[i].plain))
        self.has_structure = bool(self.struct)
        self.heading_tc = "all" if self.sme else self._notes_heading_timecodes()
        self._record_comments()
        self._classify()

    # -- small utilities ------------------------------------------------------------------------
    def _take_comment(self, m: re.Match[str]) -> str:
        inner = re.sub(r"\s+", " ", m.group(1)).strip()
        if inner:
            self.comments.append(inner)
        return ""

    def _record_comments(self) -> None:
        """Hidden HTML comments are never content; header fields inside them are recorded by category."""
        for inner in self.comments:
            rest: list[str] = []
            for part in re.split(r"\s*[;|]\s*", inner):
                kv = classify_kv(part, relaxed=True) or _loose_kv(part)
                if kv is not None and kv.value and kv.category is not None:
                    self.exclude(kv.category, f"{kv.key}: {kv.value}", "hidden comment in the source")
                    self._note_person(kv)
                elif part.strip():
                    rest.append(part)
            if rest:
                self.exclude("production_note", "; ".join(rest), "hidden comment in the source")

    def _clip_match(self, plain: str) -> re.Match[str] | None:
        p = strip_timecodes(plain)[0]
        if len(p) > 160:
            return None
        m = _CLIP.match(p)
        if m and re.search(r"\bclip\b|\bscript\b|^part\b|^video\b", p, re.I) and _label_title(m.group("title") or ""):
            return m
        return None

    def _segment_match(self, plain: str) -> re.Match[str] | None:
        p = strip_timecodes(plain)[0]
        m = _SEGMENT.match(p) if len(p) <= 160 else None
        return m if m and _label_title(m.group("title") or "") else None

    def _notes_heading_timecodes(self) -> str | None:
        """Notes: strip heading timecodes only when they look like video timing ("square": several headings with
        "[m:ss - m:ss]"; "chain": increasing ranges from the start of a video, end to start)."""
        square = 0
        ranges: list[tuple[int, int]] = []
        for u in self.units:
            if u.kind != "heading":
                continue
            if _TIMECODE_SQUARE.search(u.plain):
                square += 1
            m = re.search(rf"[\[(]\s*({_TC})\s*{_DASH}\s*({_TC})\s*[\])]|\s({_TC})\s*{_DASH}\s*({_TC})\s*$", u.plain)
            if m:
                a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                ranges.append((_seconds(a), _seconds(b)))
        chain = len(ranges) >= 2 and ranges[0][0] < 300 and all(a < b for a, b in ranges) and all(
            0 <= ranges[i + 1][0] - ranges[i][1] <= 60 for i in range(len(ranges) - 1))
        if chain:
            return "chain"
        return "square" if square >= 2 else None

    def exclude(self, category: ExcludedCategory, text: str, reason: str) -> None:
        if len(self.excluded) < MAX_EXCLUDED:
            self.excluded.append(ExcludedItem(category=category, text=_clip(text, 300) or category,
                                              reason=_clip(reason, 200), source="rules"))

    def _note_person(self, kv: _KV | None, value: str = "") -> None:
        """Remember the name tokens of an excluded person (to recognise recording notes that name them)."""
        if kv is not None and (kv.category != "person" or kv.kind not in ("name", "by")):
            return
        v = value or (kv.value if kv is not None else "")
        if not looks_like_person(v):
            return
        first = re.split(r"\s*[,;(|/]\s*", v, maxsplit=1)[0]
        first = _HONORIFIC.sub("", first.strip())
        written = tuple(t for t in (x.strip(".,") for x in first.replace(".", ". ").split()) if t)
        if written:
            self.person_names.add(written)
        tokens = [t.strip(".,").lower() for t in first.split()]
        for t in tokens:
            if len(t) >= 4 and t not in _NOT_NAME_WORDS:
                self.person_tokens.add(t)
        for a, b in zip(tokens, tokens[1:], strict=False):
            self.person_pairs.add((a, b))

    def _mentions_person(self, text: str, *, strict: bool = False) -> bool:
        """``text`` names an excluded person: any name token (``strict``: an honorific + a name token, or two
        consecutive tokens of the name, so "Thanks to Newton's laws" never matches an author called Newton)."""
        if not self.person_tokens:
            return False
        words = re.findall(r"[a-z]+", text.lower())
        if not strict:
            return any(w in self.person_tokens for w in words)
        if any(w in self.person_tokens for w in re.findall(
                r"\b(?:dr|prof|mr|mrs|ms|shri|smt)\.?\s*([a-z]+)", text.lower())):
            return True
        return any((a, b) in self.person_pairs for a, b in zip(words, words[1:], strict=False))

    # -- narration packaging and the authors' names -----------------------------------------------------
    def narration(self, text: str) -> str:
        """An SME script line without its packaging sentences: the video's length, sign-offs ("Don't forget to like
        and subscribe!"), greetings, pointers to other videos of the series and to the next clip are dropped; an
        "In this video, we will learn X" lead-in becomes "We will learn X" and "as we saw in Clip 1" becomes "as we
        saw earlier". Everything removed is recorded; other formats are returned unchanged."""
        if not self.sme or not text.strip():
            return text
        return _map_sentences(text, self._narration_sentence)

    def _narration_sentence(self, sentence: str) -> str | None:
        s = sentence.strip()
        norm = s.replace("’", "'")
        if _DURATION_SENTENCE.search(norm):
            self.exclude("duration", s, "video length in the narration")
            return None
        if _SIGN_OFF_SENTENCE.match(norm) or _BRIDGE_SENTENCE.match(norm) or _CHANNEL_TALK.search(norm):
            self.exclude("structure", s, "clip sign-off or greeting")
            return None
        if _SERIES_SENTENCE.search(norm):
            self.exclude("structure", s, "pointer to another video of the series")
            return None
        new = s
        if m := _LENGTH_IN_PHRASE.search(new):
            self.exclude("duration", m.group(0).strip(), "video length in the narration")
            new = new[:m.start()] + m.group(1) + " " + new[m.end():]
        if m := _LEAD_IN_PHRASE.match(new):
            rest = new[m.end():]
            if _BARE_LEAD_IN.match(rest.replace("’", "'")):
                self.exclude("structure", s, "lead-in to the clip's objectives")
                return None
            self.exclude("structure", m.group(0).strip(" ,"), "video packaging in the narration")
            new = _upper_first(rest)
        found = [m.group(0) for m in _CLIP_POINTER.finditer(new)]
        if found:
            self.exclude("structure", found[0], "pointer to another clip of the video")
            new = _CLIP_POINTER.sub("earlier", new)
            new = _upper_first(new) if new[:1].islower() and s[:1].isupper() else new
        return s if new == s else new

    def scrub_people(self, line: str, *, drop: bool = True, code: bool = False) -> str:
        """``line`` without the excluded authors' names (``person_names``): a sentence that is a presenter cue or a
        self-reference ("Dr. X will now demonstrate this", "I am X") is dropped (``drop``), any other mention of the
        full name, or of an honorific with part of it, becomes "the instructor". A first name or surname alone only
        counts in a presenter cue: elsewhere it may well be someone else (a scientist the lesson is about). With
        ``code`` (a line inside a fenced block) the layout is kept and only a spaced full name or honorific + name
        is replaced ("/* Author: Dr. X */" -> "/* Author: the instructor */"), never an identifier."""
        matcher = self._matcher
        if matcher is None or not line.strip():
            return line

        def one(sentence: str) -> str | None:
            strong, weak = matcher.spans(sentence)
            if code:  # inside code only a spaced name ("Dr. X", "Ravi Kumar") counts, never an identifier (DrKumar)
                strong, weak = [(a, b) for a, b in strong if any(c.isspace() for c in sentence[a:b])], []
            if not strong and not weak:
                return sentence
            cue = bool(_PRESENTER_CUE.search(_mark(sentence, strong + weak, "NAME"))) or (
                bool(strong) and bool(_SELF_INTRO.search(sentence) or _RECORDING.search(sentence)))
            if drop and cue:
                self.exclude("production_note", sentence, "a presenter cue or self-reference naming the author")
                return None
            if not strong:
                return sentence
            for a, b in strong:
                self.exclude("person", f"Name in the text: {sentence[a:b].strip()}", "the author's name in the source text")
            out, last = [], 0
            for a, b in sorted(strong):
                repl = "The instructor" if not sentence[:a].strip(" \"'“‘*_(") else "the instructor"
                out.extend([sentence[last:a], repl])
                last = b
            out.append(sentence[last:])
            return re.sub(r"\bthe instructor\s+the instructor\b", "the instructor", "".join(out), flags=re.I)

        if not drop:
            return one(line) or line
        return _map_sentences(line, one)

    def _drop_board_repeats(self) -> None:
        """SME scripts write each point twice, as narration and again as a BOARD line or bullet: within one section,
        a line that repeats an earlier line word for word (ignoring emphasis, list markers and end punctuation) goes,
        and so does a repeated connector ("Therefore:") in front of it. A short answer after "Answer:" always stays,
        and so does every line that says something new."""
        if not self.sme:
            return
        keys: list[str | None] = []
        seen: set[str] = set()
        repeat: list[bool] = []
        code = self._code_lines()
        for n, line in enumerate(self.out):
            s = line.strip()
            if n in code:  # code repeats lines on purpose ("}", "return x;"): never a board repeat
                keys.append(None)
                repeat.append(False)
                continue
            if s.startswith("#"):
                seen = set()
            if not s or s.startswith(("#", "<!--", "[Figure", "|")):
                keys.append(None)
                repeat.append(False)
                continue
            key = " ".join(plain_text(s).casefold().rstrip(" .:;!").split())
            keys.append(key)
            # "Q1: What is A + 0? Answer: A." repeats a question and an answer written out above it
            parts = [p.strip(" .:;!") for p in re.split(r"\b(?:answer|ans)\s*:\s*",
                                                        re.sub(r"^(?:q|question)\s*\d+\s*[:.)]\s*", "", key))]
            qa_repeat = len(parts) == 2 and all(p and p in seen for p in parts)
            repeat.append(bool(key) and (key in seen or qa_repeat))
            seen.add(key)

        def neighbour(i: int, step: int) -> int | None:
            j = i + step
            while 0 <= j < len(keys) and keys[j] is None and (j in code or not self.out[j].strip().startswith("#")):
                j += step
            return j if 0 <= j < len(keys) and keys[j] is not None else None

        drop = [False] * len(keys)
        for i, key in enumerate(keys):
            if not repeat[i] or key is None or len(key.split()) > 40:
                continue
            prev = neighbour(i, -1)
            after_answer = prev is not None and bool(re.match(r"^(?:answer|ans|solution)\b[^:]{0,20}$", keys[prev] or ""))
            if (len(key.split()) >= 2 or "=" in key) and not after_answer:
                drop[i] = True
        for i in range(len(keys) - 1, -1, -1):  # a repeated connector ("Therefore:") before a dropped repeat
            key = keys[i]
            if repeat[i] and key and not drop[i] and len(key.split()) <= 3 and self.out[i].rstrip(" *_").endswith(":"):
                nxt = neighbour(i, 1)
                drop[i] = nxt is not None and drop[nxt]
        removed = [self.out[i].strip() for i, d in enumerate(drop) if d]
        if removed:
            self.out = [line for line, d in zip(self.out, drop, strict=True) if not d]
            self.exclude("duplicate", f"{len(removed)} board line(s) repeating the narration, such as '{removed[0]}'",
                         "the same point written twice in the script")

    def _scrub_output(self) -> None:
        """Remove the excluded authors' names from every kept line and visual suggestion (names learnt anywhere in
        the document count, so a footer credit also covers the narration above it)."""
        self._matcher = _PersonMatcher.build(sorted(self.person_names)) if self.person_names else None
        if self._matcher is None:
            return
        code = self._code_lines()
        for i, line in enumerate(self.out):
            s = line.strip()
            if not s:
                continue
            if i in code:  # code keeps its layout and lines; the author's name in a comment or string still goes
                self.out[i] = self.scrub_people(line, drop=False, code=True)
                continue
            if s.startswith(("<!--", "[Figure")):
                continue
            if s.startswith(("#", "|")):
                self.out[i] = self.scrub_people(line, drop=False)
            else:
                self.out[i] = self.scrub_people(line)
        notes = []
        for anchor, text in self.notes:
            text = self.scrub_people(text)
            if text.strip():
                notes.append((anchor, text))
        self.notes = notes

    def _code_lines(self) -> set[int]:
        """Indexes of ``self.out`` lines inside fenced code blocks (the fence markers included)."""
        inside: set[int] = set()
        fence: tuple[str, int] | None = None
        for n, line in enumerate(self.out):
            if fence is not None:
                inside.add(n)
                if closes_fence(line, *fence):
                    fence = None
            elif (opened := fence_open(line)) is not None:
                inside.add(n)
                fence = opened[0], opened[1]
        return inside

    def emit(self, *lines: str) -> None:
        for line in lines:
            self.out.append(line)
            if line.strip() and not line.lstrip().startswith(("#", "<!--")):
                self.last_content = plain_text(line)[:80]
        if any(line.strip() for line in lines) and self.carry_visual and self.heading:
            # the first title card's visual ideas belong to the section that follows it
            self.visual_buf = self.carry_visual + self.visual_buf
            self.visual_anchor = self.heading
            self.carry_visual = []

    def emit_block(self, *lines: str) -> None:
        """Emit lines as their own paragraph (blank line before and after)."""
        if self.out and self.out[-1].strip():
            self.out.append("")
        self.emit(*lines)
        self.out.append("")

    def add_visual(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.drop == "title_card":
            if self.drop_first_card:
                self.carry_visual.append(text)
            return
        if self.drop:
            return
        anchor = self.heading or self.last_content
        if self.visual_buf and anchor != self.visual_anchor:
            self.flush_visual()
        self.visual_anchor = anchor
        self.visual_buf.append(text)

    def flush_visual(self) -> None:
        if self.visual_buf:
            text = _clip(" ".join(self.visual_buf), VISUAL_NOTE_CHARS)
            if len(self.notes) < MAX_VISUAL_NOTES:
                self.notes.append((self.visual_anchor, text))
        self.visual_buf = []

    def set_heading(self, text: str) -> None:
        self.flush_visual()
        self.heading = text
        self.visual_anchor = text

    # -- pass 1: key-value lines and header context -------------------------------------------------
    def _classify(self) -> None:
        units = self.units
        for u in units:
            if u.kind in ("heading", "text", "list") and u.plain:
                u.kv = classify_kv(u.plain)
                if u.kv is None and u.kind != "heading":
                    u.loose = _loose_kv(u.plain)
        self._merge_values()
        self.front_end = self._front_end()
        self.back_start = self._back_start()
        for i, u in enumerate(units):  # names in a header table identify the author in later recording notes
            if u.kind == "table" and i < self.front_end:
                rows = _table_rows(u)
                if self._metadata_table(rows, in_front=True):
                    col, _ = _kv_layout(rows)
                    for r in rows:
                        cells = [c for c in r[col:] if c]
                        if len(cells) >= 2 and (info := key_info(cells[0])) is not None and info.category == "person":
                            self._note_person(None, plain_text(cells[1]))
        self.accept: set[int] = set()
        self.header_labels: set[int] = set()  # "VIDEO DETAILS:" lines that introduce a header block
        i, n = 0, len(units)
        while i < n:
            if not self._run_member(i):
                i += 1
                continue
            run = [i]
            j = i + 1
            while j < n and (units[j].kind in ("blank", "page") or units[j].merged or self._run_member(j)):
                if self._run_member(j):
                    run.append(j)
                j += 1
            self._accept_run(run)
            i = j

    def _merge_values(self) -> None:
        """ "Prepared by:" followed by "Dr. X, Assistant Professor" on its own line: one field."""
        units = self.units
        for i, u in enumerate(units):
            kv = u.kv
            if kv is None or kv.value or kv.category not in ("person", "duration"):
                continue
            j = i + 1
            while j < len(units) and units[j].kind == "blank":
                j += 1
            if j >= len(units) or units[j].kind not in ("text", "list") or units[j].kv is not None:
                continue
            value = units[j].plain
            if len(value.split()) > 12 or (value.endswith(".") and not _HONORIFIC.match(value) and len(value.split()) > 4):
                continue
            ok = looks_like_person(value) if kv.category == "person" else _video_length(value, bare_ok=True)
            if ok:
                kv.value, kv.strict = value, True
                units[j].merged = True
                units[j].owner = i
                units[j].loose = None

    def _run_member(self, i: int) -> bool:
        u = self.units[i]
        if u.merged:
            return False
        if u.kv is not None:
            return True
        if u.loose is None:
            return False
        if i < self.front_end or i >= self.back_start:
            return True
        return any(0 <= j < len(self.units) and self.units[j].kv is not None for j in self._neighbours(i))

    def _neighbours(self, i: int) -> list[int]:
        out = []
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(self.units) and self.units[j].kind == "blank":
                j += step
            out.append(j)
        return out

    def _after_example(self, i: int) -> bool:
        """The block follows "For example:" / "e.g." (an example record, not a header)."""
        j = i - 1
        while j >= 0 and (self.units[j].kind in ("blank", "page") or (
                self.units[j].plain and (_PAGE_OF.match(self.units[j].plain) or self._copyright(self.units[j].plain)))):
            j -= 1
        if j < 0:
            return False
        u = self.units[j]
        if u.kind in ("text", "list") and u.plain and u.kv is None:
            if _TABLE_TITLE.match(u.plain.strip(" :*")):  # "VIDEO DETAILS:" introduces a header block
                return False
            return u.plain.rstrip().endswith(":") or bool(_EXAMPLE_CUE.search(u.plain))
        return u.kind == "heading" and bool(_EXAMPLE_HEADING.match(u.plain))

    def _known_person(self, value: str) -> bool:
        first = re.split(r"\s*[,;(|/]\s*", value, maxsplit=1)[0]
        return self._mentions_person(_HONORIFIC.sub("", first.strip()))

    def _alone_ok(self, kv: _KV, front: bool, back: bool, example: bool, top: bool = True) -> bool:
        """A field that is administrative on its own (no other header field needed). ``top``: before the title
        heading (an empty "Prepared by:" after it may introduce a list of preparation steps)."""
        if kv.meta_field is not None:
            return kv.tier == 1 and (front or self.sme) and not example
        if example and not kv.anywhere:
            return False
        if not kv.value:
            return kv.tier == 1 and ((front and top) or back)
        if kv.category == "person":
            if kv.kind == "name" and kv.tier == 1:
                return (kv.anywhere and kv.strict) or front or back
            if kv.kind == "by":
                return front or back or self._known_person(kv.value)
            return False
        if kv.category == "duration":
            if kv.tier == 1 and kv.kind in ("video", "words"):
                return True
            # "Duration: 12 min" in a header names the video's length; "Duration: 10 s" may be physics
            minutes = kv.kind == "bare" and kv.key != "time" and _minutes_value(kv.value, kv.key)
            return (kv.kind == "qualified" or minutes) and (front or back or self.sme)
        return kv.category == "admin" and kv.tier == 1

    def _accept_run(self, run: list[int]) -> None:
        kvs: list[tuple[int, _KV]] = []
        for k in run:
            kv = self.units[k].kv or self.units[k].loose
            assert kv is not None
            kvs.append((k, kv))
        front = run[0] < self.front_end
        # the true front matter: before the document's title heading (after it, a record such as
        # "Name / Email / Phone" may be the lesson's first example, so only strong keys count there)
        top = front and (self.title_heading is None or run[0] < self.title_heading)
        back = run[0] >= self.back_start
        example = self._after_example(run[0])
        alone = {k: self._alone_ok(kv, front, back, example, top) for k, kv in kvs}
        signal = any(alone[k] and kv.meta_field is None for k, kv in kvs)
        if not example and (top or back):
            signal = signal or any(kv.category == "person" and kv.kind == "name" and bool(_HONORIFIC.match(kv.value))
                                   for _, kv in kvs)
        if not example and top:
            signal = signal or any(kv.category == "person" and kv.kind == "contact" and contact_value(kv.value)
                                   for _, kv in kvs)
        fields = [kv for _, kv in kvs if kv.tier == 2 and kv.category != "duration" and kv.value
                  and not _definition_like(kv.value)]
        cats = {kv.category or "meta" for kv in fields}
        meta_fields = {kv.meta_field for kv in fields if kv.meta_field}
        front_header = top and not example and (len(cats) >= 2 or len(meta_fields) >= 2)
        sign_off = back and not example and sum(1 for kv in fields if kv.category in ("person", "admin")) >= 3 and any(
            bool(_HONORIFIC.match(kv.value)) or contact_value(kv.value) for _, kv in kvs)
        header = signal or front_header or sign_off
        for k, kv in kvs:
            ok = alone[k]
            if not ok and header and kv.value and not _definition_like(kv.value):
                ok = kv.tier <= 2 or signal
            if not ok and self.sme and kv.category == "duration" and _timing_value(kv.value):
                ok = True  # segment timing inside an SME script ("Duration: 45 sec", "Time: 0:40 - 1:10")
            if ok:
                self.accept.add(k)
                self._note_person(kv)
        if any(k in self.accept for k in run):
            j = run[0] - 1
            while j >= 0 and self.units[j].kind == "blank":
                j -= 1
            if j >= 0 and self.units[j].kind in ("text", "heading") and _TABLE_TITLE.match(self.units[j].plain.strip(" :*")):
                self.header_labels.add(j)

    def _furniture(self, u: _Unit) -> bool:
        """Header / footer material: field lines, page furniture, credits, sign-offs."""
        if u.kind in ("blank", "page", "yaml") or u.merged:
            return True
        if u.kind in ("table", "figure", "code"):
            return False
        p = u.plain
        if not p or u.kv is not None or u.loose is not None:
            return True
        return bool(_PAGE_OF.match(p) or self._copyright(p) or self._footer(p) or _CREDIT.match(p)
                    or (self.sme and _sign_off(p)))

    def _content_start(self, u: _Unit) -> bool:
        if u.kind in ("blank", "page", "figure", "yaml") or u.merged:
            return False
        if u.kind == "code":
            return True
        if u.kind == "table":
            return not self._metadata_table(_table_rows(u), in_front=True)
        p = u.plain
        if self._furniture(u) or _DOC_LABEL.match(p.strip(" :")):
            return False
        if _SESSION_LINE.match(strip_timecodes(p)[0]) or (self.sme and _UNIT_LINE.match(p)):
            return False
        if u.kind in ("text", "heading") and parse_title_line(p) is not None:
            return False  # "Subject - Unit 1: X - Session 2" on a title page
        if u.kind == "heading" or u.kind == "list":
            return True
        return len(p.split()) >= 6

    def _front_end(self) -> int:
        """Index of the first content unit; a title heading followed by header fields belongs to the front matter
        (``self.title_heading`` is then its index)."""
        self.title_heading: int | None = None
        starts = [i for i, u in enumerate(self.units) if self._content_start(u)]
        if not starts:
            return len(self.units)
        i0 = starts[0]
        if self.units[i0].kind != "heading":
            return i0
        i1 = starts[1] if len(starts) > 1 else len(self.units)
        between = self.units[i0 + 1:i1]
        if any(u.kv is not None or u.loose is not None or (u.kind == "table")
               or (u.kind == "text" and u.plain and parse_title_line(u.plain) is not None) for u in between):
            self.title_heading = i0
            return i1
        return i0

    def _back_start(self) -> int:
        for i in range(len(self.units) - 1, -1, -1):
            if not self._furniture(self.units[i]):
                return i + 1
        return 0

    @staticmethod
    def _copyright(p: str) -> bool:
        """Copyright / confidentiality boilerplate ("© 2025 Example College. All rights reserved."), never a sentence
        that mentions copyright."""
        s = p.strip()
        if len(s) > 160:
            return False
        if _ALL_RIGHTS.match(s) or _CONFIDENTIAL.match(s):
            return True
        m = _COPYRIGHT_LINE.match(s)
        if not m:
            return False
        owner = [w for w in re.findall(r"[\w&'’.\-]+", m.group("owner") or "")]
        return all(w[:1].isupper() or w[:1].isdigit() or w.lower().strip(".") in _OWNER_SMALL for w in owner)

    def _footer(self, p: str) -> list[tuple[ExcludedCategory, str]] | None:
        """A page footer such as "Prepared by Dr. X · Page 2 of 2": (category, text) of each part, or None."""
        if len(p) > 160:
            return None
        parts = [x.strip(" *_") for x in re.split(r"\s*(?:·|•|\||\s[-–—]\s)\s*", p) if x.strip(" *_")]
        if len(parts) < 2:
            return None
        out: list[tuple[ExcludedCategory, str]] = []
        anchor = False
        for part in parts:
            if _PAGE_OF.match(part) or re.fullmatch(r"(?:page|pg\.?)\s*\d+", part, re.I):
                out.append(("admin", part))
                anchor = True
            elif (m := _BY_FRAGMENT.match(part)) and looks_like_person(m.group("name")):
                out.append(("person", f"{m.group('key')}: {m.group('name')}"))
                anchor = True
            elif self._copyright(part) or _DATE_VALUE.fullmatch(part) or _DOC_LABEL.match(part):
                out.append(("admin", part))
            else:
                return None
        return out if anchor else None

    # -- metadata -----------------------------------------------------------------------------------
    def take_meta(self, kv: _KV, raw: str) -> None:
        f = kv.meta_field
        assert f is not None
        value = plain_text(kv.value).strip(" .;,")
        if f == "session_number":
            m = re.match(r"^(?P<n>\d+[a-z]?|[ivx]+)\s*(?:[-–—:.]\s*(?P<title>.+))?$", value, re.I)
            if m:
                self._set_meta("session_number", f"Session {m.group('n')}")
                if m.group("title"):
                    self._set_meta("session_title", m.group("title"))
                return
            value = value if not value.isdigit() else f"Session {value}"
        if f == "unit_name" and re.fullmatch(r"\d+|[ivxIVX]+", value):
            value = f"Unit {value}"
        if f in self.meta_seen and self.meta[f]:  # the first value wins; later header lines are not content
            same = _norm_key(self.meta[f]) == _norm_key(smart_title(value))
            self.exclude("admin", raw, "repeated document header" if same else "document header")
            return
        self._set_meta(f, value)

    def _set_meta(self, f: str, value: str) -> None:
        self.meta_seen.add(f)
        if self.meta[f]:
            return
        clean, frags = strip_title_admin(strip_timecodes(value)[0], codes=True)
        for category, text in frags:
            self.exclude(category, text, "administrative detail in the document's title lines")
            if category == "person":
                self._note_person(None, text.split(":", 1)[-1])
        clean = smart_title(clean.strip(" .;,"))
        limit = {"session_number": 60, "session_title": 300}.get(f, 240)
        if clean:
            self.meta[f] = clean[:limit]

    def in_title_zone(self) -> bool:
        """Nothing but the document's title (and at most two subtitle lines) has been kept so far."""
        headings = body = 0
        for line in self.out:
            t = line.strip()
            if not t or t.startswith("<!--"):
                continue
            if t.startswith("#"):
                headings += 1
            else:
                body += 1
        return headings <= 1 and body <= 2

    def take_title_line(self, p: str, idx: int) -> bool:
        """Move a title-page line's subject / unit / session into the document metadata (True when taken).

        Only in the front matter or the title zone; the same line repeated later (a running header) is
        set aside as well. Codes, terms, names and durations inside it are recorded as excluded items.
        """
        tl = parse_title_line(p)
        if tl is None:
            return False
        norm = _norm_key(p)
        if norm in self.title_lines:
            self.exclude("admin", p, "repeated document header")
            return True
        if not (idx < self.front_end or idx in self.accept or self.in_title_zone()):
            return False
        self.title_lines.add(norm)
        for f, value in (("subject_name", tl.subject_name), ("unit_name", tl.unit_name),
                         ("session_number", tl.session_number), ("session_title", tl.session_title)):
            if value:
                self._set_meta(f, value)
        for category, text in tl.admin:
            self.exclude(category, text, "administrative detail in the document's title lines")
            if category == "person":
                self._note_person(None, text.split(":", 1)[-1])
        return True

    def clean_title(self, title: str) -> str:
        """A clip / segment / session title without administrative fragments (each one recorded)."""
        clean, frags = strip_title_admin(title)
        for category, text in frags:
            self.exclude(category, text, "administrative detail in a heading")
        return clean

    def handle_kv(self, kv: _KV, raw: str, is_heading: bool) -> None:
        if kv.meta_field is not None:
            if not self.sme and is_heading and kv.tier > 1:
                # "# Unit 3: Logic gates" in notes is also a section heading: record it and keep it
                if kv.meta_field == "session_number":
                    self.take_meta(kv, raw)
                else:
                    self._set_meta(kv.meta_field, kv.value)
                self._keep_heading_line(raw)
                return
            self.take_meta(kv, raw)
            return
        category = kv.category or "admin"
        reason = {
            "person": "author / reviewer details", "duration": "video duration or length", "admin": "document administration",
        }.get(category, "administrative detail")
        text = plain_text(raw)
        if kv.value and kv.value not in text:  # a value on the next line
            text = f"{text.rstrip(' :')}: {kv.value}"
        elif kv.loose:
            text = f"{text[: len(text) - len(kv.value)].strip()}: {kv.value}"
        self.exclude(category, text, reason)
        self._note_person(kv)

    def _keep_heading_line(self, raw: str) -> None:
        m = _HEADING.match(raw.strip())
        text = plain_text(m.group(2)) if m else plain_text(raw)
        self.set_heading(text)
        self.emit(raw)

    def handle_yaml(self, unit: _Unit) -> None:
        """YAML front matter is document metadata: known fields are classified, the rest is set aside."""
        p = unit.plain
        if not p:
            return
        kv = classify_kv(p, relaxed=True)
        if kv is not None and kv.value:
            self.handle_kv(kv, p, False)
        else:
            self.exclude("admin", p, "document front matter")

    # -- tables -------------------------------------------------------------------------------------
    @staticmethod
    def _header_keys(rows: list[list[str]]) -> list[_KeyInfo] | None:
        """Key infos of a column-wise metadata table's header row (None: not one)."""
        if not rows:
            return None
        header = [c for c in rows[0] if c]
        infos = [key_info(c) for c in header]
        known = [k for k in infos if k is not None]
        if len(header) >= 2 and len(known) >= max(2, (2 * len(header) + 2) // 3):
            return known
        return None

    @staticmethod
    def _kv_row(row: list[str], col: int = 0) -> _KeyInfo | None:
        """Key info when the row reads as "Key | value" with a plausible value ("| Time | second |" does not)."""
        cells = row[col:]
        if not cells or not cells[0] or sum(1 for c in cells if c) < 2:
            return None
        info = key_info(cells[0])
        if info is None or classify_kv(f"{plain_text(cells[0])}: {' '.join(plain_text(c) for c in cells[1:] if c)}") is None:
            return None
        return info

    def _metadata_table(self, rows: list[list[str]], *, in_front: bool) -> bool:
        """A header/metadata table: row-wise (key column, optionally after a serial-number column and under a
        "Particulars | Details" header) or column-wise (a header row of keys and one data row, in the front matter)."""
        if not rows:
            return False
        col, has_header = _kv_layout(rows)
        body = [r for r in (rows[1:] if has_header else rows) if any(c.strip() for c in r) and not _title_row(r)]
        header = self._header_keys(rows)
        if header is not None and not has_header and not any(self._kv_row(r, col) for r in rows[1:]):
            data = [r for r in rows[1:] if any(c.strip() for c in r)]
            if not in_front or len(data) != 1:
                return False  # a table of records (faculty, courses, versions) is data, never metadata
            pairs = [(k, data[0][i] if i < len(data[0]) else "") for i, k in enumerate(rows[0]) if k]
            ok = [classify_kv(f"{plain_text(k)}: {plain_text(v)}", relaxed=True) for k, v in pairs if v]
            good = [kv for kv in ok if kv is not None]
            return len(good) * 3 >= len(pairs) * 2 and (any(k.tier == 1 for k in header) or all(k.tier <= 2 for k in header))
        kv_rows = [info for r in body if (info := self._kv_row(r, col)) is not None]
        if not kv_rows or len(kv_rows) < 0.5 * len(body):
            return False
        return any(k.tier == 1 for k in kv_rows) or (in_front and any(k.tier <= 2 for k in kv_rows))

    def handle_table(self, unit: _Unit, idx: int) -> None:
        rows = _table_rows(unit)
        roles = _script_table_columns(rows)
        if roles is not None:
            return self._script_table(rows, roles)
        in_front = idx < self.front_end or idx >= self.back_start
        if not self._metadata_table(rows, in_front=in_front):
            self.emit(*unit.lines)
            return None
        kept: list[str] = []
        col, has_header = _kv_layout(rows)
        if (self._header_keys(rows) is not None and not has_header
                and not any(self._kv_row(r, col) for r in rows[1:])):
            data = next(r for r in rows[1:] if any(c.strip() for c in r))
            for i, key in enumerate(rows[0]):
                self._table_pair(key, data[i] if i < len(data) else "", kept)
        else:
            for cells in (rows[1:] if has_header else rows):
                if _title_row(cells):
                    self.exclude("admin", plain_text(next(c for c in cells if c.strip())), "metadata table title")
                    continue
                cells = cells[col:]
                pairs: list[tuple[str, str]] = []
                if len(cells) >= 4 and all(key_info(cells[k]) for k in range(0, len(cells) - 1, 2) if cells[k]):
                    pairs = [(cells[k], cells[k + 1] if k + 1 < len(cells) else "") for k in range(0, len(cells), 2)]
                elif cells:
                    pairs = [(cells[0], " ".join(c for c in cells[1:] if c))]
                for key, value in pairs:
                    self._table_pair(key, value, kept)
        if kept:
            self.emit_block(*kept)
        return None

    def _table_pair(self, key: str, value: str, kept: list[str]) -> None:
        if not key and not value:
            return
        kv = classify_kv(f"{plain_text(key)}: {plain_text(value)}", relaxed=True) if key else None
        if kv is None:
            if value or key:
                kept.append(f"{plain_text(key)}: {plain_text(value)}".strip(": ") if key else plain_text(value))
            return
        self.handle_kv(kv, f"{plain_text(key)}: {plain_text(value)}", False)

    def _row_kind(self, visual: str, narration: str) -> str | None:
        v, t = visual.strip(), narration.strip()
        if _TITLE_CARD.match(v) or _TITLE_ONLY.match(v):
            return "title_card"
        if _BRIDGE_ROW.match(v) or _BRIDGE_TITLE.match(v) or (t and _BRIDGE_SENTENCE.match(t)):
            return "bridge"
        if _OUTRO.match(v) or (t and _sign_off(t)):
            return "outro"
        if _OBJECTIVES.match(v):
            return "objectives"
        return None

    def _script_table(self, rows: list[list[str]], roles: dict[str, list[int]]) -> None:
        if roles["time"]:
            self.exclude("duration", "timing column of the script table", "video timing")
        for r in rows[1:]:
            visual = " ".join(plain_text(r[i]) for i in roles["visual"] if i < len(r) and r[i])
            texts = [plain_text(r[i]) for i in sorted(roles["text"] + roles["other"]) if i < len(r) and r[i]]
            kind = None if self.safe else self._row_kind(visual, " ".join(texts))
            if kind == "title_card":
                if self.title_cards == 0 and visual and not self.safe:
                    self.carry_visual.append(visual)
                self.title_cards += 1
                self.exclude("structure", f"Title card: {' '.join(texts) or visual}", "per-clip title card")
                continue
            if kind == "bridge":
                self.exclude("structure", ' '.join(texts) or visual, "bridge between video clips")
                continue
            if kind == "outro":
                self.exclude("structure", ' '.join(texts) or visual, "clip sign-off")
                continue
            if kind == "objectives":
                if self.objectives_seen:
                    self.exclude("structure", visual, "repeated per-clip objectives")
                    continue
                self.objectives_seen = True
            if visual:
                self.add_visual(visual)
            for t in texts:
                t, codes = strip_body_timecodes(t)
                self.timecodes.extend(codes)
                if t:
                    self.emit_block(t)

    # -- figures --------------------------------------------------------------------------------------
    def handle_figure(self, line: str) -> None:
        m = _FIGURE.match(line.strip())
        assert m is not None
        fid, caption = m.group(1), (m.group(2) or "").strip()
        if self.pending_caption and not caption:
            caption = self.pending_caption
            self.captions[fid] = caption
            self.pending_caption = None
            line = f"[Figure {fid}: {caption}]"
        self.emit(line)

    def caption_previous_figure(self, caption: str) -> bool:
        for k in range(len(self.out) - 1, max(-1, len(self.out) - 4), -1):
            m = _FIGURE.match(self.out[k].strip()) if self.out[k].strip() else None
            if m is None and self.out[k].strip():
                return False
            if m is not None:
                if m.group(2):
                    return False
                self.out[k] = f"[Figure {m.group(1)}: {caption}]"
                self.captions[m.group(1)] = caption
                return True
        return False

    # -- drop blocks ------------------------------------------------------------------------------------
    def start_drop(self, kind: str, title: str) -> None:
        if self.safe:
            return
        self.end_capture()
        self.flush_visual()
        self.drop = kind
        self.drop_title = title
        self.drop_text = ""
        self.drop_first_card = kind == "title_card" and self.title_cards == 0
        if kind == "title_card":
            self.title_cards += 1

    def end_drop(self) -> None:
        if self.drop is None:
            return
        kind, title, text = self.drop, self.drop_title, self.drop_text
        if kind == "title_card":
            self.exclude("structure", f"Title card: {text or title}", "per-clip title card")
        elif kind == "bridge":
            self.exclude("structure", f"{title}: {text}" if text else title, "bridge between video clips")
        elif kind == "objectives":
            self.exclude("structure", title, "repeated per-clip objectives")
        elif kind == "outro":
            self.exclude("structure", title, "clip sign-off")
        elif kind == "recap":
            self.exclude("structure", f"{title}: {text}" if text else title, "recap of the previous clip")
        self.drop = None

    def in_drop(self, unit: _Unit) -> bool:
        """True when ``unit`` is swallowed by the current drop block."""
        if self.drop is None:
            return False
        p = unit.plain
        structural = unit.kind in ("heading", "text") and p and (
            self._clip_match(p) or self._segment_match(p) or _SESSION_LINE.match(strip_timecodes(p)[0]))
        if structural or (not self.has_structure and unit.kind == "heading"):
            self.end_drop()
            return False
        if unit.kind in ("text", "heading", "list") and p and not self.drop_text:
            vm = self.visual_label(p, unit.kind)
            board = _BOARD_POINTS.match(p)
            if vm and vm.group("rest") and not re.match(r"^on[\s-]?screen", vm.group("label"), re.I):
                self.add_visual(vm.group("rest"))
            elif self.drop == "bridge":
                if board and not _BOARD_TITLE.match(p) and board.group("rest"):
                    self.drop_text = f"next: {board.group('rest')}"
            elif not (vm or board or _BOARD_TITLE.match(p) or _speaker_rest(p) is not None or _NOTE.match(p)
                      or _PRODUCTION.match(p) or production_label(p)):
                self.drop_text = strip_timecodes(p)[0]
        elif unit.kind in ("text", "heading", "list") and p and self.drop == "title_card":
            vm = self.visual_label(p, unit.kind)
            if vm and vm.group("rest") and not re.match(r"^on[\s-]?screen", vm.group("label"), re.I):
                self.add_visual(vm.group("rest"))
        return True

    # -- labels -----------------------------------------------------------------------------------------
    def visual_label(self, p: str, kind: str) -> re.Match[str] | None:
        """An author's visual direction label. Notes: only ALL-CAPS labels on ordinary lines (never a heading)."""
        m = _VISUAL_LABEL.match(p)
        if m is None:
            return None
        label = m.group("label")
        if not self.sme:
            return m if kind != "heading" and _caps(label) and _VISUAL_ANY_CASE.match(label) else None
        return m if _caps(label) or _VISUAL_ANY_CASE.match(label) else None

    def source_image(self, p: str) -> re.Match[str] | None:
        m = _SOURCE_IMAGE.match(p)
        if m is None:
            return None
        label = m.group("label")
        return m if _caps(label) or (self.sme and _SOURCE_IMAGE_ANY_CASE.match(label)) else None

    def end_capture(self) -> None:
        self.capture = 0

    def is_label(self, p: str, kind: str = "text") -> bool:
        return bool(
            _speaker_rest(p) is not None or _BOARD_TITLE.match(p) or _BOARD_POINTS.match(p) or self.visual_label(p, kind)
            or _NOTE.match(p) or _PRODUCTION.match(p) or production_label(p) or self.source_image(p)
        )

    # -- headings ---------------------------------------------------------------------------------------
    def handle_structural(self, unit: _Unit, idx: int) -> bool:
        """CLIP / SEGMENT / SESSION / UNIT lines of an SME script (True when handled)."""
        p = unit.plain
        if not p:
            return False
        clean, codes = strip_timecodes(p)
        if self.sme and (m := self._clip_match(p)) is not None:
            self.end_drop()
            self.end_capture()
            self.timecodes.extend(codes)
            self.clips += 1
            self.clip_label = self.clip_label or re.split(r"\s*[-–—:]\s+", clean, maxsplit=1)[0]
            self.in_segment = False
            self.segment_since_clip = False
            self.kept_in_clip = False
            title = self.clean_title((m.group("title") or "").strip(" -:"))
            if title:
                self.set_heading(title)
                self.emit_block(f"# {title}")
            return True
        if self.sme and (m := self._segment_match(p)) is not None:
            self.end_drop()
            self.end_capture()
            self.timecodes.extend(codes)
            title = self.clean_title((m.group("title") or "").strip(" -:"))
            self.in_segment = True
            return self._segment(title, idx)
        if (m := _SESSION_LINE.match(clean)) is not None and (self.sme or self.out_is_front()) \
                and _label_title(m.group("title") or ""):
            self.end_drop()
            self.timecodes.extend(codes)
            if not self.meta["session_number"]:
                self._set_meta("session_number", f"Session {m.group('n')}")
                if m.group("title"):
                    self._set_meta("session_title", m.group("title"))
                return True
            if m.group("title") and self.sme:  # a later session in the same script: keep its title
                title = self.clean_title(m.group("title"))
                self.set_heading(title)
                self.emit_block(f"# {title}")
                return True
            return False
        if self.sme and (m := _UNIT_LINE.match(clean)) is not None and len(clean) <= 160 and _label_title(m.group("title") or ""):
            if self.meta["unit_name"]:
                self.exclude("structure", clean, "repeated unit heading")
            else:
                self._set_meta("unit_name", m.group("title") or f"Unit {m.group('n')}")
            return True
        return False

    def out_is_front(self) -> bool:
        return not any(line.strip() and not line.lstrip().startswith("<!--") for line in self.out)

    def _next_struct(self, idx: int) -> int | None:
        k = bisect.bisect_right(self.struct, idx)
        return self.struct[k] if k < len(self.struct) else None

    def _segment_body(self, idx: int) -> list[str]:
        """The plain lines of the segment that starts at ``idx`` (labels reduced to their text)."""
        end = self._next_struct(idx)
        out: list[str] = []
        for u in self.units[idx + 1:end if end is not None else len(self.units)]:
            if u.kind not in ("text", "list", "heading") or not u.plain:
                continue
            p = u.plain
            if (self.visual_label(p, u.kind) or _NOTE.match(p) or production_label(p) or _PRODUCTION.match(p)
                    or _STAGE_DIRECTION.match(p)):
                continue
            rest = _speaker_rest(p)
            if rest is None and (m := _BOARD_POINTS.match(p) or _BOARD_TITLE.match(p)):
                rest = m.group("rest")
            text = (rest if rest is not None else p).strip()
            if text:
                out.append(text)
        return out

    def _teaches(self, idx: int) -> bool:
        """The segment holds a real sentence that is not a sign-off or a pointer to the next clip."""
        for line in self._segment_body(idx):
            for s in _sentences(line.replace("’", "'")):
                s = s.strip()
                if len(s.split()) >= 8 and not (_SIGN_OFF_SENTENCE.match(s) or _BRIDGE_SENTENCE.match(s)):
                    return True
        return False

    def _segment(self, title: str, idx: int) -> bool:
        t = title.strip()
        if not t:
            self.labels["SCENE n"] += 1  # "SCENE 3" with no title: the label alone is structure
            return True
        tn = t.replace("’", "'")
        body = self._segment_body(idx) if _INTRO_ONLY.match(tn) else []
        if _TITLE_CARD.match(tn) or _TITLE_ONLY.match(tn) or (
                _INTRO_ONLY.match(tn) and len(body) <= 1 and all(len(b.split()) <= 10 and not b.endswith(".") for b in body)):
            self.start_drop("title_card", t)
            return True
        nxt = self._next_struct(idx)
        last_in_clip = nxt is None or self._clip_match(self.units[nxt].plain) is not None
        if _BRIDGE_TITLE.match(tn) or (_TRANSITION_ONLY.match(tn) and last_in_clip):
            self.start_drop("bridge", t)
            return True
        if _OUTRO.match(tn) or (_OUTRO_SOFT.match(tn) and not self._teaches(idx)):
            self.start_drop("outro", t)
            return True
        if self.clip_total >= 2 and self.clips >= 2 and not self.kept_in_clip and _RECAP.match(tn):
            self.start_drop("recap", t)
            return True
        if _OBJECTIVES.match(tn):
            if self.objectives_seen:
                self.start_drop("objectives", t)
                return True
            self.objectives_seen = True
            t = "Learning objectives"
        self.kept_in_clip = True
        if self.clips and not self.segment_since_clip and _same_title(t, self.heading):
            self.segment_since_clip = True  # "CLIP 2 - ELECTRICAL POWER" + "SEGMENT 2 - ELECTRICAL POWER": one heading
            return True
        self.segment_since_clip = True
        self.set_heading(t)
        self.emit_block(f"## {t}")
        return True

    # -- main walk ----------------------------------------------------------------------------------------
    def run(self) -> ScopeResult:
        for idx, unit in enumerate(self.units):
            self.step(idx, unit)
        self.end_drop()
        self.flush_visual()
        if self.carry_visual and self.notes is not None:
            self.notes.append((self.heading or self.last_content, _clip(" ".join(self.carry_visual), VISUAL_NOTE_CHARS)))
        self._drop_board_repeats()
        self._scrub_output()
        if self.pending_caption:
            self.exclude("production_note", f"Image reference: {self.pending_caption}", "no image was found for it")
        if self.timecodes:
            self.exclude("timecode", f"{len(self.timecodes)} timecode(s), e.g. {self.timecodes[0]}", "video timing marks")
        if self.labels:
            first = next(iter(self.labels))
            self.exclude("structure", f"{sum(self.labels.values())} narrator / board label(s) such as '{first}'",
                         "script labels; the text after them is kept")
        if self.clips >= 2:
            self.exclude("structure", f"{self.clips} clip headings such as '{self.clip_label}'",
                         "the clips are taught as one continuous lecture")
        markdown = _collapse_blank_runs("\n".join(self.out).split("\n")).strip()
        markdown = markdown + "\n" if markdown else ""
        kept = set(re.findall(r"\[Figure ([A-Za-z0-9\-]+)", markdown))
        figures = []
        for f in self.figures:
            if f.id not in kept:
                continue
            cap = self.captions.get(f.id)
            figures.append(f.model_copy(update={"caption": cap[:600]}) if cap and not f.caption else f)
        return ScopeResult(
            markdown=markdown,
            document_meta=SourceMeta(**self.meta),
            visual_notes_raw=self.notes,
            excluded=self.excluded,
            source_format=self.fmt,
            figures=figures,
        )

    def step(self, idx: int, unit: _Unit) -> None:  # noqa: C901 - one dispatch table
        kind, p = unit.kind, unit.plain
        if unit.merged and unit.owner in self.accept:
            return  # recorded with its key line
        if kind == "yaml":
            self.handle_yaml(unit)
            return
        if kind == "page":
            self.emit(*unit.lines)
            return
        if kind == "blank":
            self.out.append("")
            return
        if kind == "code":  # always teaching content, kept verbatim (also inside a dropped block)
            self.end_capture()
            self.emit_block(*unit.lines)
            return
        if self.pending_caption is not None:
            self.pending_caption_ttl -= 1
            if kind == "heading" or self.pending_caption_ttl < 0:
                self.exclude("production_note", f"Image reference: {self.pending_caption}", "no image was found for it")
                self.pending_caption = None
        if self.in_drop(unit):
            return
        # visual capture after a bare "DETAILED ANIMATION / VISUAL:" label (SME scripts)
        if self.capture:
            if kind == "text" and p and not self.is_label(p) and unit.kv is None and not (
                    self.sme and (self._clip_match(p) or self._segment_match(p))) and (
                    self.captured == 0 or _VISUAL_WORDS.search(p)):
                self.add_visual(p)
                self.capture -= 1
                self.captured += 1
                return
            self.end_capture()
        if kind == "figure":
            self.handle_figure(unit.lines[0])
            return
        if kind == "table":
            self.handle_table(unit, idx)
            return
        if not p:
            self.emit(*unit.lines)
            return
        # a title-page line "Subject - Unit 1: X - Session 2" (also "Subject: X | Unit 2 | Session 3")
        if kind in ("text", "heading") and self.take_title_line(p, idx):
            return
        # metadata and administrative key-value lines
        if idx in self.header_labels:
            self.exclude("admin", p, "header block label")
            return
        if idx in self.accept:
            kv = unit.kv or unit.loose
            assert kv is not None
            self.handle_kv(kv, unit.lines[0], kind == "heading")
            return
        if (footer := self._footer(p)) is not None:
            for category, text in footer:
                self.exclude(category, text, "page furniture")
                if category == "person":
                    self._note_person(None, text.split(":", 1)[-1])
            return
        if _PAGE_OF.match(p) or self._copyright(p):
            self.exclude("admin", p, "page furniture")
            return
        if (m := _CREDIT.match(p)) and len(p.split()) <= 30 and looks_like_person(m.group("name")):
            self.exclude("person", f"Prepared by: {m.group('name').strip()}", "author credit")
            self._note_person(None, m.group("name"))
            return
        if kind == "heading" and _DOC_LABEL.match(strip_timecodes(p)[0].strip(" :")):
            self.exclude("structure", p, "document label")
            return
        if (self.sme or idx < self.front_end) and self.handle_structural(unit, idx):
            return
        if self.sme and kind in ("heading", "text") and _label_like(p) and len(p) <= 60 \
                and _TITLE_CARD.match(strip_timecodes(p)[0].rstrip(":")):
            self.start_drop("title_card", strip_timecodes(p)[0])
            return
        if self.handle_label(unit):
            return
        if self.sme and self.sme_line_dropped(unit):
            return
        if not self.sme and kind == "text" and all(_WATCHING.match(x.strip()) for x in _sentences(p)):
            self.exclude("structure", p, "video sign-off")
            return
        if kind == "heading":
            self.handle_heading(unit)
            return
        self.handle_text(unit)

    def handle_label(self, unit: _Unit) -> bool:
        p = unit.plain
        if m := self.source_image(p):
            cap = strip_timecodes(m.group("cap"))[0].strip()
            if cap and not self.caption_previous_figure(cap):
                self.pending_caption = cap
                self.pending_caption_ttl = 3
            return True
        vm = self.visual_label(p, unit.kind)
        if vm:
            rest = vm.group("rest").strip()
            if re.match(r"^on[\s-]?screen", vm.group("label"), re.I) and not rest:
                self.exclude("production_note", p, "screen layout instruction")
                return True
            if rest:
                self.add_visual(rest)
            elif self.sme and not self.safe:
                self.capture = _MAX_VISUAL_PARAS
                self.captured = 0
            else:
                self.exclude("production_note", p, "visual direction label")
            return True
        if not self.sme:
            return False
        if production_label(p):
            self.exclude("production_note", p, "production instruction")
            return True
        if (m := _NOTE.match(p)) is not None:
            rest = m.group("rest") or ""
            if _NOTE_PRODUCTION.search(rest) or _RECORDING.search(rest) or self._mentions_person(rest):
                self.exclude("production_note", p, "editing or recording instruction")
                return True
        if _STAGE_DIRECTION.match(p) and (_STAGE_WORDS.search(p) or _RECORDING.search(p) or self._mentions_person(p)):
            self.exclude("production_note", p, "stage direction")
            return True
        if m := _BOARD_TITLE.match(p):
            self.labels["BOARD Title:"] += 1
            rest = strip_timecodes(m.group("rest"))[0].strip()
            if rest and not _similar(rest, self.heading):
                self.emit_block(rest)
            return True
        if (rest := _speaker_rest(p)) is not None:  # the narration itself is the content: the label goes
            self.labels[p[: len(p) - len(rest)].strip() if rest else p.strip()] += 1
            rest, codes = strip_body_timecodes(rest.strip())
            self.timecodes.extend(codes)
            rest = self.narration(rest)
            if rest:
                self.emit_block(rest)
            return True
        if m := _BOARD_POINTS.match(p):  # board points follow as ordinary lines / lists
            self.labels["BOARD displays:"] += 1
            rest = self.narration(m.group("rest").strip())
            if rest:
                self.emit_block(rest)
            return True
        return False

    def sme_line_dropped(self, unit: _Unit) -> bool:
        """SME script lines that are packaging: sign-offs, a bare timing key, recording talk naming the author."""
        p = unit.plain
        body = strip_body_timecodes(p)[0]
        if re.fullmatch(r"(?:time|timing|duration|length|run\s*time|running\s+time)\s*[:\-–]?\s*", body, re.I):
            self.exclude("duration", p, "segment timing")
            return True
        if _sign_off(p):
            self.exclude("structure", p, "clip sign-off")
            return True
        if _LEAD_IN.match(p):
            self.exclude("structure", p, "lead-in to the clip's objectives")
            return True
        if self._mentions_person(p, strict=True) and (_SELF_INTRO.search(p) or _RECORDING.search(p)):
            self.exclude("production_note", p, "the presenter's introduction or a recording remark")
            return True
        return False

    def handle_heading(self, unit: _Unit) -> None:
        m = _HEADING.match(unit.lines[0].strip())
        assert m is not None
        hashes, text = m.group(1), m.group(2)
        plain = plain_text(text)
        if self.sme:
            sentence = self.in_segment and (plain.endswith((".", "!")) or len(plain.split()) >= 8)
            new, codes = (strip_body_timecodes if sentence else strip_timecodes)(text)
        elif self.heading_tc == "chain":
            new, codes = _TIMECODE.sub("", text), _TIMECODE.findall(text) + _TIMECODE_BARE.findall(text)
            new = _TIMECODE_BARE.sub("", new)
        elif self.heading_tc == "square":
            new, codes = strip_body_timecodes(text)
        else:
            new, codes = text, []
        if codes:
            self.timecodes.extend(c.strip() for c in codes)
            text = new.strip()
        if not plain_text(text):
            return
        if self.sme and self.in_segment:
            # inside a script segment, bold/caps lines are board content (formulas, points), not sections
            self.emit(plain_text(text) if not re.search(r"[*_`]", text) else text.strip())
            return
        if self.sme and self.clips:
            hashes = "#" * max(2, len(hashes))
        self.set_heading(plain_text(text))
        self.emit(f"{hashes} {text}" if text != m.group(2) or self.sme else unit.lines[0])

    def handle_text(self, unit: _Unit) -> None:
        line = unit.lines[0]
        if self.sme:
            new, codes = strip_body_timecodes(line)
            if codes:
                self.timecodes.extend(codes)
                line = new
            line = self.narration(line)
            if not plain_text(line):
                return
        self.emit(line)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


_NOT_TEXT = re.compile(r"<!--.*?-->|\[Figure [^\]]*\]|[#>*_`|\-]+|\s+", re.S)
MIN_KEEP_RATIO = 0.15  # keeping less than this share of the text means the structure was misread


def _readable(markdown: str) -> int:
    return len(_NOT_TEXT.sub("", markdown or ""))


def scope_source(markdown: str, *, figures: Sequence[SourceFigure] = ()) -> ScopeResult:
    """Separate the teachable content of ``markdown`` from metadata, admin data and production notes.

    Safety net: when scoping would keep less than ``MIN_KEEP_RATIO`` of a substantial source's text
    (a script layout that was misread), scoping is repeated without dropping whole blocks; if that
    still keeps too little, the source is used as written (with a warning).
    """
    if not (markdown or "").strip():
        return ScopeResult(markdown=markdown or "", figures=list(figures))
    result = _Scoper(markdown, figures).run()
    before = _readable(markdown)
    if before < 400 or _readable(result.markdown) >= MIN_KEEP_RATIO * before:
        return result
    safe = _Scoper(markdown, figures, safe=True).run()
    if _readable(safe.markdown) >= MIN_KEEP_RATIO * before:
        safe.warnings.append("Parts of the source looked like video-production notes but were kept, because removing "
                             "them would have left too little to teach from.")
        return safe
    return ScopeResult(markdown=markdown, figures=list(figures), source_format=result.source_format,
                       warnings=["The layout of the source was not recognised; it is used as written."])


def resolve_visual_notes(raw: Sequence[tuple[str, str]], chunks: Sequence[SourceChunk], *,
                         drop_unresolved: bool = False) -> list[VisualNote]:
    """``VisualNote``s with ``near_chunk_id``: the first chunk (in document order, never going back)
    whose own heading is the anchor, else the first chunk under the anchor heading at any level, else the
    first chunk containing the anchor text. (A segment titled like its clip, "KIRCHHOFF'S CURRENT LAW"
    under "KIRCHHOFF'S CURRENT LAW", gets its own visual suggestions, not the clip's first section.)"""
    out: list[VisualNote] = []
    cursor = 0
    norm = [[h.strip().lower() for h in c.heading_path] for c in chunks]

    def find(anchor: str, start: int) -> int | None:
        a = anchor.strip().lower()
        if not a:
            return None
        for i in range(start, len(chunks)):
            if norm[i] and norm[i][-1] == a:
                return i
        for i in range(start, len(chunks)):
            if a in norm[i]:
                return i
        for i in range(start, len(chunks)):
            if a in chunks[i].text.lower():
                return i
        return None

    for anchor, text in raw:
        idx = find(anchor, cursor)
        if idx is None:
            idx = find(anchor, 0)
        if idx is None and drop_unresolved:
            continue
        if idx is not None:
            cursor = idx
        n = len(out) + 1
        out.append(VisualNote(id=f"v{n:04d}" if n < 10000 else f"v{n:05d}",
                              near_chunk_id=chunks[idx].id if idx is not None else None,
                              text=_clip(text, VISUAL_NOTE_CHARS)))
        if len(out) >= MAX_VISUAL_NOTES:
            break
    return out


_CATEGORY_LABELS: dict[str, str] = {
    "person": "SME/author details",
    "duration": "durations",
    "timecode": "timecodes",
    "admin": "course/admin details",
    "production_note": "production notes",
    "structure": "clip scaffolding",
    "duplicate": "duplicates",
}


def scope_summary(excluded: Sequence[ExcludedItem], *, sections: int, visual_notes: int, source_format: str) -> str:
    """One log line about what was ignored; never contains the excluded values themselves."""
    if not excluded and not visual_notes:
        return ""
    counts = Counter(e.category for e in excluded)
    cats = ", ".join(_CATEGORY_LABELS.get(c, c) for c, _ in counts.most_common())
    head = "Read the source as a video script. " if source_format == "sme_script" else ""
    ignored = f"Ignored {len(excluded)} non-teaching item(s) ({cats}); " if excluded else ""
    tail = f"kept {sections} content section(s) and {visual_notes} visual suggestion(s)."
    text = head + ignored + tail
    return text[:1].upper() + text[1:]


def excluded_counts(excluded: Sequence[ExcludedItem]) -> dict[str, int]:
    """``{category: n}`` (for generation_meta; no values)."""
    return dict(Counter(e.category for e in excluded))
