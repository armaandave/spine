import Nuke
import XCTest
@testable import Spine

@MainActor
final class ArtworkPrefetchTests: XCTestCase {
    func testUpcomingIsTheNextScreenOfItems() {
        let items = (0 ..< 40).map(Item.init)

        XCTAssertEqual(ArtworkPrefetch.upcoming(after: items[3], in: items).map(\.id), Array(4 ..< 20))
    }

    func testUpcomingStopsAtTheEndOfTheLoadedItems() {
        let items = (0 ..< 10).map(Item.init)

        XCTAssertEqual(ArtworkPrefetch.upcoming(after: items[6], in: items).map(\.id), [7, 8, 9])
        XCTAssertTrue(ArtworkPrefetch.upcoming(after: items[9], in: items).isEmpty)
    }

    func testUpcomingIsEmptyForAnItemNoLongerInTheList() {
        let items = (0 ..< 10).map(Item.init)

        XCTAssertTrue(ArtworkPrefetch.upcoming(after: Item(id: 99), in: items).isEmpty)
    }

    func testPrefetchHitsTheMemoryCacheEntryTheGridCellLoads() throws {
        let media = MediaSummary(
            ref: MediaRef(itemId: nil, source: "igdb", mediaType: "game", mediaId: "1", seasonNumber: nil, episodeNumber: nil),
            title: "Game",
            posterUrl: "https://images.igdb.com/igdb/image/upload/t_original/stored.jpg",
            customPosterUrl: "https://images.igdb.com/igdb/image/upload/t_original/picked.jpg",
            posterOrientation: .landscape
        )
        // What a `.tagGrid` `MediaArtwork` for this media hands `SpineAsyncImage`.
        let cellRequest = try XCTUnwrap(SpineImagePipeline.request(
            url: URL(string: "https://images.igdb.com/igdb/image/upload/t_original/picked.jpg"),
            thumbnailSize: PosterSlot.tagGrid.artworkSize(mediaType: "game", orientation: .landscape)
        ))
        let prefetchRequest = try XCTUnwrap(media.artworkRequest(slot: .tagGrid))
        let cache = ImagePipeline.shared.cache

        XCTAssertEqual(cache.makeImageCacheKey(for: prefetchRequest), cache.makeImageCacheKey(for: cellRequest))
    }

    func testArtworkWithoutAURLHasNothingToPrefetch() {
        XCTAssertNil(MediaArtwork.imageRequest(url: nil, slot: .pickerGrid))
        XCTAssertNil(MediaArtwork.imageRequest(url: "  ", slot: .pickerGrid))
    }
}

private struct Item: Identifiable {
    let id: Int
}
