import SwiftUI
import XCTest
@testable import Spine

/// The ratings chart's numbers and the friends' glass cards' words and marks. Layout and styling aren't tested.
@MainActor
final class RatingsGlowChartTests: XCTestCase {
    private typealias G = RatingsChartGeometry
    private let width: CGFloat = 374

    // MARK: Chart geometry

    func testBucketsLandOnTheirHalfStarSteps() {
        let counts = G.counts(from: [
            RatingDistributionBucket(rating: "0.5", count: 2),
            RatingDistributionBucket(rating: "4.5", count: 7),
            RatingDistributionBucket(rating: "4.4", count: 1),
            RatingDistributionBucket(rating: "5", count: 3),
        ])
        XCTAssertEqual(counts, [2, 0, 0, 0, 0, 0, 0, 0, 8, 3])
    }

    func testTheBusiestStepReachesTheAmplitudeAndEmptyStepsSitOnTheFloor() {
        let ys = G.ys(counts: [0, 5, 10, 0, 0, 0, 0, 0, 0, 0])
        XCTAssertEqual(ys[0], G.floorY)
        XCTAssertEqual(ys[1], G.floorY - G.amplitude / 2, accuracy: 0.001)
        XCTAssertEqual(ys[2], G.floorY - G.amplitude, accuracy: 0.001)
    }

    func testStepsSpanTheWidthWithAnInsetEitherSide() {
        XCTAssertEqual(G.x(step: 0, chartWidth: width), 14)
        XCTAssertEqual(G.x(step: 9, chartWidth: width), 360, accuracy: 0.001)
        XCTAssertEqual(G.step(atX: -50, chartWidth: width), 0)
        XCTAssertEqual(G.step(atX: 999, chartWidth: width), 9)
        XCTAssertEqual(G.step(atX: G.x(step: 4, chartWidth: width) + 5, chartWidth: width), 4)
    }

    func testTheAverageSitsBetweenItsSteps() {
        XCTAssertEqual(G.averageX(stars: 0.5, chartWidth: width), 14)
        XCTAssertEqual(G.averageX(stars: 5, chartWidth: width), 360, accuracy: 0.001)
        let half = (G.x(step: 7, chartWidth: width) + G.x(step: 8, chartWidth: width)) / 2
        XCTAssertEqual(G.averageX(stars: 4.25, chartWidth: width), half, accuracy: 0.001)
    }

    func testTheCurveNeverOvershootsAndStaysFlatWhereNobodyRated() {
        let ys = G.ys(counts: [0, 0, 1, 0, 0, 0, 0, 0, 40, 0])
        let slopes = G.slopes(ys)
        XCTAssertEqual(slopes[0], 0)
        XCTAssertEqual(slopes[2], 0, "a peak is flat")
        var x: CGFloat = 14
        while x <= 360 {
            let y = G.curveY(at: x, ys: ys, slopes: slopes, chartWidth: width)
            XCTAssertLessThanOrEqual(y, G.floorY + 0.001, "never dips under the floor at \(x)")
            XCTAssertGreaterThanOrEqual(y, ys.min()! - 0.001, "never rises over the busiest step at \(x)")
            x += 3
        }
        XCTAssertEqual(G.curveY(at: G.x(step: 8, chartWidth: width), ys: ys, slopes: slopes, chartWidth: width), ys[8], accuracy: 0.001)
    }

    func testStepLabelsAreWholeOrHalfStars() {
        XCTAssertEqual(G.label(step: 0), "0.5")
        XCTAssertEqual(G.label(step: 7), "4")
        XCTAssertEqual(G.label(step: 8), "4.5")
    }

    func testPhotosAtOneStepStackUpwardInOrder() {
        XCTAssertEqual(G.stackSlots(steps: [8, 3, 8, 8, 3]), [0, 0, 1, 2, 1])
    }

    // MARK: People on the curve

    func testYouGoFirstAndOnlyWhenYouHaveStars() {
        let friends = [friend(id: 1, rating: "4.5"), friend(id: 2, rating: nil), friend(id: 3, rating: "2")]
        let people = RatingChartPerson.people(viewerRating: "4.5", viewerAvatarURL: "me.png", friends: friends)
        XCTAssertEqual(people.map(\.id), ["you", "friend-1", "friend-3"])
        XCTAssertEqual(people.map(\.halfSteps), [9, 9, 4])
        XCTAssertEqual(people.map(\.isYou), [true, false, false])
        XCTAssertEqual(RatingChartPerson.people(viewerRating: nil, viewerAvatarURL: nil, friends: friends).map(\.id), ["friend-1", "friend-3"])
    }

