# Latency Budget

End-to-end target: **<1000ms** max acceptable, **350–600ms** typical, **250–430ms** best case, measured from end-of-user-speech to start-of-TTS-playback.

## Per-hop allocation (typical case target)

| Stage | Budget (ms) | Notes |
|---|---|---|
| Mic capture + VAD end-of-speech detection | 50–100 | Depends on silence threshold; lower = snappier but more false endpoints |
| Network: Pi → 4070 (WiFi) | 5–15 | WiFi; wired ethernet would halve this |
| STT final commit (Parakeet, post-partial work) | 50–100 | Partials have been flowing continuously, so only the "commit" step costs here |
| Local 8B (Llama 3.1 fp8 via vLLM): intent classification + first token | 100–200 | Depends on context length; prefix caching helps |
| First n8n / HA decision | 10–30 | Just routing, no I/O |
| TTS (Kokoro) time-to-first-audio | 80–200 | Streaming — audio starts playing before synthesis completes |
| Audio out to speaker | 20–50 | AUX + speaker buffer |
| **Sum (typical)** | **~350–600** |  |

## Per-hop allocation (best case)

Short utterance ("yes", "dismiss", "volume up"), cache-warm, no network contention:

| Stage | Budget (ms) |
|---|---|
| Mic capture + VAD | 50 |
| Network | 5 |
| STT commit | 40 |
| 8B (one-shot classifier, cached prefix) | 60 |
| Routing | 5 |
| TTS first audio | 80 |
| Speaker | 20 |
| **Sum (best)** | **~260** |

## Measurement plan

- Instrument each hop with timestamps; log JSON to a local file per utterance
- Weekly rollup in `latency_budget.md` with P50 / P95 / P99 across zones
- Regression gate: any hop that drifts >20% above its target budget for 2+ weeks triggers a `## YYYY-MM-DD` entry in `architecture_notes.md` and a dedicated investigation task in `pending_actions.md`

## Known risks

- **WiFi jitter** — will dominate once we deploy satellites across the house. Mitigation: wired 4070 box, 5GHz only for satellites, QoS tagging.
- **vLLM cold start** — first request after model load is slow. Mitigation: keepalive pings every 60s; warm prefix cache with common system prompts.
- **GPU contention** — if simracing resumes, 4070 can't serve voice. Mitigation: document as an explicit "voice off" mode.
- **Kokoro chunk size** — smaller chunks = lower latency but more audio artifacts. Tune in week 2.

## Actuals (to be filled in as we benchmark)

| Date | Stage | Target | P50 | P95 | Notes |
|---|---|---|---|---|---|
| 2026-04-21 | STT (WhisperLive, faster_whisper GPU, batch, 1.0s buf) | 50–100ms | ~2200ms | ~2400ms | Batch mode via Wyoming bridge. WhisperLive re-transcribes full buffer ~6× per utterance. espeak-ng synthetic speech, 3 runs. |
| 2026-04-21 | STT (WhisperLive, faster_whisper GPU, batch, 0.5s buf) | 50–100ms | ~2240ms | — | Lowered min buffer from 1.0→0.5s. No meaningful change in batch mode (repeated inference dominates). Will matter for streaming. |
| 2026-04-21 | STT cold start (first request, model load) | — | ~2500ms | ~16600ms | First request loads Whisper small.en to GPU. 16.6s worst case (new container). |
| 2026-04-21 | **STT (WhisperLive, TensorRT GPU, batch, 0.5s buf)** | 50–100ms | **~170ms** | ~213ms | **13x speedup** vs faster_whisper. TRT small.en float16 engine, single model mode. espeak-ng, 10+ runs across phrases. |
| 2026-04-21 | **LLM TTFT (vLLM, Llama 8B INT4, warm)** | 100–200ms | **~31ms** | ~56ms | Warm with prefix caching. Cold first request ~548ms. |
| 2026-04-21 | **TTS TTFA (Kokoro 82M, CPU/ONNX, warm)** | 80–200ms | **~374ms** | ~403ms | **Bottleneck.** 2x over budget. Cold first request ~2930ms. CPU inference is the constraint. |
| 2026-04-21 | E2E to first audio (warm, programmatic, CPU TTS) | 350–600ms | ~560ms | ~642ms | STT+LLM TTFT+TTS TTFA. No mic/speaker/WiFi hops. Kokoro CPU TTS was the dominant hop (~65% of E2E). |
| 2026-04-21 | **TTS TTFA (Kokoro 82M, GPU/ONNX, warm)** | 80–200ms | **~108ms** | ~109ms | **3.4x faster** than CPU (370ms→108ms). onnxruntime-gpu CUDAExecutionProvider, ~728 MiB VRAM. |
| 2026-04-21 | **E2E to first audio (warm, programmatic, GPU TTS)** | 350–600ms | **~320ms** | ~320ms | **Below best-case target (260ms).** STT 171 + LLM 35 + TTS 108. All three models on GPU. 155 MiB VRAM free. |
| 2026-04-21 | **E2E Voice PE live test (multi-turn, subjective)** | <1000ms | "snappy" | — | Multi-turn conversation confirmed natural. Schedule queries, meeting ops, email — LLM handles conversational context well. No partials yet. |
| 2026-04-23 | **STT (WhisperLive TRT, warm)** | 50–100ms | **~180ms** | ~192ms | Consistent with Apr 21 baseline (170ms). "dismiss" → 120-131ms (shorter audio). Benchmark script: `~/voicehub/latency_bench.py`. |
| 2026-04-23 | **LLM TTFT (vLLM, Llama 8B INT4, warm)** | 100–200ms | **~24ms** | ~25ms | Improved from 31ms baseline. Cold: 27ms. Stable across all phrases. |
| 2026-04-23 | **TTS TTFA (Kokoro 82M, GPU/ONNX, warm)** | 80–200ms | **~125ms** | ~175ms | Slight regression from 108ms baseline (Apr 21). Cold: 131ms. |
| 2026-04-23 | **E2E to first audio (warm, programmatic, all GPU)** | 350–600ms | **~313ms** | ~342ms | STT 180 + LLM 24 + TTS 125. Comparable to Apr 21 baseline (320ms). Cold: 352ms. Benchmark: `~/voicehub/latency_bench.py`. |
| 2026-05-05 | **STT (WhisperLive TRT, warm)** | 50–100ms | **~173ms** | ~195ms | Stable vs Apr 23 (180ms). "dismiss" → 124-134ms. 4070 now on ethernet (was wifi). |
| 2026-05-05 | **LLM TTFT (vLLM, Llama 8B INT4, warm)** | 100–200ms | **~24ms** | ~25ms | Identical to Apr 23. Cold: 61ms. |
| 2026-05-05 | **TTS TTFA (Kokoro 82M, GPU/ONNX, warm)** | 80–200ms | **~126ms** | ~144ms | Stable vs Apr 23 (125ms). Cold: 1510ms (Kokoro idle, ONNX session cold). |
| 2026-05-05 | **E2E to first audio (warm, programmatic, all GPU)** | 350–600ms | **~301ms** | ~350ms | STT 173 + LLM 24 + TTS 126. Cold: 1763ms (TTS dominated). ReSpeaker + hub restored after cable fix. |
