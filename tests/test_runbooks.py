from noc_cli.models import APPROVED_SYMPTOM_TAGS
from noc_cli.runbooks import (
    DOMAIN_MAP,
    RUNBOOK_SLUGS,
    _load_runbook,
    runbook_for_tag,
    runbook_slug_from_path,
)


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


def test_domain_map_covers_all_runbook_slugs():
    slugs = {s.slug for s in DOMAIN_MAP}
    assert slugs == set(RUNBOOK_SLUGS)
    assert len(DOMAIN_MAP) == 6


def test_domain_map_tags_are_all_approved():
    for s in DOMAIN_MAP:
        assert s.tag in APPROVED_SYMPTOM_TAGS, f"{s.tag!r} not approved"
        assert s.domain and s.label


def test_runbook_slug_from_path_matches_staged_runbook_paths():
    assert runbook_slug_from_path("/t/18432/runbooks/low-audio.md") == "low-audio"
    assert runbook_slug_from_path("runbooks/apex.md") == "apex"


def test_runbook_slug_from_path_rejects_non_runbooks():
    assert runbook_slug_from_path("/t/18432/runbooks/fork-rubric.md") is None
    assert runbook_slug_from_path("/t/18432/logs/station.log") is None
    assert runbook_slug_from_path("/t/18432/analysis/notes.md") is None
    assert runbook_slug_from_path("no-ani.md") is None
    assert runbook_slug_from_path("") is None


def test_stage_runbooks_copies_six_runbooks_and_rubric(tmp_path):
    from noc_cli.runbooks import stage_runbooks

    written = stage_runbooks(tmp_path / "runbooks")
    assert "fork-rubric.md" in written
    for slug in RUNBOOK_SLUGS:
        assert f"{slug}.md" in written
        staged = (tmp_path / "runbooks" / f"{slug}.md").read_text()
        assert len(staged) > 100
    assert (tmp_path / "runbooks" / "fork-rubric.md").read_text().startswith("---")
