"""truth_table: a logic gate and its truth table filled row by row."""

from __future__ import annotations

import itertools
import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ._base import SceneTemplate, TemplateParams, TitleText

Gate = Literal["AND", "OR", "NOT", "NAND", "NOR", "XOR", "XNOR", "NONE"]
MAX_ROWS = 16


def gate_output(gate: str, bits: list[int]) -> int:
    """Output of ``gate`` for input ``bits`` (multi-input gates fold over all inputs)."""
    ones = sum(bits)
    if gate == "NOT":
        return 1 - bits[0]
    base = {
        "AND": int(ones == len(bits)),
        "OR": int(ones > 0),
        "XOR": ones % 2,
        "NAND": int(ones != len(bits)),
        "NOR": int(ones == 0),
        "XNOR": 1 - ones % 2,
    }
    return base[gate]


class TruthRow(TemplateParams):
    inputs: list[Literal[0, 1]] = Field(min_length=1, max_length=4)
    output: Literal[0, 1]


class TruthTableParams(TemplateParams):
    title: TitleText = ""
    gate: Gate = Field(default="NONE", description="gate symbol to draw; NONE = table only (give rows)")
    inputs: list[str] = Field(min_length=1, max_length=4, description="input names, e.g. A, B")
    output_name: str = Field(default="Y", min_length=1, max_length=6)
    expression: str = Field(default="", max_length=40, description="plain-text expression, e.g. Y = A · B")
    rows: list[TruthRow] = Field(default_factory=list, max_length=MAX_ROWS, description="rows in order ([] = all rows of the gate)")
    rows_per_step: int = Field(default=1, ge=1, le=8, description="rows filled per beat")

    @model_validator(mode="after")
    def _semantics(self) -> TruthTableParams:
        for name in self.inputs:
            if not 1 <= len(name) <= 4:
                raise ValueError("input names must be 1-4 characters")
        n = len(self.inputs)
        if self.gate == "NOT" and n != 1:
            raise ValueError("a NOT gate has exactly one input")
        if self.gate not in ("NOT", "NONE") and n < 2:
            raise ValueError(f"an {self.gate} gate needs at least two inputs")
        if self.gate == "NONE" and not self.rows:
            raise ValueError("give the rows when gate is NONE")
        if not self.rows and 2**n > MAX_ROWS:
            raise ValueError(f"too many rows ({2**n}); use at most {int(math.log2(MAX_ROWS))} inputs")
        for i, row in enumerate(self.rows):
            if len(row.inputs) != n:
                raise ValueError(f"rows[{i}] has {len(row.inputs)} inputs, expected {n}")
            if self.gate != "NONE" and gate_output(self.gate, list(row.inputs)) != row.output:
                raise ValueError(f"rows[{i}] output {row.output} is wrong for {self.gate}{tuple(row.inputs)}")
        return self

    def all_rows(self) -> list[tuple[list[int], int]]:
        """Explicit rows, or every input combination of the gate in binary order."""
        if self.rows:
            return [(list(r.inputs), int(r.output)) for r in self.rows]
        combos = itertools.product((0, 1), repeat=len(self.inputs))
        return [(list(bits), gate_output(self.gate, list(bits))) for bits in combos]


class TruthTableTemplate(SceneTemplate):
    name = "truth_table"
    title = "Logic gate truth table"
    description = (
        "A logic gate symbol (AND, OR, NOT, NAND, NOR, XOR, XNOR) beside its truth table; each beat fills the next "
        "row(s) and shows those input/output values on the gate's wires. Rows are generated automatically from the "
        "gate, or given explicitly for any Boolean function. Use for digital logic and Boolean algebra."
    )
    steps_hint = "steps = ceil(number of rows / rows_per_step); a 2-input gate has 4 rows"
    params_model = TruthTableParams
    scene_file = "truth_table.py"
    example_params = {
        "title": "The AND gate",
        "gate": "AND",
        "inputs": ["A", "B"],
        "output_name": "Y",
        "expression": "Y = A · B",
        "rows": [],
        "rows_per_step": 1,
    }

    def step_count(self, params: BaseModel) -> int:
        model = self.coerce(params)
        return math.ceil(len(model.all_rows()) / model.rows_per_step)

    def scene_data(self, params: TruthTableParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        data = params.model_dump(mode="json")
        data["table"] = [{"inputs": bits, "output": out} for bits, out in params.all_rows()]
        data["step_total"] = self.step_count(params)
        return data
