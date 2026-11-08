import CoreImage
import Foundation
import Nuke
import UIKit

/// The picture at the top of Home: a title's backdrop framed as the media page frames it (the whole width, cropped to
/// 3:2, sharp), dissolving into a smooth wash of its own colors below it, where Home's header and the tiles sit. Baked
/// into one image off the main thread and cached by Nuke, so no view applies a live blur or a mask.
nonisolated enum HomeBackdropArt {
    /// Width over height of the whole frame on a 390 pt phone (390 x 470): the strip and the wash below it.
    static let aspect: CGFloat = 390.0 / 470.0
    /// The media page's strip: width over height.
    static let stripAspect: CGFloat = BackdropLayout.stripCropAspectRatio
    /// The size Nuke decodes, in points (3:2, about 1260 x 840 pixels at 3x), not the backdrop's full resolution.
    static let thumbnailSize = CGSize(width: 420, height: 280)
    /// How much of the strip, from its bottom up, dissolves into the wash.
    static let fade: CGFloat = 0.4
    /// How soft the wash is: its Gaussian sigma as a share of the width. Large enough that it is only color, with no
    /// shapes or streaks left in it.
    static let wash: Double = 0.22
    /// The wash is a little less colorful than the picture.
    static let washSaturation: Double = 0.9
    /// The wash is made at this share of the size, then scaled back up smoothly: the same look for far less work.
    static let washScale: CGFloat = 0.125

    static var processors: [any ImageProcessing] { [HomeBackdropMeltProcessor()] }

    /// The request the picture loads and the prefetcher warms, so both share one cache entry.
    static func request(url: URL?) -> ImageRequest? {
        SpineImagePipeline.request(url: url, thumbnailSize: thumbnailSize, processors: processors)
    }
}

/// Crops the image to the media page's 3:2 strip around its center and puts it at the top of a frame of
/// `HomeBackdropArt.aspect`. Under it is a wash: the strip, its edges carried on outward, blurred until only its colors
/// are left. The strip's lower `fade` dissolves smoothly into that wash. The output is one opaque bitmap.
nonisolated struct HomeBackdropMeltProcessor: ImageProcessing, Hashable {
    var identifier: String {
        "com.armaan.spine.homebackdrop?aspect=\(HomeBackdropArt.aspect)&strip=\(HomeBackdropArt.stripAspect)"
            + "&fade=\(HomeBackdropArt.fade)&wash=\(HomeBackdropArt.wash)&washsat=\(HomeBackdropArt.washSaturation)"
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

        // The wash, made small and scaled back up: the strip with its edges carried on outward, blurred until only its
        // colors are left, so the frame below the strip is a smooth field of the picture's own colors.
        // The small copy is whole pixels, so its edge pixels are solid and stay solid when carried outward.
        let small = CGRect(
            x: 0, y: 0,
            width: max(1, (frame.width * HomeBackdropArt.washScale).rounded(.down)),
            height: max(1, (frame.height * HomeBackdropArt.washScale).rounded(.down))
        )
        let wash = sharp
            .clampedToExtent()
            .transformed(by: CGAffineTransform(scaleX: small.width / frame.width, y: small.height / frame.height))
            .applyingGaussianBlur(sigma: max(Double(small.width) * HomeBackdropArt.wash, 0.01))
            .cropped(to: small)
            .clampedToExtent()
            .samplingLinear()
            .transformed(by: CGAffineTransform(scaleX: frame.width / small.width, y: frame.height / small.height))
            .cropped(to: extent)
            .applyingFilter("CIColorControls", parameters: [kCIInputSaturationKey: HomeBackdropArt.washSaturation])

        // Clear (the picture) above the strip's lower `fade`, solid (the wash) from the strip's bottom edge down.
        guard let ramp = CIFilter(name: "CISmoothLinearGradient", parameters: [
            "inputPoint0": CIVector(x: 0, y: stripBottom + crop.height * HomeBackdropArt.fade),
            "inputPoint1": CIVector(x: 0, y: stripBottom),
            "inputColor0": CIColor(red: 0, green: 0, blue: 0, alpha: 0),
            "inputColor1": CIColor(red: 1, green: 1, blue: 1, alpha: 1),
        ])?.outputImage?.cropped(to: extent) else { return nil }

        let blended = wash.applyingFilter("CIBlendWithAlphaMask", parameters: [
            kCIInputBackgroundImageKey: sharp.composited(over: wash),
            kCIInputMaskImageKey: ramp,
        ])
        guard let output = BlurredArt.context.createCGImage(
            blended,
            from: extent,
            format: .RGBA8,
            colorSpace: CGColorSpace(name: CGColorSpace.sRGB)
        ) else { return nil }
        return PlatformImage(cgImage: output, scale: image.scale, orientation: image.imageOrientation)
    }
}
