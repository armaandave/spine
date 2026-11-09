import Nuke
import SwiftUI
import XCTest
@testable import Spine

@MainActor
final class FeedCardArtTests: XCTestCase {
    private let cache = ImagePipeline.shared.cache

    /// What Nuke's caches key a request by: equal keys share one cached image.
    private func key(_ request: ImageRequest?) throws -> ImageCacheKey {
        cache.makeImageCacheKey(for: try XCTUnwrap(request))
    }

    private func card(
        id: Int,
        poster: String? = nil,
        mediaType: String = "movie",
        avatar: String? = nil
    ) throws -> FeedCardModel {
        let item = try FeedTestItems.item(
            id: id,
            objectID: id * 10,
            objectFields: "\"rating\": \"4\", \"liked\": false, \"is_rewatch\": false",
            avatar: avatar,
            mediaType: mediaType,
            mediaFields: "\"poster_url\": \"\(poster ?? "https://p/\(id).jpg")\""
        )
        return FeedCardPresenter.model(for: item, isNewestOfTitle: true)
    }

    private func entries(cards: Int) throws -> [FeedCardModel] {
        try (0 ..< cards).map { try card(id: $0 + 1) }
    }

    private func ids(_ models: [FeedCardModel]) -> [Int] {
        models.map(\.id)
    }

    // MARK: Requests are the views' requests

    func testThePosterRequestIsTheSlotsArtworkRequest() throws {
        let portrait = try card(id: 1)
        // What the card's MediaArtwork hands SpineAsyncImage: the slot's 60 x 90 thumbnail.
        let view = try XCTUnwrap(SpineImagePipeline.request(
            url: URL(string: "https://p/1.jpg"),
            thumbnailSize: CGSize(width: 60, height: 90)
        ))
        let prefetch = try XCTUnwrap(FeedCardArt.posterRequest(for: portrait.art))
        XCTAssertEqual(try key(prefetch), try key(view))
        XCTAssertTrue(prefetch.processors.isEmpty)
        // Another size is another cache entry, so the size is part of what is being proven equal.
        let other = SpineImagePipeline.request(url: URL(string: "https://p/1.jpg"), thumbnailSize: CGSize(width: 74, height: 111))
        XCTAssertNotEqual(try key(prefetch), try key(other))
    }

    func testSquareArtIs90ByDefault() throws {
        let album = try card(id: 2, mediaType: "music")
        XCTAssertTrue(album.art.isSquare)
        let request = try XCTUnwrap(FeedCardArt.posterRequest(for: album.art))
        let square = SpineImagePipeline.request(url: URL(string: "https://p/2.jpg"), thumbnailSize: CGSize(width: 90, height: 90))
        XCTAssertEqual(try key(request), try key(square))
        XCTAssertEqual(FeedCardArt.posterSize(isSquare: true), CGSize(width: 90, height: 90))
        XCTAssertEqual(FeedCardArt.posterSize(isSquare: false), CGSize(width: 60, height: 90))
    }

    func testTheBackgroundRequestIsTheBakedCardArt() throws {
        let model = try card(id: 3)
        let view = try XCTUnwrap(BlurredArt.request(url: URL(string: "https://p/3.jpg"), style: .card))
        let prefetch = try XCTUnwrap(FeedCardArt.backgroundRequest(for: model.art))
        XCTAssertEqual(try key(prefetch), try key(view))
        XCTAssertEqual(BlurredArt.Style.card.thumbnailSize, CGSize(width: 40, height: 24))
        XCTAssertEqual(prefetch.processors.map { $0.identifier }, [BlurredArt.Style.card.processor.identifier])
        // The poster and the art share a URL and never an entry.
        XCTAssertNotEqual(try key(prefetch), try key(FeedCardArt.posterRequest(for: model.art)))
    }

    func testAvatarsAreRequestedAt20Points() throws {
        let request = try XCTUnwrap(FeedCardArt.avatarRequest(urlString: "https://p/avatar.jpg"))
        XCTAssertEqual(FeedCardArt.avatarSize, CGSize(width: 20, height: 20))
        let view = try XCTUnwrap(SpineImagePipeline.request(
            url: URL(string: "https://p/avatar.jpg"), thumbnailSize: CGSize(width: 20, height: 20)
        ))
        XCTAssertEqual(try key(request), try key(view))
        XCTAssertNil(FeedCardArt.avatarRequest(urlString: nil))
        XCTAssertNil(FeedCardArt.avatarRequest(urlString: "  "))
    }

