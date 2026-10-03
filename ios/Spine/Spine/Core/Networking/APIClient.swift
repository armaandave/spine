import Foundation

extension Notification.Name {
    /// Posted after every successful authenticated API response: proof that the network and the session work.
    static let authenticatedRequestSucceeded = Notification.Name("spine.authenticatedRequestSucceeded")
}

struct APIClient: Sendable {
    static let defaultSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 15
        // The API sends no cache headers, so URLCache would otherwise replay stale JSON (e.g. Stats
        // from before a deploy or a new log). Poster images use URLSession.shared and keep their cache.
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }()

    static let uploadSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 300
        return URLSession(configuration: config)
    }()

    let baseURL: URL
    let tokenProvider: KeychainTokenStore
    let session: URLSession
    let multipartSession: URLSession
    /// Shared by default so every client instance funnels its refreshes through one gate.
    let refresher: TokenRefresher

    init(
        baseURL: URL = AppConfig.apiBaseURL,
        tokenProvider: KeychainTokenStore = .shared,
        session: URLSession = APIClient.defaultSession,
        multipartSession: URLSession = APIClient.uploadSession,
        refresher: TokenRefresher = .shared
    ) {
        self.baseURL = baseURL
        self.tokenProvider = tokenProvider
        self.session = session
        self.multipartSession = multipartSession
        self.refresher = refresher
    }

    func get<T: Decodable>(
        _ path: String,
        query: [URLQueryItem] = [],
        authenticated: Bool = false,
        requestTimeout: TimeInterval? = nil
    ) async throws -> T {
        try await request(
            path: path,
            method: "GET",
            query: query,
            body: Optional<Data>.none,
            authenticated: authenticated,
            requestTimeout: requestTimeout
        )
    }

    func post<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        authenticated: Bool = false
    ) async throws -> Response {
        let data = try JSONEncoder.api.encode(body)
        return try await request(path: path, method: "POST", query: [], body: data, authenticated: authenticated)
    }

    func put<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        authenticated: Bool = false
    ) async throws -> Response {
        let data = try JSONEncoder.api.encode(body)
        return try await request(path: path, method: "PUT", query: [], body: data, authenticated: authenticated)
    }

    func patch<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        authenticated: Bool = false
    ) async throws -> Response {
        let data = try JSONEncoder.api.encode(body)
        return try await request(path: path, method: "PATCH", query: [], body: data, authenticated: authenticated)
    }

    func delete<T: Decodable>(_ path: String, query: [URLQueryItem] = [], authenticated: Bool = false) async throws -> T {
        try await request(path: path, method: "DELETE", query: query, body: Optional<Data>.none, authenticated: authenticated)
    }

    func delete<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        authenticated: Bool = false
    ) async throws -> Response {
        let data = try JSONEncoder.api.encode(body)
        return try await request(path: path, method: "DELETE", query: [], body: data, authenticated: authenticated)
    }

    func uploadMultipart<Response: Decodable>(
        _ path: String,
        formFields: [String: String],
        fileFieldName: String,
        fileName: String,
        fileData: Data,
        mimeType: String,
        authenticated: Bool = true,
        progressHandler: (@MainActor @Sendable (Double) -> Void)? = nil
    ) async throws -> Response {
        let boundary = "Boundary-\(UUID().uuidString)"
        let url = try endpointURL(path: path, query: [])
        let bodyURL = try MultipartFormData.writeBodyFile(
            boundary: boundary,
            fields: formFields,
            fileFieldName: fileFieldName,
            fileName: fileName,
            fileData: fileData,
            mimeType: mimeType
        )
        // Stays on disk until this function returns so a 401 can be retried with the same body.
        defer { try? FileManager.default.removeItem(at: bodyURL) }

        func send(accessToken: String?) async throws -> (Data, HTTPURLResponse) {
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.timeoutInterval = 120
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            request.setValue(TimeZone.current.identifier, forHTTPHeaderField: "X-Spine-Timezone")
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            if let accessToken {
                request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
            }

            let (data, response): (Data, URLResponse)
            do {
                let delegate = progressHandler.map(MultipartUploadProgressDelegate.init(progressHandler:))
                (data, response) = try await multipartSession.upload(for: request, fromFile: bodyURL, delegate: delegate)
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as URLError where error.code == .cancelled {
                throw CancellationError()
            } catch {
                throw APIError.network(error)
            }
            guard let http = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            return (data, http)
        }

        let sentToken = authenticated ? tokenProvider.accessToken : nil
        var (data, http) = try await send(accessToken: sentToken)
        if http.statusCode == 401, authenticated {
            // Same shared refresh and retry rules as `request`, except that an upload never clears the tokens
            // itself: whether the session is over is the caller's call.
            try await refreshSession(afterRejecting: sentToken)
            (data, http) = try await sendAfterRefresh(clearsTokens: false, rejecting: sentToken, send)
        }
        if http.statusCode == 401 {
            throw APIError.unauthorized // an unauthenticated upload has no session to refresh
        }

        guard (200 ... 299).contains(http.statusCode) else {
            let message = String(data: data, encoding: .utf8)
            throw APIError.httpStatus(http.statusCode, message)
        }
        if authenticated {
            NotificationCenter.default.post(name: .authenticatedRequestSucceeded, object: nil)
        }

        do {
            return try JSONDecoder.api.decode(Response.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    private func request<T: Decodable>(
        path: String,
        method: String,
        query: [URLQueryItem],
        body: Data?,
        authenticated: Bool,
        requestTimeout: TimeInterval? = nil
    ) async throws -> T {
        let accessToken = authenticated ? tokenProvider.accessToken : nil
        let request = try makeRequest(
            path: path,
            method: method,
            query: query,
            body: body,
            accessToken: accessToken,
            requestTimeout: requestTimeout
        )

        let data: Data
        let http: HTTPURLResponse
        do {
            (data, http) = try await perform(request)
        } catch APIError.unauthorized where authenticated {
            // Throws `.unauthorized` only when the session is really over; a temporary refresh failure
            // surfaces as its own error and leaves the tokens alone.
            try await refreshSession(afterRejecting: accessToken)
            (data, http) = try await sendAfterRefresh(clearsTokens: true, rejecting: accessToken) { token in
                let retry = try makeRequest(
                    path: path,
                    method: method,
                    query: query,
                    body: body,
                    accessToken: token,
                    requestTimeout: requestTimeout
                )
                return try await perform(retry, mapsUnauthorized: false)
            }
        }

        guard (200 ... 299).contains(http.statusCode) else {
            let message = String(data: data, encoding: .utf8)
            throw APIError.httpStatus(http.statusCode, message)
        }
        if authenticated {
            NotificationCenter.default.post(name: .authenticatedRequestSucceeded, object: nil)
        }

        do {
            if data.isEmpty, T.self == EmptyResponse.self {
                return EmptyResponse() as! T
            }
            return try JSONDecoder.api.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    /// Sends a request again after a refresh, with the stored access token, and settles what a 401 to that retry
    /// means. Reads of the store are checked: a failed lookup is a temporary error, never a signed-out session.
    ///
    /// `rejectedToken` is what the original request was signed with. A replay never goes out under another user's
    /// token: if someone else's session has replaced the one the request belonged to, the request is over.
    private func sendAfterRefresh(
        clearsTokens: Bool,
        rejecting rejectedToken: String?,
        _ send: (String?) async throws -> (Data, HTTPURLResponse)
    ) async throws -> (Data, HTTPURLResponse) {
        var stored = try tokenProvider.loadAccessToken()
        // The stored token changes only when a refresh lands or the user signs in again, so a few attempts are
        // plenty: a chain of ever newer tokens is not a session that can be settled here.
        for _ in 0 ..< Self.maxReplays {
            // A refresh that worked leaves an access token behind. None means the session ended meanwhile (the
            // user signed out), and a request of that session has nothing to be replayed with.
            guard let token = stored else { throw APIError.unauthorized }
            try Self.requireSameUser(rejected: rejectedToken, replayedWith: token)
            let result = try await send(token)
            guard result.1.statusCode == 401 else { return result }

            if let newer = try tokenProvider.loadAccessToken(), newer != token {
                // Newer tokens were stored while this retry was in flight (another refresh landed, or the user
                // signed in again). Go again with them rather than call the session over.
                stored = newer
                continue
            }
            // Still rejected. Only the API's own answer says the session is over; a 401 page from a proxy or a
            // misconfigured server says nothing about the tokens.
            guard Self.apiErrorEnvelope(in: result.0) != nil else {
                throw APIError.httpStatus(401, String(data: result.0, encoding: .utf8))
            }
            if clearsTokens {
                // Compare-and-clear: tokens stored since this retry was signed must survive.
                guard try await refresher.clear(ifAccessToken: token, tokenStore: tokenProvider) else {
                    // They landed after the check above, so they get their turn, unless the session was ended
                    // altogether and there is nothing left to retry with.
                    guard let newer = try tokenProvider.loadAccessToken() else { throw APIError.unauthorized }
                    stored = newer
                    continue
                }
            }
            throw APIError.unauthorized
        }
        throw APIError.httpStatus(401, nil)
    }

    private static let maxReplays = 4

    /// Stops a replay that would cross users: the request was signed for one user and the store now holds
    /// another's token, which means the session it belonged to ended and someone signed in since. Cancelled, like
    /// any work whose screen has gone. Tokens that aren't JWTs, or carry no user, can't be told apart and pass.
    private static func requireSameUser(rejected: String?, replayedWith token: String?) throws {
        guard let before = rejected.flatMap(JWTExpiry.userID(of:)),
              let after = token.flatMap(JWTExpiry.userID(of:)),
              before != after else { return }
        throw CancellationError()
    }

    private func makeRequest(
        path: String,
        method: String,
        query: [URLQueryItem],
        body: Data?,
        accessToken: String?,
        requestTimeout: TimeInterval? = nil
    ) throws -> URLRequest {
        let url = try endpointURL(path: path, query: query)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = requestTimeout ?? 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(TimeZone.current.identifier, forHTTPHeaderField: "X-Spine-Timezone")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let accessToken {
            request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    /// `mapsUnauthorized: false` hands a 401 back like any other response, for the one caller that has to read
    /// its body (the refresh exchange). Every normal request keeps mapping it to `APIError.unauthorized`.
    private func perform(_ request: URLRequest, mapsUnauthorized: Bool = true) async throws -> (Data, HTTPURLResponse) {
        let first = try await performOnce(request, mapsUnauthorized: mapsUnauthorized)
        guard first.1.statusCode == 429,
              let rawDelay = first.1.value(forHTTPHeaderField: "Retry-After"),
              let delay = TimeInterval(rawDelay),
              (0 ... 10).contains(delay) else {
            return first
        }

        // ponytail: one bounded retry prevents retry storms; add a shared gate if scoped limits need more coordination.
        try await Task.sleep(for: .seconds(delay))
        return try await performOnce(request, mapsUnauthorized: mapsUnauthorized)
    }

    private func performOnce(_ request: URLRequest, mapsUnauthorized: Bool) async throws -> (Data, HTTPURLResponse) {
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw APIError.network(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        if mapsUnauthorized, http.statusCode == 401 {
            throw APIError.unauthorized
        }
        return (data, http)
    }

    /// Refreshes the stored session through the refresher shared by every `APIClient`.
    ///
    /// Throws `APIError.unauthorized` only when the session is really over: there is no refresh token, or
    /// the server itself rejected it (the tokens are cleared). Any other error is temporary and leaves both
    /// tokens untouched, so callers must not treat it as a sign-out.
    func refreshSession() async throws {
        try await refresher.refresh(tokenStore: tokenProvider, exchange: refreshExchange, revokeDropped: revokeDropped)
    }

    /// Same, for a request that was signed with `rejectedAccessToken` and got a 401.
    private func refreshSession(afterRejecting rejectedAccessToken: String?) async throws {
        try await refresher.refresh(
            afterRejecting: rejectedAccessToken,
            tokenStore: tokenProvider,
            exchange: refreshExchange,
            revokeDropped: revokeDropped
        )
    }

    private var refreshExchange: TokenRefresher.Exchange {
        { refreshToken in try await self.exchangeRefreshToken(refreshToken) }
    }

    private var revokeDropped: TokenRefresher.Revoke {
        { refreshToken in
            _ = try? await BackgroundTask.run("spine.token-revoke") { await self.revokeRefreshToken(refreshToken) }
        }
    }

    /// Asks the server to revoke `refreshToken` (POST /auth/logout/), best effort.
    ///
    /// Unauthenticated on purpose: the refresh token in the body is all the endpoint needs, and by the time
    /// anyone signs out the access token has often expired. A server that still wants one answers 401, which is
    /// no reason to refresh: nothing here retries, refreshes or clears anything, and every error is ignored.
    func revokeRefreshToken(_ refreshToken: String) async {
        let _: EmptyResponse? = try? await post("/auth/logout/", body: LogoutRequest(refresh: refreshToken))
    }

    /// POST /auth/refresh/ (unauthenticated). Only the server's own answer about the token ends the session (see
    /// `isRefreshTokenRejection`). Every other outcome is temporary and thrown as its own error: a network
    /// error, 403, 429, 5xx, an undecodable body, and any 401 or 400 that isn't the API's JSON envelope (a
    /// proxy's login page, Django's DisallowedHost page, an nginx or Cloudflare error).
    private func exchangeRefreshToken(_ refreshToken: String) async throws -> AuthRefreshResponse {
        do {
            return try await sendRefreshRequest(refreshToken)
        } catch let error where Self.mayHaveRotated(error) {
            // The request may have reached the server and rotated the token, with only the response lost. The
            // server accepts a just-rotated token again for a minute and answers with a fresh pair, so asking
            // once more with the same token recovers what would otherwise strand the session.
            return try await sendRefreshRequest(refreshToken)
        }
    }

    private func sendRefreshRequest(_ refreshToken: String) async throws -> AuthRefreshResponse {
        let body = try JSONEncoder.api.encode(RefreshRequest(refresh: refreshToken))
        let request = try makeRequest(
            path: "/auth/refresh/",
            method: "POST",
            query: [],
            body: body,
            accessToken: nil
        )
        let (data, http) = try await perform(request, mapsUnauthorized: false)
        if Self.isRefreshTokenRejection(status: http.statusCode, body: data) {
            // Why, for the log: the API's short error code, never anything from the token.
            let code = (Self.apiErrorEnvelope(in: data)?["code"] as? String).map { String($0.prefix(64)) } ?? "none"
            SessionLog.note("The server rejected the refresh token: HTTP \(http.statusCode), code \(code)")
            throw APIError.unauthorized
        }
        guard (200 ... 299).contains(http.statusCode) else {
            throw APIError.httpStatus(http.statusCode, String(data: data, encoding: .utf8))
        }
        do {
            return try JSONDecoder.api.decode(AuthRefreshResponse.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    /// True only for the API's own JSON error envelope, `{"error": {...}}`, which the token check produces:
    /// on a 401 (`token_not_valid`, `authentication_failed`), or on a 400 whose `error.fields` names `refresh`
    /// (a missing, blank or oversized token). The status alone proves nothing: a misconfigured server or a
    /// proxy answers 400 or 401 with an HTML page for every user.
    private static func isRefreshTokenRejection(status: Int, body: Data) -> Bool {
        guard status == 401 || status == 400, let error = apiErrorEnvelope(in: body) else {
            return false
        }
        return status == 401 || (error["fields"] as? [String: Any])?["refresh"] != nil
    }

    /// The `error` object of the API's JSON error envelope, `{"error": {...}}`; nil for anything else (an HTML
    /// page, an empty body, JSON of another shape).
    private static func apiErrorEnvelope(in body: Data) -> [String: Any]? {
        guard let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else { return nil }
        return json["error"] as? [String: Any]
    }

    /// Outcomes of a refresh request after which the server may still have rotated the token: the transport
    /// lost or garbled the answer (`mayHaveReachedServer`), or a gateway in front of the API gave up on it
    /// (502 and 504, and Cloudflare's 520 to 524) while the origin may well have committed the rotation.
    private static func mayHaveRotated(_ error: Error) -> Bool {
        switch error {
        case APIError.network(let underlying):
            mayHaveReachedServer(underlying)
        case APIError.httpStatus(let status, _):
            [502, 504, 520, 521, 522, 523, 524].contains(status)
        default:
            false
        }
    }

    /// Transport errors after which the request may still have reached the server: no answer came back
    /// (timeout, dropped connection) or one came back that couldn't be used. Errors that mean it never left
    /// the device or never connected (offline, can't connect, DNS, TLS setup) are not ambiguous.
    private static func mayHaveReachedServer(_ error: Error) -> Bool {
        switch (error as? URLError)?.code {
        case .timedOut, .networkConnectionLost, .badServerResponse, .cannotParseResponse, .zeroByteResource,
             .cannotDecodeRawData, .cannotDecodeContentData:
            true
        default:
            false
        }
    }

    private func endpointURL(path: String, query: [URLQueryItem]) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }

        let basePath = components.path.trimmedPathSlashes
        let apiPrefix = AppConfig.apiPrefix.trimmedPathSlashes
        let requestPath = path.trimmedPathSlashes
        var resolvedPath = "/" + [basePath, apiPrefix, requestPath]
            .filter { !$0.isEmpty }
            .joined(separator: "/")
        if path.hasSuffix("/"), !resolvedPath.hasSuffix("/") {
            resolvedPath += "/"
        }
        components.path = resolvedPath

        if !query.isEmpty {
            components.queryItems = query
        }
        guard let url = components.url else {
            throw APIError.invalidURL
        }
        return url
    }
}

private extension String {
    var trimmedPathSlashes: String {
        trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}

enum MultipartFormData {
    static func body(
        boundary: String,
        fields: [String: String],
        fileFieldName: String,
        fileName: String,
        fileData: Data,
        mimeType: String
    ) -> Data {
        var data = Data()
        let lineBreak = "\r\n"

        for (name, value) in fields.sorted(by: { $0.key < $1.key }) {
            data.appendString("--\(boundary)\(lineBreak)")
            data.appendString("Content-Disposition: form-data; name=\"\(name.multipartEscaped)\"\(lineBreak)\(lineBreak)")
            data.appendString("\(value)\(lineBreak)")
        }

        data.appendString("--\(boundary)\(lineBreak)")
        data.appendString("Content-Disposition: form-data; name=\"\(fileFieldName.multipartEscaped)\"; filename=\"\(fileName.multipartEscaped)\"\(lineBreak)")
        data.appendString("Content-Type: \(mimeType)\(lineBreak)\(lineBreak)")
        data.append(fileData)
        data.appendString(lineBreak)
        data.appendString("--\(boundary)--\(lineBreak)")
        return data
    }

    static func writeBodyFile(
        boundary: String,
        fields: [String: String],
        fileFieldName: String,
        fileName: String,
        fileData: Data,
        mimeType: String
    ) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("spine-multipart-\(UUID().uuidString)")
            .appendingPathExtension("body")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }

        let lineBreak = "\r\n"
        for (name, value) in fields.sorted(by: { $0.key < $1.key }) {
            handle.write(Data("--\(boundary)\(lineBreak)".utf8))
            handle.write(Data("Content-Disposition: form-data; name=\"\(name.multipartEscaped)\"\(lineBreak)\(lineBreak)".utf8))
            handle.write(Data("\(value)\(lineBreak)".utf8))
        }

        handle.write(Data("--\(boundary)\(lineBreak)".utf8))
        handle.write(Data("Content-Disposition: form-data; name=\"\(fileFieldName.multipartEscaped)\"; filename=\"\(fileName.multipartEscaped)\"\(lineBreak)".utf8))
        handle.write(Data("Content-Type: \(mimeType)\(lineBreak)\(lineBreak)".utf8))
        handle.write(fileData)
        handle.write(Data(lineBreak.utf8))
        handle.write(Data("--\(boundary)--\(lineBreak)".utf8))
        return url
    }
}

private final class MultipartUploadProgressDelegate: NSObject, URLSessionTaskDelegate {
    let progressHandler: @MainActor @Sendable (Double) -> Void

    init(progressHandler: @escaping @MainActor @Sendable (Double) -> Void) {
        self.progressHandler = progressHandler
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didSendBodyData bytesSent: Int64,
        totalBytesSent: Int64,
        totalBytesExpectedToSend: Int64
    ) {
        guard totalBytesExpectedToSend > 0 else { return }
        let progress = min(1, max(0, Double(totalBytesSent) / Double(totalBytesExpectedToSend)))
        Task { @MainActor [progressHandler] in
            progressHandler(progress)
        }
    }
}

private extension String {
    var multipartEscaped: String {
        replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\r", with: "")
            .replacingOccurrences(of: "\n", with: "")
    }
}

private extension Data {
    mutating func appendString(_ string: String) {
        append(Data(string.utf8))
    }
}

extension JSONDecoder {
    static let api: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()
}

extension JSONEncoder {
    static let api: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()
}
