from __future__ import annotations

import unittest

from tradestation_api_wrapper.redaction import REDACTION, redact, redact_text


class RedactionTests(unittest.TestCase):
    def test_redacts_nested_secret_keys(self) -> None:
        payload = {
            "client_secret": "secret",
            "nested": {"Refresh-Token": "refresh", "safe": "value"},
            "orders": [{"AccountID": "123456789"}],
        }

        redacted = redact(payload)

        self.assertEqual(redacted["client_secret"], REDACTION)
        self.assertEqual(redacted["nested"]["Refresh-Token"], REDACTION)
        self.assertEqual(redacted["nested"]["safe"], "value")
        self.assertEqual(redacted["orders"][0]["AccountID"], REDACTION)

    def test_redacts_bearer_tokens_in_text(self) -> None:
        text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"
        self.assertIn(REDACTION, redact_text(text))
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redact_text(text))


if __name__ == "__main__":
    unittest.main()

