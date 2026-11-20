import SwiftUI
import XCTest
@testable import Spine

/// The ratings chart's numbers and the friends' glass cards' words and marks. Layout and styling aren't tested.
@MainActor
final class RatingsGlowChartTests: XCTestCase {
    private typealias G = RatingsChartGeometry
    private let width: CGFloat = 374

    func testInterTightIsBundledAndRegistered() {
        XCTAssertNotNil(UIFont(name: "InterTight-Regular", size: 44))
    }

    // MARK: Chart geometry

    func testBucketsLandOnTheirHalfStarSteps() {
        let counts = G.counts(from: [
            RatingDistributionBucket(rating: "0.5", count: 2),
            RatingDistributionBucket(rating: "4.5", count: 7),
            RatingDistributionBucket(rating: "4.4", count: 1),
            RatingDistributionBucket(rating: "5", count: 3),
            RatingDistributionBucket(rating: "0", count: 9),
        ])
        XCTAssertEqual(counts, [2, 0, 0, 0, 0, 0, 0, 0, 8, 3])
    }

    func testTheBusiestStepReachesTheAmplitudeAndEmptyStepsSitOnTheFloor() {
        let ys = G.ys(counts: [0, 5, 10, 0, 0, 0, 0, 0, 0, 0])
        XCTAssertEqual(ys[0], G.floorY)
        XCTAssertEqual(ys[1], G.floorY - G.amplitude / 2, accuracy: 0.001)
        XCTAssertEqual(ys[2], G.floorY - G.amplitude, accuracy: 0.001)
    }

    func testTheHighestPeakStopsJustUnderTheHeader() {
        let ys = G.ys(counts: [1, 3, 2, 9, 4, 0, 7, 12, 5, 1])
        let slopes = G.slopes(ys)
        var x = G.inset
        while x <= width - G.inset {
            XCTAssertGreaterThanOrEqual(G.curveY(at: x, ys: ys, slopes: slopes, chartWidth: width), G.peakTop - 0.001)
            x += 2
        }
        XCTAssertGreaterThan(G.peakTop, 0, "the line never touches the line above it")
    }

    func testStepsSpanTheWidthWithAnInsetEitherSide() {
        XCTAssertEqual(G.x(step: 0, chartWidth: width), G.inset)
        XCTAssertEqual(G.x(step: 9, chartWidth: width), width - G.inset, accuracy: 0.001)
    }

    func testTheAverageSitsBetweenItsSteps() {
        XCTAssertEqual(G.averageX(stars: 0.5, chartWidth: width), G.inset)
        XCTAssertEqual(G.averageX(stars: 5, chartWidth: width), width - G.inset, accuracy: 0.001)
        let half = (G.x(step: 7, chartWidth: width) + G.x(step: 8, chartWidth: width)) / 2
        XCTAssertEqual(G.averageX(stars: 4.25, chartWidth: width), half, accuracy: 0.001)
    }

    func testTheCurveNeverOvershootsAndStaysFlatWhereNobodyRated() {
        let ys = G.ys(counts: [0, 0, 1, 0, 0, 0, 0, 0, 40, 0])
        let slopes = G.slopes(ys)
        XCTAssertEqual(slopes[0], 0)
        XCTAssertEqual(slopes[2], 0, "a peak is flat")
        var x: CGFloat = G.inset
        while x <= width - G.inset {
            let y = G.curveY(at: x, ys: ys, slopes: slopes, chartWidth: width)
            XCTAssertLessThanOrEqual(y, G.floorY + 0.001, "never dips under the floor at \(x)")
            XCTAssertGreaterThanOrEqual(y, ys.min()! - 0.001, "never rises over the busiest step at \(x)")
            x += 3
        }
        XCTAssertEqual(G.curveY(at: G.x(step: 8, chartWidth: width), ys: ys, slopes: slopes, chartWidth: width), ys[8], accuracy: 0.001)
    }

