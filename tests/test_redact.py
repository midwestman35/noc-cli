from noc_cli.redact import RedactionCounts, redact, redact_value, residual_pii_warning


def test_redacts_phone():
    out, counts = redact("call (555) 123-4567 now")
    assert out == "call <PHONE> now"
    assert counts.phones == 1


def test_preserves_pre_redacted_phone():
    out, counts = redact("call ***-***-1234 now")
    assert "***-***-1234" in out
    assert counts.phones == 0


def test_redacts_street_address():
    out, counts = redact("the call from 123 Main Street is bad")
    assert out == "the call from <ADDR> is bad"
    assert counts.addresses == 1


def test_redacts_coords():
    out, counts = redact("loc 36.1699, -115.1398 reported")
    assert out == "loc <COORDS> reported"
    assert counts.coords == 1


def test_ignores_phone_inside_token():
    out, counts = redact("abc5551234567xyz")
    assert out == "abc5551234567xyz"
    assert counts.phones == 0


def test_preserves_operational_ids():
    text = "Call-ID 7d209ad5-3935-440e ticket 18432 station Aurora-12 cnc de9ee414-da5a"
    out, counts = redact(text)
    assert "18432" in out
    assert "Aurora-12" in out
    assert "de9ee414-da5a" in out
    assert counts.phones == 0


def test_residual_warning_fires_on_dense_loose_coords():
    counts = RedactionCounts(enabled=True)
    text = "a 36.16, -115.13 b 40.71, -74.00 c 34.05, -118.24 d"
    w = residual_pii_warning(text, counts)
    assert w is not None
    assert "residual" in w
    assert "not blocked" in w


def test_residual_warning_below_threshold_is_none():
    counts = RedactionCounts(enabled=True)
    assert residual_pii_warning("loc 36.16, -115.13 reported", counts) is None


def test_residual_warning_ignores_hyphenated_operational_ids():
    counts = RedactionCounts(enabled=True)
    text = "CB-911-2024-5551234 ref TICKET-2024-5559999 cnc 2024-5551000-aa"
    assert residual_pii_warning(text, counts) is None


def test_residual_warning_does_not_self_trigger_on_sentinels():
    counts = RedactionCounts(enabled=True)
    text = "<PHONE> <ADDR> <COORDS> <PHONE> <ADDR> <COORDS>"
    assert residual_pii_warning(text, counts) is None


def test_redacts_two_space_separated_phones():
    # Both bare numbers must be redacted — a captured-boundary design would
    # consume the shared space and miss the second.
    out, counts = redact("555-123-4567 555-987-6543")
    assert out == "<PHONE> <PHONE>"
    assert counts.phones == 2


def test_redact_value_scrubs_string_leaves_and_counts():
    obj = {"loc": "37.7749, -122.4194", "id": 5, "tags": ["x"]}
    out, total = redact_value(obj)
    assert out["loc"] == "<COORDS>"
    assert out["id"] == 5  # non-str untouched
    assert out["tags"] == ["x"]
    assert total == 1


def test_redact_value_recurses_into_lists_of_dicts():
    obj = {"comments": [{"body": "37.7749, -122.4194"}, {"body": "clean"}]}
    out, total = redact_value(obj)
    assert out["comments"][0]["body"] == "<COORDS>"
    assert out["comments"][1]["body"] == "clean"
    assert total == 1
