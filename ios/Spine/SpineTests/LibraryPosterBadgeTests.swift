import XCTest
@testable import Spine

@MainActor
final class LibraryPosterBadgeTests: XCTestCase {
    func testABookShowsWhereYouStoppedOnlyWhileStartedAndUnfinished() {
        let pages = ProgressState(kind: "pages", value: 230, max: 500, unit: "page")

        XCTAssertEqual(badge(book(status: "In progress", progress: pages)), "230/500 pages")
        XCTAssertEqual(badge(book(status: "Paused", progress: pages)), "230/500 pages")
        XCTAssertEqual(badge(book(status: "Dropped", progress: pages)), "230/500 pages")
        XCTAssertEqual(
            badge(book(status: "In progress", progress: ProgressState(
                kind: "percentage", value: Decimal(string: "28.7"), max: 100, unit: "percent"
            ))),
            "28.7%"
        )
    }

    func testABookWithNoProgressHasNoBadge() {
        let none = ProgressState(kind: "pages", value: 0, max: 500, unit: "page")

        XCTAssertNil(badge(book(status: "In progress", progress: none)))
        XCTAssertNil(badge(book(status: "Paused", progress: none)))
        XCTAssertNil(badge(book(status: "Dropped", progress: none)))
    }

    func testAPlannedOrFinishedBookHasNoBadge() {
        XCTAssertNil(badge(book(status: "Planning", progress: ProgressState(
            kind: "pages", value: 0, max: 500, unit: "page"
        ))))
        XCTAssertNil(badge(book(status: "Completed", progress: ProgressState(
            kind: "pages", value: 500, max: 500, unit: "page"
        ))))
        XCTAssertNil(badge(book(status: "Completed", progress: ProgressState(
            kind: "percentage", value: 100, max: 100, unit: "percent"
        ))))
        XCTAssertNil(badge(book(status: "Completed", progress: nil)), "No Complete badge either")
    }

    func testAGameShowsWhatWasSavedWhileStartedAndUnfinished() {
        XCTAssertEqual(badge(game(status: "In progress", minutes: 750, percentage: 50)), "50% · 12h 30m")
        XCTAssertEqual(badge(game(status: "Paused", minutes: 720, percentage: nil)), "12h")
        XCTAssertEqual(badge(game(status: "Dropped", minutes: nil, percentage: 50)), "50%")
        XCTAssertNil(badge(game(status: "In progress", minutes: nil, percentage: nil)))
    }

    func testAGameLeavesOutASavedZero() {
        XCTAssertNil(badge(game(status: "In progress", minutes: 0, percentage: 0)))
        XCTAssertEqual(badge(game(status: "In progress", minutes: 0, percentage: 50)), "50%")
        XCTAssertEqual(badge(game(status: "In progress", minutes: 720, percentage: 0)), "12h")
    }

    func testAPlannedOrFinishedGameHasNoBadge() {
        XCTAssertNil(badge(game(status: "Planning", minutes: nil, percentage: nil)))
        XCTAssertNil(badge(game(status: "Completed", minutes: 2520, percentage: 100)))
    }

    func testTypesWithoutTrackingContractsHaveNoBadge() {
        for mediaType in ["movie", "music"] {
            let item = Spine.LibraryItem(
                media: summary(mediaType),
                tracking: TrackingState(
                    trackingId: 1,
                    status: "In progress",
                    rating: nil,
                    progress: ProgressState(kind: "count", value: 12, max: 24, unit: "chapter"),
                    repeats: nil,
                    startDate: nil,
                    endDate: nil,
                    notes: nil,
                    updatedAt: nil
                )
            )
            XCTAssertNil(badge(item), mediaType)
        }
    }

