import CoreGraphics
import Foundation
import SwiftUI

/// Whether text on a piece of art is dark (the art is light) or white (the art is dark), and the colors that go
/// with each. The poster's accent color is only the first guess: once the baked art is on screen its measured
/// luma decides (`resolve`), so a title the server sent no accent for still gets readable text.
nonisolated enum ArtInk: Equatable {
    /// Dark text on light art.
    case light
    /// White text on dark art.
    case dark

    // MARK: Choosing

    /// WCAG relative luminance of the accent at or above `lightInkMinLuminance` is light art. Nil, empty and
    /// malformed hex (three digits included) are dark.
    static func guess(accentHex: String?) -> ArtInk {
        guard let rgb = ArtHex.rgb(accentHex) else { return .dark }
        return ArtHex.relativeLuminance(rgb) >= HomeDesign.Palette.lightInkMinLuminance ? .light : .dark
    }

    static let lightAtOrAbove = 0.54
    static let darkAtOrBelow = 0.46

    /// The ink for measured art luma. Between 0.46 and 0.54 the guess stays, so near-threshold art does not flip
    /// as its image loads; no measurement keeps the guess too.
    static func resolve(luma: Double?, guess: ArtInk) -> ArtInk {
        conclusive(luma: luma) ?? guess
    }

    /// The ink a measured luma settles by itself: nil for no measurement and for the dead band between 0.46 and
    /// 0.54, where the guess stays. A card keeps only this, so a measurement that decides nothing new redraws nothing
    /// of its text.
    static func conclusive(luma: Double?) -> ArtInk? {
        guard let luma else { return nil }
        if luma >= lightAtOrAbove { return .light }
        if luma <= darkAtOrBelow { return .dark }
        return nil
    }

    // MARK: Tokens

    private var ink: Color { HomeDesign.Palette.lightInk }

    var text: Color {
        self == .dark ? .white : ink
    }

    var muted: Color {
        self == .dark ? Color.white.opacity(0.80) : ink.opacity(0.72)
    }

    /// The part of a bar or tick row before today.
    var before: Color {
        self == .dark ? Color.white.opacity(0.45) : ink.opacity(0.36)
    }

    /// The unfilled part of a bar or tick row.
    var rest: Color {
        self == .dark ? Color.white.opacity(0.16) : ink.opacity(0.12)
    }

    var star: Color {
        self == .dark ? HomeDesign.Palette.starGold : ink
    }

    var heart: Color {
        HomeDesign.Palette.heart
    }

    var chipFill: Color {
        self == .dark ? Color.black.opacity(0.26) : Color.white.opacity(0.45)
    }

    var chipLine: Color {
        self == .dark ? Color.white.opacity(0.22) : Color.white.opacity(0.60)
    }

    /// The day after a card's username: lighter than `muted`.
    var day: Color {
        self == .dark ? Color.white.opacity(0.62) : ink.opacity(0.58)
    }

    /// The rewatch glyph: a green on dark art, a deeper green on light art that still reads on it.
    var rewatch: Color {
        self == .dark ? HomeDesign.Palette.rewatchGreen : HomeDesign.Palette.rewatchGreenDeep
    }

    /// Colors the amount text, "Started", lit ticks and the lit part of a bar: a pastel of the poster color on
    /// dark art (white when there is none) and the ink itself on light art.
    func accent(posterAccentHex: String?) -> Color {
        switch self {
        case .dark:
            ArtAccent.pastel(fromHexOrNil: posterAccentHex) ?? .white
        case .light:
            ink
        }
    }
}

/// How bright the part of a card's art that text sits on is.
nonisolated enum ArtLuma {
    static let gridWidth = 16
    static let gridHeight = 10
    static let memoLimit = 512

    /// The mean Rec.601 luma, 0 to 1, over the right two thirds of the image, where a card's text sits, sampled
    /// on a 16x10 grid by drawing into a small bitmap.
    static func measure(_ image: CGImage) -> Double? {
        let width = image.width
        let height = image.height
        guard width >= 3, height >= 1 else { return nil }
        let cropX = width / 3
        guard let region = image.cropping(to: CGRect(x: cropX, y: 0, width: width - cropX, height: height)) else {
            return nil
        }
        let bytesPerRow = gridWidth * 4
        var pixels = [UInt8](repeating: 0, count: bytesPerRow * gridHeight)
        let measured: Bool = pixels.withUnsafeMutableBytes { buffer in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: gridWidth,
                height: gridHeight,
                bitsPerComponent: 8,
                bytesPerRow: bytesPerRow,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
            ) else { return false }
            context.interpolationQuality = .high
            context.draw(region, in: CGRect(x: 0, y: 0, width: gridWidth, height: gridHeight))
            return true
        }
        guard measured else { return nil }
        var total = 0.0
        for index in stride(from: 0, to: pixels.count, by: 4) {
            total += 0.299 * Double(pixels[index]) + 0.587 * Double(pixels[index + 1]) + 0.114 * Double(pixels[index + 2])
        }
        return total / Double(gridWidth * gridHeight) / 255
    }

    /// `measure`, remembered by URL string so a card scrolled back in never measures again.
    static func measure(_ image: CGImage, key: String) -> Double? {
        if let known = memo.value(for: key) { return known }
        guard let luma = measure(image) else { return nil }
        memo.store(luma, for: key)
        return luma
    }

    static func remembered(key: String) -> Double? {
        memo.value(for: key)
    }

    static func forgetAll() {
        memo.removeAll()
    }

    static var memoCount: Int { memo.count }

    private static let memo = Memo(limit: memoLimit)

    /// A small thread-safe first-in-first-out cache.
    private nonisolated final class Memo: @unchecked Sendable {
        private let lock = NSLock()
        private let limit: Int
        private var values: [String: Double] = [:]
        private var order: [String] = []

        init(limit: Int) {
            self.limit = limit
        }

        var count: Int {
            lock.lock()
            defer { lock.unlock() }
            return values.count
        }

        func value(for key: String) -> Double? {
            lock.lock()
            defer { lock.unlock() }
            return values[key]
        }

        func store(_ value: Double, for key: String) {
            lock.lock()
            defer { lock.unlock() }
            if values.updateValue(value, forKey: key) == nil {
                order.append(key)
                if order.count > limit {
                    values.removeValue(forKey: order.removeFirst())
                }
            }
        }

        func removeAll() {
            lock.lock()
            defer { lock.unlock() }
            values.removeAll()
            order.removeAll()
        }
    }
}

