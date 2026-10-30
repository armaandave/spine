import SwiftUI

/// Highest rated (STAT-205): a compact top 10 in the server's order, a big #1 with #2 tucked
/// behind it and #3 to #10 in two rows of four, each with its rank and gold stars, and
/// "Show all". The page shows it only
/// when Top comes from a list (`isShown(top:highestRated:)`), so the two never repeat each
/// other; Top's own fallback uses the same bento.
struct StatsHighestRatedSection: View {
    let highestRated: StatsHighestRated
    let onOpenTitle: (MediaSummary) -> Void
    var onShowAll: (() -> Void)?

    enum Row: Identifiable {
        case title
        case bento(StatsBento)

        var id: String {
            switch self {
            case .title: "highest.title"
            case let .bento(bento): bento.id
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: highestRated)) { row in
            switch row {
            case .title:
                StatsSectionTitle("Highest rated") {
                    if let onShowAll, Self.hasMore(highestRated) {
                        StatsShowAllButton(action: onShowAll)
                    }
                }
            case let .bento(bento):
                StatsBentoView(bento: bento, onOpenTitle: onOpenTitle)
                    .padding(.bottom, StatsSectionLayout.sectionGap)
            }
        }
    }

    // MARK: - Data shaping

    /// Only when Top comes from a list; otherwise Top already shows these titles.
    static func isShown(top: StatsTop?, highestRated: StatsHighestRated?) -> Bool {
        guard let highestRated, !highestRated.isEmpty else { return false }
        return top?.isFromList == true
    }

    /// More than the bento shows, counting the rows the payload sent but the bento leaves out.
    static func hasMore(_ highestRated: StatsHighestRated) -> Bool {
        highestRated.total > min(highestRated.items.count, StatsBento.capacity)
    }

    static func rows(for highestRated: StatsHighestRated) -> [Row] {
        let entries = highestRated.items.enumerated().map { index, title in
            StatsBentoEntry(rank: index + 1, media: title.media, stars: title.stars?.value)
        }
        guard let bento = StatsBento(entries, id: "highest.bento") else { return [] }
        return [.title, .bento(bento)]
    }
}

// MARK: - Bento

/// One poster of the bento.
struct StatsBentoEntry: Identifiable, Hashable {
    let rank: Int
    let media: MediaSummary
    let stars: Double?

    var id: String { media.id }
}

/// The first ten titles as a bento: the lead, #2 tucked behind its right edge, then up to eight
/// tiles in reading order, two rows of four. It's one row of the page's lazy stack, a fixed-size
/// block like the header's numbers card.
struct StatsBento: Identifiable, Hashable {
    static let columns = 4
    static let tileRows = 2
    static var capacity: Int { 2 + columns * tileRows }

    let id: String
    let lead: StatsBentoEntry
    let second: StatsBentoEntry?
    let tiles: [StatsBentoEntry]

    /// Nil without any entries; entries past the tenth are left out.
    init?(_ entries: [StatsBentoEntry], id: String) {
        guard let lead = entries.first else { return nil }
        self.id = id
        self.lead = lead
        second = entries.dropFirst().first
        tiles = Array(entries.dropFirst(2).prefix(Self.columns * Self.tileRows))
    }
}

/// Where a poster sits in the bento.
enum StatsBentoRole: Hashable {
    case lead
    case second
    case tile
}

/// The bento's measurements. Every poster keeps its slot's fixed size, so artwork decodes once
/// at the size it shows; the width left over is shared by the four gaps after #2, so the posters
/// sit evenly spaced on every phone.
enum StatsBentoMetrics {
    static let leadSlot: PosterSlot = .statsBentoLead
    static let secondSlot: PosterSlot = .statsBentoSecond
    static let tileSlot: PosterSlot = .statsBentoTile
    /// How far #2 tucks behind the lead's right edge.
    static let overlap: CGFloat = 26
    static let leadStarsSize: CGFloat = 11
    /// #2 and the tiles are too narrow for five stars: they show one star and the rating.
    static let secondStarsSize: CGFloat = 10
    static let tileStarsSize: CGFloat = 9
    static let leadCaptionHeight: CGFloat = 14
    static let tileCaptionHeight: CGFloat = 10
    static let leadCaptionSpacing: CGFloat = 6
    static let tileCaptionSpacing: CGFloat = 4
    static let rowGap: CGFloat = 10

