from datetime import datetime, timedelta, timezone
from unittest import mock

from typer.testing import CliRunner

import httpx

from noc_cli.cli import app
from noc_cli.models import Ticket
from noc_cli.scout.writer import ZendeskWriteError
from noc_cli.scout.models import RankedCandidate, ScoutReport
from noc_cli.zendesk import ZendeskError

runner = CliRunner()
NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)

_REPORT = ScoutReport(
    generated_at=NOW,
    ranked=[
        RankedCandidate(
            ticket_id=42,
            rank=1,
            rationale="matches low-audio",
            runbook_id="low-audio",
            runbook_match_confidence=0.8,
        )
    ],
)


class _FakeClient:
    def __init__(self, ticket):
        self.ticket = ticket

    def get_ticket(self, ticket_id):
        assert ticket_id == 42
        return self.ticket


class _SequenceClient:
    def __init__(self, *tickets):
        self._tickets = list(tickets)

    def get_ticket(self, ticket_id):
        assert ticket_id == 42
        return self._tickets.pop(0)


def _stale_ticket(**updates):
    data = {
        "id": 42,
        "subject": "stale",
        "status": "open",
        "updated_at": NOW - timedelta(days=10),
    }
    data.update(updates)
    return Ticket(**data)


def test_scout_lists_candidates():
    with mock.patch("noc_cli.scout.commands.run_scout_report", return_value=_REPORT) as run:
        result = runner.invoke(app, ["scout"])

    assert result.exit_code == 0, result.output
    run.assert_called_once()
    assert "#42" in result.output
    assert "low-audio" in result.output


def test_scout_list_warns_about_dropped_screens():
    report = ScoutReport(
        generated_at=NOW,
        ranked=_REPORT.ranked,
        candidates_screened=3,
        reports_parsed=1,
    )
    with mock.patch(
        "noc_cli.scout.commands.run_scout_report", return_value=report
    ):
        result = runner.invoke(app, ["scout"])

    assert result.exit_code == 0, result.output
    assert "2 of 3 screens" in result.output


def test_scout_default_min_staleness_is_7_days():
    with mock.patch("noc_cli.scout.commands.run_scout_report", return_value=_REPORT) as run:
        result = runner.invoke(app, ["scout"])

    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["min_staleness_days"] == 7


def test_scout_take_confirms_then_assigns_and_investigates():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch("noc_cli.scout.commands.make_zendesk_client", return_value=_FakeClient(_stale_ticket())), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 0, result.output
    writer.assign_ticket.assert_called_once_with(42, 7)
    inv.assert_called_once_with(42)


def test_scout_take_aborts_without_confirmation():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch("noc_cli.scout.commands.make_zendesk_client", return_value=_FakeClient(_stale_ticket())), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42"], input="n\n")

    assert result.exit_code != 0 or "Aborted" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()


def test_scout_take_preflight_aborts_if_assigned():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch(
            "noc_cli.scout.commands.make_zendesk_client",
            return_value=_FakeClient(_stale_ticket(assignee_id=99)),
        ), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 2
    assert "already assigned" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()


def test_scout_take_preflight_aborts_if_solved():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch(
            "noc_cli.scout.commands.make_zendesk_client",
            return_value=_FakeClient(_stale_ticket(status="solved")),
        ), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 2
    assert "solved" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()


def test_scout_take_preflight_aborts_if_closed():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch(
            "noc_cli.scout.commands.make_zendesk_client",
            return_value=_FakeClient(_stale_ticket(status="closed")),
        ), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 2
    assert "closed" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()


def test_scout_take_preflight_aborts_if_updated_at_missing():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch(
            "noc_cli.scout.commands.make_zendesk_client",
            return_value=_FakeClient(_stale_ticket(updated_at=None)),
        ), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 2
    assert "updated_at" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()


def test_scout_take_preflight_aborts_if_no_longer_stale():
    writer = mock.MagicMock()
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch(
            "noc_cli.scout.commands.make_zendesk_client",
            return_value=_FakeClient(_stale_ticket(updated_at=NOW - timedelta(days=2))),
        ), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 2
    assert "no longer stale" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()


def test_scout_take_rechecks_after_confirmation_before_assigning():
    writer = mock.MagicMock()
    client = _SequenceClient(
        _stale_ticket(),
        _stale_ticket(assignee_id=99),
    )
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch("noc_cli.scout.commands.make_zendesk_client", return_value=client), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW), \
        mock.patch("noc_cli.scout.commands.invoke_investigate") as inv:
        result = runner.invoke(app, ["scout", "--take", "42"], input="y\n")

    assert result.exit_code == 2
    assert "already assigned" in result.output
    writer.assign_ticket.assert_not_called()
    inv.assert_not_called()


def test_scout_list_surfaces_zendesk_error():
    with mock.patch(
        "noc_cli.scout.commands.run_scout_report",
        side_effect=ZendeskError("auth failed"),
    ):
        result = runner.invoke(app, ["scout"])

    assert result.exit_code == 1
    assert "Zendesk error: auth failed" in result.output


def test_scout_list_surfaces_agent_failure():
    with mock.patch(
        "noc_cli.scout.commands.run_scout_report",
        side_effect=RuntimeError("agent unavailable"),
    ):
        result = runner.invoke(app, ["scout"])

    assert result.exit_code == 1
    assert "Scout failed: agent unavailable" in result.output


def test_scout_take_surfaces_zendesk_error():
    with mock.patch(
        "noc_cli.scout.commands.make_zendesk_client",
        side_effect=ZendeskError("not configured"),
    ):
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 1
    assert "Zendesk error: not configured" in result.output


def test_scout_take_surfaces_write_error():
    writer = mock.MagicMock()
    writer.assign_ticket.side_effect = ZendeskWriteError("auth failed on assign")
    with mock.patch("noc_cli.scout.commands.make_writer", return_value=writer), \
        mock.patch("noc_cli.scout.commands.resolve_owner_id", return_value=7), \
        mock.patch("noc_cli.scout.commands.make_zendesk_client", return_value=_FakeClient(_stale_ticket())), \
        mock.patch("noc_cli.scout.commands.now_utc", return_value=NOW):
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 1
    assert "Zendesk error: auth failed on assign" in result.output


def test_scout_take_surfaces_http_error():
    with mock.patch(
        "noc_cli.scout.commands.make_zendesk_client",
        side_effect=httpx.ConnectError("network down"),
    ):
        result = runner.invoke(app, ["scout", "--take", "42", "--yes"])

    assert result.exit_code == 1
    assert "Zendesk request failed" in result.output
