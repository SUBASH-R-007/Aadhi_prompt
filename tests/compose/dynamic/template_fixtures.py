"""The fixture timelines of the B2 scene templates (B2c; B2 plan §A step 3, §G): where they live and what makes one
valid. Each template built in B2 ships at least one fixture per format it renders, a complete Dynamic timeline in
``tests/compose/dynamic/fixtures/templates/<template>.<format>[.<case>].json`` that names it in ``look.template`` and
carries the visual events it draws (``TimedScene.events``). They are run by:

* ``tests/compose/dynamic/test_template_fixtures.py`` (fast): each one validates as a ``Timeline``, is a Dynamic
  timeline of the format its name says, names its template in at least one scene, keeps its events inside their
  scenes with targets in the §1.7 grammar that the template consumes, freezes the background of every scene of a dense
  template from its start (R-55, as the resolver writes it), and every template with a fixture has one per format;
* ``tests/determinism/test_template_fixtures.py`` (slow, real Chromium): 1x / 3x / reverse frame hashes identical
  (§7.4 test (a); plan §P condition 3), no page error, no timer-guard trip.

The template's jsdom tests (``web/tests/dynamic/templates/<id>.test.js``) read the same files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aadhi.config import ROOT_DIR

FIXTURE_DIR = ROOT_DIR / "tests" / "compose" / "dynamic" / "fixtures" / "templates"
FORMATS = ("landscape", "portrait")


def fixture_paths() -> list[Path]:
    """Every template fixture, sorted by name (empty before the first template lands)."""
    return sorted(FIXTURE_DIR.glob("*.json")) if FIXTURE_DIR.is_dir() else []


def fixture_name(path: Path) -> tuple[str, str, str]:
    """``(template, format, case)`` from ``<template>.<format>[.<case>].json`` (``ValueError`` for another name)."""
    parts = path.name[: -len(".json")].split(".")
    if len(parts) not in (2, 3) or parts[1] not in FORMATS or not parts[0]:
        raise ValueError(f"{path.name}: a template fixture is named <template>.<landscape|portrait>[.<case>].json")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ""


def load_fixture(path: Path) -> dict[str, Any]:
    """The fixture's JSON (a ``Timeline`` dump)."""
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: not a timeline object")
    return data
