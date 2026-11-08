import Foundation

/// The site-wide picture at the top of Home, set by the project owner, and whether you may change it.
nonisolated struct HomePictureResponse: Codable, Equatable, Sendable {
    var picture: HomePicturePayload?
    var canChange: Bool
}

nonisolated struct HomePicturePayload: Codable, Equatable, Sendable {
    var ref: MediaRef
    var title: String
    var year: Int?
    var backdropUrl: String
    var setBy: String?
    var updatedAt: String?

    /// What Home draws, or nil when the backdrop is not a URL.
    var topArt: HomeTopArt? {
        guard let url = URL(string: backdropUrl) else { return nil }
        return HomeTopArt(ref: ref, title: title, year: year.map(String.init), backdrop: url)
    }
}

nonisolated struct HomePictureSaveRequest: Codable, Equatable {
    let ref: MediaRef
    let backdropUrl: String
}
