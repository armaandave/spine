import Nuke
import XCTest
@testable import Spine

final class SpineImagePipelineTests: XCTestCase {
    func testDiskCacheIgnoresHTTPCacheHeaders() throws {
        let configuration = SpineImagePipeline.makeConfiguration()

        let dataCache = try XCTUnwrap(configuration.dataCache as? DataCache)
        XCTAssertEqual(dataCache.sizeLimit, SpineImagePipeline.diskCacheSizeLimit)
        // URLCache would follow each host's headers (3 hours for OpenLibrary, nothing for Hardcover).
        let dataLoader = try XCTUnwrap(configuration.dataLoader as? DataLoader)
        XCTAssertNil(dataLoader.session.configuration.urlCache)
    }

    func testDiskCacheLivesInCachesSoIOSCanReclaimIt() throws {
        let dataCache = try XCTUnwrap(SpineImagePipeline.makeConfiguration().dataCache as? DataCache)
        let caches = try XCTUnwrap(FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first)

        XCTAssertEqual(
            dataCache.path.standardizedFileURL,
            caches.appendingPathComponent(SpineImagePipeline.diskCacheName, isDirectory: true).standardizedFileURL
        )
    }

    func testAppLaunchInstallsTheSpinePipeline() {
        // The unit-test host still runs SpineApp.init, which installs the pipeline.
        XCTAssertTrue(ImagePipeline.shared.configuration.dataCache is DataCache)
    }

    func testRequestWithoutURLIsNil() {
        XCTAssertNil(SpineImagePipeline.request(url: nil, thumbnailSize: CGSize(width: 80, height: 120)))
    }

    func testRequestWithoutSizeDecodesFullImage() throws {
        let request = try XCTUnwrap(SpineImagePipeline.request(url: Self.posterURL, thumbnailSize: nil))

        XCTAssertEqual(request.url, Self.posterURL)
        XCTAssertNil(request.thumbnail)
    }

    func testRequestWithSizeDecodesThumbnailThatFillsTheSlot() throws {
        let size = CGSize(width: 80, height: 120)
        let request = try XCTUnwrap(SpineImagePipeline.request(url: Self.posterURL, thumbnailSize: size))

        XCTAssertEqual(
            request.thumbnail,
            ImageRequest.ThumbnailOptions(size: size, unit: .points, contentMode: .aspectFill)
        )
    }

    private static let posterURL = URL(string: "https://image.tmdb.org/t/p/original/poster.jpg")!
}
