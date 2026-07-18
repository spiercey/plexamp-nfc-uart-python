import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from plexamp import PlexampClient


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def timeline(state, queue_id=None):
    queue_attribute = ""
    if queue_id is not None:
        queue_attribute = f' playQueueID="{queue_id}"'
    return FakeResponse(
        text=(
            "<MediaContainer>"
            f'<Timeline type="music" state="{state}"{queue_attribute}/>'
            "</MediaContainer>"
        )
    )


class PlexampClientTests(unittest.TestCase):
    machine_id = "0123456789abcdef0123456789abcdef01234567"

    def playback_url(self):
        inner_uri = f"server://{self.machine_id}/library/metadata/12345/children"
        return "http://localhost:32500/player/playback/playMedia?" + urlencode(
            {"uri": inner_uri}
        )

    def client(self, responses, verification_timeout=10):
        session = FakeSession(responses)
        clock = FakeClock()
        client = PlexampClient(
            session=session,
            verification_timeout=verification_timeout,
            poll_interval=0.5,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        return client, session, clock

    def test_empty_player_transition_to_populated_queue_succeeds(self):
        client, session, _ = self.client([
            timeline("stopped"),
            FakeResponse(),
            timeline("playing", "42"),
        ])

        result = client.play(self.playback_url())

        self.assertTrue(result.success)
        self.assertEqual(len(session.calls), 3)
        command_url, command_options = session.calls[1]
        parameters = parse_qs(urlsplit(command_url).query)
        self.assertIn("commandID", parameters)
        self.assertEqual(
            parameters["machineIdentifier"],
            [self.machine_id],
        )
        for _, options in session.calls:
            self.assertTrue(options["headers"]["X-Plex-Client-Identifier"])
            self.assertEqual(
                options["headers"]["X-Plex-Device-Name"],
                "Plexamp NFC UART",
            )
        self.assertIn("headers", command_options)

    def test_successive_commands_use_increasing_ids(self):
        client, session, _ = self.client([
            timeline("stopped"),
            FakeResponse(),
            timeline("playing", "42"),
            timeline("stopped"),
            FakeResponse(),
            timeline("playing", "43"),
        ])

        self.assertTrue(client.play(self.playback_url()).success)
        self.assertTrue(client.play(self.playback_url()).success)

        first_id = int(parse_qs(urlsplit(session.calls[1][0]).query)["commandID"][0])
        second_id = int(parse_qs(urlsplit(session.calls[4][0]).query)["commandID"][0])
        self.assertGreater(second_id, first_id)

    def test_existing_playback_requires_queue_to_change(self):
        client, session, clock = self.client([
            timeline("playing", "42"),
            FakeResponse(),
            timeline("playing", "42"),
            timeline("buffering", "43"),
        ])

        result = client.play(self.playback_url())

        self.assertTrue(result.success)
        self.assertEqual(len(session.calls), 4)
        self.assertEqual(clock.sleeps, [0.5])

    def test_non_successful_response_fails(self):
        client, session, _ = self.client([
            timeline("stopped"),
            FakeResponse(status_code=503),
        ])

        result = client.play(self.playback_url())

        self.assertFalse(result.success)
        self.assertIn("HTTP 503", result.message)
        self.assertEqual(len(session.calls), 2)

    def test_request_error_fails(self):
        client, session, _ = self.client([
            timeline("stopped"),
            requests.ConnectionError("connection refused"),
        ])

        result = client.play(self.playback_url())

        self.assertFalse(result.success)
        self.assertIn("connection refused", result.message)
        self.assertEqual(len(session.calls), 2)

    def test_malformed_timeline_fails(self):
        client, session, _ = self.client([
            FakeResponse(text="<not-valid-xml"),
        ])

        result = client.play(self.playback_url())

        self.assertFalse(result.success)
        self.assertIn("invalid timeline", result.message)
        self.assertEqual(len(session.calls), 1)

    def test_verification_timeout_fails(self):
        client, session, clock = self.client([
            timeline("stopped"),
            FakeResponse(),
            timeline("stopped"),
            timeline("stopped"),
        ], verification_timeout=1)

        result = client.play(self.playback_url())

        self.assertFalse(result.success)
        self.assertIn("within 1 seconds", result.message)
        self.assertEqual(len(session.calls), 4)
        self.assertEqual(clock.sleeps, [0.5, 0.5])


if __name__ == "__main__":
    unittest.main()