    /// A tile with its stars.
    static var tileHeight: CGFloat { tileSlot.size.height + tileCaptionSpacing + tileCaptionHeight }
    /// The lead with its stars: as tall as both tile rows and the gap between them.
    static var leadHeight: CGFloat { leadSlot.size.height + leadCaptionSpacing + leadCaptionHeight }
    static var height: CGFloat { tileHeight * CGFloat(StatsBento.tileRows) + rowGap * CGFloat(StatsBento.tileRows - 1) }
    /// The part of #2 the lead doesn't cover.
    static var secondVisibleWidth: CGFloat { secondSlot.size.width - overlap }
    /// The posters' own width, with no gaps.
    static var postersWidth: CGFloat {
        leadSlot.size.width + secondVisibleWidth + tileSlot.size.width * CGFloat(StatsBento.columns)
    }

    /// One of the four equal gaps across `width`: after #2, and between the tiles.
    static func gap(width: CGFloat) -> CGFloat {
        max((width - postersWidth) / CGFloat(StatsBento.columns), 0)
    }

    /// Each cell's top-leading corner: the lead, then #2 (bottom-aligned with the lead's
    /// poster), then the tiles in reading order.
    static func origins(width: CGFloat, count: Int) -> [CGPoint] {
        guard count > 0 else { return [] }
        var origins: [CGPoint] = [.zero]
        guard count > 1 else { return origins }
        origins.append(CGPoint(
            x: leadSlot.size.width - overlap,
            y: leadSlot.size.height - secondSlot.size.height
        ))
        let gap = gap(width: width)
        let tilesX = leadSlot.size.width + secondVisibleWidth + gap
        origins += (0 ..< count - 2).map { index in
            let row = index / StatsBento.columns
            let column = index % StatsBento.columns
            return CGPoint(
                x: tilesX + CGFloat(column) * (tileSlot.size.width + gap),
                y: CGFloat(row) * (tileHeight + rowGap)
            )
        }
        return origins
    }
}

/// Places the bento's cells at `StatsBentoMetrics.origins`, each at its own fixed size.
struct StatsBentoLayout: Layout {
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        CGSize(width: proposal.width ?? StatsBentoMetrics.postersWidth, height: StatsBentoMetrics.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let origins = StatsBentoMetrics.origins(width: bounds.width, count: subviews.count)
        for (subview, origin) in zip(subviews, origins) {
            subview.place(at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y), proposal: .unspecified)
        }
    }
}

/// The bento: the lead on the left with #2 tucked behind it, and the tiles in two rows of four.
struct StatsBentoView: View {
    let bento: StatsBento
    let onOpenTitle: (MediaSummary) -> Void

