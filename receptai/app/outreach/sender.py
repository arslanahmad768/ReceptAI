"""Email delivery via Resend API."""
from dataclasses import dataclass
import resend
from app.config import settings

resend.api_key = settings.resend_api_key


@dataclass
class SendResult:
    success: bool
    message_id: str | None
    error: str | None = None


def _text_to_html(text: str) -> str:
    """Convert plain-text email body to simple HTML (preserves line breaks)."""
    paragraphs = text.strip().split("\n\n")
    html_parts = []
    for p in paragraphs:
        lines = p.replace("\n", "<br>")
        html_parts.append(f"<p>{lines}</p>")
    return (
        "<html><body style='font-family:sans-serif;font-size:15px;"
        "line-height:1.6;color:#222;max-width:600px;margin:0 auto;padding:20px'>"
        + "".join(html_parts)
        + "</body></html>"
    )


def send_email(to: str, subject: str, body: str) -> SendResult:
    """
    Send a single email via Resend.
    `body` is plain text — converted to HTML automatically.
    Returns SendResult(success, message_id, error).
    """
    try:
        params = {
            "from": f"{settings.from_name} <{settings.from_email}>",
            "to": [to],
            "subject": subject,
            "html": _text_to_html(body),
            "text": body,
        }
        email = resend.Emails.send(params)
        return SendResult(success=True, message_id=email.id)
    except Exception as e:
        return SendResult(success=False, message_id=None, error=str(e))
