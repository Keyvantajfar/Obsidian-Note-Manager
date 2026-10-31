# Vault Manager

An LLM-assisted tagger and organizer for Obsidian vaults, with a tabbed terminal UI.
Nothing touches your vault until you review and apply it, every apply can be undone, and note bodies are never modified.

```
pip install -e ".[tui]"
vaultmgr /path/to/vault            # TUI
vaultmgr --demo                    # messy sample vault + fake model, no server needed
vaultmgr-cli /path/to/vault auto --route     # headless: tag, route, accept confident, apply
```

## Layout: core vs. UI

| Package | What it is |
|---|---|
| `src/vaultcore/` | Everything that does work, with no UI imports: scanning, header engine, LLM client, parallel jobs, stage, rules, taxonomy, apply/undo. The `Session` class is the single facade and emits events (`session.on(cb)`). |
| `src/vaulttui/` | Textual TUI. Pure presentation over `Session`. |

A GUI (or web UI) can be added as a third package that talks to `Session` the same way. `vaultmgr-cli` is a tiny example of that.

## Tabs

1. **Explorer** – browse the vault (current or planned layout), edit headers by hand, move/rename, lock, AI-tag a selection, optionally edit the body.
2. **Tagger** – run the LLM over notes in parallel, watch proposals stream in, accept / reject / edit.
3. **Organizer** – the planned folder tree (editable, with descriptions), AI structure proposal, route notes, AI suggestions for new folders.
4. **Stage** – every pending change in one place (header edits, moves). Edit destinations, names and fields, then dry-run and apply.
5. **Jobs** – pause / resume / cancel, change worker count live, retry failures.
6. **Tags** – value vocabulary with near-duplicate detection; rename / merge / delete across notes (staged).
7. **Rules** – typed rules and free-text instructions.
8. **Settings** – LLM profiles, auto mode, safety, scanning, header schema editor, editable prompts.

Keys: `1`–`8` switch tabs, `:` or `/` command bar, `p` pause jobs, `m` cycle mode, `?` help, `q` quit.
Slash commands include `/mode`, `/workers`, `/batch`, `/threshold`, `/run`, `/apply`, `/undo`, `/pause`, `/resume`, `/stop`, `/scan`, `/freeze`, `/unfreeze`, `/goto`.
Typing plain text in the command bar is a free-text instruction to the AI (prefix `@folder:` or `#tag:` to scope it).

## Modes (auto doesn't take over)

- **manual** – the AI only runs when you ask.
- **assist** – a watcher notices new/changed notes and proposes; you accept.
- **auto** – confident proposals (≥ threshold) are accepted automatically, optionally applied up to a per-run cap.

You can pause, cancel, change workers, add an instruction or lock a note at any moment. Priority is always manual > rule > AI.

## Structure: starts as a plan, then freezes

The folder plan is yours to edit (add, rename, describe). Renames retarget staged moves, rules and instructions. Once it is right, **freeze** it: the AI then only routes into existing folders and *suggests* new nested ones (e.g. `Homework`) that you accept or ignore.

## Rules

```
tag:ML -> move:2_Research/AI
title~homework|assignment -> move:3_University/Homework
path:Daily/** -> skip
noheader text~"#recipe" -> set!:status=Reference move:Cooking
```
Conditions: `path folder name title text tag <header-key>`, `~` for regex, `!` negates, `noheader`, `hasheader`, `missing:KEY`.
Actions: `move`, `set` (fill if empty), `set!` (overwrite), `add` / `tag+`, `skip`, `lock`.
Rules run before any LLM call.

## Everything is configurable

- **LLM backend**: any OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama, LM Studio, hosted). Profiles hold base URL, model, key (or an env var name; stored in your user config with 600 permissions, never in the vault), extra request body (e.g. vLLM guided decoding), JSON mode auto/on/off.
- **Header schema**: fields, kinds, sources and per-field instructions are edited in Settings. Defaults: `parent`, `status`, `tags`, `date`. Headers are handled by a byte-preserving line engine (not YAML), so odd existing headers like `status: #Idea` survive; CRLF and BOM are kept.
- **Prompts**, worker count, notes per request, thresholds, exclusions and feature toggles.
- Per-vault state lives in `.vaultmgr/`; global profiles in `~/.config/vaultmgr/` (override with `VAULTMGR_HOME`).

## Troubleshooting the model

- **HTTP 401 "Missing Authentication header"**: the provider got no usable API key. Listing models works without a key on OpenRouter, so a green "server reachable" proves nothing. Test now prints which key it sends (first 9 / last 4 characters and length), pasted keys are cleaned of spaces/newlines/quotes/`Bearer `, and a job that hits 401/402/403 pauses itself instead of failing every note.
- **Settings → LLM connection → Test** does a real chat + JSON request and tells you if the reply is empty or not JSON.
- **"model returned an empty answer"**: a thinking/reasoning model (GLM, Qwen3, DeepSeek-R1, ...) spent its token budget on hidden reasoning. The tool now retries with a bigger budget and asks the server to skip thinking (profile option *Thinking*: auto / off / on). Setting it to `off` avoids the extra cost entirely.
- **"did not return valid JSON: '...'"** now shows what the model actually said. Try JSON mode `off`, or a model that follows instructions better.
- Provider errors sent inside HTTP 200 (OpenRouter) and `response_format` rejections (HTTP 400/404) are handled and retried.
- Coming from the old tool? `vaultmgr --import-config config.json` (or `/import PATH` in the command bar) turns `ai_base_url` / `ai_model` / `ai_api_key` / `ai_timeout` into a profile.
- A header preceded by a blank line (Obsidian ignores those) is recognised as a header; when the tool writes to such a note it moves the header to line 1.

## Safety

Dry-run before every apply, journal + backups in `.vaultmgr/`, one-step undo (notes edited since are left alone and reported), never overwrites on name collisions, preserves mtimes, warns about links that a move would break, prunes empty folders only after moves.

## Development

```
pip install -e ".[tui,dev]"
pytest
```
The mock model (`--mock`) makes tests and demos work with no server.

## Future work

- **Confidence from model weights.** Today "confidence" is the model's own self-reported number, which is uncalibrated. It should be computed from token logprobs / perplexity of the generated header and route, and calibrated against accepted/rejected history, so auto-accept thresholds mean something.
- **Fine-tuned model.** Fine-tune a small LLM on your accepted/rejected stage history and ship it inside the tool for tagging and for producing that confidence signal.
- GUI frontend on top of `vaultcore`.
- Embedding-based routing as a cheap pre-filter before the LLM.
