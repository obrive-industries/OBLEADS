"""Generic SMTP + IMAP mailbox provider.

Works with any real mailbox: AgentMail (their IMAP/SMTP relay), Fastmail,
a Google Workspace account with an app password, or self-hosted mail.

.env:
    SMTP_HOST=            IMAP_HOST=
    SMTP_PORT=587         IMAP_PORT=993
    SMTP_USERNAME=        IMAP_USERNAME=   (defaults to SMTP_USERNAME)
    SMTP_PASSWORD=        IMAP_PASSWORD=   (defaults to SMTP_PASSWORD)

Sends as the configured persona (name + email). IMAP polling fetches
UNSEEN inbox messages and marks them seen after retrieval.
"""

import asyncio
import email
import email.policy
import html
import imaplib
import logging
import re
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

import aiosmtplib

from harvey.integrations.mail_provider import (
    InboundMessage,
    MailProvider,
    SendResult,
    looks_like_bounce,
)

logger = logging.getLogger("harvey.smtp")


class SmtpImapProvider(MailProvider):
    name = "smtp"

    def __init__(self, config, env):
        self.config = config
        self.smtp_host = getattr(env, "smtp_host", "")
        self.smtp_port = int(getattr(env, "smtp_port", 587) or 587)
        self.smtp_user = getattr(env, "smtp_username", "")
        self.smtp_pass = getattr(env, "smtp_password", "")
        self.imap_host = getattr(env, "imap_host", "")
        self.imap_port = int(getattr(env, "imap_port", 993) or 993)
        self.imap_user = getattr(env, "imap_username", "")
        self.imap_pass = getattr(env, "imap_password", "")

    def is_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass)

    def is_imap_configured(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_pass)

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        thread_ref: str = "",
        in_reply_to: str = "",
    ) -> SendResult:
        persona = self.config.persona
        msg = EmailMessage()
        msg["To"] = to_email
        msg["From"] = f"{persona.name} <{persona.email or self.smtp_user}>"
        msg["Subject"] = subject
        message_id = make_msgid(domain=(persona.email or self.smtp_user).split("@")[-1])
        msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)

        # Build clean HTML email with Obrive branding & clickable links
        import html
        body_escaped = html.escape(body).replace("\n", "<br/>")
        body_html_content = re.sub(
            r"(https?://[^\s<]+)",
            r'<a href="\1" style="color: #4f46e5; font-weight: 500; text-decoration: underline;">\1</a>',
            body_escaped,
        )
        html_body = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b; background-color: #f8fafc; margin: 0; padding: 20px; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 28px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
  .header {{ border-bottom: 2px solid #f1f5f9; padding-bottom: 14px; margin-bottom: 20px; }}
  .content {{ font-size: 15px; color: #334155; line-height: 1.65; }}
  .footer {{ margin-top: 28px; padding-top: 14px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <a href="https://obrive.com" target="_blank" style="text-decoration: none; color: #0f172a; font-size: 20px; font-weight: bold; letter-spacing: -0.5px;">
      <span style="color: #4f46e5; font-weight: 800; font-size: 22px;">OBRIVE</span> <span style="font-size: 14px; color: #64748b; font-weight: 500;">| AR / VR / MR, Spatial Computing & 3D AI</span>
    </a>
  </div>
  <div class="content">
    {body_html_content}
  </div>
  <div class="footer">
    <div style="font-weight: 700; color: #1e293b; margin-bottom: 4px; font-size: 13px; letter-spacing: 0.5px;">OBRIVE INDUSTRIES</div>
    <div style="color: #64748b; font-size: 12px; line-height: 1.5; margin-bottom: 8px;">
      SREE GURURAYA MANSION, 3rd Floor, No 759, 8th Main Road<br/>
      KSRTC Layout, 3rd Phase, JP Nagar, Bengaluru, Karnataka – 560078
    </div>
    <div style="color: #475569; font-size: 12px; margin-bottom: 8px;">
      <strong>Connect Me :</strong> <a href="tel:+919886944447" style="color: #4f46e5; text-decoration: none;">+91 988-6944-447</a> &nbsp;&bull;&nbsp; 
      <strong>Connect Us :</strong> <a href="tel:+918884774300" style="color: #4f46e5; text-decoration: none;">+91 888-4774-300</a>
    </div>
    <div style="color: #475569; font-size: 12px;">
      <strong>Visit Us at :</strong> <a href="https://www.obrive.com" target="_blank" style="color: #4f46e5; text-decoration: none;">www.obrive.com</a> / <a href="https://www.obrive.in" target="_blank" style="color: #4f46e5; text-decoration: none;">www.obrive.in</a><br/>
      <strong>Our Brands :</strong> <a href="https://www.obnov.in" target="_blank" style="color: #4f46e5; text-decoration: none;">www.obnov.in</a> / <a href="https://www.obzor.in" target="_blank" style="color: #4f46e5; text-decoration: none;">www.obzor.in</a>
    </div>
  </div>
</div>
</body>
</html>"""
        msg.add_alternative(html_body, subtype="html")

        try:
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_pass,
                start_tls=(self.smtp_port == 587),
                use_tls=(self.smtp_port == 465),
                timeout=30,
            )
        except Exception as e:
            logger.error(f"SMTP send failed to {to_email}: {e}")
            return SendResult(ok=False, error=str(e)[:300])
        return SendResult(ok=True, message_id=message_id, thread_ref=message_id)

    async def get_replies(self, limit: int = 50) -> list[InboundMessage]:
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._fetch_unseen, limit)
        except Exception as e:
            logger.error(f"IMAP fetch failed: {e}")
            return []

    def _fetch_unseen(self, limit: int) -> list[InboundMessage]:
        if not self.is_imap_configured():
            logger.debug("IMAP not configured. Skipping reply check.")
            return []
        out: list[InboundMessage] = []
        try:
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=10)
            try:
                conn.login(self.imap_user, self.imap_pass)
                conn.select("INBOX")
                status, data = conn.search(None, "UNSEEN")
                if status != "OK":
                    return []
                ids = data[0].split()[-limit:]
                for msg_id in ids:
                    status, parts = conn.fetch(msg_id, "(RFC822)")
                    if status != "OK" or not parts or not isinstance(parts[0], tuple):
                        continue
                    parsed = self._parse_rfc822(parts[0][1])
                    if parsed:
                        out.append(parsed)
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"IMAP fetch error: {e}")
            return []
        return out

    @staticmethod
    def _parse_rfc822(raw: bytes) -> InboundMessage | None:
        try:
            msg = email.message_from_bytes(raw, policy=email.policy.default)
        except Exception:
            return None
        from_email = parseaddr(str(msg.get("From", "")))[1].lower()
        if not from_email:
            return None
        subject = str(msg.get("Subject", ""))

        body = ""
        try:
            part = msg.get_body(preferencelist=("plain", "html"))
            if part is not None:
                body = part.get_content()
                if part.get_content_type() == "text/html":
                    import re
                    body = re.sub(r"<[^>]+>", " ", body)
        except Exception:
            pass

        message_id = str(msg.get("Message-ID", "")).strip()
        return InboundMessage(
            provider_id=message_id or f"{from_email}:{subject}",
            from_email=from_email,
            subject=subject,
            body=(body or "")[:5000],
            message_id=message_id,
            in_reply_to=str(msg.get("In-Reply-To", "")).strip(),
            thread_ref=str(msg.get("In-Reply-To", "")).strip(),
            date=str(msg.get("Date", "")),
            is_bounce=looks_like_bounce(from_email, subject),
        )

    async def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD missing from .env"
        # SMTP handshake
        try:
            smtp = aiosmtplib.SMTP(
                hostname=self.smtp_host, port=self.smtp_port,
                start_tls=(self.smtp_port == 587),
                use_tls=(self.smtp_port == 465), timeout=15,
            )
            await smtp.connect()
            await smtp.login(self.smtp_user, self.smtp_pass)
            await smtp.quit()
        except Exception as e:
            return False, f"SMTP login failed: {str(e)[:200]}"
        # If IMAP is not configured, SMTP alone is completely fine for outbound sending!
        if not self.is_imap_configured():
            return True, f"SMTP relay connected as {self.smtp_user} (IMAP not configured; outbound sending active)"

        # IMAP handshake
        loop = asyncio.get_event_loop()

        def _imap_check():
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=10)
            try:
                conn.login(self.imap_user, self.imap_pass)
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

        try:
            await loop.run_in_executor(None, _imap_check)
        except Exception as e:
            return False, f"SMTP OK but IMAP login failed: {str(e)[:200]}"
        return True, f"SMTP + IMAP connected as {self.smtp_user}"
