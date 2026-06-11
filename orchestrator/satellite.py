"""Wyoming satellite connection manager.

Connects to a wyoming-satellite as its "server" (we are the TCP client).
Handles the Describe/Info/RunSatellite handshake, then dispatches
RunPipeline events to the pipeline coordinator.

Supports multi-turn (re-listen after follow-up questions) and
barge-in (cancel TTS when user speaks again).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event, async_read_event, async_write_event
from wyoming.info import Describe, Info
from wyoming.ping import Ping, Pong
from wyoming.pipeline import RunPipeline
from wyoming.satellite import RunSatellite

if TYPE_CHECKING:
    from .config import SatelliteConfig, Settings
    from .session import SessionManager

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY_S = 10
PING_INTERVAL_S = 30


class SatelliteConnection:
    """Manages the persistent connection to one wyoming-satellite."""

    def __init__(
        self,
        config: SatelliteConfig,
        settings: Settings,
        session_manager: SessionManager,
    ):
        self.config = config
        self.settings = settings
        self.session_manager = session_manager
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pipeline_task: asyncio.Task | None = None
        self._audio_queue: asyncio.Queue[bytes | None] | None = None
        self._chunk_log_count = 0
        self._last_response_was_question = False

    @property
    def satellite_id(self) -> str:
        return self.config.name

    async def run_forever(self) -> None:
        """Connect and reconnect in a loop."""
        while True:
            try:
                await self._connect_and_run()
            except Exception:
                _LOGGER.exception(
                    "[%s] Connection error, reconnecting in %ds",
                    self.config.name,
                    RECONNECT_DELAY_S,
                )
            finally:
                await self._cleanup()
            await asyncio.sleep(RECONNECT_DELAY_S)

    async def _connect_and_run(self) -> None:
        _LOGGER.info(
            "[%s] Connecting to %s:%d",
            self.config.name,
            self.config.host,
            self.config.port,
        )
        self._reader, self._writer = await asyncio.open_connection(
            self.config.host, self.config.port
        )
        _LOGGER.info("[%s] TCP connected", self.config.name)

        # Handshake: we describe ourselves, satellite responds with its info
        await self._write_event(Describe().event())
        info_event = await self._read_event()
        if info_event is None:
            raise ConnectionError("No response to Describe")

        if Info.is_type(info_event.type):
            _LOGGER.info(
                "[%s] Satellite info: %s",
                self.config.name,
                info_event.data,
            )
        else:
            _LOGGER.warning(
                "[%s] Expected Info, got %s", self.config.name, info_event.type
            )

        # Tell satellite we're ready
        await self._write_event(RunSatellite().event())
        _LOGGER.info("[%s] Sent RunSatellite, waiting for wake word", self.config.name)

        # Start ping task
        ping_task = asyncio.create_task(self._ping_loop())

        try:
            await self._event_loop()
        finally:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass

    async def _event_loop(self) -> None:
        """Read and dispatch events from the satellite."""
        event_count = 0
        while True:
            event = await self._read_event()
            event_count += 1
            if event_count <= 5 or event_count % 100 == 0:
                _LOGGER.info(
                    "[%s] Event #%d: type=%s",
                    self.config.name,
                    event_count,
                    event.type if event else "None",
                )
            if event is None:
                _LOGGER.warning("[%s] Satellite disconnected", self.config.name)
                return

            if RunPipeline.is_type(event.type):
                pipeline_info = RunPipeline.from_event(event)
                _LOGGER.info(
                    "[%s] RunPipeline: start=%s end=%s",
                    self.config.name,
                    pipeline_info.start_stage.value,
                    pipeline_info.end_stage.value,
                )
                # Barge-in: cancel existing pipeline if one is running
                if self._pipeline_task is not None and not self._pipeline_task.done():
                    _LOGGER.info("[%s] Barge-in: cancelling active pipeline", self.config.name)
                await self._start_pipeline(pipeline_info)

            elif AudioChunk.is_type(event.type):
                if self._audio_queue is not None:
                    chunk = AudioChunk.from_event(event)
                    await self._audio_queue.put(chunk.audio)
                    if self._chunk_log_count < 3:
                        _LOGGER.info(
                            "[%s] AudioChunk queued: %d bytes",
                            self.config.name,
                            len(chunk.audio),
                        )
                        self._chunk_log_count += 1

            elif AudioStop.is_type(event.type):
                _LOGGER.info("[%s] AudioStop received", self.config.name)
                if self._audio_queue is not None:
                    await self._audio_queue.put(None)  # sentinel

            elif event.type == "played":
                # Satellite finished playing TTS audio
                _LOGGER.info("[%s] TTS playback complete", self.config.name)
                # Signal the pipeline that playback is done (for echo draining)
                if self._audio_queue is not None:
                    await self._audio_queue.put(b"PLAYED")  # sentinel

            elif Ping.is_type(event.type):
                await self._write_event(Pong().event())

            elif Pong.is_type(event.type):
                pass  # response to our ping

            elif event.type == "detection":
                pass  # handled implicitly — RunPipeline follows immediately

            else:
                _LOGGER.debug(
                    "[%s] Unhandled event: %s", self.config.name, event.type
                )

    async def _start_pipeline(self, run_info: RunPipeline) -> None:
        """Start the voice pipeline for this utterance."""
        # Cancel any existing pipeline (barge-in)
        if self._pipeline_task is not None and not self._pipeline_task.done():
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass

        self._audio_queue = asyncio.Queue()
        self._chunk_log_count = 0

        # Import here to avoid circular imports
        from .pipeline import Pipeline

        session = self.session_manager.get_or_create(self.satellite_id)
        pipeline = Pipeline(
            satellite=self,
            session=session,
            settings=self.settings,
            audio_queue=self._audio_queue,
        )
        self._pipeline_task = asyncio.create_task(
            pipeline.run(), name=f"pipeline-{self.config.name}"
        )

    async def write_event(self, event: Event) -> None:
        """Public interface for pipeline to send events to the satellite."""
        await self._write_event(event)

    async def _write_event(self, event: Event) -> None:
        if self._writer is None:
            return
        await async_write_event(event, self._writer)

    async def _read_event(self) -> Event | None:
        if self._reader is None:
            return None
        try:
            event = await async_read_event(self._reader)
            if event is None:
                _LOGGER.warning(
                    "[%s] _read_event returned None (EOF or parse error)",
                    self.config.name,
                )
            return event
        except Exception as e:
            _LOGGER.error("[%s] _read_event exception: %s", self.config.name, e)
            return None

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            try:
                await self._write_event(Ping().event())
            except Exception:
                return

    async def _cleanup(self) -> None:
        if self._pipeline_task is not None and not self._pipeline_task.done():
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass
            self._pipeline_task = None

        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
