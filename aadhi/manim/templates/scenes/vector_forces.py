# ruff: noqa: F403, F405
# Scene source for the vector_forces template (read as text; see equation_steps.py).
from manim import *


def boxes_overlap(a, b, margin=0.06):
    """True when the bounding boxes of ``a`` and ``b`` (plus ``margin``) intersect."""
    return not (
        a.get_right()[0] + margin < b.get_left()[0]
        or b.get_right()[0] + margin < a.get_left()[0]
        or a.get_top()[1] + margin < b.get_bottom()[1]
        or b.get_top()[1] + margin < a.get_bottom()[1]
    )


class VectorForcesScene(AadhiScene):
    def make_tag(self, text, latex, value, color):
        name = self.math(latex, role="label", color=color) if latex else self.label(text, role="label", color=color)
        tag = VGroup(name)
        if value:
            tag.add(self.label(value, role="small", color=color))
        return tag.arrange(DOWN, buff=0.06)

    def place_tag(self, tag, end, direction):
        unit = direction / max(np.linalg.norm(direction), 1e-6)
        tag.move_to(end + unit * (0.22 + 0.5 * max(tag.width, tag.height)))

    def separate(self, items, obstacles):
        """Push each (tag, unit direction) outward until it no longer overlaps earlier tags/obstacles."""
        placed = list(obstacles)
        for tag, unit in items:
            for _ in range(16):
                if not any(boxes_overlap(tag, other) for other in placed):
                    break
                tag.shift(unit * 0.12)
            placed.append(tag)

    def edge_distance(self, unit, half, theta, circle):
        """Distance from the body centre to its outline along ``unit`` (body rotated by ``theta``)."""
        if circle:
            return half
        local = rotate_vector(unit, -theta)
        return half / max(abs(local[0]), abs(local[1]), 1e-6)

    def construct(self):
        p = PARAMS
        forces = p["forces"]
        self.declare_steps(len(forces) + (1 if p["show_resultant"] else 0))
        if p["title"]:
            self.show_title(p["title"])
        if any(f["note"] for f in forces) or p["resultant_note"]:
            self.reserve_notes()
        cx, cy, w, h = self.content_box()
        reach = min(w, h) * 0.36
        center = np.array([cx, cy, 0.0])
        size = min(w, h) * 0.16
        theta = p["incline_deg"] * DEGREES if p["surface"] == "incline" else 0.0

        if p["body_shape"] == "circle":
            shape = Circle(radius=size / 2, color=AADHI_WHITE, stroke_width=3)
        else:
            shape = Square(side_length=size, color=AADHI_WHITE, stroke_width=3)
        shape.set_fill(AADHI_PURPLE, opacity=0.9)
        body = VGroup(shape.move_to(center))
        if p["body_label"]:
            body.add(self.label(p["body_label"], role="label", max_width=size * 0.9).move_to(center))
        body.rotate(theta, about_point=center)
        body.set_z_index(3)
        scenery = VGroup()
        bottom = center + np.array([np.sin(theta), -np.cos(theta), 0.0]) * size / 2
        if p["surface"] == "ground":
            ground = Line(bottom + LEFT * reach * 1.2, bottom + RIGHT * reach * 1.2, color=AADHI_MUTED, stroke_width=4)
            hatch = VGroup(*[
                Line(ground.point_from_proportion(t), ground.point_from_proportion(t) + DL * 0.16,
                     color=AADHI_MUTED, stroke_width=2)
                for t in np.linspace(0.03, 0.97, 14)
            ])
            scenery.add(ground, hatch)
        elif p["surface"] == "incline":
            u = np.array([np.cos(theta), np.sin(theta), 0.0])
            a = bottom - u * reach * 1.25
            b = bottom + u * reach * 0.9
            c = np.array([b[0], a[1], 0.0])
            ramp = Polygon(a, b, c, color=AADHI_MUTED, stroke_width=3)
            ramp.set_fill(AADHI_MUTED, opacity=0.12)
            arc = Arc(radius=0.55, start_angle=0, angle=theta, arc_center=a, color=AADHI_GOLD)
            mark = self.label(f"{p['incline_deg']:g}°", role="small", color=AADHI_GOLD)
            mark.move_to(a + np.array([0.9 * np.cos(theta / 2), 0.9 * np.sin(theta / 2), 0.0]))
            scenery.add(ramp, arc, mark)

        arrows = []
        tag_items = []
        circle = p["body_shape"] == "circle"
        for i, f in enumerate(forces):
            direction = np.array([p["vectors"][i]["dx"], p["vectors"][i]["dy"], 0.0])
            unit = direction / max(np.linalg.norm(direction), 1e-6)
            color = self.color(f["color"])
            # Arrows start at the centre of mass; small forces still reach visibly past the body outline.
            length = max(np.linalg.norm(direction) * reach, self.edge_distance(unit, size / 2, theta, circle) + 0.5)
            arrow = Arrow(center, center + unit * length, buff=0, color=color, stroke_width=7,
                          max_tip_length_to_length_ratio=0.18)
            tag = self.make_tag(f["label"], f["label_latex"], f["value_text"], color)
            self.place_tag(tag, arrow.get_end(), direction)
            arrows.append(VGroup(arrow, tag))
            tag_items.append((tag, unit))

        extra = VGroup()
        r = p["resultant"]
        if p["show_resultant"]:
            if r["zero"]:
                badge = self.label(f"{p['resultant_label']} = 0", role="label", color=AADHI_GOLD)
                badge.next_to(VGroup(scenery, body, *arrows), UP, buff=0.2)
                extra.add(badge)
            else:
                direction = np.array([r["dx"], r["dy"], 0.0])
                length = np.linalg.norm(direction)
                if length > 1.3:
                    direction = direction / length * 1.3
                result = Arrow(center, center + direction * reach, buff=0, color=AADHI_GOLD, stroke_width=10,
                               max_tip_length_to_length_ratio=0.2)
                result.set_z_index(5)
                badge = self.label(p["resultant_label"], role="label", color=AADHI_GOLD)
                self.place_tag(badge, result.get_end(), direction)
                extra.add(result, badge)
                tag_items.append((badge, direction / max(np.linalg.norm(direction), 1e-6)))
        self.separate(tag_items, [body])

        everything = VGroup(scenery, body, *arrows, extra)
        self.fit_to_safe_area(everything)
        self.add(scenery, body)

        for i, f in enumerate(forces):
            animations = self.note(f["note"]) if f["note"] or self.note_mob else []
            self.play_step(i, *animations, GrowArrow(arrows[i][0]), FadeIn(arrows[i][1]), preferred=1.0)

        if p["show_resultant"]:
            animations = self.note(p["resultant_note"]) if p["resultant_note"] or self.note_mob else []
            if r["zero"]:
                animations.extend([FadeIn(extra[0]), *[Indicate(a[0], color=AADHI_GOLD) for a in arrows]])
            else:
                animations.extend([GrowArrow(extra[0]), FadeIn(extra[1]), *[a.animate.set_opacity(0.35) for a in arrows]])
            self.play_step(len(forces), *animations, preferred=1.2)
