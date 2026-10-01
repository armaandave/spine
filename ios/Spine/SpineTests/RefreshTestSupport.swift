import Foundation
import Security
import XCTest
@testable import Spine

// MARK: - Fake server

enum RefreshTestPath {
    static let refresh = "/api/v1/auth/refresh"
    static let logout = "/api/v1/auth/logout"
    static let login = "/api/v1/auth/login"
    static let register = "/api/v1/auth/register"
    static let me = "/api/v1/me"
    static let health = "/api/v1/health"
    static let upload = "/api/v1/imports/upload"
}

/// One request as the fake server saw it.
struct RefreshTestRequest: Sendable {
    let method: String
    let path: String
    let bearer: String?
    let body: Data

    init(_ request: URLRequest) {
        method = request.httpMethod ?? "GET"
        path = Self.normalizedPath(of: request)
        let authorization = request.value(forHTTPHeaderField: "Authorization")
        bearer = authorization?.hasPrefix("Bearer ") == true ? String(authorization!.dropFirst("Bearer ".count)) : nil
        body = Self.readBody(of: request)
    }

    var isRefresh: Bool { path == RefreshTestPath.refresh }
    var isLogout: Bool { path == RefreshTestPath.logout }
    var isMe: Bool { path == RefreshTestPath.me }

    /// The `refresh` token in a refresh or logout body.
    var refreshToken: String? {
        (try? JSONSerialization.jsonObject(with: body) as? [String: Any])?["refresh"] as? String
    }

    static func normalizedPath(of request: URLRequest) -> String {
        var path = request.url?.path ?? ""
        while path.count > 1, path.hasSuffix("/") {
            path.removeLast()
        }
        return path
    }

    private static func readBody(of request: URLRequest) -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

struct RefreshTestReply: Sendable {
    var statusCode = 200
    var headers: [String: String] = [:]
    var body = Data()
    var failureCode: URLError.Code?

    static func json(_ json: String, status: Int = 200) -> RefreshTestReply {
        RefreshTestReply(statusCode: status, headers: ["Content-Type": "application/json"], body: Data(json.utf8))
    }

    static func status(_ code: Int, headers: [String: String] = [:], body: String = "") -> RefreshTestReply {
        RefreshTestReply(statusCode: code, headers: headers, body: Data(body.utf8))
    }

    static func failure(_ code: URLError.Code) -> RefreshTestReply {
        RefreshTestReply(failureCode: code)
    }

    static func noContent() -> RefreshTestReply {
        RefreshTestReply(statusCode: 204)
    }

    // The API's error envelope, byte for byte what `api.exceptions.api_exception_handler` serializes.

    static func envelope(_ statusCode: Int, code: String, message: String, fields: String = "null") -> RefreshTestReply {
        var headers = ["Content-Type": "application/json"]
        if statusCode == 401 {
            headers["WWW-Authenticate"] = #"Bearer realm="api""#
        }
        let body = #"{"error":{"code":"\#(code)","message":"\#(message)","fields":\#(fields),"request_id":null}}"#
        return .status(statusCode, headers: headers, body: body)
    }

    /// 401 for a dead refresh token: blacklisted, invalid, expired or the wrong type.
    static func tokenNotValid(_ message: String = "Token is blacklisted") -> RefreshTestReply {
        envelope(401, code: "token_not_valid", message: message)
    }

    /// 401 for a token whose user is inactive or deleted.
    static func authenticationFailed() -> RefreshTestReply {
        envelope(401, code: "authentication_failed", message: "No active account found for the given token.")
    }

    /// 400 for a missing, blank or oversized `refresh` field.
    static func refreshFieldInvalid() -> RefreshTestReply {
        envelope(
            400,
            code: "invalid",
            message: "One or more fields are invalid.",
            fields: #"{"refresh":["This field is required."]}"#
        )
    }

    /// 401 a normal endpoint answers for an expired or invalid access token.
    static func accessTokenNotValid() -> RefreshTestReply {
        envelope(401, code: "token_not_valid", message: "Given token not valid for any token type")
    }

