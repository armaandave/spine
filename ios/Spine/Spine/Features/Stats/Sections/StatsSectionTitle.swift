import SwiftUI

/// Spacing shared by the Stats sections, so every section sits the same distance from the next.
enum StatsSectionLayout {
    /// Below a section's last row.
    static let sectionGap: CGFloat = 26
    /// Between two cards of one section (the header's numbers and its details).
    static let cardGap: CGFloat = 12
    /// A separator's inset in a card row that has no artwork.
    static let separatorInset: CGFloat = 16
}

/// A section's plain bold title, as the diary's month index heads its year cards, with an
/// optional control on the right ("Show all").
struct StatsSectionTitle<Trailing: View>: View {
    let title: String
    var subtitle: String?
    let trailing: Trailing

    init(_ title: String, subtitle: String? = nil, @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = trailing()
    }

    var body: some View {
        HStack(alignment: .lastTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(.white)
                    .accessibilityAddTraits(.isHeader)

                if let subtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.55))
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            trailing
        }
        .padding(.leading, 4)
        .padding(.bottom, 8)
    }
}

extension StatsSectionTitle where Trailing == EmptyView {
    init(_ title: String, subtitle: String? = nil) {
        self.init(title, subtitle: subtitle) { EmptyView() }
    }
}

/// The quiet "Show all" link beside a section title.
struct StatsShowAllButton: View {
    var title = "Show all"
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(title)
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .bold))
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.white.opacity(0.62))
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// A label on the left and its value on the right. A long value wraps on the right, beside
/// its label; it never drops under it.
struct StatsLabelValueRow: View {
    let label: String
    let value: String
    var showsChevron = false
    var showsSeparator = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(.body)
                .foregroundStyle(.white.opacity(0.88))
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 12)

            Text(value)
                .font(.body.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(.white)
                .multilineTextAlignment(.trailing)
                .fixedSize(horizontal: false, vertical: true)

            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.35))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
        .frame(minHeight: 48)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsSeparator {
                DiarySeparator()
                    .padding(.leading, StatsSectionLayout.separatorInset)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Gold stars as the diary draws them: a filled star per whole star and a half star for a
/// half, with no empty outlines.
struct StatsStars: View {
    let stars: Double
    var size: CGFloat = 11

    var body: some View {
        HStack(spacing: 1) {
            ForEach(Array(Self.symbols(for: stars).enumerated()), id: \.offset) { _, name in
                Image(systemName: name)
            }
        }
        .font(.system(size: size, weight: .bold))
        .foregroundStyle(LogComposerStyle.starGold)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.accessibilityLabel(for: stars))
    }

    /// "3.5" is ★★★½. Off-scale values (STAT-109) round down to the half star below.
    static func symbols(for stars: Double) -> [String] {
        let clamped = min(max(stars, 0), 5)
        let whole = Int(clamped.rounded(.down))
        let hasHalf = clamped - Double(whole) >= 0.5
        return Array(repeating: "star.fill", count: whole) + (hasHalf ? ["star.leadinghalf.filled"] : [])
    }

    static func accessibilityLabel(for stars: Double) -> String {
        "Rated \(stars.formatted(.number.precision(.fractionLength(0 ... 2)))) out of 5 stars"
    }
}

extension View {
    /// One row of a Stats card: rounds the card's top on its first row and its bottom on its
    /// last, and leaves the section gap after the last. Each row stays its own lazy item.
    func statsCardSlice(isFirst: Bool, isLast: Bool, gapAfter: CGFloat = StatsSectionLayout.sectionGap) -> some View {
        diaryCard(roundsTop: isFirst, roundsBottom: isLast)
            .padding(.bottom, isLast ? gapAfter : 0)
    }
}
