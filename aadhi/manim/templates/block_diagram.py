"""block_diagram: labelled boxes and arrows revealed step by step (pipelines, systems, flows)."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ._base import ColorName, NoteText, SceneTemplate, TemplateParams, TitleText

NODE_ID = r"^[A-Za-z0-9_\-]{1,24}$"


class DiagramNode(TemplateParams):
    id: str = Field(pattern=NODE_ID, description="short unique id used by edges, e.g. input, cpu")
    label: str = Field(min_length=1, max_length=40, description="text inside the box")
    column: int = Field(default=-1, ge=-1, le=6, description="grid column (-1 = automatic layout)")
    row: int = Field(default=-1, ge=-1, le=6, description="grid row (-1 = automatic layout)")
    color: ColorName = "cyan"
    step: int = Field(default=0, ge=0, le=11, description="step (beat index) at which the box appears")


class DiagramEdge(TemplateParams):
    source: str = Field(pattern=NODE_ID)
    target: str = Field(pattern=NODE_ID)
    label: str = Field(default="", max_length=24)
    dashed: bool = False
    step: int = Field(default=0, ge=0, le=11, description="step at which the arrow appears (>= both boxes)")


class BlockDiagramParams(TemplateParams):
    title: TitleText = ""
    direction: Literal["right", "down"] = Field(default="right", description="main flow direction (automatic layout)")
    nodes: list[DiagramNode] = Field(min_length=2, max_length=12)
    edges: list[DiagramEdge] = Field(default_factory=list, max_length=20)
    notes: list[NoteText] = Field(default_factory=list, max_length=12, description="optional caption per step")

    @model_validator(mode="after")
    def _semantics(self) -> BlockDiagramParams:
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("node ids must be unique")
        steps = {n.id: n.step for n in self.nodes}
        for e in self.edges:
            for end in (e.source, e.target):
                if end not in steps:
                    raise ValueError(f"edge {e.source}->{e.target} references unknown node {end!r}")
            if e.source == e.target:
                raise ValueError(f"edge {e.source}->{e.target} connects a node to itself")
            if e.step < max(steps[e.source], steps[e.target]):
                raise ValueError(f"edge {e.source}->{e.target} appears before one of its boxes")
        manual = [n.column >= 0 and n.row >= 0 for n in self.nodes]
        if any(manual) and not all(manual):
            raise ValueError("set column and row for every node, or leave all at -1 for automatic layout")
        if all(manual):
            cells = [(n.column, n.row) for n in self.nodes]
            if len(set(cells)) != len(cells):
                raise ValueError("two nodes share the same column and row")
        used = {n.step for n in self.nodes} | {e.step for e in self.edges}
        total = max(used) + 1
        missing = sorted(set(range(total)) - used)
        if missing:
            raise ValueError(f"steps must be consecutive from 0: nothing appears at step(s) {missing}")
        if len(self.notes) > total:
            raise ValueError(f"notes has {len(self.notes)} entries but there are only {total} steps")
        return self


def auto_layout(nodes: list[DiagramNode], edges: list[DiagramEdge]) -> dict[str, tuple[float, float]]:
    """Layered layout: column = longest path from a source (back edges ignored), row = order in layer."""
    order = [n.id for n in nodes]
    succ: dict[str, list[str]] = {i: [] for i in order}
    for e in edges:
        succ[e.source].append(e.target)
    # Drop back edges (DFS in declaration order) so cycles still lay out left-to-right.
    state: dict[str, int] = {}
    dag: dict[str, list[str]] = {i: [] for i in order}

    def visit(u: str) -> None:
        state[u] = 1
        for v in succ[u]:
            if state.get(v) == 1:
                continue  # back edge
            dag[u].append(v)
            if v not in state:
                visit(v)
        state[u] = 2

    for i in order:
        if i not in state:
            visit(i)
    layer = dict.fromkeys(order, 0)
    for _ in range(len(order)):
        changed = False
        for u in order:
            for v in dag[u]:
                if layer[v] < layer[u] + 1:
                    layer[v] = layer[u] + 1
                    changed = True
        if not changed:
            break
    rows: dict[int, int] = {}
    slot: dict[str, int] = {}
    for i in order:
        col = layer[i]
        slot[i] = rows.get(col, 0)
        rows[col] = rows.get(col, 0) + 1
    tallest = max(rows.values())
    # Centre every layer vertically against the tallest one.
    return {i: (float(layer[i]), slot[i] + (tallest - rows[layer[i]]) / 2) for i in order}


class BlockDiagramTemplate(SceneTemplate):
    name = "block_diagram"
    title = "Block diagram"
    description = (
        "Boxes connected by arrows, revealed step by step: system architectures, data pipelines, process "
        "flows, control loops, compiler stages, cause-effect chains. Each box/arrow has the step (beat) at which "
        "it appears; layout is automatic (layered left-to-right or top-down) unless column/row are given."
    )
    steps_hint = "number of steps = highest `step` of any node/edge + 1; every step from 0 must reveal something"
    params_model = BlockDiagramParams
    scene_file = "block_diagram.py"
    example_params = {
        "title": "How a compiler works",
        "direction": "right",
        "nodes": [
            {"id": "src", "label": "Source code", "color": "cyan", "step": 0},
            {"id": "lex", "label": "Lexer", "color": "gold", "step": 1},
            {"id": "parse", "label": "Parser", "color": "gold", "step": 2},
            {"id": "gen", "label": "Code generator", "color": "pink", "step": 3},
        ],
        "edges": [
            {"source": "src", "target": "lex", "label": "characters", "step": 1},
            {"source": "lex", "target": "parse", "label": "tokens", "step": 2},
            {"source": "parse", "target": "gen", "label": "syntax tree", "step": 3},
        ],
        "notes": ["We start with plain text", "The lexer groups characters into tokens",
                  "The parser builds a tree", "Finally machine code is generated"],
    }

    def step_count(self, params: BaseModel) -> int:
        model = self.coerce(params)
        return max([n.step for n in model.nodes] + [e.step for e in model.edges]) + 1

    def scene_data(self, params: BlockDiagramParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        data = params.model_dump(mode="json")
        if all(n.column >= 0 for n in params.nodes):
            # Manual grid: column = x, row = y exactly as written; a wide grid is transposed only to fit
            # a portrait side panel (adjacency is preserved).
            pos = {n.id: (float(n.column), float(n.row)) for n in params.nodes}
            cols = max(c for c, _ in pos.values()) + 1
            rows = max(r for _, r in pos.values()) + 1
            transpose = target == "panel" and cols > rows
        else:
            # Automatic layout: layers are columns; flowing down (or into a panel) turns layers into rows.
            pos = auto_layout(params.nodes, params.edges)
            transpose = params.direction == "down" or target == "panel"
        layout = {}
        for node_id, (col, row) in pos.items():
            layout[node_id] = {"gx": row if transpose else col, "gy": col if transpose else row}
        data["layout"] = layout
        data["grid_w"] = math.ceil(max(v["gx"] for v in layout.values())) + 1
        data["grid_h"] = math.ceil(max(v["gy"] for v in layout.values())) + 1
        data["step_total"] = self.step_count(params)
        pairs = {(e.source, e.target) for e in params.edges}
        data["bidirectional"] = [[e.source, e.target] for e in params.edges if (e.target, e.source) in pairs]
        return data
