import SwiftUI

/// What Search shows while the field is idle: with "All Media", the Now strip and one shelf per media type; with
/// one media type, that type's lists grouped by kind.
struct FeaturedLanding: View {
    let store: FeaturedListsStore
    let selectedMediaType: String
    let enabledMediaTypes: [String]
    let onSelect: (FeaturedListSelection) -> Void

    var body: some View {
        if selectedMediaType == APIConstants.allMedia {
            FeaturedShelvesLanding(store: store, enabledMediaTypes: enabledMediaTypes, onSelect: onSelect)
        } else {
            FeaturedTypeLayer(store: store, mediaType: selectedMediaType, onSelect: onSelect)
                .id(selectedMediaType)
        }
    }
}

private struct FeaturedShelvesLanding: View {
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
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 30) {
                            ForEach(shelves) { shelf in
                                if shelf.isNow {
                                    FeaturedNowStrip(shelf: shelf, onSelect: onSelect)
                                } else {
                                    FeaturedShelfSection(store: store, shelf: shelf, onSelect: onSelect)
                                }
                            }
                        }
                        .padding(.top, 14)
                        .padding(.bottom, 28)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .refreshable {
                        await store.load(mediaType: nil, force: true)
                    }
                }
            } else if let error = layer.errorMessage {
                FeaturedErrorState(message: error) {
                    Task { await store.load(mediaType: nil, force: true) }
                }
            } else {
                FeaturedLandingSkeleton()
            }
        }
        .featuredLayerLifecycle(store: store, mediaType: nil)
    }
}

/// "Now": live lists and freshly changed charts, as paged chart cards.
private struct FeaturedNowStrip: View {
    let shelf: FeaturedShelf
    let onSelect: (FeaturedListSelection) -> Void

