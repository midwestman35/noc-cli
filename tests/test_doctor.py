import os
from pathlib import Path

from rich.console import Console

from noc_cli.config import Config
from noc_cli.doctor import (
    check_claude_engine,
    check_notification_capability,
    check_tickets_dir_writable,
    check_zendesk_credentials_present,
    check_zendesk_live_auth,
    print_report,
    run_checks,
)
from noc_cli.zendesk import ZendeskError


def _cfg(**kwargs) -> Config:
    defaults = dict(
        zendesk_subdomain="carbyne",
        zendesk_email="a@b.com",
        zendesk_api_token="tok",
        tickets_root=Path("/tmp/tickets"),
        owner="alice",
        watch_view="",
        watch_assignee="",
        notify="banner,ping",
    )
    defaults.update(kwargs)
    return Config(**defaults)


# ── check_zendesk_credentials_present ────────────────────────────────────────


def test_creds_present_returns_ok_when_all_three_set():
    result = check_zendesk_credentials_present(_cfg())
    assert result.ok is True
    assert "carbyne" in result.message


def test_creds_present_returns_fail_when_subdomain_missing():
    result = check_zendesk_credentials_present(_cfg(zendesk_subdomain=""))
    assert result.ok is False
    assert "ZENDESK_SUBDOMAIN" in result.message


def test_creds_present_returns_fail_when_email_missing():
    result = check_zendesk_credentials_present(_cfg(zendesk_email=""))
    assert result.ok is False
    assert "ZENDESK_EMAIL" in result.message


def test_creds_present_returns_fail_when_token_missing():
    result = check_zendesk_credentials_present(_cfg(zendesk_api_token=""))
    assert result.ok is False
    assert "ZENDESK_API_TOKEN" in result.message


# ── check_tickets_dir_writable ────────────────────────────────────────────────


def test_tickets_dir_writable_returns_ok_for_real_writable_dir(tmp_path):
    result = check_tickets_dir_writable(_cfg(tickets_root=tmp_path))
    assert result.ok is True
    assert str(tmp_path) in result.message


def test_tickets_dir_writable_creates_dir_if_absent(tmp_path):
    new_dir = tmp_path / "new_tickets"
    assert not new_dir.exists()
    result = check_tickets_dir_writable(_cfg(tickets_root=new_dir))
    assert result.ok is True
    assert new_dir.exists()


def test_tickets_dir_writable_returns_fail_for_unwritable_dir(tmp_path, monkeypatch):
    # mkdir succeeds (tmp_path is writable); patch os.access so the
    # writability gate fails — exercises the "not writable" branch.
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    result = check_tickets_dir_writable(_cfg(tickets_root=tmp_path / "nope"))
    assert result.ok is False
    assert "not writable" in result.message.lower()


def test_tickets_dir_writable_returns_fail_when_mkdir_raises(tmp_path, monkeypatch):
    # Force Path.mkdir to raise OSError — exercises the "Cannot create" branch.
    def boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", boom)
    result = check_tickets_dir_writable(_cfg(tickets_root=tmp_path / "nope"))
    assert result.ok is False
    assert "Cannot create" in result.message


# ── check_claude_engine ───────────────────────────────────────────────────────


