import Foundation

enum LaunchEnvironment {
    /// True when this process is the host app of a unit-test bundle. Xcode sets the variable when it launches the
    /// host, so it is there before any test bundle code is loaded. UI tests drive the app in its own process,
    /// without it.
    static var isHostingUnitTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }
}
