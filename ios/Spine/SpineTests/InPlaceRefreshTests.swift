import XCTest
@testable import Spine

/// A title marked watched from a list, the Library or the diary posts a state change, and the screen behind
/// refreshes. It has to keep every row it already shows: reloading only the first page left a list scrolled
/// past it (IMDb Top 250, page 5) blank until it was closed and reopened.
@MainActor
final class InPlaceRefreshTests: XCTestCase {
    // MARK: List detail (custom and featured lists)

    func testListRefreshKeepsEveryLoadedPageAndUpdatesTheMarkedTitle() async throws {
        let repository = PagedListRepository(pages: try Self.listPages(count: 6, pageSize: 2))
        let viewModel = ProfileListDetailViewModel(
            listId: 7,
            listRepository: repository,
            filterOptionsRepository: EmptyFilterOptionsRepository(),
            onUnauthorized: {}
        )
        await viewModel.load()
        await viewModel.loadFilteredItems(reset: false)
        await viewModel.loadFilteredItems(reset: false)
        XCTAssertEqual(viewModel.displayedItems.map(\.title), ["1", "2", "3", "4", "5", "6"])

        // The show on page 3 is marked watched from its page.
        repository.markWatched(title: "5")
        repository.requestedPages = []
        var shownDuringRefresh: [Int] = []
        repository.onItemsRequest = { shownDuringRefresh.append(viewModel.displayedItems.count) }

        await viewModel.refreshLoadedItems()

        XCTAssertEqual(viewModel.displayedItems.map(\.title), ["1", "2", "3", "4", "5", "6"])
        XCTAssertEqual(viewModel.displayedItems[4].userState?.status, "Completed")
        XCTAssertEqual(viewModel.list?.items.count, 6)
        XCTAssertEqual(repository.requestedPages, [nil, "2", "3"])
        XCTAssertEqual(shownDuringRefresh, [6, 6, 6], "The grid never shrinks while the refresh runs")
        XCTAssertFalse(viewModel.isLoading)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testListRefreshKeepsTheNextPageToLoad() async throws {
        let repository = PagedListRepository(pages: try Self.listPages(count: 6, pageSize: 2))
        let viewModel = ProfileListDetailViewModel(
            listId: 7,
            listRepository: repository,
            filterOptionsRepository: EmptyFilterOptionsRepository(),
            onUnauthorized: {}
        )
        await viewModel.load()
        await viewModel.loadFilteredItems(reset: false)

        await viewModel.refreshLoadedItems()
        XCTAssertEqual(viewModel.displayedItems.map(\.title), ["1", "2", "3", "4"])

        // Scrolling on still loads page 3, not page 2 again.
        repository.requestedPages = []
        await viewModel.loadNextFilteredPageIfNeeded(currentIndex: 3)
        XCTAssertEqual(repository.requestedPages, ["3"])
        XCTAssertEqual(viewModel.displayedItems.map(\.title), ["1", "2", "3", "4", "5", "6"])
    }

    func testListRefreshFailureKeepsTheRowsShown() async throws {
        let repository = PagedListRepository(pages: try Self.listPages(count: 4, pageSize: 2))
        let viewModel = ProfileListDetailViewModel(
            listId: 7,
            listRepository: repository,
            filterOptionsRepository: EmptyFilterOptionsRepository(),
            onUnauthorized: {}
        )
        await viewModel.load()
        await viewModel.loadFilteredItems(reset: false)

        repository.itemsError = URLError(.notConnectedToInternet)
        await viewModel.refreshLoadedItems()

        XCTAssertEqual(viewModel.displayedItems.map(\.title), ["1", "2", "3", "4"])
        XCTAssertNil(viewModel.errorMessage)
    }

    // MARK: Library

    func testLibraryRefreshKeepsEveryLoadedPage() async {
        let tracking = PagedTrackingRepository(pages: [
            [Self.libraryItem("1"), Self.libraryItem("2")],
            [Self.libraryItem("3"), Self.libraryItem("4")],
            [Self.libraryItem("5")],
        ])
        let viewModel = LibraryViewModel(
            mediaRepository: SurfacesMediaRepository(),
            trackingRepository: tracking,
            filterOptionsRepository: EmptyFilterOptionsRepository(),
            onUnauthorized: {}
        )
        await viewModel.reload()
        await viewModel.loadNextPage()
        XCTAssertEqual(viewModel.displayedItems.map(\.media.title), ["1", "2", "3", "4"])
        let revision = viewModel.contentRevision

        tracking.requestedPages = []
        var shownDuringRefresh: [Int] = []
        tracking.onListRequest = { shownDuringRefresh.append(viewModel.displayedItems.count) }
        await viewModel.refreshLoadedItems()

        XCTAssertEqual(viewModel.displayedItems.map(\.media.title), ["1", "2", "3", "4"])
        XCTAssertEqual(tracking.requestedPages, [nil, "2"])
        XCTAssertEqual(shownDuringRefresh, [4, 4])
        XCTAssertEqual(viewModel.contentRevision, revision, "The grid keeps its identity, so it keeps its scroll position")
        XCTAssertTrue(viewModel.hasMorePages)
        XCTAssertFalse(viewModel.isLoadingInitial)
    }

    // MARK: Diary

    func testDiaryRefreshKeepsEveryLoadedPage() async throws {
        let diary = PagedDiaryRepository(pages: [
            [try Self.diaryEntry(1), try Self.diaryEntry(2)],
            [try Self.diaryEntry(3), try Self.diaryEntry(4)],
            [try Self.diaryEntry(5)],
        ])
        let viewModel = DiaryViewModel(diaryRepository: diary, onUnauthorized: {})
        await viewModel.load()
        await viewModel.loadNextPage()
        XCTAssertEqual(viewModel.entries.map(\.id), [1, 2, 3, 4])

        diary.requestedPages = []
        var shownDuringRefresh: [Int] = []
        diary.onPageRequest = { shownDuringRefresh.append(viewModel.entries.count) }
        await viewModel.refreshLoadedEntries()

        XCTAssertEqual(viewModel.entries.map(\.id), [1, 2, 3, 4])
        XCTAssertEqual(diary.requestedPages, [nil, "2"])
        XCTAssertEqual(shownDuringRefresh, [4, 4])
        XCTAssertTrue(viewModel.hasMorePages)
        XCTAssertFalse(viewModel.isLoading)
    }

    // MARK: Fixtures

    static func ref(_ id: Int) throws -> MediaRef {
        let json = #"{"item_id": \#(id), "source": "tmdb", "media_type": "tv", "media_id": "\#(id)"}"#
        return try JSONDecoder.api.decode(MediaRef.self, from: Data(json.utf8))
    }

    static func listPages(count: Int, pageSize: Int) throws -> [[MediaSummary]] {
        let items = try (1...count).map { MediaSummary(ref: try ref($0), title: "\($0)", position: $0) }
        return stride(from: 0, to: items.count, by: pageSize).map { Array(items[$0..<min($0 + pageSize, items.count)]) }
    }

    static func libraryItem(_ id: String) -> Spine.LibraryItem {
        TVTrackingSurfacesTests.item(id, id, mediaType: "movie", updatedAt: nil)
    }

    static func diaryEntry(_ id: Int) throws -> DiaryEntry {
        let json = """
        {
          "id": \(id),
          "user": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
          "media": {
            "ref": { "item_id": \(100 + id), "source": "tmdb", "media_type": "movie", "media_id": "\(id)", "season_number": null, "episode_number": null },
            "title": "Entry \(id)",
            "image_url": null, "poster_url": null, "custom_poster_url": null
          },
          "consumed_at": "2026-06-\(10 + id)T12:00:00Z",
          "rating": null, "review_title": "", "review": "", "contains_spoilers": false, "liked": false,
          "is_rewatch": false, "tags": [], "visibility": "public", "like_count": 0, "viewer_has_liked": false,
          "created_at": "2026-06-20T12:00:00Z", "updated_at": "2026-06-20T12:00:00Z"
        }
        """
        return try JSONDecoder.api.decode(DiaryEntry.self, from: Data(json.utf8))
    }
}

/// Answers `?page=N` cursors like the API: page N of `pages`, with a next link while more remain.
private func pagedResponse<T>(_ pages: [[T]], cursor: String?) -> PagedResponse<T> {
    let index = (cursor.flatMap(Int.init) ?? 1) - 1
    let results = pages.indices.contains(index) ? pages[index] : []
    let next = index + 1 < pages.count ? "https://example.com/api/?page=\(index + 2)" : nil
    return PagedResponse(count: pages.reduce(0) { $0 + $1.count }, next: next, previous: nil, results: results)
}

@MainActor
private final class PagedListRepository: ListRepository {
    var pages: [[MediaSummary]]
    var requestedPages: [String?] = []
    var onItemsRequest: (() -> Void)?
    var itemsError: Error?

