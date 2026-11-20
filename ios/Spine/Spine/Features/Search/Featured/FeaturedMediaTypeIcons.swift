import SwiftUI

/// What a featured list holds: its one media type's glyph, or the globe when it mixes types (the rule
/// `ListMediaTypeIcons` uses for every list).
struct FeaturedMediaTypeIcons: View {
    let mediaTypes: [String]
    var size: CGFloat = 10

    var body: some View {
        if !mediaTypes.isEmpty {
            HStack(spacing: size * 0.45) {
                ForEach(ListMediaTypeIcons.shownTypes(mediaTypes), id: \.self) { mediaType in
                    MediaTypeGlyph(theme: MediaTypeTheme.theme(for: mediaType), size: size)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(ListFormatter.localizedString(
                byJoining: mediaTypes.map { MediaTypeTheme.theme(for: $0).displayName }
            ))
        }
    }
}
