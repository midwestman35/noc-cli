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


def test_stub_command_reports_coming_soon():
    result = runner.invoke(app, ["investigate", "12345"])
    assert result.exit_code == 0
    assert "not built yet" in result.stdout


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
