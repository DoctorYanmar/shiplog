"""Email parsing for .msg (Outlook) and .eml files.

Uses extract-msg for .msg files and the email stdlib for .eml files.
"""

import logging
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def parse_msg(file_path: str) -> Optional[dict]:
    """Parse an Outlook .msg file using extract-msg.

    Returns dict with keys: sender, subject, date, body_preview, body_full
    or None on failure.
    """
    try:
        import extract_msg
        msg = extract_msg.Message(file_path)
        full_body = msg.body or ""
        result = {
            "sender": msg.sender or "",
            "subject": msg.subject or "",
            "date": str(msg.date) if msg.date else "",
            "body_preview": _truncate(full_body),
            "body_full": full_body,
        }
        msg.close()
        return result
    except ImportError:
        logger.warning("extract-msg not installed; .msg parsing unavailable")
        return None
    except Exception:
        logger.exception("Failed to parse .msg file: %s", file_path)
        return None


def parse_eml(file_path: str) -> Optional[dict]:
    """Parse an .eml file using Python's email stdlib.

    Returns dict with keys: sender, subject, date, body_preview, body_full
    or None on failure.
    """
    try:
        path = Path(file_path)
        with open(path, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)

        body = ""
        if msg.get_body(preferencelist=("plain",)):
            body = msg.get_body(preferencelist=("plain",)).get_content()
        elif msg.get_body(preferencelist=("html",)):
            body = msg.get_body(preferencelist=("html",)).get_content()

        return {
            "sender": str(msg.get("From", "")),
            "subject": str(msg.get("Subject", "")),
            "date": str(msg.get("Date", "")),
            "body_preview": _truncate(body),
            "body_full": body,
        }
    except Exception:
        logger.exception("Failed to parse .eml file: %s", file_path)
        return None


def parse_email(file_path: str) -> Optional[dict]:
    """Auto-detect email type and parse accordingly."""
    ext = Path(file_path).suffix.lower()
    if ext == ".msg":
        return parse_msg(file_path)
    elif ext == ".eml":
        return parse_eml(file_path)
    else:
        logger.warning("Unsupported email format: %s", ext)
        return None
