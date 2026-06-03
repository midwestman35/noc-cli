from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

_VERSION_RE = re.compile(r'(?m)^rubric_version:\s*"?([^"\n]+?)"?\s*$')


@dataclass(frozen=True)
class Rubric:
    """The embedded fork rubric: full text + parsed version.

    `contains_row` is the soft-warn validator: it returns True when `quoted`
    is a verbatim substring of the rubric. Strictness is deliberately weak —
    the caller logs a warning on a miss (into STATE.md validator_warnings)
    rather than rejecting the agent's handoff (spec §16).
    """

    text: str
    version: str

    def contains_row(self, quoted: str) -> bool:
        if not quoted or not quoted.strip():
            return False
        return quoted in self.text


def _read_rubric_text() -> str:
    return resources.files("noc_cli.data").joinpath("fork-rubric.md").read_text(
        encoding="utf-8"
    )


def load_rubric() -> Rubric:
    """Load the embedded fork rubric. Raises ValueError if the version
    frontmatter is missing (a packaging error, caught in tests)."""
    text = _read_rubric_text()
    match = _VERSION_RE.search(text)
    if not match:
        raise ValueError("fork-rubric.md is missing the rubric_version frontmatter")
    return Rubric(text=text, version=match.group(1).strip())
