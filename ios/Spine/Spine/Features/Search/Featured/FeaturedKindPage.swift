import SwiftUI

/// A type's page header: a Liquid Glass plate with "Books · 64 lists", the kind shown as the title (a menu of the
/// type's kinds), and how many lists that kind holds. As the page scrolls it condenses to a slim bar that keeps the
/// title and the count, at Library's pace. The glass takes a hint of the first cover's color.
struct FeaturedKindPlate<Title: View>: View {
    let scroll: LibraryScroll
    let kicker: String
    let count: String
    let accentHex: String?
    private let title: Title

    init(scroll: LibraryScroll, kicker: String, count: String, accentHex: String?, @ViewBuilder title: () -> Title) {
        self.scroll = scroll
        self.kicker = kicker
        self.count = count
        self.accentHex = accentHex
        self.title = title()
    }

    var body: some View {
        let progress = scroll.collapse
        let eased = progress * progress * (3 - 2 * progress)
        // What folds away goes in the first third of the scroll.
        let fold = max(0, 1 - progress * 3)
        let shape = RoundedRectangle(cornerRadius: LibraryPlateLayout.mix(28, 26, eased), style: .continuous)
        let titleScale = LibraryPlateLayout.mix(1, 0.62, eased)
        let titleTop = LibraryPlateLayout.mix(34, 14, eased)

        ZStack(alignment: .topLeading) {
            // Only the title takes touches, so a drag that starts on the plate scrolls the page.
            shape
                .fill(.clear)
                .glassEffect(glass, in: shape)
                .allowsHitTesting(false)

            Text(kicker)
                .font(.system(size: 13, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(.white.opacity(0.62))
                .lineLimit(1)
                .frame(height: 16)
                .padding(.leading, 18)
                .offset(y: 15 - 6 * progress)
                .opacity(fold)
                .allowsHitTesting(false)
                .accessibilityAddTraits(.isHeader)

            // Full: under the kicker. Slim: smaller, centered in the bar.
            title
                .scaleEffect(titleScale, anchor: .topLeading)
                .padding(.leading, 18)
                .offset(y: titleTop)

            // Centered on the title, full and slim.
            Text(count)
                .font(.system(size: 15, weight: .medium))
                .monospacedDigit()
                .foregroundStyle(.white.opacity(0.5))
                .contentTransition(.numericText())
                .animation(.smooth(duration: 0.35), value: count)
                .frame(height: LibraryPlateLayout.titleHeight * titleScale)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .padding(.trailing, 18)
                .offset(y: titleTop)
                .allowsHitTesting(false)
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .frame(
            height: LibraryPlateLayout.mix(FeaturedKindLayout.fullHeight, FeaturedKindLayout.slimHeight, eased),
            alignment: .top
        )
        .clipShape(shape)
        // The page is always dark; light glass would wash out the white text.
        .environment(\.colorScheme, .dark)
    }

    private var glass: Glass {
        guard let rgb = ArtHex.rgb(accentHex) else { return .regular }
        return .regular.tint(Color(red: rgb.red, green: rgb.green, blue: rgb.blue).opacity(0.1))
    }
}

/// A list on a type's page: its first cover, its name, and where it's from with its period (LIVE while it's live).
struct FeaturedKindRow: View {
    let card: FeaturedListCard

    var body: some View {
        HStack(spacing: 14) {
            cover

            VStack(alignment: .leading, spacing: 5) {
                Text(card.name)
                    .font(.system(size: 17, weight: .semibold))
                    .tracking(-0.2)
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)
                FeaturedKindRowLine(card: card)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .padding(.trailing, 16)
            // Hairlines start at the words, as Diary's do.
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(.white.opacity(0.1))
                    .frame(height: 0.5)
            }
        }
        .padding(.leading, 16)
        .frame(height: card.usesSquareArtwork ? FeaturedKindLayout.squareRowHeight : FeaturedKindLayout.rowHeight)
    }

    private var cover: some View {
        let slot = PosterSlot.libraryRow
        return Group {
            if let person = card.stackPeople.first {
                FeaturedPersonPhoto(
                    person: person,
                    size: slot.artworkSize(mediaType: card.singleMediaType, orientation: nil),
                    cornerRadius: slot.cornerRadius
                )
            } else {
                let media = card.previews.first?.media
                MediaArtwork(
                    url: media?.displayPosterURL,
                    title: media?.title ?? card.name,
                    slot: slot,
                    mediaType: media?.ref.mediaType ?? card.singleMediaType ?? APIConstants.allMedia,
                    orientation: card.usesSquareArtwork ? .square : nil
                )
            }
        }
        .overlay {
            RoundedRectangle(cornerRadius: slot.cornerRadius, style: .continuous)
                .strokeBorder(.white.opacity(0.12), lineWidth: 0.5)
        }
        .accessibilityHidden(true)
    }
}

/// "[icon] NYT · Week of Dec 6, 2026", LIVE in red while live, and the count for a list with no source or period.
private struct FeaturedKindRowLine: View {
    let card: FeaturedListCard

    var body: some View {
        if card.live != nil {
            // A live window can start or end while the page is open.
            TimelineView(.periodic(from: .now, by: 60)) { context in
                line(FeaturedBadge.resolve(for: card, now: context.date))
            }
        } else {
            line(FeaturedBadge.resolve(for: card, now: .now))
        }
    }

    private func line(_ badge: FeaturedBadge) -> some View {
        let source = card.source.map(\.shownName).flatMap { $0.isEmpty ? nil : $0 }
        return HStack(spacing: 6) {
            if let url = card.source?.iconURL {
                FeaturedSourceIcon(name: source ?? "", iconURL: url)
            }
            if let source {
                Text(source)
                    .lineLimit(1)
            }
            switch badge {
            case let .live(label):
                FeaturedLiveDot(size: 6)
                Text(label.isEmpty ? "LIVE" : "LIVE · \(label)")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(FeaturedLiveDot.color)
                    .lineLimit(1)
            case let .period(period):
                Text(source == nil ? period : "· \(period)")
                    .lineLimit(1)
                    .layoutPriority(1)
            case .kind:
                if source == nil {
                    Text(card.countText)
                        .lineLimit(1)
                }
            }
        }
        .font(.system(size: 13))
        .foregroundStyle(.white.opacity(0.58))
        .frame(height: 18)
    }
}

/// A source's icon as a small rounded square; its first letter when it has none or it doesn't load.
struct FeaturedSourceIcon: View {
    let name: String
    let iconURL: URL?
    var side: CGFloat = 16

    var body: some View {
        Group {
            if let iconURL {
                SpineAsyncImage(url: iconURL, thumbnailSize: CGSize(width: side, height: side)) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                    case .failure:
                        letter
                    default:
                        Color.white.opacity(0.08)
                    }
                }
            } else {
                letter
            }
        }
        .frame(width: side, height: side)
        .clipShape(RoundedRectangle(cornerRadius: side * 0.28, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: side * 0.28, style: .continuous)
                .strokeBorder(.white.opacity(0.18), lineWidth: 0.5)
        }
        .accessibilityHidden(true)
    }

    private var letter: some View {
        ZStack {
            Color.white.opacity(0.12)
            Text(name.prefix(1).uppercased())
                .font(.system(size: side * 0.56, weight: .bold))
                .foregroundStyle(.white.opacity(0.8))
        }
    }
}
