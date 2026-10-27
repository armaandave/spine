import Foundation
import SwiftUI

@MainActor
@Observable
final class CompanyDetailViewModel {
    var detail: CompanyDetail?
    var mediaByRole: [CompanyCatalogRole: [MediaSummary]] = [:]
    var nextPageByRole: [CompanyCatalogRole: String] = [:]
    var completionByRole: [CompanyCatalogRole: CompletionProgress] = [:]
    var filter = MediaFilterState()
    var filterOptions: MediaFilterOptionsResponse
    var isLoadingProfile = true
    var profileErrorMessage: String?
    var catalogErrorByRole: [CompanyCatalogRole: String] = [:]

    private let ref: CompanyRef
    private let companyRepository: CompanyRepository
    private let onUnauthorized: () -> Void
    private var filterRevision = 0
    private var loadingRevisionByRole: [CompanyCatalogRole: Int] = [:]
    private var loadingMoreRevisionByRole: [CompanyCatalogRole: Int] = [:]
    private var loadMoreErrorRoles = Set<CompanyCatalogRole>()

    init(ref: CompanyRef, companyRepository: CompanyRepository, onUnauthorized: @escaping () -> Void) {
        self.ref = ref
        self.companyRepository = companyRepository
        self.onUnauthorized = onUnauthorized
        filterOptions = .companyFallback(for: ref)
    }

    func load() async {
        isLoadingProfile = true
        profileErrorMessage = nil
        defer { isLoadingProfile = false }

        do {
            let loaded = try await companyRepository.detail(ref: ref)
            if let existing = detail,
               ref.isAnimeStudio,
               profileRichness(existing) > profileRichness(loaded) {
                detail = existing
            } else {
                detail = loaded
            }
        } catch is CancellationError {
            return
        } catch {
            profileErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }

        guard let role = detail?.catalogRoles.first else { return }
        filterRevision += 1
        await loadCatalog(for: role, reset: true)
    }

    func loadFilterOptions() async {
        do {
            filterOptions = try await companyRepository.filterOptions(ref: ref)
        } catch is CancellationError {
            return
        } catch {
            handleUnauthorized(error)
        }
    }

