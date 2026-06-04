# Repository Guidelines

## Project Structure & Module Organization

`noc_cli/` contains the installable Python package for the `noc-cli` Typer entry point. Keep CLI command functions in `noc_cli/cli.py` thin; put behavior in focused modules such as `config.py`, `zendesk.py`, `evidence.py`, `scaffold.py`, `watch/`, `agent/`, and `tui/`. Bundled markdown assets live in `noc_cli/data/` and `noc_cli/runbooks/`. Tests live in `tests/`, with fixtures in `tests/fixtures/`. Design and implementation notes live under `docs/superpowers/`; legacy operator docs are in `Legacy/`. Runtime ticket data belongs in `Tickets/` and must not be committed.

## Build, Test, and Development Commands

- `uv sync`: install project and development dependencies from `uv.lock`.
- `uv run pytest`: run the full test suite configured by `pyproject.toml`.
- `uv run pytest tests/test_watch_poller.py -v`: run a focused test file while iterating.
- `uv run noc-cli --help`: inspect the installed CLI surface.
- `uv run noc-cli doctor`: validate local config, paths, Claude engine availability, and notification support.
- `uv run noc-cli investigate 12345 --no-agent`: exercise scaffold, evidence gathering, and redaction without invoking the LLM.

## Coding Style & Naming Conventions

Target Python 3.10+ and follow the existing style: 4-space indentation, type hints, `from __future__ import annotations`, small pure functions, and explicit `Path` handling. Use `snake_case` for modules, functions, variables, and test names; use `PascalCase` for Pydantic models, exceptions, Textual app classes, and notifier classes. Keep network, filesystem, and UI side effects injectable so tests can use `tmp_path`, mocks, or fake clients.

## Testing Guidelines

Use pytest, `pytest-httpx`, and `anyio` where async behavior is involved. Name tests `test_<behavior>.py` and test functions `test_<expected_outcome>()`. Prefer narrow unit tests for pure modules, mocked HTTP for Zendesk clients, and headless Textual tests for TUI behavior. Do not make live Zendesk or external network calls in ordinary tests.

## Commit & Pull Request Guidelines

Follow the existing Conventional Commit pattern: `feat(scope): summary`, `fix(scope): summary`, or short `feat:`/`fix:` subjects. Keep summaries imperative and specific, for example `fix(zendesk): follow 302 redirect when downloading attachments`. PRs should describe the user-visible change, link the ticket or issue, list verification commands run, and include screenshots or terminal captures for TUI changes.

## Security & Configuration Tips

`noc-cli` is read-only toward Zendesk by design. Never commit `.env`, `.env.*`, `.claude/settings.local.json`, `Tickets/`, `.remember/`, SQLite databases, or customer evidence. Local configuration is loaded from the `noc-cli` data directory `.env`, with process environment variables such as `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`, `NOC_TICKETS_ROOT`, `NOC_OWNER`, `NOC_WATCH_VIEW`, `NOC_WATCH_ASSIGNEE`, and `NOC_NOTIFY` taking precedence.
