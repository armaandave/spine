import SwiftUI

/// The Stats page's frame: the year pills and the media picker as the first rows (both stay
/// through loading and errors), then the sections, over a faint wash of the first title's
/// poster.
///
/// `content` returns the section rows for a page. They go straight into the page's
/// `LazyVStack(spacing: 0)`, so return one view per row (a `ForEach` or several views), never
/// one stack holding many rows: each row must be its own lazy item. Rows are already padded
/// 16 pt from the edges. While another scope loads they're dimmed and don't take taps.
///
/// Apply `navigationDestination`s and the media detail cover to the shell. Call
/// `viewModel.appeared()` when a full-screen cover over the page is dismissed, because
/// `onAppear` doesn't run then and a change made in the cover should reload the page.
struct StatsShell<Content: View>: View {
    let viewModel: StatsViewModel
    let content: (StatsPage) -> Content

    init(viewModel: StatsViewModel, @ViewBuilder content: @escaping (StatsPage) -> Content) {
        self.viewModel = viewModel
        self.content = content
    }

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(alignment: .leading, spacing: 0) {
                StatsPeriodPills(
                    periods: viewModel.periods,
                    selection: viewModel.selectedPeriod,
                    isLoading: viewModel.isLoading,
                    accent: StatsPeriodPills.accent(for: viewModel.selectedMediaType)
                ) { period in
                    viewModel.selectPeriod(period)
                }
                .padding(.bottom, 26)

                mediaPicker
                    .padding(.bottom, 30)

                stateRows
            }
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 48)
        }
        .refreshable {
            await viewModel.refresh()
        }
        // A dark page whatever the phone's appearance, as the other dark screens are, so
        // segmented controls and the refresh spinner draw for a dark background.
        .preferredColorScheme(.dark)
        .background {
            LogBackdrop(url: URL(string: viewModel.page?.featuredMedia?.displayPosterURL ?? ""))
        }
        .overlay(alignment: .top) {
            DiaryTopSoftScrim()
        }
        .navigationTitle("Stats")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        // Only the user's own switches: reloads stay silent.
        .sensoryFeedback(.selection, trigger: viewModel.userSwitchCount)
        .onAppear {
            viewModel.appeared()
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { _ in
            viewModel.markNeedsRefresh()
        }
        .onReceive(NotificationCenter.default.publisher(for: .customListsDidChange)) { _ in
            viewModel.markNeedsRefresh()
        }
        .onReceive(NotificationCenter.default.publisher(for: .diaryEntriesDidChange)) { _ in
            viewModel.markNeedsRefresh()
        }
    }

    @ViewBuilder
    private var stateRows: some View {
        if let message = viewModel.bannerMessage {
            StatsErrorBanner(
                title: viewModel.errorScope.map { "Couldn't load \($0.period.title)" } ?? "Couldn't load stats",
                message: message
            ) {
                viewModel.retry()
            }
            .padding(.bottom, 18)
        }

        if let page = viewModel.page {
            // The page on screen answers `pageScope`, which differs from the selection after a
            // failed load.
            let pagePeriod = viewModel.pageScope?.period ?? viewModel.selectedPeriod
            Group {
                if page.isEmpty {
                    DiaryStateCard(
                        title: "Nothing here yet",
                        systemImage: "chart.bar",
                        message: pagePeriod == .allTime
                            ? "Log or track something to start seeing your stats."
                            : "Nothing logged in \(pagePeriod.title). Try another year."
                    )
                } else {
                    content(page)
                }
            }
            // Only the dimming animates: a new page lands in the same update that ends it, and
            // its rows shouldn't animate in.
            .animation(.easeInOut(duration: SpineMotion.standardDuration)) { rows in
                rows.opacity(viewModel.isDimmed ? 0.45 : 1)
            }
            .allowsHitTesting(!viewModel.isDimmed)
        } else if let message = viewModel.fullErrorMessage {
            VStack(spacing: 16) {
                DiaryStateCard(
                    title: "Couldn't load stats",
                    systemImage: "exclamationmark.triangle",
                    message: message
                )
                Button("Try Again") {
                    viewModel.retry()
                }
                .buttonStyle(.bordered)
                .tint(.white)
            }
        } else {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 320)
        }
    }

    private var mediaPicker: some View {
        MediaSearchLensPicker(
            selectedType: Binding(
                get: { viewModel.selectedMediaType ?? "" },
                set: { viewModel.selectMediaType($0) }
            ),
            availableTypes: viewModel.mediaTypes,
            horizontalPadding: 0,
            fitsAllTypes: true,
            allowsEmptySelection: true,
            isCompact: true
        ) { selectedType in
            viewModel.selectMediaType(selectedType)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Media type")
        .accessibilityValue(
            viewModel.selectedMediaType.map { MediaTypeTheme.theme(for: $0).displayName } ?? "All Media"
        )
        .accessibilityAction(named: "Show All Media") {
            viewModel.selectMediaType(nil)
        }
    }
}

/// A load failed but the page is still on screen: say so above it, with Try Again.
private struct StatsErrorBanner: View {
    let title: String
    let message: String
    let onRetry: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 18))
                .foregroundStyle(.white.opacity(0.6))
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.55))
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Button("Try Again", action: onRetry)
                .buttonStyle(.bordered)
                .controlSize(.small)
                .tint(.white)
        }
        .padding(14)
        .diaryCard()
    }
}
