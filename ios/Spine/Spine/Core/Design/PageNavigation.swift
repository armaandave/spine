import SwiftUI
import UIKit

/// What a page is, which decides its bottom bar and whether the floating Home and Search buttons show.
enum PageKind {
    /// A tab's first page.
    case root
    /// A title, person, company, series, song or genre page. Exploring starts here: this page and every page opened
    /// after it hide the bottom bar, and the floating Home and Search buttons are the way out.
    case exploring
    /// A log's or a day's progress page: its comment box takes the bottom, so the bar stays hidden. The pages it opens
    /// are opened while exploring.
    case post
    /// Any other page: the bottom bar shows, unless the page was opened while exploring.
    case page
}

extension View {
    /// Sets a page up: its bottom bar, Apple's swipe back, the pages its links open (`pushDestination`), and the
    /// floating Home and Search buttons while exploring. Apply it last, outside the page's `pushDestination`s, so the
    /// pages they open are opened while exploring too. `allowsSwipeBack` is false while something over the page
    /// (a poster opened full screen) takes swipes itself.
    ///
    /// On a tab's first page (`.root`), tapping the tab again goes back to that page; tapped again there, the page
    /// scrolls to its top and `onTabReselect` runs (Search puts the cursor in its search box).
    func navigationPage(
        _ kind: PageKind,
        showsExplorationButtons: Bool = true,
        allowsSwipeBack: Bool = true,
        onTabReselect: (() -> Void)? = nil
    ) -> some View {
        modifier(NavigationPageModifier(
            kind: kind,
            showsExplorationButtons: showsExplorationButtons,
            allowsSwipeBack: allowsSwipeBack,
            onTabReselect: onTabReselect
        ))
    }

    /// Opens a page on the navigation stack while `item` is set, the way `fullScreenCover(item:)` opens a cover.
    /// Going back clears `item` and runs `onDismiss`; clearing `item` goes back. Works inside lazy rows and pagers,
    /// where `navigationDestination` can't be placed, because the page's `navigationPage` does the opening.
    func pushDestination<Item: Identifiable, Destination: View>(
        item: Binding<Item?>,
        onDismiss: (() -> Void)? = nil,
        @ViewBuilder destination: @escaping (Item) -> Destination
    ) -> some View {
        modifier(PushDestinationModifier(item: item, onDismiss: onDismiss, destination: destination))
    }

    /// Opens a page on the navigation stack while `isPresented` is true.
    func pushDestination<Destination: View>(
        isPresented: Binding<Bool>,
        onDismiss: (() -> Void)? = nil,
        @ViewBuilder destination: @escaping () -> Destination
    ) -> some View {
        pushDestination(
            item: Binding(
                get: { isPresented.wrappedValue ? PresentedFlag() : nil },
                set: { isPresented.wrappedValue = $0 != nil }
            ),
            onDismiss: onDismiss
        ) { _ in
            destination()
        }
    }
}

/// The back button in the header of a page that draws its own header where the navigation bar would be.
struct PageHeaderBackButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 42, height: 42)
                .background(.white.opacity(0.06), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Back")
    }
}

extension EnvironmentValues {
    /// True on a page opened while exploring (and on the exploring page itself).
    var isExploring: Bool {
        get { self[IsExploringKey.self] }
        set { self[IsExploringKey.self] = newValue }
    }

    fileprivate var pageStack: PageStack? {
        get { self[PageStackKey.self] }
        set { self[PageStackKey.self] = newValue }
    }
}

private struct IsExploringKey: EnvironmentKey {
    static let defaultValue = false
}

private struct PageStackKey: EnvironmentKey {
    static let defaultValue: PageStack? = nil
}

private struct PresentedFlag: Identifiable {
    var id: Int { 0 }
}

private struct NavigationPageModifier: ViewModifier {
    @Environment(\.isExploring) private var openedWhileExploring
    @Environment(\.appNavigationState) private var appNavigationState
    @State private var stack = PageStack()
    @State private var page = PageController()

