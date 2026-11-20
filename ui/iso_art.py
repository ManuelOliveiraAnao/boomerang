"""Isometric line illustrations for the landing page's problem section (owner: D).

Pure Python, no dependencies: shapes are described in 3D and projected to an
isometric 2D SVG. Faces are filled with the page background so nearer shapes
hide the lines behind them (draw back to front).
"""
from __future__ import annotations

import math

_C30, _S30 = math.cos(math.radians(30)), 0.5
STROKE = "rgba(255,255,255,0.62)"
FILL = "#08090A"          # page background, so faces occlude


class _Pen:
    def __init__(self, ox: float, oy: float):
        self.ox, self.oy, self.out = ox, oy, []
        self.lo, self.hi = [1e9, 1e9], [-1e9, -1e9]

    def p(self, x: float, y: float, z: float) -> tuple[float, float]:
        a, b = self.ox + (x - y) * _C30, self.oy + (x + y) * _S30 - z
        self.lo = [min(self.lo[0], a), min(self.lo[1], b)]
        self.hi = [max(self.hi[0], a), max(self.hi[1], b)]
        return a, b

    def _pts(self, pts) -> str:
        return " ".join(f"{a:.1f},{b:.1f}" for a, b in (self.p(*q) for q in pts))

    def poly(self, pts, fill: str = FILL, width: float = 1.2) -> None:
        self.out.append(f'<polygon points="{self._pts(pts)}" fill="{fill}" stroke="{STROKE}" '
                        f'stroke-width="{width}" stroke-linejoin="round"/>')

    def line(self, pts, width: float = 1.1, opacity: float = 1.0) -> None:
        self.out.append(f'<polyline points="{self._pts(pts)}" fill="none" stroke="{STROKE}" '
                        f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" '
                        f'opacity="{opacity}"/>')

    def box(self, x, y, z, w, d, h) -> None:
        """Axis-aligned box; draws the three faces the viewer can see."""
        self.poly([(x, y, z + h), (x + w, y, z + h), (x + w, y + d, z + h), (x, y + d, z + h)])   # top
        self.poly([(x + w, y, z), (x + w, y + d, z), (x + w, y + d, z + h), (x + w, y, z + h)])   # right
        self.poly([(x, y + d, z), (x + w, y + d, z), (x + w, y + d, z + h), (x, y + d, z + h)])   # left

    def text_on_right(self, x, y, z, s: str, size: float = 15) -> None:
        """Text painted on a face with constant x (the right-hand face)."""
        a, b = self.p(x, y, z)
        self.out.append(f'<text transform="matrix({_C30:.3f},{-_S30},0,1,{a:.1f},{b:.1f})" '
                        f'font-family="JetBrains Mono, monospace" font-size="{size}" fill="none" '
                        f'stroke="{STROKE}" stroke-width="0.9" text-anchor="middle">{s}</text>')

    def svg(self, label: str, pad: float = 4) -> str:
        x0, y0 = self.lo[0] - pad, self.lo[1] - pad
        w, h = self.hi[0] - self.lo[0] + 2 * pad, self.hi[1] - self.lo[1] + 2 * pad
        return (f'<svg class="iso-art" viewBox="{x0:.0f} {y0:.0f} {w:.0f} {h:.0f}" role="img" '
                f'aria-label="{label}" xmlns="http://www.w3.org/2000/svg">{"".join(self.out)}</svg>')


def lock_in() -> str:
    """A code panel with a padlock in front of it."""
    g = _Pen(118, 70)
    # upright panel (thin in x), code lines on its right face
    g.box(0, -70, 0, 8, 120, 92)
    for i, (y0, y1) in enumerate([(-58, 20), (-58, 6), (-46, 36), (-58, 14), (-46, 28)]):
        z = 76 - i * 13
        g.line([(8, y0, z), (8, y1, z)], width=1.0, opacity=0.7)
    # padlock body in front, shackle drawn in the same plane as its right face
    bx, by, bz, bw, bd, bh = 26, 6, 0, 18, 46, 50
    xs, cy = bx + bw / 2, by + bd / 2
    for r in (17, 10):
        g.line([(xs, cy + r * math.cos(t), bh + r * math.sin(t) + 8)
                for t in [math.pi * k / 24 for k in range(25)]] + [], width=1.2)
    for r in (17, 10):
        g.line([(xs, cy + r, bh), (xs, cy + r, bh + 8)])
        g.line([(xs, cy - r, bh), (xs, cy - r, bh + 8)])
    g.box(bx, by, bz, bw, bd, bh)
    # keyhole on the right face
    kx = bx + bw
    g.line([(kx, cy + 3.5, 30), (kx, cy - 3.5, 30), (kx, cy - 3.5, 35), (kx, cy + 3.5, 35), (kx, cy + 3.5, 30)], width=1.0)
    g.line([(kx, cy, 30), (kx, cy, 17)], width=1.4)
    return g.svg("A code panel locked with a padlock")


