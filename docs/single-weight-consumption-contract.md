# Single-Weight Consumption Contract

Status: **approved v1; normative for new single-weight consumption work**

Last reviewed: **2026-07-17**

This document defines watching, listening, logging, ratings, likes, library
membership, dates, imports, and social activity for single-weight media in
Spine. A single-weight item is consumed as a whole and has no tracked progress.

Initial examples are movies and music release groups such as albums, EPs, and
singles. The rules are media-neutral. Music changes user-facing language only:
`watched` becomes `listened`, and `rewatch` becomes `relisten`.

For these interactions, this contract supersedes older assumptions that marking
an album listened must create a diary entry. Marking consumed and logging are
separate actions.

## Terminology

| Term | Meaning |
| --- | --- |
| Consumed | The user has watched or listened to the item at least once. |
| Mark consumed | Record consumption without claiming a date or creating a diary entry. |
| Log | Record one consumption event on a user-selected calendar date. |
| Current rating | The user's one mutable present-day rating for the title. |
| Diary rating | The optional historical rating stored on one diary entry. |
| Media like | The user's current title-level liked state. |
| Diary like | Whether the user liked one particular logged consumption. |
| Direct consumption | Undated evidence established outside the diary that the user consumed the item. |
| Library membership | The item has a tracking record for the user. |

## Canonical state model

For each user and single-weight item, Spine may store:

| State | Cardinality | Purpose |
| --- | --- | --- |
| Tracking | Zero or one | Canonical consumed state and library membership. |
| Direct-consumption marker | Zero or one | Preserves undated consumption independently of diary history. |
| Current rating | Zero or one | The user's mutable rating for the title now. |
| Current-rating source | Zero or one | Identifies the current rating as independent or coupled to one diary entry. |
| Media like | Zero or one | The user's current title-level liked state. |
| Current-like source | Zero or one | Identifies the current like state as independent or coupled to one diary entry. |
| Diary entries | Zero or more | Dated historical consumption events. |
| Diary rating | Zero or one per diary entry | Rating for that particular consumption event. |
| Diary like | One boolean per diary entry | Whether that particular consumption was liked. |

Tracking is never duplicated when an item is consumed or logged repeatedly.
Diary entries are appendable history and may contain multiple entries for the
same title and calendar date.

Consumed tracking exists exactly while at least one of these forms of evidence
exists:

- a direct-consumption marker; or
- one or more diary entries.

The direct-consumption marker is a durable fact, not a consumption count. It is
established by marking the item consumed, directly rating it, or directly
liking it outside a diary entry. A rating or like copied from a newly created
diary entry does not establish this marker.

## Core laws

### SW-001: Marking consumed is not logging

Marking an item watched or listened:

- establishes direct consumption;
- ensures one completed tracking record exists;
- adds the item to the library;
- creates no diary entry;
- creates no social diary activity; and
- records no consumption date.

The action means only: "I consumed this at least once."

### SW-002: Logging always implies consumed tracking

Successfully creating a diary log:

- creates exactly one dated diary entry;
- ensures one completed tracking record exists;
- ensures the item is in the library; and
- never creates a duplicate tracking record.

The diary entry is the historical event. Tracking is the current library state.

### SW-003: Library membership derives from tracking

An item with tracking is in the library. Marking consumed, directly rating or
liking an item, and successfully logging an item all ensure tracking exists.

### SW-004: Mark-consumed tracking has no date

Spine must not assign today, the request time, or any synthetic date when an
item is marked consumed without a log.

Until a diary entry exists, the item's consumed date is unknown. Undated
consumption must not be counted in date-based diary statistics or presented as
having occurred today.

### SW-005: Last-consumed date is derived from diary history

When diary entries exist, the displayed last-consumed date is the greatest
user-selected diary consumption date, not the most recently created database
row.

- Adding an older backdated log does not replace a newer last-consumed date.
- Deleting the latest-dated log recomputes the date from remaining logs.
- Deleting all logs returns the consumed date to unknown if direct consumption
  keeps the item tracked; otherwise SW-009 removes tracking.

### SW-006: Diary dates are user-local calendar dates

A diary log records a calendar date in the user's timezone. Single-weight diary
behavior does not require an exact consumption time.

The date is mandatory and defaults to the current calendar date in the user's
timezone when the new-log composer opens.

