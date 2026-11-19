import Foundation
@testable import Spine
import XCTest

// Search's result rows and its opening page of lists.

@MainActor
final class SearchRowPresentationTests: XCTestCase {
    func testTheYearIsSaidOnceEvenWhenTheResultsOwnLineHasIt() {
        let line = SearchRowText.meta(
            mediaType: "anime", subtitle: "TV · 2023 · 28 episodes", releaseDate: "2023-09-29", showsKind: true
        )
        XCTAssertEqual(line?.components(separatedBy: "2023").count, 2)

        let movie = SearchRowText.meta(mediaType: "movie", subtitle: "2024", releaseDate: "2024-03-01", showsKind: false)
        XCTAssertEqual(movie, "2024")
    }

    func testTheKindLeadsOnlyWhenTheSearchCoversEveryKind() throws {
        let kind = try XCTUnwrap(TrendingTileModel.kind(of: "music"))
        let every = SearchRowText.meta(mediaType: "music", subtitle: "Hans Zimmer", releaseDate: "2021-10-22", showsKind: true)
        let one = SearchRowText.meta(mediaType: "music", subtitle: "Hans Zimmer", releaseDate: "2021-10-22", showsKind: false)

        XCTAssertEqual(every?.hasPrefix(kind), true)
        XCTAssertEqual(one?.contains(kind), false)

        // An album's own line already says "Album": it's said once, up front.
        let album = SearchRowText.meta(mediaType: "music", subtitle: "Toto · 1984 · Album", releaseDate: "1984-12-01", showsKind: true)
        XCTAssertEqual(album?.components(separatedBy: kind).count, 2)
        XCTAssertEqual(album?.hasPrefix(kind), true)
        XCTAssertNil(SearchRowText.meta(mediaType: "book", subtitle: " ", releaseDate: nil, showsKind: false))
    }

    func testTheTypedTextIsFoundWhateverItsCaseOrAccents() throws {
        let found = try XCTUnwrap(SearchRowText.match(in: "Pokémon: Dune Edition", query: "  POKEMON "))

        XCTAssertEqual(found.before + found.match + found.after, "Pokémon: Dune Edition")
        XCTAssertEqual(found.match, "Pokémon")
        XCTAssertNil(SearchRowText.match(in: "Dune", query: "frieren"))
        XCTAssertNil(SearchRowText.match(in: "Dune", query: " "))
    }

    func testYourStarsComeBeforeWhereYouAreAndAHeartAloneStillShows() {
        let rated = UserMediaState(isTracked: true, status: "In progress", rating: "4.5", hasLiked: true)
        XCTAssertEqual(SearchRowText.standing(for: rated, mediaType: "movie"), .rated(stars: "4.5", liked: true))

        let reading = UserMediaState(isTracked: true, status: "In progress")
        guard case .status = SearchRowText.standing(for: reading, mediaType: "book") else {
            return XCTFail("An unrated title in progress says where you are")
        }

        let liked = UserMediaState(isTracked: false, hasLiked: true)
        XCTAssertEqual(SearchRowText.standing(for: liked, mediaType: "movie"), .liked)
        XCTAssertNil(SearchRowText.standing(for: UserMediaState(isTracked: false), mediaType: "movie"))
        XCTAssertNil(SearchRowText.standing(for: nil, mediaType: "movie"))
    }

    func testInProgressReadsByKind() {
        let words = ["movie", "book", "game", "music"].compactMap { SearchRowText.statusWord("In progress", mediaType: $0) }
        XCTAssertEqual(Set(words).count, 4)
        XCTAssertNil(SearchRowText.statusWord("Something new", mediaType: "movie"))
    }

