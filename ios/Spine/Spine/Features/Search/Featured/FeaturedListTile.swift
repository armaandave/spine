import SwiftUI

/// A person's photo in a poster's shape, with their initials when there's no photo.
struct FeaturedPersonPhoto: View {
    let person: PersonListEntry
    let size: CGSize
    let cornerRadius: CGFloat

    var body: some View {
        SpineAsyncImage(url: Self.url(for: person), thumbnailSize: size) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Text(PersonArtwork.initials(for: person.name))
                    .font(.system(size: size.width * 0.27, weight: .bold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
        .frame(width: size.width, height: size.height)
        .background(.white.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        .accessibilityHidden(true)
    }

    static func url(for person: PersonListEntry) -> URL? {
        guard let value = person.profileUrl?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return nil
        }
        return URL(string: value)
    }
}

/// A red dot that pulses while a list is live, and holds still under Reduce Motion.
struct FeaturedLiveDot: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var size: CGFloat = 7

    var body: some View {
        let dot = Circle()
            .fill(Self.color)
            .frame(width: size, height: size)
        if reduceMotion {
            dot
        } else {
            dot.phaseAnimator([false, true]) { view, dimmed in
                view
                    .opacity(dimmed ? 0.35 : 1)
                    .scaleEffect(dimmed ? 0.78 : 1)
            } animation: { _ in
                .easeInOut(duration: 0.75)
            }
        }
    }

    static let color = Color(red: 1.0, green: 0.27, blue: 0.27)
}

/// A tile's one status line: live, its period, or its kind.
struct FeaturedStatusBadge: View {
    let card: FeaturedListCard

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            content(FeaturedBadge.resolve(for: card, now: context.date))
        }
    }

    @ViewBuilder
    private func content(_ badge: FeaturedBadge) -> some View {
        switch badge {
        case let .live(label):
            HStack(spacing: 5) {
                FeaturedLiveDot(size: 6)
                Text(label.isEmpty ? "LIVE" : "LIVE · \(label)")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(FeaturedLiveDot.color)
                    .lineLimit(1)
            }
        case let .period(label):
            Text(label)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.white.opacity(0.62))
                .lineLimit(1)
        case let .kind(kind):
            Text(kind.badgeLabel)
                .font(.system(size: 10, weight: .heavy))
                .tracking(0.6)
                .foregroundStyle(.white.opacity(0.4))
                .lineLimit(1)
        }
    }
}

/// Makes a featured card tappable, the source of the list cover's zoom, and long-pressable for a peek.
struct FeaturedListButton<Label: View>: View {
    @Environment(\.featuredZoomNamespace) private var zoomNamespace
    @Environment(\.openURL) private var openURL

    let card: FeaturedListCard
    let sourceKey: String
    let onSelect: (FeaturedListSelection) -> Void
    @ViewBuilder let label: () -> Label

    var body: some View {
        let selection = FeaturedListSelection(card: card, sourceKey: sourceKey)
        Button {
            onSelect(selection)
        } label: {
            label()
                .contentShape(Rectangle())
                .modifier(FeaturedZoomSource(id: selection.id, namespace: zoomNamespace))
        }
        .buttonStyle(.plain)
        .contextMenu {
            Button("Open list", systemImage: "list.bullet.rectangle") {
                onSelect(selection)
            }
            if let url = card.source?.linkURL {
                Button("View source", systemImage: "safari") {
                    openURL(url)
                }
            }
        } preview: {
            FeaturedListPeek(card: card)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(card.accessibilitySummary(now: .now))
        .accessibilityHint("Opens list")
        .accessibilityAddTraits(.isButton)
        .accessibilityActions {
            // The context menu's "View source", which VoiceOver can't reach on an element that ignores its children.
            if let url = card.source?.linkURL {
                Button("View source") {
                    openURL(url)
                }
            }
        }
    }
}

private struct FeaturedZoomSource: ViewModifier {
    let id: String
    let namespace: Namespace.ID?

    func body(content: Content) -> some View {
        if let namespace {
            content.matchedTransitionSource(id: id, in: namespace)
        } else {
            content
        }
    }
}

/// The long-press preview, from data already loaded: the five posters, the description, source and count.
struct FeaturedListPeek: View {
    /// The peek is 334 pt wide with 16 pt padding: 302 pt holds five 54 pt artworks 8 pt apart.
    static let width: CGFloat = 334
    static let squareSide = PosterSlot.searchRow.size.width
    static let maxPosters = 5

    let card: FeaturedListCard

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .bottom, spacing: 8) {
                ForEach(Array(card.stackPeople.prefix(Self.maxPosters).enumerated()), id: \.offset) { _, person in
                    FeaturedPersonPhoto(
                        person: person,
                        size: PosterSlot.searchRow.artworkSize(mediaType: card.singleMediaType, orientation: nil),
                        cornerRadius: PosterSlot.searchRow.cornerRadius
                    )
                }
                ForEach(Array(card.previews.prefix(Self.maxPosters).enumerated()), id: \.offset) { _, preview in
                    MediaArtwork(
                        url: preview.media.displayPosterURL,
                        title: preview.media.title,
                        slot: card.usesSquareArtwork ? .featuredSquare : .searchRow,
                        mediaType: preview.media.ref.mediaType,
                        orientation: card.usesSquareArtwork ? .square : nil
                    )
                    // The tile's 60 pt album squares, scaled to the 54 pt poster width so five fit the peek; the
                    // request stays the tile's, so both share one thumbnail.
                    .scaleEffect(card.usesSquareArtwork ? Self.squareSide / PosterSlot.featuredSquare.size.width : 1)
                    .frame(
                        width: card.usesSquareArtwork ? Self.squareSide : nil,
                        height: card.usesSquareArtwork ? Self.squareSide : nil
                    )
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    FeaturedStatusBadge(card: card)
                    Spacer(minLength: 4)
                    FeaturedMediaTypeIcons(mediaTypes: card.mediaTypes, size: 10)
                        .opacity(0.6)
                }

                Text(card.name)
                    .font(.system(size: 17, weight: .bold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }

            if !card.description.isEmpty {
                Text(card.description)
                    .font(.system(size: 13))
                    .foregroundStyle(.white.opacity(0.72))
                    .lineLimit(4)
            }

            Text(peekFooter)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.5))
                .lineLimit(1)
        }
        .padding(16)
        .frame(width: Self.width, alignment: .leading)
        .background(Color(white: 0.09))
        .environment(\.colorScheme, .dark)
    }

    /// "Netflix · 10 films"
    private var peekFooter: String {
        [card.source?.name, card.countText]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }
}
