"""A poster's palette: its biggest colors by share of the pixels."""

from io import BytesIO

from django.test import SimpleTestCase
from PIL import Image

from app.utils.color import extract_palette


def _png(rows):
    """A 96-pixel-wide PNG made of (color, row count) bands, top to bottom."""
    image = Image.new("RGB", (96, sum(count for _, count in rows)))
    y = 0
    for color, count in rows:
        image.paste(color, (0, y, 96, y + count))
        y += count
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ExtractPaletteTests(SimpleTestCase):
    """Shares are objective: every pixel counts, black included, and near-identical shades count as one."""

    def test_colors_come_biggest_share_first_with_near_identical_shades_as_one(self):
        palette = extract_palette(
            _png([
                ((0, 0, 0), 36),
                ((20, 20, 20), 36),  # near-black: counts as black
                ((200, 30, 30), 24),
            ]),
        )

        self.assertEqual(len(palette), 2)
        black, red = palette
        self.assertAlmostEqual(black["share"], 0.75, delta=0.01)
        self.assertAlmostEqual(red["share"], 0.25, delta=0.01)
        self.assertLess(max(int(black["hex"][i : i + 2], 16) for i in (1, 3, 5)), 24)
        self.assertEqual(red["hex"], "#C81E1E")

    def test_keeps_only_the_five_biggest(self):
        bands = [(color, 16) for color in [(0, 0, 0), (255, 255, 255), (220, 30, 30), (30, 160, 40), (30, 60, 220)]]
        palette = extract_palette(_png([*bands, ((240, 200, 20), 8)]))

        self.assertEqual(len(palette), 5)
        self.assertNotIn("#F0C814", [color["hex"] for color in palette])
