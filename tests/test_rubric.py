from noc_cli.rubric import Rubric, load_rubric


def test_load_rubric_has_version_and_text():
    r = load_rubric()
    assert isinstance(r, Rubric)
    assert r.version == "2026-05-13"
    assert len(r.text) > 1000
    assert "# NOC Triage" in r.text


def test_contains_row_matches_verbatim_substring():
    r = load_rubric()
    assert r.contains_row(
        "customer LAN, switch, or SDWAN. Link to site master ticket"
    )


def test_contains_row_rejects_unknown_and_empty():
    r = load_rubric()
    assert not r.contains_row("this string is not in the rubric anywhere")
    assert not r.contains_row("")
    assert not r.contains_row("   \n\t ")


def test_core_stops_before_symptom_class_tables():
    r = load_rubric()
    assert "## Symptom Class" not in r.core
    assert "Step 0" in r.core
    assert "Engineering Jira" in r.core
    assert len(r.core) < len(r.text)


def test_contains_row_accepts_extra_texts():
    r = load_rubric()
    needle = "RTP present, timestamps healthy"
    assert not r.contains_row(needle, extra_texts=[])
    assert r.contains_row(needle, extra_texts=["... RTP present, timestamps healthy ..."])
