from __future__ import annotations

import platform
import shutil
import subprocess
import threading
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


class OpenPetsNotifier(Notifier):
    """Announce ticket changes on the OpenPets desktop pet via local IPC.

    Best-effort by design: if the desktop app is closed or otherwise
    unreachable, the notification is silently dropped — a watcher must never
    crash (or freeze) because the pet is down. The IPC send runs on a
    short-lived daemon thread so the TUI thread never blocks on it.

    Messages are kept deliberately minimal (ticket number + what changed) to
    respect the OpenPets usage rules: brief, user-facing, no subjects/PII.
    """

    def _message(self, event: ChangeEvent) -> str:
        if event.customer_replied and event.old_status == "pending":
            return f"ZD-{event.ticket_id} · customer replied"
        if event.kind == ChangeKind.STATUS_CHANGED:
            return f"ZD-{event.ticket_id} · {event.old_status} → {event.new_status}"
        return f"ZD-{event.ticket_id} · new comment"

    def _reaction(self, event: ChangeEvent) -> str:
        # All values are from OpenPets' allowed reaction set.
        if event.customer_replied:
            return "waving"
        if event.kind == ChangeKind.STATUS_CHANGED:
            return "thinking"
        return "waiting"

    def notify(self, event: ChangeEvent) -> None:
        from noc_cli.watch import openpets

        message = self._message(event)
        reaction = self._reaction(event)

        def _emit() -> None:
            try:
                openpets.say(message, reaction=reaction)
            except Exception:
                # Pet closed / unreachable / any IPC hiccup — drop silently.
                pass

        threading.Thread(target=_emit, daemon=True).start()


class CompositeNotifier(Notifier):
    """Fan a single event out to several notifiers (e.g. OS ping + OpenPets)."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = notifiers

    def notify(self, event: ChangeEvent) -> None:
        for notifier in self._notifiers:
            try:
                notifier.notify(event)
            except Exception:
                pass


def build_notifier(notify_cfg: str) -> Notifier:
    """Factory: parse the ``NOC_NOTIFY`` config string and return a notifier.

    Config format: comma-separated tokens, e.g. ``"banner,ping,openpets"``.
    - ``"banner"`` is handled by the TUI (no notifier needed).
    - ``"ping"`` activates the OS desktop notifier (macOS only for now).
    - ``"openpets"`` announces changes on the OpenPets desktop pet.

    Returns ``NoOpNotifier`` when no active token applies, a single notifier
    when exactly one does, or a ``CompositeNotifier`` to fire several.
    """
    tokens = {t.strip().lower() for t in notify_cfg.split(",")}
    notifiers: list[Notifier] = []
    if "ping" in tokens and platform.system() == "Darwin":
        notifiers.append(MacOSNotifier())
    if "openpets" in tokens:
        notifiers.append(OpenPetsNotifier())

    if not notifiers:
        return NoOpNotifier()
    if len(notifiers) == 1:
        return notifiers[0]
    return CompositeNotifier(notifiers)
