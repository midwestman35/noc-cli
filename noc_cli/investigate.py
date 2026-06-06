"""In-process investigate pipeline, callable from the CLI and the TUI worker."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from noc_cli.config import Config, db_path

if TYPE_CHECKING:
    from noc_cli.evidence import PasteInput


class InvestigationError(RuntimeError):
    """Raised when the pipeline cannot complete (soft-lock, agent failure)."""


def _noop(_line: str) -> None:
    pass


async def run_investigation(
    *,
    ticket_id: int,
    config: Config,
    tickets_root: Path,
    owner: str,
    initial_hypothesis: str = "",
    extra_files: list[Path] | None = None,
    pastes: list[str | PasteInput] | None = None,
    force: bool = False,
    fixture: Path | None = None,
    no_agent: bool = False,
    verbose: bool = False,
    on_line: Callable[[str], None] | None = None,
) -> Path:
    """Run the L3 investigate pipeline. Emits human-readable progress strings via
    on_line (matching watch.inbox.detect_phase). Returns the ticket folder root.
    Raises InvestigationError on soft-lock conflict or agent failure."""
    emit = on_line or _noop
    extra_files = extra_files or []
    pastes = pastes or []

    from noc_cli.evidence import (
        PasteInput,
        _looks_like_text,
        gather_evidence,
        write_ticket_source,
    )
    from noc_cli.memory import InvestigationRecord, MemoryStore, append_investigation
    from noc_cli.scaffold import SoftLockConflict, preflight_soft_lock, scaffold_ticket

    folder = scaffold_ticket(tickets_root, ticket_id)
    try:
        preflight_soft_lock(folder, owner=owner, force=force)
    except SoftLockConflict as exc:
        raise InvestigationError(f"soft-lock conflict: {exc}") from exc
    emit(f"Scaffold ready: {folder.root}")

    attachments: list[dict] = []
    zd_client = None
    if fixture is None and not no_agent:
        try:
            from noc_cli.zendesk import ZendeskClient

            zd_client = ZendeskClient(config)
            ticket = zd_client.get_ticket(ticket_id)
            comments = zd_client.get_comments(ticket_id)
            write_ticket_source(folder, ticket, comments)
            for comment in comments:
                for att in getattr(comment, "attachments", []) or []:
                    attachments.append(att.model_dump())
            emit(f"Ticket #{ticket_id} fetched")
        except Exception as exc:
            emit(f"Zendesk fetch failed: {exc}")

    paste_inputs: list = []
    for p in pastes:
        if isinstance(p, PasteInput):
            paste_inputs.append(p)
        elif "=" in p:
            label, _, text = p.partition("=")
            paste_inputs.append(PasteInput(label=label.strip(), text=text))
        else:
            paste_inputs.append(PasteInput(label="paste", text=p))
    gather_evidence(
        folder=folder,
        zendesk_attachments=attachments,
        extra_files=extra_files,
        pastes=paste_inputs,
        zendesk_client=zd_client,
    )
    emit("Evidence gathered")

    from noc_cli.redact import redact, residual_pii_warning

    for log_file in folder.logs.iterdir():
        if log_file.is_file() and _looks_like_text(log_file.name):
            try:
                text = log_file.read_text(encoding="utf-8", errors="replace")
                redacted, counts = redact(text)
                log_file.write_text(redacted, encoding="utf-8")
                warning = residual_pii_warning(redacted, counts)
                if warning and verbose:
                    emit(f"{log_file.name}: {warning}")
            except Exception:
                pass
    emit("PII redacted")

    if no_agent:
        emit(f"Ticket #{ticket_id} complete (no-agent) — {folder.root}")
        return folder.root

    mem_db = Path(os.environ.get("NOC_DB_PATH", str(db_path())))
    mem_md = tickets_root / "MEMORY.md"
    mem_store = MemoryStore(db_path=mem_db, memory_md_path=mem_md)
    mem_store.init()

    history_context = ""
    if fixture is None:
        from noc_cli.history import seed_history
        from noc_cli.zendesk import ZendeskClient

        zd_for_history = ZendeskClient(config)
        symptom_tag = initial_hypothesis or "[unclassified]"
        candidates = seed_history(
            symptom_tag, zendesk_client=zd_for_history, memory_store=mem_store
        )
        history_context = "\n".join(
            f"- Ticket #{c.ticket_id}: {c.subject} (source: {c.source})"
            for c in candidates[:10]
        )
        emit(f"History seeded: {len(candidates)} candidate(s)")
    else:
        emit("History seeded (fixture mode)")

    from noc_cli.models import Handoff

    handoff: Handoff | None = None
    transcript = []
    if fixture is not None:
        import json

        handoff_path = fixture / "handoff_good.json"
        if not handoff_path.exists():
            raise InvestigationError(f"fixture {handoff_path} not found")
        handoff = Handoff.model_validate(json.loads(handoff_path.read_text()))
    else:
        from noc_cli.agent.prompt import build_system_prompt
        from noc_cli.agent.runner import run_agent
        from noc_cli.rubric import load_rubric

        rubric = load_rubric()
        system_prompt = build_system_prompt(rubric.core)
        runner_result = await run_agent(
            ticket_id=ticket_id,
            folder=folder,
            system_prompt=system_prompt,
            history_context=history_context,
            initial_hypothesis=initial_hypothesis,
        )
        transcript = runner_result.transcript
        handoff = runner_result.handoff
        if handoff is None:
            try:
                from noc_cli.render import render_reasoning

                render_reasoning(transcript, None, folder)
            except Exception:
                pass
            raise InvestigationError(
                f"agent failed after 2 attempts; raw stashed to {runner_result.stash_path}"
            )
    emit("Agent completed")

    from noc_cli.render import (
        consulted_runbook_slugs,
        render_handoff,
        render_reasoning,
        validation_warnings,
    )

    if initial_hypothesis:
        handoff = handoff.model_copy(
            deep=True,
            update={
                "intake": handoff.intake.model_copy(
                    update={"initial_hypothesis": initial_hypothesis}
                )
            },
        )
    consulted = consulted_runbook_slugs(transcript)
    warnings = validation_warnings(handoff, consulted, folder=folder)
    render_handoff(
        handoff,
        folder,
        owner=owner,
        consulted_runbooks=consulted,
        validator_warnings=warnings,
    )
    try:
        render_reasoning(transcript, handoff, folder)
    except Exception:
        pass
    emit("Report rendered")

    append_investigation(
        mem_store,
        InvestigationRecord(
            ticket_id=str(ticket_id),
            symptom_tag=handoff.fork_packet.symptom_tag,
            fork_letter=handoff.fork_packet.fork_letter.value,
            confidence=handoff.fork_packet.confidence.value,
            one_line_fingerprint=handoff.intake.one_line_fingerprint,
            summary=handoff.fork_packet.reasoning[:300],
            related_zendesk=handoff.fork_packet.related_zendesk,
            rubric_version=handoff.rubric_version,
        ),
    )
    emit(f"Ticket #{ticket_id} complete — {folder.root}")
    return folder.root
