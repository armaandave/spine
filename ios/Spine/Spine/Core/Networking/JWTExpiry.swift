import Foundation

/// Reads claims of a JWT without verifying anything. They only feed local decisions, such as "is a refresh due?"
/// or "does this token belong to the user the app remembers?"; the server stays the authority on whether a token
/// is valid.
nonisolated enum JWTExpiry {
    /// The moment the token expires, or nil if `token` isn't a JWT with a numeric `exp`.
    static func expiration(of token: String) -> Date? {
        guard let exp = (claims(of: token)?["exp"] as? NSNumber)?.doubleValue else { return nil }
        return Date(timeIntervalSince1970: exp)
    }

    /// The `user_id` claim SimpleJWT puts on access and refresh tokens alike, or nil if `token` isn't a JWT with an
    /// integer one. A string of digits counts, in case a serializer quotes it.
    static func userID(of token: String) -> Int? {
        switch claims(of: token)?["user_id"] {
        case let number as NSNumber:
            return Int(exactly: number.doubleValue)
        case let string as String:
            return Int(string)
        default:
            return nil
        }
    }

    private static func claims(of token: String) -> [String: Any]? {
        let segments = token.split(separator: ".", omittingEmptySubsequences: false)
        guard segments.count == 3, let payload = base64URLDecoded(String(segments[1])) else { return nil }
        return (try? JSONSerialization.jsonObject(with: payload)) as? [String: Any]
    }

    private static func base64URLDecoded(_ string: String) -> Data? {
        var base64 = string
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        return Data(base64Encoded: base64)
    }
}
