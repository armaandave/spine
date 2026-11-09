#if DEBUG
import Combine
import Network
import SwiftUI
import WebKit

/// Throwaway test, Debug builds only: can this phone read a book's Goodreads rating?
/// It tries a plain request, then a hidden browser view (which runs Goodreads' bot check script).
final class GoodreadsProbe: NSObject, ObservableObject, WKNavigationDelegate {
    @Published var lines: [String] = []
    @Published var isRunning = false
    let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
    private var statuses: [Int] = []
    private var network = "unknown"
    private var blocksHeavyContent = false
    private let pathMonitor = NWPathMonitor()

    private static let ratingScript = """
    (() => {
      for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
        try { const r = JSON.parse(s.textContent).aggregateRating; if (r) return JSON.stringify(r); } catch (e) {}
      }
      return null;
    })()
    """

    override init() {
        super.init()
        webView.navigationDelegate = self
        pathMonitor.pathUpdateHandler = { [weak self] path in
            let name = path.usesInterfaceType(.wifi) ? "Wi-Fi"
                : path.usesInterfaceType(.cellular) ? "cellular" : "other"
            Task { @MainActor in self?.network = name }
        }
        pathMonitor.start(queue: .main)
    }

    func run(goodreadsID: String) async {
        let id = goodreadsID.filter(\.isNumber)
        guard let url = URL(string: "https://www.goodreads.com/book/show/\(id)"), !id.isEmpty else { return }
        isRunning = true
        lines = ["Network: \(network)", "Book: \(id)"]
        await plainRequest(url)
        await browserView(url)
        isRunning = false
    }

    private func plainRequest(_ url: URL) async {
        let start = Date()
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let rating = Self.rating(inHTML: String(decoding: data, as: UTF8.self)) ?? "none"
            lines.append("Plain: HTTP \(status), \(data.count / 1024) KB, \(Self.elapsed(since: start)), rating \(rating)")
        } catch {
            lines.append("Plain: failed, \(error.localizedDescription)")
        }
    }

    private func browserView(_ url: URL) async {
        statuses = []
        await blockHeavyContentOnce()
        let start = Date()
        webView.load(URLRequest(url: url))
        while Date().timeIntervalSince(start) < 20 {
            try? await Task.sleep(for: .milliseconds(250))
            if let rating = try? await webView.evaluateJavaScript(Self.ratingScript) as? String {
                webView.stopLoading()
                lines.append("Browser: \(Self.elapsed(since: start)), responses \(statusText), rating \(rating)")
                return
            }
        }
        webView.stopLoading()
        lines.append("Browser: no rating after 20 s, responses \(statusText), title \(webView.title ?? "none")")
    }

    /// Skips images, video, fonts and stylesheets: only the page and its scripts load.
    private func blockHeavyContentOnce() async {
        guard !blocksHeavyContent else { return }
        let rules = """
        [{"trigger":{"url-filter":".*","resource-type":["image","media","font","style-sheet"]},"action":{"type":"block"}}]
        """
        if let list = try? await WKContentRuleListStore.default()
            .compileContentRuleList(forIdentifier: "GoodreadsProbeBlockHeavy", encodedContentRuleList: rules) {
            webView.configuration.userContentController.add(list)
            blocksHeavyContent = true
        }
    }

    private var statusText: String {
        statuses.isEmpty ? "none" : statuses.map(String.init).joined(separator: " → ")
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        if navigationResponse.isForMainFrame, let response = navigationResponse.response as? HTTPURLResponse {
            statuses.append(response.statusCode)
        }
        return .allow
    }

    private static func rating(inHTML html: String) -> String? {
        guard let range = html.range(of: #""aggregateRating":\{[^}]*\}"#, options: .regularExpression) else {
            return nil
        }
        return String(html[range])
    }

    private static func elapsed(since start: Date) -> String {
        String(format: "%.2f s", Date().timeIntervalSince(start))
    }
}

struct GoodreadsProbeView: View {
    @StateObject private var probe = GoodreadsProbe()
    @State private var goodreadsID = "54493401"

    var body: some View {
        Form {
            Section {
                TextField("Goodreads book ID", text: $goodreadsID)
                    .keyboardType(.numberPad)
                Button(probe.isRunning ? "Running…" : "Run test") {
                    Task { await probe.run(goodreadsID: goodreadsID) }
                }
                .disabled(probe.isRunning)
            }
            if !probe.lines.isEmpty {
                Section("Results") {
                    ForEach(Array(probe.lines.enumerated()), id: \.offset) { _, line in
                        Text(line)
                            .font(.footnote.monospaced())
                            .textSelection(.enabled)
                    }
                    Button("Copy results") {
                        UIPasteboard.general.string = probe.lines.joined(separator: "\n")
                    }
                }
            }
        }
        .background(GoodreadsProbeWebViewHost(webView: probe.webView).frame(width: 1, height: 1).opacity(0.01))
        .navigationTitle("Goodreads test")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct GoodreadsProbeWebViewHost: UIViewRepresentable {
    let webView: WKWebView

    func makeUIView(context: Context) -> WKWebView { webView }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
#endif
