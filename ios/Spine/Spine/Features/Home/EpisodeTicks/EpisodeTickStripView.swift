import SwiftUI

/// The update sheet's row of big ticks for a season of 14 episodes or fewer (R3SheetTV): 72 pt bars, today's in the
/// poster's pastel, earlier ones white, the rest faint, each with its number under it. A tap says which episode;
/// the host decides what it means through `EpisodeTickModel.action(for:)`.
struct EpisodeTickStripView: View {
    let model: EpisodeTickModel
    let accent: Color
    let onTap: (Int) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var taps = 0

    var body: some View {
        let ticks = model.stripTicks
        let animation = reduceMotion ? HomeDesign.Motion.reduced : Animation.spring(duration: 0.3, bounce: 0.35)
        HStack(alignment: .top, spacing: EpisodeTickStyle.stripGap) {
            ForEach(ticks) { tick in
                Button {
                    // Unaired and unlisted episodes do nothing: no haptic, no call.
                    guard tick.action != .none else { return }
                    taps += 1
                    onTap(tick.number)
                } label: {
                    EpisodeBigTick(tick: tick, accent: accent, animation: animation)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tick.accessibilityLabel)
                .accessibilityValue(tick.accessibilityValue)
            }
        }
        .sensoryFeedback(.selection, trigger: taps)
    }
}

/// One bar and its number. The hit area is the whole column, taller than 44 pt.
private struct EpisodeBigTick: View {
    let tick: EpisodeTickModel.Tick
    let accent: Color
    let animation: Animation

    var body: some View {
        VStack(spacing: EpisodeTickStyle.stripLabelGap) {
            RoundedRectangle(cornerRadius: EpisodeTickStyle.stripTickRadius, style: .continuous)
                .fill(fill)
                .frame(height: EpisodeTickStyle.stripTickHeight)
                .scaleEffect(y: tick.state == .today ? 1 : EpisodeTickStyle.stripRestScaleY)
            Text(verbatim: String(tick.number))
                .font(.system(size: 12, weight: .bold))
                .monospacedDigit()
                .foregroundStyle(numberColor)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity)
        .frame(minHeight: EpisodeTickStyle.minHit)
        .contentShape(Rectangle())
        .animation(animation, value: tick.state)
    }

    private var fill: Color {
        switch tick.state {
        case .today: accent
        case .before: EpisodeTickStyle.before
        case .rest: EpisodeTickStyle.rest
        }
    }

    private var numberColor: Color {
        switch tick.state {
        case .today: accent
        case .before: EpisodeTickStyle.labelWatched
        case .rest:
            tick.action == .none ? EpisodeTickStyle.labelUnavailable : EpisodeTickStyle.labelRest
        }
    }
}
