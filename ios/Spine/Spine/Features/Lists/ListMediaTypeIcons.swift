import SwiftUI

/// What a list holds: its one media type's icon, the globe when it mixes types, and a people icon for a people list.
struct ListMediaTypeIcons: View {
    let mediaTypes: [String]
    var isPeople = false
    var size: CGFloat = 11

    var body: some View {
        if isPeople {
            // The count beside it already says "people".
            Image(systemName: "person.2.fill")
                .font(.system(size: size, weight: .semibold))
                .foregroundStyle(.white)
                .accessibilityHidden(true)
        } else if !mediaTypes.isEmpty {
            HStack(spacing: size * 0.45) {
                ForEach(Self.shownTypes(mediaTypes), id: \.self) { mediaType in
                    MediaTypeGlyph(theme: MediaTypeTheme.theme(for: mediaType), size: size)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(Self.names(mediaTypes))
        }
    }

    /// One icon only: the type's own when the whole list is that type, else the globe.
    static func shownTypes(_ mediaTypes: [String]) -> [String] {
        mediaTypes.count > 1 ? [APIConstants.allMedia] : mediaTypes
    }

    /// "Movies and TV".
    static func names(_ mediaTypes: [String]) -> String {
        ListFormatter.localizedString(byJoining: mediaTypes.map { MediaTypeTheme.theme(for: $0).displayName })
    }
}

extension CustomListSummary {
    /// "Movies and TV", or nil when the list holds no media.
    var mediaTypesLabel: String? {
        mediaTypes.isEmpty ? nil : ListMediaTypeIcons.names(mediaTypes)
    }
}

/// A list's count in plain words: "24 films", "18 books", "1 person". A list of one media type names it; a mix (or a
/// list too new to have any) says "titles".
enum ListCountWords {
    static func text(count: Int, mediaTypes: [String], isPeople: Bool) -> String {
        "\(count.formatted()) \(noun(count: count, mediaTypes: mediaTypes, isPeople: isPeople))"
    }

    static func noun(count: Int, mediaTypes: [String], isPeople: Bool) -> String {
        let one = count == 1
        if isPeople { return one ? "person" : "people" }
        guard mediaTypes.count == 1, let mediaType = mediaTypes.first else { return one ? "title" : "titles" }
        switch mediaType {
        case "movie": return one ? "film" : "films"
        case "tv", "season", "episode": return one ? "show" : "shows"
        case "book": return one ? "book" : "books"
        case "comic": return one ? "comic" : "comics"
        case "game": return one ? "game" : "games"
        case "music": return one ? "album" : "albums"
        case "anime": return "anime"
        case "manga": return "manga"
        default: return one ? "title" : "titles"
        }
    }
}
