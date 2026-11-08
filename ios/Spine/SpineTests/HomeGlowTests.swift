import Nuke
import XCTest
@testable import Spine

@MainActor
final class HomeGlowTests: XCTestCase {
    private let a = URL(string: "https://example.com/a.jpg")!
    private let b = URL(string: "https://example.com/b.jpg")!
    private let c = URL(string: "https://example.com/c.jpg")!

    // MARK: GlowLayers

    func testFirstShowFadesInOnTheUpperLayer() {
        var layers = GlowLayers<URL>()
        XCTAssertTrue(layers.show(a))
        XCTAssertNil(layers.lower)
        XCTAssertEqual(layers.upper, a)
        XCTAssertTrue(layers.isFading)

        XCTAssertFalse(layers.fadeCompleted(of: a))
        XCTAssertEqual(layers.lower, a)
        XCTAssertNil(layers.upper)
        XCTAssertFalse(layers.isFading)
    }

    func testShowingTheSameURLDoesNothing() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        let fading = layers
        XCTAssertFalse(layers.show(a))
        XCTAssertEqual(layers, fading)

        layers.fadeCompleted(of: a)
        let settled = layers
        XCTAssertFalse(layers.show(a))
        XCTAssertEqual(layers, settled)
    }

    func testAThenBThenAEndsOnA() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        layers.fadeCompleted(of: a)

        XCTAssertTrue(layers.show(b))
        XCTAssertEqual(layers.lower, a, "the outgoing layer keeps its image while the upper one fades")
        XCTAssertEqual(layers.upper, b)
        layers.fadeCompleted(of: b)
        XCTAssertEqual(layers.lower, b)

        XCTAssertTrue(layers.show(a))
        XCTAssertEqual(layers.upper, a)
        layers.fadeCompleted(of: a)
        XCTAssertEqual(layers.lower, a)
        XCTAssertNil(layers.upper)
    }

    func testAShowDuringAFadeIsQueuedAndTheLatestWins() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        layers.fadeCompleted(of: a)
        layers.show(b)

        XCTAssertFalse(layers.show(c), "a fade is running, so c waits")
        XCTAssertEqual(layers.upper, b)
        XCTAssertEqual(layers.lower, a)
        XCTAssertEqual(layers.target, c)

        // A quick swipe through the carousel: only the last focus is kept.
        XCTAssertFalse(layers.show(a))
        XCTAssertFalse(layers.show(c))
        XCTAssertEqual(layers.target, c)

        XCTAssertTrue(layers.fadeCompleted(of: b), "the queued image starts when the running fade ends")
        XCTAssertEqual(layers.lower, b)
        XCTAssertEqual(layers.upper, c)
        XCTAssertFalse(layers.fadeCompleted(of: c))
        XCTAssertEqual(layers.lower, c)
        XCTAssertNil(layers.upper)
    }

    func testAskingForTheFadingImageAgainDropsTheQueue() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        layers.fadeCompleted(of: a)
        layers.show(b)
        layers.show(c)

        XCTAssertFalse(layers.show(b))
        XCTAssertEqual(layers.target, b)
        XCTAssertFalse(layers.fadeCompleted(of: b))
        XCTAssertEqual(layers.lower, b)
        XCTAssertNil(layers.upper)
    }

    func testAQueuedImageThatIsAlreadyTheLowerLayerStillFades() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        layers.fadeCompleted(of: a)
        layers.show(b)
        layers.show(a)

        XCTAssertTrue(layers.fadeCompleted(of: b))
        XCTAssertEqual(layers.lower, b)
        XCTAssertEqual(layers.upper, a)
    }

    /// The stuck-glow bug: a fast swipe away and back during a fade put an image that was already on screen (the
    /// lower layer) back on top. Its view was keyed by URL, so it was reused, its fade never ran and never ended,
    /// and every later swipe only queued. A layer that comes back on top must get a new identity.
    func testAnImageThatComesBackOnTopGetsANewViewIdentity() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        layers.fadeCompleted(of: a)
        let aAsLower = layers.aliveLayers.first { $0.art == a }
        layers.show(b)
        layers.show(a)
        XCTAssertTrue(layers.fadeCompleted(of: b))

        let aOnTop = layers.aliveLayers.first { $0.art == a }
        XCTAssertNotNil(aOnTop)
        XCTAssertNotEqual(aOnTop?.token, aAsLower?.token, "a re-shown image must be a new view so its fade runs")
        XCTAssertEqual(aOnTop?.token, layers.upperToken)
        XCTAssertFalse(layers.fadeCompleted(of: a))
        XCTAssertNil(layers.upper)
        XCTAssertTrue(layers.show(c), "after the fade ends the glow follows the carousel again")
    }

    func testAnUpperLayerKeepsItsIdentityWhenItBecomesTheLowerOne() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        let fading = layers.aliveLayers.last
        layers.fadeCompleted(of: a)
        XCTAssertEqual(layers.aliveLayers.first, fading, "the hand-over must not redraw the image")
    }

    func testNeverMoreThanTwoLayersAndNeverTheSameImageTwice() {
        var layers = GlowLayers<URL>()
        let urls = [a, b, c, a, c, b, b, a]
        for url in urls {
            layers.show(url)
            XCTAssertLessThanOrEqual(layers.alive.count, 2)
            XCTAssertEqual(Set(layers.alive).count, layers.alive.count)
        }
        while let fading = layers.upper {
            layers.fadeCompleted(of: fading)
            XCTAssertLessThanOrEqual(layers.alive.count, 2)
            XCTAssertEqual(Set(layers.alive).count, layers.alive.count)
        }
        XCTAssertEqual(layers.lower, urls.last)
        XCTAssertNil(layers.upper)
    }

    func testNilClearsEverythingIncludingTheQueue() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        layers.fadeCompleted(of: a)
        layers.show(b)
        layers.show(c)

        XCTAssertFalse(layers.show(nil))
        XCTAssertNil(layers.lower)
        XCTAssertNil(layers.upper)
        XCTAssertNil(layers.target)
        XCTAssertTrue(layers.alive.isEmpty)

        // The finished fade of the cleared layer is ignored.
        XCTAssertFalse(layers.fadeCompleted(of: b))
        XCTAssertTrue(layers.alive.isEmpty)
        XCTAssertFalse(layers.show(nil), "clearing an empty glow does nothing")
    }

    func testAFinishedFadeOfAnImageThatIsNotFadingIsIgnored() {
        var layers = GlowLayers<URL>()
        layers.show(a)
        XCTAssertFalse(layers.fadeCompleted(of: b))
        XCTAssertEqual(layers.upper, a)
        XCTAssertNil(layers.lower)
    }

    // MARK: Offset

    /// Where the glow's top lands on screen: its layout place (the top of the page's content) plus the offset.
    private func glowTop(scroll: CGFloat, inset: CGFloat = 62) -> CGFloat {
        let layoutTop = inset - scroll
        return layoutTop + GlowSpec.offset(layoutTop: layoutTop, topInset: inset, topOffset: BackdropLayout.topOffset)
    }

    func testTheGlowSitsAtTheTopAtRestAndRidesWithTheContent() {
        let top = BackdropLayout.topOffset
        XCTAssertEqual(glowTop(scroll: 0), top)
        // The scroll view carries it: its top moves one to one with the page, whatever the inset.
        XCTAssertEqual(glowTop(scroll: 220), top - 220)
        XCTAssertEqual(glowTop(scroll: 600), top - 600)
        XCTAssertEqual(glowTop(scroll: 220, inset: 47), top - 220)
        // The offset itself is constant while scrolling down, so nothing is computed from a lagging value.
        XCTAssertEqual(
            GlowSpec.offset(layoutTop: 62 - 10, topInset: 62, topOffset: top),
            GlowSpec.offset(layoutTop: 62 - 500, topInset: 62, topOffset: top)
        )
    }

    func testPulledPastTheTopTheGlowStaysAtTheTopWithNoGap() {
        let top = BackdropLayout.topOffset
        for pull: CGFloat in [1, 30, 90, 240] {
            XCTAssertEqual(glowTop(scroll: -pull), top, "pulled \(pull): the glow is not carried down, so nothing opens above it")
        }
    }

    func testTheGlowIsOffScreenOnceItsHeightHasScrolled() {
        XCTAssertEqual(HomeDesign.Metric.glowHeight, 440)
        let bottom = glowTop(scroll: 440) + HomeDesign.Metric.glowHeight
        XCTAssertLessThanOrEqual(bottom, 0)
    }

    // MARK: Height

    func testAtRestTheGlowEndsJustAboveTheClosedTilesAndOpenItIsFull() {
        // Tiles ending 318 down the screen: the box runs from -6 to 312, black 6 above their bottom edge.
        XCTAssertEqual(GlowSpec.height(tilesBottom: 318, isOpen: false), 318)
        XCTAssertEqual(BackdropLayout.topOffset + GlowSpec.height(tilesBottom: 318, isOpen: false), 318 - GlowSpec.restGap)
        XCTAssertEqual(GlowSpec.height(tilesBottom: 318, isOpen: true), HomeDesign.Metric.glowHeight)
        // Unmeasured: the estimate. Never shorter than the minimum or taller than the open glow.
        XCTAssertEqual(GlowSpec.height(tilesBottom: nil, isOpen: false), GlowSpec.height(tilesBottom: GlowSpec.estimatedTilesBottom, isOpen: false))
        XCTAssertEqual(GlowSpec.height(tilesBottom: 40, isOpen: false), GlowSpec.minimumHeight)
        XCTAssertEqual(GlowSpec.height(tilesBottom: 900, isOpen: false), HomeDesign.Metric.glowHeight)
    }

    func testTheModelPutsTheTilesBottomOnScreenFromWhatWasMeasured() {
        let model = HomeGlowModel()
        XCTAssertNil(model.tilesBottom)
        XCTAssertEqual(model.height, GlowSpec.height(tilesBottom: nil, isOpen: false))
        model.contentTopInset = 62
        XCTAssertNil(model.tilesBottom, "Both are needed")
        model.tilesTop = 92
        XCTAssertEqual(model.tilesBottom, 62 + 92 + TileGeometry.closedHeight)
        XCTAssertEqual(model.height, GlowSpec.height(tilesBottom: 62 + 92 + TileGeometry.closedHeight, isOpen: false))
        model.focused = media(id: "open", backdrop: nil, poster: "https://example.com/open.jpg")
        XCTAssertEqual(model.height, HomeDesign.Metric.glowHeight, "A tile is open")
        model.scrollOffset = 200
        XCTAssertEqual(model.tilesBottom, 62 + 92 + TileGeometry.closedHeight, "Scrolling moves nothing that was measured")
    }

    func testTheGlowSpecIsTheDesign() {
        XCTAssertEqual(GlowSpec.imageOpacity, 0.95)
        XCTAssertEqual(GlowSpec.imageWidthRatio, 1.513, accuracy: 0.0005)
        XCTAssertEqual(GlowSpec.imageHeight, 720)
        XCTAssertEqual(GlowSpec.imageTop, -170)
        XCTAssertEqual(GlowSpec.gradientStops.map(\.opacity), [0.22, 0.08, 0.70, 1.0])
        XCTAssertEqual(GlowSpec.gradientStops.map(\.location), [0, 0.35, 0.78, 1.0])
    }

    // MARK: Model

    func testActiveIsTheFocusedPosterElseTheIdleOne() {
        let model = HomeGlowModel()
        XCTAssertNil(model.active)
        XCTAssertNil(model.activeURL)

        model.idle = media(id: "idle", backdrop: "https://example.com/idle-backdrop.jpg", poster: "https://example.com/idle.jpg")
        XCTAssertEqual(model.active?.id, model.idle?.id)
        XCTAssertEqual(model.activeURL, URL(string: "https://example.com/idle-backdrop.jpg"))

        model.focused = media(id: "focus", backdrop: nil, poster: "https://example.com/focus.jpg")
        XCTAssertEqual(model.active?.ref.mediaId, "focus")
        XCTAssertEqual(model.activeURL, URL(string: "https://example.com/focus.jpg"), "no backdrop falls back to the poster")

        model.focused = nil
        XCTAssertEqual(model.active?.ref.mediaId, "idle")
    }

    func testScrollOffsetDoesNotChangeWhatIsShown() {
        let model = HomeGlowModel()
        model.idle = media(id: "idle", backdrop: nil, poster: "https://example.com/idle.jpg")
        let before = model.activeURL
        model.scrollOffset = 300
        XCTAssertEqual(model.activeURL, before)
    }

    // MARK: Requests

    func testTheGlowLoadsTheBakedGlowThumbnail() throws {
        let url = try XCTUnwrap(URL(string: "https://example.com/a.jpg"))
        let glow = BlurredArt.Style.glow
        // What the glow's image layer hands the image view.
        let layerRequest = try XCTUnwrap(SpineImagePipeline.request(
            url: url,
            thumbnailSize: glow.thumbnailSize,
            processors: [glow.processor]
        ))
        let expected = try XCTUnwrap(BlurredArt.request(url: url, style: .glow))
        let cache = ImagePipeline.shared.cache
        XCTAssertEqual(cache.makeImageCacheKey(for: layerRequest), cache.makeImageCacheKey(for: expected))
        XCTAssertEqual(glow.thumbnailSize, CGSize(width: 64, height: 77))
        XCTAssertNotNil(layerRequest.thumbnail)
        XCTAssertEqual(layerRequest.processors.count, 1)

        let m = media(id: "x", backdrop: "https://example.com/a.jpg", poster: nil)
        let prefetch = try XCTUnwrap(HomeGlow.request(for: m))
        XCTAssertEqual(cache.makeImageCacheKey(for: prefetch), cache.makeImageCacheKey(for: expected))
        XCTAssertNil(HomeGlow.request(for: nil))
        XCTAssertNil(HomeGlow.request(for: media(id: "none", backdrop: nil, poster: nil)))
    }

    func testNeighborsAreTwoEitherSideOfTheFocus() {
        let items = (0 ..< 8).map { media(id: "\($0)", backdrop: nil, poster: "https://example.com/\($0).jpg") }
        XCTAssertEqual(HomeGlow.neighbors(of: 4, in: items).map(\.ref.mediaId), ["2", "3", "5", "6"])
        XCTAssertEqual(HomeGlow.neighbors(of: 0, in: items).map(\.ref.mediaId), ["1", "2"])
        XCTAssertEqual(HomeGlow.neighbors(of: 7, in: items).map(\.ref.mediaId), ["5", "6"])
        XCTAssertTrue(HomeGlow.neighbors(of: 9, in: items).isEmpty)
    }

    // MARK: Two colors at rest

    func testAtRestInProgressIsOnTheLeftAndTrendingOnTheRight() throws {
        let model = HomeGlowModel()
        XCTAssertNil(model.art)

        model.trending = media(id: "hot", backdrop: nil, poster: "https://example.com/hot.jpg")
        XCTAssertEqual(model.art, GlowArt(left: try XCTUnwrap(URL(string: "https://example.com/hot.jpg")), right: nil),
                       "Trending alone fills the glow")

        model.idle = media(id: "reading", backdrop: "https://example.com/reading-backdrop.jpg", poster: nil)
        XCTAssertEqual(model.art?.left, URL(string: "https://example.com/reading-backdrop.jpg"))
        XCTAssertEqual(model.art?.right, URL(string: "https://example.com/hot.jpg"))

        model.focused = media(id: "swiped", backdrop: nil, poster: "https://example.com/swiped.jpg")
        XCTAssertEqual(model.art, GlowArt(left: try XCTUnwrap(URL(string: "https://example.com/swiped.jpg")), right: nil),
                       "an open carousel's poster fills the glow")

        model.focused = nil
        XCTAssertEqual(model.art?.right, URL(string: "https://example.com/hot.jpg"), "closing it goes back to the two colors")

        model.trending = model.idle
        XCTAssertNil(model.art?.right, "the same title on both sides is one image")
    }

    func testTheFrontPostersOfTheFansTakeOverBothSidesAtRest() throws {
        let model = HomeGlowModel()
        model.idle = media(id: "reading", backdrop: "https://example.com/reading-backdrop.jpg", poster: nil)
        model.trending = media(id: "hot", backdrop: nil, poster: "https://example.com/hot.jpg")

        model.restLeft = media(id: "third", backdrop: nil, poster: "https://example.com/third.jpg")
        model.restRight = media(id: "fifth", backdrop: nil, poster: "https://example.com/fifth.jpg")
        XCTAssertEqual(model.art?.left, URL(string: "https://example.com/third.jpg"))
        XCTAssertEqual(model.art?.right, URL(string: "https://example.com/fifth.jpg"))

        model.focused = media(id: "swiped", backdrop: nil, poster: "https://example.com/swiped.jpg")
        XCTAssertEqual(model.art, GlowArt(left: try XCTUnwrap(URL(string: "https://example.com/swiped.jpg")), right: nil))

        // A tile with nothing in it leaves its side to the page's own picture.
        model.focused = nil
        model.restLeft = nil
        model.restRight = nil
        XCTAssertEqual(model.art?.left, URL(string: "https://example.com/reading-backdrop.jpg"))
        XCTAssertEqual(model.art?.right, URL(string: "https://example.com/hot.jpg"))
    }

    func testTheRightImageFadesInFromTheMiddle() throws {
        let source = UIImage(cgImage: bitmap(width: 100, height: 10), scale: 3, orientation: .up)
        let output = try XCTUnwrap(GlowMeetProcessor().process(source)?.cgImage)
        XCTAssertEqual(output.width, 100)
        XCTAssertEqual(output.height, 10)
        let alpha = alphas(output)
        XCTAssertEqual(alpha(5, 5), 0, "clear on the left")
        XCTAssertEqual(alpha(30, 5), 0)
        XCTAssertGreaterThan(alpha(50, 5), 60, "half way in the middle")
        XCTAssertLessThan(alpha(50, 5), 200)
        XCTAssertEqual(alpha(70, 5), 255)
        XCTAssertEqual(alpha(95, 5), 255, "opaque on the right")
        XCTAssertNotEqual(
            GlowMeetProcessor().identifier,
            BlurredArt.Style.glow.processor.identifier
        )
        XCTAssertEqual(HomeGlow.meetingProcessors.map(\.identifier), [
            BlurredArt.Style.glow.processor.identifier, GlowMeetProcessor().identifier,
        ])
    }

    private func bitmap(width: Int, height: Int) -> CGImage {
        let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )!
        context.setFillColor(red: 0.8, green: 0.4, blue: 0.2, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        return context.makeImage()!
    }

    /// Alpha at (x, y), y down from the top.
    private func alphas(_ image: CGImage) -> (Int, Int) -> Int {
        let width = image.width
        let height = image.height
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let context = CGContext(
            data: &bytes, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )!
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return { x, y in Int(bytes[(y * width + x) * 4 + 3]) }
    }

    // MARK: Source

    func testTheGlowAppliesNothingLive() throws {
        let tiles = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home/Tiles")
        for name in ["HomeGlow.swift", "HomeGlowModel.swift"] {
            let source = try String(contentsOf: tiles.appendingPathComponent(name), encoding: .utf8)
            XCTAssertFalse(source.contains(".blur("), "\(name)")
            XCTAssertFalse(source.contains("compositingGroup"), "\(name)")
            XCTAssertFalse(source.contains("GeometryReader"), "\(name)")
            XCTAssertFalse(source.contains(".mask"), "\(name)")
        }
        let view = try String(contentsOf: tiles.appendingPathComponent("HomeGlow.swift"), encoding: .utf8)
        XCTAssertTrue(view.contains("PerfCounters.bump(.glowBody)"))
    }

    /// The glow trailed the content because a scroll offset written to state moved it a frame late. It is now part
    /// of the scrolling content and placed by a render-time effect, so it must read no scroll state.
    func testTheGlowIsPartOfTheScrollingContentAndReadsNoScrollState() throws {
        let features = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home")
        let glow = try String(contentsOf: features.appendingPathComponent("Tiles/HomeGlow.swift"), encoding: .utf8)
        XCTAssertFalse(glow.contains("scrollOffset"))
        XCTAssertTrue(glow.contains(".visualEffect"))

        let home = try String(contentsOf: features.appendingPathComponent("HomeView.swift"), encoding: .utf8)
        let scroll = try XCTUnwrap(home.range(of: "ScrollView(showsIndicators: false) {"))
        let glowUse = try XCTUnwrap(home.range(of: "HomeGlow(model: glow)"))
        let refresh = try XCTUnwrap(home.range(of: ".refreshable {"))
        XCTAssertTrue(scroll.upperBound < glowUse.lowerBound && glowUse.upperBound < refresh.lowerBound,
                      "HomeGlow sits inside the ScrollView's content, not behind it")
        XCTAssertEqual(home.components(separatedBy: "HomeGlow(model:").count - 1, 1)
    }

    private func media(id: String, backdrop: String?, poster: String?) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: id, seasonNumber: nil, episodeNumber: nil),
            title: "Title \(id)",
            posterUrl: poster,
            backdropUrl: backdrop
        )
    }
}