    var body: some View {
        StatsBentoLayout {
            StatsBentoCell(entry: bento.lead, role: .lead) { onOpenTitle(bento.lead.media) }
                .zIndex(1)
            if let second = bento.second {
                StatsBentoCell(entry: second, role: .second) { onOpenTitle(second.media) }
            }
            ForEach(bento.tiles) { entry in
                StatsBentoCell(entry: entry, role: .tile) { onOpenTitle(entry.media) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A bento poster: the artwork on its cell's bottom edge with its rank in the corner (and "S2"
/// for a season, which can share its show's artwork), then its gold stars. #2's rank and stars
/// sit on its right, the part the lead doesn't cover.
struct StatsBentoCell: View {
    let entry: StatsBentoEntry
    let role: StatsBentoRole
    let action: () -> Void

    private var isLead: Bool { role == .lead }
    private var isSmall: Bool { role == .tile }

    private var slot: PosterSlot {
        switch role {
        case .lead: StatsBentoMetrics.leadSlot
        case .second: StatsBentoMetrics.secondSlot
        case .tile: StatsBentoMetrics.tileSlot
        }
    }

    private var starsSize: CGFloat {
        switch role {
        case .lead: StatsBentoMetrics.leadStarsSize
        case .second: StatsBentoMetrics.secondStarsSize
        case .tile: StatsBentoMetrics.tileStarsSize
        }
    }

    private var badgeAlignment: Alignment { role == .second ? .topTrailing : .topLeading }

    var body: some View {
        Button(action: action) {
            VStack(alignment: role == .second ? .trailing : .leading, spacing: isSmall ? StatsBentoMetrics.tileCaptionSpacing : StatsBentoMetrics.leadCaptionSpacing) {
                MediaArtwork(
                    url: entry.media.displayPosterURL,
                    title: entry.media.displayTitle,
                    slot: slot,
                    mediaType: entry.media.ref.mediaType,
                    orientation: entry.media.posterOrientation
                )
                .overlay(alignment: badgeAlignment) {
                    StatsBentoBadge(text: "\(entry.rank)", isLarge: !isSmall)
                }
                .overlay(alignment: role == .second ? .bottomTrailing : .bottomLeading) {
                    if let season = StatsRatedPosterCell.seasonTag(for: entry.media.ref) {
                        StatsBentoBadge(text: season, isLarge: !isSmall)
                    }
                }
                .shadow(color: .black.opacity(isLead ? 0.45 : 0.28), radius: isLead ? 10 : 6, x: isLead ? 4 : 0, y: isLead ? 5 : 3)
                .frame(width: slot.size.width, height: slot.size.height, alignment: .bottom)

                Group {
                    if let stars = entry.stars {
                        if isLead {
                            StatsStars(stars: stars, size: starsSize)
                        } else {
                            StatsCompactRating(stars: stars, size: starsSize)
                        }
                    }
                }
                .frame(
                    width: role == .second ? StatsBentoMetrics.secondVisibleWidth : slot.size.width,
                    height: isSmall ? StatsBentoMetrics.tileCaptionHeight : StatsBentoMetrics.leadCaptionHeight,
                    alignment: role == .second ? .trailing : .leading
                )
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.accessibilityLabel(for: entry))
        .accessibilityAddTraits(.isButton)
    }

    /// "Number 2, Severance S2, Rated 4.5 out of 5 stars".
    static func accessibilityLabel(for entry: StatsBentoEntry) -> String {
        var parts = ["Number \(entry.rank)", entry.media.displayTitle]
        if let stars = entry.stars {
            parts.append(StatsStars.accessibilityLabel(for: stars))
        }
        return parts.joined(separator: ", ")
    }
}

/// One gold star and the rating ("★ 4.5"), for posters too narrow for a row of five stars.
struct StatsCompactRating: View {
    let stars: Double
    let size: CGFloat

    var body: some View {
        HStack(spacing: 2) {
            Image(systemName: "star.fill")
                .font(.system(size: size - 1, weight: .bold))
                .foregroundStyle(LogComposerStyle.starGold)
            Text(Self.text(for: stars))
                .font(.system(size: size, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(.white.opacity(0.85))
        }
        .lineLimit(1)
        .accessibilityHidden(true)
    }

    /// "5", "4.5".
    static func text(for stars: Double) -> String {
        stars.formatted(.number.precision(.fractionLength(0 ... 1)))
    }
}

/// A rank or season label on a poster's corner: white on a dark rounded square, so it reads on
/// any artwork.
private struct StatsBentoBadge: View {
    let text: String
    let isLarge: Bool

    var body: some View {
        Text(text)
            .font(.system(size: isLarge ? 13 : 10, weight: .bold))
            .monospacedDigit()
            .foregroundStyle(.white)
            .lineLimit(1)
            .padding(.horizontal, isLarge ? 6 : 4)
            .frame(minWidth: isLarge ? 22 : 16, minHeight: isLarge ? 22 : 16)
            .background(.black.opacity(0.62), in: RoundedRectangle(cornerRadius: isLarge ? 6 : 4, style: .continuous))
            .padding(isLarge ? 6 : 3)
            .accessibilityHidden(true)
    }
}

/// A rated poster: the artwork and its gold stars, and "S2" or "Specials" under a season, which
/// is rated on its own and can share its show's artwork. Highest rated's show-all page uses it.
struct StatsRatedPosterCell: View {
    let title: StatsRatedTitle
    let action: () -> Void

    var body: some View {
        StatsPosterCell(media: title.media, accessibilityText: Self.accessibilityText(for: title), action: action) {
            VStack(alignment: .leading, spacing: 3) {
                if let stars = title.stars?.value {
                    StatsStars(stars: stars, size: 10)
                } else {
                    Color.clear.frame(height: 12)
                }
                if let season = Self.seasonTag(for: title.media.ref) {
                    StatsPosterCaption(text: season)
                }
            }
        }
    }

    /// "S2" for a season, "Specials" for season 0, nil for anything else.
    static func seasonTag(for ref: MediaRef) -> String? {
        guard ref.mediaType == "season", let number = ref.seasonNumber else { return nil }
        return number == 0 ? "Specials" : "S\(number)"
    }

    static func accessibilityText(for title: StatsRatedTitle) -> String {
        var parts = [title.media.displayTitle]
        if let stars = title.stars?.value {
            parts.append(StatsStars.accessibilityLabel(for: stars))
        }
        return parts.joined(separator: ", ")
    }
}
