import Foundation
import Security
import XCTest
@testable import Spine

/// The store against the real Keychain, saving and restoring whatever the simulator holds.
@MainActor
final class KeychainTokenStoreTests: XCTestCase {
    private let store = KeychainTokenStore.shared
    private var savedAccessToken: String?
    private var savedRefreshToken: String?

    override func setUp() {
        super.setUp()
        savedAccessToken = store.accessToken
        savedRefreshToken = store.refreshToken
        store.clear()
    }

    override func tearDown() {
        store.accessToken = savedAccessToken
        store.refreshToken = savedRefreshToken
        super.tearDown()
    }

    func testWriteAddsUpdatesAndDeletes() {
        store.accessToken = "first" // no item yet: falls back to add
        XCTAssertEqual(store.accessToken, "first")

        store.accessToken = "second" // existing item: updated in place
        XCTAssertEqual(store.accessToken, "second")

        store.accessToken = nil // only nil deletes
        XCTAssertNil(store.accessToken)

        store.accessToken = "third" // and the store is usable again afterwards
        XCTAssertEqual(store.accessToken, "third")
    }

    func testTokensAreStoredIndependentlyAndClearRemovesBoth() {
        store.accessToken = "access"
        store.refreshToken = "refresh"
        store.accessToken = "access-2"

        XCTAssertEqual(store.accessToken, "access-2")
        XCTAssertEqual(store.refreshToken, "refresh")

        store.clear()

        XCTAssertNil(store.accessToken)
        XCTAssertNil(store.refreshToken)
    }

    func testCheckedReadsAndWritesWorkAgainstTheRealKeychain() throws {
        XCTAssertNil(try store.loadRefreshToken(), "no item is a nil, not an error")

        try store.setRefreshToken("refresh")
        XCTAssertEqual(try store.loadRefreshToken(), "refresh")

        try store.setRefreshToken(nil)
        XCTAssertNil(try store.loadRefreshToken())
        XCTAssertNoThrow(try store.setRefreshToken(nil), "deleting what isn't there is fine")
    }

    func testItemsStayAccessibleAfterFirstUnlockThroughUpdates() {
        for value in ["first", "second"] {
            store.accessToken = value
            let query: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrAccount as String: "spine.accessToken",
                kSecReturnAttributes as String: true,
                kSecMatchLimit as String: kSecMatchLimitOne,
            ]
            var item: CFTypeRef?
            XCTAssertEqual(SecItemCopyMatching(query as CFDictionary, &item), errSecSuccess)
            let attributes = item as? [String: Any]
            XCTAssertEqual(
                attributes?[kSecAttrAccessible as String] as? String,
                kSecAttrAccessibleAfterFirstUnlock as String
            )
        }
    }

    func testReplacingATokenNeverReadsAsMissing() async {
        let store = store
        store.accessToken = "seed"
        let finished = RefreshFlag()
        let misses = RefreshCounter()

        let reader = Task.detached {
            // Bounded, so a writer that stalls or crashes can't leave this core spinning for the rest of the run.
            let deadline = Date().addingTimeInterval(30)
            while !finished.isSet, Date() < deadline {
                if store.accessToken == nil { misses.increment() }
            }
        }
        for index in 0 ..< 300 {
            store.accessToken = "token-\(index)"
        }
        finished.set()
        await reader.value

        XCTAssertEqual(misses.value, 0, "a reader saw the token missing while it was being replaced")
        XCTAssertEqual(store.accessToken, "token-299")
    }
}

/// How the store reads and writes when the Keychain misbehaves, through the seam and an in-memory Keychain.
@MainActor
final class KeychainTokenStoreFailureTests: XCTestCase {
    private var keychain: InMemoryKeychain!
    private var store: KeychainTokenStore!

    override func setUp() {
        super.setUp()
        keychain = InMemoryKeychain()
        store = KeychainTokenStore(keychain: keychain)
    }

