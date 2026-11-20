import XCTest
@testable import Spine

/// A game's + menu, the rail's Planning bookmark and the slot it shares with the stars: what each state offers.
@MainActor
final class TrackMenuPresentationTests: XCTestCase {
    private func game(_ status: String, canRemove: Bool = true) -> GameTrackingState {
        var state = GameTrackingState.untracked
        state.currentPlaythrough = GamePlaythroughState(
            id: 1, status: status, origin: "live", startDate: "2026-11-01", endDate: nil,
            totalMinutes: nil, percentage: nil, completionDiaryEntryId: nil, isReplay: false
        )
        state.canRemoveTracking = canRemove
        return state
    }

    private func ref(_ mediaType: String, season: Int? = nil) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: mediaType, mediaId: "1", seasonNumber: season, episodeNumber: nil)
    }

    // Before a playthrough starts any shelf can be picked, so a game played before Spine can go straight to Dropped.
    func testGameMenuBeforeItStarts() {
        let untracked = GameMenu(status: nil, game: nil)
        XCTAssertEqual(untracked.rows.map(\.command), [.planning, .playing, .paused, .dropped(asks: false), .log])
        XCTAssertFalse(untracked.offersRemoval, "Nothing to remove")
        let planned = GameMenu(status: "Planning", game: .untracked)
        XCTAssertEqual(planned.rows.map(\.command), [.planning, .playing, .paused, .dropped(asks: false), .log])
        XCTAssertEqual(planned.rows.filter(\.isSelected).map(\.command), [.planning])
        XCTAssertTrue(planned.offersRemoval)
    }

    func testGameMenuDuringAPlaythrough() {
        let playing = GameMenu(status: "In progress", game: game("In progress", canRemove: false))
        XCTAssertEqual(playing.rows.map(\.command), [.playing, .paused, .dropped(asks: true), .log, .updateProgress])
        XCTAssertEqual(playing.rows.filter(\.isSelected).map(\.command), [.playing])
        XCTAssertFalse(playing.offersRemoval)
        XCTAssertEqual(playing.notes.count, 2, "Planning and a Remove that can't be used are lines under the menu, not rows")

        let paused = GameMenu(status: "Paused", game: game("Paused", canRemove: false))
        XCTAssertEqual(paused.rows.map(\.command), [.resume, .paused, .dropped(asks: true), .log, .updateProgress])
        XCTAssertEqual(paused.rows.filter(\.isSelected).map(\.command), [.paused])
    }

    func testGameMenuOnceFinished() {
        let completed = GameMenu(status: "Completed", game: game("Completed"))
        XCTAssertEqual(completed.rows.map(\.command), [.planning, .playing, .paused, .dropped(asks: false), .log, .updateProgress],
                       "Replay starts a new playthrough")
        XCTAssertFalse(completed.rows.contains(where: \.isSelected), "Completed has no row; the title says it")
        XCTAssertTrue(completed.offersRemoval)
        XCTAssertEqual(completed.notes, [])
    }

    // The slot after the eye is the same on every type: the stars once the title is finished or rated, else the bookmark
    // while it can be planned, else nothing.
    func testTheRailSlotAfterTheEye() {
        XCTAssertEqual(RailOpinionSlot.make(finished: false, rated: false, bookmark: .add), .bookmark)
        XCTAssertEqual(RailOpinionSlot.make(finished: false, rated: false, bookmark: .remove), .bookmark)
        XCTAssertEqual(RailOpinionSlot.make(finished: true, rated: false, bookmark: .hidden), .stars)
        XCTAssertEqual(RailOpinionSlot.make(finished: false, rated: true, bookmark: .add), .stars, "A rating stays in view")
        XCTAssertEqual(RailOpinionSlot.make(finished: false, rated: false, bookmark: .hidden), .none, "Started, unfinished")
    }

    // The bookmark shows until the title is started, filled while it's in Planning.
    func testTheBookmarkShowsUntilItsStarted() {
        let movie = ref("movie")
        XCTAssertEqual(PlanningBookmark.state(for: movie, viewer: .untracked), .add)
        XCTAssertEqual(PlanningBookmark.state(for: movie, viewer: UserMediaState(isTracked: true, status: "Planning")), .remove)
        XCTAssertEqual(PlanningBookmark.state(for: movie, viewer: UserMediaState(isTracked: true, status: "Completed")), .hidden)
        XCTAssertEqual(PlanningBookmark.state(for: ref("book"), viewer: UserMediaState(isTracked: true, status: "In progress")), .hidden)
        XCTAssertEqual(PlanningBookmark.state(for: ref("game"), viewer: .untracked), .add)
    }

    // A show takes Planning until anything is ticked or logged, which the server says by offering it.
    func testAShowsBookmarkFollowsTheServer() {
        let show = ref("tv")
        var viewer = UserMediaState.untracked
        viewer.tv = TVShowState(availableActions: ["watching", "planning", "log"])
        XCTAssertEqual(PlanningBookmark.state(for: show, viewer: viewer), .add)
        viewer.tv = TVShowState(inLibrary: true, status: "Planning", selectedAction: "planning",
                                availableActions: ["watching", "log", "remove"])
        XCTAssertEqual(PlanningBookmark.state(for: show, viewer: viewer), .remove)
        viewer.tv = TVShowState(inLibrary: true, status: "In progress", selectedAction: "watching", started: true,
                                availableActions: ["pause", "drop", "log"],
                                actionReasons: ["planning": "You've started this show. Pause or drop it instead."])
        XCTAssertEqual(PlanningBookmark.state(for: show, viewer: viewer), .hidden)

        // Seasons and episodes leave Planning to the show's page.
        XCTAssertEqual(PlanningBookmark.state(for: ref("season", season: 1), viewer: viewer), .hidden)
    }
}
