import XCTest
@testable import Spine

// The pure parts of the title that changes in place as you swipe (Panel/TitleDissolve.swift): where each letter sits,
// how a long title is cut, and which letters have changed at a point of the swipe. Nothing here needs a screen.

final class TitleMetricsTests: XCTestCase {
    /// Ten points a character, so a position is the index times ten.
    private let ten: (String) -> Double = { Double($0.count) * 10 }

    func testOneLetterPerCharacterAndEmojiStayWhole() {
        XCTAssertEqual(TitleMetrics.units(of: "Dune"), ["D", "u", "n", "e"])
        XCTAssertEqual(TitleMetrics.units(of: "A \u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}!").count, 4, "a family emoji is one letter")
        XCTAssertEqual(TitleMetrics.units(of: ""), [])
    }

    func testALetterThatJoinsItsNeighborsStaysInOneWord() {
        // Arabic letters change shape by their neighbors, so a word is drawn whole; the space still breaks it.
        let units = TitleMetrics.units(of: "\u{0645}\u{0631}\u{062D}\u{0628}\u{0627} Dune")
        XCTAssertEqual(units.count, 1 + 1 + 4)
        XCTAssertEqual(units[0].unicodeScalars.count, 5)
        XCTAssertEqual(units[1], " ")
    }

    func testEachLetterStartsWhereTheLineUpToItEndsLessItsOwnWidth() {
        let full = TitleMetrics.layout("Dune", measure: ten)
        XCTAssertEqual(full.glyphs.map(\.x), [0, 10, 20, 30])
        XCTAssertEqual(full.glyphs.map(\.width), [10, 10, 10, 10])
        XCTAssertEqual(full.total, 40)
        XCTAssertEqual(full.ends, [10, 20, 30, 40])
    }

    func testPairKerningMovesTheSecondLetterAndTheTotal() {
        // "AV" is 2 points tighter than A and V apart, as a real font does.
        let kerned: (String) -> Double = { Double($0.count) * 10 - ($0.contains("AV") ? 2 : 0) }
        let full = TitleMetrics.layout("AV", measure: kerned)
        XCTAssertEqual(full.glyphs[1].x, 8)
        XCTAssertEqual(full.total, 18)
    }

    func testATitleThatFitsIsWhole() {
        let full = TitleMetrics.layout("Dune", measure: ten)
        let ellipsis = TitleGlyph(text: "\u{2026}", x: 0, width: 10)
        let line = TitleMetrics.line(full, available: 40, ellipsis: ellipsis)
        XCTAssertEqual(line.text, "Dune")
        XCTAssertEqual(line.width, 40)
    }

    func testALongTitleEndsInDotsAndFitsExactly() {
        let full = TitleMetrics.layout("ABCDEFGH", measure: ten)
        let ellipsis = TitleGlyph(text: "\u{2026}", x: 0, width: 10)
        let line = TitleMetrics.line(full, available: 55, ellipsis: ellipsis)
        XCTAssertEqual(line.text, "ABCD\u{2026}")
        XCTAssertEqual(line.glyphs.last?.x, 40)
        XCTAssertEqual(line.width, 50)
        XCTAssertLessThanOrEqual(line.width, 55)
    }

    func testNoSpaceIsLeftBeforeTheDotsAndNothingFitsIsEmpty() {
        let full = TitleMetrics.layout("AB CDEFG", measure: ten)
        let ellipsis = TitleGlyph(text: "\u{2026}", x: 0, width: 10)
        let line = TitleMetrics.line(full, available: 45, ellipsis: ellipsis)
        XCTAssertEqual(line.text, "AB\u{2026}")
        XCTAssertEqual(TitleMetrics.line(full, available: 5, ellipsis: ellipsis).glyphs, [])
        XCTAssertEqual(TitleMetrics.line(full, available: 0, ellipsis: ellipsis).width, 0)
    }

