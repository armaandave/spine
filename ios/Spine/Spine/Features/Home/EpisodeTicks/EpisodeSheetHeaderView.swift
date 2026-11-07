import SwiftUI

/// The whole block an update sheet shows above its episode list (R3SheetTV, R3SheetLong): the big ticks (14
/// episodes or fewer) or the long-series window (more), then, for ticks, the summary line and the hint, and last the
/// white primary button. The host owns what a tap does: every callback only says what was asked.
///
/// The block pages its own window, so the host rebuilds `model` freely; paging starts over whenever the episode the
/// person is on changes. `height` receives the block's height, for sizing a sheet detent to it.
struct EpisodeSheetHeaderView: View {
    let model: EpisodeTickModel
    let accent: Color
    @Binding var height: CGFloat
    /// An episode's tick or window bar was tapped; ask `model.action(for:)` what that means.
    let onTap: (Int) -> Void
    /// The white button: tick the episode that is up next.
    let onPrimary: () -> Void
    /// An episode number was typed in the window: a request to mark everything up to it.
    let onType: (Int) -> Void
    /// The window was paged by -1 or +1. The block has already moved its own window; this is only a notice.
    let onPage: (Int) -> Void

    @State private var pagingOffset = 0
    @State private var primaryTaps = 0

    init(
        model: EpisodeTickModel,
        accent: Color,
        height: Binding<CGFloat>,
        onTap: @escaping (Int) -> Void,
        onPrimary: @escaping () -> Void,
        onType: @escaping (Int) -> Void = { _ in },
        onPage: @escaping (Int) -> Void = { _ in }
    ) {
        self.model = model
        self.accent = accent
        _height = height
        self.onTap = onTap
        self.onPrimary = onPrimary
        self.onType = onType
        self.onPage = onPage
    }

    var body: some View {
        let shown = model.withPagingOffset(pagingOffset)
        VStack(alignment: .leading, spacing: 0) {
            if shown.usesWindow {
                EpisodeWindowView(
                    model: shown,
                    accent: accent,
                    typed: shown.upTo > 0 ? String(shown.upTo) : "",
                    onType: onType,
                    onPage: { direction in
                        pagingOffset = shown.paged(direction).pagingOffset
                        onPage(direction)
                    },
                    onTap: onTap
                )
            } else {
                EpisodeTickStripView(model: shown, accent: accent, onTap: onTap)
                summary(shown)
                    .padding(.top, EpisodeTickStyle.summaryTop)
                Text(EpisodeTickModel.hint)
                    .font(.system(size: 13.5))
                    .foregroundStyle(EpisodeTickStyle.secondary)
                    .padding(.top, EpisodeTickStyle.hintTop)
            }
            primaryButton(shown)
                .padding(.top, EpisodeTickStyle.primaryTop)
        }
        .padding(.horizontal, HomeDesign.Metric.pageMargin)
        .padding(.top, EpisodeTickStyle.headerTop)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height = $0 }
        .onChange(of: model.upTo) { pagingOffset = 0 }
    }

    private func summary(_ shown: EpisodeTickModel) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(shown.summaryLine)
                .font(.system(size: 16, weight: .bold))
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 0)
            Text(shown.todayLine)
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(accent)
                .lineLimit(1)
                .fixedSize()
        }
    }

    private func primaryButton(_ shown: EpisodeTickModel) -> some View {
        Button {
            primaryTaps += 1
            onPrimary()
        } label: {
            Text(shown.primaryLabel)
                .font(.system(size: 16, weight: .heavy))
                .foregroundStyle(Color.black)
                .frame(maxWidth: .infinity)
                .frame(height: EpisodeTickStyle.primaryHeight)
                .background(Capsule().fill(Color.white))
                .opacity(shown.primaryEnabled ? 1 : 0.55)
                .contentShape(Capsule())
        }
        .buttonStyle(EpisodePrimaryButtonStyle())
        .disabled(!shown.primaryEnabled)
        .accessibilityLabel(shown.primaryAccessibilityLabel)
        .sensoryFeedback(.impact(weight: .medium), trigger: primaryTaps)
    }
}

/// A small press response for the white button; the fill itself is the button's label.
private struct EpisodePrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
