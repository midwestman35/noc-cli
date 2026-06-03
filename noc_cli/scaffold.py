from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_TRACKED_STATE_KEYS = ("fork", "confidence", "status", "owner", "symptom_tag")


@dataclass(frozen=True)
class TicketFolder:
    """Paths for one ticket's working directory."""

    root: Path
    logs: Path
    pcaps: Path
    analysis: Path

    @property
    def state_path(self) -> Path:
        return self.root / "STATE.md"


class SoftLockConflict(RuntimeError):
    """Raised when an existing STATE.md claims a different owner and --force
    was not given. Carries a field diff for the CLI to render (exit 2)."""

    def __init__(
        self,
        existing_owner: str,
        current_owner: str,
        summary: list[tuple[str, str, str]],
        state_path: Path,
    ) -> None:
        self.existing_owner = existing_owner
        self.current_owner = current_owner
        self.summary = summary
        self.state_path = state_path
        super().__init__(
            f"STATE.md soft-lock conflict: owned by {existing_owner}, "
            f"current is {current_owner}"
        )


def scaffold_ticket(tickets_root: Path, ticket_id: int | str) -> TicketFolder:
    """Create Tickets/<id>/{logs,pcaps,analysis}/. Idempotent."""
    root = Path(tickets_root) / str(ticket_id)
    logs = root / "logs"
    pcaps = root / "pcaps"
    analysis = root / "analysis"
    for d in (logs, pcaps, analysis):
        d.mkdir(parents=True, exist_ok=True)
    return TicketFolder(root=root, logs=logs, pcaps=pcaps, analysis=analysis)


def _strip_yaml_scalar(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return v[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    return v


def read_existing_state(state_path: Path) -> dict[str, str]:
    """Parse the tracked top-level scalar keys from a STATE.md. Indented
    (nested) lines are ignored. Missing file -> empty dict."""
    try:
        text = state_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line[0] in (" ", "\t"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        if key not in _TRACKED_STATE_KEYS:
            continue
        value = _strip_yaml_scalar(raw)
        if value:
            out[key] = value
    return out


def preflight_soft_lock(folder: TicketFolder, owner: str, force: bool) -> None:
    """Raise SoftLockConflict if an existing STATE.md names a different owner
    and `force` is False. No-op when unclaimed, same owner, or forced."""
    if force:
        return
    existing = read_existing_state(folder.state_path)
    existing_owner = existing.get("owner", "")
    if not existing_owner or existing_owner == owner:
        return
    summary: list[tuple[str, str, str]] = [("owner", existing_owner, owner)]
    for key in ("fork", "confidence", "status", "symptom_tag"):
        old = existing.get(key, "")
        if old:
            summary.append((key, old, "(pending this run)"))
    raise SoftLockConflict(existing_owner, owner, summary, folder.state_path)
