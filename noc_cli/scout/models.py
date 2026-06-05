from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Candidate(BaseModel):
    """Stage-1 output: a stale ticket worth screening, ranked by metadata."""

    ticket_id: int
    subject: str = ""
    status: str = ""
    priority: str = ""
    staleness_days: int = 0
    score: float = 0.0


class ScreenReport(BaseModel):
    """Stage-2 output: one read-only triage-readiness pre-screen."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: int
    runbook_id: str = ""
    runbook_match_confidence: float = 0.0
    triage_ready: bool = False
    missing_evidence: list[str] = Field(default_factory=list)
    one_line: str = ""


class RankedCandidate(BaseModel):
    """One entry in the synthesized ranking shown to the engineer."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: int
    rank: int
    rationale: str = ""
    runbook_id: str = ""
    runbook_match_confidence: float = 0.0
    missing_evidence: list[str] = Field(default_factory=list)


class ScoutReport(BaseModel):
    """Stage-3 output: ranked candidates needing human review."""

    model_config = ConfigDict(extra="ignore")

    generated_at: datetime | None = None
    ranked: list[RankedCandidate] = Field(default_factory=list)
    candidates_screened: int = 0
    reports_parsed: int = 0

    @property
    def dropped(self) -> int:
        """Screens whose JSON could not be parsed and were silently dropped.

        Surfaced so operators do not mistake a short list for a quiet backlog.
        """
        return max(self.candidates_screened - self.reports_parsed, 0)
