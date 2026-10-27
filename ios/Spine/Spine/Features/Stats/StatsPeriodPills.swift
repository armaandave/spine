import SwiftUI

/// The Stats year pills: All Time, then a pill per year. The look is the one the Stats page has
/// always had; the selected pill scrolls into view.
struct StatsPeriodPills: View {
    let periods: [StatsPeriod]
    let selection: StatsPeriod
    let isLoading: Bool
    /// The selected pill's tint: the tab's color, or neutral on Global.
    let accent: Color
    let onSelect: (StatsPeriod) -> Void

    /// The pill tint for a tab, the same as the page has always used.
    static func accent(for mediaType: String?) -> Color {
        mediaType.map { MediaTypeTheme.theme(for: $0).statsColor } ?? Color.white.opacity(0.88)
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                GlassEffectContainer(spacing: 8) {
                    HStack(spacing: 8) {
                        ForEach(periods) { period in
                            pill(period)
                                .id(period.id)
                        }
                    }
                }
                .padding(.vertical, 2)
            }
            .onAppear {
                proxy.scrollTo(selection.id, anchor: .center)
            }
            .onChange(of: selection) {
                withAnimation(.snappy(duration: 0.25)) {
                    proxy.scrollTo(selection.id, anchor: .center)
                }
            }
        }
    }

    private func pill(_ period: StatsPeriod) -> some View {
        let isSelected = selection == period

        return Button {
            onSelect(period)
        } label: {
            HStack(spacing: 6) {
                if isLoading, isSelected {
                    ProgressView()
                        .controlSize(.mini)
                        .tint(.white)
                }
                Text(period.title)
            }
            .font(.system(size: 13, weight: .bold))
            .foregroundStyle(.white.opacity(isSelected ? 0.96 : 0.52))
            .padding(.horizontal, 15)
            .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.plain)
        .glassEffect(
            isSelected
                ? .regular.tint(accent.opacity(0.14)).interactive()
                : .clear.interactive(),
            in: Capsule()
        )
        .accessibilityLabel("Show stats for \(period.title)")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}