    func testPrefetchRequestsAreEveryImageTheCardShows() throws {
        let model = try card(id: 4, avatar: "https://p/av.jpg")
        let requests = FeedCardArt.prefetchRequests(for: model)
        XCTAssertEqual(requests.count, 3)
        let urls = requests.compactMap { $0.url?.absoluteString }
        XCTAssertEqual(urls, ["https://p/4.jpg", "https://p/4.jpg", "https://p/av.jpg"])
        let url = URL(string: "https://p/4.jpg")
        let expected = [
            SpineImagePipeline.request(url: url, thumbnailSize: CGSize(width: 60, height: 90)),
            BlurredArt.request(url: url, style: .card),
            SpineImagePipeline.request(url: URL(string: "https://p/av.jpg"), thumbnailSize: CGSize(width: 20, height: 20)),
        ]
        XCTAssertEqual(try requests.map { try key($0) }, try expected.map { try key($0) })
    }

    func testACardWithNoArtAsksForNothing() throws {
        let item = try FeedTestItems.log(id: 5, rating: "3", mediaFields: "")
        let model = FeedCardPresenter.model(for: item, isNewestOfTitle: true)
        XCTAssertTrue(FeedCardArt.prefetchRequests(for: model).isEmpty)
    }

    func testAPlanningFanPrefetchesItsPostersAtTheSlotSize() throws {
        let row = try FeedTestItems.planningRow(id: -100, count: 3)
        let day = try FeedTestItems.planningDay(
            titles: [("A", "https://p/a.jpg"), ("B", "https://p/b.jpg"), ("C", "https://p/c.jpg"), ("D", "https://p/d.jpg")],
            count: 4
        )
        let model = FeedCardPresenter.model(for: row, isNewestOfTitle: true, planning: day)
        XCTAssertEqual(model.art.planningPosterURLs.count, 3)
        let requests = FeedCardArt.prefetchRequests(for: model)
        // Three posters, the baked art (from the first poster) and no avatar.
        XCTAssertEqual(
            requests.compactMap { $0.url?.absoluteString },
            ["https://p/a.jpg", "https://p/b.jpg", "https://p/c.jpg", "https://p/a.jpg"]
        )
        for (request, name) in zip(requests.prefix(3), ["a", "b", "c"]) {
            let expected = SpineImagePipeline.request(
                url: URL(string: "https://p/\(name).jpg"), thumbnailSize: CGSize(width: 60, height: 90)
            )
            XCTAssertEqual(try key(request), try key(expected), name)
        }
    }

    // MARK: Fan geometry

    func testTheFanStepsDownAndInFromTheMock() {
        let slots = (0 ..< 3).map { FeedCardStack.slot(index: $0, isSquare: false) }
        XCTAssertEqual(slots[0], FeedStackSlot(x: 12, y: 12, width: 60, height: 90, dimming: 0))
        XCTAssertEqual(slots[1], FeedStackSlot(x: 22, y: 20, width: 54, height: 81, dimming: 0.25))
        XCTAssertEqual(slots[2], FeedStackSlot(x: 32, y: 28, width: 48, height: 72, dimming: 0.5))
        for slot in slots {
            XCTAssertLessThanOrEqual(slot.x + slot.width, HomeDesign.Metric.cardTextLeading, "A fan poster must stay left of the text")
            XCTAssertEqual(slot.width / slot.height, 2.0 / 3.0, accuracy: 0.0001)
        }
        for slot in slots + (0 ..< 3).map({ FeedCardStack.slot(index: $0, isSquare: true) }) {
            XCTAssertLessThanOrEqual(
                slot.y + slot.height,
                HomeDesign.Metric.cardHeight - HomeDesign.Metric.cardInset,
                "A fan poster must keep the card's bottom inset"
            )
        }
    }

    func testASquareFanStaysLeftOfTheWiderText() {
        for index in 0 ..< 3 {
            let slot = FeedCardStack.slot(index: index, isSquare: true)
            XCTAssertLessThanOrEqual(slot.x + slot.width, HomeDesign.Metric.cardTextLeadingSquare)
            XCTAssertEqual(slot.width, slot.height)
        }
        XCTAssertEqual(FeedCardStack.slot(index: 0, isSquare: true).width, 90)
    }

    // MARK: Prefetch window

    private final class Recorder {
        var batches: [[ImageRequest]] = []
        var urls: [String] { batches.flatMap { $0 }.compactMap { $0.url?.absoluteString } }
        func sink(_ requests: [ImageRequest]) { batches.append(requests) }
    }

