from __future__ import annotations

from noc_cli.scout.models import ScoutReport


def render_scout_report(report: ScoutReport) -> str:
    """Render ranked Scout candidates as plain terminal text."""
    if not report.ranked:
        return "No stale tickets needing review right now."

    lines = ["Backlog Scout - candidates needing review:", ""]
    for candidate in report.ranked:
        confidence = f"{candidate.runbook_match_confidence:.0%}"
        runbook = candidate.runbook_id or "(no runbook match)"
        lines.append(
            f"  {candidate.rank}. #{candidate.ticket_id}  "
            f"[{runbook} | {confidence}]  {candidate.rationale}"
        )
        if candidate.missing_evidence:
            lines.append(f"       missing: {', '.join(candidate.missing_evidence)}")
    lines.append("")
    lines.append("Run `noc scout --take <id>` to claim + investigate one candidate.")
    return "\n".join(lines)
