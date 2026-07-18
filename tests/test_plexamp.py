import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from plexamp import InvalidPlaybackURL, prepare_playback_url


class PreparePlaybackURLTests(unittest.TestCase):
    machine_id = "0123456789abcdef0123456789abcdef01234567"

    def playback_url(self, inner_uri, **parameters):
        query = {"uri": inner_uri}
        query.update(parameters)
        return "http://localhost:32500/player/playback/playMedia?" + urlencode(query)

    def test_adds_machine_identifier_from_server_uri(self):
        inner_uri = (
            f"server://{self.machine_id}/com.plexapp.plugins.library/"
            "library/metadata/12345/children"
        )

        result = prepare_playback_url(self.playback_url(inner_uri))
        parameters = parse_qs(urlsplit(result).query)

        self.assertEqual(parameters["machineIdentifier"], [self.machine_id])
        self.assertEqual(parameters["uri"], [inner_uri])

    def test_preserves_explicit_machine_identifier(self):
        inner_uri = f"server://{self.machine_id}/library/metadata/12345"
        explicit_id = "explicit-server-id"

        result = prepare_playback_url(
            self.playback_url(inner_uri, machineIdentifier=explicit_id)
        )

        self.assertEqual(
            result,
            self.playback_url(
                inner_uri,
                machineIdentifier=explicit_id,
            ),
        )

    def test_preserves_equivalent_percent_encoded_uri_data(self):
        inner_uri = (
            f"server://{self.machine_id}/library/metadata/12345/children"
            "?source=a%2Fb&title=Album One"
        )

        result = prepare_playback_url(self.playback_url(inner_uri))

        self.assertEqual(parse_qs(urlsplit(result).query)["uri"], [inner_uri])

    def test_rejects_missing_inner_uri(self):
        url = "http://localhost:32500/player/playback/playMedia?type=music"

        with self.assertRaises(InvalidPlaybackURL):
            prepare_playback_url(url)

    def test_rejects_malformed_inner_uri(self):
        with self.assertRaises(InvalidPlaybackURL):
            prepare_playback_url(self.playback_url("not-a-server-uri"))


if __name__ == "__main__":
    unittest.main()