    /// An HTML error page: Django's DisallowedHost 400, an nginx 401. Not the API, so never a verdict on a token.
    static func html(_ statusCode: Int, _ page: String) -> RefreshTestReply {
        .status(statusCode, headers: ["Content-Type": "text/html"], body: page)
    }
}

enum RefreshErrorPage {
    /// Django's stock 400 page, which every request gets while ALLOWED_HOSTS is wrong.
    static let djangoBadRequest = #"<!doctype html><html lang="en"><head><title>Bad Request (400)</title></head><body><h1>Bad Request (400)</h1><p></p></body></html>"#
    /// nginx in front of the API asking for credentials.
    static let nginxUnauthorized = "<html><head><title>401 Authorization Required</title></head><body><center><h1>401 Authorization Required</h1></center><hr><center>nginx</center></body></html>"
}

/// Stands in for the Django API. An access token works until a test says otherwise. Every refresh rotates the
/// refresh token and blacklists the old one, exactly like the real server, so a client that races itself gets
/// rejected instead of passing by luck. `setGrace` turns on the server's real leniency: a token rotated moments
/// ago is answered with a brand-new pair. Handler state is lock-protected: requests arrive on several threads.
final class RefreshTestBackend: @unchecked Sendable {
    typealias Override = @Sendable (RefreshTestRequest) -> RefreshTestReply?

    static let signedInUserJSON = #"{"id":7,"username":"reader","display_name":"Reader","is_private":false,"avatar_url":null}"#

    private let lock = NSLock()
    private let profileJSON: String
    private var validAccessTokens: Set<String> = []
    private var validRefreshTokens: Set<String> = []
    private var rotatedAt: [String: Date] = [:]
    private var graceSeconds: TimeInterval = 0
    private var lostRefreshResponses: [RefreshTestReply] = []
    private var logoutNeedsAccessToken = false
    private var mintedCount = 0
    private var log: [RefreshTestRequest] = []
    private var overrideHandler: Override?
    private var heldPaths: Set<String> = []
    private var parked: [(path: String, delivery: @Sendable () -> Void)] = []

    init(profileJSON: String) {
        self.profileJSON = profileJSON
    }

    // Setup

    func accept(access: String? = nil, refresh: String? = nil) {
        lock.withLock {
            if let access { validAccessTokens.insert(access) }
            if let refresh { validRefreshTokens.insert(refresh) }
        }
    }

    /// Runs before the default behavior; return nil to fall through to it.
    func setOverride(_ handler: Override?) {
        lock.withLock { overrideHandler = handler }
    }

    /// Answer a token rotated within the last `seconds` with a fresh pair, as the real server does for 60.
    func setGrace(seconds: TimeInterval) {
        lock.withLock { graceSeconds = seconds }
    }

    /// The next refreshes rotate on the server, but their responses never arrive: they fail with these errors.
    func loseNextRefreshResponses(_ codes: [URLError.Code]) {
        lock.withLock { lostRefreshResponses = codes.map { .failure($0) } }
    }

    /// The next refreshes rotate on the server, but a gateway in front of it answers with these statuses instead.
    func loseNextRefreshResponses(statuses: [Int]) {
        lock.withLock { lostRefreshResponses = statuses.map { .status($0) } }
    }

    /// Behave like a server from before logout became unauthenticated: it wants a valid access token.
    func requireAuthenticatedLogout() {
        lock.withLock { logoutNeedsAccessToken = true }
    }

    /// Replies for `path` wait, once the server has processed the request, until `release(path:)`.
    func hold(path: String) {
        lock.withLock { _ = heldPaths.insert(path) }
    }

    func release(path: String) {
        let ready = lock.withLock { () -> [@Sendable () -> Void] in
            heldPaths.remove(path)
            let ready = parked.filter { $0.path == path }.map(\.delivery)
            parked.removeAll { $0.path == path }
            return ready
        }
        ready.forEach { $0() }
    }

    // Inspection

