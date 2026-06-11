"""Web search via Tavily API.

Handles factual queries that the 8B would hallucinate on.
Returns TTS-ready answer strings.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .config import Settings

_LOGGER = logging.getLogger(__name__)

TAVILY_API_KEY = "YOUR_TAVILY_KEY_HERE"
TAVILY_URL = "https://api.tavily.com/search"

# Keywords that always trigger web search
_SEARCH_KEYWORDS = [
    "search for", "look up", "google", "look online",
    "check the web", "check online", "search online",
    "what's the weather", "weather today", "weather tomorrow",
    "what's the news", "latest news",
]

# Factual question patterns that the 8B can't reliably answer
_FACTUAL_PATTERNS = [
    "what is the", "what is a", "what are the",
    "who is", "who was", "who are",
    "when did", "when was", "when is",
    "where is", "where was", "where are",
    "how many", "how much does", "how far",
    "what year", "what country", "what city",
    "define ", "meaning of",
]

# Don't search for these — the 8B or local context handles them
_SKIP_PATTERNS = [
    "what time", "what's the time", "what day",
    "what's on my", "what's in my",
    "check my", "check the inbox",
]


def should_search(utterance: str) -> bool:
    """Determine if this utterance should go to web search."""
    text = utterance.lower().strip()

    # Skip patterns take priority
    if any(p in text for p in _SKIP_PATTERNS):
        return False

    # Explicit search keywords
    if any(kw in text for kw in _SEARCH_KEYWORDS):
        return True

    # Factual question patterns
    if any(p in text for p in _FACTUAL_PATTERNS):
        return True

    return False


async def search(query: str) -> str:
    """Run a Tavily search and return a TTS-ready answer."""
    _LOGGER.info("Web search: '%s'", query[:80])

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                TAVILY_URL,
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 3,
                },
                timeout=aiohttp.ClientTimeout(total=8.0),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Tavily returned %d", resp.status)
                    return "Sorry, I couldn't search for that right now."

                data = await resp.json()

        # Tavily returns an 'answer' field with a concise summary
        answer = data.get("answer")
        if answer:
            _LOGGER.info("Tavily answer: '%s'", answer[:100])
            return answer

        # Fallback: use first result snippet
        results = data.get("results", [])
        if results:
            snippet = results[0].get("content", "")[:200]
            return snippet if snippet else "I found some results but couldn't extract a clear answer."

        return "I searched but didn't find a clear answer for that."

    except Exception:
        _LOGGER.exception("Web search failed")
        return "Sorry, I'm having trouble searching right now."
