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
