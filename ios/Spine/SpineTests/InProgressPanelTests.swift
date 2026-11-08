import XCTest
@testable import Spine

// The pure parts of the morphing In progress panel (board R4InProgress, variant A): where the buttons sit, which
// words fit, the layouts of the five worked titles, the odometer's slots and the carousel position. Nothing here
// needs a screen, a model or a network.

// MARK: - Buttons

final class PanelButtonGeometryTests: XCTestCase {
    private func make(_ litEnd: Double, width: Double = 326, quick: Bool = true) -> PanelButtonGeometry.Result {
        PanelButtonGeometry.make(litEnd: litEnd, hasQuick: quick, width: width)
    }

    func testTheWorkedRowsAt326() {
        let short = make(32.75)
        XCTAssertEqual(short.quickWidth, 36)
        XCTAssertEqual(short.updateX, 46)
        XCTAssertEqual(short.updateWidth, 280)
        XCTAssertFalse(short.updateIsIcon)

        let window = make(227.30)
        XCTAssertEqual(window.quickWidth, 227.30, accuracy: 0.001)
        XCTAssertEqual(window.updateX, 237.30, accuracy: 0.001)
        XCTAssertEqual(window.updateWidth, 88.70, accuracy: 0.001)

        let bar = make(123.88)
        XCTAssertEqual(bar.updateX, 133.88, accuracy: 0.001)
        XCTAssertEqual(bar.updateWidth, 192.12, accuracy: 0.001)

        let long = make(260.96)
        XCTAssertEqual(long.quickWidth, 260.96, accuracy: 0.001)
        XCTAssertTrue(long.updateIsIcon)
        XCTAssertEqual(long.updateX, 290)
        XCTAssertEqual(long.updateWidth, 36)

        XCTAssertEqual(make(0).quickWidth, 36)
    }

    func testNoQuickIsOneFullWidthButton() {
        let one = make(200, quick: false)
        XCTAssertEqual(one.quickWidth, 0)
        XCTAssertEqual(one.updateX, 0)
        XCTAssertEqual(one.updateWidth, 326)
        XCTAssertFalse(one.hasQuick)
    }

    func testOtherPhoneWidths() {
        let wide = make(270.56, width: 338)
        XCTAssertTrue(wide.updateIsIcon)
        XCTAssertEqual(wide.updateX, 302)
        let narrow = make(216.8, width: 311)
        XCTAssertEqual(narrow.quickWidth, 216.8, accuracy: 0.001)
        XCTAssertEqual(narrow.updateX, 226.8, accuracy: 0.001)
        XCTAssertEqual(narrow.updateWidth, 84.2, accuracy: 0.001)
        XCTAssertFalse(narrow.updateIsIcon)
    }

    func testTheBoundaries() {
        // Exactly 76 left for Update is not an icon; the quick button stops at W - 46.
        XCTAssertFalse(make(326 - 86).updateIsIcon)
        XCTAssertTrue(make(326 - 86 + 0.01).updateIsIcon)
        let widest = make(10_000)
        XCTAssertEqual(widest.quickWidth, 326 - 46)
        XCTAssertEqual(widest.updateX - widest.quickWidth, 10, "A 36 circle and the 10 gap are left")
    }

    func testInvariantsForManyLitEnds() {
        var seed: UInt64 = 12345
        func next() -> Double {
            seed = seed &* 6364136223846793005 &+ 1442695040888963407
            return Double(seed >> 33) / Double(1 << 31)
        }
        for width in [311.0, 326.0, 338.0] {
            for _ in 0 ..< 1000 {
                let result = make(next() * (width + 40), width: width)
                XCTAssertGreaterThanOrEqual(result.quickWidth, 36)
                XCTAssertGreaterThanOrEqual(result.updateX, result.quickWidth + 10 - 0.0001)
                XCTAssertEqual(result.updateX + result.updateWidth, width, accuracy: 0.0001)
                if result.updateIsIcon { XCTAssertEqual(result.updateWidth, 36, accuracy: 0.0001) }
                else { XCTAssertGreaterThanOrEqual(result.updateWidth, 76 - 0.0001) }
            }
        }
    }

