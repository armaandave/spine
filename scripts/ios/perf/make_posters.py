"""Perf harness: write deterministic poster JPEGs (no network) for the seeded titles.

    python3 make_posters.py <output dir> [count]

Each is 600x900 with a smooth colour field plus fine noise, so decoding and blurring cost
about what a real provider poster does. The same seed always gives the same files.
p00.jpg.. are posters; s00.jpg.. are the square crops used for album art.
"""
import colorsys
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

out = Path(sys.argv[1])
count = int(sys.argv[2]) if len(sys.argv) > 2 else 40
out.mkdir(parents=True, exist_ok=True)
for n in range(count):
    rng = random.Random(1000 + n)
    hue = rng.random()
    # A mix of light and dark artwork, like real posters.
    light = 0.25 if n % 3 else 0.8
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.6, min(1, light + 0.15)))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.15) % 1, 0.7, light - 0.1 if light > 0.3 else 0.1))
    img = Image.new("RGB", (600, 900))
    d = ImageDraw.Draw(img)
    for y in range(900):
        t = y / 899
        d.line([(0, y), (600, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    for _ in range(14):
        x, y, r = rng.randrange(600), rng.randrange(900), rng.randrange(40, 220)
        col = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(rng.random(), 0.5, rng.uniform(0.3, 0.95)))
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(18))
    noise = Image.effect_noise((600, 900), 24).convert("RGB")
    img = Image.blend(img, noise, 0.08)
    img.save(out / f"p{n:02d}.jpg", quality=82)
    img.crop((0, 150, 600, 750)).save(out / f"s{n:02d}.jpg", quality=82)
print(f"wrote {count * 2} images to {out}")
