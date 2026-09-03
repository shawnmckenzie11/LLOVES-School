"""Transactional email helpers for LLOVES first-login verification."""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)


def allow_dev_verification_code() -> bool:
    """Return True only when explicitly opted into on-page verification codes."""
    return (os.getenv("ALLOW_DEV_VERIFICATION_CODE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_production() -> bool:
    """Return True when this process is running as production."""
    return (os.getenv("FLASK_ENV") or "").strip().lower() == "production"


def show_on_page_verification_code() -> bool:
    """Whether the verify page may display the plaintext 6-digit code.

    Production never shows the code. Local/dev may show it only when
    ``ALLOW_DEV_VERIFICATION_CODE`` is on and Resend/SMTP is not configured.
    """
    if is_production():
        return False
    return allow_dev_verification_code() and not is_email_delivery_configured()


def is_email_delivery_configured() -> bool:
    """Return True when Resend or SMTP credentials are present."""
    if (os.getenv("RESEND_API_KEY") or "").strip():
        return True
    return bool(
        (os.getenv("SMTP_SERVER") or "").strip()
        and (os.getenv("SMTP_USERNAME") or "").strip()
        and (os.getenv("SMTP_PASSWORD") or "").strip()
    )


def _sender_address() -> str:
    """Resolve the From address for outbound mail."""
    return (
        (os.getenv("EMAIL_FROM") or "").strip()
        or (os.getenv("SMTP_SENDER") or "").strip()
        or (os.getenv("SMTP_USERNAME") or "").strip()
        or "noreply@mckenzian.com"
    )


def _build_verification_message(username: str, code: str) -> Tuple[str, str, str]:
    """Build subject/text/html bodies for a LLOVES verification email."""
    subject = "Email verification | Learning Live Online Virtually & Explicitly School"
    text = (
        f"Hello {username},\n\n"
        f"Your LLOVES verification code is: {code}\n\n"
        "Enter this code to finish your first sign-in. You will not need it again.\n"
    )
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #0b1220; color: #f8fafc; padding: 20px;">
        <div style="max-width: 500px; margin: 0 auto; background-color: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 30px;">
          <h2 style="color: #67e8f9;">Verify your email</h2>
          <p style="color: #94a3b8;">Hello <strong>{username}</strong>,</p>
          <p style="color: #94a3b8;">Use this code to finish your first LLOVES login:</p>
          <div style="background-color: rgba(103, 232, 249, 0.1); border: 1px dashed #67e8f9; padding: 15px; border-radius: 8px; font-size: 24px; font-weight: bold; letter-spacing: 4px; text-align: center; color: #f8fafc; margin: 25px 0;">
            {code}
          </div>
          <p style="color: #94a3b8; font-size: 12px;">If you did not request this code, you can ignore this email.</p>
        </div>
      </body>
    </html>
    """
    return subject, text, html


def _send_via_resend(recipient_email: str, subject: str, text: str, html: str) -> bool:
    """Send mail through the Resend HTTP API."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not api_key:
        return False
    sender = _sender_address()
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": sender,
            "to": [recipient_email],
            "subject": subject,
            "text": text,
            "html": html,
        },
        timeout=20,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Resend API error {response.status_code}: {response.text[:300]}")
    logger.info("Sent verification email via Resend to %s", recipient_email)
    return True


def _send_via_smtp(recipient_email: str, subject: str, text: str, html: str) -> bool:
    """Send mail through SMTP (STARTTLS on 587 or SSL on 465 by default)."""
    smtp_server = (os.getenv("SMTP_SERVER") or "").strip()
    smtp_username = (os.getenv("SMTP_USERNAME") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    if not smtp_server or not smtp_username or not smtp_password:
        return False

    smtp_port = int((os.getenv("SMTP_PORT") or "587").strip() or "587")
    use_ssl = (os.getenv("SMTP_USE_SSL") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not use_ssl and smtp_port == 465:
        use_ssl = True

    sender = _sender_address()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context, timeout=30) as server:
            server.login(smtp_username, smtp_password)
            server.sendmail(sender, [recipient_email], msg.as_string())
    else:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.sendmail(sender, [recipient_email], msg.as_string())

    logger.info("Sent verification email via SMTP to %s", recipient_email)
    return True


def send_email(recipient_email: str, subject: str, text: str, html: str) -> bool:
    """Send an arbitrary email via Resend or SMTP."""
    errors = []

    if (os.getenv("RESEND_API_KEY") or "").strip():
        try:
            return _send_via_resend(recipient_email, subject, text, html)
        except Exception as exc:
            errors.append(f"resend: {exc}")
            logger.exception("Resend email failed")

    if (
        (os.getenv("SMTP_SERVER") or "").strip()
        and (os.getenv("SMTP_USERNAME") or "").strip()
        and (os.getenv("SMTP_PASSWORD") or "").strip()
    ):
        try:
            return _send_via_smtp(recipient_email, subject, text, html)
        except Exception as exc:
            errors.append(f"smtp: {exc}")
            logger.exception("SMTP email failed")

    if errors:
        logger.error("Email delivery failed: %s", "; ".join(errors))
    else:
        logger.warning(
            "Email delivery is not configured; message to %s was not sent.",
            recipient_email,
        )
    return False


def send_verification_email(recipient_email: str, username: str, code: str) -> bool:
    """Send a verification code email. Prefers Resend, then SMTP."""
    subject, text, html = _build_verification_message(username, code)
    if send_email(recipient_email, subject, text, html):
        return True
    if allow_dev_verification_code() and not is_email_delivery_configured():
        logger.warning("[DEV VERIFICATION CODE] %s -> %s", username, code)
    return False


def delivery_status_message(sent: Optional[bool] = None) -> str:
    """User-facing status copy for the verify page."""
    if sent is True:
        return "A verification code was sent to your email. Check your inbox and spam folder."
    if not is_email_delivery_configured():
        return (
            "Email delivery is not configured on this server yet. "
            "Use the on-page code if shown, or contact solutions@mckenzian.com."
        )
    return (
        "We could not send the verification email just now. "
        "Please try Resend code, or contact solutions@mckenzian.com."
    )
