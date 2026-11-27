import CoreGraphics
import Nuke
import NukeUI
import PictureStrip
import SwiftUI
import UIKit
import Vision

/// A picture at the top of a page that starts under the status bar instead of behind it, with the strip behind the
/// status bar grown from the picture's own top (the PictureStrip package): the media page's and a list's backdrop, a
/// profile's backdrop and an episode's still. Home's picture does the same in its own bake (`HomeBackdropBand`).
/// A busy top gets no strip, and the page shows the picture exactly the way it always has.
nonisolated enum TopStrip {
    /// The strip's height as a share of the picture's width: taller than any iPhone's status bar plus the 6 pt these
    /// pictures reach above the screen (65 of 430 pt on a 14 Pro Max, 68 of 402 on a 17 Pro).
    static let share: CGFloat = 0.18

    /// The picture as a page shows it under the strip, `size` in points (decoded at that size), with the strip grown
    /// above it. Goes through `SpineImagePipeline.strips`, so it is made once and kept on disk.
    static func request(url: URL?, size: CGSize) -> ImageRequest? {
        guard size.width > 0, size.height > 0 else { return nil }
        return SpineImagePipeline.request(
            url: url,
            thumbnailSize: size,
            processors: [TopStripProcessor(aspect: size.width / size.height)]
        )
    }

    /// What a strip processor returns when there is no strip (a busy top): one pixel, cached like a strip, so the
    /// check isn't run again and the page knows at once to show the picture as it always has.
    static func noStrip(scale: CGFloat) -> PlatformImage? {
        guard let context = CGContext(
            data: nil, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
            space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }
        context.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard let pixel = context.makeImage() else { return nil }
        return PlatformImage(cgImage: pixel, scale: scale, orientation: .up)
    }

    static func isNoStrip(_ image: PlatformImage) -> Bool {
        (image.cgImage?.width ?? 0) <= 1
    }

    /// The faces in `picture`, each widened by a quarter on both sides and raised by half its height for the hair above
    /// it, in the picture's pixels (origin at the top left). The strip takes no patches from them: a piece of a face
    /// copied into the strip reads as a pasted face.
    static func faceAreas(in picture: CGImage) -> [CGRect] {
        let request = VNDetectFaceRectanglesRequest()
        guard (try? VNImageRequestHandler(cgImage: picture, options: [:]).perform([request])) != nil else { return [] }
        let width = CGFloat(picture.width)
        let height = CGFloat(picture.height)
        return (request.results ?? []).map { face in
            // Vision's box is a share of the picture, with its origin at the bottom left.
            let box = face.boundingBox
            return CGRect(
                x: (box.minX - box.width * 0.25) * width,
                y: (1 - box.maxY - box.height * 0.5) * height,
                width: box.width * 1.5 * width,
                height: box.height * 1.5 * height
            )
        }
    }
}

/// Crops the image to the page's shape (`aspect`, width over height) and grows `TopStrip.share` of its width above it:
/// the strip, then the picture, in one opaque bitmap. A busy top gives `TopStrip.noStrip`.
nonisolated struct TopStripProcessor: ImageProcessing, Hashable {
    /// Rounded, so the same page on the same phone always makes the same cache key.
    let aspect: CGFloat

    init(aspect: CGFloat) {
        self.aspect = (aspect * 1000).rounded() / 1000
    }

    var identifier: String {
        "com.armaan.spine.topstrip?aspect=\(aspect)&share=\(TopStrip.share)&quilt=\(PictureStrip.version)"
    }

    /// The part of a `width` x `height` image with `aspect`'s shape: centered across, and from the very top down, so the
    /// strip grows from the picture's own top edge. Cutting into the top instead puts heads and hats right where the
    /// strip starts; the bottom fades out under the page anyway.
    static func cropRect(width: CGFloat, height: CGFloat, aspect: CGFloat) -> CGRect {
        var cropWidth = width
        var cropHeight = width / aspect
        if cropHeight > height {
            cropHeight = height
            cropWidth = height * aspect
        }
        cropWidth = max(1, cropWidth.rounded())
        cropHeight = max(1, cropHeight.rounded())
        return CGRect(x: ((width - cropWidth) / 2).rounded(), y: 0, width: cropWidth, height: cropHeight)
    }

    func process(_ image: PlatformImage) -> PlatformImage? {
        guard let source = image.cgImage, aspect > 0 else { return nil }
        let crop = Self.cropRect(width: CGFloat(source.width), height: CGFloat(source.height), aspect: aspect)
        guard let picture = source.cropping(to: crop) else { return nil }
        let band = max(1, Int((CGFloat(picture.width) * TopStrip.share).rounded()))
        guard let grown = PictureStrip.grow(
            picture,
            band: band,
            sharpRows: picture.height,
            avoiding: TopStrip.faceAreas(in: picture)
        ) else {
            return TopStrip.noStrip(scale: image.scale)
        }
        return PlatformImage(cgImage: grown, scale: image.scale, orientation: image.imageOrientation)
    }
}

/// What a page's top picture has: still loading, a strip grown above it, or no strip (a busy top, or the strip
/// couldn't be made), in which case the page shows the picture the way it always has.
enum TopStripResult {
    case loading
    case strip(Image)
    case none
}

/// Loads a picture with its strip (through `SpineImagePipeline.strips`) and hands the page what it got.
struct TopStripLoader<Content: View>: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let request: ImageRequest?
    @ViewBuilder let content: (TopStripResult) -> Content

    var body: some View {
        LazyImage(
            request: request,
            transaction: Transaction(animation: SpineMotion.animation(reduceMotion: reduceMotion))
        ) { state in
            content(Self.result(image: state.image, container: state.imageContainer, failed: state.error != nil))
        }
        .pipeline(SpineImagePipeline.strips)
        // A new picture gets a fresh view, so the old and new pictures crossfade (as `SpineAsyncImage` does).
        .id(request?.url)
        .transition(.opacity)
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: request?.url)
    }

    static func result(image: Image?, container: ImageContainer?, failed: Bool) -> TopStripResult {
        if let container {
            if TopStrip.isNoStrip(container.image) { return .none }
            if let image { return .strip(image) }
        }
        return failed ? .none : .loading
    }
}

/// A picture with its strip in a frame of `size`: the picture from `pictureTop` to the frame's bottom, the strip above
/// it. The page passes the size it measured, so nothing here reads geometry.
struct TopStripImage: View {
    let image: Image
    let pictureTop: CGFloat
    let size: CGSize

    var body: some View {
        let band = size.width * TopStrip.share
        image
            .resizable()
            .frame(width: size.width, height: max(0, size.height - pictureTop) + band)
            .offset(y: pictureTop - band)
            .frame(width: size.width, height: size.height, alignment: .top)
            .clipped()
    }
}
