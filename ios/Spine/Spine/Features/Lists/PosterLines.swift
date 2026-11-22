import SwiftUI

/// What a list shows under a poster: its number (ranked lists) with its mark, then one fact.
///
/// Featured lists send the mark and the fact: a blue dot when a title is new on a chart, a green or red arrow when it
/// moved, and a gold trophy before the category a one-year award was won in. A list of your own shows only the
/// number.
struct PosterLines: View {
    /// Nil on unranked lists, which also hide a chart's movement.
    let number: Int?
    let mark: PosterMark?
    let markPlaces: Int?
    let fact: String?

    static let gold = Color(red: 0.95, green: 0.76, blue: 0.31)

    private var factText: String? {
        guard let fact = fact?.trimmingCharacters(in: .whitespacesAndNewlines), !fact.isEmpty else { return nil }
        return fact
    }

    var body: some View {
        if number != nil || factText != nil || mark == .won {
            VStack(spacing: 3) {
                if let number {
                    HStack(spacing: 5) {
                        Text("\(number)")
                            .font(.system(size: 11, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(.white.opacity(0.6))
                        movement
                    }
                }
                if factText != nil || mark == .won {
                    factLine
                        .font(.system(size: 10, weight: .semibold, design: .rounded))
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(PosterLineText.spoken(number: number, mark: mark, markPlaces: markPlaces, fact: factText))
        }
    }

    @ViewBuilder
    private var movement: some View {
        switch mark {
        case .new:
            Circle()
                .fill(.blue)
                .frame(width: 6, height: 6)
        case .up, .down:
            // The arrow alone: how far it moved is only spoken.
            Image(systemName: mark == .up ? "arrow.up" : "arrow.down")
                .font(.system(size: 8, weight: .bold))
                .foregroundStyle(mark == .up ? Color.green : Color.red)
        case .won, nil:
            EmptyView()
        }
    }

    private var factLine: Text {
        let words = Text(factText ?? "")
        guard mark == .won else { return words.foregroundStyle(.white.opacity(0.5)) }
        let trophy = Text(Image(systemName: "trophy.fill")).foregroundStyle(Self.gold)
        guard factText != nil else { return trophy }
        return Text("\(trophy) \(words.foregroundStyle(.white.opacity(0.9)))")
    }
}

enum PosterLineText {
    /// What VoiceOver says for a poster's lines: "Number 2, down 1 place", "Number 1, new", "Won, Best Soundtrack".
    static func spoken(number: Int?, mark: PosterMark?, markPlaces: Int?, fact: String?) -> String {
        var parts: [String] = []
        if let number {
            parts.append("Number \(number)")
            let places = markPlaces.map { $0 == 1 ? "1 place" : "\($0) places" }
            switch mark {
            case .new: parts.append("new")
            case .up: parts.append(["up", places].compactMap { $0 }.joined(separator: " "))
            case .down: parts.append(["down", places].compactMap { $0 }.joined(separator: " "))
            case .won, nil: break
            }
        }
        if mark == .won {
            parts.append("Won")
        }
        if let fact {
            parts.append(fact)
        }
        return parts.joined(separator: ", ")
    }
}