    func testRealFontsGiveIncreasingPositionsAndACutLineThatFits() {
        let font = UIFont.systemFont(ofSize: 19, weight: .heavy)
        let full = TitleMetrics.layout("The Lord of the Rings", font: font, tracking: -0.3)
        XCTAssertEqual(full.glyphs.count, 21)
        XCTAssertEqual(full.glyphs[0].x, 0, accuracy: 0.5)
        for index in 1 ..< full.glyphs.count {
            XCTAssertGreaterThan(full.glyphs[index].x, full.glyphs[index - 1].x)
        }
        XCTAssertEqual(full.total, full.ends.last ?? -1, accuracy: 0.0001)
        XCTAssertEqual(
            TitleMetrics.layout("The Lord of the Rings", font: font, tracking: -0.3), full, "the second ask is the cached one"
        )
        let line = TitleMetrics.line("The Lord of the Rings", font: font, tracking: -0.3, available: full.total - 30)
        XCTAssertTrue(line.text.hasSuffix("\u{2026}"))
        XCTAssertLessThanOrEqual(line.width, full.total - 30 + 0.5)
    }
}

final class TitleSweepTests: XCTestCase {
    private let ten: (String) -> Double = { Double($0.count) * 10 }

    private func line(_ text: String) -> TitleLine {
        let full = TitleMetrics.layout(text, measure: ten)
        return TitleLine(glyphs: full.glyphs, width: full.total)
    }

    // MARK: Shape

    func testTheSwipeHoldsTheTitleAtBothEndsAndNeverGoesBackwards() {
        XCTAssertEqual(TitleSweep.shape(0), 0)
        XCTAssertEqual(TitleSweep.shape(TitleSweep.rest), 0)
        XCTAssertEqual(TitleSweep.shape(1 - TitleSweep.rest), 1, accuracy: 1e-9)
        XCTAssertEqual(TitleSweep.shape(1), 1)
        var previous = 0.0
        for step in 0 ... 200 {
            let s = TitleSweep.shape(Double(step) / 200)
            XCTAssertGreaterThanOrEqual(s, previous)
            previous = s
        }
    }

    func testTheRightmostLetterChangesFirstAndTheLeftmostLast() {
        XCTAssertEqual(TitleSweep.order(center: 100, minX: 0, maxX: 100), 0)
        XCTAssertEqual(TitleSweep.order(center: 0, minX: 0, maxX: 100), 1)
        XCTAssertEqual(TitleSweep.order(center: 50, minX: 0, maxX: 100), 0.5)
        XCTAssertEqual(TitleSweep.order(center: 5, minX: 5, maxX: 5), 0, "a one-point span does not divide by zero")
        // Early in the swipe the first letter has started and the last has not.
        XCTAssertGreaterThan(TitleSweep.progress(order: 0, s: 0.2), 0)
        XCTAssertEqual(TitleSweep.progress(order: 1, s: 0.2), 0)
        // At the end every letter is done, at the start none is.
        for order in stride(from: 0.0, through: 1.0, by: 0.1) {
            XCTAssertEqual(TitleSweep.progress(order: order, s: 0), 0)
            XCTAssertEqual(TitleSweep.progress(order: order, s: 1), 1, accuracy: 1e-9)
        }
    }

    func testEveryLetterOnlyEverMovesForwardAsTheSwipeGoes() {
        for order in stride(from: 0.0, through: 1.0, by: 0.25) {
            var previous = 0.0
            for step in 0 ... 100 {
                let g = TitleSweep.progress(order: order, s: Double(step) / 100)
                XCTAssertGreaterThanOrEqual(g, previous)
                previous = g
            }
        }
        XCTAssertEqual(TitleSweep.ease(0), 0)
        XCTAssertEqual(TitleSweep.ease(1), 1)
        XCTAssertEqual(TitleSweep.ease(0.5), 0.5)
    }

