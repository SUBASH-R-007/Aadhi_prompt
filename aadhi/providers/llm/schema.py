"""Pydantic model -> provider JSON schema (Gemini ``response_json_schema`` / OpenAI ``json_schema`` /
Anthropic ``output_config.format``).

Conversion rules
----------------
* ``$ref``/``$defs`` are inlined (sibling keywords such as a field ``description`` win over the
  referenced definition). Recursive models are rejected.
* Optional fields stay ``anyOf: [{...}, {"type": "null"}]`` (every provider accepts it; the
  google-genai SDK maps it to ``nullable``).
* ``title``/``default``/``examples`` are dropped; ``const`` becomes a one-value ``enum``;
  ``exclusiveMinimum/Maximum`` become inclusive bounds (integers are shifted by one); formats are
  kept only when the provider understands them. Descriptions, enums, string/array/number
  bounds and patterns are kept.
* OpenAI and Anthropic objects get ``additionalProperties: false`` (Anthropic requires it on every
  object).
* Anthropic structured outputs reject numeric, string-length, pattern and array-size constraints:
  they are removed and restated as a short hint at the end of the node's ``description`` (e.g.
  "At most 300 characters.", "Between 1 and 6.", "1 to 8 items."), so the model still sees them;
  ``run_structured`` re-validates with pydantic and re-asks on a violation.

``assert_llm_compatible`` rejects constructs that structured-output modes handle badly or not at
all: ``oneOf``/``discriminator`` (tagged unions), multi-branch ``anyOf`` (plain unions),
``prefixItems`` (tuples), ``additionalProperties: true`` or a schema (open dicts),
``patternProperties``, ``allOf`` with several members, ``not``/``if``, untyped values (``Any``) and
recursion.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

PROVIDERS = ("gemini", "openai", "anthropic", "fake")

_DROP_KEYS = frozenset(
    {"title", "default", "examples", "example", "$comment", "readOnly", "writeOnly", "deprecated", "$schema", "$id"}
)
_FORBIDDEN_KEYS = {
    "oneOf": "tagged/plain unions (oneOf) are not supported; use separate optional fields",
    "discriminator": "discriminated unions are not supported",
    "prefixItems": "tuples (prefixItems) are not supported; use a list or a small model",
    "patternProperties": "patternProperties (open dicts) are not supported",
    "not": "'not' schemas are not supported",
    "if": "conditional schemas are not supported",
    "dependentSchemas": "dependentSchemas is not supported",
    "unevaluatedProperties": "unevaluatedProperties is not supported",
    "additionalItems": "additionalItems is not supported",
    "contains": "'contains' is not supported",
}
_TYPE_KEYS = ("type", "enum", "const", "anyOf", "allOf", "$ref", "properties")
_FORMATS = {
    "gemini": frozenset({"date-time", "date", "time"}),
    "fake": frozenset({"date-time", "date", "time"}),
    "openai": frozenset({"date-time", "date", "time", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}),
    "anthropic": frozenset(
        {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
    ),
}
_CLOSED_OBJECTS = frozenset({"openai", "anthropic"})  # every object gets additionalProperties: false
# Constraint keywords Anthropic structured outputs reject (restated in the description instead).
_ANTHROPIC_HINTED = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
     "pattern", "minItems", "maxItems", "uniqueItems"}
)


class LLMSchemaError(ValueError):
    """The model cannot be used as an LLM structured-output schema."""


def _defs_name(ref: str) -> str:
    if not ref.startswith("#/$defs/"):
        raise LLMSchemaError(f"unsupported $ref {ref!r} (only local #/$defs references)")
    return ref[len("#/$defs/") :]


def _raw_schema(model: type[BaseModel]) -> dict[str, Any]:
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"expected a pydantic model class, got {model!r}")
    return model.model_json_schema(mode="validation")


def _deref(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...], path: str) -> tuple[dict, tuple]:
    """Resolve a ``$ref`` node (merging siblings) and return it with the updated ref stack."""
    name = _defs_name(node["$ref"])
    if name in stack:
        chain = " -> ".join((*stack, name))
        raise LLMSchemaError(f"{path or '<root>'}: recursive model ({chain}) is not supported")
    if name not in defs:
        raise LLMSchemaError(f"{path or '<root>'}: unknown $ref {node['$ref']!r}")
    merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
    return merged, (*stack, name)


def _merge_all_of(node: dict[str, Any], path: str) -> dict[str, Any]:
    members = node.get("allOf") or []
    if len(members) != 1:
        raise LLMSchemaError(f"{path or '<root>'}: allOf with {len(members)} members is not supported")
    merged = {**members[0], **{k: v for k, v in node.items() if k != "allOf"}}
    return merged


def _collect_problems(node: Any, defs: dict[str, Any], stack: tuple[str, ...], path: str, out: list[str]) -> None:
    if not isinstance(node, dict):
        return
    try:
        if "$ref" in node:
            node, stack = _deref(node, defs, stack, path)
        if "allOf" in node:
            node = _merge_all_of(node, path)
            if "$ref" in node:
                node, stack = _deref(node, defs, stack, path)
    except LLMSchemaError as exc:
        out.append(str(exc))
        return
    where = path or "<root>"
    for key, reason in _FORBIDDEN_KEYS.items():
        if key in node:
            out.append(f"{where}: {reason}")
    addl = node.get("additionalProperties")
    if addl is True or isinstance(addl, dict):
        out.append(f"{where}: open dicts (additionalProperties) are not supported; use a list of key/value models")
    if node.get("type") == "object" and "properties" not in node and addl is not False:
        out.append(f"{where}: objects without declared properties are not supported")
    if isinstance(node.get("type"), list):
        out.append(f"{where}: multi-type values are not supported")
    if not any(k in node for k in (*_TYPE_KEYS, "oneOf")):
        out.append(f"{where}: untyped value (Any) is not supported; give the field a concrete type")
    branches = node.get("anyOf")
    if isinstance(branches, list):
        non_null = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
        if len(non_null) > 1:
            out.append(f"{where}: unions are not supported (only Optional[X])")
        for b in branches:
            _collect_problems(b, defs, stack, path, out)
    for name, sub in (node.get("properties") or {}).items():
        _collect_problems(sub, defs, stack, f"{path}.{name}" if path else name, out)
    if isinstance(node.get("items"), dict):
        _collect_problems(node["items"], defs, stack, f"{path}[]", out)
    for i, sub in enumerate(node.get("prefixItems") or []):
        _collect_problems(sub, defs, stack, f"{path}[{i}]", out)


def llm_compatibility_problems(model: type[BaseModel]) -> list[str]:
    """Every reason ``model`` cannot be used as a structured-output schema (``[]`` = compatible)."""
    raw = _raw_schema(model)
    out: list[str] = []
    _collect_problems(raw, raw.get("$defs") or {}, (), "", out)
    return list(dict.fromkeys(out))


def assert_llm_compatible(model: type[BaseModel]) -> None:
    """Raise ``LLMSchemaError`` listing every incompatible construct in ``model``'s JSON schema."""
    problems = llm_compatibility_problems(model)
    if problems:
        raise LLMSchemaError(f"{model.__name__} is not LLM-compatible:\n- " + "\n- ".join(problems))


