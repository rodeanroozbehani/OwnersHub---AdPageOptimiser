"""Send review notification and deployment confirmation emails via Resend."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

FROM_EMAIL = "Henry <henry@ownershub.com.au>"
THANKS_FROM_EMAIL = "OwnersHub <hello@ownershub.com.au>"

_PRIORITY_STYLE: dict[str, tuple[str, str]] = {
    "P0": ("#fff0f0", "#c0392b"),
    "P1": ("#fff8e0", "#d68910"),
    "P2": ("#e8f5e9", "#196f3d"),
}


def _resend():  # type: ignore[return]
    try:
        import resend as _r
    except ImportError as exc:
        raise RuntimeError("resend package not installed: pip install resend") from exc
    _r.api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not _r.api_key:
        raise RuntimeError("RESEND_API_KEY environment variable not set")
    return _r


def send_review_notification(
    to_email: str,
    session_id: str,
    review_base_url: str,
    changes: list[dict[str, Any]],
    score: int,
    assessment: str,
) -> None:
    resend = _resend()
    review_url = f"{review_base_url.rstrip('/')}/review/{session_id}"
    p0 = sum(1 for c in changes if c.get("priority") == "P0")
    p1 = sum(1 for c in changes if c.get("priority") == "P1")
    p2 = sum(1 for c in changes if c.get("priority") == "P2")

    rows = ""
    for c in changes:
        priority = c.get("priority", "?")
        bg, fg = _PRIORITY_STYLE.get(priority, ("#eee", "#333"))
        title = c.get("title") or c.get("reasoning", "")[:80]
        rows += (
            f"<tr>"
            f'<td style="padding:10px 12px;border-bottom:1px solid #eee;vertical-align:top;white-space:nowrap;">'
            f'<span style="background:{bg};color:{fg};padding:2px 7px;border-radius:3px;'
            f'font-size:11px;font-weight:700;">{priority}</span></td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #eee;font-size:14px;color:#1a1a2e;font-weight:500;">'
            f'{title}'
            f'<div style="color:#888;font-size:12px;font-weight:400;margin-top:2px;">{c.get("section","")}</div>'
            f'</td>'
            f"</tr>"
        )

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;background:#fff;">
  <div style="background:#1a1a2e;padding:28px 32px;border-radius:8px 8px 0 0;">
    <h1 style="color:#fff;margin:0;font-size:22px;font-weight:600;">Henry · OwnersHub</h1>
    <p style="color:#aaa;margin:6px 0 0;font-size:14px;">Content review ready for your approval</p>
  </div>
  <div style="padding:28px 32px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;">
    <p style="color:#555;font-size:14px;margin:0 0 8px;">
      <strong style="color:#1a1a2e;">Conversion Readiness Score: {score}/10</strong>
    </p>
    <p style="color:#666;font-size:13px;line-height:1.6;margin:0 0 20px;">
      {assessment[:300]}{'…' if len(assessment) > 300 else ''}
    </p>
    <p style="font-size:14px;margin:0 0 12px;">
      <strong>{len(changes)} proposed changes</strong> — P0: {p0} · P1: {p1} · P2: {p2}
    </p>
    <table style="width:100%;border-collapse:collapse;margin:0 0 24px;font-size:14px;">
      <tr style="background:#f5f5f5;">
        <th style="padding:8px 12px;text-align:left;font-size:12px;color:#888;font-weight:600;text-transform:uppercase;width:60px;">Pri</th>
        <th style="padding:8px 12px;text-align:left;font-size:12px;color:#888;font-weight:600;text-transform:uppercase;">Suggested change</th>
      </tr>
      {rows}
    </table>
    <div style="text-align:center;">
      <a href="{review_url}"
         style="display:inline-block;background:#1a1a2e;color:#fff;padding:14px 36px;
                border-radius:6px;text-decoration:none;font-size:15px;font-weight:600;">
        Review &amp; Approve Changes →
      </a>
    </div>
    <p style="text-align:center;margin:20px 0 0;color:#bbb;font-size:12px;">
      <a href="{review_url}" style="color:#bbb;">{review_url}</a>
    </p>
  </div>
</div>"""

    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": f"Henry · {len(changes)} changes ready for review (score {score}/10)",
        "html": html,
    })
    logger.info("Review notification sent to %s (session=%s)", to_email, session_id)


