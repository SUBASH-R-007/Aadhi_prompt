"""Import heavy SDK modules off the event loop.

A cold ``import google.genai`` takes about a second and ``import edge_tts`` loads the certifi CA
bundle; doing either inside ``async def`` code blocks the worker's event loop. ``import_off_loop``
performs the first import in a worker thread and remembers the fully initialised module, so later
calls are a dictionary lookup.
"""

from __future__ import annotations

import asyncio
import importlib
from types import ModuleType

_READY: dict[str, ModuleType] = {}


async def import_off_loop(name: str) -> ModuleType:
    """Return module ``name``, importing it in a worker thread the first time.

    Only modules whose import has *finished* are cached (``sys.modules`` can briefly hold a
    partially initialised module while another thread is importing it). Import errors propagate.
    """
    module = _READY.get(name)
    if module is not None:
        return module
    module = await asyncio.to_thread(importlib.import_module, name)
    _READY[name] = module  # single dict assignment: atomic under the GIL
    return module


def imported(name: str) -> ModuleType | None:
    """The module if ``import_off_loop`` already finished importing it, else ``None``."""
    return _READY.get(name)
