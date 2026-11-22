# Make a chat-sized copy of a demo GIF (≤ 10 MB): animated WebP, 800 px wide, ~10 fps.
# Usage: uv run --no-project --with pillow python tools/shrink-gif.py in.gif out.webp
import sys
from PIL import Image, ImageSequence

src, out = sys.argv[1], sys.argv[2]
im = Image.open(src)
dur = im.info.get("duration", 83) or 83
step = max(1, round(100 / dur))
frames = [f.convert("RGB") for i, f in enumerate(ImageSequence.Iterator(im)) if i % step == 0]
w = 800
h = round(frames[0].height * w / frames[0].width)
frames = [f.resize((w, h), Image.LANCZOS) for f in frames]
frames[0].save(out, save_all=True, append_images=frames[1:], duration=dur * step, loop=0, quality=72, method=4)
