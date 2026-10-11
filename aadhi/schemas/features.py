"""Schema v3 (Dynamic video, design §6.7 and §8.2): feature names, the reader-version marker and the usage walk.

Every v3 field exists from B0 as an *inert* field: parsed, validated and preserved by every transform, and left
out of the JSON at its default, so classic documents serialise exactly as before. Three rules make the inert
fields safe while their features ship one batch at a time:

* **Reader-version marker.** A document (screenplay, timeline or asset manifest) that uses any v3 field or enum
  value is written with ``schema_version = 3``; classic documents keep their version (2). ``check_reader_version``
  refuses a document whose ``schema_version`` is above ``READER_SCHEMA_VERSION`` with ``NewerDocumentError``, which
  every loader raises (the models call it before validating): the API answers 409 ``newer_document`` and a job fails
  with ``error_code="newer_document"`` instead of silently dropping the fields it does not know and writing the
  stripped document back. Because B0 knows every v3 field and refuses newer documents, B0 is the rollback floor.
  ``NewerDocumentError`` is deliberately *not* a ``ValueError``: pydantic lets it through unwrapped, so no
  ``except ValidationError`` turns it into "unreadable document" or a 422.
* **Write gates.** ``DYNAMIC_FEATURES`` (``Settings.dynamic_features``) lists the features a server accepts in
  writes; ``PUT /screenplay`` and import refuse a document that uses another one with 422 ``feature_unavailable``
  (``aadhi.api.screenplay_service.require_features``).
* **One source of truth.** Each model declares its v3 fields (``V3_FIELDS``: field -> feature), its v3 enum values
  (``V3_VALUES``: field -> value -> feature) and, for a model that is v3 as a whole, ``V3_MODEL``. A v3 field counts
  as used when it would be serialised (its ``exclude_if`` does not drop it). ``features_used`` walks a model tree.

Pure module (pydantic only): the schemas, the API, the workers and the config import it.
"""

from __future__ import annotations

import typing
from collections.abc import Mapping
from enum import Enum
from functools import cache
from typing import Any

from pydantic import BaseModel

__all__ = [
    "CLASSIC_SCHEMA_VERSION",
    "DYNAMIC_FEATURES",
    "FEATURE_LABELS",
    "READER_SCHEMA_VERSION",
    "V3_SCHEMA_VERSION",
    "NewerDocumentError",
    "check_reader_version",
    "disabled_features",
    "features_used",
    "marked_version",
    "uses_v3",
]

CLASSIC_SCHEMA_VERSION = 2  # screenplays, timelines and manifests without any v3 field
V3_SCHEMA_VERSION = 3  # a document that uses any v3 field or enum value
READER_SCHEMA_VERSION = 3  # the highest schema_version this build reads; newer documents are refused

# Feature names accepted in DYNAMIC_FEATURES (frozen in B0). Each v3 field belongs to exactly one.
DYNAMIC_FEATURES = (
    "presentation",  # Screenplay.presentation and the Dynamic annotations: look, speaker, answers, builds_on,
    #                  colour_is_content, visual_intent, sequence, the new narrative roles (B4)
    "items_v3",  # board item kinds question / stat / step / quote / pair and their fields (B4)
    "cuts",  # Beat.cuts: narration word-range cuts (B5)
    "overlays",  # Beat.overlays: B-roll shown over the narration (B6)
    "clip",  # ClipScene: teacher-inserted external clips (B6)
    "voice_override",  # Beat.voice_override_asset_key: the teacher's own recorded narration (B6)
)

# Plain words for the 422 message (what the teacher used that this server does not offer yet).
FEATURE_LABELS: dict[str, str] = {
    "presentation": "Dynamic video styling",
    "items_v3": "The new board item kinds (question, stat, step, quote, pair)",
    "cuts": "Narration cuts",
    "overlays": "Clips shown over the narration",
    "clip": "Inserted video clips",
    "voice_override": "Recorded narration",
}


class NewerDocumentError(Exception):
    """A stored or uploaded document was written by a newer build (``schema_version`` above what this one reads).

    Not a ``ValueError`` on purpose (see the module docstring). ``code`` is the API error code and job error code.
    """

    code = "newer_document"

    def __init__(self, document: str, version: int, reader: int = READER_SCHEMA_VERSION) -> None:
        super().__init__(
            f"This {document} was saved by a newer version of Aadhi EduEngine (schema version {version}); "
            f"this server reads up to version {reader}. Update the server before opening it."
        )
        self.document = document
        self.version = version
        self.reader = reader


