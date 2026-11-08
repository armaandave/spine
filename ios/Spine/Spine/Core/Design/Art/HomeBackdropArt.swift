import CoreImage
import Foundation
import Nuke
import UIKit

/// The picture at the top of Home: a title's backdrop framed as the media page frames it (the whole width, cropped to
/// 3:2, all sharp), and under it a soft blurred continuation of its bottom edge that runs on behind Home's header and
/// the tiles. Baked into one image off the main thread and cached by Nuke, so no view applies a live blur or a mask.
nonisolated enum HomeBackdropArt {
    /// Width over height of the whole picture on a 390 pt phone (390 x 470): the strip and its continuation.
    static let aspect: CGFloat = 390.0 / 470.0
    /// The media page's strip: width over height.
    static let stripAspect: CGFloat = BackdropLayout.stripCropAspectRatio
    /// The size Nuke decodes, in points (3:2, about 1260 x 840 pixels at 3x), not the backdrop's full resolution.
    static let thumbnailSize = CGSize(width: 420, height: 280)
    /// Where, as a share of the strip's height, its bottom starts to soften into the continuation.
    static let meltStart: CGFloat = 0.8
    /// The continuation's Gaussian sigma as a share of the baked width (about 23 pt on 390).
    static let sigmaRatio: Double = 0.06
    /// The continuation is a little less colorful than the picture.
    static let saturation: Double = 0.92

    static var processors: [any ImageProcessing] { [HomeBackdropMeltProcessor()] }

    /// The request the picture loads and the prefetcher warms, so both share one cache entry.
    static func request(url: URL?) -> ImageRequest? {
        SpineImagePipeline.request(url: url, thumbnailSize: thumbnailSize, processors: processors)
    }
}

/// Crops the image to the media page's 3:2 strip around its center and puts it at the top of a frame of
/// `HomeBackdropArt.aspect`. The rest of the frame below is the strip's bottom edge stretched down and blurred, and the
/// strip's last fifth melts into it. The output is one opaque bitmap.
nonisolated struct HomeBackdropMeltProcessor: ImageProcessing, Hashable {
    var identifier: String {
        "com.armaan.spine.homebackdrop?aspect=\(HomeBackdropArt.aspect)&strip=\(HomeBackdropArt.stripAspect)"
            + "&melt=\(HomeBackdropArt.meltStart)&sigma=\(HomeBackdropArt.sigmaRatio)&sat=\(HomeBackdropArt.saturation)"
    }

    /// The part of a `width` x `height` image with the strip's shape, centered.
    static func cropRect(width: CGFloat, height: CGFloat) -> CGRect {
        var cropWidth = width
        var cropHeight = width / HomeBackdropArt.stripAspect
        if cropHeight > height {
            cropHeight = height
            cropWidth = height * HomeBackdropArt.stripAspect
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

    /// The whole picture's size for a strip `stripWidth` wide.
    static func frameSize(stripWidth: CGFloat) -> CGSize {
        CGSize(width: stripWidth, height: max(1, (stripWidth / HomeBackdropArt.aspect).rounded()))
    }

    func process(_ image: PlatformImage) -> PlatformImage? {
        guard let source = image.cgImage else { return nil }
        let crop = Self.cropRect(width: CGFloat(source.width), height: CGFloat(source.height))
        let frame = Self.frameSize(stripWidth: crop.width)
        let extent = CGRect(origin: .zero, size: frame)
        // Core Image's y runs up: the strip goes at the top of the frame, from `frame.height - crop.height` up.
        let stripBottom = frame.height - crop.height
        let sharp = CIImage(cgImage: source)
            .cropped(to: crop)
            .transformed(by: CGAffineTransform(translationX: -crop.minX, y: stripBottom - crop.minY))

        // Clamping repeats the strip's edge rows outward, so below it the bottom edge runs on down the frame; the blur
        // then softens that into one continuous tone.
        let continuation = sharp
            .clampedToExtent()
            .applyingGaussianBlur(sigma: max(Double(frame.width) * HomeBackdropArt.sigmaRatio, 0.01))
            .cropped(to: extent)
            .applyingFilter("CIColorControls", parameters: [kCIInputSaturationKey: HomeBackdropArt.saturation])

        // Clear (the sharp strip) down to `meltStart` of the strip, solid (the continuation) from its bottom edge.
        guard let ramp = CIFilter(name: "CISmoothLinearGradient", parameters: [
            "inputPoint0": CIVector(x: 0, y: frame.height - crop.height * HomeBackdropArt.meltStart),
            "inputPoint1": CIVector(x: 0, y: stripBottom),
            "inputColor0": CIColor(red: 0, green: 0, blue: 0, alpha: 0),
            "inputColor1": CIColor(red: 1, green: 1, blue: 1, alpha: 1),
        ])?.outputImage?.cropped(to: extent) else { return nil }

        let melted = continuation.applyingFilter("CIBlendWithAlphaMask", parameters: [
            kCIInputBackgroundImageKey: sharp.clampedToExtent().cropped(to: extent),
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
