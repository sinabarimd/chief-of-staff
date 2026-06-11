# Package Protocol (v0)

Packages are the data contract between Cowork (producer) and Voice Hub (consumer + actuator). This is a v0 sketch — refine it as we build the first concrete integrations (morning-briefing delivery, alert on overdue clinical note, etc.).

## Envelope

```json
{
  "package_id": "uuid",
  "schema_version": "0.1",
  "source_project": "Notification Agent | Work Assistant | Clinical Notes | ...",
  "produced_at": "ISO-8601 with timezone",
  "expires_at": "ISO-8601 with timezone",
  "kind": "briefing | alert | confirmation_request | proposed_action | user_response",
  "priority": "routine | timely | urgent",
  "delivery": {
    "zones": ["main | bedroom | family | office | any"],
    "earliest": "ISO-8601",
    "latest": "ISO-8601",
    "require_ack": true,
    "suppress_if_quiet_hours": true
  },
  "content": {
    "speak": "Text to synthesize (may contain limited SSML: <break>, emphasis)",
    "summary": "Short display-form version (for logs, dashboards)",
    "context": "Optional freeform context the local 8B can use to answer follow-ups without escalating. Keep under ~1KB to fit cheaply into the 8B's prompt."
  },
  "allowed_actions": [
    {
      "id": "confirm",
      "label": "Mark complete",
      "kind": "deterministic",
      "target": "n8n:mark_task_complete",
      "payload": { "project": "Clinical Notes", "task_id": "..." }
    },
    {
      "id": "defer",
      "label": "Defer 1 day",
      "kind": "deterministic",
      "target": "n8n:reschedule_task",
      "payload": { "delta_hours": 24 }
    },
    {
      "id": "dismiss",
      "label": "Dismiss",
      "kind": "deterministic",
      "target": "n8n:noop"
    },
    {
      "id": "ask_follow_up",
      "label": "Tell me more",
      "kind": "fuzzy",
      "target": "cowork:escalate",
      "session_hint": "The user asked for more context on the briefing content above."
    }
  ],
  "escalation": {
    "cowork_endpoint": "https://n8n.local/webhook/voice-escalation",
    "session_template": "Optional prompt prefix for the Claude session. Keep short."
  }
}
```

## Kinds

| Kind | Use |
|---|---|
| `briefing` | Scheduled, pre-computed content (morning briefing, weekly digest). Speak, accept light follow-ups, rarely require_ack. |
| `alert` | Time-sensitive notification. Speak on first available delivery window. May require_ack. |
| `confirmation_request` | "Did you want me to send the draft?" style. Always require_ack. Allowed_actions drive the decision tree. |
| `proposed_action` | "I scheduled X for Tuesday at 2pm; say yes to confirm or no to cancel." Always require_ack. |
| `user_response` | Inbound from voice → Cowork. Chosen action_id + optional free-form transcript. |

## User response envelope

```json
{
  "package_id": "uuid of original",
  "response_id": "uuid",
  "responded_at": "ISO-8601",
  "kind": "user_response",
  "chosen_action": "confirm | defer | dismiss | ask_follow_up | custom",
  "transcript": "Full final STT transcript of user's utterance",
  "confidence": 0.0-1.0
}
```

## Design notes

- **Idempotency**: `package_id` is the idempotency key. If Cowork re-emits a package (e.g., after a restart), Voice Hub MUST not re-speak it if the prior delivery succeeded or expired.
- **Expiration**: expired packages are silently dropped. Use case: morning-briefing at 7am becomes irrelevant by 2pm; don't speak it if the user walks in at 1:30pm.
- **Zone routing**: `any` is the common case; specific zones are used for private content (e.g., medical prompts routed to office, not family room).
- **Context budget**: `content.context` is passed into the local 8B's prompt, so it competes with the model's 8K–32K context budget. Keep it tight; if more context is needed for a follow-up, escalate to Cowork.
- **Allowed actions cap**: limit to 3–5 per package. The local 8B has to classify the user's utterance against this list, and accuracy degrades past ~5 options.

## Example: morning briefing

```json
{
  "package_id": "brf-2026-04-20-001",
  "schema_version": "0.1",
  "source_project": "Work Assistant",
  "produced_at": "2026-04-20T06:15:00-04:00",
  "expires_at": "2026-04-20T10:00:00-04:00",
  "kind": "briefing",
  "priority": "routine",
  "delivery": {
    "zones": ["main", "bedroom"],
    "earliest": "2026-04-20T06:45:00-04:00",
    "latest": "2026-04-20T08:30:00-04:00",
    "require_ack": false,
    "suppress_if_quiet_hours": true
  },
  "content": {
    "speak": "Good morning. You have three meetings today: a 10am standup, an 11am interview, and a 2pm client call. Two overdue items in your Notes project need attention before Wednesday. Inbox is at 8 unread, one tagged urgent.",
    "summary": "3 meetings, 2 overdue notes, 8 unread (1 urgent)",
    "context": "Interview at 11am is with a senior annotator candidate; resume and notes are in Work Assistant/interview_prep/. Client call at 2pm is on a pending pilot scope. Two overdue notes from earlier in the week to be reviewed before end of day."
  },
  "allowed_actions": [
    {"id": "skip_to_inbox", "label": "Just tell me what's urgent in email", "kind": "fuzzy", "target": "cowork:escalate", "session_hint": "User wants the urgent email summary only."},
    {"id": "dismiss", "label": "Dismiss", "kind": "deterministic", "target": "n8n:noop"}
  ],
  "escalation": {
    "cowork_endpoint": "https://n8n.local/webhook/voice-escalation",
    "session_template": "Respond briefly and return to the user."
  }
}
```
