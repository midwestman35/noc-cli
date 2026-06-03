import zipfile
from pathlib import Path

from noc_cli.evidence import PasteInput, gather_evidence
from noc_cli.scaffold import scaffold_ticket


def make_zip(dest: Path, files: dict[str, bytes]) -> Path:
    """Create a zip at `dest` with the given {name: content} mapping."""
    with zipfile.ZipFile(dest, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return dest


def test_paste_input_written_to_logs(tmp_path):
    folder = scaffold_ticket(tmp_path, 1)
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[],
        pastes=[PasteInput(label="notes", text="call failed at 06:32 UTC")],
    )
    assert bundle.paste_count == 1
    written = folder.logs / "paste-notes.txt"
    assert written.exists()
    assert "06:32 UTC" in written.read_text()


def test_extra_file_copied_to_logs(tmp_path):
    folder = scaffold_ticket(tmp_path, 2)
    src = tmp_path / "station.log"
    src.write_text("log content here")
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[src],
        pastes=[],
    )
    assert bundle.file_count == 1
    assert (folder.logs / "station.log").exists()


def test_zip_extracted_text_files(tmp_path):
    folder = scaffold_ticket(tmp_path, 3)
    z = make_zip(
        tmp_path / "logs.zip",
        {
            "kamailio.log": b"SIP log line 1\nSIP log line 2",
            "notes.txt": b"analyst notes",
        },
    )
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[z],
        pastes=[],
    )
    assert (folder.logs / "kamailio.log").exists()
    assert (folder.logs / "notes.txt").exists()
    assert bundle.zip_extracted == 1


def test_zip_skips_binary_entries(tmp_path):
    folder = scaffold_ticket(tmp_path, 4)
    z = make_zip(
        tmp_path / "mixed.zip",
        {
            "log.txt": b"plain text",
            "capture.pcap": b"\xd4\xc3\xb2\xa1binary",
            "image.png": b"\x89PNG\r\n\x1a\n",
        },
    )
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[z],
        pastes=[],
    )
    assert (folder.logs / "log.txt").exists()
    assert not (folder.logs / "capture.pcap").exists()
    assert not (folder.logs / "image.png").exists()


def test_pcap_file_flagged_not_parsed(tmp_path):
    folder = scaffold_ticket(tmp_path, 5)
    pcap = tmp_path / "capture.pcap"
    pcap.write_bytes(b"\xd4\xc3\xb2\xa1\x00")
    bundle = gather_evidence(
        folder=folder,
        zendesk_attachments=[],
        extra_files=[pcap],
        pastes=[],
    )
    assert bundle.pcap_flagged == 1
    # The .pcap must NOT be parsed/opened — it is moved to pcaps/ as-is
    assert (folder.pcaps / "capture.pcap").exists()
    # logs/ must NOT contain the pcap
    assert not (folder.logs / "capture.pcap").exists()


def test_zendesk_attachment_downloaded(tmp_path, httpx_mock):
    """gather_evidence downloads a Zendesk attachment via the client; mock HTTP."""
    import httpx

    from noc_cli.config import Config
    from noc_cli.zendesk import ZendeskClient

    folder = scaffold_ticket(tmp_path, 6)
    httpx_mock.add_response(
        url="https://cdn.zendesk.example/attachments/kamailio.log",
        content=b"SIP line from Zendesk",
    )
    cfg = Config(
        zendesk_subdomain="example",
        zendesk_email="a@b.com",
        zendesk_api_token="tok",
    )
    with httpx.Client() as c:
        client = ZendeskClient(cfg, client=c)
        bundle = gather_evidence(
            folder=folder,
            zendesk_attachments=[
                {
                    "file_name": "kamailio.log",
                    "content_url": "https://cdn.zendesk.example/attachments/kamailio.log",
                    "content_type": "text/plain",
                }
            ],
            extra_files=[],
            pastes=[],
            zendesk_client=client,
        )
    assert bundle.attachment_count == 1
    assert (folder.logs / "kamailio.log").exists()