    func loadCatalog(for role: CompanyCatalogRole, reset: Bool = false) async {
        guard detail?.catalogRoles.contains(role) == true, reset || mediaByRole[role] == nil else { return }
        if reset {
            completionByRole[role] = nil
        }
        let requestRevision = filterRevision
        guard loadingRevisionByRole[role] != requestRevision else { return }
        let requestFilter = filter
        loadingRevisionByRole[role] = requestRevision
        catalogErrorByRole[role] = nil
        loadMoreErrorRoles.remove(role)
        defer {
            if loadingRevisionByRole[role] == requestRevision {
                loadingRevisionByRole[role] = nil
            }
        }

        do {
            let response = try await companyRepository.catalog(
                ref: ref,
                role: role,
                page: nil,
                filter: requestFilter
            )
            guard requestRevision == filterRevision, requestFilter == filter else { return }
            mediaByRole[role] = response.results
            nextPageByRole[role] = APIPageCursor.nextPage(from: response.next)
            if let completion = response.completion {
                completionByRole[role] = completion
            } else if response.next == nil {
                completionByRole[role] = Self.completion(from: response.results)
            }
            catalogErrorByRole[role] = nil
            loadMoreErrorRoles.remove(role)
        } catch is CancellationError {
            return
        } catch {
            guard requestRevision == filterRevision else { return }
            catalogErrorByRole[role] = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func loadMore(for role: CompanyCatalogRole) async {
        guard loadingMoreRevisionByRole[role] == nil, let page = nextPageByRole[role] else { return }
        let requestRevision = filterRevision
        let requestFilter = filter
        loadingMoreRevisionByRole[role] = requestRevision
        catalogErrorByRole[role] = nil
        defer {
            if loadingMoreRevisionByRole[role] == requestRevision {
                loadingMoreRevisionByRole[role] = nil
            }
        }

        do {
            let response = try await companyRepository.catalog(
                ref: ref,
                role: role,
                page: page,
                filter: requestFilter
            )
            guard requestRevision == filterRevision, requestFilter == filter else { return }
            var media = mediaByRole[role] ?? []
            var seen = Set(media.map(\.id))
            media += response.results.filter { seen.insert($0.id).inserted }
            mediaByRole[role] = media
            nextPageByRole[role] = APIPageCursor.nextPage(from: response.next)
            if let completion = response.completion {
                completionByRole[role] = completion
            } else if response.next == nil {
                completionByRole[role] = Self.completion(from: media)
            }
            catalogErrorByRole[role] = nil
            loadMoreErrorRoles.remove(role)
        } catch is CancellationError {
            return
        } catch {
            guard requestRevision == filterRevision else { return }
            catalogErrorByRole[role] = error.localizedDescription
            loadMoreErrorRoles.insert(role)
            handleUnauthorized(error)
        }
    }

    /// After a write elsewhere, reloads the catalog rows already shown for these roles and swaps each role's
    /// in at once: a reset load keeps only the first page, which would cut a long catalog short under the user.
    func refreshLoadedCatalogs(for roles: [CompanyCatalogRole]) async {
        let requestRevision = filterRevision
        let requestFilter = filter
        let isCurrent = { [weak self] in
            requestRevision == self?.filterRevision && requestFilter == self?.filter
        }
        for role in roles {
            guard let shown = mediaByRole[role], isCurrent() else { continue }
            do {
                let pages = try await PagedReload.pages(
                    covering: shown.count,
                    resultCount: { $0.results.count },
                    next: { $0.next }
                ) { page in
                    guard isCurrent() else { throw CancellationError() }
                    return try await companyRepository.catalog(ref: ref, role: role, page: page, filter: requestFilter)
                }
                guard isCurrent(), let last = pages.last else { return }
                var seen = Set<String>()
                let media = pages.flatMap(\.results).filter { seen.insert($0.id).inserted }
                mediaByRole[role] = media
                nextPageByRole[role] = APIPageCursor.nextPage(from: last.next)
                if let completion = last.completion {
                    completionByRole[role] = completion
                } else if last.next == nil {
                    completionByRole[role] = Self.completion(from: media)
                }
            } catch is CancellationError {
                return
            } catch {
                // The rows already shown stay; the next refresh tries again.
                handleUnauthorized(error)
            }
        }
    }

    func applyFilter(to roles: Set<CompanyCatalogRole>) async {
        filterRevision += 1
        let applyRevision = filterRevision
        catalogErrorByRole = [:]
        mediaByRole = [:]
        nextPageByRole = [:]
        completionByRole = [:]
        loadingMoreRevisionByRole = [:]
        let availableRoles = detail?.catalogRoles ?? []
        let requestedRoles = roles.isEmpty
            ? Array(availableRoles.prefix(1))
            : availableRoles.filter(roles.contains)
        for role in requestedRoles {
            guard applyRevision == filterRevision else { return }
            await loadCatalog(for: role, reset: true)
        }
    }

    func retryCatalog(for role: CompanyCatalogRole) async {
        guard !isLoadingCatalog(for: role), !isLoadingMore(for: role) else { return }
        if loadMoreErrorRoles.contains(role) {
            await loadMore(for: role)
        } else {
            await loadCatalog(for: role, reset: true)
        }
    }

    func isLoadingCatalog(for role: CompanyCatalogRole) -> Bool {
        loadingRevisionByRole[role] == filterRevision
    }

    func isLoadingMore(for role: CompanyCatalogRole) -> Bool {
        loadingMoreRevisionByRole[role] == filterRevision
    }

    func completion(
        for role: CompanyCatalogRole,
        in detail: CompanyDetail
    ) -> CompletionProgress? {
        if let completion = completionByRole[role] {
            return completion
        }
        guard mediaByRole[role] == nil, !filter.isActive else { return nil }
        return detail.catalogs.catalog(for: role)?.completion
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }

    private func profileRichness(_ detail: CompanyDetail) -> Int {
        var score = 0
        if detail.description?.isEmpty == false { score += 1 }
        if detail.logoUrl?.isEmpty == false { score += 1 }
        if detail.foundedYear != nil { score += 1 }
        if detail.status?.isEmpty == false { score += 1 }
        if detail.companySize?.isEmpty == false { score += 1 }
        if detail.parent != nil { score += 1 }
        if !detail.websites.isEmpty { score += 1 }
        return score
    }

    private static func completion(from media: [MediaSummary]) -> CompletionProgress {
        CompletionProgress(
            completedCount: media.lazy.filter {
                $0.userState?.status == "Completed"
            }.count,
            totalCount: media.count
        )
    }
}

struct CompanyDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: CompanyDetailViewModel
    @State private var expandedRoles = Set<CompanyCatalogRole>()
    @State private var selectedMedia: MediaBrowsingSelection?
    @State private var selectedCompany: CompanyRef?
    @State private var edgeDragOffset: CGFloat = 0

    private let ref: CompanyRef
    private let companyRepository: CompanyRepository
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
        ref: CompanyRef,
        companyRepository: CompanyRepository = AppRepositories.current().companies,
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
        self.ref = ref
        self.companyRepository = companyRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: CompanyDetailViewModel(
            ref: ref,
            companyRepository: companyRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            SpinePageBackground()
            content
                .spineContentTransition(value: contentPhase)
            CompanyBackButton { dismiss() }
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
        .overlay(alignment: .bottomLeading) {
            if selectedTab == .search {
                ExplorationSearchButton()
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
            }
        }
        .overlay(alignment: .bottomTrailing) {
            ExplorationHomeButton()
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
        }
        .fullScreenCover(item: $selectedMedia) { selection in
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
        .fullScreenCover(item: $selectedCompany) { ref in
            CompanyDetailView(
                ref: ref,
                companyRepository: companyRepository,
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
            guard viewModel.detail == nil else { return }
            await viewModel.load()
            expandPrimaryRole()
            await viewModel.loadFilterOptions()
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
            guard let changedRef = notification.userInfo?["ref"] as? MediaRef else { return }
            let affectedRoles = viewModel.mediaByRole.compactMap { role, media in
                media.contains(where: { $0.ref.id == changedRef.id }) ? role : nil
            }
            guard !affectedRoles.isEmpty else { return }
            // In place: a title marked from this page mustn't cut its catalog back to the first page.
            Task { await viewModel.refreshLoadedCatalogs(for: affectedRoles) }
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
        if viewModel.isLoadingProfile, viewModel.detail == nil {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel(ref.isAnimeStudio ? "Loading anime studio" : "Loading game studio")
        } else if let detail = viewModel.detail {
            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 26) {
                    hero(detail)
                    descriptionSection(detail)
                    studioDetailsSection(detail)
                    catalogSection(detail)
                }
                .padding(.horizontal, 16)
                .padding(.top, 48)
                .padding(.bottom, 36)
                .frame(maxWidth: .infinity, alignment: .leading)
                .containerRelativeFrame(.horizontal)
            }
            .scrollBounceBehavior(.basedOnSize, axes: [.horizontal, .vertical])
            .refreshable {
                await viewModel.load()
                expandPrimaryRole()
            }
        } else if let error = viewModel.profileErrorMessage, viewModel.detail == nil {
            VStack(spacing: 14) {
                ContentUnavailableView(
                    "Could not load studio",
                    systemImage: "building.2.crop.circle",
                    description: Text(error)
                )
                Button("Try Again") {
                    Task {
                        await viewModel.load()
                        expandPrimaryRole()
                        await viewModel.loadFilterOptions()
                    }
                }
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white.opacity(0.86))
                .padding(.horizontal, 24)
                .frame(height: 42)
                .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .buttonStyle(.plain)
                .disabled(viewModel.isLoadingProfile)
                .accessibilityLabel("Retry loading studio")
            }
            .foregroundStyle(.white)
            .padding()
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingProfile,
            hasContent: viewModel.detail != nil,
            hasError: viewModel.profileErrorMessage != nil
        )
    }

    private func hero(_ detail: CompanyDetail) -> some View {
        VStack(spacing: 16) {
            CompanyLogo(urlString: detail.logoUrl, name: detail.name)
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

                CompanyMetadataChips(detail: detail)
            }
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private func descriptionSection(_ detail: CompanyDetail) -> some View {
        if let description = clean(detail.description) {
            VStack(alignment: .leading, spacing: 12) {
                CompanySectionLabel(title: "About")
                Text(description)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(.white.opacity(0.76))
                    .lineSpacing(3)
                    .padding(14)
                    .background(.white.opacity(0.028), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
        }
    }

    private func catalogSection(_ detail: CompanyDetail) -> some View {
        let availableRoles = detail.catalogRoles

        return VStack(alignment: .leading, spacing: 14) {
            HStack {
                CompanySectionLabel(title: detail.resolvedMediaType == "anime" ? "Anime" : "Games")

                if let completion = catalogCompletion(detail), completion.isVisible {
                    SWCompletionProgressButton(progress: completion)
                        .accessibilityLabel("\(detail.name) catalog completion")
                }

                Spacer()

                MediaFilterButton(
                    filter: $viewModel.filter,
                    scope: .company(ref: detail.ref),
                    options: viewModel.filterOptions,
                    mediaTypes: [],
                    showsTagFilter: false
                ) {
                    let roles = expandedRoles
                    Task { await viewModel.applyFilter(to: roles) }
                }
            }

            ForEach(availableRoles) { role in
                roleDisclosureRow(role, detail: detail)
            }
        }
    }

    private func roleDisclosureRow(_ role: CompanyCatalogRole, detail: CompanyDetail) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Button {
                    toggleRole(role)
                } label: {
                    Text(roleDisclosureTitle(role, detail: detail))
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white.opacity(0.74))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityValue(expandedRoles.contains(role) ? "Expanded" : "Collapsed")

                Button {
                    toggleRole(role)
                } label: {
                    Image(systemName: expandedRoles.contains(role) ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white.opacity(0.44))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(role.creditRole) section")
                .accessibilityValue(expandedRoles.contains(role) ? "Expanded" : "Collapsed")
            }
            .padding(.leading, 12)
            .padding(.trailing, 2)
            .frame(maxWidth: .infinity)
            .frame(minHeight: 44)
            .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 8))

            if expandedRoles.contains(role) {
                roleCatalog(role)
            }
        }
    }

