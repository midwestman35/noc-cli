import anyio
from noc_cli.grounding import RunbookSelection, select_runbook


def _fake_query(result_text):
    async def q(*, prompt, options):
        class R:
            result = result_text

        yield R()

    return q


def test_select_runbook_picks_valid_slug():
    q = _fake_query(
        '{"slug":"low-audio","confidence":"high","rationale":"audio dropouts"}'
    )
    sel = anyio.run(
        lambda: select_runbook(
            "caller reports choppy audio",
            "[low audio]",
            query_fn=q,
            options_factory=lambda: None,
        )
    )
    assert sel == RunbookSelection(
        slug="low-audio", confidence="high", rationale="audio dropouts"
    )


def test_low_confidence_yields_no_slug():
    q = _fake_query('{"slug":"low-audio","confidence":"low","rationale":"unsure"}')
    sel = anyio.run(
        lambda: select_runbook(
            "vague text", "", query_fn=q, options_factory=lambda: None
        )
    )
    assert sel.slug is None
    assert sel.confidence == "low"


def test_unknown_slug_yields_no_slug():
    q = _fake_query('{"slug":"not-a-runbook","confidence":"high","rationale":"x"}')
    sel = anyio.run(
        lambda: select_runbook("t", "", query_fn=q, options_factory=lambda: None)
    )
    assert sel.slug is None


def test_unparseable_output_yields_no_slug():
    q = _fake_query("the model rambled with no json")
    sel = anyio.run(
        lambda: select_runbook("t", "", query_fn=q, options_factory=lambda: None)
    )
    assert sel.slug is None
    assert sel.confidence == "low"


def test_classifier_error_yields_no_slug():
    async def boom(*, prompt, options):
        raise RuntimeError("network")
        yield  # pragma: no cover

    sel = anyio.run(
        lambda: select_runbook("t", "", query_fn=boom, options_factory=lambda: None)
    )
    assert sel.slug is None
