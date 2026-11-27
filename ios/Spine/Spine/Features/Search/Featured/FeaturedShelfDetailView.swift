import SwiftUI

/// "See all": a media type's featured lists (or "Across Media"), pushed in Search's navigation stack.
struct FeaturedShelfDetailView: View {
    let store: FeaturedListsStore
    let mediaType: String
    let title: String
    let onSelect: (FeaturedListSelection) -> Void

    @State private var top = FeaturedTopArt()

    var body: some View {
        FeaturedTypeLayer(store: store, mediaType: mediaType, title: title, top: top, onSelect: onSelect)
            .background(alignment: .top) {
                LibraryBlurredTop(url: top.url, scroll: top.scroll, height: FeaturedKindLayout.artBelowPage)
            }
            .background(Color.black)
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            // Clear, so the art runs up behind the back button as it does behind Search's field.
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
    }
}

/// What a type's page shows behind the top of the screen: the first cover of the kind shown, blurred, moving up as the
/// page scrolls. The page sets it; the screen around the page draws it (Search draws it behind its field), since the
/// art runs above the page.
@MainActor
@Observable
final class FeaturedTopArt {
    var url: URL?
    let scroll = LibraryScroll()
}

enum FeaturedKindLayout {
    static let fullHeight: CGFloat = 96
    static let slimHeight: CGFloat = 52
    /// The plate's gap below the page's top (Search's type bar).
    static let plateTop: CGFloat = 4
    /// The first list starts this far below the page's top: under the plate, after a gap.
    static let contentTop: CGFloat = plateTop + fullHeight + 8
    /// How far the art reaches below the page's top: about Library's reach below its plate.
    static let artBelowPage: CGFloat = contentTop + 170
    static let rowHeight: CGFloat = 104
    /// A row of square album art.
    static let squareRowHeight: CGFloat = 84
}

/// A media type's lists, one kind at a time (Library's twin): a glass plate whose title is the kind shown, a menu of
/// the type's kinds, over that kind's lists. The plate condenses as the page scrolls.
struct FeaturedTypeLayer: View {
    let store: FeaturedListsStore
    let mediaType: String
    var title: String?
    let top: FeaturedTopArt
    let onSelect: (FeaturedListSelection) -> Void

    /// The kind shown, by its shelf key; until one is picked, the type's first.
    @State private var kindKey: String?
    @State private var position = ScrollPosition(edge: .top)