    func testAFaceSitsAtItsStepAndAGroupSpreadsAroundIt() {
        let alone = G.faceSlots(steps: [4], chartWidth: width)
        XCTAssertEqual(alone[0].x, G.x(step: 4, chartWidth: width), accuracy: 0.001)
        let slots = G.faceSlots(steps: [8, 3, 8, 8], chartWidth: width)
        let center = G.x(step: 8, chartWidth: width)
        XCTAssertEqual(slots[0].x, center - G.faceStep, accuracy: 0.001, "the first in the order is leftmost")
        XCTAssertEqual(slots[2].x, center, accuracy: 0.001)
        XCTAssertEqual(slots[3].x, center + G.faceStep, accuracy: 0.001)
        XCTAssertEqual(slots.map(\.rankInGroup), [0, 0, 1, 2])
        XCTAssertEqual(slots[1].x, G.x(step: 3, chartWidth: width), accuracy: 0.001)
    }

    func testAGroupAtTheEdgesIsShiftedInsideTheChartWhole() {
        let reach = G.faceSize / 2 + G.faceRingReach
        let low = G.faceSlots(steps: [Int](repeating: 0, count: 4), chartWidth: width)
        XCTAssertEqual(low.map(\.x).min()!, reach, accuracy: 0.001)
        XCTAssertEqual(low[1].x - low[0].x, G.faceStep, accuracy: 0.001, "the spacing survives the shift")
        let high = G.faceSlots(steps: [Int](repeating: 9, count: 4), chartWidth: width)
        XCTAssertEqual(high.map(\.x).max()!, width - reach, accuracy: 0.001)
        let single = G.faceSlots(steps: [0, 9], chartWidth: width)
        XCTAssertEqual(single[0].x, G.inset, "a lone face at the end is already inside")
    }

    func testALoneFaceAtEitherEndStaysInsideTheChart() {
        let reach = G.faceSize / 2 + G.faceRingReach
        for step in [0, 9] {
            let x = G.faceSlots(steps: [step], chartWidth: width)[0].x
            XCTAssertGreaterThanOrEqual(x - reach, 0)
            XCTAssertLessThanOrEqual(x + reach, width)
        }
    }

    func testAGroupOfThreeAtTheFirstStepJustFitsWithoutAShift() {
        let slots = G.faceSlots(steps: [0, 0, 0], chartWidth: width)
        XCTAssertEqual(slots[1].x, G.inset, accuracy: 0.001)
    }

    func testAVeryNarrowChartCentersTheGroup() {
        let slots = G.faceSlots(steps: [0, 0, 0], chartWidth: 20)
        XCTAssertEqual(slots[1].x, 10, accuracy: 0.001)
    }

    func testABigGroupNeverSpansMoreThanOneStep() {
        let slots = G.faceSlots(steps: [Int](repeating: 4, count: 12), chartWidth: width)
        let xs = slots.map(\.x)
        XCTAssertLessThanOrEqual(xs.max()! - xs.min()!, G.stepWidth(chartWidth: width) + 0.001)
        XCTAssertEqual((xs.max()! + xs.min()!) / 2, G.x(step: 4, chartWidth: width), accuracy: 0.001)
    }

    func testYouAreInFrontThenTheFirstOfAGroupInFrontOfTheNext() {
        let slots = G.faceSlots(steps: [5, 5, 5, 5], chartWidth: width, front: [2])
        XCTAssertEqual(slots[2].depth, Int.max, "you are in front of everyone")
        XCTAssertGreaterThan(slots[0].depth, slots[1].depth)
        XCTAssertGreaterThan(slots[1].depth, slots[3].depth)
        let apart = G.faceSlots(steps: [2, 3], chartWidth: width)
        XCTAssertNotEqual(apart[0].depth, apart[1].depth, "neighbouring groups never tie")
    }

    func testNobodyRatedMeansNoFaces() {
        XCTAssertTrue(G.faceSlots(steps: [], chartWidth: width).isEmpty)
    }

    func testTheAverageIsCountedFromTheStepsWhenThePayloadHasNone() {
        XCTAssertEqual(G.average(payload: 3.2, counts: [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]), 3.2)
        XCTAssertEqual(G.average(payload: nil, counts: [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]), 4.75, accuracy: 0.001)
        XCTAssertEqual(G.average(payload: nil, counts: [Int](repeating: 0, count: 10)), 0)
    }

    // MARK: Stars beside the number

