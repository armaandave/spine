import CoreGraphics
import SwiftUI
import UIKit

/// The numbers of the open In progress tile's panel (board R4InProgress, variant A). Panel coordinates start at the
/// panel's top-left; `W` is the panel's width.
nonisolated enum InProgressPanelMetrics {
    /// The panel is this tall whatever it shows, so a swipe never moves the tile.
    static let height: Double = 114
    /// The tile below the panel: `TileGeometry.panelTop + height + bottomMargin == HomeDesign.Metric.tileOpenHeight`.
    static let bottomMargin: Double = 28

    static let titleRowHeight: Double = 24
    /// The right-hand words sit this far down inside the row (board `top: 2px`).
    static let whereOffsetY: Double = 2
    /// Space between the title and the right-hand words.
    static let titleGap: Double = 10

    static let meterTop: Double = 40
    static let meterHeight: Double = 10
    static let meterRadius: Double = 3
    static let barRadius: Double = 5
    static let tickGap: Double = 3

    /// The series line under a window of ticks and its marker, relative to the meter's top.
    static let lineTop: Double = 15
    static let lineHeight: Double = 2
    static let markerWidth: Double = 3
    static let markerHeight: Double = 6

    static let buttonsTop: Double = 78
    static let buttonHeight: Double = 36
    /// The hit area reaches this far above and below the glass.
    static let hitSlop: Double = 4

    /// The pool of cells every kind of meter is drawn from: `ProgressMeterModel.maxTicks`.
    static let cellCount = 30

    /// A label needs this much room beyond its words to be chosen (14 pt each side).
    static let labelPadding: Double = 28

    /// How far the meter canvas reaches past the meter, so a glow is not clipped.
    static let canvasMarginX: Double = 12
    static let canvasMarginTop: Double = 12
    static let canvasMarginBottom: Double = 24

    /// The `GlassEffectContainer` merge distance: buttons rest 10 apart and stay separate glass; below this the
    /// system bridges them.
    static let glassMergeSpacing: Double = 6

    // MARK: Fonts

    static let titleFont = HomeDesign.Text.panelTitle
    static let whereFont = Font.system(size: 13, weight: .bold).monospacedDigit()
    static let labelFont = Font.system(size: 14.5, weight: .semibold)
    static let iconFont = Font.system(size: 17, weight: .semibold)

    static let whereUIFont = UIFont.monospacedDigitSystemFont(ofSize: 13, weight: .bold)
    static let labelUIFont = UIFont.systemFont(ofSize: 14.5, weight: .semibold)
    static let labelTracking: Double = -0.1
}

/// Colors the panel draws that `TileStyle` has no token for. Everything else comes from `TileStyle`.
nonisolated enum PanelStyle {
    /// The tint over the primary button's glass: the board's gradient (white .14 to .04) at its mean.
    static let primaryTint = Color.white.opacity(0.09)
    /// How strong the accent glow behind `+N` is at full.
    static let quickGlow = 0.16
    /// Strength of the lit span's glow (board `box-shadow 0 0 10px accent .6`).
    static let litGlow = 0.6

    /// Four stacked, inflated rounded rects that approximate a Gaussian of sigma 5.
    static let litGlowLayers: [(inflate: Double, alpha: Double)] = [
        (9, 0.040), (6, 0.073), (3.5, 0.101), (1.5, 0.100),
    ]
    /// The series line marker's glow (board `0 0 8px accent .7`).
    static let markerGlowLayers: [(inflate: Double, alpha: Double)] = [(5, 0.12), (2.5, 0.20)]
}

/// Every animation of the panel. Reduce Motion replaces each with the short fade, except the scrub, which is not an
/// animation at all.
nonisolated enum PanelMotion {
    /// The board's plain `ease`.
    static func ease(_ duration: Double) -> Animation {
        .timingCurve(0.25, 0.1, 0.25, 1.0, duration: duration)
    }

    /// A title's own data changed (a tap answered, a season read, a reload): the whole vector, 0.65 s.
    static func settle(reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.soft(duration: 0.65, reduceMotion: reduceMotion)
    }

    /// A word slot's width: 0.5 s.
    static func slot(reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.soft(duration: 0.5, reduceMotion: reduceMotion)
    }

    /// Words entering and leaving: blur-replace, 0.5 s in and 0.3 s out. Reduce Motion: opacity only.
    static func wordSwap(reduceMotion: Bool) -> AnyTransition {
        if reduceMotion { return .opacity.animation(HomeDesign.Motion.reduced) }
        let blur = AnyTransition(BlurReplaceTransition(configuration: .upUp))
        return .asymmetric(
            insertion: blur.animation(HomeDesign.Motion.soft(duration: 0.5, reduceMotion: false)),
            removal: blur.animation(ease(0.3))
        )
    }

    /// The bright sweep after a tap: 0.85 s.
    static let sweepDuration = 0.85
}
