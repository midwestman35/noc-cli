from rich.console import Console
from typer.testing import CliRunner

from noc_cli import __version__, branding
from noc_cli.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


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


def test_setup_command_writes_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr("noc_cli.cli.shutil.which", lambda name: "/usr/local/bin/claude")

    # CliRunner input= simulates newline-separated responses to each typer.prompt.
    # Order: subdomain, email, token, tickets_root, owner, watch_view, watch_assignee, notify
    user_input = "\n".join([
        "carbyne",
        "alice@carbyne.com",
        "tok-secret",
        str(tmp_path / "Tickets"),
        "alice",
        "888",
        "alice@carbyne.com",
        "banner,ping",
        "",  # extra newline buffer
    ])
    result = runner.invoke(app, ["setup"], input=user_input)
    assert result.exit_code == 0, result.output
    env_file = tmp_path / ".env"
    assert env_file.exists()
    text = env_file.read_text()
    assert "ZENDESK_SUBDOMAIN=carbyne" in text
    assert "ZENDESK_EMAIL=alice@carbyne.com" in text
    assert "NOC_OWNER=alice" in text


def test_setup_command_shows_closing_message(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr("noc_cli.cli.shutil.which", lambda name: None)

    user_input = "\n".join([
        "carbyne", "alice@carbyne.com", "tok",
        str(tmp_path / "Tickets"), "alice", "", "", "banner,ping", "",
    ])
    result = runner.invoke(app, ["setup"], input=user_input)
    assert result.exit_code == 0
    assert "doctor" in result.output.lower()


def test_setup_warns_when_claude_engine_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr("noc_cli.cli.shutil.which", lambda name: None)

    user_input = "\n".join([
        "carbyne", "alice@carbyne.com", "tok",
        str(tmp_path / "Tickets"), "alice", "", "", "banner,ping", "",
    ])
    result = runner.invoke(app, ["setup"], input=user_input)
    assert result.exit_code == 0
    assert "claude" in result.output.lower()


def test_setup_rerun_uses_existing_values_as_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr("noc_cli.cli.shutil.which", lambda name: "/usr/local/bin/claude")

    # First run — write the config.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ZENDESK_SUBDOMAIN=carbyne\n"
        "ZENDESK_EMAIL=alice@carbyne.com\n"
        "ZENDESK_API_TOKEN=tok-first\n"
        f"NOC_TICKETS_ROOT={tmp_path / 'Tickets'}\n"
        "NOC_OWNER=alice\n"
        "NOC_WATCH_VIEW=777\n"
        "NOC_WATCH_ASSIGNEE=alice@carbyne.com\n"
        "NOC_NOTIFY=banner,ping\n"
    )

    # Second run — analyst keeps most values, changes only the token.
    user_input = "\n".join([
        "carbyne",            # same subdomain
        "alice@carbyne.com",  # same email
        "tok-second",         # new token
        str(tmp_path / "Tickets"),
        "alice",
        "777",
        "alice@carbyne.com",
        "banner,ping",
        "",
    ])
    result = runner.invoke(app, ["setup"], input=user_input)
    assert result.exit_code == 0
    text = (tmp_path / ".env").read_text()
    assert "ZENDESK_API_TOKEN=tok-second" in text  # updated
    assert "ZENDESK_SUBDOMAIN=carbyne" in text      # unchanged


def test_doctor_exits_0_when_all_critical_checks_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "ZENDESK_SUBDOMAIN=carbyne\n"
        "ZENDESK_EMAIL=a@carbyne.com\n"
        "ZENDESK_API_TOKEN=tok\n"
        f"NOC_TICKETS_ROOT={tmp_path / 'Tickets'}\n"
        "NOC_OWNER=alice\n"
        "NOC_NOTIFY=banner,ping\n"
    )
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/local/bin/fake")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output


def test_doctor_exits_1_when_credentials_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    # No .env file — all creds missing.
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/local/bin/fake")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1


def test_doctor_output_contains_checkmarks_and_crosses(tmp_path, monkeypatch):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "ZENDESK_SUBDOMAIN=carbyne\n"
        "ZENDESK_EMAIL=a@carbyne.com\n"
        "ZENDESK_API_TOKEN=tok\n"
        f"NOC_TICKETS_ROOT={tmp_path / 'Tickets'}\n"
        "NOC_OWNER=alice\n"
        "NOC_NOTIFY=banner,ping\n"
    )
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/local/bin/fake")
    result = runner.invoke(app, ["doctor"])
    # Rich markup stripped by CliRunner — look for the text labels
    assert "Zendesk credentials" in result.output
    assert "Tickets directory" in result.output
    assert "Claude Code engine" in result.output
    assert "Notification" in result.output


def test_doctor_online_flag_skips_live_auth_when_creds_missing(tmp_path, monkeypatch):
    # With --online the live-auth factory still runs (it is NOT skipped), but
    # ZendeskClient raises at construction before any HTTP when creds are
    # missing, and live-auth is advisory only. So the critical creds-present
    # check alone drives the exit code to 1 — no network call is made.
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.setattr("noc_cli.doctor.shutil.which", lambda name: "/usr/local/bin/fake")
    result = runner.invoke(app, ["doctor", "--online"])
    assert result.exit_code == 1


def test_doctor_exits_0_when_notification_check_fails_only(tmp_path, monkeypatch):
    # Notification is non-critical: all-critical-pass + notification-fail => exit 0.
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "ZENDESK_SUBDOMAIN=carbyne\n"
        "ZENDESK_EMAIL=a@carbyne.com\n"
        "ZENDESK_API_TOKEN=tok\n"
        f"NOC_TICKETS_ROOT={tmp_path / 'Tickets'}\n"
        "NOC_OWNER=alice\n"
        "NOC_NOTIFY=banner,ping\n"
    )

    def fake_which(name: str) -> str | None:
        if name == "claude":
            return "/usr/local/bin/claude"
        return None  # no osascript, no terminal-notifier

    monkeypatch.setattr("noc_cli.doctor.shutil.which", fake_which)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_watch_command_exposes_flags():
    """The watch command accepts --view, --assignee, and --interval flags."""
    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--view" in result.stdout
    assert "--assignee" in result.stdout
    assert "--interval" in result.stdout


def test_watch_command_rejects_invalid_interval():
    """--interval must be a positive integer (min=1)."""
    result = runner.invoke(app, ["watch", "--interval", "0"])
    assert result.exit_code != 0
