import SwiftUI

/// Top (STAT-206 to STAT-208): a big #1 in a fixed cell (square for music), numbered rows for
/// #2 to #10, then "See full list". Without a tagged list it's "Highest rated", drawn as Highest
/// rated's bento, and the owner sees how to tag one. One view per row, for the page's lazy stack.
struct StatsTopSection: View {
    let top: StatsTop
    /// Highest rated's total, for the fallback's "Show all".
    var highestRatedTotal = 0
    let onOpenTitle: (MediaSummary) -> Void
    var onOpenList: ((StatsTopList) -> Void)?
    /// The fallback's "Show all": Highest rated's show-all page.
    var onShowAllHighestRated: (() -> Void)?

    enum Row: Hashable, Identifiable {
        /// The title, with "Show all" for the fallback when Highest rated has more.
        case title(showsAll: Bool)
        case hero(StatsTopItem, isLast: Bool)
        case ranked(StatsTopItem, isLast: Bool)
        case note(String, systemImage: String, isLast: Bool)
        case seeFullList(StatsTopList)
        /// The fallback's titles; `hasHint` when the owner's hint follows it.
        case bento(StatsBento, hasHint: Bool)
        /// The owner's hint under the fallback's bento.
        case hint(String)

        var id: String {
            switch self {
            case .title: "top.title"
            case let .hero(item, _): "top.hero.\(item.id)"
            case let .ranked(item, _): "top.rank.\(item.rank).\(item.id)"
            case let .note(text, _, _): "top.note.\(text)"
            case .seeFullList: "top.list"
            case .bento: "top.bento"
            case .hint: "top.hint"
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: top, highestRatedTotal: highestRatedTotal, canShowAll: onShowAllHighestRated != nil)) { row in
            switch row {
            case let .title(showsAll):
                titleRow(showsAll: showsAll)
            case let .hero(item, isLast):
                heroRow(item, isLast: isLast)
                    .statsCardSlice(isFirst: true, isLast: isLast)
            case let .ranked(item, isLast):
                rankedRow(item, isLast: isLast)
                    .statsCardSlice(isFirst: false, isLast: isLast)
            case let .note(text, systemImage, isLast):
                noteRow(text, systemImage: systemImage, isLast: isLast)
                    .statsCardSlice(isFirst: false, isLast: isLast)
            case let .seeFullList(list):
                Button {
                    onOpenList?(list)
                } label: {
                    StatsLabelValueRow(label: "See full list", value: list.total.formatted(), showsChevron: true)
                }
                .buttonStyle(DiaryRowButtonStyle())
                .accessibilityLabel("See full list, \(list.total.formatted()) titles")
                .statsCardSlice(isFirst: false, isLast: true)
            case let .bento(bento, hasHint):
                StatsBentoView(bento: bento, onOpenTitle: onOpenTitle)
                    .padding(.bottom, hasHint ? 4 : StatsSectionLayout.sectionGap)
            case let .hint(text):
                Label(text, systemImage: "tag")
                    .font(.footnote)
                    .foregroundStyle(StatsChartStyle.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 4)
                    .padding(.top, 8)
                    .padding(.bottom, StatsSectionLayout.sectionGap)
            }
        }
    }