    func testBlurComesInStepsAndTinyBlurIsNone() {
        XCTAssertEqual(TitleSweep.quantized(blur: 0.2), 0)
        XCTAssertEqual(TitleSweep.quantized(blur: 0.4), 0)
        XCTAssertEqual(TitleSweep.quantized(blur: 1.0), 0.7, accuracy: 1e-9)
        XCTAssertEqual(TitleSweep.quantized(blur: 2.8), 2.8, accuracy: 1e-9)
        let levels = Set(stride(from: 0.0, through: 2.8, by: 0.05).map { TitleSweep.quantized(blur: $0) })
        XCTAssertLessThanOrEqual(levels.count, 6, "a handful of filters at most")
    }

    // MARK: The plan

    func testAtRestIsTheOldTitleAndAtTheEndIsTheNewOneEachAsOnePiece() {
        let old = line("Dune")
        let new = line("Arrival")
        let start = TitleSweep.ops(old: old, oldOffset: 8, new: new, newOffset: 8, s: 0)
        XCTAssertEqual(start.map(\.text), ["Dune"])
        XCTAssertEqual(start.first?.x, 8)
        XCTAssertEqual(start.first?.isRun, true)
        let end = TitleSweep.ops(old: old, oldOffset: 8, new: new, newOffset: 8, s: 1)
        XCTAssertEqual(end.map(\.text), ["Arrival"])
        XCTAssertEqual(end.first?.alpha, 1)
    }

    func testTheSameTitleNeverChanges() {
        let same = TitleSweep.ops(old: line("Dune"), oldOffset: 0, new: line("Dune"), newOffset: 0, s: 0.5)
        XCTAssertEqual(same.map(\.text), ["Dune"])
        XCTAssertEqual(same.first?.alpha, 1)
        XCTAssertEqual(same.first?.blur, 0)
    }

    func testMidSwipeTheRightHasChangedTheLeftHasNotAndTheMiddleIsBetween() {
        // Both four letters wide in the same place, so letter i of the old meets letter i of the new.
        let ops = TitleSweep.ops(old: line("AAAA"), oldOffset: 0, new: line("BBBB"), newOffset: 0, s: 0.5)
        // Letter 0 has not started: it is still part of the old run. Letter 3 is done: it is part of the new run.
        XCTAssertEqual(ops.first?.text, "A")
        XCTAssertEqual(ops.first?.isRun, true)
        XCTAssertEqual(ops.first?.x, 0)
        XCTAssertEqual(ops.last?.text, "B")
        XCTAssertEqual(ops.last?.isRun, true)
        XCTAssertEqual(ops.last?.x, 30)
        // The two in between are mid-change: letters, not runs, and each pair's opacities add up to one.
        let moving = ops.filter { !$0.isRun }
        XCTAssertEqual(moving.count, 4)
        for x in [10.0, 20.0] {
            let pair = moving.filter { $0.x == x }
            XCTAssertEqual(pair.count, 2)
            XCTAssertEqual(pair.map(\.alpha).reduce(0, +), 1, accuracy: 1e-9)
            let old = pair.first { $0.text == "A" }
            let new = pair.first { $0.text == "B" }
            XCTAssertNotNil(old)
            XCTAssertNotNil(new)
            // The old one blurs out and grows a little; the new one sharpens and settles to its size.
            XCTAssertEqual(old?.scale ?? 0, 1 + 0.05 * (1 - (old?.alpha ?? 0)), accuracy: 1e-9)
            XCTAssertLessThan(new?.scale ?? 2, 1)
            XCTAssertGreaterThan(new?.glint ?? 0, 0, "the front of the sweep glints")
            XCTAssertEqual(old?.glint, 0)
        }
        // The letter nearer the right has got further.
        let left = moving.first { $0.x == 10 && $0.text == "B" }
        let right = moving.first { $0.x == 20 && $0.text == "B" }
        XCTAssertGreaterThan(right?.alpha ?? 0, left?.alpha ?? 1)
    }

