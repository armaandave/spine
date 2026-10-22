import Foundation

/// Central configuration for API connectivity.
enum AppConfig {
    static let apiPrefix = "/api/v1"

    /// Permanent API host (Cloudflare named tunnel). Set once; never use trycloudflare.com.
    /// Example after setup: https://api.yourdomain.com
    static let productionAPIBaseURL = "https://api.spine-api.com"

    /// Where Debug builds go when nothing else is set: a dev server on this Mac (`./start-dev.sh`),
    /// which the Simulator reaches as 127.0.0.1. Never port 8000, which is production on the server Mac.
    static let localAPIBaseURL = "http://127.0.0.1:8001"

    /// Override in the Xcode scheme with SPINE_API_BASE_URL when local development needs localhost.
    static var apiBaseURL: URL {
        resolvedAPIBaseURL(
            environment: ProcessInfo.processInfo.environment,
            infoDictionary: Bundle.main.infoDictionary
        )
    }

    /// Release builds use the launch environment's SPINE_API_BASE_URL, else production.
    /// Debug builds (the "Spine Dev" app) never talk to production: they use the launch environment's
    /// SPINE_API_BASE_URL (Simulator launches), then the SpineAPIBaseURL Info.plist key, which a phone
    /// build points at this Mac's backend (`scripts/ios/build-to-iphone.sh`), then `localAPIBaseURL`.
    /// A value naming the production host is ignored.
    static func resolvedAPIBaseURL(environment: [String: String], infoDictionary: [String: Any]?) -> URL {
        #if DEBUG
        let production = URL(string: productionAPIBaseURL)
        let candidates = [environment["SPINE_API_BASE_URL"], infoDictionary?["SpineAPIBaseURL"] as? String]
        for case let url? in candidates.map(usableURL) where url.host != production?.host {
            return url
        }
        return URL(string: localAPIBaseURL)!
        #else
        if let url = usableURL(environment["SPINE_API_BASE_URL"]) {
            return url
        }

        guard productionAPIBaseURL != "https://REPLACE-WITH-YOUR-HOSTNAME",
              let url = URL(string: productionAPIBaseURL) else {
            fatalError("Set AppConfig.productionAPIBaseURL to your permanent Cloudflare hostname.")
        }
        return url
        #endif
    }

    private static func usableURL(_ value: String?) -> URL? {
        guard let value, !value.isEmpty else { return nil }
        return URL(string: value)
    }
}
