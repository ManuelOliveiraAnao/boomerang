"""Generate the Boomerang pixel logo assets from one pixel map (owner: D).

Run: python ui/assets/make_logo.py
Outputs SVGs made of square <rect> pixels (crisp at any size) and PNG favicons.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN, HIGHLIGHT, SHADE = "#7170FF", "#A5A4FF", "#4E4BD8"

# 16 × 12 grid. A boomerang: long left arm from the top-left, short right arm,
# meeting at a rounded elbow. '#' = filled pixel.
PIXELS = [
    "###.............",
    "####............",
    "#####...........",
    ".#####.......###",
    "..#####.....####",
    "...#####...#####",
    "....#####.#####.",
    ".....#########..",
    "......#######...",
    ".......#####....",
    "........###.....",
    "................",
]
W, H = len(PIXELS[0]), len(PIXELS)


def filled(x: int, y: int) -> bool:
    return 0 <= y < H and 0 <= x < W and PIXELS[y][x] == "#"


def colour(x: int, y: int) -> str:
    """Highlight on top edges, shade on bottom edges, main colour inside."""
    if not filled(x, y - 1):
        return HIGHLIGHT
    if not filled(x, y + 1):
        return SHADE
    return MAIN


def svg(mono: str | None = None, pad: int = 0, bg: str | None = None, radius: float = 0) -> str:
    size = max(W, H) + 2 * pad
    ox, oy = (size - W) / 2, (size - H) / 2 + 0.5
    rects = []
    for y in range(H):
        for x in range(W):
            if filled(x, y):
                rects.append(f'<rect x="{x + ox:g}" y="{y + oy:g}" width="1" height="1" '
                             f'fill="{mono or colour(x, y)}"/>')
    back = (f'<rect width="{size}" height="{size}" rx="{radius:g}" fill="{bg}"/>' if bg else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges">{back}{"".join(rects)}</svg>')


def png(path: Path, px: int, pad: int = 1) -> None:
    from PIL import Image
    size = max(W, H) + 2 * pad
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ox, oy = (size - W) // 2, (size - H) // 2
    for y in range(H):
        for x in range(W):
            if filled(x, y):
                c = colour(x, y).lstrip("#")
                img.putpixel((x + ox, y + oy), tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,))
    img.resize((px, px), Image.NEAREST).save(path)


if __name__ == "__main__":
    (HERE / "logo.svg").write_text(svg(), encoding="utf-8")
    (HERE / "logo-white.svg").write_text(svg(mono="#FFFFFF"), encoding="utf-8")
    (HERE / "logo-black.svg").write_text(svg(mono="#000000"), encoding="utf-8")
    (HERE / "app-icon.svg").write_text(svg(pad=3, bg="#141516", radius=6), encoding="utf-8")
    png(HERE / "favicon.png", 64)
    png(HERE / "logo-512.png", 512)
    print("\n".join(PIXELS))
