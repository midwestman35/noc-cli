from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class Symptom:
    """One operator-selectable symptom and its staged runbook."""

    tag: str
    slug: str
    domain: str
    label: str


# Ordered catalog: the shared source for the operator menu and prompt domain map.
DOMAIN_MAP: tuple[Symptom, ...] = (
    Symptom("[dropped calls]", "dropped-calls", "SIP / UC", "Dropped or failed calls"),
    Symptom("[No ANI]", "no-ani", "SIP / UC", "Caller number missing (No ANI)"),
    Symptom("[No ALI]", "no-ali", "SIP / UC", "Caller location missing (No ALI)"),
    Symptom("[low audio]", "low-audio", "Media", "Audio / media quality"),
    Symptom("[apex]", "apex", "Operator Client", "APEX / station-client behavior"),
    Symptom(
        "[event history]", "event-history", "Data", "Event history / analytics gap"
    ),
)

# Approved symptom tag (normalized, no brackets/case) -> runbook slug.
# [unclassified] and [vendor] intentionally have no playbook.
_TAG_TO_SLUG: dict[str, str] = {}

RUNBOOK_SLUGS: tuple[str, ...] = tuple(s.slug for s in DOMAIN_MAP)
_RUBRIC_PACKAGE = "noc_cli.data"
_RUBRIC_FILENAME = "fork-rubric.md"


def _normalize_tag(tag: str) -> str:
    """Lowercase, strip brackets, collapse separators to single spaces."""
    t = tag.strip().lower().strip("[]")
    t = re.sub(r"[_\-\s]+", " ", t).strip()
    return t


_TAG_TO_SLUG.update({_normalize_tag(s.tag): s.slug for s in DOMAIN_MAP})


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


def runbook_slug_from_path(path: str) -> str | None:
    """Return a staged runbook slug for .../runbooks/<slug>.md paths only."""
    if not path:
        return None
    normalized = path.replace("\\", "/").rstrip("/")
    parts = normalized.split("/")
    if len(parts) < 2 or parts[-2] != "runbooks":
        return None
    name = parts[-1]
    if not name.endswith(".md"):
        return None
    slug = name[:-3]
    return slug if slug in RUNBOOK_SLUGS else None


def stage_runbooks(dest: Path) -> list[str]:
    """Copy runbooks and the full fork rubric into an agent-readable sandbox."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for slug in RUNBOOK_SLUGS:
        text = _load_runbook(slug)
        if text is None:
            continue
        filename = f"{slug}.md"
        (dest / filename).write_text(text, encoding="utf-8")
        written.append(filename)

    rubric_res = resources.files(_RUBRIC_PACKAGE).joinpath(_RUBRIC_FILENAME)
    if rubric_res.is_file():
        (dest / _RUBRIC_FILENAME).write_text(
            rubric_res.read_text(encoding="utf-8"), encoding="utf-8"
        )
        written.append(_RUBRIC_FILENAME)
    return written
