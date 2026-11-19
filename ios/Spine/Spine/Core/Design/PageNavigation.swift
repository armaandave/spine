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
    /// floating Home and Search buttons while exploring. Apply it last, outside the page's `pushDestination`s, which
    /// open their pages through it. `allowsSwipeBack` is false while something over the page (a poster opened full
    /// screen) takes swipes itself.
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
    /// Goes back to the page before the trail the page is on, in one step, with a slide or at once. The floating Home
    /// and Search buttons do this first, from the page they're on.
    var closeTrail: (_ animated: Bool) -> Void {
        get { self[CloseTrailKey.self] }
        set { self[CloseTrailKey.self] = newValue }
    }

    fileprivate var pageStack: PageStack? {
        get { self[PageStackKey.self] }
        set { self[PageStackKey.self] = newValue }
    }
}

private struct CloseTrailKey: EnvironmentKey {
    static let defaultValue: (_ animated: Bool) -> Void = { _ in }
}

private struct PageStackKey: EnvironmentKey {
    static let defaultValue: PageStack? = nil
}

private struct PresentedFlag: Identifiable {
    var id: Int { 0 }
}

private struct NavigationPageModifier: ViewModifier {
    @Environment(\.appNavigationState) private var appNavigationState
    @State private var stack = PageStack()
    @State private var page = PageController()

    let kind: PageKind
    let showsExplorationButtons: Bool
    let allowsSwipeBack: Bool
    let onTabReselect: (() -> Void)?

    func body(content: Content) -> some View {
        // Exploring pages, and other pages opened inside a trail, have the floating Home and Search buttons.
        let showsButtons = showsExplorationButtons
            && (kind == .exploring || (kind == .page && page.openedWhileExploring))
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
                if showsButtons, appNavigationState?.selectedTab == .search {
                    ExplorationSearchButton()
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                }
            }
            .overlay(alignment: .bottomTrailing) {
                if showsButtons {
                    ExplorationHomeButton()
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                }
            }
            .background {
                SwipeBackEnabler(kind: kind, allowsSwipeBack: allowsSwipeBack, page: page)
                    .frame(width: 0, height: 0)
                    .accessibilityHidden(true)
            }
            .onChange(of: appNavigationState?.tabReselectRequest) { _, request in
                if request != nil, kind == .root {
                    page.reselectTab(atFirstPage: onTabReselect)
                }
            }
            .environment(\.closeTrail, page.closeTrail(animated:))
    }
}

/// The page's own place on its navigation stack, for its bar, its buttons and closing a trail.
@Observable
private final class PageController {
    @ObservationIgnored weak var controller: SwipeBackEnabler.Controller?

    /// A page before this one on its stack is an exploring or post page, so this one is inside that trail. Worked out
    /// from the stack as the page comes on screen: SwiftUI gives a pushed page the surroundings of its stack's first
    /// page, not of the page that opened it.
    var openedWhileExploring = false

    /// Goes back to the page before the trail's first page, closing every page opened from it in one step, the way the
    /// back button's long-press menu does.
    func closeTrail(animated: Bool) {
        guard let navigationController = controller?.navigationController,
              let start = SwipeBackEnabler.Controller.trailStart(in: navigationController),
              start > 0
        else { return }
        navigationController.popToViewController(navigationController.viewControllers[start - 1], animated: animated)
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

    static func isInside(_ controller: UIViewController, _ ancestor: UIViewController) -> Bool {
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
/// The bar is set on the tab bar controller directly, by whichever page is coming on screen in the tab on screen (at
/// the start of a push, or of a swipe back, and again if the swipe is let go). SwiftUI's `.toolbar(_:for: .tabBar)`
/// was tried and dropped: a page saying `.visible` showed the bar over every page above it, and pages taken off
/// together left a tab's bar hidden for good.
private struct SwipeBackEnabler: UIViewControllerRepresentable {
    let kind: PageKind
    let allowsSwipeBack: Bool
    let page: PageController

    func makeUIViewController(context: Context) -> Controller {
        let controller = Controller()
        controller.page = page
        page.controller = controller
        return controller
    }

    func updateUIViewController(_ controller: Controller, context: Context) {
        controller.kind = kind
        controller.allowsSwipeBack = allowsSwipeBack
    }

    final class Controller: UIViewController {
        var kind = PageKind.page
        var allowsSwipeBack = true
        weak var page: PageController?

        override func didMove(toParent parent: UIViewController?) {
            super.didMove(toParent: parent)
            install()
        }

        override func viewWillAppear(_ animated: Bool) {
            super.viewWillAppear(animated)
            if let opened = isInsideTrail(), page?.openedWhileExploring != opened {
                page?.openedWhileExploring = opened
            }
            let hidesBottomBar = switch kind {
            case .root: false
            case .exploring, .post: true
            case .page: page?.openedWhileExploring ?? false
            }
            // Only a page in the tab on screen sets the bar: a tab being left, its trail closing as Home opens, must not
            // hide Home's.
            guard let tabBarController,
                  let selected = tabBarController.selectedViewController,
                  PageController.isInside(self, selected),
                  tabBarController.isTabBarHidden != hidesBottomBar
            else { return }
            tabBarController.setTabBarHidden(hidesBottomBar, animated: animated)
        }

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            install()
        }

        /// The index of the trail's first page on a stack: its first exploring or post page.
        static func trailStart(in navigationController: UINavigationController) -> Int? {
            navigationController.viewControllers.firstIndex { pageController in
                guard let kind = enabler(of: pageController)?.kind else { return false }
                return kind == .exploring || kind == .post
            }
        }

        /// Whether a page before this one on its stack is an exploring or post page; nil while it isn't on a stack.
        private func isInsideTrail() -> Bool? {
            guard let navigationController else { return nil }
            var pageController: UIViewController? = self
            while let current = pageController, current.parent !== navigationController {
                pageController = current.parent
            }
            guard let pageController, let index = navigationController.viewControllers.firstIndex(of: pageController)
            else { return nil }
            return Self.trailStart(in: navigationController).map { $0 < index } ?? false
        }

        /// A page's own enabler: the nearest one among its child controllers (none on a page without `navigationPage`).
        private static func enabler(of pageController: UIViewController) -> Controller? {
            var queue = [pageController]
            while !queue.isEmpty {
                let next = queue.removeFirst()
                if let enabler = next as? Controller { return enabler }
                queue += next.children
            }
            return nil
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
        // The swipe from the edge, and iOS 26's swipe from anywhere on the page, which here takes only a swipe that
        // starts at the edge too.
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
    final class Delegate: NSObject, UIGestureRecognizerDelegate {
        /// How far in from the screen's left edge a swipe back can start.
        private static let edgeWidth: CGFloat = 24

        private weak var navigationController: UINavigationController?
        private weak var original: UIGestureRecognizerDelegate?

        init(navigationController: UINavigationController, original: UIGestureRecognizerDelegate?) {
            self.navigationController = navigationController
            self.original = original
        }

        /// Back is a swipe from the screen's edge. iOS 26's swipe from anywhere on the page takes only a touch that
        /// starts there: the rest belong to rows that scroll sideways and the pager between titles.
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            gestureRecognizer !== navigationController?.interactiveContentPopGestureRecognizer
                || touch.location(in: nil).x <= Self.edgeWidth
        }

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            // Never on a stack's first page, while a page is still sliding, or while the page on top says no.
            guard let navigationController,
                  navigationController.viewControllers.count > 1,
                  navigationController.transitionCoordinator == nil,
                  let top = navigationController.topViewController,
                  !Self.blocksSwipeBack(top)
            else { return false }
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
    }
}
