import SwiftUI

/// The color of the Trending rail's Open icon: the title's poster color, as the In progress meter takes it. The icon sits
/// on dark glass over the dark tile, so a poster's first color that is too dim there gives way to its second, then its
/// third; with none that reads, the icon stays white.
nonisolated enum TrendingOpenTint {
    /// The tile and the glass over it, as a relative luminance (about rgb 0.20).
    static let backgroundLuminance = 0.033
    /// An icon wants 3:1 against its background; a hair under it still reads at 16 pt semibold on glass.
    static let minimumContrast = 2.6
    /// With Increase Contrast on, the icon wants the full 3:1.
    static let highContrastMinimum = 3.0

    /// The WCAG relative luminance of an sRGB color.
    static func luminance(_ color: MeterTint.RGB) -> Double {
        func linear(_ value: Double) -> Double {
            value <= 0.03928 ? value / 12.92 : pow((value + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(color.red) + 0.7152 * linear(color.green) + 0.0722 * linear(color.blue)
    }

    /// How much lighter than the dark tile `color` is, as the 1...21 ratio.
    static func contrast(_ color: MeterTint.RGB) -> Double {
        (luminance(color) + 0.05) / (backgroundLuminance + 0.05)
    }

    /// The first color of the poster's ramp that reads on the dark tile, or nil.
    static func pick(from tint: MeterTint?, highContrast: Bool = false) -> MeterTint.RGB? {
        let minimum = highContrast ? highContrastMinimum : minimumContrast
        return tint?.ramp.first { contrast($0) >= minimum }
    }

    /// The Open icon's color for a poster's swatches; nil keeps the icon white.
    static func color(from swatches: [PosterSwatch]?, highContrast: Bool = false) -> Color? {
        guard let swatches, !swatches.isEmpty else { return nil }
        return pick(from: MeterTint.make(from: swatches, highContrast: highContrast), highContrast: highContrast)?.color
    }
}
