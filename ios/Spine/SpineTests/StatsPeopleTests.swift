import Foundation
import XCTest
@testable import Spine

/// People (STAT-219): decoding, which groups show, the per-tab words, the headshot rows and
/// what a person opens.
@MainActor
final class StatsPeopleTests: XCTestCase {
    private let ownMovies2025 = StatsDrillDownContext(period: .year(2025), mediaType: "movie", isOwnStats: true)
    private let theirsMovies2025 = StatsDrillDownContext(period: .year(2025), mediaType: "movie", isOwnStats: false)

    // MARK: - Decoding

    func testDecodesEveryGroupInTheServersOrder() throws {
        let people = try Self.decode(StatsPeople.self, """
        {"groups": [
          {"role": "director", "label": "Directors", "total": 412,
           "rows": [\(Self.rowJSON(id: "525", name: "Christopher Nolan", titles: 9, rated: 8, average: "4.19", image: "https://image.tmdb.org/n.jpg"))],
           "rated_rows": [\(Self.rowJSON(id: "525", name: "Christopher Nolan", titles: 9, rated: 8, average: "4.19"))]},
          {"role": "cast", "label": "Cast", "total": 0, "rows": [], "rated_rows": []}
        ]}
        """)

        XCTAssertEqual(people.groups.map(\.role), ["director", "cast"])
        XCTAssertEqual(people.groups.map(\.label), ["Directors", "Cast"])
        let director = try XCTUnwrap(people.groups.first)
        XCTAssertEqual(director.total, 412)
        let nolan = try XCTUnwrap(director.rows.first)
        XCTAssertEqual(nolan.person.ref, PersonRef(source: "tmdb", id: "525"))
        XCTAssertEqual(nolan.person.id, "tmdb:525")
        XCTAssertEqual(nolan.person.name, "Christopher Nolan")
        XCTAssertEqual(nolan.person.imageUrl, "https://image.tmdb.org/n.jpg")
        XCTAssertEqual([nolan.titles, nolan.rated], [9, 8])
        XCTAssertEqual(nolan.averageStars?.text, "4.19")
        XCTAssertEqual(director.ratedRows.count, 1)
        XCTAssertNil(director.ratedRows.first?.person.imageUrl)
        XCTAssertFalse(people.isEmpty)
        XCTAssertTrue(people.groups[1].isEmpty)
    }

    func testABadRowIsDroppedAndOddValuesAreRead() throws {
        let group = try Self.decode(StatsPeopleGroup.self, """
        {"role": "author", "label": "Authors", "total": 1,
         "rows": [
           {"person": {"source": "hardcover", "id": 77, "name": "Ursula K. Le Guin", "image_url": ""}, "titles": 3, "rated": 0, "average_stars": null},
           {"titles": 2},
           {"person": {"name": "No source"}, "titles": 1}
         ],
         "rated_rows": "oops"}
        """)

        XCTAssertEqual(group.rows.map(\.person.name), ["Ursula K. Le Guin"], "Rows without a person are dropped")
        XCTAssertEqual(group.rows.first?.person.ref.id, "77", "A bare number ID reads as a string")
        XCTAssertNil(group.rows.first?.person.imageUrl, "An empty image is no image")
        XCTAssertNil(group.rows.first?.averageStars)
        XCTAssertTrue(group.ratedRows.isEmpty)
    }

