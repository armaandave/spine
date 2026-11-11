import SwiftUI
import UIKit

/// What a page is, which decides its bottom bar and whether the floating Home and Search buttons show.
enum PageKind {
    /// A tab's first page.
    case root
    /// A title, person, company, series, song or genre page. Exploring starts here: this page and every page opened
    /// after it hide the bottom bar, and the floating Home and Search buttons are the way out.
    case exploring
    /// A log's or a day's progress page: its comment box takes the bottom, so the bar stays hidden.
    case post
    /// Any other page: the bottom bar shows, unless the page was opened while exploring.
    case page
}

extension View {
    /// Sets a page up: its bottom bar, Apple's swipe back, the pages its links open (`pushDestination`), and the
    /// floating Home and Search buttons while exploring. Apply it last, outside the page's `pushDestination`s, so the
    /// pages they open are opened while exploring too. `allowsSwipeBack` is false while something over the page
    /// (a poster opened full screen) takes swipes itself.
    func navigationPage(
        _ kind: PageKind,
        showsExplorationButtons: Bool = true,
        allowsSwipeBack: Bool = true
    ) -> some View {
        modifier(NavigationPageModifier(
            kind: kind,
            showsExplorationButtons: showsExplorationButtons,
            allowsSwipeBack: allowsSwipeBack
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

    let kind: PageKind
    let showsExplorationButtons: Bool
    let allowsSwipeBack: Bool

    func body(content: Content) -> some View {
        let isExploring = kind == .exploring || (kind != .root && openedWhileExploring)
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
                SwipeBackEnabler(allowsSwipeBack: allowsSwipeBack)
                    .frame(width: 0, height: 0)
                    .accessibilityHidden(true)
            }
            .toolbar(bottomBar(isExploring), for: .tabBar)
            .environment(\.isExploring, isExploring)
    }

    private func showsButtons(_ isExploring: Bool) -> Bool {
        showsExplorationButtons && isExploring && kind != .post && kind != .root
    }

    private func bottomBar(_ isExploring: Bool) -> Visibility {
        switch kind {
        case .root:
            // Said outright: a trail closed from another tab (Home's button) left the bar hidden on `.automatic`.
            .visible
        case .exploring, .post:
            .hidden
        case .page:
            isExploring ? .hidden : .visible
        }
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

import os
enum NavDebug {
    static let log = Logger(subsystem: "spine.navdebug", category: "nav")
}

/// Turns Apple's swipe back on for the page's navigation stack. Our pages hide the back button or the whole
/// navigation bar to draw their own over the artwork, and iOS turns its swipes off when either is hidden.
private struct SwipeBackEnabler: UIViewControllerRepresentable {
    let allowsSwipeBack: Bool

    func makeUIViewController(context: Context) -> Controller {
        Controller()
    }

    func updateUIViewController(_ controller: Controller, context: Context) {
        controller.allowsSwipeBack = allowsSwipeBack
    }

    final class Controller: UIViewController {
        var allowsSwipeBack = true

        override func didMove(toParent parent: UIViewController?) {
            super.didMove(toParent: parent)
            install()
        }

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            install()
        }

        private func install() {
            guard let navigationController else {
                NavDebug.log.notice("enabler: no navigationController")
                return
            }
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

    /// Asks Apple's own delegate first, so the swipe works exactly as Apple's wherever Apple allows it. When Apple
    /// says no on a page that is not the stack's first, it was only because the bar or its back button is hidden,
    /// unless the finger is moving another way or a row under it can scroll back.
    final class Delegate: NSObject, UIGestureRecognizerDelegate {
        private weak var navigationController: UINavigationController?
        private weak var original: UIGestureRecognizerDelegate?

        init(navigationController: UINavigationController, original: UIGestureRecognizerDelegate?) {
            self.navigationController = navigationController
            self.original = original
        }

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            if let top = navigationController?.topViewController, Self.blocksSwipeBack(top) {
                return false
            }
            if original?.gestureRecognizerShouldBegin?(gestureRecognizer) == true {
                NavDebug.log.notice("shouldBegin: Apple's says yes")
                return true
            }
            guard let navigationController,
                  navigationController.viewControllers.count > 1,
                  navigationController.transitionCoordinator == nil
            else { return false }
            if let pan = gestureRecognizer as? UIPanGestureRecognizer,
               !(gestureRecognizer is UIScreenEdgePanGestureRecognizer) {
                // From anywhere on the page: only a swipe to the right, and not one that scrolls a row back.
                let velocity = pan.velocity(in: pan.view)
                guard velocity.x > 0, velocity.x > abs(velocity.y) else { return false }
                if Self.isOverRowThatScrollsBack(pan) { return false }
            }
            NavDebug.log.notice("shouldBegin: ours says yes")
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
            original?.gestureRecognizer?(gestureRecognizer, shouldBeRequiredToFailBy: otherGestureRecognizer)
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
