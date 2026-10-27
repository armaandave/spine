import XCTest
@testable import Spine

@MainActor
final class ListComposerTagsTests: XCTestCase {
    private let owner = UserSummary(id: 1, username: "owner", displayName: "Owner", avatarUrl: nil)
    private let collaborator = UserSummary(id: 2, username: "friend", displayName: "Friend", avatarUrl: nil)

    // MARK: - Normalizing (STAT-402)

    func testNormalizingTrimsCollapsesDropsEmptiesAndKeepsTheFirstSpelling() {
        XCTAssertEqual(
            ListTagRules.normalized(["top2025", "  Favorites ", "favorites", "", "   ", "Top   all\ttime", "top all time"]),
            ["top2025", "Favorites", "Top all time"]
        )
    }

    func testTheContractsExampleSavesTwoTags() {
        XCTAssertEqual(ListTagRules.normalized(["top2025", "  Favorites ", "favorites"]), ["top2025", "Favorites"])
    }

    func testListTagsStopAtTwentyOfAHundredCharacters() {
        let draft = LogTagDraft(maximumCount: ListTagRules.maximumCount, maximumLength: ListTagRules.maximumLength)
        for index in 1 ... 25 {
            draft.add("tag\(index)")
        }
        XCTAssertEqual(draft.tags.count, 20, "The server refuses a 21st tag")
        XCTAssertTrue(draft.isFull)

        draft.remove("tag1")
        XCTAssertFalse(draft.isFull)
        draft.add(String(repeating: "a", count: 140))
        XCTAssertEqual(draft.tags.last?.count, 100, "A long tag is cut to 100 characters")

        // The server counts code points: a family emoji is 5 of them, so 30 are cut to 20.
        draft.remove(String(repeating: "a", count: 100))
        draft.add(String(repeating: "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}", count: 30))
        XCTAssertEqual(draft.tags.last?.unicodeScalars.count, 100)
        XCTAssertEqual(draft.tags.last?.count, 20)

        let diaryDraft = LogTagDraft()
        for index in 1 ... 25 {
            diaryDraft.add("tag\(index)")
        }
        XCTAssertEqual(diaryDraft.tags.count, 25, "Log tags keep no limit")
        XCTAssertFalse(diaryDraft.isFull)
    }

    // MARK: - Suggestions

    func testTopTagsAreThisYearLastYearAndAllTime() {
        XCTAssertEqual(ListTagRules.topTags(year: 2026), ["top2026", "top2025", "topalltime"])
    }

    func testRankedMediaListSuggestsTopTagsFirst() async {
        let repository = TagsListRepository(lists: [
            summary(id: 1, tags: ["comfort", "Top2025"], owner: owner),
            summary(id: 2, tags: ["comfort"], owner: owner),
        ])
        let viewModel = makeViewModel(mode: .create(.media), repository: repository)
        viewModel.draft.isRanked = true

        await viewModel.loadTagLibrary()

        XCTAssertTrue(viewModel.suggestsTopTags)
        XCTAssertEqual(
            viewModel.tagDraft.suggestions.map(\.name),
            ["top2026", "top2025", "topalltime", "comfort"]
        )
        XCTAssertEqual(viewModel.tagDraft.suggestions.first { $0.name == "top2025" }?.usageCount, 1)
        XCTAssertEqual(viewModel.tagDraft.suggestions.first { $0.name == "comfort" }?.usageCount, 2)
    }

    func testTopTagMatchesTheServersSpellings() {
        XCTAssertEqual(ListTagRules.topTag(for: "TOP all time"), "topalltime")
        XCTAssertEqual(ListTagRules.topTag(for: " Top 2025 "), "top2025")
        XCTAssertEqual(ListTagRules.topTag(for: "top_2025"), "top2025")
        XCTAssertEqual(ListTagRules.topTag(for: "top-all-time"), "topalltime")
        XCTAssertNil(ListTagRules.topTag(for: "top movies"))
        XCTAssertNil(ListTagRules.topTag(for: "top20255"))
    }

