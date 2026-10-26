import Foundation
import XCTest
@testable import Spine

@MainActor
final class PeopleListPresentationTests: XCTestCase {
    func testTypedMediaSummaryPreservesExistingPreviewAndCounts() throws {
        let list = try JSONDecoder.api.decode(
            CustomListSummary.self,
            from: Data(
                """
                {
                  "id": 7,
                  "name": "Movies",
                  "slug": "movies",
                  "description": "",
                  "visibility": "private",
                  "list_type": "media",
                  "owner": {
                    "id": 1,
                    "username": "mobile",
                    "display_name": "Mobile",
                    "avatar_url": null
                  },
                  "preview_items": [{
                    "ref": {
                      "item_id": 42,
                      "source": "tmdb",
                      "media_type": "movie",
                      "media_id": "550",
                      "season_number": null,
                      "episode_number": null
                    },
                    "title": "Fight Club"
                  }],
                  "items_count": 3,
                  "entries_count": 3,
                  "like_count": 0
                }
                """.utf8
            )
        )

        XCTAssertEqual(list.listType, .media)
        XCTAssertEqual(list.itemsCount, 3)
        XCTAssertEqual(list.entriesCount, 3)
        XCTAssertEqual(list.previewItems?.first?.title, "Fight Club")
    }

    func testAddToListCountLabelsRemainTypeAware() {
        XCTAssertEqual(
            AddToListTarget.media(media.ref).countLabel(for: list(itemsCount: 1)),
            "1 item"
        )
        XCTAssertEqual(
            AddToListTarget.media(media.ref).countLabel(for: list(itemsCount: 2)),
            "2 items"
        )
        XCTAssertEqual(
            AddToListTarget.person(person.ref).countLabel(
                for: list(listType: .people, itemsCount: 99, peopleCount: 1, entriesCount: 1)
            ),
            "1 person"
        )
        XCTAssertEqual(
            AddToListTarget.person(person.ref).countLabel(
                for: list(listType: .people, itemsCount: 99, peopleCount: 2, entriesCount: 2)
            ),
            "2 people"
        )
    }

    func testRecentActivityKeepsMediaAndPersonSubjectsAndDropsUnsupportedEntries() {
        let mediaActivity = activity(id: 1, media: media)
        let personActivity = activity(id: 2, person: person)
        let items = ProfileRecentActivityRailModel.items(
            from: [mediaActivity, activity(id: 3), personActivity]
        )

        XCTAssertEqual(items.map(\.id), [1, 2])
        guard case let .media(selectedMedia) = items[0].subject else {
            return XCTFail("Expected the legacy media subject")
        }
        XCTAssertEqual(selectedMedia.ref, media.ref)
        guard case let .person(selectedPerson) = items[1].subject else {
            return XCTFail("Expected the person subject")
        }
        XCTAssertEqual(selectedPerson.ref, person.ref)
        XCTAssertEqual(
            ProfileRecentActivityRailModel.artworkHeight(for: [items[1]]),
            PosterSlot.profileRail.size.height
        )
    }

    func testLegacyActivityFallbackLabelsArePreserved() {
        XCTAssertEqual(
            ProfileRecentActivityRailModel.fallbackLabel(
                for: activity(id: 1, type: "list_created", objectName: "  Movie Picks  ")
            ),
            "Movie Picks"
        )
        XCTAssertEqual(
            ProfileRecentActivityRailModel.fallbackLabel(
                for: activity(id: 2, type: "list_item_added")
            ),
            "List"
        )
    }

    func testCustomListChangePublishesListIDAndType() {
        let notification = expectation(
            forNotification: .customListsDidChange,
            object: nil
        ) { notification in
            XCTAssertEqual(CustomListChange.listId(from: notification), 42)
            XCTAssertEqual(
                notification.userInfo?[CustomListChange.listTypeKey] as? String,
                CustomListType.people.rawValue
            )
            return true
        }

        CustomListChange.post(listId: 42, listType: .people)

        wait(for: [notification], timeout: 0.1)
    }

    private var media: MediaSummary {
        MediaSummary(
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
    }

    private var person: ActivityPersonSnapshot {
        ActivityPersonSnapshot(
            source: "tmdb",
            id: "525",
            name: "Christopher Nolan",
            profileUrl: nil,
            knownForDepartment: "Directing"
        )
    }

    private func list(
        listType: CustomListType = .media,
        itemsCount: Int,
        peopleCount: Int = 0,
        entriesCount: Int? = nil
    ) -> CustomListSummary {
        CustomListSummary(
            id: 7,
            name: "List",
            slug: "list",
            description: "",
            visibility: "private",
            listType: listType,
            owner: UserSummary(
                id: 1,
                username: "mobile",
                displayName: "Mobile",
                avatarUrl: nil
            ),
            itemsCount: itemsCount,
            peopleCount: peopleCount,
            entriesCount: entriesCount,
            likeCount: 0
        )
    }

    private func activity(
        id: Int,
        type: String = "list_item_added",
        media: MediaSummary? = nil,
        person: ActivityPersonSnapshot? = nil,
        objectName: String? = nil
    ) -> ActivityItem {
        ActivityItem(
            id: id,
            type: type,
            createdAt: nil,
            actor: UserSummary(
                id: 1,
                username: "mobile",
                displayName: "Mobile",
                avatarUrl: nil
            ),
            media: media,
            person: person,
            object: ActivityObject(
                type: "list",
                id: 7,
                previous: nil,
                current: nil,
                rating: nil,
                liked: nil,
                name: objectName
            )
        )
    }
}
