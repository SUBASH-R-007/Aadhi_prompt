# ruff: noqa: F403, F405
# Scene source for the graph_traversal template (read as text; see equation_steps.py).
from manim import *


class GraphTraversalScene(AadhiScene):
    def construct(self):
        p = PARAMS
        steps = p["steps"]
        self.declare_steps(len(steps))
        if p["title"]:
            self.show_title(p["title"])
        if any(s["note"] for s in steps):
            self.reserve_notes()
        cx, cy, w, h = self.content_box()
        frontier_h = 0.9 if p["has_frontier"] else 0.0
        area_cy = cy + frontier_h / 2
        area_h = h - frontier_h

        xs = [v[0] for v in p["positions"].values()]
        ys = [v[1] for v in p["positions"].values()]
        span_x = max(max(xs) - min(xs), 1e-6)
        span_y = max(max(ys) - min(ys), 1e-6)
        radius = min(0.42, w / (len(xs) * 2.6), area_h / 7)
        usable_w = w - 2.6 * radius
        usable_h = area_h - 2.6 * radius
        scale = min(usable_w / span_x if span_x > 1e-5 else 1e9, usable_h / span_y if span_y > 1e-5 else 1e9, 2.6)
        mid = np.array([(max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, 0.0])
        center = np.array([cx, area_cy, 0.0])
        spots = {k: center + (np.array([v[0], v[1], 0.0]) - mid) * scale for k, v in p["positions"].items()}

        nodes = {}
        for node in p["nodes"]:
            spot = spots[node["id"]]
            ring = Circle(radius=radius, color=AADHI_MUTED, stroke_width=4).move_to(spot)
            ring.set_fill(AADHI_BG, opacity=1.0)
            parts = VGroup(ring)
            if node["id"] in p["accepting"]:
                parts.add(Circle(radius=radius * 0.8, color=AADHI_MUTED, stroke_width=3).move_to(spot))
            text = self.label(node["label"] or node["id"], role="body", max_width=radius * 1.5)
            if text.height > radius * 1.1:
                text.scale_to_fit_height(radius * 1.1)
            parts.add(text.move_to(spot))
            parts.set_z_index(5)
            nodes[node["id"]] = parts

        curved = [tuple(x) for x in p["curved"]]
        edges = {}
        edge_group = VGroup()
        for edge in p["edges"]:
            a, b = spots[edge["source"]], spots[edge["target"]]
            tip = 0.2 if p["directed"] else 0.0
            if edge["source"] == edge["target"]:
                arc = Arc(radius=radius * 0.7, start_angle=-0.25 * PI, angle=1.5 * PI,
                          arc_center=a + UP * radius * 1.25, color=AADHI_MUTED, stroke_width=4)
                arc.add_tip(tip_length=0.16)
                line = arc
                mid_pt = a + UP * (radius * 2.2)
                normal = UP
            else:
                direction = (b - a) / np.linalg.norm(b - a)
                normal = np.array([-direction[1], direction[0], 0.0])
                start = a + direction * radius
                end = b - direction * radius
                if (edge["source"], edge["target"]) in curved:
                    # A negative angle bulges to the LEFT of the travel direction (the +normal side), so the
                    # two arcs of a pair a->b / b->a bulge apart; each label sits outside its own arc.
                    line = ArcBetweenPoints(start + normal * 0.08, end + normal * 0.08, angle=-0.5, color=AADHI_MUTED, stroke_width=4)
                    mid_pt = line.point_from_proportion(0.5)
                else:
                    line = Line(start, end, color=AADHI_MUTED, stroke_width=4)
                    mid_pt = (a + b) / 2
                if tip:
                    line.add_tip(tip_length=tip)
            group = VGroup(line)
            if edge["label"]:
                tag = self.label(edge["label"], role="small", color=AADHI_GOLD)
                tag.move_to(mid_pt + normal * (0.22 + tag.height / 2))
                group.add(tag)
            edges[(edge["source"], edge["target"])] = line
            if not p["directed"]:
                edges[(edge["target"], edge["source"])] = line
            edge_group.add(group)

        extras = VGroup()
        if p["show_start_arrow"] and p["start"]:
            s = spots[p["start"]]
            extras.add(Arrow(s + LEFT * (radius + 0.9), s + LEFT * radius, buff=0, color=AADHI_WHITE, stroke_width=4))
        self.add(edge_group, extras, *nodes.values())

        frontier_spot = np.array([cx, cy - h / 2 + frontier_h / 2, 0.0])
        frontier = VGroup()
        current = None
        visited = set()
        for i, step in enumerate(steps):
            animations = self.note(step["note"]) if step["note"] or self.note_mob else []
            if step["via"]:
                line = edges[(step["via"], step["visit"])]
                animations.append(line.animate.set_stroke(AADHI_GOLD, width=7))
            if step["visit"]:
                if current is not None and current != step["visit"]:
                    animations.append(nodes[current][0].animate.set_fill(AADHI_CYAN, opacity=0.35).set_stroke(AADHI_CYAN))
                ring = nodes[step["visit"]][0]
                animations.append(ring.animate.set_fill(AADHI_GOLD, opacity=0.55).set_stroke(AADHI_GOLD, width=6))
                visited.add(step["visit"])
                current = step["visit"]
            if p["has_frontier"]:
                cells = VGroup()
                for name in step["frontier"]:
                    box = RoundedRectangle(corner_radius=0.08, width=0.62, height=0.5, color=AADHI_PINK, stroke_width=3)
                    box.add(self.label(name, role="small", max_width=0.5).move_to(box))
                    cells.add(box)
                head = self.label(p["frontier_label"] + ":", role="small", color=AADHI_PINK)
                new_frontier = VGroup(head, *cells).arrange(RIGHT, buff=0.12)
                if new_frontier.width > w:
                    new_frontier.scale_to_fit_width(w)
                new_frontier.move_to(frontier_spot)
                if len(frontier):
                    animations.append(FadeOut(frontier))
                animations.append(FadeIn(new_frontier))
                frontier = new_frontier
            self.play_step(i, *animations, preferred=0.9)
