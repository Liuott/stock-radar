"""Generate simple app icons for the mobile home-screen manifest."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "web" / "icons"
BG = (11, 17, 27, 255)
GOLD = (244, 201, 93, 255)
CYAN = (92, 200, 255, 255)
RED = (255, 93, 109, 255)


def make(size: int) -> Image.Image:
    scale = 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = canvas * 0.08
    draw.rounded_rectangle(
        [pad, pad, canvas - pad, canvas - pad],
        radius=canvas * 0.22,
        fill=BG,
        outline=(40, 55, 75, 255),
        width=max(2, canvas // 180),
    )
    center = canvas / 2
    for radius, color, width in (
        (canvas * 0.30, GOLD, max(2, canvas // 90)),
        (canvas * 0.17, CYAN, max(2, canvas // 110)),
    ):
        draw.ellipse(
            [center - radius, center - radius, center + radius, center + radius],
            outline=color,
            width=width,
        )
    draw.ellipse(
        [center - canvas * 0.025, center - canvas * 0.025, center + canvas * 0.025, center + canvas * 0.025],
        fill=GOLD,
    )
    draw.line(
        [center, center, canvas * 0.77, canvas * 0.27],
        fill=RED,
        width=max(3, canvas // 80),
    )
    draw.ellipse(
        [canvas * 0.77 - canvas * 0.012, canvas * 0.27 - canvas * 0.012, canvas * 0.77 + canvas * 0.012, canvas * 0.27 + canvas * 0.012],
        fill=RED,
    )
    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")):
        make(size).save(ROOT / name)
        print("saved", ROOT / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