/// The soft nudge drawn as a plain overlay over baked card art: black over art that takes white text, white over
/// art that takes dark text. It is bounded on purpose, since the design's own tone range is x0.75 to x1.06 and
/// mid-tone art must not be washed out.
nonisolated enum ArtTone {
    static func overlayColor(ink: ArtInk) -> Color {
        ink == .dark ? .black : .white
    }

    static func overlayAlpha(ink: ArtInk, luma: Double?) -> Double {
        switch ink {
        case .dark:
            guard let luma else { return 0.20 }
            return min(max(1 - 0.36 / max(luma, 0.01), 0.06), 0.34)
        case .light:
            guard let luma else { return 0.04 }
            guard luma < 0.62 else { return 0 }
            return min(max((0.62 - luma) / (1 - luma), 0), 0.22)
        }
    }
}

/// The pastel a dark card colors its amount, "Started" and lit ticks with.
nonisolated enum ArtAccent {
    /// Keeps the hue, sets saturation to 0.6 of the original within 0.12 to 0.46, and brightness to 0.97. Grey
    /// has no hue to keep, and nil has no color: both give near white.
    static func pastel(fromHex hex: String?) -> Color {
        pastel(fromHexOrNil: hex) ?? Color(white: 0.97)
    }

    static func pastelHSB(fromHex hex: String?) -> (hue: Double, saturation: Double, brightness: Double) {
        guard let rgb = ArtHex.rgb(hex) else { return (0, 0, 0.97) }
        let (hue, saturation, _) = ArtHex.hsb(rgb)
        guard saturation >= 0.03 else { return (0, 0, 0.97) }
        return (hue, min(max(saturation * 0.6, 0.12), 0.46), 0.97)
    }

    /// Nil when the hex is missing or malformed, so a caller can fall back to white.
    static func pastel(fromHexOrNil hex: String?) -> Color? {
        guard ArtHex.rgb(hex) != nil else { return nil }
        let hsb = pastelHSB(fromHex: hex)
        return Color(hue: hsb.hue, saturation: hsb.saturation, brightness: hsb.brightness)
    }
}

/// Hex parsing and color math for art, kept here so no SwiftUI or UIKit conversion decides a text color.
nonisolated enum ArtHex {
    nonisolated struct RGB: Equatable {
        let red: Double
        let green: Double
        let blue: Double
    }

    /// Six hex digits with an optional leading "#". Anything else is nil.
    static func rgb(_ hex: String?) -> RGB? {
        guard let hex else { return nil }
        var value = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("#") { value.removeFirst() }
        guard value.count == 6, value.allSatisfy(\.isHexDigit), let int = UInt32(value, radix: 16) else {
            return nil
        }
        return RGB(
            red: Double((int >> 16) & 0xFF) / 255,
            green: Double((int >> 8) & 0xFF) / 255,
            blue: Double(int & 0xFF) / 255
        )
    }

    /// WCAG 2 relative luminance.
    static func relativeLuminance(_ rgb: RGB) -> Double {
        func linear(_ channel: Double) -> Double {
            channel <= 0.04045 ? channel / 12.92 : pow((channel + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(rgb.red) + 0.7152 * linear(rgb.green) + 0.0722 * linear(rgb.blue)
    }

    /// Hue 0 to 1, saturation 0 to 1, brightness 0 to 1.
    static func hsb(_ rgb: RGB) -> (hue: Double, saturation: Double, brightness: Double) {
        let maxValue = max(rgb.red, rgb.green, rgb.blue)
        let minValue = min(rgb.red, rgb.green, rgb.blue)
        let delta = maxValue - minValue
        guard maxValue > 0 else { return (0, 0, 0) }
        let saturation = delta / maxValue
        guard delta > 0 else { return (0, saturation, maxValue) }
        var hue: Double
        if maxValue == rgb.red {
            hue = (rgb.green - rgb.blue) / delta
        } else if maxValue == rgb.green {
            hue = 2 + (rgb.blue - rgb.red) / delta
        } else {
            hue = 4 + (rgb.red - rgb.green) / delta
        }
        hue /= 6
        if hue < 0 { hue += 1 }
        return (hue, saturation, maxValue)
    }
}
