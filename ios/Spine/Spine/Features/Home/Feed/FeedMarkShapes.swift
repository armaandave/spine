import SwiftUI

/// The marks of a feed log card's headline: the heart for a loved log, the arrow for a rewatch, and the whole and half
/// star a rating is drawn with. Each is a system symbol in its own color; `FeedMarkImages` gives it a soft glow.
nonisolated enum FeedMark: Equatable, Hashable, Sendable {
    case heart
    case rewatch
    case star
    case halfStar

    /// The mark for one of a rating's star symbols ("star.fill", "star.leadinghalf.filled"); nil for any other name.
    init?(starSymbol: String) {
        switch starSymbol {
        case "star.fill": self = .star
        case "star.leadinghalf.filled": self = .halfStar
        default: return nil
        }
    }

    var symbolName: String {
        switch self {
        case .heart: "heart.fill"
        case .rewatch: "arrow.clockwise"
        case .star: "star.fill"
        case .halfStar: "star.leadinghalf.filled"
        }
    }

    var cacheName: String {
        switch self {
        case .heart: "heart"
        case .rewatch: "rewatch"
        case .star: "star"
        case .halfStar: "halfStar"
        }
    }

    /// The pink heart, the rewatch green (deeper on light art, where the bright one does not read), and the stars' gold
    /// on dark art (the ink itself on light art, as the stars always were).
    func color(for ink: ArtInk) -> Color {
        switch self {
        case .heart: ink.heart
        case .rewatch: ink.rewatch
        case .star, .halfStar: ink.star
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
