from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from noc_cli.watch.inbox import InboxSummary


_SCALAR_KEYS = {
    "ticket_id",
    "fork",
    "confidence",
    "status",
    "owner",
    "symptom_tag",
    "rubric_version",
    "quoted_rubric_row",
    "cluster",
}


def parse_state_md(path: Path) -> InboxSummary | None:
    """Parse a ticket ``STATE.md`` into an inbox summary.

    This intentionally handles only the small frontmatter subset produced by
    noc-cli. Bad or unsupported scalar values return None instead of raising so
    a single corrupt ticket folder cannot break the watch inbox.
    """
    try:
        if not path.exists() or not path.is_file():
            return None

        lines = path.read_text().splitlines()
        if not lines or lines[0].strip() != "---":
            return None

        end = _frontmatter_end(lines)
        if end is None:
            return None

        scalars, related = _parse_frontmatter(lines[1:end])
        ticket_id = _parse_required_int(scalars.get("ticket_id"))
        related_zendesk = _parse_int_list(related.get("zendesk"))
        related_jira = _parse_str_list(related.get("jira"))
        master = _parse_optional_int(related.get("master"))

        quoted_rubric_row = scalars.get("quoted_rubric_row")
        if not quoted_rubric_row:
            quoted_rubric_row = _body_quote(lines[end + 1 :])

        investigated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        return InboxSummary(
            ticket_id=ticket_id,
            fork=scalars.get("fork"),
            confidence=scalars.get("confidence"),
            status=scalars.get("status"),
            owner=scalars.get("owner"),
            symptom_tag=scalars.get("symptom_tag"),
            rubric_version=scalars.get("rubric_version"),
            quoted_rubric_row=quoted_rubric_row,
            related_zendesk=related_zendesk,
            related_jira=related_jira,
            master=master,
            cluster=scalars.get("cluster"),
            investigated_at=investigated_at,
            folder=path.parent,
        )
    except (OSError, UnicodeDecodeError, TypeError, ValueError, SyntaxError):
        return None


def scan_investigations(tickets_root: Path) -> list[InboxSummary]:
    """Return parsed summaries for numeric ``Tickets/<id>/STATE.md`` folders."""
    if not tickets_root.exists() or not tickets_root.is_dir():
        return []

    summaries: list[InboxSummary] = []
    for child in sorted(tickets_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or not child.name.isdigit():
            continue

        state_path = child / "STATE.md"
        if not state_path.exists():
            continue

        try:
            summary = parse_state_md(state_path)
        except Exception:
            summary = None
        if summary is not None:
            summaries.append(summary)

    return summaries


def _frontmatter_end(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index
    return None


def _parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    scalars: dict[str, str] = {}
    related: dict[str, str] = {}
    in_related = False

    for raw_line in lines:
        if not raw_line.strip():
            continue

        if raw_line.startswith((" ", "\t")):
            if not in_related:
                continue
            key, value = _split_key_value(raw_line.strip())
            if key in {"zendesk", "jira", "master"}:
                related[key] = value.strip()
            continue

        key, value = _split_key_value(raw_line)
        in_related = key == "related"
        if in_related:
            continue
        if key in _SCALAR_KEYS:
            scalars[key] = _strip_scalar(value)

    return scalars, related


def _split_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise ValueError("frontmatter line missing colon")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _strip_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError("quoted scalar did not parse to string")
        return parsed
    return value


def _parse_required_int(value: str | None) -> int:
    if value is None:
        raise ValueError("missing required integer")
    return int(_strip_scalar(value))


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(_strip_scalar(value))


def _parse_int_list(value: str | None) -> list[int]:
    return [int(item) for item in _parse_list(value)]


def _parse_str_list(value: str | None) -> list[str]:
    return [str(item) for item in _parse_list(value)]


def _parse_list(value: str | None) -> list[object]:
    if value is None or value.strip() == "":
        return []

    raw = value.strip()
    if not raw.startswith("[") or not raw.endswith("]"):
        raise ValueError("expected inline list")

    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        parsed = None

    if isinstance(parsed, list):
        return parsed

    inner = raw[1:-1].strip()
    if not inner:
        return []
    return [_strip_scalar(item.strip()) for item in inner.split(",")]


def _body_quote(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith(">"):
            return line[1:].strip() or None
    return None
