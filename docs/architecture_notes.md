# Voice Hub — Architecture Notes

> Append-only decision log. Newest entries at the top. Keep entries to 2–6 lines. Raw material for the eventual write-up; NOT a live architecture document (that's `spec.md`).

## 2026-05-25 — Sync research/reasoning via cloud n8n + OpenClaw

**Problem:** "Ask Claude to research X" went through the async mailbox path (local n8n → Samba inbox → Notification Agent hourly cron). Fine for project tasks, but ad-hoc research/reasoning questions had a ~1hr round-trip — useless for "what's the difference between X and Y" type queries.
**Decision:** Added synchronous `voice_query` tool alongside existing `escalate`. Two modes: (1) Research — Tavily web search → OpenClaw/Claude synthesis, (2) Reasoning — direct OpenClaw/Claude, no search. Both return TTS-friendly answer in real-time. Workflow runs on cloud n8n (`your-n8n-host.example.com`), same instance as Reputation Engine. OpenClaw via docker-bridge `host.docker.internal:18789`, Responses API format (`model: 'openclaw'`, `input: string`, `instructions: string`). Keys in workflow staticData, never in committed JSON.
**Alternative:** OpenRouter (has budget caps, model auto-routing). Deferred — OpenClaw already deployed and working. Can switch later if cost control becomes an issue.

## 2026-05-16 — WebRTC VAD, n8n escalation relay, multi-turn fix, voice inbox

**Problem:** Energy-based RMS VAD couldn't reliably detect end-of-speech on Voice PE hardware. The PE's AIC3204 codec AGC raises the noise floor to 250-400 RMS after speech, making silence indistinguishable from inter-word pauses. Tried threshold tuning, two-stage peak detection, per-satellite thresholds — none worked cleanly.
**Decision:** Replaced with Google's WebRTC VAD (`webrtcvad`, aggressiveness 3). Binary speech/non-speech per 30ms frame. Handles codec AGC artifacts. 0.5s silence duration on PEs, 1.5s minimum speech before silence detection starts (prevents wake word tail from triggering premature cutoff). Per-satellite `vad_silence_s` configurable.

**Problem:** ESPHome PE multi-turn broken — PE called `_handle_va_start` on continue_conversation, which created a new audio queue, orphaning the running pipeline.
**Decision:** Detect active pipeline in `_handle_va_start`, reuse existing audio queue for turn 2+. Multi-turn now works: response ending with "?" → PE keeps mic active → user speaks follow-up without wake word.

**Decision:** n8n deployed as pure relay (no classifier). Voice orchestrator keyword-gates escalation (Python-side), POSTs to n8n webhook, n8n validates + rate-limits + drops to unified mailbox at `/var/voicehub/mailboxes/inbox/`. All messages go `to: notification-agent` — NA decides routing with full Claude reasoning. Separate voice-inbox at `/var/voicehub/mailboxes/voice-inbox/` for responses back to user (any project can write directly).

**Decision:** Interactive voice inbox. "Check my inbox" triggers stateful multi-turn: message count → summaries → read individual → mark as read → next. Deterministic command parsing, no LLM involvement.

## 2026-05-07 — Pi satellite rebuilt: boot-loop root cause + systemd hardening

**Problem:** Pi was boot-looping. Investigation revealed: (1) an ad-hoc `respeaker-watchdog.service` was crashing on boot when ReSpeaker wasn't ready, (2) `wyoming-satellite` with `Restart=always RestartSec=3` crash-looped when `arecord` failed, causing CPU spikes that triggered undervoltage on the marginal 5V/3A PSU, killing the Pi.
**Decision:** Reflashed Pi with fresh Raspberry Pi OS Trixie (64-bit Lite). Key hardening: `Restart=on-failure` + `StartLimitBurst=3` + `StartLimitIntervalSec=60` on wyoming-satellite to prevent crash-loop resource exhaustion. `usbcore.autosuspend=-1` in cmdline.txt. Wifi via NetworkManager (nmcli). Pi IP now .22 (was .21). Official 27W PSU ordered.
**Status:** Full pipeline E2E working — Pi satellite + all 3 Voice PEs connected to orchestrator. ReSpeaker cold boot issue persists (needs physical USB replug).

## 2026-05-05 — Voice PE satellites working on custom orchestrator via ESPHome API + HTTP TTS

**Decision:** Voice PEs use ESPHome native API (port 6053) with `aioesphomeapi`, not Wyoming protocol. Key insight: Voice PE uses `media_player:` mode (not `speaker:`), so TTS audio must be served via HTTP URL — device downloads and plays it through its resampler/mixer/DAC chain (48kHz stereo AIC3204). Raw `send_voice_assistant_audio()` PCM is ignored in media_player mode.
**Architecture:** ESPHomeSatellite class connects to Voice PE as API client, subscribes to voice assistant. On wake word: receives audio stream → pipeline (STT/LLM/TTS) → writes WAV to aiohttp server on port 8081 → sends URL in TTS_END event → device HTTP-GETs and plays. Critical event sequence: STT_END → INTENT_START → INTENT_PROGRESS(tts_start_streaming=1) → INTENT_END → TTS_START → TTS_END(url) transitions device to STREAMING_RESPONSE state.
**Status:** PE #2 (bedroom, 192.168.1.20) fully working. PE #1 (living room, 192.168.1.21) needs BLE re-onboard after factory reset cleared encryption key. HA stopped — no longer needed for voice pipeline.

## 2026-05-05 — Migrate Voice PE satellites to custom orchestrator

**Decision:** Voice PE satellites will move from HA's voice pipeline to the custom orchestrator. HA pipeline adds ~650ms overhead (1.2-1.6s E2E) vs orchestrator's 301ms E2E. Requires multi-satellite support in the orchestrator (currently single-connection). Wake word unified to "hey sinabot" across all satellites — microWakeWord on Voice PEs (ESP32-S3), openWakeWord on Pi. HA remains for device control/automations but no longer handles voice pipeline for any satellite.

## 2026-05-05 — 4070 switched from wifi to ethernet via eero beacon

**Decision:** 4070 was connecting to wrong eero node (-61 dBm instead of -32 dBm nearby beacon). Fixed by BSSID pinning, then switched to Cat 8 ethernet into eero 6E beacon's 2.5G port. Wifi disabled (netplan `activation-mode: manual`), static IP set to `192.168.1.100`. Ping to router: 3.3ms. Samba share and Tailscale confirmed working. Old wifi IP was .36, now .35 everywhere.

## 2026-05-01 — Boot validation + systemd service deployment

**Decision:** Single systemd service `voicehub-orchestrator` on the 4070 runs `boot_check.py --wait --fix --start`. On boot or `systemctl restart`: validates full stack (15 checks across Pi satellite + 4070 GPU services + connectivity), retries every 30s for up to 5 min if services aren't ready, attempts auto-fixes (ALSA volume, openWakeWord restart, USB reset), then exec's orchestrator. Also installed SSH key from 4070→Pi for headless boot. Pi satellite Zeroconf disabled (`--no-zeroconf`) and HA config entry removed to prevent HA from competing for the satellite connection.

## 2026-05-01 — Orchestrator v0.2: streaming + multi-turn working

**Decision:** Added streaming LLM→TTS pipeline with sentence-level splitting. LLM tokens stream via SSE, split on sentence boundaries (`.` `?` `!`), each sentence sent to Kokoro TTS immediately while LLM continues generating. First audio reaches satellite ~300-400ms after STT. Multi-turn via conversation loop: after TTS plays, if response ends with `?`, pipeline loops back to VAD+STT instead of ending. Echo drain: wait for satellite "played" event, discard queued audio, 1s post-drain pause. VAD tuned: threshold 500 RMS (speech ~3000+, ambient ~150-300), 1.2s silence duration. Aux speaker on Pi headphone jack replaces BT speaker. Mic gain 10x.
**Issues:** Barge-in deferred — wake word can't be detected over TTS playback through aux speaker (no AEC path). Needs energy-based barge-in detection during TTS. ReSpeaker cold boot audio failure persists (USB power cycle required).

## 2026-04-28 — Custom orchestrator v0.1: first E2E voice through Pi satellite

**Decision:** Custom Python orchestrator (`code/orchestrator/`) connects to wyoming-satellite as TCP client, bypasses HA pipeline entirely. Architecture: satellite connection + server-side VAD (energy-based, threshold 1000 RMS after 5x gain) → WhisperLive TRT direct WebSocket (no Wyoming bridge) → vLLM non-streaming → Kokoro Wyoming TTS → audio stream back to satellite. First live test: 1003ms E2E (STT 268 + LLM 412 + TTS 323). HA disabled for Pi satellite (Zeroconf off, config entry removed). vLLM at 0.55 gpu-memory-utilization; all three models on GPU (11,312/12,282 MiB).
**Issues found:** (1) ReSpeaker XVF3800 needs USB power cycle on cold boot. (2) openWakeWord systemd service unreliable without `--debug` flag — works after fresh start but flaky. (3) `scp -r` doesn't always propagate file changes — must clear `__pycache__` and copy individual files. (4) Satellite doesn't send AudioStop when using local wake word — server must implement its own VAD.
**Next:** LLM streaming + sentence-level TTS splitting to cut the 412ms LLM + 323ms TTS into overlapping pipeline.

## 2026-04-27 — HA pipeline adds ~650ms overhead; custom orchestrator planned

**Problem:** Direct service calls achieve P50 E2E 299ms (STT 169 + LLM TTFT 24 + TTS TTFA 122). But the HA assist pipeline measures 1.2–1.6s from VAD-end to TTS-complete. Root cause: HA calls LLM non-streaming (waits for full response ~200-450ms at 83 tok/s) then synthesizes full TTS before streaming to satellite. Patching Extended OpenAI Conversation to use `stream=True` didn't help because the v1 architecture still assembles the complete response before passing to the pipeline.
**Decision:** Build a custom streaming voice orchestrator (Python on 4070) that drives wyoming-satellite directly, bypassing HA's pipeline. HA remains for device control, automations, and intent execution via n8n webhooks. The orchestrator will stream STT→LLM→TTS with sentence-level splitting, targeting <350ms E2E.

## 2026-04-27 — Pi satellite live: ReSpeaker + Sony BT + wyoming-satellite

**Decision:** Pi 4 running wyoming-satellite (port 10700) with local openWakeWord (ok_nabu, port 10400), Silero VAD, ReSpeaker XVF3800 mic via powered USB hub (plughw:3,0, 5x software gain), Sony SRS-XB100 output via Bluetooth A2DP (bluealsa). Onboarded in HA via Zeroconf.
**Issues:** BT speaker disconnects when idle (no auto-reconnect yet). ReSpeaker outputs quiet audio (-38 dBFS peak) due to AEC — needs software gain. Powered aux speaker arriving tomorrow will eliminate BT reliability issues.

## 2026-04-23 — ESPHome Dashboard added to compose stack

**Decision:** Added ESPHome Dashboard container (port 6052, `--profile esphome`) to `compose.yml`. Used to compile and OTA-flash Voice PE firmware with custom Sinabot wake word. Voice PE config is a full copy of the official `home-assistant-voice.yaml` (dev branch) with the `micro_wake_word` models section modified to include sinabot.
**Why it matters:** Enables future firmware updates (wake word tuning, sensitivity, new features) without USB access. ESPHome Dashboard is the management plane for all ESP32-S3 satellites.

## 2026-04-23 — Custom "Sinabot" microWakeWord model trained and deployed

**Decision:** Trained custom microWakeWord model on the 4070 (CPU, OHF-Voice/micro-wake-word pipeline). 3000 synthetic samples via Piper TTS (3 phonetic variants), augmented with MIT RIRs + FMA + AudioSet. 15K training steps, MixedNet 64x4 architecture. Quantized INT8 TFLite, 60.8 KB. Deployed to Voice PE #1 via ESPHome OTA. probability_cutoff=0.5 (sensitivity: "Slightly sensitive" maps to 0.85 via the Voice PE sensitivity select).
**Why it matters:** "Sinabot" wake word is live on-device. Zero latency, zero server load. STT accuracy issue noted: "dismiss" → "This miss" (Whisper short-utterance weakness, not wake word related).

## 2026-04-23 — Wake word engine: openWakeWord → microWakeWord

**Problem:** Original decision (2026-04-20) rejected microWakeWord as "limited to pre-trained phrases, no custom training." This was wrong — microWakeWord has supported custom model training since mid-2024 via OHF-Voice/micro-wake-word, and microwakeword.com now trains models in minutes on serverless GPU.
**Decision:** Switch to microWakeWord. It is the native wake word engine on Voice PE (ESP32-S3), runs on-device with zero network latency and zero server load (~35-45 kB, <10ms inference). openWakeWord cannot run on ESP32-S3 and requires either a community firmware fork or server-side Wyoming container. Custom "Sinabot" model being trained via microwakeword.com, deployed by adding model URL to ESPHome YAML.
**Why it matters:** Eliminates the open-voice-pe firmware dependency, removes 5-15ms WiFi hop for wake detection, and frees server CPU. The native path is simpler, faster, and more maintainable.

## 2026-04-21 — Multi-turn conversational voice assistant working end-to-end
**Decision:** Updated system prompt from single-shot command style to "chief of staff" conversational style. LLM now ends responses with follow-up questions ("Anything else?"), which triggers `continue_conversation=true` in Extended OpenAI Conversation (detected via trailing `?`). Voice PE reopens mic without wake word. HA preserves `conversation_id` and chat history across turns automatically.
**Why it matters:** First proof-of-life for the target experience: natural multi-turn voice conversation with schedule queries, meeting management, email composition — all handled by Llama 8B with conversational context. Latency feels snappy even without streaming partials. The voice interaction model is now "chief of staff" not "smart speaker." Silence-on-followup automation pinned for later (graceful conversation close when user doesn't respond).

## 2026-04-21 — All three models on GPU: 320ms E2E, full 12 GB utilized
**Problem:** Initial Kokoro GPU attempt failed — TRT fp16 (batch=4, beam=4) used 3.9 GB runtime, leaving no room. INT8 compilation crashed (TRT-LLM 0.18.2 bug).
**Decision:** Recompiled TRT Whisper fp16 with batch=1, beam=1 ("slim") — saved ~200 MiB in runtime buffers (3720 vs 3912). Combined with Kokoro GPU (728 MiB) and vLLM at 0.58 (7218 MiB), total VRAM is 11.7 GB / 12.3 GB (155 MiB free). E2E warm latency dropped from 560ms → **320ms** — below the best-case target. TTS TTFA went from 370ms (CPU) to 108ms (GPU), a 3.4x speedup.
**Risk:** Only 155 MiB free VRAM. Any transient allocation spike could OOM. Monitor for stability. Escape hatch: revert Kokoro to CPU (adds ~260ms to E2E but frees 728 MiB).

## 2026-04-21 — Kokoro GPU blocked by 12 GB VRAM ceiling
**Problem:** Kokoro TTS TTFA is ~370ms on CPU (2x over 80-200ms budget). Attempted GPU move: built `kokoro-gpu` image (onnxruntime-gpu + CUDA pip libs), Kokoro uses ~706 MiB on GPU. But WhisperLive TRT uses 3.9 GB (engines 574 MB, rest is TensorRT-LLM runtime/buffers), vLLM needs 5.35 GB model + KV cache. Total exceeds 12 GB — vLLM crash-loops with "no available memory for cache blocks."
**Decision:** Reverted Kokoro to CPU. Kokoro GPU image (`kokoro-gpu:latest`) and compose config are ready for when VRAM frees up (TRT INT8 Whisper recompile would reclaim ~1-1.5 GB, or 24 GB GPU upgrade). Current E2E warm ~560ms is within the 350-600ms target despite TTS being the bottleneck.

## 2026-04-21 — TensorRT Whisper small.en: 170ms STT, 13x speedup
**Decision:** Built `whisperlive-tensorrt` image locally (26.6 GB, tensorrt_llm 0.18.2). Compiled Whisper small.en to TensorRT float16 engine (encoder 175 MB + decoder 399 MB). Single model mode (model loaded once, shared across connections). Wyoming bridge updated to detect TRT segments (no `completed` field) and stabilize-on-repeat for final text.
**Why it matters:** STT latency dropped from ~2200ms (faster_whisper) to ~170ms (TensorRT) — now well within the 50-200ms budget. Biggest optimization lever remaining is streaming partials (overlap STT with user speech). Minor issue: long phrases occasionally truncated due to TRT decoder max_output_len=96 tokens.

## 2026-04-21 — WhisperLive + Wyoming bridge working on GPU (faster_whisper backend)
**Problem:** WhisperLive (collabora GPU image) deployed but Wyoming bridge was broken — three bugs: (1) `AsyncEventHandler.__init__()` not passed `reader`/`writer`, (2) `END_OF_AUDIO` sent as text not bytes, (3) `np.interp` resampling returned float64 instead of float32 garbling audio. Also: container shipped CUDA 13 libs but CTranslate2 needed `libcublas.so.12` — fixed via symlink entrypoint.
**Decision:** All four bugs fixed. WhisperLive running faster_whisper backend on GPU, Whisper small.en, ~2.2s per utterance (batch mode). This is above the <1s STT target because WhisperLive buffers ≥1s of audio before processing. Next step: TensorRT compilation to speed up inference, then streaming partials to overlap STT with LLM.
**Why it matters:** Proves the full GPU STT pipeline works (Wyoming → bridge → WhisperLive WS → faster_whisper CUDA → transcript). The 2.2s is the batch-mode baseline; streaming partials will be the real latency win since the LLM can start processing before the user finishes speaking.

## 2026-04-20 — STT optimization path: WhisperLive + TensorRT, GPU upgrade as escape hatch
**Decision:** Current Whisper small.en on GPU works but lacks streaming partials (needed for speculative execution). Next optimization: WhisperLive (collabora, 4000+ stars) with TensorRT-compiled Whisper small.en on GPU — restores streaming partials at ~1-2 GB VRAM. Wyoming wrapper needed (WebSocket → Wyoming bridge). Canary-Qwen 2.5B evaluated as combined STT+LLM but 1.7B Qwen decoder too weak for intent classification vs our 8B Llama. If 12 GB VRAM becomes the binding constraint (e.g., for Gemma 4 E4B multimodal or Parakeet on GPU), upgrade to 24 GB card (RTX 4090 or 5080).
**Why it matters:** Streaming partials are the key latency optimization — LLM starts processing before user finishes speaking. WhisperLive is the lowest-risk path to restore this within 12 GB. GPU upgrade is the escape hatch that unlocks audio-native models.

## 2026-04-20 — First working end-to-end voice pipeline
**Decision:** Voice PE → HA (microWakeWord "okay nabu") → Faster Whisper small.en (CPU, Wyoming) → Llama 3.1 8B INT4 (GPU, vLLM, Extended OpenAI Conversation) → Kokoro 82M af_bella (CPU, Wyoming) → Voice PE speaker. HA Container with privileged mode + D-Bus + BlueZ for Bluetooth/Improv BLE satellite onboarding.
**Why it matters:** First proof-of-life for the entire pipeline. Latency not yet optimized but functional. Parakeet 0.6B couldn't share GPU with Llama INT4 (~4.5 GB actual vs ~2.8 GB reported); Whisper small.en on CPU is the working fallback. GPU STT remains a future optimization target (CUDA-enabled Whisper image or 24 GB card).

## 2026-04-20 — Llama 8B INT4 + Parakeet 0.6B dual-GPU deployment
**Decision:** Switched vLLM from Llama 3.1 8B FP8 (8.5 GiB, 51 tok/s) to INT4/W4A16 (5.35 GiB, 89 tok/s). This freed ~4.8 GiB for Parakeet TDT 0.6B to run on GPU alongside the LLM. Both models share the RTX 4070 Super (12 GB). vLLM at 0.60 gpu-memory-utilization.
**Why it matters:** INT4 is 73% faster than FP8 (memory-bandwidth-bound workload) with negligible accuracy loss for classification/extraction. Gemma 4 E4B (multimodal, audio-native) was evaluated but OOMs on 12 GB even at INT4 — the audio/vision encoders stay in bf16. Revisit when 24 GB cards or smaller audio models are available.

## 2026-04-20 — Wake word engine: openWakeWord everywhere, on-device preferred
**Decision:** openWakeWord as the sole wake word engine across all zones. HA Voice PE satellites get open-voice-pe custom ESPHome firmware (github.com/mike-nott/open-voice-pe) for on-device detection on the ESP32-S3. Pi/ReSpeaker runs openWakeWord natively on Linux. Fallback: if open-voice-pe proves unreliable, fall back to openWakeWord streaming on the 4070 via Wyoming (audio always-streaming, +5-15ms latency).
**Why it matters:** Custom wake phrase is a hard requirement — rules out microWakeWord (limited pre-trained set) and Porcupine (no ESP32-S3 support). openWakeWord is free, self-trainable, and open-voice-pe enables on-device detection preserving the "audio only streams post-wake" privacy model. Wake phrase TBD.

## 2026-04-19 — vLLM benchmark baseline: ~51 tok/s on RTX 4070 Super
**Decision:** neuralmagic/Meta-Llama-3.1-8B-Instruct-FP8 via vLLM 0.19.1 on RTX 4070 Super (12GB, driver 590.48.01 / CUDA 13.1). Benchmarked at 51.6/51.4/51.2 tok/s at 512/1024/2048 context — throughput is flat, confirming we're compute-bound not memory-bound at these lengths. At 51 tok/s, a 50-token intent classification response takes ~1s, well within the latency budget for the LLM hop.
**Why it matters:** This is the throughput baseline for latency_budget.md. FP8 quantization trades negligible accuracy for ~2x throughput vs bf16 — correct choice for classification/extraction workload. Driver was upgraded from 570→590 to support vLLM's CUDA 12.9 runtime.

## 2026-04-19 — Agent orchestration frameworks (OpenClaw, Hermes, NeMo Agent) rejected
**Decision:** No agent orchestration layer between n8n and vLLM. Evaluated OpenClaw (consumer-focused, messaging integrations + skills), Hermes Agent (self-improving local LLM agents), and NeMo Agent Toolkit (enterprise multi-framework orchestration). All add probabilistic tool selection where we have deterministic n8n routing, and duplicate the fuzzy-intent handling that Cowork/Claude already does better.
**Why it matters:** The 8B's job is classification + extraction, not autonomous tool use. n8n handles deterministic actions; Claude handles complex reasoning via Cowork escalation. An agent framework would add latency, maintenance surface, and a second probabilistic layer without improving capability.

## 2026-04-19 — Claude Code on the 4070 is a tool, not an autonomous actor
**Decision:** Claude Code on the box is available for human-initiated or voice-initiated-with-confirmation tasks (e.g., "run the benchmark suite"). It must NOT be placed in an automated n8n loop or triggered without a human checkpoint. Fuzzy intents escalate to Cowork (which has human oversight), not to a local Claude Code invocation.
**Why it matters:** Stacking two probabilistic layers (8B classification → Claude Code execution) with no human gate violates the deterministic-action contract in the action taxonomy. The Mac remains the command center for spec/planning; the box-local Claude Code is insurance + a scoped script runner for tasks with locked-down `--allowedTools`.

## 2026-04-19 — nvidia driver: 570 → 590 upgrade for vLLM CUDA 12.9 compatibility
**Decision:** Initially installed nvidia-headless-570-server-open (driver 570.211.01, CUDA 12.8). vLLM 0.19.1 ships CUDA 12.9 runtime which requires driver ≥575. Upgraded to nvidia-headless-590-server-open (driver 590.48.01, CUDA 13.1). Console display restored via GRUB params `nvidia-drm.modeset=1 nvidia-drm.fbdev=1`.
**Why it matters:** Always pin to `vllm/vllm-openai:latest` for performance fixes; keep the host driver ahead of the container's CUDA runtime. Open kernel module variant avoids DKMS rebuild issues on kernel upgrades.

## 2026-04-18 — Dual-boot physical layout: one drive per OS
**Decision:** Aurora R16 has two NVMe drives. Ubuntu Server takes Disk 0 entirely (was D:, wiped); Windows stays on Disk 1 (C:, ~935 GB SSD with EFI partition) untouched. GRUB will be installed on Disk 0's own EFI partition, not on Disk 1's. OS switching via BIOS one-time boot menu (F12), with Disk 0 as default boot.
**Why it matters:** Maximally safe dual-boot — no Windows partition shrinking, no shared bootloader, no risk that a Windows update reclaims GRUB. The Voice Hub box can be treated as a Linux box with Windows as a recoverable side-disk.

## 2026-04-18 — BIOS prep for Ubuntu install
**Decision:** Switched Aurora R16 storage controller from "RAID On" (Intel VMD) to AHCI/NVMe; disabled Secure Boot; no Fast Boot setting present in this BIOS rev.
**Why it matters:** VMD hides NVMe drives from non-Windows installers. AHCI is required for Ubuntu to see Disk 0. Secure Boot off avoids signing-key friction with nvidia drivers and any future kernel modules. Trade-off recorded: Secure Boot disable wiped TPM-bound Windows Hello PIN; Windows login is currently broken. Deferred recovery via separate task — does not block Voice Hub work.

## 2026-04-18 — Initial spec lock
**Problem:** Original voice spec (docx) had HA sitting between n8n and the AI layer and treated n8n as generic "execution." This conflated HA's role (pipeline orchestration) with n8n's role (action execution + package bus).
**Decision:** HA is the STT → conversation agent → TTS pipeline orchestrator; vLLM serves Llama 3.1 8B fp8 as the conversation agent via OpenAI-compatible API; n8n sits AFTER the LLM returns a structured intent and handles deterministic actions / fuzzy-intent escalation to Cowork.
**Why it matters:** This makes HA a thin protocol layer we can replace later (e.g., swap for a custom orchestrator) without touching the package protocol or the action taxonomy. It also clarifies that the 8B's job is classification + extraction, not generation.

## 2026-04-18 — Dual-boot Ubuntu on the simracing rig
**Decision:** Install Ubuntu Server alongside existing Windows on the i9/4070 box rather than WSL2 or Proxmox.
**Why it matters:** vLLM is Linux-native and WSL2 breaks mDNS discovery that HA Voice PE satellites need. Dual-boot keeps Windows for simracing with zero virtualization overhead. Proxmox was considered but the operational cost didn't justify the flexibility given single-user scope.

## 2026-04-18 — Separate git repo for `code/`
**Decision:** `code/` inside the Voice Hub folder is its own git repo, cloned and pulled on the 4070 host. Spec + design files in the parent folder are backed up by the Notification Agent's existing backup job.
**Alternative considered:** Monorepo with spec + code in the same history.
**Why it matters:** Implementation changes (n8n workflow exports, compose edits, prompt files) will generate a lot of history that would drown out spec-level decisions if co-mingled. Separation keeps the spec reading clean.
