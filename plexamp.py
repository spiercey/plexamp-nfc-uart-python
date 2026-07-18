"""Helpers for preparing and verifying Plexamp companion API requests."""

import itertools
import time
import uuid
from dataclasses import dataclass
from xml.etree import ElementTree
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


PLAY_MEDIA_PATH = "/player/playback/playMedia"
TIMELINE_PATH = "/player/timeline/poll"
ACTIVE_STATES = {"buffering", "playing"}
CLIENT_IDENTIFIER = str(uuid.uuid4())
DEVICE_NAME = "Plexamp NFC UART"
DEFAULT_REQUEST_TIMEOUT = 5
DEFAULT_VERIFICATION_TIMEOUT = 10
DEFAULT_POLL_INTERVAL = 0.5

_COMMAND_IDS = itertools.count(1)


class InvalidPlaybackURL(ValueError):
    """Raised when an autoplay URL cannot identify its Plex server."""


@dataclass(frozen=True)
class PlaybackResult:
    """The verified outcome of a Plexamp playback request."""

    success: bool
    message: str


@dataclass(frozen=True)
class _MusicTimeline:
    state: str
    queue_id: str


class _PlaybackError(RuntimeError):
    """Raised for expected companion API failures."""


def prepare_playback_url(url):
    """Add the Plex server identifier required by direct Plexamp requests.

    Plex web links normally derive ``machineIdentifier`` from the authority of
    their nested ``server://`` URI. Direct companion API calls bypass that web
    client preparation, so reproduce it here for playMedia requests.
    """
    parsed_url = urlsplit(url)
    if parsed_url.path != PLAY_MEDIA_PATH:
        return url

    query_items = parse_qsl(parsed_url.query, keep_blank_values=True)
    inner_uri = next(
        (value for key, value in query_items if key == "uri"),
        None,
    )
    if inner_uri is None:
        raise InvalidPlaybackURL("playMedia URL is missing its uri parameter")

    parsed_uri = urlsplit(inner_uri)
    if parsed_uri.scheme != "server" or not parsed_uri.netloc:
        raise InvalidPlaybackURL(
            "playMedia uri must use the server://<machine-id>/... format"
        )

    if any(key == "machineIdentifier" for key, _ in query_items):
        return url

    query_items.append(("machineIdentifier", parsed_uri.netloc))
    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query_items),
            parsed_url.fragment,
        )
    )


class PlexampClient:
    """Send playback commands and verify their effect on Plexamp."""

    def __init__(
        self,
        session=None,
        request_timeout=DEFAULT_REQUEST_TIMEOUT,
        verification_timeout=DEFAULT_VERIFICATION_TIMEOUT,
        poll_interval=DEFAULT_POLL_INTERVAL,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ):
        self.session = session or requests.Session()
        self.request_timeout = request_timeout
        self.verification_timeout = verification_timeout
        self.poll_interval = poll_interval
        self.monotonic = monotonic
        self.sleep = sleep
        self.headers = {
            "X-Plex-Client-Identifier": CLIENT_IDENTIFIER,
            "X-Plex-Device-Name": DEVICE_NAME,
        }

    def play(self, url):
        """Request playback and wait for Plexamp to create an active queue."""
        command_id = next(_COMMAND_IDS)

        try:
            prepared_url = prepare_playback_url(url)
            previous_timeline = self._music_timeline(
                prepared_url,
                max(command_id - 1, 0),
                self.request_timeout,
            )
            command_url = _replace_query_parameter(
                prepared_url,
                "commandID",
                str(command_id),
            )
            self._get(command_url, self.request_timeout, "playback")
        except (InvalidPlaybackURL, _PlaybackError, ValueError) as error:
            return PlaybackResult(False, str(error))

        previous_queue_id = None
        if previous_timeline.state in ACTIVE_STATES:
            previous_queue_id = previous_timeline.queue_id

        deadline = self.monotonic() + self.verification_timeout
        while self.monotonic() < deadline:
            remaining = deadline - self.monotonic()
            try:
                timeline = self._music_timeline(
                    prepared_url,
                    command_id,
                    min(self.request_timeout, remaining),
                )
            except _PlaybackError as error:
                return PlaybackResult(False, str(error))

            has_active_queue = (
                timeline.state in ACTIVE_STATES and bool(timeline.queue_id)
            )
            queue_changed = (
                previous_queue_id is None
                or timeline.queue_id != previous_queue_id
            )
            if has_active_queue and queue_changed:
                return PlaybackResult(True, "Plexamp created an active play queue")

            remaining = deadline - self.monotonic()
            if remaining <= 0:
                break
            self.sleep(min(self.poll_interval, remaining))

        return PlaybackResult(
            False,
            "Plexamp did not create an active play queue within "
            f"{self.verification_timeout:g} seconds",
        )

    def _music_timeline(self, playback_url, command_id, timeout):
        parsed_url = urlsplit(playback_url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise _PlaybackError("playback URL must include a scheme and host")

        timeline_url = urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                TIMELINE_PATH,
                urlencode({"wait": "0", "commandID": str(command_id)}),
                "",
            )
        )
        response = self._get(timeline_url, timeout, "timeline")

        try:
            document = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as error:
            raise _PlaybackError(f"Plexamp returned an invalid timeline: {error}")

        for timeline in document.iter():
            if (
                timeline.tag.rsplit("}", 1)[-1] == "Timeline"
                and timeline.get("type") == "music"
            ):
                return _MusicTimeline(
                    state=timeline.get("state", "").lower(),
                    queue_id=timeline.get("playQueueID", ""),
                )

        raise _PlaybackError("Plexamp timeline did not include music state")

    def _get(self, url, timeout, request_name):
        try:
            response = self.session.get(
                url,
                headers=self.headers,
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise _PlaybackError(f"Plexamp {request_name} request failed: {error}")

        if not 200 <= response.status_code < 300:
            raise _PlaybackError(
                f"Plexamp {request_name} request returned HTTP "
                f"{response.status_code}"
            )
        return response


def _replace_query_parameter(url, name, value):
    parsed_url = urlsplit(url)
    query_items = [
        (key, item_value)
        for key, item_value in parse_qsl(
            parsed_url.query,
            keep_blank_values=True,
        )
        if key != name
    ]
    query_items.append((name, value))
    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query_items),
            parsed_url.fragment,
        )
    )
