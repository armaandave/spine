import Foundation

/// Lists pinned to a profile, under Recent Activity: the owner's lists tagged "profile", as on Letterboxd.
enum ProfilePinnedLists {
    /// Matched like any tag: whole tag, any case.
    static let tag = "profile"
    static let limit = 3
    /// Last edited first, every list type. Tagging a list edits it, so a fourth pin takes the place of the pin
    /// edited longest ago.
    static let browse = ListBrowseState(sort: .updated, direction: .desc)

    static func pinned(from lists: [CustomListSummary]) -> [CustomListSummary] {
        Array(lists.prefix(limit))
    }
}
