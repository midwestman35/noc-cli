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
