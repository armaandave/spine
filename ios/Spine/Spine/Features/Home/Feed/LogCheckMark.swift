import SwiftUI

/// A log's check (the Check cap): it ends a log's bar on a feed card and on the log's page, or the log's marks when the
/// log has no bar. Filled in the bar's lit color with a dark tick. No glow: a live shadow in a feed row costs an
/// offscreen pass per card.
struct LogCheckMark: View {
    let color: Color
    var size: CGFloat = HomeDesign.Metric.checkSize

    var body: some View {
        Image(systemName: "checkmark.circle.fill")
            .resizable()
            .symbolRenderingMode(.palette)
            .foregroundStyle(Color.black.opacity(0.82), color)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
