import SwiftUI
import WebKit

/// Letterboxd's own sign-in page in a sheet. Once they're in, the app requests the export and hands over the file.
struct LetterboxdSignInSheet: View {
    enum Phase {
        case loading
        case signingIn
        case gettingExport
    }

    let onExport: (URL) -> Void
    let onFailure: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var phase: Phase = .loading
    @State private var isClosing = false

    var body: some View {
        NavigationStack {
            LetterboxdExportWebView(phase: $phase, isClosing: isClosing, onExport: onExport, onFailure: onFailure)
                .ignoresSafeArea(edges: .bottom)
                .overlay {
                    switch phase {
                    case .loading:
                        ProgressView()
                    case .signingIn:
                        EmptyView()
                    case .gettingExport:
                        gettingExport
                    }
                }
                .navigationTitle("Letterboxd")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(role: .close) {
                            isClosing = true
                            dismiss()
                        } label: {
                            Label("Close", systemImage: "xmark")
                        }
                    }
                }
        }
        // Closing is a choice to cancel, so it takes the button while the file is on its way.
        .interactiveDismissDisabled(phase == .gettingExport)
    }

    private var gettingExport: some View {
        ZStack {
            SpinePageBackground()

            VStack(spacing: 14) {
                ProgressView()
                Text("Getting your data...")
                    .font(.headline)
                    .foregroundStyle(.white)
            }
        }
    }
}

private struct LetterboxdExportWebView: UIViewRepresentable {
    @Binding var phase: LetterboxdSignInSheet.Phase
    let isClosing: Bool
    let onExport: (URL) -> Void
    let onFailure: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // The sign-in ends with this sheet: no Letterboxd cookies stay on the phone.
        configuration.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.isOpaque = false
        webView.load(URLRequest(url: LetterboxdExport.pageURL))
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        context.coordinator.parent = self
        if isClosing {
            context.coordinator.stop() // before the dismissal ends, so a finishing download can't start an import
        }
    }

    static func dismantleUIView(_ uiView: WKWebView, coordinator: Coordinator) {
        coordinator.stop()
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKDownloadDelegate {
        var parent: LetterboxdExportWebView
        private var hasRequestedExport = false
        private var isFinished = false
        private var activeDownload: WKDownload?

        init(parent: LetterboxdExportWebView) {
            self.parent = parent
        }

        /// The sheet is gone: a download still running belongs to nobody, and must not start an import.
        func stop() {
            isFinished = true
            activeDownload?.cancel { _ in
                LetterboxdExport.removeFile()
            }
            activeDownload = nil
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
            guard navigationAction.targetFrame?.isMainFrame == true,
                  !LetterboxdExport.isLetterboxd(navigationAction.request.url)
            else { return .allow }
            // With the page hidden behind "Getting your data...", nothing else would ever end the wait.
            if parent.phase == .gettingExport {
                fail()
            }
            return .cancel
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse) async -> WKNavigationResponsePolicy {
            let response = navigationResponse.response as? HTTPURLResponse
            guard navigationResponse.isForMainFrame,
                  LetterboxdExport.isExport(
                      mimeType: response?.mimeType,
                      contentDisposition: response?.value(forHTTPHeaderField: "Content-Disposition")
                  )
            else { return .allow }
            return .download
        }

        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
            download.delegate = self
            activeDownload = download
            parent.phase = .gettingExport
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            webView.evaluateJavaScript(LetterboxdExport.signedInScript) { [weak self] result, _ in
                self?.pageDidLoad(in: webView, isSignedIn: result as? Bool == true)
            }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            pageDidFail(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            pageDidFail(error)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            fail()
        }

        private func pageDidFail(_ error: Error) {
            let error = error as NSError
            // A newer page or the switch to a download interrupts the page that was loading (WebKit's error 102).
            let isCancellation = error.domain == NSURLErrorDomain && error.code == NSURLErrorCancelled
            let isInterruption = error.domain == "WebKitErrorDomain" && error.code == 102
            guard !isCancellation, !isInterruption else { return }
            fail()
        }

        private func pageDidLoad(in webView: WKWebView, isSignedIn: Bool) {
            guard !isFinished, activeDownload == nil else { return }
            switch LetterboxdExport.nextStep(isSignedIn: isSignedIn, hasRequestedExport: hasRequestedExport) {
            case .wait:
                parent.phase = .signingIn
            case .requestExport:
                hasRequestedExport = true
                parent.phase = .gettingExport
                webView.load(URLRequest(url: LetterboxdExport.pageURL, timeoutInterval: 300))
            case .fail:
                fail()
            }
        }

        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String) async -> URL? {
            LetterboxdExport.removeFile()
            return LetterboxdExport.fileURL
        }

        func downloadDidFinish(_ download: WKDownload) {
            activeDownload = nil
            guard !isFinished else { return }
            isFinished = true
            parent.onExport(LetterboxdExport.fileURL)
        }

        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            activeDownload = nil
            LetterboxdExport.removeFile()
            fail()
        }

        private func fail() {
            guard !isFinished else { return }
            isFinished = true
            parent.onFailure()
        }
    }
}
