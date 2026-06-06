from noc_cli.tui.command import KNOWN_COMMANDS, parse_input


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
