import SwiftUI

struct ProgressDeltaChipView: View {
    let delta: ProgressChangeDisplay

    var body: some View {
        HStack(spacing: 3) {
            Text(delta.previous)
            Image(systemName: "arrow.right")
                .font(.system(size: 9, weight: .black))
            Text(delta.current)
        }
        .font(.system(size: 10, weight: .bold))
        .foregroundStyle(.white.opacity(0.78))
        .padding(.horizontal, 10)
        .frame(height: 21)
        .background(.white.opacity(0.11), in: Capsule())
        .accessibilityLabel("\(delta.previous) to \(delta.current)")
    }
}
