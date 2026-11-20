"""Landing page: what Boomerang is, how it works, why to trust it (owner: D).

Wording follows docs/01 and docs/09: no claims of zero downtime, full
compatibility, "migrates any app" or "always cheaper".
"""
from __future__ import annotations

import streamlit as st

from ui import iso_art, motion
from ui.theme import LIGHT_COLORS, logo_svg, wordmark_svg


def _html(s: str) -> None:
    """Render HTML through st.markdown without Markdown turning indentation into code."""
    st.markdown(" ".join(line.strip() for line in s.splitlines() if line.strip()),
                unsafe_allow_html=True)


# Hero logo: bobs gently and drifts toward the cursor; spins slowly while hovered,
# then eases back upright. One global loop survives Streamlit reruns.
_BOOMERANG_JS = """
<script>
(() => {
  if (window.__bmBoom) return;
  window.__bmBoom = true;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const s = { mx: null, my: null, x: 0, y: 0, angle: 0, speed: 0, hover: false, t0: performance.now(), last: performance.now() };
  const SPIN = 360;          // degrees per second at full spin (one turn per second)
  const PULL = 0.07, MAX = 40;
  const NEAR = 140, FAR = 320;  // px from the logo's centre: full spin inside NEAR, fading out to FAR
  document.addEventListener("mousemove", (e) => { s.mx = e.clientX; s.my = e.clientY; }, { passive: true });
  document.addEventListener("mouseleave", () => { s.mx = null; s.hover = false; });
  function frame(now) {
    const dt = Math.min(0.05, (now - s.last) / 1000); s.last = now;
    const mark = document.querySelector(".lp-mark.bm-follow");
    const boom = mark && mark.querySelector(".bm-boom");
    if (boom) {
      const r = mark.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      let tx = 0, ty = 0, near = 0;
      if (s.mx !== null) {
        tx = Math.max(-MAX, Math.min(MAX, (s.mx - cx) * PULL));
        ty = Math.max(-MAX, Math.min(MAX, (s.my - cy) * PULL));
        const d = Math.hypot(s.mx - cx, s.my - cy);
        near = Math.max(0, Math.min(1, (FAR - d) / (FAR - NEAR)));
      }
      s.hover = near > 0;
      s.x += (tx - s.x) * Math.min(1, dt * 4);
      s.y += (ty - s.y) * Math.min(1, dt * 4);
      const target = SPIN * near;
      s.speed += (target - s.speed) * Math.min(1, dt * 2.5);
      s.angle += s.speed * dt;
      if (!s.hover && s.speed < 8) {             // settle back upright
        const rest = Math.round(s.angle / 360) * 360;
        s.angle += (rest - s.angle) * Math.min(1, dt * 3);
      }
      const bob = Math.sin((now - s.t0) / 700) * 5;
      const tilt = s.speed < 8 ? Math.sin((now - s.t0) / 1400) * 3 : 0;
      boom.style.transform = `translate(${s.x.toFixed(1)}px, ${(s.y + bob).toFixed(1)}px) rotate(${(s.angle + tilt).toFixed(1)}deg)`;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
</script>
"""


def _go_dashboard(key: str, label: str = "Open the dashboard →", primary: bool = True) -> None:
    _, mid, _ = st.columns([2, 1, 2])
    with mid:
        if st.button(label, key=key, type="primary" if primary else "secondary", width="stretch"):
            st.switch_page(st.session_state["_pages"]["dashboard"])


def _section(kicker: str, title: str, lead: str) -> None:
    _html(f"""
      <div class="lp-section">
        <div class="lp-kicker">{kicker}</div>
        <div class="lp-h2">{title}</div>
        <div class="lp-lead">{lead}</div>
      </div>
    """)


