# Book Tracking Contract

Status: **agent-ready product contract**

Last reviewed: **2026-07-20**

This document defines library status, direct status assignment, live reading,
progress, completion, and diary behavior for books in Spine. Its numbered laws
are the implementation contract for this scope.

The single-weight contract remains authoritative for movies and music. Books
use the same media-detail interaction language where it fits, but completion
must coexist with progress and non-completion statuses.

## Terminology

| Term | Meaning |
| --- | --- |
| Tracking | The user's current library relationship with one book. |
| Status | The book's current library section. |
| Journey | A live attempt that begins when the user starts reading and may be paused, resumed, dropped, or completed. |
| Progress update | A saved page or percentage position during a journey. |
| Completion log | The diary entry created when a live journey is finished and its log composer is saved. |
| Direct status assignment | Placing a book in a library section without claiming when the state occurred. |
| Mark as Read | Directly record that the book was completed sometime in the past without starting a journey or creating a diary entry. |

## Canonical statuses and library sections

### BK-001: Every status is a library section

Book tracking has exactly one current status:

- Planning, displayed as To Read;
- In Progress, displayed as Currently Reading;
- Paused;
- Dropped, displayed as Did Not Finish; or
- Completed, displayed as Read.

For the Dropped state specifically:

- the library section is Did Not Finish;
- compact badges use DNF;
- actions use Mark as DNF; and
- confirmation copy asks, "Mark this book as Did Not Finish?"

The backend may retain Dropped as its internal shared status value.

Every tracked book appears in the corresponding section of the Books library.
Changing status moves the existing tracking record between sections; it does
not duplicate the book.

Only the current status determines library placement. Historical outcomes do
not duplicate the book across sections. For example, a previously completed
book whose later reread was dropped appears only in DNF; its earlier completion
remains in diary and read history.

Library cards display progress as follows:

- Currently Reading shows current page or percentage;
- Paused shows preserved page or percentage;
- Did Not Finish shows the final progress reached before DNF;
- Read shows complete or 100 percent as a visual completion indicator; and
- To Read shows no progress.

A retrospectively assigned Paused or DNF book with no journey shows no invented
progress. An undated Mark as Read likewise stores no progress despite the Read
card's visual completion indicator.

Custom lists, ownership, favorites, and other organization are independent of
the status sections.

### BK-002: Library membership follows tracking

Assigning any book status creates or updates its one tracking record and places
the book in the library. Removing tracking removes it from the status sections
but must not silently remove custom-list membership or historical diary data.

Direct-only Planning, Paused, DNF, or undated Read with no real journeys or
completion logs may be removed normally. Simple removal is blocked while any
real journey or completion log exists. Deleting all reading history is a
separate destructive action requiring explicit confirmation.

## Direct status assignment

### BK-100: The media-page plus control exposes status actions

The book media page uses the same bottom action rail pattern as movies. The plus
control opens book actions rather than only a diary composer.

It provides actions for:

- Currently Reading;
- Planning, displayed as To Read;
- Paused;
- Did Not Finish;
- Log.

These base options remain visible regardless of current status. The current
status is displayed as selected or disabled rather than as a duplicate action.
When the book is Paused, the menu additionally offers Resume. Mark as Read
remains on the eye rather than appearing in this menu.

### BK-101: Retrospective status assignment does not invent history

Planning, Paused, Dropped, and Completed may be assigned directly so a user can
reconstruct their library after joining Spine.

Direct assignment:

- changes the current library section;
- creates no diary entry;
- invents no progress, start date, end date, or status date; and
- does not claim that the state change happened today.

For example, a user may place a book they abandoned before installing Spine in
the DNF section without creating a dated journey or log.

### BK-102: Mark as Read mirrors undated Mark Watched

Mark as Read records only that the user completed the book at least once before
tracking it in Spine.

It:

- assigns Completed;
- adds the book to the library;
- creates no live journey;
- creates no diary entry;
- records no completion date; and
- does not create multiple historical reads when invoked repeatedly.

Mark as Read is not the interface for entering multiple past reads. It is the
book equivalent of marking a movie watched without logging it.

When a real Currently Reading or Paused journey exists, tapping the eye does
not perform an undated Mark as Read. It opens that journey's completion composer
and follows BK-401. When no live or paused journey exists, the eye performs the
normal undated Mark as Read action.

When the current Read state was produced by an actual completion log, tapping
the filled eye cannot remove it; the UI tells the user to delete that log. The
status-only exception for older completion history is defined in BK-103. If
Read exists only through undated Mark as Read, the filled eye removes that
undated completion and clears its current rating and title-level heart under
the movie cleanup rules.

Removing undated Read restores the exact status that existed before Mark as
Read, including Planning, retrospective Paused, or DNF. If the book was
previously untracked and no journey/history remains, it is removed from the
library.

