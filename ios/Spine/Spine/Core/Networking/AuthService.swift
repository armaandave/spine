import Foundation

struct AuthService {
    /// A launch refresh is skipped while the stored access token has at least this long left.
    private static let launchRefreshLeeway: TimeInterval = 120

    let client: APIClient
    /// How long the background revocation of a signed-out session's refresh token may take before it gives up.
    var revocationTimeout: Duration = .seconds(5)

    func login(usernameOrEmail: String, password: String) async throws -> AuthUser {
        let response: AuthTokenResponse = try await client.post(
            "/auth/login/",
            body: LoginRequest(usernameOrEmail: usernameOrEmail, password: password)
        )
        try await storeTokens(from: response)
        return response.user
    }

    func register(username: String, email: String, password: String) async throws -> AuthUser {
        let response: AuthTokenResponse = try await client.post(
            "/auth/register/",
            body: RegisterRequest(
                username: username,
                email: email,
                password: password,
                passwordConfirm: password
            )
        )
        try await storeTokens(from: response)
        return response.user
    }

    /// The launch-time refresh. Every refresh rotates the refresh token, so it is skipped while the stored access
    /// token is still good for a couple of minutes; a token that can't be read or decoded is refreshed as usual.
    ///
    /// Throws `APIError.unauthorized` only when the session is really over (tokens cleared); any other error is
    /// temporary and leaves the tokens intact. `APIClient.refreshSession()` refreshes unconditionally.
    func refresh() async throws {
        if let token = try? client.tokenProvider.loadAccessToken(),
           let expiry = JWTExpiry.expiration(of: token),
           expiry.timeIntervalSinceNow > Self.launchRefreshLeeway {
            return
        }
        try await client.refreshSession()
    }

    /// Signs out. The session ends on this device before this returns, whatever the network does; telling the
    /// server comes after, in the background, with the refresh token that was stored and nothing else.
    ///
    /// The session is cleared only while the token captured here is still the stored one, so a late or
    /// duplicate sign-out can't end a session that started after it. The returned task is the revocation
    /// (best effort, bounded by `revocationTimeout`); callers that don't care can drop it.
    @discardableResult
    func logout() async -> Task<Void, Never> {
        let refreshToken: String?
        let cleared: String?
        do {
            refreshToken = try client.tokenProvider.loadRefreshToken()
            cleared = await client.refresher.signOut(ifRefreshToken: refreshToken, tokenStore: client.tokenProvider)
        } catch {
            // The Keychain wouldn't say what is stored, which is not the same as nothing being stored: there is
            // no session to name, so end whatever is there rather than skip it (and have the next launch restore it).
            refreshToken = nil
            cleared = await client.refresher.signOut(tokenStore: client.tokenProvider)
        }
        // `cleared` differs from `refreshToken` when a refresh rotated it in between; revoke both.
        var tokens: [String] = []
        for token in [refreshToken, cleared] {
            if let token, !tokens.contains(token) { tokens.append(token) }
        }
        return Task { await revoke(tokens) }
    }

    /// Revokes `tokens` on the server, one after the other, in a background task so the app keeps running if the
    /// user leaves it meanwhile. It gives up when `revocationTimeout` is over, and ignores every failure.
    private func revoke(_ tokens: [String]) async {
        guard !tokens.isEmpty else { return }
        let work = Task {
            _ = try? await BackgroundTask.run("spine.logout") {
                for token in tokens {
                    await client.revokeRefreshToken(token)
                }
            }
        }
        _ = await waitBounded(for: work, upTo: revocationTimeout)
        work.cancel()
    }

    private func storeTokens(from response: AuthTokenResponse) async throws {
        try await client.refresher.signIn(
            access: response.access,
            refresh: response.refresh,
            tokenStore: client.tokenProvider
        )
    }
}

struct HealthService {
    let client: APIClient

    func check() async throws -> HealthResponse {
        try await client.get("/health/")
    }
}
