import SwiftUI
import UIKit

@MainActor
@Observable
private final class ProfileDiaryFilterViewModel {
    var entries: [DiaryEntry] = []
    var isLoading = true
    var errorMessage: String?

    private let filter: DiaryFilter
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(filter: DiaryFilter, diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.filter = filter
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            entries = try await diaryRepository.list(filter: filter)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct ProfileReviewsView: View {
    @State private var viewModel: ProfileDiaryFilterViewModel

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileDiaryFilterViewModel(
            filter: DiaryFilter(hasReview: true),
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ProfileDiaryEntriesScreen(
            title: "Reviews",
            emptyTitle: "No reviews yet",
            emptyMessage: "Reviews you write from media pages will appear here.",
            viewModel: viewModel,
            diaryRepository: diaryRepository,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }
}

struct ProfileLikesView: View {
    @State private var viewModel: ProfileLikesViewModel

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileLikesViewModel(
            profileRepository: profileRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 16) {
                    Group {
                        if viewModel.isLoading, viewModel.media.isEmpty {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage, viewModel.media.isEmpty {
                            DiaryStateCard(title: "Could not load likes", systemImage: "exclamationmark.triangle", message: error)
                        } else if viewModel.media.isEmpty {
                            DiaryStateCard(title: "No liked media yet", systemImage: "heart", message: "Media you like while logging will appear here.")
                        } else {
                            mediaGrid(viewModel.media)
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
        .navigationTitle("Likes")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.media.isEmpty {
                await viewModel.load()
            }
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: !viewModel.media.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    private func mediaGrid(_ media: [MediaSummary]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
            ForEach(media) { item in
                NavigationLink {
                    MediaDetailView(
                        ref: item.ref,
                        browsingContext: MediaBrowsingContext(
                            refs: media.map(\.ref),
                            selected: item.ref
                        ),
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        currentUserId: currentUserId,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    MediaArtwork(
                        url: item.displayPosterURL,
                        title: item.title,
                        slot: .tagGrid,
                        mediaType: item.ref.mediaType,
                        orientation: item.posterOrientation
                    )
                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(item.title)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

@MainActor
@Observable
private final class ProfileLikesViewModel {
    var media: [MediaSummary] = []
    var isLoading = true
    var errorMessage: String?

    private let profileRepository: ProfileRepository
    private let onUnauthorized: () -> Void

    init(profileRepository: ProfileRepository, onUnauthorized: @escaping () -> Void) {
        self.profileRepository = profileRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            media = try await profileRepository.likedMedia()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private struct ProfileDiaryEntriesScreen: View {
    let title: String
    let emptyTitle: String
    let emptyMessage: String
    let viewModel: ProfileDiaryFilterViewModel
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    Group {
                        if viewModel.isLoading, viewModel.entries.isEmpty {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage, viewModel.entries.isEmpty {
                            DiaryStateCard(title: "Could not load \(title.lowercased())", systemImage: "exclamationmark.triangle", message: error)
                        } else if viewModel.entries.isEmpty {
                            DiaryStateCard(title: emptyTitle, systemImage: "text.bubble", message: emptyMessage)
                        } else {
                            DiaryEntryList(entries: viewModel.entries) { entry in
                                DiaryLogDetailView(
                                    entryId: entry.id,
                                    diaryRepository: diaryRepository,
                                    mediaRepository: mediaRepository,
                                    trackingRepository: trackingRepository,
                                    currentUserId: currentUserId,
                                    selectedTab: selectedTab,
                                    onSelectTab: onSelectTab,
                                    onUnauthorized: onUnauthorized
                                )
                            }
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
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.entries.isEmpty {
                await viewModel.load()
            }
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: !viewModel.entries.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }
}

@MainActor
@Observable
private final class ProfileTagsViewModel {
    var tags: [DiaryTagSuggestion] = []
    var isLoading = true
    var errorMessage: String?
    var totalTagUses: Int {
        tags.reduce(0) { $0 + $1.usageCount }
    }

    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            tags = try await diaryRepository.allTags(mine: true).sorted { lhs, rhs in
                if lhs.usageCount != rhs.usageCount {
                    return lhs.usageCount > rhs.usageCount
                }
                return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
            }
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func filteredTags(matching query: String) -> [DiaryTagSuggestion] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return tags }
        return tags.filter { $0.name.localizedCaseInsensitiveContains(trimmed) }
    }
}

struct ProfileTagsView: View {
    @State private var viewModel: ProfileTagsViewModel
    @State private var searchText = ""

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileTagsViewModel(diaryRepository: diaryRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 14) {
                    Group {
                        if viewModel.isLoading, viewModel.tags.isEmpty {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage, viewModel.tags.isEmpty {
                            DiaryStateCard(title: "Could not load tags", systemImage: "exclamationmark.triangle", message: error)
                        } else if viewModel.tags.isEmpty {
                            DiaryStateCard(title: "No tags yet", systemImage: "tag", message: "Tags you add while logging will appear here.")
                        } else {
                            tagsContent
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
        .navigationTitle("Tags")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.tags.isEmpty {
                await viewModel.load()
            }
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: !viewModel.tags.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    @ViewBuilder
    private var tagsContent: some View {
        ProfileTagsHeader(tagCount: viewModel.tags.count, totalUses: viewModel.totalTagUses)
        ProfileTagSearchField(text: $searchText)

        let filteredTags = viewModel.filteredTags(matching: searchText)
        if filteredTags.isEmpty {
            DiaryStateCard(
                title: "No matching tags",
                systemImage: "magnifyingglass",
                message: "Try another tag name."
            )
        } else {
            LazyVStack(spacing: 8) {
                ForEach(filteredTags, id: \.name) { tag in
                    NavigationLink {
                        TaggedDiaryView(
                            tag: tag.name,
                            diaryRepository: diaryRepository,
                            mediaRepository: mediaRepository,
                            trackingRepository: trackingRepository,
                            currentUserId: currentUserId,
                            selectedTab: selectedTab,
                            onSelectTab: onSelectTab,
                            onUnauthorized: onUnauthorized
                        )
                    } label: {
                        ProfileTagRow(tag: tag)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

private struct ProfileTagsHeader: View {
    let tagCount: Int
    let totalUses: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(tagCount) \(tagCount == 1 ? "tag" : "tags")")
                .font(.system(size: 28, weight: .black))
                .foregroundStyle(.white)

            Text("\(totalUses) total \(totalUses == 1 ? "log" : "logs") tagged")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)
    }
}

private struct ProfileTagSearchField: View {
    @Binding var text: String
    var placeholder = "Search tags"

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.white.opacity(0.54))

            TextField(placeholder, text: $text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .foregroundStyle(.white)
                .submitLabel(.search)

            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.48))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear tag search")
            }
        }
        .font(.system(size: 15, weight: .semibold))
        .padding(.horizontal, 13)
        .frame(height: 44)
        .background(.white.opacity(0.08), in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(0.08))
        }
    }
}

private struct ProfileTagRow: View {
    let tag: DiaryTagSuggestion

    var body: some View {
        HStack(spacing: 8) {
            Text(tag.name)
                .lineLimit(1)

            Spacer(minLength: 8)

            Text("\(tag.usageCount)")
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(.white.opacity(0.72))
                .padding(.horizontal, 7)
                .frame(height: 22)
                .background(.white.opacity(0.12), in: Capsule())
        }
        .font(.system(size: 16, weight: .bold))
        .foregroundStyle(.white.opacity(0.88))
        .padding(.leading, 16)
        .padding(.trailing, 10)
        .frame(height: 52)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.white.opacity(0.11), in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(0.08))
        }
        .contentShape(Capsule())
        .accessibilityLabel("\(tag.name), \(tag.usageCount) \(tag.usageCount == 1 ? "log" : "logs")")
    }
}

@MainActor
@Observable
private final class ProfileListsViewModel {
    var lists: [CustomListSummary] = []
    var isLoading = true
    var isSaving = false
    var errorMessage: String?

    private let listRepository: ListRepository
    private let onUnauthorized: () -> Void
    private var hasLoaded = false
    private var requestGeneration = 0

    init(listRepository: ListRepository, onUnauthorized: @escaping () -> Void) {
        self.listRepository = listRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        requestGeneration += 1
        let generation = requestGeneration
        isLoading = !hasLoaded
        errorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoading = false
            }
        }

        do {
            let loadedLists = try await listRepository.list()
            guard generation == requestGeneration else { return }
            lists = loadedLists
            hasLoaded = true
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

    func create(_ request: CustomListWriteRequest) async -> Bool {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            _ = try await listRepository.create(request)
            await load()
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }
}

struct ProfileListsView: View {
    @State private var viewModel: ProfileListsViewModel
    @State private var searchText = ""
    @State private var presentedForm: ListComposerMode?
    @State private var pendingCreatedListID: Int?
    @State private var createdListDestination: ProfileCreatedListDestination?

    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let profileRepository: ProfileRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let activityRepository: ActivityRepository
    private let importCoordinator: LetterboxdImportCoordinator?
    private let storygraphImportCoordinator: StoryGraphImportCoordinator?
    private let goodreadsImportCoordinator: GoodreadsImportCoordinator?
    private let myAnimeListImportCoordinator: MyAnimeListImportCoordinator?
    private let currentUserId: Int?
    private let onLogout: () -> Void
    private let onOpenDiary: () -> Void
    private let onOpenLibrary: (LibraryShelf) -> Void
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
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
        myAnimeListImportCoordinator: MyAnimeListImportCoordinator? = nil,
        currentUserId: Int? = nil,
        onLogout: @escaping () -> Void = {},
        onOpenDiary: @escaping () -> Void = {},
        onOpenLibrary: @escaping (LibraryShelf) -> Void = { _ in },
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
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
        self.myAnimeListImportCoordinator = myAnimeListImportCoordinator
        self.currentUserId = currentUserId
        self.onLogout = onLogout
        self.onOpenDiary = onOpenDiary
        self.onOpenLibrary = onOpenLibrary
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileListsViewModel(listRepository: listRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: 12) {
                    Group {
                        if viewModel.isLoading, viewModel.lists.isEmpty {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage, viewModel.lists.isEmpty {
                            DiaryStateCard(title: "Could not load lists", systemImage: "exclamationmark.triangle", message: error)
                        } else if viewModel.lists.isEmpty {
                            DiaryStateCard(title: "No lists yet", systemImage: "list.bullet.rectangle", message: "Custom lists you create will appear here.")
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
        .navigationTitle("Lists")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Media List", systemImage: "rectangle.stack") {
                        presentedForm = .create(.media)
                    }
                    Button("People List", systemImage: "person.2") {
                        presentedForm = .create(.people)
                    }
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("Create list")
                .disabled(viewModel.isLoading)
            }
        }
        .fullScreenCover(item: $presentedForm, onDismiss: openCreatedListIfNeeded) { mode in
            ListComposerView(
                mode: mode,
                listRepository: listRepository,
                mediaRepository: mediaRepository,
                peopleRepository: peopleRepository,
                onUnauthorized: onUnauthorized
            ) { listID, _ in
                pendingCreatedListID = listID
                Task { await viewModel.load() }
            }
        }
        .navigationDestination(item: $createdListDestination) { destination in
            listDetailDestination(listID: destination.id)
        }
        .task {
            if viewModel.lists.isEmpty {
                await viewModel.load()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .customListsDidChange)) { _ in
            Task { await viewModel.load() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { _ in
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
        ProfileTagSearchField(text: $searchText, placeholder: "Search lists")

        if filteredLists.isEmpty {
            DiaryStateCard(
                title: "No matching lists",
                systemImage: "magnifyingglass",
                message: "Try another list name."
            )
        } else {
            ForEach(filteredLists) { list in
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
            }
        }
    }

    private var filteredLists: [CustomListSummary] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return viewModel.lists }
        return viewModel.lists.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    private func openCreatedListIfNeeded() {
        guard let listID = pendingCreatedListID else { return }
        pendingCreatedListID = nil
        createdListDestination = ProfileCreatedListDestination(id: listID)
    }

    private func listDetailDestination(listID: Int) -> some View {
        ProfileListDetailView(
            listId: listID,
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
            myAnimeListImportCoordinator: myAnimeListImportCoordinator,
            currentUserId: currentUserId,
            onLogout: onLogout,
            onOpenDiary: onOpenDiary,
            onOpenLibrary: onOpenLibrary,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }
}

private struct ProfileCreatedListDestination: Identifiable, Hashable {
    let id: Int
}

struct ProfileListRow: View {
    let list: CustomListSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Text(list.name)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(2)

                Spacer(minLength: 0)

                Text(countLabel)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.52))
                    .lineLimit(1)
                    .padding(.top, 3)

                if list.listType == .people {
                    Text("People")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.68))
                        .padding(.horizontal, 7)
                        .frame(height: 21)
                        .background(.white.opacity(0.1), in: Capsule())
                        .padding(.top, 1)
                }

                if list.isRanked {
                    Text("Ranked")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.68))
                        .padding(.horizontal, 7)
                        .frame(height: 21)
                        .background(.white.opacity(0.1), in: Capsule())
                        .padding(.top, 1)
                }

                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white.opacity(0.24))
                    .padding(.top, 4)
            }

            CustomListPreviewStrip(list: list)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }

    private var countLabel: String {
        list.countLabel
    }
}

struct ProfileListRowContainer<PrimaryControl: View>: View {
    private let primaryControl: PrimaryControl

    init(@ViewBuilder primaryControl: () -> PrimaryControl) {
        self.primaryControl = primaryControl()
    }

    var body: some View {
        primaryControl
            .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(0.045), lineWidth: 1)
        }
    }
}

private extension CustomListSummary {
    var countLabel: String {
        let count = listType == .people ? entriesCount : itemsCount
        let noun = listType == .people
            ? (count == 1 ? "person" : "people")
            : (count == 1 ? "item" : "items")
        return "\(count.formatted()) \(noun)"
    }

    var accessibilityLabel: String {
        "\(name), \(countLabel)"
    }
}

@MainActor
@Observable
final class ProfileListDetailViewModel {
    var list: CustomListDetail?
    var filter = MediaFilterState()
    var filterOptions: MediaFilterOptionsResponse = .empty
    var filteredItems: [MediaSummary] = []
    var people: [PersonListEntry] = []
    var isLoading = true
    var isLoadingFilteredItems = false
    var isSaving = false
    var errorMessage: String?
    var nextPageErrorMessage: String?

    private let listId: Int
    private let listRepository: ListRepository
    private let filterOptionsRepository: FilterOptionsRepository
    private let onUnauthorized: () -> Void
    private var nextPage: String?
    private var requestGeneration = 0

    init(
        listId: Int,
        listRepository: ListRepository,
        filterOptionsRepository: FilterOptionsRepository? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.listId = listId
        self.listRepository = listRepository
        self.filterOptionsRepository = filterOptionsRepository ?? APIFilterOptionsRepository(client: AppEnvironment.apiClient)
        self.onUnauthorized = onUnauthorized
    }

    var displayedItems: [MediaSummary] {
        filteredItems
    }

    var displayedPeople: [PersonListEntry] {
        people
    }

    func load() async {
        requestGeneration += 1
        let generation = requestGeneration
        isLoading = list == nil
        isLoadingFilteredItems = false
        errorMessage = nil
        nextPageErrorMessage = nil
        let requestFilter = filter
        defer {
            if generation == requestGeneration {
                isLoading = false
            }
        }

        do {
            let detail = try await listRepository.detail(id: listId)
            switch detail.listType {
            case .media:
                let response = try await listRepository.items(listId: listId, page: nil, filter: requestFilter)
                guard generation == requestGeneration, requestFilter == filter else { return }
                filteredItems = response.results
                people = []
                nextPage = APIPageCursor.nextPage(from: response.next)
                list = detail.withItems(response.results)
                Task { await loadFilterOptions() }
            case .people:
                let response = try await listRepository.people(listId: listId, page: nil)
                guard generation == requestGeneration else { return }
                filteredItems = []
                filter = MediaFilterState()
                filterOptions = .empty
                people = Self.deduplicatedPeople(response.results)
                nextPage = APIPageCursor.nextPage(from: response.next)
                list = detail.withPeople(people)
            }
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration, requestFilter == filter else { return }
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func loadFilterOptions() async {
        guard list?.listType != .people else { return }
        do {
            filterOptions = try await filterOptionsRepository.options(scope: .list(id: listId), filter: filter)
        } catch {
            filterOptions = .empty
        }
    }

    func loadFilteredItems(reset: Bool) async {
        guard list?.listType != .people else { return }
        if reset {
            requestGeneration += 1
            filteredItems = []
            nextPage = nil
        }
        let generation = requestGeneration
        let requestFilter = filter
        isLoadingFilteredItems = true
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingFilteredItems = false
            }
        }

        do {
            let response = try await listRepository.items(listId: listId, page: reset ? nil : nextPage, filter: requestFilter)
            guard generation == requestGeneration, requestFilter == filter else { return }
            if reset {
                filteredItems = response.results
            } else {
                let existingIDs = Set(filteredItems.map(\.id))
                filteredItems += response.results.filter { !existingIDs.contains($0.id) }
            }
            nextPage = APIPageCursor.nextPage(from: response.next)
            if !requestFilter.isActive || list?.items.isEmpty == true {
                list = list?.withItems(filteredItems)
            }
        } catch {
            guard generation == requestGeneration, requestFilter == filter else { return }
            nextPageErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func loadNextFilteredPageIfNeeded(currentItem: MediaSummary) async {
        guard nextPage != nil, !isLoadingFilteredItems,
              let thresholdIndex = filteredItems.index(filteredItems.endIndex, offsetBy: -8, limitedBy: filteredItems.startIndex) ?? filteredItems.indices.first,
              let currentIndex = filteredItems.firstIndex(where: { $0.id == currentItem.id }),
              currentIndex >= thresholdIndex else {
            return
        }
        await loadFilteredItems(reset: false)
    }

    func loadNextPeoplePageIfNeeded(currentPerson: PersonListEntry) async {
        guard list?.listType == .people,
              nextPage != nil,
              !isLoadingFilteredItems,
              let thresholdIndex = people.index(people.endIndex, offsetBy: -8, limitedBy: people.startIndex) ?? people.indices.first,
              let currentIndex = people.firstIndex(where: { $0.entryId == currentPerson.entryId }),
              currentIndex >= thresholdIndex else {
            return
        }
        await loadNextPeoplePage()
    }

    func retryNextPage() async {
        if list?.listType == .people {
            await loadNextPeoplePage()
        } else {
            await loadFilteredItems(reset: false)
        }
    }

    private func loadNextPeoplePage() async {
        guard let page = nextPage, !isLoadingFilteredItems else { return }
        let generation = requestGeneration
        isLoadingFilteredItems = true
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingFilteredItems = false
            }
        }

        do {
            let response = try await listRepository.people(listId: listId, page: page)
            guard generation == requestGeneration else { return }
            let existingIDs = Set(people.map(\.entryId))
            people += response.results.filter { !existingIDs.contains($0.entryId) }
            nextPage = APIPageCursor.nextPage(from: response.next)
            list = list?.withPeople(people)
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

    func loadAllItemsForEditing() async -> Bool {
        guard let currentList = list else { return false }
        if currentList.listType == .people {
            guard currentList.people.count < currentList.entriesCount else { return true }
        } else {
            guard currentList.items.count < currentList.itemsCount else { return true }
        }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            switch currentList.listType {
            case .media:
                var items: [MediaSummary] = []
                var page: String?
                repeat {
                    let response = try await listRepository.items(listId: listId, page: page, filter: MediaFilterState())
                    let existingIDs = Set(items.map(\.id))
                    items += response.results.filter { !existingIDs.contains($0.id) }
                    page = APIPageCursor.nextPage(from: response.next)
                } while page != nil
                list = currentList.withItems(items)
                if !filter.isActive {
                    filteredItems = items
                    nextPage = nil
                }
            case .people:
                var loadedPeople: [PersonListEntry] = []
                var page: String?
                repeat {
                    let response = try await listRepository.people(listId: listId, page: page)
                    let existingIDs = Set(loadedPeople.map(\.entryId))
                    loadedPeople += response.results.filter { !existingIDs.contains($0.entryId) }
                    page = APIPageCursor.nextPage(from: response.next)
                } while page != nil
                people = loadedPeople
                list = currentList.withPeople(loadedPeople)
                nextPage = nil
            }
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func update(_ request: CustomListWriteRequest) async -> Bool {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let updatedList = try await listRepository.update(id: listId, request)
            list = updatedList
            if updatedList.listType == .people {
                people = updatedList.people
                filteredItems = []
                nextPage = nil
            } else if !filter.isActive {
                filteredItems = updatedList.items
                nextPage = nil
            }
            CustomListChange.post(listId: listId, listType: updatedList.listType)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func deleteList() async -> Bool {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            try await listRepository.delete(id: listId)
            CustomListChange.post(listId: listId, listType: list?.listType ?? .media)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func remove(_ item: MediaSummary) async {
        guard let itemId = item.ref.itemId else { return }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            try await listRepository.removeItem(listId: listId, itemId: itemId)
            await load()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func move(from source: IndexSet, to destination: Int) async {
        guard var items = list?.items else { return }
        items.move(fromOffsets: source, toOffset: destination)
        guard items.allSatisfy({ $0.ref.itemId != nil }) else { return }
        list = list?.withItems(items)
        if !filter.isActive {
            filteredItems = items
            nextPage = nil
        }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let updatedList = try await listRepository.reorderItems(listId: listId, itemIds: items.compactMap(\.ref.itemId))
            list = updatedList
            if !filter.isActive {
                filteredItems = updatedList.items
            }
        } catch {
            errorMessage = error.localizedDescription
            await load()
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private static func deduplicatedPeople(_ people: [PersonListEntry]) -> [PersonListEntry] {
        var seen = Set<Int>()
        return people.filter { seen.insert($0.entryId).inserted }
    }
}

private extension CustomListDetail {
    func withItems(_ items: [MediaSummary]) -> CustomListDetail {
        CustomListDetail(
            id: id,
            name: name,
            slug: slug,
            description: description,
            tags: tags,
            visibility: visibility,
            isRanked: isRanked,
            listType: listType,
            owner: owner,
            imageUrl: imageUrl,
            itemsCount: itemsCount,
            peopleCount: peopleCount,
            entriesCount: entriesCount,
            updatedAt: updatedAt,
            likeCount: likeCount,
            items: items,
            people: people,
            completion: completion
        )
    }

    func withPeople(_ people: [PersonListEntry]) -> CustomListDetail {
        CustomListDetail(
            id: id,
            name: name,
            slug: slug,
            description: description,
            tags: tags,
            visibility: visibility,
            isRanked: isRanked,
            listType: listType,
            owner: owner,
            imageUrl: imageUrl,
            itemsCount: itemsCount,
            peopleCount: peopleCount,
            entriesCount: entriesCount,
            updatedAt: updatedAt,
            likeCount: likeCount,
            items: items,
            people: people,
            completion: completion
        )
    }
}

struct ProfileListDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var viewModel: ProfileListDetailViewModel
    @State private var presentedForm: ListComposerMode?
    @State private var selectedMedia: MediaBrowsingSelection?
    @State private var selectedPerson: PersonRef?
    @State private var isDeleteAlertPresented = false
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var edgeDragOffset: CGFloat = 0

    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let profileRepository: ProfileRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let activityRepository: ActivityRepository
    private let importCoordinator: LetterboxdImportCoordinator?
    private let storygraphImportCoordinator: StoryGraphImportCoordinator?
    private let goodreadsImportCoordinator: GoodreadsImportCoordinator?
    private let myAnimeListImportCoordinator: MyAnimeListImportCoordinator?
    private let currentUserId: Int?
    private let onLogout: () -> Void
    private let onOpenDiary: () -> Void
    private let onOpenLibrary: (LibraryShelf) -> Void
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        listId: Int,
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
        myAnimeListImportCoordinator: MyAnimeListImportCoordinator? = nil,
        currentUserId: Int? = nil,
        onLogout: @escaping () -> Void = {},
        onOpenDiary: @escaping () -> Void = {},
        onOpenLibrary: @escaping (LibraryShelf) -> Void = { _ in },
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
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
        self.myAnimeListImportCoordinator = myAnimeListImportCoordinator
        self.currentUserId = currentUserId
        self.onLogout = onLogout
        self.onOpenDiary = onOpenDiary
        self.onOpenLibrary = onOpenLibrary
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileListDetailViewModel(
            listId: listId,
            listRepository: listRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        let backdropURL = viewModel.list.flatMap { list in
            list.listType == .media
                ? CustomListBackdropSelection.artworkURL(from: list.items)
                : nil
        }

        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 14) {
                    Group {
                        if viewModel.isLoading, viewModel.list == nil {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                                .padding(.horizontal, 14)
                                .padding(
                                    .top,
                                    CustomListHeaderLayout.topPadding(
                                        hasBackdrop: false,
                                        topSafeAreaInset: topSafeAreaInset
                                    ) + 12
                                )
                        } else if let error = viewModel.errorMessage, viewModel.list == nil {
                            DiaryStateCard(title: "Could not load list", systemImage: "exclamationmark.triangle", message: error)
                                .padding(.horizontal, 14)
                                .padding(
                                    .top,
                                    CustomListHeaderLayout.topPadding(
                                        hasBackdrop: false,
                                        topSafeAreaInset: topSafeAreaInset
                                    ) + 12
                                )
                        } else if let list = viewModel.list {
                            listHeader(list, backdropURL: backdropURL)
                                .padding(
                                    .top,
                                    CustomListHeaderLayout.topPadding(
                                        hasBackdrop: backdropURL != nil,
                                        topSafeAreaInset: topSafeAreaInset
                                    )
                                )
                            if list.listType == .people, viewModel.displayedPeople.isEmpty {
                                DiaryStateCard(
                                    title: "No people yet",
                                    systemImage: "person.2",
                                    message: "Add people from any person page."
                                )
                                .padding(.horizontal, 14)
                            } else if list.listType == .people {
                                peopleGrid(viewModel.displayedPeople)
                                    .padding(.horizontal, 14)
                                filteredPaginationFooter
                                    .padding(.horizontal, 14)
                            } else if viewModel.displayedItems.isEmpty {
                                DiaryStateCard(
                                    title: viewModel.filter.isActive ? "No matching items" : "No items yet",
                                    systemImage: "square.grid.2x2",
                                    message: viewModel.filter.isActive ? "Try changing or resetting the filters." : "Add items from any media detail page."
                                )
                                .padding(.horizontal, 14)
                            } else {
                                mediaGrid(viewModel.displayedItems)
                                    .padding(.horizontal, 14)
                                filteredPaginationFooter
                                    .padding(.horizontal, 14)
                            }
                        }
                    }
                    .spineContentTransition(value: contentPhase)
                }
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(
                edges: CustomListHeaderLayout.ignoredSafeAreaEdges(
                    hasBackdrop: backdropURL != nil
                )
            )

            topButtons
                .padding(.horizontal, 16)
                .padding(.top, topSafeAreaInset + CustomListHeaderLayout.topControlTopPadding)
        }
        .navigationBarBackButtonHidden()
        .toolbar(.hidden, for: .navigationBar)
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: ProfileListTopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(ProfileListTopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
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
        .fullScreenCover(item: $selectedPerson, onDismiss: {
            selectedPerson = nil
            Task { await viewModel.load() }
        }) { ref in
            PersonDetailView(
                ref: ref,
                peopleRepository: peopleRepository,
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
        .fullScreenCover(item: $presentedForm) { mode in
            ListComposerView(
                mode: mode,
                listRepository: listRepository,
                mediaRepository: mediaRepository,
                peopleRepository: peopleRepository,
                onUnauthorized: onUnauthorized
            ) { _, _ in
                Task { await viewModel.load() }
            }
        }
        .alert("Delete List?", isPresented: $isDeleteAlertPresented) {
            Button("Delete", role: .destructive) {
                Task {
                    if await viewModel.deleteList() {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(deleteConfirmationMessage)
        }
        .task {
            if viewModel.list == nil {
                await viewModel.load()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .customListsDidChange)) { notification in
            guard CustomListChange.listId(from: notification) == viewModel.list?.id,
                  !viewModel.isSaving else { return }
            Task { await viewModel.load() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
            guard let changedRef = notification.userInfo?["ref"] as? MediaRef else { return }
            let containsMedia = viewModel.displayedItems.contains { $0.ref.id == changedRef.id }
            let isViewingPerson = viewModel.list?.listType == .people && selectedPerson != nil
            guard containsMedia || isViewingPerson else { return }
            Task { await viewModel.load() }
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.list != nil,
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

    private var topButtons: some View {
        HStack {
            ProfileListCircleIconButton(systemName: "chevron.left", label: "Back") {
                dismiss()
            }

            Spacer()

            if let list = viewModel.list {
                if list.listType == .media {
                MediaFilterButton(
                    filter: $viewModel.filter,
                    scope: .list(id: list.id),
                    options: viewModel.filterOptions
                ) {
                    Task {
                        await viewModel.loadFilterOptions()
                        await viewModel.loadFilteredItems(reset: true)
                    }
                }
                }

                Menu {
                    Button("Edit List", systemImage: "slider.horizontal.3") {
                        Task {
                            if await viewModel.loadAllItemsForEditing(),
                               let list = viewModel.list {
                                presentedForm = .edit(list)
                            }
                        }
                    }
                    Button("Delete List", systemImage: "trash", role: .destructive) {
                        isDeleteAlertPresented = true
                    }
                } label: {
                    ProfileListCircleIconLabel(systemName: "ellipsis")
                }
                .buttonStyle(.plain)
                .disabled(viewModel.isSaving)
            }
        }
    }

    private func listHeader(_ list: CustomListDetail, backdropURL: String?) -> some View {
        ZStack(alignment: .bottomLeading) {
            if let backdropURL {
                ProfileListBackdropArtwork(urlString: backdropURL)
            }

            listHeaderText(list)
                .padding(.horizontal, 18)
                .padding(.bottom, 20)
                .padding(.top, backdropURL == nil ? 18 : topSafeAreaInset + 112)
        }
        .frame(maxWidth: .infinity, minHeight: backdropURL == nil ? nil : topSafeAreaInset + 408, alignment: .bottomLeading)
    }

    private func listHeaderText(_ list: CustomListDetail) -> some View {
        let isOwnerCurrentUser = list.owner.id == currentUserId

        return VStack(alignment: .leading, spacing: 7) {
            NavigationLink {
                ProfileView(
                    profileRepository: profileRepository,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    activityRepository: activityRepository,
                    listRepository: listRepository,
                    peopleRepository: peopleRepository,
                    importCoordinator: importCoordinator,
                    storygraphImportCoordinator: storygraphImportCoordinator,
                    goodreadsImportCoordinator: goodreadsImportCoordinator,
                    myAnimeListImportCoordinator: myAnimeListImportCoordinator,
                    currentUserId: currentUserId,
                    onLogout: onLogout,
                    onOpenDiary: onOpenDiary,
                    onOpenLibrary: onOpenLibrary,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    username: isOwnerCurrentUser ? nil : list.owner.username,
                    isPushedProfile: true,
                    onUnauthorized: onUnauthorized
                )
            } label: {
                HStack(spacing: 9) {
                    SpineAsyncImage(url: URL(string: list.owner.avatarUrl ?? "")) { phase in
                        if case let .success(image) = phase {
                            image
                                .resizable()
                                .scaledToFill()
                        } else {
                            Image(systemName: "person.fill")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundStyle(.white.opacity(0.54))
                        }
                    }
                    .frame(width: 21, height: 21)
                    .background(.white.opacity(0.12), in: Circle())
                    .clipShape(Circle())

                    Text(list.owner.username)
                        .font(.system(size: 18, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.72))
                        .lineLimit(1)
                }
            }
            .buttonStyle(.plain)
            .shadow(color: .black.opacity(0.28), radius: 10, y: 5)

            Text(list.name)
                .font(.system(size: 34, weight: .black))
                .foregroundStyle(.white)
                .lineLimit(3)
                .minimumScaleFactor(0.72)
                .shadow(color: .black.opacity(0.35), radius: 14, y: 8)

            HStack(spacing: 8) {
                Text(detailCountLabel(list))
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.72))

                if list.listType == .people {
                    Text("People")
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.76))
                        .padding(.horizontal, 8)
                        .frame(height: 22)
                        .background(.white.opacity(0.13), in: Capsule())
                }

                if list.isRanked {
                    Text("Ranked")
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.76))
                        .padding(.horizontal, 8)
                        .frame(height: 22)
                        .background(.white.opacity(0.13), in: Capsule())
                }

                if list.listType == .media,
                   let completion = list.completion,
                   completion.isVisible {
                    SWCompletionProgressButton(progress: completion, isCompact: true)
                        .accessibilityLabel("\(list.name) completion")
                        .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                }
            }

            if !list.tags.isEmpty {
                tagRow(list.tags)
            }

            let description = list.description.trimmingCharacters(in: .whitespacesAndNewlines)
            if !description.isEmpty {
                Text(description)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.72))
                    .lineLimit(4)
                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
            }
        }
    }

    private func tagRow(_ tags: [String]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(tags, id: \.self) { tag in
                    Text(tag)
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.76))
                        .padding(.horizontal, 8)
                        .frame(height: 22)
                        .background(.white.opacity(0.13), in: Capsule())
                }
            }
        }
    }

    private func mediaGrid(_ items: [MediaSummary]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
            ForEach(items) { item in
                Button {
                    selectedMedia = MediaBrowsingSelection(ref: item.ref, within: items.map(\.ref))
                } label: {
                    VStack(spacing: 5) {
                        MediaArtwork(
                            url: item.displayPosterURL,
                            title: item.title,
                            slot: .tagGrid,
                            mediaType: item.ref.mediaType,
                            orientation: item.posterOrientation
                        )
                        .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                        if viewModel.list?.isRanked == true, let position = item.position {
                            Text("\(position)")
                                .font(.system(size: 12, weight: .heavy))
                                .monospacedDigit()
                                .foregroundStyle(.white.opacity(0.72))
                                .frame(maxWidth: .infinity)
                        }
                    }
                }
                .buttonStyle(.plain)
                .task {
                    await viewModel.loadNextFilteredPageIfNeeded(currentItem: item)
                }
            }
        }
    }

    private func peopleGrid(_ people: [PersonListEntry]) -> some View {
        let isAccessibilitySize = dynamicTypeSize.isAccessibilitySize
        let columnCount = PeopleListGridLayout.columnCount(isAccessibilitySize: isAccessibilitySize)
        let columns = Array(
            repeating: GridItem(.flexible(), spacing: isAccessibilitySize ? 12 : 8),
            count: columnCount
        )

        return LazyVGrid(columns: columns, spacing: isAccessibilitySize ? 18 : 14) {
            ForEach(people, id: \.entryId) { person in
                VStack(spacing: 7) {
                    Button {
                        selectedPerson = person.ref
                    } label: {
                        VStack(spacing: 7) {
                            ZStack(alignment: .topLeading) {
                                PersonArtwork(
                                    urlString: person.profileUrl,
                                    name: person.name,
                                    size: PeopleListGridLayout.artworkSize(
                                        isAccessibilitySize: isAccessibilitySize
                                    )
                                )
                                .shadow(color: .black.opacity(0.28), radius: 10, y: 5)

                                if viewModel.list?.isRanked == true, let position = person.position {
                                    Text("#\(position)")
                                        .font(.system(size: 11, weight: .heavy, design: .rounded))
                                        .foregroundStyle(.black)
                                        .padding(.horizontal, 7)
                                        .frame(height: 22)
                                        .background(.white, in: Capsule())
                                }
                            }

                            Text(person.name)
                                .font(.system(size: isAccessibilitySize ? 14 : 12, weight: .heavy, design: .rounded))
                                .foregroundStyle(.white.opacity(0.94))
                                .multilineTextAlignment(.center)
                                .lineLimit(2)
                                .frame(maxWidth: .infinity)

                            if let department = person.knownForDepartment {
                                Text(department)
                                    .font(.system(size: isAccessibilitySize ? 11 : 10, weight: .semibold, design: .rounded))
                                    .foregroundStyle(.white.opacity(0.5))
                                    .lineLimit(1)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .top)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(personAccessibilityLabel(person))
                    .accessibilityHint("Opens person details")

                }
                .task {
                    await viewModel.loadNextPeoplePageIfNeeded(currentPerson: person)
                }
            }
        }
    }

    private func detailCountLabel(_ list: CustomListDetail) -> String {
        let count = list.listType == .people ? list.entriesCount : list.itemsCount
        let noun = list.listType == .people
            ? (count == 1 ? "person" : "people")
            : (count == 1 ? "item" : "items")
        return "\(count.formatted()) \(noun)"
    }

    private func personAccessibilityLabel(_ person: PersonListEntry) -> String {
        let rank = viewModel.list?.isRanked == true
            ? person.position.map { "Rank \($0), " } ?? ""
            : ""
        let department = person.knownForDepartment.map { ", \($0)" } ?? ""
        return "\(rank)\(person.name)\(department)"
    }

    private var deleteConfirmationMessage: String {
        if viewModel.list?.listType == .people {
            return "This removes the list. The people themselves are unaffected."
        }
        return "This removes the list. Items stay in your library."
    }

    @ViewBuilder
    private var filteredPaginationFooter: some View {
        Group {
            if viewModel.isLoadingFilteredItems {
                ProgressView()
                    .tint(.white)
            } else if let error = viewModel.nextPageErrorMessage {
                VStack(spacing: 10) {
                    DiaryStateCard(title: "Could not load more", systemImage: "exclamationmark.triangle", message: error)
                    Button("Retry") {
                        Task { await viewModel.retryNextPage() }
                    }
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 14)
                    .frame(height: 36)
                    .background(.white, in: Capsule())
                }
            } else {
                Color.clear
            }
        }
        .frame(maxWidth: .infinity, minHeight: 56)
        .spineContentTransition(value: filteredPaginationPhase)
    }

    private var filteredPaginationPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingFilteredItems,
            hasContent: false,
            hasError: viewModel.nextPageErrorMessage != nil
        )
    }
}

enum CustomListBackdropSelection {
    static func artworkURL(from items: [MediaSummary]) -> String? {
        items.lazy.compactMap { item -> String? in
            guard item.ref.mediaType == "movie" || item.ref.mediaType == "tv" else { return nil }
            let url = item.displayBackdropURL?.trimmingCharacters(in: .whitespacesAndNewlines)
            return url?.isEmpty == false ? url : nil
        }.first
    }
}

enum CustomListHeaderLayout {
    static let topControlTopPadding: CGFloat = 6
    static let topControlSize: CGFloat = 38

    static func ignoredSafeAreaEdges(hasBackdrop: Bool) -> Edge.Set {
        hasBackdrop ? .top : []
    }

    static func topPadding(hasBackdrop: Bool, topSafeAreaInset: CGFloat) -> CGFloat {
        hasBackdrop
            ? BackdropLayout.topOffset
            : topSafeAreaInset + topControlTopPadding + topControlSize
    }
}

enum PeopleListGridLayout {
    static func columnCount(isAccessibilitySize: Bool) -> Int {
        isAccessibilitySize ? 2 : 4
    }

    static func artworkSize(isAccessibilitySize: Bool) -> CGFloat {
        isAccessibilitySize ? 104 : 76
    }
}

private struct ProfileListTopSafeAreaInsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct ProfileListCircleIconButton: View {
    let systemName: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ProfileListCircleIconLabel(systemName: systemName)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

private struct ProfileListCircleIconLabel: View {
    let systemName: String

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: 17, weight: .bold))
            .foregroundStyle(.white)
            .frame(
                width: CustomListHeaderLayout.topControlSize,
                height: CustomListHeaderLayout.topControlSize
            )
            .background(.black.opacity(0.34), in: Circle())
    }
}

private struct ProfileListBackdropArtwork: View {
    let urlString: String
    private let pageBackground = Color(red: 0.07, green: 0.07, blue: 0.065)

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                SpineAsyncImage(url: URL(string: urlString)) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        Color.clear
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.48), location: 0),
                        .init(color: .black.opacity(0.2), location: 0.42),
                        .init(color: pageBackground.opacity(0.1), location: 0.68),
                        .init(color: pageBackground, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .mask(
                LinearGradient(
                    stops: [
                        .init(color: .white, location: 0),
                        .init(color: .white, location: 0.58),
                        .init(color: .white.opacity(0.34), location: 0.82),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
        }
        .clipped()
    }
}

private enum CustomListFormMode: Identifiable {
    case create
    case edit(CustomListDetail)

    var id: String {
        switch self {
        case .create: "create"
        case let .edit(list): "edit-\(list.id)"
        }
    }

    var title: String {
        switch self {
        case .create: "New List"
        case .edit: "Edit List"
        }
    }
}

private struct CustomListFormSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var description: String
    @State private var visibility: String
    @State private var isRanked: Bool
    @State private var errorMessage: String?

    let mode: CustomListFormMode
    let currentList: CustomListDetail?
    let isSaving: Bool
    let onDeleteItem: (MediaSummary) async -> Void
    let onMoveItem: (IndexSet, Int) async -> Void
    let onSave: (CustomListWriteRequest) async -> Bool

    init(
        mode: CustomListFormMode,
        currentList: CustomListDetail? = nil,
        isSaving: Bool,
        onDeleteItem: @escaping (MediaSummary) async -> Void = { _ in },
        onMoveItem: @escaping (IndexSet, Int) async -> Void = { _, _ in },
        onSave: @escaping (CustomListWriteRequest) async -> Bool
    ) {
        self.mode = mode
        self.currentList = currentList
        self.isSaving = isSaving
        self.onDeleteItem = onDeleteItem
        self.onMoveItem = onMoveItem
        self.onSave = onSave
        switch mode {
        case .create:
            _name = State(initialValue: "")
            _description = State(initialValue: "")
            _visibility = State(initialValue: "private")
            _isRanked = State(initialValue: false)
        case let .edit(list):
            _name = State(initialValue: list.name)
            _description = State(initialValue: list.description)
            _visibility = State(initialValue: list.visibility)
            _isRanked = State(initialValue: list.isRanked)
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 22) {
                    detailsSection
                    settingsSection

                    if let editableList, !editableList.items.isEmpty {
                        editableItemsSection(editableList)
                    }

                    if let errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.red)
                            .padding(14)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 24)
                .padding(.bottom, 34)
            }
            .background(Color.black)
            .navigationTitle(mode.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            await save()
                        }
                    } label: {
                        if isSaving {
                            ProgressView()
                        } else {
                            Text("Save")
                        }
                    }
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSaving)
                }
            }
        }
    }

    private var detailsSection: some View {
        VStack(spacing: 0) {
            TextField("Name", text: $name)
                .font(.system(size: 15, weight: .semibold))
                .padding(.vertical, 13)

            Divider()
                .overlay(.white.opacity(0.12))

            TextField("Description", text: $description, axis: .vertical)
                .font(.system(size: 15, weight: .medium))
                .lineLimit(3, reservesSpace: true)
                .padding(.vertical, 13)
        }
        .padding(.horizontal, 14)
        .background(Color.white.opacity(0.16), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var settingsSection: some View {
        VStack(spacing: 0) {
            Picker("Visibility", selection: $visibility) {
                Text("Public").tag("public")
                Text("Private").tag("private")
            }
            .font(.system(size: 15, weight: .semibold))
            .padding(.vertical, 9)

            Divider()
                .overlay(.white.opacity(0.12))

            Toggle("Ranked", isOn: $isRanked)
                .font(.system(size: 15, weight: .semibold))
                .padding(.vertical, 9)
        }
        .padding(.horizontal, 14)
        .background(Color.white.opacity(0.16), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var editableList: CustomListDetail? {
        switch mode {
        case .create:
            nil
        case let .edit(list):
            currentList ?? list
        }
    }

    @ViewBuilder
    private func editableItemsSection(_ list: CustomListDetail) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Items")
                .font(.system(size: 15, weight: .heavy))
                .foregroundStyle(.white.opacity(0.52))
                .padding(.horizontal, 2)

            if isRanked {
                CustomListRankedReorderRows(
                    items: list.items,
                    isSaving: isSaving,
                    onDeleteItem: onDeleteItem,
                    onMoveItem: onMoveItem
                )
            } else {
                VStack(spacing: 8) {
                    ForEach(list.items) { item in
                        CustomListEditableItemRow(
                            item: item,
                            isRanked: isRanked,
                            showsDeleteButton: true,
                            onDelete: {
                                Task { await onDeleteItem(item) }
                            }
                        )
                        .frame(height: CustomListReorderMath.rowHeight)
                    }
                }
            }
        }
        .disabled(isSaving)
    }

    private func save() async {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { return }
        errorMessage = nil
        let request = CustomListWriteRequest(
            name: trimmedName,
            description: description.trimmingCharacters(in: .whitespacesAndNewlines),
            visibility: visibility,
            isRanked: isRanked
        )
        if await onSave(request) {
            dismiss()
        } else {
            errorMessage = "Could not save list."
        }
    }
}

private struct CustomListRankedReorderRows: View {
    let items: [MediaSummary]
    let isSaving: Bool
    let onDeleteItem: (MediaSummary) async -> Void
    let onMoveItem: (IndexSet, Int) async -> Void

    @State private var draggedItemId: String?
    @State private var sourceIndex: Int?
    @State private var dragTranslation = 0.0
    @State private var targetIndex: Int?

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                CustomListEditableItemRow(
                    item: item,
                    isRanked: true,
                    showsDeleteButton: true,
                    showsReorderHandle: true,
                    onDelete: {
                        Task { await onDeleteItem(item) }
                    },
                    onReorderChanged: { value in
                        handleReorderChanged(value, item: item, at: index)
                    },
                    onReorderEnded: { value in
                        handleReorderEnded(value)
                    }
                )
                .frame(height: CustomListReorderMath.rowHeight)
                .offset(
                    x: 0,
                    y: Double(index) * CustomListReorderMath.rowHeight
                        + CustomListReorderMath.rowOffset(
                            for: index,
                            sourceIndex: sourceIndex,
                            targetIndex: targetIndex,
                            activeTranslation: dragTranslation,
                            rowHeight: CustomListReorderMath.rowHeight
                        )
                )
                .zIndex(draggedItemId == item.id ? 10 : 0)
                .shadow(color: draggedItemId == item.id ? .black.opacity(0.26) : .clear, radius: 12, y: 6)
                .transaction { transaction in
                    if draggedItemId == item.id {
                        transaction.animation = nil
                    }
                }
                .animation(draggedItemId == item.id ? nil : .snappy(duration: 0.14), value: targetIndex)
            }
        }
        .frame(height: Double(items.count) * CustomListReorderMath.rowHeight, alignment: .topLeading)
        .disabled(isSaving)
    }

    private func handleReorderChanged(_ value: DragGesture.Value, item: MediaSummary, at index: Int) {
        if draggedItemId == nil {
            draggedItemId = item.id
            sourceIndex = index
            targetIndex = index
            UIImpactFeedbackGenerator(style: .light).prepare()
        }
        guard let sourceIndex else { return }
        let translation = CustomListReorderMath.clampedTranslation(
            Double(value.translation.height),
            sourceIndex: sourceIndex,
            count: items.count
        )
        let nextTargetIndex = CustomListReorderMath.targetIndex(
            from: sourceIndex,
            translation: translation,
            count: items.count
        )
        dragTranslation = translation
        if nextTargetIndex != targetIndex {
            targetIndex = nextTargetIndex
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }

    private func handleReorderEnded(_ value: DragGesture.Value) {
        guard let sourceIndex else { resetDrag(); return }
        let destination = CustomListReorderMath.destination(
            from: sourceIndex,
            translation: Double(value.translation.height),
            count: items.count
        )
        resetDrag()
        guard destination != sourceIndex else { return }
        Task {
            await onMoveItem(IndexSet(integer: sourceIndex), destination)
        }
    }

    private func resetDrag() {
        draggedItemId = nil
        sourceIndex = nil
        dragTranslation = 0
        targetIndex = nil
    }
}