### BK-103: The eye represents current status, not lifetime history

The eye is filled exactly when the book's current status is Read. Earlier
completed journeys do not keep it filled while a newer current status is
Currently Reading, Paused, DNF, or To Read.

Tapping the empty eye during a live or paused journey follows BK-401. Tapping it
from DNF or another state without a live journey moves the book to Read. If a
completed journey already exists, this status change creates no additional
historical read; otherwise it creates the single undated completion fact under
BK-102.

If an eye, heart, or rating action only changed the current section using older
completion history, tapping the filled eye restores the exact prior status
without deleting or requiring deletion of those older logs. This exception can
undo only that status-only change; it cannot undo an actual journey completion
log.

### BK-104: Currently Reading starts live tracking

Choosing Currently Reading represents reading the book now rather than
reconstructing an old library state. It starts or establishes the live journey
used by progress tracking.

The action saves immediately at zero progress with a start date defaulted to
the user's current local date. The start date may be edited later but cannot be
in the future. It does not require a setup sheet.

Choosing Currently Reading from Completed creates a new journey at zero,
defaults its start date to the user's current local date, and displays it as
Rereading. Previous completions and logs remain unchanged. Completing the new
journey creates another completion log.

Mark as Read remains limited to one undated completion fact and is not used to
create repeated historical reads.

Planning cannot replace a real Currently Reading or Paused journey. While such
a journey exists, Planning is disabled and the UI explains that the user must
Pause or mark the attempt DNF. Planning remains available when no live or paused
journey exists, including when planning another attempt after Completed or DNF.

### BK-105: Direct Log creates a dated completed journey

When no active or paused journey exists, plus to Log opens the standard
completion composer. Saving atomically creates a completed journey and its
dated completion log, then moves the book to Completed. Cancelling or a failed
save changes nothing.

If an earlier completed journey exists, the new completed journey is a reread.
Direct Log and finishing a live journey are the only flows that create
additional completed reads.

### BK-106: Log finishes an existing active or paused journey

When a real Currently Reading or Paused journey exists, plus to Log opens the
completion composer for that journey. Saving completes that same journey and
links its existing progress to the new completion log; it must not create a
separate journey.

A retrospectively assigned Paused book without a journey follows BK-105.

### BK-107: Logging after DNF preserves the closed attempt

When a real DNF journey exists, plus to Log does not convert or delete it.
Saving creates a separate completed journey and completion log under BK-105.

The new completion is a reread only if a journey was Completed before it. A DNF
journey alone does not make a later completion a reread.

## Rating and heart controls

### BK-200: Hearting follows the movie interaction model

Hearting a book implies that the user has read it, just as hearting a movie
implies watched. When no live or paused journey exists, activating the heart:

- uses existing completion history when one exists, otherwise performs undated
  Mark as Read;
- moves the book to Completed;
- sets the title-level heart; and
- may present the same optional rating picker used by the movie rail.

Dismissing the optional rating picker preserves both Read and hearted state.
Unhearting alone removes the title-level heart without removing Read status.

BK-201 defines the behavior when a live or paused journey exists because that
journey cannot be completed without its log.

### BK-201: Hearting a live or paused journey completes through its log

When a real Currently Reading or Paused journey exists, activating the empty
heart opens that journey's completion composer with heart selected. Saving
atomically:

- sets the title-level heart;
- completes the existing journey;
- creates its completion log; and
- moves the book to Completed.

Cancelling or a failed save leaves the journey, status, progress, and heart
unchanged.

### BK-202: Completion ratings and hearts reuse single-weight provenance

Book completion logs follow the current-versus-diary rating and heart source
laws in the Single-Weight Consumption Contract:

- every completion log preserves its own historical rating and heart;
- a newly saved completion log becomes the current rating and heart source;
- editing the source log updates current state;
- directly changing current state decouples it from existing logs;
- editing a non-source log never changes current state, except that a log given
  a new rating fills an empty current rating and becomes its source (SW-108); and
- a later completion log recouples current state.

Deleting a completion log applies both those provenance laws and the journey
restoration laws in this contract.

A rating is either absent or one of ten half-star values from 0.5 through 5.0.
Zero represents no rating rather than an eleventh rated value.

### BK-203: Direct rating implies Read

Directly setting a rating follows the movie rating rule and implies Read.

- With no live or paused journey, it uses existing completion history when one
  exists, otherwise performs undated Mark as Read, then sets the independent
  current rating.
- With a real live or paused journey, it opens that journey's completion
  composer with the rating selected. Saving atomically completes and rates the
  journey; cancelling changes nothing.
- Directly clearing the current rating later does not remove Read status.

## Live journey and progress

