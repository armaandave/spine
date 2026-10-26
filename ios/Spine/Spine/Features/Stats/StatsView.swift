import SwiftUI

/// One section of the Stats page, by its response key (STAT-218).
enum StatsPageSection: String, Hashable, Identifiable {
    case header
    case time
    case top
    case highestRated
    case activity
    case ratings
    case ratedVsEveryone
    case releaseYears
    case genres
    case languages
    case listProgress
    case collections
    case mostLogged
    case milestones

    var id: String { rawValue }

    /// Global's order (STAT-218).
    static let globalOrder: [StatsPageSection] = [
        .header, .time, .top, .highestRated, .activity, .ratings, .ratedVsEveryone, .releaseYears,
        .genres, .languages, .listProgress, .mostLogged, .milestones,
    ]

    /// A type tab's order (STAT-218).
    static let typeOrder: [StatsPageSection] = [
        .header, .time, .top, .highestRated, .ratings, .ratedVsEveryone, .releaseYears, .genres,
        .languages, .activity, .listProgress, .collections, .mostLogged, .milestones,
    ]

    /// The sections a page shows, in its tab's order, leaving out null and empty ones
    /// (STAT-302). Highest rated shows only when Top comes from a list, and Collections only on
    /// a type tab.
    static func sections(for page: StatsPage) -> [StatsPageSection] {
        (page.mediaType == nil ? globalOrder : typeOrder).filter { $0.isShown(in: page) }
    }

    func isShown(in page: StatsPage) -> Bool {
        switch self {
        case .header: page.header.map { !$0.isEmpty } ?? false
        case .time: page.time.map { !$0.isEmpty } ?? false
        case .top: page.top.map { !$0.isEmpty } ?? false
        case .highestRated: StatsHighestRatedSection.isShown(top: page.top, highestRated: page.highestRated)
        case .activity: page.activity.map { !$0.isEmpty } ?? false
        case .ratings: page.ratings.map { !$0.isEmpty } ?? false
        case .ratedVsEveryone: page.ratedVsEveryone.map { !$0.isEmpty } ?? false
        case .releaseYears: page.releaseYears.map { !$0.isEmpty } ?? false
        case .genres: page.genres.map { !$0.isEmpty } ?? false
        case .languages: page.languages.map { !$0.isEmpty } ?? false
        case .listProgress: !page.listRings.isEmpty
        case .collections: page.mediaType != nil && (page.collections.map { !$0.isEmpty } ?? false)
        case .mostLogged: page.mostLogged.map { !$0.isEmpty } ?? false
        case .milestones: !page.milestones.isEmpty
        }
    }

    /// "Movies · 2025", "All Media · All Time": names a show-all screen's scope.
    static func scopeTitle(mediaType: String?, period: StatsPeriod) -> String {
        let type = mediaType.map { MediaTypeTheme.theme(for: $0).displayName } ?? "All Media"
        return "\(type) · \(period.title)"
    }
}

/// A screen the Stats page pushes: a tap's route, or a section's show-all list.
enum StatsPageDestination: Hashable, Identifiable {
    case route(StatsRoute)
    case highestRated(StatsScope, total: Int)
    case mostLogged(StatsScope, title: String, total: Int)
    case collections(mediaType: String, group: StatsCollectionGroup, total: Int)
    case listProgress([StatsListRing])

    var id: Self { self }
}

/// The Stats page: the year pills and the media picker, then the page's sections in their tab's
/// order (STAT-218), each row its own lazy item.
struct StatsView: View {
    @State private var viewModel: StatsViewModel
    @State private var destination: StatsPageDestination?
    @State private var coverRoute: StatsRoute?

    private let username: String?
    private let services: StatsRouteServices

