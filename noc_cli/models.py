from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Attachment(BaseModel):
    file_name: str
    content_url: str
    size: int = 0


class Comment(BaseModel):
    id: int
    author_id: int | None = None
    public: bool = True
    body: str = ""
    created_at: datetime | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class Ticket(BaseModel):
    id: int
    subject: str = ""
    description: str = ""
    requester_org: str | None = None
    requester_email: str | None = None
    requester_id: int | None = None     # Zendesk requester user id; used by the watcher diff
    assignee_email: str | None = None   # populated on view_tickets rows; used by the watcher
    status: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    comments: list[Comment] = Field(default_factory=list)


# ─── Two-axis classification enums ─────────────────────────────────────────


class ForkLetter(str, Enum):
    """A/B/C/D routing fork (see noc_cli/data/fork-rubric.md)."""

    A = "A"  # Engineering Jira
    B = "B"  # Vendor or Internal IT
    C = "C"  # NOC self-resolve
    D = "D"  # Cannot fork yet — evidence missing

    @property
    def description(self) -> str:
        return {
            "A": "Engineering Jira",
            "B": "Vendor or Internal IT",
            "C": "NOC self-resolve",
            "D": "Cannot fork yet",
        }[self.value]


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INCONCLUSIVE = "Inconclusive"


class IntakeDecision(str, Enum):
    READY_FOR_EVIDENCE_PREFLIGHT = "ready_for_evidence_preflight"
    KNOWN_ISSUE = "known_issue"
    NEEDS_CLARIFICATION = "needs_clarification"
    CANNOT_PROCEED = "cannot_proceed"


# The agent sometimes invents an intake_decision string for the Fork-D / blocked
# state (e.g. "blocked_missing_evidence"). Map the known synonyms onto the
# canonical enum so one stray label never fails the whole handoff.
_INTAKE_DECISION_SYNONYMS: dict[str, IntakeDecision] = {
    "blocked_missing_evidence": IntakeDecision.CANNOT_PROCEED,
    "blocked": IntakeDecision.CANNOT_PROCEED,
    "pending_evidence": IntakeDecision.CANNOT_PROCEED,
    "needs_evidence": IntakeDecision.CANNOT_PROCEED,
    "missing_evidence": IntakeDecision.CANNOT_PROCEED,
}


# The approved symptom-tag set (spec §17). [vendor] is deliberately NOT here
# (it is a history-exclusion tag, never a symptom). [unclassified] is the
# catch-all. The agent must emit exactly one of these.
APPROVED_SYMPTOM_TAGS: frozenset[str] = frozenset(
    {
        "[apex]",
        "[low audio]",
        "[dropped calls]",
        "[No ANI]",
        "[No ALI]",
        "[event history]",
        "[unclassified]",
    }
)


# ─── INTAKE.md ─────────────────────────────────────────────────────────────


