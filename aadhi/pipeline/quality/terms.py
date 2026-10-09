"""Terminology, concept names and figure captions (English boards only).

* ``terminology.variant`` (info) — one term written with a hyphen, a space or joined ('machine-learning'
  and 'machine learning') in the emphasised places: keywords, definition terms, table headers,
  headings and labels. When the forms also differ in capitals, this one finding covers both.
* ``terminology.casing`` (info) — the same term capitalised differently in keywords, definition terms,
  headers or headings. The first letter never counts, Title Case is normal in headings and headers, and a
  heading in capitals matches any spelling.
* ``concept.naming_variant`` (info) — one concept (its concept id, or the same title without one) titled
  with the same name written differently in its scenes or in the concept map. Sub-titles such as
  "Ohm's law in practice" are different names and are never compared.
* ``figure.caption_variant`` (info) — one figure shown with captions that say different things.

None of these is fixable by a scene rewrite: terms are the author's content. Casing and spelling
variants on the board carry a safe repair (write every other form as the most used one).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from ...schemas.screenplay import BoardItemKind, BoardScene
from .extract import CASING_POSITIONS, HEADING_POSITIONS, VARIANT_POSITIONS, Facts, Occurrence, plain, scenes_of
from .report import Finding, Repair, lint_issue
from .text import (
    cut,
    dominant,
    form_classes,
    group_key,
    loose,
    phrase_pattern,
    quote,
    replace_outside_markup,
    sep_sig,
    upper_first,
)


def case_sigs(positions: set[str] | frozenset[str], form: str) -> set[str]:
    sigs = set()
    if positions - HEADING_POSITIONS:
        sigs.add(loose(form, False))
    if positions & HEADING_POSITIONS:
        sigs.add(loose(form, True))
    return sigs


def case_compatible(a: set[str], b: set[str]) -> bool:
    return "*" in a or "*" in b or bool(a & b)


def _evidence_scenes(facts: Facts, classes: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in classes[:6]:
        for f in c["forms"][:3]:
            out[f] = [facts.lesson.scene_id(i) for i in c["by_form"][f][:12]]
    return out


def _term_repair(facts: Facts, target: str, occurrences: list[Occurrence]) -> Repair | None:
    """Edits writing each occurrence's form as ``target`` in the field it was found in (first letter kept)."""
    by_path: OrderedDict[tuple[int, tuple[Any, ...]], list[str]] = OrderedDict()
    for o in occurrences:
        if o.scene is None or o.path is None or o.form == target:
            continue
        forms = by_path.setdefault((o.scene, o.path), [])
        if o.form not in forms:
            forms.append(o.form)
    edits: list[dict[str, Any]] = []
    for (index, path), forms in by_path.items():
        if len(edits) >= 40:
            break  # only the first 40 are offered: do not build edits (and patterns) nobody sees
        before = next((v for p, v in facts.scenes[index].fields if p == path), None)
        if before is None:
            continue
        after: str | None = before
        for form in sorted(forms, key=len, reverse=True):
            def repl(m: Any, form: str = form) -> str:
                found = m.group(0)
                if found[:1].isupper() and target[:1].islower():  # a capital first letter never counts: keep it
                    return upper_first(target)
                return target
            after = replace_outside_markup(after, phrase_pattern(form), repl) if after is not None else None
        if after is not None and after != before:
            edits.append({"scene_id": facts.lesson.scene_id(index), "path": list(path), "before": before, "after": after})
    if not edits:
        return None
    return Repair(label=f"Write it as {quote(target)}", edits=edits[:40])


def term_findings(facts: Facts) -> tuple[list[Finding], set[str]]:
    """Variant and casing findings; also the group keys reported (so other checks do not repeat them)."""
    out: list[Finding] = []
    reported: set[str] = set()
    lesson = facts.lesson
    for key, forms in facts.groups.items():
        placed = [f for f, occ in forms.items() if any(o.position in VARIANT_POSITIONS and o.scene is not None for o in occ)]
        if len(placed) >= 2:
            sig = {f: sep_sig(f) for f in placed}  # once per form, not once per compared pair
            classes = form_classes(placed, lambda a, b, sig=sig: sig[a] == sig[b],
                                   lambda f, forms=forms: scenes_of(forms[f], VARIANT_POSITIONS))
            if len(classes) >= 2:
                main = dominant(classes)
                target = classes[main]["forms"][0]
                target_sigs = case_sigs({o.position for o in forms[target]}, target)
                other = next(c for n, c in enumerate(classes) if n != main)
                wrong = [o for f in placed for o in forms[f]
                         if o.position in VARIANT_POSITIONS and f != target
                         and (sig[f] != sig[target] or not case_compatible(case_sigs({o.position}, f), target_sigs))]
                msg = (f"The term {quote(target)} is written in different ways (hyphen, space or joined): "
                       f"{lesson.forms_text(classes)}. Choose one spelling for the whole lesson.")
                issue = lint_issue("terminology.variant", msg, scene_id=lesson.scene_id(other["scenes"][0]))
                out.append(Finding(issue, _term_repair(facts, target, wrong)))
                reported.add(key)
                continue
        cased = [(f, {o.position for o in occ if o.position in CASING_POSITIONS}) for f, occ in forms.items()]
        cased = [(f, pos) for f, pos in cased if pos]
        if len(cased) < 2:
            continue
        sigs = {f: case_sigs(pos, f) for f, pos in cased}
        classes = form_classes([f for f, _ in cased], lambda a, b, sigs=sigs: case_compatible(sigs[a], sigs[b]),
                               lambda f, forms=forms: scenes_of(forms[f], CASING_POSITIONS))
        if len(classes) < 2:
            continue
        main = dominant(classes)
        target = classes[main]["forms"][0]
        other = next(c for n, c in enumerate(classes) if n != main)
        wrong = [o for f, _pos in cased for o in forms[f]
                 if o.position in CASING_POSITIONS and f != target and not case_compatible(case_sigs({o.position}, f), sigs[target])]
        msg = (f"The term {quote(target)} is capitalised differently across the lesson: {lesson.forms_text(classes)}. "
               "Write it the same way everywhere so learners see one term.")
        issue = lint_issue("terminology.casing", msg, scene_id=lesson.scene_id(other["scenes"][0]))
        out.append(Finding(issue, _term_repair(facts, target, wrong)))
        reported.add(key)
    return out, reported


