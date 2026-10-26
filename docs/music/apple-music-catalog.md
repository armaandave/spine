# Apple Music catalog

Spine is moving its music catalog from MusicBrainz release groups to Apple Music albums,
so albums are listed the way Apple Music lists them: a deluxe edition such as *SOS
Deluxe: LANA* is its own album instead of one of dozens of editions inside *SOS*.
MusicBrainz stays permanently, both as the fallback and for the detailed genres behind
the genre pages.

## Status

Step 1, the groundwork, is in place:

- the `apple_music` source
- the `MusicAlbumLink` and `AppleAlbum` tables
- the Apple Music API client
- the listing rule

Nothing outside the tests calls the client yet, so music is still served from
MusicBrainz everywhere.

## Listings and identity (`src/app/music_listing.py`)

Apple's catalog has more than one album record for what a person sees as one album: an
explicit album and its clean twin, or an older upload and its re-upload. Spine shows each
group as one **listing**.

- **Same listing:** albums in each other's `other-versions` family, with the same artist
  and the same normalized title. Normalizing applies NFKC, casefolding, straight quotes,
  `[]` read as `()`, and collapsed spaces.
- **Edition words keep listings apart.** "SOS" and "SOS Deluxe: LANA" are two listings,
  and so are "(Deluxe)", "(Remastered 2022)" and " - Single" versions.
- **An album's ID is the number at the end of its `url`.** Apple keeps old duplicate IDs
  whose link names the real album: Year Zero's `1147788150` links to `1440826538`.
- **The identity** is the one ID Spine stores for a listing, chosen in this order:
  1. a member that already has a Spine `Item`; its stored ID never moves
  2. it can be streamed (`playParams`)
  3. it is complete, or not released yet
  4. explicit, then unrated, then clean
  5. the most tracks
  6. the lowest ID
- **Hidden listings** are left out of search, artist pages and other versions. A listing
  is hidden when no member can be streamed, or when every member is incomplete with a
  release date that has passed. Its IDs still open.

## Tables

- **`MusicAlbumLink`** has one row per MusicBrainz release group Spine has matched, or
  tried to match, to an Apple album. It records:
  - the match: status, method and tier
  - the MusicBrainz genres and the exact Spotify link
  - what the album's `Item` looked like before a move, so the move can be undone
- **`AppleAlbum`** has one row per Apple album ID Spine has seen. It records:
  - the listing identity the ID belongs to
  - its UPC
  - its MusicBrainz release group, genres, first release date and Spotify link

## Client (`src/app/providers/apple_music.py`)

- **Developer token.** It is signed with ES256 from three settings:
  `APPLE_MUSIC_TEAM_ID`, `APPLE_MUSIC_KEY_ID`, and `APPLE_MUSIC_PRIVATE_KEY` (base64 of
  the `.p8`, on one line).
  - It lasts 12 hours, is kept in memory, and is re-signed when less than an hour is
    left.
  - It is never logged or cached.
  - When the settings are missing, calls raise "Apple Music isn't configured" without
    sending a request.
- **Rate limits.** There are two sessions, each with its own Redis bucket:
  - Requests someone is waiting on go through the user-facing session, at 5 per second.
    They have a 10-second timeout and no retries on 429. A 429, a 5xx or a timeout
    pauses them for 60 seconds, using a flag in Redis.
  - Tasks and commands go through the background session, at 2 per second, with the
    usual retries.
- **Fetches.** They return Apple's JSON unmapped:
  - `search`
  - `albums`, 100 IDs per call
  - `album`, which follows the tracks `next` link
  - `albums_by_upc` and `songs_by_isrc`, 25 per call
  - `artist`
- **Apple's behaviour, as observed in October 2026:**
  - `filter[upc]` returns a barcode's whole product family, not just the album with that
    barcode.
  - `songs?filter[isrc]` returns every album a recording is on, compilations included.
  - `other-versions` doesn't always include the deluxe: SOS and LANA are separate
    families.
  - Clean edits have different ISRCs from the explicit songs.

## Fixtures

Recorded responses live in `src/app/tests/mock_data/apple_music/`.

- `scripts/music/record_apple_fixtures.py --token-file <file>` records them. The token
  file must be mode 600 and outside the repository.
- The script trims each response to the fields the code reads. It refuses to write a
  file containing anything that looks like a token.
- The tests sign with a throwaway key, and they fail on any request that would reach the
  network.
