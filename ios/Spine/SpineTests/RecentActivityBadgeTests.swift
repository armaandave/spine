import XCTest
@testable import Spine

@MainActor
final class RecentActivityBadgeTests: XCTestCase {
    func testABookDayInPagesShowsThePagesItAdded() throws {
        XCTAssertEqual(try badge(mediaType: "book", label: "40 pages", unit: "pages", amount: 40), "+40 p.")
        XCTAssertEqual(try badge(mediaType: "book", label: "1024 pages", unit: "pages", amount: 1024), "+1,024 p.")
    }

    func testABookDayTypedInPercentShowsThePercentItAdded() throws {
        XCTAssertEqual(try badge(mediaType: "book", label: "5.25%", unit: "pages", amount: 12, percent: 5.25), "+5.25%")
    }

    func testMangaAndComicDaysShowChaptersAndIssues() throws {
        XCTAssertEqual(try badge(mediaType: "manga", label: "5 chapters", unit: "chapters", amount: 5), "+5 ch.")
        XCTAssertEqual(try badge(mediaType: "comic", label: "12 issues", unit: "issues", amount: 12), "+12 iss.")
    }

    func testShowAndAnimeDaysShowEpisodesNotMinutes() throws {
        XCTAssertEqual(try badge(mediaType: "tv", label: "3 episodes", unit: "minutes", amount: 150), "+3 eps")
        XCTAssertEqual(try badge(mediaType: "anime", label: "1 episode", unit: "minutes", amount: 24), "+1 ep")
    }

    func testAGameDayShowsItsPlaytimeInWholeHoursFromTenHoursUp() throws {
        XCTAssertEqual(try badge(mediaType: "game", label: "2h 30m", unit: "minutes", amount: 150), "+2h 30m")
        XCTAssertEqual(try badge(mediaType: "game", label: "45m", unit: "minutes", amount: 45), "+45m")
        XCTAssertEqual(try badge(mediaType: "game", label: "9h 59m", unit: "minutes", amount: 599), "+9h 59m")
        XCTAssertEqual(try badge(mediaType: "game", label: "12h 45m", unit: "minutes", amount: 765), "+13h")
        XCTAssertEqual(try badge(mediaType: "game", label: "12h 29m", unit: "minutes", amount: 749), "+12h")
    }

    func testAGameDayToldInPercentShowsThePercent() throws {
        XCTAssertEqual(try badge(mediaType: "game", label: "15%", unit: "minutes", amount: nil, percent: 15), "+15%")
    }

    func testAStartAStatusMoveOrALogHasNoBadge() throws {
        XCTAssertNil(try badge(mediaType: "book", label: "Started", unit: "pages", amount: nil))
        XCTAssertNil(try badge(mediaType: "book", label: nil, unit: "pages", amount: nil))
        XCTAssertNil(RecentActivityBadge.text(for: try activity(object: """
        {"type": "diary", "id": 7, "rating": "4.5", "liked": true, "label": "200 pages", "unit": "pages", "amount": 200}
        """)))
    }

    func testALogShowsItsStarsAndHeart() throws {
        let marks = RecentActivityBadge.marks(for: try activity(object: """
        {"type": "diary", "id": 7, "rating": "4.5", "liked": true}
        """))

        XCTAssertEqual(marks?.stars, [.star, .star, .star, .star, .halfStar])
        XCTAssertEqual(marks?.liked, true)
    }

    func testOnlyALogShowsStarsAndHeart() throws {
        XCTAssertNil(RecentActivityBadge.marks(for: try activity(object: """
        {"type": "progress_day", "id": 9, "rating": "4", "liked": true, "label": "40 pages", "unit": "pages", "amount": 40}
        """)))
    }

    // MARK: Helpers

    private func badge(
        mediaType: String,
        label: String?,
        unit: String,
        amount: Double?,
        percent: Double? = nil
    ) throws -> String? {
        let object = """
        {"type": "progress_day", "id": 9, "summary": "", "label": \(json(label)), "unit": "\(unit)",
         "amount": \(json(amount)), "percent": \(json(percent))}
        """
        return RecentActivityBadge.text(for: try activity(mediaType: mediaType, object: object))
    }

    private func activity(mediaType: String = "book", object: String) throws -> ActivityItem {
        let json = """
        {
          "id": -9, "type": "progress_updated", "created_at": "2026-11-01T12:00:00Z", "consumed_at": "2026-11-01",
          "actor": {"id": 3, "username": "jonah", "display_name": "Jonah", "avatar_url": null},
          "media": {"ref": {"source": "tmdb", "media_type": "\(mediaType)", "media_id": "2"}, "title": "A Title"},
          "person": null,
          "object": \(object)
        }
        """
        return try JSONDecoder.api.decode(ActivityItem.self, from: Data(json.utf8))
    }

    private func json(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
    private func json(_ value: Double?) -> String { value.map { "\($0)" } ?? "null" }
}
