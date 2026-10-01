import Foundation
import Security
import XCTest
@testable import Spine

// The refresh behavior of `APIClient` and `TokenRefresher` against the fake server in `RefreshTestSupport.swift`.

@MainActor
final class TokenRefreshTests: RefreshTestCase {
    // MARK: One refresh at a time

    func testConcurrentUnauthorizedRequestsShareOneRefresh() async throws {
        signIn()
        backend.hold(path: RefreshTestPath.refresh) // the exchange stays in flight until every request has joined it
        let client = makeClient()

        let statuses = try await withThrowingTaskGroup(of: String.self) { group in
            for _ in 0 ..< 8 {
                group.addTask { try await self.fetchHealth(client) }
            }
            await waitForPendingCallers(8)
            backend.release(path: RefreshTestPath.refresh)
            return try await group.reduce(into: []) { $0.append($1) }
        }

        XCTAssertEqual(statuses, Array(repeating: "ok", count: 8))
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(backend.refreshRequests.first?.refreshToken, "refresh-0")
        XCTAssertNil(backend.refreshRequests.first?.bearer, "the refresh request must stay unauthenticated")
        let healthBearers = backend.requests(path: RefreshTestPath.health).map(\.bearer)
        XCTAssertEqual(healthBearers.filter { $0 == "access-0" }.count, 8, "every request raced on the expired token")
        XCTAssertEqual(healthBearers.filter { $0 == "access-1" }.count, 8, "and every one retried with the new token")
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testSeparateClientInstancesShareTheDefaultRefresher() {
        // Asserted by identity rather than by racing on the process-wide instance, which the host app and other
        // tests can also touch. `AppEnvironment.apiClient` builds a fresh client on every access.
        XCTAssertTrue(makeClient(useSharedRefresher: true).refresher === TokenRefresher.shared)
        XCTAssertTrue(AppEnvironment.apiClient.refresher === TokenRefresher.shared)
        XCTAssertTrue(AppEnvironment.apiClient.refresher === AppEnvironment.apiClient.refresher)
        XCTAssertFalse(makeClient().refresher === TokenRefresher.shared, "tests inject a refresher of their own")
    }

    func testPendingCallerCountReturnsToZeroOnceTheExchangeIsDone() async throws {
        signIn()
        let count = await refresher.pendingCallerCount
        XCTAssertEqual(count, 0)

        _ = try await fetchHealth(makeClient())

        let after = await refresher.pendingCallerCount
        XCTAssertEqual(after, 0)
    }

    func testConcurrentWaitersAllGetUnauthorizedWhenTheRefreshIsRejected() async {
        signIn()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()

        let kinds = await withTaskGroup(of: RefreshErrorKind?.self) { group in
            for _ in 0 ..< 6 {
                group.addTask {
                    let error = await self.thrownError { _ = try await self.fetchHealth(client) }
                    return error.map(RefreshErrorKind.init)
                }
            }
            await waitForPendingCallers(6)
            backend.release(path: RefreshTestPath.refresh)
            var collected: [RefreshErrorKind?] = []
            for await kind in group { collected.append(kind) }
            return collected
        }

        XCTAssertEqual(kinds, Array(repeating: RefreshErrorKind.unauthorized, count: 6))
        XCTAssertEqual(backend.refreshRequests.count, 1, "one exchange, one rejection, shared by all six")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testUploadJoinsAnInFlightExchange() async throws {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()

        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)
        let uploading = Task { try await self.upload(with: client) }
        await waitForPendingCallers(2) // the upload's 401 joined the exchange the request started
        backend.release(path: RefreshTestPath.refresh)

        let requestStatus = try await request.value
        let uploadStatus = try await uploading.value

        XCTAssertEqual(requestStatus, "ok")
        XCTAssertEqual(uploadStatus, "ok")
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.upload).map(\.bearer), ["access-0", "access-1"])
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testRequestThatFailsAfterAnotherCallerRefreshedRetriesWithoutRefreshing() async throws {
        signIn()
        backend.accept(access: "access-1", refresh: "refresh-1")
        let store = store
        backend.setOverride { request in
            // While this request is on the wire another caller finishes its refresh and stores new tokens.
            guard request.path == RefreshTestPath.health, request.bearer == "access-0" else { return nil }
            store.refreshToken = "refresh-1"
            store.accessToken = "access-1"
            return .accessTokenNotValid()
        }

        let status = try await fetchHealth(makeClient())

        XCTAssertEqual(status, "ok")
        XCTAssertTrue(backend.refreshRequests.isEmpty, "the stored token differs from the rejected one, so no second refresh")
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).map(\.bearer), ["access-0", "access-1"])
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testRequestRecoversWhenOnlyTheRefreshTokenSurvived() async throws {
        store.refreshToken = "refresh-0" // the access token is gone, so the request goes out unsigned
        backend.accept(refresh: "refresh-0")

        let status = try await fetchHealth(makeClient())

        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.refreshRequests.count, 1, "a missing stored access token must not skip the refresh")
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).map(\.bearer), [nil, "access-1"])
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testCancellingTheRequestThatStartedTheRefreshDoesNotFailOtherRequests() async throws {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()

        let leader = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)
        let follower = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(2)
        leader.cancel() // e.g. its SwiftUI `.task` went away
        backend.release(path: RefreshTestPath.refresh)

        let status = try await follower.value
        _ = await leader.result

        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testCancellingOneCallerDoesNotCancelTheSharedExchange() async throws {
        let store = store
        store.accessToken = "old-access"
        store.refreshToken = "old-refresh"
        let started = RefreshGate()
        let release = RefreshGate()
        let probe = RefreshExchangeProbe()
        let exchange: TokenRefresher.Exchange = { _ in
            probe.begin()
            await started.open()
            await release.wait()
            probe.finish(cancelled: Task.isCancelled)
            return await MainActor.run { AuthRefreshResponse(access: "new-access", refresh: "new-refresh") }
        }
        let refresher = refresher

        let first = Task {
            try await refresher.refresh(afterRejecting: "old-access", tokenStore: store, exchange: exchange)
        }
        await started.wait()
        let second = Task {
            try await refresher.refresh(afterRejecting: "old-access", tokenStore: store, exchange: exchange)
        }
        await waitForPendingCallers(2) // the second caller has provably joined
        first.cancel()
        await release.open()

        try await second.value
        _ = await first.result

        XCTAssertEqual(probe.startCount, 1)
        XCTAssertFalse(probe.sawCancellation, "the exchange must not inherit a caller's cancellation")
        XCTAssertEqual(store.accessToken, "new-access")
        XCTAssertEqual(store.refreshToken, "new-refresh")
    }

    // MARK: Temporary failures keep the session

    func testRefreshTimeoutKeepsTokensAfterOneImmediateRetry() async {
        // The request may have reached the server, so it is asked once more with the same token.
        await assertRefreshFailsTemporarily(.failure(.timedOut), as: .network(.timedOut), httpRequestsPerAttempt: 2)
    }

    func testRefreshWhileOfflineKeepsTokensWithoutRetrying() async {
        await assertRefreshFailsTemporarily(.failure(.notConnectedToInternet), as: .network(.notConnectedToInternet))
    }

    func testRefreshCannotConnectToHostKeepsTokensWithoutRetrying() async {
        await assertRefreshFailsTemporarily(.failure(.cannotConnectToHost), as: .network(.cannotConnectToHost))
    }

    func testRefreshConnectionLostKeepsTokensAfterOneImmediateRetry() async {
        await assertRefreshFailsTemporarily(
            .failure(.networkConnectionLost),
            as: .network(.networkConnectionLost),
            httpRequestsPerAttempt: 2
        )
    }

    func testRefreshUnusableResponsesAreRetriedOnceToo() async {
        for code in [URLError.Code.badServerResponse, .cannotParseResponse, .zeroByteResource] {
            await assertRefreshFailsTemporarily(
                .failure(code),
                as: .network(code),
                httpRequestsPerAttempt: 2,
                label: "\(code.rawValue)"
            )
        }
    }

    func testRefreshFailuresThatNeverLeftTheDeviceAreNotRetried() async {
        for code in [URLError.Code.dnsLookupFailed, .cannotFindHost, .secureConnectionFailed, .dataNotAllowed] {
            await assertRefreshFailsTemporarily(.failure(code), as: .network(code), label: "\(code.rawValue)")
        }
    }

    func testRefreshServerErrorKeepsTokens() async {
        await assertRefreshFailsTemporarily(.status(500), as: .httpStatus(500))
    }

    func testRefreshServiceUnavailableKeepsTokens() async {
        await assertRefreshFailsTemporarily(.status(503), as: .httpStatus(503))
    }

    func testRefreshGatewayErrorsKeepTokensAfterOneImmediateRetry() async {
        // A gateway in front of the API gave up on the answer, and the origin may have committed the rotation.
        for status in [502, 504, 520, 521, 522, 523, 524] {
            await assertRefreshFailsTemporarily(
                .status(status),
                as: .httpStatus(status),
                httpRequestsPerAttempt: 2,
                label: "\(status)"
            )
        }
    }

    func testRefreshErrorsThatCannotHaveRotatedTheTokenAreNotRetried() async {
        // The API answered with these itself, or failed before it got to rotate anything.
        for status in [400, 403, 404, 500, 501, 503, 505, 529] {
            await assertRefreshFailsTemporarily(
                .status(status),
                as: .httpStatus(status),
                label: "\(status)"
            )
        }
    }

    func testRefreshForbiddenKeepsTokens() async {
        await assertRefreshFailsTemporarily(.status(403), as: .httpStatus(403))
    }

    func testRefreshRateLimitedWithoutRetryAfterKeepsTokens() async {
        await assertRefreshFailsTemporarily(.status(429), as: .httpStatus(429))
    }

    func testRefreshRateLimitedWithLongRetryAfterKeepsTokensWithoutRetrying() async {
        await assertRefreshFailsTemporarily(.status(429, headers: ["Retry-After": "30"]), as: .httpStatus(429))
    }

    func testRefreshRateLimitedAgainAfterShortRetryAfterKeepsTokens() async {
        // The existing single retry still applies to the refresh call: two HTTP requests per attempt.
        await assertRefreshFailsTemporarily(
            .status(429, headers: ["Retry-After": "0"]),
            as: .httpStatus(429),
            httpRequestsPerAttempt: 2
        )
    }

    func testRefreshWithUnreadableBodyKeepsTokens() async {
        await assertRefreshFailsTemporarily(.json("not json"), as: .decoding)
    }

    func testRefreshRetriesOneShortRateLimitThenRefreshes() async throws {
        signIn()
        let attempts = RefreshCounter()
        backend.setOverride { request in
            guard request.isRefresh, attempts.increment() == 1 else { return nil }
            return .status(429, headers: ["Retry-After": "0"])
        }

        let status = try await fetchHealth(makeClient())

        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.refreshRequests.count, 2)
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    // MARK: A lost rotation response is recovered

    func testLostRotationResponseAfterATimeoutIsRecoveredByTheImmediateRetry() async {
        await assertLostRotationIsRecovered(after: .timedOut)
    }

    func testLostRotationResponseAfterADroppedConnectionIsRecoveredByTheImmediateRetry() async {
        await assertLostRotationIsRecovered(after: .networkConnectionLost)
    }

    func testLostRotationResponseBehindAGatewayErrorIsRecoveredByTheImmediateRetry() async {
        for status in [502, 504, 520, 521, 522, 523, 524] {
            await assertLostRotationIsRecovered(afterGatewayStatus: status)
        }
    }

    // MARK: A refresh token that expired long ago is dead without asking

    func testRefreshTokenExpiredOverTwoDaysAgoEndsTheSessionWithoutCallingTheServer() async {
        for entryPoint in RefreshEntryPoint.allCases {
            recorder.reset()
            let dead = makeJWT(expiringAt: Date().addingTimeInterval(-72 * 3600), kind: "refresh")
            store.accessToken = "access-0"
            store.refreshToken = dead
            backend.accept(refresh: dead) // even a server that would still take it isn't asked

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized, "\(entryPoint)")
            XCTAssertTrue(backend.refreshRequests.isEmpty, "\(entryPoint): no refresh call")
            XCTAssertNil(store.accessToken, "\(entryPoint)")
            XCTAssertNil(store.refreshToken, "\(entryPoint)")
        }
    }

    func testRefreshTokenExpiredWithinTheClockSkewMarginStillAsksTheServer() async throws {
        // A device clock can be wrong by a day or more, so anything that expired less than 48 hours ago is
        // still the server's to judge.
        for hoursAgo in [1.0, 24, 47] {
            signIn()
            let recent = makeJWT(expiringAt: Date().addingTimeInterval(-hoursAgo * 3600), kind: "refresh")
            store.refreshToken = recent
            backend.accept(refresh: recent) // the device clock may simply run ahead
            let before = backend.refreshRequests.count

            let status = try await fetchHealth(makeClient())

            XCTAssertEqual(status, "ok", "\(hoursAgo) hours ago")
            XCTAssertEqual(backend.refreshRequests.dropFirst(before).map(\.refreshToken), [recent], "\(hoursAgo) hours ago")
            XCTAssertNotEqual(store.refreshToken, recent, "\(hoursAgo) hours ago")
        }
    }

    // MARK: Only the server's own rejection ends the session

    func testRefreshRejectedByTokenNotValidEnvelopeClearsTokensAndThrowsUnauthorized() async {
        for message in ["Token is blacklisted", "Token is invalid", "Token is expired", "Token has wrong type"] {
            await assertRefreshRejected(.tokenNotValid(message), label: message)
        }
    }

    func testRefreshRejectedByAuthenticationFailedEnvelopeClearsTokensAndThrowsUnauthorized() async {
        await assertRefreshRejected(.authenticationFailed()) // the token's user is inactive or deleted
    }

    func testRefreshRejectedByInvalidRefreshFieldClearsTokensAndThrowsUnauthorized() async {
        await assertRefreshRejected(.refreshFieldInvalid())
    }

    func testRefreshHTMLUnauthorizedPageKeepsTokens() async {
        await assertRefreshFailsTemporarily(.html(401, RefreshErrorPage.nginxUnauthorized), as: .httpStatus(401))
    }

    func testRefreshEmptyUnauthorizedKeepsTokens() async {
        await assertRefreshFailsTemporarily(.status(401), as: .httpStatus(401))
    }

    func testRefreshUnauthorizedThatIsNotTheAPIEnvelopeKeepsTokens() async {
        // JSON, but not `{"error": {...}}`: DRF's bare detail, a string error, Cloudflare's errors array, non-objects.
        for body in [
            #"{"detail":"Authentication credentials were not provided."}"#,
            #"{"error":"Unauthorized"}"#,
            #"{"error":null}"#,
            #"{"errors":[{"code":1033,"message":"Cloudflare Tunnel error"}]}"#,
            "[]",
            "null",
        ] {
            let reply = RefreshTestReply.status(401, headers: ["Content-Type": "application/json"], body: body)
            await assertRefreshFailsTemporarily(reply, as: .httpStatus(401), label: body)
        }
    }

    func testRefreshHTMLBadRequestKeepsTokens() async {
        // Django's DisallowedHost page, e.g. after a deploy with a bad ALLOWED_HOSTS.
        await assertRefreshFailsTemporarily(.html(400, RefreshErrorPage.djangoBadRequest), as: .httpStatus(400))
    }

    func testRefreshBadRequestEnvelopeWithoutRefreshFieldKeepsTokens() async {
        // A parse error carries no fields, and a validation error about another field isn't about the token.
        await assertRefreshFailsTemporarily(
            .envelope(400, code: "parse_error", message: "JSON parse error - Expecting value: line 1 column 1 (char 0)"),
            as: .httpStatus(400),
            label: "parse_error"
        )
        await assertRefreshFailsTemporarily(
            .envelope(
                400,
                code: "invalid",
                message: "One or more fields are invalid.",
                fields: #"{"username":["This field is required."]}"#
            ),
            as: .httpStatus(400),
            label: "fields for another key"
        )
    }

    func testProxyAnsweringEveryRequestWithAnHTMLUnauthorizedPageNeverSignsTheUserOut() async {
        for entryPoint in RefreshEntryPoint.allCases {
            signIn()
            backend.setOverride { _ in .html(401, RefreshErrorPage.nginxUnauthorized) }

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertEqual(error.map(RefreshErrorKind.init), .httpStatus(401), "\(entryPoint)")
            XCTAssertEqual(store.accessToken, "access-0", "\(entryPoint): access token kept")
            XCTAssertEqual(store.refreshToken, "refresh-0", "\(entryPoint): refresh token kept")
        }
    }

    func testBadAllowedHostsAnsweringEveryRequestWithAnHTMLBadRequestNeverSignsTheUserOut() async {
        for entryPoint in RefreshEntryPoint.allCases {
            signIn()
            backend.setOverride { _ in .html(400, RefreshErrorPage.djangoBadRequest) }

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertEqual(error.map(RefreshErrorKind.init), .httpStatus(400), "\(entryPoint)")
            XCTAssertEqual(store.accessToken, "access-0", "\(entryPoint): access token kept")
            XCTAssertEqual(store.refreshToken, "refresh-0", "\(entryPoint): refresh token kept")
        }
    }

    func testMissingRefreshTokenIsUnauthorizedWithoutNetworkCall() async {
        for entryPoint in RefreshEntryPoint.allCases {
            store.accessToken = "access-0" // no refresh token
            let before = backend.refreshRequests.count

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized, "\(entryPoint)")
            XCTAssertEqual(backend.refreshRequests.count, before, "\(entryPoint)")
            XCTAssertNil(store.accessToken, "\(entryPoint)")
        }
    }

    func testStillUnauthorizedAfterSuccessfulRefreshClearsTokens() async {
        signIn()
        backend.setOverride { $0.isRefresh ? nil : .accessTokenNotValid() } // the refresh works, access tokens never do

        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).count, 2, "one retry, no more")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    // MARK: Never wipe newer tokens

    func testRejectedStaleRefreshKeepsNewerTokens() async throws {
        let store = store
        for entryPoint in RefreshEntryPoint.allCases {
            store.accessToken = "stale-access"
            store.refreshToken = "stale-refresh"
            backend.accept(access: "newer-access")
            backend.setOverride { request in
                // Newer tokens land while our exchange is in flight, then the server rejects the stale token.
                guard request.isRefresh else { return nil }
                store.refreshToken = "newer-refresh"
                store.accessToken = "newer-access"
                return .tokenNotValid()
            }
            let before = backend.refreshRequests.count

            // The session is alive, so nothing is thrown: callers just carry on with the newer tokens.
            try await exercise(entryPoint, with: makeClient())

            XCTAssertEqual(backend.refreshRequests.count, before + 1, "\(entryPoint)")
            XCTAssertEqual(store.accessToken, "newer-access", "\(entryPoint)")
            XCTAssertEqual(store.refreshToken, "newer-refresh", "\(entryPoint)")
        }
    }

    func testRefreshResultIsDroppedWhenTheUserSignedOutMidExchange() async {
        signIn()
        let store = store
        backend.setOverride { request in
            if request.isRefresh { store.clear() } // sign out while the exchange is in flight
            return nil // the server still answers with new tokens
        }

        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertNil(store.accessToken, "a late refresh must not resurrect a signed-out session")
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(
            backend.requests(path: RefreshTestPath.health).count,
            1,
            "the session is over, so its request isn't sent again without a token"
        )
    }

    // MARK: A 401 after the refresh

    func testUnauthorizedAfterTheRefreshRetriesOnceMoreWhenNewerTokensLandedMeanwhile() async throws {
        signIn()
        let store = store
        backend.setOverride { request in
            // The retry, signed with the freshly refreshed token, is rejected while another caller's newer tokens land.
            guard request.path == RefreshTestPath.health, request.bearer == "access-1" else { return nil }
            store.refreshToken = "refresh-9"
            store.accessToken = "access-9"
            return .accessTokenNotValid()
        }
        backend.accept(access: "access-9", refresh: "refresh-9")

        let status = try await fetchHealth(makeClient())

        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).map(\.bearer), ["access-0", "access-1", "access-9"])
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(store.accessToken, "access-9", "the newer tokens survive")
        XCTAssertEqual(store.refreshToken, "refresh-9")
    }

    func testUnauthorizedAfterTheRefreshIsAnHTTPErrorWhenTheBodyIsNotTheAPIEnvelope() async {
        signIn()
        backend.setOverride { request in
            guard request.path == RefreshTestPath.health, request.bearer == "access-1" else { return nil }
            return .html(401, RefreshErrorPage.nginxUnauthorized)
        }

        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .httpStatus(401), "a proxy's 401 page doesn't end the session")
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testUnauthorizedAfterTheSecondRetryEndsTheSessionWithoutFurtherRetries() async {
        signIn()
        let store = store
        backend.setOverride { request in
            guard request.path == RefreshTestPath.health else { return nil }
            switch request.bearer {
            case "access-1":
                store.refreshToken = "refresh-9"
                store.accessToken = "access-9"
                return .accessTokenNotValid()
            case "access-9":
                return .accessTokenNotValid()
            default:
                return nil
            }
        }

        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertEqual(
            backend.requests(path: RefreshTestPath.health).map(\.bearer),
            ["access-0", "access-1", "access-9"],
            "one retry with the refreshed token and one more with the newer one, no more"
        )
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    // MARK: Uploads

    func testUploadRetriesAfterSuccessfulRefresh() async throws {
        signIn()
        let client = makeClient()

        let status = try await upload(with: client)

        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.refreshRequests.count, 1)
        let uploads = backend.requests(path: RefreshTestPath.upload)
        XCTAssertEqual(uploads.map(\.bearer), ["access-0", "access-1"])
        XCTAssertEqual(uploads.map(\.method), ["POST", "POST"])
        for attempt in uploads {
            let body = String(decoding: attempt.body, as: UTF8.self)
            XCTAssertTrue(body.contains("Heat,4.5"), "the retry re-sends the same body file")
        }
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testUploadWithRejectedRefreshThrowsUnauthorized() async {
        signIn()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }

        let error = await thrownError { _ = try await upload(with: makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.upload).count, 1)
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testUploadWithTemporaryRefreshFailureThrowsThatErrorAndKeepsTokens() async {
        signIn()
        backend.setOverride { $0.isRefresh ? .status(503) : nil }

        let error = await thrownError { _ = try await upload(with: makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .httpStatus(503))
        XCTAssertEqual(store.accessToken, "access-0")
        XCTAssertEqual(store.refreshToken, "refresh-0")
    }

    func testUploadWithHTMLUnauthorizedRefreshKeepsTokens() async {
        signIn()
        backend.setOverride { $0.isRefresh ? .html(401, RefreshErrorPage.nginxUnauthorized) : nil }

        let error = await thrownError { _ = try await upload(with: makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .httpStatus(401))
        XCTAssertEqual(store.accessToken, "access-0")
        XCTAssertEqual(store.refreshToken, "refresh-0")
    }

    func testUploadRetriesOnceMoreWhenNewerTokensLandedMeanwhile() async throws {
        signIn()
        let store = store
        backend.setOverride { request in
            guard request.path == RefreshTestPath.upload, request.bearer == "access-1" else { return nil }
            store.refreshToken = "refresh-9"
            store.accessToken = "access-9"
            return .accessTokenNotValid()
        }
        backend.accept(access: "access-9", refresh: "refresh-9")

        let status = try await upload(with: makeClient())

        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.requests(path: RefreshTestPath.upload).map(\.bearer), ["access-0", "access-1", "access-9"])
        XCTAssertEqual(store.accessToken, "access-9")
        XCTAssertEqual(store.refreshToken, "refresh-9")
    }

    func testUploadUnauthorizedAfterTheRefreshIsAnHTTPErrorWhenTheBodyIsNotTheAPIEnvelope() async {
        signIn()
        backend.setOverride { request in
            guard request.path == RefreshTestPath.upload, request.bearer == "access-1" else { return nil }
            return .html(401, RefreshErrorPage.nginxUnauthorized)
        }

        let error = await thrownError { _ = try await upload(with: makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .httpStatus(401))
        XCTAssertEqual(store.accessToken, "access-1")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    func testUploadStillUnauthorizedAfterRefreshDoesNotClearTokensItself() async {
        signIn()
        backend.setOverride { $0.isRefresh ? nil : .accessTokenNotValid() }

        let error = await thrownError { _ = try await upload(with: makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertEqual(backend.refreshRequests.count, 1)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.upload).count, 2, "one retry, no more")
        XCTAssertEqual(store.accessToken, "access-1", "sign-out is the caller's decision, not the upload's")
        XCTAssertEqual(store.refreshToken, "refresh-1")
    }

    // MARK: A failed Keychain lookup is not a signed-out session

    func testFailedKeychainReadIsATemporaryErrorAndClearsNothing() async {
        for entryPoint in RefreshEntryPoint.allCases {
            signIn()
            keychain.failReads(with: errSecInteractionNotAllowed)
            let before = backend.refreshRequests.count

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            keychain.stopFailingReads()
            XCTAssertEqual(error.map(RefreshErrorKind.init), .keychain(errSecInteractionNotAllowed), "\(entryPoint)")
            XCTAssertEqual(backend.refreshRequests.count, before, "\(entryPoint): no exchange without a readable token")
            XCTAssertEqual(store.accessToken, "access-0", "\(entryPoint)")
            XCTAssertEqual(store.refreshToken, "refresh-0", "\(entryPoint)")
        }
    }

    func testFailedKeychainReadWhileTheServerRejectsTheTokenDoesNotClearTheSession() async {
        signIn()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()

        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)
        keychain.failReads(with: errSecInteractionNotAllowed) // the Keychain locks while the exchange is in flight
        backend.release(path: RefreshTestPath.refresh)
        let result = await request.result
        keychain.stopFailingReads()

        guard case let .failure(error) = result else { return XCTFail("Expected an error") }
        XCTAssertEqual(RefreshErrorKind(error), .keychain(errSecInteractionNotAllowed))
        XCTAssertEqual(store.accessToken, "access-0", "the rejection couldn't be checked against the store, so nothing is cleared")
        XCTAssertEqual(store.refreshToken, "refresh-0")
    }

    func testFailedWriteOfTheRotatedRefreshTokenIsTemporaryAndKeepsTheOldPair() async throws {
        signIn()
        backend.setGrace(seconds: 60)
        keychain.failWrites(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .keychain(errSecInteractionNotAllowed))
        XCTAssertEqual(store.accessToken, "access-0", "the access token isn't replaced when the refresh token couldn't be stored")
        XCTAssertEqual(store.refreshToken, "refresh-0")

        // The Keychain recovers. The server still honors the old token for a minute, so the next request works.
        keychain.stopFailingWrites()
        let status = try await fetchHealth(makeClient())
        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.refreshRequests.map(\.refreshToken), ["refresh-0", "refresh-0"])
    }

    func testExchangeStoresTheRefreshTokenBeforeTheAccessToken() async throws {
        signIn()
        keychain.resetWriteLog()

        _ = try await fetchHealth(makeClient())

        XCTAssertEqual(keychain.writes, [InMemoryKeychain.refreshAccount, InMemoryKeychain.accessAccount])
    }

    // MARK: Sign-in and sign-out go through the refresher

    func testSignInStoresTheRefreshTokenBeforeTheAccessToken() async throws {
        try await refresher.signIn(access: "new-access", refresh: "new-refresh", tokenStore: store)

        XCTAssertEqual(keychain.writes, [InMemoryKeychain.refreshAccount, InMemoryKeychain.accessAccount])
        XCTAssertEqual(store.accessToken, "new-access")
        XCTAssertEqual(store.refreshToken, "new-refresh")
    }

    func testFailedSignInLeavesNoHalfASession() async {
        keychain.failWrites(of: InMemoryKeychain.accessAccount, with: errSecInteractionNotAllowed)

        let error = await thrownError {
            try await self.refresher.signIn(access: "new-access", refresh: "new-refresh", tokenStore: self.store)
        }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .keychain(errSecInteractionNotAllowed))
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken, "the refresh token written first is rolled back")
    }

    func testSignOutWhileAnExchangeIsInFlightIsNotUndoneByIt() async {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()

        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)
        await refresher.signOut(ifRefreshToken: "refresh-0", tokenStore: store) // the user signs out mid-exchange
        backend.release(path: RefreshTestPath.refresh)
        let result = await request.result

        guard case let .failure(error) = result else { return XCTFail("Expected an error") }
        XCTAssertEqual(RefreshErrorKind(error), .unauthorized)
        XCTAssertNil(store.accessToken, "the late exchange must not resurrect the session")
        XCTAssertNil(store.refreshToken)
    }

    func testClearIfAccessTokenOnlyClearsWhenItIsStillTheStoredOne() async throws {
        signIn()

        let skipped = try await refresher.clear(ifAccessToken: "some-other-token", tokenStore: store)
        XCTAssertFalse(skipped)
        XCTAssertEqual(store.accessToken, "access-0")
        XCTAssertEqual(store.refreshToken, "refresh-0")

        let cleared = try await refresher.clear(ifAccessToken: "access-0", tokenStore: store)
        XCTAssertTrue(cleared)
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    // MARK: Sign-out is a compare-and-clear

    func testSignOutClearsTheSessionItWasDecidedForAndHandsBackItsRefreshToken() async {
        signIn()

        let cleared = await refresher.signOut(ifRefreshToken: "refresh-0", tokenStore: store)

        XCTAssertEqual(cleared, "refresh-0")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(recorder.reasons, [.userSignOut])
    }

    func testALateSignOutLeavesANewerSessionAlone() async throws {
        signIn(access: "access-A", refresh: "refresh-A")
        // The sign-out was decided for session A; by the time it runs, the user has signed in again.
        try await refresher.signIn(access: "access-B", refresh: "refresh-B", tokenStore: store)

        let cleared = await refresher.signOut(ifRefreshToken: "refresh-A", tokenStore: store)

        XCTAssertNil(cleared)
        XCTAssertEqual(store.accessToken, "access-B")
        XCTAssertEqual(store.refreshToken, "refresh-B")
        XCTAssertTrue(recorder.reasons.isEmpty, "nothing was cleared")
    }

    func testASignOutDecidedBeforeAnExchangeLandedStillEndsTheRotatedSession() async throws {
        signIn()
        let decidedFor = store.refreshToken // the sign-out captured this token ...
        _ = try await fetchHealth(makeClient()) // ... and a refresh rotated it before the sign-out got to run
        XCTAssertEqual(store.refreshToken, "refresh-1")

        let cleared = await refresher.signOut(ifRefreshToken: decidedFor, tokenStore: store)

        XCTAssertEqual(cleared, "refresh-1", "the live token comes back, so it can be revoked too")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testARotationIsNotRememberedPastASignIn() async throws {
        signIn()
        let decidedFor = store.refreshToken
        _ = try await fetchHealth(makeClient()) // refresh-0 becomes refresh-1
        try await refresher.signIn(access: "access-B", refresh: "refresh-B", tokenStore: store)

        let cleared = await refresher.signOut(ifRefreshToken: decidedFor, tokenStore: store)

        XCTAssertNil(cleared)
        XCTAssertEqual(store.refreshToken, "refresh-B")
    }

    func testASignOutDecidedWithoutATokenOnlyClearsLeftoversNotASessionThatStartedSince() async throws {
        store.accessToken = "stray-access" // an access token without a refresh token
        await refresher.signOut(ifRefreshToken: nil, tokenStore: store)
        XCTAssertNil(store.accessToken)
        XCTAssertTrue(recorder.reasons.isEmpty, "there was no session to sign out of")

        try await refresher.signIn(access: "access-B", refresh: "refresh-B", tokenStore: store)
        await refresher.signOut(ifRefreshToken: nil, tokenStore: store)
        XCTAssertEqual(store.accessToken, "access-B")
        XCTAssertEqual(store.refreshToken, "refresh-B")
    }

    func testSignOutStillTriesWhenTheKeychainCannotBeRead() async {
        signIn()
        keychain.failReads(with: errSecInteractionNotAllowed)

        await refresher.signOut(ifRefreshToken: nil, tokenStore: store) // nothing could be captured

        keychain.stopFailingReads()
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testSignOutDeletesTheRefreshTokenBeforeTheAccessToken() async {
        signIn()

        await refresher.signOut(ifRefreshToken: "refresh-0", tokenStore: store)

        XCTAssertEqual(keychain.deletes, [InMemoryKeychain.refreshAccount, InMemoryKeychain.accessAccount])
    }

    // MARK: A dropped exchange still gets its new refresh token revoked

    func testTheRotatedTokenOfAnExchangeDroppedBecauseOfASignOutIsRevoked() async {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()
        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)

        await refresher.signOut(ifRefreshToken: "refresh-0", tokenStore: store) // the user signs out mid-exchange
        backend.release(path: RefreshTestPath.refresh)
        _ = await request.result

        await waitUntil("the dropped token to be revoked") {
            backend.requests(path: RefreshTestPath.logout).map(\.refreshToken) == ["refresh-1"]
        }
        XCTAssertNil(backend.requests(path: RefreshTestPath.logout).first?.bearer, "revoked without an access token")
        XCTAssertTrue(backend.liveRefreshTokens.isEmpty, "no refresh token of the ended session stays alive")
        XCTAssertNil(store.refreshToken)
    }

    func testTheRotatedTokenOfAnExchangeDroppedBecauseOfANewSignInIsRevokedAndTheNewSessionKept() async throws {
        signIn()
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()
        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)

        try await refresher.signIn(access: "access-B", refresh: "refresh-B", tokenStore: store)
        backend.accept(access: "access-B", refresh: "refresh-B")
        backend.release(path: RefreshTestPath.refresh)
        _ = await request.result

        await waitUntil("the dropped token to be revoked") {
            backend.requests(path: RefreshTestPath.logout).map(\.refreshToken) == ["refresh-1"]
        }
        XCTAssertEqual(store.accessToken, "access-B")
        XCTAssertEqual(store.refreshToken, "refresh-B")
        XCTAssertEqual(backend.liveRefreshTokens, ["refresh-B"])
    }

    func testAnExchangeThatIsKeptRevokesNothing() async throws {
        signIn()

        _ = try await fetchHealth(makeClient())
        try? await Task.sleep(for: .milliseconds(100))

        XCTAssertTrue(backend.requests(path: RefreshTestPath.logout).isEmpty)
    }

    // MARK: A replay never crosses users

    private func userToken(_ userID: Int) -> String {
        makeJWT(expiringAt: Date().addingTimeInterval(900), userID: userID)
    }

    func testARequestOfAnEndedSessionIsNotReplayedWithTheNextUsersToken() async throws {
        let alice = userToken(7)
        let bob = userToken(8)
        signIn(access: alice, refresh: "refresh-A") // alice's access token is rejected
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()
        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)

        // While alice's request waits for the refresh, she signs out and bob signs in.
        try await refresher.signIn(access: bob, refresh: "refresh-B", tokenStore: store)
        backend.accept(access: bob, refresh: "refresh-B")
        backend.release(path: RefreshTestPath.refresh)
        let result = await request.result

        guard case let .failure(error) = result else { return XCTFail("Expected the request to be cancelled") }
        XCTAssertEqual(RefreshErrorKind(error), .cancelled)
        XCTAssertEqual(
            backend.requests(path: RefreshTestPath.health).map(\.bearer),
            [alice],
            "bob's token never carried alice's request"
        )
        XCTAssertEqual(store.accessToken, bob, "and bob's session is untouched")
        XCTAssertEqual(store.refreshToken, "refresh-B")
    }

    func testAnUploadOfAnEndedSessionIsNotReplayedWithTheNextUsersTokenEither() async throws {
        let alice = userToken(7)
        let bob = userToken(8)
        signIn(access: alice, refresh: "refresh-A")
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()
        let uploading = Task { try await self.upload(with: client) }
        await waitForPendingCallers(1)

        try await refresher.signIn(access: bob, refresh: "refresh-B", tokenStore: store)
        backend.accept(access: bob, refresh: "refresh-B")
        backend.release(path: RefreshTestPath.refresh)
        let result = await uploading.result

        guard case let .failure(error) = result else { return XCTFail("Expected the upload to be cancelled") }
        XCTAssertEqual(RefreshErrorKind(error), .cancelled)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.upload).map(\.bearer), [alice])
        XCTAssertEqual(store.accessToken, bob)
    }

    func testAReplayForTheSameUserGoesAheadWithTheNewerToken() async throws {
        let first = userToken(7)
        let second = userToken(7)
        signIn(access: first, refresh: "refresh-0")
        backend.accept(access: second, refresh: "refresh-1")
        let store = store
        backend.setOverride { request in
            // Another caller refreshes the same user's session while this request is on the wire.
            guard request.path == RefreshTestPath.health, request.bearer == first else { return nil }
            store.refreshToken = "refresh-1"
            store.accessToken = second
            return .accessTokenNotValid()
        }

        let status = try await fetchHealth(makeClient())

        XCTAssertEqual(status, "ok")
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).map(\.bearer), [first, second])
        XCTAssertTrue(backend.refreshRequests.isEmpty)
    }

    func testASecondReplayAlsoNeverCrossesUsers() async {
        let alice = userToken(7)
        let aliceRefreshed = userToken(7)
        let bob = userToken(8)
        signIn(access: alice, refresh: "refresh-A")
        let store = store
        backend.setOverride { request in
            if request.isRefresh {
                return .json(#"{"access":"\#(aliceRefreshed)","refresh":"refresh-A2"}"#)
            }
            // The retry with alice's refreshed token is rejected while bob signs in.
            guard request.path == RefreshTestPath.health, request.bearer == aliceRefreshed else { return nil }
            store.refreshToken = "refresh-B"
            store.accessToken = bob
            return .accessTokenNotValid()
        }

        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .cancelled)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).map(\.bearer), [alice, aliceRefreshed])
        XCTAssertEqual(store.accessToken, bob)
        XCTAssertEqual(store.refreshToken, "refresh-B")
    }

    // MARK: Newer tokens that appear at the last moment are retried with, not thrown away

    func testNewerTokensThatLandBetweenTheRejectionAndTheClearGetTheirTurn() async throws {
        signIn()
        backend.accept(access: "access-9", refresh: "refresh-9")
        let store = store
        // The reads of the access token in this flow: 1 the request, 2 the refresher's check, 3 the replay,
        // 4 the look for newer tokens once the replay is rejected, 5 the compare-and-clear that follows.
        // New tokens land right after the 4th, so the clear finds them and skips.
        keychain.onRead(of: InMemoryKeychain.accessAccount, afterReads: 4) {
            store.refreshToken = "refresh-9"
            store.accessToken = "access-9"
        }
        backend.setOverride { request in
            request.path == RefreshTestPath.health && request.bearer == "access-1" ? .accessTokenNotValid() : nil
        }

        let status = try await fetchHealth(makeClient())

        XCTAssertEqual(status, "ok", "the session is alive, so the request isn't answered with .unauthorized")
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).map(\.bearer), ["access-0", "access-1", "access-9"])
        XCTAssertEqual(store.accessToken, "access-9")
        XCTAssertEqual(store.refreshToken, "refresh-9")
        XCTAssertTrue(recorder.reasons.isEmpty, "nothing was cleared")
    }

    func testARequestWhoseSessionEndedAtTheLastMomentIsUnauthorizedAndNotRetried() async {
        signIn()
        let store = store
        keychain.onRead(of: InMemoryKeychain.accessAccount, afterReads: 4) {
            store.clear() // the user signs out right after the retry is rejected
        }
        backend.setOverride { request in
            request.path == RefreshTestPath.health && request.bearer == "access-1" ? .accessTokenNotValid() : nil
        }

        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertEqual(backend.requests(path: RefreshTestPath.health).map(\.bearer), ["access-0", "access-1"])
    }

    // MARK: Every clear says why

    func testNoRefreshTokenIsLogged() async {
        store.accessToken = "access-0"

        _ = await thrownError { try await exercise(.authenticatedRequest, with: makeClient()) }

        XCTAssertEqual(recorder.reasons, [.noRefreshToken])
    }

    func testLocalExpiryIsLoggedWithoutTheToken() async {
        let dead = makeJWT(expiringAt: Date().addingTimeInterval(-72 * 3600), kind: "refresh")
        store.accessToken = "access-0"
        store.refreshToken = dead

        _ = await thrownError { try await exercise(.launchRefresh, with: makeClient()) }

        XCTAssertEqual(recorder.reasons, [.localExpiry])
        XCTAssertEqual(recorder.details.count, 1)
        XCTAssertFalse(recorder.details.joined().contains(dead), "a token never reaches the log")
        XCTAssertFalse(recorder.details.joined().contains("."), "nor any part of one")
    }

    func testAServerRejectionOfTheRefreshTokenIsLogged() async {
        signIn()
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }

        _ = await thrownError { try await exercise(.authenticatedRequest, with: makeClient()) }

        XCTAssertEqual(recorder.reasons, [.serverRejection])
    }

    func testAServerRejectionOfAFreshlyRefreshedAccessTokenIsLogged() async {
        signIn()
        backend.setOverride { $0.isRefresh ? nil : .accessTokenNotValid() }

        _ = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(recorder.reasons, [.serverRejection])
    }

    func testAKeychainThatRefusesASignInIsLogged() async {
        keychain.failWrites(of: InMemoryKeychain.accessAccount, with: errSecInteractionNotAllowed)

        _ = await thrownError { try await self.refresher.signIn(access: "a", refresh: "r", tokenStore: self.store) }

        XCTAssertEqual(recorder.reasons, [.keychainError])
        XCTAssertEqual(recorder.details, ["status \(errSecInteractionNotAllowed)"])
    }

    func testTemporaryFailuresClearNothingAndLogNoClear() async {
        for reply in [RefreshTestReply.failure(.notConnectedToInternet), .status(503), .html(401, RefreshErrorPage.nginxUnauthorized)] {
            signIn()
            backend.setOverride { $0.isRefresh ? reply : nil }

            _ = await thrownError { try await exercise(.authenticatedRequest, with: makeClient()) }
        }

        XCTAssertTrue(recorder.reasons.isEmpty)
    }

    // MARK: A clear that changed nothing is not logged

    func testALate401AfterASignOutLogsNoClear() async {
        signIn()
        await refresher.signOut(ifRefreshToken: "refresh-0", tokenStore: store)
        XCTAssertEqual(recorder.reasons, [.userSignOut])

        // A request of the session that just ended is answered 401 after the sign-out.
        let error = await thrownError { _ = try await fetchHealth(makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertEqual(recorder.reasons, [.userSignOut], "no phantom \"no refresh token\": nothing was stored")
    }

    func testTheAppsOwnRevocationRejectingItsExchangeIsNotLoggedAsAServerRejection() async {
        signIn()
        // The server answers 401: the revocation the app sent for the signed-out session got there first.
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }
        backend.hold(path: RefreshTestPath.refresh)
        let client = makeClient()
        let request = Task { try await self.fetchHealth(client) }
        await waitForPendingCallers(1)

        await refresher.signOut(ifRefreshToken: "refresh-0", tokenStore: store) // the user signs out mid-exchange
        backend.release(path: RefreshTestPath.refresh)
        let result = await request.result

        guard case let .failure(error) = result else { return XCTFail("Expected an error") }
        XCTAssertEqual(RefreshErrorKind(error), .unauthorized)
        XCTAssertEqual(recorder.reasons, [.userSignOut], "the user's sign-out is all that ended this session")
    }

    func testNothingStoredLogsNothingWhereverTheClearComesFrom() async {
        for entryPoint in RefreshEntryPoint.allCases {
            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized, "\(entryPoint)")
        }
        XCTAssertTrue(recorder.reasons.isEmpty)
    }

    func testAKeychainThatRefusesTheFirstWriteOfASignInIsLoggedToo() async {
        keychain.failWrites(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        _ = await thrownError { try await self.refresher.signIn(access: "a", refresh: "r", tokenStore: self.store) }

        XCTAssertEqual(recorder.reasons, [.keychainError], "the sign-in failed, whether or not anything had been stored")
    }

    // MARK: A sign-out that couldn't tell what was stored

    func testSignOutWithoutATargetEndsWhateverIsStoredAndHandsItBack() async {
        signIn()

        let cleared = await refresher.signOut(tokenStore: store)

        XCTAssertEqual(cleared, "refresh-0")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(recorder.reasons, [.userSignOut])
    }

    func testSignOutWithoutATargetOnAnEmptyStoreLogsNothing() async {
        let cleared = await refresher.signOut(tokenStore: store)

        XCTAssertNil(cleared)
        XCTAssertTrue(recorder.reasons.isEmpty)
    }

    func testASignOutThatCannotReadTheKeychainAtAllStillClearsAndSaysSo() async {
        signIn()
        keychain.failReads(with: errSecInteractionNotAllowed)

        let cleared = await refresher.signOut(tokenStore: store)

        keychain.stopFailingReads()
        XCTAssertNil(cleared, "there was nothing readable to hand back")
        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
        XCTAssertEqual(recorder.reasons, [.userSignOut])
        XCTAssertTrue(recorder.details.first?.contains("couldn't be read") == true)
    }

    func testAnIncompleteSignOutSaysSoInTheLog() async {
        signIn()
        keychain.failDeletes(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        let cleared = await refresher.signOut(ifRefreshToken: "refresh-0", tokenStore: store)

        XCTAssertEqual(cleared, "refresh-0")
        XCTAssertNil(store.accessToken, "the other token still went")
        XCTAssertEqual(recorder.reasons, [.userSignOut])
        XCTAssertTrue(recorder.details.first?.contains("couldn't be deleted") == true, "the log doesn't claim a clean sign-out")
        XCTAssertEqual(recorder.deleteFailures.map(\.token), ["refresh token"])
        XCTAssertEqual(recorder.deleteFailures.map(\.status), [errSecInteractionNotAllowed])
    }

    func testAnIncompleteClearAfterARejectionSaysSoToo() async {
        signIn()
        keychain.failDeletes(of: InMemoryKeychain.accessAccount, with: errSecInteractionNotAllowed)
        backend.setOverride { $0.isRefresh ? .tokenNotValid() : nil }

        let error = await thrownError { try await exercise(.launchRefresh, with: makeClient()) }

        XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized)
        XCTAssertEqual(recorder.reasons, [.serverRejection])
        XCTAssertTrue(recorder.details.first?.contains("the refresh token was rejected; a token couldn't be deleted") == true)
        XCTAssertEqual(recorder.deleteFailures.map(\.token), ["access token"])
    }

    // MARK: Assertion helpers

    /// A refresh that fails for a temporary reason must keep both tokens and surface the underlying error, never
    /// `.unauthorized`, whichever way the refresh was triggered.
    private func assertRefreshFailsTemporarily(
        _ reply: RefreshTestReply,
        as expected: RefreshErrorKind,
        httpRequestsPerAttempt: Int = 1,
        label: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for entryPoint in RefreshEntryPoint.allCases {
            let context = "\(entryPoint) \(label)"
            signIn(access: "access-0", refresh: "refresh-0")
            backend.setOverride { $0.isRefresh ? reply : nil }
            let before = backend.refreshRequests.count

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertEqual(error.map(RefreshErrorKind.init), expected, context, file: file, line: line)
            XCTAssertNotEqual(error.map(RefreshErrorKind.init), .unauthorized, context, file: file, line: line)
            XCTAssertEqual(store.accessToken, "access-0", "\(context): access token kept", file: file, line: line)
            XCTAssertEqual(store.refreshToken, "refresh-0", "\(context): refresh token kept", file: file, line: line)
            XCTAssertEqual(
                backend.refreshRequests.count - before,
                httpRequestsPerAttempt,
                "\(context): refresh HTTP requests",
                file: file,
                line: line
            )
        }
    }

    /// The server rotates the token but the response never arrives. The immediate retry with the same token lands
    /// inside the server's grace window and comes back with a fresh pair, so the session survives, whichever way
    /// the refresh was triggered.
    private func assertLostRotationIsRecovered(
        after code: URLError.Code,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for entryPoint in RefreshEntryPoint.allCases {
            signIn()
            backend.setGrace(seconds: 60)
            backend.loseNextRefreshResponses([code])
            let before = backend.refreshRequests.count

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertNil(error, "\(entryPoint)", file: file, line: line)
            XCTAssertEqual(
                backend.refreshRequests.dropFirst(before).map(\.refreshToken),
                ["refresh-0", "refresh-0"],
                "\(entryPoint): the same token, twice",
                file: file,
                line: line
            )
            let stored = store.refreshToken
            XCTAssertNotEqual(stored, "refresh-0", "\(entryPoint)", file: file, line: line)
            XCTAssertTrue(
                stored.map { backend.liveRefreshTokens.contains($0) } ?? false,
                "\(entryPoint): the stored token is one the server accepts",
                file: file,
                line: line
            )
        }
    }

    /// Like `assertLostRotationIsRecovered(after:)`, but a gateway in front of the server answers with `status`.
    private func assertLostRotationIsRecovered(
        afterGatewayStatus status: Int,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for entryPoint in RefreshEntryPoint.allCases {
            signIn()
            backend.setGrace(seconds: 60)
            backend.loseNextRefreshResponses(statuses: [status])
            let before = backend.refreshRequests.count

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertNil(error, "\(entryPoint) \(status)", file: file, line: line)
            XCTAssertEqual(
                backend.refreshRequests.dropFirst(before).map(\.refreshToken),
                ["refresh-0", "refresh-0"],
                "\(entryPoint) \(status): the same token, twice",
                file: file,
                line: line
            )
            XCTAssertTrue(
                store.refreshToken.map { backend.liveRefreshTokens.contains($0) } ?? false,
                "\(entryPoint) \(status): the stored token is one the server accepts",
                file: file,
                line: line
            )
        }
    }

    /// Only the server's own rejection of the token ends the session: cleared tokens and `.unauthorized`,
    /// whichever way the refresh was triggered.
    private func assertRefreshRejected(
        _ reply: RefreshTestReply,
        label: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for entryPoint in RefreshEntryPoint.allCases {
            let context = "\(entryPoint) \(label)"
            signIn(access: "access-0", refresh: "refresh-0")
            backend.setOverride { $0.isRefresh ? reply : nil }

            let error = await thrownError { try await exercise(entryPoint, with: makeClient()) }

            XCTAssertEqual(error.map(RefreshErrorKind.init), .unauthorized, context, file: file, line: line)
            XCTAssertNil(store.accessToken, "\(context): access token cleared", file: file, line: line)
            XCTAssertNil(store.refreshToken, "\(context): refresh token cleared", file: file, line: line)
        }
    }
}
