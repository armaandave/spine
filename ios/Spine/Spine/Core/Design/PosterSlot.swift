import SwiftUI

enum PosterSlot {
    case searchRow
    case libraryRow
    case diaryRow
    /// The small poster in a diary entry row.
    case diaryEntry
    case profileRow
    case profileRail
    case listPreview
    case hero
    case carousel
    case seasonCard
    case logSheet
    case pickerGrid
    case tagGrid
    case episodeStill
    case hofCrown
    /// Square album art on Search's featured tiles.
    case featuredSquare
    /// Stats' Top from a tagged list: two rows of five on the narrowest phone.
    case statsTopGrid
    /// A post in the activity feed; its text is as tall as this.
    case activityPost
    /// A card in the Home feed; square art takes 132x132.
    case feedCard
    /// A poster in an open Home tile's carousel.
    case homeCarousel
    /// The poster on a post page's header.
    case postHero

    var size: CGSize {
        switch self {
        case .searchRow:
            CGSize(width: 54, height: 81)
        case .libraryRow, .diaryRow, .profileRow:
            CGSize(width: 56, height: 84)
        case .diaryEntry:
            CGSize(width: 42, height: 63)
        case .profileRail:
            CGSize(width: 70, height: 105)
        case .listPreview:
            CGSize(width: 45, height: 67)
        case .hero:
            CGSize(width: 191, height: 286.5)
        case .carousel:
            CGSize(width: 100, height: 150)
        case .seasonCard:
            CGSize(width: 90, height: 135)
        case .logSheet:
            CGSize(width: 106, height: 159)
        case .pickerGrid:
            CGSize(width: 104, height: 156)
        case .tagGrid:
            CGSize(width: 80, height: 120)
        case .episodeStill:
            CGSize(width: 72, height: 42)
        case .hofCrown:
            CGSize(width: 64, height: 96)
        case .featuredSquare:
            CGSize(width: 60, height: 60)
        case .statsTopGrid:
            CGSize(width: 62, height: 93)
        case .activityPost:
            CGSize(width: 74, height: 111)
        case .feedCard:
            CGSize(width: 88, height: 132)
        case .homeCarousel, .postHero:
            CGSize(width: 120, height: 180)
        }
    }

    var cornerRadius: CGFloat {
        switch self {
        case .episodeStill:
            4
        case .diaryEntry:
            6
        case .hero, .logSheet, .postHero:
            10
        case .homeCarousel:
            12
        default:
            8
        }
    }

    func artworkSize(mediaType: String?, orientation: PosterOrientation?) -> CGSize {
        let usesSquareArtwork = orientation == .square
            || ((orientation == nil || orientation == .unknown)
                && MediaTypeTheme.theme(for: mediaType ?? "unknown").artworkOrientation == .square)
        guard usesSquareArtwork else { return size }
        if self == .feedCard {
            return CGSize(width: 132, height: 132)
        }
        return CGSize(width: size.width, height: size.width)
    }
}

enum PosterContentMode {
    case fill
    case fit
}
