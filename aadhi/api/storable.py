"""Request bodies and documents that must be storable JSON (``aadhi.schemas.jsonsafe``).

FastAPI decodes JSON bodies with Python's ``json``, which accepts ``NaN``/``Infinity``, reads
``1e999`` as ``inf`` and keeps lone UTF-16 surrogates from ``"\\ud800"`` escapes. Stored, any of these
makes every later read of the version a 500 (and PostgreSQL JSONB refuses the write), so
screenplay, plan and options writes refuse them with the standard 422 ``validation`` envelope:

* ``StorableBody``: base for JSON request bodies. Ordinary field validation runs first (a non-finite
  number in a typed field keeps pydantic's precise ``finite_number`` error); then the raw body is
  checked for values JSON cannot store anywhere, including free-form fields.
* ``ensure_storable``: the same check on a document right before it is written (defence in depth).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Self

from pydantic import BaseModel, ModelWrapValidatorHandler, model_validator

from ..schemas.jsonsafe import dotted, unstorable
from .errors import ApiException

__all__ = ["StorableBody", "ensure_storable"]


class StorableBody(BaseModel):
    """A JSON request body whose content will be stored or echoed (refuses unstorable values)."""

    @model_validator(mode="wrap")
    @classmethod
    def _storable(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        result = handler(data)
        if isinstance(data, (dict, list)):
            found = unstorable(data)
            if found is not None:
                path, message = found
                raise ValueError(f"{message} (at {dotted(path)})")
        return result


def ensure_storable(document: Any, loc: Sequence[Any] = ("body",)) -> None:
    """422 ``validation`` (at ``loc`` + the value's path) when ``document`` holds a value JSON cannot store."""
    found = unstorable(document)
    if found is not None:
        path, message = found
        raise ApiException(422, "validation", [{"loc": [*loc, *path], "msg": message, "type": "value_error"}])