    func testTheIconAndTheWordsCrossFadeOnTheWidth() {
        XCTAssertEqual(PanelButtonGeometry.textOpacity(updateWidth: 70), 1)
        XCTAssertEqual(PanelButtonGeometry.textOpacity(updateWidth: 50), 0)
        XCTAssertEqual(PanelButtonGeometry.iconOpacity(updateWidth: 36), 1)
        XCTAssertEqual(PanelButtonGeometry.iconOpacity(updateWidth: 60), 0)
        XCTAssertEqual(PanelButtonGeometry.iconOpacity(updateWidth: 280), 0)
    }
}

// MARK: - Label tiers

@MainActor
final class LabelTierTests: XCTestCase {
    private let seven: (String) -> Double = { Double($0.count) * 7.2 }

    func testATierIsChosenOnlyWhenItFitsWithItsPadding() {
        let tiers = ["+20 pages", "+20 pg", "+20"]
        // "+20 pages" is 64.8 wide and needs 92.8.
        XCTAssertEqual(PanelLabelTier.pick(tiers: tiers, quickWidth: 93, measure: seven), 0)
        XCTAssertEqual(PanelLabelTier.pick(tiers: tiers, quickWidth: 92.7, measure: seven), 1)
        // "+20 pg" needs 71.2.
        XCTAssertEqual(PanelLabelTier.pick(tiers: tiers, quickWidth: 71.3, measure: seven), 1)
        XCTAssertEqual(PanelLabelTier.pick(tiers: tiers, quickWidth: 71.1, measure: seven), 2)
    }

    func testTheLastTierIsTheFallback() {
        XCTAssertEqual(PanelLabelTier.pick(tiers: ["+20 pages", "+20 pg", "+20"], quickWidth: 36, measure: seven), 2)
        XCTAssertEqual(PanelLabelTier.pick(tiers: [], quickWidth: 36, measure: seven), 0)
    }

    func testA36PointCircleHoldsTheThreeCharacterTier() {
        for plan in [QuickActionPlan.addPages(20), .addChapters(1, unit: "chapter"), .addChapters(1, unit: "issue"),
                     .addPercent(2), .addHours(1), .tickUpNext(season: 2, episode: 8)] {
            let last = plan.labelTiers.last ?? ""
            XCTAssertLessThanOrEqual(last.count, 3, last)
            let width = PanelTextMetrics.width(
                last, font: InProgressPanelMetrics.labelUIFont, tracking: InProgressPanelMetrics.labelTracking
            )
            XCTAssertLessThan(width, 36, "\(last) must fit a 36 pt circle")
        }
    }

    func testRealWidthsAreMeasured() {
        let font = InProgressPanelMetrics.labelUIFont
        XCTAssertGreaterThan(PanelTextMetrics.width("+20 pages", font: font), PanelTextMetrics.width("+20 pg", font: font))
        XCTAssertEqual(PanelTextMetrics.width("", font: font), 0)
        XCTAssertEqual(
            PanelTextMetrics.width("7", font: InProgressPanelMetrics.whereUIFont),
            PanelTextMetrics.width("0", font: InProgressPanelMetrics.whereUIFont),
            "Tabular digits are one width"
        )
    }
}

// MARK: - Layout

@MainActor
final class MorphLayoutTests: XCTestCase {
    private let width = 326.0

    private func layout(
        _ meter: ProgressMeterModel?, place: TilePlace = .none, quick: Bool = true, hex: String? = nil
    ) -> MorphLayout {
        MorphLayout.make(meter: meter, place: place, accentHex: hex, hasQuick: quick, width: width)
    }

    private func meter(_ unit: MeterUnit, _ from: Int, _ to: Int, _ total: Int) -> ProgressMeterModel {
        ProgressMeterModel.make(unit: unit, from: from, to: to, total: total)!
    }

    // MARK: The five worked titles

