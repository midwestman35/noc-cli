"""Minimal, dependency-free client for the OpenPets desktop companion.

OpenPets runs a local IPC server and publishes its address + an auth token in a
discovery file (``%APPDATA%/OpenPets/runtime/ipc.json`` on Windows, the
platform equivalents elsewhere). The wire protocol is one line of JSON in, one
line of JSON out:

    -> {"id": "<uuid>", "version": 1, "token": "<token>", "method": "pet.say",
        "params": {"message": "..."}}\\n
    <- {"id": "<uuid>", "ok": true, "result": {...}}\\n

We speak it directly (Windows named pipe / Unix socket / loopback TCP) rather
than shelling out to the official Node client, so this stays a pure-Python,
zero-install integration. Mirrors @open-pets/client v1.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

_PROTOCOL = "openpets-ipc"
_VERSION = 1
_MAX_BYTES = 16 * 1024
_TIMEOUT_S = 3.0


class OpenPetsUnavailable(Exception):
    """The OpenPets desktop app is not reachable (closed, no discovery file…).

    Callers treat this as "drop the notification" — it is never fatal.
    """


def discovery_path() -> Path:
    """Locate the discovery file the desktop app publishes for the platform."""
    override = os.environ.get("OPENPETS_DISCOVERY_FILE")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "OpenPets"
            / "runtime"
            / "ipc.json"
        )
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OpenPets" / "runtime" / "ipc.json"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "openpets" / "ipc.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "OpenPets" / "runtime" / "ipc.json"


def _read_discovery() -> dict:
    path = discovery_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpenPetsUnavailable(f"discovery file unavailable: {exc}") from exc
    try:
        disc = json.loads(raw)
    except ValueError as exc:
        raise OpenPetsUnavailable("discovery file is malformed JSON") from exc
    if disc.get("protocol") != _PROTOCOL or disc.get("protocolVersion") != _VERSION:
        raise OpenPetsUnavailable("discovery protocol/version mismatch")
    endpoint, token = disc.get("endpoint"), disc.get("token")
    if not isinstance(endpoint, str) or not isinstance(token, str):
        raise OpenPetsUnavailable("discovery missing endpoint/token")
    return disc


def _recv_line(read_chunk) -> bytes:
    """Read until a newline (the protocol delimits one response per line)."""
    buf = b""
    while b"\n" not in buf:
        chunk = read_chunk()
        if not chunk:
            break
        buf += chunk
        if len(buf) > _MAX_BYTES:
            raise OpenPetsUnavailable("response too large")
    return buf


def _send_pipe(endpoint: str, line: bytes) -> bytes:
    # Windows named pipe: open the \\.\pipe\... path as a binary duplex file.
    try:
        with open(endpoint, "r+b", buffering=0) as pipe:
            pipe.write(line)
            return _recv_line(lambda: pipe.read(1))
    except OSError as exc:
        raise OpenPetsUnavailable(f"pipe error: {exc}") from exc


def _send_socket(family: int, address, line: bytes) -> bytes:
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(_TIMEOUT_S)
    try:
        sock.connect(address)
        sock.sendall(line)
        return _recv_line(lambda: sock.recv(4096))
    except OSError as exc:
        raise OpenPetsUnavailable(f"socket error: {exc}") from exc
    finally:
        sock.close()


def _send(endpoint: str, line: bytes) -> bytes:
    if endpoint.startswith("\\\\.\\pipe\\") or sys.platform == "win32":
        return _send_pipe(endpoint, line)
    if endpoint.startswith("tcp://"):
        url = urlparse(endpoint)
        return _send_socket(socket.AF_INET, (url.hostname, url.port), line)
    if not hasattr(socket, "AF_UNIX"):
        raise OpenPetsUnavailable("unix sockets unsupported on this platform")
    return _send_socket(socket.AF_UNIX, endpoint, line)


def request(method: str, params: dict) -> dict:
    """Send one IPC request; return its ``result`` or raise OpenPetsUnavailable."""
    disc = _read_discovery()
    payload = {
        "id": str(uuid.uuid4()),
        "version": _VERSION,
        "token": disc["token"],
        "method": method,
        "params": params,
    }
    line = (json.dumps(payload) + "\n").encode("utf-8")
    if len(line) > _MAX_BYTES:
        raise OpenPetsUnavailable("request too large")

    raw = _send(disc["endpoint"], line)
    try:
        parsed = json.loads(raw.decode("utf-8").strip())
    except ValueError as exc:
        raise OpenPetsUnavailable("invalid response") from exc
    if parsed.get("ok") is True:
        return parsed.get("result") or {}
    err = parsed.get("error") or {}
    raise OpenPetsUnavailable(f"{err.get('code', 'error')}: {err.get('message', '')}")


def say(message: str, *, reaction: str | None = None) -> dict:
    """Make the default pet speak *message* (optionally with a reaction).

    Optional fields are *omitted* (not sent as null) when absent — the server
    rejects an explicit null leaseId, matching the official client which drops
    undefined keys.
    """
    params: dict = {"message": message}
    if reaction is not None:
        params["reaction"] = reaction
    return request("pet.say", params)
