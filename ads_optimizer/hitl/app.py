"""Flask approval web UI for HITL content review sessions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from flask import Flask, abort, redirect, render_template_string, request, url_for

logger = logging.getLogger(__name__)

# ── Review page template ──────────────────────────────────────────────────────

_REVIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Henry · Review</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f3f5;color:#333;padding-bottom:90px}
.hdr{background:#1a1a2e;color:#fff;padding:20px 24px}
.hdr h1{font-size:18px;font-weight:600}
.hdr .sub{color:#aaa;font-size:13px;margin-top:4px}
.assessment{background:#fff;border-bottom:1px solid #eee;padding:14px 24px;font-size:13px;color:#555;line-height:1.6}
.wrap{max-width:860px;margin:20px auto;padding:0 16px}
.card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:14px;overflow:hidden}
.card-hdr{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid #f0f0f0}
.badge{font-size:10px;font-weight:700;padding:3px 7px;border-radius:3px;text-transform:uppercase}
.P0{background:#fff0f0;color:#c0392b}.P1{background:#fff8e0;color:#d68910}.P2{background:#e8f5e9;color:#196f3d}
.card-id{font-weight:600;font-size:14px}.card-meta{color:#888;font-size:12px;margin-left:auto}
.card-body{padding:14px 18px}
.lbl{font-size:11px;font-weight:600;text-transform:uppercase;color:#999;letter-spacing:.5px;margin:0 0 4px}
.reasoning{font-size:13px;color:#444;line-height:1.6;margin-bottom:10px}
.expected{font-size:12px;color:#1a6b3a;margin-bottom:14px;line-height:1.5;padding:8px 10px;background:#f0faf4;border-radius:4px}
.toggles{display:flex;gap:8px;margin-bottom:10px}
.tog{font-size:11px;padding:4px 9px;border:1px solid #ddd;border-radius:4px;background:#f9f9f9;cursor:pointer;color:#555}
.tog:hover,.tog.open{background:#e8e8f0;border-color:#c0c0d0}
.code{display:none;background:#f6f6f6;border:1px solid #e8e8e8;border-radius:4px;padding:10px;
      font-size:11px;font-family:'Courier New',monospace;white-space:pre-wrap;word-break:break-all;
      max-height:180px;overflow-y:auto;margin-bottom:8px}
.code.open{display:block}
.decision{display:flex;flex-wrap:wrap;gap:20px;padding-top:14px;border-top:1px solid #f0f0f0;margin-top:14px}
.opt label{display:flex;align-items:center;gap:6px;cursor:pointer;font-size:14px;font-weight:500}
.opt.approve label{color:#0a5c36}.opt.reject label{color:#c0392b}.opt.fb label{color:#1a5b9b}
.fb-area{display:none;margin-top:10px}
.fb-area textarea{width:100%;border:1px solid #ddd;border-radius:4px;padding:9px;
                  font-size:13px;font-family:inherit;resize:vertical;min-height:72px}
.bar{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:2px solid #e8e8e8;
     padding:14px 24px;display:flex;align-items:center;gap:16px;z-index:100;box-shadow:0 -2px 8px rgba(0,0,0,.06)}
.submit-btn{background:#1a1a2e;color:#fff;border:none;padding:11px 28px;border-radius:6px;
            font-size:14px;font-weight:600;cursor:pointer}
.submit-btn:hover{background:#2d2d4e}
.tally{font-size:13px;color:#888}
</style>
</head>
<body>
<div class="hdr">
  <h1>Henry · OwnersHub Review</h1>
  <div class="sub">Score {{ score }}/10 · {{ total }} change{{ 's' if total != 1 else '' }} to review</div>
</div>
<div class="assessment">{{ assessment }}</div>
<form class="wrap" method="POST" id="reviewForm">
  {% for c in changes %}
  {% set cid = c.id if c.id is defined and c.id else loop.index0 %}
  <div class="card">
    <div class="card-hdr">
      <span class="badge {{ c.priority }}">{{ c.priority }}</span>
      <span class="card-id">{{ cid }}</span>
      <span class="card-meta">{{ c.section }} · {{ c.dimension }} · {{ c.effort }} effort</span>
    </div>
    <div class="card-body">
      <div class="lbl">Reasoning</div>
      <div class="reasoning">{{ c.reasoning }}</div>
      <div class="lbl">Expected value</div>
      <div class="expected">{{ c.expected_value }}</div>
      <div class="toggles">
        <button type="button" class="tog" onclick="toggleCode('cur-{{ cid }}',this)">Current HTML</button>
        <button type="button" class="tog" onclick="toggleCode('prop-{{ cid }}',this)">Proposed HTML</button>
      </div>
      <pre id="cur-{{ cid }}" class="code">{{ c.current_element }}</pre>
      <pre id="prop-{{ cid }}" class="code">{{ c.proposed_element }}</pre>
      {% if c.implementation_note %}
      <div class="lbl" style="margin-top:10px">Implementation note</div>
      <div class="reasoning" style="color:#666">{{ c.implementation_note }}</div>
      {% endif %}
      <div class="decision">
        <div class="opt approve">
          <label>
            <input type="radio" name="decision_{{ cid }}" value="approved"
                   onchange="onDecision(this)" required>
            Approve
          </label>
        </div>
        <div class="opt reject">
          <label>
            <input type="radio" name="decision_{{ cid }}" value="rejected"
                   onchange="onDecision(this)">
            Reject
          </label>
        </div>
        <div class="opt fb">
          <label>
            <input type="radio" name="decision_{{ cid }}" value="feedback"
                   onchange="onDecision(this)">
            Feedback
          </label>
        </div>
      </div>
      <div id="fb-{{ cid }}" class="fb-area">
        <textarea name="feedback_{{ cid }}"
                  placeholder="Describe what you'd like Claude to revise…"></textarea>
      </div>
    </div>
  </div>
  {% endfor %}
  <div class="bar">
    <button type="submit" class="submit-btn">Submit decisions →</button>
    <span class="tally" id="tally">0 / {{ total }} decided</span>
  </div>
</form>
<script>
function toggleCode(id, btn) {
  const el = document.getElementById(id);
  el.classList.toggle('open');
  btn.classList.toggle('open');
}
function onDecision(radio) {
  const cid = radio.name.replace('decision_', '');
  const fb = document.getElementById('fb-' + cid);
  fb.style.display = radio.value === 'feedback' ? 'block' : 'none';
  updateTally();
}
function updateTally() {
  const decided = document.querySelectorAll('input[name^="decision_"]:checked').length;
  document.getElementById('tally').textContent = decided + ' / {{ total }} decided';
}
</script>
</body>
</html>"""

