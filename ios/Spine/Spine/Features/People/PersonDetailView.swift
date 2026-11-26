import Foundation
import Nuke
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

    /// Back on screen after a page it opened: a filmography still being prepared starts checking again.
    func resumePreparationIfNeeded() {
        guard pollingTask == nil else { return }
        startPreparationPollingIfNeeded(requestFilter: filter, generation: requestGeneration)
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

    /// After a write elsewhere, reloads the credit pages already shown and applies them together, so the
    /// filmography never drops back to its first page under the user (and the screen never goes blank).
    func reloadLoadedPages() async {
        let lastLoadedPage = max(detail?.creditsPage ?? 1, 1)
        let requestFilter = filter
        guard detail != nil, presentedFilter == requestFilter, errorMessage == nil else {
            await load()
            return
        }
        stopPreparationPolling()
        requestGeneration += 1
        let generation = requestGeneration
        // A next-page request this one supersedes can't clear its own flag.
        isLoadingNextPage = false
        nextPageErrorMessage = nil

        do {
            var pages: [PersonDetail] = []
            var page = 1
            while true {
                let loaded = try await peopleRepository.detail(ref: ref, filter: requestFilter, creditsPage: page)
                guard generation == requestGeneration, requestFilter == filter else { return }
                pages.append(loaded)
                guard let next = loaded.creditsNextPage, next > page, next <= lastLoadedPage else { break }
                page = next
            }
            apply(pages[0], requestFilter: requestFilter)
            for loaded in pages.dropFirst() {
                append(loaded, requestFilter: requestFilter)
            }
            startPreparationPollingIfNeeded(requestFilter: requestFilter, generation: generation)
        } catch is CancellationError {
            return
        } catch {
            // The credits already shown stay; the next refresh tries again.
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
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
    /// The globe (every type together) or one media type; empty until the credits arrive.
    @State private var selectedKind = ""
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var presentedSheet: PersonDetailSheet?
    /// The photo's color, for the plate's glass and the bars.
    @State private var photoTint: Color?
    @State private var creditsScroll = PersonCreditsScroll()
    /// The bottom of the back and more buttons and the top of the page's scroll view, on screen. The plate stops just
    /// under the buttons.
    @State private var topButtonsBottom: CGFloat = 0
    @State private var scrollViewTop: CGFloat = 0

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
            Color.black
                .ignoresSafeArea()

            content
                .spineContentTransition(value: contentPhase)

            GlassEffectContainer {
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
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.frame(in: .global).maxY
            } action: { bottom in
                topButtonsBottom = bottom
            }
        }
        .navigationBarBackButtonHidden()
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
        .pushDestination(item: $selectedMedia) { selection in
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
        .pushDestination(item: $selectedSeries) { series in
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
                syncSelectedKind()
            } else {
                viewModel.resumePreparationIfNeeded()
            }
        }
        .task(id: viewModel.detail?.profileUrl) {
            guard let url = viewModel.detail?.profileUrl else { return }
            photoTint = MediaPageTint.known(url)
            if let loaded = await MediaPageTint.load(url) {
                photoTint = loaded
            }
        }
        .onDisappear {
            viewModel.cancelPreparation()
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
            guard let changedRef = notification.userInfo?["ref"] as? MediaRef,
                  selectedSeries != nil || viewModel.filmography.contains(where: { $0.ref.id == changedRef.id })
            else { return }
            Task {
                await viewModel.reloadLoadedPages()
                syncSelectedKind()
            }
        }
        .navigationPage(.exploring)
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading, viewModel.detail == nil {
            PersonLoadingPlaceholder(topInset: topSafeAreaInset)
        } else if let detail = viewModel.detail {
            let sections = creditSections
            let lines = PersonCreditLine.lines(for: sections)
            ScrollViewReader { proxy in
                ScrollView(showsIndicators: false) {
                    ZStack(alignment: .top) {
                        PersonHeroArtwork(urlString: detail.profileUrl)
                            .frame(height: topSafeAreaInset + 390)
                            .offset(y: BackdropLayout.topOffset)
                            .allowsHitTesting(false)

                        VStack(alignment: .leading, spacing: 26) {
                            hero(detail)
                            biographySection(detail)
                            credits(sections, lines: lines)
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, topSafeAreaInset + 68)
                        .padding(.bottom, 36)
                    }
                }
                .onScrollGeometryChange(for: PersonCreditsScroll.Viewport.self) { geometry in
                    PersonCreditsScroll.Viewport(
                        height: geometry.containerSize.height,
                        insetBottom: geometry.contentInsets.bottom
                    )
                } action: { _, viewport in
                    creditsScroll.viewport = viewport
                }
                .scrollContentBackground(.hidden)
                .ignoresSafeArea(edges: .top)
                // The plate, drawn over the page so it can stop under the back button; the black under the top
                // buttons once it has.
                .overlay(alignment: .topLeading) {
                    ZStack(alignment: .topLeading) {
                        PersonPlateScrim(scroll: creditsScroll, pinY: plateTop)
                        if !sections.isEmpty {
                            pinnedPlate(
                                sections,
                                map: PersonCreditsMap(lines: lines, width: creditsScroll.stackWidth),
                                proxy: proxy
                            )
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .ignoresSafeArea(edges: .top)
                }
                .refreshable {
                    await viewModel.reloadLoadedPages()
                    syncSelectedKind()
                }
                .onChange(of: kind) { _, _ in
                    // A new kind starts at its first section when the plate had stopped under the back button.
                    guard creditsScroll.isPinned(below: plateTop) else { return }
                    Task { @MainActor in
                        await Task.yield()
                        jump(to: creditSections.first?.id, proxy: proxy)
                    }
                }
            }
        } else if viewModel.errorMessage != nil, viewModel.detail == nil {
            PersonLoadFailed {
                Task {
                    await viewModel.load()
                    syncSelectedKind()
                }
            }
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.detail != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    // MARK: Credits

    /// Where the plate stops, from the top of the page's scroll view: just under the back and more buttons.
    private var plateTop: CGFloat {
        guard topButtonsBottom > 0 else {
            return topSafeAreaInset + PersonPlateLayout.pinBelowSafeArea
        }
        return topButtonsBottom - scrollViewTop + PersonPlateLayout.pinGap
    }

    private var kinds: [String] {
        PersonCreditSections.kinds(in: viewModel.filmography)
    }

    private var kind: String {
        let kinds = kinds
        return kinds.contains(selectedKind) ? selectedKind : PersonCreditSections.defaultKind(in: viewModel.filmography)
    }

    private var creditSections: [PersonCreditSection] {
        PersonCreditSections.make(
            filmography: viewModel.filmography,
            kind: kind,
            knownForDepartment: viewModel.detail?.knownForDepartment,
            series: { viewModel.detail?.series(for: $0) ?? [] }
        )
    }

    @ViewBuilder
    private func credits(_ sections: [PersonCreditSection], lines: [PersonCreditLine]) -> some View {
        if sections.isEmpty {
            Text("No credits yet")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.62))
                .frame(maxWidth: .infinity, minHeight: 120)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                // The plate's place in the page. The plate itself is drawn over the page (`pinnedPlate`).
                Color.clear
                    .frame(height: PersonPlateLayout.fullHeight)
                    .id(PersonPlateLayout.placeID)
                    .onGeometryChange(for: CGFloat.self) { proxy in
                        proxy.frame(in: .scrollView).minY
                    } action: { y in
                        creditsScroll.plateY = y
                    }
                    .onGeometryChange(for: CGFloat.self) { proxy in
                        proxy.size.width
                    } action: { width in
                        creditsScroll.stackWidth = width
                    }
                    // Read from inside: the scroll view reaches under the status bar, and its own frame doesn't say so.
                    .onGeometryChange(for: CGFloat.self) { proxy in
                        (proxy.frame(in: .global).minY - proxy.frame(in: .scrollView).minY).rounded()
                    } action: { top in
                        scrollViewTop = top
                    }

                ratingPreparationStatus
                    .padding(.top, 12)

                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(lines) { line in
                        creditLine(line)
                            // The lines on screen, for the plate's title.
                            .onGeometryChange(for: CGFloat.self) { proxy in
                                proxy.frame(in: .scrollView).minY
                            } action: { top in
                                creditsScroll.lineTops[line.id] = top
                            }
                            .onDisappear {
                                creditsScroll.lineTops[line.id] = nil
                            }
                    }
                }
                .padding(.top, PersonPlateLayout.firstSectionGap)

                creditPagination

                // Room for the last section to reach the plate, so the menu can jump to it.
                Color.clear
                    .frame(height: tailRoom(sections))
            }
        }
    }

    @ViewBuilder
    private func creditLine(_ line: PersonCreditLine) -> some View {
        switch line {
        case let .header(section):
            PersonCreditHeader(section: section, tint: photoTint)
                .padding(.bottom, PersonCreditsLayout.headerGap)
        case let .media(section, _, items, gap):
            PersonCreditPosterRow(section: section, items: items) { item in
                selectedMedia = MediaBrowsingSelection(ref: item.ref, within: section.media.map(\.ref))
            }
            .padding(.bottom, gap)
        case let .series(_, _, items, gap):
            PersonSeriesRow(items: items) { selectedSeries = $0.ref }
                .padding(.bottom, gap)
        }
    }

    private func pinnedPlate(
        _ sections: [PersonCreditSection],
        map: PersonCreditsMap,
        proxy: ScrollViewProxy
    ) -> some View {
        PersonPinnedPlate(
            scroll: creditsScroll,
            sections: sections,
            map: map,
            pinY: plateTop,
            tint: photoTint,
            kinds: kinds,
            kind: Binding(get: { kind }, set: { selectedKind = $0 }),
            onJump: { jump(to: $0, proxy: proxy) }
        ) {
            MediaFilterButton(
                filter: $viewModel.filter,
                scope: .person(ref: viewModel.detail?.ref ?? PersonRef(source: "", id: "")),
                options: viewModel.filterOptions,
                mediaTypes: FilmographyType.available(in: viewModel.filmography).map(\.rawValue)
            ) {
                Task {
                    await viewModel.load()
                    if let mediaType = viewModel.filter.mediaType, FilmographyType(rawValue: mediaType) != nil {
                        selectedKind = mediaType
                    }
                    syncSelectedKind()
                }
            }
        }
        .padding(.horizontal, 16)
    }

    /// Where a section's header lands when the menu jumps to it, from the top of the scroll view: just under the slim
    /// plate.
    private var jumpLine: CGFloat {
        plateTop + PersonPlateLayout.slimHeight + PersonPlateLayout.jumpGap
    }

    /// Scrolls so the section's header sits just under the slim plate; the first section, which starts under the whole
    /// plate, stops the plate where it pins instead. The scroll view finds the line itself, drawn or not. An anchor
    /// lines up the same point of the line and of the scroll view, so these put the line's top where it belongs.
    private func jump(to id: String?, proxy: ScrollViewProxy) {
        let height = creditsScroll.viewport.height
        guard let id, height > PersonPlateLayout.fullHeight else { return }
        let target: String
        let anchor: UnitPoint
        if id == creditSections.first?.id {
            target = PersonPlateLayout.placeID
            anchor = UnitPoint(x: 0.5, y: plateTop / (height - PersonPlateLayout.fullHeight))
        } else {
            let headerLine = PersonCreditsLayout.headerHeight + PersonCreditsLayout.headerGap
            target = PersonCreditLine.headerID(id)
            anchor = UnitPoint(x: 0.5, y: jumpLine / (height - headerLine))
        }
        withAnimation(.smooth(duration: 0.45)) {
            proxy.scrollTo(target, anchor: anchor)
        }
    }

    /// Reads only the width and the viewport (both change rarely), so scrolling never redraws the page.
    private func tailRoom(_ sections: [PersonCreditSection]) -> CGFloat {
        let width = creditsScroll.stackWidth
        let viewport = creditsScroll.viewport
        guard let last = sections.last, width > 0, viewport.height > 0 else {
            return 0
        }
        let lastHeight = PersonCreditsLayout.height(of: last, width: width)
        return max(0, viewport.height - viewport.insetBottom - jumpLine - lastHeight - 36)
    }

    private func syncSelectedKind() {
        let kinds = kinds
        if !kinds.contains(selectedKind) {
            selectedKind = PersonCreditSections.defaultKind(in: viewModel.filmography)
        }
    }

    // MARK: Hero

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

    @ViewBuilder
    private var creditPagination: some View {
        if viewModel.isLoadingNextPage {
            ProgressView()
                .controlSize(.small)
                .tint(.white.opacity(0.72))
                .frame(maxWidth: .infinity)
                .frame(height: 44)
                .padding(.top, 16)
        } else if viewModel.nextPageErrorMessage != nil {
            Button {
                Task {
                    await viewModel.loadNextPage()
                    syncSelectedKind()
                }
            } label: {
                Text("Retry loading credits")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white.opacity(0.78))
                    .frame(maxWidth: .infinity)
                    .frame(height: 44)
            }
            .buttonStyle(.plain)
            .padding(.top, 16)
            .accessibilityHint(viewModel.nextPageErrorMessage ?? "")
        } else if viewModel.canLoadMore {
            Color.clear
                .frame(height: 1)
                .id(viewModel.detail?.creditsNextPage)
                .onAppear {
                    Task {
                        await viewModel.loadNextPage()
                        syncSelectedKind()
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
            .padding(.leading, 18)
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

    private func clean(_ value: String?) -> String? {
        guard let text = value?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            return nil
        }
        return text
    }

    private func yearOrDate(_ value: String) -> String {
        value.count >= 4 ? String(value.prefix(4)) : value
    }
}

// MARK: - Credits: scroll, plate, sections

/// Where the plate and the section headers sit as the page scrolls. Only the plate and the scrim read the moving parts,
/// so scrolling redraws those and not the grids.
@MainActor
@Observable
final class PersonCreditsScroll {
    /// The scroll view's height, and the inset its content stops above at the bottom (the home indicator).
    nonisolated struct Viewport: Equatable {
        var height: CGFloat = 0
        var insetBottom: CGFloat = 0
    }

    var viewport = Viewport()
    /// The plate's place in the page, from the top of the scroll view. Nil until laid out.
    var plateY: CGFloat?
    /// The top of each credits line on screen, from the top of the scroll view, by line. They change on every scroll:
    /// only the plate reads them.
    var lineTops: [String: CGFloat] = [:]
    /// The sections' width, for the page itself.
    var stackWidth: CGFloat = 0

    func isPinned(below pinY: CGFloat) -> Bool {
        guard let plateY else { return false }
        return plateY < pinY
    }

    /// 0 while the plate scrolls with the page, 1 once it has stopped and condensed to its slim bar.
    func collapse(pinY: CGFloat) -> CGFloat {
        guard let plateY else { return 0 }
        return min(max((pinY - plateY) / PersonPlateLayout.collapseDistance, 0), 1)
    }
}

enum PersonPlateLayout {
    /// The id of the plate's place in the page, which a jump to the first section scrolls to.
    static let placeID = "person-plate-place"
    static let fullHeight: CGFloat = 108
    static let slimHeight: CGFloat = 52
    /// The plate stops this far under the back and more buttons.
    static let pinGap: CGFloat = 8
    /// Before the buttons have been measured: this far below the top of the safe area.
    static let pinBelowSafeArea: CGFloat = 60
    /// The scroll it takes the plate to condense once it has stopped.
    static let collapseDistance: CGFloat = 110
    static let titleHeight: CGFloat = 38
    static let cornerRadius: CGFloat = 24
    /// The first section starts this far under the plate.
    static let firstSectionGap: CGFloat = 18
    /// A section the menu jumps to lands this far under the slim plate.
    static let jumpGap: CGFloat = 12

    static func mix(_ from: CGFloat, _ to: CGFloat, _ amount: CGFloat) -> CGFloat {
        from + (to - from) * amount
    }
}

/// The black that fades in under the back and more buttons once the plate has stopped there, so the posters
/// scrolling under them don't fight the glass.
private struct PersonPlateScrim: View {
    let scroll: PersonCreditsScroll
    let pinY: CGFloat

    var body: some View {
        let amount: CGFloat = {
            guard let plateY = scroll.plateY else { return 0 }
            return min(max((pinY - plateY) / 40, 0), 1)
        }()
        LinearGradient(
            stops: [
                .init(color: .black, location: 0),
                .init(color: .black.opacity(0.8), location: 0.55),
                .init(color: .black.opacity(0), location: 1),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(height: pinY + 30)
        .opacity(amount)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// The glass plate over the credits: the section in view as the title (its menu jumps to any section), the filter,
/// the section's bar and numbers, and the type bar with the globe. It scrolls with the page until it reaches the back
/// button, stops there and condenses to a slim bar that keeps the title and the filter.
private struct PersonPinnedPlate<Trailing: View>: View {
    let scroll: PersonCreditsScroll
    let sections: [PersonCreditSection]
    let map: PersonCreditsMap
    let pinY: CGFloat
    let tint: Color?
    let kinds: [String]
    @Binding var kind: String
    let onJump: (String) -> Void
    @ViewBuilder let trailing: () -> Trailing

    var body: some View {
        if let plateY = scroll.plateY, !sections.isEmpty {
            let progress = scroll.collapse(pinY: pinY)
            let eased = progress * progress * (3 - 2 * progress)
            let fold = max(0, 1 - progress * 3)
            let height = PersonPlateLayout.mix(PersonPlateLayout.fullHeight, PersonPlateLayout.slimHeight, eased)
            let top = max(plateY, pinY)
            let current = currentSection(plateTop: top, plateHeight: height)
            let shape = RoundedRectangle(cornerRadius: PersonPlateLayout.cornerRadius, style: .continuous)

            ZStack(alignment: .topLeading) {
                titleMenu(current: current)
                    .scaleEffect(PersonPlateLayout.mix(1, 0.62, eased), anchor: .topLeading)
                    .padding(.leading, 18)
                    .padding(.trailing, 64)
                    .offset(y: PersonPlateLayout.mix(16, 14, eased))

                trailing()
                    .frame(maxWidth: .infinity, alignment: .trailing)
                    .padding(.trailing, 12)
                    .offset(y: PersonPlateLayout.mix(14, 7, eased))

                meterRow(current: current)
                    .padding(.leading, 18)
                    .padding(.trailing, kinds.count > 1 ? typeBarWidth + 24 : 18)
                    .offset(y: 64 - 10 * progress)
                    .opacity(fold)
                    .allowsHitTesting(false)

                if kinds.count > 1 {
                    MediaTypeBar(selectedType: $kind, availableTypes: kinds)
                        .frame(width: typeBarWidth)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                        .padding(.trailing, 12)
                        .offset(y: 64 - 10 * progress)
                        .opacity(fold)
                        .allowsHitTesting(fold > 0.3)
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .frame(height: height, alignment: .top)
            .clipShape(shape)
            .glassEffect(glass, in: shape)
            // The page is always dark; light glass would wash out the white text.
            .environment(\.colorScheme, .dark)
            .offset(y: top)
        }
    }

    private var typeBarWidth: CGFloat {
        CGFloat(kinds.count) * 44
    }

    private var glass: Glass {
        guard let tint else { return .regular }
        return .regular.tint(tint.opacity(0.1))
    }

    /// The section whose header has reached the plate's bottom; the first one until then. A jump lands the header
    /// `jumpGap` under the plate; the few points of slack keep rounding from leaving the title a section behind. The
    /// line on screen nearest the plate places every header, so a section whose header has scrolled away still counts.
    private func currentSection(plateTop: CGFloat, plateHeight: CGFloat) -> PersonCreditSection {
        let line = plateTop + plateHeight + PersonPlateLayout.jumpGap + 4
        let nearest = scroll.lineTops
            .filter { map.lineTops[$0.key] != nil }
            .min { abs($0.value - line) < abs($1.value - line) }
        guard let nearest, let mapped = map.lineTops[nearest.key] else { return sections[0] }
        let index = PersonCreditsLayout.currentIndex(tops: map.headerTops, line: mapped + line - nearest.value)
        return sections[min(index, sections.count - 1)]
    }

    private func titleMenu(current: PersonCreditSection) -> some View {
        Menu {
            ForEach(sections) { section in
                Toggle(isOn: Binding(get: { section.id == current.id }, set: { _ in onJump(section.id) })) {
                    Text(section.title)
                    Text(menuDetail(section))
                }
            }
        } label: {
            HStack(spacing: 6) {
                Text(current.title)
                    .font(.system(size: 30, weight: .black))
                    .tracking(-0.5)
                    .foregroundStyle(.white)
                    .shadow(color: .black.opacity(0.22), radius: 5, y: 1)
                    .lineLimit(1)
                    .minimumScaleFactor(22 / 30)
                    .contentTransition(.opacity)
                Image(systemName: "chevron.down")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white.opacity(0.6))
                    .padding(.top, 3)
            }
            .frame(height: PersonPlateLayout.titleHeight)
            .contentShape(Rectangle())
            .animation(.smooth(duration: 0.35), value: current.id)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Section, \(current.title)")
        .accessibilityHint("Jumps to another section")
    }

    private func menuDetail(_ section: PersonCreditSection) -> String {
        if let done = section.done {
            return "\(done) of \(section.count)"
        }
        return section.count == 1 ? "1 series" : "\(section.count) series"
    }

    @ViewBuilder
    private func meterRow(current: PersonCreditSection) -> some View {
        HStack(spacing: 10) {
            if let done = current.done {
                CompletionBar(done: done, total: current.count, tint: tint)
            } else {
                Text(menuDetail(current))
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.62))
                Spacer(minLength: 0)
            }
        }
        .frame(height: 32)
    }
}

/// A section's quiet header: the role, its slim bar, its numbers.
private struct PersonCreditHeader: View {
    let section: PersonCreditSection
    let tint: Color?

    var body: some View {
        HStack(spacing: 12) {
            Text(section.title)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(1)
                .layoutPriority(1)
            if let done = section.done {
                CompletionBar(
                    done: done,
                    total: section.count,
                    tint: tint,
                    height: 4,
                    countFont: .system(size: 12, weight: .semibold),
                    countOpacity: 0.58
                )
            } else {
                Spacer(minLength: 0)
                Text(section.count == 1 ? "1 series" : "\(section.count) series")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.58))
            }
        }
        .frame(height: PersonCreditsLayout.headerHeight)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// One row of a section's posters, in four columns; a short last row leaves its other columns empty.
private struct PersonCreditPosterRow: View {
    let section: PersonCreditSection
    let items: [MediaSummary]
    let onSelect: (MediaSummary) -> Void

    var body: some View {
        let media = section.media
        let isSquare = section.isSquare
        HStack(alignment: .top, spacing: PersonCreditsLayout.columnGap) {
            ForEach(items) { item in
                Button {
                    onSelect(item)
                } label: {
                    PersonCreditPoster(item: item, isSquare: isSquare)
                }
                .buttonStyle(.plain)
                .posterHoldMenu(item)
                .accessibilityLabel("View \(item.title)")
                .prefetchingArtwork(after: item, in: media) { PersonCreditPoster.request(for: $0, isSquare: isSquare) }
            }
            ForEach(items.count..<PersonCreditsLayout.columns, id: \.self) { _ in
                Color.clear
                    .aspectRatio(isSquare ? 1 : 2.0 / 3.0, contentMode: .fit)
            }
        }
    }
}

/// One row of series cards, in three columns.
private struct PersonSeriesRow: View {
    let items: [MediaSeriesSummary]
    let onSelect: (MediaSeriesSummary) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: PersonCreditsLayout.columnGap) {
            ForEach(items) { series in
                Button {
                    onSelect(series)
                } label: {
                    PersonSeriesCard(series: series)
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity)
                .accessibilityLabel("View \(series.name) series")
            }
            ForEach(items.count..<PersonCreditsLayout.seriesColumns, id: \.self) { _ in
                Color.clear
                    .frame(maxWidth: .infinity)
            }
        }
        .frame(height: PersonCreditsLayout.seriesCardHeight, alignment: .top)
    }
}

/// A plain poster that fills its column: no marks, no badges. Square for music.
private struct PersonCreditPoster: View {
    let item: MediaSummary
    let isSquare: Bool

    private static func thumbnailSize(isSquare: Bool) -> CGSize {
        isSquare ? CGSize(width: 96, height: 96) : CGSize(width: 96, height: 144)
    }

    private static func url(for item: MediaSummary) -> URL? {
        guard let url = item.displayPosterURL?.trimmingCharacters(in: .whitespacesAndNewlines), !url.isEmpty else {
            return nil
        }
        return URL(string: url)
    }

    /// The request the poster loads, so the grid can prefetch the same cache entry.
    static func request(for item: MediaSummary, isSquare: Bool) -> ImageRequest? {
        SpineImagePipeline.request(url: url(for: item), thumbnailSize: thumbnailSize(isSquare: isSquare))
    }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 8, style: .continuous)
        let url = Self.url(for: item)
        Color.clear
            .aspectRatio(isSquare ? 1 : 2.0 / 3.0, contentMode: .fit)
            .overlay {
                if url == nil {
                    missingArt
                } else {
                    SpineAsyncImage(url: url, thumbnailSize: Self.thumbnailSize(isSquare: isSquare)) { phase in
                        switch phase {
                        case let .success(image):
                            image
                                .resizable()
                                .scaledToFill()
                        case .failure:
                            missingArt
                        default:
                            Color.white.opacity(0.06)
                        }
                    }
                }
            }
            .clipShape(shape)
            .overlay {
                shape.strokeBorder(.white.opacity(0.12), lineWidth: 0.5)
            }
            .contentShape(shape)
            .contentShape(.contextMenuPreview, shape)
    }

    /// A title with no art: its name, quietly, on a dark card.
    private var missingArt: some View {
        ZStack {
            Color(red: 0.05, green: 0.05, blue: 0.05)
            Text(item.title)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.white.opacity(0.62))
                .multilineTextAlignment(.center)
                .lineLimit(5)
                .padding(8)
        }
    }
}

/// A book series: its covers fanned, its name and how many books it has.
private struct PersonSeriesCard: View {
    let series: MediaSeriesSummary

    var body: some View {
        VStack(spacing: 8) {
            PosterFan(
                urls: series.posterUrls.map(Optional.some),
                title: series.name,
                slot: .activityPost,
                mediaType: series.mediaType
            )
            .frame(maxWidth: .infinity)
            .frame(height: PersonCreditsLayout.seriesFanHeight)

            VStack(spacing: 0) {
                Text(series.name)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white.opacity(0.88))
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity)
                    .frame(height: 30, alignment: .top)
                Text(series.bookCount == 1 ? "1 book" : "\(series.bookCount) books")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.58))
                    .frame(height: 14)
            }
        }
        .frame(height: PersonCreditsLayout.seriesCardHeight, alignment: .top)
    }
}

