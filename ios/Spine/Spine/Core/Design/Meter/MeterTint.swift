import SwiftUI

/// The colors a poster lends the In progress meter: a short ramp for the days before today, left to right, and a
/// brighter color for today's part. Chosen from the poster's palette so they read on the dark open tile.
nonisolated struct MeterTint: Equatable, Sendable {
    nonisolated struct RGB: Equatable, Sendable {
        var red: Double
        var green: Double
        var blue: Double

        var color: Color { Color(.sRGB, red: red, green: green, blue: blue, opacity: 1) }

        init(red: Double, green: Double, blue: Double) {
            self.red = red
            self.green = green
            self.blue = blue
        }

        init?(hex: String) {
            let digits = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
            guard digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
            self.init(
                red: Double((value >> 16) & 0xFF) / 255,
                green: Double((value >> 8) & 0xFF) / 255,
                blue: Double(value & 0xFF) / 255
            )
        }

        var hsb: (hue: Double, saturation: Double, brightness: Double) {
            let high = max(red, green, blue)
            let low = min(red, green, blue)
            let delta = high - low
            var hue = 0.0
            if delta > 0 {
                if high == red {
                    hue = ((green - blue) / delta).truncatingRemainder(dividingBy: 6)
                } else if high == green {
                    hue = (blue - red) / delta + 2
                } else {
                    hue = (red - green) / delta + 4
                }
                hue /= 6
                if hue < 0 { hue += 1 }
            }
            return (hue, high == 0 ? 0 : delta / high, high)
        }

        init(hue: Double, saturation: Double, brightness: Double) {
            let h = (hue - hue.rounded(.down)) * 6
            let c = brightness * saturation
            let x = c * (1 - abs(h.truncatingRemainder(dividingBy: 2) - 1))
            let m = brightness - c
            let (r, g, b): (Double, Double, Double) = switch Int(h) {
            case 0: (c, x, 0)
            case 1: (x, c, 0)
            case 2: (0, c, x)
            case 3: (0, x, c)
            case 4: (x, 0, c)
            default: (c, 0, x)
            }
            self.init(red: r + m, green: g + m, blue: b + m)
        }

        func mixed(toward other: RGB, by amount: Double) -> RGB {
            RGB(
                red: red + (other.red - red) * amount,
                green: green + (other.green - green) * amount,
                blue: blue + (other.blue - blue) * amount
            )
        }

        static let white = RGB(red: 1, green: 1, blue: 1)
    }

    /// One to three colors for the earlier days, left to right.
    var ramp: [RGB]
    /// Today's part: the brightest color.
    var today: RGB

    /// The ramp's color at `fraction` of the way along the meter.
    func color(at fraction: Double) -> Color {
        guard ramp.count > 1 else { return (ramp.first ?? today).color }
        let position = min(max(fraction, 0), 1) * Double(ramp.count - 1)
        let lower = min(Int(position), ramp.count - 2)
        return ramp[lower].mixed(toward: ramp[lower + 1], by: position - Double(lower)).color
    }

    /// The ramp's color at `fraction` as plain components, for drawing code that works on numbers.
    func components(at fraction: Double) -> (red: Double, green: Double, blue: Double) {
        guard ramp.count > 1 else {
            let only = ramp.first ?? today
            return (only.red, only.green, only.blue)
        }
        let position = min(max(fraction, 0), 1) * Double(ramp.count - 1)
        let lower = min(Int(position), ramp.count - 2)
        let mixed = ramp[lower].mixed(toward: ramp[lower + 1], by: position - Double(lower))
        return (mixed.red, mixed.green, mixed.blue)
    }

    /// The ramp as gradient stops, for the bar's earlier part.
    var gradientColors: [Color] { ramp.map(\.color) }

    // MARK: Choosing

    /// Swatches smaller than this are specks, not colors of the poster.
    static let minimumShare = 0.02
    /// Dark but colored swatches are lifted to this brightness (HSB).
    static let minimumBrightness = 0.68
    static let highContrastMinimumBrightness = 0.8
    static let maximumColors = 3

    /// The colors to draw for a poster, or nil when it has none worth drawing (nothing but black, white and specks):
    /// the meter then keeps its plain look.
    ///
    /// - Near-black swatches (a poster's black border) and near-white ones (its paper) are skipped.
    /// - The rest are ranked by how much of the poster they cover, with a lean toward vivid ones, and the top three
    ///   become the ramp.
    /// - A dark color is lifted to a brightness that reads on the dark tile and against its unfilled track, keeping
    ///   its hue.
    /// - Today's part is the brightest of them, pushed toward white.
    /// - With Increase Contrast the colors are lifted further and today's part is closer to white, so lit and unlit
    ///   stay clearly apart.
    static func make(from swatches: [PosterSwatch], highContrast: Bool = false) -> MeterTint? {
        struct Candidate {
            let rgb: RGB
            let score: Double
        }
        let candidates: [Candidate] = swatches.compactMap { swatch in
            guard swatch.share >= minimumShare, let rgb = RGB(hex: swatch.hex) else { return nil }
            let lab = PosterPaletteExtractor.lab(red: rgb.red * 255, green: rgb.green * 255, blue: rgb.blue * 255)
            let chroma = (lab.1 * lab.1 + lab.2 * lab.2).squareRoot()
            let nearBlack = lab.0 < 4 || (lab.0 < 20 && chroma < 8)
            let nearWhite = lab.0 > 93 && chroma < 12
            guard !nearBlack, !nearWhite else { return nil }
            return Candidate(rgb: rgb, score: swatch.share.squareRoot() * (1 + chroma / 40))
        }
        let picked = candidates.sorted { $0.score > $1.score }.prefix(maximumColors)
        guard !picked.isEmpty else { return nil }

        let floor = highContrast ? highContrastMinimumBrightness : minimumBrightness
        let ramp: [RGB] = picked.map { candidate in
            let hsb = candidate.rgb.hsb
            return RGB(
                hue: hsb.hue,
                saturation: hsb.saturation,
                brightness: min(max(hsb.brightness, floor), 0.97)
            )
        }
        let brightest = ramp.max { $0.hsb.brightness < $1.hsb.brightness } ?? ramp[0]
        let today = brightest.mixed(toward: .white, by: highContrast ? 0.6 : 0.4)
        return MeterTint(ramp: ramp, today: today)
    }
}
