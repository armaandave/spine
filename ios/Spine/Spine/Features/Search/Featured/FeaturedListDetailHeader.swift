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
