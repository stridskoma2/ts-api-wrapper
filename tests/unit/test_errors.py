from __future__ import annotations

import unittest

from tradestation_api_wrapper.errors import (
    AuthenticationError,
    StreamError,
    TradeStationAPIError,
)

SECRET = "SECRET-TOKEN-VALUE"


class ErrorReprRedactionTests(unittest.TestCase):
    def test_api_error_repr_and_str_exclude_payload(self) -> None:
        error = TradeStationAPIError(401, "Unauthorized", "expired", {"refresh_token": SECRET})

        self.assertNotIn(SECRET, repr(error))
        self.assertNotIn(SECRET, str(error))
        self.assertEqual(str(error), "TradeStation API 401 Unauthorized: expired")

    def test_authentication_error_repr_excludes_payload(self) -> None:
        error = AuthenticationError(0, "AuthError", "token refresh failed", {"token": SECRET})

        self.assertNotIn(SECRET, repr(error))

    def test_stream_error_repr_excludes_payload(self) -> None:
        error = StreamError("stream returned an error", {"AccountID": SECRET})

        self.assertNotIn(SECRET, repr(error))
        self.assertEqual(str(error), "stream returned an error")

    def test_payload_stays_available_to_callers(self) -> None:
        payload = {"Error": "BadRequest"}

        error = TradeStationAPIError(400, "BadRequest", "bad request", payload)

        self.assertEqual(error.payload, payload)


if __name__ == "__main__":
    unittest.main()
