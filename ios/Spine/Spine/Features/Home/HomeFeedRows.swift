import SwiftUI

/// The feed's rows under the page top: one item per entry, a day header or a card. The body is a single `ForEach`,
/// which the lazy stack flattens, so each header and each card is its own lazy child with a stable id (`day-<date>`
/// or `card-<id>`) and a fixed height. This view reads `HomeFeedModel.entries` and nothing else, so a feed that
/// changes redraws these rows and never the page.
struct HomeFeedRows: View {
    let model: HomeFeedModel
    /// The card's item id: the page looks the current row up when the tap lands.
    let onOpen: (Int) -> Void

    /// A card and the gap under it: the lazy stack only ever moves known sizes.
    static let cardRowHeight = HomeDesign.Metric.cardHeight + HomeDesign.Metric.cardGap

    var body: some View {
        let entries = model.entries
        ForEach(Array(entries.enumerated()), id: \.element.id) { index, entry in
            row(entry, at: index, in: entries)
        }
    }

    @ViewBuilder
    private func row(_ entry: FeedListEntry, at index: Int, in entries: [FeedListEntry]) -> some View {
        switch entry {
        case let .day(_, title):
            FeedDayHeaderView(title: title)
                .equatable()
                .frame(height: FeedDayHeaderView.height)
                .prefetchingFeedCards(at: index, in: entries)
        case let .card(card):
            FeedCardView(model: card, onOpen: onOpen)
                .equatable()
                .padding(.horizontal, HomeDesign.Metric.pageMargin)
                .frame(height: Self.cardRowHeight, alignment: .top)
                .prefetchingFeedCards(at: index, in: entries)
                .onAppear { model.cardAppeared(card) }
                .task { await model.loadNextPageIfNeeded(cardID: card.id) }
        }
    }
}

/// What follows the last card: the next page's spinner, or its error with a retry. Its own view, so a page loading
/// redraws only this.
struct HomeFeedFooter: View {
    let feed: ActivityFeedViewModel

    var body: some View {
        if feed.isLoadingNextPage {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 60)
        } else if let error = feed.nextPageErrorMessage {
            VStack(spacing: 8) {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.52))
                    .multilineTextAlignment(.center)

                Button("Retry") {
                    Task { await feed.loadNextPage() }
                }
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white.opacity(0.86))
            }
            .frame(maxWidth: .infinity, minHeight: 60)
        }
    }
}
