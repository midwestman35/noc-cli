# noc-cli Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the installable Python package and the shared core libraries (config, models, read-only Zendesk client, SQLite primitive) that every other noc-cli plan depends on.

**Architecture:** A `uv`-managed Python package exposing a `noc-cli` Typer entry point. Foundation ships the libraries + a CLI shell with a **stubbed, styled command surface**: the four commands render a branded "coming soon" notice until later plans implement them, so the package is demoable to the team after this phase. Each library module has one responsibility and is unit-tested in isolation; the Zendesk client is tested with mocked HTTP and never performs writes.

**Tech Stack:** Python 3.10+, uv, Typer, httpx, pydantic v2, platformdirs, python-dotenv; pytest + pytest-httpx for tests.

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, `noc-cli` console script, pytest config |
| `noc_cli/__init__.py` | Package marker + `__version__` |
| `noc_cli/cli.py` | Typer app shell + `--version`; **stubbed command surface** (Task 6), each stub replaced by a later plan |
| `noc_cli/branding.py` | Styled brand banner / visual identity, reused by the TUI plans |
| `noc_cli/config.py` | `Config` model, data-dir/path resolution, load (.env + env override) |
| `noc_cli/models.py` | Read-side pydantic models: `Ticket`, `Comment`, `Attachment` |
| `noc_cli/zendesk.py` | Read-only Zendesk API v2 client (`get_ticket`/`get_comments`/`search`/`view_tickets`) |
| `noc_cli/store.py` | SQLite connection primitive (pragmas, row factory, dir creation) |
| `tests/test_cli.py` | CLI `--version`, branded banner, and stubbed command-surface tests |
| `tests/test_config.py` | Path resolution + load/override tests |
| `tests/test_models.py` | Zendesk payload parsing test |
| `tests/test_zendesk.py` | Mocked-HTTP client tests (auth scheme, parsing, errors) |
| `tests/test_store.py` | SQLite connect/pragma test |

---

## Task 1: Project skeleton + CLI `--version`

**Files:**
- Create: `pyproject.toml`
- Create: `noc_cli/__init__.py`
- Create: `noc_cli/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
from typer.testing import CliRunner

from noc_cli import __version__
from noc_cli.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli'` (package does not exist yet).

- [ ] **Step 3: Create the package and CLI**

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "noc-cli"
version = "0.1.0"
description = "Read-only NOC triage assistant for Carbyne APEX NG911/E911 (Python / Claude Agent SDK)."
requires-python = ">=3.10"
dependencies = [
    "typer>=0.12",
    "httpx>=0.27",
    "pydantic>=2.7",
    "platformdirs>=4.2",
    "python-dotenv>=1.0",
]

[project.scripts]
noc-cli = "noc_cli.cli:app"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-httpx>=0.30",
]

