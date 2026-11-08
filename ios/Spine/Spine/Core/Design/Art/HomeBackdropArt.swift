import CoreImage
import Foundation
import Nuke
import UIKit

/// The picture at the top of Home: a title's backdrop, cropped to a tall frame, sharp at its top and melting into a
/// soft blur of itself lower down. The melt is baked into one image off the main thread and cached by Nuke, so no
/// view applies a live blur or a mask.
nonisolated enum HomeBackdropArt {
    /// Width over height of the frame on a 390 pt phone (390 x 470).
    static let aspect: CGFloat = 390.0 / 470.0
    /// The size Nuke decodes, in points: about 960 x 1158 pixels at 3x, not the backdrop's full resolution.
    static let thumbnailSize = CGSize(width: 320, height: 386)
    /// Where, as a share of the height from the top, the blurred copy starts to show and where it is all there is.
    static let meltFrom: CGFloat = 0.34
    static let meltTo: CGFloat = 0.62
    /// The blur's Gaussian sigma as a share of the baked width (about 12 pt on 390).
    static let sigmaRatio: Double = 0.03
    /// The blurred copy is a little less colorful than the sharp image.
    static let saturation: Double = 0.92

    static var processors: [any ImageProcessing] { [HomeBackdropMeltProcessor()] }

    /// The request the picture loads and the prefetcher warms, so both share one cache entry.
    static func request(url: URL?) -> ImageRequest? {
        SpineImagePipeline.request(url: url, thumbnailSize: thumbnailSize, processors: processors)
    }
}

/// Crops the image to `HomeBackdropArt.aspect` around its center, then blends a blurred copy over the sharp one
/// through a vertical ramp: all sharp down to `meltFrom` of the height, all blurred from `meltTo`. The output is one
/// opaque bitmap.
nonisolated struct HomeBackdropMeltProcessor: ImageProcessing, Hashable {
    var identifier: String {
        "com.armaan.spine.homebackdrop?aspect=\(HomeBackdropArt.aspect)&from=\(HomeBackdropArt.meltFrom)"
            + "&to=\(HomeBackdropArt.meltTo)&sigma=\(HomeBackdropArt.sigmaRatio)&sat=\(HomeBackdropArt.saturation)"
    }

    /// The part of a `width` x `height` image that has the frame's shape, centered.
    static func cropRect(width: CGFloat, height: CGFloat) -> CGRect {
        var cropWidth = width
        var cropHeight = width / HomeBackdropArt.aspect
        if cropHeight > height {
            cropHeight = height
            cropWidth = height * HomeBackdropArt.aspect
        }
        cropWidth = max(1, cropWidth.rounded())
        cropHeight = max(1, cropHeight.rounded())
        return CGRect(
            x: ((width - cropWidth) / 2).rounded(),
            y: ((height - cropHeight) / 2).rounded(),
            width: cropWidth,
            height: cropHeight
        )
    }

    func process(_ image: PlatformImage) -> PlatformImage? {
        guard let source = image.cgImage else { return nil }
        let crop = Self.cropRect(width: CGFloat(source.width), height: CGFloat(source.height))
        let extent = CGRect(origin: .zero, size: crop.size)
        let sharp = CIImage(cgImage: source)
            .cropped(to: crop)
            .transformed(by: CGAffineTransform(translationX: -crop.minX, y: -crop.minY))

        // Clamping first extends the edge pixels outward, so the blur does not fade the borders to transparent.
        let blurred = sharp
            .clampedToExtent()
            .applyingGaussianBlur(sigma: max(Double(extent.width) * HomeBackdropArt.sigmaRatio, 0.01))
            .cropped(to: extent)
            .applyingFilter("CIColorControls", parameters: [kCIInputSaturationKey: HomeBackdropArt.saturation])

        // Core Image's y runs up: the top of the picture is at `extent.height`, and is where the ramp is clear.
        guard let ramp = CIFilter(name: "CISmoothLinearGradient", parameters: [
            "inputPoint0": CIVector(x: 0, y: extent.height * (1 - HomeBackdropArt.meltFrom)),
            "inputPoint1": CIVector(x: 0, y: extent.height * (1 - HomeBackdropArt.meltTo)),
            "inputColor0": CIColor(red: 0, green: 0, blue: 0, alpha: 0),
            "inputColor1": CIColor(red: 1, green: 1, blue: 1, alpha: 1),
        ])?.outputImage?.cropped(to: extent) else { return nil }

        let melted = blurred.applyingFilter("CIBlendWithAlphaMask", parameters: [
            kCIInputBackgroundImageKey: sharp,
            kCIInputMaskImageKey: ramp,
        ])
        guard let output = BlurredArt.context.createCGImage(
            melted,
            from: extent,
            format: .RGBA8,
            colorSpace: CGColorSpace(name: CGColorSpace.sRGB)
        ) else { return nil }
        return PlatformImage(cgImage: output, scale: image.scale, orientation: image.imageOrientation)
    }
}
