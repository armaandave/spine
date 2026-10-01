import SwiftUI

struct StatsView: View {
    @State private var viewModel: StatsViewModel
    @State private var selectedMediaType: String?
    @State private var selectedRef: MediaRef?
    @State private var scrollOffset: CGFloat = 0
    @State private var scrollPosition = ScrollPosition(edge: .top)

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

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
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            RadialGradient(
                colors: [StatsPalette.accent(for: selectedMediaType).opacity(selectedMediaType == nil ? 0.05 : 0.12), .clear],
                center: .topLeading,
                startRadius: 0,
                endRadius: 420
            )
            .ignoresSafeArea()
            .allowsHitTesting(false)
            .animation(.smooth(duration: 0.4), value: selectedMediaType)

            StatsAtmosphere(media: atmosphereMedia)
                .offset(y: -scrollOffset)
                .opacity(HomeBackdropMotion.opacity(for: scrollOffset))

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 26) {
                    periodPicker
                    stateContent
                }
                .padding(.horizontal, 16)
                .padding(.top, 10)
                .padding(.bottom, 48)
            }
            .scrollPosition($scrollPosition)
            .scrollEdgeEffectStyle(.soft, for: .top)
            .refreshable {
                await viewModel.reload()
            }
            .onScrollGeometryChange(for: CGFloat.self) { geometry in
                max(0, geometry.contentOffset.y + geometry.contentInsets.top)
            } action: { _, offset in
                scrollOffset = offset
            }
        }
        .navigationTitle("Stats")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if case .initial = viewModel.state {
                await viewModel.load()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { _ in
            Task { await viewModel.reload() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .customListsDidChange)) { _ in
            Task { await viewModel.reload() }
        }
        .fullScreenCover(item: $selectedRef, onDismiss: { selectedRef = nil }) { ref in
            MediaDetailView(
                ref: ref,
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

    private var atmosphereMedia: MediaSummary? {
        guard let summary = viewModel.summary else { return nil }
        return StatsScopeSnapshot(summary: summary, mediaType: selectedMediaType).featuredMedia
    }

    private var periodPicker: some View {
        let accent = StatsPalette.accent(for: selectedMediaType)

        return ScrollView(.horizontal, showsIndicators: false) {
            GlassEffectContainer(spacing: 8) {
                HStack(spacing: 8) {
                    ForEach([StatsPeriod.allTime] + StatsPeriod.recentYears()) { period in
                        let isSelected = viewModel.selectedPeriod == period

                        Button {
                            Task { await viewModel.selectPeriod(period) }
                        } label: {
                            HStack(spacing: 6) {
                                if viewModel.isLoading, isSelected {
                                    ProgressView()
                                        .controlSize(.mini)
                                        .tint(.white)
                                }
                                Text(period.title)
                            }
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.white.opacity(isSelected ? 0.96 : 0.52))
                            .padding(.horizontal, 15)
                            .frame(minWidth: 44, minHeight: 44)
                        }
                        .buttonStyle(.plain)
                        .glassEffect(
                            isSelected
                                ? .regular.tint(accent.opacity(0.14)).interactive()
                                : .clear.interactive(),
                            in: Capsule()
                        )
                        .accessibilityLabel("Show stats for \(period.title)")
                        .accessibilityAddTraits(isSelected ? .isSelected : [])
                    }
                }
            }
            .padding(.vertical, 2)
        }
    }

    @ViewBuilder
    private var stateContent: some View {
        switch viewModel.state {
        case .initial, .loading:
            StatsLoadingView()
                .spineContentTransition(value: StatsContentPhase.loading)
        case let .loaded(summary):
            statsContent(summary)
                .spineContentTransition(value: StatsContentPhase.loaded(viewModel.selectedPeriod.id))
        case .empty:
            StatsEmptyView(
                title: "Your stats are waiting",
                message: "Track or log something in this period to start seeing your media story."
            )
            .spineContentTransition(value: StatsContentPhase.empty)
        case let .error(message):
            StatsErrorView(message: message) {
                Task { await viewModel.retry() }
            }
            .spineContentTransition(value: StatsContentPhase.error)
        }
    }

    private func statsContent(_ summary: StatsSummary) -> some View {
        let scope = StatsScopeSnapshot(summary: summary, mediaType: selectedMediaType)
        let accent = StatsPalette.accent(for: selectedMediaType)

        return VStack(alignment: .leading, spacing: 30) {
            mediaPicker(summary)

            if scope.isEmpty {
                StatsEmptyView(
                    title: scope.isAllTime
                        ? "No stats for \(scope.title) yet"
                        : "No stats for \(scope.title) in \(viewModel.selectedPeriod.title)",
                    message: "Choose another media type or period to explore your history."
                )
                .transition(.opacity)
            } else {
                StatsHero(scope: scope, period: viewModel.selectedPeriod)

                VStack(alignment: .leading, spacing: 34) {
                    if selectedMediaType == nil {
                        StatsActivitySection(summary: summary, tint: accent)
                        StatsMediaMixSection(summary: summary) { mediaType in
                            withAnimation(.snappy(duration: 0.25)) {
                                selectedMediaType = mediaType
                            }
                            withAnimation(.smooth(duration: 0.45)) {
                                scrollPosition.scrollTo(edge: .top)
                            }
                        }
                    }

                    StatsRatingsSection(points: scope.ratingPoints)

                    if !scope.topRated.isEmpty {
                        StatsSection(title: "Top rated") {
                            StatsPosterRail(items: scope.topRated.map {
                                let stars = StatsCopy.stars($0.rating, for: $0.media)
                                return StatsPosterRailItem(
                                    media: $0.media,
                                    stars: stars,
                                    caption: stars == nil ? "Rated" : nil,
                                    accessibilityCaption: stars.map {
                                        "rated \(SWStatsRatingChart.starLabel($0)) out of 5 stars"
                                    } ?? "rated"
                                )
                            }) { media in
                                selectedRef = media.ref
                            }
                        }
                    }

                    StatsReleaseYearsSection(years: scope.releaseYears, tint: accent)

                    StatsTasteSection(
                        genres: scope.topGenres,
                        languages: scope.topLanguages,
                        coverage: scope.metadataCoverage,
                        tint: accent
                    )

                    if !scope.mostLogged.isEmpty {
                        StatsSection(title: "Most logged") {
                            StatsPosterRail(items: scope.mostLogged.map {
                                StatsPosterRailItem(
                                    media: $0.media,
                                    caption: StatsCopy.logs($0.logCount),
                                    accessibilityCaption: StatsCopy.logs($0.logCount)
                                )
                            }) { media in
                                selectedRef = media.ref
                            }
                        }
                    }
                }
                .id(selectedMediaType ?? APIConstants.allMedia)
                .transition(.opacity)
            }
        }
    }

    private func mediaPicker(_ summary: StatsSummary) -> some View {
        let advertised = summary.mediaTypes.map(\.mediaType)
        let extra = advertised.filter { !APIConstants.fallbackMediaTypes.contains($0) }

        return MediaSearchLensPicker(
            selectedType: Binding(
                get: { selectedMediaType ?? "" },
                set: { selectedMediaType = $0.isEmpty ? nil : $0 }
            ),
            availableTypes: APIConstants.fallbackMediaTypes + extra,
            horizontalPadding: 0,
            fitsAllTypes: true,
            allowsEmptySelection: true,
            isCompact: true
        ) { selectedType in
            withAnimation(.snappy(duration: 0.25)) {
                selectedMediaType = selectedType.isEmpty ? nil : selectedType
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Media type")
        .accessibilityValue(
            selectedMediaType.map { MediaTypeTheme.theme(for: $0).displayName } ?? "All Media"
        )
        .accessibilityAction(named: "Show All Media") {
            selectedMediaType = nil
        }
    }
}

/// Blurred artwork wash behind the header, fading into the page with no hard
/// edge. It follows the featured title of the current scope.
private struct StatsAtmosphere: View {
    let media: MediaSummary?

    var body: some View {
        SpineAsyncImage(url: HomeArtworkAtmosphereModel.url(for: media)) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Color.clear
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 470)
        .clipped()
        .blur(radius: 34)
        .saturation(1.15)
        .opacity(0.6)
        .mask {
            LinearGradient(
                stops: [
                    .init(color: .black, location: 0),
                    .init(color: .black, location: 0.3),
                    .init(color: .clear, location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

private enum StatsContentPhase: Hashable {
    case loading
    case loaded(String)
    case empty
    case error
}

// MARK: - Scope

private struct StatsScopeSnapshot {
    let title: String
    let completedCount: Int
    let diaryEntryCount: Int
    let uniqueLoggedCount: Int
    let reviewCount: Int
    let repeatCount: Int
    let ratedCount: Int
    let likedCount: Int
    let ratingPoints: [SWStatsRatingPoint]
    let releaseYears: [StatsReleaseYearBucket]
    let topGenres: [StatsNamedCount]
    let topLanguages: [StatsNamedCount]
    let metadataCoverage: StatsMetadataCoverage
    let topRated: [StatsTopRatedItem]
    let mostLogged: [StatsMostLoggedItem]
    let isAllTime: Bool

    init(summary: StatsSummary, mediaType: String?) {
        isAllTime = summary.range.isAllTime
        if let mediaType {
            title = MediaTypeTheme.theme(for: mediaType).displayName
            if let media = summary.mediaTypeSummary(for: mediaType) {
                completedCount = media.completedCount
                diaryEntryCount = media.diaryEntryCount
                uniqueLoggedCount = media.uniqueLoggedCount
                reviewCount = media.reviewCount
                repeatCount = media.repeatCount
                ratedCount = media.ratedCount
                likedCount = media.likedCount
                ratingPoints = SWStatsRatingChart.normalizedPoints(
                    from: media.ratingDistribution,
                    mediaType: mediaType
                )
                releaseYears = media.releaseYears
                topGenres = media.topGenres
                topLanguages = media.topLanguages
                metadataCoverage = media.metadataCoverage
                topRated = media.topRated
                mostLogged = media.mostLogged
            } else {
                completedCount = 0
                diaryEntryCount = 0
                uniqueLoggedCount = 0
                reviewCount = 0
                repeatCount = 0
                ratedCount = 0
                likedCount = 0
                ratingPoints = []
                releaseYears = []
                topGenres = []
                topLanguages = []
                metadataCoverage = .empty
                topRated = []
                mostLogged = []
            }
        } else {
            let typedPoints = SWStatsRatingChart.normalizedPoints(from: summary.mediaTypes)
            title = "All Media"
            completedCount = summary.overview.completedCount
            diaryEntryCount = summary.overview.diaryEntryCount
            uniqueLoggedCount = summary.overview.uniqueLoggedCount
            reviewCount = summary.overview.reviewCount
            repeatCount = summary.overview.repeatCount
            ratedCount = summary.overview.ratedCount
            likedCount = summary.overview.likedCount
            ratingPoints = typedPoints.contains { $0.count > 0 }
                ? typedPoints
                : SWStatsRatingChart.normalizedPoints(from: summary.ratingDistribution, mediaType: nil)
            releaseYears = summary.releaseYears
            topGenres = summary.topGenres
            topLanguages = summary.topLanguages
            metadataCoverage = summary.metadataCoverage
            topRated = summary.topRated
            mostLogged = summary.mostLogged
        }
    }

    var featuredMedia: MediaSummary? {
        topRated.first?.media ?? mostLogged.first?.media
    }

    var isEmpty: Bool {
        if !isAllTime {
            return diaryEntryCount == 0
                && uniqueLoggedCount == 0
                && reviewCount == 0
                && repeatCount == 0
                && ratedCount == 0
                && topRated.isEmpty
                && mostLogged.isEmpty
        }
        return completedCount == 0
            && diaryEntryCount == 0
            && uniqueLoggedCount == 0
            && ratedCount == 0
            && topRated.isEmpty
            && mostLogged.isEmpty
    }
}

// MARK: - Hero

private struct StatsHero: View {
    let scope: StatsScopeSnapshot
    let period: StatsPeriod

    private var primaryCount: Int {
        scope.isAllTime ? scope.completedCount : scope.uniqueLoggedCount
    }

    private var primaryLabel: String {
        let noun = primaryCount == 1 ? "title" : "titles"
        return scope.isAllTime ? "\(noun) completed" : "\(noun) logged in \(period.title)"
    }

    private var chips: [StatsHeroChipItem] {
        [
            StatsHeroChipItem(title: "Logs", value: scope.diaryEntryCount, systemName: "calendar"),
            StatsHeroChipItem(title: "Reviews", value: scope.reviewCount, systemName: "text.quote"),
            StatsHeroChipItem(title: "Repeats", value: scope.repeatCount, systemName: "arrow.clockwise"),
            scope.isAllTime
                ? StatsHeroChipItem(title: "Likes", value: scope.likedCount, systemName: "heart")
                : StatsHeroChipItem(title: "Rated", value: scope.ratedCount, systemName: "star"),
        ]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 0) {
                Text(primaryCount.formatted())
                    .font(.system(size: 68, weight: .heavy))
                    .tracking(-2)
                    .foregroundStyle(.white)
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                    .contentTransition(.numericText(value: Double(primaryCount)))

                Text(primaryLabel)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.62))
                    .contentTransition(.opacity)
            }
            .accessibilityElement(children: .combine)

            HStack(spacing: 8) {
                ForEach(chips) { chip in
                    StatsHeroChip(item: chip)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(.snappy(duration: 0.3), value: primaryCount)
    }
}

private struct StatsHeroChipItem: Identifiable {
    let title: String
    let value: Int
    let systemName: String

    var id: String { title }
}

private struct StatsHeroChip: View {
    private static let cornerRadius: CGFloat = 10.5

    let item: StatsHeroChipItem

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous)

        VStack(spacing: 2.5) {
            HStack(spacing: 3.5) {
                Image(systemName: item.systemName)
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))

                Text(item.value.formatted())
                    .font(.system(size: 15.5, weight: .bold))
                    .foregroundStyle(.white.opacity(0.94))
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.62)
                    .contentTransition(.numericText(value: Double(item.value)))
            }

            Text(item.title)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.white.opacity(0.5))
                .lineLimit(1)
                .minimumScaleFactor(0.72)
        }
        .frame(maxWidth: .infinity, minHeight: 56)
        .background(.black.opacity(0.15), in: shape)
        .glassEffect(.regular.tint(.white.opacity(0.06)), in: shape)
        .overlay {
            shape.strokeBorder(.white.opacity(0.18), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.12), radius: 3.5, y: 1.75)
        .animation(.snappy(duration: 0.3), value: item.value)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(item.value.formatted()) \(item.title)")
    }
}

// MARK: - Activity

private struct StatsActivitySection: View {
    let summary: StatsSummary
    let tint: Color

    @State private var selectedBucketID: String?
    @State private var selectedWeekdayID: String?

    var body: some View {
        let series = StatsActivitySeries.make(months: summary.activity.months, range: summary.range)

        if summary.overview.diaryEntryCount > 0 || !series.buckets.isEmpty {
            StatsSection(title: "Activity") {
                VStack(spacing: 10) {
                    StatsCard {
                        VStack(alignment: .leading, spacing: 18) {
                            headline(series)

                            if series.total > 0 {
                                StatsBarChart(
                                    items: series.buckets.map {
                                        StatsBarItem(
                                            id: $0.id,
                                            value: $0.count,
                                            axisLabel: $0.axisLabel,
                                            accessibilityLabel: $0.title
                                        )
                                    },
                                    tint: tint,
                                    height: 150,
                                    emphasizedID: series.busiest?.id,
                                    accessibilityTitle: series.granularity == .year ? "Logs by year" : "Logs by month",
                                    selectedID: $selectedBucketID
                                )
                            }

                            Rectangle()
                                .fill(.white.opacity(0.06))
                                .frame(height: 0.5)

                            StatsInlineMetrics(metrics: [
                                StatsInlineMetric(
                                    title: "Active days",
                                    value: summary.overview.activeDays.formatted()
                                ),
                                StatsInlineMetric(
                                    title: "Current streak",
                                    value: StatsCopy.days(summary.overview.currentStreakDays)
                                ),
                                StatsInlineMetric(
                                    title: "Longest streak",
                                    value: StatsCopy.days(summary.overview.longestStreakDays)
                                ),
                            ])
                        }
                    }

                    weekdayCard
                }
            }
        }
    }

    @ViewBuilder
    private func headline(_ series: StatsActivitySeries) -> some View {
        if let selectedBucketID, let bucket = series.buckets.first(where: { $0.id == selectedBucketID }) {
            StatsHeadline(
                value: bucket.count.formatted(),
                unit: bucket.count == 1 ? "log" : "logs",
                caption: bucket.title
            )
        } else {
            let total = summary.overview.diaryEntryCount
            StatsHeadline(
                value: total.formatted(),
                unit: total == 1 ? "log" : "logs",
                caption: busiestCaption(series)
            )
        }
    }

    private func busiestCaption(_ series: StatsActivitySeries) -> String {
        guard let busiest = series.busiest else { return "No logs in this period" }
        switch series.granularity {
        case .year:
            return "Busiest year was \(busiest.title)"
        case .month:
            let month = busiest.title.split(separator: " ").first.map(String.init) ?? busiest.title
            return "Busiest month was \(month)"
        }
    }

    @ViewBuilder
    private var weekdayCard: some View {
        let counts = StatsWeekdayDistribution.activeDays(from: summary.activity.days)
        let totalDays = counts.reduce(0, +)

        if totalDays > 0 {
            let peakIndex = summary.activity.mostActiveWeekday?.weekday
                ?? counts.indices.max { counts[$0] < counts[$1] }
                ?? 0
            let selectedIndex = selectedWeekdayID.flatMap(Int.init)
            let shownIndex = selectedIndex ?? peakIndex
            let shownCount = counts.indices.contains(shownIndex) ? counts[shownIndex] : 0
            let percentage = Double(shownCount) / Double(totalDays) * 100

            StatsCard {
                HStack(alignment: .center, spacing: 18) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(selectedIndex == nil ? "MOST ACTIVE ON" : "ACTIVE ON")
                            .font(.system(size: 10, weight: .heavy))
                            .tracking(0.6)
                            .foregroundStyle(.white.opacity(0.42))
                        Text(StatsWeekdayDistribution.names[min(max(shownIndex, 0), 6)] + "s")
                            .font(.system(size: 22, weight: .bold))
                            .foregroundStyle(.white)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                            .contentTransition(.opacity)
                        Text(
                            selectedIndex == nil
                                ? "\(percentage.formatted(.number.precision(.fractionLength(0))))% of active days"
                                : "\(shownCount.formatted()) active \(shownCount == 1 ? "day" : "days")"
                        )
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.white.opacity(0.5))
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .contentTransition(.numericText())
                    }
                    .animation(.snappy(duration: 0.22), value: shownIndex)
                    .accessibilityElement(children: .combine)

                    Spacer(minLength: 0)

                    StatsBarChart(
                        items: counts.enumerated().map { index, count in
                            StatsBarItem(
                                id: String(index),
                                value: count,
                                axisLabel: StatsWeekdayDistribution.shortNames[index],
                                accessibilityLabel: StatsWeekdayDistribution.names[index]
                            )
                        },
                        tint: tint,
                        height: 78,
                        emphasizedID: String(peakIndex),
                        maximumBarWidth: 13,
                        accessibilityTitle: "Active days by weekday",
                        selectedID: $selectedWeekdayID
                    )
                    .frame(width: 150)
                }
            }
        }
    }
}

