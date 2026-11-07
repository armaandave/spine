import SwiftUI
import XCTest
@testable import Spine

/// The before/today bar under a progress sheet's number: what it draws for a book, a game, a manga, and for every number
/// the field can hold.
@MainActor
final class ProgressSheetBarTests: XCTestCase {
    private func bar(
        _ unit: MeterUnit,
        last: Decimal?,
        typed: Decimal?,
        total: Int?
    ) -> ProgressSheetBar? {
        ProgressSheetBar.make(unit: unit, last: last, typed: typed, total: total, accent: .yellow)
    }

    func testBookInPagesShowsTheEarlierPartAndTodays() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 212, typed: 232, total: 400))
        XCTAssertEqual(bar.model.from, 212)
        XCTAssertEqual(bar.model.to, 232)
        XCTAssertEqual(bar.model.total, 400)
        XCTAssertEqual(bar.model.beforeFraction, 0.53, accuracy: 0.0001)
        XCTAssertEqual(bar.model.todayFraction, 0.05, accuracy: 0.0001)
        XCTAssertEqual(bar.text, "+20 pages")
    }

    func testBookInPercentRunsToAHundredWhateverThePages() throws {
        let bar = try XCTUnwrap(bar(.percent, last: 47.32, typed: 52, total: nil))
        XCTAssertEqual(bar.model.total, 100)
        XCTAssertEqual(bar.model.from, 47)
        XCTAssertEqual(bar.model.to, 52)
        XCTAssertEqual(bar.text, "+4.68%")
        XCTAssertEqual(try XCTUnwrap(self.bar(.percent, last: 53, typed: 58, total: 400)).text, "+5%")
    }

    func testGamePercent() throws {
        let bar = try XCTUnwrap(bar(.percent, last: 40, typed: 55, total: 100))
        XCTAssertEqual(bar.model.from, 40)
        XCTAssertEqual(bar.model.to, 55)
        XCTAssertEqual(bar.text, "+15%")
    }

    func testMangaChaptersAndComicIssues() throws {
        let manga = try XCTUnwrap(bar(.chapters, last: 41, typed: 45, total: 90))
        XCTAssertEqual(manga.model.kind, .bar(total: 90))
        XCTAssertEqual(manga.model.total, 90)
        XCTAssertEqual(manga.text, "+4 chapters")
        XCTAssertEqual(try XCTUnwrap(bar(.chapters, last: 41, typed: 42, total: 90)).text, "+1 chapter")
        XCTAssertEqual(try XCTUnwrap(bar(.issues, last: 3, typed: 6, total: 12)).text, "+3 issues")
        XCTAssertEqual(try XCTUnwrap(bar(.pages, last: 9, typed: 10, total: 400)).text, "+1 page")
    }

    func testNoBarWhenTheTotalIsUnknown() {
        XCTAssertNil(bar(.pages, last: 212, typed: 232, total: nil))
        XCTAssertNil(bar(.pages, last: 212, typed: 232, total: 0))
        XCTAssertNil(bar(.chapters, last: 41, typed: 45, total: nil))
        XCTAssertNil(bar(.issues, last: 3, typed: 4, total: nil))
    }

    func testNoBarForGameHours() {
        XCTAssertNil(bar(.minutes, last: 14, typed: 20, total: 100))
    }

    func testMovedBackLightsNothingAndSaysSo() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 212, typed: 150, total: 400))
        XCTAssertEqual(bar.model.from, 212)
        XCTAssertEqual(bar.model.to, 212)
        XCTAssertEqual(bar.model.todayFraction, 0)
        XCTAssertEqual(bar.text, "moved back")
    }

    func testAboveTheTotalStopsAtTheEndWithoutOverdrawing() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 212, typed: 999_999, total: 400))
        XCTAssertEqual(bar.model.to, 400)
        XCTAssertEqual(bar.model.endFraction, 1)
        XCTAssertLessThanOrEqual(bar.model.beforeFraction + bar.model.todayFraction, 1)
        XCTAssertEqual(bar.text, "+188 pages")
        let percent = try XCTUnwrap(self.bar(.percent, last: 50, typed: 250, total: nil))
        XCTAssertEqual(percent.model.to, 100)
        XCTAssertEqual(percent.text, "+50%")
    }

    func testNothingTypedKeepsTheBarAndLightsNothing() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 212, typed: nil, total: 400))
        XCTAssertEqual(bar.model.from, 212)
        XCTAssertEqual(bar.model.to, 212)
        XCTAssertNil(bar.text)
    }

    func testTheSameNumberAsTheLastSaveHasNoText() throws {
        XCTAssertNil(try XCTUnwrap(bar(.pages, last: 212, typed: 212, total: 400)).text)
    }

    func testBeforeAFirstSaveEverythingTypedIsToday() throws {
        let bar = try XCTUnwrap(bar(.pages, last: nil, typed: 30, total: 400))
        XCTAssertEqual(bar.model.from, 0)
        XCTAssertEqual(bar.model.to, 30)
        XCTAssertEqual(bar.text, "+30 pages")
    }

    func testALastSavePastTheTotalIsClamped() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 450, typed: 400, total: 400))
        XCTAssertEqual(bar.model.from, 400)
        XCTAssertEqual(bar.model.to, 400)
        XCTAssertNil(bar.text)
    }

    func testSizingTextIsTheUnitsWidestLabel() throws {
        XCTAssertEqual(try XCTUnwrap(bar(.pages, last: 0, typed: 1, total: 10)).sizingText, "+9999 pages")
        XCTAssertEqual(try XCTUnwrap(bar(.percent, last: 0, typed: 1, total: nil)).sizingText, "+9999%")
        XCTAssertEqual(try XCTUnwrap(bar(.chapters, last: 0, typed: 1, total: 10)).sizingText, "+9999 chapters")
    }

    func testTheSheetsViewModelFeedsTheBook() throws {
        let title = ProgressSheetTitle(item: Self.bookItem())
        ProgressDisplayPreferences.removeMode(for: title.ref)
        defer { ProgressDisplayPreferences.removeMode(for: title.ref) }
        let progress = ProgressState(kind: "pages", value: 212, max: 400, unit: "page")
        let viewModel = ProgressUpdateViewModel(title: title, progress: progress)
        viewModel.updateInput("232")

        let pages = try XCTUnwrap(bar(.pages, last: viewModel.lastValue, typed: viewModel.currentValue, total: viewModel.totalPages))
        XCTAssertEqual(pages.model.from, 212)
        XCTAssertEqual(pages.model.to, 232)
        XCTAssertEqual(pages.text, "+20 pages")

        viewModel.selectMode(.percentage)
        let percent = try XCTUnwrap(bar(.percent, last: viewModel.lastValue, typed: viewModel.currentValue, total: viewModel.totalPages))
        XCTAssertEqual(percent.model.from, 53)
        XCTAssertEqual(percent.model.to, 58)
        XCTAssertEqual(percent.text, "+5%")

        viewModel.updateInput("")
        XCTAssertNil(viewModel.currentValue)
        XCTAssertNil(try XCTUnwrap(bar(.percent, last: viewModel.lastValue, typed: viewModel.currentValue, total: viewModel.totalPages)).text)
    }

    func testThePosterAccentReachesTheTitle() {
        XCTAssertEqual(ProgressSheetTitle(item: Self.bookItem(accent: "#3366AA")).posterAccentHex, "#3366AA")
        XCTAssertNil(ProgressSheetTitle(item: Self.bookItem()).posterAccentHex)
    }

    private static func bookItem(accent: String? = nil) -> Spine.LibraryItem {
        let ref = MediaRef(itemId: nil, source: "hardcover", mediaType: "book", mediaId: "sheet-bar",
                           seasonNumber: nil, episodeNumber: nil)
        return Spine.LibraryItem(
            media: MediaSummary(ref: ref, title: "Book", posterUrl: nil, posterOrientation: .portrait, posterAccentColor: accent),
            tracking: TrackingState(trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil,
                                    startDate: nil, endDate: nil, notes: nil, updatedAt: nil, book: nil, game: nil, tv: nil)
        )
    }

    func testThePaletteIsTheSheetsOwn() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 1, typed: 2, total: 10))
        XCTAssertEqual(bar.palette.accent, Color.yellow)
        XCTAssertEqual(ProgressSheetBar.height, 14)
        XCTAssertEqual(ProgressSheetBar.radius, 7)
    }
}
