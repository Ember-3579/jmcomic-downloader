"""Generate the application icon without external assets."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 512


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def vertical_gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    strip = Image.new("RGB", (1, SIZE))
    pixels = strip.load()
    for y in range(SIZE):
        t = y / (SIZE - 1)
        pixels[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    return strip.resize((SIZE, SIZE))


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    gradient = vertical_gradient((45, 27, 57), (17, 12, 23)).convert("RGBA")
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=112, fill=255)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)

    # Soft pink glow in the upper left.
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-120, -170, 330, 280), fill=(239, 79, 125, 65))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    canvas = Image.alpha_composite(canvas, glow)
    canvas.putalpha(mask)

    draw = ImageDraw.Draw(canvas)

    # Thin luminous border.
    draw.rounded_rectangle(
        (7, 7, SIZE - 8, SIZE - 8),
        radius=108,
        outline=(255, 126, 158, 150),
        width=4,
    )

    # JM monogram with a subtle shadow.
    big = font(218)
    draw.text((258, 178), "JM", font=big, anchor="mm", fill=(0, 0, 0, 90))
    draw.text((252, 172), "JM", font=big, anchor="mm", fill=(255, 255, 255, 255))
    draw.text((252, 172), "JM", font=big, anchor="mm", fill=(255, 242, 247, 255))

    # Download badge.
    cx, cy, radius = 372, 374, 88
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=(239, 79, 125, 255),
        outline=(255, 163, 187, 255),
        width=3,
    )
    shaft_top, shaft_bottom = cy - 39, cy + 2
    draw.line((cx, shaft_top, cx, shaft_bottom), fill="white", width=17)
    draw.polygon(
        [
            (cx - 34, shaft_bottom - 6),
            (cx + 34, shaft_bottom - 6),
            (cx, shaft_bottom + 35),
        ],
        fill="white",
    )
    draw.rounded_rectangle(
        (cx - 36, cy + 35, cx + 36, cy + 49),
        radius=7,
        fill="white",
    )

    # Small sparkles.
    for x, y, r, alpha in ((114, 390, 7, 190), (407, 143, 5, 160), (92, 128, 4, 120)):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 210, 225, alpha))

    png_path = ASSETS / "icon.png"
    ico_path = ASSETS / "icon.ico"
    canvas.save(png_path)
    canvas.save(
        ico_path,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(png_path)
    print(ico_path)


if __name__ == "__main__":
    main()
