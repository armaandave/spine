import SwiftUI
import XCTest
@testable import Spine

final class HallOfFameCrownLayoutTests: XCTestCase {
    func testCrownLayoutReturnsEmptyForZeroCount() {
        let placements = HallOfFameCrownLayout.placements(
            count: 0,
            avatarDiameter: 128
        )

        XCTAssertEqual(placements, [])
    }

    func testCrownLayoutSymmetricForTwoCards() {
        let placements = HallOfFameCrownLayout.placements(
            count: 2,
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 2)
        XCTAssertEqual(placements[0].x, -placements[1].x, accuracy: 0.001)
        XCTAssertEqual(placements[0].rotation.degrees, -placements[1].rotation.degrees, accuracy: 0.001)
    }

    func testCrownLayoutCenterCardUprightForThree() {
        let placements = HallOfFameCrownLayout.placements(
            count: 3,
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 3)
        XCTAssertEqual(placements[1].x, 0, accuracy: 0.001)
        XCTAssertEqual(placements[1].rotation.degrees, 0, accuracy: 0.001)
    }

    func testCrownLayoutShowsEightSlots() {
        let placements = HallOfFameCrownLayout.placements(
            count: 8,
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 8)
    }

    func testFavoriteSlotsSortOrderPreserved() {
        let hof: [String: MediaSummary?] = [
            "comic": nil,
            "book": nil,
            "movie": nil,
            "anime": nil,
            "game": nil,
            "tv": nil,
            "manga": nil,
            "music": nil,
        ]

        XCTAssertEqual(
            ProfileFavorites.slots(from: hof).map(\.id),
            ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"]
        )
    }

    func testFavoriteSlotsIncludeDefaultEmptyChoicesWhenAPIOnlySendsFilledItems() {
        let movie = media(index: 1, mediaType: "movie")
        let book = media(index: 2, mediaType: "book")
        let slots = ProfileFavorites.slots(from: [
            "movie": movie,
            "book": book,
        ])

        XCTAssertEqual(slots.map(\.id), ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"])
        XCTAssertEqual(slots.compactMap(\.item).map(\.id), [movie.id, book.id])
    }

    func testFavoriteSlotsPreserveEmptyAndFilledState() {
        let movie = media(index: 1, mediaType: "movie")
        let slots = ProfileFavorites.slots(from: [
            "movie": movie,
            "tv": nil,
        ])

        XCTAssertEqual(slots.count, 8)
        XCTAssertEqual(slots[0].id, "movie")
        XCTAssertEqual(slots[0].item?.id, movie.id)
        XCTAssertEqual(slots[1].id, "tv")
        XCTAssertNil(slots[1].item)
    }

    func testCrownLayoutUsesGeometricArcForEightSlots() {
        let placements = HallOfFameCrownLayout.placements(
            count: 8,
            avatarDiameter: 128
        )

        XCTAssertEqual(placements[3].x, -placements[4].x, accuracy: 0.001)
        XCTAssertEqual(placements[3].rotation.degrees, -placements[4].rotation.degrees, accuracy: 0.001)
        XCTAssertEqual(placements[0].x, -placements[7].x, accuracy: 0.001)
        XCTAssertEqual(placements[0].y, placements[7].y, accuracy: 0.001)
        XCTAssertEqual(placements[0].rotation.degrees, -placements[7].rotation.degrees, accuracy: 0.001)
    }

    func testCrownLayoutRaisesCenterCardAboveAvatar() {
        let placements = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128
        )

        XCTAssertLessThan(placements[3].y, -96)
    }

