import Foundation

/// A person in search results or a followers list, with how the viewer is connected to them.
struct SocialUser: Codable, Identifiable, Hashable {
    let id: Int
    let username: String
    let displayName: String
    let avatarUrl: String?
    let isPrivate: Bool
    var viewerRelationship: SocialRelationship?

    var summary: UserSummary {
        UserSummary(id: id, username: username, displayName: displayName, avatarUrl: avatarUrl)
    }

    var followState: FollowState {
        FollowState(relationship: viewerRelationship)
    }
}

struct SocialRelationship: Codable, Hashable {
    var following: Bool
    var requested: Bool
    var followedBy: Bool
}

struct FollowRequest: Codable, Identifiable, Hashable {
    let id: Int
    let fromUser: UserSummary
}

struct FollowRequestsResponse: Decodable {
    let results: [FollowRequest]
}

struct UserSearchResponse: Decodable {
    let results: [SocialUser]
}

struct FollowStateResponse: Decodable {
    let state: String
}

/// What the Follow button on someone's profile or row says.
enum FollowState: Equatable {
    case none
    case requested
    case following

    init(relationship: SocialRelationship?) {
        if relationship?.following == true {
            self = .following
        } else if relationship?.requested == true {
            self = .requested
        } else {
            self = .none
        }
    }

    init(relationship: ViewerRelationship) {
        self.init(relationship: SocialRelationship(
            following: relationship.following,
            requested: relationship.requested,
            followedBy: relationship.followedBy
        ))
    }

    init(apiState: String) {
        switch apiState {
        case "following": self = .following
        case "requested": self = .requested
        default: self = .none
        }
    }

    func title(followedBy: Bool) -> String {
        switch self {
        case .none: followedBy ? "Follow Back" : "Follow"
        case .requested: "Requested"
        case .following: "Following"
        }
    }
}

/// Where tapping a push opens, from the `route` the backend sends with it.
enum PushRoute: Equatable {
    case profile(username: String)
    case followRequests

    /// `nonisolated` so the notification delegate can read a tapped push off the main actor.
    nonisolated init?(userInfo: [AnyHashable: Any]) {
        guard let route = userInfo["route"] as? [String: Any], let kind = route["kind"] as? String else { return nil }
        switch kind {
        case "profile":
            guard let username = route["username"] as? String, !username.isEmpty else { return nil }
            self = .profile(username: username)
        case "follow_requests":
            self = .followRequests
        default:
            return nil
        }
    }
}

extension Notification.Name {
    /// Who the signed-in user follows changed (a follow, unfollow or block), so Home's activity is out of date.
    static let followsDidChange = Notification.Name("followsDidChange")
}

struct PushDeviceRegistration: Encodable {
    let token: String
    let bundleId: String
    let environment: String
}