    func testPeopleAreOptional() throws {
        let page = try JSONDecoder.api.decode(StatsPage.self, from: Data(#"{"schema_version": 2, "media_type": "movie", "people": null}"#.utf8))
        XCTAssertNil(page.people)
        let broken = try JSONDecoder.api.decode(StatsPage.self, from: Data(#"{"schema_version": 2, "media_type": "movie", "people": [1, 2]}"#.utf8))
        XCTAssertNil(broken.people, "A malformed block hides the section")
        XCTAssertTrue(StatsPeopleData.visibleGroups(nil).isEmpty)
    }

    // MARK: - Groups and rows

    func testOnlyGroupsWithPeopleShow() {
        let people = StatsPeople(groups: [
            group("cast", rows: 0),
            group("creator", rows: 3),
        ])
        XCTAssertEqual(StatsPeopleData.visibleGroups(people).map(\.role), ["creator"])
        XCTAssertTrue(StatsPeopleSection.rows(for: group("cast", rows: 0), sort: .titles).isEmpty)
        XCTAssertTrue(StatsPeople(groups: [group("cast", rows: 0)]).isEmpty)
    }

    func testTenPeopleInTwoRowsOfFiveThenShowAll() {
        let directors = group("director", rows: 10, rated: 4, total: 412)
        let rows = StatsPeopleSection.rows(for: directors, sort: .titles)

        XCTAssertEqual(rows.map(\.id), [
            "people.director.title",
            "people.director.picker",
            "people.director.titles.0",
            "people.director.titles.1",
            "people.director.showAll",
        ], "Each headshot row is its own lazy item, its ID carrying the role")
        guard case let .people(first, firstIsLast) = rows[2], case let .people(second, secondIsLast) = rows[3] else {
            return XCTFail("Expected two headshot rows")
        }
        XCTAssertEqual(first.items.map(\.person.name), (1 ... 5).map { "Person \($0)" })
        XCTAssertEqual(second.items.count, 5)
        XCTAssertEqual([firstIsLast, secondIsLast], [false, true])
        guard case let .showAll(title, _) = rows[4] else { return XCTFail("Expected Show all") }
        XCTAssertEqual(title, "Show all \(412.formatted())")
        XCTAssertEqual(StatsPeopleSection.rows(for: directors, sort: .titles, canShowAll: false).last?.id, "people.director.titles.1")
    }

    func testFewPeopleHaveNoShowAllAndNoRatingsHaveNoPicker() {
        let cast = group("cast", rows: 3, rated: 0, total: 3)
        XCTAssertEqual(StatsPeopleSection.rows(for: cast, sort: .titles).map(\.id), ["people.cast.title", "people.cast.titles.0"])
        XCTAssertNil(StatsPeopleData.showAllTitle(cast, sort: .titles))
        XCTAssertEqual(StatsPeopleData.effectiveSort(chosen: .rating, in: cast), .titles, "Highest rated needs someone rated three times")
    }

    func testHighestRatedShowsTheRatedRows() {
        let directors = group("director", rows: 12, rated: 10, total: 40)
        XCTAssertEqual(StatsPeopleData.effectiveSort(chosen: nil, in: directors), .titles)
        XCTAssertEqual(StatsPeopleData.effectiveSort(chosen: .rating, in: directors), .rating)

        let rows = StatsPeopleSection.rows(for: directors, sort: .rating)
        XCTAssertEqual(rows.map(\.id), [
            "people.director.title", "people.director.picker",
            "people.director.rating.0", "people.director.rating.1", "people.director.showAll",
        ])
        guard case let .people(first, _) = rows[2] else { return XCTFail("Expected headshots") }
        XCTAssertEqual(first.items.first?.person.name, "Rated 1")
        XCTAssertEqual(StatsPeopleData.showAllTitle(directors, sort: .rating), "Show all", "The rated count isn't in the payload")
        XCTAssertNil(StatsPeopleData.showAllTitle(group("director", rows: 12, rated: 4, total: 40), sort: .rating))
        XCTAssertEqual(StatsPeopleData.rows(group("director", rows: 12, rated: 0, total: 12), sort: .titles).count, 10)
    }

    // MARK: - Words

    func testEachTabUsesItsOwnWords() {
        XCTAssertEqual(
            ["movie", "tv", "music", "book"].map { StatsPeopleData.sortTitle(.titles, mediaType: $0) },
            ["Most watched", "Most watched", "Most listened", "Most read"]
        )
        XCTAssertEqual(StatsPeopleData.sortTitle(.rating, mediaType: "music"), "Highest rated")

        let nine = row("Christopher Nolan", titles: 9, average: "4.19")
        XCTAssertEqual(StatsPeopleData.caption(nine, sort: .titles, mediaType: "movie"), "9 films")
        XCTAssertEqual(StatsPeopleData.caption(row("A", titles: 1), sort: .titles, mediaType: "tv"), "1 show")
        XCTAssertEqual(StatsPeopleData.caption(row("A", titles: 12), sort: .titles, mediaType: "music"), "12 albums")
        XCTAssertEqual(StatsPeopleData.caption(row("A", titles: 3), sort: .titles, mediaType: "book"), "3 books")
        XCTAssertEqual(StatsPeopleData.caption(nine, sort: .rating, mediaType: "movie"), "4.2★")
        XCTAssertEqual(StatsPeopleData.caption(row("A", titles: 4), sort: .rating, mediaType: "movie"), "4 films", "No average falls back to the count")
        XCTAssertEqual(StatsPeopleData.accessibilityLabel(nine, sort: .titles, mediaType: "movie"), "Christopher Nolan, 9 films")
        XCTAssertEqual(StatsPeopleData.accessibilityLabel(nine, sort: .rating, mediaType: "movie"), "Christopher Nolan, Rated 4.19 out of 5 stars")
        XCTAssertEqual(StatsPeopleListView.caption(sort: .titles, mediaType: "movie", period: .year(2025)), "Most watched · Movies · 2025")
    }

    // MARK: - What a person opens

    func testAPersonOpensTheirLogsInTheirRole() {
        let nolan = row("Christopher Nolan", titles: 9).person
        let drillDown = StatsDrillDown.person(nolan, role: "director")
        let query = drillDown.diaryFilter(in: ownMovies2025).queryItems().map { [$0.name, $0.value ?? ""] }

        XCTAssertTrue(query.contains(["credit", "tmdb:person-christopher-nolan"]))
        XCTAssertTrue(query.contains(["role", "director"]))
        XCTAssertTrue(query.contains(["tv_rollup", "true"]))
        XCTAssertTrue(query.contains(["skip_backfill", "true"]))
        XCTAssertTrue(query.contains(["watched_from", "2025-01-01"]))
        XCTAssertTrue(query.contains(["media_type", "movie"]))
        XCTAssertEqual(drillDown.diaryTitle(in: ownMovies2025), "Christopher Nolan · 2025")
        XCTAssertEqual(drillDown.route(in: ownMovies2025), .diary(drillDown, ownMovies2025))
    }

    func testSomeoneElsesPeopleOpenATMDBPageOrNothing() {
        let nolan = row("Christopher Nolan", titles: 9).person
        let route = StatsDrillDown.person(nolan, role: "director").route(in: theirsMovies2025)
        XCTAssertEqual(route, .person(nolan.ref))
        XCTAssertEqual(route?.presentsFullScreen, true)

        let author = StatsPerson(ref: PersonRef(source: "hardcover", id: "77"), name: "Ursula K. Le Guin", imageUrl: nil)
        XCTAssertNil(StatsDrillDown.person(author, role: "author").route(in: theirsMovies2025))
        XCTAssertNotNil(StatsDrillDown.person(author, role: "author").route(in: ownMovies2025), "Your own stats open anyone's logs")
    }

    func testCreditIsOneFilterAndRoleRidesWithIt() {
        var filter = MediaFilterState()
        filter.role = "cast"
        XCTAssertFalse(filter.queryItems().contains { $0.name == "role" }, "A role alone sends nothing")
        XCTAssertEqual(filter.activeCount, 0)

        filter.credit = "tmdb:6193"
        XCTAssertEqual(filter.activeCount, 1)
        let query = filter.queryItems().map { [$0.name, $0.value ?? ""] }
        XCTAssertTrue(query.contains(["credit", "tmdb:6193"]))
        XCTAssertTrue(query.contains(["role", "cast"]))
    }

    // MARK: - Helpers

    private func row(_ name: String, titles: Int, rated: Int = 0, average: String? = nil, source: String = "tmdb") -> StatsPersonRow {
        let slug = name.lowercased().replacingOccurrences(of: " ", with: "-")
        return StatsPersonRow(
            person: StatsPerson(ref: PersonRef(source: source, id: "person-\(slug)"), name: name, imageUrl: nil),
            titles: titles,
            rated: rated,
            averageStars: average.flatMap(StatsDecimal.init)
        )
    }

    private func group(_ role: String, rows: Int, rated: Int = 0, total: Int? = nil) -> StatsPeopleGroup {
        StatsPeopleGroup(
            role: role,
            label: role.capitalized,
            rows: (0 ..< rows).map { row("Person \($0 + 1)", titles: 20 - $0) },
            ratedRows: (0 ..< rated).map { row("Rated \($0 + 1)", titles: 5, rated: 4, average: "4.5") },
            total: total ?? rows
        )
    }

    private static func rowJSON(id: String, name: String, titles: Int, rated: Int, average: String?, image: String? = nil) -> String {
        """
        {"person": {"source": "tmdb", "id": "\(id)", "name": "\(name)", "image_url": \(image.map { "\"\($0)\"" } ?? "null")},
         "titles": \(titles), "rated": \(rated), "average_stars": \(average.map { "\"\($0)\"" } ?? "null")}
        """
    }

    private static func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(T.self, from: Data(json.utf8))
    }
}