// MARK: - Media mix

private struct StatsMediaMixSection: View {
    let summary: StatsSummary
    let onSelect: (String) -> Void

    private var slices: [SWStatsSlice] {
        summary.mediaTypes
            .compactMap { item -> SWStatsSlice? in
                let value = summary.range.isAllTime ? item.completedCount : item.uniqueLoggedCount
                guard value > 0 else { return nil }
                return SWStatsSlice(
                    id: item.mediaType,
                    title: MediaTypeTheme.theme(for: item.mediaType).displayName,
                    value: value,
                    color: StatsPalette.mediaMixColor(for: item.mediaType)
                )
            }
            .sorted { $0.value > $1.value }
    }

    var body: some View {
        let slices = slices
        let total = slices.reduce(0) { $0 + $1.value }

        if !slices.isEmpty {
            StatsSection(title: "Media mix") {
                StatsCard(padding: 16) {
                    VStack(alignment: .leading, spacing: 10) {
                        StatsMixBar(slices: slices)
                            .padding(.bottom, 4)

                        VStack(spacing: 0) {
                            ForEach(Array(slices.enumerated()), id: \.element.id) { index, slice in
                                if index > 0 {
                                    Rectangle()
                                        .fill(.white.opacity(0.06))
                                        .frame(height: 0.5)
                                        .padding(.leading, 44)
                                }

                                Button {
                                    onSelect(slice.id)
                                } label: {
                                    row(slice, total: total)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("\(slice.title), \(slice.value.formatted()), \(StatsCopy.share(slice.value, of: total))")
                                .accessibilityHint("Shows \(slice.title) stats")
                            }
                        }
                    }
                }
            }
        }
    }

    private func row(_ slice: SWStatsSlice, total: Int) -> some View {
        HStack(spacing: 12) {
            MediaTypeGlyph(theme: MediaTypeTheme.theme(for: slice.id), size: 12)
                .frame(width: 32, height: 32)
                .background(slice.color.opacity(0.24), in: Circle())

            Text(slice.title)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.92))
                .lineLimit(1)

            Spacer(minLength: 8)

            Text(slice.value.formatted())
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.92))
                .monospacedDigit()

            Text(StatsCopy.share(slice.value, of: total))
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.white.opacity(0.42))
                .monospacedDigit()
                .frame(minWidth: 36, alignment: .trailing)

            Image(systemName: "chevron.right")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(.white.opacity(0.24))
        }
        .frame(minHeight: 50)
        .contentShape(Rectangle())
    }
}

