import UIKit

/// Keeps the app running long enough to finish work that mustn't be dropped half way, such as a token refresh
/// whose response would be lost if the user switches apps.
nonisolated enum BackgroundTask {
    /// Runs `operation` inside a UIKit background task. `UIApplication` is main-actor state, so the task is
    /// begun and ended on the main actor while `operation` runs wherever the caller is.
    static func run<Result: Sendable>(
        _ name: String,
        operation: @Sendable () async throws -> Result
    ) async throws -> Result {
        let assertion = await BackgroundTaskAssertion(name: name)
        do {
            let result = try await operation()
            await assertion.end()
            return result
        } catch {
            await assertion.end()
            throw error
        }
    }
}

@MainActor
private final class BackgroundTaskAssertion {
    private var identifier = UIBackgroundTaskIdentifier.invalid

    init(name: String) {
        identifier = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            // iOS is about to suspend the app: hand the task back rather than be killed for overstaying it.
            self?.end()
        }
    }

    func end() {
        guard identifier != .invalid else { return }
        UIApplication.shared.endBackgroundTask(identifier)
        identifier = .invalid
    }
}