    @ViewBuilder
    private func titleRow(showsAll: Bool) -> some View {
        if !top.isFromList {
            StatsSectionTitle(Self.title(for: top)) {
                if showsAll, let onShowAllHighestRated {
                    StatsShowAllButton(action: onShowAllHighestRated)
                }
            }
        } else if let list = top.list, let onOpenList {
            Button {
                onOpenList(list)
            } label: {
                StatsSectionTitle(Self.title(for: top), subtitle: list.name)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the list")
        } else {
            StatsSectionTitle(Self.title(for: top), subtitle: top.isFromList ? top.list?.name : nil)
        }
    }

    private func heroRow(_ item: StatsTopItem, isLast: Bool) -> some View {
        let media = item.media
        let cell = PosterSlot.logSheet.artworkSize(mediaType: media.ref.mediaType, orientation: media.posterOrientation)
        return Button {
            onOpenTitle(media)
        } label: {
            HStack(alignment: .center, spacing: 16) {
                MediaArtwork(
                    url: media.displayPosterURL,
                    title: media.displayTitle,
                    slot: .logSheet,
                    mediaType: media.ref.mediaType,
                    orientation: media.posterOrientation
                )
                .shadow(color: .black.opacity(0.3), radius: 10, y: 5)
                .frame(width: cell.width, height: cell.height)

                VStack(alignment: .leading, spacing: 6) {
                    Text("#\(item.rank)")
                        .font(.subheadline.weight(.bold))
                        .monospacedDigit()
                        .foregroundStyle(.white.opacity(0.45))

                    Text(media.displayTitle)
                        .font(.title3.weight(.bold))
                        .foregroundStyle(.white)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)

                    if let year = StatsTitleFormat.year(of: media) {
                        Text(year)
                            .font(.subheadline)
                            .foregroundStyle(.white.opacity(0.45))
                    }

                    if let stars = item.stars?.value {
                        StatsStars(stars: stars, size: 13)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(14)
            .contentShape(Rectangle())
            .overlay(alignment: .bottom) {
                if !isLast {
                    DiarySeparator()
                        .padding(.leading, 14)
                }
            }
        }
        .buttonStyle(DiaryRowButtonStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.accessibilityLabel(for: item))
        .accessibilityAddTraits(.isButton)
    }

    private func rankedRow(_ item: StatsTopItem, isLast: Bool) -> some View {
        Button {
            onOpenTitle(item.media)
        } label: {
            StatsMediaRow(media: item.media, rank: item.rank, showsSeparator: !isLast) {
                if let stars = item.stars?.value {
                    StatsStars(stars: stars)
                }
            } trailing: {
                EmptyView()
            }
        }
        .buttonStyle(DiaryRowButtonStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.accessibilityLabel(for: item))
        .accessibilityAddTraits(.isButton)
    }

    private func noteRow(_ text: String, systemImage: String, isLast: Bool) -> some View {
        Label(text, systemImage: systemImage)
            .font(.footnote)
            .foregroundStyle(.white.opacity(0.6))
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(alignment: .bottom) {
                if !isLast {
                    DiarySeparator()
                        .padding(.leading, StatsSectionLayout.separatorInset)
                }
            }
    }

    // MARK: - Data shaping

    /// "Top 10" for a list (as many as it shows), "Highest rated" for the fallback.
    static func title(for top: StatsTop) -> String {
        guard top.isFromList else { return "Highest rated" }
        return top.items.count >= 2 ? "Top \(top.items.count)" : "Top"
    }

    /// The owner's hint in the fallback, as the server words it: "Tag a ranked movie list
    /// top2025" (STAT-208). Other viewers get no hint (STAT-004).
    static func hintText(for top: StatsTop) -> String? {
        guard !top.isFromList,
              let hint = top.tagHint?.trimmingCharacters(in: .whitespacesAndNewlines),
              !hint.isEmpty else { return nil }
        return hint
    }

    static func rows(for top: StatsTop, highestRatedTotal: Int = 0, canShowAll: Bool = true) -> [Row] {
        let items = top.items.sorted { $0.rank < $1.rank }
        guard let first = items.first else { return [] }

        // The fallback is Highest rated: its bento, the owner's hint, and "Show all" by the title
        // when Highest rated has more than the bento shows.
        guard top.isFromList else {
            let entries = items.map { StatsBentoEntry(rank: $0.rank, media: $0.media, stars: $0.stars?.value) }
            guard let bento = StatsBento(entries, id: "top.bento") else { return [] }
            let shown = 1 + (bento.second == nil ? 0 : 1) + bento.tiles.count
            let hint = hintText(for: top)
            var rows: [Row] = [
                .title(showsAll: canShowAll && highestRatedTotal > shown),
                .bento(bento, hasHint: hint != nil),
            ]
            if let hint {
                rows.append(.hint(hint))
            }
            return rows
        }

        var notes: [Row] = []
        if top.hintPrivate {
            notes.append(.note("Only you can see this list.", systemImage: "lock", isLast: false))
        }

        var footer: Row?
        if let list = top.list {
            footer = .seeFullList(list)
        }

        let ranked = Array(items.dropFirst())
        let trailingCount = notes.count + (footer == nil ? 0 : 1)

        var rows: [Row] = [.title(showsAll: false), .hero(first, isLast: ranked.isEmpty && trailingCount == 0)]
        for (index, item) in ranked.enumerated() {
            rows.append(.ranked(item, isLast: index == ranked.count - 1 && trailingCount == 0))
        }
        for (index, note) in notes.enumerated() {
            guard case let .note(text, image, _) = note else { continue }
            rows.append(.note(text, systemImage: image, isLast: index == notes.count - 1 && footer == nil))
        }
        if let footer {
            rows.append(footer)
        }
        return rows
    }

    static func accessibilityLabel(for item: StatsTopItem) -> String {
        var parts = ["Number \(item.rank)", item.media.displayTitle]
        if let year = StatsTitleFormat.year(of: item.media) {
            parts.append(year)
        }
        if let stars = item.stars?.value {
            parts.append(StatsStars.accessibilityLabel(for: stars))
        }
        return parts.joined(separator: ", ")
    }
}
