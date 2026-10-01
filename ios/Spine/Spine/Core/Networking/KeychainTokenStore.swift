import Foundation
import Security

/// A Keychain call failed for a reason other than "no such item", for example because the device is locked.
/// That says nothing about whether a session exists, so callers treat it as temporary and never as a sign-out.
nonisolated struct KeychainError: LocalizedError, Equatable, Sendable {
    let status: OSStatus

    var errorDescription: String? {
        "Couldn't reach your saved sign-in in the Keychain (error \(status)). Try again in a moment."
    }
}

/// The Security framework calls the token store makes, behind a seam so tests can make them fail.
nonisolated protocol KeychainOperations: Sendable {
    func read(account: String) -> (status: OSStatus, data: Data?)
    /// Replaces the value in place; `errSecItemNotFound` if there is none yet.
    func update(account: String, data: Data) -> OSStatus
    func add(account: String, data: Data) -> OSStatus
    func delete(account: String) -> OSStatus
}

nonisolated struct SecurityKeychain: KeychainOperations {
    func read(account: String) -> (status: OSStatus, data: Data?) {
        var query = baseQuery(account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        return (status, item as? Data)
    }

    func update(account: String, data: Data) -> OSStatus {
        SecItemUpdate(baseQuery(account) as CFDictionary, attributes(for: data) as CFDictionary)
    }

    func add(account: String, data: Data) -> OSStatus {
        SecItemAdd(baseQuery(account).merging(attributes(for: data)) { $1 } as CFDictionary, nil)
    }

    func delete(account: String) -> OSStatus {
        SecItemDelete(baseQuery(account) as CFDictionary)
    }

    private func baseQuery(_ account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: account,
        ]
    }

    private func attributes(for data: Data) -> [String: Any] {
        [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
    }
}

/// Persists JWT access/refresh tokens for authenticated API calls.
///
/// `nonisolated` (the app module defaults to `@MainActor`) because `TokenRefresher` and `APIClient` both use it:
/// every call is a single thread-safe Keychain operation and the type holds no mutable state.
///
/// The `accessToken` and `refreshToken` properties read a failed lookup as "no token", which suits UI that only
/// wants to know whether a session exists. Anything that decides about the session uses the throwing `load…` and
/// `set…` methods instead, which keep a real "not found" apart from a lookup that failed.
nonisolated final class KeychainTokenStore: @unchecked Sendable {
    static let shared = KeychainTokenStore()

    private let accessKey = "spine.accessToken"
    private let refreshKey = "spine.refreshToken"
    private let keychain: KeychainOperations
    private let onDeleteFailure: @Sendable (_ token: String, _ status: OSStatus) -> Void

    /// `onDeleteFailure` hears of every token `clear()` couldn't delete; the app logs it.
    init(
        keychain: KeychainOperations = SecurityKeychain(),
        onDeleteFailure: @escaping @Sendable (String, OSStatus) -> Void = { SessionLog.deleteFailed($0, status: $1) }
    ) {
        self.keychain = keychain
        self.onDeleteFailure = onDeleteFailure
    }

    var accessToken: String? {
        get { try? loadAccessToken() }
        set { try? setAccessToken(newValue) }
    }

    var refreshToken: String? {
        get { try? loadRefreshToken() }
        set { try? setRefreshToken(newValue) }
    }

    /// nil means there is no token; a failed lookup throws `KeychainError`.
    func loadAccessToken() throws -> String? {
        try read(key: accessKey)
    }

    func loadRefreshToken() throws -> String? {
        try read(key: refreshKey)
    }

    /// nil deletes the token.
    func setAccessToken(_ value: String?) throws {
        try write(key: accessKey, value: value)
    }

    func setRefreshToken(_ value: String?) throws {
        try write(key: refreshKey, value: value)
    }

    /// Removes both tokens, best effort: a delete that fails doesn't stop the other. The refresh token goes first
    /// because it is the long-lived credential; an access token left behind by a failed delete lapses within
    /// minutes, while a refresh token left behind would keep the session alive for a month.
    ///
    /// Says whether both are gone. A token that is still there would bring the session back at the next launch,
    /// so every delete that fails is reported to `onDeleteFailure`.
    @discardableResult
    func clear() -> Bool {
        var complete = true
        do {
            try setRefreshToken(nil)
        } catch {
            complete = false
            reportDeleteFailure(of: "refresh token", error)
        }
        do {
            try setAccessToken(nil)
        } catch {
            complete = false
            reportDeleteFailure(of: "access token", error)
        }
        return complete
    }

    private func reportDeleteFailure(of token: String, _ error: Error) {
        onDeleteFailure(token, (error as? KeychainError)?.status ?? errSecInternalError)
    }

    private func read(key: String) throws -> String? {
        let (status, data) = keychain.read(account: key)
        switch status {
        case errSecSuccess:
            return data.flatMap { String(data: $0, encoding: .utf8) }
        case errSecItemNotFound:
            return nil
        default:
            throw KeychainError(status: status)
        }
    }

    private func write(key: String, value: String?) throws {
        // Only a nil value removes the item. Replacing a token updates it in place, so a
        // concurrent reader never sees the token missing while it is being rotated.
        guard let value else {
            let status = keychain.delete(account: key)
            guard status == errSecSuccess || status == errSecItemNotFound else {
                throw KeychainError(status: status)
            }
            return
        }

        let data = Data(value.utf8)
        var status = keychain.update(account: key, data: data)
        if status == errSecItemNotFound {
            status = keychain.add(account: key, data: data)
        }
        if status == errSecDuplicateItem {
            // A concurrent writer added the item between our update and add.
            status = keychain.update(account: key, data: data)
        }
        guard status == errSecSuccess else {
            throw KeychainError(status: status)
        }
    }
}
