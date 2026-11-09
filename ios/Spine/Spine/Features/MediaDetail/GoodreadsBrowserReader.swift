import UIKit
import WebKit

/// Reads a Goodreads page in a hidden browser view. Goodreads sometimes answers a plain request
/// with its bot check, a script a browser runs before it shows the page; the browser view runs it
/// like Safari would. The pass it earns is then shared with plain requests, so the next pages
/// load without the browser.
final class GoodreadsBrowserReader {
    static let shared = GoodreadsBrowserReader()

    private static let ratingScript = """
    (() => {
      for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
        try { const r = JSON.parse(s.textContent).aggregateRating; if (r) return JSON.stringify(r); } catch (e) {}
      }
      return null;
    })()
    """
    /// Only the page and its scripts load: no images, video, fonts or stylesheets.
    private static let blockHeavyContent = """
    [{"trigger":{"url-filter":".*","resource-type":["image","media","font","style-sheet"]},"action":{"type":"block"}}]
    """

    private var isReading = false

    func rating(_ page: URL) async -> (value: Double, count: Int?)? {
        // One page at a time; a second book opened meanwhile goes without.
        guard !isReading, let window = Self.keyWindow() else { return nil }
        isReading = true
        let webView = await Self.makeWebView()
        webView.frame = CGRect(x: 0, y: 0, width: 1, height: 1)
        webView.alpha = 0.01
        webView.isUserInteractionEnabled = false
        window.addSubview(webView)
        defer {
            webView.stopLoading()
            webView.removeFromSuperview()
            isReading = false
        }

        webView.load(URLRequest(url: page))
        let deadline = ContinuousClock.now + .seconds(15)
        while ContinuousClock.now < deadline, !Task.isCancelled {
            try? await Task.sleep(for: .milliseconds(300))
            if let json = try? await webView.evaluateJavaScript(Self.ratingScript) as? String,
               let found = GoodreadsRating.aggregateRating(in: Data(#""aggregateRating":\#(json)"#.utf8)) {
                await Self.sharePass(from: webView)
                return found
            }
        }
        return nil
    }

    private static func makeWebView() async -> WKWebView {
        let configuration = WKWebViewConfiguration()
        if let rules = try? await WKContentRuleListStore.default()
            .compileContentRuleList(forIdentifier: "GoodreadsBlockHeavyContent", encodedContentRuleList: blockHeavyContent) {
            configuration.userContentController.add(rules)
        }
        return WKWebView(frame: .zero, configuration: configuration)
    }

    /// Copies Goodreads' cookies (its bot check pass among them) to plain requests.
    private static func sharePass(from webView: WKWebView) async {
        let cookies = await webView.configuration.websiteDataStore.httpCookieStore.allCookies()
        for cookie in cookies where cookie.domain.hasSuffix("goodreads.com") {
            HTTPCookieStorage.shared.setCookie(cookie)
        }
    }

    private static func keyWindow() -> UIWindow? {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
    }
}