    let kind: PageKind
    let showsExplorationButtons: Bool
    let allowsSwipeBack: Bool
    let onTabReselect: (() -> Void)?

    func body(content: Content) -> some View {
        let isExploring = kind == .exploring || (kind != .root && openedWhileExploring)
        let hidesBottomBar = kind == .exploring || kind == .post || isExploring
        content
            .environment(\.pageStack, stack)
            .navigationDestination(item: $stack.pushed) { page in
                page.content
            }
            .onChange(of: stack.pushed) { oldValue, newValue in
                if let oldValue, oldValue.id != newValue?.id {
                    oldValue.onPop()
                }
            }
            .overlay(alignment: .bottomLeading) {
                if showsButtons(isExploring), appNavigationState?.selectedTab == .search {
                    ExplorationSearchButton()
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                }
            }
            .overlay(alignment: .bottomTrailing) {
                if showsButtons(isExploring) {
                    ExplorationHomeButton()
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                }
            }
            .background {
                SwipeBackEnabler(allowsSwipeBack: allowsSwipeBack, hidesBottomBar: hidesBottomBar, page: page)
                    .frame(width: 0, height: 0)
                    .accessibilityHidden(true)
            }
            // The Home and Search buttons close the trail from its first page.
            .onChange(of: appNavigationState?.returnHomeRequest) { _, request in
                if request != nil, startsTrail {
                    page.closeTrail(animated: appNavigationState?.selectedTab == .home)
                }
            }
            .onChange(of: appNavigationState?.returnToSearchRequest) { _, request in
                if request != nil, startsTrail {
                    page.closeTrail(animated: true)
                }
            }
            .onChange(of: appNavigationState?.tabReselectRequest) { _, request in
                if request != nil, kind == .root {
                    page.reselectTab(atFirstPage: onTabReselect)
                }
            }
            .environment(\.isExploring, isExploring || kind == .post)
    }

    /// The trail's first page: an exploring or post page that wasn't opened while exploring.
    private var startsTrail: Bool {
        (kind == .exploring || kind == .post) && !openedWhileExploring
    }

    private func showsButtons(_ isExploring: Bool) -> Bool {
        showsExplorationButtons && isExploring && kind != .post && kind != .root
    }
}

/// The page's own place on its navigation stack, for closing a trail.
private final class PageController {
    weak var controller: SwipeBackEnabler.Controller?

    /// Goes back to the page before this one, closing everything opened from it, the way the back button's long-press
    /// menu does.
    func closeTrail(animated: Bool) {
        guard let controller, let navigationController = controller.navigationController else { return }
        var pageController: UIViewController? = controller
        while let current = pageController, current.parent !== navigationController {
            pageController = current.parent
        }
        guard let pageController,
              let index = navigationController.viewControllers.firstIndex(of: pageController),
              index > 0
        else { return }
        navigationController.popToViewController(navigationController.viewControllers[index - 1], animated: animated)
    }

    /// The tab this first page belongs to was tapped while on screen: back to this page, or, already here, up to its
    /// top. Other tabs' first pages hear the same tap and do nothing.
    func reselectTab(atFirstPage: (() -> Void)?) {
        guard let controller,
              let tabBarController = controller.tabBarController,
              let selected = tabBarController.selectedViewController,
              Self.isInside(controller, selected),
              let navigationController = controller.navigationController
        else { return }
        if navigationController.viewControllers.count > 1 {
            navigationController.popToRootViewController(animated: true)
            return
        }
        if let scrollView = Self.pageScrollView(in: navigationController.viewControllers.first?.view) {
            let top = CGPoint(x: scrollView.contentOffset.x, y: -scrollView.adjustedContentInset.top)
            scrollView.setContentOffset(top, animated: true)
        }
        atFirstPage?()
    }

    private static func isInside(_ controller: UIViewController, _ ancestor: UIViewController) -> Bool {
        var current: UIViewController? = controller
        while let candidate = current {
            if candidate === ancestor { return true }
            current = candidate.parent
        }
        return false
    }

