from __future__ import annotations

import platform
import shutil
import subprocess
from abc import ABC, abstractmethod

from noc_cli.watch.diff import ChangeEvent, ChangeKind


class Notifier(ABC):
    """Abstract desktop notification interface.

    Implement one subclass per OS. The macOS implementation is provided;
    Linux (``notify-send``) and Windows impls are a later seam.
    """

    @abstractmethod
    def notify(self, event: ChangeEvent) -> None:
        """Fire a desktop notification for *event*."""


class NoOpNotifier(Notifier):
    """Silent notifier used in tests and CI environments."""

    def notify(self, event: ChangeEvent) -> None:
        pass


def _escape_applescript(text: str) -> str:
    """Escape backslash and double-quote for an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class MacOSNotifier(Notifier):
    """macOS desktop notifier.

    Uses ``terminal-notifier`` when present (richer: sound, group de-dupe,
    click-to-focus), falling back to ``osascript`` (zero-install).
    Detected once at construction time via ``shutil.which``.
    """

    def __init__(self) -> None:
        self._terminal_notifier: str | None = shutil.which("terminal-notifier")

    def _title(self, event: ChangeEvent) -> str:
        if event.customer_replied and event.old_status == "pending" and event.new_status == "open":
            return "noc-cli · Customer replied"
        if event.kind == ChangeKind.STATUS_CHANGED:
            return f"noc-cli · Status changed ({event.old_status} → {event.new_status})"
        return "noc-cli · New requester comment"

    def _message(self, event: ChangeEvent) -> str:
        return f"Ticket #{event.ticket_id}: {event.ticket_subject}"

    def notify(self, event: ChangeEvent) -> None:
        title = self._title(event)
        message = self._message(event)
        if self._terminal_notifier:
            subprocess.run(
                [
                    self._terminal_notifier,
                    "-title", title,
                    "-message", message,
                    "-group", f"noc-cli-{event.ticket_id}",
                    "-sound", "default",
                ],
                check=False,
            )
        else:
            # osascript AppleScript — always available on macOS. Escape the
            # interpolated strings so a subject containing a quote can't break
            # or inject the AppleScript.
            safe_title = _escape_applescript(title)
            safe_message = _escape_applescript(message)
            script = (
                f'display notification "{safe_message}" '
                f'with title "{safe_title}" '
                f'sound name "default"'
            )
            subprocess.run(["osascript", "-e", script], check=False)


def build_notifier(notify_cfg: str) -> Notifier:
    """Factory: parse the ``NOC_NOTIFY`` config string and return the right notifier.

    Config format: comma-separated tokens, e.g. ``"banner,ping"``.
    ``"ping"`` activates the OS desktop notifier; ``"banner"`` is handled by the TUI.
    When ``"ping"`` is absent (or the platform is not macOS), returns ``NoOpNotifier``.
    """
    tokens = {t.strip().lower() for t in notify_cfg.split(",")}
    if "ping" not in tokens:
        return NoOpNotifier()
    if platform.system() == "Darwin":
        return MacOSNotifier()
    # Linux/Windows: not yet implemented; fall back gracefully.
    return NoOpNotifier()
