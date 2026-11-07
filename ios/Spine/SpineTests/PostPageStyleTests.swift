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

    func testPagesDrawTheBakedArtAndNothingLive() throws {
        for path in Self.pagePaths {
            let text = try Self.source(path)
            XCTAssertFalse(text.contains("LogHeroArtwork"), "\(path) still names LogHeroArtwork")
            XCTAssertFalse(text.contains(".blur("), "\(path) blurs live")
            XCTAssertFalse(text.contains("Material"), "\(path) uses a material")
            XCTAssertFalse(text.contains("SpinePageBackground"), "\(path) still draws the grey page")
            XCTAssertTrue(text.contains("PostPageBackground("), "\(path) has no page background")
            XCTAssertTrue(text.contains("PostHeroPoster("), "\(path) has no hero poster")
        }
    }

    func testPageBackgroundUsesTheBakedPageStyle() throws {
        let diary = try Self.source("Features/Diary/DiaryLogDetailView.swift")
        XCTAssertTrue(diary.contains("BlurredArtBackground(url: url, accentHex: accentHex, style: PostPageStyle.artStyle)"))
        XCTAssertEqual(PostPageStyle.artStyle, .page)
    }

    func testPagesKeepTheirControlsAndIdentifiers() throws {
        for path in Self.pagePaths {
            let text = try Self.source(path)
            XCTAssertTrue(text.contains("\"Back\""), "\(path) lost the Back button")
            XCTAssertTrue(text.contains("PostCommentBar(model: viewModel.engagement"), "\(path) lost the comment bar")
            XCTAssertTrue(text.contains(".toolbar(.hidden, for: .tabBar)"), "\(path) shows the tab bar")
            XCTAssertTrue(text.contains(".dismissExplorationOnReturnHome()"), "\(path) lost the Home return")
            XCTAssertTrue(text.contains("edgeSwipeBackGesture"), "\(path) lost the edge swipe")
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

    func testArtIsTheBackdropOfAMovieOrShowElseThePoster() throws {
        let withBackdrop = detail(mediaType: "movie", poster: "https://example.com/p.jpg", backdrop: "https://example.com/b.jpg")
        XCTAssertEqual(
            PostPageStyle.artURL(mediaType: "movie", posterURL: "https://example.com/list.jpg", detail: withBackdrop)?.absoluteString,
            "https://example.com/b.jpg"
        )
        let show = detail(mediaType: "tv", poster: "https://example.com/p.jpg", backdrop: "https://example.com/b.jpg")
        XCTAssertEqual(
            PostPageStyle.artURL(mediaType: "tv", posterURL: nil, detail: show)?.absoluteString,
            "https://example.com/b.jpg"
        )
        let book = detail(mediaType: "book", poster: "https://example.com/cover.jpg", backdrop: "https://example.com/b.jpg")
        XCTAssertEqual(
            PostPageStyle.artURL(mediaType: "book", posterURL: nil, detail: book)?.absoluteString,
            "https://example.com/cover.jpg"
        )
        let noBackdrop = detail(mediaType: "movie", poster: "https://example.com/p.jpg", backdrop: nil)
        XCTAssertEqual(
            PostPageStyle.artURL(mediaType: "movie", posterURL: nil, detail: noBackdrop)?.absoluteString,
            "https://example.com/p.jpg"
        )
    }

    func testArtFallsBackToTheRowsPosterBeforeTheDetailLoads() {
        XCTAssertEqual(
            PostPageStyle.artURL(mediaType: "movie", posterURL: "https://example.com/row.jpg", detail: nil)?.absoluteString,
            "https://example.com/row.jpg"
        )
        XCTAssertNil(PostPageStyle.artURL(mediaType: "movie", posterURL: nil, detail: nil))
        XCTAssertNil(PostPageStyle.artURL(mediaType: "movie", posterURL: "", detail: nil))
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

    // MARK: Fixtures

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
