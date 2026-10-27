import XCTest
@testable import Spine

@MainActor
final class ActivityPeopleTests: XCTestCase {
    func testDecodesTopLevelPersonSnapshot() throws {
        let data = Data(
            """
            {
              "id": 42,
              "type": "list_item_added",
              "created_at": "2026-07-27T12:00:00Z",
              "actor": {
                "id": 1,
                "username": "mobile",
                "display_name": "Mobile",
                "avatar_url": null
              },
              "media": null,
              "person": {
                "source": "tmdb",
                "id": "525",
                "name": "Christopher Nolan",
                "profile_url": "https://example.com/nolan.jpg",
                "known_for_department": "Directing"
              },
              "object": {
                "type": "list",
                "id": 9,
                "name": "Favorite Directors"
              }
            }
            """.utf8
        )

        let activity = try JSONDecoder.api.decode(ActivityItem.self, from: data)

        XCTAssertEqual(activity.person?.ref, PersonRef(source: "tmdb", id: "525"))
        XCTAssertEqual(activity.person?.name, "Christopher Nolan")
        XCTAssertEqual(activity.person?.knownForDepartment, "Directing")
        XCTAssertEqual(ActivityFeedPresentation.actionText(for: activity), "added to a list")
        XCTAssertEqual(ActivityFeedPresentation.listName(for: activity), "Favorite Directors")
        XCTAssertEqual(ActivityFeedPresentation.destinationHint(for: activity), "Opens person details")
    }

    func testDirectStarsReadAsARating() throws {
        // Stars set straight on a show or season post rating activity (SW-401, TV-703); it isn't "shared activity".
        func activity(mediaType: String, seasonNumber: Int? = nil) throws -> ActivityItem {
            let season = seasonNumber.map { #","season_number":\#($0)"# } ?? ""
            return try JSONDecoder.api.decode(ActivityItem.self, from: Data("""
            {"id":7,"type":"rating_updated","created_at":"2026-10-26T14:00:00Z",
             "actor":{"id":1,"username":"claude","display_name":"Claude","avatar_url":null},
             "media":{"ref":{"source":"tmdb","media_type":"\(mediaType)","media_id":"19885"\(season)},"title":"Sherlock"},
             "object":{"type":"tracking","id":1,"rating":"8.0"}}
            """.utf8))
        }

        let show = try activity(mediaType: "tv")
        XCTAssertEqual(ActivityFeedPresentation.actionText(for: show), "rated a TV show")
        XCTAssertEqual(ActivityFeedPresentation.rating(for: show), "8.0")
        XCTAssertEqual(ActivityFeedPresentation.destinationHint(for: show), "Opens media details")
        XCTAssertEqual(ActivityFeedPresentation.actionText(for: try activity(mediaType: "season", seasonNumber: 1)), "rated a season")
        XCTAssertEqual(ActivityFeedPresentation.actionText(for: try activity(mediaType: "movie")), "rated a movie")
        XCTAssertEqual(ActivityFeedPresentation.actionText(for: try activity(mediaType: "music")), "rated an album")
    }

    func testDestinationPrecedenceIsDiaryThenPersonThenMediaThenNone() {
        let person = ActivityPersonSnapshot(
            source: "tmdb",
            id: "525",
            name: "Christopher Nolan",
            profileUrl: nil,
            knownForDepartment: nil
        )
        let media = MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: "tmdb",
                mediaType: "movie",
                mediaId: "13",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Movie"
        )

        XCTAssertEqual(
            ActivityDestination.resolve(activity(objectType: "diary", media: media, person: person)),
            .diary(7)
        )
        XCTAssertEqual(
            ActivityDestination.resolve(activity(objectType: "list", media: media, person: person)),
            .person(PersonRef(source: "tmdb", id: "525"))
        )
        XCTAssertEqual(
            ActivityDestination.resolve(activity(objectType: "item", media: media)),
            .media(media.ref)
        )
        XCTAssertEqual(
            ActivityDestination.resolve(activity(objectType: "list")),
            .none
        )
    }

    private func activity(
        objectType: String,
        media: MediaSummary? = nil,
        person: ActivityPersonSnapshot? = nil
    ) -> ActivityItem {
        ActivityItem(
            id: 1,
            type: "list_item_added",
            createdAt: nil,
            actor: UserSummary(id: 1, username: "mobile", displayName: "Mobile", avatarUrl: nil),
            media: media,
            person: person,
            object: ActivityObject(
                type: objectType,
                id: 7,
                previous: nil,
                current: nil,
                rating: nil,
                liked: nil,
                name: nil
            )
        )
    }
}