# Traffic-light rules, from docs/02 §4 (B's boomerang/assess.py). Each row:
# (service, feature, why it gets this light, what happens on Leaseweb, what handles it)
_LIGHTS = [
    ("green", "Config only", "Same API on the other side. Only the endpoint and keys change.", [
        ("S3", "Basic object operations", "put, get, list, delete and head work the same on any S3-compatible store.",
         "S3-compatible object storage: SeaweedFS, or Leaseweb Object Storage", "Storage adapter, config only"),
    ]),
    ("amber", "Moves via adapter", "Moves through a small, tested adapter. Every behaviour difference is listed.", [
        ("DynamoDB", "Items, queries on key or GSI", "Postgres through the kv adapter, which mimics boto3's Table calls.",
         "Postgres. GSIs become indexes; numbers come back as Decimal, like boto3", "sovereign_sdk.kv"),
        ("DynamoDB", "Conditional writes", "Only attribute_not_exists and attribute_exists are supported.",
         "Same check in Postgres; a failure raises ConditionalCheckFailedException", "sovereign_sdk.kv"),
        ("SQS", "Standard queues, retries, DLQ", "Send, receive, delete, visibility timeout and maxReceiveCount.",
         "RabbitMQ quorum queue with a dead-letter exchange", "sovereign_sdk.queue"),
        ("S3", "Presigned URLs, versioning, lifecycle", "Supported differently, or needs configuring on the target.",
         "Listed in the plan with what to set up", "Review in the plan"),
    ]),
    ("red", "Needs a human", "No safe automatic equivalent. Flagged, explained and left for a person.", [
        ("IAM", "Roles, instance profiles, policies", "Permissions can't be translated one to one.",
         "Replaced by generated credentials scoped to one database, vhost and bucket", "Human review"),
        ("Lambda", "Functions and triggers", "Serverless functions have no drop-in equivalent here.",
         "Flagged, not migrated", "Human"),
        ("S3", "Event notifications", "Events that call Lambda or SQS aren't wired up on the target.",
         "Flagged, not migrated", "Human"),
        ("DynamoDB", "Transactions, streams, TTL, PartiQL", "Outside what the kv adapter supports.",
         "The adapter raises UnsupportedFeature instead of guessing", "Human"),
        ("SQS", "FIFO, attribute filters, delays", "Outside what the queue adapter supports.",
         "Flagged, not migrated", "Human"),
    ]),
]


def _lights_board() -> str:
    order = ["green", "amber", "red"]
    cols = []
    for key, label, blurb, rows in _LIGHTS:
        signal = "".join(f'<i class="{"lit" if k == key else ""}"></i>' for k in order)
        items = "".join(
            f'<div class="lt-row" tabindex="0">'
            f'<span class="lt-svc">{svc}</span><span class="lt-feat">{feat}</span>'
            f'<span class="lt-chev" aria-hidden="true">›</span>'
            f'<div class="lt-pop" role="tooltip">'
            f'<div class="lt-pop-h"><span class="lt-pop-l">{label}</span><span class="lt-pop-s">{svc}</span></div>'
            f'<div class="lt-pop-t">{feat}</div>'
            f'<div class="lt-kv"><span>Why</span><p>{why}</p></div>'
            f'<div class="lt-kv"><span>On Leaseweb</span><p>{target}</p></div>'
            f'<div class="lt-kv"><span>Handled by</span><p><code>{by}</code></p></div>'
            f'</div></div>'
            for svc, feat, why, target, by in rows
        )
        cols.append(
            f'<div class="lt-col" style="--c:{LIGHT_COLORS[key]}">'
            f'<div class="lt-head"><span class="lt-signal">{signal}</span>'
            f'<div><div class="lt-label">{label}</div>'
            f'<div class="lt-count">{len(rows)} rule{"s" if len(rows) != 1 else ""}</div></div></div>'
            f'<p class="lt-blurb">{blurb}</p><div class="lt-rows">{items}</div></div>'
        )
    return (
        '<div class="lt-board">' + "".join(cols) + "</div>"
        '<div class="lt-note"><span>Rules decide the light.</span> The AI only writes the explanation, '
        "and it may raise a light, never lower it. Hover or tap a rule for details.</div>"
    )