    var requests: [RefreshTestRequest] { lock.withLock { log } }
    var refreshRequests: [RefreshTestRequest] { requests.filter(\.isRefresh) }
    var liveRefreshTokens: Set<String> { lock.withLock { validRefreshTokens } }

    func requests(path: String) -> [RefreshTestRequest] {
        requests.filter { $0.path == path }
    }

    // Serving

    func reply(to urlRequest: URLRequest) -> RefreshTestReply {
        let request = RefreshTestRequest(urlRequest)
        let handler = lock.withLock { () -> Override? in
            log.append(request)
            return overrideHandler
        }
        // Overrides run outside the lock: they may touch the token store or this backend.
        if let reply = handler?(request) { return reply }
        return lock.withLock { defaultReply(to: request) }
    }

    /// Delivers now, unless replies for `path` are held: then delivery waits for `release(path:)`.
    func deliver(path: String, _ delivery: @escaping @Sendable () -> Void) {
        let parkedNow = lock.withLock { () -> Bool in
            guard heldPaths.contains(path) else { return false }
            parked.append((path, delivery))
            return true
        }
        if !parkedNow { delivery() }
    }

    private func defaultReply(to request: RefreshTestRequest) -> RefreshTestReply {
        switch request.path {
        case RefreshTestPath.refresh:
            return refreshReply(to: request)
        case RefreshTestPath.logout:
            return logoutReply(to: request)
        case RefreshTestPath.login:
            return signInReply(status: 200)
        case RefreshTestPath.register:
            return signInReply(status: 201)
        default:
            break
        }
        guard let bearer = request.bearer else {
            return .envelope(401, code: "not_authenticated", message: "Authentication credentials were not provided.")
        }
        guard validAccessTokens.contains(bearer) else {
            return .accessTokenNotValid()
        }
        if request.path == RefreshTestPath.me {
            return .json(profileJSON)
        }
        return .json(#"{"status":"ok","version":"v1","time":"now"}"#)
    }

    private func refreshReply(to request: RefreshTestRequest) -> RefreshTestReply {
        guard let token = request.refreshToken else {
            return .tokenNotValid("Token is invalid")
        }
        let withinGrace = rotatedAt[token].map { Date().timeIntervalSince($0) <= graceSeconds } ?? false
        if validRefreshTokens.remove(token) != nil {
            rotatedAt[token] = Date()
        } else if !withinGrace {
            return .tokenNotValid("Token is blacklisted")
        }
        let pair = mintPair()
        if !lostRefreshResponses.isEmpty {
            return lostRefreshResponses.removeFirst() // rotated on the server, response lost
        }
        return .json(#"{"access":"\#(pair.access)","refresh":"\#(pair.refresh)"}"#)
    }

    /// Like the real endpoint: no authentication, the refresh token in the body is the only credential, and the
    /// answer is 204 whatever was posted. A valid token is revoked for good (it never gets the rotation grace).
    private func logoutReply(to request: RefreshTestRequest) -> RefreshTestReply {
        if logoutNeedsAccessToken {
            guard let bearer = request.bearer else {
                return .envelope(401, code: "not_authenticated", message: "Authentication credentials were not provided.")
            }
            guard validAccessTokens.contains(bearer) else {
                return .accessTokenNotValid()
            }
        }
        if let token = request.refreshToken {
            validRefreshTokens.remove(token) // revoked; an already rotated token is simply dead
        }
        return .noContent()
    }

    private func signInReply(status: Int) -> RefreshTestReply {
        let pair = mintPair()
        return .json(
            #"{"access":"\#(pair.access)","refresh":"\#(pair.refresh)","user":\#(Self.signedInUserJSON)}"#,
            status: status
        )
    }

    private func mintPair() -> (access: String, refresh: String) {
        mintedCount += 1
        let access = "access-\(mintedCount)"
        let refresh = "refresh-\(mintedCount)"
        validAccessTokens.insert(access)
        validRefreshTokens.insert(refresh)
        return (access, refresh)
    }
}

/// URLProtocol instances are handed a client that is safe to call from any thread; this says so.
private struct UncheckedSendableBox<Value>: @unchecked Sendable {
    let value: Value

    init(_ value: Value) {
        self.value = value
    }
}

/// Routes each request to the backend registered for its host. Every test has a host of its own, so a request that
/// outlives its test (a background revocation, a late retry) can only reach its own, already forgotten, backend and
/// never the one of the test that happens to be running.
final class RefreshTestURLProtocol: URLProtocol {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var backends: [String: RefreshTestBackend] = [:]

    static func register(_ backend: RefreshTestBackend, forHost host: String) {
        lock.withLock { backends[host] = backend }
    }

    static func unregister(host: String) {
        lock.withLock { _ = backends.removeValue(forKey: host) }
    }

    private static func backend(forHost host: String?) -> RefreshTestBackend? {
        guard let host else { return nil }
        return lock.withLock { backends[host] }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let backend = Self.backend(forHost: request.url?.host) else {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotConnectToHost))
            return
        }
        let reply = backend.reply(to: request)
        let protocolInstance = UncheckedSendableBox(self)
        backend.deliver(path: RefreshTestRequest.normalizedPath(of: request)) { protocolInstance.value.deliver(reply) }
    }

