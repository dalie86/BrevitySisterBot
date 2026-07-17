import pytest

from app.integrations.telegram import parse_update


def test_parse_group_text_message() -> None:
    update = parse_update(
        {
            "update_id": 10,
            "message": {
                "message_id": 20,
                "date": 1784210000,
                "chat": {"id": -1001, "type": "supergroup", "title": "Demo"},
                "from": {
                    "id": 30,
                    "first_name": "Pavel",
                    "username": "pavel",
                    "is_bot": False,
                },
                "text": "Привет",
            },
        }
    )

    assert update.update_id == 10
    assert update.message is not None
    assert update.message.text == "Привет"
    assert update.message.chat_id == -1001
    assert update.message.chat_title == "Demo"
    assert update.message.username == "@pavel"


def test_parse_caption_and_bot_sender() -> None:
    update = parse_update(
        {
            "update_id": 11,
            "message": {
                "message_id": 21,
                "date": 1784210000,
                "chat": {"id": -1002, "type": "group"},
                "from": {"id": 31, "first_name": "Helper", "is_bot": True},
                "caption": "Подпись",
            },
        }
    )

    assert update.message is not None
    assert update.message.text == "Подпись"
    assert update.message.sender_is_bot is True


def test_parse_unsupported_update_keeps_update_identity() -> None:
    update = parse_update({"update_id": 12, "edited_message": {}})

    assert update.update_id == 12
    assert update.message is None
    assert update.callback is None


def test_parse_private_chat_shared() -> None:
    update = parse_update(
        {
            "update_id": 20,
            "message": {
                "message_id": 30,
                "date": 1784210000,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "first_name": "Pavel"},
                "chat_shared": {
                    "request_id": -77,
                    "chat_id": -1001,
                    "title": "Demo",
                },
            },
        }
    )

    assert update.chat_shared is not None
    assert update.chat_shared.request_id == -77
    assert update.chat_shared.shared_chat_id == -1001
    assert update.chat_shared.title == "Demo"
    assert update.chat_shared.user_id == 42
    assert update.chat_shared.private_chat_id == 42
    assert update.message is None


def test_parse_restricted_membership_uses_is_member() -> None:
    update = parse_update(
        {
            "update_id": 21,
            "chat_member": {
                "chat": {"id": -1001, "type": "supergroup"},
                "from": {"id": 7, "first_name": "Admin"},
                "date": 1784210000,
                "old_chat_member": {"user": {"id": 42}, "status": "member"},
                "new_chat_member": {
                    "user": {
                        "id": 42,
                        "first_name": "Pavel",
                        "last_name": "Petrov",
                        "username": "pavel",
                    },
                    "status": "restricted",
                    "is_member": False,
                },
            },
        }
    )

    assert update.chat_member is not None
    assert update.chat_member.chat_id == -1001
    assert update.chat_member.user_id == 42
    assert update.chat_member.username == "@pavel"
    assert update.chat_member.full_name == "Pavel Petrov"
    assert update.chat_member.new_membership.status == "restricted"
    assert update.chat_member.new_membership.is_member is False


@pytest.mark.parametrize(
    ("status", "is_member", "can_read", "is_admin"),
    [
        ("creator", True, True, True),
        ("administrator", True, True, True),
        ("member", True, True, False),
        ("restricted", True, True, False),
        ("restricted", False, False, False),
        ("left", False, False, False),
        ("kicked", False, False, False),
    ],
)
def test_membership_capabilities(
    status: str,
    is_member: bool,
    can_read: bool,
    is_admin: bool,
) -> None:
    from app.domain.models import TelegramMembership

    membership = TelegramMembership(status=status, is_member=is_member)

    assert membership.can_read is can_read
    assert membership.is_admin is is_admin
