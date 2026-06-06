from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from typing import Iterable

_VERSION_RE = re.compile(r'(?m)^rubric_version:\s*"?([^"\n]+?)"?\s*$')
_CORE_BOUNDARY = "## Symptom Class"


@dataclass(frozen=True)
class Rubric:
    """The embedded fork rubric: full text, parsed version, and core.

    `core` is the domain-agnostic preamble before the per-symptom class tables.
    `contains_row` is the soft-warn validator for rubric and runbook quotes.
    """

    text: str
    version: str
    core: str = ""

    def contains_row(self, quoted: str, *, extra_texts: Iterable[str] = ()) -> bool:
        if not quoted or not quoted.strip():
            return False
        if quoted in self.text:
            return True
        return any(quoted in text for text in extra_texts)


def _extract_core(text: str) -> str:
    return text.split(_CORE_BOUNDARY, 1)[0].rstrip() + "\n"


def _read_rubric_text() -> str:
    return (
        resources.files("noc_cli.data")
        .joinpath("fork-rubric.md")
        .read_text(encoding="utf-8")
    )


def load_rubric() -> Rubric:
    """Load the embedded fork rubric. Raises ValueError if the version
    frontmatter is missing (a packaging error, caught in tests)."""
    text = _read_rubric_text()
    match = _VERSION_RE.search(text)
    if not match:
        raise ValueError("fork-rubric.md is missing the rubric_version frontmatter")
    return Rubric(text=text, version=match.group(1).strip(), core=_extract_core(text))
