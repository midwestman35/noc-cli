import pytest

from noc_cli.scaffold import (
    SoftLockConflict,
    TicketFolder,
    preflight_soft_lock,
    read_existing_state,
    scaffold_ticket,
)


def test_scaffold_creates_subdirs(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    assert isinstance(folder, TicketFolder)
    assert folder.root == tmp_path / "18432"
    assert (folder.root / "logs").is_dir()
    assert (folder.root / "pcaps").is_dir()
    assert (folder.root / "analysis").is_dir()


def test_scaffold_is_idempotent(tmp_path):
    scaffold_ticket(tmp_path, 7)
    folder = scaffold_ticket(tmp_path, 7)  # second call must not error
    assert (folder.root / "logs").is_dir()


def test_read_existing_state_extracts_owner_and_fork(tmp_path):
    root = tmp_path / "44671"
    root.mkdir()
    (root / "STATE.md").write_text(
        '---\nticket_id: 44671\nfork: "B"\nowner: "alice@axon.com"\n'
        "status: open\nrelated:\n  zendesk: [1, 2]\n---\n"
    )
    state = read_existing_state(root / "STATE.md")
    assert state["owner"] == "alice@axon.com"
    assert state["fork"] == "B"
    # nested keys under related: must be ignored by the narrow parser
    assert "zendesk" not in state


def test_preflight_passes_when_unclaimed(tmp_path):
    folder = scaffold_ticket(tmp_path, 100)
    preflight_soft_lock(folder, owner="bob@axon.com", force=False)  # no raise


def test_preflight_passes_for_same_owner(tmp_path):
    folder = scaffold_ticket(tmp_path, 101)
    (folder.root / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')
    preflight_soft_lock(folder, owner="alice@axon.com", force=False)  # no raise


def test_preflight_blocks_other_owner(tmp_path):
    folder = scaffold_ticket(tmp_path, 102)
    (folder.root / "STATE.md").write_text(
        '---\nfork: "A"\nowner: "alice@axon.com"\nstatus: open\n---\n'
    )
    with pytest.raises(SoftLockConflict) as exc:
        preflight_soft_lock(folder, owner="bob@axon.com", force=False)
    err = exc.value
    assert err.existing_owner == "alice@axon.com"
    assert err.current_owner == "bob@axon.com"
    # diff summarizes the owner change for the CLI to render
    assert any(field == "owner" for field, _old, _new in err.summary)


def test_preflight_force_overrides_other_owner(tmp_path):
    folder = scaffold_ticket(tmp_path, 103)
    (folder.root / "STATE.md").write_text('---\nowner: "alice@axon.com"\n---\n')
    preflight_soft_lock(folder, owner="bob@axon.com", force=True)  # no raise


def test_scaffold_stages_runbooks(tmp_path):
    folder = scaffold_ticket(tmp_path, 18432)
    assert folder.runbooks == folder.root / "runbooks"
    assert (folder.runbooks / "low-audio.md").is_file()
    assert (folder.runbooks / "apex.md").is_file()
    assert (folder.runbooks / "fork-rubric.md").is_file()
