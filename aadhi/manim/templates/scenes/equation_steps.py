# ruff: noqa: F403, F405
# Scene source for the equation_steps template. Read as TEXT and prefixed with PARAMS + the
# Aadhi runtime; never imported. Must pass aadhi.manim.guard.check_code.
from manim import *


class EquationStepsScene(AadhiScene):
    def construct(self):
        p = PARAMS
        steps = p["steps"]
        self.declare_steps(len(steps))
        if p["title"]:
            self.show_title(p["title"])
        if any(s["annotation"] for s in steps):
            self.reserve_notes()
        color = self.color(p["color"])
        stack = p["layout"] == "stack"
        cx, cy, w, h = self.content_box()
        current = None
        lines = VGroup()
        for i, step in enumerate(steps):
            eq = self.math(step["latex"], color=color, split=True, size=self.text_size("math") * (1.2 if stack else 1.5))
            if eq.width > w:
                eq.scale_to_fit_width(w)
            if eq.height > h * 0.45:
                eq.scale_to_fit_height(h * 0.45)
            animations = self.note(step["annotation"]) if step["annotation"] or self.note_mob else []
            if stack:
                group = VGroup(*[m.copy() for m in lines], eq).arrange(DOWN, buff=0.45)
                self.fit_to_safe_area(group)
                for old, new in zip(lines, group[:-1]):
                    animations.append(Transform(old, new.set_opacity(0.55)))
                animations.append(Write(eq) if i == 0 else FadeIn(eq, shift=DOWN * 0.2))
                lines.add(eq)
                current = eq
            else:
                eq.move_to(np.array([cx, cy, 0.0]))
                if current is None:
                    animations.append(Write(eq))
                elif self.HAS_LATEX:
                    animations.append(TransformMatchingTex(current, eq))
                else:
                    animations.append(TransformMatchingShapes(current, eq))
                current = eq
            self.play_step(i, *animations, preferred=1.3)
        if p["box_final"] and current is not None:
            left = self.TOTAL_DURATION - self.now()
            if left > 0.5:
                box = self.highlight_box(current)
                self.play(Create(box), run_time=min(0.6, left * 0.4))
