import XCTest
@testable import Spine

final class ListBrowseStateTests: XCTestCase {
    func testDefaultIsLastEditedNewestFirstOverEveryList() {
        let state = ListBrowseState.default

        XCTAssertEqual(state.sort, .updated)
        XCTAssertEqual(state.resolvedDirection, .desc)
        XCTAssertEqual(state.activeCount(includesVisibility: true), 0)
        XCTAssertEqual(
            state.queryItems(includesVisibility: true),
            [
                URLQueryItem(name: "list_type", value: "all"),
                URLQueryItem(name: "sort", value: "updated"),
                URLQueryItem(name: "direction", value: "desc"),
            ]
        )
    }

    func testEachSortStartsOnItsOwnOrder() {
        XCTAssertEqual(ListSort.name.defaultDirection, .asc)
        XCTAssertEqual(ListSort.name.label(for: .asc), "A–Z")
        XCTAssertEqual(ListSort.created.label(for: .desc), "Newest first")
        XCTAssertEqual(ListSort.items.label(for: .asc), "Fewest first")
        XCTAssertEqual(ListSort.likes.label(for: .desc), "Most first")
    }

    func testFiltersGoToTheServerAndCountOnTheBadge() {
        var state = ListBrowseState()
        state.sort = .name
        state.listType = .media
        state.mediaType = "movie"
        state.visibility = .private

        XCTAssertEqual(state.activeCount(includesVisibility: true), 4)
        XCTAssertEqual(state.activeCount(includesVisibility: false), 3)
        XCTAssertEqual(
            state.queryItems(includesVisibility: true),
            [
                URLQueryItem(name: "list_type", value: "media"),
                URLQueryItem(name: "sort", value: "name"),
                URLQueryItem(name: "direction", value: "asc"),
                URLQueryItem(name: "media_type", value: "movie"),
                URLQueryItem(name: "visibility", value: "private"),
            ]
        )
        // Another person's lists are all public to you, so visibility isn't sent.
        XCTAssertFalse(state.queryItems(includesVisibility: false).contains { $0.name == "visibility" })
    }

    func testPeopleListsIgnoreTheMediaType() {
        var state = ListBrowseState()
        state.listType = .people
        state.mediaType = "movie"

        XCTAssertNil(state.effectiveMediaType)
        XCTAssertFalse(state.queryItems(includesVisibility: true).contains { $0.name == "media_type" })
        XCTAssertEqual(state.activeCount(includesVisibility: true), 1)
    }

    func testATypeTurnedOffIsDropped() {
        var state = ListBrowseState()
        state.mediaType = "music"

        XCTAssertNil(state.pruned(toMediaTypes: ["movie", "tv"]).mediaType)
        XCTAssertEqual(state.pruned(toMediaTypes: ["music"]), state)
    }

    func testTheChoiceIsRememberedOnThePhone() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "ListBrowseStateTests"))
        defaults.removePersistentDomain(forName: "ListBrowseStateTests")
        XCTAssertEqual(ListBrowseStore.load(from: defaults), .default)

        var state = ListBrowseState()
        state.sort = .likes
        state.direction = .asc
        ListBrowseStore.save(state, to: defaults)

        XCTAssertEqual(ListBrowseStore.load(from: defaults), state)
        defaults.removePersistentDomain(forName: "ListBrowseStateTests")
    }
}
