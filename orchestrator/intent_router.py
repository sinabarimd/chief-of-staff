"""Intent router — dispatches tool calls from the LLM to handlers.

Supports:
- escalate → POST to local n8n webhook → async mailbox (project tasks)
- voice_query → POST to cloud n8n webhook → sync response (research/reasoning)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .config import Settings
    from .llm import ToolCall

_LOGGER = logging.getLogger(__name__)

# Default acks
_FALLBACK_ACK = "I'll pass that along, but I'm having trouble reaching the task system right now."
_FALLBACK_QUERY = "Sorry, I couldn't reach the research service right now. Want me to queue that as a task instead?"
_INTERIM_ACK = "Let me look into that."


async def handle_tool_call(
    tool_call: ToolCall, settings: Settings, tts_callback=None
) -> str:
    """Dispatch a tool call and return a TTS-ready response string.

    Args:
        tts_callback: Optional async callable(str) to send an interim
            TTS message before the sync response arrives.
    """
    if tool_call.name == "escalate":
        return await _handle_escalate(tool_call, settings)
    if tool_call.name == "voice_query":
        return await _handle_voice_query(tool_call, settings, tts_callback)

    _LOGGER.warning("Unknown tool call: %s", tool_call.name)
    return "I'm not sure how to handle that request."


async def _handle_escalate(tool_call: ToolCall, settings: Settings) -> str:
    """POST escalation to local n8n webhook, return the ack message for TTS."""
    args = tool_call.arguments
    payload = args.get("payload", {})

    body = {
        "payload": payload,
        "source_agent": "voice",
        "idempotency_key": str(uuid.uuid4()),
        "depth": 0,
        "parent_message_id": None,
    }

    _LOGGER.info(
        "Escalating (async): %s",
        payload.get("utterance", "unknown")[:80],
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                settings.n8n_webhook_url,
                json=body,
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ack = data.get("ack_message", "On it, I'll have that for you in a bit.")
                    job_id = data.get("job_id", "unknown")
                    _LOGGER.info("Escalation accepted: job_id=%s", job_id)
                    return ack
                else:
                    text = await resp.text()
                    _LOGGER.error("n8n returned %d: %s", resp.status, text[:200])
                    return _FALLBACK_ACK
    except Exception:
        _LOGGER.exception("Failed to reach local n8n webhook")
        return _FALLBACK_ACK


async def _handle_voice_query(
    tool_call: ToolCall, settings: Settings, tts_callback=None
) -> str:
    """POST query to cloud n8n webhook, wait for sync response."""
    args = tool_call.arguments
    query = args.get("query", "")
    mode = args.get("mode", "auto")

    if not query:
        return "I didn't catch what you wanted me to look into."

    if not settings.cloud_n8n_key:
        _LOGGER.error("VOICEHUB_CLOUD_N8N_KEY not set — cannot use voice_query")
        return _FALLBACK_QUERY

    _LOGGER.info("Voice query (sync, mode=%s): %s", mode, query[:80])

    # Send interim "let me look into that" so user knows we heard them
    if tts_callback:
        try:
            await tts_callback(_INTERIM_ACK)
        except Exception:
            _LOGGER.debug("Interim TTS callback failed", exc_info=True)

    body = {
        "query": query,
        "mode": mode,
        "session_id": None,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Voice-Key": settings.cloud_n8n_key,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                settings.cloud_n8n_url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    answer = data.get("answer", "")
                    resp_mode = data.get("mode", mode)
                    _LOGGER.info(
                        "Voice query answered (mode=%s, %d chars)",
                        resp_mode, len(answer),
                    )
                    return answer or "I got a response but it was empty. Try asking again?"
                elif resp.status == 429:
                    _LOGGER.warning("Cloud n8n rate limited")
                    return "I'm being rate limited on research right now. Try again in a minute."
                else:
                    text = await resp.text()
                    _LOGGER.error("Cloud n8n returned %d: %s", resp.status, text[:200])
                    return _FALLBACK_QUERY
    except aiohttp.ClientError:
        _LOGGER.exception("Failed to reach cloud n8n")
        return _FALLBACK_QUERY
    except TimeoutError:
        _LOGGER.warning("Cloud n8n query timed out after 30s")
        return "That research is taking too long. Want me to queue it as a task instead?"
