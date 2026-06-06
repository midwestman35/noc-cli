from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from noc_cli.models import (
    DraftsBlock,
    ForkLetter,
    ForkPacket,
    Handoff,
    IntakeBlock,
    PreflightBlock,
)
from noc_cli.runbooks import runbook_for_tag, runbook_slug_from_path
from noc_cli.scaffold import TicketFolder

if TYPE_CHECKING:
    from noc_cli.agent.runner import TranscriptEntry

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
        f"**Incident window:** {intake.incident_window}"
        if intake.incident_window
        else "",
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

    gv = fp.grounding_verified
    slug = fp.runbook_reference.slug or "rubric-core"
    if gv is True:
        grounding_line = f"- Grounding: ✓ grounded in `{slug}`"
    elif gv is False:
        grounding_line = f"- Grounding: ⚠ unverified ({fp.grounding_note})"
    else:
        grounding_line = "- Grounding: not checked"
    lines += ["", grounding_line]

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
        lines += [
            "",
            f"**Related Zendesk:** {', '.join(f'#{z}' for z in fp.related_zendesk)}",
        ]
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


def _yaml_q(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def _render_state(
    handoff: Handoff,
    owner: str,
    consulted_runbooks: list[str] | None = None,
    validator_warnings: list[str] | None = None,
) -> str:
    fp = handoff.fork_packet
    intake = handoff.intake
    pivoted = _pivoted(handoff, consulted_runbooks or [])
    lines = [
        "---",
        f"ticket_id: {intake.ticket_id}",
        f'fork: "{fp.fork_letter.value}"',
        f'symptom_tag: "{fp.symptom_tag}"',
        f'confidence: "{fp.confidence.value}"',
        f'rubric_version: "{handoff.rubric_version}"',
        f"quoted_rubric_row: {_yaml_q(fp.quoted_rubric_row)}",
        'status: "open"',
        f'owner: "{owner}"',
    ]
    if fp.cluster is not None:
        lines.append(f"cluster: {_yaml_q(fp.cluster)}")
    lines += [
        "related:",
        f"  zendesk: {json.dumps(fp.related_zendesk)}",
        f"  jira: {json.dumps(fp.related_jira)}",
    ]
    if fp.master_ticket is not None:
        lines.append(f"  master: {fp.master_ticket}")
    lines += [
        "---",
        "",
        f"# Ticket {intake.ticket_id} — {intake.one_line_fingerprint}",
        "",
        f"**Fork:** {fp.fork_letter.value} | **Tag:** {fp.symptom_tag} | "
        f"**Confidence:** {fp.confidence.value}",
        "",
        f"> {fp.quoted_rubric_row}" if fp.quoted_rubric_row else "",
    ]
    consulted_runbooks = consulted_runbooks or []
    validator_warnings = validator_warnings or []
    if consulted_runbooks:
        lines += ["", f"Runbooks consulted: {', '.join(consulted_runbooks)}"]
    lines += ["", f"Pivoted: {'yes' if pivoted else 'no'}"]
    if validator_warnings:
        lines += ["", "## Validator Warnings"]
        lines += [f"- {warning}" for warning in validator_warnings]
    return "\n".join(lines)


def render_handoff(
    handoff: Handoff,
    folder: TicketFolder,
    owner: str = "",
    *,
    consulted_runbooks: list[str] | None = None,
    validator_warnings: list[str] | None = None,
) -> None:
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
        "STATE.md": _render_state(
            handoff, owner, consulted_runbooks, validator_warnings
        ),
    }

    with tempfile.TemporaryDirectory(dir=dest, prefix=".render-tmp-") as td:
        tmp = Path(td)
        for name, text in content_map.items():
            (tmp / name).write_text(text, encoding="utf-8")
        for name in _CANONICAL_FILES:
            src_file = tmp / name
            dst_file = dest / name
            shutil.move(str(src_file), dst_file)


def consulted_runbook_slugs(transcript: list[TranscriptEntry]) -> list[str]:
    """Return ordered, de-duplicated runbook slugs the agent actually read."""
    slugs: list[str] = []
    for entry in transcript:
        if getattr(entry, "kind", "") != "tool":
            continue
        if getattr(entry, "tool_name", "") != "Read":
            continue
        slug = runbook_slug_from_path(getattr(entry, "tool_args", ""))
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _consulted_runbook_texts(folder: TicketFolder, consulted: list[str]) -> list[str]:
    texts: list[str] = []
    for slug in consulted:
        path = folder.runbooks / f"{slug}.md"
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8"))
    return texts