[tool.hatch.build.targets.wheel]
packages = ["noc_cli"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`noc_cli/__init__.py`:

```python
"""noc-cli — read-only NOC triage assistant for Carbyne APEX NG911/E911."""

__version__ = "0.1.0"
```

`noc_cli/cli.py`:

```python
from __future__ import annotations

import typer

from noc_cli import __version__

app = typer.Typer(
    name="noc-cli",
    help="Read-only NOC triage assistant for Carbyne APEX NG911/E911.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"noc-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Read-only NOC triage assistant."""


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Sync the environment and run the test**

Run: `uv sync && uv run pytest tests/test_cli.py -v`
Expected: PASS. Also verify the entry point: `uv run noc-cli --version` prints `noc-cli 0.1.0`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml noc_cli/__init__.py noc_cli/cli.py tests/test_cli.py uv.lock
git commit -m "feat: project skeleton + noc-cli --version"
```

---

## Task 2: Config model + path resolution

**Files:**
- Create: `noc_cli/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
from noc_cli import config


def test_data_dir_respects_noc_home(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    assert config.data_dir() == tmp_path


def test_load_config_reads_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)
    monkeypatch.delenv("NOC_OWNER", raising=False)
    (tmp_path / ".env").write_text("ZENDESK_SUBDOMAIN=carbyne\nNOC_OWNER=alice\n")
    cfg = config.load_config()
    assert cfg.zendesk_subdomain == "carbyne"
    assert cfg.owner == "alice"
    assert cfg.zendesk_base_url == "https://carbyne.zendesk.com/api/v2"


def test_process_env_overrides_file(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("ZENDESK_SUBDOMAIN=fromfile\n")
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "fromenv")
    cfg = config.load_config()
    assert cfg.zendesk_subdomain == "fromenv"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: module 'noc_cli.config' has no attribute 'data_dir'` (module/symbols missing).

- [ ] **Step 3: Implement `config.py`**

`noc_cli/config.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from platformdirs import user_data_dir
from pydantic import BaseModel, Field

APP_NAME = "noc-cli"

# Config field -> environment variable / .env key
_FIELD_ENV: dict[str, str] = {
    "zendesk_subdomain": "ZENDESK_SUBDOMAIN",
    "zendesk_email": "ZENDESK_EMAIL",
    "zendesk_api_token": "ZENDESK_API_TOKEN",
    "tickets_root": "NOC_TICKETS_ROOT",
    "owner": "NOC_OWNER",
    "watch_view": "NOC_WATCH_VIEW",
    "watch_assignee": "NOC_WATCH_ASSIGNEE",
    "notify": "NOC_NOTIFY",
}


def data_dir() -> Path:
    """noc-cli data directory. `NOC_HOME` overrides the platform default."""
    override = os.environ.get("NOC_HOME")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir(APP_NAME, appauthor=False))


def config_path() -> Path:
    return data_dir() / ".env"


def db_path() -> Path:
    return data_dir() / "noc.db"


class Config(BaseModel):
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_api_token: str = ""
    tickets_root: Path = Field(default_factory=lambda: Path.cwd() / "Tickets")
    owner: str = Field(default_factory=lambda: os.environ.get("USER", "unknown"))
    watch_view: str = ""
    watch_assignee: str = ""
    notify: str = "banner,ping"

    @property
    def zendesk_base_url(self) -> str:
        return f"https://{self.zendesk_subdomain}.zendesk.com/api/v2"


def load_config() -> Config:
    """Load config from the data-dir `.env`, with process env overriding file values."""
    path = config_path()
    file_values = dotenv_values(path) if path.exists() else {}
    merged: dict[str, str] = {}
    for field, env_key in _FIELD_ENV.items():
        value = os.environ.get(env_key, file_values.get(env_key))
        if value:
            merged[field] = value
    return Config(**merged)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/config.py tests/test_config.py
git commit -m "feat: config model + data-dir/path resolution with env override"
```

---

## Task 3: Core read-side models

**Files:**
- Create: `noc_cli/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:

```python
from noc_cli.models import Comment, Ticket


def test_ticket_parses_zendesk_payload_and_ignores_extra_fields():
    payload = {
        "id": 18432,
        "subject": "PSAP - No ANI displaying",
        "description": "Caller ID not populating",
        "status": "open",
        "tags": ["no_ani", "apex"],
        "created_at": "2026-06-01T03:02:17Z",
        "organization_id": 99,  # extra field must not break parsing
    }
    ticket = Ticket.model_validate(payload)
    assert ticket.id == 18432
    assert ticket.status == "open"
    assert "apex" in ticket.tags
    assert ticket.comments == []


def test_comment_defaults_public_true():
    comment = Comment.model_validate({"id": 1, "body": "hello"})
    assert comment.public is True
    assert comment.attachments == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.models'`.

- [ ] **Step 3: Implement `models.py`**

`noc_cli/models.py`:

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    file_name: str
    content_url: str
    size: int = 0


class Comment(BaseModel):
    id: int
    author_id: int | None = None
    public: bool = True
    body: str = ""
    created_at: datetime | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class Ticket(BaseModel):
    id: int
    subject: str = ""
    description: str = ""
    requester_org: str | None = None
    requester_email: str | None = None
    status: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    comments: list[Comment] = Field(default_factory=list)
```

(pydantic v2 ignores unknown fields by default, so real Zendesk payloads with extra keys parse cleanly.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/models.py tests/test_models.py
git commit -m "feat: read-side pydantic models (Ticket, Comment, Attachment)"
```

---

## Task 4: Read-only Zendesk API v2 client

**Files:**
- Create: `noc_cli/zendesk.py`
- Test: `tests/test_zendesk.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_zendesk.py`:

```python
import base64

import httpx
import pytest

from noc_cli.config import Config
from noc_cli.models import Ticket
from noc_cli.zendesk import ZendeskClient, ZendeskError


def make_config() -> Config:
    return Config(
        zendesk_subdomain="carbyne",
        zendesk_email="agent@x.com",
        zendesk_api_token="tok123",
    )


def test_missing_config_raises():
    with pytest.raises(ZendeskError):
        ZendeskClient(Config())


def test_get_ticket_uses_token_auth_and_parses(httpx_mock):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/tickets/18432.json",
        json={"ticket": {"id": 18432, "subject": "No ANI", "status": "open", "tags": ["apex"]}},
    )
    client = ZendeskClient(make_config(), client=httpx.Client())
    ticket = client.get_ticket(18432)
    assert isinstance(ticket, Ticket)
    assert ticket.id == 18432
    request = httpx_mock.get_request()
    expected = "Basic " + base64.b64encode(b"agent@x.com/token:tok123").decode()
    assert request.headers["Authorization"] == expected


def test_auth_failure_raises_friendly_error(httpx_mock):
    httpx_mock.add_response(status_code=401)
    client = ZendeskClient(make_config(), client=httpx.Client())
    with pytest.raises(ZendeskError, match="auth failed"):
        client.get_ticket(1)


def test_search_filters_to_tickets(httpx_mock):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/search.json?query=foo",
        json={"results": [
            {"id": 1, "result_type": "ticket", "subject": "a"},
            {"id": 2, "result_type": "user"},
        ]},
    )
    client = ZendeskClient(make_config(), client=httpx.Client())
    results = client.search("foo")
    assert [t.id for t in results] == [1]


def test_view_tickets_parses_list(httpx_mock):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/views/555/tickets.json",
        json={"tickets": [{"id": 7, "status": "pending"}]},
    )
    client = ZendeskClient(make_config(), client=httpx.Client())
    tickets = client.view_tickets(555)
    assert tickets[0].status == "pending"


def test_get_comments_parses_list(httpx_mock):
    httpx_mock.add_response(
        url="https://carbyne.zendesk.com/api/v2/tickets/9/comments.json",
        json={"comments": [{"id": 1, "body": "hi", "public": True}]},
    )
    client = ZendeskClient(make_config(), client=httpx.Client())
    comments = client.get_comments(9)
    assert comments[0].body == "hi"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_zendesk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.zendesk'`.

- [ ] **Step 3: Implement `zendesk.py`**

`noc_cli/zendesk.py`:

```python
from __future__ import annotations

import base64

import httpx

from noc_cli.config import Config
from noc_cli.models import Comment, Ticket


class ZendeskError(RuntimeError):
    pass


class ZendeskClient:
    """Read-only Zendesk API v2 client. Performs no writes, ever."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        if not (config.zendesk_subdomain and config.zendesk_email and config.zendesk_api_token):
            raise ZendeskError(
                "Zendesk is not configured. Run `noc-cli setup` to set "
                "ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_TOKEN."
            )
        self._base_url = config.zendesk_base_url
        # Zendesk token auth: "<email>/token:<api_token>". Do not pre-append /token.
        raw = f"{config.zendesk_email}/token:{config.zendesk_api_token}".encode()
        self._auth_header = "Basic " + base64.b64encode(raw).decode()
        self._client = client or httpx.Client(timeout=30.0)

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._client.get(
            f"{self._base_url}{path}",
            params=params,
            headers={"Authorization": self._auth_header},
        )
        if resp.status_code == 401:
            raise ZendeskError(
                "Zendesk auth failed - check ZENDESK_EMAIL and ZENDESK_API_TOKEN."
            )
        resp.raise_for_status()
        return resp.json()

    def get_ticket(self, ticket_id: int) -> Ticket:
        data = self._get(f"/tickets/{ticket_id}.json")
        return Ticket.model_validate(data["ticket"])

    def get_comments(self, ticket_id: int) -> list[Comment]:
        data = self._get(f"/tickets/{ticket_id}/comments.json")
        return [Comment.model_validate(c) for c in data.get("comments", [])]

    def search(self, query: str) -> list[Ticket]:
        data = self._get("/search.json", params={"query": query})
        return [
            Ticket.model_validate(r)
            for r in data.get("results", [])
            if r.get("result_type") == "ticket"
        ]

    def view_tickets(self, view_id: int | str) -> list[Ticket]:
        data = self._get(f"/views/{view_id}/tickets.json")
        return [Ticket.model_validate(t) for t in data.get("tickets", [])]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_zendesk.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add noc_cli/zendesk.py tests/test_zendesk.py
git commit -m "feat: read-only Zendesk API v2 client"
```

---

## Task 5: SQLite connection primitive

**Files:**
- Create: `noc_cli/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:

```python
from noc_cli import store


def test_connect_creates_dirs_sets_pragmas_and_row_factory(tmp_path):
    db = tmp_path / "nested" / "noc.db"
    conn = store.connect(db)
    try:
        assert db.exists()
        assert conn.execute("PRAGMA foreign_keys;").fetchone()[0] == 1
        conn.execute("CREATE TABLE t (a TEXT);")
        conn.execute("INSERT INTO t (a) VALUES ('x');")
        row = conn.execute("SELECT a FROM t;").fetchone()
        assert row["a"] == "x"  # sqlite3.Row enables name access
    finally:
        conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.store'`.

- [ ] **Step 3: Implement `store.py`**

`noc_cli/store.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection, creating the parent directory if needed.

    Consumers (memory in the investigate plan, watch-state in the watch plan)
    own their own `CREATE TABLE IF NOT EXISTS` DDL; this primitive only
    guarantees a consistently configured connection at the right path.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -v`
Expected: PASS (all foundation tests green).

```bash
git add noc_cli/store.py tests/test_store.py
git commit -m "feat: SQLite connection primitive"
```

---

## Task 6: Showable skeleton — branded banner + stubbed command surface

Turns Foundation into a demoable artifact: `noc-cli --help` lists the full planned command surface, each command prints a branded "coming soon" notice, and `noc-cli --version` shows a styled banner. Each later plan replaces its stub with the real command.

**Files:**
- Create: `noc_cli/branding.py`
- Modify: `noc_cli/cli.py`
- Test: `tests/test_cli.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (the `app`, `runner`, and `__version__` imports already exist from Task 1):

```python
from rich.console import Console

from noc_cli import branding


def test_render_banner_includes_name_and_tagline():
    console = Console(record=True, width=80)
    branding.render_banner(console)
    out = console.export_text()
    assert "noc-cli" in out
    assert "Carbyne APEX" in out


def test_help_lists_full_command_surface():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("setup", "doctor", "investigate", "watch"):
        assert command in result.stdout


def test_stub_command_reports_coming_soon():
    result = runner.invoke(app, ["investigate", "12345"])
    assert result.exit_code == 0
    assert "not built yet" in result.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_cli.branding'`, plus the missing-command assertions.

- [ ] **Step 3: Implement the banner and stubbed commands**

Create `noc_cli/branding.py`:

```python
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

TAGLINE = "Read-only NOC triage · Carbyne APEX NG911/E911"


def render_banner(console: Console | None = None) -> None:
    """Print the noc-cli brand banner. Reused by the TUI plans."""
    console = console or Console()
    console.print(Panel.fit(Text("noc-cli", style="bold cyan"), border_style="cyan"))
    console.print(TAGLINE, style="dim")
```

Replace `noc_cli/cli.py` with:

```python
from __future__ import annotations

import typer

from noc_cli import __version__, branding

app = typer.Typer(
    name="noc-cli",
    help="Read-only NOC triage assistant for Carbyne APEX NG911/E911.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        branding.render_banner()
        typer.echo(f"noc-cli {__version__}")
        raise typer.Exit()


def _coming_soon(name: str, arriving_in: str) -> None:
    typer.secho(
        f"🚧 noc-cli {name} is not built yet — arriving in {arriving_in}.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=0)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Read-only NOC triage assistant."""


@app.command()
def setup() -> None:
    """Interactive first-run onboarding (Zendesk creds, paths, watch, notifications)."""
    _coming_soon("setup", "the setup & doctor plan")


@app.command()
def doctor() -> None:
    """Health-check credentials, paths, the Claude Code engine, and notifications."""
    _coming_soon("doctor", "the setup & doctor plan")


@app.command()
def investigate(ticket: str) -> None:
    """Run the L3 agent investigation on a Zendesk ticket (id or URL)."""
    _coming_soon("investigate", "the investigate plan")


@app.command()
def watch() -> None:
    """Watch a Zendesk queue and notify on status changes to your tickets."""
    _coming_soon("watch", "the watch plan")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS. Demo checks: `uv run noc-cli --version` shows the banner; `uv run noc-cli --help` lists all four commands; `uv run noc-cli investigate 123` prints the coming-soon notice.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -v`
Expected: PASS (all foundation tests green, including the original `test_version_flag_prints_version`).

```bash
git add noc_cli/branding.py noc_cli/cli.py tests/test_cli.py
git commit -m "feat: showable skeleton — branded banner + stubbed command surface"
```

---

## Self-Review

**Spec coverage (foundation-relevant sections of `2026-06-02-noc-cli-design.md`):**
- §5 stack (Typer/httpx/pydantic/sqlite3/dotenv) → established in Task 1 deps + Tasks 2–5. ✓ (Textual/Rich/claude-agent-sdk are intentionally deferred to Plans 3–4.)
- §7 modules `config.py`, `models.py`, `zendesk.py`, plus the shared SQLite layer → Tasks 2, 3, 4, 5. ✓
- §14 config keys (`ZENDESK_*`, `NOC_TICKETS_ROOT`, `NOC_OWNER`, watch view/assignee, notify) → `_FIELD_ENV` in Task 2. ✓ (Interactive *writing* of these is Plan 2 `setup`; foundation only *reads*.)
- §12 read-only Zendesk → `ZendeskClient` exposes only GETs; class docstring states no writes. ✓
- Data-dir / `NOC_HOME` override (sister-app `TRIAGE_HOME` parallel) → Task 2. ✓

**Placeholder scan:** No TBD/TODO; every code and test step contains complete, runnable content. ✓

**Type consistency:** `Config.zendesk_base_url` (Task 2) is consumed by `ZendeskClient` (Task 4). `Ticket`/`Comment` (Task 3) are returned by the client (Task 4) and asserted in its tests. `store.connect` returns `sqlite3.Connection` used directly in its test. `config.db_path()` is defined for later plans (memory/watch) but not consumed within this plan — intentional, documented in `store.py`. ✓

**Deferred-but-referenced check:** `setup`/`doctor`/`investigate`/`watch` are registered as **styled stubs** (Task 6); each later plan replaces its stub with the real command. The CLI shell lists the full surface via `--help`. ✓
