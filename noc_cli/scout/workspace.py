from __future__ import annotations

from pathlib import Path

from noc_cli.runbooks import stage_runbooks


def materialize_workspace(root: Path) -> Path:
    """Stage embedded runbooks under root/runbooks and return that path."""
    root.mkdir(parents=True, exist_ok=True)
    runbooks_dir = root / "runbooks"
    runbooks_dir.mkdir(parents=True, exist_ok=True)
    stage_runbooks(runbooks_dir)
    return runbooks_dir
