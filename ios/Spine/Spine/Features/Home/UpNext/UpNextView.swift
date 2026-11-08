import SwiftUI

/// The Up next page, opened from Home's tile: months in the Diary's look, soonest first, each row the day it airs,
/// the poster, the title and what airs, with how far away it is. A row opens the title.
struct UpNextView: View {
    let model: HomeUpNextModel
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let listRepository: ListRepository
    let peopleRepository: PeopleRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    /// Worked out when the page appears and again when the day changes under it.
    @State private var today = Date()

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(alignment: .leading, spacing: 0, pinnedViews: [.sectionHeaders]) {
                content
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 28)
        }
        .diaryPinnedHeaders()
        .refreshable {
            await model.load()
        }
        .background {
            LogBackdrop(url: URL(string: model.items.first?.media.displayPosterURL ?? ""))
        }
        .overlay(alignment: .top) {
            DiaryTopSoftScrim()
        }
        // A dark page whatever the phone's appearance, so the refresh spinner draws for a dark background.
        .environment(\.colorScheme, .dark)
        // The title is the page's own, as the Diary's is: the bar keeps only the back button.
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            // Home's copy is shown at once; this brings it up to date.
            await model.load()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.significantTimeChangeNotification)) { _ in
            today = Date()
        }
    }

    @ViewBuilder
    private var content: some View {
        let items = model.items
        VStack(alignment: .leading, spacing: 4) {
            Text("Up next")
                .font(.system(size: 32, weight: .black))
                .foregroundStyle(.white)
                .accessibilityAddTraits(.isHeader)
            if !items.isEmpty {
                Text(UpNextPresentation.countLine(items.count))
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.84))
            }
        }
        .padding(.bottom, 14)
        if items.isEmpty {
            if model.isLoading || !model.hasLoaded, model.errorMessage == nil {
                ProgressView()
                    .tint(.white)
                    .frame(maxWidth: .infinity, minHeight: 320)
            } else if let error = model.errorMessage, !model.hasLoaded {
                DiaryStateCard(title: "Couldn't load Up next", systemImage: "exclamationmark.triangle", message: error)
                    .padding(.top, 40)
            } else {
                DiaryStateCard(
                    title: "Nothing coming up",
                    systemImage: "calendar",
                    message: "New episodes of shows and anime you're watching show up here once they have a date."
                )
                .padding(.top, 40)
            }
        } else {
            let refs = items.map(\.media.ref)
            ForEach(UpNextPresentation.monthSections(items, today: today)) { section in
                Section {
                    // Each row is its own item in the lazy stack, a slice of the month's card, as on the Diary.
                    ForEach(section.items) { item in
                        let isFirst = item.id == section.items.first?.id
                        let isLast = item.id == section.items.last?.id
                        NavigationLink {
                            MediaDetailView(
                                ref: item.media.ref,
                                browsingContext: MediaBrowsingContext(refs: refs, selected: item.media.ref),
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
                        } label: {
                            UpNextRow(item: item, today: today, showsSeparator: !isLast)
                        }
                        .buttonStyle(DiaryRowButtonStyle())
                        .diaryCard(roundsTop: isFirst, roundsBottom: isLast)
                        .padding(.bottom, isLast ? 18 : 0)
                    }
                } header: {
                    DiaryMonthHeader(title: section.title)
                        .id(section.id)
                }
            }
        }
    }
}

/// One title on the Up next page, in the Diary row's layout: the day it airs, the poster, the title and what airs,
/// then "Today" or how far away it is.
struct UpNextRow: View {
    let item: UpNextItem
    let today: Date
    var showsSeparator = false

    var body: some View {
        let airDate = item.airing.airDate
        let days = UpNextPresentation.daysAway(airDate, today: today) ?? 0
        let badge = UpNextPresentation.badge(for: airDate)
        HStack(alignment: .center, spacing: 12) {
            VStack(spacing: 2) {
                Text(badge.weekday)
                    .font(.system(size: 11, weight: .bold))
                    .tracking(0.6)
                    .foregroundStyle(.white.opacity(days <= 0 ? 1 : 0.55))
                Text(badge.day)
                    .font(.system(size: 20, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.9))
            }
            .lineLimit(1)
            .frame(width: UpNextRowSpec.badgeWidth)

            MediaArtwork(
                url: item.media.displayPosterURL,
                title: item.media.displayTitle,
                slot: .diaryEntry,
                mediaType: item.media.ref.mediaType,
                orientation: item.media.posterOrientation
            )
            .frame(width: 42, height: 63)
            .shadow(color: .black.opacity(0.2), radius: 6, y: 3)

            VStack(alignment: .leading, spacing: 5) {
                Text(item.media.displayTitle)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                Text(UpNextPresentation.line(for: item))
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white.opacity(0.62))
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if days <= 0 {
                Text("Today")
                    .font(.system(size: 12.5, weight: .bold))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 10)
                    .frame(height: 26)
                    .background(Capsule().fill(.white))
            } else {
                Text(UpNextPresentation.relative(days: days))
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.6))
                    .lineLimit(1)
                    .fixedSize()
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsSeparator {
                // Starts under the poster, like an inset list separator.
                DiarySeparator()
                    .padding(.leading, 14 + UpNextRowSpec.badgeWidth + 12)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "\(item.media.displayTitle), \(UpNextPresentation.line(for: item)), "
                + UpNextPresentation.spokenWhen(airDate, today: today)
        )
    }
}

private enum UpNextRowSpec {
    static let badgeWidth: CGFloat = 34
}
