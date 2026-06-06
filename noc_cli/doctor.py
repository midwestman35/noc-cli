"""noc-cli doctor — individual health-check functions.

Each `check_*` function returns a `CheckResult(ok, label, message)`.
`run_checks` collects them; `print_report` renders and returns an exit code.
All external dependencies (shutil.which, ZendeskClient construction) are
importable at the module level or injected so tests can monkeypatch cleanly.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from noc_cli.config import Config


@dataclass
class CheckResult:
    ok: bool
    label: str
    message: str


# ── Individual checks ─────────────────────────────────────────────────────────


def check_zendesk_credentials_present(config: Config) -> CheckResult:
    """Verify all three Zendesk credential env vars are non-empty."""
    missing = []
    if not config.zendesk_subdomain:
        missing.append("ZENDESK_SUBDOMAIN")
    if not config.zendesk_email:
        missing.append("ZENDESK_EMAIL")
    if not config.zendesk_api_token:
        missing.append("ZENDESK_API_TOKEN")

    if missing:
        return CheckResult(
            ok=False,
            label="Zendesk credentials",
            message=f"Missing: {', '.join(missing)}. Run `noc-cli setup` to configure.",
        )
    return CheckResult(
        ok=True,
        label="Zendesk credentials",
        message=f"Configured for {config.zendesk_subdomain}.zendesk.com as {config.zendesk_email}.",
    )


def check_tickets_dir_writable(config: Config) -> CheckResult:
    """Verify the tickets root dir exists (creating it if needed) and is writable."""
    tickets_dir = config.tickets_root
    try:
        tickets_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(
            ok=False,
            label="Tickets directory",
            message=f"Cannot create {tickets_dir}: {exc}.",
        )

    if not os.access(tickets_dir, os.W_OK):
        return CheckResult(
            ok=False,
            label="Tickets directory",
            message=f"{tickets_dir} is not writable.",
        )
    return CheckResult(
        ok=True,
        label="Tickets directory",
        message=str(tickets_dir),
    )


def check_claude_engine() -> CheckResult:
    """Check whether the `claude` CLI is on PATH (best-effort engine probe)."""
    path = shutil.which("claude")
    if path is None:
        return CheckResult(
            ok=False,
            label="Claude Code engine",
            message=(
                "`claude` not found on PATH. Install Claude Code and log in: "
                "https://claude.ai/claude-code"
            ),
        )
    return CheckResult(
        ok=True,
        label="Claude Code engine",
        message=path,
    )


def check_notification_capability() -> CheckResult:
    """Check whether osascript or terminal-notifier is available (macOS)."""
    tn = shutil.which("terminal-notifier")
    if tn:
        return CheckResult(
            ok=True,
            label="Notification",
            message=f"terminal-notifier at {tn} (preferred).",
        )
    osa = shutil.which("osascript")
    if osa:
        return CheckResult(
            ok=True,
            label="Notification",
            message=f"osascript at {osa} (built-in macOS).",
        )
    return CheckResult(
        ok=False,
        label="Notification",
        message=(
            "Notifier not found: neither `terminal-notifier` nor `osascript` "
            "is on PATH. OS desktop notifications will not fire."
        ),
    )


# ── Zendesk live-auth probe (optional; gated behind factory) ─────────────────


def check_zendesk_live_auth(
    config: Config,
    zd_factory: Callable[[Config], object] | None = None,
) -> CheckResult:
    """Best-effort live Zendesk auth check.

    `zd_factory` is a callable(config) -> ZendeskClient-like object with a
    `get_ticket(int)` method. Pass None to skip the check entirely (e.g. in CI).
    In production, pass `lambda cfg: ZendeskClient(cfg)`.

    The probe calls `get_ticket(1)`. A 404 means creds are valid (ticket 1 just
    doesn't exist). A ZendeskError with "auth failed" means the credentials are
    wrong. Any other exception is treated as a transient network failure and
    returns a warning (ok=True, non-fatal) so a broken network doesn't block setup.
    """
    if zd_factory is None:
        return CheckResult(
            ok=True,
            label="Zendesk live auth",
            message="Skipped (offline / CI mode).",
        )
    try:
        client = zd_factory(config)
        client.get_ticket(1)  # 404 is fine; 401 is the signal we care about
    except Exception as exc:
        msg = str(exc)
        if "auth failed" in msg.lower():
            return CheckResult(
                ok=False,
                label="Zendesk live auth",
                message=f"Auth rejected by Zendesk: {msg}",
            )
        # Network errors, 404, etc. — non-fatal; credentials may still be correct.
        return CheckResult(
            ok=True,
            label="Zendesk live auth",
            message=f"Could not confirm (network/404): {msg}",
        )
    return CheckResult(
        ok=True,
        label="Zendesk live auth",
        message="Authenticated successfully.",
    )


# ── Collector + renderer ──────────────────────────────────────────────────────


def run_checks(
    config: Config,
    which_fn: Callable[[str], str | None] | None = None,
    zd_factory: Callable[[Config], object] | None = None,
) -> list[CheckResult]:
    """Run all checks and return a list of CheckResult in display order.

    `which_fn` is not used at this layer (individual checks import shutil
    directly so tests monkeypatch `noc_cli.doctor.shutil.which`). The
    parameter is kept for future extensibility without breaking callers.
    """
    return [
        check_zendesk_credentials_present(config),
        check_tickets_dir_writable(config),
        check_claude_engine(),
        check_notification_capability(),
        check_zendesk_live_auth(config, zd_factory=zd_factory),
    ]


def print_report(results: list[CheckResult], console: Console | None = None) -> int:
    """Render the check results to the console and return an exit code.

    Returns 0 if all critical checks pass, 1 otherwise. The notification check
    is non-critical (warning only); the Zendesk live-auth check is advisory.
    Critical checks: Zendesk credentials present, tickets dir writable,
    Claude engine reachable.
    """
    con = console or Console()
    critical_labels = {
        "Zendesk credentials",
        "Tickets directory",
        "Claude Code engine",
    }

    all_critical_ok = True
    for result in results:
        icon = "[green]✓[/green]" if result.ok else "[red]✗[/red]"
        con.print(f"  {icon}  {result.label}: {result.message}")
        if not result.ok and result.label in critical_labels:
            all_critical_ok = False

    return 0 if all_critical_ok else 1
