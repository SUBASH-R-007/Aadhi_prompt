# ruff: noqa: F403, F405
# Scene source for the wave template (read as text; see equation_steps.py).
from manim import *


def fmt(v):
    v = round(float(v), 3)
    return f"{v:g}"


class WaveScene(AadhiScene):
    def readout(self, step, unit):
        text = f"A = {fmt(step['amplitude'])}    f = {fmt(step['frequency'])} {unit}    φ = {fmt(step['phase_deg'])}°"
        return self.label(text, role="small", color=AADHI_GOLD)

    def construct(self):
        p = PARAMS
        steps = p["steps"]
        self.declare_steps(len(steps))
        if p["title"]:
            self.show_title(p["title"])
        if any(s["note"] for s in steps):
            self.reserve_notes()
        cx, cy, w, h = self.content_box()
        top_room = 0.6 if p["show_readout"] else 0.0
        left_room = 0.7  # y tick labels
        x_lab = self.label(p["x_label"], role="small", color=AADHI_MUTED)
        axes = Axes(
            x_range=[0, p["x_max"], p["x_step"]],
            y_range=[-p["y_max"], p["y_max"], p["y_step"]],
            x_length=w - left_room - x_lab.width - 0.3,
            y_length=(h - top_room) * 0.85,
            tips=False,
            axis_config={"color": AADHI_MUTED, "stroke_width": 2, "include_numbers": False},
        )
        axes.shift(np.array([cx - w / 2 + left_room, cy - top_room / 2, 0.0]) - axes.c2p(0, 0))
        ticks = VGroup()
        v = p["x_step"]
        while v <= p["x_max"] + 1e-9:
            ticks.add(self.label(fmt(v), role="small", color=AADHI_MUTED).next_to(axes.c2p(v, 0), DOWN, buff=0.1))
            v += p["x_step"]
        for v in (p["y_step"], -p["y_step"]):
            if abs(v) <= p["y_max"]:
                ticks.add(self.label(fmt(v), role="small", color=AADHI_MUTED).next_to(axes.c2p(0, v), LEFT, buff=0.1))
        x_lab.next_to(axes.c2p(p["x_max"], 0), RIGHT, buff=0.15)
        y_lab = self.label(p["y_label"], role="small", color=AADHI_MUTED).next_to(axes.c2p(0, p["y_max"]), RIGHT, buff=0.12)
        self.add(axes, ticks, x_lab, y_lab)

        amp = ValueTracker(steps[0]["amplitude"])
        freq = ValueTracker(steps[0]["frequency"])
        phase = ValueTracker(steps[0]["phase_deg"])
        use_cos = p["function"] == "cos"
        color = self.color(p["color"])
        x_end = p["x_max"]

        def wave_curve():
            a, f, ph = amp.get_value(), freq.get_value(), phase.get_value() * DEGREES
            if use_cos:
                return axes.plot(lambda x: a * np.cos(2 * PI * f * x + ph), x_range=[0, x_end, x_end / 300],
                                 color=color, stroke_width=5)
            return axes.plot(lambda x: a * np.sin(2 * PI * f * x + ph), x_range=[0, x_end, x_end / 300],
                             color=color, stroke_width=5)

        curve = wave_curve()
        live = None
        readout = VGroup()
        readout_spot = np.array([cx, cy + h / 2 - top_room / 2, 0.0])
        ghosts = VGroup()
        for i, step in enumerate(steps):
            animations = self.note(step["note"]) if step["note"] or self.note_mob else []
            if p["show_readout"]:
                new_readout = self.readout(step, p["unit_frequency"]).move_to(readout_spot)
                if new_readout.width > w:
                    new_readout.scale_to_fit_width(w)
                if len(readout):
                    animations.append(FadeOut(readout))
                animations.append(FadeIn(new_readout))
                readout = new_readout
            if i == 0:
                animations.append(Create(curve))
                self.play_step(i, *animations, preferred=1.5)
                live = always_redraw(wave_curve)
                live.suspend_updating()
                self.remove(curve)
                self.add(live)
                continue
            if step["keep_previous"]:
                ghost = live.copy().clear_updaters().set_stroke(opacity=0.3)
                ghosts.add(ghost)
                self.add(ghost)
            elif len(ghosts):
                animations.append(FadeOut(ghosts))
                ghosts = VGroup()
            animations.extend([
                amp.animate.set_value(step["amplitude"]),
                freq.animate.set_value(step["frequency"]),
                phase.animate.set_value(step["phase_deg"]),
            ])
            self.wait_until_beat(i)
            live.resume_updating()
            self.play_step(i, *animations, preferred=1.5)
            live.suspend_updating()
