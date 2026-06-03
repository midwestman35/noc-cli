# Agent Definitions

These Markdown files define the personal DailyNOC agents used by `triage-cli`.

Rust owns execution mechanics: polling, file writes, event append, status files, notification dedupe, and safety boundaries. These Markdown files own behavior: personality, task scope, source priority, allowed writes, stop conditions, and output expectations.

Definitions live under `agents/` and are loaded as operator-editable Markdown.

Edit these files to tune agent personality and task boundaries over time:

- `queue-scout.md`
- `claim-and-intake.md`
- `stale-refresh.md`
- `source-refresh.md`

Validate behavior changes with fixture commands before daily use:

```bash
cd triage-cli-rs
cargo test --lib substrate notifications spinners
cargo test --test integration runbook_09
```
