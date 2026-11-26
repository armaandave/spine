import XCTest
@testable import Spine

final class PersonCreditSectionsTests: XCTestCase {
    func testSelfAppearanceIsAnActingCreditAsThemselves() {
        for character in ["Self", "Self - Guest", "Himself", "Herself (archive footage)", "Themselves", "self (voice)"] {
            XCTAssertTrue(credit("a", roles: [character], creditRoles: ["Actor"]).isSelfAppearance, character)
        }
        XCTAssertFalse(credit("b", roles: ["Selfridge"], creditRoles: ["Actor"]).isSelfAppearance)
        XCTAssertFalse(credit("c", roles: ["Dom Cobb"], creditRoles: ["Actor"]).isSelfAppearance)
        XCTAssertFalse(credit("d", roles: [], creditRoles: ["Actor"]).isSelfAppearance)
        XCTAssertFalse(credit("e", roles: ["Self"], creditRoles: ["Director"]).isSelfAppearance)
    }

    func testAppearancesLeaveActorAndComeLast() {
        let filmography = [
            credit("role", roles: ["Bob"], creditRoles: ["Actor"]),
            credit("talk", roles: ["Self - Guest"], creditRoles: ["Actor"]),
            credit("doc", roles: ["Self", "Director"], creditRoles: ["Actor", "Director"]),
            credit("film", roles: ["Director"], creditRoles: ["Director"]),
            credit("film2", roles: ["Director"], creditRoles: ["Director"]),
        ]

        let groups = FilmographyCreditGroup.groups(from: filmography, knownForDepartment: "Acting")

        XCTAssertEqual(groups.map(\.role), ["Actor", "Director", "Appearances"])
        XCTAssertEqual(groups[0].media.map(\.ref.mediaId), ["role"])
        XCTAssertEqual(groups[1].media.map(\.ref.mediaId), ["doc", "film", "film2"])
        XCTAssertEqual(groups[2].media.map(\.ref.mediaId), ["talk", "doc"])
    }

    func testGlobeMergesEachRoleAcrossTypesOneOfEachInTurn() {
        let filmography = [
            credit("f1", creditRoles: ["Actor"], done: true),
            credit("f2", creditRoles: ["Actor"]),
            credit("f3", creditRoles: ["Actor"]),
            credit("p1", creditRoles: ["Producer"]),
            credit("t1", mediaType: "tv", creditRoles: ["Actor"], done: true),
            credit("t2", mediaType: "tv", creditRoles: ["Actor"]),
        ]

        let sections = PersonCreditSections.make(
            filmography: filmography,
            kind: PersonCreditSections.allKind,
            knownForDepartment: "Acting"
        )

        XCTAssertEqual(sections.map(\.title), ["Actor", "Producer"])
        XCTAssertEqual(sections[0].media.map(\.ref.mediaId), ["f1", "t1", "f2", "t2", "f3"])
        XCTAssertEqual(sections[0].count, 5)
        XCTAssertEqual(sections[0].done, 2)

        let shows = PersonCreditSections.make(filmography: filmography, kind: "tv", knownForDepartment: "Acting")
        XCTAssertEqual(shows.map(\.title), ["Actor"])
        XCTAssertEqual(shows[0].media.map(\.ref.mediaId), ["t1", "t2"])
    }

    func testTheGlobeIsOfferedFirstOnlyForTwoOrMoreTypes() {
        let mixed = [credit("m", creditRoles: ["Actor"]), credit("t", mediaType: "tv", creditRoles: ["Actor"])]
        XCTAssertEqual(PersonCreditSections.kinds(in: mixed), [PersonCreditSections.allKind, "movie", "tv"])
        XCTAssertEqual(PersonCreditSections.defaultKind(in: mixed), PersonCreditSections.allKind)

        let music = [credit("a", mediaType: "music", creditRoles: ["Albums"])]
        XCTAssertEqual(PersonCreditSections.kinds(in: music), ["music"])
        XCTAssertEqual(PersonCreditSections.defaultKind(in: music), "music")
    }

