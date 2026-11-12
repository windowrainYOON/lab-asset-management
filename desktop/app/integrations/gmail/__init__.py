"""Send mail as the signed-in Google account (Gmail API, gmail.send scope only)."""
from __future__ import annotations

import base64
from email.message import EmailMessage

from ..gdrive.drive import DriveError, GMAIL_SCOPE, load_credentials


class MailError(DriveError):
    pass


def can_send(creds=None) -> bool:
    creds = creds or load_credentials()
    return bool(creds and GMAIL_SCOPE in (creds.scopes or []))


def sender_address(creds=None) -> str:
    """The signed-in account's address ('' if unknown)."""
    creds = creds or load_credentials()
    if creds is None:
        return ""
    try:
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return svc.users().getProfile(userId="me").execute(num_retries=2).get("emailAddress", "")
    except Exception:  # noqa: BLE001
        return ""


def build_message(to: list[str], subject: str, html: str, text: str, reply_to: str = "") -> EmailMessage:
    """From is left out: Gmail sends as the signed-in account."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send(to: list[str], subject: str, html: str, text: str, reply_to: str = "", creds=None) -> str:
    """Send and return the Gmail message id."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    creds = creds or load_credentials()
    if not can_send(creds):
        raise MailError("메일을 보내려면 Google 계정으로 다시 로그인해 메일 보내기 권한을 허용해 주세요.")
    msg = build_message(to, subject, html, text, reply_to)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        res = svc.users().messages().send(userId="me", body={"raw": raw}).execute(num_retries=2)
    except HttpError as exc:
        status = getattr(exc.resp, "status", 0)
        if status == 403 and ("has not been used" in str(exc) or "disabled" in str(exc)):
            raise MailError("Google Cloud 프로젝트에서 Gmail API가 꺼져 있습니다. "
                            "Cloud Console → API 및 서비스 → 라이브러리에서 ‘Gmail API’를 사용 설정해 주세요.") from exc
        raise MailError(f"메일을 보내지 못했습니다 ({status}): {exc}") from exc
    except OSError as exc:
        raise MailError(f"메일 서버에 연결하지 못했습니다: {exc}") from exc
    return res.get("id", "")
