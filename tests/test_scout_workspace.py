from noc_cli.scout.workspace import materialize_workspace


def test_materialize_stages_runbooks(tmp_path):
    runbooks_dir = materialize_workspace(tmp_path)

    assert runbooks_dir == tmp_path / "runbooks"
    staged = {p.name for p in runbooks_dir.glob("*.md")}
    assert "low-audio.md" in staged
    assert "dropped-calls.md" in staged


def test_materialize_is_idempotent(tmp_path):
    materialize_workspace(tmp_path)
    runbooks_dir = materialize_workspace(tmp_path)

    assert (runbooks_dir / "low-audio.md").exists()
