"""The committed pair-kerning table still bounds what Chromium draws (aadhi/compose/dynamic/kerning.py; B2b review F1,
F2): every Latin face is measured again in real Chromium, pair by pair, and no pair may be drawn wider than the
table's bound for it, nor any pair narrower than the face's ``most_negative``. On the machine the table was made on
the measurement is the table itself (``python -m aadhi.compose.dynamic.kerning --check``); on another platform it may
differ only in the conservative direction (a stored value is never below a measured one).
"""

from __future__ import annotations

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import kerning as kn
from tests.compose.dynamic.helpers import missing

pytestmark = pytest.mark.slow


def test_the_table_bounds_every_pair_chromium_draws() -> None:
    if not all(fc.web_path(r).is_file() for r in kn.table_faces()):
        missing("web/vendor is not vendored (npm ci): the faces the table is measured on")
    measured = kn.measure()
    assert set(measured) == set(kn.load())
    assert kn.covers(kn.load(), measured) == []