    func testBelowAvatarCrownMirrorsExpandedArcVertically() {
        let above = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128
        )
        let below = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128,
            position: .belowAvatar
        )

        XCTAssertEqual(below.count, above.count)
        for index in below.indices {
            XCTAssertEqual(below[index].x, above[index].x, accuracy: 0.001)
            XCTAssertEqual(below[index].y, -above[index].y, accuracy: 0.001)
            XCTAssertEqual(below[index].rotation.degrees, -above[index].rotation.degrees, accuracy: 0.001)
            XCTAssertEqual(below[index].scale, above[index].scale, accuracy: 0.001)
        }
    }

    func testCollapsedCrownProgressZeroMatchesExpandedLayout() {
        let expanded = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128
        )
        let collapsedAtZero = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128,
            collapseProgress: 0
        )

        XCTAssertEqual(collapsedAtZero, expanded)
    }

    func testCollapsedCrownProgressOneConvergesAtAvatarCenter() {
        let placements = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128,
            collapseProgress: 1
        )

        XCTAssertEqual(placements.count, 7)
        for placement in placements {
            XCTAssertEqual(placement.x, 0, accuracy: 0.001)
            XCTAssertEqual(placement.y, 0, accuracy: 0.001)
            XCTAssertEqual(placement.rotation.degrees, 0, accuracy: 0.001)
            XCTAssertEqual(placement.scale, 0.28, accuracy: 0.001)
            XCTAssertEqual(placement.zIndex, 0, accuracy: 0.001)
        }
    }

    func testBelowAvatarCrownProgressOneConvergesAtAvatarCenter() {
        let placements = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128,
            collapseProgress: 1,
            position: .belowAvatar
        )

        for placement in placements {
            XCTAssertEqual(placement.x, 0, accuracy: 0.001)
            XCTAssertEqual(placement.y, 0, accuracy: 0.001)
            XCTAssertEqual(placement.rotation.degrees, 0, accuracy: 0.001)
        }
    }

    func testCollapsedCrownMidpointClosesArcInward() {
        let expanded = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128
        )
        let midpoint = HallOfFameCrownLayout.placements(
            count: 7,
            avatarDiameter: 128,
            collapseProgress: 0.5
        )

        XCTAssertEqual(midpoint[0].x, expanded[0].x * 0.5, accuracy: 0.001)
        XCTAssertEqual(midpoint[0].y, expanded[0].y * 0.5, accuracy: 0.001)
        XCTAssertEqual(midpoint[0].rotation.degrees, expanded[0].rotation.degrees * 0.5, accuracy: 0.001)
        XCTAssertEqual(midpoint[0].scale, (expanded[0].scale + 0.28) / 2, accuracy: 0.001)
    }

    func testCrownArrangementPositionsMusicByEnabledSlotParity() {
        let noMusic = HallOfFameCrownLayout.arrangement(for: slots(["movie", "tv", "book", "comic"]))
        XCTAssertNil(noMusic.above)
        XCTAssertEqual(noMusic.below.map(\.id), ["movie", "tv", "book", "comic"])

        for ids in [
            ["music"],
            ["movie", "tv", "music"],
            ["movie", "tv", "anime", "manga", "music"],
            ["movie", "tv", "anime", "manga", "game", "book", "music"],
        ] {
            let arrangement = HallOfFameCrownLayout.arrangement(for: slots(ids))
            XCTAssertNil(arrangement.above)
            XCTAssertEqual(arrangement.below[arrangement.below.count / 2].id, "music")
        }

        for ids in [
            ["movie", "music"],
            ["movie", "tv", "anime", "music"],
            ["movie", "tv", "anime", "manga", "game", "music"],
            ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"],
        ] {
            let arrangement = HallOfFameCrownLayout.arrangement(for: slots(ids))
            XCTAssertEqual(arrangement.above?.id, "music")
            XCTAssertFalse(arrangement.below.contains(where: { $0.id == "music" }))
            XCTAssertEqual(arrangement.below.map(\.id), ids.filter { $0 != "music" })
        }
    }

    func testMusicFrameAndArrangementIgnoreFilledState() {
        let emptySlots = slots(["movie", "music"])
        let filledSlots = [
            FavoriteSlot(id: "movie", title: "Movie", item: nil),
            FavoriteSlot(id: "music", title: "Music", item: media(index: 9, mediaType: "music")),
        ]
        let cardSize = CGSize(width: 70, height: 105)

        XCTAssertEqual(HallOfFameCrownLayout.arrangement(for: emptySlots).above?.id, "music")
        XCTAssertEqual(HallOfFameCrownLayout.arrangement(for: filledSlots).above?.id, "music")
        XCTAssertEqual(
            HallOfFameCrownLayout.visualSize(for: emptySlots[1], cardSize: cardSize),
            CGSize(width: 70, height: 70)
        )
        XCTAssertEqual(
            HallOfFameCrownLayout.visualSize(for: filledSlots[1], cardSize: cardSize),
            CGSize(width: 70, height: 70)
        )
        XCTAssertEqual(HallOfFameCrownLayout.visualSize(for: emptySlots[0], cardSize: cardSize), cardSize)
    }

    func testMusicPlacementMirrorsExistingCollapse() {
        let expanded = HallOfFameCrownLayout.aboveMusicPlacement(
            cardSize: CGSize(width: 60, height: 60),
            lowerCount: 3,
            avatarDiameter: 128,
            collapseProgress: 0
        )
        let halfway = HallOfFameCrownLayout.aboveMusicPlacement(
            cardSize: CGSize(width: 60, height: 60),
            lowerCount: 3,
            avatarDiameter: 128,
            collapseProgress: 0.5
        )
        let collapsed = HallOfFameCrownLayout.aboveMusicPlacement(
            cardSize: CGSize(width: 60, height: 60),
            lowerCount: 3,
            avatarDiameter: 128,
            collapseProgress: 1
        )

        XCTAssertEqual(expanded.x, 0)
        XCTAssertEqual(expanded.y, -130.3, accuracy: 0.001)
        XCTAssertEqual(expanded.rotation, .zero)
        XCTAssertEqual(expanded.scale, 1.04, accuracy: 0.001)
        XCTAssertEqual(halfway.y, -65.15, accuracy: 0.001)
        XCTAssertEqual(collapsed.y, 0, accuracy: 0.001)
        XCTAssertEqual(collapsed.scale, 0.28, accuracy: 0.001)
    }

    func testAboveMusicGapMatchesCenteredLowerCard() {
        for lowerCount in [1, 3, 5, 7] {
            let cardSize = HallOfFameCrownLayout.cardSize(for: lowerCount)
            let lower = HallOfFameCrownLayout.placements(
                count: lowerCount,
                avatarDiameter: 128,
                position: .belowAvatar
            )[lowerCount / 2]
            let music = HallOfFameCrownLayout.aboveMusicPlacement(
                cardSize: CGSize(width: cardSize.width, height: cardSize.width),
                lowerCount: lowerCount,
                avatarDiameter: 128
            )
            let lowerGap = HallOfFameCrownLayout.crownHeight / 2 + lower.y - cardSize.height / 2 - 128
            let musicGap = -(128 / 2 + music.y + cardSize.width / 2)

            XCTAssertEqual(musicGap, lowerGap, accuracy: 0.001)
        }
    }

    func testAvatarTopClearanceIsTheSameForEveryArrangement() {
        XCTAssertEqual(HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 0), 104.3, accuracy: 0.001)
        XCTAssertEqual(HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 0.5), 52.15, accuracy: 0.001)
        XCTAssertEqual(HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 1), 0)
    }

    func testAvatarTopClearanceFitsMusicAboveForEveryEvenCount() {
        for lowerCount in [1, 3, 5, 7] {
            let cardSize = HallOfFameCrownLayout.cardSize(for: lowerCount)
            let needed = cardSize.width + HallOfFameCrownLayout.musicAvatarGap(forLowerCount: lowerCount)

            XCTAssertLessThanOrEqual(needed, HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 0) + 0.001)
        }
    }

    func testProfileHeroCollapseProgressClampsAtRestAndRefreshPull() {
        XCTAssertEqual(ProfileHeroCollapse.progress(for: 0), 0)
        XCTAssertEqual(ProfileHeroCollapse.progress(for: -24), 0)
    }

    func testProfileHeroCollapseProgressUsesLongEasedDistance() {
        XCTAssertEqual(ProfileHeroCollapse.progress(for: 65), 0.15625, accuracy: 0.001)
        XCTAssertEqual(ProfileHeroCollapse.progress(for: 130), 0.5, accuracy: 0.001)
        XCTAssertEqual(ProfileHeroCollapse.progress(for: 260), 1)
        XCTAssertEqual(ProfileHeroCollapse.progress(for: 300), 1)
    }

    func testProfileHeroLayoutCollapsesMoreSlowly() {
        XCTAssertEqual(ProfileHeroCollapse.layoutProgress(for: -24), 0)
        XCTAssertEqual(ProfileHeroCollapse.layoutProgress(for: 170), 0.5, accuracy: 0.001)
        XCTAssertLessThan(ProfileHeroCollapse.layoutProgress(for: 260), 1)
        XCTAssertEqual(ProfileHeroCollapse.layoutProgress(for: 340), 1)
    }

    private func media(index: Int, mediaType: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: index,
                source: "test",
                mediaType: mediaType,
                mediaId: "\(index)",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Favorite \(index)",
            posterOrientation: mediaType == "music" ? .square : .portrait
        )
    }

    private func slots(_ ids: [String]) -> [FavoriteSlot] {
        ids.map { FavoriteSlot(id: $0, title: $0.capitalized, item: nil) }
    }
}
