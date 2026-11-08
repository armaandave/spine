import CoreImage
import Foundation
import Nuke
import UIKit

/// The picture at the top of Home: a title's backdrop framed as the media page frames it (the whole width, cropped to
/// 3:2, sharp), fading to the page's black over its lower part, with black below it where Home's header and the tiles
/// sit. Baked into one image off the main thread and cached by Nuke, so no view applies a live mask.
nonisolated enum HomeBackdropArt {
    /// Width over height of the whole frame on a 390 pt phone (390 x 470): the strip and the black below it.
    static let aspect: CGFloat = 390.0 / 470.0
    /// The media page's strip: width over height.
    static let stripAspect: CGFloat = BackdropLayout.stripCropAspectRatio
    /// The size Nuke decodes, in points (3:2, about 1260 x 840 pixels at 3x), not the backdrop's full resolution.
    static let thumbnailSize = CGSize(width: 420, height: 280)
    /// How much of the strip, from its bottom up, fades to black.
    static let fade: CGFloat = 0.35

    static var processors: [any ImageProcessing] { [HomeBackdropMeltProcessor()] }

    /// The request the picture loads and the prefetcher warms, so both share one cache entry.
    static func request(url: URL?) -> ImageRequest? {
        SpineImagePipeline.request(url: url, thumbnailSize: thumbnailSize, processors: processors)
    }
}

/// Crops the image to the media page's 3:2 strip around its center, puts it at the top of a frame of
/// `HomeBackdropArt.aspect`, and fades its lower `HomeBackdropArt.fade` smoothly to black; the frame below the strip is
/// black. The output is one opaque bitmap.
nonisolated struct HomeBackdropMeltProcessor: ImageProcessing, Hashable {
    var identifier: String {
        "com.armaan.spine.homebackdrop?aspect=\(HomeBackdropArt.aspect)&strip=\(HomeBackdropArt.stripAspect)"
            + "&fade=\(HomeBackdropArt.fade)"
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

    /// The whole frame's size for a strip `stripWidth` wide.
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
        let black = CIImage(color: CIColor(red: 0, green: 0, blue: 0, alpha: 1)).cropped(to: extent)

        // Clear (the picture) above the fade, solid (black) from the strip's bottom edge down.
        guard let ramp = CIFilter(name: "CISmoothLinearGradient", parameters: [
            "inputPoint0": CIVector(x: 0, y: stripBottom + crop.height * HomeBackdropArt.fade),
            "inputPoint1": CIVector(x: 0, y: stripBottom),
            "inputColor0": CIColor(red: 0, green: 0, blue: 0, alpha: 0),
            "inputColor1": CIColor(red: 1, green: 1, blue: 1, alpha: 1),
        ])?.outputImage?.cropped(to: extent) else { return nil }

        let faded = black.applyingFilter("CIBlendWithAlphaMask", parameters: [
            kCIInputBackgroundImageKey: sharp.composited(over: black),
            kCIInputMaskImageKey: ramp,
        ])
        guard let output = BlurredArt.context.createCGImage(
            faded,
            from: extent,
            format: .RGBA8,
            colorSpace: CGColorSpace(name: CGColorSpace.sRGB)
        ) else { return nil }
        return PlatformImage(cgImage: output, scale: image.scale, orientation: image.imageOrientation)
    }
}