    func testTenTicksOfASeason() {
        let l = layout(meter(.episodes, 6, 7, 10))
        let sw = (326.0 - 27) / 10
        XCTAssertEqual(sw, 29.9, accuracy: 0.0001)
        XCTAssertEqual(l.bar, 0)
        XCTAssertEqual(l.litStart, 194.40, accuracy: 0.01)
        XCTAssertEqual(l.litEnd, 227.30, accuracy: 0.01)
        XCTAssertEqual(l.glow, 0.6)
        XCTAssertEqual(l.lineOpacity, 0)
        XCTAssertEqual(l.cells[0].x, 0)
        XCTAssertEqual(l.cells[0].w, 29.9, accuracy: 0.0001)
        XCTAssertEqual(l.cells[9].x, 9 * (29.9 + 3), accuracy: 0.0001)
        // Before, today, rest.
        XCTAssertEqual(l.cells[5].a, 0.55, accuracy: 0.0001)
        XCTAssertEqual(l.cells[6].a, 1)
        XCTAssertEqual(l.cells[6].r, l.accentR)
        XCTAssertEqual(l.cells[7].a, 0.16, accuracy: 0.0001)
        // Hidden cells: zero width, spread along the bar.
        XCTAssertEqual(l.cells[10].w, 0)
        XCTAssertEqual(l.cells[10].opacity, 0)
        XCTAssertEqual(l.cells[10].x, 10.0 / 30 * 326, accuracy: 0.0001)
        XCTAssertEqual(l.cells[0].radiusLeading, 3)
        XCTAssertEqual(l.cells[0].radiusTrailing, 3)
    }

    func testAWindowOfALongSeries() {
        let model = meter(.episodes, 143, 145, 232)
        guard case let .window(first, _, _) = TileGeometry.panelMeter(model)!.kind else { return XCTFail("a window") }
        XCTAssertEqual(first, 130)
        let l = layout(model)
        XCTAssertEqual(l.litStart, 227.30, accuracy: 0.01)
        XCTAssertEqual(l.litEnd, 260.20, accuracy: 0.01)
        XCTAssertEqual(l.lineOpacity, 1)
        XCTAssertEqual(l.lineAt, 203.75, accuracy: 0.01)
        XCTAssertEqual(l.cells[19].x + l.cells[19].w, 326, accuracy: 0.0001)
        // The left edge fades in.
        XCTAssertEqual(l.cells[0].opacity, TileGeometry.windowFade(index: 0, count: 20), accuracy: 0.0001)
        XCTAssertLessThan(l.cells[0].opacity, 0.2)
        XCTAssertEqual(l.cells[10].opacity, 1)
    }

    func testABarOfAThousandPages() {
        let l = layout(meter(.pages, 303, 333, 416))
        XCTAssertEqual(l.bar, 1)
        XCTAssertEqual(l.litStart, 237.45, accuracy: 0.01)
        XCTAssertEqual(l.litEnd, 260.96, accuracy: 0.01)
        XCTAssertEqual(l.cells[0].radiusLeading, 5)
        XCTAssertEqual(l.cells[0].radiusTrailing, 0)
        XCTAssertEqual(l.cells[29].radiusLeading, 0)
        XCTAssertEqual(l.cells[29].radiusTrailing, 5)
        XCTAssertEqual(l.cells[10].radiusLeading, 0)
        XCTAssertEqual(l.cells[0].w, 326.0 / 30 + 0.4, accuracy: 0.0001)
        XCTAssertEqual(l.cells[29].w, 326.0 / 30, accuracy: 0.0001)
        XCTAssertEqual(l.cells[29].x + l.cells[29].w, 326, accuracy: 0.0001)
    }

    func testAPercentBar() {
        let l = layout(meter(.percent, 34, 38, 100))
        XCTAssertEqual(l.litStart, 110.84, accuracy: 0.01)
        XCTAssertEqual(l.litEnd, 123.88, accuracy: 0.01)
    }

    func testABookJustStarted() {
        let l = layout(meter(.pages, 33, 45, 448))
        XCTAssertEqual(l.litStart, 24.01, accuracy: 0.01)
        XCTAssertEqual(l.litEnd, 32.75, accuracy: 0.01)
        XCTAssertEqual(l.quickWidth, 36, "Under 36 pt the button is a circle")
    }

    func testNothingLitHasNoGlow() {
        let l = layout(meter(.episodes, 4, 4, 10))
        XCTAssertEqual(l.glow, 0)
        XCTAssertEqual(l.litStart, l.litEnd)
    }

    func testNoMeterHidesEveryCell() {
        let l = layout(nil)
        XCTAssertEqual(l.glow, 0)
        XCTAssertEqual(l.bar, 0)
        XCTAssertEqual(l.lineOpacity, 0)
        XCTAssertTrue(l.cells.allSatisfy { $0.w == 0 && $0.opacity == 0 })
    }

