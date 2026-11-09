import SwiftUI
import XCTest
@testable import Spine

@MainActor
final class ProgressMeterTests: XCTestCase {
    // MARK: make(): when a meter exists

    func testNoMeterWhenTheEndOrTheStopIsUnknown() {
        for unit in [MeterUnit.episodes, .chapters, .issues, .pages] {
            XCTAssertNil(ProgressMeterModel.make(unit: unit, from: 1, to: 2, total: nil), "\(unit) nil total")
            XCTAssertNil(ProgressMeterModel.make(unit: unit, from: 1, to: 2, total: 0), "\(unit) zero total")
            XCTAssertNil(ProgressMeterModel.make(unit: unit, from: 1, to: 2, total: -4), "\(unit) negative total")
            XCTAssertNil(ProgressMeterModel.make(unit: unit, from: 1, to: nil, total: 10), "\(unit) nil to")
        }
        XCTAssertNil(ProgressMeterModel.make(unit: .percent, from: 10, to: nil, total: 100))
    }

    func testGameHoursAloneGetNoMeter() {
        XCTAssertNil(ProgressMeterModel.make(unit: .minutes, from: 60, to: 150, total: 600))
        XCTAssertNil(ProgressMeterModel.make(unit: .minutes, from: nil, to: 150, total: nil))
    }

    func testKindByUnitAndTotal() {
        let cases: [(MeterUnit, Int, String)] = [
            (.episodes, 1, "ticks"),
            (.episodes, 9, "ticks"),
            (.episodes, 30, "ticks"),
            (.episodes, 31, "window"),
            (.episodes, 1150, "window"),
            (.chapters, 1, "ticks"),
            (.chapters, 30, "ticks"),
            (.chapters, 31, "bar"),
            (.chapters, 1150, "bar"),
            (.issues, 30, "ticks"),
            (.issues, 31, "bar"),
            (.pages, 1, "bar"),
            (.pages, 30, "bar"),
            (.pages, 400, "bar"),
        ]
        for (unit, total, expected) in cases {
            let model = ProgressMeterModel.make(unit: unit, from: 0, to: 1, total: total)
            XCTAssertEqual(model.map(Self.name(of:)), expected, "\(unit) of \(total)")
            XCTAssertEqual(model?.total, total, "\(unit) of \(total)")
        }
    }

    func testPercentIsAlwaysABarOutOfAHundred() {
        let model = ProgressMeterModel.make(unit: .percent, from: 58, to: 71, total: 100)
        XCTAssertEqual(model?.kind, .bar(total: 100))
        XCTAssertEqual(ProgressMeterModel.make(unit: .percent, from: 58, to: 71, total: nil)?.kind, .bar(total: 100))
        XCTAssertEqual(ProgressMeterModel.make(unit: .percent, from: 0, to: 250, total: 100)?.to, 100)
    }

    func testShortRunsDrawOneTickPerUnit() {
        let model = ProgressMeterModel.make(unit: .episodes, from: 14, to: 18, total: 28)
        XCTAssertEqual(model?.kind, .ticks(count: 28))
        XCTAssertEqual(model?.tickCount, 28)
        let long = ProgressMeterModel.make(unit: .episodes, from: 1088, to: 1089, total: 1150)
        XCTAssertEqual(long?.tickCount, 20)
    }

    // MARK: clamping and a day that lights nothing

    func testNumbersClampToTheRun() {
        let high = ProgressMeterModel.make(unit: .pages, from: 900, to: 950, total: 400)
        XCTAssertEqual(high?.from, 400)
        XCTAssertEqual(high?.to, 400)
        let low = ProgressMeterModel.make(unit: .pages, from: -20, to: -5, total: 400)
        XCTAssertEqual(low?.from, 0)
        XCTAssertEqual(low?.to, 0)
        let split = ProgressMeterModel.make(unit: .episodes, from: -3, to: 99, total: 10)
        XCTAssertEqual(split?.from, 0)
        XCTAssertEqual(split?.to, 10)
    }

    func testMissingFromBecomesTo() {
        let model = ProgressMeterModel.make(unit: .episodes, from: nil, to: 5, total: 9)
        XCTAssertEqual(model?.from, 5)
        XCTAssertEqual(model?.to, 5)
        XCTAssertEqual(model?.todayFraction, 0)
    }