    override func stopLoading() {}

    private func deliver(_ reply: RefreshTestReply) {
        if let failureCode = reply.failureCode {
            client?.urlProtocol(self, didFailWithError: URLError(failureCode))
            return
        }
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: reply.statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: reply.headers
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: reply.body)
        client?.urlProtocolDidFinishLoading(self)
    }
}

// MARK: - In-memory Keychain

/// A Keychain the tests own: nothing here touches the real one, and it can be told to fail.
final class InMemoryKeychain: KeychainOperations, @unchecked Sendable {
    static let accessAccount = "spine.accessToken"
    static let refreshAccount = "spine.refreshToken"

    private let lock = NSLock()
    private var items: [String: Data] = [:]
    private var readFailure: OSStatus?
    private var readFailures: [String: OSStatus] = [:]
    private var readFailuresOnce: [String: OSStatus] = [:]
    private var writeFailures: [String: OSStatus] = [:]
    private var deleteFailures: [String: OSStatus] = [:]
    private var writeLog: [String] = []
    private var deleteLog: [String] = []
    private var readCounts: [String: Int] = [:]
    private var readHook: (account: String, afterReads: Int, action: @Sendable () -> Void)?
    private var staleReads: [String: Data] = [:]

    /// Every read fails with `status` until `stopFailingReads`.
    func failReads(with status: OSStatus) {
        lock.withLock { readFailure = status }
    }

    /// Reads of `account` alone fail with `status` until `stopFailingReads`.
    func failReads(of account: String, with status: OSStatus) {
        lock.withLock { readFailures[account] = status }
    }

    /// The next read of `account` fails with `status`; the ones after it work again.
    func failNextRead(of account: String, with status: OSStatus) {
        lock.withLock { readFailuresOnce[account] = status }
    }

    func stopFailingReads() {
        lock.withLock {
            readFailure = nil
            readFailures.removeAll()
            readFailuresOnce.removeAll()
        }
    }

    /// The next read of `account` returns `value` whatever is stored, as a read that was overtaken by a write would.
    func returnOnNextRead(of account: String, _ value: String) {
        lock.withLock { staleReads[account] = Data(value.utf8) }
    }

    /// Runs `action` right after the `afterReads`-th read of `account` has returned its value, so a test can
    /// change the store at an exact point inside a flow that reads it several times.
    func onRead(of account: String, afterReads: Int, _ action: @escaping @Sendable () -> Void) {
        lock.withLock { readHook = (account, afterReads, action) }
    }

    /// Writes to `account` fail with `status` until `stopFailingWrites`.
    func failWrites(of account: String, with status: OSStatus) {
        lock.withLock { writeFailures[account] = status }
    }

    func stopFailingWrites() {
        lock.withLock { writeFailures.removeAll() }
    }

    /// Deleting `account` fails with `status`.
    func failDeletes(of account: String, with status: OSStatus) {
        lock.withLock { deleteFailures[account] = status }
    }

