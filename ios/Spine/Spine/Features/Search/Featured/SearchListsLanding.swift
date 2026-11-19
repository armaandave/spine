import SwiftUI

/// What Search shows before you type, with every kind of media in the field: one big word per kind of list
/// ("Movies", "Books", "Across Media"), how many lists it has and three of their posters. A word opens that kind's
/// lists.
struct SearchListsLanding: View {
    let store: FeaturedListsStore
    let enabledMediaTypes: [String]
    let onSelect: (FeaturedListSelection) -> Void

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
                SearchListsSkeleton()
            }
        }
        .featuredLayerLifecycle(store: store, mediaType: nil)
    }

    private func kinds(_ shelves: [FeaturedShelf], everyKind: Int) -> some View {
        SearchListsRows(everyKind: everyKind) { rowHeight in
            ForEach(shelves) { shelf in
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
        .refreshable {
            await store.load(mediaType: nil, force: true)
        }
    }
}

/// "LISTS" over the kinds. The rows split the height down to the tab bar so that every kind fits exactly when
/// they're all turned on (fewer end higher), and scroll only on a screen too short for that.
private struct SearchListsRows<Rows: View>: View {
    let everyKind: Int
    @ViewBuilder let rows: (_ rowHeight: CGFloat) -> Rows

    private static var minimumRowHeight: CGFloat { 60 }
    /// Room left between the last row and the tab bar.
    private static var bottomGap: CGFloat { 8 }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SearchListsHeading()
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
    var body: some View {
        Text("LISTS")
            .font(.system(size: 13, weight: .bold))
            .tracking(0.8)
            .foregroundStyle(.white.opacity(0.55))
            .padding(.bottom, 4)
            .accessibilityAddTraits(.isHeader)
    }
}

/// "Movies 39" in big type, the count on the word's baseline, three of its posters at the far end.
private struct SearchListsKindRow: View {
    let shelf: FeaturedShelf
    let height: CGFloat

    var body: some View {
        HStack(spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(shelf.title)
                    .font(.system(size: 32, weight: .heavy))
                    .tracking(-1.1)
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text(shelf.count.formatted())
                    .font(.system(size: 13, weight: .bold))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.45))
            }
            Spacer(minLength: 8)
            SearchListsPosters(posters: shelf.rowPosters, square: shelf.mediaType == "music")
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
        .accessibilityLabel("\(shelf.title), \(shelf.count) \(shelf.count == 1 ? "list" : "lists")")
        .accessibilityHint("Opens its lists")
    }
}

/// Up to three posters side by side, straight, in slots of one width so every row's posters line up.
private struct SearchListsPosters: View {
    static let slotWidth: CGFloat = 32
    static let posterHeight: CGFloat = 48
    static let spacing: CGFloat = 6

    let posters: [MediaSummary]
    let square: Bool

    var body: some View {
        HStack(spacing: Self.spacing) {
            ForEach(posters, id: \.ref) { media in
                let size = CGSize(width: Self.slotWidth, height: square ? Self.slotWidth : Self.posterHeight)
                SpineAsyncImage(url: URL(string: media.displayPosterURL ?? ""), thumbnailSize: size) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                    } else {
                        Color.white.opacity(0.08)
                    }
                }
                .frame(width: size.width, height: size.height)
                .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            }
        }
        .frame(width: Self.slotWidth * 3 + Self.spacing * 2, height: Self.posterHeight, alignment: .trailing)
        .accessibilityHidden(true)
    }
}

private struct SearchListsSkeleton: View {
    var body: some View {
        SearchListsRows(everyKind: 8) { rowHeight in
            ForEach(0 ..< 8, id: \.self) { index in
                HStack {
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .fill(.white.opacity(0.08))
                        .frame(width: [150, 70, 120, 120, 120, 120, 140, 110][index], height: 26)
                    Spacer()
                    HStack(spacing: SearchListsPosters.spacing) {
                        ForEach(0 ..< 3, id: \.self) { _ in
                            RoundedRectangle(cornerRadius: 4, style: .continuous)
                                .fill(.white.opacity(0.06))
                                .frame(width: SearchListsPosters.slotWidth, height: SearchListsPosters.posterHeight)
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

extension FeaturedShelf {
    /// Up to three posters for a kind's row: the first poster of each of its first lists, then more from the first
    /// list when it has fewer lists, never the same title twice.
    var rowPosters: [MediaSummary] {
        var posters: [MediaSummary] = []
        func add(_ media: MediaSummary?) {
            guard let media, posters.count < 3, !posters.contains(where: { $0.ref == media.ref }) else { return }
            posters.append(media)
        }
        lists.forEach { add($0.previews.first?.media) }
        lists.first?.previews.forEach { add($0.media) }
        return posters
    }
}
