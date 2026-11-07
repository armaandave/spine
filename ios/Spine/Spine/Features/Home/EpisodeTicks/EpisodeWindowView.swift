import SwiftUI

/// The update sheet's view of a long series (R3SheetLong): a card to type the episode you are on, a paging row, a
/// window of 20 ticks, and a line for the whole series with the window and the current episode on it.
///
/// It never writes. A tap says which episode (`onTap`); a typed number is a request, sent once when the person
/// finishes typing (`onType`), and the host asks its existing "mark everything up to it" question before anything
/// is saved. `typed` is the number the field shows at rest.
struct EpisodeWindowView: View {
    let model: EpisodeTickModel
    let accent: Color
    let typed: String
    let onType: (Int) -> Void
    let onPage: (Int) -> Void
    let onTap: (Int) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var draft: String
    @State private var taps = 0
    @State private var pages = 0
    @FocusState private var typing: Bool

    init(
        model: EpisodeTickModel,
        accent: Color,
        typed: String,
        onType: @escaping (Int) -> Void,
        onPage: @escaping (Int) -> Void,
        onTap: @escaping (Int) -> Void
    ) {
        self.model = model
        self.accent = accent
        self.typed = typed
        self.onType = onType
        self.onPage = onPage
        self.onTap = onTap
        _draft = State(initialValue: typed)
    }

    var body: some View {
        VStack(spacing: 0) {
            numberCard
            pager
                .padding(.top, EpisodeTickStyle.pagerTop)
            tickRow
                .padding(.top, EpisodeTickStyle.tickRowTop)
            seriesLine
                .padding(.top, EpisodeTickStyle.lineTop)
        }
        .onChange(of: typed) { _, new in
            if !typing { draft = new }
        }
        .onChange(of: draft) { old, new in
            let clean = model.sanitizedTyped(new, previous: old)
            if clean != new { draft = clean }
        }
        .onChange(of: typing) { _, isTyping in
            if !isTyping { commit() }
        }
        .sensoryFeedback(.selection, trigger: taps)
        .sensoryFeedback(.selection, trigger: pages)
    }

    // MARK: Number card

    private var numberCard: some View {
        let request = model.typedEpisode(draft)
        return VStack(alignment: .leading, spacing: 0) {
            Text("Episode")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Color.white.opacity(0.78))
                .accessibilityHidden(true)
            HStack(alignment: .lastTextBaseline, spacing: 8) {
                TextField("", text: $draft)
                    .keyboardType(.numberPad)
                    .focused($typing)
                    .font(.system(size: 44, weight: .heavy))
                    .monospacedDigit()
                    .tracking(-1.5)
                    .tint(accent)
                    .frame(width: EpisodeTickStyle.typedWidth, alignment: .leading)
                    .accessibilityLabel("Episode")
                Text(verbatim: "of \(EpisodeTickModel.grouped(model.total))")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(EpisodeTickStyle.secondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Text(model.windowTodayLine(typed: request))
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(accent)
                    .lineLimit(1)
            }
            .padding(.top, 2)
            Text(EpisodeTickModel.windowHint)
                .font(.system(size: 13))
                .foregroundStyle(EpisodeTickStyle.secondary)
                .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, EpisodeTickStyle.cardPaddingH)
        .padding(.vertical, EpisodeTickStyle.cardPaddingV)
        .background(
            RoundedRectangle(cornerRadius: EpisodeTickStyle.cardRadius, style: .continuous)
                .fill(EpisodeTickStyle.card)
        )
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { typing = false }
            }
        }
    }

    /// Sends the typed number once, when the field is done, then shows the real number again. The host's answer
    /// (a confirmed write) changes `typed`; a declined one leaves the real number showing.
    private func commit() {
        let request = model.typedEpisode(draft)
        draft = typed
        if let request, request != model.upTo { onType(request) }
    }

    // MARK: Paging row

    private var pager: some View {
        HStack {
            pagerButton(
                systemImage: "chevron.left",
                label: "Earlier episodes",
                enabled: model.windowFirst > 1,
                direction: -1
            )
            Spacer(minLength: 0)
            Text(model.windowLabel)
                .font(.system(size: 14, weight: .bold))
                .monospacedDigit()
                .lineLimit(1)
            Spacer(minLength: 0)
            pagerButton(
                systemImage: "chevron.right",
                label: "Later episodes",
                enabled: model.windowLast < model.total,
                direction: 1
            )
        }
    }

    private func pagerButton(systemImage: String, label: String, enabled: Bool, direction: Int) -> some View {
        Button {
            pages += 1
            onPage(direction)
        } label: {
            Image(systemName: systemImage)
                .font(.system(size: 16, weight: .bold))
                .frame(width: EpisodeTickStyle.minHit, height: EpisodeTickStyle.minHit)
                .background(Circle().fill(EpisodeTickStyle.control))
                .opacity(enabled ? 1 : 0.35)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }

    // MARK: Ticks

    private var tickRow: some View {
        let animation = reduceMotion ? HomeDesign.Motion.reduced : Animation.spring(duration: 0.3, bounce: 0.35)
        return HStack(alignment: .bottom, spacing: EpisodeTickStyle.windowTickGap) {
            ForEach(model.windowTicks) { tick in
                Button {
                    guard tick.action != .none else { return }
                    // A tap settles whatever was being typed: the tap wins, nothing is asked twice.
                    draft = typed
                    typing = false
                    taps += 1
                    onTap(tick.number)
                } label: {
                    RoundedRectangle(cornerRadius: EpisodeTickStyle.windowTickRadius, style: .continuous)
                        .fill(fill(for: tick.state))
                        .frame(height: EpisodeTickModel.windowTickHeight(tick.state))
                        .frame(maxWidth: .infinity)
                        .frame(height: EpisodeTickStyle.windowTickRowHeight, alignment: .bottom)
                        .contentShape(Rectangle())
                        .animation(animation, value: tick.state)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tick.accessibilityLabel)
                .accessibilityValue(tick.accessibilityValue)
            }
        }
        .frame(height: EpisodeTickStyle.windowTickRowHeight)
    }

    private func fill(for state: MeterTickState) -> Color {
        switch state {
        case .today: accent
        case .before: EpisodeTickStyle.before
        case .rest: EpisodeTickStyle.rest
        }
    }

    // MARK: Whole-series line

    private var seriesLine: some View {
        let animation = reduceMotion ? HomeDesign.Motion.reduced : Animation.easeInOut(duration: 0.4)
        return VStack(spacing: 8) {
            EpisodeSeriesLineLayout(
                fill: model.seriesFill,
                boxStart: model.seriesWindowStart,
                boxWidth: model.seriesWindowWidth
            ) {
                Capsule().fill(EpisodeTickStyle.lineTrack)
                Capsule().fill(EpisodeTickStyle.lineFill)
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(EpisodeTickStyle.lineBox)
                    .overlay(
                        RoundedRectangle(cornerRadius: 3, style: .continuous)
                            .strokeBorder(EpisodeTickStyle.lineBoxStroke, lineWidth: 1)
                    )
                RoundedRectangle(cornerRadius: 2, style: .continuous).fill(accent)
            }
            .animation(animation, value: model.upTo)
            .animation(animation, value: model.windowFirst)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(model.seriesAccessibilityLabel)
            .accessibilityAddTraits(.isImage)

            HStack {
                Text(verbatim: "E1")
                Spacer(minLength: 0)
                Text("The whole series")
                Spacer(minLength: 0)
                Text(verbatim: "E\(EpisodeTickModel.grouped(model.total))")
            }
            .font(.system(size: 12, weight: .semibold))
            .monospacedDigit()
            .foregroundStyle(EpisodeTickStyle.lineCaption)
            .accessibilityHidden(true)
        }
    }
}

