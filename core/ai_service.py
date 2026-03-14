"""AI summary module using OpenRouter API.

Runs as a QThread to safely emit signals back to the UI.
Tracks token usage (input/output) with daily and total counters.
Includes retry logic for SSL errors common on ship networks.
"""

import json
import socket
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_SYSTEM_PROMPT = (
    "You are a concise technical assistant for a ship's Chief Engineer. "
    "Summarize marine engineering projects clearly and concisely."
)


def check_internet(host: str = "openrouter.ai", port: int = 443,
                   timeout: float = 3.0) -> bool:
    """Lightweight connectivity check."""
    try:
        socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False


# ── Token Usage Tracking ──────────────────────────────────


def _get_usage_path() -> Path:
    return Path.home() / "ShipLog" / "data" / "token_usage.json"


def load_token_usage() -> dict:
    path = _get_usage_path()
    default = {
        "total_in": 0, "total_out": 0,
        "daily_in": 0, "daily_out": 0,
        "daily_date": date.today().isoformat(),
    }
    if path.exists():
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if data.get("daily_date") != date.today().isoformat():
                data["daily_in"] = 0
                data["daily_out"] = 0
                data["daily_date"] = date.today().isoformat()
            return data
        except Exception:
            pass
    return default


def save_token_usage(usage: dict) -> None:
    path = _get_usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(usage, f, indent=2)


def add_token_usage(prompt_tokens: int, completion_tokens: int) -> dict:
    usage = load_token_usage()
    usage["total_in"] += prompt_tokens
    usage["total_out"] += completion_tokens
    usage["daily_in"] += prompt_tokens
    usage["daily_out"] += completion_tokens
    save_token_usage(usage)
    return usage


def reset_token_usage() -> dict:
    usage = {
        "total_in": 0, "total_out": 0,
        "daily_in": 0, "daily_out": 0,
        "daily_date": date.today().isoformat(),
    }
    save_token_usage(usage)
    return usage


MAX_RETRIES = 3
RETRY_DELAYS = [3, 6, 12]  # seconds between retries


def _is_ssl_error(exc: Exception) -> bool:
    """Check if exception is an SSL-related error worth retrying."""
    err_str = str(exc).lower()
    return any(kw in err_str for kw in [
        "ssl", "eof occurred", "unexpected_eof",
        "connection reset", "connection aborted",
    ])


def _post_with_retry(url: str, headers: dict, json_data: dict,
                     timeout: int = 30) -> dict:
    """Make an HTTP POST with retry logic for SSL/connection errors."""
    import requests

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                url, headers=headers, json=json_data, timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            last_exc = e
            if _is_ssl_error(e) and attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "[AI] SSL/connection error (attempt %d/%d), "
                    "retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES, delay, e,
                )
                time.sleep(delay)
            else:
                raise
    raise last_exc  # unreachable, but satisfies type checkers


def _extract_content(data: dict) -> str:
    """Extract text content from OpenRouter API response.

    Different models place the response in different fields:
    - choices[0].message.content (standard)
    - choices[0].message.reasoning (minimax and similar reasoning models)
    - choices[0].message.reasoning_content (some reasoning models)
    - choices[0].text (legacy completions)

    Reasoning models may use all max_tokens on internal reasoning and
    leave content empty. In that case we fall back to the reasoning text.
    """
    try:
        choice = data["choices"][0]
        msg = choice.get("message", {})

        # Standard chat completion content (preferred)
        content = msg.get("content")
        if content:
            return str(content)

        # Reasoning models: minimax uses "reasoning" field
        reasoning = msg.get("reasoning")
        if reasoning:
            logger.info("[AI] Using 'reasoning' field as content (model used reasoning mode)")
            return str(reasoning)

        # Other reasoning models use "reasoning_content"
        reasoning_content = msg.get("reasoning_content")
        if reasoning_content:
            logger.info("[AI] Using 'reasoning_content' field as content")
            return str(reasoning_content)

        # Legacy completion format
        text = choice.get("text")
        if text:
            return str(text)

        # Log the full structure for debugging
        logger.warning("[AI] No content found in response. "
                       "Message keys: %s, Choice keys: %s",
                       list(msg.keys()), list(choice.keys()))
        logger.warning("[AI] Full message object: %s",
                       json.dumps(msg, default=str)[:500])
        return ""
    except (KeyError, IndexError, TypeError) as e:
        logger.error("[AI] Failed to extract content: %s, data=%s",
                     e, json.dumps(data, default=str)[:500])
        return ""


def _clean_markdown(text: str) -> str:
    """Strip common markdown formatting from AI output."""
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\)\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^-\s+', '', text, flags=re.MULTILINE)
    return text.strip()


# ── Workers ───────────────────────────────────────────────


