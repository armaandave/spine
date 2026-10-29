import SwiftUI

@MainActor
@Observable
final class TaggedListsViewModel {
    var lists: [CustomListSummary] = []
    var totalCount = 0
    var isLoading = true
    var isLoadingNextPage = false
    var errorMessage: String?
    var nextPageErrorMessage: String?

    private let tag: String
    private let owner: String
    private let listRepository: ListRepository
    private let onUnauthorized: () -> Void
    private var nextPage: String?
    private var requestGeneration = 0

    /// `owner` is the username whose lists the tag covers.
    init(tag: String, owner: String, listRepository: ListRepository, onUnauthorized: @escaping () -> Void) {
        self.tag = tag
        self.owner = owner
        self.listRepository = listRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        requestGeneration += 1
        let generation = requestGeneration
        isLoading = true
        // A next page still in flight belongs to the old generation, so it won't clear this itself.
        isLoadingNextPage = false
        errorMessage = nil
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoading = false
            }
        }

        do {
            let response = try await listRepository.tagged(tag, owner: owner, page: nil)
            guard generation == requestGeneration else { return }
            lists = response.results
            totalCount = response.count
            nextPage = APIPageCursor.nextPage(from: response.next)
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func loadNextPageIfNeeded(currentList: CustomListSummary) async {
        guard let thresholdIndex = lists.index(lists.endIndex, offsetBy: -4, limitedBy: lists.startIndex) ?? lists.indices.first,
              let currentIndex = lists.firstIndex(where: { $0.id == currentList.id }),
              currentIndex >= thresholdIndex else {
            return
        }
        await loadNextPage()
    }

    func loadNextPage() async {
        guard !isLoading, !isLoadingNextPage, let page = nextPage else { return }
        let generation = requestGeneration
        isLoadingNextPage = true
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingNextPage = false
            }
        }

        do {
            let response = try await listRepository.tagged(tag, owner: owner, page: page)
            guard generation == requestGeneration else { return }
            let existingIDs = Set(lists.map(\.id))
            lists += response.results.filter { !existingIDs.contains($0.id) }
            totalCount = response.count
            nextPage = APIPageCursor.nextPage(from: response.next)
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            nextPageErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

/// Where a list page's tag leads: that list owner's lists with the tag.
struct TaggedListsView: View {
    @State private var viewModel: TaggedListsViewModel

    private let tag: String
    private let owner: UserSummary
    private let profileRepository: ProfileRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let activityRepository: ActivityRepository
    private let importCoordinator: LetterboxdImportCoordinator?
    private let storygraphImportCoordinator: StoryGraphImportCoordinator?
    private let goodreadsImportCoordinator: GoodreadsImportCoordinator?
    private let marginsImportCoordinator: MarginsImportCoordinator?
    private let myAnimeListImportCoordinator: MyAnimeListImportCoordinator?
    private let currentUserId: Int?
    private let onLogout: () -> Void
    private let onOpenDiary: () -> Void
    private let onOpenLibrary: (LibraryShelf) -> Void
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        tag: String,
        owner: UserSummary,
        profileRepository: ProfileRepository,
        listRepository: ListRepository,
        peopleRepository: PeopleRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        activityRepository: ActivityRepository,
        importCoordinator: LetterboxdImportCoordinator? = nil,
        storygraphImportCoordinator: StoryGraphImportCoordinator? = nil,
        goodreadsImportCoordinator: GoodreadsImportCoordinator? = nil,
        marginsImportCoordinator: MarginsImportCoordinator? = nil,
        myAnimeListImportCoordinator: MyAnimeListImportCoordinator? = nil,
        currentUserId: Int? = nil,
        onLogout: @escaping () -> Void = {},
        onOpenDiary: @escaping () -> Void = {},
        onOpenLibrary: @escaping (LibraryShelf) -> Void = { _ in },
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        let trimmedTag = tag.trimmingCharacters(in: .whitespacesAndNewlines)
        self.tag = trimmedTag
        self.owner = owner
        self.profileRepository = profileRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.activityRepository = activityRepository
        self.importCoordinator = importCoordinator
        self.storygraphImportCoordinator = storygraphImportCoordinator
        self.goodreadsImportCoordinator = goodreadsImportCoordinator
        self.marginsImportCoordinator = marginsImportCoordinator
        self.myAnimeListImportCoordinator = myAnimeListImportCoordinator
        self.currentUserId = currentUserId
        self.onLogout = onLogout
        self.onOpenDiary = onOpenDiary
        self.onOpenLibrary = onOpenLibrary
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: TaggedListsViewModel(
            tag: trimmedTag,
            owner: owner.username,
            listRepository: listRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 12) {
                    Group {
                        if viewModel.isLoading, viewModel.lists.isEmpty {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage, viewModel.lists.isEmpty {
                            DiaryStateCard(title: "Could not load lists", systemImage: "exclamationmark.triangle", message: error)
                        } else if viewModel.lists.isEmpty {
                            DiaryStateCard(
                                title: "No lists tagged \(tag)",
                                systemImage: "tag",
                                message: "Lists with this tag will appear here."
                            )
                        } else {
                            listsContent
                        }
                    }
                    .spineContentTransition(value: contentPhase)
                }
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle(tag)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .task {
            if viewModel.lists.isEmpty {
                await viewModel.load()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .customListsDidChange)) { _ in
            Task { await viewModel.load() }
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: !viewModel.lists.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    @ViewBuilder
    private var listsContent: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(viewModel.totalCount.formatted()) \(viewModel.totalCount == 1 ? "list" : "lists")")
                .font(.system(size: 28, weight: .black))
                .foregroundStyle(.white)

            if owner.id != currentUserId {
                Text("by \(owner.username)")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.58))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)

        ForEach(viewModel.lists) { list in
            ProfileListRowContainer {
                NavigationLink {
                    ProfileListDetailView(
                        listId: list.id,
                        profileRepository: profileRepository,
                        listRepository: listRepository,
                        peopleRepository: peopleRepository,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        activityRepository: activityRepository,
                        importCoordinator: importCoordinator,
                        storygraphImportCoordinator: storygraphImportCoordinator,
                        goodreadsImportCoordinator: goodreadsImportCoordinator,
                        marginsImportCoordinator: marginsImportCoordinator,
                        myAnimeListImportCoordinator: myAnimeListImportCoordinator,
                        currentUserId: currentUserId,
                        onLogout: onLogout,
                        onOpenDiary: onOpenDiary,
                        onOpenLibrary: onOpenLibrary,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    ProfileListRow(list: list)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(list.accessibilityLabel)
                .accessibilityHint("Opens list")
            }
            .task {
                await viewModel.loadNextPageIfNeeded(currentList: list)
            }
        }

        paginationFooter
    }

    @ViewBuilder
    private var paginationFooter: some View {
        if viewModel.isLoadingNextPage {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 56)
        } else if let error = viewModel.nextPageErrorMessage {
            VStack(spacing: 10) {
                DiaryStateCard(title: "Could not load more", systemImage: "exclamationmark.triangle", message: error)
                Button("Retry") {
                    Task { await viewModel.loadNextPage() }
                }
                .font(.system(size: 13, weight: .heavy, design: .rounded))
                .foregroundStyle(.black)
                .padding(.horizontal, 14)
                .frame(height: 36)
                .background(.white, in: Capsule())
            }
        }
    }
}
