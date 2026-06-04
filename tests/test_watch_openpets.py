from __future__ import annotations

import json

import pytest

from noc_cli.watch import openpets
from noc_cli.watch.openpets import OpenPetsUnavailable


def test_discovery_path_respects_env_override(monkeypatch, tmp_path):
    target = tmp_path / "ipc.json"
    monkeypatch.setenv("OPENPETS_DISCOVERY_FILE", str(target))
    assert openpets.discovery_path() == target


def test_read_discovery_unavailable_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENPETS_DISCOVERY_FILE", str(tmp_path / "nope.json"))
    with pytest.raises(OpenPetsUnavailable):
        openpets._read_discovery()


def test_read_discovery_rejects_protocol_version_mismatch(monkeypatch, tmp_path):
    path = tmp_path / "ipc.json"
    path.write_text(
        json.dumps(
            {"protocol": "openpets-ipc", "protocolVersion": 2, "endpoint": "x", "token": "y"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENPETS_DISCOVERY_FILE", str(path))
    with pytest.raises(OpenPetsUnavailable):
        openpets._read_discovery()


def test_request_builds_payload_and_returns_result(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        openpets,
        "_read_discovery",
        lambda: {"endpoint": "\\\\.\\pipe\\openpets-x", "token": "tok-abc"},
    )

    def fake_send(endpoint, line):
        captured["endpoint"] = endpoint
        captured["payload"] = json.loads(line.decode("utf-8").strip())
        return b'{"id":"1","ok":true,"result":{"spoke":true}}\n'

    monkeypatch.setattr(openpets, "_send", fake_send)

    result = openpets.request("pet.say", {"message": "hi"})

    assert result == {"spoke": True}
    assert captured["endpoint"] == "\\\\.\\pipe\\openpets-x"
    assert captured["payload"]["method"] == "pet.say"
    assert captured["payload"]["token"] == "tok-abc"
    assert captured["payload"]["version"] == 1
    assert captured["payload"]["params"] == {"message": "hi"}
    assert "id" in captured["payload"]


def test_request_raises_on_error_response(monkeypatch):
    monkeypatch.setattr(openpets, "_read_discovery", lambda: {"endpoint": "e", "token": "t"})
    monkeypatch.setattr(
        openpets,
        "_send",
        lambda endpoint, line: b'{"id":"1","ok":false,"error":{"code":"no_pet","message":"none"}}\n',
    )
    with pytest.raises(OpenPetsUnavailable):
        openpets.request("pet.say", {"message": "hi"})


def test_say_uses_pet_say_method_and_passes_reaction(monkeypatch):
    seen: dict = {}

    def fake_request(method, params):
        seen["method"] = method
        seen["params"] = params
        return {}

    monkeypatch.setattr(openpets, "request", fake_request)

    openpets.say("hello", reaction="waving")

    assert seen["method"] == "pet.say"
    assert seen["params"]["message"] == "hello"
    assert seen["params"]["reaction"] == "waving"


def test_say_omits_reaction_when_none(monkeypatch):
    # The server rejects an explicit null leaseId/reaction, so absent optional
    # fields must be omitted entirely, not sent as null.
    seen: dict = {}
    monkeypatch.setattr(
        openpets, "request", lambda method, params: seen.update(params=params) or {}
    )

    openpets.say("hello")

    assert seen["params"] == {"message": "hello"}
    assert "reaction" not in seen["params"]
    assert "leaseId" not in seen["params"]
