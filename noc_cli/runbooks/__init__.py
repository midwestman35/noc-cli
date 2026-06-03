from __future__ import annotations

import re
from importlib import resources

# Approved symptom tag (normalized, no brackets/case) -> runbook slug.
# [unclassified] and [vendor] intentionally have no playbook.
_TAG_TO_SLUG: dict[str, str] = {
    "no ani": "no-ani",
    "no ali": "no-ali",
    "low audio": "low-audio",
    "dropped calls": "dropped-calls",
    "event history": "event-history",
    "apex": "apex",
}

RUNBOOK_SLUGS: tuple[str, ...] = tuple(sorted(set(_TAG_TO_SLUG.values())))


def _normalize_tag(tag: str) -> str:
    """Lowercase, strip brackets, collapse separators to single spaces."""
    t = tag.strip().lower().strip("[]")
    t = re.sub(r"[_\-\s]+", " ", t).strip()
    return t


def _load_runbook(slug: str) -> str | None:
    """Read a packaged runbook by slug. Returns None if absent."""
    resource = resources.files("noc_cli.runbooks").joinpath(f"{slug}.md")
    if not resource.is_file():
        return None
    return resource.read_text(encoding="utf-8")


def runbook_for_tag(symptom_tag: str) -> tuple[str, str] | None:
    """Map a symptom tag to (slug, runbook_markdown).

    Bracket- and case-insensitive: `[No ANI]`, `no ani`, `No_ANI` all match.
    Returns None for [unclassified], [vendor], or any unknown tag.
    """
    slug = _TAG_TO_SLUG.get(_normalize_tag(symptom_tag))
    if slug is None:
        return None
    text = _load_runbook(slug)
    if text is None:
        return None
    return slug, text