    /// The accounts written so far, in order (updates and adds).
    var writes: [String] { lock.withLock { writeLog } }

    /// The accounts deleted so far, in order.
    var deletes: [String] { lock.withLock { deleteLog } }

    func resetWriteLog() {
        lock.withLock { writeLog.removeAll() }
    }

    func read(account: String) -> (status: OSStatus, data: Data?) {
        var hook: (@Sendable () -> Void)?
        let result = lock.withLock { () -> (status: OSStatus, data: Data?) in
            if let readFailure { return (readFailure, nil) }
            if let failure = readFailures[account] { return (failure, nil) }
            if let failure = readFailuresOnce.removeValue(forKey: account) { return (failure, nil) }
            if let stale = staleReads.removeValue(forKey: account) { return (errSecSuccess, stale) }
            readCounts[account, default: 0] += 1
            if let readHook, readHook.account == account, readHook.afterReads == readCounts[account] {
                hook = readHook.action
                self.readHook = nil
            }
            if let data = items[account] { return (errSecSuccess, data) }
            return (errSecItemNotFound, nil)
        }
        hook?() // outside the lock: it writes to this Keychain
        return result
    }

    func update(account: String, data: Data) -> OSStatus {
        lock.withLock {
            if let failure = writeFailures[account] { return failure }
            guard items[account] != nil else { return errSecItemNotFound }
            items[account] = data
            writeLog.append(account)
            return errSecSuccess
        }
    }

    func add(account: String, data: Data) -> OSStatus {
        lock.withLock {
            if let failure = writeFailures[account] { return failure }
            if items[account] != nil { return errSecDuplicateItem }
            items[account] = data
            writeLog.append(account)
            return errSecSuccess
        }
    }

    func delete(account: String) -> OSStatus {
        lock.withLock {
            deleteLog.append(account)
            if let failure = deleteFailures[account] { return failure }
            return items.removeValue(forKey: account) == nil ? errSecItemNotFound : errSecSuccess
        }
    }
}

// MARK: - Helpers

enum RefreshErrorKind: Equatable {
    case unauthorized
    case network(URLError.Code)
    case httpStatus(Int)
    case decoding
    case keychain(OSStatus)
    case cancelled
    case other(String)

    init(_ error: Error) {
        switch error {
        case APIError.unauthorized:
            self = .unauthorized
        case let APIError.network(inner):
            self = .network((inner as? URLError)?.code ?? .unknown)
        case let APIError.httpStatus(code, _):
            self = .httpStatus(code)
        case APIError.decoding:
            self = .decoding
        case let error as KeychainError:
            self = .keychain(error.status)
        case is CancellationError:
            self = .cancelled
        default:
            self = .other(String(describing: error))
        }
    }
}

/// The two ways the app asks for a refresh: a 401 on a normal request, and the launch-time refresh.
enum RefreshEntryPoint: CaseIterable {
    case authenticatedRequest
    case launchRefresh
}

actor RefreshGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !isOpen else { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        isOpen = true
        let pending = waiters
        waiters = []
        pending.forEach { $0.resume() }
    }
}

final class RefreshExchangeProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var started = 0
    private var cancelledDuringExchange = false

    var startCount: Int { lock.withLock { started } }
    var sawCancellation: Bool { lock.withLock { cancelledDuringExchange } }

    func begin() { lock.withLock { started += 1 } }
    func finish(cancelled: Bool) { lock.withLock { cancelledDuringExchange = cancelled } }
}

final class RefreshCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var value: Int { lock.withLock { count } }

    /// Increments and returns the new count.
    @discardableResult
    func increment() -> Int {
        lock.withLock {
            count += 1
            return count
        }
    }
}

final class RefreshFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var isSet: Bool { lock.withLock { value } }
    func set() { lock.withLock { value = true } }
}