Future consumption dates are invalid. Planning future media belongs to planning
or release features, not the diary.

### SW-007: Duplicate same-day logs are valid

Spine permits multiple diary entries for the same user, title, and calendar
date. A user may consume the same item more than once in one day. No uniqueness
constraint may reject this case.

### SW-008: The eye toggles consumed state only when history permits

Tapping an empty eye marks the item consumed. Tapping a filled eye unwatches the
item only when no diary entries exist.

Unwatching an item with no diary entries removes:

- the direct-consumption marker;
- tracking and library membership;
- the current rating; and
- the current media like.

It does not remove independent custom-list membership.

When one or more diary entries exist, the item cannot be unwatched because the
diary proves consumption. The UI must explain that the logs must be deleted
first rather than silently failing or deleting them automatically.

### SW-009: Deleting the sole consumption history removes derived tracking

Deleting a diary entry does not unwatch an item while another diary entry or a
direct-consumption marker still proves consumption.

If the final diary entry is deleted without a direct-consumption marker, the
item becomes unwatched and the normal SW-008 cleanup applies. If direct
consumption exists, deleting all diary entries leaves the item watched with an
unknown consumed date.

Therefore, a diary-derived current rating or media like does not by itself keep
the item watched after its final log is deleted. A direct eye action, direct
current-rating action, or direct media-like action does, because each
establishes the direct-consumption marker.

### SW-010: Rewatch and relisten default from prior consumption

The default for a new diary entry is determined before that entry is saved:

- no prior consumed tracking means the first log is not a rewatch or relisten;
- prior consumed tracking with no diary entries means the first log defaults to
  rewatch or relisten;
- every later log defaults to rewatch or relisten; and
- the user may correct the default before saving.

Marking watched/listened, directly rating an item, or liking an unconsumed item
establishes at least one prior consumption. A later first diary entry therefore
represents at least the second consumption. Spine does not guess how many times
the item was consumed before it was first tracked.

Diary entries and the undated prior-consumption fact are the canonical evidence
for repeat behavior. Do not maintain an independent repeat counter that can
drift from them.

The repeat value saved on a diary entry is a historical assertion. Editing or
deleting another diary entry does not recalculate it. The default is calculated
only when a new composer opens, and the user may later edit that entry's value.

### SW-011: The eye and heart actions survive rating-picker dismissal

Activating an empty media-page eye or heart may present an optional rating
picker. Dismissing that picker without choosing a rating does not undo the base
action:

- the eye remains marked consumed; and
- the heart remains consumed and liked.

Whether persistence occurs before or during presentation is an implementation
detail. The user-observable result and failure handling must obey this contract.

If the eye cannot unwatch an item because diary entries exist, the UI must tell
the user that the logs must be deleted first.

### SW-012: Diary history universally prevents tracking removal

No control, screen, API, or other operation may remove consumed tracking while
one or more diary entries exist. All entry points must enforce the same
invariant as the media-page eye. An operation must never silently delete diary
history as a side effect of removing an item from the library.

## Rating laws

### SW-100: Current and diary ratings are distinct

The current rating is one mutable title-level value. Each diary entry has its
own optional historical rating.

Changing the current rating never rewrites any diary rating.

The current rating is either independent or coupled to exactly one diary entry.
The source is behavioral provenance, not a second rating value.

### SW-101: A rated new log updates both ratings

Creating a diary entry with a rating:

- stores that rating on the new diary entry; and
- sets the title's current rating to the same value; and
- couples the current rating to that new diary entry, replacing any prior
  independent or diary source.

This also applies when the new log is backdated. The user is explicitly
expressing a rating now even though the recorded consumption occurred earlier.

### SW-102: An unrated new log preserves the current rating

Creating a diary entry without a rating does not clear or otherwise modify the
current rating.

### SW-103: Clearing ratings is scoped

- Clearing a diary rating removes that diary entry's rating. It changes the
  current rating only when that diary entry is its source, as defined by
  SW-104.
- Clearing the current rating removes only the current rating and its source;
  it does not clear diary ratings.
- An unrated log is not an instruction to clear the current rating.

Diary ratings remain intact when the current rating is changed or cleared.

### SW-104: Editing the source diary rating updates the current rating

While the current rating is coupled to a diary entry:

