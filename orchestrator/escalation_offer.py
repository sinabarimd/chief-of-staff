"""Escalation offer state — tracks when the LLM offered to escalate and
handles the user's follow-up response.

Flow:
1. LLM says "want me to look into it?" → offer stored
2. Next utterance from same satellite is routed here:
   - "yes" / "yeah" / "sure" → voice_query (sync research/reasoning)
   - "no" / "nah" / "I'll handle it" → drop it, respond "okay"
   - "check online" / "search for it" → voice_query with mode=research
   - "nevermind" / "forget it" → drop it, respond "no problem"
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass
class PendingOffer:
    """A pending escalation offer waiting for user decision."""
    original_utterance: str
    offered_at: float
    satellite_id: str


# Per-satellite pending offers
_pending: dict[str, PendingOffer] = {}

# How long an offer stays valid (seconds)
OFFER_TTL_S = 60.0

# Response keywords
_YES_KEYWORDS = ["yes", "yeah", "sure", "go ahead", "please", "do it", "yep", "ok"]
_NO_KEYWORDS = ["no", "nah", "don't", "i'll handle it", "i got it", "no thanks"]
_SEARCH_KEYWORDS = ["check online", "search", "look it up", "google it", "web search"]
_CANCEL_KEYWORDS = ["nevermind", "never mind", "forget it", "cancel", "nope"]


def store_offer(satellite_id: str, original_utterance: str) -> None:
    """Store a pending escalation offer for this satellite."""
    _pending[satellite_id] = PendingOffer(
        original_utterance=original_utterance,
        offered_at=time.monotonic(),
        satellite_id=satellite_id,
    )
    _LOGGER.info("[%s] Escalation offer stored: '%s'", satellite_id, original_utterance[:60])


def has_pending_offer(satellite_id: str) -> bool:
    """Check if there's a valid pending offer for this satellite."""
    offer = _pending.get(satellite_id)
    if offer is None:
        return False
    if (time.monotonic() - offer.offered_at) > OFFER_TTL_S:
        del _pending[satellite_id]
        return False
    return True


def get_pending_offer(satellite_id: str) -> PendingOffer | None:
    """Get and clear the pending offer."""
    offer = _pending.pop(satellite_id, None)
    if offer and (time.monotonic() - offer.offered_at) > OFFER_TTL_S:
        return None
    return offer


def classify_response(utterance: str) -> str:
    """Classify the user's response to an escalation offer.

    Returns: 'escalate', 'no', 'search', 'cancel', or 'unknown'
    """
    text = utterance.lower().strip()

    for kw in _CANCEL_KEYWORDS:
        if kw in text:
            return "cancel"

    for kw in _SEARCH_KEYWORDS:
        if kw in text:
            return "search"

    for kw in _NO_KEYWORDS:
        if kw in text:
            return "no"

    for kw in _YES_KEYWORDS:
        if kw in text:
            return "escalate"

    return "unknown"
