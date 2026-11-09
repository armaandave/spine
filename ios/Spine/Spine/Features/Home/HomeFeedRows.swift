import SwiftUI

/// The feed's rows under the page top: one card per entry. The body is a single `ForEach`, which the lazy stack
/// flattens, so each card is its own lazy child with a stable id (the item's) and a fixed height. This view reads
/// `HomeFeedModel.entries` and nothing else, so a feed that changes redraws these rows and never the page.
struct HomeFeedRows: View {
    let model: HomeFeedModel
    /// The card's item id: the page looks the current row up when the tap lands. Rebuilt every time the page runs, and
    /// no different for it (`StableInput`).
    @StableInput var onOpen: (Int) -> Void

    /// A card and the gap under it: the lazy stack only ever moves known sizes.
    static let cardRowHeight = HomeDesign.Metric.cardHeight + HomeDesign.Metric.cardGap

    var body: some View {
        let entries = model.entries
        ForEach(Array(entries.enumerated()), id: \.element.id) { index, card in
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
