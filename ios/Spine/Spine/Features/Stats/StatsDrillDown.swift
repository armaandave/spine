import SwiftUI

/// Whose stats, for which range and tab. Drill-downs into the diary exist only on your own stats,
/// because the diary list is always the signed-in user's (STAT-401).
struct StatsDrillDownContext: Hashable {
    let period: StatsPeriod
    /// The tab; nil is Global.
    let mediaType: String?
    let isOwnStats: Bool
}

/// What a tap on a stats row or chart part opens (STAT-401).
///
/// A chart case's `mediaType` is one type's part of a Global bar. On a type tab the tab's type
/// applies anyway, so it can be left nil there.
enum StatsDrillDown: Hashable {
    /// A type tab's genre, with every stored spelling of it (`values`).
    case genre(name: String, values: [String])
    /// A Global genre group (STAT-211).
    case genreGroup(key: String, name: String, mediaType: String? = nil)
    case language(name: String, mediaType: String? = nil)
    case releaseYear(Int, mediaType: String? = nil)
    /// A decade, by its first year: 1990 is 1990–1999.
    case releaseDecade(Int, mediaType: String? = nil)
    /// A ratings bar, with the stored bounds the payload sends (STAT-204).
    case rating(StatsRatingsBucket, mediaType: String? = nil)
    /// An activity month, "YYYY-MM".
    case month(String, mediaType: String? = nil)
    /// A title in Top, Highest rated, Rated higher than most or Most logged. `within` is the
    /// section's titles, for swiping between title pages.
    case title(MediaSummary, within: [MediaRef] = [])
    case liked
    case tag(String)
    case milestone(StatsMilestone)
    /// Top's list or a list progress ring.
    case list(id: Int)
    case collection(MediaSeriesSummary)

    /// A genre row: a group on Global, the provider's genre on a type tab.
    static func forGenre(_ row: StatsGenreRow, mediaType: String? = nil) -> StatsDrillDown {
        if let key = row.key {
            return .genreGroup(key: key, name: row.name, mediaType: mediaType)
        }
        let values = (row.values ?? []).isEmpty ? [row.name] : (row.values ?? [])
        return .genre(name: row.name, values: values)
    }

    /// Where the tap goes, or nil when the row isn't tappable for this viewer.
    /// On someone else's stats, titles (and milestone posters) open the title page, lists and
    /// series open as usual, and charts aren't tappable.
    func route(in context: StatsDrillDownContext) -> StatsRoute? {
        switch self {
        case let .list(id):
            return .list(id: id)
        case let .collection(series):
            return .series(series.ref)
        case let .title(media, within):
            if context.isOwnStats, media.ref.itemId != nil {
                return .diary(self, context)
            }
            return .title(MediaBrowsingSelection(ref: media.ref, within: within.isEmpty ? [media.ref] : within))
        case let .milestone(milestone):
            if context.isOwnStats, let entryId = milestone.entryId {
                return .log(entryId: entryId)
            }
            return .title(MediaBrowsingSelection(ref: milestone.media.ref, within: [milestone.media.ref]))
        case .genre, .genreGroup, .language, .releaseYear, .releaseDecade, .rating, .month, .liked, .tag:
            return context.isOwnStats ? .diary(self, context) : nil
        }
    }

