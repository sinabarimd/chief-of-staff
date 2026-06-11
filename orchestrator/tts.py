"""Kokoro TTS client via Wyoming protocol.

Opens a TCP connection to Kokoro, sends Synthesize, and streams
AudioChunk events back. Provides both a per-sentence iterator
and a convenience function that streams directly to a satellite.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, TYPE_CHECKING

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice

if TYPE_CHECKING:
    from .config import Settings
    from .satellite import SatelliteConnection

_LOGGER = logging.getLogger(__name__)


async def synthesize_chunks(
    text: str, settings: Settings
) -> AsyncIterator[AudioChunk]:
    """Synthesize text and yield AudioChunk events from Kokoro."""
    client = AsyncTcpClient(settings.kokoro_host, settings.kokoro_port)
    await client.connect()

    voice = SynthesizeVoice(name=settings.tts_voice)
    await client.write_event(Synthesize(text=text, voice=voice).event())

    try:
        while True:
            event = await asyncio.wait_for(
                client.read_event(), timeout=settings.tts_timeout_s
            )
            if event is None:
                break
            if AudioChunk.is_type(event.type):
                yield AudioChunk.from_event(event)
            elif AudioStop.is_type(event.type):
                break
            # AudioStart is ignored — we manage our own start/stop to satellite
    except asyncio.TimeoutError:
        _LOGGER.warning("TTS timeout for: %s", text[:50])
    except Exception:
        _LOGGER.exception("TTS error")


async def synthesize_and_stream(
    text: str,
    satellite: SatelliteConnection,
    settings: Settings,
) -> None:
    """Synthesize text and stream audio directly to a satellite.

    Wraps all audio in a single AudioStart/AudioStop pair.
    """
    sent_start = False

    async for chunk in synthesize_chunks(text, settings):
        if not sent_start:
            await satellite.write_event(
                AudioStart(
                    rate=settings.tts_sample_rate,
                    width=2,
                    channels=1,
                ).event()
            )
            sent_start = True
        await satellite.write_event(chunk.event())

    if sent_start:
        await satellite.write_event(AudioStop().event())
