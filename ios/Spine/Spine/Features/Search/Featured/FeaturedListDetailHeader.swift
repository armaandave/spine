import SwiftUI

/// A featured list's banner on its detail page while it's live: "● LIVE · 7 of 23 announced".
struct FeaturedListLiveBanner: View {
    let card: FeaturedListCard

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            if let live = card.live, live.isLive(at: context.date) {
                HStack(spacing: 7) {
                    FeaturedLiveDot(size: 8)
                    Text("LIVE")
                        .font(.system(size: 12, weight: .black))
                        .tracking(0.6)
                    if !live.label.isEmpty {
                        Text(live.label)
                            .font(.system(size: 12, weight: .semibold))
                            .lineLimit(1)
                    }
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 10)
                .frame(height: 26)
                .background(FeaturedLiveDot.color.opacity(0.22), in: Capsule())
                .overlay {
                    Capsule().stroke(FeaturedLiveDot.color.opacity(0.5), lineWidth: 1)
                }
                .accessibilityElement(children: .combine)
            }
        }
    }
}

/// A featured list's metadata on its detail page ("CHART · Netflix · Updated 2h ago · Refreshes weekly") and a
/// link to its source.
struct FeaturedListDetailMeta: View {
    let card: FeaturedListCard

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            TimelineView(.periodic(from: .now, by: 60)) { context in
                let line = card.metadataLine(now: context.date)
                if !line.isEmpty {
                    Text(line)
                        .font(.system(size: 12, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.66))
                        .lineLimit(2)
                }
            }

            if let url = card.source?.linkURL {
                Link(destination: url) {
                    HStack(spacing: 5) {
                        Text(sourceLinkTitle)
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 10, weight: .bold))
                    }
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white.opacity(0.86))
                    .padding(.horizontal, 10)
                    .frame(height: 26)
                    .background(.white.opacity(0.13), in: Capsule())
                }
                .accessibilityHint("Opens the source in your browser")
            }
        }
        .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
    }

    private var sourceLinkTitle: String {
        guard let name = card.source?.name, !name.isEmpty else { return "View source" }
        return "View on \(name)"
    }
}
