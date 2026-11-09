import SwiftUI
import XCTest
@testable import Spine

/// The feed card's bar takes the poster's colors the way the In progress panel does: the palette chosen from the server's
/// colors, from colors counted on the phone, or none, and how it is kept off light-ink cards.
@MainActor
final class FeedMeterColorsTests: XCTestCase {
    private let red = [PosterSwatch(hex: "#C8102E", share: 0.6), PosterSwatch(hex: "#1E5AA8", share: 0.3)]
    private let green = [PosterSwatch(hex: "#1F9D55", share: 0.7)]
    /// Nothing but black and white: not an answer.
    private let blackAndWhite = [PosterSwatch(hex: "#0B0B0B", share: 0.8), PosterSwatch(hex: "#FFFFFF", share: 0.2)]

    private func tint(
        server: [PosterSwatch]? = nil, counted: [PosterSwatch]? = nil, contrast: Bool = false, ink: ArtInk = .dark
    ) -> MeterTint? {
        FeedMeterColors.tint(server: server, counted: counted, highContrast: contrast, ink: ink)
    }

    // MARK: Which colors

    func testTheServersColorsComeFirst() throws {
        let chosen = try XCTUnwrap(tint(server: red, counted: green))
        XCTAssertEqual(chosen, MeterTint.make(from: red))
    }

    func testCountedColorsAreUsedWhenTheServerSendsNone() throws {
        XCTAssertEqual(try XCTUnwrap(tint(server: nil, counted: green)), MeterTint.make(from: green))
        XCTAssertEqual(try XCTUnwrap(tint(server: [], counted: green)), MeterTint.make(from: green))
    }

    func testAServerPaletteOfOnlyBlackAndWhiteFallsBackToTheCount() throws {
        XCTAssertEqual(try XCTUnwrap(tint(server: blackAndWhite, counted: green)), MeterTint.make(from: green))
    }

    func testNoColorsGivesNoTint() {
        XCTAssertNil(tint())
        XCTAssertNil(tint(server: blackAndWhite, counted: blackAndWhite))
    }

    func testIncreaseContrastLiftsTheColorsAsInProgressDoes() throws {
        let plain = try XCTUnwrap(tint(counted: red))
        let high = try XCTUnwrap(tint(counted: red, contrast: true))
        XCTAssertEqual(high, MeterTint.make(from: red, highContrast: true))
        XCTAssertNotEqual(plain, high)
    }

    func testALightInkCardKeepsItsDarkPaletteWhateverTheColors() {
        XCTAssertNil(tint(server: red, counted: green, ink: .light))
        let accent = Color.orange
        let palette = FeedMeterColors.palette(ink: .light, accent: accent, tint: tint(server: red, ink: .light))
        XCTAssertEqual(palette, MeterPalette(before: ArtInk.light.before, rest: ArtInk.light.rest, accent: accent))
        XCTAssertNil(palette.tint)
    }

    // MARK: The palette

    func testWithoutATintThePaletteIsTheCardsPlainOne() {
        let accent = Color.pink
        XCTAssertEqual(
            FeedMeterColors.palette(ink: .dark, accent: accent, tint: nil),
            MeterPalette(before: ArtInk.dark.before, rest: ArtInk.dark.rest, accent: accent)
        )
    }

    func testATintMapsTheWayInProgressDoes() throws {
        let chosen = try XCTUnwrap(tint(counted: red))
        let palette = FeedMeterColors.palette(ink: .dark, accent: .pink, tint: chosen)
        XCTAssertEqual(palette.tint, chosen)
        // Today is the poster's brightest color, the days before run along its ramp at 80 %, the rest is the card's track.
        XCTAssertEqual(palette.todayColor, chosen.today.color)
        XCTAssertEqual(palette.tintOpacity, 0.8)
        XCTAssertEqual(palette.beforeColor(index: 0, count: 5), chosen.color(at: 0).opacity(0.8))
        XCTAssertEqual(palette.beforeColor(index: 4, count: 5), chosen.color(at: 1).opacity(0.8))
        XCTAssertEqual(palette.rest, ArtInk.dark.rest)
    }

    // MARK: Which poster is counted

    private func model(id: Int = 1, meter: Bool, posterURL: String? = "https://p/1.jpg", palette: [PosterSwatch]? = nil) -> FeedCardModel {
        FeedCardModel(
            id: id, kind: .progress, username: "a", avatarURL: nil, dayLabel: nil, title: "T", tag: nil, headline: nil,
            details: [], meter: meter ? ProgressMeterModel.make(unit: .episodes, from: 1, to: 3, total: 10) : nil,
            engagement: nil,
            art: FeedCardArtSpec(
                posterURL: posterURL, backgroundURL: posterURL, accentHex: nil, guessInk: .dark, isSquare: false,
                planningPosterURLs: [], posterPalette: palette
            ),
            accessibilityLabel: "", accessibilityHint: "", destination: .none
        )
    }

    func testOnlyACardWithABarCountsItsPoster() {
        XCTAssertEqual(FeedMeterColors.countURL(for: model(meter: true)), URL(string: "https://p/1.jpg"))
        XCTAssertNil(FeedMeterColors.countURL(for: model(meter: false)), "No bar, no count")
        XCTAssertNil(FeedMeterColors.countURL(for: model(meter: true, posterURL: nil)), "No poster, nothing to count")
    }

    func testAServerPaletteMeansNothingIsCounted() {
        XCTAssertNil(FeedMeterColors.countURL(for: model(meter: true, palette: red)))
        XCTAssertNotNil(
            FeedMeterColors.countURL(for: model(meter: true, palette: blackAndWhite)),
            "A palette of only black and white is not an answer, so the poster is counted"
        )
    }

    func testThePrefetcherWarmsEachBarPosterOnceAndOnlyForCardsWithABar() {
        let withBar = model(meter: true)
        let withoutBar = FeedCardModel(
            id: 2, kind: .log, username: "a", avatarURL: nil, dayLabel: nil, title: "U", tag: nil, headline: nil,
            details: [], meter: nil, engagement: nil, art: withBar.art, accessibilityLabel: "", accessibilityHint: "",
            destination: .none
        )
        var warmed: [URL] = []
        let prefetcher = FeedCardPrefetcher(sink: { _ in }, palettes: { warmed.append($0) })
        let list = [model(id: 0, meter: false), withBar, withoutBar]
        prefetcher.entryAppeared(at: 0, in: list)
        prefetcher.entryAppeared(at: 0, in: list)
        XCTAssertEqual(warmed, [URL(string: "https://p/1.jpg")!])
    }
}
