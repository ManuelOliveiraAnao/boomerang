"""Landing page: what Boomerang is, how it works, why to trust it (owner: D).

Wording follows docs/01 and docs/09: no claims of zero downtime, full
compatibility, "migrates any app" or "always cheaper".
"""
from __future__ import annotations

import streamlit as st

from ui.theme import LIGHT_COLORS, logo_svg


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
  document.addEventListener("mousemove", (e) => {
    s.mx = e.clientX; s.my = e.clientY;
    const mark = document.querySelector(".lp-mark.bm-follow");
    if (!mark) return;
    const r = mark.getBoundingClientRect();
    s.hover = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
  }, { passive: true });
  document.addEventListener("mouseleave", () => { s.mx = null; s.hover = false; });
  function frame(now) {
    const dt = Math.min(0.05, (now - s.last) / 1000); s.last = now;
    const mark = document.querySelector(".lp-mark.bm-follow");
    const boom = mark && mark.querySelector(".bm-boom");
    if (boom) {
      const r = mark.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      let tx = 0, ty = 0;
      if (s.mx !== null) {
        tx = Math.max(-MAX, Math.min(MAX, (s.mx - cx) * PULL));
        ty = Math.max(-MAX, Math.min(MAX, (s.my - cy) * PULL));
      }
      s.x += (tx - s.x) * Math.min(1, dt * 4);
      s.y += (ty - s.y) * Math.min(1, dt * 4);
      const target = s.hover ? SPIN : 0;
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


def render() -> None:
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
    _html("""
      <div class="lp-grid">
        <div class="lp-card"><div class="lp-icon">🔗</div><h3>Code lock-in</h3>
          <p>DynamoDB, SQS, S3, Lambda and IAM calls are spread through the code and Terraform. Leaving looks like a rewrite.</p></div>
        <div class="lp-card"><div class="lp-icon">❓</div><h3>Unknown losses</h3>
          <p>Some features have no direct equivalent. Teams find out in production, not before.</p></div>
        <div class="lp-card"><div class="lp-icon">🚪</div><h3>No way back</h3>
          <p>Once data is written on the new side, going back means a second migration nobody planned.</p></div>
        <div class="lp-card"><div class="lp-icon">💶</div><h3>Fuzzy costs</h3>
          <p>"It'll be cheaper" is a guess unless you price both sides with real numbers and real ops time.</p></div>
      </div>
    """)

    # ---------------- how it works ----------------
    _section(
        "How it works",
        "From AWS app to verified Leaseweb setup, in nine steps.",
        "Rules and templates do everything that must be exact. The AI only does what needs "
        "reading unfamiliar code, and every AI output is checked before it is used.",
    )
    steps = [
        ("Scan", "Finds every AWS dependency in code, Terraform, Dockerfile and env, with file and line.", "AI + static analysis", True),
        ("Traffic lights", "Labels each service and feature green, amber or red. Rules decide; AI only explains.", "Rules", False),
        ("Approve", "A human reviews the plan. Nothing is written before approval.", "Human", False),
        ("De-lock", "Rewrites each function that calls AWS onto small, pre-tested adapters. S3 is just a config change.", "AI per function", True),
        ("Leaseweb files", "Generates docker-compose, cloud-init, deploy script, backups, scoped credentials and DEPLOY.md.", "Templates", False),
        ("Verify live", "Files valid → stack boots → the app's original tests pass → health check.", "Plain code", False),
        ("Move data", "Copies items, objects and pending messages; compares counts and checksums.", "Plain code", False),
        ("Round trip", "Back to AWS with the original code and data copied back, tests again, and a list of what got lost.", "Plain code", False),
        ("Costs", "AWS vs Leaseweb with published prices, your usage and the ops hours to run it yourself.", "Plain code", False),
    ]
    _html('<div class="lp-steps">' + "".join(
        f'<div class="lp-step"><div class="num"></div><div><b>{t}</b><p>{d}</p>'
        f'<span class="lp-tag {"ai" if ai else ""}">{tag}</span></div></div>'
        for t, d, tag, ai in steps
    ) + "</div>")

    # ---------------- traffic lights ----------------
    _section(
        "Traffic lights",
        "Know what breaks before anything moves.",
        "Every AWS service and feature the app uses gets one of three lights. Reds are flagged "
        "for a human; Boomerang never pretends to migrate them.",
    )
    g, a, r = LIGHT_COLORS["green"], LIGHT_COLORS["amber"], LIGHT_COLORS["red"]
    _html(f"""
      <div class="lp-grid">
        <div class="lp-card lp-light" style="--c:{g}"><span class="dot"></span><h3>Config only</h3>
          <p>Same API on the other side; only endpoints and keys change.</p>
          <ul><li>S3 → S3-compatible object storage</li></ul></div>
        <div class="lp-card lp-light" style="--c:{a}"><span class="dot"></span><h3>Moves via adapter</h3>
          <p>Moves through a tested adapter. Every behaviour difference is listed.</p>
          <ul><li>DynamoDB → Postgres</li><li>SQS → RabbitMQ (retries, dead-letter queue)</li></ul></div>
        <div class="lp-card lp-light" style="--c:{r}"><span class="dot"></span><h3>Needs a human</h3>
          <p>No safe automatic equivalent. Flagged, explained and left for a person.</p>
          <ul><li>Lambda triggers</li><li>IAM roles and policies</li><li>CloudWatch alarms</li></ul></div>
      </div>
    """)

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
    _html(f'<div class="lp-foot"><span style="display:inline-block;vertical-align:middle;margin-right:6px">{logo_svg(14)}</span>Boomerang · InfraHack 2026 · Leaseweb × Velv · Apache-2.0</div>')