def test_claude_engine_ok_when_which_returns_path(monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/local/bin/claude")
    result = check_claude_engine()
    assert result.ok is True
    assert "/usr/local/bin/claude" in result.message


def test_claude_engine_fails_when_which_returns_none(monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: None)
    result = check_claude_engine()
    assert result.ok is False
    assert "not found" in result.message.lower()


# ── check_notification_capability ────────────────────────────────────────────


def test_notification_ok_when_terminal_notifier_present(monkeypatch):
    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/terminal-notifier" if name == "terminal-notifier" else None

    monkeypatch.setattr("noc_cli.doctor.shutil.which", fake_which)
    result = check_notification_capability()
    assert result.ok is True
    assert "terminal-notifier" in result.message


def test_notification_ok_when_only_osascript_present(monkeypatch):
    def fake_which(name: str) -> str | None:
        return "/usr/bin/osascript" if name == "osascript" else None

    monkeypatch.setattr("noc_cli.doctor.shutil.which", fake_which)
    result = check_notification_capability()
    assert result.ok is True
    assert "osascript" in result.message


def test_notification_fails_when_neither_present(monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: None)
    result = check_notification_capability()
    assert result.ok is False
    assert "not found" in result.message.lower()


# ── run_checks + print_report integration ────────────────────────────────────


def test_run_checks_returns_five_results(tmp_path, monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/bin/fake")
    cfg = _cfg(tickets_root=tmp_path)
    results = run_checks(cfg, zd_factory=None)
    assert len(results) == 5
    labels = [r.label for r in results]
    assert "Zendesk credentials" in labels
    assert "Tickets directory" in labels
    assert "Claude Code engine" in labels
    assert "Notification" in labels
    assert "Zendesk live auth" in labels


def test_run_checks_skips_live_auth_when_factory_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/bin/fake")
    cfg = _cfg(tickets_root=tmp_path)
    results = run_checks(cfg, zd_factory=None)
    live_auth = next(r for r in results if r.label == "Zendesk live auth")
    assert live_auth.ok is True
    assert "Skipped" in live_auth.message


def test_print_report_returns_0_when_all_critical_pass(tmp_path, monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/bin/fake")
    cfg = _cfg(tickets_root=tmp_path)
    results = run_checks(cfg, zd_factory=None)
    console = Console(record=True)
    code = print_report(results, console=console)
    assert code == 0


def test_print_report_returns_1_when_credentials_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/bin/fake")
    cfg = _cfg(zendesk_subdomain="", zendesk_email="", zendesk_api_token="", tickets_root=tmp_path)
    results = run_checks(cfg, zd_factory=None)
    console = Console(record=True)
    code = print_report(results, console=console)
    assert code == 1


def test_print_report_returns_1_when_tickets_dir_not_writable(tmp_path, monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/bin/fake")
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    cfg = _cfg(tickets_root=tmp_path / "unwritable")
    results = run_checks(cfg, zd_factory=None)
    console = Console(record=True)
    code = print_report(results, console=console)
    assert code == 1


def test_print_report_returns_1_when_claude_engine_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: None)
    cfg = _cfg(tickets_root=tmp_path)
    results = run_checks(cfg, zd_factory=None)
    console = Console(record=True)
    code = print_report(results, console=console)
    # notification also fails when which returns None, but the critical check
    # is the Claude engine — exit code must be 1
    assert code == 1


def test_print_report_returns_0_when_only_notification_fails(tmp_path, monkeypatch):
    # Notification is a warning, not critical — exit code must be 0.
    def fake_which(name: str) -> str | None:
        if name == "claude":
            return "/usr/local/bin/claude"
        return None  # osascript and terminal-notifier absent

    monkeypatch.setattr("noc_cli.doctor.shutil.which", fake_which)
    cfg = _cfg(tickets_root=tmp_path)
    results = run_checks(cfg, zd_factory=None)
    console = Console(record=True)
    code = print_report(results, console=console)
    assert code == 0


def test_check_zendesk_live_auth_returns_fail_on_auth_error():
    def bad_factory(cfg: Config) -> object:
        class FakeClient:
            def get_ticket(self, _id: int) -> None:
                raise ZendeskError("Zendesk auth failed - check ZENDESK_EMAIL and ZENDESK_API_TOKEN.")
        return FakeClient()

    result = check_zendesk_live_auth(_cfg(), zd_factory=bad_factory)
    assert result.ok is False
    assert "Auth rejected" in result.message


def test_check_zendesk_live_auth_returns_ok_on_404():
    def not_found_factory(cfg: Config) -> object:
        class FakeClient:
            def get_ticket(self, _id: int) -> None:
                raise Exception("404 Not Found")
        return FakeClient()

    result = check_zendesk_live_auth(_cfg(), zd_factory=not_found_factory)
    assert result.ok is True  # 404 means creds are valid
    assert "network/404" in result.message


def test_check_zendesk_live_auth_returns_ok_on_success():
    def success_factory(cfg: Config) -> object:
        class FakeClient:
            def get_ticket(self, _id: int) -> object:
                return object()  # any non-exception result
        return FakeClient()

    result = check_zendesk_live_auth(_cfg(), zd_factory=success_factory)
    assert result.ok is True
    assert "Authenticated" in result.message