    init(pages: [[MediaSummary]]) {
        self.pages = pages
    }

    func markWatched(title: String) {
        pages = pages.map { page in
            page.map { item in
                guard item.title == title else { return item }
                var marked = item
                marked.userState = UserMediaState(isTracked: true, trackingId: 1, status: "Completed")
                return marked
            }
        }
    }

    func detail(id: Int) async throws -> CustomListDetail {
        let json = """
        {"id": \(id), "name": "IMDb Top 250", "slug": "imdb-top-250", "description": "", "visibility": "public",
         "is_ranked": true, "list_type": "media",
         "owner": {"id": 1, "username": "Spine", "display_name": "Spine", "avatar_url": null},
         "items_count": \(pages.reduce(0) { $0 + $1.count }), "like_count": 0, "items": []}
        """
        return try JSONDecoder.api.decode(CustomListDetail.self, from: Data(json.utf8))
    }

    func items(listId: Int, page: String?, filter: MediaFilterState) async throws -> PagedResponse<MediaSummary> {
        requestedPages.append(page)
        onItemsRequest?()
        if let itemsError { throw itemsError }
        return pagedResponse(pages, cursor: page)
    }

    func list(membershipFor ref: MediaRef?) async throws -> [CustomListSummary] { [] }
    func peopleLists(membershipFor ref: PersonRef) async throws -> [CustomListSummary] { [] }
    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary { fatalError("unused") }
    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail { fatalError("unused") }
    func delete(id: Int) async throws {}
    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary { fatalError("unused") }
    func removeItem(listId: Int, itemId: Int) async throws {}
    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail { fatalError("unused") }
    func people(listId: Int, page: String?) async throws -> PagedResponse<PersonListEntry> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }
    func addPerson(listId: Int, ref: PersonRef) async throws -> PersonListEntry { fatalError("unused") }
    func removePerson(listId: Int, entryId: Int) async throws {}
    func reorderPeople(listId: Int, entryIds: [Int]) async throws -> CustomListDetail { fatalError("unused") }
}

@MainActor
private final class EmptyFilterOptionsRepository: FilterOptionsRepository {
    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse {
        .empty
    }
}

private final class PagedTrackingRepository: TrackingRepository {
    let pages: [[Spine.LibraryItem]]
    var requestedPages: [String?] = []
    var onListRequest: (() -> Void)?

    init(pages: [[Spine.LibraryItem]]) {
        self.pages = pages
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        requestedPages.append(page)
        onListRequest?()
        return pagedResponse(pages, cursor: page)
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

private final class PagedDiaryRepository: DiaryRepository {
    let pages: [[DiaryEntry]]
    var requestedPages: [String?] = []
    var onPageRequest: (() -> Void)?

    init(pages: [[DiaryEntry]]) {
        self.pages = pages
    }

    func page(filter: MediaFilterState, page: String?) async throws -> PagedResponse<DiaryEntry> {
        requestedPages.append(page)
        onPageRequest?()
        return pagedResponse(pages, cursor: page)
    }

    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}
