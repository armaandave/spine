import UIKit
import XCTest
@testable import Spine

final class ProfileTabImageTests: XCTestCase {
    func testAvatarCreatesFixedOriginalImage() throws {
        let data = try XCTUnwrap(
            UIGraphicsImageRenderer(size: CGSize(width: 60, height: 30))
                .image { context in
                    context.cgContext.setFillColor(UIColor.red.cgColor)
                    context.cgContext.fill(CGRect(x: 0, y: 0, width: 60, height: 30))
                }
                .pngData()
        )

        let image = try XCTUnwrap(ProfileTabImage.avatar(from: data, scale: 3))

        XCTAssertEqual(image.size, CGSize(width: 30, height: 30))
        XCTAssertEqual(image.renderingMode, .alwaysOriginal)
        XCTAssertEqual(ProfileTabImage.fallback.renderingMode, .alwaysTemplate)
    }
}
