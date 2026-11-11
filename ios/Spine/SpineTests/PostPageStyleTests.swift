import Nuke
import XCTest
@testable import Spine

@MainActor
final class PostPageStyleTests: XCTestCase {
    // MARK: Source scans

    private static func source(_ relativePath: String) throws -> String {
        let spineRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(
            contentsOf: spineRoot.appendingPathComponent("Spine/\(relativePath)"),
            encoding: .utf8
        )
    }

    private static let pagePaths = [
        "Features/Diary/DiaryLogDetailView.swift",
        "Features/Social/ProgressPostDetailView.swift",
    ]

    /// The page both draw on: background, buttons, hero, comments.
    private static let sharedPagePath = "Features/Social/PostPage.swift"

    func testPagesDrawTheBakedArtAndNothingLive() throws {
        for path in Self.pagePaths + [Self.sharedPagePath] {
            let text = try Self.source(path)
            XCTAssertFalse(text.contains("LogHeroArtwork"), "\(path) still names LogHeroArtwork")
            XCTAssertFalse(text.contains(".blur("), "\(path) blurs live")
            XCTAssertFalse(text.contains("Material"), "\(path) uses a material")
            XCTAssertFalse(text.contains("SpinePageBackground"), "\(path) still draws the grey page")
        }
        for path in Self.pagePaths {
            let text = try Self.source(path)
            XCTAssertTrue(text.contains("PostPage("), "\(path) does not draw on the shared page")
            XCTAssertTrue(text.contains("PostHero("), "\(path) does not draw the shared hero")
        }
        let shared = try Self.source(Self.sharedPagePath)
        XCTAssertTrue(shared.contains("PostPageBackground("), "the shared page has no page background")
        XCTAssertTrue(shared.contains("PostHeroPoster("), "the shared hero has no poster")
    }

    func testPageBackgroundUsesTheBakedPageStyle() throws {
        let diary = try Self.source("Features/Diary/DiaryLogDetailView.swift")
        XCTAssertTrue(diary.contains("BlurredArtBackground(url: url, accentHex: accentHex, style: PostPageStyle.artStyle)"))
        XCTAssertEqual(PostPageStyle.artStyle, .page)
    }

    func testPagesKeepTheirControlsAndIdentifiers() throws {
        let path = Self.sharedPagePath
        let text = try Self.source(path)
        XCTAssertTrue(text.contains("\"Back\""), "\(path) lost the Back button")
        XCTAssertTrue(text.contains("PostCommentBar(model: engagement"), "\(path) lost the comment bar")
        // Each post page hides the tab bar, swipes back, and closes for the Home button through its page setup.
        for page in Self.pagePaths {
            XCTAssertTrue(try Self.source(page).contains(".navigationPage(.post)"), "\(page) isn't set up as a post page")
        }
        let bar = try Self.source("Features/Social/ActivityPostViews.swift")
        for identifier in ["post.like", "post.comment-field", "post.comment-send"] {
            XCTAssertTrue(bar.contains("\"\(identifier)\""), "\(identifier) is gone")
        }
    }

    func testProgressPageKeepsPhaseFiveEditAndDelete() throws {
        let text = try Self.source("Features/Social/ProgressPostDetailView.swift")
        for kept in ["viewModel.controls.showsEdit", "viewModel.controls.showsDelete", "leaveIfChanged", "ProgressRowEditSheet"] {
            XCTAssertTrue(text.contains(kept), "\(kept) was touched")
        }
    }

    // MARK: Style

    func testPosterSlotIsOneTwentyByOneEighty() {
        XCTAssertEqual(PosterSlot.postHero.size, CGSize(width: 120, height: 180))
        XCTAssertEqual(PosterSlot.postHero.cornerRadius, 10)
    }

    func testPageArtRequestIsTheBakedPageRequest() throws {
        let url = try XCTUnwrap(URL(string: "https://example.com/backdrop.jpg"))
        let request = try XCTUnwrap(PostPageStyle.artRequest(url: url))
        let expected = try XCTUnwrap(BlurredArt.request(url: url, style: .page))
        XCTAssertEqual(request.url, expected.url)
        XCTAssertEqual(request.processors.map(\.identifier), expected.processors.map(\.identifier))
        XCTAssertEqual(request.processors.map(\.identifier), [BlurredArt.Style.page.processor.identifier])
        XCTAssertEqual(request.thumbnail, expected.thumbnail)
        XCTAssertNotNil(request.thumbnail)
        XCTAssertNil(PostPageStyle.artRequest(url: nil))
    }

