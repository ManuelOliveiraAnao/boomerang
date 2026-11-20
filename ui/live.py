"""Live activity feed and offline replay (owner: D, task D7).

Replay re-emits a recorded run's events.jsonl with compressed timing (default ×8) through
the same renderers as a live run, so a recorded golden run can be shown with no network.
"""
from __future__ import annotations

import html
import time
from typing import Any

from ui.theme import ACCENT, FAINT, LIGHT_COLORS, MUTED

LEVEL_COLOR = {"info": MUTED, "success": LIGHT_COLORS["green"], "warning": LIGHT_COLORS["amber"],
               "error": LIGHT_COLORS["red"]}


# ---------------------------------------------------------------- replay clock
def start_replay(events: list[dict[str, Any]], speed: float = 8.0) -> dict[str, Any]:
    first = events[0]["ts"] if events else 0.0
    last = events[-1]["ts"] if events else 0.0
    return {"t0": time.time(), "speed": speed, "first": first, "last": last}


def replay_visible(events: list[dict[str, Any]], clock: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not clock:
        return events
    horizon = clock["first"] + (time.time() - clock["t0"]) * clock["speed"]
    return [e for e in events if e["ts"] <= horizon]


def replay_done(clock: dict[str, Any] | None) -> bool:
    if not clock:
        return True
    return clock["first"] + (time.time() - clock["t0"]) * clock["speed"] >= clock["last"]


def replay_progress(clock: dict[str, Any] | None) -> float:
    if not clock or clock["last"] <= clock["first"]:
        return 1.0
    done = (time.time() - clock["t0"]) * clock["speed"]
    return max(0.0, min(1.0, done / (clock["last"] - clock["first"])))


# ---------------------------------------------------------------- feed
def feed_html(events: list[dict[str, Any]], limit: int = 9, running: bool = False) -> str:
    """Vercel-style build log: newest last, elapsed time, level dot, stage, message."""
    if not events:
        return ('<div class="bm-feed"><div class="row"><span class="msg" style="color:%s">'
                'No activity yet.</span></div></div>' % FAINT)
    t0 = events[0]["ts"]
    rows = []
    for e in events[-limit:]:
        c = LEVEL_COLOR.get(e.get("level", "info"), MUTED)
        dt = e["ts"] - t0
        rows.append(
            f'<div class="row"><span class="t">{int(dt // 60):02d}:{int(dt % 60):02d}</span>'
            f'<span class="dot" style="background:{c};box-shadow:0 0 6px {c}"></span>'
            f'<span class="stage">{html.escape(e.get("stage", ""))}</span>'
            f'<span class="msg">{html.escape(e.get("message", ""))}</span></div>'
        )
    if running:
        rows.append(f'<div class="row"><span class="t"></span><span class="dot bm-pulse" '
                    f'style="background:{ACCENT}"></span><span class="msg" style="color:{MUTED}">'
                    f'running…</span></div>')
    return f'<div class="bm-feed">{"".join(rows)}</div>'


FEED_CSS = f"""
<style>
  .bm-feed {{ font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 0.78rem;
    background: #0B0C0D; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 10px 14px;
    margin: -8px 0 18px; }}
  .bm-feed .row {{ display: flex; align-items: center; gap: 10px; padding: 3px 0; }}
  .bm-feed .t {{ color: {FAINT}; width: 42px; flex: 0 0 42px; }}
  .bm-feed .dot {{ width: 6px; height: 6px; border-radius: 50%; flex: 0 0 6px; }}
  .bm-feed .stage {{ color: #A5A4FF; width: 72px; flex: 0 0 72px; }}
  .bm-feed .msg {{ color: #D0D6E0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .bm-pulse {{ animation: bm-pulse 1s ease-in-out infinite; }}
  @keyframes bm-pulse {{ 0%,100% {{ opacity: .3; }} 50% {{ opacity: 1; }} }}
</style>
"""
