"""WhisperLive STT client — direct WebSocket connection.

Sends float32 audio to WhisperLive TRT and receives transcription.
Uses stabilize-on-repeat logic for TRT segments (no 'completed' field).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING

import numpy as np
import websockets

from .audio import pcm_to_float32, resample, WHISPERLIVE_RATE

if TYPE_CHECKING:
    from .config import Settings

_LOGGER = logging.getLogger(__name__)

CHUNK_SAMPLES = 4096
CHUNK_DELAY_S = 0.01
TRT_SETTLE_S = 0.3


async def transcribe(audio_bytes: bytes | bytearray, settings: Settings) -> str:
    """Send audio to WhisperLive and return final transcript."""
    if len(audio_bytes) == 0:
        return ""

    audio_f32 = pcm_to_float32(bytes(audio_bytes))
    # Audio from satellite is 16kHz — same as WhisperLive expects
    # Resample only if needed
    audio_f32 = resample(audio_f32, 16000, WHISPERLIVE_RATE)

    duration_s = len(audio_f32) / WHISPERLIVE_RATE
    _LOGGER.debug("STT audio: %.2fs, %d samples", duration_s, len(audio_f32))

    try:
        async with websockets.connect(settings.whisperlive_url) as ws:
            # Send init config
            config = {
                "uid": str(uuid.uuid4()),
                "language": "en",
                "task": "transcribe",
                "model": "small.en",
                "use_vad": True,
            }
            await ws.send(json.dumps(config))

            # Wait for SERVER_READY
            try:
                ready_msg = await asyncio.wait_for(ws.recv(), timeout=15)
                if isinstance(ready_msg, str):
                    ready_data = json.loads(ready_msg)
                    _LOGGER.debug("WhisperLive ready: %s", ready_data)
                    if ready_data.get("message") == "WAIT":
                        _LOGGER.warning("WhisperLive server full")
                        return ""
            except asyncio.TimeoutError:
                _LOGGER.error("Timeout waiting for WhisperLive SERVER_READY")
                return ""

            # Stream audio as float32 chunks
            for i in range(0, len(audio_f32), CHUNK_SAMPLES):
                chunk = audio_f32[i : i + CHUNK_SAMPLES]
                await ws.send(chunk.tobytes())
                await asyncio.sleep(CHUNK_DELAY_S)

            _LOGGER.debug("All audio sent, waiting for transcript...")

            # Collect transcript with TRT stabilize-on-repeat logic
            final_text = ""
            wait_timeout = max(5.0, duration_s * 2)
            deadline = asyncio.get_event_loop().time() + wait_timeout

            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break

                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                if not isinstance(msg, str):
                    continue

                data = json.loads(msg)

                # Server control messages
                if "message" in data and "segments" not in data:
                    if data.get("message") == "DISCONNECT":
                        break
                    continue

                for seg in data.get("segments", []):
                    text = seg.get("text", "").strip()
                    if not text:
                        continue

                    if "completed" not in seg:
                        # TRT backend: stabilize-on-repeat
                        if text == final_text:
                            _LOGGER.debug("TRT text stabilized: %s", text)
                            return final_text
                        # New text — short deadline for more
                        deadline = asyncio.get_event_loop().time() + TRT_SETTLE_S

                    final_text = text
                    if seg.get("completed", False):
                        return final_text

            return final_text

    except Exception:
        _LOGGER.exception("WhisperLive error")
        return ""
