import Foundation

/// Getting someone's export straight from Letterboxd once they've signed in.
enum LetterboxdExport {
    /// The link behind Letterboxd's Export Data button. Signed out, it answers with the sign-in page.
    static let pageURL = URL(string: "https://letterboxd.com/data/export/")!

    /// A fixed path, so the phone only ever holds one copy of someone's export.
    static let fileURL = URL.temporaryDirectory.appending(path: "letterboxd-export.zip")

    /// Letterboxd describes the visitor to its own scripts on every page.
    static let signedInScript = "window.person?.loggedIn === true"

    enum NextStep: Equatable {
        case wait
        case requestExport
        case fail
    }

    /// What to do once a page has loaded. A page after the export was asked for means Letterboxd didn't send the file.
    static func nextStep(isSignedIn: Bool, hasRequestedExport: Bool) -> NextStep {
        guard isSignedIn else { return .wait }
        return hasRequestedExport ? .fail : .requestExport
    }

    static func isExport(mimeType: String?, contentDisposition: String?) -> Bool {
        let mimeType = mimeType?.lowercased()
        if mimeType == "application/zip" || mimeType == "application/x-zip-compressed" {
            return true
        }
        let disposition = contentDisposition?.lowercased() ?? ""
        return disposition.contains("attachment") && disposition.contains(".zip")
    }

    /// The sheet is for signing in to Letterboxd, not for browsing the web.
    static func isLetterboxd(_ url: URL?) -> Bool {
        guard url?.scheme == "https", let host = url?.host()?.lowercased() else { return false }
        return host == "letterboxd.com" || host.hasSuffix(".letterboxd.com")
    }

    static func removeFile() {
        try? FileManager.default.removeItem(at: fileURL)
    }
}