    func testWashDeepensTowardTheBottom() {
        let stops = PostPageStyle.washStops
        XCTAssertEqual(stops.map(\.location), [0, 0.45, 1])
        XCTAssertEqual(stops.map(\.opacity), [0, 0.14, 0.48])
    }

    func testALogsArtIsItsOwnPosterAndDetailsOnlyStandIn() {
        XCTAssertEqual(
            PostPageStyle.artURL(posterURL: "https://example.com/row.jpg", detailPosterURL: "https://example.com/d.jpg")?.absoluteString,
            "https://example.com/row.jpg"
        )
        XCTAssertEqual(
            PostPageStyle.artURL(posterURL: nil, detailPosterURL: "https://example.com/d.jpg")?.absoluteString,
            "https://example.com/d.jpg"
        )
        XCTAssertNil(PostPageStyle.artURL(posterURL: nil, detailPosterURL: nil))
        XCTAssertNil(PostPageStyle.artURL(posterURL: "", detailPosterURL: nil))
    }

    func testTheAccentIsTheRowsAndDetailsOnlyFillAGap() {
        XCTAssertEqual(PostPageStyle.accentHex(summary: "#112233", detail: "#445566"), "#112233")
        XCTAssertEqual(PostPageStyle.accentHex(summary: nil, detail: "#445566"), "#445566")
        XCTAssertNil(PostPageStyle.accentHex(summary: nil, detail: nil))
    }

    func testAProgressPostsArtIsTheCardsArtNotTheBackdrop() {
        let show = media(type: "tv", poster: "https://example.com/p.jpg", backdrop: "https://example.com/b.jpg")
        XCTAssertEqual(PostPageStyle.artURL(for: show)?.absoluteString, "https://example.com/p.jpg")
        XCTAssertEqual(FeedCardPresenter.artURL(for: show), "https://example.com/p.jpg")
    }

    func testThePageHidesTheNavigationBarAndDrawsOneRowOfButtons() throws {
        let text = try Self.source("Features/Social/ProgressPostDetailView.swift")
        let shared = try Self.source(Self.sharedPagePath)
        XCTAssertFalse(text.contains(".toolbar {"), "Edit and Delete are drawn by the page, beside Back")
        XCTAssertFalse(shared.contains(".toolbar {"), "Edit and Delete are drawn by the page, beside Back")
        XCTAssertTrue(shared.contains(".toolbar(.hidden, for: .navigationBar)"))
        XCTAssertTrue(text.contains("PostProgressMeterView("))
    }

    func testTheMeterColorsAreCountedOnlyForAPosterWithNoServerPalette() {
        let withPalette = FeedCardArtSpec(
            posterURL: "https://example.com/p.jpg", backgroundURL: nil, accentHex: nil, guessInk: .dark,
            isSquare: false, planningPosterURLs: [], posterPalette: [PosterSwatch(hex: "#112233", share: 1)]
        )
        let without = FeedCardArtSpec(
            posterURL: "https://example.com/p.jpg", backgroundURL: nil, accentHex: nil, guessInk: .dark,
            isSquare: false, planningPosterURLs: []
        )
        XCTAssertNil(FeedMeterColors.countURL(hasMeter: false, art: without))
        XCTAssertNil(FeedMeterColors.countURL(hasMeter: true, art: withPalette))
        XCTAssertEqual(FeedMeterColors.countURL(hasMeter: true, art: without)?.absoluteString, "https://example.com/p.jpg")
    }