def _fmt_num(value: Any) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def _count(n: Any, noun: str) -> str:
    return f"{_fmt_num(n)} {noun}" + ("" if n == 1 else "s")


def _range_hint(lo: Any, hi: Any, noun: str) -> str:
    """"Exactly 3 items." / "1 to 8 items." / "At most 300 characters." / "At least 1 character."."""
    if lo is not None and hi is not None:
        return f"Exactly {_count(lo, noun)}." if lo == hi else f"{_fmt_num(lo)} to {_count(hi, noun)}."
    if hi is not None:
        return f"At most {_count(hi, noun)}."
    return f"At least {_count(lo, noun)}." if lo is not None else ""


def _bounds_hint(node: dict[str, Any]) -> str:
    """Numeric bounds as text: "Between 1 and 6.", "Greater than 0 and less than 1.", "At most 9."."""
    lo, lo_open = node.get("minimum"), False
    if "exclusiveMinimum" in node:
        lo, lo_open = node["exclusiveMinimum"], True
    hi, hi_open = node.get("maximum"), False
    if "exclusiveMaximum" in node:
        hi, hi_open = node["exclusiveMaximum"], True
    if lo is not None and hi is not None and not lo_open and not hi_open:
        return f"Between {_fmt_num(lo)} and {_fmt_num(hi)}."
    parts = []
    if lo is not None:
        parts.append(f"{'greater than' if lo_open else 'at least'} {_fmt_num(lo)}")
    if hi is not None:
        parts.append(f"{'less than' if hi_open else 'at most'} {_fmt_num(hi)}")
    text = " and ".join(parts)
    return f"{text[:1].upper()}{text[1:]}." if text else ""