// MARK: - Ratings

private struct StatsRatingsSection: View {
    let points: [SWStatsRatingPoint]

    @State private var selectedID: String?

    var body: some View {
        let buckets = SWStatsRatingChart.starBuckets(from: points)
        let total = buckets.reduce(0) { $0 + $1.count }

        if total > 0 {
            StatsSection(title: "Ratings") {
                StatsCard {
                    VStack(alignment: .leading, spacing: 18) {
                        headline(buckets: buckets, total: total)

                        VStack(spacing: 8) {
                            StatsBarChart(
                                items: buckets.map {
                                    StatsBarItem(
                                        id: String($0.step),
                                        value: $0.count,
                                        accessibilityLabel: "\(SWStatsRatingChart.starLabel($0.stars)) stars"
                                    )
                                },
                                tint: StatsPalette.rating,
                                height: 104,
                                accessibilityTitle: "Rating distribution",
                                selectedID: $selectedID
                            )

                            HStack {
                                Image(systemName: "star.fill")
                                    .font(.system(size: 9, weight: .bold))
                                    .foregroundStyle(StatsPalette.rating.opacity(0.7))
                                Spacer()
                                HStack(spacing: 1) {
                                    ForEach(0 ..< 5, id: \.self) { _ in
                                        Image(systemName: "star.fill")
                                            .font(.system(size: 9, weight: .bold))
                                    }
                                }
                                .foregroundStyle(StatsPalette.rating.opacity(0.7))
                            }
                            .accessibilityHidden(true)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func headline(buckets: [StatsStarBucket], total: Int) -> some View {
        if let selectedID, let bucket = buckets.first(where: { String($0.step) == selectedID }) {
            StatsHeadline(
                value: bucket.count.formatted(),
                unit: bucket.count == 1 ? "title" : "titles",
                caption: "Rated \(SWStatsRatingChart.starLabel(bucket.stars)) \(bucket.stars == 1 ? "star" : "stars")"
            )
        } else {
            let average = SWStatsRatingChart.averageRating(from: points).map { $0 / 2 }
            StatsHeadline(
                value: average?.formatted(.number.precision(.fractionLength(1))) ?? "—",
                symbol: "star.fill",
                caption: "Average across \(total.formatted()) \(total == 1 ? "rating" : "ratings")"
            )
        }
    }
}

// MARK: - Release years

private struct StatsReleaseYearsSection: View {
    let years: [StatsReleaseYearBucket]
    let tint: Color

    @State private var selectedID: String?

    var body: some View {
        let series = StatsReleaseSeries.make(
            points: years.map { SWStatsYearPoint(year: $0.year, count: $0.count) }
        )

        if let peak = series.peak {
            StatsSection(title: "Release years") {
                StatsCard {
                    VStack(alignment: .leading, spacing: 18) {
                        headline(series, peak: peak)

                        StatsBarChart(
                            items: series.buckets.map {
                                StatsBarItem(
                                    id: $0.id,
                                    value: $0.count,
                                    axisLabel: $0.axisLabel,
                                    accessibilityLabel: $0.title
                                )
                            },
                            tint: tint,
                            height: 140,
                            emphasizedID: peak.id,
                            accessibilityTitle: series.granularity == .decade
                                ? "Titles by release decade"
                                : "Titles by release year",
                            selectedID: $selectedID
                        )
                        .accessibilityIdentifier("stats.releaseChart")
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func headline(_ series: StatsReleaseSeries, peak: StatsActivityBucket) -> some View {
        if let selectedID, let bucket = series.buckets.first(where: { $0.id == selectedID }) {
            StatsHeadline(
                value: bucket.count.formatted(),
                unit: bucket.count == 1 ? "title" : "titles",
                caption: series.granularity == .decade
                    ? "Released in the \(bucket.title)"
                    : "Released in \(bucket.title)"
            )
        } else {
            let years = years.filter { $0.count > 0 }.map(\.year)
            let range = years.min().flatMap { first in
                years.max().map { last in first == last ? nil : "\(first)–\(last)" }
            } ?? nil
            StatsHeadline(
                value: peak.title,
                caption: [
                    "\(series.granularity == .decade ? "Top decade" : "Top year") · \(peak.count.formatted()) titles",
                    range,
                ]
                .compactMap { $0 }
                .joined(separator: " · ")
            )
        }
    }
}

// MARK: - Taste

private enum StatsTasteMode: Hashable {
    case genres
    case languages

    var title: String {
        switch self {
        case .genres: "Genres"
        case .languages: "Languages"
        }
    }
}

private struct StatsTasteSection: View {
    let genres: [StatsNamedCount]
    let languages: [StatsNamedCount]
    let coverage: StatsMetadataCoverage
    let tint: Color

    @State private var mode: StatsTasteMode = .genres

    private var modes: [StatsTasteMode] {
        (genres.isEmpty ? [] : [.genres]) + (languages.isEmpty ? [] : [.languages])
    }

    var body: some View {
        let modes = modes

        if let fallback = modes.first {
            let activeMode = modes.contains(mode) ? mode : fallback
            let items = activeMode == .genres ? genres : languages
            let covered = activeMode == .genres ? coverage.genreItems : coverage.languageItems

            StatsSection(title: "Taste") {
                if modes.count > 1 {
                    StatsSegmentedToggle(
                        options: modes,
                        selection: Binding(get: { activeMode }, set: { mode = $0 }),
                        title: \.title
                    )
                }
            } content: {
                StatsCard {
                    VStack(alignment: .leading, spacing: 16) {
                        StatsRankedBars(items: Array(items.prefix(6)), tint: tint)
                            .id(activeMode)
                            .transition(.opacity)

                        if coverage.totalItems > 0, covered < coverage.totalItems {
                            Text("Based on \(covered.formatted()) of \(coverage.totalItems.formatted()) titles with \(activeMode == .genres ? "genre" : "language") info")
                                .font(.system(size: 11, weight: .medium))
                                .foregroundStyle(.white.opacity(0.36))
                        }
                    }
                }
            }
        }
    }
}

// MARK: - States

private struct StatsLoadingView: View {
    @State private var isDimmed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 30) {
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .fill(.white.opacity(0.06))
                .frame(height: 52)

            VStack(alignment: .leading, spacing: 10) {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(.white.opacity(0.09))
                    .frame(width: 150, height: 62)
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(.white.opacity(0.06))
                    .frame(width: 120, height: 14)
                HStack(spacing: 8) {
                    ForEach(0 ..< 4, id: \.self) { _ in
                        RoundedRectangle(cornerRadius: 10.5, style: .continuous)
                            .fill(.white.opacity(0.06))
                            .frame(height: 56)
                    }
                }
                .padding(.top, 8)
            }

            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(.white.opacity(0.045))
                .frame(height: 290)

            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(.white.opacity(0.045))
                .frame(height: 180)
        }
        .opacity(isDimmed ? 0.55 : 1)
        .animation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true), value: isDimmed)
        .onAppear { isDimmed = true }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading stats")
    }
}

private struct StatsEmptyView: View {
    let title: String
    let message: String

    var body: some View {
        ContentUnavailableView(title, systemImage: "chart.bar.xaxis", description: Text(message))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 300)
    }
}

private struct StatsErrorView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            ContentUnavailableView(
                "Could not load stats",
                systemImage: "exclamationmark.triangle",
                description: Text(message)
            )
            .foregroundStyle(.white)

            Button("Try Again", action: retry)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(.black)
                .padding(.horizontal, 22)
                .frame(minHeight: 44)
                .background(.white.opacity(0.92), in: Capsule())
                .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, minHeight: 360)
    }
}

// MARK: - Copy

private enum StatsCopy {
    static func days(_ value: Int) -> String {
        "\(value.formatted()) \(value == 1 ? "day" : "days")"
    }

    static func logs(_ value: Int) -> String {
        "\(value.formatted()) \(value == 1 ? "log" : "logs")"
    }

    static func share(_ value: Int, of total: Int) -> String {
        guard total > 0 else { return "0%" }
        let fraction = Double(value) / Double(total)
        if fraction > 0, fraction < 0.01 {
            return "<1%"
        }
        return fraction.formatted(.percent.precision(.fractionLength(0)))
    }

    /// Converts a stored rating onto the app's five-star display scale.
    static func stars(_ rawValue: String?, for media: MediaSummary) -> Double? {
        guard let rawValue, let value = Double(rawValue), value.isFinite, value > 0 else { return nil }
        let usesFiveStarScale = media.ref.usesFiveStarRatingScale && value <= 5
        return min(5, usesFiveStarScale ? value : value / 2)
    }
}
