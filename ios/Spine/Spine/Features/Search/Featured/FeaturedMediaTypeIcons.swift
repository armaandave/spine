import SwiftUI

/// What a featured list holds: one media type's glyph, both glyphs for two, a globe for more.
///
/// The `list-media-types` branch adds `ListMediaTypeIcons` for all lists; once both land, this can become it.
struct FeaturedMediaTypeIcons: View {
    let mediaTypes: [String]
    var size: CGFloat = 10

    var body: some View {
        if !mediaTypes.isEmpty {
            HStack(spacing: size * 0.45) {
                ForEach(shownTypes, id: \.self) { mediaType in
                    MediaTypeGlyph(theme: MediaTypeTheme.theme(for: mediaType), size: size)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(ListFormatter.localizedString(
                byJoining: mediaTypes.map { MediaTypeTheme.theme(for: $0).displayName }
            ))
        }
    }

    private var shownTypes: [String] {
        mediaTypes.count > 2 ? [APIConstants.allMedia] : mediaTypes
    }
}
