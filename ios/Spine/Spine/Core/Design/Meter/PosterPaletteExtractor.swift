import CoreGraphics
import Foundation
import Nuke
import UIKit

/// The same count the server makes (`extract_palette`): the poster at 96 px, median-cut down to 24 colors, colors
/// closer than 20 apart in CIELAB merged, the biggest 5 kept with their shares. Pure pixel work, so it runs off the main
/// thread (`nonisolated`; the app module defaults to `@MainActor`).
nonisolated enum PosterPaletteExtractor {
    static let side = 96
    static let quantizedColors = 24
    static let mergeDistance = 20.0
    static let paletteSize = 5

    // MARK: Loading

    /// The poster through the shared image pipeline, decoded straight to 96 px. The carousel has usually shown it
    /// already, so its bytes come from the pipeline's cache and nothing is downloaded again.
    /// `limit` is how many colors to keep; Home's picture keeps more than a poster's five, to find a second color.
    @concurrent
    static func load(url: URL, limit: Int = paletteSize) async -> [PosterSwatch]? {
        var request = ImageRequest(url: url)
        request.thumbnail = ImageRequest.ThumbnailOptions(maxPixelSize: Float(side))
        return await load(request: request, limit: limit)
    }

    /// The colors of the image `request` makes, as it is shown (after its processors), counted at 96 px.
    @concurrent
    static func load(request: ImageRequest, limit: Int = paletteSize) async -> [PosterSwatch]? {
        guard let image = try? await ImagePipeline.shared.image(for: request), let cgImage = image.cgImage else {
            return nil
        }
        return palette(of: cgImage, limit: limit)
    }

    // MARK: Counting

    static func palette(of image: CGImage, limit: Int = paletteSize) -> [PosterSwatch]? {
        guard let pixels = rgbPixels(of: image), !pixels.isEmpty else { return nil }
        return palette(of: pixels, limit: limit)
    }

    /// Each pixel as 0xRRGGBB, transparency flattened onto white like the server does, long side at most 96.
    static func rgbPixels(of image: CGImage) -> [UInt32]? {
        let scale = min(1, Double(side) / Double(max(image.width, image.height)))
        let width = max(1, Int((Double(image.width) * scale).rounded()))
        let height = max(1, Int((Double(image.height) * scale).rounded()))
        var data = [UInt8](repeating: 255, count: width * height * 4)
        let drawn = data.withUnsafeMutableBytes { buffer -> Bool in
            guard let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(
                    data: buffer.baseAddress,
                    width: width,
                    height: height,
                    bitsPerComponent: 8,
                    bytesPerRow: width * 4,
                    space: space,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                  )
            else { return false }
            context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
            context.interpolationQuality = .medium
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        var pixels = [UInt32]()
        pixels.reserveCapacity(width * height)
        for offset in stride(from: 0, to: data.count, by: 4) {
            pixels.append(UInt32(data[offset]) << 16 | UInt32(data[offset + 1]) << 8 | UInt32(data[offset + 2]))
        }
        return pixels
    }

    /// The server's steps on a list of 0xRRGGBB pixels, keeping the biggest `limit` colors (the server keeps five).
    static func palette(of pixels: [UInt32], limit: Int = paletteSize) -> [PosterSwatch] {
        let swatches = medianCut(pixels, into: quantizedColors)
        let total = Double(swatches.reduce(0) { $0 + $1.count })
        guard total > 0 else { return [] }

        // Each group keeps its biggest swatch's color as its center, so groups don't drift.
        struct Group {
            var lab: (Double, Double, Double)
            var count: Int
            var sum: (Double, Double, Double)
        }
        var groups = [Group]()
        for swatch in swatches.sorted(by: { $0.count > $1.count }) {
            let lab = Self.lab(red: swatch.red, green: swatch.green, blue: swatch.blue)
            let weight = Double(swatch.count)
            if let index = groups.firstIndex(where: { distance($0.lab, lab) <= mergeDistance }) {
                groups[index].count += swatch.count
                groups[index].sum.0 += swatch.red * weight
                groups[index].sum.1 += swatch.green * weight
                groups[index].sum.2 += swatch.blue * weight
            } else {
                groups.append(Group(
                    lab: lab,
                    count: swatch.count,
                    sum: (swatch.red * weight, swatch.green * weight, swatch.blue * weight)
                ))
            }
        }
        groups.sort { $0.count > $1.count }
        return groups.prefix(limit).map { group in
            let count = Double(group.count)
            return PosterSwatch(
                hex: hex(red: group.sum.0 / count, green: group.sum.1 / count, blue: group.sum.2 / count),
                share: (count / total * 1000).rounded() / 1000
            )
        }
    }

    // MARK: Median cut

    private struct Swatch {
        var red: Double
        var green: Double
        var blue: Double
        var count: Int
    }

    /// Splits the pixels into at most `target` boxes, always cutting the biggest box that still has two colors, along
    /// its widest channel at the median pixel; each box's swatch is its mean color.
    private static func medianCut(_ pixels: [UInt32], into target: Int) -> [Swatch] {
        var boxes = [pixels]
        while boxes.count < target {
            guard let index = boxes.indices
                .filter({ boxes[$0].count > 1 && channelRange(boxes[$0]).range > 0 })
                .max(by: { boxes[$0].count < boxes[$1].count })
            else { break }
            let box = boxes[index]
            let shift = channelRange(box).shift
            let sorted = box.sorted { ($0 >> shift) & 0xFF < ($1 >> shift) & 0xFF }
            var middle = sorted.count / 2
            // Never cut between equal values of the channel: pixels of one color stay in one box.
            let value = (sorted[middle] >> shift) & 0xFF
            while middle > 0, (sorted[middle - 1] >> shift) & 0xFF == value { middle -= 1 }
            if middle == 0 {
                middle = sorted.count / 2
                while middle < sorted.count, (sorted[middle] >> shift) & 0xFF == value { middle += 1 }
            }
            boxes[index] = Array(sorted[..<middle])
            boxes.append(Array(sorted[middle...]))
        }
        return boxes.map { box in
            var red = 0.0, green = 0.0, blue = 0.0
            for pixel in box {
                red += Double((pixel >> 16) & 0xFF)
                green += Double((pixel >> 8) & 0xFF)
                blue += Double(pixel & 0xFF)
            }
            let count = Double(box.count)
            return Swatch(red: red / count, green: green / count, blue: blue / count, count: box.count)
        }
    }

    /// The channel (as a bit shift) with the widest spread, and that spread.
    private static func channelRange(_ box: [UInt32]) -> (shift: UInt32, range: UInt32) {
        var low: [UInt32] = [255, 255, 255]
        var high: [UInt32] = [0, 0, 0]
        let shifts: [UInt32] = [16, 8, 0]
        for pixel in box {
            for slot in 0 ..< 3 {
                let value = (pixel >> shifts[slot]) & 0xFF
                low[slot] = min(low[slot], value)
                high[slot] = max(high[slot], value)
            }
        }
        let ranges = (0 ..< 3).map { high[$0] - low[$0] }
        let widest = ranges.indices.max(by: { ranges[$0] < ranges[$1] }) ?? 0
        return (shifts[widest], ranges[widest])
    }

    // MARK: Color math

    /// sRGB (0...255) to CIELAB, D65: the server's `_lab`.
    static func lab(red: Double, green: Double, blue: Double) -> (Double, Double, Double) {
        func linear(_ value: Double) -> Double {
            let v = value / 255
            return v <= 0.04045 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4)
        }
        func f(_ value: Double) -> Double {
            value > 0.008856 ? pow(value, 1.0 / 3.0) : 7.787 * value + 16.0 / 116.0
        }
        let r = linear(red), g = linear(green), b = linear(blue)
        let x = f((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047)
        let y = f(0.2126 * r + 0.7152 * g + 0.0722 * b)
        let z = f((0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
        return (116 * y - 16, 500 * (x - y), 200 * (y - z))
    }

    private static func distance(_ a: (Double, Double, Double), _ b: (Double, Double, Double)) -> Double {
        let l = a.0 - b.0, m = a.1 - b.1, n = a.2 - b.2
        return (l * l + m * m + n * n).squareRoot()
    }

    private static func hex(red: Double, green: Double, blue: Double) -> String {
        func byte(_ v: Double) -> Int { min(255, max(0, Int(v.rounded()))) }
        return String(format: "#%02X%02X%02X", byte(red), byte(green), byte(blue))
    }
}