    func testTopTagsInOtherSpellingsArentSuggestedTwice() async {
        // "Top 2025" on another list is top2025: one suggestion, with its count.
        let repository = TagsListRepository(lists: [
            summary(id: 1, tags: ["Top 2025", "comfort"], owner: owner),
            summary(id: 2, tags: ["top2025"], owner: owner),
        ])
        let viewModel = makeViewModel(mode: .create(.media), repository: repository)
        viewModel.draft.isRanked = true
        await viewModel.loadTagLibrary()

        XCTAssertEqual(viewModel.tagSuggestions.map(\.name), ["top2026", "top2025", "topalltime", "comfort"])
        XCTAssertEqual(viewModel.tagSuggestions.first { $0.name == "top2025" }?.usageCount, 2)
        XCTAssertTrue(viewModel.showsTopTagHint)

        // The list already has "TOP all time": topalltime isn't offered and the hint goes.
        viewModel.tagDraft.pick("TOP all time")
        XCTAssertEqual(viewModel.tagSuggestions.map(\.name), ["top2026", "top2025", "comfort"])
        XCTAssertFalse(viewModel.showsTopTagHint)
    }

    func testUnrankedListWithoutPositionsSuggestsOnlyItsOwnTags() async {
        let repository = TagsListRepository(lists: [summary(id: 1, tags: ["comfort"], owner: owner)])
        let viewModel = makeViewModel(mode: .create(.media), repository: repository)

        await viewModel.loadTagLibrary()

        XCTAssertFalse(viewModel.suggestsTopTags)
        XCTAssertEqual(viewModel.tagDraft.suggestions.map(\.name), ["comfort"])
    }

    func testAListWithPositionsCountsAsRanked() {
        let list = detail(owner: owner, isRanked: false, items: [
            MediaSummary(
                ref: MediaRef(itemId: 5, source: "tmdb", mediaType: "movie", mediaId: "5", seasonNumber: nil, episodeNumber: nil),
                title: "Positioned",
                posterOrientation: .portrait,
                position: 1
            ),
        ])
        let viewModel = makeViewModel(mode: .edit(list), repository: TagsListRepository())

        XCTAssertTrue(viewModel.suggestsTopTags)
    }

    func testTopTagsFollowTheRankedSwitch() async {
        let viewModel = makeViewModel(mode: .create(.media), repository: TagsListRepository())
        await viewModel.loadTagLibrary()
        XCTAssertTrue(viewModel.tagDraft.suggestions.isEmpty)

        viewModel.draft.isRanked = true
        viewModel.refreshTagSuggestions()
        XCTAssertEqual(viewModel.tagDraft.suggestions.map(\.name), ["top2026", "top2025", "topalltime"])
    }

    func testPeopleListsNeverSuggestTopTags() {
        let viewModel = makeViewModel(mode: .create(.people), repository: TagsListRepository())
        viewModel.draft.isRanked = true

        XCTAssertFalse(viewModel.suggestsTopTags)
    }

    func testOtherOwnersListTagsArentSuggested() {
        let counted = ListTagRules.counted(
            [summary(id: 1, tags: ["mine"], owner: owner), summary(id: 2, tags: ["theirs"], owner: collaborator)],
            ownerId: owner.id
        )
        XCTAssertEqual(counted.map(\.name), ["mine"])
    }

    // MARK: - Owner only

    func testOnlyTheOwnerEditsTags() {
        let list = detail(owner: owner, collaborators: [collaborator])

        XCTAssertTrue(ListTagRules.canEditTags(mode: .create(.media), currentUserId: nil))
        XCTAssertTrue(ListTagRules.canEditTags(mode: .edit(list), currentUserId: owner.id))
        XCTAssertFalse(ListTagRules.canEditTags(mode: .edit(list), currentUserId: collaborator.id))
        XCTAssertFalse(ListTagRules.canEditTags(mode: .edit(list), currentUserId: nil))
        XCTAssertTrue(ListTagRules.canEditTags(mode: .edit(detail(owner: owner)), currentUserId: nil))
    }