    // MARK: Friend cards

    func testStarsAreGlyphsWholeThenHalf() {
        XCTAssertEqual(friend(rating: "4.5").cardMarks.stars, [.star, .star, .star, .star, .halfStar])
        XCTAssertEqual(friend(rating: "0.5").cardMarks.stars, [.halfStar])
        XCTAssertEqual(friend(rating: "3").cardMarks.stars, [.star, .star, .star])
        XCTAssertTrue(friend(rating: nil).cardMarks.isEmpty)
    }

    func testHeartRewatchAndSpoilerEyeJoinTheMarks() {
        let marks = friend(rating: "5", liked: true, isRewatch: true, review: "He dies.", containsSpoilers: true).cardMarks
        XCTAssertEqual(marks.stars.count, 5)
        XCTAssertTrue(marks.liked)
        XCTAssertTrue(marks.rewatch)
        XCTAssertTrue(marks.spoiler)
        XCTAssertFalse(friend(rating: "5", review: "Fine.").cardMarks.spoiler)
    }

    func testTheSpoilerMarkIsASoftRedEye() {
        XCTAssertEqual(FeedMark.spoiler.symbolName, "eye.slash.fill")
        XCTAssertEqual(FeedMark.spoiler.color(for: .dark), HomeDesign.Palette.spoilerRed)
        XCTAssertEqual(FeedMark.spoiler.cacheName, "spoiler")
    }

    func testTheSecondLinePrefersTheReviewThenTheLogThenStanding() {
        let movie = ref("movie")
        XCTAssertEqual(
            friend(diaryEntryId: 1, consumedAt: "2024-03-02", reviewTitle: "Great", review: "Words.").cardLine(for: movie),
            .review(title: "Great", text: "Words.")
        )
        XCTAssertEqual(friend(reviewTitle: "", review: "Words.").cardLine(for: movie), .review(title: nil, text: "Words."))
        XCTAssertEqual(friend(diaryEntryId: 1, consumedAt: "2024-03-02").cardLine(for: movie), .plain("Logged Mar 2, 2024"))
        XCTAssertEqual(
            friend(status: "In progress", progressLabel: "5 of 10 episodes").cardLine(for: ref("tv")),
            .status(word: "Watching", progress: "5 of 10 episodes")
        )
        XCTAssertEqual(friend(status: "Completed").cardLine(for: movie), .status(word: "Completed", progress: nil))
        XCTAssertNil(friend(rating: "4").cardLine(for: movie))
    }

    func testASpoilerReviewFallsBackToTheLogDate() {
        let spoiler = friend(diaryEntryId: 1, consumedAt: "2024-03-02", review: "He dies.", containsSpoilers: true)
        XCTAssertEqual(spoiler.cardLine(for: ref("movie")), .plain("Logged Mar 2, 2024"))
    }

    // MARK: Builders

    private func ref(_ mediaType: String) -> MediaRef {
        MediaRef(itemId: nil, source: "manual", mediaType: mediaType, mediaId: "1", seasonNumber: nil, episodeNumber: nil)
    }

    private func friend(
        id: Int = 7,
        diaryEntryId: Int? = nil,
        consumedAt: String? = nil,
        rating: String? = nil,
        liked: Bool = false,
        isRewatch: Bool = false,
        reviewTitle: String? = nil,
        review: String = "",
        containsSpoilers: Bool = false,
        status: String? = nil,
        progressLabel: String? = nil
    ) -> MediaFriend {
        MediaFriend(
            user: UserSummary(id: id, username: "u\(id)", displayName: "User \(id)", avatarUrl: nil),
            diaryEntryId: diaryEntryId,
            consumedAt: consumedAt,
            rating: rating,
            liked: liked,
            isRewatch: isRewatch,
            reviewTitle: reviewTitle,
            review: review,
            containsSpoilers: containsSpoilers,
            status: status,
            progressLabel: progressLabel
        )
    }
}