    private func toggleRole(_ role: CompanyCatalogRole) {
        let isExpanding = !expandedRoles.contains(role)
        withAnimation(.easeInOut(duration: 0.2)) {
            if isExpanding {
                expandedRoles.insert(role)
            } else {
                expandedRoles.remove(role)
            }
        }
        if isExpanding {
            Task { await viewModel.loadCatalog(for: role) }
        }
    }

    @ViewBuilder
    private func roleCatalog(_ role: CompanyCatalogRole) -> some View {
        let media = viewModel.mediaByRole[role] ?? []
        let error = viewModel.catalogErrorByRole[role]

        Group {
            if viewModel.isLoadingCatalog(for: role), media.isEmpty, error == nil {
                ProgressView()
                    .tint(.white)
                    .frame(maxWidth: .infinity, minHeight: 120)
                    .accessibilityLabel("Loading \(role.collectionTitle.lowercased())")
            } else if let error, media.isEmpty {
                catalogError(error, role: role)
            } else if media.isEmpty {
                ContentUnavailableView(
                    role == .studio ? "No anime" : "No \(role.title.lowercased()) games",
                    systemImage: "square.grid.2x2",
                    description: Text(
                        role == .studio
                            ? "Anime will appear here when available."
                            : "Games will appear here when available."
                    )
                )
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, minHeight: 180)
            } else {
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
                                title: item.displayTitle,
                                slot: .tagGrid,
                                mediaType: item.ref.mediaType,
                                orientation: item.posterOrientation
                            )
                            .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(
                            role == .studio
                                ? "View anime \(item.displayTitle)"
                                : "View \(item.displayTitle)"
                        )
                        .prefetchingArtwork(after: item, in: media) { $0.artworkRequest(slot: .tagGrid) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Group {
                    if error != nil {
                        Button {
                            Task { await viewModel.retryCatalog(for: role) }
                        } label: {
                            Text("Try Again")
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(.white.opacity(0.86))
                                .frame(maxWidth: .infinity)
                                .frame(height: 42)
                                .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Retry loading more \(role.collectionTitle.lowercased())")
                    } else if viewModel.nextPageByRole[role] != nil {
                        Button {
                            Task { await viewModel.loadMore(for: role) }
                        } label: {
                            Group {
                                if viewModel.isLoadingMore(for: role) {
                                    ProgressView().tint(.white)
                                } else {
                                    Text("Load More")
                                }
                            }
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.white.opacity(0.86))
                            .frame(maxWidth: .infinity)
                            .frame(height: 42)
                            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        }
                        .buttonStyle(.plain)
                        .disabled(viewModel.isLoadingMore(for: role))
                    } else {
                        Color.clear
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 42)
                .spineContentTransition(value: rolePaginationPhase(role))
            }
        }
        .spineContentTransition(value: roleGamesPhase(role))
    }