def send_rereview_notification(
    to_email: str,
    session_id: str,
    review_base_url: str,
    revised_changes: list[dict[str, Any]],
    parent_session_id: str,
) -> None:
    """Email sent after Claude has revised changes based on the reviewer's feedback —
    asks for explicit re-approval before any of them deploy."""
    resend = _resend()
    review_url = f"{review_base_url.rstrip('/')}/review/{session_id}"

    rows = ""
    for c in revised_changes:
        title = c.get("title") or c.get("reasoning", "")[:80]
        feedback = c.get("feedback_text", "") or ""
        revision = ""
        rev_result = c.get("re_review_result") or {}
        if isinstance(rev_result, dict):
            revision = rev_result.get("revision_summary", "")
        rows += (
            f'<tr><td style="padding:14px 12px;border-bottom:1px solid #eee;">'
            f'<div style="font-size:14px;color:#1a1a2e;font-weight:600;">{title}</div>'
            f'<div style="margin-top:6px;font-size:12px;color:#666;"><strong>Your feedback:</strong> {feedback}</div>'
            f'<div style="margin-top:4px;font-size:12px;color:#1a5b9b;"><strong>Claude\'s revision:</strong> {revision}</div>'
            f'</td></tr>'
        )

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;background:#fff;">
  <div style="background:#1a5b9b;padding:28px 32px;border-radius:8px 8px 0 0;">
    <h1 style="color:#fff;margin:0;font-size:22px;font-weight:600;">Henry · Revised Changes</h1>
    <p style="color:#bcd5ec;margin:6px 0 0;font-size:14px;">Claude has revised {len(revised_changes)} change{'s' if len(revised_changes) != 1 else ''} based on your feedback — needs your final approval</p>
  </div>
  <div style="padding:28px 32px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;">
    <p style="color:#555;font-size:14px;margin:0 0 20px;line-height:1.6;">
      You sent feedback on these in the previous review. Claude has now revised them.
      <strong>None of these have been deployed yet</strong> — open the review to approve, reject, or send more feedback.
    </p>
    <table style="width:100%;border-collapse:collapse;margin:0 0 24px;">
      {rows}
    </table>
    <div style="text-align:center;">
      <a href="{review_url}"
         style="display:inline-block;background:#1a1a2e;color:#fff;padding:14px 36px;
                border-radius:6px;text-decoration:none;font-size:15px;font-weight:600;">
        Review Revisions →
      </a>
    </div>
    <p style="text-align:center;margin:20px 0 0;color:#bbb;font-size:12px;">
      <a href="{review_url}" style="color:#bbb;">{review_url}</a>
    </p>
  </div>
