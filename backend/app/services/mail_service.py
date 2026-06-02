"""SMTP mail service. Honors MAIL_DRY_RUN: when true, just logs."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def send_mail(*, to: str, subject: str, body: str, html_body: str | None = None) -> None:
    """Send an email. In dry-run mode just logs the content."""
    settings = get_settings()

    if settings.mail_dry_run:
        logger.info(
            "[MAIL DRY-RUN] to=%s subject=%s\n%s",
            to,
            subject,
            body,
        )
        return

    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            if settings.smtp_port == 587:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("Sent mail to=%s subject=%s", to, subject)
    except Exception:
        logger.exception("Failed to send mail to=%s", to)
        raise


def send_verification_email(*, to: str, display_name: str, token: str) -> None:
    settings = get_settings()
    verify_url = f"{settings.frontend_base_url}/verify-email?token={token}"
    body = (
        f"{display_name} 님, HEAXHub 가입을 환영합니다.\n\n"
        f"아래 링크에서 이메일을 인증해 주세요 (24시간 내):\n{verify_url}\n\n"
        f"본인이 가입한 적이 없다면 이 메일을 무시하세요."
    )
    send_mail(to=to, subject="[HEAXHub] 이메일 인증 안내", body=body)


def send_password_reset_email(*, to: str, display_name: str, token: str) -> None:
    settings = get_settings()
    reset_url = f"{settings.frontend_base_url}/password/reset?token={token}"
    body = (
        f"{display_name} 님,\n\n"
        f"아래 링크에서 비밀번호를 재설정해 주세요 (2시간 내):\n{reset_url}\n\n"
        f"본인이 요청한 적이 없다면 이 메일을 무시하세요."
    )
    send_mail(to=to, subject="[HEAXHub] 비밀번호 재설정 안내", body=body)
