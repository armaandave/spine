import SwiftUI

@MainActor
@Observable
final class SeriesDetailViewModel {
    var detail: SeriesDetail?
    var isLoading = true
    var errorMessage: String?

    private let ref: SeriesRef
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        ref: SeriesRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            detail = try await mediaRepository.series(ref: ref)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func selection(for item: MediaSummary) -> MediaBrowsingSelection {
        MediaBrowsingSelection(
            ref: item.ref,
            within: detail?.items.map(\.ref) ?? [item.ref]
        )
    }
}

struct SeriesDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: SeriesDetailViewModel
    @State private var selectedMedia: MediaBrowsingSelection?
    @State private var edgeDragOffset: CGFloat = 0

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        ref: SeriesRef,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        peopleRepository: PeopleRepository = AppRepositories.current().people,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: SeriesDetailViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                content
                    .spineContentTransition(value: contentPhase)
            }
            .navigationTitle(viewModel.detail?.name ?? "Series")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    MediaDiscoverBackButton {
                        dismiss()
                    }
                }
            }
            .task {
                if viewModel.detail == nil {
                    await viewModel.load()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
                guard let changedRef = notification.userInfo?["ref"] as? MediaRef,
                      viewModel.detail?.items.contains(where: { $0.ref.id == changedRef.id }) == true
                else { return }
                Task { await viewModel.load() }
            }
        }
        .background(Color.black)
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .fullScreenCover(item: $selectedMedia, onDismiss: { selectedMedia = nil }) { selection in
            MediaDetailView(
                ref: selection.ref,
                browsingContext: selection.context,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading, viewModel.detail == nil {
            ProgressView()
                .tint(.white)
        } else if let error = viewModel.errorMessage, viewModel.detail == nil {
            ContentUnavailableView(
                "Could not load series",
                systemImage: "exclamationmark.triangle",
                description: Text(error)
            )
        } else if let detail = viewModel.detail, detail.items.isEmpty {
            ContentUnavailableView(
                emptyTitle(for: detail.mediaType),
                systemImage: emptyIcon(for: detail.mediaType)
            )
        } else if let detail = viewModel.detail {
            ScrollView {
                GeometryReader { proxy in
                    let columnWidth = (proxy.size.width - 24) / 4
                    let firstPosterInset = max(0, (columnWidth - PosterSlot.tagGrid.size.width) / 2)

                    HStack(spacing: 8) {
                        Text(itemCountTitle(for: detail))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(.white.opacity(0.56))

                        if let completion = detail.completion, completion.isVisible {
                            SWCompletionProgressButton(progress: completion)
                        }

                        Spacer()
                    }
                    .padding(.leading, firstPosterInset)
                }
                .frame(height: 44)
                .padding(.horizontal, 16)

                LazyVGrid(
                    columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4),
                    spacing: 10
                ) {
                    ForEach(detail.items) { item in
                        Button {
                            selectedMedia = viewModel.selection(for: item)
                        } label: {
                            VStack(alignment: .leading, spacing: 7) {
                                MediaArtwork(
                                    url: item.displayPosterURL,
                                    title: item.displayTitle,
                                    slot: .tagGrid,
                                    mediaType: item.ref.mediaType,
                                    orientation: item.posterOrientation
                                )
                                .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                                if detail.mediaType == "anime" {
                                    Text(item.displayTitle)
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundStyle(.white)
                                        .lineLimit(2)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(item.displayTitle)")
                        .prefetchingArtwork(after: item, in: detail.items) { $0.artworkRequest(slot: .tagGrid) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.top, 4)
            }
        }
    }

    private func itemCountTitle(for detail: SeriesDetail) -> String {
        let count = detail.itemCount
        let noun = switch detail.mediaType {
        case "movie": count == 1 ? "movie" : "movies"
        case "game": count == 1 ? "game" : "games"
        case "anime": "anime"
        default: count == 1 ? "book" : "books"
        }
        return "\(count.formatted()) \(noun)"
    }

    private func emptyTitle(for mediaType: String) -> String {
        switch mediaType {
        case "movie": "No movies"
        case "game": "No games"
        case "anime": "No anime installments"
        default: "No books"
        }
    }

    private func emptyIcon(for mediaType: String) -> String {
        switch mediaType {
        case "movie": "film"
        case "game": "gamecontroller"
        case "anime": "play.rectangle"
        default: "books.vertical"
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.detail != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }
}
