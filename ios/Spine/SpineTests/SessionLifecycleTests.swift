import Foundation
import UIKit
import XCTest
@testable import Spine

// MARK: - App launch

/// `AppSession` over the real repositories and the fake server. The Keychain, the defaults and the network it
/// touches all belong to the test.
@MainActor
final class AppSessionLaunchTests: RefreshTestCase {
    private let cachedUser = AuthUser(
        id: 7,
        username: "reader",
        displayName: "Cached Reader",
        isPrivate: false,
        avatarUrl: nil
    )

    // MARK: Signed out and signed in

    func testStartWithoutStoredTokensSignsOutAndForgetsAStaleCachedUser() async {
        seedCache()
        let session = makeSession()

        await session.start()

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
    }

    func testStartRefreshesAndSignsInWhenTheServerIsReachable() async {
        signIn(accessExpired: false)
        seedRunningImport()
        let session = makeSession(imports: ParkedImportRepository())

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected signed in, got \(session.state)")
        }
        XCTAssertEqual(user?.displayName, "Reader")
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
        XCTAssertEqual(SignedInUserCache(defaults: defaults).load()?.displayName, "Reader")
        XCTAssertTrue(isProcessing(session), "the network works, so a running import resumes")
        await session.logout()
    }

    func testStartSignsOutWhenTheRefreshTokenIsRejected() async {
        signIn()
        seedCache()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }
        let session = makeSession()

        await session.start()

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertNotNil(session.errorMessage)
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
        XCTAssertNil(SignedInUserCache(defaults: defaults).load(), "a signed-out session forgets who it was")
    }

    // MARK: Degraded launch

    func testTemporaryRefreshFailureEntersTheShellWithTheCachedUserWithoutWaitingForTheProfile() async {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImport()
        backend.setOverride { $0.isRefresh ? .failure(.notConnectedToInternet) : nil }
        backend.hold(path: RefreshTestPath.me) // the profile request stays unanswered, as on a bad network
        let session = makeSession(imports: ParkedImportRepository())

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected the shell, got \(session.state)")
        }
        XCTAssertEqual(user?.displayName, "Cached Reader", "ownership-dependent UI works from the first frame")
        XCTAssertNil(session.errorMessage)
        XCTAssertEqual(store.refreshToken, "refresh-0")
        XCTAssertEqual(
            session.letterboxdImportCoordinator.phase,
            .idle,
            "a running import isn't resumed before the network is known to work"
        )

        await session.logout()
        backend.release(path: RefreshTestPath.me)
    }

    func testBackgroundProfileArrivalUpdatesTheStateCachesTheUserAndResumesImports() async {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImport()
        backend.setOverride { $0.isRefresh ? .failure(.notConnectedToInternet) : nil }
        backend.hold(path: RefreshTestPath.me)
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()

        backend.release(path: RefreshTestPath.me)
        await waitUntil("the fresh profile") { displayName(of: session) == "Reader" }

        XCTAssertEqual(SignedInUserCache(defaults: defaults).load()?.displayName, "Reader")
        XCTAssertTrue(isProcessing(session), "a profile that got through confirms the network, so the import resumes")
        await session.logout()
    }

    func testWithoutACachedUserTheShellOpensWithAnUnknownUserThenLoadsTheProfile() async {
        signIn(accessExpired: false)
        backend.setOverride { $0.isRefresh ? .failure(.notConnectedToInternet) : nil }
        backend.hold(path: RefreshTestPath.me)
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected the shell, got \(session.state)")
        }
        XCTAssertNil(user)

        backend.release(path: RefreshTestPath.me)
        await waitUntil("the profile") { displayName(of: session) == "Reader" }
        XCTAssertEqual(SignedInUserCache(defaults: defaults).load()?.username, "reader")
    }

    func testProfileLoadRetriesWhenTheAppBecomesActiveAgain() async {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImport()
        backend.setOverride { _ in .failure(.notConnectedToInternet) } // nothing gets through
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()
        await waitUntil("the first profile attempt") { backend.requests(path: RefreshTestPath.me).count == 1 }
        XCTAssertFalse(isProcessing(session))

        backend.setOverride(nil) // the network is back
        await waitUntil("the retry after the app became active") {
            postActivation()
            return displayName(of: session) == "Reader"
        }
        postActivation()
        postActivation()
        try? await Task.sleep(for: .milliseconds(100))

        XCTAssertEqual(
            backend.requests(path: RefreshTestPath.me).count,
            2,
            "one failed attempt and one retry; once the profile is in, activations do nothing"
        )
        XCTAssertTrue(isProcessing(session))
        await session.logout()
    }

    func testSigningOutAbandonsTheProfileRequestInFlight() async {
        signIn(accessExpired: false)
        seedCache()
        backend.setOverride { $0.isRefresh ? .failure(.notConnectedToInternet) : nil }
        backend.hold(path: RefreshTestPath.me)
        let session = makeSession()
        await session.start()

        await session.logout()
        backend.release(path: RefreshTestPath.me)
        try? await Task.sleep(for: .milliseconds(100))

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
    }

    func testProfileArrivingAfterLogoutIsIgnored() async {
        signIn(accessExpired: false)
        seedCache()
        backend.setOverride { $0.isRefresh ? .failure(.notConnectedToInternet) : nil }
        let gate = RefreshGate()
        // A profile request that completes whatever happens to the task that asked for it.
        let profile = GatedProfileRepository(
            base: AppRepositories.live(client: makeClient()).profile,
            profile: Self.makeProfile(),
            gate: gate
        )
        let session = makeSession(profile: profile)
        await session.start()

        await session.logout()
        await gate.open() // the profile arrives after the session has ended
        try? await Task.sleep(for: .milliseconds(100))

        guard case .signedOut = session.state else {
            return XCTFail("A late profile must not sign the user back in, got \(session.state)")
        }
        XCTAssertNil(SignedInUserCache(defaults: defaults).load(), "nor be remembered")
    }

    func testRetryingTheProfileWhileSignedOutDoesNothing() async {
        let session = makeSession()
        await session.start()

        session.retryProfileIfNeeded()
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertTrue(backend.requests.isEmpty)
    }

    func testProfileRejectedForGoodInTheBackgroundSignsTheUserOut() async {
        signIn() // the access token has expired
        seedCache()
        let refreshes = RefreshCounter()
        backend.setOverride { request in
            guard request.isRefresh else { return nil }
            // The launch refresh can't get through; by the time the profile request refreshes, the server
            // has rejected the token.
            return refreshes.increment() == 1 ? .failure(.notConnectedToInternet) : .tokenNotValid()
        }
        let session = makeSession()

        await session.start()
        await waitUntil("the session to end") {
            if case .signedOut = session.state { return true }
            return false
        }

        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testProfileFailureAfterASuccessfulRefreshEntersTheShellWithTheCachedUser() async {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImport()
        backend.setOverride { $0.isMe ? .failure(.notConnectedToInternet) : nil }
        let session = makeSession(imports: ParkedImportRepository())

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected the shell, got \(session.state)")
        }
        XCTAssertEqual(user?.displayName, "Cached Reader")
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertFalse(isProcessing(session), "no request has completed since the refresh, so imports wait")
        await session.logout()
    }

    func testStartStaysSignedInWhenRefreshFailsBecauseTheDeviceIsOffline() async {
        signIn()
        backend.setOverride { _ in .failure(.notConnectedToInternet) }
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected to stay signed in, got \(session.state)")
        }
        XCTAssertNil(user, "no profile loaded and nothing cached")
        XCTAssertNil(session.errorMessage)
        XCTAssertEqual(store.accessToken, "access-0")
        XCTAssertEqual(store.refreshToken, "refresh-0")
        XCTAssertTrue(backend.requests(path: RefreshTestPath.logout).isEmpty)
    }

    func testStartStaysSignedInWithProfileWhenRefreshFailsButTheAccessTokenStillWorks() async {
        signIn(accessExpired: false)
        backend.setOverride { $0.isRefresh ? .status(503) : nil }
        let session = makeSession()

        await session.start()
        await waitUntil("the profile") { displayName(of: session) == "Reader" }

        XCTAssertNil(session.errorMessage)
        XCTAssertEqual(store.refreshToken, "refresh-0")
    }

    func testStartStaysSignedInWhenTheServerAnswersEveryRequestWithAnHTMLBadRequest() async {
        signIn()
        backend.setOverride { _ in .html(400, RefreshErrorPage.djangoBadRequest) } // ALLOWED_HOSTS is wrong after a deploy
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected to stay signed in, got \(session.state)")
        }
        XCTAssertNil(user)
        XCTAssertNil(session.errorMessage)
        XCTAssertEqual(store.accessToken, "access-0")
        XCTAssertEqual(store.refreshToken, "refresh-0")
        XCTAssertTrue(backend.requests(path: RefreshTestPath.logout).isEmpty)
    }

    func testStartStaysSignedInWhenAProxyAnswersEveryRequestWithAnHTMLUnauthorizedPage() async {
        signIn()
        backend.setOverride { _ in .html(401, RefreshErrorPage.nginxUnauthorized) }
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected to stay signed in, got \(session.state)")
        }
        XCTAssertNil(user)
        XCTAssertNil(session.errorMessage)
        XCTAssertEqual(store.accessToken, "access-0")
        XCTAssertEqual(store.refreshToken, "refresh-0")
        XCTAssertTrue(backend.requests(path: RefreshTestPath.logout).isEmpty)
    }

    // MARK: The launch refresh is skipped while the access token is good

    func testLaunchSkipsTheRefreshWhileTheAccessTokenHasMoreThanTwoMinutesLeft() async {
        let fresh = makeJWT(expiringAt: Date().addingTimeInterval(600))
        store.accessToken = fresh
        store.refreshToken = "refresh-0"
        backend.accept(access: fresh, refresh: "refresh-0")
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected signed in, got \(session.state)")
        }
        XCTAssertEqual(user?.displayName, "Reader")
        XCTAssertTrue(backend.refreshRequests.isEmpty, "no rotation for a token that is still good")
        XCTAssertEqual(store.accessToken, fresh)
        XCTAssertEqual(store.refreshToken, "refresh-0")
    }

    func testLaunchRefreshesWhenTheAccessTokenIsAboutToExpire() async {
        await assertLaunchRefreshes(accessToken: makeJWT(expiringAt: Date().addingTimeInterval(60)))
    }

    func testLaunchRefreshesWhenTheAccessTokenHasExpired() async {
        await assertLaunchRefreshes(accessToken: makeJWT(expiringAt: Date().addingTimeInterval(-300)))
    }

    func testLaunchRefreshesWhenTheAccessTokenCannotBeDecoded() async {
        await assertLaunchRefreshes(accessToken: "not-a-jwt")
    }

    func testLaunchWithOnlyARefreshTokenRefreshes() async {
        await assertLaunchRefreshes(accessToken: nil)
    }

    // MARK: Signing in and out

    func testLoginStoresTheSessionCachesTheUserAndResumesImports() async {
        seedRunningImport()
        let session = makeSession(imports: ParkedImportRepository())

        await session.login(usernameOrEmail: "reader", password: "long-enough")

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected signed in, got \(session.state)")
        }
        XCTAssertEqual(user?.username, "reader")
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
        XCTAssertEqual(SignedInUserCache(defaults: defaults).load()?.username, "reader")
        XCTAssertTrue(isProcessing(session))
        await session.logout()
    }

    func testRegistrationCachesTheUserAndOpensSearch() async {
        let session = makeSession()

        await session.register(username: "reader", email: "reader@example.com", password: "long-enough")

        guard case .signedIn = session.state else {
            return XCTFail("Expected signed in, got \(session.state)")
        }
        XCTAssertEqual(session.signedInEntryPoint, .search)
        XCTAssertEqual(SignedInUserCache(defaults: defaults).load()?.username, "reader")
        await session.logout()
    }

    func testLogoutClearsTheCacheAndTheTokens() async {
        signIn(accessExpired: false)
        seedCache()
        let session = makeSession()
        await session.start()

        await session.logout()

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    // MARK: Launch when the Keychain can't be read

    func testAKeychainThatCannotBeReadAtLaunchKeepsTheSessionAndTheRememberedUser() async {
        signIn(accessExpired: false)
        seedCache()
        keychain.failReads(with: errSecInteractionNotAllowed) // e.g. the app was launched while the phone is locked
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected the shell, got \(session.state)")
        }
        XCTAssertEqual(user?.displayName, "Cached Reader", "neither the login screen nor a forgotten user")
        XCTAssertNil(session.errorMessage)
        XCTAssertNotNil(SignedInUserCache(defaults: defaults).load(), "the cache stays")
        XCTAssertTrue(backend.refreshRequests.isEmpty, "no exchange without a readable token")
        XCTAssertTrue(recorder.reasons.isEmpty, "nothing was cleared")

        // Once the Keychain is readable the next activation loads the profile.
        keychain.stopFailingReads()
        await waitUntil("the profile after the Keychain came back") {
            postActivation()
            return displayName(of: session) == "Reader"
        }
        XCTAssertEqual(store.refreshToken, "refresh-0", "the session was never lost")
    }

    func testAKeychainThatCannotBeReadAtLaunchWithNothingRememberedStillOpensTheShell() async {
        signIn(accessExpired: false)
        keychain.failReads(with: errSecInteractionNotAllowed)
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected the shell, got \(session.state)")
        }
        XCTAssertNil(user)
    }

    // MARK: The remembered user must be the stored session's

    func testARememberedUserThatContradictsTheStoredTokensIsIgnoredAndForgotten() async {
        // The tokens belong to user 99, the cache to user 7: a backup restored the two from different moments.
        let stranger = makeJWT(expiringAt: Date().addingTimeInterval(900), userID: 99)
        store.accessToken = stranger
        store.refreshToken = makeJWT(expiringAt: Date().addingTimeInterval(86400), kind: "refresh", userID: 99)
        backend.accept(access: stranger)
        seedCache()
        backend.setOverride { _ in .failure(.notConnectedToInternet) } // no fresh profile to correct it
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected the shell, got \(session.state)")
        }
        XCTAssertNil(user, "another account's name and owner-only controls must not show")
        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
    }

    func testARememberedUserThatMatchesTheStoredTokensIsUsed() async {
        let mine = makeJWT(expiringAt: Date().addingTimeInterval(900), userID: 7)
        store.accessToken = mine
        store.refreshToken = makeJWT(expiringAt: Date().addingTimeInterval(86400), kind: "refresh", userID: 7)
        backend.accept(access: mine)
        seedCache()
        backend.setOverride { _ in .failure(.notConnectedToInternet) }
        let session = makeSession()

        await session.start()

        XCTAssertEqual(displayName(of: session), "Cached Reader")
        XCTAssertNotNil(SignedInUserCache(defaults: defaults).load())
    }

    func testTokensThatCarryNoUserLeaveTheRememberedUserAlone() async {
        signIn(accessExpired: false) // opaque tokens: nothing to compare with
        seedCache()
        backend.setOverride { _ in .failure(.notConnectedToInternet) }
        let session = makeSession()

        await session.start()

        XCTAssertEqual(displayName(of: session), "Cached Reader")
    }

    // MARK: A slow launch refresh doesn't hold the shell up

    func testALaunchWithARememberedUserOpensTheShellWhileTheRefreshIsStillRunning() async throws {
        signIn() // the access token has expired, so requests will need the refresh too
        seedCache()
        backend.hold(path: RefreshTestPath.refresh) // the refresh gets no answer
        let session = makeSession(launchShellDelay: .milliseconds(150))

        let launch = Task { await session.start() }
        await waitUntil("the shell to open while the refresh is still running") { displayName(of: session) != nil }
        XCTAssertEqual(displayName(of: session), "Cached Reader")
        XCTAssertEqual(backend.refreshRequests.count, 1)

        // A screen of the open shell makes a request; it is rejected, and joins the refresh that is running.
        let client = makeClient()
        let screenRequest = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(2)
        XCTAssertEqual(backend.refreshRequests.count, 1, "no second refresh")
        backend.release(path: RefreshTestPath.refresh)

        let status = try await screenRequest.value
        await launch.value
        await waitUntil("the profile after the refresh") { displayName(of: session) == "Reader" }
        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testALaunchWithARememberedUserDoesNotWaitForASlowProfileEither() async {
        signIn(accessExpired: false)
        seedCache()
        backend.hold(path: RefreshTestPath.me) // the refresh is quick, the profile request gets no answer
        let session = makeSession(launchShellDelay: .milliseconds(150))

        let launch = Task { await session.start() }
        await waitUntil("the shell to open while the profile is still loading") { displayName(of: session) != nil }

        XCTAssertEqual(displayName(of: session), "Cached Reader")
        backend.release(path: RefreshTestPath.me)
        await launch.value
        await waitUntil("the profile") { displayName(of: session) == "Reader" }
        XCTAssertEqual(backend.requests(path: RefreshTestPath.me).count, 1, "the launch's own request, not a second one")
    }

    func testARefreshThatFailsAfterTheShellOpenedEndsTheSession() async {
        signIn()
        seedCache()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }
        backend.hold(path: RefreshTestPath.refresh)
        let session = makeSession(launchShellDelay: .milliseconds(150))
        let launch = Task { await session.start() }
        await waitUntil("the shell to open") { displayName(of: session) != nil }

        backend.release(path: RefreshTestPath.refresh)
        await launch.value
        await waitUntil("the session to end") {
            if case .signedOut = session.state { return true }
            return false
        }

        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
        XCTAssertNotNil(session.errorMessage, "a rejected session says so on the way out")
        XCTAssertEqual(backend.refreshRequests.count, 1)
    }

    func testAProfileThatIsRefusedAfterTheShellOpenedEndsTheSession() async {
        signIn(accessExpired: false)
        seedCache()
        backend.setOverride { $0.isMe ? .accessTokenNotValid() : nil } // the refresh works, the profile never does
        backend.hold(path: RefreshTestPath.me)
        let session = makeSession(launchShellDelay: .milliseconds(150))
        let launch = Task { await session.start() }
        await waitUntil("the shell to open") { displayName(of: session) != nil }

        backend.release(path: RefreshTestPath.me)
        await launch.value
        await waitUntil("the session to end") {
            if case .signedOut = session.state { return true }
            return false
        }

        XCTAssertNil(store.refreshToken)
    }

    func testARefreshThatFailsTemporarilyAfterTheShellOpenedKeepsTheSession() async {
        signIn()
        seedCache()
        backend.setOverride { $0.isRefresh ? .status(503) : nil }
        backend.hold(path: RefreshTestPath.refresh)
        let session = makeSession(launchShellDelay: .milliseconds(150))
        let launch = Task { await session.start() }
        await waitUntil("the shell to open") { displayName(of: session) != nil }

        backend.release(path: RefreshTestPath.refresh)
        await launch.value
        try? await Task.sleep(for: .milliseconds(200))

        XCTAssertEqual(displayName(of: session), "Cached Reader")
        XCTAssertEqual(store.refreshToken, "refresh-0")
    }

    func testAnEndedSessionIsNotEndedAgainByTheLaunchRefreshItLeftBehind() async {
        signIn()
        seedCache()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }
        backend.hold(path: RefreshTestPath.refresh)
        let session = makeSession(launchShellDelay: .milliseconds(150))
        let launch = Task { await session.start() }
        await waitUntil("the shell to open") { displayName(of: session) != nil }

        await session.logout() // the user leaves first ...
        await session.login(usernameOrEmail: "reader", password: "long-enough") // ... and signs in again
        let signedIn = store.refreshToken
        backend.release(path: RefreshTestPath.refresh) // the old launch refresh now fails
        await launch.value
        try? await Task.sleep(for: .milliseconds(200))

        XCTAssertNotNil(displayName(of: session), "the new session is not the one that was rejected")
        XCTAssertEqual(store.refreshToken, signedIn)
    }

    func testALaunchWithoutARememberedUserWaitsForTheRefresh() async {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let session = makeSession(launchShellDelay: .milliseconds(100))

        let launch = Task { await session.start() }
        try? await Task.sleep(for: .milliseconds(400))
        var stillChecking = false
        if case .checking = session.state { stillChecking = true }

        backend.release(path: RefreshTestPath.refresh)
        await launch.value

        XCTAssertTrue(stillChecking, "there is nobody to show yet, so the splash screen stays")
        XCTAssertEqual(displayName(of: session), "Reader")
    }

    // MARK: A profile that is refused after a good refresh ends the session

    func testStartSignsOutWhenTheProfileIsRejectedAfterASuccessfulRefresh() async {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImport()
        backend.setOverride { $0.isMe ? .accessTokenNotValid() : nil } // refreshes fine, but never gets a profile
        let session = makeSession()

        await session.start()

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertNotNil(session.errorMessage)
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
        XCTAssertNil(defaults.string(forKey: "letterboxdImport.taskId"))
        XCTAssertEqual(recorder.reasons, [.serverRejection])
    }

    // MARK: Every way the session can end clears the import jobs too

    func testUserSignOutClearsRunningImports() async {
        signIn(accessExpired: false)
        seedRunningImports()
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()
        XCTAssertTrue(isProcessing(session))

        await session.logout()

        assertImportJobsCleared(session)
    }

    func testARejectedLaunchRefreshClearsRunningImports() async {
        signIn()
        seedRunningImports()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }
        let session = makeSession()

        await session.start()

        assertImportJobsCleared(session)
    }

    func testARejectedProfileAfterAGoodRefreshClearsRunningImports() async {
        signIn(accessExpired: false)
        seedRunningImports()
        backend.setOverride { $0.isMe ? .accessTokenNotValid() : nil }
        let session = makeSession()

        await session.start()

        assertImportJobsCleared(session)
    }

    func testARejectedBackgroundProfileClearsRunningImports() async {
        signIn()
        seedCache()
        seedRunningImports()
        let refreshes = RefreshCounter()
        backend.setOverride { request in
            guard request.isRefresh else { return nil }
            return refreshes.increment() == 1 ? .failure(.notConnectedToInternet) : .tokenNotValid()
        }
        let session = makeSession()

        await session.start()
        await waitUntil("the session to end") {
            if case .signedOut = session.state { return true }
            return false
        }

        assertImportJobsCleared(session)
    }

    func testAnImportThatFindsTheSessionOverClearsRunningImportsToo() async {
        signIn(accessExpired: false)
        seedRunningImports()
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()

        session.letterboxdImportCoordinator.onUnauthorized?()
        await waitUntil("the session to end") {
            if case .signedOut = session.state { return true }
            return false
        }

        assertImportJobsCleared(session)
    }

    // MARK: Signing out happens once, at once, and can't undo a sign-in

    func testSigningOutTwiceAtOnceSignsOutOnceAndRevokesOnce() async {
        signIn(accessExpired: false)
        seedCache()
        let session = makeSession()
        await session.start()
        let refreshToken = store.refreshToken

        async let first: Void = session.logout()
        async let second: Void = session.logout() // e.g. a double tap on Sign Out
        _ = await (first, second)

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertNil(store.refreshToken)
        await waitUntil("the revocation") { !backend.requests(path: RefreshTestPath.logout).isEmpty }
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(backend.requests(path: RefreshTestPath.logout).map(\.refreshToken), [refreshToken])
        XCTAssertEqual(recorder.reasons, [.userSignOut], "logged once")
    }

    func testASecondSignOutWhileOneIsUnderWayDoesNotStartAnother() async {
        signIn(accessExpired: false)
        let auth = HangingSignOutAuthRepository()
        let session = makeSession(auth: auth)
        await session.start()

        let first = Task { await session.logout() }
        await waitUntil("the first sign-out to start") { auth.logoutCallCount == 1 }
        let second = Task { await session.logout() } // a double tap: there is nothing left for it to do
        try? await Task.sleep(for: .milliseconds(100))

        XCTAssertEqual(auth.logoutCallCount, 1)
        await auth.gate.open()
        await first.value
        await second.value
        XCTAssertEqual(auth.logoutCallCount, 1)
    }

    func testASignOutAfterTheSessionEndedNeverReachesTheRepositoryAgain() async {
        signIn(accessExpired: false)
        let auth = HangingSignOutAuthRepository()
        let session = makeSession(auth: auth)
        await session.start()
        await auth.gate.open() // sign-outs complete at once

        await session.logout()
        await session.logout()
        await session.logout()

        XCTAssertEqual(auth.logoutCallCount, 1, "a late onUnauthorized finds nothing left to sign out of")
    }

    func testAnotherSignOutAfterTheSessionEndedDoesNothing() async {
        signIn(accessExpired: false)
        let session = makeSession()
        await session.start()
        await session.logout()
        await waitUntil("the revocation") { !backend.requests(path: RefreshTestPath.logout).isEmpty }
        try? await Task.sleep(for: .milliseconds(50))
        let requests = backend.requests.count

        await session.logout() // a late onUnauthorized, say
        try? await Task.sleep(for: .milliseconds(100))

        XCTAssertEqual(backend.requests.count, requests)
    }

    func testAnImmediateSignInIsNotUndoneByTheRevocationOfTheSessionBeforeIt() async {
        let session = makeSession()
        await session.login(usernameOrEmail: "reader", password: "long-enough") // refresh-1
        backend.hold(path: RefreshTestPath.logout) // the server is slow to answer the revocation

        await session.logout()
        XCTAssertNil(store.refreshToken, "the session is over on the device already, revoked or not")
        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        await session.login(usernameOrEmail: "reader", password: "long-enough") // refresh-2
        backend.release(path: RefreshTestPath.logout)
        await waitUntil("the revocation") { !backend.requests(path: RefreshTestPath.logout).isEmpty }
        try? await Task.sleep(for: .milliseconds(100))

        XCTAssertEqual(store.refreshToken, "refresh-2")
        XCTAssertEqual(store.accessToken, "access-2")
        XCTAssertNotNil(displayName(of: session))
        XCTAssertEqual(backend.requests(path: RefreshTestPath.logout).map(\.refreshToken), ["refresh-1"])
        XCTAssertEqual(backend.liveRefreshTokens, ["refresh-2"], "the old session is revoked, the new one alive")
    }

    func testARevocationRefusedByAnOlderServerNeverBorrowsTheNextSessionsToken() async {
        backend.requireAuthenticatedLogout() // wants an access token, which a signed-out session no longer has
        let session = makeSession()
        await session.login(usernameOrEmail: "reader", password: "long-enough") // refresh-1
        backend.hold(path: RefreshTestPath.logout)

        await session.logout()
        await session.login(usernameOrEmail: "reader", password: "long-enough") // refresh-2, straight away
        backend.release(path: RefreshTestPath.logout) // now the refusal arrives
        try? await Task.sleep(for: .milliseconds(200))

        XCTAssertEqual(
            backend.requests(path: RefreshTestPath.logout).map(\.bearer),
            [nil],
            "the revocation isn't retried, least of all with the bearer of the session that started since"
        )
        XCTAssertEqual(store.refreshToken, "refresh-2")
        XCTAssertEqual(store.accessToken, "access-2")
        XCTAssertNotNil(displayName(of: session))
        XCTAssertTrue(backend.refreshRequests.isEmpty)
    }

    func testNothingRevivesASessionThatIsEnding() async {
        signIn(accessExpired: false)
        seedCache()
        let auth = HangingSignOutAuthRepository()
        let session = makeSession(auth: auth)
        await session.start()
        XCTAssertEqual(displayName(of: session), "Reader")

        let signingOut = Task { await session.logout() }
        await waitUntil("the sign-out to start") { auth.logoutCallCount == 1 }
        // The session is half way through ending: still shown as signed in, but already cancelling its work.
        let profileRequests = backend.requests(path: RefreshTestPath.me).count
        session.retryProfileIfNeeded() // what an app activation queues
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(
            backend.requests(path: RefreshTestPath.me).count,
            profileRequests,
            "no profile request may start while the session is ending"
        )

        await auth.gate.open()
        await signingOut.value
        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
    }

    // MARK: The shell can't resume an import before the network is known to work

    func testTheShellCannotResumeImportsBeforeARequestHasGotThrough() async {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImports()
        backend.setOverride { _ in .failure(.notConnectedToInternet) }
        let session = makeSession(imports: ParkedImportRepository())
        await session.start() // offline: the shell opens without a fresh profile

        // What AppShellView does whenever the scene becomes active.
        session.letterboxdImportCoordinator.resumeIfNeeded()
        session.storygraphImportCoordinator.resumeIfNeeded()
        session.goodreadsImportCoordinator.resumeIfNeeded()
        session.myAnimeListImportCoordinator.resumeIfNeeded()

        XCTAssertEqual(session.letterboxdImportCoordinator.phase, .idle)
        XCTAssertEqual(session.storygraphImportCoordinator.phase, .idle)
        XCTAssertEqual(session.goodreadsImportCoordinator.phase, .idle)
        XCTAssertEqual(session.myAnimeListImportCoordinator.phase, .idle)

        // Once the profile gets through, the session resumes them itself.
        backend.setOverride(nil)
        await waitUntil("the profile") {
            postActivation()
            return displayName(of: session) == "Reader"
        }
        XCTAssertTrue(isProcessing(session))
        session.storygraphImportCoordinator.resumeIfNeeded() // and now the shell may too
        XCTAssertNotEqual(session.storygraphImportCoordinator.phase, .idle)
        XCTAssertTrue(session.myAnimeListImportCoordinator.hasProcessingJob, "the session resumed MyAnimeList too")
    }

    func testTheShellCannotResumeImportsOfASignedOutSession() async {
        signIn(accessExpired: false)
        seedRunningImports()
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()
        await session.logout()
        seedRunningImports() // left behind by something else

        session.letterboxdImportCoordinator.resumeIfNeeded()

        XCTAssertEqual(session.letterboxdImportCoordinator.phase, .idle)
    }

    // MARK: A logout during the launch is not undone by the launch

    /// A session whose launch is waiting on a profile request that ignores cancellation, so the launch can finish
    /// after the session has ended. The gate opens the profile request.
    private func makeSessionWithAProfileOnHold(
        failure: Error? = nil
    ) -> (session: AppSession, profile: GatedProfileRepository, gate: RefreshGate) {
        let gate = RefreshGate()
        let profile = GatedProfileRepository(
            base: AppRepositories.live(client: makeClient()).profile,
            profile: Self.makeProfile(),
            gate: gate,
            failure: failure
        )
        return (makeSession(profile: profile), profile, gate)
    }

    func testALogoutDuringTheLaunchIsNotUndoneByAProfileThatArrivesLate() async {
        signIn(accessExpired: false)
        let (session, profile, gate) = makeSessionWithAProfileOnHold()
        let launch = Task { await session.start() }
        await waitUntil("the launch to wait for the profile") { profile.calls.value == 1 }

        await session.logout() // while the splash screen is still up
        await gate.open() // the profile arrives after the session ended
        await launch.value

        guard case .signedOut = session.state else {
            return XCTFail("A late profile must not sign the user back in, got \(session.state)")
        }
        XCTAssertNil(SignedInUserCache(defaults: defaults).load(), "nor be remembered")
        XCTAssertNil(store.refreshToken)
    }

    func testALogoutDuringTheLaunchIsNotUndoneByALaunchThatFailsLate() async {
        signIn(accessExpired: false)
        let (session, profile, gate) = makeSessionWithAProfileOnHold(failure: URLError(.notConnectedToInternet))
        let launch = Task { await session.start() }
        await waitUntil("the launch to wait for the profile") { profile.calls.value == 1 }

        await session.logout()
        await gate.open() // the launch now finds the network down
        await launch.value
        try? await Task.sleep(for: .milliseconds(100))

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertEqual(profile.calls.value, 1, "the abandoned launch starts no profile retry")
    }

    func testALogoutDuringTheLaunchLeavesTheNextSessionAloneWhenTheLaunchIsRejectedLate() async {
        signIn(accessExpired: false)
        let (session, profile, gate) = makeSessionWithAProfileOnHold(failure: APIError.unauthorized)
        let launch = Task { await session.start() }
        await waitUntil("the launch to wait for the profile") { profile.calls.value == 1 }

        await session.logout()
        await session.login(usernameOrEmail: "reader", password: "long-enough") // somebody signs in at once
        let signedIn = store.refreshToken
        await gate.open() // the first launch is now told its session was rejected
        await launch.value
        try? await Task.sleep(for: .milliseconds(100))

        guard case .signedIn = session.state else {
            return XCTFail("The new session must survive the old launch's verdict, got \(session.state)")
        }
        XCTAssertEqual(store.refreshToken, signedIn)
        XCTAssertNotNil(SignedInUserCache(defaults: defaults).load())
    }

    // MARK: Any authenticated response confirms the network

    func testAnAuthenticatedResponseResumesImportsEvenWhenTheProfileKeepsFailing() async throws {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImport()
        backend.setOverride { $0.isMe ? .failure(.notConnectedToInternet) : nil } // /me/ never gets through
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()
        XCTAssertFalse(isProcessing(session), "nothing has got through yet")

        _ = try await fetchHealth(makeClient()) // a screen's request succeeds

        await waitUntil("the import to resume") { isProcessing(session) }
    }

    func testTheProfileKeepsBeingRetriedAfterAnotherResponseConfirmedTheNetwork() async throws {
        signIn(accessExpired: false)
        seedCache()
        backend.setOverride { $0.isMe ? .failure(.notConnectedToInternet) : nil }
        let session = makeSession()
        await session.start()
        _ = try await fetchHealth(makeClient())
        backend.setOverride(nil)

        await waitUntil("the profile after the app became active") {
            postActivation()
            return displayName(of: session) == "Reader"
        }
    }

    func testOnlyAnAuthenticatedSuccessConfirmsTheNetwork() async throws {
        signIn(accessExpired: false)
        seedCache()
        seedRunningImport()
        backend.setOverride { $0.isMe ? .failure(.notConnectedToInternet) : nil }
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()
        let client = makeClient()

        // An unauthenticated call that works ...
        let _: AuthTokenResponse = try await client.post(
            "/auth/login/",
            body: LoginRequest(usernameOrEmail: "reader", password: "long-enough")
        )
        // ... and an authenticated one that doesn't.
        backend.setOverride { $0.isMe || $0.path == RefreshTestPath.health ? .status(500) : nil }
        _ = await thrownError { _ = try await self.fetchHealth(client) }
        try? await Task.sleep(for: .milliseconds(100))

        XCTAssertFalse(isProcessing(session), "neither says the session's network works")
    }

    func testAFreshSignInNeedsNoProfileRetry() async {
        for register in [false, true] {
            let session = makeSession()
            if register {
                await session.register(username: "reader", email: "reader@example.com", password: "long-enough")
            } else {
                await session.login(usernameOrEmail: "reader", password: "long-enough")
            }
            let before = backend.requests(path: RefreshTestPath.me).count

            session.retryProfileIfNeeded() // the user the server just returned is as fresh as a profile
            try? await Task.sleep(for: .milliseconds(50))

            XCTAssertEqual(backend.requests(path: RefreshTestPath.me).count, before, "register: \(register)")
            await session.logout()
        }
    }

    func testAResponseAfterTheSessionEndedResumesNothing() async throws {
        signIn(accessExpired: false)
        seedCache()
        backend.setOverride { $0.isMe ? .failure(.notConnectedToInternet) : nil }
        let session = makeSession(imports: ParkedImportRepository())
        await session.start()
        await session.logout()
        signIn(accessExpired: false) // somebody else's tokens, say, and a job of theirs
        seedRunningImport()

        _ = try await fetchHealth(makeClient())
        try? await Task.sleep(for: .milliseconds(100))

        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)")
        }
        XCTAssertEqual(session.letterboxdImportCoordinator.phase, .idle)
    }

    // MARK: Helpers

    private func assertLaunchRefreshes(
        accessToken: String?,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        store.accessToken = accessToken
        store.refreshToken = "refresh-0"
        backend.accept(access: accessToken, refresh: "refresh-0")
        let session = makeSession()

        await session.start()

        guard case let .signedIn(user) = session.state else {
            return XCTFail("Expected signed in, got \(session.state)", file: file, line: line)
        }
        XCTAssertEqual(user?.displayName, "Reader", file: file, line: line)
        XCTAssertEqual(backend.refreshRequests.count, 1, file: file, line: line)
        XCTAssertEqual(store.refreshToken, "refresh-1", file: file, line: line)
    }

    private func seedCache() {
        SignedInUserCache(defaults: defaults).save(cachedUser)
    }

    /// A Letterboxd import left running by an earlier launch. The session resumes it once it knows the network works.
    private func seedRunningImport() {
        defaults.set("task-1", forKey: "letterboxdImport.taskId")
        defaults.set(ImportMode.new.rawValue, forKey: "letterboxdImport.mode")
        defaults.set(Date().timeIntervalSince1970, forKey: "letterboxdImport.startedAt")
    }

    /// Running Letterboxd, StoryGraph, Goodreads and MyAnimeList imports left by an earlier launch.
    private func seedRunningImports() {
        for service in ["letterboxd", "storygraph", "goodreads", "myAnimeList"] {
            defaults.set("task-1", forKey: "\(service)Import.taskId")
            defaults.set(ImportMode.new.rawValue, forKey: "\(service)Import.mode")
            defaults.set(Date().timeIntervalSince1970, forKey: "\(service)Import.startedAt")
        }
    }

    private func assertImportJobsCleared(_ session: AppSession, file: StaticString = #filePath, line: UInt = #line) {
        guard case .signedOut = session.state else {
            return XCTFail("Expected signed out, got \(session.state)", file: file, line: line)
        }
        for service in ["letterboxd", "storygraph", "goodreads", "myAnimeList"] {
            XCTAssertNil(defaults.string(forKey: "\(service)Import.taskId"), "\(service) job", file: file, line: line)
        }
        XCTAssertEqual(session.letterboxdImportCoordinator.phase, .idle, file: file, line: line)
        XCTAssertFalse(isProcessing(session), "no import keeps running", file: file, line: line)
    }

    private func isProcessing(_ session: AppSession) -> Bool {
        if case .processing = session.letterboxdImportCoordinator.phase { return true }
        return false
    }

    private func displayName(of session: AppSession) -> String? {
        if case let .signedIn(user) = session.state { return user?.displayName }
        return nil
    }

    private func postActivation() {
        NotificationCenter.default.post(name: UIApplication.didBecomeActiveNotification, object: nil)
    }
}

