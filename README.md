# Chief of Staff

A personal operating system for one physician-technologist: voice-first, local-first, multi-agent.

This repo is the reference implementation behind [The Chief of Staff: Building a Local Voice Agent as a Personal Operating System](https://drsinabari.com/articles/chief-of-staff-personal-operating-system.html). The article describes the architecture, the coordination protocols, the production failures, and what the system costs to run; this repo is the running code those decisions produced.

It is a *reference* implementation, not a product. The wiring is opinionated to the author's house and reading life. The point of publishing it is the design, not the deployment.

## What's in here

```
orchestrator/                       custom Python voice orchestrator (~2,900 lines)
├── pipeline.py                     streaming STT → LLM → TTS pipeline
├── esphome_satellite.py            ESPHome Voice PE bridge
├── satellite.py                    Wyoming protocol Pi satellite
├── stt.py / tts.py / llm.py        engine adapters
├── intent_router.py                keyword-gated tool routing
├── voice_inbox.py                  voice-bound mailbox responses
├── escalation_offer.py             escalate-tool offer logic
├── boot_check.py                   15-check pre-flight (refuses launch on real failures)
├── session.py / audio.py / web_search.py
├── config.py                       env-driven configuration
├── voicehub-orchestrator.service   systemd unit
└── README.md

n8n-workflows/                      exported n8n workflow JSON
├── voice-escalate.json             local n8n relay → mailbox drop
└── voice-research.json             cloud n8n research+reasoning router

models/                             wake-word models (publishable as-is)
├── Hey_Sinabot_microWakeWord.tflite
└── Hey_Sinabot_openWakeWord.onnx

docs/                               architecture + design context
├── architecture_notes.md           append-only decision log
├── spec.md                         overall system spec
├── software_stack.md               component list
├── hardware_setup.md               host + satellite hardware
├── latency_budget.md               per-hop budgets and measured actuals
├── action_taxonomy.md              intent taxonomy
├── package_protocol.md             inbound briefing-package schema
├── voice-hub-n8n-architecture.md   n8n side
└── test_plan.md

scripts/                            Pi rescue + backup
├── pi_backup_reference.md
└── pi_wifi_rescue.sh

PUBLISH_MANIFEST.md                 allowlist for what /push-repo syncs from the parent project root
```

## Design principles

The five rules that survived more than one revision:

1. **Voice is a terminal, not a reasoner.** The 8B local model never composes content. It classifies intent, extracts arguments, and reads pre-baked TTS-ready strings verbatim from a shared `daily.json` package. Hallucination by paraphrase is eliminated by moving formatting upstream to the single state authority.
2. **Tools are keyword-gated before the model sees them.** If your utterance contains nothing that could plausibly want a tool, the tool isn't in the model's tool list for that turn. You cannot hallucinate a tool you were never offered.
3. **Causality chain + hop counter + idempotency keys + agent-of-record.** Four loop-prevention primitives, modeled on routing-protocol patterns, enforced independently by every agent on its own inbox plus a weekly executive sweep.
4. **Append first, destroy after.** Write the new state, verify it, then trim the old. A crash in this order costs a duplicate; the reverse costs the data.
5. **Surface but don't process.** The executive layer sees all six domains and flags stuck items, but never does another agent's domain work. Cross-domain visibility, no cross-domain mutations.

The article walks through how each rule was learned, usually by breaking something.

## Running it

This isn't a one-click install — the voice path assumes a specific hardware shape (a single consumer GPU box, ESPHome Voice PE satellites or a Raspberry Pi with ReSpeaker, Wyoming + ESPHome native API services, vLLM, WhisperLive, Kokoro). The architecture is portable; the specific bringup is left as an exercise tuned to your house.

The minimum viable shape:

- A box with a ≥12 GiB GPU, ≥32 GiB system RAM
- WhisperLive (Whisper small.en, TensorRT FP16)
- vLLM serving Llama 3.1 8B INT4
- Kokoro 82M for TTS, on GPU
- ESPHome native API satellites and/or a Wyoming-protocol Pi
- An n8n instance (local for `voice-escalate`, optionally a separate instance for `voice-research`)

Configuration lives in `orchestrator/config.py` (env-driven, with sensible defaults). Wire up the n8n side using the exported workflow JSONs in `n8n-workflows/`.

`orchestrator/boot_check.py --wait --fix --start` runs the 15-check pre-flight: a real failure refuses launch; a fixable one (stuck USB mic, muted mixer, dead wake-word service) tries to recover before declining. Nothing in the runtime is trusted because it was configured — only because it was checked.

## What you won't find here

- The state authority (`pending_actions.md` per project, the executive Notification Agent's sync engine). That layer is the operator's house specifically and the value is in the design, not the code; see the article.
- The six domain agents themselves. They're Cowork projects with private context.
- Live state from the operator's box. The reference is the design.

## License

MIT.

## Related

- Article: [The Chief of Staff: Building a Local Voice Agent as a Personal Operating System](https://drsinabari.com/articles/chief-of-staff-personal-operating-system.html)
- Previous spotlight: [How I Built a Personal Reputation Engine with AI Agents](https://sinabarimd.com/articles/how-i-built-a-personal-reputation-engine.html) (the system that publishes this article).
- Author: [Dr. Sina Bari, MD](https://sinabarimd.com/about) — Stanford-trained plastic surgeon and VP of Medical AI at iMerit. Writes about medicine, technology, and building things at [sinabarimd.com](https://sinabarimd.com).