/// Loading: the page's shapes in faint glass, still, until the credits arrive and fade in together.
private struct PersonLoadingPlaceholder: View {
    let topInset: CGFloat

    var body: some View {
        VStack(spacing: 26) {
            VStack(spacing: 16) {
                Circle()
                    .fill(.white.opacity(0.07))
                    .frame(width: 156, height: 156)
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(.white.opacity(0.07))
                    .frame(width: 220, height: 30)
            }
            RoundedRectangle(cornerRadius: PersonPlateLayout.cornerRadius, style: .continuous)
                .fill(.white.opacity(0.05))
                .frame(height: PersonPlateLayout.fullHeight)
            LazyVGrid(
                columns: Array(
                    repeating: GridItem(.flexible(), spacing: PersonCreditsLayout.columnGap),
                    count: PersonCreditsLayout.columns
                ),
                spacing: PersonCreditsLayout.rowGap
            ) {
                ForEach(0..<8, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(.white.opacity(0.05))
                        .aspectRatio(2.0 / 3.0, contentMode: .fit)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.top, topInset + 68)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .ignoresSafeArea(edges: .top)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading")
    }
}

/// The page couldn't load: one quiet line and a way to try again.
private struct PersonLoadFailed: View {
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Text("Couldn't load this page.")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.72))
            Button(action: onRetry) {
                Text("Try again")
                    .font(.system(size: 13, weight: .semibold))
                    .padding(.horizontal, 16)
                    .frame(height: 32)
            }
            .buttonStyle(.plain)
            .contentShape(.capsule)
            .glassEffect(.regular.interactive(), in: .capsule)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .environment(\.colorScheme, .dark)
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
    static let actorRole = "Actor"
    /// Acting credits where they play themselves (`MediaSummary.isSelfAppearance`): always the last role.
    static let appearancesRole = "Appearances"