    @MainActor
    func testSeriesComeAfterEveryRole() throws {
        let books = [credit("b1", mediaType: "book", creditRoles: ["Author"])]
        let series = try JSONDecoder.api.decode(
            MediaSeriesSummary.self,
            from: Data(#"{"id": "981", "source": "hardcover", "media_type": "book", "name": "A Song of Ice and Fire", "item_count": 5, "poster_urls": []}"#.utf8)
        )

        let sections = PersonCreditSections.make(
            filmography: books,
            kind: "book",
            knownForDepartment: "Author",
            series: { $0 == "book" ? [series] : [] }
        )

        XCTAssertEqual(sections.map(\.title), ["Author", "Series"])
        XCTAssertNil(sections[1].done)
        XCTAssertEqual(sections[1].count, 1)
    }

    func testLayoutWorksOutASectionsHeightFromItsCount() {
        let section = PersonCreditSection(id: "a", title: "A", content: .media((1...5).map { credit("a\($0)") }))

        // 398 wide: four 93.5 pt columns, so 140.25 pt posters; five posters are two rows.
        // Header 28 + gap 12 + two rows (2 x 140.25 + 10).
        XCTAssertEqual(PersonCreditsLayout.height(of: section, width: 398), 330.5, accuracy: 0.001)
    }

    func testLinesAreEachHeaderThenRowsOfFourAndOnlyTheLastSectionEndsWithoutAGap() {
        let sections = [
            PersonCreditSection(id: "a", title: "A", content: .media((1...5).map { credit("a\($0)") })),
            PersonCreditSection(id: "b", title: "B", content: .media([credit("b1")])),
        ]

        let lines = PersonCreditLine.lines(for: sections)

        XCTAssertEqual(lines.map(\.id), ["header-a", "row-a-0", "row-a-1", "header-b", "row-b-0"])
        guard case let .media(_, _, first, firstGap) = lines[1],
              case let .media(_, _, second, secondGap) = lines[2],
              case let .media(_, _, last, lastGap) = lines[4] else {
            return XCTFail("Expected poster rows")
        }
        XCTAssertEqual(first.map(\.ref.mediaId), ["a1", "a2", "a3", "a4"])
        XCTAssertEqual(second.map(\.ref.mediaId), ["a5"])
        XCTAssertEqual(last.map(\.ref.mediaId), ["b1"])
        XCTAssertEqual(firstGap, PersonCreditsLayout.rowGap)
        XCTAssertEqual(secondGap, PersonCreditsLayout.sectionGap)
        XCTAssertEqual(lastGap, 0)
    }

    func testTheMapPlacesEveryLineFromTheFixedHeights() {
        let sections = [
            PersonCreditSection(id: "a", title: "A", content: .media((1...5).map { credit("a\($0)") })),
            PersonCreditSection(id: "b", title: "B", content: .media([credit("b1")])),
        ]

        // 398 wide: 140.25 pt posters. A header line is 28 + 12; rows are 10 apart, sections 40.
        let map = PersonCreditsMap(lines: PersonCreditLine.lines(for: sections), width: 398)

        XCTAssertEqual(map.headerTops.count, 2)
        XCTAssertEqual(map.headerTops[0], 0)
        XCTAssertEqual(map.headerTops[1], 370.5, accuracy: 0.001)
        XCTAssertEqual(map.lineTops["row-a-1"] ?? -1, 190.25, accuracy: 0.001)
    }

    func testTheSectionInViewIsTheLastHeaderToReachTheLine() {
        let tops: [CGFloat] = [0, 370.5, 900]

        XCTAssertEqual(PersonCreditsLayout.currentIndex(tops: tops, line: -40), 0)
        XCTAssertEqual(PersonCreditsLayout.currentIndex(tops: tops, line: 370), 0)
        XCTAssertEqual(PersonCreditsLayout.currentIndex(tops: tops, line: 370.5), 1)
        XCTAssertEqual(PersonCreditsLayout.currentIndex(tops: tops, line: 5000), 2)
    }

    private func credit(
        _ id: String,
        mediaType: String = "movie",
        roles: [String] = [],
        creditRoles: [String] = [],
        done: Bool = false
    ) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: mediaType == "book" ? "hardcover" : "tmdb",
                mediaType: mediaType,
                mediaId: id,
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: id,
            posterUrl: "https://example.com/\(id).jpg",
            roles: roles,
            creditRoles: creditRoles,
            userState: done ? UserMediaState(isTracked: true, status: "Completed") : nil
        )
    }
}
