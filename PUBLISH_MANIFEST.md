# Publish Manifest — sinabarimd/chief-of-staff

This file is the allowlist for what `/push-repo` syncs from the project
root into this staging tree. The project root has no `.git`. This
staging tree is one of the only two places git ever runs (the other is
`github/reputation-engine/`).

## How `/push-repo` reads this file

Same format as `github/reputation-engine/PUBLISH_MANIFEST.md`:

1. Parse the **`## Sync from project root`** table. Each row is
   `source-path | dest-path | sanitize` where:
   - `source-path` is relative to the project root.
   - `dest-path` is relative to this staging tree
     (`github/chief-of-staff/`).
   - `sanitize` is `none` or `s|pattern|replacement|` (multiple separated
     by `;;`).
2. Copy each source → dest with sanitize.
3. Run `.githooks/pre-push` against the staging tree.
4. On clean scan, show `git status` / `git diff --stat`, confirm with
   operator, commit, push.

Anything outside this manifest's sync table or the repo-canonical list
never gets copied in.

## Sync from project root

| Source (project root) | Dest (this staging tree) | Sanitize |
|-----------------------|--------------------------|----------|
| _(none — every file in this tree is repo-canonical, scrubbed once via `scripts/scrub_bundle.py` from the gitignored `voicehub_repo_bundle/` at initial curation)_ | | |

## Repo-canonical files (live here, edited in place)

These files exist in this staging tree as the source of truth. They are
edited directly here (not auto-synced from the bundle on each push) and
are pushed as-is.

- `README.md`, `.gitignore`, `.githooks/pre-push`, `PUBLISH_MANIFEST.md`
- `orchestrator/*.py` — the custom voice orchestrator (scrubbed from
  the bundle; further edits land here)
- `orchestrator/voicehub-orchestrator.service`
- `orchestrator/requirements.txt`
- `orchestrator/README.md`
- `n8n-workflows/voice-escalate.json`
- `n8n-workflows/voice-research.json`
- `models/Hey_Sinabot_microWakeWord.tflite` (binary)
- `models/Hey_Sinabot_openWakeWord.onnx` (binary)
- `docs/*.md` — architecture, spec, latency, etc.
- `scripts/pi_backup_reference.md`, `scripts/pi_wifi_rescue.sh`

If the parent bundle (`voicehub_repo_bundle/` at the project root) gets
a fresh drop and you want to refresh this tree, re-run the scrub script
(`/tmp/scrub_bundle.py` in the project root, or whatever path you saved
it to). It overwrites in-place with the same sanitization pass. Review
the diff carefully before committing — any local edits to this tree
would be reverted by the overwrite.

## Never published (defense in depth)

`/push-repo` must refuse to copy any of these in, regardless of where
they appear:

- `voicehub_repo_bundle/` itself — the raw bundle stays at the project
  root, gitignored. Content reaches this tree only via the scrub script
  with explicit sanitization.
- The bundle's
  `voice_stack_handoff_to_reputation_engine_2026-06-11.md` — it
  contains plaintext secrets in its REDACT section and is **never**
  copied or transformed into the staging tree.
- Any file containing the redacted values from the bundle's REDACT
  list (HuggingFace tokens, Bearer JWTs, the cloud n8n voice-query
  auth header value from the rotation, the Tavily key, any other
  secret string).
- `backups/`, `.env`, `pending_actions.md`, `CLAUDE.md`, `.claude/`
- `spec_*.md`, `*_DRAFT.*`, `*handoff*`
- Personal photos / narratives / essays / drafts

## Initial curation (already done — 2026-06-11)

When the bundle scrub pass ran for the first commit:

1. Read `voicehub_repo_bundle/voice_stack_handoff_to_reputation_engine_2026-06-11.md`
   end-to-end. The REDACT section was the authoritative scrub list.
2. The scrub script (`scrub_bundle.py`, kept locally — not pushed) ran
   every bundle file through a regex substitution pass: IPs in the
   192.168.4.0/24 subnet → 192.168.1.0/24, Tailscale IPs → placeholders,
   MAC/BSSID → `XX:XX:XX:XX:XX:XX`, hostnames → generic, `/mnt/storage/`
   paths → `/var/voicehub/`, `/home/sinabot/` → `/opt/` or `/home/user/`,
   `n8n.sinabarimd.com` → `your-n8n-host.example.com`, plus a defense
   pass against `hf_`, `tvly-`, `sk-`, `ghp_`, `eyJ…` literals.
3. Two real-world Tavily key leaks (`tvly-dev-...` in `web_search.py`
   and `voice-hub-n8n-architecture.md`) were caught by the scrub pass
   and replaced with `YOUR_TAVILY_KEY_HERE`. Three further false
   positives where the literal string `X-Voice-Key` appears in
   protocol-name context (header set from env in `intent_router.py`,
   docs describing the protocol) were resolved by tightening the scan
   to only flag `X-Voice-Key:` followed by an actual 20+ char key
   value.
4. Two clinical-example task strings in `docs/action_taxonomy.md` and
   `docs/package_protocol.md` were genericized (clinical-note examples
   → family-budget examples) — the leak risk was zero (the names were
   fake) but the framing was clinical-adjacent and the article had
   already genericized the same kind of example.
5. Wake-word model files (`Hey_Sinabot_microWakeWord.tflite`,
   `Hey_Sinabot_openWakeWord.onnx`) copied as-is per the Voice Hub
   handoff — they are publishable.
6. Pre-push scan green from inside this staging tree; initial commit
   pushed to `github.com/sinabarimd/chief-of-staff` as the seed.
