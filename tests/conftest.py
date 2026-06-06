"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_live_runbook_classifier(monkeypatch):
    """Default-stub the live runbook classifier so no test calls the model.

    ``noc_cli.grounding.select_runbook`` makes a live Haiku call. Any test that
    reaches the investigate path (directly, or via the watch auto-investigate
    flow) would otherwise hit the API. This default returns "no runbook"
    (rubric-core fallback). Tests that need a specific selection override it
    with their own ``monkeypatch.setattr``; ``tests/test_grounding.py`` is
    unaffected because it imports ``select_runbook`` directly and exercises the
    real function with an injected ``query_fn``.
    """

    async def _stub(ticket_text, hypothesis, **kwargs):
        from noc_cli.grounding import RunbookSelection

        return RunbookSelection(slug=None, confidence="low", rationale="test-default")

    monkeypatch.setattr("noc_cli.grounding.select_runbook", _stub)
