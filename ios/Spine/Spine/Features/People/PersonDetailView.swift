import Foundation
import SwiftUI

private struct PersonTopSafeAreaInsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private enum PersonDetailSheet: Identifiable {
    case actions
    case addToList

    var id: String {
        switch self {
        case .actions:
            "actions"
        case .addToList:
            "addToList"
        }
    }
}

@MainActor
@Observable
final class PersonDetailViewModel {
    var detail: PersonDetail?
    var filmography: [MediaSummary] = []
    var filter = MediaFilterState()
    var filterOptions: MediaFilterOptionsResponse = .empty
    var isLoading = true
    var isLoadingNextPage = false
    var isPreparationPolling = false
    var preparationTimedOut = false
    var errorMessage: String?
    var nextPageErrorMessage: String?

    private let ref: PersonRef
    private let peopleRepository: PeopleRepository
    private let onUnauthorized: () -> Void
    private let pollInterval: Duration
    private let maxPollAttempts: Int
    private var requestGeneration = 0
    private var presentedFilter: MediaFilterState?
    private var pollingTask: Task<Void, Never>?

    var canLoadMore: Bool {
        detail?.creditsNextPage != nil
    }

    init(
        ref: PersonRef,
        peopleRepository: PeopleRepository,
        onUnauthorized: @escaping () -> Void,
        pollInterval: Duration = .seconds(5),
        maxPollAttempts: Int = 12
    ) {
        self.ref = ref
        self.peopleRepository = peopleRepository
        self.onUnauthorized = onUnauthorized
        self.pollInterval = pollInterval
        self.maxPollAttempts = maxPollAttempts
    }

    func load() async {
        stopPreparationPolling()
        requestGeneration += 1
        let generation = requestGeneration
        let requestFilter = filter
        if presentedFilter != requestFilter {
            detail = nil
            filmography = []
        }
        presentedFilter = requestFilter
        isLoading = true
        isLoadingNextPage = false
        errorMessage = nil
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration, requestFilter == filter {
                isLoading = false
            }
        }