# ── Done page template ────────────────────────────────────────────────────────

_DONE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Henry · Done</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f3f5;
     color:#333;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}
.card{background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.1);padding:40px;
      max-width:480px;width:100%;text-align:center}
.icon{font-size:48px;margin-bottom:16px}
h1{font-size:22px;font-weight:600;margin-bottom:8px}
.sub{color:#666;font-size:14px;line-height:1.6;margin-bottom:24px}
.stats{display:flex;border:1px solid #eee;border-radius:6px;overflow:hidden;margin-bottom:24px}
.stat{flex:1;padding:14px;border-right:1px solid #eee}
.stat:last-child{border-right:none}
.stat .n{font-size:28px;font-weight:700}
.stat .l{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.n-ok{color:#0a5c36}.n-no{color:#c0392b}.n-fb{color:#1a5b9b}
.commit{font-size:12px;color:#888;margin-top:12px}
.commit a{color:#0066cc;text-decoration:none}
.err{color:#c0392b;font-size:13px;margin-top:12px;padding:10px;background:#fff0f0;border-radius:4px;text-align:left}
</style>
</head>
<body>
<div class="card">
  {% if deployed %}
  <div class="icon">🚀</div>
  <h1>Changes Deployed</h1>
  <p class="sub">Approved changes committed and pushed to GitHub.<br>Cloudflare Pages will be live within ~60 seconds.</p>
  {% elif deploy_error %}
  <div class="icon">⚠️</div>
  <h1>Decisions Saved</h1>
  <p class="sub">Decisions recorded, but deployment failed.</p>
  <div class="err">{{ deploy_error }}</div>
  {% else %}
  <div class="icon">✓</div>
  <h1>Decisions Saved</h1>
  <p class="sub">No approved changes to deploy.</p>
  {% endif %}
  <div class="stats">
    <div class="stat"><div class="n n-ok">{{ approved }}</div><div class="l">Approved</div></div>
    <div class="stat"><div class="n n-no">{{ rejected }}</div><div class="l">Rejected</div></div>
    <div class="stat"><div class="n n-fb">{{ rereviewed }}</div><div class="l">Re-reviewed</div></div>
  </div>
  {% if commit_url %}
  <div class="commit">Commit: <a href="{{ commit_url }}">{{ commit_url.split('/')[-1] }}</a></div>
  {% endif %}
</div>
</body>
</html>"""


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(config: dict[str, Any], project_root: Path) -> Flask:
    app = Flask(__name__)
    app.secret_key = "hitl-henry-ownershub-2026"

    hitl_cfg = config.get("hitl", {})
    session_dir = project_root / hitl_cfg.get("session_dir", "data/review-sessions")
    to_email: str = hitl_cfg.get("to_email", "")
    landing_repo = Path(hitl_cfg.get("landing_page_repo", ""))
    html_file: str = hitl_cfg.get("html_file", "index.html")
    branch: str = hitl_cfg.get("branch", "main")
    git_name: str = hitl_cfg.get("git_user_name", "Henry")
    git_email: str = hitl_cfg.get("git_user_email", "henry@ownershub.com.au")

    @app.route("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.route("/review/<session_id>", methods=["GET"])
    def review(session_id: str):  # type: ignore[return]
        from .state import ReviewSession

        try:
            sess = ReviewSession.load(session_dir, session_id)
        except FileNotFoundError:
            abort(404)

        data = sess.read()
        if data["status"] != "pending":
            return redirect(url_for("done", session_id=session_id))

        changes = sorted(
            data.get("changes", []),
            key=lambda c: {"P0": 0, "P1": 1, "P2": 2}.get(c.get("priority", "P2"), 2),
        )
        return render_template_string(
            _REVIEW_HTML,
            session=data,
            changes=changes,
            score=data.get("conversion_readiness_score", 0),
            assessment=data.get("overall_assessment", ""),
            total=len(changes),
        )

    @app.route("/review/<session_id>", methods=["POST"])
    def submit_review(session_id: str):  # type: ignore[return]
        from .state import ReviewSession
        from .mailer import send_deployment_confirmation
        from .deployer import apply_and_deploy

        try:
            sess = ReviewSession.load(session_dir, session_id)
        except FileNotFoundError:
            abort(404)

        data = sess.read()
        if data["status"] != "pending":
            return redirect(url_for("done", session_id=session_id))

        # Record decisions from form
        for i, change in enumerate(data["changes"]):
            cid = change.get("id") or str(i)
            decision = request.form.get(f"decision_{cid}", "rejected")
            change["decision"] = decision
            change["feedback_text"] = (
                request.form.get(f"feedback_{cid}", "").strip()
                if decision == "feedback"
                else None
            )

        # Re-review feedback items via Claude
        for change in data["changes"]:
            if change["decision"] == "feedback" and change.get("feedback_text"):
                try:
                    revised = _re_review_change(change, config, project_root)
                    change["re_review_result"] = revised
                    change["decision"] = "approved"
                    logger.info("Re-review complete for %s", change.get("id", "?"))
                except Exception as exc:
                    logger.warning("Re-review failed for %s: %s — rejecting", change.get("id", "?"), exc)
                    change["decision"] = "rejected"

        # Apply and deploy approved changes
        approved = [c for c in data["changes"] if c["decision"] == "approved"]
        rejected = [c for c in data["changes"] if c["decision"] == "rejected"]
        rereviewed = [c for c in data["changes"] if c.get("re_review_result")]

        if approved:
            try:
                html_path = landing_repo / html_file
                commit_url = apply_and_deploy(
                    html_path=html_path,
                    repo_dir=landing_repo,
                    approved_changes=approved,
                    branch=branch,
                    git_user_name=git_name,
                    git_user_email=git_email,
                )
                data["deployed"] = bool(commit_url)
                data["commit_url"] = commit_url
            except Exception as exc:
                logger.error("Deploy failed: %s", exc)
                data["deploy_error"] = str(exc)

        data["status"] = "completed"
        sess.update(data)

        try:
            send_deployment_confirmation(
                to_email=to_email,
                session_id=session_id,
                approved_count=len(approved),
                rejected_count=len(rejected),
                feedback_count=len(rereviewed),
                commit_url=data.get("commit_url", ""),
            )
        except Exception as exc:
            logger.warning("Confirmation email failed: %s", exc)

        return redirect(url_for("done", session_id=session_id))

    @app.route("/review/<session_id>/done")
    def done(session_id: str):  # type: ignore[return]
        from .state import ReviewSession

        try:
            sess = ReviewSession.load(session_dir, session_id)
        except FileNotFoundError:
            abort(404)

        data = sess.read()
        changes = data.get("changes", [])
        return render_template_string(
            _DONE_HTML,
            deployed=data.get("deployed", False),
            deploy_error=data.get("deploy_error", ""),
            commit_url=data.get("commit_url", ""),
            approved=sum(1 for c in changes if c.get("decision") == "approved"),
            rejected=sum(1 for c in changes if c.get("decision") == "rejected"),
            rereviewed=sum(1 for c in changes if c.get("re_review_result")),
        )

    return app


def _re_review_change(
    change: dict[str, Any],
    config: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    from ads_optimizer.claude_client import ClaudeClient

    prompt_path = project_root / "prompts" / "hitl_rereview.txt"
    client = ClaudeClient(config)
    return client.re_review_change(
        prompt_path=prompt_path,
        change=change,
        feedback=change["feedback_text"],
    )