    /// The diary filter for this drill-down: always `skip_backfill`, `tv_rollup` and the range's
    /// dates, plus the tab's or the part's media types and the row's own filter (STAT-401).
    func diaryFilter(in context: StatsDrillDownContext) -> MediaFilterState {
        var filter = MediaFilterState()
        filter.skipBackfill = true
        filter.tvRollup = true
        if let range = Self.dayRange(for: context.period) {
            filter.watchedFrom = MediaFilterState.date(fromDay: range.from)
            filter.watchedTo = MediaFilterState.date(fromDay: range.to)
        }
        filter.mediaTypes = Self.diaryMediaTypes(for: partMediaType ?? context.mediaType)

        switch self {
        case let .genre(_, values):
            filter.genres = values
        case let .genreGroup(key, _, _):
            filter.genreGroups = [key]
        case let .language(name, _):
            filter.originalLanguages = [name]
        case let .releaseYear(year, _):
            filter.year = year
        case let .releaseDecade(start, _):
            filter.yearMin = start
            filter.yearMax = start + 9
        case let .rating(bucket, _):
            filter.ratingMin = Self.storedRating(bucket.ratingMin)
            filter.ratingMax = Self.storedRating(bucket.ratingMax)
        case let .month(month, _):
            if let range = Self.dayRange(forMonth: month) {
                filter.watchedFrom = MediaFilterState.date(fromDay: range.from)
                filter.watchedTo = MediaFilterState.date(fromDay: range.to)
            }
        case let .title(media, _):
            filter.itemId = media.ref.itemId
        case .liked:
            filter.liked = true
        case let .tag(tag):
            filter.tag = tag
        case .milestone, .list, .collection:
            break
        }
        return filter
    }

    /// The filtered diary's title: the stat, then the part's type on Global, then the range,
    /// for example "Sci-Fi & Fantasy · 2025".
    func diaryTitle(in context: StatsDrillDownContext) -> String {
        var parts = [label]
        if context.mediaType == nil, let partMediaType {
            parts.append(MediaTypeTheme.theme(for: partMediaType).displayName)
        }
        if case .month = self {
            // The month already names its year.
        } else {
            parts.append(context.period.title)
        }
        return parts.joined(separator: " · ")
    }

    private var label: String {
        switch self {
        case let .genre(name, _), let .genreGroup(_, name, _), let .language(name, _):
            return name
        case let .releaseYear(year, _):
            return "Released in \(year)"
        case let .releaseDecade(start, _):
            return "Released in the \(start)s"
        case let .rating(bucket, _):
            return Self.starsLabel(bucket.stars)
        case let .month(month, _):
            return Self.monthTitle(month) ?? month
        case let .title(media, _):
            return media.displayTitle
        case .liked:
            return "Likes"
        case let .tag(tag):
            return tag
        case let .milestone(milestone):
            return milestone.media.displayTitle
        case .list, .collection:
            return ""
        }
    }

    private var partMediaType: String? {
        switch self {
        case let .genreGroup(_, _, mediaType), let .language(_, mediaType), let .releaseYear(_, mediaType),
             let .releaseDecade(_, mediaType), let .rating(_, mediaType), let .month(_, mediaType):
            mediaType
        default:
            nil
        }
    }

    /// One type's diary media types; TV also asks for its season and episode logs.
    static func diaryMediaTypes(for mediaType: String?) -> [String] {
        guard let mediaType else { return [] }
        return mediaType == "tv" ? ["tv", "season", "episode"] : [mediaType]
    }

    /// A year pill's first and last day; All Time has none.
    static func dayRange(for period: StatsPeriod) -> (from: String, to: String)? {
        switch period {
        case .allTime:
            nil
        case let .year(year):
            (String(format: "%04d-01-01", year), String(format: "%04d-12-31", year))
        }
    }

    /// A "YYYY-MM" month's first and last day.
    static func dayRange(forMonth month: String) -> (from: String, to: String)? {
        let parts = month.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 2, (1 ... 12).contains(parts[1]) else { return nil }
        let (year, monthNumber) = (parts[0], parts[1])
        let isLeap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
        let lastDay = switch monthNumber {
        case 2: isLeap ? 29 : 28
        case 4, 6, 9, 11: 30
        default: 31
        }
        return (
            String(format: "%04d-%02d-01", year, monthNumber),
            String(format: "%04d-%02d-%02d", year, monthNumber, lastDay)
        )
    }

    /// "3.5 stars", "4 stars", "1 star".
    static func starsLabel(_ stars: StatsDecimal) -> String {
        let value = stars.value
        let text = value.rounded() == value ? String(Int(value)) : stars.text
        return value == 1 ? "1 star" : "\(text) stars"
    }

    /// A stored 0–10 bound sent as it is ("6.5").
    private static func storedRating(_ bound: StatsDecimal) -> Decimal? {
        Decimal(string: bound.text, locale: Locale(identifier: "en_US_POSIX"))
    }

