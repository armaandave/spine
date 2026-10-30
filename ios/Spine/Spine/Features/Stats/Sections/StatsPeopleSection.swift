import SwiftUI

// People (STAT-219): the directors, cast, creators, artists and authors you logged most, one
// section per group on the movie, TV, music and book tabs. A segmented control switches between
// most logged and highest rated; round headshots sit five to a row, two rows, each row its own
// lazy item, then "Show all". A person opens their logs on your own stats.

enum StatsPeopleData {
    static let columns = 5
    static let collapsedCount = 10
    /// Five columns share the row's width, this far apart, so names get every point there is.
    static let columnSpacing: CGFloat = 6
    static let photoSize: CGFloat = 56

    /// The groups with anyone in them, in the server's order.
    static func visibleGroups(_ people: StatsPeople?) -> [StatsPeopleGroup] {
        (people?.groups ?? []).filter { !$0.isEmpty }
    }

    /// "Most watched", "Most listened", "Most read", then "Highest rated".
    static func sortTitle(_ sort: StatsPeopleSort, mediaType: String?) -> String {
        sort == .titles ? StatsMostLoggedSection.label(for: mediaType) : "Highest rated"
    }

    /// The order showing: the one picked, else most logged; Highest rated needs rows.
    static func effectiveSort(chosen: StatsPeopleSort?, in group: StatsPeopleGroup) -> StatsPeopleSort {
        chosen == .rating && !group.ratedRows.isEmpty ? .rating : .titles
    }

    static func rows(_ group: StatsPeopleGroup, sort: StatsPeopleSort) -> [StatsPersonRow] {
        Array((sort == .rating ? group.ratedRows : group.rows).prefix(collapsedCount))
    }

    /// "9 films", "1 show", "12 albums", "3 books"; "4.2★" for Highest rated.
    static func caption(_ row: StatsPersonRow, sort: StatsPeopleSort, mediaType: String?) -> String {
        if sort == .rating, let average = row.averageStars {
            return "\(average.averageText)★"
        }
        let (singular, plural) = titleNouns(for: mediaType)
        return StatsChartCopy.count(row.titles, singular, plural)
    }

    /// The noun a person's count uses on each tab.
    static func titleNouns(for mediaType: String?) -> (String, String) {
        switch mediaType {
        case "movie": ("film", "films")
        case "tv": ("show", "shows")
        case "music": ("album", "albums")
        case "book": ("book", "books")
        default: ("title", "titles")
        }
    }

    /// "Show all 412" for most logged; Highest rated's count isn't in the payload, so it's just
    /// "Show all", once its first ten are full. Nil when there's nothing more.
    static func showAllTitle(_ group: StatsPeopleGroup, sort: StatsPeopleSort) -> String? {
        switch sort {
        case .titles:
            group.total > group.rows.count ? "Show all \(group.total.formatted())" : nil
        case .rating:
            group.ratedRows.count >= collapsedCount ? "Show all" : nil
        }
    }

    /// "Christopher Nolan, 9 films".
    static func accessibilityLabel(_ row: StatsPersonRow, sort: StatsPeopleSort, mediaType: String?) -> String {
        if sort == .rating, let average = row.averageStars {
            return "\(row.person.name), \(StatsStars.accessibilityLabel(for: average.value))"
        }
        return "\(row.person.name), \(caption(row, sort: .titles, mediaType: mediaType))"
    }
}

/// One People group's rows: its title, the order control when anyone has enough ratings, the
/// headshot rows, and "Show all". A person opens what `StatsDrillDown.person` routes to for this
/// viewer, or nothing.
struct StatsPeopleSection: View {
    let group: StatsPeopleGroup
    /// The tab, for the per-type words.
    let mediaType: String?
    let drillDownContext: StatsDrillDownContext
    let onOpen: (StatsDrillDown) -> Void
    var onShowAll: ((StatsPeopleSort) -> Void)?

    /// Every row carries its group's role in its ID, so two groups never collide in the page's
    /// lazy stack.
    enum Row: Identifiable {
        case title(role: String)
        case picker(role: String)
        case people(StatsPosterGridRow<StatsPersonRow>, isLast: Bool)
        case showAll(String, role: String)

        var id: String {
            switch self {
            case let .title(role): "people.\(role).title"
            case let .picker(role): "people.\(role).picker"
            case let .people(row, _): row.id
            case let .showAll(_, role): "people.\(role).showAll"
            }
        }
    }