# ---------------------------------------------------------------------------
# Concept names
# ---------------------------------------------------------------------------

def concept_names(facts: Facts) -> OrderedDict[str, dict[str, Any]]:
    """{concept key: {"id", "map_title", "names": [(name, scene index or None)], "scenes"}} in lesson order."""
    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    titles = {c.id: cut(c.title, 80) for c in facts.sp.concept_map}
    for f in facts.scenes:
        cid = getattr(f.scene, "concept_id", None)
        if cid:
            key = f"id:{cid}"
        elif f.title_name:
            key = f"title:{group_key(f.title_name)}"
        else:
            continue
        c = out.setdefault(key, {"id": cid, "map_title": titles.get(cid) if cid else None, "names": [], "scenes": []})
        c["scenes"].append(f.index)
        if f.title_name:
            c["names"].append((f.title_name, f.index))
    return out


def concept_findings(facts: Facts, reported: set[str]) -> list[Finding]:
    out: list[Finding] = []
    lesson = facts.lesson
    for c in concept_names(facts).values():
        names: list[tuple[str, int | None]] = ([(c["map_title"], None)] if c["map_title"] else []) + c["names"]
        if len(names) < 2:
            continue
        by_group: OrderedDict[str, OrderedDict[str, list[int | None]]] = OrderedDict()
        for name, scene in names:
            by_group.setdefault(group_key(name), OrderedDict()).setdefault(name, []).append(scene)
        for gkey, forms in by_group.items():
            if len(forms) < 2 or gkey in reported:
                continue
            sigs = {f: {loose(f, True)} for f in forms}
            classes = form_classes(list(forms), lambda a, b, sigs=sigs: sep_sig(a) == sep_sig(b) and case_compatible(sigs[a], sigs[b]),
                                   lambda f, forms=forms: [s for s in forms[f] if s is not None])
            if len(classes) < 2:
                continue
            main = dominant(classes)
            other = next(cl for n, cl in enumerate(classes) if n != main)
            scene = other["scenes"][0] if other["scenes"] else (c["scenes"][0] if c["scenes"] else None)
            msg = (f"One concept is titled in different ways: {lesson.forms_text(classes)}"
                   + (f" (the concept map calls it {quote(c['map_title'])})" if c["map_title"] else "")
                   + ". Use one name so learners recognise it each time.")
            out.append(Finding(lint_issue("concept.naming_variant", msg,
                                          scene_id=lesson.scene_id(scene) if scene is not None else None)))
            reported.add(gkey)
    return out


# ---------------------------------------------------------------------------
# Figure captions
# ---------------------------------------------------------------------------

def figure_captions(facts: Facts) -> OrderedDict[str, list[tuple[str, int]]]:
    """{figure id: [(caption, scene index)]} for every figure shown with words of its own."""
    out: OrderedDict[str, list[tuple[str, int]]] = OrderedDict()
    for f in facts.scenes:
        scene = f.scene
        if isinstance(scene, BoardScene):
            for item in scene.board:
                if item.kind == BoardItemKind.figure and item.figure_id and (item.caption or "").strip():
                    out.setdefault(item.figure_id, []).append((cut(plain(item.caption or ""), 200), f.index))
        panel = scene.side_panel
        if panel is not None and panel.kind == "figure" and panel.figure_id and (panel.title or "").strip():
            out.setdefault(panel.figure_id, []).append((cut(panel.title, 200), f.index))
    return out


def figure_findings(facts: Facts) -> list[Finding]:
    out: list[Finding] = []
    lesson = facts.lesson
    for figure_id, shown in figure_captions(facts).items():
        variants: OrderedDict[str, tuple[str, list[int]]] = OrderedDict()
        for caption, index in shown:
            entry = variants.setdefault(group_key(caption), (caption, []))
            if index not in entry[1]:
                entry[1].append(index)
        if len(variants) < 2:
            continue
        listed = list(variants.values())
        msg = (f"The same figure ({quote(figure_id, 40)}) is shown with different captions: "
               + "; ".join(f"{lesson.where(scenes)} says {quote(caption)}" for caption, scenes in listed[:3])
               + ". Check that each caption describes the picture.")
        out.append(Finding(lint_issue("figure.caption_variant", msg, scene_id=lesson.scene_id(listed[1][1][0]))))
    return out
