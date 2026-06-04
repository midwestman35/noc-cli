from unittest.mock import MagicMock, patch

import pytest

from noc_cli.watch.diff import ChangeEvent, ChangeKind
from noc_cli.watch.notify import (
    CompositeNotifier,
    MacOSNotifier,
    NoOpNotifier,
    OpenPetsNotifier,
    build_notifier,
)


def _evt(
    tid: int = 1,
    kind: ChangeKind = ChangeKind.STATUS_CHANGED,
    old_status: str = "pending",
    new_status: str = "open",
    customer_replied: bool = True,
    subject: str = "PSAP - No ANI",
) -> ChangeEvent:
    return ChangeEvent(
        ticket_id=tid,
        ticket_subject=subject,
        kind=kind,
        old_status=old_status,
        new_status=new_status,
        customer_replied=customer_replied,
    )


def test_noop_notifier_does_not_raise():
    n = NoOpNotifier()
    n.notify(_evt())  # must not raise


def test_macos_notifier_uses_osascript_when_terminal_notifier_absent():
    with patch("shutil.which", return_value=None) as mock_which, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        notifier.notify(_evt())

        mock_which.assert_called_with("terminal-notifier")
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "osascript"
        joined = " ".join(cmd)
        assert "Ticket #1" in joined or "display notification" in joined


def test_macos_notifier_customer_replied_title_contains_flag():
    with patch("shutil.which", return_value=None), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        evt = _evt(customer_replied=True, old_status="pending", new_status="open")
        notifier.notify(evt)
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "Customer replied" in cmd_str or "customer replied" in cmd_str.lower()


def test_macos_notifier_uses_terminal_notifier_when_present():
    tn_path = "/usr/local/bin/terminal-notifier"
    with patch("shutil.which", return_value=tn_path) as mock_which, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        notifier.notify(_evt())

        mock_which.assert_called_with("terminal-notifier")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == tn_path


def test_macos_notifier_terminal_notifier_passes_title_and_message():
    tn_path = "/usr/local/bin/terminal-notifier"
    with patch("shutil.which", return_value=tn_path), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        notifier = MacOSNotifier()
        notifier.notify(_evt(tid=42, subject="No ALI on site 7"))
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "-title" in cmd_str
        assert "-message" in cmd_str


def test_build_notifier_returns_noop_when_ping_not_in_notify_string():
    n = build_notifier(notify_cfg="banner")
    assert isinstance(n, NoOpNotifier)


def test_build_notifier_returns_macos_when_ping_in_notify_string():
    import platform

    if platform.system() != "Darwin":
        pytest.skip("MacOSNotifier only on macOS")
    with patch("shutil.which", return_value=None), \
         patch("subprocess.run", return_value=MagicMock(returncode=0)):
        n = build_notifier(notify_cfg="banner,ping")
        assert isinstance(n, MacOSNotifier)


class _SyncThread:
    """Run the target inline so the fire-and-forget IPC send is observable."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


def test_openpets_notifier_says_message_with_reaction():
    with patch("noc_cli.watch.notify.threading.Thread", _SyncThread), \
         patch("noc_cli.watch.openpets.say") as mock_say:
        OpenPetsNotifier().notify(
            _evt(tid=42, customer_replied=True, old_status="pending", new_status="open")
        )
        assert mock_say.call_count == 1
        msg = mock_say.call_args[0][0]
        assert "ZD-42" in msg and "customer replied" in msg
        assert mock_say.call_args[1]["reaction"] == "waving"


def test_openpets_notifier_status_change_message():
    with patch("noc_cli.watch.notify.threading.Thread", _SyncThread), \
         patch("noc_cli.watch.openpets.say") as mock_say:
        OpenPetsNotifier().notify(
            _evt(tid=7, kind=ChangeKind.STATUS_CHANGED, customer_replied=False,
                 old_status="open", new_status="solved")
        )
        msg = mock_say.call_args[0][0]
        assert "ZD-7" in msg and "open" in msg and "solved" in msg


def test_openpets_notifier_swallows_errors():
    with patch("noc_cli.watch.notify.threading.Thread", _SyncThread), \
         patch("noc_cli.watch.openpets.say", side_effect=Exception("pet down")):
        OpenPetsNotifier().notify(_evt())  # must not raise


def test_build_notifier_openpets_token_returns_openpets_notifier():
    assert isinstance(build_notifier("banner,openpets"), OpenPetsNotifier)


def test_build_notifier_composes_ping_and_openpets_on_macos():
    with patch("noc_cli.watch.notify.platform.system", return_value="Darwin"), \
         patch("shutil.which", return_value=None):
        assert isinstance(build_notifier("ping,openpets"), CompositeNotifier)


def test_build_notifier_openpets_only_off_macos():
    with patch("noc_cli.watch.notify.platform.system", return_value="Windows"):
        assert isinstance(build_notifier("ping,openpets"), OpenPetsNotifier)
