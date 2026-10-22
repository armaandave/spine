import Nuke
import SwiftUI
import UIKit

/// The color behind an album page, taken from its cover the way Apple Music's album pages do it, but darker and
/// calmer than Apple's so the cover stays the brightest thing on the page.
enum AlbumBackdropColor {
    struct HSB: Equatable {
        var hue: Double
        var saturation: Double
        var brightness: Double
    }

    private struct Bucket {
        var red = 0.0
        var green = 0.0
        var blue = 0.0
        var weight = 0.0
    }

    private static let sampleSide = 32

    /// The hero has usually shown the cover already, so its bytes come from the pipeline's disk cache, and the
    /// thumbnail decodes straight to a few dozen pixels.
    static func load(url: URL) async -> Color? {
        var request = ImageRequest(url: url)
        request.thumbnail = ImageRequest.ThumbnailOptions(maxPixelSize: 64)
        guard let image = try? await ImagePipeline.shared.image(for: request),
              let cgImage = image.cgImage,
              let dominant = dominant(in: cgImage)
        else { return nil }
        let tint = tint(for: dominant)
        return Color(hue: tint.hue, saturation: tint.saturation, brightness: tint.brightness)
    }

    /// The cover's main color: the most common hue family among its vivid pixels, or the average of its grays
    /// when those outweigh every hue.
    static func dominant(in image: CGImage) -> HSB? {
        let side = sampleSide
        var pixels = [UInt8](repeating: 0, count: side * side * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(
                    data: buffer.baseAddress,
                    width: side,
                    height: side,
                    bitsPerComponent: 8,
                    bytesPerRow: side * 4,
                    space: colorSpace,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                  )
            else { return false }
            context.interpolationQuality = .medium
            context.draw(image, in: CGRect(x: 0, y: 0, width: side, height: side))
            return true
        }
        guard drawn else { return nil }

        // Twelve 30° hue buckets centred on red, orange, yellow and so on (so reds either side of 0° land
        // together), plus one bucket for grays, blacks and whites.
        var buckets = [Bucket](repeating: Bucket(), count: 13)
        for offset in stride(from: 0, to: pixels.count, by: 4) where pixels[offset + 3] == 255 {
            let red = Double(pixels[offset]) / 255
            let green = Double(pixels[offset + 1]) / 255
            let blue = Double(pixels[offset + 2]) / 255
            let hsb = HSB(red: red, green: green, blue: blue)
            let isVivid = hsb.saturation >= 0.2 && hsb.brightness >= 0.2
            let index = isVivid ? Int((hsb.hue * 12).rounded()) % 12 : 12
            // Vivid pixels count a little extra, so a cover's color beats a similar area of black or white.
            let weight = isVivid ? 0.5 + hsb.saturation * hsb.brightness : 0.5
            buckets[index].red += red * weight
            buckets[index].green += green * weight
            buckets[index].blue += blue * weight
            buckets[index].weight += weight
        }
        // A cover whose blues run from azure to indigo is a blue cover, so each hue also counts half of its two
        // neighbours when picking the winner; the color itself comes from the winning hue's pixels alone.
        func score(_ index: Int) -> Double {
            guard index < 12 else { return buckets[index].weight }
            return buckets[index].weight + (buckets[(index + 11) % 12].weight + buckets[(index + 1) % 12].weight) / 2
        }
        guard let index = buckets.indices.max(by: { score($0) < score($1) }), buckets[index].weight > 0 else {
            return nil
        }
        let winner = buckets[index]
        return HSB(
            red: winner.red / winner.weight,
            green: winner.green / winner.weight,
            blue: winner.blue / winner.weight
        )
    }

    /// Apple Music darkens the cover color to about a third of full brightness and keeps it saturated. This
    /// goes darker and caps the saturation, so a vivid blue cover gives a muted navy rather than a royal blue.
    static func tint(for color: HSB) -> HSB {
        guard color.saturation >= 0.2 else {
            // Black, white and gray covers keep their faint cast on a near-black page.
            return HSB(hue: color.hue, saturation: min(color.saturation, 0.12), brightness: 0.12)
        }
        var degrees = color.hue * 360
        // Darkened yellows turn olive; pulling them toward orange makes them brown, as Apple Music's are.
        if (38...66).contains(degrees) {
            degrees -= (degrees - 30) * 0.6
        }
        return HSB(hue: degrees / 360, saturation: min(color.saturation, 0.62), brightness: 0.24)
    }
}

extension AlbumBackdropColor.HSB {
    init(red: Double, green: Double, blue: Double) {
        var hue: CGFloat = 0
        var saturation: CGFloat = 0
        var brightness: CGFloat = 0
        UIColor(red: red, green: green, blue: blue, alpha: 1)
            .getHue(&hue, saturation: &saturation, brightness: &brightness, alpha: nil)
        self.init(hue: Double(hue), saturation: Double(saturation), brightness: Double(brightness))
    }
}

/// The album color behind the whole page: solid down through the tracklist and details, then fading into the
/// usual near-black over the last stretch, so the page ends like every other page.
struct AlbumBackdrop: View {
    private static let fadeLength: CGFloat = 640

    let tint: Color

    var body: some View {
        GeometryReader { proxy in
            let fadeStart = max(0, 1 - Self.fadeLength / max(proxy.size.height, 1))
            LinearGradient(
                stops: [
                    .init(color: tint, location: 0),
                    .init(color: tint, location: fadeStart),
                    .init(color: tint.opacity(0.55), location: fadeStart + (1 - fadeStart) * 0.4),
                    .init(color: tint.opacity(0.15), location: fadeStart + (1 - fadeStart) * 0.75),
                    .init(color: tint.opacity(0), location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        }
        .accessibilityHidden(true)
    }
}
