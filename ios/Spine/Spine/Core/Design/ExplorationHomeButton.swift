import SwiftUI

@Observable
final class AppNavigationState {
    private(set) var returnHomeRequest = 0
    private(set) var returnToSearchRequest = 0

    func returnHome() {
        returnHomeRequest += 1
    }

    func returnToSearch() {
        returnToSearchRequest += 1
    }
}

private struct AppNavigationStateKey: EnvironmentKey {
    static let defaultValue: AppNavigationState? = nil
}

extension EnvironmentValues {
    var appNavigationState: AppNavigationState? {
        get { self[AppNavigationStateKey.self] }
        set { self[AppNavigationStateKey.self] = newValue }
    }
}

private struct ExplorationDepthKey: EnvironmentKey {
    static let defaultValue = 0
}

private extension EnvironmentValues {
    var explorationDepth: Int {
        get { self[ExplorationDepthKey.self] }
        set { self[ExplorationDepthKey.self] = newValue }
    }
}

struct ExplorationHomeButton: View {
    @Environment(\.appNavigationState) private var appNavigationState
    var glass: Glass = .regular.tint(.white.opacity(0.1)).interactive()

    var body: some View {
        if let appNavigationState {
            ExplorationGlassButton(systemName: "house.fill", glass: glass) {
                appNavigationState.returnHome()
            }
            .accessibilityLabel("Go to Home")
            .accessibilityHint("Closes media browsing and returns to Home")
            .accessibilityIdentifier("exploration.home")
        }
    }
}

struct ExplorationSearchButton: View {
    @Environment(\.appNavigationState) private var appNavigationState
    var glass: Glass = .regular.tint(.white.opacity(0.1)).interactive()

    var body: some View {
        if let appNavigationState {
            ExplorationGlassButton(systemName: "magnifyingglass", glass: glass) {
                appNavigationState.returnToSearch()
            }
            .accessibilityLabel("Go to Search")
            .accessibilityHint("Closes media browsing and returns to Search")
            .accessibilityIdentifier("exploration.search")
        }
    }
}

private struct ExplorationGlassButton: View {
    let systemName: String
    let glass: Glass
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white.opacity(0.84))
                .frame(width: 58, height: 58)
        }
        .buttonStyle(.plain)
        .contentShape(Circle())
        .glassEffect(glass, in: .circle)
    }
}

private struct DismissExplorationOnReturnHome: ViewModifier {
    @Environment(\.appNavigationState) private var appNavigationState
    @Environment(\.explorationDepth) private var explorationDepth
    @Environment(\.dismiss) private var dismiss

    func body(content: Content) -> some View {
        content
            .environment(\.explorationDepth, explorationDepth + 1)
            .onChange(of: appNavigationState?.returnHomeRequest) { _, request in
                guard request != nil, explorationDepth == 0 else { return }
                dismiss()
            }
            .onChange(of: appNavigationState?.returnToSearchRequest) { _, request in
                guard request != nil, explorationDepth == 0 else { return }
                dismiss()
            }
    }
}

extension View {
    func dismissExplorationOnReturnHome() -> some View {
        modifier(DismissExplorationOnReturnHome())
    }
}