    private func roleGamesPhase(_ role: CompanyCatalogRole) -> SpineContentPhase {
        let media = viewModel.mediaByRole[role] ?? []
        return .resolve(
            isLoading: viewModel.isLoadingCatalog(for: role),
            hasContent: !media.isEmpty,
            hasError: viewModel.catalogErrorByRole[role] != nil
        )
    }

    private func rolePaginationPhase(_ role: CompanyCatalogRole) -> String {
        if viewModel.isLoadingMore(for: role) {
            return "loading"
        }
        return viewModel.nextPageByRole[role] == nil ? "empty" : "available"
    }

    private func roleDisclosureTitle(_ role: CompanyCatalogRole, detail: CompanyDetail) -> String {
        guard let count = role.count(in: detail) else { return role.creditRole }
        return "\(role.creditRole) · \(count.formatted()) \(role.itemNoun(count: count))"
    }

    private func catalogError(_ message: String, role: CompanyCatalogRole) -> some View {
        VStack(spacing: 12) {
            ContentUnavailableView(
                "Could not load \(role.collectionTitle.lowercased())",
                systemImage: "exclamationmark.arrow.trianglehead.2.clockwise.rotate.90",
                description: Text(message)
            )
            Button("Try Again") {
                Task { await viewModel.retryCatalog(for: role) }
            }
            .font(.system(size: 13, weight: .bold))
            .foregroundStyle(.white.opacity(0.86))
            .padding(.horizontal, 24)
            .frame(height: 42)
            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .buttonStyle(.plain)
            .disabled(viewModel.isLoadingCatalog(for: role) || viewModel.isLoadingMore(for: role))
            .accessibilityLabel("Retry loading \(role.collectionTitle.lowercased())")
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity, minHeight: 180)
    }

