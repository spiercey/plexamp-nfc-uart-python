# The Plexamp URL format

What actually goes on a tag, so you can build links from Plex ids instead of
guessing. Tested against Plexamp on iOS, September 2026.

## Two different things

| | Identifier | Server named? |
|---|---|---|
| Browse — open the page | global guid, `plex://<kind>/<id>` | no |
| Playback — start it playing | local `ratingKey` + a play queue | yes |

Using one identifier for the other's job is the mistake that costs the most
time. A ratingKey on a browse URL opens a *different* item, with no error.

## Browse

```
https://listen.plex.tv/album/<id>
https://listen.plex.tv/artist/<id>
https://listen.plex.tv/track/<id>
```

`<id>` is the item's `guid` minus the `plex://<kind>/` prefix. It is on every
library listing already. No server, no token.

Playlists have no global guid (theirs is `com.plexapp.agents.none://<uuid>`),
so they cannot be browsed this way. They can be played.

## Playback

```
https://listen.plex.tv/player/playback/playMedia
  ?type=music
  &key=/library/metadata/<ratingKey>
  &containerKey=/playQueues/<playQueueID>?own=1
  &machineIdentifier=<server machine id>
  &protocol=http
  &address=<server address>
  &port=32400
  &offset=0
  &commandID=1
```

No token needed — Plexamp is signed in to the account that owns the queue.

### The queue is required

With `key` alone, Plexamp says **"can't start playback, try again"**. Build one
first:

```
POST http://<server>:32400/playQueues
  ?type=audio
  &uri=server://<machineId>/com.plexapp.plugins.library/<path>
  &shuffle=0&repeat=0&continuous=0
  &X-Plex-Token=<token>

Headers: X-Plex-Client-Identifier, X-Plex-Product, X-Plex-Version, X-Plex-Platform
```

Take `playQueueID` and the first item's `key` from the response.

`<path>` by kind:

| Kind | path | queue contains |
|---|---|---|
| Album | `library/metadata/<ratingKey>` | the whole album |
| Track | `library/metadata/<ratingKey>` | that track |
| Artist | `library/metadata/<ratingKey>` | everything of theirs in the library |
| Playlist | `playlists/<ratingKey>` | the playlist, smart ones included |

Album, track and artist share a path; Plex infers the kind. `playlistID=<id>`
as a parameter also works for playlists.

**`playlists/<id>/items` returns an empty queue and no error.** Use
`playlists/<id>`.

## Why it doesn't fit an NTAG213

That tag holds ~130 characters of URI. A playback URL carries a queue id, a
machine identifier and an address, so it is longer. If a candidate URL is much
shorter than 130 characters, it is the wrong shape.

## Dead ends

- No `plexamp://` album scheme exists. The bare scheme opens the app, nothing more.
- `listen.plex.tv/library/metadata/<ratingKey>` opens the wrong item, silently.
- Share menu → Copy Link gives the `app.plex.tv` browse URL, whatever the
  playback toggle is set to. It is not what gets written to a tag.
- A global guid will not play anything; playback needs a queue, and a queue is
  built on one server.
- The `mbid://` guid Plex also carries is no use for either.

## An app bug worth knowing about

The first link works. A second link, to a different item, opens Plexamp but
leaves it wherever it already was. Quitting Plexamp and tapping again lands
correctly, every time.

So it is not the URLs: the same link works or does not depending only on
whether the app has already handled one this session. If you are building
something that hands Plexamp several links in a row, expect this.

## Not verified

- The literal bytes on a tag. This is reconstructed from the player protocol,
  not captured, so a real tag may differ in order or extras.
- Stations. The reader recognises a `stations` path; nothing here to test with.
