import CoreGraphics

enum BackdropLayout {
    static let topOffset: CGFloat = -6
    /// The media page's backdrop strip: as wide as the screen, cropped to 3:2, and no taller than this
    /// below the safe area. List headers use the same strip, so a backdrop looks the same on both.
    static let stripHeight: CGFloat = 352.34375
    static let stripCropAspectRatio: CGFloat = 1.5

    static func safeAreaCompensation(for topInset: CGFloat) -> CGFloat {
        topInset + topOffset
    }

    static func stripHeight(width: CGFloat, topInset: CGFloat) -> CGFloat {
        min(topInset + stripHeight, width / stripCropAspectRatio)
    }
}