    func testEveryMovingLetterIsPartlyThereAndNoLetterIsDrawnTwiceOnOneSide() {
        for step in 1 ..< 100 {
            let s = Double(step) / 100
            let ops = TitleSweep.ops(old: line("AAAAAAAA"), oldOffset: 0, new: line("BBBBBBBB"), newOffset: 0, s: s)
            for op in ops where !op.isRun {
                XCTAssertGreaterThan(op.alpha, 0)
                XCTAssertLessThanOrEqual(op.alpha, 1)
                XCTAssertLessThanOrEqual(op.blur, TitleSweep.maxBlur + 1e-9)
            }
            // Each side shows each of its letters at most once.
            let text = ops.map(\.text).joined()
            XCTAssertLessThanOrEqual(text.filter { $0 == "A" }.count, 8)
            XCTAssertLessThanOrEqual(text.filter { $0 == "B" }.count, 8)
        }
    }

    func testALetterThatIsTheSameInTheSamePlaceStaysPut() {
        // "Dune Part Two" and "Dune Messiah" share "Dune ": it never blurs or fades.
        let ops = TitleSweep.ops(
            old: line("Dune Part Two"), oldOffset: 0, new: line("Dune Messiah"), newOffset: 0, s: 0.5
        )
        for op in ops where !op.isRun {
            XCTAssertGreaterThanOrEqual(op.x, 50, "\(op.text) is inside the shared \"Dune \"")
        }
        let first = ops.first { $0.isRun && $0.x == 0 }
        XCTAssertNotNil(first)
        XCTAssertTrue(first?.text.hasPrefix("Dune ") == true)
    }

    func testACenteredPairIsOrderedByWhereTheLettersReallyAre() {
        // A short title and a long one, each centered in 100 points: the long one's outer letters have no partner.
        let short = line("AAAA")
        let long = line("BBBBBBBB")
        let ops = TitleSweep.ops(old: short, oldOffset: 30, new: long, newOffset: 10, s: 0.6)
        XCTAssertFalse(ops.isEmpty)
        for op in ops {
            XCTAssertGreaterThanOrEqual(op.x, 10 - 1e-9)
            XCTAssertLessThanOrEqual(op.x + (op.isRun ? 0 : op.width), 90 + 1e-9)
        }
    }

    func testAnEmptyTitleHasNothingToDraw() {
        let empty = TitleLine(glyphs: [], width: 0)
        XCTAssertEqual(TitleSweep.ops(old: empty, oldOffset: 0, new: empty, newOffset: 0, s: 0.5), [])
        let toEmpty = TitleSweep.ops(old: line("Dune"), oldOffset: 0, new: empty, newOffset: 0, s: 0.5)
        XCTAssertFalse(toEmpty.isEmpty, "the old title still fades out")
        let fromEmpty = TitleSweep.ops(old: empty, oldOffset: 0, new: line("Dune"), newOffset: 0, s: 0.5)
        XCTAssertFalse(fromEmpty.isEmpty)
    }

    @MainActor
    func testTheStylesMatchTheTitlesTheyDraw() {
        XCTAssertFalse(TitleDissolveStyle.inProgress.centered)
        XCTAssertTrue(TitleDissolveStyle.trending.centered)
        XCTAssertEqual(TitleDissolveStyle.inProgress.height, InProgressPanelMetrics.titleRowHeight)
        XCTAssertEqual(TitleDissolveStyle.trending.height, Double(HomeDesign.Metric.trendingTitleHeight))
        XCTAssertEqual(TitleDissolveStyle.inProgress.font, HomeDesign.Text.panelTitle)
        XCTAssertEqual(TitleDissolveStyle.trending.font, HomeDesign.Text.trendingTitle)
        XCTAssertEqual(TitleDissolveStyle.trending.inset, 16)
        XCTAssertEqual(TitleDissolveStyle.inProgress.uiFont.pointSize, 19)
        XCTAssertEqual(TitleDissolveStyle.trending.uiFont.pointSize, 20)
    }
}
