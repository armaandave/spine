import XCTest
@testable import Spine

final class FeedDetailPlannerTests: XCTestCase {
    private let song = FeedFavorite(glyph: "♫", text: "Snooze")
    private let longReview = "saw it again in IMAX and the juke joint scene still levitates, the cut from the guitar"

    private func rows(
        meter: Bool = false,
        headline: Bool = true,
        spoilers: Bool = false,
        review: String? = nil,
        favorite: FeedFavorite? = nil,
        quiet: String? = nil,
        quietLines: Int = 1
    ) -> [FeedDetailRow] {
        FeedDetailPlanner.rows(
            hasMeter: meter, hasHeadline: headline, spoilers: spoilers, review: review,
            favorite: favorite, quiet: quiet, quietLines: quietLines
        )
    }

    // MARK: Priority without a meter

    func testSpoilersAloneWinOverEverything() {
        XCTAssertEqual(rows(spoilers: true, review: longReview, favorite: song, quiet: "Billie Eilish"), [.spoilers])
    }

    func testTheChipNeedsTextToHide() {
        XCTAssertEqual(rows(spoilers: true), [])
        XCTAssertEqual(rows(spoilers: true, review: "  \n"), [])
        XCTAssertEqual(
            rows(headline: false, spoilers: true, favorite: song, quiet: "q"),
            [.favorite(glyph: "♫", text: "Snooze"), .quiet(text: "q", lines: 1)]
        )
        XCTAssertEqual(rows(spoilers: true, favorite: song, quiet: "q"), [.favorite(glyph: "♫", text: "Snooze")])
    }

    func testAReviewTakesTwoLinesWithoutAHeadlineAndOneUnderOne() {
        XCTAssertEqual(rows(headline: false, review: longReview), [.review(text: longReview, lines: 2)])
        XCTAssertEqual(rows(headline: false, review: longReview, quiet: "ignored"), [.review(text: longReview, lines: 2)])
        // Under a headline: 77 + 4 + 38 = 119 is past 103, so one line.
        XCTAssertEqual(rows(review: longReview), [.review(text: longReview, lines: 1)])
    }

    func testAReviewWithAFavoriteTakesOneLineAndTheFavoriteFollowsWhenThereIsRoom() {
        XCTAssertEqual(
            rows(headline: false, review: longReview, favorite: song),
            [.review(text: longReview, lines: 1), .favorite(glyph: "♫", text: "Snooze")]
        )
        // Under a headline only the review fits: 77 + 4 + 19 + 3 + 18 = 121.
        XCTAssertEqual(rows(review: longReview, favorite: song), [.review(text: longReview, lines: 1)])
    }

    func testWithoutAReviewTheFavoriteAndTheQuietLineShare() {
        XCTAssertEqual(
            rows(headline: false, favorite: song, quiet: "Billie Eilish · 2024"),
            [.favorite(glyph: "♫", text: "Snooze"), .quiet(text: "Billie Eilish · 2024", lines: 1)]
        )
        XCTAssertEqual(rows(favorite: song, quiet: "Billie Eilish · 2024"), [.favorite(glyph: "♫", text: "Snooze")])
        XCTAssertEqual(rows(favorite: song), [.favorite(glyph: "♫", text: "Snooze")])
        XCTAssertEqual(rows(quiet: "Andy Weir"), [.quiet(text: "Andy Weir", lines: 1)])
        XCTAssertEqual(rows(), [])
    }

    // MARK: A meter takes the bottom

    func testAMeterUnderAHeadlineLeavesNoRowAndWithoutOneLeavesOneInTheSamePriority() {
        XCTAssertEqual(rows(meter: true, review: longReview, favorite: song, quiet: "q"), [])
        XCTAssertEqual(rows(meter: true, quiet: "last 64 pages"), [])
        XCTAssertEqual(
            rows(meter: true, headline: false, review: longReview, favorite: song, quiet: "q"),
            [.review(text: longReview, lines: 1)]
        )
        XCTAssertEqual(rows(meter: true, headline: false, favorite: song, quiet: "q"), [.favorite(glyph: "♫", text: "Snooze")])
        XCTAssertEqual(rows(meter: true, headline: false, quiet: "last 64 pages"), [.quiet(text: "last 64 pages", lines: 1)])
        XCTAssertEqual(rows(meter: true), [])
    }

    func testAChipThatCannotFitBesideAMeterAndHeadlineGivesNothingRatherThanTheReview() {
        // 77 + 3 + 22 = 102 is past the meter's 81.
        XCTAssertEqual(rows(meter: true, headline: true, spoilers: true, review: longReview), [])
        // Without a headline the rows start at 55 and the chip fits: 55 + 3 + 22 = 80.
        XCTAssertEqual(rows(meter: true, headline: false, spoilers: true, review: longReview), [.spoilers])
    }

