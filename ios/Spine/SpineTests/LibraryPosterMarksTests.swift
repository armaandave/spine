import XCTest
@testable import Spine

/// The pill on a finished title's Library grid poster: its stars and your heart (SW-701).
@MainActor
final class LibraryPosterMarksTests: XCTestCase {
    func testAFinishedTitleShowsItsStarsAndHeart() {
        let marks = LibraryPosterMarks.marks(for: item("movie", status: "Completed", rating: "4.5", liked: true))

        XCTAssertEqual(marks?.stars, [.star, .star, .star, .star, .halfStar])
        XCTAssertEqual(marks?.liked, true)
    }

    func testAHeartWithNoStarsStillShows() {
        let marks = LibraryPosterMarks.marks(for: item("book", status: "Completed", rating: nil, liked: true))

        XCTAssertEqual(marks?.stars, [])
        XCTAssertEqual(marks?.liked, true)
    }

    func testAFinishedTitleWithNoStarsAndNoHeartHasNoPill() {
        XCTAssertNil(LibraryPosterMarks.marks(for: item("movie", status: "Completed", rating: nil, liked: false)))
    }

    func testOnlyAFinishedTitleHasThePill() {
        for status in ["Planning", "In progress", "Paused", "Dropped"] {
            XCTAssertNil(LibraryPosterMarks.marks(for: item("game", status: status, rating: "4", liked: true)), status)
        }
    }

    // A show's and an anime's row carries no heart of its own; yours is on your state for the title.
    func testAShowsHeartComesFromYourState() {
        for mediaType in ["tv", "anime"] {
            let marks = LibraryPosterMarks.marks(for: item(
                mediaType,
                status: "Completed",
                rating: "5",
                liked: nil,
                yourHeart: true
            ))

            XCTAssertEqual(marks?.stars.count, 5, mediaType)
            XCTAssertEqual(marks?.liked, true, mediaType)
        }
    }

    private func item(
        _ mediaType: String,
        status: String,
        rating: String?,
        liked: Bool?,
        yourHeart: Bool = false
    ) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(
                ref: MediaRef(
                    itemId: nil,
                    source: "test",
                    mediaType: mediaType,
                    mediaId: "poster-marks",
                    seasonNumber: nil,
                    episodeNumber: nil
                ),
                title: "Title",
                posterUrl: nil,
                posterOrientation: .portrait,
                userState: UserMediaState(isTracked: true, status: status, rating: rating, hasLiked: yourHeart)
            ),
            tracking: TrackingState(
                trackingId: 1,
                status: status,
                rating: rating,
                progress: nil,
                repeats: nil,
                startDate: nil,
                endDate: nil,
                notes: nil,
                updatedAt: nil,
                liked: liked
            )
        )
    }
}
