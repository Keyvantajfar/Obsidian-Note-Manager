"""Regenerate packaging/icon.png (an original glyph: a vault door with a tag notch). Needs Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# rounded square with a vertical gradient
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
top, bot = (62, 52, 140), (28, 24, 70)
for y in range(S):
    t = y / (S - 1)
    gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=255)
img.paste(grad, (0, 0), mask)
# vault door: ring + bolt handle
cx = cy = S // 2
ring = (235, 232, 255, 255)
d.ellipse((cx - 300, cy - 300, cx + 300, cy + 300), outline=ring, width=44)
d.ellipse((cx - 190, cy - 190, cx + 190, cy + 190), outline=(168, 160, 240, 255), width=26)
for ang_x, ang_y in ((0, -1), (1, 0), (0, 1), (-1, 0)):
    d.line([(cx, cy), (cx + ang_x * 190, cy + ang_y * 190)], fill=ring, width=34)
d.ellipse((cx - 62, cy - 62, cx + 62, cy + 62), fill=(255, 196, 92, 255))
# tag notch (top right)
d.polygon([(cx + 250, cy - 330), (cx + 400, cy - 330), (cx + 400, cy - 180), (cx + 325, cy - 105), (cx + 250, cy - 180)],
          fill=(255, 196, 92, 255))
d.ellipse((cx + 313, cy - 292, cx + 357, cy - 248), fill=(40, 34, 90, 255))
out = Path(__file__).with_name("icon.png")
img.resize((512, 512), Image.LANCZOS).save(out)
print("wrote", out)
