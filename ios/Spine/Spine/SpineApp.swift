import SwiftUI

@main
struct SpineApp: App {
    var body: some Scene {
        WindowGroup {
            // A unit-test host must not boot the real app: its session would refresh, sign out and resume
            // imports against the same Keychain and network the tests are using.
            if !LaunchEnvironment.isHostingUnitTests {
                RootView()
            }
        }
    }
}
