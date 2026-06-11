# Publish Manifest — sinabarimd/chief-of-staff

This file is the allowlist for what `/push-repo` (or equivalent) syncs
from the project root into this staging tree before pushing. The
project root has no `.git`. This staging tree is one of the only two
places git ever runs (the other is `github/reputation-engine/`).

**Status as of 2026-06-11:** empty scaffold. No content has been
curated in yet. The sync table is intentionally empty until the
Voice Hub bundle + NA artifact scrub pass lands.

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
| _(none yet — populate during Voice Hub bundle curation pass)_ | | |

## Repo-canonical files (live here, edited in place)

- `README.md`
- `.gitignore`
- `.githooks/pre-push`
- `PUBLISH_MANIFEST.md`

## Never published (defense in depth)

`/push-repo` must refuse to copy any of these in, regardless of where
they appear:

- `voicehub_repo_bundle/` — the raw bundle stays at the project root,
  gitignored. Content is scrubbed and copied in file by file via the
  sync table above (with explicit sanitize substitutions matched
  against the bundle's REDACT list in
  `voice_stack_handoff_to_reputation_engine_2026-06-11.md`).
- The bundle's `voice_stack_handoff_to_reputation_engine_2026-06-11.md`
  itself is **never** copied — it contains plaintext secrets in its
  REDACT section.
- Any file containing the redacted values from the bundle's REDACT
  list (HuggingFace tokens, Bearer JWTs, the cloud n8n voice-query
  auth header value from the rotation, any other secret string).
- `backups/`, `.env`, `pending_actions.md`, `CLAUDE.md`, `.claude/`
- `spec_*.md`, `*_DRAFT.*`, `*handoff*`
- Personal photos / narratives / essays / drafts

## Curation pass checklist (planned, not yet executed)

When the bundle scrub pass runs:

1. Read `voicehub_repo_bundle/voice_stack_handoff_to_reputation_engine_2026-06-11.md`
   end-to-end. The REDACT section is the authoritative scrub list —
   collect every redact pattern.
2. For each bundle file (`README_BUNDLE.md`, `architecture_notes.md`,
   `action_taxonomy.md`, `hardware_setup.md`, `latency_budget.md`,
   `package_protocol.md`, the `code/` and `scripts/` trees, the wake-
   word model files):
   a. Read the file.
   b. For every REDACT pattern, verify zero hits in the working copy
      (grep before adding to the manifest, not just trust the manifest).
   c. Add a manifest row with the appropriate sanitize cell — `none`
      only if the working file genuinely has no real-world secrets,
      domains, or tokens.
3. Wake-word model files (`Hey_Sinabot_*.tflite`,
   `Openwakeword model Hey_Sinabot_*.onnx`) are publishable as-is per
   the Voice Hub handoff. Add them as binary manifest entries.
4. Decide NA artifacts to include (token-cost analysis, observability
   layer code, calendar/email integration sketches). Add each with
   sanitize cells.
5. Run the pre-push scan from this staging tree against the populated
   working copy. Resolve every hit.
6. First commit is the curated initial drop, with a CHANGELOG entry
   under `## Week of 2026-06-15` (or whenever the curation lands).
