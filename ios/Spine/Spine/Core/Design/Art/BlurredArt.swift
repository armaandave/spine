import CoreImage
import Foundation
import Nuke
import UIKit

/// Blurred, saturated, dimmed artwork baked once off the main thread and cached by Nuke, so no view ever applies a
/// live blur. Each style loads one small thumbnail (a blurred image has no detail to lose) and the processor
/// bakes the design's `blur() saturate() brightness()` filter into it.
nonisolated enum BlurredArt {
    nonisolated enum Style: String, CaseIterable, Hashable {
        /// Behind a feed card.
        case card
        /// Behind a Home tile.
        case tile
        /// The page glow at the top of Home.
        case glow
        /// Behind a post page.
        case page

        /// The thumbnail Nuke decodes, in points.
        var thumbnailSize: CGSize {
            switch self {
            case .card: CGSize(width: 40, height: 24)
            case .tile: CGSize(width: 40, height: 40)
            case .glow: CGSize(width: 64, height: 77)
            case .page: CGSize(width: 54, height: 93)
            }
        }

        /// The bake's shape, width over height, or nil to keep the thumbnail's. A card's art is the whole poster
        /// squeezed to the card's shape (about 358 x 115), so the card shows the poster's colors from top to bottom
        /// instead of a magnified middle band, and the view stretches it to the card without cropping.
        var bakedAspect: CGFloat? {
            switch self {
            case .card: 3.1
            case .tile, .glow, .page: nil
            }
        }

        /// Gaussian sigma in pixels of a 3x thumbnail. Other scales get the same look: the sigma follows the
        /// decoded bitmap's width. The card's is light (about 6% of its width) so its colors stay distinct.
        var sigmaAt3x: Double {
            switch self {
            case .card: 7
            case .tile: 14
            case .glow: 16
            case .page: 15
            }
        }

        /// The card and the closed tiles stay near the poster's own colors, so Home is calm; the others are boosted.
        var saturation: Double {
            switch self {
            case .card: 1.1
            case .tile: 1.0
            case .glow: 1.75
            case .page: 1.6
            }
        }

        /// A multiplier on the color channels. The card is darkened like the tiles, a little less (0.7).
        var brightness: Double {
            switch self {
            case .card: 0.7
            case .tile: 0.5
            case .glow: 0.82
            case .page: 0.6
            }
        }

        var processor: BlurredArtProcessor {
            BlurredArtProcessor(style: self)
        }
    }

    /// The request every view and prefetcher uses for this art, so they share one cache entry.
    static func request(url: URL?, style: Style) -> ImageRequest? {
        SpineImagePipeline.request(url: url, thumbnailSize: style.thumbnailSize, processors: [style.processor])
    }

    /// One context for every bake: contexts are expensive to make and safe to share between threads. It works in
    /// sRGB like a browser's CSS filters, so `brightness(0.6)` multiplies the displayed values by 0.6 rather than
    /// the linear light, which would only dim them to 0.79.
    nonisolated(unsafe) static let context = CIContext(options: [
        .cacheIntermediates: false,
        .workingColorSpace: CGColorSpace(name: CGColorSpace.sRGB) as Any,
    ])
}

/// Blur, then saturation, then a multiplicative brightness, in one Core Image pass.
nonisolated struct BlurredArtProcessor: ImageProcessing, Hashable {
    let style: BlurredArt.Style

    var identifier: String {
        let aspect = style.bakedAspect.map { "&aspect=\($0)" } ?? ""
        return "com.armaan.spine.blurredart.\(style.rawValue)?sigma=\(style.sigmaAt3x)&sat=\(style.saturation)&bright=\(style.brightness)\(aspect)"
    }

    func process(_ image: PlatformImage) -> PlatformImage? {
        guard let source = image.cgImage else { return nil }
        var input = CIImage(cgImage: source)
        var extent = CGRect(x: 0, y: 0, width: source.width, height: source.height)
        if let aspect = style.bakedAspect {
            // Squeeze the whole image to the bake's shape before blurring, so the blur is round on screen.
            let height = max(1, (CGFloat(source.width) / aspect).rounded())
            input = input.transformed(by: CGAffineTransform(scaleX: 1, y: height / CGFloat(source.height)))
            extent.size.height = height
        }
        let sigma = max(style.sigmaAt3x * Double(source.width) / (style.thumbnailSize.width * 3), 0.01)
        let brightness = style.brightness

        // Clamping first extends the edge pixels outward, so the blur does not fade the borders to transparent;
        // cropping afterwards puts the image back at its own size.
        let baked = input
            .clampedToExtent()
            .applyingGaussianBlur(sigma: sigma)
            .cropped(to: extent)
            .applyingFilter("CIColorControls", parameters: [kCIInputSaturationKey: style.saturation])
            .applyingFilter("CIColorMatrix", parameters: [
                "inputRVector": CIVector(x: brightness, y: 0, z: 0, w: 0),
                "inputGVector": CIVector(x: 0, y: brightness, z: 0, w: 0),
                "inputBVector": CIVector(x: 0, y: 0, z: brightness, w: 0),
                "inputAVector": CIVector(x: 0, y: 0, z: 0, w: 1),
            ])
            .cropped(to: extent)

        guard let output = BlurredArt.context.createCGImage(
            baked,
            from: extent,
            format: .RGBA8,
            colorSpace: CGColorSpace(name: CGColorSpace.sRGB)
        ) else { return nil }
        return PlatformImage(cgImage: output, scale: image.scale, orientation: image.imageOrientation)
    }
}
