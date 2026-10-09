# ruff: noqa: F403, F405
# Scene source for the function_plot template (read as text; see equation_steps.py).
from manim import *


def fmt(v):
    v = round(float(v), 4)
    return f"{v:g}"


class FunctionPlotScene(AadhiScene):
    def construct(self):
        p = PARAMS
        steps = p["steps"]
        self.declare_steps(len(steps))
        if p["title"]:
            self.show_title(p["title"])
        if any(s["note"] for s in steps):
            self.reserve_notes()
        cx, cy, w, h = self.content_box()
        x0, x1, y0, y1 = p["x_min"], p["x_max"], p["y_min"], p["y_max"]
        axes = Axes(
            x_range=[x0, x1, p["x_step"]],
            y_range=[y0, y1, p["y_step"]],
            x_length=w * 0.86,
            y_length=h * 0.82,
            tips=False,
            axis_config={"color": AADHI_MUTED, "stroke_width": 2, "include_numbers": False},
        )
        axes.move_to(np.array([cx + w * 0.03, cy + h * 0.02, 0.0]))
        axis_y = min(max(0.0, y0), y1)
        axis_x = min(max(0.0, x0), x1)
        ticks = VGroup()
        v = np.ceil(x0 / p["x_step"]) * p["x_step"]
        while v <= x1 + 1e-9:
            if abs(v - axis_x) > 1e-9:
                ticks.add(self.label(fmt(v), role="small", color=AADHI_MUTED).next_to(axes.c2p(v, axis_y), DOWN, buff=0.12))
            v += p["x_step"]
        v = np.ceil(y0 / p["y_step"]) * p["y_step"]
        while v <= y1 + 1e-9:
            if abs(v - axis_y) > 1e-9:
                ticks.add(self.label(fmt(v), role="small", color=AADHI_MUTED).next_to(axes.c2p(axis_x, v), LEFT, buff=0.12))
            v += p["y_step"]
        x_lab = self.label(p["x_label"], role="label", color=AADHI_MUTED).next_to(axes.c2p(x1, axis_y), UR, buff=0.1)
        y_lab = self.label(p["y_label"], role="label", color=AADHI_MUTED).next_to(axes.c2p(axis_x, y1), UR, buff=0.1)
        self.add(axes, ticks, x_lab, y_lab)

        curves = []
        labels = []
        for k, fn in enumerate(p["functions"]):
            data = p["curves"][k]
            group = VGroup()
            segment = []
            for xv, yv in list(zip(data["xs"], data["ys"])) + [(None, None)]:
                if yv is None or xv is None:
                    if len(segment) >= 2:
                        piece = VMobject(stroke_color=self.color(fn["color"]), stroke_width=5)
                        piece.set_points_smoothly(segment)
                        group.add(piece)
                    segment = []
                else:
                    segment.append(axes.c2p(xv, yv))
            curves.append(group)
            lab = VGroup()
            if fn["label_latex"]:
                lab = self.math(fn["label_latex"], role="label", color=self.color(fn["color"]))
                anchor = group[-1].get_end() if len(group) else axes.c2p(x1, y1)
                lab.next_to(anchor, UL, buff=0.15)
                lab.shift(LEFT * max(0.0, lab.get_right()[0] - (cx + w / 2)))
                lab.shift(DOWN * max(0.0, lab.get_top()[1] - (cy + h / 2)))
            labels.append(lab)

        shown = set()
        dot = None
        dot_fn = None
        dot_label = None
        tracker = ValueTracker(0.0)
        tangent = None
        area = None
        vline = None
        for i, step in enumerate(steps):
            info = p["details"][i]
            k = step["function_index"]
            action = step["action"]
            animations = self.note(step["note"]) if step["note"] or self.note_mob else []
            if action != "note" and k not in shown:
                shown.add(k)
                animations.append(Create(curves[k]))
                if len(labels[k]):
                    animations.append(FadeIn(labels[k]))
            if action == "dot":
                pt = axes.c2p(step["x"], info["y"])
                new_label = self.label(f"({fmt(step['x'])}, {fmt(info['y'])})", role="small", color=AADHI_GOLD)
                new_label.next_to(pt, UL if (info.get("slope") or 0) >= 0 else UR, buff=0.12)
                if dot is not None and dot_fn == k:
                    pairs = [(a, b) for a, b in zip(p["curves"][k]["xs"], p["curves"][k]["ys"]) if b is not None]
                    xs = [a for a, b in pairs]
                    ys = [b for a, b in pairs]
                    dot.add_updater(lambda d, xs=xs, ys=ys: d.move_to(axes.c2p(tracker.get_value(), np.interp(tracker.get_value(), xs, ys))))
                    self.play_step(i, *animations, FadeOut(dot_label), tracker.animate.set_value(step["x"]), preferred=1.4)
                    dot.clear_updaters()
                    dot.move_to(pt)
                    self.play(FadeIn(new_label), run_time=self.step_run_time(i, 0.4))
                else:
                    if dot is not None:
                        animations.extend([FadeOut(dot), FadeOut(dot_label)])
                    dot = Dot(pt, radius=0.09, color=AADHI_GOLD)
                    tracker.set_value(step["x"])
                    self.play_step(i, *animations, FadeIn(dot, scale=0.5), FadeIn(new_label), preferred=0.8)
                dot_fn = k
                dot_label = new_label
            elif action == "tangent":
                new = Line(axes.c2p(*info["p1"]), axes.c2p(*info["p2"]), color=AADHI_PINK, stroke_width=4)
                slope = self.label(f"slope = {fmt(info['slope'])}", role="small", color=AADHI_PINK)
                along = new.get_unit_vector()
                slope.move_to(new.get_end() + along * (0.15 + slope.width / 2) + DOWN * 0.05)
                if slope.get_right()[0] > cx + w / 2:
                    slope.next_to(new.get_end(), DOWN if info["slope"] >= 0 else UP, buff=0.15)
                group = VGroup(new, slope)
                if tangent is not None:
                    animations.append(FadeOut(tangent))
                if dot is None or dot_fn != k:
                    animations.append(FadeIn(Dot(axes.c2p(step["x"], info["y"]), radius=0.07, color=AADHI_PINK)))
                animations.extend([Create(new), FadeIn(slope)])
                tangent = group
                self.play_step(i, *animations, preferred=1.0)
            elif action == "area":
                poly = Polygon(*[axes.c2p(a, b) for a, b in info["polygon"]], stroke_width=0)
                poly.set_fill(self.color(p["functions"][k]["color"]), opacity=0.35)
                if area is not None:
                    animations.append(FadeOut(area))
                if tangent is not None:
                    animations.append(FadeOut(tangent))
                    tangent = None
                animations.append(FadeIn(poly))
                area = poly
                self.play_step(i, *animations, preferred=1.0)
            elif action == "vline":
                line = DashedLine(axes.c2p(step["x"], y0), axes.c2p(step["x"], y1), color=AADHI_GOLD, stroke_width=3)
                tag = self.label(f"x = {fmt(step['x'])}", role="small", color=AADHI_GOLD).next_to(line, UP, buff=0.08)
                if vline is not None:
                    animations.append(FadeOut(vline))
                vline = VGroup(line, tag)
                animations.extend([Create(line), FadeIn(tag)])
                self.play_step(i, *animations, preferred=0.8)
            else:
                self.play_step(i, *animations, preferred=0.8)
