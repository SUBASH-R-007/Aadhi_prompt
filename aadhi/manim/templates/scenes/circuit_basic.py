# ruff: noqa: F403, F405
# Scene source for the circuit_basic template (read as text; see equation_steps.py).
from manim import *

WIRE = 4


def pt(x, y):
    return np.array([x, y, 0.0])


def zigzag(start, end, amplitude=0.17, teeth=6):
    direction = end - start
    length = np.linalg.norm(direction)
    unit = direction / length
    normal = np.array([-unit[1], unit[0], 0.0])
    lead = length * 0.12
    points = [start, start + unit * lead]
    body = length - 2 * lead
    for k in range(teeth):
        frac = (k + 0.5) / teeth
        sign = 1 if k % 2 == 0 else -1
        points.append(start + unit * (lead + body * frac) + normal * amplitude * sign)
    points.extend([end - unit * lead, end])
    return VMobject(stroke_color=AADHI_WHITE, stroke_width=WIRE).set_points_as_corners(points)


def wire(*points):
    return VMobject(stroke_color=AADHI_MUTED, stroke_width=WIRE).set_points_as_corners(list(points))


class CircuitBasicScene(AadhiScene):
    def construct(self):
        p = PARAMS
        steps = p["steps"]
        resistors = p["resistors"]
        n = len(resistors)
        series = p["topology"] == "series"
        self.declare_steps(len(steps))
        if p["title"]:
            self.show_title(p["title"])
        if any(s["note"] for s in steps):
            self.reserve_notes()
        has_eq = any(s["action"] == "equation" for s in steps)
        revealed_source = any(s["action"] == "source" for s in steps)
        revealed_res = {s["index"] for s in steps if s["action"] == "resistor"}

        big_w = 6.4 if series else 1.9 * n + 2.2
        big_h = 3.6
        W, H = big_w, big_h
        TL, TR, BR, BL = pt(-W / 2, H / 2), pt(W / 2, H / 2), pt(W / 2, -H / 2), pt(-W / 2, -H / 2)
        bat_top, bat_bot = pt(-W / 2, 0.12), pt(-W / 2, -0.12)
        plus_plate = Line(pt(-W / 2 - 0.4, 0.12), pt(-W / 2 + 0.4, 0.12), color=AADHI_WHITE, stroke_width=5)
        minus_plate = Line(pt(-W / 2 - 0.2, -0.12), pt(-W / 2 + 0.2, -0.12), color=AADHI_WHITE, stroke_width=10)
        signs = VGroup(
            self.label("+", role="label", color=AADHI_GOLD).move_to(pt(-W / 2 + 0.62, 0.36)),
            self.label("−", role="label", color=AADHI_GOLD).move_to(pt(-W / 2 + 0.62, -0.36)),
        )
        battery = VGroup(plus_plate, minus_plate, signs)
        src_name = self.label(p["source_label"], role="label", color=AADHI_GOLD).next_to(battery, LEFT, buff=0.25)
        src_value = VGroup()
        if p["source_value"]:
            src_value = self.label(p["source_value"], role="small", color=AADHI_GOLD).next_to(src_name, DOWN, buff=0.1)

        wires = VGroup(wire(bat_top, TL), wire(bat_bot, BL))
        parts = []
        loops = []
        if series:
            xs = [-W / 2 + W * (k + 1) / (n + 1) for k in range(n)]
            seg = min(1.2, W / (n + 1) * 0.7)
            cursor = TL
            for k, x in enumerate(xs):
                a, b = pt(x - seg / 2, H / 2), pt(x + seg / 2, H / 2)
                wires.add(wire(cursor, a))
                cursor = b
                body = zigzag(a, b)
                name = self.label(resistors[k]["label"], role="label").next_to(body, UP, buff=0.2)
                value = VGroup()
                if resistors[k]["value"]:
                    value = self.label(resistors[k]["value"], role="small", color=AADHI_CYAN).next_to(name, UP, buff=0.08)
                parts.append({"body": body, "name": name, "value": value, "brace_dir": DOWN})
            wires.add(wire(cursor, TR, BR, BL))
            loops.append(wire(bat_top, TL, TR, BR, BL, bat_bot))
            arrow = Arrow(pt(W / 2 + 0.35, 0.7), pt(W / 2 + 0.35, -0.7), buff=0, color=AADHI_GOLD, stroke_width=6)
            arrow_dir = RIGHT
        else:
            xs = [-W / 2 + W * (k + 1) / n for k in range(n)]
            wires.add(wire(TL, pt(xs[-1], H / 2)), wire(BL, pt(xs[-1], -H / 2)))
            for k, x in enumerate(xs):
                a, b = pt(x, 0.6), pt(x, -0.6)
                wires.add(wire(pt(x, H / 2), a), wire(b, pt(x, -H / 2)))
                body = zigzag(a, b)
                name = self.label(resistors[k]["label"], role="label").next_to(body, RIGHT, buff=0.22)
                value = VGroup()
                if resistors[k]["value"]:
                    value = self.label(resistors[k]["value"], role="small", color=AADHI_CYAN).next_to(name, DOWN, buff=0.08)
                parts.append({"body": body, "name": name, "value": value, "brace_dir": LEFT})
                loops.append(wire(bat_top, TL, pt(x, H / 2), pt(x, -H / 2), BL, bat_bot))
            x0 = xs[0]
            arrow = Arrow(pt(-W / 2 + 0.25, H / 2 + 0.38), pt(min(x0 - 0.25, -W / 2 + 1.6), H / 2 + 0.38),
                          buff=0, color=AADHI_GOLD, stroke_width=6)
            arrow_dir = UP
        for loop in loops:
            loop.set_stroke(opacity=0)

        # Current labels are laid out now so the fit-to-zone scaling below leaves room for them.
        current_labels = {}
        for i, step in enumerate(steps):
            if step["action"] == "current" and step["text"]:
                tag = self.label(step["text"], role="small", color=AADHI_GOLD)
                current_labels[i] = tag.next_to(arrow, arrow_dir, buff=0.12)
        circuit = VGroup(wires, battery, src_name, *[VGroup(q["body"], q["name"]) for q in parts])
        hidden = VGroup(src_value, *[q["value"] for q in parts], arrow, *loops, *current_labels.values())
        whole = VGroup(circuit, hidden)

        cx, cy, w, h = self.content_box()
        if has_eq and not self.IS_PANEL:
            zone_c = (cx - w * 0.2, cy, w * 0.58, h)
            eq_zone = (cx + w * 0.3, cy, w * 0.38, h)
        elif has_eq:
            zone_c = (cx, cy + h * 0.22, w, h * 0.55)
            eq_zone = (cx, cy - h * 0.3, w, h * 0.4)
        else:
            zone_c = (cx, cy, w, h)
            eq_zone = (cx, cy, w, h)
        scale = min(zone_c[2] * 0.92 / whole.width, zone_c[3] * 0.92 / whole.height, 1.6)
        whole.scale(scale)
        whole.move_to(pt(zone_c[0], zone_c[1]))

        start_hidden = steps[0]["action"] == "draw"
        visible_now = [circuit]
        if not revealed_source:
            visible_now.append(src_value)
        for k, q in enumerate(parts):
            if k not in revealed_res:
                visible_now.append(q["value"])
        if not start_hidden:
            self.add(*visible_now)

        dots = VGroup()
        flow = ValueTracker(0.0)
        eq_count = 0
        eq_slots = max(1, sum(1 for s in steps if s["action"] == "equation"))
        current_label = VGroup()
        for i, step in enumerate(steps):
            action = step["action"]
            animations = self.note(step["note"]) if step["note"] or self.note_mob else []
            if action == "draw":
                animations.append(Create(wires))
                animations.extend(FadeIn(m) for m in visible_now[1:])
                animations.extend([FadeIn(battery), FadeIn(src_name)])
                animations.extend(Create(q["body"]) for q in parts)
                animations.extend(FadeIn(q["name"]) for q in parts)
            elif action == "source":
                animations.extend([Indicate(battery, color=AADHI_GOLD), FadeIn(src_value)])
            elif action == "resistor":
                q = parts[step["index"]]
                animations.extend([Indicate(q["body"], color=AADHI_CYAN), FadeIn(q["value"])])
            elif action == "voltage_drop":
                q = parts[step["index"]]
                brace = Brace(q["body"], direction=q["brace_dir"], color=AADHI_PINK)
                tag = self.label(step["text"] or "V", role="small", color=AADHI_PINK)
                tag.next_to(brace, q["brace_dir"], buff=0.08)
                animations.extend([GrowFromCenter(brace), FadeIn(tag)])
            elif action == "current":
                if len(dots) == 0:
                    per_loop = 10 if series else 6
                    for loop in loops:
                        for k in range(per_loop):
                            d = Dot(radius=0.05 * max(scale, 0.6), color=AADHI_GOLD)
                            d.move_to(loop.point_from_proportion(k / per_loop))
                            dots.add(d)
                    lp = len(dots) // len(loops)

                    def place(group, lp=lp):
                        t = flow.get_value()
                        for j, d in enumerate(group):
                            path = loops[j // lp]
                            d.move_to(path.point_from_proportion(((j % lp) / lp + t) % 1.0))

                    dots.add_updater(place)
                    flow.add_updater(lambda m, dt: m.increment_value(dt * 0.12))
                    self.add(flow)
                    animations.append(FadeIn(dots))
                    animations.append(GrowArrow(arrow))
                new_label = current_labels.get(i, VGroup())
                if len(new_label):
                    animations.append(FadeIn(new_label))
                if len(current_label):
                    animations.append(FadeOut(current_label))
                current_label = new_label
            elif action == "equation":
                ex, ey, ew, eh = eq_zone
                slot_h = eh / eq_slots
                eq = self.math(step["latex"], role="math", color=AADHI_WHITE)
                if eq.width > ew * 0.95:
                    eq.scale_to_fit_width(ew * 0.95)
                if eq.height > slot_h * 0.8:
                    eq.scale_to_fit_height(slot_h * 0.8)
                eq.move_to(pt(ex, ey + eh / 2 - slot_h * (eq_count + 0.5)))
                eq_count += 1
                animations.append(Write(eq))
            self.play_step(i, *animations, preferred=1.2)
