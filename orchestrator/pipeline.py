"""Streaming voice pipeline: STT → LLM → TTS.

One Pipeline instance per conversation (may span multiple turns).
Supports multi-turn: if LLM response ends with '?', loops back to
listen for more speech without requiring a wake word.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop

if TYPE_CHECKING:
    from .config import Settings
    from .satellite import SatelliteConnection
    from .session import ConversationSession

_LOGGER = logging.getLogger(__name__)

# VAD parameters
VAD_MAX_DURATION_S = 15.0  # Hard cap on utterance length
VAD_FRAME_MS = 30  # WebRTC VAD frame size (10, 20, or 30 ms)
VAD_FRAME_BYTES = int(16000 * 2 * VAD_FRAME_MS / 1000)  # bytes per frame at 16kHz 16-bit
# Multi-turn: how long to wait for follow-up speech before ending conversation
MULTITURN_TIMEOUT_S = 12.0


class Pipeline:
    """Coordinates STT → LLM → TTS, with multi-turn conversation support."""

    def __init__(
        self,
        satellite: SatelliteConnection,
        session: ConversationSession,
        settings: Settings,
        audio_queue: asyncio.Queue[bytes | None],
    ):
        self.satellite = satellite
        self.session = session
        self.settings = settings
        self.audio_queue = audio_queue

    def _make_tts_callback(self, sat_name: str):
        """Create a callback that sends an interim TTS message to the satellite."""
        async def _send_interim(text: str) -> None:
            from .tts import synthesize_chunks
            _LOGGER.info("[%s] Sending interim TTS: '%s'", sat_name, text[:60])
            await self.satellite.write_event(
                AudioStart(
                    rate=self.settings.tts_sample_rate, width=2, channels=1,
                ).event()
            )
            async for chunk in synthesize_chunks(text, self.settings):
                await self.satellite.write_event(chunk.event())
            await self.satellite.write_event(AudioStop().event())
        return _send_interim

    async def run(self) -> None:
        """Run the pipeline. May loop for multi-turn conversations."""
        turn = 0
        while True:
            turn += 1
            result = await self._run_turn(turn)
            if result == "continue":
                # Multi-turn: LLM asked a follow-up question.
                # Satellite is still streaming audio to us — loop back.
                _LOGGER.info(
                    "[%s] Multi-turn: listening for turn %d",
                    self.satellite.config.name,
                    turn + 1,
                )
                continue
            else:
                # Conversation done — tell satellite to go back to wake word
                await self.satellite.write_event(
                    Transcript(text="").event()
                )
                _LOGGER.info(
                    "[%s] Conversation ended after %d turn(s)",
                    self.satellite.config.name,
                    turn,
                )
                return

    async def _run_turn(self, turn: int) -> str:
        """Run one turn of the conversation. Returns 'continue' or 'done'."""
        import webrtcvad

        t_start = time.monotonic()
        sat_name = self.satellite.config.name
        vad_silence_s = getattr(self.satellite.config, 'vad_silence_s', 0.8)

        # WebRTC VAD — aggressiveness 3 = most aggressive at filtering non-speech
        vad = webrtcvad.Vad(3)

        # --- Collect audio with server-side VAD ---
        if turn == 1:
            _LOGGER.info("[%s] Pipeline started, collecting audio...", sat_name)
        else:
            _LOGGER.info("[%s] Turn %d: listening...", sat_name, turn)

        audio_bytes = bytearray()
        vad_buf = bytearray()  # accumulates until we have a full VAD frame
        chunk_count = 0
        silence_start: float | None = None
        speech_detected = False
        speech_frames = 0
        silence_frames = 0
        # For multi-turn, use a longer initial timeout to wait for user to start speaking
        initial_timeout = MULTITURN_TIMEOUT_S if turn > 1 else self.settings.stt_timeout_s

        while True:
            timeout = initial_timeout if not speech_detected else self.settings.stt_timeout_s
            try:
                data = await asyncio.wait_for(
                    self.audio_queue.get(), timeout=timeout
                )
            except asyncio.TimeoutError:
                if not speech_detected and turn > 1:
                    _LOGGER.info("[%s] Multi-turn timeout, no follow-up speech", sat_name)
                    return "done"
                _LOGGER.warning("[%s] Audio timeout", sat_name)
                break

            if data is None:  # AudioStop sentinel
                break

            audio_bytes.extend(data)
            vad_buf.extend(data)
            chunk_count += 1
            if chunk_count == 1 and turn == 1:
                _LOGGER.info("[%s] First audio chunk received: %d bytes", sat_name, len(data))

            # Process complete VAD frames (30ms = 960 bytes at 16kHz 16-bit)
            now = time.monotonic()
            audio_duration = len(audio_bytes) / (16000 * 2)

            while len(vad_buf) >= VAD_FRAME_BYTES:
                frame = bytes(vad_buf[:VAD_FRAME_BYTES])
                vad_buf = vad_buf[VAD_FRAME_BYTES:]

                is_speech = vad.is_speech(frame, 16000)

                if is_speech:
                    speech_frames += 1
                    silence_frames = 0
                    if not speech_detected and speech_frames >= 3:
                        # Need 3 consecutive speech frames (~90ms) to trigger
                        speech_detected = True
                        speech_start_time = now
                        _LOGGER.info("[%s] Speech detected", sat_name)
                    silence_start = None
                else:
                    silence_frames += 1
                    speech_frames = 0
                    if speech_detected and (now - speech_start_time) >= 1.5:
                        if silence_start is None:
                            silence_start = now
                        elif (now - silence_start) >= vad_silence_s:
                            _LOGGER.info(
                                "[%s] End of speech (%.1fs silence)",
                                sat_name, vad_silence_s,
                            )
                            break
            else:
                # inner while didn't break — continue outer loop
                if audio_duration >= VAD_MAX_DURATION_S:
                    _LOGGER.warning("[%s] Max audio duration reached", sat_name)
                    break
                continue
            # inner while broke — end of speech detected
            break

        t_audio = time.monotonic()
        audio_duration_s = len(audio_bytes) / (16000 * 2)
        _LOGGER.info(
            "[%s] Audio collected: %d chunks, %.1fs, %.1f KB in %.0fms",
            sat_name, chunk_count, audio_duration_s,
            len(audio_bytes) / 1024, (t_audio - t_start) * 1000,
        )

        # Debug: dump audio to WAV for analysis
        import wave, io
        debug_path = f"/tmp/vad_debug_{sat_name}.wav"
        with wave.open(debug_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(bytes(audio_bytes))
        _LOGGER.info("[%s] Debug audio saved: %s", sat_name, debug_path)

        if len(audio_bytes) < 1600:  # < 50ms
            _LOGGER.warning("[%s] Audio too short, skipping", sat_name)
            return "done"

        # --- STT ---
        from .stt import transcribe

        transcript_text = await transcribe(audio_bytes, self.settings)
        t_stt = time.monotonic()
        _LOGGER.info(
            "[%s] STT (%.0fms): '%s'",
            sat_name, (t_stt - t_audio) * 1000, transcript_text,
        )

        if not transcript_text.strip():
            _LOGGER.warning("[%s] No text recognized", sat_name)
            return "done"

        # --- Check for inbox command (bypasses LLM) ---
        from .voice_inbox import handle_inbox_command, is_inbox_active, INBOX_KEYWORDS
        is_inbox_trigger = any(kw in transcript_text.lower() for kw in INBOX_KEYWORDS)
        if is_inbox_trigger or is_inbox_active(sat_name):
            from .tts import synthesize_chunks
            inbox_response = await handle_inbox_command(sat_name, transcript_text)
            _LOGGER.info("[%s] Inbox: '%s'", sat_name, inbox_response[:80])
            llm_response_text = inbox_response

            # Signal multi-turn if response ends with '?'
            if hasattr(self.satellite, 'continue_conversation'):
                self.satellite.continue_conversation = inbox_response.rstrip().endswith("?")

            t_first_audio = None
            await self.satellite.write_event(
                AudioStart(
                    rate=self.settings.tts_sample_rate,
                    width=2, channels=1,
                ).event()
            )
            async for chunk in synthesize_chunks(inbox_response, self.settings):
                if t_first_audio is None:
                    t_first_audio = time.monotonic()
                await self.satellite.write_event(chunk.event())
            await self.satellite.write_event(AudioStop().event())

            self.session.add_message("user", transcript_text)
            self.session.add_message("assistant", inbox_response)

            # Echo drain
            drained = 0
            try:
                while True:
                    data = await asyncio.wait_for(
                        self.audio_queue.get(), timeout=15.0
                    )
                    if data == b"PLAYED":
                        break
                    if data is None:
                        break
                    drained += 1
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1.0)
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            t_done = time.monotonic()
            _LOGGER.info("[%s] Inbox turn in %.0fms", sat_name, (t_done - t_audio) * 1000)

            if inbox_response.rstrip().endswith("?"):
                return "continue"
            return "done"

        # --- Check for pending escalation offer response ---
        from .escalation_offer import has_pending_offer, get_pending_offer, classify_response, store_offer
        if has_pending_offer(sat_name):
            from .tts import synthesize_chunks
            from .intent_router import handle_tool_call
            from .llm import ToolCall

            decision = classify_response(transcript_text)
            offer = get_pending_offer(sat_name)
            _LOGGER.info("[%s] Escalation decision: %s", sat_name, decision)

            if decision == "escalate" and offer:
                # Route to sync voice_query instead of async escalate
                tool_call = ToolCall(
                    name="voice_query",
                    arguments={"query": offer.original_utterance, "mode": "auto"},
                )
                llm_response_text = await handle_tool_call(
                    tool_call, self.settings,
                    tts_callback=self._make_tts_callback(sat_name),
                )
            elif decision == "search" and offer:
                tool_call = ToolCall(
                    name="voice_query",
                    arguments={"query": offer.original_utterance, "mode": "research"},
                )
                llm_response_text = await handle_tool_call(
                    tool_call, self.settings,
                    tts_callback=self._make_tts_callback(sat_name),
                )
            elif decision == "no":
                llm_response_text = "Okay, let me try. " + transcript_text
                # Fall through to LLM below — skip this block
                # Actually just let LLM handle it without tools
                pass
            elif decision == "cancel":
                llm_response_text = "No problem."
            else:
                llm_response_text = "I didn't catch that. Would you like me to ask Claude, or should I try to help directly?"
                if offer:
                    store_offer(sat_name, offer.original_utterance)

            if decision != "no":
                # TTS the response
                if hasattr(self.satellite, 'continue_conversation'):
                    self.satellite.continue_conversation = llm_response_text.rstrip().endswith("?")
                t_first_audio = None
                await self.satellite.write_event(
                    AudioStart(rate=self.settings.tts_sample_rate, width=2, channels=1).event()
                )
                async for chunk in synthesize_chunks(llm_response_text, self.settings):
                    if t_first_audio is None:
                        t_first_audio = time.monotonic()
                    await self.satellite.write_event(chunk.event())
                await self.satellite.write_event(AudioStop().event())

                self.session.add_message("user", transcript_text)
                self.session.add_message("assistant", llm_response_text)

                # Echo drain
                drained = 0
                try:
                    while True:
                        data = await asyncio.wait_for(self.audio_queue.get(), timeout=15.0)
                        if data == b"PLAYED":
                            break
                        if data is None:
                            break
                        drained += 1
                except asyncio.TimeoutError:
                    pass
                await asyncio.sleep(1.0)
                while not self.audio_queue.empty():
                    try:
                        self.audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                if llm_response_text.rstrip().endswith("?"):
                    return "continue"
                return "done"

        # --- Check for web search queries ---
        from .web_search import should_search, search as web_search
        if should_search(transcript_text):
            from .tts import synthesize_chunks
            search_answer = await web_search(transcript_text)
            _LOGGER.info("[%s] Web search answer: '%s'", sat_name, search_answer[:80])
            llm_response_text = search_answer + " Is there anything else I can help with?"

            if hasattr(self.satellite, 'continue_conversation'):
                self.satellite.continue_conversation = True

            t_first_audio = None
            await self.satellite.write_event(
                AudioStart(rate=self.settings.tts_sample_rate, width=2, channels=1).event()
            )
            async for chunk in synthesize_chunks(llm_response_text, self.settings):
                if t_first_audio is None:
                    t_first_audio = time.monotonic()
                await self.satellite.write_event(chunk.event())
            await self.satellite.write_event(AudioStop().event())

            self.session.add_message("user", transcript_text)
            self.session.add_message("assistant", llm_response_text)

            # Echo drain
            drained = 0
            try:
                while True:
                    data = await asyncio.wait_for(self.audio_queue.get(), timeout=15.0)
                    if data == b"PLAYED":
                        break
                    if data is None:
                        break
                    drained += 1
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1.0)
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            t_done = time.monotonic()
            _LOGGER.info("[%s] Web search turn in %.0fms", sat_name, (t_done - t_audio) * 1000)
            return "continue"

        # --- LLM: try tool-calling first, fall back to streaming ---
        from .llm import call_with_tools, stream_completion
        from .intent_router import handle_tool_call
        from .tts import synthesize_chunks

        self.session.add_message("user", transcript_text)
        messages = self.session.get_messages()

        t_first_audio: float | None = None

        # First, try with tools — non-streaming for reliable tool-call parsing
        llm_result = await call_with_tools(messages, self.settings)
        t_llm = time.monotonic()

        if llm_result.is_tool_call:
            # --- Tool call path: route intent, TTS the ack ---
            _LOGGER.info(
                "[%s] Tool call detected (%.0fms): %s",
                sat_name, (t_llm - t_stt) * 1000, llm_result.tool_call.name,
            )
            ack_message = await handle_tool_call(
                llm_result.tool_call, self.settings,
                tts_callback=self._make_tts_callback(sat_name),
            )
            llm_response_text = ack_message

            # Signal multi-turn: ack ends with '?' so PE should keep listening
            if hasattr(self.satellite, 'continue_conversation'):
                self.satellite.continue_conversation = llm_response_text.rstrip().endswith("?")

            # Send ack through TTS
            await self.satellite.write_event(
                AudioStart(
                    rate=self.settings.tts_sample_rate,
                    width=2, channels=1,
                ).event()
            )
            async for chunk in synthesize_chunks(ack_message, self.settings):
                if t_first_audio is None:
                    t_first_audio = time.monotonic()
                await self.satellite.write_event(chunk.event())
            await self.satellite.write_event(AudioStop().event())

        elif llm_result.content:
            # --- Text path: use streaming for natural conversation ---
            llm_response_text = llm_result.content
            _LOGGER.info(
                "[%s] LLM text (%.0fms): '%s'",
                sat_name, (t_llm - t_stt) * 1000, llm_response_text[:80],
            )

            # If LLM offered to escalate, store the offer for next turn
            offer_phrases = ["ask claude", "hand it off", "want me to ask", "want me to look into", "look into it"]
            if any(p in llm_response_text.lower() for p in offer_phrases):
                store_offer(sat_name, transcript_text)
                _LOGGER.info("[%s] Stored escalation offer for: '%s'", sat_name, transcript_text[:60])

            # Signal multi-turn if response ends with '?'
            if hasattr(self.satellite, 'continue_conversation'):
                self.satellite.continue_conversation = llm_response_text.rstrip().endswith("?")

            sentences = _split_sentences(llm_response_text)
            sent_audio_start = False
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                async for chunk in synthesize_chunks(sentence, self.settings):
                    if not sent_audio_start:
                        await self.satellite.write_event(
                            AudioStart(
                                rate=self.settings.tts_sample_rate,
                                width=2, channels=1,
                            ).event()
                        )
                        sent_audio_start = True
                        t_first_audio = time.monotonic()
                        _LOGGER.info(
                            "[%s] First audio to satellite (%.0fms after STT)",
                            sat_name, (t_first_audio - t_stt) * 1000,
                        )
                    await self.satellite.write_event(chunk.event())
            if sent_audio_start:
                await self.satellite.write_event(AudioStop().event())
        else:
            _LOGGER.warning("[%s] LLM returned empty response", sat_name)
            llm_response_text = ""

        # Wait for TTS playback to finish on the speaker, draining echo audio.
        # The satellite sends a "played" event when done, which arrives as
        # b"PLAYED" sentinel in the audio queue.
        drained = 0
        try:
            while True:
                data = await asyncio.wait_for(
                    self.audio_queue.get(), timeout=15.0
                )
                if data == b"PLAYED":
                    _LOGGER.info("[%s] Playback confirmed, drained %d echo chunks", sat_name, drained)
                    break
                if data is None:
                    break
                drained += 1
        except asyncio.TimeoutError:
            _LOGGER.warning("[%s] Timeout waiting for playback confirmation, drained %d chunks", sat_name, drained)
        # Pause for trailing echo to clear
        await asyncio.sleep(1.0)
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break

        t_done = time.monotonic()
        self.session.add_message("assistant", llm_response_text)

        _LOGGER.info(
            "[%s] Turn %d complete: stt=%.0fms first_audio=%.0fms total=%.0fms | '%s'",
            sat_name, turn,
            (t_stt - t_audio) * 1000,
            (t_first_audio - t_stt) * 1000 if t_first_audio else 0,
            (t_done - t_audio) * 1000,
            llm_response_text[:80],
        )

        # Multi-turn: if response ends with '?', keep listening
        if self.session.should_continue():
            return "continue"
        return "done"


# --- Sentence splitting ---

_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')
_ABBREVS = {'mr.', 'mrs.', 'ms.', 'dr.', 'prof.', 'sr.', 'jr.', 'vs.', 'etc.', 'e.g.', 'i.e.', 'a.m.', 'p.m.'}


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences. Returns list where last element may be incomplete."""
    parts = _SENTENCE_END.split(text)
    if len(parts) <= 1:
        return parts

    merged = [parts[0]]
    for part in parts[1:]:
        prev_lower = merged[-1].lower().rstrip()
        if any(prev_lower.endswith(abbr) for abbr in _ABBREVS):
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)

    return merged
