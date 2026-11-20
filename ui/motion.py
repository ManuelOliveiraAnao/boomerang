"""Scroll and entrance motion for the landing page (owner: D).

Vanilla JS + CSS, no libraries. The effects (and their timings) follow the
reference site the team picked, which used GSAP + Framer Motion:

* headings rise word by word out of a clipped line (0.86s, 45ms stagger);
* cards, steps, stats and list items arrive with a lift, fade and slight
  scale when their group scrolls into view (0.72s, 80ms stagger);
* the hero staggers in on load, then drifts up, fades and shrinks a little
  as you scroll away from it (smoothed, like a spring);
* primary buttons lean toward the cursor ("magnetic").

Everything is gated on ``html.bm-motion``, which the script sets only when the
user has not asked for reduced motion, so without JS the page is fully visible.
"""
from __future__ import annotations

import streamlit as st

# Groups whose children enter one after another: (selector, stagger in ms).
_GROUPS = [
    (".lp-stats", 80),
    (".lp-grid", 80),
    (".lp-problem", 110),
    (".lp-steps", 60),
    (".lp-trip", 90),
    (".lp-list", 50),
    (".lp-chips", 25),
]
# Single blocks that enter on their own.
_SINGLES = [".lp-kicker", ".lp-lead", ".lp-cta", ".lp-hero .lp-mark", ".lp-hero .bm-eyebrow", ".lp-hero .bm-sub"]
# Headings split into words.
_SPLITS = [".lp-hero .bm-title", ".lp-h2"]

_EASE_OUT = "cubic-bezier(.215,.61,.355,1)"  # GSAP power3.out
_EASE_HERO = "cubic-bezier(.22,1,.36,1)"

_hidden = ", ".join(
    [f"html.bm-motion {g} > *:not([data-bm])" for g, _ in _GROUPS]
    + [f"html.bm-motion {s}:not([data-bm])" for s in _SINGLES + _SPLITS]
)

_CSS = f"""
<style>
  /* Hide targets the script has not reached yet, so nothing flashes before it animates in. */
  {_hidden} {{ opacity: 0; }}

  html.bm-motion .bm-rv {{
    opacity: 0; transform: translate3d(0, 22px, 0) scale(.985);
    transition: opacity .72s {_EASE_OUT}, transform .72s {_EASE_OUT};
    transition-delay: var(--d, 0ms);
  }}
  html.bm-motion .bm-rv.bm-hero {{ transform: translate3d(0, 18px, 0); transition-duration: .7s; transition-timing-function: {_EASE_HERO}; }}
  html.bm-motion .bm-rv.in {{ opacity: 1; transform: none; }}
  html.bm-motion .bm-pre, html.bm-motion .bm-pre * {{ transition: none !important; }}

  /* Word reveal: each word rises out of its own clipped box. */
  .bm-w {{ display: inline-block; overflow: clip; vertical-align: top; padding-bottom: .1em; margin-bottom: -.1em; }}
  .bm-w > span {{ display: inline-block; }}
  html.bm-motion .bm-split .bm-w > span {{
    opacity: 0; transform: translate3d(0, 105%, 0);
    transition: transform .86s {_EASE_OUT}, opacity .86s {_EASE_OUT};
    transition-delay: var(--d, 0ms);
  }}
  html.bm-motion .bm-split.in .bm-w > span {{ opacity: 1; transform: none; }}
  /* The hero title is gradient text; each word carries the gradient itself. */
  .bm-title .bm-w > span {{
    background: linear-gradient(180deg, #fff 30%, #9BA0A8);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}

  html.bm-motion .lp-hero {{ will-change: transform, opacity; transform-origin: 50% 0; }}
  [data-testid="stBaseButton-primary"] {{ will-change: transform; }}
</style>
"""