    func testStarSymbolsDrawOnlyWhatIsEarned() {
        XCTAssertEqual(PostPageStyle.starSymbols(rating: "4.5"), ["star.fill", "star.fill", "star.fill", "star.fill", "star.leadinghalf.filled"])
        XCTAssertEqual(PostPageStyle.starSymbols(rating: "3.0"), ["star.fill", "star.fill", "star.fill"])
        XCTAssertEqual(PostPageStyle.starSymbols(rating: "0.5"), ["star.leadinghalf.filled"])
        XCTAssertEqual(PostPageStyle.starSymbols(rating: "5").count, 5)
        XCTAssertTrue(PostPageStyle.starSymbols(rating: "0").isEmpty)
    }

    func testReviewSplitsAtBlankLines() {
        XCTAssertEqual(
            PostPageStyle.paragraphs("first line\nsame paragraph\n\n  second  \n\n\n\nthird\r\n\r\nfourth"),
            ["first line\nsame paragraph", "second", "third", "fourth"]
        )
        XCTAssertTrue(PostPageStyle.paragraphs("  \n\n ").isEmpty)
    }

    func testTheLineUnderTheTitleSaysTheYearOnceThenTheRuntime() {
        let ref = MediaRef(itemId: 1, source: "tmdb", mediaType: "movie", mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        let movie = MediaDetail(ref: ref, title: "Sinners", releaseDate: "2025-04-18", details: ["runtime": .string("2h 18m")])
        XCTAssertEqual(PostHero.metadata(displayTitle: "Sinners (2025)", detail: movie), "2025 - 2h 18m")
        XCTAssertEqual(PostHero.metadata(displayTitle: "Sinners", detail: movie), "2025 - 2h 18m")
        XCTAssertEqual(PostHero.metadata(displayTitle: "Sinners (2025)", detail: nil), "2025")
        XCTAssertNil(PostHero.metadata(displayTitle: "A Feast for Crows", detail: nil))
    }

    func testTheTitleWaitsUntilItKnowsWhetherItIsALogo() throws {
        let ref = MediaRef(itemId: 1, source: "tmdb", mediaType: "tv", mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        let withLogo = MediaDetail(ref: ref, title: "Pluribus", logoUrl: "https://example.com/logo.png")
        let withoutLogo = MediaDetail(ref: ref, title: "Intermezzo")
        let request = try XCTUnwrap(PostHero.logoRequest(for: withLogo))
        XCTAssertNil(PostHero.logoRequest(for: withoutLogo))
        XCTAssertNil(PostHero.logoRequest(for: nil))

        // Details still coming: the words wait.
        XCTAssertEqual(PostHero.titleArt(isLoadingDetail: true, logoRequest: nil, isLogoCached: { _ in true }), .pending)
        // No logo (or no details at all): text straight away.
        XCTAssertEqual(PostHero.titleArt(isLoadingDetail: false, logoRequest: nil, isLogoCached: { _ in true }), .text)
        // A logo already in the cache shows at once; one still downloading waits.
        XCTAssertEqual(PostHero.titleArt(isLoadingDetail: false, logoRequest: request, isLogoCached: { _ in true }), .logo)
        XCTAssertEqual(PostHero.titleArt(isLoadingDetail: false, logoRequest: request, isLogoCached: { _ in false }), .pending)
    }

    func testATitleLosesOnlyAYearAtItsEnd() {
        XCTAssertEqual(PostHero.titleParts("Dune (2021)").title, "Dune")
        XCTAssertEqual(PostHero.titleParts("Dune (2021)").year, "2021")
        XCTAssertEqual(PostHero.titleParts("Blade Runner 2049").title, "Blade Runner 2049")
        XCTAssertNil(PostHero.titleParts("Persona (Director's Cut)").year)
    }

    // MARK: Fixtures

    private func media(type: String, poster: String?, backdrop: String?) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(itemId: 1, source: "tmdb", mediaType: type, mediaId: "1", seasonNumber: nil, episodeNumber: nil),
            title: "The Bear",
            posterUrl: poster,
            backdropUrl: backdrop
        )
    }

    private func detail(mediaType: String, poster: String?, backdrop: String?) -> MediaDetail {
        MediaDetail(
            ref: MediaRef(
                itemId: 1,
                source: "tmdb",
                mediaType: mediaType,
                mediaId: "1",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Past Lives",
            posterUrl: poster,
            backdropUrl: backdrop
        )
    }
}
