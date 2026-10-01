import Foundation

/// The signed-in user's identity, remembered between launches. With it the shell can open with everything that
/// depends on who you are (edit and delete on your own logs, list ownership, the profile tab image) while the
/// profile request can't complete. It holds ids and display fields only, never a token, so UserDefaults will do.
struct SignedInUserCache {
    private static let key = "spine.signedInUser.v1"

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func load() -> AuthUser? {
        guard let data = defaults.data(forKey: Self.key) else { return nil }
        return try? JSONDecoder().decode(AuthUser.self, from: data)
    }

    func save(_ user: AuthUser) {
        guard let data = try? JSONEncoder().encode(user) else { return }
        defaults.set(data, forKey: Self.key)
    }

    func clear() {
        defaults.removeObject(forKey: Self.key)
    }
}
