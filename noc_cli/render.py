from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from noc_cli.models import (
    DraftsBlock,
    ForkLetter,
    ForkPacket,
    Handoff,
    IntakeBlock,
    PreflightBlock,
)
from noc_cli.scaffold import TicketFolder

_CANONICAL_FILES = (
    "INTAKE.md",
    "EVIDENCE_PREFLIGHT.md",
    "FORK_PACKET.md",
    "DRAFTS.md",
    "STATE.md",
)


def _render_intake(intake: IntakeBlock) -> str:
    lines = [
        "# INTAKE",
        "",
        f"**Ticket:** [{intake.ticket_id}]({intake.url})",
        f"**Status:** {intake.status}",
        f"**Requester:** {intake.requester}",
        f"**Organization:** {intake.organization}",
    ]
    if intake.site:
        lines.append(f"**Site:** {intake.site}")
    if intake.cnc:
        lines.append(f"**CNC:** {intake.cnc}")
    if intake.region:
        lines.append(f"**Region:** {intake.region}")
    lines += [
        f"**Incident window:** {intake.incident_window}" if intake.incident_window else "",
        "",
        f"**Fingerprint:** {intake.one_line_fingerprint}",
        "",
        "## Ticket Summary",
    ]
    for bullet in intake.ticket_summary:
        lines.append(f"- {bullet}")
    lines += ["", "## Context Pulls"]
    for cp in intake.context_pulls:
        lines.append(f"- **{cp.pull}:** {cp.result} *(source: {cp.source})*")
    lines += [
        "",
        f"**Initial hypothesis:** {intake.initial_hypothesis}",
        f"**Intake decision:** `{intake.intake_decision.value}`",
    ]
    return "\n".join(lines)


def _render_preflight(preflight: PreflightBlock) -> str:
    lines = ["# EVIDENCE PREFLIGHT", ""]
    if preflight.gathered:
        lines.append("## Gathered Evidence")
        for ev in preflight.gathered:
            lines += [
                f"### {ev.evidence_type or 'Evidence'}",
                f"- **Source:** {ev.source}",
                f"- **Window:** {ev.time_window}",
                f"- {ev.summary}",
                "",
            ]
    lines += ["## Decisive Evidence"]
    for d in preflight.decisive_evidence:
        lines.append(f"- {d}")
    lines += ["", "## Missing / Non-Decisive"]
    for m in preflight.missing_or_non_decisive:
        lines.append(f"- {m}")
    return "\n".join(lines)


def _render_fork_packet(fp: ForkPacket) -> str:
    fork_desc = {
        ForkLetter.A: "Engineering Jira",
        ForkLetter.B: "Vendor / Internal IT",
        ForkLetter.C: "NOC Self-Resolve",
        ForkLetter.D: "Cannot Fork Yet",
    }[fp.fork_letter]

    lines = [
        "# FORK PACKET",
        "",
        f"**Fork:** {fp.fork_letter.value} — {fork_desc}",
        f"**Confidence:** {fp.confidence.value}",
        f"**Symptom tag:** `{fp.symptom_tag}`",
        f"**Rubric class:** {fp.rubric_class}",
        "",
        "## Decision Signal",
        "",
        f"> {fp.quoted_rubric_row}",
        "",
        "## Reasoning",
        "",
        fp.reasoning,
        "",
        "## Evidence Summary",
    ]
    for ev in fp.evidence_summary:
        lines.append(f"- {ev}")

    if fp.missing_evidence:
        lines += ["", "## Missing Evidence"]
        for m in fp.missing_evidence:
            lines.append(f"- {m}")

    lines += ["", "## Runbook Reference", ""]
    if fp.runbook_reference.slug:
        lines += [
            f"**Slug:** `{fp.runbook_reference.slug}`",
            "",
            fp.runbook_reference.section,
        ]
    else:
        lines.append("*(no runbook matched — symptom_tag is [unclassified])*")

    if fp.historical_matches:
        lines += ["", "## Historical Matches"]
        for hm in fp.historical_matches:
            lines += [
                f"### Ticket #{hm.ticket_id} — {hm.subject}",
                f"- **Relevance:** {hm.relevance}",
                f"- **Resolution:** {hm.resolution}",
                "",
            ]

    if fp.related_zendesk:
        lines += ["", f"**Related Zendesk:** {', '.join(f'#{z}' for z in fp.related_zendesk)}"]
    if fp.related_jira:
        lines += [f"**Related Jira:** {', '.join(fp.related_jira)}"]

    return "\n".join(lines)


def _render_drafts(drafts: DraftsBlock) -> str:
    lines = [
        "# DRAFTS",
        "",
        "## Customer Reply",
        "",
        drafts.customer_reply,
        "",
        "## Internal Note",
        "",
        drafts.internal_note,
    ]
    if drafts.jira_draft:
        jd = drafts.jira_draft
        lines += [
            "",
            "## Jira Draft",
            "",
            f"**Project:** {jd.project}",
            f"**Title:** {jd.title}",
            "",
            jd.description,
        ]
        if jd.repro_steps:
            lines += ["", "### Repro Steps"]
            for step in jd.repro_steps:
                lines.append(f"1. {step}")
    return "\n".join(lines)


def _render_state(handoff: Handoff, owner: str) -> str:
    fp = handoff.fork_packet
    intake = handoff.intake
    lines = [
        "---",
        f"ticket_id: {intake.ticket_id}",
        f'fork: "{fp.fork_letter.value}"',
        f'symptom_tag: "{fp.symptom_tag}"',
        f'confidence: "{fp.confidence.value}"',
        f'rubric_version: "{handoff.rubric_version}"',
        'status: "open"',
        f'owner: "{owner}"',
        "related:",
        f"  zendesk: {json.dumps(fp.related_zendesk)}",
        f"  jira: {json.dumps(fp.related_jira)}",
        "---",
        "",
        f"# Ticket {intake.ticket_id} — {intake.one_line_fingerprint}",
        "",
        f"**Fork:** {fp.fork_letter.value} | **Tag:** {fp.symptom_tag} | "
        f"**Confidence:** {fp.confidence.value}",
        "",
        f"> {fp.quoted_rubric_row}" if fp.quoted_rubric_row else "",
    ]
    return "\n".join(lines)


def render_handoff(handoff: Handoff, folder: TicketFolder, owner: str = "") -> None:
    """Write the five canonical files into `folder.root`.

    Each file is fully written to a temp directory under `dest`, then moved
    into place with a same-filesystem rename (atomic per file). A write failure
    is fully contained in the temp dir (no file reaches `dest`); cleanup is
    handled by TemporaryDirectory. Note: the moves are per-file, not a single
    transaction — an error partway through the move loop could leave a prefix
    of the five files in place. In practice same-fs renames do not fail.
    `owner` is recorded in STATE.md frontmatter (drives the soft-lock on re-run).
    """
    dest = folder.root
    content_map = {
        "INTAKE.md": _render_intake(handoff.intake),
        "EVIDENCE_PREFLIGHT.md": _render_preflight(handoff.evidence_preflight),
        "FORK_PACKET.md": _render_fork_packet(handoff.fork_packet),
        "DRAFTS.md": _render_drafts(handoff.drafts),
        "STATE.md": _render_state(handoff, owner),
    }

    with tempfile.TemporaryDirectory(dir=dest, prefix=".render-tmp-") as td:
        tmp = Path(td)
        for name, text in content_map.items():
            (tmp / name).write_text(text, encoding="utf-8")
        for name in _CANONICAL_FILES:
            src_file = tmp / name
            dst_file = dest / name
            shutil.move(str(src_file), dst_file)
