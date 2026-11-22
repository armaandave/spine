import UIKit
import XCTest
@testable import Spine

final class LogShareContentTests: XCTestCase {
    func testSpoilerWordsAndFavoriteAreNeverDrawn() throws {
        let content = LogShareContent(entry: try entry(review: "The twin dies.", containsSpoilers: true, favorite: true), detail: nil)

        XCTAssertNil(content.words)
        XCTAssertTrue(content.hidesSpoilers)
        XCTAssertNil(content.favorite)
    }

    func testSeasonLogNamesTheShowThenTheSeason() throws {
        let content = LogShareContent(entry: try entry(mediaType: "season", title: "Severance", seasonNumber: 2), detail: nil)

        XCTAssertEqual(content.title, "Severance")
        XCTAssertEqual(content.subtitle, "Season 2")
    }

    func testDateLeavesOutThisYear() {
        let now = Date(timeIntervalSince1970: 1_795_300_000) // Nov 2026

        XCTAssertEqual(LogShareContent.dateLabel("2026-11-20T12:00:00Z", now: now), "Nov 20")
        XCTAssertEqual(LogShareContent.dateLabel("2025-03-21", now: now), "Mar 21, 2025")
    }

    func testWordsAreCutAfterAWholeWord() {
        let text = String(repeating: "Coogler takes his time and you are glad he does. ", count: 6)
        let fitted = ShareTextFit.fit(text, font: .systemFont(ofSize: 15.5), lineSpacing: 4, width: 300, lines: 2)

        XCTAssertTrue(fitted.isCut)
        XCTAssertTrue(fitted.text.hasSuffix("…"))
        let kept = String(fitted.text.dropLast())
        XCTAssertTrue(text.hasPrefix(kept))
        XCTAssertEqual(text[text.index(text.startIndex, offsetBy: kept.count)], " ")
        XCTAssertFalse(ShareTextFit.fit("Short.", font: .systemFont(ofSize: 15.5), lineSpacing: 4, width: 300, lines: 2).isCut)
    }

    func testWordsWithoutSpacesAreCutBetweenCharacters() {
        let text = String(repeating: "葬送のフリーレンは静かで優しい物語だった。", count: 8)
        let fitted = ShareTextFit.fit(text, font: .systemFont(ofSize: 15.5), lineSpacing: 4, width: 300, lines: 2)

        XCTAssertTrue(fitted.isCut)
        XCTAssertLessThan(fitted.text.count, text.count)
        XCTAssertTrue(text.hasPrefix(String(fitted.text.dropLast())))
    }

    private func entry(
        mediaType: String = "movie",
        title: String = "Entry",
        seasonNumber: Int? = nil,
        review: String = "",
        containsSpoilers: Bool = false,
        favorite: Bool = false
    ) throws -> DiaryEntry {
        var object: [String: Any] = [
            "id": 1,
            "user": ["id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": NSNull()],
            "media": [
                "ref": [
                    "item_id": 101, "source": "tmdb", "media_type": mediaType, "media_id": "1",
                    "season_number": seasonNumber.map { $0 as Any } ?? NSNull(), "episode_number": NSNull(),
                ],
                "title": title,
                "show_title": title,
                "image_url": NSNull(),
            ],
            "consumed_at": "2026-06-20T12:00:00Z",
            "rating": "4.5",
            "review_title": "",
            "review": review,
            "contains_spoilers": containsSpoilers,
            "liked": true,
            "is_rewatch": false,
            "tags": [],
            "visibility": "public",
            "like_count": 0,
            "viewer_has_liked": false,
            "created_at": "2026-06-20T12:00:00Z",
            "updated_at": "2026-06-20T12:00:00Z",
        ]
        if favorite {
            object["favorite"] = ["kind": "episode", "title": "Cold Harbor", "season_number": 2, "episode_number": 10]
        }
        return try JSONDecoder.api.decode(DiaryEntry.self, from: JSONSerialization.data(withJSONObject: object))
    }
}
