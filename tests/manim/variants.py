"""Edge-case params for every template (shared by fast validation tests and slow render tests).

Each entry is ``name -> (template, params)``; they exercise options the examples do not: asymptotes,
negative bars, parallel circuits, manual layouts, circles/angles, 3x3 LaTeX matrices, FSMs with
bidirectional edges, 16-row truth tables, crowded free-body diagrams and long timelines.
"""

from __future__ import annotations

VARIANTS: dict[str, tuple[str, dict]] = {
    "fp_asymptote": ("function_plot", {
        "title": "Asymptotes", "x_min": -4, "x_max": 4, "y_min": -5, "y_max": 5,
        "functions": [{"expr": "1/x", "label_latex": r"y=\frac{1}{x}", "color": "gold"},
                      {"expr": "sin(x)", "label_latex": "", "color": "pink"}],
        "steps": [{"action": "show_function", "function_index": 0}, {"action": "show_function", "function_index": 1,
                  "note": "sine"}, {"action": "vline", "x": 1}, {"action": "dot", "function_index": 1, "x": 2},
                  {"action": "note", "note": "done"}]}),
    "bar_negative": ("bar_compare", {
        "title": "Profit", "unit": "%", "bars": [
            {"label": "Q1", "value": -12.5, "step": 0}, {"label": "Q2", "value": 30, "step": 0},
            {"label": "Q3", "value": 8, "step": 1, "color": "green"},
            {"label": "Q4 long label name", "value": -3, "step": 1, "color": "red"}], "highlight_max": True}),
    "bar_many": ("bar_compare", {
        "title": "", "unit": "ms", "y_label": "Latency", "bars": [
            {"label": f"Algo {i}", "value": v, "step": i} for i, v in enumerate([3, 1e5, 2.5e6, 12, 7, 0.5, 9, 40, 1, 2])]}),
    "circuit_parallel": ("circuit_basic", {
        "title": "Parallel", "topology": "parallel", "source_label": "E", "source_value": "9 V",
        "resistors": [{"label": "R1", "value": "3 Ω"}, {"label": "R2", "value": "6 Ω"}, {"label": "R3", "value": "9 Ω"}],
        "steps": [{"action": "source"}, {"action": "resistor", "index": 1}, {"action": "voltage_drop", "index": 2,
                  "text": "9 V"}, {"action": "current", "text": "I = 5.5 A"},
                  {"action": "equation", "latex": r"\frac{1}{R} = \frac{1}{3} + \frac{1}{6} + \frac{1}{9}"},
                  {"action": "equation", "latex": r"R \approx 1.64\,\Omega"}]}),
    "circuit_series3": ("circuit_basic", {
        "title": "Voltage divider", "topology": "series", "source_value": "12 V",
        "resistors": [{"label": "R1", "value": "2 Ω"}, {"label": "R2", "value": "4 Ω"}, {"label": "R3", "value": "6 Ω"}],
        "steps": [{"action": "draw"}, {"action": "current", "text": "I = 1 A"},
                  {"action": "voltage_drop", "index": 0, "text": "2 V"}, {"action": "voltage_drop", "index": 1,
                  "text": "4 V"}, {"action": "voltage_drop", "index": 2, "text": "6 V"},
                  {"action": "current", "text": "same I everywhere"}]}),
    "block_feedback": ("block_diagram", {
        "title": "Feedback loop", "direction": "down", "nodes": [
            {"id": "ref", "label": "Reference", "column": 0, "row": 0, "step": 0},
            {"id": "ctl", "label": "Controller", "column": 0, "row": 1, "step": 0},
            {"id": "plant", "label": "Plant", "column": 1, "row": 1, "step": 1},
            {"id": "sense", "label": "Sensor", "column": 1, "row": 0, "step": 2}],
        "edges": [{"source": "ref", "target": "ctl", "step": 0},
                  {"source": "ctl", "target": "plant", "label": "u", "step": 1},
                  {"source": "plant", "target": "sense", "label": "y", "step": 2},
                  {"source": "sense", "target": "ctl", "dashed": True, "step": 2},
                  {"source": "ctl", "target": "sense", "step": 2}]}),
    "block_wide": ("block_diagram", {
        "title": "Microservices", "nodes": [
            {"id": f"n{i}", "label": lbl, "step": min(i, 5)} for i, lbl in enumerate(
                ["Browser", "API gateway", "Auth service", "Orders", "Payments", "Database", "Cache",
                 "Message queue with a long name"])],
        "edges": [{"source": "n0", "target": "n1", "label": "HTTPS", "step": 1},
                  {"source": "n1", "target": "n2", "label": "JWT", "step": 2},
                  {"source": "n1", "target": "n3", "step": 3}, {"source": "n3", "target": "n4", "step": 4},
                  {"source": "n3", "target": "n5", "label": "SQL", "step": 5},
                  {"source": "n3", "target": "n6", "step": 5}, {"source": "n4", "target": "n7", "step": 5}]}),
    "geometry_circle": ("geometry", {
        "title": "Circle theorem", "points": [
            {"name": "O", "x": 0, "y": 0, "step": 0}, {"name": "A", "x": 3, "y": 0, "step": 0},
            {"name": "B", "x": -1.5, "y": 2.6, "step": 1}, {"name": "C", "x": -1.5, "y": -2.6, "step": 2}],
        "shapes": [{"kind": "circle", "points": ["O", "A"], "color": "cyan", "step": 0},
                   {"kind": "segment", "points": ["O", "B"], "label": "r", "step": 1},
                   {"kind": "polygon", "points": ["A", "B", "C"], "fill": True, "color": "gold", "step": 2},
                   {"kind": "angle", "points": ["B", "O", "A"], "label_latex": r"\theta", "step": 3},
                   {"kind": "line", "points": ["B", "C"], "dashed": True, "step": 3},
                   {"kind": "ray", "points": ["O", "C"], "step": 3}],
        "notes": ["a circle", "a radius", "a triangle", "angles"]}),
    "matrix_3x3": ("matrix_ops", {
        "title": "Determinant", "matrices": [{"name": "M", "rows": [
            {"values": ["a_{11}", "a_{12}", "a_{13}"]}, {"values": ["a_{21}", "a_{22}", "a_{23}"]},
            {"values": ["a_{31}", "a_{32}", "a_{33}"]}]}],
        "steps": [{"action": "show"}, {"action": "highlight_row", "row": 0},
                  {"action": "highlight_col", "col": 2, "text": "column 3"}, {"action": "highlight_cell", "row": 1, "col": 1}]}),
    "matrix_add": ("matrix_ops", {
        "title": "Adding", "matrices": [
            {"name": "A", "rows": [{"values": ["1", "-2", "3"]}]}, {"name": "B", "rows": [{"values": ["4", "5", "-6"]}]},
            {"name": "C", "rows": [{"values": ["5", "3", "-3"]}], "hidden": True}],
        "operators": ["+", "="],
        "steps": [{"action": "reveal_cell", "matrix": 2, "row": 0, "col": 0, "text": "1 + 4 = 5"},
                  {"action": "reveal_cell", "matrix": 2, "row": 0, "col": 1}, {"action": "reveal_all", "matrix": 2}]}),
    "graph_dfa": ("graph_traversal", {
        "title": "DFA", "directed": True, "layout": "circular", "start": "q0", "show_start_arrow": True,
        "accepting": ["q2"], "frontier_label": "Stack", "nodes": [{"id": "q0"}, {"id": "q1"}, {"id": "q2"}],
        "edges": [{"source": "q0", "target": "q1", "label": "a"}, {"source": "q1", "target": "q2", "label": "b"},
                  {"source": "q2", "target": "q0", "label": "a"}, {"source": "q1", "target": "q0", "label": "b"}],
        "steps": [{"visit": "q0", "frontier": ["q1"]}, {"visit": "q1", "via": "q0"}, {"visit": "q2", "via": "q1"},
                  {"visit": "q0", "via": "q2"}]}),
    "graph_big": ("graph_traversal", {
        "title": "DFS on 12 nodes", "layout": "layered", "start": "a", "frontier_label": "Stack",
        "nodes": [{"id": c} for c in "abcdefghijkl"],
        "edges": [{"source": s, "target": t} for s, t in
                  ["ab", "ac", "ad", "be", "bf", "cg", "dh", "ei", "fj", "gk", "hl", "ij", "kl"]],
        "steps": [{"visit": "a", "frontier": ["b", "c", "d"]}, {"visit": "b", "via": "a", "frontier": ["e", "f", "c", "d"]},
                  {"visit": "e", "via": "b"}, {"visit": "i", "via": "e"}, {"visit": "j", "via": "i"}]}),
    "wave_cos": ("wave", {
        "title": "", "function": "cos", "x_max": 10, "x_label": "x", "y_label": "", "show_readout": False,
        "steps": [{"amplitude": 1, "frequency": 0.2}, {"amplitude": 3, "frequency": 0.5, "keep_previous": True},
                  {"amplitude": 0, "frequency": 0.5}]}),
    "tt_xor3": ("truth_table", {"title": "3-input XOR", "gate": "XOR", "inputs": ["A", "B", "C"],
                                "expression": "Y = A ⊕ B ⊕ C", "rows_per_step": 3}),
    "tt_not": ("truth_table", {"title": "", "gate": "NOT", "inputs": ["A"]}),
    "tt_majority": ("truth_table", {"title": "Majority", "gate": "NONE", "inputs": ["A", "B", "C"], "rows": [
        {"inputs": [0, 0, 0], "output": 0}, {"inputs": [0, 1, 1], "output": 1}, {"inputs": [1, 1, 1], "output": 1}]}),
    "tt_nand4": ("truth_table", {"title": "NAND", "gate": "NAND", "inputs": ["A", "B", "C", "D"], "rows_per_step": 8}),
    "vf_ball": ("vector_forces", {
        "title": "Forces on a ball", "body_shape": "circle", "body_label": "5 kg", "surface": "ground", "forces": [
            {"label": "Weight", "magnitude": 49, "angle_deg": -90, "value_text": "49 N", "color": "gold"},
            {"label": "Normal", "magnitude": 49, "angle_deg": 90, "color": "cyan"},
            {"label": "Push", "magnitude": 20, "angle_deg": 0, "color": "green"},
            {"label": "Friction", "magnitude": 5, "angle_deg": 180, "color": "red"},
            {"label": "Wind", "magnitude": 3, "angle_deg": 45, "color": "blue"},
            {"label": "Drag", "magnitude": 2, "angle_deg": 200, "color": "orange"}], "show_resultant": True}),
    "timeline_8": ("timeline_steps", {"title": "Compiler phases", "events": [
        {"marker": f"Phase {i + 1}", "title": t, "detail": d} for i, (t, d) in enumerate([
            ("Lexical analysis", "Characters become tokens"), ("Syntax analysis", "Tokens become a parse tree"),
            ("Semantic analysis", "Types and scopes are checked carefully"), ("Intermediate code", "Three-address code"),
            ("Optimisation", "Remove redundant work"), ("Code generation", "Target machine instructions"),
            ("Linking", "Combine object files and libraries"), ("Loading", "Program placed in memory")])]}),
    "eq_stack": ("equation_steps", {"title": "Quadratic formula", "layout": "stack", "box_final": False, "steps": [
        {"latex": "ax^2 + bx + c = 0"}, {"latex": r"x^2 + \frac{b}{a}x = -\frac{c}{a}"},
        {"latex": r"\left(x + \frac{b}{2a}\right)^2 = \frac{b^2 - 4ac}{4a^2}", "annotation": "complete the square"},
        {"latex": r"x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}", "annotation": "take square roots"}]}),
    "eq_long": ("equation_steps", {"title": "", "steps": [
        {"latex": r"\int_0^1 x^2\,dx"}, {"latex": r"\left[\frac{x^3}{3}\right]_0^1"},
        {"latex": r"\frac{1}{3} - 0"}, {"latex": r"\frac{1}{3}"},
        {"latex": r"\sum_{k=1}^{n} k^2 = \frac{n(n+1)(2n+1)}{6} \approx \frac{n^3}{3} \text{ for large } n"}]}),
}