/// A JWT-shaped token with the given expiry, and for `userID` if there is one. It isn't signed: the app only ever
/// reads its `exp` and `user_id`.
func makeJWT(expiringAt expiry: Date, kind: String = "access", userID: Int? = nil) -> String {
    func segment(_ object: [String: Any]) -> String {
        let data = try! JSONSerialization.data(withJSONObject: object)
        return data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
    var claims: [String: Any] = ["token_type": kind, "exp": Int(expiry.timeIntervalSince1970), "jti": UUID().uuidString]
    if let userID { claims["user_id"] = userID }
    return [
        segment(["alg": "HS256", "typ": "JWT"]),
        segment(claims),
        "signature",
    ].joined(separator: ".")
}

/// What the refresher reported each time it cleared the session, in order.
final class SessionLogRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [(reason: SessionEndReason, detail: String?)] = []
    private var failedDeletes: [(token: String, status: OSStatus)] = []

    var reasons: [SessionEndReason] { lock.withLock { recorded.map(\.reason) } }
    var details: [String] { lock.withLock { recorded.compactMap(\.detail) } }
    /// The tokens the Keychain refused to delete, in order.
    var deleteFailures: [(token: String, status: OSStatus)] { lock.withLock { failedDeletes } }

    func record(_ reason: SessionEndReason, _ detail: String?) {
        lock.withLock { recorded.append((reason, detail)) }
    }

    func recordDeleteFailure(_ token: String, _ status: OSStatus) {
        lock.withLock { failedDeletes.append((token, status)) }
    }

    func reset() {
        lock.withLock {
            recorded.removeAll()
            failedDeletes.removeAll()
        }
    }
}

/// Every import call fails as if offline: keeps persisted import jobs from touching the fake server.
struct OfflineImportRepository: ImportRepository {
    func queueLetterboxdImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func queueStoryGraphImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func queueGoodreadsImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func queueMyAnimeListImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func importTaskStatus(taskId _: String) async throws -> ImportTaskStatus {
        throw URLError(.notConnectedToInternet)
    }
}

/// An import server that never answers a status poll, so a resumed job stays `.processing` until cancelled.
struct ParkedImportRepository: ImportRepository {
    func queueLetterboxdImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func queueStoryGraphImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func queueGoodreadsImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func queueMyAnimeListImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        throw URLError(.notConnectedToInternet)
    }

    func importTaskStatus(taskId _: String) async throws -> ImportTaskStatus {
        try await Task.sleep(for: .seconds(3600))
        throw CancellationError()
    }
}

/// An import repository whose uploads and status checks each hang until cancelled, or fail with a given error.
struct StubImportRepository: ImportRepository {
    enum Behavior {
        case hang
        case fail(Error)
    }

    var upload: Behavior = .fail(URLError(.notConnectedToInternet))
    var status: Behavior = .fail(URLError(.notConnectedToInternet))
    /// How many uploads and status checks have reached this repository, so a test can wait for them.
    let uploadCalls = RefreshCounter()
    let statusCalls = RefreshCounter()

    private func run(_ behavior: Behavior) async throws -> Never {
        switch behavior {
        case .hang:
            try await Task.sleep(for: .seconds(3600)) // cancelled long before this ends
            throw CancellationError()
        case let .fail(error):
            throw error
        }
    }

    func queueLetterboxdImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        uploadCalls.increment()
        try await run(upload)
    }

    func queueStoryGraphImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        uploadCalls.increment()
        try await run(upload)
    }

    func queueGoodreadsImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        uploadCalls.increment()
        try await run(upload)
    }

    func queueMyAnimeListImport(
        fileData _: Data,
        fileName _: String,
        mode _: ImportMode,
        progressHandler _: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        uploadCalls.increment()
        try await run(upload)
    }

    func importTaskStatus(taskId _: String) async throws -> ImportTaskStatus {
        statusCalls.increment()
        try await run(status)
    }
}

/// The real profile repository, except that `me()` waits on a gate that ignores cancellation. It lets a test
/// deliver a profile after the session that asked for it has ended, which a real URLSession request can't do
/// (cancelling it aborts the request).
struct GatedProfileRepository: ProfileRepository {
    let base: ProfileRepository
    let profile: UserProfile
    let gate: RefreshGate
    /// What `me()` throws once the gate opens, instead of returning the profile.
    var failure: Error?
    /// How many times `me()` has been called.
    let calls = RefreshCounter()

