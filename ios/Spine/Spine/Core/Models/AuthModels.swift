import Foundation

struct AuthUser: Codable, Identifiable {
    let id: Int
    let username: String
    let displayName: String
    let isPrivate: Bool
    let avatarUrl: String?
    /// The media types the user has on, remembered with the user so type pickers open on
    /// them. Nil until a profile has loaded.
    var enabledMediaTypes: [String]? = nil
}

struct AuthTokenResponse: Decodable {
    let access: String
    let refresh: String
    let user: AuthUser
}

struct AuthRefreshResponse: Decodable {
    let access: String
    let refresh: String?
}

struct LoginRequest: Encodable {
    let usernameOrEmail: String
    let password: String
}

struct RegisterRequest: Encodable {
    let username: String
    let email: String
    let password: String
    let passwordConfirm: String
}

#if DEBUG
/// The dev sign-in body is empty: a local backend signs in its one dev user.
struct DevSignInRequest: Encodable {}
#endif

struct RefreshRequest: Encodable {
    let refresh: String
}

struct LogoutRequest: Encodable {
    let refresh: String
}