- changing that diary rating changes the current rating to the same value; and
- clearing that diary rating also clears the current rating.

Editing or clearing any other diary rating does not affect the current rating,
except when the current rating is empty under SW-108. Coupling follows the identified source entry, not whichever entry has the
newest consumption date, creation time, or edit time.

### SW-105: A direct current-rating change decouples older diary history

Directly setting or changing the current rating to a rating:

- establishes direct consumption and ensures tracking and library membership;
- makes the current rating independent of diary history; and
- never changes a diary rating.

After decoupling, editing any existing diary entry cannot overwrite the current
rating. A later rated new log becomes the source again under SW-101. An empty
current rating can still be filled by a log edit under SW-108.

Directly clearing the current rating clears its source and leaves diary ratings
unchanged. Clearing does not itself establish direct consumption, but it does
not erase an existing direct-consumption marker or unwatch the item.

Example: Log A is created with 4 stars, so Log A and the current rating are both
4 and coupled. The user directly changes the current rating to 5, making it
independent. If the user later edits Log A to 2, Log A becomes 2 while the
current rating remains 5. A later rated Log B may replace and recouple the
current rating under SW-101.

### SW-106: Deleting a diary entry does not roll back the current rating

Deleting a diary entry does not restore an older rating, select another diary
rating, or otherwise recalculate the current rating while consumed tracking
remains.

If the deleted entry was the current-rating source and tracking remains, its
current value is preserved and becomes independent. This preservation does not
retroactively establish direct consumption. If deleting the final diary entry
also unwatches the item under SW-009, the SW-008 cleanup removes the current
rating instead.

### SW-107: Ratings use ten half-star values

A rating is one of: 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, or 5.0.
Zero means unrated and is represented by the absence of a rating, not by a
stored zero-star rating.

### SW-108: Rating a log fills an empty current rating

When the title has no current rating, editing a diary entry to a new rating:

- sets the current rating to that value; and
- couples the current rating to that diary entry.

Saving a diary entry without changing its rating does not fill an empty
current rating, so a directly cleared rating stays cleared when an unrelated
field of an older log is edited.

Example: Log A was imported without a rating, so the title has no current
rating. The user edits Log A to 3 stars. The current rating becomes 3 and Log A
is its source, so the media page and community ratings show it.

## Like laws

### SW-200: Liking an unconsumed item also marks it consumed

Liking an item that has no tracking must atomically:

- establish direct consumption;
- ensure completed tracking exists;
- add the item to the library; and
- set the media like.

All effects succeed together or none are committed.

### SW-201: Unliking alone does not remove consumption

Unliking through the heart control removes the media like without removing
tracking, library membership, current rating, diary entries, or historical
diary likes.

Directly liking an item establishes a durable direct-consumption marker.
Unliking does not erase that marker; the eye is the explicit control for
unwatching an item when SW-008 permits it.

### SW-202: Current and diary likes are distinct

The current media like is one mutable title-level value. Each diary entry stores
whether that particular consumption was liked.

- Liking or unliking the title directly never rewrites diary likes.
- A new log stores its heart state on that diary entry and makes the current
  media-like state match it, including turning the current heart off when the
  saved log is unhearted.
- The new log becomes the source of the current media-like state without
  establishing direct consumption independently of the log.
- Old diary entries retain their original heart state when the current media
  like changes.

### SW-203: Current media-like state couples and decouples like rating

While the current media-like state is coupled to a diary entry, editing that
entry's heart updates the current state. Editing any other diary heart does not.

Directly changing the title-level heart makes its state independent of diary
history and never rewrites a diary heart. Directly turning the heart on
establishes direct consumption. Directly turning it off does not establish or
erase direct consumption.

After decoupling, editing an existing diary entry cannot overwrite the current
state. A later new log becomes the source again under SW-202.

Deleting the source diary entry does not select another diary heart. If
tracking remains, the current state is preserved and becomes independent
without retroactively establishing direct consumption. If deleting the final
log unwatches the item under SW-009, normal unwatch cleanup removes the current
media like.

## Save and cancellation laws

### SW-300: Log creation is atomic

A log submission may create or update diary, tracking, library, current-rating,
and like state. The submitted operation succeeds as one transaction or leaves
all prior state unchanged.

A failure must not leave a diary entry without required tracking or apply only
some of the user's requested changes.

