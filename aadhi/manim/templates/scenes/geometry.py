# ruff: noqa: F403, F405
# Scene source for the geometry template (read as text; see equation_steps.py).
from manim import *


class GeometryScene(AadhiScene):
    def construct(self):
        p = PARAMS
        total = p["step_total"]
        self.declare_steps(total)
        if p["title"]:
            self.show_title(p["title"])
        if any(p["notes"]):
            self.reserve_notes()
        cx, cy, w, h = self.content_box()
        x0, y0, x1, y1 = p["bbox"]
        scale = min(w / (x1 - x0), h / (y1 - y0))
        mid = np.array([(x0 + x1) / 2, (y0 + y1) / 2, 0.0])
        center = np.array([cx, cy, 0.0])

        def to_scene(x, y):
            return center + (np.array([x, y, 0.0]) - mid) * scale

        coords = {pt["name"]: to_scene(pt["x"], pt["y"]) for pt in p["points"]}
        centroid = to_scene(*p["centroid"])

        point_mobs = {}
        for pt in p["points"]:
            spot = coords[pt["name"]]
            group = VGroup(Dot(spot, radius=0.07, color=AADHI_WHITE))
            if pt["show_label"]:
                away = spot - centroid
                norm = np.linalg.norm(away)
                away = away / norm if norm > 1e-6 else UR / np.linalg.norm(UR)
                tag = self.label(pt["name"], role="label", color=AADHI_WHITE)
                tag.move_to(spot + away * (0.3 + tag.width * 0.3))
                group.add(tag)
            group.set_z_index(10)
            point_mobs[pt["name"]] = group

        shape_mobs = []
        for k, shape in enumerate(p["shapes"]):
            info = p["extra"][k]
            color = self.color(shape["color"])
            pts = [coords[n] for n in shape["points"]]
            kind = shape["kind"]
            if kind in ("segment", "line", "ray"):
                a, b = pts if kind == "segment" else [to_scene(*e) for e in info["ends"]]
                maker = DashedLine if shape["dashed"] else Line
                mob = maker(a, b, color=color, stroke_width=5)
                if kind == "ray":
                    mob.add_tip(tip_length=0.2)
                anchor = (pts[0] + pts[1]) / 2
                direction = (pts[1] - pts[0]) / np.linalg.norm(pts[1] - pts[0])
                normal = np.array([-direction[1], direction[0], 0.0])
                if np.dot(normal, anchor - centroid) < 0:
                    normal = -normal
            elif kind == "polygon":
                mob = Polygon(*pts, color=color, stroke_width=5)
                mob.set_fill(color, opacity=0.18 if shape["fill"] else 0.0)
                anchor = sum(pts) / len(pts)
                normal = ORIGIN
            elif kind == "circle":
                mob = Circle(radius=info["radius"] * scale, color=color, stroke_width=5).move_to(pts[0])
                if shape["dashed"]:
                    mob = DashedVMobject(mob, num_dashes=40)
                mob.set_fill(color, opacity=0.15 if shape["fill"] else 0.0)
                anchor = pts[0] + UP * info["radius"] * scale
                normal = UP
            else:
                a, b, c = pts
                l1, l2 = Line(b, a), Line(b, c)
                if info["swap"]:
                    l1, l2 = l2, l1
                radius = min(0.55, np.linalg.norm(a - b) * 0.4, np.linalg.norm(c - b) * 0.4)
                if kind == "right_angle":
                    mob = Angle(l1, l2, radius=radius * 0.7, elbow=True, color=color, stroke_width=4)
                else:
                    mob = Angle(l1, l2, radius=radius, color=color, stroke_width=4)
                bis = (a - b) / np.linalg.norm(a - b) + (c - b) / np.linalg.norm(c - b)
                bis = bis / np.linalg.norm(bis) if np.linalg.norm(bis) > 1e-6 else UP
                anchor = b + bis * radius
                normal = bis
            group = VGroup(mob)
            if shape["label_latex"] or shape["label"]:
                if shape["label_latex"]:
                    tag = self.math(shape["label_latex"], role="label", color=color)
                else:
                    tag = self.label(shape["label"], role="small", color=color)
                tag.move_to(anchor + normal * (0.22 + max(tag.width, tag.height) * 0.5))
                group.add(tag)
            shape_mobs.append(group)

        everything = VGroup(*point_mobs.values(), *shape_mobs)
        if everything.width > w or everything.height > h:
            factor = min(w / everything.width, h / everything.height)
            everything.scale(factor, about_point=center)

        for step in range(total):
            animations = []
            note = p["notes"][step] if step < len(p["notes"]) else ""
            if note or self.note_mob:
                animations.extend(self.note(note))
            for k, shape in enumerate(p["shapes"]):
                if shape["step"] == step:
                    animations.append(Create(shape_mobs[k][0]))
                    if len(shape_mobs[k]) > 1:
                        animations.append(FadeIn(shape_mobs[k][1]))
            for pt in p["points"]:
                if pt["step"] == step:
                    animations.append(FadeIn(point_mobs[pt["name"]], scale=0.6))
            self.play_step(step, *animations, preferred=1.0)