    // MC-006: the place, with the total when there is one, only while started and unfinished.
    func testAMangaOrAComicShowsWhereYouStoppedOnlyWhileStartedAndUnfinished() {
        for status in ["In progress", "Paused", "Dropped"] {
            XCTAssertEqual(badge(series("manga", status: status, value: 120, max: 380, unit: "chapter")), "120/380 chapters", status)
            XCTAssertEqual(badge(series("manga", status: status, value: 120, max: nil, unit: "chapter")), "120 chapters", status)
            XCTAssertEqual(badge(series("comic", status: status, value: 12, max: 54, unit: "issue")), "12/54 issues", status)
            XCTAssertNil(badge(series("manga", status: status, value: 0, max: 380, unit: "chapter")), "A place of zero: \(status)")
        }
        XCTAssertEqual(badge(series("manga", status: "In progress", value: 1, max: nil, unit: "chapter")), "1 chapter")
        for status in ["Planning", "Completed"] {
            XCTAssertNil(badge(series("manga", status: status, value: 380, max: 380, unit: "chapter")), status)
            XCTAssertNil(badge(series("comic", status: status, value: 54, max: 54, unit: "issue")), status)
        }
    }

    // MC-006: the grid's small poster leaves out the unit, on one line. Books and Home keep their words.
    func testTheGridBadgeOfAMangaOrAComicHasNoUnit() {
        func grid(_ item: Spine.LibraryItem) -> String? { LibraryPosterBadge.gridText(for: item) }
        XCTAssertEqual(grid(series("manga", status: "In progress", value: 120, max: 380, unit: "chapter")), "120/380")
        XCTAssertEqual(grid(series("comic", status: "Paused", value: 12, max: 54, unit: "issue")), "12/54")
        XCTAssertEqual(grid(series("manga", status: "Dropped", value: 120, max: nil, unit: "chapter")), "Ch. 120")
        XCTAssertEqual(grid(series("comic", status: "In progress", value: 12, max: nil, unit: "issue")), "Iss. 12")
        XCTAssertNil(grid(series("manga", status: "In progress", value: 0, max: 380, unit: "chapter")), "A place of zero")
        XCTAssertNil(grid(series("manga", status: "Completed", value: 380, max: 380, unit: "chapter")), "Finished")
        let book = series("book", status: "In progress", value: 120, max: 380, unit: "page")
        XCTAssertEqual(grid(book), badge(book))
        XCTAssertEqual(badge(series("manga", status: "In progress", value: 120, max: 380, unit: "chapter")), "120/380 chapters")
    }

    private func series(_ mediaType: String, status: String, value: Decimal, max: Decimal?, unit: String) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: summary(mediaType),
            tracking: TrackingState(
                trackingId: 1,
                status: status,
                rating: nil,
                progress: ProgressState(kind: "count", value: value, max: max, unit: unit),
                repeats: nil,
                startDate: nil,
                endDate: nil,
                notes: nil,
                updatedAt: nil
            )
        )
    }

    private func badge(_ item: Spine.LibraryItem) -> String? {
        LibraryPosterBadge.text(for: item)
    }

    private func summary(_ mediaType: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: "test",
                mediaType: mediaType,
                mediaId: "poster-badge",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Title",
            posterUrl: nil,
            posterOrientation: .portrait
        )
    }

    private func book(status: String, progress: ProgressState?) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: summary("book"),
            tracking: TrackingState(
                trackingId: 1,
                status: status,
                rating: nil,
                progress: progress,
                repeats: nil,
                startDate: nil,
                endDate: nil,
                notes: nil,
                updatedAt: nil
            )
        )
    }

    private func game(status: String, minutes: Int?, percentage: Int?) -> Spine.LibraryItem {
        let playthrough = GamePlaythroughState(
            id: 1, status: status, origin: "live", startDate: nil, endDate: nil,
            totalMinutes: minutes, percentage: percentage, completionDiaryEntryId: nil, isReplay: false
        )
        return Spine.LibraryItem(
            media: summary("game"),
            tracking: TrackingState(
                trackingId: 1,
                status: status,
                rating: nil,
                progress: nil,
                repeats: nil,
                startDate: nil,
                endDate: nil,
                notes: nil,
                updatedAt: nil,
                game: GameTrackingState(
                    currentPlaythrough: playthrough, playHistory: [], undatedCompletion: nil, statusSource: nil,
                    completionDates: [], completedPlaythroughCount: 0, lifetimeCompletionCount: 0, isReplaying: false,
                    canRemoveTracking: false, availableActions: [], actionReasons: [:], importedLifetimeMinutes: nil,
                    importedLifetimeSource: nil
                )
            )
        )
    }
}
