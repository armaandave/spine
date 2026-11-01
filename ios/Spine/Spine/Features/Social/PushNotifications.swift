import SwiftUI
import UIKit
import UserNotifications

/// Follow pushes: asks permission the first time someone is signed in, sends this phone's token to the
/// backend, and turns a tapped push into a route for the app shell to open.
@MainActor
@Observable
final class PushNotifications: NSObject, UNUserNotificationCenterDelegate {
    var pendingRoute: PushRoute?

    private var deviceToken: String?
    private var repository: SocialRepository?

    /// Debug builds (Spine Dev) are signed for Apple's sandbox; TestFlight builds for production.
    private static var environment: String {
        #if DEBUG
        "sandbox"
        #else
        "production"
        #endif
    }

    /// Off until the app is signed for push (an `aps-environment` entitlement, with the Push Notifications
    /// capability on its App ID) and the server has a push key. Until then the app doesn't ask for permission, so
    /// the one-time prompt isn't spent on notifications that can't arrive.
    static let isAvailable = false

    func install() {
        UNUserNotificationCenter.current().delegate = self
    }

    /// Asks once, then registers on every launch so a changed token reaches the backend.
    func signedIn(repository: SocialRepository) async {
        guard Self.isAvailable else { return }
        self.repository = repository
        let center = UNUserNotificationCenter.current()
        var status = await center.notificationSettings().authorizationStatus
        if status == .notDetermined {
            _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
            status = await center.notificationSettings().authorizationStatus
        }
        guard status == .authorized || status == .provisional || status == .ephemeral else { return }
        UIApplication.shared.registerForRemoteNotifications()
        await sendToken()
    }

    /// Stops pushes to this phone before the session ends, while the request can still be signed.
    func signOut() async {
        defer { repository = nil }
        guard let deviceToken, let repository else { return }
        try? await repository.unregisterPushDevice(token: deviceToken)
    }

    func didRegister(deviceToken data: Data) {
        deviceToken = data.map { String(format: "%02x", $0) }.joined()
        Task { await sendToken() }
    }

    private func sendToken() async {
        guard let deviceToken, let repository, let bundleId = Bundle.main.bundleIdentifier else { return }
        try? await repository.registerPushDevice(PushDeviceRegistration(
            token: deviceToken,
            bundleId: bundleId,
            environment: Self.environment
        ))
    }

    nonisolated func userNotificationCenter(
        _: UNUserNotificationCenter,
        willPresent _: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
    }

    nonisolated func userNotificationCenter(
        _: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        guard let route = PushRoute(userInfo: userInfo) else { return }
        await MainActor.run {
            self.pendingRoute = route
        }
    }
}

final class SpineAppDelegate: NSObject, UIApplicationDelegate {
    let push = PushNotifications()

    func application(
        _: UIApplication,
        didFinishLaunchingWithOptions _: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        push.install()
        return true
    }

    func application(_: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        push.didRegister(deviceToken: deviceToken)
    }
}

extension PushRoute: Identifiable {
    var id: String {
        switch self {
        case let .profile(username): "profile:\(username)"
        case .followRequests: "follow_requests"
        }
    }
}