def constraint_hint(node: dict[str, Any]) -> str:
    """Human-readable restatement of the constraint keywords Anthropic does not accept ("" if none)."""
    hints = [
        _bounds_hint(node),
        f"A multiple of {_fmt_num(node['multipleOf'])}." if "multipleOf" in node else "",
        _range_hint(node.get("minLength"), node.get("maxLength"), "character"),
        f"Must match the regular expression {node['pattern']}." if "pattern" in node else "",
        _range_hint(node.get("minItems"), node.get("maxItems"), "item"),
        "Items must be unique." if node.get("uniqueItems") else "",
    ]
    return " ".join(h for h in hints if h)


def _with_hint(description: Any, hint: str) -> str:
    text = str(description or "").rstrip()
    if not text:
        return hint
    return f"{text}{'' if text[-1] in '.!?:;' else '.'} {hint}"


def _convert(node: dict[str, Any], defs: dict[str, Any], provider: str, stack: tuple[str, ...], path: str) -> dict:
    if "$ref" in node:
        node, stack = _deref(node, defs, stack, path)
    if "allOf" in node:
        node = _merge_all_of(node, path)
        if "$ref" in node:
            node, stack = _deref(node, defs, stack, path)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS or key == "$defs":
            continue
        if provider == "anthropic" and key in _ANTHROPIC_HINTED:
            continue  # restated in the description below
        if key == "properties":
            out["properties"] = {
                name: _convert(sub, defs, provider, stack, f"{path}.{name}" if path else name)
                for name, sub in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            out["items"] = _convert(value, defs, provider, stack, f"{path}[]")
        elif key == "anyOf":
            out["anyOf"] = [_convert(b, defs, provider, stack, path) for b in value]
        elif key == "const":
            out["enum"] = [value]
        elif key == "format":
            if value in _FORMATS[provider]:
                out["format"] = value
        elif key in ("exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
            if provider == "fake":  # the synthesiser understands them; real providers get inclusive bounds
                out[key] = value
        elif key == "uniqueItems":
            continue
        elif key == "additionalProperties":
            if value is False and provider in _CLOSED_OBJECTS:
                out[key] = False
        elif key == "required":
            out["required"] = list(value)
        elif key in _FORBIDDEN_KEYS:
            raise LLMSchemaError(f"{path or '<root>'}: {_FORBIDDEN_KEYS[key]}")
        else:
            out[key] = copy.deepcopy(value)
    is_int = node.get("type") == "integer"
    if "exclusiveMinimum" in node and provider not in ("fake", "anthropic"):
        lo = node["exclusiveMinimum"]
        lo = lo + 1 if is_int and isinstance(lo, int) else lo
        out["minimum"] = max(out.get("minimum", lo), lo)
    if "exclusiveMaximum" in node and provider not in ("fake", "anthropic"):
        hi = node["exclusiveMaximum"]
        hi = hi - 1 if is_int and isinstance(hi, int) else hi
        out["maximum"] = min(out.get("maximum", hi), hi)
    if provider == "anthropic":
        hint = constraint_hint(node)
        if hint:
            out["description"] = _with_hint(out.get("description"), hint)
    if "anyOf" in out and len(out["anyOf"]) == 1:  # degenerate union
        only = out.pop("anyOf")[0]
        out = {**only, **out}
    if "properties" in out:
        out.setdefault("type", "object")
        props = out["properties"]
        out["required"] = [r for r in out.get("required", []) if r in props]
        if not out["required"]:
            out.pop("required")
        if provider in _CLOSED_OBJECTS:
            out["additionalProperties"] = False
    if "enum" in out and "type" not in out:
        sample = out["enum"][0] if out["enum"] else ""
        out["type"] = (
            "boolean" if isinstance(sample, bool)
            else "integer" if isinstance(sample, int)
            else "number" if isinstance(sample, float)
            else "string"
        )
    return out


@lru_cache(maxsize=512)
def _cached_schema(model: type[BaseModel], provider: str) -> dict[str, Any]:
    assert_llm_compatible(model)
    raw = _raw_schema(model)
    return _convert(raw, raw.get("$defs") or {}, provider, (), "")


def to_provider_schema(model: type[BaseModel], provider: str) -> dict[str, Any]:
    """JSON schema for ``model`` in the dialect of ``provider`` (``gemini`` | ``openai`` | ``anthropic`` | ``fake``).

    Raises ``LLMSchemaError`` when the model is not LLM-compatible (see module docstring).
    Returns a fresh copy (callers may mutate it).
    """
    key = (provider or "").lower()
    if key not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r} (expected one of {', '.join(PROVIDERS)})")
    return copy.deepcopy(_cached_schema(model, key))


def schema_name(model: type[BaseModel]) -> str:
    """OpenAI-safe schema name (``[A-Za-z0-9_-]{1,64}``)."""
    name = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in model.__name__)
    return (name or "response")[:64]