    // MARK: Lines and budget

    func testQuietLinesCutBackToWhatFits() {
        // Without a headline (a Planning day): 55 + 3 + 36 = 94 fits two lines.
        XCTAssertEqual(rows(headline: false, quiet: "A, B and C", quietLines: 2), [.quiet(text: "A, B and C", lines: 2)])
        // Under a headline: 77 + 3 + 36 = 116 is past 103, so one line.
        XCTAssertEqual(rows(quiet: "A, B and C", quietLines: 2), [.quiet(text: "A, B and C", lines: 1)])
        // Beside a meter without a headline the limit is 81, so one line: 55 + 3 + 18 = 76.
        XCTAssertEqual(rows(meter: true, headline: false, quiet: "A, B and C", quietLines: 2), [.quiet(text: "A, B and C", lines: 1)])
        // Behind a favorite without a headline: 55 + 3 + 18 + 3 + 36 = 115 is over, so the quiet line is cut to one.
        XCTAssertEqual(
            rows(headline: false, favorite: song, quiet: "A, B and C", quietLines: 2),
            [.favorite(glyph: "♫", text: "Snooze"), .quiet(text: "A, B and C", lines: 1)]
        )
    }

    func testRowHeightsAreTheMocks() {
        XCTAssertEqual(FeedDetailRow.spoilers.height, 22)
        XCTAssertEqual(FeedDetailRow.spoilers.topMargin, 3)
        XCTAssertEqual(FeedDetailRow.review(text: "r", lines: 1).height, 19)
        XCTAssertEqual(FeedDetailRow.review(text: "r", lines: 2).height, 38)
        XCTAssertEqual(FeedDetailRow.review(text: "r", lines: 2).topMargin, 4)
        XCTAssertEqual(FeedDetailRow.favorite(glyph: "x", text: "y").height, 18)
        XCTAssertEqual(FeedDetailRow.favorite(glyph: "x", text: "y").topMargin, 3)
        XCTAssertEqual(FeedDetailRow.quiet(text: "q", lines: 2).height, 36)
        XCTAssertEqual(FeedDetailRow.quiet(text: "q", lines: 1).topMargin, 3)
        XCTAssertEqual(FeedDetailPlanner.rowsStart(hasHeadline: true), 77)
        XCTAssertEqual(FeedDetailPlanner.rowsStart(hasHeadline: false), 55)
        XCTAssertEqual(FeedDetailPlanner.rowsLimit(hasMeter: true), 81)
        XCTAssertEqual(FeedDetailPlanner.rowsLimit(hasMeter: false), 103)
    }

    /// Every combination of meter, headline, spoilers, review, favorite, quiet and quiet lines.
    func testNoCombinationOverflowsTheCardOrShowsMoreThanTwoRows() {
        var checked = 0
        for meter in [false, true] {
            for headline in [false, true] {
                for spoilers in [false, true] {
                    for review in [nil, "A short review.", longReview, "   "] as [String?] {
                        for favorite in [nil, song] as [FeedFavorite?] {
                            for quiet in [nil, "Sally Rooney · p. 212 of 448"] as [String?] {
                                for quietLines in 1 ... 3 {
                                    let result = rows(
                                        meter: meter, headline: headline, spoilers: spoilers, review: review,
                                        favorite: favorite, quiet: quiet, quietLines: quietLines
                                    )
                                    let label = "meter \(meter) headline \(headline) spoilers \(spoilers) review \(review ?? "nil") "
                                        + "favorite \(favorite != nil) quiet \(quiet != nil) lines \(quietLines)"
                                    XCTAssertLessThanOrEqual(result.count, meter ? 1 : 2, label)
                                    XCTAssertLessThanOrEqual(
                                        FeedDetailPlanner.bottom(of: result, hasHeadline: headline),
                                        FeedDetailPlanner.rowsLimit(hasMeter: meter),
                                        label
                                    )
                                    for row in result {
                                        XCTAssertGreaterThanOrEqual(row.lines, 1, label)
                                    }
                                    // The chip hides text: no review row ever beside it, and never without text.
                                    if result.contains(.spoilers) {
                                        XCTAssertEqual(result.count, 1, label)
                                        XCTAssertNotNil(review?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty, label)
                                    }
                                    // A flagged log never shows its review text.
                                    if spoilers, review?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty != nil {
                                        for row in result {
                                            if case .review = row { XCTFail("Spoiler review shown: \(label)") }
                                        }
                                    }
                                    checked += 1
                                }
                            }
                        }
                    }
                }
            }
        }
        XCTAssertEqual(checked, 2 * 2 * 2 * 4 * 2 * 2 * 3)
    }
}

private extension String {
    var nonEmpty: String? { isEmpty ? nil : self }
}
