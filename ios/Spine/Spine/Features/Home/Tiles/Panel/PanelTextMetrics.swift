import UIKit

/// Real text widths, so a slot can be exactly as wide as its words and animate to the next words' width. The
/// panel's fonts are fixed-size, so a width depends only on the text.
nonisolated enum PanelTextMetrics {
    nonisolated(unsafe) private static let cache = NSCache<NSString, NSNumber>()

    /// The width in points, rounded up, plus one point of slack for Bold Text.
    static func width(_ text: String, font: UIFont, tracking: Double = 0) -> Double {
        guard !text.isEmpty else { return 0 }
        let key = "\(font.fontName)|\(font.pointSize)|\(tracking)|\(text)" as NSString
        if let cached = cache.object(forKey: key) { return cached.doubleValue }
        let size = (text as NSString).size(withAttributes: [.font: font, .kern: tracking])
        let measured = ceil(size.width) + 1
        cache.setObject(NSNumber(value: measured), forKey: key)
        return measured
    }

    /// One digit's advance in the where font (all digits are the same width): the slack is not part of it.
    static var digitWidth: Double {
        width("0", font: InProgressPanelMetrics.whereUIFont) - 1
    }

    static var commaWidth: Double {
        width(",", font: InProgressPanelMetrics.whereUIFont)
    }
}
