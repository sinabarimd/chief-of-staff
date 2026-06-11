"""ESPHome Voice PE satellite connection manager.

Connects to an ESPHome Voice PE via the native API (port 6053).
Implements the same interface as SatelliteConnection so the Pipeline
class can drive it identically to a Wyoming satellite.

The Voice PE does on-device wake word detection (microWakeWord) and
streams audio to us via the API. TTS audio is served via HTTP —
the Voice PE's media_player downloads and plays it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import TYPE_CHECKING

from aiohttp import web

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event

from aioesphomeapi import (
    APIClient,
    VoiceAssistantAudioSettings,
    VoiceAssistantEventType,
)

if TYPE_CHECKING:
    from .config import ESPHomeSatelliteConfig, Settings
    from .session import SessionManager

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY_S = 10
TTS_HTTP_PORT = 8081


class TTSHttpServer:
    """Serves TTS audio files for Voice PE media player downloads."""

    def __init__(self, port: int = TTS_HTTP_PORT):
        self.port = port
        self._app = web.Application()
        self._app.router.add_get("/tts/{filename}", self._handle_tts)
        self._runner: web.AppRunner | None = None
        self._files: dict[str, bytes] = {}
        self._started = False

    def store(self, filename: str, wav_data: bytes) -> str:
        """Store WAV data and return the URL path."""
        self._files[filename] = wav_data
        return f"/tts/{filename}"

    def clear(self, filename: str) -> None:
        self._files.pop(filename, None)

    async def _handle_tts(self, request: web.Request) -> web.Response:
        filename = request.match_info["filename"]
        data = self._files.get(filename)
        if data is None:
            return web.Response(status=404)
        return web.Response(
            body=data,
            content_type="audio/wav",
        )

    async def start(self) -> None:
        if self._started:
            return
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port, reuse_address=True)
        await site.start()
        self._started = True
        _LOGGER.info("TTS HTTP server listening on port %d", self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()


# Shared TTS server — started once, used by all ESPHome satellites
_tts_server: TTSHttpServer | None = None
_tts_server_lock = asyncio.Lock()


async def _get_tts_server(port: int = TTS_HTTP_PORT) -> TTSHttpServer:
    global _tts_server
    async with _tts_server_lock:
        if _tts_server is None:
            _tts_server = TTSHttpServer(port)
            await _tts_server.start()
        return _tts_server


class ESPHomeSatellite:
    """Manages the persistent connection to one ESPHome Voice PE."""

    def __init__(
        self,
        config: "ESPHomeSatelliteConfig",
        settings: "Settings",
        session_manager: "SessionManager",
    ):
        self.config = config
        self.settings = settings
        self.session_manager = session_manager
        self._client: APIClient | None = None
        self._pipeline_task: asyncio.Task | None = None
        self._audio_queue: asyncio.Queue[bytes | None] | None = None
        self._connected = False
        self._pending_pipeline = False
        self._tts_server: TTSHttpServer | None = None
        self._tts_audio_buf: bytearray = bytearray()
        self._tts_filename: str = ""
        self._tts_sample_rate: int = 0
        self.continue_conversation: bool = False  # set by pipeline for multi-turn

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
            "[%s] Connecting to ESPHome at %s:%d",
            self.config.name,
            self.config.host,
            self.config.port,
        )

        # Ensure TTS HTTP server is running
        self._tts_server = await _get_tts_server()

        self._client = APIClient(
            address=self.config.host,
            port=self.config.port,
            password="",
            noise_psk=self.config.encryption_key or None,
        )

        async def _on_disconnect(expected_disconnect: bool) -> None:
            if not expected_disconnect:
                _LOGGER.warning(
                    "[%s] ESPHome connection lost, will reconnect",
                    self.config.name,
                )
            self._connected = False

        await self._client.connect(on_stop=_on_disconnect, login=True)
        _LOGGER.info("[%s] ESPHome API connected", self.config.name)

        device_info = await self._client.device_info()
        _LOGGER.info(
            "[%s] Device: %s (v%s)",
            self.config.name,
            device_info.friendly_name or device_info.name,
            device_info.esphome_version,
        )

        # Subscribe to voice assistant events
        self._client.subscribe_voice_assistant(
            handle_start=self._handle_va_start,
            handle_stop=self._handle_va_stop,
            handle_audio=self._handle_va_audio,
        )
        _LOGGER.info("[%s] Subscribed to voice assistant, waiting for wake word", self.config.name)
        self._connected = True

        # Event loop — check for pending pipeline starts
        try:
            while self._connected:
                if self._pending_pipeline:
                    self._pending_pipeline = False
                    await self._start_pipeline()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass

    async def _handle_va_start(
        self,
        conversation_id: str,
        flags: int,
        audio_settings: VoiceAssistantAudioSettings,
        wake_word_phrase: str | None,
    ) -> int | None:
        """Called when the Voice PE detects a wake word and wants to start a pipeline.

        Returns port number for UDP audio, or 0 to use API-based audio.
        MUST return immediately — the library sends VoiceAssistantResponse only
        after this coroutine completes.
        """
        # Check if this is a continuation (multi-turn) — pipeline already running
        if self._pipeline_task is not None and not self._pipeline_task.done():
            _LOGGER.info(
                "[%s] Multi-turn continuation (conversation_id=%s, flags=%d)",
                self.config.name, conversation_id, flags,
            )
            # Reuse existing audio queue — pipeline is waiting for turn 2 audio
            return 0

        _LOGGER.info(
            "[%s] Wake word detected: '%s' (conversation_id=%s, flags=%d)",
            self.config.name,
            wake_word_phrase or "unknown",
            conversation_id,
            flags,
        )

        # New pipeline — set up fresh queue
        self._audio_queue = asyncio.Queue()
        self._pending_pipeline = True

        # Return 0 immediately = use API audio (not UDP)
        return 0

    async def _start_pipeline(self) -> None:
        """Start the pipeline after VoiceAssistantResponse has been sent."""
        # Cancel any existing pipeline (barge-in)
        if self._pipeline_task is not None and not self._pipeline_task.done():
            _LOGGER.info("[%s] Barge-in: cancelling active pipeline", self.config.name)
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass

        from .pipeline import Pipeline

        session = self.session_manager.get_or_create(self.satellite_id)
        pipeline = Pipeline(
            satellite=self,
            session=session,
            settings=self.settings,
            audio_queue=self._audio_queue,
        )
        self._pipeline_task = asyncio.create_task(
            self._run_pipeline_with_events(pipeline),
            name=f"pipeline-{self.config.name}",
        )

    async def _run_pipeline_with_events(self, pipeline) -> None:
        """Run pipeline and send lifecycle events to the Voice PE for LED control."""
        # Signal state progression (device should already have our port=0 response)
        self._send_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START)
        self._send_event(VoiceAssistantEventType.VOICE_ASSISTANT_STT_START)
        try:
            await pipeline.run()
        except asyncio.CancelledError:
            _LOGGER.info("[%s] Pipeline cancelled", self.config.name)
        except Exception:
            _LOGGER.exception("[%s] Pipeline error", self.config.name)
            self._send_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_ERROR,
                {"code": "pipeline-error", "message": "Pipeline failed"},
            )
        finally:
            self._send_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END)

    async def _handle_va_stop(self, abort: bool) -> None:
        """Called when the Voice PE wants to stop the pipeline."""
        _LOGGER.info("[%s] Voice assistant stop (abort=%s)", self.config.name, abort)
        if self._pipeline_task is not None and not self._pipeline_task.done():
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass
        if self._audio_queue is not None:
            await self._audio_queue.put(None)

    async def _handle_va_audio(self, data: bytes) -> None:
        """Called when audio data arrives from the Voice PE."""
        if self._audio_queue is not None:
            await self._audio_queue.put(data)

    async def write_event(self, event: Event) -> None:
        """Public interface matching SatelliteConnection — translates Wyoming
        events to ESPHome voice assistant protocol.

        Voice PE uses media_player mode — TTS audio must be served via HTTP URL.
        We accumulate audio chunks, write a WAV file, serve it, and send the
        URL to the device in the TTS_END event.
        """
        if self._client is None:
            return

        if AudioStart.is_type(event.type):
            # Start accumulating TTS audio
            audio_start = AudioStart.from_event(event)
            self._tts_audio_buf = bytearray()
            self._tts_sample_rate = audio_start.rate or self.settings.tts_sample_rate
            self._tts_filename = f"tts_{self.config.name}_{id(self)}.wav"

        elif AudioChunk.is_type(event.type):
            # Buffer the audio — will be served as WAV when AudioStop arrives
            chunk = AudioChunk.from_event(event)
            self._tts_audio_buf.extend(chunk.audio)

        elif AudioStop.is_type(event.type):
            # All TTS audio received — write WAV, serve via HTTP, send events
            if not self._tts_audio_buf or self._tts_server is None:
                if self._audio_queue is not None:
                    await self._audio_queue.put(b"PLAYED")
                return

            # Build WAV file in memory
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self._tts_sample_rate)
                wf.writeframes(bytes(self._tts_audio_buf))
            wav_data = wav_buf.getvalue()

            # Store in HTTP server
            path = self._tts_server.store(self._tts_filename, wav_data)
            tts_url = f"http://192.168.1.100:{TTS_HTTP_PORT}{path}"

            audio_duration_s = len(self._tts_audio_buf) / (self._tts_sample_rate * 2)
            _LOGGER.info(
                "[%s] TTS audio ready: %.1fs, %d bytes, serving at %s",
                self.config.name, audio_duration_s, len(wav_data), tts_url,
            )

            # Send HA-compatible event sequence for media_player mode
            self._send_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
                {"text": ""},
            )
            self._send_event(VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_START)
            self._send_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_PROGRESS,
                {"tts_start_streaming": "1"},
            )
            self._send_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END,
                {"conversation_id": "", "continue_conversation": "1" if self.continue_conversation else "0"},
            )
            self._send_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START,
                {"text": ""},
            )
            self._send_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END,
                {"url": tts_url},
            )

            # Wait for the device to download and play the audio
            await asyncio.sleep(audio_duration_s + 0.5)

            # Clean up served file
            self._tts_server.clear(self._tts_filename)
            self._tts_audio_buf = bytearray()

            # Signal pipeline that playback is done
            if self._audio_queue is not None:
                await self._audio_queue.put(b"PLAYED")

        # Transcript events (empty transcript = conversation end) are no-ops
        # for ESPHome — the RUN_END event handles it

    def _send_event(
        self,
        event_type: VoiceAssistantEventType,
        data: dict[str, str] | None = None,
    ) -> None:
        """Send a voice assistant event to the device."""
        if self._client is not None:
            self._client.send_voice_assistant_event(event_type, data)

    async def _cleanup(self) -> None:
        self._connected = False
        if self._pipeline_task is not None and not self._pipeline_task.done():
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass
            self._pipeline_task = None

        if self._client is not None:
            try:
                # Send RUN_END to clear any active state on the device
                self._send_event(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END)
            except Exception:
                pass
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
