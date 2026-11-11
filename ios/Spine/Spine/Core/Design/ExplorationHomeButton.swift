import SwiftUI

@Observable
final class AppNavigationState {
    private(set) var returnHomeRequest = 0
    private(set) var returnToSearchRequest = 0
    /// The tab on screen, so a page can show the Search button only in the Search tab.
    var selectedTab: AppTab = .home
    /// The tab on screen was tapped again (`navigationPage(.root)` takes it back to its first page, or up to its top).
    private(set) var tabReselectRequest = 0

    func returnHome() {
        returnHomeRequest += 1
    }

    func returnToSearch() {
        returnToSearchRequest += 1
    }

    func reselectTab() {
        tabReselectRequest += 1
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