    var body: some View {
        let layer = store.layer(mediaType: mediaType)
        Group {
            if let response = layer.response, !response.shelves.isEmpty {
                content(response)
            } else if let error = layer.errorMessage, layer.response == nil {
                FeaturedErrorState(message: error) {
                    Task { await store.load(mediaType: mediaType, force: true) }
                }
            } else if layer.response != nil {
                ContentUnavailableView(
                    "No featured lists yet",
                    systemImage: "list.star",
                    description: Text("Enter a title to find media.")
                )
            } else {
                ProgressView()
                    .tint(.white)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .featuredLayerLifecycle(store: store, mediaType: mediaType)
        .onAppear {
            top.scroll.offset = 0
        }
        .onChange(of: artURL(layer.response), initial: true) { _, url in
            withAnimation(.smooth(duration: 0.6)) {
                top.url = url
            }
        }
        .onDisappear {
            // Another type's page may have shown its own art already.
            guard top.url == artURL(layer.response) else { return }
            withAnimation(.smooth(duration: 0.35)) {
                top.url = nil
            }
        }
        .sensoryFeedback(.selection, trigger: kindKey)
    }

    private var displayTitle: String {
        if let title { return title }
        return mediaType == FeaturedShelf.mixedMediaType ? "Across Media" : MediaTypeTheme.theme(for: mediaType).displayName
    }

    private func shown(_ response: FeaturedShelvesResponse?) -> FeaturedShelf? {
        guard let shelves = response?.shelves, let first = shelves.first else { return nil }
        return shelves.first { $0.key == kindKey } ?? first
    }

    /// The art at the top: the first cover (or a people list's first photo) of the kind shown.
    private func artURL(_ response: FeaturedShelvesResponse?) -> URL? {
        guard let shelf = shown(response) else { return nil }
        for card in shelf.lists {
            if let poster = card.previews.first?.media.displayPosterURL, let url = URL(string: poster) {
                return url
            }
            if let person = card.stackPeople.first, let url = FeaturedPersonPhoto.url(for: person) {
                return url
            }
        }
        return nil
    }

    private func content(_ response: FeaturedShelvesResponse) -> some View {
        let shelves = response.shelves
        let shelf = shown(response) ?? shelves[0]
        return ZStack(alignment: .top) {
            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: 0) {
                    ForEach(shelf.lists) { card in
                        FeaturedListButton(card: card, sourceKey: "\(mediaType)/\(shelf.key)", onSelect: onSelect) {
                            FeaturedKindRow(card: card)
                        }
                    }
                }
                .id(shelf.key)
                .transition(.opacity.combined(with: .offset(y: 6)))
                .padding(.bottom, 28)
            }
            .scrollPosition($position)
            // The lists start under the plate, and pull to refresh shows below it.
            .safeAreaInset(edge: .top, spacing: 0) {
                Color.clear.frame(height: FeaturedKindLayout.contentTop)
            }
            .refreshable {
                await store.load(mediaType: mediaType, force: true)
            }
            .onScrollGeometryChange(for: CGFloat.self) { geometry in
                // Past the art's reach nothing up top changes, so the offset stops there.
                min(geometry.contentOffset.y + geometry.contentInsets.top, LibraryPlateLayout.scrollCap)
            } action: { _, offset in
                top.scroll.offset = offset
            }
            // Rows fade out where the page meets the type bar instead of being cut.
            .mask {
                VStack(spacing: 0) {
                    LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                        .frame(height: FeaturedKindLayout.plateTop + 8)
                    Color.black
                }
            }

            FeaturedKindPlate(
                scroll: top.scroll,
                kicker: kicker(response),
                count: listCount(shelf.count),
                accentHex: shelf.lists.first?.previews.first?.media.posterAccentColor
            ) {
                kindTitle(shelves, shown: shelf)
            }
            .padding(.horizontal, 16)
            .padding(.top, FeaturedKindLayout.plateTop)
        }
    }

    /// The kind shown, as the title; with more than one kind, a tap opens the type's kinds.
    @ViewBuilder
    private func kindTitle(_ shelves: [FeaturedShelf], shown shelf: FeaturedShelf) -> some View {
        if shelves.count > 1 {
            Menu {
                Picker("Kind", selection: kindBinding(current: shelf.key)) {
                    ForEach(shelves) { shelf in
                        Text(shelf.title).tag(shelf.key)
                    }
                }
            } label: {
                titleLabel(shelf.title, showsChevron: true)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Kind of list, \(shelf.title)")
            .accessibilityHint("Shows another kind of list")
        } else {
            titleLabel(shelf.title, showsChevron: false)
                .accessibilityAddTraits(.isHeader)
        }
    }

    private func titleLabel(_ title: String, showsChevron: Bool) -> some View {
        HStack(spacing: 6) {
            Text(title)
                .font(.system(size: 30, weight: .black))
                .tracking(-0.5)
                .foregroundStyle(.white)
                .shadow(color: .black.opacity(0.22), radius: 5, y: 1)
                .lineLimit(1)
                .contentTransition(.opacity)
            if showsChevron {
                Image(systemName: "chevron.down")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white.opacity(0.6))
                    .padding(.top, 3)
            }
        }
        .frame(height: LibraryPlateLayout.titleHeight)
        .contentShape(Rectangle())
        .animation(.smooth(duration: 0.35), value: title)
    }

    /// Picking a kind shows its lists from the top.
    private func kindBinding(current: String) -> Binding<String> {
        Binding(
            get: { current },
            set: { key in
                guard key != current else { return }
                position.scrollTo(edge: .top)
                withAnimation(.smooth(duration: 0.35)) {
                    kindKey = key
                }
            }
        )
    }

    /// "Books · 64 lists", and how many are live while any is.
    private func kicker(_ response: FeaturedShelvesResponse) -> String {
        let total = response.shelves.reduce(0) { $0 + $1.count }
        let live = Set(response.allCards.filter { $0.isLive(at: .now) }.map(\.id)).count
        return ([displayTitle, listCount(total)] + (live > 0 ? ["\(live) live"] : [])).joined(separator: " · ")
    }

    private func listCount(_ count: Int) -> String {
        count == 1 ? "1 list" : "\(count.formatted()) lists"
    }
}