    private func prefetchedCardIDs(_ recorder: Recorder) -> [Int] {
        // Every card here has a poster at /<id>.jpg, requested once as the poster and once as the baked art.
        recorder.urls.compactMap { url in
            Int(url.replacingOccurrences(of: "https://p/", with: "").replacingOccurrences(of: ".jpg", with: ""))
        }
    }

    func testTheWindowIsTheNextElevenCards() throws {
        XCTAssertEqual(FeedCardArt.lookahead, 11)
        let list = try entries(cards: 30)
        let prefetcher = FeedCardPrefetcher(sink: { _ in })
        // Entry 0 is card 1; the window after it is cards 2 to 12.
        XCTAssertEqual(ids(prefetcher.cardsToPrefetch(appearing: 0, in: list)), Array(2 ... 12))
        XCTAssertEqual(ids(prefetcher.cardsToPrefetch(appearing: 1, in: list)), Array(3 ... 13))
    }

    func testEachAppearanceExtendsTheWindowByTheCardsNotYetAsked() throws {
        let list = try entries(cards: 30)
        let recorder = Recorder()
        let prefetcher = FeedCardPrefetcher(sink: recorder.sink)

        prefetcher.entryAppeared(at: 0, in: list)
        XCTAssertEqual(Set(prefetchedCardIDs(recorder)), Set(2 ... 12))

        // The next card on screen only adds the one card that came into the window.
        recorder.batches.removeAll()
        prefetcher.entryAppeared(at: 1, in: list)
        XCTAssertEqual(Set(prefetchedCardIDs(recorder)), [13])

        recorder.batches.removeAll()
        prefetcher.entryAppeared(at: 2, in: list)
        XCTAssertEqual(Set(prefetchedCardIDs(recorder)), [14])
    }

    func testAFastFlingExtendsFromWhereItLands() throws {
        let list = try entries(cards: 60)
        let recorder = Recorder()
        let prefetcher = FeedCardPrefetcher(sink: recorder.sink)
        prefetcher.entryAppeared(at: 0, in: list)
        recorder.batches.removeAll()

        // Landing on entry 40 (card 41): the 11 cards after it, nothing before.
        prefetcher.entryAppeared(at: 40, in: list)
        let landed = Set(prefetchedCardIDs(recorder))
        XCTAssertEqual(landed.count, 11)
        XCTAssertTrue(landed.allSatisfy { id in
            list.firstIndex(where: { $0.id == id }).map { $0 > 40 } ?? false
        })
    }

    func testNoCardIsEverRequestedTwice() throws {
        let list = try entries(cards: 40)
        let recorder = Recorder()
        let prefetcher = FeedCardPrefetcher(sink: recorder.sink)
        for index in 0 ..< list.count {
            prefetcher.entryAppeared(at: index, in: list)
        }
        // Scrolling back over the same rows asks for nothing more.
        let before = recorder.batches.count
        for index in stride(from: list.count - 1, through: 0, by: -1) {
            prefetcher.entryAppeared(at: index, in: list)
        }
        XCTAssertEqual(recorder.batches.count, before)

        let posterIDs = prefetchedCardIDs(recorder)
        // Each card appears twice in the requests (poster and art), never more.
        let counts = Dictionary(grouping: posterIDs, by: { $0 }).mapValues(\.count)
        XCTAssertTrue(counts.values.allSatisfy { $0 == 2 }, "\(counts)")
        XCTAssertEqual(counts.count, 39, "Every card after the first was asked for; the first loads itself")
    }

    func testTheWindowStopsAtTheEndOfTheList() throws {
        let list = try entries(cards: 5)
        let prefetcher = FeedCardPrefetcher(sink: { _ in })
        XCTAssertEqual(ids(prefetcher.cardsToPrefetch(appearing: list.count - 1, in: list)), [])
        XCTAssertEqual(ids(prefetcher.cardsToPrefetch(appearing: -1, in: list)), [])
        XCTAssertEqual(ids(prefetcher.cardsToPrefetch(appearing: 0, in: list)), [2, 3, 4, 5])
    }

    func testACardWhoseArtChangedIsAskedForAgain() throws {
        let row = try FeedTestItems.planningRow(id: -200, count: 2)
        let bare = FeedCardPresenter.model(for: row, isNewestOfTitle: true)
        let day = try FeedTestItems.planningDay(titles: [("A", "https://p/a.jpg"), ("B", "https://p/b.jpg")], count: 2)
        let fanned = FeedCardPresenter.model(for: row, isNewestOfTitle: true, planning: day)

        let recorder = Recorder()
        let prefetcher = FeedCardPrefetcher(sink: recorder.sink)
        let lead = try card(id: 1)
        prefetcher.entryAppeared(at: 0, in: [lead, bare])
        let first = recorder.batches.count
        XCTAssertEqual(first, 1)
        prefetcher.entryAppeared(at: 0, in: [lead, bare])
        XCTAssertEqual(recorder.batches.count, first, "Unchanged art is not asked for again")
        prefetcher.entryAppeared(at: 0, in: [lead, fanned])
        XCTAssertEqual(recorder.batches.count, first + 1, "The fan's posters are new requests")
    }

