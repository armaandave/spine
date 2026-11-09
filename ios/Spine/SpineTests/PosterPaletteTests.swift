import UIKit
import XCTest
@testable import Spine

@MainActor
final class PosterPaletteTests: XCTestCase {
    // MARK: Decoding

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    private func detailJSON(palette: String?) -> String {
        let field = palette.map { #", "poster_palette": \#($0)"# } ?? ""
        return #"""
        { "ref": { "source": "tmdb", "media_type": "movie", "media_id": "1" },
          "title": "Heat", "external_links": {}\#(field) }
        """#
    }

    func testDetailDecodesAPalette() throws {
        let detail = try decode(MediaDetail.self, detailJSON(palette: ##"[{"hex": "#0B0B0B", "share": 0.83}, {"hex": "#C8102E", "share": 0.1}]"##))
        XCTAssertEqual(detail.posterPalette, [
            PosterSwatch(hex: "#0B0B0B", share: 0.83),
            PosterSwatch(hex: "#C8102E", share: 0.1),
        ])
    }

    func testDetailWithoutOrNullPaletteStillDecodes() throws {
        XCTAssertNil(try decode(MediaDetail.self, detailJSON(palette: nil)).posterPalette)
        let null = try decode(MediaDetail.self, detailJSON(palette: "null"))
        XCTAssertNil(null.posterPalette)
        XCTAssertEqual(null.title, "Heat")
    }

    func testAMalformedPaletteNeverFailsTheWholePayload() throws {
        let detail = try decode(MediaDetail.self, detailJSON(palette: #"[{"hex": 5}]"#))
        XCTAssertNil(detail.posterPalette)
        XCTAssertEqual(detail.title, "Heat")
    }

    func testSummaryDecodesPresentAbsentAndNull() throws {
        let base = #""ref": { "source": "tmdb", "media_type": "movie", "media_id": "1" }, "title": "Heat""#
        let present = try decode(MediaSummary.self, "{ \(base), \"poster_palette\": [{\"hex\": \"#AA5500\", \"share\": 0.5}] }")
        XCTAssertEqual(present.posterPalette, [PosterSwatch(hex: "#AA5500", share: 0.5)])
        XCTAssertNil(try decode(MediaSummary.self, "{ \(base) }").posterPalette)
        XCTAssertNil(try decode(MediaSummary.self, "{ \(base), \"poster_palette\": null }").posterPalette)
    }

    // MARK: Counting on the device

    func testTwoColorPixelsGiveTwoSwatchesWithTheirShares() {
        let pixels = [UInt32](repeating: 0xFF0000, count: 700) + [UInt32](repeating: 0x0000FF, count: 300)
        let palette = PosterPaletteExtractor.palette(of: pixels)
        XCTAssertEqual(palette, [
            PosterSwatch(hex: "#FF0000", share: 0.7),
            PosterSwatch(hex: "#0000FF", share: 0.3),
        ])
    }

    func testNearColorsMergeIntoOne() {
        // Two reds a few steps apart are one color; the blue stays its own.
        let pixels = [UInt32](repeating: 0xF00000, count: 400)
            + [UInt32](repeating: 0xE80808, count: 300)
            + [UInt32](repeating: 0x0000FF, count: 300)
        let palette = PosterPaletteExtractor.palette(of: pixels)
        XCTAssertEqual(palette.count, 2)
        XCTAssertEqual(palette[0].share, 0.7, accuracy: 0.001)
        XCTAssertEqual(palette[1], PosterSwatch(hex: "#0000FF", share: 0.3))
    }

    func testAtMostFiveSwatchesBiggestFirst() {
        let colors: [UInt32] = [0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF, 0x00FFFF, 0x000000]
        var pixels = [UInt32]()
        for (index, color) in colors.enumerated() {
            pixels += [UInt32](repeating: color, count: 100 * (colors.count - index))
        }
        let palette = PosterPaletteExtractor.palette(of: pixels)
        XCTAssertEqual(palette.count, 5)
        XCTAssertEqual(palette.map(\.share), palette.map(\.share).sorted(by: >))
        XCTAssertEqual(palette[0].hex, "#FF0000")
    }

    func testImageWithTwoColorsIsCountedFromItsPixels() throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 96, height: 96), format: {
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1
            return format
        }())
        let image = renderer.image { context in
            UIColor(red: 0.1, green: 0.5, blue: 0.2, alpha: 1).setFill()
            context.fill(CGRect(x: 0, y: 0, width: 96, height: 96))
            UIColor.white.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 96, height: 24))
        }
        let palette = try XCTUnwrap(PosterPaletteExtractor.palette(of: try XCTUnwrap(image.cgImage)))
        XCTAssertEqual(palette.count, 2)
        XCTAssertEqual(palette[0].share, 0.75, accuracy: 0.01)
        XCTAssertEqual(palette[1].share, 0.25, accuracy: 0.01)
        XCTAssertEqual(palette[1].hex, "#FFFFFF")
    }

    // MARK: Choosing the meter's colors

    private func lightness(_ color: MeterTint.RGB) -> Double { color.hsb.brightness }

    func testBlackBorderIsSkippedAndTheVividColorsLead() throws {
        let tint = try XCTUnwrap(MeterTint.make(from: [
            PosterSwatch(hex: "#0B0B0B", share: 0.83),
            PosterSwatch(hex: "#C8102E", share: 0.10),
            PosterSwatch(hex: "#F5C518", share: 0.07),
        ]))
        XCTAssertEqual(tint.ramp.count, 2)
        XCTAssertEqual(tint.ramp[0].hsb.hue * 360, 350, accuracy: 4, "red first")
        for color in tint.ramp {
            XCTAssertGreaterThanOrEqual(lightness(color), MeterTint.minimumBrightness - 0.001)
        }
        XCTAssertGreaterThan(lightness(tint.today), tint.ramp.map(lightness).max() ?? 1, "today is brightest")
    }

    func testVeryDarkColorIsLiftedKeepingItsHue() throws {
        let tint = try XCTUnwrap(MeterTint.make(from: [PosterSwatch(hex: "#101040", share: 0.6)]))
        let color = try XCTUnwrap(tint.ramp.first)
        XCTAssertEqual(color.hsb.brightness, MeterTint.minimumBrightness, accuracy: 0.001)
        XCTAssertEqual(color.hsb.hue * 360, 240, accuracy: 2)
        XCTAssertEqual(color.hsb.saturation, 0.75, accuracy: 0.01)
    }

    func testBrightColorIsKeptNotDimmed() throws {
        let tint = try XCTUnwrap(MeterTint.make(from: [PosterSwatch(hex: "#FFD60A", share: 0.5)]))
        XCTAssertEqual(try XCTUnwrap(tint.ramp.first).hsb.hue * 360, 50, accuracy: 3)
        XCTAssertGreaterThanOrEqual(lightness(try XCTUnwrap(tint.ramp.first)), 0.95)
    }

    func testGreyPaletteBecomesALiftedNeutral() throws {
        let tint = try XCTUnwrap(MeterTint.make(from: [
            PosterSwatch(hex: "#050505", share: 0.4),
            PosterSwatch(hex: "#808080", share: 0.4),
            PosterSwatch(hex: "#FBFBFB", share: 0.2),
        ]))
        XCTAssertEqual(tint.ramp.count, 1)
        XCTAssertEqual(tint.ramp[0].hsb.saturation, 0, accuracy: 0.01)
        XCTAssertEqual(lightness(tint.ramp[0]), MeterTint.minimumBrightness, accuracy: 0.001)
    }

    func testOnlyBlackWhiteAndSpecksGiveNoTint() {
        XCTAssertNil(MeterTint.make(from: [
            PosterSwatch(hex: "#000000", share: 0.7),
            PosterSwatch(hex: "#FFFFFF", share: 0.29),
            PosterSwatch(hex: "#FF0000", share: 0.01),
        ]))
        XCTAssertNil(MeterTint.make(from: []))
    }

    func testRampHasAtMostThreeColors() throws {
        let tint = try XCTUnwrap(MeterTint.make(from: [
            PosterSwatch(hex: "#C8102E", share: 0.3), PosterSwatch(hex: "#1E90FF", share: 0.25),
            PosterSwatch(hex: "#32CD32", share: 0.2), PosterSwatch(hex: "#FFD700", share: 0.15),
            PosterSwatch(hex: "#FF69B4", share: 0.1),
        ]))
        XCTAssertEqual(tint.ramp.count, 3)
    }

    func testIncreaseContrastLiftsColorsAndPushesTodayTowardWhite() throws {
        let swatches = [PosterSwatch(hex: "#3A2A8C", share: 0.5)]
        let normal = try XCTUnwrap(MeterTint.make(from: swatches))
        let high = try XCTUnwrap(MeterTint.make(from: swatches, highContrast: true))
        XCTAssertGreaterThanOrEqual(lightness(high.ramp[0]), MeterTint.highContrastMinimumBrightness - 0.001)
        XCTAssertLessThan(high.today.hsb.saturation, normal.today.hsb.saturation, "today is paler")
        XCTAssertGreaterThan(lightness(high.today) - lightness(high.ramp[0]), -0.001)
    }

    func testRampColorsRunLeftToRight() throws {
        let tint = MeterTint(
            ramp: [.init(red: 1, green: 0, blue: 0), .init(red: 0, green: 0, blue: 1)],
            today: .white
        )
        XCTAssertEqual(tint.ramp.count, 2)
        XCTAssertNotEqual(tint.color(at: 0), tint.color(at: 1))
        XCTAssertEqual(tint.color(at: 0), MeterTint.RGB(red: 1, green: 0, blue: 0).color)
    }

    // MARK: Where the colors come from

    private final class Counter: @unchecked Sendable {
        private let lock = NSLock()
        private var value = 0
        func bump() { lock.lock(); value += 1; lock.unlock() }
        var count: Int { lock.lock(); defer { lock.unlock() }; return value }
    }

    private let url = URL(string: "https://example.com/poster.jpg")
    private let server = [PosterSwatch(hex: "#112233", share: 1)]
    private let counted = [PosterSwatch(hex: "#AABBCC", share: 1)]

    func testServerPaletteWinsAndNothingIsCounted() async {
        let counter = Counter()
        let store = PosterPaletteStore { [counted] _ in counter.bump(); return counted }
        let found = await store.swatches(server: server, url: url)
        XCTAssertEqual(found, server)
        XCTAssertEqual(store.known(server: server, url: url), server)
        XCTAssertEqual(counter.count, 0)
    }

    func testWithoutAServerPaletteThePosterIsCountedOnceAndRemembered() async {
        let counter = Counter()
        let store = PosterPaletteStore { [counted] _ in counter.bump(); return counted }
        XCTAssertNil(store.known(server: nil, url: url))
        let first = await store.swatches(server: nil, url: url)
        let second = await store.swatches(server: [], url: url)
        XCTAssertEqual(first, counted)
        XCTAssertEqual(second, counted)
        XCTAssertEqual(counter.count, 1)
        XCTAssertEqual(store.known(server: nil, url: url), counted, "later panels start with the colors")
    }

    func testNoPosterOrAnUnreadableOneGivesNothingAndIsNotRemembered() async {
        let counter = Counter()
        let store = PosterPaletteStore { _ in counter.bump(); return nil }
        let none = await store.swatches(server: nil, url: nil)
        XCTAssertNil(none)
        let unreadable = await store.swatches(server: nil, url: url)
        XCTAssertNil(unreadable)
        _ = await store.swatches(server: nil, url: url)
        XCTAssertEqual(counter.count, 2, "a failed count is tried again later")
    }

    // MARK: In the panel's layout

    private let panelTint = MeterTint.make(from: [
        PosterSwatch(hex: "#C8102E", share: 0.5), PosterSwatch(hex: "#1E90FF", share: 0.4),
    ])

    private func panelLayout(tint: MeterTint?) -> MorphLayout {
        let meter = ProgressMeterModel.make(unit: .episodes, from: 6, to: 7, total: 10)
        return MorphLayout.make(
            meter: meter, place: .none, accentHex: nil, hasQuick: true, width: 326, tint: tint
        )
    }

    func testPanelWithoutAPaletteKeepsItsPlainColors() {
        let plain = panelLayout(tint: nil)
        XCTAssertEqual(plain.cells[0].r, 1)
        XCTAssertEqual(plain.cells[0].a, 0.55, accuracy: 0.0001)
        XCTAssertEqual(plain.cells[5].a, 0.55, accuracy: 0.0001)
    }

    func testPanelTicksRunAlongThePostersRampAndTodayIsItsBrightest() throws {
        let tint = try XCTUnwrap(panelTint)
        let l = panelLayout(tint: tint)
        XCTAssertEqual(l.accentR, tint.today.red, accuracy: 0.0001)
        XCTAssertEqual(l.accentB, tint.today.blue, accuracy: 0.0001)
        XCTAssertEqual(l.cells[6].r, l.accentR, accuracy: 0.0001, "today's tick is the accent")
        XCTAssertEqual(l.cells[0].a, 0.8, accuracy: 0.0001)
        XCTAssertNotEqual(l.cells[0].r, l.cells[5].r, "earlier ticks differ along the ramp")
        XCTAssertEqual(l.cells[7].a, 0.16, accuracy: 0.0001, "ticks still to come stay the plain track")
    }

    // MARK: Which palette the panel colors with

    private let blackAndWhite = [
        PosterSwatch(hex: "#050505", share: 0.7), PosterSwatch(hex: "#FAFAFA", share: 0.3),
    ]
    private let vivid = [PosterSwatch(hex: "#1E90FF", share: 0.6), PosterSwatch(hex: "#FFB000", share: 0.3)]

    func testAServerPaletteWithNoUsableColorsDoesNotStopThePosterFromBeingCounted() async throws {
        // The cause of "the colors never show": the store hands back any non-empty server palette as the answer, and
        // a palette of only black and white gives no tint, so the poster was never counted and the meter stayed
        // plain. The panel now asks the store with no server palette, and chooses between the two.
        let counter = Counter()
        let store = PosterPaletteStore { [vivid] _ in counter.bump(); return vivid }
        XCTAssertNil(MeterTint.make(from: blackAndWhite), "the server's palette gives nothing to draw")
        let stuck = await store.swatches(server: blackAndWhite, url: url)
        XCTAssertEqual(stuck, blackAndWhite, "asking with the server's palette never counts")
        XCTAssertEqual(counter.count, 0)

        let counted = await store.swatches(server: nil, url: url)
        XCTAssertEqual(counter.count, 1)
        let tint = try XCTUnwrap(MeterTint.choose(server: blackAndWhite, counted: counted))
        XCTAssertEqual(tint, MeterTint.make(from: vivid), "the panel draws the counted colors")
    }

    func testTheServersColorsWinWhenTheyGiveATintAndTheCountIsTheFallback() throws {
        let server = [PosterSwatch(hex: "#C8102E", share: 0.5)]
        let fromServer = try XCTUnwrap(MeterTint.choose(server: server, counted: vivid))
        XCTAssertEqual(fromServer, MeterTint.make(from: server))
        XCTAssertEqual(MeterTint.choose(server: nil, counted: vivid), MeterTint.make(from: vivid))
        XCTAssertEqual(MeterTint.choose(server: [], counted: vivid), MeterTint.make(from: vivid))
        XCTAssertNil(MeterTint.choose(server: nil, counted: nil))
        XCTAssertNil(MeterTint.choose(server: blackAndWhite, counted: blackAndWhite))
    }

    func testPostersCountedTogetherAreEachCountedOnce() async {
        let counter = Counter()
        let store = PosterPaletteStore { [vivid] _ in counter.bump(); return vivid }
        let urls = (0 ..< 6).compactMap { URL(string: "https://example.com/\($0).jpg") }
        await withTaskGroup(of: Void.self) { group in
            for url in urls + urls {
                group.addTask { _ = await store.swatches(server: nil, url: url) }
            }
        }
        XCTAssertEqual(counter.count, 6)
        for url in urls { XCTAssertEqual(store.known(server: nil, url: url), vivid) }
    }

    func testPanelColorsBlendWithTheScroll() throws {
        let plain = panelLayout(tint: nil)
        let colored = panelLayout(tint: try XCTUnwrap(panelTint))
        let mid = MorphLayout.blend(plain, colored, 0.5)
        XCTAssertEqual(mid.accentR, (plain.accentR + colored.accentR) / 2, accuracy: 0.01)
    }
}
