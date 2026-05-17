from __future__ import annotations

import unittest

from tradestation_api_wrapper.errors import StreamError, StreamParseError
from tradestation_api_wrapper.stream import JsonStreamParser, StreamEventKind, classify_stream_message


class StreamParserTests(unittest.TestCase):
    def test_parses_split_json_object(self) -> None:
        parser = JsonStreamParser()

        self.assertEqual(parser.feed('{"OrderID":"1",'), [])
        self.assertEqual(parser.feed('"Status":"ACK"}'), [{"OrderID": "1", "Status": "ACK"}])

    def test_parses_utf8_split_across_chunks(self) -> None:
        parser = JsonStreamParser()
        prefix = b'{"Symbol":"'
        encoded = prefix + "€".encode("utf-8") + b'"}'

        self.assertEqual(parser.feed(encoded[: len(prefix) + 1]), [])
        self.assertEqual(parser.feed(encoded[len(prefix) + 1 :]), [{"Symbol": "€"}])

    def test_parses_multiple_objects_in_one_chunk(self) -> None:
        parser = JsonStreamParser()

        messages = parser.feed('{"OrderID":"1"}\n{"OrderID":"2"}')

        self.assertEqual([message["OrderID"] for message in messages], ["1", "2"])

    def test_classifies_stream_status_and_heartbeat(self) -> None:
        self.assertEqual(
            classify_stream_message({"StreamStatus": "EndSnapshot"}).kind,
            StreamEventKind.END_SNAPSHOT,
        )
        self.assertEqual(
            classify_stream_message({"StreamStatus": "GoAway"}).kind,
            StreamEventKind.GO_AWAY,
        )
        self.assertEqual(
            classify_stream_message({"Heartbeat": 1}).kind,
            StreamEventKind.HEARTBEAT,
        )

    def test_market_depth_message_with_message_field_is_data(self) -> None:
        self.assertEqual(
            classify_stream_message({"Message": "quote", "Bids": [], "Asks": []}).kind,
            StreamEventKind.DATA,
        )
        self.assertEqual(
            classify_stream_message({"Message": "depth", "Entries": []}).kind,
            StreamEventKind.DATA,
        )

    def test_malformed_json_raises_without_dropping_good_messages(self) -> None:
        parser = JsonStreamParser()

        with self.assertRaises(StreamParseError):
            parser.feed('{"bad":}')

    def test_stream_error_class_carries_payload(self) -> None:
        error = StreamError("TradeStation stream returned an error", {"Error": "BadRequest"})

        self.assertEqual(str(error), "TradeStation stream returned an error")
        self.assertEqual(error.payload, {"Error": "BadRequest"})


if __name__ == "__main__":
    unittest.main()
