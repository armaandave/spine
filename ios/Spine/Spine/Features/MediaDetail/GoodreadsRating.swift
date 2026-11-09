import Foundation

/// A book's Goodreads rating, read on the phone. Goodreads turns away the server's
/// requests, so the server sends only the book's Goodreads page (a rating with no value)
/// and the app reads the rating from that page, keeping it for a week.
enum GoodreadsRating {
    static let source = "Goodreads"
    private static let keepFor: TimeInterval = 7 * 24 * 60 * 60
    private static let defaultsKey = "goodreadsRatings.v1"

    static func load(
        _ page: URL,
        session: URLSession = .shared,
        defaults: UserDefaults = .standard,
        now: Date = Date()
    ) async -> ExternalRating? {
        if let saved = saved(page, defaults: defaults, now: now) {
            return saved
        }
        var request = URLRequest(url: page)
        request.timeoutInterval = 15
        guard let (data, response) = try? await session.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let found = await Task.detached(operation: { aggregateRating(in: data) }).value
        else {
            return nil
        }
        save(found, for: page, defaults: defaults, now: now)
        return rating(value: found.value, count: found.count, page: page)
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
            value: String(format: "%.2f", value),
            voteCount: count,
            maxValue: "5",
            url: page.absoluteString
        )
    }

    private static func saved(_ page: URL, defaults: UserDefaults, now: Date) -> ExternalRating? {
        guard let entry = entries(defaults)[page.absoluteString],
              let value = entry["value"] as? Double,
              let savedAt = entry["savedAt"] as? Double,
              now.timeIntervalSince1970 - savedAt < keepFor
        else {
            return nil
        }
        return rating(value: value, count: entry["count"] as? Int, page: page)
    }

    private static func save(_ found: (value: Double, count: Int?), for page: URL, defaults: UserDefaults, now: Date) {
        let cutoff = now.timeIntervalSince1970 - keepFor
        var kept = entries(defaults).filter { ($0.value["savedAt"] as? Double ?? 0) > cutoff }
        var entry: [String: Any] = ["value": found.value, "savedAt": now.timeIntervalSince1970]
        entry["count"] = found.count
        kept[page.absoluteString] = entry
        defaults.set(kept, forKey: defaultsKey)
    }

    private static func entries(_ defaults: UserDefaults) -> [String: [String: Any]] {
        defaults.dictionary(forKey: defaultsKey) as? [String: [String: Any]] ?? [:]
    }
}
