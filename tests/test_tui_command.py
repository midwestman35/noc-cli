from noc_cli.tui.command import (
    KNOWN_COMMANDS,
    CommandMatch,
    match_commands,
    parse_input,
)


def test_match_commands_slash_lists_all_in_insertion_order():
    matches = match_commands("/")
    assert [m.name for m in matches] == list(KNOWN_COMMANDS)


def test_match_commands_prefix_filters_to_one():
    assert [m.name for m in match_commands("/in")] == ["investigate"]


def test_match_commands_is_case_insensitive():
    assert [m.name for m in match_commands("/IN")] == ["investigate"]


def test_match_commands_no_prefix_match_is_empty():
    assert match_commands("/zzz") == []


def test_match_commands_space_means_arguments_not_menu():
    assert match_commands("/file foo") == []


def test_match_commands_freeform_and_blank_are_empty():
    assert match_commands("why is this call stuck?") == []
    assert match_commands("") == []


def test_command_match_carries_name_and_description():
    [match] = match_commands("/doctor")
    assert match == CommandMatch("doctor", KNOWN_COMMANDS["doctor"])


def test_slash_command_with_args():
    p = parse_input("/investigate 45747")
    assert p.is_command is True
    assert p.name == "investigate"
    assert p.args == "45747"


def test_slash_command_no_args_lowercased():
    p = parse_input("/Refresh")
    assert p.is_command is True
    assert p.name == "refresh"
    assert p.args == ""


def test_freeform_text_is_not_a_command():
    p = parse_input("why is this call stuck?")
    assert p.is_command is False
    assert p.name == ""
    assert p.args == "why is this call stuck?"


def test_blank_input_is_freeform_empty():
    p = parse_input("   ")
    assert p.is_command is False
    assert p.args == ""


def test_known_commands_cover_the_spec_set():
    assert {
        "investigate",
        "scout",
        "doctor",
        "help",
        "refresh",
        "copy",
        "open",
        "quit",
        "file",
        "paste",
        "revise",
        "retry",
    } <= set(KNOWN_COMMANDS)
