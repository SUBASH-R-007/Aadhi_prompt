# ruff: noqa: F403, F405
# Scene source for the bar_compare template (read as text; see equation_steps.py).
from manim import *


class BarCompareScene(AadhiScene):
    def construct(self):
        p = PARAMS
        bars = p["bars"]
        n = len(bars)
        bar_steps = p["bar_steps"]
        self.declare_steps(bar_steps + (1 if p["highlight_max"] else 0))
        if p["title"]:
            self.show_title(p["title"])
        if any(b["note"] for b in bars) or p["highlight_note"]:
            self.reserve_notes()
        cx, cy, w, h = self.content_box()
        horizontal = self.IS_PANEL
        span = p["v_max"] - p["v_min"]
        has_negative = p["v_min"] < 0
        if horizontal:
            label_w = w * 0.3
            tag_room = min(max(self.label(b["display"], role="small").width for b in bars), w * 0.3) + 0.15
            # Value tags sit beyond the bar ends: right of positive bars, left of negative ones.
            neg_room = tag_room if has_negative else 0.0
            length = max(w - label_w - 0.25 - tag_room - neg_room, w * 0.25)
            slot = h / n
            thickness = min(slot * 0.62, 0.7)
            base = cx - w / 2 + label_w + 0.1 + neg_room + length * (-p["v_min"] / span)
            axis = Line(np.array([base, cy + h / 2, 0.0]), np.array([base, cy - h / 2, 0.0]), color=AADHI_MUTED, stroke_width=3)
        else:
            label_h = 0.75
            # Negative bars carry their value tag below them: keep it clear of the category names.
            neg_room = self.label("0", role="small").height + 0.2 if has_negative else 0.0
            length = h - label_h - 0.6 - neg_room
            slot = w / n
            thickness = min(slot * 0.6, 1.4)
            base = cy - h / 2 + label_h + neg_room + length * (-p["v_min"] / span)
            axis = Line(np.array([cx - w / 2, base, 0.0]), np.array([cx + w / 2, base, 0.0]), color=AADHI_MUTED, stroke_width=3)
        self.add(axis)
        if p["y_label"] and not horizontal:
            y_label = self.label(p["y_label"], role="small", color=AADHI_MUTED).rotate(PI / 2)
            y_label.move_to(np.array([cx - w / 2 + 0.05, base + length * 0.4, 0.0]))
            self.add(y_label)

        rects = []
        tags = []
        names = []
        for k, bar in enumerate(bars):
            color = self.color(bar["color"])
            size = max(abs(bar["value"]) / span * length, 0.02)
            positive = bar["value"] >= 0
            if horizontal:
                y = cy + h / 2 - slot * (k + 0.5)
                rect = Rectangle(width=size, height=thickness, stroke_width=0)
                rect.set_fill(color, opacity=0.9)
                rect.move_to(np.array([base + (size / 2 if positive else -size / 2), y, 0.0]))
                name = self.label(bar["label"], role="small", max_width=label_w - 0.2)
                name.move_to(np.array([cx - w / 2 + label_w / 2, y, 0.0]))
                tag = self.label(bar["display"], role="small", color=color, max_width=w * 0.3)
                tag.next_to(rect, RIGHT if positive else LEFT, buff=0.12)
            else:
                x = cx - w / 2 + slot * (k + 0.5)
                rect = Rectangle(width=thickness, height=size, stroke_width=0)
                rect.set_fill(color, opacity=0.9)
                rect.move_to(np.array([x, base + (size / 2 if positive else -size / 2), 0.0]))
                name = self.label(bar["label"], role="small", max_width=slot * 0.92)
                name.move_to(np.array([x, cy - h / 2 + 0.3, 0.0]))
                tag = self.label(bar["display"], role="small", color=color, max_width=slot * 0.95)
                tag.next_to(rect, UP if positive else DOWN, buff=0.1)
            rects.append(rect)
            tags.append(tag)
            names.append(name)

        for step in range(bar_steps):
            animations = []
            note = next((b["note"] for b in bars if b["step"] == step and b["note"]), "")
            if note or self.note_mob:
                animations.extend(self.note(note))
            for k, bar in enumerate(bars):
                if bar["step"] == step:
                    if horizontal:
                        edge = LEFT if bar["value"] >= 0 else RIGHT
                    else:
                        edge = DOWN if bar["value"] >= 0 else UP
                    animations.extend([GrowFromEdge(rects[k], edge), FadeIn(names[k]), FadeIn(tags[k])])
            self.play_step(step, *animations, preferred=1.0)

        if p["highlight_max"]:
            k = p["max_index"]
            animations = self.note(p["highlight_note"]) if p["highlight_note"] or self.note_mob else []
            others = [r.animate.set_fill(opacity=0.35) for j, r in enumerate(rects) if j != k]
            animations.extend([Circumscribe(VGroup(rects[k], tags[k]), color=AADHI_GOLD), *others])
            self.play_step(bar_steps, *animations, preferred=1.2)
