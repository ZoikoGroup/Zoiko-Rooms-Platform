import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

# Local dev fallback only -- mirrors Django's/Rails' well-known file-based email
# backends. Used whenever EMAIL_PROVIDER is not explicitly set to "smtp" (the
# default), so nothing sends real mail until an operator opts in.
DEV_MAIL_OUTBOX_DIR = Path("dev_mail_outbox")

BRAND_COLOR = "#0e2f73"
SUPPORT_EMAIL = "support@zoikorooms.com"


def _render_email(*, heading: str, body_lines: list[str], cta_label: str | None = None, cta_url: str | None = None) -> tuple[str, str]:
    """Returns (html, text) for a simple branded transactional email. Callers
    control exactly what appears in body_lines -- never pass password/document
    contents/raw internal IDs into this."""
    text_parts = [heading, "", *body_lines]
    if cta_label and cta_url:
        text_parts += ["", f"{cta_label}: {cta_url}"]
    text_parts += ["", f"Need help? Contact {SUPPORT_EMAIL}", "-- Zoiko Rooms"]
    text_body = "\n".join(text_parts)

    body_html = "".join(f'<p style="margin:0 0 12px;color:#334155;font-size:15px;line-height:1.5;">{line}</p>' for line in body_lines)
    cta_html = ""
    if cta_label and cta_url:
        cta_html = f"""
        <p style="margin:24px 0;">
          <a href="{cta_url}" style="background:{BRAND_COLOR};color:#ffffff;text-decoration:none;
             padding:12px 24px;border-radius:9999px;font-weight:600;font-size:14px;display:inline-block;">
            {cta_label}
          </a>
        </p>"""

    html_body = f"""<!DOCTYPE html>
<html>
  <body style="margin:0;padding:32px 16px;background:#f1f5f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:16px;overflow:hidden;">
      <tr><td style="background:{BRAND_COLOR};padding:24px 32px;">
        <span style="color:#ffffff;font-size:18px;font-weight:800;">Zoiko Rooms</span>
      </td></tr>
      <tr><td style="padding:32px;">
        <h1 style="margin:0 0 16px;color:#0f172a;font-size:20px;font-weight:800;">{heading}</h1>
        {body_html}
        {cta_html}
        <p style="margin:24px 0 0;color:#94a3b8;font-size:12px;">
          Need help? Contact <a href="mailto:{SUPPORT_EMAIL}" style="color:{BRAND_COLOR};">{SUPPORT_EMAIL}</a>.
        </p>
      </td></tr>
    </table>
  </body>
</html>"""
    return html_body, text_body


def _write_to_dev_outbox(to_email: str, subject: str, text_body: str) -> None:
    DEV_MAIL_OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "@.-_" else "_" for c in to_email)
    outbox_file = DEV_MAIL_OUTBOX_DIR / f"{safe_name}.txt"
    outbox_file.write_text(
        "This file is a local development mail outbox entry, not a sent email.\n"
        f"To: {to_email}\n"
        f"Generated at: {datetime.now(timezone.utc).isoformat()}\n"
        f"Subject: {subject}\n\n"
        f"{text_body}\n",
        encoding="utf-8",
    )


