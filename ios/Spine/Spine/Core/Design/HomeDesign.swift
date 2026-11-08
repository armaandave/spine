import SwiftUI

/// Every number, color, font and motion curve of the Home redesign. Views read these and hard-code none.
nonisolated enum HomeDesign {
    // MARK: Metric

    nonisolated enum Metric {
        static let pageMargin: CGFloat = 16
        static let tileGap: CGFloat = 12
        static let tileOpenHeight: CGFloat = 410
        /// A closed tile is as tall as its fan, its title and its count need, with a 10 pt margin under the count's
        /// line (about 13 under its letters). It is no longer square: the square left a band of nothing under the text.
        static let tileClosedHeight: CGFloat = 164
        static let tileCorner: CGFloat = 30

        /// 30% shorter than the mock's 164.
        static let cardHeight: CGFloat = 115
        static let cardCorner: CGFloat = 20
        static let cardPoster = CGSize(width: 60, height: 90)
        static let cardSquare = CGSize(width: 90, height: 90)
        /// The poster's (or square art's) left and top inset.
        static let cardInset: CGFloat = 12
        /// Where the text starts when the art is a poster (square art: `cardTextLeadingSquare`): inset, art, 14.
        static let cardTextLeading: CGFloat = 86
        static let cardTextLeadingSquare: CGFloat = 116
        static let cardTrailing: CGFloat = 14
        static let cardGap: CGFloat = 10
        static let cardTextTop: CGFloat = 9
        /// The meter's drawing ends this far above the card's bottom, level with the counts.
        static let meterBottom: CGFloat = 15
        /// A window meter's series line ends this far above the card's bottom.
        static let meterLineBottom: CGFloat = 17
        static let countsBottom: CGFloat = 12

        static let glowHeight: CGFloat = 440

        static let carouselPoster = CGSize(width: 120, height: 180)
        static let carouselStep: CGFloat = 134
        static let carouselTop: CGFloat = 70
        static let carouselRestScale: CGFloat = 0.84
        static let carouselRestBrightness: Double = 0.5
        static let posterRadius: CGFloat = 12

        /// Two tiles side by side across the content width: 173 at 358.
        static func tileSide(contentWidth: CGFloat) -> CGFloat {
            floor((contentWidth - tileGap) / 2)
        }

        /// One of the five posters fanned over a closed tile. `x` and `y` are the poster's top-left inside
        /// the tile, `degrees` its rotation, `brightness` the multiplier the mock applies (1 is untouched).
        nonisolated struct Fan: Equatable {
            let x: CGFloat
            let y: CGFloat
            let width: CGFloat
            let height: CGFloat
            let degrees: Double
            let brightness: Double
            let z: Double
            let opacity: Double
        }

        static func fan(index: Int, tileSide: CGFloat) -> Fan {
            let cx = tileSide / 2
            switch index {
            case 0:
                return Fan(x: cx - 33, y: 16, width: 66, height: 99, degrees: 0, brightness: 1, z: 5, opacity: 1)
            case 1:
                return Fan(x: cx - 64, y: 24, width: 56, height: 84, degrees: -11, brightness: 0.62, z: 4, opacity: 1)
            case 2:
                return Fan(x: cx + 8, y: 24, width: 56, height: 84, degrees: 11, brightness: 0.62, z: 3, opacity: 1)
            default:
                return Fan(x: cx - 28, y: 22, width: 56, height: 84, degrees: 0, brightness: 0.5, z: 1, opacity: 0)
            }
        }
    }

    // MARK: Palette

    nonisolated enum Palette {
        static let starGold = Color(red: 0xFF / 255, green: 0xD4 / 255, blue: 0x5C / 255)
        static let heart = Color(red: 0xFF / 255, green: 0x6B / 255, blue: 0x8A / 255)
        /// The rewatch glyph on dark-ink cards, and the deeper green that still reads on light-ink cards.
        static let rewatchGreen = Color(red: 0x4A / 255, green: 0xDE / 255, blue: 0x80 / 255)
        static let rewatchGreenDeep = Color(red: 0x0F / 255, green: 0x7A / 255, blue: 0x3B / 255)
        /// Text on light art.
        static let lightInk = Color(red: 0x0D / 255, green: 0x1A / 255, blue: 0x1F / 255)
        /// Initials on an avatar disc.
        static let avatarInk = Color(red: 0x1B / 255, green: 0x1B / 255, blue: 0x1B / 255)
        static let pageBackground = Color.black
        /// WCAG relative luminance of a poster's accent color at or above which its card gets dark text.
        static let lightInkMinLuminance: Double = 0.40
    }

    // MARK: Text

    nonisolated enum Text {
        static let username = Font.system(size: 13.5, weight: .bold)
        static let title = Font.system(size: 17, weight: .semibold)
        static let tag = Font.system(size: 15, weight: .bold)
        static let headline = Font.system(size: 15)
        static let review = Font.system(size: 14)
        static let quiet = Font.system(size: 13.5, weight: .semibold)
        static let counts = Font.system(size: 12.5, weight: .bold)
        static let pill = Font.system(size: 12.5, weight: .bold)
        /// The day after a card's username.
        static let day = Font.system(size: 12, weight: .medium)
        static let tileTitle = Font.system(size: 16, weight: .heavy)
        static let tileTitleOpen = Font.system(size: 24, weight: .heavy)
        static let tileSubtitle = Font.system(size: 12, weight: .semibold)
        static let panelTitle = Font.system(size: 19, weight: .heavy)

        /// Every fixed-size row clamps its Dynamic Type with this.
        static let maxCategory = DynamicTypeSize.xxLarge
    }

    // MARK: Motion

    nonisolated enum Motion {
        /// The design's cubic-bezier(.25, 1, .35, 1).
        static let softCurve = (c1x: 0.25, c1y: 1.0, c2x: 0.35, c2y: 1.0)
        /// The design's cubic-bezier(.3, 1.2, .4, 1).
        static let springCurve = (c1x: 0.3, c1y: 1.2, c2x: 0.4, c2y: 1.0)

        // Durations in seconds, as plain numbers.
        static let tileHeightDuration = 0.62
        static let tileWidthDuration = 0.62
        static let transformDuration = 0.45
        static let panelRiseDuration = 0.5
        static let springDuration = 0.7
        static let glowCrossfadeDuration = 0.7
        /// What every animation becomes when Reduce Motion is on.
        static let reducedDuration = 0.1

        static let openStaggerMs = 45
        static let closeStaggerMs = 22

        static func soft(duration: Double, reduceMotion: Bool) -> Animation {
            guard !reduceMotion else { return reduced }
            return .timingCurve(softCurve.c1x, softCurve.c1y, softCurve.c2x, softCurve.c2y, duration: duration)
        }

        static func tileHeight(reduceMotion: Bool) -> Animation {
            soft(duration: tileHeightDuration, reduceMotion: reduceMotion)
        }

        static func tileWidth(reduceMotion: Bool) -> Animation {
            soft(duration: tileWidthDuration, reduceMotion: reduceMotion)
        }

        static func transform(reduceMotion: Bool) -> Animation {
            soft(duration: transformDuration, reduceMotion: reduceMotion)
        }

        static func panelRise(reduceMotion: Bool) -> Animation {
            soft(duration: panelRiseDuration, reduceMotion: reduceMotion)
        }

        static func spring(reduceMotion: Bool) -> Animation {
            guard !reduceMotion else { return reduced }
            return .timingCurve(
                springCurve.c1x, springCurve.c1y, springCurve.c2x, springCurve.c2y,
                duration: springDuration
            )
        }

        /// The small bounce on a checked tick or a pressed control.
        static func pop(reduceMotion: Bool) -> Animation {
            guard !reduceMotion else { return reduced }
            return .spring(response: 0.4, dampingFraction: 0.6)
        }

        static func glowCrossfade(reduceMotion: Bool) -> Animation {
            guard !reduceMotion else { return reduced }
            return .easeInOut(duration: glowCrossfadeDuration)
        }

        static var reduced: Animation {
            .easeOut(duration: reducedDuration)
        }

        /// Delay in seconds before poster `index` moves when a tile opens.
        static func openStagger(index: Int) -> Double {
            Double(index * openStaggerMs) / 1000
        }

        /// Delay in seconds before poster `index` of `count` moves when a tile closes: the last goes first.
        static func closeStagger(index: Int, count: Int) -> Double {
            Double(max(count - 1 - index, 0) * closeStaggerMs) / 1000
        }
    }
}
