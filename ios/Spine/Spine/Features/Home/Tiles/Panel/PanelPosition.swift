import Foundation

/// Turns the carousel's fractional offset into what the panel uses: the two titles it is between and how far, and
/// the one it reads as (its words, its buttons).
nonisolated enum PanelPosition {
    /// A finger resting near the midpoint does not flip the words back and forth.
    static let hysteresis = 0.06
    /// The buttons work only this close to a poster.
    static let settledWithin = 0.12

    struct Split: Equatable {
        var lo: Int
        var hi: Int
        var t: Double
    }

    /// `reported` is `state.scrollPosition`, `focusIndex` the focused poster. A reported value more than one poster
    /// away from the focus is stale (a reload moved the list, or a tile opened on a later title), and then the focus
    /// wins. With nothing reported the panel sits on the focus.
    static func resolve(reported: Double?, focusIndex: Int?, count: Int) -> Double {
        guard count > 0 else { return 0 }
        let top = Double(count - 1)
        let focus = focusIndex.map { min(max(Double($0), 0), top) }
        guard let reported, reported.isFinite else { return focus ?? 0 }
        if let focus, abs(reported - focus) > 1 { return focus }
        return min(max(reported, 0), top)
    }

    /// The two titles `p` is between and how far from the first to the second: `lo = floor(p)`, `hi = lo + 1`, both
    /// kept inside the list. On the last title `hi == lo` and `t == 0`.
    static func split(_ p: Double, count: Int) -> Split {
        guard count > 0 else { return Split(lo: 0, hi: 0, t: 0) }
        let last = count - 1
        let clamped = min(max(p, 0), Double(last))
        let lo = min(max(Int(clamped.rounded(.down)), 0), last)
        let hi = min(lo + 1, last)
        return Split(lo: lo, hi: hi, t: hi == lo ? 0 : min(max(clamped - Double(lo), 0), 1))
    }

    /// The poster `p` reads as: the nearest, but `previous` stays while `p` is within half a poster and the
    /// hysteresis of it.
    static func nearest(_ p: Double, previous: Int, count: Int) -> Int {
        guard count > 0 else { return 0 }
        let last = count - 1
        let kept = min(max(previous, 0), last)
        if abs(p - Double(kept)) <= 0.5 + hysteresis { return kept }
        return min(max(Int(p.rounded()), 0), last)
    }

    static func isSettled(_ p: Double, near: Int) -> Bool {
        abs(p - Double(near)) <= settledWithin
    }
}