    let role: String
    let media: [MediaSummary]

    var id: String { role }

    static func groups(
        from media: [MediaSummary],
        knownForDepartment: String? = nil
    ) -> [FilmographyCreditGroup] {
        var grouped: [String: [MediaSummary]] = [:]

        for item in media {
            var roles = cleanRoles(item.creditRoles)
            if item.isSelfAppearance, let actor = roles.firstIndex(of: actorRole) {
                roles[actor] = appearancesRole
            }
            for role in roles.isEmpty ? ["Credits"] : roles {
                grouped[role, default: []].append(item)
            }
        }

        let primaryRoles = primaryRoles(for: knownForDepartment)
        let isMusic = media.allSatisfy { $0.ref.mediaType == FilmographyType.music.rawValue }

        return grouped
            .map { FilmographyCreditGroup(role: $0.key, media: $0.value) }
            .sorted { precedes($0, $1, primaryRoles: primaryRoles, isMusic: isMusic) }
    }

    /// The app's role order: Appearances last; music in release order; the person's main role first; then the role
    /// with more credits; then by name.
    static func precedes(
        _ first: FilmographyCreditGroup,
        _ second: FilmographyCreditGroup,
        primaryRoles: Set<String>,
        isMusic: Bool
    ) -> Bool {
        let firstIsAppearances = first.role == appearancesRole
        let secondIsAppearances = second.role == appearancesRole
        if firstIsAppearances != secondIsAppearances {
            return secondIsAppearances
        }
        if isMusic {
            let firstOrder = musicRoleOrder(first.role)
            let secondOrder = musicRoleOrder(second.role)
            if firstOrder != secondOrder {
                return firstOrder < secondOrder
            }
        }
        let firstIsPrimary = primaryRoles.contains(first.role.lowercased())
        let secondIsPrimary = primaryRoles.contains(second.role.lowercased())
        if firstIsPrimary != secondIsPrimary {
            return firstIsPrimary
        }
        if first.media.count != second.media.count {
            return first.media.count > second.media.count
        }
        return first.role < second.role
    }

    private static func cleanRoles(_ roles: [String]) -> [String] {
        var seen = Set<String>()
        return roles.compactMap { role in
            let clean = role.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !clean.isEmpty, seen.insert(clean).inserted else { return nil }
            return clean
        }
    }

    static func primaryRoles(for department: String?) -> Set<String> {
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
                        Color.black
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
                        .init(color: Color.black.opacity(0.2), location: 0.54),
                        .init(color: Color.black.opacity(0.7), location: 0.78),
                        .init(color: Color.black, location: 1),
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
                .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .contentShape(.circle)
        .glassEffect(.regular.interactive(), in: .circle)
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
