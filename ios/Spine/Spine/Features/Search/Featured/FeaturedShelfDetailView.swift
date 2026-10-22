import SwiftUI

/// "See all": a media type's featured lists (or "Across Media"), pushed in Search's navigation stack.
struct FeaturedShelfDetailView: View {
    let store: FeaturedListsStore
    let mediaType: String
    let title: String
    let onSelect: (FeaturedListSelection) -> Void

    var body: some View {
        FeaturedTypeLayer(store: store, mediaType: mediaType, title: title, onSelect: onSelect)
            .background(Color.black)
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
    }
}

/// A media type's lists grouped by kind: a header, kind chips pinned under it, then one section per kind. Charts
/// are wide rows; everything else is a two-column grid.
struct FeaturedTypeLayer: View {
    /// Room for the pinned chips above a section that a chip scrolled to.
    private static let chipBarHeight: CGFloat = 46
    private static let columnSpacing: CGFloat = 11
    private static let horizontalInset: CGFloat = 16

    let store: FeaturedListsStore
    let mediaType: String
    var title: String?
    let onSelect: (FeaturedListSelection) -> Void

    @State private var contentWidth: CGFloat = 393

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
    }

    private var displayTitle: String {
        if let title { return title }
        return mediaType == FeaturedShelf.mixedMediaType ? "Across Media" : MediaTypeTheme.theme(for: mediaType).displayName
    }

    private var columnWidth: CGFloat {
        max(140, ((contentWidth - Self.horizontalInset * 2 - Self.columnSpacing) / 2).rounded(.down))
    }

    private func content(_ response: FeaturedShelvesResponse) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(displayTitle)
                            .font(.system(size: 30, weight: .black))
                            .foregroundStyle(.white)
                            .accessibilityAddTraits(.isHeader)
                        Text(response.summaryLine(now: .now))
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.55))
                    }
                    .padding(.horizontal, Self.horizontalInset)
                    .padding(.top, 8)
                    .padding(.bottom, 10)

                    LazyVStack(alignment: .leading, spacing: 0, pinnedViews: [.sectionHeaders]) {
                        Section {
                            ForEach(response.shelves) { shelf in
                                kindSection(shelf)
                                    .padding(.top, Self.chipBarHeight + 6)
                                    .id(shelf.key)
                            }
                        } header: {
                            if response.shelves.count > 1 {
                                chipBar(response.shelves, proxy: proxy)
                            }
                        }
                    }
                }
                .padding(.bottom, 28)
            }
            .refreshable {
                await store.load(mediaType: mediaType, force: true)
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { contentWidth = $0 }
    }

    private func chipBar(_ shelves: [FeaturedShelf], proxy: ScrollViewProxy) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(shelves) { shelf in
                    Button {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            proxy.scrollTo(shelf.key, anchor: .top)
                        }
                    } label: {
                        HStack(spacing: 5) {
                            Text(shelf.title)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white)
                            Text(shelf.count.formatted())
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.5))
                        }
                        .padding(.horizontal, 12)
                        .frame(height: 30)
                        .background(.white.opacity(0.1), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(shelf.title), \(shelf.count) lists")
                    .accessibilityHint("Jumps to the section")
                }
            }
            .padding(.horizontal, Self.horizontalInset)
        }
        .frame(height: Self.chipBarHeight)
        .background(Color.black.opacity(0.94))
    }

    private func kindSection(_ shelf: FeaturedShelf) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text(shelf.title.uppercased())
                    .font(.system(size: 13, weight: .black))
                    .tracking(0.8)
                    .foregroundStyle(.white)
                    .accessibilityAddTraits(.isHeader)
                Text(shelf.count.formatted())
                    .font(.system(size: 13, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.42))
            }

            if shelf.kind == .chart {
                VStack(spacing: 10) {
                    ForEach(shelf.lists) { card in
                        FeaturedListButton(card: card, sourceKey: "\(mediaType)/\(shelf.key)", onSelect: onSelect) {
                            FeaturedChartRow(card: card)
                        }
                    }
                }
            } else {
                LazyVGrid(
                    columns: Array(repeating: GridItem(.fixed(columnWidth), spacing: Self.columnSpacing, alignment: .topLeading), count: 2),
                    alignment: .leading,
                    spacing: 20
                ) {
                    ForEach(shelf.lists) { card in
                        FeaturedListButton(card: card, sourceKey: "\(mediaType)/\(shelf.key)", onSelect: onSelect) {
                            FeaturedMediumTile(card: card, width: columnWidth)
                        }
                    }
                }
            }
        }
        .padding(.horizontal, Self.horizontalInset)
    }
}
