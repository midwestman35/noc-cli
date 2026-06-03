from noc_cli.models import Comment, Ticket


def test_ticket_parses_zendesk_payload_and_ignores_extra_fields():
    payload = {
        "id": 18432,
        "subject": "PSAP - No ANI displaying",
        "description": "Caller ID not populating",
        "status": "open",
        "tags": ["no_ani", "apex"],
        "created_at": "2026-06-01T03:02:17Z",
        "organization_id": 99,  # extra field must not break parsing
    }
    ticket = Ticket.model_validate(payload)
    assert ticket.id == 18432
    assert ticket.status == "open"
    assert "apex" in ticket.tags
    assert ticket.comments == []


def test_comment_defaults_public_true():
    comment = Comment.model_validate({"id": 1, "body": "hello"})
    assert comment.public is True
    assert comment.attachments == []
