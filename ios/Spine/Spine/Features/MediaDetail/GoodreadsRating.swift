import Foundation

/// A book's Goodreads rating, read on the phone. Goodreads turns away the server's
/// requests, so the server sends only the book's Goodreads page (a rating with no value)
/// and the app reads the rating from that page. The phone keeps the last rating it read
/// until a newer one replaces it, and reads the page again once that rating is a week old.
enum GoodreadsRating {
    static let source = "Goodreads"
    private static let freshFor: TimeInterval = 7 * 24 * 60 * 60
    private static let defaultsKey = "goodreadsRatings.v1"

    struct Saved: Equatable {
        let rating: ExternalRating
        /// Read within the last week; an older one shows while the page is read again.
        let isFresh: Bool
    }

    /// Reads the rating from the Goodreads page and keeps it; nil when the page has none to give.
    static func load(
        _ page: URL,
        session: URLSession = .shared,
        defaults: UserDefaults = .standard,
        now: Date = Date(),
        readInBrowser: (URL) async -> (value: Double, count: Int?)? = { await GoodreadsBrowserReader.shared.rating($0) }
    ) async -> ExternalRating? {
        // A plain request first; when Goodreads answers with its bot check, the hidden browser.
        var found = await plainRequest(page, session: session)
        if found == nil, !Task.isCancelled {
            found = await readInBrowser(page)
        }
        guard let found else { return nil }
        save(found, for: page, defaults: defaults, now: now)
        return rating(value: found.value, count: found.count, page: page)
    }

    private static func plainRequest(_ page: URL, session: URLSession) async -> (value: Double, count: Int?)? {
        var request = URLRequest(url: page)
        request.timeoutInterval = 15
        guard let (data, response) = try? await session.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200
        else {
            return nil
        }
        return await Task.detached(operation: { aggregateRating(in: data) }).value
    }

    /// The rating in the page's structured data, the block Goodreads keeps for search engines:
    /// `"aggregateRating":{"@type":"AggregateRating","ratingValue":4.51,"ratingCount":1934391,...}`.
    nonisolated static func aggregateRating(in data: Data) -> (value: Double, count: Int?)? {
        let html = String(decoding: data, as: UTF8.self)
        let key = #""aggregateRating":"#
        guard let range = html.range(of: key + #"\{[^}]*\}"#, options: .regularExpression),
              let object = try? JSONSerialization.jsonObject(with: Data(html[range].dropFirst(key.count).utf8)),
              let fields = object as? [String: Any],
              let value = (fields["ratingValue"] as? NSNumber)?.doubleValue,
              (0...5).contains(value)
        else {
            return nil
        }
        return (value, (fields["ratingCount"] as? NSNumber)?.intValue)
    }

    private static func rating(value: Double, count: Int?, page: URL) -> ExternalRating {
        ExternalRating(
            source: source,
            // Goodreads' two decimals without trailing zeros: 4.51, 4.4, 4.
            value: String(format: "%.2f", value)
                .replacingOccurrences(of: #"\.?0+$"#, with: "", options: .regularExpression),
            voteCount: count,
            maxValue: "5",
            url: page.absoluteString
        )
    }

    /// The last rating read from this page, however old.
    static func saved(_ page: URL, defaults: UserDefaults = .standard, now: Date = Date()) -> Saved? {
        guard let entry = entries(defaults)[page.absoluteString],
              let value = entry["value"] as? Double,
              let savedAt = entry["savedAt"] as? Double
        else {
            return nil
        }
        return Saved(
            rating: rating(value: value, count: entry["count"] as? Int, page: page),
            isFresh: now.timeIntervalSince1970 - savedAt < freshFor
        )
    }

    private static func save(_ found: (value: Double, count: Int?), for page: URL, defaults: UserDefaults, now: Date) {
        var entries = entries(defaults)
        var entry: [String: Any] = ["value": found.value, "savedAt": now.timeIntervalSince1970]
        entry["count"] = found.count
        entries[page.absoluteString] = entry
        defaults.set(entries, forKey: defaultsKey)
    }

    private static func entries(_ defaults: UserDefaults) -> [String: [String: Any]] {
        defaults.dictionary(forKey: defaultsKey) as? [String: [String: Any]] ?? [:]
    }
}
