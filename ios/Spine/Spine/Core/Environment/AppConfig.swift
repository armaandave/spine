import Foundation

/// Central configuration for API connectivity.
enum AppConfig {
    static let apiPrefix = "/api/v1"

    /// Permanent API host (Cloudflare named tunnel). Set once; never use trycloudflare.com.
    /// Example after setup: https://api.yourdomain.com
    static let productionAPIBaseURL = "https://api.spine-api.com"

    /// Override in the Xcode scheme with SPINE_API_BASE_URL when local development needs localhost.
    static var apiBaseURL: URL {
        resolvedAPIBaseURL(
            environment: ProcessInfo.processInfo.environment,
            infoDictionary: Bundle.main.infoDictionary
        )
    }

    /// The launch environment's SPINE_API_BASE_URL wins (Simulator launches). Debug builds then use the
    /// SpineAPIBaseURL Info.plist key, which a local phone build points at this Mac's backend
    /// (`scripts/ios/build-to-iphone.sh --local`). Otherwise, and always in Release builds, production.
    static func resolvedAPIBaseURL(environment: [String: String], infoDictionary: [String: Any]?) -> URL {
        if let url = usableURL(environment["SPINE_API_BASE_URL"]) {
            return url
        }

        #if DEBUG
        if let url = usableURL(infoDictionary?["SpineAPIBaseURL"] as? String) {
            return url
        }
        #endif

        guard productionAPIBaseURL != "https://REPLACE-WITH-YOUR-HOSTNAME",
              let url = URL(string: productionAPIBaseURL) else {
            fatalError("Set AppConfig.productionAPIBaseURL to your permanent Cloudflare hostname.")
        }
        return url
    }

    private static func usableURL(_ value: String?) -> URL? {
        guard let value, !value.isEmpty else { return nil }
        return URL(string: value)
    }
}
