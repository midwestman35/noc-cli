from noc_cli.agent.prompt import (
    APPROVED_TAGS_IN_PROMPT,
    SYSTEM_PROMPT,
    build_system_prompt,
)
from noc_cli.rubric import load_rubric
from noc_cli.runbooks import RUNBOOK_SLUGS


def test_system_prompt_documents_live_tools():
    sp = build_system_prompt("## Symptom Class\nx")
    assert "Live tools" in sp
    for token in ("get_ticket", "get_comments", "search", "search_history"):
        assert token in sp
    # reinforces that live output is already redacted
    assert "redacted" in sp.lower()


def test_system_prompt_contains_role():
    assert "NOC" in SYSTEM_PROMPT
    assert "triage" in SYSTEM_PROMPT.lower()
    assert "L3" in SYSTEM_PROMPT or "senior" in SYSTEM_PROMPT.lower()


def test_system_prompt_contains_all_approved_tags():
    for tag in APPROVED_TAGS_IN_PROMPT:
        assert tag in SYSTEM_PROMPT, f"tag {tag!r} missing from SYSTEM_PROMPT"


def test_system_prompt_excludes_vendor_tag():
    assert "[vendor]" not in SYSTEM_PROMPT


def test_system_prompt_contains_inconclusive_preference():
    lower = SYSTEM_PROMPT.lower()
    assert "inconclusive" in lower or "cannot fork" in lower


def test_system_prompt_contains_read_only_constraint():
    lower = SYSTEM_PROMPT.lower()
    assert "read" in lower and (
        "only" in lower or "no write" in lower or "never write" in lower
    )


def test_system_prompt_contains_json_contract():
    assert "JSON" in SYSTEM_PROMPT or "json" in SYSTEM_PROMPT
    assert "Handoff" in SYSTEM_PROMPT or "handoff" in SYSTEM_PROMPT.lower()


def test_build_system_prompt_injects_rubric():
    prompt = build_system_prompt("RUBRIC_SENTINEL_12345")
    assert "RUBRIC_SENTINEL_12345" in prompt


def test_approved_tags_in_prompt_excludes_vendor():
    assert "[vendor]" not in APPROVED_TAGS_IN_PROMPT
    assert "[unclassified]" in APPROVED_TAGS_IN_PROMPT
    assert len(APPROVED_TAGS_IN_PROMPT) == 7


def test_system_prompt_lists_all_runbook_slugs():
    for slug in RUNBOOK_SLUGS:
        assert f"runbooks/{slug}.md" in SYSTEM_PROMPT, f"{slug} missing from domain map"


def test_system_prompt_has_grounding_protocol():
    lower = SYSTEM_PROMPT.lower()
    assert "hypothesis" in lower
    assert "re-steer" in lower or "re-ground" in lower or "pivot" in lower


def test_build_system_prompt_with_core_excludes_symptom_class_tables():
    prompt = build_system_prompt(load_rubric().core)
    assert "## Symptom Class" not in prompt
    assert "SBC sends unsolicited BYE" not in prompt


def test_build_system_prompt_trims_full_rubric_to_core():
    prompt = build_system_prompt(load_rubric().text)
    assert "## Symptom Class" not in prompt
    assert "SBC sends unsolicited BYE" not in prompt