def _as_version(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def check_reader_version(data: Any, document: str) -> None:
    """Raise ``NewerDocumentError`` when ``data`` (a raw document) has a ``schema_version`` this build cannot read.

    Anything that is not a mapping, or has no usable ``schema_version``, passes (the schema validates it)."""
    if not isinstance(data, Mapping):
        return
    version = _as_version(data.get("schema_version"))
    if version is not None and version > READER_SCHEMA_VERSION:
        raise NewerDocumentError(document, version)


def _field_used(cls: type[BaseModel], name: str, value: Any) -> bool:
    """A v3 field is used when it would be serialised: its ``exclude_if`` does not drop the value."""
    info = cls.model_fields[name]
    exclude_if = getattr(info, "exclude_if", None)
    if exclude_if is None:  # pragma: no cover - every v3 field has one (tests/schemas/test_v3_schema.py)
        return value is not None
    return not exclude_if(value)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _models_in(tp: Any) -> list[type[BaseModel]]:
    """The pydantic models values of annotation ``tp`` can hold (``Any`` holds plain JSON data only)."""
    if isinstance(tp, type):
        return [tp] if issubclass(tp, BaseModel) else []
    return [m for arg in typing.get_args(tp) if arg is not Ellipsis for m in _models_in(arg)]


_PENDING: set[type[BaseModel]] = set()


@cache
def _may_hold_v3(cls: type[BaseModel]) -> bool:
    """Whether an instance of ``cls`` can use a v3 field or value, itself or in a model inside it (pruning: the walk
    never descends into, e.g., the thousands of timed words of a timeline)."""
    if getattr(cls, "V3_MODEL", None) or getattr(cls, "V3_FIELDS", None) or getattr(cls, "V3_VALUES", None):
        return True
    _PENDING.add(cls)
    try:
        return any(
            m not in _PENDING and _may_hold_v3(m)
            for info in cls.model_fields.values() for m in _models_in(info.annotation)
        )
    finally:
        _PENDING.discard(cls)


@cache
def _scan_plan(cls: type[BaseModel]) -> tuple[tuple[str, str | None, Mapping[str, str] | None, bool], ...]:
    """Per model class: (field, v3 feature, v3 values, worth descending) for the fields the walk must look at."""
    v3_fields: Mapping[str, str] = getattr(cls, "V3_FIELDS", {})
    v3_values: Mapping[str, Mapping[str, str]] = getattr(cls, "V3_VALUES", {})
    plan = []
    for name, info in cls.model_fields.items():
        nested = any(_may_hold_v3(m) for m in _models_in(info.annotation))
        if nested or name in v3_fields or name in v3_values:
            plan.append((name, v3_fields.get(name), v3_values.get(name), nested))
    return tuple(plan)


def _walk(root: BaseModel, *, first_only: bool) -> dict[str, list[tuple[Any, ...]]]:
    found: dict[str, list[tuple[Any, ...]]] = {}

    def add(feature: str, loc: tuple[Any, ...]) -> bool:
        found.setdefault(feature, []).append(loc)
        return first_only

    stack: list[tuple[tuple[Any, ...], Any]] = [((), root)]
    while stack:
        path, obj = stack.pop()
        if isinstance(obj, BaseModel):
            cls = type(obj)
            model_feature = getattr(cls, "V3_MODEL", None)
            if model_feature and add(model_feature, path):
                return found
            children: list[tuple[tuple[Any, ...], Any]] = []
            for name, feature, values, nested in _scan_plan(cls):
                value = getattr(obj, name, None)
                if feature is not None and _field_used(cls, name, value) and add(feature, (*path, name)):
                    return found
                if values is not None and not isinstance(value, (list, dict)):
                    hit = values.get(_enum_value(value))
                    if hit is not None and add(hit, (*path, name)):
                        return found
                if nested and isinstance(value, (BaseModel, list, tuple, dict)):
                    children.append(((*path, name), value))
            stack.extend(reversed(children))
        elif isinstance(obj, (list, tuple)):
            stack.extend(
                ((*path, i), item) for i, item in reversed(list(enumerate(obj)))
                if isinstance(item, (BaseModel, list, tuple, dict))
            )
        elif isinstance(obj, dict):
            stack.extend(
                ((*path, k), item) for k, item in reversed(list(obj.items()))
                if isinstance(item, (BaseModel, list, tuple, dict))
            )
    return found


def features_used(model: BaseModel) -> dict[str, list[tuple[Any, ...]]]:
    """Feature name -> locations (field paths) of every v3 field or enum value ``model`` uses, in walk order (a
    model's own fields before the models inside it; features in the order they are first met)."""
    return _walk(model, first_only=False)


def disabled_features(model: BaseModel, enabled: Any) -> dict[str, list[tuple[Any, ...]]]:
    """The features ``model`` uses that are not in ``enabled`` (``Settings.dynamic_features``), with their locations."""
    allowed = set(enabled or ())
    return {name: locs for name, locs in features_used(model).items() if name not in allowed}


def uses_v3(model: BaseModel) -> bool:
    """True when ``model`` (or anything inside it) uses a v3 field or enum value."""
    return bool(_walk(model, first_only=True))


def marked_version(model: BaseModel, current: int) -> int:
    """The ``schema_version`` a document is written with: 3 when it uses a v3 field, else its own version
    (a 3 without any v3 field left goes back to 2, so the document is classic again)."""
    if uses_v3(model):
        return max(current, V3_SCHEMA_VERSION)
    return CLASSIC_SCHEMA_VERSION if current == V3_SCHEMA_VERSION else current
