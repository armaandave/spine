import Foundation

protocol SocialRepository {
    func searchUsers(query: String) async throws -> [SocialUser]
    func follow(username: String) async throws -> FollowState
    func unfollow(username: String) async throws
    /// One page of a user's followers, or of the people they follow, newest first.
    func followList(username: String, followers: Bool, page: String?) async throws -> PagedResponse<SocialUser>
    func followRequests() async throws -> [FollowRequest]
    func answerFollowRequest(id: Int, accept: Bool) async throws
    func removeFollower(username: String) async throws
    func block(username: String) async throws
    func unblock(username: String) async throws
    func report(username: String) async throws
    func registerPushDevice(_ registration: PushDeviceRegistration) async throws
    func unregisterPushDevice(token: String) async throws
}

struct APISocialRepository: SocialRepository {
    let client: APIClient

    func searchUsers(query: String) async throws -> [SocialUser] {
        let response: UserSearchResponse = try await client.get(
            "/users/search/",
            query: [URLQueryItem(name: "q", value: query)],
            authenticated: true
        )
        return response.results
    }

    func follow(username: String) async throws -> FollowState {
        let response: FollowStateResponse = try await client.post(
            "\(Self.userPath(username))follow/",
            body: EmptyResponse(),
            authenticated: true
        )
        return FollowState(apiState: response.state)
    }

    func unfollow(username: String) async throws {
        let _: FollowStateResponse = try await client.delete("\(Self.userPath(username))follow/", authenticated: true)
    }

    func followList(username: String, followers: Bool, page: String?) async throws -> PagedResponse<SocialUser> {
        var query: [URLQueryItem] = []
        if let page = Self.pageValue(from: page) {
            query.append(URLQueryItem(name: "page", value: page))
        }
        return try await client.get(
            "\(Self.userPath(username))\(followers ? "followers" : "following")/",
            query: query,
            authenticated: true
        )
    }

    func followRequests() async throws -> [FollowRequest] {
        let response: FollowRequestsResponse = try await client.get("/follow-requests/", authenticated: true)
        return response.results
    }

    func answerFollowRequest(id: Int, accept: Bool) async throws {
        let _: FollowStateResponse = try await client.post(
            "/follow-requests/\(id)/\(accept ? "accept" : "reject")/",
            body: EmptyResponse(),
            authenticated: true
        )
    }

    func removeFollower(username: String) async throws {
        let _: EmptyResponse = try await client.delete("/me/followers/\(Self.escaped(username))/", authenticated: true)
    }

    func block(username: String) async throws {
        let _: EmptyResponse = try await client.post(
            "\(Self.userPath(username))block/",
            body: EmptyResponse(),
            authenticated: true
        )
    }

    func unblock(username: String) async throws {
        let _: EmptyResponse = try await client.delete("\(Self.userPath(username))block/", authenticated: true)
    }

    func report(username: String) async throws {
        let _: EmptyResponse = try await client.post(
            "\(Self.userPath(username))report/",
            body: EmptyResponse(),
            authenticated: true
        )
    }

    func registerPushDevice(_ registration: PushDeviceRegistration) async throws {
        let _: EmptyResponse = try await client.post("/me/push-devices/", body: registration, authenticated: true)
    }

    func unregisterPushDevice(token: String) async throws {
        let _: EmptyResponse = try await client.delete("/me/push-devices/\(token)/", authenticated: true)
    }

    private static func escaped(_ username: String) -> String {
        username.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? username
    }

    private static func userPath(_ username: String) -> String {
        "/users/\(escaped(username))/"
    }

    /// A page-number `next` link from the API, reduced to its page number.
    private static func pageValue(from link: String?) -> String? {
        guard let link, !link.isEmpty else { return nil }
        guard let components = URLComponents(string: link) else { return link }
        return components.queryItems?.first(where: { $0.name == "page" })?.value ?? link
    }
}
