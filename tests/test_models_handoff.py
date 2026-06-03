import json

import pytest
from pydantic import ValidationError

from noc_cli.models import (
    APPROVED_SYMPTOM_TAGS,
    Confidence,
    ForkLetter,
    Handoff,
)


def minimal_handoff_dict(**overrides):
    base = {
        "rubric_version": "2026-05-13",
        "intake": {
            "ticket_id": 18432,
            "url": "https://carbyne.zendesk.com/agent/tickets/18432",
            "status": "open",
            "tags": ["apex"],
            "requester": "PSAP Ops",
            "organization": "Aurora 911",
            "one_line_fingerprint": "Aurora / apex / Network Error / 06:30 UTC",
            "ticket_summary": ["Brief all-console network error"],
            "context_pulls": [
                {"pull": "Last 3 tickets", "result": "none similar", "source": "Zendesk"}
            ],
            "initial_hypothesis": "Fork B (site network)",
            "intake_decision": "ready_for_evidence_preflight",
        },
        "evidence_preflight": {
            "gathered": [
                {
                    "evidence_type": "station log",
                    "source": "Aurora-12",
                    "time_window": "06:30 UTC",
                    "summary": "RECONNECT_ON_DRAINING",
                }
            ],
            "decisive_evidence": ["Multiple stations flipped within seconds"],
            "missing_or_non_decisive": ["No switch logs"],
        },
        "fork_packet": {
            "fork_letter": "B",
            "confidence": "Medium",
            "symptom_tag": "[apex]",
            "rubric_class": "Symptom Class 3",
            "quoted_rubric_row": "customer LAN, switch, or SDWAN. Link to site master ticket",
            "reasoning": "Multi-station within seconds is Class 3 (b)",
            "evidence_summary": ["3 stations ERROR in 4s"],
            "missing_evidence": [],
            "runbook_reference": {
                "slug": "apex",
                "section": "Multiple stations at one site flip ERROR within seconds -> Fork B",
            },
            "historical_matches": [
                {
                    "ticket_id": "41675",
                    "subject": "Cobb site network error",
                    "relevance": "same multi-station pattern",
                    "resolution": "linked to site master",
                }
            ],
            "related_zendesk": [41675],
            "related_jira": [],
        },
        "drafts": {
            "customer_reply": "Hi — we saw a brief network interruption ...",
            "internal_note": "Fork B; site LAN. Rubric row quoted.",
            "jira_draft": None,
        },
    }
    base.update(overrides)
    return base


def test_minimal_handoff_parses_and_exposes_fields():
    h = Handoff.model_validate(minimal_handoff_dict())
    assert h.intake.ticket_id == 18432
    assert h.fork_packet.fork_letter is ForkLetter.B
    assert h.fork_packet.confidence is Confidence.MEDIUM
    assert h.fork_packet.symptom_tag == "[apex]"
    assert h.fork_packet.runbook_reference.slug == "apex"
    assert h.fork_packet.historical_matches[0].ticket_id == "41675"
    assert h.drafts.jira_draft is None


def test_fork_letter_rejects_lowercase_and_unknown():
    with pytest.raises(ValidationError):
        Handoff.model_validate(minimal_handoff_dict(
            fork_packet={**minimal_handoff_dict()["fork_packet"], "fork_letter": "a"}
        ))


def test_symptom_tag_must_be_in_approved_set():
    bad = minimal_handoff_dict()
    bad["fork_packet"]["symptom_tag"] = "[ghost]"
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_unclassified_is_an_approved_symptom_tag():
    ok = minimal_handoff_dict()
    ok["fork_packet"]["symptom_tag"] = "[unclassified]"
    h = Handoff.model_validate(ok)
    assert h.fork_packet.symptom_tag == "[unclassified]"


def test_vendor_is_not_an_approved_symptom_tag():
    assert "[vendor]" not in APPROVED_SYMPTOM_TAGS
    bad = minimal_handoff_dict()
    bad["fork_packet"]["symptom_tag"] = "[vendor]"
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_fork_d_requires_missing_evidence():
    bad = minimal_handoff_dict()
    bad["fork_packet"]["fork_letter"] = "D"
    bad["fork_packet"]["confidence"] = "Inconclusive"
    bad["fork_packet"]["missing_evidence"] = []
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_fork_d_with_high_confidence_is_incoherent():
    bad = minimal_handoff_dict()
    bad["fork_packet"]["fork_letter"] = "D"
    bad["fork_packet"]["confidence"] = "High"
    bad["fork_packet"]["missing_evidence"] = ["need server-side logs"]
    with pytest.raises(ValidationError):
        Handoff.model_validate(bad)


def test_extra_fields_are_ignored():
    d = minimal_handoff_dict()
    d["fork_packet"]["telemetry"] = {"unused": True}
    h = Handoff.model_validate(d)  # must not raise
    assert h.fork_packet.fork_letter is ForkLetter.B


def test_handoff_round_trips_via_json():
    h = Handoff.model_validate(minimal_handoff_dict())
    again = Handoff.model_validate(json.loads(h.model_dump_json()))
    assert again.fork_packet.symptom_tag == "[apex]"