def render() -> None:
    motion.inject()

    # ---------------- hero ----------------
    # Our own static markup + script (no user input), so running its JS is safe.
    # The SVG goes through st.markdown (st.html's sanitiser strips SVG); only the script uses st.html.
    _html(f'<div class="lp-hero" style="padding-bottom:0"><span class="lp-mark bm-follow">'
          f'<span class="bm-boom">{logo_svg(80)}</span></span></div>')
    st.html(_BOOMERANG_JS, unsafe_allow_javascript=True)
    _html("""
      <div class="lp-hero" style="padding-top:0">
        <span class="bm-eyebrow"><b>InfraHack 2026</b>AI for sovereign cloud migration</span>
        <div class="bm-title">Leave AWS.<br>Prove it works. Come back.</div>
        <div class="bm-sub">
          Boomerang scans an AWS app, shows exactly what will break, moves it to a ready-to-run
          <span class="bm-lw">Leaseweb</span> setup with a <b>local AI model</b>, proves it with the
          app's own tests, moves the data, and can take it all back to
          <span class="bm-aws">AWS</span>.
        </div>
      </div>
    """)
    st.write("")
    _go_dashboard("cta_top")

    _html("""
      <div class="lp-stats">
        <div class="lp-stat"><b>Local AI</b><span>The model runs on your laptop. Code and keys stay home.</span></div>
        <div class="lp-stat"><b>Your tests</b><span>The app's original tests are the proof. AI never edits them.</span></div>
        <div class="lp-stat"><b>Round trip</b><span>AWS → Leaseweb → AWS, data included, counts checked.</span></div>
        <div class="lp-stat"><b>Honest costs</b><span>Published prices with sources, plus the hours to run it yourself.</span></div>
      </div>
    """)

    # ---------------- problem ----------------
    _section(
        "The problem",
        "Leaving a hyperscaler feels like a one-way door.",
        "Companies stay on AWS not because of price, but because their code is wired into "
        "proprietary services and nobody can say what will break, what it will cost, or how to go back.",
    )
    problems = [
        (iso_art.lock_in(), "Code lock-in",
         "DynamoDB, SQS, S3, Lambda and IAM calls are spread through the code and Terraform. Leaving looks like a rewrite."),
        (iso_art.unknown(), "Unknown losses",
         "Some features have no direct equivalent. Teams find out in production, not before."),
        (iso_art.no_way_back(), "No way back",
         "Once data is written on the new side, going back means a second migration nobody planned."),
        (iso_art.fuzzy_costs(), "Fuzzy costs",
         '"It\'ll be cheaper" is a guess unless you price both sides with real numbers and real ops time.'),
    ]
    _html('<div class="lp-problem">' + "".join(
        f'<div class="lp-prob"><div class="art">{art}</div><h3>{t}</h3><p>{d}</p></div>'
        for art, t, d in problems) + "</div>")

    # ---------------- how it works ----------------
    # A scroll-driven timeline: the heading stays pinned on the left while the
    # boomerang travels down the rail on the right, lighting each step it passes.
    # (title, description, who does it, uses AI, what it produces)
    steps = [
        ("Scan", "Finds every AWS dependency in code, Terraform, Dockerfile and env, with file and line.",
         "Static analysis + AI", True, "pam.json"),
        ("Traffic lights", "Labels each service and feature green, amber or red. Rules decide; the AI only explains.",
         "Rules", False, "assessment.json"),
        ("Approve", "A human reviews the plan. Nothing is written before approval.",
         "Human", False, "approval.json"),
        ("De-lock", "Rewrites each function that calls AWS onto small, pre-tested adapters. S3 is just a config change.",
         "AI, one function at a time", True, "delock.json · diffs/"),
        ("Leaseweb files", "Generates docker-compose, cloud-init, a deploy script, backups, scoped credentials and DEPLOY.md.",
         "Templates", False, "leaseweb/"),
        ("Verify", "Files valid, stack boots, the app's original tests pass, health check answers.",
         "Plain code", False, "tests passed"),
        ("Move data", "Copies items, objects and pending messages, then compares counts and checksums.",
         "Plain code", False, "counts + checksums"),
        ("Round trip", "Back to AWS with the original code and the data copied back, tests again, and a list of what got lost.",
         "Plain code", False, "what got lost"),
        ("Costs", "AWS against Leaseweb with published prices, your usage and the ops hours to run it yourself.",
         "Plain code", False, "cost estimate"),
    ]
    phases = [("Plan", "01–03"), ("Build", "04–05"), ("Prove", "06–09")]
    side = (
        '<div class="tl-side">'
        '<div class="lp-kicker">How it works</div>'
        '<div class="lp-h2">From AWS app to verified Leaseweb setup, in nine steps.</div>'
        '<div class="lp-lead">Rules and templates do everything that must be exact. The AI only does what '
        'needs reading unfamiliar code, and every AI output is checked before it is used.</div>'
        '<div class="tl-read"><span class="tl-cur">01</span><span class="tl-of">/ 09</span></div>'
        f'<div class="tl-name">{steps[0][0]}</div>'
        '<div class="tl-bar"><i></i></div>'
        '<div class="tl-phases">' + "".join(
            f'<div class="tl-phase{" on" if i == 0 else ""}"><b>{p}</b><span>{r}</span></div>'
            for i, (p, r) in enumerate(phases)) + '</div>'
        '</div>'
    )
    items = "".join(
        f'<div class="tl-step"><span class="tl-node"></span>'
        f'<div class="tl-meta"><span class="tl-n">{i:02d}</span>'
        f'<span class="tl-kind{" ai" if ai else ""}">{kind}</span></div>'
        f'<div class="tl-title">{t}</div><p>{d}</p>'
        f'<div class="tl-out"><span>Output</span><code>{out}</code></div></div>'
        for i, (t, d, kind, ai, out) in enumerate(steps, 1)
    )
    _html(
        '<div class="lp-section lp-tl">' + side +
        '<div class="tl-list"><div class="tl-rail"><i class="tl-fill"></i></div>'
        f'<div class="tl-mark"><span class="tl-boom">{logo_svg(20)}</span></div>'
        + items + '</div></div>'
    )

    # ---------------- traffic lights ----------------
    _section(
        "Traffic lights",
        "Know what breaks before anything moves.",
        "Every AWS service and feature the app uses gets one of three lights. Reds are flagged "
        "for a human; Boomerang never pretends to migrate them.",
    )
    _html(_lights_board())

    # ---------------- round trip ----------------
    _section(
        "The round trip",
        "Exit insurance: going back is part of the plan.",
        "AWS stays untouched until every check passes. Going back redeploys the original code and "
        "copies the data back, including what was written after the move, then reruns the same tests.",
    )
    _html("""
      <div class="lp-trip">
        <div class="lp-stop"><div class="where bm-aws">AWS</div>
          <div class="what">Original app and data. Scanned, never modified.</div></div>
        <div class="lp-arrow">→</div>
        <div class="lp-stop"><div class="where bm-lw">Leaseweb</div>
          <div class="what">Generated setup boots; original tests pass; data counts and checksums match.</div></div>
        <div class="lp-arrow">→</div>
        <div class="lp-stop"><div class="where bm-aws">Back to AWS</div>
          <div class="what">Original code, data copied back, tests again, and an explicit "what got lost" list.</div></div>
      </div>
    """)

    # ---------------- trust ----------------
    _section(
        "Why trust it",
        "AI where it helps. Guardrails everywhere else.",
        "Small local models are useful and fallible, so Boomerang is built to make their mistakes visible.",
    )
    trust = [
        "The app's original tests are the spec. The AI can never write or edit tests.",
        "The AI runs locally and only proposes code for one function at a time.",
        "Every AI output is rule-checked: paths, compiles, no forbidden calls.",
        "The AI never runs commands and never sees secrets.",
        "A human approves the plan before anything is written.",
        "AWS stays untouched until the checks pass.",
    ]
    _html('<ul class="lp-list">' + "".join(f"<li><i>✓</i>{t}</li>" for t in trust) + "</ul>")

    # ---------------- costs ----------------
    _section(
        "Honest costs",
        "No number without a source.",
        "The cost view uses published AWS (Frankfurt) and Leaseweb prices with the link and date for each "
        "line, your own usage, and the ops hours self-hosting adds. If AWS comes out cheaper for your "
        "numbers, Boomerang says so. Under the EU Data Act, switching charges including egress are banned "
        "from 12 January 2027.",
    )

    # ---------------- under the hood ----------------
    _section(
        "Under the hood",
        "Open source, boring on purpose.",
        "One Python package used by a CLI and this dashboard. AWS is emulated locally with moto, so no "
        "AWS account is needed for the demo.",
    )
    stack = ["Python 3.12", "Ollama · qwen2.5-coder:7b", "Pydantic v2", "Streamlit", "Typer",
             "boto3", "Postgres", "RabbitMQ", "SeaweedFS / Leaseweb Object Storage",
             "Docker Compose", "moto", "pytest", "Apache-2.0"]
    _html('<div class="lp-chips">' + "".join(f'<span class="lp-chip">{s}</span>' for s in stack) + "</div>")

    # ---------------- never claim ----------------
    _section(
        "What we don't claim",
        "Clear limits are part of the product.",
        "Passing tests prove what the tests cover, not everything.",
    )
    never = ["Zero downtime: cut-over needs a short write freeze.",
             "Full compatibility: reds stay with a human.",
             "Migrates any app: today, DynamoDB, SQS and S3 in Python apps.",
             "Always cheaper: the numbers decide, sometimes AWS wins."]
    _html('<ul class="lp-list never">' + "".join(f"<li><i>✕</i>{t}</li>" for t in never) + "</ul>")

    # ---------------- CTA ----------------
    _html("""
      <div class="lp-cta">
        <div class="lp-h2">See it on ShopLite.</div>
        <div class="lp-lead" style="margin:8px auto 0">An AWS shop app with DynamoDB, SQS and S3, migrated, verified and brought back.</div>
      </div>
    """)
    st.write("")
    _go_dashboard("cta_bottom")
    _html(f'<div class="lp-foot"><div style="display:flex;justify-content:center;margin-bottom:10px">'
          f'{wordmark_svg(22)}</div>InfraHack 2026 · Leaseweb × Velv · Apache-2.0</div>')
