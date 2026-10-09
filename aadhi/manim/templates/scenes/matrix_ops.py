# ruff: noqa: F403, F405
# Scene source for the matrix_ops template (read as text; see equation_steps.py).
from manim import *


class MatrixOpsScene(AadhiScene):
    def build_matrix(self, spec):
        entries = [[self.math(v, role="label") for v in row["values"]] for row in spec["rows"]]
        n_rows, n_cols = len(entries), len(entries[0])
        col_w = [max(entries[r][c].width for r in range(n_rows)) for c in range(n_cols)]
        row_h = max(max(e.height for e in row) for row in entries)
        gap_x, gap_y = 0.45, 0.32
        total_w = sum(col_w) + gap_x * (n_cols - 1)
        total_h = row_h * n_rows + gap_y * (n_rows - 1)
        grid = VGroup()
        for r in range(n_rows):
            x = -total_w / 2
            for c in range(n_cols):
                y = total_h / 2 - row_h / 2 - r * (row_h + gap_y)
                entries[r][c].move_to(np.array([x + col_w[c] / 2, y, 0.0]))
                grid.add(entries[r][c])
                x += col_w[c] + gap_x
        top, bottom = total_h / 2 + 0.15, -total_h / 2 - 0.15
        left, right = -total_w / 2 - 0.2, total_w / 2 + 0.2
        lb = VMobject(stroke_color=AADHI_MUTED, stroke_width=4).set_points_as_corners([
            np.array([left + 0.14, top, 0.0]), np.array([left, top, 0.0]),
            np.array([left, bottom, 0.0]), np.array([left + 0.14, bottom, 0.0])])
        rb = VMobject(stroke_color=AADHI_MUTED, stroke_width=4).set_points_as_corners([
            np.array([right - 0.14, top, 0.0]), np.array([right, top, 0.0]),
            np.array([right, bottom, 0.0]), np.array([right - 0.14, bottom, 0.0])])
        body = VGroup(lb, grid, rb)
        name = VGroup()
        if spec["name"]:
            name = self.label(spec["name"], role="label", color=AADHI_GOLD).next_to(body, UP, buff=0.18)
        return VGroup(body, name), entries

    def construct(self):
        p = PARAMS
        steps = p["steps"]
        self.declare_steps(len(steps))
        if p["title"]:
            self.show_title(p["title"])
        if any(s["note"] for s in steps):
            self.reserve_notes()
        has_text = any(s["text"] for s in steps)
        cx, cy, w, h = self.content_box()

        mats, cells = [], []
        for spec in p["matrices"]:
            mob, entries = self.build_matrix(spec)
            mats.append(mob)
            cells.append(entries)
        ops = []
        symbols = p["operators"] or (["×", "="][: len(mats) - 1] if len(mats) == 3 else ["×"] * (len(mats) - 1))
        row = VGroup()
        for k, mob in enumerate(mats):
            row.add(mob)
            if k < len(mats) - 1:
                op = self.label(symbols[k], role="heading", color=AADHI_WHITE)
                ops.append(op)
                row.add(op)
        avail_h = h - (0.9 if has_text else 0.0)
        best = None
        for direction in (RIGHT, DOWN):
            row.arrange(direction, buff=0.45)
            factor = min(w * 0.95 / row.width, avail_h * 0.95 / row.height, 1.5)
            if best is None or factor > best[1] * 1.15:
                best = (direction, factor)
        row.arrange(best[0], buff=0.45)
        factor = best[1]
        row.scale(factor)
        row.move_to(np.array([cx, cy + (0.45 if has_text else 0.0), 0.0]))
        calc_spot = np.array([cx, cy - h / 2 + 0.4, 0.0])

        shown_later = {s["matrix"] for s in steps if s["action"] == "show"}
        for k, mob in enumerate(mats):
            if k in shown_later:
                continue
            spec = p["matrices"][k]
            if spec["hidden"]:
                self.add(mob[0][0], mob[0][2], mob[1])
            else:
                self.add(mob)
            if k > 0 and (k - 1) not in shown_later:
                self.add(ops[k - 1])

        hidden = {k for k, spec in enumerate(p["matrices"]) if spec["hidden"]}
        boxes = VGroup()
        calc = VGroup()
        revealed = set()
        for i, step in enumerate(steps):
            action = step["action"]
            m = step["matrix"]
            animations = self.note(step["note"]) if step["note"] or self.note_mob else []
            new_boxes = VGroup()
            if action == "show":
                if m in hidden:
                    animations.append(FadeIn(VGroup(mats[m][0][0], mats[m][0][2], mats[m][1])))
                else:
                    animations.append(FadeIn(mats[m], shift=UP * 0.1))
                if m > 0:
                    animations.append(FadeIn(ops[m - 1]))
            elif action == "highlight_row":
                new_boxes.add(self.highlight_box(VGroup(*cells[m][step["row"]]), color=AADHI_CYAN))
            elif action == "highlight_col":
                new_boxes.add(self.highlight_box(VGroup(*[r[step["col"]] for r in cells[m]]), color=AADHI_PINK))
            elif action == "highlight_cell":
                new_boxes.add(self.highlight_box(cells[m][step["row"]][step["col"]], color=AADHI_GOLD))
            elif action == "product_entry":
                r, c = step["row"], step["col"]
                new_boxes.add(self.highlight_box(VGroup(*cells[0][r]), color=AADHI_CYAN))
                new_boxes.add(self.highlight_box(VGroup(*[rr[c] for rr in cells[1]]), color=AADHI_PINK))
                target = cells[2][r][c]
                new_boxes.add(self.highlight_box(target, color=AADHI_GOLD))
                if 2 in hidden and (2, r, c) not in revealed:
                    revealed.add((2, r, c))
                    animations.append(FadeIn(target, scale=1.3))
            elif action == "reveal_cell":
                target = cells[m][step["row"]][step["col"]]
                new_boxes.add(self.highlight_box(target, color=AADHI_GOLD))
                if m in hidden and (m, step["row"], step["col"]) not in revealed:
                    revealed.add((m, step["row"], step["col"]))
                    animations.append(FadeIn(target, scale=1.3))
            elif action == "reveal_all" and m in hidden:
                rest = [cells[m][r][c] for r in range(len(cells[m])) for c in range(len(cells[m][0]))
                        if (m, r, c) not in revealed]
                revealed.update((m, r, c) for r in range(len(cells[m])) for c in range(len(cells[m][0])))
                if rest:
                    animations.append(FadeIn(VGroup(*rest), lag_ratio=0.1))
            if len(boxes):
                animations.append(FadeOut(boxes))
            if len(new_boxes):
                animations.append(Create(new_boxes))
            boxes = new_boxes
            if step["text"] or len(calc):
                if len(calc):
                    animations.append(FadeOut(calc))
                calc = VGroup()
                if step["text"]:
                    calc = self.label(step["text"], role="body", color=AADHI_GOLD, max_width=w * 0.95).move_to(calc_spot)
                    animations.append(FadeIn(calc, shift=UP * 0.1))
            self.play_step(i, *animations, preferred=1.0)