    private static func monthTitle(_ month: String) -> String? {
        guard let range = dayRange(forMonth: month),
              let date = MediaFilterState.date(fromDay: range.from) else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "LLLL yyyy"
        return formatter.string(from: date)
    }
}

/// A screen a stats tap opens. Series and title pages bring their own navigation, so they present
/// full screen; the rest push onto the Stats navigation stack.
enum StatsRoute: Hashable, Identifiable {
    case diary(StatsDrillDown, StatsDrillDownContext)
    case log(entryId: Int)
    case list(id: Int)
    case series(SeriesRef)
    case title(MediaBrowsingSelection)

    var id: Self { self }

    var presentsFullScreen: Bool {
        switch self {
        case .series, .title: true
        case .diary, .log, .list: false
        }
    }
}

/// What the stats screens need to open their routes.
struct StatsRouteServices {
    var profileRepository: ProfileRepository
    var mediaRepository: MediaRepository
    var trackingRepository: TrackingRepository
    var diaryRepository: DiaryRepository
    var listRepository: ListRepository
    var peopleRepository: PeopleRepository = AppRepositories.current().people
    var activityRepository: ActivityRepository = AppRepositories.current().activity
    var currentUserId: Int?
    var selectedTab: AppTab = .profile
    var onSelectTab: (AppTab) -> Void = { _ in }
    var onUnauthorized: () -> Void = {}
}

/// The screen for a route: use it in `navigationDestination(item:)` for pushed routes and in
/// `fullScreenCover(item:)` for `presentsFullScreen` ones.
struct StatsRouteDestination: View {
    let route: StatsRoute
    let services: StatsRouteServices

    var body: some View {
        switch route {
        case let .diary(drillDown, context):
            FilteredDiaryView(
                title: drillDown.diaryTitle(in: context),
                filter: drillDown.diaryFilter(in: context),
                diaryRepository: services.diaryRepository,
                mediaRepository: services.mediaRepository,
                trackingRepository: services.trackingRepository,
                currentUserId: services.currentUserId,
                selectedTab: services.selectedTab,
                onSelectTab: services.onSelectTab,
                onUnauthorized: services.onUnauthorized
            )
        case let .log(entryId):
            DiaryLogDetailView(
                entryId: entryId,
                diaryRepository: services.diaryRepository,
                mediaRepository: services.mediaRepository,
                trackingRepository: services.trackingRepository,
                currentUserId: services.currentUserId,
                selectedTab: services.selectedTab,
                onSelectTab: services.onSelectTab,
                onUnauthorized: services.onUnauthorized
            )
        case let .list(id):
            ProfileListDetailView(
                listId: id,
                profileRepository: services.profileRepository,
                listRepository: services.listRepository,
                peopleRepository: services.peopleRepository,
                mediaRepository: services.mediaRepository,
                trackingRepository: services.trackingRepository,
                diaryRepository: services.diaryRepository,
                activityRepository: services.activityRepository,
                currentUserId: services.currentUserId,
                selectedTab: services.selectedTab,
                onSelectTab: services.onSelectTab,
                onUnauthorized: services.onUnauthorized
            )
        case let .series(ref):
            SeriesDetailView(
                ref: ref,
                mediaRepository: services.mediaRepository,
                trackingRepository: services.trackingRepository,
                diaryRepository: services.diaryRepository,
                listRepository: services.listRepository,
                peopleRepository: services.peopleRepository,
                currentUserId: services.currentUserId,
                selectedTab: services.selectedTab,
                onSelectTab: services.onSelectTab,
                onUnauthorized: services.onUnauthorized
            )
        case let .title(selection):
            MediaDetailView(
                ref: selection.ref,
                browsingContext: selection.context,
                mediaRepository: services.mediaRepository,
                trackingRepository: services.trackingRepository,
                diaryRepository: services.diaryRepository,
                listRepository: services.listRepository,
                peopleRepository: services.peopleRepository,
                currentUserId: services.currentUserId,
                selectedTab: services.selectedTab,
                onSelectTab: services.onSelectTab,
                onUnauthorized: services.onUnauthorized
            )
        }
    }
}
