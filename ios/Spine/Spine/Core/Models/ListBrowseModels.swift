import Foundation

/// How a page of lists is sorted, as the server sorts it (`sort` on the list endpoints).
enum ListSort: String, Codable, CaseIterable, Identifiable {
    case updated
    case name
    case created
    case items
    case likes

    var id: String { rawValue }

    var label: String {
        switch self {
        case .updated: "Last edited"
        case .name: "Name"
        case .created: "Date created"
        case .items: "Number of items"
        case .likes: "Likes"
        }
    }

    var defaultDirection: MediaFilterDirection {
        self == .name ? .asc : .desc
    }

    func label(for direction: MediaFilterDirection) -> String {
        switch (self, direction) {
        case (.updated, .desc), (.created, .desc): "Newest first"
        case (.updated, .asc), (.created, .asc): "Oldest first"
        case (.name, .asc): "A–Z"
        case (.name, .desc): "Z–A"
        case (.items, .desc), (.likes, .desc): "Most first"
        case (.items, .asc), (.likes, .asc): "Fewest first"
        }
    }
}

enum ListVisibilityFilter: String, Codable, CaseIterable, Identifiable {
    case `public`
    case `private`

    var id: String { rawValue }

    var label: String {
        switch self {
        case .public: "Public"
        case .private: "Private"
        }
    }
}

/// The sort and filters of every page of lists. One choice, remembered on the phone, covers them all.
struct ListBrowseState: Codable, Equatable {
    var sort: ListSort = .updated
    /// Nil for the sort's own direction.
    var direction: MediaFilterDirection?
    /// Nil for media and people lists alike.
    var listType: CustomListType? {
        // People lists hold no media, so a media type picked before can't come back later unseen.
        didSet { if listType == .people { mediaType = nil } }
    }
    /// Lists holding only this media type. Nil for any.
    var mediaType: String?
    /// Only on your own lists: theirs are all public to you anyway.
    var visibility: ListVisibilityFilter?

    static let `default` = ListBrowseState()

    var resolvedDirection: MediaFilterDirection {
        direction ?? sort.defaultDirection
    }

    /// The media type the server filters by: none when only people lists are asked for.
    var effectiveMediaType: String? {
        listType == .people ? nil : mediaType
    }

    /// The choices that differ from the default, for the filter button's badge.
    func activeCount(includesVisibility: Bool) -> Int {
        [
            sort != .updated || resolvedDirection != sort.defaultDirection,
            listType != nil,
            effectiveMediaType != nil,
            includesVisibility && visibility != nil,
        ]
        .filter { $0 }
        .count
    }

    /// Drops a media type that is no longer offered, so the page doesn't sit on a hidden filter.
    func pruned(toMediaTypes mediaTypes: [String]) -> ListBrowseState {
        guard let mediaType, !mediaTypes.contains(mediaType) else { return self }
        var state = self
        state.mediaType = nil
        return state
    }

    func queryItems(includesVisibility: Bool) -> [URLQueryItem] {
        var items = [
            URLQueryItem(name: "list_type", value: listType?.rawValue ?? "all"),
            URLQueryItem(name: "sort", value: sort.rawValue),
            URLQueryItem(name: "direction", value: resolvedDirection.rawValue),
        ]
        if let mediaType = effectiveMediaType {
            items.append(URLQueryItem(name: "media_type", value: mediaType))
        }
        if includesVisibility, let visibility {
            items.append(URLQueryItem(name: "visibility", value: visibility.rawValue))
        }
        return items
    }
}

/// Where the phone keeps the lists choice.
enum ListBrowseStore {
    static let key = "listBrowseState"

    static func load(from defaults: UserDefaults = .standard) -> ListBrowseState {
        guard let data = defaults.data(forKey: key),
              let state = try? JSONDecoder().decode(ListBrowseState.self, from: data) else {
            return .default
        }
        return state
    }

    static func save(_ state: ListBrowseState, to defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(state) {
            defaults.set(data, forKey: key)
        }
    }
}
