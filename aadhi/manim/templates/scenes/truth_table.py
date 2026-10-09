# ruff: noqa: F403, F405
# Scene source for the truth_table template (read as text; see equation_steps.py).
from manim import *


def pt(x, y):
    return np.array([x, y, 0.0])


class TruthTableScene(AadhiScene):
    def gate_body(self, gate):
        stroke = {"color": AADHI_WHITE, "stroke_width": 5}
        base = gate[1:] if gate in ("NAND", "NOR", "XNOR") else gate
        if gate == "XNOR":
            base = "XOR"
        if base == "AND":
            body = VGroup(
                Line(pt(-0.6, 0.5), pt(-0.6, -0.5), **stroke),
                Line(pt(-0.6, 0.5), pt(0.0, 0.5), **stroke),
                Line(pt(-0.6, -0.5), pt(0.0, -0.5), **stroke),
                Arc(radius=0.5, start_angle=PI / 2, angle=-PI, arc_center=pt(0.0, 0.0), **stroke),
            )
            tip = pt(0.5, 0.0)
            back_x = -0.6
        elif base in ("OR", "XOR"):
            body = VGroup(
                ArcBetweenPoints(pt(-0.6, 0.5), pt(-0.6, -0.5), angle=-0.9, **stroke),
                ArcBetweenPoints(pt(-0.6, 0.5), pt(0.55, 0.0), angle=-0.6, **stroke),
                ArcBetweenPoints(pt(-0.6, -0.5), pt(0.55, 0.0), angle=0.6, **stroke),
            )
            if base == "XOR":
                body.add(ArcBetweenPoints(pt(-0.78, 0.5), pt(-0.78, -0.5), angle=-0.9, **stroke))
            tip = pt(0.55, 0.0)
            back_x = -0.45
        else:
            body = VGroup(Polygon(pt(-0.55, 0.5), pt(-0.55, -0.5), pt(0.35, 0.0), **stroke))
            tip = pt(0.35, 0.0)
            back_x = -0.55
        if gate in ("NAND", "NOR", "XNOR", "NOT"):
            bubble = Circle(radius=0.1, **stroke).move_to(tip + RIGHT * 0.1)
            body.add(bubble)
            tip = tip + RIGHT * 0.2
        return body, tip, back_x

    def construct(self):
        p = PARAMS
        table = p["table"]
        names = p["inputs"]
        total = p["step_total"]
        per = p["rows_per_step"]
        self.declare_steps(total)
        if p["title"]:
            self.show_title(p["title"])
        cx, cy, w, h = self.content_box()

        n_cols = len(names) + 1
        cell_w, cell_h = 0.95, 0.5
        header = VGroup()
        for c, name in enumerate(names + [p["output_name"]]):
            color = AADHI_GOLD if c == n_cols - 1 else AADHI_CYAN
            header.add(self.label(name, role="label", color=color).move_to(pt(c * cell_w, 0.0)))
        rule = Line(pt(-cell_w / 2, -cell_h / 2), pt((n_cols - 0.5) * cell_w, -cell_h / 2), color=AADHI_MUTED, stroke_width=3)
        divider = Line(pt((n_cols - 1.5) * cell_w, cell_h / 2), pt((n_cols - 1.5) * cell_w, -cell_h * (len(table) + 0.5)),
                       color=AADHI_MUTED, stroke_width=2)
        rows = []
        for r, row in enumerate(table):
            y = -(r + 1) * cell_h
            cells = VGroup()
            for c, bit in enumerate(row["inputs"]):
                cells.add(self.label(str(bit), role="body").move_to(pt(c * cell_w, y)))
            out_color = AADHI_GREEN if row["output"] else AADHI_RED
            cells.add(self.label(str(row["output"]), role="body", color=out_color, weight=BOLD).move_to(pt((n_cols - 1) * cell_w, y)))
            rows.append(cells)
        table_mob = VGroup(header, rule, divider, *rows)

        gate_mob = VGroup()
        in_anchors = []
        out_anchor = None
        if p["gate"] != "NONE":
            body, tip, back_x = self.gate_body(p["gate"])
            n_in = len(names)
            ys = [0.0] if n_in == 1 else [0.32 - 0.64 * k / (n_in - 1) for k in range(n_in)]
            wires = VGroup()
            for k, y in enumerate(ys):
                wires.add(Line(pt(-1.4, y), pt(back_x, y), color=AADHI_MUTED, stroke_width=4))
                wires.add(self.label(names[k], role="label", color=AADHI_CYAN).next_to(pt(-1.4, y), LEFT, buff=0.12))
                anchor = Dot(pt(-1.0, y + 0.2), radius=0.01).set_opacity(0)
                in_anchors.append(anchor)
                wires.add(anchor)
            wires.add(Line(tip, pt(1.4, 0.0), color=AADHI_MUTED, stroke_width=4))
            wires.add(self.label(p["output_name"], role="label", color=AADHI_GOLD).next_to(pt(1.4, 0.0), RIGHT, buff=0.12))
            out_anchor = Dot(pt(1.05, 0.22), radius=0.01).set_opacity(0)
            wires.add(out_anchor)
            caption = self.label(p["gate"], role="small", color=AADHI_MUTED).next_to(body, DOWN, buff=0.3)
            gate_mob = VGroup(wires, body, caption)
            if p["expression"]:
                gate_mob.add(self.label(p["expression"], role="body", color=AADHI_WHITE).next_to(caption, DOWN, buff=0.25))
        elif p["expression"]:
            gate_mob = VGroup(self.label(p["expression"], role="heading", color=AADHI_WHITE))

        layout = VGroup(*(m for m in (gate_mob, table_mob) if len(m)))
        layout.arrange(DOWN if self.IS_PANEL else RIGHT, buff=0.6 if self.IS_PANEL else 1.0)
        before = max(layout.width, 1e-6)
        self.fit_to_safe_area(layout, grow=True, max_grow=1.5)
        tag_scale = layout.width / before
        self.add(gate_mob, header, rule, divider)

        live_tags = VGroup()
        for step in range(total):
            chunk = list(range(step * per, min(len(table), (step + 1) * per)))
            animations = [FadeIn(rows[r], shift=LEFT * 0.1) for r in chunk]
            if out_anchor is not None and chunk:
                last = table[chunk[-1]]
                tags = VGroup()
                for k, bit in enumerate(last["inputs"]):
                    tag = self.label(str(bit), role="small", color=AADHI_CYAN).scale(tag_scale)
                    tags.add(tag.move_to(in_anchors[k].get_center()))
                out_color = AADHI_GREEN if last["output"] else AADHI_RED
                tag = self.label(str(last["output"]), role="small", color=out_color).scale(tag_scale)
                tags.add(tag.move_to(out_anchor.get_center()))
                if len(live_tags):
                    animations.append(FadeOut(live_tags))
                animations.append(FadeIn(tags))
                live_tags = tags
            self.play_step(step, *animations, preferred=0.8)
