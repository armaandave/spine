import Nuke
import SwiftUI

/// Five overlapping posters: the lead at the front left, the rest stepping back behind it, darker and smaller,
/// bottoms aligned. A people list stacks its people's photos the same way.
struct FeaturedPosterStack: View {
    enum Style {
        /// Landing tiles: 54×81 posters (music 60×60) across 148 pt.
        case tile
        /// See-all tiles: 64×96 posters (music 64×64).
        case medium(width: CGFloat)

        var width: CGFloat {
            switch self {
            case .tile: 148
            case let .medium(width): width
            }
        }

        func slot(squareArtwork: Bool) -> PosterSlot {
            switch self {
            case .tile: squareArtwork ? .featuredSquare : .searchRow
            case .medium: .hofCrown
            }
        }
    }

    static let maxPosters = 5

    let card: FeaturedListCard
    var style: Style = .tile

    var body: some View {
        let slot = style.slot(squareArtwork: card.usesSquareArtwork)
        let posterSize = slot.artworkSize(mediaType: card.singleMediaType, orientation: nil)
        let previews = Array(card.previews.prefix(Self.maxPosters))
        let people = Array(card.stackPeople.prefix(Self.maxPosters))
        let step = (style.width - posterSize.width) / CGFloat(Self.maxPosters - 1)

        ZStack(alignment: .bottomLeading) {
            if previews.isEmpty, people.isEmpty {
                MediaArtwork(url: nil, title: card.name, slot: slot, mediaType: card.singleMediaType ?? APIConstants.allMedia)
            }
            ForEach(Array(previews.enumerated()), id: \.offset) { index, preview in
                stacked(poster(preview.media, slot: slot), slot: slot, index: index, step: step)
            }
            ForEach(Array(people.enumerated()), id: \.offset) { index, person in
                stacked(
                    FeaturedPersonPhoto(person: person, size: posterSize, cornerRadius: slot.cornerRadius),
                    slot: slot,
                    index: index,
                    step: step
                )
            }
        }
        .frame(width: style.width, height: posterSize.height, alignment: .bottomLeading)
        .accessibilityHidden(true)
    }

    private func poster(_ media: MediaSummary, slot: PosterSlot) -> some View {
        MediaArtwork(
            url: media.displayPosterURL,
            title: media.title,
            slot: slot,
            mediaType: media.ref.mediaType,
            orientation: card.usesSquareArtwork ? .square : nil
        )
    }

    /// Places one artwork in the stack: further back is darker, smaller and further right.
    private func stacked(_ artwork: some View, slot: PosterSlot, index: Int, step: CGFloat) -> some View {
        let depth = CGFloat(index) / CGFloat(Self.maxPosters - 1)
        return artwork
            .overlay {
                // Brightness 1.0 at the front to 0.55 at the back.
                RoundedRectangle(cornerRadius: slot.cornerRadius, style: .continuous)
                    .fill(.black.opacity(0.45 * depth))
            }
            .shadow(color: .black.opacity(0.32), radius: 4, x: 2, y: 2)
            .scaleEffect(1 - 0.08 * depth, anchor: .bottom)
            .overlay(alignment: .topLeading) {
                if index == 0, let rank = card.rank(at: 0) {
                    FeaturedRankChip(rank: rank)
                        .padding(4)
                }
            }
            .offset(x: CGFloat(index) * step)
            .zIndex(Double(-index))
    }
}

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

extension FeaturedListCard {
    /// The artwork requests a tile's poster stack makes, for prefetching.
    func tileArtworkRequests() -> [ImageRequest] {
        let slot = FeaturedPosterStack.Style.tile.slot(squareArtwork: usesSquareArtwork)
        let posters = previews.prefix(FeaturedPosterStack.maxPosters).compactMap { preview in
            MediaArtwork.imageRequest(
                url: preview.media.displayPosterURL,
                slot: slot,
                mediaType: preview.media.ref.mediaType,
                orientation: usesSquareArtwork ? .square : nil
            )
        }
        let photoSize = slot.artworkSize(mediaType: singleMediaType, orientation: nil)
        let photos = stackPeople.prefix(FeaturedPosterStack.maxPosters).compactMap { person in
            SpineImagePipeline.request(url: FeaturedPersonPhoto.url(for: person), thumbnailSize: photoSize)
        }
        return posters + photos
    }
}

/// The people grid's rank chip, smaller: black on a white capsule.
struct FeaturedRankChip: View {
    let rank: Int

    var body: some View {
        Text("\(rank)")
            .font(.system(size: 11, weight: .heavy, design: .rounded))
            .foregroundStyle(.black)
            .monospacedDigit()
            .padding(.horizontal, 5)
            .frame(minWidth: 18)
            .frame(height: 18)
            .background(.white, in: Capsule())
            .accessibilityLabel("Number \(rank)")
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

/// A tile's one status line: live, recently updated, its period, or its kind.
struct FeaturedStatusBadge: View {
    let card: FeaturedListCard

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            content(FeaturedBadge.resolve(for: card, now: context.date), now: context.date)
        }
    }

