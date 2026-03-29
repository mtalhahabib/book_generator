"""Notification service — Email (SMTP) and MS Teams webhook dispatching."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.config import settings
from app.services import db_service

logger = logging.getLogger(__name__)


# ── Email via SMTP ────────────────────────────────────────────────────────────


def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email via SMTP. Returns True on success."""
    if not settings.smtp_user or not settings.smtp_pass:
        logger.warning("SMTP not configured — skipping email.")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = settings.smtp_user
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_pass)
            server.send_message(msg)

        logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False


# ── Unified Dispatcher ────────────────────────────────────────────────────────

EVENT_LABELS = {
    "outline_ready": "📝 Outline Ready for Review",
    "chapter_ready": "📖 Chapter Ready for Review",
    "waiting_chapter_notes": "⏳ Waiting for Chapter Notes",
    "final_draft_compiled": "✅ Final Draft Compiled",
    "error_pause": "⚠️ Pipeline Paused — Missing Input",
}


def notify(
    event: str,
    book_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Dispatch a notification to all configured channels and log it.

    Args:
        event: Event key (see EVENT_LABELS).
        book_id: Book UUID string.
        details: Extra context (title, chapter number, etc.).
    """
    details = details or {}
    label = EVENT_LABELS.get(event, event)
    title = details.get("title", "Unknown Book")

    # Build message
    body_lines = [f"<b>{label}</b>", f"Book: <i>{title}</i>"]
    if "chapter_number" in details:
        body_lines.append(f"Chapter: {details['chapter_number']}")
    if "message" in details:
        body_lines.append(details["message"])

    dashboard_url = f"{settings.app_base_url}/static/index.html"
    body_lines.append(f'<a href="{dashboard_url}">Open Dashboard →</a>')

    html_body = "<br>".join(body_lines)
    plain_body = "\n".join(
        line.replace("<b>", "").replace("</b>", "")
        .replace("<i>", "").replace("</i>", "")
        .replace("<br>", "\n")
        for line in body_lines
    )

    # Dispatch
    email_ok = False

    if settings.notification_email_to:
        email_ok = send_email(
            to=settings.notification_email_to,
            subject=f"Book Generator: {label} — {title}",
            body=html_body,
        )

    # Log
    if email_ok:
        try:
            db_service.log_notification(
                book_id=book_id,
                event=event,
                channel="email",
                payload={"label": label, "details": details},
            )
        except Exception as exc:
            logger.error("Failed to log notification: %s", exc)
