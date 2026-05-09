from __future__ import annotations

import unittest

from tradestation_api_wrapper.errors import StreamParseError
from tradestation_api_wrapper.stream import JsonStreamParser, StreamEventKind, classify_stream_message


class StreamParserTests(unittest.TestCase):
    def test_parses_split_json_object(self) -> None:
        parser = JsonStreamParser()

        self.assertEqual(parser.feed('{"OrderID":"1",'), [])
        self.assertEqual(parser.feed('"Status":"ACK"}'), [{"OrderID": "1", "Status": "ACK"}])

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

    def test_malformed_json_raises_without_dropping_good_messages(self) -> None:
        parser = JsonStreamParser()

        with self.assertRaises(StreamParseError):
            parser.feed('{"bad":}')


if __name__ == "__main__":
    unittest.main()