    func testStarsFillWholeThenToTheFractionAndDrawNothingForTheRest() {
        XCTAssertEqual(G.starFills(average: 5), [1, 1, 1, 1, 1])
        XCTAssertEqual(G.starFills(average: 4.5), [1, 1, 1, 1, 0.5])
        XCTAssertEqual(G.starFills(average: 3), [1, 1, 1])
        XCTAssertEqual(G.starFills(average: 0.5), [0.5])
        let sliver = G.starFills(average: 4.1)
        XCTAssertEqual(sliver.count, 5)
        XCTAssertEqual(sliver.last ?? 0, 0.1, accuracy: 0.000_1)
    }

    func testStarsFillToTheAverageAsTheNumberShowsIt() {
        // 3.96 shows as 4.0: four whole stars, no sliver of a fifth.
        XCTAssertEqual(G.starFills(average: 3.96), [1, 1, 1, 1])
        // 4.06 shows as 4.1.
        XCTAssertEqual(G.starFills(average: 4.06).last ?? 0, 0.1, accuracy: 0.000_1)
    }

    // MARK: People in the faces row

    func testAnEpisodePageChartsTheEpisodeStarsAndEveryOtherPageTheTitles() {
        XCTAssertEqual(RatingChartPerson.viewerRating(mediaType: "episode", titleRating: "4", episodeRating: nil), nil)
        XCTAssertEqual(RatingChartPerson.viewerRating(mediaType: "episode", titleRating: "4", episodeRating: "2.5"), "2.5")
        XCTAssertEqual(RatingChartPerson.viewerRating(mediaType: "season", titleRating: "4", episodeRating: "2.5"), "4")
    }

    func testYouGoFirstAndOnlyWhenYouHaveStars() {
        let friends = [friend(id: 1, rating: "4.5"), friend(id: 2, rating: nil), friend(id: 3, rating: "2")]
        let people = RatingChartPerson.people(viewerRating: "4.5", viewerAvatarURL: "me.png", friends: friends)
        XCTAssertEqual(people.map(\.id), ["you", "friend-1", "friend-3"])
        XCTAssertEqual(people.map(\.halfSteps), [9, 9, 4])
        XCTAssertEqual(people.map(\.isYou), [true, false, false])
        XCTAssertEqual(RatingChartPerson.people(viewerRating: nil, viewerAvatarURL: nil, friends: friends).map(\.id), ["friend-1", "friend-3"])
        let noStars = [friend(id: 1, rating: "0"), friend(id: 2, rating: ""), friend(id: 3, rating: nil)]
        XCTAssertTrue(RatingChartPerson.people(viewerRating: "0", viewerAvatarURL: nil, friends: noStars).isEmpty)
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

    func testTheSecondLinePrefersTheReviewThenTheLogThenStanding() {
        let movie = ref("movie")
        XCTAssertEqual(
            friend(diaryEntryId: 1, consumedAt: "2024-03-02", reviewTitle: "Great", review: "Words.").cardLine(for: movie),
            .review(title: "Great", text: "Words.")
        )
        XCTAssertEqual(friend(reviewTitle: "", review: "Words.").cardLine(for: movie), .review(title: nil, text: "Words."))
        guard case let .plain(logged) = friend(diaryEntryId: 1, consumedAt: "2024-03-02").cardLine(for: movie) else {
            return XCTFail("a log with no review says when it was logged")
        }
        XCTAssertTrue(logged.hasPrefix("Logged"))
        XCTAssertEqual(friend(progressLabel: "5 of 10 episodes").cardLine(for: ref("tv")), .detail("5 of 10 episodes"))
        XCTAssertEqual(
            friend(status: "In progress", progressLabel: "5 of 10 episodes").cardLine(for: ref("tv")),
            .status(word: "Watching", progress: "5 of 10 episodes")
        )
        XCTAssertEqual(friend(status: "Completed").cardLine(for: movie), .status(word: "Completed", progress: nil))
        XCTAssertNil(friend(rating: "4").cardLine(for: movie))
    }

    func testASpoilerReviewFallsBackToTheLogDate() {
        let spoiler = friend(diaryEntryId: 1, consumedAt: "2024-03-02", review: "He dies.", containsSpoilers: true)
        guard case let .plain(logged) = spoiler.cardLine(for: ref("movie")) else {
            return XCTFail("a spoiler review's words wait behind its log")
        }
        XCTAssertTrue(logged.hasPrefix("Logged"))
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
