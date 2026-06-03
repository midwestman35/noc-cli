from noc_cli.runbooks import RUNBOOK_SLUGS, _load_runbook, runbook_for_tag


def test_each_approved_symptom_tag_maps_to_a_runbook():
    cases = {
        "[No ANI]": "no-ani",
        "[No ALI]": "no-ali",
        "[low audio]": "low-audio",
        "[dropped calls]": "dropped-calls",
        "[event history]": "event-history",
        "[apex]": "apex",
    }
    for tag, slug in cases.items():
        result = runbook_for_tag(tag)
        assert result is not None, f"{tag} should resolve"
        got_slug, section = result
        assert got_slug == slug
        assert len(section) > 100
        assert "##" in section  # real markdown content


def test_lookup_is_case_and_bracket_insensitive():
    a = runbook_for_tag("[No ANI]")
    b = runbook_for_tag("no ani")
    c = runbook_for_tag("No_ANI")
    assert a is not None and b is not None and c is not None
    assert a[0] == b[0] == c[0] == "no-ani"


def test_unclassified_and_vendor_have_no_runbook():
    assert runbook_for_tag("[unclassified]") is None
    assert runbook_for_tag("[vendor]") is None
    assert runbook_for_tag("nonsense") is None


def test_all_declared_slugs_resolve_to_packaged_files():
    # Every slug in RUNBOOK_SLUGS must have shipped markdown content.
    for slug in RUNBOOK_SLUGS:
        text = _load_runbook(slug)
        assert text and len(text) > 100
