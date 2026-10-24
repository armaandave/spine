import Nuke
import SwiftUI

/// Lazy grids only create a cell, and so only start its image, as it reaches the edge of the screen. Without a
/// look-ahead, even a slowly scrolled row arrives empty and fades its posters in.
enum ArtworkPrefetch {
    /// Four rows of a four-column poster grid, about one screen.
    static let lookahead = 16

    // Created on first use, after `SpineImagePipeline.install()` has replaced the shared pipeline. Requests run at
    // low priority, two at a time, and decode into the memory cache: visible cells still load first, and a
    // prefetched cell shows its image on the first frame.
    private static let prefetcher = ImagePrefetcher(pipeline: .shared)

    static func upcoming<Item: Identifiable>(after item: Item, in items: [Item]) -> ArraySlice<Item> {
        guard let index = items.firstIndex(where: { $0.id == item.id }) else { return [] }
        return upcoming(at: index, in: items)
    }

    /// The items after `index`, for grids that can hold the same item more than once.
    static func upcoming<Item>(at index: Int, in items: [Item]) -> ArraySlice<Item> {
        let start = index + 1
        guard start <= items.count else { return [] }
        return items[start ..< min(start + lookahead, items.count)]
    }

    static func prefetch(_ requests: [ImageRequest]) {
        guard !requests.isEmpty else { return }
        prefetcher.startPrefetching(with: requests)
    }
}

extension MediaSummary {
    /// The request of a `MediaArtwork` showing this media's poster in `slot`.
    func artworkRequest(slot: PosterSlot) -> ImageRequest? {
        MediaArtwork.imageRequest(url: displayPosterURL, slot: slot, mediaType: ref.mediaType, orientation: posterOrientation)
    }
}

extension View {
    /// When this cell appears, prefetches the artwork of the next `ArtworkPrefetch.lookahead` items. `request` must
    /// build the same request as the cell's `MediaArtwork`, or the prefetched image lands in a different cache entry.
    func prefetchingArtwork<Item: Identifiable>(
        after item: Item,
        in items: [Item],
        request: @escaping (Item) -> ImageRequest?
    ) -> some View {
        onAppear {
            ArtworkPrefetch.prefetch(ArtworkPrefetch.upcoming(after: item, in: items).compactMap(request))
        }
    }

    /// `prefetchingArtwork(after:in:request:)` for the cell at `index`, when the same item can appear twice.
    func prefetchingArtwork<Item>(
        at index: Int,
        in items: [Item],
        request: @escaping (Item) -> ImageRequest?
    ) -> some View {
        onAppear {
            ArtworkPrefetch.prefetch(ArtworkPrefetch.upcoming(at: index, in: items).compactMap(request))
        }
    }
}
