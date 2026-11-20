import XCTest
@testable import Spine

final class ListMediaTypeIconsTests: XCTestCase {
    func testListPayloadDecodesMediaTypesAndDefaultsForOlderServers() throws {
        let list = try JSONDecoder.api.decode(
            CustomListSummary.self,
            from: Data(listJSON(extra: #""media_types": ["movie", "tv"],"#).utf8)
        )
        let older = try JSONDecoder.api.decode(CustomListSummary.self, from: Data(listJSON(extra: "").utf8))

        XCTAssertEqual(list.mediaTypes, ["movie", "tv"])
        XCTAssertEqual(list.mediaTypesLabel, "Movies and TV")
        XCTAssertEqual(older.mediaTypes, [])
        XCTAssertNil(older.mediaTypesLabel)
    }

    func testListDetailKeepsItsMediaTypesWhenItsRowsArriveSeparately() throws {
        let detail = try JSONDecoder.api.decode(
            CustomListDetail.self,
            from: Data(listJSON(extra: #""media_types": ["movie"],"#).utf8)
        )

        XCTAssertEqual(detail.withItems([]).mediaTypes, ["movie"])
        XCTAssertEqual(detail.withPeople([]).mediaTypes, ["movie"])
    }

    func testOneTypeShowsItsIconAndAnyMixShowsTheGlobe() {
        XCTAssertEqual(ListMediaTypeIcons.shownTypes([]), [])
        XCTAssertEqual(ListMediaTypeIcons.shownTypes(["movie"]), ["movie"])
        XCTAssertEqual(ListMediaTypeIcons.shownTypes(["anime", "manga"]), [APIConstants.allMedia])
        XCTAssertEqual(ListMediaTypeIcons.shownTypes(["movie", "tv", "book"]), [APIConstants.allMedia])
    }
}

private func listJSON(extra: String) -> String {
    """
    {
        "id": 7,
        "name": "Watch",
        "slug": "watch",
        "description": "",
        "visibility": "private",
        \(extra)
        "owner": {"id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null},
        "items_count": 2,
        "like_count": 0
    }
    """
}
