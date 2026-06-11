"""Conversation session management."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 20  # 10 turns


@dataclass
class ConversationSession:
    satellite_id: str
    system_prompt: str
    messages: list[dict] = field(default_factory=list)
    last_activity: float = field(default_factory=time.monotonic)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        # Keep history bounded
        if len(self.messages) > MAX_HISTORY_MESSAGES:
            self.messages = self.messages[-MAX_HISTORY_MESSAGES:]
        self.last_activity = time.monotonic()

    def get_messages(self) -> list[dict]:
        """Return full message list with system prompt + current time prepended."""
        from datetime import datetime, timezone, timedelta
        pacific = timezone(timedelta(hours=-7))  # PDT (UTC-7)
        now = datetime.now(pacific).strftime("%A, %B %d, %Y at %I:%M %p")
        system_content = f"{self.system_prompt}\n\nCurrent date and time: {now}"
        return [{"role": "system", "content": system_content}] + self.messages

    def is_expired(self, timeout_s: float) -> bool:
        return (time.monotonic() - self.last_activity) > timeout_s

    def should_continue(self) -> bool:
        """True if last assistant message ends with a question."""
        if not self.messages:
            return False
        last = self.messages[-1]
        return last["role"] == "assistant" and last["content"].rstrip().endswith("?")


class SessionManager:
    def __init__(self, system_prompt: str, timeout_s: float = 120.0):
        self._sessions: dict[str, ConversationSession] = {}
        self._system_prompt = system_prompt
        self._timeout_s = timeout_s

    def get_or_create(self, satellite_id: str) -> ConversationSession:
        session = self._sessions.get(satellite_id)
        if session is None or session.is_expired(self._timeout_s):
            if session is not None:
                _LOGGER.info("[%s] Session expired, starting fresh", satellite_id)
            session = ConversationSession(
                satellite_id=satellite_id,
                system_prompt=self._system_prompt,
            )
            self._sessions[satellite_id] = session
        session.last_activity = time.monotonic()
        return session

    async def cleanup_loop(self) -> None:
        """Periodically remove expired sessions."""
        while True:
            await asyncio.sleep(30)
            expired = [
                sid
                for sid, s in self._sessions.items()
                if s.is_expired(self._timeout_s)
            ]
            for sid in expired:
                _LOGGER.info("[%s] Cleaning up expired session", sid)
                del self._sessions[sid]