    /// The page's main scroll view: the largest one on screen that scrolls up and down (not a sideways row, and not
    /// one the page keeps faded out, like the Diary's month index).
    private static func pageScrollView(in view: UIView?) -> UIScrollView? {
        guard let view else { return nil }
        var found: [UIScrollView] = []
        var queue = [view]
        var index = 0
        while index < queue.count {
            let next = queue[index]
            index += 1
            if let scrollView = next as? UIScrollView,
               scrollView.contentSize.height + scrollView.adjustedContentInset.top + scrollView.adjustedContentInset.bottom
                > scrollView.bounds.height + 1,
               isOnScreen(scrollView) {
                found.append(scrollView)
            }
            queue.append(contentsOf: next.subviews)
        }
        return found.max { $0.bounds.width * $0.bounds.height < $1.bounds.width * $1.bounds.height }
    }

    private static func isOnScreen(_ view: UIView) -> Bool {
        var current: UIView? = view
        while let candidate = current {
            if candidate.isHidden || candidate.alpha < 0.01 || candidate.layer.opacity < 0.01 { return false }
            current = candidate.superview
        }
        return view.window != nil
    }
}

/// The page this page has opened on the navigation stack, if any. One per page.
@Observable
private final class PageStack {
    var pushed: PushedPage?
}

private struct PushedPage: Hashable {
    let id: UUID
    let content: AnyView
    let onPop: () -> Void

    static func == (lhs: PushedPage, rhs: PushedPage) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}

private struct PushDestinationModifier<Item: Identifiable, Destination: View>: ViewModifier {
    @Environment(\.pageStack) private var stack
    @Binding var item: Item?
    @State private var pushedID: UUID?

    let onDismiss: (() -> Void)?
    let destination: (Item) -> Destination

    func body(content: Content) -> some View {
        content
            .onChange(of: item?.id) {
                sync()
            }
    }

    private func sync() {
        if let item {
            let id = UUID()
            pushedID = id
            stack?.pushed = PushedPage(id: id, content: AnyView(destination(item))) {
                // Back, a swipe or `dismiss()` took the page off the stack.
                guard pushedID == id else { return }
                pushedID = nil
                self.item = nil
                onDismiss?()
            }
        } else if let pushedID, stack?.pushed?.id == pushedID {
            stack?.pushed = nil
        }
    }
}

// MARK: - Apple's swipe back

/// Turns Apple's swipe back on for the page's navigation stack, and shows or hides the tab's bottom bar as the page
/// comes on screen. Our pages hide the back button or the whole navigation bar to draw their own over the artwork, and
/// iOS turns its swipes off when either is hidden.
///
/// The bar is set on the tab bar controller directly, by whichever page is coming on screen (at the start of a push,
/// or of a swipe back, and again if the swipe is let go). SwiftUI's `.toolbar(_:for: .tabBar)` was tried and dropped:
/// a page saying `.visible` showed the bar over every page above it, and pages taken off together left a tab's bar
/// hidden for good.
private struct SwipeBackEnabler: UIViewControllerRepresentable {
    let allowsSwipeBack: Bool
    let hidesBottomBar: Bool
    let page: PageController

    func makeUIViewController(context: Context) -> Controller {
        let controller = Controller()
        page.controller = controller
        return controller
    }

    func updateUIViewController(_ controller: Controller, context: Context) {
        controller.allowsSwipeBack = allowsSwipeBack
        controller.hidesBottomBar = hidesBottomBar
    }

    final class Controller: UIViewController {
        var allowsSwipeBack = true
        var hidesBottomBar = false

        override func didMove(toParent parent: UIViewController?) {
            super.didMove(toParent: parent)
            install()
        }

        override func viewWillAppear(_ animated: Bool) {
            super.viewWillAppear(animated)
            guard let tabBarController, tabBarController.isTabBarHidden != hidesBottomBar else { return }
            tabBarController.setTabBarHidden(hidesBottomBar, animated: animated)
        }

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            install()
        }

        private func install() {
            guard let navigationController else { return }
            SwipeBack.install(on: navigationController)
        }
    }
}

