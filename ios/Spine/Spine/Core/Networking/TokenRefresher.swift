import Foundation

/// Serializes refresh-token exchanges for every `APIClient`, and every other write to the stored session.
///
/// The server rotates the refresh token on each exchange, so two concurrent exchanges with the same token
/// can't both count. Everything that needs a refresh goes through one instance (`shared` in the app, a fresh one
/// per test): concurrent callers await a single in-flight exchange, and a caller whose access token has already
/// been replaced skips the exchange entirely. Sign-in and sign-out also go through here, so none of them can
/// interleave with an exchange checking the store and then writing to it.
///
/// Every read of the store is a checked one: a failed Keychain lookup (as opposed to "no such token") is a
/// temporary error, thrown as `KeychainError`. It never clears anything and is never `.unauthorized`.
///
/// Every path that clears the session reports why through `log` (see `SessionLog`).
actor TokenRefresher {
    static let shared = TokenRefresher()

    /// Trades a refresh token for new tokens. It must throw `APIError.unauthorized` only when the server
    /// definitively rejected the token; any other error means the outcome is unknown (offline, timeout, 5xx, ...).
    typealias Exchange = @Sendable (_ refreshToken: String) async throws -> AuthRefreshResponse

    /// Asks the server to revoke a refresh token, best effort.
    typealias Revoke = @Sendable (_ refreshToken: String) async -> Void

    /// A refresh token that expired longer ago than this is dead whatever the server would say. The margin
    /// absorbs a device clock that is wrong, which can be off by days, and ending a session is not undoable: a
    /// token that is really dead costs one refresh request to find out.
    private static let expiredRefreshTokenMargin: TimeInterval = 48 * 60 * 60

    private let log: @Sendable (SessionEndReason, String?) -> Void

    private var inFlight: Task<Void, Error>?

    /// The last exchange this actor committed: the refresh token it spent and the one it stored in its place.
    /// A sign-out decided just before that exchange landed then still finds the session it was meant for.
    private var lastRotation: (spent: String, stored: String)?

    /// How many callers are waiting on the exchange right now, the one that started it included. Tests hold the
    /// exchange open until this reaches the number of callers they started, so overlap is guaranteed, not hoped for.
    private(set) var pendingCallerCount = 0

    init(log: @escaping @Sendable (SessionEndReason, String?) -> Void = { SessionLog.cleared($0, $1) }) {
        self.log = log
    }

    /// Call after a request signed with `rejectedAccessToken` got a 401. Returns normally when the caller
    /// should retry with whatever access token is stored now: either another caller already replaced the
    /// rejected one, or this call refreshed it.
    ///
    /// `revokeDropped` revokes a refresh token the server rotated to for an exchange whose result was thrown
    /// away because the session ended while it was in flight.
    ///
    /// - Throws: `APIError.unauthorized` when the session is really over. Any other error is a temporary
    ///   failure that leaves both tokens untouched.
    func refresh(
        afterRejecting rejectedAccessToken: String?,
        tokenStore: KeychainTokenStore,
        exchange: @escaping Exchange,
        revokeDropped: Revoke? = nil
    ) async throws {
        if let current = try tokenStore.loadAccessToken(), current != rejectedAccessToken {
            return
        }
        try await refresh(tokenStore: tokenStore, exchange: exchange, revokeDropped: revokeDropped)
    }

    /// Refreshes the stored session, awaiting the in-flight exchange if there is one.
    /// Throws the same errors as `refresh(afterRejecting:tokenStore:exchange:revokeDropped:)`.
    func refresh(
        tokenStore: KeychainTokenStore,
        exchange: @escaping Exchange,
        revokeDropped: Revoke? = nil
    ) async throws {
        pendingCallerCount += 1
        defer { pendingCallerCount -= 1 }

        if let inFlight {
            try await inFlight.value
            return
        }
        guard let refreshToken = try tokenStore.loadRefreshToken() else {
            clearSession(tokenStore, .noRefreshToken)
            throw APIError.unauthorized
        }
        if let expiry = JWTExpiry.expiration(of: refreshToken),
           Date().timeIntervalSince(expiry) > Self.expiredRefreshTokenMargin {
            // Dead by our own clock, say a user coming back after the token's 30 days. No need to ask the
            // server, and this holds whatever a server or proxy in between would answer.
            let hours = Int(Date().timeIntervalSince(expiry) / 3600)
            clearSession(tokenStore, .localExpiry, "the refresh token expired \(hours) hours ago")
            throw APIError.unauthorized
        }

        // Unstructured on purpose: a caller's cancellation (say a SwiftUI `.task` going away) must not
        // cancel the exchange for every other caller awaiting it. The background task keeps the app alive
        // until the response is in hand and stored, even if the user switches apps meanwhile.
        let task = Task {
            try await BackgroundTask.run("spine.token-refresh") {
                try await self.performExchange(
                    refreshToken,
                    tokenStore: tokenStore,
                    exchange: exchange,
                    revokeDropped: revokeDropped
                )
            }
        }
        inFlight = task
        try await task.value
    }

    /// Stores the tokens a login or registration returned. A failed write leaves no half a session behind.
    func signIn(access: String, refresh: String, tokenStore: KeychainTokenStore) throws {
        lastRotation = nil
        do {
            // The refresh token first: it is the credential that outlives the access token.
            try tokenStore.setRefreshToken(refresh)
            try tokenStore.setAccessToken(access)
        } catch {
            // Always logged: the sign-in failed because of the Keychain, whether or not its first write made it.
            let complete = tokenStore.clear()
            log(.keychainError, Self.describe((error as? KeychainError).map { "status \($0.status)" }, complete: complete))
            throw error
        }
    }

    /// Ends the session on sign-out, if `refreshToken` is still the one stored (nil: if none is), and returns
    /// the refresh token it removed, for revoking on the server. It goes through the actor so it can't land
    /// between an exchange's check and its writes and have those writes resurrect the session.
    ///
    /// `refreshToken` is the session the sign-out was decided for. A late or duplicate sign-out therefore can't
    /// end a session that started after it (the user signing in again right away, say): the stored token is
    /// another one and nothing is touched. A refresh that rotated the token in between is not another session,
    /// and is recognized as such. A token that can't be read now counts as none, so a locked Keychain doesn't
    /// stop the attempt to sign out.
    ///
    /// A caller that couldn't read the stored token when it decided to sign out has no session to name; it
    /// uses `signOut(tokenStore:)`.
    @discardableResult
    func signOut(ifRefreshToken refreshToken: String?, tokenStore: KeychainTokenStore) -> String? {
        let (stored, unreadable) = storedRefreshToken(in: tokenStore)
        if let stored, stored != refreshToken, !isRotation(from: refreshToken, to: stored) {
            return nil
        }
        return endSignedOutSession(tokenStore, removing: stored, unreadable: unreadable)
    }

    /// Ends whatever session is stored, and returns its refresh token for revoking on the server. For a sign-out
    /// that couldn't read the Keychain when it was decided: it can't tell which session it was meant for, or
    /// whether a newer one has started, but the user asked to sign out and the session must not stay behind in
    /// the Keychain for the next launch to restore.
    @discardableResult
    func signOut(tokenStore: KeychainTokenStore) -> String? {
        let (stored, unreadable) = storedRefreshToken(in: tokenStore)
        return endSignedOutSession(tokenStore, removing: stored, unreadable: unreadable)
    }

    /// The stored refresh token, telling one that isn't there (nil) from a Keychain that wouldn't answer.
    private func storedRefreshToken(in tokenStore: KeychainTokenStore) -> (token: String?, unreadable: Bool) {
        do {
            return (try tokenStore.loadRefreshToken(), false)
        } catch {
            return (nil, true)
        }
    }

    private func endSignedOutSession(_ tokenStore: KeychainTokenStore, removing stored: String?, unreadable: Bool) -> String? {
        let complete = tokenStore.clear()
        lastRotation = nil
        // Logged only if a session was there to end (or might have been: what was stored couldn't be read).
        if stored != nil || unreadable {
            log(.userSignOut, Self.describe(unreadable ? "what was stored couldn't be read first" : nil, complete: complete))
        }
        return stored
    }

    /// Clears the session if `accessToken` is still the stored one, so tokens stored after a request was
    /// signed survive its failure. Says whether it cleared.
    @discardableResult
    func clear(ifAccessToken accessToken: String?, tokenStore: KeychainTokenStore) throws -> Bool {
        guard try tokenStore.loadAccessToken() == accessToken else { return false }
        clearSession(tokenStore, .serverRejection, "an access token was rejected after a refresh")
        return true
    }

    private func isRotation(from spent: String?, to stored: String) -> Bool {
        guard let spent, let lastRotation else { return false }
        return lastRotation.spent == spent && lastRotation.stored == stored
    }

    /// Clears the session and says why, if there was anything to clear. A clear that finds nothing stored (a late
    /// 401 after the user signed out, an exchange that the app's own revocation got rejected) changed nothing, and
    /// logging it would make it look as if something had just ended a session.
    private func clearSession(_ tokenStore: KeychainTokenStore, _ reason: SessionEndReason, _ detail: String? = nil) {
        let hadTokens = (try? tokenStore.loadRefreshToken()) != nil || (try? tokenStore.loadAccessToken()) != nil
        let complete = tokenStore.clear()
        lastRotation = nil
        if hadTokens {
            log(reason, Self.describe(detail, complete: complete))
        }
    }

    /// A clear that couldn't delete a token hasn't ended the session as far as the next launch is concerned: the
    /// log says so, next to the reason.
    private static func describe(_ detail: String?, complete: Bool) -> String? {
        guard !complete else { return detail }
        let warning = "a token couldn't be deleted and may return at the next launch"
        return detail.map { "\($0); \(warning)" } ?? warning
    }

    private func performExchange(
        _ refreshToken: String,
        tokenStore: KeychainTokenStore,
        exchange: Exchange,
        revokeDropped: Revoke?
    ) async throws {
        // Runs on the actor after the last suspension, so applying the result and freeing the slot are
        // atomic: a caller either joins this exchange or sees the tokens it stored.
        defer { inFlight = nil }

        let response: AuthRefreshResponse
        do {
            response = try await exchange(refreshToken)
        } catch APIError.unauthorized {
            // The server rejected this refresh token. End the session only if it is still the stored one;
            // if newer tokens landed while the exchange was in flight they must survive, and callers
            // simply retry with them.
            if let stored = try tokenStore.loadRefreshToken(), stored != refreshToken { return }
            clearSession(tokenStore, .serverRejection, "the refresh token was rejected")
            throw APIError.unauthorized
        }

        // Drop the result if the user signed out (or in again) while the exchange was in flight.
        guard try tokenStore.loadRefreshToken() == refreshToken else {
            // The server rotated the token all the same, so the new one is a live credential that nobody
            // holds. Revoke it; nobody waits for that.
            if let rotated = response.refresh, let revokeDropped {
                Task { await revokeDropped(rotated) }
            }
            return
        }
        // Refresh token first: it is the one the server just rotated, so if we're killed between the two
        // writes the next launch can still refresh instead of holding a blacklisted token. A failed write
        // stops here and surfaces as a temporary error; the server still honors the old token for a minute.
        if let rotated = response.refresh {
            try tokenStore.setRefreshToken(rotated)
            lastRotation = (refreshToken, rotated)
        }
        try tokenStore.setAccessToken(response.access)
    }
}
