import Foundation

enum InProgressLibraryLoader {
    static let status = "In progress"

    /// TV and anime load through Continue Watching; seasons and episodes are never library rows (TV-001, TV-701,
    /// AN-701).
    static func mediaTypes(from profile: UserProfile?) -> [String] {
        let enabledTypes = profile?.preferences.enabledMediaTypes ?? []
        let baseTypes = enabledTypes.isEmpty ? APIConstants.fallbackMediaTypes : enabledTypes
        return baseTypes.filter { $0 != "season" && $0 != "episode" }
    }

    static func load(
        profile: UserProfile?,
        trackingRepository: TrackingRepository,
        limit: Int
    ) async throws -> [LibraryItem] {
        try await load(
            mediaTypes: mediaTypes(from: profile),
            trackingRepository: trackingRepository,
            limit: limit
        )
    }

    static func load(
        mediaTypes: [String],
        trackingRepository: TrackingRepository,
        limit: Int
    ) async throws -> [LibraryItem] {
        guard limit > 0 else { return [] }

        var shows: [LibraryItem] = []
        var anime: [LibraryItem] = []
        var otherItems: [LibraryItem] = []
        for mediaType in mediaTypes {
            if mediaType == "tv" {
                shows += try await trackingRepository.continueWatching(page: nil).results
            } else if mediaType == "anime" {
                anime += try await trackingRepository.continueWatchingAnime(page: nil).results
            } else {
                let response = try await trackingRepository.list(
                    mediaType: mediaType,
                    page: nil,
                    status: status
                )
                otherItems += response.results
            }
        }

        return Array(mergedItems(shows: interleaved(shows, anime), others: otherItems).prefix(limit))
    }

    /// Continue Watching keeps the server's order of shows and of anime (TV-701, AN-701: calendar day, then recorded
    /// time). Other types are sorted by `updatedAt`, and a stable two-list merge interleaves the shows among them by
    /// `updatedAt` without reordering either list.
    static func mergedItems(shows: [LibraryItem], others: [LibraryItem]) -> [LibraryItem] {
        interleaved(shows, others.sorted(by: inProgressSort))
    }

    /// A stable merge by `updatedAt`: neither list is reordered, and on a tie the first list's item comes first.
    private static func interleaved(_ first: [LibraryItem], _ second: [LibraryItem]) -> [LibraryItem] {
        var merged: [LibraryItem] = []
        merged.reserveCapacity(first.count + second.count)
        var firstIndex = 0
        var secondIndex = 0
        while firstIndex < first.count, secondIndex < second.count {
            if isMoreRecent(second[secondIndex], than: first[firstIndex]) {
                merged.append(second[secondIndex])
                secondIndex += 1
            } else {
                merged.append(first[firstIndex])
                firstIndex += 1
            }
        }
        merged += first[firstIndex...]
        merged += second[secondIndex...]
        return merged
    }

    static func limitedSortedItems(_ items: [LibraryItem], limit: Int) -> [LibraryItem] {
        guard limit > 0 else { return [] }
        return Array(items.sorted(by: inProgressSort).prefix(limit))
    }

    /// Strictly more recent: on a tie, or when neither has a date, the show keeps its place first.
    private nonisolated static func isMoreRecent(_ item: LibraryItem, than show: LibraryItem) -> Bool {
        switch (date(from: item.tracking.updatedAt), date(from: show.tracking.updatedAt)) {
        case let (itemDate?, showDate?):
            itemDate > showDate
        case (.some, .none):
            true
        case (.none, _):
            false
        }
    }

    private nonisolated static func inProgressSort(_ lhs: LibraryItem, _ rhs: LibraryItem) -> Bool {
        let leftDate = date(from: lhs.tracking.updatedAt)
        let rightDate = date(from: rhs.tracking.updatedAt)
        switch (leftDate, rightDate) {
        case let (left?, right?):
            return left > right
        case (.some, .none):
            return true
        case (.none, .some):
            return false
        case (.none, .none):
            return lhs.media.title.localizedCaseInsensitiveCompare(rhs.media.title) == .orderedAscending
        }
    }

    private nonisolated static func date(from value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        return fractionalFormatter.date(from: value) ?? standardFormatter.date(from: value)
    }

    private nonisolated static var fractionalFormatter: ISO8601DateFormatter {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }

    private nonisolated static var standardFormatter: ISO8601DateFormatter {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }
}