def unknown() -> str:
    """A cluster of boxes, one marked with a question mark."""
    g = _Pen(128, 62)
    g.box(-30, -40, 0, 46, 46, 50)          # back box
    g.box(-30, -40, 50, 34, 34, 16)          # small box on top of it
    g.box(-80, 10, 0, 70, 34, 16)            # low slab, left
    g.box(-66, 14, 16, 40, 30, 40)           # box with "?" on the slab
    g.text_on_right(-26, 30, 34, "?", 20)
    g.box(30, 6, 0, 52, 52, 52)              # big box, right
    for x, y, z, s in [(4, -36, 59, "123"), (16, -10, 35, "#")]:
        g.text_on_right(x, y, z, s, 8)
    return g.svg("Boxes, one of them marked with a question mark")


def no_way_back() -> str:
    """An open door on a floor, with an arrow leading away from it."""
    g = _Pen(122, 56)
    g.poly([(-10, -20, 0), (100, -20, 0), (100, 70, 0), (-10, 70, 0)])          # floor
    g.box(-10, -20, 0, 6, 82, 112)                                               # door slab, standing
    g.poly([(-4, -12, 6), (-4, 54, 6), (-4, 54, 104), (-4, -12, 104)], fill="none", width=0.9)
    # arrow on the floor, pointing out of the door
    g.line([(20, 30, 0), (72, 30, 0)], width=1.3)
    g.line([(60, 18, 0), (72, 30, 0), (60, 42, 0)], width=1.3)
    return g.svg("An open door with an arrow pointing away from it")


def fuzzy_costs() -> str:
    """A stack of coins and one coin standing up with a euro sign."""
    out = []
    cx, top, rx, ry, layers, step = 104, 52, 50, 22, 7, 17
    bottom = top + layers * step
    out.append(f'<path d="M{cx - rx},{top} L{cx - rx},{bottom} A{rx},{ry} 0 0 0 {cx + rx},{bottom} '
               f'L{cx + rx},{top} Z" fill="{FILL}" stroke="{STROKE}" stroke-width="1.2"/>')
    for i in range(1, layers):
        y = top + i * step
        out.append(f'<path d="M{cx - rx},{y} A{rx},{ry} 0 0 0 {cx + rx},{y}" fill="none" '
                   f'stroke="{STROKE}" stroke-width="1" opacity="0.75"/>')
    out.append(f'<ellipse cx="{cx}" cy="{top}" rx="{rx}" ry="{ry}" fill="{FILL}" stroke="{STROKE}" stroke-width="1.2"/>')
    # standing coin: two offset ellipses give it thickness
    kx, ky, krx, kry = 182, 128, 30, 40
    out.append(f'<ellipse cx="{kx - 7}" cy="{ky}" rx="{krx}" ry="{kry}" fill="{FILL}" stroke="{STROKE}" stroke-width="1.2"/>')
    out.append(f'<rect x="{kx - 7}" y="{ky - kry}" width="7" height="{2 * kry}" fill="{FILL}"/>')
    out.append(f'<line x1="{kx - 7}" y1="{ky - kry}" x2="{kx}" y2="{ky - kry}" stroke="{STROKE}" stroke-width="1.2"/>')
    out.append(f'<line x1="{kx - 7}" y1="{ky + kry}" x2="{kx}" y2="{ky + kry}" stroke="{STROKE}" stroke-width="1.2"/>')
    out.append(f'<ellipse cx="{kx}" cy="{ky}" rx="{krx}" ry="{kry}" fill="{FILL}" stroke="{STROKE}" stroke-width="1.2"/>')
    out.append(f'<ellipse cx="{kx}" cy="{ky}" rx="{krx - 6}" ry="{kry - 7}" fill="none" stroke="{STROKE}" stroke-width="0.9" opacity="0.7"/>')
    out.append(f'<text x="{kx}" y="{ky + 11}" font-family="Inter, sans-serif" font-size="32" fill="none" '
               f'stroke="{STROKE}" stroke-width="1" text-anchor="middle">&#8364;</text>')
    x0, y0 = cx - rx - 4, top - ry - 4
    return (f'<svg class="iso-art" viewBox="{x0} {y0} {kx + krx + 4 - x0} {bottom + ry + 4 - y0}" role="img" '
            f'aria-label="A stack of coins and a euro coin" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(out)}</svg>')
