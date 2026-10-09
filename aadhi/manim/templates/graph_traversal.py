"""graph_traversal: graphs / state machines with a visit order (BFS, DFS, Dijkstra, FSM runs)."""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ._base import Finite, NoteText, SceneTemplate, TemplateParams, TitleText

NODE_ID = r"^[A-Za-z0-9_\-]{1,12}$"


class GraphNode(TemplateParams):
    id: str = Field(pattern=NODE_ID)
    label: str = Field(default="", max_length=8, description="text in the node ('' = the id)")
    x: Finite = Field(default=0.0, ge=-20, le=20, description="position, used only when layout = manual")
    y: Finite = Field(default=0.0, ge=-20, le=20)


class GraphEdge(TemplateParams):
    source: str = Field(pattern=NODE_ID)
    target: str = Field(pattern=NODE_ID)
    label: str = Field(default="", max_length=8, description="weight or input symbol")


class TraversalStep(TemplateParams):
    visit: str = Field(default="", description="node visited (made current) at this step; '' = none")
    via: str = Field(default="", description="node the traversal came from (highlights the edge via -> visit)")
    frontier: list[str] = Field(default_factory=list, max_length=12, description="queue/stack contents after this step")
    note: NoteText = ""


class GraphTraversalParams(TemplateParams):
    title: TitleText = ""
    directed: bool = False
    layout: Literal["layered", "circular", "manual"] = Field(
        default="layered", description="layered = tree-like from `start`; circular; manual uses node x/y"
    )
    start: str = Field(default="", description="start node (root of the layered layout; FSM start arrow)")
    show_start_arrow: bool = False
    accepting: list[str] = Field(default_factory=list, max_length=12, description="FSM accepting states (double circle)")
    frontier_label: str = Field(default="Queue", max_length=12, description="name of the frontier, e.g. Queue or Stack")
    nodes: list[GraphNode] = Field(min_length=2, max_length=12)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=24)
    steps: list[TraversalStep] = Field(min_length=1, max_length=12, description="one step per beat")

    @model_validator(mode="after")
    def _semantics(self) -> GraphTraversalParams:
        ids = [n.id for n in self.nodes]
        known = set(ids)
        if len(known) != len(ids):
            raise ValueError("node ids must be unique")
        for e in self.edges:
            if e.source not in known or e.target not in known:
                raise ValueError(f"edge {e.source}->{e.target} references an unknown node")
            if e.source == e.target and not self.directed:
                raise ValueError("self-loops are only allowed in directed graphs (state machines)")
        pairs = {(e.source, e.target) for e in self.edges}
        if not self.directed:
            pairs |= {(b, a) for a, b in pairs}
        for name in [self.start, *self.accepting]:
            if name and name not in known:
                raise ValueError(f"unknown node {name!r}")
        for i, step in enumerate(self.steps):
            if step.visit and step.visit not in known:
                raise ValueError(f"steps[{i}].visit {step.visit!r} is not a node")
            if step.via:
                if not step.visit:
                    raise ValueError(f"steps[{i}].via needs `visit`")
                if (step.via, step.visit) not in pairs:
                    raise ValueError(f"steps[{i}]: there is no edge {step.via} -> {step.visit}")
            for f in step.frontier:
                if f not in known:
                    raise ValueError(f"steps[{i}].frontier contains unknown node {f!r}")
        return self


def layout_nodes(params: GraphTraversalParams) -> dict[str, tuple[float, float]]:
    """Node positions in abstract units (y up)."""
    ids = [n.id for n in params.nodes]
    if params.layout == "manual":
        return {n.id: (n.x, n.y) for n in params.nodes}
    if params.layout == "circular":
        k = len(ids)
        return {i: (math.cos(math.pi / 2 - 2 * math.pi * j / k), math.sin(math.pi / 2 - 2 * math.pi * j / k))
                for j, i in enumerate(ids)}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for e in params.edges:
        adj[e.source].append(e.target)
        adj[e.target].append(e.source)
    depth: dict[str, int] = {}
    order: list[str] = []
    roots = [params.start] if params.start else []
    roots += [i for i in ids if i not in roots]
    for root in roots:
        if root in depth:
            continue
        base = max(depth.values()) + 1 if depth else 0
        depth[root] = base
        queue = deque([root])
        while queue:
            u = queue.popleft()
            order.append(u)
            for v in adj[u]:
                if v not in depth:
                    depth[v] = depth[u] + 1
                    queue.append(v)
    layers: dict[int, list[str]] = {}
    for u in order:
        layers.setdefault(depth[u], []).append(u)
    widest = max(len(v) for v in layers.values())
    pos = {}
    for d, members in layers.items():
        for j, u in enumerate(members):
            pos[u] = (j - (len(members) - 1) / 2, -float(d) * max(1.0, widest / 3))
    return pos


class GraphTraversalTemplate(SceneTemplate):
    name = "graph_traversal"
    title = "Graph traversal / state machine"
    description = (
        "A graph (directed or undirected, optional edge weights) where each beat visits the next node, highlights "
        "the edge used and shows the queue/stack. Also draws finite state machines (start arrow, accepting states, "
        "self-loops). Use for BFS, DFS, shortest paths, topological order, FSM/automata runs, network routing."
    )
    steps_hint = "one step per item of `steps` (usually one visited node per step)"
    params_model = GraphTraversalParams
    scene_file = "graph_traversal.py"
    example_params = {
        "title": "Breadth-first search",
        "directed": False,
        "layout": "layered",
        "start": "A",
        "frontier_label": "Queue",
        "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}, {"id": "E"}],
        "edges": [
            {"source": "A", "target": "B"}, {"source": "A", "target": "C"},
            {"source": "B", "target": "D"}, {"source": "C", "target": "E"},
        ],
        "steps": [
            {"visit": "A", "frontier": ["B", "C"], "note": "Start at A and queue its neighbours"},
            {"visit": "B", "via": "A", "frontier": ["C", "D"], "note": "Visit B, queue D"},
            {"visit": "C", "via": "A", "frontier": ["D", "E"], "note": "Visit C, queue E"},
            {"visit": "D", "via": "B", "frontier": ["E"], "note": "Visit D"},
            {"visit": "E", "via": "C", "frontier": [], "note": "Visit E: the queue is empty, done"},
        ],
    }

    def step_count(self, params: BaseModel) -> int:
        return len(self.coerce(params).steps)

    def scene_data(self, params: GraphTraversalParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        data = params.model_dump(mode="json")
        pos = layout_nodes(params)
        data["positions"] = {k: [round(x, 5), round(y, 5)] for k, (x, y) in pos.items()}
        pairs = {(e.source, e.target) for e in params.edges}
        data["curved"] = [[e.source, e.target] for e in params.edges
                          if params.directed and e.source != e.target and (e.target, e.source) in pairs]
        data["has_frontier"] = any(s.frontier for s in params.steps)
        return data
