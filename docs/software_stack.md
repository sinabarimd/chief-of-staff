# Software Stack

## Host OS (i9/4070 box)

- **Ubuntu Server 24.04 LTS**, dual-boot with existing Windows install
- Nvidia driver 550+, CUDA 12.4+, cuDNN matched to CUDA
- Docker Engine + Docker Compose v2
- nvidia-container-toolkit (so containers can access the 4070)

## Compose stack (single `compose.yml` on the host)

| Service | Image / build | Purpose | Notes |
|---|---|---|---|
| `vllm` | `vllm/vllm-openai:latest` (or pinned) | LLM serving | GPU-bound. Exposes OpenAI-compatible API on 8000. |
| `parakeet-wyoming` | custom (NeMo + Wyoming wrapper) | Streaming STT | GPU-bound. Wyoming on 10300. |
| `kokoro-wyoming` | custom (Kokoro + Wyoming wrapper) | Streaming TTS | GPU-bound. Wyoming on 10200. |
| `homeassistant` | `ghcr.io/home-assistant/home-assistant:stable` | Pipeline orchestrator | Host networking or macvlan for mDNS. |
| `n8n` | `n8nio/n8n:latest` | Package bus + action executor | Persistent volume for workflows. |

GPU allocation: vLLM holds the model in VRAM; Parakeet and Kokoro dynamically load/unload. Monitor VRAM with `nvidia-smi`; Llama 3.1 8B fp8 ≈ 9GB, leaves ~3GB for STT/TTS which is tight but workable.

## LLM

- **Llama 3.1 8B in INT4 (W4A16 GPTQ-Marlin)** served by vLLM 0.19.1
- Model: `neuralmagic/Meta-Llama-3.1-8B-Instruct-quantized.w4a16`
- VRAM: 5.35 GiB model + ~2 GiB KV cache = ~7.4 GiB total at 0.60 utilization
- Benchmark: **~89 tok/s** at 512/1024/2048 context (73% faster than FP8)
- Consumed by HA's OpenAI-compatible conversation agent integration (points at `http://vllm:8000/v1`)
- Role: intent classification + argument extraction + short acknowledgments. NOT free-form generation.
- **Rejected alternatives:** FP8 (51 tok/s, 8.5 GiB — no room for STT on GPU), Gemma 4 E4B multimodal (OOM on 12 GB even at INT4 due to bf16 audio/vision encoders).

## STT

- **Faster Whisper small.en** via `rhasspy/wyoming-whisper` Docker image
- Runs on **CPU** (i9) — GPU version needs CUDA-enabled image (future optimization)
- Wyoming protocol on port 10300
- English-only, ~6% WER, ~244M params
- **No streaming partials** — batch transcription only (tradeoff vs Parakeet)
- **Rejected alternatives:** Parakeet TDT 0.6B (OOM on GPU alongside Llama INT4 — needs ~4.5 GB, not 2.8 GB as reported; 640ms on CPU), Whisper tiny.en (lower accuracy)

## TTS

- **Kokoro 82M** via `nordwestt/kokoro-wyoming` Docker image
- Runs on **CPU** (ONNX) — zero GPU impact, leaves VRAM for LLM + STT
- Wyoming protocol on port 10200 (mapped from container port 10210)
- **Voice: `bm_lewis`** (British male, authoritative "chief of staff" persona)
- Alternative voices available: `am_michael` (American male), `af_nova`, `af_bella` (female)
- Streaming output supported
- **Rejected alternatives:** Piper (lower quality, but native Wyoming + fastest latency — fallback option), Fish Speech (best quality but restrictive license), CosyVoice2 (no Wyoming wrapper)

## Wake word

- **microWakeWord** — sole wake word engine across all zones
  - **HA Voice PE satellites:** on-device on ESP32-S3 (native — microWakeWord is the stock wake word engine on Voice PE). INT8 TFLite, ~35-45 kB tensor arena, <10ms inference per step. Zero network latency, zero server load.
  - **Pi + ReSpeaker:** native Linux via ESPHome or Wyoming-wrapped microWakeWord
  - Custom model trained via [microwakeword.com](https://microwakeword.com/) (serverless GPU, minutes). Fine-tuning via [OHF-Voice/micro-wake-word](https://github.com/OHF-Voice/micro-wake-word) Jupyter notebook if accuracy needs iteration.
  - Deployment: adopt Voice PE in ESPHome Dashboard → add model URL to `micro_wake_word:` YAML → compile and flash.
- **Wake phrase:** "Sinabot" (single word, no "hey" prefix)
- **Rejected alternatives:**
  - openWakeWord — supports custom training but cannot run on ESP32-S3 (speech embedding model too heavy). Requires either custom firmware fork (open-voice-pe) or server-side Wyoming container (+5-15ms latency, server CPU load per satellite). microWakeWord is the native, zero-latency path.
  - Porcupine — no ESP32-S3 support, can't run on-device on HA Voice PE

## Home Assistant

- **HA Container** (not HAOS) deployed via Docker Compose
- Purpose: voice-pipeline orchestrator only — NOT general smart home
- Key configuration:
  - `assist_pipeline`: one pipeline per zone, all pointing at the same Wyoming endpoints + conversation agent
  - `openai_conversation`: integration pointed at vLLM's OpenAI-compatible endpoint
  - `wyoming`: discovers Parakeet + Kokoro services
- Minimal UI exposure — no Lovelace dashboards needed for users, only for debugging

## n8n

- **Docker container** on same host
- Role 1: package bus — receives packages from Cowork webhook, routes to HA voice delivery
- Role 2: deterministic action executor — Google Calendar, Gmail, Tasks, file writes to project pending_actions.md files
- Role 3: escalation bridge — packages fuzzy intents back to Cowork

## Version pinning

Pin all images to specific tags before first real-world use; do NOT run `:latest` in production. Versions tracked in `compose.yml`; major version upgrades go in `architecture_notes.md`.
