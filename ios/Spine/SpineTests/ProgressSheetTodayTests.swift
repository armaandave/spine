import SwiftUI
import XCTest
@testable import Spine

/// What a book's or a game's progress sheet lights beside its number: all of today's progress on the title, as Home's
/// tile counts it, growing as a number is typed; the words about it; the drag along the bar; and a game's −1h and +1h.
@MainActor
final class ProgressSheetTodayTests: XCTestCase {
    private func bar(
        _ unit: MeterUnit,
        last: Decimal?,
        typed: Decimal?,
        total: Int?,
        before: Decimal = 0
    ) -> ProgressSheetBar? {
        ProgressSheetBar.make(unit: unit, last: last, typed: typed, total: total, accent: .yellow, todayBefore: before)
    }

    // MARK: What the title had today before this save

    func testTheTilesTodayInTheSheetsOwnUnit() {
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .pages, amount: 30), unit: .pages, total: 400, last: 212), 30
        )
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .chapters, amount: 3), unit: .chapters, total: nil, last: 41), 3,
            "The same unit needs no total"
        )
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .percent, amount: 1.5), unit: .percent, total: nil, last: 40), 1.5
        )
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .minutes, amount: 180), unit: .minutes, total: nil, last: 1_260), 180
        )
    }

    func testPagesAndPercentConvertThroughThePageCount() {
        // 40 pages of 400 is 10 percent, and 10 percent of 400 is 40 pages.
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .pages, amount: 40), unit: .percent, total: 400, last: 53), 10
        )
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .percent, amount: 10), unit: .pages, total: 400, last: 212), 40
        )
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .pages, amount: 40), unit: .percent, total: nil, last: 53), 0,
            "No page count, no conversion"
        )
    }

    func testAGamesMinutesNeverBecomePercentAndNothingCountsWithoutAPart() {
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .minutes, amount: 180), unit: .percent, total: nil, last: 40), 0
        )
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .percent, amount: 5), unit: .minutes, total: nil, last: 600), 0
        )
        XCTAssertEqual(ProgressSheetToday.before(part: nil, unit: .pages, total: 400, last: 212), 0)
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .pages, amount: 0), unit: .pages, total: 400, last: 212), 0
        )
    }

    func testTodayNeverExceedsWhereTheTitleStands() {
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .pages, amount: 300), unit: .pages, total: 400, last: 212), 212
        )
        XCTAssertEqual(
            ProgressSheetToday.before(part: TodayPart(unit: .pages, amount: 30), unit: .pages, total: 400, last: nil), 0
        )
    }

    // MARK: The lit part follows what is typed

    func testAllOfTodayIsLitBeforeAnythingIsTyped() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 212, typed: nil, total: 400, before: 30))
        XCTAssertEqual(bar.model.from, 182)
        XCTAssertEqual(bar.model.to, 212)
        XCTAssertEqual(bar.beforeFraction, 182.0 / 400, accuracy: 0.0001)
        XCTAssertEqual(bar.todayFraction, 30.0 / 400, accuracy: 0.0001)
        XCTAssertEqual(bar.text, "+30 pages")
        XCTAssertEqual(bar.todayLine, "+30 pages today")
    }

    func testTypingAddsToWhatWasAlreadyDoneToday() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 212, typed: 232, total: 400, before: 30))
        XCTAssertEqual(bar.model.from, 182)
        XCTAssertEqual(bar.model.to, 232)
        XCTAssertEqual(bar.thumbFraction, 232.0 / 400, accuracy: 0.0001)
        XCTAssertEqual(bar.text, "+50 pages", "30 from earlier today and 20 more")
        XCTAssertEqual(bar.todayLine, "+50 pages today")
    }

    func testTheLitPartIsOnePieceEndingAtTheThumb() throws {
        for typed in [Decimal(0), 100, 182, 212, 250, 400] {
            let bar = try XCTUnwrap(bar(.pages, last: 212, typed: typed, total: 400, before: 30))
            XCTAssertEqual(
                bar.beforeFraction + bar.todayFraction, bar.thumbFraction, accuracy: 0.0001,
                "The lit part ends where the thumb is, at \(typed)"
            )
            XCTAssertLessThanOrEqual(bar.beforeFraction + bar.todayFraction, 1)
        }
    }

    func testMovingBackIsMovedBackAndNeverLightsWhatItPassedOver() throws {
        // Today began at page 182. Page 190 is back from 212 but still inside today's part.
        let inside = try XCTUnwrap(bar(.pages, last: 212, typed: 190, total: 400, before: 30))
        XCTAssertEqual(inside.text, "moved back")
        XCTAssertEqual(inside.todayLine, "moved back")
        XCTAssertEqual(inside.model.from, 182)
        XCTAssertEqual(inside.model.to, 190)
        // Page 100 is before today began: nothing is lit.
        let before = try XCTUnwrap(bar(.pages, last: 212, typed: 100, total: 400, before: 30))
        XCTAssertEqual(before.model.from, 100)
        XCTAssertEqual(before.model.to, 100)
        XCTAssertEqual(before.todayFraction, 0)
    }

    func testPercentKeepsItsDecimalsInTheLitPartAndTheWords() throws {
        let bar = try XCTUnwrap(bar(.percent, last: 47.32, typed: 52, total: nil, before: 1.5))
        XCTAssertEqual(bar.text, "+6.18%", "1.5 from earlier today and 4.68 more")
        XCTAssertEqual(bar.beforeFraction, 0.4582, accuracy: 0.0001)
        XCTAssertEqual(bar.thumbFraction, 0.52, accuracy: 0.0001)
    }

    func testNothingToday() throws {
        let bar = try XCTUnwrap(bar(.pages, last: 212, typed: nil, total: 400))
        XCTAssertNil(bar.text)
        XCTAssertNil(bar.todayLine)
        XCTAssertEqual(bar.todayFraction, 0)
    }

    func testAMangaAndAComicCountChaptersAndIssuesToday() throws {
        let manga = try XCTUnwrap(bar(.chapters, last: 41, typed: 45, total: 90, before: 3))
        XCTAssertEqual(manga.text, "+7 chapters")
        XCTAssertEqual(try XCTUnwrap(bar(.issues, last: 3, typed: 4, total: 12, before: 0)).todayLine, "+1 issue today")
    }

    // MARK: Hours

    func testHoursSayTodayInHoursAndMinutes() {
        let three = ProgressSheetToday.lit(before: 0, last: 1_260, typed: 1_440)
        XCTAssertEqual(three.amount, 180)
        XCTAssertEqual(ProgressSheetToday.line(forAmountText: ProgressSheetToday.amountText(unit: .minutes, lit: three.amount, movedBack: false)), "+3h today")
        let odd = ProgressSheetToday.lit(before: 30, last: 870, typed: 900)
        XCTAssertEqual(odd.amount, 60)
        XCTAssertEqual(ProgressSheetToday.amountText(unit: .minutes, lit: odd.amount, movedBack: false), "+1h")
        XCTAssertEqual(ProgressSheetToday.amountText(unit: .minutes, lit: 90, movedBack: false), "+1h 30m")
    }

    func testHoursBelowTheLastSaveAreMovedBack() {
        let back = ProgressSheetToday.lit(before: 60, last: 1_260, typed: 1_200)
        XCTAssertTrue(back.movedBack)
        XCTAssertEqual(
            ProgressSheetToday.line(forAmountText: ProgressSheetToday.amountText(unit: .minutes, lit: back.amount, movedBack: back.movedBack)),
            "moved back"
        )
    }

    func testHoursNothingTypedKeepsTodaysEarlierPart() {
        let none = ProgressSheetToday.lit(before: 120, last: 1_260, typed: nil)
        XCTAssertEqual(none.amount, 120)
        XCTAssertFalse(none.movedBack)
        XCTAssertNil(ProgressSheetToday.amountText(unit: .minutes, lit: 0, movedBack: false))
    }

    func testMinusAndPlusOneHour() {
        XCTAssertEqual(GameHoursStep.hours(typed: 24, last: 21, change: 1), 25)
        XCTAssertEqual(GameHoursStep.hours(typed: 24, last: 21, change: -1), 23)
        XCTAssertEqual(GameHoursStep.hours(typed: nil, last: 21, change: 1), 22, "From the last saved hours when nothing is typed")
        XCTAssertEqual(GameHoursStep.hours(typed: nil, last: nil, change: 1), 1)
        XCTAssertEqual(GameHoursStep.hours(typed: 0, last: 21, change: -1), 0, "Never below nothing")
        XCTAssertEqual(GameHoursStep.hours(typed: nil, last: nil, change: -1), 0)
        XCTAssertEqual(GameHoursStep.hours(typed: 99_999, last: nil, change: 1), 99_999, "Never past five digits")
    }

    // MARK: The drag

    func testAPlaceAlongTheBarIsAWholePageChapterOrIssue() throws {
        let book = try XCTUnwrap(bar(.pages, last: 212, typed: nil, total: 400))
        XCTAssertEqual(book.value(atFraction: 0), 0)
        XCTAssertEqual(book.value(atFraction: 1), 400)
        XCTAssertEqual(book.value(atFraction: 0.5), 200)
        XCTAssertEqual(book.value(atFraction: 0.5801), 232)
        XCTAssertEqual(book.value(atFraction: 0.0012), 0)
        XCTAssertEqual(book.value(atFraction: 0.0013), 1)
        let manga = try XCTUnwrap(bar(.chapters, last: 41, typed: nil, total: 90))
        XCTAssertEqual(manga.value(atFraction: 0.5), 45)
        XCTAssertEqual(manga.value(atFraction: 0.4999), 45)
    }

    func testAPlaceAlongAPercentBarIsAHalfPercent() throws {
        let game = try XCTUnwrap(bar(.percent, last: 38, typed: nil, total: 100))
        XCTAssertEqual(game.step, Decimal(string: "0.5"))
        XCTAssertEqual(game.value(atFraction: 0.4753), Decimal(string: "47.5"))
        XCTAssertEqual(game.value(atFraction: 0.4776), Decimal(string: "48"))
        XCTAssertEqual(game.value(atFraction: 1), 100)
        XCTAssertEqual(game.value(atFraction: 0), 0)
    }

    func testAPlaceOffEitherEndStopsAtTheEnd() throws {
        let book = try XCTUnwrap(bar(.pages, last: 212, typed: nil, total: 400))
        XCTAssertEqual(book.value(atFraction: -0.4), 0)
        XCTAssertEqual(book.value(atFraction: 7), 400)
        XCTAssertEqual(book.value(atFraction: .nan), 0)
    }

    func testEveryPlaceMapsBackToItself() throws {
        let book = try XCTUnwrap(bar(.pages, last: 0, typed: nil, total: 400))
        for page in stride(from: 0, through: 400, by: 7) {
            let share = Double(page) / 400
            XCTAssertEqual(book.value(atFraction: share), Decimal(page), "page \(page)")
        }
    }

    func testTheThumbTicksAtMostTwentyTimesAlongTheBar() throws {
        XCTAssertEqual(try XCTUnwrap(bar(.pages, last: 0, typed: nil, total: 448)).detentCount, 20)
        XCTAssertEqual(try XCTUnwrap(bar(.percent, last: 0, typed: nil, total: nil)).detentCount, 20, "Half percents: 200 steps")
        XCTAssertEqual(try XCTUnwrap(bar(.chapters, last: 0, typed: nil, total: 10)).detentCount, 10, "One per step when there are few")
        XCTAssertEqual(try XCTUnwrap(bar(.chapters, last: 0, typed: nil, total: 1)).detentCount, 1)
        let book = try XCTUnwrap(bar(.pages, last: 0, typed: nil, total: 400))
        XCTAssertEqual(book.detent(of: 0), 0)
        XCTAssertEqual(book.detent(of: 400), 20)
        XCTAssertEqual(book.detent(of: 200), 10)
        XCTAssertEqual(book.detent(of: 4), book.detent(of: 6), "Neighboring pages share a tick")
    }

    func testVoiceOverSwipesMoveATwentiethOfTheBarAtLeastOneStep() throws {
        let book = try XCTUnwrap(bar(.pages, last: 212, typed: 232, total: 400))
        XCTAssertEqual(book.adjusted(by: 1), 252)
        XCTAssertEqual(book.adjusted(by: -1), 212)
        let nearEnd = try XCTUnwrap(bar(.pages, last: 212, typed: 395, total: 400))
        XCTAssertEqual(nearEnd.adjusted(by: 1), 400)
        let nearStart = try XCTUnwrap(bar(.pages, last: 212, typed: 5, total: 400))
        XCTAssertEqual(nearStart.adjusted(by: -1), 0)
        let short = try XCTUnwrap(bar(.chapters, last: 2, typed: 3, total: 10))
        XCTAssertEqual(short.adjusted(by: 1), 4, "At least one step")
    }

    func testVoiceOverReadsThePlace() throws {
        XCTAssertEqual(try XCTUnwrap(bar(.pages, last: 212, typed: 232, total: 400)).spokenValue, "232 of 400 pages")
        XCTAssertEqual(try XCTUnwrap(bar(.percent, last: 40, typed: 47.5, total: nil)).spokenValue, "47.5 percent")
        XCTAssertEqual(try XCTUnwrap(bar(.chapters, last: 41, typed: 45, total: 90)).spokenValue, "45 of 90 chapters")
    }

    func testADragSetsTheNumberAsIfItWereTyped() {
        let title = ProgressSheetTitle(item: Self.bookItem())
        ProgressDisplayPreferences.removeMode(for: title.ref)
        defer { ProgressDisplayPreferences.removeMode(for: title.ref) }
        let viewModel = ProgressUpdateViewModel(
            title: title,
            progress: ProgressState(kind: "pages", value: 212, max: 400, unit: "page")
        )
        XCTAssertEqual(viewModel.keyboardInputText, "", "Nothing typed: the last page shows as the placeholder")
        viewModel.setNumber(232)
        XCTAssertEqual(viewModel.keyboardInputText, "232")
        XCTAssertEqual(viewModel.currentValue, 232)
        XCTAssertTrue(viewModel.canSave)
        // A digit typed after a drag adds to what the drag set, as any typed number would.
        viewModel.updateInput("2321")
        XCTAssertEqual(viewModel.keyboardInputText, "2321")
        XCTAssertNotNil(viewModel.validationMessage)
    }

    func testADragToTheLastSavedNumberCannotSave() {
        let title = ProgressSheetTitle(item: Self.bookItem())
        ProgressDisplayPreferences.removeMode(for: title.ref)
        defer { ProgressDisplayPreferences.removeMode(for: title.ref) }
        let viewModel = ProgressUpdateViewModel(
            title: title,
            progress: ProgressState(kind: "pages", value: 212, max: 400, unit: "page")
        )
        viewModel.setNumber(212)
        XCTAssertFalse(viewModel.canSave)
    }

    // MARK: Home hands the sheets the tile's numbers

    func testHomeGivesTheSheetsTodayAsTheTileCountsIt() throws {
        let home = Self.makeHome()
        let book = InProgressUpdatesTests.item(
            "book", "today-book", "Book", tracking: InProgressUpdatesTests.bookTracking(status: "In progress", pages: 212)
        )
        home.inProgressItems = [book]
        home.tiles.baselines = HomeViewModel.baselines(for: [book])
        XCTAssertEqual(try XCTUnwrap(home.todayPart(for: book.media.ref)), TodayPart(unit: .pages, amount: 0))

        home.tiles.todayParts = [HomeTodayStore.titleKey(for: book.media.ref): TodayPart(unit: .pages, amount: 30)]
        XCTAssertEqual(home.todayPart(for: book.media.ref), TodayPart(unit: .pages, amount: 30), "What the server counted today")

        // Pages added from the tile since Home loaded count too.
        let moved = InProgressUpdatesTests.item(
            "book", "today-book", "Book", tracking: InProgressUpdatesTests.bookTracking(status: "In progress", pages: 232)
        )
        home.inProgressItems = [moved]
        XCTAssertEqual(home.todayPart(for: book.media.ref), TodayPart(unit: .pages, amount: 50))

        // A title that is not on the rail has no tile to agree with.
        let other = InProgressUpdatesTests.item("book", "not-on-rail", "Other", tracking: InProgressUpdatesTests.bookTracking(status: "In progress", pages: 1))
        XCTAssertNil(home.todayPart(for: other.media.ref))
    }

    // MARK: Fixtures

    private static func makeHome() -> HomeViewModel {
        HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: TVTrackingSurfacesTests.profile(enabled: ["book"])),
            trackingRepository: HomeRecordingTrackingRepository(),
            activityRepository: HomeRecordingActivityRepository(),
            mediaRepository: SurfacesMediaRepository(),
            diaryRepository: SurfacesDiaryRepository(),
            onUnauthorized: {}
        )
    }

    private static func bookItem() -> Spine.LibraryItem {
        let ref = MediaRef(itemId: nil, source: "hardcover", mediaType: "book", mediaId: "sheet-today",
                           seasonNumber: nil, episodeNumber: nil)
        return Spine.LibraryItem(
            media: MediaSummary(ref: ref, title: "Book", posterUrl: nil, posterOrientation: .portrait, posterAccentColor: nil),
            tracking: TrackingState(trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil,
                                    startDate: nil, endDate: nil, notes: nil, updatedAt: nil, book: nil, game: nil, tv: nil)
        )
    }
}
