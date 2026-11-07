"""Perf harness: write deterministic poster JPEGs (no network) for the seeded titles.

    python3 make_posters.py <output dir> [count]

Each is 600x900 with a smooth colour field plus fine noise, so decoding and blurring cost
about what a real provider poster does. The same seed always gives the same files.
p00.jpg.. are posters; s00.jpg.. are the square crops used for album art.

The Home redesign's data set (seed_perf.py) also needs art that decides the card's ink on
purpose, so these are written too, always the same count:
  l00..l07  light posters (pale, high luminance)      d00..d07  dark posters (near black)
  m00..m03  mid-tone posters (luma near 0.5, the ink's dead band)
  q00..q07  square album art, alternating light and dark (600x600)
  e00..e05  landscape episode stills (960x540)
  a00..a04  avatars (160x160)
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


def field(n, size, base, spread, sat, shapes, seed, dark_shapes=False):
    """A smooth colour field: a vertical gradient from `base` to a shifted hue, plus soft shapes."""
    w, h = size
    rng = random.Random(seed)
    hue = rng.random()
    lo, hi = base
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, hi))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + spread) % 1, min(1, sat + 0.1), lo))
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    for _ in range(shapes):
        x, y, r = rng.randrange(w), rng.randrange(h), rng.randrange(w // 14, w // 3)
        value = rng.uniform(0.15, 0.4) if dark_shapes else rng.uniform(hi - 0.25, hi)
        col = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(rng.random(), sat * rng.uniform(0.4, 1.0), value))
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(max(w, h) // 40))
    noise = Image.effect_noise(size, 24).convert("RGB")
    return Image.blend(img, noise, 0.06)


for n in range(8):
    # Pale posters with a few softer shapes: the baked card stays light, ink is dark.
    field(n, (600, 900), (0.82, 0.97), 0.1, 0.22, 12, 2000 + n).save(out / f"l{n:02d}.jpg", quality=82)
    # Near-black posters with deep, saturated glows: ink is light.
    field(n, (600, 900), (0.05, 0.22), 0.2, 0.8, 12, 3000 + n, dark_shapes=True).save(out / f"d{n:02d}.jpg", quality=82)
    # Square album art, light on even numbers and dark on odd.
    if n % 2 == 0:
        square = field(n, (600, 600), (0.7, 0.95), 0.15, 0.3, 10, 4000 + n)
    else:
        square = field(n, (600, 600), (0.06, 0.3), 0.2, 0.75, 10, 4000 + n, dark_shapes=True)
    square.save(out / f"q{n:02d}.jpg", quality=82)
for n in range(4):
    field(n, (600, 900), (0.5, 0.74), 0.12, 0.4, 14, 5000 + n).save(out / f"m{n:02d}.jpg", quality=82)
for n in range(6):
    still = field(n, (960, 540), (0.1, 0.7), 0.2, 0.55, 12, 6000 + n, dark_shapes=n % 2 == 0)
    still.save(out / f"e{n:02d}.jpg", quality=82)
for n in range(5):
    field(n, (160, 160), (0.3, 0.85), 0.25, 0.5, 5, 7000 + n).save(out / f"a{n:02d}.jpg", quality=88)
print(f"wrote {count * 2 + 8 * 3 + 4 + 6 + 5} images to {out}")