### BK-300: Progress belongs to the live journey

The existing book progress system remains the foundation. A live journey owns
the user's page or percentage updates. Pausing, resuming, dropping, and
finishing operate on that same journey rather than creating unrelated tracking
records.

A book may have at most one Currently Reading or Paused journey. A new journey
may begin only after the prior journey is Completed or DNF. Retrospective
Paused status without a journey does not count as a live journey.

### BK-301: Progress UI exposes journey status actions

The update-progress screen provides direct actions to:

- pause the active journey;
- mark it Did Not Finish; and
- finish the book.

Resuming a paused book returns it to Currently Reading and continues its saved
progress rather than resetting it.

The "Update progress ›" line on a book's In progress card on Home and Profile
opens this same screen over Home or Profile, with the same actions and
follow-ups (TV-701).

Pausing saves immediately without confirmation. Marking DNF requires a simple
confirmation, then closes the journey and preserves progress without opening a
completion composer or creating a diary entry.

### BK-302: Final progress automatically presents completion

Saving a progress update at the final page or 100 percent first saves that
progress, then automatically opens the journey's completion composer.

- Saving the composer completes the journey and creates its log.
- Dismissing it leaves the book Currently Reading at its saved final progress
  with no completion log.

Reaching final progress alone never marks the book Completed.

### BK-303: Pausing preserves progress

Pausing moves the book to the Paused library section and preserves its current
progress. Resume returns it to Currently Reading with that progress intact.

If Paused was assigned retrospectively and no journey exists, Resume creates a
new live journey at zero progress and defaults its start date to the user's
current local date.

For a real paused journey, the contextual Resume action and the Currently
Reading option both continue that same journey with preserved progress. Neither
creates a new journey or resets progress. Restarting from the beginning is a
separate confirmed action.

### BK-304: Restart from Beginning preserves the abandoned attempt

Restart from Beginning requires confirmation explaining that existing progress
will remain as a previous attempt. Saving the action atomically:

- closes the current journey as DNF using the user's current local date;
- creates a new Currently Reading journey at zero;
- defaults the new start date to the user's current local date; and
- keeps the book in the Currently Reading library section.

The new journey is a reread only if an earlier journey was Completed. The
abandoned journey remains a historical DNF.

Restart from Beginning is available as a secondary action in the progress UI
for real Currently Reading and Paused journeys. It is not a primary action-rail
button and is unavailable for retrospective status without a journey.

### BK-305: Dropping preserves progress

Marking a live journey Did Not Finish moves the book to the DNF library section
and preserves the progress reached before it was dropped.

The action closes the journey using the user's current local date. That DNF end
date may be edited later under BK-306. A retrospectively assigned DNF status
without a journey remains undated.

DNF permanently closes that journey. Choosing Currently Reading later creates a
new journey at zero progress with a start date defaulting to the user's current
local date. The closed journey and its progress remain historical and are not
carried into the new journey.

The new journey is not a reread unless at least one earlier journey was
Completed.

### BK-306: Journey dates are chronologically valid

- A live journey start date is required and cannot be in the future.
- A progress update cannot predate its journey start.
- A completion or DNF date cannot predate the journey start or its latest
  progress update.
- Editing dates must preserve the same ordering.
- Direct retrospective statuses remain undated.
- Direct Log requires a completion date but no start date.

### BK-307: DNF attempts remain visible as reading history

A closed DNF journey remains visible in a compact Reading History or Attempts
section on the book media page, including its start date, DNF date, and final
progress when those values exist. It is journey history, not a diary log or
completion activity, and is not published as one. Completed journeys continue
to be represented by their diary entries.

### BK-308: A DNF attempt can be deleted explicitly

Reading History allows the user to delete a DNF journey with confirmation.
Deletion removes that journey and all progress updates belonging to it, without
changing any completed journeys or diary entries.

If the deleted journey is the current DNF attempt, the book returns to the
exact library status it held before that journey began, including Read, To
Read, or untracked. If it is an older attempt, the current status does not
change.

### BK-309: An active or paused journey can be deleted explicitly

The progress screen provides a secondary Delete Reading Journey action for a
real Currently Reading or Paused journey. It requires confirmation and deletes
only that journey and its progress updates without first converting it to DNF.

After deletion, the book returns to the exact status it held before the journey
began. Older DNF journeys, completed journeys, and diary entries remain
unchanged.

### BK-310: Undated Read remains manageable after status changes

An undated Mark as Read appears in Reading History as Read with an unknown
date. It remains independently deletable even when a newer journey or status
means the eye no longer represents it.

Deleting it removes only that undated historical completion and updates future
reread derivation. It does not alter the current journey, DNF attempts,
completed journeys, or diary entries. If no newer tracking state or history
remains, the restoration behavior in BK-102 applies.