// MARK: - Signing in and out at the service

@MainActor
final class AuthServiceTests: RefreshTestCase {
    func testLoginStoresTheRefreshTokenBeforeTheAccessToken() async throws {
        let user = try await AuthService(client: makeClient()).login(usernameOrEmail: "reader", password: "long-enough")

        XCTAssertEqual(user.username, "reader")
        XCTAssertEqual(keychain.writes, [InMemoryKeychain.refreshAccount, InMemoryKeychain.accessAccount])
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testLoginFailsWhenTheKeychainCannotStoreTheSession() async {
        keychain.failWrites(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        let error = await thrownError {
            _ = try await AuthService(client: makeClient()).login(usernameOrEmail: "reader", password: "long-enough")
        }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .keychain(errSecInteractionNotAllowed))
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testRegistrationStoresTheSession() async throws {
        let user = try await AuthService(client: makeClient()).register(
            username: "reader",
            email: "reader@example.com",
            password: "long-enough"
        )

        XCTAssertEqual(user.username, "reader")
        XCTAssertEqual(keychain.writes, [InMemoryKeychain.refreshAccount, InMemoryKeychain.accessAccount])
    }

    func testLoginWhileAnExchangeForTheOldSessionIsInFlightIsNotOverwrittenByIt() async throws {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()
        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)

        // Someone signs in (another account, say) while the old session's exchange is still in flight.
        _ = try await AuthService(client: client).login(usernameOrEmail: "reader", password: "long-enough")
        let signedInAccess = store.accessToken
        let signedInRefresh = store.refreshToken
        backend.release(path: RefreshTestPath.refresh)
        _ = await request.result

        XCTAssertEqual(store.accessToken, signedInAccess, "the late exchange must not overwrite the new session")
        XCTAssertEqual(store.refreshToken, signedInRefresh)
    }

    func testLogoutEndsTheSessionAtOnceAndRevokesItInTheBackground() async {
        signIn(accessExpired: false)
        backend.hold(path: RefreshTestPath.logout) // the server doesn't answer the revocation
        let started = Date()

        let revocation = await AuthService(client: makeClient()).logout()

        XCTAssertLessThan(Date().timeIntervalSince(started), 2, "signing out doesn't wait for the server")
        XCTAssertNil(store.accessToken, "the session is over before the server has answered")
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(recorder.reasons, [.userSignOut])
        await waitUntil("the revocation to reach the server") {
            !backend.requests(path: RefreshTestPath.logout).isEmpty
        }
        XCTAssertEqual(backend.requests(path: RefreshTestPath.logout).map(\.refreshToken), ["refresh-0"])
        backend.release(path: RefreshTestPath.logout)
        await revocation.value
        XCTAssertTrue(backend.liveRefreshTokens.isEmpty)
    }

    func testLogoutRevokesWithNothingButTheRefreshTokenAndNeverRefreshes() async {
        signIn() // the access token has expired: an authenticated call would have to refresh first

        await AuthService(client: makeClient()).logout().value

        let logouts = backend.requests(path: RefreshTestPath.logout)
        XCTAssertEqual(logouts.map(\.refreshToken), ["refresh-0"])
        XCTAssertEqual(logouts.map(\.bearer), [nil], "unauthenticated")
        XCTAssertTrue(backend.refreshRequests.isEmpty, "no refresh dance on the way out")
        XCTAssertTrue(backend.liveRefreshTokens.isEmpty, "and the token is revoked all the same")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testLogoutAgainstAnOlderServerThatWantsAnAccessTokenIsHarmless() async {
        backend.requireAuthenticatedLogout()
        signIn(accessExpired: false)

        await AuthService(client: makeClient()).logout().value

        XCTAssertEqual(backend.requests(path: RefreshTestPath.logout).count, 1, "one attempt: no refresh, no retry")
        XCTAssertTrue(backend.refreshRequests.isEmpty)
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(recorder.reasons, [.userSignOut], "the refused revocation isn't another session ending")
        XCTAssertEqual(backend.liveRefreshTokens, ["refresh-0"], "that server can't revoke it; it lapses by itself")
    }

    func testALogoutThatCouldNotReadTheKeychainStillEndsTheSession() async {
        signIn(accessExpired: false)
        // The capture at the start of the sign-out fails; the Keychain works again straight after.
        keychain.failNextRead(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        await AuthService(client: makeClient()).logout().value

        XCTAssertNil(store.accessToken, "an unreadable token is not an absent one: the session must not stay behind")
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.logout).map(\.refreshToken), ["refresh-0"], "and it is revoked")
        XCTAssertEqual(recorder.reasons, [.userSignOut])
    }

    func testLogoutWithoutAStoredRefreshTokenJustClears() async {
        store.accessToken = "access-0"

        await AuthService(client: makeClient()).logout().value

        XCTAssertTrue(backend.requests(path: RefreshTestPath.logout).isEmpty)
        XCTAssertNil(store.accessToken)
    }

    func testLogoutWithNothingStoredDoesNothing() async {
        await AuthService(client: makeClient()).logout().value

        XCTAssertTrue(backend.requests.isEmpty)
        XCTAssertTrue(recorder.reasons.isEmpty, "no session ended")
    }

    func testLogoutIgnoresAFailingServer() async {
        signIn(accessExpired: false)
        backend.setOverride { $0.isLogout ? .status(503) : nil }

        await AuthService(client: makeClient()).logout().value

        XCTAssertEqual(backend.requests(path: RefreshTestPath.logout).count, 1, "not retried")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testLogoutIgnoresMissingNetwork() async {
        signIn(accessExpired: false)
        backend.setOverride { _ in .failure(.notConnectedToInternet) }

        await AuthService(client: makeClient()).logout().value

        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testARevocationThatGetsNoAnswerGivesUpAfterTheTimeout() async {
        signIn(accessExpired: false)
        backend.hold(path: RefreshTestPath.logout) // the server never answers
        var service = AuthService(client: makeClient())
        service.revocationTimeout = .milliseconds(200)
        let started = Date()

        let revocation = await service.logout()
        XCTAssertNil(store.refreshToken)
        await revocation.value

        XCTAssertLessThan(Date().timeIntervalSince(started), 3, "the revocation gave up instead of waiting for the server")
        backend.release(path: RefreshTestPath.logout)
    }

    func testLogoutWhileARefreshIsInFlightLeavesNoSessionAndRevokesWhatTheExchangeMinted() async {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()
        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)

        // The user signs out while the exchange the health request started is still in flight.
        await AuthService(client: client).logout().value
        backend.release(path: RefreshTestPath.refresh)
        _ = await request.result

        XCTAssertNil(store.accessToken, "the exchange that finished after sign-out must not resurrect the session")
        XCTAssertNil(store.refreshToken)
        await waitUntil("the token the exchange minted to be revoked") { backend.liveRefreshTokens.isEmpty }
    }

    func testALogoutThatARefreshOvertookStillEndsTheSessionAndRevokesBothTokens() async throws {
        signIn()
        _ = try await fetchHealth(makeClient()) // refresh-0 becomes refresh-1
        XCTAssertEqual(store.refreshToken, "refresh-1")
        // The sign-out read refresh-0 just before that rotation landed.
        keychain.returnOnNextRead(of: InMemoryKeychain.refreshAccount, "refresh-0")

        await AuthService(client: makeClient()).logout().value

        XCTAssertNil(store.accessToken, "the rotation did not make the sign-out skip the session")
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(
            Set(backend.requests(path: RefreshTestPath.logout).compactMap(\.refreshToken)),
            ["refresh-0", "refresh-1"],
            "both the token that was read and the one that replaced it are revoked"
        )
        XCTAssertTrue(backend.liveRefreshTokens.isEmpty)
    }
}

/// An `AuthRepository` whose sign-out hangs until its gate opens, which holds a session half way through ending.
@MainActor
private final class HangingSignOutAuthRepository: AuthRepository {
    let gate = RefreshGate()
    private(set) var logoutCallCount = 0

    var hasStoredTokens: Bool { true }

    func login(usernameOrEmail: String, password: String) async throws -> AuthUser {
        AuthUser(id: 7, username: usernameOrEmail, displayName: usernameOrEmail, isPrivate: false, avatarUrl: nil)
    }

    func register(username: String, email: String, password: String) async throws -> AuthUser {
        AuthUser(id: 7, username: username, displayName: username, isPrivate: false, avatarUrl: nil)
    }

    func refresh() async throws {}

    func logout() async {
        logoutCallCount += 1
        await gate.wait()
    }
}

// MARK: - Import coordinators

/// The import coordinators behind one set of closures, so each test runs against all of them.
@MainActor
final class ImportCoordinatorCancellationTests: XCTestCase {
    private struct Harness {
        let name: String
        let fileExtension: String
        let startImport: (URL) -> Void
        let cancelUpload: () -> Void
        let resume: () -> Void
        let checkStatus: () -> Void
        let clear: () -> Void
        let isIdle: () -> Bool
        let isFailed: () -> Bool
        let isUploading: () -> Bool
        let isProcessing: () -> Bool
        let isChecking: () -> Bool
    }

    private var suite: String!
    private var defaults: UserDefaults!
    private var files: [URL] = []

    override func setUp() {
        super.setUp()
        suite = "spine.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        files.forEach { try? FileManager.default.removeItem(at: $0) }
        super.tearDown()
    }

    private func harnesses(_ repository: ImportRepository) -> [Harness] {
        let letterboxd = LetterboxdImportCoordinator(importRepository: repository, defaults: defaults)
        let storyGraph = StoryGraphImportCoordinator(importRepository: repository, defaults: defaults)
        let goodreads = GoodreadsImportCoordinator(importRepository: repository, defaults: defaults)
        let myAnimeList = MyAnimeListImportCoordinator(importRepository: repository, defaults: defaults)
        return [
            Harness(
                name: "letterboxd",
                fileExtension: "zip",
                startImport: { letterboxd.startImport(fileURL: $0, mode: .new) },
                cancelUpload: { letterboxd.cancelUploadFailure() },
                resume: { letterboxd.resumeIfNeeded() },
                checkStatus: { letterboxd.checkStatusOnce() },
                clear: { letterboxd.clearFinishedJob() },
                isIdle: { letterboxd.phase == .idle },
                isFailed: { if case .failed = letterboxd.phase { true } else { false } },
                isUploading: { if case .uploading = letterboxd.phase { true } else { false } },
                isProcessing: { letterboxd.hasProcessingJob },
                isChecking: { letterboxd.isCheckingStatus }
            ),
            Harness(
                name: "storygraph",
                fileExtension: "csv",
                startImport: { storyGraph.startImport(fileURL: $0, mode: .new) },
                cancelUpload: { storyGraph.cancelUploadFailure() },
                resume: { storyGraph.resumeIfNeeded() },
                checkStatus: { storyGraph.checkStatusOnce() },
                clear: { storyGraph.clearFinishedJob() },
                isIdle: { storyGraph.phase == .idle },
                isFailed: { if case .failed = storyGraph.phase { true } else { false } },
                isUploading: { if case .uploading = storyGraph.phase { true } else { false } },
                isProcessing: { storyGraph.hasProcessingJob },
                isChecking: { storyGraph.isCheckingStatus }
            ),
            Harness(
                name: "goodreads",
                fileExtension: "csv",
                startImport: { goodreads.startImport(fileURL: $0, mode: .new) },
                cancelUpload: { goodreads.cancelUploadFailure() },
                resume: { goodreads.resumeIfNeeded() },
                checkStatus: { goodreads.checkStatusOnce() },
                clear: { goodreads.clearFinishedJob() },
                isIdle: { goodreads.phase == .idle },
                isFailed: { if case .failed = goodreads.phase { true } else { false } },
                isUploading: { if case .uploading = goodreads.phase { true } else { false } },
                isProcessing: { goodreads.hasProcessingJob },
                isChecking: { goodreads.isCheckingStatus }
            ),
            Harness(
                name: "myAnimeList",
                fileExtension: "gz",
                startImport: { myAnimeList.startImport(fileURL: $0, mode: .new) },
                cancelUpload: { myAnimeList.cancelUploadFailure() },
                resume: { myAnimeList.resumeIfNeeded() },
                checkStatus: { myAnimeList.checkStatusOnce() },
                clear: { myAnimeList.clearFinishedJob() },
                isIdle: { myAnimeList.phase == .idle },
                isFailed: { if case .failed = myAnimeList.phase { true } else { false } },
                isUploading: { if case .uploading = myAnimeList.phase { true } else { false } },
                isProcessing: { myAnimeList.hasProcessingJob },
                isChecking: { myAnimeList.isCheckingStatus }
            ),
        ]
    }

    @nonobjc
    private func waitUntil(_ description: String, timeout: TimeInterval = 5, _ condition: () -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            guard Date() < deadline else { return XCTFail("Timed out waiting for \(description)") }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    private func seedRunningJob(_ harness: Harness) {
        defaults.set("task-1", forKey: "\(harness.name)Import.taskId")
        defaults.set(ImportMode.new.rawValue, forKey: "\(harness.name)Import.mode")
        defaults.set(Date().timeIntervalSince1970, forKey: "\(harness.name)Import.startedAt")
    }

    private func makeFile(_ harness: Harness) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("spine-test-\(UUID().uuidString)")
            .appendingPathExtension(harness.fileExtension)
        try Data("title,rating\n".utf8).write(to: url)
        files.append(url)
        return url
    }

    func testClearingARunningImportLeavesItIdleNotFailed() async {
        let repository = StubImportRepository(status: .hang)
        for harness in harnesses(repository) {
            seedRunningJob(harness)
            let polls = repository.statusCalls.value
            harness.resume()
            XCTAssertTrue(harness.isProcessing(), harness.name)
            await waitUntil("\(harness.name) to start polling") { repository.statusCalls.value > polls }

            harness.clear() // cancels the poll, which reports its cancellation
            try? await Task.sleep(for: .milliseconds(100))

            XCTAssertTrue(harness.isIdle(), "\(harness.name): cancelling a poll isn't an import that failed")
        }
    }

    func testCancellingAnUploadLeavesItIdleNotFailed() async throws {
        for harness in harnesses(StubImportRepository(upload: .hang)) {
            harness.startImport(try makeFile(harness))
            await waitUntil("\(harness.name) to be uploading") { harness.isUploading() }

            harness.cancelUpload()
            try? await Task.sleep(for: .milliseconds(100))

            XCTAssertTrue(harness.isIdle(), "\(harness.name)")
        }
    }

    func testClearingAnUploadLeavesItIdleNotFailed() async throws {
        for harness in harnesses(StubImportRepository(upload: .hang)) {
            harness.startImport(try makeFile(harness))
            await waitUntil("\(harness.name) to be uploading") { harness.isUploading() }

            harness.clear()
            try? await Task.sleep(for: .milliseconds(100))

            XCTAssertTrue(harness.isIdle(), "\(harness.name)")
        }
    }

    func testAStatusCheckThatIsCancelledIsNotAFailureEither() async {
        // What the replay guard produces for the status check of a session that has ended.
        for harness in harnesses(StubImportRepository(status: .fail(CancellationError()))) {
            seedRunningJob(harness)

            harness.checkStatus()
            await waitUntil("\(harness.name) to be checking") { harness.isChecking() || harness.isIdle() }
            try? await Task.sleep(for: .milliseconds(100))

            XCTAssertFalse(harness.isFailed(), "\(harness.name)")
            XCTAssertFalse(harness.isChecking(), "\(harness.name): the check is over")
        }
    }

    func testARealFailureStillShowsAsFailed() async {
        for harness in harnesses(StubImportRepository(status: .fail(URLError(.notConnectedToInternet)))) {
            seedRunningJob(harness)
            harness.resume()

            await waitUntil("\(harness.name) to fail") { harness.isFailed() }

            harness.clear()
        }
    }
}

// MARK: - Small units

final class JWTExpiryTests: XCTestCase {
    func testReadsTheExpirationClaim() {
        let expiry = Date(timeIntervalSince1970: 1_900_000_000)

        XCTAssertEqual(JWTExpiry.expiration(of: makeJWT(expiringAt: expiry)), expiry)
    }

    func testReadsPayloadsWhateverTheirBase64Padding() {
        let expiry = Date(timeIntervalSince1970: 1_900_000_000)
        for length in 1 ... 8 {
            let token = makeJWT(expiringAt: expiry, kind: String(repeating: "k", count: length))
            XCTAssertEqual(JWTExpiry.expiration(of: token), expiry, "kind of length \(length)")
        }
    }

    func testReadsAFractionalExpiration() {
        let payload = Data(#"{"exp":1900000000.5}"#.utf8).base64EncodedString()
            .replacingOccurrences(of: "=", with: "")

        XCTAssertEqual(
            JWTExpiry.expiration(of: "header.\(payload).signature"),
            Date(timeIntervalSince1970: 1_900_000_000.5)
        )
    }

    func testReadsPayloadsThatUseTheURLSafeAlphabet() {
        // Standard base64 of this payload has "+" and "/"; in base64url they are "-" and "_".
        let json = #"{"exp":1900000000,"user_id":7,"note":">>>>>>>>>>>>????????????"}"#
        let standard = Data(json.utf8).base64EncodedString()
        XCTAssertTrue(standard.contains("+") && standard.contains("/"), "the premise of this test")
        let urlSafe = standard
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        XCTAssertTrue(urlSafe.contains("-") && urlSafe.contains("_"))
        let token = "header.\(urlSafe).signature"

        XCTAssertEqual(JWTExpiry.expiration(of: token), Date(timeIntervalSince1970: 1_900_000_000))
        XCTAssertEqual(JWTExpiry.userID(of: token), 7)
    }

    func testReadsTheUserIDClaim() {
        let expiry = Date(timeIntervalSince1970: 1_900_000_000)

        XCTAssertEqual(JWTExpiry.userID(of: makeJWT(expiringAt: expiry, userID: 7)), 7)
        XCTAssertEqual(JWTExpiry.userID(of: makeJWT(expiringAt: expiry, kind: "refresh", userID: 123_456)), 123_456)
        XCTAssertNil(JWTExpiry.userID(of: makeJWT(expiringAt: expiry)), "a token without the claim has no user")
    }

    func testAStringOfDigitsIsAUserID() {
        XCTAssertEqual(JWTExpiry.userID(of: "header.\(encoded(#"{"user_id":"42"}"#)).signature"), 42)
    }

    func testAnythingElseIsNotAUserID() {
        let tokens = [
            "",
            "not-a-jwt",
            "only.two",
            "header.!!!.signature",
            "header.\(encoded("not json")).signature",
            "header.\(encoded("[7]")).signature",
            "header.\(encoded(#"{"user_id":7.5}"#)).signature",
            "header.\(encoded(#"{"user_id":"abc"}"#)).signature",
            "header.\(encoded(#"{"user_id":null}"#)).signature",
            "header.\(encoded(#"{"user_id":[7]}"#)).signature",
            "header.\(encoded(#"{"user_id":{"id":7}}"#)).signature",
            "header.\(encoded(#"{"sub":"7"}"#)).signature",
        ]
        for token in tokens {
            XCTAssertNil(JWTExpiry.userID(of: token), token)
        }
    }

    private func encoded(_ json: String) -> String {
        Data(json.utf8).base64EncodedString().replacingOccurrences(of: "=", with: "")
    }

    func testAnythingElseIsNotAnExpiration() {
        func payload(_ json: String) -> String {
            "header.\(Data(json.utf8).base64EncodedString().replacingOccurrences(of: "=", with: "")).signature"
        }
        let tokens = [
            "",
            "not-a-jwt",
            "only.two",
            "one.too.many.parts",
            "header.!!!.signature",
            payload("not json"),
            payload("[]"),
            payload(#"{"sub":"7"}"#),
            payload(#"{"exp":"1900000000"}"#),
            payload(#"{"exp":null}"#),
        ]
        for token in tokens {
            XCTAssertNil(JWTExpiry.expiration(of: token), token)
        }
    }
}

@MainActor
final class SignedInUserCacheTests: XCTestCase {
    private var suite: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suite = "spine.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func testRemembersAndForgetsTheUser() {
        let cache = SignedInUserCache(defaults: defaults)
        XCTAssertNil(cache.load())

        cache.save(AuthUser(id: 3, username: "ada", displayName: "Ada", isPrivate: true, avatarUrl: "https://example.com/a.png"))
        let loaded = cache.load()
        XCTAssertEqual(loaded?.id, 3)
        XCTAssertEqual(loaded?.username, "ada")
        XCTAssertEqual(loaded?.displayName, "Ada")
        XCTAssertEqual(loaded?.isPrivate, true)
        XCTAssertEqual(loaded?.avatarUrl, "https://example.com/a.png")

        cache.clear()
        XCTAssertNil(cache.load())
    }

    func testUnreadableDataIsNoUser() {
        defaults.set(Data("garbage".utf8), forKey: "spine.signedInUser.v1")

        XCTAssertNil(SignedInUserCache(defaults: defaults).load())
    }
}

@MainActor
final class BackgroundTaskTests: XCTestCase {
    func testRunsTheOperationAndReturnsItsResult() async throws {
        let value = try await BackgroundTask.run("spine.tests.result") { 42 }

        XCTAssertEqual(value, 42)
    }

    func testRethrowsTheOperationsError() async {
        do {
            try await BackgroundTask.run("spine.tests.error") { throw URLError(.timedOut) }
            XCTFail("Expected the error")
        } catch {
            XCTAssertEqual((error as? URLError)?.code, .timedOut)
        }
    }

    func testWorksFromOutsideTheMainActor() async throws {
        let value = try await Task.detached {
            try await BackgroundTask.run("spine.tests.detached") { "done" }
        }.value

        XCTAssertEqual(value, "done")
    }
}

@MainActor
final class LaunchEnvironmentTests: XCTestCase {
    func testTheUnitTestHostIsRecognizedAsOne() {
        // `SpineApp` shows nothing here, so the host app can't refresh, sign out or resume imports against the
        // Keychain and network these tests use. If Xcode ever stops setting this variable, that guard goes quiet
        // and this test says so.
        XCTAssertNotNil(ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"])
        XCTAssertTrue(LaunchEnvironment.isHostingUnitTests)
    }
}
