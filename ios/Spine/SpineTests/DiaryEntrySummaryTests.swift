import XCTest
@testable import Spine

final class DiaryEntrySummaryTests: XCTestCase {
    func testShowsStarCountHeartAndReviewMark() throws {
        let summary = DiaryEntrySummary(entry: try diaryEntry(
            mediaType: "music",
            rating: "3.0",
            liked: true,
            review: "Loved the second side."
        ))

        XCTAssertEqual(summary.stars, "3")
        XCTAssertEqual(summary.starSymbols, ["star.fill", "star.fill", "star.fill"])
        XCTAssertTrue(summary.liked)
        XCTAssertTrue(summary.hasReview)
        XCTAssertEqual(summary.accessibilityLabel, "Rated 3 out of 5 stars, Liked, Has review")
    }

    func testEveryTypeReadsStars() throws {
        let summary = DiaryEntrySummary(entry: try diaryEntry(mediaType: "tv", rating: "3.5"))

        XCTAssertEqual(summary.stars, "3.5")
        XCTAssertEqual(summary.starSymbols, ["star.fill", "star.fill", "star.fill", "star.leadinghalf.filled"])
    }

    func testRatingsBetweenHalfStarsDrawTheNearest() throws {
        let summary = DiaryEntrySummary(entry: try diaryEntry(mediaType: "anime", rating: "3.75"))

        XCTAssertEqual(summary.stars, "3.8")
        XCTAssertEqual(summary.starSymbols, ["star.fill", "star.fill", "star.fill", "star.fill"])
    }

    func testReviewTitleAloneCountsAsReview() throws {
        let summary = DiaryEntrySummary(entry: try diaryEntry(reviewTitle: "A slow burn"))

        XCTAssertTrue(summary.hasReview)
    }

    func testBlankEntryShowsNothing() throws {
        let summary = DiaryEntrySummary(entry: try diaryEntry(reviewTitle: "  ", review: "\n"))

        XCTAssertNil(summary.stars)
        XCTAssertEqual(summary.starSymbols, [])
        XCTAssertFalse(summary.hasReview)
        XCTAssertTrue(summary.isEmpty)
    }

    private func diaryEntry(
        mediaType: String = "movie",
        rating: String? = nil,
        liked: Bool = false,
        reviewTitle: String = "",
        review: String = ""
    ) throws -> DiaryEntry {
        let object: [String: Any] = [
            "id": 1,
            "user": ["id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": NSNull()],
            "media": [
                "ref": [
                    "item_id": 101, "source": "tmdb", "media_type": mediaType, "media_id": "1",
                    "season_number": NSNull(), "episode_number": NSNull(),
                ],
                "title": "Entry",
                "image_url": NSNull(),
            ],
            "consumed_at": "2026-06-20T12:00:00Z",
            "rating": rating.map { $0 as Any } ?? NSNull(),
            "review_title": reviewTitle,
            "review": review,
            "contains_spoilers": false,
            "liked": liked,
            "is_rewatch": false,
            "tags": ["vinyl", "late night"],
            "visibility": "public",
            "like_count": 0,
            "viewer_has_liked": false,
            "created_at": "2026-06-20T12:00:00Z",
            "updated_at": "2026-06-20T12:00:00Z",
        ]
        return try JSONDecoder.api.decode(DiaryEntry.self, from: JSONSerialization.data(withJSONObject: object))
    }
}