    @ViewBuilder
    private func content(_ badge: FeaturedBadge, now: Date) -> some View {
        switch badge {
        case let .live(label):
            HStack(spacing: 5) {
                FeaturedLiveDot(size: 6)
                Text(label.isEmpty ? "LIVE" : "LIVE · \(label)")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(FeaturedLiveDot.color)
                    .lineLimit(1)
            }
        case let .updated(date):
            Text(FeaturedRelativeTime.updatedLabel(from: date, now: now))
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(card.accentColor)
                .lineLimit(1)
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

/// A landing tile, about 148×162 pt.
struct FeaturedListTile: View {
    let card: FeaturedListCard
    var showsMediaTypes = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            FeaturedPosterStack(card: card)

            HStack(spacing: 6) {
                FeaturedStatusBadge(card: card)
                Spacer(minLength: 4)
                if showsMediaTypes {
                    FeaturedMediaTypeIcons(mediaTypes: card.mediaTypes, size: 9)
                        .opacity(0.6)
                }
            }
            .frame(height: 14)
            .padding(.top, 9)

            Text(card.name)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .multilineTextAlignment(.leading)
                .lineLimit(2, reservesSpace: true)
                .padding(.top, 4)

            Text(card.countLabel)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.white.opacity(0.5))
                .lineLimit(1)
                .padding(.top, 2)
        }
        .frame(width: FeaturedPosterStack.Style.tile.width, alignment: .leading)
    }
}

/// A see-all tile: bigger posters and a line of description.
struct FeaturedMediumTile: View {
    let card: FeaturedListCard
    let width: CGFloat
    var showsMediaTypes = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            FeaturedPosterStack(card: card, style: .medium(width: width))

            HStack(spacing: 6) {
                FeaturedStatusBadge(card: card)
                Spacer(minLength: 4)
                if showsMediaTypes {
                    FeaturedMediaTypeIcons(mediaTypes: card.mediaTypes, size: 9)
                        .opacity(0.6)
                }
            }
            .frame(height: 14)
            .padding(.top, 9)

            Text(card.name)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .multilineTextAlignment(.leading)
                .lineLimit(2, reservesSpace: true)
                .padding(.top, 4)

            Text(card.description.isEmpty ? " " : card.description)
                .font(.system(size: 12))
                .foregroundStyle(.white.opacity(0.55))
                .lineLimit(1)
                .padding(.top, 2)

            Text(card.countLabel)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.white.opacity(0.5))
                .lineLimit(1)
                .padding(.top, 2)
        }
        .frame(width: width, alignment: .leading)
    }
}

/// The end of a shelf: "See all N".
struct FeaturedSeeAllTile: View {
    let count: Int

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "arrow.right")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 36, height: 36)
                .background(.white.opacity(0.1), in: Circle())

            Text("See all \(count.formatted())")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.7))
        }
        .frame(width: 104, height: PosterSlot.searchRow.size.height)
        .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(0.045), lineWidth: 1)
        }
        .accessibilityElement(children: .combine)
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
        .accessibilityLabel(card.accessibilitySummary)
        .accessibilityHint("Opens list")
        .accessibilityAddTraits(.isButton)
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

/// The long-press preview, from data already loaded: the five posters with ranks, the description, source,
/// cadence and count.
struct FeaturedListPeek: View {
    let card: FeaturedListCard

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .bottom, spacing: 8) {
                ForEach(Array(card.stackPeople.prefix(FeaturedPosterStack.maxPosters).enumerated()), id: \.offset) { index, person in
                    FeaturedPersonPhoto(
                        person: person,
                        size: PosterSlot.searchRow.artworkSize(mediaType: card.singleMediaType, orientation: nil),
                        cornerRadius: PosterSlot.searchRow.cornerRadius
                    )
                    .overlay(alignment: .topLeading) {
                        if let rank = card.rank(at: index) {
                            FeaturedRankChip(rank: rank)
                                .padding(4)
                        }
                    }
                }
                ForEach(Array(card.previews.prefix(FeaturedPosterStack.maxPosters).enumerated()), id: \.offset) { index, preview in
                    MediaArtwork(
                        url: preview.media.displayPosterURL,
                        title: preview.media.title,
                        slot: card.usesSquareArtwork ? .featuredSquare : .searchRow,
                        mediaType: preview.media.ref.mediaType,
                        orientation: card.usesSquareArtwork ? .square : nil
                    )
                    .overlay(alignment: .topLeading) {
                        if let rank = card.rank(at: index) {
                            FeaturedRankChip(rank: rank)
                                .padding(4)
                        }
                    }
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
        .frame(width: 334, alignment: .leading)
        .background(Color(white: 0.09))
        .environment(\.colorScheme, .dark)
    }

    /// "Netflix · Weekly · 10 films"
    private var peekFooter: String {
        let count = "\(card.itemsCount.formatted()) \(FeaturedCountNoun.noun(for: card.mediaTypes, count: card.itemsCount))"
        return [card.source?.name, card.cadence.label, count]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }
}
