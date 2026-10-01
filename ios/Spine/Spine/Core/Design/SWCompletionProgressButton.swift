import SwiftUI

struct SWCompletionProgressButton: View {
    let progress: CompletionProgress
    var isCompact = false

    @State private var showsCount = false

    var body: some View {
        Button {
            showsCount.toggle()
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 11, weight: .bold))
                Text(showsCount ? progress.countText : progress.percentageText)
                    .monospacedDigit()
                    .lineLimit(1)
                    .contentTransition(.numericText())
            }
            .fixedSize(horizontal: true, vertical: false)
            .font(.system(size: 11, weight: .heavy))
            .foregroundStyle(.green.opacity(0.9))
            .padding(.horizontal, isCompact ? 8 : 10)
            .frame(minWidth: 52, minHeight: isCompact ? 22 : 30)
            .background(.green.opacity(0.11), in: Capsule())
            .overlay {
                Capsule()
                    .stroke(.green.opacity(0.2), lineWidth: 0.75)
            }
        }
        .buttonStyle(.plain)
        .animation(.snappy(duration: 0.2), value: showsCount)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .accessibilityLabel("Completion")
        .accessibilityValue(showsCount ? progress.countText : progress.percentageText)
        .accessibilityHint(showsCount ? "Shows percentage" : "Shows completed count")
    }
}
