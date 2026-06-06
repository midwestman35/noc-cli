from __future__ import annotations

import json
import re
from dataclasses import dataclass

from noc_cli.model_profiles import profile_for
from noc_cli.runbooks import DOMAIN_MAP, RUNBOOK_SLUGS, _load_runbook
from noc_cli.scout.llm_io import extract_json, final_result

GROUNDING_SYSTEM_PROMPT = (
    "You classify a single 911/NG911 support ticket into exactly one runbook "
    "symptom class, or none if nothing fits. You do not investigate or fetch "
    "anything; you read the provided ticket text and pick the best-matching "
    "runbook from the catalog. The operator hypothesis is one signal, not a "
    "verdict — let the ticket evidence decide."
)

_CONFIDENCE = {"high", "medium", "low"}


@dataclass(frozen=True)
class RunbookSelection:
    slug: str | None
    confidence: str
    rationale: str


def _catalog() -> str:
    return "\n".join(
        f"  - {s.slug}: {s.label} ({s.domain}) [tag {s.tag}]" for s in DOMAIN_MAP
    )


def _parse_selection(raw: str) -> RunbookSelection:
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, ValueError, TypeError):
        return RunbookSelection(None, "low", "unparseable classifier output")
    if not isinstance(data, dict):
        return RunbookSelection(None, "low", "classifier output not an object")
    slug = (data.get("slug") or "").strip() or None
    conf = str(data.get("confidence", "low")).strip().lower()
    rationale = str(data.get("rationale", ""))[:300]
    if conf not in _CONFIDENCE:
        conf = "low"
    if slug not in RUNBOOK_SLUGS or conf == "low":
        return RunbookSelection(None, conf, rationale)
    return RunbookSelection(slug, conf, rationale)


def _default_options_factory():
    from claude_agent_sdk import ClaudeAgentOptions  # noqa: PLC0415

    p = profile_for("grounding")
    return ClaudeAgentOptions(
        system_prompt=GROUNDING_SYSTEM_PROMPT,
        model=p.model,
        effort=p.effort,
        max_turns=1,
        allowed_tools=[],
        permission_mode="bypassPermissions",
    )


async def select_runbook(
    ticket_text: str,
    hypothesis: str,
    *,
    query_fn=None,
    options_factory=None,
) -> RunbookSelection:
    """Classify a ticket into one runbook (or none). Never raises."""
    if query_fn is None:
        from claude_agent_sdk import query as query_fn  # noqa: PLC0415
    if options_factory is None:
        options_factory = _default_options_factory

    prompt = (
        "Classify this ticket into exactly one runbook from the catalog, or "
        "leave slug empty if none fits.\n\n"
        f"Operator hypothesis (a signal, not a verdict): {hypothesis or '(none)'}\n\n"
        f"Runbook catalog:\n{_catalog()}\n\n"
        f"Ticket text:\n{(ticket_text or '')[:6000]}\n\n"
        'Emit ONLY this JSON: {"slug":"<runbook slug or empty>",'
        '"confidence":"high|medium|low","rationale":"<=200 chars"}'
    )
    try:
        raw = await final_result(query_fn(prompt=prompt, options=options_factory()))
    except Exception:  # noqa: BLE001 — classifier failure degrades to rubric-core
        return RunbookSelection(None, "low", "classifier call failed")
    return _parse_selection(raw)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def verify_grounding(handoff, *, selected_slug, runbook_text):
    """Soft check that the Handoff's quoted row traces to the cited runbook.

    Returns (verified, note): True/False on a real check, None when verification
    can't run. Never raises.
    """
    try:
        fp = handoff.fork_packet
        quote = _norm(fp.quoted_rubric_row)
        if not quote:
            return None, "no quoted_rubric_row to verify"
        cited_slug = (
            getattr(fp.runbook_reference, "slug", "") or selected_slug
        ) or None
        text = None
        if cited_slug and cited_slug in RUNBOOK_SLUGS:
            text = _load_runbook(cited_slug)
        if text is None:
            text = (
                runbook_text  # selected runbook text, or rubric core when slug is None
            )
        if text is None:
            return None, "no runbook text to verify against"
        label = cited_slug or "rubric-core"
        if quote in _norm(text):
            return True, f"quoted row found in {label}"
        return False, f"quoted row not found in {label}; possible drift"
    except Exception:  # noqa: BLE001 — verification is best-effort
        return None, "verification skipped"
