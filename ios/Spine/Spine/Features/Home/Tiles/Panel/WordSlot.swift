import SwiftUI

/// A word in the title row's right-hand block, in a slot exactly as wide as the word. When the word changes, the old
/// one blurs out and the new one blurs in, and the slot's width animates, so the neighbors slide instead of
/// jumping. The same word across two titles (" of ") does not swap, so it does not flicker.
struct WordSlot: View {
    let text: String

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack(alignment: .leading) {
            if !text.isEmpty {
                Text(text)
                    .id(text)
                    .transition(PanelMotion.wordSwap(reduceMotion: reduceMotion))
            }
        }
        .font(InProgressPanelMetrics.whereFont)
        .foregroundStyle(TileStyle.panelSecondary)
        .lineLimit(1)
        .fixedSize()
        .frame(
            width: PanelTextMetrics.width(text, font: InProgressPanelMetrics.whereUIFont),
            height: InProgressPanelMetrics.titleRowHeight,
            alignment: .leading
        )
        .animation(PanelMotion.slot(reduceMotion: reduceMotion), value: text)
        .accessibilityHidden(true)
    }
}