    func testDigitsAndButtonsComeFromThePlaceAndThePlan() {
        let place = TilePlace(prefix: "S2 \u{00B7} ", value: 7, joiner: " of ", total: 10, suffix: " episodes")
        let l = layout(meter(.episodes, 6, 7, 10), place: place)
        XCTAssertEqual(l.a, 7)
        XCTAssertEqual(l.aShown, 1)
        XCTAssertEqual(l.b, 10)
        XCTAssertEqual(l.bShown, 1)
        XCTAssertEqual(l.quickOn, 1)
        XCTAssertEqual(l.quickWidth, 227.30, accuracy: 0.01)
        XCTAssertEqual(l.updateX, 237.30, accuracy: 0.01)
        XCTAssertEqual(l.updatePrimary, 0)

        let alone = layout(meter(.episodes, 6, 7, 10), place: .none, quick: false)
        XCTAssertEqual(alone.aShown, 0)
        XCTAssertEqual(alone.bShown, 0)
        XCTAssertEqual(alone.quickOn, 0)
        XCTAssertEqual(alone.quickWidth, 0)
        XCTAssertEqual(alone.updateX, 0)
        XCTAssertEqual(alone.updateWidth, 326)
        XCTAssertEqual(alone.updatePrimary, 1)
    }

    func testTheAccentIsThePosterPastelOrNearWhite() {
        let none = layout(nil)
        XCTAssertEqual(none.accentR, 0.97, accuracy: 0.001)
        XCTAssertEqual(none.accentG, 0.97, accuracy: 0.001)
        let red = layout(nil, hex: "#C03030")
        XCTAssertGreaterThan(red.accentR, red.accentG)
        XCTAssertEqual(red.accentR, 0.97, accuracy: 0.001, "Brightness is 0.97")
    }

    func testHsbToRgb() {
        let (r, g, b) = MorphLayout.rgb(hue: 0, saturation: 0, brightness: 0.5)
        XCTAssertEqual([r, g, b], [0.5, 0.5, 0.5])
        let green = MorphLayout.rgb(hue: 1.0 / 3, saturation: 1, brightness: 1)
        XCTAssertEqual(green.0, 0, accuracy: 0.0001)
        XCTAssertEqual(green.1, 1, accuracy: 0.0001)
        XCTAssertEqual(green.2, 0, accuracy: 0.0001)
    }

    // MARK: Vector

    func testTheVectorRoundTrips() {
        let l = layout(meter(.episodes, 6, 7, 10), place: TilePlace(prefix: "", value: 7, joiner: "", total: nil, suffix: ""))
        XCTAssertEqual(l.values.count, 288)
        XCTAssertEqual(MorphLayout(values: l.values), l)
        let other = layout(meter(.pages, 303, 333, 416))
        let vector = MorphVector(lo: l.values, hi: other.values, t: 0.25)
        XCTAssertEqual(vector.values.count, 577)
        XCTAssertEqual(MorphLayout(values: vector.lo), l)
        XCTAssertEqual(MorphLayout(values: vector.hi), other)
        XCTAssertEqual(vector.t, 0.25)
        XCTAssertEqual(MorphLayout.resolve(MorphVector(lo: l.values, hi: other.values, t: 0)), l)
        XCTAssertEqual(MorphLayout.resolve(MorphVector(lo: l.values, hi: other.values, t: 1)), other)
        XCTAssertEqual(MorphLayout.resolve(.zero), MorphLayout())
    }

    func testTheZeroVectorActsAsZeros() {
        var vector = MorphVector(values: [1, 2, 3])
        vector += .zero
        XCTAssertEqual(vector.values, [1, 2, 3])
        var empty = MorphVector.zero
        empty += vector
        XCTAssertEqual(empty.values, [1, 2, 3])
        var delta = vector
        delta -= MorphVector(values: [1, 1, 1])
        delta.scale(by: 0.5)
        XCTAssertEqual(delta.values, [0, 0.5, 1])
        XCTAssertEqual(MorphVector.zero.magnitudeSquared, 0)
        var scaled = MorphVector.zero
        scaled.scale(by: 3)
        XCTAssertEqual(scaled, .zero)
    }

    // MARK: Blend

