import pytest

from noc_cli.seed import menu_lines, resolve_seed


def test_suspect_slug_maps_to_tag():
    assert resolve_seed("low-audio", interactive=False) == "[low audio]"
    assert resolve_seed("apex", interactive=False) == "[apex]"


def test_unknown_suspect_raises():
    with pytest.raises(ValueError):
        resolve_seed("nonsense", interactive=False)


def test_non_interactive_no_suspect_returns_empty():
    assert resolve_seed(None, interactive=False) == ""


def test_interactive_number_maps_to_tag():
    tag = resolve_seed(
        None, interactive=True, prompt_fn=lambda _label: "4", echo_fn=lambda _msg: None
    )
    assert tag == "[low audio]"


def test_interactive_not_sure_returns_empty():
    tag = resolve_seed(
        None, interactive=True, prompt_fn=lambda _label: "7", echo_fn=lambda _msg: None
    )
    assert tag == ""


def test_menu_lines_list_all_labels_and_domains():
    text = "\n".join(menu_lines())
    assert "SIP / UC" in text
    assert "Audio / media quality" in text
    assert "Not sure" in text
