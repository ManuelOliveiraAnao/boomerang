"""Generate the Boomerang logo assets from one pixel map (owner: D).

Run: python ui/assets/make_logo.py [path/to/Inter-SemiBold.ttf]
Outputs SVGs made of square <rect> pixels (crisp at any size), PNG favicons and, when an
Inter SemiBold TTF is given (needs fontTools), the wordmark with the letters converted to
vector outlines, so the SVG needs no font installed. Inter is under the SIL Open Font License.
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


def wordmark(font_path: str, text_fill: str, height: float = 40, tracking: float = -0.015) -> str:
    """Pixel mark + "Boomerang" in Inter SemiBold, letters as outlined paths."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    font = TTFont(font_path)
    upm = font["head"].unitsPerEm
    cap = font["OS/2"].sCapHeight or 0.727 * upm
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]

    px = height / W * 1.0                       # one mark pixel
    mark_h = H * px
    size = 0.62 * height / (cap / upm)          # caps ≈ 62% of the logo height
    k = size / upm
    gap = 0.34 * height
    x = W * px + gap
    baseline = height / 2 + cap * k / 2
    paths = []
    for ch in "Boomerang":
        g = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[g].draw(TransformPen(pen, (k, 0, 0, -k, x, baseline)))
        paths.append(pen.getCommands())
        x += hmtx[g][0] * k + tracking * size
    width = x - tracking * size
    oy = (height - mark_h) / 2
    rects = "".join(
        f'<rect x="{xx * px:g}" y="{yy * px + oy:g}" width="{px:g}" height="{px:g}" fill="{colour(xx, yy)}"/>'
        for yy in range(H) for xx in range(W) if filled(xx, yy))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.2f} {height:g}" '
            f'width="{width:.2f}" height="{height:g}">'
            f'<g shape-rendering="crispEdges">{rects}</g>'
            f'<path fill="{text_fill}" d="{" ".join(paths)}"/></svg>')


if __name__ == "__main__":
    import sys
    (HERE / "logo.svg").write_text(svg(), encoding="utf-8")
    (HERE / "logo-white.svg").write_text(svg(mono="#FFFFFF"), encoding="utf-8")
    (HERE / "logo-black.svg").write_text(svg(mono="#000000"), encoding="utf-8")
    (HERE / "app-icon.svg").write_text(svg(pad=3, bg="#141516", radius=6), encoding="utf-8")
    png(HERE / "favicon.png", 64)
    png(HERE / "logo-512.png", 512)
    if len(sys.argv) > 1:
        (HERE / "wordmark.svg").write_text(wordmark(sys.argv[1], "#F7F8F8"), encoding="utf-8")
        (HERE / "wordmark-dark.svg").write_text(wordmark(sys.argv[1], "#0B0C0E"), encoding="utf-8")
    print("\n".join(PIXELS))