    func me() async throws -> UserProfile {
        calls.increment()
        await gate.wait()
        if let failure { throw failure }
        return profile
    }

    func profile(username: String) async throws -> UserProfile {
        try await base.profile(username: username)
    }

    func statsSummary(username: String?, period: StatsPeriod) async throws -> StatsSummary {
        try await base.statsSummary(username: username, period: period)
    }

    func likedMedia() async throws -> [MediaSummary] {
        try await base.likedMedia()
    }

    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile {
        try await base.updateProfile(request)
    }

    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? {
        try await base.uploadAvatar(imageData: imageData, fileName: fileName, mimeType: mimeType)
    }

    func deleteAvatar() async throws -> String? {
        try await base.deleteAvatar()
    }

    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse {
        try await base.saveProfileBackdrop(ref: ref, backdropURL: backdropURL)
    }

    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse {
        try await base.clearProfileBackdrop()
    }

    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences {
        try await base.updatePreferences(request)
    }

    func changePassword(_ request: PasswordChangeRequest) async throws {
        try await base.changePassword(request)
    }

    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] {
        try await base.setHallOfFameItem(mediaType: mediaType, ref: ref)
    }

    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] {
        try await base.clearHallOfFameItem(mediaType: mediaType)
    }
}

// MARK: - Base test case

/// Shared setup: a fake server behind a URLProtocol-backed session, a fresh `TokenRefresher` per test, and a
/// Keychain and UserDefaults of the test's own. Nothing here touches the real Keychain, the standard defaults or
/// the network, so a test can't disturb the host app or another test, or be disturbed by them. The fake server
/// has a host of its own too, so a request that outlives its test can't reach the next test's server.
@MainActor
class RefreshTestCase: XCTestCase {
    // XCTest makes a fresh instance for every test method, so these are per test.
    let keychain = InMemoryKeychain()
    lazy var store = KeychainTokenStore(keychain: keychain, onDeleteFailure: { [recorder] in
        recorder.recordDeleteFailure($0, $1)
    })
    let recorder = SessionLogRecorder()
    lazy var refresher = TokenRefresher(log: { [recorder] in recorder.record($0, $1) })
    var backend: RefreshTestBackend!
    var defaults: UserDefaults!
    private let defaultsSuite = "spine.tests.\(UUID().uuidString)"
    private let host = "\(UUID().uuidString.lowercased()).spine-tests.invalid"

    override func setUpWithError() throws {
        try super.setUpWithError()
        backend = RefreshTestBackend(profileJSON: try Self.profileJSON())
        RefreshTestURLProtocol.register(backend, forHost: host)
        defaults = UserDefaults(suiteName: defaultsSuite)
    }

    override func tearDown() {
        RefreshTestURLProtocol.unregister(host: host)
        defaults.removePersistentDomain(forName: defaultsSuite)
        super.tearDown()
    }

