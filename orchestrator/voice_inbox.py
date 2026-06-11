"""Voice inbox — interactive message reader.

Messages arrive at /var/voicehub/mailboxes/voice-inbox/ as .md files.
The body (after frontmatter) is TTS-ready text spoken verbatim.

Supports multi-turn interaction:
- "check inbox" → count + summaries
- "read the first/latest/next" → reads body
- "mark as read" / "next" / "done" → state transitions
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

VOICE_INBOX_PATH = Path("/var/voicehub/mailboxes/voice-inbox")
PROCESSED_PATH = VOICE_INBOX_PATH / "processed"

# Keywords that trigger inbox mode
INBOX_KEYWORDS = [
    "check inbox",
    "check my inbox",
    "check messages",
    "any messages",
    "check the inbox",
    "read my messages",
    "what's in my inbox",
    "do i have messages",
    "do i have any messages",
]

# Regex to split frontmatter from body
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class InboxMessage:
    filepath: Path
    message_id: str
    from_agent: str
    body: str
    subject: str  # first sentence or first line of body


@dataclass
class InboxSession:
    """Tracks state for an interactive inbox session."""
    messages: list[InboxMessage] = field(default_factory=list)
    current_index: int = -1  # -1 = haven't read any yet
    active: bool = False


# Per-satellite inbox sessions
_sessions: dict[str, InboxSession] = {}


def get_session(satellite_id: str) -> InboxSession:
    return _sessions.get(satellite_id, InboxSession())


def _parse_message(filepath: Path) -> InboxMessage | None:
    """Parse a mailbox message file."""
    try:
        content = filepath.read_text()
    except OSError:
        return None

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None

    meta = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()

    body = content[match.end():].strip()
    if not body:
        return None

    # Extract subject: first sentence or first 60 chars
    first_sentence = re.split(r'[.!?]', body)[0].strip()
    subject = first_sentence[:60] if first_sentence else body[:60]

    return InboxMessage(
        filepath=filepath,
        message_id=meta.get("message_id", filepath.stem),
        from_agent=meta.get("from", "unknown"),
        body=body,
        subject=subject,
    )


def _load_messages() -> list[InboxMessage]:
    """Load all unread messages from inbox, sorted newest first."""
    if not VOICE_INBOX_PATH.exists():
        return []
    files = sorted(VOICE_INBOX_PATH.glob("*.md"), reverse=True)  # newest first
    messages = []
    for f in files:
        msg = _parse_message(f)
        if msg:
            messages.append(msg)
    return messages


async def handle_inbox_command(satellite_id: str, utterance: str) -> str:
    """Process an inbox command. Returns TTS-ready response.

    Call this for the initial "check inbox" and for follow-up commands
    during the multi-turn inbox session.
    """
    text = utterance.lower().strip()
    session = _sessions.get(satellite_id)

    # Initial check / refresh
    if any(kw in text for kw in INBOX_KEYWORDS) or session is None or not session.active:
        messages = _load_messages()
        session = InboxSession(messages=messages, active=True)
        _sessions[satellite_id] = session

        if not messages:
            session.active = False
            return "Your inbox is empty."
        elif len(messages) == 1:
            return (
                f"You have one new message from {messages[0].from_agent}. "
                f"It says: {messages[0].subject}. "
                f"Would you like me to read it?"
            )
        else:
            summaries = []
            for i, msg in enumerate(messages, 1):
                summaries.append(f"{i}. From {msg.from_agent}: {msg.subject}")
            summary_text = ". ".join(summaries)
            return (
                f"You have {len(messages)} new messages. "
                f"{summary_text}. "
                f"Which one would you like me to read?"
            )

    # Session is active — interpret follow-up commands
    if not session.messages:
        session.active = False
        return "No more messages."

    # Read commands
    if any(w in text for w in ["read it", "yes", "read the first", "read one", "read the latest", "read the most recent", "go ahead"]):
        session.current_index = 0
        msg = session.messages[0]
        remaining = len(session.messages) - 1
        suffix = f" That's the end of that message. {remaining} more remaining. Next?" if remaining > 0 else " That's all your messages."
        return f"From {msg.from_agent}. {msg.body}{suffix}"

    if any(w in text for w in ["read the second", "number 2", "read two"]):
        if len(session.messages) > 1:
            session.current_index = 1
            msg = session.messages[1]
            return f"From {msg.from_agent}. {msg.body} Want me to continue?"
        return "There's no second message."

    if any(w in text for w in ["next", "read the next", "next one", "continue"]):
        session.current_index += 1
        if session.current_index >= len(session.messages):
            session.active = False
            return "No more messages."
        msg = session.messages[session.current_index]
        remaining = len(session.messages) - session.current_index - 1
        suffix = f" {remaining} more. Next?" if remaining > 0 else " That's all your messages."
        return f"From {msg.from_agent}. {msg.body}{suffix}"

    # Mark as read
    if any(w in text for w in ["mark as read", "mark it read", "mark read", "archive", "done with it", "got it"]):
        if session.current_index >= 0 and session.current_index < len(session.messages):
            msg = session.messages[session.current_index]
            PROCESSED_PATH.mkdir(parents=True, exist_ok=True)
            try:
                msg.filepath.rename(PROCESSED_PATH / msg.filepath.name)
                _LOGGER.info("Marked as read: %s from %s", msg.message_id, msg.from_agent)
            except OSError:
                pass
            session.messages.pop(session.current_index)
            if session.current_index >= len(session.messages):
                session.current_index = len(session.messages) - 1
            remaining = len(session.messages)
            if remaining > 0:
                return f"Marked as read. {remaining} message{'s' if remaining > 1 else ''} left. Want me to read the next?"
            else:
                session.active = False
                return "Marked as read. No more messages."
        return "Nothing to mark. Would you like me to read a message first?"

    # Mark all as read
    if any(w in text for w in ["mark all read", "mark all as read", "clear inbox", "archive all"]):
        PROCESSED_PATH.mkdir(parents=True, exist_ok=True)
        count = 0
        for msg in session.messages:
            try:
                msg.filepath.rename(PROCESSED_PATH / msg.filepath.name)
                count += 1
            except OSError:
                pass
        session.messages.clear()
        session.active = False
        _LOGGER.info("Marked all %d messages as read", count)
        return f"Done, marked {count} message{'s' if count != 1 else ''} as read."

    # Exit inbox
    if any(w in text for w in ["no", "done", "that's all", "never mind", "exit", "stop"]):
        session.active = False
        return "Okay."

    # Didn't understand
    return "I can read a message, go to the next, or mark as read. What would you like?"


def is_inbox_active(satellite_id: str) -> bool:
    """Check if this satellite has an active inbox session."""
    session = _sessions.get(satellite_id)
    return session is not None and session.active
