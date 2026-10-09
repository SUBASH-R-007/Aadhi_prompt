"""matrix_ops: matrices with row/column/cell highlights and step-by-step products."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ._base import NoteText, SceneTemplate, TemplateParams, TitleText, tex_validator


class MatrixRow(TemplateParams):
    values: list[str] = Field(min_length=1, max_length=5, description="entries as numbers or short LaTeX, e.g. 3, -1, a_{11}")

    @field_validator("values")
    @classmethod
    def _entries(cls, values: list[str]) -> list[str]:
        for v in values:
            if not v or len(v) > 16:
                raise ValueError("matrix entries must be 1-16 characters")
            tex_validator(v)
        return values


class MatrixDef(TemplateParams):
    name: str = Field(default="", max_length=4, description="name shown above, e.g. A")
    rows: list[MatrixRow] = Field(min_length=1, max_length=5)
    hidden: bool = Field(default=False, description="entries start hidden (e.g. a result filled by product_entry)")

    @model_validator(mode="after")
    def _rect(self) -> MatrixDef:
        if len({len(r.values) for r in self.rows}) != 1:
            raise ValueError("every row of a matrix must have the same number of entries")
        return self

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.rows), len(self.rows[0].values)


class MatrixStep(TemplateParams):
    action: Literal[
        "show", "highlight_row", "highlight_col", "highlight_cell", "product_entry", "reveal_cell", "reveal_all", "note"
    ] = Field(
        description="show: reveal matrices[matrix]; highlight_*: box a row/column/cell of matrices[matrix]; "
        "product_entry: box row `row` of matrix 0 and column `col` of matrix 1 and reveal cell (row, col) of matrix 2; "
        "reveal_cell / reveal_all: reveal hidden entries of matrices[matrix]; note: caption only"
    )
    matrix: int = Field(default=0, ge=0, le=2)
    row: int = Field(default=0, ge=0, le=4)
    col: int = Field(default=0, ge=0, le=4)
    text: str = Field(default="", max_length=60, description="working shown below, e.g. 1·5 + 2·7 = 19")
    note: NoteText = ""


class MatrixOpsParams(TemplateParams):
    title: TitleText = ""
    matrices: list[MatrixDef] = Field(min_length=1, max_length=3)
    operators: list[str] = Field(default_factory=list, max_length=2, description="symbols between matrices, e.g. ×, =")
    steps: list[MatrixStep] = Field(min_length=1, max_length=10, description="one step per beat")

    @field_validator("operators")
    @classmethod
    def _ops(cls, ops: list[str]) -> list[str]:
        for op in ops:
            if not op or len(op) > 3:
                raise ValueError("operators must be 1-3 characters, e.g. ×, +, =")
        return ops

    @model_validator(mode="after")
    def _semantics(self) -> MatrixOpsParams:
        if self.operators and len(self.operators) != len(self.matrices) - 1:
            raise ValueError("operators needs exactly one symbol between consecutive matrices")
        shapes = [m.shape for m in self.matrices]
        for i, step in enumerate(self.steps):
            if step.action == "note":
                continue
            if step.action == "product_entry":
                if len(self.matrices) < 3:
                    raise ValueError("product_entry needs three matrices: left, right and result")
                (r0, c0), (r1, c1), (r2, c2) = shapes[0], shapes[1], shapes[2]
                if c0 != r1 or (r2, c2) != (r0, c1):
                    raise ValueError("product_entry needs compatible shapes: (m x n)(n x p) = (m x p)")
                if step.row >= r0 or step.col >= c1:
                    raise ValueError(f"steps[{i}]: row/col outside the result matrix")
                continue
            if step.matrix >= len(self.matrices):
                raise ValueError(f"steps[{i}].matrix {step.matrix} does not exist")
            rows, cols = shapes[step.matrix]
            if step.action in ("highlight_row", "highlight_cell", "reveal_cell") and step.row >= rows:
                raise ValueError(f"steps[{i}].row {step.row} is outside matrix {step.matrix}")
            if step.action in ("highlight_col", "highlight_cell", "reveal_cell") and step.col >= cols:
                raise ValueError(f"steps[{i}].col {step.col} is outside matrix {step.matrix}")
        return self


class MatrixOpsTemplate(SceneTemplate):
    name = "matrix_ops"
    title = "Matrix operations"
    description = (
        "Up to three matrices side by side (e.g. A × B = C) with step-by-step highlights of rows, columns and "
        "cells; product_entry walks through matrix multiplication one result entry at a time with the working "
        "shown below. Use for matrix multiplication, determinants, transformations, systems of equations."
    )
    steps_hint = "one step per item of `steps`"
    params_model = MatrixOpsParams
    scene_file = "matrix_ops.py"
    example_params = {
        "title": "Multiplying matrices",
        "matrices": [
            {"name": "A", "rows": [{"values": ["1", "2"]}, {"values": ["3", "4"]}]},
            {"name": "B", "rows": [{"values": ["5", "6"]}, {"values": ["7", "8"]}]},
            {"name": "C", "rows": [{"values": ["19", "22"]}, {"values": ["43", "50"]}], "hidden": True},
        ],
        "operators": ["×", "="],
        "steps": [
            {"action": "note", "note": "Multiply A by B to get C"},
            {"action": "product_entry", "row": 0, "col": 0, "text": "1·5 + 2·7 = 19", "note": "Row 1 of A times column 1 of B"},
            {"action": "product_entry", "row": 0, "col": 1, "text": "1·6 + 2·8 = 22", "note": "Row 1 times column 2"},
            {"action": "reveal_all", "matrix": 2, "note": "The remaining entries follow the same rule"},
        ],
    }

    def step_count(self, params: BaseModel) -> int:
        return len(self.coerce(params).steps)