    func testALookupTellsNoTokenFromAFailedLookup() throws {
        XCTAssertNil(try store.loadAccessToken(), "nothing stored is a nil")

        try store.setAccessToken("token")
        XCTAssertEqual(try store.loadAccessToken(), "token")

        keychain.failReads(with: errSecInteractionNotAllowed)
        XCTAssertThrowsError(try store.loadAccessToken()) { error in
            XCTAssertEqual(error as? KeychainError, KeychainError(status: errSecInteractionNotAllowed))
        }
        XCTAssertThrowsError(try store.loadRefreshToken(), "a failed lookup throws even when nothing is stored")
    }

    func testThePropertiesReadAFailedLookupAsNoToken() {
        store.accessToken = "token"
        keychain.failReads(with: errSecInteractionNotAllowed)

        XCTAssertNil(store.accessToken, "UI that only asks whether a session exists sees none")

        keychain.stopFailingReads()
        XCTAssertEqual(store.accessToken, "token", "and nothing was lost")
    }

    func testFailedWritesThrowTheirStatus() {
        keychain.failWrites(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        XCTAssertThrowsError(try store.setRefreshToken("refresh")) { error in
            XCTAssertEqual(error as? KeychainError, KeychainError(status: errSecInteractionNotAllowed))
        }
        XCTAssertNoThrow(try store.setAccessToken("access"), "other accounts are unaffected")
    }

    func testDeletingWhatIsNotThereIsNotAnError() {
        XCTAssertNoThrow(try store.setAccessToken(nil))
    }

    func testAnAddThatLosesARaceFallsBackToAnUpdate() throws {
        let racing = LosesTheAddRaceKeychain()
        let store = KeychainTokenStore(keychain: racing)

        try store.setAccessToken("token")

        XCTAssertEqual(racing.calls, ["update", "add", "update"])
    }

    func testClearDeletesTheRefreshTokenFirstBecauseItIsTheLongLivedCredential() {
        store.accessToken = "access"
        store.refreshToken = "refresh"

        store.clear()

        XCTAssertEqual(keychain.deletes, [InMemoryKeychain.refreshAccount, InMemoryKeychain.accessAccount])
    }

    func testClearTriesBothTokensEvenWhenTheFirstDeleteFails() {
        store.accessToken = "access"
        store.refreshToken = "refresh"
        keychain.failDeletes(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        store.clear()

        XCTAssertNil(store.accessToken, "the second token is still removed")
        XCTAssertEqual(store.refreshToken, "refresh", "a delete that fails leaves its token, as best effort can")
    }

    func testClearReportsEveryTokenItCouldNotDeleteAndSaysWhetherBothAreGone() {
        let recorder = SessionLogRecorder()
        let store = KeychainTokenStore(keychain: keychain, onDeleteFailure: { recorder.recordDeleteFailure($0, $1) })
        store.accessToken = "access"
        store.refreshToken = "refresh"

        XCTAssertTrue(store.clear())
        XCTAssertTrue(recorder.deleteFailures.isEmpty)

        store.accessToken = "access"
        store.refreshToken = "refresh"
        keychain.failDeletes(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)
        keychain.failDeletes(of: InMemoryKeychain.accessAccount, with: errSecAuthFailed)

        XCTAssertFalse(store.clear())
        XCTAssertEqual(recorder.deleteFailures.map(\.token), ["refresh token", "access token"])
        XCTAssertEqual(recorder.deleteFailures.map(\.status), [errSecInteractionNotAllowed, errSecAuthFailed])
    }

    func testClearingWhatIsNotThereIsNotAFailure() {
        let recorder = SessionLogRecorder()
        let store = KeychainTokenStore(keychain: keychain, onDeleteFailure: { recorder.recordDeleteFailure($0, $1) })

        XCTAssertTrue(store.clear())
        XCTAssertTrue(recorder.deleteFailures.isEmpty)
    }

    func testTheErrorHasAReadableDescription() {
        XCTAssertNotNil(KeychainError(status: errSecInteractionNotAllowed).errorDescription)
    }
}

/// What the app asks the repository at launch: is there a session, and whose.
@MainActor
final class StoredSessionTests: XCTestCase {
    private var keychain: InMemoryKeychain!
    private var store: KeychainTokenStore!
    private var auth: APIAuthRepository!

    override func setUp() {
        super.setUp()
        keychain = InMemoryKeychain()
        store = KeychainTokenStore(keychain: keychain)
        let client = APIClient(tokenProvider: store, refresher: TokenRefresher())
        auth = APIAuthRepository(service: AuthService(client: client), tokenStore: store)
    }

    func testNothingStoredIsNoSessionAndNotAnError() throws {
        XCTAssertFalse(try auth.hasStoredTokens)
    }

    func testEitherTokenIsASession() throws {
        store.refreshToken = "refresh"
        XCTAssertTrue(try auth.hasStoredTokens)

        store.clear()
        store.accessToken = "access"
        XCTAssertTrue(try auth.hasStoredTokens)
    }

    func testAKeychainThatCannotBeReadIsAnErrorNotNoSession() {
        store.refreshToken = "refresh"
        store.accessToken = "access"
        keychain.failReads(with: errSecInteractionNotAllowed)

        XCTAssertThrowsError(try auth.hasStoredTokens) { error in
            XCTAssertEqual(error as? KeychainError, KeychainError(status: errSecInteractionNotAllowed))
        }
    }

    func testAnUnreadableTokenDoesNotHideTheOneThatIsThere() throws {
        store.refreshToken = "refresh"
        store.accessToken = "access"

        keychain.failReads(of: InMemoryKeychain.accessAccount, with: errSecInteractionNotAllowed)
        XCTAssertTrue(try auth.hasStoredTokens, "the refresh token alone proves a session")

        keychain.stopFailingReads()
        keychain.failReads(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)
        XCTAssertTrue(try auth.hasStoredTokens, "and so does the access token alone")
    }

    func testAnUnreadableTokenWithNothingElseStoredIsAnError() {
        keychain.failReads(of: InMemoryKeychain.refreshAccount, with: errSecInteractionNotAllowed)

        XCTAssertThrowsError(try auth.hasStoredTokens, "the unreadable one might be a session")
    }

    func testTheStoredUserComesFromTheTokens() {
        let soon = Date().addingTimeInterval(900)
        store.refreshToken = makeJWT(expiringAt: soon, kind: "refresh", userID: 5)
        XCTAssertEqual(auth.storedUserID, 5)

        store.clear()
        store.accessToken = makeJWT(expiringAt: soon, userID: 6)
        XCTAssertEqual(auth.storedUserID, 6, "the access token will do when there is no refresh token")
    }

    func testTokensThatCarryNoUserGiveNoStoredUser() {
        XCTAssertNil(auth.storedUserID, "nothing stored")

        store.refreshToken = "opaque-refresh"
        store.accessToken = "opaque-access"
        XCTAssertNil(auth.storedUserID)

        keychain.failReads(with: errSecInteractionNotAllowed)
        XCTAssertNil(auth.storedUserID, "nor can an unreadable Keychain tell")
    }
}

/// `update` finds nothing, `add` collides with an item a concurrent writer just created, and the second `update` works.
private final class LosesTheAddRaceKeychain: KeychainOperations, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [String] = []

    var calls: [String] { lock.withLock { log } }

    func read(account _: String) -> (status: OSStatus, data: Data?) {
        (errSecItemNotFound, nil)
    }

    func update(account _: String, data _: Data) -> OSStatus {
        lock.withLock {
            log.append("update")
            return log.filter { $0 == "update" }.count == 1 ? errSecItemNotFound : errSecSuccess
        }
    }

    func add(account _: String, data _: Data) -> OSStatus {
        lock.withLock {
            log.append("add")
            return errSecDuplicateItem
        }
    }

    func delete(account _: String) -> OSStatus {
        errSecSuccess
    }
}