    init(
        profileRepository: ProfileRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .profile,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        username: String? = nil,
        onUnauthorized: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: StatsViewModel(
            profileRepository: profileRepository,
            username: username,
            onUnauthorized: onUnauthorized
        ))
        self.username = username
        services = StatsRouteServices(
            profileRepository: profileRepository,
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

    var body: some View {
        StatsShell(viewModel: viewModel) { page in
            ForEach(StatsPageSection.sections(for: page)) { section in
                sectionRows(section, page: page)
            }
        }
        .navigationDestination(item: $destination) { destination in
            destinationView(destination)
        }
        .fullScreenCover(item: $coverRoute, onDismiss: {
            // onAppear doesn't run when a cover closes; a change made inside should reload.
            viewModel.appeared()
        }) { route in
            StatsRouteDestination(route: route, services: services)
        }
    }

    // MARK: Sections

    @ViewBuilder
    private func sectionRows(_ section: StatsPageSection, page: StatsPage) -> some View {
        let mediaType = page.mediaType
        // Charts open the diary only on your own stats (STAT-401).
        let drillDown: ((StatsDrillDown) -> Void)? = isOwnStats ? { open($0, page: page) } : nil
        // On Global, a Time row switches to its type.
        let selectType: ((String) -> Void)? = mediaType == nil ? { viewModel.selectMediaType($0) } : nil

        switch section {
        case .header:
            if let header = page.header {
                StatsHeaderSection(header: header, mediaType: mediaType)
            }
        case .time:
            if let time = page.time {
                StatsTimeSection(
                    time: time,
                    mediaType: mediaType,
                    onSelectMediaType: selectType
                )
            }
        case .top:
            if let top = page.top {
                let refs = top.items.map(\.media.ref)
                StatsTopSection(
                    top: top,
                    highestRatedTotal: page.highestRated?.total ?? 0,
                    onOpenTitle: { open(.title($0, within: refs), page: page) },
                    onOpenList: { open(.list(id: $0.id), page: page) },
                    onShowAllHighestRated: { showHighestRated(page) }
                )
            }
        case .highestRated:
            if let highestRated = page.highestRated {
                let refs = highestRated.items.map(\.media.ref)
                StatsHighestRatedSection(
                    highestRated: highestRated,
                    onOpenTitle: { open(.title($0, within: refs), page: page) },
                    onShowAll: { showHighestRated(page) }
                )
            }
        case .activity:
            if let activity = page.activity {
                StatsActivityChartSection(activity: activity, mediaType: mediaType, onDrillDown: drillDown)
            }
        case .ratings:
            if let ratings = page.ratings {
                StatsRatingsChartSection(ratings: ratings, mediaType: mediaType, onDrillDown: drillDown)
            }
        case .ratedVsEveryone:
            if let ratedVsEveryone = page.ratedVsEveryone {
                let refs = (ratedVsEveryone.higher + ratedVsEveryone.lower).map(\.media.ref)
                StatsRatedVsEveryoneSection(ratedVsEveryone: ratedVsEveryone) {
                    open(.title($0, within: refs), page: page)
                }
            }
        case .releaseYears:
            if let releaseYears = page.releaseYears {
                StatsReleaseYearsChartSection(
                    releaseYears: releaseYears,
                    mediaType: mediaType,
                    currentYear: page.years?.current ?? Calendar.current.component(.year, from: Date()),
                    onDrillDown: drillDown
                )
            }
        case .genres:
            if let genres = page.genres {
                StatsGenresSection(genres: genres, mediaType: mediaType, onDrillDown: drillDown)
            }
        case .languages:
            if let languages = page.languages {
                StatsLanguagesSection(languages: languages, mediaType: mediaType, onDrillDown: drillDown)
            }
        case .listProgress:
            StatsListProgressSection(
                rings: page.listRings,
                onOpen: { open($0, page: page) },
                onShowAll: { destination = .listProgress(page.listRings) }
            )
        case .collections:
            if let collections = page.collections, let tabType = mediaType {
                StatsCollectionsSection(
                    collections: collections,
                    onOpen: { open($0, page: page) },
                    onShowAll: { group in
                        let total = group == .complete ? collections.completeCount : collections.almostCount
                        destination = .collections(mediaType: tabType, group: group, total: total)
                    }
                )
            }
        case .mostLogged:
            if let mostLogged = page.mostLogged {
                let refs = mostLogged.items.map(\.media.ref)
                StatsMostLoggedSection(
                    mostLogged: mostLogged,
                    mediaType: mediaType,
                    onOpenTitle: { open(.title($0, within: refs), page: page) },
                    onShowAll: {
                        destination = .mostLogged(
                            scope(of: page),
                            title: StatsMostLoggedSection.title(for: mostLogged, mediaType: mediaType),
                            total: mostLogged.total
                        )
                    }
                )
            }
        case .milestones:
            StatsMilestonesSection(milestones: page.milestones) { milestone in
                open(.milestone(milestone), page: page)
            }
        }
    }

    // MARK: Navigation

    private var isOwnStats: Bool {
        username == nil
    }

    /// The scope a page answers: its own tab, and the period it was asked for.
    private func scope(of page: StatsPage) -> StatsScope {
        viewModel.pageScope ?? StatsScope(period: viewModel.selectedPeriod, mediaType: page.mediaType)
    }

    private func open(_ drillDown: StatsDrillDown, page: StatsPage) {
        let pageScope = scope(of: page)
        let context = StatsDrillDownContext(
            period: pageScope.period,
            mediaType: page.mediaType,
            isOwnStats: isOwnStats
        )
        guard let route = drillDown.route(in: context) else { return }
        if route.presentsFullScreen {
            coverRoute = route
        } else {
            destination = .route(route)
        }
    }

    private func showHighestRated(_ page: StatsPage) {
        destination = .highestRated(scope(of: page), total: page.highestRated?.total ?? 0)
    }

    @ViewBuilder
    private func destinationView(_ destination: StatsPageDestination) -> some View {
        switch destination {
        case let .route(route):
            StatsRouteDestination(route: route, services: services)
        case let .highestRated(scope, total):
            StatsHighestRatedListView(
                viewModel: .highestRated(
                    profileRepository: services.profileRepository,
                    username: username,
                    period: scope.period,
                    mediaType: scope.mediaType,
                    expectedTotal: total,
                    onUnauthorized: services.onUnauthorized
                ),
                scopeTitle: StatsPageSection.scopeTitle(mediaType: scope.mediaType, period: scope.period),
                mediaRepository: services.mediaRepository,
                trackingRepository: services.trackingRepository,
                diaryRepository: services.diaryRepository,
                listRepository: services.listRepository,
                currentUserId: services.currentUserId,
                selectedTab: services.selectedTab,
                onSelectTab: services.onSelectTab,
                onUnauthorized: services.onUnauthorized
            )
        case let .mostLogged(scope, title, total):
            StatsMostLoggedView(
                viewModel: .mostLogged(
                    profileRepository: services.profileRepository,
                    username: username,
                    period: scope.period,
                    mediaType: scope.mediaType,
                    expectedTotal: total,
                    onUnauthorized: services.onUnauthorized
                ),
                title: title,
                scopeTitle: StatsPageSection.scopeTitle(mediaType: scope.mediaType, period: scope.period),
                mediaRepository: services.mediaRepository,
                trackingRepository: services.trackingRepository,
                diaryRepository: services.diaryRepository,
                listRepository: services.listRepository,
                currentUserId: services.currentUserId,
                selectedTab: services.selectedTab,
                onSelectTab: services.onSelectTab,
                onUnauthorized: services.onUnauthorized
            )
        case let .collections(mediaType, group, total):
            StatsCollectionsListView(
                viewModel: .collections(
                    profileRepository: services.profileRepository,
                    username: username,
                    mediaType: mediaType,
                    group: group,
                    expectedTotal: total,
                    onUnauthorized: services.onUnauthorized
                ),
                group: group,
                services: services
            )
        case let .listProgress(rings):
            StatsListProgressListView(rings: rings, services: services)
        }
    }
}
