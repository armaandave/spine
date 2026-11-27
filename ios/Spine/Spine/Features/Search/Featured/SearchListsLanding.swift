import SwiftUI

/// What Search shows before you type, with every kind of media in the field: one big word per kind of list
/// ("Movies", "Books") and where its lists come from ("Oscars · Cannes +23"). A word picks that kind, exactly as the
/// media picker does; only "Across Media", which the picker has no place for, opens its own page.
struct SearchListsLanding: View {
    let store: FeaturedListsStore
    let enabledMediaTypes: [String]
    let onSelect: (FeaturedListSelection) -> Void
    let onPickKind: (String) -> Void

    var body: some View {
        let layer = store.layer(mediaType: nil)
        Group {
            if let response = layer.response {
                let shelves = response.visibleShelves(enabledMediaTypes: enabledMediaTypes)
                if shelves.isEmpty {
                    FeaturedEmptyPrompt()
                } else {
                    // Every kind there is, whichever are turned on, so rows keep one height.
                    kinds(shelves, everyKind: response.shelves.filter { !$0.isNow }.count)
                }
            } else if let error = layer.errorMessage {
                FeaturedErrorState(message: error) {
                    Task { await store.load(mediaType: nil, force: true) }
                }
            } else {
                SearchListsSkeleton(kinds: enabledMediaTypes)
            }
        }
        .featuredLayerLifecycle(store: store, mediaType: nil)
    }

    private func kinds(_ shelves: [FeaturedShelf], everyKind: Int) -> some View {
        SearchListsRows(everyKind: everyKind, summary: SearchListsSummary.text(for: shelves)) { rowHeight in
            ForEach(shelves) { shelf in
                if let mediaType = shelf.mediaType, !shelf.isMixed {
                    Button {
                        onPickKind(mediaType)
                    } label: {
                        SearchListsKindRow(shelf: shelf, height: rowHeight)
                    }
                    .buttonStyle(.plain)
                } else {
                    NavigationLink {
                        FeaturedShelfDetailView(
                            store: store,
                            mediaType: shelf.mediaType ?? shelf.key,
                            title: shelf.title,
                            onSelect: onSelect
                        )
                    } label: {
                        SearchListsKindRow(shelf: shelf, height: rowHeight)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .refreshable {
            await store.load(mediaType: nil, force: true)
        }
    }
}

/// The line beside LISTS, adding up the rows on screen: every row's lists, and each source once however many rows
/// it's on.
enum SearchListsSummary {
    static func totals(for shelves: [FeaturedShelf]) -> (lists: Int, sources: Int) {
        (shelves.reduce(0) { $0 + $1.count }, Set(shelves.flatMap { $0.sources.map(\.name) }).count)
    }

    /// "174 lists · 75 sources"; just the lists from a server that doesn't name sources.
    static func text(for shelves: [FeaturedShelf]) -> String {
        let (lists, sources) = totals(for: shelves)
        let listsText = lists == 1 ? "1 list" : "\(lists.formatted()) lists"
        guard sources > 0 else { return listsText }
        return "\(listsText) · \(sources == 1 ? "1 source" : "\(sources.formatted()) sources")"
    }
}

/// "LISTS" over the kinds. The rows split the height down to the tab bar so that every kind fits exactly when
/// they're all turned on (fewer end higher), and scroll only on a screen too short for that.
private struct SearchListsRows<Rows: View>: View {
    let everyKind: Int
    var summary: String?
    @ViewBuilder let rows: (_ rowHeight: CGFloat) -> Rows

    private static var minimumRowHeight: CGFloat { 60 }
    /// Room left between the last row and the tab bar.
    private static var bottomGap: CGFloat { 8 }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SearchListsHeading(summary: summary)
                .padding(.horizontal, 16)
                .padding(.top, 14)
            GeometryReader { proxy in
                let fitted = (proxy.size.height - Self.bottomGap) / CGFloat(max(everyKind, 1))
                ScrollView {
                    VStack(spacing: 0) {
                        rows(max(Self.minimumRowHeight, fitted))
                    }
                    .padding(.horizontal, 16)
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .ignoresSafeArea(.keyboard)
        }
    }
}

private struct SearchListsHeading: View {
    var summary: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("LISTS")
                .font(.system(size: 13, weight: .bold))
                .tracking(0.8)
                .foregroundStyle(.white.opacity(0.55))
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            if let summary {
                Text(summary)
                    .font(.system(size: 12, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.42))
            }
        }
        .padding(.bottom, 4)
    }
}

/// "Movies" in big type, and where its lists come from at the far end.
private struct SearchListsKindRow: View {
    let shelf: FeaturedShelf
    let height: CGFloat

    var body: some View {
        HStack(spacing: 12) {
            Text(shelf.title)
                .font(.system(size: 32, weight: .heavy))
                .tracking(-1.1)
                .foregroundStyle(.white)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .layoutPriority(1)
            Spacer(minLength: 0)
            SearchListsSourcesLine(sources: shelf.sources)
        }
        .frame(height: height)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.1))
                .frame(height: 0.5)
        }
        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityHint("Shows its lists")
    }

    private var accessibilityText: String {
        let lists = "\(shelf.count) \(shelf.count == 1 ? "list" : "lists")"
        guard !shelf.sources.isEmpty else { return "\(shelf.title), \(lists)" }
        let total = shelf.sources.count
        let named = shelf.sources.prefix(2).map(\.name).joined(separator: " and ")
        return "\(shelf.title), \(lists) from \(total) \(total == 1 ? "source" : "sources"), including \(named)"
    }
}

