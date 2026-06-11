"""vLLM streaming client with tool-calling support.

Provides stream_completion() for token-by-token streaming and
call_with_tools() for tool-calling interactions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .config import Settings

_LOGGER = logging.getLogger(__name__)

# Tool definitions for voice escalation and sync queries
_ESCALATE_TOOL = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": "Hand off a PROJECT task to Claude for async processing (drafts, write-ups, project work). Response comes later.",
        "parameters": {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "properties": {
                        "utterance": {
                            "type": "string",
                            "description": "The user's request verbatim",
                        },
                        "context": {
                            "type": "string",
                            "description": "Any relevant context",
                        },
                    },
                    "required": ["utterance"],
                },
            },
            "required": ["payload"],
        },
    },
}

_VOICE_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "voice_query",
        "description": "Ask Claude a question and get an answer NOW. Use for research (web search needed) or reasoning (decision help, explanations). User will hear the answer immediately.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The question to research or reason about",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "research", "reasoning"],
                    "description": "auto: let the system decide. research: needs web search. reasoning: pure thinking, no search needed.",
                },
            },
            "required": ["query"],
        },
    },
}

VOICE_TOOLS = [_ESCALATE_TOOL, _VOICE_QUERY_TOOL]


@dataclass
class ToolCall:
    """Parsed tool call from LLM response."""
    name: str
    arguments: dict


@dataclass
class LLMResult:
    """Result from an LLM call — either text content or a tool call."""
    content: str | None = None
    tool_call: ToolCall | None = None

    @property
    def is_tool_call(self) -> bool:
        return self.tool_call is not None


async def stream_completion(
    messages: list[dict], settings: Settings
) -> AsyncIterator[str]:
    """Stream tokens from vLLM via SSE. Plain text path only (no tools)."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{settings.vllm_url}/chat/completions",
            json={
                "model": settings.vllm_model,
                "messages": messages,
                "max_tokens": 256,
                "temperature": 0.7,
                "stream": True,
            },
            timeout=aiohttp.ClientTimeout(
                total=30, sock_read=settings.llm_timeout_s
            ),
        ) as resp:
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


# Keywords that signal sync query (research/reasoning — answered immediately)
_SYNC_KEYWORDS = [
    "research", "look into", "find out", "look up", "search for",
    "what is", "what are", "who is", "how does", "how do",
    "compare", "difference between", "should i", "which is better",
    "explain", "help me decide", "help me understand",
]

# Keywords that signal async escalation (project tasks — queued for later)
_ASYNC_KEYWORDS = [
    "draft", "write up", "prepare", "compose",
    "have claude", "ask claude", "send to claude",
]

# Combined for tool offering check
_ESCALATE_KEYWORDS = _SYNC_KEYWORDS + _ASYNC_KEYWORDS


def _should_offer_tools(messages: list[dict]) -> bool:
    """Check if the latest user message contains escalation keywords."""
    for msg in reversed(messages):
        if msg["role"] == "user":
            text = msg["content"].lower()
            return any(kw in text for kw in _ESCALATE_KEYWORDS)
    return False


async def call_with_tools(
    messages: list[dict], settings: Settings
) -> LLMResult:
    """Non-streaming call. Only offers tools if escalation keywords detected.

    This prevents the 8B from over-eagerly escalating every request.
    """
    use_tools = _should_offer_tools(messages)

    body = {
        "model": settings.vllm_model,
        "messages": messages,
        "max_tokens": 128,
        "temperature": 0.3 if use_tools else 0.7,
    }

    if use_tools:
        body["tools"] = VOICE_TOOLS
        body["tool_choice"] = "auto"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{settings.vllm_url}/chat/completions",
            json=body,
            timeout=aiohttp.ClientTimeout(total=max(settings.llm_timeout_s, 10.0)),
        ) as resp:
            data = await resp.json()

    if "error" in data:
        _LOGGER.error("vLLM error: %s", data["error"])
        return LLMResult(content="Sorry, I'm having trouble thinking right now.")

    if "choices" not in data or not data["choices"]:
        _LOGGER.error("vLLM unexpected response: %s", str(data)[:200])
        return LLMResult(content="Sorry, I'm having trouble thinking right now.")

    choice = data["choices"][0]
    message = choice["message"]

    # Known tool names — only these are real tool calls
    KNOWN_TOOLS = {"escalate", "voice_query"}

    # Check for tool calls
    tool_calls = message.get("tool_calls")
    if tool_calls:
        tc = tool_calls[0]  # take first tool call
        fn_name = tc.get("function", {}).get("name", "")
        try:
            args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            _LOGGER.warning("Failed to parse tool call arguments: %s", tc)
            return LLMResult(content=message.get("content", ""))

        if fn_name in KNOWN_TOOLS:
            _LOGGER.info(
                "Tool call: %s(%s)", fn_name, json.dumps(args, indent=None)
            )
            return LLMResult(
                tool_call=ToolCall(name=fn_name, arguments=args)
            )

        # Hallucinated tool call — 8B models sometimes invent tools.
        # Try to extract text from the arguments.
        _LOGGER.warning("Unknown tool call '%s', treating as text", fn_name)
        text = args.get("text") or args.get("response") or args.get("message")
        if text:
            return LLMResult(content=text)
        # Last resort: return whatever content field exists
        return LLMResult(content=message.get("content") or str(args))

    # Plain text response — but check if model emitted a tool call as text
    content = message.get("content", "")
    if content and content.strip().startswith("{") and "escalate" in content:
        try:
            parsed = json.loads(content)
            name = parsed.get("name", "")
            params = parsed.get("parameters", {})
            if name in KNOWN_TOOLS:
                _LOGGER.info("Parsed tool call from text: %s", name)
                return LLMResult(
                    tool_call=ToolCall(name=name, arguments=params)
                )
        except json.JSONDecodeError:
            pass  # not valid JSON, treat as text

    return LLMResult(content=content)
