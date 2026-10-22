import SwiftUI

/// A "Now" strip card, 300 pt wide: the list's top three as text rows, its status and its source.
struct FeaturedChartCard: View {
    static let width: CGFloat = 300

    let card: FeaturedListCard

    var body: some View {
        ProfileListRowContainer {
            content
        }
        .frame(width: Self.width)
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                FeaturedNowBadge(card: card)

                if let source = card.source?.name, !source.isEmpty {
                    Text(source)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.5))
                        .lineLimit(1)
                }

                Spacer(minLength: 4)

                FeaturedMediaTypeIcons(mediaTypes: card.mediaTypes, size: 9)
                    .opacity(0.6)
            }
            .frame(height: 14)

            Text(card.name)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .lineLimit(1)
                .padding(.top, 4)

            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(card.previews.prefix(3).enumerated()), id: \.offset) { index, preview in
                    FeaturedChartEntryRow(
                        rank: card.rank(at: index) ?? index + 1,
                        preview: preview,
                        squareArtwork: card.usesSquareArtwork
                    )
                }
            }
            .padding(.top, 8)
        }
    }
}

/// One chart entry: rank, a 28×42 poster, title and movement.
private struct FeaturedChartEntryRow: View {
    let rank: Int
    let preview: FeaturedPreview
    let squareArtwork: Bool

    var body: some View {
        HStack(spacing: 10) {
            Text("\(rank)")
                .font(.system(size: 13, weight: .heavy, design: .rounded))
                .foregroundStyle(.white.opacity(0.8))
                .monospacedDigit()
                .frame(width: 18, alignment: .leading)

            // The tile's 54×81 request scaled down, so both share one thumbnail in the image cache.
            MediaArtwork(
                url: preview.media.displayPosterURL,
                title: preview.media.title,
                slot: squareArtwork ? .featuredSquare : .searchRow,
                mediaType: preview.media.ref.mediaType,
                orientation: squareArtwork ? .square : nil
            )
            .scaleEffect(28 / (squareArtwork ? PosterSlot.featuredSquare : PosterSlot.searchRow).size.width)
            .frame(width: 28, height: 42)

            Text(preview.media.displayTitle)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.92))
                .lineLimit(1)

            Spacer(minLength: 4)

            if let movement = FeaturedMovement(note: preview.note) {
                Text(movement.text)
                    .font(.system(size: 11, weight: .heavy, design: .rounded))
                    .foregroundStyle(movement.color)
            }
        }
        .frame(height: 42)
    }
}

/// The Now strip's status: "● LIVE · 7 of 23 announced", or how long ago the chart changed ("● 2h").
private struct FeaturedNowBadge: View {
    let card: FeaturedListCard

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            if let live = card.live, live.isLive(at: context.date) {
                HStack(spacing: 5) {
                    FeaturedLiveDot(size: 6)
                    Text(live.label.isEmpty ? "LIVE" : "LIVE · \(live.label)")
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(FeaturedLiveDot.color)
                        .lineLimit(1)
                }
            } else if let changed = card.contentChangedAt {
                HStack(spacing: 5) {
                    Circle()
                        .fill(card.accentColor)
                        .frame(width: 6, height: 6)
                    Text(FeaturedRelativeTime.short(from: changed, now: context.date))
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(card.accentColor)
                }
            } else {
                FeaturedStatusBadge(card: card)
            }
        }
    }
}

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
                ForEach(Array(card.previews.prefix(3).enumerated()), id: \.offset) { index, preview in
                    MediaArtwork(
                        url: preview.media.displayPosterURL,
                        title: preview.media.title,
                        slot: card.usesSquareArtwork ? .featuredSquare : .searchRow,
                        mediaType: preview.media.ref.mediaType,
                        orientation: card.usesSquareArtwork ? .square : nil
                    )
                    .overlay(alignment: .topLeading) {
                        FeaturedRankChip(rank: card.rank(at: index) ?? index + 1)
                            .padding(3)
                    }
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

                Text([card.source?.name, card.countLabel].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.white.opacity(0.5))
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
        }
    }
}