class AITestWorker(QThread):
    """Background thread to test API connection with a simple prompt."""

    test_result = pyqtSignal(bool, str)

    def __init__(self, api_key: str, model: str, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.model = model

    def run(self):
        if not check_internet():
            self.test_result.emit(False, "No internet connection to openrouter.ai")
            return
        try:
            import requests
            logger.info("[AI TEST] Sending test request to model: %s", self.model)
            response = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "Reply with: OK"}],
                    "max_tokens": 10,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            reply = _extract_content(data).strip()
            usage = data.get("usage", {})
            logger.info("[AI TEST] Response: '%s' | tokens in=%d out=%d",
                        reply, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            add_token_usage(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
            self.test_result.emit(True, f"Model responded: \"{reply}\"")
        except Exception as e:
            logger.exception("[AI TEST] Failed")
            self.test_result.emit(False, str(e))


class AISummaryWorker(QThread):
    """Background thread that generates an AI summary for a single project."""

    summary_ready = pyqtSignal(int, str, str)  # project_id, short_summary, full_summary
    error_occurred = pyqtSignal(int, str)

    def __init__(self, project_id: int, project_text: str,
                 api_key: str, model: str, system_prompt: str = "",
                 parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.project_text = project_text
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    def run(self):
        if not check_internet():
            self.error_occurred.emit(self.project_id, "No internet connection")
            return

        try:
            prompt = (
                "Based on the project data below, provide TWO summaries.\n"
                "Line 1 — SHORT: one sentence, max 25 words (start, problem, what's needed).\n"
                "Line 2 — empty line.\n"
                "Lines 3+ — DETAILED: 2-4 sentences with full context.\n"
                "Do NOT use markdown, bullets, or numbering. Plain text only.\n\n"
                f"{self.project_text}"
            )

            logger.info("[AI SUMMARY] Requesting summary for project %d, model=%s",
                        self.project_id, self.model)
            logger.info("[AI SUMMARY] Prompt length: %d chars", len(prompt))

            data = _post_with_retry(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_data={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1000,
                },
                timeout=60,
            )
            raw_text = _extract_content(data)

            usage = data.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            add_token_usage(tokens_in, tokens_out)

            logger.info("[AI SUMMARY] Raw response (%d chars, tokens in=%d out=%d):\n%s",
                        len(raw_text), tokens_in, tokens_out, raw_text)

            if not raw_text.strip():
                self.error_occurred.emit(
                    self.project_id,
                    "AI returned empty response. Try a different model."
                )
                return

            cleaned = _clean_markdown(raw_text)

            # Try to split on empty line
            parts = re.split(r'\n\s*\n', cleaned, maxsplit=1)
            if len(parts) >= 2:
                short = parts[0].strip()
                detailed = parts[1].strip()
            else:
                # Fallback: first sentence vs rest
                sentences = cleaned.split(". ")
                if len(sentences) > 1:
                    short = sentences[0].strip().rstrip(".") + "."
                    detailed = cleaned
                else:
                    short = cleaned[:120] + ("..." if len(cleaned) > 120 else "")
                    detailed = cleaned

            if len(short) > 150:
                short = short[:147] + "..."

            logger.info("[AI SUMMARY] Parsed short: '%s'", short)
            logger.info("[AI SUMMARY] Parsed detailed (%d chars): '%s'",
                        len(detailed), detailed[:200])

            self.summary_ready.emit(self.project_id, short, detailed)

        except Exception as e:
            logger.exception("[AI SUMMARY] Failed for project %d", self.project_id)
            self.error_occurred.emit(self.project_id, str(e))


class AIDigestWorker(QThread):
    """Background thread that generates a weekly digest across all projects."""

    digest_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, all_projects_text: str, api_key: str, model: str,
                 system_prompt: str = "", parent=None):
        super().__init__(parent)
        self.all_projects_text = all_projects_text
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    def run(self):
        if not check_internet():
            self.error_occurred.emit("No internet connection")
            return

        try:
            prompt = (
                "Generate a weekly digest for a ship's Chief Engineer. "
                "Summarize what was done this week across all projects, "
                "and what's blocking each open project. Plain text, no markdown.\n\n"
                f"{self.all_projects_text}"
            )

            logger.info("[AI DIGEST] Requesting digest, model=%s, text length=%d",
                        self.model, len(prompt))

            data = _post_with_retry(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_data={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 800,
                },
                timeout=60,
            )
            raw = _extract_content(data)
            digest = _clean_markdown(raw)

            usage = data.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            add_token_usage(tokens_in, tokens_out)

            logger.info("[AI DIGEST] Response (%d chars, tokens in=%d out=%d):\n%s",
                        len(digest), tokens_in, tokens_out, digest[:500])

            if not digest:
                self.error_occurred.emit(
                    "AI returned empty response. Try a different model."
                )
                return

            self.digest_ready.emit(digest)

        except Exception as e:
            logger.exception("[AI DIGEST] Failed")
            self.error_occurred.emit(str(e))
