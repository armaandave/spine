import XCTest
@testable import Spine

/// The profile's This Year section: the wall's grid rule and the rows built from the server's numbers (STAT-312).
final class ProfileYearTests: XCTestCase {
    func testWallTakesTheMostPostersThatFillItAtAPostersShape() {
        // A menu-width wall on a 430 pt phone: 398 wide by 568 tall.
        let steps = [8, 9, 15, 16, 19, 20, 24, 25, 29, 30, 35, 36, 50].map { posters in
            ProfileYearWall.grid(posters: posters, width: 398, height: 568).map { [$0.across, $0.down] }
        }
        XCTAssertEqual(steps, [
            nil,
            [3, 3], [3, 3],
            [4, 4], [4, 4],
            [5, 4], [5, 4],
            [5, 5], [5, 5],
            [6, 5], [6, 5],
            [6, 6], [6, 6],
        ])
        // Every wall is full: no gaps, and never more than 6 across.
        for posters in 9...60 {
            guard let grid = ProfileYearWall.grid(posters: posters, width: 398, height: 568) else {
                XCTFail("No wall for \(posters) posters")
                continue
            }
            XCTAssertLessThanOrEqual(grid.count, posters)
            XCTAssertLessThanOrEqual(grid.across, ProfileYearWall.maximumAcross)
        }
    }

    func testRowsFollowTheTurnedOnTypesAndShowTimeOnlyWhenThereIsSome() throws {
        let year = try JSONDecoder.api.decode(ProfileYear.self, from: Data(Self.payload.utf8))

        XCTAssertEqual(year.year, 2026)
        XCTAssertEqual(year.titles, 30)
        XCTAssertEqual(year.posters.map(\.title), ["Sinners"])

        let rows = ProfileYearPresentation.rows(for: year)
        XCTAssertEqual(rows.map(\.mediaType), ["movie", "book", "music", "game"])
        XCTAssertEqual(rows.map(\.isEmpty), [false, false, false, true])
        // Movies and books have time; the album's runtime is unknown (0) and Games has nothing this year.
        XCTAssertEqual(rows.map { $0.measure != nil }, [true, true, false, false])
        XCTAssertEqual(rows[0].measure, StatsTimeSection.value(for: year.time.rows[0]))
    }

    func testTheWallStaysTheSameForTheSessionUntilANewDeal() throws {
        let session = ProfileYearWallSession()
        let first = try (1...12).map { try Self.poster("first-\($0)") }
        let later = try (1...12).map { try Self.poster("later-\($0)") }

        let shown = session.posters(for: "maya/2026", from: first, dealNew: false)
        XCTAssertEqual(Set(shown.map(\.id)), Set(first.map(\.id)))
        // Coming back to the profile, with a new pick from the server: the same wall, in the same order.
        XCTAssertEqual(session.posters(for: "maya/2026", from: later, dealNew: false).map(\.id), shown.map(\.id))
        // Another profile has its own wall.
        XCTAssertEqual(Set(session.posters(for: "theo/2026", from: later, dealNew: false).map(\.id)), Set(later.map(\.id)))
        // Pull to refresh deals from the new pick.
        XCTAssertEqual(Set(session.posters(for: "maya/2026", from: later, dealNew: true).map(\.id)), Set(later.map(\.id)))
    }

    private static func poster(_ mediaID: String) throws -> MediaSummary {
        let json = """
        {"ref": {"item_id": 1, "source": "tmdb", "media_type": "movie", "media_id": "\(mediaID)", "season_number": null, "episode_number": null},
         "title": "\(mediaID)", "poster_url": "https://image.tmdb.org/t/p/w500/\(mediaID).jpg"}
        """
        return try JSONDecoder.api.decode(MediaSummary.self, from: Data(json.utf8))
    }

    private static let payload = """
    {
      "year": 2026,
      "range": {"start_date": "2026-01-01", "end_date": "2026-12-31", "timezone": "America/New_York", "is_all_time": false},
      "audience": "owner",
      "titles": 30,
      "media_types": [
        {"media_type": "movie", "titles": 20, "logs": 24},
        {"media_type": "book", "titles": 8, "logs": 3},
        {"media_type": "music", "titles": 2, "logs": 2},
        {"media_type": "game", "titles": 0, "logs": 0}
      ],
      "time": {
        "rows": [
          {"media_type": "movie", "unit": "minutes", "value": 2400, "known": 24, "total": 24, "approximate": false},
          {"media_type": "book", "unit": "pages", "value": 2210, "known": 8, "total": 8, "approximate": false},
          {"media_type": "music", "unit": "minutes", "value": 0, "known": 0, "total": 2, "approximate": false}
        ],
        "hours_total": {"minutes": 2400, "media_types": ["movie", "game", "music"], "leaves_out": ["book"], "approximate": false}
      },
      "posters": [
        {
          "ref": {"item_id": 812, "source": "tmdb", "media_type": "movie", "media_id": "1233413", "season_number": null, "episode_number": null},
          "title": "Sinners",
          "subtitle": null,
          "overview": null,
          "image_url": "https://image.tmdb.org/t/p/w500/sinners.jpg",
          "poster_url": "https://image.tmdb.org/t/p/w500/sinners.jpg",
          "backdrop_url": null,
          "poster_accent_color": null,
          "release_date": null,
          "default_source": "tmdb",
          "custom_poster_url": null,
          "custom_backdrop_url": null,
          "user_state": null
        }
      ]
    }
    """
}