def _rubric_contains_row(quoted: str, extra_texts: list[str]) -> bool:
    from noc_cli.rubric import load_rubric

    rubric = load_rubric()
    return rubric.contains_row(quoted, extra_texts=extra_texts)


def _pivoted(handoff: Handoff, consulted: list[str]) -> bool:
    fp = handoff.fork_packet
    ref_slug = fp.runbook_reference.slug
    seed = runbook_for_tag(handoff.intake.initial_hypothesis)
    seed_slug = seed[0] if seed else ""
    if seed_slug and ref_slug and seed_slug != ref_slug:
        return True
    if consulted and ref_slug and consulted[-1] != ref_slug:
        return True
    text = " ".join(
        part
        for part in (
            handoff.intake.initial_hypothesis,
            fp.reasoning,
            fp.runbook_reference.section,
        )
        if part
    ).lower()
    pivot_terms = ("pivot", "re-steer", "reclassified", "ruled out", "leaving")
    return any(term in text for term in pivot_terms)


def validation_warnings(
    handoff: Handoff,
    consulted: list[str],
    *,
    folder: TicketFolder,
) -> list[str]:
    """Soft-warn on runbook-reference or quote drift; never reject a handoff."""
    warnings: list[str] = []
    ref_slug = handoff.fork_packet.runbook_reference.slug
    if ref_slug:
        if not consulted:
            warnings.append(
                f"runbook_reference.slug '{ref_slug}' was cited, but no runbooks were read"
            )
        elif ref_slug not in consulted:
            warnings.append(
                f"runbook_reference.slug '{ref_slug}' was not among the runbooks actually "
                f"read ({', '.join(consulted)})"
            )

    quoted = handoff.fork_packet.quoted_rubric_row
    extra_texts = _consulted_runbook_texts(folder, consulted)
    if quoted and not _rubric_contains_row(quoted, extra_texts):
        warnings.append(
            "quoted_rubric_row was not found in the rubric or consulted runbooks"
        )
    return warnings


def render_reasoning(
    transcript: list[TranscriptEntry],
    handoff: Handoff | None,
    folder: TicketFolder,
) -> None:
    """Write REASONING.md from the captured transcript and optional handoff."""
    consulted = consulted_runbook_slugs(transcript)
    if handoff is not None:
        fp = handoff.fork_packet
        ticket = handoff.intake.ticket_id
        hypothesis = handoff.intake.initial_hypothesis or "(none)"
        final = (
            f"Fork {fp.fork_letter.value} - {fp.symptom_tag} - {fp.confidence.value}"
        )
        pivoted = _pivoted(handoff, consulted)
    else:
        ticket = folder.root.name
        hypothesis = "(unknown - handoff failed to parse)"
        final = "(no handoff - agent output was unparseable)"
        pivoted = False

    turns = sum(
        1 for entry in transcript if getattr(entry, "kind", "") in {"reasoning", "tool"}
    )
    lines = [
        f"# REASONING - Ticket #{ticket}",
        "",
        f"**Hypothesis:** {hypothesis} -> **Final:** {final}",
        f"**Runbooks consulted:** {', '.join(consulted) if consulted else '(none)'}",
        f"Pivoted: {'yes' if pivoted else 'no'}",
        f"**Turns:** {turns}",
        "",
        "## Transcript",
        "",
    ]

    step = 0
    for entry in transcript:
        kind = getattr(entry, "kind", "")
        if kind == "reasoning":
            step += 1
            lines += [f"### {step}. Reasoning", "", getattr(entry, "text", ""), ""]
        elif kind == "tool":
            tool = getattr(entry, "tool_name", "")
            args = getattr(entry, "tool_args", "")
            lines += [f"> {tool} {args}".rstrip(), ""]
        elif kind == "tool_result":
            lines += ["> result", "", getattr(entry, "text", ""), ""]

    if handoff is not None:
        fp = handoff.fork_packet
        decisive = "; ".join(handoff.evidence_preflight.decisive_evidence) or "(none)"
        lines += [
            "## Decision summary (from handoff)",
            "",
            f"- Decisive evidence: {decisive}",
            f"- Fork: {fp.fork_letter.value} - {fp.confidence.value}",
            f"- Reasoning: {fp.reasoning}",
        ]

    (folder.root / "REASONING.md").write_text("\n".join(lines), encoding="utf-8")