    func testBlendIsExactAtTheEnds() {
        let lo = layout(meter(.episodes, 6, 7, 10), place: TilePlace(prefix: "", value: 7, joiner: "", total: nil, suffix: ""))
        let hi = layout(meter(.pages, 303, 333, 416))
        XCTAssertEqual(MorphLayout.blend(lo, hi, 0), lo)
        XCTAssertEqual(MorphLayout.blend(lo, hi, 1), hi)
        XCTAssertEqual(MorphLayout.blend(lo, hi, -3), lo)
        XCTAssertEqual(MorphLayout.blend(lo, hi, 4), hi)
    }

    func testEveryFieldMovesMonotonicallyFromLoToHi() {
        let lo = layout(meter(.episodes, 6, 7, 10), place: TilePlace(prefix: "", value: 7, joiner: " of ", total: 10, suffix: ""))
        let hi = layout(meter(.pages, 303, 333, 416), place: TilePlace(prefix: "", value: 333, joiner: " of ", total: 416, suffix: ""))
        var previous = lo.values
        for step in 1 ... 100 {
            let now = MorphLayout.blend(lo, hi, Double(step) / 100).values
            for i in now.indices {
                let (a, b) = (lo.values[i], hi.values[i])
                if a <= b { XCTAssertGreaterThanOrEqual(now[i], previous[i] - 1e-9, "field \(i) at step \(step)") }
                else { XCTAssertLessThanOrEqual(now[i], previous[i] + 1e-9, "field \(i) at step \(step)") }
            }
            previous = now
        }
    }

    func testTheCascadeStartsWithCellZeroAndEndsWithCellTwentyNine() {
        XCTAssertEqual(MorphLayout.cellProgress(cell: 0, t: 0), 0)
        XCTAssertEqual(MorphLayout.cellProgress(cell: 0, t: 2.0 / 3), 1, accuracy: 0.0001)
        XCTAssertEqual(MorphLayout.cellProgress(cell: 29, t: 1.0 / 3), 0, accuracy: 0.0001)
        XCTAssertEqual(MorphLayout.cellProgress(cell: 29, t: 1), 1, accuracy: 0.0001)
        for t in stride(from: 0.05, to: 1, by: 0.05) {
            XCTAssertGreaterThanOrEqual(
                MorphLayout.cellProgress(cell: 0, t: t), MorphLayout.cellProgress(cell: 29, t: t)
            )
            for j in 1 ..< 30 {
                XCTAssertGreaterThanOrEqual(
                    MorphLayout.cellProgress(cell: j - 1, t: t), MorphLayout.cellProgress(cell: j, t: t) - 1e-12
                )
            }
        }
        let lo = layout(meter(.episodes, 6, 7, 10))
        let hi = layout(meter(.pages, 303, 333, 416))
        let mid = MorphLayout.blend(lo, hi, 0.5)
        let first = abs(mid.cells[0].w - hi.cells[0].w) / max(abs(lo.cells[0].w - hi.cells[0].w), 1)
        let last = abs(mid.cells[29].w - hi.cells[29].w) / max(abs(lo.cells[29].w - hi.cells[29].w), 1)
        XCTAssertLessThan(first, last, "Cell 0 is further along than cell 29 halfway")
    }

    func testANumberThatFadesInDoesNotRollUpFromZero() {
        let none = layout(meter(.episodes, 6, 7, 10), place: .none)
        let some = layout(meter(.episodes, 6, 7, 10), place: TilePlace(prefix: "", value: 412, joiner: "", total: nil, suffix: "%"))
        let mid = MorphLayout.blend(none, some, 0.5)
        XCTAssertEqual(mid.a, 412)
        XCTAssertEqual(mid.aShown, 0.5, accuracy: 0.0001)
        let back = MorphLayout.blend(some, none, 0.5)
        XCTAssertEqual(back.a, 412)
    }

    // MARK: Sweep