    @ViewBuilder
    private func studioDetailsSection(_ detail: CompanyDetail) -> some View {
        let website = detail.websites.first ?? detail.providerUrl ?? detail.igdbUrl
        if detail.parent != nil || website != nil {
            VStack(alignment: .leading, spacing: 12) {
                CompanySectionLabel(title: "Studio Details")

                VStack(spacing: 0) {
                    if let parent = detail.parent {
                        Button {
                            selectedCompany = CompanyRef(source: detail.source, companyId: parent.id)
                        } label: {
                            detailRow(label: "Parent", value: parent.name, showsChevron: true)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(parent.name)")
                    }

                    if let website, let url = URL(string: website) {
                        if detail.parent != nil {
                            Divider().overlay(.white.opacity(0.045))
                        }
                        Link(destination: url) {
                            detailRow(label: "Website", value: url.host ?? website, showsChevron: true)
                        }
                    }
                }
                .background(.white.opacity(0.028), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(.white.opacity(0.045), lineWidth: 1)
                }
            }
        }
    }

    private func detailRow(label: String, value: String, showsChevron: Bool) -> some View {
        HStack(spacing: 14) {
            Text(label)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.44))
                .frame(width: 78, alignment: .leading)
            Text(value)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.84))
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.white.opacity(0.36))
            }
        }
        .padding(.horizontal, 14)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
    }

    private func clean(_ value: String?) -> String? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    private func catalogCompletion(_ detail: CompanyDetail) -> CompletionProgress? {
        if let completion = detail.completion {
            return completion
        }
        guard detail.catalogRoles.count == 1,
              let role = detail.catalogRoles.first else {
            return nil
        }
        return viewModel.completion(for: role, in: detail)
    }

    private func expandPrimaryRole() {
        guard let detail = viewModel.detail else {
            expandedRoles = []
            return
        }
        if let primaryRole = detail.catalogRoles.first {
            expandedRoles = [primaryRole]
        } else {
            expandedRoles = []
        }
    }
}

