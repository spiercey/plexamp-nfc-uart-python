# The Plexamp URL format

This project reads a URL off an NFC tag and hands it to Plexamp, but what that
URL actually looks like is not documented anywhere, and the obvious guesses are
all wrong in the same way. This is what works, so you can write tags without
owning a phone that can write them, or build the links yourself from Plex ids.

Worked out against Plexamp on iOS in September 2026. Everything below was
tested; anything that was not is called out at the end.

## Browse and playback are not variations of each other

They use different identifiers, which is the single fact that makes everything
else make sense.

| | Identifier | Needs the server named? |
|---|---|---|
| **Browse** — open the album's page | the **global** album id, `plex://album/<id>` | no |
| **Playback** — start it playing | the **local** rating key, plus a play queue | yes |

Browsing, for each kind:

```
https://listen.plex.tv/album/<id>      from guid plex://album/<id>
https://listen.plex.tv/artist/<id>     from guid plex://artist/<id>
https://listen.plex.tv/track/<id>      from guid plex://track/<id>
```

The id is what your server reports as `guid`, minus the `plex://<kind>/`
prefix. It arrives free on any library listing, so no extra call, and it
identifies the thing everywhere rather than on one server, which is why nothing
else has to be named.

**Playlists cannot be browsed this way.** A playlist's guid is not a `plex://`
id at all, it is something like `com.plexapp.agents.none://<uuid>`. Playlists
are server-local objects, so there is no global thing to link to. They can still
be played, which is below.

Playback is the rest of this document.

**Do not put a rating key on a browse URL.** It opens *an* album, just not
yours: rating keys mean nothing off the server that issued them, so it lands on
whatever happens to carry that number. This is a silent wrong answer, not an
error, and it is the single most misleading thing about the whole area.

## The short version

Plexamp does not take a link to an album. It takes a **player command**, and
that command needs a **play queue**, not just an item:

```
https://listen.plex.tv/player/playback/playMedia
  ?type=music
  &key=/library/metadata/<ratingKey>
  &containerKey=/playQueues/<playQueueID>?own=1
  &machineIdentifier=<server machine identifier>
  &protocol=http
  &address=<server address>
  &port=32400
  &offset=0
  &commandID=1
```

Tapping that on a device with Plexamp installed opens the app and starts
playing. It is a universal link, not a custom scheme.

## Why a play queue

Given `key` alone, Plexamp answers **"can't start playback, try again"**. The
Plex player protocol wants something to play rather than something to find, so
you have to build a queue first and pass its `containerKey`. This is the same
thing any Plex controller does before telling a player to start.

Build one against your server:

```
POST http://<server>:32400/playQueues
  ?type=audio
  &uri=server://<machineIdentifier>/com.plexapp.plugins.library/library/metadata/<ratingKey>
  &shuffle=0&repeat=0&continuous=0
  &X-Plex-Token=<token>

Headers: X-Plex-Client-Identifier, X-Plex-Product, X-Plex-Version, X-Plex-Platform
```

The response carries `playQueueID` and the queue's items. Use the first item's
`key` as `key`, and the queue id as `containerKey`. An album URI produces a
queue of the whole album, so this plays the record, not one track.

## Albums, tracks, artists and playlists

All four are the same command. Only the `uri` the play queue is built from
changes, and Plex expands it:

| Kind | Queue built from | Result |
|---|---|---|
| Album | `library/metadata/<ratingKey>` | every track on the album |
| Track | `library/metadata/<ratingKey>` | that one track |
| Artist | `library/metadata/<ratingKey>` | everything by them in the library |
| Playlist | `playlists/<ratingKey>` | the playlist, smart ones included |

Album, track and artist take the same path; Plex works out which from the item
itself. Only the playlist differs, and note it is `playlists/<id>` — with
`/items` on the end you get a queue of nothing, silently, which looks exactly
like a broken playlist. `playlistID=<id>` as a query parameter works too.

Once the queue exists, the playMedia URL is identical for all four.

## You do not need a token in the URL

Confirmed. Every playback link above was tapped with no `token` parameter at
all and played. Plexamp is signed in to the account that owns the queue, so it
authenticates itself.

Leave it out. A token in a URL ends up in browser history and in anything the
link is shared through, and it is a long lived credential for the whole server.
If a real NFC tag does carry one, that is worth knowing about before you stick
one to a record sleeve where anyone can read it with a phone.

## How this was found

The format is not published, but it is recoverable, and this repository is
where the thread starts. It takes the tag's URL and swaps the host for
Plexamp's own local API:

```
https://listen.plex.tv/<path>   ->   http://localhost:32500/<path>
```

That host swap is the giveaway. The path is a **Plexamp API path**, and headless
Plexamp's API on port 32500 speaks the Plex player protocol, which is where
`/player/playback/playMedia` comes from.

A second clue, from a comment on r/homeassistant: the URL Plexamp writes **does
not fit an NTAG213**, which holds roughly 130 characters of URI. Any candidate
much shorter than that is the wrong shape. A command carrying a queue id, a
machine identifier and an address comfortably exceeds it.

## Dead ends, so you can skip them

- **There is no `plexamp://` album scheme.** The bare scheme opens the app and
  does nothing else. Every path and query variant tried under it did the same.
- **`https://listen.plex.tv/library/metadata/<ratingKey>` opens the wrong
  album.** A rating key is meaningful only on the server that issued it;
  resolved anywhere else it lands on whatever happens to share that number.
- **The Share menu's Copy Link gives you the browse URL**, on `app.plex.tv`,
  even with the playback toggle set. Copy Link and NFC tag writing do not
  produce the same string.
- **A global id will not play anything.** `plex://album/<id>` is exactly right
  for browsing and useless for playback, because playback needs a queue and a
  queue is built on a particular server. Using either id for the other job is
  the mistake that costs the most time here.
- **The `mbid://` Plex also carries is no use for either.**
- **MusicBrainz cannot bridge an external album to your copy.** Going from a
  Spotify id to a release to a release group lands on a different MBID than the
  one Plex holds for the same album, at least sometimes. Relevant if you are
  trying to link something like an album-of-the-day service to a local library:
  there is no identifier joining them, and matching on artist and title is the
  only route.

## Reliability

Opening the app this way works, but not every single time. When it misses it
tends to open Plexamp without landing anywhere in particular, and trying again
works. That appears to be the app rather than the URLs, since the same link
behaves differently on consecutive taps.

## Still unknown

- The exact string Plexamp writes to a tag. Everything here is a
  reconstruction from the protocol that behaves correctly, not a capture, so
  the tag may well carry something shorter or differently ordered.
- Stations. The tag readers recognise a `stations` path, but there was nothing
  to test it against.
