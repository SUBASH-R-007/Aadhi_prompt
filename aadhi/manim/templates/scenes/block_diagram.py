# ruff: noqa: F403, F405
# Scene source for the block_diagram template (read as text; see equation_steps.py).
from manim import *


def border_point(center, half_w, half_h, direction):
    dx, dy = abs(direction[0]), abs(direction[1])
    tx = half_w / dx if dx > 1e-9 else 1e9
    ty = half_h / dy if dy > 1e-9 else 1e9
    return center + direction * min(tx, ty)


class BlockDiagramScene(AadhiScene):
    def construct(self):
        p = PARAMS
        total = p["step_total"]
        self.declare_steps(total)
        if p["title"]:
            self.show_title(p["title"])
        if any(p["notes"]):
            self.reserve_notes()
        cx, cy, w, h = self.content_box()
        gw, gh = p["grid_w"], p["grid_h"]
        cell_w, cell_h = w / gw, h / gh
        labelled = any(e["label"] for e in p["edges"])
        # Edge labels sit in the gaps between boxes: make horizontal gaps wide enough for them.
        tags = {}
        need_gap = 0.0
        for k, edge in enumerate(p["edges"]):
            if not edge["label"]:
                continue
            tags[k] = self.label(edge["label"], role="small", color=AADHI_GOLD)
            a, b = p["layout"][edge["source"]], p["layout"][edge["target"]]
            if abs(a["gx"] - b["gx"]) * cell_w >= abs(a["gy"] - b["gy"]) * cell_h:
                need_gap = max(need_gap, tags[k].width + 0.4)
        box_w = min(cell_w * (0.6 if labelled else 0.74), 3.4)
        if need_gap:
            box_w = max(min(box_w, cell_w - need_gap), cell_w * 0.45)
        box_h = min(cell_h * (0.5 if labelled else 0.58), 1.15, box_w * 0.75)
        left, top = cx - w / 2, cy + h / 2

        boxes = {}
        for node in p["nodes"]:
            spot = p["layout"][node["id"]]
            center = np.array([left + (spot["gx"] + 0.5) * cell_w, top - (spot["gy"] + 0.5) * cell_h, 0.0])
            color = self.color(node["color"])
            rect = RoundedRectangle(corner_radius=0.14, width=box_w, height=box_h, color=color, stroke_width=4)
            rect.set_fill(color, opacity=0.14).move_to(center)
            text = self.paragraph(node["label"], width=box_w * 0.86, role="body", max_lines=2)
            if text.height > box_h * 0.78:
                text.scale_to_fit_height(box_h * 0.78)
            text.move_to(center)
            boxes[node["id"]] = (VGroup(rect, text), center)

        bidirectional = [tuple(pair) for pair in p["bidirectional"]]
        arrows = []
        for k, edge in enumerate(p["edges"]):
            a = boxes[edge["source"]][1]
            b = boxes[edge["target"]][1]
            direction = (b - a) / max(np.linalg.norm(b - a), 1e-6)
            normal = np.array([-direction[1], direction[0], 0.0])
            offset = normal * 0.14 if (edge["source"], edge["target"]) in bidirectional else ORIGIN
            start = border_point(a, box_w / 2 + 0.06, box_h / 2 + 0.06, direction) + offset
            end = border_point(b, box_w / 2 + 0.06, box_h / 2 + 0.06, -direction) + offset
            if edge["dashed"]:
                line = DashedLine(start, end, color=AADHI_MUTED, stroke_width=4).add_tip(tip_length=0.2)
            else:
                line = Arrow(start, end, buff=0, color=AADHI_MUTED, stroke_width=5, max_tip_length_to_length_ratio=0.2)
            group = VGroup(line)
            if k in tags:
                tag = tags[k]
                room = max(np.linalg.norm(end - start) * 0.9, 0.6)
                if abs(direction[0]) > abs(direction[1]) and tag.width > room:
                    tag.scale_to_fit_width(room)
                lift = 0.12 + (tag.height if abs(direction[0]) >= abs(direction[1]) else tag.width) / 2
                tag.move_to((start + end) / 2 + normal * lift)
                group.add(tag)
            arrows.append(group)

        for step in range(total):
            animations = []
            note = p["notes"][step] if step < len(p["notes"]) else ""
            if note or self.note_mob:
                animations.extend(self.note(note))
            for node in p["nodes"]:
                if node["step"] == step:
                    animations.append(FadeIn(boxes[node["id"]][0], scale=0.85))
            for k, edge in enumerate(p["edges"]):
                if edge["step"] == step:
                    animations.append(Create(arrows[k][0]))
                    if len(arrows[k]) > 1:
                        animations.append(FadeIn(arrows[k][1]))
            self.play_step(step, *animations, preferred=1.0)