_JS = """
<script>
(() => {
  if (window.__bmMotion) return;
  window.__bmMotion = true;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const root = document.documentElement;
  root.classList.add("bm-motion");

  const GROUPS = %GROUPS%, SINGLES = %SINGLES%, SPLITS = %SPLITS%;
  const HERO_DELAY = 120, HERO_STEP = 95;

  // Reveal when the element's top passes ~80% of the viewport, once.
  const waiting = new Set();
  const reveal = (el) => { el.classList.add("in"); io.unobserve(el); waiting.delete(el); };
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) reveal(e.target);
  }, { rootMargin: "0px 0px -18% 0px", threshold: 0 });
  // A fast jump (anchor link, End key) can carry an element from below the
  // viewport to above it without it ever intersecting: reveal what we passed.
  setInterval(() => {
    const line = innerHeight * 0.82;
    for (const el of waiting) {
      if (!el.isConnected) { waiting.delete(el); continue; }
      if (el.getBoundingClientRect().top < line) reveal(el);
    }
  }, 150);

  function arm(el, delay, extra) {
    el.dataset.bm = "1";
    el.classList.add("bm-pre", extra);
    if (extra !== "bm-split") el.classList.add("bm-rv");
    el.style.setProperty("--d", delay + "ms");
    void el.offsetWidth;                      // commit the hidden state without a transition
    requestAnimationFrame(() => { el.classList.remove("bm-pre"); waiting.add(el); io.observe(el); });
  }

  function splitWords(el, base, step) {
    let i = 0;
    const walk = (node) => {
      for (const ch of [...node.childNodes]) {
        if (ch.nodeType === 3) {
          const frag = document.createDocumentFragment();
          for (const part of ch.textContent.split(/(\\s+)/)) {
            if (!part) continue;
            if (/^\\s+$/.test(part)) { frag.appendChild(document.createTextNode(" ")); continue; }
            const box = document.createElement("span"), word = document.createElement("span");
            box.className = "bm-w"; word.textContent = part;
            word.style.setProperty("--d", (base + step * i++) + "ms");
            box.appendChild(word); frag.appendChild(box);
          }
          ch.replaceWith(frag);
        } else if (ch.nodeType === 1 && ch.tagName !== "BR") walk(ch);
      }
    };
    walk(el);
  }

  function scan() {
    // Hero, in load order: mark, eyebrow, title words, subtitle.
    let h = 0;
    for (const sel of [".lp-hero .lp-mark", ".lp-hero .bm-eyebrow"]) {
      document.querySelectorAll(sel).forEach((el) => { if (!el.dataset.bm) arm(el, HERO_DELAY + HERO_STEP * h++, "bm-hero"); });
    }
    document.querySelectorAll(SPLITS.join(",")).forEach((el) => {
      if (el.dataset.bm) return;
      const hero = !!el.closest(".lp-hero");
      splitWords(el, hero ? HERO_DELAY + HERO_STEP * 2 : 60, hero ? 60 : 45);
      arm(el, 0, "bm-split");
    });
    document.querySelectorAll(".lp-hero .bm-sub").forEach((el) => { if (!el.dataset.bm) arm(el, HERO_DELAY + HERO_STEP * 5, "bm-hero"); });

    for (const sel of SINGLES) {
      if (sel.startsWith(".lp-hero")) continue;
      document.querySelectorAll(sel).forEach((el) => {
        if (el.dataset.bm) return;
        arm(el, el.classList.contains("lp-lead") ? 180 : 0, "bm-block");
      });
    }
    for (const [sel, step] of GROUPS) {
      document.querySelectorAll(sel).forEach((g) => {
        [...g.children].forEach((el, i) => { if (!el.dataset.bm) arm(el, i * step, "bm-item"); });
      });
    }
  }

  // Streamlit renders (and re-renders) asynchronously: rescan whenever the DOM changes.
  let pending = 0;
  new MutationObserver(() => { pending ||= requestAnimationFrame(() => { pending = 0; scan(); }); })
    .observe(document.body, { childList: true, subtree: true });
  scan();

  // ---- scroll-linked hero + magnetic buttons, one rAF loop ----
  let scroller = null;
  function findScroller(from) {
    for (let el = from; el && el !== document.body; el = el.parentElement) {
      const oy = getComputedStyle(el).overflowY;
      if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight) return el;
    }
    return document.scrollingElement;
  }
  let p = 0, mx = null, my = null;
  const mag = new WeakMap();
  document.addEventListener("mousemove", (e) => { mx = e.clientX; my = e.clientY; }, { passive: true });
  document.addEventListener("mouseleave", () => { mx = null; });
  const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  function frame() {
    const heroes = document.querySelectorAll(".lp-hero");
    if (heroes.length) {
      if (!scroller || !scroller.isConnected || !scroller.contains(heroes[0])) scroller = findScroller(heroes[0]);
      const vh = scroller === document.scrollingElement ? innerHeight : scroller.clientHeight;
      const target = Math.min(1, scroller.scrollTop / Math.max(1, vh * 0.92));
      p += (target - p) * 0.14;                               // spring-ish smoothing
      if (Math.abs(target - p) < 0.0005) p = target;
      const y = -88 * p, o = 1 - 0.82 * Math.min(1, p / 0.84), s = 1 - 0.025 * p;
      heroes.forEach((el) => {
        el.style.transform = p ? `translate3d(0, ${y.toFixed(1)}px, 0) scale(${s.toFixed(4)})` : "";
        el.style.opacity = p ? o.toFixed(3) : "";
      });
    }
    if (fine) {
      document.querySelectorAll('[data-testid="stBaseButton-primary"]').forEach((b) => {
        const st = mag.get(b) || { x: 0, y: 0 };
        const r = b.getBoundingClientRect();
        let tx = 0, ty = 0;
        if (mx !== null && mx > r.left - 24 && mx < r.right + 24 && my > r.top - 24 && my < r.bottom + 24) {
          tx = (mx - (r.left + r.width / 2)) * 0.12;
          ty = (my - (r.top + r.height / 2)) * 0.16;
        }
        st.x += (tx - st.x) * 0.16; st.y += (ty - st.y) * 0.16;
        if (Math.abs(st.x) < 0.05 && Math.abs(st.y) < 0.05 && !tx && !ty) { st.x = st.y = 0; }
        b.style.transform = st.x || st.y ? `translate3d(${st.x.toFixed(2)}px, ${st.y.toFixed(2)}px, 0)` : "";
        mag.set(b, st);
      });
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
</script>
"""


def inject() -> None:
    """Add the motion layer to the current page (safe to call on every rerun)."""
    import json

    js = (_JS.replace("%GROUPS%", json.dumps(_GROUPS))
             .replace("%SINGLES%", json.dumps(_SINGLES))
             .replace("%SPLITS%", json.dumps(_SPLITS)))
    st.markdown(_CSS, unsafe_allow_html=True)
    st.html(js, unsafe_allow_javascript=True)
