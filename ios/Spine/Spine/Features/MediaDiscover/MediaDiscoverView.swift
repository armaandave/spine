import SwiftUI

@MainActor
@Observable
final class MediaDiscoverViewModel {
    var results: [MediaSummary] = []
    var isLoading = false
    var isLoadingNextPage = false
    var errorMessage: String?
    /// The server needs the user to choose their streaming services before On my services can work.
    var needsStreamingServices = false
    var totalCount = 0

    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private(set) var request: MediaDiscoverRequest
    /// The genre, year, or platform pill Discover opened with.
    let pillFilter: MediaDiscoverRequest.Filter?
    private var nextPage: String?
    private var hasAttemptedInitialLoad = false

    init(request: MediaDiscoverRequest, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.request = request
        self.pillFilter = request.filter
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    var hasMorePages: Bool {
        nextPage != nil
    }

    var isLoadingInitial: Bool {
        !hasAttemptedInitialLoad || isLoading
    }

    var isOnMyServices: Bool {
        request.service.isActive
    }

    var includesPillFilter: Bool {
        request.filter != nil
    }

    /// The pill filter can be dropped only while On my services keeps the results narrowed.
    var canTogglePillFilter: Bool {
        pillFilter != nil && isOnMyServices
    }

    func setOnMyServices(_ isOn: Bool) async {
        guard request.supportsServiceFilter, isOnMyServices != isOn else { return }
        request.service = isOn ? .mine : .any
        if !isOn, request.filter == nil {
            request.filter = pillFilter
        }
        await reloadForNewCriteria()
    }

    func setIncludesPillFilter(_ isOn: Bool) async {
        guard let pillFilter, includesPillFilter != isOn, isOn || isOnMyServices else { return }
        request.filter = isOn ? pillFilter : nil
        await reloadForNewCriteria()
    }

    func loadNextPageIfNeeded(currentItem: MediaSummary) async {
        guard shouldLoadNextPage(currentItem: currentItem) else { return }
        await loadNextPage()
    }

    func load() async {
        guard !isLoading else { return }
        request.page = nil
        isLoading = true
        errorMessage = nil
        needsStreamingServices = false
        defer { isLoading = false }

        do {
            let response = try await mediaRepository.discover(request)
            results = response.results
            totalCount = response.count
            nextPage = Self.page(from: response.next)
            hasAttemptedInitialLoad = true
        } catch is CancellationError {
            return
        } catch {
            hasAttemptedInitialLoad = true
            needsStreamingServices = isOnMyServices && StreamingFilterRules.isChooseServicesError(error)
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    /// New criteria start from an empty grid, so an error or empty result is never hidden behind old results.
    private func reloadForNewCriteria() async {
        results = []
        totalCount = 0
        nextPage = nil
        await load()
    }

    func loadNextPage() async {
        guard let nextPage, !isLoadingNextPage else { return }
        request.page = nextPage
        isLoadingNextPage = true
        defer { isLoadingNextPage = false }

        do {
            let response = try await mediaRepository.discover(request)
            let existing = Set(results.map(\.id))
            results += response.results.filter { !existing.contains($0.id) }
            totalCount = response.count
            self.nextPage = Self.page(from: response.next)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func shouldLoadNextPage(currentItem: MediaSummary) -> Bool {
        guard nextPage != nil,
              !isLoading,
              !isLoadingNextPage,
              let index = results.firstIndex(where: { $0.id == currentItem.id }) else { return false }
        return index >= results.count - 12
    }

    static func page(from urlString: String?) -> String? {
        guard let urlString,
              let components = URLComponents(string: urlString) else { return nil }
        return components.queryItems?.first(where: { $0.name == "page" })?.value
    }
}

struct MediaDiscoverView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaDiscoverViewModel
    @State private var selectedMedia: MediaBrowsingSelection?
    @State private var edgeDragOffset: CGFloat = 0
    @State private var isChoosingStreamingServices = false

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let streamingRepository: StreamingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        request: MediaDiscoverRequest,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        streamingRepository: StreamingRepository = AppRepositories.current().streaming,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.streamingRepository = streamingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: MediaDiscoverViewModel(
            request: request,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack(alignment: .topLeading) {
                Color.black.ignoresSafeArea()

                Group {
                    if viewModel.isLoadingInitial, viewModel.results.isEmpty {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if viewModel.needsStreamingServices, viewModel.results.isEmpty {
                        ContentUnavailableView {
                            Label("Choose your streaming services", systemImage: "play.tv")
                        } description: {
                            Text("Pick the services you subscribe to and see what's streaming on them.")
                        } actions: {
                            Button("Choose Services") {
                                isChoosingStreamingServices = true
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.white)
                            .foregroundStyle(.black)
                        }
                    } else if let error = viewModel.errorMessage, viewModel.results.isEmpty {
                        ContentUnavailableView("Could not load media", systemImage: "exclamationmark.triangle", description: Text(error))
                    } else if viewModel.results.isEmpty, viewModel.isOnMyServices {
                        ContentUnavailableView(
                            "Nothing streaming on your services",
                            systemImage: "play.tv",
                            description: Text("Turn off On my services to see everything.")
                        )
                    } else if viewModel.results.isEmpty {
                        ContentUnavailableView("No results", systemImage: "square.grid.2x2", description: Text("Try another media detail pill."))
                    } else {
                        ScrollView {
                            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
                                ForEach(viewModel.results) { media in
                                    Button {
                                        selectedMedia = MediaBrowsingSelection(
                                            ref: media.ref,
                                            within: viewModel.results.map(\.ref)
                                        )
                                    } label: {
                                        MediaArtwork(
                                            url: media.displayPosterURL,
                                            title: media.title,
                                            slot: .tagGrid,
                                            mediaType: media.ref.mediaType,
                                            orientation: media.posterOrientation
                                        )
                                        .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityLabel("View \(media.title)")
                                    .task {
                                        await viewModel.loadNextPageIfNeeded(currentItem: media)
                                    }
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 16)
                            .padding(.top, 14)
                        }
                    }
                }
                .spineContentTransition(value: contentPhase)
                .safeAreaInset(edge: .top, spacing: 0) {
                    if viewModel.request.supportsServiceFilter {
                        serviceFilterBar
                    }
                }
            }
            .navigationTitle(viewModel.request.title)
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
                if viewModel.results.isEmpty {
                    await viewModel.load()
                }
            }
            .sheet(isPresented: $isChoosingStreamingServices) {
                StreamingServicesSheet(repository: streamingRepository, onUnauthorized: onUnauthorized)
            }
            .onReceive(NotificationCenter.default.publisher(for: .streamingPreferencesDidChange)) { _ in
                guard viewModel.isOnMyServices else { return }
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
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingInitial,
            hasContent: !viewModel.results.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    /// The opening pill plus On my services; either can narrow the results alone (ST-302).
    private var serviceFilterBar: some View {
        let isBusy = viewModel.isLoading || viewModel.isLoadingNextPage

        return ScrollView(.horizontal, showsIndicators: false) {
            GlassEffectContainer(spacing: 8) {
                HStack(spacing: 8) {
                    if let pillFilter = viewModel.pillFilter {
                        MediaDiscoverFilterPill(
                            title: pillFilter.value,
                            systemImage: viewModel.includesPillFilter ? "checkmark" : "plus",
                            isSelected: viewModel.includesPillFilter
                        ) {
                            Task { await viewModel.setIncludesPillFilter(!viewModel.includesPillFilter) }
                        }
                        .allowsHitTesting(viewModel.canTogglePillFilter && !isBusy)
                    }

                    MediaDiscoverFilterPill(
                        title: "On my services",
                        systemImage: "play.tv",
                        isSelected: viewModel.isOnMyServices
                    ) {
                        Task { await viewModel.setOnMyServices(!viewModel.isOnMyServices) }
                    }
                    .allowsHitTesting(!isBusy)
                }
                .padding(.horizontal, 16)
            }
            .padding(.vertical, 6)
        }
        .background(Color.black)
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

private struct MediaDiscoverFilterPill: View {
    let title: String
    let systemImage: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white.opacity(isSelected ? 0.96 : 0.52))
                .padding(.horizontal, 15)
                .frame(minHeight: 44)
        }
        .buttonStyle(.plain)
        .glassEffect(
            isSelected
                ? .regular.tint(.white.opacity(0.14)).interactive()
                : .clear.interactive(),
            in: Capsule()
        )
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

struct MediaDiscoverBackButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Back")
    }
}
