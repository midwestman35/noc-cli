import os
from pathlib import Path

from noc_cli.config import Config
from noc_cli.doctor import (
    check_claude_engine,
    check_notification_capability,
    check_tickets_dir_writable,
    check_zendesk_credentials_present,
)


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
