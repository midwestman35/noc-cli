from noc_cli.scout.models import Candidate, RankedCandidate, ScoutReport, ScreenReport


def test_candidate_defaults():
    candidate = Candidate(ticket_id=10)

    assert candidate.score == 0.0
    assert candidate.staleness_days == 0


def test_screen_report_ignores_extra_keys():
    report = ScreenReport.model_validate(
        {
            "ticket_id": 10,
            "runbook_id": "low-audio",
            "runbook_match_confidence": 0.8,
            "triage_ready": True,
            "missing_evidence": ["pcap"],
            "one_line": "ok",
            "chatter": "ignored",
        }
    )

    assert report.runbook_id == "low-audio"
    assert report.missing_evidence == ["pcap"]


def test_scout_report_holds_ranked_list():
    report = ScoutReport(
        ranked=[RankedCandidate(ticket_id=10, rank=1, rationale="why")]
    )

    assert report.ranked[0].rank == 1
