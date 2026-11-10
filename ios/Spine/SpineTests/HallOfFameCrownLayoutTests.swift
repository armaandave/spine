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

    func testEveryCrownIsACenteredSliceOfTheFullOne() {
        let full = HallOfFameCrownLayout.placements(count: 7, avatarDiameter: 128, position: .belowAvatar)

        for count in 1...7 {
            let placements = HallOfFameCrownLayout.placements(count: count, avatarDiameter: 128, position: .belowAvatar)
            let start = (7 - count) / 2
            for (index, placement) in placements.enumerated() {
                let offset = Double(index) - Double(count - 1) / 2
                // Same angle between neighbors as the full crown, centered on the avatar.
                XCTAssertEqual(placement.rotation.degrees, offset * 26 * -0.58, accuracy: 0.001)
                XCTAssertEqual(placement.x, -placements[count - 1 - index].x, accuracy: 0.001)
                if count.isMultiple(of: 2) == false {
                    // Lifted toward the avatar, otherwise the full crown's own cards.
                    let lift = HallOfFameCrownLayout.avatarLift(forCount: count)
                    XCTAssertEqual(placement, HallOfFameCrownPlacement(
                        index: index, x: full[start + index].x, y: full[start + index].y - lift,
                        rotation: full[start + index].rotation, scale: full[start + index].scale,
                        zIndex: full[start + index].zIndex
                    ))
                }
            }
        }
    }

    func testSmallCrownsSitCloserToTheAvatar() {
        let lifts = (1...7).map { HallOfFameCrownLayout.avatarLift(forCount: $0) }

        XCTAssertEqual(lifts, [29, 29, 29, 21.75, 14.5, 7.25, 0])
    }

    func testCrownLayoutShowsEightSlots() {
        let placements = HallOfFameCrownLayout.placements(
            count: 8,
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 8)
    }

    func testFavoriteSlotsKeepCrownOrderAndHideTurnedOffTypes() {
        let book = media(index: 1, mediaType: "book")
        let movie = media(index: 2, mediaType: "movie")
        let game = media(index: 3, mediaType: "game")
        let otherMovie = media(index: 4, mediaType: "movie")

        let slots = ProfileFavorites.slots(
            from: [book, movie, game, otherMovie],
            enabledMediaTypes: ["movie", "book"]
        )

        XCTAssertEqual(slots.map(\.item?.id), [book.id, movie.id, otherMovie.id])
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

    func testCrownArrangementLiftsTheMiddleCardOfAnEvenCount() {
        for ids in [["movie"], ["movie", "tv", "music"], ["movie", "tv", "anime", "manga", "book"]] {
            let arrangement = HallOfFameCrownLayout.arrangement(for: slots(ids))
            XCTAssertNil(arrangement.above)
            XCTAssertEqual(arrangement.below.map(\.id), ids)
        }

        let two = HallOfFameCrownLayout.arrangement(for: slots(["movie", "tv"]))
        XCTAssertEqual(two.above?.id, "tv")
        XCTAssertEqual(two.below.map(\.id), ["movie"])

        let eight = HallOfFameCrownLayout.arrangement(for: slots(["movie", "tv", "anime", "manga", "music", "game", "book", "comic"]))
        XCTAssertEqual(eight.above?.id, "music")
        XCTAssertEqual(eight.below.map(\.id), ["movie", "tv", "anime", "manga", "game", "book", "comic"])
    }

    func testMusicCardsAreSquare() {
        let cardSize = CGSize(width: 70, height: 105)

        XCTAssertEqual(HallOfFameCrownLayout.visualSize(for: slots(["music"])[0], cardSize: cardSize), CGSize(width: 70, height: 70))
        XCTAssertEqual(HallOfFameCrownLayout.visualSize(for: slots(["movie"])[0], cardSize: cardSize), cardSize)
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
        XCTAssertEqual(expanded.y, -110.3, accuracy: 0.001)
        XCTAssertEqual(expanded.rotation, .zero)
        XCTAssertEqual(expanded.scale, 1.04, accuracy: 0.001)
        XCTAssertEqual(halfway.y, -55.15, accuracy: 0.001)
        XCTAssertEqual(collapsed.y, 0, accuracy: 0.001)
        XCTAssertEqual(collapsed.scale, 0.28, accuracy: 0.001)
    }

    func testAboveMusicGapMatchesCenteredLowerCard() {
        let cardSize = HallOfFameCrownLayout.cardSize
        for lowerCount in [1, 3, 5, 7] {
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
        XCTAssertEqual(HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 0), 99.3, accuracy: 0.001)
        XCTAssertEqual(HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 0.5), 49.65, accuracy: 0.001)
        XCTAssertEqual(HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 1), 0)
    }

    func testLowestCardEdgeRisesWithTheLift() {
        // Odd rows hang one upright card straight down the middle, lifted for smaller crowns.
        let ids = ["movie", "tv", "anime", "manga", "game", "book", "comic"]

        for count in [1, 3, 5, 7] {
            XCTAssertEqual(
                HallOfFameCrownLayout.lowestCardEdge(for: slots(Array(ids.prefix(count)))),
                257.54 - HallOfFameCrownLayout.avatarLift(forCount: count),
                accuracy: 0.001
            )
        }
    }

    func testLowestCardEdgeUsesTheSquareMusicCardInTheMiddle() {
        // Music takes the middle of an odd row, and its square card is shorter than a poster.
        let withMusic = HallOfFameCrownLayout.lowestCardEdge(for: slots(["movie", "music", "tv"]))
        let withoutMusic = HallOfFameCrownLayout.lowestCardEdge(for: slots(["movie", "book", "tv"]))

        XCTAssertLessThan(withMusic, withoutMusic)
    }

    func testNameKeepsTheSameDistanceFromTheLowestCard() {
        let fullCrown = slots(["movie", "tv", "anime", "manga", "game", "book", "comic", "music"])
        let threeBelow = slots(["movie", "tv", "book", "music"])
        let twoBelow = slots(["movie", "tv"])

        // A full crown is the baseline, and its name stays where it was.
        XCTAssertEqual(HallOfFameCrownLayout.nameOffset(for: fullCrown, collapseProgress: 0), 0, accuracy: 0.001)
        XCTAssertEqual(HallOfFameCrownLayout.nameOffset(for: threeBelow, collapseProgress: 0), -29, accuracy: 0.001)
        XCTAssertLessThan(HallOfFameCrownLayout.nameOffset(for: twoBelow, collapseProgress: 0), 0)

        for favorites in [fullCrown, threeBelow, twoBelow] {
            XCTAssertEqual(
                HallOfFameCrownLayout.lowestCardEdge(for: favorites)
                    - HallOfFameCrownLayout.nameOffset(for: favorites, collapseProgress: 0),
                257.54,
                accuracy: 0.001
            )
        }
    }

    func testNameOffsetFadesOutAsTheHeroCollapses() {
        let twoBelow = slots(["movie", "tv"])
        let expanded = HallOfFameCrownLayout.nameOffset(for: twoBelow, collapseProgress: 0)

        XCTAssertEqual(HallOfFameCrownLayout.nameOffset(for: twoBelow, collapseProgress: 0.5), expanded / 2, accuracy: 0.001)
        XCTAssertEqual(HallOfFameCrownLayout.nameOffset(for: twoBelow, collapseProgress: 1), 0, accuracy: 0.001)
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
        ids.enumerated().map { index, id in
            FavoriteSlot(id: id, title: id.capitalized, item: media(index: index, mediaType: id))
        }
    }
}
