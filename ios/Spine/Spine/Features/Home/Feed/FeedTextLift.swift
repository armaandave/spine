import SwiftUI

/// A text's lift off the glass. On dark art a crisp copy of the text one point down in black at 0.32 (a one-point
/// blur would look the same and cost an offscreen pass per text); on light art a faint white halo, the one live shadow
/// in the feed, on the few texts of the minority of cards that are light.
struct FeedTextLift: ViewModifier {
    let ink: ArtInk

    @ViewBuilder
    func body(content: Content) -> some View {
        switch ink {
        case .dark:
            content.background(alignment: .topLeading) {
                content
                    .foregroundStyle(Color.black.opacity(HomeDesign.FeedDepth.darkLiftOpacity))
                    .offset(y: 1)
                    .accessibilityHidden(true)
            }
        case .light:
            content.shadow(color: .white.opacity(HomeDesign.FeedDepth.lightHaloOpacity), radius: 3)
        }
    }
}

extension View {
    /// Apply right after `.font` and `.lineLimit` and before `.foregroundStyle`: the dark copy takes its black from
    /// its own style, and the text's style then applies to the text alone.
    func feedTextLift(_ ink: ArtInk) -> some View {
        modifier(FeedTextLift(ink: ink))
    }
}
