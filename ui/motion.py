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
    (".lp-trip", 90),
    (".lp-list", 50),
    (".lp-chips", 25),
]
# Single blocks that enter on their own.
_SINGLES = [".lp-kicker", ".lp-lead", ".lp-cta", ".lt-board", ".lp-hero .lp-mark", ".lp-hero .bm-eyebrow", ".lp-hero .bm-sub"]
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

  /* Enter / Shift+Enter checkpoint navigation: a small hint, bottom right, landing page only */
  .bm-keys {{ position: fixed; right: 20px; bottom: 18px; z-index: 999; display: flex; align-items: center; gap: 10px;
    padding: 7px 12px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.10); background: rgba(15,16,17,0.86);
    backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); color: #8A8F98;
    font: 500 0.72rem/1 "JetBrains Mono", ui-monospace, monospace; transition: opacity .4s ease, transform .4s ease; }}
  .bm-keys kbd {{ display: inline-block; padding: 3px 6px; border-radius: 5px; border: 1px solid rgba(255,255,255,0.16);
    border-bottom-width: 2px; background: #141516; color: #D0D6E0; font: inherit; }}
  .bm-keys b {{ color: #F7F8F8; font-weight: 500; min-width: 3.2em; text-align: right; }}
  .bm-keys.quiet {{ opacity: .45; }}
  .bm-keys.quiet:hover {{ opacity: 1; }}
  @media (hover: none) {{ .bm-keys {{ display: none; }} }}
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
    document.querySelectorAll(".lp-tl").forEach(timeline);
    requestAnimationFrame(frame);
  }

  // ---- "How it works" timeline: the boomerang travels down the rail and
  // lights every step it passes; the pinned side panel follows along. ----
  const tlState = new WeakMap();
  const PHASE_OF = [0, 0, 0, 1, 1, 2, 2, 2, 2];
  const NODE = 12.5;                       // node centre, px below a step's top
  function flip(el, text) {
    if (!el || el.textContent === text) return;
    el.textContent = text;
    el.classList.remove("flip"); void el.offsetWidth; el.classList.add("flip");
  }
  function timeline(tl) {
    const list = tl.querySelector(".tl-list");
    const steps = list ? [...list.querySelectorAll(".tl-step")] : [];
    if (!steps.length) return;
    const st = tlState.get(tl) || { p: 0, cur: -2, sc: null };
    if (!st.sc || !st.sc.isConnected) st.sc = findScroller(tl);
    const vr = st.sc === document.scrollingElement
      ? { top: 0, height: innerHeight } : st.sc.getBoundingClientRect();
    const lr = list.getBoundingClientRect();
    const ys = steps.map((s) => s.getBoundingClientRect().top - lr.top + NODE);
    const y0 = ys[0], span = Math.max(1, ys[ys.length - 1] - y0);

    const rail = list.querySelector(".tl-rail");
    rail.style.top = y0 + "px"; rail.style.height = span + "px"; rail.style.bottom = "auto";

    // The reading line sits a little above the middle of the viewport.
    const line = vr.top + vr.height * 0.52;
    const target = Math.max(0, Math.min(span, line - lr.top - y0));
    st.p += (target - st.p) * 0.16;
    if (Math.abs(target - st.p) < 0.1) st.p = target;

    list.querySelector(".tl-fill").style.height = st.p.toFixed(1) + "px";
    list.querySelector(".tl-mark").style.transform = `translate3d(0, ${(y0 + st.p - 22).toFixed(1)}px, 0)`;
    // A boomerang spins as it flies: about one turn per 320px of travel.
    list.querySelector(".tl-boom").style.transform = `rotate(${(st.p * 1.125).toFixed(1)}deg)`;

    let cur = -1;
    const seenLine = vr.top + vr.height * 0.9;
    steps.forEach((s, i) => {
      const on = st.p >= ys[i] - y0 - 6;
      s.classList.toggle("on", on);
      if (on) cur = i;
      if (lr.top + ys[i] < seenLine) s.classList.add("seen");
    });
    steps.forEach((s, i) => s.classList.toggle("is-cur", i === cur));
    const bar = tl.querySelector(".tl-bar i");
    if (bar) bar.style.width = (100 * st.p / span).toFixed(2) + "%";

    if (cur !== st.cur) {
      st.cur = cur;
      const i = Math.max(0, cur);
      flip(tl.querySelector(".tl-cur"), String(i + 1).padStart(2, "0"));
      flip(tl.querySelector(".tl-name"), steps[i].querySelector(".tl-title").textContent);
      tl.querySelectorAll(".tl-phase").forEach((ph, k) => ph.classList.toggle("on", k === PHASE_OF[i]));
    }
    tlState.set(tl, st);
  }
  requestAnimationFrame(frame);
})();
</script>
"""


_KEYS_JS = """
<script>
(() => {
  // Enter = next checkpoint, Shift+Enter = previous. Checkpoints: the top of the page, every section heading,
  // each step of the "How it works" timeline (aligned to the reading line, so the boomerang lights it) and the CTA.
  if (window.__bmKeys) return;
  window.__bmKeys = true;
  const CHECKPOINTS = ".lp-section, .tl-step, .lp-cta";
  const NAV = 84;                                   // keep headings clear of the top navigation
  const smooth = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

  function scroller(from) {
    for (let el = from; el && el !== document.body; el = el.parentElement) {
      const oy = getComputedStyle(el).overflowY;
      if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight) return el;
    }
    return document.scrollingElement;
  }
  function stops() {
    const els = [...document.querySelectorAll(CHECKPOINTS)].filter((el) => el.offsetParent !== null);
    if (!els.length) return null;
    const sc = scroller(els[0]);
    const top = sc === document.scrollingElement ? 0 : sc.getBoundingClientRect().top;
    const vh = sc === document.scrollingElement ? innerHeight : sc.clientHeight;
    const list = els.map((el) => {
      // Resting position: steps not yet revealed are drawn 28px lower (their slide-in), so remove the transform.
      let ty = 0;
      try { ty = new DOMMatrixReadOnly(getComputedStyle(el).transform).m42 || 0; } catch (_) { ty = 0; }
      const r = { top: el.getBoundingClientRect().top - ty };
      // a timeline step: put its node just above the reading line (52% of the view) so it lights up
      const want = el.classList.contains("tl-step") ? r.top + 12.5 - (top + vh * 0.52 - 10) : r.top - top - NAV;
      return { el, y: Math.max(0, Math.round(sc.scrollTop + want)) };
    }).sort((a, b) => a.y - b.y);
    // Start at the top; merge stops closer than a third of the view (they'd show the same screen).
    const uniq = [{ el: null, y: 0 }];
    for (const p of list) if (p.y - uniq[uniq.length - 1].y > vh / 3) uniq.push(p);
    return { sc, list: uniq };
  }
  function typing(t) {
    if (!t || t === document.body) return false;
    if (t.isContentEditable) return true;
    return !!t.closest("input, textarea, select, button, a, summary, [role=tab], [role=button], [role=option], [role=listbox], [role=combobox], [role=dialog]");
  }
  function go(dir) {
    const s = stops();
    if (!s) return false;
    const cur = s.sc.scrollTop;
    const next = dir > 0 ? s.list.find((p) => p.y > cur + 6)
                         : [...s.list].reverse().find((p) => p.y < cur - 6);
    if (!next) return false;
    s.sc.scrollTo({ top: next.y, behavior: smooth() });
    // A smooth scroll can be dropped (focus changes, a rerender): make sure we arrive.
    setTimeout(() => { if (Math.abs(s.sc.scrollTop - next.y) > 40) s.sc.scrollTo({ top: next.y }); }, 900);
    hint(s.list.indexOf(next), s.list.length - 1);
    return true;
  }

  // ---- hint, bottom right, landing page only ----
  let box = null, quietTimer = 0;
  function hint(n, total) {
    if (!box) return;
    box.querySelector("b").textContent = n ? `${n} / ${total}` : "";
    box.classList.remove("quiet");
    clearTimeout(quietTimer);
    quietTimer = setTimeout(() => box && box.classList.add("quiet"), 2200);
  }
  function syncHint() {
    const onLanding = !!document.querySelector(".lp-hero");
    if (onLanding && !box) {
      box = document.createElement("div");
      box.className = "bm-keys quiet";
      box.setAttribute("aria-hidden", "true");
      // Built with DOM calls: markup inside a script string makes Streamlit's sanitiser drop the whole script.
      const el = (tag, text) => { const n = document.createElement(tag); if (text) n.textContent = text; return n; };
      const part = (keys, word) => {
        const span = el("span");
        keys.forEach((k, i) => { if (i) span.append("+"); span.append(el("kbd", k)); });
        span.append(" " + word);
        return span;
      };
      box.append(part(["Enter"], "next"), part(["Shift", "Enter"], "back"), el("b"));
      document.body.appendChild(box);
    } else if (!onLanding && box) {
      box.remove(); box = null;
    }
  }
  setInterval(syncHint, 500);
  syncHint();

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    if (typing(e.target) || !document.querySelector(".lp-hero")) return;
    if (go(e.shiftKey ? -1 : 1)) e.preventDefault();
  }, true);
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
    st.html(_KEYS_JS, unsafe_allow_javascript=True)
