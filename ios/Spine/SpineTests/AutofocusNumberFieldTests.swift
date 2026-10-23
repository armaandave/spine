import UIKit
import XCTest
@testable import Spine

@MainActor
final class AutofocusNumberFieldTests: XCTestCase {
    func testDigitsScaleOnceForDynamicTypeLikeTheUnitBesideThem() {
        let categories: [UIContentSizeCategory] = [.small, .large, .extraExtraLarge, .accessibilityLarge]
        for category in categories {
            let traits = UITraitCollection(preferredContentSizeCategory: category)
            let unit = UIFont.preferredFont(forTextStyle: .title3, compatibleWith: traits)
            XCTAssertEqual(AutofocusNumberField.font(for: category).pointSize, unit.pointSize, category.rawValue)
        }
    }
}
