import Foundation
import os

/// Why the stored session was cleared. Every code path that clears it reports one of these, so a "random
/// sign-out" report can be traced to its cause from the device log instead of guessed at.
nonisolated enum SessionEndReason: String, Sendable {
    /// A refresh was due but no refresh token was stored.
    case noRefreshToken = "no refresh token"
    /// The refresh token expired long enough ago that it is dead whatever the server would say.
    case localExpiry = "local expiry"
    /// The server itself rejected the refresh token, or an access token it had just refreshed.
    case serverRejection = "server rejection"
    /// The user chose Sign Out.
    case userSignOut = "user sign-out"
    /// The Keychain refused the writes of a sign-in, which is undone rather than left half stored.
    case keychainError = "keychain error"
}

/// The `session` category of the app's unified log (subsystem: the bundle id), for events that change or decide
/// about the session. Read it with Console.app or `log stream --predicate 'category == "session"'`.
///
/// Nothing here ever takes a token. A `detail` is a short, non-secret fact about the event (an HTTP status, the
/// API's error code, how long ago a token expired).
nonisolated enum SessionLog {
    private static let logger = Logger(
        subsystem: Bundle.main.bundleIdentifier ?? "app.spine",
        category: "session"
    )

    /// The session was cleared, for `reason`.
    static func cleared(_ reason: SessionEndReason, _ detail: String? = nil) {
        if let detail {
            logger.notice("Session cleared: \(reason.rawValue, privacy: .public) (\(detail, privacy: .public))")
        } else {
            logger.notice("Session cleared: \(reason.rawValue, privacy: .public)")
        }
    }

    /// Something happened to the session that didn't clear it, for example a launch that kept it.
    static func note(_ message: String) {
        logger.notice("\(message, privacy: .public)")
    }

    /// A token couldn't be removed from the Keychain, so the session it belongs to may come back at the next
    /// launch. Only the OSStatus is logged.
    static func deleteFailed(_ token: String, status: OSStatus) {
        logger.error("Keychain: couldn't delete the \(token, privacy: .public) (status \(status))")
    }
}