    // MARK: Source

    private static var feedFolder: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home/Feed")
    }

    func testTheFeedFolderUsesNoBannedViewApi() throws {
        let folder = Self.feedFolder
        let files = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "swift" }
        XCTAssertGreaterThanOrEqual(files.count, 10)
        let banned = ["Geometry" + "Reader", ".blur" + "(", "Mate" + "rial", "View" + "ThatFits"]
        for file in files {
            let text = try String(contentsOf: file, encoding: .utf8)
            for word in banned {
                XCTAssertFalse(text.contains(word), "\(file.lastPathComponent) uses \(word)")
            }
            // SpineAsyncImage is the allowed way in; the bare system view is not.
            XCTAssertFalse(
                text.replacingOccurrences(of: "SpineAsync" + "Image", with: "").contains("Async" + "Image"),
                "\(file.lastPathComponent) uses the system async image"
            )
        }
    }

    func testThePosterHasNoShadowAndOnlyTheBackgroundIsClipped() throws {
        let folder = Self.feedFolder
        let view = try String(contentsOf: folder.appendingPathComponent("FeedCardView.swift"), encoding: .utf8)
        let art = try String(contentsOf: folder.appendingPathComponent("FeedCardArt.swift"), encoding: .utf8)
        XCTAssertFalse(view.contains(".shadow" + "("))
        XCTAssertFalse(art.contains(".shadow" + "("))
        // The card view clips nothing itself; its background view clips to the card's rounded shape, once.
        XCTAssertFalse(view.contains(".clipped" + "()"))
        XCTAssertFalse(view.contains(".clipShape" + "("))
        let background = try XCTUnwrap(art.range(of: "struct FeedCardBackground"))
        let edge = try XCTUnwrap(art.range(of: "struct FeedCardEdge"))
        let backgroundSource = String(art[background.lowerBound ..< edge.lowerBound])
        XCTAssertEqual(backgroundSource.components(separatedBy: ".clipShape" + "(").count - 1, 1)
        XCTAssertFalse(backgroundSource.contains(".clipped" + "()"))
    }

    func testTheViewTakesItsRequestsFromFeedCardArt() throws {
        let folder = Self.feedFolder
        let art = try String(contentsOf: folder.appendingPathComponent("FeedCardArt.swift"), encoding: .utf8)
        let link = try String(contentsOf: folder.appendingPathComponent("FeedAuthorLink.swift"), encoding: .utf8)
        // The single poster, the fan, the background and the avatar all build their request from FeedCardArt's pieces.
        XCTAssertTrue(art.contains("slot: .feedCard"))
        XCTAssertTrue(art.contains("orientation: FeedCardArt.orientation(isSquare: art.isSquare)"))
        XCTAssertTrue(art.contains("thumbnailSize: FeedCardArt.posterSize(isSquare: art.isSquare)"))
        XCTAssertTrue(art.contains("style: .card"))
        XCTAssertTrue(link.contains("thumbnailSize: FeedCardArt.avatarSize"))
    }

    // MARK: Avatar and author line

    func testTheInitialDiscIsStablePerName() {
        XCTAssertEqual(FeedAvatar.initial(of: "perf-ava"), "P")
        XCTAssertEqual(FeedAvatar.initial(of: "_x"), "X")
        XCTAssertEqual(FeedAvatar.initial(of: ""), "?")
        XCTAssertEqual(FeedAvatar.hue(for: "maya"), FeedAvatar.hue(for: "maya"))
        XCTAssertNotEqual(FeedAvatar.hue(for: "maya"), FeedAvatar.hue(for: "theo"))
        XCTAssertTrue((0 ... 1).contains(FeedAvatar.hue(for: "maximiliano.rodriguez")))
    }

    func testTheDayNeverTruncatesAndTheAuthorLineKeepsItsHeight() throws {
        let link = try String(contentsOf: Self.feedFolder.appendingPathComponent("FeedAuthorLink.swift"), encoding: .utf8)
        XCTAssertTrue(link.contains(".fixedSize(horizontal: true, vertical: false)"), "The day keeps its width")
        XCTAssertEqual(FeedAuthorLink.height, 22)
    }
}
