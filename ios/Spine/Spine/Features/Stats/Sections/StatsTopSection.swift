import SwiftUI

/// Top (STAT-206 to STAT-208): a big #1 in a fixed cell (square for music), numbered rows for
/// #2 to #10, then "See full list". Without a tagged list it's titled "Highest rated", and the
/// owner sees how to tag one. One view per row, for the page's lazy stack.
struct StatsTopSection: View {
    let top: StatsTop
    /// Highest rated's total, for the fallback's "Show all".
    var highestRatedTotal = 0
    let onOpenTitle: (MediaSummary) -> Void
    var onOpenList: ((StatsTopList) -> Void)?
    /// The fallback's "Show all": Highest rated's show-all page.
    var onShowAllHighestRated: (() -> Void)?

    enum Row: Hashable, Identifiable {
        case title
        case hero(StatsTopItem, isLast: Bool)
        case ranked(StatsTopItem, isLast: Bool)
        case note(String, systemImage: String, isLast: Bool)
        case seeFullList(StatsTopList)
        case showAllRated(total: Int)

        var id: String {
            switch self {
            case .title: "top.title"
            case let .hero(item, _): "top.hero.\(item.id)"
            case let .ranked(item, _): "top.rank.\(item.rank).\(item.id)"
            case let .note(text, _, _): "top.note.\(text)"
            case .seeFullList: "top.list"
            case .showAllRated: "top.showAll"
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: top, highestRatedTotal: highestRatedTotal, canShowAll: onShowAllHighestRated != nil)) { row in
            switch row {
            case .title:
                titleRow
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
            case let .showAllRated(total):
                Button {
                    onShowAllHighestRated?()
                } label: {
                    StatsLabelValueRow(label: "Show all", value: total.formatted(), showsChevron: true)
                }
                .buttonStyle(DiaryRowButtonStyle())
                .accessibilityLabel("Show all \(total.formatted()) highest rated")
                .statsCardSlice(isFirst: false, isLast: true)
            }
        }
    }

    @ViewBuilder
    private var titleRow: some View {
        if let list = top.list, top.isFromList, let onOpenList {
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

        var notes: [Row] = []
        if top.isFromList, top.hintPrivate {
            notes.append(.note("Only you can see this list.", systemImage: "lock", isLast: false))
        }
        if let hint = hintText(for: top) {
            notes.append(.note(hint, systemImage: "tag", isLast: false))
        }

        var footer: Row?
        if top.isFromList, let list = top.list {
            footer = .seeFullList(list)
        } else if !top.isFromList, canShowAll, highestRatedTotal > items.count {
            footer = .showAllRated(total: highestRatedTotal)
        }

        let ranked = Array(items.dropFirst())
        let trailingCount = notes.count + (footer == nil ? 0 : 1)

        var rows: [Row] = [.title, .hero(first, isLast: ranked.isEmpty && trailingCount == 0)]
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