    var body: some View {
        SpineSection(title: shelf.title, headerInset: 16) {
            if shelf.liveCount > 0 || shelf.lists.contains(where: { $0.isLive(at: .now) }) {
                FeaturedLiveDot()
            }
        } trailing: {
            EmptyView()
        } content: {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 10) {
                    ForEach(shelf.lists) { card in
                        FeaturedListButton(card: card, sourceKey: shelf.key, showsTopEntries: true, onSelect: onSelect) {
                            FeaturedChartCard(card: card)
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .contentMargins(.horizontal, 16, for: .scrollContent)
            .scrollTargetBehavior(.viewAligned)
        }
    }
}

/// One media type's shelf (or "Across Media"): up to 12 tiles, then a "See all" end cap.
private struct FeaturedShelfSection: View {
    static let maxTiles = 12

    let store: FeaturedListsStore
    let shelf: FeaturedShelf
    let onSelect: (FeaturedListSelection) -> Void

    var body: some View {
        let cards = Array(shelf.lists.prefix(Self.maxTiles))
        SpineSection(title: shelf.title, headerInset: 16) {
            MediaTypeGlyph(theme: MediaTypeTheme.theme(for: glyphType), size: 12)
                .accessibilityHidden(true)
        } trailing: {
            HStack(spacing: 10) {
                Text(shelf.count.formatted())
                    .font(.system(size: 13, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.42))
                    .accessibilityLabel("\(shelf.count) lists")

                if hasSeeAll {
                    NavigationLink {
                        seeAllDestination
                    } label: {
                        HStack(spacing: 3) {
                            Text("See all")
                            Image(systemName: "chevron.right")
                                .font(.system(size: 11, weight: .bold))
                        }
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.72))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("See all \(shelf.title)")
                }
            }
        } content: {
            SpineRail(items: cards, spacing: 10, leadingInset: 16, trailingInset: 16) { card in
                FeaturedListButton(card: card, sourceKey: shelf.key, onSelect: onSelect) {
                    FeaturedListTile(card: card, showsMediaTypes: shelf.isMixed)
                }
                .onAppear {
                    ArtworkPrefetch.prefetch(
                        ArtworkPrefetch.upcoming(after: card, in: cards).prefix(3).flatMap { $0.tileArtworkRequests() }
                    )
                }
            } trailing: {
                if hasSeeAll, shelf.count > cards.count {
                    NavigationLink {
                        seeAllDestination
                    } label: {
                        FeaturedSeeAllTile(count: shelf.count)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var glyphType: String {
        shelf.isMixed ? APIConstants.allMedia : (shelf.mediaType ?? APIConstants.allMedia)
    }

    private var hasSeeAll: Bool {
        shelf.seeAll != nil && shelf.mediaType != nil
    }

    private var seeAllDestination: some View {
        FeaturedShelfDetailView(
            store: store,
            mediaType: shelf.mediaType ?? shelf.key,
            title: shelf.title,
            onSelect: onSelect
        )
    }
}

extension View {
    /// Loads a featured layer when it appears, revalidates it when the app returns from the background, and polls
    /// it while it's on screen and one of its lists is live, or an upcoming window is still to start.
    func featuredLayerLifecycle(store: FeaturedListsStore, mediaType: String?) -> some View {
        modifier(FeaturedLayerLifecycle(store: store, mediaType: mediaType))
    }
}

private struct FeaturedLayerLifecycle: ViewModifier {
    @Environment(\.scenePhase) private var scenePhase
    @State private var wasInBackground = false

    let store: FeaturedListsStore
    let mediaType: String?

    func body(content: Content) -> some View {
        content
            .task(id: mediaType) {
                await store.load(mediaType: mediaType)
            }
            .task(id: pollID) {
                guard pollID.polls else { return }
                await store.pollWhileLive(mediaType: mediaType)
            }
            .onChange(of: scenePhase) { _, phase in
                switch phase {
                case .background:
                    wasInBackground = true
                case .active where wasInBackground:
                    wasInBackground = false
                    // The normal staleness rule, so every layer still in the navigation stack doesn't refetch on
                    // each app switch; a live list always refetches.
                    Task { await store.load(mediaType: mediaType, force: store.hasLiveList(mediaType: mediaType)) }
                default:
                    break
                }
            }
    }

    private var pollID: PollID {
        // An upcoming window counts, so polling starts when it does without waiting for a re-render.
        PollID(mediaType: mediaType, polls: scenePhase == .active && store.nextPollDelay(mediaType: mediaType) != nil)
    }

    private struct PollID: Hashable {
        let mediaType: String?
        let polls: Bool
    }
}

struct FeaturedErrorState: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Could not load featured lists", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
        } actions: {
            Button("Try Again", action: retry)
                .buttonStyle(.bordered)
                .tint(.white)
        }
    }
}

struct FeaturedEmptyPrompt: View {
    var body: some View {
        ContentUnavailableView(
            "Search Spine",
            systemImage: "magnifyingglass",
            description: Text("Enter a title to find media.")
        )
    }
}

private struct FeaturedLandingSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 30) {
            ForEach(0 ..< 3, id: \.self) { _ in
                VStack(alignment: .leading, spacing: 12) {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(.white.opacity(0.08))
                        .frame(width: 96, height: 12)
                    HStack(spacing: 10) {
                        ForEach(0 ..< 3, id: \.self) { _ in
                            VStack(alignment: .leading, spacing: 8) {
                                RoundedRectangle(cornerRadius: 8, style: .continuous)
                                    .fill(.white.opacity(0.07))
                                    .frame(width: 148, height: 81)
                                RoundedRectangle(cornerRadius: 4)
                                    .fill(.white.opacity(0.07))
                                    .frame(width: 110, height: 12)
                                RoundedRectangle(cornerRadius: 4)
                                    .fill(.white.opacity(0.05))
                                    .frame(width: 70, height: 10)
                            }
                        }
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .clipped()
        .accessibilityLabel("Loading featured lists")
    }
}