    func testCollaboratorsSaveWithoutSendingTags() async throws {
        let repository = TagsListRepository()
        let list = detail(owner: owner, tags: ["top2025"], collaborators: [collaborator], items: [movie(5)])
        repository.serverItems = list.items
        let viewModel = makeViewModel(mode: .edit(list), repository: repository, currentUserId: collaborator.id)
        viewModel.draft.name = "Renamed"

        _ = await viewModel.save()

        let request = try XCTUnwrap(repository.updates.first)
        XCTAssertNil(request.tags)
        let body = try XCTUnwrap(String(data: JSONEncoder.api.encode(request), encoding: .utf8))
        XCTAssertFalse(body.contains("tags"), "nil tags are left out of the body")
    }

    // MARK: - Saving

    func testOwnerSavesNormalizedTagsIncludingWhatsStillTyped() async throws {
        let repository = TagsListRepository()
        let list = detail(owner: owner, tags: ["comfort"], items: [movie(5)])
        repository.serverItems = list.items
        let viewModel = makeViewModel(mode: .edit(list), repository: repository, currentUserId: owner.id)

        viewModel.tagDraft.add("  Top  2025 ")
        viewModel.tagDraft.query = "favorites"
        XCTAssertTrue(viewModel.isDirty)

        _ = await viewModel.save()

        let request = try XCTUnwrap(repository.updates.first)
        XCTAssertEqual(request.tags, ["comfort", "Top 2025", "favorites"])
    }

    func testOwnerRenameLeavesUnchangedTagsOut() async throws {
        // An imported list can hold more tags than a write accepts; a rename must still save.
        let repository = TagsListRepository()
        let imported = (1...25).map { "tag \($0)" }
        let list = detail(owner: owner, tags: imported, items: [movie(5)])
        repository.serverItems = list.items
        let viewModel = makeViewModel(mode: .edit(list), repository: repository, currentUserId: owner.id)
        viewModel.draft.name = "Renamed"

        _ = await viewModel.save()

        let request = try XCTUnwrap(repository.updates.first)
        XCTAssertNil(request.tags)
        XCTAssertEqual(request.name, "Renamed")
    }

    func testRemovingTheLastTagSendsAnEmptyListToClearIt() async throws {
        let repository = TagsListRepository()
        let list = detail(owner: owner, tags: ["comfort"], items: [movie(5)])
        repository.serverItems = list.items
        let viewModel = makeViewModel(mode: .edit(list), repository: repository, currentUserId: owner.id)

        viewModel.tagDraft.remove("comfort")
        XCTAssertTrue(viewModel.isDirty)
        _ = await viewModel.save()

        XCTAssertEqual(try XCTUnwrap(repository.updates.first).tags, [])
    }

    func testUntouchedTagsArentAChange() {
        let list = detail(owner: owner, tags: ["comfort"], items: [movie(5)])
        let viewModel = makeViewModel(mode: .edit(list), repository: TagsListRepository(), currentUserId: owner.id)

        XCTAssertFalse(viewModel.isDirty)
    }

    func testNewListSendsItsTags() async throws {
        let repository = TagsListRepository()
        let viewModel = makeViewModel(mode: .create(.media), repository: repository)
        viewModel.draft.name = "My 2026"
        viewModel.draft.items = [movie(5)]
        viewModel.tagDraft.add("top2026")

        _ = await viewModel.save()

        XCTAssertEqual(try XCTUnwrap(repository.creates.first).tags, ["top2026"])
        let body = try XCTUnwrap(String(data: JSONEncoder.api.encode(repository.creates[0]), encoding: .utf8))
        XCTAssertTrue(body.contains("\"tags\":[\"top2026\"]"))
    }

