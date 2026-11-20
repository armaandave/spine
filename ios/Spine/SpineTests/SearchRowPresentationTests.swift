import Foundation
@testable import Spine
import XCTest

// Search's result rows and its opening page of lists.

@MainActor
final class SearchRowPresentationTests: XCTestCase {
    func testSearchOpensOnTheKindsYouHaveOnNotEveryKind() throws {
        let meta = try JSONDecoder.api.decode(MetaResponse.self, from: Data("""
        {"version": "1", "media_types": ["movie", "tv", "book", "music"], "sources": {}, "status_choices": [],
         "source_choices": []}
        """.utf8))
        let remembered = SearchViewModel(
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            rememberedMediaTypes: ["movie", "tv", "season", "book", "music"],
            onUnauthorized: {}
        )
        let unknown = SearchViewModel(mediaRepository: MetaOnlyMediaRepository(meta: meta), onUnauthorized: {})

        XCTAssertEqual(remembered.mediaTypes, ["movie", "tv", "book", "music"])
        XCTAssertEqual(unknown.mediaTypes, APIConstants.fallbackMediaTypes)
    }

    func testWhoMadeItIsNamedInFullAndTheRestAreCounted() {
        typealias Credit = SearchRowText.Credit

        XCTAssertEqual(SearchRowText.credits(nil), [])
        XCTAssertEqual(SearchRowText.credits([" Ryan Coogler ", "Ryan Coogler", ""]), [Credit(name: "Ryan Coogler", more: 0)])
        // Two: both when they fit, else the first and "+1".
        XCTAssertEqual(
            SearchRowText.credits(["Alan Moore", "Dave Gibbons"]),
            [Credit(name: "Alan Moore & Dave Gibbons", more: 0), Credit(name: "Alan Moore", more: 1)]
        )
        XCTAssertEqual(
            SearchRowText.credits(["Brian Herbert", "Kevin J. Anderson", "Raúl Allén", "Patricia Martín"]),
            [Credit(name: "Brian Herbert", more: 3)]
        )
    }

    func testTheYearIsTheServersElseTheReleaseDatesElseALineThatIsOnlyAYear() {
        let ref = MediaRef(itemId: nil, source: "comicvine", mediaType: "comic", mediaId: "3622", seasonNumber: nil, episodeNumber: nil)
        var sent = MediaSummary(ref: ref, title: "Watchmen", releaseDate: "1987-01-01")
        sent.year = 1986

        XCTAssertEqual(SearchRowText.year(of: sent), "1986")
        XCTAssertEqual(SearchRowText.year(of: MediaSummary(ref: ref, title: "Watchmen", releaseDate: "1986-09-01")), "1986")
        XCTAssertEqual(SearchRowText.year(of: MediaSummary(ref: ref, title: "Watchmen", subtitle: "1986")), "1986")
        XCTAssertNil(SearchRowText.year(of: MediaSummary(ref: ref, title: "Watchmen", subtitle: "1986 · DC Comics")))
    }

    func testVoiceOverNamesEveryoneWhoMadeItAndTheKindOnlyWhenEveryKindIsSearched() {
        let every = SearchRowText.spokenLine(mediaType: "comic", makers: ["Alan Moore", "Dave Gibbons"], year: "1986", showsKind: true)
        let one = SearchRowText.spokenLine(mediaType: "game", makers: [], year: nil, showsKind: false)

        XCTAssertEqual(every, "Comic, Alan Moore and Dave Gibbons, 1986")
        XCTAssertNil(one)
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
           "friends": {"count": 5, "people": [{"id": 2, "username": "maya", "display_name": "Maya", "avatar_url": null}]},
           "made_by": ["Denis Villeneuve"], "year": 2024},
          {"ref": {"item_id": 8, "source": "tmdb", "media_type": "movie", "media_id": "438631"}, "title": "Dune",
           "friends": {"count": "lots"}, "made_by": "Denis Villeneuve", "year": "soon"},
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
        XCTAssertEqual(results[0].madeBy, ["Denis Villeneuve"])
        XCTAssertEqual(results[0].year, 2024)
        XCTAssertNil(results[1].madeBy)
        XCTAssertNil(results[1].year)

        // Recently opened keeps who made a title and its year.
        let kept = try JSONDecoder().decode([MediaSummary].self, from: JSONEncoder().encode(results))
        XCTAssertEqual(kept[0].madeBy, ["Denis Villeneuve"])
        XCTAssertEqual(kept[0].year, 2024)
    }

    private func face(_ username: String) -> MediaFriendFace {
        MediaFriendFace(id: username.count, username: username, displayName: username, avatarUrl: nil)
    }
}