    func testAMovedBackDayLightsNothing() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 8, to: 3, total: 10))
        XCTAssertEqual(model.from, 8)
        XCTAssertEqual(model.to, 8)
        XCTAssertFalse((0..<10).contains { model.tickState(at: $0) == .today })
        XCTAssertEqual(model.accessibilityLabel, "Episode 8 of 10, nothing today")

        let bar = try XCTUnwrap(ProgressMeterModel.make(unit: .pages, from: 300, to: 120, total: 400))
        XCTAssertEqual(bar.todayFraction, 0)
    }

    func testAStatusDayLightsNothing() throws {
        let ticks = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 5, to: 5, total: 9))
        XCTAssertFalse((0..<9).contains { ticks.tickState(at: $0) == .today })
        XCTAssertEqual(ticks.accessibilityLabel, "Episode 5 of 9, nothing today")

        let bar = try XCTUnwrap(ProgressMeterModel.make(unit: .pages, from: 140, to: 140, total: 303))
        XCTAssertEqual(bar.todayFraction, 0)
        XCTAssertEqual(bar.beforeFraction, 140.0 / 303.0, accuracy: 1e-9)
    }

    // MARK: tickState

    func testTickStateForTicksIsByIndex() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 4, to: 7, total: 10))
        let states = (0..<10).map { model.tickState(at: $0) }
        XCTAssertEqual(states, [
            .before, .before, .before, .before,
            .today, .today, .today,
            .rest, .rest, .rest,
        ])
    }

    func testTickStateForAWindowIsByEpisodeNumber() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 1065, to: 1089, total: 1150))
        guard case .window(let first, let span, let seriesTotal) = model.kind else {
            return XCTFail("expected a window")
        }
        XCTAssertEqual(first, 1072)
        XCTAssertEqual(span, 20)
        XCTAssertEqual(seriesTotal, 1150)
        // Episodes 1072...1089 are today's (18 ticks); 1090 and 1091 are still to come.
        for index in 0..<18 {
            XCTAssertEqual(model.tickState(at: index), .today, "tick \(index)")
        }
        XCTAssertEqual(model.tickState(at: 18), .rest)
        XCTAssertEqual(model.tickState(at: 19), .rest)
    }

    func testAWindowShowsEarlierEpisodesAsBefore() throws {
        // Watched 1070 before and 1075 by the end of the day, in a window the caller placed at 1066.
        let model = try XCTUnwrap(ProgressMeterModel.make(
            unit: .episodes, from: 1070, to: 1075, total: 1150, windowFirst: 1066
        ))
        XCTAssertEqual(model.kind, .window(first: 1066, span: 20, seriesTotal: 1150))
        let states = (0..<20).map { model.tickState(at: $0) }
        // 1066...1070 before, 1071...1075 today, 1076...1085 rest.
        XCTAssertEqual(Array(states[0..<5]), Array(repeating: .before, count: 5))
        XCTAssertEqual(Array(states[5..<10]), Array(repeating: .today, count: 5))
        XCTAssertEqual(Array(states[10..<20]), Array(repeating: .rest, count: 10))
    }

    func testWindowFirstOverrideIsClampedToTheSeries() {
        let tooHigh = ProgressMeterModel.make(unit: .episodes, from: 1, to: 2, total: 40, windowFirst: 500)
        XCTAssertEqual(tooHigh?.kind, .window(first: 21, span: 20, seriesTotal: 40))
        let tooLow = ProgressMeterModel.make(unit: .episodes, from: 1, to: 2, total: 40, windowFirst: -9)
        XCTAssertEqual(tooLow?.kind, .window(first: 1, span: 20, seriesTotal: 40))
    }

    // MARK: accessibility labels

    func testAccessibilityLabels() {
        XCTAssertEqual(
            ProgressMeterModel.make(unit: .episodes, from: 14, to: 18, total: 28)?.accessibilityLabel,
            "Episode 18 of 28, 4 today"
        )
        XCTAssertEqual(
            ProgressMeterModel.make(unit: .pages, from: 192, to: 212, total: 400)?.accessibilityLabel,
            "Page 212 of 400, 20 today"
        )
        XCTAssertEqual(
            ProgressMeterModel.make(unit: .percent, from: 58, to: 71, total: 100)?.accessibilityLabel,
            "71 percent, 13 today"
        )
        XCTAssertEqual(
            ProgressMeterModel.make(unit: .episodes, from: 5, to: 5, total: 9)?.accessibilityLabel,
            "Episode 5 of 9, nothing today"
        )
        XCTAssertEqual(
            ProgressMeterModel.make(unit: .chapters, from: 36, to: 41, total: 90)?.accessibilityLabel,
            "Chapter 41 of 90, 5 today"
        )
        XCTAssertEqual(
            ProgressMeterModel.make(unit: .issues, from: 11, to: 12, total: 24)?.accessibilityLabel,
            "Issue 12 of 24, 1 today"
        )
    }

    // MARK: EpisodeWindow

    func testFeedPresetMatchesTheMock() {
        XCTAssertEqual(EpisodeWindow.feedFirst(to: 1089, total: 1150), 1072)
        XCTAssertEqual(EpisodeWindow.feedFirst(to: 5, total: 1150), 1)
        XCTAssertEqual(EpisodeWindow.feedFirst(to: 18, total: 1150), 1)
        XCTAssertEqual(EpisodeWindow.feedFirst(to: 19, total: 1150), 2)
    }

    func testPanelPresetMatchesTheMock() {
        XCTAssertEqual(EpisodeWindow.panelFirst(value: 1089, total: 1150), 1074)
        XCTAssertEqual(EpisodeWindow.panelFirst(value: 3, total: 1150), 1)
    }

    func testSheetPresetClampsAtBothEnds() {
        XCTAssertEqual(EpisodeWindow.sheetFirst(value: 1089, pagingOffset: 0, total: 1150), 1074)
        XCTAssertEqual(EpisodeWindow.sheetFirst(value: 1089, pagingOffset: 20, total: 1150), 1094)
        XCTAssertEqual(EpisodeWindow.sheetFirst(value: 1089, pagingOffset: -20, total: 1150), 1054)
        // Paging past the start and past the end stops at episode 1 and at the last full window.
        XCTAssertEqual(EpisodeWindow.sheetFirst(value: 1089, pagingOffset: -5000, total: 1150), 1)
        XCTAssertEqual(EpisodeWindow.sheetFirst(value: 1089, pagingOffset: 5000, total: 1150), 1131)
        XCTAssertEqual(EpisodeWindow.sheetFirst(value: 1150, pagingOffset: 0, total: 1150), 1131)
    }

    func testFirstNeverLeavesTheSeries() {
        XCTAssertEqual(EpisodeWindow.first(anchor: 0, lead: 17, total: 1150), 1)
        XCTAssertEqual(EpisodeWindow.first(anchor: 1150, lead: 17, total: 1150), 1131)
        // A series shorter than the window starts at 1.
        XCTAssertEqual(EpisodeWindow.first(anchor: 12, lead: 15, total: 12), 1)
        XCTAssertEqual(EpisodeWindow.first(anchor: 12, lead: 0, total: 0), 1)
    }

    func testFeedWindowSitsWhereThePresetSaysAtTheEndOfASeries() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 1140, to: 1150, total: 1150))
        XCTAssertEqual(model.kind, .window(first: 1131, span: 20, seriesTotal: 1150))
        XCTAssertEqual(model.tickState(at: 19), .today)
    }

    // MARK: drawing

    private let palette = MeterPalette(before: .red, rest: .green, accent: .blue)

    func testEveryKindRendersAtEveryWidth() throws {
        let models = try [
            XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 4, to: 6, total: 10)),
            XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 14, to: 18, total: 28)),
            XCTUnwrap(ProgressMeterModel.make(unit: .chapters, from: 20, to: 30, total: 30)),
            XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 1065, to: 1089, total: 1150)),
            XCTUnwrap(ProgressMeterModel.make(unit: .pages, from: 126, to: 212, total: 448)),
            XCTUnwrap(ProgressMeterModel.make(unit: .percent, from: 61, to: 64, total: 100)),
            XCTUnwrap(ProgressMeterModel.make(unit: .pages, from: 140, to: 140, total: 303)),
            XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 0, to: 0, total: 1)),
        ]
        for model in models {
            for width in [120.0, 200, 280, 358] as [CGFloat] {
                let compact = try render(ProgressMeterView(model: model, palette: palette), width: width)
                XCTAssertEqual(compact.width, Int(width), "compact \(model.kind) at \(width)")
                XCTAssertEqual(
                    CGFloat(compact.height),
                    ProgressMeterMetrics.height(for: model.kind),
                    "compact height \(model.kind)"
                )
                let bar = try render(
                    ProgressMeterView.bar(model: model, palette: palette, height: 14, radius: 7),
                    width: width
                )
                XCTAssertEqual(bar.width, Int(width), "bar \(model.kind) at \(width)")
                XCTAssertEqual(bar.height, 14)
            }
        }
    }

    func testTicksPaintTheirThreeStates() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 2, to: 4, total: 6))
        let image = try render(ProgressMeterView(model: model, palette: palette), width: 300)
        // Six ticks, gap 3: each is (300 - 15) / 6 = 47.5 wide.
        let y = 4
        let centers = (0..<6).map { Int(CGFloat($0) * 50.5 + 23.75) }
        let colors = centers.map { image.pixel(x: $0, y: y) }
        XCTAssertEqual(colors.map(\.name), ["red", "red", "blue", "blue", "green", "green"])
        // The gap between ticks stays clear.
        XCTAssertEqual(image.pixel(x: 48 + 1, y: y).alpha, 0)
    }

    func testABarPaintsEarlierTodayAndTrack() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .pages, from: 100, to: 200, total: 400))
        let compact = try render(ProgressMeterView(model: model, palette: palette), width: 400)
        XCTAssertEqual(compact.pixel(x: 50, y: 4).name, "red")
        XCTAssertEqual(compact.pixel(x: 150, y: 4).name, "blue")
        XCTAssertEqual(compact.pixel(x: 300, y: 4).name, "green")
        // Rounded ends: the very corner is outside the track.
        XCTAssertEqual(compact.pixel(x: 0, y: 0).alpha, 0)

        let animated = try render(
            ProgressMeterView.bar(model: model, palette: palette, height: 14, radius: 7),
            width: 400
        )
        XCTAssertEqual(animated.pixel(x: 50, y: 7).name, "red")
        XCTAssertEqual(animated.pixel(x: 150, y: 7).name, "blue")
        XCTAssertEqual(animated.pixel(x: 300, y: 7).name, "green")
        XCTAssertEqual(animated.pixel(x: 0, y: 0).alpha, 0)
    }

    func testAWindowFadesInFromTheLeftAndDrawsTheSeriesLine() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 1065, to: 1089, total: 1150))
        let image = try render(ProgressMeterView(model: model, palette: palette), width: 358)
        // Tick 0 sits at the left edge, where the fade is nearly clear; the last tick is solid.
        let firstTick = image.pixel(x: 8, y: 4)
        XCTAssertLessThan(firstTick.alpha, 120)
        let lastTick = image.pixel(x: 358 - 8, y: 4)
        XCTAssertEqual(lastTick.name, "green")
        XCTAssertGreaterThan(lastTick.alpha, 240)
        // The series line is drawn under the ticks: filled up to 1089/1150 in the "before" color.
        let lineY = Int(ProgressMeterMetrics.tickHeight + ProgressMeterMetrics.lineOffset) + 1
        XCTAssertEqual(image.pixel(x: 100, y: lineY).name, "red")
        XCTAssertEqual(image.pixel(x: 355, y: lineY).name, "green")
    }

    func testMeterViewsHaveOneAccessibilityLabel() throws {
        let model = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 14, to: 18, total: 28))
        XCTAssertEqual(model.accessibilityLabel, "Episode 18 of 28, 4 today")
        // Equatable so a feed row can skip redrawing when nothing changed.
        XCTAssertEqual(
            ProgressMeterView(model: model, palette: palette),
            ProgressMeterView(model: model, palette: palette)
        )
        let moved = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 14, to: 19, total: 28))
        XCTAssertNotEqual(
            ProgressMeterView(model: model, palette: palette),
            ProgressMeterView(model: moved, palette: palette)
        )
    }

    func testMeterSourcesAvoidTheCostlyAPIs() throws {
        let folder = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Core/Design/Meter")
        let files = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "swift" }
        XCTAssertGreaterThanOrEqual(files.count, 3)
        let banned = ["Geometry" + "Reader", ".blur" + "(", "Mate" + "rial", "View" + "ThatFits", "Async" + "Image"]
        for file in files {
            let text = try String(contentsOf: file, encoding: .utf8)
            for word in banned {
                XCTAssertFalse(text.contains(word), "\(file.lastPathComponent) uses \(word)")
            }
        }
    }

    // MARK: helpers

    private static func name(of model: ProgressMeterModel) -> String {
        switch model.kind {
        case .ticks: "ticks"
        case .window: "window"
        case .bar: "bar"
        }
    }

    private func render<Content: View>(_ view: Content, width: CGFloat) throws -> RenderedMeter {
        let renderer = ImageRenderer(content: view.frame(width: width))
        renderer.scale = 1
        let image = try XCTUnwrap(renderer.cgImage)
        return try XCTUnwrap(RenderedMeter(image))
    }
}

/// A rendered meter as RGBA pixels, to read colors back.
private struct RenderedMeter {
    struct Pixel {
        var red: UInt8, green: UInt8, blue: UInt8, alpha: UInt8

        /// The strongest of red, green and blue, which is how the test palette tells its colors apart.
        var name: String {
            if red >= green, red >= blue { return "red" }
            if green >= red, green >= blue { return "green" }
            return "blue"
        }
    }

    let width: Int
    let height: Int
    private let pixels: [UInt8]

    init?(_ image: CGImage) {
        let width = image.width
        let height = image.height
        var data = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = data.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        self.width = width
        self.height = height
        pixels = data
    }

    /// Pixel at (x, y) with y counted from the top.
    func pixel(x: Int, y: Int) -> Pixel {
        let index = (y * width + x) * 4
        return Pixel(red: pixels[index], green: pixels[index + 1], blue: pixels[index + 2], alpha: pixels[index + 3])
    }
}