/// Places the four parts of the whole-series line (track, filled part, window box, current-episode marker) by
/// fractions of the width. The fractions animate, so the box and the marker glide when the episode or the window
/// changes.
private struct EpisodeSeriesLineLayout: Layout {
    var fill: Double
    var boxStart: Double
    var boxWidth: Double

    var animatableData: AnimatablePair<Double, AnimatablePair<Double, Double>> {
        get { AnimatablePair(fill, AnimatablePair(boxStart, boxWidth)) }
        set {
            fill = newValue.first
            boxStart = newValue.second.first
            boxWidth = newValue.second.second
        }
    }

    private var height: CGFloat { EpisodeTickStyle.lineMarkSize.height }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        CGSize(width: proposal.width ?? 0, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard subviews.count == 4, bounds.width > 0 else { return }
        let width = bounds.width
        let trackHeight = EpisodeTickStyle.lineHeight
        let trackY = bounds.minY + (height - trackHeight) / 2
        let at = min(max(fill, 0), 1)

        subviews[0].place(
            at: CGPoint(x: bounds.minX, y: trackY), anchor: .topLeading,
            proposal: ProposedViewSize(width: width, height: trackHeight)
        )
        subviews[1].place(
            at: CGPoint(x: bounds.minX, y: trackY), anchor: .topLeading,
            proposal: ProposedViewSize(width: max(width * at, 0), height: trackHeight)
        )

        let boxHeight = EpisodeTickStyle.lineBoxHeight
        let boxX = min(max(width * boxStart, 0), width)
        let boxW = max(min(width * boxWidth, width - boxX), 0)
        subviews[2].place(
            at: CGPoint(x: bounds.minX + boxX, y: bounds.minY + (height - boxHeight) / 2), anchor: .topLeading,
            proposal: ProposedViewSize(width: boxW, height: boxHeight)
        )

        let mark = EpisodeTickStyle.lineMarkSize
        let markX = min(max(width * at - mark.width / 2, 0), width - mark.width)
        subviews[3].place(
            at: CGPoint(x: bounds.minX + markX, y: bounds.minY), anchor: .topLeading,
            proposal: ProposedViewSize(width: mark.width, height: mark.height)
        )
    }
}
