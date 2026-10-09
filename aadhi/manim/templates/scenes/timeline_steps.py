# ruff: noqa: F403, F405
# Scene source for the timeline_steps template (read as text; see equation_steps.py).
from manim import *


class TimelineStepsScene(AadhiScene):
    def card(self, event, width, color, align_left=False):
        head = self.paragraph(event["title"], width=width, role="body", color=color, max_lines=2)
        parts = [head]
        if event["detail"]:
            parts.append(self.paragraph(event["detail"], width=width, role="small", color=AADHI_WHITE, max_lines=3))
        return VGroup(*parts).arrange(DOWN, buff=0.1, aligned_edge=LEFT if align_left else ORIGIN)

    def construct(self):
        p = PARAMS
        events = p["events"]
        n = len(events)
        self.declare_steps(n)
        if p["title"]:
            self.show_title(p["title"])
        cx, cy, w, h = self.content_box()
        # Side panels, and long timelines that would crowd a horizontal line, run top to bottom.
        vertical = self.IS_PANEL or n >= 7
        # A vertical timeline in a 16:9 frame is a centred column (cards beside the line stay readable).
        col_w = w if self.IS_PANEL else min(w, 9.0)
        if vertical:
            x_line = cx - col_w / 2 + 0.9
            start, end = np.array([x_line, cy + h / 2, 0.0]), np.array([x_line, cy - h / 2, 0.0])
        else:
            start, end = np.array([cx - w / 2, cy, 0.0]), np.array([cx + w / 2, cy, 0.0])
        line = Line(start, end, color=AADHI_MUTED, stroke_width=4)
        self.add(line)
        marker_h = max(self.label(e["marker"], role="label").height for e in events)

        items = []
        for k, event in enumerate(events):
            color = self.color(event["color"])
            frac = (k + 0.5) / n
            spot = start + (end - start) * frac
            dot = Dot(spot, radius=0.12, color=color)
            ring = Circle(radius=0.22, color=color, stroke_width=3).move_to(spot)
            marker = self.label(event["marker"], role="label", color=color)
            if vertical:
                slot_h = h / n
                marker.next_to(dot, LEFT, buff=0.18)
                if marker.width > 0.8:
                    marker.scale_to_fit_width(0.8)
                    marker.next_to(dot, LEFT, buff=0.12)
                card = self.card(event, col_w - 1.4, color, align_left=True)
                if card.height > slot_h * 0.92:
                    card.scale_to_fit_height(slot_h * 0.92)
                card.next_to(dot, RIGHT, buff=0.3)
            else:
                slot_w = w / n
                # Cards alternate above/below the line; same-side neighbours are two slots apart.
                card_w = min(slot_w * 1.85, 3.6)
                above = k % 2 == 0
                marker.next_to(dot, DOWN if above else UP, buff=0.2)
                if marker.width > slot_w * 0.95:
                    marker.scale_to_fit_width(slot_w * 0.95)
                # Clear the marker zone of the neighbouring events (markers sit on the other side).
                card_buff = 0.2 + marker_h + 0.2
                card = self.card(event, card_w, color)
                if card.height > h / 2 - card_buff - 0.1:
                    card.scale_to_fit_height(h / 2 - card_buff - 0.1)
                card.next_to(dot, UP if above else DOWN, buff=card_buff)
                dx = max(0.0, (cx - w / 2) - card.get_left()[0]) - max(0.0, card.get_right()[0] - (cx + w / 2))
                card.shift(RIGHT * dx)
                tip = card.get_bottom() + DOWN * 0.08 if above else card.get_top() + UP * 0.08
                stem = Line(dot.get_center(), tip, color=color, stroke_width=2)
                stem.set_opacity(0.6)
                card = VGroup(stem, card)
            items.append((dot, ring, marker, card))

        previous = None
        for k, (dot, ring, marker, card) in enumerate(items):
            animations = [FadeIn(dot, scale=0.5), Create(ring), FadeIn(marker), FadeIn(card, shift=UP * 0.15)]
            if previous is not None:
                animations.extend([FadeOut(previous[1]), previous[3].animate.set_opacity(0.6)])
            self.play_step(k, *animations, preferred=1.0)
            previous = (dot, ring, marker, card)
