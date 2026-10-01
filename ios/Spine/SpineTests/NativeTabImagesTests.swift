import UIKit
import XCTest
@testable import Spine

@MainActor
final class NativeTabImagesTests: XCTestCase {
    func testImageConfigurationPreservesSelectionDelegate() {
        final class SelectionDelegate: NSObject, UITabBarControllerDelegate {}
        let delegate = SelectionDelegate()
        let tabController = UITabBarController()
        tabController.delegate = delegate
        let tabs = (0..<5).map { index in
            let controller = UIViewController()
            controller.tabBarItem = UITabBarItem(title: "Tab \(index)", image: nil, tag: index)
            return controller
        }
        tabController.viewControllers = tabs

        for (index, tab) in tabs.enumerated() {
            let configurator = TabBarImageConfigurator.ImageViewController()
            tab.addChild(configurator)
            configurator.didMove(toParent: tab)
            tabController.selectedIndex = index
            configurator.viewDidAppear(false)
            configurator.configureImages()
            XCTAssertTrue(tabController.delegate === delegate)
            XCTAssertEqual(tabController.selectedIndex, index)
            XCTAssertNotNil(tabs[0].tabBarItem.selectedImage)
        }
    }

    func testPairsAndAvatarRemainStableAcrossSelectionUpdates() throws {
        let names = ["house", "magnifyingglass", "books.vertical", "calendar", "person.crop.circle"]
        let items = names.map { UITabBarItem(title: $0, image: UIImage(systemName: $0), selectedImage: nil) }
        let avatar = try XCTUnwrap(UIImage(systemName: "person.crop.circle"))
            .withRenderingMode(.alwaysOriginal)
        items[4].image = avatar

        NativeTabImages.configure(items: items)

        XCTAssertEqual(items[0].selectedImage?.pngData(), UIImage(systemName: "house.fill")?.pngData())
        XCTAssertEqual(items[1].selectedImage?.renderingMode, .alwaysTemplate)
        XCTAssertNotNil(items[1].selectedImage)
        XCTAssertEqual(items[2].selectedImage?.pngData(), UIImage(systemName: "books.vertical.fill")?.pngData())
        XCTAssertTrue(items[3].selectedImage === items[3].image)
        XCTAssertTrue(items[4].selectedImage === avatar)
        let images = items.map(\.image)
        let selectedImages = items.map { $0.selectedImage?.pngData() }

        NativeTabImages.configure(items: items)

        for index in items.indices {
            XCTAssertTrue(items[index].image === images[index])
            XCTAssertEqual(items[index].selectedImage?.pngData(), selectedImages[index])
            XCTAssertEqual(items[index].title, names[index])
        }
    }
}
