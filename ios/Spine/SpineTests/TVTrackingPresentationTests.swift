import XCTest
@testable import Spine

/// Card, season, Continue Watching and caught-up lines, Diary titles, the + menu and composer target switching.
@MainActor
final class TVTrackingPresentationTests: XCTestCase {
    /// The user's calendar is Los Angeles; "today" is Monday 2026-10-26 there (TV-202, TV-407).
    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        return calendar
    }

    private var today: Date {
        calendar.date(from: DateComponents(year: 2026, month: 10, day: 26, hour: 21))!
    }

    // MARK: TV-700 Library card lines

    func testTV700WatchingCardNamesUpNextAndProgress() {
        let tv = show(status: "In progress", watched: 13, aired: 20, upNext: (2, 4))
        XCTAssertEqual(line(tv), "S2 E4 next · 13 of 20")

        var unknownCounts = tv
        unknownCounts.watchedCount = nil
        XCTAssertEqual(line(unknownCounts), "S2 E4 next", "No invented count (TV-103).")
    }

    func testTV700WatchingCardWhenCaughtUpOrRewatching() {
        var caughtUp = show(status: "In progress", watched: 6, aired: 6, caughtUp: true)
        caughtUp.nextAiring = TVAiring(seasonNumber: 1, episodeNumber: 7, airDate: "2026-10-29")
        XCTAssertEqual(line(caughtUp), "Caught up · E7 on Thu")
        caughtUp.nextAiring?.airDate = nil
        XCTAssertEqual(line(caughtUp), "Caught up")

        var rewatch = show(status: "In progress", watched: 3, aired: 20, upNext: (1, 3))
        rewatch.rewatch = TVRewatch(scope: .show, startedAt: "2026-10-20T10:00:00Z")
        XCTAssertEqual(line(rewatch), "Rewatching · S1 E3 next")
    }

    func testTV700PlanningPausedDroppedAndCompletedCards() {
        var planning = show(status: "Planning", started: false)
        planning.premiere = TVAiring(seasonNumber: 1, airDate: "2027-03-12")
        XCTAssertEqual(line(planning), "Premieres Mar 12")
        planning.premiere = nil
        XCTAssertNil(line(planning), "Planning names a premiere only while nothing has aired.")

        XCTAssertEqual(line(show(status: "Paused", watched: 10, aired: 20, upNext: (2, 3))), "Paused at S2 E3")
        XCTAssertEqual(line(show(status: "Dropped", watched: 11, aired: 20, upNext: (2, 4))), "Stopped at S2 E4")
        XCTAssertEqual(line(show(status: "Paused", watched: 0, aired: 20, upNext: (1, 1), started: false)), "Paused",
                       "Nothing watched names no episode.")
        XCTAssertEqual(line(show(status: "Dropped", watched: 20, aired: 20)), "Dropped", "No up next names no episode.")

        XCTAssertNil(line(show(status: "Completed", watched: 20, aired: 20)))
        var returning = show(status: "Completed", watched: 20, aired: 20, caughtUp: true)
        returning.nextAiring = TVAiring(seasonNumber: 3, episodeNumber: 1, airDate: "2027-03-12")
        XCTAssertEqual(line(returning), "Caught up · S3 on Mar 12")
        returning.nextAiring = TVAiring(seasonNumber: 3, episodeNumber: 1, airDate: nil)
        XCTAssertEqual(line(returning), "Caught up")
    }

    // MARK: TV-104 Progress

    func testTV104ProgressIsACountNeverAnEpisodeNumber() {
        // E1 to E3 and E5 of ten: "4 of 10", not "5".
        XCTAssertEqual(TVTrackingPresentation.progressText(watched: 4, aired: 10), "4 of 10")
        XCTAssertNil(TVTrackingPresentation.progressText(watched: nil, aired: 10))
        XCTAssertNil(TVTrackingPresentation.progressText(watched: 0, aired: 0))
        let season = TVSeasonState(seasonNumber: 1, started: true, state: .inProgress, watchedCount: 4, airedCount: 10)
        XCTAssertEqual(TVTrackingPresentation.seasonLabel(for: season), "4 of 10")
    }

    // MARK: TV-200 Season labels

    func testTV200SeasonLabels() {
        XCTAssertEqual(label(.watched, watched: 10, aired: 10), "Watched")
        XCTAssertEqual(label(.airing, watched: 6, aired: 6), "Airing")
        XCTAssertEqual(label(.inProgress, watched: 6, aired: 10), "6 of 10")
        XCTAssertEqual(label(.inProgress, watched: 9, aired: 10, gap: 4), "9 of 10 · E4 not watched")
        XCTAssertNil(label(.notStarted, watched: 0, aired: 10))
        XCTAssertNil(label(.unaired, watched: nil, aired: nil))
        XCTAssertNil(label(.unknown, watched: nil, aired: nil), "No stored list: no count (TV-103).")
        // A-11: only the finale watched is "1 of 10", never finished.
        XCTAssertEqual(label(.inProgress, watched: 1, aired: 10), "1 of 10")
    }

    func testTV204SeasonsOfAPausedShowShowItsStatus() {
        XCTAssertEqual(label(.inProgress, watched: 6, aired: 10, status: "Paused"), "Paused · 6 of 10")
        XCTAssertEqual(label(.inProgress, watched: 6, aired: 10, status: "Dropped"), "Dropped · 6 of 10")
        XCTAssertEqual(label(.watched, watched: 10, aired: 10, status: "Completed"), "Watched", "Finished seasons stay Watched.")
        XCTAssertEqual(TVTrackingPresentation.seasonTitle(0), "Specials")
        XCTAssertEqual(TVTrackingPresentation.seasonTitle(2), "Season 2")
    }

    // MARK: TV-202 Caught up

    func testTV202CaughtUpUsesAWeekdayWithinSixDaysElseADate() {
        var tv = show(status: "In progress", watched: 6, aired: 6, caughtUp: true)
        tv.nextAiring = TVAiring(seasonNumber: 1, episodeNumber: 7, airDate: "2026-11-01")
        XCTAssertEqual(caughtUp(tv), "Caught up · E7 on Sun", "Six days ahead reads as a weekday.")
        tv.nextAiring?.airDate = "2026-11-02"
        XCTAssertEqual(caughtUp(tv), "Caught up · E7 on Nov 2", "Seven days ahead reads as a date.")
        tv.nextAiring?.airDate = "2026-10-27"
        XCTAssertEqual(caughtUp(tv), "Caught up · E7 on Tue")

        // A Completed show waiting for its next season names the date, even within six days (TV-202, TV-700).
        var completed = show(status: "Completed", watched: 20, aired: 20, caughtUp: true)
        completed.nextAiring = TVAiring(seasonNumber: 3, episodeNumber: 1, airDate: "2026-10-30")
        XCTAssertEqual(caughtUp(completed), "Caught up · S3 on Oct 30")
        completed.nextAiring = TVAiring(seasonNumber: 3, episodeNumber: nil, airDate: "2027-03-12")
        XCTAssertEqual(caughtUp(completed), "Caught up · S3 on Mar 12")
        completed.nextAiring = nil
        XCTAssertEqual(caughtUp(completed), "Caught up")
    }

    func testTV202DaysCountOnTheUsersCalendar() {
        // 9 p.m. Monday in Los Angeles is already Tuesday in New York; Sunday is six days ahead only in Los Angeles.
        var newYork = Calendar(identifier: .gregorian)
        newYork.timeZone = TimeZone(identifier: "America/New_York")!
        let sunday = TVTrackingPresentation.calendarDate("2026-11-01", calendar: calendar)!
        XCTAssertEqual(TVTrackingPresentation.airDayText(sunday, today: today, calendar: calendar), "Sun")
        let sundayNewYork = TVTrackingPresentation.calendarDate("2026-11-02", calendar: newYork)!
        XCTAssertEqual(TVTrackingPresentation.airDayText(sundayNewYork, today: today, calendar: newYork), "Mon")
        XCTAssertEqual(TVTrackingPresentation.calendarString(today, calendar: calendar), "2026-10-26")
        XCTAssertEqual(TVTrackingPresentation.calendarString(today, calendar: newYork), "2026-10-27")
    }

    // MARK: TV-701 Continue Watching

    func testTV701ContinueWatchingLines() {
        XCTAssertEqual(TVTrackingPresentation.continueWatchingLine(for: show(status: "In progress", upNext: (1, 2))), "S1 E2 next")
        var new = show(status: "In progress", upNext: (3, 1))
        new.upNext?.isNew = true
        XCTAssertEqual(TVTrackingPresentation.continueWatchingLine(for: new), "S3 E1 · New")
        XCTAssertNil(TVTrackingPresentation.continueWatchingLine(for: show(status: "In progress")))

        // Up next, then the count, for VoiceOver: the card itself reads "Update progress ›".
        let watching = show(status: "In progress", watched: 4, aired: 10, upNext: (2, 5))
        XCTAssertEqual(TVTrackingPresentation.continueWatchingLine(for: watching), "S2 E5 next · 4 of 10")
        new.watchedCount = 20
        new.airedCount = 21
        XCTAssertEqual(TVTrackingPresentation.continueWatchingLine(for: new), "S3 E1 · New · 20 of 21")
    }

    func testTV701ACardATickCaughtUpOrFinishedReadsAsItsLibraryCardUntilHomeReloads() {
        var caughtUp = show(status: "In progress", watched: 6, aired: 6, caughtUp: true)
        caughtUp.nextAiring = TVAiring(seasonNumber: 1, episodeNumber: 7, airDate: "2026-10-29")
        XCTAssertEqual(
            TVTrackingPresentation.continueWatchingLine(for: caughtUp, today: today, calendar: calendar),
            "Caught up · E7 on Thu"
        )
        let finished = show(status: "Completed", watched: 10, aired: 10)
        XCTAssertEqual(TVTrackingPresentation.continueWatchingLine(for: finished), "10 of 10")
    }

    func testTV701TheLineOpensUpNextsSeasonAndSaysSo() {
        // The title and "Update progress ›" are one button that opens up next's season; VoiceOver reads where the
        // show stands, which the card no longer shows.
        let tv = show(status: "In progress", watched: 4, aired: 10, upNext: (2, 5))
        XCTAssertEqual(TVTrackingPresentation.continueWatchingAccessibilityLabel(show: "The Crown", tv: tv),
                       "The Crown, update progress, S2 E5 next, 4 of 10")
        var new = show(status: "In progress", watched: 20, aired: 21, upNext: (3, 1))
        new.upNext?.isNew = true
        XCTAssertEqual(TVTrackingPresentation.continueWatchingAccessibilityLabel(show: "The Bear", tv: new),
                       "The Bear, update progress, S3 E1, New, 20 of 21")
        // A card a tick left without up next opens the show, and reads its count.
        XCTAssertEqual(TVTrackingPresentation.continueWatchingAccessibilityLabel(show: "The Crown",
                                                                                  tv: show(status: "Completed", watched: 10, aired: 10)),
                       "The Crown, update progress, 10 of 10")

        // The sheet is titled with the show and season, by the provider's name for it once it has loaded.
        XCTAssertEqual(TVTrackingPresentation.episodeSheetTitle(show: "The Crown", seasonNumber: 2), "The Crown · Season 2")
        XCTAssertEqual(TVTrackingPresentation.episodeSheetTitle(show: "Doctor Who", seasonNumber: 2, seasonName: "Series 2"),
                       "Doctor Who · Series 2")
        XCTAssertEqual(TVTrackingPresentation.episodeSheetTitle(show: "The Crown", seasonNumber: 2, seasonName: "  "),
                       "The Crown · Season 2")
        XCTAssertEqual(TVTrackingPresentation.episodeSheetTitle(show: "The Crown", seasonNumber: 0), "The Crown · Specials")
    }

    // MARK: TV-600 Rewatch lines

    func testTV600RewatchLines() {
        var tv = show(status: "In progress", upNext: (1, 1))
        tv.rewatch = TVRewatch(scope: .show, startedAt: "2026-10-26T10:00:00Z")
        XCTAssertEqual(TVTrackingPresentation.rewatchLine(for: tv), "Rewatching · S1 E1 next")
        tv.upNext = TVEpisodePointer(seasonNumber: 2, episodeNumber: 1)
        tv.rewatch = TVRewatch(scope: .season, seasonNumber: 2, startedAt: "2026-10-26T10:00:00Z")
        XCTAssertEqual(TVTrackingPresentation.rewatchLine(for: tv), "Rewatching S2 · E1 next")
        tv.upNext = nil
        XCTAssertEqual(TVTrackingPresentation.rewatchLine(for: tv), "Rewatching S2")
        XCTAssertNil(TVTrackingPresentation.rewatchLine(for: show(status: "In progress")))
    }

    // MARK: TV-702 Diary titles

    func testTV702DiaryTitles() throws {
        XCTAssertEqual(try title(type: "season", season: 1, title: "Breaking Bad", show: "Breaking Bad"), "Breaking Bad · Season 1")
        XCTAssertEqual(try title(type: "season", season: 0, title: "Breaking Bad", show: "Breaking Bad"), "Breaking Bad · Specials")
        XCTAssertEqual(try title(type: "episode", season: 1, episode: 4, title: "Breaking Bad", show: "Breaking Bad", episodeTitle: "Cancer Man"),
                       "Breaking Bad · S1 E4 · Cancer Man")
        // A Trakt-style episode: the Item's title is the show's and the stored listing has no episode title.
        XCTAssertEqual(try title(type: "episode", season: 1, episode: 4, title: "Breaking Bad", show: nil, episodeTitle: nil),
                       "Breaking Bad · S1 E4")
        XCTAssertEqual(try title(type: "tv", title: "Breaking Bad", show: "Breaking Bad"), "Breaking Bad")
        XCTAssertEqual(try title(type: "movie", title: "Heat", show: nil), "Heat")
    }

    // MARK: TV-005 + menu

    func testTV005ShowMenuListsBaseOptionsWithReasonsAndTheSelectedStatus() throws {
        let tv = try JSONDecoder.api.decode(TVShowState.self, from: Data(TVTrackingModelsTests.showDetailJSON.utf8))
        let items = TVTrackingPresentation.showMenu(for: tv)
        XCTAssertEqual(items.map(\.key), ["watching", "planning", "pause", "drop", "log", "remove"])
        XCTAssertEqual(items.map(\.title), ["Watching", "Planning", "Pause", "Drop", "Log", "Remove from Library"])

        let watching = try XCTUnwrap(items.first { $0.key == "watching" })
        XCTAssertTrue(watching.isSelected, "The current status shows as selected, not as a second action.")
        XCTAssertFalse(watching.isAvailable)
        XCTAssertNil(watching.reason)

        let planning = try XCTUnwrap(items.first { $0.key == "planning" })
        XCTAssertFalse(planning.isAvailable)
        XCTAssertEqual(planning.reason, "You've started this show. Pause or drop it instead.")

        XCTAssertEqual(items.first { $0.key == "pause" }?.command, .action(.pause))
        XCTAssertEqual(items.first { $0.key == "drop" }?.command, .confirmDrop, "Drop asks first (TV-007).")
        XCTAssertEqual(items.first { $0.key == "remove" }?.command, .confirmRemove)
        XCTAssertEqual(items.first { $0.key == "log" }?.command, .log)
        XCTAssertTrue(items.filter { ["pause", "drop", "log", "remove"].contains($0.key) }.allSatisfy(\.isAvailable))
    }

    func testTV005ResumeRewatchAndStopRewatchAppearOnlyWhenListed() {
        // A Paused show has one row, Resume, in place of Watching (TV-005, TV-007).
        var paused = TVShowState(inLibrary: true, status: "Paused", choice: "Paused", selectedAction: "pause", started: true,
                                 availableActions: ["watching", "resume", "drop", "log"])
        var keys = TVTrackingPresentation.showMenu(for: paused).map(\.key)
        XCTAssertEqual(keys, ["resume", "planning", "pause", "drop", "log", "remove"])
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: paused).map(\.title).first, "Resume")
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: paused).first?.command, .action(.resume))
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: paused).first?.systemImage, "play.fill")
        XCTAssertTrue(TVTrackingPresentation.showMenu(for: paused).first { $0.key == "pause" }?.isSelected == true)

        // Without a resume action the row still reads Resume and sends Watching, which continues the same way.
        paused.availableActions = ["watching", "drop"]
        let resume = TVTrackingPresentation.showMenu(for: paused).first
        XCTAssertEqual(resume?.title, "Resume")
        XCTAssertEqual(resume?.command, .action(.watching))
        XCTAssertTrue(resume?.isAvailable == true)
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: paused).filter { $0.title == "Watching" }.count, 0)

        paused.availableActions = ["drop"]
        paused.actionReasons = ["watching": "Nothing has aired yet."]
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: paused).first?.title, "Resume")
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: paused).first?.reason, "Nothing has aired yet.")

        // A Dropped show keeps Watching.
        let dropped = TVShowState(inLibrary: true, status: "Dropped", choice: "Dropped", selectedAction: "drop", started: true,
                                  availableActions: ["watching", "pause", "log"])
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: dropped).first?.title, "Watching")
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: dropped).first?.command, .action(.watching))

        let finished = TVShowState(inLibrary: true, status: "Completed", started: true, finished: true,
                                   availableActions: ["rewatch", "pause", "drop", "log", "remove", "rate"],
                                   actionReasons: ["watching": "You've watched everything that has aired."])
        let finishedItems = TVTrackingPresentation.showMenu(for: finished)
        keys = finishedItems.map(\.key)
        XCTAssertEqual(keys, ["watching", "planning", "pause", "drop", "rewatch", "log", "remove"])
        XCTAssertEqual(finishedItems.first?.reason, "You've watched everything that has aired.")
        XCTAssertFalse(finishedItems.contains { $0.key == "rate" }, "Stars live on the rail, not in the + menu.")

        let rewatching = TVShowState(inLibrary: true, status: "In progress", selectedAction: "watching", started: true,
                                     availableActions: ["stop_rewatch", "pause", "drop", "log"])
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: rewatching).first { $0.key == "stop_rewatch" }?.title, "Stop Rewatching")
        XCTAssertFalse(TVTrackingPresentation.showMenu(for: rewatching).map(\.key).contains("rewatch"))
    }

    func testTV003RemoveFromLibraryIsListedOnlyForAShowInTheLibrary() throws {
        // A show that isn't in the library has nothing to remove: no row, whatever the server sends.
        let untracked = TVShowState(availableActions: ["watching", "planning", "pause", "drop", "log"])
        XCTAssertEqual(TVTrackingPresentation.showMenu(for: untracked).map(\.key),
                       ["watching", "planning", "pause", "drop", "log"])
        let offered = TVShowState(availableActions: ["watching", "planning", "pause", "drop", "log", "remove"],
                                  actionReasons: ["remove": "Nothing to remove."])
        XCTAssertFalse(TVTrackingPresentation.showMenu(for: offered).contains { $0.key == "remove" })

        // In the library: listed, and a blocked removal keeps its disabled row with the server's reason.
        let tracked = TVShowState(inLibrary: true, status: "Planning", selectedAction: "planning",
                                  availableActions: ["watching", "drop", "log", "remove"])
        let remove = try XCTUnwrap(TVTrackingPresentation.showMenu(for: tracked).last)
        XCTAssertEqual(remove.key, "remove")
        XCTAssertTrue(remove.isAvailable)
        XCTAssertEqual(remove.command, .confirmRemove)

        let blocked = TVShowState(inLibrary: true, status: "In progress", selectedAction: "watching", started: true,
                                  availableActions: ["pause", "drop", "log"],
                                  actionReasons: ["remove": "Delete the logs first."])
        let blockedRemove = try XCTUnwrap(TVTrackingPresentation.showMenu(for: blocked).first { $0.key == "remove" })
        XCTAssertFalse(blockedRemove.isAvailable)
        XCTAssertEqual(blockedRemove.reason, "Delete the logs first.")
    }

    func testTV005SeasonAndEpisodeMenus() {
        let season = TVSeasonState(seasonNumber: 2, started: true, state: .watched,
                                   availableActions: ["log_season", "rewatch_season", "rate"])
        let items = TVTrackingPresentation.seasonMenu(for: season)
        XCTAssertEqual(items.map(\.title), ["Log Season", "Rewatch Season"])
        XCTAssertEqual(items.last?.command, .rewatchSeason(2))

        let airing = TVSeasonState(seasonNumber: 3, started: true, state: .airing, availableActions: ["log_season"])
        XCTAssertEqual(TVTrackingPresentation.seasonMenu(for: airing).map(\.key), ["log_season"])

        // During a rewatch Rewatch Season is left out, not shown greyed out (TV-600).
        let blocked = TVSeasonState(seasonNumber: 2, started: true, state: .watched, availableActions: ["log_season"],
                                    actionReasons: ["rewatch_season": "Finish or stop your rewatch first."])
        XCTAssertEqual(TVTrackingPresentation.seasonMenu(for: blocked).map(\.key), ["log_season"])

        let unaired = TVEpisodeState(seasonNumber: 3, episodeNumber: 7, aired: false,
                                     actionReasons: ["log_episode": "This episode hasn't aired yet."])
        let episodeItems = TVTrackingPresentation.episodeMenu(for: unaired)
        XCTAssertEqual(episodeItems.map(\.title), ["Log Episode"])
        XCTAssertEqual(episodeItems.first?.isAvailable, false)
        XCTAssertEqual(episodeItems.first?.reason, "This episode hasn't aired yet.")
    }

    func testSelectedActionKeys() {
        XCTAssertEqual(TVTrackingPresentation.selectedActionKey(forStatus: "In progress"), "watching")
        XCTAssertEqual(TVTrackingPresentation.selectedActionKey(forStatus: "Planning"), "planning")
        XCTAssertEqual(TVTrackingPresentation.selectedActionKey(forStatus: "Paused"), "pause")
        XCTAssertEqual(TVTrackingPresentation.selectedActionKey(forStatus: "Dropped"), "drop")
        XCTAssertNil(TVTrackingPresentation.selectedActionKey(forStatus: "Completed"))
        XCTAssertNil(TVTrackingPresentation.selectedActionKey(forStatus: nil))
    }

    // MARK: Dialog copy

    func testDialogCopy() {
        XCTAssertEqual(TVTrackingPresentation.dropPrompt, "Drop this show? Your progress will be kept.")
        XCTAssertEqual(TVTrackingPresentation.removePrompt(count: 13), "Remove this show? Your 13 watched episodes will be unmarked.")
        XCTAssertEqual(TVTrackingPresentation.removePrompt(count: 1), "Remove this show? Your 1 watched episode will be unmarked.")
        XCTAssertEqual(TVTrackingPresentation.removePrompt(count: 0), "Remove this show?")

        XCTAssertEqual(TVTrackingPresentation.eyePrompt(for: TVEyeState(filled: false, tap: .markConfirm, count: 62,
                                                                       prompt: "Mark the rest of this rewatch watched?")),
                       "Mark the rest of this rewatch watched?")
        XCTAssertEqual(TVTrackingPresentation.eyePrompt(for: TVEyeState(filled: false, tap: .markConfirm, count: 62)),
                       "Mark all 62 aired episodes watched?")
        XCTAssertEqual(TVTrackingPresentation.eyePrompt(for: TVEyeState(filled: true, tap: .unmarkConfirm, count: 62)),
                       "Mark all 62 episodes unwatched?")
        XCTAssertNil(TVTrackingPresentation.eyePrompt(for: TVEyeState(filled: false, tap: .mark, count: 10)))
        XCTAssertEqual(TVTrackingPresentation.eyeMessage(for: TVEyeState(filled: false, tap: .nothingAired, message: "S1 premieres Mar 12")),
                       "S1 premieres Mar 12")
        XCTAssertEqual(TVTrackingPresentation.eyeMessage(for: TVEyeState(filled: true, tap: .blocked)), "Delete the logs first.")

        XCTAssertEqual(TVTrackingPresentation.markUpToPrompt(for: TVEpisodeState(seasonNumber: 2, episodeNumber: 4,
                                                                                 markUpToPrompt: "Mark S1 E1–S2 E4 watched?")),
                       "Mark S1 E1–S2 E4 watched?")
        XCTAssertEqual(TVTrackingPresentation.deleteLogPrompt(unmarksCount: 7),
                       "Delete this log? 7 episodes it marked watched will be unmarked.")
        XCTAssertNil(TVTrackingPresentation.deleteLogPrompt(unmarksCount: 0))
    }

    // MARK: TV-404 Composer target switching

    func testTV404SwitchingTargetReplacesOnlyUntouchedDefaults() throws {
        let composer = try XCTUnwrap(JSONDecoder.api.decode(TVShowState.self, from: Data(TVTrackingModelsTests.showDetailJSON.utf8)).composer)
        var draft = try XCTUnwrap(TVComposerDraft(composer: composer))
        XCTAssertEqual(draft.target.id, "season:1")
        XCTAssertEqual(draft.ratingSteps, 8)
        XCTAssertTrue(draft.liked)
        XCTAssertEqual(draft.date, "2026-10-26")

        draft.selectTarget(id: "show")
        XCTAssertEqual(draft.target.id, "show")
        XCTAssertEqual(draft.ratingSteps, 9, "Untouched stars take the show's.")
        XCTAssertFalse(draft.liked, "Untouched heart takes the show's.")

        draft.ratingSteps = 6
        draft.date = "2026-10-20"
        draft.selectTarget(id: "season:2")
        XCTAssertEqual(draft.ratingSteps, 6, "Changed stars stay.")
        XCTAssertEqual(draft.date, "2026-10-20", "A changed date stays.")
        XCTAssertFalse(draft.liked)
        XCTAssertEqual(draft.editedFields, [.rating, .date])

        draft.selectTarget(id: "season:99")
        XCTAssertEqual(draft.target.id, "season:2", "An unknown target changes nothing.")
    }

    func testTV404RewatchDefaultFollowsTheTargetUntilChanged() throws {
        let composer = TVComposer(defaultTarget: "season:2", targets: [
            TVComposerTarget(id: "season:1", level: .season, seasonNumber: 1, title: "Season 1", date: "2026-10-26", isRewatch: false),
            TVComposerTarget(id: "season:2", level: .season, seasonNumber: 2, title: "Season 2", date: "2026-10-24", isRewatch: true),
        ])
        var draft = try XCTUnwrap(TVComposerDraft(composer: composer))
        XCTAssertTrue(draft.isRewatch)
        XCTAssertEqual(draft.date, "2026-10-24", "The finish composer's date is its tick's day (TV-403).")
        draft.selectTarget(id: "season:1")
        XCTAssertFalse(draft.isRewatch)
        XCTAssertEqual(draft.date, "2026-10-26")
        draft.isRewatch = true
        draft.selectTarget(id: "season:2")
        draft.selectTarget(id: "season:1")
        XCTAssertTrue(draft.isRewatch)
        XCTAssertNil(TVComposerDraft(composer: TVComposer(defaultTarget: "show", targets: [])))

        draft.setDate(TVTrackingPresentation.calendarDate("2026-10-21", calendar: calendar)!, calendar: calendar)
        XCTAssertEqual(draft.date, "2026-10-21")
        XCTAssertEqual(draft.dateValue(calendar: calendar), TVTrackingPresentation.calendarDate("2026-10-21", calendar: calendar))
        XCTAssertEqual(TVComposerDraft.ratingSteps(from: "10.0"), 10)
        XCTAssertEqual(TVComposerDraft.ratingSteps(from: nil), 0)
    }

    func testComposerTargetsSaveAgainstTheirLevel() {
        let show = MediaRef(itemId: 3, source: "tmdb", mediaType: "tv", mediaId: "1399", seasonNumber: nil, episodeNumber: nil)
        let season = TVComposerTarget(id: "season:0", level: .season, seasonNumber: 0, title: "Specials", date: "2026-10-26")
        XCTAssertEqual(season.ref(show: show), MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 0, episodeNumber: nil))
        let episode = TVComposerTarget(id: "episode:1:4", level: .episode, seasonNumber: 1, episodeNumber: 4, title: "Fly", date: "2026-10-26")
        XCTAssertEqual(episode.ref(show: show).mediaType, "episode")
        XCTAssertEqual(episode.ref(show: show).episodeNumber, 4)
        let whole = TVComposerTarget(id: "show", level: .show, title: "Whole show", date: "2026-10-26")
        XCTAssertEqual(whole.ref(show: show).mediaType, "tv")
    }

    // MARK: Helpers

    private func show(
        status: String?,
        watched: Int? = nil,
        aired: Int? = nil,
        upNext: (Int, Int)? = nil,
        caughtUp: Bool = false,
        started: Bool = true
    ) -> TVShowState {
        TVShowState(
            version: 1,
            inLibrary: status != nil,
            status: status,
            started: started,
            finished: status == "Completed",
            caughtUp: caughtUp,
            watchedCount: watched,
            airedCount: aired,
            upNext: upNext.map { TVEpisodePointer(seasonNumber: $0.0, episodeNumber: $0.1) }
        )
    }

    private func line(_ tv: TVShowState) -> String? {
        TVTrackingPresentation.cardLine(for: tv, today: today, calendar: calendar)
    }

    private func caughtUp(_ tv: TVShowState) -> String {
        TVTrackingPresentation.caughtUpLine(for: tv, today: today, calendar: calendar)
    }

    private func label(
        _ state: TVSeasonProgress,
        watched: Int?,
        aired: Int?,
        gap: Int? = nil,
        status: String? = "In progress"
    ) -> String? {
        TVTrackingPresentation.seasonLabel(for: TVSeasonState(
            seasonNumber: 1, started: state != .notStarted, status: status, state: state,
            watchedCount: watched, airedCount: aired, firstGapEpisode: gap
        ))
    }

    private func title(
        type: String,
        season: Int? = nil,
        episode: Int? = nil,
        title: String,
        show: String?,
        episodeTitle: String? = nil
    ) throws -> String {
        var ref: [String: Any] = ["source": "tmdb", "media_type": type, "media_id": "1396"]
        ref["season_number"] = season
        ref["episode_number"] = episode
        var media: [String: Any] = ["ref": ref, "title": title]
        media["show_title"] = show
        media["episode_title"] = episodeTitle
        let data = try JSONSerialization.data(withJSONObject: media)
        return TVTrackingPresentation.diaryTitle(for: try JSONDecoder.api.decode(DiaryMedia.self, from: data))
    }
}