    func testFriendsLineNamesEveryoneWhoFitsAndCountsTheRest() throws {
        let three = MediaFriendsSummary(count: 3, people: ["maya", "theo", "jules"].map(face))
        let all = try XCTUnwrap(SearchRowText.friendsLine(three))
        XCTAssertTrue(["maya", "theo", "jules"].allSatisfy(all.contains))
        XCTAssertFalse(all.contains(where: \.isNumber))

        let six = MediaFriendsSummary(count: 6, people: ["maya", "theo", "jules"].map(face))
        let some = try XCTUnwrap(SearchRowText.friendsLine(six))
        XCTAssertTrue(some.contains("maya") && some.contains("theo"))
        XCTAssertTrue(some.contains("4"))

        XCTAssertNil(SearchRowText.friendsLine(MediaFriendsSummary(count: 0, people: [])))
    }

    func testTheLineAboveTheKindsAddsUpTheRowsAndCountsEachSourceOnce() {
        let movies = FeaturedShelf(
            key: "movie", title: "Movies", mediaType: "movie", count: 39, lists: [],
            sources: [FeaturedShelfSource(name: "Oscars"), FeaturedShelfSource(name: "Netflix", lists: 3)]
        )
        let tv = FeaturedShelf(
            key: "tv", title: "TV", mediaType: "tv", count: 18, lists: [],
            sources: [FeaturedShelfSource(name: "Emmys"), FeaturedShelfSource(name: "Netflix", lists: 2)]
        )
        let totals = SearchListsSummary.totals(for: [movies, tv])
        XCTAssertEqual(totals.lists, 57)
        XCTAssertEqual(totals.sources, 3)

        let older = FeaturedShelf(key: "book", title: "Books", mediaType: "book", count: 59, lists: [])
        XCTAssertEqual(SearchListsSummary.totals(for: [older]).sources, 0)
    }

    func testShelvesDecodeTheirSourcesAndDropABrokenOne() throws {
        let json = #"""
        {"shelves": [
          {"key": "movie", "title": "Movies", "media_type": "movie", "count": 39, "lists": [],
           "sources": [{"name": "Oscars", "icon_url": "https://example.com/oscars.png", "lists": 2},
                       {"icon_url": "https://example.com/nameless.png", "lists": 1},
                       {"name": "A Zine", "icon_url": null, "lists": 1}]},
          {"key": "tv", "title": "TV", "media_type": "tv", "count": 18, "lists": []}
        ]}
        """#
        let response = try JSONDecoder.api.decode(FeaturedShelvesResponse.self, from: Data(json.utf8))

        XCTAssertEqual(response.shelves[0].sources.map(\.name), ["Oscars", "A Zine"])
        XCTAssertEqual(response.shelves[0].sources[0].lists, 2)
        XCTAssertEqual(response.shelves[0].sources[0].iconUrl, "https://example.com/oscars.png")
        XCTAssertNil(response.shelves[0].sources[1].iconUrl)
        XCTAssertEqual(response.shelves[1].sources, [])
    }

    func testSearchResultsDecodeTheirFriendsAndSurviveABrokenOne() throws {
        let json = #"""
        [
          {"ref": {"item_id": 7, "source": "tmdb", "media_type": "movie", "media_id": "693134"}, "title": "Dune: Part Two",
           "friends": {"count": 5, "people": [{"id": 2, "username": "maya", "display_name": "Maya", "avatar_url": null}]}},
          {"ref": {"item_id": 8, "source": "tmdb", "media_type": "movie", "media_id": "438631"}, "title": "Dune",
           "friends": {"count": "lots"}},
          {"ref": {"item_id": null, "source": "tmdb", "media_type": "movie", "media_id": "841"}, "title": "Dune",
           "friends": null}
        ]
        """#
        let results = try JSONDecoder.api.decode([MediaSummary].self, from: Data(json.utf8))

        XCTAssertEqual(results.count, 3)
        XCTAssertEqual(results[0].friends?.count, 5)
        XCTAssertEqual(results[0].friends?.people.first?.username, "maya")
        XCTAssertNil(results[1].friends)
        XCTAssertNil(results[2].friends)
    }

    private func face(_ username: String) -> MediaFriendFace {
        MediaFriendFace(id: username.count, username: username, displayName: username, avatarUrl: nil)
    }
}