## Journey completion and diary

### BK-400: Finishing a live journey requires the log composer

When the user finishes a journey, Spine opens the standard book log composer so
the user can supply the completion date, rating, heart, review, and other diary
fields.

Successfully saving the composer must atomically:

- complete the live journey;
- move the book to the Completed library section;
- create exactly one completion diary entry; and
- set current progress to 100 percent and, when total pages are known, the final
  page; and
- keep the completion and diary linked as one event.

The composer:

- requires a completion date defaulted to the user's current local date;
- rejects future dates;
- prefills rating and heart from current title state;
- automatically marks reread when an earlier completed journey exists; and
- saves visible prefilled values even when their controls were untouched.

Reread is derived: the journey is a reread exactly when an earlier completed
journey or undated Mark as Read exists. Prior DNF alone does not count.

The composer still exposes a reread display toggle, defaulted from that derived
fact. Turning it off suppresses the reread circle on that completion log only.
It does not change journey history, derived reread state, counts, statistics, or
future reread defaults.

### BK-401: Journey completion is atomic with log creation

Opening the finish composer does not change tracking. If the user dismisses the
composer or its save fails:

- the journey remains in its original active or paused state;
- the book remains Currently Reading or Paused accordingly;
- progress remains unchanged; and
- no completion diary entry is created.

The journey becomes Completed only when its completion log saves successfully.

### BK-402: Direct completion and journey completion are different flows

- Mark as Read creates undated Completed tracking without a journey or log.
- Finishing a journey creates Completed tracking through a required completion
  log.

The UI and persistence layer must preserve this distinction.

### BK-403: Deleting a live-journey completion log reopens the journey

Deleting the completion log that finished a real journey removes that completed
read and restores the journey's exact pre-finish state:

- a journey finished from Currently Reading returns to Currently Reading;
- a journey finished from Paused returns to Paused; and
- all progress updates remain intact; and
- progress returns to its exact pre-finish position.

The completion log and journey completion are coupled in both directions.

Editing a completion log's date updates its linked journey finish date in the
same transaction. The edit is rejected if the new date predates the journey
start or its latest progress update.

The reopening behavior applies only when that completion is still the latest
tracking event for the book. If newer status or journey history exists,
deleting an older completion removes only its log and linked journey. It must
not reopen the older journey or disturb the newer state. Reread derivation and
statistics are recalculated from the history that remains.

### BK-404: Deleting a direct completion log restores prior tracking

Deleting a completion created by direct Log removes the completed journey that
was created with it. It restores the exact status that existed before logging
only when that completion is still the latest tracking event. If newer tracking
exists, the newer status or journey remains unchanged.

If the book was previously untracked and no other completion or historical
journey remains, deletion removes it from the library. Remaining history or
undated completion evidence continues to determine its status under this
contract.

### BK-405: Library and reading statistics count different things

- The Read library section counts unique books whose current status is Read,
  not the number of times those books were read.
- Every completed journey with a completion log counts as one read.
- An undated Mark as Read counts as one lifetime read but is excluded from
  date-based statistics.
- DNF journeys do not count as reads.
- The completion log's visual reread-circle toggle does not change journey
  counts, reading statistics, or derived reread state.

### BK-406: Completion chronology is based on the user's dates

Completed journeys are ordered by their user-supplied completion dates. Stable
creation order breaks ties when multiple completions share a date. Adding or
editing an out-of-order log recalculates derived reread history and statistics
from that chronology.

An undated Mark as Read is treated as history earlier than every dated
completion. Recalculation never overwrites the per-log visual reread-circle
choice; that toggle remains presentation-only.

## Cross-surface consistency

### BK-500: Status and progress agree everywhere

After a successful mutation, media detail, book library sections, progress UI,
diary, profile, and activity surfaces must agree about:

- current status;
- library membership;
- current progress;
- whether a live journey exists; and
- whether a completion log exists.

Opening the same progress or log editor from different surfaces must not change
its domain behavior.

### BK-501: Mutations cannot leave partial or duplicate history

The backend is authoritative for every transition. Operations that touch a
status, journey, progress, and log together must commit atomically or leave all
of them unchanged. Retried saves and rapid double taps must not create duplicate
journeys or completion logs. The client may choose optimistic or confirmed-save
presentation, but it must reconcile to the returned authoritative state.

## Scope boundary

This contract does not add edition switching, timed reading sessions, reading
goals, or multiple retrospective reads without diary logs. Those features need
separate contracts if introduced later.

## Contract change policy

Numbered laws are normative only where they state resolved behavior. Sentences
that explicitly say behavior remains to be finalized are not implementation
instructions. New decisions from the product interview must update this
contract before implementation begins.
