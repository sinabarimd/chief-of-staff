# Test Plan

Three concerns, in priority order: **latency**, **intent accuracy**, **failure behavior**.

## 1. Latency

See `latency_budget.md` for targets and measurement plan.

Bench harness: a synthetic test suite that plays recorded utterances through the pipeline and measures each hop. Run nightly via cron on the 4070 host.

Test cases:
- **L1** — short command ("dismiss") — best case, expect P50 ≤ 300ms
- **L2** — medium command ("add dentist appointment Monday 2pm") — typical case, expect P50 ≤ 500ms
- **L3** — long query ("what's on my calendar today and what's overdue") — escalation case, expect P50 ≤ 800ms for first audio (holding reply)
- **L4** — barge-in during TTS — expect TTS stop ≤ 100ms after new speech onset
- **L5** — cold start (first utterance after 5-min idle) — expect ≤ 2× warm latency

## 2. Intent accuracy

Build a labeled dataset of 200+ utterances across deterministic and fuzzy intents (see `action_taxonomy.md`). Track:

| Metric | Target |
|---|---|
| Deterministic intent classification accuracy | ≥ 95% |
| Fuzzy intent detection (correctly escalates instead of guessing) | ≥ 98% |
| Argument extraction accuracy (dates, times, contacts, project names) | ≥ 90% |
| False-positive action execution rate (wrong action taken on committed transcript) | ≤ 1% |

Test cases:
- **I1** — all deterministic intent rows from `action_taxonomy.md` × 5 paraphrases each
- **I2** — intentional ambiguity ("the meeting with Jane" when multiple Janes exist) — expect escalation, not guess
- **I3** — missing required args ("add a calendar event next week") — expect follow-up question or escalation
- **I4** — adversarial ("mark the thing done") — expect escalation
- **I5** — partial transcript dry-runs — verify speculative work never executes actions

## 3. Failure modes

For each failure, verify:
- Voice system gives a coherent response (not silence, not a stack trace)
- Package state is preserved (no lost briefings)
- Recovery is automatic where possible

Test cases:
- **F1** — vLLM down → fallback to Claude API via n8n (if policy is set to that) OR graceful "my local brain is offline, I'll save this for later"
- **F2** — Parakeet down → graceful error, tell user to try again in a moment
- **F3** — Kokoro down → text-only notification to the user's phone (if that fallback is wired)
- **F4** — n8n down → local queue on HA; replay on recovery
- **F5** — network drop mid-utterance → HA Voice PE buffers locally; recovery tests
- **F6** — Cowork webhook 5xx on escalation → retry with backoff, fall back to "I couldn't reach the hub, please try again"
- **F7** — Corrupt package (malformed JSON) → log + skip, don't break the pipeline
- **F8** — Expired package arrives → silently drop, log
- **F9** — Two zones both try to deliver the same package → dedup by `package_id`, only one speaks

## 4. Real-world tests (after lab passes)

- **R1** — Morning briefing delivered at 7am in bedroom → verify on-time, audible, appropriate volume
- **R2** — Main-room conversation during cooking/TV noise → ReSpeaker far-field performance
- **R3** — Multiple zones simultaneously (someone in bedroom talking, someone in family room talking) → no cross-talk
- **R4** — Multi-turn interaction ("add dentist appointment" → "what time?" → "2pm") — context carry within a session

## Regression tracking

Each test case has a row in a benchmarks spreadsheet (TBD: decide where this lives — `code/benchmarks/` or a shared sheet). Weekly run, delta from previous week logged, any regression of >10% opens a pending_actions.md item.
