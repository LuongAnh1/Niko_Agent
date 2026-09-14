import unittest

from bots.chat_gateway import (
    build_identity_context,
    format_identity_reply,
    parse_allowed_user_keys,
    parse_user_aliases,
    telegram_message_to_gateway,
)


class ChatGatewayTests(unittest.TestCase):
    def test_parses_aliases_with_default_platform(self):
        aliases = parse_user_aliases("123=Anh Luong;telegram:456=Nam,789=Con cu")

        self.assertEqual(aliases["telegram:123"], "Anh Luong")
        self.assertEqual(aliases["telegram:456"], "Nam")
        self.assertEqual(aliases["telegram:789"], "Con cu")

    def test_parses_allowed_user_keys_with_default_platform(self):
        keys = parse_allowed_user_keys("123, telegram:456;789")

        self.assertEqual(keys, {"telegram:123", "telegram:456", "telegram:789"})

    def test_builds_telegram_gateway_message(self):
        message = telegram_message_to_gateway(
            {
                "text": "@NikoAgent hello",
                "from": {
                    "id": 123,
                    "username": "luonganh",
                    "first_name": "Luong",
                    "last_name": "Anh",
                    "language_code": "vi",
                },
                "chat": {
                    "id": -100,
                    "type": "supergroup",
                    "title": "Niko Test",
                },
            },
            {"telegram:123": "Boss"},
        )

        self.assertEqual(message.user.key, "telegram:123")
        self.assertEqual(message.user.label, "Boss")
        self.assertEqual(message.chat_id, "-100")
        self.assertEqual(message.chat_title, "Niko Test")

    def test_formats_identity_context(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )

        context = build_identity_context(message)
        reply = format_identity_reply(message)

        self.assertIn("user_key: telegram:123", context)
        self.assertIn("chat_id: 456", reply)


if __name__ == "__main__":
    unittest.main()
