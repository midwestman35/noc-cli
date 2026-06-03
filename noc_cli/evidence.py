from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noc_cli.zendesk import ZendeskClient

from noc_cli.scaffold import TicketFolder

_TEXT_EXTENSIONS = {
    ".txt", ".log", ".md", ".json", ".xml", ".csv", ".tsv",
    ".yaml", ".yml", ".conf", ".cfg", ".ini", ".sip", ".sdp",
}
_PCAP_EXTENSIONS = {".pcap", ".pcapng", ".cap"}


def _looks_like_text(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in _PCAP_EXTENSIONS:
        return False
    if suffix in _TEXT_EXTENSIONS:
        return True
    # Accept no-extension files (e.g. raw log dumps)
    return suffix == ""


def _is_pcap(path: Path) -> bool:
    return path.suffix.lower() in _PCAP_EXTENSIONS


@dataclass
class PasteInput:
    label: str
    text: str


@dataclass
class EvidenceBundle:
    attachment_count: int = 0
    file_count: int = 0
    paste_count: int = 0
    zip_extracted: int = 0
    pcap_flagged: int = 0
    warnings: list[str] = field(default_factory=list)


def gather_evidence(
    folder: TicketFolder,
    zendesk_attachments: list[dict],
    extra_files: list[Path],
    pastes: list[PasteInput],
    zendesk_client: "ZendeskClient | None" = None,
) -> EvidenceBundle:
    """Gather all evidence into the ticket sandbox.

    - Zendesk attachments: fetched via ZendeskClient (requires client).
    - extra_files: copied/moved from the local filesystem.
      * .zip → text members extracted to logs/; binary members skipped.
      * .pcap/.pcapng → moved to pcaps/ unflagged, never parsed.
      * everything else → copied to logs/.
    - pastes: written as paste-<label>.txt into logs/.
    """
    bundle = EvidenceBundle()

    # 1. Zendesk attachments
    if zendesk_attachments and zendesk_client is not None:
        for att in zendesk_attachments:
            name: str = att["file_name"]
            url: str = att["content_url"]
            dest = folder.logs / name
            data = zendesk_client.download_attachment(url)
            dest.write_bytes(data)
            bundle.attachment_count += 1

    # 2. Local files / zip archives
    for src in extra_files:
        src = Path(src)
        if src.suffix.lower() == ".zip":
            _extract_zip(src, folder.logs)
            bundle.zip_extracted += 1
        elif _is_pcap(src):
            shutil.copy2(src, folder.pcaps / src.name)
            bundle.pcap_flagged += 1
        else:
            shutil.copy2(src, folder.logs / src.name)
            bundle.file_count += 1

    # 3. Paste inputs
    for paste in pastes:
        dest = folder.logs / f"paste-{paste.label}.txt"
        dest.write_text(paste.text, encoding="utf-8")
        bundle.paste_count += 1

    return bundle


def _extract_zip(zip_path: Path, logs_dir: Path) -> None:
    """Extract text-safe members to logs_dir; silently skip binary/pcap entries."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if not name or info.is_dir():
                continue
            if not _looks_like_text(info.filename):
                continue
            dest = logs_dir / name
            dest.write_bytes(zf.read(info.filename))