</div>"""

    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": f"Henry · {len(revised_changes)} revised change{'s' if len(revised_changes) != 1 else ''} — final approval needed",
        "html": html,
    })
    logger.info("Re-review notification sent to %s (session=%s, parent=%s)", to_email, session_id, parent_session_id)


def send_deployment_confirmation(
    to_email: str,
    session_id: str,
    approved_count: int,
    rejected_count: int,
    feedback_count: int,
    commit_url: str,
) -> None:
    resend = _resend()
    short_id = session_id[:8]
    commit_cell = (
        f'<a href="{commit_url}" style="color:#0066cc;">'
        f'{commit_url.split("/")[-1]}</a>'
        if commit_url else "–"
    )

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;background:#fff;">
  <div style="background:#0a5c36;padding:28px 32px;border-radius:8px 8px 0 0;">
    <h1 style="color:#fff;margin:0;font-size:22px;font-weight:600;">Henry · Changes Deployed</h1>
    <p style="color:#a8d8b9;margin:6px 0 0;font-size:14px;">Session {short_id} · Cloudflare Pages deploying now</p>
  </div>
  <div style="padding:28px 32px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;">
    <table style="width:100%;border-collapse:collapse;margin:0 0 20px;font-size:14px;">
      <tr>
        <td style="padding:10px 12px;border-bottom:1px solid #eee;">Approved &amp; deployed</td>
        <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:700;color:#0a5c36;">{approved_count}</td>
      </tr>
      <tr>
        <td style="padding:10px 12px;border-bottom:1px solid #eee;">Rejected</td>
        <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:700;color:#c0392b;">{rejected_count}</td>
      </tr>
      <tr>
        <td style="padding:10px 12px;">Revised by Claude</td>
        <td style="padding:10px 12px;text-align:right;font-weight:700;color:#1a5b9b;">{feedback_count}</td>
      </tr>
    </table>
    <p style="color:#555;font-size:13px;">Commit: {commit_cell}</p>
    <p style="text-align:center;margin:20px 0 0;color:#bbb;font-size:12px;">
      Henry · OwnersHub Ad &amp; Page Optimiser
    </p>
  </div>
</div>"""

    subject = (
        f"Henry · {approved_count} change{'s' if approved_count != 1 else ''} "
        f"deployed to ownershub.com.au"
    )
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html,
    })
    logger.info("Deployment confirmation sent to %s", to_email)


def send_form_submission_thanks(to_email: str, first_name: str) -> None:
    """Thank-you email sent to a visitor immediately after they submit the
    landing-page registration form. Triggered from the /api/registration-thanks
    endpoint on Henry, which the landing page calls after Formspree succeeds."""
    resend = _resend()
    safe_name = (first_name or "").strip() or "there"

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;background:#fff;color:#333;">
  <div style="background:#0D1B2A;padding:28px 32px;border-radius:8px 8px 0 0;">
    <h1 style="color:#fff;margin:0;font-size:22px;font-weight:600;">Thanks for registering, {safe_name}.</h1>
    <p style="color:#9aa6b3;margin:8px 0 0;font-size:14px;">We&rsquo;ve received your interest in the OwnersHub pilot.</p>
  </div>
  <div style="padding:28px 32px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;line-height:1.7;">
    <p style="margin:0 0 16px;font-size:15px;">
      We&rsquo;re building OwnersHub specifically for self-managed NSW Owners Corporations and your registration helps us shape the pilot around real committee needs.
    </p>
    <p style="margin:0 0 16px;font-size:15px;"><strong>What happens next:</strong></p>
    <ul style="margin:0 0 18px 18px;padding:0;font-size:15px;">
      <li style="margin-bottom:8px;">We&rsquo;ll review your submission to assess pilot fit.</li>
      <li style="margin-bottom:8px;">If your scheme matches our intake criteria, we&rsquo;ll be in touch directly to discuss next steps.</li>
      <li style="margin-bottom:8px;">In the meantime there&rsquo;s nothing you need to do.</li>
    </ul>
    <p style="margin:0 0 16px;font-size:15px;">
      If you have questions or anything has changed since you registered, just reply to this email.
    </p>
    <p style="margin:24px 0 0;font-size:15px;">
      &mdash; The OwnersHub team
    </p>
    <p style="margin:24px 0 0;color:#9aa6b3;font-size:12px;line-height:1.5;">
      You&rsquo;re receiving this because you submitted the registration form on ownershub.com.au.
      We&rsquo;ll only use your details to contact you about pilot intake. See our
      <a href="https://ownershub.com.au/legal/privacy.html" style="color:#9aa6b3;">privacy policy</a> for more.
    </p>
  </div>
</div>"""

    resend.Emails.send({
        "from": THANKS_FROM_EMAIL,
        "to": [to_email],
        "subject": "Thanks for registering interest in OwnersHub",
        "html": html,
    })
    logger.info("Registration thanks email sent to %s", to_email)