private struct CustomListEditableItemRow: View {
    let item: MediaSummary
    let isRanked: Bool
    var showsDeleteButton = false
    var showsReorderHandle = false
    var onDelete: () -> Void = {}
    var onReorderChanged: (DragGesture.Value) -> Void = { _ in }
    var onReorderEnded: (DragGesture.Value) -> Void = { _ in }

    var body: some View {
        HStack(spacing: 12) {
            MediaArtwork(
                url: item.displayPosterURL,
                title: item.title,
                slot: .diaryRow,
                mediaType: item.ref.mediaType,
                orientation: item.posterOrientation
            )
            .scaleEffect(0.75)
            .frame(width: 42, height: 63)

            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(2)

                if isRanked, let position = item.position {
                    Text("#\(position)")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.5))
                }
            }

            Spacer(minLength: 8)

            if showsDeleteButton {
                Button(role: .destructive, action: onDelete) {
                    Image(systemName: "trash")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.red.opacity(0.78))
                        .frame(width: 36, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Remove \(item.title)")
            }

            if showsReorderHandle {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.52))
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 4)
                            .onChanged(onReorderChanged)
                            .onEnded(onReorderEnded)
                    )
                    .accessibilityLabel("Reorder \(item.title)")
            }
        }
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 12))
        .listRowBackground(Color.white.opacity(0.055))
    }
}

