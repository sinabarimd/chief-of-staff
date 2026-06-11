"""Orchestrator configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SatelliteConfig:
    name: str
    host: str
    port: int = 10700
    vad_threshold: int = 500  # Pi with 10x gain needs higher
    vad_silence_s: float = 1.2


@dataclass
class ESPHomeSatelliteConfig:
    name: str
    host: str
    port: int = 6053
    encryption_key: str = ""
    vad_threshold: int = 250
    vad_silence_s: float = 0.7  # shorter for snappy response


@dataclass
class Settings:
    satellites: list[SatelliteConfig] = field(default_factory=list)
    esphome_satellites: list[ESPHomeSatelliteConfig] = field(default_factory=list)
    whisperlive_url: str = "ws://localhost:9090"
    vllm_url: str = "http://localhost:8000/v1"
    vllm_model: str = "neuralmagic/Meta-Llama-3.1-8B-Instruct-quantized.w4a16"
    kokoro_host: str = "localhost"
    kokoro_port: int = 10200
    n8n_webhook_url: str = "http://localhost:5678/webhook/voice-escalate"
    cloud_n8n_url: str = "https://your-n8n-host.example.com/webhook/voice-query"
    cloud_n8n_key: str = ""
    session_timeout_s: float = 120.0
    stt_timeout_s: float = 10.0
    llm_timeout_s: float = 5.0
    tts_timeout_s: float = 3.0
    confidence_threshold: float = 0.8
    tts_voice: str = "bm_lewis"
    tts_sample_rate: int = 22050
    system_prompt_path: str = "prompts/system.txt"

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls()

        # Wyoming satellites from JSON env var
        sat_json = os.environ.get("VOICEHUB_SATELLITES", "")
        if sat_json:
            for s in json.loads(sat_json):
                settings.satellites.append(
                    SatelliteConfig(
                        name=s["name"],
                        host=s["host"],
                        port=s.get("port", 10700),
                        vad_threshold=s.get("vad_threshold", 500),
                        vad_silence_s=s.get("vad_silence_s", 1.2),
                    )
                )

        # ESPHome satellites from JSON env var
        esph_json = os.environ.get("VOICEHUB_ESPHOME_SATELLITES", "")
        if esph_json:
            for s in json.loads(esph_json):
                settings.esphome_satellites.append(
                    ESPHomeSatelliteConfig(
                        name=s["name"],
                        host=s["host"],
                        port=s.get("port", 6053),
                        encryption_key=s.get("encryption_key", ""),
                        vad_threshold=s.get("vad_threshold", 250),
                        vad_silence_s=s.get("vad_silence_s", 0.7),
                    )
                )

        for attr, env_key in [
            ("whisperlive_url", "VOICEHUB_WHISPERLIVE_URL"),
            ("vllm_url", "VOICEHUB_VLLM_URL"),
            ("vllm_model", "VOICEHUB_VLLM_MODEL"),
            ("kokoro_host", "VOICEHUB_KOKORO_HOST"),
            ("n8n_webhook_url", "VOICEHUB_N8N_WEBHOOK_URL"),
            ("cloud_n8n_url", "VOICEHUB_CLOUD_N8N_URL"),
            ("cloud_n8n_key", "VOICEHUB_CLOUD_N8N_KEY"),
            ("tts_voice", "VOICEHUB_TTS_VOICE"),
            ("system_prompt_path", "VOICEHUB_SYSTEM_PROMPT"),
        ]:
            val = os.environ.get(env_key)
            if val is not None:
                setattr(settings, attr, val)

        for attr, env_key in [
            ("kokoro_port", "VOICEHUB_KOKORO_PORT"),
            ("tts_sample_rate", "VOICEHUB_TTS_SAMPLE_RATE"),
        ]:
            val = os.environ.get(env_key)
            if val is not None:
                setattr(settings, attr, int(val))

        for attr, env_key in [
            ("session_timeout_s", "VOICEHUB_SESSION_TIMEOUT"),
            ("stt_timeout_s", "VOICEHUB_STT_TIMEOUT"),
            ("llm_timeout_s", "VOICEHUB_LLM_TIMEOUT"),
            ("tts_timeout_s", "VOICEHUB_TTS_TIMEOUT"),
            ("confidence_threshold", "VOICEHUB_CONFIDENCE_THRESHOLD"),
        ]:
            val = os.environ.get(env_key)
            if val is not None:
                setattr(settings, attr, float(val))

        return settings

    def load_system_prompt(self) -> str:
        """Load system prompt from file, searching relative to this package."""
        # Try relative to this file first, then absolute
        pkg_dir = Path(__file__).parent
        for candidate in [pkg_dir / self.system_prompt_path, Path(self.system_prompt_path)]:
            if candidate.is_file():
                return candidate.read_text().strip()
        raise FileNotFoundError(f"System prompt not found: {self.system_prompt_path}")
