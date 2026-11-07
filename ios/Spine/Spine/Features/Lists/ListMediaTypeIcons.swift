import SwiftUI

/// What a list holds: one media type's icon, both icons for two, a globe for more, and a people icon for a people list.
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
                ForEach(shownTypes, id: \.self) { mediaType in
                    MediaTypeGlyph(theme: MediaTypeTheme.theme(for: mediaType), size: size)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(Self.names(mediaTypes))
        }
    }

    private var shownTypes: [String] {
        mediaTypes.count > 2 ? [APIConstants.allMedia] : mediaTypes
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