    func testTheSweepCoversWhatWasLitOnABarAndTheNewTicksOnTicks() {
        // A bar: from the old lit end to the new one.
        let old = spec(meter(.pages, 100, 120, 400), id: "a")
        let new = spec(meter(.pages, 100, 140, 400), id: "a")
        let bar = SweepSpan.make(old: old, new: new)
        XCTAssertEqual(bar?.from ?? 0, old.layout.litEnd, accuracy: 0.0001)
        XCTAssertEqual(bar?.to ?? 0, new.layout.litEnd, accuracy: 0.0001)
        // Ticks: one tick added ends on the new lit end and is one tick wide.
        let tOld = spec(meter(.episodes, 5, 6, 10), id: "t")
        let tNew = spec(meter(.episodes, 5, 7, 10), id: "t")
        let ticks = SweepSpan.make(old: tOld, new: tNew)
        XCTAssertEqual(ticks?.to ?? 0, tNew.layout.litEnd, accuracy: 0.0001)
        XCTAssertEqual(ticks?.width ?? 0, 29.9, accuracy: 0.01)
        // Another title, or no move, or a move back: none.
        XCTAssertNil(SweepSpan.make(old: old, new: spec(meter(.pages, 100, 140, 400), id: "b")))
        XCTAssertNil(SweepSpan.make(old: new, new: new))
        XCTAssertNil(SweepSpan.make(old: new, new: old))
    }

    private func spec(_ meter: ProgressMeterModel, id: String) -> PanelTitleSpec {
        let ref = MediaRef(itemId: nil, source: "tmdb", mediaType: "book", mediaId: id, seasonNumber: nil, episodeNumber: nil)
        let item = Spine.LibraryItem(
            media: MediaSummary(ref: ref, title: "T", posterUrl: nil, posterOrientation: .portrait),
            tracking: TrackingState(
                trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil, startDate: nil,
                endDate: nil, notes: nil, updatedAt: nil
            )
        )
        let detail = TileDetailModel(
            title: "T", todayText: nil, place: .none, meter: meter, accentHex: nil, primary: .addPages(20),
            secondaryTitle: "Update", accessibilityLabel: "T"
        )
        return PanelTitleSpec.make(item: item, detail: detail, width: 326)
    }
}

// MARK: - Odometer

final class OdometerSlotsTests: XCTestCase {
    private func make(_ x: Double, shown: Double = 1) -> OdometerSlots {
        OdometerSlots.make(x: x, shown: shown, digitWidth: 8, commaWidth: 4)
    }

    private func slot(_ slots: OdometerSlots, place: Int) -> OdometerSlots.Slot {
        slots.slots.first { $0.place == place }!
    }

    func testASingleDigit() {
        let s = make(7)
        XCTAssertEqual(s.slots.map(\.place), [4, 3, 2, 1, 0])
        XCTAssertEqual(s.slots.map(\.width), [0, 0, 0, 0, 8])
        XCTAssertEqual(slot(s, place: 0).roll, 7)
        XCTAssertEqual(s.width, 8)
        XCTAssertEqual(s.commaWidth, 0)
    }

    func testATensDigitGrowsInAsTheNumberPassesNine() {
        let s = make(9.5)
        XCTAssertEqual(slot(s, place: 1).opacity, 0.5, accuracy: 0.0001)
        XCTAssertEqual(slot(s, place: 1).width, 4, accuracy: 0.0001)
        XCTAssertEqual(slot(s, place: 1).roll, 0.5, accuracy: 0.0001)
        XCTAssertEqual(slot(s, place: 0).roll, 9.5, accuracy: 0.0001)
    }

    func testAHundredsDigit() {
        let s = make(99.5)
        XCTAssertEqual(slot(s, place: 2).opacity, 0.5, accuracy: 0.0001)
        XCTAssertEqual(slot(s, place: 2).roll, 0.95, accuracy: 0.0001)
        XCTAssertEqual(slot(s, place: 1).opacity, 1)
        XCTAssertEqual(slot(s, place: 1).roll, 9.5, accuracy: 0.0001)
        XCTAssertEqual(slot(s, place: 0).roll, 9.5, accuracy: 0.0001)
    }

    func testAThousandsDigitAndTheComma() {
        let s = make(1089)
        XCTAssertEqual(s.commaWidth, 4)
        XCTAssertEqual(slot(s, place: 4).width, 0)
        XCTAssertEqual(slot(s, place: 3).roll, 1)
        XCTAssertEqual(slot(s, place: 2).roll, 0)
        XCTAssertEqual(slot(s, place: 1).roll, 8)
        XCTAssertEqual(slot(s, place: 0).roll, 9)
        XCTAssertEqual(s.width, 4 * 8 + 4)
        // The comma grows in with the thousands digit.
        XCTAssertEqual(make(999.5).commaWidth, 2, accuracy: 0.0001)
    }