    // MARK: - Helpers

    private func makeViewModel(
        mode: ListComposerMode,
        repository: TagsListRepository,
        currentUserId: Int? = nil
    ) -> ListComposerViewModel {
        ListComposerViewModel(
            mode: mode,
            listRepository: repository,
            currentUserId: currentUserId,
            now: { Self.date(year: 2026) },
            onUnauthorized: {}
        )
    }

    private static func date(year: Int) -> Date {
        Calendar.current.date(from: DateComponents(year: year, month: 6, day: 15))!
    }

    private func movie(_ id: Int) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(itemId: id, source: "tmdb", mediaType: "movie", mediaId: "\(id)", seasonNumber: nil, episodeNumber: nil),
            title: "Movie \(id)",
            posterOrientation: .portrait
        )
    }

    private func summary(id: Int, tags: [String], owner: UserSummary) -> CustomListSummary {
        CustomListSummary(
            id: id, name: "List \(id)", slug: "list-\(id)", description: "", tags: tags,
            visibility: "public", owner: owner, itemsCount: 1, likeCount: 0
        )
    }

    private func detail(
        owner: UserSummary,
        tags: [String] = [],
        isRanked: Bool = false,
        collaborators: [UserSummary] = [],
        items: [MediaSummary] = []
    ) -> CustomListDetail {
        CustomListDetail(
            id: 77, name: "Favorites", slug: "favorites", description: "", tags: tags,
            visibility: "private", isRanked: isRanked, owner: owner, itemsCount: items.count,
            likeCount: 0, items: items, collaborators: collaborators
        )
    }
}

@MainActor
private final class TagsListRepository: ListRepository {
    var lists: [CustomListSummary]
    var serverItems: [MediaSummary] = []
    private(set) var creates: [CustomListWriteRequest] = []
    private(set) var updates: [CustomListWriteRequest] = []

    init(lists: [CustomListSummary] = []) {
        self.lists = lists
    }

    func list(membershipFor ref: MediaRef?) async throws -> [CustomListSummary] { lists }
    func peopleLists(membershipFor ref: PersonRef) async throws -> [CustomListSummary] { [] }

    func detail(id: Int) async throws -> CustomListDetail {
        CustomListDetail(
            id: id, name: "Saved", slug: "saved", description: "", visibility: "private",
            owner: UserSummary(id: 1, username: "owner", displayName: "Owner", avatarUrl: nil),
            itemsCount: serverItems.count, likeCount: 0, items: serverItems
        )
    }

    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary {
        creates.append(request)
        return CustomListSummary(
            id: 77, name: request.name ?? "", slug: "saved", description: "", visibility: "private",
            owner: UserSummary(id: 1, username: "owner", displayName: "Owner", avatarUrl: nil),
            itemsCount: 0, likeCount: 0
        )
    }

    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail {
        updates.append(request)
        return try await detail(id: id)
    }

    func delete(id: Int) async throws {}

    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary {
        let saved = MediaSummary(ref: ref, title: "Saved", posterOrientation: .portrait)
        serverItems.append(saved)
        return saved
    }

    func items(listId: Int, page: String?, filter: MediaFilterState) async throws -> PagedResponse<MediaSummary> {
        PagedResponse(count: serverItems.count, next: nil, previous: nil, results: serverItems)
    }

    func removeItem(listId: Int, itemId: Int) async throws {
        serverItems.removeAll { $0.ref.itemId == itemId }
    }

    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail {
        try await detail(id: listId)
    }

    func people(listId: Int, page: String?) async throws -> PagedResponse<PersonListEntry> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func addPerson(listId: Int, ref: PersonRef) async throws -> PersonListEntry { fatalError("Not used") }
    func removePerson(listId: Int, entryId: Int) async throws {}
    func reorderPeople(listId: Int, entryIds: [Int]) async throws -> CustomListDetail { fatalError("Not used") }
}
