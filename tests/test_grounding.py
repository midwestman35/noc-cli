import anyio

from noc_cli.grounding import RunbookSelection, select_runbook, verify_grounding
from noc_cli.models import Confidence, ForkLetter, Handoff


def _handoff(quote, slug):
    """Build a minimal Handoff using model_validate to satisfy all required fields."""
    d = {
        "rubric_version": "t",
        "intake": {"ticket_id": 1},
        "evidence_preflight": {},
        "fork_packet": {
            "fork_letter": ForkLetter.B.value,
            "confidence": Confidence.HIGH.value,
            "symptom_tag": "[low audio]",
            "quoted_rubric_row": quote,
            "runbook_reference": {"slug": slug, "section": "Fork decision"},
        },
        "drafts": {},
    }
    return Handoff.model_validate(d)


def test_verify_true_on_normalized_match():
    from noc_cli.runbooks import _load_runbook

    low_audio_text = _load_runbook("low-audio")
    assert low_audio_text  # sanity
    # Pick a real line and mangle whitespace/case to exercise normalisation.
    real_line = next(ln for ln in low_audio_text.splitlines() if ln.strip())
    mangled = real_line.upper().replace(" ", "   ")
    h = _handoff(mangled, "low-audio")
    ok, note = verify_grounding(
        h, selected_slug="low-audio", runbook_text=low_audio_text
    )
    assert ok is True


def test_verify_false_when_quote_absent():
    h = _handoff("a row that is nowhere in the runbook", "low-audio")
    ok, note = verify_grounding(
        h, selected_slug="low-audio", runbook_text="unrelated text"
    )
    assert ok is False
    assert "low-audio" in note


def test_verify_none_when_no_quote():
    h = _handoff("", "low-audio")
    ok, note = verify_grounding(h, selected_slug="low-audio", runbook_text="x")
    assert ok is None


def test_verify_rubric_core_path_when_no_slug():
    # selection had no slug → verify the quote against the passed rubric-core text
    h = _handoff("CORE MARKER ROW", "")
    ok, note = verify_grounding(
        h, selected_slug=None, runbook_text="rubric core ... CORE MARKER ROW ... end"
    )
    assert ok is True
    assert "rubric-core" in note


def test_verify_true_on_resteer_to_a_different_runbook():
    from noc_cli.runbooks import _load_runbook

    no_ani_text = _load_runbook("no-ani")
    assert no_ani_text  # sanity
    quoted = next(ln for ln in no_ani_text.splitlines() if ln.strip())
    # selected was low-audio, but the agent re-steered and cited no-ani:
    h = _handoff(quoted, "no-ani")
    ok, note = verify_grounding(
        h,
        selected_slug="low-audio",
        runbook_text="LOW AUDIO TEXT (selected, not cited)",
    )
    assert ok is True
    assert "no-ani" in note


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