private struct CompanyLogo: View {
    let urlString: String?
    let name: String

    var body: some View {
        SpineAsyncImage(url: URL(string: urlString ?? "")) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFit()
                    .padding(22)
            default:
                Image(systemName: "building.2.fill")
                    .font(.system(size: 48, weight: .bold))
                    .foregroundStyle(.black.opacity(0.62))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            LinearGradient(
                colors: [Color(white: 0.92), Color(white: 0.62)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            ),
            in: RoundedRectangle(cornerRadius: 28, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .stroke(.white.opacity(0.3), lineWidth: 1)
        }
        .accessibilityLabel(name)
    }
}

private struct CompanySectionLabel: View {
    let title: String

    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 12, weight: .heavy))
            .tracking(1.1)
            .foregroundStyle(.white.opacity(0.58))
    }
}

private struct CompanyMetadataChips: View {
    let detail: CompanyDetail

    private var chips: [String] {
        var chips = detail.catalogRoles.compactMap { role -> String? in
            guard let count = role.count(in: detail), count > 0 else { return nil }
            if role == .studio {
                return "\(count.formatted()) anime"
            }
            return "\(count.formatted()) \(role.rawValue)"
        }
        chips += [
            detail.foundedYear.map { "Founded \($0)" },
            detail.status,
        ].compactMap { $0 }
        if let companySize = detail.companySize, !companySize.isEmpty {
            chips.append(companySize)
        }
        return chips
    }

    var body: some View {
        if !chips.isEmpty {
            CenteredFlowLayout(spacing: 8) {
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
            .frame(maxWidth: .infinity)
        }
    }
}

private struct CenteredFlowLayout: Layout {
    let spacing: CGFloat

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        let rows = rows(for: width, subviews: subviews)
        let height = rows.reduce(0) { $0 + $1.height } + CGFloat(max(0, rows.count - 1)) * spacing
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let rows = rows(for: bounds.width, subviews: subviews)
        var y = bounds.minY

        for row in rows {
            var x = bounds.minX + max(0, (bounds.width - row.width) / 2)
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(
                    at: CGPoint(x: x, y: y + (row.height - size.height) / 2),
                    proposal: ProposedViewSize(size)
                )
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private func rows(for width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var row = Row()

        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let nextWidth = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            if !row.indices.isEmpty, nextWidth > width {
                rows.append(row)
                row = Row()
            }
            row.indices.append(index)
            row.width = row.indices.count == 1 ? size.width : row.width + spacing + size.width
            row.height = max(row.height, size.height)
        }
        if !row.indices.isEmpty {
            rows.append(row)
        }
        return rows
    }
}

private struct CompanyBackButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(.system(size: 17, weight: .bold))
                .frame(width: 38, height: 38)
        }
        .buttonStyle(.plain)
        .contentShape(.circle)
        .glassEffect(.regular.interactive(), in: .circle)
        .accessibilityLabel("Back")
    }
}