### SW-301: Cancelling the composer has no effects

Closing or cancelling the log composer saves nothing. Draft rating, like,
review, tags, date, and other changes have no effect until the log saves
successfully.

### SW-302: The visible log draft is authoritative when saved

The new-log composer initializes its rating and heart from the title's current
rating and media-like state. These visible values are part of the draft even if
the user does not touch their controls.

Saving stores those values on the new diary entry and applies the normal
current-state coupling laws. Empty rating remains unrated. The heart always
saves its visible on or off state. Cancelling remains governed by SW-301.

### SW-303: Diary editing behavior is independent of entry point

Opening a diary entry from the diary, media detail, or another surface uses the
same editor and the same save, coupling, validation, and deletion laws. The
originating screen must not change domain behavior.

## Social and privacy laws

### SW-400: Visibility is account-level

Social visibility follows the user's public or private profile. Single diary
entries do not have independent visibility settings in this contract.

### SW-401: Activity creation is action-specific

- Creating a visible diary log creates one diary activity, including any rating
  on that log. It does not also create a separate rating activity.
- Directly changing the title's current rating creates or updates standalone
  rating activity.
- Marking consumed without a rating creates no social activity.
- Changing the media like creates no social feed activity in this phase.
- Editing a diary entry updates that diary activity rather than creating a new
  unrelated rating event, even when a coupled current rating changes.

Private profiles do not expose these activities to viewers who cannot view the
profile.

These rules govern the `social.Activity` rows written. What people see as
activity is defined in [the activity contract](activity-contract.md): logs only,
so standalone rating rows are not shown.

## Import laws

### SW-500: Imports use the same state model

- An imported consumed item without a date creates completed, undated tracking
  with direct consumption.
- An imported diary row creates a dated diary entry and ensures tracking.
- An imported diary rating remains attached to its diary entry.
- Imports do not emit social feed activity.

Imports may use bulk implementation paths, but their final state must be
indistinguishable from state created under this contract.

### SW-501: Imported undated title state becomes current

An imported undated title-level rating or liked state:

- establishes direct consumption and tracking;
- becomes the current rating or media like; and
- is independent of diary history.

It creates no synthetic diary entry or consumption date.

### SW-502: Independent current state survives diary imports

Importing diary history does not overwrite an independent current rating or
media-like state.

When the corresponding current state is not independent, the combined local
and imported diary history determines it after the import:

- the latest-consumed rated diary entry supplies and becomes the source of the
  current rating; and
- the latest-consumed diary entry supplies and becomes the source of the
  current heart state, whether that entry is hearted or unhearted.

Rows are ordered first by consumption date and then by source order. Existing
rows precede newly imported rows on the same date. Among imported rows on the
same date, the last row in the import source wins.

### SW-503: Imports preserve or derive repeat assertions

When an imported diary row supplies a rewatch or relisten value, Spine trusts
and stores that value.

When the source omits it, Spine derives the default by processing rows in
consumption-date and source order under SW-010. Prior local diary history and
direct consumption count as prior consumption when deriving the value.

### SW-504: Reimporting the same source record is idempotent

Spine skips a diary row that it has already imported from the same identifiable
source record. Re-running the same import must not duplicate its prior rows.

This deduplication is based on import-source identity, not merely matching
title and date. Distinct same-day source records remain distinct diary entries
under SW-007.

## Media-language law

### SW-600: Movies and albums share behavior

Movies and music release groups use the same state transitions, invariants, and
persistence rules. Only user-facing copy changes:

| Movie language | Music language |
| --- | --- |
| Watched | Listened |
| Mark watched | Mark listened |
| Rewatch | Relisten |
| Date watched | Date listened |

Do not create separate movie and music implementations for this behavior.

## Consistency law

### SW-700: All surfaces reflect canonical state

After a successful mutation, media detail, library, diary, profile, activity,
and search/discovery surfaces must agree about:

- consumed state;
- library membership;
- current rating;
- media like;
- diary count; and
- last-consumed date.

Optimistic UI may temporarily represent a pending mutation, but failure must
restore the last confirmed canonical state.

## Contract change policy

The numbered laws above are normative product behavior. Implementation details
may change without editing this document, but behavior that contradicts a law
requires an explicit product decision and contract update first.

Behavior omitted from this working contract is not implicitly decided.
