import SwiftUI

/// A see-all chart row across the full width: the top three posters inline, then the list.
struct FeaturedChartRow: View {
    let card: FeaturedListCard

    var body: some View {
        ProfileListRowContainer {
            content
        }
    }

    private var content: some View {
        HStack(spacing: 12) {
            HStack(spacing: 6) {
                // By position, not id: a list can hold the same title more than once.
                ForEach(Array(card.previews.prefix(3).enumerated()), id: \.offset) { _, preview in
                    MediaArtwork(
                        url: preview.media.displayPosterURL,
                        title: preview.media.title,
                        slot: card.usesSquareArtwork ? .featuredSquare : .searchRow,
                        mediaType: preview.media.ref.mediaType,
                        orientation: card.usesSquareArtwork ? .square : nil
                    )
                }
            }
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    FeaturedStatusBadge(card: card)
                    Spacer(minLength: 4)
                    FeaturedMediaTypeIcons(mediaTypes: card.mediaTypes, size: 9)
                        .opacity(0.6)
                }

                Text(card.name)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)

                Text([card.source?.name, card.countText].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.white.opacity(0.5))
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
        }
    }
}
