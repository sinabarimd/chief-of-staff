# Action Taxonomy

The single most important design artifact in this project. Defines what the local 8B can execute deterministically (sub-second, via n8n) vs. what must escalate to Cowork (multi-second, via Claude + MCPs).

## Routing rule

Given a committed STT transcript, the local 8B emits a structured response:

```json
{
  "intent": "add_calendar_event | mark_task_complete | ... | escalate",
  "confidence": 0.0-1.0,
  "args": { ... },
  "speak": "short acknowledgment to TTS"
}
```

Route:
- `confidence ≥ 0.8` AND `intent ∈ deterministic_set` AND all required args present → n8n executes directly
- Otherwise → package to Cowork with the transcript + intent guess + any partial args

Thresholds are tunable; start at 0.8 and measure false-positive rate (wrong action taken) before loosening.

## Deterministic intents (n8n executes)

| Intent | Required args | n8n target | Example utterance |
|---|---|---|---|
| `add_calendar_event` | title, start_iso, duration_min, calendar_id | Google Calendar API | "Add a dentist appointment Monday 2pm for 1 hour" |
| `add_task` | project, description, due_iso? | append to project's pending_actions.md | "Add to family budget: reconcile June statements Wednesday" |
| `mark_task_complete` | project, task_match_string | sync engine | "Mark the reconcile task done" |
| `reschedule_focus_block` | block_id, new_start_iso | Google Calendar API | "Move my 11 focus block to 1pm" |
| `send_canned_message` | contact, template_id, template_args | Gmail / SMS | "Text the team I'm running 15 minutes late" |
| `set_timer` / `set_reminder` | duration_min or time_iso, label | local n8n state | "Remind me in 20 minutes to take the chicken out" |
| `media_control` | zone, action (play/pause/volume/skip) | HA media services | "Pause in the family room" |
| `query_today` | aspect (meetings / deadlines / inbox_count) | read cached briefing package | "How many meetings do I have today?" |
| `dismiss_package` | package_id | n8n package-store | "Dismiss the last alert" |

## Read-only query intents (orchestrator fetches context, LLM reads it)

These don't execute actions — they inject pre-built context into a follow-up LLM call so it can answer naturally.

| Intent | Source | Example utterance |
|---|---|---|
| `query_calendar` | `/var/voicehub/context/calendar.json` (pushed by Notification Agent) | "What's on my schedule Thursday?" |
| `query_todos` | `/var/voicehub/context/todos.json` (pushed by Notification Agent) | "What tasks are due this week?" |
| `query_budget` | `/var/voicehub/context/budget.json` (pushed by Family Budget agent) | "How much did I spend on groceries?" |
| `query_shopping` | `/var/voicehub/context/shopping.json` (pushed by Family Assistant) | "What's on the shopping list?" |
| `web_search` | n8n workflow → search API (SearXNG/Brave/Perplexity) | "What's the weather in Toronto?" / "What time does Costco close?" |

Route: `confidence ≥ 0.8` AND `intent ∈ query_set` → orchestrator reads context file (or calls n8n for web search) → injects result into new LLM call with user's question → TTS. If context file is missing or stale (>1 hour), speak "I don't have up-to-date info on that" and optionally escalate to Cowork.

## Fuzzy intents (escalate to Cowork)

These REQUIRE Claude + MCPs because they involve generation, judgment, or cross-project reasoning.

| Intent | Why it escalates | Example utterance |
|---|---|---|
| Draft an email | Requires writing, tone judgment, recipient context | "Draft a reply to Dr. Jones declining Tuesday" |
| Find a meeting slot | Requires reading multiple calendars + preferences | "Find me 30 minutes with the iMerit team next week" |
| Summarize recent activity | Requires reading across projects | "What's happening with Clinical Notes this week" |
| Cross-project reasoning | Same as above | "What do I owe the billing team before month-end" |
| Add to a new project I haven't told you about | Project doesn't exist in the routing map | "Track this in my upcoming vacation project" |
| Open-ended Q&A about personal context | Requires deep memory + judgment | "When did I last see Dr. Smith" |

## Ambiguous cases (escalate when confidence is low)

- Intent classification below 0.8 → escalate
- Required args missing and not easily inferable → escalate or ask a clarifying question and re-parse
- Intent matches deterministic set but args reference something that needs disambiguation ("the meeting with Jane" where there are two Janes) → escalate

## Keeping this file current

When a new deterministic intent is added to n8n, add a row to the table above. When an intent is promoted from fuzzy → deterministic (because we built a new n8n workflow for it), move its row. This file is the contract — the 8B's intent classifier prompt is built from this table.