        do {
            let loaded = try await peopleRepository.detail(
                ref: ref,
                filter: requestFilter,
                creditsPage: 1
            )
            guard generation == requestGeneration, requestFilter == filter else { return }
            apply(loaded, requestFilter: requestFilter)
            startPreparationPollingIfNeeded(requestFilter: requestFilter, generation: generation)
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

    func cancelPreparation() {
        requestGeneration += 1
        stopPreparationPolling()
    }

    func loadNextPage() async {
        guard let page = detail?.creditsNextPage, !isLoadingNextPage else { return }
        let generation = requestGeneration
        let requestFilter = filter
        isLoadingNextPage = true
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration, requestFilter == filter {
                isLoadingNextPage = false
            }
        }

        do {
            let loaded = try await peopleRepository.detail(
                ref: ref,
                filter: requestFilter,
                creditsPage: page
            )
            guard generation == requestGeneration, requestFilter == filter else { return }
            append(loaded, requestFilter: requestFilter)
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration, requestFilter == filter else { return }
            nextPageErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func reloadLoadedPages() async {
        let lastLoadedPage = max(detail?.creditsPage ?? 1, 1)
        await load()
        guard errorMessage == nil else { return }

        while canLoadMore, (detail?.creditsPage ?? 1) < lastLoadedPage {
            let previousPage = detail?.creditsPage
            await loadNextPage()
            guard detail?.creditsPage != previousPage else { return }
        }
    }

    private func apply(_ loaded: PersonDetail, requestFilter: MediaFilterState) {
        let loadedFilmography = Self.uniqueFilmography(from: loaded.filmography)
        detail = loaded
        filmography = loadedFilmography
        if let options = loaded.filterOptions {
            filterOptions = options
        } else if !requestFilter.isActive || filterOptions == .empty {
            filterOptions = Self.options(from: loadedFilmography)
        }
    }

    private func append(_ loaded: PersonDetail, requestFilter: MediaFilterState) {
        let previous = detail
        let mergedFilmography = Self.uniqueFilmography(
            from: filmography + loaded.filmography
        )
        let mergedSeries = Self.uniqueSeries(
            from: (previous?.series ?? []) + (loaded.series ?? [])
        )
        let creditsComplete = loaded.creditsComplete ?? previous?.creditsComplete
        let localCompletions = loaded.creditsNextPage == nil && loaded.creditsComplete == true
            ? Self.completions(from: mergedFilmography)
            : nil

        detail = PersonDetail(
            id: loaded.id,
            source: loaded.source,
            name: loaded.name,
            biography: loaded.biography ?? previous?.biography,
            profileUrl: loaded.profileUrl ?? previous?.profileUrl,
            knownForDepartment: loaded.knownForDepartment ?? previous?.knownForDepartment,
            birthDate: loaded.birthDate ?? previous?.birthDate,
            deathDate: loaded.deathDate ?? previous?.deathDate,
            placeOfBirth: loaded.placeOfBirth ?? previous?.placeOfBirth,
            popularity: loaded.popularity ?? previous?.popularity,
            filterOptions: loaded.filterOptions ?? previous?.filterOptions,
            ratingPreparation: loaded.ratingPreparation ?? previous?.ratingPreparation,
            creditsPage: loaded.creditsPage,
            creditsNextPage: loaded.creditsNextPage,
            creditsComplete: creditsComplete,
            series: mergedSeries.isEmpty ? nil : mergedSeries,
            credits: PersonCredits(cast: mergedFilmography),
            completion: loaded.completion ?? localCompletions?.overall,
            mediaTypeCompletions: loaded.mediaTypeCompletions ?? localCompletions?.byMediaType,
            roleCompletions: loaded.roleCompletions ?? localCompletions?.byRole
        )
        filmography = mergedFilmography

        if let options = loaded.filterOptions {
            filterOptions = options
        } else if !requestFilter.isActive || filterOptions == .empty {
            filterOptions = Self.options(from: mergedFilmography)
        }
    }

    private func startPreparationPollingIfNeeded(
        requestFilter: MediaFilterState,
        generation: Int
    ) {
        guard requestFilter.sort?.isExternalRating == true,
              detail?.ratingPreparation?.state == .pending else { return }
        preparationTimedOut = false
        isPreparationPolling = true
        pollingTask = Task { [weak self] in
            await self?.pollPreparation(requestFilter: requestFilter, generation: generation)
        }
    }

    private func pollPreparation(requestFilter: MediaFilterState, generation: Int) async {
        for _ in 0..<maxPollAttempts {
            do {
                try await Task.sleep(for: pollInterval)
                guard !Task.isCancelled,
                      generation == requestGeneration,
                      requestFilter == filter else { return }
                let requestedPage = detail?.creditsPage ?? 1
                let loaded = try await peopleRepository.detail(
                    ref: ref,
                    filter: requestFilter,
                    creditsPage: requestedPage
                )
                guard generation == requestGeneration, requestFilter == filter else { return }
                guard (loaded.creditsPage ?? 1) >= (detail?.creditsPage ?? 1) else {
                    continue
                }
                if requestedPage > 1 {
                    append(loaded, requestFilter: requestFilter)
                } else {
                    apply(loaded, requestFilter: requestFilter)
                }
                if loaded.ratingPreparation?.state != .pending {
                    stopPreparationPolling()
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                guard generation == requestGeneration, requestFilter == filter else { return }
                stopPreparationPolling()
                if case APIError.unauthorized = error {
                    onUnauthorized()
                } else {
                    preparationTimedOut = true
                }
                return
            }
        }
        guard generation == requestGeneration, requestFilter == filter else { return }
        isPreparationPolling = false
        preparationTimedOut = true
        pollingTask = nil
    }

    private func stopPreparationPolling() {
        pollingTask?.cancel()
        pollingTask = nil
        isPreparationPolling = false
    }

    static func uniqueFilmography(from media: [MediaSummary]) -> [MediaSummary] {
        var seen = Set<String>()
        return media.filter { seen.insert($0.id).inserted }
    }

    private static func uniqueSeries(from series: [MediaSeriesSummary]) -> [MediaSeriesSummary] {
        var seen = Set<String>()
        return series.filter {
            seen.insert("\($0.source):\($0.mediaType):\($0.id)").inserted
        }
    }

    private static func completions(from media: [MediaSummary]) -> (
        overall: CompletionProgress,
        byMediaType: [String: CompletionProgress],
        byRole: [String: [String: CompletionProgress]]
    ) {
        let byMediaType = Dictionary(grouping: media, by: \.ref.mediaType)
        var mediaTypeCompletions: [String: CompletionProgress] = [:]
        var roleCompletions: [String: [String: CompletionProgress]] = [:]

        for (mediaType, items) in byMediaType {
            mediaTypeCompletions[mediaType] = completion(from: items)
            var itemsByRole: [String: [MediaSummary]] = [:]
            for item in items {
                let roles = cleanedRoles(item.creditRoles)
                for role in roles.isEmpty ? ["Credits"] : roles {
                    itemsByRole[role, default: []].append(item)
                }
            }
            roleCompletions[mediaType] = itemsByRole.mapValues {
                completion(from: $0)
            }
        }

        return (
            completion(from: media),
            mediaTypeCompletions,
            roleCompletions
        )
    }

    private static func completion(from media: [MediaSummary]) -> CompletionProgress {
        CompletionProgress(
            completedCount: media.lazy.filter {
                $0.userState?.status == "Completed"
            }.count,
            totalCount: media.count
        )
    }

    private static func cleanedRoles(_ roles: [String]) -> [String] {
        var seen = Set<String>()
        return roles.compactMap { role in
            let cleaned = role.trimmingCharacters(in: .whitespacesAndNewlines)
            return cleaned.isEmpty || !seen.insert(cleaned).inserted
                ? nil
                : cleaned
        }
    }

    static func options(from media: [MediaSummary]) -> MediaFilterOptionsResponse {
        let years = Set(media.compactMap { item -> Int? in
            guard let releaseDate = item.releaseDate, releaseDate.count >= 4 else { return nil }
            return Int(releaseDate.prefix(4))
        })
        let genres = Set(media.flatMap(\.genres)).sorted()
        let languages = Set(media.flatMap(\.languages)).sorted()
        return MediaFilterOptionsResponse(
            sorts: [],
            genres: genres.map { FilterChoice(value: $0, label: $0) },
            languages: languages.map { FilterChoice(value: $0, label: $0) },
            years: years.sorted(by: >)
        )
    }
}

struct PersonDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: PersonDetailViewModel
    @State private var selectedMedia: MediaBrowsingSelection?
    @State private var selectedSeries: SeriesRef?
    @State private var selectedFilmographyType: FilmographyType = .movie
    @State private var expandedCreditRoles = Set<String>()
    @State private var edgeDragOffset: CGFloat = 0
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var presentedSheet: PersonDetailSheet?

    private let peopleRepository: PeopleRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        ref: PersonRef,
        peopleRepository: PeopleRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.peopleRepository = peopleRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: PersonDetailViewModel(
            ref: ref,
            peopleRepository: peopleRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            SpinePageBackground()

            content
                .spineContentTransition(value: contentPhase)

            HStack {
                PersonCircleIconButton(systemName: "chevron.left", label: "Back") {
                    dismiss()
                }
                .accessibilityIdentifier("person-detail.back")

                Spacer()

                if viewModel.detail != nil {
                    PersonCircleIconButton(systemName: "ellipsis", label: "More") {
                        presentedSheet = .actions
                    }
                    .accessibilityIdentifier("person-detail.more")
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
        .toolbar(.hidden, for: .tabBar)
        .navigationBarBackButtonHidden()
        .dismissExplorationOnReturnHome()
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .overlay(alignment: .bottomTrailing) {
            ExplorationHomeButton()
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: PersonTopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(PersonTopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
        .sheet(item: $presentedSheet) { sheet in
            switch sheet {
            case .actions:
                PersonActionSheet {
                    presentedSheet = .addToList
                }
                .presentationDetents([.height(80)])
                .presentationDragIndicator(.visible)
            case .addToList:
                if let detail = viewModel.detail {
                    AddToListSheet(
                        target: .person(detail.ref),
                        listRepository: listRepository,
                        mediaRepository: mediaRepository,
                        peopleRepository: peopleRepository,
                        onUnauthorized: onUnauthorized
                    )
                }
            }
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
        .fullScreenCover(item: $selectedSeries, onDismiss: { selectedSeries = nil }) { series in
            SeriesDetailView(
                ref: series,
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
        .task {
            if viewModel.detail == nil {
                await viewModel.load()
                syncSelectedFilmographyType()
                expandPrimaryCreditRole()
            }
        }
        .onDisappear {
            viewModel.cancelPreparation()
        }
        .onChange(of: selectedFilmographyType) { _, _ in
            expandPrimaryCreditRole()
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
            guard let changedRef = notification.userInfo?["ref"] as? MediaRef,
                  selectedSeries != nil || viewModel.filmography.contains(where: { $0.ref.id == changedRef.id })
            else { return }
            Task {
                await viewModel.reloadLoadedPages()
                syncSelectedFilmographyType()
            }
        }
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

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading, viewModel.detail == nil {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let detail = viewModel.detail {
            ScrollView(showsIndicators: false) {
                ZStack(alignment: .top) {
                    PersonHeroArtwork(urlString: detail.profileUrl)
                        .frame(height: topSafeAreaInset + 390)
                        .offset(y: BackdropLayout.topOffset)
                        .allowsHitTesting(false)

                    VStack(alignment: .leading, spacing: 26) {
                        hero(detail)
                        biographySection(detail)
                        filmographySection
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, topSafeAreaInset + 68)
                    .padding(.bottom, 36)
                }
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)
            .refreshable {
                await viewModel.reloadLoadedPages()
                syncSelectedFilmographyType()
            }
        } else if let error = viewModel.errorMessage, viewModel.detail == nil {
            ContentUnavailableView(
                "Could not load person",
                systemImage: "exclamationmark.triangle",
                description: Text(error)
            )
            .foregroundStyle(.white)
            .padding()
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.detail != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    private func hero(_ detail: PersonDetail) -> some View {
        VStack(spacing: 16) {
            PersonProfileImage(urlString: detail.profileUrl, name: detail.name)
                .frame(width: 156, height: 156)
                .shadow(color: .black.opacity(0.42), radius: 24, y: 14)

            VStack(spacing: 10) {
                Text(detail.name)
                    .font(.system(size: 34, weight: .black))
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
                    .minimumScaleFactor(0.72)
                    .frame(maxWidth: .infinity)

                personChips(detail)
            }
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private func personChips(_ detail: PersonDetail) -> some View {
        let chips = metadataChips(detail)
        if !chips.isEmpty {
            GeometryReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(chips, id: \.self) { chip in
                            Text(chip)
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(.white.opacity(0.82))
                                .lineLimit(1)
                                .padding(.horizontal, 11)
                                .frame(height: 31)
                                .background(.white.opacity(0.12), in: Capsule())
                        }
                    }
                    .frame(minWidth: proxy.size.width)
                }
                .mask(alignment: .trailing) {
                    LinearGradient(
                        stops: [
                            .init(color: .black, location: 0),
                            .init(color: .black, location: 0.9),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                }
            }
            .frame(height: 31)
        }
    }

    @ViewBuilder
    private func biographySection(_ detail: PersonDetail) -> some View {
        if let biography = clean(detail.biography) {
            SynopsisText(text: biography)
        }
    }

    private var filmographySection: some View {
        let types = FilmographyType.available(in: viewModel.filmography)
        let selectedType = types.contains(selectedFilmographyType) ? selectedFilmographyType : types.first ?? selectedFilmographyType
        let filmography = viewModel.filmography.filter { $0.ref.mediaType == selectedType.rawValue }
        let groups = FilmographyCreditGroup.groups(
            from: filmography,
            knownForDepartment: viewModel.detail?.knownForDepartment
        )

        return VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                PersonSectionLabel(title: selectedType.sectionTitle)
                    .accessibilityIdentifier("person-filmography-section-title")

                Spacer(minLength: 8)

                if let completion = mediaTypeCompletion(for: selectedType),
                   completion.isVisible {
                    SWCompletionProgressButton(progress: completion)
                        .accessibilityLabel("\(selectedType.sectionTitle) completion")
                        .accessibilityIdentifier("person-filmography-completion")
                }

                MediaFilterButton(
                    filter: $viewModel.filter,
                    scope: .person(ref: viewModel.detail?.ref ?? PersonRef(source: "", id: "")),
                    options: viewModel.filterOptions,
                    mediaTypes: types.map(\.rawValue)
                ) {
                    Task {
                        await viewModel.load()
                        if let mediaType = viewModel.filter.mediaType, let selectedType = FilmographyType(rawValue: mediaType) {
                            selectedFilmographyType = selectedType
                        }
                        syncSelectedFilmographyType()
                        expandPrimaryCreditRole()
                    }
                }

            }

            if types.count > 1 {
                Picker("Credit type", selection: $selectedFilmographyType) {
                    ForEach(types) { type in
                        Text(type.title).tag(type)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: .infinity)
            }

            ratingPreparationStatus

            Group {
                if filmography.isEmpty {
                    ContentUnavailableView(
                        "No \(selectedType.title.lowercased())",
                        systemImage: "square.grid.2x2",
                        description: Text("Credits will appear here when available.")
                    )
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity, minHeight: 220)
                } else {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        let series = viewModel.detail?.series(for: selectedType.rawValue) ?? []
                        if !series.isEmpty {
                            seriesDisclosureRow(series)
                        }

                        ForEach(groups) { group in
                            roleDisclosureRow(group, type: selectedType)
                        }

                        creditPagination
                    }
                }
            }
            .spineContentTransition(value: selectedType)
        }
    }

    @ViewBuilder
    private var creditPagination: some View {
        if viewModel.isLoadingNextPage {
            ProgressView()
                .controlSize(.small)
                .tint(.white.opacity(0.72))
                .frame(maxWidth: .infinity)
                .frame(height: 44)
        } else if viewModel.nextPageErrorMessage != nil {
            Button {
                Task {
                    await viewModel.loadNextPage()
                    syncSelectedFilmographyType()
                }
            } label: {
                Text("Retry loading credits")
                    .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white.opacity(0.78))
                .frame(maxWidth: .infinity)
                .frame(height: 44)
                .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .accessibilityHint(viewModel.nextPageErrorMessage ?? "")
        } else if viewModel.canLoadMore {
            Color.clear
                .frame(height: 1)
                .id(viewModel.detail?.creditsNextPage)
                .onAppear {
                    Task {
                        await viewModel.loadNextPage()
                        syncSelectedFilmographyType()
                    }
                }
        }
    }

    @ViewBuilder
    private var ratingPreparationStatus: some View {
        if viewModel.filter.sort?.isExternalRating == true,
           let preparation = viewModel.detail?.ratingPreparation,
           preparation.state != .ready {
            HStack(spacing: 8) {
                if preparation.state == .pending, viewModel.isPreparationPolling {
                    ProgressView()
                        .controlSize(.small)
                        .tint(.white.opacity(0.72))
                }
                Text(preparationText(preparation))
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.68))
            }
            .accessibilityElement(children: .combine)
        }
    }

    private func preparationText(_ preparation: PersonRatingPreparation) -> String {
        if preparation.state == .degraded {
            return "Some ratings could not be prepared"
        }
        if viewModel.preparationTimedOut {
            return "Ratings are still preparing. Pull to refresh."
        }
        let label = viewModel.filterOptions.sorts.first {
            $0.value == viewModel.filter.sort?.rawValue
        }?.label ?? "ratings"
        return "Preparing \(label.lowercased()) · \(preparation.processed) of \(preparation.total)"
    }

    private func roleDisclosureRow(_ group: FilmographyCreditGroup, type: FilmographyType) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Button {
                    toggleCreditRole(group.id)
                } label: {
                    Text(group.compactTitle(for: type))
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white.opacity(0.74))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityValue(expandedCreditRoles.contains(group.id) ? "Expanded" : "Collapsed")
                .accessibilityIdentifier("person-role-header.\(type.rawValue).\(group.id)")

                if let completion = roleCompletion(for: group, type: type),
                   completion.isVisible {
                    SWCompletionProgressButton(progress: completion)
                        .accessibilityLabel("\(group.role) completion")
                        .accessibilityIdentifier("person-role-completion.\(type.rawValue).\(group.id)")
                }

                Button {
                    toggleCreditRole(group.id)
                } label: {
                    Image(systemName: expandedCreditRoles.contains(group.id) ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white.opacity(0.44))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(group.role) section")
                .accessibilityValue(expandedCreditRoles.contains(group.id) ? "Expanded" : "Collapsed")
                .accessibilityIdentifier("person-role-chevron.\(type.rawValue).\(group.id)")
            }
            .padding(.leading, 12)
            .padding(.trailing, 2)
            .frame(maxWidth: .infinity)
            .frame(minHeight: 44)
            .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 8))

            if expandedCreditRoles.contains(group.id) {
                filmographyGrid(group.media)
            }
        }
    }

    private func filmographyGrid(_ media: [MediaSummary]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
            ForEach(media) { item in
                Button {
                    selectedMedia = MediaBrowsingSelection(
                        ref: item.ref,
                        within: media.map(\.ref)
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

    private func seriesDisclosureRow(_ series: [MediaSeriesSummary]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    if expandedCreditRoles.contains(Self.seriesRoleID) {
                        expandedCreditRoles.remove(Self.seriesRoleID)
                    } else {
                        expandedCreditRoles.insert(Self.seriesRoleID)
                    }
                }
            } label: {
                HStack(spacing: 10) {
                    Text("Series · \(series.count)")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white.opacity(0.74))

                    Spacer()

                    Image(systemName: expandedCreditRoles.contains(Self.seriesRoleID) ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white.opacity(0.44))
                }
                .padding(.horizontal, 12)
                .frame(height: 42)
                .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)

            if expandedCreditRoles.contains(Self.seriesRoleID) {
                LazyVGrid(
                    columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4),
                    spacing: 14
                ) {
                    ForEach(series) { item in
                        Button {
                            selectedSeries = item.ref
                        } label: {
                            MediaSeriesCard(series: item)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(item.name) series")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func metadataChips(_ detail: PersonDetail) -> [String] {
        var chips: [String] = []
        if let department = clean(detail.knownForDepartment) {
            chips.append(department)
        }
        if let birthDate = clean(detail.birthDate) {
            chips.append("Born \(yearOrDate(birthDate))")
        }
        if let deathDate = clean(detail.deathDate) {
            chips.append("Died \(yearOrDate(deathDate))")
        }
        if let place = clean(detail.placeOfBirth) {
            chips.append(place)
        }
        return chips
    }

    private func syncSelectedFilmographyType() {
        let types = FilmographyType.available(in: viewModel.filmography)
        if let first = types.first, !types.contains(selectedFilmographyType) {
            selectedFilmographyType = first
        }
    }

    private func expandPrimaryCreditRole() {
        let types = FilmographyType.available(in: viewModel.filmography)
        let selectedType = types.contains(selectedFilmographyType) ? selectedFilmographyType : types.first ?? selectedFilmographyType
        let filmography = viewModel.filmography.filter { $0.ref.mediaType == selectedType.rawValue }
        if let primaryGroup = FilmographyCreditGroup.groups(
            from: filmography,
            knownForDepartment: viewModel.detail?.knownForDepartment
        ).first {
            expandedCreditRoles = [primaryGroup.id]
        } else {
            expandedCreditRoles = []
        }
        if !(viewModel.detail?.series(for: selectedType.rawValue).isEmpty ?? true) {
            expandedCreditRoles.insert(Self.seriesRoleID)
        }
    }

    private func toggleCreditRole(_ id: String) {
        withAnimation(.easeInOut(duration: 0.2)) {
            if expandedCreditRoles.contains(id) {
                expandedCreditRoles.remove(id)
            } else {
                expandedCreditRoles.insert(id)
            }
        }
    }

    private func clean(_ value: String?) -> String? {
        guard let text = value?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            return nil
        }
        return text
    }

    private func yearOrDate(_ value: String) -> String {
        value.count >= 4 ? String(value.prefix(4)) : value
    }

    private func mediaTypeCompletion(for type: FilmographyType) -> CompletionProgress? {
        if let completion = viewModel.detail?.mediaTypeCompletions?[type.rawValue] {
            return completion
        }
        let types = FilmographyType.available(in: viewModel.filmography)
        return types.count == 1 ? viewModel.detail?.completion : nil
    }

    private func roleCompletion(
        for group: FilmographyCreditGroup,
        type: FilmographyType
    ) -> CompletionProgress? {
        viewModel.detail?.roleCompletions?[type.rawValue]?[group.role]
    }

    private static let seriesRoleID = "media-series"
}

private struct MediaSeriesCard: View {
    let series: MediaSeriesSummary

    var body: some View {
        VStack(spacing: 8) {
            ZStack {
                ForEach(Array(posters.enumerated()), id: \.offset) { index, url in
                    MediaArtwork(
                        url: url,
                        title: series.name,
                        slot: .profileRail,
                        mediaType: series.mediaType
                    )
                    .scaleEffect(scale(index))
                    .rotationEffect(.degrees(rotation(index)))
                    .offset(x: offset(index))
                    .shadow(color: .black.opacity(0.32), radius: 8, y: 4)
                    .zIndex(Double(posters.count - index))
                }
            }
            .frame(width: 80, height: 120)

            Text(series.name)
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(.white.opacity(0.82))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .frame(width: 80, height: 28, alignment: .top)
        }
    }

    private func offset(_ index: Int) -> CGFloat {
        switch posters.count {
        case 2: index == 0 ? -6 : 6
        case 3: CGFloat(index - 1) * 10
        default: 0
        }
    }

    private func rotation(_ index: Int) -> Double {
        switch posters.count {
        case 2: index == 0 ? -3 : 3
        case 3: Double(index - 1) * 4
        default: 0
        }
    }

    private func scale(_ index: Int) -> CGFloat {
        posters.count == 3 && index == 1 ? 0.94 : 0.88
    }

    private var posters: [String?] {
        let urls = Array(series.posterUrls.prefix(3)).map(Optional.some)
        return urls.isEmpty ? [nil] : urls
    }
}

enum FilmographyType: String, CaseIterable, Identifiable {
    case movie
    case tv
    case anime
    case book
    case manga
    case music

    var id: String { rawValue }

    var title: String {
        switch self {
        case .movie:
            "Film"
        case .tv:
            "TV"
        case .anime:
            "Anime"
        case .book:
            "Books"
        case .manga:
            "Manga"
        case .music:
            "Music"
        }
    }

    var sectionTitle: String {
        switch self {
        case .book:
            "Books"
        case .anime:
            "Anime"
        case .manga:
            "Manga"
        case .music:
            "Discography"
        case .movie, .tv:
            "Filmography"
        }
    }

    static func available(in media: [MediaSummary]) -> [FilmographyType] {
        allCases.filter { type in
            media.contains { $0.ref.mediaType == type.rawValue }
        }
    }
}

struct FilmographyCreditGroup: Identifiable {
    let role: String
    let media: [MediaSummary]

    var id: String { role }

    static func groups(
        from media: [MediaSummary],
        knownForDepartment: String? = nil
    ) -> [FilmographyCreditGroup] {
        var grouped: [String: [MediaSummary]] = [:]

        for item in media {
            let roles = cleanRoles(item.creditRoles)
            for role in roles.isEmpty ? ["Credits"] : roles {
                grouped[role, default: []].append(item)
            }
        }

        let primaryRoles = primaryRoles(for: knownForDepartment)
        let isMusic = media.allSatisfy { $0.ref.mediaType == FilmographyType.music.rawValue }

        return grouped
            .map { FilmographyCreditGroup(role: $0.key, media: $0.value) }
            .sorted {
                if isMusic {
                    let firstOrder = musicRoleOrder($0.role)
                    let secondOrder = musicRoleOrder($1.role)
                    if firstOrder != secondOrder {
                        return firstOrder < secondOrder
                    }
                }
                let firstIsPrimary = primaryRoles.contains($0.role.lowercased())
                let secondIsPrimary = primaryRoles.contains($1.role.lowercased())
                if firstIsPrimary != secondIsPrimary {
                    return firstIsPrimary
                }
                if $0.media.count != $1.media.count {
                    return $0.media.count > $1.media.count
                }
                return $0.role < $1.role
            }
    }

    fileprivate func title(for type: FilmographyType) -> String {
        if role == "Credits" {
            return compactTitle(for: type)
        }
        return "\(role) \(connector) \(media.count) \(type.creditNoun(count: media.count))"
    }

    fileprivate func compactTitle(for type: FilmographyType) -> String {
        if type == .music {
            return "\(role) · \(media.count)"
        }
        return "\(role) · \(media.count) \(type.creditNoun(count: media.count))"
    }

    private var connector: String {
        switch role.lowercased() {
        case "actor", "actress":
            "in"
        case "author":
            "of"
        default:
            "of"
        }
    }

    private static func cleanRoles(_ roles: [String]) -> [String] {
        var seen = Set<String>()
        return roles.compactMap { role in
            let clean = role.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !clean.isEmpty, seen.insert(clean).inserted else { return nil }
            return clean
        }
    }

    private static func primaryRoles(for department: String?) -> Set<String> {
        let department = department?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if department?.contains("voice act") == true {
            return ["voice actor", "voice actress", "voice acting"]
        }
        return switch department {
        case "acting":
            ["actor", "actress"]
        case "directing":
            ["director"]
        case "writing":
            ["writer"]
        case "production":
            ["producer"]
        case "author":
            ["author"]
        case "mangaka":
            ["story & art", "story", "art", "author"]
        case "artist":
            ["artist"]
        default:
            []
        }
    }

    private static func musicRoleOrder(_ role: String) -> Int {
        [
            "Albums", "EPs", "Singles", "Mixtapes", "Compilations", "Soundtracks",
            "Live releases", "Remix releases", "DJ mixes", "Demos", "Broadcasts",
            "Audiobooks", "Interviews", "Other",
        ].firstIndex(of: role) ?? .max
    }
}

extension FilmographyType {
    func creditNoun(count: Int) -> String {
        switch self {
        case .movie:
            count == 1 ? "film" : "films"
        case .tv:
            count == 1 ? "show" : "shows"
        case .anime:
            "anime"
        case .book:
            count == 1 ? "book" : "books"
        case .manga:
            "manga"
        case .music:
            count == 1 ? "release" : "releases"
        }
    }
}

private struct PersonProfileImage: View {
    let urlString: String?
    let name: String

    var body: some View {
        SpineAsyncImage(url: imageURL) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                Circle()
                    .fill(.white.opacity(0.12))
                    .overlay {
                        Image(systemName: "person.fill")
                            .font(.system(size: 58, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.72))
                    }
            }
        }
        .clipShape(Circle())
        .overlay {
            Circle().stroke(.white.opacity(0.16), lineWidth: 1)
        }
        .accessibilityLabel(name)
    }

    private var imageURL: URL? {
        guard let urlString, !urlString.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return URL(string: urlString)
    }
}

private struct PersonHeroArtwork: View {
    let urlString: String?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                SpineAsyncImage(url: imageURL) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        SpinePalette.pageBackground
                    }
                }
                .blur(radius: 30, opaque: true)
                .scaleEffect(1.28)
                .brightness(-0.04)
                .saturation(1.2)
                .frame(width: proxy.size.width, height: proxy.size.height)

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.4), location: 0),
                        .init(color: .black.opacity(0.14), location: 0.3),
                        .init(color: .clear, location: 0.58),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0.32),
                        .init(color: SpinePalette.pageBackground.opacity(0.2), location: 0.54),
                        .init(color: SpinePalette.pageBackground.opacity(0.7), location: 0.78),
                        .init(color: SpinePalette.pageBackground, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .clipped()
        }
    }

    private var imageURL: URL? {
        guard let urlString, !urlString.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return URL(string: urlString)
    }
}

private struct PersonCircleIconButton: View {
    let systemName: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

private struct PersonActionSheet: View {
    let onAddToList: () -> Void

    var body: some View {
        VStack(spacing: 10) {
            Button(action: onAddToList) {
                Label("Add to List", systemImage: "list.bullet.rectangle")
                    .font(.system(size: 17, weight: .semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .frame(height: 54)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)
            .accessibilityIdentifier("person-detail.add-to-list")
        }
        .presentationBackground(.regularMaterial)
    }
}

private struct PersonSectionLabel: View {
    let title: String

    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 12, weight: .heavy))
            .foregroundStyle(.white.opacity(0.54))
            .tracking(1.2)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .layoutPriority(1)
    }
}