def _send_via_smtp(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    if not settings.smtp_host or not settings.smtp_username or not settings.smtp_password:
        logger.error(
            "mailer: EMAIL_PROVIDER=smtp but SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD are not fully "
            "configured -- email to %s was not sent.", to_email,
        )
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.email_from
    message["To"] = to_email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return True
    except Exception:
        # Email is always best-effort: a delivery failure must never roll back or
        # otherwise corrupt the database transaction that triggered it, so this is
        # caught, logged, and swallowed here rather than re-raised to the caller.
        logger.exception("mailer: failed to send email to %s via SMTP", to_email)
        return False


def send_email(to_email: str, subject: str, *, heading: str, body_lines: list[str], cta_label: str | None = None, cta_url: str | None = None) -> bool:
    """Sends one branded transactional email. Never raises -- returns False and
    logs on any failure, so a broken mail provider can never abort the calling
    request's database transaction."""
    html_body, text_body = _render_email(heading=heading, body_lines=body_lines, cta_label=cta_label, cta_url=cta_url)

    if settings.email_provider != "smtp":
        try:
            _write_to_dev_outbox(to_email, subject, text_body)
        except Exception:
            logger.exception("mailer: failed to write dev outbox entry for %s", to_email)
            return False
        return True

    return _send_via_smtp(to_email, subject, html_body, text_body)


def send_password_reset_email(to_email: str, reset_link: str, expires_minutes: int) -> None:
    send_email(
        to_email,
        "Reset your Zoiko Rooms password",
        heading="Reset your password",
        body_lines=[
            f"We received a request to reset your Zoiko Rooms password. This link is valid for {expires_minutes} minutes and can only be used once.",
            "If you didn't request this, you can safely ignore this email -- your password won't change.",
        ],
        cta_label="Reset password",
        cta_url=reset_link,
    )


def send_identity_verification_approved_email(to_email: str, full_name: str) -> None:
    send_email(
        to_email,
        "Your identity has been verified",
        heading="You're verified!",
        body_lines=[
            f"Hi {full_name},",
            "Your identity document has been reviewed and approved. You can now apply to rent a room and publish listings if you host.",
        ],
        cta_label="Go to your account",
        cta_url=f"{settings.frontend_url}/account/identity",
    )


def send_identity_verification_rejected_email(to_email: str, full_name: str, notes: str = "") -> None:
    body_lines = [
        f"Hi {full_name},",
        "Your identity document could not be verified. Please submit a new document to try again.",
    ]
    if notes:
        body_lines.append(f"Reviewer notes: {notes}")
    send_email(
        to_email,
        "Your identity verification needs another look",
        heading="Verification not approved",
        body_lines=body_lines,
        cta_label="Submit a new document",
        cta_url=f"{settings.frontend_url}/account/identity",
    )


def send_listing_published_email(to_email: str, full_name: str, listing_name: str) -> None:
    send_email(
        to_email,
        f"Your listing '{listing_name}' has been approved and published",
        heading="Your listing is live!",
        body_lines=[
            f"Hi {full_name},",
            f"Your listing \"{listing_name}\" has been reviewed, approved and is now published on the marketplace.",
        ],
        cta_label="View your listings",
        cta_url=f"{settings.frontend_url}/account/host/listings",
    )


def send_alert_confirmation_email(to_email: str, city: str, unsubscribe_url: str) -> None:
    send_email(
        to_email,
        f"You'll be notified about new rooms in {city}",
        heading="Alert saved",
        body_lines=[
            f"We'll email you at this address whenever a new room matching your criteria in {city} is published.",
            "You can stop these emails at any time using the link below.",
        ],
        cta_label="Unsubscribe from this alert",
        cta_url=unsubscribe_url,
    )


def send_alert_match_email(to_email: str, city: str, listing_names: list[str], unsubscribe_url: str) -> None:
    names = ", ".join(listing_names)
    send_email(
        to_email,
        f"New room{'s' if len(listing_names) != 1 else ''} published in {city}",
        heading=f"New in {city}",
        body_lines=[
            f"The following room{'s' if len(listing_names) != 1 else ''} matching your alert "
            f"{'were' if len(listing_names) != 1 else 'was'} just published: {names}.",
            "Sign in to view details and apply.",
            f"Unsubscribe from this alert: {unsubscribe_url}",
        ],
        cta_label="View rooms",
        cta_url=f"{settings.frontend_url}/find-a-room?city={city}",
    )


def send_listing_rejected_email(to_email: str, full_name: str, listing_name: str, reason: str = "") -> None:
    body_lines = [
        f"Hi {full_name},",
        f"Your listing \"{listing_name}\" was not approved.",
    ]
    if reason:
        body_lines.append(f"Reason: {reason}")
    send_email(
        to_email,
        f"Your listing '{listing_name}' was not approved",
        heading="Listing not approved",
        body_lines=body_lines,
        cta_label="Review your listings",
        cta_url=f"{settings.frontend_url}/account/host/listings",
    )