private enum SwipeBack {
    /// Keeps each stack's delegates alive (gesture recognizers hold their delegates weakly).
    private static let delegates = NSMapTable<UIGestureRecognizer, Delegate>(
        keyOptions: .weakMemory,
        valueOptions: .strongMemory
    )

    static func install(on navigationController: UINavigationController) {
        // The swipe from the edge, and iOS 26's swipe from anywhere on the page.
        let gestures = [
            navigationController.interactivePopGestureRecognizer,
            navigationController.interactiveContentPopGestureRecognizer,
        ]
        for case let gesture? in gestures where !(gesture.delegate is Delegate) {
            let delegate = Delegate(navigationController: navigationController, original: gesture.delegate)
            delegates.setObject(delegate, forKey: gesture)
            gesture.delegate = delegate
            gesture.isEnabled = true
        }
    }

    /// Stands in for Apple's own delegate, which turns the swipes away whenever the bar or its back button is hidden.
    /// It still asks Apple's delegate whether to begin, so the swipe behaves as Apple's wherever Apple allows it.
    final class Delegate: NSObject, UIGestureRecognizerDelegate {
        private weak var navigationController: UINavigationController?
        private weak var original: UIGestureRecognizerDelegate?

        init(navigationController: UINavigationController, original: UIGestureRecognizerDelegate?) {
            self.navigationController = navigationController
            self.original = original
        }

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            // Never on a stack's first page, while a page is still sliding, or while the page on top says no.
            guard let navigationController,
                  navigationController.viewControllers.count > 1,
                  navigationController.transitionCoordinator == nil,
                  let top = navigationController.topViewController,
                  !Self.blocksSwipeBack(top)
            else { return false }
            if original?.gestureRecognizerShouldBegin?(gestureRecognizer) == true {
                return true
            }
            if let pan = gestureRecognizer as? UIPanGestureRecognizer,
               !(gestureRecognizer is UIScreenEdgePanGestureRecognizer) {
                // From anywhere on the page: only a swipe to the right, and not one that scrolls a row back.
                let velocity = pan.velocity(in: pan.view)
                guard velocity.x > 0, velocity.x > abs(velocity.y) else { return false }
                if Self.isOverRowThatScrollsBack(pan) { return false }
            }
            return true
        }

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            original?.gestureRecognizer?(gestureRecognizer, shouldRecognizeSimultaneouslyWith: otherGestureRecognizer)
                ?? false
        }

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRequireFailureOf otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            original?.gestureRecognizer?(gestureRecognizer, shouldRequireFailureOf: otherGestureRecognizer)
                ?? false
        }

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldBeRequiredToFailBy otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            // A swipe from the very edge always goes back: a sideways row or the title pager waits for it to fail
            // (it fails at once for a touch that didn't start at the edge).
            if gestureRecognizer is UIScreenEdgePanGestureRecognizer, otherGestureRecognizer.view is UIScrollView {
                return true
            }
            return original?.gestureRecognizer?(gestureRecognizer, shouldBeRequiredToFailBy: otherGestureRecognizer)
                ?? false
        }

        /// True when the page on top asked for no swipe back (its enabler sits among its child controllers).
        private static func blocksSwipeBack(_ controller: UIViewController) -> Bool {
            if let enabler = controller as? SwipeBackEnabler.Controller, !enabler.allowsSwipeBack {
                return true
            }
            return controller.children.contains(where: blocksSwipeBack)
        }

        /// True when the touch is on a sideways row (a carousel, the title pager) that isn't at its start, so the
        /// swipe scrolls it back instead of leaving the page.
        private static func isOverRowThatScrollsBack(_ pan: UIPanGestureRecognizer) -> Bool {
            guard let view = pan.view else { return false }
            var current = view.hitTest(pan.location(in: view), with: nil)
            while let candidate = current, candidate !== view {
                if let scrollView = candidate as? UIScrollView,
                   scrollView.contentSize.width > scrollView.bounds.width + 1,
                   scrollView.contentOffset.x > -scrollView.adjustedContentInset.left + 1 {
                    return true
                }
                current = candidate.superview
            }
            return false
        }
    }
}