    func testZeroIsShownAndNothingShownHasNoWidth() {
        let zero = make(0)
        XCTAssertEqual(zero.width, 8)
        XCTAssertEqual(slot(zero, place: 0).roll, 0)
        XCTAssertEqual(make(1089, shown: 0).width, 0)
        XCTAssertEqual(make(5, shown: 0.5).width, 4)
    }

    func testTheWholeNumberGrowsMonotonicallyAndNoSlotIsWiderThanADigit() {
        var previous = 0.0
        var x = 0.0
        while x <= 20_000 {
            let s = make(x)
            XCTAssertGreaterThanOrEqual(s.width, previous - 1e-9, "x = \(x)")
            XCTAssertTrue(s.slots.allSatisfy { $0.width <= 8 + 1e-9 })
            previous = s.width
            x += 0.5
        }
    }

    func testBeyondFivePlacesItClamps() {
        XCTAssertEqual(make(250_000).width, make(99_999).width)
    }

    // MARK: In-place change

    func testADigitAtRestIsSolidAndAloneAndTheNextDigitIsNotShown() {
        let rest = OdometerSlots.fade(roll: 7)
        XCTAssertEqual(rest, OdometerSlots.Fade(base: 7, next: 8, amount: 0))
        // The first and last stretch of a step hold the digit still.
        XCTAssertEqual(OdometerSlots.fade(roll: 7.1).amount, 0)
        XCTAssertEqual(OdometerSlots.fade(roll: 7.9).amount, 1)
    }

    func testTheCrossFadeIsEasedAndHalfwayAtTheMiddleOfAStep() {
        XCTAssertEqual(OdometerSlots.fade(roll: 7.5).amount, 0.5, accuracy: 0.0001)
        var previous = 0.0
        var roll = 3.0
        while roll < 4 {
            let amount = OdometerSlots.fade(roll: roll).amount
            XCTAssertGreaterThanOrEqual(amount, previous - 1e-12, "the next digit only ever fades in")
            XCTAssertTrue((0 ... 1).contains(amount))
            previous = amount
            roll += 0.01
        }
    }

    func testNineFadesIntoZeroAndTheTensCarryFadesWithIt() {
        let ones = OdometerSlots.fade(roll: 9.5)
        XCTAssertEqual(ones.base, 9)
        XCTAssertEqual(ones.next, 0)
        // 19.5: the tens digit is on 1 and moves to 2 during the last unit, as the ones digit goes 9 to 0.
        let s = OdometerSlots.make(x: 19.5, shown: 1, digitWidth: 8, commaWidth: 4)
        let tens = OdometerSlots.fade(roll: s.slots.first { $0.place == 1 }!.roll)
        XCTAssertEqual(tens.base, 1)
        XCTAssertEqual(tens.next, 2)
        XCTAssertEqual(tens.amount, 0.5, accuracy: 0.0001)
    }

    func testTheNumberNeverChangesWidthJustBecauseADigitIsFading() {
        // Only a digit growing in changes the width; a digit fading into the next is the same width at every roll.
        var roll = 4.0
        let widths = Set((0 ..< 100).map { step -> Double in
            roll = 4.0 + Double(step) / 100
            return OdometerSlots.make(x: roll, shown: 1, digitWidth: 8, commaWidth: 4).width
        })
        XCTAssertEqual(widths, [8])
    }
}

// MARK: - Position

final class PanelPositionTests: XCTestCase {
    func testSplit() {
        XCTAssertEqual(PanelPosition.split(0, count: 5), .init(lo: 0, hi: 1, t: 0))
        let quarter = PanelPosition.split(2.25, count: 5)
        XCTAssertEqual(quarter.lo, 2)
        XCTAssertEqual(quarter.hi, 3)
        XCTAssertEqual(quarter.t, 0.25, accuracy: 0.0001)
        let almost = PanelPosition.split(2.9999, count: 5)
        XCTAssertEqual(almost.lo, 2)
        XCTAssertEqual(almost.t, 0.9999, accuracy: 0.0001)
        XCTAssertEqual(PanelPosition.split(4, count: 5), .init(lo: 4, hi: 4, t: 0))
    }

