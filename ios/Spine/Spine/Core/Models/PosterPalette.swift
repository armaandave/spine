import Foundation

/// One of a poster's biggest colors and its share of the poster's pixels, as the server's `poster_palette` sends it
/// (`{"hex": "#0B0B0B", "share": 0.83}`).
nonisolated struct PosterSwatch: Codable, Hashable, Sendable {
    let hex: String
    let share: Double
}
