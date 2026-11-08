import SwiftUI

/// The two marks of a feed log card's headline: the heart for a loved log and the arrow for a rewatch. Each is the
/// system symbol it always was, in its own color; `FeedMarkImages` gives it a soft glow.
nonisolated enum FeedMark: Equatable, Hashable, Sendable {
    case heart
    case rewatch

    var symbolName: String {
        switch self {
        case .heart: "heart.fill"
        case .rewatch: "arrow.clockwise"
        }
    }

    var cacheName: String {
        switch self {
        case .heart: "heart"
        case .rewatch: "rewatch"
        }
    }

    /// The pink heart, and the rewatch green (deeper on light art, where the bright one does not read).
    func color(for ink: ArtInk) -> Color {
        switch self {
        case .heart: ink.heart
        case .rewatch: ink.rewatch
        }
    }
}

/// A heart that fills the width of its rect and sits in the middle of its height, so it can be drawn at the same size
/// as the comment bubble beside it. Its outline is the board's heart, which is 18 wide and 16 tall in a 24 box.
nonisolated struct HeartShape: Shape {
    func path(in rect: CGRect) -> Path {
        let k = rect.width / 18
        let originY = rect.midY - 16 * k / 2
        func at(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: rect.minX + CGFloat(x - 3) * k, y: originY + CGFloat(y - 4.4) * k)
        }
        var path = Path()
        path.move(to: at(12, 20.4))
        path.addCurve(to: at(10.75, 19.94), control1: at(11.55, 20.4), control2: at(11.1, 20.24))
        path.addCurve(to: at(3, 9.5), control1: at(6.2, 16.1), control2: at(3, 13.2))
        path.addCurve(to: at(7.9, 4.4), control1: at(3, 6.5), control2: at(5.2, 4.4))
        path.addCurve(to: at(12, 6.65), control1: at(9.55, 4.4), control2: at(11.1, 5.25))
        path.addCurve(to: at(16.1, 4.4), control1: at(12.9, 5.25), control2: at(14.45, 4.4))
        path.addCurve(to: at(21, 9.5), control1: at(18.8, 4.4), control2: at(21, 6.5))
        path.addCurve(to: at(13.25, 19.94), control1: at(21, 13.2), control2: at(17.8, 16.1))
        path.addCurve(to: at(12, 20.4), control1: at(12.9, 20.24), control2: at(12.45, 20.4))
        path.closeSubpath()
        return path
    }
}