    func testSplitClampsAtBothEnds() {
        XCTAssertEqual(PanelPosition.split(-0.4, count: 5), .init(lo: 0, hi: 1, t: 0))
        XCTAssertEqual(PanelPosition.split(9, count: 5), .init(lo: 4, hi: 4, t: 0))
        XCTAssertEqual(PanelPosition.split(0.5, count: 1), .init(lo: 0, hi: 0, t: 0))
        XCTAssertEqual(PanelPosition.split(0.5, count: 0), .init(lo: 0, hi: 0, t: 0))
    }

    func testResolve() {
        XCTAssertEqual(PanelPosition.resolve(reported: nil, focusIndex: 3, count: 5), 3, "Nothing reported yet")
        XCTAssertEqual(PanelPosition.resolve(reported: nil, focusIndex: nil, count: 5), 0)
        XCTAssertEqual(PanelPosition.resolve(reported: 2.4, focusIndex: 2, count: 5), 2.4)
        XCTAssertEqual(PanelPosition.resolve(reported: 2.4, focusIndex: 3, count: 5), 2.4, "Within one poster of the focus")
        XCTAssertEqual(PanelPosition.resolve(reported: 4, focusIndex: 1, count: 5), 1, "A stale value loses to the focus")
        XCTAssertEqual(PanelPosition.resolve(reported: 4.6, focusIndex: 4, count: 5), 4, "Clamped to the list")
        XCTAssertEqual(PanelPosition.resolve(reported: .nan, focusIndex: 2, count: 5), 2)
        XCTAssertEqual(PanelPosition.resolve(reported: 1, focusIndex: 1, count: 0), 0)
    }

    func testNearestKeepsThePreviousPosterInsideTheHysteresis() {
        // From poster 2, the midpoint to 3 is 2.5; it takes 2.56 to flip.
        XCTAssertEqual(PanelPosition.nearest(2.55, previous: 2, count: 5), 2)
        XCTAssertEqual(PanelPosition.nearest(2.57, previous: 2, count: 5), 3)
        // And back: from 3, it takes less than 2.44.
        XCTAssertEqual(PanelPosition.nearest(2.45, previous: 3, count: 5), 3)
        XCTAssertEqual(PanelPosition.nearest(2.43, previous: 3, count: 5), 2)
        XCTAssertEqual(PanelPosition.nearest(0, previous: 7, count: 5), 0, "A stale previous is clamped first")
        XCTAssertEqual(PanelPosition.nearest(3.2, previous: 0, count: 5), 3)
    }

    func testSettled() {
        XCTAssertTrue(PanelPosition.isSettled(2, near: 2))
        XCTAssertTrue(PanelPosition.isSettled(2.11, near: 2))
        XCTAssertFalse(PanelPosition.isSettled(2.13, near: 2))
        XCTAssertTrue(PanelPosition.isSettled(1.9, near: 2))
        XCTAssertFalse(PanelPosition.isSettled(1.5, near: 2))
    }
}

// MARK: - Metrics

final class InProgressPanelMetricsTests: XCTestCase {
    func testThePanelFillsTheOpenTileExactly() {
        XCTAssertEqual(
            TileGeometry.panelTop + InProgressPanelMetrics.height + InProgressPanelMetrics.bottomMargin,
            HomeDesign.Metric.tileOpenHeight
        )
        XCTAssertGreaterThanOrEqual(InProgressPanelMetrics.bottomMargin, 12)
    }

    func testTheRowsStackInsideThePanel() {
        XCTAssertLessThanOrEqual(InProgressPanelMetrics.titleRowHeight, InProgressPanelMetrics.meterTop)
        XCTAssertLessThanOrEqual(
            InProgressPanelMetrics.meterTop + InProgressPanelMetrics.lineTop + InProgressPanelMetrics.lineHeight,
            InProgressPanelMetrics.buttonsTop
        )
        XCTAssertEqual(InProgressPanelMetrics.buttonsTop + InProgressPanelMetrics.buttonHeight, InProgressPanelMetrics.height)
        XCTAssertEqual(InProgressPanelMetrics.cellCount, ProgressMeterModel.maxTicks)
        XCTAssertEqual(InProgressPanelMetrics.hitSlop * 2 + InProgressPanelMetrics.buttonHeight, 44)
        XCTAssertLessThan(InProgressPanelMetrics.hitSlop * 2, PanelButtonGeometry.gap, "The two hit areas never overlap")
    }
}