    /// A client whose network is the fake server. Uses this test's refresher unless `useSharedRefresher` is set.
    func makeClient(useSharedRefresher: Bool = false) -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RefreshTestURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let baseURL = URL(string: "https://\(host)")!
        if useSharedRefresher {
            return APIClient(baseURL: baseURL, tokenProvider: store, session: session, multipartSession: session)
        }
        return APIClient(
            baseURL: baseURL,
            tokenProvider: store,
            session: session,
            multipartSession: session,
            refresher: refresher
        )
    }

    /// Stores tokens on the device. By default the access token has already expired (the server rejects it)
    /// while the refresh token is still good.
    func signIn(access: String = "access-0", refresh: String = "refresh-0", accessExpired: Bool = true) {
        store.accessToken = access
        store.refreshToken = refresh
        backend.accept(access: accessExpired ? nil : access, refresh: refresh)
    }

    /// A session over the real repositories and the fake server, with defaults of this test's own. Imports stay
    /// offline unless a test passes its own repository. It is signed out when the test ends, so a profile request
    /// or a background task it still has can't run on into the next test.
    func makeSession(
        imports: ImportRepository = OfflineImportRepository(),
        profile: ProfileRepository? = nil,
        auth: AuthRepository? = nil,
        launchShellDelay: Duration = .seconds(2)
    ) -> AppSession {
        let live = AppRepositories.live(client: makeClient())
        let session = AppSession(
            repositories: AppRepositories(
                auth: auth ?? live.auth,
                media: live.media,
                music: live.music,
                people: live.people,
                companies: live.companies,
                tracking: live.tracking,
                diary: live.diary,
                activity: live.activity,
                profile: profile ?? live.profile,
                lists: live.lists,
                filterOptions: live.filterOptions,
                imports: imports
            ),
            defaults: defaults,
            launchShellDelay: launchShellDelay
        )
        addTeardownBlock { await session.logout() }
        return session
    }

    // `@nonobjc`: on an NSObject subclass the compiler otherwise tries to give this async method with a
    // non-escaping closure an Objective-C thunk, which it can't build.
    @nonobjc
    func waitUntil(_ description: String, timeout: TimeInterval = 5, _ condition: () -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            guard Date() < deadline else { return XCTFail("Timed out waiting for \(description)") }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    /// Waits until `count` callers are waiting on the refresher's exchange, so a test that holds the exchange
    /// open can release it knowing every request has joined.
    func waitForPendingCallers(_ count: Int, timeout: TimeInterval = 5) async {
        let deadline = Date().addingTimeInterval(timeout)
        var pending = await refresher.pendingCallerCount
        while pending < count {
            guard Date() < deadline else {
                return XCTFail("Timed out waiting for \(count) pending callers (have \(pending))")
            }
            try? await Task.sleep(for: .milliseconds(5))
            pending = await refresher.pendingCallerCount
        }
    }

    // Calls

    func fetchHealth(_ client: APIClient) async throws -> String {
        let response: HealthResponse = try await client.get("/health/", authenticated: true)
        return response.status
    }

    func upload(with client: APIClient) async throws -> String {
        let response: HealthResponse = try await client.uploadMultipart(
            "/imports/upload/",
            formFields: ["mode": "new"],
            fileFieldName: "file",
            fileName: "ratings.csv",
            fileData: Data("title,rating\nHeat,4.5\n".utf8),
            mimeType: "text/csv"
        )
        return response.status
    }

    /// Triggers a refresh the way `entryPoint` does.
    func exercise(_ entryPoint: RefreshEntryPoint, with client: APIClient) async throws {
        switch entryPoint {
        case .authenticatedRequest:
            _ = try await fetchHealth(client)
        case .launchRefresh:
            try await AuthService(client: client).refresh()
        }
    }

    func thrownError(_ body: () async throws -> Void) async -> Error? {
        do {
            try await body()
            return nil
        } catch {
            return error
        }
    }

    private static func profileJSON() throws -> String {
        String(decoding: try JSONEncoder.api.encode(makeProfile()), as: UTF8.self)
    }

    static func makeProfile() -> UserProfile {
        UserProfile(
            id: 7,
            username: "reader",
            displayName: "Reader",
            email: nil,
            bio: nil,
            pronouns: nil,
            location: nil,
            avatarUrl: nil,
            profileBackdropUrl: nil,
            profileBackdropItem: nil,
            isPrivate: false,
            viewerRelationship: ViewerRelationship(following: false, followedBy: false, requested: false, blocked: false),
            counts: ProfileCounts(followers: 0, following: 0, diaryEntries: 0, lists: 0, reviews: 0, tags: 0),
            hof: [:],
            preferences: UserPreferences(
                enabledMediaTypes: ["movie"],
                dateFormat: "Y-m-d",
                timeFormat: "H:i",
                weekStartDay: "monday",
                quickWatchDate: "current_date",
                releaseNotificationsEnabled: false,
                dailyDigestEnabled: false
            )
        )
    }
}
