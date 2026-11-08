import Foundation

/// A stored input of a view that SwiftUI's change check treats as never changed.
///
/// SwiftUI decides whether a view's body has to run again by comparing the view's stored properties with the ones it
/// was built with. A closure (or a repository handed over as a protocol) cannot be compared, so a view that holds one
/// counts as changed every time its parent runs, and its body runs with it, and its children's, down to every card.
/// Home's closures and repositories are the session's and the shell's own: they act on state boxes and objects that
/// stay the same however often a parent rebuilds them, so a new copy tells the view nothing. Put them in this wrapper
/// and the view runs again only for what really changes (the signed-in user, the selected tab, its own state).
///
///     @StableInput var onOpen: (Int) -> Void
///
/// Do not wrap a value the view draws: a change in it would not redraw anything.
@propertyWrapper
nonisolated struct StableInput<Value>: Equatable {
    var wrappedValue: Value

    init(wrappedValue: Value) {
        self.wrappedValue = wrappedValue
    }

    static func == (lhs: StableInput<Value>, rhs: StableInput<Value>) -> Bool {
        true
    }
}
