"""Regression tests for the investigate evidence→agent seam.

These guard the bug where `investigate <id>` fetched the ticket but never
landed any of it in the agent sandbox, and the runner then could not parse the
agent's (correct) "no evidence" reply. See the two layers:

  1. fetch → sandbox: the ticket body + comment attachments must be written
     into logs/ for the agent to read.
  2. agent → parse: the runner must tolerate prose-wrapped JSON and the
     schema must tolerate the shapes the model actually emits.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from noc_cli.agent.runner import _extract_json_from_result
from noc_cli.cli import app
from noc_cli.scaffold import scaffold_ticket

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test")
    monkeypatch.setenv("ZENDESK_EMAIL", "a@b.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "tok")
    monkeypatch.setenv("NOC_TICKETS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOC_HOME", str(tmp_path))


# ── Layer 2a: runner JSON extraction tolerates prose ────────────────────────


def test_extract_json_skips_leading_prose_and_fence():
    raw = (
        "Some explanatory preamble.\n"
        "`★ Insight ───`\n\n"
        "More prose before the block.\n\n"
        '```json\n{"fork_letter": "D", "ok": true}\n```\n'
    )
    extracted = _extract_json_from_result(raw)
    assert json.loads(extracted) == {"fork_letter": "D", "ok": True}


def test_extract_json_bare_braces_without_fence():
    raw = 'prose here\n{"a": 1, "b": [2, 3]}\ntrailing prose'
    extracted = _extract_json_from_result(raw)
    assert json.loads(extracted) == {"a": 1, "b": [2, 3]}


# ── Layer 2b: schema tolerates the shapes the model emits ───────────────────


def _good_handoff_dict() -> dict:
    return json.loads((FIXTURES / "handoff_good.json").read_text())


def test_context_pulls_accepts_plain_strings():
    from noc_cli.models import Handoff

    data = _good_handoff_dict()
    data["intake"]["context_pulls"] = [
        "Glob Tickets/45884/** — only events.jsonl present",
        "ListMcpResourcesTool — no Zendesk MCP available",
    ]
    handoff = Handoff.model_validate(data)
    assert handoff.intake.context_pulls[0].pull.startswith("Glob")
    assert handoff.intake.context_pulls[1].pull.startswith("ListMcp")


def test_intake_decision_coerces_unknown_blocked_value():
    from noc_cli.models import Handoff, IntakeDecision

    data = _good_handoff_dict()
    data["intake"]["intake_decision"] = "blocked_missing_evidence"
    handoff = Handoff.model_validate(data)
    assert handoff.intake.intake_decision is IntakeDecision.CANNOT_PROCEED


def test_gathered_evidence_accepts_plain_strings():
    from noc_cli.models import Handoff

    data = _good_handoff_dict()
    data["evidence_preflight"]["gathered"] = [
        "events.jsonl — session journal only, not ticket evidence",
    ]
    handoff = Handoff.model_validate(data)
    assert handoff.evidence_preflight.gathered[0].summary.startswith("events.jsonl")


def test_historical_matches_accepts_plain_strings():
    from noc_cli.models import Handoff

    data = _good_handoff_dict()
    data["fork_packet"]["historical_matches"] = [
        "Ticket #41675: Cobb site network error — resolved by switch reboot",
    ]
    handoff = Handoff.model_validate(data)
    assert handoff.fork_packet.historical_matches[0].subject.startswith("Ticket #41675")


def test_try_parse_handles_real_world_agent_reply():
    """The exact shape that failed in the field: prose + ★Insight block, then a
    fenced JSON whose intake.context_pulls and evidence_preflight.gathered are
    plain strings and whose intake_decision is an invented 'blocked_missing_evidence'.
    Mirrors Tickets/45884 attempts 1 & 2 without depending on the runtime stash."""
    from noc_cli.agent.runner import _try_parse
    from noc_cli.models import ForkLetter

    data = _good_handoff_dict()
    data["intake"]["context_pulls"] = ["Glob ** — only events.jsonl present"]
    data["intake"]["intake_decision"] = "blocked_missing_evidence"
    data["evidence_preflight"]["gathered"] = ["events.jsonl — session journal only"]
    data["fork_packet"]["fork_letter"] = "D"
    data["fork_packet"]["confidence"] = "Inconclusive"
    data["fork_packet"]["missing_evidence"] = ["ticket body", "station logs"]

    raw = (
        "`★ Insight ───`\n"
        "No evidence in the working directory; Fork D is mandatory.\n\n"
        "```json\n" + json.dumps(data, indent=2) + "\n```\n"
    )
    handoff = _try_parse(raw)
    assert handoff is not None
    assert handoff.fork_packet.fork_letter is ForkLetter.D


# ── Layer 1: fetch lands the ticket body in the sandbox ─────────────────────


def test_write_ticket_source_writes_redactable_markdown(tmp_path):
    from noc_cli.evidence import write_ticket_source
    from noc_cli.models import Comment, Ticket

    folder = scaffold_ticket(tmp_path, 7)
    ticket = Ticket(
        id=7,
        subject="All consoles network error",
        description="Stations flipped ERROR at 06:30 UTC",
        status="open",
        tags=["apex"],
    )
    comments = [Comment(id=1, public=True, body="Audio dropped on station 12")]
    path = write_ticket_source(folder, ticket, comments)

    assert path.exists()
    assert path.parent == folder.logs  # under logs/ so the redact pass scrubs PII
    text = path.read_text()
    assert "All consoles network error" in text
    assert "Stations flipped ERROR" in text
    assert "Audio dropped on station 12" in text


# ── Capstone: the whole investigate path lands evidence + renders ───────────


def test_investigate_persists_ticket_and_attachments_into_sandbox(
    tmp_path, monkeypatch
):
    """The real (non-fixture) path must land ticket body + attachments in the
    sandbox and then render. This is the regression that the mocked suite missed."""
    from noc_cli.agent.runner import RunnerResult as _RR  # noqa: F811
    from noc_cli.models import Attachment, Comment, Handoff, Ticket

    _base_env(monkeypatch, tmp_path)

    class FakeZD:
        def __init__(self, cfg, client=None):
            pass

        def get_ticket(self, ticket_id):
            return Ticket(
                id=ticket_id,
                subject="All consoles network error",
                description="Stations flipped ERROR at 06:30 UTC",
                status="open",
                tags=["apex"],
            )

        def get_comments(self, ticket_id):
            return [
                Comment(
                    id=1,
                    public=True,
                    body="Audio dropped on station 12",
                    attachments=[
                        Attachment(
                            file_name="kamailio.log",
                            content_url="https://cdn.zendesk.example/a/kamailio.log",
                            size=36,
                        )
                    ],
                )
            ]

        def download_attachment(self, url):
            return b"SIP/2.0 480 Temporarily Unavailable"

        def search(self, query):
            return []

    monkeypatch.setattr("noc_cli.zendesk.ZendeskClient", FakeZD)
    monkeypatch.setattr("noc_cli.history.seed_history", lambda *a, **k: [])

    handoff = Handoff.model_validate(_good_handoff_dict())

    async def fake_run_agent(**kwargs):
        return _RR(handoff=handoff, attempts=1)

    monkeypatch.setattr("noc_cli.agent.runner.run_agent", fake_run_agent)

    async def fake_select_runbook(ticket_text, hypothesis, **kwargs):
        from noc_cli.grounding import RunbookSelection

        return RunbookSelection(slug=None, confidence="low", rationale="stub")

    monkeypatch.setattr("noc_cli.grounding.select_runbook", fake_select_runbook)

    result = runner.invoke(app, ["investigate", "18432"])
    assert result.exit_code == 0, result.output

    logs = tmp_path / "18432" / "logs"
    assert (logs / "00-ticket.md").exists(), "ticket body not persisted for the agent"
    assert "network error" in (logs / "00-ticket.md").read_text()
    assert (logs / "kamailio.log").exists(), "attachment not downloaded into sandbox"
    assert (tmp_path / "18432" / "INTAKE.md").exists()
