# Voice Hub — Implementation

This directory is a **separate git repo** from the parent Voice Hub folder. It holds:

- `compose.yml` — the Docker Compose stack on the 4070 host
- `parakeet_wyoming/` — Parakeet RNN-T STT wrapped in Wyoming protocol
- `kokoro_wyoming/` — Kokoro TTS wrapped in Wyoming protocol
- `n8n_workflows/` — exported n8n workflow JSON
- `ha_config/` — Home Assistant configuration (pipelines, OpenAI conversation integration)
- `vllm_launch/` — vLLM launch scripts and systemd units
- `benchmarks/` — latency + intent accuracy test harnesses

## Workflow

1. Spec/design edits → parent folder (`../`)
2. Code edits → here, committed as a separate repo
3. Claude Code on the 4070 host pulls this repo and runs against the local stack
4. Benchmark runs write results back into `benchmarks/` for regression tracking

## Initial setup (to do in week 1 — see `../pending_actions.md`)

- [ ] Initialize git repo: `git init`
- [ ] Create an initial `compose.yml` with placeholder services
- [ ] Create `.gitignore` — exclude secrets, model weights, local logs
- [ ] Push to GitHub (private) and clone on the 4070 host once Ubuntu is installed

## Secrets

Nothing secret belongs in this repo. All API keys, OAuth tokens, and credentials live in `.env` files on the host, referenced by `compose.yml` but gitignored.
