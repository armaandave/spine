import SwiftUI

/// How much of a set the viewer has finished: one continuous bar, never segments (a person can have hundreds of
/// credits), then the plain numbers. The done part takes the page's art color when it has one, else white. It replaced
/// the green completion pill on person, studio, series and list pages and over a show's episodes.
struct CompletionBar: View {
    let done: Int
    let total: Int
    /// The page's art color; nil draws white.
    var tint: Color?
    /// 8 on plates and headers; 4 in a person page's section headers.
    var height: CGFloat = 8
    var countFont: Font = .system(size: 13, weight: .semibold)
    var countOpacity: Double = 0.62

    init(
        done: Int,
        total: Int,
        tint: Color? = nil,
        height: CGFloat = 8,
        countFont: Font = .system(size: 13, weight: .semibold),
        countOpacity: Double = 0.62
    ) {
        self.done = done
        self.total = total
        self.tint = tint
        self.height = height
        self.countFont = countFont
        self.countOpacity = countOpacity
    }

    init(progress: CompletionProgress, tint: Color? = nil, height: CGFloat = 8) {
        self.init(done: progress.normalizedCompletedCount, total: max(progress.totalCount, 0), tint: tint, height: height)
    }

    var body: some View {
        HStack(spacing: 10) {
            CompletionBarTrack(done: done, total: total, tint: tint, height: height)
                // The bar takes the row's free width, never less than this.
                .frame(minWidth: 56, maxWidth: .infinity)
            Text("\(done) of \(total)")
                .font(countFont)
                .monospacedDigit()
                .foregroundStyle(.white.opacity(countOpacity))
                .lineLimit(1)
                .fixedSize()
                .contentTransition(.numericText())
        }
        .animation(.smooth(duration: 0.5), value: done)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Completion")
        .accessibilityValue("\(done) of \(total)")
    }
}

/// The bar alone: a white 16% track and the done share in the art's color at 80%. Anything finished shows at least a
/// round sliver, so 4 of 429 still reads as started.
struct CompletionBarTrack: View {
    let done: Int
    let total: Int
    var tint: Color?
    var height: CGFloat = 8

    var body: some View {
        Capsule()
            .fill(.white.opacity(0.16))
            .overlay(alignment: .leading) {
                GeometryReader { proxy in
                    Capsule()
                        .fill((tint ?? .white).opacity(0.8))
                        .frame(width: Self.fillWidth(done: done, total: total, width: proxy.size.width, height: height))
                }
            }
            .clipShape(Capsule())
            .frame(height: height)
    }

    static func fillWidth(done: Int, total: Int, width: CGFloat, height: CGFloat) -> CGFloat {
        guard total > 0, done > 0, width > 0 else { return 0 }
        let share = CGFloat(min(done, total)) / CGFloat(total)
        return min(width, max(height, width * share))
    }
}
