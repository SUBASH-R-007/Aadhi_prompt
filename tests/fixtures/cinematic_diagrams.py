"""Two plain educational diagrams for the cinematic browser check (Phase 13): a photosynthesis overview and a leaf
cross-section, drawn with Pillow so the check composes real-looking visuals (the AI stand-in only makes flat colours).

    python tests/fixtures/cinematic_diagrams.py <output folder>
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont


def font(size, bold=False):
    for name in (("arialbd.ttf" if bold else "arial.ttf"), "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def arrow(d, a, b, colour, width=8):
    d.line([a, b], fill=colour, width=width)
    import math
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    head = 26
    left = (b[0] - head * math.cos(ang - 0.45), b[1] - head * math.sin(ang - 0.45))
    right = (b[0] - head * math.cos(ang + 0.45), b[1] - head * math.sin(ang + 0.45))
    d.polygon([b, left, right], fill=colour)


def plant(path):
    img = Image.new("RGB", (900, 900), "#f6f3ea")
    d = ImageDraw.Draw(img)
    d.ellipse([60, 50, 230, 220], fill="#ffcc33", outline="#e0a800", width=6)  # sun
    for i in range(8):
        import math
        a = i * math.pi / 4
        d.line([(145 + 110 * math.cos(a), 135 + 110 * math.sin(a)), (145 + 150 * math.cos(a), 135 + 150 * math.sin(a))], fill="#e0a800", width=8)
    d.rectangle([430, 420, 470, 800], fill="#5b8c3a")  # stem
    d.ellipse([250, 330, 470, 470], fill="#4caf50", outline="#2e7d32", width=6)  # leaves
    d.ellipse([450, 280, 700, 430], fill="#4caf50", outline="#2e7d32", width=6)
    d.rectangle([300, 800, 600, 860], fill="#8d6e63")  # soil
    arrow(d, (230, 230), (360, 360), "#e0a800")  # light in
    arrow(d, (820, 560), (600, 430), "#607d8b")  # CO2 in
    arrow(d, (560, 300), (760, 170), "#1e88e5")  # O2 out
    arrow(d, (450, 850), (450, 520), "#29b6f6")  # water up
    f, b = font(40), font(44, True)
    d.text((250, 240), "Sunlight", font=b, fill="#8a6100")
    d.text((700, 590), "CO2", font=b, fill="#37474f")
    d.text((700, 110), "O2", font=b, fill="#1565c0")
    d.text((480, 700), "Water", font=f, fill="#0277bd")
    d.text((520, 450), "Glucose", font=f, fill="#2e7d32")
    img.save(path)


def leaf(path):
    img = Image.new("RGB", (1200, 800), "#f6f3ea")
    d = ImageDraw.Draw(img)
    layers = [("Upper epidermis", "#c5e1a5", 120, 170), ("Palisade cells", "#66bb6a", 170, 330), ("Spongy layer", "#a5d6a7", 330, 520),
              ("Lower epidermis", "#c5e1a5", 520, 570)]
    for _name, colour, y0, y1 in layers:
        d.rectangle([80, y0, 760, y1], fill=colour, outline="#2e7d32", width=4)
    for x in range(110, 740, 70):  # palisade columns
        d.rounded_rectangle([x, 182, x + 50, 318], radius=18, fill="#43a047", outline="#1b5e20", width=3)
    for x, y in ((150, 380), (300, 430), (460, 370), (620, 450), (240, 480), (540, 480)):  # spongy cells
        d.ellipse([x, y, x + 90, y + 55], fill="#81c784", outline="#2e7d32", width=3)
    d.ellipse([370, 548, 470, 590], fill="#f6f3ea", outline="#1b5e20", width=5)  # stoma
    f = font(38)
    for name, _c, y0, y1 in layers:
        d.line([(760, (y0 + y1) // 2), (820, (y0 + y1) // 2)], fill="#33691e", width=4)
        d.text((835, (y0 + y1) // 2 - 22), name, font=f, fill="#1b3a0e")
    d.line([(420, 590), (420, 660)], fill="#33691e", width=4)
    d.text((340, 668), "Stoma", font=f, fill="#1b3a0e")
    img.save(path)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    plant(os.path.join(out, "photosynthesis_diagram.png"))
    leaf(os.path.join(out, "leaf_cross_section.png"))
    print("ok")