class ContextPull(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pull: str = ""
    result: str = ""
    source: str = ""


class IntakeBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket_id: int
    url: str = ""
    status: str = ""
    priority: str = ""
    tags: list[str] = Field(default_factory=list)
    requester: str = ""
    organization: str = ""
    site: str | None = None
    cnc: str | None = None
    region: str | None = None
    affected_stations: list[str] = Field(default_factory=list)
    affected_agents: list[str] = Field(default_factory=list)
    call_id: str | None = None
    incident_window: str = ""
    one_line_fingerprint: str = ""
    ticket_summary: list[str] = Field(default_factory=list)
    context_pulls: list[ContextPull] = Field(default_factory=list)
    initial_hypothesis: str = ""
    intake_decision: IntakeDecision = IntakeDecision.READY_FOR_EVIDENCE_PREFLIGHT

    @field_validator("context_pulls", mode="before")
    @classmethod
    def _coerce_context_pulls(cls, v):
        # The agent often emits context_pulls as plain strings rather than
        # {pull, result, source} objects. Promote each string to ContextPull(pull=...).
        if isinstance(v, list):
            return [{"pull": item} if isinstance(item, str) else item for item in v]
        return v

    @field_validator("intake_decision", mode="before")
    @classmethod
    def _coerce_intake_decision(cls, v):
        # Accept the canonical enum, map known synonyms, and fall back to
        # needs_clarification for anything unrecognized — never hard-fail here.
        if isinstance(v, IntakeDecision):
            return v
        if isinstance(v, str):
            try:
                return IntakeDecision(v)
            except ValueError:
                return _INTAKE_DECISION_SYNONYMS.get(
                    v.strip().lower(), IntakeDecision.NEEDS_CLARIFICATION
                )
        return v


# ─── EVIDENCE_PREFLIGHT.md ─────────────────────────────────────────────────


class GatheredEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    evidence_type: str = ""
    source: str = ""
    time_window: str = ""
    summary: str = ""


class PreflightBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    gathered: list[GatheredEvidence] = Field(default_factory=list)
    decisive_evidence: list[str] = Field(default_factory=list)
    missing_or_non_decisive: list[str] = Field(default_factory=list)

    @field_validator("gathered", mode="before")
    @classmethod
    def _coerce_gathered(cls, v):
        # Like context_pulls, the agent often emits gathered as plain strings
        # rather than {evidence_type, source, summary, ...} objects. Promote each
        # string to GatheredEvidence(summary=...).
        if isinstance(v, list):
            return [{"summary": item} if isinstance(item, str) else item for item in v]
        return v


# ─── FORK_PACKET.md ────────────────────────────────────────────────────────


class RunbookReference(BaseModel):
    """The symptom-runbook section the agent quoted into FORK_PACKET.md."""

    model_config = ConfigDict(extra="ignore")
    slug: str = ""
    section: str = ""


class HistoricalMatch(BaseModel):
    """One prior ticket/investigation the agent selected as relevant (≤5)."""

    model_config = ConfigDict(extra="ignore")
    ticket_id: str = ""
    subject: str = ""
    relevance: str = ""
    resolution: str = "[unknown]"


class ForkPacket(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fork_letter: ForkLetter
    confidence: Confidence
    symptom_tag: str
    rubric_class: str = ""
    quoted_rubric_row: str = ""
    reasoning: str = ""
    evidence_summary: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    runbook_reference: RunbookReference = Field(default_factory=RunbookReference)
    historical_matches: list[HistoricalMatch] = Field(default_factory=list)
    related_zendesk: list[int] = Field(default_factory=list)
    related_jira: list[str] = Field(default_factory=list)
    master_ticket: int | None = None
    cluster: str | None = None

    @field_validator("historical_matches", mode="before")
    @classmethod
    def _coerce_historical_matches(cls, v):
        # Same string-vs-object pattern as context_pulls / gathered: promote a
        # bare string to HistoricalMatch(subject=...).
        if isinstance(v, list):
            return [{"subject": item} if isinstance(item, str) else item for item in v]
        return v

    @model_validator(mode="after")
    def _check_invariants(self) -> "ForkPacket":
        # Symptom tag must be in the approved set (spec §17).
        if self.symptom_tag not in APPROVED_SYMPTOM_TAGS:
            raise ValueError(
                f"symptom_tag {self.symptom_tag!r} is not in the approved set "
                f"{sorted(APPROVED_SYMPTOM_TAGS)}"
            )
        # Fork D (cannot fork yet) must name what is missing (spec §16/§18).
        if self.fork_letter is ForkLetter.D and not self.missing_evidence:
            raise ValueError("fork_letter D requires a non-empty missing_evidence list")
        # A high-confidence "cannot fork yet" is incoherent (spec §16).
        if self.fork_letter is ForkLetter.D and self.confidence is Confidence.HIGH:
            raise ValueError("fork_letter D with confidence High is incoherent")
        return self


# ─── DRAFTS.md ─────────────────────────────────────────────────────────────


class JiraDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    project: str = "REP"
    title: str = ""
    description: str = ""
    affected_component: str | None = None
    suspected_area: str | None = None
    repro_steps: list[str] = Field(default_factory=list)


class DraftsBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    customer_reply: str = ""
    internal_note: str = ""
    jira_draft: JiraDraft | None = None


# ─── The whole handoff ─────────────────────────────────────────────────────


class Handoff(BaseModel):
    """The structured payload the L3 agent emits and render.py consumes.

    `extra="ignore"` everywhere lets the agent include scratch fields without
    breaking validation; the invariants live on ForkPacket.
    """

    model_config = ConfigDict(extra="ignore")
    intake: IntakeBlock
    evidence_preflight: PreflightBlock
    fork_packet: ForkPacket
    drafts: DraftsBlock
    rubric_version: str = ""
