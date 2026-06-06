from __future__ import annotations

from collections.abc import Callable

from noc_cli.runbooks import DOMAIN_MAP

_NOT_SURE_LABEL = "Not sure - let the agent decide"


def slug_to_tag(slug: str) -> str | None:
    for symptom in DOMAIN_MAP:
        if symptom.slug == slug:
            return symptom.tag
    return None


def menu_lines() -> list[str]:
    """Numbered selection menu grouped by domain, plus a final no-seed option."""
    lines: list[str] = []
    last_domain: str | None = None
    number = 0
    for symptom in DOMAIN_MAP:
        if symptom.domain != last_domain:
            lines.append(f"  {symptom.domain}")
            last_domain = symptom.domain
        number += 1
        lines.append(f"    {number}) {symptom.label}")
    lines.append(f"    {number + 1}) {_NOT_SURE_LABEL}")
    return lines


def _choice_to_tag(choice: str) -> str:
    try:
        index = int((choice or "").strip())
    except ValueError:
        return ""
    if 1 <= index <= len(DOMAIN_MAP):
        return DOMAIN_MAP[index - 1].tag
    return ""


def resolve_seed(
    suspect: str | None,
    *,
    interactive: bool,
    prompt_fn: Callable[[str], str] | None = None,
    echo_fn: Callable[[str], None] | None = None,
) -> str:
    """Resolve an analyst seed to an approved symptom tag, or empty for no seed."""
    if suspect:
        normalized = suspect.strip()
        tag = slug_to_tag(normalized)
        if tag is None:
            valid = ", ".join(symptom.slug for symptom in DOMAIN_MAP)
            raise ValueError(f"unknown --suspect {suspect!r}; valid slugs: {valid}")
        return tag

    if not interactive or prompt_fn is None:
        return ""

    if echo_fn is not None:
        echo_fn(
            "What do you suspect this ticket is? You can change course mid-investigation."
        )
        for line in menu_lines():
            echo_fn(line)
    return _choice_to_tag(prompt_fn("Selection"))