    /// The order the user picked; nil until they pick one.
    @State private var chosenSort: StatsPeopleSort?

    var body: some View {
        let sort = StatsPeopleData.effectiveSort(chosen: chosenSort, in: group)
        ForEach(Self.rows(for: group, sort: sort, canShowAll: onShowAll != nil)) { row in
            switch row {
            case .title:
                StatsSectionTitle(group.label)
            case .picker:
                Picker("Order", selection: Binding(get: { sort }, set: { chosenSort = $0 })) {
                    ForEach(StatsPeopleSort.allCases) { sort in
                        Text(StatsPeopleData.sortTitle(sort, mediaType: mediaType)).tag(sort)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.bottom, 14)
                .sensoryFeedback(.selection, trigger: chosenSort)
            case let .people(gridRow, isLast):
                HStack(alignment: .top, spacing: StatsPeopleData.columnSpacing) {
                    ForEach(0 ..< StatsPeopleData.columns, id: \.self) { column in
                        if column < gridRow.items.count {
                            let person = gridRow.items[column]
                            let drillDown = StatsDrillDown.person(person.person, role: group.role)
                            StatsPersonCell(
                                row: person,
                                caption: StatsPeopleData.caption(person, sort: sort, mediaType: mediaType),
                                accessibilityText: StatsPeopleData.accessibilityLabel(person, sort: sort, mediaType: mediaType),
                                action: drillDown.route(in: drillDownContext) == nil ? nil : { onOpen(drillDown) }
                            )
                        } else {
                            // A short last row keeps the same columns.
                            Color.clear
                                .frame(maxWidth: .infinity, maxHeight: 1)
                                .accessibilityHidden(true)
                        }
                    }
                }
                .padding(.bottom, isLast ? (Self.hasShowAll(group, sort: sort, canShowAll: onShowAll != nil) ? 12 : StatsSectionLayout.sectionGap) : 16)
            case let .showAll(title, _):
                StatsShowAllRow(title: title) {
                    onShowAll?(sort)
                }
                .padding(.bottom, StatsSectionLayout.sectionGap)
            }
        }
    }

    // MARK: - Data shaping

    private static func hasShowAll(_ group: StatsPeopleGroup, sort: StatsPeopleSort, canShowAll: Bool) -> Bool {
        canShowAll && StatsPeopleData.showAllTitle(group, sort: sort) != nil
    }

    /// The group's rows; none for an empty group. Row IDs carry the role and order, so two
    /// groups never collide in the page's lazy stack.
    static func rows(for group: StatsPeopleGroup, sort: StatsPeopleSort, canShowAll: Bool = true) -> [Row] {
        guard !group.isEmpty else { return [] }
        var rows: [Row] = [.title(role: group.role)]
        if !group.ratedRows.isEmpty {
            rows.append(.picker(role: group.role))
        }
        let gridRows = StatsPosterGridLayout.rows(
            StatsPeopleData.rows(group, sort: sort),
            columns: StatsPeopleData.columns,
            idPrefix: "people.\(group.role).\(sort.rawValue)"
        )
        rows += gridRows.map { .people($0, isLast: $0.index == gridRows.count - 1) }
        if canShowAll, let title = StatsPeopleData.showAllTitle(group, sort: sort) {
            rows.append(.showAll(title, role: group.role))
        }
        return rows
    }
}

/// A round headshot (initials on a neutral circle without one), the name under it, and the
/// count or average.
struct StatsPersonCell: View {
    let row: StatsPersonRow
    let caption: String
    let accessibilityText: String
    /// Nil when the person opens nothing.
    let action: (() -> Void)?

    var body: some View {
        if let action {
            Button(action: action) { content }
                .buttonStyle(.plain)
                .accessibilityAddTraits(.isButton)
        } else {
            content
        }
    }

    private var content: some View {
        VStack(spacing: 6) {
            PersonArtwork(urlString: row.person.imageUrl, name: row.person.name, size: StatsPeopleData.photoSize)
                .shadow(color: .black.opacity(0.25), radius: 6, y: 3)

            VStack(spacing: 2) {
                Text(row.person.name)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.88))
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .allowsTightening(true)
                    .minimumScaleFactor(0.85)
                Text(caption)
                    .font(.caption2)
                    .monospacedDigit()
                    .foregroundStyle(StatsChartStyle.secondaryText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
        }
        .frame(maxWidth: .infinity)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }
}