/// A row's sources on one line: the first two by name with their icons, or the first alone when two don't fit,
/// then how many more.
private struct SearchListsSourcesLine: View {
    let sources: [FeaturedShelfSource]

    var body: some View {
        ViewThatFits(in: .horizontal) {
            line(naming: 2)
            line(naming: 1)
        }
        .accessibilityHidden(true)
    }

    private func line(naming count: Int) -> some View {
        let named = Array(sources.prefix(count))
        let more = sources.count - named.count
        return HStack(spacing: 11) {
            ForEach(named) { source in
                HStack(spacing: 6) {
                    FeaturedSourceIcon(name: source.name, iconURL: source.iconUrl.flatMap(URL.init(string:)))
                    Text(source.name)
                        .font(.system(size: 12.5, weight: .semibold))
                        .tracking(-0.1)
                        .foregroundStyle(.white.opacity(0.9))
                        .lineLimit(1)
                }
            }
            if more > 0 {
                Text("+\(more)")
                    .font(.system(size: 12, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.4))
            }
        }
    }
}

/// A row per kind you have on, each about as wide as its word, so the lists land without the rows changing.
private struct SearchListsSkeleton: View {
    let kinds: [String]

    private static let wordWidths: [String: CGFloat] = [
        "movie": 150, "tv": 70, "anime": 120, "manga": 120, "game": 120, "book": 120, "comic": 140, "music": 110,
    ]

    var body: some View {
        SearchListsRows(everyKind: 8) { rowHeight in
            ForEach(kinds, id: \.self) { kind in
                HStack {
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .fill(.white.opacity(0.08))
                        .frame(width: Self.wordWidths[kind] ?? 120, height: 26)
                    Spacer()
                    HStack(spacing: 11) {
                        ForEach(0 ..< 2, id: \.self) { _ in
                            HStack(spacing: 6) {
                                RoundedRectangle(cornerRadius: 4.5, style: .continuous)
                                    .fill(.white.opacity(0.08))
                                    .frame(width: 16, height: 16)
                                RoundedRectangle(cornerRadius: 3, style: .continuous)
                                    .fill(.white.opacity(0.06))
                                    .frame(width: 46, height: 10)
                            }
                        }
                    }
                }
                .frame(height: rowHeight)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading lists")
    }
}
