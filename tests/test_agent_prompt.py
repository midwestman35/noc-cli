from noc_cli.agent.prompt import APPROVED_TAGS_IN_PROMPT, SYSTEM_PROMPT, build_system_prompt


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
    assert "read" in lower and ("only" in lower or "no write" in lower or "never write" in lower)


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