enum CustomListReorderMath {
    static let rowHeight = 79.0

    static func clampedTranslation(
        _ translation: Double,
        sourceIndex: Int,
        count: Int,
        rowHeight: Double = rowHeight
    ) -> Double {
        guard count > 1 else { return 0 }
        let minTranslation = -Double(sourceIndex) * rowHeight
        let maxTranslation = Double(count - sourceIndex - 1) * rowHeight
        return min(max(translation, minTranslation), maxTranslation)
    }

    static func targetIndex(
        from sourceIndex: Int,
        translation: Double,
        count: Int,
        rowHeight: Double = rowHeight
    ) -> Int {
        guard count > 1 else { return sourceIndex }
        let clamped = clampedTranslation(
            translation,
            sourceIndex: sourceIndex,
            count: count,
            rowHeight: rowHeight
        )
        return min(
            max(sourceIndex + Int((clamped / rowHeight).rounded()), 0),
            count - 1
        )
    }

    static func destination(
        from sourceIndex: Int,
        translation: Double,
        count: Int,
        rowHeight: Double = rowHeight
    ) -> Int {
        let targetIndex = Self.targetIndex(
            from: sourceIndex,
            translation: translation,
            count: count,
            rowHeight: rowHeight
        )
        return targetIndex > sourceIndex ? targetIndex + 1 : targetIndex
    }

    static func rowOffset(
        for index: Int,
        sourceIndex: Int?,
        targetIndex: Int?,
        activeTranslation: Double,
        rowHeight: Double = rowHeight
    ) -> Double {
        guard let sourceIndex, let targetIndex else { return 0 }
        if index == sourceIndex {
            return activeTranslation
        }
        if targetIndex > sourceIndex, index > sourceIndex, index <= targetIndex {
            return -rowHeight
        }
        if targetIndex < sourceIndex, index >= targetIndex, index < sourceIndex {
            return rowHeight
        }
        return 0
    }
}
