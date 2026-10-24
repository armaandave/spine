import Foundation
import Nuke

/// Every remote image in the app loads through this pipeline (via `SpineAsyncImage`).
enum SpineImagePipeline {
    static let diskCacheName = "com.armaan.Spine.ImageCache"
    // A heavy library across every media type is a few thousand covers: ~200 MB at the usual 40–135 KB, plus
    // headroom for 0.4–0.9 MB originals (IGDB covers, picker-chosen TMDB posters) and backdrops. Nuke's 150 MB
    // default would evict part of that library between launches. The cache lives in Caches, so iOS can still
    // reclaim it when the phone runs low on storage, and Nuke trims least-recently-used files first.
    static let diskCacheSizeLimit = 300 * 1024 * 1024

    /// Decoded images live in Nuke's shared memory cache, sized from device RAM and purged on memory warnings.
    /// Downloaded bytes live in a `DataCache`, which ignores HTTP cache headers: image hosts disagree wildly
    /// (TMDB and IGDB allow a year, OpenLibrary's archive.org redirect three hours, Hardcover sends none),
    /// so every source gets the same LRU disk cache instead.
    static func makeConfiguration() -> ImagePipeline.Configuration {
        .withDataCache(name: diskCacheName, sizeLimit: diskCacheSizeLimit)
    }

    static func install() {
        ImagePipeline.shared = ImagePipeline(configuration: makeConfiguration())
    }

    /// With a `thumbnailSize` (in points), ImageIO decodes straight to that size: a 2000×3000 poster in an
    /// 80×120 grid cell costs ~0.35 MB of memory instead of ~24 MB. The disk cache still keeps the original
    /// bytes, so a full-size view of the same URL needs no new download.
    /// `processors` run once, off the main thread, and their output is cached like the image itself.
    static func request(url: URL?, thumbnailSize: CGSize?, processors: [any ImageProcessing] = []) -> ImageRequest? {
        guard let url else { return nil }
        var request = ImageRequest(url: url, processors: processors)
        if let thumbnailSize {
            request.thumbnail = ImageRequest.ThumbnailOptions(size: thumbnailSize, unit: .points, contentMode: .aspectFill)
        }
        return request
    }
}
